"""Fakes shared by more than one test module."""

from __future__ import annotations

from pathlib import Path


class FakeProbe:
    """A hooked CS2 that 'dies' — touching the stop file the fake csdm waits for — when killed."""

    def __init__(self, stopfile: Path, cs2: bool = False, ffmpeg: bool = False):
        self.stopfile, self.cs2, self.ffmpeg = stopfile, cs2, ffmpeg
        self.kills = 0

    def names(self):
        return set()

    def running(self, name):
        return name == "ffmpeg.exe" and self.ffmpeg

    def service_running(self, name):
        return False

    def hooked_cs2_running(self):
        return self.cs2

    def kill_hooked_cs2(self):
        self.kills += 1
        self.cs2 = False
        self.stopfile.touch()

    def children_named(self, pid, name):
        return set()

    def kill_processes(self, processes):
        pass
