"""Tests for clipper.provision: nothing is downloaded or installed.

`World` stands in for everything setup would fetch, and leaves on disk what the real thing would: a
fetch writes a small real zip laid out like the real download, so the real unpacking runs. The app's
settings are the real file, under the test's own app data folder."""
from __future__ import annotations

import json
import logging
import zipfile
from pathlib import Path

import pytest

from clipper import checks, components, paths, provision
from clipper.components import Asset
from clipper.download import DownloadError
from clipper.lock import single_instance
from clipper.settings import SettingsStore

GB = 1024**3
STEPS = ["csda", "ffmpeg", "hlae"]
FF_TOP = "ffmpeg-9.0.2-essentials_build/"
FFMPEG_FILES = ("bin/ffmpeg.exe", "bin/ffprobe.exe", "bin/ffplay.exe", "doc/ffmpeg.html", "LICENSE", "README.txt")
CHANGELOG = '<?xml version="1.0"?><changelog><release><name>HLAE</name><version>{}</version></release></changelog>'


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def names(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def tools() -> Path:
    """Where setup installs what it installs."""
    return paths.data_dir() / "tools"


def downloads() -> Path:
    return paths.data_dir() / "downloads"


def write_settings(**values) -> None:
    file = paths.settings_file()
    current = json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}
    file.write_text(json.dumps({**current, **{name: str(value) for name, value in values.items()}}), encoding="utf-8")


def settings_file() -> bytes:
    return paths.settings_file().read_bytes()


class World:
    def __init__(self):
        self.fetched: list[str] = []            # the files downloaded, in order
        self.on_path: dict[str, str] = {}       # program name -> where `which` finds it
        self.fail: dict[str, Exception] = {}    # what goes wrong: "fetch <file>", "latest hlae"
        self.during: dict[str, object] = {}     # a call's name, or "half of <file>" -> called at that point
        self.csda_files = ("csda.exe",)
        self.free = 100 * GB
        self.hlae_latest = "2.192.6"

    def tools(self) -> provision.Tools:
        return provision.Tools(fetch=self.fetch, which=self.on_path.get, latest_hlae=self.latest_hlae,
                               free_bytes=lambda path: self.free)

    def _check(self, what: str) -> None:
        if what in self.during:
            self.during[what]()
        if what in self.fail:
            raise self.fail[what]

    def hlae_asset(self) -> Asset:
        name = f"hlae_{self.hlae_latest.replace('.', '_')}.zip"
        url = f"https://github.com/advancedfx/advancedfx/releases/download/v{self.hlae_latest}/{name}"
        return Asset(name, url, "c" * 64, 9_000_000)

    def latest_hlae(self) -> tuple[str, Asset]:
        self._check("latest hlae")
        return self.hlae_latest, self.hlae_asset()

    def _entries(self, name: str) -> dict[str, str]:
        if name == components.CSDA.name:
            return {file: "x" for file in self.csda_files}
        if name == components.FFMPEG.name:
            return {FF_TOP + file: "x" for file in FFMPEG_FILES}
        return {"HLAE.exe": "x", "changelog.xml": CHANGELOG.format(self.hlae_latest), "x64/AfxHookSource2.dll": "x"}

    def fetch(self, url, dest, *, sha256, size, progress):
        name = dest.name
        assert dest.parent == downloads()
        self.fetched.append(name)
        self._check(f"fetch {name}")
        asset = {a.name: a for a in (components.CSDA, components.FFMPEG, self.hlae_asset())}[name]
        assert (url, sha256, size) == (asset.url, asset.sha256, asset.size)     # the pinned file, and checked
        progress(size // 2, size)
        self._check(f"half of {name}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w") as zf:
            for entry, body in self._entries(name).items():
                zf.writestr(entry, body)
        progress(size, size)
        return dest


@pytest.fixture
def world():
    """A fresh PC: nothing is installed, and the settings are the defaults."""
    return World()


def context(world: World, latest: str | None = "2.192.6", **more) -> provision.Context:
    return provision.Context(store=SettingsStore(paths.settings_file()), tools=world.tools(), latest_hlae=latest, **more)


def install_by_hand(world: World, tmp_path: Path, hlae: str = "2.192.6") -> None:
    """A PC set up by hand, the way the developer's is: FFmpeg on PATH, HLAE in a folder of the user's
    choosing, and csda where setup puts it (there is no setting for it)."""
    touch(tools() / "csda" / "csda.exe")
    world.on_path = {name: str(touch(tmp_path / "bin" / f"{name}.exe")) for name in ("ffmpeg", "ffprobe")}
    touch(tmp_path / "HLAE" / "hlae.exe")
    (tmp_path / "HLAE" / "changelog.xml").write_text(CHANGELOG.format(hlae), encoding="utf-8")
    write_settings(hlae_exe=tmp_path / "HLAE" / "hlae.exe")


# --- a fresh PC --------------------------------------------------------------------------------------


def test_a_fresh_pc_needs_every_step(world):
    assert provision.needed(context(world)) == STEPS


def test_setup_installs_everything_in_order_and_then_nothing_is_needed(world):
    ctx = context(world)

    assert provision.run(ctx) is None

    assert world.fetched == [components.CSDA.name, components.FFMPEG.name, "hlae_2_192_6.zip"]
    cfg = ctx.cfg()
    assert cfg.csda_exe == tools() / "csda" / "csda.exe" and cfg.csda_exe.is_file()
    assert (cfg.ffmpeg, cfg.ffprobe) == (str(tools() / "ffmpeg" / "bin" / "ffmpeg.exe"),
                                         str(tools() / "ffmpeg" / "bin" / "ffprobe.exe"))
    assert cfg.hlae_exe == ""
    assert checks.hlae_exe(cfg) == tools() / "hlae" / "HLAE.exe"
    assert checks.hlae_version(checks.hlae_exe(cfg)) == "2.192.6"
    assert checks.startup_problems(cfg)[2:] == []       # all but SteamID and clips folder
    assert provision.needed(ctx) == []


def test_only_what_is_used_is_unpacked(world):
    provision.run(context(world))

    assert names(tools()) == ["csda/csda.exe", "ffmpeg/LICENSE", "ffmpeg/README.txt", "ffmpeg/bin/ffmpeg.exe",
                              "ffmpeg/bin/ffprobe.exe", "hlae/HLAE.exe", "hlae/changelog.xml",
                              "hlae/x64/AfxHookSource2.dll"]


def test_downloads_are_removed_once_they_are_installed(world):
    provision.run(context(world))

    assert list(downloads().iterdir()) == []


def test_a_second_run_downloads_nothing_and_changes_no_setting(world):
    ctx = context(world)
    provision.run(ctx)
    world.fetched.clear()
    before = settings_file()

    assert provision.run(ctx) is None

    assert world.fetched == []
    assert settings_file() == before


# --- a PC that already has things --------------------------------------------------------------------


def test_a_pc_that_has_everything_is_left_alone(world, tmp_path):
    install_by_hand(world, tmp_path)
    ctx = context(world)
    before = settings_file()

    assert provision.needed(ctx) == []
    assert provision.run(ctx) is None

    assert world.fetched == []
    assert settings_file() == before
    assert names(tools()) == ["csda/csda.exe"] and not downloads().exists()


def test_an_ffmpeg_already_on_the_pc_is_used(world, tmp_path):
    world.on_path = {name: str(touch(tmp_path / "bin" / f"{name}.exe")) for name in ("ffmpeg", "ffprobe")}
    ctx = context(world)

    assert provision.run(ctx) is None

    assert components.FFMPEG.name not in world.fetched
    assert (ctx.cfg().ffmpeg, ctx.cfg().ffprobe) == ("ffmpeg", "ffprobe")
    assert not (tools() / "ffmpeg").exists()
    assert provision.needed(ctx) == []


def test_an_ffmpeg_without_its_ffprobe_gives_way_to_the_pair_setup_installs(world, tmp_path):
    world.on_path = {"ffmpeg": str(touch(tmp_path / "bin" / "ffmpeg.exe"))}
    ctx = context(world)

    assert provision.run(ctx) is None

    managed = tools() / "ffmpeg" / "bin"
    assert (ctx.cfg().ffmpeg, ctx.cfg().ffprobe) == (str(managed / "ffmpeg.exe"), str(managed / "ffprobe.exe"))


def test_an_ffmpeg_setup_unpacked_before_is_taken_up_again_without_a_download(world):
    for name in ("ffmpeg", "ffprobe"):
        touch(tools() / "ffmpeg" / "bin" / f"{name}.exe")       # as if settings.json had been made anew
    ctx = context(world)

    assert provision.needed(ctx) == STEPS
    assert provision.Setup(lambda: ctx).status()["download_bytes"] == components.CSDA.size + provision.HLAE_BYTES
    assert provision.run(ctx) is None

    assert components.FFMPEG.name not in world.fetched
    assert ctx.cfg().ffmpeg == str(tools() / "ffmpeg" / "bin" / "ffmpeg.exe")


# --- HLAE --------------------------------------------------------------------------------------------


def test_hlae_is_installed_again_when_a_newer_release_is_out(world):
    ctx = context(world)
    provision.run(ctx)
    world.fetched.clear()
    world.hlae_latest = ctx.latest_hlae = "2.193.0"

    assert provision.needed(ctx) == ["hlae"]
    assert provision.run(ctx) is None

    assert world.fetched == ["hlae_2_193_0.zip"]
    assert checks.hlae_version(checks.hlae_exe(ctx.cfg())) == "2.193.0"
    assert provision.needed(ctx) == []


def test_an_hlae_of_the_users_own_that_is_behind_gives_way_to_the_copy_setup_installs(world, tmp_path):
    install_by_hand(world, tmp_path, hlae="2.191.0")
    ctx = context(world)

    assert provision.needed(ctx) == ["hlae"]
    assert provision.run(ctx) is None

    assert ctx.cfg().hlae_exe == ""
    assert checks.hlae_exe(ctx.cfg()) == tools() / "hlae" / "HLAE.exe"
    assert checks.hlae_version(checks.hlae_exe(ctx.cfg())) == "2.192.6"
    assert names(tmp_path / "HLAE") == ["changelog.xml", "hlae.exe"]              # theirs is still there
    assert provision.needed(ctx) == []


def test_an_hlae_path_that_names_no_file_gives_way_to_the_copy_setup_installs(world, tmp_path):
    install_by_hand(world, tmp_path)
    (tmp_path / "HLAE" / "hlae.exe").unlink()
    ctx = context(world)

    assert provision.needed(ctx) == ["hlae"]
    assert provision.run(ctx) is None

    assert ctx.cfg().hlae_exe == ""
    assert checks.hlae_exe(ctx.cfg()) == tools() / "hlae" / "HLAE.exe"


def test_hlae_is_not_called_behind_while_the_newest_release_is_unknown(world, tmp_path):
    install_by_hand(world, tmp_path, hlae="2.191.0")

    assert provision.needed(context(world, latest=None)) == []


# --- what can go wrong -------------------------------------------------------------------------------


def test_a_step_that_fails_stops_the_run_and_the_next_run_picks_up_there(world):
    world.fail[f"fetch {components.FFMPEG.name}"] = DownloadError("could not download ffmpeg: timed out")
    ctx = context(world)

    assert provision.run(ctx) == "FFmpeg: could not download ffmpeg: timed out"
    assert provision.needed(ctx) == ["ffmpeg", "hlae"]                 # HLAE was not tried

    world.fail.clear()
    world.fetched.clear()
    assert provision.run(ctx) is None
    assert world.fetched == [components.FFMPEG.name, "hlae_2_192_6.zip"]
    assert provision.needed(ctx) == []


def test_a_step_that_leaves_the_pc_as_it_was_is_not_called_done(world):
    world.csda_files = ("README.md",)          # a download without csda.exe
    ctx = context(world)

    assert provision.run(ctx) == "csda: it was installed, but it is still not where the app looks for it"
    assert provision.needed(ctx) == STEPS


def test_a_step_that_breaks_in_a_way_nobody_expected_is_reported_not_raised(world):
    world.fail["latest hlae"] = ZeroDivisionError("boom")

    assert provision.run(context(world)) == "HLAE: boom"


def test_too_little_free_space_refuses_before_anything_is_downloaded(world):
    world.free = GB

    error = provision.run(context(world))

    assert "2 GB" in error and "1.0 GB" in error and str(paths.data_dir()) in error
    assert world.fetched == []


def test_free_space_does_not_matter_when_nothing_is_needed(world, tmp_path):
    install_by_hand(world, tmp_path)
    world.free = 0

    assert provision.run(context(world)) is None


def test_two_setups_cannot_run_at_once(world):
    with single_instance(paths.data_dir() / "setup.lock"):
        assert provision.run(context(world)) == "Setup is already running"

    assert world.fetched == []


def test_each_step_is_reported_as_it_starts_and_ends(world):
    world.fail["latest hlae"] = DownloadError("could not read the release")
    seen = []

    provision.run(context(world), lambda step, state: seen.append((step, state)))

    assert seen == [(step, state) for step in STEPS[:2] for state in ("running", "ok")] + [
        ("hlae", "running"), ("hlae", "failed")]


def test_a_good_run_logs_each_step(world, caplog):
    caplog.set_level(logging.DEBUG)

    provision.run(context(world))

    assert [r.getMessage() for r in caplog.records if r.name == "clipper.provision" and "installing" in r.getMessage()] \
        == [f"setup: installing {name}" for name in ("csda", "FFmpeg", "HLAE")]


def test_download_progress_is_passed_on(world):
    seen = []

    provision.run(context(world, progress=lambda done, total: seen.append((done, total))))

    assert (components.FFMPEG.size, components.FFMPEG.size) in seen


# --- Setup: what Status shows ------------------------------------------------------------------------


class Threads:
    """Keeps what Setup would run on a thread, for the test to run when it wants."""

    def __init__(self):
        self.jobs = []

    def __call__(self, job):
        self.jobs.append(job)

    def run(self):
        while self.jobs:
            self.jobs.pop(0)()


def states(status: dict) -> list[str]:
    return [step["state"] for step in status["steps"]]


def test_status_on_a_fresh_pc_lists_every_step_as_needed(world):
    status = provision.Setup(lambda: context(world)).status()

    assert (status["running"], status["needed"], status["error"], status["progress"]) == (False, True, None, None)
    assert [(step["id"], step["name"], step["state"]) for step in status["steps"]] == [
        ("csda", "csda", "needed"), ("ffmpeg", "FFmpeg", "needed"), ("hlae", "HLAE", "needed")]
    assert status["download_bytes"] == components.CSDA.size + components.FFMPEG.size + provision.HLAE_BYTES


def test_status_counts_only_what_would_really_be_downloaded(world, tmp_path):
    world.on_path = {name: str(touch(tmp_path / "bin" / f"{name}.exe")) for name in ("ffmpeg", "ffprobe")}

    status = provision.Setup(lambda: context(world)).status()

    assert states(status) == ["needed", "ok", "needed"]
    assert status["download_bytes"] == components.CSDA.size + provision.HLAE_BYTES


def test_status_on_a_pc_that_has_everything_needs_nothing(world, tmp_path):
    install_by_hand(world, tmp_path)

    status = provision.Setup(lambda: context(world)).status()

    assert (status["needed"], status["download_bytes"], states(status)) == (False, 0, ["ok"] * 3)


def test_a_step_that_replaces_what_is_already_there_is_an_update(world, tmp_path):
    install_by_hand(world, tmp_path, hlae="2.191.0")

    status = provision.Setup(lambda: context(world)).status()

    assert [(step["id"], step["state"], step["update"]) for step in status["steps"] if step["state"] != "ok"] == [
        ("hlae", "needed", True)]
    assert [step["update"] for step in status["steps"]] == [False, False, True]


def test_on_a_fresh_pc_nothing_is_an_update(world):
    status = provision.Setup(lambda: context(world)).status()

    assert [step["update"] for step in status["steps"]] == [False] * 3


def test_a_run_goes_on_a_thread_one_at_a_time_and_ends_with_nothing_needed(world):
    threads, ended = Threads(), []
    setup = provision.Setup(lambda: context(world), after=lambda: ended.append(True), spawn=threads)

    assert setup.start() is True
    assert setup.status()["running"] is True
    assert setup.start() is False                       # one run at a time
    threads.run()

    status = setup.status()
    assert (status["running"], status["needed"], status["error"], status["progress"]) == (False, False, None, None)
    assert states(status) == ["ok"] * 3
    assert ended == [True]
    assert world.fetched == [components.CSDA.name, components.FFMPEG.name, "hlae_2_192_6.zip"]


def test_while_it_runs_status_tells_done_under_way_and_to_come_apart(world):
    threads, seen = Threads(), {}
    setup = provision.Setup(lambda: context(world), spawn=threads)
    world.during[f"half of {components.FFMPEG.name}"] = lambda: seen.update(setup.status())

    setup.start()
    threads.run()

    assert states(seen) == ["ok", "running", "needed"]
    assert (seen["running"], seen["needed"]) == (True, True)
    assert seen["progress"] == {"done": components.FFMPEG.size // 2, "total": components.FFMPEG.size}


def test_progress_is_of_the_download_under_way_and_of_no_earlier_one(world):
    threads, seen = Threads(), {}
    setup = provision.Setup(lambda: context(world), spawn=threads)
    world.during["latest hlae"] = lambda: seen.update(setup.status())      # before HLAE's own download begins

    setup.start()
    threads.run()

    assert states(seen) == ["ok", "ok", "running"]
    assert seen["progress"] is None


def test_a_failed_run_keeps_its_error_until_the_next_one_starts(world):
    world.fail["latest hlae"] = DownloadError("could not read the release")
    threads, ended = Threads(), []
    setup = provision.Setup(lambda: context(world), after=lambda: ended.append(True), spawn=threads)
    setup.start()
    threads.run()

    status = setup.status()
    assert (status["running"], status["needed"], status["error"]) == (False, True, "HLAE: could not read the release")
    assert states(status) == ["ok", "ok", "needed"]
    assert ended == [True]

    world.fail.clear()
    setup.start()
    assert setup.status()["error"] is None


def test_a_run_that_cannot_even_begin_still_ends(world):
    def no_context():
        raise OSError("settings.json is locked")
    threads, ended = Threads(), []
    setup = provision.Setup(no_context, after=lambda: ended.append(True), spawn=threads)

    setup.start()
    threads.run()

    assert ended == [True] and setup.start() is True        # not stuck on "running"


def test_running_is_true_from_the_start_of_a_run_to_its_end(world):
    threads = Threads()
    setup = provision.Setup(lambda: context(world), spawn=threads)

    assert setup.running is False
    setup.start()
    assert setup.running is True
    threads.run()
    assert setup.running is False


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_between_runs_the_pc_is_looked_at_only_every_few_seconds(world, tmp_path):
    clock, looks = Clock(), []

    def looked_at():
        looks.append(clock.now)
        return context(world)
    setup = provision.Setup(looked_at, clock=clock)

    assert setup.status()["needed"] is True
    install_by_hand(world, tmp_path)
    clock.now += provision.LOOK_SECONDS - 0.1
    assert setup.status()["needed"] is True and len(looks) == 1     # Status asks far more often than the PC changes

    clock.now += 0.1
    status = setup.status()
    assert (status["needed"], states(status), len(looks)) == (False, ["ok"] * 3, 2)


def test_what_status_is_given_cannot_change_what_the_next_one_is_told(world):
    setup = provision.Setup(lambda: context(world), clock=Clock())

    setup.status()["steps"].clear()

    assert states(setup.status()) == ["needed"] * 3


def test_what_a_run_changed_is_seen_as_soon_as_it_ends(world):
    threads = Threads()
    setup = provision.Setup(lambda: context(world), spawn=threads, clock=Clock())
    assert setup.status()["needed"] is True         # looked at just before the run

    setup.start()
    threads.run()

    assert setup.status()["needed"] is False        # not what that look found


def test_a_look_that_fails_is_answered_and_not_raised(world):
    def no_context():
        raise OSError("settings.json is locked")

    status = provision.Setup(no_context).status()

    assert status == {"running": False, "needed": True, "steps": [], "download_bytes": 0, "progress": None,
                      "error": "settings.json is locked"}
