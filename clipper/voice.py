"""Who talks when in a Demo: the tick of every voice message (`svc_VoiceData`) a CS2 demo holds. GOTV records every
player's voice, both teams', whoever could hear it in the game, and each message is one 10 ms Opus frame of one
player (seen in a FACEIT demo, 2026-10-12).

A CS2 demo (`PBDEMS2`) is a 16-byte header, then commands: a varint kind (plus 64 when the payload is Snappy
compressed), a varint tick, a varint size and the payload. A `DEM_Packet`'s payload is a `CDemoPacket` whose `data`
(field 3) is a bit stream of net messages, each a UBitVar kind, a varint size and that many bytes. Reading a 227 MB
demo this way took 8 s."""

from __future__ import annotations

import mmap
from pathlib import Path

MAGIC = b"PBDEMS2\x00"
HEADER_BYTES = 16
DEM_PACKET = 7
COMPRESSED = 64
SVC_VOICE_DATA = 47
FRAME_S = 0.01          # one voice message: 10 ms of one player talking


class VoiceError(Exception):
    """The file is not a CS2 demo."""


def talk_ticks(dem: Path) -> list[int]:
    """The tick of each voice message in the demo at `dem`, in the order recorded. A demo cut off part way gives the
    ticks before the cut, and a packet that cannot be read is passed over. Raises VoiceError when the file is not a
    CS2 demo, OSError when it cannot be read."""
    with open(dem, "rb") as file:
        if file.read(HEADER_BYTES)[:len(MAGIC)] != MAGIC:
            raise VoiceError(f"{dem.name} is not a CS2 demo")
        with mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as data:
            return _scan(data)


def _scan(data: mmap.mmap) -> list[int]:
    ticks: list[int] = []
    pos, end = HEADER_BYTES, len(data)
    while pos < end:
        try:
            kind, pos = _varint(data, pos)
            tick, pos = _varint(data, pos)
            size, pos = _varint(data, pos)
        except IndexError:
            break                                   # cut off inside a command's header
        if pos + size > end:
            break                                   # cut off inside its payload
        if kind & ~COMPRESSED == DEM_PACKET:
            try:
                payload = data[pos:pos + size]
                if kind & COMPRESSED:
                    payload = unsnappy(payload)
                ticks.extend([tick] * _voice_messages(_packet_data(payload)))
            except (ValueError, IndexError):
                pass                                # a packet that cannot be read holds no voice we can count
        pos += size
    return ticks


def _varint(data, pos: int) -> tuple[int, int]:
    """The varint at `pos` and where it ends. IndexError when the data ends first."""
    value = shift = 0
    while True:
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def unsnappy(src: bytes) -> bytes:
    """`src` decompressed: Snappy's raw format, which CS2 compresses demo commands with. ValueError when it is not."""
    size, pos = _varint(src, 0)
    out = bytearray()
    while pos < len(src):
        tag = src[pos]
        pos += 1
        if tag & 3 == 0:                            # a literal: the length, then that many bytes
            length = tag >> 2
            if length >= 60:                        # 60 to 63: the length is in the next 1 to 4 bytes
                extra = length - 59
                length = int.from_bytes(src[pos:pos + extra], "little")
                pos += extra
            out += src[pos:pos + length + 1]
            pos += length + 1
            continue
        if tag & 3 == 1:                            # a copy of 4 to 11 bytes, its offset in 11 bits
            length = ((tag >> 2) & 7) + 4
            offset = ((tag >> 5) << 8) | src[pos]
            pos += 1
        else:                                       # a copy of 1 to 64 bytes, its offset in the next 2 or 4 bytes
            width = 2 if tag & 3 == 2 else 4
            length = (tag >> 2) + 1
            offset = int.from_bytes(src[pos:pos + width], "little")
            pos += width
        if not 0 < offset <= len(out):
            raise ValueError("a Snappy copy reaches before the start")
        start = len(out) - offset
        # A copy longer than its offset repeats the bytes it has just written.
        out += (out[start:] * (length // offset + 1))[:length]
    if len(out) != size:
        raise ValueError(f"Snappy data says {size} bytes, holds {len(out)}")
    return bytes(out)


def _packet_data(payload: bytes) -> bytes:
    """A CDemoPacket's `data` (field 3), or nothing when it has none."""
    pos = 0
    while pos < len(payload):
        key, pos = _varint(payload, pos)
        wire = key & 7
        if wire == 2:
            length, pos = _varint(payload, pos)
            if key >> 3 == 3:
                return payload[pos:pos + length]
            pos += length
        elif wire == 0:
            _, pos = _varint(payload, pos)
        elif wire == 1:
            pos += 8
        elif wire == 5:
            pos += 4
        else:
            raise ValueError(f"not a CDemoPacket: wire type {wire}")
    return b""


def _voice_messages(data: bytes) -> int:
    """How many of the net messages in a packet's bit stream are voice."""
    bits = _Bits(data)
    count = 0
    while bits.left() >= 8:                         # fewer bits than that are the padding at the end
        kind = bits.ubitvar()
        size = bits.varint()
        if kind == SVC_VOICE_DATA:
            count += 1
        bits.skip(size * 8)
    return count


class _Bits:
    """A packet's bit stream, read least significant bit first."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._pos = 0
        self._end = len(data) * 8

    def left(self) -> int:
        return self._end - self._pos

    def skip(self, bits: int) -> None:
        self._pos += bits

    def read(self, bits: int) -> int:
        """The next `bits` bits (up to 32)."""
        byte, shift = divmod(self._pos, 8)
        self._pos += bits
        return (int.from_bytes(self._data[byte:byte + 5], "little") >> shift) & ((1 << bits) - 1)

    def ubitvar(self) -> int:
        """Valve's UBitVar: 6 bits, whose top two say whether 4, 8 or 28 more bits follow."""
        value = self.read(6)
        extra = {16: 4, 32: 8, 48: 28}.get(value & 0x30)
        return value if extra is None else (value & 15) | (self.read(extra) << 4)

    def varint(self) -> int:
        value = shift = 0
        while True:
            byte = self.read(8)
            value |= (byte & 0x7F) << shift
            if not byte & 0x80 or shift >= 28:      # a varint32 is 5 bytes at most
                return value
            shift += 7
