"""clipper.winjob's real behaviour, no mocks (spec: Closing and quitting, Code units).

A job object only proves itself when the process that holds it actually exits, so the guarded case
runs a helper (tests/winjob_child.py) as a subprocess: it starts a harmless grandchild
(`sys.executable -c "import time; time.sleep(60)"`), calls guard() on it, writes its PID, and exits
without stopping it. The test then watches from outside whether the grandchild is still there.

Never launches CS2, CS Demo Manager, csdm or HLAE — every process started here is `sys.executable`
itself, and every test cleans up whatever it started, whatever happens.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import psutil
import pytest

from clipper import winjob

HELPER = Path(__file__).with_name("winjob_child.py")
HELPER_TIMEOUT_SECONDS = 15
GONE_WITHIN_SECONDS = 5.0


def _run_helper(pidfile: Path, mode: str) -> int:
    result = subprocess.run(
        [sys.executable, str(HELPER), str(pidfile), mode],
        timeout=HELPER_TIMEOUT_SECONDS, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0
    return int(pidfile.read_text(encoding="utf-8").strip())


def _wait_until_gone(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while psutil.pid_exists(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


def _kill_if_running(pid: int) -> None:
    try:
        psutil.Process(pid).kill()
    except psutil.NoSuchProcess:
        pass


def test_guard_kills_the_grandchild_when_the_guarding_process_exits(tmp_path):
    pid = _run_helper(tmp_path / "pid.txt", "guard")
    try:
        assert _wait_until_gone(pid, GONE_WITHIN_SECONDS), "grandchild outlived the guarding process"
    finally:
        _kill_if_running(pid)


def test_control_without_guard_the_grandchild_survives(tmp_path):
    """Proves the test above can fail: with no guard, the grandchild is not tied to the helper."""
    pid = _run_helper(tmp_path / "pid.txt", "noguard")
    try:
        assert not _wait_until_gone(pid, 2.0), "grandchild died without being guarded"
    finally:
        _kill_if_running(pid)


def test_guard_on_an_already_exited_process_does_not_raise():
    proc = subprocess.Popen([sys.executable, "-c", "pass"], creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        proc.wait(timeout=5)
        winjob.guard(proc)  # must not raise
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
