"""The stats of finished matches that have none, and FACEIT's line for each player (spec: Stats).

The Demo steps add a match's stats when they analyze it. This works through the finished matches from before that:
one csda run or one FACEIT request a tick, newest match first, and only while the pipeline has nothing to do and
neither CS2 nor FACEIT AC is open. A failure is tried again a day later, not on every tick.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path

from clipper import radar
from clipper.analysis import Analyses
from clipper.config import Config
from clipper.index import Index
from clipper.intake import faceit_match_id

REPLAY_REQUESTED = "replay_requested:"
REPLAY_FAILED = "replay_failed:"          # why the last try at a match's replay failed, for the page to show
REPLAY_TRIED = "replay_tried"

log = logging.getLogger(__name__)

RETRY_AFTER = timedelta(hours=24)
DONE = "done"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def request_replay(index: Index, checksum: str, now: datetime) -> None:
    """Asks for a match's replay data: its positions, and its radar. Worked out on a tick when CS2 and FACEIT AC are
    both closed. Asking again after a failure forgets the failure and tries at once, not a day later."""
    index.delete_flag(f"{REPLAY_TRIED}:{checksum}")
    index.delete_flag(REPLAY_FAILED + checksum)
    index.set_flag(REPLAY_REQUESTED + checksum, now.isoformat())


def replay_request(index: Index, checksum: str) -> tuple[bool, str | None]:
    """Whether a match's replay is asked for, and why its last try failed (None when it has not failed)."""
    return index.get_flag(REPLAY_REQUESTED + checksum) is not None, index.get_flag(REPLAY_FAILED + checksum)


class StatsBackfill:
    def __init__(self, index: Index, cfg: Config, analyses: Analyses, unpack: Callable[[Path, Path], Path],
                 faceit_stats: Callable[[str, str], bool] | None = None,
                 now: Callable[[], datetime] = _utc_now,
                 radar_source: Callable[[], Path | None] = lambda: None):
        self.index = index
        self.cfg = cfg
        self.analyses = analyses
        self.unpack = unpack
        self.faceit_stats = faceit_stats          # (checksum, FACEIT match id) -> whether the line was added
        self.now = now
        self.radar_source = radar_source          # CS2's game\csgo folder, or None when CS2 is not installed
        self._complete: set[str] = set()          # matches whose stats and FACEIT line are settled this run

    def tick(self, clear: Callable[[], bool]) -> None:
        """Works on one match, if one needs it: a replay asked for, then its stats, then FACEIT's line. `clear` says
        whether CS2 and FACEIT AC are both closed; it is asked only once there is something to do."""
        if self._replay_work(clear):
            return
        for demo in self._finished_demos():
            checksum = demo["match_checksum"]
            if checksum in self._complete:
                continue
            if not self.analyses.has_stats(checksum):
                if self._due("stats_tried", checksum) and clear():
                    self._work_out(demo)
                    return
                continue
            if self._faceit_settled(demo, checksum):
                self._complete.add(checksum)
                continue
            if self.faceit_stats is not None and self._due("faceit_tried", checksum) and clear():
                self.add_faceit(demo, checksum)
                return

    def add_faceit(self, demo, checksum: str) -> None:
        """Adds FACEIT's line for each player to the match's stats, when the Demo's FACEIT match is known. A failure is
        logged and tried again a day later: the match keeps the demo's own numbers meanwhile."""
        match_id = faceit_match_id(Path(demo["archive_path"]).name)
        if match_id is None:
            self.index.set_flag(f"faceit_stats:{checksum}", DONE)     # not a FACEIT Demo: no line to add
            return
        if self.faceit_stats is None:                                  # no FACEIT key in this copy
            return
        self._mark("faceit_tried", checksum)
        try:
            if self.faceit_stats(checksum, match_id):
                self.index.set_flag(f"faceit_stats:{checksum}", DONE)
        except Exception:  # noqa: BLE001 - FACEIT is a service the app does not control
            log.exception("demo #%s: FACEIT's stats for match %s could not be added; trying again in a day",
                          demo["id"], match_id)

    def _finished_demos(self) -> Iterator:
        """The finished Demos, one per match, newest download first."""
        seen: set[str] = set()
        for demo in sorted(self.index.demos_in(("done",)), key=lambda row: row["id"], reverse=True):
            checksum = demo["match_checksum"]
            if checksum and checksum not in seen:
                seen.add(checksum)
                yield demo

    def _faceit_settled(self, demo, checksum: str) -> bool:
        return (self.index.get_flag(f"faceit_stats:{checksum}") == DONE
                or faceit_match_id(Path(demo["archive_path"]).name) is None)

    def _work_out(self, demo) -> None:
        """Unpacks the Demo's download, analyzes it (which keeps its stats), and removes what it unpacked."""
        checksum = demo["match_checksum"]
        self._mark("stats_tried", checksum)
        archive = Path(demo["archive_path"])
        if not archive.is_file():
            log.warning("demo #%s: its download is gone, so its match keeps no stats", demo["id"])
            return
        dem = self.unpack(archive, self.cfg.demos_dir)
        try:
            again = self.analyses.analyze(dem, self.cfg.logs_dir / f"demo-{demo['id']}-stats.log")
        except Exception:  # noqa: BLE001 - a failure is tried again a day later
            log.exception("demo #%s: the stats could not be worked out; trying again in a day", demo["id"])
            return
        finally:
            if Path(dem) != archive:
                Path(dem).unlink(missing_ok=True)
        if again != checksum:
            log.warning("demo #%s: its analysis is match %s, not %s", demo["id"], again, checksum)
            return
        log.info("demo #%s: stats worked out for match %s", demo["id"], checksum)
        self.add_faceit(demo, checksum)

    def _due(self, name: str, checksum: str) -> bool:
        last = self.index.get_flag(f"{name}:{checksum}")
        return last is None or self.now() - datetime.fromisoformat(last) >= RETRY_AFTER

    def _mark(self, name: str, checksum: str) -> None:
        self.index.set_flag(f"{name}:{checksum}", self.now().isoformat())

    def _replay_work(self, clear: Callable[[], bool]) -> bool:
        """Works out the replay data of the first match asked for that lacks it. True when it did a unit of work (the
        tick is then done); False when there is nothing to do or the game holds the work."""
        for key in self.index.flags_with_prefix(REPLAY_REQUESTED):
            checksum = key[len(REPLAY_REQUESTED):]
            demo = next((row for row in self._finished_demos() if row["match_checksum"] == checksum), None)
            if demo is None:                             # no finished Demo of that match: nothing to replay
                self.index.set_flag(REPLAY_FAILED + checksum,
                                    "This match has no finished Demo to read the positions from.")
                self.index.delete_flag(key)
                continue
            if self.analyses.has_positions(checksum) and self._radar_ready(checksum):
                self.index.delete_flag(key)
                continue
            if self._due(REPLAY_TRIED, checksum) and clear():
                self._work_out_replay(demo, checksum)
                return True
        return False

    def _work_out_replay(self, demo, checksum: str) -> None:
        """Unpacks the Demo, has csda write its positions, and builds the radar of the map if it is not built yet. A
        failure keeps its reason for the page and stays asked for: it is tried again a day later, or when asked again."""
        self._mark(REPLAY_TRIED, checksum)
        archive = Path(demo["archive_path"])
        if not self.analyses.has_positions(checksum):
            if not archive.is_file():
                log.warning("demo #%s: its download is gone, so its replay cannot be made", demo["id"])
                self.index.set_flag(REPLAY_FAILED + checksum,
                                    "The Demo's download is gone, so its positions cannot be read.")
                return
            dem = None
            try:
                dem = self.unpack(archive, self.cfg.demos_dir)
                self.analyses.positions(checksum, dem, self.cfg.logs_dir / f"demo-{demo['id']}-positions.log")
            except Exception as exc:  # noqa: BLE001 - kept for the page, and tried again a day later
                log.exception("demo #%s: the positions could not be worked out; trying again in a day", demo["id"])
                self.index.set_flag(REPLAY_FAILED + checksum, str(exc)[:300] or type(exc).__name__)
                return
            finally:
                if dem is not None and Path(dem) != archive:
                    Path(dem).unlink(missing_ok=True)
        self._build_radar(checksum)
        self.index.delete_flag(REPLAY_REQUESTED + checksum)
        self.index.delete_flag(REPLAY_FAILED + checksum)
        log.info("demo #%s: replay data ready for match %s", demo["id"], checksum)

    def _radar_ready(self, checksum: str) -> bool:
        match = self.index.match(checksum)
        return match is None or radar.ready(match["map"], self.cfg.radar_dir) is not None

    def _build_radar(self, checksum: str) -> None:
        """Builds the radar of the match's map from CS2 when it is not built yet. Without CS2 there is no radar: the
        replay still shows the players."""
        match = self.index.match(checksum)
        if match is None or self._radar_ready(checksum):
            return
        csgo = self.radar_source()
        if csgo is None:
            log.info("no CS2 install found: the radar of %s is not built", match["map"])
            return
        try:
            radar.build(match["map"], csgo, self.cfg.radar_dir)
        except Exception:  # noqa: BLE001 - the radar is a picture: a failure leaves the replay without one
            log.exception("the radar of %s could not be built from CS2", match["map"])
