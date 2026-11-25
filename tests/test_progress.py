"""Progress: the subject's form over their last matches against the matches before, their maps, their Mistakes by
Type and their records. Pure arithmetic over made-up matches, newest first."""

from __future__ import annotations

from clipper import mistakes, progress
from clipper.progress import Played


def played(n: int, *, result: str = "win", map_: str = "de_mirage", kills: int = 20, deaths: int = 10,
           rating: float = 1.2, adr: float = 80.0, hs: float | None = 50.0, kast: float | None = 70.0,
           opening: tuple[int, int] = (2, 1), made: tuple[str, ...] = (), score: tuple[int, int] = (13, 7),
           flashes_kept: bool = True, utility: int = 100) -> Played:
    line = {"source": "demo", "rounds": sum(score), "kills": kills, "deaths": deaths, "assists": 4, "adr": adr,
            "hs_pct": hs, "kd": round(kills / deaths, 2) if deaths else None, "multi_kills": {}, "kast_pct": kast,
            "opening_kills": opening[0], "opening_deaths": opening[1], "trade_kills": 0, "traded_deaths": 0,
            "utility_damage": utility, "enemies_flashed": 0, "clutch_wins": 0, "clutch_attempts": 0, "rating": rating}
    return Played(checksum=f"{n:x}", map=map_, played_at=f"2026-11-{30 - n:02d}T20:00:00+00:00", result=result,
                  team_score=score[0], opponent_score=score[1], line=line,
                  mistakes=[{"type": kind} for kind in made], flashes_kept=flashes_kept)


def test_the_form_is_the_last_matches_against_the_ones_before():
    matches = [played(0, rating=1.4), played(1, rating=1.2), played(2, rating=1.0), played(3, rating=0.8)]
    form = progress.form(matches, 2)["rating"]
    assert (form["value"], form["previous"], form["delta"], form["higher_is_better"]) == (1.3, 0.9, 0.4, True)
    assert form["series"] == [1.2, 1.4]                                   # the last two, oldest first


def test_with_no_matches_before_there_is_nothing_to_compare_with():
    form = progress.form([played(0), played(1)], 2)
    assert form["rating"]["previous"] is None and form["rating"]["delta"] is None


def test_ratios_are_of_the_totals_not_the_mean_of_each_match():
    matches = [played(0, kills=30, deaths=10, opening=(4, 0)), played(1, kills=3, deaths=3, opening=(0, 4))]
    form = progress.form(matches, 2)
    assert form["kd"]["value"] == 2.54                                    # 33 / 13, not the mean of 3.0 and 1.0
    assert form["opening_win_pct"]["value"] == 50.0
    assert form["opening_win_pct"]["series"] == [0.0, 100.0]


def test_the_win_rate_counts_wins_only_and_its_trend_gives_a_tie_half():
    matches = [played(0, result="win"), played(1, result="tie"), played(2, result="loss"), played(3, result="win")]
    form = progress.form(matches, 4)["win_pct"]
    assert form["value"] == 50.0
    assert form["series"] == [100.0, 0.0, 50.0, 100.0]


def test_mistakes_a_match_is_better_when_it_falls():
    matches = [played(0, made=(mistakes.TEAM_KILL,)), played(1, made=(mistakes.TEAM_KILL,) * 3)]
    form = progress.form(matches, 1)["mistakes"]
    assert (form["value"], form["previous"], form["delta"], form["higher_is_better"]) == (1.0, 3.0, -2.0, False)


def test_a_number_a_match_lacks_is_left_out_of_the_mean_and_is_a_gap_in_the_trend():
    form = progress.form([played(0, hs=None), played(1, hs=40.0)], 2)["hs_pct"]
    assert (form["value"], form["series"]) == (40.0, [40.0, None])


def test_each_map_gets_its_numbers_over_every_match_on_it_the_most_played_first():
    matches = [played(0, map_="de_nuke", result="loss", score=(5, 13)), played(1),
               played(2, result="loss", score=(9, 13)), played(3, result="tie", score=(15, 15))]
    mirage, nuke = progress.by_map(matches)
    assert (mirage["map"], mirage["matches"]) == ("de_mirage", 3)
    assert (mirage["wins"], mirage["losses"], mirage["ties"]) == (1, 1, 1)
    assert mirage["win_pct"] == 33.33
    assert mirage["rounds_won_pct"] == 51.39                              # 37 of 72 rounds
    assert (nuke["map"], nuke["matches"], nuke["win_pct"]) == ("de_nuke", 1, 0.0)


def test_the_mistakes_are_counted_by_type_the_commonest_first_with_its_name_and_tip():
    matches = [played(0, made=(mistakes.TEAM_FLASH, mistakes.OPENING_DEATH)), played(1, made=(mistakes.TEAM_FLASH,))]
    first, second = progress.mistake_types(matches)
    assert (first["type"], first["count"], first["per_match"]) == (mistakes.TEAM_FLASH, 2, 1.0)
    assert first["title"] == mistakes.KINDS[mistakes.TEAM_FLASH]["title"] and first["tip"]
    assert (second["type"], second["count"], second["per_match"]) == (mistakes.OPENING_DEATH, 1, 0.5)
    assert progress.mistake_types([played(0)]) == []


def test_a_record_is_the_best_match_and_the_earliest_of_two_that_tie():
    matches = [played(0, kills=30), played(1, kills=34), played(2, kills=34)]
    kills = next(r for r in progress.records(matches) if r["key"] == "kills")
    assert (kills["value"], kills["checksum"], kills["label"]) == (34, "2", "Most kills")


def test_a_headshot_record_needs_ten_kills_behind_it():
    matches = [played(0, kills=2, hs=100.0), played(1, kills=10, hs=60.0)]
    best = next(r for r in progress.records(matches) if r["key"] == "hs_pct")
    assert (best["value"], best["checksum"]) == (60.0, "1")
    assert all(r["key"] != "hs_pct" for r in progress.records([played(0, kills=2, hs=100.0)]))


def test_the_summary_covers_the_window_asked_for():
    matches = [played(n, flashes_kept=n < 3) for n in range(25)]
    last_ten = progress.summary(matches, 10)
    assert (last_ten["matches"], last_ten["window"], last_ten["asked"]) == (25, 10, 10)
    assert len(last_ten["recent"]) == 10 and last_ten["recent"][0]["checksum"] == "0"
    assert last_ten["without_flashes"] == 7
    assert len(last_ten["maps"]) == 1 and last_ten["maps"][0]["matches"] == 25       # the maps cover every match


def test_all_matches_can_be_asked_for_and_anything_else_is_the_default():
    matches = [played(n) for n in range(25)]
    everything = progress.summary(matches, 0)
    assert (everything["window"], everything["asked"]) == (25, 0)
    assert everything["form"]["rating"]["previous"] is None
    assert progress.summary(matches, 7)["asked"] == progress.DEFAULT_WINDOW
    assert progress.summary(matches, -3)["window"] == progress.DEFAULT_WINDOW


def test_no_matches_is_an_empty_summary_not_an_error():
    empty = progress.summary([], 20)
    assert (empty["matches"], empty["window"], empty["recent"], empty["maps"], empty["records"]) == (0, 0, [], [], [])
    assert empty["form"]["rating"] == {"value": None, "previous": None, "delta": None, "higher_is_better": True,
                                       "series": []}
