"""The Flask app: pages, JSON endpoints, and the reach and action guards (spec: Pages, Reach from
other devices). Takes over the Demos to grab page from `page.py` (Task 9 removes it once `cli.py`
switches over); later tasks add the Status, Reels and Settings pages to `create_app`, with new
`WebContext` fields that default so this task's callers need no change.

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
import re
import socket
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import psutil
from flask import Flask, Response, jsonify, redirect, request
from waitress.server import create_server
from werkzeug.routing import BaseConverter

from clipper import applog
from clipper.alerts import LINK_LIFETIME, map_label, utc_now
from clipper.checks import Check
from clipper.index import Index
from clipper.state import Snapshot, summary

MARKER_HEADER = "X-CS2-Clipper"
PAGES_DIR = Path(__file__).with_name("pages")
PORTS_TO_TRY = 10
KEEP_DECIDED = timedelta(hours=24)
IN_PIPELINE = ("spotted", "unpacked", "analyzed", "scored", "rendering", "joined")
NOT_LISTED = ("done", "skipped")               # Status's Demos list leaves these out (spec: Pages, Status)
PERSPECTIVES = ("player", "enemy")

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_MATCH_ID_PATTERN = r"1-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
# Flask endpoint names (not paths) reachable from another device (spec: Reach from other devices).
_PHONE_ENDPOINTS = frozenset({"demos_page", "demos_json", "demos_action", "app_css", "app_js"})
# Kept in step with app.PAGES by value; app.py can't be imported here without a cycle (it imports web.py).
_WINDOW_PAGES = ("/status", "/demos", "/reels", "/settings")


class _MatchIdConverter(BaseConverter):
    """Only a real match id is a route at all; anything else (``..``, a stray file name) is a plain
    404 regardless of method, the way today's regex-matched `page.py` never routes it anywhere."""

    regex = _MATCH_ID_PATTERN


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


@dataclass
class WebContext:
    """What the pages need from the app. Later tasks add fields here, each with a default, for the
    Status, Reels and Settings pages."""

    index_path: Path
    gate_reasons: Callable[[], tuple[str, ...]]
    own_addresses: Callable[[], set[str]] = own_addresses
    clock: Callable[[], datetime] = utc_now
    open_window: Callable[[str], None] = lambda page: None
    snapshot: Callable[[], Snapshot] = Snapshot
    warnings: Callable[[], tuple[str, ...]] = tuple
    checks: Callable[[], list[Check]] = list
    quit: Callable[[str | None], str] = lambda mode: "now"
    resume: Callable[[], None] = lambda: None
    log_lines: Callable[[int], list[str]] = applog.recent


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
        body = {
            "summary": summary(snapshot),
            "rendering": rendering,
            "paused_by": snapshot.paused_by,
            "quitting": snapshot.quitting,
            "problems": list(snapshot.problems),
            "warnings": list(ctx.warnings()),
            "pages_off": snapshot.pages_off,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail, "hint": c.hint} for c in ctx.checks()],
            "demos": demos,
        }
        return _no_store(jsonify(body))

    @app.get("/api/log")
    def api_log():
        return _no_store(jsonify(lines=ctx.log_lines(200)))

    @app.post("/api/demos/<int:demo_id>/retry")
    def api_demo_retry(demo_id: int):
        index = Index(ctx.index_path)
        try:
            demo = index.demo(demo_id)
            if demo is None:
                return Response(status=404)
            if demo["state"] != "failed":
                response = jsonify(error=f"demo {demo_id} has not failed")
                response.status_code = 409
                return _no_store(response)
            state = index.retry(demo_id)
        finally:
            index.close()
        return _no_store(jsonify(state=state))

    @app.post("/api/resume")
    def api_resume():
        ctx.resume()
        return Response(status=204)

    @app.post("/api/quit")
    def api_quit():
        body = request.get_json(silent=True)
        mode = body.get("mode") if isinstance(body, dict) else None
        if mode is not None and mode not in ("now", "after_render"):
            return Response(status=400)
        return _no_store(jsonify(result=ctx.quit(mode)))

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
        if page not in _WINDOW_PAGES:
            return Response(status=400)
        ctx.open_window(page)
        return Response(status=204)

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
