"""A lock file that one holder at a time can have: the running app (``clipper.lock``) and a setup run
(``setup.lock``)."""
from __future__ import annotations

import msvcrt
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


class AlreadyRunning(Exception):
    """Another clipper holds the lock."""


@contextmanager
def single_instance(lock_path: Path) -> Iterator[None]:
    """Hold an exclusive lock on lock_path for the life of the block."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        handle.close()
        raise AlreadyRunning(f"another clipper is already running (lock: {lock_path})") from exc
    try:
        yield
    finally:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()
