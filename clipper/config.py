"""Settings: the one frozen object every module reads (spec: Settings and data).

Defaults fit a fresh install: no SteamID, no clips folder, no FACEIT nickname or key. ``settings.py``
reads ``settings.json``, validates it against these defaults, and builds this ``Config``; nothing here
reads a file."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from clipper import paths
from clipper.windows import downloads_dir

REPO_ROOT = Path(__file__).resolve().parents[1]

# aspect_ratio -> (width, height, stretched to 1920x1080 when joining).
RATIOS: dict[str, tuple[int, int, bool]] = {
    "16:9": (1920, 1080, False),
    "4:3": (1280, 960, False),
    "4:3-hd": (1440, 1080, False),
    "4:3-stretched": (1280, 960, True),
}
SEQUENCE_EVENTS = ("kills", "rounds")
# perspectives -> the Perspectives each Demo is rendered from, in render order.
PERSPECTIVE_CHOICES: dict[str, tuple[str, ...]] = {
    "both": ("player", "enemy"),
    "player": ("player",),
    "enemy": ("enemy",),
}


@dataclass(frozen=True)
class Config:
    subject_steamid: str = ""
    faceit_nickname: str = ""
    faceit_oauth_client_id: str = ""
    faceit_redirect_uri: str = ""
    faceit_client_secret_protected: str = field(default="", repr=False)
    faceit_api_key_protected: str = field(default="", repr=False)
    downloads_dir: Path = field(default_factory=downloads_dir)
    data_root: Path | None = None
    index_path: Path = field(default_factory=paths.index_file)
    logs_dir: Path = field(default_factory=paths.logs_dir)
    tools_dir: Path = field(default_factory=paths.tools_dir)
    analyses_dir: Path = field(default_factory=paths.analyses_dir)
    cs2_settings_dir: Path = field(default_factory=paths.cs2_settings_dir)
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    hlae_exe: str = ""              # blank: the HLAE Setup installs
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
    perspectives: str = "both"
    match_alerts: bool = True
    stopped_playing_minutes: float = 5.0
    page_port: int = 8765

    def __post_init__(self) -> None:
        if self.aspect_ratio not in RATIOS:
            raise ValueError(f"aspect_ratio must be one of {', '.join(RATIOS)} (got {self.aspect_ratio!r})")
        if self.sequence_event not in SEQUENCE_EVENTS:
            raise ValueError(
                f"sequence_event must be one of {', '.join(SEQUENCE_EVENTS)} (got {self.sequence_event!r})"
            )
        if self.perspectives not in PERSPECTIVE_CHOICES:
            raise ValueError(
                f"perspectives must be one of {', '.join(PERSPECTIVE_CHOICES)} (got {self.perspectives!r})"
            )

    @property
    def perspectives_to_render(self) -> tuple[str, ...]:
        """The Perspectives each Demo is rendered from. Round Clips only ever come from the player's view:
        a round has no one enemy to follow, so an Enemy render would record the player's camera again."""
        if self.sequence_event == "rounds":
            return ("player",)
        return PERSPECTIVE_CHOICES[self.perspectives]

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

    def store_path(self, path: Path) -> str:
        """A Clip's or a Reel's path as the index keeps it: relative to the clips folder, so moving that
        folder (the `data_root` setting) cannot strand every file the way absolute paths did. A path
        outside the clips folder is kept whole -- there is nothing for it to be relative to."""
        try:
            return str(Path(path).relative_to(self.data_root))
        except (TypeError, ValueError):      # no clips folder set, or a path outside it
            return str(path)

    def load_path(self, stored: str) -> Path:
        """A stored path back as a real one. Rows written before paths became relative still hold an
        absolute path, and joining an absolute path discards the clips folder, so both read correctly."""
        if self.data_root is None:
            return Path(stored)
        return self.data_root / stored

    @property
    def csda_exe(self) -> Path:
        """cs-demo-analyzer, which reads each Demo: always the one Setup installs."""
        return self.tools_dir / "csda" / "csda.exe"

    @property
    def hlae_path(self) -> Path:
        """HLAE.exe, which starts CS2 for each render: the `hlae_exe` setting, else the one Setup installs."""
        return Path(self.hlae_exe) if self.hlae_exe else self.tools_dir / "hlae" / "HLAE.exe"
