"""Joining: a Highlight's Clips become one Reel per Perspective (spec: Joining)."""

from __future__ import annotations

import bisect
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from clipper.model import ClipFile

BASE_TOLERANCE_S = 0.25
PER_JOIN_TOLERANCE_S = 0.05   # joining two MP3-audio Clips measured +0.045 s on this machine
# 4:3-stretched: bake the horizontal stretch into a 16:9 Reel. The only join that re-encodes.
STRETCH_ARGS = ("-vf", "scale=1920:1080,setsar=1", "-c:v", "libx264", "-crf", "23", "-preset", "medium",
                "-c:a", "copy")


class JoinError(Exception):
    """Clips could not be matched to Highlights, or a Reel could not be made."""


def assign_clips(clips: Sequence[ClipFile], rounds: Sequence[tuple[int, int]]) -> dict[int, list[ClipFile]]:
    """Group Clips by selected round. `rounds` holds (round, round_start_tick) per selected
    Highlight. A Clip belongs to the latest selected round that starts at or before its middle Tick:
    CS:DM only renders the rounds it was given, so that is always the Clip's own round — even for a
    whole-round Sequence that begins a little before the round's start Tick."""
    ordered = sorted(rounds, key=lambda pair: pair[1])
    starts = [start for _, start in ordered]
    groups: dict[int, list[ClipFile]] = {number: [] for number, _ in ordered}
    for clip in sorted(clips, key=lambda c: c.start_tick):
        middle = (clip.start_tick + clip.end_tick) // 2
        position = bisect.bisect_right(starts, middle) - 1
        if position < 0:
            raise JoinError(f"{clip.path.name} lies before every selected round")
        groups[ordered[position][0]].append(clip)
    empty = [str(number) for number, got in groups.items() if not got]
    if empty:
        raise JoinError(f"no Clip for selected round(s) {', '.join(empty)}")
    return groups


def _concat_line(path: Path) -> str:
    return "file '" + path.as_posix().replace("'", "'\\''") + "'\n"


def join_reel(clips: Sequence[Path], out_path: Path, *, ffmpeg: str,
              duration_of: Callable[[Path], float], stretch: bool = False) -> float:
    """Concatenate `clips` (already in Sequence order) into out_path — a stream copy, or a re-encode
    to 1920x1080 when `stretch` — check the result's length, and return it in seconds."""
    if not clips:
        raise JoinError(f"no Clips for {out_path.name}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    expected = sum(duration_of(clip) for clip in clips)
    partial = out_path.with_name(out_path.stem + ".partial.mp4")
    if len(clips) == 1 and not stretch:
        shutil.copyfile(clips[0], partial)
    else:
        listing = out_path.with_name(out_path.stem + ".concat.txt")
        listing.write_text("".join(_concat_line(clip) for clip in clips), encoding="utf-8")
        codec = STRETCH_ARGS if stretch else ("-c", "copy")
        try:
            result = subprocess.run(
                [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                 "-i", str(listing), *codec, str(partial)],
                capture_output=True, text=True, timeout=600, creationflags=subprocess.CREATE_NO_WINDOW,
            )
        finally:
            listing.unlink(missing_ok=True)
        if result.returncode != 0:
            partial.unlink(missing_ok=True)
            raise JoinError(f"ffmpeg could not join {out_path.name}: {result.stderr.strip()[:300]}")
    actual = duration_of(partial)
    tolerance = BASE_TOLERANCE_S + PER_JOIN_TOLERANCE_S * (len(clips) - 1)
    if abs(actual - expected) > tolerance:
        partial.unlink(missing_ok=True)
        raise JoinError(f"{out_path.name} is {actual:.2f}s but its Clips sum to {expected:.2f}s")
    os.replace(partial, out_path)
    return actual
