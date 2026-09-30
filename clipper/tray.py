"""The tray icon: what the app is doing in its tooltip, and the menu that opens, pauses and quits it
(spec: How the app runs, The tray; Closing and quitting).

Adapted from thelifeofsuleyman/cs2-clipper's `aegis/app.py` (`_tray_image`, `_start_tray_for_window`,
`_run_tray_browser`, `_open_folder`). What we changed: the icon runs on the main thread until the app
has quit, not beside an in-process window; the tooltip is the one-line summary the Status page shows;
the menu is made from the app's state (Pause or Resume, and a Quit that asks while a render runs); a
menu action that fails is logged; and the icon is a plainer mark (clipper.icon, shared with the exe).

MIT License

Copyright (c) 2026 thelifeofsuleyman

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from clipper.state import Snapshot, summary

if TYPE_CHECKING:
    import pystray

    from clipper.app import App

log = logging.getLogger(__name__)

NAME = "cs2-clipper"
REFRESH_SECONDS = 2.0
TOOLTIP_LIMIT = 127      # Windows cuts a tray icon's tooltip after 127 characters


def tooltip(snapshot: Snapshot) -> str:
    """The summary the Status page shows, and a second line when the web server has no port, cut to
    what Windows shows."""
    text = summary(snapshot)
    if snapshot.pages_off:
        text += f"\nPages are off: {snapshot.pages_off}"
    return text if len(text) <= TOOLTIP_LIMIT else text[:TOOLTIP_LIMIT - 1] + "…"



@dataclass(frozen=True)
class _View:
    """What about the app shapes the menu: when it changes, the menu is built again."""

    paused: bool
    rendering: bool
    folder: Path | None      # the clips folder, when it is set and exists


def _view(app: App) -> _View:
    snapshot = app.state.snapshot()
    folder = app.settings.current().config.data_root
    return _View(paused=snapshot.paused_by is not None, rendering=snapshot.rendering is not None,
                 folder=folder if folder is not None and folder.is_dir() else None)


def _guarded(action: Callable[[], object]) -> Callable[[], None]:
    """A menu action runs inside the tray's message loop, where an exception would only be printed to a
    console that is usually not there: it is logged instead."""
    def run() -> None:
        try:
            action()
        except Exception:  # noqa: BLE001 - nothing may escape into the message loop
            log.exception("a tray menu action failed")
    return run


def menu(app: App, *, open_folder: Callable[[Path], None] = lambda folder: None) -> pystray.Menu:
    """The tray menu. On Windows pystray builds the menu ahead of time and asks for its items again at
    every `Icon.update_menu()`, so they are made from the app's state each time they are asked for.
    `open_folder` shows a folder in Explorer (`run_tray` passes `os.startfile`)."""
    import pystray

    def items() -> Iterator[pystray.MenuItem]:
        view = _view(app)
        yield pystray.MenuItem("Open CS2 Clipper", _guarded(lambda: app.open_window("/status")), default=True)
        yield pystray.MenuItem("Open clips folder", _guarded(lambda: open_folder(view.folder)),
                               enabled=view.folder is not None)
        yield pystray.MenuItem("Resume rendering" if view.paused else "Pause rendering",
                               _guarded(app.resume if view.paused else app.pause))
        if view.rendering:
            yield pystray.MenuItem("Quit", pystray.Menu(
                pystray.MenuItem("Quit now (the render is redone later)", _guarded(lambda: app.quit("now"))),
                pystray.MenuItem("Quit when this render finishes", _guarded(lambda: app.quit("after_render"))),
            ))
        else:
            yield pystray.MenuItem("Quit", _guarded(lambda: app.quit("now")))

    return pystray.Menu(items)


def _keep_fresh(icon: pystray.Icon, app: App) -> None:
    """Every REFRESH_SECONDS: the tooltip, and the menu when what shapes it has changed (building it
    anew while someone has it open is best avoided). Stops the icon once the app has quit; `app.wait`
    returns at once when the worker thread ends, so that takes no longer than the worker does."""
    try:
        shown = _view(app)       # pystray built the menu a moment ago, from the same state
        while not app.wait(REFRESH_SECONDS):
            try:
                icon.title = tooltip(app.state.snapshot())
                view = _view(app)
                if view != shown:
                    icon.update_menu()
                    shown = view
            except Exception:  # noqa: BLE001 - a bad refresh must not end the loop that stops the icon
                log.exception("could not refresh the tray")
    finally:
        icon.stop()


def _setup(icon: pystray.Icon, app: App) -> None:
    """pystray runs this on a thread of its own once its message loop is up: shows the icon and starts
    the refresh thread (a daemon, so it never holds the process open)."""
    try:
        icon.visible = True
    except Exception:  # noqa: BLE001
        log.exception("could not show the tray icon")
        icon.stop()              # `run` returns, and the app goes on without a tray
        return
    threading.Thread(target=_keep_fresh, args=(icon, app), name="tray", daemon=True).start()


def run_tray(app: App) -> bool:
    """Shows the tray icon and runs it on the calling thread, which must be the main one, until the app
    has quit; True then. False, with nothing shown, when there is no tray to be had: pystray or Pillow
    cannot be imported, or the icon fails to start."""
    try:
        import pystray

        from clipper.icon import mark

        image = mark(64)
    except Exception as exc:  # noqa: BLE001 - ImportError, or a native library that fails to load
        log.warning("the tray icon is not available (%s)", exc)
        return False
    try:
        icon = pystray.Icon(NAME, image, tooltip(app.state.snapshot()), menu(app, open_folder=os.startfile))
        icon.run(setup=lambda icon: _setup(icon, app))
    except Exception:  # noqa: BLE001
        log.exception("the tray icon failed")
        return False
    if not app.wait(0):
        log.warning("the tray icon closed before the app quit; the app keeps running without it")
    return True
