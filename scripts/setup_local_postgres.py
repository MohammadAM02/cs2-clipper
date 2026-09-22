#!/usr/bin/env python3
"""Provision a private, portable PostgreSQL 17 for CS:DM and wire it into its settings.

No Windows service, no elevation, no UAC: the cluster lives under %LOCALAPPDATA%\\pg17 and is
started with pg_ctl. Deleting that folder undoes everything.

    python scripts/setup_local_postgres.py            # init if needed, start, create db, configure
    python scripts/setup_local_postgres.py --status    # report only
    python scripts/setup_local_postgres.py --stop      # stop the cluster

The generated superuser password is written to home/.csdm/.pgpass.txt (gitignored) and into CS:DM's
own settings.json, which is where CS:DM requires it. It is never printed.

Implementation note: `pg_ctl start` spawns a long-lived postgres server that INHERITS the parent's
stdout/stderr handles. Capturing that output makes the call block forever waiting for an EOF that
never arrives, so lifecycle calls run with output sent to DEVNULL.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PGROOT = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "pg17"
BIN = PGROOT / "pgsql" / "bin"
DATA = PGROOT / "data"
LOG = PGROOT / "postgres.log"
PORT = 5432
DB_NAME = "csdm"
SUPERUSER = "postgres"

CSDM_HOME = ROOT / "home" / ".csdm"           # CS:DM app folder (USERPROFILE override target)
PWFILE = CSDM_HOME / ".pgpass.txt"
SETTINGS = CSDM_HOME / "settings.json"


def run(
    args: list[str],
    *,
    check: bool = True,
    env: dict | None = None,
    stdin: str | None = None,
    quiet: bool = False,
) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **(env or {})}
    handles: dict = (
        {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL} if quiet else {"capture_output": True}
    )
    return subprocess.run(
        args, text=True, check=check, env=full_env, input=stdin, timeout=300, **handles
    )


def exe(name: str) -> str:
    path = BIN / f"{name}.exe"
    if not path.exists():
        print(f"missing binary: {path}\nInstall the EDB binaries zip into {PGROOT} first.")
        raise SystemExit(2)
    return str(path)


def get_password() -> str:
    PWFILE.parent.mkdir(parents=True, exist_ok=True)
    if PWFILE.exists():
        password = PWFILE.read_text(encoding="utf-8").strip()
        if password:
            return password
    password = secrets.token_urlsafe(24)
    PWFILE.write_text(password, encoding="utf-8")
    return password


def is_initialised() -> bool:
    return (DATA / "PG_VERSION").exists()


def is_running() -> bool:
    return run([exe("pg_ctl"), "-D", str(DATA), "status"], check=False, quiet=True).returncode == 0


def init_cluster(password: str) -> None:
    DATA.parent.mkdir(parents=True, exist_ok=True)
    pwfile = PGROOT / ".initpw"
    pwfile.write_text(password, encoding="utf-8")
    try:
        print(f"initdb -> {DATA}")
        result = run(
            [
                exe("initdb"),
                "-D", str(DATA),
                "-U", SUPERUSER,
                "--pwfile", str(pwfile),
                "-E", "UTF8",
                "-A", "scram-sha-256",
            ],
            check=False,
        )
    finally:
        pwfile.unlink(missing_ok=True)
    if result.returncode != 0:
        print((result.stdout or "")[-2000:])
        print((result.stderr or "")[-2000:])
        raise SystemExit(1)
    print("initdb OK")


def start_cluster() -> None:
    print(f"starting postgres on 127.0.0.1:{PORT}")
    result = run(
        [
            exe("pg_ctl"), "-D", str(DATA), "-l", str(LOG),
            "-o", f"-p {PORT} -c listen_addresses=127.0.0.1",
            "-w", "start",
        ],
        check=False,
        quiet=True,
    )
    if result.returncode != 0:
        print(f"pg_ctl start failed (see {LOG})")
        tail = LOG.read_text(encoding="utf-8", errors="replace").splitlines()[-15:] if LOG.exists() else []
        print("\n".join(tail))
        raise SystemExit(1)
    print("postgres started")


def stop_cluster() -> None:
    run([exe("pg_ctl"), "-D", str(DATA), "-m", "fast", "stop"], check=False, quiet=True)
    print("postgres stopped")


def psql(password: str, sql: str, dbname: str = "postgres") -> subprocess.CompletedProcess:
    return run(
        [exe("psql"), "-h", "127.0.0.1", "-p", str(PORT), "-U", SUPERUSER, "-d", dbname,
         "-v", "ON_ERROR_STOP=1", "-tAc", sql],
        check=False,
        env={"PGPASSWORD": password},
    )


def ensure_database(password: str) -> None:
    exists = psql(password, f"SELECT 1 FROM pg_database WHERE datname='{DB_NAME}';")
    if exists.stdout.strip() == "1":
        print(f"database '{DB_NAME}' already exists")
        return
    created = psql(password, f'CREATE DATABASE "{DB_NAME}";')
    if created.returncode != 0:
        print((created.stdout or "")[-500:], (created.stderr or "")[-500:])
        raise SystemExit(1)
    print(f"database '{DB_NAME}' created")


def configure_csdm(password: str) -> None:
    CSDM_HOME.mkdir(parents=True, exist_ok=True)
    settings = {}
    if SETTINGS.exists():
        settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    settings["database"] = {
        "hostname": "127.0.0.1",
        "port": PORT,
        "username": SUPERUSER,
        "password": password,
        "database": DB_NAME,
    }
    SETTINGS.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    print(f"CS:DM settings updated -> {SETTINGS} (password stored, not printed)")


def report(password: str) -> None:
    version = psql(password, "SELECT version();")
    print((version.stdout or version.stderr or "").strip()[:200])
    tables = psql(
        password,
        "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';",
        DB_NAME,
    )
    print(f"tables in {DB_NAME}: {tables.stdout.strip()}")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args(argv)

    if not BIN.exists():
        print(f"PostgreSQL binaries not found at {BIN}")
        return 2

    password = get_password()

    if args.stop:
        stop_cluster()
        return 0

    if args.status:
        print(f"cluster initialised: {is_initialised()}")
        print(f"running            : {is_running()}")
        if is_running():
            report(password)
        return 0

    if not is_initialised():
        init_cluster(password)
    if not is_running():
        start_cluster()
    else:
        print("postgres already running")
    ensure_database(password)
    configure_csdm(password)
    print()
    report(password)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
