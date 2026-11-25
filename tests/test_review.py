"""A match as the subject played it: their line for Progress, and the document the app keeps of the match."""

from __future__ import annotations

from clipper import analysis, mistakes, review, stats
from tests.csda_json import ENEMY, MATCH, SUBJECT

NOBODY = "76561190000000099"


def subjects_stats() -> dict:
    return stats.compute(analysis.stats_match(MATCH))["players"][SUBJECT]


def test_the_line_is_the_demos_numbers_with_a_rating_when_faceit_has_no_line():
    line = review.line(subjects_stats())
    assert (line["source"], line["rounds"], line["kills"], line["deaths"], line["assists"]) == ("demo", 3, 4, 1, 0)
    assert (line["opening_kills"], line["opening_deaths"]) == (2, 0)
    assert line["multi_kills"] == {"2": 1, "3": 0, "4": 0, "5": 0}
    # 0.6845 + 0.656 x 4/3 kills - 0.76 x 1/3 deaths + 0.0037 x 33.3 ADR + 0.7217 x 1/3 rounds with 2+ Frags
    assert line["rating"] == 1.67


def test_the_line_leads_with_faceits_numbers_when_the_match_has_them():
    worked = stats.attach_faceit(stats.compute(analysis.stats_match(MATCH)), {SUBJECT: {"nickname": "sub", "stats": {
        "Kills": "21", "Deaths": "15", "Assists": "4", "ADR": "88.5", "Headshots %": "48", "K/D Ratio": "1.4",
        "Double Kills": "3", "Triple Kills": "1", "Quadro Kills": "0", "Penta Kills": "0"}}})
    line = review.line(worked["players"][SUBJECT])
    assert (line["source"], line["kills"], line["adr"], line["hs_pct"], line["kd"]) == ("faceit", 21.0, 88.5, 48.0, 1.4)
    assert line["kast_pct"] == subjects_stats()["kast_pct"]              # FACEIT has no KAST: the demo's stays


def test_there_is_no_line_without_stats():
    assert review.line(None) is None


def test_the_document_has_the_line_the_mistakes_and_the_places():
    place = review.Place("kill", 1, 3100, "T", 10.04, -20.06, 300.0, 400.0, "AK-47", True)
    made = review.document(SUBJECT, analysis.review_match(MATCH), [place], subjects_stats())
    assert (made["version"], made["subject"], made["flashes_kept"]) == (review.VERSION, SUBJECT, True)
    assert made["line"]["kills"] == 4
    assert [m["type"] for m in made["mistakes"]] == [mistakes.TEAM_KILL, mistakes.OWN_DEATH]
    assert made["places"] == [{"kind": "kill", "round": 1, "tick": 3100, "side": "T", "x": 10.0, "y": -20.1,
                               "ox": 300.0, "oy": 400.0, "weapon": "AK-47", "headshot": True}]


def test_a_player_who_was_not_in_the_match_has_no_line_and_no_mistakes():
    made = review.document(NOBODY, analysis.review_match(MATCH), [], None)
    assert (made["line"], made["mistakes"], made["places"]) == (None, [], [])


def test_another_players_mistakes_are_their_own():
    made = review.document(ENEMY, analysis.review_match(MATCH), [], None)
    assert [(m["type"], m["round"]) for m in made["mistakes"]] == [(mistakes.OPENING_DEATH, 3)]   # not round 1's:
    # there the subject's other Frag came first
