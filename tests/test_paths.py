"""Tests for clipper.paths: the app data folder, its layout, and atomic writes."""
from __future__ import annotations

import os
from pathlib import Path

from clipper import paths


def test_data_dir_defaults_to_localappdata_cs2clipper(tmp_path, monkeypatch):
    monkeypatch.delenv(paths.ENV_VAR, raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    path = paths.data_dir()

    assert path == tmp_path / "CS2Clipper"
    assert path.is_dir()
    assert paths.overridden() is False


def test_data_dir_honours_the_override(tmp_path, monkeypatch):
    override = tmp_path / "wherever-i-want"
    monkeypatch.setenv(paths.ENV_VAR, str(override))

    assert paths.data_dir() == override
    assert paths.overridden() is True


def test_the_file_layout(tmp_path, monkeypatch):
    monkeypatch.setenv(paths.ENV_VAR, str(tmp_path / "data"))

    assert paths.settings_file() == paths.data_dir() / "settings.json"
    assert paths.index_file() == paths.data_dir() / "clipper.sqlite"
    assert paths.lock_file() == paths.data_dir() / "clipper.lock"

    logs = paths.logs_dir()
    assert logs == paths.data_dir() / "logs"
    assert logs.is_dir()

    csdm_home = paths.csdm_home()
    assert csdm_home == paths.data_dir() / "csdm-home"
    assert not csdm_home.exists()


def test_atomic_write_text_replaces_an_existing_file_and_leaves_no_tmp(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text("old content", encoding="utf-8")

    paths.atomic_write_text(target, "café ☃")

    assert target.read_text(encoding="utf-8") == "café ☃"
    assert not target.with_name(target.name + ".tmp").exists()


def test_conftest_keeps_this_test_off_the_real_localappdata():
    real = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / paths.APP_FOLDER

    assert paths.data_dir() != real
