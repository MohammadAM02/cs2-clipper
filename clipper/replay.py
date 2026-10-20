"""The replay's positions: each player's place once a second, from csda's per-frame positions (spec: Replay).

csda writes a position for every player on every frame when it runs with `-positions`, which is far too much to keep:
64 frames a second, ten players, most of a match. This reads that array a piece at a time, keeps the frames that fall
on a whole second, and gives them as compact rows. The file is never read whole.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

VERSION = 1
FIELDS = ("tick", "round", "steam_id", "x", "y", "yaw", "alive", "side", "health")
ARRAY = "playerPositions"
PIECE = 1 << 20                                  # characters read at a time


def sample_rows(records: Iterator[dict[str, Any]], tickrate: float) -> list[list]:
    """The rows (in FIELDS order) of the records that fall on a whole second of the match."""
    step = round(tickrate)
    rows = []
    for record in records:
        tick = record["tick"]
        if tick % step == 0:
            rows.append([tick, record["roundNumber"], str(record["steamId"]), record["x"], record["y"], record["yaw"],
                         record["isAlive"], record["side"], record["health"]])
    return rows


def iter_array(path: Path, key: str, piece: int = PIECE) -> Iterator[dict[str, Any]]:
    """Each object of the JSON array under `key` in `path`, read a piece at a time: only a piece of the file, and the
    object being decoded, is in memory at any moment. Nothing is yielded when the array is absent or empty."""
    start = re.compile(rf'"{re.escape(key)}"\s*:\s*\[')
    decoder = json.JSONDecoder()
    with open(path, encoding="utf-8") as handle:
        buf = ""
        while True:                                       # find where the array starts
            found = start.search(buf)
            if found:
                buf = buf[found.end():]
                break
            chunk = handle.read(piece)
            if not chunk:
                return
            buf = buf[-256:] + chunk                      # the key may be split across two pieces
        pos = 0
        while True:
            while pos < len(buf) and buf[pos] in ", \r\n\t":
                pos += 1
            if pos == len(buf):                           # the piece is used up: read the next
                chunk = handle.read(piece)
                if not chunk:
                    return
                buf, pos = chunk, 0
                continue
            if buf[pos] == "]":
                return
            try:
                record, end = decoder.raw_decode(buf, pos)
            except json.JSONDecodeError:                  # the object runs past the piece: read on and try again
                chunk = handle.read(piece)
                if not chunk:
                    raise
                buf, pos = buf[pos:] + chunk, 0
                continue
            yield record
            pos = end
            if pos > piece:                               # drop what is decoded, so the piece stays short
                buf, pos = buf[pos:], 0


def sample_file(path: Path, tickrate: float, piece: int = PIECE) -> list[list]:
    """The sampled rows of csda's output at `path`; an empty list when it holds no positions."""
    return sample_rows(iter_array(path, ARRAY, piece), tickrate)


def document(checksum: str, tickrate: float, rows: list[list]) -> dict[str, Any]:
    """What the app keeps for a match's positions."""
    return {"version": VERSION, "checksum": checksum, "tickrate": tickrate, "fields": list(FIELDS), "samples": rows}
