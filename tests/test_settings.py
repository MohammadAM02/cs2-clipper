"""Tests for clipper.settings: settings.json load/validate/save and the FIELDS table (spec: Settings,
Settings and data)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from clipper import protect
from clipper.config import Config
from clipper.settings import (
    FIELDS, KEY_FIELD, STORED_KEY, Loaded, SettingsStore, defaults, json_values, load, save, validate,
)


def read_raw(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# --- FIELDS ------------------------------------------------------------------------------------------


def test_fields_cover_every_group_in_page_order():
    groups = [f.group for f in FIELDS]
    assert groups[0] == "You"
    assert groups[-1] == "App"
    assert {f.name for f in FIELDS} >= {"subject_steamid", "data_root", "match_alerts", "page_port"}
    assert FIELDS[0].name == "subject_steamid"


# --- defaults() ----------------------------------------------------------------------------------------


def test_defaults_have_no_steamid_and_no_clips_folder():
    values = defaults()
    assert values["subject_steamid"] == ""
    assert values["data_root"] == ""
    assert values["faceit_api_key_protected"] == ""
    assert values["faceit_nickname"] == ""


def test_defaults_match_configs_own_defaults():
    values = defaults()
    cfg = Config()
    assert values["top_n"] == cfg.top_n == 5
    assert values["page_port"] == cfg.page_port == 8765
    assert values["aspect_ratio"] == "16:9"
    assert values["match_alerts"] is True
    assert isinstance(values["pg_bin"], str)
    assert values["pg_bin"] == str(cfg.pg_bin)


def test_defaults_excludes_the_app_data_fields():
    values = defaults()
    assert "index_path" not in values
    assert "csdm_home" not in values
    assert "logs_dir" not in values


# --- json_values(): the Settings page's GET, without the protected key (Task 13) ---------------------


def test_json_values_matches_defaults_minus_the_protected_key():
    assert json_values(Config()) == {k: v for k, v in defaults().items() if k != STORED_KEY}
    assert STORED_KEY not in json_values(Config())


def test_json_values_reflects_the_given_configs_effective_values(tmp_path):
    cfg = Config(top_n=9, data_root=tmp_path, faceit_api_key_protected=protect.protect("a-key"))
    values = json_values(cfg)
    assert values["top_n"] == 9
    assert values["data_root"] == str(tmp_path)
    assert STORED_KEY not in values


def test_json_values_data_root_is_empty_string_when_none():
    assert json_values(Config())["data_root"] == ""


# --- load(): missing / unreadable file -------------------------------------------------------------


def test_a_missing_file_gives_plain_defaults_and_no_warnings(tmp_path):
    loaded = load(tmp_path / "settings.json")
    assert loaded == Loaded(Config(), ())
    assert loaded.config.subject_steamid == ""
    assert loaded.config.data_root is None


def test_invalid_json_falls_back_to_defaults_with_one_warning(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")

    loaded = load(path)

    assert loaded.config == Config()
    assert len(loaded.warnings) == 1
    assert "settings.json" in loaded.warnings[0]
    assert "using the defaults" in loaded.warnings[0]
    assert path.read_text(encoding="utf-8") == "{not json"    # untouched


def test_a_json_array_instead_of_an_object_falls_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    loaded = load(path)

    assert loaded.config == Config()
    assert len(loaded.warnings) == 1
    assert "using the defaults" in loaded.warnings[0]


# --- load(): known values, unknown keys, bad values --------------------------------------------------


def test_known_values_are_applied(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"top_n": 10, "subject_steamid": "76561198000000001"}), encoding="utf-8")

    loaded = load(path)

    assert loaded.config.top_n == 10
    assert loaded.config.subject_steamid == "76561198000000001"
    assert loaded.warnings == ()


def test_data_root_empty_string_becomes_none_and_a_real_path_becomes_a_path(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"data_root": ""}), encoding="utf-8")
    assert load(path).config.data_root is None

    path.write_text(json.dumps({"data_root": "D:/clips"}), encoding="utf-8")
    assert load(path).config.data_root == Path("D:/clips")


def test_unknown_keys_are_ignored_without_a_warning(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"some_future_setting": 123}), encoding="utf-8")

    loaded = load(path)

    assert loaded.warnings == ()
    assert loaded.config == Config()


def test_a_bad_hand_edited_value_falls_back_and_warns_with_its_label(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"top_n": 999}), encoding="utf-8")

    loaded = load(path)

    assert loaded.config.top_n == 5
    assert len(loaded.warnings) == 1
    assert "Highlights per match" in loaded.warnings[0]


def test_several_bad_values_each_warn_and_the_rest_still_load(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"top_n": 999, "aspect_ratio": "21:9", "page_port": 42}), encoding="utf-8")

    loaded = load(path)

    assert (loaded.config.top_n, loaded.config.aspect_ratio, loaded.config.page_port) == (5, "16:9", 8765)
    assert len(loaded.warnings) == 3


def test_folders_are_not_checked_for_existence_on_load(tmp_path):
    missing = tmp_path / "does" / "not" / "exist"
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"pg_bin": str(missing)}), encoding="utf-8")

    loaded = load(path)

    assert loaded.config.pg_bin == missing
    assert loaded.warnings == ()


def test_the_stored_protected_key_loads_as_is_and_a_corrupt_one_falls_back(tmp_path):
    blob = protect.protect("a-real-key")
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"faceit_api_key_protected": blob}), encoding="utf-8")
    assert load(path).config.faceit_api_key_protected == blob

    path.write_text(json.dumps({"faceit_api_key_protected": "not base64 at all!!"}), encoding="utf-8")
    loaded = load(path)
    assert loaded.config.faceit_api_key_protected == ""
    assert len(loaded.warnings) == 1
    assert "FACEIT API key" in loaded.warnings[0]


# --- validate(): one representative case per kind -----------------------------------------------------


@pytest.mark.parametrize(("name", "value"), [
    ("subject_steamid", "76561198192858303"),
    ("subject_steamid", ""),
    ("faceit_nickname", "someone"),
    ("aspect_ratio", "4:3-stretched"),
    ("sequence_event", "rounds"),
    ("top_n", 25),
    ("padding_before_s", 4),
    ("padding_before_s", 4.5),
    ("match_alerts", False),
    ("page_port", 8765),
])
def test_valid_values_pass(name, value):
    assert validate({name: value}, check_exists=False) == {}


@pytest.mark.parametrize(("name", "value"), [
    ("subject_steamid", "12345"),
    ("subject_steamid", 76561198192858303),
    ("faceit_nickname", " someone"),
    ("faceit_nickname", "x" * 65),
    ("aspect_ratio", "21:9"),
    ("sequence_event", "frags"),
    ("top_n", 0),
    ("top_n", 5.0),
    ("padding_before_s", -1),
    ("padding_before_s", "4"),
    ("match_alerts", 1),
    ("page_port", 80),
])
def test_invalid_values_fail(name, value):
    errors = validate({name: value}, check_exists=False)
    assert name in errors


def test_error_messages_are_short_and_say_whats_allowed():
    errors = validate({"top_n": 500}, check_exists=False)
    assert errors["top_n"] == "must be a whole number from 1 to 50"
    errors = validate({"aspect_ratio": "21:9"}, check_exists=False)
    assert errors["aspect_ratio"] == "must be one of 16:9, 4:3, 4:3-hd, 4:3-stretched"


def test_the_secret_kind_is_only_checked_when_a_value_is_given():
    assert validate({KEY_FIELD: "a-good-key"}, check_exists=False) == {}
    assert KEY_FIELD in validate({KEY_FIELD: "a bad key with spaces"}, check_exists=False)
    assert KEY_FIELD in validate({KEY_FIELD: "x" * 201}, check_exists=False)


def test_the_secret_error_never_contains_the_key():
    errors = validate({KEY_FIELD: "super-secret-value has spaces"}, check_exists=False)
    assert "super-secret-value" not in errors[KEY_FIELD]


def test_folder_existence_is_only_checked_when_asked(tmp_path):
    missing = str(tmp_path / "nope")
    assert validate({"pg_bin": missing}, check_exists=False) == {}
    assert "pg_bin" in validate({"pg_bin": missing}, check_exists=True)
    real = str(tmp_path)
    assert validate({"pg_bin": real}, check_exists=True) == {}


def test_data_root_alone_may_be_blank():
    assert validate({"data_root": ""}, check_exists=True) == {}
    assert "downloads_dir" in validate({"downloads_dir": ""}, check_exists=True)


def test_program_existence_is_only_checked_when_asked(tmp_path):
    assert validate({"ffmpeg": "some-tool-not-on-path.exe"}, check_exists=False) == {}
    assert "ffmpeg" in validate({"ffmpeg": "some-tool-not-on-path.exe"}, check_exists=True)

    real_file = tmp_path / "ffmpeg.exe"    # an existing file, not a lookup on PATH: no real program needed
    real_file.write_bytes(b"")
    assert validate({"ffmpeg": str(real_file)}, check_exists=True) == {}


def test_an_unknown_name_is_reported():
    assert validate({"nope": 1}, check_exists=False) == {"nope": "unknown setting"}


# --- save(): only changed values are written ----------------------------------------------------------


def test_only_changed_values_are_written(tmp_path):
    path = tmp_path / "settings.json"

    assert save(path, {"top_n": 10}) == {}
    assert read_raw(path) == {"top_n": 10}


def test_a_value_set_back_to_its_default_disappears_from_the_file(tmp_path):
    path = tmp_path / "settings.json"
    save(path, {"top_n": 10})

    assert save(path, {"top_n": 5}) == {}

    assert read_raw(path) == {}


def test_numbers_are_compared_by_value_not_type(tmp_path):
    path = tmp_path / "settings.json"

    assert save(path, {"padding_before_s": 4}) == {}     # default is 4.0; 4 == 4.0

    assert read_raw(path) == {}


def test_folder_values_are_compared_as_paths_not_strings(tmp_path, monkeypatch):
    # A tmp folder standing in for pg_bin's default, so this doesn't depend on a real portable Postgres
    # being installed on whatever machine runs the suite (save() only needs *a* default to compare
    # against; monkeypatching defaults() controls that without touching any other setting).
    path = tmp_path / "settings.json"
    default_pg_bin = tmp_path / "pg" / "bin"
    default_pg_bin.mkdir(parents=True)
    monkeypatch.setattr("clipper.settings.defaults", lambda: {"pg_bin": str(default_pg_bin)})

    same_path_forward_slashes = str(default_pg_bin).replace("\\", "/")
    assert same_path_forward_slashes != str(default_pg_bin)   # a genuinely different string, same path

    assert save(path, {"pg_bin": same_path_forward_slashes}) == {}

    assert read_raw(path) == {}


def test_unknown_keys_already_in_the_file_are_kept(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"a_future_setting": "x"}), encoding="utf-8")

    assert save(path, {"top_n": 10}) == {}

    assert read_raw(path) == {"a_future_setting": "x", "top_n": 10}


def test_one_message_per_bad_field_and_nothing_is_written(tmp_path):
    path = tmp_path / "settings.json"

    errors = save(path, {"top_n": 999, "aspect_ratio": "21:9"})

    assert set(errors) == {"top_n", "aspect_ratio"}
    assert not path.exists()


def test_an_unknown_setting_name_is_an_error_and_nothing_is_written(tmp_path):
    path = tmp_path / "settings.json"

    errors = save(path, {"bogus": 1})

    assert errors == {"bogus": "unknown setting"}
    assert not path.exists()


def test_folders_are_checked_for_existence_on_save(tmp_path):
    path = tmp_path / "settings.json"
    missing = str(tmp_path / "does-not-exist")

    errors = save(path, {"pg_bin": missing})

    assert "pg_bin" in errors
    assert not path.exists()


def test_save_is_atomic_and_leaves_no_tmp_file(tmp_path):
    path = tmp_path / "settings.json"

    save(path, {"top_n": 10})

    assert not path.with_name(path.name + ".tmp").exists()


# --- save(): the FACEIT key ----------------------------------------------------------------------------


def test_the_key_is_saved_protected_and_never_appears_in_the_files_text(tmp_path):
    path = tmp_path / "settings.json"

    assert save(path, {KEY_FIELD: "super-secret-faceit-api-key"}) == {}

    text = path.read_text(encoding="utf-8")
    assert "super-secret-faceit-api-key" not in text
    raw = json.loads(text)
    assert STORED_KEY in raw
    assert protect.unprotect(raw[STORED_KEY]) == "super-secret-faceit-api-key"


def test_an_empty_key_leaves_the_stored_key_unchanged(tmp_path):
    path = tmp_path / "settings.json"
    save(path, {KEY_FIELD: "the-original-key"})
    before = read_raw(path)[STORED_KEY]

    assert save(path, {KEY_FIELD: ""}) == {}

    assert read_raw(path)[STORED_KEY] == before


def test_none_removes_the_stored_key(tmp_path):
    path = tmp_path / "settings.json"
    save(path, {KEY_FIELD: "the-original-key"})

    assert save(path, {KEY_FIELD: None}) == {}

    assert STORED_KEY not in read_raw(path)


def test_a_bad_key_is_rejected_and_nothing_is_written(tmp_path):
    path = tmp_path / "settings.json"

    errors = save(path, {KEY_FIELD: "has a space"})

    assert KEY_FIELD in errors
    assert not path.exists()
    assert "has a space" not in errors[KEY_FIELD]


# --- SettingsStore -------------------------------------------------------------------------------------


def test_the_store_loads_defaults_when_the_file_is_missing(tmp_path):
    store = SettingsStore(tmp_path / "settings.json")

    assert store.current().config == Config()
    assert store.path == tmp_path / "settings.json"


def test_the_store_reloads_after_the_file_changes_on_disk(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.current()

    path.write_text(json.dumps({"top_n": 12, "note": "make this write a different size"}), encoding="utf-8")

    assert store.current().config.top_n == 12


def test_the_store_reloads_after_its_own_save(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    assert store.current().config.top_n == 5

    assert store.save({"top_n": 9}) == {}

    assert store.current().config.top_n == 9


def test_the_store_does_not_reload_when_nothing_changed(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    first = store.current()

    assert store.current() is first
