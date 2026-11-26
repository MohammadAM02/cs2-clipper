import pytest

from clipper.model import FaceitStats
from clipper import rating as ratings
from clipper.rating import rating

# 2 × 3K and 1 × 4K are the rounds with 3+ Frags; with 3 × 2K that makes 6 multi-kill rounds.
STRONG = FaceitStats(map_name="de_inferno", team_score=13, opponent_score=9, won=True, rounds=22,
                     kills=24, deaths=15, assists=5, adr=94.0, double_kills=3, triple_kills=2, quadro_kills=1)
AVERAGE = FaceitStats(map_name="de_nuke", team_score=11, opponent_score=13, rounds=24, kills=18, deaths=17,
                      assists=4, adr=79.0, double_kills=2, triple_kills=1)


def test_the_highlights_are_the_rounds_with_3_or_more_frags():
    assert STRONG.highlights == {"3k": 2, "4k": 1, "5k": 0}
    assert STRONG.multi_kill_rounds == 6


def test_rating_matches_faceitperf():
    # The expected values are faceitperf's estimateRating (apps/web/src/features/stats.ts) on the same inputs.
    assert rating(STRONG) == pytest.approx(1.4983620944695974)
    assert rating(AVERAGE) == pytest.approx(1.0733880127650517)


def test_rating_never_goes_below_zero_and_needs_rounds():
    assert rating(FaceitStats(rounds=13, deaths=30)) == 0.0
    assert rating(FaceitStats()) == 0.0


# --- HLTV Rating 2.1, estimated from what the Demo says ----------------------------------------------------------

def test_hltv21_is_the_reverse_engineered_formula():
    # 22 rounds, 18-15-4, 78.5 ADR, 72.7% KAST
    kpr, dpr, apr = 18 / 22, 15 / 22, 4 / 22
    impact = 2.13 * kpr + 0.42 * apr - 0.41
    expected = 0.0073 * 72.7 + 0.3591 * kpr - 0.5329 * dpr + 0.2372 * impact + 0.0032 * 78.5 + 0.1587
    assert ratings.hltv21(18, 15, 4, 78.5, 72.7, 22) == round(expected, 2) == 1.21


def test_hltv21_needs_rounds_and_kast_and_is_never_below_zero():
    assert ratings.hltv21(18, 15, 4, 78.5, None, 22) is None
    assert ratings.hltv21(18, 15, 4, 78.5, 72.7, 0) is None
    assert ratings.hltv21(0, 22, 0, 0.0, 0.0, 22) == 0.0
