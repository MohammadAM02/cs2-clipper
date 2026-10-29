"""The map radars, read from the installed CS2 (spec: Replay).

CS2 keeps each map's radar as a compiled texture (`panorama/images/overheadmaps/<map>_radar_psd.vtex_c`) inside its
`pak01_*.vpk` archives, with the map's overview metadata (`resource/overviews/<map>.txt`: `pos_x`, `pos_y` and
`scale`) in the index `pak01_dir.vpk`. This reads those two files, decodes the texture to a PNG and keeps both in
the app's data folder. Nothing is shipped: the images come from the copy of the game on this PC.

The texture layout follows ValveResourceFormat (MIT): a resource's DATA block holds the texture header, then the
mip data follows the block, smallest mip first; mip 0 is one LZ4 block when its compressed size is smaller than
its raw size. Radars are 1024x1024 BGRA8888 in this build of CS2.
"""

from __future__ import annotations

import json
import re
import struct
import zlib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from clipper import paths

DIR_INDEX = "pak01_dir.vpk"
IN_DIR_ARCHIVE = 0x7FFF           # a VPK entry whose data sits in the index file itself
RADAR_PATH = "panorama/images/overheadmaps/{map}_radar_psd.vtex_c"
OVERVIEW_PATH = "resource/overviews/{map}.txt"
MAP_NAME = re.compile(r"[a-z0-9_]{2,40}")
KEYVALUE = re.compile(r'"([A-Za-z0-9_]+)"\s+"([^"]*)"')
FORMATS = {4: "RGBA8888", 28: "BGRA8888"}      # VTexFormat values this decoder reads
EXTRA_COMPRESSED_MIP_SIZE = 4
LZ4_METHOD = 1


class RadarError(Exception):
    """A radar could not be read or decoded: CS2 not installed, a file missing, or a format not handled."""


@dataclass(frozen=True)
class VpkEntry:
    archive: int
    offset: int
    length: int
    preload: bytes


def checked_map(name: str) -> str:
    if not MAP_NAME.fullmatch(name or ""):
        raise RadarError(f"not a map name: {name!r}")
    return name


def vpk_index(dir_bytes: bytes, wanted: Iterable[str]) -> dict[str, VpkEntry]:
    """The entries of a VPK directory index whose full paths are `wanted`, by path."""
    wanted = set(wanted)
    _sig, version, tree_size = struct.unpack_from("<III", dir_bytes, 0)
    pos = 12 + (16 if version >= 2 else 0)
    found: dict[str, VpkEntry] = {}
    while True:
        ext, pos = _cstring(dir_bytes, pos)
        if not ext:
            return found
        while True:
            folder, pos = _cstring(dir_bytes, pos)
            if not folder:
                break
            while True:
                name, pos = _cstring(dir_bytes, pos)
                if not name:
                    break
                _crc, preload_len, archive, offset, length, _term = struct.unpack_from("<IHHIIH", dir_bytes, pos)
                pos += 18
                preload = dir_bytes[pos:pos + preload_len]
                pos += preload_len
                full = f"{name}.{ext}" if folder == " " else f"{folder}/{name}.{ext}"
                if full in wanted:
                    found[full] = VpkEntry(archive, offset, length, bytes(preload))


def _cstring(buf: bytes, pos: int) -> tuple[str, int]:
    end = buf.index(b"\x00", pos)
    return buf[pos:end].decode("utf-8", "replace"), end + 1


def read_vpk_entry(csgo: Path, dir_bytes: bytes, entry: VpkEntry) -> bytes:
    """The bytes of an entry: its preload part, then the rest from the archive it names (or from the index)."""
    if entry.archive == IN_DIR_ARCHIVE:
        _sig, version, tree_size = struct.unpack_from("<III", dir_bytes, 0)
        start = (28 if version >= 2 else 12) + tree_size + entry.offset
        body = dir_bytes[start:start + entry.length]
    else:
        with open(csgo / f"pak01_{entry.archive:03d}.vpk", "rb") as handle:
            handle.seek(entry.offset)
            body = handle.read(entry.length)
    return entry.preload + body


@dataclass(frozen=True)
class Radar:
    width: int
    height: int
    rgba: bytes


def decode_vtex(data: bytes) -> Radar:
    """Mip 0 of a Source 2 texture, as RGBA8888."""
    start, size = _data_block(data)
    if u16(data, start) != 1:
        raise RadarError(f"texture header version {u16(data, start)} is not handled")
    info = _texture_header(data, start)
    if info["format"] not in FORMATS:
        raise RadarError(f"texture format {info['format']} is not handled")
    if info["depth"] != 1:
        raise RadarError("volume textures are not handled")
    width, height = info["width"], info["height"]
    raw = width * height * 4
    pos = start + size                               # the mip data follows the DATA block, smallest mip first
    sizes = info["compressed"]
    for level in range(info["mips"] - 1, 0, -1):
        mip_raw = max(1, width >> level) * max(1, height >> level) * 4
        pos += sizes[level] if sizes is not None and sizes[level] < mip_raw else mip_raw
    if sizes is not None and sizes[0] < raw:
        pixels = lz4_block_decode(data[pos:pos + sizes[0]], raw)
    else:
        pixels = data[pos:pos + raw]
    if len(pixels) != raw:
        raise RadarError(f"short texture data: {len(pixels)} of {raw} bytes")
    if FORMATS[info["format"]] == "BGRA8888":
        rgba = bytearray(raw)
        rgba[0::4], rgba[1::4], rgba[2::4], rgba[3::4] = pixels[2::4], pixels[1::4], pixels[0::4], pixels[3::4]
        pixels = bytes(rgba)
    return Radar(width, height, bytes(pixels))


def u16(data: bytes, pos: int) -> int:
    return struct.unpack_from("<H", data, pos)[0]


def u32(data: bytes, pos: int) -> int:
    return struct.unpack_from("<I", data, pos)[0]


def _data_block(data: bytes) -> tuple[int, int]:
    """(start, size) of the resource's DATA block. The block table sits at 8 + the u32 at 8."""
    table = 8 + u32(data, 8)
    for i in range(u32(data, 12)):
        at = table + 12 * i
        if data[at:at + 4] == b"DATA":
            return at + 4 + u32(data, at + 4), u32(data, at + 8)
    raise RadarError("no DATA block in this texture")


def _texture_header(data: bytes, start: int) -> dict:
    """The header after the version and flags: 16 bytes of reflectivity, the size (u16 x3), the format and mip
    bytes, the picmip field, then the extra-data table's offset and count."""
    pos = start + 20
    info = {"width": u16(data, pos), "height": u16(data, pos + 2), "depth": u16(data, pos + 4),
            "format": data[pos + 6], "mips": data[pos + 7]}
    extra_offset, extra_count = u32(data, start + 32), u32(data, start + 36)
    pos = start + 40
    if extra_count:
        pos += extra_offset - 8
    info["compressed"] = None
    for _ in range(extra_count):
        kind, offset = u32(data, pos), u32(data, pos + 4) - 8          # the entry's size is not needed here
        pos += 12
        at = pos + offset
        if kind == EXTRA_COMPRESSED_MIP_SIZE:
            method, mips_offset, count = u32(data, at), u32(data, at + 4), u32(data, at + 8)
            if method != LZ4_METHOD:
                raise RadarError(f"mip compression {method} is not handled")
            sizes_at = at + 12 + mips_offset - 8
            info["compressed"] = [struct.unpack_from("<i", data, sizes_at + 4 * k)[0] for k in range(count)]
    return info


def lz4_block_decode(src: bytes, out_size: int) -> bytes:
    """One LZ4 block, with no frame header, as the format writes it."""
    dst = bytearray()
    i, n = 0, len(src)
    while i < n:
        token = src[i]
        i += 1
        literals = token >> 4
        if literals == 15:
            while True:
                extra = src[i]
                i += 1
                literals += extra
                if extra != 255:
                    break
        dst += src[i:i + literals]
        i += literals
        if i >= n:
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        match = (token & 15) + 4
        if (token & 15) == 15:
            while True:
                extra = src[i]
                i += 1
                match += extra
                if extra != 255:
                    break
        start = len(dst) - offset
        if offset >= match:
            dst += dst[start:start + match]
        else:
            for k in range(match):
                dst.append(dst[start + k])
    if len(dst) != out_size:
        raise RadarError(f"LZ4 gave {len(dst)} bytes, expected {out_size}")
    return bytes(dst)


def parse_overview(text: str) -> dict[str, str]:
    """The key/value pairs of a map's overview file (KeyValues, with // comments)."""
    return dict(KEYVALUE.findall(text))


def write_png(path: Path, width: int, height: int, rgba: bytes) -> None:
    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)                                # no filter on any row
        raw += rgba[y * stride:(y + 1) * stride]

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(png)
    paths.move_into_place(tmp, path)


def build(map_name: str, csgo: Path, out_dir: Path) -> dict:
    """Reads `map_name`'s radar and overview from the game in `csgo` (its `game\\csgo` folder), writes the PNG and a
    JSON file of the overview's numbers into `out_dir`, and gives those numbers."""
    name = checked_map(map_name)
    index_path = csgo / DIR_INDEX
    if not index_path.is_file():
        raise RadarError(f"no CS2 index at {index_path}")
    dir_bytes = index_path.read_bytes()
    entries = vpk_index(dir_bytes, [RADAR_PATH.format(map=name), OVERVIEW_PATH.format(map=name)])
    radar_key, overview_key = RADAR_PATH.format(map=name), OVERVIEW_PATH.format(map=name)
    if radar_key not in entries or overview_key not in entries:
        raise RadarError(f"CS2 has no radar or overview for {name}")
    radar = decode_vtex(read_vpk_entry(csgo, dir_bytes, entries[radar_key]))
    overview = parse_overview(read_vpk_entry(csgo, dir_bytes, entries[overview_key]).decode("utf-8", "replace"))
    meta = {"map": name, "width": radar.width, "height": radar.height,
            "pos_x": float(overview["pos_x"]), "pos_y": float(overview["pos_y"]),
            "scale": float(overview["scale"]), "rotate": int(float(overview.get("rotate", "0")))}
    out_dir.mkdir(parents=True, exist_ok=True)
    write_png(out_dir / f"{name}.png", radar.width, radar.height, radar.rgba)
    paths.atomic_write_text(out_dir / f"{name}.json", json.dumps(meta))
    return meta


def world_to_pixel(x: float, y: float, meta: dict) -> tuple[float, float]:
    """Where world position (x, y) is on the stored radar image, in its pixels. The overview's `pos_x`/`pos_y` is the
    top-left corner and `scale` the world units a pixel covers. A radar with `rotate` 1 is stored turned a quarter
    turn; this turns the point the same way (clockwise). That turn is NOT yet checked against real positions: it is
    the first thing to confirm on a Dust II or Italy demo. The page does the same arithmetic."""
    u = (x - meta["pos_x"]) / meta["scale"]
    v = (meta["pos_y"] - y) / meta["scale"]
    if meta.get("rotate", 0) == 1:
        return meta["height"] - v, u
    return u, v


def ready(map_name: str, out_dir: Path) -> dict | None:
    """The overview numbers of `map_name` once its radar is in `out_dir`, or None."""
    name = checked_map(map_name)
    if not (out_dir / f"{name}.png").is_file() or not (out_dir / f"{name}.json").is_file():
        return None
    return json.loads((out_dir / f"{name}.json").read_text(encoding="utf-8"))
