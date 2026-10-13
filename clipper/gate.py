"""The Gate: may the pipeline launch CS2 right now? (spec: The Gate)"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import psutil

from clipper.procs import ProcessProbe

CS2_REASON = "CS2 is running"
FACEIT_REASON = "FACEIT AC is running"
STEAM_REASON = "Steam is not running"        # CS2 cannot start without it, and the render fails three times
FACEIT_SERVICE = "FACEITService"
GB = 1024**3
# Settings -> Start renders (config.RENDER_WHEN): "idle" waits until nobody has touched the PC for IDLE_MINUTES;
# "overnight" waits for NIGHT_START to NIGHT_END o'clock, local time.
IDLE_MINUTES = 10
NIGHT_START, NIGHT_END = 1, 7
IDLE_REASON = f"waiting until the PC has been idle for {IDLE_MINUTES} min"
NIGHT_REASON = f"waiting for the night ({NIGHT_START} am to {NIGHT_END} am)"


@dataclass(frozen=True)
class GateStatus:
    ok: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Schedule:
    """When renders may start (Settings -> Render), with how the PC's idle time and the local time are read."""

    when: str = "anytime"
    idle_seconds: Callable[[], float] = lambda: float("inf")
    local_now: Callable[[], datetime] = datetime.now

    def reason(self) -> str | None:
        """Why a render must wait for the schedule now, or None when it may start."""
        if self.when == "idle" and self.idle_seconds() < IDLE_MINUTES * 60:
            return IDLE_REASON
        if self.when == "overnight" and not NIGHT_START <= self.local_now().hour < NIGHT_END:
            return NIGHT_REASON
        return None


def _free_bytes(path: Path) -> int:
    return psutil.disk_usage(path.anchor or str(path)).free


class Gate:
    """Conditions 1 to 3 of the spec, plus Steam running and the render schedule. Condition 4 (no other Render
    Job running) holds by construction: one worker renders at a time, and a lock file keeps a single app
    instance."""

    def __init__(self, probe: ProcessProbe, data_root: Path, min_free_gb: float,
                 free_bytes: Callable[[Path], int] = _free_bytes, *, schedule: Schedule = Schedule()):
        self._probe = probe
        self._data_root = data_root
        self._min_free_gb = min_free_gb
        self._free_bytes = free_bytes
        self._schedule = schedule

    def faceit_running(self) -> bool:
        return self._faceit_running(self._probe.names())

    def cs2_running(self) -> bool:
        """Whether CS2 is open: then nothing heavy runs, not even an analysis, so the game keeps every frame."""
        return "cs2.exe" in self._probe.names()

    def _faceit_running(self, names: set[str]) -> bool:
        return self._probe.service_running(FACEIT_SERVICE) or any(name.startswith("faceit") for name in names)

    def check(self) -> GateStatus:
        names = self._probe.names()
        reasons = []
        if "cs2.exe" in names:
            reasons.append(CS2_REASON)
        if self._faceit_running(names):
            reasons.append(FACEIT_REASON)
        if "steam.exe" not in names:
            reasons.append(STEAM_REASON)
        drive = self._data_root.anchor or str(self._data_root)
        try:
            if self._free_bytes(self._data_root) < self._min_free_gb * GB:
                reasons.append(f"less than {self._min_free_gb:g} GB free on {drive}")
        except OSError:
            reasons.append(f"{drive} is not available")
        waiting = self._schedule.reason()
        if waiting:
            reasons.append(waiting)
        return GateStatus(ok=not reasons, reasons=tuple(reasons))
