"""Status's checks and the start-up problems (spec: Pages -> Status (Checks); When something goes
wrong; Testing, checks and start-up problems)."""

from __future__ import annotations

import json
import logging
import shutil
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import psutil

from clipper import download
from clipper.config import Config

log = logging.getLogger(__name__)

GB = 1024**3
ADVANCEDFX_RELEASES_URL = "https://api.github.com/repos/advancedfx/advancedfx/releases/latest"
DAILY_SECONDS = 24 * 60 * 60.0
RETRY_AFTER_FAILURE_SECONDS = 60 * 60.0


@dataclass(frozen=True)
class Check:
    name: str            # "csda" | "HLAE" | "FFmpeg" | "Clips folder" | "Match alerts"
    ok: bool | None      # None: could not tell
    detail: str          # one line, e.g. "found", "2.192.6 (latest)"
    hint: str = ""       # one line: how to fix it, when not ok


# --- HLAE: where it is, and whether a newer one is out -------------------------------------------


def hlae_exe(cfg: Config) -> Path | None:
    """HLAE.exe (the ``hlae_exe`` setting, else the one Setup installs), or None when there is no such
    file. A path that is set is never swapped for the installed one."""
    exe = cfg.hlae_path
    return exe if exe.is_file() else None


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
    request = urllib.request.Request(url, headers=download.headers_for(url))
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


def hlae_behind(cfg: Config, latest: str | None) -> bool:
    """Whether the HLAE the app uses is older than `latest`. False when either version is unknown."""
    exe = hlae_exe(cfg)
    installed = hlae_version(exe) if exe is not None else None
    return installed is not None and latest is not None and _is_newer(latest, installed)


def _free_bytes(path: Path) -> int:
    return psutil.disk_usage(str(path)).free


# --- the five checks ------------------------------------------------------------------------------


def _csda_check(cfg: Config) -> Check:
    if not cfg.csda_exe.is_file():
        return Check("csda", False, "not found", "Set up this PC to install it")
    return Check("csda", True, "found")


def _hlae_check(cfg: Config, releases: HlaeReleases) -> Check:
    exe = hlae_exe(cfg)
    if exe is None:
        return Check("HLAE", False, "not found", "Set up this PC to install it, or set its path in Settings")
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
               which: Callable[[str], str | None] = shutil.which,
               free_bytes: Callable[[Path], int] = _free_bytes) -> list[Check]:
    """The five checks Status shows, in order. Does not call `releases.refresh_if_due` — the app
    does that off the request path, daily."""
    return [
        _csda_check(cfg),
        _hlae_check(cfg, releases),
        _ffmpeg_check(cfg, which),
        _clips_folder_check(cfg, free_bytes),
        _match_alerts_check(alerts_status),
    ]


# --- start-up problems -----------------------------------------------------------------------------


def startup_problems(cfg: Config) -> list[str]:
    """What keeps the worker from doing anything, in order; an empty list means it may run. No HLAE is
    not one: without it the app still downloads, analyzes and scores, and each render says why it fails."""
    problems = []
    if not cfg.subject_steamid:
        problems.append("Set your SteamID in Settings")
    if cfg.data_root is None:
        problems.append("Choose a clips folder in Settings")
    elif not cfg.data_root.is_dir():
        problems.append(f"The clips folder {cfg.data_root} is missing")
    if not cfg.csda_exe.is_file():
        problems.append(f"csda was not found in {cfg.csda_exe.parent}")
    return problems
