"""The app's mark: the Slice mark (a play triangle cut at the italic lean) in the pages' orange on the cut-corner tile. The tray draws it at 64 px; the
packaged exe carries it as a .ico (its file icon, its taskbar button and the window's icon).

Adapted from thelifeofsuleyman/cs2-clipper's `aegis/app.py` (`_tray_image`) and
`packaging/make_icon.py` (MIT). What we changed: the mark is our plainer one, drawn at any size from its
64 px layout, and the .ico also holds a 24 px image.

MIT License

Copyright (c) 2026 thelifeofsuleyman

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)


# Slice: the play triangle cut at the Saira italic lean, tip knocked up and out (64 px grid).
TILE = ((14, 2), (62, 2), (62, 50), (50, 62), (2, 62), (2, 14))      # the pages' .cut shape
BODY = ((18, 50), (30.05, 43.22), (36.22, 24.25), (18, 14))
TIP = ((51.6, 28.4), (40.86, 22.36), (36.06, 37.14))
TIP_SMALL = ((53.0, 27.6), (42.3, 21.6), (37.5, 36.4))               # wider cut so it survives <= 24 px


def mark(size: int) -> Image.Image:
    """The Slice mark on a clear `size` x `size` image, drawn 4x and downscaled for smooth edges."""
    big = size * 4
    scale = big / 64
    image = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    sc = lambda pts: [(x * scale, y * scale) for x, y in pts]
    draw.polygon(sc(TILE), fill="#141414")
    draw.polygon(sc(BODY), fill="#ff5500")
    draw.polygon(sc(TIP_SMALL if size <= 24 else TIP), fill="#ff5500")
    return image.resize((size, size), Image.LANCZOS)


def write_ico(path: Path) -> None:
    """Writes the mark as a .ico holding every size in ICO_SIZES, so Windows never scales one up."""
    mark(256).save(path, format="ICO", sizes=[(size, size) for size in ICO_SIZES])
