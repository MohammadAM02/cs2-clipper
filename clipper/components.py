"""What setup downloads.

csda and FFmpeg are pinned: one known file each, checked against the SHA-256 written here, so a
changed or tampered download is refused. Setup replaces a csda unpacked from any other zip
(checks.csda_state), since an older csda cannot read the Demos of newer CS2 builds. HLAE is not pinned,
because an HLAE older than the CS2 build cannot record: setup takes the newest release and checks it
against the SHA-256 GitHub publishes for that file, and refuses a release that publishes none."""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from clipper import download
from clipper.download import DownloadError

ADVANCEDFX_RELEASES_URL = "https://api.github.com/repos/advancedfx/advancedfx/releases/latest"


@dataclass(frozen=True)
class Asset:
    name: str        # the file's name, as it is saved
    url: str
    sha256: str
    size: int        # bytes


# cs-demo-analyzer: the zip holds csda.exe alone, which reads each Demo. 1.14.2 reads the Demos of CS2's update of
# 2026-11-11 (fixed8-encoded fields on the player controller), which 1.11.0 failed on from the first packet.
CSDA = Asset(
    name="csda-1.14.2-windows-x64.zip",
    url="https://github.com/akiver/cs-demo-analyzer/releases/download/v1.14.2/windows-x64.zip",
    sha256="05767a62998c1e35074467bb1ca82336c70c59629c0be45445882a63635a98b5",
    size=3_744_334,
)
FFMPEG = Asset(
    name="ffmpeg-9.0.2-essentials_build.zip",
    url="https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/ffmpeg-9.0.2-essentials_build.zip",
    sha256="60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba",
    size=114_768_076,
)

_HLAE_ZIP = re.compile(r"hlae_[0-9_]+\.zip")
_SHA256 = re.compile(r"sha256:([0-9a-f]{64})")


def latest_hlae(fetch_json: Callable[[str], dict] = download.fetch_json) -> tuple[str, Asset]:
    """The newest HLAE release: its version (e.g. "2.192.6") and its zip. Raises DownloadError when
    the release cannot be read, has no zip, or publishes no SHA-256 for it."""
    data = fetch_json(ADVANCEDFX_RELEASES_URL)
    try:
        version = str(data["tag_name"]).removeprefix("v")
        found = next(asset for asset in data["assets"] if _HLAE_ZIP.fullmatch(asset["name"]))
        name, url, size = found["name"], found["browser_download_url"], int(found["size"])
    except (KeyError, TypeError, ValueError, StopIteration) as exc:
        raise DownloadError("HLAE's newest release has no zip to install") from exc
    digest = _SHA256.fullmatch(str(found.get("digest") or ""))
    if digest is None:
        raise DownloadError(f"HLAE's newest release publishes no SHA-256 for {name}, so it cannot be checked")
    return version, Asset(name=name, url=url, sha256=digest.group(1), size=size)
