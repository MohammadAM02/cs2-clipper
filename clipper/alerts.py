"""Match alerts (spec: .scratch/match-alerts/spec.md): which FACEIT matches have Highlights, when to
say so, and in what words."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone

from clipper.model import FaceitStats

LINK_LIFETIME = timedelta(days=30)   # FACEIT's Demo links expire about 30 days after the match (ADR-0002)
REMIND_BEFORE = timedelta(days=3)
SHOWN_IN_SUMMARY = 3
_WORDS = (("5k", "Ace"), ("4k", "4K"), ("3k", "3K"))


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def qualifies(stats: FaceitStats) -> bool:
    """At least one round with 3+ Frags: the clipping rule's Highlights. Clutches play no part."""
    return sum(stats.highlights.values()) > 0


def highlights_text(counts: Mapping[str, int]) -> str:
    parts = []
    for key, word in _WORDS:
        count = counts.get(key, 0)
        if count:
            parts.append(word if count == 1 else f"{count}× {word}")
    return " + ".join(parts)


def map_label(map_name: str) -> str:
    """de_inferno → Inferno."""
    return map_name.split("_", 1)[-1].replace("_", " ").title()


def expires_at(finished_at: datetime) -> datetime:
    return finished_at + LINK_LIFETIME


def reminder_due(finished_at: datetime, now: datetime) -> bool:
    return expires_at(finished_at) - REMIND_BEFORE <= now < expires_at(finished_at)


def _finished(row: Mapping) -> datetime:
    return datetime.fromisoformat(row["finished_at"])


def _line(row: Mapping) -> str:
    return f"{map_label(row['map'])} {highlights_text(json.loads(row['highlights']))}"


def _listed(rows: Sequence[Mapping]) -> str:
    body = " · ".join(_line(row) for row in rows[:SHOWN_IN_SUMMARY])
    if len(rows) > SHOWN_IN_SUMMARY:
        body += f" · +{len(rows) - SHOWN_IN_SUMMARY} more"
    return body


def summary_text(rows: Sequence[Mapping]) -> tuple[str, str]:
    """The Match Alert for `rows`, newest first."""
    title = "1 new match has Highlights" if len(rows) == 1 else f"{len(rows)} new matches have Highlights"
    return title, _listed(rows)


def reminder_text(rows: Sequence[Mapping], now: datetime) -> tuple[str, str]:
    if len(rows) == 1:
        row = rows[0]
        days = max(1, math.ceil((expires_at(_finished(row)) - now) / timedelta(days=1)))
        title = f"{map_label(row['map'])} demo expires in {days} day{'' if days == 1 else 's'}"
        return title, f"{highlights_text(json.loads(row['highlights']))} · you haven't grabbed or skipped it"
    return f"{len(rows)} demos expire in 3 days or less", _listed(rows)


class StoppedPlaying:
    """Says once per play session that the user's CS2 has stayed closed for `minutes`."""

    def __init__(self, minutes: float):
        self._after = timedelta(minutes=minutes)
        self._played = False
        self._closed_at: datetime | None = None

    def update(self, cs2_running: bool, now: datetime) -> bool:
        if cs2_running:
            self._played, self._closed_at = True, None
            return False
        if not self._played:
            return False
        if self._closed_at is None:
            self._closed_at = now
        if now - self._closed_at < self._after:
            return False
        self._played, self._closed_at = False, None
        return True
