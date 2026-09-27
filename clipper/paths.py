"""The app data folder (spec: Settings and data, The app data folder).

``%LOCALAPPDATA%\\CS2Clipper\\`` holds: ``settings.json`` (what you changed; defaults fill in the
rest), ``clipper.sqlite`` (the index), ``clipper.lock`` (single instance), ``logs\\`` (clipper.log
and one log per csdm call) and ``csdm-home\\`` (USERPROFILE for csdm processes, so CS:DM's settings
are ``csdm-home\\.csdm\\settings.json``). The clips folder (Demos, renders, Reels) is unchanged and
lives elsewhere.

It is ``%LOCALAPPDATA%``, not Aegis's roaming ``%APPDATA%``, because this folder holds this PC's
paths and databases, not things that should follow you to another machine. ``CLIPPER_DATA_DIR``
overrides it, for tests and development.

Adapted from thelifeofsuleyman/cs2-clipper's ``aegis/paths.py`` (MIT): ``data_root()`` becomes
``data_dir()``, the env var and app folder are ours, Aegis's per-purpose subfolders (thumbnails,
montages, clips, buffer, avatars) are replaced by this app's own layout, and ``overridden()`` is
added.

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

import os
from pathlib import Path

ENV_VAR = "CLIPPER_DATA_DIR"
APP_FOLDER = "CS2Clipper"


def data_dir() -> Path:
    """``CLIPPER_DATA_DIR`` if set to a non-empty value, else ``%LOCALAPPDATA%\\CS2Clipper``
    (``LOCALAPPDATA`` falling back to ``~/AppData/Local``). Created if missing."""
    override = os.environ.get(ENV_VAR)
    if override:
        path = Path(override)
    else:
        local_appdata = os.environ.get("LOCALAPPDATA")
        base = Path(local_appdata) if local_appdata else Path.home() / "AppData" / "Local"
        path = base / APP_FOLDER
    path.mkdir(parents=True, exist_ok=True)
    return path


def overridden() -> bool:
    """Whether ``CLIPPER_DATA_DIR`` is set to a non-empty value."""
    return bool(os.environ.get(ENV_VAR))


def settings_file() -> Path:
    return data_dir() / "settings.json"


def index_file() -> Path:
    return data_dir() / "clipper.sqlite"


def lock_file() -> Path:
    return data_dir() / "clipper.lock"


def logs_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def csdm_home() -> Path:
    """USERPROFILE for csdm processes. Not created here: a missing CS:DM settings folder must stay
    visible as missing, rather than looking like a fresh empty one."""
    return data_dir() / "csdm-home"


def atomic_write_text(path: Path, text: str) -> None:
    """Write text without risk of leaving a half-written file: write ``<name>.tmp`` beside it, then
    ``os.replace()``, which is atomic on both Windows and POSIX."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
