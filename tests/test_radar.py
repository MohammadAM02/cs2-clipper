"""The map radars: the VPK index, the texture decoder (uncompressed and LZ4), the overview numbers and the PNG. The
archives and textures here are made in the test, the way CS2 lays them out; the last test reads a real radar when CS2
is installed."""

from __future__ import annotations

import struct
import zlib

import pytest

from clipper import radar
from clipper.radar import RadarError, VpkEntry


def make_vpk(folder, entries: dict[str, bytes], in_index: frozenset[str] = frozenset()) -> None:
    """A VPK v2 index (pak01_dir.vpk) and its archive (pak01_000.vpk). Names in `in_index` keep their data in the index."""
    tree = bytearray()
    archive, index_data = bytearray(), bytearray()
    groups: dict[str, dict[str, list[tuple[str, bytes]]]] = {}
    for path, body in entries.items():
        folder_name, base = path.rsplit("/", 1)
        name, ext = base.rsplit(".", 1)
        groups.setdefault(ext, {}).setdefault(folder_name, []).append((name, body))
    for ext, folders in groups.items():
        tree += ext.encode() + b"\x00"
        for folder_name, files in folders.items():
            tree += folder_name.encode() + b"\x00"
            for name, body in files:
                in_dir = f"{folder_name}/{name}.{ext}" in in_index
                if in_dir:
                    archive_number, offset = 0x7FFF, len(index_data)
                    index_data += body
                else:
                    archive_number, offset = 0, len(archive)
                    archive += body
                tree += name.encode() + b"\x00" + struct.pack("<IHHIIH", 0, 0, archive_number, offset, len(body), 0xFFFF)
            tree += b"\x00"
        tree += b"\x00"
    tree += b"\x00"
    header = struct.pack("<IIIIIII", 0x55AA1234, 2, len(tree), 0, 0, 0, 0)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "pak01_dir.vpk").write_bytes(header + bytes(tree) + bytes(index_data))
    (folder / "pak01_000.vpk").write_bytes(bytes(archive))


def lz4_run(pixel: bytes, pixels: int) -> bytes:
    """An LZ4 block for `pixels` copies of one 4-byte pixel: the first pixel as literals, then one match repeating it."""
    match_extra = 4 * pixels - 4 - 4                # the match length less the 4 every match starts from
    out = bytearray([(4 << 4) | min(match_extra, 15)])
    out += pixel + struct.pack("<H", 4)             # one pixel back
    if match_extra >= 15:
        rest = match_extra - 15
        while rest >= 255:
            out.append(255)
            rest -= 255
        out.append(rest)
    return bytes(out)


def vtex(width: int, height: int, pixels: bytes, *, fmt: int = 28, compressed: bool = False) -> bytes:
    """A texture resource with one mip, as CS2 writes one: the resource header, the DATA block (header, and for a
    compressed mip the COMPRESSED_MIP_SIZE extra data), then the mip."""
    raw = width * height * 4
    if compressed:
        mip = lz4_run(pixels[:4], raw // 4)
    else:
        mip = pixels
    extra = b""
    count = 0
    if compressed:
        extra = struct.pack("<III", 4, 8, 16) + struct.pack("<IIII", 1, 8, 1, len(mip))
        count = 1
    texture = (struct.pack("<HH", 1, 0) + bytes(16) + struct.pack("<HHHBBI", width, height, 1, fmt, 1, 0)
               + struct.pack("<II", 8, count) + extra)
    block = texture
    header = struct.pack("<IHHII", 0, 12, 1, 8, 1)
    table = b"DATA" + struct.pack("<II", 8, len(block))
    body = header + table + block + mip
    return struct.pack("<I", len(body)) + body[4:]


def test_the_index_finds_its_entries_by_full_path_and_their_archive(tmp_path):
    make_vpk(tmp_path, {"resource/overviews/de_test.txt": b"hello",
                        "panorama/images/overheadmaps/de_test_radar_psd.vtex_c": b"radar"})
    dir_bytes = (tmp_path / "pak01_dir.vpk").read_bytes()
    found = radar.vpk_index(dir_bytes, ["resource/overviews/de_test.txt", "missing/none.txt"])
    assert set(found) == {"resource/overviews/de_test.txt"}
    entry = found["resource/overviews/de_test.txt"]
    assert (entry.archive, entry.length) == (0, 5)
    assert radar.read_vpk_entry(tmp_path, dir_bytes, entry) == b"hello"


def test_an_entry_kept_in_the_index_is_read_from_the_index(tmp_path):
    make_vpk(tmp_path, {"resource/overviews/de_small.txt": b"small one"},
             in_index=frozenset({"resource/overviews/de_small.txt"}))
    dir_bytes = (tmp_path / "pak01_dir.vpk").read_bytes()
    entry = radar.vpk_index(dir_bytes, ["resource/overviews/de_small.txt"])["resource/overviews/de_small.txt"]
    assert entry.archive == radar.IN_DIR_ARCHIVE
    assert radar.read_vpk_entry(tmp_path, dir_bytes, entry) == b"small one"


def test_an_entry_with_preload_bytes_gives_them_before_the_archive_bytes(tmp_path):
    entry = VpkEntry(archive=0, offset=0, length=3, preload=b"pre")
    (tmp_path / "pak01_000.vpk").write_bytes(b"abc")
    assert radar.read_vpk_entry(tmp_path, b"", entry) == b"preabc"


def test_lz4_decodes_literals_and_a_match_that_overlaps_itself():
    # literals "ab", then a match of 6 bytes from 2 back ("abababab" in all), then one trailing literal "z"
    block = bytes([(2 << 4) | 2, ord("a"), ord("b"), 2, 0, (1 << 4), ord("z")])
    assert radar.lz4_block_decode(block, 9) == b"abababab" + b"z"
    with pytest.raises(RadarError, match="LZ4 gave 9 bytes, expected 10"):
        radar.lz4_block_decode(block, 10)


def test_a_texture_in_bgra_uncompressed_comes_out_as_rgba():
    pixel = bytes([10, 20, 30, 255])                          # B G R A
    texture = radar.decode_vtex(vtex(4, 4, pixel * 16))
    assert (texture.width, texture.height) == (4, 4)
    assert texture.rgba == bytes([30, 20, 10, 255]) * 16


def test_a_texture_with_an_lz4_mip_is_decompressed_before_the_colours_are_swapped():
    pixel = bytes([10, 20, 30, 255])
    assert radar.decode_vtex(vtex(4, 4, pixel * 16, compressed=True)).rgba == bytes([30, 20, 10, 255]) * 16


def test_a_texture_format_that_is_not_handled_says_so():
    with pytest.raises(RadarError, match="texture format 20 is not handled"):
        radar.decode_vtex(vtex(4, 4, bytes(64), fmt=20))


def test_the_overview_numbers_are_read_from_its_key_values():
    text = '"de_x"\n{\n\t"material"\t"overviews/de_x"\t// texture file\n\t"pos_x"\t\t"-3230"\n\t"pos_y"\t\t"1713"\n' \
           '\t"scale"\t\t"5.00"\n\t"rotate"\t"1"\n}\n'
    found = radar.parse_overview(text)
    assert (found["pos_x"], found["pos_y"], found["scale"], found["rotate"]) == ("-3230", "1713", "5.00", "1")


def test_a_world_point_is_placed_on_the_radar_image_and_a_turned_radar_is_turned():
    meta = {"pos_x": 0.0, "pos_y": 0.0, "scale": 2.0, "width": 4, "height": 4, "rotate": 0}
    assert radar.world_to_pixel(4.0, -6.0, meta) == (2.0, 3.0)
    assert radar.world_to_pixel(4.0, -6.0, {**meta, "rotate": 1}) == (1.0, 2.0)


def test_build_writes_the_png_and_the_overview_numbers_for_a_map(tmp_path):
    game = tmp_path / "csgo"
    pixel = bytes([1, 2, 3, 4])
    make_vpk(game, {
        "panorama/images/overheadmaps/de_test_radar_psd.vtex_c": vtex(4, 4, pixel * 16, compressed=True),
        "resource/overviews/de_test.txt": b'"pos_x" "-100"\n"pos_y" "200"\n"scale" "4.0"\n"rotate" "0"\n',
    })
    out = tmp_path / "radar"
    meta = radar.build("de_test", game, out)
    assert meta == {"map": "de_test", "width": 4, "height": 4, "pos_x": -100.0, "pos_y": 200.0, "scale": 4.0,
                    "rotate": 0}
    assert radar.ready("de_test", out) == meta
    assert radar.ready("de_none", out) is None
    png = (out / "de_test.png").read_bytes()
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert zlib.decompress(png_data(png))[:5] == bytes([0, 3, 2, 1, 4])   # filter byte, then the first pixel as RGBA


def png_data(png: bytes) -> bytes:
    """The compressed image data of a PNG: its IDAT chunks, joined."""
    pos, data = 8, b""
    while pos < len(png):
        size = struct.unpack_from(">I", png, pos)[0]
        if png[pos + 4:pos + 8] == b"IDAT":
            data += png[pos + 8:pos + 8 + size]
        pos += 12 + size
    return data


def test_a_map_name_that_is_not_a_map_is_refused():
    with pytest.raises(RadarError, match="not a map name"):
        radar.checked_map("../secret")
    assert radar.checked_map("de_mirage") == "de_mirage"


def test_building_a_radar_the_game_does_not_have_says_so(tmp_path):
    make_vpk(tmp_path, {"resource/overviews/de_only.txt": b'"scale" "1"'})
    with pytest.raises(RadarError, match="has no radar or overview"):
        radar.build("de_only", tmp_path, tmp_path / "out")


def test_a_real_radar_is_read_from_the_installed_game_when_there_is_one(tmp_path):
    from clipper import cs2_paths

    exe = cs2_paths.find_cs2_exe()
    if exe is None:
        pytest.skip("CS2 is not installed here")
    meta = radar.build("de_mirage", cs2_paths.csgo_dir(exe), tmp_path)
    assert (meta["width"], meta["height"], meta["rotate"]) == (1024, 1024, 0)
    assert (tmp_path / "de_mirage.png").stat().st_size > 10_000
