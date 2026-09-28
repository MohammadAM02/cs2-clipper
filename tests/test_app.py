"""Tests for clipper.app: start-up order, the worker thread, quit, headless run (spec: How the app
runs; The terminal; When something goes wrong; Testing).

Fakes and tmp folders throughout: no real csdm, CS2, Postgres or network. `step`'s tests use a fake
`problems` and a fake `build` so a "clear" pass never touches build_worker's real subsystems."""
from __future__ import annotations

import logging
import socket
from dataclasses import dataclass, field

import pytest

from clipper import paths, web
from clipper.app import AlreadyRunning, App, gate_reasons, run_headless, single_instance, start_match_alerts
from clipper.config import Config
from clipper.index import Index
from clipper.settings import SettingsStore
from clipper.state import AppState, Rendering
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
    calls: list = field(default_factory=list)      # (cfg, page_url, pages_off)
    workers: list = field(default_factory=list)

    def __call__(self, cfg, index, *, state, stop, page_url, pages_off):
        worker = FakeWorker()
        self.calls.append((cfg, page_url, pages_off))
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


# --- run_headless --------------------------------------------------------------------------------


def test_run_headless_returns_0_with_a_message_when_the_lock_is_already_held(capsys):
    with single_instance(paths.lock_file()):
        assert run_headless() == 0
    assert "already running" in capsys.readouterr().err
