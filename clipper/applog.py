"""The app log: a rotating clipper.log, plus the ring buffer the Status page's Log shows (spec:
Pages, Status: Log).

Adapted from thelifeofsuleyman/cs2-clipper's `aegis/log.py` (MIT). Aegis's `log()` prints and appends
to its own ring buffer directly, bypassing Python's logging; here the ring is a `logging.Handler`, so
every module's existing `logging.getLogger(__name__)` calls reach it, alongside a rotating file
handler and (when there is a console) a stream handler, all wired onto the root logger by `setup()`.

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
import sys
import threading
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

LINES_KEPT = 500
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

_buffer: deque[str] = deque(maxlen=LINES_KEPT)
_lock = threading.Lock()
_added: list[logging.Handler] = []          # the handlers our own setup() last added to the root logger


class _RingHandler(logging.Handler):
    """Appends every formatted record it handles to the module-level ring that `recent()` reads."""

    def emit(self, record: logging.LogRecord) -> None:
        line = self.format(record)
        with _lock:
            _buffer.append(line)


def setup(logs_dir: Path) -> list[logging.Handler]:
    """Root logger at INFO: a rotating `clipper.log`, the ring buffer, and a console handler when one
    exists (a windowed app run with pythonw has no `sys.stderr`). Safe to call again — first removes
    and closes only the handlers an earlier `setup()` call added, leaving anyone else's alone (e.g. a
    test runner's) — so a re-read of settings never duplicates log lines. Returns the handlers added."""
    root = logging.getLogger()
    for handler in _added:
        root.removeHandler(handler)
        handler.close()
    _added.clear()

    logs_dir.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(FORMAT)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(logs_dir / "clipper.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"),
        _RingHandler(),
    ]
    if sys.stderr is not None:                          # pythonw (a windowed app) has no console
        handlers.append(logging.StreamHandler())
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(logging.INFO)

    _added.extend(handlers)
    return list(_added)


def recent(limit: int = 200) -> list[str]:
    """The newest `limit` formatted lines from the ring, oldest first."""
    with _lock:
        items = list(_buffer)
    return items[-limit:]
