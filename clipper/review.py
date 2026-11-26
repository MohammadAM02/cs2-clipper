"""A match as the subject played it (spec: Progress): their line, their Mistakes, and where their Frags and deaths
happened. Small enough to read for every match at once, which is what Progress and Heatmaps do.

Pure arithmetic: no JSON, no I/O. `analysis.py` reads csda's output into the records here and in `mistakes.py`
(ADR 0002) and keeps the document this makes beside the match's analysis.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from clipper import mistakes, stats

VERSION = 2                      # raised when this or mistakes.py changes what a kept document would say
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
    """The subject's numbers Progress tracks from match to match, from their entry in the match's stats: FACEIT's
    official number for each field FACEIT reports when the match has FACEIT's line, else the demo's
    (`stats.official`), KAST, trades and flash assists from the demo, and HLTV Rating 2.1. None without stats."""
    if player is None:
        return None
    o = stats.official(player)
    return {
        "source": o["source"], "rounds": o["rounds"],
        "kills": o["kills"], "deaths": o["deaths"], "assists": o["assists"], "adr": o["adr"],
        "hs_pct": o["hs_pct"], "kd": o["kd"], "multi_kills": o["multi_kills"], "kast_pct": o["kast_pct"],
        "opening_kills": o["entry_won"] or 0, "opening_deaths": max(0, (o["entry_tried"] or 0) - (o["entry_won"] or 0)),
        "first_kills": o["first_kills"] or 0,
        "trade_kills": o["trade_kills"], "traded_deaths": o["traded_deaths"],
        "utility_damage": o["utility_damage"] or 0, "enemies_flashed": o["enemies_flashed"] or 0,
        "flash_assists": player.get("flash_assists", 0),
        "clutch_wins": o["clutch_won"] or 0, "clutch_attempts": o["clutch_tried"] or 0,
        "rating": o["rating"],
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
