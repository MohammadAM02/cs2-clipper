from datetime import datetime, timezone

import pytest

from clipper.cli import AlreadyRunning, cmd_retry, cmd_run, format_status, single_instance, start_match_alerts
from clipper.config import Config
from clipper.gate import GateStatus
from clipper.index import Index


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
    index.set_flag("paused", "1")
    lines = format_status(index, GateStatus(ok=False, reasons=("CS2 is running", "FACEIT AC is running"))).splitlines()
    assert lines[0] == "Gate: waiting: CS2 is running; FACEIT AC is running"
    assert lines[1].startswith("Rendering: PAUSED")
    assert lines[2] == f"#{demo_id:<3} failed    1-a.dem.zst  - unpacked: disk full"


def test_retry_sends_a_failed_demo_back(index, tmp_path):
    demo_id = index.add_demo("1-a.dem.zst", "a" * 64, tmp_path / "1-a.dem.zst")
    index.advance(demo_id, "unpacked")
    index.fail(demo_id, "unpacked: boom")
    assert cmd_retry(index, str(demo_id)) == 0
    assert index.demo(demo_id)["state"] == "unpacked"
    assert cmd_retry(index, "nope") == 1


def test_only_one_instance_can_hold_the_lock(tmp_path):
    lock = tmp_path / "clipper.lock"
    with single_instance(lock):
        with pytest.raises(AlreadyRunning):
            with single_instance(lock):
                pass
    with single_instance(lock):   # released again
        pass


def test_a_failed_start_is_reported_and_stops(tmp_path, monkeypatch):
    # Build a Config with folders under tmp_path
    cfg = Config(downloads_dir=tmp_path / "downloads", data_root=tmp_path / "clips",
                 index_path=tmp_path / "clipper.sqlite")

    # Monkeypatch _setup_logging to a no-op
    monkeypatch.setattr("clipper.cli._setup_logging", lambda logs_dir: None)

    # Monkeypatch postgres.ensure_running to raise RuntimeError
    monkeypatch.setattr("clipper.cli.postgres.ensure_running",
                       lambda pg_bin, pg_data: (_ for _ in ()).throw(RuntimeError("pg_ctl start failed")))

    # Monkeypatch notify to record (title, body)
    notifications = []
    monkeypatch.setattr("clipper.cli.notify", lambda title, body: notifications.append((title, body)))

    # cmd_run should return 1
    assert cmd_run(cfg) == 1

    # Exactly one notification should be recorded
    assert len(notifications) == 1
    title, body = notifications[0]
    assert title == "clipper could not start"
    assert "pg_ctl start failed" in body


def test_status_shows_match_alerts_and_the_page(index):
    index.set_flag("alerts_status", "on")
    index.set_flag("page_port", "8765")
    index.save_faceit_match("1-00000000-0000-0000-0000-000000000001",
                            datetime(2026, 9, 25, tzinfo=timezone.utc), "ready")
    lines = format_status(index, GateStatus(ok=True), host="gaming-pc").splitlines()
    assert lines[1] == "Match alerts: on · 1 to grab · http://gaming-pc:8765/demos"


def test_status_says_why_match_alerts_are_off(index):
    index.set_flag("alerts_status", "off: set FACEIT_API_KEY and FACEIT_NICKNAME in .env")
    lines = format_status(index, GateStatus(ok=True)).splitlines()
    assert lines[1] == "Match alerts: off: set FACEIT_API_KEY and FACEIT_NICKNAME in .env"


def test_match_alerts_stay_off_without_the_setting_or_the_key(index, tmp_path, monkeypatch):
    folders = {"downloads_dir": tmp_path, "data_root": tmp_path / "clips", "index_path": tmp_path / "clipper.sqlite"}
    assert start_match_alerts(Config(**folders, match_alerts=False), index, probe=None, gate=None) is None
    assert index.get_flag("alerts_status") == "off: match_alerts = false in clipper.toml"
    monkeypatch.setattr("clipper.cli.load_env", lambda path: {})
    assert start_match_alerts(Config(**folders), index, probe=None, gate=None) is None
    assert index.get_flag("alerts_status") == "off: set FACEIT_API_KEY and FACEIT_NICKNAME in .env"
