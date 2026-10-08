"""The HLAE plan: the Sequences CS:DM would record for a request, and the console script that records them
through HLAE instead of CS:DM's server plugin. Everything here is pure: no CS2 and no HLAE."""
import re
from pathlib import Path

import pytest

from clipper import analysis, hlae_plan, render
from clipper.hlae_plan import Kill, NoticePlayer, Player, RenderInputs, Round, Sequence, build_sequences
from tests import csda_json
from tests.fixtures import SUBJECT

ENEMY = "76561198000000011"
OTHER_ENEMY = "76561198000000012"
MATE = "76561198000000013"
PLAYERS = (
    Player(SUBJECT, "Subject", 3),
    Player(ENEMY, "Enemy One", 7),
    Player(OTHER_ENEMY, "Enemy Two", 8),
    Player(MATE, "Mate", 4),
)


def kill(tick, victim=ENEMY, killer=SUBJECT, round_number=1):
    return Kill(tick=tick, round_number=round_number, killer_steam_id=killer, victim_steam_id=victim)


def inputs(kills=(), rounds=(), tickrate=64.0, tick_count=200_000, players=PLAYERS):
    return RenderInputs(tickrate=tickrate, tick_count=tick_count, kills=tuple(kills), rounds=tuple(rounds),
                        players=players)


def test_clip_name_is_what_csdm_calls_a_clip_and_the_renderer_reads():
    name = hlae_plan.clip_name(Sequence(number=3, start_tick=10235, end_tick=10491, cameras=()))
    assert name == "sequence-3-tick-10235-to-10491.mp4"
    found = render.CLIP_NAME.match(name)
    assert (int(found[1]), int(found[2]), int(found[3])) == (3, 10235, 10491)


def test_raw_folder_is_csdms_name_for_a_sequence_inside_the_raw_dir(tmp_path):
    assert hlae_plan.raw_folder(tmp_path, 2) == tmp_path / "2-sequence"


@pytest.mark.parametrize("line, expected", [
    ("[InputService] execing cs2clipper_go", ("playing", None)),
    ("[InputService] execing cs2clipper_s1_prepare", ("ready", 1)),
    ("[InputService] execing cs2clipper_s12_start", ("recording", 12)),
    ("[InputService] execing cs2clipper_s3_end", ("done", 3)),
    ("[InputService] execing cs2clipper_s2_quit", ("quit", None)),
    ("10/08 04:38:26 [InputService] execing cs2clipper_s4_end\r\n", ("done", 4)),     # as console.log has it
])
def test_parse_marker_reads_the_step_from_the_cfg_cs2_says_it_runs(line, expected):
    assert hlae_plan.parse_marker(line) == expected


@pytest.mark.parametrize("line", [
    "",
    "[InputService] execing cs2clipper",                # the first cfg, before the demo plays
    "[InputService] execing cs2clipper_s1",             # a Sequence's seek
    "[InputService] execing cs2clipper_s1_aim",
    "[InputService] execing cs2clipper_s1_camera2",
    "[InputService] execing cs2clipper_s1_next",
    "[InputService] execing cs2clipper_s1_startx",
    "[InputService] execing cs2clipper_sx_start",
    "[InputService] execing cs2clipper_gone",
    "[InputService] execing autoexec",
    "CS2CLIPPER playing",           # what an `echo` prints, which never reaches console.log
])
def test_parse_marker_ignores_anything_else(line):
    assert hlae_plan.parse_marker(line) is None


def kills_plan(kills, *, before=2.0, after=2.0, perspective="player", rounds=(), **options):
    return build_sequences(inputs(kills, **options), SUBJECT, event="kills", perspective=perspective,
                           rounds=rounds, padding_before_s=before, padding_after_s=after)


# Sequences from Kills: CS:DM's build-players-event-sequences.ts, at 64 tick with 2 s before and after
# unless a test says otherwise (2 s = 128 ticks, the 10 s that extend a Sequence = 640 ticks).

def test_a_kill_is_one_sequence_padded_on_both_sides():
    [sequence] = kills_plan([kill(10363)])
    assert (sequence.number, sequence.start_tick, sequence.end_tick) == (1, 10363 - 128, 10363 + 128)
    assert sequence.cameras == ((10363 - 128, SUBJECT),)


def test_the_notices_name_every_player_and_highlight_the_subject():
    [sequence] = kills_plan([kill(10363)])
    assert sequence.notices == (
        NoticePlayer(SUBJECT, "Subject", True),
        NoticePlayer(ENEMY, "Enemy One", False),
        NoticePlayer(OTHER_ENEMY, "Enemy Two", False),
        NoticePlayer(MATE, "Mate", False),
    )


def test_only_the_subjects_kills_make_sequences():
    assert kills_plan([]) == []
    assert kills_plan([kill(5000, victim=SUBJECT, killer=ENEMY)]) == []


def test_sequences_are_numbered_from_one_and_come_in_tick_order():
    sequences = kills_plan([kill(10363), kill(30000, round_number=5)])
    assert [(s.number, s.start_tick, s.end_tick) for s in sequences] == [
        (1, 10235, 10491),
        (2, 29872, 30128),
    ]


def test_a_sequence_never_starts_before_tick_one():
    [sequence] = kills_plan([kill(50)])
    assert (sequence.start_tick, sequence.end_tick) == (1, 178)
    assert sequence.cameras == ((1, SUBJECT),)


def test_a_sequence_never_ends_after_the_demos_last_tick():
    [sequence] = kills_plan([kill(950)], tick_count=1000)
    assert sequence.end_tick == 1000


def test_a_kill_within_ten_seconds_of_the_next_extends_the_sequence_and_merges_them():
    [sequence] = kills_plan([kill(1000), kill(1640, victim=OTHER_ENEMY)])
    assert (sequence.number, sequence.start_tick, sequence.end_tick) == (1, 872, 1640 + 128)
    assert sequence.cameras == ((872, SUBJECT), (1320, SUBJECT))      # the midpoint of the two Kills


def test_ten_seconds_and_a_tick_apart_is_too_far_to_extend():
    first, second = kills_plan([kill(1000), kill(1641)])
    assert (first.start_tick, first.end_tick) == (872, 1128)
    assert (second.number, second.start_tick, second.end_tick) == (2, 1513, 1769)


def test_the_extended_end_stays_within_the_demo():
    [sequence] = kills_plan([kill(1000), kill(1400)], tick_count=1500)
    assert sequence.end_tick == 1500


def test_sequences_that_come_within_two_seconds_of_each_other_merge():
    # 5 s before and after: the Sequences span 1000 +- 320. The gap between the Kills is over 10 s, so
    # nothing is extended; the paddings alone make them overlap, and 2 s apart still counts.
    [merged] = kills_plan([kill(1000), kill(1768)], before=5, after=5)
    assert (merged.start_tick, merged.end_tick) == (680, 1768 + 320)
    assert merged.cameras == ((680, SUBJECT), (1384, SUBJECT))
    first, second = kills_plan([kill(1000), kill(1769)], before=5, after=5)
    assert (first.start_tick, first.end_tick) == (680, 1320)
    assert (second.start_tick, second.end_tick) == (1449, 1769 + 320)


def test_a_merged_sequence_does_not_use_up_a_number():
    sequences = kills_plan([kill(1000), kill(1100, victim=OTHER_ENEMY), kill(5000, round_number=2)])
    assert [s.number for s in sequences] == [1, 2]


@pytest.mark.parametrize("second_tick, midpoint", [
    (1002, 1001),       # exact
    (1001, 1001),       # 1000.5: JavaScript's Math.round goes up, Python's round() would not
    (1003, 1002),       # 1001.5
    (1000, 1000),       # two Kills on the same tick
])
def test_the_camera_changes_at_the_midpoint_rounded_half_up(second_tick, midpoint):
    [sequence] = kills_plan([kill(1000), kill(second_tick, victim=OTHER_ENEMY)])
    assert [tick for tick, _ in sequence.cameras] == [872, midpoint]


def test_padding_ticks_are_rounded_half_up_as_well():
    # 64 tick * 0.0390625 s is 2.5 ticks
    [sequence] = kills_plan([kill(1000)], before=0.0390625, after=0.0390625)
    assert (sequence.start_tick, sequence.end_tick) == (997, 1003)


def test_the_padding_follows_the_tickrate():
    [sequence] = kills_plan([kill(10000)], tickrate=128.0)
    assert (sequence.start_tick, sequence.end_tick) == (10000 - 256, 10000 + 256)


def test_the_enemy_perspective_follows_each_victim():
    kills = [kill(1000, victim=ENEMY), kill(1300, victim=OTHER_ENEMY)]
    [sequence] = kills_plan(kills, perspective="enemy")
    assert sequence.cameras == ((872, ENEMY), (1150, OTHER_ENEMY))
    [sequence] = kills_plan(kills, perspective="player")
    assert sequence.cameras == ((872, SUBJECT), (1150, SUBJECT))


@pytest.mark.parametrize("victim, followed", [
    ("", SUBJECT),      # nobody to follow: CS:DM keeps the killer
    ("0", "0"),         # a bot: the id is not empty, and a camera with no slot is skipped later on
])
def test_the_enemy_perspective_keeps_the_killer_only_without_a_victim(victim, followed):
    [sequence] = kills_plan([kill(1000, victim=victim)], perspective="enemy")
    assert sequence.cameras == ((872, followed),)


def test_rounds_limit_the_kills():
    kills = [kill(1000, round_number=1), kill(5000, round_number=2), kill(9000, round_number=3)]
    assert [s.start_tick for s in kills_plan(kills, rounds=(2,))] == [5000 - 128]
    assert [s.start_tick for s in kills_plan(kills, rounds=(1, 3))] == [872, 9000 - 128]
    assert len(kills_plan(kills, rounds=())) == 3


def test_only_kills_and_rounds_events_and_two_perspectives_are_known():
    with pytest.raises(ValueError):
        build_sequences(inputs([kill(1000)]), SUBJECT, event="deaths", padding_before_s=2, padding_after_s=2)
    with pytest.raises(ValueError):
        build_sequences(inputs([kill(1000)]), SUBJECT, perspective="spectator", padding_before_s=2,
                        padding_after_s=2)


def rounds_plan(rounds, kills=(), *, before=2.0, after=2.0, only=(), perspective="player", **options):
    return build_sequences(inputs(kills, rounds, **options), SUBJECT, event="rounds", perspective=perspective,
                           rounds=only, padding_before_s=before, padding_after_s=after)


# Sequences from rounds: CS:DM's build-players-rounds-sequences.ts. A round runs from the end of its freeze
# time to its end, or to the subject's death, and the padding is added to both.

def test_a_round_is_one_sequence_from_the_end_of_the_freeze_time_to_the_end_of_the_round():
    [sequence] = rounds_plan([Round(number=1, end_tick=9000, freeze_time_end_tick=1000)])
    assert (sequence.number, sequence.start_tick, sequence.end_tick) == (1, 1000 - 128, 9000 + 128)
    assert sequence.cameras == ((1000 - 128, SUBJECT),)
    assert sequence.notices == ()       # every death notice shows


def test_a_round_the_subject_dies_in_ends_at_the_death():
    rounds = [Round(1, 9000, 1000), Round(2, 18000, 10000), Round(3, 27000, 19000), Round(4, 36000, 28000)]
    kills = [
        kill(4000, victim=SUBJECT, killer=ENEMY, round_number=1),       # dies: the round ends there
        kill(12000, victim=ENEMY, killer=SUBJECT, round_number=2),      # kills and lives on
        kill(20000, victim=MATE, killer=ENEMY, round_number=3),         # someone else dies
        kill(30000, victim=SUBJECT, killer=ENEMY, round_number=4),
    ]
    ends = [sequence.end_tick for sequence in rounds_plan(rounds, kills)]
    assert ends == [4000 + 128, 18000 + 128, 27000 + 128, 30000 + 128]


def test_the_first_death_of_the_subject_in_a_round_counts():
    kills = [kill(4000, victim=SUBJECT, killer=ENEMY), kill(5000, victim=SUBJECT, killer=OTHER_ENEMY)]
    [sequence] = rounds_plan([Round(1, 9000, 1000)], kills)
    assert sequence.end_tick == 4000 + 128


def test_rounds_limit_the_rounds_and_the_numbers_follow_the_rounds_taken():
    rounds = [Round(1, 9000, 1000), Round(2, 18000, 10000), Round(3, 27000, 19000), Round(4, 36000, 28000)]
    taken = rounds_plan(rounds, only=(2, 4))
    assert [(s.number, s.start_tick) for s in taken] == [(1, 10000 - 128), (2, 28000 - 128)]
    assert len(rounds_plan(rounds, only=())) == 4
    assert rounds_plan(rounds, only=(9,)) == []


def test_the_perspective_makes_no_difference_to_whole_rounds():
    kills = [kill(4000, victim=SUBJECT, killer=ENEMY)]
    plan = rounds_plan([Round(1, 9000, 1000)], kills)
    assert rounds_plan([Round(1, 9000, 1000)], kills, perspective="enemy") == plan
    assert plan[0].cameras == ((1000 - 128, SUBJECT),)


def test_whole_rounds_are_not_clamped_to_the_demo_as_csdm_does_not_either():
    [sequence] = rounds_plan([Round(1, 990, 10)], tick_count=1000)
    assert (sequence.start_tick, sequence.end_tick) == (10 - 128, 990 + 128)


def test_a_fraction_of_a_tick_is_rounded_half_up_where_csdm_would_name_a_clip_after_it():
    # 64 tick * 0.0390625 s is 2.5 ticks; CS:DM leaves 997.5 and 9002.5 in the Clip's name
    [sequence] = rounds_plan([Round(1, 9000, 1000)], before=0.0390625, after=0.0390625)
    assert (sequence.start_tick, sequence.end_tick) == (998, 9003)
    assert sequence.cameras == ((998, SUBJECT),)


def test_the_padding_of_whole_rounds_follows_the_tickrate():
    [sequence] = rounds_plan([Round(1, 9000, 1000)], tickrate=128.0)
    assert (sequence.start_tick, sequence.end_tick) == (1000 - 256, 9000 + 256)


def test_sequences_come_in_start_order_and_keep_their_numbers():
    rounds = [Round(1, 19000, 11000), Round(2, 9000, 1000)]     # out of order, as nothing guarantees them
    assert [(s.number, s.start_tick) for s in rounds_plan(rounds)] == [(2, 1000 - 128), (1, 11000 - 128)]


# The console script. Ticks are worked out by hand at 64 tick: 2 s is 128 ticks, 5 s 320, 0.25 s 16, 0.5 s 32
# and 1 s 64, and the plan starts to act at tick 96.

DEMO = Path(r"C:\Users\Some One\Demos\match 1.dem")
RAW = Path(r"C:\Users\Some One\AppData\Local\cs2-clipper\raw")
RAW_1 = "C:/Users/Some One/AppData/Local/cs2-clipper/raw/1-sequence"      # as CS:DM writes it to HLAE
STEP = re.compile(r"^mirv_cmd addAtTick (\d+) exec (\S+)$")


def plan_script(sequences, tickrate=64.0, **options):
    return hlae_plan.script(sequences, inputs(tickrate=tickrate), demo_path=DEMO, raw_dir=RAW, **options)


def cfg_lines(files, name):
    text = files[name + ".cfg"]
    assert text.endswith("\n")
    return text.splitlines()


def steps(files, name):
    """What the cfg `name` schedules, as (tick, cfg name) in the order it says."""
    return [(int(found[1]), found[2]) for found in map(STEP.match, cfg_lines(files, name)) if found]


def two_sequences():
    return kills_plan([kill(10363), kill(30000, round_number=5)])


def test_the_script_is_a_set_of_cfg_files_named_after_the_app():
    files = plan_script(two_sequences())
    assert sorted(files) == [
        "cs2clipper.cfg", "cs2clipper_go.cfg",
        "cs2clipper_s1.cfg", "cs2clipper_s1_aim.cfg", "cs2clipper_s1_end.cfg", "cs2clipper_s1_next.cfg",
        "cs2clipper_s1_prepare.cfg", "cs2clipper_s1_start.cfg",
        "cs2clipper_s2.cfg", "cs2clipper_s2_aim.cfg", "cs2clipper_s2_end.cfg", "cs2clipper_s2_prepare.cfg",
        "cs2clipper_s2_quit.cfg", "cs2clipper_s2_start.cfg",
    ]
    assert hlae_plan.ENTRY == "cs2clipper"      # CS2 is started with +exec cs2clipper


def test_the_entry_cfg_starts_the_demo_and_schedules_the_first_step():
    assert cfg_lines(plan_script(two_sequences()), "cs2clipper") == [
        "mirv_cmd clear",
        "mirv_cmd enabled 1",
        "mirv_cmd addAtTick 96 exec cs2clipper_go",
        "demo_ui_mode 0",       # before the playback starts, or CS2 shows its playback bar (CS:DM's plugin-main.cpp)
        r'playdemo "C:\Users\Some One\Demos\match 1.dem"',      # the path as CS:DM passes it, spaces and all
    ]


def test_the_go_cfg_sends_csdms_settings_once_and_hands_over_to_the_first_sequence():
    assert cfg_lines(plan_script(two_sequences()), "cs2clipper_go") == [
        "sv_cheats 1",
        "volume 1",
        "cl_hud_telemetry_frametime_show 0",
        "cl_hud_telemetry_net_misdelivery_show 0",
        "cl_hud_telemetry_ping_show 0",
        "cl_hud_telemetry_serverrecvmargin_graph_show 0",
        "cl_trueview_show_status 0",
        "r_show_build_info 0",
        "mirv_streams record screen enabled 1",
        "cl_demo_predict 0",
        "cl_draw_only_deathnotices 1",
        "mirv_deathmsg lifetime 5",
        "mirv_deathmsg filter clear",
        "tv_listen_voice_indices -1",
        "tv_listen_voice_indices_h -1",
        "exec cs2clipper_s1",
    ]


def test_the_video_settings_change_what_is_sent():
    settings = hlae_plan.VideoSettings(
        show_xray=False, show_assists=False, show_only_death_notices=False, death_notices_duration=8,
        player_voices_enabled=False, record_audio=False, true_view=True, framerate=30, video_codec="libx265",
        constant_rate_factor=18, container="mkv")
    files = plan_script(two_sequences(), settings=settings)
    go = cfg_lines(files, "cs2clipper_go")
    for line in ("cl_demo_predict 1", "cl_draw_only_deathnotices 0", "mirv_deathmsg lifetime 8",
                 "tv_listen_voice_indices 0", "tv_listen_voice_indices_h 0"):
        assert line in go
    assert "tv_listen_voice_indices -1" not in go
    prepare = cfg_lines(files, "cs2clipper_s1_prepare")
    for line in ("mirv_streams record startMovieWav 0", "spec_show_xray 0", "mp_display_kill_assists 0",
                 "mirv_streams record fps 30"):
        assert line in prepare
    [preset] = [line for line in prepare if line.startswith("mirv_streams settings add ffmpeg")]
    assert preset == ('mirv_streams settings add ffmpeg cs2clipperPreset1 "-c:v libx265 -pix_fmt yuv420p -crf 18 '
                      '{QUOTE}' + RAW_1 + r'\\video.mkv{QUOTE}"')


def test_output_parameters_take_the_place_of_the_crf_as_in_csdm():
    files = plan_script(two_sequences(), settings=hlae_plan.VideoSettings(output_parameters="-b:v 8M"))
    [preset] = [line for line in cfg_lines(files, "cs2clipper_s1_prepare") if " settings add ffmpeg " in line]
    assert preset == ('mirv_streams settings add ffmpeg cs2clipperPreset1 "-c:v libx264 -pix_fmt yuv420p -b:v 8M '
                      '{QUOTE}' + RAW_1 + r'\\video.mp4{QUOTE}"')


def test_the_video_settings_default_to_the_values_renders_through_csdm_pinned():
    settings = hlae_plan.VideoSettings()
    assert (settings.show_xray, settings.show_assists, settings.show_only_death_notices,
            settings.death_notices_duration, settings.player_voices_enabled, settings.record_audio,
            settings.true_view, settings.framerate, settings.video_codec, settings.constant_rate_factor,
            settings.output_parameters, settings.container) == (
        True, True, True, 5, True, True, False, 60, "libx264", 23, "", "mp4")


def test_a_sequences_cfg_schedules_its_steps_then_seeks_to_where_it_lands():
    files = plan_script(two_sequences())
    # 10235 - 2 s lands at 10107: 0.25 s later it sets up, 0.5 s later it aims, then come the start, the end
    # and, 0.5 s after that, the hand-off
    assert cfg_lines(files, "cs2clipper_s1") == [
        "mirv_cmd clear",
        "mirv_cmd addAtTick 10123 exec cs2clipper_s1_prepare",
        "mirv_cmd addAtTick 10139 exec cs2clipper_s1_aim",
        "mirv_cmd addAtTick 10235 exec cs2clipper_s1_start",
        "mirv_cmd addAtTick 10491 exec cs2clipper_s1_end",
        "mirv_cmd addAtTick 10523 exec cs2clipper_s1_next",
        "demo_gototick 10107",
    ]
    # the last one quits 1 s after its end instead of handing over
    assert cfg_lines(files, "cs2clipper_s2") == [
        "mirv_cmd clear",
        "mirv_cmd addAtTick 29760 exec cs2clipper_s2_prepare",
        "mirv_cmd addAtTick 29776 exec cs2clipper_s2_aim",
        "mirv_cmd addAtTick 29872 exec cs2clipper_s2_start",
        "mirv_cmd addAtTick 30128 exec cs2clipper_s2_end",
        "mirv_cmd addAtTick 30192 exec cs2clipper_s2_quit",
        "demo_gototick 29744",
    ]


def test_the_prepare_cfg_sets_up_the_recording_as_csdm_does():
    lines = cfg_lines(plan_script(two_sequences()), "cs2clipper_s1_prepare")
    assert lines[:9] == [
        "mirv_streams record startMovieWav 1",
        f'mirv_streams record name "{RAW_1}"',
        "mirv_deathmsg clear",
        "spec_show_xray 1",
        "mp_display_kill_assists 1",
        'mirv_streams settings add ffmpeg cs2clipperPreset1 "-c:v libx264 -pix_fmt yuv420p -crf 23 '
        + "{QUOTE}" + RAW_1 + r"\\video.mp4{QUOTE}" + '"',
        "mirv_streams record screen settings cs2clipperPreset1",
        "mirv_streams record fps 60",
        "mirv_deathmsg filter clear",
    ]
    assert lines[9] == "mirv_deathmsg filter add block=1"       # nothing shows but what is allowed below
    allowed = []
    for player in PLAYERS:
        allowed += [
            f'mirv_replace_name byXuid add x{player.steam_id} "{player.name}"',
            f"mirv_deathmsg filter add attackerMatch=x{player.steam_id} "
            f"attackerIsLocal={1 if player.steam_id == SUBJECT else 0} block=0",
        ]
    assert lines[10:] == allowed


def test_every_sequence_gets_its_own_folder_and_preset():
    second = cfg_lines(plan_script(two_sequences()), "cs2clipper_s2_prepare")
    assert 'mirv_streams record name "' + RAW_1.replace("/1-sequence", "/2-sequence") + '"' in second
    assert "mirv_streams record screen settings cs2clipperPreset2" in second
    assert any(line.startswith("mirv_streams settings add ffmpeg cs2clipperPreset2 ") for line in second)


def test_a_sequence_with_no_notices_leaves_the_death_notices_alone():
    files = plan_script([Sequence(1, 10000, 12000, ((10000, SUBJECT),))])
    assert cfg_lines(files, "cs2clipper_s1_prepare")[-1] == "mirv_deathmsg filter clear"


def test_the_steps_hold_the_commands_csdm_sends_at_those_ticks():
    files = plan_script(two_sequences())
    assert cfg_lines(files, "cs2clipper_s1_aim") == ["spec_mode 1", "spec_player 3"]
    assert cfg_lines(files, "cs2clipper_s1_start") == [
        "spec_mode 1", "spec_player 3", "mirv_streams record start"]
    assert cfg_lines(files, "cs2clipper_s1_end") == ["mirv_streams record end"]
    assert cfg_lines(files, "cs2clipper_s1_next") == ["exec cs2clipper_s2"]
    assert cfg_lines(files, "cs2clipper_s2_quit") == ["quit"]


@pytest.mark.parametrize("start, landing, seeks", [
    (543, 415, False),      # 319 ticks from tick 96 to the seek target: one short
    (544, 416, True),       # 320 ticks, 5 s: seeking skips enough to be worth it
])
def test_the_first_sequence_seeks_only_when_that_skips_five_seconds_from_tick_96(start, landing, seeks):
    files = plan_script([Sequence(1, start, 900, ((start, SUBJECT),))])
    assert (f"demo_gototick {landing}" in cfg_lines(files, "cs2clipper_s1")) is seeks
    assert steps(files, "cs2clipper_s1")[0] == (landing + 16, "cs2clipper_s1_prepare")


def test_a_sequence_close_to_the_start_of_the_demo_does_not_seek():
    [sequence] = kills_plan([kill(400)])        # 272 to 528
    files = plan_script([sequence])
    assert steps(files, "cs2clipper_s1") == [
        (160, "cs2clipper_s1_prepare"), (176, "cs2clipper_s1_aim"), (272, "cs2clipper_s1_start"),
        (528, "cs2clipper_s1_end"), (592, "cs2clipper_s1_quit")]
    assert not any(line.startswith("demo_gototick") for line in cfg_lines(files, "cs2clipper_s1"))


@pytest.mark.parametrize("start, landing, seeks", [
    (2479, 2351, False),    # the hand-off is at 2000 + 32 = 2032, and 2351 is 319 ticks on
    (2480, 2352, True),
])
def test_a_later_sequence_seeks_only_when_that_skips_five_seconds_from_the_hand_off(start, landing, seeks):
    files = plan_script([Sequence(1, 1000, 2000, ((1000, SUBJECT),)), Sequence(2, start, 3000, ((start, SUBJECT),))])
    assert (f"demo_gototick {landing}" in cfg_lines(files, "cs2clipper_s2")) is seeks
    assert steps(files, "cs2clipper_s2")[0] == (landing + 16, "cs2clipper_s2_prepare")


def test_a_sequence_whose_approach_is_behind_the_hand_off_lands_at_the_hand_off_and_never_seeks_back():
    first, second = kills_plan([kill(1000), kill(1769)], before=5, after=5)     # 680 to 1320, 1449 to 2089
    files = plan_script([first, second])
    # 1449 - 2 s is 1321, behind the hand-off at 1320 + 32: the demo plays on and it sets up 0.25 s after that
    assert steps(files, "cs2clipper_s2") == [
        (1368, "cs2clipper_s2_prepare"), (1384, "cs2clipper_s2_aim"), (1449, "cs2clipper_s2_start"),
        (2089, "cs2clipper_s2_end"), (2153, "cs2clipper_s2_quit")]
    assert not any(line.startswith("demo_gototick") for line in cfg_lines(files, "cs2clipper_s2"))


def test_the_offsets_follow_the_tickrate():
    files = plan_script([Sequence(1, 20000, 21000, ((20000, SUBJECT),)),
                         Sequence(2, 30000, 31000, ((30000, SUBJECT),))], tickrate=128.0)
    assert steps(files, "cs2clipper_s1") == [
        (19776, "cs2clipper_s1_prepare"), (19808, "cs2clipper_s1_aim"), (20000, "cs2clipper_s1_start"),
        (21000, "cs2clipper_s1_end"), (21064, "cs2clipper_s1_next")]
    assert "demo_gototick 19744" in cfg_lines(files, "cs2clipper_s1")       # 2 s is 256 ticks
    assert steps(files, "cs2clipper_s2")[-1] == (31128, "cs2clipper_s2_quit")


def test_the_chain_follows_the_order_of_the_list_and_not_the_numbers():
    files = plan_script([Sequence(2, 5000, 6000, ((5000, SUBJECT),)), Sequence(1, 9000, 10000, ((9000, SUBJECT),))])
    assert cfg_lines(files, "cs2clipper_go")[-1] == "exec cs2clipper_s2"
    assert cfg_lines(files, "cs2clipper_s2_next") == ["exec cs2clipper_s1"]
    assert "cs2clipper_s1_quit.cfg" in files


# Whom the camera follows.

def camera_plan(*cameras, start=10000, end=12000):
    return plan_script([Sequence(1, start, end, tuple(cameras))])


def test_the_camera_aims_before_the_start_and_again_as_it_starts():
    files = camera_plan((10000, SUBJECT))
    # lands at 9872: sets up at 9888, aims at 9904
    assert steps(files, "cs2clipper_s1") == [
        (9888, "cs2clipper_s1_prepare"), (9904, "cs2clipper_s1_aim"), (10000, "cs2clipper_s1_start"),
        (12000, "cs2clipper_s1_end"), (12064, "cs2clipper_s1_quit")]
    assert cfg_lines(files, "cs2clipper_s1_aim") == ["spec_mode 1", "spec_player 3"]
    assert cfg_lines(files, "cs2clipper_s1_start")[:2] == ["spec_mode 1", "spec_player 3"]


def test_a_later_camera_change_is_a_step_at_its_tick():
    files = camera_plan((10000, SUBJECT), (10700, ENEMY), (11200, OTHER_ENEMY))
    assert steps(files, "cs2clipper_s1")[2:] == [
        (10000, "cs2clipper_s1_start"), (10700, "cs2clipper_s1_camera2"), (11200, "cs2clipper_s1_camera3"),
        (12000, "cs2clipper_s1_end"), (12064, "cs2clipper_s1_quit")]
    assert cfg_lines(files, "cs2clipper_s1_camera2") == ["spec_mode 1", "spec_player 7"]
    assert cfg_lines(files, "cs2clipper_s1_camera3") == ["spec_mode 1", "spec_player 8"]


def test_the_cameras_up_to_the_start_fold_into_the_one_the_start_aims_at_and_the_last_wins():
    files = camera_plan((9000, ENEMY), (9500, OTHER_ENEMY), (10000, MATE))
    assert cfg_lines(files, "cs2clipper_s1_aim") == ["spec_mode 1", "spec_player 4"]
    assert [name for _, name in steps(files, "cs2clipper_s1")] == [
        "cs2clipper_s1_prepare", "cs2clipper_s1_aim", "cs2clipper_s1_start", "cs2clipper_s1_end",
        "cs2clipper_s1_quit"]


def test_two_camera_changes_on_one_tick_make_one_step_and_the_last_wins():
    files = camera_plan((10000, SUBJECT), (10700, ENEMY), (10700, OTHER_ENEMY))
    assert [step for step in steps(files, "cs2clipper_s1") if 10000 < step[0] < 12000] == [
        (10700, "cs2clipper_s1_camera2")]
    assert cfg_lines(files, "cs2clipper_s1_camera2") == ["spec_mode 1", "spec_player 8"]


def test_a_camera_at_or_after_the_end_is_dropped():
    files = camera_plan((10000, SUBJECT), (12000, ENEMY), (13000, OTHER_ENEMY))
    assert not any("camera" in name for _, name in steps(files, "cs2clipper_s1"))


def test_a_camera_on_nobody_in_the_demo_is_skipped_as_csdm_skips_it():
    files = camera_plan((10000, "0"), (10700, ENEMY), (11000, "76561198999999999"))
    assert "cs2clipper_s1_aim.cfg" not in files
    assert cfg_lines(files, "cs2clipper_s1_start") == ["mirv_streams record start"]
    assert [name for _, name in steps(files, "cs2clipper_s1")] == [
        "cs2clipper_s1_prepare", "cs2clipper_s1_start", "cs2clipper_s1_camera2", "cs2clipper_s1_end",
        "cs2clipper_s1_quit"]


def test_a_steam_id_the_demo_lists_twice_is_found_at_its_first_slot():
    players = (Player("0", "Bot A", 5), Player("0", "Bot B", 6))
    files = hlae_plan.script([Sequence(1, 10000, 12000, ((10000, "0"),))], inputs(players=players),
                             demo_path=DEMO, raw_dir=RAW)
    assert cfg_lines(files, "cs2clipper_s1_aim") == ["spec_mode 1", "spec_player 5"]


# What can and cannot go into a cfg.

def test_a_players_name_cannot_end_its_quotes_or_start_another_command():
    sequence = Sequence(1, 10000, 12000, ((10000, SUBJECT),), (
        NoticePlayer(SUBJECT, 'Say "hi"; quit\r\nquit', True),
        NoticePlayer(ENEMY, "Zoë 山田 O'Neil", False),
    ))
    files = plan_script([sequence])
    lines = cfg_lines(files, "cs2clipper_s1_prepare")
    assert f"mirv_replace_name byXuid add x{SUBJECT} \"Say 'hi', quit  quit\"" in lines
    assert f'mirv_replace_name byXuid add x{ENEMY} "Zoë 山田 O\'Neil"' in lines      # any other name as it is
    assert not any("\r" in text for text in files.values())


def test_a_notice_player_without_a_steam_id_to_match_is_left_out():
    sequence = Sequence(1, 10000, 12000, ((10000, SUBJECT),), (
        NoticePlayer("", "Nobody", False),
        NoticePlayer("7656; quit", "Evil", False),
        NoticePlayer(SUBJECT, "Subject", True),
    ))
    lines = cfg_lines(plan_script([sequence]), "cs2clipper_s1_prepare")
    assert [line for line in lines if line.startswith("mirv_replace_name")] == [
        f'mirv_replace_name byXuid add x{SUBJECT} "Subject"']
    assert not any("quit" in line for line in lines)


def test_a_quote_or_a_line_break_in_a_path_or_a_setting_is_refused():
    with pytest.raises(hlae_plan.PlanError):
        hlae_plan.script(two_sequences(), inputs(), demo_path=Path('C:\\demos\\a"b.dem'), raw_dir=RAW)
    with pytest.raises(hlae_plan.PlanError):
        hlae_plan.script(two_sequences(), inputs(), demo_path=DEMO, raw_dir=Path("C:\\raw\nquit"))
    with pytest.raises(hlae_plan.PlanError):
        plan_script(two_sequences(), settings=hlae_plan.VideoSettings(output_parameters='-vf "scale=1280:-1"'))
    with pytest.raises(hlae_plan.PlanError):
        plan_script(two_sequences(), settings=hlae_plan.VideoSettings(video_codec="libx264\nquit"))


def test_a_path_may_hold_a_semicolon_or_spaces():
    files = hlae_plan.script(two_sequences(), inputs(), demo_path=Path(r"C:\Demos; more\a b.dem"),
                             raw_dir=Path(r"C:\Raw; more\a b"))
    assert cfg_lines(files, "cs2clipper")[-1] == r'playdemo "C:\Demos; more\a b.dem"'
    assert 'mirv_streams record name "C:/Raw; more/a b/1-sequence"' in cfg_lines(files, "cs2clipper_s1_prepare")


def test_nothing_to_record_is_refused():
    with pytest.raises(hlae_plan.PlanError):
        plan_script([])


def test_two_sequences_with_one_number_are_refused():
    with pytest.raises(hlae_plan.PlanError):
        plan_script([Sequence(1, 5000, 6000, ()), Sequence(1, 9000, 10000, ())])


@pytest.mark.parametrize("end", [5000, 4000])
def test_a_sequence_that_ends_when_it_starts_or_before_is_refused(end):
    with pytest.raises(hlae_plan.PlanError):
        plan_script([Sequence(1, 5000, end, ())])


@pytest.mark.parametrize("start, fits", [(128, False), (129, True)])
def test_the_first_sequence_needs_a_half_second_after_tick_96_to_set_up_and_aim(start, fits):
    sequences = [Sequence(1, start, 900, ((start, SUBJECT),))]
    if fits:
        assert steps(plan_script(sequences), "cs2clipper_s1")[:2] == [
            (112, "cs2clipper_s1_prepare"), (128, "cs2clipper_s1_aim")]
    else:
        with pytest.raises(hlae_plan.PlanError):
            plan_script(sequences)


@pytest.mark.parametrize("start, fits", [(2064, False), (2065, True)])
def test_a_sequence_needs_the_same_after_the_hand_off_of_the_one_before(start, fits):
    sequences = [Sequence(1, 1000, 2000, ((1000, SUBJECT),)), Sequence(2, start, 3000, ((start, SUBJECT),))]
    if fits:
        assert steps(plan_script(sequences), "cs2clipper_s2")[:2] == [
            (2048, "cs2clipper_s2_prepare"), (2064, "cs2clipper_s2_aim")]
    else:
        with pytest.raises(hlae_plan.PlanError):
            plan_script(sequences)


def test_sequences_that_overlap_cannot_be_recorded_without_going_back_and_are_refused():
    with pytest.raises(hlae_plan.PlanError):
        plan_script([Sequence(1, 1000, 2000, ((1000, SUBJECT),)), Sequence(2, 1500, 3000, ((1500, SUBJECT),))])


# Whatever the plan, it stays within what HLAE and CS2 do reliably.

PLANS = {
    "two kills": two_sequences,
    "merged kills": lambda: kills_plan([kill(1000), kill(1300, victim=OTHER_ENEMY)], perspective="enemy"),
    "paddings of 5 s": lambda: kills_plan([kill(1000), kill(1769), kill(9000)], before=5, after=5),
    "a kill at the start": lambda: kills_plan([kill(400)]),
    "rounds": lambda: rounds_plan([Round(1, 9000, 1000), Round(2, 19000, 11000), Round(3, 29000, 21000)],
                                  [kill(15000, victim=SUBJECT, killer=ENEMY, round_number=2)]),
}
ALLOWED_COMMANDS = {
    # what CS:DM sends
    "sv_cheats", "volume", "cl_hud_telemetry_frametime_show", "cl_hud_telemetry_net_misdelivery_show",
    "cl_hud_telemetry_ping_show", "cl_hud_telemetry_serverrecvmargin_graph_show", "cl_trueview_show_status",
    "r_show_build_info", "mirv_streams", "cl_demo_predict", "cl_draw_only_deathnotices", "mirv_deathmsg",
    "tv_listen_voice_indices", "tv_listen_voice_indices_h", "spec_show_xray", "mp_display_kill_assists",
    "mirv_replace_name", "spec_mode", "spec_player",
    # what the plan adds to drive it
    "mirv_cmd", "exec", "demo_gototick", "demo_ui_mode", "playdemo", "quit",
}


@pytest.fixture(params=sorted(PLANS))
def planned(request):
    return plan_script(PLANS[request.param]())


def test_only_commands_csdm_sends_or_the_plan_needs_are_used(planned):
    verbs = {line.split()[0] for text in planned.values() for line in text.splitlines()}
    assert verbs <= ALLOWED_COMMANDS


def test_every_cfg_a_cfg_runs_exists(planned):
    for text in planned.values():
        for line in text.splitlines():
            found = re.search(r"\bexec (\S+)$", line)
            if found:
                assert found[1] + ".cfg" in planned
    assert "cs2clipper.cfg" in planned


def test_a_cfg_never_schedules_two_steps_on_one_tick_nor_out_of_order_nor_before_it_lands(planned):
    for name, text in planned.items():
        ticks = [tick for tick, _ in steps(planned, name[:-4])]
        assert ticks == sorted(set(ticks)), name
        seek = re.search(r"^demo_gototick (\d+)$", text, re.MULTILINE)
        if seek:
            assert all(tick > int(seek[1]) for tick in ticks), name


def test_every_step_is_scheduled_by_one_cfg_and_every_cfg_but_the_entry_is_scheduled_or_handed_over_to(planned):
    lines = [line for text in planned.values() for line in text.splitlines()]
    scheduled = [STEP.match(line)[2] for line in lines if STEP.match(line)]
    assert len(scheduled) == len(set(scheduled))
    handed_over = {line.split()[1] for line in lines if line.startswith("exec ")}
    assert {name[:-4] for name in planned} == {"cs2clipper"} | set(scheduled) | handed_over


def test_the_runner_can_follow_the_plan_by_the_cfg_files_cs2_says_it_runs(planned):
    logged = [hlae_plan.parse_marker(f"[InputService] execing {name.removesuffix('.cfg')}") for name in planned]
    markers = [marker for marker in logged if marker is not None]
    numbers = sorted(int(name.split("_")[1][1:]) for name in planned if name.endswith("_start.cfg"))
    assert sorted(kind for kind, _ in markers) == sorted(
        ["playing", "quit"] + ["ready", "recording", "done"] * len(numbers))
    assert sorted(number for kind, number in markers if kind == "recording") == numbers


# What the plan reads from csda's analysis of a match (clipper/analysis.py, tested in test_analysis.py)


def test_the_analysis_of_a_match_makes_the_plan_aim_at_the_subject():
    inputs_ = analysis.render_inputs(csda_json.MATCH)
    [first, *_] = build_sequences(inputs_, csda_json.SUBJECT, padding_before_s=2, padding_after_s=2)
    assert first.cameras[0][1] == csda_json.SUBJECT
    files = hlae_plan.script([first], inputs_, demo_path=DEMO, raw_dir=RAW)
    assert cfg_lines(files, "cs2clipper_s1_aim") == ["spec_mode 1", "spec_player 4"]     # csda's userId 3, plus 1
