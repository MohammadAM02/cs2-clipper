"""The worker: moves each Demo through the Flow, one step per tick (spec: Flow).

spotted → unpacked → analyzed → scored → rendering → joined → done, or skipped / failed.
Every step can run again safely, so a crash or a reboot resumes where it stopped.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from clipper.config import Config
from clipper.gate import GateStatus
from clipper.index import Index
from clipper.join import JoinError, assign_clips
from clipper.model import MatchInfo, RoundFacts
from clipper.render import RenderRequest, RenderResult
from clipper.scoring import score_match, select
from clipper.state import AppState, Rendering

log = logging.getLogger(__name__)

ACTIVE_STATES = ("spotted", "unpacked", "analyzed", "scored", "rendering", "joined")
PERSPECTIVES = ("player", "enemy")
MAX_STEP_ATTEMPTS = 3
MAX_RENDER_ATTEMPTS = 3
PAUSE_AFTER_FAILURES = 3
QUIT_ABORT = "aborted: the app was quit"


class Skip(Exception):
    """This Demo should not be processed at all."""


class GiveUp(Exception):
    """This Demo cannot be finished; running the step again will not help."""


class IntakeLike(Protocol):
    def ready(self) -> list[Path]: ...
    def take(self, path: Path, index: Index) -> int | None: ...


class FactsSource(Protocol):
    def has(self, checksum: str) -> bool: ...
    def match_info(self, checksum: str, steamid: str) -> MatchInfo | None: ...
    def round_facts(self, checksum: str, steamid: str) -> list[RoundFacts]: ...


class GateLike(Protocol):
    def check(self) -> GateStatus: ...
    def faceit_running(self) -> bool: ...


class AlertsStep(Protocol):
    def tick(self) -> None: ...


class StopRequest:
    """How the app asks the worker to stop. "after_render": a running render finishes, then no new
    step. "now": a running render is aborted the way a FACEIT AC abort stops it. Thread-safe: the
    app's threads (tray, web server) request; the worker thread only reads."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mode: str | None = None
        self._event = threading.Event()

    def request(self, mode: str) -> None:
        """`mode` is "now" or "after_render". "now" wins: a later "after_render" never downgrades
        it, a later "now" upgrades an "after_render" already in effect."""
        with self._lock:
            if self._mode != "now":
                self._mode = mode
        self._event.set()

    @property
    def mode(self) -> str | None:
        with self._lock:
            return self._mode

    def stopping(self) -> bool:
        return self.mode is not None

    def abort_render(self) -> bool:
        return self.mode == "now"

    def wait(self, timeout: float) -> bool:
        """Sleep up to `timeout` seconds, waking early on a request. True if a request was or
        becomes in effect; False if `timeout` elapsed with none."""
        return self._event.wait(timeout)


class DeleteRequest:
    """How the Status page deletes a Demo without pulling it out from under the worker. The worker
    claims each Demo for a step; a delete asked during that step aborts its render the way a FACEIT AC
    abort stops it, and the worker deletes the Demo once the step is over. Any other Demo is deleted
    on the spot, the worker keeping off it until it is gone. Thread-safe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._working_on: int | None = None
        self._asked: set[int] = set()

    def claim(self, demo_id: int) -> bool:
        """The worker is about to take a step on this Demo. False while it is being deleted."""
        with self._lock:
            if demo_id in self._asked:
                return False
            self._working_on = demo_id
            return True

    def asked(self, demo_id: int) -> bool:
        with self._lock:
            return demo_id in self._asked

    def release(self, demo_id: int, remove: Callable[[], object]) -> None:
        """The worker's step on this Demo is over: a delete asked during it happens now."""
        with self._lock:
            self._working_on = None
            if demo_id not in self._asked:
                return
        try:
            remove()
        finally:
            with self._lock:
                self._asked.discard(demo_id)

    def delete(self, demo_id: int, remove: Callable[[], bool]) -> str | None:
        """"deferred" while the worker is on this Demo (or it is already going); otherwise `remove`
        runs now: "deleted", or None when it found no such Demo. Its errors pass through."""
        with self._lock:
            if demo_id in self._asked:
                return "deferred"
            self._asked.add(demo_id)
            if self._working_on == demo_id:
                return "deferred"
        try:
            return "deleted" if remove() else None
        finally:
            with self._lock:
                self._asked.discard(demo_id)


@dataclass
class Services:
    """Everything the worker talks to. Tests swap in fakes."""

    intake: IntakeLike
    unpack: Callable[[Path, Path], Path]
    analyze: Callable[[Path, Path], str]          # (the .dem, its log) -> the match checksum
    facts: FactsSource
    gate: GateLike
    render: Callable[[RenderRequest, Callable[[], bool]], RenderResult]
    join: Callable[[list[Path], Path], float]
    notify: Callable[..., None]
    sleep: Callable[[float], None] = time.sleep
    alerts: AlertsStep | None = None


def _fresh_dir(path: Path) -> Path:
    """`path`, or `path-2`, `path-3`, … if it already exists."""
    candidate, n = path, 1
    while candidate.exists():
        n += 1
        candidate = path.with_name(f"{path.name}-{n}")
    return candidate


def delete_demo(index: Index, cfg: Config, demo_id: int) -> bool:
    """Forget a Demo that is not in Reels yet, with its download, its unpacked .dem, its renders and
    its match's analysis; its logs stay. False when there is no such Demo; ValueError when it is
    already in Reels. A file that cannot go is logged and left."""
    demo = index.delete_demo(demo_id)
    if demo is None:
        return False
    for path in filter(None, (demo["archive_path"], demo["dem_path"])):
        try:
            Path(path).unlink(missing_ok=True)
        except OSError as exc:
            log.warning("demo #%s: could not remove %s: %s", demo_id, path, exc)
    checksum = demo["match_checksum"]
    if checksum and not any(other["match_checksum"] == checksum for other in index.all_demos()):
        renders = cfg.renders_dir / checksum
        shutil.rmtree(renders, ignore_errors=True)
        if renders.exists():
            log.warning("demo #%s: could not remove all of %s", demo_id, renders)
        analysis = cfg.analyses_dir / f"{checksum}.json"
        try:
            analysis.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("demo #%s: could not remove %s: %s", demo_id, analysis, exc)
    log.info("demo #%s deleted: %s", demo_id, demo["file_name"])
    return True


def prune_renders(index: Index, cfg: Config, checksum: str) -> bool:
    """Delete a match's raw Clips (its renders folder: every view and attempt) once every Demo of the match is
    done, since its Reels are made and they are all that is kept. True when the folder was removed. The library
    is never touched; a folder that cannot all go is logged and left."""
    renders = cfg.renders_dir / checksum
    if not renders.exists():
        return False
    if any(demo["state"] != "done" for demo in index.all_demos() if demo["match_checksum"] == checksum):
        return False                                  # a Demo of the match still needs its Clips
    shutil.rmtree(renders, ignore_errors=True)
    if renders.exists():
        log.warning("match %s: could not remove all of %s", checksum, renders)
        return False
    return True


class Worker:
    def __init__(self, cfg: Config, index: Index, services: Services, *,
                 state: AppState | None = None, stop: StopRequest | None = None,
                 deletes: DeleteRequest | None = None):
        self.cfg = cfg
        self.index = index
        self.services = services
        self.state = state if state is not None else AppState()
        self.stop = stop if stop is not None else StopRequest()
        self.deletes = deletes if deletes is not None else DeleteRequest()
        self._waiting_reasons: tuple[str, ...] = ()
        self._pruned_at_start = False

    # --- the loop --------------------------------------------------------------------------------

    def tick(self) -> None:
        """Take any new Demos, move every unfinished Demo on by at most one step, then run match
        alerts. Only the oldest Demo in rendering gets a step there, so one map has every view it renders
        done before the next map's first. A view the settings no longer render comes off every Demo's queue.
        Once a stop is requested, no new step starts (a running one, a render included,
        finishes on its own terms); publishes the "what it's doing" summary throughout, and always
        ends idle or waiting, never stuck saying "rendering"."""
        self.state.set_paused_by(self.index.paused_by())
        self._waiting_reasons = ()
        try:
            if not self._pruned_at_start:
                self._pruned_at_start = True
                self._prune_done_matches()
            for path in self.services.intake.ready():
                if self.stop.stopping():
                    return
                try:
                    self.services.intake.take(path, self.index)
                except OSError:
                    log.exception("could not take %s; will try again", path.name)
            for perspective in PERSPECTIVES:
                if perspective in self.cfg.perspectives_to_render:
                    continue
                removed = self.index.unqueue_renders(perspective)
                if removed:
                    log.info("the %s view is off in Settings: %s queued render%s taken off the queue",
                             perspective, removed, "" if removed == 1 else "s")
            render_turn_taken = False
            for demo in self.index.demos_in(ACTIVE_STATES):
                if self.stop.stopping():
                    return
                if demo["state"] == "rendering":
                    if render_turn_taken:
                        continue      # maps first: the oldest Demo renders its views before the next starts
                    render_turn_taken = True
                self._step(demo["id"])
            if self.stop.stopping():
                return
            if self.services.alerts is not None:
                try:
                    self.services.alerts.tick()
                except Exception:  # noqa: BLE001 - match alerts must never stop the pipeline
                    log.exception("match alerts failed")
        finally:
            if self._waiting_reasons:
                self.state.set_waiting(self._waiting_reasons)
            else:
                self.state.set_idle()

    def _prune_done_matches(self) -> None:
        """On the first tick of each Worker: matches finished before their raw Clips were pruned give them up now.
        A match with a Demo that is not done keeps its Clips (prune_renders)."""
        checksums = {demo["match_checksum"] for demo in self.index.demos_in(("done",)) if demo["match_checksum"]}
        for checksum in sorted(checksums):
            prune_renders(self.index, self.cfg, checksum)

    def _step(self, demo_id: int) -> None:
        """One step on one Demo, which the Status page cannot delete under it: a delete asked
        meanwhile aborts its render and happens once the step is over."""
        if not self.deletes.claim(demo_id):
            return                                    # being deleted right now
        try:
            demo = self.index.demo(demo_id)           # None: deleted since this tick listed it
            if demo is not None and not self.deletes.asked(demo_id):
                self._advance(demo)
        finally:
            self.deletes.release(demo_id, lambda: self._delete(demo_id))

    def _delete(self, demo_id: int) -> None:
        try:
            delete_demo(self.index, self.cfg, demo_id)
        except ValueError as exc:                     # it reached Reels during the step
            log.warning("demo #%s was not deleted: %s", demo_id, exc)

    def _gives_way(self, demo_id: int) -> bool:
        return self.stop.stopping() or self.deletes.asked(demo_id)

    def _advance(self, demo) -> None:
        steps = {
            "spotted": self._unpack,
            "unpacked": self._analyze,
            "analyzed": self._score,
            "scored": self._queue_renders,
            "rendering": self._render,
            "joined": self._finish,
        }
        try:
            steps[demo["state"]](demo)
        except Skip as exc:
            log.info("demo #%s skipped: %s", demo["id"], exc)
            self.index.advance(demo["id"], "skipped", last_error=str(exc))
        except GiveUp as exc:
            log.error("demo #%s failed: %s", demo["id"], exc)
            self.index.fail(demo["id"], str(exc))
        except Exception as exc:  # noqa: BLE001 - every failure is recorded; none may stop the app
            log.exception("demo #%s: the %s step failed", demo["id"], demo["state"])
            error = f"{demo['state']}: {exc}"
            if self.index.record_failure(demo["id"], error) >= MAX_STEP_ATTEMPTS:
                self.index.fail(demo["id"], error)

    # --- steps 2–4 -------------------------------------------------------------------------------

    def _unpack(self, demo) -> None:
        dem = self.services.unpack(Path(demo["archive_path"]), self.cfg.demos_dir)
        self.index.advance(demo["id"], "unpacked", dem_path=dem)

    def _analyze(self, demo) -> None:
        checksum = self.services.analyze(Path(demo["dem_path"]), self._log_path(demo, "analyze"))
        info = self.services.facts.match_info(checksum, self.cfg.subject_steamid)
        if info is None:
            raise Skip("the subject's SteamID is not in this match")
        self.index.save_match(info)
        self.index.advance(demo["id"], "analyzed", match_checksum=checksum)

    def _ensure_analysis(self, demo) -> None:
        """Analyze the Demo again when the app has no analysis of its match, as for a Demo analyzed before
        the app kept its own analyses."""
        checksum = demo["match_checksum"]
        if self.services.facts.has(checksum):
            return
        again = self.services.analyze(Path(demo["dem_path"]), self._log_path(demo, "analyze"))
        if again != checksum:
            raise GiveUp(f"analyzing the Demo again gave match {again}, not {checksum}")

    def _score(self, demo) -> None:
        self._ensure_analysis(demo)
        facts =self.services.facts.round_facts(demo["match_checksum"], self.cfg.subject_steamid)
        highlights = score_match(facts)
        chosen = select(highlights, self.cfg.top_n)
        self.index.save_highlights(demo["match_checksum"], highlights, {h.round for h in chosen})
        self.index.advance(demo["id"], "scored")

    def _queue_renders(self, demo) -> None:
        if not self.index.selected_highlights(demo["match_checksum"]):
            self._finish(demo)                        # no Frags: nothing to render
            return
        for perspective in self.cfg.perspectives_to_render:
            if self.index.latest_render(demo["id"], perspective) is None:
                self.index.queue_render(demo["id"], perspective, attempt=1)
        self.index.advance(demo["id"], "rendering")

    # --- step 5: rendering -----------------------------------------------------------------------

    def _render(self, demo) -> None:
        for perspective in self.cfg.perspectives_to_render:
            job = self.index.latest_render(demo["id"], perspective)
            if job is None:
                self.index.queue_render(demo["id"], perspective, attempt=1)
                job = self.index.latest_render(demo["id"], perspective)
            if job["state"] == "done":
                continue
            if job["state"] == "running":             # the app stopped in the middle of this render
                self.index.finish_render(job["id"], "failed", "interrupted: the app stopped mid-render")
                job = self.index.latest_render(demo["id"], perspective)
            if job["state"] == "failed":
                if job["attempt"] >= MAX_RENDER_ATTEMPTS:
                    raise GiveUp(f"{perspective} render failed {job['attempt']} times: {job['failure']}")
                self.index.queue_render(demo["id"], perspective, attempt=job["attempt"] + 1)
                job = self.index.latest_render(demo["id"], perspective)
            elif job["state"] == "aborted":            # FACEIT AC appeared: go again, not counted
                self.index.queue_render(demo["id"], perspective, attempt=job["attempt"])
                job = self.index.latest_render(demo["id"], perspective)
            self._try_render(demo, job)
            return                                    # at most one CS2 launch per tick
        self._join(demo)
        self.index.advance(demo["id"], "joined")

    def _try_render(self, demo, job) -> None:
        if self._gives_way(demo["id"]):
            return
        if self.index.paused_by() is not None:
            return
        if not self._gate_is_clear():
            return
        self._ensure_analysis(demo)
        highlights = self.index.selected_highlights(demo["match_checksum"])
        match = self.index.match(demo["match_checksum"])
        self.services.notify(
            "Rendering highlights",
            f"{len(highlights)} Highlights from {match['map']} ({job['perspective']} view). "
            f"CS2 opens in {self.cfg.heads_up_seconds:g} s — please don't start it yourself.",
        )
        if not self._gate_stays_clear(self.cfg.heads_up_seconds, demo["id"]):
            return
        output_dir = _fresh_dir(
            self.cfg.renders_dir / demo["match_checksum"] / job["perspective"] / f"attempt-{job['attempt']}"
        )
        log_path = self._log_path(demo, f"render-{job['perspective']}-{job['attempt']}")
        self.index.start_render(job["id"], output_dir, log_path)
        self.state.set_rendering(Rendering(match["map"], job["perspective"], time.time()))
        request = RenderRequest(
            demo_path=Path(demo["dem_path"]),
            perspective=job["perspective"],
            rounds=tuple(h["round"] for h in highlights),
            output_dir=output_dir,
            log_path=log_path,
            steamid=self.cfg.subject_steamid,
            padding_before_s=self.cfg.padding_before_s,
            padding_after_s=self.cfg.padding_after_s,
            event=self.cfg.sequence_event,
            width=self.cfg.video_size[0],
            height=self.cfg.video_size[1],
            checksum=demo["match_checksum"],
        )
        try:
            result = self.services.render(
                request, lambda: (self.services.gate.faceit_running() or self.stop.abort_render()
                                  or self.deletes.asked(demo["id"]))
            )
        except Exception as exc:  # noqa: BLE001 - a crashed render is a failed attempt
            log.exception("render crashed")
            result = RenderResult(ok=False, failure=f"render crashed: {exc}")
        if result.aborted:
            failure = QUIT_ABORT if self.stop.abort_render() else result.failure
            self.index.finish_render(job["id"], "aborted", failure)
            return
        if result.ok:
            try:
                groups = assign_clips(result.clips, [(h["round"], h["round_start_tick"]) for h in highlights])
            except JoinError as exc:
                result = RenderResult(ok=False, failure=str(exc))
            else:
                ids = {h["round"]: h["id"] for h in highlights}
                for number, clips in groups.items():
                    for clip in clips:
                        self.index.add_clip(job["id"], ids[number], clip,
                                            self.cfg.store_path(clip.path))
                self.index.finish_render(job["id"], "done")
                self.index.set_flag("consecutive_failures", "0")
                return
        self.index.finish_render(job["id"], "failed", result.failure)
        log.warning("demo #%s (%s): %s render attempt %s of %s failed: %s", demo["id"], match["map"],
                    job["perspective"], job["attempt"], MAX_RENDER_ATTEMPTS, result.failure)
        failures = int(self.index.get_flag("consecutive_failures", "0")) + 1
        self.index.set_flag("consecutive_failures", str(failures))
        if failures >= PAUSE_AFTER_FAILURES:
            self.index.pause("failures")
            self.services.notify(
                "Rendering paused",
                f"{failures} renders failed in a row. Check HLAE/CS2 compatibility,"
                f" then resume rendering from the tray or the Status page.",
            )

    def _gate_is_clear(self) -> bool:
        status = self.services.gate.check()
        if not status.ok:
            self._waiting_reasons = status.reasons
        return status.ok

    def _gate_stays_clear(self, seconds: float, demo_id: int) -> bool:
        """Wait out the heads-up, giving way as soon as the Gate closes, a stop is requested or the
        Demo is to be deleted."""
        waited = 0.0
        while waited < seconds:
            if self._gives_way(demo_id):
                return False
            step = min(1.0, seconds - waited)
            self.services.sleep(step)
            waited += step
            if self._gives_way(demo_id):
                return False
            status = self.services.gate.check()
            if not status.ok:
                self._waiting_reasons = status.reasons
                return False
        return True

    # --- steps 6–7 -------------------------------------------------------------------------------

    def _join(self, demo) -> None:
        """Every view the Demo has rendered becomes Reels, one rendered before the settings dropped it too.
        A Reel already made stays as it is: its raw Clips may have been pruned since."""
        rendered = [perspective for perspective in PERSPECTIVES
                    if (job := self.index.latest_render(demo["id"], perspective)) is not None
                    and job["state"] == "done"]
        for highlight in self.index.selected_highlights(demo["match_checksum"]):
            for perspective in rendered:
                if self.index.has_reel(highlight["id"], perspective):
                    continue
                clips = [self.cfg.load_path(row["path"])
                         for row in self.index.clips_for(highlight["id"], perspective)]
                out = (self.cfg.library_dir / "videos" / demo["match_checksum"]
                       / f"r{highlight['round']}-{perspective}.mp4")
                duration = self.services.join(clips, out)
                self.index.save_reel(highlight["id"], perspective, self.cfg.store_path(out), duration)

    def _finish(self, demo) -> None:
        dem, archive = demo["dem_path"], demo["archive_path"]
        if dem and Path(dem) != Path(archive):
            Path(dem).unlink(missing_ok=True)         # the compressed download stays
        count = len(self.index.selected_highlights(demo["match_checksum"])) if demo["match_checksum"] else 0
        if count:
            match = self.index.match(demo["match_checksum"])
            self.services.notify("Highlights ready", f"{count} Highlights from {match['map']} are ready.")
        self.index.advance(demo["id"], "done")
        if demo["match_checksum"]:
            prune_renders(self.index, self.cfg, demo["match_checksum"])   # the Reels are made: the raw Clips go

    def _log_path(self, demo, name: str) -> Path:
        return self.cfg.logs_dir / f"demo-{demo['id']}-{name}.log"
