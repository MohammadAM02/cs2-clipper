"""Plain data passed between modules: no behaviour beyond simple derived values, and no I/O."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoundFacts:
    """What the subject did in one round, straight from CS:DM's tables."""

    round: int
    frag_ticks: tuple[int, ...]
    headshots: int = 0
    blind_frags: int = 0
    knife_frags: int = 0
    noscope_frags: int = 0
    smoke_frags: int = 0
    bot_frags: int = 0
    round_won: bool = False
    round_start_tick: int = 0
    round_end_tick: int = 0

    @property
    def frags(self) -> int:
        return len(self.frag_ticks)


@dataclass(frozen=True)
class Highlight:
    """A scored round: it becomes one Reel per Perspective if selected."""

    round: int
    type: str
    score: int
    reasons: tuple[str, ...]
    frag_ticks: tuple[int, ...]
    round_start_tick: int
    round_end_tick: int
