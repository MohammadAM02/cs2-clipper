"""CS:DM's settings file (``<csdm home>/.csdm/settings.json``), as setup writes it.

CS:DM reads this file as it is and fills nothing in from its defaults (3.20.1, ``get-settings.ts``), so
a file holding only the database connection makes ``csdm video`` crash on the missing ``video``
section. A file that is missing, or that has no ``schemaVersion`` (what the old
``scripts/setup_local_postgres.py`` wrote on a fresh home), is therefore started from ``TEMPLATE``:
CS:DM 3.20.1's defaults, changed only where the render needs it. A newer CS:DM upgrades a complete
file through its own migrations. A file that has a ``schemaVersion`` is CS:DM's own: only the keys
asked for are changed.

The file holds the database password and API keys: nothing here logs a value from it, and no error
message carries one."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from clipper import paths


class SettingsError(Exception):
    """CS:DM's settings file is there but cannot be used."""


_KILLS = {"beforeKillDelayInSeconds": 5, "afterKillDelayInSeconds": 2, "includeDamages": False}
_DOWNLOADS = ("Valve", "Faceit", "5EPlay", "Renown")

# `defaultSettings` of CS:DM 3.20.1 (src/node/settings/default-settings.ts, schema 13) without its
# `database` section, which setup adds once it has made the database. Not CS:DM's defaults: recording
# through HLAE into mp4 at 60 fps and 1920x1080, which is what the render expects.
TEMPLATE: dict = {
    "schemaVersion": 13,
    "autoDownloadUpdates": True,
    "folders": [],
    "autoExtractDemosFromArchives": [],
    "demos": {"showAllFolders": False, "currentFolderPath": "", "sources": [], "tagIds": [], "types": [],
              "games": [], "analysisStatus": "all"},
    "pinnedPlayerSteamId": "",
    "steamApiKey": "",
    "faceitApiKey": "",
    "ui": {"locale": "en", "theme": "dark", "initialPage": "matches", "redirectDemoToMatch": False,
           "enableHardwareAcceleration": True},
    "analyze": {"maxConcurrentAnalyses": 4, "analyzePositions": False},
    "playback": {
        "width": 1024,
        "height": 768,
        "displayMode": "windowed",
        "useCustomHighlights": True,
        "useCustomLowlights": True,
        "highlights": dict(_KILLS),
        "lowlights": dict(_KILLS),
        "round": {"beforeRoundDelayInSeconds": 0, "afterRoundDelayInSeconds": 2, "waitForRoundEnd": False},
        "launchParameters": "",
        "useHlae": False,
        "playerVoicesEnabled": True,
        "followSymbolicLinks": False,
        "customCs2SteamRuntimeScriptLocationEnabled": False,
        "cs2SteamRuntimeScriptPath": "",
    },
    "video": {
        "recordingSystem": "HLAE",
        "recordingOutput": "video",
        "closeGameAfterRecording": True,
        "concatenateSequences": False,
        "outputFileName": "",
        "showXRay": True,
        "showAssists": True,
        "showOnlyDeathNotices": True,
        "deathNoticesDuration": 5,
        "playerVoicesEnabled": True,
        "recordAudio": True,
        "trueView": False,
        "encoderSoftware": "FFmpeg",
        "ffmpegSettings": {
            "audioBitrate": 256,
            "constantRateFactor": 23,
            "customLocationEnabled": False,
            "customExecutableLocation": "",
            "videoContainer": "mp4",
            "videoCodec": "libx264",
            "audioCodec": "libmp3lame",
            "inputParameters": "",
            "outputParameters": "",
        },
        "framerate": 60,
        "height": 1080,
        "width": 1920,
        "outputFolderPath": "",
        "hlae": {"customLocationEnabled": False, "customExecutableLocation": "", "configFolderEnabled": False,
                 "configFolderPath": ""},
    },
    "playerProfile": {"gameModes": [], "games": [], "demoSources": [], "demoTypes": [], "ranking": "all",
                      "tagIds": [], "maxRounds": []},
    "download": {f"download{source}Demos{when}": True for source in _DOWNLOADS
                 for when in ("AtStartup", "InBackground")},
    "matches": {"gameModes": [], "demoTypes": [], "demoSources": [], "games": [], "ranking": "all", "tagIds": [],
                "maxRounds": []},
    "players": {"bans": [], "tagIds": []},
    "teams": {},
    "teamProfile": {"gameModes": [], "games": [], "demoSources": [], "demoTypes": [], "tagIds": [],
                    "maxRounds": []},
    "ban": {"ignoreBanBeforeFirstSeen": True},
}


def settings_file(csdm_home: Path) -> Path:
    return csdm_home / ".csdm" / "settings.json"


def read(csdm_home: Path) -> dict | None:
    """The settings, or None when there is no file. Raises SettingsError when the file cannot be read
    as a JSON object."""
    path = settings_file(csdm_home)
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise SettingsError(f"CS Demo Manager's settings file cannot be read: {path}") from exc
    if not isinstance(settings, dict):
        raise SettingsError(f"CS Demo Manager's settings file cannot be read: {path}")
    return settings


def _merged(base: dict, over: dict) -> dict:
    """`base` with `over` laid on top: sections merge key by key, anything else is replaced."""
    result = copy.deepcopy(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merged(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def update(csdm_home: Path, changes: dict) -> dict:
    """Lays `changes` over the settings and writes them, the way CS:DM does (two-space JSON). A missing
    file, or one without a `schemaVersion`, is filled in from TEMPLATE first. Returns what was written."""
    current = read(csdm_home) or {}
    base = current if "schemaVersion" in current else _merged(TEMPLATE, current)
    settings = _merged(base, changes)
    path = settings_file(csdm_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    paths.atomic_write_text(path, json.dumps(settings, indent=2))
    return settings


def _section(csdm_home: Path, *names: str) -> dict:
    """A section of the settings, or {} when the file, or the section, is missing or unreadable."""
    try:
        node = read(csdm_home) or {}
    except SettingsError:
        return {}
    for name in names:
        node = node.get(name) if isinstance(node, dict) else None
    return node if isinstance(node, dict) else {}


def database(csdm_home: Path) -> dict | None:
    """The database connection, when the settings name one with a password and a port; else None."""
    block = _section(csdm_home, "database")
    port, password = block.get("port"), block.get("password")
    if isinstance(port, int) and not isinstance(port, bool) and isinstance(password, str) and password:
        return block
    return None


def ffmpeg_exe(csdm_home: Path) -> Path:
    """Where CS:DM looks for FFmpeg: its custom location when that is enabled and set, else the copy
    in its own folder (3.20.1, ``ffmpeg-location.ts``)."""
    ffmpeg = _section(csdm_home, "video", "ffmpegSettings")
    custom = ffmpeg.get("customExecutableLocation")
    if ffmpeg.get("customLocationEnabled") and isinstance(custom, str) and custom:
        return Path(custom)
    return csdm_home / ".csdm" / "ffmpeg" / "bin" / "ffmpeg.exe"
