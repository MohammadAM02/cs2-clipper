import clipper.procs
from clipper.procs import SystemProbe


class FakeProcess:
    def __init__(self, name, args):
        self.info, self._args = {"name": name}, args

    def cmdline(self):
        return self._args


def test_only_a_cs2_without_the_render_flag_is_the_users(monkeypatch):
    running = [FakeProcess("explorer.exe", []), FakeProcess("cs2.exe", ["cs2.exe", "-insecure"])]
    monkeypatch.setattr(clipper.procs.psutil, "process_iter", lambda attrs: iter(running))
    assert SystemProbe().user_cs2_running() is False
    running.append(FakeProcess("CS2.EXE", ["cs2.exe", "-steam"]))
    assert SystemProbe().user_cs2_running() is True
