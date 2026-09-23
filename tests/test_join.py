import shutil
import subprocess
from pathlib import Path

import pytest

from clipper.join import JoinError, assign_clips, join_reel
from clipper.media import probe_duration
from clipper.model import ClipFile


def clip(sequence, start, end):
    return ClipFile(sequence=sequence, start_tick=start, end_tick=end,
                    path=Path(f"sequence-{sequence}-tick-{start}-to-{end}.mp4"), duration_s=4.0)


def test_clips_group_by_the_round_they_start_in():
    groups = assign_clips([clip(3, 76071, 76559), clip(1, 19832, 20213), clip(2, 74007, 74263)],
                          [(12, 72031), (3, 15259)])
    assert [c.sequence for c in groups[3]] == [1]
    assert [c.sequence for c in groups[12]] == [2, 3]


def test_sequence_ten_comes_after_sequence_two():   # issue 10
    groups = assign_clips([clip(10, 90000, 90256), clip(2, 80000, 80256)], [(12, 72031)])
    assert [c.sequence for c in groups[12]] == [2, 10]


def test_a_whole_round_clip_that_starts_early_still_belongs_to_its_round():
    groups = assign_clips([clip(1, 67531, 71711), clip(2, 71903, 81605)], [(11, 67659), (12, 72031)])
    assert [c.sequence for c in groups[11]] == [1]
    assert [c.sequence for c in groups[12]] == [2]


def test_a_clip_before_every_selected_round_is_an_error():
    with pytest.raises(JoinError, match="lies before every selected round"):
        assign_clips([clip(1, 100, 356)], [(3, 15259)])


def test_a_selected_round_without_clips_is_an_error():
    with pytest.raises(JoinError, match="no Clip for selected round"):
        assign_clips([clip(1, 19832, 20213)], [(3, 15259), (12, 72031)])


@pytest.fixture(scope="module")
def one_second_clip(tmp_path_factory):
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("ffmpeg/ffprobe not on PATH")
    path = tmp_path_factory.mktemp("media") / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=60:d=1",
         "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "libmp3lame", "-shortest", str(path)],
        check=True,
    )
    return path


def copies(folder, source, *names):
    paths = [folder / name for name in names]
    for path in paths:
        shutil.copyfile(source, path)
    return paths


@pytest.mark.ffmpeg
def test_two_clips_join_into_one_reel(tmp_path, one_second_clip):
    a, b = copies(tmp_path, one_second_clip, "a.mp4", "b.mp4")
    out = tmp_path / "library" / "r12-player.mp4"
    duration = join_reel([a, b], out, ffmpeg="ffmpeg", duration_of=probe_duration)
    assert out.exists()
    assert duration == pytest.approx(2.0, abs=0.25)
    assert sorted(p.name for p in out.parent.iterdir()) == ["r12-player.mp4"]


@pytest.mark.ffmpeg
def test_a_single_clip_is_copied(tmp_path, one_second_clip):
    (a,) = copies(tmp_path, one_second_clip, "a.mp4")
    out = tmp_path / "library" / "r3-enemy.mp4"
    assert join_reel([a], out, ffmpeg="ffmpeg", duration_of=probe_duration) == pytest.approx(1.0, abs=0.05)


@pytest.mark.ffmpeg
def test_a_reel_that_does_not_add_up_is_rejected(tmp_path, one_second_clip):
    a, b = copies(tmp_path, one_second_clip, "a.mp4", "b.mp4")
    out = tmp_path / "library" / "r12-player.mp4"

    def inflated(path):   # pretend each Clip is 5 s long, so a 2 s Reel cannot be right
        return 5.0 if path.name in {"a.mp4", "b.mp4"} else probe_duration(path)

    with pytest.raises(JoinError, match="Clips sum to"):
        join_reel([a, b], out, ffmpeg="ffmpeg", duration_of=inflated)
    assert not out.exists()
    assert not list(out.parent.glob("*.partial.mp4"))


@pytest.mark.ffmpeg
def test_a_stretched_reel_is_re_encoded_to_1920x1080(tmp_path, one_second_clip):
    (a,) = copies(tmp_path, one_second_clip, "a.mp4")
    out = tmp_path / "library" / "r12-player.mp4"
    duration = join_reel([a], out, ffmpeg="ffmpeg", duration_of=probe_duration, stretch=True)
    size = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
         "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert size == "1920,1080"
    assert duration == pytest.approx(1.0, abs=0.1)
