"""Fetching and unpacking what setup installs.

A download is used only when its SHA-256 is the one expected, and it is written under another name
until then, so a half-downloaded file is never mistaken for a whole one. An archive is unpacked whole
or not at all: into ``<folder>.part`` first, then moved into place."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import shutil
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

from clipper import paths

CHUNK = 1024 * 1024
TIMEOUT_SECONDS = 30.0
GITHUB_API = "https://api.github.com/"
TOKEN_VARIABLE = "CLIPPER_GITHUB_TOKEN"


class DownloadError(Exception):
    """A file that could not be fetched, is not the file expected, or could not be unpacked."""


def headers_for(url: str) -> dict[str, str]:
    """What a request to `url` carries. GitHub's API answers an address sixty times an hour, and the
    runner a release is built on shares its address with many others: a token in CLIPPER_GITHUB_TOKEN,
    which the release workflow sets, goes to GitHub's API and to nothing else."""
    headers = {"User-Agent": "cs2-clipper"}
    token = os.environ.get(TOKEN_VARIABLE)
    if token and url.startswith(GITHUB_API):
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _open(url: str):
    request = urllib.request.Request(url, headers=headers_for(url))
    return urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS)


def _https_only(url: str) -> None:
    if not url.lower().startswith("https://"):
        raise DownloadError(f"refusing {url}: not an https address")


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        while chunk := file.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(url: str, dest: Path, *, sha256: str, size: int | None = None,
          progress: Callable[[int, int], None] = lambda done, total: None,
          opener: Callable[[str], object] = _open) -> Path:
    """Downloads `url` to `dest` and returns `dest`. A `dest` that is already the expected file is
    kept as it is. `progress(done, total)` is called as the bytes arrive (`total` is 0 when unknown).
    Raises DownloadError, leaving no file behind, when the download fails or is not the expected file."""
    _https_only(url)
    if dest.is_file() and sha256_of(dest) == sha256.lower():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    done = 0
    try:
        with opener(url) as response, open(part, "wb") as out:
            total = size or int(response.headers.get("Content-Length") or 0)
            while chunk := response.read(CHUNK):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                progress(done, total)
        if (size is not None and done != size) or digest.hexdigest() != sha256.lower():
            raise DownloadError(f"{dest.name} is not the file that was expected (its size or SHA-256 differs)")
        paths.move_into_place(part, dest)
    except (OSError, http.client.HTTPException) as exc:      # urllib's URLError is an OSError
        raise DownloadError(f"could not download {dest.name}: {exc}") from exc
    finally:
        part.unlink(missing_ok=True)
    return dest


def fetch_json(url: str, *, opener: Callable[[str], object] = _open) -> dict:
    """The JSON object at `url`. Raises DownloadError when it cannot be had."""
    _https_only(url)
    try:
        with opener(url) as response:
            data = json.loads(response.read())
    except (OSError, http.client.HTTPException, ValueError) as exc:
        raise DownloadError(f"could not read {url}: {exc}") from exc
    if not isinstance(data, dict):
        raise DownloadError(f"could not read {url}: not a JSON object")
    return data


def _members(zf: zipfile.ZipFile, archive: Path, strip_top: bool) -> list[tuple[zipfile.ZipInfo, str]]:
    """Every entry with the path it is unpacked to, relative to the folder. Checked before anything is
    written: an entry that would land outside the folder refuses the whole archive."""
    infos = zf.infolist()
    for info in infos:
        name = info.filename
        if name.startswith("/") or ":" in name or "\\" in name or ".." in name.split("/"):
            raise DownloadError(f"{archive.name} holds a path that leaves its folder: {name!r}")
    if not strip_top:
        return [(info, info.filename) for info in infos]
    tops = {info.filename.split("/", 1)[0] for info in infos}
    if len(tops) != 1 or any("/" not in info.filename for info in infos):
        raise DownloadError(f"{archive.name} is not laid out as expected (no single top folder)")
    return [(info, info.filename.split("/", 1)[1]) for info in infos]


def _remove(folder: Path) -> None:
    if folder.exists():
        shutil.rmtree(folder)


def extract(archive: Path, dest: Path, *, strip_top: bool = False,
            want: Callable[[str], bool] = lambda name: True) -> None:
    """Unpacks the zip `archive` into the folder `dest`, replacing what `dest` held. `strip_top` drops
    the archive's one top folder; `want(name)` picks the entries to keep, by their path inside `dest`.
    Raises DownloadError, with `dest` as it was, when the archive is broken or unsafe."""
    part = dest.with_name(dest.name + ".part")
    old = dest.with_name(dest.name + ".old")
    try:
        with zipfile.ZipFile(archive) as zf:
            members = _members(zf, archive, strip_top)
            _remove(part)
            part.mkdir(parents=True)
            for info, name in members:
                if not name or not want(name):
                    continue
                target = part / name
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as source, open(target, "wb") as out:
                    shutil.copyfileobj(source, out, CHUNK)
        _remove(old)
        if dest.exists():
            paths.move_into_place(dest, old)
        try:
            paths.move_into_place(part, dest)
        except OSError:
            if old.exists():
                paths.move_into_place(old, dest)   # put back what was there
            raise
        _remove(old)
    except (zipfile.BadZipFile, OSError) as exc:
        _remove(part)
        raise DownloadError(f"could not unpack {archive.name}: {exc}") from exc
