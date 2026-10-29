"""The worker's part of the stats: FACEIT AC running holds the Demo steps as CS2 does, an analysis adds the FACEIT line,
and the stats backfill runs only once the pipeline has nothing to do."""

from __future__ import annotations

from clipper.gate import FACEIT_REASON
from tests.fixtures import MATCH_CHECKSUM
from tests.test_worker import world  # noqa: F401 - the fixture


class FakeBackfill:
    def __init__(self):
        self.added = []
        self.ticks = 0

    def add_faceit(self, demo, checksum):
        self.added.append(checksum)

    def tick(self, clear):
        self.ticks += 1


def test_faceit_ac_running_holds_the_demo_steps_as_cs2_does(world):  # noqa: F811
    demo_id = world.add_demo()
    world.gate.faceit_open = True
    world.ticks(3)
    assert world.index.demo(demo_id)["state"] == "spotted"
    assert world.worker._waiting_reasons == (FACEIT_REASON,)
    world.gate.faceit_open = False
    world.ticks(1)
    assert world.index.demo(demo_id)["state"] == "unpacked"


def test_an_analysis_adds_the_faceit_line_to_the_match_stats(world):  # noqa: F811
    world.services.stats_backfill = FakeBackfill()
    demo_id = world.add_demo()
    world.ticks(2)                                    # spotted, then unpacked and analyzed
    assert world.index.demo(demo_id)["state"] == "analyzed"
    assert world.services.stats_backfill.added == [MATCH_CHECKSUM]


def test_the_stats_backfill_waits_until_the_pipeline_is_idle(world):  # noqa: F811
    backfill = FakeBackfill()
    world.services.stats_backfill = backfill
    world.add_demo()
    world.ticks(1)
    assert backfill.ticks == 0                        # a Demo is moving: the backfill waits
    world.ticks(12)
    assert backfill.ticks >= 1                        # the Demo is done, so the pipeline is idle
