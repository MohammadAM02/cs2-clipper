import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clipper import analysis, stats
from clipper.hlae_plan import Kill, Player, Round
from clipper.model import MatchInfo, RoundFacts
from tests.csda_json import (ALLY, BRAVO, CHECKSUM, CT, DEMO_NAME, ENEMY, MATCH, OTHER_ENEMY, SUBJECT, T, kill,
                             match, player, round_)

NOBODY = "76561190000000099"


# --- what the app reads from csda's JSON ------------------------------------------------------------------

def test_match_info_is_from_the_subjects_side():
    played_at = datetime(2026, 10, 3, 17, 54, 5, tzinfo=timezone.utc)
    assert analysis.match_info(MATCH, SUBJECT) == MatchInfo(CHECKSUM, "de_mirage", played_at, 13, 9)
    assert analysis.match_info(MATCH, ENEMY) == MatchInfo(CHECKSUM, "de_mirage", played_at, 9, 13)


def test_the_match_date_is_in_local_time_to_the_second():
    """The index sorts matches by the date's text, and every earlier date in it is in this PC's time zone,
    without a fraction of a second."""
    info = analysis.match_info(match(date="2026-10-03T01:00:00.9999999+04:00"), SUBJECT)
    assert info.played_at.isoformat() == datetime(2026, 10, 2, 21, 0, 0, tzinfo=timezone.utc).astimezone().isoformat()


def test_match_info_is_none_when_the_subject_did_not_play():
    assert analysis.match_info(MATCH, NOBODY) is None
    assert analysis.match_info(MATCH, "") is None


def test_match_info_is_none_when_the_subjects_team_is_neither_team():
    players = dict(MATCH["players"], **{SUBJECT: player(SUBJECT, "subject", 3, "team_Charlie")})
    assert analysis.match_info(match(players=players), SUBJECT) is None


def test_round_facts_are_the_subjects_frags_per_round():
    """No hit on the subject in MATCH: they make every Frag on 100 HP. Round 3 is a Clutch they won against two."""
    assert analysis.round_facts(MATCH, SUBJECT) == [
        RoundFacts(round=1, frag_ticks=(3000, 3100), headshots=1, noscope_frags=1, smoke_frags=1, round_won=True,
                   round_start_tick=1000, round_end_tick=9000, lowest_hp=100),
        RoundFacts(round=3, frag_ticks=(20000,), blind_frags=1, knife_frags=1, bot_frags=1, round_won=True,
                   round_start_tick=18500, round_end_tick=27000, lowest_hp=100, clutch_opponents=2),
    ]


def test_round_facts_count_desert_eagle_jump_and_wallbang_frags():
    kills = [kill(3000, 1, SUBJECT, OTHER_ENEMY, weaponName="Desert Eagle", weaponType="pistol"),
             kill(3100, 1, SUBJECT, ENEMY, is_killer_airborne=True, penetratedObjects=2),
             kill(5000, 1, ENEMY, SUBJECT, killer_side=CT, victim_side=T, weaponName="Desert Eagle",
                  is_killer_airborne=True, penetratedObjects=1)]               # the enemy's, not the subject's
    facts = analysis.round_facts(match(kills=kills), SUBJECT)[0]
    assert (facts.deagle_frags, facts.airborne_frags, facts.wallbang_frags) == (1, 1, 1)


def test_the_subjects_hp_at_a_frag_is_100_less_the_damage_they_took_before_it_that_round():
    """60 before the first Frag, 25 more before the second: 40 HP then 15. Round 2's hit is another round's."""
    hits = [{"tick": 2500, "roundNumber": 1, "victimSteamId": int(SUBJECT), "healthDamage": 60},
            {"tick": 3050, "roundNumber": 1, "victimSteamId": int(SUBJECT), "healthDamage": 25},
            {"tick": 3050, "roundNumber": 1, "victimSteamId": int(ENEMY), "healthDamage": 99},
            {"tick": 9600, "roundNumber": 2, "victimSteamId": int(SUBJECT), "healthDamage": 90}]
    assert analysis.round_facts(match(damages=hits), SUBJECT)[0].lowest_hp == 15


def test_a_hit_in_the_same_tick_as_the_frag_counts():
    hits = [{"tick": 3000, "roundNumber": 1, "victimSteamId": int(SUBJECT), "healthDamage": 85}]
    assert analysis.round_facts(match(damages=hits), SUBJECT)[0].lowest_hp == 15


def test_a_frag_made_in_the_tick_the_subject_dies_is_on_1_hp_not_less():
    hits = [{"tick": 3100, "roundNumber": 1, "victimSteamId": int(SUBJECT), "healthDamage": 100},
            {"tick": 3100, "roundNumber": 1, "victimSteamId": int(SUBJECT), "healthDamage": 27}]
    assert analysis.round_facts(match(damages=hits), SUBJECT)[0].lowest_hp == 1


def test_an_analysis_that_kept_no_hits_has_no_hp():
    """Every match analyzed before the app kept hits."""
    old = match()
    del old["damages"]
    assert [facts.lowest_hp for facts in analysis.round_facts(old, SUBJECT)] == [None, None]


def test_a_clutch_is_the_subjects_only_when_they_won_it():
    clutches = [{"roundNumber": 1, "clutcherSteamId": int(SUBJECT), "opponentCount": 3, "hasWon": False},
                {"roundNumber": 3, "clutcherSteamId": int(ALLY), "opponentCount": 2, "hasWon": True}]
    facts = analysis.round_facts(match(clutches=clutches), SUBJECT)
    assert [f.clutch_opponents for f in facts] == [0, 0]


def test_team_kills_suicides_and_kills_in_a_round_without_a_row_are_not_frags():
    """Round 2's only kill by the subject is of a teammate, and round 4 has no row in csda's rounds."""
    assert [facts.round for facts in analysis.round_facts(MATCH, SUBJECT)] == [1, 3]
    suicide = kill(12000, 2, SUBJECT, SUBJECT, victim_side=2)
    assert [f.round for f in analysis.round_facts(match(kills=[*MATCH["kills"], suicide]), SUBJECT)] == [1, 3]


def test_a_round_is_won_when_its_winner_is_the_subjects_team():
    rounds = [round_(1, 1000, 2000, 9000, BRAVO), *MATCH["rounds"][1:]]
    facts = analysis.round_facts(match(rounds=rounds), SUBJECT)
    assert [(f.round, f.round_won) for f in facts] == [(1, False), (3, True)]


def test_there_are_no_round_facts_for_someone_not_in_the_match():
    lone = kill(5000, 1, NOBODY, ENEMY)
    assert analysis.round_facts(match(kills=[lone]), NOBODY) == []
    assert analysis.round_facts(MATCH, "") == []


def test_render_inputs_are_what_the_hlae_plan_reads():
    inputs = analysis.render_inputs(MATCH)
    assert (inputs.tickrate, inputs.tick_count) == (64.0, 40_000)
    assert inputs.kills == (
        Kill(3000, 1, SUBJECT, OTHER_ENEMY),        # ordered by tick, keeping csda's order within a tick
        Kill(3000, 1, ENEMY, ALLY),
        Kill(3100, 1, SUBJECT, ENEMY),
        Kill(11000, 2, SUBJECT, ALLY),
        Kill(11500, 2, "", SUBJECT),                 # the world is nobody
        Kill(20000, 3, SUBJECT, ENEMY),
        Kill(30000, 4, SUBJECT, OTHER_ENEMY),
    )
    assert inputs.rounds == (Round(1, 9000, 2000), Round(2, 18000, 10500), Round(3, 27000, 19500))


def test_render_inputs_give_each_player_the_slot_spec_player_takes():
    """`spec_player` counts from 1 where csda's userId counts from 0. Players come by name."""
    assert analysis.render_inputs(MATCH).players == (
        Player(ALLY, "Ally", 1),
        Player(OTHER_ENEMY, "Bravo Two", 2),
        Player(ENEMY, "enemy", 8),
        Player(SUBJECT, "subject", 4),
    )


def test_render_inputs_are_ordered_whatever_order_csda_wrote_them_in():
    shuffled = match(kills=list(reversed(MATCH["kills"])), rounds=list(reversed(MATCH["rounds"])))
    inputs = analysis.render_inputs(shuffled)
    assert [k.tick for k in inputs.kills] == [3000, 3000, 3100, 11000, 11500, 20000, 30000]
    assert [r.number for r in inputs.rounds] == [1, 2, 3]


def test_the_kept_copy_drops_what_the_app_never_reads():
    kept = analysis.trim(MATCH)
    assert {"playerPositions", "grenadePositions", "shots"}.isdisjoint(kept)
    for key in ("checksum", "date", "mapName", "tickrate", "tickCount", "demoFileName", "teamA", "teamB",
                "players", "rounds", "kills", "clutches"):
        assert kept[key] == MATCH[key]


def test_the_kept_copy_keeps_each_hit_with_only_what_hp_is_worked_out_from():
    assert analysis.trim(MATCH)["damages"] == [
        {"tick": 3000, "roundNumber": 1, "victimSteamId": int(ENEMY), "healthDamage": 100}]


def test_has_hits_says_whether_the_kept_analysis_has_them(tmp_path, dem):
    old = match()
    del old["damages"]
    store = analysis.Analyses(tmp_path / "analyses", tmp_path / "csda.exe", run=FakeCsda(old))
    store.analyze(dem, tmp_path / "analyze.log")
    assert store.has_hits(CHECKSUM) is False
    analysis.Analyses(tmp_path / "analyses", tmp_path / "csda.exe", run=FakeCsda()).analyze(dem, tmp_path / "a.log")
    assert store.has_hits(CHECKSUM) is True


# --- running csda and keeping its analysis ----------------------------------------------------------------

class FakeCsda:
    """Writes `data` where csda writes its JSON: <output>/<the Demo's file name without .dem>.json."""

    def __init__(self, data=MATCH, *, writes=True, exit_code=0):
        self.data, self.writes, self.exit_code = data, writes, exit_code
        self.commands = []

    def __call__(self, command, log_path):
        self.commands.append(command)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text("csda says hello\n", encoding="utf-8")
        if self.writes:
            demo = Path(command[command.index("-demo-path") + 1])
            out = Path(command[command.index("-output") + 1]) / f"{demo.stem}.json"
            out.write_text(json.dumps(self.data), encoding="utf-8")
        return self.exit_code


@pytest.fixture
def dem(tmp_path):
    path = tmp_path / "demos" / f"{DEMO_NAME}.dem"
    path.parent.mkdir()
    path.write_bytes(b"HL2DEMO")
    return path


def test_analyze_runs_csda_and_keeps_its_analysis_by_checksum(tmp_path, dem):
    csda = FakeCsda()
    store = analysis.Analyses(tmp_path / "analyses", tmp_path / "tools" / "csda.exe", run=csda)
    assert store.analyze(dem, tmp_path / "logs" / "analyze.log") == CHECKSUM
    [command] = csda.commands
    output = command[command.index("-output") + 1]
    assert command == [str(tmp_path / "tools" / "csda.exe"), "-demo-path", str(dem), "-output", output,
                       "-format", "json", "-minify", "-source", "faceit"]
    saved = tmp_path / "analyses" / f"{CHECKSUM}.json"
    assert json.loads(saved.read_text(encoding="utf-8")) == analysis.trim(MATCH)
    assert sorted(p.name for p in (tmp_path / "analyses").iterdir()) == \
        [f"{CHECKSUM}.json", f"{CHECKSUM}.stats.json"]          # csda's own file is gone
    assert store.has(CHECKSUM)


def test_analyze_fails_when_csda_writes_nothing(tmp_path, dem):
    store = analysis.Analyses(tmp_path / "analyses", tmp_path / "csda.exe", run=FakeCsda(writes=False, exit_code=1))
    with pytest.raises(RuntimeError, match=r"csda wrote no analysis \(exit code 1\)"):
        store.analyze(dem, tmp_path / "analyze.log")
    assert list((tmp_path / "analyses").iterdir()) == []


def test_analyze_refuses_a_checksum_that_is_not_one(tmp_path, dem):
    store = analysis.Analyses(tmp_path / "analyses", tmp_path / "csda.exe", run=FakeCsda(match(checksum="../x")))
    with pytest.raises(RuntimeError, match="not a checksum"):
        store.analyze(dem, tmp_path / "analyze.log")
    assert list((tmp_path / "analyses").iterdir()) == []


def test_the_store_reads_the_kept_analysis(tmp_path, dem):
    store = analysis.Analyses(tmp_path / "analyses", tmp_path / "csda.exe", run=FakeCsda())
    store.analyze(dem, tmp_path / "analyze.log")
    assert store.match_info(CHECKSUM, SUBJECT) == analysis.match_info(MATCH, SUBJECT)
    assert store.round_facts(CHECKSUM, SUBJECT) == analysis.round_facts(MATCH, SUBJECT)
    assert store.render_inputs(CHECKSUM) == analysis.render_inputs(MATCH)


def test_a_match_without_an_analysis(tmp_path):
    store = analysis.Analyses(tmp_path / "analyses", tmp_path / "csda.exe", run=FakeCsda())
    assert not store.has("fedcba9876543210")
    assert store.render_inputs("fedcba9876543210") is None
    with pytest.raises(analysis.MissingAnalysis, match="no analysis of match fedcba9876543210"):
        store.round_facts("fedcba9876543210", SUBJECT)
    with pytest.raises(analysis.MissingAnalysis):
        store.match_info("fedcba9876543210", SUBJECT)


def test_forget_removes_a_kept_analysis_and_its_stats(tmp_path, dem):
    store = analysis.Analyses(tmp_path / "analyses", tmp_path / "csda.exe", run=FakeCsda())
    store.analyze(dem, tmp_path / "analyze.log")
    assert store.has_stats(CHECKSUM)
    store.forget(CHECKSUM)
    store.forget(CHECKSUM)                           # already gone: nothing to do
    assert not store.has(CHECKSUM)
    assert not store.has_stats(CHECKSUM)


def test_analyze_works_out_the_match_stats_from_csdas_full_output(tmp_path, dem):
    store = analysis.Analyses(tmp_path / "analyses", tmp_path / "csda.exe", run=FakeCsda())
    store.analyze(dem, tmp_path / "analyze.log")
    assert store.stats(CHECKSUM) == json.loads(json.dumps(stats.compute(analysis.stats_match(MATCH))))


def test_a_stats_failure_keeps_the_analysis(tmp_path, dem):
    broken = match(damages=[{"tick": 1}])            # a damage with none of the fields the stats read
    store = analysis.Analyses(tmp_path / "analyses", tmp_path / "csda.exe", run=FakeCsda(broken))
    assert store.analyze(dem, tmp_path / "analyze.log") == CHECKSUM
    assert store.has(CHECKSUM)
    assert not store.has_stats(CHECKSUM)


def test_add_faceit_adds_each_players_line_to_the_stats(tmp_path, dem):
    store = analysis.Analyses(tmp_path / "analyses", tmp_path / "csda.exe", run=FakeCsda())
    assert store.add_faceit(CHECKSUM, {}) is False   # no stats worked out yet
    store.analyze(dem, tmp_path / "analyze.log")
    line = {SUBJECT: {"nickname": "subject-faceit", "stats": {"Kills": "9", "Headshots %": "50"}}}
    assert store.add_faceit(CHECKSUM, line) is True
    faceit = store.stats(CHECKSUM)["players"][SUBJECT]["faceit"]
    assert (faceit["nickname"], faceit["kills"], faceit["hs_pct"]) == ("subject-faceit", 9.0, 50.0)


def test_a_checksum_can_be_shorter_than_16_characters():
    """csda writes a checksum without its leading zeros, so some are 15 characters long."""
    assert analysis.is_checksum("4aaf50216c775f5")
    assert analysis.is_checksum(CHECKSUM)
    assert not analysis.is_checksum("")
    assert not analysis.is_checksum("0123456789abcdef0")
    assert not analysis.is_checksum("../0123")


def test_run_logged_keeps_what_the_command_printed(tmp_path):
    log_path = tmp_path / "logs" / "analyze.log"
    code = analysis.run_logged(
        [sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"], log_path)
    assert code == 3
    assert log_path.read_text(encoding="utf-8").split() == ["out", "err"]
