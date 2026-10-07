import sys
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clipper.config import Config
from clipper.csdm_cli import CsdmCli
from clipper.gate import GUI_REASON, GateStatus
from clipper.index import Index
from clipper.model import ClipFile, MatchInfo
from clipper.render import RenderResult, render as run_render
from clipper.state import AppState
from clipper.worker import QUIT_ABORT, Services, StopRequest, Worker
from tests.fakes import FakeProbe
from tests.fixtures import DEMO_NAME, MATCH_CHECKSUM, MATCH_FACTS

FAKE_CSDM = Path(__file__).with_name("fake_csdm.py")
ROUND_START = {facts.round: facts.round_start_tick for facts in MATCH_FACTS}
SECOND_DEMO = "1-00000000-0000-4000-8000-000000000002-1-1"
SECOND_CHECKSUM = "00000000000000b2"
MATCH = MatchInfo(checksum=MATCH_CHECKSUM, map_name="de_inferno",
                  played_at=datetime(2026, 9, 22, 9, 39, 14, tzinfo=timezone.utc),
                  team_score=13, opponent_score=5)


class FakeIntake:
    def ready(self):
        return []

    def take(self, path, index):
        raise AssertionError("these tests use no Downloads folder")


class FakeFacts:
    def __init__(self):
        self.info = MATCH
        self.facts = MATCH_FACTS
        self.checksums = {SECOND_DEMO: SECOND_CHECKSUM}

    def find_checksum(self, demo_name):
        return self.checksums.get(demo_name, MATCH_CHECKSUM)

    def match_info(self, checksum, steamid):
        return None if self.info is None else replace(self.info, checksum=checksum)

    def round_facts(self, checksum, steamid):
        return list(self.facts)


class FakeGate:
    def __init__(self):
        self.reasons: tuple[str, ...] = ()

    def check(self):
        return GateStatus(ok=not self.reasons, reasons=self.reasons)

    def faceit_running(self):
        return False


class FakeRender:
    """Succeeds with one Clip per requested round, unless canned results are queued."""

    def __init__(self):
        self.calls = []
        self.results = []

    def __call__(self, request, should_abort):
        self.calls.append(request)
        if self.results:
            return self.results.pop(0)
        clips = tuple(
            ClipFile(sequence=n, start_tick=ROUND_START[r] + 1000, end_tick=ROUND_START[r] + 1256,
                     path=request.output_dir / f"sequence-{n}.mp4", duration_s=4.0)
            for n, r in enumerate(request.rounds, start=1)
        )
        return RenderResult(ok=True, clips=clips)


@dataclass
class World:
    cfg: Config
    index: Index
    services: Services
    worker: Worker
    facts: FakeFacts
    gate: FakeGate
    render: FakeRender
    notices: list
    state: AppState
    stop: StopRequest

    def add_demo(self, name: str = DEMO_NAME, sha256: str = "0" * 64) -> int:
        file_name = f"{name}.dem.zst"
        return self.index.add_demo(file_name, sha256, self.cfg.demos_dir / file_name)

    def ticks(self, count: int) -> None:
        for _ in range(count):
            self.worker.tick()

    def titles(self) -> list[str]:
        return [title for title, _ in self.notices]


@pytest.fixture
def world(tmp_path):
    cfg = Config(downloads_dir=tmp_path / "downloads", data_root=tmp_path / "clips",
                 index_path=tmp_path / "clipper.sqlite")
    index = Index(cfg.index_path)
    facts, gate, render, notices = FakeFacts(), FakeGate(), FakeRender(), []
    state, stop = AppState(), StopRequest()
    services = Services(
        intake=FakeIntake(),
        unpack=lambda archive, out_dir: out_dir / archive.name.removesuffix(".zst"),
        analyze=lambda dem, log_path: "ok",
        facts=facts,
        gate=gate,
        render=render,
        join=lambda clips, out: 4.0 * len(clips),
        notify=lambda title, body: notices.append((title, body)),
        sleep=lambda seconds: None,
    )
    worker = Worker(cfg, index, services, state=state, stop=stop)
    yield World(cfg, index, services, worker, facts, gate, render, notices, state, stop)
    index.close()


def test_a_demo_goes_from_spotted_to_done(world):
    demo_id = world.add_demo()
    world.ticks(8)
    assert world.index.demo(demo_id)["state"] == "done"
    assert [call.perspective for call in world.render.calls] == ["player", "enemy"]
    assert world.render.calls[0].rounds == (3, 4, 8, 12, 14)
    first = world.render.calls[0]
    assert (first.event, first.width, first.height) == ("kills", 1920, 1080)
    assert world.index.reel_count(MATCH_CHECKSUM) == 10
    assert world.titles() == ["Rendering highlights", "Rendering highlights", "Highlights ready"]


def test_one_demo_renders_both_views_before_the_next_demo_starts(world):
    first = world.add_demo()
    second = world.add_demo(SECOND_DEMO, "1" * 64)
    world.ticks(12)
    assert [(call.demo_path.name, call.perspective) for call in world.render.calls] == [
        (f"{DEMO_NAME}.dem", "player"), (f"{DEMO_NAME}.dem", "enemy"),
        (f"{SECOND_DEMO}.dem", "player"), (f"{SECOND_DEMO}.dem", "enemy"),
    ]
    assert world.index.demo(first)["state"] == world.index.demo(second)["state"] == "done"


def test_the_next_demo_waits_while_the_first_retries_a_failed_view(world):
    world.render.results = [RenderResult(ok=False, failure="stalled: no ffmpeg for 180s while CS2 ran")]
    world.add_demo()
    world.add_demo(SECOND_DEMO, "1" * 64)
    world.ticks(13)
    assert [(call.demo_path.name, call.perspective) for call in world.render.calls] == [
        (f"{DEMO_NAME}.dem", "player"), (f"{DEMO_NAME}.dem", "player"), (f"{DEMO_NAME}.dem", "enemy"),
        (f"{SECOND_DEMO}.dem", "player"), (f"{SECOND_DEMO}.dem", "enemy"),
    ]


def test_a_match_without_the_subject_is_skipped(world):
    world.facts.info = None
    demo_id = world.add_demo()
    world.ticks(3)
    demo = world.index.demo(demo_id)
    assert demo["state"] == "skipped"
    assert "not in this match" in demo["last_error"]


def test_renders_wait_for_the_gate(world):
    world.gate.reasons = ("FACEIT AC is running",)
    demo_id = world.add_demo()
    world.ticks(6)
    assert world.index.demo(demo_id)["state"] == "rendering"
    assert world.render.calls == []
    world.gate.reasons = ()
    world.ticks(4)
    assert world.index.demo(demo_id)["state"] == "done"


def test_the_csdm_gui_notice_is_sent_once(world):
    world.gate.reasons = (GUI_REASON,)
    world.add_demo()
    world.ticks(8)
    assert world.titles().count("Close CS Demo Manager") == 1


def test_three_failed_renders_fail_the_demo_and_pause_rendering(world):
    world.render.results = [RenderResult(ok=False, failure="csdm reported: Game error")] * 3
    demo_id = world.add_demo()
    world.ticks(8)
    demo = world.index.demo(demo_id)
    assert demo["state"] == "failed"
    assert demo["last_error"] == "player render failed 3 times: csdm reported: Game error"
    assert world.index.get_flag("paused") == "1"
    assert world.index.paused_by() == "failures"
    assert "Rendering paused" in world.titles()


def test_an_aborted_render_is_tried_again_without_counting(world):
    world.render.results = [RenderResult(ok=False, aborted=True,
                                         failure="aborted: FACEIT AC started during the render")]
    demo_id = world.add_demo()
    world.ticks(9)
    assert world.index.demo(demo_id)["state"] == "done"
    assert [call.perspective for call in world.render.calls] == ["player", "player", "enemy"]
    assert world.index.get_flag("consecutive_failures", "0") == "0"


def test_paused_rendering_waits_for_resume(world):
    world.index.pause("you")
    demo_id = world.add_demo()
    world.ticks(8)
    assert world.index.demo(demo_id)["state"] == "rendering"
    assert world.render.calls == []


def test_a_step_that_keeps_failing_fails_the_demo_and_retry_resumes_it(world):
    def broken_unpack(archive, out_dir):
        raise OSError("disk full")

    healthy_unpack = world.services.unpack
    world.services.unpack = broken_unpack
    demo_id = world.add_demo()
    world.ticks(3)
    demo = world.index.demo(demo_id)
    assert (demo["state"], demo["resume_state"]) == ("failed", "spotted")
    assert demo["last_error"] == "spotted: disk full"
    world.services.unpack = healthy_unpack
    assert world.index.retry(demo_id) == "spotted"
    world.ticks(8)
    assert world.index.demo(demo_id)["state"] == "done"


def test_a_match_without_frags_finishes_without_rendering(world):
    world.facts.facts = ()
    demo_id = world.add_demo()
    world.ticks(4)
    assert world.index.demo(demo_id)["state"] == "done"
    assert world.render.calls == []
    assert world.notices == []


class FailingAlerts:
    def __init__(self):
        self.ticks = 0

    def tick(self):
        self.ticks += 1
        raise RuntimeError("FACEIT is having a bad day")


def test_match_alerts_run_every_tick_and_never_stop_the_pipeline(world):
    world.services.alerts = FailingAlerts()
    demo_id = world.add_demo()
    world.ticks(8)
    assert world.services.alerts.ticks == 8
    assert world.index.demo(demo_id)["state"] == "done"


class _CountingAlerts:
    def __init__(self):
        self.ticks = 0

    def tick(self):
        self.ticks += 1


# --- StopRequest -------------------------------------------------------------------------------


def test_stop_request_upgrades_after_render_to_now_but_never_downgrades():
    stop = StopRequest()
    assert stop.mode is None
    stop.request("after_render")
    assert stop.mode == "after_render"
    stop.request("now")
    assert stop.mode == "now"
    stop.request("after_render")   # must not downgrade a "now" that is already in effect
    assert stop.mode == "now"


def test_stop_request_stopping_and_abort_render_reflect_the_mode():
    stop = StopRequest()
    assert not stop.stopping()
    assert not stop.abort_render()
    stop.request("after_render")
    assert stop.stopping()
    assert not stop.abort_render()
    stop.request("now")
    assert stop.stopping()
    assert stop.abort_render()


def test_stop_request_wait_returns_true_on_a_request_and_false_on_timeout():
    stop = StopRequest()
    assert stop.wait(0.05) is False
    stop.request("now")
    assert stop.wait(1.0) is True


# --- state: what the worker publishes each tick -------------------------------------------------


def test_state_reports_rendering_during_a_render_then_clears_it_after_the_tick(world):
    seen = {}

    def render_and_snapshot(request, should_abort):
        seen["rendering"] = world.state.snapshot().rendering
        clips = tuple(
            ClipFile(sequence=n, start_tick=ROUND_START[r] + 1000, end_tick=ROUND_START[r] + 1256,
                     path=request.output_dir / f"sequence-{n}.mp4", duration_s=4.0)
            for n, r in enumerate(request.rounds, start=1)
        )
        return RenderResult(ok=True, clips=clips)

    world.services.render = render_and_snapshot
    world.add_demo()
    world.ticks(5)   # spotted -> unpacked -> analyzed -> scored -> rendering (queues) -> the player render
    rendering = seen["rendering"]
    assert rendering is not None
    assert (rendering.map_name, rendering.perspective) == ("de_inferno", "player")
    snap = world.state.snapshot()
    assert snap.rendering is None   # cleared by the end of the tick that started it
    assert snap.waiting == ()


def test_state_reports_waiting_with_the_gates_reasons(world):
    world.gate.reasons = ("FACEIT AC is running",)
    world.add_demo()
    world.ticks(5)   # reaches the render step, but the Gate keeps it waiting
    snap = world.state.snapshot()
    assert snap.waiting == ("FACEIT AC is running",)
    assert snap.rendering is None


def test_state_publishes_paused_by(world):
    world.index.pause("you")
    world.add_demo()
    world.ticks(1)
    assert world.state.snapshot().paused_by == "you"
    world.index.resume()
    world.ticks(1)
    assert world.state.snapshot().paused_by is None


# --- stopping ------------------------------------------------------------------------------------


def test_a_stop_during_the_heads_up_skips_the_render_and_ends_early(world):
    calls = []

    def render_spy(request, should_abort):
        calls.append(request)
        return RenderResult(ok=True, clips=())

    world.services.render = render_spy
    sleep_calls = []

    def sleep_then_quit_now(seconds):
        sleep_calls.append(seconds)
        world.stop.request("now")

    world.services.sleep = sleep_then_quit_now
    demo_id = world.add_demo()
    world.ticks(5)
    assert calls == []
    assert len(sleep_calls) == 1   # the heads-up loop bailed right after the stop arrived
    assert world.index.latest_render(demo_id, "player")["state"] == "queued"


def test_quit_after_render_finishes_it_then_takes_no_further_step(world):
    calls = []

    def render_then_request_stop(request, should_abort):
        calls.append(request)
        world.stop.request("after_render")
        clips = tuple(
            ClipFile(sequence=n, start_tick=ROUND_START[r] + 1000, end_tick=ROUND_START[r] + 1256,
                     path=request.output_dir / f"sequence-{n}.mp4", duration_s=4.0)
            for n, r in enumerate(request.rounds, start=1)
        )
        return RenderResult(ok=True, clips=clips)

    world.services.render = render_then_request_stop
    alerts = _CountingAlerts()
    world.services.alerts = alerts
    demo_id = world.add_demo()
    world.ticks(5)
    assert world.index.latest_render(demo_id, "player")["state"] == "done"
    assert world.index.demo(demo_id)["state"] == "rendering"   # the enemy Perspective was not started
    assert len(calls) == 1
    assert alerts.ticks == 4   # ticks 1-4 ran alerts; tick 5's alerts step was skipped once stopping
    world.ticks(3)   # nothing more should ever happen
    assert len(calls) == 1
    assert world.index.demo(demo_id)["state"] == "rendering"
    assert alerts.ticks == 4


class _QuitsOnFirstAsk(FakeProbe):
    """Like FakeProbe, with its hooked CS2 already running, but the first hooked_cs2_running() check
    requests a "now" stop -- simulating Quit now arriving from the tray or the Status page while
    render.render's watch loop is asking about the game, exactly where a real click would land."""

    def __init__(self, stopfile: Path, stop: StopRequest):
        super().__init__(stopfile, cs2=True)
        self._stop = stop
        self._asked = False

    def hooked_cs2_running(self):
        if not self._asked:
            self._asked = True
            self._stop.request("now")
        return super().hooked_cs2_running()


def test_quit_now_during_a_render_aborts_it_like_a_faceit_ac_abort(world, tmp_path, monkeypatch):
    stopfile = tmp_path / "game-died"
    monkeypatch.setenv("FAKE_CSDM_MODE", "hang")
    monkeypatch.setenv("FAKE_CSDM_STOPFILE", str(stopfile))
    csdm = CsdmCli(prefix=(sys.executable, str(FAKE_CSDM)), home=tmp_path / "home", pg_bin=tmp_path / "pgbin")
    probe = _QuitsOnFirstAsk(stopfile, world.stop)

    def real_render(request, should_abort):
        return run_render(
            request, csdm=csdm, probe=probe, should_abort=should_abort,
            stall_seconds=5.0, launch_timeout_seconds=5.0, duration_of=lambda path: 4.0,
            poll_seconds=0.05, exit_grace_seconds=0.0, abort_sweep_seconds=0.0,
        )

    world.services.render = real_render
    alerts = _CountingAlerts()
    world.services.alerts = alerts
    demo_id = world.add_demo()
    world.ticks(5)
    job = world.index.latest_render(demo_id, "player")
    assert (job["state"], job["failure"]) == ("aborted", QUIT_ABORT)
    assert world.index.demo(demo_id)["state"] == "rendering"
    assert world.index.latest_render(demo_id, "enemy")["state"] == "queued"   # not started
    assert probe.kills >= 1
    assert alerts.ticks == 4   # this tick's alerts step was skipped once the abort set stop
