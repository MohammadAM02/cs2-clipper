"""Tests for clipper.cs2_paths: where CS2 lives. Every Steam library, CS:DM settings file and registry here is
a fake under tmp_path (or a stand-in winreg): nothing reads the real registry, Steam or CS:DM."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from clipper import cs2_paths, csdm_settings

CS2 = Path(r"D:\SteamLibrary\steamapps\common\Counter-Strike Global Offensive\game\bin\win64\cs2.exe")
INSTALL = Path("steamapps") / "common" / "Counter-Strike Global Offensive"

# Steam's own file: backslashes doubled, one section per library, `apps` holding the installed games.
LIBRARY_FOLDERS = r'''"libraryfolders"
{
    "0"
    {
        "path"      "C:\\Program Files (x86)\\Steam"
        "label"     ""
        "apps"
        {
            "228980"        "123"
        }
    }
    "1"
    {
        "path"      "D:\\SteamLibrary"
        "label"     "E:\\not a library"
        "apps"
        {
            "730"       "456"
        }
    }
}
'''


def make_cs2(library: Path) -> Path:
    """A cs2.exe where Steam puts it in `library`; the file is empty, nothing runs it."""
    exe = library / INSTALL / "game" / "bin" / "win64" / "cs2.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    return exe


def write_library_folders(steam: Path, *libraries: Path) -> None:
    sections = "".join(
        f'    "{index}"\n    {{\n        "path"      "{str(library).replace(chr(92), chr(92) * 2)}"\n    }}\n'
        for index, library in enumerate(libraries)
    )
    vdf = steam / "steamapps" / "libraryfolders.vdf"
    vdf.parent.mkdir(parents=True, exist_ok=True)
    vdf.write_text('"libraryfolders"\n{\n' + sections + "}\n", encoding="utf-8")


def write_playback(home: Path, playback: dict) -> None:
    settings = csdm_settings.settings_file(home)
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(json.dumps({"schemaVersion": 13, "playback": playback}), encoding="utf-8")


class FakeKey:
    def __init__(self, values: dict):
        self.values = values

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeWinreg:
    """Stands in for the winreg module: the keys it was given, no registry."""

    HKEY_CURRENT_USER = "HKCU"

    def __init__(self, keys: dict):
        self.keys = keys        # {(hive, key path): {value name: data}}

    def OpenKey(self, hive, path):
        if (hive, path) not in self.keys:
            raise FileNotFoundError(path)
        return FakeKey(self.keys[(hive, path)])

    def QueryValueEx(self, key, name):
        if name not in key.values:
            raise FileNotFoundError(name)
        return key.values[name], 1


def use_registry(monkeypatch, keys: dict) -> None:
    monkeypatch.setitem(sys.modules, "winreg", FakeWinreg(keys))


def steam_must_not_be_read():
    raise AssertionError("Steam was looked up")


def test_the_csgo_folder_is_game_csgo_three_folders_up_from_the_executable():
    assert cs2_paths.csgo_dir(CS2) == Path(
        r"D:\SteamLibrary\steamapps\common\Counter-Strike Global Offensive\game\csgo")


def test_cfg_files_and_the_console_log_live_in_the_csgo_folder():
    csgo = cs2_paths.csgo_dir(CS2)

    assert cs2_paths.cfg_dir(CS2) == csgo / "cfg"
    assert cs2_paths.console_log(CS2) == csgo / "console.log"


def test_every_path_entry_of_libraryfolders_vdf_is_a_library():
    assert cs2_paths.parse_library_folders(LIBRARY_FOLDERS) == [
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"D:\SteamLibrary"),
    ]


def test_the_doubled_backslashes_of_the_vdf_are_undoubled_so_a_network_share_stays_a_unc_path():
    text = r'"libraryfolders" { "0" { "path" "\\\\nas\\games\\Steam" } }'

    [library] = cs2_paths.parse_library_folders(text)

    assert library.drive == r"\\nas\games"      # pathlib folds doubled backslashes on a drive, not on a share


@pytest.mark.parametrize("text", [
    "",
    "not a vdf at all",
    '"libraryfolders"\n{\n}\n',
    '"libraryfolders" { "0" { "path" ""',
])
def test_a_vdf_without_a_path_names_no_library(text):
    assert cs2_paths.parse_library_folders(text) == []


def test_the_steam_folder_from_the_registry_is_written_the_way_windows_does():
    steam = cs2_paths.steam_folder(read=lambda: "c:/program files (x86)/steam")

    assert str(steam) == r"C:\program files (x86)\steam"       # a Path would equal the lower-case spelling too


@pytest.mark.parametrize("value", [None, ""])
def test_a_missing_steampath_is_no_steam_folder(value):
    assert cs2_paths.steam_folder(read=lambda: value) is None


def test_steampath_is_read_from_the_current_users_steam_key(monkeypatch):
    use_registry(monkeypatch, {("HKCU", r"Software\Valve\Steam"): {"SteamPath": "c:/program files (x86)/steam"}})

    assert cs2_paths.registry_steam_path() == "c:/program files (x86)/steam"


@pytest.mark.parametrize("keys", [
    {},
    {("HKCU", r"Software\Valve\Steam"): {}},
    {("HKCU", r"Software\Valve\Steam"): {"SteamPath": 5}},
    {("HKCU", r"Software\Valve\Steam"): {"SteamPath": ""}},
])
def test_no_steampath_in_the_registry_is_none(monkeypatch, keys):
    use_registry(monkeypatch, keys)

    assert cs2_paths.registry_steam_path() is None


def test_a_custom_cs2_location_wins_over_steam(tmp_path):
    home, steam = tmp_path / "csdm", tmp_path / "Steam"
    make_cs2(steam)
    custom = tmp_path / "mine" / "cs2.exe"
    custom.parent.mkdir()
    custom.write_bytes(b"")
    write_playback(home, {"customCs2LocationEnabled": True, "cs2ExecutablePath": str(custom)})

    assert cs2_paths.find_cs2_exe(home, steam_folder=lambda: steam) == custom


def test_a_custom_cs2_location_is_not_used_unless_it_is_enabled(tmp_path):
    home, steam = tmp_path / "csdm", tmp_path / "Steam"
    exe = make_cs2(steam)
    custom = tmp_path / "mine" / "cs2.exe"
    custom.parent.mkdir()
    custom.write_bytes(b"")
    write_playback(home, {"customCs2LocationEnabled": False, "cs2ExecutablePath": str(custom)})

    assert cs2_paths.find_cs2_exe(home, steam_folder=lambda: steam) == exe


@pytest.mark.parametrize("playback", [
    {"customCs2LocationEnabled": True, "cs2ExecutablePath": "no such folder\\cs2.exe"},
    {"customCs2LocationEnabled": True, "cs2ExecutablePath": ""},
    {"customCs2LocationEnabled": True, "cs2ExecutablePath": 5},
    {"customCs2LocationEnabled": True},
])
def test_a_custom_cs2_location_that_is_no_file_leaves_it_to_steam(tmp_path, playback):
    home, steam = tmp_path / "csdm", tmp_path / "Steam"
    exe = make_cs2(steam)
    write_playback(home, playback)

    assert cs2_paths.find_cs2_exe(home, steam_folder=lambda: steam) == exe


@pytest.mark.parametrize("text", ["{not json", "[]", '{"schemaVersion": 13, "playback": []}'])
def test_a_settings_file_that_cannot_be_used_leaves_it_to_steam(tmp_path, text):
    home, steam = tmp_path / "csdm", tmp_path / "Steam"
    exe = make_cs2(steam)
    csdm_settings.settings_file(home).parent.mkdir(parents=True)
    csdm_settings.settings_file(home).write_text(text, encoding="utf-8")

    assert cs2_paths.find_cs2_exe(home, steam_folder=lambda: steam) == exe


def test_cs2_is_found_in_the_steam_folder_when_there_is_no_libraryfolders_vdf(tmp_path):
    steam = tmp_path / "Steam"
    exe = make_cs2(steam)

    assert cs2_paths.find_cs2_exe(tmp_path / "csdm", steam_folder=lambda: steam) == exe


def test_cs2_is_found_in_the_steam_folder_when_the_vdf_does_not_list_it(tmp_path):
    steam, elsewhere = tmp_path / "Steam", tmp_path / "Elsewhere"
    exe = make_cs2(steam)
    write_library_folders(steam, elsewhere)

    assert cs2_paths.find_cs2_exe(tmp_path / "csdm", steam_folder=lambda: steam) == exe


def test_cs2_is_found_in_another_library_that_libraryfolders_vdf_lists(tmp_path):
    steam, games = tmp_path / "Steam", tmp_path / "Games" / "SteamLibrary"
    write_library_folders(steam, steam, games)
    exe = make_cs2(games)

    assert cs2_paths.find_cs2_exe(tmp_path / "csdm", steam_folder=lambda: steam) == exe


def test_the_first_library_that_has_cs2_wins_and_the_steam_folder_comes_after_the_vdf(tmp_path):
    steam, first, second = tmp_path / "Steam", tmp_path / "First", tmp_path / "Second"
    write_library_folders(steam, first, second)
    make_cs2(steam)
    make_cs2(second)
    first_exe = make_cs2(first)

    assert cs2_paths.find_cs2_exe(tmp_path / "csdm", steam_folder=lambda: steam) == first_exe


def test_a_library_that_has_no_cs2_is_skipped(tmp_path):
    steam, empty, games = tmp_path / "Steam", tmp_path / "Empty", tmp_path / "Games"
    write_library_folders(steam, steam, tmp_path / "Gone", empty, games)
    empty.mkdir()
    exe = make_cs2(games)

    assert cs2_paths.find_cs2_exe(tmp_path / "csdm", steam_folder=lambda: steam) == exe


def test_no_cs2_in_any_library_is_none(tmp_path):
    steam, games = tmp_path / "Steam", tmp_path / "Games"
    write_library_folders(steam, steam, games)
    games.mkdir()

    assert cs2_paths.find_cs2_exe(tmp_path / "csdm", steam_folder=lambda: steam) is None


def test_no_steam_folder_and_no_custom_location_is_none(tmp_path):
    assert cs2_paths.find_cs2_exe(tmp_path / "csdm", steam_folder=lambda: None) is None


def test_steam_is_not_looked_up_for_a_custom_cs2_location(tmp_path):
    home = tmp_path / "csdm"
    custom = tmp_path / "cs2.exe"
    custom.write_bytes(b"")
    write_playback(home, {"customCs2LocationEnabled": True, "cs2ExecutablePath": str(custom)})

    assert cs2_paths.find_cs2_exe(home, steam_folder=steam_must_not_be_read) == custom


def test_the_default_steam_folder_comes_from_the_registry(tmp_path, monkeypatch):
    steam = tmp_path / "Steam"
    exe = make_cs2(steam)
    registry_path = str(steam).replace("\\", "/")
    use_registry(monkeypatch, {
        ("HKCU", r"Software\Valve\Steam"): {"SteamPath": registry_path[0].lower() + registry_path[1:]},
    })

    assert cs2_paths.find_cs2_exe(tmp_path / "csdm") == exe
