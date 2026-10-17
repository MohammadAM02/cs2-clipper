"""The worker: moves each Demo through the Flow, one step per tick (spec: Flow).

spotted → unpacked → analyzed → scored → rendering → joined → done, or skipped / failed.
Every step can run again safely, so a crash or a reboot resumes where it stopped. Nothing moves while CS2 is
open, so the game keeps every frame; each view is its own CS2 launch, yours first, and gets its Reels as soon as
it is recorded.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from clipper import links, share, voice
from clipper.breaks import talking, windows
from clipper.config import Config
from clipper.gate import CS2_REASON, FACEIT_REASON, STEAM_REASON, GateStatus
from clipper.hlae_plan import RenderInputs
from clipper.index import Index
from clipper.join import JoinError, assign_clips
from clipper.model import Break, MatchInfo, RoundFacts
from clipper.render import RenderProgress, RenderRequest, RenderResult
from clipper.scoring import score_match, select
from clipper.state import AppState, Rendering, view_name, views_label

log = logging.getLogger(__name__)

ACTIVE_STATES = ("spotted", "unpacked", "analyzed", "scored", "rendering", "joined")
PERSPECTIVES = ("player", "enemy")
READY_TITLES = {"player": "Your view ready", "enemy": "Enemy POV ready"}
FAILED_TITLES = {"player": "Couldn't render your view", "enemy": "Couldn't render the Enemy POV"}
MAX_STEP_ATTEMPTS = 3
MAX_RENDER_ATTEMPTS = 3
PAUSE_AFTER_FAILURES = 3
QUIT_ABORT = "aborted: the app was quit"
BACK_TO_BACK_SECONDS = 120.0       # a view this soon after CS2 closed for its Demo starts without a heads-up
_OLD_REEL = re.compile(r"r\d+-(player|enemy)\.mp4")      # a Reel named before Reels had readable names
SNOOZE_MINUTES = 60                # the heads-up toast's Not now
STUCK_TOAST_SECONDS = 120.0        # how long a Gate held only by what the player can fix waits before a toast says so
# What the player can fix themselves, and how a toast asks them to.
_FIXES = {FACEIT_REASON: "close FACEIT AC", STEAM_REASON: "open Steam"}


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
    def render_inputs(self, checksum: str) -> RenderInputs | None: ...


class GateLike(Protocol):
    def check(self) -> GateStatus: ...
    def faceit_running(self) -> bool: ...
    def cs2_running(self) -> bool: ...
    def faceit_running(self) -> bool: ...


class AlertsStep(Protocol):
    def tick(self) -> None: ...
    def check_now(self) -> str | None: ...


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
    render: Callable[[RenderRequest, Callable[[], bool], Callable[[RenderProgress], None]], RenderResult]
    join: Callable[[list[Path], Path], float]
    notify: Callable[..., None]
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    alerts: AlertsStep | None = None
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    talk_ticks: Callable[[Path], list[int]] = voice.talk_ticks     # the tick of each voice message in a .dem


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


def reel_path(index: Index, cfg: Config, checksum: str, round_number: int, highlight_type: str,
              perspective: str) -> Path:
    """Where a Reel of the match goes: its match's folder under library\\videos ("2026-10-12 Dust2 13-9"), the folder
    its other Reels are in once it has any, named as it would be posted ("ACE Dust2 R9 s1mple.mp4"). A folder another
    match has taken already (same day, map and score) gets the match's checksum after its name."""
    match = index.match(checksum)
    name = share.reel_name(highlight_type, match["map"], round_number, cfg.faceit_nickname, perspective) + ".mp4"
    videos = cfg.library_dir / "videos"
    others: set[Path] = set()
    for reel in index.all_reel_details():
        folder = cfg.load_path(reel["path"]).parent
        if reel["checksum"] != checksum:
            others.add(folder)
        elif folder.parent == videos and not _OLD_REEL.fullmatch(cfg.load_path(reel["path"]).name):
            return folder / name                      # where its other Reels are
    folder = videos / share.match_folder(match["map"], match["played_at"], match["team_score"],
                                         match["opponent_score"])
    return (folder if folder not in others else folder.with_name(f"{folder.name} ({checksum})")) / name


def rename_old_reels(index: Index, cfg: Config) -> int:
    """Gives each Reel made before Reels had readable names ("library\\videos\\<checksum>\\r9-player.mp4") its readable
    name and folder, and leaves an emptied checksum folder gone. One that cannot move now (being played, say) is
    logged and tried at the next start. How many moved."""
    moved = 0
    for reel in index.all_reel_details():
        old = cfg.load_path(reel["path"])
        if not _OLD_REEL.fullmatch(old.name) or not old.is_file():
            continue
        new = reel_path(index, cfg, reel["checksum"], reel["round"], reel["type"], reel["perspective"])
        try:
            new.parent.mkdir(parents=True, exist_ok=True)
            os.replace(old, new)
        except OSError as exc:
            log.warning("could not give %s its readable name yet: %s", old, exc)
            continue
        index.set_reel_path(reel["id"], cfg.store_path(new))
        moved += 1
        try:
            old.parent.rmdir()                        # only once it is empty
        except OSError:
            pass
    if moved:
        log.info("%s Reel%s got readable names", moved, "" if moved == 1 else "s")
    return moved


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
        self._last_render: tuple[int, float] | None = None   # (Demo id, clock time) of the last render not aborted
        self._stuck: tuple[tuple[str, ...], float, bool] | None = None   # (reasons, since, said) while only fixable

    # --- the loop --------------------------------------------------------------------------------

    def tick(self) -> None:
        """Take any new Demos, move every unfinished Demo on by at most one step, then run match
        alerts. Only the oldest Demo in rendering gets a step there, so one map has every view it renders
        done before the next map's first. A view the settings no longer render comes off every Demo's queue,
        except the one a Demo asked for (`ask_render`).
        Once a stop is requested, no new step starts (a running one, a render included,
        finishes on its own terms); publishes the "what it's doing" summary throughout, and always
        ends idle or waiting, never stuck saying "rendering"."""
        self.state.set_paused_by(self.index.paused_by())
        self._waiting_reasons = ()
        try:
            if not self._pruned_at_start:
                self._pruned_at_start = True
                self._prune_done_matches()
                rename_old_reels(self.index, self.cfg)
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
            demos = self.index.demos_in(ACTIVE_STATES)
            if demos and self.services.gate.cs2_running():
                self._waiting_reasons = (CS2_REASON,)  # someone is playing: no unpack, analysis, render or join
                demos = []
            for demo in demos:
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
        return self.stop.stopping() or self.deletes.asked(demo_id) or self._snoozed()

    def _snoozed(self) -> bool:
        """Whether renders are snoozed (the heads-up toast's Not now); says until when, as the reason to wait."""
        until = self.index.snoozed_until(self.services.now())
        if until is None:
            return False
        self._waiting_reasons = (f"snoozed until {until.astimezone():%H:%M}",)
        return True

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
        if chosen:                                    # a Break is recorded with the Highlights, never by itself
            self._find_breaks(demo)
        self.index.advance(demo["id"], "scored")

    def _find_breaks(self, demo) -> None:
        """Keeps the match's Breaks someone talks in, for its render to record in your view. A Demo whose voice
        cannot be read keeps none and goes on to render its Highlights."""
        inputs = self.services.facts.render_inputs(demo["match_checksum"])
        if inputs is None:
            return
        try:
            ticks = self.services.talk_ticks(Path(demo["dem_path"]))
        except (OSError, voice.VoiceError) as exc:
            log.warning("demo #%s: no Breaks, as its voice could not be read: %s", demo["id"], exc)
            return
        self.index.save_breaks(demo["match_checksum"], talking(windows(inputs.rounds, inputs.max_rounds), ticks))

    def _queue_renders(self, demo) -> None:
        if not self.index.selected_highlights(demo["match_checksum"]):
            self._finish(demo)                        # no Frags: nothing to render
            return
        for perspective in self.cfg.perspectives_to_render:
            if self.index.latest_render(demo["id"], perspective) is None:
                self.index.queue_render(demo["id"], perspective, attempt=1)
        self.index.advance(demo["id"], "rendering")

    # --- step 5: rendering -----------------------------------------------------------------------

    def _wanted(self, demo) -> tuple[str, ...]:
        """The views `demo`'s render steps are for: the views it was asked for (`ask_render`), else the views the
        settings render. Round mode has no enemy view. Player first."""
        wanted = self.index.asked_renders(demo["id"]) or self.cfg.perspectives_to_render
        if self.cfg.sequence_event == "rounds":
            wanted = tuple(perspective for perspective in wanted if perspective != "enemy")
        return tuple(perspective for perspective in PERSPECTIVES if perspective in wanted)

    def _render(self, demo) -> None:
        if demo["dem_path"] is None or not Path(demo["dem_path"]).is_file():
            self._unpack_again(demo)                  # the Demo is back for a view: `_finish` took its .dem
            return
        asked = self.index.asked_renders(demo["id"])
        jobs = []                                     # the Render Jobs of the views not done yet, player first
        for perspective in self._wanted(demo):
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
                    if perspective in asked or self._shipped(demo):   # the Demo keeps the Reels it has
                        self._drop_asks(demo, (perspective,),
                                        f"it failed {job['attempt']} times, last with: {job['failure']}")
                        return
                    raise GiveUp(f"{perspective} render failed {job['attempt']} times: {job['failure']}")
                self.index.queue_render(demo["id"], perspective, attempt=job["attempt"] + 1)
                job = self.index.latest_render(demo["id"], perspective)
            elif job["state"] == "aborted":            # FACEIT AC appeared: go again, not counted
                self.index.queue_render(demo["id"], perspective, attempt=job["attempt"])
                job = self.index.latest_render(demo["id"], perspective)
            jobs.append(job)
        if not jobs:
            self._join(demo)
            self.index.advance(demo["id"], "joined")
            return
        if self._ship_rendered_views(demo):
            return                                    # one step per tick: the next view records on the next
        self._try_render(demo, jobs[:1])              # one view per CS2 launch, yours first

    def _ship_rendered_views(self, demo) -> bool:
        """A view recorded while another is still to come gets its Reels now, and a toast says so, rather than
        waiting for the other: your view is ready before the Enemy POV records. True when it made any. The next
        view's launch gets its heads-up, since the player may be watching these."""
        rendered = [perspective for perspective in self._wanted(demo) if self._lacks_reels(demo, perspective)]
        if not rendered:
            return False
        self._join(demo)
        for perspective in rendered:
            self._announce(demo, perspective, more_to_come=True)
        self.index.set_flag(f"shipped:{demo['id']}", ",".join(sorted(self._shipped(demo) | set(rendered))))
        self._last_render = None
        return True

    def _shipped(self, demo) -> set[str]:
        """The views of `demo` whose Reels were made, and announced, while another view was still to record."""
        return set(filter(None, (self.index.get_flag(f"shipped:{demo['id']}") or "").split(",")))

    def _lacks_reels(self, demo, perspective: str) -> bool:
        """Whether `perspective` is recorded for `demo` but some selected Highlight has no Reel of it yet."""
        job = self.index.latest_render(demo["id"], perspective)
        return job is not None and job["state"] == "done" and any(
            not self.index.has_reel(highlight["id"], perspective)
            for highlight in self.index.selected_highlights(demo["match_checksum"]))

    def _unpack_again(self, demo) -> None:
        """Unpack the download the Demo kept, again, once its .dem is gone: one step per tick. With no download
        left, a Demo asked for a view drops its asks (`_drop_asks`); any other Demo fails."""
        archive = Path(demo["archive_path"])
        if not archive.is_file():
            asked = self.index.asked_renders(demo["id"])
            if asked:
                self._drop_asks(demo, asked, "its download is gone")
                return
            raise GiveUp("its download is gone")
        dem = self.services.unpack(archive, self.cfg.demos_dir)
        self.index.advance(demo["id"], "rendering", dem_path=dem)

    def _drop_asks(self, demo, perspectives, why: str) -> None:
        """The views the Demo was asked for cannot be rendered (`why`): says so, forgets the asks, and finishes the
        Demo as it was, done with the Reels it has. It stays done rather than failed, so its match keeps its Reels."""
        match = self.index.match(demo["match_checksum"])
        for perspective in perspectives:
            self.services.notify(FAILED_TITLES[perspective], f"{match['map']}: {why}. The match keeps its Reels.")
        log.warning("demo #%s (%s): not rendering %s, so the Demo stays done without it: %s",
                    demo["id"], match["map"], " and ".join(perspectives), why)
        self.index.clear_asked_renders(demo["id"])
        self._settle(demo)

    def _try_render(self, demo, jobs) -> None:
        """One CS2 launch for `jobs`: a view, or both views of the Demo, which share one heads-up and one log."""
        if self._gives_way(demo["id"]):
            return
        if self.index.paused_by() is not None:
            return
        if not self._gate_is_clear():
            return
        self._ensure_analysis(demo)
        highlights = self.index.selected_highlights(demo["match_checksum"])
        match = self.index.match(demo["match_checksum"])
        perspective = "both" if len(jobs) == 2 else jobs[0]["perspective"]
        if self._back_to_back(demo["id"]):
            if self._gives_way(demo["id"]) or not self._gate_is_clear():
                return                                # the last look the heads-up would have had
            log.info("demo #%s (%s) follows straight on from the last render, so no heads-up",
                     demo["id"], views_label(perspective))
        else:
            self.services.notify(
                "Rendering highlights",
                f"{len(highlights)} Highlights from {match['map']} ({views_label(perspective)}). "
                f"CS2 opens in {self.cfg.heads_up_seconds:g} s — please don't start it yourself.",
                actions=((f"Not now ({SNOOZE_MINUTES // 60} h)", links.to_snooze(SNOOZE_MINUTES)),),
            )
            if not self._gate_stays_clear(self.cfg.heads_up_seconds, demo["id"]):
                return
        log_path = self._log_path(demo, f"render-{perspective}-{jobs[0]['attempt']}")
        outputs = {}
        for job in jobs:
            output_dir = _fresh_dir(
                self.cfg.renders_dir / demo["match_checksum"] / job["perspective"] / f"attempt-{job['attempt']}"
            )
            self.index.start_render(job["id"], output_dir, log_path)
            outputs[job["perspective"]] = output_dir
        self.state.set_rendering(Rendering(match["map"], perspective, time.time()))
        request = RenderRequest(
            demo_path=Path(demo["dem_path"]),
            outputs=outputs,
            rounds=tuple(h["round"] for h in highlights),
            log_path=log_path,
            steamid=self.cfg.subject_steamid,
            padding_before_s=self.cfg.padding_before_s,
            padding_after_s=self.cfg.padding_after_s,
            event=self.cfg.sequence_event,
            width=self.cfg.video_size[0],
            height=self.cfg.video_size[1],
            checksum=demo["match_checksum"],
            breaks=self._breaks(demo) if any(job["perspective"] == "player" for job in jobs) else (),
        )
        try:
            result = self.services.render(
                request, lambda: (self.services.gate.faceit_running() or self.stop.abort_render()
                                  or self.deletes.asked(demo["id"])),
                self.state.set_progress,
            )
        except Exception as exc:  # noqa: BLE001 - a crashed render is a failed attempt
            log.exception("render crashed")
            result = RenderResult(ok=False, failure=f"render crashed: {exc}")
        if result.aborted:
            self._last_render = None                  # an abort: the next render warns again
            failure = QUIT_ABORT if self.stop.abort_render() else result.failure
            for job in jobs:
                self.index.finish_render(job["id"], "aborted", failure)
            return
        self._last_render = (demo["id"], self.services.clock())   # CS2 has just closed
        failed = False
        for job in jobs:
            failure = self._save_clips(demo, highlights, job, result)
            if failure is None:
                self.index.finish_render(job["id"], "done")
                continue
            failed = True
            self.index.finish_render(job["id"], "failed", failure)
            log.warning("demo #%s (%s): %s render attempt %s of %s failed: %s", demo["id"], match["map"],
                        job["perspective"], job["attempt"], MAX_RENDER_ATTEMPTS, failure)
        if not failed:
            self.index.set_flag("consecutive_failures", "0")
            return
        failures = int(self.index.get_flag("consecutive_failures", "0")) + 1    # one per launch that failed
        self.index.set_flag("consecutive_failures", str(failures))
        if failures >= PAUSE_AFTER_FAILURES:
            self.index.pause("failures")
            self.services.notify(
                "Rendering paused",
                f"{failures} renders failed in a row. Check HLAE/CS2 compatibility,"
                f" then resume rendering from the tray or the Status page.",
            )

    def _save_clips(self, demo, highlights, job, result: RenderResult) -> str | None:
        """Keeps the Clips `result` gives for `job`'s view, each under its Highlight. Returns why the view failed,
        or None when its Clips are kept. A view the launch did not record fails with the launch's reason."""
        if job["perspective"] not in result.clips:
            return result.failure or f"no Clips of the {job['perspective']} view"
        try:
            groups = assign_clips(result.clips[job["perspective"]],
                                  [(h["round"], h["round_start_tick"]) for h in highlights])
        except JoinError as exc:
            return str(exc)
        ids = {h["round"]: h["id"] for h in highlights}
        for number, clips in groups.items():
            for clip in clips:
                self.index.add_clip(job["id"], ids[number], clip, self.cfg.store_path(clip.path))
        if job["perspective"] == "player":            # a Break is recorded in your view only
            breaks = {row["after_round"]: row["id"] for row in self.index.breaks(demo["match_checksum"])}
            for found, clip in result.breaks:
                if found.after_round in breaks:
                    self.index.add_clip(job["id"], breaks[found.after_round], clip, self.cfg.store_path(clip.path))
        return None

    def _breaks(self, demo) -> tuple[Break, ...]:
        return tuple(Break(row["after_round"], row["start_tick"], row["end_tick"], row["label"])
                     for row in self.index.breaks(demo["match_checksum"]))

    def _back_to_back(self, demo_id: int) -> bool:
        """True when the last render not aborted was of this same Demo and ended less than BACK_TO_BACK_SECONDS
        ago: CS2 has only just closed for this match, so its next view starts without a heads-up."""
        if self._last_render is None:
            return False
        last_demo_id, ended = self._last_render
        return last_demo_id == demo_id and self.services.clock() - ended < BACK_TO_BACK_SECONDS

    def _gate_is_clear(self) -> bool:
        status = self.services.gate.check()
        if not status.ok:
            self._waiting_reasons = status.reasons
            self._last_render = None                  # someone may be at the PC: the next render warns again
        self._say_when_stuck(status.reasons)
        return status.ok

    def _say_when_stuck(self, reasons: tuple[str, ...]) -> None:
        """Once the Gate has been held for STUCK_TOAST_SECONDS only by what the player can fix (FACEIT AC left
        running, Steam closed), a toast says so, once until the reasons change: otherwise the wait is silent and
        the app looks broken. While CS2 runs or the disk is full there is nothing to ask."""
        if not reasons or any(reason not in _FIXES for reason in reasons):
            self._stuck = None
            return
        now = self.services.clock()
        if self._stuck is None or self._stuck[0] != reasons:
            self._stuck = (reasons, now, False)
            return
        _, since, said = self._stuck
        if said or now - since < STUCK_TOAST_SECONDS:
            return
        self._stuck = (reasons, since, True)
        matches = len({demo["match_checksum"] for demo in self.index.demos_in(("rendering",))})
        fixes = " and ".join(_FIXES[reason] for reason in reasons)
        why = " CS2 Clipper never opens CS2 while FACEIT AC runs." if FACEIT_REASON in reasons else ""
        self.services.notify(f"{matches} match{'' if matches == 1 else 'es'} ready to clip",
                             f"{fixes[0].upper()}{fixes[1:]} to start.{why}")

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
                out = reel_path(self.index, self.cfg, demo["match_checksum"], highlight["round"], highlight["type"],
                                perspective)
                duration = self.services.join(clips, out)
                self.index.save_reel(highlight["id"], perspective, self.cfg.store_path(out), duration)
        if "player" in rendered:
            self._join_breaks(demo)

    def _join_breaks(self, demo) -> None:
        """Each Break your view recorded becomes its Reel, of its one Clip. One the render had no room for among the
        Highlights' Sequences (`hlae_plan.add_breaks`) has no Clip and gets none."""
        for row in self.index.breaks(demo["match_checksum"]):
            clips = [self.cfg.load_path(clip["path"]) for clip in self.index.clips_for(row["id"], "player")]
            if not clips or self.index.has_reel(row["id"], "player"):
                continue
            out = reel_path(self.index, self.cfg, demo["match_checksum"], 0, row["label"], "player")
            self.index.save_reel(row["id"], "player", self.cfg.store_path(out), self.services.join(clips, out))

    def _finish(self, demo) -> None:
        asked = self.index.asked_renders(demo["id"])
        shipped = self._shipped(demo)
        if asked:
            self._notify_ready(demo, asked)           # the view asked for is ready, not the whole match
        elif shipped:                                 # your view was announced as it landed: the rest now
            self._notify_ready(demo, tuple(p for p in PERSPECTIVES if p not in shipped))
        elif demo["match_checksum"] and self.index.selected_highlights(demo["match_checksum"]):
            first = next((p for p in self._wanted(demo) if self.index.best_reel(demo["match_checksum"], p)), None)
            self._announce(demo, first or "player", more_to_come=False)
        self.index.clear_asked_renders(demo["id"])
        self._settle(demo)

    def _announce(self, demo, perspective: str, *, more_to_come: bool) -> None:
        """"Highlights ready", with Watch (the best Highlight's Reel of `perspective`, in the app window) and Show
        in folder."""
        match = self.index.match(demo["match_checksum"])
        count = len(self.index.selected_highlights(demo["match_checksum"]))
        later = " The Enemy POV records next." if more_to_come else ""
        self.services.notify("Highlights ready", f"{count} Highlights from {match['map']} are ready.{later}",
                             actions=self._watch(demo["match_checksum"], perspective))

    def _notify_ready(self, demo, perspectives) -> None:
        """Says each of `perspectives` is ready, once it has Reels: how many Highlights have them."""
        match = self.index.match(demo["match_checksum"])
        highlights = self.index.selected_highlights(demo["match_checksum"])
        for perspective in perspectives:
            count = sum(self.index.has_reel(highlight["id"], perspective) for highlight in highlights)
            if count:
                self.services.notify(READY_TITLES[perspective],
                                     f"{count} Highlights from {match['map']} now have {view_name(perspective)}.",
                                     actions=self._watch(demo["match_checksum"], perspective))

    def _watch(self, checksum: str, perspective: str) -> tuple[tuple[str, str], ...]:
        """A ready toast's buttons: Watch plays the match's best Reel of `perspective` in the app window, and Show
        in folder selects its file in Explorer. None without a Reel to show."""
        reel = self.index.best_reel(checksum, perspective)
        if reel is None:
            return ()
        return (("Watch", links.to_page(f"/reels?play={reel}")), ("Show in folder", links.to_reveal(reel)))

    def _settle(self, demo) -> None:
        """The Demo is done: its unpacked .dem goes (the compressed download stays), and its match's raw Clips go
        once every Demo of the match is done. Shared by a finished Demo and one whose asks were dropped."""
        dem, archive = demo["dem_path"], demo["archive_path"]
        if dem and Path(dem) != Path(archive):
            Path(dem).unlink(missing_ok=True)         # the compressed download stays
        self.index.delete_flag(f"shipped:{demo['id']}")
        self.index.advance(demo["id"], "done")
        if demo["match_checksum"]:
            prune_renders(self.index, self.cfg, demo["match_checksum"])   # the Reels are made: the raw Clips go

    def _log_path(self, demo, name: str) -> Path:
        return self.cfg.logs_dir / f"demo-{demo['id']}-{name}.log"
