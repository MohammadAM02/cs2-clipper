"""Where CS2 is, and the folders next to it that a recording through HLAE uses.

Finds cs2.exe in Steam's libraries the way CS Demo Manager 3.20.1 does on Windows (``get-steam-folder-path.ts``,
``get-csgo-folder-path.ts``): Steam's own folder from the registry and the others from ``libraryfolders.vdf``.
Only reads: it launches nothing and writes nothing.

The MIT License (MIT)

Copyright (c) 2014-present AkiVer

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

import re
from collections.abc import Callable
from pathlib import Path

# Where Steam puts CS2 inside a library.
_CS2_IN_LIBRARY = Path("steamapps/common/Counter-Strike Global Offensive/game/bin/win64/cs2.exe")

# The pieces of a Valve text file (VDF) that matter here: a quoted string, which has `\\` and `\"` inside, and a brace.
_VDF_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def parse_library_folders(text: str) -> list[Path]:
    """The ``path`` of every library in the text of Steam's ``libraryfolders.vdf``, in file order, with the
    doubled backslashes of the file undoubled."""
    libraries: list[Path] = []
    key: str | None = None
    for token in _VDF_TOKEN.finditer(text):
        if token[2]:        # a brace: the string before it named a section
            key = None
        elif key is None:
            key = token[1]
        else:
            if key.lower() == "path" and token[1]:
                libraries.append(Path(token[1].replace("\\\\", "\\")))
            key = None
    return libraries


def registry_steam_path() -> str | None:
    """Steam's folder as the registry spells it (``c:/program files (x86)/steam``), or None when the current
    user has no Steam."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "SteamPath")
    except OSError:
        return None
    return value if isinstance(value, str) and value else None


def steam_folder(read: Callable[[], str | None] = registry_steam_path) -> Path | None:
    """Steam's folder, spelled the way Windows does (CS:DM's ``sanitizePathFromRegistry``): a capital drive
    letter and backslashes. None when ``read`` finds none."""
    path = read()
    if not path:
        return None
    return Path(path[0].upper() + path[1:].replace("/", "\\"))


def _libraries(steam: Path) -> list[Path]:
    """The libraries ``libraryfolders.vdf`` names, in its order, and then Steam's own folder when it is not
    one of them."""
    try:
        text = (steam / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    libraries = parse_library_folders(text)
    return libraries if steam in libraries else [*libraries, steam]


def find_cs2_exe(*, steam_folder: Callable[[], Path | None] = steam_folder) -> Path | None:
    """cs2.exe, or None when there is none to be found: the first Steam library that has it. ``steam_folder``
    gives Steam's folder, by default as the registry has it."""
    steam = steam_folder()
    if steam is None:
        return None
    for library in _libraries(steam):
        exe = library / _CS2_IN_LIBRARY
        if exe.is_file():
            return exe
    return None


def csgo_dir(cs2_exe: Path) -> Path:
    """CS2's ``csgo`` folder, the one next to ``bin`` (cs2.exe is in ``bin/win64``). Its ``cfg`` folder holds
    the cfg files CS2 execs and CS2 writes ``console.log`` into it."""
    return cs2_exe.parents[2] / "csgo"


def cfg_dir(cs2_exe: Path) -> Path:
    """Where CS2 looks for the cfg files that ``+exec`` runs."""
    return csgo_dir(cs2_exe) / "cfg"


def console_log(cs2_exe: Path) -> Path:
    """What CS2 started with ``-condebug`` writes its console to."""
    return csgo_dir(cs2_exe) / "console.log"
