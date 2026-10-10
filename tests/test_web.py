"""Tests for clipper.web: the Flask app (spec: Pages, Reach from other devices; Testing, Web).

Ported from tests/test_page.py (the Demos to grab page moved in), plus the reach guard, the action
guard, the no-CORS rule, and WebServer's real-socket behaviour."""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import urllib.request
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from clipper import protect
from clipper.checks import Check
from clipper.config import Config
from clipper.faceit import FaceitError
from clipper.faceit_oauth import OAuthError
from clipper.index import Index
from clipper.model import FaceitStats, Highlight, MatchInfo
from clipper.render import RenderProgress
from clipper.settings import FIELDS, KEY_FIELD, SECRET_FIELD, STORED_KEY, Loaded, SettingsStore
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


def test_the_stylesheet_lets_hidden_win_over_any_display_a_class_sets(client):
    # `.skip` and `#quit-choice` set `display`, which beats the browser's own `[hidden]` rule: without
    # this one, Resume and the two quit buttons would show whatever the page says.
    css = "".join(pc_get(client, "/app.css").get_data(as_text=True).split())
    assert "[hidden]{display:none!important}" in css


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
    checks = [Check("HLAE", True, "2.192.6 (latest)"), Check("csda", False, "not installed", "run Setup")]
    app = app_for(tmp_path, snapshot=lambda: snapshot, warnings=lambda: ("settings.json: top_n bad",),
                 checks=lambda: checks)
    data = pc_get(app.test_client(), "/api/status").get_json()

    assert data["summary"] == "Rendering Mirage (player view)"
    assert data["rendering"] == {"map": "Mirage", "perspective": "player", "started_at": 1790000000.0, "progress": None}
    assert (data["paused_by"], data["quitting"], data["pages_off"]) == (None, None, None)
    assert data["problems"] == []
    assert data["warnings"] == ["settings.json: top_n bad"]
    assert data["checks"] == [
        {"name": "HLAE", "ok": True, "detail": "2.192.6 (latest)", "hint": ""},
        {"name": "csda", "ok": False, "detail": "not installed", "hint": "run Setup"},
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


def test_api_status_carries_the_progress_the_render_reports(tmp_path):
    progress = RenderProgress("recording", "enemy", done=1, total=2, overall=0.5, seconds_left=None)
    snapshot = Snapshot(rendering=Rendering("de_mirage", "enemy", 1790000000.0, progress=progress))
    data = pc_get(app_for(tmp_path, snapshot=lambda: snapshot).test_client(), "/api/status").get_json()
    assert data["rendering"]["progress"] == {"stage": "recording", "perspective": "enemy", "done": 1, "total": 2,
                                             "overall": 0.5, "seconds_left": None}


def test_api_status_a_failed_demo_shows_its_error_and_can_retry_and_has_no_jobs_before_rendering(tmp_path, index):
    demo_id = _add_demo(index)
    index.fail(demo_id, "boom: something broke")

    data = pc_get(app_for(tmp_path).test_client(), "/api/status").get_json()

    demo = next(d for d in data["demos"] if d["id"] == demo_id)
    assert (demo["state"], demo["error"], demo["retry"], demo["map"], demo["jobs"]) == (
        "failed", "boom: something broke", True, None, [])


def test_api_status_refuses_a_phone(client):
    assert phone_get(client, "/api/status").status_code == 403


def test_api_summary_is_the_pill_and_never_runs_the_checks(tmp_path):
    ran = []
    snapshot = Snapshot(rendering=Rendering("de_mirage", "player", 1790000000.0))
    app = app_for(tmp_path, snapshot=lambda: snapshot, checks=lambda: ran.append(True) or [])

    response = pc_get(app.test_client(), "/api/summary")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.get_json() == {"summary": "Rendering Mirage (player view)", "rendering": True,
                                   "paused_by": None, "problems": []}
    assert ran == []


def test_api_summary_says_idle_paused_and_the_start_up_problems(tmp_path):
    def summary_of(snapshot):
        return pc_get(app_for(tmp_path, snapshot=lambda: snapshot).test_client(), "/api/summary").get_json()

    assert summary_of(Snapshot()) == {"summary": "Idle", "rendering": False, "paused_by": None, "problems": []}
    assert summary_of(Snapshot(paused_by="you")) == {"summary": "Paused by you", "rendering": False,
                                                     "paused_by": "you", "problems": []}
    assert summary_of(Snapshot(problems=("HLAE is missing", "csda is missing"))) == {
        "summary": "HLAE is missing (+1 more)", "rendering": False, "paused_by": None,
        "problems": ["HLAE is missing", "csda is missing"]}


def test_api_summary_refuses_a_phone(client):
    assert phone_get(client, "/api/summary").status_code == 403


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


def test_delete_replies_200_once_the_demo_is_gone(tmp_path):
    asked = []
    app = app_for(tmp_path, delete_demo=lambda demo_id: asked.append(demo_id) or "deleted")
    response = pc_post(app.test_client(), "/api/demos/7/delete", headers=MARKED)
    assert response.status_code == 200
    assert response.get_json() == {"result": "deleted"}
    assert asked == [7]


def test_delete_replies_202_while_the_worker_finishes_its_step_on_the_demo(tmp_path):
    response = pc_post(app_for(tmp_path, delete_demo=lambda demo_id: "deferred").test_client(),
                       "/api/demos/7/delete", headers=MARKED)
    assert response.status_code == 202
    assert response.get_json() == {"result": "deferred"}


def test_delete_on_an_unknown_demo_is_404(tmp_path):
    response = pc_post(app_for(tmp_path, delete_demo=lambda demo_id: None).test_client(),
                       "/api/demos/999/delete", headers=MARKED)
    assert response.status_code == 404


def test_delete_on_a_demo_in_reels_is_409(tmp_path):
    def in_reels(demo_id):
        raise ValueError(f"demo {demo_id} is already in Reels")

    response = pc_post(app_for(tmp_path, delete_demo=in_reels).test_client(), "/api/demos/7/delete",
                       headers=MARKED)
    assert response.status_code == 409
    assert response.get_json() == {"error": "demo 7 is already in Reels"}


def test_delete_refuses_a_phone_and_needs_the_marker(tmp_path):
    asked = []
    app = app_for(tmp_path, delete_demo=lambda demo_id: asked.append(demo_id) or "deleted")
    assert pc_post(app.test_client(), "/api/demos/7/delete").status_code == 403
    assert phone_post(app.test_client(), "/api/demos/7/delete", headers=MARKED).status_code == 403
    assert asked == []


def test_pause_calls_the_context_and_replies_204(tmp_path):
    calls = []
    app = app_for(tmp_path, pause=lambda: calls.append(True))
    response = pc_post(app.test_client(), "/api/pause", headers=MARKED)
    assert response.status_code == 204
    assert calls == [True]


def test_pause_refuses_a_phone_and_needs_the_marker(client):
    assert pc_post(client, "/api/pause").status_code == 403
    assert phone_post(client, "/api/pause", headers=MARKED).status_code == 403


def test_resume_calls_the_context_and_replies_204(tmp_path):
    calls = []
    app = app_for(tmp_path, resume=lambda: calls.append(True))
    response = pc_post(app.test_client(), "/api/resume", headers=MARKED)
    assert response.status_code == 204
    assert calls == [True]


def test_resume_refuses_a_phone_and_needs_the_marker(client):
    assert pc_post(client, "/api/resume").status_code == 403
    assert phone_post(client, "/api/resume", headers=MARKED).status_code == 403


# --- Set up: what a fresh PC lacks, and the button that installs it ---------------------------------

SETUP_STATUS = {"running": False, "needed": True, "download_bytes": 300_000_000, "progress": None, "error": None,
                "steps": [{"id": "csda", "name": "csda", "state": "needed"}]}
RENDERING = "A Reel is rendering. Set up once it is done."


def test_api_setup_is_what_setup_says_and_is_not_stored(tmp_path):
    response = pc_get(app_for(tmp_path, setup_status=lambda: SETUP_STATUS).test_client(), "/api/setup")

    assert (response.status_code, response.get_json()) == (200, SETUP_STATUS)
    assert response.headers["Cache-Control"] == "no-store"


def test_api_setup_needs_nothing_when_no_setup_is_wired_in(client):
    data = pc_get(client, "/api/setup").get_json()

    assert (data["running"], data["needed"], data["steps"], data["error"]) == (False, False, [], None)


def test_posting_to_api_setup_starts_it_and_replies_202(tmp_path):
    calls = []
    app = app_for(tmp_path, start_setup=lambda: calls.append(True))

    assert pc_post(app.test_client(), "/api/setup", headers=MARKED).status_code == 202
    assert calls == [True]


def test_a_setup_that_is_refused_is_a_409_that_says_why(tmp_path):
    app = app_for(tmp_path, start_setup=lambda: RENDERING)

    response = pc_post(app.test_client(), "/api/setup", headers=MARKED)

    assert (response.status_code, response.get_json()) == (409, {"error": RENDERING})


def test_api_setup_refuses_a_phone_and_needs_the_marker(tmp_path):
    calls = []
    client = app_for(tmp_path, start_setup=lambda: calls.append(True)).test_client()

    assert pc_post(client, "/api/setup").status_code == 403
    assert phone_post(client, "/api/setup", headers=MARKED).status_code == 403
    assert phone_get(client, "/api/setup").status_code == 403
    assert calls == []


def test_the_status_page_carries_the_set_up_card(client):
    page = pc_get(client, "/status").get_data(as_text=True)

    assert 'id="setup"' in page and 'api("api/setup"' in page


# --- Update: a newer CS2 Clipper, its notes, and the button that installs it ---------------------------

UPDATE_STATUS = {"current": "0.2.0", "running": False, "stage": None, "progress": None, "error": None,
                 "available": {"version": "0.3.0", "notes": "<h3>Fixed</h3>", "page": "https://github.com/x",
                               "bytes": 27_000_000}}


def test_api_update_is_what_the_updates_say_and_is_not_stored(tmp_path):
    response = pc_get(app_for(tmp_path, update_status=lambda: UPDATE_STATUS).test_client(), "/api/update")

    assert (response.status_code, response.get_json()) == (200, UPDATE_STATUS)
    assert response.headers["Cache-Control"] == "no-store"


def test_api_update_offers_nothing_when_no_updates_are_wired_in(client):
    data = pc_get(client, "/api/update").get_json()

    assert (data["available"], data["running"], data["error"]) == (None, False, None)


def test_posting_to_api_update_starts_it_and_replies_202(tmp_path):
    calls = []
    app = app_for(tmp_path, start_update=lambda: calls.append(True))

    assert pc_post(app.test_client(), "/api/update", headers=MARKED).status_code == 202
    assert calls == [True]


def test_an_update_that_is_refused_is_a_409_that_says_why(tmp_path):
    refusal = "A Reel is rendering. Update once it is done."
    app = app_for(tmp_path, start_update=lambda: refusal)

    response = pc_post(app.test_client(), "/api/update", headers=MARKED)

    assert (response.status_code, response.get_json()) == (409, {"error": refusal})


def test_api_update_refuses_a_phone_and_needs_the_marker(tmp_path):
    calls = []
    client = app_for(tmp_path, start_update=lambda: calls.append(True)).test_client()

    assert pc_post(client, "/api/update").status_code == 403
    assert phone_post(client, "/api/update", headers=MARKED).status_code == 403
    assert phone_get(client, "/api/update").status_code == 403
    assert calls == []


def test_the_status_page_carries_the_update_card(client):
    page = pc_get(client, "/status").get_data(as_text=True)

    assert 'id="update"' in page and 'api("api/update"' in page and 'id="update-notes"' in page


def test_the_status_page_deletes_a_demo_after_asking(client):
    page = pc_get(client, "/status").get_data(as_text=True)

    assert "data-delete=" in page and "data-delete-yes=" in page and "data-delete-no=" in page
    assert "api(`api/demos/${id}/delete`" in page


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


@pytest.mark.parametrize("path", ["/", "/status"])
def test_the_home_routes_refuse_a_phone(client, path):
    assert phone_get(client, path).status_code == 403


def test_api_status_survives_a_check_that_raises_and_says_so_in_one_row(tmp_path, caplog):
    def broken_checks():
        raise OSError("the clips drive is gone")

    app = app_for(tmp_path, checks=broken_checks)

    with caplog.at_level(logging.ERROR, logger="clipper.web"):
        response = pc_get(app.test_client(), "/api/status")

    assert response.status_code == 200
    data = response.get_json()
    assert data["checks"] == [{"name": "Checks", "ok": False,
                               "detail": "Couldn't run the checks: the clips drive is gone", "hint": ""}]
    assert data["summary"] == "Idle"                     # the rest of the page's data is still there
    assert "could not run the checks" in caplog.text


def test_retry_that_loses_a_race_with_the_worker_is_409_not_500(tmp_path, index, monkeypatch):
    demo_id = _add_demo(index)
    index.fail(demo_id, "boom")

    def raced(self, demo_id):     # the Demo stopped being failed between the route looking and retry's transaction
        raise ValueError(f"demo {demo_id} has not failed")

    monkeypatch.setattr(Index, "retry", raced)

    response = pc_post(app_for(tmp_path).test_client(), f"/api/demos/{demo_id}/retry", headers=MARKED)

    assert response.status_code == 409
    assert response.get_json() == {"error": f"demo {demo_id} has not failed"}


# --- Reels page (Task 12) -------------------------------------------------------------------------

REEL_MATCH = MatchInfo(checksum="c0ffee00c0ffee00", map_name="de_mirage",
                       played_at=datetime(2026, 9, 27, 18, 0, tzinfo=timezone.utc),
                       team_score=13, opponent_score=5)


def _reels_index(index, match: MatchInfo = REEL_MATCH) -> int:
    """A done Demo for `match` with one selected Highlight (round 8, 4K). Returns its highlight id;
    the caller adds the Reel(s) it needs."""
    index.save_match(match)
    demo_id = _add_demo(index, name="1-reel.dem.zst", sha="b" * 64)
    index.advance(demo_id, "done", match_checksum=match.checksum)
    hl = Highlight(round=8, type="4K", score=40, reasons=("4k",), frag_ticks=(100,),
                   round_start_tick=0, round_end_tick=5000)
    index.save_highlights(match.checksum, [hl], {8})
    return index.selected_highlights(match.checksum)[0]["id"]


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


def test_a_reel_keeps_playing_after_the_clips_folder_moves(tmp_path):
    """Paths are kept relative to the clips folder, so repointing it (the `data_root` setting) cannot
    strand every Reel the way absolute paths did -- the bug this replaced."""
    clips = tmp_path / "clips"
    video = clips / "library" / "videos" / "abc" / "r8-player.mp4"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"0123456789")
    app, store = _settings_app(tmp_path)
    assert store.save({"data_root": str(clips)}) == {}
    index = Index(tmp_path / "clipper.sqlite")
    try:
        highlight_id = _reels_index(index)
        index.save_reel(highlight_id, "player", store.current().config.store_path(video), 12.5)
        reel_id = index.match_reels(REEL_MATCH.checksum)[0]["player_reel_id"]
    finally:
        index.close()

    client = app.test_client()
    assert pc_get(client, f"/reels/{reel_id}.mp4").status_code == 200

    clips.rename(tmp_path / "moved")
    assert store.save({"data_root": str(tmp_path / "moved")}) == {}

    assert pc_get(client, f"/reels/{reel_id}.mp4").status_code == 200


class FakeFfmpeg:
    """Stands in for `subprocess.run` on the frame route: records the command and writes `frame` to its
    output file (the last argument), or nothing when `frame` is None, which is what ffmpeg does when a
    Reel is shorter than the seek."""

    def __init__(self, frame: bytes | None = b"\xff\xd8 a frame"):
        self.commands = []
        self.frame = frame

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if self.frame is not None:
            Path(command[-1]).write_bytes(self.frame)
        return subprocess.CompletedProcess(command, 0, b"", b"")


def _reel_with_video(tmp_path, index, name="clip.mp4") -> tuple[int, Path]:
    highlight_id = _reels_index(index)
    video_path = tmp_path / name
    video_path.write_bytes(b"not really a video")
    index.save_reel(highlight_id, "player", video_path, 12.5)
    return index.match_reels(REEL_MATCH.checksum)[0]["player_reel_id"], video_path


def test_reel_frame_is_cut_once_and_kept_beside_the_reel(tmp_path, index, monkeypatch):
    reel_id, video_path = _reel_with_video(tmp_path, index)
    ffmpeg = FakeFfmpeg()
    monkeypatch.setattr(subprocess, "run", ffmpeg)
    client = app_for(tmp_path, load_settings=lambda: Loaded(Config(ffmpeg="C:/tools/ffmpeg.exe"), ())).test_client()

    first = pc_get(client, f"/reels/{reel_id}.jpg")
    second = pc_get(client, f"/reels/{reel_id}.jpg")

    assert (first.status_code, first.mimetype, first.data) == (200, "image/jpeg", ffmpeg.frame)
    assert second.data == ffmpeg.frame
    assert len(ffmpeg.commands) == 1                    # the second request found the frame already cut
    command = ffmpeg.commands[0]
    assert command[0] == "C:/tools/ffmpeg.exe"
    assert command[command.index("-ss") + 1] == "3"
    assert command[command.index("-i") + 1] == str(video_path)
    assert [p.name for p in tmp_path.glob("*.jpg")] == ["clip.jpg"]      # and no half-written file


def test_a_reel_written_after_its_frame_gets_a_new_frame(tmp_path, index, monkeypatch):
    reel_id, video_path = _reel_with_video(tmp_path, index)
    video_path.with_suffix(".jpg").write_bytes(b"the old frame")
    os.utime(video_path.with_suffix(".jpg"), (1_000_000_000, 1_000_000_000))     # long before the Reel
    ffmpeg = FakeFfmpeg(b"\xff\xd8 the new frame")
    monkeypatch.setattr(subprocess, "run", ffmpeg)

    response = pc_get(app_for(tmp_path).test_client(), f"/reels/{reel_id}.jpg")

    assert response.data == ffmpeg.frame
    assert len(ffmpeg.commands) == 1


def test_reel_frame_404_when_ffmpeg_cuts_nothing(tmp_path, index, monkeypatch):
    reel_id, _ = _reel_with_video(tmp_path, index)
    monkeypatch.setattr(subprocess, "run", FakeFfmpeg(frame=None))

    response = pc_get(app_for(tmp_path).test_client(), f"/reels/{reel_id}.jpg")

    assert response.status_code == 404
    assert list(tmp_path.glob("*.jpg")) == []


def test_reel_frame_404_when_ffmpeg_cannot_start(tmp_path, index, monkeypatch):
    reel_id, _ = _reel_with_video(tmp_path, index)

    def missing(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(subprocess, "run", missing)

    assert pc_get(app_for(tmp_path).test_client(), f"/reels/{reel_id}.jpg").status_code == 404


def test_reel_frame_404_and_nothing_left_behind_when_ffmpeg_times_out(tmp_path, index, monkeypatch):
    reel_id, _ = _reel_with_video(tmp_path, index)

    def hangs(command, **kwargs):
        Path(command[-1]).write_bytes(b"half")      # what a killed ffmpeg leaves behind
        raise subprocess.TimeoutExpired(command, 30)

    monkeypatch.setattr(subprocess, "run", hangs)

    response = pc_get(app_for(tmp_path).test_client(), f"/reels/{reel_id}.jpg")

    assert response.status_code == 404
    assert list(tmp_path.glob("*.jpg")) == []


def test_reel_frame_404_for_an_unknown_reel_or_a_missing_file_without_running_ffmpeg(tmp_path, index, monkeypatch):
    highlight_id = _reels_index(index)
    index.save_reel(highlight_id, "player", tmp_path / "gone.mp4", 12.5)       # never written to disk
    reel_id = index.match_reels(REEL_MATCH.checksum)[0]["player_reel_id"]
    ffmpeg = FakeFfmpeg()
    monkeypatch.setattr(subprocess, "run", ffmpeg)
    client = app_for(tmp_path).test_client()

    assert pc_get(client, "/reels/999999.jpg").status_code == 404
    assert pc_get(client, f"/reels/{reel_id}.jpg").status_code == 404
    assert ffmpeg.commands == []


def test_reel_frame_refuses_a_phone(tmp_path, index):
    reel_id, _ = _reel_with_video(tmp_path, index)

    response = phone_get(app_for(tmp_path).test_client(), f"/reels/{reel_id}.jpg")

    assert response.status_code == 403


@pytest.mark.ffmpeg
def test_reel_frame_is_a_real_jpeg_from_the_real_ffmpeg(tmp_path, index):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH")
    reel_id, video_path = _reel_with_video(tmp_path, index)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=320x240:r=30:d=5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video_path)], check=True)

    response = pc_get(app_for(tmp_path).test_client(), f"/reels/{reel_id}.jpg")

    assert response.status_code == 200
    assert response.mimetype == "image/jpeg"
    assert response.data[:2] == b"\xff\xd8"
    assert [p.name for p in tmp_path.glob("*.jpg")] == ["clip.jpg"]


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


def test_open_folder_404_when_the_folder_cannot_be_opened(tmp_path, index):
    highlight_id = _reels_index(index)
    index.save_reel(highlight_id, "player", tmp_path / "videos" / "clip.mp4", 1.0)   # its folder is gone

    def gone(folder):
        raise FileNotFoundError(folder)

    app = app_for(tmp_path, open_folder=gone)

    response = pc_post(app.test_client(), f"/api/reels/{REEL_MATCH.checksum}/open-folder", headers=MARKED)

    assert response.status_code == 404


def test_open_folder_404_when_the_match_has_no_reels(tmp_path):
    response = pc_post(app_for(tmp_path).test_client(), f"/api/reels/{'a' * 16}/open-folder", headers=MARKED)
    assert response.status_code == 404


def test_open_folder_takes_a_checksum_without_its_leading_zeros(tmp_path, index):
    # csda writes a match's checksum as hex without its leading zeros: some are shorter than 16 digits.
    match = replace(REEL_MATCH, checksum="ffee00c0ffee00")
    highlight_id = _reels_index(index, match)
    index.save_reel(highlight_id, "player", tmp_path / "videos" / "clip.mp4", 1.0)
    calls = []
    app = app_for(tmp_path, open_folder=lambda folder: calls.append(folder))

    response = pc_post(app.test_client(), f"/api/reels/{match.checksum}/open-folder", headers=MARKED)

    assert response.status_code == 204
    assert calls == [tmp_path / "videos"]


@pytest.mark.parametrize("checksum", ["not-hex", "a" * 17, "AEA4E59CCFC6C962"])
def test_open_folder_404_for_a_malformed_checksum(tmp_path, checksum):
    response = pc_post(app_for(tmp_path).test_client(), f"/api/reels/{checksum}/open-folder", headers=MARKED)
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


def _stored_blob(tmp_path) -> str:
    return json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))[STORED_KEY]


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
    assert data["secrets_set"] == {"faceit_api_key": False, "faceit_client_secret": False}
    assert data["warnings"] == []
    assert data["port_in_use"] == 8765


def test_api_settings_get_with_a_saved_key_never_echoes_it_or_its_blob(tmp_path):
    app, store = _settings_app(tmp_path)
    assert store.save({KEY_FIELD: "super-secret-value"}) == {}
    blob = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))[STORED_KEY]

    response = pc_get(app.test_client(), "/api/settings")
    data = response.get_json()
    text = response.get_data(as_text=True)

    assert data["secrets_set"]["faceit_api_key"] is True
    assert "super-secret-value" not in text
    assert blob not in text


@pytest.mark.parametrize(("saved", "secrets_set"), [
    ({KEY_FIELD: "the-app-key"}, {"faceit_api_key": True, "faceit_client_secret": False}),
    ({SECRET_FIELD: "the-client-secret"}, {"faceit_api_key": False, "faceit_client_secret": True}),
])
def test_api_settings_get_says_which_secret_is_saved_each_on_its_own(tmp_path, saved, secrets_set):
    # The page's "is saved" and Remove are per row and come from this. One flag for both made the
    # client-secret row claim a secret nobody saved, and its Remove delete the API key as well.
    app, store = _settings_app(tmp_path)
    assert store.save(saved) == {}

    data = pc_get(app.test_client(), "/api/settings").get_json()

    assert data["secrets_set"] == secrets_set


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


def test_put_a_bad_key_is_400_and_neither_the_typed_value_nor_the_stored_blob_comes_back(tmp_path):
    app, store = _settings_app(tmp_path)
    assert store.save({KEY_FIELD: "the-original-key"}) == {}
    blob = _stored_blob(tmp_path)

    response = pc_put(app.test_client(), "/api/settings", headers=MARKED,
                      json={KEY_FIELD: "a typed key with spaces"})

    assert response.status_code == 400
    assert set(response.get_json()["errors"]) == {KEY_FIELD}
    text = response.get_data(as_text=True)
    assert "a typed key with spaces" not in text
    assert "the-original-key" not in text
    assert blob not in text
    assert _stored_blob(tmp_path) == blob                # and the stored key is untouched


def test_put_a_blank_key_keeps_the_stored_one(tmp_path):
    app, store = _settings_app(tmp_path)
    assert store.save({KEY_FIELD: "the-original-key"}) == {}
    blob = _stored_blob(tmp_path)
    client = app.test_client()

    response = pc_put(client, "/api/settings", headers=MARKED, json={KEY_FIELD: "", "top_n": 9})

    assert response.status_code == 200
    assert pc_get(client, "/api/settings").get_json()["secrets_set"]["faceit_api_key"] is True
    assert _stored_blob(tmp_path) == blob


def test_put_bad_fields_leave_an_existing_settings_file_byte_for_byte_unchanged(tmp_path):
    app, store = _settings_app(tmp_path)
    assert store.save({"top_n": 9, "faceit_nickname": "someone"}) == {}
    path = tmp_path / "settings.json"
    before = path.read_bytes()

    response = pc_put(app.test_client(), "/api/settings", headers=MARKED,
                      json={"top_n": 999, "aspect_ratio": "21:9", "faceit_nickname": "changed"})

    assert response.status_code == 400
    assert set(response.get_json()["errors"]) == {"top_n", "aspect_ratio"}   # the good field is not saved either
    assert path.read_bytes() == before


# --- Sign in with FACEIT (Settings) -----------------------------------------------------------------

FACEIT_LOGIN = "https://accounts.faceit.com?client_id=c&state=s"


def test_api_settings_carries_the_faceit_login_url(tmp_path):
    app, _store = _settings_app(tmp_path, faceit_login_url=lambda: FACEIT_LOGIN)
    assert pc_get(app.test_client(), "/api/settings").get_json()["faceit_login_url"] == FACEIT_LOGIN


def test_api_settings_says_no_sign_in_is_set_up_by_default(tmp_path):
    app, _store = _settings_app(tmp_path)
    assert pc_get(app.test_client(), "/api/settings").get_json()["faceit_login_url"] == ""


def test_faceit_session_passes_the_code_and_state_through_and_returns_the_nickname(tmp_path):
    calls = []
    app, _store = _settings_app(
        tmp_path, faceit_sign_in=lambda code, state: calls.append((code, state)) or "cheesebagga")

    response = pc_post(app.test_client(), "/api/faceit/session", headers=MARKED,
                       json={"code": "a-code", "state": "a-state"})

    assert response.status_code == 200
    assert response.get_json() == {"nickname": "cheesebagga"}
    assert calls == [("a-code", "a-state")]


def test_faceit_session_without_a_code_is_400(tmp_path):
    app, _store = _settings_app(tmp_path)
    assert pc_post(app.test_client(), "/api/faceit/session", headers=MARKED, json={}).status_code == 400


def test_faceit_session_reports_a_refused_sign_in_as_400_not_500(tmp_path):
    def refuse(code, state):
        raise OAuthError("that sign-in did not come back from this app; try again")

    app, _store = _settings_app(tmp_path, faceit_sign_in=refuse)

    response = pc_post(app.test_client(), "/api/faceit/session", headers=MARKED, json={"code": "c"})

    assert response.status_code == 400
    assert "did not come back" in response.get_json()["error"]


def test_faceit_session_refuses_a_phone_and_needs_the_marker(tmp_path):
    app, _store = _settings_app(tmp_path)
    client = app.test_client()
    assert pc_post(client, "/api/faceit/session", json={"code": "c"}).status_code == 403
    assert phone_post(client, "/api/faceit/session", headers=MARKED, json={"code": "c"}).status_code == 403


# --- Look up my FACEIT name (Settings) ---------------------------------------------------------------


def test_faceit_lookup_passes_the_nickname_through_and_returns_the_player(tmp_path):
    calls = []
    app, _store = _settings_app(tmp_path, faceit_lookup=lambda nickname: calls.append(nickname) or
                                {"nickname": "cheesebagga", "steamid": "76561198192858303"})

    response = pc_post(app.test_client(), "/api/faceit/lookup", headers=MARKED, json={"nickname": "cheesebagga"})

    assert response.status_code == 200
    assert response.get_json() == {"nickname": "cheesebagga", "steamid": "76561198192858303"}
    assert calls == ["cheesebagga"]


def test_faceit_lookup_without_a_nickname_is_400(tmp_path):
    app, _store = _settings_app(tmp_path)
    client = app.test_client()
    assert pc_post(client, "/api/faceit/lookup", headers=MARKED, json={}).status_code == 400
    assert pc_post(client, "/api/faceit/lookup", headers=MARKED, json={"nickname": "   "}).status_code == 400


def test_faceit_lookup_reports_a_faceit_error_as_400_not_500(tmp_path):
    def missing(nickname):
        raise FaceitError("FACEIT has no player called nope", 404)

    app, _store = _settings_app(tmp_path, faceit_lookup=missing)

    response = pc_post(app.test_client(), "/api/faceit/lookup", headers=MARKED, json={"nickname": "nope"})

    assert response.status_code == 400
    assert "no player called nope" in response.get_json()["error"]


def test_faceit_lookup_refuses_a_phone_and_needs_the_marker(tmp_path):
    app, _store = _settings_app(tmp_path)
    client = app.test_client()
    assert pc_post(client, "/api/faceit/lookup", json={"nickname": "x"}).status_code == 403
    assert phone_post(client, "/api/faceit/lookup", headers=MARKED, json={"nickname": "x"}).status_code == 403


# --- every route Tasks 10-14 added is PC-only: a foreign Host is refused on all of them -------------

PC_ONLY_ROUTES = [
    ("GET", "/"), ("GET", "/status"), ("GET", "/api/status"), ("GET", "/api/summary"), ("GET", "/api/log"),
    ("POST", "/api/demos/1/retry"), ("POST", "/api/pause"), ("POST", "/api/resume"), ("POST", "/api/quit"),
    ("GET", "/api/setup"), ("POST", "/api/setup"), ("GET", "/api/update"), ("POST", "/api/update"),
    ("GET", "/reels"), ("GET", "/api/reels"), ("GET", "/reels/1.mp4"), ("GET", "/reels/1.jpg"),
    ("POST", f"/api/reels/{'a' * 16}/open-folder"),
    ("GET", "/settings"), ("GET", "/api/settings"), ("PUT", "/api/settings"),
    ("POST", "/api/faceit/session"), ("POST", "/api/faceit/lookup"),
    ("GET", "/api/window"), ("POST", "/api/window"),
]


@pytest.mark.parametrize(("method", "path"), PC_ONLY_ROUTES)
def test_a_pc_only_route_refuses_a_foreign_host(client, method, path):
    headers = {"Host": "evil.example:8765", **MARKED}     # marked, so only the Host can be what refuses
    assert client.open(path, method=method, base_url=PC, headers=headers).status_code == 403
