"""Tests for clipper.move_in: the one-time copy from the repo (spec: Settings and data, Moving in from
the repo). Every test builds its own fake repo layout under tmp_path; the real repo's .env,
clipper.toml, data\\ and home\\ must never be touched."""
from __future__ import annotations

import json
import logging
import shutil
import sys
from pathlib import Path

import pytest

from clipper import paths, protect
from clipper.index import Index
from clipper.move_in import OLD_CLIPS_FOLDER, OLD_STEAMID, move_in, needed, on_start
from clipper.settings import STORED_KEY


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_settings(data_dir: Path) -> dict:
    return json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))


def _full_fake_repo(tmp_path: Path) -> Path:
    """A fake repo (never the real one) holding all four old-setup items."""
    repo = tmp_path / "repo"
    _write(repo / "clipper.toml",
           'top_n = 3\npoll_seconds = 5.0\nindex_path = "C:/old/index.sqlite"\ncsdm_home = "C:/old/home"\n')
    _write(repo / ".env", 'FACEIT_NICKNAME=someplayer\nFACEIT_API_KEY="a-real-faceit-key"\n')
    index_path = repo / "data" / "clipper.sqlite"
    index_path.parent.mkdir(parents=True)
    index_path.write_bytes(b"fake sqlite bytes, just for a byte-identical copy check")
    home_settings = repo / "home" / ".csdm" / "settings.json"
    _write(home_settings, '{"database": {"password": "hunter2"}}')
    return repo


# --- needed() -----------------------------------------------------------------------------------------


def test_needed_is_false_for_a_repo_with_none_of_the_four_items(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    assert needed(repo, data_dir) is False


def test_needed_is_true_when_any_one_item_exists(tmp_path):
    repo = tmp_path / "repo"
    _write(repo / "clipper.toml", "top_n = 3\n")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    assert needed(repo, data_dir) is True


def test_needed_is_false_once_settings_json_exists(tmp_path):
    repo = _full_fake_repo(tmp_path)
    data_dir = tmp_path / "data"
    _write(data_dir / "settings.json", "{}")

    assert needed(repo, data_dir) is False


# --- move_in(): every item is copied --------------------------------------------------------------------


def test_every_item_is_copied(tmp_path):
    repo = _full_fake_repo(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    copied = move_in(repo, data_dir)

    assert copied == ["the index", "CS Demo Manager's settings folder", "the settings", "the FACEIT key"]

    assert (data_dir / "clipper.sqlite").read_bytes() == (repo / "data" / "clipper.sqlite").read_bytes()
    assert (data_dir / "csdm-home" / ".csdm" / "settings.json").read_text(encoding="utf-8") == \
        (repo / "home" / ".csdm" / "settings.json").read_text(encoding="utf-8")

    values = _read_settings(data_dir)
    assert values["top_n"] == 3                    # changed from the default: written
    assert "poll_seconds" not in values             # equal to the new default: not written
    assert "index_path" not in values               # old-only key: dropped
    assert "csdm_home" not in values                 # old-only key: dropped
    assert values["faceit_nickname"] == "someplayer"
    assert values["subject_steamid"] == OLD_STEAMID
    assert values["data_root"] == OLD_CLIPS_FOLDER

    text = (data_dir / "settings.json").read_text(encoding="utf-8")
    assert "a-real-faceit-key" not in text
    assert protect.unprotect(values[STORED_KEY]) == "a-real-faceit-key"


def test_the_originals_are_untouched(tmp_path):
    repo = _full_fake_repo(tmp_path)
    original_toml = (repo / "clipper.toml").read_text(encoding="utf-8")
    original_env = (repo / ".env").read_text(encoding="utf-8")
    original_index = (repo / "data" / "clipper.sqlite").read_bytes()
    original_home = (repo / "home" / ".csdm" / "settings.json").read_text(encoding="utf-8")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    move_in(repo, data_dir)

    assert (repo / "clipper.toml").read_text(encoding="utf-8") == original_toml
    assert (repo / ".env").read_text(encoding="utf-8") == original_env
    assert (repo / "data" / "clipper.sqlite").read_bytes() == original_index
    assert (repo / "home" / ".csdm" / "settings.json").read_text(encoding="utf-8") == original_home


def test_it_runs_once(tmp_path):
    repo = _full_fake_repo(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    move_in(repo, data_dir)

    assert needed(repo, data_dir) is False


def test_the_old_steamid_and_clips_folder_are_carried_over_unless_the_toml_set_them(tmp_path):
    repo = tmp_path / "repo"
    _write(repo / "clipper.toml", 'data_root = "D:/clips"\n')
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    move_in(repo, data_dir)

    values = _read_settings(data_dir)
    assert values["subject_steamid"] == OLD_STEAMID     # not set by the TOML: the old default wins
    assert values["data_root"] == "D:/clips"             # set by the TOML: it wins over the old default


def test_env_values_that_are_empty_are_skipped(tmp_path):
    repo = tmp_path / "repo"
    _write(repo / ".env", "FACEIT_NICKNAME=\nFACEIT_API_KEY=\n")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    copied = move_in(repo, data_dir)

    assert "the FACEIT key" not in copied
    values = _read_settings(data_dir)
    assert "faceit_nickname" not in values
    assert STORED_KEY not in values


def test_the_key_never_appears_in_the_log_line(tmp_path, caplog):
    repo = _full_fake_repo(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    with caplog.at_level(logging.INFO):
        move_in(repo, data_dir)

    assert "a-real-faceit-key" not in caplog.text


# --- move_in(): the index and csdm-home are only replaced when safe to --------------------------------


def test_an_index_with_a_row_is_not_overwritten(tmp_path):
    repo = _full_fake_repo(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    dest = data_dir / "clipper.sqlite"
    index = Index(dest)
    index.add_demo("1-a.dem.zst", "a" * 64, tmp_path / "1-a.dem.zst")
    index.close()
    before = dest.read_bytes()

    copied = move_in(repo, data_dir)

    assert "the index" not in copied
    assert dest.read_bytes() == before


def test_an_empty_schema_index_is_replaced(tmp_path):
    repo = _full_fake_repo(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    dest = data_dir / "clipper.sqlite"
    Index(dest).close()          # schema only, no rows in demos or faceit_matches

    copied = move_in(repo, data_dir)

    assert "the index" in copied
    assert dest.read_bytes() == (repo / "data" / "clipper.sqlite").read_bytes()


def test_an_existing_csdm_home_is_not_overwritten(tmp_path):
    repo = _full_fake_repo(tmp_path)
    data_dir = tmp_path / "data"
    marker = data_dir / "csdm-home" / "marker.txt"
    _write(marker, "already here")

    copied = move_in(repo, data_dir)

    assert "CS Demo Manager's settings folder" not in copied
    assert marker.read_text(encoding="utf-8") == "already here"
    assert not (data_dir / "csdm-home" / ".csdm").exists()


# --- move_in(): the index and csdm-home copies are all-or-nothing --------------------------------------


def test_a_leftover_partial_csdm_home_is_cleaned_up_before_copying(tmp_path):
    repo = tmp_path / "repo"
    _write(repo / "home" / ".csdm" / "settings.json", '{"database": {"password": "hunter2"}}')
    data_dir = tmp_path / "data"
    _write(data_dir / "csdm-home.partial" / "stray-from-a-crashed-attempt.txt", "leftover")

    copied = move_in(repo, data_dir)

    assert "CS Demo Manager's settings folder" in copied
    dest = data_dir / "csdm-home"
    assert (dest / ".csdm" / "settings.json").read_text(encoding="utf-8") == \
        (repo / "home" / ".csdm" / "settings.json").read_text(encoding="utf-8")
    assert not (dest / "stray-from-a-crashed-attempt.txt").exists()
    assert not (data_dir / "csdm-home.partial").exists()


def test_a_csdm_home_copy_that_fails_midway_leaves_nothing_and_the_next_move_in_completes_it(
    tmp_path, monkeypatch,
):
    repo = tmp_path / "repo"
    _write(repo / "home" / ".csdm" / "settings.json", '{"database": {"password": "hunter2"}}')
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    real_copytree = shutil.copytree

    def flaky_copytree(src, dst, *args, **kwargs):
        Path(dst).mkdir(parents=True, exist_ok=True)
        (Path(dst) / "only-partly-there.txt").write_text("x", encoding="utf-8")
        raise OSError("disk full")

    monkeypatch.setattr("clipper.move_in.shutil.copytree", flaky_copytree)

    with pytest.raises(OSError):
        move_in(repo, data_dir)

    assert not (data_dir / "csdm-home").exists()
    assert not (data_dir / "settings.json").exists()

    monkeypatch.setattr("clipper.move_in.shutil.copytree", real_copytree)

    copied = move_in(repo, data_dir)

    assert "CS Demo Manager's settings folder" in copied
    assert (data_dir / "csdm-home" / ".csdm" / "settings.json").exists()
    assert not (data_dir / "csdm-home.partial").exists()


def test_an_index_copy_that_fails_midway_leaves_no_file_under_the_final_name(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    index_path = repo / "data" / "clipper.sqlite"
    index_path.parent.mkdir(parents=True)
    index_path.write_bytes(b"fake sqlite bytes, just for a byte-identical copy check")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    real_copy2 = shutil.copy2

    def flaky_copy2(src, dst, *args, **kwargs):
        Path(dst).write_bytes(b"only-partly-written")
        raise OSError("disk full")

    monkeypatch.setattr("clipper.move_in.shutil.copy2", flaky_copy2)

    with pytest.raises(OSError):
        move_in(repo, data_dir)

    assert not (data_dir / "clipper.sqlite").exists()
    assert not (data_dir / "settings.json").exists()

    monkeypatch.setattr("clipper.move_in.shutil.copy2", real_copy2)

    copied = move_in(repo, data_dir)

    assert "the index" in copied
    assert (data_dir / "clipper.sqlite").read_bytes() == index_path.read_bytes()


def test_an_index_copy_that_fails_midway_leaves_a_previous_empty_index_untouched(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    index_path = repo / "data" / "clipper.sqlite"
    index_path.parent.mkdir(parents=True)
    index_path.write_bytes(b"fake sqlite bytes, just for a byte-identical copy check")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    dest = data_dir / "clipper.sqlite"
    Index(dest).close()          # schema only, no rows: normally replaceable
    before = dest.read_bytes()

    def flaky_copy2(src, dst, *args, **kwargs):
        Path(dst).write_bytes(b"only-partly-written")
        raise OSError("disk full")

    monkeypatch.setattr("clipper.move_in.shutil.copy2", flaky_copy2)

    with pytest.raises(OSError):
        move_in(repo, data_dir)

    assert dest.read_bytes() == before            # untouched: the failed .partial never got swapped in
    assert not (data_dir / "settings.json").exists()


# --- on_start() -----------------------------------------------------------------------------------------


def test_on_start_returns_nothing_while_clipper_data_dir_is_set(tmp_path):
    repo = _full_fake_repo(tmp_path)   # a full fake repo: on_start must not even look at it

    assert on_start(repo) == []


def test_on_start_moves_in_when_not_overridden(tmp_path, monkeypatch):
    repo = _full_fake_repo(tmp_path)
    local_appdata = tmp_path / "local-appdata"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.delenv("CLIPPER_DATA_DIR", raising=False)

    copied = on_start(repo)

    assert copied == ["the index", "CS Demo Manager's settings folder", "the settings", "the FACEIT key"]
    data_dir = local_appdata / paths.APP_FOLDER
    assert (data_dir / "settings.json").exists()
    assert (data_dir / "clipper.sqlite").exists()


def test_on_start_never_moves_in_from_the_packaged_exe(tmp_path, monkeypatch):
    # In the exe, REPO_ROOT is the unpacked bundle, not a repo: even a repo-shaped folder is not copied.
    repo = _full_fake_repo(tmp_path)
    local_appdata = tmp_path / "local-appdata"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.delenv("CLIPPER_DATA_DIR", raising=False)
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert on_start(repo) == []
    assert not (local_appdata / paths.APP_FOLDER / "settings.json").exists()


def test_on_start_does_nothing_for_a_fresh_clone(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    local_appdata = tmp_path / "local-appdata"
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.delenv("CLIPPER_DATA_DIR", raising=False)

    assert on_start(repo) == []
    assert not (local_appdata / paths.APP_FOLDER / "settings.json").exists()
