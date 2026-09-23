"""Unpacking: a downloaded Demo (.dem.zst, .dem.gz or .dem) becomes a .dem (spec: Flow, step 2)."""

from __future__ import annotations

import gzip
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

import zstandard

DEMO_MAGIC = b"PBDEMS2\x00"


class UnpackError(Exception):
    """The file is not a Demo, or it did not decompress into one."""


@contextmanager
def _zstd_reader(path: Path) -> Iterator[BinaryIO]:
    with open(path, "rb") as raw, zstandard.ZstdDecompressor().stream_reader(raw) as reader:
        yield reader


@contextmanager
def _plain_reader(path: Path) -> Iterator[BinaryIO]:
    with open(path, "rb") as raw:
        yield raw


def _has_demo_header(path: Path) -> bool:
    with open(path, "rb") as handle:
        return handle.read(len(DEMO_MAGIC)) == DEMO_MAGIC


def unpack(src: Path, out_dir: Path) -> Path:
    """Write the .dem for `src` into out_dir and return its path. A plain .dem that is already in
    out_dir is returned as it is."""
    name = src.name
    lowered = name.lower()
    if lowered.endswith(".dem.zst"):
        dem_name, opener = name[: -len(".zst")], _zstd_reader
    elif lowered.endswith(".dem.gz"):
        dem_name, opener = name[: -len(".gz")], gzip.open
    elif lowered.endswith(".dem"):
        dem_name, opener = name, _plain_reader
    else:
        raise UnpackError(f"not a Demo: {name}")
    out = out_dir / dem_name
    if out.exists() and out.resolve() == src.resolve():
        if not _has_demo_header(out):
            raise UnpackError(f"{name} does not start with the PBDEMS2 header")
        return out
    out_dir.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.name + ".partial")
    try:
        with opener(src) as reader, open(partial, "wb") as writer:
            shutil.copyfileobj(reader, writer, 1 << 20)
    except (OSError, EOFError, zstandard.ZstdError) as exc:
        partial.unlink(missing_ok=True)
        raise UnpackError(f"could not unpack {name}: {exc}") from exc
    if not _has_demo_header(partial):
        partial.unlink()
        raise UnpackError(f"{name} did not unpack into a Demo (no PBDEMS2 header)")
    os.replace(partial, out)
    return out
