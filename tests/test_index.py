import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from clipper.index import Index, PickRefused
from clipper.model import Break, ClipFile, FaceitStats, Highlight, MatchInfo

MATCH = MatchInfo(checksum="aea4e59ccfc6c962", map_name="de_inferno",
                  played_at=datetime(2026, 9, 22, 9, 39, 14, tzinfo=timezone.utc),
                  team_score=13, opponent_score=5)


def highlight(round_, score, start):
    return Highlight(round=round_, type="3K", score=score, reasons=("3k",), frag_ticks=(start + 100,),
                     round_start_tick=start, round_end_tick=start + 5000)


def worth(round_, start):
    """A Highlight worth clipping."""
    return replace(highlight(round_, 40, start), worthy=True)


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
    index.save_highlights(MATCH.checksum, [highlight(3, 10, 1000)])
    index.mark_picked(MATCH.checksum, {3})
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


def test_a_demo_rendering_another_view_of_its_reels_is_never_deleted(index):
    demo_id = add(index)
    index.save_match(MATCH)
    index.save_highlights(MATCH.checksum, [highlight(3, 50, 1000)])
    index.mark_picked(MATCH.checksum, {3})
    index.advance(demo_id, "done", match_checksum=MATCH.checksum)
    index.save_reel(index.selected_highlights(MATCH.checksum)[0]["id"], "player", "r3-player.mp4", 6.0)
    index.ask_render(demo_id, "enemy")
    with pytest.raises(ValueError, match="Reels"):
        index.delete_demo(demo_id)
    assert index.demo(demo_id)["state"] == "rendering"


def test_deleting_an_unknown_demo_finds_nothing(index):
    assert index.delete_demo(999) is None


def test_saving_highlights_again_updates_them_in_place_and_keeps_the_picks(index):
    """A match is scored again when its analysis is made again (the HP backfill) or the rules change."""
    index.save_highlights(MATCH.checksum, [highlight(3, 40, 15259), highlight(12, 80, 72031)])
    index.mark_picked(MATCH.checksum, {12})
    ids = {row["round"]: row["id"] for row in index.selected_highlights(MATCH.checksum)}
    again = [replace(highlight(3, 40, 15259), reasons=("3k", "14 HP"), worthy=True),
             replace(highlight(12, 80, 72031), reasons=("3k", "deagle"), worthy=True)]
    index.save_highlights(MATCH.checksum, again)
    rows = index.selected_highlights(MATCH.checksum)
    assert [(row["round"], row["id"]) for row in rows] == [(12, ids[12])]
    assert (json.loads(rows[0]["reasons"]), rows[0]["worthy"]) == (["3k", "deagle"], 1)
    assert [(row["round"], row["selected"]) for row in index.pick_rows(MATCH.checksum)] == [(3, 0), (12, 1)]


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


def test_a_perspectives_queued_render_jobs_come_off_the_queue_and_the_rest_stay(index):
    first, second = add(index), add(index, "2-b.dem.zst", "b" * 64)
    failed = index.queue_render(first, "enemy", attempt=1)
    index.start_render(failed, Path("E:/out"), Path("E:/log.txt"))
    index.finish_render(failed, "failed", "stalled")
    index.queue_render(first, "enemy", attempt=2)
    index.queue_render(second, "enemy", attempt=1)
    index.queue_render(second, "player", attempt=1)
    assert index.unqueue_renders("enemy") == 2
    assert index.latest_render(first, "enemy")["id"] == failed
    assert index.latest_render(second, "enemy") is None
    assert index.latest_render(second, "player")["state"] == "queued"


# --- asking a done Demo for the view it did not render: asked_renders ----------------------------


def done_demo(index, name="1-a.dem.zst", sha="a" * 64) -> int:
    demo_id = add(index, name, sha)
    index.advance(demo_id, "done")
    return demo_id


def test_asking_for_a_view_queues_it_and_takes_the_done_demo_back_to_rendering(index):
    demo_id = done_demo(index)
    index.advance(demo_id, "done", last_error="an old hiccup")

    index.ask_render(demo_id, "enemy")

    demo = index.demo(demo_id)
    assert (demo["state"], demo["attempts"], demo["last_error"]) == ("rendering", 0, None)
    assert index.asked_renders(demo_id) == ("enemy",)
    job = index.latest_render(demo_id, "enemy")
    assert (job["state"], job["attempt"]) == ("queued", 1)
    assert index.latest_render(demo_id, "player") is None


@pytest.mark.parametrize("state", ["spotted", "rendering", "joined", "failed"])
def test_only_a_done_demo_can_be_asked_for_a_view(index, state):
    demo_id = add(index)
    index.advance(demo_id, state)
    with pytest.raises(ValueError, match="is not done"):
        index.ask_render(demo_id, "enemy")
    assert index.demo(demo_id)["state"] == state
    assert index.asked_renders(demo_id) == ()
    assert index.latest_render(demo_id, "enemy") is None


def test_asking_about_an_unknown_demo_or_a_bad_view_is_a_value_error(index):
    demo_id = done_demo(index)
    with pytest.raises(ValueError, match="does not exist"):
        index.ask_render(999, "enemy")
    with pytest.raises(ValueError):
        index.ask_render(demo_id, "both")
    assert index.asked_renders(demo_id) == ()


def test_a_failed_ask_changes_nothing(index, monkeypatch):
    demo_id = done_demo(index)

    def failing_queue_render(demo_id, perspective, attempt):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(index, "queue_render", failing_queue_render)
    with pytest.raises(sqlite3.OperationalError):
        index.ask_render(demo_id, "enemy")

    assert index.demo(demo_id)["state"] == "done"
    assert index.asked_renders(demo_id) == ()


def test_the_asks_of_a_demo_can_be_cleared(index):
    demo_id = done_demo(index)
    index.ask_render(demo_id, "enemy")
    index.clear_asked_renders(demo_id)
    assert index.asked_renders(demo_id) == ()


def test_unqueue_renders_leaves_the_queued_job_of_a_demo_that_asked_for_the_view(index):
    asked, other = done_demo(index), done_demo(index, "2-b.dem.zst", "b" * 64)
    index.ask_render(asked, "enemy")
    index.queue_render(other, "enemy", attempt=1)
    index.queue_render(asked, "player", attempt=1)

    assert index.unqueue_renders("enemy") == 1
    assert index.latest_render(asked, "enemy")["state"] == "queued"
    assert index.latest_render(other, "enemy") is None
    assert index.unqueue_renders("player") == 1
    assert index.latest_render(asked, "player") is None


def test_deleting_a_demo_takes_its_asks_with_it(index):
    demo_id = done_demo(index)
    index.ask_render(demo_id, "enemy")
    assert index.delete_demo(demo_id)["id"] == demo_id
    assert index.demo(demo_id) is None
    assert index.asked_renders(demo_id) == ()


def test_demo_of_match_is_the_newest_demo_of_that_match(index):
    first = add(index)
    index.advance(first, "done", match_checksum=MATCH.checksum)
    second = add(index, "2-b.dem.zst", "b" * 64)
    index.advance(second, "rendering", match_checksum=MATCH.checksum)
    assert index.demo_of_match(MATCH.checksum)["id"] == second
    assert index.demo_of_match("0" * 16) is None


def test_clips_come_from_finished_renders_in_tick_order(index):
    demo_id = add(index)
    index.save_highlights(MATCH.checksum, [highlight(12, 80, 72031)])
    index.mark_picked(MATCH.checksum, {12})
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



HALFTIME = Break(12, 72031, 74271, "HALFTIME")
OVERTIME = Break(24, 150000, 152240, "OT 1")


def test_a_break_is_kept_beside_the_highlight_of_the_round_it_follows(index):
    index.save_highlights(MATCH.checksum, [highlight(12, 80, 70000)])
    index.mark_picked(MATCH.checksum, {12})
    index.save_breaks(MATCH.checksum, [OVERTIME, HALFTIME])
    assert [(row["after_round"], row["label"], row["start_tick"], row["end_tick"])
            for row in index.breaks(MATCH.checksum)] == [(12, "HALFTIME", 72031, 74271), (24, "OT 1", 150000, 152240)]
    assert [row["round"] for row in index.selected_highlights(MATCH.checksum)] == [12]


def test_saving_breaks_again_updates_them_in_place(index):
    index.save_breaks(MATCH.checksum, [HALFTIME])
    first = index.breaks(MATCH.checksum)[0]["id"]
    index.save_breaks(MATCH.checksum, [replace(HALFTIME, start_tick=72100)])
    assert [(row["id"], row["start_tick"]) for row in index.breaks(MATCH.checksum)] == [(first, 72100)]


def test_scoring_the_match_again_leaves_its_breaks_alone(index):
    index.save_breaks(MATCH.checksum, [HALFTIME])
    index.save_highlights(MATCH.checksum, [highlight(12, 80, 70000)])
    index.mark_picked(MATCH.checksum, {12})
    assert [row["label"] for row in index.breaks(MATCH.checksum)] == ["HALFTIME"]


def test_a_breaks_reel_is_listed_apart_from_the_highlights_reels(index):
    index.save_match(MATCH)
    index.save_highlights(MATCH.checksum, [highlight(12, 80, 70000)])
    index.mark_picked(MATCH.checksum, {12})
    index.save_breaks(MATCH.checksum, [HALFTIME, OVERTIME])
    twelve = index.selected_highlights(MATCH.checksum)[0]["id"]
    index.save_reel(twelve, "player", "r12.mp4", 15.0)
    half = index.breaks(MATCH.checksum)[0]["id"]
    index.save_reel(half, "player", "half.mp4", 35.0)
    assert [(row["after_round"], row["label"], row["duration_s"])
            for row in index.pick_breaks(MATCH.checksum)] == [(12, "HALFTIME", 35.0), (24, "OT 1", None)]
    assert [row["round"] for row in index.match_reels(MATCH.checksum)] == [12]
    assert index.reel_details(index.best_reel(MATCH.checksum, "player"))["path"] == "r12.mp4"
    assert index.reel_details(index.pick_breaks(MATCH.checksum)[0]["reel_id"])["type"] == "HALFTIME"

def test_one_reel_per_highlight_and_perspective(index):
    index.save_match(MATCH)
    index.save_highlights(MATCH.checksum, [highlight(12, 80, 72031)])
    index.mark_picked(MATCH.checksum, {12})
    highlight_id = index.selected_highlights(MATCH.checksum)[0]["id"]
    index.save_reel(highlight_id, "player", Path("E:/r12-player.mp4"), 15.6)
    index.save_reel(highlight_id, "player", Path("E:/r12-player.mp4"), 15.7)
    index.save_reel(highlight_id, "enemy", Path("E:/r12-enemy.mp4"), 15.6)
    assert index.reel_count(MATCH.checksum) == 2


def test_has_reel_tells_whether_a_highlight_already_has_a_reel_for_a_view(index):
    index.save_match(MATCH)
    index.save_highlights(MATCH.checksum, [highlight(12, 80, 72031)])
    index.mark_picked(MATCH.checksum, {12})
    highlight_id = index.selected_highlights(MATCH.checksum)[0]["id"]
    assert index.has_reel(highlight_id, "player") is False
    index.save_reel(highlight_id, "player", Path("E:/r12-player.mp4"), 15.6)
    assert index.has_reel(highlight_id, "player") is True
    assert index.has_reel(highlight_id, "enemy") is False


# --- pick_matches / match_reels / reel: the Reels page (Task 12) -----------------------------------


def test_pick_matches_lists_matches_with_something_worth_clipping_or_a_reel_newest_played_first(index):
    def played(checksum, day, map_name="de_inferno"):
        return MatchInfo(checksum=checksum * 16, map_name=map_name, played_at=datetime(2026, 9, day, tzinfo=timezone.utc),
                         team_score=13, opponent_score=5)

    clipped, to_pick, filler, plain, no_demo = (played("a", 20), played("b", 25, "de_mirage"), played("c", 26),
                                                played("d", 27), played("e", 28))
    for m in (clipped, to_pick, filler, plain, no_demo):
        index.save_match(m)
    for m in (clipped, to_pick, filler, plain):
        demo_id = add(index, name=f"1-{m.checksum}.dem.zst", sha=m.checksum + "0" * 48)
        index.advance(demo_id, "done", match_checksum=m.checksum)
    index.save_highlights(clipped.checksum, [worth(1, 1000)])
    index.mark_picked(clipped.checksum, {1})
    index.save_reel(index.selected_highlights(clipped.checksum)[0]["id"], "player", Path("E:/a.mp4"), 10.0)
    index.save_highlights(to_pick.checksum, [worth(4, 1000)])             # nothing picked yet: ready to pick
    index.save_highlights(filler.checksum, [highlight(2, 5, 1000)])       # a single Frag the old top 5 picked
    index.mark_picked(filler.checksum, {2})
    index.save_reel(index.selected_highlights(filler.checksum)[0]["id"], "player", Path("E:/c.mp4"), 4.0)
    index.save_highlights(plain.checksum, [highlight(2, 5, 1000)])        # nothing worth clipping, no Reel
    index.save_highlights(no_demo.checksum, [worth(4, 1000)])             # its Demo was deleted before any Reel

    rows = index.pick_matches()

    assert [r["checksum"] for r in rows] == [filler.checksum, to_pick.checksum, clipped.checksum]
    assert (rows[2]["map"], rows[2]["team_score"], rows[2]["opponent_score"], rows[2]["result"]) == (
        "de_inferno", 13, 5, "win")


def test_pick_rows_are_the_rounds_worth_clipping_and_the_picked_ones_with_their_reels(index):
    index.save_highlights(MATCH.checksum, [worth(3, 15259), highlight(9, 5, 50000), highlight(11, 5, 60000),
                                           worth(12, 72031)])
    index.mark_picked(MATCH.checksum, {9, 12})
    ids = {row["round"]: row["id"] for row in index.selected_highlights(MATCH.checksum)}
    index.save_reel(ids[12], "enemy", Path("E:/r12-enemy.mp4"), 15.6)

    rows = index.pick_rows(MATCH.checksum)

    assert [(r["round"], r["worthy"], r["selected"], r["player_reel_id"], r["enemy_s"]) for r in rows] == [
        (3, 1, 0, None, None), (9, 0, 1, None, None), (12, 1, 1, None, 15.6)]
    assert isinstance(rows[2]["enemy_reel_id"], int)


def test_pick_breaks_are_every_break_with_whether_it_is_picked_and_its_reel(index):
    index.save_breaks(MATCH.checksum, [Break(12, 70000, 72240, "HALFTIME"), Break(24, 150000, 152240, "OT 1")])
    index.mark_picked(MATCH.checksum, breaks={12, 24})
    index.save_reel(index.breaks(MATCH.checksum)[1]["id"], "player", Path("E:/OT 1.mp4"), 30.0)

    rows = index.pick_breaks(MATCH.checksum)

    assert [(r["after_round"], r["label"], r["picked"], r["duration_s"]) for r in rows] == [
        (12, "HALFTIME", 1, None), (24, "OT 1", 1, 30.0)]
    assert rows[0]["reel_id"] is None and isinstance(rows[1]["reel_id"], int)


def test_match_reels_pairs_perspectives_leaves_missing_ones_null_and_drops_reel_less_highlights(index):
    index.save_match(MATCH)
    index.save_highlights(MATCH.checksum, [highlight(12, 80, 72031), highlight(3, 40, 15259),
                                           highlight(20, 10, 90000)])
    index.mark_picked(MATCH.checksum, {3, 12, 20})
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
    index.save_highlights(MATCH.checksum, [highlight(12, 80, 72031)])
    index.mark_picked(MATCH.checksum, {12})
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


def test_a_snooze_lasts_until_its_time_and_can_be_ended(index):
    now = datetime(2026, 10, 13, 12, 0, tzinfo=timezone.utc)
    assert index.snoozed_until(now) is None
    index.snooze(now + timedelta(hours=1))
    assert index.snoozed_until(now) == now + timedelta(hours=1)
    assert index.snoozed_until(now + timedelta(hours=1)) is None
    index.snooze(None)
    assert index.snoozed_until(now) is None


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


def test_an_existing_index_gains_the_asked_renders_table(tmp_path):
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
        demo_id = index.find_demo("1-a.dem.zst")["id"]
        index.ask_render(demo_id, "enemy")
        assert index.asked_renders(demo_id) == ("enemy",)
    finally:
        index.close()


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


# --- picking (spec: Pick your rounds) ---------------------------------------------------------------

OLD_HIGHLIGHTS = """CREATE TABLE highlights (
    id INTEGER PRIMARY KEY, match_checksum TEXT NOT NULL, round INTEGER NOT NULL, type TEXT NOT NULL,
    score INTEGER NOT NULL, reasons TEXT NOT NULL, frag_ticks TEXT NOT NULL, round_start_tick INTEGER NOT NULL,
    round_end_tick INTEGER NOT NULL, selected INTEGER NOT NULL DEFAULT 0, UNIQUE (match_checksum, round))"""


def test_an_index_made_before_picks_gains_their_columns_and_keeps_its_rows(tmp_path):
    path = tmp_path / "old.sqlite"
    old = sqlite3.connect(path)
    old.execute(OLD_HIGHLIGHTS)
    old.execute("INSERT INTO highlights (match_checksum, round, type, score, reasons, frag_ticks,"
                " round_start_tick, round_end_tick, selected) VALUES ('ab', 3, '3K', 40, '[]', '[]', 0, 9, 1)")
    old.commit()
    old.close()
    Index(path).close()
    index = Index(path)                                  # and opening it again changes nothing
    [row] = index.selected_highlights("ab")
    assert (row["round"], row["worthy"], row["break_picked"]) == (3, 0, 0)
    index.close()


def test_saved_highlights_keep_whether_they_are_worth_clipping(index):
    index.save_highlights(MATCH.checksum, [worth(3, 15259), highlight(9, 5, 50000)])
    assert [(r["round"], r["worthy"]) for r in index.pick_rows(MATCH.checksum)] == [(3, 1)]


def ready_demo(index, *highlights, name="1-picks.dem.zst"):
    """A done Demo of MATCH, scored to `highlights`, with nothing picked. Returns its id."""
    index.save_match(MATCH)
    demo_id = add(index, name=name, sha="p" * 64)
    index.advance(demo_id, "done", match_checksum=MATCH.checksum)
    index.save_highlights(MATCH.checksum, highlights)
    return demo_id


BOTH = ("player", "enemy")


def test_pick_marks_the_rounds_and_breaks_it_names_and_says_which_were_new(index):
    ready_demo(index, worth(3, 15259), worth(12, 72031))
    index.save_breaks(MATCH.checksum, [Break(12, 81477, 84000, "HALFTIME")])
    index.mark_picked(MATCH.checksum, {3})
    assert index.pick(MATCH.checksum, [3, 12], [12], BOTH) == ([12], [12])
    assert [row["round"] for row in index.selected_highlights(MATCH.checksum)] == [3, 12]
    assert [row["picked"] for row in index.pick_breaks(MATCH.checksum)] == [1]
    assert index.pick(MATCH.checksum, [3, 12], [12], BOTH) == ([], [])     # picked already: left as it is


@pytest.mark.parametrize(("rounds", "breaks"), [([9], []), ([7], []), ([0], []), ([], [24]), ([3, 9], [])])
def test_pick_refuses_a_round_not_worth_clipping_and_a_break_the_match_lacks_and_picks_nothing(index, rounds, breaks):
    demo_id = ready_demo(index, worth(3, 15259), highlight(9, 5, 50000))
    with pytest.raises(ValueError):
        index.pick(MATCH.checksum, rounds, breaks, BOTH)
    assert index.selected_highlights(MATCH.checksum) == []
    assert index.demo(demo_id)["state"] == "done"


def test_picking_on_a_done_demo_queues_each_view_that_lacks_a_pick_and_sends_it_back_to_rendering(index):
    demo_id = ready_demo(index, worth(3, 15259), worth(12, 72031))
    index.record_failure(demo_id, "it failed 3 times, last with: stalled")
    index.pick(MATCH.checksum, [12], [], BOTH)
    demo = index.demo(demo_id)
    assert (demo["state"], demo["last_error"]) == ("rendering", None)
    assert [(index.latest_render(demo_id, p)["state"], index.latest_render(demo_id, p)["attempt"]) for p in BOTH] == [
        ("queued", 1), ("queued", 1)]


def test_a_view_that_failed_three_times_starts_again_from_attempt_one_when_picked_again(index):
    demo_id = ready_demo(index, worth(3, 15259))
    job = index.queue_render(demo_id, "player", attempt=3)
    index.finish_render(job, "failed", "stalled")
    index.pick(MATCH.checksum, [3], [], ("player",))
    latest = index.latest_render(demo_id, "player")
    assert (latest["state"], latest["attempt"]) == ("queued", 1)


def test_a_picked_break_asks_for_your_view_only(index):
    demo_id = ready_demo(index, worth(3, 15259))
    index.save_breaks(MATCH.checksum, [Break(12, 81477, 84000, "HALFTIME")])
    index.pick(MATCH.checksum, [], [12], BOTH)
    assert index.latest_render(demo_id, "player")["state"] == "queued"
    assert index.latest_render(demo_id, "enemy") is None


def test_picking_what_every_view_has_leaves_a_done_demo_done(index):
    demo_id = ready_demo(index, worth(3, 15259))
    index.mark_picked(MATCH.checksum, {3})
    for perspective in BOTH:
        index.save_reel(index.selected_highlights(MATCH.checksum)[0]["id"], perspective, f"E:/{perspective}.mp4", 4.0)
    index.pick(MATCH.checksum, [3], [], BOTH)
    assert index.demo(demo_id)["state"] == "done"
    assert index.latest_render(demo_id, "player") is None


def test_picking_on_a_demo_still_on_its_way_leaves_it_to_the_worker(index):
    demo_id = ready_demo(index, worth(3, 15259))
    index.advance(demo_id, "scored")
    index.pick(MATCH.checksum, [3], [], BOTH)
    assert index.demo(demo_id)["state"] == "scored"
    assert index.latest_render(demo_id, "player") is None
    assert [row["round"] for row in index.selected_highlights(MATCH.checksum)] == [3]


def test_pick_is_refused_for_a_match_without_a_demo_and_for_a_failed_demo(index):
    index.save_match(MATCH)
    index.save_highlights(MATCH.checksum, [worth(3, 15259)])
    with pytest.raises(PickRefused, match="This match has no Demo."):
        index.pick(MATCH.checksum, [3], [], BOTH)
    demo_id = add(index)
    index.advance(demo_id, "rendering", match_checksum=MATCH.checksum)
    index.fail(demo_id, "rendering: CS2 never started the demo")
    with pytest.raises(PickRefused, match="This Demo failed: rendering: CS2 never started the demo. Retry it on Status."):
        index.pick(MATCH.checksum, [3], [], BOTH)
    assert index.selected_highlights(MATCH.checksum) == []


def test_what_is_left_to_record_is_what_is_picked_and_the_view_has_neither_a_reel_nor_clips_of(index):
    demo_id = ready_demo(index, worth(3, 15259), worth(12, 72031), worth(14, 90164), worth(18, 116005))
    index.mark_picked(MATCH.checksum, {3, 12, 14})
    ids = {row["round"]: row["id"] for row in index.selected_highlights(MATCH.checksum)}
    index.save_reel(ids[3], "player", "E:/r3-player.mp4", 6.0)
    job = index.queue_render(demo_id, "enemy", attempt=1)              # recorded, not joined yet
    index.add_clip(job, ids[14], ClipFile(1, 90264, 90520, Path("E:/s1.mp4"), 4.0), "s1.mp4")
    index.finish_render(job, "done")
    assert [r["round"] for r in index.highlights_to_record(MATCH.checksum, "player")] == [12, 14]
    assert [r["round"] for r in index.highlights_to_record(MATCH.checksum, "enemy")] == [3, 12]


def test_the_breaks_left_to_record_are_the_picked_ones_without_a_reel(index):
    index.save_breaks(MATCH.checksum, [Break(12, 81477, 84000, "HALFTIME"), Break(24, 150000, 152240, "OT 1"),
                                       Break(27, 170000, 172240, "OT 1 HALF")])
    index.mark_picked(MATCH.checksum, breaks={12, 24})
    index.save_reel(index.breaks(MATCH.checksum)[0]["id"], "player", "E:/HALFTIME.mp4", 35.0)
    assert [(b["after_round"], b["label"], b["start_tick"]) for b in index.breaks_to_record(MATCH.checksum)] == [
        (24, "OT 1", 150000)]


def test_give_back_unpicks_what_has_no_reel_in_any_view(index):
    index.save_highlights(MATCH.checksum, [worth(3, 15259), worth(12, 72031)])
    index.save_breaks(MATCH.checksum, [Break(12, 81477, 84000, "HALFTIME")])
    index.mark_picked(MATCH.checksum, {3, 12}, {12})
    index.save_reel(index.selected_highlights(MATCH.checksum)[0]["id"], "player", "E:/r3-player.mp4", 6.0)
    assert index.give_back(MATCH.checksum) == 2
    assert [row["round"] for row in index.selected_highlights(MATCH.checksum)] == [3]
    assert [row["picked"] for row in index.pick_breaks(MATCH.checksum)] == [0]


def test_the_move_to_picks_unpicks_what_top_n_picked_and_never_recorded_but_not_while_it_renders(index):
    rendering = replace(MATCH, checksum="b" * 16)
    ready_demo(index, worth(3, 15259), worth(12, 72031))
    index.mark_picked(MATCH.checksum, {3, 12})
    index.save_reel(index.selected_highlights(MATCH.checksum)[0]["id"], "player", "E:/r3-player.mp4", 6.0)
    index.save_match(rendering)
    demo_id = add(index, name="1-rendering.dem.zst", sha="r" * 64)
    index.advance(demo_id, "rendering", match_checksum=rendering.checksum)
    index.save_highlights(rendering.checksum, [worth(5, 30000)])
    index.mark_picked(rendering.checksum, {5})
    assert index.unpick_unrecorded() == 1
    assert [row["round"] for row in index.selected_highlights(MATCH.checksum)] == [3]
    assert [row["round"] for row in index.selected_highlights(rendering.checksum)] == [5]


def test_recorded_by_counts_the_highlights_a_render_job_has_clips_of(index):
    demo_id = ready_demo(index, worth(3, 15259), worth(12, 72031))
    index.save_breaks(MATCH.checksum, [Break(12, 81477, 84000, "HALFTIME")])
    index.mark_picked(MATCH.checksum, {3, 12}, {12})
    ids = {row["round"]: row["id"] for row in index.selected_highlights(MATCH.checksum)}
    job = index.queue_render(demo_id, "player", attempt=1)
    for n, highlight_id in enumerate((ids[3], ids[3], ids[12], index.breaks(MATCH.checksum)[0]["id"]), start=1):
        index.add_clip(job, highlight_id, ClipFile(n, n * 100, n * 100 + 50, Path(f"E:/s{n}.mp4"), 4.0), f"s{n}.mp4")
    assert index.recorded_by(job) == 2                  # two Highlights; the Break is no Highlight


def test_render_seconds_counts_the_highlights_each_render_recorded_not_all_those_picked(index):
    demo_id = ready_demo(index, worth(3, 15259), worth(12, 72031), worth(14, 90164))
    index.mark_picked(MATCH.checksum, {3, 12, 14})
    twelve = next(h["id"] for h in index.selected_highlights(MATCH.checksum) if h["round"] == 12)
    job = index.queue_render(demo_id, "player", attempt=1)                # a later pick of round 12 alone
    index.add_clip(job, twelve, ClipFile(1, 72100, 72400, Path("E:/s1.mp4"), 4.0), "s1.mp4")
    index.start_render(job, Path("E:/out"), Path("E:/log.txt"))
    index._db.execute("UPDATE render_jobs SET state = 'done', started_at = ?, finished_at = ? WHERE id = ?",
                      ("2026-10-13T10:00:00+00:00", "2026-10-13T10:01:40+00:00", job))
    assert index.render_seconds() == [(100.0, 1)]


def test_lacks_reel_says_whether_a_picked_highlight_has_no_reel_of_a_view(index):
    index.save_highlights(MATCH.checksum, [worth(3, 15259), worth(12, 72031)])
    index.mark_picked(MATCH.checksum, {3})
    index.save_reel(index.selected_highlights(MATCH.checksum)[0]["id"], "player", "E:/r3-player.mp4", 6.0)
    assert index.lacks_reel(MATCH.checksum, "player") is False       # round 12 is not picked
    assert index.lacks_reel(MATCH.checksum, "enemy") is True
