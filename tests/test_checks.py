"""clipper.checks: Status's checks and the start-up problems (spec: Pages -> Status (Checks); When
something goes wrong; Code units).

No network, and no real HLAE or csda: everything lives under tmp_path, and every collaborator that
would touch the network or the real machine (which, free_bytes, HlaeReleases' own fetch) is a fake.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from clipper import checks
from clipper.config import Config

GB = 1024**3


def _cfg(tmp_path, **overrides) -> Config:
    fields = dict(
        downloads_dir=tmp_path / "downloads",
        data_root=None,
        index_path=tmp_path / "clipper.sqlite",
        logs_dir=tmp_path / "logs",
        tools_dir=tmp_path / "tools",
        analyses_dir=tmp_path / "analyses",
        ffmpeg="ffmpeg-missing-xyz",
        ffprobe="ffprobe-missing-xyz",
        min_free_gb=5.0,
    )
    fields.update(overrides)
    return Config(**fields)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


# --- hlae_exe --------------------------------------------------------------------------------------


def test_hlae_exe_is_the_one_setup_installs_when_no_path_is_set(tmp_path):
    cfg = _cfg(tmp_path)
    exe = _touch(tmp_path / "tools" / "hlae" / "HLAE.exe")
    assert checks.hlae_exe(cfg) == exe


def test_hlae_exe_is_the_path_set_in_settings(tmp_path):
    exe = _touch(tmp_path / "HLAE" / "hlae.exe")
    _touch(tmp_path / "tools" / "hlae" / "HLAE.exe")
    assert checks.hlae_exe(_cfg(tmp_path, hlae_exe=str(exe))) == exe


def test_hlae_exe_is_none_when_there_is_no_such_file(tmp_path):
    _touch(tmp_path / "tools" / "hlae" / "HLAE.exe")      # a path that is set is not swapped for this one
    assert checks.hlae_exe(_cfg(tmp_path, hlae_exe=str(tmp_path / "gone" / "hlae.exe"))) is None
    assert checks.hlae_exe(_cfg(tmp_path / "nothing installed")) is None


# --- hlae_version ------------------------------------------------------------------------------


CHANGELOG_TWO_RELEASES = """<?xml version="1.0"?>
<changelog>
  <release><name>HLAE</name><version>2.192.6</version><time>2026-08-01</time></release>
  <release><name>HLAE</name><version>2.191.0</version><time>2026-05-01</time></release>
</changelog>
"""


def test_hlae_version_is_the_first_releases_version(tmp_path):
    exe = tmp_path / "HLAE.exe"
    exe.write_bytes(b"")
    (tmp_path / "changelog.xml").write_text(CHANGELOG_TWO_RELEASES, encoding="utf-8")
    assert checks.hlae_version(exe) == "2.192.6"


def test_hlae_version_is_none_when_the_changelog_is_missing(tmp_path):
    assert checks.hlae_version(tmp_path / "HLAE.exe") is None


def test_hlae_version_is_none_when_the_changelog_is_malformed(tmp_path):
    exe = tmp_path / "HLAE.exe"
    (tmp_path / "changelog.xml").write_text("<not-even-xml", encoding="utf-8")
    assert checks.hlae_version(exe) is None


def test_hlae_version_is_none_when_the_first_release_has_no_version_tag(tmp_path):
    exe = tmp_path / "HLAE.exe"
    (tmp_path / "changelog.xml").write_text(
        "<changelog><release><name>HLAE</name></release></changelog>", encoding="utf-8")
    assert checks.hlae_version(exe) is None


# --- latest_hlae_release -------------------------------------------------------------------------


def test_latest_hlae_release_strips_the_v_prefix():
    assert checks.latest_hlae_release(lambda url: {"tag_name": "v2.192.6"}) == "2.192.6"


def test_latest_hlae_release_without_a_leading_v_is_unchanged():
    assert checks.latest_hlae_release(lambda url: {"tag_name": "2.192.6"}) == "2.192.6"


def test_latest_hlae_release_raises_when_the_tag_is_missing():
    with pytest.raises(KeyError):
        checks.latest_hlae_release(lambda url: {})


# --- HlaeReleases --------------------------------------------------------------------------------


def test_hlae_releases_refreshes_at_start_then_daily_not_sooner(tmp_path):
    now = [1_000_000.0]
    fetches = []

    def fetch():
        fetches.append(now[0])
        return "2.192.6"

    releases = checks.HlaeReleases(fetch=fetch, clock=lambda: now[0])
    releases.refresh_if_due()
    assert releases.latest == "2.192.6"
    assert len(fetches) == 1

    now[0] += 3600   # an hour later: not due yet
    releases.refresh_if_due()
    assert len(fetches) == 1

    now[0] += 24 * 60 * 60   # a day later: due again
    releases.refresh_if_due()
    assert len(fetches) == 2


def test_hlae_releases_a_failure_keeps_the_previous_latest_and_retries_after_an_hour():
    now = [0.0]
    calls = {"n": 0}

    def fetch():
        calls["n"] += 1
        if calls["n"] == 1:
            return "2.192.6"
        raise OSError("no network")

    releases = checks.HlaeReleases(fetch=fetch, clock=lambda: now[0])
    releases.refresh_if_due()
    assert (releases.latest, releases.failed) == ("2.192.6", False)

    now[0] += 24 * 60 * 60
    releases.refresh_if_due()
    assert (releases.latest, releases.failed) == ("2.192.6", True)   # kept; marked failed
    assert calls["n"] == 2

    now[0] += 1800   # half an hour after the failure: not due yet
    releases.refresh_if_due()
    assert calls["n"] == 2

    now[0] += 1800   # an hour after the failure: due again
    releases.refresh_if_due()
    assert calls["n"] == 3


def test_hlae_releases_refresh_if_due_never_raises():
    def boom():
        raise RuntimeError("boom")

    releases = checks.HlaeReleases(fetch=boom, clock=lambda: 0.0)
    releases.refresh_if_due()   # must not raise
    assert releases.failed
    assert releases.latest is None


# --- run_checks ----------------------------------------------------------------------------------


class _FakeReleases:
    def __init__(self, latest=None):
        self.latest = latest


def _get(cfg, **overrides):
    kwargs = dict(
        alerts_status="on",
        releases=_FakeReleases("9.9.9"),
        which=lambda name: None,
        free_bytes=lambda path: 999 * GB,
    )
    kwargs.update(overrides)
    results = checks.run_checks(cfg, **kwargs)
    assert [c.name for c in results] == ["csda", "HLAE", "FFmpeg", "Clips folder", "Match alerts"]
    return {c.name: c for c in results}


def test_run_checks_csda_missing(tmp_path):
    result = _get(_cfg(tmp_path))["csda"]
    assert (result.ok, result.detail) == (False, "not found")
    assert "Set up" in result.hint


def test_run_checks_csda_found(tmp_path):
    cfg = _cfg(tmp_path)
    _touch(cfg.csda_exe)
    result = _get(cfg)["csda"]
    assert (result.ok, result.detail, result.hint) == (True, "found", "")


def _install_hlae(cfg: Config, changelog_xml: str) -> Path:
    """The HLAE Setup installs, with a changelog.xml beside it."""
    exe = _touch(cfg.hlae_path)
    exe.with_name("changelog.xml").write_text(changelog_xml, encoding="utf-8")
    return exe


def _release_xml(version: str) -> str:
    return f"<changelog><release><version>{version}</version></release></changelog>"


def test_run_checks_hlae_not_found(tmp_path):
    cfg = _cfg(tmp_path)
    result = _get(cfg)["HLAE"]
    assert (result.ok, result.detail) == (False, "not found")
    assert "Set up" in result.hint and "Settings" in result.hint


def test_run_checks_hlae_is_the_latest(tmp_path):
    cfg = _cfg(tmp_path)
    _install_hlae(cfg, _release_xml("2.192.6"))
    result = _get(cfg, releases=_FakeReleases("2.192.6"))["HLAE"]
    assert (result.ok, result.detail) == (True, "2.192.6 (latest)")


def test_run_checks_hlae_is_out_of_date(tmp_path):
    cfg = _cfg(tmp_path)
    _install_hlae(cfg, _release_xml("2.192.6"))
    result = _get(cfg, releases=_FakeReleases("2.193.0"))["HLAE"]
    assert (result.ok, result.detail) == (False, "2.192.6 — 2.193.0 is out")
    assert "older than CS2" in result.hint


def test_run_checks_hlae_release_check_unavailable(tmp_path):
    cfg = _cfg(tmp_path)
    _install_hlae(cfg, _release_xml("2.192.6"))
    result = _get(cfg, releases=_FakeReleases(None))["HLAE"]
    assert (result.ok, result.detail) == (None, "2.192.6 (couldn't check for a newer release)")


def test_run_checks_hlae_unreadable_version_skips_the_comparison(tmp_path):
    cfg = _cfg(tmp_path)
    _touch(cfg.hlae_path)   # no changelog.xml beside it
    result = _get(cfg, releases=_FakeReleases("2.192.6"))["HLAE"]
    assert (result.ok, result.detail) == (None, "unknown version")


def test_run_checks_ffmpeg_found_via_which(tmp_path):
    cfg = _cfg(tmp_path, ffmpeg="ffmpeg", ffprobe="ffprobe")
    result = _get(cfg, which=lambda name: f"C:\\tools\\{name}.exe")["FFmpeg"]
    assert (result.ok, result.detail) == (True, "found")


def test_run_checks_ffmpeg_found_via_an_existing_file_path(tmp_path):
    exe = tmp_path / "ffmpeg.exe"
    exe.write_bytes(b"")
    cfg = _cfg(tmp_path, ffmpeg=str(exe), ffprobe=str(exe))
    result = _get(cfg, which=lambda name: None)["FFmpeg"]
    assert (result.ok, result.detail) == (True, "found")


def test_run_checks_ffmpeg_missing_names_what_is_missing(tmp_path):
    cfg = _cfg(tmp_path, ffmpeg="ffmpeg-nope", ffprobe="ffprobe-nope")
    result = _get(cfg, which=lambda name: None)["FFmpeg"]
    assert result.ok is False
    assert "ffmpeg" in result.detail and "ffprobe" in result.detail
    assert "Settings" in result.hint


def test_run_checks_clips_folder_unset(tmp_path):
    cfg = _cfg(tmp_path, data_root=None)
    result = _get(cfg)["Clips folder"]
    assert (result.ok, result.detail) == (False, "not set")
    assert "Settings" in result.hint


def test_run_checks_clips_folder_missing(tmp_path):
    cfg = _cfg(tmp_path, data_root=tmp_path / "no-such-clips-folder")
    result = _get(cfg)["Clips folder"]
    assert result.ok is False
    assert "is missing" in result.detail


def test_run_checks_clips_folder_enough_space(tmp_path):
    clips = tmp_path / "clips"
    clips.mkdir()
    cfg = _cfg(tmp_path, data_root=clips, min_free_gb=5.0)
    result = _get(cfg, free_bytes=lambda path: 10 * GB)["Clips folder"]
    assert result.ok is True
    assert "10 GB free" in result.detail


def test_run_checks_clips_folder_not_enough_space(tmp_path):
    clips = tmp_path / "clips"
    clips.mkdir()
    cfg = _cfg(tmp_path, data_root=clips, min_free_gb=5.0)
    result = _get(cfg, free_bytes=lambda path: 2 * GB)["Clips folder"]
    assert result.ok is False
    assert "2 GB free" in result.detail
    assert "5 GB" in result.hint


def test_run_checks_match_alerts_not_started(tmp_path):
    cfg = _cfg(tmp_path)
    result = _get(cfg, alerts_status=None)["Match alerts"]
    assert (result.ok, result.detail) == (None, "not started")


def test_run_checks_match_alerts_on(tmp_path):
    cfg = _cfg(tmp_path)
    result = _get(cfg, alerts_status="on")["Match alerts"]
    assert (result.ok, result.detail) == (True, "on")


def test_run_checks_match_alerts_off_shows_the_reason(tmp_path):
    cfg = _cfg(tmp_path)
    result = _get(cfg, alerts_status="off: FACEIT rejected the key")["Match alerts"]
    assert (result.ok, result.detail) == (False, "FACEIT rejected the key")


# --- startup_problems ----------------------------------------------------------------------------


def test_startup_problems_lists_each_message_in_order(tmp_path):
    cfg = _cfg(tmp_path, subject_steamid="", data_root=None)
    assert checks.startup_problems(cfg) == [
        "Set your SteamID in Settings",
        "Choose a clips folder in Settings",
        f"csda was not found in {cfg.csda_exe.parent}",
    ]


def test_startup_problems_a_set_but_missing_clips_folder_names_the_path_instead_of_unset(tmp_path):
    cfg = _cfg(tmp_path, subject_steamid="76561190000000001", data_root=tmp_path / "gone")
    problems = checks.startup_problems(cfg)
    assert f"The clips folder {cfg.data_root} is missing" in problems
    assert not any("Choose a clips folder" in p for p in problems)


def test_startup_problems_empty_when_all_is_well_even_without_hlae(tmp_path):
    """Without HLAE the app still downloads, analyzes and scores; each render says HLAE is missing."""
    clips = tmp_path / "clips"
    clips.mkdir()
    cfg = _cfg(tmp_path, subject_steamid="76561190000000001", data_root=clips)
    _touch(cfg.csda_exe)
    assert checks.hlae_exe(cfg) is None
    assert checks.startup_problems(cfg) == []


# --- hlae_behind ---------------------------------------------------------------------------------


def test_hlae_is_behind_only_when_both_versions_are_known_and_the_release_is_newer(tmp_path):
    cfg = _cfg(tmp_path)
    assert checks.hlae_behind(cfg, "2.192.7") is False       # no HLAE at all: nothing to be behind

    _install_hlae(cfg, CHANGELOG_TWO_RELEASES)              # 2.192.6

    assert checks.hlae_behind(cfg, "2.192.7") is True
    assert checks.hlae_behind(cfg, "2.192.6") is False
    assert checks.hlae_behind(cfg, None) is False            # the release check has not answered
