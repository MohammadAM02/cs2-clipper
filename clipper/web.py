"""The Flask app: the four pages (Status, Demos to grab, Reels, Settings), their JSON endpoints, and the
reach and action guards (spec: Pages, Reach from other devices). Every route is PC-only except the Demos
to grab page, its list and its actions, the Reels page with what it reads (its list, each Reel's video, frame and
scrub strip: watch and download, nothing else), and the stylesheets and scripts those load; every POST, PUT and
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

import ipaddress
import json
import logging
import os
import re
import socket
import sqlite3
import subprocess
import threading
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

import psutil
from flask import Flask, Response, jsonify, redirect, request, send_file
from waitress.server import create_server
from werkzeug.routing import BaseConverter

from clipper import applog, links, share, stats_view
from clipper.alerts import LINK_LIFETIME, map_label, utc_now
from clipper.analysis import Analyses
from clipper.backfill import REPLAY_REQUESTED
from clipper.checks import Check
from clipper.config import Config
from clipper.faceit import FaceitError
from clipper.faceit_oauth import OAuthError
from clipper.footprint import Usage
from clipper.index import Index
from clipper.settings import FIELDS, SECRETS, Field, Loaded, json_values
from clipper.state import Snapshot, summary, view_name

log = logging.getLogger(__name__)

MARKER_HEADER = "X-CS2-Clipper"
PAGES_DIR = Path(__file__).with_name("pages")
PAGES = links.PAGES                            # what `/api/window` may ask the window to show (links.valid_page)
PORTS_TO_TRY = 10
KEEP_DECIDED = timedelta(hours=24)
IN_PIPELINE = ("spotted", "unpacked", "analyzed", "scored", "rendering", "joined")
NOT_LISTED = ("done", "skipped")               # Status's Demos list leaves these out (spec: Pages, Status)
PERSPECTIVES = ("player", "enemy")
TOUCHES = ("ui_sounds", "map_glow", "calm_mode", "keyboard_shortcuts")    # Settings -> Touches, for touches.js
WEEK = timedelta(days=7)                       # what the scoreboard (hold Tab) covers
SERVER_THREADS = 8                             # an export takes one for its seconds; the pages' polls need the rest
SECONDS_PER_HIGHLIGHT = 45.0                   # one view of one Highlight, CS2's start shared out, before any render
STRIP_FRAMES = 12                              # frames in a Reel's hover scrub strip; touches.js steps through as many
STRIP_FRAME = (320, 180)                       # each one's size: the size of the scrub (touches.css, .t-scrub)
# The scoreboard's Status column: a FACEIT match by its own state, a grabbed one by its Demo's.
_MATCH_STATUS = {"ready": "To grab", "announced": "To grab", "waiting": "Waiting for FACEIT", "skipped": "Skipped",
                 "expired": "Link expired", "no_highlights": "No Highlights"}
_DEMO_STATUS = {"spotted": "Spotted", "unpacked": "Unpacked", "analyzed": "Analyzed", "scored": "Scored",
                "rendering": "Rendering", "joined": "Joined", "done": "Done", "failed": "Failed", "skipped": "Skipped"}

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_MATCH_ID_PATTERN = r"1-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
_CHECKSUM_PATTERN = r"[0-9a-f]{1,16}"         # as csda writes it: hex without its leading zeros
# Flask endpoint names (not paths) reachable from another device (spec: Reach from other devices).
_PHONE_ENDPOINTS = frozenset({"demos_page", "demos_json", "demos_action", "app_css", "app_js", "touches_css",
                              "touches_js", "nugget_js", "font"})
# Reels to watch and download from another device too -- reading only, and only under a Host a DNS-rebinding page
# cannot send (`_plain_host`), since what they show is the player's own clips.
_WATCH_ENDPOINTS = frozenset({"reels_page", "api_reels", "reel_video", "reel_frame", "reel_strip"})
# The fonts the pages load from /fonts/ (their licences sit next to them and are not served).
_FONTS = frozenset({"Bangers.woff2", "Barlow-Regular.woff2", "Barlow-Medium.woff2", "Barlow-SemiBold.woff2",
                    "Barlow-Bold.woff2", "BarlowCondensed-SemiBold.woff2", "BarlowCondensed-Bold.woff2",
                    "BarlowCondensed-ExtraBold.woff2"})


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
    rows, opened = [], index.opened_matches()
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
            "opened": row["match_id"] in opened,       # its room was opened: the page waits for the download
        })
    return rows


def seconds_per_highlight(index: Index) -> float:
    """How long a view of one Highlight takes to record, CS2's start shared out: from the last renders, or
    SECONDS_PER_HIGHLIGHT before there are any."""
    past = index.render_seconds()
    if not past:
        return SECONDS_PER_HIGHLIGHT
    return sum(seconds for seconds, _ in past) / sum(highlights for _, highlights in past)


def status_demos(index: Index, now: datetime | None = None, running_left: float | None = None) -> list[dict]:
    """Status's Demos list: every Demo not done or skipped, newest first, with the latest Render Job
    per Perspective (none before rendering has been queued) and, once its Highlights are scored, about how long
    until its Reels are made (`eta_s`): each view still to record, at the pace the last renders went -- or, for the
    view recording now, what the render itself says is left (`running_left`), when it says."""
    now = now or utc_now()
    rows = [row for row in index.all_demos() if row["state"] not in NOT_LISTED]
    rows.reverse()
    demos, pace = [], None
    for row in rows:
        match = index.match(row["match_checksum"]) if row["match_checksum"] else None
        jobs = []
        for perspective in PERSPECTIVES:
            job = index.latest_render(row["id"], perspective)
            if job is not None:
                jobs.append({"perspective": perspective, "attempt": job["attempt"], "state": job["state"],
                            "failure": job["failure"], "started_at": job["started_at"]})
        eta = None
        if row["state"] in ("scored", "rendering") and match is not None:
            pace = pace if pace is not None else seconds_per_highlight(index)
            count = len(index.selected_highlights(row["match_checksum"]))
            left = [job for job in jobs if job["state"] != "done"] or ([] if jobs else [None])
            eta = 0.0
            for job in left:
                view = count * pace
                if job is not None and job["state"] == "running" and running_left is not None:
                    view = running_left
                elif job is not None and job["state"] == "running" and job["started_at"]:
                    view -= (now - datetime.fromisoformat(job["started_at"])).total_seconds()
                eta += max(view, 30.0)
        demos.append({
            "id": row["id"], "file_name": row["file_name"], "state": row["state"],
            "map": map_label(match["map"]) if match is not None else None,
            "error": row["last_error"], "retry": row["state"] == "failed",
            "jobs": [{key: value for key, value in job.items() if key != "started_at"} for job in jobs],
            "eta_s": None if eta is None else round(eta),
        })
    return demos


def week_rows(index: Index, now: datetime) -> list[dict]:
    """The scoreboard every page shows while Tab is held: each match of the last 7 days, newest first. A FACEIT
    match brings its K–D and its rounds of 3+ Frags, and once grabbed shows its Demo's state; a Demo whose match
    FACEIT never told the app about (Match alerts off, a Demo dropped in by hand) is a row of its own, with the
    Highlights picked from it and no K–D."""
    since = now - WEEK
    rows, linked = [], set()
    for f in index.faceit_matches_since(since):
        if not f["map"]:                          # FACEIT has not sent its stats yet
            continue
        if f["demo_checksum"]:
            linked.add(f["demo_checksum"])
        counts = json.loads(f["highlights"])
        status = (_DEMO_STATUS.get(f["demo_state"], "Grabbed") if f["state"] == "grabbed"
                  else _MATCH_STATUS.get(f["state"], f["state"]))
        rows.append({"map": map_label(f["map"]), "played_at": f["finished_at"],
                     "score": f"{f['team_score']}–{f['opponent_score']}", "won": bool(f["won"]),
                     "kd": f"{f['kills']}–{f['deaths']}", "highlights": sum(counts.values()),
                     "ace": counts.get("5k", 0) > 0, "status": status, "tone": _tone(status),
                     "match_id": f["match_id"], "demo_id": f["demo_id"]})
    for m in index.matches_played_since(since):
        if m["checksum"] in linked:
            continue
        status = _DEMO_STATUS.get(m["demo_state"], "")
        rows.append({"map": map_label(m["map"]), "played_at": m["played_at"],
                     "score": f"{m['team_score']}–{m['opponent_score']}", "won": m["result"] == "win", "kd": None,
                     "highlights": m["picked"], "ace": m["aces"] > 0, "status": status, "tone": _tone(status),
                     "match_id": None, "demo_id": m["demo_id"]})
    rows.sort(key=lambda row: datetime.fromisoformat(row["played_at"]), reverse=True)
    return rows


def _tone(status: str) -> str:
    """How the scoreboard colours a Status: done, out of the way, or plain."""
    return "done" if status == "Done" else "off" if status in ("Skipped", "Link expired") else ""


def field_dict(f: Field) -> dict:
    """One `FIELDS` entry as JSON for the Settings page (spec: Pages, Settings)."""
    return {"name": f.name, "group": f.group, "label": f.label, "kind": f.kind,
            "minimum": f.minimum, "maximum": f.maximum, "choices": list(f.choices), "help": f.help,
            "advanced": f.advanced}


def _render_options(index: Index, config: Config, checksum: str) -> tuple[list[str], dict[str, str | None]]:
    """What the Reels page can ask of `checksum`'s Demo: the views it is rendering now (asked for and not done), and
    for each Perspective why it cannot be asked for now in plain words, or None when it can. A view can be asked for
    when the Demo is done with its download kept, the match has Highlights and no Reel of that view, and the view is
    not already rendering; Round mode has no Enemy POV (spec: Pages, Reels)."""
    demo = index.demo_of_match(checksum)
    if demo is None:
        return [], {perspective: "This match has no Demo." for perspective in PERSPECTIVES}
    asked = index.asked_renders(demo["id"])
    has_highlights = bool(index.selected_highlights(checksum))
    reels = index.match_reels(checksum)
    refusals: dict[str, str | None] = {}
    for perspective in PERSPECTIVES:
        name = view_name(perspective)
        if not has_highlights:
            refusal = "This match has no Highlights to render."
        elif any(row[f"{perspective}_reel_id"] is not None for row in reels):
            refusal = f"This match already has {name}."
        elif perspective in asked:
            refusal = f"Already rendering {name}."
        elif perspective == "enemy" and config.sequence_event == "rounds":
            refusal = "Round mode has no Enemy POV."
        elif demo["state"] != "done":
            refusal = "The Demo is not done yet."
        elif not Path(demo["archive_path"]).is_file():
            refusal = "The Demo's download is gone, so it cannot be rendered again."
        else:
            refusal = None
        refusals[perspective] = refusal
    return list(asked), refusals


def reels_row(index: Index, match: sqlite3.Row, config: Config) -> dict:
    """One entry of `/api/reels`: `match`'s Reels, in round order, its Breaks' Reels (your view only), and the views
    the Reels page can render for it: `rendering` (asked for, not done yet) and `can_render` (see `_render_options`)
    (spec: Pages, Reels)."""
    highlights = [
        {"round": h["round"], "type": h["type"], "reasons": json.loads(h["reasons"]),
         "player": h["player_reel_id"], "enemy": h["enemy_reel_id"],
         "player_s": h["player_s"], "enemy_s": h["enemy_s"]}             # how long each runs, for the hover scrub
        for h in index.match_reels(match["checksum"])
    ]
    rendering, refusals = _render_options(index, config, match["checksum"])
    return {
        "checksum": match["checksum"], "map": map_label(match["map"]), "played_at": match["played_at"],
        "score": f"{match['team_score']}–{match['opponent_score']}", "result": match["result"],
        "highlights": highlights,
        "breaks": [{"label": b["label"], "after_round": b["after_round"], "reel": b["reel_id"], "secs": b["duration_s"]}
                   for b in index.match_breaks(match["checksum"])],
        "rendering": rendering,
        "can_render": [perspective for perspective in PERSPECTIVES if refusals[perspective] is None],
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


def reel_strip_file(video: Path, ffmpeg: str, seconds: float) -> Path | None:
    """The Reels page's hover scrub for `video`: STRIP_FRAMES frames from across its `seconds`, side by side in one
    JPEG, cut on the first request and kept beside it like its frame; None when ffmpeg cannot cut one. Each frame
    is an input of its own, seeked to its time, so ffmpeg decodes a few frames per input, never the whole Reel; a
    frame that is not 16:9 is shown whole, with bars."""
    strip = video.with_suffix(".strip.jpg")
    if strip.is_file() and strip.stat().st_mtime >= video.stat().st_mtime:
        return strip
    width, height = STRIP_FRAME
    inputs, frames = [], []
    for i in range(STRIP_FRAMES):
        inputs += ["-ss", f"{seconds * (i + 0.5) / STRIP_FRAMES:.3f}", "-i", str(video)]
        frames.append(f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                      f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1[f{i}]")
    graph = ";".join(frames) + ";" + "".join(f"[f{i}]" for i in range(STRIP_FRAMES)) + f"hstack=inputs={STRIP_FRAMES}"
    part = video.with_name(f"{video.stem}.{threading.get_ident()}.strip.part.jpg")
    try:
        subprocess.run([ffmpeg, "-y", *inputs, "-filter_complex", graph, "-frames:v", "1", "-q:v", "5", str(part)],
                       capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        os.replace(part, strip)      # no `part` at all when ffmpeg cut nothing
    except (OSError, subprocess.SubprocessError):
        part.unlink(missing_ok=True)
        return strip if strip.is_file() else None
    return strip


def _faceit_not_set_up(token: str, state: str) -> str:
    raise OAuthError("FACEIT sign-in is not set up")


def _faceit_lookup_not_set_up(nickname: str) -> dict:
    raise FaceitError("FACEIT lookup is not set up")


def _nothing_to_set_up() -> dict:
    return {"running": False, "needed": False, "steps": [], "download_bytes": 0, "progress": None, "error": None}


def _no_exports(*args, **kwargs) -> Path:
    raise share.ShareError("exports are not set up")


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
    faceit_lookup: Callable[[str], dict] = _faceit_lookup_not_set_up      # finds the player and saves them
    faceit_find: Callable[[str], dict] = _faceit_lookup_not_set_up        # finds the player only
    setup_status: Callable[[], dict] = _nothing_to_set_up        # provision.Setup.status
    start_setup: Callable[[], str | None] = lambda: None         # why it was not started, or None
    update_status: Callable[[], dict] = _no_update               # update.Updates.status
    start_update: Callable[[], str | None] = lambda: None        # why it was not started, or None
    delete_demo: Callable[[int], str | None] = lambda demo_id: None   # "deleted", "deferred" or None: no such Demo
    check_faceit: Callable[[], str | None] = lambda: "Match alerts are off"   # why not, or None: they look soon
    follow_link: Callable[[links.Link], None] = lambda link: None          # a toast button's cs2clipper: link
    footprint: Callable[[], Usage | None] = lambda: None                   # the app's own memory and CPU
    onboarding: Callable[[], dict] = lambda: {"done": True}                # the first-run screen (App.onboarding)
    home: Callable[[], str] = lambda: "/status"                            # the window's first page (App.home)
    copy_file: Callable[[Path], None] = lambda path: None                  # a file onto the clipboard (share)
    export_reel: Callable[..., Path] = _no_exports                         # share.export
    reveal: Callable[[Path], None] = lambda path: None                     # a file, selected, in Explorer


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


def _plain_host() -> bool:
    """Whether the request's `Host` is one a page on another site cannot make a browser send by rebinding its own
    name to this PC: an IP address, or this PC's own names (localhost, its computer name, its .local and Tailscale
    names). The phone reaches the app by one of those."""
    host = (request.headers.get("Host") or "").lower()
    name = host[1:host.index("]")] if host.startswith("[") else host.rsplit(":", 1)[0]
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    me = socket.gethostname().lower()
    return name in ("localhost", me, f"{me}.local") or (name.startswith(f"{me}.") and name.endswith(".ts.net"))


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
        if request.endpoint in _WATCH_ENDPOINTS and request.method == "GET" and _plain_host():
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
            progress = snapshot.rendering.progress
            rendering = {"map": map_label(snapshot.rendering.map_name),
                         "perspective": snapshot.rendering.perspective, "started_at": snapshot.rendering.started_at,
                         "progress": None if progress is None else asdict(progress)}
        progress = snapshot.rendering.progress if snapshot.rendering is not None else None
        index = Index(ctx.index_path)
        try:
            demos = status_demos(index, running_left=None if progress is None else progress.seconds_left)
        finally:
            index.close()
        try:
            checks = [{"name": c.name, "ok": c.ok, "detail": c.detail, "hint": c.hint} for c in ctx.checks()]
        except Exception as exc:  # noqa: BLE001 - a check that raises must not blank the whole page
            log.exception("could not run the checks")
            checks = [{"name": "Checks", "ok": False, "detail": f"Couldn't run the checks: {exc}", "hint": ""}]
        usage = ctx.footprint()
        body = {
            "summary": summary(snapshot),
            "footprint": None if usage is None else {"memory_mb": usage.memory_mb, "cpu_percent": usage.cpu_percent},
            "onboarding": ctx.onboarding(),
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
            map=map_label(snapshot.rendering.map_name) if snapshot.rendering is not None else None,   # the glow's
            paused_by=snapshot.paused_by,
            problems=list(snapshot.problems),
        ))

    @app.get("/api/touches")
    def api_touches():
        """Settings' Touches switches, which every page's touches.js asks for. Not `/api/settings`: that one mints a
        new FACEIT sign-in state each time it is asked."""
        config = ctx.load_settings().config
        return _no_store(jsonify({name: getattr(config, name) for name in TOUCHES}))

    @app.get("/api/week")
    def api_week():
        """The scoreboard every page shows while Tab is held (touches.js), with its totals."""
        index = Index(ctx.index_path)
        try:
            rows = week_rows(index, ctx.clock())
        finally:
            index.close()
        return _no_store(jsonify(rows=rows, matches=len(rows), highlights=sum(row["highlights"] for row in rows),
                                 aces=sum(row["ace"] for row in rows)))

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

    @app.get("/stats")
    def stats_page():
        return _no_store(Response((PAGES_DIR / "stats.html").read_bytes(), mimetype="text/html"))

    @app.get("/api/stats/<checksum:checksum>")
    def api_stats(checksum: str):
        config = ctx.load_settings().config
        index = Index(ctx.index_path)
        try:
            body = stats_view.match_view(index, Analyses(config.analyses_dir, config.csda_exe),
                                         config.subject_steamid, checksum)
        finally:
            index.close()
        if body is None:
            return _no_store(jsonify(error="no such match")), 404
        return _no_store(jsonify(body))

    @app.get("/replay")
    def replay_page():
        return _no_store(Response((PAGES_DIR / "replay.html").read_bytes(), mimetype="text/html"))

    @app.get("/api/replay/<checksum:checksum>")
    def api_replay(checksum: str):
        config = ctx.load_settings().config
        index = Index(ctx.index_path)
        try:
            requested = index.get_flag(REPLAY_REQUESTED + checksum) is not None
            body = stats_view.replay_view(index, Analyses(config.analyses_dir, config.csda_exe),
                                          config.subject_steamid, config.radar_dir, checksum, requested)
        finally:
            index.close()
        if body is None:
            return _no_store(jsonify(error="no such match")), 404
        return _no_store(jsonify(body))

    @app.post("/api/replay/<checksum:checksum>")
    def api_request_replay(checksum: str):
        index = Index(ctx.index_path)
        try:
            if index.match(checksum) is None:
                return _no_store(jsonify(error="no such match")), 404
            index.set_flag(REPLAY_REQUESTED + checksum, utc_now().isoformat())
        finally:
            index.close()
        return _no_store(jsonify(requested=True)), 202

    @app.get("/radar/<name>")
    def radar_image(name: str):
        config = ctx.load_settings().config
        if not re.fullmatch(r"[a-z0-9_]{2,40}[.]png", name):
            return _no_store(jsonify(error="not a radar")), 404
        path = config.radar_dir / name
        if not path.is_file():
            return _no_store(jsonify(error="no radar yet")), 404
        return send_file(path, mimetype="image/png", conditional=True)

    @app.get("/reels")
    def reels_page():
        return _no_store(Response((PAGES_DIR / "reels.html").read_bytes(), mimetype="text/html"))

    @app.get("/api/reels")
    def api_reels():
        config = ctx.load_settings().config
        index = Index(ctx.index_path)
        try:
            body = [reels_row(index, match, config) for match in index.reel_matches()]
        finally:
            index.close()
        return _no_store(jsonify(body))

    @app.get("/api/reels/volume")
    def api_reels_volume():
        """Where the Reels player's volume slider was last left (0 to 1), or None before it has been moved: kept here,
        since the window forgets what a page stores each time it closes. The page has its own quieter start."""
        index = Index(ctx.index_path)
        try:
            stored = index.get_flag("reel_volume")
        finally:
            index.close()
        try:
            volume = float(stored) if stored is not None else None
        except ValueError:
            volume = None
        if volume is not None and not 0 <= volume <= 1:       # NaN too
            volume = None
        return _no_store(jsonify(volume=volume))

    @app.put("/api/reels/volume")
    def api_reels_volume_save():
        body = request.get_json(silent=True)
        volume = body.get("volume") if isinstance(body, dict) else None
        if isinstance(volume, bool) or not isinstance(volume, (int, float)) or not 0 <= volume <= 1:
            return Response(status=400)
        index = Index(ctx.index_path)
        try:
            index.set_flag("reel_volume", repr(float(volume)))
        finally:
            index.close()
        return Response(status=204)

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
        download = request.args.get("download") == "1"        # a phone's Download: saved under its readable name
        return send_file(video, mimetype="video/mp4", conditional=True, as_attachment=download,
                         download_name=video.name if download else None)

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

    @app.get("/reels/<int:reel_id>.strip.jpg")
    def reel_strip(reel_id: int):
        """The frames the Reels page flicks through while the pointer moves across a Highlight (touches.js)."""
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
        strip = reel_strip_file(video, config.ffmpeg, row["duration_s"])
        if strip is None:
            return Response(status=404)
        return send_file(strip, mimetype="image/jpeg", conditional=True)

    def _reel_file(reel_id: int):
        """A Reel's details (share's name and title card come from them) and its file, or None when either is gone."""
        index = Index(ctx.index_path)
        try:
            reel = index.reel_details(reel_id)
        finally:
            index.close()
        config = ctx.load_settings().config
        if reel is None or config.data_root is None:
            return None
        video = config.load_path(reel["path"])
        return (reel, video) if video.is_file() else None

    def _share_refused(message: str) -> Response:
        response = jsonify(error=message)
        response.status_code = 409
        return _no_store(response)

    @app.post("/api/reels/<int:reel_id>/copy")
    def api_reel_copy(reel_id: int):
        """Puts the Reel's file on the clipboard, as Explorer's Copy does: pasted into Discord, it uploads."""
        found = _reel_file(reel_id)
        if found is None:
            return Response(status=404)
        try:
            ctx.copy_file(found[1])
        except share.ShareError as exc:
            return _share_refused(f"Couldn't copy it: {exc}")
        return Response(status=204)

    @app.post("/api/reels/<int:reel_id>/reveal")
    def api_reel_reveal(reel_id: int):
        """Shows the Reel's file, selected, in Explorer."""
        found = _reel_file(reel_id)
        if found is None:
            return Response(status=404)
        ctx.reveal(found[1])
        return Response(status=204)

    @app.post("/api/reels/<int:reel_id>/export")
    def api_reel_export(reel_id: int):
        """Makes an export of the Reel in the clips folder's Exports folder, opening on its title card when Settings
        has one: "discord" fits Discord's free upload limit and goes on the clipboard, ready to paste; "vertical" is
        9:16 for TikTok, Shorts and Instagram Reels, and shows in Explorer. 200 with the file's name and size."""
        body = request.get_json(silent=True)
        kind = body.get("kind") if isinstance(body, dict) else None
        if kind not in share.EXPORTS:
            return Response(status=400)
        found = _reel_file(reel_id)
        if found is None:
            return Response(status=404)
        reel, video = found
        config = ctx.load_settings().config
        name = share.reel_name(reel["type"], reel["map"], reel["round"], config.faceit_nickname, reel["perspective"])
        card = share.title(reel["type"], reel["map"], reel["round"], config.faceit_nickname) if config.title_card else None
        out = share.export_path(config.data_root / "Exports", name, kind)
        try:
            out = ctx.export_reel(video, kind, out, duration_s=reel["duration_s"], ffmpeg=config.ffmpeg, card=card)
        except share.ShareError as exc:
            return _share_refused(f"Couldn't export it: {exc}")
        copied = False
        if kind == "discord":
            try:
                ctx.copy_file(out)
                copied = True
            except share.ShareError as exc:
                log.warning("the Discord export of Reel %s is made but not on the clipboard: %s", reel_id, exc)
        else:
            ctx.reveal(out)
        return _no_store(jsonify(file=out.name, mb=round(out.stat().st_size / 1e6, 1), copied=copied))

    @app.post("/api/reels/open-folder")
    def api_open_reels_folder():
        """The folder every match's Reels are in (the quick wheel, the console's `open reels`); 404 until a Reel
        has made it."""
        config = ctx.load_settings().config
        folder = config.library_dir / "videos" if config.data_root is not None else None
        if folder is None or not folder.is_dir():
            return Response(status=404)
        try:
            ctx.open_folder(folder)
        except OSError as exc:
            log.warning("could not open %s: %s", folder, exc)
            return Response(status=404)
        return Response(status=204)

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

    @app.post("/api/reels/<checksum:checksum>/render")
    def api_reels_render(checksum: str):
        """202 once the match's Demo is asked to render `perspective`: it goes back in the queue for that view alone,
        using its kept download. 409 says in plain words why it cannot (spec: Pages, Reels)."""
        body = request.get_json(silent=True)
        perspective = body.get("perspective") if isinstance(body, dict) else None
        if perspective not in PERSPECTIVES:
            response = jsonify(error='perspective must be "player" or "enemy"')
            response.status_code = 400
            return _no_store(response)
        config = ctx.load_settings().config
        index = Index(ctx.index_path)
        try:
            demo = index.demo_of_match(checksum)
            if demo is None:
                return Response(status=404)
            _, refusals = _render_options(index, config, checksum)
            if refusals[perspective] is not None:
                response = jsonify(error=refusals[perspective])
                response.status_code = 409
                return _no_store(response)
            try:
                index.ask_render(demo["id"], perspective)
            except ValueError as exc:     # the Demo changed since the check above; `ask_render` checked again
                response = jsonify(error=str(exc))
                response.status_code = 409
                return _no_store(response)
        finally:
            index.close()
        response = jsonify(queued=perspective)
        response.status_code = 202
        return _no_store(response)

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
        """Turns a typed FACEIT nickname into the nickname and SteamID Match Alerts need: nobody knows their
        SteamID64, and alerts verify the nickname against it. Saves both, unless the body says ``"save": false``
        (Settings, where Save keeps them with everything else)."""
        body = request.get_json(silent=True)
        nickname = body.get("nickname") if isinstance(body, dict) else None
        if not isinstance(nickname, str) or not nickname.strip():
            return Response(status=400)
        save = body.get("save", True) is not False
        try:
            found = (ctx.faceit_lookup if save else ctx.faceit_find)(nickname)
        except FaceitError as exc:
            response = jsonify(error=str(exc))
            response.status_code = 400
            return _no_store(response)
        return _no_store(jsonify(**found))

    @app.post("/api/faceit/check")
    def api_faceit_check():
        """The quick wheel's Check for demos. 202: Match Alerts look for new FACEIT matches on the worker's next tick
        (once CS2 has closed). 409 says why they cannot."""
        refused = ctx.check_faceit()
        if refused:
            response = jsonify(error=refused)
            response.status_code = 409
            return _no_store(response)
        return Response(status=202)

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
        """Skip, Undo, and Opened (its room was opened to download the Demo, kept here rather than in one window)."""
        if action not in ("skip", "undo", "opened"):
            return Response(status=404)
        index = Index(ctx.index_path)
        try:
            if action == "skip":
                changed = index.skip_match(match_id, ctx.clock())
            elif action == "undo":
                changed = index.undo_skip(match_id)
            else:
                changed = index.mark_opened(match_id, ctx.clock())
        finally:
            index.close()
        return Response(status=204 if changed else 409)

    @app.get("/app.css")
    def app_css():
        return Response((PAGES_DIR / "app.css").read_bytes(), mimetype="text/css")

    @app.get("/app.js")
    def app_js():
        return Response((PAGES_DIR / "app.js").read_bytes(), mimetype="text/javascript")

    @app.get("/touches.css")
    def touches_css():
        return Response((PAGES_DIR / "touches.css").read_bytes(), mimetype="text/css")

    @app.get("/touches.js")
    def touches_js():
        return Response((PAGES_DIR / "touches.js").read_bytes(), mimetype="text/javascript")

    @app.get("/nugget.js")
    def nugget_js():
        return Response((PAGES_DIR / "nugget.js").read_bytes(), mimetype="text/javascript")

    @app.get("/fonts/<name>")
    def font(name: str):
        if name not in _FONTS:
            return Response(status=404)
        return Response((PAGES_DIR / "fonts" / name).read_bytes(), mimetype="font/woff2")

    @app.post("/api/window")
    def api_window():
        """PC-only (not a phone endpoint), marker required (the write guard above). A second start
        (Task 10's `hand_over`) uses this to ask the running copy to show a page."""
        body = request.get_json(silent=True)
        page = body.get("page") if isinstance(body, dict) else None
        if page == "home":                    # a second start with no page asked: the running copy's home page
            page = ctx.home()
        if not links.valid_page(page):
            return Response(status=400)
        ctx.open_window(page)
        return Response(status=204)

    @app.post("/api/link")
    def api_link():
        """PC-only, marker required. A second start (`app.open_link`) passes on the cs2clipper: link a toast's
        button opened; only the forms `links.parse` reads are followed."""
        body = request.get_json(silent=True)
        link = links.parse(body.get("url") if isinstance(body, dict) else None)
        if link is None:
            return Response(status=400)
        ctx.follow_link(link)
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
                server = create_server(app, host=host, port=candidate, threads=SERVER_THREADS)
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
