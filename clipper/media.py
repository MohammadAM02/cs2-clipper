"""Reading video files with ffprobe."""

from __future__ import annotations

import subprocess
from pathlib import Path


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
