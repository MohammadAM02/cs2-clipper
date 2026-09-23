"""One `csdm video` call per Render Job, watched until it ends (spec: Rendering)."""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from clipper.csdm_cli import CsdmCli
from clipper.media import MediaError
from clipper.model import ClipFile
from clipper.procs import ProcessProbe
from clipper.windows import keep_awake

CLIP_NAME = re.compile(r"^sequence-(\d+)-tick-(\d+)-to-(\d+)\.mp4$", re.IGNORECASE)
FAILURE_MARKERS = (
    "password authentication failed",
    "ECONNREFUSED",
    "Game error",
    "Invalid argument",
    "does not exist",
)
ABORTED = "aborted: FACEIT AC started during the render"
NEVER_LAUNCHED = "csdm never launched CS2"


@dataclass(frozen=True)
class RenderRequest:
    demo_path: Path
    perspective: str            # "player" or "enemy"
    rounds: tuple[int, ...]
    output_dir: Path
    log_path: Path
    steamid: str
    padding_before_s: float
    padding_after_s: float
    event: str = "kills"        # "kills" (a Sequence per Frag) or "rounds" (whole rounds)
    width: int = 1920
    height: int = 1080


@dataclass(frozen=True)
class RenderResult:
    ok: bool
    aborted: bool = False
    failure: str | None = None
    clips: tuple[ClipFile, ...] = ()


def video_args(req: RenderRequest) -> list[str]:
    return [
        "video", str(req.demo_path),
        "--mode", "player",
        "--steamids", req.steamid,
        "--event", req.event,
        "--rounds", ",".join(str(number) for number in req.rounds),
        "--perspective", req.perspective,
        "--width", str(req.width),
        "--height", str(req.height),
        "--output", str(req.output_dir),
        "--close-game-after-recording",
        "--start-seconds-before", f"{req.padding_before_s:g}",
        "--end-seconds-after", f"{req.padding_after_s:g}",
    ]


def find_clips(output_dir: Path, duration_of: Callable[[Path], float]) -> list[ClipFile]:
    """The Clips CS:DM wrote, in Tick order."""
    clips = []
    for path in output_dir.iterdir():
        match = CLIP_NAME.match(path.name)
        if match:
            clips.append(ClipFile(sequence=int(match[1]), start_tick=int(match[2]), end_tick=int(match[3]),
                                  path=path, duration_s=duration_of(path)))
    return sorted(clips, key=lambda clip: clip.start_tick)


def judge(log_text: str, output_dir: Path, duration_of: Callable[[Path], float]) -> RenderResult:
    """Decide from csdm's output and files, never its exit code: csdm exits 0 when it fails."""
    lowered = log_text.lower()
    for marker in FAILURE_MARKERS:
        if marker.lower() in lowered:
            return RenderResult(ok=False, failure=f"csdm reported: {marker}")
    if "video generated" not in lowered:
        return RenderResult(ok=False, failure="csdm did not report 'Video generated'")
    try:
        clips = find_clips(output_dir, duration_of)
    except MediaError as exc:
        return RenderResult(ok=False, failure=f"unreadable clip: {exc}")
    if not clips:
        return RenderResult(ok=False, failure="no sequence-*.mp4 in the output folder")
    return RenderResult(ok=True, clips=tuple(clips))


def render(
    req: RenderRequest,
    *,
    csdm: CsdmCli,
    probe: ProcessProbe,
    should_abort: Callable[[], bool],
    stall_seconds: float,
    launch_timeout_seconds: float,
    duration_of: Callable[[Path], float],
    poll_seconds: float = 5.0,
    exit_grace_seconds: float = 120.0,
    abort_sweep_seconds: float = 30.0,
) -> RenderResult:
    req.output_dir.mkdir(parents=True, exist_ok=True)   # csdm aborts if the folder does not exist
    req.log_path.parent.mkdir(parents=True, exist_ok=True)
    with keep_awake(), open(req.log_path, "wb") as log_file:
        proc = subprocess.Popen(
            csdm.command(*video_args(req)), env=csdm.env(), stdout=log_file,
            stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        intervention, aborted = _watch(proc, probe, should_abort, stall_seconds,
                                       launch_timeout_seconds, poll_seconds, exit_grace_seconds)
        if intervention is not None:
            _sweep_hooked_cs2(probe, abort_sweep_seconds if aborted else 0.0, poll_seconds)
    if aborted:
        return RenderResult(ok=False, aborted=True, failure=intervention)
    if intervention is not None:
        return RenderResult(ok=False, failure=intervention)
    return judge(req.log_path.read_text(encoding="utf-8", errors="replace"), req.output_dir, duration_of)


def _watch(
    proc: subprocess.Popen,
    probe: ProcessProbe,
    should_abort: Callable[[], bool],
    stall_seconds: float,
    launch_timeout_seconds: float,
    poll_seconds: float,
    exit_grace_seconds: float,
) -> tuple[str | None, bool]:
    """Wait for csdm to exit, stepping in only as the spec allows: close the hooked CS2 on a stall or
    when FACEIT AC appears; stop csdm itself only when no hooked CS2 exists, so nothing is orphaned.
    Returns (why the app stepped in or None, whether it was an abort)."""
    started = time.monotonic()
    cs2_since: float | None = None
    last_ffmpeg: float | None = None
    intervened_at = 0.0
    intervention: str | None = None
    aborted = False
    own: set[tuple[int, float]] = set()
    while proc.poll() is None:
        own |= probe.children_named(proc.pid, "cs-demo-manager.exe")
        now = time.monotonic()
        cs2 = probe.hooked_cs2_running()
        if cs2:
            if cs2_since is None:
                cs2_since = now
            if probe.running("ffmpeg.exe"):
                last_ffmpeg = now
        if intervention is None:
            if should_abort():
                intervention, aborted, intervened_at = ABORTED, True, now
            elif cs2 and now - (last_ffmpeg or cs2_since) >= stall_seconds:
                intervention = f"stalled: no ffmpeg for {stall_seconds:g}s while CS2 ran"
                intervened_at = now
            elif cs2_since is None and now - started >= launch_timeout_seconds:
                intervention, intervened_at = NEVER_LAUNCHED, now
        if intervention is not None:
            if cs2:
                probe.kill_hooked_cs2()           # never stop csdm while its game runs
            elif aborted or cs2_since is None or now - intervened_at >= exit_grace_seconds:
                proc.terminate()                  # no hooked CS2 exists, so nothing is orphaned
        time.sleep(poll_seconds)
    probe.kill_processes(own)                     # CS:DM helpers our csdm call left behind
    return intervention, aborted


def _sweep_hooked_cs2(probe: ProcessProbe, seconds: float, poll_seconds: float) -> None:
    """After stepping in, close any hooked CS2 still around; after an abort HLAE may start one late."""
    deadline = time.monotonic() + seconds
    while True:
        if probe.hooked_cs2_running():
            probe.kill_hooked_cs2()
        if time.monotonic() >= deadline:
            return
        time.sleep(poll_seconds)
