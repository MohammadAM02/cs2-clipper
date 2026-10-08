"""The one-time copy from the repo into the app data folder (spec: Settings and data, Moving in from
the repo). Runs at start, before anything else reads settings or the index: when the app data folder
has no settings.json yet and the old repo-based setup exists, it is copied in (never moved, never
deleted) so the app keeps working the way it did before it became a desktop app.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import tomllib
from pathlib import Path

from clipper import packaged, paths, protect
from clipper.config import REPO_ROOT
from clipper.settings import STORED_KEY, defaults, differs_from_default

log = logging.getLogger(__name__)

OLD_STEAMID = "76561198192858303"     # the old built-in default subject_steamid
OLD_CLIPS_FOLDER = "E:/cs2clips"      # the old built-in default data_root
SHIPPED_ENV = "shipped.env"           # the key a packaged build ships (packaging/build.ps1 writes it)


def needed(repo_root: Path, data_dir: Path) -> bool:
    """True when `data_dir` has no settings.json yet and the repo holds an old setup: any of
    clipper.toml, .env, data\\clipper.sqlite exists. A fresh clone has none of them, so nothing is
    moved in. CS Demo Manager's old settings folder, home\\, is not one: nothing is taken from it."""
    if (data_dir / "settings.json").exists():
        return False
    return any((
        (repo_root / "clipper.toml").exists(),
        (repo_root / ".env").exists(),
        (repo_root / "data" / "clipper.sqlite").exists(),
    ))


def move_in(repo_root: Path, data_dir: Path) -> list[str]:
    """Copies the repo's old setup into `data_dir` (never moves, never deletes the originals).
    Returns a short description of each thing copied."""
    copied: list[str] = []
    if _copy_index(repo_root, data_dir):
        copied.append("the index")

    settings_out, key_copied = _collect_settings(repo_root)
    paths.atomic_write_text(data_dir / "settings.json", json.dumps(settings_out, indent=2, sort_keys=True))
    if settings_out:
        copied.append("the settings")
    if key_copied:
        copied.append("the FACEIT key")

    if copied:
        log.info("moved in from %s: %s", repo_root, ", ".join(copied))
    return copied


def on_start(repo_root: Path = REPO_ROOT) -> list[str]:
    """The call site: [] when CLIPPER_DATA_DIR is set (tests and development never copy the real
    .env key or index), or when there is nothing to move in; else the result of move_in().

    In the packaged exe `repo_root` is the folder the exe unpacked into, which is not a repository: it
    has no old setup to move in, and the only thing it may seed is the key the build shipped -- see
    `_seed_shipped`."""
    if paths.overridden():
        return []
    data_dir = paths.data_dir()
    if packaged.frozen():
        return _seed_shipped(repo_root, data_dir)
    if not needed(repo_root, data_dir):
        return []
    return move_in(repo_root, data_dir)


def _seed_shipped(repo_root: Path, data_dir: Path) -> list[str]:
    """The packaged app's first run: the FACEIT key the build shipped, written into settings.json for
    the Windows account that runs it on that machine. Nothing else is copied -- the bundle is not a
    repo, so there is no index to take, and no SteamID or clips folder from the machine that built it
    (`old_defaults=False`)."""
    if (data_dir / "settings.json").exists() or not (repo_root / SHIPPED_ENV).is_file():
        return []
    settings_out, key_copied = _collect_settings(repo_root, old_defaults=False)
    paths.atomic_write_text(data_dir / "settings.json",
                            json.dumps(settings_out, indent=2, sort_keys=True))
    copied = (["the settings"] if settings_out else []) + (["the FACEIT key"] if key_copied else [])
    if copied:
        log.info("seeded from %s: %s", repo_root / SHIPPED_ENV, ", ".join(copied))
    return copied


# --- the index --------------------------------------------------------------------------------------------


def _copy_index(repo_root: Path, data_dir: Path) -> bool:
    """All-or-nothing: copies into `clipper.sqlite.partial`, then `os.replace`s it onto the final
    name (atomic on both Windows and POSIX), so a crash or a full disk mid-copy can never leave a
    half-written `clipper.sqlite` and can never lose an existing one being replaced."""
    src = repo_root / "data" / "clipper.sqlite"
    if not src.is_file():
        return False
    dest = data_dir / "clipper.sqlite"
    if dest.exists() and not _index_is_empty(dest):
        return False
    partial = data_dir / "clipper.sqlite.partial"
    shutil.copy2(src, partial)
    os.replace(partial, dest)
    return True


def _index_is_empty(path: Path) -> bool:
    """Whether an existing index holds no rows in `demos` or `faceit_matches` (a missing table counts
    as empty too): safe to replace with the repo's copy. An index that cannot be read counts as
    not-empty, so it is left alone."""
    try:
        conn = sqlite3.connect(str(path))
    except sqlite3.Error:
        return False
    try:
        for table in ("demos", "faceit_matches"):
            has_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()
            if has_table and conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                return False
        return True
    except sqlite3.Error:
        return False
    finally:
        conn.close()


# --- settings.json ------------------------------------------------------------------------------------------


def _collect_settings(repo_root: Path, old_defaults: bool = True) -> tuple[dict[str, object], bool]:
    """The settings.json content to write, and whether it holds the FACEIT key. `old_defaults` is
    False for the packaged app, which has no old setup: its `shipped.env` holds a key and nothing
    else, so this developer's SteamID and clips folder stay out of someone else's install."""
    toml_values = _load_toml(repo_root / "clipper.toml")
    defaults_ = defaults()
    out: dict[str, object] = {
        name: value for name, value in toml_values.items()
        if name in defaults_ and differs_from_default(name, value, defaults_[name])
    }
    if old_defaults:
        if "subject_steamid" not in toml_values:
            out["subject_steamid"] = OLD_STEAMID
        if "data_root" not in toml_values:
            out["data_root"] = OLD_CLIPS_FOLDER

    # The packaged app has no .env of its own: what the build shipped stands in for it.
    env_values = _load_env(repo_root / ".env") or _load_env(repo_root / SHIPPED_ENV)
    nickname = env_values.get("FACEIT_NICKNAME", "")
    if nickname:
        out["faceit_nickname"] = nickname
    api_key = env_values.get("FACEIT_API_KEY", "")
    key_copied = bool(api_key)
    if key_copied:
        out[STORED_KEY] = protect.protect(api_key)

    return out, key_copied


def _load_toml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _load_env(path: Path) -> dict[str, str]:
    """KEY=value lines of a .env file. Comments, blank lines and lines without '=' are skipped;
    quotes around a value are dropped. A missing file gives no values."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values
