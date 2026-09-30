"""The app's mark: a play triangle in the pages' orange on a dark tile. The tray draws it at 64 px; the
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


def mark(size: int) -> Image.Image:
    """The mark on a clear `size` x `size` image, laid out on a 64 px grid: the tile covers pixels 2 to
    61 with 12 px corners, and the triangle points right from (24, 16)-(24, 48) to (50, 32)."""
    scale = size / 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    first, last = 2 * scale, (61 + 1) * scale - 1      # the tile's first and last pixel
    draw.rounded_rectangle((first, first, last, last), radius=12 * scale, fill="#191b1f")
    draw.polygon([(x * scale, y * scale) for x, y in ((24, 16), (24, 48), (50, 32))], fill="#ff5500")
    return image


def write_ico(path: Path) -> None:
    """Writes the mark as a .ico holding every size in ICO_SIZES, so Windows never scales one up."""
    mark(256).save(path, format="ICO", sizes=[(size, size) for size in ICO_SIZES])
