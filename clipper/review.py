"""A match as the subject played it (spec: Progress): their line, their Mistakes, and where their Frags and deaths
happened. Small enough to read for every match at once, which is what Progress and Heatmaps do.

Pure arithmetic: no JSON, no I/O. `analysis.py` reads csda's output into the records here and in `mistakes.py`
(ADR 0002) and keeps the document this makes beside the match's analysis.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from clipper import mistakes, rating, stats
from clipper.model import FaceitStats

VERSION = 1                      # raised when this or mistakes.py changes what a kept document would say
SIDES = {2: "T", 3: "CT"}        # csda's numbers for the sides


@dataclass(frozen=True)
class Place:
    """One Frag or death of the subject's on the map: where they stood, and where the other player was."""
    kind: str                    # "kill" or "death"
    round: int
    tick: int
    side: str                    # the subject's side then: "T" or "CT" ("" when csda names neither)
    x: float
    y: float
    other_x: float
    other_y: float
    weapon: str
    headshot: bool

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "round": self.round, "tick": self.tick, "side": self.side, "x": round(self.x, 1),
                "y": round(self.y, 1), "ox": round(self.other_x, 1), "oy": round(self.other_y, 1),
                "weapon": self.weapon, "headshot": self.headshot}


def line(player: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """The subject's numbers Progress tracks from match to match, from their entry in the match's stats: the headline
    ones (FACEIT's when the match has its line), the demo's own, and the Rating 2.0 estimate. None without stats."""
    if player is None:
        return None
    head = stats.headline(player)
    rounds = player.get("rounds") or 0
    multi = sum(int(count or 0) for count in head["multi_kills"].values())
    return {
        "source": head["source"], "rounds": rounds,
        "kills": head["kills"], "deaths": head["deaths"], "assists": head["assists"], "adr": head["adr"],
        "hs_pct": head["hs_pct"], "kd": head["kd"], "multi_kills": head["multi_kills"],
        "kast_pct": player.get("kast_pct"),
        "opening_kills": player.get("opening_kills", 0), "opening_deaths": player.get("opening_deaths", 0),
        "trade_kills": player.get("trade_kills", 0), "traded_deaths": player.get("traded_deaths", 0),
        "utility_damage": player.get("utility_damage", 0), "enemies_flashed": player.get("enemies_flashed", 0),
        "clutch_wins": player.get("clutch_wins", 0), "clutch_attempts": player.get("clutch_attempts", 0),
        "rating": round(rating.rating(FaceitStats(
            rounds=rounds, kills=int(head["kills"] or 0), deaths=int(head["deaths"] or 0),
            assists=int(head["assists"] or 0), adr=float(head["adr"] or 0.0), double_kills=multi)), 2),
    }


def document(subject: str, match: mistakes.Match, places: list[Place],
             player: Mapping[str, Any] | None) -> dict[str, Any]:
    """What the app keeps of a match for Progress and Heatmaps. JSON-ready."""
    return {
        "version": VERSION, "subject": subject,
        "line": line(player),
        "mistakes": [m.as_dict() for m in mistakes.find(match, subject)],
        "flashes_kept": match.flashes is not None,
        "places": [p.as_dict() for p in places],
    }
