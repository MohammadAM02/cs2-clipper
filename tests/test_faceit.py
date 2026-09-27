import urllib.error
from datetime import datetime, timezone

import pytest

from clipper import faceit
from clipper.faceit import AuthError, FaceitClient, FaceitError, MatchDetails, Player, PlayerNotFound, http_get_json

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
    assert FaceitClient(lambda: "key", fetch).player("some one") == Player("p-1", "someone", SUBJECT)
    assert fetch.urls == [f"{faceit.DATA_API}/players?nickname=some%20one"]


def test_a_player_without_cs2_is_an_error():
    fetch = FakeFetch({"/players": {"player_id": "p-1", "nickname": "someone", "games": {}}})
    with pytest.raises(PlayerNotFound, match="no CS2 profile"):
        FaceitClient(lambda: "key", fetch).player("someone")


def test_an_unknown_nickname_is_player_not_found():
    fetch = FakeFetch({"/players": FaceitError("HTTP 404 from FACEIT", 404)})
    with pytest.raises(PlayerNotFound, match="no player called someone"):
        FaceitClient(lambda: "key", fetch).player("someone")


def test_other_errors_from_the_player_lookup_stay_as_they_are():
    fetch = FakeFetch({"/players": FaceitError("HTTP 503 from FACEIT", 503)})
    with pytest.raises(FaceitError) as caught:
        FaceitClient(lambda: "key", fetch).player("someone")
    assert not isinstance(caught.value, PlayerNotFound)

    fetch = FakeFetch({"/players": AuthError("HTTP 401 from FACEIT", 401)})
    with pytest.raises(AuthError):
        FaceitClient(lambda: "key", fetch).player("someone")


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
    found = FaceitClient(lambda: "key", fetch).finished_since("p-1", since)
    assert len(found) == 101
    assert found[0] == ("m0", datetime.fromtimestamp(1_790_000_000, timezone.utc))
    assert f"from={int(since.timestamp())}" in fetch.urls[0]
    assert "offset=100&" in fetch.urls[1]


def test_stats_are_the_subjects_line():
    stats = FaceitClient(lambda: "key", FakeFetch({f"/matches/{MATCH_ID}/stats": STATS})).stats(MATCH_ID, "p-1")
    assert (stats.map_name, stats.team_score, stats.opponent_score, stats.won, stats.rounds) == (
        "de_inferno", 13, 9, True, 22)
    assert (stats.kills, stats.deaths, stats.assists, stats.adr) == (24, 15, 5, 94.0)
    assert stats.highlights == {"3k": 2, "4k": 1, "5k": 0}


def test_stats_from_the_losing_side():
    stats = FaceitClient(lambda: "key", FakeFetch({f"/matches/{MATCH_ID}/stats": STATS})).stats(MATCH_ID, "p-9")
    assert (stats.team_score, stats.opponent_score, stats.won) == (9, 13, False)


def test_stats_not_published_yet_are_none():
    fetch = FakeFetch({f"/matches/{MATCH_ID}/stats": FaceitError("HTTP 404 from FACEIT", 404)})
    assert FaceitClient(lambda: "key", fetch).stats(MATCH_ID, "p-1") is None


def test_details_say_whether_the_demo_is_listed_and_where_the_matchroom_is():
    fetch = FakeFetch({f"/matches/{MATCH_ID}": {"demo_url": ["https://demos.example/x.dem.zst"],
                                                  "faceit_url": "https://www.faceit.com/{lang}/cs2/room/" + MATCH_ID}})
    assert FaceitClient(lambda: "key", fetch).details(MATCH_ID) == MatchDetails(
        demo_listed=True, matchroom_url=f"https://www.faceit.com/en/cs2/room/{MATCH_ID}")
    fetch = FakeFetch({f"/matches/{MATCH_ID}": {"demo_url": []}})
    assert FaceitClient(lambda: "key", fetch).details(MATCH_ID).demo_listed is False


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


def test_the_api_key_getter_is_called_fresh_for_every_request_not_cached(monkeypatch):
    calls: list[int] = []

    def api_key() -> str:
        calls.append(len(calls))
        return "secret-key"

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b'{"ok": true}'

    def urlopen(request, timeout):
        assert request.get_header("Authorization") == "Bearer secret-key"
        return Response()

    monkeypatch.setattr(faceit.urllib.request, "urlopen", urlopen)
    client = FaceitClient(api_key)

    client.details(MATCH_ID)
    client.details(MATCH_ID)

    assert calls == [0, 1]   # asked again for the second request, not reused from the first
