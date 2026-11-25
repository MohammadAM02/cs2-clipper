"""The Stats page's data: a kill's place in the Reels, the match view with the subject's Mistakes, and their comparison
with their earlier matches. And Progress's and Heatmaps' data over every match. The index, the stats files and the
web app are real; the match is csda's made-up one."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clipper import analysis, mistakes, stats, stats_view
from clipper.analysis import Analyses
from clipper.config import Config
from clipper.index import Index
from clipper.backfill import radar_requested
from clipper.model import DEATH, DEATH_KEYS, ClipFile, Highlight, MatchInfo
from clipper.settings import Loaded
from tests.csda_json import ALLY, CHECKSUM, CT, ENEMY, MATCH, OTHER_ENEMY, SUBJECT, T, kill, match
from tests.test_web import MARKED, app_for, pc_get, pc_post, phone_get

PLAYED = datetime(2026, 10, 3, 17, 54, tzinfo=timezone.utc)
OTHER = "fedcba9876543210"


@pytest.fixture
def index(tmp_path):
    db = Index(tmp_path / "clipper.sqlite")
    yield db
    db.close()


def save(index: Index, checksum: str, when: datetime = PLAYED, map_name: str = "de_mirage") -> None:
    index.save_match(MatchInfo(checksum=checksum, map_name=map_name, played_at=when, team_score=13,
                               opponent_score=9))


def write_stats(folder: Path, checksum: str, data: dict) -> None:
    """The stats file csda's analysis would have written for this match."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{checksum}.stats.json").write_text(json.dumps(stats.compute(analysis.stats_match(data))),
                                                   encoding="utf-8")


def clip_round_one(index: Index, checksum: str, *, reel: bool) -> int:
    """Round 1 of `checksum` with two rendered Clips (ticks 900-1156 and 2000-2256), and a Reel if `reel`."""
    demo_id = index.add_demo(f"{checksum}.dem.zst", "0" * 64, Path(f"{checksum}.dem.zst"))
    index.save_highlights(checksum, [Highlight(1, "3K", 30, (), (900,), 0, 6400)])
    index.mark_picked(checksum, {1})
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


# --- the subject's Mistakes on the Stats page (spec: Progress) --------------------------------------------

def write_match(folder: Path, checksum: str, data: dict) -> None:
    """The analysis and the stats the app would have kept of this match."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{checksum}.json").write_text(json.dumps(analysis.trim(data)), encoding="utf-8")
    write_stats(folder, checksum, data)


def test_the_match_view_lists_the_subjects_mistakes_in_words(index, tmp_path):
    save(index, CHECKSUM)
    write_match(tmp_path / "analyses", CHECKSUM, MATCH)
    view = stats_view.match_view(index, Analyses(tmp_path / "analyses", tmp_path / "csda.exe"), SUBJECT, CHECKSUM)
    team_kill, own = view["mistakes"]["list"]
    assert (team_kill["type"], team_kill["round"], team_kill["tick"]) == (mistakes.TEAM_KILL, 2, 11000)
    assert team_kill["title"] == "Killed a teammate" and team_kill["detail"] == "You killed Ally with the AK-47."
    assert team_kill["tip"] == mistakes.KINDS[mistakes.TEAM_KILL]["tip"] and team_kill["clips"] == []
    assert own["detail"] == "Your own AK-47 killed you."
    assert view["mistakes"]["by_type"] == {mistakes.TEAM_KILL: 1, mistakes.OWN_DEATH: 1}
    assert view["mistakes"]["flashes_kept"] is True


def test_each_kind_of_mistake_is_said_to_the_player():
    names = {SUBJECT: "subject", ALLY: "Ally", ENEMY: "enemy"}
    said = lambda **fields: stats_view._said(fields, names, SUBJECT)  # noqa: E731
    assert said(type=mistakes.OPENING_DEATH, other=ENEMY, weapon="AWP") == (
        "enemy got you with the AWP before anyone else fell, and nobody traded it.")
    assert said(type=mistakes.DIED_BLIND, other=ENEMY, weapon="AWP", team_flash=False) == (
        "enemy got you with the AWP while you were flashed.")
    assert said(type=mistakes.DIED_BLIND, other=ALLY, team_flash=True) == (
        "You were still blind from Ally's flash when you died.")
    assert said(type=mistakes.DIED_BLIND, other=SUBJECT, team_flash=True) == (
        "You were still blind from your own flash when you died.")
    assert said(type=mistakes.THREW_ADVANTAGE, other=ENEMY, weapon="MP9", ours=4, theirs=2) == (
        "A 4v2 in your favour: enemy got you with the MP9, untraded.")
    assert said(type=mistakes.TEAM_FLASH, other=ALLY, blind_s=3.1, blinded=1) == "Your flash blinded Ally for 3.1 s."
    assert said(type=mistakes.TEAM_FLASH, other=ALLY, blind_s=3.1, blinded=3) == (
        "Your flash blinded Ally for 3.1 s, and 2 more of your team with them.")
    assert said(type=mistakes.BOMB_DEATH, weapon="C4") == "The bomb went off with you still in range."
    assert said(type=mistakes.OWN_DEATH, weapon="World") == "You died to the world: a fall, most likely."
    assert said(type=mistakes.OPENING_DEATH, other="gone", weapon="") == (
        "someone got you with the a weapon before anyone else fell, and nobody traded it.")


def test_a_death_while_blind_from_the_teams_flash_gets_the_tip_for_it(index, tmp_path):
    save(index, CHECKSUM)
    data = match(kills=[kill(3000, 1, ENEMY, SUBJECT, killer_side=CT, victim_side=T, is_victim_blinded=True)],
                 playersFlashed=[{"tick": 2950, "roundNumber": 1, "flasherSteamId": int(ALLY),
                                  "flashedSteamId": int(SUBJECT), "duration": 3.0}])
    write_match(tmp_path / "analyses", CHECKSUM, data)
    view = stats_view.match_view(index, Analyses(tmp_path / "analyses", tmp_path / "csda.exe"), SUBJECT, CHECKSUM)
    [blind] = view["mistakes"]["list"]
    assert (blind["type"], blind["team_flash"], blind["tip"]) == (mistakes.DIED_BLIND, True, mistakes.BLIND_BY_TEAM_TIP)


def test_a_mistake_links_to_its_deaths_own_reel(index, tmp_path):
    save(index, CHECKSUM)
    data = match(kills=[kill(3000, 1, ENEMY, SUBJECT, killer_side=CT, victim_side=T)])
    write_match(tmp_path / "analyses", CHECKSUM, data)
    demo_id = index.add_demo(f"{CHECKSUM}.dem.zst", "0" * 64, Path(f"{CHECKSUM}.dem.zst"))
    index.save_highlights(CHECKSUM, [Highlight(1, DEATH, 1, (), (), 1000, 9000, worthy=True)])
    index.mark_picked(CHECKSUM, {1 + DEATH_KEYS})
    highlight_id = index.selected_highlights(CHECKSUM)[0]["id"]
    job = index.queue_render(demo_id, "player", attempt=1)
    index.add_clip(job, highlight_id, ClipFile(1, 2744, 3128, Path("d1.mp4"), 6.0), "d1.mp4")
    index.finish_render(job, "done")
    index.save_reel(highlight_id, "player", "d1-player.mp4", 6.0)
    view = stats_view.match_view(index, Analyses(tmp_path / "analyses", tmp_path / "csda.exe"), SUBJECT, CHECKSUM)
    [opening] = view["mistakes"]["list"]
    assert opening["type"] == mistakes.OPENING_DEATH
    reel_id = index.all_reel_details()[0]["id"]
    assert opening["clips"] == [{"reel_id": reel_id, "perspective": "player", "seconds": 4.0}]    # 256 ticks in


def test_without_the_subject_in_the_match_or_its_analysis_there_are_no_mistakes_to_list(index, tmp_path):
    save(index, CHECKSUM)
    write_stats(tmp_path / "analyses", CHECKSUM, MATCH)                    # stats, but no kept analysis
    analyses = Analyses(tmp_path / "analyses", tmp_path / "csda.exe")
    assert stats_view.match_view(index, analyses, SUBJECT, CHECKSUM)["mistakes"] is None
    assert stats_view.match_view(index, analyses, "76561190000000099", CHECKSUM)["mistakes"] is None


# --- Progress (spec: Progress) ----------------------------------------------------------------------------

def test_progress_covers_the_matches_the_subject_has_stats_in(index, tmp_path):
    save(index, CHECKSUM)
    save(index, OTHER, datetime(2026, 9, 1, tzinfo=timezone.utc), "de_nuke")
    save(index, "1111111111111111", datetime(2026, 8, 1, tzinfo=timezone.utc))      # no analysis at all
    write_match(tmp_path / "analyses", CHECKSUM, MATCH)
    write_match(tmp_path / "analyses", OTHER, match(checksum=OTHER))
    view = stats_view.progress_view(index, Analyses(tmp_path / "analyses", tmp_path / "csda.exe"), SUBJECT, 20)
    assert (view["state"], view["subject"], view["matches"], view["left_out"]) == ("ready", SUBJECT, 2, 1)
    assert [m["checksum"] for m in view["recent"]] == [CHECKSUM, OTHER]
    assert view["recent"][0]["mistakes"] == 2 and view["form"]["kd"]["value"] == 4.0
    assert [row["map"] for row in view["maps"]] == ["de_mirage", "de_nuke"]
    assert {row["type"] for row in view["mistakes"]} == {mistakes.TEAM_KILL, mistakes.OWN_DEATH}


def test_progress_is_empty_without_matches_without_stats_or_without_a_subject(index, tmp_path):
    analyses = Analyses(tmp_path / "analyses", tmp_path / "csda.exe")
    assert stats_view.progress_view(index, analyses, SUBJECT, 20)["state"] == "empty"
    save(index, CHECKSUM)
    (tmp_path / "analyses").mkdir()
    (tmp_path / "analyses" / f"{CHECKSUM}.json").write_text(json.dumps(analysis.trim(MATCH)), encoding="utf-8")
    no_stats = stats_view.progress_view(index, analyses, SUBJECT, 20)
    assert (no_stats["state"], no_stats["left_out"]) == ("empty", 1)
    write_stats(tmp_path / "analyses", CHECKSUM, MATCH)
    assert stats_view.progress_view(index, analyses, "", 20)["state"] == "empty"
    assert stats_view.progress_view(index, analyses, SUBJECT, 20)["state"] == "ready"


# --- Heatmaps (spec: Progress) ----------------------------------------------------------------------------

def placed_match(checksum: str = CHECKSUM, **changes) -> dict:
    """The made-up match with a place on two of the subject's kills and one death."""
    where = {"killerX": 100.0, "killerY": 200.0, "victimX": -300.0, "victimY": 400.0}
    kills = [kill(3100, 1, SUBJECT, ENEMY, **where), kill(3000, 1, SUBJECT, OTHER_ENEMY, **where),
             kill(11500, 2, ENEMY, SUBJECT, killer_side=CT, victim_side=T, **where)]
    return match(checksum=checksum, kills=kills, **changes)


def test_the_heatmap_is_of_the_most_played_map_unless_one_is_asked_for(index, tmp_path):
    save(index, CHECKSUM)
    save(index, OTHER, datetime(2026, 9, 1, tzinfo=timezone.utc))
    save(index, "2222222222222222", datetime(2026, 8, 1, tzinfo=timezone.utc), "de_nuke")
    save(index, "1111111111111111", datetime(2026, 7, 1, tzinfo=timezone.utc), "de_nuke")       # no analysis
    for checksum in (CHECKSUM, OTHER, "2222222222222222"):
        write_match(tmp_path / "analyses", checksum, placed_match(checksum))
    analyses, radar_dir = Analyses(tmp_path / "analyses", tmp_path / "csda.exe"), tmp_path / "radar"
    view = stats_view.heatmap_view(index, analyses, SUBJECT, radar_dir)
    assert (view["state"], view["map"], view["match"], view["radar"]) == ("ready", "de_mirage", None, None)
    assert view["maps"] == [{"map": "de_mirage", "matches": 2}, {"map": "de_nuke", "matches": 1}]
    assert [m["checksum"] for m in view["matches"]] == [CHECKSUM, OTHER]
    assert [(p["kind"], p["m"]) for p in view["places"]] == [("kill", 0), ("kill", 0), ("death", 0),
                                                             ("kill", 1), ("kill", 1), ("death", 1)]
    assert view["places"][2] == {"kind": "death", "round": 2, "tick": 11500, "side": "T", "x": -300.0, "y": 400.0,
                                 "ox": 100.0, "oy": 200.0, "weapon": "AK-47", "headshot": False, "m": 0}
    nuke = stats_view.heatmap_view(index, analyses, SUBJECT, radar_dir, map_name="de_nuke")
    assert (nuke["map"], len(nuke["matches"]), len(nuke["places"])) == ("de_nuke", 1, 3)
    assert stats_view.heatmap_view(index, analyses, SUBJECT, radar_dir, map_name="de_cache")["map"] == "de_mirage"


def test_the_heatmap_can_be_of_one_match_or_of_the_last_few(index, tmp_path):
    save(index, CHECKSUM)
    save(index, OTHER, datetime(2026, 9, 1, tzinfo=timezone.utc), "de_nuke")
    for checksum in (CHECKSUM, OTHER):
        write_match(tmp_path / "analyses", checksum, placed_match(checksum))
    analyses, radar_dir = Analyses(tmp_path / "analyses", tmp_path / "csda.exe"), tmp_path / "radar"
    one = stats_view.heatmap_view(index, analyses, SUBJECT, radar_dir, checksum=OTHER, map_name="de_mirage")
    assert (one["map"], one["match"], [m["checksum"] for m in one["matches"]]) == ("de_nuke", OTHER, [OTHER])
    save(index, "3333333333333333", datetime(2026, 8, 1, tzinfo=timezone.utc))
    write_match(tmp_path / "analyses", "3333333333333333", placed_match("3333333333333333"))
    last = stats_view.heatmap_view(index, analyses, SUBJECT, radar_dir, last=1)
    assert [m["checksum"] for m in last["matches"]] == [CHECKSUM]
    assert stats_view.heatmap_view(index, analyses, SUBJECT, radar_dir, checksum="abc")["match"] is None


def test_the_heatmap_is_empty_with_no_analyzed_match(index, tmp_path):
    save(index, CHECKSUM)
    view = stats_view.heatmap_view(index, Analyses(tmp_path / "analyses", tmp_path / "csda.exe"), SUBJECT,
                                   tmp_path / "radar")
    assert (view["state"], view["map"], view["maps"], view["places"]) == ("empty", None, [], [])


def heat_client(tmp_path, *, radar: bool = False):
    index = Index(tmp_path / "clipper.sqlite")
    save(index, CHECKSUM)
    index.close()
    write_match(tmp_path / "analyses", CHECKSUM, placed_match())
    radar_dir = tmp_path / "radar"
    radar_dir.mkdir()
    if radar:
        (radar_dir / "de_mirage.png").write_bytes(b"png")
        (radar_dir / "de_mirage.json").write_text(json.dumps({"map": "de_mirage", "width": 1024, "height": 1024,
                                                              "pos_x": -3230.0, "pos_y": 1713.0, "scale": 5.0,
                                                              "rotate": 0}), encoding="utf-8")
    settings = Loaded(Config(analyses_dir=tmp_path / "analyses", radar_dir=radar_dir, subject_steamid=SUBJECT), ())
    return app_for(tmp_path, load_settings=lambda: settings).test_client()


def test_the_heatmap_endpoint_gives_the_radar_once_read_and_ignores_what_is_malformed(tmp_path):
    client = heat_client(tmp_path, radar=True)
    body = pc_get(client, "/api/heatmap?map=de_mirage").get_json()
    assert (body["map"], body["radar"]["scale"], body["radar_requested"], len(body["places"])) == (
        "de_mirage", 5.0, False, 3)
    odd = pc_get(client, "/api/heatmap?map=..%2F..&match=NOPE&last=-4")
    assert odd.status_code == 200 and (odd.get_json()["map"], odd.get_json()["match"]) == ("de_mirage", None)
    assert pc_get(client, f"/api/heatmap?match={CHECKSUM}").get_json()["match"] == CHECKSUM
    assert pc_get(client, "/heatmaps").status_code == 200
    assert phone_get(client, "/api/heatmap").status_code == 403


def test_a_radar_is_asked_for_only_for_a_map_the_player_has_a_match_on(tmp_path):
    client = heat_client(tmp_path)
    assert pc_get(client, "/api/heatmap").get_json()["radar_requested"] is False
    assert pc_post(client, "/api/radar/de_mirage").status_code == 403                # unmarked
    assert pc_post(client, "/api/radar/de_nuke", headers=MARKED).status_code == 404  # no match on it
    assert pc_post(client, "/api/radar/NOT-A-MAP", headers=MARKED).status_code == 404
    asked = pc_post(client, "/api/radar/de_mirage", headers=MARKED)
    assert asked.status_code == 202 and asked.get_json() == {"requested": True}
    assert pc_get(client, "/api/heatmap").get_json()["radar_requested"] is True
    index = Index(tmp_path / "clipper.sqlite")
    assert radar_requested(index, "de_mirage")
    index.close()


def test_the_progress_endpoint_answers_for_the_window_asked_for(tmp_path):
    client = heat_client(tmp_path)
    body = pc_get(client, "/api/progress?last=10").get_json()
    assert (body["state"], body["matches"], body["asked"], body["window"]) == ("ready", 1, 10, 1)
    assert pc_get(client, "/api/progress?last=banana").get_json()["asked"] == 20
    assert pc_get(client, "/api/progress").get_json()["asked"] == 20
    assert pc_get(client, "/progress").status_code == 200
