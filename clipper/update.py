"""Updates: a newer CS2 Clipper, found on GitHub and installed with one click.

The installed app asks GitHub for this repo's newest Release at start-up and then every six hours (an hour
after a failure). A Release newer than the running app is offered on the Status page with its notes, as long
as GitHub publishes a SHA-256 for its installer. Update downloads that installer, checks it, and runs it
silently with /RELAUNCH: the installer asks the running copy to quit, replaces the exe, keeps the shortcuts
the user chose and the app data folder, and starts the app again (packaging/cs2clipper.iss). Python running
the repo's source never looks for updates."""
from __future__ import annotations

import html
import logging
import os
import re
import subprocess
import threading
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from clipper import download
from clipper.components import Asset
from clipper.config import REPO_ROOT
from clipper.download import DownloadError

log = logging.getLogger(__name__)

REPO = "MohammadAM02/cs2-clipper"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
INSTALLER = "CS2Clipper-Setup.exe"
CHECK_SECONDS = 6 * 3600.0          # how often the installed app asks GitHub
RETRY_SECONDS = 3600.0              # and how soon again when GitHub could not be asked
# /RELAUNCH is ours (cs2clipper.iss): the app quits for the update, so the installer starts it again.
INSTALLER_FLAGS = ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH")
UP_TO_DATE = "CS2 Clipper is up to date."

_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
_SHA256 = re.compile(r"sha256:([0-9a-f]{64})")
_KEPT_INSTALLER = re.compile(r"CS2Clipper-Setup-(\d+\.\d+\.\d+)\.exe")


def running_version() -> str:
    """The version of the app that runs: pyproject.toml's. The exe carries that file, and its build stamps the
    same version on the exe (packaging/cs2clipper.spec), so the two cannot differ."""
    with (REPO_ROOT / "pyproject.toml").open("rb") as file:
        return tomllib.load(file)["project"]["version"]


def version_key(text: str) -> tuple[int, int, int] | None:
    """`text` ("0.3.0" or "v0.3.0") as numbers to compare, or None when it is not such a version."""
    found = _VERSION.fullmatch(text)
    return None if found is None else (int(found[1]), int(found[2]), int(found[3]))


def newer(version: str, than: str) -> bool:
    """Whether `version` comes after `than`. False when either is not a version."""
    mine, theirs = version_key(version), version_key(than)
    return mine is not None and theirs is not None and mine > theirs


@dataclass(frozen=True)
class Release:
    version: str        # "0.3.0"
    notes: str          # Markdown, as GitHub has it
    page: str           # the Release's page on GitHub
    installer: Asset    # named by its version, so an installer kept from one update is never taken for another's


def latest_release(fetch_json: Callable[[str], dict] = download.fetch_json) -> Release:
    """This repo's newest Release. Raises DownloadError when it cannot be read, has no installer, its tag is
    not a version, or GitHub publishes no SHA-256 for the installer."""
    data = fetch_json(LATEST_URL)
    try:
        tag = str(data["tag_name"])
        found = next(asset for asset in data["assets"] if asset["name"] == INSTALLER)
        url, size = str(found["browser_download_url"]), int(found["size"])
    except (KeyError, TypeError, ValueError, StopIteration) as exc:
        raise DownloadError(f"the newest release has no {INSTALLER}") from exc
    if version_key(tag) is None:
        raise DownloadError(f"the newest release's tag {tag!r} is not a version")
    digest = _SHA256.fullmatch(str(found.get("digest") or ""))
    if digest is None:
        raise DownloadError(f"the newest release publishes no SHA-256 for {INSTALLER}, so it cannot be checked")
    version = tag.removeprefix("v")
    return Release(version=version, notes=str(data.get("body") or ""), page=str(data.get("html_url") or ""),
                   installer=Asset(name=f"CS2Clipper-Setup-{version}.exe", url=url, sha256=digest[1], size=size))


# --- the notes, as the Status page shows them ----------------------------------------------------------

# One piece of a line that becomes markup: `code`, **bold**, [a link](https://...), or an https address.
_INLINE = re.compile(r"`([^`]+)`|\*\*(.+?)\*\*|\[([^\]]+)\]\((https://[^\s)\"<>]+)\)|(https://[^\s\"<>]*[^\s\"<>.,;:!?)])")
_HEADING = re.compile(r"#{1,6}\s+(.*)")
_ITEM = re.compile(r"[-*]\s+(.*)")


def _link(href: str, label: str) -> str:
    return f'<a href="{html.escape(href)}" target="_blank" rel="noopener">{html.escape(label)}</a>'


def _inline(text: str) -> str:
    out, at = [], 0
    for found in _INLINE.finditer(text):
        out.append(html.escape(text[at:found.start()]))
        code, bold, label, href, bare = found.groups()
        if code is not None:
            out.append(f"<code>{html.escape(code)}</code>")
        elif bold is not None:
            out.append(f"<b>{html.escape(bold)}</b>")
        elif label is not None:
            out.append(_link(href, label))
        else:
            out.append(_link(bare, bare))
        at = found.end()
    out.append(html.escape(text[at:]))
    return "".join(out)


def notes_html(notes: str) -> str:
    """A Release's notes as HTML for the Status page: headings, paragraphs, lists of `-` or `*` items, `code`,
    **bold** and https links. Everything else is text: nothing in the notes becomes markup of its own."""
    blocks: list[str] = []
    paragraph: list[str] = []
    items: list[str] = []

    def close() -> None:
        if paragraph:
            blocks.append(f"<p>{' '.join(paragraph)}</p>")
            paragraph.clear()
        if items:
            blocks.append("<ul>" + "".join(f"<li>{item}</li>" for item in items) + "</ul>")
            items.clear()

    for line in notes.splitlines():
        text = line.strip()
        heading, item = _HEADING.fullmatch(text), _ITEM.fullmatch(text)
        if not text:
            close()
        elif heading:
            close()
            blocks.append(f"<h3>{_inline(heading[1])}</h3>")
        elif item:
            if paragraph:
                close()
            items.append(_inline(item[1]))
        elif items and line[:1].isspace():      # an item that goes on over lines
            items[-1] += " " + _inline(text)
        else:
            if items:
                close()
            paragraph.append(_inline(text))
    close()
    return "".join(blocks)


# --- the install ---------------------------------------------------------------------------------------


def _clean_environment() -> dict[str, str]:
    """This app's environment without what PyInstaller's bootloader puts in it for the app's own processes
    (`_PYI_*`). With it, the installer's `CS2Clipper.exe quit` and the app it starts again would take themselves
    for parts of this app, and look for its unpacked files, which go when it quits."""
    return {name: value for name, value in os.environ.items() if not name.upper().startswith("_PYI_")}


def _run_installer(command: list[str], environment: dict[str, str]) -> subprocess.Popen:
    """Starts the installer on its own: not in the app's job (winjob), which would end it with the app, and with
    no console."""
    return subprocess.Popen(command, env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)


def _on_a_thread(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="update", daemon=True).start()


class Updates:
    """The newest Release, looked for when due, and one update at a time. `busy()` says why the app cannot be
    updated now, in words for the user, or None; `announce(release)` is told of each newer Release once.
    `folder()` is where installers are downloaded to and `logs()` where the installer writes its log."""

    def __init__(self, *, current: str, enabled: bool, folder: Callable[[], Path], logs: Callable[[], Path],
                 busy: Callable[[], str | None] = lambda: None,
                 announce: Callable[[Release], None] = lambda release: None,
                 latest: Callable[[], Release] = latest_release,
                 fetch: Callable[..., Path] = download.fetch,
                 run_installer: Callable[[list[str], dict[str, str]], subprocess.Popen] = _run_installer,
                 spawn: Callable[[Callable[[], None]], None] = _on_a_thread,
                 clock: Callable[[], float] = time.monotonic):
        self._current, self._enabled = current, enabled
        self._folder, self._logs, self._busy, self._announce = folder, logs, busy, announce
        self._latest, self._fetch, self._run_installer = latest, fetch, run_installer
        self._spawn, self._clock = spawn, clock
        self._lock = threading.Lock()
        self._offered: tuple[Release, str] | None = None     # a newer Release, and its notes as HTML
        self._announced: set[str] = set()
        self._next_at: float | None = None                   # None: at once
        self._running = False
        self._stage: str | None = None                       # "downloading", then "installing"
        self._progress: dict[str, int] | None = None
        self._error: str | None = None

    def refresh_if_due(self) -> None:
        """Asks GitHub when it is due, and never raises. Only the release check's thread calls it."""
        if not self._enabled:
            return
        now = self._clock()
        if self._next_at is not None and now < self._next_at:
            return
        try:
            release = self._latest()
        except Exception as exc:  # noqa: BLE001 - an update check must never stop the app
            log.warning("could not look for a newer CS2 Clipper (%s); trying again in an hour", exc)
            self._next_at = now + RETRY_SECONDS
            return
        self._next_at = now + CHECK_SECONDS
        offered = release if newer(release.version, self._current) else None
        with self._lock:
            self._offered = None if offered is None else (offered, notes_html(offered.notes))
        self._remove_old_installers()
        if offered is not None and offered.version not in self._announced:
            self._announced.add(offered.version)
            log.info("CS2 Clipper %s is out (this is %s)", offered.version, self._current)
            self._announce(offered)

    def status(self) -> dict:
        """What Status shows: the running version, the newer Release on offer (None: none) with its notes as
        HTML, whether an update is under way and at which stage ("downloading", then "installing"), how the
        download is going, and what the last update ended on."""
        with self._lock:
            offered, running, stage, progress, error = (
                self._offered, self._running, self._stage, self._progress, self._error)
        available = None
        if offered is not None:
            release, notes = offered
            available = {"version": release.version, "notes": notes, "page": release.page,
                         "bytes": release.installer.size}
        return {"current": self._current, "available": available, "running": running, "stage": stage,
                "progress": progress, "error": error}

    def start(self) -> str | None:
        """Starts the update to the Release on offer. Returns why not, in words for the user: there is none, or the
        app is busy. A second click while an update is under way starts nothing."""
        with self._lock:
            if self._running:
                return None
            offered = self._offered
        if offered is None:
            return UP_TO_DATE
        refused = self._busy()
        if refused:
            return refused
        with self._lock:
            if self._running:
                return None
            self._running, self._stage, self._progress, self._error = True, "downloading", None, None
        release = offered[0]
        self._spawn(lambda: self._run(release))
        return None

    def _run(self, release: Release) -> None:
        error = None
        try:
            error = self._update(release)
        except DownloadError as exc:
            error = str(exc)
        except Exception as exc:  # noqa: BLE001 - an update that cannot even begin must still end, and say why
            log.exception("update to %s: could not run", release.version)
            error = str(exc) or type(exc).__name__
        finally:
            with self._lock:
                self._running, self._stage, self._progress, self._error = False, None, None, error

    def _update(self, release: Release) -> str | None:
        """Downloads and runs the installer. The installer quits this app on its way, so what comes back is why
        it did not update."""
        asset = release.installer
        installer = self._fetch(asset.url, self._folder() / asset.name, sha256=asset.sha256, size=asset.size,
                                progress=self._on_progress)
        refused = self._busy()          # a Reel may have begun rendering while it downloaded
        if refused:
            return refused
        log_file = self._logs() / f"update-{release.version}.log"
        with self._lock:
            self._stage, self._progress = "installing", None
        log.info("updating to %s with %s", release.version, installer)
        try:
            process = self._run_installer([str(installer), *INSTALLER_FLAGS, f"/LOG={log_file}"],
                                          _clean_environment())
        except OSError as exc:
            return f"The installer could not be started: {exc}"
        code = process.wait()
        said = f"The installer ended without updating (exit code {code}). Its log is {log_file}."
        log.warning("update to %s: %s", release.version, said)
        return said

    def _on_progress(self, done: int, total: int) -> None:
        with self._lock:
            self._progress = {"done": done, "total": total}

    def _remove_old_installers(self) -> None:
        """Installers kept from earlier updates, for this version or an older one, are not needed any more."""
        try:
            for path in self._folder().glob("CS2Clipper-Setup-*.exe"):
                found = _KEPT_INSTALLER.fullmatch(path.name)
                if found and not newer(found[1], self._current):
                    path.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("could not remove an installer kept from an earlier update: %s", exc)
