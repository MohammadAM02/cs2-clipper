"""Tests for clipper.paths: the app data folder, its layout, and atomic writes."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from clipper import paths
from tests.fakes import refusing


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

    for folder, name in ((paths.tools_dir(), "tools"), (paths.analyses_dir(), "analyses")):
        assert folder == paths.data_dir() / name
        assert not folder.exists()          # made when something goes in


def test_atomic_write_text_replaces_an_existing_file_and_leaves_no_tmp(tmp_path):
    target = tmp_path / "settings.json"
    target.write_text("old content", encoding="utf-8")

    paths.atomic_write_text(target, "café ☃")

    assert target.read_text(encoding="utf-8") == "café ☃"
    assert not target.with_name(target.name + ".tmp").exists()


def test_a_move_windows_refuses_for_a_moment_is_tried_again(tmp_path, monkeypatch):
    src, dst, naps = tmp_path / "new", tmp_path / "old", []
    src.write_text("new")
    dst.write_text("old")
    monkeypatch.setattr(os, "replace", refusing(2))

    paths.move_into_place(src, dst, sleep=naps.append)

    assert dst.read_text() == "new" and not src.exists()
    assert naps == [paths.MOVE_WAIT_SECONDS] * 2


def test_a_move_that_stays_refused_gives_up_and_leaves_both_as_they_were(tmp_path, monkeypatch):
    src, dst, naps = tmp_path / "new", tmp_path / "old", []
    src.write_text("new")
    dst.write_text("old")
    monkeypatch.setattr(os, "replace", refusing(10_000))

    with pytest.raises(PermissionError):
        paths.move_into_place(src, dst, sleep=naps.append)

    assert (src.read_text(), dst.read_text()) == ("new", "old")
    assert len(naps) == paths.MOVE_TRIES - 1


def test_atomic_write_text_gets_past_a_file_that_is_being_read_for_a_moment(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    target.write_text("old content", encoding="utf-8")
    monkeypatch.setattr(os, "replace", refusing(2))
    monkeypatch.setattr(time, "sleep", lambda seconds: None)

    paths.atomic_write_text(target, "new content")

    assert target.read_text(encoding="utf-8") == "new content"


def test_conftest_keeps_this_test_off_the_real_localappdata():
    real = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))) / paths.APP_FOLDER

    assert paths.data_dir() != real
