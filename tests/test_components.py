"""Tests for clipper.components. GitHub is never asked: the release is a dict handed to latest_hlae."""
from __future__ import annotations

import re

import pytest

from clipper import components
from clipper.download import DownloadError

DIGEST = "b3acae70babb536e3b4a34fbbbe4ca8e55a1028068eaaf5fc98817775b72f4fa"
BASE = "https://github.com/advancedfx/advancedfx/releases/download/v2.192.6/"


def release(**zip_overrides):
    zip_asset = {"name": "hlae_2_192_6.zip", "size": 8989218, "digest": f"sha256:{DIGEST}",
                 "browser_download_url": BASE + "hlae_2_192_6.zip", **zip_overrides}
    return {"tag_name": "v2.192.6", "assets": [
        {"name": "hlae_2_192_6.zip.asc", "size": 833, "digest": "sha256:" + "1" * 64,
         "browser_download_url": BASE + "hlae_2_192_6.zip.asc"},
        {"name": "HLAE_Setup.exe", "size": 8184196, "digest": "sha256:" + "2" * 64,
         "browser_download_url": BASE + "HLAE_Setup.exe"},
        zip_asset,
    ]}


@pytest.mark.parametrize("asset", [components.CSDM, components.POSTGRES, components.FFMPEG])
def test_every_pinned_download_names_an_https_file_a_hash_and_a_size(asset):
    assert asset.url.startswith("https://github.com/")
    assert asset.url.rsplit("/", 1)[1] == asset.name
    assert re.fullmatch(r"[0-9a-f]{64}", asset.sha256)
    assert asset.size > 1_000_000


def test_the_pinned_cs_demo_manager_is_the_version_the_settings_template_is_for():
    assert components.CSDM_VERSION in components.CSDM.name


def test_latest_hlae_is_the_releases_zip_with_githubs_hash():
    asked = []

    version, asset = components.latest_hlae(lambda url: asked.append(url) or release())

    assert asked == ["https://api.github.com/repos/advancedfx/advancedfx/releases/latest"]
    assert version == "2.192.6"
    assert asset == components.Asset("hlae_2_192_6.zip", BASE + "hlae_2_192_6.zip", DIGEST, 8989218)


@pytest.mark.parametrize("digest", [None, "", "md5:abc", "sha256:tooshort"])
def test_a_release_that_publishes_no_sha256_is_refused(digest):
    with pytest.raises(DownloadError, match="publishes no SHA-256 for hlae_2_192_6.zip"):
        components.latest_hlae(lambda url: release(digest=digest))


@pytest.mark.parametrize("data", [{}, {"tag_name": "v2.192.6"}, {"tag_name": "v2.192.6", "assets": []},
                                  {"tag_name": "v2.192.6", "assets": [{"name": "HLAE_Setup.exe"}]},
                                  {"tag_name": "v2.192.6", "assets": [{"name": "hlae_2_192_6.zip"}]}])
def test_a_release_without_a_usable_zip_is_refused(data):
    with pytest.raises(DownloadError, match="has no zip to install"):
        components.latest_hlae(lambda url: data)
