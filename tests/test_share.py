import json
import shutil
import subprocess
from pathlib import Path

import pytest

from clipper import share
from clipper.share import DISCORD_LIMIT, ShareError, Title


def test_a_reel_is_named_as_it_would_be_posted():
    assert share.reel_name("ACE", "de_dust2", 9, "s1mple", "player") == "ACE Dust2 R9 s1mple"
    assert share.reel_name("5K", "de_dust2", 9, "s1mple", "player") == "ACE Dust2 R9 s1mple"
    assert share.reel_name("3k", "de_ancient", 14, "", "enemy") == "3K Ancient R14 (Enemy POV)"


def test_a_name_holds_nothing_windows_refuses_in_a_file_name():
    assert share.reel_name("4K", "de_mirage", 3, 'a<b>c:"d/e\\f|g?h*', "player") == "4K Mirage R3 abcdefgh"


def test_a_matchs_folder_is_its_day_map_and_score():
    assert share.match_folder("de_dust2", "2026-10-12T18:00:00+00:00", 13, 9).endswith(" Dust2 13-9")
    assert share.match_folder("de_dust2", "2026-10-12T18:00:00+00:00", 13, 9)[:10] in ("2026-10-12", "2026-10-13")



def test_a_reel_of_no_round_names_none():
    # a Break: the index keeps it under the negative of the round it follows
    assert share.reel_name("HALFTIME", "de_mirage", -12, "s1mple", "player") == "HALFTIME Mirage s1mple"
    assert share.reel_name("OT 1 HALF", "de_mirage", 0, "", "player") == "OT 1 HALF Mirage"
    assert share.title("HALFTIME", "de_mirage", -12, "s1mple") == Title("HALFTIME", "s1mple · Mirage")

def test_the_title_card_says_what_who_where_and_when():
    assert share.title("ACE", "de_dust2", 9, "s1mple") == Title("ACE", "s1mple · Dust2 · Round 9")
    assert share.title("3K", "de_nuke", 2, "") == Title("3K", "Nuke · Round 2")


def test_copying_puts_the_file_on_the_clipboard_through_powershell(tmp_path):
    ran = []

    def run(command, **kwargs):
        ran.append((command, kwargs["env"]["CLIPPER_FILE"]))
        return subprocess.CompletedProcess(command, 0, "", "")

    share.copy_to_clipboard(tmp_path / "ACE Dust2 R9 s1mple.mp4", run=run)
    [(command, file)] = ran
    assert "Set-Clipboard -LiteralPath $env:CLIPPER_FILE" in command
    assert file == str(tmp_path / "ACE Dust2 R9 s1mple.mp4")


def test_a_clipboard_that_refuses_says_so(tmp_path):
    def run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "access denied")

    with pytest.raises(ShareError, match="refused"):
        share.copy_to_clipboard(tmp_path / "x.mp4", run=run)


def test_a_reel_that_fits_discord_already_is_copied_as_it_is(tmp_path):
    reel = tmp_path / "reel.mp4"
    reel.write_bytes(b"x" * 1000)
    out = share.export(reel, "discord", tmp_path / "Exports" / "a (Discord).mp4", duration_s=20, ffmpeg="ffmpeg",
                       card=None, run=lambda *a, **k: pytest.fail("no encode for a Reel that fits"))
    assert out.read_bytes() == reel.read_bytes()


def _encoding_run(sizes):
    """A stand-in FFmpeg that writes an output of the next size in `sizes`, and keeps each command."""
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"x" * sizes[len(commands) - 1])
        return subprocess.CompletedProcess(command, 0, "", "")

    return run, commands


def test_a_long_reel_spends_its_discord_budget_on_fewer_lines(tmp_path):
    reel = tmp_path / "reel.mp4"
    reel.write_bytes(b"x" * (DISCORD_LIMIT * 2))
    run, commands = _encoding_run([DISCORD_LIMIT - 1])
    share.export(reel, "discord", tmp_path / "out.mp4", duration_s=60, ffmpeg="ffmpeg", card=None, run=run)
    graph = commands[0][commands[0].index("-vf") + 1]
    assert graph == "scale=-2:'min(ih,720)'"            # ~2.3 Mbit/s for 60 s: 720p at most
    rate = commands[0][commands[0].index("-b:v") + 1]
    assert 2000 <= int(rate.removesuffix("k")) <= 2400


def test_an_export_that_misses_the_limit_is_made_again_smaller_then_given_up(tmp_path):
    reel = tmp_path / "reel.mp4"
    reel.write_bytes(b"x" * (DISCORD_LIMIT * 2))
    run, commands = _encoding_run([DISCORD_LIMIT + 1, DISCORD_LIMIT - 1])
    share.export(reel, "discord", tmp_path / "out.mp4", duration_s=20, ffmpeg="ffmpeg", card=None, run=run)
    rates = [int(c[c.index("-b:v") + 1].removesuffix("k")) for c in commands]
    assert rates[1] < rates[0]

    run, _ = _encoding_run([DISCORD_LIMIT + 1, DISCORD_LIMIT + 1])
    with pytest.raises(ShareError, match="over Discord's 20 MB"):
        share.export(reel, "discord", tmp_path / "out.mp4", duration_s=20, ffmpeg="ffmpeg", card=None, run=run)


def test_a_vertical_export_is_cropped_from_the_middle_with_its_title_card(tmp_path):
    reel = tmp_path / "reel.mp4"
    reel.write_bytes(b"x")
    font = tmp_path / "impact.ttf"
    font.write_bytes(b"font")
    run, commands = _encoding_run([1000])
    share.export(reel, "vertical", tmp_path / "out.mp4", duration_s=20, ffmpeg="ffmpeg",
                 card=Title("ACE", "s1mple · Dust2 · Round 9"), run=run, font=font)
    graph = commands[0][commands[0].index("-vf") + 1]
    assert graph.startswith("crop=ih*9/16:ih,scale=1080:1920,drawbox=")
    assert "textfile=big.txt" in graph and "enable='lt(t,2.5)'" in graph


def test_with_no_font_the_export_goes_without_its_card(tmp_path):
    reel = tmp_path / "reel.mp4"
    reel.write_bytes(b"x")
    run, commands = _encoding_run([1000])
    share.export(reel, "vertical", tmp_path / "out.mp4", duration_s=20, ffmpeg="ffmpeg", card=Title("ACE", "x"),
                 run=run, font=tmp_path / "missing.ttf")
    assert "drawtext" not in commands[0][commands[0].index("-vf") + 1]


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None or not share.TITLE_FONT.is_file(),
                    reason="needs FFmpeg, FFprobe and Windows' Impact font")
@pytest.mark.parametrize(("kind", "size"), [("discord", (1280, 720)), ("vertical", (1080, 1920))])
def test_real_exports_come_out_with_their_title_card(tmp_path, kind, size):
    reel = tmp_path / "reel.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=s=1280x720:r=30:d=3",
                    "-f", "lavfi", "-i", "sine=d=3", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "1",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(reel)], check=True)
    out = share.export(reel, kind, tmp_path / "Exports" / f"ACE Dust2 R9 s1mple ({kind}).mp4", duration_s=3,
                       ffmpeg="ffmpeg", card=share.title("ACE", "de_dust2", 9, "s1mple"))
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                       "stream=width,height", "-of", "json", str(out)],
                                      capture_output=True, text=True, check=True).stdout)
    stream = probe["streams"][0]
    assert (stream["width"], stream["height"]) == size
    assert out.stat().st_size <= DISCORD_LIMIT
