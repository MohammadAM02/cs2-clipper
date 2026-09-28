"""The worker: moves each Demo through the Flow, one step per tick (spec: Flow).

spotted → unpacked → analyzed → scored → rendering → joined → done, or skipped / failed.
Every step can run again safely, so a crash or a reboot resumes where it stopped.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from clipper.config import Config
from clipper.gate import GUI_REASON, GateStatus
from clipper.index import Index
from clipper.join import JoinError, assign_clips
from clipper.model import MatchInfo, RoundFacts
from clipper.render import RenderRequest, RenderResult
from clipper.scoring import score_match, select

log = logging.getLogger(__name__)

ACTIVE_STATES = ("spotted", "unpacked", "analyzed", "scored", "rendering", "joined")
PERSPECTIVES = ("player", "enemy")
MAX_STEP_ATTEMPTS = 3
MAX_RENDER_ATTEMPTS = 3
PAUSE_AFTER_FAILURES = 3


class Skip(Exception):
    """This Demo should not be processed at all."""


class GiveUp(Exception):
    """This Demo cannot be finished; running the step again will not help."""


class IntakeLike(Protocol):
    def ready(self) -> list[Path]: ...
    def take(self, path: Path, index: Index) -> int | None: ...


class FactsSource(Protocol):
    def find_checksum(self, demo_name: str) -> str | None: ...
    def match_info(self, checksum: str, steamid: str) -> MatchInfo | None: ...
    def round_facts(self, checksum: str, steamid: str) -> list[RoundFacts]: ...


class GateLike(Protocol):
    def check(self) -> GateStatus: ...
    def faceit_running(self) -> bool: ...


class AlertsStep(Protocol):
    def tick(self) -> None: ...


@dataclass
class Services:
    """Everything the worker talks to. Tests swap in fakes."""

    intake: IntakeLike
    unpack: Callable[[Path, Path], Path]
    analyze: Callable[[Path, Path], str]
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


class Worker:
    def __init__(self, cfg: Config, index: Index, services: Services):
        self.cfg = cfg
        self.index = index
        self.services = services
        self._told_about_gui = False

    # --- the loop --------------------------------------------------------------------------------

    def tick(self) -> None:
        """Take any new Demos, move every unfinished Demo on by at most one step, then run match alerts."""
        for path in self.services.intake.ready():
            try:
                self.services.intake.take(path, self.index)
            except OSError:
                log.exception("could not take %s; will try again", path.name)
        for demo in self.index.demos_in(ACTIVE_STATES):
            self._advance(demo)
        if self.services.alerts is not None:
            try:
                self.services.alerts.tick()
            except Exception:  # noqa: BLE001 - match alerts must never stop the pipeline
                log.exception("match alerts failed")

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
        dem = Path(demo["dem_path"])
        self.services.analyze(dem, self._log_path(demo, "analyze"))
        checksum = self.services.facts.find_checksum(dem.stem)
        if checksum is None:
            raise RuntimeError("csdm analyze did not put the match in CS:DM's database")
        info = self.services.facts.match_info(checksum, self.cfg.subject_steamid)
        if info is None:
            raise Skip("the subject's SteamID is not in this match")
        self.index.save_match(info)
        self.index.advance(demo["id"], "analyzed", match_checksum=checksum)

    def _score(self, demo) -> None:
        facts = self.services.facts.round_facts(demo["match_checksum"], self.cfg.subject_steamid)
        highlights = score_match(facts)
        chosen = select(highlights, self.cfg.top_n)
        self.index.save_highlights(demo["match_checksum"], highlights, {h.round for h in chosen})
        self.index.advance(demo["id"], "scored")

    def _queue_renders(self, demo) -> None:
        if not self.index.selected_highlights(demo["match_checksum"]):
            self._finish(demo)                        # no Frags: nothing to render
            return
        for perspective in PERSPECTIVES:
            if self.index.latest_render(demo["id"], perspective) is None:
                self.index.queue_render(demo["id"], perspective, attempt=1)
        self.index.advance(demo["id"], "rendering")

    # --- step 5: rendering -----------------------------------------------------------------------

    def _render(self, demo) -> None:
        for perspective in PERSPECTIVES:
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
        if self.index.paused_by() is not None:
            return
        if not self._gate_is_clear():
            return
        highlights = self.index.selected_highlights(demo["match_checksum"])
        match = self.index.match(demo["match_checksum"])
        self.services.notify(
            "Rendering highlights",
            f"{len(highlights)} Highlights from {match['map']} ({job['perspective']} view). "
            f"CS2 opens in {self.cfg.heads_up_seconds:g} s — please don't start it yourself.",
        )
        if not self._gate_stays_clear(self.cfg.heads_up_seconds):
            return
        output_dir = _fresh_dir(
            self.cfg.renders_dir / demo["match_checksum"] / job["perspective"] / f"attempt-{job['attempt']}"
        )
        log_path = self._log_path(demo, f"render-{job['perspective']}-{job['attempt']}")
        self.index.start_render(job["id"], output_dir, log_path)
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
        )
        try:
            result = self.services.render(request, self.services.gate.faceit_running)
        except Exception as exc:  # noqa: BLE001 - a crashed render is a failed attempt
            log.exception("render crashed")
            result = RenderResult(ok=False, failure=f"render crashed: {exc}")
        if result.aborted:
            self.index.finish_render(job["id"], "aborted", result.failure)
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
                        self.index.add_clip(job["id"], ids[number], clip)
                self.index.finish_render(job["id"], "done")
                self.index.set_flag("consecutive_failures", "0")
                return
        self.index.finish_render(job["id"], "failed", result.failure)
        failures = int(self.index.get_flag("consecutive_failures", "0")) + 1
        self.index.set_flag("consecutive_failures", str(failures))
        if failures >= PAUSE_AFTER_FAILURES:
            self.index.pause("failures")
            self.services.notify(
                "Rendering paused",
                f"{failures} renders failed in a row. Check HLAE/CS2 compatibility, then run: clipper resume",
            )

    def _gate_is_clear(self) -> bool:
        status = self.services.gate.check()
        gui_open = GUI_REASON in status.reasons
        if gui_open and not self._told_about_gui:
            self.services.notify("Close CS Demo Manager", "Rendering is waiting for CS Demo Manager to close.")
        self._told_about_gui = gui_open
        return status.ok

    def _gate_stays_clear(self, seconds: float) -> bool:
        """Wait out the heads-up, giving up if the Gate closes meanwhile."""
        waited = 0.0
        while waited < seconds:
            step = min(1.0, seconds - waited)
            self.services.sleep(step)
            waited += step
            if not self.services.gate.check().ok:
                return False
        return True

    # --- steps 6–7 -------------------------------------------------------------------------------

    def _join(self, demo) -> None:
        for highlight in self.index.selected_highlights(demo["match_checksum"]):
            for perspective in PERSPECTIVES:
                clips = [Path(row["path"]) for row in self.index.clips_for(highlight["id"], perspective)]
                out = (self.cfg.library_dir / "videos" / demo["match_checksum"]
                       / f"r{highlight['round']}-{perspective}.mp4")
                duration = self.services.join(clips, out)
                self.index.save_reel(highlight["id"], perspective, out, duration)

    def _finish(self, demo) -> None:
        dem, archive = demo["dem_path"], demo["archive_path"]
        if dem and Path(dem) != Path(archive):
            Path(dem).unlink(missing_ok=True)         # the compressed download stays
        count = len(self.index.selected_highlights(demo["match_checksum"])) if demo["match_checksum"] else 0
        if count:
            match = self.index.match(demo["match_checksum"])
            self.services.notify("Highlights ready", f"{count} Highlights from {match['map']} are ready.")
        self.index.advance(demo["id"], "done")

    def _log_path(self, demo, name: str) -> Path:
        return self.cfg.logs_dir / f"demo-{demo['id']}-{name}.log"
