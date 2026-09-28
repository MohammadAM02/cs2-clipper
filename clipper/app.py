"""One running copy of the app: start-up order, the worker thread, the web server, and quitting
(spec: How the app runs; The terminal; When something goes wrong).

`single_instance`/`AlreadyRunning`, `start_match_alerts` and `build_worker` are our own code, moved
here from `cli.py` (Tasks 1-8) rather than adapted from Aegis. The overall shape of `run_headless` --
single instance, then move in, then settings, then the web server, then the worker, then a short-wait
loop so Ctrl+C is noticed promptly -- is adapted from thelifeofsuleyman/cs2-clipper's `aegis/app.py`
(`main`, `_serve`); ours adds the quit-during-a-render choice the spec asks for, which Aegis has none
of (it exits at once).

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

import logging
import msvcrt
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from functools import partial
from pathlib import Path

from clipper import applog, checks, csdm_db, move_in, paths, protect, web
from clipper.alerts import MatchAlerts
from clipper.config import Config
from clipper.csdm_cli import CsdmCli
from clipper.faceit import FaceitClient
from clipper.gate import Gate
from clipper.index import Index
from clipper.intake import Intake
from clipper.join import join_reel
from clipper.media import probe_duration
from clipper.notify import notify
from clipper.procs import ProcessProbe, SystemProbe
from clipper.render import render
from clipper.settings import SettingsStore
from clipper.state import AppState
from clipper.unpack import unpack
from clipper.worker import Services, StopRequest, Worker

log = logging.getLogger(__name__)


class AlreadyRunning(Exception):
    """Another clipper holds the lock."""


@contextmanager
def single_instance(lock_path: Path) -> Iterator[None]:
    """Hold an exclusive lock on lock_path for the life of the block."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        handle.close()
        raise AlreadyRunning(f"another clipper is already running (lock: {lock_path})") from exc
    try:
        yield
    finally:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def start_match_alerts(cfg: Config, index: Index, probe: SystemProbe, *,
                       page_url: str | None, pages_off: str | None) -> MatchAlerts | None:
    """Start the match alerts step. When alerts are off, say why in the index (clipper status shows
    it) and return None. The app owns the one web server now (Task 8); a missing `page_url` means it
    could not start, and `pages_off` (from `App.start_web`) carries the reason."""
    if not cfg.match_alerts:
        index.set_flag("alerts_status", "off: switched off in Settings")
        return None
    if not (cfg.faceit_nickname and cfg.faceit_api_key_protected):
        index.set_flag("alerts_status", "off: set your FACEIT nickname and API key in Settings")
        return None
    if page_url is None:
        index.set_flag("alerts_status", f"off: the Demos to grab page could not start: {pages_off}")
        return None
    faceit = FaceitClient(lambda: protect.unprotect(cfg.faceit_api_key_protected))
    return MatchAlerts(index, faceit, notify, probe.user_cs2_running, nickname=cfg.faceit_nickname,
                       subject_steamid=cfg.subject_steamid, page_url=page_url,
                       stopped_playing_minutes=cfg.stopped_playing_minutes)


def build_worker(cfg: Config, index: Index, *, state: AppState, stop: StopRequest,
                 page_url: str | None, pages_off: str | None) -> Worker:
    probe = SystemProbe()
    gate = Gate(probe, cfg.data_root, cfg.min_free_gb)
    csdm = CsdmCli(prefix=(str(cfg.csdm_exe), str(cfg.csdm_cli_js)), home=cfg.csdm_home, pg_bin=cfg.pg_bin)
    duration_of = partial(probe_duration, ffprobe=cfg.ffprobe)

    def render_job(request, should_abort):
        return render(request, csdm=csdm, probe=probe, should_abort=should_abort,
                      stall_seconds=cfg.stall_seconds, launch_timeout_seconds=cfg.launch_timeout_seconds,
                      duration_of=duration_of)

    services = Services(
        intake=Intake(cfg.downloads_dir, cfg.demos_dir),
        unpack=unpack,
        analyze=csdm.analyze,
        facts=csdm_db.CsdmFacts(cfg.database_conninfo()),
        gate=gate,
        render=render_job,
        join=partial(join_reel, ffmpeg=cfg.ffmpeg, duration_of=duration_of, stretch=cfg.stretch),
        notify=notify,
        alerts=start_match_alerts(cfg, index, probe, page_url=page_url, pages_off=pages_off),
    )
    return Worker(cfg, index, services, state=state, stop=stop)


def gate_reasons(cfg: Config, probe: ProcessProbe) -> tuple[str, ...]:
    """For the Demos to grab page: the Gate's reasons now, or a clips-folder reason when none is set
    yet (the Gate itself needs a `data_root` to check free space against)."""
    if cfg.data_root is None:
        return ("no clips folder is set",)
    return Gate(probe, cfg.data_root, cfg.min_free_gb).check().reasons


class App:
    """One running copy: its settings, what it is doing (`state`), the web server and the worker
    thread. `problems` and `build` are swappable so tests never touch real csdm/CS2/Postgres."""

    def __init__(self, settings: SettingsStore, *, index_path: Path, state: AppState | None = None,
                 stop: StopRequest | None = None,
                 problems: Callable[[Config], list[str]] = checks.startup_problems,
                 build: Callable[..., Worker] = build_worker,
                 recheck_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.settings = settings
        self.state = state if state is not None else AppState()
        self.stop = stop if stop is not None else StopRequest()
        self.page_port: int | None = None
        self._index_path = index_path
        self._problems = problems
        self._build = build
        self._recheck_seconds = recheck_seconds
        self._clock = clock
        self._probe = SystemProbe()
        self._web: web.WebServer | None = None
        self._worker_thread: threading.Thread | None = None
        self._worker: Worker | None = None
        self._worker_cfg: Config | None = None
        self._problems_cfg: Config | None = None
        self._problems_checked_at: float | None = None

    # --- the web server --------------------------------------------------------------------------

    def start_web(self) -> None:
        """Binds now (Task 8's `WebServer`); on OSError (no free port) the app runs without pages:
        `page_port` stays None and `state.pages_off` says why (spec: When something goes wrong)."""
        ctx = web.WebContext(
            index_path=self._index_path,
            gate_reasons=lambda: gate_reasons(self.settings.current().config, self._probe),
        )
        try:
            self._web = web.WebServer(web.create_app(ctx), self.settings.current().config.page_port,
                                      tries=web.PORTS_TO_TRY)
        except OSError as exc:
            self.page_port = None
            self.state.set_pages_off(str(exc))
            log.warning("the Demos to grab page could not start: %s", exc)
            return
        self.page_port = self._web.port
        self.state.set_pages_off(None)
        self._web.start()
        index = Index(self._index_path)
        try:
            index.set_flag("page_port", str(self.page_port))
        finally:
            index.close()
        log.info("the Demos to grab page is on port %s", self.page_port)

    def close(self) -> None:
        if self._web is not None:
            self._web.stop()

    # --- the worker thread -----------------------------------------------------------------------

    def start_worker(self) -> None:
        self._worker_thread = threading.Thread(target=self.run_worker, name="worker", daemon=True)
        self._worker_thread.start()

    def run_worker(self) -> None:
        index = Index(self._index_path)
        try:
            while not self.stop.stopping():
                delay = self.step(index)
                self.stop.wait(delay)
        finally:
            index.close()

    def wait(self, timeout: float | None = None) -> bool:
        """True once the worker thread has ended (or never ran)."""
        if self._worker_thread is None:
            return True
        self._worker_thread.join(timeout)
        return not self._worker_thread.is_alive()

    def step(self, index: Index) -> float:
        """One pass: start-up problems first (cheap, checked every pass; the expensive parts -- e.g.
        Postgres -- only every `recheck_seconds`), then a rebuild when settings changed, then a tick.
        Never raises: a tick that does is logged and the next pass still runs."""
        cfg = self.settings.current().config
        now = self._clock()
        needs_check = (
            self._problems_checked_at is None
            or cfg != self._problems_cfg
            or now - self._problems_checked_at >= self._recheck_seconds
        )
        if needs_check:
            problems = self._problems(cfg)
            self.state.set_problems(problems)
            self._problems_cfg = cfg
            self._problems_checked_at = now
        else:
            problems = self.state.snapshot().problems
        if problems:
            return cfg.poll_seconds

        if self._worker is None or cfg != self._worker_cfg:
            for folder in (cfg.demos_dir, cfg.renders_dir, cfg.library_dir):
                folder.mkdir(parents=True, exist_ok=True)
            if self._worker is not None:
                log.info("settings changed; rebuilding the worker")
            page_url = f"http://127.0.0.1:{self.page_port}/demos" if self.page_port else None
            self._worker = self._build(cfg, index, state=self.state, stop=self.stop,
                                       page_url=page_url, pages_off=self.state.snapshot().pages_off)
            self._worker_cfg = cfg

        try:
            self._worker.tick()
        except Exception:  # noqa: BLE001 - the loop must survive anything a tick throws
            log.exception("tick failed")
        return cfg.poll_seconds

    # --- quitting ----------------------------------------------------------------------------------

    def quit(self, mode: str | None = None) -> str:
        """`mode` None asks: "ask" while a Render Job is running (the caller offers the choice),
        otherwise it behaves as "now". "now"/"after_render" request the stop and publish it."""
        if mode is None:
            if self.state.snapshot().rendering is not None:
                return "ask"
            mode = "now"
        self.stop.request(mode)
        self.state.set_quitting(self.stop.mode)
        log.info("quit requested: %s", self.stop.mode)
        return self.stop.mode


def run_headless() -> int:
    """`clipper run` / `clipper run --headless`: the worker and the web server, no tray or window
    (spec: The terminal; How the app runs, Starting)."""
    try:
        with single_instance(paths.lock_file()):
            applog.setup(paths.logs_dir())
            move_in.on_start()
            settings = SettingsStore(paths.settings_file())
            for warning in settings.current().warnings:
                log.warning(warning)

            app = App(settings, index_path=paths.index_file())
            app.start_web()
            app.start_worker()

            quitting = False
            while True:
                try:
                    if app.wait(timeout=0.5):
                        break
                except KeyboardInterrupt:
                    if quitting:
                        break   # a second Ctrl+C ends it at once
                    app.quit("now")
                    print("stopping any render first, then quitting…", file=sys.stderr)
                    quitting = True
            app.close()
            return 0
    except AlreadyRunning as exc:
        print(exc, file=sys.stderr)
        return 0
