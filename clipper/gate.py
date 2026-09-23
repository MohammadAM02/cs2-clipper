"""The Gate: may the pipeline launch CS2 right now? (spec: The Gate)"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psutil

from clipper.procs import ProcessProbe

CS2_REASON = "CS2 is running"
FACEIT_REASON = "FACEIT AC is running"
GUI_REASON = "CS Demo Manager is open"
FACEIT_SERVICE = "FACEITService"
GB = 1024**3


@dataclass(frozen=True)
class GateStatus:
    ok: bool
    reasons: tuple[str, ...] = ()


def _free_bytes(path: Path) -> int:
    return psutil.disk_usage(path.anchor or str(path)).free


class Gate:
    """Conditions 1–4 of the spec. Condition 5 (no other Render Job running) holds by construction:
    one worker renders at a time, and a lock file keeps a single app instance."""

    def __init__(self, probe: ProcessProbe, data_root: Path, min_free_gb: float,
                 free_bytes: Callable[[Path], int] = _free_bytes):
        self._probe = probe
        self._data_root = data_root
        self._min_free_gb = min_free_gb
        self._free_bytes = free_bytes

    def faceit_running(self) -> bool:
        return self._faceit_running(self._probe.names())

    def _faceit_running(self, names: set[str]) -> bool:
        return self._probe.service_running(FACEIT_SERVICE) or any(name.startswith("faceit") for name in names)

    def check(self) -> GateStatus:
        names = self._probe.names()
        reasons = []
        if "cs2.exe" in names:
            reasons.append(CS2_REASON)
        if self._faceit_running(names):
            reasons.append(FACEIT_REASON)
        if "cs-demo-manager.exe" in names:
            reasons.append(GUI_REASON)
        drive = self._data_root.anchor or str(self._data_root)
        try:
            if self._free_bytes(self._data_root) < self._min_free_gb * GB:
                reasons.append(f"less than {self._min_free_gb:g} GB free on {drive}")
        except OSError:
            reasons.append(f"{drive} is not available")
        return GateStatus(ok=not reasons, reasons=tuple(reasons))
