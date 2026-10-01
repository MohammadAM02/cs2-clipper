"""Tests for clipper.csdm_settings: CS:DM's settings file lives under tmp_path, and CS:DM never runs."""
from __future__ import annotations

import json

import pytest

from clipper import csdm_settings
from clipper.csdm_settings import SettingsError

SECRET = "hunter2-do-not-log"
DATABASE = {"hostname": "127.0.0.1", "port": 5433, "username": "postgres", "password": SECRET, "database": "csdm"}


def on_disk(home):
    return json.loads(csdm_settings.settings_file(home).read_text(encoding="utf-8"))


def write(home, settings):
    path = csdm_settings.settings_file(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(settings if isinstance(settings, str) else json.dumps(settings), encoding="utf-8")


def test_the_file_is_where_cs_demo_manager_reads_it(tmp_path):
    assert csdm_settings.settings_file(tmp_path) == tmp_path / ".csdm" / "settings.json"
    assert csdm_settings.read(tmp_path) is None


def test_a_missing_file_is_written_whole_so_csdm_finds_every_section(tmp_path):
    written = csdm_settings.update(tmp_path, {"database": DATABASE})

    assert written == on_disk(tmp_path)
    assert written["schemaVersion"] == 13
    assert written["database"] == DATABASE
    for section in ("demos", "ui", "analyze", "playback", "video", "playerProfile", "download", "matches",
                    "players", "teams", "teamProfile", "ban"):
        assert isinstance(written[section], dict), section
    assert set(written["video"]["ffmpegSettings"]) >= {"audioBitrate", "constantRateFactor", "videoCodec",
                                                      "audioCodec", "inputParameters", "outputParameters"}


def test_a_new_file_records_the_way_the_render_expects(tmp_path):
    # render.py looks for sequence-<n>-tick-<a>-to-<b>.mp4, recorded through HLAE.
    video = csdm_settings.update(tmp_path, {})["video"]

    assert (video["recordingSystem"], video["encoderSoftware"]) == ("HLAE", "FFmpeg")
    assert (video["ffmpegSettings"]["videoContainer"], video["framerate"]) == ("mp4", 60)
    assert video["concatenateSequences"] is False


def test_a_new_file_has_no_database_until_setup_makes_one(tmp_path):
    assert "database" not in csdm_settings.update(tmp_path, {})
    assert csdm_settings.database(tmp_path) is None


def test_a_file_cs_demo_manager_wrote_changes_only_in_the_keys_asked_for(tmp_path):
    theirs = {"schemaVersion": 14, "steamApiKey": SECRET, "somethingNew": {"kept": [1, 2]},
              "video": {"framerate": 30, "hlae": {"customLocationEnabled": True, "customExecutableLocation": "C:\\HLAE\\hlae.exe"}}}
    write(tmp_path, theirs)

    csdm_settings.update(tmp_path, {"video": {"hlae": {"customLocationEnabled": False}}})

    theirs["video"]["hlae"]["customLocationEnabled"] = False
    assert on_disk(tmp_path) == theirs


def test_a_file_without_a_schema_version_is_filled_in_and_keeps_what_it_says(tmp_path):
    # scripts/setup_local_postgres.py wrote only this much on a fresh home: csdm video crashed on it.
    write(tmp_path, {"database": DATABASE, "video": {"framerate": 30}})

    written = csdm_settings.update(tmp_path, {})

    assert (written["schemaVersion"], written["database"], written["video"]["framerate"]) == (13, DATABASE, 30)
    assert written["video"]["recordingSystem"] == "HLAE"
    assert written["playback"]["round"]["afterRoundDelayInSeconds"] == 2


@pytest.mark.parametrize("content", ["{ not json", json.dumps(["a", "list"])])
def test_a_file_that_cannot_be_read_is_left_alone_and_the_error_shows_none_of_it(tmp_path, content):
    write(tmp_path, content + SECRET)
    before = csdm_settings.settings_file(tmp_path).read_bytes()

    with pytest.raises(SettingsError) as raised:
        csdm_settings.update(tmp_path, {"database": DATABASE})

    assert csdm_settings.settings_file(tmp_path).read_bytes() == before
    assert SECRET not in str(raised.value)
    assert str(csdm_settings.settings_file(tmp_path)) in str(raised.value)


def test_updating_leaves_the_template_as_it_was(tmp_path):
    first = csdm_settings.update(tmp_path / "one", {})
    first["folders"].append("changed")
    first["video"]["hlae"]["customLocationEnabled"] = True

    second = csdm_settings.update(tmp_path / "two", {})

    assert second["folders"] == []
    assert second["video"]["hlae"]["customLocationEnabled"] is False


@pytest.mark.parametrize("block", [None, {}, {"port": 5433}, {"port": 5433, "password": ""},
                                   {"port": "x", "password": SECRET}, "text"])
def test_a_database_block_without_a_password_or_a_port_does_not_count(tmp_path, block):
    write(tmp_path, {"schemaVersion": 13, "database": block})

    assert csdm_settings.database(tmp_path) is None


def test_the_database_block_is_handed_back_when_it_is_usable(tmp_path):
    write(tmp_path, {"schemaVersion": 13, "database": DATABASE})

    assert csdm_settings.database(tmp_path) == DATABASE


def test_ffmpeg_is_looked_for_where_cs_demo_manager_looks(tmp_path):
    managed = tmp_path / ".csdm" / "ffmpeg" / "bin" / "ffmpeg.exe"
    assert csdm_settings.ffmpeg_exe(tmp_path) == managed       # no settings file yet

    write(tmp_path, {"schemaVersion": 13, "video": {"ffmpegSettings": {"customLocationEnabled": True,
                                                                     "customExecutableLocation": ""}}})
    assert csdm_settings.ffmpeg_exe(tmp_path) == managed       # enabled but empty: CS:DM falls back too

    csdm_settings.update(tmp_path, {"video": {"ffmpegSettings": {"customExecutableLocation": "D:\\ff\\ffmpeg.exe"}}})
    assert str(csdm_settings.ffmpeg_exe(tmp_path)) == "D:\\ff\\ffmpeg.exe"
