"""What the app is doing right now: one shared, thread-safe snapshot, and the one-line summary the
tray tooltip and the Status page's Now show (spec: How the app runs, The tray; Pages, Status).

The worker (Task 6) is the only writer; the tray, the Status page and the app (Tasks 9–14) only read
`summary(state.snapshot())`.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from threading import Lock

from clipper.alerts import map_label


@dataclass(frozen=True)
class Rendering:
    map_name: str          # CS:DM's map name, e.g. "de_mirage"
    perspective: str       # "player" | "enemy"
    started_at: float      # time.time() when the Render Job started


@dataclass(frozen=True)
class Snapshot:
    problems: tuple[str, ...] = ()      # start-up problems; while any exist the worker does not tick
    paused_by: str | None = None        # "you" | "failures" | None
    waiting: tuple[str, ...] = ()       # the Gate's reasons while a render waits for it
    rendering: Rendering | None = None
    quitting: str | None = None         # "now" | "after_render" | None
    pages_off: str | None = None        # why the web server has no port (the tray adds it to its tooltip)


def summary(snapshot: Snapshot) -> str:
    """The one line the tray tooltip and the Status page show. First match wins: quitting beats a
    start-up problem, which beats a render in progress, which beats being paused, which beats waiting
    for the Gate."""
    if snapshot.quitting is not None:
        if snapshot.quitting == "after_render" and snapshot.rendering is not None:
            return "Quitting after this render"
        return "Quitting…"
    if snapshot.problems:
        text = snapshot.problems[0]
        more = len(snapshot.problems) - 1
        return f"{text} (+{more} more)" if more else text
    if snapshot.rendering is not None:
        return f"Rendering {map_label(snapshot.rendering.map_name)} ({snapshot.rendering.perspective} view)"
    if snapshot.paused_by == "you":
        return "Paused by you"
    if snapshot.paused_by == "failures":
        return "Paused after repeated failed renders"
    if snapshot.waiting:
        return "Waiting: " + "; ".join(snapshot.waiting)
    return "Idle"


class AppState:
    """Thread-safe: one lock guards the snapshot, and every setter replaces it with a new frozen
    `Snapshot` under that lock, so a reader never sees a half-updated combination of fields."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._snapshot = Snapshot()

    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    def set_problems(self, problems: Iterable[str]) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, problems=tuple(problems))

    def set_paused_by(self, by: str | None) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, paused_by=by)

    def set_waiting(self, reasons: Iterable[str]) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, waiting=tuple(reasons), rendering=None)

    def set_rendering(self, rendering: Rendering) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, rendering=rendering, waiting=())

    def set_idle(self) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, waiting=(), rendering=None)

    def set_quitting(self, mode: str | None) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, quitting=mode)

    def set_pages_off(self, reason: str | None) -> None:
        with self._lock:
            self._snapshot = replace(self._snapshot, pages_off=reason)
