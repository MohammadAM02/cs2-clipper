"""Tests for clipper.hlae_files: the cfg files, the FFmpeg ini and the join of HLAE's raw recording into Clips.
Folders are fakes under tmp_path and FFmpeg is a stand-in for subprocess.run: nothing here starts a program or
touches CS2's or HLAE's real folders."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from clipper import hlae_files, hlae_plan
from clipper.hlae_plan import RenderInputs, Sequence

FFMPEG = Path(r"C:\Users\someone\AppData\Local\CS2Clipper\tools\ffmpeg\bin\ffmpeg.exe")


def sequence(number: int, start: int = 1000, end: int = 2000) -> Sequence:
    return Sequence(number=number, start_tick=start, end_tick=end, cameras=())


def record(raw_dir: Path, number: int, *, takes=("take0000",), audio=True, container="mp4") -> Path:
    """What HLAE leaves for a Sequence: video.<container>, and in each take an audio.wav holding the take's name."""
    folder = hlae_plan.raw_folder(raw_dir, number)
    folder.mkdir(parents=True)
    (folder / f"video.{container}").write_bytes(b"video")
    for take in takes:
        (folder / take).mkdir()
        if audio:
            (folder / take / "audio.wav").write_bytes(take.encode())
    return folder


def mux_error(call, *args, **kwargs) -> str:
    with pytest.raises(hlae_files.MuxError) as error:
        call(*args, **kwargs)
    return str(error.value)


class FakeFfmpeg:
    """Stands in for subprocess.run: keeps the commands it was given and starts the output file the way ffmpeg does,
    even when it then fails or is cut short."""

    def __init__(self, returncode: int = 0, stderr: str = "", raises: BaseException | None = None):
        self.calls: list[tuple[list[str], dict]] = []
        self.returncode, self.stderr, self.raises = returncode, stderr, raises

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        output = Path(command[-1])
        assert output.parent.is_dir(), "ffmpeg fails when the output folder is not there"
        output.write_bytes(b"clip")
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(command, self.returncode, stdout="", stderr=self.stderr)


def test_the_plans_cfg_files_are_written_as_utf8_with_the_line_breaks_they_came_with(tmp_path):
    hlae_files.write_cfgs(tmp_path, {"cs2clipper.cfg": "mirv_cmd clear\nexec cs2clipper_go\n",
                                     "cs2clipper_s1_prepare.cfg": 'mirv_replace_name byXuid add x1 "Zoë"\n'})

    assert (tmp_path / "cs2clipper.cfg").read_bytes() == b"mirv_cmd clear\nexec cs2clipper_go\n"
    assert (tmp_path / "cs2clipper_s1_prepare.cfg").read_bytes() == 'mirv_replace_name byXuid add x1 "Zoë"\n'.encode()


def test_cfg_files_an_earlier_run_left_are_removed_before_the_new_ones_are_written(tmp_path):
    (tmp_path / "cs2clipper.cfg").write_text("old entry")
    (tmp_path / "cs2clipper_s7_prepare.cfg").write_text("a Sequence the new plan does not have")

    hlae_files.write_cfgs(tmp_path, {"cs2clipper.cfg": "new entry"})

    assert sorted(path.name for path in tmp_path.iterdir()) == ["cs2clipper.cfg"]
    assert (tmp_path / "cs2clipper.cfg").read_text() == "new entry"


def test_writing_cfg_files_leaves_every_other_file_in_the_folder_alone(tmp_path):
    others = {"autoexec.cfg": "bind", "cs2.cfg": "fps_max", "mycs2clipper.cfg": "x", "cs2clipper.txt": "y"}
    for name, text in others.items():
        (tmp_path / name).write_text(text)

    hlae_files.write_cfgs(tmp_path, {"cs2clipper.cfg": "entry"})

    assert {name: (tmp_path / name).read_text() for name in others} == others


def test_every_cfg_file_the_plan_makes_is_one_that_removing_cfg_files_deletes(tmp_path):
    cfg = tmp_path / "cfg"
    plan = hlae_plan.script(
        [sequence(1, 1000, 2000), sequence(2, 3000, 4000)],
        RenderInputs(tickrate=64.0, tick_count=10_000, kills=(), rounds=(), players=()),
        demo_path=tmp_path / "demo.dem", raw_dir=tmp_path / "raw")

    hlae_files.write_cfgs(cfg, plan)
    written = sorted(path.name for path in cfg.iterdir())
    hlae_files.remove_cfgs(cfg)

    assert written == sorted(plan) and len(plan) > 2
    assert list(cfg.iterdir()) == []


def test_writing_cfg_files_makes_the_cfg_folder_when_it_is_missing(tmp_path):
    cfg = tmp_path / "game" / "csgo" / "cfg"

    hlae_files.write_cfgs(cfg, {"cs2clipper.cfg": "entry"})

    assert (cfg / "cs2clipper.cfg").read_text() == "entry"


def test_removing_cfg_files_deletes_the_plans_and_nothing_else(tmp_path):
    for name in ("cs2clipper.cfg", "cs2clipper_go.cfg", "cs2clipper_s1_prepare.cfg", "CS2CLIPPER_s2.CFG"):
        (tmp_path / name).write_text("plan")
    for name in ("autoexec.cfg", "cs2clipper.log", "other_cs2clipper.cfg"):
        (tmp_path / name).write_text("not ours")
    (tmp_path / "cs2clipper_folder.cfg").mkdir()

    hlae_files.remove_cfgs(tmp_path)

    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "autoexec.cfg", "cs2clipper.log", "cs2clipper_folder.cfg", "other_cs2clipper.cfg"]


def test_removing_cfg_files_from_a_folder_that_is_not_there_does_nothing(tmp_path):
    hlae_files.remove_cfgs(tmp_path / "no such folder")


def test_the_ffmpeg_ini_goes_in_the_ffmpeg_folder_next_to_hlae_and_names_ffmpeg(tmp_path):
    hlae_exe = tmp_path / "HLAE" / "HLAE.exe"

    hlae_files.ensure_ffmpeg_ini(hlae_exe, FFMPEG)

    ini = (tmp_path / "HLAE" / "ffmpeg" / "ffmpeg.ini").read_bytes()
    assert ini == b"[Ffmpeg]\nPath=" + str(FFMPEG).encode()        # as CS:DM writes it: LF, no line break at the end


def test_an_ffmpeg_ini_that_already_says_so_is_not_written_again(tmp_path):
    hlae_exe = tmp_path / "HLAE.exe"
    hlae_files.ensure_ffmpeg_ini(hlae_exe, FFMPEG)
    ini = tmp_path / "ffmpeg" / "ffmpeg.ini"
    long_ago = 1_000_000_000_000_000_000
    os.utime(ini, ns=(long_ago, long_ago))

    hlae_files.ensure_ffmpeg_ini(hlae_exe, FFMPEG)

    assert ini.stat().st_mtime_ns == long_ago


def test_an_ffmpeg_ini_that_names_another_ffmpeg_is_rewritten(tmp_path):
    hlae_exe = tmp_path / "HLAE.exe"
    ini = tmp_path / "ffmpeg" / "ffmpeg.ini"
    ini.parent.mkdir()
    ini.write_bytes(b"[Ffmpeg]\nPath=D:\\old\\ffmpeg.exe")

    hlae_files.ensure_ffmpeg_ini(hlae_exe, FFMPEG)

    assert ini.read_bytes() == b"[Ffmpeg]\nPath=" + str(FFMPEG).encode()


def test_the_raw_files_of_a_sequence_are_its_video_and_the_wav_of_the_last_take(tmp_path):
    folder = record(tmp_path, 3, takes=("take0002", "take0000", "take0001"))

    video, wav = hlae_files.raw_files(tmp_path, 3)

    assert video == folder / "video.mp4"
    assert wav == folder / "take0002" / "audio.wav"


def test_the_video_is_named_for_the_container(tmp_path):
    folder = record(tmp_path, 1, container="mkv")

    video, _ = hlae_files.raw_files(tmp_path, 1, container="mkv")

    assert video == folder / "video.mkv"


def test_without_audio_there_is_no_wav_and_no_take_folder_is_needed(tmp_path):
    folder = record(tmp_path, 1, takes=())

    assert hlae_files.raw_files(tmp_path, 1, record_audio=False) == (folder / "video.mp4", None)


def test_a_sequence_without_a_folder_is_a_mux_error_that_names_the_folder(tmp_path):
    record(tmp_path, 1)

    message = mux_error(hlae_files.raw_files, tmp_path, 2)

    assert "Sequence 2" in message and "recording folder" in message
    assert str(hlae_plan.raw_folder(tmp_path, 2)) in message


def test_a_folder_without_the_video_is_a_mux_error_that_names_the_video(tmp_path):
    folder = record(tmp_path, 2)
    (folder / "video.mp4").unlink()

    message = mux_error(hlae_files.raw_files, tmp_path, 2)

    assert "Sequence 2" in message and "video.mp4" in message and str(folder) in message


def test_audio_without_a_take_folder_is_a_mux_error_that_names_the_take_folder(tmp_path):
    folder = record(tmp_path, 2, takes=())
    (folder / "take0000").write_bytes(b"a file, not a folder")

    message = mux_error(hlae_files.raw_files, tmp_path, 2)

    assert "Sequence 2" in message and "take folder" in message and str(folder) in message


def test_a_last_take_without_a_wav_is_a_mux_error_that_names_the_wav(tmp_path):
    folder = record(tmp_path, 2, takes=("take0000", "take0001"))
    (folder / "take0001" / "audio.wav").unlink()

    message = mux_error(hlae_files.raw_files, tmp_path, 2)

    assert "Sequence 2" in message and "audio.wav" in message and str(folder / "take0001") in message


def test_the_command_that_joins_video_and_audio_copies_the_video_and_encodes_the_audio_as_mp3():
    command = hlae_files.mux_command(Path("ffmpeg.exe"), Path("v.mp4"), Path("a.wav"), Path("o.mp4"))

    assert command == ["ffmpeg.exe", "-y", "-i", "v.mp4", "-i", "a.wav", "-map", "0:v:0", "-map", "1:a:0",
                       "-c:v", "copy", "-c:a", "libmp3lame", "-b:a", "256k", "o.mp4"]


def test_the_command_for_a_video_without_audio_copies_everything():
    command = hlae_files.mux_command("ffmpeg", Path("v.mp4"), None, Path("o.mp4"))

    assert command == ["ffmpeg", "-y", "-i", "v.mp4", "-map", "0:v:0", "-c", "copy", "o.mp4"]


def test_mux_joins_each_sequence_into_its_clip_and_returns_the_clips_in_order(tmp_path):
    raw, out = tmp_path / "raw", tmp_path / "clips"
    first, second = record(raw, 1), record(raw, 2)
    ffmpeg = FakeFfmpeg()
    sequences = [sequence(1, 500, 900), sequence(2, 5000, 5400)]

    clips = hlae_files.mux(FFMPEG, raw, sequences, out, run=ffmpeg)

    assert clips == [out / "sequence-1-tick-500-to-900.mp4", out / "sequence-2-tick-5000-to-5400.mp4"]
    assert all(clip.is_file() for clip in clips)
    assert [command for command, _ in ffmpeg.calls] == [
        hlae_files.mux_command(FFMPEG, folder / "video.mp4", folder / "take0000" / "audio.wav", clip)
        for folder, clip in zip((first, second), clips)
    ]


def test_mux_makes_the_output_folder_before_it_runs_ffmpeg(tmp_path):
    raw, out = tmp_path / "raw", tmp_path / "deep" / "clips"
    record(raw, 1)

    hlae_files.mux(FFMPEG, raw, [sequence(1)], out, run=FakeFfmpeg())     # the stand-in fails without the folder

    assert out.is_dir()


def test_mux_runs_ffmpeg_in_no_window_with_its_output_captured_and_a_timeout(tmp_path):
    record(tmp_path / "raw", 1)
    ffmpeg = FakeFfmpeg()

    hlae_files.mux(FFMPEG, tmp_path / "raw", [sequence(1)], tmp_path / "out", run=ffmpeg)

    [(_, kwargs)] = ffmpeg.calls
    assert kwargs["capture_output"] is True
    assert kwargs["text"] is True
    assert kwargs["creationflags"] == subprocess.CREATE_NO_WINDOW
    assert kwargs["timeout"] > 0


def test_mux_without_audio_joins_the_video_alone(tmp_path):
    raw, out = tmp_path / "raw", tmp_path / "out"
    folder = record(raw, 1, takes=())
    ffmpeg = FakeFfmpeg()

    hlae_files.mux(FFMPEG, raw, [sequence(1)], out, record_audio=False, run=ffmpeg)

    [(command, _)] = ffmpeg.calls
    assert command == hlae_files.mux_command(FFMPEG, folder / "video.mp4", None, out / hlae_plan.clip_name(sequence(1)))


def test_mux_with_no_sequence_runs_nothing(tmp_path):
    ffmpeg = FakeFfmpeg()

    assert hlae_files.mux(FFMPEG, tmp_path, [], tmp_path / "out", run=ffmpeg) == []
    assert ffmpeg.calls == []


def test_a_failed_ffmpeg_is_a_mux_error_with_the_last_lines_of_what_it_said(tmp_path):
    record(tmp_path / "raw", 4)
    said = ("banner\nconfiguration: --enable-x\nStream mapping:\nCould not write header\n"
            "Error initializing\nConversion failed!\n")
    ffmpeg = FakeFfmpeg(returncode=1, stderr=said)

    message = mux_error(hlae_files.mux, FFMPEG, tmp_path / "raw", [sequence(4)], tmp_path / "out", run=ffmpeg)

    assert "Sequence 4" in message
    assert "Stream mapping:" in message and "Could not write header" in message
    assert "Error initializing" in message and "Conversion failed!" in message
    assert "banner" not in message and "configuration" not in message


def test_a_failed_ffmpeg_that_said_nothing_is_a_mux_error_with_its_exit_code(tmp_path):
    record(tmp_path / "raw", 4)

    message = mux_error(hlae_files.mux, FFMPEG, tmp_path / "raw", [sequence(4)], tmp_path / "out",
                        run=FakeFfmpeg(returncode=3))

    assert "Sequence 4" in message and "3" in message


def test_a_failed_ffmpeg_leaves_no_half_written_clip(tmp_path):
    record(tmp_path / "raw", 4)
    out = tmp_path / "out"

    mux_error(hlae_files.mux, FFMPEG, tmp_path / "raw", [sequence(4)], out, run=FakeFfmpeg(returncode=1))

    assert list(out.iterdir()) == []


def test_an_ffmpeg_that_takes_too_long_is_a_mux_error_and_leaves_no_half_written_clip(tmp_path):
    record(tmp_path / "raw", 4)
    out = tmp_path / "out"
    ffmpeg = FakeFfmpeg(raises=subprocess.TimeoutExpired("ffmpeg", 300))

    message = mux_error(hlae_files.mux, FFMPEG, tmp_path / "raw", [sequence(4)], out, run=ffmpeg)

    assert "Sequence 4" in message
    assert list(out.iterdir()) == []


def test_an_ffmpeg_that_cannot_start_is_a_mux_error_that_says_why(tmp_path):
    record(tmp_path / "raw", 4)
    ffmpeg = FakeFfmpeg(raises=FileNotFoundError(2, "The system cannot find the file specified"))

    message = mux_error(hlae_files.mux, FFMPEG, tmp_path / "raw", [sequence(4)], tmp_path / "out", run=ffmpeg)

    assert "Sequence 4" in message and "cannot find the file" in message


def test_mux_stops_at_the_first_sequence_that_was_not_recorded(tmp_path):
    raw, out = tmp_path / "raw", tmp_path / "out"
    record(raw, 1)
    ffmpeg = FakeFfmpeg()

    message = mux_error(hlae_files.mux, FFMPEG, raw, [sequence(1), sequence(2), sequence(3)], out, run=ffmpeg)

    assert "Sequence 2" in message
    assert len(ffmpeg.calls) == 1
