"""The Flask app: the four pages (Status, Demos to grab, Reels, Settings), their JSON endpoints, and the
reach and action guards (spec: Pages, Reach from other devices). Every route is PC-only except the Demos
to grab page, its list and its actions, and the stylesheet and script those load; every POST, PUT and
DELETE, those included, must carry the marker header. What the pages need from the app comes in through
`WebContext`, so a test builds one with only the fields it exercises.

Structure adapted from thelifeofsuleyman/cs2-clipper's `aegis/web.py` (a single Flask app factory);
`WebServer` adapts `_serve` from `aegis/app.py` (waitress on a background thread), replacing its
blocking `serve()` with `waitress.server.create_server` so `start()`/`stop()` can control the thread
and the bind happens (and can fail) before `start()` is ever called.

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
import re
import socket
import sqlite3
import subprocess
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import psutil
from flask import Flask, Response, jsonify, redirect, request, send_file
from waitress.server import create_server
from werkzeug.routing import BaseConverter

from clipper import applog
from clipper.alerts import LINK_LIFETIME, map_label, utc_now
from clipper.checks import Check
from clipper.config import Config
from clipper.faceit import FaceitError
from clipper.faceit_oauth import OAuthError
from clipper.index import Index
from clipper.settings import FIELDS, SECRETS, Field, Loaded, json_values
from clipper.state import Snapshot, summary

log = logging.getLogger(__name__)

MARKER_HEADER = "X-CS2-Clipper"
PAGES_DIR = Path(__file__).with_name("pages")
PAGES = ("/status", "/demos", "/reels", "/settings")   # what `/api/window` may ask the window to show
PORTS_TO_TRY = 10
KEEP_DECIDED = timedelta(hours=24)
IN_PIPELINE = ("spotted", "unpacked", "analyzed", "scored", "rendering", "joined")
NOT_LISTED = ("done", "skipped")               # Status's Demos list leaves these out (spec: Pages, Status)
PERSPECTIVES = ("player", "enemy")

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_MATCH_ID_PATTERN = r"1-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_CHECKSUM_PATTERN = r"[0-9a-f]{1,16}"         # as csda writes it: hex without its leading zeros
# Flask endpoint names (not paths) reachable from another device (spec: Reach from other devices).
_PHONE_ENDPOINTS = frozenset({"demos_page", "demos_json", "demos_action", "app_css", "app_js"})


class _MatchIdConverter(BaseConverter):
    """Only a real match id is a route at all; anything else (``..``, a stray file name) is a plain
    404 regardless of method, the way today's regex-matched `page.py` never routes it anywhere."""

    regex = _MATCH_ID_PATTERN


class _ChecksumConverter(BaseConverter):
    """Only a real match checksum (up to 16 lowercase hex digits) is a route at all; anything else is
    a plain 404 (spec: Pages, Reels)."""

    regex = _CHECKSUM_PATTERN


def own_addresses() -> set[str]:
    """Every address of this PC: loopback, LAN and Tailscale alike."""
    found = {"127.0.0.1", "::1"}
    for entries in psutil.net_if_addrs().values():
        for entry in entries:
            if entry.family in (socket.AF_INET, socket.AF_INET6):
                found.add(entry.address.split("%")[0])
    return found


def page_rows(index: Index, now: datetime, waiting_for: tuple[str, ...]) -> list[dict]:
    """The Demos to grab page's list, newest first. `waiting_for` is the Gate's reasons, shown on
    grabbed Demos that have not been rendered yet."""
    rows = []
    for row in index.page_matches(now - KEEP_DECIDED):
        finished = datetime.fromisoformat(row["finished_at"])
        rendering_waits = row["state"] == "grabbed" and row["demo_state"] in IN_PIPELINE
        rows.append({
            "id": row["match_id"],
            "map": map_label(row["map"]),
            "finished_at": row["finished_at"],
            "expires_at": (finished + LINK_LIFETIME).isoformat(timespec="seconds"),
            "won": bool(row["won"]),
            "score": f"{row['team_score']}–{row['opponent_score']}",
            "highlights": json.loads(row["highlights"]),
            "rating": round(row["rating"], 2),
            "kd": f"{row['kills']}–{row['deaths']}",
            "adr": round(row["adr"], 1),
            "state": row["state"],
            "reminded": row["reminded_at"] is not None,
            "url": row["matchroom_url"],
            "waiting_for": "; ".join(waiting_for) if rendering_waits else "",
        })
    return rows


def status_demos(index: Index) -> list[dict]:
    """Status's Demos list: every Demo not done or skipped, newest first, with the latest Render Job
    per Perspective (none before rendering has been queued)."""
    rows = [row for row in index.all_demos() if row["state"] not in NOT_LISTED]
    rows.reverse()
    demos = []
    for row in rows:
        match = index.match(row["match_checksum"]) if row["match_checksum"] else None
        jobs = []
        for perspective in PERSPECTIVES:
            job = index.latest_render(row["id"], perspective)
            if job is not None:
                jobs.append({"perspective": perspective, "attempt": job["attempt"], "state": job["state"],
                            "failure": job["failure"]})
        demos.append({
            "id": row["id"], "file_name": row["file_name"], "state": row["state"],
            "map": map_label(match["map"]) if match is not None else None,
            "error": row["last_error"], "retry": row["state"] == "failed", "jobs": jobs,
        })
    return demos


def field_dict(f: Field) -> dict:
    """One `FIELDS` entry as JSON for the Settings page (spec: Pages, Settings)."""
    return {"name": f.name, "group": f.group, "label": f.label, "kind": f.kind,
            "minimum": f.minimum, "maximum": f.maximum, "choices": list(f.choices), "help": f.help}


def reels_row(index: Index, match: sqlite3.Row) -> dict:
    """One entry of `/api/reels`: `match`'s Reels, in round order (spec: Pages, Reels)."""
    highlights = [
        {"round": h["round"], "type": h["type"], "reasons": json.loads(h["reasons"]),
         "player": h["player_reel_id"], "enemy": h["enemy_reel_id"]}
        for h in index.match_reels(match["checksum"])
    ]
    return {
        "checksum": match["checksum"], "map": map_label(match["map"]), "played_at": match["played_at"],
        "score": f"{match['team_score']}–{match['opponent_score']}", "result": match["result"],
        "highlights": highlights,
    }


def reel_frame_file(video: Path, ffmpeg: str) -> Path | None:
    """The still the Reels carousel shows for `video`, cut on the first request and kept beside it; None
    when ffmpeg cannot cut one. Three seconds in lands just before the first Frag (4 s of padding). It is
    cut under a name of its own and moved into place whole, so a second request for the same Reel never
    serves a half-written file."""
    frame = video.with_suffix(".jpg")
    if frame.is_file() and frame.stat().st_mtime >= video.stat().st_mtime:
        return frame
    part = video.with_name(f"{video.stem}.{threading.get_ident()}.part.jpg")
    try:
        subprocess.run([ffmpeg, "-y", "-ss", "3", "-i", str(video), "-frames:v", "1", "-vf", "scale=1280:-2",
                        "-q:v", "4", str(part)],
                       capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        os.replace(part, frame)      # no `part` at all when ffmpeg cut nothing (a Reel under 3 s)
    except (OSError, subprocess.SubprocessError):
        part.unlink(missing_ok=True)
        return frame if frame.is_file() else None
    return frame


def _faceit_not_set_up(token: str, state: str) -> str:
    raise OAuthError("FACEIT sign-in is not set up")


def _faceit_lookup_not_set_up(nickname: str) -> dict:
    raise FaceitError("FACEIT lookup is not set up")


def _nothing_to_set_up() -> dict:
    return {"running": False, "needed": False, "steps": [], "download_bytes": 0, "progress": None, "error": None}


def _no_update() -> dict:
    return {"current": "", "available": None, "running": False, "stage": None, "progress": None, "error": None}


@dataclass
class WebContext:
    """What the pages need from the app, one field per thing. `App.start_web` fills every one; each field
    after `gate_reasons` has a harmless default, so a test names only the ones it exercises."""

    index_path: Path
    gate_reasons: Callable[[], tuple[str, ...]]
    own_addresses: Callable[[], set[str]] = own_addresses
    clock: Callable[[], datetime] = utc_now
    open_window: Callable[[str], None] = lambda page: None
    snapshot: Callable[[], Snapshot] = Snapshot
    warnings: Callable[[], tuple[str, ...]] = tuple
    checks: Callable[[], list[Check]] = list
    quit: Callable[[str | None], str] = lambda mode: "now"
    pause: Callable[[], None] = lambda: None
    resume: Callable[[], None] = lambda: None
    log_lines: Callable[[int], list[str]] = applog.recent
    open_folder: Callable[[Path], None] = lambda folder: None
    load_settings: Callable[[], Loaded] = lambda: Loaded(Config(), ())
    save_settings: Callable[[Mapping[str, object]], dict[str, str]] = lambda changes: {}
    port_in_use: Callable[[], int | None] = lambda: None
    window_request: Callable[[], dict] = lambda: {"seq": 0, "page": "/status"}
    faceit_login_url: Callable[[], str] = lambda: ""
    faceit_sign_in: Callable[[str, str], str] = _faceit_not_set_up
    faceit_lookup: Callable[[str], dict] = _faceit_lookup_not_set_up
    setup_status: Callable[[], dict] = _nothing_to_set_up        # provision.Setup.status
    start_setup: Callable[[], str | None] = lambda: None         # why it was not started, or None
    update_status: Callable[[], dict] = _no_update               # update.Updates.status
    start_update: Callable[[], str | None] = lambda: None        # why it was not started, or None
    delete_demo: Callable[[int], str | None] = lambda demo_id: None   # "deleted", "deferred" or None: no such Demo


def _client_address() -> str:
    """The current request's address, normalised: an IPv4-mapped ``::ffff:127.0.0.1`` counts as
    ``127.0.0.1``, and a zone id (``fe80::1%eth0``) is stripped."""
    return (request.remote_addr or "").split("%")[0].removeprefix("::ffff:")


def _is_pc_request() -> bool:
    """Loopback only, and only under a `Host` the server itself answers to -- `127.0.0.1:<port>` or
    `localhost:<port>`, `<port>` being the port the request arrived on. Defeats DNS rebinding."""
    if _client_address() not in ("127.0.0.1", "::1"):
        return False
    port = request.environ.get("SERVER_PORT", "")
    return request.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}")


def _no_store(response: Response) -> Response:
    response.headers["Cache-Control"] = "no-store"
    return response


def create_app(ctx: WebContext) -> Flask:
    app = Flask(__name__)
    app.url_map.converters["match_id"] = _MatchIdConverter
    app.url_map.converters["checksum"] = _ChecksumConverter

    @app.before_request
    def _guard():
        if request.method in _WRITE_METHODS and request.headers.get(MARKER_HEADER) != "1":
            return Response(status=403)
        if request.endpoint in _PHONE_ENDPOINTS:
            return None
        if not _is_pc_request():
            return Response(status=403)
        return None

    @app.get("/health")
    def health():
        return _no_store(jsonify(ok=True, app="cs2-clipper"))

    @app.get("/")
    def root():
        return redirect("/status")

    @app.get("/status")
    def status_page():
        return _no_store(Response((PAGES_DIR / "status.html").read_bytes(), mimetype="text/html"))

    @app.get("/api/status")
    def api_status():
        snapshot = ctx.snapshot()
        rendering = None
        if snapshot.rendering is not None:
            rendering = {"map": map_label(snapshot.rendering.map_name),
                        "perspective": snapshot.rendering.perspective, "started_at": snapshot.rendering.started_at}
        index = Index(ctx.index_path)
        try:
            demos = status_demos(index)
        finally:
            index.close()
        try:
            checks = [{"name": c.name, "ok": c.ok, "detail": c.detail, "hint": c.hint} for c in ctx.checks()]
        except Exception as exc:  # noqa: BLE001 - a check that raises must not blank the whole page
            log.exception("could not run the checks")
            checks = [{"name": "Checks", "ok": False, "detail": f"Couldn't run the checks: {exc}", "hint": ""}]
        body = {
            "summary": summary(snapshot),
            "rendering": rendering,
            "paused_by": snapshot.paused_by,
            "quitting": snapshot.quitting,
            "problems": list(snapshot.problems),
            "warnings": list(ctx.warnings()),
            "pages_off": snapshot.pages_off,
            "checks": checks,
            "demos": demos,
        }
        return _no_store(jsonify(body))

    @app.get("/api/summary")
    def api_summary():
        """What the nav's status pill needs on every page: `/api/status` without its six checks."""
        snapshot = ctx.snapshot()
        return _no_store(jsonify(
            summary=summary(snapshot),
            rendering=snapshot.rendering is not None,
            paused_by=snapshot.paused_by,
            problems=list(snapshot.problems),
        ))

    @app.get("/api/log")
    def api_log():
        return _no_store(jsonify(lines=ctx.log_lines(200)))

    @app.post("/api/demos/<int:demo_id>/retry")
    def api_demo_retry(demo_id: int):
        index = Index(ctx.index_path)
        try:
            if index.demo(demo_id) is None:
                return Response(status=404)
            try:
                state = index.retry(demo_id)
            except ValueError as exc:     # not failed: `retry` checks inside its own transaction, so no race
                response = jsonify(error=str(exc))
                response.status_code = 409
                return _no_store(response)
        finally:
            index.close()
        return _no_store(jsonify(state=state))

    @app.post("/api/demos/<int:demo_id>/delete")
    def api_demo_delete(demo_id: int):
        """200 once the Demo is gone; 202 while the worker finishes its step on it (a render is aborted)."""
        try:
            result = ctx.delete_demo(demo_id)
        except ValueError as exc:         # already in Reels
            response = jsonify(error=str(exc))
            response.status_code = 409
            return _no_store(response)
        if result is None:
            return Response(status=404)
        response = jsonify(result=result)
        response.status_code = 202 if result == "deferred" else 200
        return _no_store(response)

    @app.post("/api/pause")
    def api_pause():
        ctx.pause()
        return Response(status=204)

    @app.post("/api/resume")
    def api_resume():
        ctx.resume()
        return Response(status=204)

    @app.get("/api/setup")
    def api_setup():
        return _no_store(jsonify(ctx.setup_status()))

    @app.post("/api/setup")
    def api_setup_start():
        """202: setup goes on after the reply, and `GET /api/setup` tells how it is going."""
        refused = ctx.start_setup()
        if refused:
            response = jsonify(error=refused)
            response.status_code = 409
            return _no_store(response)
        return Response(status=202)

    @app.get("/api/update")
    def api_update():
        return _no_store(jsonify(ctx.update_status()))

    @app.post("/api/update")
    def api_update_start():
        """202: the update goes on after the reply, and `GET /api/update` tells how it is going until the
        installer quits the app."""
        refused = ctx.start_update()
        if refused:
            response = jsonify(error=refused)
            response.status_code = 409
            return _no_store(response)
        return Response(status=202)

    @app.post("/api/quit")
    def api_quit():
        body = request.get_json(silent=True)
        mode = body.get("mode") if isinstance(body, dict) else None
        if mode is not None and mode not in ("now", "after_render"):
            return Response(status=400)
        return _no_store(jsonify(result=ctx.quit(mode)))

    @app.get("/reels")
    def reels_page():
        return _no_store(Response((PAGES_DIR / "reels.html").read_bytes(), mimetype="text/html"))

    @app.get("/api/reels")
    def api_reels():
        index = Index(ctx.index_path)
        try:
            body = [reels_row(index, match) for match in index.reel_matches()]
        finally:
            index.close()
        return _no_store(jsonify(body))

    @app.get("/reels/<int:reel_id>.mp4")
    def reel_video(reel_id: int):
        index = Index(ctx.index_path)
        try:
            row = index.reel(reel_id)
        finally:
            index.close()
        video = None if row is None else ctx.load_settings().config.load_path(row["path"])
        if video is None or not video.is_file():
            return Response(status=404)
        return send_file(video, mimetype="video/mp4", conditional=True)

    @app.get("/reels/<int:reel_id>.jpg")
    def reel_frame(reel_id: int):
        index = Index(ctx.index_path)
        try:
            row = index.reel(reel_id)
        finally:
            index.close()
        if row is None:
            return Response(status=404)
        config = ctx.load_settings().config
        video = config.load_path(row["path"])
        if not video.is_file():
            return Response(status=404)
        frame = reel_frame_file(video, config.ffmpeg)
        if frame is None:
            return Response(status=404)
        return send_file(frame, mimetype="image/jpeg", conditional=True)

    @app.post("/api/reels/<checksum:checksum>/open-folder")
    def api_open_folder(checksum: str):
        """The folder passed to `ctx.open_folder` always comes from the index, never from the
        request body (spec: Pages, Reels)."""
        index = Index(ctx.index_path)
        try:
            reel_id = next(
                (h["player_reel_id"] or h["enemy_reel_id"] for h in index.match_reels(checksum)
                 if h["player_reel_id"] or h["enemy_reel_id"]),
                None,
            )
            row = index.reel(reel_id) if reel_id is not None else None
        finally:
            index.close()
        if row is None:
            return Response(status=404)
        folder = ctx.load_settings().config.load_path(row["path"]).parent
        try:
            ctx.open_folder(folder)
        except OSError as exc:            # the folder is gone
            log.warning("could not open %s: %s", folder, exc)
            return Response(status=404)
        return Response(status=204)

    @app.get("/settings")
    def settings_page():
        return _no_store(Response((PAGES_DIR / "settings.html").read_bytes(), mimetype="text/html"))

    @app.get("/api/settings")
    def api_get_settings():
        loaded = ctx.load_settings()
        body = {
            "fields": [field_dict(f) for f in FIELDS],
            "values": json_values(loaded.config),
            # One answer per secret: the API key and the client secret are saved and removed apart.
            "secrets_set": {name: bool(getattr(loaded.config, stored)) for name, stored in SECRETS.items()},
            "faceit_login_url": ctx.faceit_login_url(),
            "warnings": list(loaded.warnings),
            "port_in_use": ctx.port_in_use(),
        }
        return _no_store(jsonify(body))

    @app.put("/api/settings")
    def api_put_settings():
        changes = request.get_json(silent=True)
        if not isinstance(changes, dict):
            return Response(status=400)
        errors = ctx.save_settings(changes)
        if errors:
            response = jsonify(errors=errors)
            response.status_code = 400
            return _no_store(response)
        restart_needed = ctx.load_settings().config.page_port != ctx.port_in_use()
        return _no_store(jsonify(saved=True, restart_needed=restart_needed))

    @app.post("/api/faceit/session")
    def api_faceit_session():
        """The browser hands back the authorization code FACEIT put in the settings URL; the app
        exchanges it for who the user is (spec: Settings, Sign in with FACEIT)."""
        body = request.get_json(silent=True)
        code = body.get("code") if isinstance(body, dict) else None
        state = body.get("state") if isinstance(body, dict) else None
        if not isinstance(code, str) or not code:
            return Response(status=400)
        try:
            nickname = ctx.faceit_sign_in(code, state if isinstance(state, str) else "")
        except OAuthError as exc:
            response = jsonify(error=str(exc))
            response.status_code = 400
            return _no_store(response)
        return _no_store(jsonify(nickname=nickname))

    @app.post("/api/faceit/lookup")
    def api_faceit_lookup():
        """Turns a typed FACEIT nickname into the nickname and SteamID Match Alerts need, and saves
        both: nobody knows their SteamID64, and alerts verify the nickname against it."""
        body = request.get_json(silent=True)
        nickname = body.get("nickname") if isinstance(body, dict) else None
        if not isinstance(nickname, str) or not nickname.strip():
            return Response(status=400)
        try:
            found = ctx.faceit_lookup(nickname)
        except FaceitError as exc:
            response = jsonify(error=str(exc))
            response.status_code = 400
            return _no_store(response)
        return _no_store(jsonify(**found))

    @app.get("/demos")
    def demos_page():
        return _no_store(Response((PAGES_DIR / "demos.html").read_bytes(), mimetype="text/html"))

    @app.get("/demos.json")
    def demos_json():
        index = Index(ctx.index_path)
        try:
            body = {
                "this_pc": _client_address() in ctx.own_addresses(),
                "rows": page_rows(index, ctx.clock(), ctx.gate_reasons()),
            }
        finally:
            index.close()
        return _no_store(jsonify(body))

    @app.post("/demos/<match_id:match_id>/<action>")
    def demos_action(match_id: str, action: str):
        if action not in ("skip", "undo"):
            return Response(status=404)
        index = Index(ctx.index_path)
        try:
            changed = index.skip_match(match_id, ctx.clock()) if action == "skip" else index.undo_skip(match_id)
        finally:
            index.close()
        return Response(status=204 if changed else 409)

    @app.get("/app.css")
    def app_css():
        return Response((PAGES_DIR / "app.css").read_bytes(), mimetype="text/css")

    @app.get("/app.js")
    def app_js():
        return Response((PAGES_DIR / "app.js").read_bytes(), mimetype="text/javascript")

    @app.post("/api/window")
    def api_window():
        """PC-only (not a phone endpoint), marker required (the write guard above). A second start
        (Task 10's `hand_over`) uses this to ask the running copy to show a page."""
        body = request.get_json(silent=True)
        page = body.get("page") if isinstance(body, dict) else None
        if page not in PAGES:
            return Response(status=400)
        ctx.open_window(page)
        return Response(status=204)

    @app.get("/api/window")
    def api_window_request():
        """The latest request for a page. The page in the open window asks every 2 s (`app.js`), and
        switches when `seq` has grown since it loaded. PC-only, like everything not on the phone list."""
        return _no_store(jsonify(ctx.window_request()))

    return app


class WebServer:
    """waitress on a daemon thread, on the first free port from `port` on."""

    def __init__(self, app: Flask, port: int, *, host: str = "0.0.0.0", tries: int = PORTS_TO_TRY):
        candidates = (0,) if port == 0 else range(port, port + tries)
        server = None
        for candidate in candidates:
            try:
                server = create_server(app, host=host, port=candidate)
                break
            except OSError:
                continue
        if server is None:
            raise OSError(f"no free port in {port}–{port + tries - 1}")
        self._server = server
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._server.effective_port)   # waitress's getsockname() returns it as a numeric string

    def start(self) -> None:
        self._thread = threading.Thread(target=self._server.run, name="web", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Closes the socket (frees the port) even when `start()` was never called. Once the server
        is running, closing its socket from this thread while the "web" thread's `select()` is using
        it is a race (harmless, but throws inside that thread) -- so instead this asks the event loop
        to close itself, through waitress's own thread-safe wakeup (`trigger`), and waits for it."""
        if self._thread is None:
            self._server.close()
            return
        done = threading.Event()

        def _close_from_loop_thread() -> None:
            self._server.close()
            done.set()

        self._server.trigger.pull_trigger(_close_from_loop_thread)
        done.wait(timeout=5)
        self._thread.join(timeout=5)
