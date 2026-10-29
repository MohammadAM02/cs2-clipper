"""The stats backfill: finished matches from before the stats existed get them, one a tick, only with the game closed,
and a failure waits a day. csda and FACEIT are fakes; the archive, the index and the folders are real."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipper import analysis
from clipper.backfill import DONE, StatsBackfill
from clipper.config import Config
from clipper.index import Index
from clipper.intake import faceit_match_id
from tests.csda_json import CHECKSUM, DEMO_NAME, match

T0 = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
MATCH_ID_OF_DEMO = DEMO_NAME.rsplit("-", 2)[0]        # the FACEIT match id a demo's file name starts with


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


class FakeCsda:
    """Writes the match where csda writes it: <output>/<the Demo's name without .dem>.json."""

    def __init__(self):
        self.calls = 0
        self.fail = False

    def __call__(self, command, log_path):
        self.calls += 1
        log_path.parent.mkdir(parents=True, exist_ok=True)
        if self.fail:
            raise RuntimeError("csda crashed")
        demo = Path(command[command.index("-demo-path") + 1])
        out = Path(command[command.index("-output") + 1])
        (out / f"{demo.stem}.json").write_text(json.dumps(match()), encoding="utf-8")
        return 0


@pytest.fixture
def world(tmp_path):
    cfg = Config(data_root=tmp_path / "clips", index_path=tmp_path / "index.sqlite", logs_dir=tmp_path / "logs",
                 analyses_dir=tmp_path / "analyses", tools_dir=tmp_path / "tools", cs2_settings_dir=tmp_path / "cs2",
                 downloads_dir=tmp_path / "downloads", subject_steamid="76561190000000001")
    archive = cfg.downloads_dir / f"{DEMO_NAME}.dem.zst"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"compressed")
    index = Index(cfg.index_path)
    demo_id = index.add_demo(archive.name, "0" * 64, archive)
    index.advance(demo_id, "done", match_checksum=CHECKSUM)
    csda, clock, unpacked, faceit_calls = FakeCsda(), Clock(), [], []

    def unpack(src, out_dir):
        dem = out_dir / f"{DEMO_NAME}.dem"
        dem.parent.mkdir(parents=True, exist_ok=True)
        dem.write_bytes(b"HL2DEMO")
        unpacked.append(dem)
        return dem

    def faceit_stats(checksum, match_id):
        faceit_calls.append((checksum, match_id))
        return True

    analyses = analysis.Analyses(cfg.analyses_dir, tmp_path / "csda.exe", run=csda)
    backfill = StatsBackfill(index, cfg, analyses, unpack, faceit_stats, now=clock)
    yield SimpleNamespace(cfg=cfg, index=index, analyses=analyses, csda=csda, clock=clock, backfill=backfill,
                          unpacked=unpacked, faceit_calls=faceit_calls, demo_id=demo_id)
    index.close()


def test_a_match_with_no_stats_is_worked_out_and_its_faceit_line_added(world):
    world.backfill.tick(lambda: True)
    assert world.analyses.has_stats(CHECKSUM)
    assert world.csda.calls == 1
    assert world.faceit_calls == [(CHECKSUM, MATCH_ID_OF_DEMO)]
    assert not (world.cfg.demos_dir / f"{DEMO_NAME}.dem").exists()       # the unpacked Demo is removed


def test_nothing_is_worked_out_while_the_game_holds_the_pipeline(world):
    world.backfill.tick(lambda: False)
    assert world.csda.calls == 0
    assert not world.analyses.has_stats(CHECKSUM)
    assert world.faceit_calls == []


def test_a_match_with_its_stats_and_faceit_line_is_left_alone(world):
    world.backfill.tick(lambda: True)
    world.csda.calls = 0
    world.faceit_calls.clear()
    world.backfill.tick(lambda: True)
    assert world.csda.calls == 0
    assert world.faceit_calls == []


def test_a_failed_analysis_is_tried_again_a_day_later(world):
    world.csda.fail = True
    world.backfill.tick(lambda: True)
    assert not world.analyses.has_stats(CHECKSUM)
    world.backfill.tick(lambda: True)
    assert world.csda.calls == 1                                        # not again on the next tick
    world.clock.now += timedelta(hours=25)
    world.csda.fail = False
    world.backfill.tick(lambda: True)
    assert world.analyses.has_stats(CHECKSUM)


def test_a_failed_faceit_request_is_tried_again_a_day_later(world):
    attempts = []

    def failing(checksum, match_id):
        attempts.append(match_id)
        raise OSError("network down")

    world.backfill.faceit_stats = failing
    world.backfill.tick(lambda: True)                                   # the stats are worked out; FACEIT fails
    world.backfill.tick(lambda: True)                                   # not again straight away
    assert len(attempts) == 1
    world.clock.now += timedelta(hours=25)
    world.backfill.faceit_stats = lambda checksum, match_id: attempts.append(match_id) or True
    world.backfill.tick(lambda: True)
    assert len(attempts) == 2
    assert world.index.get_flag(f"faceit_stats:{CHECKSUM}") == DONE


def test_without_a_faceit_key_only_the_stats_are_worked_out(world):
    world.backfill.faceit_stats = None
    world.backfill.tick(lambda: True)
    world.backfill.tick(lambda: True)
    assert world.analyses.has_stats(CHECKSUM)
    assert world.faceit_calls == []


def test_a_demo_not_named_as_a_faceit_match_has_no_line_to_add(world):
    world.backfill.add_faceit({"archive_path": "downloads/not-faceit.dem.zst", "id": 9}, CHECKSUM)
    assert world.faceit_calls == []
    assert world.index.get_flag(f"faceit_stats:{CHECKSUM}") == DONE


def test_the_faceit_match_id_is_read_from_a_demo_file_name():
    assert faceit_match_id(f"{DEMO_NAME}.dem.zst") == MATCH_ID_OF_DEMO
    assert faceit_match_id("1-00000000-0000-4000-8000-0000000000aa-1-1.dem") == MATCH_ID_OF_DEMO
    assert faceit_match_id("my-demo.dem.zst") is None
