"""The Demos to grab page (spec: The Demos to grab page): a small HTTP server on a worker thread.

It answers only the page, its list, and Skip / Undo, and serves no files. Every request opens its
own index connection, because an SQLite connection belongs to the thread that made it."""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from clipper.alerts import utc_now
from clipper.index import Index
from clipper.web import IN_PIPELINE, KEEP_DECIDED, own_addresses, page_rows

log = logging.getLogger(__name__)

PAGE_HTML = Path(__file__).with_name("page.html")
ACTION = re.compile(r"^/demos/(1-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/(skip|undo)$")
PORTS_TO_TRY = 10


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
