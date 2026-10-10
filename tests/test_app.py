"""Tests for clipper.app: start-up order, the worker thread, quit, headless run (spec: How the app
runs; The terminal; When something goes wrong; Testing).

Fakes and tmp folders throughout: no real csda, HLAE, CS2 or network. `step`'s tests use a fake
`problems` and a fake `build` so a "clear" pass never touches build_worker's real subsystems."""
from __future__ import annotations

import logging
import socket
import threading
import types
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from clipper import checks, cs2_paths, hlae_render, packaged, paths, update, web
from clipper.app import (
    PAGES, AlreadyRunning, App, ask_to_quit, build_worker, find_running, gate_reasons, hand_over, quit_running, run,
    run_headless, single_instance, start_match_alerts,
)
from clipper.config import Config
from clipper.faceit import FaceitError, Player
from clipper.faceit_oauth import OAuthError
from clipper.index import Index
from clipper.render import RenderRequest, RenderResult
from clipper.settings import KEY_FIELD, SECRET_FIELD, SettingsStore
from clipper.state import AppState, Rendering
from clipper.web import WebContext, WebServer, create_app
from clipper.worker import DeleteRequest, StopRequest


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


# --- build_worker: what a Demo is analyzed and a Render Job recorded with ------------------------------


class Renderer:
    """Stands in for the HLAE renderer and for the search for cs2.exe (the real one reads the registry), and keeps
    what each was called with."""

    def __init__(self):
        self.result = RenderResult(ok=False, failure="from a fake renderer")
        self.calls: list[tuple] = []        # (request, keyword arguments)
        self.cs2_exes: list = []            # what the search finds, one answer per search

    def render(self, request, **kwargs):
        self.calls.append((request, kwargs))
        return self.result

    def find_cs2_exe(self):
        return self.cs2_exes.pop(0)


@pytest.fixture
def renderer(monkeypatch):
    renderer = Renderer()
    monkeypatch.setattr(hlae_render, "render", renderer.render)
    monkeypatch.setattr(cs2_paths, "find_cs2_exe", renderer.find_cs2_exe)
    return renderer


@pytest.fixture
def built(tmp_path, renderer):
    """`built(**settings)` is the Worker `build_worker` gives for a Config in `tmp_path`, with an empty tools
    folder and no match alerts. Nothing runs or looks at the PC."""
    indexes = []

    def build(**settings):
        cfg = Config(downloads_dir=tmp_path / "downloads", data_root=tmp_path / "clips",
                     index_path=tmp_path / "clipper.sqlite", tools_dir=tmp_path / "tools",
                     analyses_dir=tmp_path / "analyses", cs2_settings_dir=tmp_path / "cs2-settings",
                     match_alerts=False, **settings)
        indexes.append(Index(cfg.index_path))
        return build_worker(cfg, indexes[-1], state=AppState(), stop=StopRequest(), deletes=DeleteRequest(),
                            page_url=None, pages_off=None)

    yield build
    for index in indexes:
        index.close()


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def _render_request(tmp_path) -> RenderRequest:
    return RenderRequest(
        demo_path=tmp_path / "match.dem", outputs={"player": tmp_path / "out"}, rounds=(3, 12),
        log_path=tmp_path / "render.log", steamid="76561198192858303", padding_before_s=4.0, padding_after_s=2.0,
        checksum="aea4e59ccfc6c962",
    )


def test_demos_are_analyzed_by_the_csda_setup_installs_into_the_apps_analyses(built):
    worker = built()
    analyses = worker.services.facts
    assert (analyses.folder, analyses.csda_exe) == (worker.cfg.analyses_dir, worker.cfg.tools_dir / "csda" / "csda.exe")
    assert worker.services.analyze == analyses.analyze      # a method of the one instance the facts come from


def test_a_render_job_goes_to_hlae_with_what_it_drives_cs2_with(built, renderer, tmp_path):
    cs2_exe = tmp_path / "steam" / "game" / "bin" / "win64" / "cs2.exe"
    renderer.cs2_exes = [cs2_exe]
    hlae = _touch(tmp_path / "HLAE" / "HLAE.exe")
    ffmpeg = _touch(tmp_path / "ffmpeg" / "ffmpeg.exe")
    worker = built(hlae_exe=str(hlae), ffmpeg=str(ffmpeg), stall_seconds=90.0, launch_timeout_seconds=240.0)
    request, abort, progress = _render_request(tmp_path), lambda: False, lambda report: None
    assert worker.services.render(request, abort, progress) is renderer.result
    [(given, kwargs)] = renderer.calls
    assert given is request
    assert set(kwargs) == {"probe", "should_abort", "stall_seconds", "launch_timeout_seconds", "duration_of",
                           "load_inputs", "cs2_exe", "hlae_exe", "hlae_ffmpeg", "ffmpeg", "cs2_settings_dir",
                           "progress"}
    assert kwargs["should_abort"] is abort
    assert kwargs["progress"] is progress           # the Status page's progress is told the render's progress
    assert (kwargs["stall_seconds"], kwargs["launch_timeout_seconds"]) == (90.0, 240.0)
    assert kwargs["cs2_exe"] == cs2_exe
    assert kwargs["hlae_exe"] == hlae               # the setting's
    assert kwargs["hlae_ffmpeg"] == ffmpeg          # the FFmpeg HLAE records with is ours, found
    assert kwargs["ffmpeg"] == str(ffmpeg)          # and the mux's is the setting as it is
    assert kwargs["cs2_settings_dir"] == tmp_path / "cs2-settings"     # CS2's settings while it records


def test_hlae_and_ffmpeg_that_are_nowhere_reach_the_renderer_as_none(built, renderer, tmp_path):
    renderer.cs2_exes = [None]
    worker = built(ffmpeg=str(tmp_path / "no-ffmpeg" / "ffmpeg.exe"))      # and no HLAE in the tools folder
    worker.services.render(_render_request(tmp_path), lambda: False, lambda report: None)
    [(_, kwargs)] = renderer.calls
    assert (kwargs["cs2_exe"], kwargs["hlae_exe"], kwargs["hlae_ffmpeg"]) == (None, None, None)


def test_the_renderer_reads_its_plan_through_the_same_analyses_as_the_worker(built, renderer, tmp_path):
    renderer.cs2_exes = [None]
    worker = built()
    worker.services.render(_render_request(tmp_path), lambda: False, lambda report: None)
    [(_, kwargs)] = renderer.calls
    assert kwargs["load_inputs"] == worker.services.facts.render_inputs     # a method of the one instance


def test_cs2_and_hlae_are_looked_for_again_for_every_render_job(built, renderer, tmp_path):
    cs2_exe = tmp_path / "cs2.exe"
    renderer.cs2_exes = [None, cs2_exe]
    worker = built()
    worker.services.render(_render_request(tmp_path), lambda: False, lambda report: None)
    hlae = _touch(worker.cfg.tools_dir / "hlae" / "HLAE.exe")       # Setup installed it in between
    worker.services.render(_render_request(tmp_path), lambda: False, lambda report: None)
    assert [(kwargs["cs2_exe"], kwargs["hlae_exe"]) for _, kwargs in renderer.calls] == [(None, None), (cs2_exe, hlae)]


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
    deletes: list = field(default_factory=list)    # the DeleteRequest each attempt was given

    def __call__(self, cfg, index, *, state, stop, deletes, page_url, pages_off):
        self.calls.append((cfg, page_url, pages_off))
        self.deletes.append(deletes)
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


class FakeSetup:
    """Stands in for provision.Setup: it never looks at the PC and installs nothing. A run lasts from
    `start()` until the test calls `end()`."""

    def __init__(self, context, *, after):
        self.context, self.after = context, after
        self.running = False
        self.starts = 0

    def start(self) -> bool:
        if self.running:
            return False
        self.starts += 1
        self.running = True
        return True

    def status(self) -> dict:
        return {"running": self.running, "needed": True, "steps": [], "download_bytes": 0, "progress": None,
                "error": None}

    def end(self) -> None:
        self.running = False
        self.after()


class FakeUpdates:
    """Stands in for update.Updates: it never asks GitHub, downloads or installs anything. It keeps what the app
    gave it, and a click starts an update unless `busy()` says why not."""

    def __init__(self, **given):
        self.given = given
        self.starts = 0
        self.refreshes = 0

    def refresh_if_due(self) -> None:
        self.refreshes += 1

    def start(self) -> str | None:
        refused = self.given["busy"]()
        if refused:
            return refused
        self.starts += 1
        return None

    def status(self) -> dict:
        return {"current": self.given["current"], "available": None, "running": self.starts > 0, "stage": None,
                "progress": None, "error": None}


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

    @property
    def setup(self) -> FakeSetup:
        return self.app.setup

    @property
    def updates(self) -> FakeUpdates:
        return self.app.updates


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
             problems=problems, build=build, setup=FakeSetup, updates=FakeUpdates, recheck_seconds=60.0,
             clock=clock)
    yield World(store, index, state, stop, clock, problems, build, app)
    index.close()


def test_problems_keep_the_worker_from_being_built_or_ticked_and_are_published(world):
    world.problems.problems = ["csda was not found"]
    delay = world.app.step(world.index)
    assert delay == world.store.current().config.poll_seconds
    assert world.build.calls == []
    assert world.state.snapshot().problems == ("csda was not found",)


def test_the_worker_is_built_with_the_delete_request_the_status_page_uses(world):
    world.app.step(world.index)
    assert len(world.build.deletes) == 1
    assert world.build.deletes[0] is world.app.deletes


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


def test_a_changed_hlae_path_is_in_the_config_of_the_rebuilt_worker(world, tmp_path):
    world.app.step(world.index)
    cfg = world.build.calls[-1][0]
    assert cfg.hlae_path == cfg.tools_dir / "hlae" / "HLAE.exe"        # blank: the one Setup installs

    hlae = _touch(tmp_path / "HLAE" / "HLAE.exe")
    assert world.store.save({"hlae_exe": str(hlae)}) == {}
    world.app.step(world.index)
    assert len(world.build.calls) == 2
    assert world.build.calls[-1][0].hlae_path == hlae


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
    world.build.errors.append(RuntimeError("csda.exe is gone"))

    with caplog.at_level(logging.ERROR, logger="clipper.app"):
        delay = world.app.step(world.index)              # must not raise

    assert delay == world.store.current().config.poll_seconds
    assert "could not start the worker" in caplog.text
    assert world.state.snapshot().problems == ("Couldn't start the worker: csda.exe is gone",)
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


# --- Set up: the worker stands still while it runs, and looks again when it ends -------------------


def test_the_worker_does_nothing_while_setup_runs(world):
    assert world.app.start_setup() is None

    delay = world.app.step(world.index)

    assert delay == world.store.current().config.poll_seconds
    assert (world.problems.calls, world.build.calls) == (0, [])     # nothing runs what setup is replacing


def test_problems_are_looked_at_again_as_soon_as_setup_ends(world):
    world.problems.problems = ["csda was not found"]
    world.app.step(world.index)
    world.app.start_setup()
    world.problems.problems = []                 # setup installs it, and changes no setting
    world.setup.end()

    world.app.step(world.index)

    assert world.problems.calls == 2             # at once, not after recheck_seconds
    assert world.state.snapshot().problems == () and world.build.workers[0].ticks == 1


def test_setup_ending_makes_the_worker_look_again_only_once(world):
    world.app.start_setup()
    world.setup.end()
    world.app.step(world.index)

    world.app.step(world.index)

    assert world.problems.calls == 1


def test_checks_are_run_again_as_soon_as_setup_ends(world, monkeypatch):
    runs = []
    monkeypatch.setattr("clipper.app.checks.run_checks", lambda cfg, **given: runs.append(True) or [])
    world.app.checks()
    world.app.start_setup()
    world.setup.end()

    world.app.checks()
    world.app.checks()

    assert len(runs) == 2                        # once more for what setup changed, then cached again


def test_setup_is_refused_while_a_reel_renders(world):
    world.state.set_rendering(Rendering("de_mirage", "player", 0.0))

    assert world.app.start_setup() == "A Reel is rendering. Set up once it is done."
    assert world.setup.starts == 0               # HLAE and FFmpeg are not replaced under a render

    world.state.set_idle()
    assert world.app.start_setup() is None
    assert world.setup.starts == 1


def test_asking_for_setup_again_while_it_runs_starts_no_second_run(world):
    assert world.app.start_setup() is None

    assert world.app.start_setup() is None
    assert world.setup.starts == 1


def test_setup_works_on_the_apps_own_settings_and_is_told_the_newest_hlae(world):
    world.app.releases = checks.HlaeReleases(fetch=lambda: "2.200.0")
    world.app.releases.refresh_if_due()

    context = world.setup.context()

    assert context.store is world.store and context.latest_hlae == "2.200.0"


# --- the one-click update -----------------------------------------------------------------------------


def test_the_app_offers_updates_to_the_version_it_is_from_the_downloads_folder(world, monkeypatch):
    given = world.updates.given
    assert given["current"] == update.running_version()
    assert given["enabled"] is False                 # tests run the source, which never updates itself
    assert given["folder"]() == paths.data_dir() / "downloads"      # beside what Setup downloads
    assert given["logs"]() == paths.logs_dir()


def test_only_the_installed_exe_looks_for_updates(tmp_path, monkeypatch):
    monkeypatch.setattr(packaged, "frozen", lambda: True)
    app = App(SettingsStore(tmp_path / "settings.json"), index_path=tmp_path / "clipper.sqlite",
              setup=FakeSetup, updates=FakeUpdates)
    assert app.updates.given["enabled"] is True


def test_update_is_refused_while_a_reel_renders(world):
    world.state.set_rendering(Rendering("de_mirage", "player", 0.0))

    assert world.app.start_update() == "A Reel is rendering. Update once it is done."
    assert world.updates.starts == 0             # the installer quits the app, which would stop the render

    world.state.set_idle()
    assert world.app.start_update() is None
    assert world.updates.starts == 1


def test_update_is_refused_while_setup_runs(world):
    world.setup.start()

    assert world.app.start_update() == "Set up is running. Update once it is done."

    world.setup.end()
    assert world.app.start_update() is None


def test_a_newer_version_is_announced_with_a_toast_that_opens_status(world, monkeypatch):
    toasts = []
    monkeypatch.setattr("clipper.app.notify", lambda title, body, actions=(): toasts.append((title, body, actions)))
    world.app.page_port = 8766

    world.updates.given["announce"](update.Release(version="0.3.0", notes="", page="", installer=None))

    assert toasts == [("CS2 Clipper 0.3.0 is out",
                       "Open Status to see what changed, then update with one click.",
                       (("Show", "http://127.0.0.1:8766/status"), ("Not now", None)))]


def test_the_toast_of_a_newer_version_has_no_button_when_there_are_no_pages(world, monkeypatch):
    toasts = []
    monkeypatch.setattr("clipper.app.notify", lambda title, body, actions=(): toasts.append(actions))

    world.updates.given["announce"](update.Release(version="0.3.0", notes="", page="", installer=None))

    assert toasts == [()]


def test_the_release_check_looks_for_updates_too(world):
    world.app.releases = checks.HlaeReleases(fetch=lambda: "2.200.0")
    world.stop.wait = lambda seconds: world.stop.request("now")         # one round, then the loop ends

    world.app._run_releases()

    assert world.updates.refreshes == 1 and world.app.releases.latest == "2.200.0"


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
            raise RuntimeError("csda vanished")        # the start-up check itself raises, once
        second_check.set()
        return []

    app = App(world.store, index_path=tmp_path / "clipper.sqlite", state=world.state, stop=world.stop,
              problems=problems, build=world.build, recheck_seconds=60.0, clock=world.clock)

    with caplog.at_level(logging.ERROR, logger="clipper.app"):
        app.start_worker()
        assert second_check.wait(timeout=5.0)            # a later pass ran, on the same thread
        app.quit("now")
        assert app.wait(timeout=5.0) is True             # and it ended only because it was asked to

    assert published[1] == ("The worker hit an error: csda vanished",)
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


def _releases_threads(known=()):
    """The live "releases" threads besides `known` ones (another test's, should one have leaked)."""
    return [t for t in threading.enumerate() if t.name == "releases" and t not in known]


def _end_releases(world, known) -> None:
    """The fixture never stops the app, so a test that starts the release check ends it itself."""
    world.app.quit("now")
    for thread in _releases_threads(known):
        thread.join(timeout=5.0)
    assert _releases_threads(known) == []


def test_start_releases_calls_refresh_if_due_promptly_and_stops_when_the_app_quits(world):
    called = threading.Event()
    world.app.releases.refresh_if_due = called.set
    known = _releases_threads()

    world.app.start_releases()

    try:
        assert called.wait(timeout=2.0)
    finally:
        _end_releases(world, known)


def test_start_releases_starts_one_thread_however_often_it_is_called(world):
    world.app.releases.refresh_if_due = lambda: None
    known = _releases_threads()

    world.app.start_releases()
    world.app.start_releases()          # HlaeReleases has no lock, so it may only ever have one writer

    try:
        assert len(_releases_threads(known)) == 1
    finally:
        _end_releases(world, known)


# --- open_window: the window process, through the launcher (Task 14) --------------------------------

PC = "http://127.0.0.1:8765"


@dataclass
class FakeLauncher:
    """`WindowLauncher` without a process: records what it was asked, and shares an event log so a test
    can see the order things closed in."""

    base_url: str
    events: list
    opened: list = field(default_factory=list)

    def open(self, page: str) -> None:
        self.opened.append(page)

    def current(self) -> dict:
        return {"seq": len(self.opened), "page": self.opened[-1] if self.opened else "/status"}

    def close(self) -> None:
        self.events.append("launcher closed")


@dataclass
class Windowed:
    app: App
    launchers: list
    servers: list
    events: list


@pytest.fixture
def windowed(tmp_path, monkeypatch):
    """An App whose web server has no socket (it "binds" the port it is asked for) and whose window
    launcher records its calls: nothing listens, nothing opens."""
    (tmp_path / "clips").mkdir()
    store = SettingsStore(tmp_path / "settings.json")
    assert store.save({"data_root": str(tmp_path / "clips")}) == {}
    events, launchers, servers = [], [], []

    class Server:
        def __init__(self, flask_app, port, *, tries):
            self.flask_app, self.port = flask_app, port
            servers.append(self)

        def start(self) -> None:
            events.append("web started")

        def stop(self) -> None:
            events.append("web stopped")

    def make_launcher(base_url: str) -> FakeLauncher:
        launchers.append(FakeLauncher(base_url, events))
        return launchers[-1]

    monkeypatch.setattr(web, "WebServer", Server)
    app = App(store, index_path=tmp_path / "clipper.sqlite", launcher=make_launcher, setup=FakeSetup,
              updates=FakeUpdates)
    return Windowed(app, launchers, servers, events)


def test_start_web_prepares_the_window_for_the_url_of_the_port_it_got(windowed):
    windowed.app.start_web()
    assert [launcher.base_url for launcher in windowed.launchers] == ["http://127.0.0.1:8765"]


def test_open_window_asks_the_launcher_to_show_the_page(windowed):
    windowed.app.start_web()
    windowed.app.open_window("/reels")
    assert windowed.launchers[0].opened == ["/reels"]


def test_open_window_without_a_web_server_opens_nothing_and_says_so(windowed, caplog):
    with caplog.at_level(logging.INFO, logger="clipper.app"):
        windowed.app.open_window("/status")
    assert windowed.launchers == []
    assert "no web server" in caplog.text


def test_the_web_contexts_window_request_is_the_launchers(windowed):
    windowed.app.start_web()
    client = windowed.servers[0].flask_app.test_client()
    assert client.get("/api/window", base_url=PC).get_json() == {"seq": 0, "page": "/status"}

    windowed.app.open_window("/reels")

    assert client.get("/api/window", base_url=PC).get_json() == {"seq": 1, "page": "/reels"}


def test_a_page_asked_for_through_the_web_reaches_the_launcher(windowed):
    windowed.app.start_web()
    client = windowed.servers[0].flask_app.test_client()
    marked = {web.MARKER_HEADER: "1"}

    response = client.post("/api/window", base_url=PC, headers=marked, json={"page": "/settings"})

    assert response.status_code == 204
    assert windowed.launchers[0].opened == ["/settings"]


def test_the_update_routes_are_answered_by_the_apps_updates(windowed):
    windowed.app.start_web()
    client = windowed.servers[0].flask_app.test_client()
    assert client.get("/api/update", base_url=PC).get_json()["running"] is False

    response = client.post("/api/update", base_url=PC, headers={web.MARKER_HEADER: "1"})

    assert response.status_code == 202 and windowed.app.updates.starts == 1
    assert client.get("/api/update", base_url=PC).get_json()["running"] is True


def test_the_set_up_routes_are_answered_by_the_apps_setup(windowed):
    windowed.app.start_web()
    client = windowed.servers[0].flask_app.test_client()
    assert client.get("/api/setup", base_url=PC).get_json()["running"] is False

    response = client.post("/api/setup", base_url=PC, headers={web.MARKER_HEADER: "1"})

    assert response.status_code == 202 and windowed.app.setup.starts == 1
    assert client.get("/api/setup", base_url=PC).get_json()["running"] is True


def _download(tmp_path):
    archive = tmp_path / "clips" / "demos" / "1-a.dem.zst"
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive.write_bytes(b"x")
    return archive


def test_a_delete_through_the_web_takes_the_demo_and_its_download(windowed, tmp_path):
    archive = _download(tmp_path)
    index = Index(tmp_path / "clipper.sqlite")
    try:
        demo_id = index.add_demo(archive.name, "a" * 64, archive)
        windowed.app.start_web()
        client = windowed.servers[0].flask_app.test_client()

        response = client.post(f"/api/demos/{demo_id}/delete", base_url=PC, headers={web.MARKER_HEADER: "1"})

        assert response.status_code == 200
        assert index.demo(demo_id) is None
        assert not archive.exists()
    finally:
        index.close()


def test_a_delete_through_the_web_waits_while_the_worker_takes_a_step_on_the_demo(windowed, tmp_path):
    archive = _download(tmp_path)
    index = Index(tmp_path / "clipper.sqlite")
    try:
        demo_id = index.add_demo(archive.name, "a" * 64, archive)
        windowed.app.start_web()
        client = windowed.servers[0].flask_app.test_client()
        assert windowed.app.deletes.claim(demo_id)          # what the worker does before each step

        response = client.post(f"/api/demos/{demo_id}/delete", base_url=PC, headers={web.MARKER_HEADER: "1"})

        assert response.status_code == 202
        assert index.demo(demo_id) is not None and archive.exists()
    finally:
        index.close()


def test_close_closes_the_window_and_then_stops_the_web_server(windowed):
    windowed.app.start_web()
    windowed.events.clear()

    windowed.app.close()

    assert windowed.events == ["launcher closed", "web stopped"]


def test_close_before_any_web_server_is_harmless(windowed):
    windowed.app.close()
    assert windowed.events == []


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


# --- ask_to_quit / quit_running: `clipper quit`, which the installer runs before it replaces the exe ---


def test_ask_to_quit_finds_the_running_copy_and_asks_it_to_quit_now(tmp_path):
    modes = []
    ctx = WebContext(index_path=tmp_path / "clipper.sqlite", gate_reasons=lambda: (),
                     quit=lambda mode: modes.append(mode) or "now")
    server = WebServer(create_app(ctx), 0, host="127.0.0.1", tries=1)
    server.start()
    try:
        assert ask_to_quit([server.port]) is True
        assert modes == ["now"]         # not None: a render under way is stopped, nobody is there to be asked
    finally:
        server.stop()


def test_ask_to_quit_with_nothing_listening_returns_false():
    assert ask_to_quit([_free_port()]) is False


class Naps:
    """A clock that only moves when `sleep` is called, and `then(n, do)`: what happens during nap n."""

    def __init__(self):
        self.now, self.taken, self._then = 0.0, 0, {}

    def then(self, nap: int, do) -> None:
        self._then[nap] = do

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.taken += 1
        self._then.pop(self.taken, lambda: None)()


def _never_asked(ports):
    pytest.fail("asked a copy to quit")      # and never the real ask_to_quit: 8765 may be the user's running app


def test_quit_with_no_copy_running_asks_nothing_and_ends_at_once():
    naps = Naps()

    assert quit_running(ask=_never_asked, others=lambda: [], clock=naps.clock, sleep=naps.sleep) == 0
    assert naps.taken == 0


def test_quit_asks_the_running_copy_once_and_waits_until_it_has_ended():
    naps, asked = Naps(), []
    running = single_instance(paths.lock_file())
    running.__enter__()
    naps.then(3, lambda: running.__exit__(None, None, None))      # stopping a render takes it a moment

    code = quit_running(ask=lambda ports: asked.append(ports) or True, others=lambda: [],
                        clock=naps.clock, sleep=naps.sleep)

    assert code == 0
    assert asked == [range(Config.page_port, Config.page_port + web.PORTS_TO_TRY)]      # where its pages may be
    assert naps.taken == 3


def test_quit_asks_again_a_copy_whose_pages_did_not_answer():
    naps, answers = Naps(), [False, True]
    running = single_instance(paths.lock_file())
    running.__enter__()
    naps.then(4, lambda: running.__exit__(None, None, None))

    assert quit_running(ask=lambda ports: answers.pop(0), others=lambda: [], clock=naps.clock, sleep=naps.sleep) == 0
    assert answers == []


def test_quit_waits_for_the_exes_other_processes_to_end_too():
    # The onefile exe's outer process is still clearing away what it unpacked: the file is in use until it ends.
    naps, others = Naps(), [["outer"], ["outer"], []]

    assert quit_running(ask=_never_asked, others=lambda: others.pop(0), clock=naps.clock, sleep=naps.sleep) == 0
    assert naps.taken == 2


def test_quit_gives_up_on_a_copy_that_is_still_running_when_the_wait_is_over(capsys):
    naps = Naps()
    with single_instance(paths.lock_file()):
        code = quit_running(wait_seconds=5.0, ask=lambda ports: True, others=lambda: [],
                            clock=naps.clock, sleep=naps.sleep)

    assert code == 1
    assert 5.0 <= naps.now < 6.0
    assert "still running" in capsys.readouterr().err


# --- run_headless --------------------------------------------------------------------------------


def test_run_headless_returns_0_with_a_message_when_the_lock_is_already_held(monkeypatch, capsys):
    # Never the real hand_over: on 8765-8774 it would find the user's running app and open its window.
    monkeypatch.setattr("clipper.app.hand_over", lambda page, ports: True)
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


# --- run: what starts, what opens, and what closes (Task 14) ------------------------------------------


@pytest.fixture
def startup(monkeypatch):
    """`run` with everything that would listen or show replaced by a note in `events`: the web server,
    the worker and releases threads, the window and the tray. The lock, the settings and the App itself
    are real, in the test's own app data folder."""
    events = []
    monkeypatch.setattr("clipper.app.applog.setup", lambda logs_dir: [])
    monkeypatch.setattr(App, "start_web", lambda self: events.append("web"))
    monkeypatch.setattr(App, "start_worker", lambda self: events.append("worker"))
    monkeypatch.setattr(App, "start_releases", lambda self: events.append("releases"))
    monkeypatch.setattr(App, "open_window", lambda self, page: events.append(("window", page)))
    monkeypatch.setattr(App, "close", lambda self: events.append("close"))
    monkeypatch.setattr("clipper.app.tray.run_tray", lambda app: events.append("tray") or True)
    return events


def test_run_starts_the_pages_and_the_worker_then_opens_the_status_window_then_the_tray(startup):
    assert run() == 0
    assert startup == ["web", "worker", "releases", ("window", "/status"), "tray", "close"]


def test_run_opens_the_page_it_is_asked_for(startup):
    assert run(open_page="/reels") == 0
    assert ("window", "/reels") in startup
    assert ("window", "/status") not in startup


def test_run_in_the_background_shows_only_the_tray(startup):
    assert run(background=True) == 0
    assert startup == ["web", "worker", "releases", "tray", "close"]


def test_run_headless_shows_neither_tray_nor_window(startup):
    assert run(headless=True, open_page="/reels") == 0
    assert startup == ["web", "worker", "releases", "close"]


def test_run_headless_is_run_without_tray_or_window(startup):
    assert run_headless("/reels") == 0
    assert startup == ["web", "worker", "releases", "close"]


def test_run_without_a_tray_says_so(startup, monkeypatch, caplog):
    monkeypatch.setattr("clipper.app.tray.run_tray", lambda app: startup.append("tray") or False)
    with caplog.at_level(logging.WARNING, logger="clipper.app"):
        assert run() == 0
    assert "no tray" in caplog.text
    assert startup[-2:] == ["tray", "close"]


def test_run_keeps_the_app_alive_until_the_worker_has_ended_whatever_the_tray_did(startup, monkeypatch):
    waits = []

    def wait(self, timeout=None):       # the worker "ends" at the third look
        waits.append(timeout)
        return len(waits) >= 3

    monkeypatch.setattr(App, "wait", wait)
    assert run() == 0
    assert len(waits) == 3
    assert startup[-1] == "close"


def test_a_background_start_while_a_copy_runs_exits_quietly_without_showing_anything(monkeypatch, capsys):
    handed = []
    monkeypatch.setattr("clipper.app.hand_over", lambda page, ports: handed.append(page) or True)
    with single_instance(paths.lock_file()):
        assert run(background=True) == 0
    assert handed == []                  # sign-in never opens a window
    assert capsys.readouterr().err == ""


# --- Sign in with FACEIT (Settings) -----------------------------------------------------------------


def _ready(tmp_path, monkeypatch, *, key="the-app-key", secret="the-client-secret",
           steamid="76561198192858303", nickname="cheesebagga"):
    """An App whose web server is up on a port and whose sign-in has been started (so a state and a
    PKCE verifier exist). The FACEIT calls are faked: no secret is decrypted, no network."""
    store = SettingsStore(tmp_path / "settings.json")
    store.save({"faceit_oauth_client_id": "client-1",
                **({KEY_FIELD: key} if key else {}), **({SECRET_FIELD: secret} if secret else {})})
    app = App(store, index_path=tmp_path / "clipper.sqlite")
    app.page_port = 8765
    app.faceit_login_url()                                     # mints and stores the state + verifier
    monkeypatch.setattr("clipper.app.faceit_oauth.access_token", lambda *args, **kwargs: "a-token")
    monkeypatch.setattr("clipper.app.faceit_oauth.player_id", lambda token: "p-1")
    monkeypatch.setattr("clipper.app.FaceitClient", lambda key_of: types.SimpleNamespace(
        player=lambda name: Player("p-1", name.strip(), steamid),
        player_by_id=lambda player_id: Player("p-1", nickname, steamid)))
    index = Index(tmp_path / "clipper.sqlite")
    try:
        return app, store, index.get_flag("faceit_oauth_state")
    finally:
        index.close()


def test_faceit_login_url_is_empty_until_there_is_a_client_id_and_a_port(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    app = App(store, index_path=tmp_path / "clipper.sqlite")
    assert app.faceit_login_url() == ""                        # no client ID and no web server yet
    store.save({"faceit_oauth_client_id": "client-1"})
    assert app.faceit_login_url() == ""                        # still no web server
    app.page_port = 8765
    url = app.faceit_login_url()
    assert url.startswith("https://accounts.faceit.com?")


def test_faceit_login_url_uses_the_https_relay_page_when_no_redirect_is_set(tmp_path):
    # FACEIT refuses a plain-http redirect URI, so the app's own http://localhost address can never be
    # the fallback: the relay page (docs/index.html, on GitHub Pages) is.
    store = SettingsStore(tmp_path / "settings.json")
    store.save({"faceit_oauth_client_id": "client-1"})
    app = App(store, index_path=tmp_path / "clipper.sqlite")
    app.page_port = 8765

    url = app.faceit_login_url()

    assert "redirect_uri=https%3A%2F%2Fmohammadam02.github.io%2Fcs2-clipper%2F" in url


def test_faceit_login_url_uses_the_registered_redirect_when_one_is_set(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")
    store.save({"faceit_oauth_client_id": "client-1",
                "faceit_redirect_uri": "https://baggaclipper.example/faceit"})
    app = App(store, index_path=tmp_path / "clipper.sqlite")
    app.page_port = 8765

    url = app.faceit_login_url()

    assert "redirect_uri=https%3A%2F%2Fbaggaclipper.example%2Ffaceit" in url


def test_the_state_carries_the_port_so_the_https_relay_can_find_this_app(tmp_path):
    # The relay page is static and knows nothing about this machine, so the port it must send the
    # browser back to travels in the state FACEIT echoes round the trip.
    store = SettingsStore(tmp_path / "settings.json")
    store.save({"faceit_oauth_client_id": "client-1",
                "faceit_redirect_uri": "https://mohammadam02.github.io/cs2-clipper/"})
    app = App(store, index_path=tmp_path / "clipper.sqlite")
    app.page_port = 8765

    url = app.faceit_login_url()
    state = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))["state"]

    assert state.startswith("8765.")
    index = Index(tmp_path / "clipper.sqlite")
    try:
        assert index.get_flag("faceit_oauth_state") == state       # what comes back is what we minted
    finally:
        index.close()


def test_faceit_sign_in_saves_the_nickname_and_steamid(tmp_path, monkeypatch):
    app, store, state = _ready(tmp_path, monkeypatch)

    assert app.faceit_sign_in("a-code", state) == "cheesebagga"

    cfg = store.current().config
    assert (cfg.faceit_nickname, cfg.subject_steamid) == ("cheesebagga", "76561198192858303")


def test_faceit_sign_in_exchanges_the_code_under_the_redirect_the_link_named(tmp_path, monkeypatch):
    # FACEIT's token endpoint refuses a code presented under another redirect URI than the one it was
    # issued for; with none set in Settings, both halves name the https relay page.
    app, _store, state = _ready(tmp_path, monkeypatch)
    redirects = []
    monkeypatch.setattr("clipper.app.faceit_oauth.access_token",
                        lambda code, client_id, secret, redirect_uri, verifier:
                        redirects.append(redirect_uri) or "a-token")

    app.faceit_sign_in("a-code", state)

    assert redirects == ["https://mohammadam02.github.io/cs2-clipper/"]


def test_faceit_sign_in_refuses_a_state_that_is_not_ours(tmp_path, monkeypatch):
    app, _store, _state = _ready(tmp_path, monkeypatch)
    with pytest.raises(OAuthError, match="did not come back"):
        app.faceit_sign_in("a-code", "someone-elses-state")


def test_faceit_sign_in_needs_the_app_key_first(tmp_path, monkeypatch):
    app, _store, state = _ready(tmp_path, monkeypatch, key=None)
    with pytest.raises(OAuthError, match="API key first"):
        app.faceit_sign_in("a-code", state)


def test_faceit_sign_in_needs_the_client_secret_first(tmp_path, monkeypatch):
    app, _store, state = _ready(tmp_path, monkeypatch, secret=None)
    with pytest.raises(OAuthError, match="client secret first"):
        app.faceit_sign_in("a-code", state)


def test_faceit_lookup_saves_the_nickname_and_steamid(tmp_path, monkeypatch):
    app, store, _state = _ready(tmp_path, monkeypatch)

    found = app.faceit_lookup("  cheesebagga  ")

    assert found == {"nickname": "cheesebagga", "steamid": "76561198192858303"}
    cfg = store.current().config
    assert (cfg.faceit_nickname, cfg.subject_steamid) == ("cheesebagga", "76561198192858303")


def test_faceit_lookup_needs_the_app_key_first(tmp_path, monkeypatch):
    app, _store, _state = _ready(tmp_path, monkeypatch, key=None)
    with pytest.raises(FaceitError, match="API key first"):
        app.faceit_lookup("cheesebagga")
