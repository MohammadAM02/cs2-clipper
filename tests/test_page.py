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
