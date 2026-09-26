"""Command line (spec: Running it): run, status, retry, resume and highlights."""

from __future__ import annotations

import argparse
import logging
import msvcrt
import re
import socket
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from functools import partial
from logging.handlers import RotatingFileHandler
from pathlib import Path

from clipper import csdm_db, postgres
from clipper.alerts import MatchAlerts
from clipper.config import REPO_ROOT, Config, load_config, load_env
from clipper.install import install, uninstall
from clipper.csdm_cli import CsdmCli
from clipper.faceit import FaceitClient
from clipper.gate import Gate, GateStatus
from clipper.index import Index
from clipper.intake import Intake
from clipper.join import join_reel
from clipper.media import probe_duration
from clipper.notify import notify
from clipper.page import PageServer
from clipper.procs import SystemProbe
from clipper.render import render
from clipper.scoring import score_match, select
from clipper.unpack import unpack
from clipper.worker import PERSPECTIVES, Services, Worker

log = logging.getLogger("clipper")
CHECKSUM = re.compile(r"^[0-9a-f]{16}$")


class AlreadyRunning(Exception):
    """Another clipper holds the lock."""


@contextmanager
def single_instance(lock_path: Path) -> Iterator[None]:
    """Hold an exclusive lock on lock_path for the life of the block."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    handle.seek(0)
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError as exc:
        handle.close()
        raise AlreadyRunning(f"another clipper is already running (lock: {lock_path})") from exc
    try:
        yield
    finally:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


def format_status(index: Index, gate: GateStatus, host: str | None = None) -> str:
    lines = ["Gate: " + ("clear" if gate.ok else "waiting: " + "; ".join(gate.reasons))]
    alerts = index.get_flag("alerts_status")
    if alerts is not None:
        port = index.get_flag("page_port")
        if alerts == "on" and port:
            waiting = len(index.faceit_matches_in(("ready", "announced")))
            alerts += f" · {waiting} to grab · http://{host or socket.gethostname()}:{port}/demos"
        lines.append(f"Match alerts: {alerts}")
    if index.get_flag("paused") == "1":
        lines.append("Rendering: PAUSED after repeated failures. Check HLAE/CS2, then run: clipper resume")
    demos = index.all_demos()
    if not demos:
        lines.append("No Demos yet. Download one from a FACEIT matchroom.")
    for demo in demos:
        line = f"#{demo['id']:<3} {demo['state']:<9} {demo['file_name']}"
        match = index.match(demo["match_checksum"]) if demo["match_checksum"] else None
        if match is not None:
            line += f"  {match['map']} {match['team_score']}-{match['opponent_score']} {match['result']}"
        if demo["state"] == "rendering":
            jobs = [(p, index.latest_render(demo["id"], p)) for p in PERSPECTIVES]
            line += "  " + ", ".join(f"{p} {job['state']} (attempt {job['attempt']})" for p, job in jobs if job)
        if demo["state"] == "done" and match is not None:
            line += f"  {index.reel_count(demo['match_checksum'])} Reels"
        if demo["state"] in ("failed", "skipped") and demo["last_error"]:
            line += f"  - {demo['last_error']}"
        lines.append(line)
    return "\n".join(lines)


def cmd_retry(index: Index, key: str) -> int:
    demo = index.find_demo(key)
    if demo is None:
        print(f"no Demo matches {key!r}")
        return 1
    try:
        state = index.retry(demo["id"])
    except ValueError as exc:
        print(exc)
        return 1
    print(f"#{demo['id']} is back at '{state}'")
    return 0


def cmd_resume(index: Index) -> int:
    index.set_flag("paused", "0")
    index.set_flag("consecutive_failures", "0")
    print("rendering resumed")
    return 0


def cmd_highlights(cfg: Config, index: Index, key: str) -> int:
    demo = index.find_demo(key)
    checksum = demo["match_checksum"] if demo is not None else (key if CHECKSUM.match(key) else None)
    if not checksum:
        print(f"no analyzed Demo matches {key!r}")
        return 1
    with csdm_db.connect(cfg.database_conninfo()) as conn:
        facts = csdm_db.round_facts(conn, checksum, cfg.subject_steamid)
    highlights = score_match(facts)
    chosen = {h.round for h in select(highlights, cfg.top_n)}
    print(f"{'round':>6} {'type':<5} {'score':>5}  reasons")
    for h in highlights:
        mark = "*" if h.round in chosen else " "
        print(f"{h.round:>5}{mark} {h.type:<5} {h.score:>5}  {', '.join(h.reasons)}")
    print(f"* = in the top {cfg.top_n}")
    return 0


def start_match_alerts(cfg: Config, index: Index, probe: SystemProbe, gate: Gate) -> MatchAlerts | None:
    """Start the Demos to grab page and return the match alerts step. When alerts are off, say why in
    the index (clipper status shows it) and return None."""
    if not cfg.match_alerts:
        index.set_flag("alerts_status", "off: match_alerts = false in clipper.toml")
        return None
    env = load_env(REPO_ROOT / ".env")
    key, nickname = env.get("FACEIT_API_KEY", ""), env.get("FACEIT_NICKNAME", "")
    if not (key and nickname):
        index.set_flag("alerts_status", "off: set FACEIT_API_KEY and FACEIT_NICKNAME in .env")
        return None
    page = PageServer(cfg.index_path, cfg.page_port, gate_reasons=lambda: gate.check().reasons)
    page.start()
    index.set_flag("page_port", str(page.port))
    return MatchAlerts(index, FaceitClient(key), notify, probe.user_cs2_running, nickname=nickname,
                       subject_steamid=cfg.subject_steamid, page_url=f"http://127.0.0.1:{page.port}/demos",
                       stopped_playing_minutes=cfg.stopped_playing_minutes)


def build_worker(cfg: Config, index: Index) -> Worker:
    probe = SystemProbe()
    gate = Gate(probe, cfg.data_root, cfg.min_free_gb)
    csdm = CsdmCli(prefix=(str(cfg.csdm_exe), str(cfg.csdm_cli_js)), home=cfg.csdm_home, pg_bin=cfg.pg_bin)
    duration_of = partial(probe_duration, ffprobe=cfg.ffprobe)

    def render_job(request, should_abort):
        return render(request, csdm=csdm, probe=probe, should_abort=should_abort,
                      stall_seconds=cfg.stall_seconds, launch_timeout_seconds=cfg.launch_timeout_seconds,
                      duration_of=duration_of)

    services = Services(
        intake=Intake(cfg.downloads_dir, cfg.demos_dir),
        unpack=unpack,
        analyze=csdm.analyze,
        facts=csdm_db.CsdmFacts(cfg.database_conninfo()),
        gate=gate,
        render=render_job,
        join=partial(join_reel, ffmpeg=cfg.ffmpeg, duration_of=duration_of, stretch=cfg.stretch),
        notify=notify,
        alerts=start_match_alerts(cfg, index, probe, gate),
    )
    return Worker(cfg, index, services)


def _setup_logging(logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        RotatingFileHandler(logs_dir / "clipper.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    ]
    if sys.stderr is not None:                        # pythonw has no console
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, handlers=handlers,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def cmd_run(cfg: Config) -> int:
    try:
        with single_instance(cfg.index_path.with_name("clipper.lock")):
            try:
                _setup_logging(cfg.logs_dir)
                for folder in (cfg.demos_dir, cfg.renders_dir, cfg.library_dir):
                    folder.mkdir(parents=True, exist_ok=True)
                postgres.ensure_running(cfg.pg_bin, cfg.pg_data)
                index = Index(cfg.index_path)
                worker = build_worker(cfg, index)
            except Exception as exc:  # noqa: BLE001 - at sign-in there is no console: say why, then stop
                log.exception("clipper could not start")
                notify("clipper could not start", str(exc) or type(exc).__name__)
                return 1
            log.info("clipper is running; watching %s", cfg.downloads_dir)
            while True:
                try:
                    worker.tick()
                except Exception:  # noqa: BLE001 - the loop must survive anything a tick throws
                    log.exception("tick failed")
                time.sleep(cfg.poll_seconds)
    except AlreadyRunning as exc:
        print(exc, file=sys.stderr)
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clipper", description="Hands-off CS2 highlight clipper.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("run", help="run the background app")
    commands.add_parser("status", help="show every Demo's state and the Gate")
    commands.add_parser("retry", help="send a failed Demo back through").add_argument(
        "demo", help="index id or file name")
    commands.add_parser("resume", help="resume rendering after a pause")
    commands.add_parser("highlights", help="print the scored Highlights of an analyzed Demo").add_argument(
        "demo", help="index id, file name, or CS:DM match checksum")
    commands.add_parser("install", help="start the app when you sign in (Task Scheduler)")
    commands.add_parser("uninstall", help="remove the sign-in task")
    args = parser.parse_args(argv)
    cfg = load_config()
    if args.command == "run":
        return cmd_run(cfg)
    if args.command == "install":
        install(REPO_ROOT)
        print("clipper will start when you sign in (Task Scheduler task 'cs2-clipper')")
        return 0
    if args.command == "uninstall":
        uninstall()
        print("sign-in task removed")
        return 0
    index = Index(cfg.index_path)
    try:
        if args.command == "status":
            print(format_status(index, Gate(SystemProbe(), cfg.data_root, cfg.min_free_gb).check()))
            return 0
        if args.command == "retry":
            return cmd_retry(index, args.demo)
        if args.command == "resume":
            return cmd_resume(index)
        return cmd_highlights(cfg, index, args.demo)
    finally:
        index.close()
