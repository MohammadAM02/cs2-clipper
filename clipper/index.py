"""Our SQLite index — the only code that writes it (spec: Index). The Clip library will read what
this stores; CS:DM's own database is never written."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from clipper.model import ClipFile, Highlight, MatchInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS demos (
    id             INTEGER PRIMARY KEY,
    file_name      TEXT NOT NULL UNIQUE,
    sha256         TEXT NOT NULL UNIQUE,
    archive_path   TEXT NOT NULL,
    dem_path       TEXT,
    match_checksum TEXT,
    state          TEXT NOT NULL,
    resume_state   TEXT,
    attempts       INTEGER NOT NULL DEFAULT 0,
    last_error     TEXT,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS matches (
    checksum       TEXT PRIMARY KEY,
    map            TEXT NOT NULL,
    played_at      TEXT NOT NULL,
    team_score     INTEGER NOT NULL,
    opponent_score INTEGER NOT NULL,
    result         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS highlights (
    id               INTEGER PRIMARY KEY,
    match_checksum   TEXT NOT NULL,
    round            INTEGER NOT NULL,
    type             TEXT NOT NULL,
    score            INTEGER NOT NULL,
    reasons          TEXT NOT NULL,
    frag_ticks       TEXT NOT NULL,
    round_start_tick INTEGER NOT NULL,
    round_end_tick   INTEGER NOT NULL,
    selected         INTEGER NOT NULL DEFAULT 0,
    UNIQUE (match_checksum, round)
);
CREATE TABLE IF NOT EXISTS render_jobs (
    id          INTEGER PRIMARY KEY,
    demo_id     INTEGER NOT NULL REFERENCES demos (id),
    perspective TEXT NOT NULL,
    attempt     INTEGER NOT NULL,
    state       TEXT NOT NULL,
    output_dir  TEXT,
    log_path    TEXT,
    started_at  TEXT,
    finished_at TEXT,
    failure     TEXT
);
CREATE TABLE IF NOT EXISTS clips (
    id            INTEGER PRIMARY KEY,
    render_job_id INTEGER NOT NULL REFERENCES render_jobs (id),
    highlight_id  INTEGER NOT NULL REFERENCES highlights (id),
    sequence      INTEGER NOT NULL,
    start_tick    INTEGER NOT NULL,
    end_tick      INTEGER NOT NULL,
    path          TEXT NOT NULL,
    duration_s    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS reels (
    id           INTEGER PRIMARY KEY,
    highlight_id INTEGER NOT NULL REFERENCES highlights (id),
    perspective  TEXT NOT NULL,
    path         TEXT NOT NULL,
    duration_s   REAL NOT NULL,
    UNIQUE (highlight_id, perspective)
);
CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_DEMO_FIELDS = frozenset({"dem_path", "match_checksum", "last_error"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Index:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, isolation_level=None)   # autocommit: one statement, one transaction
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        self._db.close()

    def _one(self, sql: str, params: tuple = ()) -> sqlite3.Row | None:
        return self._db.execute(sql, params).fetchone()

    def _all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._db.execute(sql, params).fetchall()

    # --- Demos ---------------------------------------------------------------------------------

    def add_demo(self, file_name: str, sha256: str, archive_path: Path) -> int:
        now = _now()
        cursor = self._db.execute(
            "INSERT INTO demos (file_name, sha256, archive_path, state, created_at, updated_at)"
            " VALUES (?, ?, ?, 'spotted', ?, ?)",
            (file_name, sha256, str(archive_path), now, now),
        )
        return cursor.lastrowid

    def has_demo(self, file_name: str, sha256: str) -> bool:
        row = self._one("SELECT 1 FROM demos WHERE file_name = ? OR sha256 = ?", (file_name, sha256))
        return row is not None

    def demo(self, demo_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM demos WHERE id = ?", (demo_id,))

    def find_demo(self, key: str) -> sqlite3.Row | None:
        """By index id or by file name."""
        if key.isdigit() and (row := self.demo(int(key))) is not None:
            return row
        return self._one("SELECT * FROM demos WHERE file_name = ?", (key,))

    def demos_in(self, states: Iterable[str]) -> list[sqlite3.Row]:
        states = tuple(states)
        marks = ", ".join("?" * len(states))
        return self._all(f"SELECT * FROM demos WHERE state IN ({marks}) ORDER BY id", states)

    def all_demos(self) -> list[sqlite3.Row]:
        return self._all("SELECT * FROM demos ORDER BY id")

    def advance(self, demo_id: int, state: str, **fields: object) -> None:
        """Move a Demo to `state`; the new step's attempts start from zero."""
        unknown = set(fields) - _DEMO_FIELDS
        if unknown:
            raise ValueError(f"unknown demo field(s): {', '.join(sorted(unknown))}")
        values = [str(value) if isinstance(value, Path) else value for value in fields.values()]
        extra = "".join(f", {name} = ?" for name in fields)
        self._db.execute(
            f"UPDATE demos SET state = ?, attempts = 0, updated_at = ?{extra} WHERE id = ?",
            (state, _now(), *values, demo_id),
        )

    def record_failure(self, demo_id: int, error: str) -> int:
        """Count a failed attempt at the current step; returns the attempts so far."""
        self._db.execute(
            "UPDATE demos SET attempts = attempts + 1, last_error = ?, updated_at = ? WHERE id = ?",
            (error, _now(), demo_id),
        )
        return self.demo(demo_id)["attempts"]

    def fail(self, demo_id: int, error: str) -> None:
        """Give up on a Demo, remembering the step to resume at."""
        self._db.execute(
            "UPDATE demos SET resume_state = state, state = 'failed', last_error = ?, updated_at = ?"
            " WHERE id = ?",
            (error, _now(), demo_id),
        )

    def retry(self, demo_id: int) -> str:
        """Send a failed Demo back to the step that failed; returns that step's state."""
        demo = self.demo(demo_id)
        if demo is None or demo["state"] != "failed":
            raise ValueError(f"demo {demo_id} has not failed")
        state = demo["resume_state"] or "spotted"
        self.advance(demo_id, state, last_error=None)
        if state == "rendering":
            for perspective in ("player", "enemy"):
                job = self.latest_render(demo_id, perspective)
                if job is not None and job["state"] == "failed":
                    self.queue_render(demo_id, perspective, attempt=1)
        return state

    # --- Matches and Highlights ------------------------------------------------------------------

    def save_match(self, info: MatchInfo) -> None:
        self._db.execute(
            "INSERT INTO matches (checksum, map, played_at, team_score, opponent_score, result)"
            " VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (checksum) DO UPDATE SET map = excluded.map, played_at = excluded.played_at,"
            " team_score = excluded.team_score, opponent_score = excluded.opponent_score,"
            " result = excluded.result",
            (info.checksum, info.map_name, info.played_at.isoformat(), info.team_score,
             info.opponent_score, info.result),
        )

    def match(self, checksum: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM matches WHERE checksum = ?", (checksum,))

    def save_highlights(self, checksum: str, highlights: Iterable[Highlight], selected_rounds: set[int]) -> None:
        for h in highlights:
            self._db.execute(
                "INSERT INTO highlights (match_checksum, round, type, score, reasons, frag_ticks,"
                " round_start_tick, round_end_tick, selected) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT (match_checksum, round) DO UPDATE SET type = excluded.type,"
                " score = excluded.score, reasons = excluded.reasons, frag_ticks = excluded.frag_ticks,"
                " round_start_tick = excluded.round_start_tick, round_end_tick = excluded.round_end_tick,"
                " selected = excluded.selected",
                (checksum, h.round, h.type, h.score, json.dumps(list(h.reasons)),
                 json.dumps(list(h.frag_ticks)), h.round_start_tick, h.round_end_tick,
                 int(h.round in selected_rounds)),
            )

    def selected_highlights(self, checksum: str) -> list[sqlite3.Row]:
        return self._all(
            "SELECT * FROM highlights WHERE match_checksum = ? AND selected = 1 ORDER BY round", (checksum,)
        )

    # --- Render Jobs, Clips and Reels --------------------------------------------------------------

    def queue_render(self, demo_id: int, perspective: str, attempt: int) -> int:
        cursor = self._db.execute(
            "INSERT INTO render_jobs (demo_id, perspective, attempt, state) VALUES (?, ?, ?, 'queued')",
            (demo_id, perspective, attempt),
        )
        return cursor.lastrowid

    def latest_render(self, demo_id: int, perspective: str) -> sqlite3.Row | None:
        return self._one(
            "SELECT * FROM render_jobs WHERE demo_id = ? AND perspective = ? ORDER BY id DESC LIMIT 1",
            (demo_id, perspective),
        )

    def start_render(self, job_id: int, output_dir: Path, log_path: Path) -> None:
        self._db.execute(
            "UPDATE render_jobs SET state = 'running', output_dir = ?, log_path = ?, started_at = ?"
            " WHERE id = ?",
            (str(output_dir), str(log_path), _now(), job_id),
        )

    def finish_render(self, job_id: int, state: str, failure: str | None = None) -> None:
        self._db.execute(
            "UPDATE render_jobs SET state = ?, failure = ?, finished_at = ? WHERE id = ?",
            (state, failure, _now(), job_id),
        )

    def add_clip(self, job_id: int, highlight_id: int, clip: ClipFile) -> None:
        self._db.execute(
            "INSERT INTO clips (render_job_id, highlight_id, sequence, start_tick, end_tick, path, duration_s)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (job_id, highlight_id, clip.sequence, clip.start_tick, clip.end_tick, str(clip.path),
             clip.duration_s),
        )

    def clips_for(self, highlight_id: int, perspective: str) -> list[sqlite3.Row]:
        """The Clips of a finished render for one Highlight and Perspective, in Tick order."""
        return self._all(
            "SELECT c.* FROM clips c JOIN render_jobs j ON j.id = c.render_job_id"
            " WHERE c.highlight_id = ? AND j.perspective = ? AND j.state = 'done' ORDER BY c.start_tick",
            (highlight_id, perspective),
        )

    def save_reel(self, highlight_id: int, perspective: str, path: Path, duration_s: float) -> None:
        self._db.execute(
            "INSERT INTO reels (highlight_id, perspective, path, duration_s) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (highlight_id, perspective) DO UPDATE SET path = excluded.path,"
            " duration_s = excluded.duration_s",
            (highlight_id, perspective, str(path), duration_s),
        )

    def reel_count(self, checksum: str) -> int:
        row = self._one(
            "SELECT count(*) FROM reels r JOIN highlights h ON h.id = r.highlight_id"
            " WHERE h.match_checksum = ?",
            (checksum,),
        )
        return row[0]

    # --- App state -----------------------------------------------------------------------------------

    def get_flag(self, key: str, default: str | None = None) -> str | None:
        row = self._one("SELECT value FROM app_state WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_flag(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO app_state (key, value) VALUES (?, ?)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
