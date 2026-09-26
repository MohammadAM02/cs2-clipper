"""The Demos to grab page (spec: The Demos to grab page): a small HTTP server on a worker thread.

It answers only the page, its list, and Skip / Undo, and serves no files. Every request opens its
own index connection, because an SQLite connection belongs to the thread that made it."""

from __future__ import annotations

import json
import logging
import re
import socket
import threading
from collections.abc import Callable
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import psutil

from clipper.alerts import LINK_LIFETIME, map_label, utc_now
from clipper.index import Index

log = logging.getLogger(__name__)

PAGE_HTML = Path(__file__).with_name("page.html")
ACTION = re.compile(r"^/demos/(1-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/(skip|undo)$")
KEEP_DECIDED = timedelta(hours=24)
PORTS_TO_TRY = 10
IN_PIPELINE = ("spotted", "unpacked", "analyzed", "scored", "rendering", "joined")


def own_addresses() -> set[str]:
    """Every address of this PC: loopback, LAN and Tailscale alike."""
    found = {"127.0.0.1", "::1"}
    for entries in psutil.net_if_addrs().values():
        for entry in entries:
            if entry.family in (socket.AF_INET, socket.AF_INET6):
                found.add(entry.address.split("%")[0])
    return found


def page_rows(index: Index, now: datetime, waiting_for: tuple[str, ...]) -> list[dict]:
    """The page's list, newest first. `waiting_for` is the Gate's reasons, shown on grabbed Demos that
    have not been rendered yet."""
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


class PageServer:
    def __init__(self, index_path: Path, port: int, gate_reasons: Callable[[], tuple[str, ...]], *,
                 host: str = "0.0.0.0", own: Callable[[], set[str]] = own_addresses,
                 clock: Callable[[], datetime] = utc_now):
        self._index_path = index_path
        self._gate_reasons = gate_reasons
        self._own = own
        self._clock = clock
        self._server = self._bind(host, port)

    @property
    def port(self) -> int:
        return self._server.server_address[1]

    def start(self) -> None:
        threading.Thread(target=self._server.serve_forever, name="demos-page", daemon=True).start()
        log.info("the Demos to grab page is on port %s", self.port)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def _bind(self, host: str, port: int) -> ThreadingHTTPServer:
        handler = self._handler()
        for candidate in range(port, port + PORTS_TO_TRY) if port else (0,):
            try:
                return ThreadingHTTPServer((host, candidate), handler)
            except OSError:
                log.info("port %s is taken; trying the next one", candidate)
        raise OSError(f"no free port in {port}–{port + PORTS_TO_TRY - 1}")

    def _list(self, client: str) -> dict:
        index = Index(self._index_path)
        try:
            rows = page_rows(index, self._clock(), self._gate_reasons())
        finally:
            index.close()
        return {"this_pc": client.split("%")[0].removeprefix("::ffff:") in self._own(), "rows": rows}

    def _decide(self, match_id: str, action: str) -> bool:
        index = Index(self._index_path)
        try:
            return index.skip_match(match_id, self._clock()) if action == "skip" else index.undo_skip(match_id)
        finally:
            index.close()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        page = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:   # pythonw has no stderr
                log.debug("page: " + format, *args)

            def do_GET(self) -> None:
                if self.path == "/demos":
                    self._send(200, "text/html; charset=utf-8", PAGE_HTML.read_bytes())
                elif self.path == "/demos.json":
                    body = json.dumps(page._list(self.client_address[0])).encode("utf-8")
                    self._send(200, "application/json", body)
                else:
                    self._send(404, "text/plain; charset=utf-8", b"not found")

            def do_POST(self) -> None:
                found = ACTION.match(self.path)
                if found is None:
                    self._send(404, "text/plain; charset=utf-8", b"not found")
                    return
                changed = page._decide(*found.groups())
                self._send(204 if changed else 409, "text/plain; charset=utf-8", b"")

            def _send(self, status: int, content_type: str, body: bytes) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

        return Handler
