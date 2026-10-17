import pytest

from clipper.voice import VoiceError, talk_ticks, unsnappy

VOICE = 47                                    # svc_VoiceData
SPEECH = b"\x21" + (76561198271290375).to_bytes(8, "little") + b"\x0a\x03\x08\x02\x12"


def varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte, value = value & 0x7F, value >> 7
        out.append(byte | 0x80 if value else byte)
        if not value:
            return bytes(out)


class Bits:
    """Writes a packet's bit stream the way CS2 reads it: least significant bit first."""

    def __init__(self) -> None:
        self.value = 0
        self.length = 0

    def write(self, value: int, bits: int) -> None:
        self.value |= value << self.length
        self.length += bits

    def message(self, kind: int, payload: bytes) -> None:
        if kind < 16:                         # UBitVar: 6 bits, the top two saying how many follow
            self.write(kind, 6)
        elif kind < 256:
            self.write((kind & 15) | 16, 6)
            self.write(kind >> 4, 4)
        elif kind < 4096:
            self.write((kind & 15) | 32, 6)
            self.write(kind >> 4, 8)
        else:
            self.write((kind & 15) | 48, 6)
            self.write(kind >> 4, 28)
        for byte in varint(len(payload)) + payload:
            self.write(byte, 8)

    def bytes(self) -> bytes:
        return self.value.to_bytes((self.length + 7) // 8, "little")


def packet(*messages: tuple[int, bytes]) -> bytes:
    """A CDemoPacket whose data (field 3) is these (kind, payload) net messages."""
    bits = Bits()
    for kind, payload in messages:
        bits.message(kind, payload)
    data = bits.bytes()
    return b"\x1a" + varint(len(data)) + data


def command(kind: int, tick: int, payload: bytes) -> bytes:
    return varint(kind) + varint(tick) + varint(len(payload)) + payload


def demo(tmp_path, *commands: bytes):
    path = tmp_path / "match.dem"
    path.write_bytes(b"PBDEMS2\x00" + bytes(8) + b"".join(commands))
    return path


def snappy_literal(data: bytes) -> bytes:
    """`data` as a Snappy stream of one literal (up to 256 bytes)."""
    tag = (len(data) - 1) << 2 if len(data) <= 60 else 60 << 2
    extra = b"" if len(data) <= 60 else bytes([len(data) - 1])
    return varint(len(data)) + bytes([tag]) + extra + data


def test_a_voice_message_is_found_at_the_tick_of_its_packet(tmp_path):
    path = demo(tmp_path, command(7, 640, packet((55, bytes(20)), (VOICE, SPEECH))))
    assert talk_ticks(path) == [640]


def test_each_voice_message_counts_once(tmp_path):
    path = demo(tmp_path,
                command(7, 700, packet((VOICE, SPEECH), (VOICE, SPEECH), (VOICE, SPEECH))),
                command(7, 702, packet((VOICE, SPEECH))))
    assert talk_ticks(path) == [700, 700, 700, 702]


def test_messages_of_any_kind_number_and_size_are_stepped_over(tmp_path):
    path = demo(tmp_path, command(7, 64, packet(
        (4, b"x"), (VOICE, SPEECH), (300, b"yyy"), (5000, bytes(200)), (55, bytes(1000)), (VOICE, SPEECH))))
    assert talk_ticks(path) == [64, 64]


def test_a_compressed_packet_is_read_too(tmp_path):
    path = demo(tmp_path, command(7 | 64, 900, snappy_literal(packet((VOICE, SPEECH)))))
    assert talk_ticks(path) == [900]


def test_commands_that_are_not_packets_are_passed_over(tmp_path):
    lookalike = packet((VOICE, SPEECH))
    path = demo(tmp_path, command(1, 0, lookalike), command(13, 50, lookalike), command(7, 64, lookalike))
    assert talk_ticks(path) == [64]


def test_a_packet_that_does_not_decompress_is_skipped(tmp_path):
    path = demo(tmp_path, command(7 | 64, 10, b"\x10\x09\x05"), command(7, 20, packet((VOICE, SPEECH))))
    assert talk_ticks(path) == [20]


def test_a_demo_cut_off_part_way_gives_the_voice_before_the_cut(tmp_path):
    path = demo(tmp_path, command(7, 10, packet((VOICE, SPEECH))), command(7, 20, packet((VOICE, SPEECH))))
    path.write_bytes(path.read_bytes()[:-3])
    assert talk_ticks(path) == [10]


def test_a_file_that_is_not_a_cs2_demo_is_refused(tmp_path):
    path = tmp_path / "csgo.dem"
    path.write_bytes(b"HL2DEMO\x00" + bytes(100))
    with pytest.raises(VoiceError, match="not a CS2 demo"):
        talk_ticks(path)


@pytest.mark.parametrize("stream, plain", [
    (b"\x08\x04ab\x09\x02", b"abababab"),                                        # a copy that overlaps itself
    (b"\x49\xf0\x45" + bytes(range(70)) + b"\x0a\x46\x00", bytes(range(70)) + b"\x00\x01\x02"),   # long literal
    (b"\x05\x08abc\x07\x03\x00\x00\x00", b"abcab"),                              # four-byte offset
])
def test_unsnappy_reads_literals_and_copies(stream, plain):
    assert unsnappy(stream) == plain


def test_unsnappy_refuses_a_copy_from_before_the_start():
    with pytest.raises(ValueError):
        unsnappy(b"\x04\x09\x05")
