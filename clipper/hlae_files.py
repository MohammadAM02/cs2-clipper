"""The files around a recording through HLAE: the cfg files CS2 runs, the ini that tells HLAE where FFmpeg is,
and the join of what HLAE recorded into Clips.

Ports of three things in CS Demo Manager 3.20.1: ``registerFfmpegLocation`` (``start-counter-strike-with-hlae.ts``),
``getHlaeRawFiles`` (``get-sequence-raw-files.ts``, HLAE's video output) and the HLAE video branch of
``generateVideoWithFFmpeg`` (``generate-video-with-ffmpeg.ts``). Starts no game and no HLAE; the one program it
runs is FFmpeg, and `mux` takes the way to run it so that tests need none.

The MIT License (MIT)

Copyright (c) 2014-present AkiVer

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""
from __future__ import annotations

import subprocess
from collections.abc import Callable, Iterable
from pathlib import Path

from clipper import hlae_plan
from clipper.hlae_plan import Sequence

MUX_TIMEOUT_S = 300     # copying a Sequence's video and encoding its audio takes seconds, not minutes
STDERR_LINES = 4        # how many of FFmpeg's last lines go into the error


class MuxError(Exception):
    """A Sequence's recording is not all there, or FFmpeg could not join it into its Clip."""


def remove_cfgs(cfg_dir: Path) -> None:
    """Deletes the cfg files of a plan (``cs2clipper*.cfg``) from `cfg_dir`, and nothing else in it. A folder that
    is not there has none."""
    for cfg in cfg_dir.glob(f"{hlae_plan.ENTRY}*.cfg"):
        if cfg.is_file():
            cfg.unlink()


def write_cfgs(cfg_dir: Path, files: dict[str, str]) -> None:
    """Puts the cfg files of a plan (file name -> text, from `hlae_plan.script`) in `cfg_dir`, CS2's cfg folder, after
    removing the ones an earlier plan left. They are UTF-8, with the line breaks the plan gives them."""
    cfg_dir.mkdir(parents=True, exist_ok=True)
    remove_cfgs(cfg_dir)
    for name, text in files.items():
        (cfg_dir / name).write_text(text, encoding="utf-8", newline="\n")


def ensure_ffmpeg_ini(hlae_exe: Path, ffmpeg_exe: Path) -> None:
    """Tells HLAE where FFmpeg is: ``ffmpeg/ffmpeg.ini`` next to hlae.exe, written the way CS:DM does. Not written
    again when it already says so."""
    ini = hlae_exe.parent / "ffmpeg" / "ffmpeg.ini"
    content = f"[Ffmpeg]\nPath={ffmpeg_exe}".encode("utf-8")      # LF, and no line break at the end
    try:
        if ini.read_bytes() == content:
            return
    except FileNotFoundError:
        pass
    ini.parent.mkdir(parents=True, exist_ok=True)
    ini.write_bytes(content)


def raw_files(
    raw_dir: Path, number: int, *, container: str = "mp4", record_audio: bool = True
) -> tuple[Path, Path | None]:
    """The video HLAE recorded for Sequence `number` and, when audio was recorded, its wav: the files of
    ``hlae_plan.raw_folder``, as CS:DM looks for them,

        <n>-sequence/video.<container>
        <n>-sequence/take0000/audio.wav        (the last take, when there are several)

    Raises MuxError naming what is missing: the folder, the video, the take folder or the wav. The take folder is
    only looked for when the audio is wanted."""
    folder = hlae_plan.raw_folder(raw_dir, number)
    if not folder.is_dir():
        raise MuxError(f"Sequence {number} has no recording folder: {folder}")
    video = folder / f"video.{container}"
    if not video.is_file():
        raise MuxError(f"Sequence {number} has no {video.name} in {folder}")
    if not record_audio:
        return video, None
    takes = sorted(path for path in folder.glob("take*") if path.is_dir())
    if not takes:
        raise MuxError(f"Sequence {number} has no take folder in {folder}")
    wav = takes[-1] / "audio.wav"
    if not wav.is_file():
        raise MuxError(f"Sequence {number} has no {wav.name} in {takes[-1]}")
    return video, wav


def mux_command(ffmpeg: str | Path, video: Path, wav: Path | None, out: Path) -> list[str]:
    """FFmpeg's command line for one Clip: the video copied as it is and, when there is a wav, the audio encoded as
    MP3 (CS:DM's default for it) at 256 kbit/s."""
    if wav is None:
        return [str(ffmpeg), "-y", "-i", str(video), "-map", "0:v:0", "-c", "copy", str(out)]
    return [str(ffmpeg), "-y", "-i", str(video), "-i", str(wav), "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "libmp3lame", "-b:a", "256k", str(out)]


def _run_ffmpeg(command: list[str], number: int, run: Callable[..., subprocess.CompletedProcess]) -> None:
    """Runs `command`, which joins Sequence `number`. MuxError when FFmpeg cannot start, takes too long or fails."""
    try:
        done = run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=MUX_TIMEOUT_S, creationflags=subprocess.CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise MuxError(f"ffmpeg took more than {MUX_TIMEOUT_S} seconds on Sequence {number}") from exc
    except OSError as exc:
        raise MuxError(f"ffmpeg could not be run for Sequence {number}: {exc}") from exc
    if done.returncode != 0:
        said = [line.strip() for line in (done.stderr or "").splitlines() if line.strip()]
        tail = f": {' | '.join(said[-STDERR_LINES:])}" if said else ""
        raise MuxError(f"ffmpeg failed on Sequence {number} (exit code {done.returncode}){tail}")


def mux(
    ffmpeg: str | Path,
    raw_dir: Path,
    sequences: Iterable[Sequence],
    output_dir: Path,
    *,
    record_audio: bool = True,
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[Path]:
    """Joins what HLAE recorded for each of `sequences` (see `raw_files`) into its Clip, ``output_dir /
    hlae_plan.clip_name(sequence)``, and returns the Clips in the order given. Raises MuxError at the first
    Sequence that was not recorded or that FFmpeg cannot join; the Clips before it stay."""
    output_dir.mkdir(parents=True, exist_ok=True)       # FFmpeg fails when the folder of its output is not there
    clips: list[Path] = []
    for sequence in sequences:
        video, wav = raw_files(raw_dir, sequence.number, record_audio=record_audio)
        clip = output_dir / hlae_plan.clip_name(sequence)
        try:
            _run_ffmpeg(mux_command(ffmpeg, video, wav, clip), sequence.number, run)
        except MuxError:
            clip.unlink(missing_ok=True)        # FFmpeg starts its output before it knows it can finish
            raise
        clips.append(clip)
    return clips
