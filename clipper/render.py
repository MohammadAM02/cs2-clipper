"""What a Render Job is asked for and what it gives back (spec: Rendering). `hlae_render` records them."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from clipper.model import ClipFile

CLIP_NAME = re.compile(r"^sequence-(\d+)-tick-(\d+)-to-(\d+)\.mp4$", re.IGNORECASE)
ABORTED = "aborted: FACEIT AC started during the render"


@dataclass(frozen=True)
class RenderRequest:
    demo_path: Path
    perspective: str            # "player" or "enemy"
    rounds: tuple[int, ...]
    output_dir: Path
    log_path: Path
    steamid: str
    padding_before_s: float
    padding_after_s: float
    event: str = "kills"        # "kills" (a Sequence per Frag) or "rounds" (whole rounds)
    width: int = 1920
    height: int = 1080
    checksum: str = ""          # the Demo's match, whose analysis the plan is made from


@dataclass(frozen=True)
class RenderResult:
    ok: bool
    aborted: bool = False
    failure: str | None = None
    clips: tuple[ClipFile, ...] = ()


def find_clips(output_dir: Path, duration_of: Callable[[Path], float]) -> list[ClipFile]:
    """The Clips in `output_dir`, in Tick order."""
    clips = []
    for path in output_dir.iterdir():
        match = CLIP_NAME.match(path.name)
        if match:
            clips.append(ClipFile(sequence=int(match[1]), start_tick=int(match[2]), end_tick=int(match[3]),
                                  path=path, duration_s=duration_of(path)))
    return sorted(clips, key=lambda clip: clip.start_tick)
