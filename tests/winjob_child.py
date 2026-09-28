"""Helper for tests/test_winjob.py: starts a grandchild, optionally guards it with
clipper.winjob, writes the grandchild's PID to a file, then exits without stopping it.
Whether the grandchild survives that exit is what the test observes from outside.

Usage:
  winjob_child.py <pidfile> guard|noguard
  winjob_child.py <pidfile> many <thread count>   # that many threads call guard() at once, racing
                                                   # the module's lazy job creation; one PID per line
"""

import subprocess
import sys
import threading
from pathlib import Path

from clipper import winjob


def _start_sleeper() -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def _guard_from_many_threads(count: int) -> list[int]:
    children = [_start_sleeper() for _ in range(count)]
    barrier = threading.Barrier(count)

    def worker(proc: subprocess.Popen) -> None:
        barrier.wait()   # line every thread up so guard() is first called from all of them at once
        winjob.guard(proc)

    threads = [threading.Thread(target=worker, args=(child,)) for child in children]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return [child.pid for child in children]


def main() -> int:
    pidfile, mode = Path(sys.argv[1]), sys.argv[2]
    if mode == "many":
        pids = _guard_from_many_threads(int(sys.argv[3]))
        pidfile.write_text("\n".join(str(pid) for pid in pids), encoding="utf-8")
        return 0
    child = _start_sleeper()
    if mode == "guard":
        winjob.guard(child)
    pidfile.write_text(str(child.pid), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
