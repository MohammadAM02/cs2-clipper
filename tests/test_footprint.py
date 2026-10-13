from types import SimpleNamespace

import psutil

from clipper.footprint import MB, Footprint, Usage


class FakeProcess:
    def __init__(self, pid, name, rss_mb, cpu, children=()):
        self.pid, self._name, self._rss, self._cpu, self._children = pid, name, rss_mb * MB, cpu, list(children)

    def name(self):
        return self._name

    def memory_info(self):
        return SimpleNamespace(rss=self._rss)

    def cpu_percent(self, interval=None):
        return self._cpu

    def children(self, recursive=False):
        return self._children


class Gone(FakeProcess):
    def memory_info(self):
        raise psutil.NoSuchProcess(self.pid)


def test_the_app_and_its_window_count_and_what_a_render_starts_does_not():
    window = FakeProcess(2, "CS2Clipper.exe", 60, 4.0)
    webview = FakeProcess(3, "msedgewebview2.exe", 90, 2.0)
    cs2 = FakeProcess(4, "cs2.exe", 3000, 200.0)
    ffmpeg = FakeProcess(5, "ffmpeg.exe", 80, 100.0)
    root = FakeProcess(1, "CS2Clipper.exe", 50, 2.0, children=[window, webview, cs2, ffmpeg])
    assert Footprint(root, cores=8).usage() == Usage(200, 1.0)


def test_a_process_that_ends_while_it_is_looked_at_is_left_out():
    root = FakeProcess(1, "CS2Clipper.exe", 50, 0.0, children=[Gone(2, "CS2Clipper.exe", 60, 0.0)])
    assert Footprint(root, cores=1).usage() == Usage(50, 0.0)


def test_a_fresh_sample_is_reused_for_two_seconds():
    now = [0.0]
    root = FakeProcess(1, "CS2Clipper.exe", 50, 0.0)
    footprint = Footprint(root, cores=1, clock=lambda: now[0])
    assert footprint.usage().memory_mb == 50
    root._rss = 70 * MB
    now[0] = 1.9
    assert footprint.usage().memory_mb == 50
    now[0] = 2.0
    assert footprint.usage().memory_mb == 70


def test_this_process_has_a_footprint():
    usage = Footprint().usage()
    assert usage.memory_mb > 0 and usage.cpu_percent >= 0
    assert usage.line().endswith("% CPU")
