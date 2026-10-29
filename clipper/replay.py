"""The replay's positions: each player's place once a second (spec: Replay).

csda writes a position for every player on every frame when it runs with `-positions`, which is far too much to keep:
64 frames a second, ten players, most of a match. `analysis.py`, the only code that reads csda's output (ADR 0002),
reads those frames a piece at a time and gives each one as a `Position`; this keeps the ones that fall on a whole
second, as compact rows.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

VERSION = 1
FIELDS = ("tick", "round", "steam_id", "x", "y", "yaw", "alive", "side", "health")


@dataclass(frozen=True)
class Position:
    """One player's place on one frame of a Demo."""
    tick: int
    round: int
    steam_id: str
    x: float
    y: float
    yaw: float
    alive: bool
    side: int
    health: int


def sample(positions: Iterable[Position], tickrate: float) -> list[list]:
    """The rows (in FIELDS order) of the positions that fall on a whole second of the match."""
    step = round(tickrate)
    return [[getattr(p, name) for name in FIELDS] for p in positions if p.tick % step == 0]


def document(checksum: str, tickrate: float, rows: list[list]) -> dict[str, Any]:
    """What the app keeps for a match's positions."""
    return {"version": VERSION, "checksum": checksum, "tickrate": tickrate, "fields": list(FIELDS), "samples": rows}
