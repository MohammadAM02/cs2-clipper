"""Match alerts (spec: .scratch/match-alerts/spec.md): which FACEIT matches have Highlights, when to
say so, and in what words."""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Protocol

from clipper.faceit import AuthError, FaceitError, MatchDetails, Player, PlayerNotFound
from clipper.index import Index
from clipper.model import FaceitStats
from clipper.protect import ProtectError
from clipper.rating import rating

LINK_LIFETIME = timedelta(days=30)   # FACEIT's Demo links expire about 30 days after the match (ADR-0002)
REMIND_BEFORE = timedelta(days=3)
SHOWN_IN_SUMMARY = 3
_WORDS = (("5k", "Ace"), ("4k", "4K"), ("3k", "3K"))

log = logging.getLogger(__name__)

RECHECK_EVERY = timedelta(minutes=3)
WAIT_AT_MOST = timedelta(minutes=30)
RETRY_AFTER = timedelta(minutes=5)
LOOK_BACK_OVERLAP = timedelta(hours=1)
FRESH = timedelta(hours=2)   # FACEIT publishes stats and Demos within minutes; older waiting matches hold nothing
PENDING = ("ready", "announced")


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


class FaceitLike(Protocol):
    def player(self, nickname: str) -> Player: ...
    def finished_since(self, player_id: str, since: datetime) -> list[tuple[str, datetime]]: ...
    def stats(self, match_id: str, player_id: str) -> FaceitStats | None: ...
    def details(self, match_id: str) -> MatchDetails: ...


class MatchAlerts:
    """The worker's match-alerts step, run every tick (spec: Flow)."""

    def __init__(self, index: Index, faceit: FaceitLike, notify: Callable[..., None],
                 cs2_running: Callable[[], bool], *, nickname: str, subject_steamid: str, page_url: str,
                 stopped_playing_minutes: float, clock: Callable[[], datetime] = utc_now):
        self._index = index
        self._faceit = faceit
        self._notify = notify
        self._cs2_running = cs2_running
        self._nickname = nickname
        self._subject = subject_steamid
        self._page_url = page_url
        self._clock = clock
        self._stopped = StoppedPlaying(stopped_playing_minutes)
        self._player_id: str | None = None
        self._off = False
        self._first = True                             # the start-up check looks back the full 30 days
        self._check_at: datetime | None = clock()      # the start-up check
        self._recheck_at: datetime | None = None
        self._summary_by: datetime | None = None
        self._session: set[str] = set()                # waiting matches that hold this Match Alert back
        index.set_flag("alerts_status", "on")

    def tick(self) -> None:
        now = self._clock()
        self._index.link_grabbed_matches(now)
        self._index.expire_faceit_matches(now - LINK_LIFETIME)
        playing = self._cs2_running()
        if self._stopped.update(playing, now):
            self._check_at = now
        if self._off or playing:
            return
        try:
            if self._check_at is not None and now >= self._check_at:
                self._check(now)
            elif self._recheck_at is not None and now >= self._recheck_at:
                self._recheck(now)
        except ProtectError:
            self._turn_off(
                "the saved FACEIT key can't be read on this Windows account; enter it again in Settings")
            return
        except AuthError as exc:
            self._turn_off(f"FACEIT rejected the key ({exc})")
            return
        except PlayerNotFound as exc:
            self._turn_off(f"{exc}; check the FACEIT nickname in Settings")
            return
        except FaceitError as exc:
            log.warning("could not ask FACEIT (%s); trying again in 5 minutes", exc)
            self._check_at = now + RETRY_AFTER
            return
        if self._off:
            return
        self._send_summary(now)
        self._send_reminders(now)

    # --- asking FACEIT --------------------------------------------------------------------------

    def _identify(self) -> str | None:
        """The FACEIT player ID, once per run; None (and alerts off) if it is not the subject's account."""
        if self._player_id is None:
            player = self._faceit.player(self._nickname)
            if player.steamid != self._subject:
                self._turn_off(f"FACEIT nickname {self._nickname} plays as SteamID {player.steamid},"
                               f" not your SteamID {self._subject}")
                return None
            self._player_id = player.player_id
        return self._player_id

    def _check(self, now: datetime) -> None:
        player_id = self._identify()
        if player_id is None:
            return
        last = self._index.get_flag("faceit_checked_at")
        # ponytail: the first check of a run looks back the full 30 days rather than from the last
        # check. A gap longer than LOOK_BACK_OVERLAP (the app was closed, or its key was broken for a
        # while) would otherwise drop matches for good -- and re-scanning is safe, because a match
        # already in the index is never announced twice.
        since = (now - LINK_LIFETIME if self._first or not last
                 else datetime.fromisoformat(last) - LOOK_BACK_OVERLAP)
        seen = set()
        for match_id, finished_at in self._faceit.finished_since(player_id, since):
            if self._index.faceit_match(match_id) is None:
                self._evaluate(match_id, finished_at, player_id)
                seen.add(match_id)
        for row in self._index.faceit_matches_in(("waiting",)):
            if row["match_id"] not in seen:
                self._evaluate(row["match_id"], _finished(row), player_id)
        self._first = False
        self._index.set_flag("faceit_checked_at", now.isoformat(timespec="seconds"))
        self._check_at = None
        self._session = {row["match_id"] for row in self._index.faceit_matches_in(("waiting",))
                         if now - _finished(row) <= FRESH}
        self._summary_by = now + WAIT_AT_MOST if self._session else now
        self._recheck_at = now + RECHECK_EVERY if self._session else None

    def _recheck(self, now: datetime) -> None:
        player_id = self._identify()
        if player_id is None:
            return
        for row in self._index.faceit_matches_in(("waiting",)):
            if row["match_id"] in self._session:
                self._evaluate(row["match_id"], _finished(row), player_id)
        self._session &= {row["match_id"] for row in self._index.faceit_matches_in(("waiting",))}
        self._recheck_at = now + RECHECK_EVERY if self._session else None

    def _evaluate(self, match_id: str, finished_at: datetime, player_id: str) -> None:
        stats = self._faceit.stats(match_id, player_id)
        if stats is None:
            self._index.save_faceit_match(match_id, finished_at, "waiting")
        elif not qualifies(stats):
            self._index.save_faceit_match(match_id, finished_at, "no_highlights", stats)
        else:
            details = self._faceit.details(match_id)
            state = "ready" if details.demo_listed else "waiting"
            self._index.save_faceit_match(match_id, finished_at, state, stats, rating(stats),
                                          details.matchroom_url)

    # --- telling the user -----------------------------------------------------------------------

    def _send_summary(self, now: datetime) -> None:
        if self._summary_by is None or (self._session and now < self._summary_by):
            return
        self._summary_by, self._recheck_at, self._session = None, None, set()
        self._index.link_grabbed_matches(now)
        ready = self._index.faceit_matches_in(("ready",))
        if not ready:
            return
        title, body = summary_text(ready)
        self._notify(title, body, actions=(("Show matches", self._page_url), ("Not now", None)))
        self._index.announce([row["match_id"] for row in ready], now)
        # A match announced inside its last 3 days has had its one reminder.
        self._index.remind([row["match_id"] for row in ready if reminder_due(_finished(row), now)], now)

    def _send_reminders(self, now: datetime) -> None:
        due = [row for row in self._index.faceit_matches_in(PENDING)
               if row["reminded_at"] is None and reminder_due(_finished(row), now)]
        if not due:
            return
        title, body = reminder_text(due, now)
        self._notify(title, body, actions=(("Show matches", self._page_url), ("Dismiss", None)))
        self._index.remind([row["match_id"] for row in due], now)

    def _turn_off(self, reason: str) -> None:
        log.warning("match alerts are off: %s", reason)
        self._off = True
        self._index.set_flag("alerts_status", f"off: {reason}")
