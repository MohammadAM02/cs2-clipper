"""A Render Job, recorded by starting CS2 through HLAE.exe (ADR 0002).

The plan of `hlae_plan` goes into CS2's cfg folder as files and HLAE.exe starts CS2 with `+exec cs2clipper`. CS2 names
each cfg it runs in console.log (it starts with `-condebug`), and the names of the plan's step files are its markers;
`render` follows them to know where the recording is, closes the game when it stalls or never starts, starts it again
when that can help, joins what HLAE recorded into the Clips, and tells the caller how far it has got (RenderProgress).

It starts only what it is handed -- HLAE.exe (`launch`) and FFmpeg (`run_ffmpeg`) -- and the probe, the clock and
`sleep` are parameters too, so tests run a whole Render Job against a scripted world."""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import IO, Literal

from clipper import cs2_paths, hlae_files, hlae_plan, winjob
from clipper.hlae_files import MuxError
from clipper.hlae_plan import PlanError, RenderInputs, Sequence
from clipper.media import MediaError
from clipper.model import ClipFile
from clipper.procs import HOOKED_FLAG, ProcessProbe
from clipper.render import ABORTED, RenderProgress, RenderRequest, RenderResult, find_clips
from clipper.windows import keep_awake

CONSOLE_TAIL_LINES = 40         # how many of console.log's last lines go into the log when a Render Job fails
LEFTOVER_PAUSE_S = 2.0          # after closing a hooked CS2 an earlier run left, so it lets go of its files
HLAE_ERROR_TITLE = "Error - AfxHookSource"      # the title of the window HLAE opens when it cannot hook this CS2
FFMPEG_WAIT_S = 60.0            # the longest the mux waits for HLAE's FFmpeg to finish the videos after the game
SETTINGS = hlae_plan.VideoSettings()            # what the cfg files record with, which the mux has to expect too
# CS2 keeps its video settings, convars and keys in this folder when the variable names one, instead of the player's
# Steam userdata (CS:DM's `define-cfg-folder-location.ts`); HLAE's -afxDisableSteamStorage is meant to go with it.
USRLOCAL_VARIABLE = "USRLOCALCSGO"


def _launch(command: list[str], log: IO[str], environment: dict[str, str]) -> subprocess.Popen:
    """Starts HLAE.exe without a window, with `environment`, which the CS2 it starts gets too; whatever it prints goes
    into the log."""
    return subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=environment,
                            creationflags=subprocess.CREATE_NO_WINDOW)


def _hlae_error_shown() -> bool:
    """Whether a CS2 has HLAE's error window open: `tasklist` lists a window's title with its process. A tasklist that
    cannot answer is a no."""
    try:
        done = subprocess.run(["tasklist", "/fi", "imagename eq cs2.exe", "/v", "/nh"], capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=15,
                              creationflags=subprocess.CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return HLAE_ERROR_TITLE in (done.stdout or "")


def _command(hlae_exe: Path, cs2_exe: Path, width: int, height: int) -> list[str]:
    """HLAE.exe's command line: no window, CS2 started with the hook on at once, and CS2's own flags -- the one that
    marks it as ours, console.log, a window of the size of the Clips, and the plan."""
    return [
        str(hlae_exe), "-noGui", "-autoStart", "-noConfig", "-afxDisableSteamStorage", "-customLoader",
        "-hookDllPath", str(hlae_exe.parent / "x64" / "AfxHookSource2.dll"), "-programPath", str(cs2_exe),
        "-cmdLine", f"{HOOKED_FLAG} -novid -condebug -width {width} -height {height} -sw +exec {hlae_plan.ENTRY}",
    ]


def _plural(count: int, one: str, many: str | None = None) -> str:
    return f"{count} {one if count == 1 else many or one + 's'}"


def _tree_size(folder: Path) -> int:
    """The total size of the files under `folder`: what HLAE has written so far. A file that goes while the folder is
    counted is not counted."""
    total = 0
    try:
        for path in folder.rglob("*"):
            try:
                if path.is_file():
                    total += path.stat().st_size
            except OSError:
                pass
    except OSError:
        pass
    return total


class _Console:
    """console.log, where CS2 names each cfg of the plan it runs. It is read from where it ended at the
    launch, so what an earlier game wrote is not this one's, and a line still being written waits for its end."""

    def __init__(self, path: Path):
        self.path = path
        self.offset = 0
        self.partial = b""
        self.recent: deque[str] = deque(maxlen=CONSOLE_TAIL_LINES)

    def begin(self) -> None:
        """A launch is about to start: what is in the file now is not its."""
        try:
            self.offset = self.path.stat().st_size
        except OSError:
            self.offset = 0
        self.partial = b""
        self.recent.clear()

    def read(self) -> list[str]:
        """The lines completed since the last call. A file that is shorter than what was read has been started over
        (CS2 does when it starts), so it is read from its beginning."""
        try:
            size = self.path.stat().st_size
            if size < self.offset:
                self.offset, self.partial = 0, b""
            if size == self.offset:
                return []
            with self.path.open("rb") as handle:
                handle.seek(self.offset)
                chunk = handle.read(size - self.offset)
        except OSError:
            return []
        self.offset += len(chunk)
        *whole, self.partial = (self.partial + chunk).split(b"\n")
        lines = [line.decode("utf-8", errors="replace").rstrip("\r") for line in whole]
        self.recent.extend(lines)
        return lines

    def tail(self) -> list[str]:
        """The last lines read since the launch, and the line still being written after them."""
        lines = list(self.recent)
        if self.partial:
            lines.append(self.partial.decode("utf-8", errors="replace"))
        return lines[-CONSOLE_TAIL_LINES:]


@dataclass(frozen=True)
class _Env:
    """What every launch of a Render Job shares: the PC to look at and act on, the clock, the log, and the limits."""

    probe: ProcessProbe
    should_abort: Callable[[], bool]
    hlae_error_shown: Callable[[], bool]
    launch: Callable[[list[str], IO[str], dict[str, str]], subprocess.Popen]
    environment: dict[str, str]         # HLAE.exe's and CS2's: the app's, with CS2's settings in their own folder
    guard: Callable[[subprocess.Popen], None]
    clock: Callable[[], float]
    sleep: Callable[[float], None]
    say: Callable[[str], None]
    log: IO[str]
    console: _Console
    raw_dir: Path
    numbers: frozenset[int]             # the Sequences the plan records
    views: tuple[tuple[str, tuple[Sequence, ...]], ...]     # each view's Perspective and Sequences, in recording order
    progress: Callable[[RenderProgress], None]              # told how far the launch has got; it does not raise
    stall_seconds: float
    launch_timeout_seconds: float
    poll_seconds: float
    exit_grace_seconds: float


@dataclass
class _Launch:
    """One start of HLAE.exe, and what has been seen of the game it starts."""

    proc: subprocess.Popen
    started: float
    last_progress: float
    playing: bool = False               # a marker has come, so the demo plays
    seen: bool = False                  # a hooked CS2 has been running
    ended: bool = False                 # HLAE.exe has been told to end
    quit_at: float | None = None        # when the 'quit' marker came
    first_ready: float | None = None    # when the first 'ready' marker came: the pace of the launch counts from it
    last_marker: str = ""
    raw_size: int = 0                   # the size of the raw folder when it was last looked at
    done: set[int] = field(default_factory=set)


@dataclass(frozen=True)
class _Outcome:
    """How a launch ended: the game recorded and quit ("finished", with the Sequences it finished), the Render Job
    failed or was aborted, or the launch came to nothing and another may help ("retry")."""

    kind: Literal["finished", "failed", "aborted", "retry"]
    text: str = ""                      # why it failed
    done: frozenset[int] = frozenset()


def _end_hlae(env: _Env, game: _Launch) -> None:
    """HLAE.exe exits once it has started CS2; one that is still there is ended."""
    if game.ended or game.proc.poll() is not None:
        return
    game.ended = True
    env.say("intervention: HLAE.exe was still running; ended it")
    try:
        game.proc.terminate()
    except OSError:
        pass


def _stop(env: _Env, game: _Launch, why: str) -> None:
    """Steps in: closes the hooked CS2 and ends HLAE.exe, so that nothing is left running."""
    env.say(f"intervention: {why}")
    env.probe.kill_hooked_cs2()
    _end_hlae(env, game)


def _sweep(env: _Env, seconds: float) -> None:
    """After a stop HLAE may still start a CS2: for `seconds`, close any hooked one that shows up."""
    deadline = env.clock() + seconds
    while True:
        if env.probe.hooked_cs2_running():
            env.say("intervention: a hooked CS2 started after the stop; closed it")
            env.probe.kill_hooked_cs2()
        if env.clock() >= deadline:
            return
        env.sleep(env.poll_seconds)


def _length(sequences: Iterable[Sequence]) -> int:
    """How many ticks `sequences` record in all."""
    return sum(sequence.end_tick - sequence.start_tick for sequence in sequences)


def _view_of(env: _Env, number: int) -> str | None:
    """The view that records the Sequence `number`, or None when the plan has no such Sequence."""
    return next((name for name, sequences in env.views for sequence in sequences if sequence.number == number), None)


def _report(env: _Env, game: _Launch, stage: str, perspective: str, now: float) -> None:
    """Tells the caller how far the launch has got, for the view `perspective`. Once a Sequence is done, the time left
    is estimated at the pace of the launch so far (from its first 'ready' to `now`, per tick recorded), for the ticks
    of every Sequence not recorded yet, in both views."""
    everything = [sequence for _, sequences in env.views for sequence in sequences]
    mine = next(sequences for name, sequences in env.views if name == perspective)
    done_ticks = _length(sequence for sequence in everything if sequence.number in game.done)
    total_ticks = _length(everything)
    seconds_left = None
    if game.done and game.first_ready is not None and done_ticks > 0:
        seconds_left = (now - game.first_ready) / done_ticks * (total_ticks - done_ticks)
    env.progress(RenderProgress(stage, perspective, done=sum(sequence.number in game.done for sequence in mine),
                                total=len(mine), overall=done_ticks / total_ticks, seconds_left=seconds_left))


def _note(env: _Env, game: _Launch, kind: str, number: int | None, now: float) -> None:
    """A marker has come. Any marker means the plan got going, even if `playing` was lost."""
    text = kind if number is None else f"{kind} {number}"
    env.say(f"marker {text}, {now - game.started:.1f} s after launch")
    game.playing = True
    game.last_marker, game.last_progress = text, now
    if kind == "ready" and game.first_ready is None:
        game.first_ready = now
    if kind == "done" and number is not None:
        game.done.add(number)
        view = _view_of(env, number)
        if view is not None:
            _report(env, game, "recording", view, now)
    elif kind == "again" and len(env.views) > 1:
        _report(env, game, "restarting", env.views[1][0], now)     # the second view starts from its first Sequence
    elif kind == "quit":
        game.quit_at = now


def _watch(env: _Env, game: _Launch) -> _Outcome:
    """Follows one launch until the game has recorded, the Render Job has failed or been aborted, or the launch has come
    to nothing. Steps in only to close the game: on a stall, an error, a stop, or a game that does not close itself."""
    def failed(text: str) -> _Outcome:
        return _Outcome("failed", text, done=frozenset(game.done))      # what was recorded before still counts

    while True:
        now = env.clock()
        if env.should_abort():
            _stop(env, game, "asked to stop (FACEIT AC, a quit or a delete)")
            return _Outcome("aborted")
        # Asked before console.log is read: a game that writes 'quit' and exits in between is then known to have
        # written everything it did, and what it wrote is read below.
        running = env.probe.hooked_cs2_running()
        game.seen = game.seen or running
        for line in env.console.read():
            marker = hlae_plan.parse_marker(line)
            if marker is not None:
                _note(env, game, *marker, now)
        if game.playing:
            size = _tree_size(env.raw_dir)
            if size > game.raw_size:
                game.last_progress = now        # HLAE is writing video
            game.raw_size = size

        code = game.proc.poll()
        if code not in (None, 0) and game.quit_at is None:
            _stop(env, game, f"HLAE.exe exited with code {code}")
            return failed(f"HLAE error: HLAE.exe exited with code {code}")
        if not game.playing:
            if env.hlae_error_shown():
                _stop(env, game, f"the '{HLAE_ERROR_TITLE}' window is open")
                return failed(f"HLAE error: the '{HLAE_ERROR_TITLE}' window opened "
                              f"(often an HLAE older than this CS2 build)")
            if now - game.started >= env.launch_timeout_seconds:
                _stop(env, game, f"no 'playing' in console.log after {env.launch_timeout_seconds:g} s")
                return _Outcome("retry")
            if game.seen and not running:
                _stop(env, game, "CS2 closed before 'playing'")
                return _Outcome("retry")
        elif game.quit_at is not None:
            if not running:
                return _Outcome("finished", done=frozenset(game.done))
            if now - game.quit_at >= env.exit_grace_seconds:
                _stop(env, game, f"CS2 still running {env.exit_grace_seconds:g} s after 'quit'")
                return _Outcome("finished", done=frozenset(game.done))
        elif not running:
            if env.numbers <= game.done:        # the 'quit' line may not have reached the disk before the game went
                env.say("CS2 closed before its 'quit' marker, but every Sequence is recorded")
                return _Outcome("finished", done=frozenset(game.done))
            return failed(f"CS2 closed before the recording finished (last marker: {game.last_marker})")
        elif now - game.last_progress >= env.stall_seconds:
            _stop(env, game, f"no progress for {env.stall_seconds:g} s after '{game.last_marker}'")
            return failed(f"stalled: no progress for {env.stall_seconds:g}s after '{game.last_marker}'")
        env.sleep(env.poll_seconds)


def _wait_for_ffmpeg(env: _Env, seconds: float) -> None:
    """HLAE's FFmpeg finishes a video file after its recording ends, so it may still be writing the last one when CS2
    has gone. The mux waits for it, but at most `seconds`: another program's ffmpeg.exe looks the same."""
    deadline = env.clock() + seconds
    said = False
    while env.probe.running("ffmpeg.exe"):
        if env.clock() >= deadline:
            env.say(f"an ffmpeg.exe was still running after {seconds:g} s; joining the recordings anyway")
            return
        if not said:
            env.say("waiting for HLAE's FFmpeg to finish the recordings")
            said = True
        env.sleep(env.poll_seconds)


def _launch_once(env: _Env, command: list[str], number: int, total: int) -> _Outcome:
    """Starts HLAE.exe once and watches the game it starts. Whatever happens, no game or HLAE.exe is left running."""
    if env.probe.hooked_cs2_running():
        env.say("intervention: a hooked CS2 was already running; closing it")
        env.probe.kill_hooked_cs2()
        env.sleep(LEFTOVER_PAUSE_S)
    env.console.begin()
    env.say(f"launch {number} of {total}: {subprocess.list2cmdline(command)}")
    try:
        proc = env.launch(command, env.log, env.environment)
    except OSError as exc:
        return _Outcome("failed", f"HLAE could not be started: {exc}")
    started = env.clock()
    game = _Launch(proc, started, last_progress=started)
    _report(env, game, "starting", env.views[0][0], started)
    try:
        env.guard(proc)
        return _watch(env, game)
    except BaseException:
        _stop(env, game, "the Render Job ended with an error")
        raise
    finally:
        _end_hlae(env, game)


def _record(env: _Env, command: list[str], max_launches: int) -> _Outcome:
    """Starts HLAE.exe until a launch gets the demo playing, at most `max_launches` times."""
    for number in range(1, max_launches + 1):
        outcome = _launch_once(env, command, number, max_launches)
        if outcome.kind != "retry":
            return outcome
    return _Outcome("failed", f"CS2 never started the demo: no 'playing' in console.log after "
                              f"{_plural(max_launches, 'launch', 'launches')}")


def _prepare(req: RenderRequest, raw_dir: Path, hlae_exe: Path, hlae_ffmpeg: Path, cfgs: Path,
             files: dict[str, str], cs2_settings_dir: Path) -> None:
    """Gets the PC ready for a launch: a folder for the Clips of each view, no recording of an earlier attempt, HLAE
    told where FFmpeg is, the cfg files in CS2's folder, and a folder for CS2's settings."""
    for output in req.outputs.values():
        output.mkdir(parents=True, exist_ok=True)
    cs2_settings_dir.mkdir(parents=True, exist_ok=True)
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    hlae_files.ensure_ffmpeg_ini(hlae_exe, hlae_ffmpeg)
    hlae_files.write_cfgs(cfgs, files)


def _remove_cfgs(say: Callable[[str], None], cfgs: Path) -> None:
    """The cfg files do not stay in CS2's folder once the Render Job is over. Not being able to remove them is said, not
    raised, so it cannot hide what ended the job."""
    try:
        hlae_files.remove_cfgs(cfgs)
    except OSError as exc:
        say(f"could not remove the cfg files from {cfgs}: {exc}")


def render(
    req: RenderRequest,
    *,
    probe: ProcessProbe,
    should_abort: Callable[[], bool],
    stall_seconds: float,
    launch_timeout_seconds: float,
    duration_of: Callable[[Path], float],
    load_inputs: Callable[[str], RenderInputs | None],
    cs2_exe: Path | None,
    hlae_exe: Path | None,
    hlae_ffmpeg: Path | None,
    ffmpeg: str,
    cs2_settings_dir: Path,
    launch: Callable[[list[str], IO[str], dict[str, str]], subprocess.Popen] = _launch,
    guard: Callable[[subprocess.Popen], None] = winjob.guard,
    hlae_error_shown: Callable[[], bool] = _hlae_error_shown,
    run_ffmpeg: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    poll_seconds: float = 5.0,
    exit_grace_seconds: float = 120.0,
    abort_sweep_seconds: float = 30.0,
    max_launches: int = 3,
    progress: Callable[[RenderProgress], None] = lambda progress: None,
) -> RenderResult:
    """Records the Clips of `req`'s views through HLAE, or says why it could not. The views are recorded in one launch
    of CS2, the second after the first (`hlae_plan.script`). `cs2_exe`, `hlae_exe` and `hlae_ffmpeg`, the FFmpeg HLAE
    records with, are where they were found (None: nowhere); `load_inputs` gives what the plan needs from the analysis
    of a Demo's match, by its checksum, and `ffmpeg` is the FFmpeg that joins the recordings. CS2 keeps its settings in
    `cs2_settings_dir` while it records, so the player's own are left as they are. What happens goes into
    `req.log_path` as it happens. A launch that fails once the first view is recorded keeps that view's Clips in the
    result. `progress` is told how far the launch has got; one that raises is said once in the log, and the job goes
    on."""
    req.log_path.parent.mkdir(parents=True, exist_ok=True)
    with keep_awake(), open(req.log_path, "w", encoding="utf-8", errors="replace", newline="\n") as log_file:

        def say(text: str) -> None:
            log_file.write(text + "\n")
            log_file.flush()                # the Status page shows the log while the Render Job runs

        progress_failed = False

        def report(update: RenderProgress) -> None:
            """Hands `update` to the caller. A callback that raises is said once, and the Render Job goes on."""
            nonlocal progress_failed
            try:
                progress(update)
            except Exception as exc:  # noqa: BLE001 - a progress report must not fail the Render Job
                if not progress_failed:
                    say(f"progress report failed: {exc}")
                progress_failed = True

        def fail(text: str, console: _Console | None = None,
                 clips: dict[str, tuple[ClipFile, ...]] | None = None) -> RenderResult:
            say(f"failed: {text}")
            if console is not None:
                lines = console.tail()
                if lines:
                    say(f"the last {_plural(len(lines), 'line')} of console.log since the launch:")
                    for line in lines:
                        say(f"    {line}")
                else:
                    say("nothing written to console.log since the launch")
            return RenderResult(ok=False, failure=text, clips=clips or {})

        def join(views: list[tuple[str, list[Sequence]]]) -> tuple[dict[str, tuple[ClipFile, ...]], str | None]:
            """Joins what HLAE recorded for each of `views`, in order, into the Clips of its output folder. Returns the
            Clips of the views joined and what stopped the join, or None when nothing did. The raw folder goes once
            every mux has worked: a mux that fails keeps it, as the only copy of what was recorded."""
            joined: list[str] = []
            problem: str | None = None
            for perspective, sequences in views:
                say(f"mux: joining {_plural(len(sequences), 'Sequence')} of the {perspective} view into Clips")
                try:
                    hlae_files.mux(ffmpeg, raw_dir, sequences, req.outputs[perspective],
                                   record_audio=SETTINGS.record_audio, run=run_ffmpeg)
                except MuxError as exc:
                    problem = f"mux failed: {exc}"
                    break
                joined.append(perspective)
            if problem is None:
                shutil.rmtree(raw_dir, ignore_errors=True)
            clips: dict[str, tuple[ClipFile, ...]] = {}
            for perspective in joined:
                try:
                    found = find_clips(req.outputs[perspective], duration_of)
                except MediaError as exc:
                    return clips, f"unreadable clip: {exc}"
                if not found:
                    return clips, "no sequence-*.mp4 in the output folder"
                for clip in found:
                    say(f"  {clip.path.name}, {clip.duration_s:.1f} s")
                clips[perspective] = tuple(found)
            return clips, problem

        if cs2_exe is None:
            return fail("CS2 not found: no cs2.exe in any Steam library")
        if hlae_exe is None:
            return fail("HLAE not found")
        if hlae_ffmpeg is None:
            return fail("FFmpeg not found")
        if not probe.running("steam.exe"):
            return fail("Steam is not running")
        inputs = load_inputs(req.checksum)
        if inputs is None:
            return fail("the app has no analysis of this Demo")
        raw_dir = req.outputs[req.perspectives[0]] / "raw"        # the first view's folder keeps the raw recordings
        planned: list[tuple[str, list[Sequence]]] = []          # each view's Sequences, in the order they are recorded
        try:
            for perspective in req.perspectives:
                sequences = hlae_plan.build_sequences(
                    inputs, req.steamid, event=req.event, perspective=perspective, rounds=req.rounds,
                    padding_before_s=req.padding_before_s, padding_after_s=req.padding_after_s)
                if not sequences:
                    return fail("nothing to record")
                if planned:         # the second view's Sequences are numbered on from the first view's
                    offset = len(planned[0][1])
                    sequences = [replace(sequence, number=sequence.number + offset) for sequence in sequences]
                planned.append((perspective, sequences))
            files = hlae_plan.script(
                planned[0][1], inputs, demo_path=req.demo_path, raw_dir=raw_dir, settings=SETTINGS,
                second_pass=[sequence for _, sequences in planned[1:] for sequence in sequences])
        except (PlanError, ValueError) as exc:
            return fail(f"plan failed: {exc}")
        all_sequences = [sequence for _, sequences in planned for sequence in sequences]
        for index, (perspective, sequences) in enumerate(planned):
            if index == 0:
                say(f"plan: {_plural(len(sequences), 'Sequence')} of the {perspective} view")
            else:
                say(f"then {_plural(len(sequences), 'Sequence')} of the {perspective} view, "
                    f"after the demo starts again")
            for sequence in sequences:
                say(f"  Sequence {sequence.number}: ticks {sequence.start_tick} to {sequence.end_tick}, "
                    f"{_plural(len(sequence.cameras), 'camera')}")

        cfgs = cs2_paths.cfg_dir(cs2_exe)
        try:
            _prepare(req, raw_dir, hlae_exe, hlae_ffmpeg, cfgs, files, cs2_settings_dir)
        except OSError as exc:
            _remove_cfgs(say, cfgs)         # a few of the files may be in place
            return fail(f"could not set up the HLAE files: {exc}")
        console = _Console(cs2_paths.console_log(cs2_exe))
        env = _Env(
            probe=probe, should_abort=should_abort, hlae_error_shown=hlae_error_shown, launch=launch,
            environment={**os.environ, USRLOCAL_VARIABLE: str(cs2_settings_dir)}, guard=guard,
            clock=clock, sleep=sleep, say=say, log=log_file, console=console, raw_dir=raw_dir,
            numbers=frozenset(sequence.number for sequence in all_sequences),
            views=tuple((perspective, tuple(sequences)) for perspective, sequences in planned), progress=report,
            stall_seconds=stall_seconds, launch_timeout_seconds=launch_timeout_seconds, poll_seconds=poll_seconds,
            exit_grace_seconds=exit_grace_seconds,
        )
        try:
            outcome = _record(env, _command(hlae_exe, cs2_exe, req.width, req.height), max_launches)
        finally:
            _remove_cfgs(say, cfgs)
        if outcome.kind == "aborted":
            _sweep(env, abort_sweep_seconds)
            say("stopped: the Render Job was asked to stop")
            return RenderResult(ok=False, aborted=True, failure=ABORTED)
        missing = [sequence.number for sequence in all_sequences if sequence.number not in outcome.done]
        failure: str | None = None
        if outcome.kind == "failed":
            failure = outcome.text
        elif missing:
            failure = f"Sequence {missing[0]} never finished recording"
        # a launch that failed after the first view was recorded whole keeps that view's Clips
        whole_first = len(planned) == 2 and all(sequence.number in outcome.done for sequence in planned[0][1])
        if failure is not None and not whole_first:
            return fail(failure, console)
        views = planned if failure is None else planned[:1]         # a launch that failed joins its first view alone
        last_perspective, last_sequences = views[-1]
        report(RenderProgress("joining", last_perspective, done=len(last_sequences), total=len(last_sequences),
                              overall=1.0))
        _wait_for_ffmpeg(env, FFMPEG_WAIT_S)
        clips, problem = join(views)
        if failure is not None:
            first, second = planned[0][0], planned[1][0]
            if problem is None:
                say(f"kept: {_plural(len(clips[first]), 'Clip')} of the {first} view; "
                    f"the {second} view was not recorded")
            else:
                say(f"the {first} view was not kept: {problem}")
            return fail(failure, console, clips)
        if problem is not None:
            return fail(problem, console, clips)
        say(f"done: {_plural(sum(len(found) for found in clips.values()), 'Clip')}")
        return RenderResult(ok=True, clips=clips)
