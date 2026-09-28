"""CsdmCli.analyze after the switch from subprocess.run to Popen + winjob.guard (spec: Closing and
quitting, Code units): unchanged output/log/return behaviour, guarded like every csdm call, and a
timeout still kills the process, reaps it, and raises subprocess.TimeoutExpired.

Uses tests/fake_csdm.py like test_render.py does; never launches csdm for real.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from clipper import csdm_cli
from clipper.csdm_cli import CsdmCli

FAKE_CSDM = Path(__file__).with_name("fake_csdm.py")


class _GuardSpy:
    def __init__(self):
        self.guarded = []

    def guard(self, proc):
        self.guarded.append(proc)


@pytest.fixture
def cli(tmp_path):
    return CsdmCli(prefix=(sys.executable, str(FAKE_CSDM)), home=tmp_path / "home", pg_bin=tmp_path / "pgbin")


def _find_by_cmdline_fragment(fragment: str):
    for process in psutil.process_iter(["cmdline"]):
        try:
            if any(fragment in part for part in (process.info["cmdline"] or [])):
                return process
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return None


def test_analyze_guards_the_process(cli, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CSDM_MODE", "auth")
    spy = _GuardSpy()
    monkeypatch.setattr(csdm_cli, "winjob", spy)
    cli.analyze(tmp_path / "match.dem", tmp_path / "logs" / "analyze.log")
    assert len(spy.guarded) == 1


def test_analyze_still_joins_output_writes_the_log_and_returns_it(cli, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CSDM_MODE", "auth")
    log_path = tmp_path / "logs" / "analyze.log"
    output = cli.analyze(tmp_path / "match.dem", log_path)
    assert 'password authentication failed for user "postgres"' in output
    assert log_path.read_text(encoding="utf-8") == output


def test_a_timeout_kills_and_reaps_the_process_then_raises(cli, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_CSDM_MODE", "hang")
    monkeypatch.setenv("FAKE_CSDM_STOPFILE", str(tmp_path / "never-touched"))
    monkeypatch.setattr(csdm_cli, "ANALYZE_TIMEOUT_SECONDS", 0.4)
    dem = tmp_path / "match-timeout-marker.dem"   # unique marker: identifies the child in psutil
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            cli.analyze(dem, tmp_path / "logs" / "analyze.log")
        deadline = time.monotonic() + 5.0
        leftover = _find_by_cmdline_fragment(str(dem))
        while leftover is not None and time.monotonic() < deadline:
            time.sleep(0.1)
            leftover = _find_by_cmdline_fragment(str(dem))
        assert leftover is None, "the timed-out csdm process was not killed and reaped"
    finally:
        leftover = _find_by_cmdline_fragment(str(dem))
        if leftover is not None:
            leftover.kill()
