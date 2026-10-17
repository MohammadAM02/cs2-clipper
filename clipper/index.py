"""Our SQLite index — the only code that writes it (spec: Index). The Clip library will read what
this stores."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from clipper.model import Break, ClipFile, FaceitStats, Highlight, MatchInfo

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
CREATE TABLE IF NOT EXISTS asked_renders (
    demo_id     INTEGER NOT NULL REFERENCES demos (id),
    perspective TEXT NOT NULL,
    PRIMARY KEY (demo_id, perspective)
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
CREATE TABLE IF NOT EXISTS faceit_matches (
    match_id       TEXT PRIMARY KEY,
    finished_at    TEXT NOT NULL,
    state          TEXT NOT NULL,
    map            TEXT NOT NULL DEFAULT '',
    team_score     INTEGER NOT NULL DEFAULT 0,
    opponent_score INTEGER NOT NULL DEFAULT 0,
    won            INTEGER NOT NULL DEFAULT 0,
    highlights     TEXT NOT NULL DEFAULT '{}',
    kills          INTEGER NOT NULL DEFAULT 0,
    deaths         INTEGER NOT NULL DEFAULT 0,
    assists        INTEGER NOT NULL DEFAULT 0,
    adr            REAL NOT NULL DEFAULT 0,
    rounds         INTEGER NOT NULL DEFAULT 0,
    rating         REAL NOT NULL DEFAULT 0,
    matchroom_url  TEXT NOT NULL DEFAULT '',
    announced_at   TEXT,
    reminded_at    TEXT,
    decided_at     TEXT,
    demo_id        INTEGER REFERENCES demos (id)
);
CREATE TABLE IF NOT EXISTS app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_DEMO_FIELDS = frozenset({"dem_path", "match_checksum", "last_error"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


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

    def demo_of_match(self, checksum: str) -> sqlite3.Row | None:
        """The newest Demo of a match, or None when no Demo of it is in the index."""
        return self._one("SELECT * FROM demos WHERE match_checksum = ? ORDER BY id DESC LIMIT 1", (checksum,))

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
        """Send a failed Demo back to the step that failed; returns that step's state.

        Runs as one transaction so the worker never sees the Demo back in rendering
        before its Render Jobs are queued again.
        """
        self._db.execute("BEGIN IMMEDIATE")
        try:
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
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")
        return state

    def ask_render(self, demo_id: int, perspective: str) -> None:
        """Ask a done Demo to render the view it did not render: it goes back to rendering with a fresh Render Job for
        that view, and keeps the ask (`asked_renders`) until it is finished. One transaction, as in `retry`."""
        self._db.execute("BEGIN IMMEDIATE")
        try:
            demo = self.demo(demo_id)
            if demo is None:
                raise ValueError(f"demo {demo_id} does not exist")
            if demo["state"] != "done":
                raise ValueError(f"demo {demo_id} is not done")
            if perspective not in ("player", "enemy"):
                raise ValueError(f"unknown perspective: {perspective!r}")
            self._db.execute("INSERT INTO asked_renders (demo_id, perspective) VALUES (?, ?)"
                             " ON CONFLICT (demo_id, perspective) DO NOTHING", (demo_id, perspective))
            self.queue_render(demo_id, perspective, attempt=1)
            self.advance(demo_id, "rendering", last_error=None)
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")

    def delete_demo(self, demo_id: int) -> sqlite3.Row | None:
        """Forget a Demo that is not in Reels yet: its Render Jobs and their Clips go with it, and its
        FACEIT match lets go of it. Returns the row deleted (None when there was none); its files are
        the caller's. The match and its Highlights stay -- the same Demo taken again saves over them.
        """
        self._db.execute("BEGIN IMMEDIATE")
        try:
            demo = self.demo(demo_id)
            if demo is not None:
                if demo["state"] in ("joined", "done") or (       # or it renders another view of its Reels
                        demo["match_checksum"] and self.reel_count(demo["match_checksum"])):
                    raise ValueError(f"demo {demo_id} is already in Reels")
                self._db.execute("DELETE FROM clips WHERE render_job_id IN"
                                 " (SELECT id FROM render_jobs WHERE demo_id = ?)", (demo_id,))
                self._db.execute("DELETE FROM render_jobs WHERE demo_id = ?", (demo_id,))
                self._db.execute("DELETE FROM asked_renders WHERE demo_id = ?", (demo_id,))
                self._db.execute("UPDATE faceit_matches SET demo_id = NULL WHERE demo_id = ?", (demo_id,))
                self._db.execute("DELETE FROM demos WHERE id = ?", (demo_id,))
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._db.execute("COMMIT")
        return demo

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

    # Breaks live in the highlights table, so their Clips and Reels are like any others: the Reels page plays, copies
    # and exports them by Reel id. A Break is the row of round -N, N the round it follows, with its label as the type,
    # and it is never selected. The table keeps one row per round number, and builds of the app that know nothing of
    # Breaks share this database: they list selected rows only.

    def save_breaks(self, checksum: str, breaks: Iterable[Break]) -> None:
        """Saved again, a Break is updated in place."""
        for found in breaks:
            self._db.execute(
                "INSERT INTO highlights (match_checksum, round, type, score, reasons, frag_ticks,"
                " round_start_tick, round_end_tick, selected) VALUES (?, ?, ?, 0, '[]', '[]', ?, ?, 0)"
                " ON CONFLICT (match_checksum, round) DO UPDATE SET type = excluded.type,"
                " round_start_tick = excluded.round_start_tick, round_end_tick = excluded.round_end_tick",
                (checksum, -found.after_round, found.label, found.start_tick, found.end_tick),
            )

    def breaks(self, checksum: str) -> list[sqlite3.Row]:
        """The match's Breaks in round order: each row's id, the round it follows, its label and its ticks."""
        return self._all(
            "SELECT id, -round AS after_round, type AS label, round_start_tick AS start_tick,"
            " round_end_tick AS end_tick FROM highlights WHERE match_checksum = ? AND round < 0 ORDER BY round DESC",
            (checksum,),
        )

    def match_breaks(self, checksum: str) -> list[sqlite3.Row]:
        """The match's Breaks that have their Reel (a Break has your view only), in round order, each with the round
        it follows, its label, and its Reel's id and length in seconds."""
        return self._all(
            "SELECT -h.round AS after_round, h.type AS label, r.id AS reel_id, r.duration_s"
            " FROM highlights h JOIN reels r ON r.highlight_id = h.id AND r.perspective = 'player'"
            " WHERE h.match_checksum = ? AND h.round < 0 ORDER BY h.round DESC",
            (checksum,),
        )

    # --- Render Jobs, Clips and Reels --------------------------------------------------------------

    def queue_render(self, demo_id: int, perspective: str, attempt: int) -> int:
        cursor = self._db.execute(
            "INSERT INTO render_jobs (demo_id, perspective, attempt, state) VALUES (?, ?, ?, 'queued')",
            (demo_id, perspective, attempt),
        )
        return cursor.lastrowid

    def asked_renders(self, demo_id: int) -> tuple[str, ...]:
        """The views a Demo was asked to render (`ask_render`); the asks are cleared once the Demo is done."""
        rows = self._all("SELECT perspective FROM asked_renders WHERE demo_id = ? ORDER BY perspective", (demo_id,))
        return tuple(row["perspective"] for row in rows)

    def clear_asked_renders(self, demo_id: int) -> None:
        self._db.execute("DELETE FROM asked_renders WHERE demo_id = ?", (demo_id,))

    def unqueue_renders(self, perspective: str) -> int:
        """Take every Demo's queued Render Job for `perspective` off the queue; returns how many went.
        Jobs that started stay, so each Demo's latest attempt is its last real one again; so do the queued
        jobs of Demos that asked for this view (`ask_render`)."""
        cursor = self._db.execute(
            "DELETE FROM render_jobs WHERE perspective = ? AND state = 'queued'"
            " AND NOT EXISTS (SELECT 1 FROM asked_renders a WHERE a.demo_id = render_jobs.demo_id"
            " AND a.perspective = render_jobs.perspective)",
            (perspective,),
        )
        return cursor.rowcount

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

    def add_clip(self, job_id: int, highlight_id: int, clip: ClipFile, path: str) -> None:
        """`clip` holds the ticks and the duration; `path` is how the index keeps the file
        (`Config.store_path`: relative to the clips folder), not where it actually is."""
        self._db.execute(
            "INSERT INTO clips (render_job_id, highlight_id, sequence, start_tick, end_tick, path, duration_s)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (job_id, highlight_id, clip.sequence, clip.start_tick, clip.end_tick, path,
             clip.duration_s),
        )

    def clips_for(self, highlight_id: int, perspective: str) -> list[sqlite3.Row]:
        """The Clips of a finished render for one Highlight and Perspective, in Tick order."""
        return self._all(
            "SELECT c.* FROM clips c JOIN render_jobs j ON j.id = c.render_job_id"
            " WHERE c.highlight_id = ? AND j.perspective = ? AND j.state = 'done' ORDER BY c.start_tick",
            (highlight_id, perspective),
        )

    def save_reel(self, highlight_id: int, perspective: str, path: Path | str, duration_s: float) -> None:
        """`path` is stored as given: the app passes `Config.store_path` (relative to the clips folder),
        tests pass whatever they like."""
        self._db.execute(
            "INSERT INTO reels (highlight_id, perspective, path, duration_s) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (highlight_id, perspective) DO UPDATE SET path = excluded.path,"
            " duration_s = excluded.duration_s",
            (highlight_id, perspective, str(path), duration_s),
        )

    def has_reel(self, highlight_id: int, perspective: str) -> bool:
        """Whether this Highlight already has a Reel for `perspective`."""
        return self._one("SELECT 1 FROM reels WHERE highlight_id = ? AND perspective = ?",
                         (highlight_id, perspective)) is not None

    def reel_count(self, checksum: str) -> int:
        row = self._one(
            "SELECT count(*) FROM reels r JOIN highlights h ON h.id = r.highlight_id"
            " WHERE h.match_checksum = ?",
            (checksum,),
        )
        return row[0]

    def reel_matches(self) -> list[sqlite3.Row]:
        """Every match with at least one Reel on a selected Highlight -- what `match_reels` lists -- whatever
        its Demo's state: a match stays on the Reels page while its other view renders. Newest played first
        (spec: Pages, Reels)."""
        return self._all(
            "SELECT m.checksum, m.map, m.played_at, m.team_score, m.opponent_score, m.result"
            " FROM matches m"
            " WHERE EXISTS (SELECT 1 FROM highlights h JOIN reels r ON r.highlight_id = h.id"
            " WHERE h.match_checksum = m.checksum AND h.selected = 1)"
            " ORDER BY m.played_at DESC"
        )

    def match_reels(self, checksum: str) -> list[sqlite3.Row]:
        """That match's selected Highlights that have a Reel, in round order, with each
        Perspective's Reel id and length in seconds (NULL when that Perspective has none)."""
        return self._all(
            "SELECT h.id AS highlight_id, h.round, h.type, h.reasons,"
            " pr.id AS player_reel_id, er.id AS enemy_reel_id, pr.duration_s AS player_s, er.duration_s AS enemy_s"
            " FROM highlights h"
            " LEFT JOIN reels pr ON pr.highlight_id = h.id AND pr.perspective = 'player'"
            " LEFT JOIN reels er ON er.highlight_id = h.id AND er.perspective = 'enemy'"
            " WHERE h.match_checksum = ? AND h.selected = 1 AND (pr.id IS NOT NULL OR er.id IS NOT NULL)"
            " ORDER BY h.round",
            (checksum,),
        )

    def render_seconds(self, last: int = 10) -> list[tuple[float, int]]:
        """The last `last` finished Render Jobs as (seconds from start to finish, Highlights recorded): what an
        estimate of the next render's length goes on."""
        rows = self._all(
            "SELECT j.started_at, j.finished_at, (SELECT count(*) FROM highlights h"
            " WHERE h.match_checksum = d.match_checksum AND h.selected = 1) AS highlights"
            " FROM render_jobs j JOIN demos d ON d.id = j.demo_id"
            " WHERE j.state = 'done' AND j.started_at IS NOT NULL AND j.finished_at IS NOT NULL"
            " ORDER BY j.id DESC LIMIT ?",
            (last,),
        )
        found = []
        for row in rows:
            try:
                seconds = (datetime.fromisoformat(row["finished_at"]) - datetime.fromisoformat(row["started_at"]))
            except ValueError:
                continue
            if row["highlights"] and seconds.total_seconds() > 0:
                found.append((seconds.total_seconds(), row["highlights"]))
        return found

    def best_reel(self, checksum: str, perspective: str) -> int | None:
        """The id of the match's Reel of `perspective` whose selected Highlight scored highest (the earlier round
        on a tie): what a "ready" toast's Watch plays. None when the match has no Reel of that view."""
        row = self._one(
            "SELECT r.id FROM reels r JOIN highlights h ON h.id = r.highlight_id"
            " WHERE h.match_checksum = ? AND h.selected = 1 AND r.perspective = ?"
            " ORDER BY h.score DESC, h.round LIMIT 1",
            (checksum, perspective),
        )
        return row["id"] if row else None

    def reel(self, reel_id: int) -> sqlite3.Row | None:
        return self._one("SELECT * FROM reels WHERE id = ?", (reel_id,))

    _REEL_DETAILS = (
        "SELECT r.id, r.path, r.perspective, r.duration_s, h.round, h.type, h.match_checksum AS checksum, m.map,"
        " m.played_at, m.team_score, m.opponent_score FROM reels r JOIN highlights h ON h.id = r.highlight_id"
        " JOIN matches m ON m.checksum = h.match_checksum"
    )

    def reel_details(self, reel_id: int) -> sqlite3.Row | None:
        """A Reel with what its name and title card say: its Highlight's round and type, and its match."""
        return self._one(self._REEL_DETAILS + " WHERE r.id = ?", (reel_id,))

    def all_reel_details(self) -> list[sqlite3.Row]:
        return self._all(self._REEL_DETAILS + " ORDER BY r.id")

    def set_reel_path(self, reel_id: int, path: str) -> None:
        self._db.execute("UPDATE reels SET path = ? WHERE id = ?", (path, reel_id))

    # --- FACEIT matches (match alerts) ---------------------------------------------------------------

    def save_faceit_match(self, match_id: str, finished_at: datetime, state: str,
                          stats: FaceitStats | None = None, rating: float = 0.0, matchroom_url: str = "") -> None:
        """What FACEIT says about a match. The announced, reminded and decided times and the Demo stay."""
        s = stats or FaceitStats()
        self._db.execute(
            "INSERT INTO faceit_matches (match_id, finished_at, state, map, team_score, opponent_score, won,"
            " highlights, kills, deaths, assists, adr, rounds, rating, matchroom_url)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (match_id) DO UPDATE SET state = excluded.state, map = excluded.map,"
            " team_score = excluded.team_score, opponent_score = excluded.opponent_score, won = excluded.won,"
            " highlights = excluded.highlights, kills = excluded.kills, deaths = excluded.deaths,"
            " assists = excluded.assists, adr = excluded.adr, rounds = excluded.rounds,"
            " rating = excluded.rating, matchroom_url = excluded.matchroom_url",
            (match_id, _iso(finished_at), state, s.map_name, s.team_score, s.opponent_score, int(s.won),
             json.dumps(s.highlights), s.kills, s.deaths, s.assists, s.adr, s.rounds, rating, matchroom_url),
        )

    def faceit_match(self, match_id: str) -> sqlite3.Row | None:
        return self._one("SELECT * FROM faceit_matches WHERE match_id = ?", (match_id,))

    def faceit_matches_in(self, states: Iterable[str]) -> list[sqlite3.Row]:
        """Newest first."""
        states = tuple(states)
        marks = ", ".join("?" * len(states))
        return self._all(
            f"SELECT * FROM faceit_matches WHERE state IN ({marks}) ORDER BY finished_at DESC", states)

    def announce(self, match_ids: Iterable[str], at: datetime) -> None:
        """These ready matches were in a Match Alert."""
        for match_id in match_ids:
            self._db.execute(
                "UPDATE faceit_matches SET state = 'announced', announced_at = ?"
                " WHERE match_id = ? AND state = 'ready'", (_iso(at), match_id))

    def remind(self, match_ids: Iterable[str], at: datetime) -> None:
        for match_id in match_ids:
            self._db.execute("UPDATE faceit_matches SET reminded_at = ? WHERE match_id = ?", (_iso(at), match_id))

    def skip_match(self, match_id: str, at: datetime) -> bool:
        cursor = self._db.execute(
            "UPDATE faceit_matches SET state = 'skipped', decided_at = ?"
            " WHERE match_id = ? AND state IN ('ready', 'announced')", (_iso(at), match_id))
        return cursor.rowcount == 1

    def undo_skip(self, match_id: str) -> bool:
        cursor = self._db.execute(
            "UPDATE faceit_matches SET decided_at = NULL,"
            " state = CASE WHEN announced_at IS NULL THEN 'ready' ELSE 'announced' END"
            " WHERE match_id = ? AND state = 'skipped'", (match_id,))
        return cursor.rowcount == 1

    def link_grabbed_matches(self, at: datetime) -> int:
        """A match whose Demo is in the index has been grabbed, announced or not. Returns how many changed."""
        demo = ("(SELECT d.id FROM demos d WHERE d.file_name LIKE faceit_matches.match_id || '-%'"
                " ORDER BY d.id LIMIT 1)")
        cursor = self._db.execute(
            f"UPDATE faceit_matches SET state = 'grabbed', decided_at = ?, demo_id = {demo}"
            f" WHERE state IN ('waiting', 'ready', 'announced', 'skipped') AND {demo} IS NOT NULL",
            (_iso(at),))
        return cursor.rowcount

    def expire_faceit_matches(self, finished_before: datetime) -> int:
        cursor = self._db.execute(
            "UPDATE faceit_matches SET state = 'expired'"
            " WHERE state IN ('waiting', 'ready', 'announced') AND finished_at < ?", (_iso(finished_before),))
        return cursor.rowcount

    def faceit_matches_since(self, since: datetime) -> list[sqlite3.Row]:
        """FACEIT matches finished since `since`, newest first, each with its grabbed Demo's state and match
        checksum (`demo_state`, `demo_checksum`; NULL until a Demo is grabbed and analyzed)."""
        return self._all(
            "SELECT f.*, d.state AS demo_state, d.match_checksum AS demo_checksum FROM faceit_matches f"
            " LEFT JOIN demos d ON d.id = f.demo_id WHERE f.finished_at >= ? ORDER BY f.finished_at DESC",
            (_iso(since),),
        )

    def matches_played_since(self, since: datetime) -> list[sqlite3.Row]:
        """Matches played since `since` (by their Demos' own clocks), newest first: each with its newest Demo's id
        and state, how many Highlights were picked, and how many of those are Aces."""
        rows = self._all(
            "SELECT m.*,"
            " (SELECT d.id FROM demos d WHERE d.match_checksum = m.checksum ORDER BY d.id DESC LIMIT 1) AS demo_id,"
            " (SELECT d.state FROM demos d WHERE d.match_checksum = m.checksum ORDER BY d.id DESC LIMIT 1)"
            " AS demo_state,"
            " (SELECT COUNT(*) FROM highlights h WHERE h.match_checksum = m.checksum AND h.selected = 1) AS picked,"
            " (SELECT COUNT(*) FROM highlights h WHERE h.match_checksum = m.checksum AND h.selected = 1"
            " AND h.type = 'ACE') AS aces"
            " FROM matches m WHERE m.played_at >= ? ORDER BY m.played_at DESC",
            # played_at keeps the UTC offset of the PC that analyzed it, so the text comparison keeps two days
            # to spare and the exact cut is made on the parsed time
            ((since - timedelta(days=2)).isoformat(),),
        )
        return [row for row in rows if datetime.fromisoformat(row["played_at"]) >= since]

    def page_matches(self, decided_since: datetime) -> list[sqlite3.Row]:
        """What the Demos to grab page lists, newest first: every match still to grab, and those skipped or
        grabbed since `decided_since`. `demo_state` is the grabbed Demo's pipeline state."""
        return self._all(
            "SELECT f.*, d.state AS demo_state FROM faceit_matches f LEFT JOIN demos d ON d.id = f.demo_id"
            " WHERE f.state IN ('ready', 'announced')"
            " OR (f.state IN ('skipped', 'grabbed') AND f.decided_at >= ?)"
            " ORDER BY f.finished_at DESC",
            (_iso(decided_since),),
        )

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

    def mark_opened(self, match_id: str, at: datetime) -> bool:
        """The player opened the match's room to download its Demo (Demos to grab's Open): the page says it waits for
        the download, whichever window or device asks. False when there is no such match to grab."""
        if self.faceit_match(match_id) is None:
            return False
        self.set_flag(f"opened:{match_id}", _iso(at))
        return True

    def opened_matches(self) -> set[str]:
        return {row["key"].removeprefix("opened:") for row in self._all(
            "SELECT key FROM app_state WHERE key LIKE 'opened:%'")}

    def delete_flag(self, key: str) -> None:
        self._db.execute("DELETE FROM app_state WHERE key = ?", (key,))

    def pause(self, by: str) -> None:
        """`by` is "you" (the tray, the Status page) or "failures" (the worker, after repeated
        failed renders)."""
        self.set_flag("paused", "1")
        self.set_flag("paused_by", by)

    def resume(self) -> None:
        self.set_flag("paused", "0")
        self.set_flag("consecutive_failures", "0")
        self.set_flag("paused_by", "")

    def snooze(self, until: datetime | None) -> None:
        """No render starts before `until` (the heads-up toast's Not now); None ends a snooze."""
        self.set_flag("snoozed_until", _iso(until) if until is not None else "")

    def snoozed_until(self, now: datetime) -> datetime | None:
        """When the snooze ends, while one is on at `now`; None when there is none."""
        try:
            until = datetime.fromisoformat(self.get_flag("snoozed_until") or "")
        except ValueError:
            return None
        return until if until > now else None

    def paused_by(self) -> str | None:
        """None unless paused; "failures" when paused but paused_by is missing or empty (an index
        paused by an older version)."""
        if self.get_flag("paused") != "1":
            return None
        return self.get_flag("paused_by") or "failures"
