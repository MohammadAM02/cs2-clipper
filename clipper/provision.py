"""Setup: installs what a fresh PC lacks before the app can clip, so the user never has to.

Five steps, in this order: CS Demo Manager (its own installer, run silently), Postgres (unpacked into
the app data folder), the database (a cluster of its own, on the first port nothing else holds, with a
password made up here), FFmpeg and HLAE (unpacked where CS:DM keeps its own copies). Each step can tell
whether it is needed, so a run does only what is missing: it can be run again after a failure, and a PC
that was set up by hand is left as it is. Nothing is unpacked into, or removed from, a folder the user
chose.

The database password goes to initdb in a file and from there into CS:DM's settings. It is never
logged, and an error that would carry it has it struck out."""
from __future__ import annotations

import copy
import logging
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import psutil
import psycopg
from psycopg import sql

from clipper import checks, components, csdm_settings, download, paths, postgres
from clipper.components import Asset
from clipper.config import Config
from clipper.download import DownloadError
from clipper.lock import AlreadyRunning, single_instance
from clipper.settings import SettingsStore

log = logging.getLogger(__name__)

GB = 1024**3
FREE_BYTES_NEEDED = 2 * GB                  # the downloads and what they unpack to, with room to spare
PORTS = range(5432, 5532)                   # the database listens on the first of these that nothing else holds
CSDM_DIR = Config.csdm_app_dir              # where CS:DM's installer puts it: it offers no other folder
CSDM_EXE = "cs-demo-manager.exe"
INSTALLER_WAIT_SECONDS = 60                 # for the exe to show up once the installer has ended
DATABASE = "csdm"
HLAE_BYTES = 9_000_000                      # about what a release weighs; its real size comes with the release
FFMPEG_FILES = ("bin/ffmpeg.exe", "bin/ffprobe.exe", "LICENSE", "README.txt")
VC_RUNTIME = ("vcruntime140.dll", "msvcp140.dll")     # what the Postgres programs import and do not bring
VC_REDIST_URL = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
STRUCK = "***"
LOOK_SECONDS = 5.0                          # Status asks far more often than this: how long a look is good for


class SetupError(Exception):
    """A step that could not be done, said in words for the user."""


_EXPECTED = (SetupError, DownloadError, csdm_settings.SettingsError)


def _said(exc: Exception) -> str:
    return str(exc) or type(exc).__name__


# --- what setup runs and asks: every one can be swapped for a fake -----------------------------------


def run_program(args: list[str], *, capture: bool = True) -> tuple[int, str]:
    """Runs a program to its end, without a console window: its exit code and, when `capture`, what it
    wrote. An installer is run uncaptured: whatever it leaves running would hold a captured pipe open."""
    out = subprocess.PIPE if capture else subprocess.DEVNULL
    err = subprocess.STDOUT if capture else subprocess.DEVNULL
    result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=out, stderr=err, text=True, errors="replace",
                            creationflags=subprocess.CREATE_NO_WINDOW)
    return result.returncode, result.stdout or ""


def port_free(port: int) -> bool:
    """Whether Postgres could listen on 127.0.0.1:`port`, found out the way Postgres would: by binding
    it. That fails for a port another program holds, and for one Windows keeps back."""
    with socket.socket() as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def missing_runtime(pg_bin: Path) -> list[str]:
    """The Visual C++ runtime DLLs Postgres needs that are neither beside it nor in System32."""
    system32 = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32"
    return [name for name in VC_RUNTIME if not (pg_bin / name).exists() and not (system32 / name).exists()]


def create_database(port: int, password: str) -> None:
    """Makes the database CS:DM uses on the Postgres listening on `port`, unless it is there. CS:DM
    makes the tables itself, the first time it runs."""
    with psycopg.connect(host="127.0.0.1", port=port, user="postgres", password=password, dbname="postgres",
                         autocommit=True, connect_timeout=10) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DATABASE,)).fetchone() is None:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(DATABASE)))


def _free_bytes(path: Path) -> int:
    return psutil.disk_usage(str(path)).free


@dataclass(frozen=True)
class Tools:
    fetch: Callable[..., Path] = download.fetch
    run: Callable[..., tuple[int, str]] = run_program
    which: Callable[[str], str | None] = shutil.which
    latest_hlae: Callable[[], tuple[str, Asset]] = components.latest_hlae
    port_free: Callable[[int], bool] = port_free
    create_database: Callable[[int, str], None] = create_database
    ensure_postgres: Callable[[Path, Path, int], None] = postgres.ensure_running
    stop_postgres: Callable[[Path, Path], None] = postgres.stop
    new_password: Callable[[], str] = lambda: secrets.token_urlsafe(24)
    free_bytes: Callable[[Path], int] = _free_bytes
    missing_runtime: Callable[[Path], list[str]] = missing_runtime
    sleep: Callable[[float], None] = time.sleep


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


# --- CS Demo Manager ---------------------------------------------------------------------------------


def _csdm_needed(ctx: Context) -> bool:
    return not ctx.cfg().csdm_exe.exists()


def _csdm_download(ctx: Context) -> int:
    return 0 if (CSDM_DIR / CSDM_EXE).exists() else components.CSDM.size


def _install_csdm(ctx: Context) -> None:
    exe = CSDM_DIR / CSDM_EXE
    if not exe.exists():
        installer = ctx.fetch(components.CSDM)
        code, _ = ctx.tools.run([str(installer), "/S"], capture=False)     # /S: no window, and CS:DM is not started
        if code != 0:
            raise SetupError(f"its installer ended with code {code}")
        for _ in range(INSTALLER_WAIT_SECONDS):
            if exe.exists():
                break
            ctx.tools.sleep(1.0)
        else:
            raise SetupError(f"its installer finished, but {exe} is not there")
        installer.unlink(missing_ok=True)
    if ctx.cfg().csdm_app_dir != CSDM_DIR:
        ctx.save({"csdm_app_dir": str(CSDM_DIR)})


# --- Postgres and the database -----------------------------------------------------------------------


def _own_postgres() -> Path:
    """Where setup keeps the Postgres it installs: the programs in `pgsql`, the cluster in `data`."""
    return paths.data_dir() / "postgres"


def _postgres_needed(ctx: Context) -> bool:
    return not (ctx.cfg().pg_bin / "pg_ctl.exe").exists()


def _postgres_download(ctx: Context) -> int:
    return 0 if (_own_postgres() / "pgsql" / "bin" / "pg_ctl.exe").exists() else components.POSTGRES.size


def _install_postgres(ctx: Context) -> None:
    programs = _own_postgres() / "pgsql"
    if not (programs / "bin" / "pg_ctl.exe").exists():
        archive = ctx.fetch(components.POSTGRES)
        download.extract(archive, programs, strip_top=True,
                         want=lambda name: not name.startswith(("include/", "StackBuilder/")))
        archive.unlink(missing_ok=True)
    ctx.save({"pg_bin": str(programs / "bin")})


def _database_needed(ctx: Context) -> bool:
    cfg = ctx.cfg()
    return not (cfg.pg_data / "PG_VERSION").exists() or csdm_settings.database(cfg.csdm_home) is None


def _install_database(ctx: Context) -> None:
    cfg = ctx.cfg()
    own = _own_postgres() / "data"
    data = next((folder for folder in (cfg.pg_data, own) if (folder / "PG_VERSION").exists()), None)
    if data is None:
        data, port = own, _make_cluster(ctx, cfg, own)
    else:
        connection = csdm_settings.database(cfg.csdm_home)
        if connection is None:      # no password for it: it is not setup's to remake, and it cannot be used
            raise SetupError(f"{data} already holds a database, but CS Demo Manager's settings do not hold its "
                             "password. Move that folder away, or name the right Postgres data folder in "
                             "Settings, and run setup again")
        port = connection["port"]
    if cfg.pg_data != data:
        ctx.save({"pg_data": str(data)})
    ctx.tools.ensure_postgres(cfg.pg_bin, data, port)


def _make_cluster(ctx: Context, cfg: Config, data: Path) -> int:
    """Makes a cluster at `data` with CS:DM's database in it, writes the connection into CS:DM's
    settings, and returns the port chosen. The cluster is made as ``data.part`` and moved into place
    last, so a folder named `data` is always a whole cluster whose password CS:DM's settings hold."""
    tools, home = ctx.tools, cfg.csdm_home
    missing = tools.missing_runtime(cfg.pg_bin)
    if missing:
        raise SetupError(f"Postgres needs the Microsoft Visual C++ runtime ({' and '.join(missing)} missing). "
                         f"Install it from {VC_REDIST_URL} and run setup again")
    if data.exists():
        raise SetupError(f"{data} is in the way: it is not a database. Move it away and run setup again")
    before = csdm_settings.read(home)           # an unreadable file stops here, before anything is made
    part = data.with_name(data.name + ".part")
    if (part / "PG_VERSION").exists():
        tools.stop_postgres(cfg.pg_bin, part)   # an interrupted run may have left its Postgres up
    if part.exists():
        shutil.rmtree(part)
    port = next((port for port in PORTS if tools.port_free(port)), None)
    if port is None:
        raise SetupError(f"no port from {PORTS[0]} to {PORTS[-1]} is free")
    password = tools.new_password()
    try:
        _initdb(tools, cfg.pg_bin, part, password)
        try:
            tools.ensure_postgres(cfg.pg_bin, part, port)
            tools.create_database(port, password)
        finally:
            tools.stop_postgres(cfg.pg_bin, part)
        if before is not None and "database" in before:     # a connection to a database that is gone: kept, in case
            file = csdm_settings.settings_file(home)
            shutil.copyfile(file, file.with_name("settings.before-setup.json"))
        csdm_settings.update(home, {"database": {"hostname": "127.0.0.1", "port": port, "username": "postgres",
                                                 "password": password, "database": DATABASE}})
        paths.move_into_place(part, data)
    except Exception as exc:  # noqa: BLE001 - run() tells the user; here the password is struck out first
        if not isinstance(exc, _EXPECTED):
            log.warning("setup: making the database failed\n%s", traceback.format_exc().replace(password, STRUCK))
        raise SetupError(_said(exc).replace(password, STRUCK)) from None
    return port


def _initdb(tools: Tools, pg_bin: Path, data: Path, password: str) -> None:
    """A new cluster at `data` whose superuser `postgres` has `password`. initdb takes the password
    from a file, not from its command line, which any program on the PC can read."""
    data.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix="pw-", dir=data.parent)
    pwfile = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(password)
        code, said = tools.run([str(pg_bin / "initdb.exe"), "-D", str(data), "-U", "postgres", "--pwfile", str(pwfile),
                                "-E", "UTF8", "-A", "scram-sha-256", "--no-locale"])
    finally:
        pwfile.unlink(missing_ok=True)
    if code != 0:
        log.warning("setup: initdb ended with code %s and said:\n%s", code, said)
        lines = [line.strip() for line in said.splitlines() if line.strip()]
        telling = [line for line in lines if "error" in line.lower() or "fatal" in line.lower()]
        raise SetupError(f"initdb failed: {(telling or lines or [f'exit code {code}'])[0]}")


# --- FFmpeg and HLAE: in the folders where CS:DM keeps its own copies ----------------------------------


def _found(program: str, which: Callable[[str], str | None]) -> str | None:
    """Where `program` is, given the way Settings takes it: a name on PATH, or a path to a file."""
    return which(program) or (program if Path(program).is_file() else None)


def _own_ffmpeg(cfg: Config) -> dict[str, Path]:
    return {name: cfg.csdm_home / ".csdm" / "ffmpeg" / "bin" / f"{name}.exe" for name in ("ffmpeg", "ffprobe")}


def _app_has_ffmpeg(ctx: Context) -> bool:
    cfg = ctx.cfg()
    return all(_found(program, ctx.tools.which) for program in (cfg.ffmpeg, cfg.ffprobe))


def _ffmpeg_needed(ctx: Context) -> bool:
    return not _app_has_ffmpeg(ctx) or not csdm_settings.ffmpeg_exe(ctx.cfg().csdm_home).is_file()


def _ffmpeg_download(ctx: Context) -> int:
    there = _app_has_ffmpeg(ctx) or all(exe.is_file() for exe in _own_ffmpeg(ctx.cfg()).values())
    return 0 if there else components.FFMPEG.size


def _install_ffmpeg(ctx: Context) -> None:
    cfg = ctx.cfg()
    own = _own_ffmpeg(cfg)
    if not _app_has_ffmpeg(ctx):
        if not all(exe.is_file() for exe in own.values()):
            archive = ctx.fetch(components.FFMPEG)
            download.extract(archive, cfg.csdm_home / ".csdm" / "ffmpeg", strip_top=True,
                             want=lambda name: name in FFMPEG_FILES)
            archive.unlink(missing_ok=True)
        ctx.save({name: str(exe) for name, exe in own.items()})
    if not csdm_settings.ffmpeg_exe(cfg.csdm_home).is_file():
        if own["ffmpeg"].is_file():
            location = {"customLocationEnabled": False}
        else:       # the PC has an FFmpeg of its own, which the app uses: CS:DM is pointed at the same one
            location = {"customLocationEnabled": True,
                        "customExecutableLocation": os.path.abspath(_found(cfg.ffmpeg, ctx.tools.which))}
        csdm_settings.update(cfg.csdm_home, {"video": {"ffmpegSettings": location}})


def _hlae_installed(ctx: Context) -> bool:
    exe = checks.hlae_exe(ctx.cfg().csdm_home)
    return exe is not None and exe.is_file()


def _hlae_needed(ctx: Context) -> bool:
    """Missing, or behind: an HLAE older than the CS2 build cannot record."""
    return not _hlae_installed(ctx) or checks.hlae_behind(ctx.cfg().csdm_home, ctx.latest_hlae)


def _install_hlae(ctx: Context) -> None:
    home = ctx.cfg().csdm_home
    _, asset = ctx.tools.latest_hlae()
    archive = ctx.fetch(asset)
    download.extract(archive, home / ".csdm" / "hlae")
    archive.unlink(missing_ok=True)
    csdm_settings.update(home, {"video": {"hlae": {"customLocationEnabled": False}}})


# HLAE counts as installed once it is there: were its changelog ever behind its release tag, asking
# "no longer behind" would fail a step that did all it could.
STEPS = (
    Step("csdm", "CS Demo Manager", _csdm_needed, _install_csdm, _csdm_download),
    Step("postgres", "Postgres", _postgres_needed, _install_postgres, _postgres_download),
    Step("database", "Database", _database_needed, _install_database),
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
