"""Setup: installs what a fresh PC lacks before the app can clip, so the user never has to.

Three steps, in this order: csda (which reads each Demo), FFmpeg and HLAE, each unpacked into the
``tools`` folder of the app data folder. Each step can tell whether it is needed, so a run does only
what is missing: it can be run again after a failure, and a PC that was set up by hand is left as it
is. Nothing is unpacked into, or removed from, a folder the user chose."""
from __future__ import annotations

import copy
import logging
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import psutil

from clipper import checks, components, download, paths
from clipper.components import Asset
from clipper.config import Config
from clipper.download import DownloadError
from clipper.lock import AlreadyRunning, single_instance
from clipper.settings import SettingsStore

log = logging.getLogger(__name__)

GB = 1024**3
FREE_BYTES_NEEDED = 2 * GB                  # the downloads and what they unpack to, with room to spare
HLAE_BYTES = 9_000_000                      # about what a release weighs; its real size comes with the release
FFMPEG_FILES = ("bin/ffmpeg.exe", "bin/ffprobe.exe", "LICENSE", "README.txt")
LOOK_SECONDS = 5.0                          # Status asks far more often than this: how long a look is good for


class SetupError(Exception):
    """A step that could not be done, said in words for the user."""


_EXPECTED = (SetupError, DownloadError)


def _said(exc: Exception) -> str:
    return str(exc) or type(exc).__name__


# --- what setup asks and fetches: every one can be swapped for a fake --------------------------------


def _free_bytes(path: Path) -> int:
    return psutil.disk_usage(str(path)).free


@dataclass(frozen=True)
class Tools:
    fetch: Callable[..., Path] = download.fetch
    which: Callable[[str], str | None] = shutil.which
    latest_hlae: Callable[[], tuple[str, Asset]] = components.latest_hlae
    free_bytes: Callable[[Path], int] = _free_bytes


@dataclass
class Context:
    """What a step works with: the app's settings, the tools, and the newest HLAE release when it is
    known. `progress(done, total)` is told how a download is going."""
    store: SettingsStore
    tools: Tools = field(default_factory=Tools)
    latest_hlae: str | None = None
    progress: Callable[[int, int], None] = lambda done, total: None

    def cfg(self) -> Config:
        return self.store.current().config

    def save(self, changes: dict[str, str]) -> None:
        errors = self.store.save(changes)
        if errors:
            raise SetupError("the settings could not be saved: "
                             + "; ".join(f"{name} {error}" for name, error in errors.items()))

    def fetch(self, asset: Asset) -> Path:
        """The downloaded file. One a failed run left behind is used again, if it is the right file."""
        return self.tools.fetch(asset.url, paths.data_dir() / "downloads" / asset.name, sha256=asset.sha256,
                                size=asset.size, progress=self.progress)


@dataclass(frozen=True)
class Step:
    id: str
    name: str
    needed: Callable[[Context], bool]
    install: Callable[[Context], None]
    download: Callable[[Context], int] = lambda ctx: 0          # the bytes it would fetch
    installed: Callable[[Context], bool] | None = None          # when that asks less than "no longer needed"


# --- csda: always the one setup installs, since nothing else on a PC would have it ---------------------


def _csda_needed(ctx: Context) -> bool:
    return not ctx.cfg().csda_exe.is_file()


def _install_csda(ctx: Context) -> None:
    archive = ctx.fetch(components.CSDA)
    download.extract(archive, ctx.cfg().csda_exe.parent)
    archive.unlink(missing_ok=True)


# --- FFmpeg: the one the PC has, else one setup installs --------------------------------------------------


def _found(program: str, which: Callable[[str], str | None]) -> str | None:
    """Where `program` is, given the way Settings takes it: a name on PATH, or a path to a file."""
    return which(program) or (program if Path(program).is_file() else None)


def _own_ffmpeg(cfg: Config) -> dict[str, Path]:
    return {name: cfg.tools_dir / "ffmpeg" / "bin" / f"{name}.exe" for name in ("ffmpeg", "ffprobe")}


def _ffmpeg_needed(ctx: Context) -> bool:
    cfg = ctx.cfg()
    return not all(_found(program, ctx.tools.which) for program in (cfg.ffmpeg, cfg.ffprobe))


def _ffmpeg_download(ctx: Context) -> int:
    return 0 if all(exe.is_file() for exe in _own_ffmpeg(ctx.cfg()).values()) else components.FFMPEG.size


def _install_ffmpeg(ctx: Context) -> None:
    cfg = ctx.cfg()
    own = _own_ffmpeg(cfg)
    if not all(exe.is_file() for exe in own.values()):
        archive = ctx.fetch(components.FFMPEG)
        download.extract(archive, cfg.tools_dir / "ffmpeg", strip_top=True, want=lambda name: name in FFMPEG_FILES)
        archive.unlink(missing_ok=True)
    ctx.save({name: str(exe) for name, exe in own.items()})


# --- HLAE: the newest release, since an older one than the CS2 build cannot record -----------------------


def _hlae_installed(ctx: Context) -> bool:
    return checks.hlae_exe(ctx.cfg()) is not None


def _hlae_needed(ctx: Context) -> bool:
    """Missing, or behind: an HLAE older than the CS2 build cannot record."""
    return not _hlae_installed(ctx) or checks.hlae_behind(ctx.cfg(), ctx.latest_hlae)


def _install_hlae(ctx: Context) -> None:
    """Into the app's own folder, even when the ``hlae_exe`` setting names another: that HLAE is the
    user's and is left as it is, and the setting is cleared so the app uses the one installed here."""
    cfg = ctx.cfg()
    _, asset = ctx.tools.latest_hlae()
    archive = ctx.fetch(asset)
    download.extract(archive, cfg.tools_dir / "hlae")
    archive.unlink(missing_ok=True)
    if cfg.hlae_exe:
        ctx.save({"hlae_exe": ""})


# HLAE counts as installed once it is there: were its changelog ever behind its release tag, asking
# "no longer behind" would fail a step that did all it could.
STEPS = (
    Step("csda", "csda", _csda_needed, _install_csda, lambda ctx: components.CSDA.size),
    Step("ffmpeg", "FFmpeg", _ffmpeg_needed, _install_ffmpeg, _ffmpeg_download),
    Step("hlae", "HLAE", _hlae_needed, _install_hlae, lambda ctx: HLAE_BYTES, _hlae_installed),
)


# --- a run -------------------------------------------------------------------------------------------


def needed(ctx: Context) -> list[str]:
    """The ids of the steps this PC still needs, in order."""
    return [step.id for step in STEPS if step.needed(ctx)]


def run(ctx: Context, report: Callable[[str, str], None] = lambda step, state: None) -> str | None:
    """Installs what is needed, in order, and stops at the first step that fails. Returns what went
    wrong, in words for the user, or None when the PC is set up. `report(step id, state)` is told as a
    step becomes "running", then "ok" or "failed"; a step that is not needed is "ok" straight away.
    One run at a time, across processes: ``setup.lock`` is held for as long as it takes."""
    try:
        with single_instance(paths.data_dir() / "setup.lock"):
            return _run(ctx, report)
    except AlreadyRunning:
        return "Setup is already running"


def _run(ctx: Context, report: Callable[[str, str], None]) -> str | None:
    if not needed(ctx):
        return None
    free = ctx.tools.free_bytes(paths.data_dir())
    if free < FREE_BYTES_NEEDED:
        return (f"Setup needs {FREE_BYTES_NEEDED // GB} GB free on the drive that holds {paths.data_dir()}, "
                f"and only {free / GB:.1f} GB is free")
    for step in STEPS:
        if not step.needed(ctx):
            report(step.id, "ok")
            continue
        report(step.id, "running")
        log.info("setup: installing %s", step.name)
        try:
            step.install(ctx)
            if not (step.installed(ctx) if step.installed else not step.needed(ctx)):
                raise SetupError("it was installed, but it is still not where the app looks for it")
        except Exception as exc:  # noqa: BLE001 - whatever went wrong is told to the user, who can run setup again
            log.warning("setup: %s failed: %s", step.name, _said(exc), exc_info=not isinstance(exc, _EXPECTED))
            report(step.id, "failed")
            return f"{step.name}: {_said(exc)}"
        log.info("setup: %s is in place", step.name)
        report(step.id, "ok")
    return None


# --- what Status shows, and its "Set up" button --------------------------------------------------------


def _look(ctx: Context) -> dict:
    """`update` is true of a needed step that replaces what is there already: a newer HLAE."""
    todo = needed(ctx)
    return {"steps": [{"id": step.id, "name": step.name, "state": "needed" if step.id in todo else "ok",
                       "update": step.id in todo and step.installed is not None and step.installed(ctx)}
                      for step in STEPS],
            "download_bytes": sum(step.download(ctx) for step in STEPS if step.id in todo)}


def _on_a_thread(job: Callable[[], None]) -> None:
    threading.Thread(target=job, name="setup", daemon=True).start()


class Setup:
    """One run at a time, on a thread of its own, and what Status shows of it. `context()` makes the
    Context for a run or a look; `after()` is called when a run has ended, however it ended."""

    def __init__(self, context: Callable[[], Context], *, after: Callable[[], None] = lambda: None,
                 spawn: Callable[[Callable[[], None]], None] = _on_a_thread,
                 clock: Callable[[], float] = time.monotonic):
        self._context, self._after, self._spawn, self._clock = context, after, spawn, clock
        self._lock = threading.Lock()
        self._running = False
        self._seen: dict | None = None          # during a run: what it found as it began, and each step's state since
        self._looked: tuple[float, dict] | None = None      # between runs: when the PC was looked at, and what it had
        self._progress: dict[str, int] | None = None
        self._error: str | None = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    def start(self) -> bool:
        """Starts a run. False when one is under way already."""
        with self._lock:
            if self._running:
                return False
            self._running, self._error = True, None
        self._spawn(self._run)
        return True

    def status(self) -> dict:
        """Each step's state ("ok", "needed", "running" or "failed") and whether it is an update, what a
        run would download, how the download under way is going, and what the last run ended on. While
        a run is under way the states are the ones it reports, so nothing is read from folders it is
        busy filling. A PC that cannot be looked at is answered too: no steps, and why in `error`."""
        with self._lock:
            running, progress, error = self._running, self._progress, self._error
            seen = copy.deepcopy(self._seen)
        if seen is None:
            try:
                seen = self._between_runs()
            except Exception as exc:  # noqa: BLE001 - Status asks every two seconds: it is told why, and nothing is logged
                return {"running": running, "needed": True, "steps": [], "download_bytes": 0, "progress": progress,
                        "error": _said(exc)}
        return {"running": running, "needed": any(step["state"] != "ok" for step in seen["steps"]), **seen,
                "progress": progress, "error": error}

    def _between_runs(self) -> dict:
        """What the PC has, looked at no more than once in LOOK_SECONDS."""
        now = self._clock()
        with self._lock:
            looked = self._looked
        if looked is None or now - looked[0] >= LOOK_SECONDS:
            looked = (now, _look(self._context()))
            with self._lock:
                self._looked = looked
        return copy.deepcopy(looked[1])

    def _run(self) -> None:
        error = None
        try:
            ctx = self._context()
            ctx.progress = self._on_progress
            seen = _look(ctx)
            with self._lock:
                self._seen = seen
            error = run(ctx, self._on_step)
        except Exception as exc:  # noqa: BLE001 - a run that cannot even begin must still end, and say why
            log.exception("setup: could not run")
            error = _said(exc)
        finally:
            with self._lock:
                self._running, self._seen, self._progress, self._error = False, None, None, error
                self._looked = None             # whatever was found before the run, the run has changed it
            self._after()

    def _on_step(self, step_id: str, state: str) -> None:
        with self._lock:
            for step in (self._seen or {}).get("steps", ()):
                if step["id"] == step_id:
                    step["state"] = state
            self._progress = None

    def _on_progress(self, done: int, total: int) -> None:
        with self._lock:
            self._progress = {"done": done, "total": total}
