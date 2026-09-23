"""Plain data passed between modules: no behaviour beyond simple derived values, and no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


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


@dataclass(frozen=True)
class MatchInfo:
    """A match as seen from the subject's team."""

    checksum: str
    map_name: str
    played_at: datetime
    team_score: int
    opponent_score: int

    @property
    def result(self) -> str:
        if self.team_score > self.opponent_score:
            return "win"
        if self.team_score < self.opponent_score:
            return "loss"
        return "tie"


@dataclass(frozen=True)
class ClipFile:
    """One rendered Sequence. CS:DM names it sequence-<n>-tick-<start>-to-<end>.mp4."""

    sequence: int
    start_tick: int
    end_tick: int
    path: Path
    duration_s: float
