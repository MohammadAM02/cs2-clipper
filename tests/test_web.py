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

from clipper.index import Index
from clipper.model import FaceitStats
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


def phone_get(client, path, **kw):
    return client.get(path, base_url=LAN, environ_base=PHONE_ENVIRON, **kw)


def phone_post(client, path, **kw):
    return client.post(path, base_url=LAN, environ_base=PHONE_ENVIRON, **kw)


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
    for path in ("/", "/demos/../clipper.sqlite", "/demos.json/x"):
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
