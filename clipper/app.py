"""One running copy of the app: start-up order, the worker thread, the web server, and quitting
(spec: How the app runs; The terminal; When something goes wrong).

`start_match_alerts` and `build_worker` are our own code, moved here from `cli.py` (Tasks 1-8) rather
than adapted from Aegis; so are `single_instance`/`AlreadyRunning`, which now live in `lock.py`. The
overall shape of `run` -- single instance, then move in, then settings, then the web server, then the
worker, then the window and the tray, or else a short-wait loop so Ctrl+C is noticed promptly -- is
adapted from thelifeofsuleyman/cs2-clipper's `aegis/app.py` (`main`, `_serve`); ours adds the
quit-during-a-render choice the spec asks for, which Aegis has none of (it exits at once), and
`--background`, which shows only the tray.

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

import json
import logging
import os
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable
from functools import partial
from pathlib import Path

from clipper import applog, checks, csdm_db, faceit_oauth, move_in, paths, protect, settings, tray, web
from clipper.alerts import MatchAlerts
from clipper.config import Config
from clipper.csdm_cli import CsdmCli
from clipper.faceit import FaceitClient, FaceitError
from clipper.faceit_oauth import OAuthError
from clipper.gate import Gate
from clipper.index import Index
from clipper.intake import Intake
from clipper.join import join_reel
from clipper.lock import AlreadyRunning, single_instance
from clipper.media import probe_duration
from clipper.notify import notify
from clipper.procs import ProcessProbe, SystemProbe
from clipper.render import render
from clipper.settings import SettingsStore
from clipper.state import AppState
from clipper.unpack import unpack
from clipper.web import PAGES  # noqa: F401 - its one home is web.py; cli.py and the tests read it as app.PAGES
from clipper.window import WindowLauncher
from clipper.worker import Services, StopRequest, Worker

log = logging.getLogger(__name__)

CHECKS_CACHE_SECONDS = 15.0            # the Status page polls every 2 s; some checks are not free
RELEASES_INTERVAL_SECONDS = 10 * 60.0  # HlaeReleases.refresh_if_due only asks GitHub when it is due
WORKER_ERROR_WAIT_SECONDS = 30.0       # how long the worker thread waits after a pass that raised


def find_running(ports: Iterable[int], *, timeout: float = 1.0) -> int | None:
    """The first port among `ports` whose `/health` answers ``{"ok": true, "app": "cs2-clipper"}``
    (spec: How the app runs, Starting -- Aegis's `/health` check). Anything else -- nothing
    listening, another app, a bad answer -- is skipped."""
    for port in ports:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=timeout) as response:
                body = json.loads(response.read())
        except (OSError, ValueError):
            continue
        if isinstance(body, dict) and body.get("ok") is True and body.get("app") == "cs2-clipper":
            return port
    return None


def hand_over(page: str | None, ports: Iterable[int]) -> bool:
    """Finds the running copy among `ports` and asks it to show `page` (or Status). True once it
    answers 204; False when none of `ports` is a running copy, or it refuses."""
    port = find_running(ports)
    if port is None:
        return False
    body = json.dumps({"page": page or "/status"}).encode("utf-8")
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/window", data=body, method="POST",
        headers={"Content-Type": "application/json", web.MARKER_HEADER: "1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=1.0) as response:
            return response.status == 204
    except OSError:   # urllib.error.HTTPError (a 4xx/5xx "refusal") is one too
        return False


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
    """One running copy: its settings, what it is doing (`state`), the web server, the window and the
    worker thread. `problems`, `build` and `launcher` (given the web server's base URL) are swappable so
    tests never touch real csdm/CS2/Postgres or open a window."""

    def __init__(self, settings: SettingsStore, *, index_path: Path, state: AppState | None = None,
                 stop: StopRequest | None = None,
                 problems: Callable[[Config], list[str]] = checks.startup_problems,
                 build: Callable[..., Worker] = build_worker,
                 launcher: Callable[[str], WindowLauncher] = WindowLauncher,
                 recheck_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.settings = settings
        self.state = state if state is not None else AppState()
        self.stop = stop if stop is not None else StopRequest()
        self.releases = checks.HlaeReleases()
        self.page_port: int | None = None
        self._index_path = index_path
        self._problems = problems
        self._build = build
        self._launcher_factory = launcher
        self._launcher: WindowLauncher | None = None
        self._recheck_seconds = recheck_seconds
        self._clock = clock
        self._probe = SystemProbe()
        self._web: web.WebServer | None = None
        self._worker_thread: threading.Thread | None = None
        self._worker: Worker | None = None
        self._worker_cfg: Config | None = None
        self._problems_cfg: Config | None = None
        self._problems_checked_at: float | None = None
        self._releases_thread: threading.Thread | None = None
        self._checks_cache: list[checks.Check] | None = None
        self._checks_at: float | None = None

    # --- the web server --------------------------------------------------------------------------

    def start_web(self) -> None:
        """Binds now (Task 8's `WebServer`); on OSError (no free port) the app runs without pages:
        `page_port` stays None and `state.pages_off` says why (spec: When something goes wrong)."""
        ctx = web.WebContext(
            index_path=self._index_path,
            gate_reasons=lambda: gate_reasons(self.settings.current().config, self._probe),
            open_window=self.open_window,
            window_request=self._window_request,
            snapshot=self.state.snapshot,
            warnings=lambda: self.settings.current().warnings,
            checks=self.checks,
            quit=self.quit,
            pause=self.pause,
            resume=self.resume,
            open_folder=os.startfile,
            load_settings=self.settings.current,
            save_settings=self.settings.save,
            port_in_use=lambda: self.page_port,
            faceit_login_url=self.faceit_login_url,
            faceit_sign_in=self.faceit_sign_in,
            faceit_lookup=self.faceit_lookup,
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
        self._launcher = self._launcher_factory(f"http://127.0.0.1:{self.page_port}")
        self.state.set_pages_off(None)
        self._web.start()
        index = Index(self._index_path)
        try:
            index.set_flag("page_port", str(self.page_port))
        finally:
            index.close()
        log.info("the Demos to grab page is on port %s", self.page_port)

    def close(self) -> None:
        """The window goes first, then the web server it was showing."""
        if self._launcher is not None:
            self._launcher.close()
        if self._web is not None:
            self._web.stop()

    def open_window(self, page: str) -> None:
        """Shows `page` in the app window: starts the window process, or asks the one that is open to
        switch to it. The tray calls this, and so does `/api/window` for a second start (`hand_over`)
        or a notification button. With no web server there is no page to show."""
        if self._launcher is None:
            log.info("nothing to show %s in: there is no web server", page)
            return
        self._launcher.open(page)

    def _window_request(self) -> dict:
        """`/api/window`'s answer, which the page in the open window polls (see `WindowLauncher`)."""
        return self._launcher.current() if self._launcher is not None else {"seq": 0, "page": "/status"}

    # --- sign in with FACEIT (Settings) ---------------------------------------------------------------

    def faceit_login_url(self) -> str:
        """Where Settings' Sign in with FACEIT button goes, or "" when sign-in is not set up. The
        redirect comes back to `/settings` with the authorization code (spec: Settings)."""
        cfg = self.settings.current().config
        if not cfg.faceit_oauth_client_id or self.page_port is None:
            return ""
        state, verifier = f"{self.page_port}.{faceit_oauth.new_state()}", faceit_oauth.new_verifier()
        index = Index(self._index_path)
        try:
            index.set_flag("faceit_oauth_state", state)
            index.set_flag("faceit_oauth_verifier", verifier)
        finally:
            index.close()
        # ponytail: one live state, so a second tab's sign-in invalidates the first (and says so);
        # per-tab states only if that ever matters. The port rides in the state because the https
        # relay page in the middle has no other way to know which port this app is on.
        return faceit_oauth.authorize_url(cfg.faceit_oauth_client_id, self._faceit_redirect(cfg),
                                          state, faceit_oauth.challenge(verifier))

    def faceit_sign_in(self, code: str, state: str) -> str:
        """The browser's FACEIT answer turned into the signed-in player: the code is exchanged for a
        token, the token says which FACEIT player it is, and their nickname and SteamID are saved so
        Match Alerts pick them up (the same two settings someone would otherwise type)."""
        index = Index(self._index_path)
        try:
            expected = index.get_flag("faceit_oauth_state")
            verifier = index.get_flag("faceit_oauth_verifier") or ""
        finally:
            index.close()
        if not state or state != expected:
            raise OAuthError("that sign-in did not come back from this app; try again")
        cfg = self.settings.current().config
        if not cfg.faceit_api_key_protected:
            raise OAuthError("set the FACEIT API key first, then sign in")
        if not cfg.faceit_client_secret_protected:
            raise OAuthError("set the FACEIT client secret first, then sign in")
        try:
            token = faceit_oauth.access_token(code, cfg.faceit_oauth_client_id,
                                              protect.unprotect(cfg.faceit_client_secret_protected),
                                              self._faceit_redirect(cfg), verifier)
            client = FaceitClient(lambda: protect.unprotect(cfg.faceit_api_key_protected))
            player = client.player_by_id(faceit_oauth.player_id(token))
        except protect.ProtectError:
            raise OAuthError("a saved FACEIT secret can't be read on this Windows account") from None
        except FaceitError as exc:
            raise OAuthError(f"FACEIT could not answer: {exc}") from None
        errors = self.settings.save({"faceit_nickname": player.nickname,
                                     "subject_steamid": player.steamid})
        if errors:
            raise OAuthError("; ".join(errors.values()))
        return player.nickname

    def _faceit_redirect(self, cfg: Config) -> str:
        """The redirect URI, exactly as the FACEIT OAuth2 client has it registered: the one in Settings,
        else the https relay page (never the app's own address, which is plain http)."""
        return cfg.faceit_redirect_uri or faceit_oauth.RELAY_URL

    def faceit_lookup(self, nickname: str) -> dict:
        """The FACEIT player behind a nickname, saved as the two settings Match Alerts need. The app's
        own key does the asking, so no FACEIT sign-in is needed -- this is the whole of "tell the app
        who you are" without OAuth."""
        cfg = self.settings.current().config
        if not cfg.faceit_api_key_protected:
            raise FaceitError("set the FACEIT API key first")
        try:
            client = FaceitClient(lambda: protect.unprotect(cfg.faceit_api_key_protected))
            player = client.player(nickname)
        except protect.ProtectError:
            raise FaceitError("the saved FACEIT API key can't be read on this Windows account") from None
        errors = self.settings.save({"faceit_nickname": player.nickname,
                                     "subject_steamid": player.steamid})
        if errors:
            raise FaceitError("; ".join(errors.values()))
        return {"nickname": player.nickname, "steamid": player.steamid}

    # --- pause / resume (the tray, Task 14; the Status page's Pause and Resume) ---------------

    def pause(self) -> None:
        """Recorded as paused "by you", so Status can say that rather than "after 3 failed renders"."""
        index = Index(self._index_path)
        try:
            index.pause("you")
        finally:
            index.close()
        self.state.set_paused_by("you")

    def resume(self) -> None:
        index = Index(self._index_path)
        try:
            index.resume()
        finally:
            index.close()
        self.state.set_paused_by(None)

    # --- the worker thread -----------------------------------------------------------------------

    def start_worker(self) -> None:
        self._worker_thread = threading.Thread(target=self.run_worker, name="worker", daemon=True)
        self._worker_thread.start()

    def run_worker(self) -> None:
        """The thread ends only when asked to stop. A pass that raises -- the start-up problems check
        included -- is logged and published as a problem, and the next pass comes
        `WORKER_ERROR_WAIT_SECONDS` later: a fixed wait, since the settings may be what failed."""
        index = Index(self._index_path)
        try:
            while not self.stop.stopping():
                try:
                    delay = self.step(index)
                except Exception as exc:  # noqa: BLE001 - the thread must outlive anything a pass throws
                    log.exception("the worker hit an error")
                    self.state.set_problems((f"The worker hit an error: {exc}",))
                    delay = WORKER_ERROR_WAIT_SECONDS
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
        A rebuild that fails is logged and published as a problem, and tried again at the next recheck
        (which replaces the problem with the real start-up problems); a tick that raises is logged and
        the next pass still runs. Anything else it raises is `run_worker`'s to catch."""
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
            try:
                for folder in (cfg.demos_dir, cfg.renders_dir, cfg.library_dir):
                    folder.mkdir(parents=True, exist_ok=True)
                if self._worker is not None:
                    log.info("settings changed; rebuilding the worker")
                page_url = f"http://127.0.0.1:{self.page_port}/demos" if self.page_port else None
                worker = self._build(cfg, index, state=self.state, stop=self.stop,
                                     page_url=page_url, pages_off=self.state.snapshot().pages_off)
            except Exception as exc:  # noqa: BLE001 - a failed start is a problem to show; the thread goes on
                log.exception("could not start the worker")
                self.state.set_problems((f"Couldn't start the worker: {exc}",))
                return cfg.poll_seconds
            self._worker = worker
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

    # --- Status's checks ---------------------------------------------------------------------------

    def checks(self) -> list[checks.Check]:
        """`checks.run_checks`, cached for `CHECKS_CACHE_SECONDS` -- the Status page polls every 2 s,
        and some checks (a `pg_ctl status`, file-version reads) are not free. Never calls
        `self.releases.refresh_if_due` itself: that thread is the release check's only writer."""
        now = self._clock()
        if self._checks_at is None or now - self._checks_at >= CHECKS_CACHE_SECONDS:
            cfg = self.settings.current().config
            index = Index(self._index_path)
            try:
                alerts_status = index.get_flag("alerts_status")
            finally:
                index.close()
            self._checks_cache = checks.run_checks(cfg, alerts_status=alerts_status, releases=self.releases)
            self._checks_at = now
        return self._checks_cache

    # --- the HLAE release check ----------------------------------------------------------------------

    def start_releases(self) -> None:
        """Starts the release check's thread, once: `HlaeReleases` has no lock, so it is safe only with a
        single writer, and a second call starts nothing."""
        if self._releases_thread is not None:
            return
        self._releases_thread = threading.Thread(target=self._run_releases, name="releases", daemon=True)
        self._releases_thread.start()

    def _run_releases(self) -> None:
        while not self.stop.stopping():
            self.releases.refresh_if_due()
            self.stop.wait(RELEASES_INTERVAL_SECONDS)


def _wait_for_quit(app: App) -> None:
    """Blocks until the worker thread has ended, looking every half second so Ctrl+C is noticed: the
    first one asks for a quit-now, a second ends the wait at once."""
    quitting = False
    while True:
        try:
            if app.wait(timeout=0.5):
                return
        except KeyboardInterrupt:
            if quitting:
                return   # a second Ctrl+C ends it at once
            app.quit("now")
            print("stopping any render first, then quitting…", file=sys.stderr)
            quitting = True


def run(*, open_page: str | None = None, background: bool = False, headless: bool = False) -> int:
    """`clipper` / `clipper run`: the worker and the web server, and unless `headless` the window and the
    tray icon too (spec: The terminal; How the app runs, Starting). The window opens on `open_page`
    (Status when None); `background`, the sign-in mode, shows the tray icon only. The tray runs on this,
    the main, thread. A second start (`AlreadyRunning`) hands over to the running copy instead, asking
    it to show `open_page` (Status when None), or, with `background`, just exits."""
    try:
        with single_instance(paths.lock_file()):
            applog.setup(paths.logs_dir())
            move_in.on_start()
            store = SettingsStore(paths.settings_file())
            for warning in store.current().warnings:
                log.warning(warning)

            app = App(store, index_path=paths.index_file())
            try:
                app.start_web()
                app.start_worker()
                app.start_releases()
                if not headless:
                    if not background:
                        app.open_window(open_page or "/status")
                    if not tray.run_tray(app):
                        log.warning("no tray icon: the app keeps running without one")
                _wait_for_quit(app)   # at once when the tray only returned because the app had quit
            finally:
                app.close()
            return 0
    except AlreadyRunning as exc:
        if background:
            return 0   # started at sign-in with a copy already running: there is nothing to show
        page_port = settings.load(paths.settings_file()).config.page_port
        ports = range(page_port, page_port + web.PORTS_TO_TRY)
        if hand_over(open_page, ports):
            print("CS2 Clipper is already running; showing it", file=sys.stderr)
        else:
            print(f"{exc}, but its pages do not answer", file=sys.stderr)
        return 0


def run_headless(open_page: str | None = None) -> int:
    """`clipper run --headless`: `run` without the window and the tray."""
    return run(headless=True, open_page=open_page)
