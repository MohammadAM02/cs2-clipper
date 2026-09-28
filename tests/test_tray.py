"""Tests for clipper.tray's tooltip and menu (spec: How the app runs, The tray; Testing).

The icon itself -- pystray's message loop -- is never started by a test: it would appear on the desktop,
and the user checks it by hand. `menu()` only builds pystray's menu objects; its Explorer call is a fake,
and `FakeApp` records what the menu items do."""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from clipper import tray
from clipper.config import Config
from clipper.settings import Loaded
from clipper.state import AppState, Rendering, Snapshot


class FakeApp:
    """The App as the tray sees it: its state, its settings, and the things a menu item does."""

    def __init__(self, clips=None):
        self.state = AppState()
        self.settings = SimpleNamespace(current=lambda: Loaded(Config(data_root=clips), ()))
        self.calls: list[tuple] = []

    def open_window(self, page: str) -> None:
        self.calls.append(("open_window", page))

    def pause(self) -> None:
        self.calls.append(("pause",))

    def resume(self) -> None:
        self.calls.append(("resume",))

    def quit(self, mode: str | None = None) -> str:
        self.calls.append(("quit", mode))
        return mode or "now"


def menu_of(app: FakeApp, opened: list | None = None):
    pytest.importorskip("pystray")
    return tray.menu(app, open_folder=(opened if opened is not None else []).append)


def item_named(menu, text: str):
    return next(item for item in menu if item.text == text)


# --- the tooltip -----------------------------------------------------------------------------------------


def test_the_tooltip_is_the_one_line_summary():
    assert tray.tooltip(Snapshot()) == "Idle"
    assert tray.tooltip(Snapshot(paused_by="you")) == "Paused by you"
    assert tray.tooltip(Snapshot(rendering=Rendering("de_mirage", "player", 0.0))) == (
        "Rendering Mirage (player view)")


def test_the_tooltip_has_a_second_line_when_the_pages_are_off():
    assert tray.tooltip(Snapshot(pages_off="no free port in 8765–8774")) == (
        "Idle\nPages are off: no free port in 8765–8774")


def test_the_tooltip_is_cut_to_the_127_characters_windows_shows():
    text = tray.tooltip(Snapshot(problems=("Postgres won't start: " + "x" * 300,), pages_off="no free port"))
    assert len(text) == 127
    assert text.startswith("Postgres won't start: xxx")
    assert text.endswith("…")


def test_a_tooltip_of_exactly_127_characters_is_kept_whole():
    assert tray.tooltip(Snapshot(problems=("y" * 127,))) == "y" * 127


# --- the menu --------------------------------------------------------------------------------------------


def test_the_menu_of_an_idle_app_with_no_clips_folder_set():
    assert [(item.text, item.enabled, item.default) for item in menu_of(FakeApp())] == [
        ("Open CS2 Clipper", True, True),
        ("Open clips folder", False, False),
        ("Pause rendering", True, False),
        ("Quit", True, False),
    ]


def test_a_click_on_the_icon_opens_the_status_window():
    app = FakeApp()
    menu_of(app)(None)                   # pystray runs the default item when the icon is clicked
    assert app.calls == [("open_window", "/status")]


def test_open_clips_folder_is_enabled_only_while_the_folder_exists(tmp_path):
    assert item_named(menu_of(FakeApp(clips=None)), "Open clips folder").enabled is False
    assert item_named(menu_of(FakeApp(clips=tmp_path / "gone")), "Open clips folder").enabled is False
    assert item_named(menu_of(FakeApp(clips=tmp_path)), "Open clips folder").enabled is True


def test_open_clips_folder_opens_it_in_explorer(tmp_path):
    opened: list = []
    item_named(menu_of(FakeApp(clips=tmp_path), opened), "Open clips folder")(None)
    assert opened == [tmp_path]


@pytest.mark.parametrize("paused_by, label, call", [
    (None, "Pause rendering", "pause"),
    ("you", "Resume rendering", "resume"),
    ("failures", "Resume rendering", "resume"),
])
def test_the_pause_item_follows_who_paused_rendering(paused_by, label, call):
    app = FakeApp()
    app.state.set_paused_by(paused_by)
    assert [item.text for item in menu_of(app)].count(label) == 1
    item_named(menu_of(app), label)(None)
    assert app.calls == [(call,)]


def test_quit_is_a_single_item_while_no_render_runs():
    app = FakeApp()
    quit_item = item_named(menu_of(app), "Quit")
    assert quit_item.submenu is None
    quit_item(None)
    assert app.calls == [("quit", "now")]


def test_quit_offers_the_choice_while_a_render_runs():
    app = FakeApp()
    app.state.set_rendering(Rendering("de_mirage", "player", 0.0))
    quit_item = item_named(menu_of(app), "Quit")
    now, after = list(quit_item.submenu)
    assert (now.text, after.text) == ("Quit now (the render is redone later)", "Quit when this render finishes")
    now(None)
    after(None)
    assert app.calls == [("quit", "now"), ("quit", "after_render")]


def test_the_menu_is_made_from_the_apps_state_each_time_it_is_asked():
    app = FakeApp()
    menu = menu_of(app)
    assert "Pause rendering" in [item.text for item in menu]
    app.state.set_paused_by("you")
    assert "Resume rendering" in [item.text for item in menu]


def test_a_menu_action_that_fails_is_logged_and_does_not_escape(caplog):
    app = FakeApp()

    def no_window(page: str) -> None:
        raise OSError("no window")

    app.open_window = no_window
    with caplog.at_level(logging.ERROR, logger="clipper.tray"):
        menu_of(app)(None)               # runs inside the tray's message loop: nothing may escape into it
    assert "menu action failed" in caplog.text
