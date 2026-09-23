import json
import sys
import threading
from dataclasses import replace
from pathlib import Path

import pytest

from clipper.csdm_cli import CsdmCli
from clipper.media import MediaError
from clipper.render import NEVER_LAUNCHED, RenderRequest, render

FAKE_CSDM = Path(__file__).with_name("fake_csdm.py")
SUBJECT = "76561198192858303"


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


@pytest.fixture
def csdm(tmp_path):
    return CsdmCli(prefix=(sys.executable, str(FAKE_CSDM)), home=tmp_path / "home", pg_bin=tmp_path / "pgbin")


@pytest.fixture
def req(tmp_path):
    return RenderRequest(
        demo_path=tmp_path / "match.dem", perspective="enemy", rounds=(3, 12),
        output_dir=tmp_path / "out", log_path=tmp_path / "logs" / "render.log",
        steamid=SUBJECT, padding_before_s=4.0, padding_after_s=2.0,
    )


@pytest.fixture
def stopfile(tmp_path, monkeypatch):
    path = tmp_path / "game-died"
    monkeypatch.setenv("FAKE_CSDM_STOPFILE", str(path))
    return path


def run(req, csdm, probe, *, abort=lambda: False, stall=5.0, launch=5.0, duration_of=lambda path: 4.0):
    return render(
        req, csdm=csdm, probe=probe, should_abort=abort, stall_seconds=stall,
        launch_timeout_seconds=launch, duration_of=duration_of,
        poll_seconds=0.05, exit_grace_seconds=5.0, abort_sweep_seconds=0.0,
    )


def test_success_returns_the_clips_in_tick_order(req, csdm, stopfile, monkeypatch):
    monkeypatch.setenv("FAKE_CSDM_MODE", "ok")
    monkeypatch.setenv("FAKE_CSDM_TICKS", "76071-76559,19832-20213")
    result = run(req, csdm, FakeProbe(stopfile))
    assert result.ok and result.failure is None
    assert [(c.start_tick, c.end_tick) for c in result.clips] == [(19832, 20213), (76071, 76559)]
    assert [c.sequence for c in result.clips] == [2, 1]
    assert all(c.duration_s == 4.0 for c in result.clips)


def test_the_command_line_and_environment(req, csdm, stopfile, tmp_path, monkeypatch):
    argv_file = tmp_path / "argv.json"
    monkeypatch.setenv("FAKE_CSDM_MODE", "argv")
    monkeypatch.setenv("FAKE_CSDM_ARGV", str(argv_file))
    monkeypatch.setenv("PGPASSWORD", "must-not-leak")
    run(replace(req, event="rounds", width=1280, height=960), csdm, FakeProbe(stopfile))
    seen = json.loads(argv_file.read_text(encoding="utf-8"))
    assert seen["argv"] == [
        "video", str(req.demo_path), "--mode", "player", "--steamids", SUBJECT, "--event", "rounds",
        "--rounds", "3,12", "--perspective", "enemy", "--width", "1280", "--height", "960",
        "--output", str(req.output_dir), "--close-game-after-recording",
        "--start-seconds-before", "4", "--end-seconds-after", "2",
    ]
    assert seen["USERPROFILE"] == str(tmp_path / "home")
    assert seen["ELECTRON_RUN_AS_NODE"] == "1"
    assert seen["PGPASSWORD"] is None
    assert seen["PATH"].startswith(str(tmp_path / "pgbin"))


def test_a_reported_failure_wins_over_the_exit_code(req, csdm, stopfile, monkeypatch):
    monkeypatch.setenv("FAKE_CSDM_MODE", "auth")
    result = run(req, csdm, FakeProbe(stopfile))
    assert not result.ok
    assert result.failure == "csdm reported: password authentication failed"


def test_success_without_clips_is_a_failure(req, csdm, stopfile, monkeypatch):
    monkeypatch.setenv("FAKE_CSDM_MODE", "noclips")
    result = run(req, csdm, FakeProbe(stopfile))
    assert not result.ok and "no sequence" in result.failure


def test_an_unreadable_clip_is_a_failure(req, csdm, stopfile, monkeypatch):
    monkeypatch.setenv("FAKE_CSDM_MODE", "ok")
    monkeypatch.setenv("FAKE_CSDM_TICKS", "100-356")

    def unreadable(path):
        raise MediaError(f"ffprobe cannot read {path.name}")

    result = run(req, csdm, FakeProbe(stopfile), duration_of=unreadable)
    assert not result.ok and result.failure.startswith("unreadable clip")


def test_a_stall_closes_the_hooked_cs2_and_fails(req, csdm, stopfile, monkeypatch):
    monkeypatch.setenv("FAKE_CSDM_MODE", "hang")
    probe = FakeProbe(stopfile, cs2=True, ffmpeg=False)
    result = run(req, csdm, probe, stall=0.3)
    assert not result.ok and result.failure.startswith("stalled")
    assert probe.kills >= 1


def test_ffmpeg_activity_is_not_a_stall(req, csdm, stopfile, monkeypatch):
    monkeypatch.setenv("FAKE_CSDM_MODE", "hang")
    probe = FakeProbe(stopfile, cs2=True, ffmpeg=True)
    threading.Timer(1.0, stopfile.touch).start()
    result = run(req, csdm, probe, stall=0.3)
    assert result.failure == "csdm reported: Game error"
    assert probe.kills == 0


def test_csdm_that_never_launches_cs2_is_stopped(req, csdm, stopfile, monkeypatch):
    monkeypatch.setenv("FAKE_CSDM_MODE", "hang")
    result = run(req, csdm, FakeProbe(stopfile, cs2=False), launch=0.3)
    assert not result.ok and result.failure == NEVER_LAUNCHED


def test_faceit_starting_aborts_the_render(req, csdm, stopfile, monkeypatch):
    monkeypatch.setenv("FAKE_CSDM_MODE", "hang")
    probe = FakeProbe(stopfile, cs2=True)
    result = run(req, csdm, probe, abort=lambda: True)
    assert result.aborted and not result.ok
    assert probe.kills >= 1
