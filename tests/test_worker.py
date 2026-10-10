import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clipper.config import Config
from clipper.gate import GateStatus
from clipper.index import Index
from clipper.model import ClipFile, MatchInfo
from clipper.render import ABORTED, RenderProgress, RenderResult
from clipper.state import AppState
from clipper.worker import QUIT_ABORT, DeleteRequest, Services, StopRequest, Worker, delete_demo, prune_renders
from tests.fixtures import DEMO_NAME, MATCH_CHECKSUM, MATCH_FACTS

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
    """The analyses: `analyze` stands in for csda, which keeps an analysis of the Demo's match."""

    def __init__(self):
        self.info = MATCH
        self.facts = MATCH_FACTS
        self.checksums = {SECOND_DEMO: SECOND_CHECKSUM}
        self.kept: set[str] = set()
        self.analyzed: list[str] = []

    def analyze(self, dem, log_path):
        self.analyzed.append(dem.name)
        checksum = self.checksums.get(dem.stem, MATCH_CHECKSUM)
        self.kept.add(checksum)
        return checksum

    def has(self, checksum):
        return checksum in self.kept

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


def clips_of(request):
    """What a render of `request` gives back: one Clip per round asked for, in the folder of each of its views."""
    return {
        perspective: tuple(
            ClipFile(sequence=n, start_tick=ROUND_START[r] + 1000, end_tick=ROUND_START[r] + 1256,
                     path=folder / f"sequence-{n}.mp4", duration_s=4.0)
            for n, r in enumerate(request.rounds, start=1))
        for perspective, folder in request.outputs.items()
    }


class FakeRender:
    """Succeeds with one Clip per requested round, unless canned results are queued: a RenderResult, or a function
    of the request that gives one (for a partial result, whose Clips depend on the request)."""

    def __init__(self):
        self.calls = []
        self.results = []

    def __call__(self, request, should_abort, progress):
        self.calls.append(request)
        if self.results:
            result = self.results.pop(0)
            return result(request) if callable(result) else result
        return RenderResult(ok=True, clips=clips_of(request))


class FakeClock:
    """The worker's clock: `now` moves only when a test moves it."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


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
    deletes: DeleteRequest
    clock: FakeClock

    def add_demo(self, name: str = DEMO_NAME, sha256: str = "0" * 64) -> int:
        file_name = f"{name}.dem.zst"
        return self.index.add_demo(file_name, sha256, self.cfg.demos_dir / file_name)

    def ticks(self, count: int) -> None:
        for _ in range(count):
            self.worker.tick()

    def use(self, **settings) -> None:
        """Change settings as the app does: the next tick runs on a new Worker with the new Config."""
        self.cfg = replace(self.cfg, **settings)
        self.worker = Worker(self.cfg, self.index, self.services, state=self.state, stop=self.stop,
                             deletes=self.deletes)

    def titles(self) -> list[str]:
        return [title for title, _ in self.notices]


@pytest.fixture
def world(tmp_path):
    cfg = Config(downloads_dir=tmp_path / "downloads", data_root=tmp_path / "clips",
                 index_path=tmp_path / "clipper.sqlite", analyses_dir=tmp_path / "analyses")
    index = Index(cfg.index_path)
    facts, gate, render, notices = FakeFacts(), FakeGate(), FakeRender(), []
    state, stop, deletes, clock = AppState(), StopRequest(), DeleteRequest(), FakeClock()
    services = Services(
        intake=FakeIntake(),
        unpack=lambda archive, out_dir: out_dir / archive.name.removesuffix(".zst"),
        analyze=facts.analyze,
        facts=facts,
        gate=gate,
        render=render,
        join=lambda clips, out: 4.0 * len(clips),
        notify=lambda title, body: notices.append((title, body)),
        sleep=lambda seconds: None,
        clock=clock,
    )
    worker = Worker(cfg, index, services, state=state, stop=stop, deletes=deletes)
    yield World(cfg, index, services, worker, facts, gate, render, notices, state, stop, deletes, clock)
    index.close()


def test_a_demo_goes_from_spotted_to_done(world):
    demo_id = world.add_demo()
    world.ticks(8)
    assert world.index.demo(demo_id)["state"] == "done"
    assert [call.perspectives for call in world.render.calls] == [("player", "enemy")]   # one CS2 launch, both views
    assert world.render.calls[0].rounds == (3, 4, 8, 12, 14)
    first = world.render.calls[0]
    assert (first.event, first.width, first.height) == ("kills", 1920, 1080)
    assert world.index.reel_count(MATCH_CHECKSUM) == 10
    assert world.titles() == ["Rendering highlights", "Highlights ready"]   # one heads-up for both views


def test_both_views_are_recorded_in_one_cs2_launch(world):
    demo_id = world.add_demo()
    world.ticks(5)                                    # queued on tick 4, then the one launch that records both views
    assert len(world.render.calls) == 1
    request = world.render.calls[0]
    assert request.perspectives == ("player", "enemy")
    assert request.outputs == {"player": world.cfg.renders_dir / MATCH_CHECKSUM / "player" / "attempt-1",
                               "enemy": world.cfg.renders_dir / MATCH_CHECKSUM / "enemy" / "attempt-1"}
    assert request.log_path.name == f"demo-{demo_id}-render-both-1.log"
    assert [world.index.latest_render(demo_id, p)["state"] for p in ("player", "enemy")] == ["done", "done"]
    world.ticks(3)
    assert world.index.demo(demo_id)["state"] == "done"
    assert world.index.reel_count(MATCH_CHECKSUM) == 10


@pytest.mark.parametrize(("perspectives", "named"),
                         [("player", "(player view)"), ("enemy", "(enemy view)"), ("both", "(both views)")])
def test_the_heads_up_names_the_views_the_launch_records(world, perspectives, named):
    world.use(perspectives=perspectives)
    world.add_demo()
    world.ticks(5)
    assert world.notices[0][1].count(named) == 1


def test_a_failed_two_view_launch_is_tried_again_one_view_at_a_time(world):
    world.render.results = [RenderResult(ok=False, failure="CS2 closed before the render finished")]
    demo_id = world.add_demo()
    world.ticks(5)                                    # both views fail in the one launch
    assert [world.index.latest_render(demo_id, p)["state"] for p in ("player", "enemy")] == ["failed", "failed"]
    world.ticks(2)                                    # then each view on its own: the player's, then the enemy's
    assert [call.perspectives for call in world.render.calls] == [("player", "enemy"), ("player",), ("enemy",)]
    assert [call.log_path.name for call in world.render.calls] == [
        f"demo-{demo_id}-render-both-1.log", f"demo-{demo_id}-render-player-2.log",
        f"demo-{demo_id}-render-enemy-2.log",
    ]
    assert world.index.latest_render(demo_id, "enemy")["attempt"] == 2
    world.ticks(3)
    assert world.index.demo(demo_id)["state"] == "done"
    assert world.index.reel_count(MATCH_CHECKSUM) == 10
    assert world.index.get_flag("consecutive_failures") == "0"


def test_a_partial_two_view_launch_keeps_the_view_that_recorded(world):
    def player_recorded_enemy_stalled(request):
        return RenderResult(ok=False, failure="stalled: no ffmpeg for 180s while CS2 ran",
                            clips={"player": clips_of(request)["player"]})

    world.render.results = [player_recorded_enemy_stalled]
    demo_id = world.add_demo()
    world.ticks(5)                                    # the player view is kept; the enemy view failed
    assert world.index.latest_render(demo_id, "player")["state"] == "done"
    assert world.index.latest_render(demo_id, "enemy")["state"] == "failed"
    world.ticks(2)                                    # the enemy view alone, at attempt 2
    assert [call.perspectives for call in world.render.calls] == [("player", "enemy"), ("enemy",)]
    assert world.index.latest_render(demo_id, "enemy")["attempt"] == 2
    assert world.index.latest_render(demo_id, "enemy")["state"] == "done"
    world.ticks(3)
    assert world.index.demo(demo_id)["state"] == "done"
    assert world.index.reel_count(MATCH_CHECKSUM) == 10


def test_an_aborted_two_view_launch_is_tried_again_as_one_launch(world):
    world.render.results = [RenderResult(ok=False, aborted=True, failure=ABORTED)]
    demo_id = world.add_demo()
    world.ticks(9)
    assert [call.perspectives for call in world.render.calls] == [("player", "enemy")] * 2
    assert world.index.demo(demo_id)["state"] == "done"
    assert world.index.get_flag("consecutive_failures", "0") == "0"
    assert world.titles() == ["Rendering highlights", "Rendering highlights", "Highlights ready"]   # retry: heads-up


def test_one_failed_two_view_launch_adds_one_to_the_consecutive_failures(world):
    world.render.results = [RenderResult(ok=False, failure="stalled: no ffmpeg for 180s while CS2 ran")] * 2
    demo_id = world.add_demo()
    world.ticks(5)                                    # one launch, both views failed
    assert [world.index.latest_render(demo_id, p)["state"] for p in ("player", "enemy")] == ["failed", "failed"]
    assert world.index.get_flag("consecutive_failures") == "1"
    world.ticks(1)                                    # the player view alone fails again
    assert world.index.get_flag("consecutive_failures") == "2"
    assert world.index.paused_by() is None


def test_one_demo_renders_both_views_before_the_next_demo_starts(world):
    first = world.add_demo()
    second = world.add_demo(SECOND_DEMO, "1" * 64)
    world.ticks(12)
    assert [(call.demo_path.name, call.perspectives) for call in world.render.calls] == [
        (f"{DEMO_NAME}.dem", ("player", "enemy")), (f"{SECOND_DEMO}.dem", ("player", "enemy")),
    ]
    assert world.index.demo(first)["state"] == world.index.demo(second)["state"] == "done"


@pytest.mark.parametrize("perspective", ["player", "enemy"])
def test_a_demo_renders_and_joins_only_the_chosen_view(world, perspective):
    world.use(perspectives=perspective)
    demo_id = world.add_demo()
    world.ticks(8)
    assert world.index.demo(demo_id)["state"] == "done"
    assert [call.perspectives[0] for call in world.render.calls] == [perspective]
    reels = world.index.match_reels(MATCH_CHECKSUM)
    assert [(r["player_reel_id"] is not None, r["enemy_reel_id"] is not None) for r in reels] == [
        (perspective == "player", perspective == "enemy")] * 5
    assert world.titles() == ["Rendering highlights", "Highlights ready"]


def test_round_clips_are_rendered_from_the_players_view_only(world):
    world.use(sequence_event="rounds")
    world.add_demo()
    world.ticks(8)
    assert [(call.perspectives[0], call.event) for call in world.render.calls] == [("player", "rounds")]


def test_turning_a_view_off_takes_it_off_every_waiting_demos_queue(world, caplog):
    first = world.add_demo()
    second = world.add_demo(SECOND_DEMO, "1" * 64)
    world.ticks(4)                                    # both Demos are rendering, each with both views queued
    world.use(perspectives="player")
    with caplog.at_level(logging.INFO, logger="clipper.worker"):
        world.ticks(1)                                # the enemy views come off; the first Demo's player view renders
    assert world.index.demo(second)["state"] == "rendering"
    assert world.index.latest_render(first, "enemy") is None
    assert world.index.latest_render(second, "enemy") is None
    assert "the enemy view is off in Settings: 2 queued renders taken off the queue" in caplog.messages
    world.ticks(6)
    assert [(call.demo_path.name, call.perspectives) for call in world.render.calls] == [
        (f"{DEMO_NAME}.dem", ("player",)), (f"{SECOND_DEMO}.dem", ("player",))]
    assert world.index.demo(first)["state"] == world.index.demo(second)["state"] == "done"


def test_a_view_rendered_before_it_was_turned_off_still_becomes_reels(world):
    demo_id = world.add_demo()
    world.ticks(5)                                    # both views are rendered, not yet joined
    world.use(perspectives="enemy")
    world.ticks(2)
    assert world.index.demo(demo_id)["state"] == "done"
    assert world.index.reel_count(MATCH_CHECKSUM) == 10


def test_each_render_is_given_the_checksum_the_analysis_gave_its_demo(world):
    world.add_demo()
    world.add_demo(SECOND_DEMO, "1" * 64)
    world.ticks(12)
    assert [(call.demo_path.name, call.checksum) for call in world.render.calls] == [
        (f"{DEMO_NAME}.dem", MATCH_CHECKSUM), (f"{SECOND_DEMO}.dem", SECOND_CHECKSUM),
    ]


def test_the_next_demo_waits_while_the_first_retries_a_failed_view(world):
    world.render.results = [RenderResult(ok=False, failure="stalled: no ffmpeg for 180s while CS2 ran")]
    world.add_demo()
    world.add_demo(SECOND_DEMO, "1" * 64)
    world.ticks(13)
    assert [(call.demo_path.name, call.perspectives) for call in world.render.calls] == [
        (f"{DEMO_NAME}.dem", ("player", "enemy")), (f"{DEMO_NAME}.dem", ("player",)),
        (f"{DEMO_NAME}.dem", ("enemy",)), (f"{SECOND_DEMO}.dem", ("player", "enemy")),
    ]


def test_each_demo_is_analyzed_once(world):
    world.add_demo()
    world.ticks(8)
    assert world.facts.analyzed == [f"{DEMO_NAME}.dem"]


def test_a_demo_with_no_analysis_kept_is_analyzed_again_before_it_is_scored(world):
    """Demos analyzed before the app kept its own analyses have none in its folder."""
    demo_id = world.add_demo()
    world.ticks(2)                                  # analyzed
    world.facts.kept.clear()
    world.ticks(1)
    assert world.index.demo(demo_id)["state"] == "scored"
    assert world.facts.analyzed == [f"{DEMO_NAME}.dem"] * 2


def test_a_demo_with_no_analysis_kept_is_analyzed_again_before_it_is_rendered(world):
    demo_id = world.add_demo()
    world.ticks(4)                                  # rendering, nothing rendered yet
    world.facts.kept.clear()
    world.ticks(1)
    assert world.facts.analyzed == [f"{DEMO_NAME}.dem"] * 2
    assert [call.perspectives for call in world.render.calls] == [("player", "enemy")]
    assert world.index.demo(demo_id)["state"] == "rendering"


def test_a_demo_whose_analysis_now_gives_another_match_fails(world):
    demo_id = world.add_demo()
    world.ticks(2)
    world.facts.kept.clear()
    world.facts.checksums[DEMO_NAME] = "00000000000000c3"
    world.ticks(1)
    demo = world.index.demo(demo_id)
    assert demo["state"] == "failed"
    assert demo["last_error"] == f"analyzing the Demo again gave match 00000000000000c3, not {MATCH_CHECKSUM}"


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


def test_three_failed_renders_fail_the_demo_and_pause_rendering(world):
    world.render.results = [RenderResult(ok=False, failure="CS2 closed before the render finished")] * 3
    demo_id = world.add_demo()
    world.ticks(8)
    demo = world.index.demo(demo_id)
    assert demo["state"] == "failed"
    assert demo["last_error"] == "player render failed 3 times: CS2 closed before the render finished"
    assert world.index.get_flag("paused") == "1"
    assert world.index.paused_by() == "failures"
    assert "Rendering paused" in world.titles()


def test_every_failed_render_attempt_gets_its_own_log_line(world, caplog):
    world.render.results = [RenderResult(ok=False, failure="stalled: no ffmpeg for 180s while CS2 ran"),
                            RenderResult(ok=False, failure="HLAE error: HLAE.exe exited with code 1")]
    demo_id = world.add_demo()
    with caplog.at_level(logging.WARNING, logger="clipper.worker"):
        world.ticks(6)
    assert [r.getMessage() for r in caplog.records if r.name == "clipper.worker"] == [
        f"demo #{demo_id} (de_inferno): player render attempt 1 of 3 failed: "
        "stalled: no ffmpeg for 180s while CS2 ran",
        f"demo #{demo_id} (de_inferno): enemy render attempt 1 of 3 failed: "
        "stalled: no ffmpeg for 180s while CS2 ran",
        f"demo #{demo_id} (de_inferno): player render attempt 2 of 3 failed: HLAE error: HLAE.exe exited with code 1",
    ]


def test_an_aborted_render_is_tried_again_without_counting(world):
    world.use(perspectives="player")                  # one view: the abort and its retry are that view's launches
    world.render.results = [RenderResult(ok=False, aborted=True,
                                         failure="aborted: FACEIT AC started during the render")]
    demo_id = world.add_demo()
    world.ticks(9)
    assert world.index.demo(demo_id)["state"] == "done"
    assert [call.perspectives for call in world.render.calls] == [("player",), ("player",)]
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


# --- deleting a Demo -----------------------------------------------------------------------------


def _write(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def test_deleting_a_demo_takes_its_download_its_dem_its_renders_and_analysis_but_not_its_logs(world, tmp_path):
    demo_id = world.add_demo()
    world.ticks(3)                                  # unpacked and analyzed: it has a .dem and a match
    cfg = replace(world.cfg, logs_dir=tmp_path / "logs")
    files = [_write(cfg.demos_dir / f"{DEMO_NAME}.dem.zst"), _write(cfg.demos_dir / f"{DEMO_NAME}.dem"),
             _write(cfg.renders_dir / MATCH_CHECKSUM / "player" / "attempt-1" / "sequence-1.mp4"),
             _write(cfg.analyses_dir / f"{MATCH_CHECKSUM}.json")]
    log_file = _write(cfg.logs_dir / f"demo-{demo_id}-analyze.log")
    assert delete_demo(world.index, cfg, demo_id) is True
    assert world.index.demo(demo_id) is None
    assert [path.exists() for path in files] == [False, False, False, False]
    assert not (cfg.renders_dir / MATCH_CHECKSUM).exists()
    assert log_file.exists()
    assert delete_demo(world.index, cfg, demo_id) is False      # nothing left to delete


def test_deleting_a_demo_leaves_the_renders_and_analysis_another_demo_of_its_match_has(world):
    first = world.add_demo()
    world.add_demo("1-00000000-0000-4000-8000-000000000003-1-1", "3" * 64)   # the same match, another file
    world.ticks(3)
    clip = _write(world.cfg.renders_dir / MATCH_CHECKSUM / "player" / "attempt-1" / "sequence-1.mp4")
    analysis = _write(world.cfg.analyses_dir / f"{MATCH_CHECKSUM}.json")
    assert delete_demo(world.index, world.cfg, first) is True
    assert clip.exists() and analysis.exists()


def test_deleting_the_demo_being_rendered_aborts_its_render_then_deletes_it(world):
    seen = {}

    def render_while_deleted(request, should_abort, progress):
        seen["before"] = should_abort()
        seen["delete"] = world.deletes.delete(demo_id, lambda: pytest.fail("deleted under the worker"))
        seen["after"] = should_abort()
        return RenderResult(ok=False, aborted=True, failure=ABORTED)

    world.services.render = render_while_deleted
    demo_id = world.add_demo()
    world.ticks(5)
    assert seen == {"before": False, "delete": "deferred", "after": True}
    assert world.index.demo(demo_id) is None


def test_a_delete_during_the_heads_up_keeps_cs2_closed(world):
    def sleep_while_deleted(seconds):
        world.deletes.delete(demo_id, lambda: pytest.fail("deleted under the worker"))

    world.services.sleep = sleep_while_deleted
    demo_id = world.add_demo()
    world.ticks(5)
    assert world.render.calls == []
    assert world.index.demo(demo_id) is None


def test_a_demo_deleted_after_the_tick_listed_it_gets_no_step(world):
    unpacked = []

    def unpack_and_delete_the_other(archive, out_dir):
        unpacked.append(archive.name)
        world.deletes.delete(second, lambda: delete_demo(world.index, world.cfg, second))
        return out_dir / archive.name.removesuffix(".zst")

    world.services.unpack = unpack_and_delete_the_other
    world.add_demo()
    second = world.add_demo(SECOND_DEMO, "1" * 64)
    world.ticks(1)
    assert unpacked == [f"{DEMO_NAME}.dem.zst"]
    assert world.index.demo(second) is None


def test_delete_request_keeps_the_worker_off_a_demo_while_it_goes():
    deletes = DeleteRequest()
    claimed = []
    assert deletes.delete(7, lambda: claimed.append(deletes.claim(7)) or True) == "deleted"
    assert claimed == [False]
    assert deletes.claim(7)                         # gone: nothing holds the id any more
    assert deletes.delete(9, lambda: False) is None   # no such Demo


def test_delete_request_waits_for_the_step_the_worker_is_taking():
    deletes = DeleteRequest()
    removed = []
    assert deletes.claim(7)
    assert deletes.delete(7, lambda: pytest.fail("deleted under the worker")) == "deferred"
    assert deletes.delete(7, lambda: pytest.fail("deleted twice")) == "deferred"
    assert deletes.asked(7) and not deletes.asked(8)
    deletes.release(7, lambda: removed.append(7))
    assert removed == [7] and not deletes.asked(7)
    assert deletes.claim(8)
    deletes.release(8, lambda: pytest.fail("no delete was asked"))


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

    def render_and_snapshot(request, should_abort, progress):
        seen["rendering"] = world.state.snapshot().rendering
        return RenderResult(ok=True, clips=clips_of(request))

    world.services.render = render_and_snapshot
    world.add_demo()
    world.ticks(5)   # spotted -> unpacked -> analyzed -> scored -> rendering (queues) -> the render of both views
    rendering = seen["rendering"]
    assert rendering is not None
    assert (rendering.map_name, rendering.perspective) == ("de_inferno", "both")
    snap = world.state.snapshot()
    assert snap.rendering is None   # cleared by the end of the tick that started it
    assert snap.waiting == ()


def test_progress_the_render_reports_reaches_the_state_while_it_runs(world):
    seen = {}
    report = RenderProgress("recording", "player", done=1, total=2, overall=0.25, seconds_left=40.0)

    def render_with_progress(request, should_abort, progress):
        seen["before"] = world.state.snapshot().rendering.progress
        progress(report)
        seen["during"] = world.state.snapshot().rendering.progress
        return RenderResult(ok=True, clips=clips_of(request))

    world.services.render = render_with_progress
    world.add_demo()
    world.ticks(5)
    assert seen == {"before": None, "during": report}
    assert world.state.snapshot().rendering is None   # cleared with the rest of the Rendering


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

    def render_spy(request, should_abort, progress):
        calls.append(request)
        return RenderResult(ok=True, clips={})

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
    def quit_after_this_render(request):
        world.stop.request("after_render")
        return RenderResult(ok=True, clips=clips_of(request))

    world.render.results = [RenderResult(ok=False, failure="stalled: no ffmpeg for 180s while CS2 ran"),
                            quit_after_this_render]   # both views fail; then the player view alone, and quit
    alerts = _CountingAlerts()
    world.services.alerts = alerts
    demo_id = world.add_demo()
    world.ticks(6)
    assert world.index.latest_render(demo_id, "player")["state"] == "done"
    assert world.index.latest_render(demo_id, "enemy")["state"] == "queued"   # the enemy view was not started
    assert world.index.demo(demo_id)["state"] == "rendering"
    assert len(world.render.calls) == 2
    assert alerts.ticks == 5   # ticks 1-5 ran alerts; tick 6's alerts step was skipped once stopping
    world.ticks(3)   # nothing more should ever happen
    assert len(world.render.calls) == 2
    assert world.index.demo(demo_id)["state"] == "rendering"
    assert alerts.ticks == 5


def test_quit_now_during_a_render_aborts_it_like_a_faceit_ac_abort(world):
    """The renderer asks `should_abort` at every poll (test_hlae_render) and stops the way a FACEIT AC
    abort stops it; here Quit now arrives from the tray or the Status page mid-render."""
    world.use(perspectives="player")                  # one view: the render Quit now stops is that view's

    def render_until_quit(request, should_abort, progress):
        assert not should_abort()
        world.stop.request("now")
        assert should_abort()
        return RenderResult(ok=False, aborted=True, failure=ABORTED)

    world.services.render = render_until_quit
    alerts = _CountingAlerts()
    world.services.alerts = alerts
    demo_id = world.add_demo()
    world.ticks(5)
    job = world.index.latest_render(demo_id, "player")
    assert (job["state"], job["failure"]) == ("aborted", QUIT_ABORT)
    assert world.index.demo(demo_id)["state"] == "rendering"
    assert alerts.ticks == 4   # this tick's alerts step was skipped once the abort set stop


def test_quit_now_during_a_two_view_launch_aborts_both_views(world):
    def render_until_quit(request, should_abort, progress):
        world.stop.request("now")
        assert should_abort()
        return RenderResult(ok=False, aborted=True, failure=ABORTED)

    world.services.render = render_until_quit
    demo_id = world.add_demo()
    world.ticks(5)
    jobs = [world.index.latest_render(demo_id, perspective) for perspective in ("player", "enemy")]
    assert [(job["state"], job["failure"]) for job in jobs] == [("aborted", QUIT_ABORT)] * 2
    assert world.index.demo(demo_id)["state"] == "rendering"


# --- raw Clips: deleted once their Reels are made ------------------------------------------------


def test_a_finished_match_loses_its_raw_clips_and_keeps_its_reels_and_the_library(world):
    world.add_demo()
    _write(world.cfg.renders_dir / MATCH_CHECKSUM / "player" / "attempt-1" / "sequence-1.mp4")
    _write(world.cfg.renders_dir / MATCH_CHECKSUM / "enemy" / "attempt-1" / "sequence-1.mp4")
    reel = _write(world.cfg.library_dir / "videos" / MATCH_CHECKSUM / "r3-player.mp4")
    world.ticks(8)
    assert not (world.cfg.renders_dir / MATCH_CHECKSUM).exists()
    assert world.index.reel_count(MATCH_CHECKSUM) == 10
    assert reel.exists()


def test_a_view_added_to_a_finished_match_joins_alone(world):
    """The finished match's raw Clips are gone, so the view it already has must not be joined again."""
    world.use(perspectives="player")
    demo_id = world.add_demo()
    world.ticks(7)                                  # done with the player view only
    assert world.index.demo(demo_id)["state"] == "done"
    world.use(perspectives="both")
    joined = []

    def record_join(clips, out):
        joined.append(out.name)
        return 4.0 * len(clips)

    world.services.join = record_join
    world.index.advance(demo_id, "rendering")       # back to rendering: the enemy view is rendered now
    world.ticks(3)
    assert [call.perspectives[0] for call in world.render.calls] == ["player", "enemy"]
    assert joined == [f"r{round_}-enemy.mp4" for round_ in (3, 4, 8, 12, 14)]
    assert world.index.demo(demo_id)["state"] == "done"
    assert world.index.reel_count(MATCH_CHECKSUM) == 10


def test_a_new_worker_takes_leftover_renders_of_done_matches_and_keeps_those_of_failed_demos(world):
    world.add_demo()
    world.ticks(8)
    _write(world.cfg.renders_dir / MATCH_CHECKSUM / "player" / "attempt-1" / "sequence-1.mp4")
    failed = world.add_demo(SECOND_DEMO, "1" * 64)
    world.index.advance(failed, "analyzed", match_checksum=SECOND_CHECKSUM)
    world.index.fail(failed, "x")
    kept = _write(world.cfg.renders_dir / SECOND_CHECKSUM / "player" / "attempt-1" / "sequence-1.mp4")
    world.use()                                     # a new Worker, as after a restart
    world.ticks(1)
    assert not (world.cfg.renders_dir / MATCH_CHECKSUM).exists()
    assert kept.exists()
    assert world.index.reel_count(MATCH_CHECKSUM) == 10


def test_a_match_keeps_its_renders_until_every_demo_of_it_is_done(world):
    clip = _write(world.cfg.renders_dir / MATCH_CHECKSUM / "player" / "attempt-1" / "sequence-1.mp4")
    done = world.add_demo()
    world.index.advance(done, "done", match_checksum=MATCH_CHECKSUM)
    other = world.add_demo(SECOND_DEMO, "1" * 64)
    world.index.advance(other, "rendering", match_checksum=MATCH_CHECKSUM)
    assert prune_renders(world.index, world.cfg, MATCH_CHECKSUM) is False
    assert clip.exists()
    world.index.advance(other, "done")
    assert prune_renders(world.index, world.cfg, MATCH_CHECKSUM) is True
    assert not clip.exists()
    assert prune_renders(world.index, world.cfg, MATCH_CHECKSUM) is False    # nothing left to remove


# --- one heads-up per match ----------------------------------------------------------------------


def test_a_failed_render_retried_straight_away_gets_no_new_heads_up(world, caplog):
    world.render.results = [RenderResult(ok=False, failure="stalled: no ffmpeg for 180s while CS2 ran")]
    world.add_demo()
    with caplog.at_level(logging.INFO, logger="clipper.worker"):
        world.ticks(9)
    assert [call.perspectives for call in world.render.calls] == [("player", "enemy"), ("player",), ("enemy",)]
    assert world.titles() == ["Rendering highlights", "Highlights ready"]
    assert sum("no heads-up" in message for message in caplog.messages) == 2   # the retry and the enemy view


@pytest.mark.parametrize(("seconds_later", "heads_up_again"), [(119.9, False), (120.0, True), (121.0, True)])
def test_a_retry_gets_a_heads_up_again_only_once_two_minutes_have_passed(world, seconds_later, heads_up_again):
    world.render.results = [RenderResult(ok=False, failure="stalled: no ffmpeg for 180s while CS2 ran")]
    world.add_demo()
    world.ticks(5)                                    # both views fail in one launch: one heads-up so far
    world.clock.now += seconds_later
    world.ticks(1)                                    # the player view, tried again on its own
    assert world.titles() == ["Rendering highlights"] * (2 if heads_up_again else 1)


def test_the_retry_after_an_aborted_render_gets_a_heads_up(world):
    world.render.results = [RenderResult(ok=False, failure="stalled: no ffmpeg for 180s while CS2 ran"),
                            RenderResult(ok=False, aborted=True, failure=ABORTED)]
    demo_id = world.add_demo()
    world.ticks(6)                                    # both views fail, then the player view is aborted
    world.ticks(3)                                    # the player view, tried again with a heads-up; then the enemy
    assert world.titles() == ["Rendering highlights", "Rendering highlights"]
    assert world.index.latest_render(demo_id, "enemy")["state"] == "done"


def test_a_second_demo_gets_its_own_heads_up(world):
    world.add_demo()
    world.add_demo(SECOND_DEMO, "1" * 64)
    world.ticks(12)
    assert world.titles() == ["Rendering highlights", "Highlights ready", "Rendering highlights", "Highlights ready"]


def test_a_render_straight_after_another_still_waits_for_the_gate_and_a_pause(world):
    world.render.results = [RenderResult(ok=False, failure="stalled: no ffmpeg for 180s while CS2 ran")]
    world.add_demo()
    world.ticks(5)                                    # both views fail in one launch: CS2 has just closed
    world.gate.reasons = ("FACEIT AC is running",)
    world.ticks(1)                                    # the player view's retry, straight after
    assert len(world.render.calls) == 1
    assert world.state.snapshot().waiting == ("FACEIT AC is running",)
    world.gate.reasons = ()
    world.index.pause("you")
    world.ticks(1)
    assert len(world.render.calls) == 1
    world.index.resume()
    world.ticks(1)
    assert [call.perspectives for call in world.render.calls] == [("player", "enemy"), ("player",)]
    assert world.titles() == ["Rendering highlights"] * 2        # the Gate closed: someone was at the PC


def test_a_stop_asked_while_the_analysis_is_made_again_keeps_cs2_closed(world):
    world.render.results = [RenderResult(ok=False, failure="stalled: no ffmpeg for 180s while CS2 ran")]
    demo_id = world.add_demo()
    world.ticks(5)                                    # both views fail in one launch: CS2 has just closed
    world.facts.kept.clear()                          # the next launch needs its Demo analyzed again
    analyze = world.services.analyze

    def analyze_while_stopped(dem, log_path):
        world.stop.request("after_render")
        return analyze(dem, log_path)

    world.services.analyze = analyze_while_stopped
    world.ticks(1)
    assert len(world.render.calls) == 1
    assert world.index.latest_render(demo_id, "player")["state"] == "queued"
    assert world.index.latest_render(demo_id, "enemy")["state"] == "queued"


def test_a_gate_that_closes_between_views_brings_the_heads_up_back(world):
    world.render.results = [RenderResult(ok=False, failure="stalled: no ffmpeg for 180s while CS2 ran")]
    world.add_demo()
    world.ticks(6)                                    # both views fail in one launch; then the player view alone
    world.gate.reasons = ("CS2 is running",)          # someone is at the PC
    world.ticks(1)                                    # the enemy view waits
    world.gate.reasons = ()
    world.ticks(1)                                    # the enemy view, with a heads-up again
    assert [call.perspectives for call in world.render.calls] == [("player", "enemy"), ("player",), ("enemy",)]
    assert world.titles().count("Rendering highlights") == 2
