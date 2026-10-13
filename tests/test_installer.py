"""Tests for packaging/cs2clipper.iss, the installer's script, and the PowerShell scripts beside it.
Nothing is compiled, installed or run: the scripts are read, and what the installer asks of the exe is
asked of the command line the exe has."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from clipper import cli, components, links, notify, update

PACKAGING = Path(__file__).resolve().parent.parent / "packaging"
SCRIPT = (PACKAGING / "cs2clipper.iss").read_text(encoding="utf-8")


def test_windows_knows_the_app_by_the_id_it_has_always_had():
    # An installer with another AppId would install a second copy beside the first, not upgrade it.
    assert re.findall(r"^AppId=(.+)$", SCRIPT, re.M) == ["{{03EF2ADC-14C4-4DA0-8A95-52187AA0BF34}"]


def test_the_installer_asks_the_exe_only_for_what_its_command_line_has(monkeypatch):
    asked = []
    monkeypatch.setattr("clipper.cli.app.quit_running", lambda: asked.append("quit") or 0)
    monkeypatch.setattr("clipper.cli.app.run", lambda **kwargs: asked.append(kwargs) or 0)
    [before_replacing_it] = re.findall(r"Exec\(Exe, '([^']*)'", SCRIPT)
    [at_sign_in] = re.findall(r'^Name: "\{userstartup\}.*Parameters: "([^"]*)"', SCRIPT, re.M)

    assert cli.main(before_replacing_it.split()) == 0       # a command line the exe lacks ends in SystemExit
    assert cli.main(at_sign_in.split()) == 0

    assert asked == ["quit", {"open_page": None, "background": True, "headless": False}]


def test_the_apps_own_update_makes_the_installer_start_the_app_again():
    # The update runs the installer silently with /RELAUNCH (clipper.update); only such an install starts the app.
    assert {"/VERYSILENT", "/RELAUNCH"} <= set(update.INSTALLER_FLAGS)
    [entry] = [line for line in SCRIPT.splitlines() if "Check: RelaunchAsked" in line]
    assert entry == r'Filename: "{app}\{#AppExe}"; Flags: nowait skipifnotsilent; Check: RelaunchAsked'
    assert "if CompareText(ParamStr(I), '/RELAUNCH') = 0 then" in SCRIPT


@pytest.mark.parametrize("name", ["cs2clipper.iss", "build.ps1", "prove.ps1"])
def test_a_script_a_windows_tool_reads_is_plain_ascii(name):
    # Inno Setup and Windows PowerShell read a file that has no byte order mark in the PC's own code page.
    (PACKAGING / name).read_bytes().decode("ascii")


def test_the_start_menu_shortcut_carries_the_id_the_apps_toasts_go_by():
    assert re.findall(r'^#define AppUserModelID "([^"]+)"$', SCRIPT, re.M) == [notify.APP_ID]
    [start_menu] = [line for line in SCRIPT.splitlines() if line.startswith('Name: "{autoprograms}')]
    assert 'AppUserModelID: "{#AppUserModelID}"' in start_menu


def test_uninstalling_takes_away_what_the_app_registers_for_itself():
    keys = re.findall(r'^Root: HKCU; Subkey: "([^"]+)"; Flags: uninsdeletekey$', SCRIPT, re.M)
    assert keys == [rf"Software\Classes\{links.SCHEME}", r"Software\Classes\AppUserModelId\{#AppUserModelID}"]


def test_the_installer_says_how_much_the_first_start_downloads():
    sizes = re.findall(r"about (\d+) MB", SCRIPT.split("[Messages]", 1)[1].split("[Icons]", 1)[0])
    pinned = (components.CSDA.size + components.FFMPEG.size) / 1e6      # HLAE comes on top, a few MB
    assert sizes and all(pinned < int(size) < pinned + 15 for size in sizes)
