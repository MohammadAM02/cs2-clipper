import gzip

import pytest
import zstandard

from clipper.unpack import UnpackError, unpack

DEMO = b"PBDEMS2\x00" + bytes(range(256)) * 64
NAME = "1-2b882547-d8dd-4ef7-b5c3-6e9558217b17-1-1"


def test_zst_is_decompressed(tmp_path):
    src = tmp_path / f"{NAME}.dem.zst"
    src.write_bytes(zstandard.ZstdCompressor().compress(DEMO))
    out = unpack(src, tmp_path / "demos")
    assert out.name == f"{NAME}.dem"
    assert out.read_bytes() == DEMO


def test_gz_is_decompressed(tmp_path):
    src = tmp_path / f"{NAME}.dem.gz"
    src.write_bytes(gzip.compress(DEMO))
    assert unpack(src, tmp_path / "demos").read_bytes() == DEMO


def test_a_plain_dem_already_in_place_is_returned_as_is(tmp_path):
    src = tmp_path / f"{NAME}.dem"
    src.write_bytes(DEMO)
    assert unpack(src, tmp_path) == src


def test_a_plain_dem_elsewhere_is_copied(tmp_path):
    src = tmp_path / f"{NAME}.dem"
    src.write_bytes(DEMO)
    out = unpack(src, tmp_path / "demos")
    assert out == tmp_path / "demos" / f"{NAME}.dem"
    assert out.read_bytes() == DEMO
    assert src.exists()


def test_a_file_without_the_demo_header_is_rejected_and_leaves_nothing(tmp_path):
    src = tmp_path / f"{NAME}.dem.zst"
    src.write_bytes(zstandard.ZstdCompressor().compress(b"not a demo"))
    with pytest.raises(UnpackError, match="PBDEMS2"):
        unpack(src, tmp_path / "demos")
    assert list((tmp_path / "demos").iterdir()) == []


def test_a_corrupt_zst_is_rejected(tmp_path):
    src = tmp_path / f"{NAME}.dem.zst"
    src.write_bytes(b"definitely not zstd")
    with pytest.raises(UnpackError):
        unpack(src, tmp_path / "demos")


def test_other_extensions_are_rejected(tmp_path):
    src = tmp_path / "notes.txt"
    src.write_bytes(b"x")
    with pytest.raises(UnpackError, match="not a Demo"):
        unpack(src, tmp_path)
