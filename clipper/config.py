"""Settings. Defaults fit this machine; any of them can be overridden in clipper.toml at the repo root."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from clipper.windows import downloads_dir

REPO_ROOT = Path(__file__).resolve().parents[1]
_LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
_PATH_SETTINGS = frozenset(
    {"downloads_dir", "data_root", "index_path", "csdm_home", "csdm_app_dir", "pg_bin", "pg_data"}
)

# aspect_ratio -> (width, height, stretched to 1920x1080 when joining). Mirrors render_reel.sh's REEL_RATIO.
RATIOS: dict[str, tuple[int, int, bool]] = {
    "16:9": (1920, 1080, False),
    "4:3": (1280, 960, False),
    "4:3-hd": (1440, 1080, False),
    "4:3-stretched": (1280, 960, True),
}
SEQUENCE_EVENTS = ("kills", "rounds")


@dataclass(frozen=True)
class Config:
    subject_steamid: str = "76561198192858303"
    downloads_dir: Path = field(default_factory=downloads_dir)
    data_root: Path = Path("E:/cs2clips")
    index_path: Path = REPO_ROOT / "data" / "clipper.sqlite"
    csdm_home: Path = REPO_ROOT / "home"
    csdm_app_dir: Path = _LOCALAPPDATA / "Programs" / "cs-demo-manager"
    pg_bin: Path = _LOCALAPPDATA / "pg17" / "pgsql" / "bin"
    pg_data: Path = _LOCALAPPDATA / "pg17" / "data"
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    top_n: int = 5
    padding_before_s: float = 4.0
    padding_after_s: float = 2.0
    stall_seconds: float = 180.0
    launch_timeout_seconds: float = 300.0
    min_free_gb: float = 5.0
    heads_up_seconds: float = 30.0
    poll_seconds: float = 5.0
    aspect_ratio: str = "16:9"
    sequence_event: str = "kills"

    def __post_init__(self) -> None:
        if self.aspect_ratio not in RATIOS:
            raise ValueError(f"aspect_ratio must be one of {', '.join(RATIOS)} (got {self.aspect_ratio!r})")
        if self.sequence_event not in SEQUENCE_EVENTS:
            raise ValueError(
                f"sequence_event must be one of {', '.join(SEQUENCE_EVENTS)} (got {self.sequence_event!r})"
            )

    @property
    def video_size(self) -> tuple[int, int]:
        width, height, _ = RATIOS[self.aspect_ratio]
        return width, height

    @property
    def stretch(self) -> bool:
        """Whether Reels are stretched to 1920x1080 when joining (4:3-stretched)."""
        return RATIOS[self.aspect_ratio][2]

    @property
    def demos_dir(self) -> Path:
        return self.data_root / "demos"

    @property
    def renders_dir(self) -> Path:
        return self.data_root / "renders"

    @property
    def library_dir(self) -> Path:
        return self.data_root / "library"

    @property
    def logs_dir(self) -> Path:
        return self.data_root / "logs"

    @property
    def csdm_exe(self) -> Path:
        return self.csdm_app_dir / "cs-demo-manager.exe"

    @property
    def csdm_cli_js(self) -> Path:
        return self.csdm_app_dir / "resources" / "app.asar" / "cli.js"

    def database_conninfo(self) -> dict[str, object]:
        """CS:DM's own connection settings. The password lives only in CS:DM's settings file."""
        settings = json.loads((self.csdm_home / ".csdm" / "settings.json").read_text(encoding="utf-8"))
        db = settings["database"]
        return {
            "host": db["hostname"],
            "port": db["port"],
            "user": db["username"],
            "password": db["password"],
            "dbname": db["database"],
        }


def load_config(path: Path | None = None) -> Config:
    """Defaults, overridden by the TOML file at `path` (default: <repo>/clipper.toml) if it exists."""
    path = path or REPO_ROOT / "clipper.toml"
    if not path.exists():
        return Config()
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown = sorted(set(raw) - {f.name for f in fields(Config)})
    if unknown:
        raise ValueError(f"unknown setting(s) in {path}: {', '.join(unknown)}")
    return Config(**{key: Path(value) if key in _PATH_SETTINGS else value for key, value in raw.items()})
