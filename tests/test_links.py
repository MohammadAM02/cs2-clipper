import sys

import pytest

from clipper import links
from clipper.links import Link


@pytest.mark.parametrize("page", ["/status", "/demos", "/reels", "/settings", "/reels?play=12"])
def test_every_page_the_window_shows_makes_a_link_that_reads_back_as_itself(page):
    assert links.parse(links.to_page(page)) == Link("open", page)


def test_the_links_toasts_carry_read_back():
    assert links.to_page("/reels?play=12") == "cs2clipper://reels?play=12"
    assert links.parse(links.to_reveal(12)) == Link("reveal", "/reels?play=12", reel=12)
    assert links.parse(links.to_snooze(60)) == Link("snooze", "/status", minutes=60)


@pytest.mark.parametrize("url", ["CS2Clipper://Reels?play=7", "cs2clipper://reels/?play=7", " cs2clipper://reels?play=7 "])
def test_a_link_survives_what_windows_and_browsers_do_to_it(url):
    assert links.parse(url) == Link("open", "/reels?play=7")


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8765/status", "cs2clipper://api/quit", "cs2clipper://reels?play=x", "cs2clipper://reels?play=",
    "cs2clipper://reels?play=1&play=2", "cs2clipper://reels?play=1&x=2", "cs2clipper://reels?other=1",
    "cs2clipper://status/../settings", "cs2clipper://reveal", "cs2clipper://reveal?reel=-1",
    "cs2clipper://reveal?reel=1&reel=2", "cs2clipper://snooze?minutes=0", "cs2clipper://snooze?minutes=1441",
    "cs2clipper://snooze?minutes=1e3", "cs2clipper://reels?play=1#x", "cs2clipper://" + "a" * 300, "", None, 12,
])
def test_anything_else_is_not_a_link(url):
    assert links.parse(url) is None


def test_only_the_pages_the_window_shows_make_links():
    with pytest.raises(ValueError):
        links.to_page("/api/quit")


def test_registering_makes_cs2clipper_links_start_this_app_with_the_link(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Programs\CS2Clipper\CS2Clipper.exe")
    written = []
    links.register(lambda key, name, value: written.append((key, name, value)))
    assert written == [
        (r"Software\Classes\cs2clipper", "", "URL:CS2 Clipper link"),
        (r"Software\Classes\cs2clipper", "URL Protocol", ""),
        (r"Software\Classes\cs2clipper\shell\open\command", "",
         r'"C:\Programs\CS2Clipper\CS2Clipper.exe" --link "%1"'),
    ]


def test_run_from_source_a_link_starts_the_package_without_a_console(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    (tmp_path / "pythonw.exe").write_bytes(b"")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
    assert links.command() == f'"{tmp_path / "pythonw.exe"}" -m clipper --link "%1"'
