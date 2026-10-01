"""Tests for clipper.download. Nothing comes from the network: a fake opener hands back the bytes, and
the archives are small zips built under tmp_path."""
from __future__ import annotations

import hashlib
import io
import os
import time
import urllib.error
import zipfile

import pytest

from clipper import download
from clipper.download import DownloadError
from tests.fakes import refusing

URL = "https://example.test/tool.zip"
BODY = b"the tool's bytes" * 1000
GOOD = hashlib.sha256(BODY).hexdigest()


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, fail_after: int | None = None):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}
        self._fail_after = fail_after

    def read(self, size=-1):
        if self._fail_after is not None and self.tell() >= self._fail_after:
            raise ConnectionResetError("the connection dropped")
        return super().read(size)


def opener_for(body: bytes, **kwargs):
    opened = []

    def opener(url):
        opened.append(url)
        return FakeResponse(body, **kwargs)

    opener.opened = opened
    return opener


def leftovers(folder):
    return sorted(p.name for p in folder.iterdir())


# --- fetch --------------------------------------------------------------------------------------------


def test_fetch_saves_a_file_whose_hash_matches_and_reports_progress(tmp_path):
    dest = tmp_path / "downloads" / "tool.zip"
    seen = []

    result = download.fetch(URL, dest, sha256=GOOD, size=len(BODY), opener=opener_for(BODY),
                            progress=lambda done, total: seen.append((done, total)))

    assert result == dest
    assert dest.read_bytes() == BODY
    assert seen[-1] == (len(BODY), len(BODY))
    assert leftovers(dest.parent) == ["tool.zip"]


def test_fetch_refuses_a_file_whose_hash_is_wrong_and_leaves_nothing_behind(tmp_path):
    dest = tmp_path / "tool.zip"

    with pytest.raises(DownloadError, match="tool.zip is not the file that was expected"):
        download.fetch(URL, dest, sha256="0" * 64, opener=opener_for(BODY))

    assert leftovers(tmp_path) == []


def test_fetch_refuses_a_file_of_the_wrong_size(tmp_path):
    dest = tmp_path / "tool.zip"

    with pytest.raises(DownloadError, match="tool.zip is not the file that was expected"):
        download.fetch(URL, dest, sha256=GOOD, size=len(BODY) + 1, opener=opener_for(BODY))

    assert leftovers(tmp_path) == []


def test_fetch_does_not_download_again_what_is_already_there(tmp_path):
    dest = tmp_path / "tool.zip"
    dest.write_bytes(BODY)

    assert download.fetch(URL, dest, sha256=GOOD, opener=lambda url: pytest.fail("downloaded again")) == dest


def test_fetch_downloads_again_over_a_file_that_is_there_but_wrong(tmp_path):
    dest = tmp_path / "tool.zip"
    dest.write_bytes(b"half of it")

    download.fetch(URL, dest, sha256=GOOD, opener=opener_for(BODY))

    assert dest.read_bytes() == BODY


def test_fetch_only_speaks_https(tmp_path):
    opener = opener_for(BODY)

    with pytest.raises(DownloadError, match="not an https address"):
        download.fetch("http://example.test/tool.zip", tmp_path / "tool.zip", sha256=GOOD, opener=opener)

    assert opener.opened == []


def test_fetch_reports_a_dropped_connection_and_keeps_no_half_file(tmp_path):
    dest = tmp_path / "tool.zip"

    with pytest.raises(DownloadError, match="could not download tool.zip"):
        download.fetch(URL, dest, sha256=GOOD, opener=opener_for(BODY, fail_after=1))

    assert leftovers(tmp_path) == []


def test_fetch_reports_a_site_that_cannot_be_reached(tmp_path):
    def opener(url):
        raise urllib.error.URLError("no route to host")

    with pytest.raises(DownloadError, match="could not download tool.zip"):
        download.fetch(URL, tmp_path / "tool.zip", sha256=GOOD, opener=opener)


# --- extract ------------------------------------------------------------------------------------------


def make_zip(path, entries: dict[str, bytes]):
    with zipfile.ZipFile(path, "w") as zf:
        for name, body in entries.items():
            zf.writestr(name, body)
    return path


def tree(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*")) if p.is_file()}


def test_extract_unpacks_an_archive_as_it_is(tmp_path):
    archive = make_zip(tmp_path / "hlae.zip", {"HLAE.exe": b"exe", "x64/hook.dll": b"dll"})

    download.extract(archive, tmp_path / "hlae")

    assert tree(tmp_path / "hlae") == {"HLAE.exe": b"exe", "x64/hook.dll": b"dll"}
    assert leftovers(tmp_path) == ["hlae", "hlae.zip"]


def test_extract_can_drop_the_archives_one_top_folder(tmp_path):
    archive = make_zip(tmp_path / "pg.zip", {"postgresql-17/": b"", "postgresql-17/bin/pg_ctl.exe": b"ctl",
                                             "postgresql-17/LICENSE": b"licence"})

    download.extract(archive, tmp_path / "pgsql", strip_top=True)

    assert tree(tmp_path / "pgsql") == {"bin/pg_ctl.exe": b"ctl", "LICENSE": b"licence"}


def test_extract_refuses_to_drop_a_top_folder_that_is_not_the_only_one(tmp_path):
    archive = make_zip(tmp_path / "pg.zip", {"one/a.txt": b"a", "two/b.txt": b"b"})

    with pytest.raises(DownloadError, match="pg.zip is not laid out as expected"):
        download.extract(archive, tmp_path / "pgsql", strip_top=True)

    assert leftovers(tmp_path) == ["pg.zip"]


def test_extract_leaves_out_what_is_not_wanted(tmp_path):
    archive = make_zip(tmp_path / "ff.zip", {"ff/bin/ffmpeg.exe": b"1", "ff/bin/ffplay.exe": b"2",
                                             "ff/doc/ffmpeg.html": b"3", "ff/LICENSE": b"4"})

    download.extract(archive, tmp_path / "ffmpeg", strip_top=True,
                     want=lambda name: name in ("bin/ffmpeg.exe", "LICENSE"))

    assert tree(tmp_path / "ffmpeg") == {"bin/ffmpeg.exe": b"1", "LICENSE": b"4"}


@pytest.mark.parametrize("name", ["../evil.txt", "ok/../../evil.txt", "/evil.txt", "C:/evil.txt", "..\\evil.txt"])
def test_extract_refuses_an_archive_that_would_write_outside_its_folder(tmp_path, name):
    archive = make_zip(tmp_path / "bad.zip", {"fine.txt": b"fine", name: b"evil"})

    with pytest.raises(DownloadError, match="bad.zip holds a path that leaves its folder"):
        download.extract(archive, tmp_path / "out" / "tool")

    assert not (tmp_path / "out").exists()
    assert leftovers(tmp_path) == ["bad.zip"]      # nothing was written, inside or outside


def test_extract_replaces_what_the_folder_held_before(tmp_path):
    dest = tmp_path / "hlae"
    (dest / "old").mkdir(parents=True)
    (dest / "old" / "stale.dll").write_bytes(b"stale")
    archive = make_zip(tmp_path / "hlae.zip", {"HLAE.exe": b"new"})

    download.extract(archive, dest)

    assert tree(dest) == {"HLAE.exe": b"new"}
    assert leftovers(tmp_path) == ["hlae", "hlae.zip"]


def test_extract_clears_away_what_an_interrupted_run_left(tmp_path):
    (tmp_path / "hlae.part").mkdir()
    (tmp_path / "hlae.part" / "half.dll").write_bytes(b"half")
    archive = make_zip(tmp_path / "hlae.zip", {"HLAE.exe": b"new"})

    download.extract(archive, tmp_path / "hlae")

    assert tree(tmp_path / "hlae") == {"HLAE.exe": b"new"}
    assert leftovers(tmp_path) == ["hlae", "hlae.zip"]


def test_extract_keeps_what_was_there_when_the_archive_is_broken(tmp_path):
    dest = tmp_path / "hlae"
    dest.mkdir()
    (dest / "HLAE.exe").write_bytes(b"working")
    archive = tmp_path / "hlae.zip"
    archive.write_bytes(b"this is not a zip")

    with pytest.raises(DownloadError, match="could not unpack hlae.zip"):
        download.extract(archive, dest)

    assert tree(dest) == {"HLAE.exe": b"working"}
    assert leftovers(tmp_path) == ["hlae", "hlae.zip"]


# --- fetch_json ---------------------------------------------------------------------------------------


def test_fetch_json_only_speaks_https():
    with pytest.raises(DownloadError, match="not an https address"):
        download.fetch_json("http://example.test/latest", opener=lambda url: pytest.fail("opened"))


def test_fetch_json_reads_an_object_and_reports_anything_else(tmp_path):
    assert download.fetch_json("https://example.test/latest", opener=opener_for(b'{"tag_name": "v1"}')) == {"tag_name": "v1"}
    with pytest.raises(DownloadError, match="could not read https://example.test/latest"):
        download.fetch_json("https://example.test/latest", opener=opener_for(b"<html>"))


def test_extract_gets_past_a_folder_that_is_in_use_for_a_moment(tmp_path, monkeypatch):
    archive = make_zip(tmp_path / "a.zip", {"bin/tool.exe": b"new"})
    dest = tmp_path / "tool"
    (dest / "bin").mkdir(parents=True)
    (dest / "bin" / "tool.exe").write_bytes(b"old")
    monkeypatch.setattr(os, "replace", refusing(3))
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    download.extract(archive, dest)

    assert tree(dest) == {"bin/tool.exe": b"new"}
    assert leftovers(tmp_path) == ["a.zip", "tool"]       # no .part and no .old left beside them
