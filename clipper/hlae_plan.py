"""What CS Demo Manager's server plugin did for `csdm video`, as a plan CS2 follows by itself.

Since a CS2 update the plugin no longer hooks in (upstream akiver/cs-demo-manager#1458), so a Render Job
records through HLAE alone: CS2 starts with `+exec cs2clipper`, and HLAE's `mirv_cmd addAtTick` runs the
plan below at the right ticks. `build_sequences` ports CS:DM 3.20.1's two builders, so the same request
gives the same Sequences and the same Clip names; `script` writes the cfg files that record them.

Pure: no file, process or database access. The runner writes the cfg files, launches CS2, reads its
console for the markers (`parse_marker`) and finds each Clip in `raw_folder`."""
from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

ENTRY = "cs2clipper"        # CS2 starts with `+exec cs2clipper`; every other cfg file is named after it

# CS:DM's builders (build-players-event-sequences.ts): Sequences closer than this merge, and a Frag with
# another this close after it carries on into it.
MERGE_GAP_S = 2.0
EXTEND_WITHIN_S = 10.0

# When the console script acts. Nothing runs before FIRST_TICK, as in CS:DM (`getValidTick`): the game may skip
# the first ticks after it loads a demo. A Sequence is approached PREROLL_S before its first tick; the demo
# is seeked to there only when that skips SEEK_MIN_S or more (the game never goes back), else it plays on.
FIRST_TICK = 96
PREROLL_S = 2.0
SEEK_MIN_S = 5.0
PREPARE_S = 0.25        # after landing: the recording is set up this late, so the game is steady by then
CAMERA_S = 0.5          # after landing: the camera is aimed this late
HANDOFF_S = 0.5         # after a Sequence ends: the next one's cfg takes over this late
QUIT_S = 1.0            # after the last Sequence ends: the game quits this late (CS:DM: `endTick + 64`)


@dataclass(frozen=True)
class Kill:
    tick: int
    round_number: int
    killer_steam_id: str
    victim_steam_id: str


@dataclass(frozen=True)
class Round:
    number: int
    end_tick: int
    freeze_time_end_tick: int


@dataclass(frozen=True)
class Player:
    steam_id: str
    name: str
    slot: int       # what `spec_player` takes: csda's userId plus 1


@dataclass(frozen=True)
class RenderInputs:
    """What the builders read from the analysis of one Demo's match (`analysis.render_inputs`)."""

    tickrate: float
    tick_count: int
    kills: tuple[Kill, ...]         # by tick
    rounds: tuple[Round, ...]       # by number
    players: tuple[Player, ...]


@dataclass(frozen=True)
class NoticePlayer:
    """One player's line in the death notices (`SequencePlayerOptions` in CS:DM)."""

    steam_id: str
    name: str
    highlight: bool     # the notice of a Frag by this player is drawn as the local player's


@dataclass(frozen=True)
class Sequence:
    """One Clip to record, as CS:DM's builders make it."""

    number: int
    start_tick: int
    end_tick: int
    cameras: tuple[tuple[int, str], ...]        # (tick, steam id): whom the camera follows from that tick on
    notices: tuple[NoticePlayer, ...] = ()      # empty: every death notice shows


class PlanError(Exception):
    """A request the console script cannot carry out: a Sequence with no room to be set up, or a path or
    setting that would end its own quotes or start another command."""


@dataclass(frozen=True)
class VideoSettings:
    """The video settings of CS:DM that change what it sends to the game. The defaults are the ones a Render
    Job pinned in CS:DM's settings file when it rendered through CS:DM, so leaving them out records the same."""

    show_xray: bool = True
    show_assists: bool = True
    show_only_death_notices: bool = True
    death_notices_duration: float = 5       # seconds
    player_voices_enabled: bool = True
    record_audio: bool = True
    true_view: bool = False
    framerate: int = 60
    video_codec: str = "libx264"
    constant_rate_factor: int = 23
    output_parameters: str = ""             # FFmpeg output parameters; when set they replace the factor
    container: str = "mp4"


# CS2 writes `[InputService] execing <name>` to console.log for each cfg it runs; what an `echo` prints never gets
# there. So the runner follows the plan by the names of the step files that mark how far it has got.
_MARKER = re.compile(rf"execing\s+{ENTRY}_(?:go|s(\d+)_(prepare|start|end|quit))(?!\S)")
_SEQUENCE_MARKERS = {"prepare": "ready", "start": "recording", "end": "done"}
_DIGITS = re.compile(r"[0-9]+")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")      # what ends a cfg line or cannot be typed in one


@dataclass
class _Draft:
    """A Sequence while the builder may still merge the next Frag into it."""

    start: int
    end: int
    cameras: list[tuple[int, str]]


def _round_half_up(value: float) -> int:
    """JavaScript's Math.round, which CS:DM's builders use: halves go up. Python's round() sends them to
    the even number, so (1002 + 1003) / 2 would land a tick early."""
    floor = math.floor(value)
    return floor + 1 if value - floor >= 0.5 else floor


def _ticks(tickrate: float, seconds: float) -> int:
    return _round_half_up(tickrate * seconds)


def build_sequences(
    inputs: RenderInputs,
    steamid: str,
    *,
    event: str = "kills",
    perspective: str = "player",
    rounds: tuple[int, ...] = (),
    padding_before_s: float,
    padding_after_s: float,
) -> list[Sequence]:
    """The Sequences `csdm video --mode player --steamids <steamid>` records for the same request, numbered
    from 1 and in start order. `event` is "kills" (a Sequence per Frag) or "rounds" (a whole round each);
    `rounds` limits it to those round numbers (none: every round); the padding is in seconds. Raises
    ValueError for any other event (CS:DM also has "deaths", which a Render Job never asks for) or
    perspective."""
    if perspective not in ("player", "enemy"):
        raise ValueError(f"unknown perspective: {perspective!r}")
    if event == "kills":
        sequences = _kills_sequences(inputs, steamid, perspective, rounds, padding_before_s, padding_after_s)
    elif event == "rounds":
        sequences = _rounds_sequences(inputs, steamid, rounds, padding_before_s, padding_after_s)
    else:
        raise ValueError(f"unsupported event: {event!r}")
    return sorted(sequences, key=lambda sequence: (sequence.start_tick, sequence.number))     # generate-video.ts


def _kills_sequences(
    inputs: RenderInputs,
    steamid: str,
    perspective: str,
    rounds: tuple[int, ...],
    padding_before_s: float,
    padding_after_s: float,
) -> list[Sequence]:
    kills = [kill for kill in inputs.kills
             if (not rounds or kill.round_number in rounds) and kill.killer_steam_id == steamid]
    notices = tuple(NoticePlayer(player.steam_id, player.name, player.steam_id == steamid)
                    for player in inputs.players)
    tickrate = inputs.tickrate
    before = _ticks(tickrate, padding_before_s)
    after = _ticks(tickrate, padding_after_s)
    merge_gap = _ticks(tickrate, MERGE_GAP_S)
    extend_within = _ticks(tickrate, EXTEND_WITHIN_S)

    drafts: list[_Draft] = []
    for index, kill in enumerate(kills):
        followed = kill.victim_steam_id if perspective == "enemy" and kill.victim_steam_id else kill.killer_steam_id
        start = max(1, kill.tick - before)
        end = min(inputs.tick_count, kill.tick + after)
        if index + 1 < len(kills) and kill.tick + extend_within >= kills[index + 1].tick:
            end = min(inputs.tick_count, kills[index + 1].tick + after)
        if drafts and drafts[-1].end + merge_gap >= start:
            # Overlapping: carry on into this Frag, looking at it from the midpoint between the two.
            drafts[-1].end = end
            midpoint = _round_half_up((kills[index - 1].tick + kill.tick) / 2)
            drafts[-1].cameras.append((midpoint, followed))
            continue
        drafts.append(_Draft(start, end, [(start, followed)]))
    return [Sequence(number, draft.start, draft.end, tuple(draft.cameras), notices)
            for number, draft in enumerate(drafts, start=1)]


def _rounds_sequences(
    inputs: RenderInputs,
    steamid: str,
    rounds: tuple[int, ...],
    padding_before_s: float,
    padding_after_s: float,
) -> list[Sequence]:
    """Each round from the end of its freeze time to its end, or to the subject's death. CS:DM neither
    clamps these to the demo nor rounds them (a padding that is not whole ticks leaves a fraction in the
    Clip's name); here they are whole ticks, rounded as the other builder does."""
    tickrate = inputs.tickrate
    sequences: list[Sequence] = []
    for round_ in inputs.rounds:
        if rounds and round_.number not in rounds:
            continue
        death = next((kill for kill in inputs.kills
                      if kill.round_number == round_.number and kill.victim_steam_id == steamid), None)
        start = _round_half_up(round_.freeze_time_end_tick - tickrate * padding_before_s)
        end = _round_half_up((death.tick if death else round_.end_tick) + tickrate * padding_after_s)
        sequences.append(Sequence(len(sequences) + 1, start, end, ((start, steamid),)))
    return sequences


def clip_name(sequence: Sequence) -> str:
    """The file CS:DM names the Clip of `sequence` (3.20.1, `get-sequence-output-file-path.ts`), which
    `render.CLIP_NAME` reads back."""
    return f"sequence-{sequence.number}-tick-{sequence.start_tick}-to-{sequence.end_tick}.mp4"


def raw_folder(raw_dir: Path, number: int) -> Path:
    """Where HLAE records Sequence `number`, named as CS:DM does (`get-sequence-name.ts`); `video.mp4` ends
    up inside."""
    return raw_dir / f"{number}-sequence"


@dataclass(frozen=True)
class _Step:
    """One cfg that a Sequence's cfg schedules for a tick."""

    tick: int
    name: str               # what the cfg's file name has after the Sequence's: cs2clipper_s1_<name>
    lines: tuple[str, ...]


def script(
    sequences: Iterable[Sequence],
    inputs: RenderInputs,
    *,
    demo_path: Path,
    raw_dir: Path,
    settings: VideoSettings = VideoSettings(),
) -> dict[str, str]:
    """The cfg files (file name -> text) that record `sequences`, one after the other and in the order given,
    from the Demo at `demo_path` into `raw_folder(raw_dir, number)`.

    CS2 starts with `+exec cs2clipper`, which plays the Demo and schedules `cs2clipper_go` for tick 96 (`playing`).
    That sends CS:DM's settings and runs the first Sequence's cfg. Each Sequence's cfg schedules its own steps with
    `mirv_cmd addAtTick`, every step a cfg of its own, and seeks to its approach when that is worth it: set the
    recording up (`ready`), aim the camera, start (`recording`), change the camera, end (`done`) and hand over to the
    next Sequence's cfg, or, after the last, quit (`quit`). The runner reads those markers from console.log, where
    CS2 names each cfg it runs (`parse_marker`).

    Raises PlanError for no Sequence, two with one number, one that ends when it starts or before, one with
    no room to be set up after the one before it (they overlap, or the first starts within the demo's first
    seconds), and a path or setting that cannot go inside quotes."""
    sequences = list(sequences)
    if not sequences:
        raise PlanError("there is nothing to record")
    if len({sequence.number for sequence in sequences}) < len(sequences):
        raise PlanError("two Sequences have the same number")
    demo = _quotable(str(demo_path), "the demo's path")
    _quotable(str(raw_dir), "the raw folder")
    for text in (settings.video_codec, settings.output_parameters, settings.container):
        _quotable(text, "a video setting")
    slots: dict[str, int] = {}
    for player in inputs.players:
        slots.setdefault(player.steam_id, player.slot)      # CS:DM looks a player up with `find`: the first

    files = {
        f"{ENTRY}.cfg": _cfg(
            "mirv_cmd clear",
            "mirv_cmd enabled 1",
            f"mirv_cmd addAtTick {FIRST_TICK} exec {ENTRY}_go",
            "demo_ui_mode 0",       # CS2 shows its playback bar unless this comes before the demo plays (CS:DM does the same)
            f'playdemo "{demo}"',
        ),
        f"{ENTRY}_go.cfg": _cfg(
            *_pinned_lines(settings),
            f"exec {_stem(sequences[0])}",
        ),
    }
    handoff = FIRST_TICK
    for index, sequence in enumerate(sequences):
        following = sequences[index + 1] if index + 1 < len(sequences) else None
        files.update(_sequence_files(sequence, following, handoff, inputs.tickrate, slots, raw_dir, settings))
        handoff = sequence.end_tick + _ticks(inputs.tickrate, HANDOFF_S)
    return files


def _sequence_files(
    sequence: Sequence,
    following: Sequence | None,
    handoff: int,
    tickrate: float,
    slots: dict[str, int],
    raw_dir: Path,
    settings: VideoSettings,
) -> dict[str, str]:
    """The cfg of `sequence` and the cfg of each of its steps. The game is at `handoff` when it runs."""
    number, start, end = sequence.number, sequence.start_tick, sequence.end_tick
    if end <= start:
        raise PlanError(f"Sequence {number} ends at tick {end}, which is not after its start at {start}")
    target = start - _ticks(tickrate, PREROLL_S)
    landing = max(handoff, target)
    seeks = target - handoff >= _ticks(tickrate, SEEK_MIN_S)
    opening, changes = _cameras(sequence, slots)
    aim = () if opening is None else _camera_lines(opening)

    folder = str(raw_folder(raw_dir, number)).replace("\\", "/")        # the way CS:DM writes it to HLAE
    steps = [_Step(landing + _ticks(tickrate, PREPARE_S), "prepare", _prepare_lines(sequence, folder, settings))]
    if opening is not None:
        steps.append(_Step(landing + _ticks(tickrate, CAMERA_S), "aim", aim))
    steps.append(_Step(start, "start", (*aim, "mirv_streams record start")))
    steps += [_Step(tick, f"camera{position}", _camera_lines(slot))
              for position, (tick, slot) in enumerate(changes, start=2)]
    steps.append(_Step(end, "end", ("mirv_streams record end",)))
    if following is None:
        steps.append(_Step(end + _ticks(tickrate, QUIT_S), "quit", ("quit",)))
    else:
        steps.append(_Step(end + _ticks(tickrate, HANDOFF_S), "next", (f"exec {_stem(following)}",)))
    ticks = [landing, *(step.tick for step in steps)]
    if any(before >= after for before, after in zip(ticks, ticks[1:])):
        raise PlanError(f"Sequence {number} (ticks {start} to {end}) leaves no room to set it up after tick {handoff}")

    stem = _stem(sequence)
    lines = ["mirv_cmd clear", *(f"mirv_cmd addAtTick {step.tick} exec {stem}_{step.name}" for step in steps)]
    if seeks:
        lines.append(f"demo_gototick {landing}")
    files = {f"{stem}.cfg": _cfg(*lines)}
    files.update({f"{stem}_{step.name}.cfg": _cfg(*step.lines) for step in steps})
    return files


def _stem(sequence: Sequence) -> str:
    return f"{ENTRY}_s{sequence.number}"


def _cfg(*lines: str) -> str:
    return "\n".join(lines) + "\n"


def _flag(on: bool) -> int:
    return 1 if on else 0


def _quotable(text: str, what: str) -> str:
    """`text` for the inside of quotes in a cfg line. A `"` would end them and a line break would end the
    line; a `;` is safe between quotes."""
    if '"' in text or _CONTROL.search(text):
        raise PlanError(f"{what} has a quote or a control character in it: {text!r}")
    return text


def _name(text: str) -> str:
    """A player's name for the inside of quotes. CS:DM sends names as they are and CS2 has no way to quote a
    `"` in one, so a name could end its quotes and start commands of its own; here `"` becomes `'`, `;` becomes
    `,` and control characters become spaces."""
    return _CONTROL.sub(" ", text.replace('"', "'").replace(";", ","))


def _camera_lines(slot: int) -> tuple[str, ...]:
    # spec_mode first, or spec_player may not work (CS:DM, json-actions-file-generator.ts)
    return "spec_mode 1", f"spec_player {slot}"


def _cameras(sequence: Sequence, slots: dict[str, int]) -> tuple[int | None, list[tuple[int, int]]]:
    """(the slot the camera follows as the Sequence starts, or None; the (tick, slot) of each later change).
    CS:DM sends nothing for a camera on a player the demo does not list. The cameras up to the start fold into
    one, the last of them; two on one tick are one too, the last; one at or after the end is left out."""
    located = sorted(((tick, slots[steam_id]) for tick, steam_id in sequence.cameras if steam_id in slots),
                     key=lambda camera: camera[0])
    opening = None
    later: dict[int, int] = {}
    for tick, slot in located:
        if tick <= sequence.start_tick:
            opening = slot
        elif tick < sequence.end_tick:
            later[tick] = slot
    return opening, list(later.items())


def _pinned_lines(settings: VideoSettings) -> list[str]:
    """What CS:DM sends at the first valid tick for every Sequence (3.20.1, `create-cs2-video-json-file.ts`).
    A Render Job pins the settings, so they are the same for all of them and are sent once."""
    voices = -1 if settings.player_voices_enabled else 0
    return [
        "sv_cheats 1",
        "volume 1",
        "cl_hud_telemetry_frametime_show 0",
        "cl_hud_telemetry_net_misdelivery_show 0",
        "cl_hud_telemetry_ping_show 0",
        "cl_hud_telemetry_serverrecvmargin_graph_show 0",
        "cl_trueview_show_status 0",
        "r_show_build_info 0",
        "mirv_streams record screen enabled 1",
        f"cl_demo_predict {_flag(settings.true_view)}",
        f"cl_draw_only_deathnotices {_flag(settings.show_only_death_notices)}",
        f"mirv_deathmsg lifetime {settings.death_notices_duration:g}",
        "mirv_deathmsg filter clear",
        f"tv_listen_voice_indices {voices}",
        f"tv_listen_voice_indices_h {voices}",
    ]


def _prepare_lines(sequence: Sequence, folder: str, settings: VideoSettings) -> tuple[str, ...]:
    """What CS:DM sends at a Sequence's setup tick, in its order, then the marker that says it is done."""
    number = sequence.number
    preset = f"{ENTRY}Preset{number}"       # CS:DM: csdmPreset<n>; one per Sequence, as it names its own folder
    quality = settings.output_parameters or f"-crf {settings.constant_rate_factor:g}"      # CS:DM: either, not both
    lines = [
        f"mirv_streams record startMovieWav {_flag(settings.record_audio)}",
        f'mirv_streams record name "{folder}"',
        "mirv_deathmsg clear",
        f"spec_show_xray {_flag(settings.show_xray)}",
        f"mp_display_kill_assists {_flag(settings.show_assists)}",
        # as CS:DM sends it: the quotes inside the preset are {QUOTE}, and two backslashes come before the file
        f'mirv_streams settings add ffmpeg {preset} "-c:v {settings.video_codec} -pix_fmt yuv420p {quality} '
        f'{{QUOTE}}{folder}\\\\video.{settings.container}{{QUOTE}}"',
        f"mirv_streams record screen settings {preset}",
        f"mirv_streams record fps {settings.framerate:g}",
        "mirv_deathmsg filter clear",
    ]
    # Whoever the Sequence names a notice player for is allowed; the rest is blocked. A player with nothing
    # to match the Frag's attacker by is left out (CS:DM matches by Steam ID, which is digits).
    notices = [notice for notice in sequence.notices if _DIGITS.fullmatch(notice.steam_id)]
    if notices:
        lines.append("mirv_deathmsg filter add block=1")
    for notice in notices:
        lines.append(f'mirv_replace_name byXuid add x{notice.steam_id} "{_name(notice.name)}"')
        lines.append(f"mirv_deathmsg filter add attackerMatch=x{notice.steam_id} "
                     f"attackerIsLocal={_flag(notice.highlight)} block=0")
    return tuple(lines)


def parse_marker(line: str) -> tuple[str, int | None] | None:
    """The marker a console line holds, as (kind, Sequence number or None), or None when it holds none. A marker is
    CS2 saying it runs a step file of the plan: `_go` is playing, and a Sequence's `_prepare`, `_start` and `_end` are
    ready, recording and done; `_quit` is quit. All but playing and quit name their Sequence."""
    found = _MARKER.search(line)
    if found is None:
        return None
    step = found[2]
    if step is None:
        return "playing", None
    if step == "quit":
        return "quit", None
    return _SEQUENCE_MARKERS[step], int(found[1])
