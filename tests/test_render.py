"""What a Render Job is asked for and gives back (clipper/render.py)."""
from clipper.render import find_clips


def test_the_clips_in_a_folder_are_found_in_tick_order(tmp_path):
    for name in ("sequence-2-tick-900-to-1000.mp4", "sequence-1-tick-100-to-300.mp4", "video.mp4", "notes.txt"):
        (tmp_path / name).write_bytes(b"")
    clips = find_clips(tmp_path, duration_of=lambda path: 4.0)
    assert [(c.sequence, c.start_tick, c.end_tick, c.path.name, c.duration_s) for c in clips] == [
        (1, 100, 300, "sequence-1-tick-100-to-300.mp4", 4.0),
        (2, 900, 1000, "sequence-2-tick-900-to-1000.mp4", 4.0),
    ]
