import shutil
import subprocess

import pytest

from clipper.media import MediaError, gpu_encoder, probe_duration


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


def _encodes_with(*working):
    """A stand-in for running FFmpeg: a test encode succeeds only with the codecs in `working`."""
    tried = []

    def run(command, **kwargs):
        codec = command[command.index("-c:v") + 1]
        tried.append(codec)
        return subprocess.CompletedProcess(command, 0 if codec in working else 1, b"", b"")

    return run, tried


def test_the_first_graphics_card_encoder_that_works_is_used():
    run, tried = _encodes_with("h264_amf", "h264_qsv")
    assert gpu_encoder("ffmpeg", run=run) == ("h264_amf", "-quality quality -rc cqp -qp_i 22 -qp_p 24")
    assert tried == ["h264_nvenc", "h264_amf"]


def test_with_no_graphics_card_encoder_x264_records():
    run, tried = _encodes_with()
    assert gpu_encoder("ffmpeg", run=run) is None
    assert tried == ["h264_nvenc", "h264_amf", "h264_qsv"]
