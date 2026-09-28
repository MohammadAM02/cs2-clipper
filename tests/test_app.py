"""Tests for clipper.app: start-up order, the worker thread, quit, headless run (spec: How the app
runs; The terminal; When something goes wrong; Testing).

Fakes and tmp folders throughout: no real csdm, CS2, Postgres or network. `step`'s tests use a fake
`problems` and a fake `build` so a "clear" pass never touches build_worker's real subsystems."""
from __future__ import annotations

import logging
import socket
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from clipper import paths, web
from clipper.app import (
    PAGES, AlreadyRunning, App, find_running, gate_reasons, hand_over, run_headless, single_instance,
    start_match_alerts,
)
from clipper.config import Config
from clipper.index import Index
from clipper.settings import SettingsStore
from clipper.state import AppState, Rendering
from clipper.web import WebContext, WebServer, create_app
from clipper.worker import StopRequest


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


# --- moved from tests/test_cli.py: single_instance ------------------------------------------------


def test_only_one_instance_can_hold_the_lock(tmp_path):
    lock = tmp_path / "clipper.lock"
    with single_instance(lock):
        with pytest.raises(AlreadyRunning):
            with single_instance(lock):
                pass
    with single_instance(lock):   # released again
        pass


# --- moved from tests/test_cli.py, adapted: start_match_alerts -------------------------------------


def test_match_alerts_stay_off_without_the_setting_or_the_key(tmp_path):
    index = Index(tmp_path / "clipper.sqlite")
    folders = dict(downloads_dir=tmp_path, data_root=tmp_path / "clips", index_path=tmp_path / "clipper.sqlite")
    try:
        off = start_match_alerts(Config(**folders, match_alerts=False), index, None, page_url=None, pages_off=None)
        assert off is None
        assert index.get_flag("alerts_status") == "off: switched off in Settings"

        no_key = start_match_alerts(Config(**folders), index, None, page_url=None, pages_off=None)
        assert no_key is None
        assert index.get_flag("alerts_status") == "off: set your FACEIT nickname and API key in Settings"
    finally:
        index.close()


def test_match_alerts_stay_off_when_the_page_could_not_start(tmp_path):
    index = Index(tmp_path / "clipper.sqlite")
    folders = dict(downloads_dir=tmp_path, data_root=tmp_path / "clips", index_path=tmp_path / "clipper.sqlite",
                   faceit_nickname="someone", faceit_api_key_protected="a-protected-blob")
    try:
        result = start_match_alerts(Config(**folders), index, None, page_url=None,
                                    pages_off="no free port in 8765–8774")
        assert result is None
        assert index.get_flag("alerts_status") == (
            "off: the Demos to grab page could not start: no free port in 8765–8774")
    finally:
        index.close()


# --- gate_reasons: only the new fallback branch needs a test (the Gate itself is tested elsewhere) --


def test_gate_reasons_names_the_missing_clips_folder_when_none_is_set():
    assert gate_reasons(Config(), probe=None) == ("no clips folder is set",)


# --- App.step: fakes for problems and build --------------------------------------------------------


class FakeWorker:
    def __init__(self):
        self.ticks = 0
        self.raise_next = False

    def tick(self) -> None:
        self.ticks += 1
        if self.raise_next:
            self.raise_next = False
            raise RuntimeError("boom")


@dataclass
class FakeBuild:
    calls: list = field(default_factory=list)      # (cfg, page_url, pages_off), one per attempt
    workers: list = field(default_factory=list)
    errors: list = field(default_factory=list)     # one is raised per attempt while any are left

    def __call__(self, cfg, index, *, state, stop, page_url, pages_off):
        self.calls.append((cfg, page_url, pages_off))
        if self.errors:
            raise self.errors.pop(0)
        worker = FakeWorker()
        self.workers.append(worker)
        return worker


@dataclass
class FakeProblems:
    problems: list = field(default_factory=list)
    calls: int = 0

    def __call__(self, cfg) -> list:
        self.calls += 1
        return list(self.problems)


class FakeClock:
    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class World:
    store: SettingsStore
    index: Index
    state: AppState
    stop: StopRequest
    clock: FakeClock
    problems: FakeProblems
    build: FakeBuild
    app: App


@pytest.fixture
def world(tmp_path):
    (tmp_path / "clips").mkdir()
    store = SettingsStore(tmp_path / "settings.json")
    assert store.save({"data_root": str(tmp_path / "clips")}) == {}
    index = Index(tmp_path / "clipper.sqlite")
    state, stop = AppState(), StopRequest()
    clock = FakeClock()
    problems = FakeProblems()
    build = FakeBuild()
    app = App(store, index_path=tmp_path / "clipper.sqlite", state=state, stop=stop,
             problems=problems, build=build, recheck_seconds=60.0, clock=clock)
    yield World(store, index, state, stop, clock, problems, build, app)
    index.close()


def test_problems_keep_the_worker_from_being_built_or_ticked_and_are_published(world):
    world.problems.problems = ["Postgres won't start"]
    delay = world.app.step(world.index)
    assert delay == world.store.current().config.poll_seconds
    assert world.build.calls == []
    assert world.state.snapshot().problems == ("Postgres won't start",)


def test_a_settings_change_reevaluates_problems_at_once_then_builds_and_ticks_once_clear(world):
    world.problems.problems = ["Set your SteamID in Settings"]
    world.app.step(world.index)
    assert world.problems.calls == 1
    assert world.build.calls == []

    world.problems.problems = []                                          # the fix
    assert world.store.save({"subject_steamid": "76561198000000001"}) == {}
    world.app.step(world.index)
    assert world.problems.calls == 2             # re-evaluated at once, not after recheck_seconds
    assert len(world.build.calls) == 1
    assert world.build.workers[0].ticks == 1
    assert world.state.snapshot().problems == ()


def test_problems_are_rechecked_after_recheck_seconds_without_a_settings_change(world):
    world.app.step(world.index)
    assert world.problems.calls == 1
    world.app.step(world.index)                  # same settings, well within recheck_seconds
    assert world.problems.calls == 1
    world.clock.advance(61.0)
    world.app.step(world.index)
    assert world.problems.calls == 2


def test_a_changed_setting_rebuilds_the_worker(world):
    world.app.step(world.index)
    assert len(world.build.calls) == 1
    first_worker = world.build.workers[0]

    assert world.store.save({"top_n": 7}) == {}
    world.app.step(world.index)
    assert len(world.build.calls) == 2
    assert world.build.workers[-1] is not first_worker


def test_a_tick_that_raises_is_logged_and_the_next_pass_still_ticks(world, caplog):
    world.app.step(world.index)
    worker = world.build.workers[0]
    worker.raise_next = True
    with caplog.at_level(logging.ERROR, logger="clipper.app"):
        world.app.step(world.index)
    assert "tick failed" in caplog.text

    world.app.step(world.index)
    assert worker.ticks == 3   # 1 ok, 1 raised (still counted as attempted), 1 ok again


def test_a_build_that_raises_publishes_a_problem_and_is_tried_again_after_the_recheck(world, caplog):
    world.build.errors.append(RuntimeError("csdm.exe is gone"))

    with caplog.at_level(logging.ERROR, logger="clipper.app"):
        delay = world.app.step(world.index)              # must not raise

    assert delay == world.store.current().config.poll_seconds
    assert "could not start the worker" in caplog.text
    assert world.state.snapshot().problems == ("Couldn't start the worker: csdm.exe is gone",)
    assert len(world.build.calls) == 1                   # it did try ...
    assert world.build.workers == []                     # ... and there is no worker

    world.app.step(world.index)                          # the published problem holds later passes off
    assert len(world.build.calls) == 1

    world.clock.advance(61.0)                            # the recheck replaces it and tries again
    world.app.step(world.index)
    assert len(world.build.calls) == 2
    assert world.build.workers[0].ticks == 1
    assert world.state.snapshot().problems == ()


def test_the_demos_renders_and_library_folders_are_created(world):
    world.app.step(world.index)
    cfg = world.store.current().config
    assert cfg.demos_dir.is_dir()
    assert cfg.renders_dir.is_dir()
    assert cfg.library_dir.is_dir()


# --- run_worker / wait ------------------------------------------------------------------------------


def test_run_worker_ends_when_quit_is_called_and_closes_its_index(world, monkeypatch):
    closed = []
    from clipper.index import Index as RealIndex

    class TrackingIndex(RealIndex):
        def close(self) -> None:
            closed.append(True)
            super().close()

    monkeypatch.setattr("clipper.app.Index", TrackingIndex)
    world.app.start_worker()
    world.app.quit("now")
    assert world.app.wait(timeout=5.0) is True
    assert closed == [True]


def test_the_worker_thread_outlives_a_pass_that_raises(world, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr("clipper.app.WORKER_ERROR_WAIT_SECONDS", 0.01)
    published = []                     # the problems on show just before each start-up check
    second_check = threading.Event()

    def problems(cfg):
        published.append(world.state.snapshot().problems)
        if len(published) == 1:
            raise RuntimeError("pg_ctl vanished")      # the start-up check itself raises, once
        second_check.set()
        return []

    app = App(world.store, index_path=tmp_path / "clipper.sqlite", state=world.state, stop=world.stop,
              problems=problems, build=world.build, recheck_seconds=60.0, clock=world.clock)

    with caplog.at_level(logging.ERROR, logger="clipper.app"):
        app.start_worker()
        assert second_check.wait(timeout=5.0)            # a later pass ran, on the same thread
        app.quit("now")
        assert app.wait(timeout=5.0) is True             # and it ended only because it was asked to

    assert published[1] == ("The worker hit an error: pg_ctl vanished",)
    assert "the worker hit an error" in caplog.text


# --- quit --------------------------------------------------------------------------------------------


def test_quit_idle_returns_now(world):
    assert world.app.quit() == "now"
    assert world.state.snapshot().quitting == "now"


def test_quit_while_rendering_asks_and_requests_nothing(world):
    world.state.set_rendering(Rendering("de_mirage", "player", 0.0))
    assert world.app.quit() == "ask"
    assert world.stop.mode is None
    assert world.state.snapshot().quitting is None


def test_quit_after_render_then_now_ends_up_now(world):
    assert world.app.quit("after_render") == "after_render"
    assert world.state.snapshot().quitting == "after_render"
    assert world.app.quit("now") == "now"
    assert world.state.snapshot().quitting == "now"


# --- pause / resume: a short-lived Index plus the shared state (Task 11) ---------------------------


def test_pause_marks_the_index_and_the_state(world):
    world.app.pause()
    assert world.index.paused_by() == "you"
    assert world.state.snapshot().paused_by == "you"


def test_resume_clears_the_index_and_the_state(world):
    world.index.pause("you")
    world.state.set_paused_by("you")

    world.app.resume()

    assert world.index.paused_by() is None
    assert world.state.snapshot().paused_by is None


# --- checks: cached for 15 s (Task 11) ---------------------------------------------------------------


def test_checks_is_cached_and_recomputed_after_15_seconds(world, monkeypatch):
    calls = []

    def fake_run_checks(cfg, *, alerts_status, releases):
        calls.append(releases)
        return []

    monkeypatch.setattr("clipper.app.checks.run_checks", fake_run_checks)

    world.app.checks()
    world.app.checks()
    assert len(calls) == 1
    assert calls[0] is world.app.releases

    world.clock.advance(15.0)
    world.app.checks()
    assert len(calls) == 2


def test_checks_reads_the_alerts_status_flag_from_the_index(world, monkeypatch):
    world.index.set_flag("alerts_status", "on")
    seen = []
    monkeypatch.setattr(
        "clipper.app.checks.run_checks",
        lambda cfg, *, alerts_status, releases: seen.append(alerts_status) or [],
    )

    world.app.checks()

    assert seen == ["on"]


# --- releases: a daemon thread that calls refresh_if_due at start (Task 11) -------------------------


def test_start_releases_calls_refresh_if_due_promptly(world):
    called = threading.Event()
    world.app.releases.refresh_if_due = called.set

    world.app.start_releases()

    assert called.wait(timeout=2.0)


# --- open_window: headless for now (Task 14 replaces it with the app window) -----------------------


def test_open_window_opens_the_browser_when_the_page_port_is_set(world, monkeypatch):
    opened = []
    monkeypatch.setattr("clipper.app.webbrowser.open", lambda url: opened.append(url))
    world.app.page_port = 8765
    world.app.open_window("/reels")
    assert opened == ["http://127.0.0.1:8765/reels"]


def test_open_window_does_nothing_without_a_page_port(world, monkeypatch):
    opened = []
    monkeypatch.setattr("clipper.app.webbrowser.open", lambda url: opened.append(url))
    world.app.page_port = None
    world.app.open_window("/status")
    assert opened == []


def test_pages_has_one_home():
    assert PAGES is web.PAGES      # what `/api/window` accepts and what cli.py's --open offers are one tuple


# --- start_web: every port taken -------------------------------------------------------------------


def test_start_web_with_every_port_taken_turns_pages_off_and_the_next_worker_gets_no_page_url(
    tmp_path, monkeypatch,
):
    port = _free_port()
    (tmp_path / "clips").mkdir()
    store = SettingsStore(tmp_path / "settings.json")
    assert store.save({"data_root": str(tmp_path / "clips"), "page_port": port}) == {}
    monkeypatch.setattr(web, "PORTS_TO_TRY", 1)

    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.bind(("0.0.0.0", port))
    blocker.listen(1)
    build = FakeBuild()
    state = AppState()
    index = Index(tmp_path / "clipper.sqlite")
    try:
        app = App(store, index_path=tmp_path / "clipper.sqlite", state=state, build=build,
                  problems=FakeProblems())
        app.start_web()
        assert app.page_port is None
        assert state.snapshot().pages_off is not None

        app.step(index)
        assert len(build.calls) == 1
        _, page_url, pages_off = build.calls[0]
        assert page_url is None
        assert pages_off == state.snapshot().pages_off
    finally:
        blocker.close()
        index.close()


# --- find_running / hand_over: against a real WebServer, and against nothing of ours --------------


def test_hand_over_finds_the_running_copy_and_the_page_arrives(tmp_path):
    calls = []
    ctx = WebContext(index_path=tmp_path / "clipper.sqlite", gate_reasons=lambda: (),
                     open_window=lambda page: calls.append(page))
    server = WebServer(create_app(ctx), 0, host="127.0.0.1", tries=1)
    server.start()
    try:
        assert hand_over("/reels", [server.port]) is True
        assert calls == ["/reels"]
        assert find_running([server.port]) == server.port
    finally:
        server.stop()


def test_hand_over_skips_a_foreign_server_that_answers_health_without_our_app_field():
    class ForeignHealth(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    foreign = HTTPServer(("127.0.0.1", 0), ForeignHealth)
    threading.Thread(target=foreign.serve_forever, daemon=True).start()
    try:
        assert hand_over("/reels", [foreign.server_address[1]]) is False
    finally:
        foreign.shutdown()
        foreign.server_close()


def test_hand_over_with_nothing_listening_returns_false():
    assert hand_over("/reels", [_free_port()]) is False


# --- run_headless --------------------------------------------------------------------------------


def test_run_headless_returns_0_with_a_message_when_the_lock_is_already_held(capsys):
    with single_instance(paths.lock_file()):
        assert run_headless() == 0
    assert "already running" in capsys.readouterr().err


def test_run_headless_hands_over_to_the_running_copy_and_passes_the_page(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("clipper.app.hand_over", lambda page, ports: calls.append(page) or True)
    with single_instance(paths.lock_file()):
        assert run_headless("/reels") == 0
    assert calls == ["/reels"]
    assert "already running" in capsys.readouterr().err


def test_run_headless_reports_when_the_running_copys_pages_do_not_answer(monkeypatch, capsys):
    monkeypatch.setattr("clipper.app.hand_over", lambda page, ports: False)
    with single_instance(paths.lock_file()):
        assert run_headless() == 0
    err = capsys.readouterr().err
    assert "already running" in err
    assert "do not answer" in err
