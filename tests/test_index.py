import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from clipper.index import Index
from clipper.model import ClipFile, FaceitStats, Highlight, MatchInfo

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
    index.finish_render(job, "failed", "HLAE error: HLAE.exe exited with code 1")
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
    index.finish_render(job, "failed", "HLAE error: HLAE.exe exited with code 1")
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


def test_deleting_a_demo_removes_its_rows_and_unlinks_its_faceit_match(index):
    index.save_match(MATCH)
    index.save_highlights(MATCH.checksum, [highlight(3, 10, 1000)], {3})
    hl = index.selected_highlights(MATCH.checksum)[0]["id"]
    demo_id = add(index, name="1-abc-1.dem.zst")
    index.advance(demo_id, "rendering", match_checksum=MATCH.checksum)
    job = index.queue_render(demo_id, "player", attempt=1)
    index.add_clip(job, hl, ClipFile(sequence=1, start_tick=1100, end_tick=1356,
                                     path=Path("sequence-1.mp4"), duration_s=4.0), "renders/sequence-1.mp4")
    index.finish_render(job, "done")
    found = datetime(2026, 10, 6, 20, 0, tzinfo=timezone.utc)
    index.save_faceit_match("1-abc", found, "ready")
    index.link_grabbed_matches(found)

    deleted = index.delete_demo(demo_id)

    assert deleted["archive_path"] == str(Path("E:/cs2clips/demos/1-abc-1.dem.zst"))
    assert index.demo(demo_id) is None
    assert index.latest_render(demo_id, "player") is None
    assert index.clips_for(hl, "player") == []
    assert index.faceit_match("1-abc")["demo_id"] is None
    assert not index.has_demo("1-abc-1.dem.zst", "a" * 64)      # downloaded again, it is taken fresh


@pytest.mark.parametrize("state", ["joined", "done"])
def test_a_demo_that_has_its_reels_is_never_deleted(index, state):
    demo_id = add(index)
    index.advance(demo_id, state)
    with pytest.raises(ValueError, match="Reels"):
        index.delete_demo(demo_id)
    assert index.demo(demo_id)["state"] == state


def test_deleting_an_unknown_demo_finds_nothing(index):
    assert index.delete_demo(999) is None


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
    index.add_clip(failed, highlight_id, ClipFile(1, 74007, 74263, Path("old.mp4"), 4.0), "old.mp4")
    index.finish_render(failed, "failed", "stalled")
    done = index.queue_render(demo_id, "player", attempt=2)
    index.add_clip(done, highlight_id, ClipFile(10, 78233, 78489, Path("c.mp4"), 4.0), "c.mp4")
    index.add_clip(done, highlight_id, ClipFile(2, 76071, 76559, Path("b.mp4"), 7.6), "b.mp4")
    index.finish_render(done, "done")
    assert [row["sequence"] for row in index.clips_for(highlight_id, "player")] == [2, 10]
    assert index.clips_for(highlight_id, "enemy") == []
    # the stored path is the one handed in (relative to the clips folder), not the file's own
    assert [row["path"] for row in index.clips_for(highlight_id, "player")] == ["b.mp4", "c.mp4"]


def test_one_reel_per_highlight_and_perspective(index):
    index.save_match(MATCH)
    index.save_highlights(MATCH.checksum, [highlight(12, 80, 72031)], {12})
    highlight_id = index.selected_highlights(MATCH.checksum)[0]["id"]
    index.save_reel(highlight_id, "player", Path("E:/r12-player.mp4"), 15.6)
    index.save_reel(highlight_id, "player", Path("E:/r12-player.mp4"), 15.7)
    index.save_reel(highlight_id, "enemy", Path("E:/r12-enemy.mp4"), 15.6)
    assert index.reel_count(MATCH.checksum) == 2


# --- reel_matches / match_reels / reel: the Reels page (Task 12) -----------------------------------


def test_reel_matches_lists_only_done_demos_with_a_reel_newest_played_first(index):
    older = MatchInfo(checksum="a" * 16, map_name="de_inferno",
                      played_at=datetime(2026, 9, 20, tzinfo=timezone.utc), team_score=13, opponent_score=5)
    newer = MatchInfo(checksum="b" * 16, map_name="de_mirage",
                      played_at=datetime(2026, 9, 25, tzinfo=timezone.utc), team_score=10, opponent_score=13)
    no_reel = MatchInfo(checksum="c" * 16, map_name="de_nuke",
                        played_at=datetime(2026, 9, 26, tzinfo=timezone.utc), team_score=13, opponent_score=0)
    not_done = MatchInfo(checksum="d" * 16, map_name="de_ancient",
                         played_at=datetime(2026, 9, 27, tzinfo=timezone.utc), team_score=13, opponent_score=1)
    for m in (older, newer, no_reel, not_done):
        index.save_match(m)
    for m, state in ((older, "done"), (newer, "done"), (no_reel, "done"), (not_done, "rendering")):
        demo_id = add(index, name=f"1-{m.checksum}.dem.zst", sha=m.checksum + "0" * 48)
        index.advance(demo_id, state, match_checksum=m.checksum)
    for m in (older, newer, not_done):
        index.save_highlights(m.checksum, [highlight(1, 40, 1000)], {1})
        highlight_id = index.selected_highlights(m.checksum)[0]["id"]
        index.save_reel(highlight_id, "player", Path(f"E:/{m.checksum}-player.mp4"), 10.0)
    # no_reel: a done Demo, but never scored/rendered -- no Highlights, so no Reel either

    rows = index.reel_matches()

    assert [r["checksum"] for r in rows] == [newer.checksum, older.checksum]
    row = rows[1]
    assert (row["map"], row["team_score"], row["opponent_score"], row["result"]) == ("de_inferno", 13, 5, "win")


def test_reel_matches_leaves_out_a_match_whose_reels_sit_only_on_deselected_highlights(index):
    index.save_match(MATCH)
    demo_id = add(index, name="1-deselected.dem.zst", sha="e" * 64)
    index.advance(demo_id, "done", match_checksum=MATCH.checksum)
    index.save_highlights(MATCH.checksum, [highlight(12, 80, 72031)], {12})
    highlight_id = index.selected_highlights(MATCH.checksum)[0]["id"]
    index.save_reel(highlight_id, "player", Path("E:/r12-player.mp4"), 15.6)
    assert [r["checksum"] for r in index.reel_matches()] == [MATCH.checksum]

    # Scored again with another top_n, round 12 is no longer selected -- its Reel stays in the index.
    index.save_highlights(MATCH.checksum, [highlight(12, 80, 72031)], set())

    assert index.reel_matches() == []                    # match_reels would show it an empty block
    assert index.match_reels(MATCH.checksum) == []


def test_match_reels_pairs_perspectives_leaves_missing_ones_null_and_drops_reel_less_highlights(index):
    index.save_match(MATCH)
    index.save_highlights(MATCH.checksum, [highlight(12, 80, 72031), highlight(3, 40, 15259),
                                           highlight(20, 10, 90000)], {3, 12, 20})
    ids = {row["round"]: row["id"] for row in index.selected_highlights(MATCH.checksum)}
    index.save_reel(ids[12], "player", Path("E:/r12-player.mp4"), 15.6)
    index.save_reel(ids[12], "enemy", Path("E:/r12-enemy.mp4"), 15.6)
    index.save_reel(ids[3], "player", Path("E:/r3-player.mp4"), 8.0)
    # round 3 has no enemy Reel; round 20 (selected) has no Reel at all

    rows = index.match_reels(MATCH.checksum)

    assert [r["round"] for r in rows] == [3, 12]
    assert (rows[0]["player_reel_id"] is not None, rows[0]["enemy_reel_id"]) == (True, None)
    assert (rows[1]["player_reel_id"] is not None, rows[1]["enemy_reel_id"] is not None) == (True, True)
    assert json.loads(rows[0]["reasons"]) == ["3k"]


def test_reel_by_id_and_none_for_an_unknown_one(index):
    index.save_match(MATCH)
    index.save_highlights(MATCH.checksum, [highlight(12, 80, 72031)], {12})
    highlight_id = index.selected_highlights(MATCH.checksum)[0]["id"]
    index.save_reel(highlight_id, "player", Path("E:/r12-player.mp4"), 15.6)
    reel_id = index.match_reels(MATCH.checksum)[0]["player_reel_id"]

    row = index.reel(reel_id)

    assert (row["highlight_id"], row["perspective"], row["path"], row["duration_s"]) == (
        highlight_id, "player", str(Path("E:/r12-player.mp4")), 15.6)
    assert index.reel(reel_id + 999) is None


def test_matches_and_flags_round_trip(index):
    index.save_match(MATCH)
    row = index.match(MATCH.checksum)
    assert (row["map"], row["team_score"], row["opponent_score"], row["result"]) == ("de_inferno", 13, 5, "win")
    assert index.get_flag("paused", "0") == "0"
    index.set_flag("paused", "1")
    assert index.get_flag("paused") == "1"


def test_pause_records_who_and_resume_clears_it(index):
    assert index.paused_by() is None
    index.pause("you")
    assert index.paused_by() == "you"
    assert index.get_flag("paused") == "1"
    index.set_flag("consecutive_failures", "2")
    index.resume()
    assert index.paused_by() is None
    assert index.get_flag("paused") == "0"
    assert index.get_flag("consecutive_failures") == "0"


def test_pause_after_failures(index):
    index.pause("failures")
    assert index.paused_by() == "failures"


def test_paused_by_falls_back_to_failures_for_an_index_paused_by_an_older_version(index):
    index.set_flag("paused", "1")   # an older version paused without recording who
    assert index.paused_by() == "failures"


def test_paused_by_is_none_when_not_paused(index):
    index.set_flag("paused", "0")
    assert index.paused_by() is None


NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)
FIRST = "1-00000000-0000-0000-0000-000000000001"
SECOND = "1-00000000-0000-0000-0000-000000000002"
THREE_K = FaceitStats(map_name="de_inferno", team_score=13, opponent_score=9, won=True, rounds=22,
                      kills=24, deaths=15, assists=5, adr=94.0, double_kills=3, triple_kills=2, quadro_kills=1)
ROOM = "https://www.faceit.com/en/cs2/room/"


def test_a_faceit_match_is_saved_then_filled_in(index):
    index.save_faceit_match(FIRST, NOW - timedelta(hours=2), "waiting")
    assert index.faceit_match(FIRST)["state"] == "waiting"
    index.save_faceit_match(FIRST, NOW - timedelta(hours=2), "ready", THREE_K, 1.5, ROOM + FIRST)
    row = index.faceit_match(FIRST)
    assert (row["state"], row["map"], row["won"], row["rating"], row["matchroom_url"]) == (
        "ready", "de_inferno", 1, 1.5, ROOM + FIRST)
    assert json.loads(row["highlights"]) == {"3k": 2, "4k": 1, "5k": 0}
    assert row["finished_at"] == "2026-09-25T18:00:00+00:00"


def test_faceit_matches_come_newest_first(index):
    index.save_faceit_match(FIRST, NOW - timedelta(hours=3), "ready", THREE_K)
    index.save_faceit_match(SECOND, NOW - timedelta(hours=1), "ready", THREE_K)
    assert [r["match_id"] for r in index.faceit_matches_in(("ready",))] == [SECOND, FIRST]


def test_announce_and_remind_stamp_the_times(index):
    index.save_faceit_match(FIRST, NOW, "ready", THREE_K)
    index.announce([FIRST], NOW)
    index.remind([FIRST], NOW + timedelta(days=27))
    row = index.faceit_match(FIRST)
    assert row["state"] == "announced"
    assert row["announced_at"] == "2026-09-25T20:00:00+00:00"
    assert row["reminded_at"] == "2026-10-22T20:00:00+00:00"


def test_skip_and_undo(index):
    index.save_faceit_match(FIRST, NOW, "ready", THREE_K)
    index.announce([FIRST], NOW)
    assert index.skip_match(FIRST, NOW) is True
    assert index.faceit_match(FIRST)["state"] == "skipped"
    assert index.skip_match(FIRST, NOW) is False               # already skipped
    assert index.undo_skip(FIRST) is True
    assert (index.faceit_match(FIRST)["state"], index.faceit_match(FIRST)["decided_at"]) == ("announced", None)
    index.save_faceit_match(SECOND, NOW, "ready", THREE_K)
    index.skip_match(SECOND, NOW)
    index.undo_skip(SECOND)
    assert index.faceit_match(SECOND)["state"] == "ready"      # never announced
    assert index.undo_skip("1-00000000-0000-0000-0000-00000000dead") is False


def test_a_demo_in_the_index_grabs_its_match(index):
    index.save_faceit_match(FIRST, NOW, "announced", THREE_K)
    index.save_faceit_match(SECOND, NOW, "no_highlights")
    demo_id = add(index, name=f"{FIRST}-1-1.dem.zst")
    add(index, name=f"{SECOND}-1-1.dem.zst", sha="b" * 64)
    assert index.link_grabbed_matches(NOW) == 1
    row = index.faceit_match(FIRST)
    assert (row["state"], row["demo_id"], row["decided_at"]) == ("grabbed", demo_id, "2026-09-25T20:00:00+00:00")
    assert index.faceit_match(SECOND)["state"] == "no_highlights"
    assert index.link_grabbed_matches(NOW) == 0


def test_matches_whose_link_expired_drop_out(index):
    index.save_faceit_match(FIRST, NOW - timedelta(days=31), "announced", THREE_K)
    index.save_faceit_match(SECOND, NOW - timedelta(days=29), "announced", THREE_K)
    assert index.expire_faceit_matches(NOW - timedelta(days=30)) == 1
    assert index.faceit_match(FIRST)["state"] == "expired"
    assert index.faceit_match(SECOND)["state"] == "announced"


def test_the_page_lists_matches_to_grab_and_what_was_decided_in_the_last_day(index):
    third, fourth = FIRST[:-1] + "3", FIRST[:-1] + "4"
    index.save_faceit_match(FIRST, NOW - timedelta(hours=5), "announced", THREE_K)
    index.save_faceit_match(SECOND, NOW - timedelta(hours=4), "ready", THREE_K)
    index.save_faceit_match(third, NOW - timedelta(days=3), "ready", THREE_K)
    index.skip_match(third, NOW - timedelta(days=2))          # decided two days ago: gone
    index.save_faceit_match(fourth, NOW - timedelta(hours=1), "no_highlights")
    rows = index.page_matches(NOW - timedelta(hours=24))
    assert [r["match_id"] for r in rows] == [SECOND, FIRST]
    assert rows[0]["demo_state"] is None


def test_an_existing_index_gains_the_faceit_table(tmp_path):
    path = tmp_path / "clipper.sqlite"
    old = sqlite3.connect(path)
    old.execute("CREATE TABLE demos (id INTEGER PRIMARY KEY, file_name TEXT NOT NULL UNIQUE,"
                " sha256 TEXT NOT NULL UNIQUE, archive_path TEXT NOT NULL, dem_path TEXT, match_checksum TEXT,"
                " state TEXT NOT NULL, resume_state TEXT, attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT,"
                " created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
    old.execute("INSERT INTO demos (file_name, sha256, archive_path, state, created_at, updated_at)"
                " VALUES ('1-a.dem.zst', 'aaa', 'E:/a', 'done', 'x', 'x')")
    old.commit()
    old.close()
    index = Index(path)
    try:
        assert index.find_demo("1-a.dem.zst")["state"] == "done"
        index.save_faceit_match(FIRST, NOW, "ready", THREE_K)
        assert index.faceit_match(FIRST)["state"] == "ready"
    finally:
        index.close()
