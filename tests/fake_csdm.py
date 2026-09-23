"""Stands in for CS:DM's CLI in tests (see tests/test_render.py). FAKE_CSDM_MODE picks what it does:

  ok       writes one Clip per span in FAKE_CSDM_TICKS ("100-356,1000-1256") and reports success
  noclips  reports success but writes nothing
  auth     reports a database authentication failure (and, like csdm, still exits 0)
  hang     'records' until FAKE_CSDM_STOPFILE exists — the game died — then reports 'Game error'
  argv     writes its arguments and chosen environment variables to FAKE_CSDM_ARGV as JSON
"""

import json
import os
import sys
import time
from pathlib import Path


def option(name: str) -> str:
    return sys.argv[sys.argv.index(name) + 1]


def main() -> int:
    mode = os.environ.get("FAKE_CSDM_MODE", "ok")
    if mode == "argv":
        seen = {key: os.environ.get(key) for key in ("USERPROFILE", "ELECTRON_RUN_AS_NODE", "PGPASSWORD", "PATH")}
        Path(os.environ["FAKE_CSDM_ARGV"]).write_text(json.dumps({"argv": sys.argv[1:], **seen}), encoding="utf-8")
        return 0
    if mode == "auth":
        print('password authentication failed for user "postgres"', flush=True)
        return 0
    print("Starting Counter-Strike...", flush=True)
    print("Recording in progress...", flush=True)
    if mode == "hang":
        stopfile = Path(os.environ["FAKE_CSDM_STOPFILE"])
        while not stopfile.exists():
            time.sleep(0.05)
        print("Game error", flush=True)
        return 0
    output = Path(option("--output"))
    if mode == "ok":
        spans = [span.split("-") for span in os.environ["FAKE_CSDM_TICKS"].split(",")]
        for number, (start, end) in enumerate(spans, start=1):
            (output / f"sequence-{number}-tick-{start}-to-{end}.mp4").write_bytes(b"fake video")
            print(f"Converting sequence #{number} ({number}/{len(spans)})...", flush=True)
    print(f"Video generated in {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
