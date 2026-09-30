"""Tests for clipper.icon: the app's mark, drawn for the tray and written as the exe's .ico."""
from __future__ import annotations

from PIL import Image

from clipper.icon import mark, write_ico

CLEAR = (0, 0, 0, 0)
TILE = (25, 27, 31, 255)      # #191b1f
ORANGE = (255, 85, 0, 255)    # #ff5500


def test_the_tray_mark_is_an_orange_play_triangle_on_a_dark_tile_with_clear_corners():
    image = mark(64)

    assert image.size == (64, 64)
    assert image.getpixel((0, 0)) == CLEAR       # outside the rounded corner: the taskbar shows through
    assert image.getpixel((8, 32)) == TILE       # on the tile, left of the triangle
    assert image.getpixel((32, 32)) == ORANGE    # inside the triangle


def test_a_larger_mark_is_the_same_drawing_scaled_up():
    image = mark(256)

    assert image.size == (256, 256)
    assert image.getpixel((0, 0)) == CLEAR
    assert image.getpixel((32, 128)) == TILE     # clear if the tile kept its 64 px coordinates
    assert image.getpixel((128, 128)) == ORANGE  # tile if the triangle kept its 64 px coordinates


def test_the_ico_holds_every_size_windows_picks_from(tmp_path):
    path = tmp_path / "CS2Clipper.ico"

    write_ico(path)

    assert Image.open(path).info["sizes"] == {
        (16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)}
