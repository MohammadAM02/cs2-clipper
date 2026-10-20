"""The per-match stats, worked out by hand on a 2v2 match of three rounds (64 ticks a second, so a trade's window is
320 ticks). Each expected number below is worked out from the events in this file, not read from the code."""

from __future__ import annotations

import pytest

from clipper import stats
from clipper.stats import Bomb, Clutch, Damage, Flash, Kill, Match, Player, Round

A, B = "alpha", "bravo"
TICKRATE = 64.0
PLAYERS = (Player("1", "a1", A), Player("2", "a2", A), Player("3", "b1", B), Player("4", "b2", B))
ROUNDS = (
    Round(1, 0, 6400, B, "bomb_exploded"),
    Round(2, 7000, 13000, A, "bomb_defused"),
    Round(3, 14000, 20000, A, "enemies_eliminated"),
)
KILLS = (
    Kill(1000, 1, "1", "3", "", "AK-47", True, False),     # a1 kills b1 with a headshot: the opening kill of round 1
    Kill(1200, 1, "4", "2", "", "AK-47", False, False),    # b2 kills a2
    Kill(1400, 1, "1", "4", "", "AK-47", False, False),    # a1 kills b2 200 ticks later: a trade for a1, a2 is traded
    Kill(7500, 2, "3", "1", "4", "AK-47", False, True),    # b1 kills a1, assisted by b2 with a flash
    Kill(9000, 2, "2", "3", "", "AK-47", False, False),    # a2 kills b1 1500 ticks later: too late to trade a1
    Kill(14500, 3, "1", "3", "", "Knife", False, False),   # a1 knifes b1: the opening kill of round 3
    Kill(14600, 3, "1", "4", "", "AK-47", False, False),   # a1 kills b2: two Frags in round 3
)
DAMAGES = (
    Damage(1, "1", "3", 80, "AK-47"),
    Damage(1, "1", "4", 35, "HE Grenade"),         # utility damage
    Damage(1, "1", "2", 20, "AK-47"),              # team damage: not in ADR
    Damage(1, "4", "2", 100, "AK-47"),
    Damage(2, "3", "1", 100, "AK-47"),
    Damage(2, "2", "3", 60, "Molotov"),            # utility damage
    Damage(3, "1", "3", 100, "AK-47"),
    Damage(3, "1", "4", 100, "AK-47"),
    Damage(3, "", "1", 5, "World"),                # the world does not count
)
FLASHES = (
    Flash(7100, 2, "2", "1", 1.0),                 # a2 blinds a1: a team flash
    Flash(7300, 2, "4", "1", 2.5),                 # b2 blinds a1
    Flash(14200, 3, "1", "3", 3.0),                # a1 blinds b1
    Flash(14250, 3, "1", "4", 1.0),                # and b2
)
BOMBS = (
    Bomb(2000, 1, "planted", "1", "A"),
    Bomb(6300, 1, "exploded", "", "A"),
    Bomb(10000, 2, "planted", "3", "B"),
    Bomb(12000, 2, "defused", "2", "B"),
)
CLUTCHES = (Clutch(3, "1", 2, True), Clutch(2, "4", 1, False))


def match(**changes) -> Match:
    fields = dict(checksum="0123456789abcdef", tickrate=TICKRATE, players=PLAYERS, rounds=ROUNDS, kills=KILLS,
                  damages=DAMAGES, flashes=FLASHES, bombs=BOMBS, clutches=CLUTCHES)
    fields.update(changes)
    return Match(**fields)


@pytest.fixture
def result() -> dict:
    return stats.compute(match())


def check(player: dict, **expected) -> None:
    for key, value in expected.items():
        assert player[key] == value, key


# --- the numbers, by hand -------------------------------------------------------------------------------------

def test_a1_scorecard_by_hand(result):
    check(result["players"]["1"], rounds=3, kills=4, deaths=1, assists=0, headshot_kills=1, hs_pct=25.0,
          damage=315, adr=105.0, utility_damage=35, kast_pct=66.7, opening_kills=2, opening_deaths=1,
          opening_win_pct=66.7, trade_kills=1, traded_deaths=0, enemies_flashed=2, enemy_blind_avg_s=2.0,
          flash_assists=0, team_flashes=0, clutch_attempts=1, clutch_wins=1,
          multi_kill_rounds={"2": 2, "3": 0, "4": 0, "5": 0}, bomb_plants=1, bomb_defuses=0)


def test_a2_scorecard_by_hand(result):
    check(result["players"]["2"], kills=1, deaths=1, assists=0, hs_pct=0.0, damage=60, adr=20.0, utility_damage=60,
          kast_pct=100.0, opening_kills=0, opening_deaths=0, opening_win_pct=None, trade_kills=0, traded_deaths=1,
          enemies_flashed=0, enemy_blind_avg_s=None, team_flashes=1, clutch_attempts=0, bomb_defuses=1,
          multi_kill_rounds={"2": 0, "3": 0, "4": 0, "5": 0})


def test_b1_scorecard_by_hand(result):
    check(result["players"]["3"], kills=1, deaths=3, assists=0, hs_pct=0.0, damage=100, adr=33.3, utility_damage=0,
          kast_pct=33.3, opening_kills=1, opening_deaths=2, opening_win_pct=33.3, trade_kills=0, traded_deaths=0,
          enemies_flashed=0, bomb_plants=1)


def test_b2_scorecard_by_hand(result):
    check(result["players"]["4"], kills=1, deaths=2, assists=1, flash_assists=1, hs_pct=0.0, damage=100, adr=33.3,
          kast_pct=66.7, opening_kills=0, opening_deaths=0, opening_win_pct=None, trade_kills=0, traded_deaths=0,
          enemies_flashed=1, enemy_blind_avg_s=2.5, clutch_attempts=1, clutch_wins=0)


def test_every_player_is_there_with_their_team_and_name(result):
    assert set(result["players"]) == {"1", "2", "3", "4"}
    assert result["players"]["3"]["name"] == "b1"
    assert result["players"]["3"]["team"] == B
    assert result["rounds_played"] == 3


# --- the rules --------------------------------------------------------------------------------------------------

def test_a_trade_counts_when_the_kill_comes_within_five_seconds():
    window = 320                                                  # 5 s at 64 ticks a second
    base = [Kill(1000, 1, "4", "2", "", "AK-47", False, False)]   # b2 kills a2
    within = match(kills=tuple(base + [Kill(1000 + window, 1, "1", "4", "", "AK-47", False, False)]))
    beyond = match(kills=tuple(base + [Kill(1000 + window + 1, 1, "1", "4", "", "AK-47", False, False)]))
    assert stats.compute(within)["players"]["1"]["trade_kills"] == 1
    assert stats.compute(within)["players"]["2"]["traded_deaths"] == 1
    assert stats.compute(beyond)["players"]["1"]["trade_kills"] == 0
    assert stats.compute(beyond)["players"]["2"]["traded_deaths"] == 0


def test_a_team_kill_is_not_a_kill_but_is_a_death():
    team_kill = match(kills=(*KILLS, Kill(14800, 3, "2", "1", "", "AK-47", False, False)))
    result = stats.compute(team_kill)
    check(result["players"]["2"], kills=1)
    check(result["players"]["1"], deaths=2, kills=4)


def test_a_suicide_is_a_death_and_not_a_kill():
    suicide = match(kills=(Kill(1000, 1, "3", "3", "", "World", False, False),), damages=(), flashes=(), bombs=(),
                    clutches=())
    check(stats.compute(suicide)["players"]["3"], deaths=1, kills=0)


def test_a_round_with_no_row_still_counts_frags_but_not_rounds():
    extra = match(kills=(*KILLS, Kill(30000, 4, "1", "4", "", "AK-47", False, False)))
    check(stats.compute(extra)["players"]["1"], kills=5, rounds=3)


def test_with_no_rounds_the_rates_are_empty_not_zero():
    result = stats.compute(match(rounds=(), kills=(), damages=(), flashes=(), bombs=(), clutches=()))
    check(result["players"]["1"], kills=0, deaths=0, adr=0.0, kast_pct=None, hs_pct=None, opening_win_pct=None)
    assert result["rounds"] == []


def test_a_player_with_no_deaths_has_no_kd():
    line = stats.headline({"kills": 3, "deaths": 0, "assists": 1, "adr": 40.0, "hs_pct": 33.3})
    assert (line["source"], line["kd"]) == ("demo", None)


def test_the_timeline_is_in_round_order_with_each_kill_and_bomb_event(result):
    first = result["rounds"][0]
    assert (first["number"], first["winner"], first["end_reason"]) == (1, B, "bomb_exploded")
    assert [event["type"] for event in first["events"]] == \
        ["kill", "kill", "kill", "bomb_planted", "bomb_exploded"]
    headshot, _, trade = first["events"][0], first["events"][1], first["events"][2]
    assert (headshot["killer"], headshot["victim"], headshot["weapon"], headshot["headshot"]) == \
        ("1", "3", "AK-47", True)
    assert trade["trade"] is True and trade["flash_assist"] is False
    assert first["events"][3]["player"] == "1" and first["events"][4]["player"] is None
    assert headshot["t"] == pytest.approx(1000 / TICKRATE, abs=0.01)


def test_a_kill_with_no_killer_is_a_world_kill_in_the_timeline():
    world = match(kills=(Kill(1000, 1, "", "3", "", "World", False, False),))
    kill = stats.compute(world)["rounds"][0]["events"][0]
    assert kill["killer"] is None and kill["victim"] == "3"


# --- FACEIT's line and the headline ----------------------------------------------------------------------------

FACEIT_LINE = {"nickname": "a1-faceit", "stats": {
    "Kills": "18", "Deaths": "21", "Assists": "7", "ADR": "65.8", "Headshots %": "44", "K/D Ratio": "0.86",
    "Headshots": "8", "Enemies Flashed": "12", "Utility Damage": "150", "Penta Kills": "0"}}


def test_faceit_lines_attach_to_the_player_with_the_same_steam_id(result):
    out = stats.attach_faceit(result, {"1": FACEIT_LINE})
    faceit = out["players"]["1"]["faceit"]
    assert (faceit["nickname"], faceit["kills"], faceit["deaths"], faceit["adr"], faceit["hs_pct"]) == \
        ("a1-faceit", 18.0, 21.0, 65.8, 44.0)
    assert faceit["quadro_kills"] is None                         # not in the line: None, not 0
    assert "faceit" not in result["players"]["1"]                 # the input is left as it was


def test_a_line_for_nobody_in_the_match_is_left_out(result):
    out = stats.attach_faceit(result, {"99": FACEIT_LINE})
    assert set(out["players"]) == {"1", "2", "3", "4"}
    assert all("faceit" not in player for player in out["players"].values())


def test_the_headline_is_faceits_when_the_match_has_it_and_the_demos_otherwise(result):
    with_faceit = stats.headline(stats.attach_faceit(result, {"1": FACEIT_LINE})["players"]["1"])
    assert (with_faceit["source"], with_faceit["kills"], with_faceit["hs_pct"]) == ("faceit", 18.0, 44.0)
    without = stats.headline(result["players"]["1"])
    assert (without["source"], without["kills"], without["deaths"], without["kd"]) == ("demo", 4, 1, 4.0)
    assert stats.headline(result["players"]["3"])["kd"] == round(1 / 3, 2)


# --- the comparison ---------------------------------------------------------------------------------------------

def test_the_comparison_is_this_match_against_the_average_of_the_earlier_ones():
    now = {"kills": 18, "deaths": 16, "assists": 3, "adr": 80.0, "hs_pct": 40.0}
    earlier = [{"kills": 10, "deaths": 20, "assists": 2, "adr": 60.0, "hs_pct": None},
               {"kills": 14, "deaths": 12, "assists": 4, "adr": 70.0, "hs_pct": 30.0}]
    out = stats.comparison(now, earlier)
    assert out["count"] == 2
    assert out["averages"]["kills"] == 12.0 and out["deltas"]["kills"] == 6.0
    assert out["averages"]["deaths"] == 16.0 and out["deltas"]["deaths"] == 0.0
    assert out["averages"]["hs_pct"] == 30.0 and out["deltas"]["hs_pct"] == 10.0


def test_the_comparison_with_no_earlier_matches_has_no_averages():
    out = stats.comparison({"kills": 5, "deaths": 2, "assists": 1, "adr": 50.0, "hs_pct": 20.0}, [])
    assert out["count"] == 0
    assert all(value is None for value in out["averages"].values())
    assert all(value is None for value in out["deltas"].values())
