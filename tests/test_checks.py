"""clipper.checks: Status's checks and the start-up problems (spec: Pages -> Status (Checks); When
something goes wrong; Code units).

No network, and no real CS:DM/HLAE paths: everything lives under tmp_path, and every collaborator
that would touch the network or the real machine (pg_running, which, free_bytes, version_of,
HlaeReleases' own fetch) is a fake.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from clipper import checks
from clipper.config import Config

GB = 1024**3
# A file every Windows install carries, always with a version resource -- unlike a venv's own
# python.exe, which on this machine is a small launcher stub with no PE version resource at all
# (confirmed directly: GetFileVersionInfoSizeW returns 0, last-error 1813 RESOURCE_TYPE_NOT_FOUND).
KNOWN_VERSIONED_FILE = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "kernel32.dll"


def _cfg(tmp_path, **overrides) -> Config:
    fields = dict(
        downloads_dir=tmp_path / "downloads",
        data_root=None,
        index_path=tmp_path / "clipper.sqlite",
        csdm_home=tmp_path / "csdm-home",
        logs_dir=tmp_path / "logs",
        csdm_app_dir=tmp_path / "csdm-app",
        pg_bin=tmp_path / "pg" / "bin",
        pg_data=tmp_path / "pg" / "data",
        ffmpeg="ffmpeg-missing-xyz",
        ffprobe="ffprobe-missing-xyz",
        min_free_gb=5.0,
    )
    fields.update(overrides)
    return Config(**fields)


# --- file_version --------------------------------------------------------------------------------


def test_file_version_of_a_real_exe_is_a_dotted_version_string():
    version = checks.file_version(KNOWN_VERSIONED_FILE)
    assert version is not None
    parts = version.split(".")
    assert 3 <= len(parts) <= 4
    assert all(part.isdigit() for part in parts)


def test_file_version_of_a_non_exe_is_none(tmp_path):
    text = tmp_path / "not-an-exe.txt"
    text.write_text("hello", encoding="utf-8")
    assert checks.file_version(text) is None


# --- hlae_exe --------------------------------------------------------------------------------------


def _write_csdm_settings(csdm_home: Path, hlae: dict) -> None:
    settings_dir = csdm_home / ".csdm"
    settings_dir.mkdir(parents=True)
    (settings_dir / "settings.json").write_text(
        json.dumps({"video": {"hlae": hlae}, "database": {"password": "super-secret-pw"}}),
        encoding="utf-8",
    )


def test_hlae_exe_uses_the_custom_location_when_enabled_and_leaks_no_other_setting(tmp_path, caplog):
    csdm_home = tmp_path / "csdm-home"
    custom = tmp_path / "HLAE" / "hlae.exe"
    _write_csdm_settings(csdm_home, {"customLocationEnabled": True, "customExecutableLocation": str(custom)})
    exe = checks.hlae_exe(csdm_home)
    assert exe == custom
    assert "super-secret-pw" not in str(exe)
    assert "super-secret-pw" not in caplog.text


def test_hlae_exe_falls_back_to_csdms_own_copy_when_the_custom_location_is_off(tmp_path):
    csdm_home = tmp_path / "csdm-home"
    _write_csdm_settings(csdm_home, {"customLocationEnabled": False, "customExecutableLocation": ""})
    assert checks.hlae_exe(csdm_home) == csdm_home / ".csdm" / "hlae" / "HLAE.exe"


def test_hlae_exe_falls_back_when_the_custom_location_key_is_absent(tmp_path):
    csdm_home = tmp_path / "csdm-home"
    _write_csdm_settings(csdm_home, {})
    assert checks.hlae_exe(csdm_home) == csdm_home / ".csdm" / "hlae" / "HLAE.exe"


def test_hlae_exe_is_none_when_settings_cannot_be_read(tmp_path):
    assert checks.hlae_exe(tmp_path / "no-such-csdm-home") is None


def test_hlae_exe_is_none_when_settings_is_malformed_json(tmp_path):
    csdm_home = tmp_path / "csdm-home"
    (csdm_home / ".csdm").mkdir(parents=True)
    (csdm_home / ".csdm" / "settings.json").write_text("{not json", encoding="utf-8")
    assert checks.hlae_exe(csdm_home) is None


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
        pg_running=lambda pg_bin, pg_data: True,
        which=lambda name: None,
        free_bytes=lambda path: 999 * GB,
        version_of=lambda path: None,
    )
    kwargs.update(overrides)
    results = checks.run_checks(cfg, **kwargs)
    assert [c.name for c in results] == [
        "CS Demo Manager", "Postgres", "HLAE", "FFmpeg", "Clips folder", "Match alerts",
    ]
    return {c.name: c for c in results}


def test_run_checks_cs_demo_manager_missing(tmp_path):
    cfg = _cfg(tmp_path)
    result = _get(cfg)["CS Demo Manager"]
    assert (result.ok, result.detail) == (False, "not found")
    assert result.hint == "Install CS Demo Manager, or set its folder in Settings"


def test_run_checks_cs_demo_manager_found_with_a_version(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.csdm_app_dir.mkdir(parents=True)
    cfg.csdm_exe.write_bytes(b"")
    result = _get(cfg, version_of=lambda path: "3.20.1")["CS Demo Manager"]
    assert (result.ok, result.detail, result.hint) == (True, "3.20.1", "")


def test_run_checks_cs_demo_manager_found_without_a_readable_version(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.csdm_app_dir.mkdir(parents=True)
    cfg.csdm_exe.write_bytes(b"")
    result = _get(cfg, version_of=lambda path: None)["CS Demo Manager"]
    assert (result.ok, result.detail) == (True, "found")


def test_run_checks_postgres_running(tmp_path):
    cfg = _cfg(tmp_path)
    result = _get(cfg, pg_running=lambda a, b: True)["Postgres"]
    assert (result.ok, result.detail) == (True, "running")


def test_run_checks_postgres_not_running(tmp_path):
    cfg = _cfg(tmp_path)
    result = _get(cfg, pg_running=lambda a, b: False)["Postgres"]
    assert (result.ok, result.detail) == (False, "not running")
    assert "Settings" in result.hint


def test_run_checks_postgres_that_raises_counts_as_not_running(tmp_path):
    cfg = _cfg(tmp_path)

    def boom(pg_bin, pg_data):
        raise RuntimeError("pg_ctl exploded")

    result = _get(cfg, pg_running=boom)["Postgres"]
    assert (result.ok, result.detail) == (False, "not running")


def _install_hlae(cfg: Config, changelog_xml: str) -> Path:
    """CS:DM's own bundled HLAE (the custom location off), with a changelog.xml beside it."""
    _write_csdm_settings(cfg.csdm_home, {"customLocationEnabled": False, "customExecutableLocation": ""})
    exe = cfg.csdm_home / ".csdm" / "hlae" / "HLAE.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    exe.with_name("changelog.xml").write_text(changelog_xml, encoding="utf-8")
    return exe


def _release_xml(version: str) -> str:
    return f"<changelog><release><version>{version}</version></release></changelog>"


def test_run_checks_hlae_not_found(tmp_path):
    cfg = _cfg(tmp_path)
    result = _get(cfg)["HLAE"]
    assert (result.ok, result.detail) == (False, "not found")
    assert "video settings" in result.hint


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
    _write_csdm_settings(cfg.csdm_home, {"customLocationEnabled": False, "customExecutableLocation": ""})
    exe = cfg.csdm_home / ".csdm" / "hlae" / "HLAE.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")   # no changelog.xml beside it
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

    def failing_ensure(pg_bin, pg_data):
        raise RuntimeError("pg_ctl exploded")

    problems = checks.startup_problems(cfg, ensure_postgres=failing_ensure)
    assert problems == [
        "Set your SteamID in Settings",
        "Choose a clips folder in Settings",
        f"CS Demo Manager was not found in {cfg.csdm_app_dir}",
        f"CS Demo Manager's settings are missing from {cfg.csdm_home}",
        "Postgres won't start: pg_ctl exploded",
    ]


def test_startup_problems_a_set_but_missing_clips_folder_names_the_path_instead_of_unset(tmp_path):
    cfg = _cfg(tmp_path, subject_steamid="76561198192858303", data_root=tmp_path / "gone")
    problems = checks.startup_problems(cfg, ensure_postgres=lambda a, b: None)
    assert f"The clips folder {cfg.data_root} is missing" in problems
    assert not any("Choose a clips folder" in p for p in problems)


def test_startup_problems_empty_when_all_is_well(tmp_path):
    clips = tmp_path / "clips"
    clips.mkdir()
    cfg = _cfg(tmp_path, subject_steamid="76561198192858303", data_root=clips)
    cfg.csdm_app_dir.mkdir(parents=True)
    cfg.csdm_exe.write_bytes(b"")
    (cfg.csdm_home / ".csdm").mkdir(parents=True)
    (cfg.csdm_home / ".csdm" / "settings.json").write_text("{}", encoding="utf-8")
    problems = checks.startup_problems(cfg, ensure_postgres=lambda pg_bin, pg_data: None)
    assert problems == []


def test_startup_problems_always_attempts_postgres_even_with_earlier_problems(tmp_path):
    cfg = _cfg(tmp_path, subject_steamid="", data_root=None)
    calls = []

    def ensure(pg_bin, pg_data):
        calls.append((pg_bin, pg_data))

    checks.startup_problems(cfg, ensure_postgres=ensure)
    assert calls == [(cfg.pg_bin, cfg.pg_data)]
