from clipper.breaks import talking, windows
from clipper.hlae_plan import Round
from clipper.model import Break


def rounds(count: int) -> list[Round]:
    """Round n's freeze time ends at tick n*1000 + 100 and the round ends at n*1000 + 900."""
    return [Round(number, end_tick=number * 1000 + 900, freeze_time_end_tick=number * 1000 + 100)
            for number in range(1, count + 1)]


def test_a_match_without_overtime_has_its_halftime():
    assert windows(rounds(20), max_rounds=24) == [Break(12, 12900, 13100, "HALFTIME")]


def test_overtime_has_a_break_before_each_overtime_and_at_its_half():
    assert windows(rounds(34), max_rounds=24) == [
        Break(12, 12900, 13100, "HALFTIME"),
        Break(24, 24900, 25100, "OT 1"),
        Break(27, 27900, 28100, "OT 1 HALF"),
        Break(30, 30900, 31100, "OT 2"),
        Break(33, 33900, 34100, "OT 2 HALF"),
    ]


def test_the_half_follows_the_match_length():
    assert windows(rounds(17), max_rounds=16) == [Break(8, 8900, 9100, "HALFTIME"), Break(16, 16900, 17100, "OT 1")]


def test_a_match_over_before_the_next_round_has_no_break_there():
    assert windows(rounds(12), max_rounds=24) == []
    assert windows(rounds(27), max_rounds=24) == [Break(12, 12900, 13100, "HALFTIME"), Break(24, 24900, 25100, "OT 1")]


def test_a_break_where_someone_talks_a_second_is_kept():
    half = Break(12, 12900, 13100, "HALFTIME")
    assert talking([half], [12900] * 60 + [13100] * 40) == [half]


def test_a_break_with_less_talk_than_that_is_dropped():
    half = Break(12, 12900, 13100, "HALFTIME")
    assert talking([half], [12000] * 500 + [12950] * 99 + [13101] * 500) == []


def test_each_break_counts_its_own_talk():
    half, overtime = Break(12, 12900, 13100, "HALFTIME"), Break(24, 24900, 25100, "OT 1")
    assert talking([half, overtime], [25000] * 100) == [overtime]
