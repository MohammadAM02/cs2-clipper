"""What a Render Job is asked for and gives back (clipper/render.py)."""
from pathlib import Path

import pytest

from clipper.render import RenderRequest, RenderResult, find_clips


def request(outputs):
    return RenderRequest(
        demo_path=Path("match.dem"), outputs=outputs, rounds=(3,), log_path=Path("render.log"), steamid="7656",
        padding_before_s=4.0, padding_after_s=2.0,
    )


def test_the_clips_in_a_folder_are_found_in_tick_order(tmp_path):
    for name in ("sequence-2-tick-900-to-1000.mp4", "sequence-1-tick-100-to-300.mp4", "video.mp4", "notes.txt"):
        (tmp_path / name).write_bytes(b"")
    clips = find_clips(tmp_path, duration_of=lambda path: 4.0)
    assert [(c.sequence, c.start_tick, c.end_tick, c.path.name, c.duration_s) for c in clips] == [
        (1, 100, 300, "sequence-1-tick-100-to-300.mp4", 4.0),
        (2, 900, 1000, "sequence-2-tick-900-to-1000.mp4", 4.0),
    ]


def test_a_request_records_one_view_or_two_in_the_order_its_outputs_are_given():
    assert request({"player": Path("a")}).perspectives == ("player",)
    assert request({"enemy": Path("b"), "player": Path("a")}).perspectives == ("enemy", "player")


@pytest.mark.parametrize("outputs", [
    {},                                                                     # nothing to record
    {"player": Path("a"), "enemy": Path("b"), "spectator": Path("c")},      # three views
    {"spectator": Path("a")},                                               # a Perspective that is not one
    {"player": Path("a"), "spectator": Path("b")},
])
def test_a_request_is_refused_unless_it_is_one_or_two_known_views(outputs):
    with pytest.raises(ValueError):
        request(outputs)


def test_a_result_has_no_clips_unless_it_says_it_has_some():
    assert RenderResult(ok=False, failure="stalled").clips == {}
