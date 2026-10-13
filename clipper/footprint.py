"""What the app itself takes from the PC: the memory and CPU of its own processes, which Status and the tray show so
the zero-FPS promise can be checked at a glance. The window is a process of its own (with WebView2's under it) and
ends with the window, so its share goes when it closes. What a render starts -- HLAE, CS2, FFmpeg, csda -- is the
render's, not the app's idle cost, and is left out."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import psutil

RENDER_TOOLS = frozenset({"cs2.exe", "hlae.exe", "ffmpeg.exe", "ffprobe.exe", "csda.exe"})
MB = 1024 * 1024
FRESH_SECONDS = 2.0          # Status asks every 2 s and the tray every few: one sample serves both


@dataclass(frozen=True)
class Usage:
    memory_mb: int
    cpu_percent: float       # of the whole CPU, every core counted: 100 is every core busy

    def line(self) -> str:
        return f"{self.memory_mb} MB RAM · {self.cpu_percent:.1f}% CPU"


class Footprint:
    """Samples the app's process and its children; a CPU share needs two samples, so the first one reads 0."""

    def __init__(self, root: psutil.Process | None = None, cores: int | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self._root = root if root is not None else psutil.Process()
        self._cores = cores or psutil.cpu_count() or 1
        self._clock = clock
        self._lock = threading.Lock()
        self._known: dict[int, psutil.Process] = {}      # by pid: cpu_percent compares with its own last call
        self._last: tuple[float, Usage] | None = None

    def usage(self) -> Usage:
        with self._lock:
            now = self._clock()
            if self._last is not None and now - self._last[0] < FRESH_SECONDS:
                return self._last[1]
            usage = self._sample()
            self._last = (now, usage)
            return usage

    def _sample(self) -> Usage:
        try:
            processes = [self._root, *self._root.children(recursive=True)]
        except psutil.Error:
            processes = [self._root]
        memory, cpu, alive = 0, 0.0, {}
        for process in processes:
            try:
                if process.name().lower() in RENDER_TOOLS:
                    continue
                known = self._known.get(process.pid, process)
                memory += known.memory_info().rss
                cpu += known.cpu_percent(interval=None)
                alive[process.pid] = known
            except psutil.Error:      # it ended while being looked at
                continue
        self._known = alive
        return Usage(round(memory / MB), round(cpu / self._cores, 1))
