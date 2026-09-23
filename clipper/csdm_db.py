"""The only code that reads CS:DM's database (ADR 0003). If CS:DM renames a table or a column,
this file is what changes. It only reads: nothing here writes to CS:DM's tables."""

from __future__ import annotations

from collections.abc import Mapping

import psycopg
from psycopg.rows import dict_row

from clipper.model import MatchInfo, RoundFacts

_FIND_CHECKSUM = """
SELECT checksum FROM demos WHERE name = %(name)s ORDER BY date DESC LIMIT 1
"""

_MATCH_INFO = """
SELECT d.checksum, d.map_name, d.date, us.score AS team_score, them.score AS opponent_score
FROM demos d
JOIN players p  ON p.match_checksum = d.checksum AND p.steam_id = %(steamid)s
JOIN teams us   ON us.match_checksum = d.checksum AND us.name = p.team_name
JOIN teams them ON them.match_checksum = d.checksum AND them.name <> p.team_name
WHERE d.checksum = %(checksum)s
"""

# Every Kill credited to the subject, per round, without team kills and suicides (same side).
# rounds and players are joined on match_checksum as well as on number / steam_id (issue 08).
_ROUND_FACTS = """
SELECT k.round_number                                      AS round,
       array_agg(k.tick ORDER BY k.tick)                   AS frag_ticks,
       count(*) FILTER (WHERE k.is_headshot)               AS headshots,
       count(*) FILTER (WHERE k.is_killer_blinded)         AS blind_frags,
       count(*) FILTER (WHERE k.weapon_type = 'melee')     AS knife_frags,
       count(*) FILTER (WHERE k.is_no_scope)               AS noscope_frags,
       count(*) FILTER (WHERE k.is_through_smoke)          AS smoke_frags,
       count(*) FILTER (WHERE k.is_killer_controlling_bot) AS bot_frags,
       bool_or(r.winner_name = p.team_name)                AS round_won,
       min(r.start_tick)                                   AS round_start_tick,
       min(r.end_tick)                                     AS round_end_tick
FROM kills k
JOIN rounds r  ON r.match_checksum = k.match_checksum AND r.number = k.round_number
JOIN players p ON p.match_checksum = k.match_checksum AND p.steam_id = k.killer_steam_id
WHERE k.match_checksum = %(checksum)s
  AND k.killer_steam_id = %(steamid)s
  AND k.killer_side <> k.victim_side
GROUP BY k.round_number
ORDER BY k.round_number
"""


def connect(conninfo: Mapping[str, object]) -> psycopg.Connection:
    return psycopg.connect(**conninfo, connect_timeout=10)


def find_checksum(conn: psycopg.Connection, demo_name: str) -> str | None:
    """The match checksum CS:DM gave the Demo named `demo_name` (its file name without `.dem`).
    Looked up by name, not path: `csdm analyze` skips a Demo it already knows, so the stored path
    can point at an older copy of the same file."""
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(_FIND_CHECKSUM, {"name": demo_name}).fetchone()
    return row["checksum"] if row else None


def match_info(conn: psycopg.Connection, checksum: str, steamid: str) -> MatchInfo | None:
    """Map, date and final score from the subject's side, or None if the subject is not in the match."""
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(_MATCH_INFO, {"checksum": checksum, "steamid": steamid}).fetchone()
    if row is None:
        return None
    return MatchInfo(
        checksum=row["checksum"],
        map_name=row["map_name"],
        played_at=row["date"],
        team_score=row["team_score"],
        opponent_score=row["opponent_score"],
    )


def round_facts(conn: psycopg.Connection, checksum: str, steamid: str) -> list[RoundFacts]:
    """What the subject did in each round where they got at least one Frag."""
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(_ROUND_FACTS, {"checksum": checksum, "steamid": steamid}).fetchall()
    return [
        RoundFacts(
            round=row["round"],
            frag_ticks=tuple(row["frag_ticks"]),
            headshots=row["headshots"],
            blind_frags=row["blind_frags"],
            knife_frags=row["knife_frags"],
            noscope_frags=row["noscope_frags"],
            smoke_frags=row["smoke_frags"],
            bot_frags=row["bot_frags"],
            round_won=bool(row["round_won"]),
            round_start_tick=row["round_start_tick"],
            round_end_tick=row["round_end_tick"],
        )
        for row in rows
    ]


class CsdmFacts:
    """The three lookups the worker needs, each on its own short-lived connection."""

    def __init__(self, conninfo: Mapping[str, object]):
        self._conninfo = dict(conninfo)

    def find_checksum(self, demo_name: str) -> str | None:
        with connect(self._conninfo) as conn:
            return find_checksum(conn, demo_name)

    def match_info(self, checksum: str, steamid: str) -> MatchInfo | None:
        with connect(self._conninfo) as conn:
            return match_info(conn, checksum, steamid)

    def round_facts(self, checksum: str, steamid: str) -> list[RoundFacts]:
        with connect(self._conninfo) as conn:
            return round_facts(conn, checksum, steamid)
