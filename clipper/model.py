"""Plain data passed between modules: no behaviour beyond simple derived values, and no I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class RoundFacts:
    """What the subject did in one round, straight from csda's analysis of the match."""

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
    """One rendered Sequence, named sequence-<n>-tick-<start>-to-<end>.mp4 (`hlae_plan.clip_name`)."""

    sequence: int
    start_tick: int
    end_tick: int
    path: Path
    duration_s: float


@dataclass(frozen=True)
class FaceitStats:
    """The subject's line in one finished FACEIT match, as FACEIT's match stats report it."""

    map_name: str = ""
    team_score: int = 0
    opponent_score: int = 0
    won: bool = False
    rounds: int = 0
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    adr: float = 0.0
    double_kills: int = 0
    triple_kills: int = 0
    quadro_kills: int = 0
    penta_kills: int = 0

    @property
    def highlights(self) -> dict[str, int]:
        """Rounds with 3+ Frags, by size: what the clipping rule looks for."""
        return {"3k": self.triple_kills, "4k": self.quadro_kills, "5k": self.penta_kills}

    @property
    def multi_kill_rounds(self) -> int:
        return self.double_kills + self.triple_kills + self.quadro_kills + self.penta_kills
