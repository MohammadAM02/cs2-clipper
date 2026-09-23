from datetime import datetime, timezone

import psycopg
import pytest

from clipper import csdm_db
from clipper.config import load_config
from tests.fixtures import DEMO_NAME, MATCH_CHECKSUM, MATCH_FACTS, SUBJECT

pytestmark = pytest.mark.integration


@pytest.fixture
def conn():
    try:
        connection = psycopg.connect(**load_config().database_conninfo(), connect_timeout=5, autocommit=True)
    except (OSError, KeyError, psycopg.OperationalError) as exc:
        pytest.skip(f"CS:DM's Postgres is not reachable: {exc}")
    with connection:
        yield connection


def test_the_checksum_is_found_by_demo_name(conn):
    assert csdm_db.find_checksum(conn, DEMO_NAME) == MATCH_CHECKSUM
    assert csdm_db.find_checksum(conn, "1-00000000-0000-0000-0000-000000000000-1-1") is None


def test_match_info_is_from_the_subjects_side(conn):
    info = csdm_db.match_info(conn, MATCH_CHECKSUM, SUBJECT)
    assert info.map_name == "de_inferno"
    assert (info.team_score, info.opponent_score, info.result) == (13, 5, "win")
    assert info.played_at == datetime(2026, 9, 22, 9, 39, 14, tzinfo=timezone.utc)


def test_match_info_is_none_when_the_subject_did_not_play(conn):
    assert csdm_db.match_info(conn, MATCH_CHECKSUM, "76561190000000000") is None


def test_round_facts_match_the_fixture(conn):
    assert csdm_db.round_facts(conn, MATCH_CHECKSUM, SUBJECT) == list(MATCH_FACTS)


def test_round_facts_stay_within_one_match(conn):   # issue 08
    """A second match with the same round numbers must not leak into the first match's facts.
    Temp tables shadow the real ones for this transaction only; the rollback removes them."""
    with conn.transaction(force_rollback=True):
        conn.execute("SET LOCAL search_path = pg_temp, public")
        for table in ("kills", "rounds", "players"):
            conn.execute(f"CREATE TEMP TABLE {table} ON COMMIT DROP AS SELECT * FROM public.{table}")
            conn.execute(f"UPDATE {table} SET match_checksum = 'second-match'")
            conn.execute(f"INSERT INTO {table} SELECT * FROM public.{table}")
        assert csdm_db.round_facts(conn, MATCH_CHECKSUM, SUBJECT) == list(MATCH_FACTS)
        assert csdm_db.round_facts(conn, "second-match", SUBJECT) == list(MATCH_FACTS)
