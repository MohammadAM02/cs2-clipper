"""The replay's web answers: the match's replay state, asking for it, the radar image and the page. The index, the files
and the web app are real; the match is csda's made-up one."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from clipper import analysis, replay, stats
from clipper.backfill import REPLAY_FAILED, REPLAY_REQUESTED
from clipper.config import Config
from clipper.index import Index
from clipper.model import MatchInfo
from clipper.settings import Loaded
from tests.csda_json import CHECKSUM, MATCH, SUBJECT
from tests.test_web import MARKED, app_for, pc_get, pc_post

PNG = b"\x89PNG\r\n\x1a\n" + b"not really a picture"


def setup(tmp_path, *, positions: bool, radar: bool = False):
    index = Index(tmp_path / "clipper.sqlite")
    index.save_match(MatchInfo(checksum=CHECKSUM, map_name="de_mirage",
                               played_at=datetime(2026, 10, 3, 17, 54, tzinfo=timezone.utc), team_score=13,
                               opponent_score=9))
    index.close()
    analyses = tmp_path / "analyses"
    analyses.mkdir()
    (analyses / f"{CHECKSUM}.stats.json").write_text(json.dumps(stats.compute(analysis.stats_match(MATCH))),
                                                     encoding="utf-8")
    if positions:
        rows = [[0, 1, str(SUBJECT), 1.0, 2.0, 90.0, True, 2, 100]]
        (analyses / f"{CHECKSUM}.positions.json").write_text(json.dumps(replay.document(CHECKSUM, 64.0, rows)),
                                                             encoding="utf-8")
    radar_dir = tmp_path / "radar"
    radar_dir.mkdir()
    if radar:
        (radar_dir / "de_mirage.png").write_bytes(PNG)
        (radar_dir / "de_mirage.json").write_text(json.dumps({"map": "de_mirage", "width": 1024, "height": 1024,
                                                              "pos_x": -3230.0, "pos_y": 1713.0, "scale": 5.0,
                                                              "rotate": 0}), encoding="utf-8")
    settings = Loaded(Config(analyses_dir=analyses, radar_dir=radar_dir, subject_steamid=SUBJECT), ())
    return app_for(tmp_path, load_settings=lambda: settings).test_client()


def test_a_match_that_is_not_there_has_no_replay(tmp_path):
    client = setup(tmp_path, positions=False)
    assert pc_get(client, "/api/replay/fedcba9876543210").status_code == 404
    assert pc_post(client, "/api/replay/fedcba9876543210", headers=MARKED).status_code == 404


def test_a_replay_is_asked_for_once_and_then_waits_for_the_game_to_close(tmp_path):
    client = setup(tmp_path, positions=False)
    before = pc_get(client, f"/api/replay/{CHECKSUM}").get_json()
    assert (before["state"], before["requested"]) == ("not_requested", False)
    assert "samples" not in before
    asked = pc_post(client, f"/api/replay/{CHECKSUM}", headers=MARKED)
    assert asked.status_code == 202 and asked.get_json() == {"requested": True}
    waiting = pc_get(client, f"/api/replay/{CHECKSUM}").get_json()
    assert (waiting["state"], waiting["requested"]) == ("requested", True)
    assert [player["name"] for player in waiting["players"]][:1] == ["subject"]


def test_a_failed_replay_says_why_and_can_be_asked_for_again(tmp_path):
    client = setup(tmp_path, positions=False)
    index = Index(tmp_path / "clipper.sqlite")
    index.set_flag(REPLAY_REQUESTED + CHECKSUM, "2026-10-22T10:00:00+00:00")      # a failed run stays asked for
    index.set_flag(REPLAY_FAILED + CHECKSUM, "csda gave no player positions: see demo-1-positions.log")
    index.close()
    failed = pc_get(client, f"/api/replay/{CHECKSUM}").get_json()
    assert (failed["state"], failed["failure"]) == ("failed", "csda gave no player positions: see demo-1-positions.log")
    assert pc_post(client, f"/api/replay/{CHECKSUM}", headers=MARKED).status_code == 202
    again = pc_get(client, f"/api/replay/{CHECKSUM}").get_json()
    assert (again["state"], again["failure"]) == ("requested", None)


def test_a_replay_without_the_marker_header_is_refused(tmp_path):
    client = setup(tmp_path, positions=False)
    assert pc_post(client, f"/api/replay/{CHECKSUM}").status_code == 403


def test_a_ready_replay_has_its_samples_and_the_radar_once_read(tmp_path):
    client = setup(tmp_path, positions=True, radar=True)
    body = pc_get(client, f"/api/replay/{CHECKSUM}").get_json()
    assert body["state"] == "ready" and body["map"] == "de_mirage"
    assert body["fields"][:3] == ["tick", "round", "steam_id"]
    assert body["samples"] == [[0, 1, str(SUBJECT), 1.0, 2.0, 90.0, True, 2, 100]]
    assert body["radar"]["pos_x"] == -3230.0 and body["radar"]["rotate"] == 0
    assert [round_["number"] for round_ in body["rounds"]] == [1, 2, 3]


def test_a_ready_replay_without_the_radar_says_so_through_null(tmp_path):
    client = setup(tmp_path, positions=True, radar=False)
    body = pc_get(client, f"/api/replay/{CHECKSUM}").get_json()
    assert body["state"] == "ready" and body["radar"] is None


def test_the_radar_image_is_served_only_for_a_map_name_and_once_there(tmp_path):
    client = setup(tmp_path, positions=False, radar=False)
    assert pc_get(client, "/radar/de_mirage.png").status_code == 404
    assert pc_get(client, "/radar/BAD.png").status_code == 404
    (tmp_path / "radar" / "de_mirage.png").write_bytes(PNG)
    picture = pc_get(client, "/radar/de_mirage.png")
    assert picture.status_code == 200 and picture.mimetype == "image/png"
    assert picture.data == PNG


def test_the_replay_page_is_served(tmp_path):
    client = setup(tmp_path, positions=False)
    page = pc_get(client, "/replay")
    assert page.status_code == 200 and page.mimetype == "text/html"
    assert b"canvas" in page.data
