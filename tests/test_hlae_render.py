"""The HLAE render driver: one Render Job recorded by starting CS2 through HLAE.exe and following its console.log.

No game, HLAE or Steam is started: a scripted `World` stands in for the PC. Its clock moves only when the code under
test sleeps, and as it does the events scripted for the current launch fire (CS2 starting or closing, lines in
console.log, files in the raw folder). Every file is under tmp_path."""
from __future__ import annotations

import inspect
import itertools
import subprocess
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipper import hlae_render
from clipper.hlae_plan import Kill, Player, RenderInputs, Round
from clipper.media import MediaError
from clipper.render import ABORTED, RenderRequest
from tests.fixtures import MATCH_CHECKSUM, SUBJECT

ENEMY = "76561198000000011"
# Round 3 has two Frags close together (one Sequence with two cameras), round 12 one; round 14 is never asked for.
INPUTS = RenderInputs(
    tickrate=64.0,
    tick_count=200_000,
    kills=(Kill(20000, 3, SUBJECT, ENEMY), Kill(20200, 3, SUBJECT, ENEMY), Kill(76000, 12, SUBJECT, ENEMY),
           Kill(90000, 14, SUBJECT, ENEMY)),
    rounds=(Round(3, 21000, 15500), Round(12, 80000, 72500), Round(14, 94000, 90500)),
    players=(Player(SUBJECT, "Subject", 3), Player(ENEMY, "Enemy", 7)),
)
CLIP_1 = "sequence-1-tick-19744-to-20328.mp4"
CLIP_2 = "sequence-2-tick-75744-to-76128.mp4"


class HlaeProc:
    """HLAE.exe: running while the world's `hlae_code` is None."""

    def __init__(self, world):
        self.world = world

    def poll(self):
        return self.world.hlae_code

    def terminate(self):
        self.world.hlae_ended += 1
        self.world.hlae_code = 1


class World:
    """The PC as the render sees it, and its own probe, launcher, clock and FFmpeg. A launch's script is a list of
    (seconds after the launch, event name, arguments...), run by the `do_<event name>` methods; the events of a
    game that was killed do not happen, except the `late_` ones (HLAE starting a CS2 after it was told to stop)."""

    def __init__(self, tmp_path: Path, req: RenderRequest):
        self.req = req
        self.cs2_exe = tmp_path / "steam" / "game" / "bin" / "win64" / "cs2.exe"
        self.hlae_exe = tmp_path / "hlae" / "HLAE.exe"
        self.hlae_ffmpeg = tmp_path / "ffmpeg" / "bin" / "ffmpeg.exe"
        self.csgo = tmp_path / "steam" / "game" / "csgo"
        self.console = self.csgo / "console.log"
        self.cfg_dir = self.csgo / "cfg"
        self.raw_dir = req.output_dir / "raw"
        self.now = 1000.0
        self.steam = True
        self.inputs: RenderInputs | None = INPUTS
        self.cs2 = False                        # a hooked CS2 is running
        self.error_window = False
        self.hlae_code: int | None = None       # None while HLAE.exe runs
        self.abort_at: float | None = None
        self.abort_raises: Exception | None = None
        self.launch_error: Exception | None = None
        self.ffmpeg_failure: str | None = None
        self.makes_clips = True
        self.ffmpeg_busy = 0                    # how many more times an ffmpeg.exe is seen running when asked
        self.scripts: list[list[tuple]] = []    # the script of the first launch, of the second, ...
        self.before_look: list[tuple] = []      # events that happen just before the probe is next asked, once due
        # what happened
        self.kills = 0                          # hooked CS2s that were running when told to stop
        self.hlae_ended = 0
        self.launches: list[list[str]] = []
        self.launched_at: list[float] = []
        self.at_launch: list[dict] = []
        self.guarded: list[HlaeProc] = []
        self.asked_for: list[str] = []
        self.sleeps: list[float] = []
        self.ffmpeg_calls: list[list[str]] = []
        self.snapshots: list[str] = []
        self.trace: list[str] = []
        self._due: list[tuple] = []
        self._order = itertools.count()
        self._game = 0

    # --- what the render is given ---------------------------------------------------------------------

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        while True:
            due = sorted(event for event in self._due if event[0] <= self.now)
            if not due:
                return
            self._due.remove(due[0])
            _, _, _, name, args = due[0]
            getattr(self, f"do_{name}")(*args)

    def should_abort(self) -> bool:
        if self.abort_at is None or self.now < self.abort_at:
            return False
        if self.abort_raises is not None:
            raise self.abort_raises
        return True

    def running(self, name: str) -> bool:
        if name == "ffmpeg.exe":
            busy, self.ffmpeg_busy = self.ffmpeg_busy > 0, max(0, self.ffmpeg_busy - 1)
            return busy
        return self.steam if name == "steam.exe" else False

    def hooked_cs2_running(self) -> bool:
        due = [event for event in self.before_look if self.launched_at and self.now >= self.launched_at[-1] + event[0]]
        for event in due:
            self.before_look.remove(event)
            getattr(self, f"do_{event[1]}")(*event[2:])
        return self.cs2

    def kill_hooked_cs2(self) -> None:
        if self.cs2:
            self.kills += 1
        self.cs2 = False
        self._due = [event for event in self._due if event[3].startswith("late_") or event[2] != self._game]

    def load_inputs(self, checksum: str) -> RenderInputs | None:
        self.asked_for.append(checksum)
        return self.inputs

    def launch(self, command: list[str], log) -> HlaeProc:
        if self.launch_error is not None:
            raise self.launch_error
        self.trace.append("launch")
        self.launches.append(list(command))
        self.launched_at.append(self.now)
        self.at_launch.append(self._look())
        self.hlae_code = None
        self._game += 1
        index = len(self.launches) - 1
        for offset, name, *args in self.scripts[index] if index < len(self.scripts) else []:
            self._due.append((self.now + offset, next(self._order), self._game, name, tuple(args)))
        return HlaeProc(self)

    def guard(self, proc: HlaeProc) -> None:
        self.guarded.append(proc)

    def hlae_error_shown(self) -> bool:
        return self.error_window

    def run_ffmpeg(self, command, **kwargs):
        self.trace.append("mux" if not self.ffmpeg_busy else "mux while an ffmpeg.exe ran")
        self.ffmpeg_calls.append(list(command))
        if self.ffmpeg_failure is not None:
            return SimpleNamespace(returncode=1, stderr=self.ffmpeg_failure)
        if self.makes_clips:
            Path(command[-1]).write_bytes(b"clip")
        return SimpleNamespace(returncode=0, stderr="")

    @contextmanager
    def keep_awake(self):
        self.trace.append("awake")
        try:
            yield
        finally:
            self.trace.append("asleep")

    # --- what the game does ---------------------------------------------------------------------------

    def do_cs2_up(self) -> None:
        self.cs2 = True

    do_late_cs2_up = do_cs2_up

    def do_cs2_down(self) -> None:
        self.cs2 = False

    def do_hlae_exits(self, code: int) -> None:
        self.hlae_code = code

    def do_error_window(self) -> None:
        self.error_window = True

    def do_line(self, text: str) -> None:
        self.do_text(text + "\n")

    def do_text(self, text: str) -> None:
        self.console.parent.mkdir(parents=True, exist_ok=True)
        with self.console.open("ab") as handle:
            handle.write(text.encode("utf-8"))

    def do_rewrite(self, text: str) -> None:
        """CS2 starting its console.log over."""
        self.console.parent.mkdir(parents=True, exist_ok=True)
        self.console.write_bytes(text.encode("utf-8"))

    def do_record(self, number: int, size: int) -> None:
        """HLAE writing Sequence `number`'s video, which is `size` bytes by now, and its wav."""
        take = self.raw_dir / f"{number}-sequence" / "take0000"
        take.mkdir(parents=True, exist_ok=True)
        (take.parent / "video.mp4").write_bytes(b"v" * size)
        (take / "audio.wav").write_bytes(b"w")

    def do_snapshot(self) -> None:
        self.snapshots.append(self.req.log_path.read_text(encoding="utf-8"))

    # --- what the tests look at -----------------------------------------------------------------------

    def cfgs(self) -> list[str]:
        return sorted(path.name for path in self.cfg_dir.glob("cs2clipper*.cfg")) if self.cfg_dir.is_dir() else []

    def ini(self) -> str | None:
        ini = self.hlae_exe.parent / "ffmpeg" / "ffmpeg.ini"
        return ini.read_text(encoding="utf-8") if ini.is_file() else None

    def log(self) -> str:
        return self.req.log_path.read_text(encoding="utf-8")

    def _look(self) -> dict:
        """The state of the PC as HLAE.exe is started."""
        return {
            "cfgs": self.cfgs(),
            "ini": self.ini(),
            "raw_dir": self.raw_dir.exists(),
            "json": self.req.demo_path.with_name(self.req.demo_path.name + ".json").exists(),
            "cs2": self.cs2,
        }


def sequence_events(number: int, at: int) -> list[tuple]:
    """Sequence `number` recorded from `at` seconds after the launch, for ten seconds."""
    return [
        (at, "line", f"CS2CLIPPER ready {number}"),
        (at + 1, "record", number, 1000),
        (at + 1, "line", f"CS2CLIPPER recording {number}"),
        (at + 6, "record", number, 3000),
        (at + 10, "line", f"CS2CLIPPER done {number}"),
    ]


def game(numbers=(1, 2), *, quits=True, closes=True) -> list[tuple]:
    """A game that records the Sequences `numbers`, quits and closes: HLAE.exe exits at 2 s, CS2 is up at 4 s, the demo
    plays at 15 s and the Sequences follow every 15 s."""
    script = [(2, "hlae_exits", 0), (4, "cs2_up"), (15, "line", "CS2CLIPPER playing")]
    at = 17
    for number in numbers:
        script += sequence_events(number, at)
        at += 15
    if quits:
        script.append((at, "line", "CS2CLIPPER quit"))
        at += 2
    if closes:
        script.append((at, "cs2_down"))
    return script


@pytest.fixture
def req(tmp_path):
    return RenderRequest(
        demo_path=tmp_path / "demos" / "match.dem", perspective="player", rounds=(3, 12),
        output_dir=tmp_path / "out", log_path=tmp_path / "logs" / "render.log",
        steamid=SUBJECT, padding_before_s=4.0, padding_after_s=2.0, checksum=MATCH_CHECKSUM,
    )


@pytest.fixture
def world(tmp_path, req, monkeypatch):
    world = World(tmp_path, req)
    monkeypatch.setattr(hlae_render, "keep_awake", world.keep_awake)
    return world


def run(world, req, **overrides):
    options = {
        "probe": world, "should_abort": world.should_abort, "stall_seconds": 30.0, "launch_timeout_seconds": 60.0,
        "duration_of": lambda path: 4.0, "load_inputs": world.load_inputs, "cs2_exe": world.cs2_exe,
        "hlae_exe": world.hlae_exe, "hlae_ffmpeg": world.hlae_ffmpeg, "ffmpeg": "the-ffmpeg-to-mux-with",
        "launch": world.launch, "guard": world.guard, "hlae_error_shown": world.hlae_error_shown,
        "run_ffmpeg": world.run_ffmpeg, "clock": world.clock, "sleep": world.sleep, "poll_seconds": 1.0,
        "exit_grace_seconds": 10.0, "abort_sweep_seconds": 5.0,
    }
    options.update(overrides)
    return hlae_render.render(req, **options)


def assert_clean(world):
    """Whatever happened, no game or HLAE.exe is left running and no cfg file is left in CS2's folder."""
    assert not world.cs2
    assert world.hlae_code is not None or not world.launches
    assert world.cfgs() == []


def assert_not_started(world, result, failure):
    assert (result.ok, result.aborted, result.failure, result.clips) == (False, False, failure, ())
    assert world.launches == []
    assert world.cfgs() == []
    assert world.ini() is None
    assert f"failed: {failure}" in world.log()


# --- the happy path -----------------------------------------------------------------------------------


def test_two_sequences_become_two_clips_and_nothing_is_left_behind(world, req):
    world.scripts = [game()]
    result = run(world, req)
    assert result.ok and result.failure is None and not result.aborted
    assert [(clip.sequence, clip.start_tick, clip.end_tick, clip.path.name) for clip in result.clips] == [
        (1, 19744, 20328, CLIP_1), (2, 75744, 76128, CLIP_2)]
    assert all(clip.path.parent == req.output_dir and clip.path.is_file() and clip.duration_s == 4.0
               for clip in result.clips)
    assert not world.raw_dir.exists()
    assert_clean(world)
    assert len(world.launches) == 1 and world.kills == 0 and world.hlae_ended == 0


def test_the_raw_recordings_are_joined_with_the_ffmpeg_it_was_given(world, req):
    world.scripts = [game()]
    run(world, req)
    assert [(command[0], command[command.index("-i") + 1]) for command in world.ffmpeg_calls] == [
        ("the-ffmpeg-to-mux-with", str(world.raw_dir / "1-sequence" / "video.mp4")),
        ("the-ffmpeg-to-mux-with", str(world.raw_dir / "2-sequence" / "video.mp4")),
    ]
    assert [Path(command[-1]).name for command in world.ffmpeg_calls] == [CLIP_1, CLIP_2]


def test_the_request_for_the_plan_is_the_one_the_job_makes(world, req):
    world.scripts = [game([1])]
    result = run(world, replace(req, rounds=(12,)))
    assert world.asked_for == [MATCH_CHECKSUM]
    assert [clip.path.name for clip in result.clips] == ["sequence-1-tick-75744-to-76128.mp4"]


def test_the_defaults_are_the_ones_the_job_runs_with():
    parameters = inspect.signature(hlae_render.render).parameters
    assert [parameters[name].default for name in
            ("poll_seconds", "exit_grace_seconds", "abort_sweep_seconds", "max_launches")] == [5.0, 120.0, 30.0, 3]


# --- staging ------------------------------------------------------------------------------------------


def test_the_cfg_files_and_the_ffmpeg_ini_are_in_place_when_hlae_is_started(world, req):
    world.scripts = [game()]
    run(world, req)
    [seen] = world.at_launch
    assert {"cs2clipper.cfg", "cs2clipper_go.cfg", "cs2clipper_s1.cfg", "cs2clipper_s2.cfg",
            "cs2clipper_s2_quit.cfg"} <= set(seen["cfgs"])
    assert seen["ini"] == f"[Ffmpeg]\nPath={world.hlae_ffmpeg}"
    assert (world.cfg_dir / "cs2clipper.cfg").exists() is False        # and gone once the job is over
    assert world.cfgs() == []


def test_a_leftover_raw_folder_and_actions_file_are_removed_before_the_launch(world, req):
    world.raw_dir.mkdir(parents=True)
    (world.raw_dir / "1-sequence").mkdir()
    (world.raw_dir / "1-sequence" / "video.mp4").write_bytes(b"from an earlier attempt")
    actions = req.demo_path.with_name("match.dem.json")
    actions.parent.mkdir(parents=True)
    actions.write_text("{}", encoding="utf-8")
    world.scripts = [game()]
    result = run(world, req)
    assert result.ok
    [seen] = world.at_launch
    assert seen["raw_dir"] is False and seen["json"] is False
    assert not actions.exists()


def test_a_hooked_cs2_left_over_is_closed_before_the_launch(world, req):
    world.cs2 = True
    world.scripts = [game()]
    result = run(world, req)
    assert result.ok
    assert world.kills == 1 and world.sleeps[0] == 2.0
    assert world.at_launch[0]["cs2"] is False
    assert "intervention: a hooked CS2 was already running" in world.log()


def test_hlae_is_started_with_this_command_line(world, req):
    world.scripts = [game()]
    run(world, replace(req, width=1280, height=960))
    hlae = world.hlae_exe
    assert world.launches == [[
        str(hlae), "-noGui", "-autoStart", "-noConfig", "-afxDisableSteamStorage", "-customLoader",
        "-hookDllPath", str(hlae.parent / "x64" / "AfxHookSource2.dll"), "-programPath", str(world.cs2_exe),
        "-cmdLine", "-insecure -novid -condebug -width 1280 -height 960 -sw +exec cs2clipper",
    ]]
    assert len(world.guarded) == 1


def test_the_run_is_kept_awake_from_before_the_launch_until_after_the_mux(world, req):
    world.scripts = [game()]
    run(world, req)
    assert world.trace == ["awake", "launch", "mux", "mux", "asleep"]


# --- before the launch --------------------------------------------------------------------------------


def test_without_cs2_nothing_is_started(world, req):
    result = run(world, req, cs2_exe=None)
    assert_not_started(world, result, "CS2 not found: no cs2.exe in CS:DM's custom location or any Steam library")


def test_without_hlae_nothing_is_started(world, req):
    assert_not_started(world, run(world, req, hlae_exe=None), "HLAE not found")


def test_without_steam_nothing_is_started(world, req):
    world.steam = False
    assert_not_started(world, run(world, req), "Steam is not running")
    assert world.asked_for == []


def test_a_demo_csdm_has_not_analysed_is_not_rendered(world, req):
    world.inputs = None
    result = run(world, req)
    assert_not_started(world, result, "CS:DM's database has no analysis of this Demo")
    assert world.asked_for == [MATCH_CHECKSUM]


def test_a_request_with_nothing_to_record_is_not_rendered(world, req):
    result = run(world, replace(req, rounds=(99,)))
    assert_not_started(world, result, "nothing to record")


def test_a_plan_that_cannot_be_carried_out_is_not_rendered(world, req):
    world.inputs = replace(INPUTS, kills=(Kill(150, 3, SUBJECT, ENEMY),))       # no room to set up before it
    result = run(world, req)
    assert result.failure.startswith("plan failed: Sequence 1")
    assert world.launches == [] and world.cfgs() == [] and world.ini() is None


def test_an_event_the_plan_does_not_know_is_a_failed_plan(world, req):
    result = run(world, replace(req, event="deaths"))
    assert result.failure == "plan failed: unsupported event: 'deaths'"
    assert world.launches == []


def test_the_preconditions_are_checked_in_this_order(world, req):
    world.steam = False
    world.inputs = None
    assert run(world, req, cs2_exe=None, hlae_exe=None).failure.startswith("CS2 not found")
    assert run(world, req, hlae_exe=None).failure == "HLAE not found"
    assert run(world, req).failure == "Steam is not running"
    world.steam = True
    assert run(world, req).failure == "CS:DM's database has no analysis of this Demo"


@pytest.mark.parametrize("blocked", ["ini", "cfg"])
def test_a_file_that_cannot_be_written_fails_the_job_instead_of_crashing_it(world, req, blocked):
    if blocked == "ini":
        (world.hlae_exe.parent).mkdir(parents=True)
        (world.hlae_exe.parent / "ffmpeg").write_text("a file where HLAE's ffmpeg folder should be")
    else:
        world.csgo.mkdir(parents=True)
        world.cfg_dir.write_text("a file where CS2's cfg folder should be")
    world.scripts = [game()]
    result = run(world, req)
    assert not result.ok and result.failure.startswith("could not set up the HLAE files: ")
    assert world.launches == []
    assert "failed: could not set up the HLAE files" in world.log()


def test_hlae_that_cannot_be_started_fails_the_job(world, req):
    world.launch_error = FileNotFoundError(2, "The system cannot find the file specified")
    result = run(world, req)
    assert not result.ok and result.failure.startswith("HLAE could not be started: ")
    assert len(world.launches) == 0
    assert world.cfgs() == []


# --- the log ------------------------------------------------------------------------------------------


def test_the_log_tells_the_plan_the_launch_each_marker_and_the_mux(world, req):
    world.scripts = [game()]
    run(world, req)
    text = world.log()
    assert "plan: 2 Sequences" in text
    assert "Sequence 1: ticks 19744 to 20328, 2 cameras" in text
    assert "Sequence 2: ticks 75744 to 76128, 1 camera" in text
    assert f"launch 1 of 3: {subprocess.list2cmdline(world.launches[0])}" in text
    for marker in ("playing, 15.0", "ready 1, 17.0", "recording 1, 18.0", "done 1, 27.0", "ready 2, 32.0",
                   "recording 2, 33.0", "done 2, 42.0", "quit, 47.0"):
        assert f"marker {marker} s after launch" in text
    assert "mux: joining 2 Sequences" in text
    assert text.rstrip().endswith("done: 2 Clips")


def test_the_log_is_written_as_it_goes(world, req):
    world.scripts = [game() + [(15, "snapshot"), (16, "snapshot")]]
    run(world, req)
    before, after = world.snapshots
    assert "plan: 2 Sequences" in before and "launch 1 of 3: " in before
    assert "marker playing" not in before and "marker playing" in after
    assert "done: 2 Clips" not in after


def test_a_failure_puts_the_last_40_console_lines_in_the_log(world, req):
    lines = [(5 + i / 100, "line", f"console line {i}") for i in range(1, 61)]
    world.scripts = [[(2, "hlae_exits", 0), (4, "cs2_up"), (15, "line", "CS2CLIPPER playing"), *lines]]
    result = run(world, req)
    assert result.failure.startswith("stalled")
    text = world.log()
    assert "failed: stalled" in text
    assert "console line 60" in text and "console line 22" in text
    assert "console line 21\n" not in text and "console line 1\n" not in text


def test_a_failure_before_anything_was_written_says_so(world, req):
    world.scripts = [[(4, "cs2_up")]] * 3
    run(world, req)
    assert "nothing written to console.log since the launch" in world.log()


# --- starting again -----------------------------------------------------------------------------------


def test_a_game_that_never_plays_is_closed_and_started_again(world, req):
    world.scripts = [[(4, "cs2_up")], game()]       # the first: CS2 starts but the demo never plays; HLAE.exe stays
    result = run(world, req)
    assert result.ok
    assert len(world.launches) == 2
    assert world.launched_at[1] - world.launched_at[0] == 60.0
    assert world.kills == 1 and world.hlae_ended == 1
    assert world.at_launch[1]["cs2"] is False and world.at_launch[1]["cfgs"] == world.at_launch[0]["cfgs"]
    text = world.log()
    assert "intervention: no 'playing' in console.log after 60 s" in text
    assert "launch 2 of 3: " in text
    assert_clean(world)


def test_a_game_that_closes_before_it_plays_is_started_again_at_once(world, req):
    world.scripts = [[(4, "cs2_up"), (10, "cs2_down")], game()]
    result = run(world, req)
    assert result.ok and len(world.launches) == 2
    assert world.launched_at[1] - world.launched_at[0] == 10.0
    assert world.hlae_ended == 1
    assert "intervention: CS2 closed before 'playing'" in world.log()


def test_the_job_fails_when_every_launch_comes_to_nothing(world, req):
    world.scripts = [[(4, "cs2_up")]] * 3
    result = run(world, req)
    assert (result.ok, result.aborted) == (False, False)
    assert result.failure == "CS2 never started the demo: no 'playing' in console.log after 3 launches"
    assert len(world.launches) == 3 and world.kills == 3 and world.hlae_ended == 3
    assert_clean(world)


def test_how_many_launches_are_made_is_a_setting(world, req):
    world.scripts = [[(4, "cs2_up")]] * 3
    result = run(world, req, max_launches=2)
    assert result.failure == "CS2 never started the demo: no 'playing' in console.log after 2 launches"
    assert len(world.launches) == 2


def test_console_lines_from_before_the_launch_are_not_this_games(world, req):
    world.console.parent.mkdir(parents=True)
    world.console.write_text("CS2CLIPPER playing\nCS2CLIPPER done 1\nCS2CLIPPER quit\n", encoding="utf-8")
    world.scripts = [[(4, "cs2_up")]] * 3
    result = run(world, req)
    assert result.failure.startswith("CS2 never started the demo")
    assert "marker" not in world.log()


def test_half_a_line_left_by_one_launch_is_not_joined_to_the_next_launchs_first_line(world, req):
    world.scripts = [
        [(4, "cs2_up"), (5, "text", "CS2CLIPPER pla")],     # the game is closed in the middle of a line
        [(4, "cs2_up"), (6, "text", "ying\n")],             # the next game's first bytes
        [(4, "cs2_up")],
    ]
    result = run(world, req)
    assert result.failure.startswith("CS2 never started the demo")
    assert "marker" not in world.log()


# --- HLAE's own errors --------------------------------------------------------------------------------


def test_hlae_exiting_with_an_error_ends_the_job_without_a_new_launch(world, req):
    world.scripts = [[(3, "cs2_up"), (5, "hlae_exits", 3)]]
    result = run(world, req)
    assert (result.ok, result.aborted, result.failure) == (False, False, "HLAE error: HLAE.exe exited with code 3")
    assert len(world.launches) == 1 and world.kills == 1
    assert_clean(world)


def test_the_hlae_error_window_ends_the_job_and_closes_the_game(world, req):
    world.scripts = [[(2, "hlae_exits", 0), (3, "cs2_up"), (6, "error_window")]]
    result = run(world, req)
    assert result.failure == ("HLAE error: the 'Error - AfxHookSource' window opened "
                              "(often an HLAE older than this CS2 build)")
    assert len(world.launches) == 1 and world.kills == 1
    assert_clean(world)


def test_a_title_like_an_error_window_after_the_demo_plays_is_not_an_error(world, req):
    world.scripts = [game() + [(30, "error_window")]]
    assert run(world, req).ok


def test_hlae_exiting_with_an_error_after_the_game_quit_does_not_matter(world, req):
    world.scripts = [[*game(closes=False), (48, "hlae_exits", 1), (49, "cs2_down")]]
    result = run(world, req)
    assert result.ok


# --- while it records ---------------------------------------------------------------------------------


def test_a_recording_that_stops_making_progress_is_closed(world, req):
    world.scripts = [[(2, "hlae_exits", 0), (4, "cs2_up"), (15, "line", "CS2CLIPPER playing"),
                      (17, "line", "CS2CLIPPER ready 1")]]
    result = run(world, req)
    assert result.failure == "stalled: no progress for 30s after 'ready 1'"
    assert world.now - world.launched_at[0] == 47.0
    assert len(world.launches) == 1 and world.kills == 1
    assert "intervention: no progress for 30 s after 'ready 1'" in world.log()
    assert_clean(world)


def test_a_recording_that_keeps_writing_is_not_stalled_however_long_it_takes(world, req):
    growth = [(18 + 10 * n, "record", 1, 1000 * (n + 1)) for n in range(1, 8)]        # 28, 38, ... 88
    world.scripts = [[(2, "hlae_exits", 0), (4, "cs2_up"), (15, "line", "CS2CLIPPER playing"),
                      (17, "line", "CS2CLIPPER ready 1"), (18, "record", 1, 1000),
                      (18, "line", "CS2CLIPPER recording 1"), *growth, (100, "line", "CS2CLIPPER done 1"),
                      (101, "line", "CS2CLIPPER quit"), (103, "cs2_down")]]
    result = run(world, replace(req, rounds=(3,)))
    assert result.ok and [clip.path.name for clip in result.clips] == [CLIP_1]


def test_a_game_that_closes_while_recording_fails_the_job(world, req):
    world.scripts = [[(2, "hlae_exits", 0), (4, "cs2_up"), (15, "line", "CS2CLIPPER playing"),
                      (17, "line", "CS2CLIPPER ready 1"), (18, "record", 1, 1000),
                      (18, "line", "CS2CLIPPER recording 1"), (25, "cs2_down")]]
    result = run(world, req)
    assert result.failure == "CS2 closed before the recording finished (last marker: recording 1)"
    assert len(world.launches) == 1
    assert_clean(world)


def test_a_game_that_closes_after_the_last_sequence_but_before_quit_has_recorded_everything(world, req):
    world.scripts = [game(quits=False)]
    result = run(world, req)
    assert result.ok and len(result.clips) == 2
    assert "every Sequence is recorded" in world.log()


def test_a_game_that_finishes_just_before_it_is_looked_at_is_read_to_its_end(world, req):
    # Its last lines and its exit come between two polls, just before the probe answers that it is gone: what it wrote
    # is read after that answer, not before.
    script = [event for event in game() if event[0] < 42]
    world.scripts = [script]
    world.before_look = [(45, "line", "CS2CLIPPER done 2"), (45, "line", "CS2CLIPPER quit"), (45, "cs2_down")]
    result = run(world, req)
    assert result.ok and len(result.clips) == 2
    assert "marker quit, 45.0 s after launch" in world.log()
    assert "intervention" not in world.log()


def test_a_game_that_does_not_close_after_quit_is_closed_without_failing_the_job(world, req):
    world.scripts = [game(closes=False)]
    result = run(world, req)
    assert result.ok and len(result.clips) == 2
    assert world.kills == 1
    assert "intervention: CS2 still running 10 s after 'quit'" in world.log()
    assert_clean(world)


def test_a_game_that_closes_by_itself_after_quit_is_not_closed_again(world, req):
    world.scripts = [game()]
    run(world, req)
    assert world.kills == 0
    assert "intervention" not in world.log()


def test_hlae_still_running_when_the_game_is_over_is_ended(world, req):
    world.scripts = [[event for event in game() if event[1] != "hlae_exits"]]
    result = run(world, req)
    assert result.ok and world.hlae_ended == 1
    assert "intervention: HLAE.exe was still running" in world.log()


# --- aborting -----------------------------------------------------------------------------------------


def test_an_abort_closes_the_game_and_hlae_and_removes_the_cfgs(world, req):
    world.scripts = [[(4, "cs2_up"), (15, "line", "CS2CLIPPER playing")]]
    world.abort_at = world.now + 20
    result = run(world, req)
    assert (result.ok, result.aborted, result.failure, result.clips) == (False, True, ABORTED, ())
    assert world.kills == 1 and world.hlae_ended == 1
    assert_clean(world)


def test_an_abort_sweeps_for_a_game_that_hlae_starts_late(world, req):
    world.scripts = [[(2, "hlae_exits", 0), (4, "cs2_up"), (12, "late_cs2_up")]]
    world.abort_at = world.now + 10
    result = run(world, req, abort_sweep_seconds=5.0)
    assert result.aborted
    assert world.kills == 2
    assert world.now - world.launched_at[0] >= 15.0         # it watched for the 5 s it was given
    assert_clean(world)


def test_an_abort_is_asked_about_at_every_poll(world, req):
    asked = []
    world.scripts = [game()]

    def should_abort():
        asked.append(world.now - world.launched_at[0])
        return False

    run(world, req, should_abort=should_abort)
    assert asked[:4] == [0.0, 1.0, 2.0, 3.0] and asked[-1] == 49.0


# --- after the game -----------------------------------------------------------------------------------


def test_a_sequence_without_its_done_marker_fails_the_job(world, req):
    script = game()
    script.remove((42, "line", "CS2CLIPPER done 2"))
    world.scripts = [script]
    result = run(world, req)
    assert result.failure == "Sequence 2 never finished recording"
    assert world.ffmpeg_calls == []
    assert_clean(world)


def test_the_mux_waits_for_hlaes_ffmpeg_to_finish_the_last_video(world, req):
    world.scripts = [game()]
    world.ffmpeg_busy = 3                   # still writing the end of the last video when CS2 has gone
    result = run(world, req)
    assert result.ok
    assert "mux while an ffmpeg.exe ran" not in world.trace
    assert world.trace.count("mux") == 2
    assert "waiting for HLAE's FFmpeg to finish the recordings" in world.log()


def test_an_ffmpeg_that_does_not_end_holds_the_mux_back_only_so_long(world, req, monkeypatch):
    monkeypatch.setattr(hlae_render, "FFMPEG_WAIT_S", 5.0)
    world.scripts = [game()]
    world.ffmpeg_busy = 10**6               # another program's FFmpeg looks the same
    result = run(world, req)
    assert result.ok
    assert "an ffmpeg.exe was still running after 5 s; joining the recordings anyway" in world.log()


def test_a_failed_mux_fails_the_job(world, req):
    world.ffmpeg_failure = "Invalid data found when processing input"
    world.scripts = [game()]
    result = run(world, req)
    assert result.failure == ("mux failed: ffmpeg failed on Sequence 1 (exit code 1): "
                              "Invalid data found when processing input")
    assert result.clips == ()
    assert_clean(world)
    assert "failed: mux failed" in world.log()


def test_a_missing_recording_fails_the_job_as_a_mux_failure(world, req):
    script = [event for event in game() if event[1] != "record" or event[2] != 2]
    world.scripts = [script]
    result = run(world, req)
    assert result.failure.startswith("mux failed: Sequence 2 has no recording folder")


def test_a_clip_that_cannot_be_read_fails_the_job(world, req):
    def unreadable(path):
        raise MediaError(f"ffprobe cannot read {path.name}")

    world.scripts = [game()]
    result = run(world, req, duration_of=unreadable)
    assert result.failure == f"unreadable clip: ffprobe cannot read {CLIP_1}"
    assert not world.raw_dir.exists()
    assert_clean(world)


def test_a_mux_that_made_no_clip_fails_the_job(world, req):
    world.makes_clips = False
    world.scripts = [game()]
    result = run(world, req)
    assert result.failure == "no sequence-*.mp4 in the output folder"
    assert_clean(world)


# --- reading console.log ------------------------------------------------------------------------------


def test_a_console_log_that_starts_over_is_read_from_its_start(world, req):
    world.console.parent.mkdir(parents=True)
    world.console.write_text("a game from before\n" * 100, encoding="utf-8")      # longer than the new one will be
    world.scripts = [[(3, "rewrite", "Loading map\n"), *game()]]
    result = run(world, req)
    assert result.ok
    assert "marker playing, 15.0 s after launch" in world.log()


def test_a_line_that_is_still_being_written_waits_for_its_end(world, req):
    script = [event for event in game() if event[1:] != ("line", "CS2CLIPPER playing")]
    world.scripts = [[*script, (15, "text", "CS2CLIPPER pla"), (16, "text", "ying\n"),
                      (20, "text", "CS2CLIPPER ready 1"), (21, "text", "2\n")]]
    result = run(world, req)
    assert result.ok
    text = world.log()
    assert "marker playing, 16.0 s after launch" in text
    assert "marker ready 12, 21.0 s after launch" in text
    assert "marker ready 1, 20.0" not in text


def test_lines_that_end_in_crlf_are_read(world, req):
    script = [event for event in game() if event[1:] != ("line", "CS2CLIPPER playing")]
    world.scripts = [[*script, (15, "text", "CS2CLIPPER playing\r\n")]]
    assert run(world, req).ok


# --- when something unexpected happens ----------------------------------------------------------------


def test_an_error_in_the_watch_closes_the_game_and_hlae_and_is_raised_again(world, req):
    world.scripts = [[(4, "cs2_up"), (15, "line", "CS2CLIPPER playing")]]
    world.abort_at = world.now + 10
    world.abort_raises = RuntimeError("a bug")
    with pytest.raises(RuntimeError, match="a bug"):
        run(world, req)
    assert world.kills == 1 and world.hlae_ended == 1
    assert_clean(world)


def test_the_cfg_files_are_removed_even_when_the_job_ends_in_an_error(world, req):
    world.scripts = [[(4, "cs2_up")]]
    world.abort_at = world.now + 10
    world.abort_raises = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run(world, req)
    assert world.cfgs() == []


# --- the real defaults --------------------------------------------------------------------------------


def test_the_error_window_is_looked_for_in_the_title_of_cs2s_window(monkeypatch):
    asked = {}

    def tasklist(command, **kwargs):
        asked["command"] = command
        return SimpleNamespace(stdout="cs2.exe  4242 Console  1  1,200,000 K  Running  PC\\me  0:01:09  "
                                      "Error - AfxHookSource\r\n")

    monkeypatch.setattr(hlae_render, "subprocess", SimpleNamespace(run=tasklist, CREATE_NO_WINDOW=0x08000000,
                                                                  TimeoutExpired=subprocess.TimeoutExpired))
    assert hlae_render._hlae_error_shown() is True
    assert asked["command"] == ["tasklist", "/fi", "imagename eq cs2.exe", "/v", "/nh"]


@pytest.mark.parametrize("stdout", ["cs2.exe  4242 Console  1  1,200,000 K  Running  PC\\me  0:01:09  N/A\r\n",
                                    "INFO: No tasks are running which match the specified criteria.\r\n"])
def test_a_cs2_window_without_the_error_title_is_not_an_error(monkeypatch, stdout):
    def tasklist(command, **kwargs):
        return SimpleNamespace(stdout=stdout)

    monkeypatch.setattr(hlae_render, "subprocess", SimpleNamespace(run=tasklist, CREATE_NO_WINDOW=0,
                                                                  TimeoutExpired=subprocess.TimeoutExpired))
    assert hlae_render._hlae_error_shown() is False


@pytest.mark.parametrize("error", [OSError("no tasklist"), subprocess.TimeoutExpired("tasklist", 15)])
def test_a_tasklist_that_cannot_answer_is_no_error(monkeypatch, error):
    def tasklist(command, **kwargs):
        raise error

    monkeypatch.setattr(hlae_render, "subprocess", SimpleNamespace(run=tasklist, CREATE_NO_WINDOW=0,
                                                                  TimeoutExpired=subprocess.TimeoutExpired))
    assert hlae_render._hlae_error_shown() is False


def test_hlae_is_started_without_a_window_and_its_output_goes_to_the_log(monkeypatch):
    started = {}

    def popen(command, **kwargs):
        started.update(command=command, **kwargs)
        return "the process"

    monkeypatch.setattr(hlae_render, "subprocess", SimpleNamespace(
        Popen=popen, CREATE_NO_WINDOW=0x08000000, STDOUT=subprocess.STDOUT, DEVNULL=subprocess.DEVNULL))
    assert hlae_render._launch(["HLAE.exe", "-noGui"], "the log") == "the process"
    assert started["command"] == ["HLAE.exe", "-noGui"]
    assert started["stdout"] == "the log" and started["stderr"] == subprocess.STDOUT
    assert started["creationflags"] == 0x08000000
