"""Tests for clipper.web: the Flask app (spec: Pages, Reach from other devices; Testing, Web).

Ported from tests/test_page.py (the Demos to grab page moved in), plus the reach guard, the action
guard, the no-CORS rule, and WebServer's real-socket behaviour."""
from __future__ import annotations

import json
import socket
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from clipper import protect
from clipper.checks import Check
from clipper.index import Index
from clipper.model import FaceitStats, Highlight, MatchInfo
from clipper.settings import FIELDS, KEY_FIELD, STORED_KEY, SettingsStore
from clipper.state import Rendering, Snapshot
from clipper.web import MARKER_HEADER, WebContext, WebServer, create_app

NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)
FIRST = "1-00000000-0000-0000-0000-000000000001"
SECOND = "1-00000000-0000-0000-0000-000000000002"
ROOM = "https://www.faceit.com/en/cs2/room/"
THREE_K = FaceitStats(map_name="de_inferno", team_score=13, opponent_score=9, won=True, rounds=22, kills=24,
                      deaths=15, assists=5, adr=94.0, double_kills=3, triple_kills=2, quadro_kills=1)

PC = "http://127.0.0.1:8765"
LAN = "http://10.0.0.9:8765"
PHONE_ENVIRON = {"REMOTE_ADDR": "10.0.0.9"}
MARKED = {MARKER_HEADER: "1"}


@pytest.fixture
def index(tmp_path):
    idx = Index(tmp_path / "clipper.sqlite")
    idx.save_faceit_match(FIRST, NOW - timedelta(hours=5), "ready", THREE_K, 1.4983, ROOM + FIRST)
    idx.announce([FIRST], NOW - timedelta(hours=4))
    idx.save_faceit_match(SECOND, NOW - timedelta(hours=2), "ready", THREE_K, 1.2, ROOM + SECOND)
    yield idx
    idx.close()


def app_for(tmp_path, **overrides):
    ctx = WebContext(index_path=tmp_path / "clipper.sqlite",
                      gate_reasons=lambda: ("FACEIT AC is running",), clock=lambda: NOW, **overrides)
    return create_app(ctx)


@pytest.fixture
def client(tmp_path, index):
    return app_for(tmp_path).test_client()


def pc_get(client, path, **kw):
    return client.get(path, base_url=PC, **kw)


def pc_post(client, path, **kw):
    return client.post(path, base_url=PC, **kw)


def pc_put(client, path, **kw):
    return client.put(path, base_url=PC, **kw)


def phone_get(client, path, **kw):
    return client.get(path, base_url=LAN, environ_base=PHONE_ENVIRON, **kw)


def phone_post(client, path, **kw):
    return client.post(path, base_url=LAN, environ_base=PHONE_ENVIRON, **kw)


def phone_put(client, path, **kw):
    return client.put(path, base_url=LAN, environ_base=PHONE_ENVIRON, **kw)


# --- ported from tests/test_page.py ---------------------------------------------------------------


def test_the_page_is_served(client):
    response = pc_get(client, "/demos")
    assert response.status_code == 200
    assert response.content_type == "text/html; charset=utf-8"
    assert b"Demos to grab" in response.data


def test_the_list_comes_newest_first_with_the_stat_line(client):
    response = pc_get(client, "/demos.json")
    data = response.get_json()
    assert (response.status_code, response.content_type, data["this_pc"]) == (200, "application/json", True)
    assert [row["id"] for row in data["rows"]] == [SECOND, FIRST]
    assert data["rows"][1] == {
        "id": FIRST, "map": "Inferno", "finished_at": "2026-09-25T15:00:00+00:00",
        "expires_at": "2026-10-25T15:00:00+00:00", "won": True, "score": "13–9",
        "highlights": {"3k": 2, "4k": 1, "5k": 0}, "rating": 1.5, "kd": "24–15", "adr": 94.0,
        "state": "announced", "reminded": False, "url": ROOM + FIRST, "waiting_for": "",
    }


def test_skip_and_undo_reach_the_index(client, index):
    assert pc_post(client, f"/demos/{FIRST}/skip", headers=MARKED).status_code == 204
    assert index.faceit_match(FIRST)["state"] == "skipped"
    assert pc_post(client, f"/demos/{FIRST}/skip", headers=MARKED).status_code == 409   # nothing left to skip
    assert pc_post(client, f"/demos/{FIRST}/undo", headers=MARKED).status_code == 204
    assert index.faceit_match(FIRST)["state"] == "announced"


def test_other_devices_get_no_open_button(tmp_path, index):
    app = app_for(tmp_path, own_addresses=lambda: {"10.0.0.9"})
    response = app.test_client().get("/demos.json", base_url=PC)
    assert response.get_json()["this_pc"] is False


def test_a_grabbed_demo_says_what_rendering_waits_for(client, index):
    name = f"{FIRST}-1-1.dem.zst"
    index.add_demo(name, "a" * 64, Path("E:/cs2clips/demos") / name)
    index.link_grabbed_matches(NOW)
    rows = pc_get(client, "/demos.json").get_json()["rows"]
    grabbed = next(row for row in rows if row["id"] == FIRST)
    assert (grabbed["state"], grabbed["waiting_for"]) == ("grabbed", "FACEIT AC is running")


def test_nothing_else_is_served(client):
    for path in ("/demos/../clipper.sqlite", "/demos.json/x"):
        assert pc_get(client, path).status_code == 404
    assert pc_post(client, "/demos/not-a-match/skip", headers=MARKED).status_code == 404
    assert pc_post(client, f"/demos/{FIRST}/delete", headers=MARKED).status_code == 404


# --- reach: PC-only vs phone routes ----------------------------------------------------------------


def test_health_answers_the_pc(client):
    response = pc_get(client, "/health")
    assert response.status_code == 200
    assert response.get_json() == {"ok": True, "app": "cs2-clipper"}


def test_health_refuses_a_non_loopback_client(client):
    assert phone_get(client, "/health").status_code == 403


def test_health_refuses_dns_rebinding(client):
    response = client.get("/health", base_url=PC, headers={"Host": "evil.example:8765"})
    assert response.status_code == 403


def test_health_refuses_a_host_whose_port_is_not_the_servers(client):
    response = client.get("/health", base_url=PC, headers={"Host": "127.0.0.1:9999"})
    assert response.status_code == 403


def test_health_accepts_localhost_as_the_host(client):
    response = client.get("/health", base_url=PC, headers={"Host": "localhost:8765"})
    assert response.status_code == 200


def test_phone_routes_answer_the_phone_with_a_lan_host(client):
    assert phone_get(client, "/demos").status_code == 200
    assert phone_get(client, "/demos.json").status_code == 200
    assert phone_get(client, "/app.css").status_code == 200
    assert phone_get(client, "/app.js").status_code == 200


def test_an_unknown_path_is_404_on_the_pc_and_403_from_the_phone(client):
    assert pc_get(client, "/nope").status_code == 404
    assert phone_get(client, "/nope").status_code == 403


# --- action guard: the marker header ---------------------------------------------------------------


def test_skip_without_the_marker_is_refused_from_pc_and_phone(client):
    assert pc_post(client, f"/demos/{FIRST}/skip").status_code == 403
    assert phone_post(client, f"/demos/{FIRST}/skip").status_code == 403


def test_skip_with_the_marker_succeeds_from_the_phone(client):
    assert phone_post(client, f"/demos/{FIRST}/skip", headers=MARKED).status_code == 204


# --- /api/window: PC-only, marker required, Task 10's hand-over lands here -------------------------


def test_api_window_204_and_recorded(tmp_path):
    calls = []
    app = app_for(tmp_path, open_window=lambda page: calls.append(page))
    response = app.test_client().post("/api/window", base_url=PC, headers=MARKED, json={"page": "/reels"})
    assert response.status_code == 204
    assert calls == ["/reels"]


def test_api_window_400_for_a_page_not_in_pages(client):
    assert pc_post(client, "/api/window", headers=MARKED, json={"page": "/nope"}).status_code == 400


def test_api_window_400_for_a_missing_page(client):
    assert pc_post(client, "/api/window", headers=MARKED, json={}).status_code == 400


def test_api_window_400_for_a_non_json_body(client):
    response = pc_post(client, "/api/window", headers=MARKED, data=b"not json")
    assert response.status_code == 400


def test_api_window_403_without_the_marker(client):
    assert pc_post(client, "/api/window", json={"page": "/status"}).status_code == 403


def test_api_window_403_from_a_non_loopback_client(client):
    response = phone_post(client, "/api/window", headers=MARKED, json={"page": "/status"})
    assert response.status_code == 403


# --- GET /api/window: what the open window's page polls every 2 s (Task 14) ------------------------


def test_get_api_window_returns_the_contexts_request(tmp_path):
    app = app_for(tmp_path, window_request=lambda: {"seq": 3, "page": "/reels"})
    response = pc_get(app.test_client(), "/api/window")
    assert response.status_code == 200
    assert response.get_json() == {"seq": 3, "page": "/reels"}
    assert response.headers["Cache-Control"] == "no-store"


def test_get_api_window_says_no_request_has_been_made_by_default(client):
    assert pc_get(client, "/api/window").get_json() == {"seq": 0, "page": "/status"}


def test_get_api_window_refuses_a_phone_and_a_foreign_host(client):
    assert phone_get(client, "/api/window").status_code == 403
    assert client.get("/api/window", base_url=PC, headers={"Host": "evil.example:8765"}).status_code == 403


# --- no CORS, ever -----------------------------------------------------------------------------------


def test_no_response_ever_carries_a_cors_header(client):
    responses = [
        pc_get(client, "/demos.json"),
        phone_get(client, "/demos.json"),
        client.open("/demos.json", method="OPTIONS", base_url=PC),
        client.open(f"/demos/{FIRST}/skip", method="OPTIONS", base_url=LAN, environ_base=PHONE_ENVIRON),
    ]
    for response in responses:
        assert not any(name.lower().startswith("access-control-") for name in response.headers.keys())


# --- WebServer: real sockets --------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_webserver_serves_health_over_http(tmp_path):
    server = WebServer(app_for(tmp_path), 0, host="127.0.0.1", tries=1)
    server.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{server.port}/health", timeout=5) as response:
            assert response.status == 200
            assert json.loads(response.read()) == {"ok": True, "app": "cs2-clipper"}
    finally:
        server.stop()


def test_webserver_takes_the_next_free_port(tmp_path):
    port = _free_port()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    try:
        server = WebServer(app_for(tmp_path), port, host="127.0.0.1")
        try:
            assert server.port == port + 1
        finally:
            server.stop()
    finally:
        blocker.close()


def test_webserver_raises_when_every_port_is_taken(tmp_path):
    port = _free_port()
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    try:
        with pytest.raises(OSError, match=f"no free port in {port}–{port}"):
            WebServer(app_for(tmp_path), port, host="127.0.0.1", tries=1)
    finally:
        blocker.close()


def test_webserver_stop_frees_the_port(tmp_path):
    server = WebServer(app_for(tmp_path), 0, host="127.0.0.1", tries=1)
    port = server.port
    server.start()
    server.stop()
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))   # raises OSError if the port is still held
    finally:
        probe.close()


# --- Status page (Task 11) ------------------------------------------------------------------------

MATCH_CHECKSUM = "aea4e59ccfc6c962"


def _add_demo(index, name="1-status.dem.zst", sha="c" * 64):
    return index.add_demo(name, sha, Path("E:/cs2clips/demos") / name)


def test_root_redirects_to_status(client):
    response = pc_get(client, "/")
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/status")


def test_status_page_is_served(client):
    response = pc_get(client, "/status")
    assert response.status_code == 200
    assert response.content_type == "text/html; charset=utf-8"


def test_api_status_shape_and_values(tmp_path, index):
    demo_id = _add_demo(index)
    index.save_match(MatchInfo(checksum=MATCH_CHECKSUM, map_name="de_mirage", played_at=NOW,
                               team_score=13, opponent_score=5))
    index.advance(demo_id, "rendering", match_checksum=MATCH_CHECKSUM)
    job_id = index.queue_render(demo_id, "player", attempt=1)
    index.start_render(job_id, Path("E:/out"), Path("E:/log.txt"))

    snapshot = Snapshot(problems=(), paused_by=None, rendering=Rendering("de_mirage", "player", 1790000000.0),
                        quitting=None, pages_off=None)
    checks = [Check("HLAE", True, "2.192.6 (latest)"), Check("Postgres", False, "not running", "start it")]
    app = app_for(tmp_path, snapshot=lambda: snapshot, warnings=lambda: ("settings.json: top_n bad",),
                 checks=lambda: checks)
    data = pc_get(app.test_client(), "/api/status").get_json()

    assert data["summary"] == "Rendering Mirage (player view)"
    assert data["rendering"] == {"map": "Mirage", "perspective": "player", "started_at": 1790000000.0}
    assert (data["paused_by"], data["quitting"], data["pages_off"]) == (None, None, None)
    assert data["problems"] == []
    assert data["warnings"] == ["settings.json: top_n bad"]
    assert data["checks"] == [
        {"name": "HLAE", "ok": True, "detail": "2.192.6 (latest)", "hint": ""},
        {"name": "Postgres", "ok": False, "detail": "not running", "hint": "start it"},
    ]
    demo = next(d for d in data["demos"] if d["id"] == demo_id)
    assert demo == {
        "id": demo_id, "file_name": "1-status.dem.zst", "state": "rendering", "map": "Mirage",
        "error": None, "retry": False,
        "jobs": [{"perspective": "player", "attempt": 1, "state": "running", "failure": None}],
    }


def test_api_status_demos_are_filtered_to_unfinished_and_ordered_newest_first(tmp_path, index):
    done = _add_demo(index, name="1-done.dem.zst", sha="d" * 64)
    index.advance(done, "done")
    skipped = _add_demo(index, name="1-skipped.dem.zst", sha="e" * 64)
    index.advance(skipped, "skipped")
    older = _add_demo(index, name="1-older.dem.zst", sha="f" * 64)
    newer = _add_demo(index, name="1-newer.dem.zst", sha="a1" * 32)

    data = pc_get(app_for(tmp_path).test_client(), "/api/status").get_json()

    assert [d["id"] for d in data["demos"]] == [newer, older]


def test_api_status_a_failed_demo_shows_its_error_and_can_retry_and_has_no_jobs_before_rendering(tmp_path, index):
    demo_id = _add_demo(index)
    index.fail(demo_id, "boom: something broke")

    data = pc_get(app_for(tmp_path).test_client(), "/api/status").get_json()

    demo = next(d for d in data["demos"] if d["id"] == demo_id)
    assert (demo["state"], demo["error"], demo["retry"], demo["map"], demo["jobs"]) == (
        "failed", "boom: something broke", True, None, [])


def test_api_status_refuses_a_phone(client):
    assert phone_get(client, "/api/status").status_code == 403


def test_api_log_returns_the_lines_asking_for_200(tmp_path):
    app = app_for(tmp_path, log_lines=lambda n: [f"limit={n}", "a line"])
    response = pc_get(app.test_client(), "/api/log")
    assert response.status_code == 200
    assert response.get_json() == {"lines": ["limit=200", "a line"]}


def test_api_log_refuses_a_phone(client):
    assert phone_get(client, "/api/log").status_code == 403


def test_retry_replies_200_with_the_new_state(tmp_path, index):
    demo_id = _add_demo(index)
    index.fail(demo_id, "boom")
    response = pc_post(app_for(tmp_path).test_client(), f"/api/demos/{demo_id}/retry", headers=MARKED)
    assert response.status_code == 200
    assert response.get_json() == {"state": "spotted"}
    assert index.demo(demo_id)["state"] == "spotted"


def test_retry_on_a_demo_that_has_not_failed_is_409(tmp_path, index):
    demo_id = _add_demo(index)
    response = pc_post(app_for(tmp_path).test_client(), f"/api/demos/{demo_id}/retry", headers=MARKED)
    assert response.status_code == 409
    assert "error" in response.get_json()


def test_retry_on_an_unknown_demo_is_404(tmp_path):
    response = pc_post(app_for(tmp_path).test_client(), "/api/demos/999/retry", headers=MARKED)
    assert response.status_code == 404


def test_retry_refuses_a_phone_and_needs_the_marker(tmp_path, index):
    demo_id = _add_demo(index)
    index.fail(demo_id, "boom")
    app = app_for(tmp_path)
    assert pc_post(app.test_client(), f"/api/demos/{demo_id}/retry").status_code == 403
    assert phone_post(app.test_client(), f"/api/demos/{demo_id}/retry", headers=MARKED).status_code == 403


def test_resume_calls_the_context_and_replies_204(tmp_path):
    calls = []
    app = app_for(tmp_path, resume=lambda: calls.append(True))
    response = pc_post(app.test_client(), "/api/resume", headers=MARKED)
    assert response.status_code == 204
    assert calls == [True]


def test_resume_refuses_a_phone_and_needs_the_marker(client):
    assert pc_post(client, "/api/resume").status_code == 403
    assert phone_post(client, "/api/resume", headers=MARKED).status_code == 403


def test_quit_passes_the_mode_through_and_returns_the_result(tmp_path):
    calls = []
    app = app_for(tmp_path, quit=lambda mode: calls.append(mode) or "ask")
    response = pc_post(app.test_client(), "/api/quit", headers=MARKED, json={})
    assert response.status_code == 200
    assert response.get_json() == {"result": "ask"}
    assert calls == [None]

    pc_post(app.test_client(), "/api/quit", headers=MARKED, json={"mode": "now"})
    assert calls == [None, "now"]

    pc_post(app.test_client(), "/api/quit", headers=MARKED, json={"mode": "after_render"})
    assert calls == [None, "now", "after_render"]


def test_quit_with_a_bad_mode_is_400_and_does_not_call_the_context(tmp_path):
    calls = []
    app = app_for(tmp_path, quit=lambda mode: calls.append(mode) or "now")
    response = pc_post(app.test_client(), "/api/quit", headers=MARKED, json={"mode": "later"})
    assert response.status_code == 400
    assert calls == []


def test_quit_refuses_a_phone_and_needs_the_marker(client):
    assert pc_post(client, "/api/quit", json={}).status_code == 403
    assert phone_post(client, "/api/quit", headers=MARKED, json={}).status_code == 403


def test_status_and_new_routes_answer_403_on_a_foreign_host(client):
    assert client.get("/status", base_url=PC, headers={"Host": "evil.example:8765"}).status_code == 403


# --- Reels page (Task 12) -------------------------------------------------------------------------

REEL_MATCH = MatchInfo(checksum="c0ffee00c0ffee00", map_name="de_mirage",
                       played_at=datetime(2026, 9, 27, 18, 0, tzinfo=timezone.utc),
                       team_score=13, opponent_score=5)


def _reels_index(index) -> int:
    """A done Demo for REEL_MATCH with one selected Highlight (round 8, 4K). Returns its highlight id;
    the caller adds the Reel(s) it needs."""
    index.save_match(REEL_MATCH)
    demo_id = _add_demo(index, name="1-reel.dem.zst", sha="b" * 64)
    index.advance(demo_id, "done", match_checksum=REEL_MATCH.checksum)
    hl = Highlight(round=8, type="4K", score=40, reasons=("4k",), frag_ticks=(100,),
                   round_start_tick=0, round_end_tick=5000)
    index.save_highlights(REEL_MATCH.checksum, [hl], {8})
    return index.selected_highlights(REEL_MATCH.checksum)[0]["id"]


def test_reels_page_is_served(client):
    response = pc_get(client, "/reels")
    assert response.status_code == 200
    assert response.content_type == "text/html; charset=utf-8"


def test_api_reels_shape(tmp_path, index):
    highlight_id = _reels_index(index)
    video_path = tmp_path / "r8-player.mp4"
    video_path.write_bytes(b"0123456789")
    index.save_reel(highlight_id, "player", video_path, 12.5)

    data = pc_get(app_for(tmp_path).test_client(), "/api/reels").get_json()

    assert len(data) == 1
    match = data[0]
    assert (match["checksum"], match["map"], match["score"], match["result"]) == (
        REEL_MATCH.checksum, "Mirage", "13–5", "win")
    assert match["played_at"] == REEL_MATCH.played_at.isoformat()
    assert len(match["highlights"]) == 1
    h = match["highlights"][0]
    assert (h["round"], h["type"], h["reasons"], h["enemy"]) == (8, "4K", ["4k"], None)
    assert isinstance(h["player"], int)


def test_api_reels_refuses_a_phone(client):
    assert phone_get(client, "/api/reels").status_code == 403


def test_reels_page_refuses_a_phone(client):
    assert phone_get(client, "/reels").status_code == 403


def test_reel_video_is_served_whole_and_in_part(tmp_path, index):
    highlight_id = _reels_index(index)
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(bytes(range(200)))
    index.save_reel(highlight_id, "player", video_path, 12.5)
    reel_id = index.match_reels(REEL_MATCH.checksum)[0]["player_reel_id"]
    client = app_for(tmp_path).test_client()

    whole = pc_get(client, f"/reels/{reel_id}.mp4")
    assert whole.status_code == 200
    assert whole.data == bytes(range(200))

    part = pc_get(client, f"/reels/{reel_id}.mp4", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206
    assert part.data == bytes(range(100))


def test_reel_video_404_for_an_unknown_reel(client):
    assert pc_get(client, "/reels/999999.mp4").status_code == 404


def test_reel_video_404_when_its_file_is_gone(tmp_path, index):
    highlight_id = _reels_index(index)
    index.save_reel(highlight_id, "player", tmp_path / "gone.mp4", 12.5)   # never written to disk
    reel_id = index.match_reels(REEL_MATCH.checksum)[0]["player_reel_id"]

    response = pc_get(app_for(tmp_path).test_client(), f"/reels/{reel_id}.mp4")

    assert response.status_code == 404


def test_reel_video_refuses_a_phone(tmp_path, index):
    highlight_id = _reels_index(index)
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"x")
    index.save_reel(highlight_id, "player", video_path, 1.0)
    reel_id = index.match_reels(REEL_MATCH.checksum)[0]["player_reel_id"]

    response = phone_get(app_for(tmp_path).test_client(), f"/reels/{reel_id}.mp4")

    assert response.status_code == 403


def test_open_folder_passes_the_folder_from_the_index_and_ignores_the_request_body(tmp_path, index):
    highlight_id = _reels_index(index)
    video_path = tmp_path / "videos" / "clip.mp4"
    video_path.parent.mkdir()
    video_path.write_bytes(b"x")
    index.save_reel(highlight_id, "player", video_path, 1.0)
    calls = []
    app = app_for(tmp_path, open_folder=lambda folder: calls.append(folder))

    response = pc_post(app.test_client(), f"/api/reels/{REEL_MATCH.checksum}/open-folder", headers=MARKED,
                       json={"folder": "C:/evil"})

    assert response.status_code == 204
    assert calls == [video_path.parent]


def test_open_folder_404_when_the_match_has_no_reels(tmp_path):
    response = pc_post(app_for(tmp_path).test_client(), f"/api/reels/{'a' * 16}/open-folder", headers=MARKED)
    assert response.status_code == 404


def test_open_folder_404_for_a_malformed_checksum(tmp_path):
    response = pc_post(app_for(tmp_path).test_client(), "/api/reels/not-hex/open-folder", headers=MARKED)
    assert response.status_code == 404


def test_open_folder_refuses_a_phone_and_needs_the_marker(tmp_path):
    checksum = "a" * 16
    app = app_for(tmp_path)
    assert pc_post(app.test_client(), f"/api/reels/{checksum}/open-folder").status_code == 403
    assert phone_post(app.test_client(), f"/api/reels/{checksum}/open-folder", headers=MARKED).status_code == 403


# --- Settings page (Task 13) ------------------------------------------------------------------------


def _settings_app(tmp_path, **overrides):
    """A real SettingsStore on tmp_path (isolated; never the real app data folder), wired the way
    App.start_web wires it."""
    store = SettingsStore(tmp_path / "settings.json")
    overrides.setdefault("load_settings", store.current)
    overrides.setdefault("save_settings", store.save)
    overrides.setdefault("port_in_use", lambda: 8765)
    return app_for(tmp_path, **overrides), store


def test_settings_page_is_served(client):
    response = pc_get(client, "/settings")
    assert response.status_code == 200
    assert response.content_type == "text/html; charset=utf-8"


def test_settings_page_and_api_refuse_a_phone(client):
    assert phone_get(client, "/settings").status_code == 403
    assert phone_get(client, "/api/settings").status_code == 403


def test_api_settings_get_shape_and_field_order(tmp_path):
    app, _store = _settings_app(tmp_path)

    data = pc_get(app.test_client(), "/api/settings").get_json()

    assert [f["name"] for f in data["fields"]] == [f.name for f in FIELDS]
    assert data["fields"][0] == {
        "name": "subject_steamid", "group": "You", "label": "SteamID", "kind": "steamid",
        "minimum": None, "maximum": None, "choices": [],
        "help": "Your SteamID64: 17 digits starting with 7656119.",
    }
    assert data["values"]["top_n"] == 5
    assert "faceit_api_key_protected" not in data["values"]
    assert data["faceit_key_set"] is False
    assert data["warnings"] == []
    assert data["port_in_use"] == 8765


def test_api_settings_get_with_a_saved_key_never_echoes_it_or_its_blob(tmp_path):
    app, store = _settings_app(tmp_path)
    assert store.save({KEY_FIELD: "super-secret-value"}) == {}
    blob = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))[STORED_KEY]

    response = pc_get(app.test_client(), "/api/settings")
    data = response.get_json()
    text = response.get_data(as_text=True)

    assert data["faceit_key_set"] is True
    assert "super-secret-value" not in text
    assert blob not in text


def test_put_settings_saves_a_change_and_the_file_holds_only_that_value(tmp_path):
    app, _store = _settings_app(tmp_path)

    response = pc_put(app.test_client(), "/api/settings", headers=MARKED, json={"top_n": 9})

    assert response.status_code == 200
    assert response.get_json() == {"saved": True, "restart_needed": False}
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8")) == {"top_n": 9}


def test_put_settings_with_bad_fields_is_400_with_one_message_each_and_nothing_written(tmp_path):
    app, _store = _settings_app(tmp_path)

    response = pc_put(app.test_client(), "/api/settings", headers=MARKED,
                      json={"top_n": 999, "aspect_ratio": "21:9"})

    assert response.status_code == 400
    assert set(response.get_json()["errors"]) == {"top_n", "aspect_ratio"}
    assert not (tmp_path / "settings.json").exists()


def test_put_settings_stores_the_key_protected_and_never_echoes_it(tmp_path):
    app, _store = _settings_app(tmp_path)

    response = pc_put(app.test_client(), "/api/settings", headers=MARKED, json={KEY_FIELD: "super-secret-value"})

    assert response.status_code == 200
    assert "super-secret-value" not in response.get_data(as_text=True)
    raw = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert STORED_KEY in raw
    assert protect.unprotect(raw[STORED_KEY]) == "super-secret-value"


def test_put_settings_null_removes_the_key(tmp_path):
    app, store = _settings_app(tmp_path)
    store.save({KEY_FIELD: "the-original-key"})

    response = pc_put(app.test_client(), "/api/settings", headers=MARKED, json={KEY_FIELD: None})

    assert response.status_code == 200
    raw = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert STORED_KEY not in raw


def test_put_settings_restart_needed_when_the_saved_port_differs_from_port_in_use(tmp_path):
    app, _store = _settings_app(tmp_path)   # port_in_use defaults to 8765

    response = pc_put(app.test_client(), "/api/settings", headers=MARKED, json={"page_port": 8766})

    assert response.status_code == 200
    assert response.get_json() == {"saved": True, "restart_needed": True}


def test_put_settings_not_an_object_is_400(tmp_path):
    app, _store = _settings_app(tmp_path)
    client = app.test_client()

    assert pc_put(client, "/api/settings", headers=MARKED, data=b"not json").status_code == 400
    assert pc_put(client, "/api/settings", headers=MARKED, json=[1, 2, 3]).status_code == 400


def test_put_settings_refuses_a_phone_and_needs_the_marker(tmp_path):
    app, _store = _settings_app(tmp_path)
    client = app.test_client()

    assert pc_put(client, "/api/settings", json={"top_n": 9}).status_code == 403
    assert phone_put(client, "/api/settings", headers=MARKED, json={"top_n": 9}).status_code == 403
