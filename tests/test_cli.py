from datetime import datetime, timezone

import pytest

from clipper.cli import cmd_resume, cmd_retry, format_status, main
from clipper.config import Config
from clipper.gate import GateStatus
from clipper.index import Index
from clipper.settings import Loaded


@pytest.fixture
def index(tmp_path):
    idx = Index(tmp_path / "clipper.sqlite")
    yield idx
    idx.close()


def test_status_of_an_empty_index(index):
    text = format_status(index, GateStatus(ok=True))
    assert text.splitlines() == ["Gate: clear", "No Demos yet. Download one from a FACEIT matchroom."]


def test_status_shows_why_the_gate_waits_and_why_a_demo_failed(index, tmp_path):
    demo_id = index.add_demo("1-a.dem.zst", "a" * 64, tmp_path / "1-a.dem.zst")
    index.fail(demo_id, "unpacked: disk full")
    index.pause("failures")
    lines = format_status(index, GateStatus(ok=False, reasons=("CS2 is running", "FACEIT AC is running"))).splitlines()
    assert lines[0] == "Gate: waiting: CS2 is running; FACEIT AC is running"
    assert lines[1] == ("Rendering: PAUSED after repeated failures. Check HLAE/CS2, then resume from the tray, "
                        "the Status page or: clipper resume")
    assert lines[2] == f"#{demo_id:<3} failed    1-a.dem.zst  - unpacked: disk full"


def test_status_says_who_paused_when_you_did(index):
    index.pause("you")
    lines = format_status(index, GateStatus(ok=True)).splitlines()
    assert lines[1] == "Rendering: PAUSED by you. Resume from the tray, the Status page or: clipper resume"


def test_status_falls_back_to_after_failures_for_an_index_paused_by_an_older_version(index):
    index.set_flag("paused", "1")   # no paused_by: an older version paused it
    lines = format_status(index, GateStatus(ok=True)).splitlines()
    assert lines[1] == ("Rendering: PAUSED after repeated failures. Check HLAE/CS2, then resume from the tray, "
                        "the Status page or: clipper resume")


def test_resume_clears_the_pause_and_prints(index, capsys):
    index.pause("failures")
    assert cmd_resume(index) == 0
    assert index.paused_by() is None
    assert "resumed" in capsys.readouterr().out


def test_retry_sends_a_failed_demo_back(index, tmp_path):
    demo_id = index.add_demo("1-a.dem.zst", "a" * 64, tmp_path / "1-a.dem.zst")
    index.advance(demo_id, "unpacked")
    index.fail(demo_id, "unpacked: boom")
    assert cmd_retry(index, str(demo_id)) == 0
    assert index.demo(demo_id)["state"] == "unpacked"
    assert cmd_retry(index, "nope") == 1


def test_status_shows_match_alerts_and_the_page(index):
    index.set_flag("alerts_status", "on")
    index.set_flag("page_port", "8765")
    index.save_faceit_match("1-00000000-0000-0000-0000-000000000001",
                            datetime(2026, 9, 25, tzinfo=timezone.utc), "ready")
    lines = format_status(index, GateStatus(ok=True), host="gaming-pc").splitlines()
    assert lines[1] == "Match alerts: on · 1 to grab · http://gaming-pc:8765/demos"


def test_status_says_why_match_alerts_are_off(index):
    index.set_flag("alerts_status", "off: set your FACEIT nickname and API key in Settings")
    lines = format_status(index, GateStatus(ok=True)).splitlines()
    assert lines[1] == "Match alerts: off: set your FACEIT nickname and API key in Settings"


def test_status_says_the_clips_folder_is_not_set_instead_of_crashing(monkeypatch, capsys):
    monkeypatch.setattr("clipper.settings.load", lambda path: Loaded(Config(), ()))

    assert main(["status"]) == 0

    out = capsys.readouterr().out
    assert "Gate: waiting: the clips folder is not set" in out


# --- run delegates to app.run_headless ------------------------------------------------------------


def test_no_subcommand_runs_the_app(monkeypatch):
    calls = []
    monkeypatch.setattr("clipper.cli.app.run_headless", lambda: calls.append(True) or 0)
    assert main([]) == 0
    assert calls == [True]


def test_run_headless_runs_the_app(monkeypatch):
    calls = []
    monkeypatch.setattr("clipper.cli.app.run_headless", lambda: calls.append(True) or 0)
    assert main(["run", "--headless"]) == 0
    assert calls == [True]


def test_install_is_no_longer_a_command():
    with pytest.raises(SystemExit):
        main(["install"])
