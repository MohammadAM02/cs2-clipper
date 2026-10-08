"""Windows Job Object that kills child processes when the app dies (spec: Closing and quitting).

Copied from thelifeofsuleyman/cs2-clipper's `aegis/winjob.py` (MIT), with our own logging
(`logging.getLogger(__name__)`) in place of Aegis's `log()`, `argtypes`/`restype` declared for every
kernel32 call so a 64-bit handle is never silently truncated (Aegis leaves some undeclared), and
`AssignProcessToJobObject`'s result checked instead of ignored.

Why this exists here: rendering runs HLAE, which launches a hooked CS2; analysis runs csda. On Windows
a child does NOT die when its parent is killed — so a crash, a Task Manager "End task", or a
self-update that exits abruptly would leave HLAE (and, we hope, the hooked CS2 it started) or csda
running forever.

Assigning a child to a Job Object created with KILL_ON_JOB_CLOSE means the OS terminates it the
instant our process exits for ANY reason, because closing our last handle to the job triggers the
kill. Pure ctypes — no extra dependency, and a no-op on non-Windows.

MIT License

Copyright (c) 2026 thelifeofsuleyman

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
import threading
from ctypes import wintypes

log = logging.getLogger(__name__)

_JobObjectExtendedLimitInformation = 9
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

_job: int | None = None
_tried = False
_warned_no_job = False
# Guards the check/create sequence in _get_job() and the warn-once flag below: the app is about to
# run a worker thread alongside web-server threads, any of which may call guard() first.
_lock = threading.Lock()


class _BASIC_LIMIT(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.POINTER(wintypes.ULONG)),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _EXTENDED_LIMIT(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BASIC_LIMIT),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


def _get_job() -> int | None:
    """Lazily create the kill-on-close job (kept alive for the process's lifetime: we never close
    our handle to it ourselves, since closing it is what kills the members).

    The whole check/create sequence runs under `_lock`. Without it, a second thread could see
    `_tried` already set (it is set before the job exists, since the kernel32 calls below release
    the GIL) and return `None` while the first thread is still creating the job — silently leaving
    its process unguarded. With the lock, a concurrent caller simply waits for the first creation
    and gets its result."""
    global _job, _tried
    with _lock:
        if _tried:
            return _job
        _tried = True
        if sys.platform != "win32":
            return None
        try:
            k32 = ctypes.WinDLL("kernel32", use_last_error=True)
            k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
            k32.CreateJobObjectW.restype = wintypes.HANDLE
            job = k32.CreateJobObjectW(None, None)
            if not job:
                return None
            info = _EXTENDED_LIMIT()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            k32.SetInformationJobObject.argtypes = [
                wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
            ]
            k32.SetInformationJobObject.restype = wintypes.BOOL
            ok = k32.SetInformationJobObject(
                job, _JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info))
            if not ok:
                return None
            _job = job
            return _job
        except Exception as e:
            log.warning("Job object unavailable (%s); HLAE and csda won't be auto-killed on crash", e)
            return None


def _warn_no_job_once() -> None:
    global _warned_no_job
    with _lock:
        already_warned, _warned_no_job = _warned_no_job, True
    if not already_warned:
        log.warning("No job object available; HLAE and csda will not be auto-killed on crash")


def guard(proc: subprocess.Popen) -> None:
    """Tie a started process to the app's lifetime (kill it when we exit). Best effort: if the job
    cannot be created or the process cannot be assigned (e.g. it already exited), log a warning and
    carry on. No-op off Windows. When no job is available at all, that warning is logged once per
    process rather than on every call, so a busy worker thread does not spam the log."""
    if sys.platform != "win32":
        return
    job = _get_job()
    if not job:
        _warn_no_job_once()
        return
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k32.AssignProcessToJobObject.restype = wintypes.BOOL
        if not k32.AssignProcessToJobObject(job, int(proc._handle)):
            raise ctypes.WinError(ctypes.get_last_error())
    except Exception as e:
        log.warning("Could not guard child process: %s", e)
