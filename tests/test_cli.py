import json
from datetime import datetime, timezone

import pytest

from clipper import analysis, paths
from clipper.cli import cmd_highlights, cmd_resume, cmd_retry, format_status, main
from clipper.config import Config
from clipper.gate import GateStatus
from clipper.index import Index
from clipper.scoring import score_match
from clipper.settings import Loaded
from tests import csda_json


@pytest.fixture
def index(tmp_path):
    idx = Index(tmp_path / "clipper.sqlite")
    yield idx
    idx.close()


def test_status_of_an_empty_index(index):
    text = format_status(index, GateStatus(ok=True))
    assert text.splitlines() == ["Gate: clear", "No Demos yet. Download one from a FACEIT matchroom."]


def test_status_shows_why_the_gate_waits_and_why_a_demo_failed(index, tmp_path):
    demo_id = index.add_demo("1-a.dem.zst", "a" * 64, tmp_path / "1-a.dem.zst")
    index.fail(demo_id, "unpacked: disk full")
    index.pause("failures")
    lines = format_status(index, GateStatus(ok=False, reasons=("CS2 is running", "FACEIT AC is running"))).splitlines()
    assert lines[0] == "Gate: waiting: CS2 is running; FACEIT AC is running"
    assert lines[1] == ("Rendering: PAUSED after repeated failures. Check HLAE/CS2, then resume from the tray, "
                        "the Status page or: clipper resume")
    assert lines[2] == f"#{demo_id:<3} failed    1-a.dem.zst  - unpacked: disk full"


def test_status_says_who_paused_when_you_did(index):
    index.pause("you")
    lines = format_status(index, GateStatus(ok=True)).splitlines()
    assert lines[1] == "Rendering: PAUSED by you. Resume from the tray, the Status page or: clipper resume"


def test_status_falls_back_to_after_failures_for_an_index_paused_by_an_older_version(index):
    index.set_flag("paused", "1")   # no paused_by: an older version paused it
    lines = format_status(index, GateStatus(ok=True)).splitlines()
    assert lines[1] == ("Rendering: PAUSED after repeated failures. Check HLAE/CS2, then resume from the tray, "
                        "the Status page or: clipper resume")


def test_resume_clears_the_pause_and_prints(index, capsys):
    index.pause("failures")
    assert cmd_resume(index) == 0
    assert index.paused_by() is None
    assert "resumed" in capsys.readouterr().out


def test_retry_sends_a_failed_demo_back(index, tmp_path):
    demo_id = index.add_demo("1-a.dem.zst", "a" * 64, tmp_path / "1-a.dem.zst")
    index.advance(demo_id, "unpacked")
    index.fail(demo_id, "unpacked: boom")
    assert cmd_retry(index, str(demo_id)) == 0
    assert index.demo(demo_id)["state"] == "unpacked"
    assert cmd_retry(index, "nope") == 1


# --- highlights: a Demo's scored rounds, read from the app's own analysis of its match --------------


def analyzed(tmp_path) -> Config:
    """Settings whose analyses folder holds tests.csda_json's match, as the app keeps it."""
    cfg = Config(analyses_dir=tmp_path / "analyses", subject_steamid=csda_json.SUBJECT, top_n=1)
    cfg.analyses_dir.mkdir()
    (cfg.analyses_dir / f"{csda_json.CHECKSUM}.json").write_text(
        json.dumps(analysis.trim(csda_json.match())), encoding="utf-8")
    return cfg


def printed_rounds(out: str) -> list[str]:
    """The round column of each Highlight line: the round, marked "*" when it is chosen."""
    return [line.split()[0] for line in out.splitlines()[1:-1]]


def test_highlights_scores_an_analyzed_demo(index, tmp_path, capsys):
    cfg = analyzed(tmp_path)
    demo_id = index.add_demo("1-a.dem.zst", "a" * 64, tmp_path / "1-a.dem.zst")
    index.advance(demo_id, "analyzed", match_checksum=csda_json.CHECKSUM)
    scored = score_match(analysis.round_facts(csda_json.match(), csda_json.SUBJECT))
    best = max(scored, key=lambda h: h.score)

    assert cmd_highlights(cfg, index, str(demo_id)) == 0

    out = capsys.readouterr().out
    assert out.splitlines()[0].split() == ["round", "type", "score", "reasons"]
    assert printed_rounds(out) == [f"{h.round}{'*' if h is best else ''}" for h in scored]
    assert out.splitlines()[-1] == "* = in the top 1"


def test_highlights_takes_a_match_checksum_too(index, tmp_path, capsys):
    assert cmd_highlights(analyzed(tmp_path), index, csda_json.CHECKSUM) == 0

    assert printed_rounds(capsys.readouterr().out)


def test_highlights_of_a_demo_that_was_never_analyzed_says_so(index, tmp_path, capsys):
    cfg = analyzed(tmp_path)
    index.add_demo("1-a.dem.zst", "a" * 64, tmp_path / "1-a.dem.zst")

    assert cmd_highlights(cfg, index, "1-a.dem.zst") == 1
    assert cmd_highlights(cfg, index, "not-a-checksum") == 1

    assert capsys.readouterr().out.splitlines() == ["no analyzed Demo matches '1-a.dem.zst'",
                                                    "no analyzed Demo matches 'not-a-checksum'"]


def test_highlights_of_a_match_the_app_has_no_analysis_of_says_so(index, tmp_path, capsys):
    assert cmd_highlights(analyzed(tmp_path), index, "fedcba9876543210") == 1

    assert capsys.readouterr().out.strip() == "the app has no analysis of match fedcba9876543210"


def test_status_shows_match_alerts_and_the_page(index):
    index.set_flag("alerts_status", "on")
    index.set_flag("page_port", "8765")
    index.save_faceit_match("1-00000000-0000-0000-0000-000000000001",
                            datetime(2026, 9, 25, tzinfo=timezone.utc), "ready")
    lines = format_status(index, GateStatus(ok=True), host="gaming-pc").splitlines()
    assert lines[1] == "Match alerts: on · 1 to grab · http://gaming-pc:8765/demos"


def test_status_says_why_match_alerts_are_off(index):
    index.set_flag("alerts_status", "off: set your FACEIT nickname and API key in Settings")
    lines = format_status(index, GateStatus(ok=True)).splitlines()
    assert lines[1] == "Match alerts: off: set your FACEIT nickname and API key in Settings"


def test_status_says_the_clips_folder_is_not_set_instead_of_crashing(monkeypatch, capsys):
    monkeypatch.setattr("clipper.settings.load", lambda path: Loaded(Config(), ()))

    assert main(["status"]) == 0

    out = capsys.readouterr().out
    assert "Gate: waiting: the clips folder is not set" in out


# --- the app's modes: no subcommand and `run` go to app.run, --window to the window -----------------


@pytest.fixture
def run_calls(monkeypatch):
    calls = []
    monkeypatch.setattr("clipper.cli.app.run", lambda **kwargs: calls.append(kwargs) or 0)
    return calls


def test_no_subcommand_runs_the_app_with_a_window_and_a_tray(run_calls):
    assert main([]) == 0
    assert run_calls == [{"open_page": None, "background": False, "headless": False}]


def test_run_is_the_same_as_no_subcommand(run_calls):
    assert main(["run"]) == 0
    assert run_calls == [{"open_page": None, "background": False, "headless": False}]


def test_background_starts_in_the_tray_only(run_calls):
    assert main(["--background"]) == 0
    assert run_calls == [{"open_page": None, "background": True, "headless": False}]


def test_background_is_not_dropped_by_the_run_subcommand(run_calls):
    assert main(["--background", "run"]) == 0
    assert run_calls == [{"open_page": None, "background": True, "headless": False}]


def test_run_headless_runs_the_app_without_tray_or_window(run_calls):
    assert main(["run", "--headless"]) == 0
    assert run_calls == [{"open_page": None, "background": False, "headless": True}]


def test_install_is_no_longer_a_command():
    with pytest.raises(SystemExit):
        main(["install"])


# --- --open: top-level, and on the run subcommand ---------------------------------------------------


def test_open_reels_with_no_subcommand_reaches_run_as_reels(run_calls):
    assert main(["--open", "reels"]) == 0
    assert run_calls == [{"open_page": "/reels", "background": False, "headless": False}]


def test_run_open_reels_reaches_run_as_reels(run_calls):
    assert main(["run", "--open", "reels"]) == 0
    assert run_calls == [{"open_page": "/reels", "background": False, "headless": False}]


# --- --window URL: the window process (internal) ---------------------------------------------------


def test_window_shows_the_page_and_starts_nothing_else(monkeypatch, run_calls):
    shown = []
    monkeypatch.setattr("clipper.cli.window.run_window",
                        lambda url, profile_dir: shown.append((url, profile_dir)) or 0)
    monkeypatch.setattr("clipper.cli.move_in.on_start", lambda: pytest.fail("the window process moves nothing in"))

    assert main(["--window", "http://127.0.0.1:8765/status"]) == 0

    assert shown == [("http://127.0.0.1:8765/status", paths.data_dir() / "window-profile")]
    assert run_calls == []       # no app, so no lock and no worker either


def test_window_is_not_offered_in_help(capsys):
    with pytest.raises(SystemExit):
        main(["--help"])
    help_text = capsys.readouterr().out
    assert "--background" in help_text
    assert "--window" not in help_text
    assert "--bundle-check" not in help_text


@pytest.fixture
def events(monkeypatch):
    """What main() sets off, in order: the DLL search path release, and the role it hands over to."""
    seen: list[str] = []
    monkeypatch.setattr("clipper.packaged.release_dll_directory", lambda: seen.append("release"))
    monkeypatch.setattr("clipper.packaged.check", lambda report: seen.append(f"check {report.name}") or 0)
    monkeypatch.setattr("clipper.cli.app.run", lambda **kwargs: seen.append("app") or 0)
    monkeypatch.setattr("clipper.cli.window.run_window", lambda url, profile: seen.append("window") or 0)
    monkeypatch.setattr("clipper.cli.move_in.on_start", lambda: pytest.fail("moved in"))
    return seen


def test_the_app_releases_the_dll_search_path_before_it_starts_anything(events):
    # Its children (csda, HLAE, CS2, ffmpeg) must not inherit the exe's bundle on theirs.
    assert main([]) == 0
    assert events == ["release", "app"]


def test_the_window_process_keeps_the_bundle_on_its_dll_search_path(events):
    # pywebview loads .NET, pythonnet and WebView2 from the bundle.
    assert main(["--window", "http://127.0.0.1:1/status"]) == 0
    assert events == ["window"]


def test_the_bundle_check_runs_released_like_the_app_and_starts_nothing(events, tmp_path):
    assert main(["--bundle-check", str(tmp_path / "report.txt")]) == 0
    assert events == ["release", "check report.txt"]


# --- setup: the Status page's "Set up", from a terminal ----------------------------------------------


@pytest.fixture
def setup_runs(monkeypatch):
    """`clipper setup` with the run faked, so nothing is downloaded or installed and no log file is
    opened. Each run's Context is kept."""
    runs = []
    monkeypatch.setattr("clipper.cli.provision.run", lambda context: runs.append(context))
    monkeypatch.setattr("clipper.cli.checks.latest_hlae_release", lambda: "2.200.0")
    monkeypatch.setattr("clipper.cli.applog.setup", lambda logs_dir: [])
    monkeypatch.setattr("clipper.cli.move_in.on_start", lambda: pytest.fail("setup moves nothing in"))
    monkeypatch.setattr("clipper.cli.Index", lambda path: pytest.fail("setup opens no index"))
    return runs


def test_setup_installs_what_the_pc_lacks_and_exits_0(setup_runs, capsys):
    assert main(["setup"]) == 0

    [context] = setup_runs
    assert context.store.path == paths.settings_file()      # the app's own settings
    assert context.latest_hlae == "2.200.0"                 # so an HLAE that is behind is replaced
    assert "Everything is installed." in capsys.readouterr().out


def test_a_setup_that_fails_exits_1_and_says_why(monkeypatch, setup_runs, capsys):
    monkeypatch.setattr("clipper.cli.provision.run", lambda context: "FFmpeg: the download was cut short")

    assert main(["setup"]) == 1

    said = capsys.readouterr()
    assert "FFmpeg: the download was cut short" in said.err and "Everything is installed." not in said.out


def test_setup_goes_ahead_when_the_newest_hlae_cannot_be_asked_for(monkeypatch, setup_runs):
    def offline():
        raise OSError("no network")
    monkeypatch.setattr("clipper.cli.checks.latest_hlae_release", offline)

    assert main(["setup"]) == 0

    assert setup_runs[0].latest_hlae is None


def test_setup_writes_to_the_apps_log(monkeypatch, setup_runs):
    logged_to = []
    monkeypatch.setattr("clipper.cli.applog.setup", logged_to.append)

    main(["setup"])

    assert logged_to == [paths.logs_dir()]


def test_setup_tells_a_download_with_a_line_for_each_tenth(setup_runs, capsys):
    main(["setup"])
    progress = setup_runs[0].progress
    capsys.readouterr()

    for done in (0, 4, 9, 10, 55, 100, 0, 100):       # one download, and then the next
        progress(done, 100)

    assert capsys.readouterr().out.split() == ["0%", "10%", "50%", "100%", "0%", "100%"]


def test_setup_releases_the_dll_search_path_before_it_starts_anything(events, monkeypatch):
    # Nothing setup starts may inherit the exe's bundle on its DLL search path.
    monkeypatch.setattr("clipper.cli.cmd_setup", lambda: events.append("setup") or 0)

    assert main(["setup"]) == 0
    assert events == ["release", "setup"]


def test_quit_asks_the_running_copy_to_quit_and_starts_nothing_else(monkeypatch):
    # The installer runs it before it replaces the exe; its exit code says whether the copy has ended.
    monkeypatch.setattr("clipper.cli.app.quit_running", lambda: 1)
    monkeypatch.setattr("clipper.cli.app.run", lambda **kwargs: pytest.fail("quit starts no app"))
    monkeypatch.setattr("clipper.cli.move_in.on_start", lambda: pytest.fail("quit moves nothing in"))
    monkeypatch.setattr("clipper.cli.Index", lambda path: pytest.fail("quit opens no index"))

    assert main(["quit"]) == 1
