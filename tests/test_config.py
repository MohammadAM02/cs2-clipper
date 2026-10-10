import pytest
from pathlib import Path

from clipper import paths
from clipper.config import Config


def test_defaults_have_no_steamid_or_clips_folder():
    cfg = Config()
    assert cfg.subject_steamid == ""
    assert cfg.faceit_nickname == ""
    assert cfg.faceit_api_key_protected == ""
    assert cfg.data_root is None


def test_defaults_match_the_spec():
    cfg = Config()
    assert cfg.top_n == 5
    assert (cfg.padding_before_s, cfg.padding_after_s) == (4.0, 2.0)
    assert cfg.stall_seconds == 180.0


def test_dirs_derived_from_the_clips_folder(tmp_path):
    cfg = Config(data_root=tmp_path / "clips")
    assert cfg.demos_dir == tmp_path / "clips" / "demos"
    assert cfg.renders_dir == tmp_path / "clips" / "renders"
    assert cfg.library_dir == tmp_path / "clips" / "library"


# --- paths as the index keeps them: relative to the clips folder (every Clips folder move otherwise
# --- stranded every Clip and Reel with a path that no longer existed) -------------------------------


def test_a_stored_path_is_relative_to_the_clips_folder(tmp_path):
    cfg = Config(data_root=tmp_path / "clips")

    stored = cfg.store_path(tmp_path / "clips" / "library" / "videos" / "r1.mp4")

    assert stored == str(Path("library") / "videos" / "r1.mp4")


def test_a_path_outside_the_clips_folder_is_stored_whole(tmp_path):
    outside = tmp_path / "elsewhere" / "r1.mp4"

    assert Config(data_root=tmp_path / "clips").store_path(outside) == str(outside)
    assert Config().store_path(outside) == str(outside)        # no clips folder to be relative to


def test_a_stored_path_comes_back_under_the_clips_folder(tmp_path):
    cfg = Config(data_root=tmp_path / "clips")

    assert cfg.load_path(str(Path("library") / "videos" / "r1.mp4")) == (
        tmp_path / "clips" / "library" / "videos" / "r1.mp4")


def test_moving_the_clips_folder_still_finds_what_was_stored_relative_to_it(tmp_path):
    stored = Config(data_root=tmp_path / "old").store_path(
        tmp_path / "old" / "library" / "videos" / "r1.mp4")

    assert Config(data_root=tmp_path / "new").load_path(stored) == (
        tmp_path / "new" / "library" / "videos" / "r1.mp4")


def test_an_absolute_path_from_before_the_change_still_reads(tmp_path):
    # Rows written earlier hold the whole path; joining an absolute path discards the clips folder.
    cfg = Config(data_root=tmp_path / "clips")
    assert cfg.load_path(str(tmp_path / "old" / "r1.mp4")) == tmp_path / "old" / "r1.mp4"
    assert Config().load_path(str(tmp_path / "old" / "r1.mp4")) == tmp_path / "old" / "r1.mp4"


def test_the_faceit_key_is_kept_out_of_repr():
    cfg = Config(faceit_api_key_protected="a-protected-blob")
    assert "a-protected-blob" not in repr(cfg)


def test_the_tools_are_in_the_tools_folder_unless_hlae_is_set(tmp_path):
    cfg = Config(tools_dir=tmp_path / "tools")
    assert cfg.csda_exe == tmp_path / "tools" / "csda" / "csda.exe"
    assert cfg.hlae_path == tmp_path / "tools" / "hlae" / "HLAE.exe"
    assert Config(hlae_exe=r"C:\HLAE\hlae.exe").hlae_path == Path(r"C:\HLAE\hlae.exe")


def test_picture_and_sequence_settings():
    cfg = Config()
    assert (cfg.aspect_ratio, cfg.video_size, cfg.stretch, cfg.sequence_event) == ("16:9", (1920, 1080), False, "kills")
    cfg = Config(aspect_ratio="4:3-stretched", sequence_event="rounds")
    assert (cfg.video_size, cfg.stretch, cfg.sequence_event) == ((1280, 960), True, "rounds")


def test_an_unknown_aspect_ratio_is_rejected():
    with pytest.raises(ValueError, match="aspect_ratio"):
        Config(aspect_ratio="21:9")


def test_an_unknown_sequence_event_is_rejected():
    with pytest.raises(ValueError, match="sequence_event"):
        Config(sequence_event="frags")


def test_both_perspectives_are_rendered_by_default():
    cfg = Config()
    assert (cfg.perspectives, cfg.perspectives_to_render) == ("both", ("player", "enemy"))


def test_one_perspective_can_be_rendered_alone():
    assert Config(perspectives="player").perspectives_to_render == ("player",)
    assert Config(perspectives="enemy").perspectives_to_render == ("enemy",)


def test_round_clips_are_rendered_from_the_players_view_only():
    for choice in ("both", "player", "enemy"):
        assert Config(perspectives=choice, sequence_event="rounds").perspectives_to_render == ("player",)


def test_an_unknown_perspectives_choice_is_rejected():
    with pytest.raises(ValueError, match="perspectives"):
        Config(perspectives="neither")


def test_match_alert_settings_have_defaults():
    cfg = Config()
    assert (cfg.match_alerts, cfg.stopped_playing_minutes, cfg.page_port) == (True, 5.0, 8765)


def test_the_app_data_folders_default_from_the_app_data_folder():
    cfg = Config()
    assert cfg.index_path == paths.index_file()
    assert cfg.logs_dir == paths.logs_dir()
    assert cfg.tools_dir == paths.tools_dir()
    assert cfg.analyses_dir == paths.analyses_dir()
    assert cfg.cs2_settings_dir == paths.cs2_settings_dir()
