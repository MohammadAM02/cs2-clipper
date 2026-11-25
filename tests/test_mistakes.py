"""The subject's Mistakes in a match: each rule on a match made up for it. Pure arithmetic, so no files and no csda."""

from __future__ import annotations

from clipper import mistakes
from clipper.mistakes import Flash, Kill, Match, Round

ME, MATE, MATE2 = "me", "mate", "mate2"
FOE, FOE2, FOE3 = "foe", "foe2", "foe3"
TEAMS = {ME: "A", MATE: "A", MATE2: "A", FOE: "B", FOE2: "B", FOE3: "B"}
TICKRATE = 64.0
ROUNDS = (Round(1, 1000), Round(2, 10_000))


def kill(tick: int, killer: str, victim: str, *, round_: int = 1, weapon: str = "AK-47", blind: bool = False) -> Kill:
    return Kill(tick, round_, killer, victim, weapon, blind)


def found(kills, flashes=(), subject: str = ME, teams=TEAMS):
    return mistakes.find(Match(TICKRATE, dict(teams), ROUNDS, tuple(kills), tuple(flashes)), subject)


def types(kills, flashes=()) -> list[str]:
    return [m.type for m in found(kills, flashes)]


def test_the_first_death_of_the_round_untraded_is_an_opening_death():
    [mistake] = found([kill(2000, FOE, ME), kill(4000, MATE, FOE)])     # the kill back comes 31 s later: no trade
    assert (mistake.type, mistake.round, mistake.tick, mistake.other, mistake.weapon) == (
        mistakes.OPENING_DEATH, 1, 2000, FOE, "AK-47")
    assert mistake.seconds == 15.62                                     # 1000 ticks into the round


def test_an_opening_death_a_teammate_trades_is_no_mistake():
    assert types([kill(2000, FOE, ME), kill(2000 + 5 * 64, MATE, FOE)]) == []          # traded on the last tick


def test_a_trade_must_be_of_the_killer_by_a_teammate_within_the_window():
    assert types([kill(2000, FOE, ME), kill(2100, MATE, FOE2)]) == [mistakes.OPENING_DEATH]      # another enemy
    assert types([kill(2000, FOE, ME), kill(2000 + 5 * 64 + 1, MATE, FOE)]) == [mistakes.OPENING_DEATH]


def test_a_later_death_with_no_numbers_in_hand_is_no_mistake():
    assert types([kill(2000, MATE, FOE), kill(2100, FOE2, ME)]) == []       # 3v2 when they died: one ahead


def test_dying_untraded_two_or_more_ahead_throws_the_advantage():
    [mistake] = found([kill(2000, MATE, FOE), kill(2100, MATE, FOE2), kill(2200, FOE3, ME)])
    assert (mistake.type, mistake.ours, mistake.theirs, mistake.other) == (mistakes.THREW_ADVANTAGE, 3, 1, FOE3)


def test_a_traded_death_while_ahead_is_no_mistake():
    assert types([kill(2000, MATE, FOE), kill(2100, MATE, FOE2), kill(2200, FOE3, ME), kill(2300, MATE, FOE3)]) == []


def test_a_death_while_blind_is_one_mistake_even_when_it_is_also_the_opening_death():
    [mistake] = found([kill(2000, FOE, ME, blind=True)])
    assert (mistake.type, mistake.other, mistake.team_flash) == (mistakes.DIED_BLIND, FOE, False)


def test_a_death_while_blind_from_a_teammates_flash_names_the_teammate():
    flash = Flash(1900, 1, MATE, ME, 3.0)                              # blind until tick 2092
    [mistake] = found([kill(2000, FOE, ME, blind=True)], [flash])
    assert (mistake.type, mistake.other, mistake.team_flash) == (mistakes.DIED_BLIND, MATE, True)


def test_a_flash_that_had_worn_off_or_an_enemys_is_not_blamed_on_the_team():
    worn_off = Flash(1700, 1, MATE, ME, 1.0)
    enemys = Flash(1900, 1, FOE2, ME, 3.0)
    for flash in (worn_off, enemys):
        [mistake] = found([kill(2000, FOE, ME, blind=True)], [flash])
        assert (mistake.other, mistake.team_flash) == (FOE, False)


def test_killing_a_teammate_is_a_mistake_and_being_killed_by_one_is_not():
    assert types([kill(2000, ME, MATE, weapon="HE Grenade")]) == [mistakes.TEAM_KILL]
    assert types([kill(2000, MATE, ME)]) == []


def test_a_team_kill_does_not_open_the_round():
    assert types([kill(2000, MATE2, MATE), kill(2100, FOE, ME)]) == [mistakes.OPENING_DEATH]


def test_the_bomb_and_the_world_are_told_apart():
    [bomb] = found([kill(2000, "", ME, weapon="C4")])
    [fall] = found([kill(2000, "", ME, weapon="World")])
    [own] = found([kill(2000, ME, ME, weapon="Molotov")])
    assert (bomb.type, fall.type, own.type) == (mistakes.BOMB_DEATH, mistakes.OWN_DEATH, mistakes.OWN_DEATH)


def test_a_flashbang_that_blinds_teammates_is_one_mistake_however_many_it_blinds():
    flashes = [Flash(1500, 1, ME, MATE, 2.4), Flash(1502, 1, ME, MATE2, 3.1), Flash(1502, 1, ME, FOE, 4.0)]
    [mistake] = found([], flashes)
    assert (mistake.type, mistake.tick, mistake.other, mistake.blind_s, mistake.blinded) == (
        mistakes.TEAM_FLASH, 1500, MATE2, 3.1, 2)


def test_two_flashbangs_are_two_mistakes_and_a_graze_is_none():
    flashes = [Flash(1500, 1, ME, MATE, 2.4), Flash(1700, 1, ME, MATE, 2.0), Flash(1900, 1, ME, MATE, 1.99)]
    assert [m.tick for m in found([], flashes)] == [1500, 1700]


def test_a_teammates_team_flash_is_not_the_subjects():
    assert found([], [Flash(1500, 1, MATE, MATE2, 3.0), Flash(1500, 1, MATE, ME, 3.0)]) == []


def test_mistakes_come_in_the_order_they_happened_across_rounds():
    kills = [kill(11_000, FOE, ME, round_=2), kill(2000, FOE, ME)]
    flashes = [Flash(1500, 1, ME, MATE, 2.5)]
    assert [(m.round, m.type) for m in found(kills, flashes)] == [
        (1, mistakes.TEAM_FLASH), (1, mistakes.OPENING_DEATH), (2, mistakes.OPENING_DEATH)]


def test_a_kill_in_a_round_with_no_row_and_a_subject_not_in_the_match_give_nothing():
    assert types([kill(50_000, FOE, ME, round_=9)]) == []
    assert found([kill(2000, FOE, ME)], subject="stranger") == []


def test_without_kept_flashes_the_deaths_are_still_judged():
    match = Match(TICKRATE, dict(TEAMS), ROUNDS, (kill(2000, FOE, ME, blind=True),), None)
    [mistake] = mistakes.find(match, ME)
    assert (mistake.type, mistake.team_flash) == (mistakes.DIED_BLIND, False)


def test_a_mistake_is_written_with_only_the_fields_its_type_uses():
    [threw] = found([kill(2000, MATE, FOE), kill(2100, MATE, FOE2), kill(2200, FOE3, ME)])
    assert threw.as_dict() == {"type": mistakes.THREW_ADVANTAGE, "round": 1, "tick": 2200, "t": 18.75, "other": FOE3,
                               "weapon": "AK-47", "ours": 3, "theirs": 1}
    [flash] = found([], [Flash(1500, 1, ME, MATE, 2.5)])
    assert flash.as_dict() == {"type": mistakes.TEAM_FLASH, "round": 1, "tick": 1500, "t": 7.81, "other": MATE,
                               "blind_s": 2.5, "blinded": 1}


def test_the_tally_counts_each_type_in_the_pages_order():
    listed = [{"type": mistakes.TEAM_KILL}, {"type": mistakes.OPENING_DEATH}, {"type": mistakes.TEAM_KILL}]
    assert list(mistakes.tally(listed).items()) == [(mistakes.OPENING_DEATH, 1), (mistakes.TEAM_KILL, 2)]


def test_every_type_has_a_name_and_a_tip():
    for kind in (mistakes.OPENING_DEATH, mistakes.DIED_BLIND, mistakes.THREW_ADVANTAGE, mistakes.TEAM_FLASH,
                 mistakes.TEAM_KILL, mistakes.BOMB_DEATH, mistakes.OWN_DEATH):
        assert mistakes.KINDS[kind]["title"] and mistakes.KINDS[kind]["tip"], kind
