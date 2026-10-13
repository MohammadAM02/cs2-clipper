"""cs2clipper: links, which toast buttons open. Windows starts the app with ``--link <url>`` (the app registers
the scheme for itself at every start, `register`); that second start hands the link to the running copy, which
shows a page in its window, shows a Reel's file in Explorer, or snoozes renders. A web page can open cs2clipper:
links too, so only the forms below are links at all: anything else is refused, never guessed at.

    cs2clipper://status            (also demos, reels, settings): show that page
    cs2clipper://reels?play=12     show Reels and play Reel 12
    cs2clipper://reveal?reel=12    show Reel 12's file in Explorer
    cs2clipper://snooze?minutes=60 no render starts for an hour
"""

from __future__ import annotations

import re
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

SCHEME = "cs2clipper"
PAGES = ("/status", "/demos", "/reels", "/settings")   # what the window may be asked to show
MAX_SNOOZE_MINUTES = 24 * 60
_PLAY_PAGE = re.compile(r"/reels\?play=[0-9]{1,9}")
_NUMBER = re.compile(r"[0-9]{1,9}")
_KEY = rf"Software\Classes\{SCHEME}"


@dataclass(frozen=True)
class Link:
    """What a link asks for. `page` is what a fresh start shows when no copy is running to follow it."""

    action: str                  # "open", "reveal" or "snooze"
    page: str
    reel: int | None = None      # reveal: the Reel whose file Explorer shows
    minutes: int = 0             # snooze: how long no render starts


def valid_page(page: object) -> bool:
    """A page the window may be asked to show: one of PAGES, or Reels playing one Reel."""
    return isinstance(page, str) and (page in PAGES or _PLAY_PAGE.fullmatch(page) is not None)


def to_page(page: str) -> str:
    """The link that shows `page`, one `valid_page` allows."""
    if not valid_page(page):
        raise ValueError(f"not a page the window shows: {page!r}")
    path, _, query = page.partition("?")
    return f"{SCHEME}://{path.removeprefix('/')}" + (f"?{query}" if query else "")


def to_reveal(reel_id: int) -> str:
    return f"{SCHEME}://reveal?reel={int(reel_id)}"


def to_snooze(minutes: int) -> str:
    return f"{SCHEME}://snooze?minutes={int(minutes)}"


def parse(url: object) -> Link | None:
    """The Link `url` is, or None when it is not one of the forms above."""
    if not isinstance(url, str) or len(url) > 200:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme.lower() != SCHEME or parts.path not in ("", "/") or parts.fragment:
        return None
    query = parse_qs(parts.query, keep_blank_values=True)
    if any(len(values) != 1 for values in query.values()):
        return None
    args = {key: values[0] for key, values in query.items()}
    host = parts.netloc.lower()
    if host == "reveal":
        if set(args) != {"reel"} or not _NUMBER.fullmatch(args["reel"]):
            return None
        reel = int(args["reel"])
        return Link("reveal", f"/reels?play={reel}", reel=reel)
    if host == "snooze":
        if set(args) != {"minutes"} or not _NUMBER.fullmatch(args["minutes"]):
            return None
        minutes = int(args["minutes"])
        return Link("snooze", "/status", minutes=minutes) if 1 <= minutes <= MAX_SNOOZE_MINUTES else None
    page = f"/{host}" + "".join(f"?{key}={value}" for key, value in args.items())
    return Link("open", page) if valid_page(page) else None


def command() -> str:
    """How Windows starts this app with a link: the exe itself, or, run from source, this Python's windowless
    twin running the package."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --link "%1"'
    python = Path(sys.executable)
    windowless = python.with_name("pythonw.exe")
    return f'"{windowless if windowless.is_file() else python}" -m clipper --link "%1"'


def register(write: Callable[[str, str, str], None]) -> None:
    """Makes cs2clipper: links start this app, for this Windows user only (no admin prompt). Run at every
    start: the exe may have moved since the last one, and a copy run from source registers itself too.
    `write(key, name, value)` sets a string under HKEY_CURRENT_USER (`windows.set_user_value`)."""
    write(_KEY, "", "URL:CS2 Clipper link")
    write(_KEY, "URL Protocol", "")
    write(_KEY + r"\shell\open\command", "", command())
