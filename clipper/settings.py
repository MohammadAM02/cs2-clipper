"""settings.json: load, validate and save Settings, and watch the file for changes (spec: Settings,
Settings and data). Only values that differ from the defaults are written; a key this app does not
know is kept and ignored; a value that fails validation falls back to its default with a warning. This
is a deliberate difference from Aegis's config.py, which saves every value from its DEFAULTS dict."""

from __future__ import annotations

import base64
import json
import math
import re
import shutil
import threading
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path

from clipper import paths, protect
from clipper.config import RATIOS, SEQUENCE_EVENTS, Config
from clipper.faceit_oauth import RELAY_URL

KEY_FIELD = "faceit_api_key"                  # write-only: never stored or read back under this name
STORED_KEY = "faceit_api_key_protected"       # the DPAPI blob actually stored in settings.json
SECRET_FIELD = "faceit_client_secret"         # the OAuth client secret, write-only like the key
STORED_SECRET = "faceit_client_secret_protected"
SECRETS = {KEY_FIELD: STORED_KEY, SECRET_FIELD: STORED_SECRET}   # write-only name -> stored blob

_APP_DATA_FIELDS = frozenset({"index_path", "csdm_home", "logs_dir"})    # live in the app data folder, not here
_FOLDER_FIELDS = frozenset({"data_root", "downloads_dir", "csdm_app_dir", "pg_bin", "pg_data"})


@dataclass(frozen=True)
class Field:
    name: str                      # the settings.json key (a Config field), or "faceit_api_key" (write-only)
    group: str                     # "You" | "Folders" | "Picture" | "Rendering" | "Match alerts" | "App"
    label: str
    kind: str                      # steamid | text | secret | folder | program | choice | int | number | bool
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    help: str = ""


FIELDS: tuple[Field, ...] = (
    Field("subject_steamid", "You", "SteamID", "steamid",
          help="Your SteamID64: 17 digits starting with 7656119."),
    Field("faceit_nickname", "You", "FACEIT nickname", "text"),
    Field("faceit_oauth_client_id", "You", "FACEIT sign-in client ID", "text",
          help="Your FACEIT app's OAuth2 client ID. With it set, Sign in with FACEIT fills in the "
               "nickname and SteamID."),
    Field("faceit_redirect_uri", "You", "FACEIT redirect URI", "url",
          help="Must match your FACEIT OAuth2 client's Redirect URI exactly. Blank uses "
               f"{RELAY_URL} (FACEIT needs https; that page hands the sign-in back to this app)."),
    Field("faceit_api_key", "You", "FACEIT API key", "secret",
          help="Stored encrypted for your Windows account. It is never shown again."),
    Field(SECRET_FIELD, "You", "FACEIT client secret", "secret",
          help="Your FACEIT OAuth2 client's secret, for the sign-in above. Stored encrypted."),
    Field("data_root", "Folders", "Clips folder", "folder", help="Where Demos, renders and Reels go."),
    Field("downloads_dir", "Folders", "Downloads", "folder"),
    Field("csdm_app_dir", "Folders", "CS Demo Manager", "folder"),
    Field("pg_bin", "Folders", "Postgres programs", "folder"),
    Field("pg_data", "Folders", "Postgres data", "folder"),
    Field("ffmpeg", "Folders", "FFmpeg", "program", help="A program on PATH or a full path."),
    Field("ffprobe", "Folders", "FFprobe", "program", help="A program on PATH or a full path."),
    Field("aspect_ratio", "Picture", "Aspect ratio", "choice", choices=tuple(RATIOS)),
    Field("sequence_event", "Picture", "One Clip per", "choice", choices=SEQUENCE_EVENTS),
    Field("padding_before_s", "Picture", "Seconds before each Frag", "number", minimum=0, maximum=30),
    Field("padding_after_s", "Picture", "Seconds after each Frag", "number", minimum=0, maximum=30),
    Field("top_n", "Rendering", "Highlights per match", "int", minimum=1, maximum=50),
    Field("stall_seconds", "Rendering", "Stall timeout (s)", "number", minimum=30, maximum=3600),
    Field("launch_timeout_seconds", "Rendering", "Launch timeout (s)", "number", minimum=30, maximum=3600),
    Field("heads_up_seconds", "Rendering", "Heads-up before CS2 opens (s)", "number", minimum=0, maximum=600),
    Field("min_free_gb", "Rendering", "Minimum free space (GB)", "number", minimum=0, maximum=10000),
    Field("match_alerts", "Match alerts", "Match alerts", "bool"),
    Field("stopped_playing_minutes", "Match alerts", "Minutes after you stop playing", "number",
          minimum=0, maximum=120),
    Field("page_port", "App", "Port", "int", minimum=1024, maximum=65535, help="Applies at the next start."),
    Field("poll_seconds", "App", "Check for work every (s)", "number", minimum=1, maximum=300),
)

_BY_NAME: dict[str, Field] = {f.name: f for f in FIELDS}
_FIELD_OF_STORED = {stored: field for field, stored in SECRETS.items()}
_FIELD_NAMES = frozenset(_BY_NAME)
_STORED_NAMES = frozenset(f.name for f in fields(Config)) - _APP_DATA_FIELDS
_STEAMID_RE = re.compile(r"^7656119\d{10}$")


@dataclass(frozen=True)
class Loaded:
    config: Config
    warnings: tuple[str, ...]      # one per hand-edited value that fell back to its default, or an unreadable file


# --- defaults -------------------------------------------------------------------------------------------


def defaults() -> dict[str, object]:
    """Every stored setting's default, in JSON form (paths as ``str``, ``data_root`` and
    ``subject_steamid`` as ``""``)."""
    cfg = Config()
    return {name: _to_json(getattr(cfg, name)) for name in _STORED_NAMES}


def _to_json(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, Path):
        return str(value)
    return value


def json_values(cfg: Config) -> dict[str, object]:
    """Every stored setting's effective value on `cfg`, in JSON form (paths as ``str``, ``data_root``
    as ``""`` when unset) -- without `faceit_api_key_protected` (spec: Settings; The FACEIT key: no
    response may ever carry the key or its protected blob)."""
    return {name: _to_json(getattr(cfg, name)) for name in _STORED_NAMES
            if name not in SECRETS.values()}


# --- validate ---------------------------------------------------------------------------------------------


def validate(values: Mapping[str, object], *, check_exists: bool = True) -> dict[str, str]:
    """Validate each given setting on its own. Strict on JSON types (a bool is never a number).
    Messages are short, say what is allowed, and never contain a secret value."""
    errors: dict[str, str] = {}
    for name, value in values.items():
        message = _validate_one(name, value, check_exists=check_exists)
        if message:
            errors[name] = message
    return errors


def _validate_one(name: str, value: object, *, check_exists: bool) -> str | None:
    if name in _FIELD_OF_STORED:
        return _validate_protected(value)
    spec = _BY_NAME.get(name)
    if spec is None:
        return "unknown setting"
    kind = spec.kind
    if kind == "steamid":
        return _validate_steamid(value)
    if kind == "text":
        return _validate_text(value)
    if kind == "url":
        return _validate_url(value)
    if kind == "secret":
        return _validate_secret(value)
    if kind == "folder":
        return _validate_folder(spec, value, check_exists=check_exists)
    if kind == "program":
        return _validate_program(value, check_exists=check_exists)
    if kind == "choice":
        return _validate_choice(spec, value)
    if kind == "int":
        return _validate_int(spec, value)
    if kind == "number":
        return _validate_number(spec, value)
    if kind == "bool":
        return _validate_bool(value)
    raise AssertionError(kind)  # pragma: no cover - every kind above is handled


def _validate_steamid(value: object) -> str | None:
    if not isinstance(value, str):
        return "must be text"
    if value == "":
        return None
    if not _STEAMID_RE.match(value):
        return "must be 17 digits starting with 7656119"
    return None


def _validate_text(value: object) -> str | None:
    if not isinstance(value, str):
        return "must be text"
    if value != value.strip():
        return "must not have leading or trailing spaces"
    if len(value) > 64:
        return "must be at most 64 characters"
    return None


def _validate_url(value: object) -> str | None:
    if not isinstance(value, str):
        return "must be text"
    if value == "":
        return None
    if not value.startswith(("http://", "https://")):
        return "must start with http:// or https://"
    if len(value) > 300:
        return "must be at most 300 characters"
    return None


def _validate_secret(value: object) -> str | None:
    if not isinstance(value, str):
        return "must be text"
    if value == "":
        return "must not be empty"
    if any(ch.isspace() for ch in value):
        return "must not contain spaces"
    if len(value) > 200:
        return "must be at most 200 characters"
    return None


def _validate_protected(value: object) -> str | None:
    if not isinstance(value, str):
        return "must be text"
    if value == "":
        return None
    try:
        base64.b64decode(value, validate=True)
    except ValueError:
        return "must be valid base64"
    return None


def _validate_folder(spec: Field, value: object, *, check_exists: bool) -> str | None:
    if not isinstance(value, str):
        return "must be a folder path"
    if value == "":
        return None if spec.name == "data_root" else "must be set"
    path = Path(value)
    if not path.is_absolute():
        return "must be a full path"
    if check_exists and not path.is_dir():
        return "no such folder"
    return None


def _validate_program(value: object, *, check_exists: bool) -> str | None:
    if not isinstance(value, str) or value == "":
        return "must be set"
    if check_exists and not (shutil.which(value) or Path(value).is_file()):
        return "no such program"
    return None


def _validate_choice(spec: Field, value: object) -> str | None:
    if not isinstance(value, str) or value not in spec.choices:
        return f"must be one of {', '.join(spec.choices)}"
    return None


def _fmt(n: float) -> str:
    return f"{n:g}"


def _validate_int(spec: Field, value: object) -> str | None:
    message = f"must be a whole number from {_fmt(spec.minimum)} to {_fmt(spec.maximum)}"
    if isinstance(value, bool) or not isinstance(value, int):
        return message
    if (spec.minimum is not None and value < spec.minimum) or (spec.maximum is not None and value > spec.maximum):
        return message
    return None


def _validate_number(spec: Field, value: object) -> str | None:
    message = f"must be a number from {_fmt(spec.minimum)} to {_fmt(spec.maximum)}"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return message
    if (spec.minimum is not None and value < spec.minimum) or (spec.maximum is not None and value > spec.maximum):
        return message
    return None


def _validate_bool(value: object) -> str | None:
    return None if isinstance(value, bool) else "must be true or false"


# --- load -------------------------------------------------------------------------------------------------


def load(path: Path) -> Loaded:
    """Defaults, overridden by the known, valid keys of the JSON object at ``path``. A missing file is
    silently all-defaults; an unreadable one warns once; each bad value warns and keeps its default."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return Loaded(Config(), ())
    except OSError as exc:
        return Loaded(Config(), (f"{path.name} could not be read ({exc}); using the defaults",))

    try:
        raw = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError("not a JSON object")
    except ValueError as exc:
        return Loaded(Config(), (f"{path.name} could not be read ({exc}); using the defaults",))

    known = {name: value for name, value in raw.items() if name in _STORED_NAMES}
    errors = validate(known, check_exists=False)

    warnings: list[str] = []
    resolved = defaults()
    for name, value in known.items():
        if name in errors:
            warnings.append(f"{_label_of(name)}: {errors[name]}; using the default")
        else:
            resolved[name] = value

    return Loaded(Config(**_to_config_kwargs(resolved)), tuple(warnings))


def _label_of(name: str) -> str:
    return _BY_NAME[_FIELD_OF_STORED.get(name, name)].label


def _to_config_kwargs(resolved: Mapping[str, object]) -> dict[str, object]:
    kwargs = dict(resolved)
    kwargs["data_root"] = Path(kwargs["data_root"]) if kwargs["data_root"] else None
    for name in _FOLDER_FIELDS - {"data_root"}:
        kwargs[name] = Path(kwargs[name])
    return kwargs


# --- save -------------------------------------------------------------------------------------------------


def save(path: Path, changes: Mapping[str, object]) -> dict[str, str]:
    """Validate every given field (folders and programs checked for real this time); on any problem,
    nothing is written and every problem is returned. Otherwise the current file's values, overlaid with
    `changes`, are written back with only the values that differ from `defaults()` kept."""
    errors: dict[str, str] = {}
    rest: dict[str, object] = {}
    given: dict[str, object] = {}
    for name, value in changes.items():
        if name not in _FIELD_NAMES:
            errors[name] = "unknown setting"
        elif name in SECRETS:
            given[name] = value
        else:
            rest[name] = value

    errors.update(validate(rest, check_exists=True))
    errors.update(validate({name: value for name, value in given.items() if value not in ("", None)},
                           check_exists=True))
    if errors:
        return errors

    merged = {**_read_raw(path), **rest}
    defaults_ = defaults()
    kept = {
        name: value for name, value in merged.items()
        if name not in _STORED_NAMES or differs_from_default(name, value, defaults_[name])
    }
    for name, stored in SECRETS.items():
        if name not in given:
            continue
        if given[name] is None:
            kept.pop(stored, None)
        elif given[name] != "":
            kept[stored] = protect.protect(str(given[name]))

    paths.atomic_write_text(path, json.dumps(kept, indent=2, sort_keys=True))
    return {}


def _read_raw(path: Path) -> dict[str, object]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def differs_from_default(name: str, value: object, default_value: object) -> bool:
    if name in _FOLDER_FIELDS:
        return Path(str(value)) != Path(str(default_value))
    return value != default_value


# --- SettingsStore ------------------------------------------------------------------------------------------


class SettingsStore:
    """One `settings.json`, shared by the web server's threads and the worker thread."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._loaded: Loaded | None = None
        self._stamp: tuple[int, int] | None = None

    @property
    def path(self) -> Path:
        return self._path

    def current(self) -> Loaded:
        """Loads once; reloads when the file's (mtime_ns, size) or existence has changed since."""
        with self._lock:
            stamp = self._stamp_of(self._path)
            if self._loaded is None or stamp != self._stamp:
                self._loaded = load(self._path)
                self._stamp = stamp
            return self._loaded

    def save(self, changes: Mapping[str, object]) -> dict[str, str]:
        with self._lock:
            errors = save(self._path, changes)
            if not errors:
                self._loaded = None    # the next current() reloads
            return errors

    @staticmethod
    def _stamp_of(path: Path) -> tuple[int, int] | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)
