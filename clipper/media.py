"""Reading video files with ffprobe, and finding the graphics card's encoder."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

# The graphics cards' H.264 encoders, in the order they are tried, each with the options that make a Clip look like
# x264's at CRF 23: constant quality, no bitrate cap.
GPU_ENCODERS = (
    ("h264_nvenc", "-preset p5 -rc vbr -cq 23 -b:v 0"),
    ("h264_amf", "-quality quality -rc cqp -qp_i 22 -qp_p 24"),
    ("h264_qsv", "-global_quality 23"),
)


class MediaError(Exception):
    """ffprobe could not read a file."""


def probe_duration(path: Path, ffprobe: str = "ffprobe") -> float:
    """The file's duration in seconds, or MediaError if ffprobe cannot read it."""
    proc = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    text = proc.stdout.strip()
    if proc.returncode != 0 or not text:
        raise MediaError(f"ffprobe cannot read {path.name}: {proc.stderr.strip()[:200]}")
    try:
        return float(text)
    except ValueError as exc:
        raise MediaError(f"ffprobe gave no duration for {path.name}: {text[:50]}") from exc


def gpu_encoder(ffmpeg: str, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> tuple[str, str] | None:
    """The first of GPU_ENCODERS this PC's graphics card can use, as (codec, options): each is tried on a tenth of a
    second of black. None when none can, and x264 records instead."""
    for codec, options in GPU_ENCODERS:
        try:
            done = run([ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=640x360:d=0.1",
                        "-c:v", codec, *options.split(), "-f", "null", "-"],
                       capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        except (OSError, subprocess.SubprocessError):
            continue
        if done.returncode == 0:
            return codec, options
    return None
