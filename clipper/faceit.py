"""The FACEIT Data API, read-only (spec: match alerts). ``api_key`` is a getter, called once per
request and never cached, so the decrypted key (spec: Settings and data, The FACEIT key) is held no
longer than one call. The key travels only in the Authorization header; it never appears in a log
line, an error message or the index."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from clipper.model import FaceitStats

DATA_API = "https://open.faceit.com/data/v4"
PAGE_SIZE = 100          # the history endpoint's largest page


class FaceitError(Exception):
    """FACEIT could not be asked, or said no. `status` is the HTTP status when there was one."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class AuthError(FaceitError):
    """FACEIT rejected the key (HTTP 401 or 403)."""


class PlayerNotFound(FaceitError):
    """FACEIT has no CS2 player by that nickname."""


@dataclass(frozen=True)
class Player:
    player_id: str
    nickname: str
    steamid: str


@dataclass(frozen=True)
class MatchDetails:
    demo_listed: bool
    matchroom_url: str


def _player_of(data: dict, missing: str) -> Player:
    cs2 = (data.get("games") or {}).get("cs2")
    if not cs2:
        raise PlayerNotFound(missing)
    return Player(data["player_id"], data["nickname"], str(cs2["game_player_id"]))


def _invalid_token(exc: urllib.error.HTTPError) -> bool:
    """Whether a 400 is really a bad key: FACEIT answers an unrecognised token with
    ``400 {"error":"invalid_token"}``, not the 401 you would expect."""
    if exc.code != 400:
        return False
    try:
        body = json.loads(exc.read())
    except (OSError, ValueError, AttributeError):   # a body we cannot read is not a verdict
        return False
    return isinstance(body, dict) and str(body.get("error", "")).lower() == "invalid_token"


def _http_error(exc: urllib.error.HTTPError) -> FaceitError:
    if exc.code in (401, 403) or _invalid_token(exc):
        return AuthError("the API key was not recognised by FACEIT", exc.code)
    return FaceitError(f"HTTP {exc.code} from FACEIT", exc.code)


def http_get_json(url: str, api_key: str, timeout: float = 20.0) -> dict:
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}", "Accept": "application/json", "User-Agent": "cs2-clipper"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raise _http_error(exc) from None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise FaceitError(f"FACEIT could not be reached: {type(exc).__name__}: {exc}") from None


def _int(value: object) -> int:
    try:
        return int(float(str(value)))
    except ValueError:
        return 0


def _float(value: object) -> float:
    try:
        return float(str(value))
    except ValueError:
        return 0.0


class FaceitClient:
    def __init__(self, api_key: Callable[[], str], fetch: Callable[[str], dict] | None = None):
        self._fetch = fetch or (lambda url: http_get_json(url, api_key()))

    def player(self, nickname: str) -> Player:
        try:
            data = self._fetch(f"{DATA_API}/players?nickname={urllib.parse.quote(nickname)}")
        except FaceitError as exc:
            if exc.status == 404:
                raise PlayerNotFound(f"FACEIT has no player called {nickname}", 404) from None
            raise
        return _player_of(data, f"{nickname} has no CS2 profile on FACEIT")

    def player_by_id(self, player_id: str) -> Player:
        """The player behind a FACEIT player id -- what a sign-in says the user is."""
        try:
            data = self._fetch(f"{DATA_API}/players/{urllib.parse.quote(player_id)}")
        except FaceitError as exc:
            if exc.status == 404:
                raise PlayerNotFound(f"FACEIT has no player {player_id}", 404) from None
            raise
        return _player_of(data, f"FACEIT has no player {player_id}")

    def finished_since(self, player_id: str, since: datetime) -> list[tuple[str, datetime]]:
        """(match ID, finished at) of every CS2 match the player finished since `since`."""
        found: list[tuple[str, datetime]] = []
        offset = 0
        while True:
            data = self._fetch(f"{DATA_API}/players/{player_id}/history?game=cs2"
                               f"&from={int(since.timestamp())}&offset={offset}&limit={PAGE_SIZE}")
            items = data.get("items") or []
            # FACEIT spells it "finished" (lower case), so compare case-insensitively: a change of
            # case on their side must never silently empty every Match Alert again.
            found += [(item["match_id"], datetime.fromtimestamp(item["finished_at"], timezone.utc))
                      for item in items
                      if str(item.get("status", "")).lower() == "finished" and item.get("finished_at")]
            if len(items) < PAGE_SIZE:
                return found
            offset += PAGE_SIZE

    def stats(self, match_id: str, player_id: str) -> FaceitStats | None:
        """The player's line in the match, or None while FACEIT has not published the stats."""
        try:
            data = self._fetch(f"{DATA_API}/matches/{match_id}/stats")
        except FaceitError as exc:
            if exc.status == 404:
                return None
            raise
        games = data.get("rounds") or []
        if not games:
            return None
        game = games[0]                                   # queue matches are one map
        map_name = game["round_stats"].get("Map", "")
        teams = game.get("teams") or []
        ours = next((t for t in teams if any(p["player_id"] == player_id for p in t["players"])), None)
        if ours is None:
            return FaceitStats(map_name=map_name)         # not the player's match: nothing to clip
        theirs = next((t for t in teams if t is not ours), {"team_stats": {}})
        line = next(p for p in ours["players"] if p["player_id"] == player_id)["player_stats"]
        return FaceitStats(
            map_name=map_name,
            team_score=_int(ours["team_stats"].get("Final Score")),
            opponent_score=_int(theirs["team_stats"].get("Final Score")),
            won=ours["team_stats"].get("Team Win") == "1",
            rounds=_int(game["round_stats"].get("Rounds")),
            kills=_int(line.get("Kills")),
            deaths=_int(line.get("Deaths")),
            assists=_int(line.get("Assists")),
            adr=_float(line.get("ADR")),
            double_kills=_int(line.get("Double Kills")),
            triple_kills=_int(line.get("Triple Kills")),
            quadro_kills=_int(line.get("Quadro Kills")),
            penta_kills=_int(line.get("Penta Kills")),
        )

    def details(self, match_id: str) -> MatchDetails:
        data = self._fetch(f"{DATA_API}/matches/{match_id}")
        url = data.get("faceit_url") or f"https://www.faceit.com/{{lang}}/cs2/room/{match_id}"
        return MatchDetails(demo_listed=bool(data.get("demo_url")), matchroom_url=url.replace("{lang}", "en"))
