import shutil
import subprocess

import pytest

from clipper.media import MediaError, probe_duration


def test_a_file_that_is_not_video_raises(tmp_path):
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe not on PATH")
    fake = tmp_path / "fake.mp4"
    fake.write_bytes(b"fake video")
    with pytest.raises(MediaError, match="fake.mp4"):
        probe_duration(fake)


@pytest.mark.ffmpeg
def test_a_real_clip_reports_its_duration(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH")
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=60:d=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )
    assert probe_duration(clip) == pytest.approx(1.0, abs=0.05)
