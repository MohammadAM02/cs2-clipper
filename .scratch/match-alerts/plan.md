# Match Alerts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After the user stops playing, one Windows notification lists the new FACEIT matches that have Highlights, and opens a Demos to grab page where each match can be opened in its matchroom or skipped.

**Architecture:** A `MatchAlerts` step runs on every worker tick. It notices when the user's CS2 has been closed for 5 minutes, asks FACEIT's Data API (read-only, standard-library `urllib`) which new matches have a 3K, 4K or Ace, records them in a new `faceit_matches` table, and sends the Match Alert and, later, at most one Reminder per match. A small standard-library `http.server` on a worker thread serves the page and its Skip / Undo; each request opens its own index connection. faceitperf's Rating 2.0 formula is ported for the page's stat line.

**Tech Stack:** Python 3.13 (uv), standard library (`urllib.request`, `http.server`, `json`, `sqlite3`), psutil (already a dependency), pytest; plain HTML/CSS/JS for the page, no build step; PowerShell toast notifications (existing `notify.py`).

**Spec:** `.scratch/match-alerts/spec.md` — read it first; this plan argues from it. Background: `CONTEXT.md` (glossary), `docs/adr/0002-faceit-only-ingestion.md`, `.scratch/orchestrator/spec.md` (the worker, the Gate, the index).

## Global Constraints

- Python ≥ 3.13 via uv. **No new dependencies**: FACEIT calls use `urllib.request`, the page uses `http.server`. Runtime dependencies stay `psycopg[binary]`, `psutil`, `zstandard`; `pytest` stays the only dev dependency.
- Windows only.
- `clipper/index.py` is the only code that writes our SQLite. The page server opens its own `Index` per request: an SQLite connection belongs to the thread that made it.
- The FACEIT key comes only from `.env` (`FACEIT_API_KEY`, with `FACEIT_NICKNAME`), travels only as `Authorization: Bearer <key>`, and never appears in a log line, an error message, the index or output.
- A match **qualifies** when FACEIT's stats give the subject at least one 3K, 4K or 5K (`Triple Kills` + `Quadro Kills` + `Penta Kills` ≥ 1) **and** FACEIT lists its Demo (`demo_url` not empty). Clutches play no part.
- The Match Alert goes out once, `stopped_playing_minutes` (default `5.0`) after the user's CS2 closes. A render's hooked CS2 (`cs2.exe` with `-insecure`) never counts as playing. FACEIT is checked then and once at start-up — never while CS2 runs, never on a timer while idle.
- A match whose stats or Demo are not ready is checked again every 3 minutes, for at most 30 minutes; then the Match Alert goes out without it.
- A Demo link is taken to expire 30 days after the match finished. One Reminder when 3 days or less remain; never while CS2 runs; Reminders due together share one notification.
- Match Alert: title `1 new match has Highlights` / `N new matches have Highlights`; body lists up to 3 matches newest first (`Inferno 4K + 2× 3K`), then `· +N more`; buttons **Show matches** (opens `http://127.0.0.1:<port>/demos`) and **Not now**. Reminder buttons: **Show matches** and **Dismiss**.
- The page listens on `0.0.0.0`, port `page_port` (default `8765`) or the next free one of the following nine, with no password. It answers only `GET /demos`, `GET /demos.json`, `POST /demos/<match id>/skip` and `POST /demos/<match id>/undo`, and serves no files. **Tests bind `127.0.0.1` only** — binding `0.0.0.0` makes Windows Firewall ask.
- Page: a table, newest first, with the stat line (Rating 2.0 · K–D · ADR). **Open** only for requests from the PC's own addresses; other devices get Skip / Undo and the note "Grab these at your PC".
- Rating 2.0 = `0.6844811040150518 + 0.65597945·KPR + 0.31304591·APR − 0.75999214·DPR + 0.00370714·ADR + 0.72169367·MKPR`, never below 0. faceitperf's MIT notice travels with `clipper/rating.py`.
- Use the glossary's words: Demo, Highlight, Frag, Reel, Gate, and this feature's Match Alert, Reminder, Grab, Skip.
- Run tests with `uv run pytest -q`. Baseline before Task 1: 91 passed with CS:DM's Postgres up (its 5 integration tests skip otherwise).
- **Never run `clipper run` or `clipper install`, launch CS2, or show a notification** — that is Task 10, with the user at the PC. An executing agent stops there and hands over.
- Work on branch `match-alerts-spec`. Commit at the end of every task. Do not push.

## Where this plan refines the spec

Flagged so the reviewer can object; none changes what the user sees.

1. **Grabbed matches** are found by one query each tick (a Demo in `demos` whose file name starts with the match ID), instead of a hook in `intake.py`. Same behaviour, and it also covers a crash between intake and the hook.
2. **The FACEIT player ID** is looked up once per run instead of kept in `app_state`, so a changed nickname or account is caught at the next start. `app_state` keeps `faceit_checked_at`, `alerts_status` and `page_port`.
3. **Only waiting matches that finished in the last 2 hours hold a Match Alert back.** FACEIT publishes stats and Demos within minutes; an older match that is still waiting would otherwise delay every future alert by 30 minutes.
4. **A match that is already in its last 3 days when first announced counts as reminded**, so the first run does not send a Reminder right after the Match Alert about the same match.

## File map

```
clipper/
  config.py       + match_alerts, stopped_playing_minutes, page_port; load_env()   Task 1
  model.py        + FaceitStats                                                       Task 2
  rating.py       Rating 2.0, ported from faceitperf (MIT)                            Task 2
  faceit.py       FACEIT Data API client, read-only                                   Task 3
  index.py        + faceit_matches table and its methods                              Task 4
  alerts.py       rules and words (Task 5); the MatchAlerts step (Task 6)             Tasks 5–6
  notify.py       + toast buttons                                                     Task 7
  page.py         the Demos to grab server                                            Task 8
  page.html       the page itself                                                     Task 8
  procs.py        + SystemProbe.user_cs2_running()                                    Task 9
  worker.py       + Services.alerts, run every tick                                   Task 9
  cli.py          + start_match_alerts(); status line                                 Task 9
clipper.example.toml  + the three settings                                            Task 1
tests/
  test_config.py test_rating.py test_faceit.py test_index.py test_alerts.py
  test_match_alerts.py test_notify.py test_page.py test_procs.py test_worker.py test_cli.py
```

---

### Task 1: Settings and the `.env` reader

**Files:**
- Modify: `clipper/config.py` (the `Config` dataclass; add `load_env` after `load_config`)
- Modify: `clipper.example.toml`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Config.match_alerts: bool = True`, `Config.stopped_playing_minutes: float = 5.0`, `Config.page_port: int = 8765`; `load_env(path: Path) -> dict[str, str]`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_config.py`, change the import line to:

```python
from clipper.config import Config, load_config, load_env
```

and append:

```python
def test_match_alert_settings_have_defaults(tmp_path):
    cfg = load_config(tmp_path / "missing.toml")
    assert (cfg.match_alerts, cfg.stopped_playing_minutes, cfg.page_port) == (True, 5.0, 8765)


def test_load_env_reads_keys_and_skips_comments(tmp_path):
    env = tmp_path / ".env"
    env.write_text('# a comment\n\nFACEIT_API_KEY="abc-123"\nFACEIT_NICKNAME=someone\nBROKEN LINE\n',
                   encoding="utf-8")
    assert load_env(env) == {"FACEIT_API_KEY": "abc-123", "FACEIT_NICKNAME": "someone"}


def test_load_env_of_a_missing_file_is_empty(tmp_path):
    assert load_env(tmp_path / ".env") == {}
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_config.py -q`
Expected: FAIL — `ImportError: cannot import name 'load_env'`.

- [ ] **Step 3: Implement**

In `clipper/config.py`, add three fields at the end of `Config`, after `sequence_event: str = "kills"`:

```python
    match_alerts: bool = True
    stopped_playing_minutes: float = 5.0
    page_port: int = 8765
```

and add after `load_config`:

```python
def load_env(path: Path) -> dict[str, str]:
    """KEY=value lines of a .env file. Comments, blank lines and lines without '=' are skipped;
    quotes around a value are dropped. A missing file gives no values."""
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values
```

Append to `clipper.example.toml`:

```toml
# match_alerts = true            # after you play, say which FACEIT matches have Highlights (needs .env)
# stopped_playing_minutes = 5.0  # how long CS2 stays closed before that notification
# page_port = 8765               # the Demos to grab page: http://<this PC>:8765/demos
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_config.py -q`
Expected: PASS (9 passed).

- [ ] **Step 5: Commit**

```bash
git add clipper/config.py clipper.example.toml tests/test_config.py
git commit -m "feat: match alert settings and the .env reader"
```

---

### Task 2: FaceitStats and the Rating 2.0 port

**Files:**
- Modify: `clipper/model.py` (add `FaceitStats` at the end)
- Create: `clipper/rating.py`
- Test: `tests/test_rating.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `clipper.model.FaceitStats` — frozen dataclass, every field defaulted: `map_name: str = ""`, `team_score: int = 0`, `opponent_score: int = 0`, `won: bool = False`, `rounds: int = 0`, `kills: int = 0`, `deaths: int = 0`, `assists: int = 0`, `adr: float = 0.0`, `double_kills: int = 0`, `triple_kills: int = 0`, `quadro_kills: int = 0`, `penta_kills: int = 0`; properties `highlights -> dict[str, int]` (`{"3k": …, "4k": …, "5k": …}`) and `multi_kill_rounds -> int`. `clipper.rating.rating(stats: FaceitStats) -> float`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_rating.py`:

```python
import pytest

from clipper.model import FaceitStats
from clipper.rating import rating

# 2 × 3K and 1 × 4K are the rounds with 3+ Frags; with 3 × 2K that makes 6 multi-kill rounds.
STRONG = FaceitStats(map_name="de_inferno", team_score=13, opponent_score=9, won=True, rounds=22,
                     kills=24, deaths=15, assists=5, adr=94.0, double_kills=3, triple_kills=2, quadro_kills=1)
AVERAGE = FaceitStats(map_name="de_nuke", team_score=11, opponent_score=13, rounds=24, kills=18, deaths=17,
                      assists=4, adr=79.0, double_kills=2, triple_kills=1)


def test_the_highlights_are_the_rounds_with_3_or_more_frags():
    assert STRONG.highlights == {"3k": 2, "4k": 1, "5k": 0}
    assert STRONG.multi_kill_rounds == 6


def test_rating_matches_faceitperf():
    # The expected values are faceitperf's estimateRating (apps/web/src/features/stats.ts) on the same inputs.
    assert rating(STRONG) == pytest.approx(1.4983620944695974)
    assert rating(AVERAGE) == pytest.approx(1.0733880127650517)


def test_rating_never_goes_below_zero_and_needs_rounds():
    assert rating(FaceitStats(rounds=13, deaths=30)) == 0.0
    assert rating(FaceitStats()) == 0.0
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_rating.py -q`
Expected: FAIL — `ImportError: cannot import name 'FaceitStats'`.

- [ ] **Step 3: Implement**

Append to `clipper/model.py`:

```python
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
```

Create `clipper/rating.py`:

```python
"""HLTV Rating 2.0, estimated from what FACEIT reports for one map (spec: Rating 2.0).

Ported from faceitperf's estimateRating (https://github.com/iffypixy/faceitperf,
apps/web/src/features/stats.ts). One deliberate difference: with no rounds there is nothing to rate,
so the result is 0, where faceitperf's divide() would pass the raw count through.

MIT License

Copyright (c) 2024 Ansat Euler

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

from clipper.model import FaceitStats

INTERCEPT = 0.6844811040150518
PER_KILL = 0.65597945          # × kills per round
PER_ASSIST = 0.31304591        # × assists per round
PER_DEATH = -0.75999214        # × deaths per round
PER_ADR = 0.00370714           # × average damage per round
PER_MULTI_KILL = 0.72169367    # × rounds with 2+ Frags per round


def rating(stats: FaceitStats) -> float:
    if stats.rounds <= 0:
        return 0.0
    value = (INTERCEPT
             + PER_KILL * stats.kills / stats.rounds
             + PER_ASSIST * stats.assists / stats.rounds
             + PER_DEATH * stats.deaths / stats.rounds
             + PER_ADR * stats.adr
             + PER_MULTI_KILL * stats.multi_kill_rounds / stats.rounds)
    return max(value, 0.0)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_rating.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add clipper/model.py clipper/rating.py tests/test_rating.py
git commit -m "feat: FaceitStats and faceitperf's Rating 2.0, ported with its MIT notice"
```

---

### Task 3: The FACEIT client

**Files:**
- Create: `clipper/faceit.py`
- Test: `tests/test_faceit.py`

**Interfaces:**
- Consumes: `FaceitStats` (Task 2).
- Produces: `DATA_API = "https://open.faceit.com/data/v4"`; `FaceitError(message: str, status: int | None = None)` with `.status`; `AuthError(FaceitError)`; `Player(player_id: str, nickname: str, steamid: str)`; `MatchDetails(demo_listed: bool, matchroom_url: str)`; `http_get_json(url: str, api_key: str, timeout: float = 20.0) -> dict`; `FaceitClient(api_key: str, fetch: Callable[[str], dict] | None = None)` with `player(nickname) -> Player`, `finished_since(player_id, since: datetime) -> list[tuple[str, datetime]]`, `stats(match_id, player_id) -> FaceitStats | None` (None while FACEIT has not published them), `details(match_id) -> MatchDetails`.

The response shapes below were checked against the live API on 2026-09-25 (keys and types only; the values here are made up).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_faceit.py`:

```python
import urllib.error
from datetime import datetime, timezone

import pytest

from clipper import faceit
from clipper.faceit import AuthError, FaceitClient, FaceitError, MatchDetails, Player, http_get_json

SUBJECT = "76561198000000001"   # made up
MATCH_ID = "1-00000000-0000-0000-0000-000000000001"


def line(kills, deaths, assists, adr, doubles=0, triples=0, quadros=0, pentas=0):
    return {"Kills": str(kills), "Deaths": str(deaths), "Assists": str(assists), "ADR": str(adr),
            "Double Kills": str(doubles), "Triple Kills": str(triples), "Quadro Kills": str(quadros),
            "Penta Kills": str(pentas)}


STATS = {"rounds": [{
    "round_stats": {"Map": "de_inferno", "Rounds": "22", "Score": "13 / 9", "Winner": "t1"},
    "teams": [
        {"team_id": "t1", "team_stats": {"Final Score": "13", "Team Win": "1"},
         "players": [{"player_id": "p-1", "player_stats": line(24, 15, 5, 94.0, 3, 2, 1)},
                     {"player_id": "p-2", "player_stats": line(10, 16, 3, 61.2)}]},
        {"team_id": "t2", "team_stats": {"Final Score": "9", "Team Win": "0"},
         "players": [{"player_id": "p-9", "player_stats": line(20, 18, 2, 80.5, 1, 1)}]},
    ],
}]}


class FakeFetch:
    """Answers by URL path and records every URL asked for."""

    def __init__(self, answers):
        self.answers, self.urls = answers, []

    def __call__(self, url):
        self.urls.append(url)
        answer = self.answers[url.removeprefix(faceit.DATA_API).split("?")[0]]
        if isinstance(answer, Exception):
            raise answer
        return answer(url) if callable(answer) else answer


def test_player_gives_the_ids_match_alerts_need():
    fetch = FakeFetch({"/players": {"player_id": "p-1", "nickname": "someone",
                                    "games": {"cs2": {"game_player_id": SUBJECT}}}})
    assert FaceitClient("key", fetch).player("some one") == Player("p-1", "someone", SUBJECT)
    assert fetch.urls == [f"{faceit.DATA_API}/players?nickname=some%20one"]


def test_a_player_without_cs2_is_an_error():
    fetch = FakeFetch({"/players": {"player_id": "p-1", "nickname": "someone", "games": {}}})
    with pytest.raises(FaceitError, match="no CS2 profile"):
        FaceitClient("key", fetch).player("someone")


def test_finished_since_pages_through_the_history():
    def page(url):
        if "offset=0&" in url:
            items = [{"match_id": "live", "status": "ONGOING", "finished_at": 0}]
            items += [{"match_id": f"m{i}", "status": "FINISHED", "finished_at": 1_790_000_000 + i}
                      for i in range(99)]
        else:
            items = [{"match_id": f"m{99 + i}", "status": "FINISHED", "finished_at": 1_790_000_099 + i}
                     for i in range(2)]
        return {"items": items}

    fetch = FakeFetch({"/players/p-1/history": page})
    since = datetime(2026, 9, 1, tzinfo=timezone.utc)
    found = FaceitClient("key", fetch).finished_since("p-1", since)
    assert len(found) == 101
    assert found[0] == ("m0", datetime.fromtimestamp(1_790_000_000, timezone.utc))
    assert f"from={int(since.timestamp())}" in fetch.urls[0]
    assert "offset=100&" in fetch.urls[1]


def test_stats_are_the_subjects_line():
    stats = FaceitClient("key", FakeFetch({f"/matches/{MATCH_ID}/stats": STATS})).stats(MATCH_ID, "p-1")
    assert (stats.map_name, stats.team_score, stats.opponent_score, stats.won, stats.rounds) == (
        "de_inferno", 13, 9, True, 22)
    assert (stats.kills, stats.deaths, stats.assists, stats.adr) == (24, 15, 5, 94.0)
    assert stats.highlights == {"3k": 2, "4k": 1, "5k": 0}


def test_stats_from_the_losing_side():
    stats = FaceitClient("key", FakeFetch({f"/matches/{MATCH_ID}/stats": STATS})).stats(MATCH_ID, "p-9")
    assert (stats.team_score, stats.opponent_score, stats.won) == (9, 13, False)


def test_stats_not_published_yet_are_none():
    fetch = FakeFetch({f"/matches/{MATCH_ID}/stats": FaceitError("HTTP 404 from FACEIT", 404)})
    assert FaceitClient("key", fetch).stats(MATCH_ID, "p-1") is None


def test_details_say_whether_the_demo_is_listed_and_where_the_matchroom_is():
    fetch = FakeFetch({f"/matches/{MATCH_ID}": {"demo_url": ["https://demos.example/x.dem.zst"],
                                                  "faceit_url": "https://www.faceit.com/{lang}/cs2/room/" + MATCH_ID}})
    assert FaceitClient("key", fetch).details(MATCH_ID) == MatchDetails(
        demo_listed=True, matchroom_url=f"https://www.faceit.com/en/cs2/room/{MATCH_ID}")
    fetch = FakeFetch({f"/matches/{MATCH_ID}": {"demo_url": []}})
    assert FaceitClient("key", fetch).details(MATCH_ID).demo_listed is False


@pytest.mark.parametrize(("error", "kind", "status"), [
    (urllib.error.HTTPError("u", 401, "Unauthorized", None, None), AuthError, 401),
    (urllib.error.HTTPError("u", 403, "Forbidden", None, None), AuthError, 403),
    (urllib.error.HTTPError("u", 404, "Not Found", None, None), FaceitError, 404),
    (urllib.error.HTTPError("u", 429, "Too Many Requests", None, None), FaceitError, 429),
    (urllib.error.URLError("no route"), FaceitError, None),
    (TimeoutError("timed out"), FaceitError, None),
])
def test_http_errors_become_faceit_errors_without_the_key(monkeypatch, error, kind, status):
    def urlopen(request, timeout):
        assert request.get_header("Authorization") == "Bearer secret-key"
        raise error

    monkeypatch.setattr(faceit.urllib.request, "urlopen", urlopen)
    with pytest.raises(kind) as caught:
        http_get_json(f"{faceit.DATA_API}/players?nickname=x", "secret-key")
    assert caught.value.status == status
    assert "secret-key" not in str(caught.value)


def test_a_json_answer_is_parsed(monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"ok": true}'

    monkeypatch.setattr(faceit.urllib.request, "urlopen", lambda request, timeout: Response())
    assert http_get_json(f"{faceit.DATA_API}/x", "key") == {"ok": True}
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_faceit.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'clipper.faceit'`.

- [ ] **Step 3: Implement**

Create `clipper/faceit.py`:

```python
"""The FACEIT Data API, read-only (spec: match alerts). The key travels only in the Authorization
header; it never appears in a log line, an error message or the index."""

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


@dataclass(frozen=True)
class Player:
    player_id: str
    nickname: str
    steamid: str


@dataclass(frozen=True)
class MatchDetails:
    demo_listed: bool
    matchroom_url: str


def http_get_json(url: str, api_key: str, timeout: float = 20.0) -> dict:
    request = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}", "Accept": "application/json", "User-Agent": "cs2-clipper"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        kind = AuthError if exc.code in (401, 403) else FaceitError
        raise kind(f"HTTP {exc.code} from FACEIT", exc.code) from None
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
    def __init__(self, api_key: str, fetch: Callable[[str], dict] | None = None):
        self._fetch = fetch or (lambda url: http_get_json(url, api_key))

    def player(self, nickname: str) -> Player:
        data = self._fetch(f"{DATA_API}/players?nickname={urllib.parse.quote(nickname)}")
        cs2 = (data.get("games") or {}).get("cs2")
        if not cs2:
            raise FaceitError(f"{nickname} has no CS2 profile on FACEIT")
        return Player(data["player_id"], data["nickname"], str(cs2["game_player_id"]))

    def finished_since(self, player_id: str, since: datetime) -> list[tuple[str, datetime]]:
        """(match ID, finished at) of every CS2 match the player finished since `since`."""
        found: list[tuple[str, datetime]] = []
        offset = 0
        while True:
            data = self._fetch(f"{DATA_API}/players/{player_id}/history?game=cs2"
                               f"&from={int(since.timestamp())}&offset={offset}&limit={PAGE_SIZE}")
            items = data.get("items") or []
            found += [(item["match_id"], datetime.fromtimestamp(item["finished_at"], timezone.utc))
                      for item in items if item.get("status") == "FINISHED" and item.get("finished_at")]
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
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_faceit.py -q`
Expected: PASS (14 passed).

- [ ] **Step 5: Commit**

```bash
git add clipper/faceit.py tests/test_faceit.py
git commit -m "feat: a read-only FACEIT Data API client that never shows the key"
```

---

### Task 4: FACEIT matches in the index

**Files:**
- Modify: `clipper/index.py` (the `SCHEMA` string, the model import, a `_iso` helper, a new method section before `# --- App state`)
- Test: `tests/test_index.py`

**Interfaces:**
- Consumes: `FaceitStats` (Task 2).
- Produces, all on `Index`:
  - `save_faceit_match(match_id: str, finished_at: datetime, state: str, stats: FaceitStats | None = None, rating: float = 0.0, matchroom_url: str = "") -> None` — upsert; leaves `announced_at`, `reminded_at`, `decided_at`, `demo_id` alone.
  - `faceit_match(match_id: str) -> sqlite3.Row | None`
  - `faceit_matches_in(states: Iterable[str]) -> list[sqlite3.Row]` — newest first.
  - `announce(match_ids: Iterable[str], at: datetime) -> None` — `ready` → `announced`.
  - `remind(match_ids: Iterable[str], at: datetime) -> None`
  - `skip_match(match_id: str, at: datetime) -> bool` — from `ready` / `announced` only.
  - `undo_skip(match_id: str) -> bool` — back to `announced`, or `ready` if never announced.
  - `link_grabbed_matches(at: datetime) -> int` — matches in `waiting` / `ready` / `announced` / `skipped` whose Demo is in `demos` become `grabbed`.
  - `expire_faceit_matches(finished_before: datetime) -> int` — `waiting` / `ready` / `announced` → `expired`.
  - `page_matches(decided_since: datetime) -> list[sqlite3.Row]` — `ready` + `announced`, plus `skipped` / `grabbed` decided since then, newest first, each with `demo_state` (the grabbed Demo's pipeline state, or None).
- Row columns: `match_id, finished_at, state, map, team_score, opponent_score, won, highlights (JSON), kills, deaths, assists, adr, rounds, rating, matchroom_url, announced_at, reminded_at, decided_at, demo_id`. Times are ISO 8601 UTC strings such as `2026-09-25T20:00:00+00:00`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_index.py`, change the imports at the top to:

```python
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from clipper.index import Index
from clipper.model import ClipFile, FaceitStats, Highlight, MatchInfo
```

and append:

```python
NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)
FIRST = "1-00000000-0000-0000-0000-000000000001"
SECOND = "1-00000000-0000-0000-0000-000000000002"
THREE_K = FaceitStats(map_name="de_inferno", team_score=13, opponent_score=9, won=True, rounds=22,
                      kills=24, deaths=15, assists=5, adr=94.0, double_kills=3, triple_kills=2, quadro_kills=1)
ROOM = "https://www.faceit.com/en/cs2/room/"


def test_a_faceit_match_is_saved_then_filled_in(index):
    index.save_faceit_match(FIRST, NOW - timedelta(hours=2), "waiting")
    assert index.faceit_match(FIRST)["state"] == "waiting"
    index.save_faceit_match(FIRST, NOW - timedelta(hours=2), "ready", THREE_K, 1.5, ROOM + FIRST)
    row = index.faceit_match(FIRST)
    assert (row["state"], row["map"], row["won"], row["rating"], row["matchroom_url"]) == (
        "ready", "de_inferno", 1, 1.5, ROOM + FIRST)
    assert json.loads(row["highlights"]) == {"3k": 2, "4k": 1, "5k": 0}
    assert row["finished_at"] == "2026-09-25T18:00:00+00:00"


def test_faceit_matches_come_newest_first(index):
    index.save_faceit_match(FIRST, NOW - timedelta(hours=3), "ready", THREE_K)
    index.save_faceit_match(SECOND, NOW - timedelta(hours=1), "ready", THREE_K)
    assert [r["match_id"] for r in index.faceit_matches_in(("ready",))] == [SECOND, FIRST]


def test_announce_and_remind_stamp_the_times(index):
    index.save_faceit_match(FIRST, NOW, "ready", THREE_K)
    index.announce([FIRST], NOW)
    index.remind([FIRST], NOW + timedelta(days=27))
    row = index.faceit_match(FIRST)
    assert row["state"] == "announced"
    assert row["announced_at"] == "2026-09-25T20:00:00+00:00"
    assert row["reminded_at"] == "2026-10-22T20:00:00+00:00"


def test_skip_and_undo(index):
    index.save_faceit_match(FIRST, NOW, "ready", THREE_K)
    index.announce([FIRST], NOW)
    assert index.skip_match(FIRST, NOW) is True
    assert index.faceit_match(FIRST)["state"] == "skipped"
    assert index.skip_match(FIRST, NOW) is False               # already skipped
    assert index.undo_skip(FIRST) is True
    assert (index.faceit_match(FIRST)["state"], index.faceit_match(FIRST)["decided_at"]) == ("announced", None)
    index.save_faceit_match(SECOND, NOW, "ready", THREE_K)
    index.skip_match(SECOND, NOW)
    index.undo_skip(SECOND)
    assert index.faceit_match(SECOND)["state"] == "ready"      # never announced
    assert index.undo_skip("1-00000000-0000-0000-0000-00000000dead") is False


def test_a_demo_in_the_index_grabs_its_match(index):
    index.save_faceit_match(FIRST, NOW, "announced", THREE_K)
    index.save_faceit_match(SECOND, NOW, "no_highlights")
    demo_id = add(index, name=f"{FIRST}-1-1.dem.zst")
    add(index, name=f"{SECOND}-1-1.dem.zst", sha="b" * 64)
    assert index.link_grabbed_matches(NOW) == 1
    row = index.faceit_match(FIRST)
    assert (row["state"], row["demo_id"], row["decided_at"]) == ("grabbed", demo_id, "2026-09-25T20:00:00+00:00")
    assert index.faceit_match(SECOND)["state"] == "no_highlights"
    assert index.link_grabbed_matches(NOW) == 0


def test_matches_whose_link_expired_drop_out(index):
    index.save_faceit_match(FIRST, NOW - timedelta(days=31), "announced", THREE_K)
    index.save_faceit_match(SECOND, NOW - timedelta(days=29), "announced", THREE_K)
    assert index.expire_faceit_matches(NOW - timedelta(days=30)) == 1
    assert index.faceit_match(FIRST)["state"] == "expired"
    assert index.faceit_match(SECOND)["state"] == "announced"


def test_the_page_lists_matches_to_grab_and_what_was_decided_in_the_last_day(index):
    third, fourth = FIRST[:-1] + "3", FIRST[:-1] + "4"
    index.save_faceit_match(FIRST, NOW - timedelta(hours=5), "announced", THREE_K)
    index.save_faceit_match(SECOND, NOW - timedelta(hours=4), "ready", THREE_K)
    index.save_faceit_match(third, NOW - timedelta(days=3), "ready", THREE_K)
    index.skip_match(third, NOW - timedelta(days=2))          # decided two days ago: gone
    index.save_faceit_match(fourth, NOW - timedelta(hours=1), "no_highlights")
    rows = index.page_matches(NOW - timedelta(hours=24))
    assert [r["match_id"] for r in rows] == [SECOND, FIRST]
    assert rows[0]["demo_state"] is None


def test_an_existing_index_gains_the_faceit_table(tmp_path):
    path = tmp_path / "clipper.sqlite"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE demos (id INTEGER PRIMARY KEY, file_name TEXT NOT NULL UNIQUE,"
                " sha256 TEXT NOT NULL UNIQUE, archive_path TEXT NOT NULL, dem_path TEXT, match_checksum TEXT,"
                " state TEXT NOT NULL, resume_state TEXT, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,"
                " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    old.execute("INSERT INTO demos (file_name, sha256, archive_path, state, created_at, updated_at)"
                " VALUES ('1-a.dem.zst', 'aaa', 'E:/a', 'done', 'x', 'x')")
    old.commit()
    old.close()
    index = Index(path)
    try:
        assert index.find_demo("1-a.dem.zst")["state"] == "done"
        index.save_faceit_match(FIRST, NOW, "ready", THREE_K)
        assert index.faceit_match(FIRST)["state"] == "ready"
    finally:
        index.close()
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_index.py -q`
Expected: FAIL — `AttributeError: 'Index' object has no attribute 'save_faceit_match'`.

- [ ] **Step 3: Implement**

In `clipper/index.py`:

Change the model import to:

```python
from clipper.model import ClipFile, FaceitStats, Highlight, MatchInfo
```

Add this table to `SCHEMA`, just before `CREATE TABLE IF NOT EXISTS app_state`:

```sql
CREATE TABLE IF NOT EXISTS faceit_matches (
    match_id       TEXT PRIMARY KEY,
    finished_at    TEXT NOT NULL,
    state          TEXT NOT NULL,
    map            TEXT NOT NULL DEFAULT '',
    team_score     INTEGER NOT NULL DEFAULT 0,
    opponent_score INTEGER NOT NULL DEFAULT 0,
    won            INTEGER NOT NULL DEFAULT 0,
    highlights     TEXT NOT NULL DEFAULT '{}',
    kills          INTEGER NOT NULL DEFAULT 0,
    deaths         INTEGER NOT NULL DEFAULT 0,
    assists        INTEGER NOT NULL DEFAULT 0,
    adr            REAL NOT NULL DEFAULT 0,
    rounds         INTEGER NOT NULL DEFAULT 0,
    rating         REAL NOT NULL DEFAULT 0,
    matchroom_url  TEXT NOT NULL DEFAULT '',
    announced_at   TEXT,
    reminded_at    TEXT,
    decided_at     TEXT,
    demo_id        INTEGER REFERENCES demos (id)
);
```

Add after `_now()`:

```python
def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")
```

Add this section to `Index`, just before `# --- App state`:

```python
    # --- FACEIT matches (match alerts) ---------------------------------------------------------------

    def save_faceit_match(self, match_id: str, finished_at: datetime, state: str,
                          stats: FaceitStats | None = None, rating: float = 0.0, matchroom_url: str = "") -> None:
        """What FACEIT says about a match. The announced, reminded and decided times and the Demo stay."""
        s = stats or FaceitStats()
        self._db.execute(
            "INSERT INTO faceit_matches (match_id, finished_at, state, map, team_score, opponent_score, won,"
            " highlights, kills, deaths, assists, adr, rounds, rating, matchroom_url)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (match_id) DO UPDATE SET state = excluded.state, map = excluded.map,"
            " team_score = excluded.team_score, opponent_score = excluded.opponent_score, won = excluded.won,"
            " highlights = excluded.highlights, kills = excluded.kills, deaths = excluded.deaths,"
            " assists = excluded.assists, adr = excluded.adr, rounds = excluded.rounds,"
            " rating = excluded.rating, matchroom_url = excluded.matchroom_url",
            (match_id, _iso(finished_at), state, s.map_name, s.team_score, s.opponent_score, int(s.won),
             json.dumps(s.highlights), s.kills, s.deaths, s.assists, s.adr, s.rounds, rating, matchroom_url),
        )

    def faceit_match(self, match_id: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM faceit_matches WHERE match_id = ?", (match_id,))

    def faceit_matches_in(self, states: Iterable[str]) -> list[sqlite3.Row]:
        """Newest first."""
        states = tuple(states)
        marks = ", ".join("?" * len(states))
        return self._all(
            f"SELECT * FROM faceit_matches WHERE state IN ({marks}) ORDER BY finished_at DESC", states)

    def announce(self, match_ids: Iterable[str], at: datetime) -> None:
        """These ready matches were in a Match Alert."""
        for match_id in match_ids:
            self._db.execute(
                "UPDATE faceit_matches SET state = 'announced', announced_at = ?"
                " WHERE match_id = ? AND state = 'ready'", (_iso(at), match_id))

    def remind(self, match_ids: Iterable[str], at: datetime) -> None:
        for match_id in match_ids:
            self._db.execute("UPDATE faceit_matches SET reminded_at = ? WHERE match_id = ?", (_iso(at), match_id))

    def skip_match(self, match_id: str, at: datetime) -> bool:
        cursor = self._db.execute(
            "UPDATE faceit_matches SET state = 'skipped', decided_at = ?"
            " WHERE match_id = ? AND state IN ('ready', 'announced')", (_iso(at), match_id))
        return cursor.rowcount == 1

    def undo_skip(self, match_id: str) -> bool:
        cursor = self._db.execute(
            "UPDATE faceit_matches SET decided_at = NULL,"
            " state = CASE WHEN announced_at IS NULL THEN 'ready' ELSE 'announced' END"
            " WHERE match_id = ? AND state = 'skipped'", (match_id,))
        return cursor.rowcount == 1

    def link_grabbed_matches(self, at: datetime) -> int:
        """A match whose Demo is in the index has been grabbed, announced or not. Returns how many changed."""
        demo = ("(SELECT d.id FROM demos d WHERE d.file_name LIKE faceit_matches.match_id || '-%'"
                " ORDER BY d.id LIMIT 1)")
        cursor = self._db.execute(
            f"UPDATE faceit_matches SET state = 'grabbed', decided_at = ?, demo_id = {demo}"
            f" WHERE state IN ('waiting', 'ready', 'announced', 'skipped') AND {demo} IS NOT NULL",
            (_iso(at),))
        return cursor.rowcount

    def expire_faceit_matches(self, finished_before: datetime) -> int:
        cursor = self._db.execute(
            "UPDATE faceit_matches SET state = 'expired'"
            " WHERE state IN ('waiting', 'ready', 'announced') AND finished_at < ?", (_iso(finished_before),))
        return cursor.rowcount

    def page_matches(self, decided_since: datetime) -> list[sqlite3.Row]:
        """What the Demos to grab page lists, newest first: every match still to grab, and those skipped or
        grabbed since `decided_since`. `demo_state` is the grabbed Demo's pipeline state."""
        return self._all(
            "SELECT f.*, d.state AS demo_state FROM faceit_matches f LEFT JOIN demos d ON d.id = f.demo_id"
            " WHERE f.state IN ('ready', 'announced')"
            " OR (f.state IN ('skipped', 'grabbed') AND f.decided_at >= ?)"
            " ORDER BY f.finished_at DESC",
            (_iso(decided_since),),
        )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_index.py -q`
Expected: PASS (all old tests plus the 8 new ones).

- [ ] **Step 5: Commit**

```bash
git add clipper/index.py tests/test_index.py
git commit -m "feat: the index keeps FACEIT matches for match alerts"
```

---

### Task 5: Alert rules and words

**Files:**
- Create: `clipper/alerts.py`
- Test: `tests/test_alerts.py`

**Interfaces:**
- Consumes: `FaceitStats` (Task 2).
- Produces, in `clipper.alerts`: `LINK_LIFETIME = timedelta(days=30)`, `REMIND_BEFORE = timedelta(days=3)`, `SHOWN_IN_SUMMARY = 3`; `utc_now() -> datetime`; `qualifies(stats: FaceitStats) -> bool`; `highlights_text(counts: Mapping[str, int]) -> str`; `map_label(map_name: str) -> str`; `expires_at(finished_at: datetime) -> datetime`; `reminder_due(finished_at: datetime, now: datetime) -> bool`; `summary_text(rows: Sequence[Mapping]) -> tuple[str, str]`; `reminder_text(rows: Sequence[Mapping], now: datetime) -> tuple[str, str]`; `StoppedPlaying(minutes: float)` with `update(cs2_running: bool, now: datetime) -> bool`; the private helper `_finished(row) -> datetime`, which Task 6 uses. Rows are index rows or dicts with `map`, `highlights` (JSON string) and `finished_at` (ISO string).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_alerts.py`:

```python
import json
from datetime import datetime, timedelta, timezone

from clipper.alerts import (StoppedPlaying, expires_at, highlights_text, map_label, qualifies, reminder_due,
                            reminder_text, summary_text)
from clipper.model import FaceitStats

NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)


def row(map_name, counts, finished_at=NOW):
    return {"map": map_name, "highlights": json.dumps({"3k": 0, "4k": 0, "5k": 0} | counts),
            "finished_at": finished_at.isoformat(timespec="seconds")}


def test_a_match_qualifies_with_a_round_of_3_or_more_frags():
    assert qualifies(FaceitStats(triple_kills=1))
    assert qualifies(FaceitStats(penta_kills=1))
    assert not qualifies(FaceitStats(double_kills=5))


def test_highlights_read_biggest_first():
    assert highlights_text({"3k": 2, "4k": 1, "5k": 0}) == "4K + 2× 3K"
    assert highlights_text({"3k": 1, "4k": 0, "5k": 1}) == "Ace + 3K"


def test_map_labels():
    assert [map_label(m) for m in ("de_inferno", "de_dust2", "cs_office")] == ["Inferno", "Dust2", "Office"]


def test_the_summary_lists_three_matches_then_counts_the_rest():
    rows = [row("de_inferno", {"4k": 1, "3k": 2}), row("de_ancient", {"3k": 2}), row("de_nuke", {"3k": 1}),
            row("de_mirage", {"3k": 1}), row("de_anubis", {"5k": 1})]
    assert summary_text(rows[:1]) == ("1 new match has Highlights", "Inferno 4K + 2× 3K")
    assert summary_text(rows[:3]) == ("3 new matches have Highlights",
                                      "Inferno 4K + 2× 3K · Ancient 2× 3K · Nuke 3K")
    assert summary_text(rows) == ("5 new matches have Highlights",
                                  "Inferno 4K + 2× 3K · Ancient 2× 3K · Nuke 3K · +2 more")


def test_the_reminder_names_the_match_or_counts_them():
    finished = NOW - timedelta(days=27)
    assert reminder_text([row("de_overpass", {"5k": 1, "3k": 1}, finished)], NOW) == (
        "Overpass demo expires in 3 days", "Ace + 3K · you haven't grabbed or skipped it")
    two = [row("de_overpass", {"5k": 1}, finished), row("de_nuke", {"3k": 1}, finished)]
    assert reminder_text(two, NOW) == ("2 demos expire in 3 days or less", "Overpass Ace · Nuke 3K")


def test_a_reminder_is_due_in_the_last_3_days_of_the_link():
    assert expires_at(NOW - timedelta(days=30)) == NOW
    assert not reminder_due(NOW - timedelta(days=26, hours=23), NOW)    # 3 days 1 hour left
    assert reminder_due(NOW - timedelta(days=27), NOW)
    assert reminder_due(NOW - timedelta(days=29, hours=23), NOW)
    assert not reminder_due(NOW - timedelta(days=30), NOW)              # expired


def test_stopped_playing_fires_once_5_minutes_after_cs2_closes():
    stopped = StoppedPlaying(5.0)
    assert not stopped.update(False, NOW)                               # CS2 never ran
    assert not stopped.update(True, NOW + timedelta(minutes=1))
    assert not stopped.update(False, NOW + timedelta(minutes=40))       # closed at 20:40
    assert not stopped.update(False, NOW + timedelta(minutes=44))
    assert stopped.update(False, NOW + timedelta(minutes=45))
    assert not stopped.update(False, NOW + timedelta(minutes=50))       # once per session


def test_opening_cs2_again_starts_the_wait_over():
    stopped = StoppedPlaying(5.0)
    stopped.update(True, NOW)
    stopped.update(False, NOW + timedelta(minutes=1))
    stopped.update(True, NOW + timedelta(minutes=4))                    # back in before 5 minutes
    assert not stopped.update(False, NOW + timedelta(minutes=7))
    assert stopped.update(False, NOW + timedelta(minutes=12))
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_alerts.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'clipper.alerts'`.

- [ ] **Step 3: Implement**

Create `clipper/alerts.py`:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_alerts.py -q`
Expected: PASS (8 passed).

- [ ] **Step 5: Commit**

```bash
git add clipper/alerts.py tests/test_alerts.py
git commit -m "feat: match alert rules — what qualifies, when, and the words"
```

---

### Task 6: The MatchAlerts step

**Files:**
- Modify: `clipper/alerts.py` (replace the import block; add constants, `FaceitLike`, `MatchAlerts`)
- Test: `tests/test_match_alerts.py`

**Interfaces:**
- Consumes: `FaceitError`, `AuthError`, `Player`, `MatchDetails` (Task 3); every `Index` FACEIT method (Task 4); the rules from Task 5; `rating` (Task 2).
- Produces: `class FaceitLike(Protocol)` — the four `FaceitClient` methods; `MatchAlerts(index: Index, faceit: FaceitLike, notify: Callable[..., None], cs2_running: Callable[[], bool], *, nickname: str, subject_steamid: str, page_url: str, stopped_playing_minutes: float, clock: Callable[[], datetime] = utc_now)` with `tick() -> None`. `notify` is called as `notify(title, body, actions=((label, url_or_None), ...))`. Flags written: `alerts_status` (`on`, or `off: <reason>`) and `faceit_checked_at`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_match_alerts.py`:

```python
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from clipper.alerts import MatchAlerts
from clipper.faceit import AuthError, FaceitError, MatchDetails, Player
from clipper.index import Index
from clipper.model import FaceitStats

SUBJECT = "76561198192858303"
PAGE = "http://127.0.0.1:8765/demos"
START = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)
SHOW = (("Show matches", PAGE), ("Not now", None))
THREE_K = FaceitStats(map_name="de_inferno", team_score=13, opponent_score=9, won=True, rounds=22, kills=24,
                      deaths=15, assists=5, adr=94.0, double_kills=3, triple_kills=2, quadro_kills=1)
ANCIENT_3K = FaceitStats(map_name="de_ancient", team_score=13, opponent_score=11, won=True, rounds=24,
                         kills=21, deaths=15, assists=6, adr=88.0, triple_kills=2)
NOTHING = FaceitStats(map_name="de_nuke", team_score=10, opponent_score=13, rounds=23, kills=12, deaths=18,
                      assists=3, adr=64.0, double_kills=2)


def mid(n: int) -> str:
    return f"1-00000000-0000-0000-0000-{n:012d}"


class FakeFaceit:
    def __init__(self):
        self.steamid = SUBJECT
        self.finished: dict[str, datetime] = {}
        self.stats_of: dict[str, FaceitStats | None] = {}
        self.listed: dict[str, bool] = {}
        self.error: Exception | None = None
        self.calls: list[str] = []

    def add(self, n, finished_at, stats, listed=True):
        self.finished[mid(n)], self.stats_of[mid(n)], self.listed[mid(n)] = finished_at, stats, listed

    def player(self, nickname):
        self.calls.append("player")
        if self.error:
            raise self.error
        return Player("p-1", nickname, self.steamid)

    def finished_since(self, player_id, since):
        self.calls.append("history")
        if self.error:
            raise self.error
        return [(m, t) for m, t in self.finished.items() if t >= since]

    def stats(self, match_id, player_id):
        self.calls.append(f"stats {match_id}")
        return self.stats_of.get(match_id)

    def details(self, match_id):
        self.calls.append(f"details {match_id}")
        return MatchDetails(self.listed.get(match_id, True), f"https://www.faceit.com/en/cs2/room/{match_id}")


@dataclass
class World:
    index: Index
    faceit: FakeFaceit
    notices: list = field(default_factory=list)
    playing: bool = False
    now: datetime = START
    alerts: MatchAlerts | None = None

    def tick(self, **later):
        self.now += timedelta(**later)
        self.alerts.tick()

    def state(self, n):
        return self.index.faceit_match(mid(n))["state"]

    def titles(self):
        return [title for title, _, _ in self.notices]


@pytest.fixture
def world(tmp_path):
    index = Index(tmp_path / "clipper.sqlite")
    w = World(index, FakeFaceit())
    w.alerts = MatchAlerts(
        index, w.faceit, lambda title, body, actions=(): w.notices.append((title, body, actions)),
        lambda: w.playing, nickname="someone", subject_steamid=SUBJECT, page_url=PAGE,
        stopped_playing_minutes=5.0, clock=lambda: w.now)
    yield w
    index.close()


def test_the_start_up_check_announces_the_matches_with_highlights(world):
    world.faceit.add(1, START - timedelta(hours=2), THREE_K)
    world.faceit.add(2, START - timedelta(hours=1), NOTHING)
    world.tick()
    assert world.notices == [("1 new match has Highlights", "Inferno 4K + 2× 3K", SHOW)]
    assert (world.state(1), world.state(2)) == ("announced", "no_highlights")
    assert f"details {mid(2)}" not in world.faceit.calls        # no need to ask about its Demo
    assert world.index.get_flag("alerts_status") == "on"


def test_nothing_is_asked_while_cs2_runs(world):
    world.playing = True
    world.faceit.add(1, START - timedelta(hours=1), THREE_K)
    world.tick()
    world.tick(minutes=30)
    assert world.faceit.calls == []
    assert world.notices == []


def test_the_match_alert_comes_5_minutes_after_cs2_closes(world):
    world.tick()                                                  # start-up check: nothing yet
    world.playing = True
    world.tick(minutes=1)
    world.faceit.add(1, START + timedelta(minutes=40), THREE_K)
    world.playing = False
    world.tick(minutes=45)                                        # CS2 closed at 20:46
    world.tick(minutes=4)
    assert world.notices == []
    world.tick(minutes=1)
    assert world.titles() == ["1 new match has Highlights"]


def test_a_match_whose_stats_are_not_out_yet_holds_the_alert(world):
    world.faceit.add(1, START - timedelta(minutes=10), None)
    world.faceit.add(2, START - timedelta(minutes=50), ANCIENT_3K)
    world.tick()
    assert world.notices == [] and world.state(1) == "waiting"
    world.faceit.stats_of[mid(1)] = THREE_K
    world.tick(minutes=3)
    assert world.notices == [("2 new matches have Highlights", "Inferno 4K + 2× 3K · Ancient 2× 3K", SHOW)]


def test_the_alert_waits_30_minutes_at_most(world):
    world.faceit.add(1, START - timedelta(minutes=10), None)     # FACEIT never publishes its stats
    world.faceit.add(2, START - timedelta(minutes=50), ANCIENT_3K)
    world.tick()
    world.tick(minutes=27)
    assert world.notices == []
    world.tick(minutes=3)
    assert world.notices == [("1 new match has Highlights", "Ancient 2× 3K", SHOW)]
    assert world.state(1) == "waiting"


def test_a_demo_that_is_not_listed_yet_waits_like_missing_stats(world):
    world.faceit.add(1, START - timedelta(minutes=10), THREE_K, listed=False)
    world.tick()
    assert world.state(1) == "waiting" and world.notices == []
    world.faceit.listed[mid(1)] = True
    world.tick(minutes=3)
    assert world.state(1) == "announced"


def test_one_reminder_in_the_last_3_days_and_never_while_cs2_runs(world):
    world.index.save_faceit_match(mid(1), START - timedelta(days=27, hours=1), "ready", THREE_K)
    world.index.announce([mid(1)], START - timedelta(days=27))
    world.index.save_faceit_match(mid(2), START - timedelta(days=27, hours=1), "ready", ANCIENT_3K)
    world.index.skip_match(mid(2), START - timedelta(days=27))
    world.playing = True
    world.tick()
    assert world.notices == []
    world.playing = False
    world.tick(minutes=1)
    assert world.notices == [("Inferno demo expires in 3 days", "4K + 2× 3K · you haven't grabbed or skipped it",
                              (("Show matches", PAGE), ("Dismiss", None)))]
    world.tick(minutes=1)
    assert len(world.notices) == 1


def test_a_match_announced_in_its_last_3_days_gets_no_second_reminder(world):
    world.faceit.add(1, START - timedelta(days=29), THREE_K)     # the first check looks back 30 days
    world.faceit.add(2, START - timedelta(days=31), ANCIENT_3K)  # its link has expired
    world.tick()
    world.tick(minutes=1)
    assert world.titles() == ["1 new match has Highlights"]
    assert world.index.faceit_match(mid(1))["reminded_at"] is not None
    assert world.index.faceit_match(mid(2)) is None


def test_a_downloaded_demo_grabs_its_match(world):
    world.faceit.add(1, START - timedelta(hours=1), THREE_K)
    world.tick()
    name = f"{mid(1)}-1-1.dem.zst"
    demo_id = world.index.add_demo(name, "a" * 64, Path("E:/cs2clips/demos") / name)
    world.tick(minutes=1)
    assert world.state(1) == "grabbed"
    assert world.index.faceit_match(mid(1))["demo_id"] == demo_id


def test_a_demo_already_in_the_index_is_never_announced(world):
    name = f"{mid(1)}-1-1.dem.zst"
    world.index.add_demo(name, "a" * 64, Path("E:/cs2clips/demos") / name)
    world.faceit.add(1, START - timedelta(days=3), THREE_K)
    world.tick()
    assert world.notices == []
    assert world.state(1) == "grabbed"


def test_the_wrong_faceit_account_turns_alerts_off(world):
    world.faceit.steamid = "76561198000000009"
    world.tick()
    world.tick(minutes=10)
    assert world.faceit.calls == ["player"]
    assert world.index.get_flag("alerts_status") == (
        "off: FACEIT_NICKNAME someone plays as SteamID 76561198000000009, not subject_steamid 76561198192858303")


def test_a_rejected_key_turns_alerts_off(world):
    world.faceit.error = AuthError("HTTP 401 from FACEIT", 401)
    world.tick()
    world.faceit.error = None
    world.tick(minutes=10)
    assert world.faceit.calls == ["player"]
    assert world.index.get_flag("alerts_status") == "off: FACEIT rejected the key (HTTP 401 from FACEIT)"


def test_faceit_trouble_is_tried_again_5_minutes_later(world):
    world.faceit.error = FaceitError("HTTP 503 from FACEIT", 503)
    world.faceit.add(1, START - timedelta(hours=1), THREE_K)
    world.tick()
    world.faceit.error = None
    world.tick(minutes=4)
    assert world.notices == [] and world.faceit.calls == ["player"]
    world.tick(minutes=1)
    assert world.titles() == ["1 new match has Highlights"]


def test_matches_whose_link_expired_drop_out(world):
    world.index.save_faceit_match(mid(1), START - timedelta(days=31), "ready", THREE_K)
    world.index.announce([mid(1)], START - timedelta(days=31))
    world.tick()
    assert world.state(1) == "expired"
    assert world.notices == []
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_match_alerts.py -q`
Expected: FAIL — `ImportError: cannot import name 'MatchAlerts'`.

- [ ] **Step 3: Implement**

In `clipper/alerts.py`, replace the import block (from `import json` down to `from clipper.model import FaceitStats`) with:

```python
import json
import logging
import math
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Protocol

from clipper.faceit import AuthError, FaceitError, MatchDetails, Player
from clipper.index import Index
from clipper.model import FaceitStats
from clipper.rating import rating
```

Below the imports, next to the other constants, add:

```python
log = logging.getLogger(__name__)

RECHECK_EVERY = timedelta(minutes=3)
WAIT_AT_MOST = timedelta(minutes=30)
RETRY_AFTER = timedelta(minutes=5)
LOOK_BACK_OVERLAP = timedelta(hours=1)
FRESH = timedelta(hours=2)   # FACEIT publishes stats and Demos within minutes; older waiting matches hold nothing
PENDING = ("ready", "announced")
```

Append to the end of `clipper/alerts.py`:

```python
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
        except AuthError as exc:
            self._turn_off(f"FACEIT rejected the key ({exc})")
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
                self._turn_off(f"FACEIT_NICKNAME {self._nickname} plays as SteamID {player.steamid},"
                               f" not subject_steamid {self._subject}")
                return None
            self._player_id = player.player_id
        return self._player_id

    def _check(self, now: datetime) -> None:
        player_id = self._identify()
        if player_id is None:
            return
        last = self._index.get_flag("faceit_checked_at")
        since = datetime.fromisoformat(last) - LOOK_BACK_OVERLAP if last else now - LINK_LIFETIME
        seen = set()
        for match_id, finished_at in self._faceit.finished_since(player_id, since):
            if self._index.faceit_match(match_id) is None:
                self._evaluate(match_id, finished_at, player_id)
                seen.add(match_id)
        for row in self._index.faceit_matches_in(("waiting",)):
            if row["match_id"] not in seen:
                self._evaluate(row["match_id"], _finished(row), player_id)
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
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_match_alerts.py tests/test_alerts.py -q`
Expected: PASS (14 + 8 passed).

- [ ] **Step 5: Commit**

```bash
git add clipper/alerts.py tests/test_match_alerts.py
git commit -m "feat: the match alerts step — check after playing, alert once, remind once"
```

---

### Task 7: Toast buttons

**Files:**
- Modify: `clipper/notify.py` (`toast_xml`, `notify`)
- Test: `tests/test_notify.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `Action = tuple[str, str | None]` (button label, URL to open; `None` means dismiss); `toast_xml(title: str, body: str, actions: Sequence[Action] = ()) -> str`; `notify(title: str, body: str, actions: Sequence[Action] = ()) -> None`. With a URL action, clicking the toast itself also opens the first URL. Without actions the XML is exactly what it was.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_notify.py`:

```python
def test_a_toast_can_open_a_page_or_be_dismissed():
    url = "http://127.0.0.1:8765/demos?a=1&b=2"
    xml = toast_xml("2 new matches have Highlights", "Inferno 4K", actions=(("Show matches", url), ("Not now", None)))
    assert xml.startswith('<toast activationType="protocol" launch="http://127.0.0.1:8765/demos?a=1&amp;b=2">')
    assert ('<action content="Show matches" activationType="protocol"'
            ' arguments="http://127.0.0.1:8765/demos?a=1&amp;b=2"/>') in xml
    assert '<action content="Not now" activationType="system" arguments="dismiss"/>' in xml
    assert xml.endswith("</actions></toast>")


def test_a_toast_without_actions_is_unchanged():
    assert toast_xml("Title", "Body") == (
        "<toast><visual><binding template='ToastGeneric'><text>Title</text><text>Body</text>"
        "</binding></visual></toast>")
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_notify.py -q`
Expected: FAIL — `TypeError: toast_xml() got an unexpected keyword argument 'actions'`.

- [ ] **Step 3: Implement**

In `clipper/notify.py`, add `from collections.abc import Sequence` to the imports, change the saxutils import to `from xml.sax.saxutils import escape, quoteattr`, and replace `toast_xml` and the first line of `notify` so they read:

```python
Action = tuple[str, str | None]   # (button label, URL to open); no URL means "dismiss"


def toast_xml(title: str, body: str, actions: Sequence[Action] = ()) -> str:
    buttons = "".join(
        f'<action content={quoteattr(label)} activationType="protocol" arguments={quoteattr(url)}/>'
        if url else f'<action content={quoteattr(label)} activationType="system" arguments="dismiss"/>'
        for label, url in actions
    )
    first_url = next((url for _, url in actions if url), None)
    launch = f' activationType="protocol" launch={quoteattr(first_url)}' if first_url else ""
    return (
        f"<toast{launch}><visual><binding template='ToastGeneric'>"
        f"<text>{escape(title)}</text><text>{escape(body)}</text>"
        "</binding></visual>" + (f"<actions>{buttons}</actions>" if buttons else "") + "</toast>"
    )


def notify(title: str, body: str, actions: Sequence[Action] = ()) -> None:
    env = {**os.environ, "CLIPPER_TOAST": toast_xml(title, body, actions)}
```

The rest of `notify` (the `try: subprocess.run(...)` block) stays as it is.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_notify.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add clipper/notify.py tests/test_notify.py
git commit -m "feat: notifications can carry buttons that open a page or dismiss"
```

---

### Task 8: The Demos to grab page

**Files:**
- Create: `clipper/page.py`
- Create: `clipper/page.html`
- Test: `tests/test_page.py`

**Interfaces:**
- Consumes: `Index.page_matches`, `Index.skip_match`, `Index.undo_skip` (Task 4); `LINK_LIFETIME`, `map_label`, `utc_now` (Task 5).
- Produces: `own_addresses() -> set[str]`; `page_rows(index: Index, now: datetime, waiting_for: tuple[str, ...]) -> list[dict]`; `PageServer(index_path: Path, port: int, gate_reasons: Callable[[], tuple[str, ...]], *, host: str = "0.0.0.0", own: Callable[[], set[str]] = own_addresses, clock: Callable[[], datetime] = utc_now)` with `.port -> int`, `.start() -> None`, `.stop() -> None`. `port=0` lets the OS pick a port (tests). `GET /demos.json` answers `{"this_pc": bool, "rows": [...]}`; each row is `{"id", "map", "finished_at", "expires_at", "won", "score", "highlights", "rating", "kd", "adr", "state", "reminded", "url", "waiting_for"}`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_page.py`:

```python
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from clipper.index import Index
from clipper.model import FaceitStats
from clipper.page import PageServer

NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)
FIRST = "1-00000000-0000-0000-0000-000000000001"
SECOND = "1-00000000-0000-0000-0000-000000000002"
ROOM = "https://www.faceit.com/en/cs2/room/"
THREE_K = FaceitStats(map_name="de_inferno", team_score=13, opponent_score=9, won=True, rounds=22, kills=24,
                      deaths=15, assists=5, adr=94.0, double_kills=3, triple_kills=2, quadro_kills=1)


@pytest.fixture
def index(tmp_path):
    idx = Index(tmp_path / "clipper.sqlite")
    idx.save_faceit_match(FIRST, NOW - timedelta(hours=5), "ready", THREE_K, 1.4983, ROOM + FIRST)
    idx.announce([FIRST], NOW - timedelta(hours=4))
    idx.save_faceit_match(SECOND, NOW - timedelta(hours=2), "ready", THREE_K, 1.2, ROOM + SECOND)
    yield idx
    idx.close()


def serve(tmp_path, **options):
    server = PageServer(tmp_path / "clipper.sqlite", 0, gate_reasons=lambda: ("FACEIT AC is running",),
                        host="127.0.0.1", clock=lambda: NOW, **options)
    server.start()
    return server


@pytest.fixture
def page(tmp_path, index):
    server = serve(tmp_path)
    yield server
    server.stop()


def get(server, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{server.port}{path}", timeout=5) as response:
        return response.status, response.headers["Content-Type"], response.read()


def post(server, path):
    request = urllib.request.Request(f"http://127.0.0.1:{server.port}{path}", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_the_page_is_served(page):
    status, kind, body = get(page, "/demos")
    assert (status, kind) == (200, "text/html; charset=utf-8")
    assert b"Demos to grab" in body


def test_the_list_comes_newest_first_with_the_stat_line(page):
    status, kind, body = get(page, "/demos.json")
    data = json.loads(body)
    assert (status, kind, data["this_pc"]) == (200, "application/json", True)
    assert [row["id"] for row in data["rows"]] == [SECOND, FIRST]
    assert data["rows"][1] == {
        "id": FIRST, "map": "Inferno", "finished_at": "2026-09-25T15:00:00+00:00",
        "expires_at": "2026-10-25T15:00:00+00:00", "won": True, "score": "13–9",
        "highlights": {"3k": 2, "4k": 1, "5k": 0}, "rating": 1.5, "kd": "24–15", "adr": 94.0,
        "state": "announced", "reminded": False, "url": ROOM + FIRST, "waiting_for": "",
    }


def test_skip_and_undo_reach_the_index(page, index):
    assert post(page, f"/demos/{FIRST}/skip") == 204
    assert index.faceit_match(FIRST)["state"] == "skipped"
    assert post(page, f"/demos/{FIRST}/skip") == 409                  # nothing left to skip
    assert post(page, f"/demos/{FIRST}/undo") == 204
    assert index.faceit_match(FIRST)["state"] == "announced"


def test_other_devices_get_no_open_button(tmp_path, index):
    server = serve(tmp_path, own=lambda: {"10.0.0.9"})
    try:
        assert json.loads(get(server, "/demos.json")[2])["this_pc"] is False
    finally:
        server.stop()


def test_a_grabbed_demo_says_what_rendering_waits_for(page, index):
    name = f"{FIRST}-1-1.dem.zst"
    index.add_demo(name, "a" * 64, Path("E:/cs2clips/demos") / name)
    index.link_grabbed_matches(NOW)
    rows = json.loads(get(page, "/demos.json")[2])["rows"]
    grabbed = next(row for row in rows if row["id"] == FIRST)
    assert (grabbed["state"], grabbed["waiting_for"]) == ("grabbed", "FACEIT AC is running")


def test_nothing_else_is_served(page):
    for path in ("/", "/demos/../clipper.sqlite", "/demos.json/x"):
        with pytest.raises(urllib.error.HTTPError) as caught:
            get(page, path)
        assert caught.value.code == 404
    assert post(page, "/demos/not-a-match/skip") == 404
    assert post(page, f"/demos/{FIRST}/delete") == 404
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_page.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'clipper.page'`.

- [ ] **Step 3: Implement the server**

Create `clipper/page.py`:

```python
"""The Demos to grab page (spec: The Demos to grab page): a small HTTP server on a worker thread.

It answers only the page, its list, and Skip / Undo, and serves no files. Every request opens its
own index connection, because an SQLite connection belongs to the thread that made it."""

from __future__ import annotations

import json
import logging
import re
import socket
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

from clipper.alerts import LINK_LIFETIME, map_label, utc_now
from clipper.index import Index

log = logging.getLogger(__name__)

PAGE_HTML = Path(__file__).with_name("page.html")
ACTION = re.compile(r"^/demos/(1-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/(skip|undo)$")
KEEP_DECIDED = timedelta(hours=24)
PORTS_TO_TRY = 10
IN_PIPELINE = ("spotted", "unpacked", "analyzed", "scored", "rendering", "joined")


def own_addresses() -> set[str]:
    """Every address of this PC: loopback, LAN and Tailscale alike."""
    found = {"127.0.0.1", "::1"}
    for entries in psutil.net_if_addrs().values():
        for entry in entries:
            if entry.family in (socket.AF_INET, socket.AF_INET6):
                found.add(entry.address.split("%")[0])
    return found


def page_rows(index: Index, now: datetime, waiting_for: tuple[str, ...]) -> list[dict]:
    """The page's list, newest first. `waiting_for` is the Gate's reasons, shown on grabbed Demos that
    have not been rendered yet."""
    rows = []
    for row in index.page_matches(now - KEEP_DECIDED):
        finished = datetime.fromisoformat(row["finished_at"])
        rendering_waits = row["state"] == "grabbed" and row["demo_state"] in IN_PIPELINE
        rows.append({
            "id": row["match_id"],
            "map": map_label(row["map"]),
            "finished_at": row["finished_at"],
            "expires_at": (finished + LINK_LIFETIME).isoformat(timespec="seconds"),
            "won": bool(row["won"]),
            "score": f"{row['team_score']}–{row['opponent_score']}",
            "highlights": json.loads(row["highlights"]),
            "rating": round(row["rating"], 2),
            "kd": f"{row['kills']}–{row['deaths']}",
            "adr": round(row["adr"], 1),
            "state": row["state"],
            "reminded": row["reminded_at"] is not None,
            "url": row["matchroom_url"],
            "waiting_for": "; ".join(waiting_for) if rendering_waits else "",
        })
    return rows


class PageServer:
    def __init__(self, index_path: Path, port: int, gate_reasons: Callable[[], tuple[str, ...]], *,
                 host: str = "0.0.0.0", own: Callable[[], set[str]] = own_addresses,
                 clock: Callable[[], datetime] = utc_now):
        self._index_path = index_path
        self._gate_reasons = gate_reasons
        self._own = own
        self._clock = clock
        self._server = self._bind(host, port)

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def start(self) -> None:
        threading.Thread(target=self._server.serve_forever, name="demos-page", daemon=True).start()
        log.info("the Demos to grab page is on port %s", self.port)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _bind(self, host: str, port: int) -> ThreadingHTTPServer:
        handler = self._handler()
        for candidate in range(port, port + PORTS_TO_TRY) if port else (0,):
            try:
                return ThreadingHTTPServer((host, candidate), handler)
            except OSError:
                log.info("port %s is taken; trying the next one", candidate)
        raise OSError(f"no free port in {port}–{port + PORTS_TO_TRY - 1}")

    def _list(self, client: str) -> dict:
        index = Index(self._index_path)
        try:
            rows = page_rows(index, self._clock(), self._gate_reasons())
        finally:
            index.close()
        return {"this_pc": client.split("%")[0].removeprefix("::ffff:") in self._own(), "rows": rows}

    def _decide(self, match_id: str, action: str) -> bool:
        index = Index(self._index_path)
        try:
            return index.skip_match(match_id, self._clock()) if action == "skip" else index.undo_skip(match_id)
        finally:
            index.close()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        page = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:   # pythonw has no stderr
                log.debug("page: " + format, *args)

            def do_GET(self) -> None:
                if self.path == "/demos":
                    self._send(200, "text/html; charset=utf-8", PAGE_HTML.read_bytes())
                elif self.path == "/demos.json":
                    body = json.dumps(page._list(self.client_address[0])).encode("utf-8")
                    self._send(200, "application/json", body)
                else:
                    self._send(404, "text/plain; charset=utf-8", b"not found")

            def do_POST(self) -> None:
                found = ACTION.match(self.path)
                if found is None:
                    self._send(404, "text/plain; charset=utf-8", b"not found")
                    return
                changed = page._decide(*found.groups())
                self._send(204 if changed else 409, "text/plain; charset=utf-8", b"")

            def _send(self, status: int, content_type: str, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

        return Handler
```

- [ ] **Step 4: Write the page**

Create `clipper/page.html`:

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Demos to grab</title>
<style>
:root{--bg:#0e0f11;--grid:#15171a;--panel:#191b1f;--panel2:#22252a;--line:#2c3036;--ink:#eff0f1;--muted:#8b9199;
  --orange:#ff5500;--blue:#3d8be0;--good:#41d075;--bad:#ff5d62;--warn:#f0b43a;color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 "Segoe UI",system-ui,sans-serif;
  background-image:linear-gradient(var(--grid) 1px,transparent 1px),linear-gradient(90deg,var(--grid) 1px,transparent 1px);
  background-size:26px 26px}
main{max-width:1100px;margin:0 auto;padding:24px 16px 48px}
.brand{font-weight:800;font-size:15px;color:#4e9ae8;margin:0 0 14px}.brand span{color:var(--orange)}
h1{font-size:26px;margin:0 0 4px}
.sum{color:var(--muted);margin:0 0 16px}
.note{border:1px solid var(--line);background:var(--panel);border-radius:8px;padding:9px 11px;margin:0 0 16px}
.wrap{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}
th{font:600 11px/1 Consolas,monospace;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);
  text-align:left;padding:0 10px 9px;white-space:nowrap}
td{background:var(--panel);border-top:1px solid var(--line);padding:10px;white-space:nowrap;vertical-align:middle}
tr:hover td{background:var(--panel2)}
td small{display:block;color:var(--muted);font-size:12px}
.num{font-family:Consolas,monospace}
.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}.muted{color:var(--muted)}
.b{display:inline-block;font:600 11px/1 Consolas,monospace;padding:4px 6px;border-radius:4px;margin-right:4px}
.ace{background:#f5c542;color:#1a1400}.k4{background:#ff5500;color:#170600}
.k3{background:#33290f;color:#f0b43a;box-shadow:inset 0 0 0 1px #6b5220}
.pill{margin-left:6px;font:600 10px/1 Consolas,monospace;text-transform:uppercase;color:var(--warn);
  border:1px solid #6b5220;border-radius:99px;padding:3px 6px}
.act{text-align:right}
.open,.skip,.undo{font:600 13px/1 "Segoe UI",system-ui,sans-serif;border-radius:6px;padding:8px 12px;cursor:pointer;
  text-decoration:none;display:inline-block}
.open{background:var(--orange);color:#170600;border:0}
.skip{background:transparent;color:var(--ink);border:1px solid var(--line);margin-left:6px}
.undo{background:none;border:0;color:var(--blue);text-decoration:underline;padding:4px}
.done td:not(.act){opacity:.45}
.got{color:var(--good);font-weight:600}.got small{color:var(--muted);font-weight:400}
:focus-visible{outline:2px solid var(--blue);outline-offset:2px}
</style>
</head>
<body>
<main>
  <p class="brand"><span>CS2</span>clipper</p>
  <h1>Demos to grab</h1>
  <p class="sum" id="sum">Loading…</p>
  <p class="note" id="note" hidden>Grab these at your PC. Watch Demo downloads to whichever device you press it on.</p>
  <div class="wrap"><table>
    <thead><tr><th>Match</th><th>Result</th><th>Highlights</th><th>Rating 2.0</th><th>K–D</th><th>ADR</th>
      <th>Link expires</th><th></th></tr></thead>
    <tbody id="rows"></tbody>
  </table></div>
</main>
<script>
const WORDS = [["5k", "ACE", "ace"], ["4k", "4K", "k4"], ["3k", "3K", "k3"]];
const opened = new Set();
let data = {this_pc: false, rows: []};
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

function badges(h) {
  return WORDS.filter(([k]) => h[k]).map(([k, word, cls]) =>
    `<span class="b ${cls}">${h[k] > 1 ? h[k] + "× " : ""}${word}</span>`).join("");
}
function when(iso) {
  const d = new Date(iso);
  const time = d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit"});
  return d.toDateString() === new Date().toDateString()
    ? `Today, ${time}` : `${d.toLocaleDateString([], {day: "numeric", month: "short"})}, ${time}`;
}
function left(iso) {
  const hours = (new Date(iso) - Date.now()) / 3600000;
  return hours >= 48 ? `${Math.ceil(hours / 24)} days` : `${Math.max(1, Math.ceil(hours))} hours`;
}
function actions(r) {
  if (r.state === "grabbed") {
    return `<span class="got">✓ Got it${r.waiting_for ? `<small>rendering waits: ${esc(r.waiting_for)}</small>` : ""}</span>`;
  }
  if (r.state === "skipped") {
    return `<span class="muted">Skipped</span> <button class="undo" data-act="undo" data-id="${esc(r.id)}">Undo</button>`;
  }
  const open = !data.this_pc ? "" : opened.has(r.id) ? `<span class="muted">Waiting for the download…</span>`
    : `<a class="open" href="${esc(r.url)}" target="_blank" rel="noopener" data-open="${esc(r.id)}">Open</a>`;
  return `${open}<button class="skip" data-act="skip" data-id="${esc(r.id)}">Skip</button>`;
}
function render() {
  const pending = data.rows.filter(r => r.state === "ready" || r.state === "announced");
  const count = pending.reduce((n, r) => n + r.highlights["3k"] + r.highlights["4k"] + r.highlights["5k"], 0);
  const soonest = [...pending].sort((a, b) => a.expires_at.localeCompare(b.expires_at))[0];
  const urgent = soonest && new Date(soonest.expires_at) - Date.now() <= 3 * 86400000;
  document.getElementById("sum").textContent = pending.length
    ? `${pending.length} match${pending.length > 1 ? "es" : ""} · ${count} Highlight${count > 1 ? "s" : ""}`
      + (urgent ? ` · ${soonest.map} expires in ${left(soonest.expires_at)}` : "")
    : "All caught up. New matches show up here after you play.";
  document.getElementById("note").hidden = data.this_pc;
  document.getElementById("rows").innerHTML = data.rows.map(r => {
    const rating = r.rating >= 1.1 ? "good" : r.rating < 0.95 ? "bad" : "";
    const soon = new Date(r.expires_at) - Date.now() <= 3 * 86400000;
    return `<tr class="${r.state === "skipped" || r.state === "grabbed" ? "done" : ""}">
      <td><b>${esc(r.map)}</b><small>${when(r.finished_at)}</small></td>
      <td class="num ${r.won ? "good" : "bad"}">${r.won ? "W" : "L"} ${esc(r.score)}</td>
      <td>${badges(r.highlights)}</td>
      <td class="num ${rating}">${r.rating.toFixed(2)}</td>
      <td class="num">${esc(r.kd)}</td>
      <td class="num">${r.adr.toFixed(1)}</td>
      <td><span class="${soon ? "warn" : ""}">${left(r.expires_at)}</span>${r.reminded ? '<span class="pill">Reminder</span>' : ""}</td>
      <td class="act">${actions(r)}</td></tr>`;
  }).join("");
}
async function load() {
  try {
    const response = await fetch("demos.json", {cache: "no-store"});
    if (response.ok) { data = await response.json(); render(); }
  } catch (e) {
    document.getElementById("sum").textContent = "Can't reach clipper right now. Is it running?";
  }
}
document.addEventListener("click", async e => {
  const link = e.target.closest("a[data-open]");
  if (link) { opened.add(link.dataset.open); setTimeout(render, 0); return; }
  const button = e.target.closest("button[data-act]");
  if (!button) return;
  button.disabled = true;
  await fetch(`demos/${button.dataset.id}/${button.dataset.act}`, {method: "POST"});
  await load();
});
load();
setInterval(load, 5000);
</script>
</body>
</html>
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_page.py -q`
Expected: PASS (6 passed). No Windows Firewall prompt appears: the tests bind 127.0.0.1.

- [ ] **Step 6: Commit**

```bash
git add clipper/page.py clipper/page.html tests/test_page.py
git commit -m "feat: the Demos to grab page — list, Open on the PC, Skip and Undo anywhere"
```

---

### Task 9: Wiring it into the worker and the command line

**Files:**
- Modify: `clipper/procs.py` (`ProcessProbe`, `SystemProbe`)
- Modify: `clipper/worker.py` (`Services`, `Worker.tick`)
- Modify: `clipper/cli.py` (imports, `start_match_alerts`, `build_worker`, `format_status`)
- Test: `tests/test_procs.py` (new), `tests/test_worker.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `MatchAlerts` (Task 6), `FaceitClient` (Task 3), `PageServer` (Task 8), `load_env` and the settings (Task 1), `notify` with actions (Task 7).
- Produces: `SystemProbe.user_cs2_running() -> bool`; `AlertsStep` Protocol (`tick() -> None`) and `Services.alerts: AlertsStep | None = None`; `start_match_alerts(cfg: Config, index: Index, probe: SystemProbe, gate: Gate) -> MatchAlerts | None`; `format_status(index: Index, gate: GateStatus, host: str | None = None) -> str`, which adds a `Match alerts: …` line once the worker has set `alerts_status`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_procs.py`:

```python
import clipper.procs
from clipper.procs import SystemProbe


class FakeProcess:
    def __init__(self, name, args):
        self.info, self._args = {"name": name}, args

    def cmdline(self):
        return self._args


def test_only_a_cs2_without_the_render_flag_is_the_users(monkeypatch):
    running = [FakeProcess("explorer.exe", []), FakeProcess("cs2.exe", ["cs2.exe", "-insecure"])]
    monkeypatch.setattr(clipper.procs.psutil, "process_iter", lambda attrs: iter(running))
    assert SystemProbe().user_cs2_running() is False
    running.append(FakeProcess("CS2.EXE", ["cs2.exe", "-steam"]))
    assert SystemProbe().user_cs2_running() is True
```

Append to `tests/test_worker.py`:

```python
class FailingAlerts:
    def __init__(self):
        self.ticks = 0

    def tick(self):
        self.ticks += 1
        raise RuntimeError("FACEIT is having a bad day")


def test_match_alerts_run_every_tick_and_never_stop_the_pipeline(world):
    world.services.alerts = FailingAlerts()
    demo_id = world.add_demo()
    world.ticks(8)
    assert world.services.alerts.ticks == 8
    assert world.index.demo(demo_id)["state"] == "done"
```

In `tests/test_cli.py`, change the imports at the top to:

```python
from datetime import datetime, timezone

import pytest

from clipper.cli import AlreadyRunning, cmd_retry, cmd_run, format_status, single_instance, start_match_alerts
from clipper.config import Config
from clipper.gate import GateStatus
from clipper.index import Index
```

and append:

```python
def test_status_shows_match_alerts_and_the_page(index):
    index.set_flag("alerts_status", "on")
    index.set_flag("page_port", "8765")
    index.save_faceit_match("1-00000000-0000-0000-0000-000000000001",
                            datetime(2026, 9, 25, tzinfo=timezone.utc), "ready")
    lines = format_status(index, GateStatus(ok=True), host="gaming-pc").splitlines()
    assert lines[1] == "Match alerts: on · 1 to grab · http://gaming-pc:8765/demos"


def test_status_says_why_match_alerts_are_off(index):
    index.set_flag("alerts_status", "off: set FACEIT_API_KEY and FACEIT_NICKNAME in .env")
    lines = format_status(index, GateStatus(ok=True)).splitlines()
    assert lines[1] == "Match alerts: off: set FACEIT_API_KEY and FACEIT_NICKNAME in .env"


def test_match_alerts_stay_off_without_the_setting_or_the_key(index, tmp_path, monkeypatch):
    folders = {"downloads_dir": tmp_path, "data_root": tmp_path / "clips", "index_path": tmp_path / "clipper.sqlite"}
    assert start_match_alerts(Config(**folders, match_alerts=False), index, probe=None, gate=None) is None
    assert index.get_flag("alerts_status") == "off: match_alerts = false in clipper.toml"
    monkeypatch.setattr("clipper.cli.load_env", lambda path: {})
    assert start_match_alerts(Config(**folders), index, probe=None, gate=None) is None
    assert index.get_flag("alerts_status") == "off: set FACEIT_API_KEY and FACEIT_NICKNAME in .env"
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_procs.py tests/test_worker.py tests/test_cli.py -q`
Expected: FAIL — `AttributeError: 'SystemProbe' object has no attribute 'user_cs2_running'`, and `ImportError: cannot import name 'start_match_alerts'`.

- [ ] **Step 3: Implement the probe**

In `clipper/procs.py`, add to `ProcessProbe`, after `hooked_cs2_running`:

```python
    def user_cs2_running(self) -> bool: ...
```

and to `SystemProbe`, after `hooked_cs2_running`:

```python
    def user_cs2_running(self) -> bool:
        """A CS2 the user started: any cs2.exe without the render flag. One whose command line cannot
        be read counts as the user's."""
        for process in psutil.process_iter(["name"]):
            if (process.info["name"] or "").lower() != "cs2.exe":
                continue
            try:
                if not any(arg.lower() == HOOKED_FLAG for arg in process.cmdline()):
                    return True
            except psutil.NoSuchProcess:
                continue
            except psutil.AccessDenied:
                return True
        return False
```

- [ ] **Step 4: Implement the worker hook**

In `clipper/worker.py`, add after the `GateLike` protocol:

```python
class AlertsStep(Protocol):
    def tick(self) -> None: ...
```

add a last field to `Services`, after `sleep`:

```python
    alerts: AlertsStep | None = None
```

and replace `Worker.tick` with:

```python
    def tick(self) -> None:
        """Take any new Demos, move every unfinished Demo on by at most one step, then run match alerts."""
        for path in self.services.intake.ready():
            try:
                self.services.intake.take(path, self.index)
            except OSError:
                log.exception("could not take %s; will try again", path.name)
        for demo in self.index.demos_in(ACTIVE_STATES):
            self._advance(demo)
        if self.services.alerts is not None:
            try:
                self.services.alerts.tick()
            except Exception:  # noqa: BLE001 - match alerts must never stop the pipeline
                log.exception("match alerts failed")
```

- [ ] **Step 5: Implement the command-line side**

In `clipper/cli.py`, add `import socket` to the standard-library imports, and these imports among the `clipper` ones (keep them alphabetical):

```python
from clipper.alerts import MatchAlerts
from clipper.config import REPO_ROOT, Config, load_config, load_env
from clipper.faceit import FaceitClient
from clipper.page import PageServer
```

(the `clipper.config` line replaces the existing one).

Replace the first two lines of `format_status` (its `def` line and the `lines = [...]` line) with:

```python
def format_status(index: Index, gate: GateStatus, host: str | None = None) -> str:
    lines = ["Gate: " + ("clear" if gate.ok else "waiting: " + "; ".join(gate.reasons))]
    alerts = index.get_flag("alerts_status")
    if alerts is not None:
        port = index.get_flag("page_port")
        if alerts == "on" and port:
            waiting = len(index.faceit_matches_in(("ready", "announced")))
            alerts += f" · {waiting} to grab · http://{host or socket.gethostname()}:{port}/demos"
        lines.append(f"Match alerts: {alerts}")
```

Add before `build_worker`:

```python
def start_match_alerts(cfg: Config, index: Index, probe: SystemProbe, gate: Gate) -> MatchAlerts | None:
    """Start the Demos to grab page and return the match alerts step. When alerts are off, say why in
    the index (clipper status shows it) and return None."""
    if not cfg.match_alerts:
        index.set_flag("alerts_status", "off: match_alerts = false in clipper.toml")
        return None
    env = load_env(REPO_ROOT / ".env")
    key, nickname = env.get("FACEIT_API_KEY", ""), env.get("FACEIT_NICKNAME", "")
    if not (key and nickname):
        index.set_flag("alerts_status", "off: set FACEIT_API_KEY and FACEIT_NICKNAME in .env")
        return None
    page = PageServer(cfg.index_path, cfg.page_port, gate_reasons=lambda: gate.check().reasons)
    page.start()
    index.set_flag("page_port", str(page.port))
    return MatchAlerts(index, FaceitClient(key), notify, probe.user_cs2_running, nickname=nickname,
                       subject_steamid=cfg.subject_steamid, page_url=f"http://127.0.0.1:{page.port}/demos",
                       stopped_playing_minutes=cfg.stopped_playing_minutes)
```

In `build_worker`, add one argument at the end of the `Services(...)` call, after `notify=notify,`:

```python
        alerts=start_match_alerts(cfg, index, probe, gate),
```

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS — the baseline plus every test added in Tasks 1–9, and nothing failing. (With CS:DM's Postgres down, its 5 integration tests skip.)

- [ ] **Step 7: Commit**

```bash
git add clipper/procs.py clipper/worker.py clipper/cli.py tests/test_procs.py tests/test_worker.py tests/test_cli.py
git commit -m "feat: match alerts run in the worker; clipper status shows them and the page"
```

---

### Task 10: First run with the user (user — at the PC)

An executing agent stops here and hands these steps to the user. Nothing below may run unattended: it shows notifications, listens on the network, and (step 5) involves CS2.

- [ ] **Step 1: Check that toast buttons open the browser**

The spec's open question. Run:

```bash
uv run python -c "from clipper.notify import notify; notify('Match alerts test', 'Does Show matches open your browser?', actions=(('Show matches', 'https://www.faceit.com/en'), ('Not now', None)))"
```

Expected: a notification with two buttons. **Show matches** opens FACEIT in the default browser, and **Not now** closes it. If the button does nothing, stop here and report back: the notification then needs its own registered app ID (a Start-menu shortcut carrying an AppUserModelID), which is a separate change.

- [ ] **Step 2: Start the worker and read its status**

Close CS2, then start the worker in a terminal: `uv run clipper run` (or restart it however it normally starts). When Windows Firewall asks whether Python may use networks, allow **private networks**. In a second terminal:

```bash
uv run clipper status
```

Expected: a line `Match alerts: on · N to grab · http://<PC name>:8765/demos`. Within a minute, with CS2 closed, the first Match Alert arrives covering the past 30 days' matches with Highlights whose Demo is not downloaded yet.

- [ ] **Step 3: Use the page on the PC**

Click **Show matches**. Check: newest first; the stat line; Highlight badges; the link-expiry column. Press **Open** on one match. The matchroom opens and the row shows "Waiting for the download…". Press Watch Demo there. Within about 15 seconds of the download finishing, the row shows "✓ Got it", with what rendering waits for if the Gate is closed (for example FACEIT AC). Skip another match, then Undo it.

- [ ] **Step 4: Use the page on the phone**

On the same Wi-Fi, open `http://<PC name>:8765/demos` (or the PC's IPv4 address from `ipconfig`). Expected: no Open buttons, and the note "Grab these at your PC". Skip and Undo work and show up on the PC's page within 5 seconds.

- [ ] **Step 5: A real session end**

Play a FACEIT match, or just start CS2 and close it. Expected: 5 minutes after CS2 closes, one Match Alert covering only the matches since the last check. Nothing arrives while CS2 is open.

- [ ] **Step 6: Document it in README.md**

`README.md` is currently untracked; the user decides whether to commit it. Add three rows to its Configuration table:

```markdown
| `match_alerts` | `true` | After you play, say which FACEIT matches have Highlights (needs `.env`) |
| `stopped_playing_minutes` | `5.0` | How long CS2 stays closed before that notification |
| `page_port` | `8765` | Port of the Demos to grab page (`http://<this PC>:8765/demos`) |
```

and this section after "Running the background app":

```markdown
## Match alerts

Five minutes after you close CS2, one notification lists your new FACEIT matches with a 3K, 4K or Ace,
known from FACEIT's stats before any Demo is downloaded. **Show matches** opens the Demos to grab page:
**Open** takes you to the matchroom (press Watch Demo there), and **Skip** drops a match. A match you
neither grab nor skip gets one reminder 3 days before its Demo link expires. The page also opens on
your phone (same Wi-Fi, or Tailscale) for looking and skipping. It needs `FACEIT_API_KEY` and
`FACEIT_NICKNAME` in `.env`; `clipper status` says whether alerts are on and where the page is.
```

- [ ] **Step 7: Merge when satisfied**

The branch `match-alerts-spec` holds the spec, this plan and the code. Merging it into `master` is the user's call.
