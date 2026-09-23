from clipper.model import RoundFacts
from clipper.scoring import score_match, score_round, select
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


def test_bonuses_other_than_the_knife_need_two_frags():
    scored = by_round(score_match(MATCH_FACTS))
    assert (scored[7].score, scored[7].reasons) == (5, ("frag",))   # one Frag, through smoke
    assert scored[9].score == 5                                      # one Frag, a headshot


def test_a_knife_frag_earns_thirty():   # issue 09
    highlight = score_round(RoundFacts(round=7, frag_ticks=(100,), knife_frags=1))
    assert highlight.score == 35
    assert highlight.reasons == ("frag", "knife kill")


def test_an_ace_is_the_top_base_rule():
    highlight = score_round(RoundFacts(round=1, frag_ticks=(1, 2, 3, 4, 5), headshots=5))
    assert (highlight.type, highlight.score) == ("ACE", 110)


def test_a_round_without_frags_is_not_a_highlight():
    assert score_round(RoundFacts(round=1, frag_ticks=())) is None


def test_the_top_five_break_ties_by_the_earlier_round():
    assert [h.round for h in select(score_match(MATCH_FACTS), 5)] == [12, 3, 4, 8, 14]


def test_fewer_highlights_than_n_returns_them_all():
    assert len(select(score_match(MATCH_FACTS[:2]), 5)) == 2
