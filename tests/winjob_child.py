"""Helper for tests/test_winjob.py: starts a grandchild, optionally guards it with
clipper.winjob, writes the grandchild's PID to a file, then exits without stopping it.
Whether the grandchild survives that exit is what the test observes from outside.

Usage: winjob_child.py <pidfile> guard|noguard
"""

import subprocess
import sys
from pathlib import Path

from clipper import winjob


def main() -> int:
    pidfile, mode = Path(sys.argv[1]), sys.argv[2]
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if mode == "guard":
        winjob.guard(child)
    pidfile.write_text(str(child.pid), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
