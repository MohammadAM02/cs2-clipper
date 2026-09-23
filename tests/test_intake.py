from pathlib import Path

import pytest

from clipper.index import Index
from clipper.intake import Intake

NAME = "1-2b882547-d8dd-4ef7-b5c3-6e9558217b17-1-1.dem.zst"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def setup(tmp_path):
    downloads, demos = tmp_path / "Downloads", tmp_path / "demos"
    downloads.mkdir()
    clock = Clock()
    index = Index(tmp_path / "clipper.sqlite")
    yield downloads, demos, clock, Intake(downloads, demos, clock=clock), index
    index.close()


def test_a_demo_is_ready_once_its_size_holds_for_ten_seconds(setup):
    downloads, _, clock, intake, _ = setup
    demo = downloads / NAME
    demo.write_bytes(b"abc")
    assert intake.ready() == []        # first sighting
    clock.now += 5
    assert intake.ready() == []        # not stable for long enough
    demo.write_bytes(b"abcdef")        # still growing
    clock.now += 10
    assert intake.ready() == []        # the size changed, so the wait starts again
    clock.now += 10
    assert intake.ready() == [demo]


def test_other_files_are_ignored(setup):
    downloads, _, clock, intake, _ = setup
    for name in ("notes.txt", "match.dem", "1-2b882547-d8dd-4ef7-b5c3-6e9558217b17-1-1.zip"):
        (downloads / name).write_bytes(b"x")
    intake.ready()
    clock.now += 60
    assert intake.ready() == []


def test_a_partial_download_holds_the_demo_back(setup):
    downloads, _, clock, intake, _ = setup
    (downloads / NAME).write_bytes(b"abc")
    (downloads / (NAME + ".part")).write_bytes(b"")
    intake.ready()
    clock.now += 60
    assert intake.ready() == []


def test_take_moves_the_demo_and_records_it(setup):
    downloads, demos, _, intake, index = setup
    (downloads / NAME).write_bytes(b"abc")
    demo_id = intake.take(downloads / NAME, index)
    row = index.demo(demo_id)
    assert (row["file_name"], row["state"]) == (NAME, "spotted")
    assert Path(row["archive_path"]) == demos / NAME
    assert (demos / NAME).read_bytes() == b"abc"
    assert not (downloads / NAME).exists()


def test_a_duplicate_is_moved_aside_without_a_new_row(setup):
    downloads, demos, _, intake, index = setup
    (downloads / NAME).write_bytes(b"abc")
    intake.take(downloads / NAME, index)
    (downloads / NAME).write_bytes(b"abc")
    assert intake.take(downloads / NAME, index) is None
    assert (demos / "duplicates" / NAME).exists()
    assert len(index.all_demos()) == 1
