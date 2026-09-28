"""Command line (spec: The terminal): the app itself (no subcommand, or `run`), and status, retry,
resume and highlights for development. `--window` is internal: the window process."""

from __future__ import annotations

import argparse
import re
import socket
import sys

from clipper import app, csdm_db, move_in, paths, settings, window
from clipper.config import REPO_ROOT, Config
from clipper.gate import Gate, GateStatus
from clipper.index import Index
from clipper.procs import SystemProbe
from clipper.scoring import score_match, select
from clipper.worker import PERSPECTIVES

CHECKSUM = re.compile(r"^[0-9a-f]{16}$")


def format_status(index: Index, gate: GateStatus, host: str | None = None) -> str:
    lines = ["Gate: " + ("clear" if gate.ok else "waiting: " + "; ".join(gate.reasons))]
    alerts = index.get_flag("alerts_status")
    if alerts is not None:
        port = index.get_flag("page_port")
        if alerts == "on" and port:
            waiting = len(index.faceit_matches_in(("ready", "announced")))
            alerts += f" · {waiting} to grab · http://{host or socket.gethostname()}:{port}/demos"
        lines.append(f"Match alerts: {alerts}")
    paused_by = index.paused_by()
    if paused_by == "you":
        lines.append("Rendering: PAUSED by you. Resume from the tray, the Status page or: clipper resume")
    elif paused_by is not None:
        lines.append(
            "Rendering: PAUSED after repeated failures. Check HLAE/CS2, then resume from the tray, "
            "the Status page or: clipper resume"
        )
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
    index.resume()
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


_OPEN_CHOICES = tuple(page.removeprefix("/") for page in app.PAGES)


def _add_open_argument(target: argparse.ArgumentParser) -> None:
    target.add_argument("--open", choices=_OPEN_CHOICES, default=None,
                        help="show this page (asks the running copy, or starts the app and opens it)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="clipper", description="Hands-off CS2 highlight clipper.")
    _add_open_argument(parser)
    parser.add_argument("--background", action="store_true",
                        help="start in the tray only (used at sign-in)")
    parser.add_argument("--window", metavar="URL", help=argparse.SUPPRESS)   # internal: the window process
    commands = parser.add_subparsers(dest="command")
    run_parser = commands.add_parser("run", help="run the app")
    run_parser.add_argument("--headless", action="store_true",
                            help="no tray or window: the worker and the web server only")
    _add_open_argument(run_parser)
    commands.add_parser("status", help="show every Demo's state and the Gate")
    commands.add_parser("retry", help="send a failed Demo back through").add_argument(
        "demo", help="index id or file name")
    commands.add_parser("resume", help="resume rendering after a pause")
    commands.add_parser("highlights", help="print the scored Highlights of an analyzed Demo").add_argument(
        "demo", help="index id, file name, or CS:DM match checksum")
    args = parser.parse_args(argv)

    if args.window:      # the window process: no lock and no move-in, it only shows a page
        return window.run_window(args.window, paths.data_dir() / "window-profile")
    if args.command in (None, "run"):
        return app.run(open_page=f"/{args.open}" if args.open else None, background=args.background,
                       headless=args.command == "run" and args.headless)

    copied = move_in.on_start()
    if copied:
        print(f"Moved in from {REPO_ROOT}: {', '.join(copied)}", file=sys.stderr)
    cfg = settings.load(paths.settings_file()).config
    index = Index(cfg.index_path)
    try:
        if args.command == "status":
            if cfg.data_root is None:
                gate_status = GateStatus(ok=False, reasons=("the clips folder is not set",))
            else:
                gate_status = Gate(SystemProbe(), cfg.data_root, cfg.min_free_gb).check()
            print(format_status(index, gate_status))
            return 0
        if args.command == "retry":
            return cmd_retry(index, args.demo)
        if args.command == "resume":
            return cmd_resume(index)
        return cmd_highlights(cfg, index, args.demo)
    finally:
        index.close()
