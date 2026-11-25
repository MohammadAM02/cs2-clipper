"""How the subject's play changes from match to match (spec: Progress): their form over their last matches against
the matches before, each map's numbers, their Mistakes by Type, and their records.

Pure arithmetic over `Played` records, one per match with stats, newest first: no JSON, no I/O. `stats_view.py`
makes the records from the index and each match's review (`review.py`).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from clipper import mistakes

WINDOWS = (10, 20, 50)        # how many of the last matches the form can be asked over
DEFAULT_WINDOW = 20
# The form's numbers, in the order the page shows them: (key, whether more is better).
FORM = (("win_pct", True), ("rating", True), ("kd", True), ("adr", True), ("hs_pct", True), ("kast_pct", True),
        ("opening_win_pct", True), ("mistakes", False))
# The records: (key, the line's field, what the page calls it).
RECORDS = (("kills", "kills", "Most kills"), ("adr", "adr", "Highest ADR"), ("rating", "rating", "Best Rating"),
           ("hs_pct", "hs_pct", "Best HS%"), ("kast_pct", "kast_pct", "Best KAST"),
           ("utility_damage", "utility_damage", "Most utility damage"))
RECORD_MIN_KILLS = 10         # a HS% record needs this many kills behind it: 2 of 2 is not a record


@dataclass(frozen=True)
class Played:
    """One match with stats, as the subject played it."""
    checksum: str
    map: str
    played_at: str
    result: str                          # "win", "loss" or "tie"
    team_score: int
    opponent_score: int
    line: Mapping[str, Any]              # `review.line`
    mistakes: Sequence[Mapping[str, Any]] = ()
    flashes_kept: bool = True            # False: the team-flash Mistakes could not be looked for in this match


def _mean(values: Sequence[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return round(sum(known) / len(known), 2) if known else None


def _ratio(part: float, whole: float, scale: float = 1.0) -> float | None:
    return round(scale * part / whole, 2) if whole else None


def _each(match: Played) -> dict[str, float | None]:
    """The match's own value of each of the form's numbers, for the trend lines."""
    line = match.line
    duels = line["opening_kills"] + line["opening_deaths"]
    return {
        "win_pct": {"win": 100.0, "tie": 50.0}.get(match.result, 0.0),
        "rating": line["rating"], "kd": line["kd"], "adr": line["adr"], "hs_pct": line["hs_pct"],
        "kast_pct": line["kast_pct"], "opening_win_pct": _ratio(line["opening_kills"], duels, 100.0),
        "mistakes": float(len(match.mistakes)),
    }


def _over(matches: Sequence[Played]) -> dict[str, float | None]:
    """Each of the form's numbers over `matches`. Ratios are of the totals, not the mean of each match's ratio, so a
    3-0 match does not count as much as a 30-10 one."""
    if not matches:
        return {key: None for key, _ in FORM}
    lines = [m.line for m in matches]
    wins = sum(m.result == "win" for m in matches)
    opening_kills = sum(line["opening_kills"] for line in lines)
    opening = opening_kills + sum(line["opening_deaths"] for line in lines)
    return {
        "win_pct": _ratio(wins, len(matches), 100.0),
        "rating": _mean([line["rating"] for line in lines]),
        "kd": _ratio(sum(line["kills"] or 0 for line in lines), sum(line["deaths"] or 0 for line in lines)),
        "adr": _mean([line["adr"] for line in lines]),
        "hs_pct": _mean([line["hs_pct"] for line in lines]),
        "kast_pct": _mean([line["kast_pct"] for line in lines]),
        "opening_win_pct": _ratio(opening_kills, opening, 100.0),
        "mistakes": _mean([float(len(m.mistakes)) for m in matches]),
    }


def form(matches: Sequence[Played], window: int) -> dict[str, dict[str, Any]]:
    """Each of the form's numbers over the last `window` matches: its value, its value over the `window` matches
    before those (None when there are none), the change, whether more of it is better, and its value in each of the
    last `window` matches, oldest first."""
    last, before = matches[:window], matches[window:2 * window]
    now, then = _over(last), _over(before)
    each = [_each(m) for m in reversed(last)]
    out = {}
    for key, higher_is_better in FORM:
        delta = round(now[key] - then[key], 2) if now[key] is not None and then[key] is not None else None
        out[key] = {"value": now[key], "previous": then[key], "delta": delta, "higher_is_better": higher_is_better,
                    "series": [point[key] for point in each]}
    return out


def by_map(matches: Sequence[Played]) -> list[dict[str, Any]]:
    """Each map's numbers over every match on it, the most played map first."""
    played: dict[str, list[Played]] = defaultdict(list)
    for match in matches:
        played[match.map].append(match)
    rows = []
    for name, group in played.items():
        results = Counter(m.result for m in group)
        won = sum(m.team_score for m in group)
        rounds = won + sum(m.opponent_score for m in group)
        over = _over(group)
        rows.append({"map": name, "matches": len(group), "wins": results["win"], "losses": results["loss"],
                     "ties": results["tie"], "win_pct": over["win_pct"], "rounds_won_pct": _ratio(won, rounds, 100.0),
                     "kd": over["kd"], "adr": over["adr"], "rating": over["rating"], "hs_pct": over["hs_pct"],
                     "mistakes": over["mistakes"]})
    rows.sort(key=lambda row: (-row["matches"], row["map"]))
    return rows


def mistake_types(matches: Sequence[Played]) -> list[dict[str, Any]]:
    """The Mistakes over `matches` by Type, the most common first: how many, and how many a match."""
    counts: Counter[str] = Counter()
    for match in matches:
        counts.update(m["type"] for m in match.mistakes)
    rows = [{"type": kind, **mistakes.KINDS[kind], "count": counts[kind],
             "per_match": _ratio(counts[kind], len(matches))}
            for kind in mistakes.KINDS if counts[kind]]
    rows.sort(key=lambda row: -row["count"])
    return rows


def records(matches: Sequence[Played]) -> list[dict[str, Any]]:
    """The subject's best match for each of RECORDS, the earliest when two tie."""
    found = []
    for key, field, label in RECORDS:
        able = [m for m in matches if m.line.get(field) is not None
                and (key != "hs_pct" or (m.line["kills"] or 0) >= RECORD_MIN_KILLS)]
        if not able:
            continue
        best = max(reversed(able), key=lambda m: m.line[field])
        found.append({"key": key, "label": label, "value": best.line[field], "checksum": best.checksum,
                      "map": best.map, "played_at": best.played_at})
    return found


def summary(matches: Sequence[Played], window: int = DEFAULT_WINDOW) -> dict[str, Any]:
    """What the Progress page shows. `matches` is every match with stats, newest first; `window` is how many of the
    last ones the form covers: one of WINDOWS, or 0 for all of them (anything else is DEFAULT_WINDOW)."""
    if window not in WINDOWS and window != 0:
        window = DEFAULT_WINDOW
    span = window or len(matches)
    last = matches[:span]
    return {
        "matches": len(matches),
        "window": len(last),
        "asked": window,
        "form": form(matches, max(span, 1)),
        "recent": [{"checksum": m.checksum, "map": m.map, "played_at": m.played_at, "result": m.result,
                    "team_score": m.team_score, "opponent_score": m.opponent_score, "kills": m.line["kills"],
                    "deaths": m.line["deaths"], "assists": m.line["assists"], "adr": m.line["adr"],
                    "rating": m.line["rating"], "mistakes": len(m.mistakes)} for m in last],
        "maps": by_map(matches),
        "mistakes": mistake_types(last),
        "without_flashes": sum(not m.flashes_kept for m in last),
        "records": records(matches),
    }
