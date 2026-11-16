from dataclasses import replace

import pytest

from clipper.model import DEATH, DEATH_KEYS, RoundFacts
from clipper.scoring import death_of, score_match, score_round
from tests.fixtures import MATCH_FACTS


def by_round(highlights):
    return {h.round: h for h in highlights}


def test_the_analyzed_match_scores_as_the_old_query_did():
    scored = by_round(score_match(MATCH_FACTS))
    assert len(scored) == 15
    assert sum(len(h.frag_ticks) for h in scored.values()) == 34
    assert (scored[12].type, scored[12].score, scored[12].reasons) == ("4K", 80, ("4k", "blind kill"))
    assert (scored[1].type, scored[1].score, scored[1].reasons) == ("2K", 25, ("2k", "all headshots"))
    assert (scored[3].type, scored[3].score) == ("3K", 40)
    assert (scored[2].type, scored[2].score, scored[2].reasons) == ("FRAG", 5, ("frag",))


def test_bonuses_other_than_the_knife_score_only_with_two_frags():
    scored = by_round(score_match(MATCH_FACTS))
    assert (scored[7].score, scored[7].reasons) == (5, ("frag", "through smoke"))   # one Frag, through smoke
    assert (scored[9].score, scored[9].reasons) == (5, ("frag",))                    # one Frag, a headshot


def test_a_knife_frag_earns_thirty():
    highlight = score_round(RoundFacts(round=7, frag_ticks=(100,), knife_frags=1))
    assert highlight.score == 35
    assert highlight.reasons == ("frag", "knife kill")


def test_an_ace_is_the_top_base_rule():
    highlight = score_round(RoundFacts(round=1, frag_ticks=(1, 2, 3, 4, 5), headshots=5))
    assert (highlight.type, highlight.score) == ("ACE", 110)


def test_a_round_without_frags_is_not_a_highlight():
    assert score_round(RoundFacts(round=1, frag_ticks=())) is None


# --- worth clipping (spec: Pick your rounds, Worth clipping) ---------------------------------------

def test_a_plain_single_frag_is_not_worth_clipping():
    assert score_round(RoundFacts(round=2, frag_ticks=(100,))).worthy is False


def test_two_frags_are_always_worth_clipping():
    assert score_round(RoundFacts(round=2, frag_ticks=(100, 200))).worthy is True


@pytest.mark.parametrize(("facts", "reason"), [
    ({"deagle_frags": 1}, "deagle"),
    ({"knife_frags": 1}, "knife kill"),
    ({"lowest_hp": 20}, "20 HP"),
    ({"noscope_frags": 1}, "no-scope"),
    ({"airborne_frags": 1}, "jump shot"),
    ({"wallbang_frags": 1}, "wallbang"),
    ({"smoke_frags": 1}, "through smoke"),
    ({"blind_frags": 1}, "blind kill"),
    ({"clutch_opponents": 3}, "clutch 1v3"),
])
def test_a_single_frag_with_something_special_is_worth_clipping(facts, reason):
    highlight = score_round(RoundFacts(round=2, frag_ticks=(100,), **facts))
    assert highlight.worthy is True
    assert highlight.reasons == ("frag", reason)


def test_21_hp_is_not_low():
    highlight = score_round(RoundFacts(round=2, frag_ticks=(100,), lowest_hp=21))
    assert (highlight.worthy, highlight.reasons) == (False, ("frag",))


def test_a_won_round_against_one_is_no_clutch():
    highlight = score_round(RoundFacts(round=2, frag_ticks=(100,), clutch_opponents=1))
    assert (highlight.worthy, highlight.reasons) == (False, ("frag",))


def test_the_new_reasons_add_nothing_to_the_score():
    highlight = score_round(RoundFacts(round=2, frag_ticks=(100, 200), deagle_frags=1, airborne_frags=1,
                                       wallbang_frags=1, lowest_hp=5, clutch_opponents=4))
    assert highlight.score == 15
    assert highlight.reasons == ("2k", "clutch 1v4", "5 HP", "deagle", "jump shot", "wallbang")


def test_the_analyzed_match_lists_its_multi_frag_rounds_and_its_single_frag_through_smoke():
    scored = score_match(MATCH_FACTS)
    assert [h.round for h in scored if h.worthy] == [1, 3, 4, 5, 6, 7, 8, 10, 12, 14, 15, 18]
    assert [h.round for h in scored if not h.worthy] == [2, 9, 11]


# --- your deaths (Settings: Clip your deaths) -------------------------------------------------------------------

DIED_ONLY = RoundFacts(round=4, frag_ticks=(), round_start_tick=20000, round_end_tick=27000, death_tick=24000)
FRAGGED_AND_DIED = RoundFacts(round=2, frag_ticks=(100,), round_start_tick=50, round_end_tick=900, death_tick=300)


def test_your_deaths_are_no_highlights_unless_they_are_clipped():
    assert score_match([DIED_ONLY, FRAGGED_AND_DIED]) == [score_round(FRAGGED_AND_DIED)]


def test_a_death_is_a_highlight_of_its_own_worth_clipping_that_scores_nothing():
    [death] = score_match([DIED_ONLY], deaths=True)
    assert (death.round, death.type, death.score, death.reasons, death.frag_ticks) == (4, DEATH, 0, (), ())
    assert (death.round_start_tick, death.round_end_tick, death.worthy) == (20000, 27000, True)
    assert death.key == 4 + DEATH_KEYS          # beside its round's own Highlight in the index


def test_a_round_you_died_in_keeps_its_highlight_as_it_was_and_its_death_follows_it():
    frag, death = score_match([FRAGGED_AND_DIED], deaths=True)
    assert frag == score_round(FRAGGED_AND_DIED)
    assert (frag.key, frag.worthy) == (2, False)            # a plain single Frag, as without deaths
    assert (death.round, death.type, death.key) == (2, DEATH, 2 + DEATH_KEYS)


def test_a_round_you_lived_through_has_no_death():
    lived = RoundFacts(round=2, frag_ticks=(100,))
    assert score_match([lived], deaths=True) == [score_round(lived)]
    assert death_of(RoundFacts(round=3, frag_ticks=())) is None


def test_with_your_deaths_clipped_the_match_lists_each_death_after_its_round_in_round_order():
    died = replace(DIED_ONLY, round=13)                 # MATCH_FACTS has no round 13
    scored = score_match([died, *MATCH_FACTS], deaths=True)
    assert [h.round for h in scored] == sorted(h.round for h in scored)
    assert len(scored) == len(score_match(MATCH_FACTS)) + 1
    assert [h.type for h in scored if h.round == 13] == [DEATH]
