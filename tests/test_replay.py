"""The replay's positions: the piece-by-piece reader, the once-a-second sampling, csda's positions run, and the backfill
that works a request out when CS2 and FACEIT AC are both closed. csda and the demo are fakes; the files are real."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipper import replay
from clipper.analysis import Analyses, MissingAnalysis
from clipper.backfill import REPLAY_REQUESTED, StatsBackfill
from clipper.config import Config
from clipper.index import Index
from tests.csda_json import CHECKSUM, DEMO_NAME, ENEMY, MATCH, SUBJECT

T0 = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
ROW = [0, 1, str(SUBJECT), 1.0, 2.0, 90.0, True, 2, 100]


def position(tick: int, steam: int = SUBJECT, *, alive: bool = True, x: float = 1.0) -> dict:
    return {"tick": tick, "roundNumber": 1, "steamId": steam, "x": x, "y": 2.0, "yaw": 90.0, "isAlive": alive,
            "side": 2, "health": 100, "name": "subject"}


class CsdaRun:
    """What csda writes: `data` for a run without positions, `positions` (None writes a null) for a run with them."""

    def __init__(self, data: dict, positions=None):
        self.data = data
        self.positions = positions
        self.commands: list[list[str]] = []

    def __call__(self, command, log_path):
        self.commands.append(list(command))
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("csda\n", encoding="utf-8")
        demo = Path(command[command.index("-demo-path") + 1])
        out = Path(command[command.index("-output") + 1])
        payload = self.positions if "-positions" in command else self.data
        if "-positions" in command:
            payload = {**self.data, "playerPositions": payload}
        (out / f"{demo.stem}.json").write_text(json.dumps(payload), encoding="utf-8")
        return 0

    def positions_runs(self) -> list[list[str]]:
        return [c for c in self.commands if "-positions" in c]


def test_the_array_is_read_object_by_object_whatever_the_size_of_the_pieces(tmp_path):
    records = [{"tick": 64, "name": 'é{[]}"x'}, {"tick": 128, "name": "two"}]
    path = tmp_path / "out.json"
    path.write_text(json.dumps({"checksum": "abc", "kills": [{"tick": 5}], "playerPositions": records, "after": 1},
                               separators=(",", ":")), encoding="utf-8")
    for piece in (1, 2, 3, 7, 64, 1 << 20):
        assert list(replay.iter_array(path, "playerPositions", piece)) == records


def test_a_missing_or_empty_array_gives_no_records(tmp_path):
    path = tmp_path / "out.json"
    path.write_text('{"checksum":"abc"}', encoding="utf-8")
    assert list(replay.iter_array(path, "playerPositions")) == []
    path.write_text('{"playerPositions":[]}', encoding="utf-8")
    assert list(replay.iter_array(path, "playerPositions", 3)) == []
    path.write_text('{"playerPositions":null}', encoding="utf-8")
    assert list(replay.iter_array(path, "playerPositions")) == []


def test_only_the_frames_on_a_whole_second_are_kept_as_compact_rows(tmp_path):
    path = tmp_path / "out.json"
    records = [position(0), position(1), position(64), position(65, ENEMY, x=3.5), position(128, alive=False)]
    path.write_text(json.dumps({"playerPositions": records}, separators=(",", ":")), encoding="utf-8")
    assert replay.sample_file(path, 64.0) == [
        ROW, [64, 1, str(SUBJECT), 1.0, 2.0, 90.0, True, 2, 100],
        [128, 1, str(SUBJECT), 1.0, 2.0, 90.0, False, 2, 100],
    ]
    assert replay.FIELDS == ("tick", "round", "steam_id", "x", "y", "yaw", "alive", "side", "health")


def test_a_run_with_positions_is_kept_by_checksum_and_its_csda_output_removed(tmp_path):
    csda = CsdaRun(MATCH, positions=[position(0), position(64), position(70)])
    store = Analyses(tmp_path / "analyses", tmp_path / "csda.exe", run=csda)
    dem = tmp_path / "demos" / f"{DEMO_NAME}.dem"
    dem.parent.mkdir()
    dem.write_bytes(b"HL2DEMO")
    store.analyze(dem, tmp_path / "analyze.log")
    kept = store.positions(CHECKSUM, dem, tmp_path / "positions.log")
    assert kept.name == f"{CHECKSUM}.positions.json"
    [command] = csda.positions_runs()
    assert "-positions" in command and "-minify" in command
    assert store.positions_of(CHECKSUM)["samples"] == [ROW, [64, 1, str(SUBJECT), 1.0, 2.0, 90.0, True, 2, 100]]
    assert store.has_positions(CHECKSUM)
    assert sorted(p.name for p in (tmp_path / "analyses").iterdir()) == \
        sorted([f"{CHECKSUM}.json", f"{CHECKSUM}.stats.json", f"{CHECKSUM}.positions.json"])
    store.forget(CHECKSUM)
    assert not store.has_positions(CHECKSUM)


def test_a_run_that_gives_no_positions_is_a_failure_not_an_empty_replay(tmp_path):
    csda = CsdaRun(MATCH, positions=None)
    store = Analyses(tmp_path / "analyses", tmp_path / "csda.exe", run=csda)
    dem = tmp_path / "demos" / f"{DEMO_NAME}.dem"
    dem.parent.mkdir()
    dem.write_bytes(b"HL2DEMO")
    store.analyze(dem, tmp_path / "analyze.log")
    with pytest.raises(RuntimeError, match="csda gave no player positions"):
        store.positions(CHECKSUM, dem, tmp_path / "positions.log")
    assert not store.has_positions(CHECKSUM)


def test_positions_need_the_match_analysed_first(tmp_path):
    store = Analyses(tmp_path / "analyses", tmp_path / "csda.exe", run=CsdaRun(MATCH))
    with pytest.raises(MissingAnalysis):
        store.positions(CHECKSUM, tmp_path / "x.dem", tmp_path / "log.txt")
    assert store.positions_of(CHECKSUM) is None


@pytest.fixture
def world(tmp_path):
    cfg = Config(data_root=tmp_path / "clips", index_path=tmp_path / "index.sqlite", logs_dir=tmp_path / "logs",
                 analyses_dir=tmp_path / "analyses", tools_dir=tmp_path / "tools", cs2_settings_dir=tmp_path / "cs2",
                 downloads_dir=tmp_path / "downloads", radar_dir=tmp_path / "radar", subject_steamid=str(SUBJECT))
    archive = cfg.downloads_dir / f"{DEMO_NAME}.dem.zst"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(b"compressed")
    index = Index(cfg.index_path)
    demo_id = index.add_demo(archive.name, "0" * 64, archive)
    index.advance(demo_id, "done", match_checksum=CHECKSUM)
    clock = SimpleNamespace(now=T0)
    csda = CsdaRun(MATCH, positions=[position(0), position(64)])
    unpacked = []

    def unpack(src, out_dir):
        dem = out_dir / f"{DEMO_NAME}.dem"
        dem.parent.mkdir(parents=True, exist_ok=True)
        dem.write_bytes(b"HL2DEMO")
        unpacked.append(dem)
        return dem

    analyses = Analyses(cfg.analyses_dir, tmp_path / "csda.exe", run=csda)
    seed = tmp_path / "seed" / f"{DEMO_NAME}.dem"
    seed.parent.mkdir()
    seed.write_bytes(b"HL2DEMO")
    analyses.analyze(seed, tmp_path / "seed.log")              # the match's analysis, as the pipeline keeps it
    backfill = StatsBackfill(index, cfg, analyses, unpack, None, now=lambda: clock.now)
    yield SimpleNamespace(cfg=cfg, index=index, analyses=analyses, csda=csda, backfill=backfill, unpacked=unpacked,
                          clock=clock)
    index.close()


def test_a_requested_replay_is_worked_out_only_when_the_game_is_closed(world):
    world.backfill.request_replay(CHECKSUM)
    world.backfill.tick(lambda: False)                          # CS2 or FACEIT AC open: nothing is done
    assert not world.analyses.has_positions(CHECKSUM)
    assert world.csda.positions_runs() == []
    world.backfill.tick(lambda: True)
    assert world.analyses.has_positions(CHECKSUM)
    assert world.index.get_flag(REPLAY_REQUESTED + CHECKSUM) is None
    assert len(world.csda.positions_runs()) == 1
    assert not any(p.exists() for p in world.unpacked)          # the unpacked Demo is removed afterwards


def test_a_replay_for_a_match_with_no_finished_demo_is_dropped(world):
    world.backfill.request_replay("fedcba9876543210")
    world.backfill.tick(lambda: True)
    assert world.index.get_flag(REPLAY_REQUESTED + "fedcba9876543210") is None
    assert world.csda.positions_runs() == []


def test_a_replay_that_fails_is_tried_again_only_a_day_later(world):
    world.csda.positions = None                                 # csda gives no positions: the run fails
    world.backfill.request_replay(CHECKSUM)
    world.backfill.tick(lambda: True)
    assert not world.analyses.has_positions(CHECKSUM)
    world.backfill.tick(lambda: True)
    assert len(world.csda.positions_runs()) == 1                # not again on the next tick
    world.clock.now += timedelta(hours=25)
    world.csda.positions = [position(0)]
    world.backfill.tick(lambda: True)
    assert world.analyses.has_positions(CHECKSUM)
