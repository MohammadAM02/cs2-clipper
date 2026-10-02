"""The packaged app: CS2Clipper.exe, one file built by PyInstaller (packaging\\cs2clipper.spec).

The exe unpacks itself into a temporary folder on every start and runs from there, so nothing in it
can lean on the repo: ``REPO_ROOT`` is that folder, and there is nothing to move in from. Two more
things only the exe does: it takes that folder back off the DLL search path its children inherit, and
the build runs ``CS2Clipper.exe --bundle-check <report>`` to find what PyInstaller left out, since the
app loads some modules and files only once it is running.
"""
from __future__ import annotations

import ctypes
import io
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import psutil

Check = tuple[str, Callable[[], object]]

PAGE_FILES = ("status.html", "demos.html", "reels.html", "settings.html", "app.css", "app.js")


def frozen() -> bool:
    """Whether this is the packaged exe rather than Python running the repo's source."""
    return bool(getattr(sys, "frozen", False))


def release_dll_directory() -> None:
    """In the exe, takes its unpacked bundle back off the DLL search path. PyInstaller's bootloader puts
    it there (SetDllDirectoryW) and every process the app starts inherits it, so csdm, HLAE, CS2,
    Postgres and ffmpeg would load the bundle's copies of DLLs such as vcruntime140.dll before their
    own. The window process keeps it: pywebview loads .NET and WebView2 from the bundle."""
    if frozen():
        ctypes.windll.kernel32.SetDllDirectoryW(None)


def other_copies() -> list[psutil.Process]:
    """The other processes running this exe's file; none when this is not the exe. The exe is one file
    that runs as two processes, and the outer one is still clearing away what it unpacked for a moment
    after the app inside it has ended: the file cannot be replaced until that one is gone too."""
    if not frozen():
        return []
    mine = {os.getpid(), os.getppid()}
    exe = os.path.normcase(sys.executable)
    return [process for process in psutil.process_iter(["exe"])
            if process.pid not in mine and os.path.normcase(process.info["exe"] or "") == exe]


# --- the bundle check --------------------------------------------------------------------------------------


def _dll_search_path() -> None:
    """release_dll_directory() worked: the processes the app starts get the default DLL search path."""
    buffer = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetDllDirectoryW(len(buffer), buffer)
    if buffer.value:
        raise RuntimeError(f"the DLL search path still has {buffer.value}")


def _tray() -> None:
    """pystray picks its backend by name when imported, which PyInstaller cannot see."""
    import pystray

    if pystray.Icon.__module__ != "pystray._win32":
        raise RuntimeError(f"pystray's backend is {pystray.Icon.__module__}, not pystray._win32")


def _tray_icon() -> None:
    """pystray hands Windows the mark as a .ico, which Pillow writes."""
    from clipper.icon import mark

    mark(64).save(io.BytesIO(), format="ICO")


def _database() -> None:
    """CS Demo Manager's Postgres: psycopg must use the libpq it brings along, as the PC may have none."""
    import psycopg

    if psycopg.pq.__impl__ != "binary":
        raise RuntimeError(f"psycopg runs on its {psycopg.pq.__impl__} build, not binary")
    psycopg.pq.version()


def _demos() -> None:
    """Downloaded Demos are unpacked with zstandard."""
    import zstandard

    data = b"demo" * 64
    if zstandard.ZstdDecompressor().decompress(zstandard.ZstdCompressor().compress(data)) != data:
        raise RuntimeError("a zstandard round trip changed the data")


def _https() -> None:
    """The FACEIT API is HTTPS: the ssl module and Windows' certificate store."""
    import ssl

    ssl.create_default_context()


def _pages() -> None:
    """The pages are data files, in the exe only when the spec lists them."""
    from clipper import web

    missing = [name for name in PAGE_FILES if not (web.PAGES_DIR / name).is_file()]
    if missing:
        raise FileNotFoundError(f"not in {web.PAGES_DIR}: {', '.join(missing)}")


def _window() -> None:
    """The window process: pywebview on WinForms and WebView2, through pythonnet and .NET."""
    import webview.platforms.edgechromium  # noqa: F401 - loads .NET, WinForms and the WebView2 assemblies
    import webview.platforms.winforms  # noqa: F401
    from webview.util import interop_dll_path

    loader = Path(interop_dll_path("win-x64")) / "WebView2Loader.dll"
    if not loader.is_file():
        raise FileNotFoundError(loader)


def _window_scripts() -> None:
    """pywebview reads its JavaScript from files as each page loads. pywebview's own PyInstaller hook
    brings them; the one in pyinstaller-hooks-contrib would not."""
    from webview.util import get_js_dir

    api = Path(get_js_dir()) / "api.js"
    if not api.is_file():
        raise FileNotFoundError(api)


MAIN_CHECKS: tuple[Check, ...] = (
    ("DLL search path", _dll_search_path),
    ("tray", _tray),
    ("tray icon", _tray_icon),
    ("CS Demo Manager's database", _database),
    ("Demo unpacking", _demos),
    ("HTTPS", _https),
    ("pages", _pages),
)
WINDOW_CHECKS: tuple[Check, ...] = (("window", _window), ("window scripts", _window_scripts))


def check(report: Path, checks: Sequence[Check] = MAIN_CHECKS + WINDOW_CHECKS) -> int:
    """Runs every check, even after one fails, and writes a line for each to `report` as it goes
    ("ok <name>", or "FAIL <name>: <error>"), since the exe has no console. 0 when all pass, else 1.
    The main process's checks come first, before the window's load .NET and WebView2, which the app's
    main process never loads."""
    failed = False
    with report.open("w", encoding="utf-8") as out:
        for name, run in checks:
            try:
                run()
            except Exception as exc:  # noqa: BLE001 - every failure goes in the report
                failed = True
                out.write(f"FAIL {name}: {type(exc).__name__}: {exc}\n")
            else:
                out.write(f"ok {name}\n")
            out.flush()
    return 1 if failed else 0
