from pathlib import Path

import pytest

from clipper.config import Config, load_config, load_env


def test_defaults_match_the_spec(tmp_path):
    cfg = load_config(tmp_path / "missing.toml")
    assert cfg.top_n == 5
    assert (cfg.padding_before_s, cfg.padding_after_s) == (4.0, 2.0)
    assert cfg.stall_seconds == 180.0
    assert cfg.library_dir == Path("E:/cs2clips/library")
    assert cfg.csdm_cli_js.name == "cli.js"


def test_toml_overrides_and_converts_paths(tmp_path):
    toml = tmp_path / "clipper.toml"
    toml.write_text('top_n = 3\ndata_root = "D:/clips"\n', encoding="utf-8")
    cfg = load_config(toml)
    assert cfg.top_n == 3
    assert cfg.data_root == Path("D:/clips")
    assert cfg.demos_dir == Path("D:/clips/demos")


def test_an_unknown_setting_is_rejected(tmp_path):
    toml = tmp_path / "clipper.toml"
    toml.write_text("topn = 3\n", encoding="utf-8")
    with pytest.raises(ValueError, match="topn"):
        load_config(toml)


def test_database_conninfo_comes_from_csdm_settings(tmp_path):
    settings = tmp_path / ".csdm" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(
        '{"database": {"hostname": "127.0.0.1", "port": 5432, "username": "postgres",'
        ' "password": "pw", "database": "csdm"}}',
        encoding="utf-8",
    )
    cfg = Config(downloads_dir=tmp_path, csdm_home=tmp_path)
    assert cfg.database_conninfo() == {
        "host": "127.0.0.1", "port": 5432, "user": "postgres", "password": "pw", "dbname": "csdm",
    }


def test_picture_and_sequence_settings(tmp_path):
    cfg = load_config(tmp_path / "missing.toml")
    assert (cfg.aspect_ratio, cfg.video_size, cfg.stretch, cfg.sequence_event) == ("16:9", (1920, 1080), False, "kills")
    toml = tmp_path / "clipper.toml"
    toml.write_text('aspect_ratio = "4:3-stretched"\nsequence_event = "rounds"\n', encoding="utf-8")
    cfg = load_config(toml)
    assert (cfg.video_size, cfg.stretch, cfg.sequence_event) == ((1280, 960), True, "rounds")


def test_an_unknown_value_is_rejected(tmp_path):
    toml = tmp_path / "clipper.toml"
    toml.write_text('aspect_ratio = "21:9"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="aspect_ratio"):
        load_config(toml)


def test_match_alert_settings_have_defaults(tmp_path):
    cfg = load_config(tmp_path / "missing.toml")
    assert (cfg.match_alerts, cfg.stopped_playing_minutes, cfg.page_port) == (True, 5.0, 8765)


def test_load_env_reads_keys_and_skips_comments(tmp_path):
    env = tmp_path / ".env"
    env.write_text('# a comment\n\nFACEIT_API_KEY="abc-123"\nFACEIT_NICKNAME=someone\nBROKEN LINE\n',
                   encoding="utf-8")
    assert load_env(env) == {"FACEIT_API_KEY": "abc-123", "FACEIT_NICKNAME": "someone"}


def test_load_env_of_a_missing_file_is_empty(tmp_path):
    assert load_env(tmp_path / ".env") == {}
