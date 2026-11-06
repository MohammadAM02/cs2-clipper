"""Nugget's pixel sound effect, `NUGGET.burst` in nugget.js: the badge on every Highlight row of Reels.

Reels draws one badge per row and a few hundred rows is normal, so a badge has to stay a handful of elements. Drawn
as one <rect> per pixel (0.11.0), 510 rows made Reels 164,000 elements and every restyle of the page took 0.4 s.
These run nugget.js under Node.js, as the page runs it, and are skipped when Node is not on PATH.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

NUGGET_JS = Path(__file__).resolve().parent.parent / "clipper" / "pages" / "nugget.js"
INK = "#0D0A08"
RUN = re.compile(r"M(\d+) (\d+)h(\d+)v1h-\3z")          # a run of whole pixels along one row

# The star as 0.11.0 drew it, one <rect> per pixel: k is the ink outline, o the fill.
STAR = """
..........................
..........................
............kk............
...........kook...........
.........k.kook.k.........
....kk..kokkookkok........
...kookkkooooooookkk......
....koookooooooookook.....
.....kooooooooooooook.....
.....koooooooooooookkkkk..
....koooooooooooooooooook.
...kooooooooooooooooookk..
....kooooooooooooooook....
....kooooooooooooooook....
...kooooooooooooooooook...
....kooooooooooooooook....
.....koooooooooooookk.....
.....kooooooooooooook.....
....koookooooooookook.....
...kookkkooooooookkk......
....kk..kokkookoook.......
.........k.kookkook.......
............kk..kok.......
.................k........
..........................
..........................
"""


def burst(word: str, fill: str) -> str:
    """`NUGGET.burst(word, fill)`, from nugget.js as it ships, run by Node.js."""
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")
    script = (f"const src = require('fs').readFileSync({json.dumps(str(NUGGET_JS))}, 'utf8');"
              "const NUGGET = require('vm').runInNewContext(src + '\\n;NUGGET', {});"
              f"process.stdout.write(NUGGET.burst({json.dumps(word)}, {json.dumps(fill)}));")
    return subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True,
                          timeout=60).stdout


def pixels(svg: str) -> dict[str, set[tuple[int, int]]]:
    """The pixels each colour paints, whether the badge draws them as <rect>s or as <path> runs."""
    painted: dict[str, set[tuple[int, int]]] = {}
    for x, y, w, h, fill in re.findall(r'<rect x="(\d+)" y="(\d+)" width="(\d+)" height="(\d+)" fill="([^"]+)"/>', svg):
        painted.setdefault(fill, set()).update((int(x) + i, int(y) + j) for i in range(int(w)) for j in range(int(h)))
    for fill, d in re.findall(r'<path fill="([^"]+)" d="([^"]+)"/>', svg):
        assert RUN.sub("", d) == "", f"not runs of whole pixels: {d}"
        for x, y, n in RUN.findall(d):
            painted.setdefault(fill, set()).update((int(x) + i, int(y)) for i in range(int(n)))
    return painted


@pytest.mark.node
def test_a_burst_is_one_path_per_colour_not_an_element_per_pixel():
    svg = burst("ACE!", "#FF5500")
    assert re.findall(r"<(rect|path|polygon|polyline|circle|ellipse|line|g|use)\b", svg) == ["path", "path"]
    assert re.findall(r'<path fill="([^"]+)"', svg) == [INK, "#FF5500"]      # the outline, then the fill
    assert svg.endswith("</svg><b>ACE!</b>")


@pytest.mark.node
def test_a_burst_paints_the_same_star_in_any_colour():
    for fill in ("#FF5500", "#8F857A"):
        painted = pixels(burst("2K!", fill))
        assert set(painted) == {INK, fill}
        assert not painted[INK] & painted[fill]
        assert all(0 <= x < 26 and 0 <= y < 26 for x, y in painted[INK] | painted[fill])
        drawn = [["."] * 26 for _ in range(26)]
        for x, y in painted[INK]:
            drawn[y][x] = "k"
        for x, y in painted[fill]:
            drawn[y][x] = "o"
        assert "\n".join("".join(row) for row in drawn) == STAR.strip("\n"), fill
