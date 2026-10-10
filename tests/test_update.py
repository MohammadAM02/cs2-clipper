"""The app's own updates: the newest Release on GitHub, its notes, and the one-click install.

Nothing here asks GitHub, downloads or runs an installer: each is a fake, and every file is under tmp_path."""
from __future__ import annotations

import logging
import tomllib
from pathlib import Path

import pytest

from clipper import config, update
from clipper.download import DownloadError

DIGEST = "ab" * 32
PAGE = "https://github.com/MohammadAM02/cs2-clipper/releases/tag/v0.3.0"
URL = "https://github.com/MohammadAM02/cs2-clipper/releases/download/v0.3.0/CS2Clipper-Setup.exe"


def release_json(tag="v0.3.0", *, digest=f"sha256:{DIGEST}", name="CS2Clipper-Setup.exe",
                 body="### Fixed\n- a bug") -> dict:
    asset = {"name": name, "size": 27_000_000, "browser_download_url": URL}
    if digest is not None:
        asset["digest"] = digest
    return {"tag_name": tag, "body": body, "html_url": PAGE, "assets": [{"name": "other.zip", "size": 1}, asset]}


def release(version="0.3.0", notes="### Fixed\n- a bug") -> update.Release:
    return update.Release(version=version, notes=notes, page=PAGE, installer=update.Asset(
        name=f"CS2Clipper-Setup-{version}.exe", url=URL, sha256=DIGEST, size=27_000_000))


# --- the newest Release ---------------------------------------------------------------------------------


def test_the_newest_release_is_read_from_this_repos_releases():
    asked = []
    found = update.latest_release(lambda url: asked.append(url) or release_json())
    assert asked == ["https://api.github.com/repos/MohammadAM02/cs2-clipper/releases/latest"]
    assert found == release()


def test_a_release_without_notes_has_none():
    assert update.latest_release(lambda url: release_json(body=None)).notes == ""


@pytest.mark.parametrize("data", [
    release_json(digest=None),
    release_json(digest="sha1:" + "ab" * 20),
], ids=["no digest", "not a sha256"])
def test_a_release_whose_installer_cannot_be_checked_is_refused(data):
    with pytest.raises(DownloadError, match="no SHA-256"):
        update.latest_release(lambda url: data)


@pytest.mark.parametrize("data", [
    release_json(name="CS2Clipper.exe"),
    {"tag_name": "v0.3.0"},
    {"tag_name": "v0.3.0", "assets": [{"name": "CS2Clipper-Setup.exe"}]},
], ids=["another file", "no assets", "no address"])
def test_a_release_without_an_installer_is_refused(data):
    with pytest.raises(DownloadError, match="no CS2Clipper-Setup.exe"):
        update.latest_release(lambda url: data)


def test_a_release_whose_tag_is_not_a_version_is_refused():
    with pytest.raises(DownloadError, match="not a version"):
        update.latest_release(lambda url: release_json(tag="nightly"))


@pytest.mark.parametrize("version, than, expected", [
    ("0.3.0", "0.2.0", True),
    ("v0.3.0", "0.2.0", True),
    ("0.10.0", "0.9.0", True),
    ("1.0.0", "0.99.99", True),
    ("0.2.0", "0.2.0", False),
    ("0.1.9", "0.2.0", False),
    ("nightly", "0.2.0", False),
    ("0.3.0", "dev", False),
])
def test_a_version_is_newer_only_when_its_numbers_are_higher(version, than, expected):
    assert update.newer(version, than) is expected


def test_the_running_version_is_pyprojects():
    with (config.REPO_ROOT / "pyproject.toml").open("rb") as file:
        assert update.running_version() == tomllib.load(file)["project"]["version"]


# --- the notes, as the Status page shows them ----------------------------------------------------------


def test_notes_become_headings_paragraphs_and_lists():
    notes = "### Fixed: a bug\nIt was bad.\nVery bad.\n\n- one\n- two\n\nAfter."
    assert update.notes_html(notes) == (
        "<h3>Fixed: a bug</h3><p>It was bad. Very bad.</p><ul><li>one</li><li>two</li></ul><p>After.</p>")


def test_a_list_item_that_goes_on_over_lines_is_one_item():
    assert update.notes_html("- one\n  and more\n* two") == "<ul><li>one and more</li><li>two</li></ul>"


def test_code_bold_and_https_links_are_kept():
    notes = "Set `USRLOCALCSGO` **once**, see [the notes](https://example.com/a?b=1) or https://example.com/c."
    assert update.notes_html(notes) == (
        '<p>Set <code>USRLOCALCSGO</code> <b>once</b>, see '
        '<a href="https://example.com/a?b=1" target="_blank" rel="noopener">the notes</a> or '
        '<a href="https://example.com/c" target="_blank" rel="noopener">https://example.com/c</a>.</p>')


def test_nothing_in_the_notes_becomes_markup_of_its_own():
    notes = '<script>alert(1)</script> & [x](javascript:alert(1)) `<b>` "quoted"'
    assert update.notes_html(notes) == (
        "<p>&lt;script&gt;alert(1)&lt;/script&gt; &amp; [x](javascript:alert(1)) <code>&lt;b&gt;</code> "
        "&quot;quoted&quot;</p>")


def test_a_link_cannot_end_its_own_attribute():
    assert update.notes_html('https://example.com/"onmouseover="x') == (
        '<p><a href="https://example.com/" target="_blank" rel="noopener">https://example.com/</a>'
        '&quot;onmouseover=&quot;x</p>')


def test_empty_notes_are_empty():
    assert update.notes_html("") == "" and update.notes_html("\n \n") == ""


# --- Updates: looking --------------------------------------------------------------------------------------


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeInstaller:
    """An installer that ends with `code` at once: an app still running then was not updated. What Status
    showed while it ran is kept in `seen`."""

    def __init__(self, updates_of, code=1):
        self.updates_of, self.code = updates_of, code
        self.seen: dict | None = None

    def wait(self) -> int:
        self.seen = self.updates_of().status()
        return self.code


class Fakes:
    def __init__(self, tmp_path: Path):
        self.folder = tmp_path / "downloads"
        self.logs = tmp_path / "logs"
        self.clock = FakeClock()
        self.latest: list = [release()]            # one answer per look; an exception is raised
        self.looks = 0
        self.busy: list[str | None] = [None]       # one answer per question, the last one kept
        self.announced: list[str] = []
        self.fetched: list[tuple] = []
        self.fetch_error: Exception | None = None
        self.progress: list[tuple[int, int]] = []
        self.during_download: list[dict] = []
        self.installs: list[tuple[list[str], dict]] = []
        self.installer_code = 1
        self.installer_error: Exception | None = None
        self.installers: list[FakeInstaller] = []
        self.jobs: list = []
        self.deferred = False
        self.updates: update.Updates | None = None

    def look(self) -> update.Release:
        self.looks += 1
        answer = self.latest.pop(0) if len(self.latest) > 1 else self.latest[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def is_busy(self) -> str | None:
        return self.busy.pop(0) if len(self.busy) > 1 else self.busy[0]

    def fetch(self, url, dest, *, sha256, size, progress):
        self.fetched.append((url, dest, sha256, size))
        for done, total in self.progress:
            progress(done, total)
            self.during_download.append(self.updates.status())
        if self.fetch_error is not None:
            raise self.fetch_error
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"installer")
        return dest

    def run_installer(self, command, environment):
        if self.installer_error is not None:
            raise self.installer_error
        self.installs.append((command, environment))
        self.installers.append(FakeInstaller(lambda: self.updates, self.installer_code))
        return self.installers[-1]

    def spawn(self, job):
        if self.deferred:
            self.jobs.append(job)
        else:
            job()

    def make(self, *, current="0.2.0", enabled=True) -> update.Updates:
        self.updates = update.Updates(
            current=current, enabled=enabled, folder=lambda: self.folder, logs=lambda: self.logs,
            busy=self.is_busy, announce=lambda found: self.announced.append(found.version), latest=self.look,
            fetch=self.fetch, run_installer=self.run_installer, spawn=self.spawn, clock=self.clock)
        return self.updates


@pytest.fixture
def fakes(tmp_path):
    return Fakes(tmp_path)


def test_a_source_run_never_asks_github(fakes):
    updates = fakes.make(enabled=False)
    updates.refresh_if_due()
    assert fakes.looks == 0
    assert updates.status()["available"] is None


def test_github_is_asked_at_start_and_then_every_six_hours(fakes):
    updates = fakes.make()
    updates.refresh_if_due()
    fakes.clock.now += 6 * 3600 - 1
    updates.refresh_if_due()
    assert fakes.looks == 1
    fakes.clock.now += 1
    updates.refresh_if_due()
    assert fakes.looks == 2


def test_a_look_that_fails_is_tried_again_in_an_hour(fakes, caplog):
    fakes.latest = [DownloadError("could not read it: offline"), release()]
    updates = fakes.make()
    with caplog.at_level(logging.WARNING, logger="clipper.update"):
        updates.refresh_if_due()
    assert "could not look for a newer CS2 Clipper (could not read it: offline)" in caplog.text
    assert updates.status()["available"] is None
    fakes.clock.now += 3600 - 1
    updates.refresh_if_due()
    assert fakes.looks == 1
    fakes.clock.now += 1
    updates.refresh_if_due()
    assert updates.status()["available"]["version"] == "0.3.0"


def test_a_newer_release_is_offered_with_its_notes(fakes):
    updates = fakes.make()
    updates.refresh_if_due()
    assert updates.status() == {
        "current": "0.2.0",
        "available": {"version": "0.3.0", "notes": "<h3>Fixed</h3><ul><li>a bug</li></ul>", "page": PAGE,
                      "bytes": 27_000_000},
        "running": False, "stage": None, "progress": None, "error": None,
    }


@pytest.mark.parametrize("version", ["0.2.0", "0.1.0"])
def test_the_running_or_an_older_release_is_not_offered(fakes, version):
    fakes.latest = [release(version)]
    updates = fakes.make()
    updates.refresh_if_due()
    assert updates.status()["available"] is None
    assert fakes.announced == []


def test_a_release_that_is_no_longer_the_newest_is_no_longer_offered(fakes):
    fakes.latest = [release("0.3.0"), release("0.2.0")]     # 0.3.0 was taken back
    updates = fakes.make()
    updates.refresh_if_due()
    fakes.clock.now += 6 * 3600
    updates.refresh_if_due()
    assert updates.status()["available"] is None


def test_each_newer_release_is_announced_once(fakes):
    fakes.latest = [release("0.3.0"), release("0.3.0"), release("0.4.0")]
    updates = fakes.make()
    for _ in range(3):
        updates.refresh_if_due()
        fakes.clock.now += 6 * 3600
    assert fakes.announced == ["0.3.0", "0.4.0"]


def test_installers_kept_from_earlier_updates_go_once_this_version_runs(fakes):
    fakes.folder.mkdir()
    for name in ("CS2Clipper-Setup-0.1.0.exe", "CS2Clipper-Setup-0.2.0.exe", "CS2Clipper-Setup-0.3.0.exe",
                 "ffmpeg-9.0.2-essentials_build.zip"):
        (fakes.folder / name).write_bytes(b"x")
    fakes.make().refresh_if_due()
    assert sorted(path.name for path in fakes.folder.iterdir()) == [
        "CS2Clipper-Setup-0.3.0.exe", "ffmpeg-9.0.2-essentials_build.zip"]


# --- Updates: the one click -------------------------------------------------------------------------------


def test_update_downloads_the_installer_checks_it_and_runs_it_silently(fakes):
    updates = fakes.make()
    updates.refresh_if_due()
    assert updates.start() is None
    installer = fakes.folder / "CS2Clipper-Setup-0.3.0.exe"
    assert fakes.fetched == [(URL, installer, DIGEST, 27_000_000)]
    [(command, _)] = fakes.installs
    assert command == [str(installer), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RELAUNCH",
                       f"/LOG={fakes.logs / 'update-0.3.0.log'}"]
    seen = fakes.installers[0].seen                 # the installer quits the app on its way
    assert (seen["running"], seen["stage"], seen["error"]) == (True, "installing", None)


def test_the_installer_and_the_app_it_starts_again_start_as_new_apps(fakes, monkeypatch):
    monkeypatch.setenv("_PYI_APPLICATION_HOME_DIR", r"C:\Temp\_MEI123")
    monkeypatch.setenv("_PYI_PARENT_PROCESS_LEVEL", "1")
    monkeypatch.setenv("CLIPPER_TEST_KEPT", "kept")
    updates = fakes.make()
    updates.refresh_if_due()
    updates.start()
    [(_, environment)] = fakes.installs
    assert not [name for name in environment if name.upper().startswith("_PYI_")]
    assert environment["CLIPPER_TEST_KEPT"] == "kept"


def test_the_download_shows_how_far_it_has_got(fakes):
    fakes.progress = [(5_000_000, 27_000_000)]
    updates = fakes.make()
    updates.refresh_if_due()
    updates.start()
    [seen] = fakes.during_download
    assert (seen["running"], seen["stage"], seen["progress"]) == (
        True, "downloading", {"done": 5_000_000, "total": 27_000_000})


def test_a_failed_download_says_why_and_can_be_tried_again(fakes):
    fakes.fetch_error = DownloadError("could not download CS2Clipper-Setup-0.3.0.exe: offline")
    updates = fakes.make()
    updates.refresh_if_due()
    updates.start()
    status = updates.status()
    assert (status["running"], status["stage"], status["error"]) == (
        False, None, "could not download CS2Clipper-Setup-0.3.0.exe: offline")
    assert fakes.installs == []

    fakes.fetch_error = None
    assert updates.start() is None
    assert len(fakes.installs) == 1


def test_an_installer_that_ends_without_updating_says_so(fakes, caplog):
    fakes.installer_code = 5
    updates = fakes.make()
    updates.refresh_if_due()
    with caplog.at_level(logging.WARNING, logger="clipper.update"):
        updates.start()
    log_file = fakes.logs / "update-0.3.0.log"
    error = f"The installer ended without updating (exit code 5). Its log is {log_file}."
    assert updates.status()["error"] == error
    assert updates.status()["running"] is False
    assert error in caplog.text


def test_an_installer_that_cannot_start_says_why(fakes):
    fakes.installer_error = OSError("blocked by policy")
    updates = fakes.make()
    updates.refresh_if_due()
    updates.start()
    assert updates.status()["error"] == "The installer could not be started: blocked by policy"
    assert updates.status()["running"] is False


def test_update_is_refused_while_the_app_is_busy(fakes):
    fakes.busy = ["A Reel is rendering. Update once it is done."]
    updates = fakes.make()
    updates.refresh_if_due()
    assert updates.start() == "A Reel is rendering. Update once it is done."
    assert fakes.fetched == [] and updates.status()["running"] is False


def test_a_render_that_starts_during_the_download_holds_the_install_back(fakes):
    fakes.busy = [None, "A Reel is rendering. Update once it is done."]
    updates = fakes.make()
    updates.refresh_if_due()
    assert updates.start() is None
    assert fakes.installs == []
    assert updates.status()["error"] == "A Reel is rendering. Update once it is done."
    assert (fakes.folder / "CS2Clipper-Setup-0.3.0.exe").is_file()      # kept for the next click


def test_without_a_newer_release_update_starts_nothing(fakes):
    fakes.latest = [release("0.2.0")]
    updates = fakes.make()
    updates.refresh_if_due()
    assert updates.start() == "CS2 Clipper is up to date."
    assert fakes.fetched == []


def test_a_second_click_while_updating_starts_no_second_update(fakes):
    fakes.deferred = True
    updates = fakes.make()
    updates.refresh_if_due()
    assert updates.start() is None
    assert updates.start() is None
    assert len(fakes.jobs) == 1
    assert updates.status()["running"] is True and updates.status()["stage"] == "downloading"
