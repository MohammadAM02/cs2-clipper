import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clipper.index import Index
from clipper.model import ClipFile, Highlight, MatchInfo

MATCH = MatchInfo(checksum="aea4e59ccfc6c962", map_name="de_inferno",
                  played_at=datetime(2026, 9, 22, 9, 39, 14, tzinfo=timezone.utc),
                  team_score=13, opponent_score=5)


def highlight(round_, score, start):
    return Highlight(round=round_, type="3K", score=score, reasons=("3k",), frag_ticks=(start + 100,),
                     round_start_tick=start, round_end_tick=start + 5000)


@pytest.fixture
def index(tmp_path):
    idx = Index(tmp_path / "clipper.sqlite")
    yield idx
    idx.close()


def add(index, name="1-a.dem.zst", sha="a" * 64):
    return index.add_demo(name, sha, Path("E:/cs2clips/demos") / name)


def test_a_new_demo_is_spotted_and_found_by_id_or_name(index):
    demo_id = add(index)
    assert index.demo(demo_id)["state"] == "spotted"
    assert index.find_demo(str(demo_id))["id"] == demo_id
    assert index.find_demo("1-a.dem.zst")["id"] == demo_id
    assert index.find_demo("nope") is None


def test_has_demo_matches_the_name_or_the_hash(index):
    add(index)
    assert index.has_demo("1-a.dem.zst", "b" * 64)
    assert index.has_demo("other.dem.zst", "a" * 64)
    assert not index.has_demo("other.dem.zst", "b" * 64)


def test_advance_resets_attempts_and_sets_fields(index):
    demo_id = add(index)
    assert index.record_failure(demo_id, "boom") == 1
    index.advance(demo_id, "unpacked", dem_path=Path("E:/cs2clips/demos/1-a.dem"))
    demo = index.demo(demo_id)
    assert (demo["state"], demo["attempts"]) == ("unpacked", 0)
    assert demo["dem_path"] == str(Path("E:/cs2clips/demos/1-a.dem"))
    with pytest.raises(ValueError):
        index.advance(demo_id, "analyzed", colour="red")


def test_fail_remembers_the_step_and_retry_returns_to_it(index):
    demo_id = add(index)
    index.advance(demo_id, "rendering")
    job = index.queue_render(demo_id, "player", attempt=3)
    index.finish_render(job, "failed", "csdm reported: Game error")
    index.fail(demo_id, "player render failed 3 times")
    demo = index.demo(demo_id)
    assert (demo["state"], demo["resume_state"]) == ("failed", "rendering")
    assert index.retry(demo_id) == "rendering"
    assert index.demo(demo_id)["last_error"] is None
    latest = index.latest_render(demo_id, "player")
    assert (latest["state"], latest["attempt"]) == ("queued", 1)
    with pytest.raises(ValueError):
        index.retry(demo_id)


def test_a_retry_that_fails_midway_changes_nothing(index, monkeypatch):
    demo_id = add(index)
    index.advance(demo_id, "rendering")
    job = index.queue_render(demo_id, "player", attempt=3)
    index.finish_render(job, "failed", "csdm reported: Game error")
    index.fail(demo_id, "player render failed 3 times")

    # Monkeypatch queue_render to raise an error
    def failing_queue_render(demo_id, perspective, attempt):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(index, "queue_render", failing_queue_render)

    # Retry should raise the error
    with pytest.raises(sqlite3.OperationalError):
        index.retry(demo_id)

    # Demo state should still be failed
    assert index.demo(demo_id)["state"] == "failed"


def test_saving_highlights_again_updates_them_in_place(index):
    both = [highlight(3, 40, 15259), highlight(12, 80, 72031)]
    index.save_highlights(MATCH.checksum, both, {3, 12})
    ids = {row["round"]: row["id"] for row in index.selected_highlights(MATCH.checksum)}
    index.save_highlights(MATCH.checksum, both, {12})
    rows = index.selected_highlights(MATCH.checksum)
    assert [(row["round"], row["id"]) for row in rows] == [(12, ids[12])]
    assert json.loads(rows[0]["reasons"]) == ["3k"]


def test_render_jobs_track_the_latest_attempt(index):
    demo_id = add(index)
    first = index.queue_render(demo_id, "player", attempt=1)
    index.start_render(first, Path("E:/out"), Path("E:/log.txt"))
    assert index.latest_render(demo_id, "player")["state"] == "running"
    index.finish_render(first, "failed", "stalled")
    second = index.queue_render(demo_id, "player", attempt=2)
    latest = index.latest_render(demo_id, "player")
    assert (latest["id"], latest["attempt"], latest["state"]) == (second, 2, "queued")
    assert index.latest_render(demo_id, "enemy") is None


def test_clips_come_from_finished_renders_in_tick_order(index):
    demo_id = add(index)
    index.save_highlights(MATCH.checksum, [highlight(12, 80, 72031)], {12})
    highlight_id = index.selected_highlights(MATCH.checksum)[0]["id"]
    failed = index.queue_render(demo_id, "player", attempt=1)
    index.add_clip(failed, highlight_id, ClipFile(1, 74007, 74263, Path("old.mp4"), 4.0))
    index.finish_render(failed, "failed", "stalled")
    done = index.queue_render(demo_id, "player", attempt=2)
    index.add_clip(done, highlight_id, ClipFile(10, 78233, 78489, Path("c.mp4"), 4.0))
    index.add_clip(done, highlight_id, ClipFile(2, 76071, 76559, Path("b.mp4"), 7.6))
    index.finish_render(done, "done")
    assert [row["sequence"] for row in index.clips_for(highlight_id, "player")] == [2, 10]
    assert index.clips_for(highlight_id, "enemy") == []


def test_one_reel_per_highlight_and_perspective(index):
    index.save_match(MATCH)
    index.save_highlights(MATCH.checksum, [highlight(12, 80, 72031)], {12})
    highlight_id = index.selected_highlights(MATCH.checksum)[0]["id"]
    index.save_reel(highlight_id, "player", Path("E:/r12-player.mp4"), 15.6)
    index.save_reel(highlight_id, "player", Path("E:/r12-player.mp4"), 15.7)
    index.save_reel(highlight_id, "enemy", Path("E:/r12-enemy.mp4"), 15.6)
    assert index.reel_count(MATCH.checksum) == 2


def test_matches_and_flags_round_trip(index):
    index.save_match(MATCH)
    row = index.match(MATCH.checksum)
    assert (row["map"], row["team_score"], row["opponent_score"], row["result"]) == ("de_inferno", 13, 5, "win")
    assert index.get_flag("paused", "0") == "0"
    index.set_flag("paused", "1")
    assert index.get_flag("paused") == "1"
