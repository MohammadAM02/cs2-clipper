"""Intake: finished FACEIT Demos in Downloads move into the pipeline's own folder (spec: Flow, step 1)."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from clipper.index import Index

log = logging.getLogger(__name__)

FACEIT_DEMO = re.compile(
    r"^1-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}-\d+-\d+\.dem(\.zst|\.gz)?$",
    re.IGNORECASE,
)
PARTIAL_SUFFIXES = (".crdownload", ".part", ".tmp")


@dataclass
class _Sighting:
    size: int
    since: float


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _free_name(path: Path) -> Path:
    """`path`, or the same name with ' (2)', ' (3)', … before its extensions if it is taken."""
    if not path.exists():
        return path
    base, extensions = path.name.split(".", 1)
    n = 2
    while (candidate := path.with_name(f"{base} ({n}).{extensions}")).exists():
        n += 1
    return candidate


def _copy_then_rename(src: Path, dest: Path) -> None:
    """Copy across drives without ever leaving a half-written file under the final name."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    shutil.copyfile(src, partial)
    os.replace(partial, dest)


class Intake:
    def __init__(self, downloads: Path, demos_dir: Path, stable_seconds: float = 10.0,
                 clock: Callable[[], float] = time.monotonic):
        self._downloads = downloads
        self._demos_dir = demos_dir
        self._stable_seconds = stable_seconds
        self._clock = clock
        self._sightings: dict[str, _Sighting] = {}

    def ready(self) -> list[Path]:
        """FACEIT Demos whose size has held for stable_seconds, with no partial-download sibling."""
        if not self._downloads.is_dir():
            return []
        now = self._clock()
        entries = {entry.name.lower(): entry for entry in self._downloads.iterdir()}
        ready = []
        for name, path in entries.items():
            if not FACEIT_DEMO.match(path.name) or not path.is_file():
                continue
            if any(name + suffix in entries for suffix in PARTIAL_SUFFIXES):
                continue
            size = path.stat().st_size
            seen = self._sightings.get(name)
            if seen is None or seen.size != size:
                self._sightings[name] = _Sighting(size, now)
            elif now - seen.since >= self._stable_seconds:
                ready.append(path)
        return sorted(ready)

    def take(self, path: Path, index: Index) -> int | None:
        """Move a ready Demo into demos_dir and record it. A Demo already in the index (same file
        name or SHA-256) goes to demos_dir/duplicates instead. Returns the new Demo's id, or None."""
        self._sightings.pop(path.name.lower(), None)
        digest = sha256_of(path)
        if index.has_demo(path.name, digest):
            dest = _free_name(self._demos_dir / "duplicates" / path.name)
            _copy_then_rename(path, dest)
            path.unlink()
            log.info("%s is already in the index; moved it to %s", path.name, dest)
            return None
        dest = self._demos_dir / path.name
        _copy_then_rename(path, dest)
        demo_id = index.add_demo(path.name, digest, dest)
        path.unlink()
        log.info("took %s as demo #%s", path.name, demo_id)
        return demo_id
