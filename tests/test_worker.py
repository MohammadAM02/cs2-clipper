from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from clipper.config import Config
from clipper.gate import GUI_REASON, GateStatus
from clipper.index import Index
from clipper.model import ClipFile, MatchInfo
from clipper.render import RenderResult
from clipper.worker import Services, Worker
from tests.fixtures import DEMO_NAME, MATCH_CHECKSUM, MATCH_FACTS

ROUND_START = {facts.round: facts.round_start_tick for facts in MATCH_FACTS}
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

    def find_checksum(self, demo_name):
        return MATCH_CHECKSUM

    def match_info(self, checksum, steamid):
        return self.info

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

    def add_demo(self) -> int:
        name = f"{DEMO_NAME}.dem.zst"
        return self.index.add_demo(name, "0" * 64, self.cfg.demos_dir / name)

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
    yield World(cfg, index, services, Worker(cfg, index, services), facts, gate, render, notices)
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
    world.index.set_flag("paused", "1")
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
