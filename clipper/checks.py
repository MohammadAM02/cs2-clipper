"""Status's checks and the start-up problems (spec: Pages -> Status (Checks); When something goes
wrong; Testing, checks and start-up problems).

CS:DM's settings.json holds passwords and API keys (spec: Settings and data): this module reads
only ``video.hlae`` from it, and never logs or returns anything else it contains.
"""

from __future__ import annotations

import ctypes
import json
import logging
import shutil
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

import psutil

from clipper import postgres
from clipper.config import Config

log = logging.getLogger(__name__)

GB = 1024**3
ADVANCEDFX_RELEASES_URL = "https://api.github.com/repos/advancedfx/advancedfx/releases/latest"
DAILY_SECONDS = 24 * 60 * 60.0
RETRY_AFTER_FAILURE_SECONDS = 60 * 60.0


@dataclass(frozen=True)
class Check:
    name: str            # "CS Demo Manager" | "Postgres" | "HLAE" | "FFmpeg" | "Clips folder" | "Match alerts"
    ok: bool | None      # None: could not tell
    detail: str          # one line, e.g. "3.20.1", "running", "2.192.6 (latest)"
    hint: str = ""       # one line: how to fix it, when not ok


# --- reading version information off disk -------------------------------------------------------


class _FixedFileInfo(ctypes.Structure):
    _fields_ = [
        ("signature", wintypes.DWORD),
        ("struc_version", wintypes.DWORD),
        ("file_version_ms", wintypes.DWORD),
        ("file_version_ls", wintypes.DWORD),
        ("product_version_ms", wintypes.DWORD),
        ("product_version_ls", wintypes.DWORD),
        ("file_flags_mask", wintypes.DWORD),
        ("file_flags", wintypes.DWORD),
        ("file_os", wintypes.DWORD),
        ("file_type", wintypes.DWORD),
        ("file_subtype", wintypes.DWORD),
        ("file_date_ms", wintypes.DWORD),
        ("file_date_ls", wintypes.DWORD),
    ]


def file_version(path: Path) -> str | None:
    """The exe's version resource via ctypes: "major.minor.build", plus ".revision" only when the
    revision is not 0. None when there is no version resource, the file has none, or it can't be
    read (a missing file included). No-op off Windows."""
    if sys.platform != "win32":
        return None
    name = str(path)
    version_dll = ctypes.WinDLL("version", use_last_error=True)
    version_dll.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, wintypes.LPDWORD]
    version_dll.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    size = version_dll.GetFileVersionInfoSizeW(name, None)
    if not size:
        return None
    version_dll.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID]
    version_dll.GetFileVersionInfoW.restype = wintypes.BOOL
    buf = ctypes.create_string_buffer(size)
    if not version_dll.GetFileVersionInfoW(name, 0, size, buf):
        return None
    version_dll.VerQueryValueW.argtypes = [
        wintypes.LPCVOID, wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPVOID), wintypes.PUINT,
    ]
    version_dll.VerQueryValueW.restype = wintypes.BOOL
    value = wintypes.LPVOID()
    length = wintypes.UINT()
    if not version_dll.VerQueryValueW(buf, "\\", ctypes.byref(value), ctypes.byref(length)):
        return None
    if not value.value or not length.value:
        return None
    info = ctypes.cast(value, ctypes.POINTER(_FixedFileInfo)).contents
    major, minor = info.file_version_ms >> 16, info.file_version_ms & 0xFFFF
    build, revision = info.file_version_ls >> 16, info.file_version_ls & 0xFFFF
    text = f"{major}.{minor}.{build}"
    return f"{text}.{revision}" if revision else text


def hlae_exe(csdm_home: Path) -> Path | None:
    """Where CS:DM's video.hlae settings say HLAE lives: the custom location when it is enabled and
    set, else CS:DM's own bundled copy (<csdm_home>/.csdm/hlae/HLAE.exe). None when CS:DM's
    settings.json is missing or is not readable JSON."""
    settings_path = csdm_home / ".csdm" / "settings.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    hlae = settings.get("video", {}).get("hlae", {}) if isinstance(settings, dict) else {}
    custom = hlae.get("customExecutableLocation") if isinstance(hlae, dict) else None
    if isinstance(hlae, dict) and hlae.get("customLocationEnabled") and custom:
        return Path(custom)
    return csdm_home / ".csdm" / "hlae" / "HLAE.exe"


def hlae_version(exe: Path) -> str | None:
    """The first <release><version> of changelog.xml beside `exe` (HLAE lists releases newest
    first). None when the changelog is missing, malformed, or has no version there."""
    changelog = exe.with_name("changelog.xml")
    try:
        root = ET.parse(changelog).getroot()
    except (OSError, ET.ParseError):
        return None
    release = root.find("release")
    if release is None:
        return None
    version = release.findtext("version")
    return version.strip() if version and version.strip() else None


def _fetch_release_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "cs2-clipper"})
    with urllib.request.urlopen(request, timeout=10.0) as response:
        return json.loads(response.read())


def latest_hlae_release(fetch_json: Callable[[str], dict] = _fetch_release_json) -> str:
    """The newest advancedfx release's tag (e.g. "2.192.6"), without a leading "v". Raises
    OSError (no network; urllib.error.URLError is one), ValueError (not valid JSON), or KeyError
    (no "tag_name") when it can't be had."""
    data = fetch_json(ADVANCEDFX_RELEASES_URL)
    tag = data["tag_name"]
    return tag[1:] if tag.startswith("v") else tag


class HlaeReleases:
    """The newest advancedfx release: asked at start-up and then daily (an hour after a failure)."""

    def __init__(self, fetch: Callable[[], str] = latest_hlae_release, clock: Callable[[], float] = time.time):
        self._fetch = fetch
        self._clock = clock
        self._latest: str | None = None
        self._failed = False
        self._next_at = clock()   # due immediately, at start-up

    def refresh_if_due(self) -> None:
        """Never raises: a failed fetch just sets `failed` and tries again in an hour."""
        now = self._clock()
        if now < self._next_at:
            return
        try:
            self._latest = self._fetch()
            self._failed = False
            self._next_at = now + DAILY_SECONDS
        except Exception as exc:  # noqa: BLE001 - a release check must never stop the app
            log.warning("could not check the latest HLAE release (%s); trying again in an hour", exc)
            self._failed = True
            self._next_at = now + RETRY_AFTER_FAILURE_SECONDS

    @property
    def latest(self) -> str | None:
        return self._latest

    @property
    def failed(self) -> bool:
        return self._failed


def _version_tuple(text: str) -> tuple[int, ...]:
    parts = []
    for piece in text.split("."):
        try:
            parts.append(int(piece))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _is_newer(candidate: str, than: str) -> bool:
    return _version_tuple(candidate) > _version_tuple(than)


def _free_bytes(path: Path) -> int:
    return psutil.disk_usage(str(path)).free


# --- the six checks -------------------------------------------------------------------------------


def _csdm_check(cfg: Config, version_of: Callable[[Path], str | None]) -> Check:
    if not cfg.csdm_exe.exists():
        return Check("CS Demo Manager", False, "not found",
                     "Install CS Demo Manager, or set its folder in Settings")
    return Check("CS Demo Manager", True, version_of(cfg.csdm_exe) or "found")


def _postgres_check(cfg: Config, pg_running: Callable[[Path, Path], bool]) -> Check:
    try:
        running = pg_running(cfg.pg_bin, cfg.pg_data)
    except Exception as exc:  # noqa: BLE001 - a check that raises counts as not running
        log.warning("could not tell whether Postgres is running (%s)", exc)
        running = False
    if running:
        return Check("Postgres", True, "running")
    return Check("Postgres", False, "not running",
                 "The app starts it when it can; check the Postgres folders in Settings")


def _hlae_check(cfg: Config, releases: HlaeReleases) -> Check:
    exe = hlae_exe(cfg.csdm_home)
    if exe is None or not exe.exists():
        return Check("HLAE", False, "not found", "Install HLAE from CS Demo Manager's video settings")
    installed = hlae_version(exe)
    if installed is None:
        return Check("HLAE", None, "unknown version")
    if releases.latest is None:
        return Check("HLAE", None, f"{installed} (couldn't check for a newer release)")
    if _is_newer(releases.latest, installed):
        return Check("HLAE", False, f"{installed} — {releases.latest} is out",
                     "Update HLAE: renders fail when HLAE is older than CS2")
    return Check("HLAE", True, f"{installed} (latest)")


def _ffmpeg_check(cfg: Config, which: Callable[[str], str | None]) -> Check:
    names = {"ffmpeg": cfg.ffmpeg, "ffprobe": cfg.ffprobe}
    missing = [label for label, name in names.items() if which(name) is None and not Path(name).exists()]
    if not missing:
        return Check("FFmpeg", True, "found")
    return Check("FFmpeg", False, f"{' and '.join(missing)} not found",
                 "Install FFmpeg, or set its path in Settings")


def _clips_folder_check(cfg: Config, free_bytes: Callable[[Path], int]) -> Check:
    if cfg.data_root is None:
        return Check("Clips folder", False, "not set", "Choose a clips folder in Settings")
    if not cfg.data_root.is_dir():
        return Check("Clips folder", False, f"{cfg.data_root} is missing")
    free = free_bytes(cfg.data_root)
    detail = f"{cfg.data_root} · {free // GB} GB free"
    if free >= cfg.min_free_gb * GB:
        return Check("Clips folder", True, detail)
    return Check("Clips folder", False, detail, f"Rendering waits until {cfg.min_free_gb:g} GB are free")


def _match_alerts_check(alerts_status: str | None) -> Check:
    if alerts_status is None:
        return Check("Match alerts", None, "not started")
    if alerts_status == "on":
        return Check("Match alerts", True, "on")
    return Check("Match alerts", False, alerts_status.removeprefix("off: "))


def run_checks(cfg: Config, *, alerts_status: str | None, releases: HlaeReleases,
               pg_running: Callable[[Path, Path], bool] = postgres.is_running,
               which: Callable[[str], str | None] = shutil.which,
               free_bytes: Callable[[Path], int] = _free_bytes,
               version_of: Callable[[Path], str | None] = file_version) -> list[Check]:
    """The six checks Status shows, in order. Does not call `releases.refresh_if_due` — the app
    does that off the request path, daily."""
    return [
        _csdm_check(cfg, version_of),
        _postgres_check(cfg, pg_running),
        _hlae_check(cfg, releases),
        _ffmpeg_check(cfg, which),
        _clips_folder_check(cfg, free_bytes),
        _match_alerts_check(alerts_status),
    ]


# --- start-up problems -----------------------------------------------------------------------------


def startup_problems(cfg: Config, *,
                     ensure_postgres: Callable[[Path, Path], None] = postgres.ensure_running) -> list[str]:
    """What keeps the worker from doing anything, in order; an empty list means it may run.
    `ensure_postgres` is always attempted, regardless of earlier problems."""
    problems = []
    if not cfg.subject_steamid:
        problems.append("Set your SteamID in Settings")
    if cfg.data_root is None:
        problems.append("Choose a clips folder in Settings")
    elif not cfg.data_root.is_dir():
        problems.append(f"The clips folder {cfg.data_root} is missing")
    if not cfg.csdm_exe.exists():
        problems.append(f"CS Demo Manager was not found in {cfg.csdm_app_dir}")
    if not (cfg.csdm_home / ".csdm" / "settings.json").exists():
        problems.append(f"CS Demo Manager's settings are missing from {cfg.csdm_home}")
    try:
        ensure_postgres(cfg.pg_bin, cfg.pg_data)
    except Exception as exc:  # noqa: BLE001 - any failure is reported, never crashes the app
        problems.append(f"Postgres won't start: {exc}")
    return problems
