"""Starts the portable Postgres that CS:DM uses. It is not a Windows service, so it is down after a reboot."""

from __future__ import annotations

import subprocess
from pathlib import Path

PORT = 5432


def is_running(pg_bin: Path, pg_data: Path) -> bool:
    result = subprocess.run(
        [str(pg_bin / "pg_ctl.exe"), "-D", str(pg_data), "status"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return result.returncode == 0


def ensure_running(pg_bin: Path, pg_data: Path) -> None:
    """Start the cluster if it is down. Output goes to DEVNULL: the server inherits pg_ctl's handles,
    and capturing them would block forever (see scripts/setup_local_postgres.py)."""
    if is_running(pg_bin, pg_data):
        return
    log_file = pg_data.parent / "postgres.log"
    result = subprocess.run(
        [str(pg_bin / "pg_ctl.exe"), "-D", str(pg_data), "-l", str(log_file),
         "-o", f"-p {PORT} -c listen_addresses=127.0.0.1", "-w", "start"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Postgres did not start; see {log_file}")
