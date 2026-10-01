"""The app's window: a process of its own that shows the pages, and the launcher that starts it (spec:
How the app runs, Two processes; When something goes wrong, the window row).

`run_window`, `WindowApi`, `find_chromium` and the Chromium `--app` fallback are adapted from
thelifeofsuleyman/cs2-clipper's `aegis/app.py` (`_run_window`, `_WindowApi`, `_find_chromium`,
`_open_app_mode`). What we changed: the window lives in a process that ends with it, so closing the
window frees its memory (Aegis hides the window to the tray and keeps the process); `WindowApi.front`
replaces Aegis's `background` and `quit`; `minimize`, `toggle_maximize` and `close` are ours (the window
has no frame, so the pages draw the title bar), and so is `pick_folder`; the Chromium fallback takes its
profile folder as an argument and waits for the browser, so the process ends with that window too; the
last resort, the default browser, opens at once instead of after a timer; and errors go to stderr, never
to the app's log file. `WindowLauncher`, the main process's side, is our own.

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
import subprocess
import sys
import threading
import webbrowser
from collections.abc import Callable
from pathlib import Path

from clipper import winjob

log = logging.getLogger(__name__)

TITLE = "CS2 Clipper"
WIDTH, HEIGHT = 1180, 820
MIN_SIZE = (900, 600)


def window_command(url: str) -> list[str]:
    """The command line of a window process showing `url`: this Python running the package, or, once
    piece 2 packages the app, the executable itself."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "--window", url]
    return [sys.executable, "-m", "clipper", "--window", url]


# --- the window process -----------------------------------------------------------------------------------


class WindowApi:
    """Exposed to the page as `window.pywebview.api`. pywebview publishes every public name on it, so
    the window itself and what is known about it stay private."""

    def __init__(self) -> None:
        self._window = None                 # set by `_attach` once the window exists
        self._state = "normal"              # "normal", "minimized" or "maximized"
        self._before_minimized = "normal"   # what a minimized window goes back to

    def _attach(self, window) -> None:
        """Follows the window's state through its events: pywebview has no call that reads it, and
        `restore()` on a maximized window would un-maximize it."""
        self._window = window
        window.events.minimized += self._on_minimized
        window.events.maximized += self._on_maximized
        window.events.restored += self._on_restored

    def _on_minimized(self) -> None:
        if self._state != "minimized":
            self._before_minimized = self._state
        self._state = "minimized"

    def _on_maximized(self) -> None:
        self._state = "maximized"

    def _on_restored(self) -> None:
        self._state = "normal"

    def front(self) -> None:
        """Shows the window and raises it above the other windows. A minimized one comes back first, at
        the size it had: a maximized window stays maximized. Switching TopMost on and off is what lifts a
        window over another program's; it stays an ordinary window afterwards."""
        window = self._window
        if window is None:
            return
        try:
            if self._state == "minimized":
                if self._before_minimized == "maximized":
                    window.maximize()
                else:
                    window.restore()
            window.show()
            window.on_top = True
            window.on_top = False
        except Exception:  # noqa: BLE001 - a page asking to come forward must never get an error back
            log.exception("could not bring the window to the front")

    # The window has no frame of its own, so the nav draws the title bar and these are its three buttons.

    def minimize(self) -> None:
        if self._window is not None:
            self._window.minimize()

    def toggle_maximize(self) -> None:
        """Maximizes the window; a maximized one goes back to its size."""
        window = self._window
        if window is None:
            return
        if self._state == "maximized":
            window.restore()
        else:
            window.maximize()

    def close(self) -> None:
        if self._window is not None:
            self._window.destroy()

    def pick_folder(self, start: str = "") -> str | None:
        """The folder chosen in a system dialog that opens at `start`, or None when it is cancelled."""
        if self._window is None:
            return None
        import webview

        picked = self._window.create_file_dialog(webview.FileDialog.FOLDER, directory=start or "")
        return picked[0] if picked else None


def _show_in_pywebview(url: str) -> bool:
    """The native window, on the Edge WebView2 engine. Returns when it is closed; False, with the reason
    logged, when pywebview cannot load or cannot start (no pythonnet, ...), or when all it has is the
    old Internet Explorer engine (no WebView2), which cannot run the pages' scripts."""
    try:
        import webview
    except Exception as exc:  # noqa: BLE001 - ImportError, or a native library that fails to load
        log.warning("pywebview is not available (%s); trying a Chromium window", exc)
        return False
    engines = []

    def refuse_mshtml(renderer):
        # pywebview 6 falls back to MSHTML even when asked for edgechromium; a False here cancels the
        # window before it shows, and `start` returns
        engines.append(renderer)
        return renderer != "mshtml"

    try:
        api = WindowApi()
        window = webview.create_window(TITLE, url, js_api=api, width=WIDTH, height=HEIGHT, min_size=MIN_SIZE,
                                       frameless=True, easy_drag=False)
        api._attach(window)
        window.events.initialized += refuse_mshtml
        webview.start(gui="edgechromium")      # blocks until the window is closed
    except Exception as exc:  # noqa: BLE001 - whatever pywebview's backends raise
        log.warning("pywebview could not start the window (%s); trying a Chromium window", exc)
        return False
    if "mshtml" in engines:
        log.warning("pywebview has only the old Internet Explorer engine (is WebView2 missing?); "
                    "trying a Chromium window")
        return False
    return True


def find_chromium() -> str:
    """A Chromium browser's path (Edge is on every Windows), or "" when there is none: the registry's
    App Paths first, then the usual install folders."""
    import winreg

    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for name in ("msedge.exe", "chrome.exe", "brave.exe"):
            try:
                with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{name}") as key:
                    path = winreg.QueryValue(key, None)
            except OSError:
                continue
            if path and os.path.exists(path):
                return path
    for path in (
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Programs\BraveSoftware\Brave-Browser\Application\brave.exe"),
    ):
        if os.path.exists(path):
            return path
    return ""


def _show_in_chromium(url: str, profile_dir: Path) -> bool:
    """A chromeless Chromium `--app` window with a profile of its own, apart from anyone's browsing
    (no tabs, no address bar, its own taskbar entry). Waits for the browser, so this process ends with
    its window. False when there is no Chromium browser or it cannot start."""
    exe = find_chromium()
    if not exe:
        log.warning("no Chromium browser found for a window of its own")
        return False
    command = [exe, f"--app={url}", f"--user-data-dir={profile_dir}", f"--window-size={WIDTH},{HEIGHT}",
               "--no-first-run", "--no-default-browser-check"]
    try:
        process = subprocess.Popen(command)
    except OSError as exc:
        log.warning("could not start %s (%s)", os.path.basename(exe), exc)
        return False
    log.info("showing the window in %s (--app mode)", os.path.basename(exe))
    process.wait()
    return True


def run_window(url: str, profile_dir: Path) -> int:
    """The window process (`--window <url>`): shows `url` in a pywebview window; if pywebview cannot
    load or start, in a Chromium `--app` window with its own profile in `profile_dir`; failing that,
    in the default browser. Returns 0 once the window is closed. Logs to stderr only: the app's
    rotating log file belongs to the main process."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if not _show_in_pywebview(url) and not _show_in_chromium(url, profile_dir):
        webbrowser.open(url)
    return 0


# --- the main process's side ------------------------------------------------------------------------------


class WindowLauncher:
    """One window process at a time. While one is alive, `open` only records a request for a page: the
    page the window shows asks `current` (through `/api/window`) every 2 s and switches when the
    request is newer than the one it loaded with."""

    def __init__(self, base_url: str, popen: Callable[..., subprocess.Popen] = subprocess.Popen):
        self._base_url = base_url
        self._popen = popen
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._seq = 0
        self._page = "/status"

    def open(self, page: str) -> None:
        """Starts the window process on `page`, or, while one is alive, asks it to show `page`."""
        with self._lock:
            self._page = page
            if self._process is not None and self._process.poll() is None:
                self._seq += 1
                return
            try:
                self._process = self._popen(window_command(self._base_url + page),
                                            creationflags=subprocess.CREATE_NO_WINDOW)
            except OSError:
                log.exception("could not start the window")
                self._process = None
            else:
                winjob.guard(self._process)      # so it ends with the app, even one ended from Task Manager

    def current(self) -> dict:
        """The latest request: `seq` grows with every request made to a window that was already open."""
        with self._lock:
            return {"seq": self._seq, "page": self._page}

    def close(self) -> None:
        """Ends the window process, if it is alive (the app is quitting)."""
        with self._lock:
            process, self._process = self._process, None
        if process is not None and process.poll() is None:
            process.terminate()
