"""Tests for packaging/cs2clipper.iss, the installer's script, and the PowerShell scripts beside it.
Nothing is compiled, installed or run: the scripts are read, and what the installer asks of the exe is
asked of the command line the exe has."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from clipper import cli

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


@pytest.mark.parametrize("name", ["cs2clipper.iss", "build.ps1"])
def test_a_script_a_windows_tool_reads_is_plain_ascii(name):
    # Inno Setup and Windows PowerShell read a file that has no byte order mark in the PC's own code page.
    (PACKAGING / name).read_bytes().decode("ascii")
