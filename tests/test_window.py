"""Tests for clipper.window: the command line, the launcher and the fallbacks (spec: How the app runs,
Two processes; When something goes wrong, the window row; Testing).

Nothing here opens a window. `popen` is a fake, pywebview is a fake module (or made unimportable), and
Chromium and the default browser are faked too -- the autouse fixture below fakes every one of them so a
test that forgets cannot put a window on the desktop."""
from __future__ import annotations

import logging
import subprocess
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from clipper import window
from clipper.window import TITLE, WindowApi, WindowLauncher, run_window, window_command

BASE = "http://127.0.0.1:8765"
CHROMIUM = "C:/fake/msedge.exe"
PROFILE = Path("C:/fake/window-profile")


# --- what the launcher starts ---------------------------------------------------------------------------


class FakeProcess:
    def __init__(self, command: list[str], kwargs: dict):
        self.command, self.kwargs = command, kwargs
        self.exit_code: int | None = None      # None while "alive"
        self.terminated = False

    def poll(self) -> int | None:
        return self.exit_code

    def terminate(self) -> None:
        self.terminated = True
        self.exit_code = 1


class FakePopen:
    """`subprocess.Popen` that starts nothing."""

    def __init__(self) -> None:
        self.started: list[FakeProcess] = []
        self.fail_with: OSError | None = None

    def __call__(self, command: list[str], **kwargs) -> FakeProcess:
        if self.fail_with is not None:
            raise self.fail_with
        process = FakeProcess(command, kwargs)
        self.started.append(process)
        return process


# --- window_command --------------------------------------------------------------------------------------


def test_window_command_from_source_runs_the_package():
    url = f"{BASE}/status"
    assert window_command(url) == [sys.executable, "-m", "clipper", "--window", url]


def test_window_command_when_frozen_runs_the_executable_itself(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    url = f"{BASE}/status"
    assert window_command(url) == [sys.executable, "--window", url]


# --- WindowLauncher --------------------------------------------------------------------------------------


def test_a_new_launcher_has_made_no_request():
    assert WindowLauncher(BASE, FakePopen()).current() == {"seq": 0, "page": "/status"}


def test_the_first_open_starts_one_window_process_on_that_page_without_a_console():
    popen = FakePopen()
    launcher = WindowLauncher(BASE, popen)

    launcher.open("/reels")

    assert [process.command for process in popen.started] == [window_command(f"{BASE}/reels")]
    assert popen.started[0].kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert launcher.current()["seq"] == 0            # starting a window is not a request to switch one


def test_the_window_process_joins_the_job_so_it_ends_with_the_app(fakes):
    popen = FakePopen()
    launcher = WindowLauncher(BASE, popen)

    launcher.open("/reels")
    launcher.open("/settings")                       # a request to the live window starts nothing to guard

    assert fakes.guarded == popen.started            # the one process started, put in the job once


def test_open_while_the_window_is_alive_records_a_request_and_starts_nothing():
    popen = FakePopen()
    launcher = WindowLauncher(BASE, popen)
    launcher.open("/status")

    launcher.open("/reels")
    assert launcher.current() == {"seq": 1, "page": "/reels"}
    launcher.open("/settings")
    assert launcher.current() == {"seq": 2, "page": "/settings"}

    assert len(popen.started) == 1


def test_open_after_the_window_process_exited_starts_a_new_one():
    popen = FakePopen()
    launcher = WindowLauncher(BASE, popen)
    launcher.open("/status")
    popen.started[0].exit_code = 0                   # the window was closed

    launcher.open("/demos")

    assert [process.command for process in popen.started] == [
        window_command(f"{BASE}/status"), window_command(f"{BASE}/demos"),
    ]


def test_close_terminates_a_live_window_process():
    popen = FakePopen()
    launcher = WindowLauncher(BASE, popen)
    launcher.open("/status")

    launcher.close()

    assert popen.started[0].terminated is True


def test_close_is_harmless_with_no_window_or_with_one_that_already_exited():
    popen = FakePopen()
    launcher = WindowLauncher(BASE, popen)
    launcher.close()                                 # nothing was ever opened

    launcher.open("/status")
    popen.started[0].exit_code = 0
    launcher.close()
    launcher.close()                                 # and twice

    assert popen.started[0].terminated is False


def test_a_window_that_cannot_start_is_logged_not_raised_and_the_next_open_tries_again(caplog):
    popen = FakePopen()
    popen.fail_with = OSError("the python is gone")
    launcher = WindowLauncher(BASE, popen)

    with caplog.at_level(logging.ERROR, logger="clipper.window"):
        launcher.open("/status")                     # must not raise
    assert "could not start the window" in caplog.text

    popen.fail_with = None
    launcher.open("/status")
    assert len(popen.started) == 1


# --- run_window: pywebview first, then Chromium --app, then the default browser ---------------------------


class FakeEvent:
    """One of pywebview's `window.events`: `+=` subscribes, `set` calls every handler with its arguments."""

    def __init__(self) -> None:
        self.handlers: list = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def set(self, *args) -> list:
        return [handler(*args) for handler in self.handlers]


class FakeWindow:
    """What pywebview's `create_window` returns: records what `WindowApi.front` does to it, and fires the
    events a real window fires when its state changes."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.events = types.SimpleNamespace(initialized=FakeEvent(), minimized=FakeEvent(),
                                            maximized=FakeEvent(), restored=FakeEvent())

    def restore(self) -> None:
        self.calls.append("restore")

    def maximize(self) -> None:
        self.calls.append("maximize")

    def show(self) -> None:
        self.calls.append("show")

    @property
    def on_top(self) -> bool:
        return False

    @on_top.setter
    def on_top(self, value: bool) -> None:
        self.calls.append(f"on_top={value}")


class FakeWebview:
    """The pywebview module: `create_window` and `start`, and nothing shown. Like pywebview 6, `start`
    fires each window's `initialized` with the engine it picked, and a handler's False cancels the window
    before it shows."""

    def __init__(self) -> None:
        self.created: list[tuple[tuple, dict]] = []
        self.windows: list[FakeWindow] = []
        self.started = 0
        self.start_kwargs: dict = {}
        self.start_error: Exception | None = None
        self.renderer = "edgechromium"
        self.shown = 0

    def create_window(self, *args, **kwargs) -> FakeWindow:
        self.created.append((args, kwargs))
        self.windows.append(FakeWindow())
        return self.windows[-1]

    def start(self, *args, **kwargs) -> None:
        self.started += 1
        self.start_kwargs = kwargs
        if self.start_error is not None:
            raise self.start_error
        for fake in self.windows:
            if False in fake.events.initialized.set(self.renderer):
                return
        self.shown += 1


@dataclass
class Chromium:
    """`subprocess.Popen` for the Chromium fallback, and where `find_chromium` says it is."""

    path: str = CHROMIUM
    launched: list = field(default_factory=list)
    waited: int = 0
    fail_with: OSError | None = None

    def popen(self, command, **kwargs):
        if self.fail_with is not None:
            raise self.fail_with
        self.launched.append(command)
        return types.SimpleNamespace(wait=self._wait)

    def _wait(self) -> int:
        self.waited += 1
        return 0


@dataclass
class Browser:
    opened: list = field(default_factory=list)


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    """pywebview unimportable, a Chromium at CHROMIUM whose launches are recorded, a browser that
    records what it is asked to open, and a job object that records the processes put in it. Tests
    change one of them."""
    chromium, browser, guarded = Chromium(), Browser(), []
    monkeypatch.setitem(sys.modules, "webview", None)      # `import webview` raises ImportError
    monkeypatch.setattr(window, "find_chromium", lambda: chromium.path)
    monkeypatch.setattr(window.subprocess, "Popen", chromium.popen)
    monkeypatch.setattr(window.webbrowser, "open", lambda url: browser.opened.append(url))
    monkeypatch.setattr("clipper.winjob.guard", guarded.append)
    return types.SimpleNamespace(chromium=chromium, browser=browser, guarded=guarded)


def _pywebview(monkeypatch) -> FakeWebview:
    webview = FakeWebview()
    monkeypatch.setitem(sys.modules, "webview", webview)
    return webview


def test_pywebview_shows_the_page_in_a_window_of_the_size_the_app_uses(monkeypatch, fakes):
    webview = _pywebview(monkeypatch)
    url = f"{BASE}/status"

    assert run_window(url, PROFILE) == 0

    (args, kwargs), = webview.created
    assert args == (TITLE, url)
    assert (kwargs["width"], kwargs["height"], kwargs["min_size"]) == (1180, 820, (900, 600))
    assert isinstance(kwargs["js_api"], WindowApi)
    assert webview.started == 1
    assert fakes.chromium.launched == [] and fakes.browser.opened == []


def test_pywebview_is_asked_for_the_edge_webview2_engine(monkeypatch):
    webview = _pywebview(monkeypatch)

    run_window(f"{BASE}/status", PROFILE)

    assert webview.start_kwargs.get("gui") == "edgechromium"


def test_a_pywebview_left_with_only_internet_explorer_falls_back_to_the_chromium_window(monkeypatch, fakes, caplog):
    webview = _pywebview(monkeypatch)
    webview.renderer = "mshtml"          # no WebView2: pywebview 6 picks MSHTML even when asked for edgechromium

    with caplog.at_level(logging.WARNING, logger="clipper.window"):
        assert run_window(f"{BASE}/status", PROFILE) == 0

    assert webview.shown == 0            # cancelled before it showed: MSHTML can't run the pages' scripts
    assert len(fakes.chromium.launched) == 1
    assert fakes.browser.opened == []
    assert "Internet Explorer" in caplog.text


RAISE = ["show", "on_top=True", "on_top=False"]


def _front_after(monkeypatch, *events: str) -> list[str]:
    """What the page's `front` call does to a pywebview window whose state went through `events`."""
    webview = _pywebview(monkeypatch)
    run_window(f"{BASE}/status", PROFILE)
    (_, kwargs), = webview.created
    for name in events:
        getattr(webview.windows[0].events, name).set()
    kwargs["js_api"].front()
    return webview.windows[0].calls


@pytest.mark.parametrize("events", [(), ("maximized",), ("minimized", "restored"), ("maximized", "restored"),
                                    ("maximized", "minimized", "maximized")])
def test_front_shows_and_raises_a_window_that_is_not_minimized_and_keeps_its_size(monkeypatch, events):
    assert _front_after(monkeypatch, *events) == RAISE


def test_front_restores_a_minimized_window_first(monkeypatch):
    assert _front_after(monkeypatch, "minimized") == ["restore", *RAISE]


def test_front_brings_a_window_minimized_from_maximized_back_maximized(monkeypatch):
    assert _front_after(monkeypatch, "maximized", "minimized") == ["maximize", *RAISE]


def _window_is_gone() -> None:
    raise RuntimeError("the window is gone")


def test_front_never_raises_into_the_page_and_is_harmless_before_there_is_a_window(monkeypatch, caplog):
    WindowApi().front()                              # no window yet

    webview = _pywebview(monkeypatch)
    run_window(f"{BASE}/status", PROFILE)
    (_, kwargs), = webview.created
    webview.windows[0].show = _window_is_gone
    with caplog.at_level(logging.ERROR, logger="clipper.window"):
        kwargs["js_api"].front()                     # must not raise
    assert "bring the window to the front" in caplog.text


def test_without_pywebview_a_chromium_app_window_with_its_own_profile_is_shown_and_waited_for(fakes):
    url = f"{BASE}/reels"

    assert run_window(url, PROFILE) == 0

    (command,) = fakes.chromium.launched
    assert command[0] == CHROMIUM
    assert f"--app={url}" in command
    assert f"--user-data-dir={PROFILE}" in command
    assert fakes.chromium.waited == 1                # this process ends with its window
    assert fakes.browser.opened == []


def test_a_pywebview_that_cannot_start_falls_back_to_the_chromium_window(monkeypatch, fakes):
    webview = _pywebview(monkeypatch)
    webview.start_error = RuntimeError("no WebView2")

    assert run_window(f"{BASE}/status", PROFILE) == 0

    assert len(fakes.chromium.launched) == 1
    assert fakes.browser.opened == []


def test_with_no_chromium_the_default_browser_opens_the_page(fakes):
    fakes.chromium.path = ""
    url = f"{BASE}/settings"

    assert run_window(url, PROFILE) == 0

    assert fakes.chromium.launched == []
    assert fakes.browser.opened == [url]


def test_a_chromium_that_cannot_start_falls_back_to_the_default_browser(fakes):
    fakes.chromium.fail_with = OSError("access denied")
    url = f"{BASE}/status"

    assert run_window(url, PROFILE) == 0

    assert fakes.browser.opened == [url]
