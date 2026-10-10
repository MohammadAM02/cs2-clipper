"""What a Render Job is asked for and what it gives back (spec: Rendering). `hlae_render` records them."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from clipper.model import ClipFile

CLIP_NAME = re.compile(r"^sequence-(\d+)-tick-(\d+)-to-(\d+)\.mp4$", re.IGNORECASE)
ABORTED = "aborted: FACEIT AC started during the render"
PERSPECTIVES = ("player", "enemy")


@dataclass(frozen=True)
class RenderRequest:
    demo_path: Path
    outputs: dict[str, Path]    # each Perspective to record, with the folder its Clips go to, in the order recorded
    rounds: tuple[int, ...]
    log_path: Path
    steamid: str
    padding_before_s: float
    padding_after_s: float
    event: str = "kills"        # "kills" (a Sequence per Frag) or "rounds" (whole rounds)
    width: int = 1920
    height: int = 1080
    checksum: str = ""          # the Demo's match, whose analysis the plan is made from

    def __post_init__(self) -> None:
        if not 1 <= len(self.outputs) <= 2:
            raise ValueError(f"a Render Job records one or two views, not {len(self.outputs)}")
        for perspective in self.outputs:
            if perspective not in PERSPECTIVES:
                raise ValueError(f"unknown perspective: {perspective!r}")

    @property
    def perspectives(self) -> tuple[str, ...]:
        """The views to record, in the order they are recorded."""
        return tuple(self.outputs)


@dataclass(frozen=True)
class RenderResult:
    ok: bool                    # every view recorded
    aborted: bool = False
    failure: str | None = None
    clips: dict[str, tuple[ClipFile, ...]] = field(default_factory=dict)     # by Perspective: the views that recorded


def find_clips(output_dir: Path, duration_of: Callable[[Path], float]) -> list[ClipFile]:
    """The Clips in `output_dir`, in Tick order."""
    clips = []
    for path in output_dir.iterdir():
        match = CLIP_NAME.match(path.name)
        if match:
            clips.append(ClipFile(sequence=int(match[1]), start_tick=int(match[2]), end_tick=int(match[3]),
                                  path=path, duration_s=duration_of(path)))
    return sorted(clips, key=lambda clip: clip.start_tick)
