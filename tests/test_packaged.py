"""Tests for clipper.packaged: what only the exe does. The DLL search path tests set this process's real
one the way the exe's bootloader does, and put it back afterwards."""
from __future__ import annotations

import ctypes
import sys

import pytest

from clipper import packaged

kernel32 = ctypes.windll.kernel32


def _dll_directory() -> str:
    buffer = ctypes.create_unicode_buffer(1024)
    kernel32.GetDllDirectoryW(len(buffer), buffer)
    return buffer.value


@pytest.fixture
def bundle_on_dll_path(tmp_path):
    """What the exe's bootloader does before any of our code runs: its unpacked bundle goes on the DLL
    search path, which every process the app starts inherits."""
    before = _dll_directory()
    kernel32.SetDllDirectoryW(str(tmp_path))
    yield str(tmp_path)
    kernel32.SetDllDirectoryW(before or None)


def test_the_exe_takes_its_bundle_off_the_dll_search_path(bundle_on_dll_path, monkeypatch):
    # Else csda, HLAE, CS2 and ffmpeg would load the bundle's DLLs before their own.
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    packaged.release_dll_directory()

    assert _dll_directory() == ""


def test_running_from_source_leaves_the_dll_search_path_alone(bundle_on_dll_path):
    packaged.release_dll_directory()

    assert _dll_directory() == bundle_on_dll_path


# --- the exe's other processes -----------------------------------------------------------------------------


class FakeProcess:
    def __init__(self, pid: int, exe: str | None):
        self.pid, self.info = pid, {"exe": exe}


def test_the_exes_other_copies_are_the_other_processes_running_its_file(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Apps\CS2Clipper\CS2Clipper.exe")
    monkeypatch.setattr(packaged.os, "getpid", lambda: 11)
    monkeypatch.setattr(packaged.os, "getppid", lambda: 10)       # a onefile exe: its outer process is its parent
    running = [
        FakeProcess(10, r"C:\Apps\CS2Clipper\CS2Clipper.exe"),
        FakeProcess(11, r"C:\Apps\CS2Clipper\CS2Clipper.exe"),
        FakeProcess(20, r"c:\apps\cs2clipper\CS2CLIPPER.EXE"),      # Windows tells the same file in any case
        FakeProcess(30, r"C:\Elsewhere\CS2Clipper.exe"),
        FakeProcess(40, None),                                      # one that may not be asked
    ]
    monkeypatch.setattr(packaged.psutil, "process_iter", lambda attrs: iter(running))

    assert [process.pid for process in packaged.other_copies()] == [20]


def test_python_running_the_source_has_no_other_copies(monkeypatch):
    monkeypatch.setattr(packaged.psutil, "process_iter", lambda attrs: pytest.fail("looked at the PC's processes"))

    assert packaged.other_copies() == []


# --- the bundle check --------------------------------------------------------------------------------------


def _boom() -> None:
    raise RuntimeError("boom")


def test_the_report_has_a_line_per_check_and_all_passing_gives_0(tmp_path):
    report = tmp_path / "report.txt"

    code = packaged.check(report, [("first", lambda: None), ("second", lambda: None)])

    assert code == 0
    assert report.read_text(encoding="utf-8").splitlines() == ["ok first", "ok second"]


def test_a_failing_check_is_reported_with_its_error_and_the_rest_still_run(tmp_path):
    report = tmp_path / "report.txt"

    code = packaged.check(report, [("first", _boom), ("second", lambda: None)])

    assert code == 1
    assert report.read_text(encoding="utf-8").splitlines() == ["FAIL first: RuntimeError: boom", "ok second"]


def test_the_main_process_checks_pass_from_source(tmp_path):
    # Everything is there when running from source, so a failure in the exe is a gap in the bundle, not a
    # wrong check. (The window's checks load .NET, so they only run in the build.)
    report = tmp_path / "report.txt"

    code = packaged.check(report, packaged.MAIN_CHECKS)

    assert code == 0, report.read_text(encoding="utf-8")
