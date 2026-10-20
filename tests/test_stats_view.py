"""The Stats page's data: a kill's place in the Reels, the match view, and the subject's comparison with their earlier
matches. The index, the stats files and the web app are real; the match is csda's made-up one."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clipper import analysis, stats, stats_view
from clipper.analysis import Analyses
from clipper.config import Config
from clipper.index import Index
from clipper.model import ClipFile, Highlight, MatchInfo
from clipper.settings import Loaded
from tests.csda_json import CHECKSUM, MATCH, SUBJECT, match
from tests.test_web import app_for, pc_get

PLAYED = datetime(2026, 10, 3, 17, 54, tzinfo=timezone.utc)
OTHER = "fedcba9876543210"


@pytest.fixture
def index(tmp_path):
    db = Index(tmp_path / "clipper.sqlite")
    yield db
    db.close()


def save(index: Index, checksum: str, when: datetime = PLAYED) -> None:
    index.save_match(MatchInfo(checksum=checksum, map_name="de_mirage", played_at=when, team_score=13,
                               opponent_score=9))


def write_stats(folder: Path, checksum: str, data: dict) -> None:
    """The stats file csda's analysis would have written for this match."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{checksum}.stats.json").write_text(json.dumps(stats.compute(analysis.stats_match(data))),
                                                   encoding="utf-8")


def clip_round_one(index: Index, checksum: str, *, reel: bool) -> int:
    """Round 1 of `checksum` with two rendered Clips (ticks 900-1156 and 2000-2256), and a Reel if `reel`."""
    demo_id = index.add_demo(f"{checksum}.dem.zst", "0" * 64, Path(f"{checksum}.dem.zst"))
    index.save_highlights(checksum, [Highlight(1, "3K", 30, (), (900,), 0, 6400)], {1})
    highlight_id = index.selected_highlights(checksum)[0]["id"]
    job = index.queue_render(demo_id, "player", attempt=1)
    index.add_clip(job, highlight_id, ClipFile(1, 900, 1156, Path("s1.mp4"), 4.0), "s1.mp4")
    index.add_clip(job, highlight_id, ClipFile(2, 2000, 2256, Path("s2.mp4"), 4.0), "s2.mp4")
    index.finish_render(job, "done")
    if reel:
        index.save_reel(highlight_id, "player", "r1-player.mp4", 8.0)
    return highlight_id


def test_a_tick_is_placed_in_its_reel_in_seconds_from_the_start(index):
    save(index, CHECKSUM)
    clip_round_one(index, CHECKSUM, reel=True)
    reel = index.best_reel(CHECKSUM, "player")
    assert index.clip_moments(CHECKSUM, 1, 1000, 64.0) == [{"reel_id": reel, "perspective": "player", "seconds": 1.56}]
    assert index.clip_moments(CHECKSUM, 1, 2100, 64.0) == [
        {"reel_id": reel, "perspective": "player", "seconds": 5.56}]     # 4.0 s of the first Clip, then 1.56
    assert index.clip_moments(CHECKSUM, 1, 1500, 64.0) == []              # between the two Clips
    assert index.clip_moments(CHECKSUM, 2, 1000, 64.0) == []              # another round


def test_a_clip_with_no_reel_yet_gives_no_moment(index):
    save(index, CHECKSUM)
    clip_round_one(index, CHECKSUM, reel=False)
    assert index.clip_moments(CHECKSUM, 1, 1000, 64.0) == []


def test_the_match_view_is_not_computed_until_its_stats_are(index, tmp_path):
    save(index, CHECKSUM)
    analyses = Analyses(tmp_path / "analyses", tmp_path / "csda.exe")
    view = stats_view.match_view(index, analyses, SUBJECT, CHECKSUM)
    assert (view["state"], view["map"], view["subject"]) == ("not_computed", "de_mirage", SUBJECT)
    assert stats_view.match_view(index, analyses, SUBJECT, OTHER) is None


def test_the_match_view_has_the_players_the_timeline_with_clip_links_and_the_comparison(index, tmp_path):
    save(index, CHECKSUM)
    clip_round_one(index, CHECKSUM, reel=True)
    write_stats(tmp_path / "analyses", CHECKSUM, MATCH)
    view = stats_view.match_view(index, Analyses(tmp_path / "analyses", tmp_path / "csda.exe"), SUBJECT, CHECKSUM)
    assert view["state"] == "ready" and view["tickrate"] == 64.0 and view["faceit"] is False
    you = next(player for player in view["players"] if player["you"])
    assert (you["steam_id"], you["name"], you["headline"]["source"]) == (SUBJECT, "subject", "demo")
    headshot = next(event for event in view["rounds"][0]["events"] if event["tick"] == 3100)
    assert headshot["killer"] == SUBJECT and headshot["headshot"] is True
    assert headshot["clips"] == []                                        # tick 3100 is outside both Clips
    assert view["comparison"]["count"] == 0


def test_a_kill_inside_a_clip_links_to_its_reel(index, tmp_path):
    save(index, CHECKSUM)
    clip_round_one(index, CHECKSUM, reel=True)
    inside = match(kills=[{**k, "tick": 1000} if k["tick"] == 3100 else k for k in MATCH["kills"]])
    write_stats(tmp_path / "analyses", CHECKSUM, inside)
    view = stats_view.match_view(index, Analyses(tmp_path / "analyses", tmp_path / "csda.exe"), SUBJECT, CHECKSUM)
    kill = next(event for event in view["rounds"][0]["events"] if event["tick"] == 1000)
    reel = index.best_reel(CHECKSUM, "player")
    assert kill["clips"] == [{"reel_id": reel, "perspective": "player", "seconds": round(100 / 64, 2)}]


def test_the_comparison_is_against_the_most_recent_other_matches_with_stats(index, tmp_path):
    save(index, CHECKSUM)
    save(index, OTHER, datetime(2026, 9, 1, tzinfo=timezone.utc))         # earlier, with stats
    save(index, "1111111111111111", datetime(2026, 8, 1, tzinfo=timezone.utc))   # earlier, no stats
    write_stats(tmp_path / "analyses", CHECKSUM, MATCH)
    write_stats(tmp_path / "analyses", OTHER, match(checksum=OTHER))
    view = stats_view.match_view(index, Analyses(tmp_path / "analyses", tmp_path / "csda.exe"), SUBJECT, CHECKSUM)
    assert view["comparison"]["count"] == 1
    assert view["comparison"]["deltas"]["kills"] == 0.0


def test_the_stats_endpoint_answers_for_a_match_and_404s_for_none(tmp_path):
    index = Index(tmp_path / "clipper.sqlite")
    save(index, CHECKSUM)
    index.close()
    write_stats(tmp_path / "analyses", CHECKSUM, MATCH)
    settings = lambda: Loaded(Config(analyses_dir=tmp_path / "analyses", subject_steamid=SUBJECT), ())  # noqa: E731
    client = app_for(tmp_path, load_settings=settings).test_client()
    ready = pc_get(client, f"/api/stats/{CHECKSUM}")
    assert ready.status_code == 200 and ready.get_json()["state"] == "ready"
    assert pc_get(client, f"/api/stats/{OTHER}").status_code == 404
    assert pc_get(client, "/stats").status_code == 200
