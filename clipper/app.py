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
import shutil
import sys
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable
from datetime import timedelta
from functools import partial
from pathlib import Path

from clipper import (
    analysis, applog, checks, cs2_paths, faceit_oauth, hlae_plan, hlae_render, icon, links, move_in, packaged, paths,
    protect, provision, settings, share, tray, update, web, windows,
)
from clipper import notify as toasts
from clipper.alerts import MatchAlerts, utc_now
from clipper.config import Config
from clipper.faceit import FaceitClient, FaceitError
from clipper.faceit_oauth import OAuthError
from clipper.footprint import Footprint
from clipper.gate import Gate, Schedule
from clipper.index import Index
from clipper.intake import Intake
from clipper.join import join_reel
from clipper.lock import AlreadyRunning, single_instance
from clipper.media import gpu_encoder, probe_duration
from clipper.procs import ProcessProbe, SystemProbe
from clipper.settings import SettingsStore
from clipper.state import AppState
from clipper.unpack import unpack
from clipper.links import PAGES  # noqa: F401 - its one home is links.py; cli.py and the tests read it as app.PAGES
from clipper.window import WindowLauncher
from clipper.worker import DeleteRequest, Services, StopRequest, Worker, delete_demo

log = logging.getLogger(__name__)

CHECKS_CACHE_SECONDS = 15.0            # the Status page polls every 2 s; some checks are not free
RELEASES_INTERVAL_SECONDS = 10 * 60.0  # HlaeReleases and Updates only ask GitHub when it is due
WORKER_ERROR_WAIT_SECONDS = 30.0       # how long the worker thread waits after a pass that raised
QUIT_WAIT_SECONDS = 60.0               # how long `clipper quit` waits for the running copy to end
QUIT_LOOK_SECONDS = 0.25               # and how often it looks whether it has


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


def _ask(ports: Iterable[int], route: str, body: dict) -> int | None:
    """Finds the running copy among `ports` and posts `body` to its `route`: the status it answers
    with, or None when none of `ports` is a running copy, or it refuses."""
    port = find_running(ports)
    if port is None:
        return None
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{route}", data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", web.MARKER_HEADER: "1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=1.0) as response:
            return response.status
    except OSError:   # urllib.error.HTTPError (a 4xx/5xx "refusal") is one too
        return None


def hand_over(page: str | None, ports: Iterable[int]) -> bool:
    """Finds the running copy among `ports` and asks it to show `page` (or its home page, Reels once set up). True
    once it answers 204; False when none of `ports` is a running copy, or it refuses."""
    return _ask(ports, "/api/window", {"page": page or "home"}) == 204


def ask_to_quit(ports: Iterable[int]) -> bool:
    """Finds the running copy among `ports` and asks it to quit now, stopping a render that is under
    way. True once it answers; False when none of `ports` is a running copy, or it refuses."""
    return _ask(ports, "/api/quit", {"mode": "now"}) == 200


def open_link(url: str, *, ask: Callable[[Iterable[int], str, dict], int | None] | None = None,
              start: Callable[..., int] | None = None) -> int:
    """`clipper --link URL`: a cs2clipper: link a toast's button opened (clipper.links). The running copy follows
    it; with none running, the app starts and shows the page the link is about. 2 for a URL that is not a link."""
    link = links.parse(url)
    if link is None:
        print("not a CS2 Clipper link", file=sys.stderr)
        return 2
    page_port = settings.load(paths.settings_file()).config.page_port
    if (ask or _ask)(range(page_port, page_port + web.PORTS_TO_TRY), "/api/link", {"url": url}) == 204:
        return 0
    return (start or run)(open_page=link.page)


def register_with_windows() -> None:
    """At every start: cs2clipper: links start this copy (clipper.links), toasts go out as CS2 Clipper with its
    mark (clipper.notify), and the window shares the Start menu shortcut's taskbar button. Best effort: what
    fails is logged, and toasts then say Windows PowerShell, as they did before."""
    try:
        links.register(windows.set_user_value)
    except OSError as exc:
        log.warning("cs2clipper: links are not registered, so toast buttons open nothing: %s", exc)
    try:
        mark = paths.data_dir() / "CS2Clipper.png"
        icon.write_png(mark)
        toasts.register(mark, windows.set_user_value)
    except OSError as exc:
        log.warning("toasts go out as Windows PowerShell: %s", exc)
    try:
        windows.set_app_id(toasts.APP_ID)
    except OSError as exc:
        log.warning("the window may not share the shortcut's taskbar button: %s", exc)


def start_match_alerts(cfg: Config, index: Index, probe: SystemProbe, *,
                       page_url: str | None, pages_off: str | None) -> MatchAlerts | None:
    """Start the match alerts step. When alerts are off, say why in the index (clipper status shows
    it) and return None. The app owns the one web server now (Task 8); a missing `page_url` means it
    could not start, and `pages_off` (from `App.start_web`) carries the reason."""
    if not cfg.match_alerts:
        index.set_flag("alerts_status", "off: switched off in Settings")
        return None
    if not cfg.faceit_nickname:
        index.set_flag("alerts_status", "off: look up your FACEIT nickname on Status or in Settings")
        return None
    if not cfg.faceit_api_key_protected:
        index.set_flag("alerts_status", "off: this copy has no FACEIT API key; add one under Settings -> Advanced")
        return None
    if page_url is None:
        index.set_flag("alerts_status", f"off: the Demos to grab page could not start: {pages_off}")
        return None
    faceit = FaceitClient(lambda: protect.unprotect(cfg.faceit_api_key_protected))
    return MatchAlerts(index, faceit, toasts.notify, probe.user_cs2_running, nickname=cfg.faceit_nickname,
                       subject_steamid=cfg.subject_steamid, page_url=page_url,
                       stopped_playing_minutes=cfg.stopped_playing_minutes)


_gpu_encoders: dict[str, tuple[str, str] | None] = {}    # FFmpeg -> the graphics card encoder it can use, once asked


def _video_settings(cfg: Config) -> hlae_plan.VideoSettings:
    """What a Render Job records with: the HUD settings, the encoder (the graphics card's, when Settings asks for it
    and the PC has one), and the plan's own defaults for the rest."""
    encoder = {}
    if cfg.encoder == "gpu":
        if cfg.ffmpeg not in _gpu_encoders:
            _gpu_encoders[cfg.ffmpeg] = gpu_encoder(cfg.ffmpeg)
            log.info("GPU encoder: %s", (_gpu_encoders[cfg.ffmpeg] or ("none, so x264",))[0])
        found = _gpu_encoders[cfg.ffmpeg]
        if found:
            encoder = {"video_codec": found[0], "output_parameters": found[1]}
    return hlae_plan.VideoSettings(
        show_xray=cfg.show_xray, show_assists=cfg.show_assists, show_only_death_notices=cfg.hud == "killfeed",
        death_notices_duration=cfg.kill_feed_seconds, player_voices_enabled=cfg.player_voices, **encoder)


def _program(name: str) -> Path | None:
    """Where `name`, a program on PATH or a full path to one, is; None: nowhere."""
    found = shutil.which(name)
    return Path(found) if found else None


def build_worker(cfg: Config, index: Index, *, state: AppState, stop: StopRequest, deletes: DeleteRequest,
                 page_url: str | None, pages_off: str | None) -> Worker:
    probe = SystemProbe()
    gate = Gate(probe, cfg.data_root, cfg.min_free_gb, schedule=schedule(cfg))
    analyses = analysis.Analyses(cfg.analyses_dir, cfg.csda_exe)
    duration_of = partial(probe_duration, ffprobe=cfg.ffprobe)

    # CS2, HLAE and FFmpeg are looked for per job: they may have moved
    def render_job(request, should_abort, progress):
        return hlae_render.render(
            request, probe=probe, should_abort=should_abort, stall_seconds=cfg.stall_seconds,
            launch_timeout_seconds=cfg.launch_timeout_seconds, duration_of=duration_of,
            load_inputs=analyses.render_inputs, cs2_exe=cs2_paths.find_cs2_exe(), hlae_exe=checks.hlae_exe(cfg),
            hlae_ffmpeg=_program(cfg.ffmpeg), ffmpeg=cfg.ffmpeg, cs2_settings_dir=cfg.cs2_settings_dir,
            progress=progress, video=_video_settings(cfg))

    services = Services(
        intake=Intake(cfg.downloads_dir, cfg.demos_dir),
        unpack=unpack,
        analyze=analyses.analyze,
        facts=analyses,
        gate=gate,
        render=render_job,
        join=partial(join_reel, ffmpeg=cfg.ffmpeg, duration_of=duration_of, stretch=cfg.stretch),
        notify=toasts.notify,
        alerts=start_match_alerts(cfg, index, probe, page_url=page_url, pages_off=pages_off),
    )
    return Worker(cfg, index, services, state=state, stop=stop, deletes=deletes)


def adopt_default_clips_folder(store: SettingsStore, videos: Callable[[], Path] | None = None) -> Path | None:
    """A fresh install keeps its clips in Videos\\CS2 Clipper: set, and made, when no clips folder is, so the first
    screen asks for nothing the app can choose itself. Settings moves it. The folder set, or None when one was
    set already or this one cannot be."""
    if store.current().config.data_root is not None:
        return None
    try:
        folder = (videos or windows.videos_dir)() / "CS2 Clipper"
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("no default clips folder: %s", exc)
        return None
    errors = store.save({"data_root": str(folder)})
    if errors:
        log.warning("no default clips folder: %s", "; ".join(errors.values()))
        return None
    log.info("the clips folder is %s", folder)
    return folder


def schedule(cfg: Config) -> Schedule:
    """When renders may start, as Settings -> Start renders says."""
    return Schedule(cfg.render_when, idle_seconds=windows.idle_seconds)


def gate_reasons(cfg: Config, probe: ProcessProbe) -> tuple[str, ...]:
    """For the Demos to grab page: the Gate's reasons now, or a clips-folder reason when none is set
    yet (the Gate itself needs a `data_root` to check free space against)."""
    if cfg.data_root is None:
        return ("no clips folder is set",)
    return Gate(probe, cfg.data_root, cfg.min_free_gb, schedule=schedule(cfg)).check().reasons


class App:
    """One running copy: its settings, what it is doing (`state`), the web server, the window, the
    worker thread, Setup and the app's own updates. `problems`, `build`, `launcher` (given the web
    server's base URL), `setup` and `updates` are swappable so tests never touch real csda/HLAE/CS2,
    open a window, ask GitHub or install anything."""

    def __init__(self, settings: SettingsStore, *, index_path: Path, state: AppState | None = None,
                 stop: StopRequest | None = None,
                 problems: Callable[[Config], list[str]] = checks.startup_problems,
                 build: Callable[..., Worker] = build_worker,
                 launcher: Callable[[str], WindowLauncher] = WindowLauncher,
                 setup: Callable[..., provision.Setup] = provision.Setup,
                 updates: Callable[..., update.Updates] = update.Updates,
                 reveal: Callable[[Path], None] = windows.reveal,
                 recheck_seconds: float = 60.0, clock: Callable[[], float] = time.monotonic):
        self.settings = settings
        self.state = state if state is not None else AppState()
        self.stop = stop if stop is not None else StopRequest()
        self.deletes = DeleteRequest()          # the Status page's deletes, shared with every worker built
        self.releases = checks.HlaeReleases()
        self.setup = setup(self._setup_context, after=self._after_setup)
        self.updates = updates(current=update.running_version(), enabled=packaged.frozen(),
                               folder=lambda: paths.data_dir() / "downloads", logs=paths.logs_dir,
                               busy=self._update_refused, announce=self._announce_update)
        self.page_port: int | None = None
        self.footprint = Footprint()
        self._index_path = index_path
        self._problems = problems
        self._build = build
        self._launcher_factory = launcher
        self._launcher: WindowLauncher | None = None
        self._reveal = reveal
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
        self._setups_ended = 0                  # counted on Setup's thread; the two below are what was last looked at
        self._problems_setups = 0
        self._checks_setups = 0

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
            faceit_find=self.faceit_find,
            setup_status=self.setup.status,
            start_setup=self.start_setup,
            update_status=self.updates.status,
            start_update=self.start_update,
            delete_demo=self.delete_demo,
            check_faceit=self.check_faceit,
            follow_link=self.follow_link,
            footprint=lambda: self.footprint.usage(),
            onboarding=self.onboarding,
            home=self.home,
            copy_file=share.copy_to_clipboard,
            export_reel=share.export,
            reveal=self._reveal,
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

    def home(self) -> str:
        """The page the window opens on: Reels, once the PC is set up (clip apps open on the clips), else Status,
        which sets it up."""
        return "/reels" if self.onboarding()["done"] else "/status"

    # --- the first run: one screen, three things (Status) ------------------------------------------------------

    def onboarding(self) -> dict:
        """What the first-run screen shows: who the Highlights are for, where they go, and the tools, which download
        on their own. `done` once all three are there."""
        cfg = self.settings.current().config
        setup = self.setup.status()
        missing = [step for step in setup.get("steps", []) if step["state"] != "ok" and not step.get("update")]
        tools = ("downloading" if setup.get("running") else "failed" if setup.get("error") and missing
                 else "needed" if missing or not setup.get("steps") and setup.get("needed") else "ok")
        return {"done": bool(cfg.subject_steamid) and cfg.data_root is not None and tools == "ok",
                "nickname": cfg.faceit_nickname, "steamid": bool(cfg.subject_steamid),
                "clips_folder": str(cfg.data_root or ""), "tools": tools,
                "download_mb": round(setup.get("download_bytes", 0) / 1_000_000)}

    def start_setup_if_missing(self) -> None:
        """At start, Setup downloads what the PC lacks (the installer says it will): nobody has to find a button
        for it. A newer HLAE or FFmpeg alone waits for its Update button, since an update can wait."""
        status = self.setup.status()
        if status.get("running"):
            return
        if any(step["state"] != "ok" and not step.get("update") for step in status.get("steps", [])):
            log.info("Setup starts on its own: this PC lacks some of the tools")
            self.start_setup()

    # --- cs2clipper: links (toast buttons; `open_link`) ---------------------------------------------------

    def follow_link(self, link: links.Link) -> None:
        """Does what a cs2clipper: link asks: shows a page, shows a Reel's file in Explorer (or, when it is gone,
        Reels), or snoozes renders."""
        if link.action == "open":
            self.open_window(link.page)
        elif link.action == "reveal":
            if not self.reveal_reel(link.reel):
                self.open_window(link.page)
        elif link.action == "snooze":
            self.snooze(link.minutes)

    def reveal_reel(self, reel_id: int) -> bool:
        """Shows Reel `reel_id`'s file, selected, in Explorer. False when there is no such Reel or file."""
        cfg = self.settings.current().config
        index = Index(self._index_path)
        try:
            row = index.reel(reel_id)
        finally:
            index.close()
        if row is None or cfg.data_root is None:
            return False
        path = cfg.load_path(row["path"])
        if not path.is_file():
            return False
        self._reveal(path)
        return True

    def snooze(self, minutes: int) -> None:
        """No render starts for `minutes` (the heads-up toast's Not now); Resume ends it sooner."""
        until = utc_now() + timedelta(minutes=minutes)
        index = Index(self._index_path)
        try:
            index.snooze(until)
        finally:
            index.close()
        log.info("renders snoozed until %s", until.isoformat(timespec="seconds"))

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
        """The FACEIT player behind a nickname, saved as the two settings Match Alerts need: the first-run setup's
        Look up. The app's own key does the asking, so no FACEIT sign-in is needed -- this is the whole of "tell
        the app who you are" without OAuth."""
        found = self.faceit_find(nickname)
        errors = self.settings.save({"faceit_nickname": found["nickname"], "subject_steamid": found["steamid"]})
        if errors:
            raise FaceitError("; ".join(errors.values()))
        return found

    def faceit_find(self, nickname: str) -> dict:
        """The FACEIT player behind a nickname, not saved: Settings' Look up fills in the two boxes, and Save keeps
        them with everything else."""
        cfg = self.settings.current().config
        if not cfg.faceit_api_key_protected:
            raise FaceitError("this copy has no FACEIT API key: add one under Settings -> Advanced")
        try:
            client = FaceitClient(lambda: protect.unprotect(cfg.faceit_api_key_protected))
            player = client.player(nickname)
        except protect.ProtectError:
            raise FaceitError("the saved FACEIT API key can't be read on this Windows account") from None
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
        """Ends a pause, and a snooze too."""
        index = Index(self._index_path)
        try:
            index.resume()
            index.snooze(None)
        finally:
            index.close()
        self.state.set_paused_by(None)

    # --- deleting a Demo (the Status page's In progress list) ---------------------------------------

    def delete_demo(self, demo_id: int) -> str | None:
        """"deleted"; "deferred" while the worker finishes its step on the Demo (a render is aborted)
        and then deletes it; None for no such Demo. ValueError when it is already in Reels."""
        index = Index(self._index_path)
        try:
            return self.deletes.delete(
                demo_id, lambda: delete_demo(index, self.settings.current().config, demo_id))
        finally:
            index.close()

    # --- Check for demos (the pages' quick wheel and console) ------------------------------------------

    def check_faceit(self) -> str | None:
        """Asks Match Alerts to look for new FACEIT matches on the worker's next tick. Why they cannot, or None."""
        worker = self._worker
        alerts = worker.services.alerts if worker is not None else None
        if alerts is None:
            return "Match alerts are off. Look up your FACEIT nickname first."
        return alerts.check_now()

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
        """One pass: start-up problems first (checked again every `recheck_seconds`, and as soon as the
        settings change or a Setup ends), then a rebuild when settings changed, then a tick.
        A rebuild that fails is logged and published as a problem, and tried again at the next recheck
        (which replaces the problem with the real start-up problems); a tick that raises is logged and
        the next pass still runs. Anything else it raises is `run_worker`'s to catch."""
        cfg = self.settings.current().config
        if self.setup.running:      # it replaces the tools a tick would run
            return cfg.poll_seconds
        now = self._clock()
        setups = self._setups_ended
        needs_check = (
            self._problems_checked_at is None
            or cfg != self._problems_cfg
            or now - self._problems_checked_at >= self._recheck_seconds
            or setups != self._problems_setups
        )
        if needs_check:
            problems = self._problems(cfg)
            self.state.set_problems(problems)
            self._problems_cfg = cfg
            self._problems_checked_at = now
            self._problems_setups = setups      # the count from before the check: a run ending during it is not missed
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
                page_url = links.to_page("/demos") if self.page_port else None   # its toasts open the window
                worker = self._build(cfg, index, state=self.state, stop=self.stop, deletes=self.deletes,
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
        and some checks (HLAE's changelog, a drive's free space) are not free. Never calls
        `self.releases.refresh_if_due` itself: that thread is the release check's only writer."""
        now = self._clock()
        setups = self._setups_ended
        if (self._checks_at is None or now - self._checks_at >= CHECKS_CACHE_SECONDS
                or setups != self._checks_setups):
            cfg = self.settings.current().config
            index = Index(self._index_path)
            try:
                alerts_status = index.get_flag("alerts_status")
            finally:
                index.close()
            self._checks_cache = checks.run_checks(cfg, alerts_status=alerts_status, releases=self.releases)
            self._checks_at = now
            self._checks_setups = setups
        return self._checks_cache

    # --- Setup: what a fresh PC lacks ----------------------------------------------------------------

    def start_setup(self) -> str | None:
        """Starts Setup, unless it is running already. Returns why not, in words for the user, when it
        must not start now: it replaces HLAE and FFmpeg, which a render is using."""
        if self.state.snapshot().rendering is not None:
            return "A Reel is rendering. Set up once it is done."
        self.setup.start()
        return None

    def _setup_context(self) -> provision.Context:
        return provision.Context(store=self.settings, latest_hlae=self.releases.latest)

    def _after_setup(self) -> None:
        """Called on Setup's thread when a run has ended: the worker's next pass and Status's next
        checks look at the PC again, rather than at what they found before."""
        self._setups_ended += 1

    # --- the app's own updates ----------------------------------------------------------------------

    def start_update(self) -> str | None:
        """Starts the update to the newer version on offer. Returns why not, in words for the user."""
        return self.updates.start()

    def _update_refused(self) -> str | None:
        """Why the app cannot be updated now: the installer quits the app, which would stop a render, or Setup
        halfway through installing the tools."""
        if self.state.snapshot().rendering is not None:
            return "A Reel is rendering. Update once it is done."
        if self.setup.running:
            return "Set up is running. Update once it is done."
        return None

    def _announce_update(self, release: update.Release) -> None:
        """A toast for a newer version: the app mostly runs in the tray, where Status is not seen."""
        actions = (("Show", links.to_page("/status")), ("Not now", None)) if self.page_port else ()
        toasts.notify(f"CS2 Clipper {release.version} is out",
               "Open Status to see what changed, then update with one click.", actions)

    # --- the HLAE release check ----------------------------------------------------------------------

    def start_releases(self) -> None:
        """Starts the release check's thread, once: `HlaeReleases` has no lock, so it is safe only with a
        single writer, and a second call starts nothing. It looks for the app's own updates too."""
        if self._releases_thread is not None:
            return
        self._releases_thread = threading.Thread(target=self._run_releases, name="releases", daemon=True)
        self._releases_thread.start()

    def _run_releases(self) -> None:
        while not self.stop.stopping():
            self.releases.refresh_if_due()
            self.updates.refresh_if_due()
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
            register_with_windows()
            store = SettingsStore(paths.settings_file())
            for warning in store.current().warnings:
                log.warning(warning)
            adopt_default_clips_folder(store)

            app = App(store, index_path=paths.index_file())
            try:
                app.start_web()
                app.start_worker()
                app.start_releases()
                app.start_setup_if_missing()
                if not headless:
                    if not background:
                        app.open_window(open_page or app.home())
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


def _a_copy_holds_the_lock() -> bool:
    try:
        with single_instance(paths.lock_file()):
            return False
    except AlreadyRunning:
        return True


def quit_running(*, wait_seconds: float = QUIT_WAIT_SECONDS, ask: Callable[[Iterable[int]], bool] = ask_to_quit,
                 others: Callable[[], list] = packaged.other_copies, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> int:
    """`clipper quit`, which the installer runs before it replaces the exe, as an exe that is running
    cannot be replaced: asks the running copy to quit and waits until it has ended, and with it every
    other process that runs the exe (`others`). 0 once nothing of the app runs, which is at once when
    nothing did; 1 when something still does after `wait_seconds`."""
    page_port = settings.load(paths.settings_file()).config.page_port
    ports = range(page_port, page_port + web.PORTS_TO_TRY)
    deadline = clock() + wait_seconds
    asked = False
    while True:
        running = _a_copy_holds_the_lock()
        if not running and not others():
            return 0
        if clock() >= deadline:
            print("CS2 Clipper is still running", file=sys.stderr)
            return 1
        if running and not asked:
            asked = ask(ports)      # and again the next time round, when its pages did not answer
        sleep(QUIT_LOOK_SECONDS)
