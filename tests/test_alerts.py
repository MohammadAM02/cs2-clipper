import json
from datetime import datetime, timedelta, timezone

from clipper.alerts import (StoppedPlaying, expires_at, highlights_text, map_label, qualifies, reminder_due,
                            reminder_text, summary_text)
from clipper.model import FaceitStats

NOW = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)


def row(map_name, counts, finished_at=NOW):
    return {"map": map_name, "highlights": json.dumps({"3k": 0, "4k": 0, "5k": 0} | counts),
            "finished_at": finished_at.isoformat(timespec="seconds")}


def test_a_match_qualifies_with_a_round_of_3_or_more_frags():
    assert qualifies(FaceitStats(triple_kills=1))
    assert qualifies(FaceitStats(penta_kills=1))
    assert not qualifies(FaceitStats(double_kills=5))


def test_highlights_read_biggest_first():
    assert highlights_text({"3k": 2, "4k": 1, "5k": 0}) == "4K + 2× 3K"
    assert highlights_text({"3k": 1, "4k": 0, "5k": 1}) == "Ace + 3K"


def test_map_labels():
    assert [map_label(m) for m in ("de_inferno", "de_dust2", "cs_office")] == ["Inferno", "Dust2", "Office"]


def test_the_summary_lists_three_matches_then_counts_the_rest():
    rows = [row("de_inferno", {"4k": 1, "3k": 2}), row("de_ancient", {"3k": 2}), row("de_nuke", {"3k": 1}),
            row("de_mirage", {"3k": 1}), row("de_anubis", {"5k": 1})]
    assert summary_text(rows[:1]) == ("1 new match has Highlights", "Inferno 4K + 2× 3K")
    assert summary_text(rows[:3]) == ("3 new matches have Highlights",
                                      "Inferno 4K + 2× 3K · Ancient 2× 3K · Nuke 3K")
    assert summary_text(rows) == ("5 new matches have Highlights",
                                  "Inferno 4K + 2× 3K · Ancient 2× 3K · Nuke 3K · +2 more")


def test_the_reminder_names_the_match_or_counts_them():
    finished = NOW - timedelta(days=27)
    assert reminder_text([row("de_overpass", {"5k": 1, "3k": 1}, finished)], NOW) == (
        "Overpass demo expires in 3 days", "Ace + 3K · you haven't grabbed or skipped it")
    two = [row("de_overpass", {"5k": 1}, finished), row("de_nuke", {"3k": 1}, finished)]
    assert reminder_text(two, NOW) == ("2 demos expire in 3 days or less", "Overpass Ace · Nuke 3K")


def test_a_reminder_is_due_in_the_last_3_days_of_the_link():
    assert expires_at(NOW - timedelta(days=30)) == NOW
    assert not reminder_due(NOW - timedelta(days=26, hours=23), NOW)    # 3 days 1 hour left
    assert reminder_due(NOW - timedelta(days=27), NOW)
    assert reminder_due(NOW - timedelta(days=29, hours=23), NOW)
    assert not reminder_due(NOW - timedelta(days=30), NOW)              # expired


def test_stopped_playing_fires_once_5_minutes_after_cs2_closes():
    stopped = StoppedPlaying(5.0)
    assert not stopped.update(False, NOW)                               # CS2 never ran
    assert not stopped.update(True, NOW + timedelta(minutes=1))
    assert not stopped.update(False, NOW + timedelta(minutes=40))       # closed at 20:40
    assert not stopped.update(False, NOW + timedelta(minutes=44))
    assert stopped.update(False, NOW + timedelta(minutes=45))
    assert not stopped.update(False, NOW + timedelta(minutes=50))       # once per session


def test_opening_cs2_again_starts_the_wait_over():
    stopped = StoppedPlaying(5.0)
    stopped.update(True, NOW)
    stopped.update(False, NOW + timedelta(minutes=1))
    stopped.update(True, NOW + timedelta(minutes=4))                    # back in before 5 minutes
    assert not stopped.update(False, NOW + timedelta(minutes=7))
    assert stopped.update(False, NOW + timedelta(minutes=12))
