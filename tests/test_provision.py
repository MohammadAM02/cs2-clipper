"""Tests for clipper.provision: nothing is downloaded, installed or started (but for the one test marked
`integration`, which runs this PC's own initdb on a folder and a port of its own).

`World` stands in for everything setup would fetch or run, and leaves on disk what the real thing
would: a fetch writes a small real zip laid out like the real download (so the real unpacking runs),
CS:DM's installer makes cs-demo-manager.exe, initdb makes PG_VERSION, and a Postgres that is up is an
entry in a dict. The app's settings and CS:DM's settings are the real files, under the test's own app
data folder."""
from __future__ import annotations

import json
import logging
import socket
import sys
import traceback
import zipfile
from pathlib import Path

import psycopg
import pytest

from clipper import checks, components, csdm_settings, paths, postgres, provision
from clipper.components import Asset
from clipper.config import Config
from clipper.download import DownloadError
from clipper.lock import single_instance
from clipper.settings import SettingsStore

GB = 1024**3
PASSWORD = "s3cret-do-not-log"                 # the one setup makes up
THEIRS = "hunter2-do-not-log"                  # one that was in CS:DM's settings already
STEPS = ["csdm", "postgres", "database", "ffmpeg", "hlae"]
PG_TOP = "postgresql-17.11.0-x86_64-pc-windows-msvc/"
FF_TOP = "ffmpeg-9.0.2-essentials_build/"
POSTGRES_FILES = ("bin/pg_ctl.exe", "bin/initdb.exe", "lib/libpq.dll", "share/postgres.bki",
                  "include/libpq-fe.h", "StackBuilder/bin/stackbuilder.exe")
FFMPEG_FILES = ("bin/ffmpeg.exe", "bin/ffprobe.exe", "bin/ffplay.exe", "doc/ffmpeg.html", "LICENSE", "README.txt")
CHANGELOG = '<?xml version="1.0"?><changelog><release><name>HLAE</name><version>{}</version></release></changelog>'
OURS = {"hostname": "127.0.0.1", "port": 5432, "username": "postgres", "password": PASSWORD, "database": "csdm"}


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return path


def names(folder: Path) -> list[str]:
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def own() -> Path:
    """Where setup keeps the Postgres it installs."""
    return paths.data_dir() / "postgres"


def downloads() -> Path:
    return paths.data_dir() / "downloads"


def write_settings(**values) -> None:
    file = paths.settings_file()
    current = json.loads(file.read_text(encoding="utf-8")) if file.exists() else {}
    file.write_text(json.dumps({**current, **{name: str(value) for name, value in values.items()}}), encoding="utf-8")


def write_csdm_settings(settings: dict) -> None:
    file = csdm_settings.settings_file(paths.csdm_home())
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(settings, indent=2), encoding="utf-8")


def both_settings_files() -> tuple[bytes, bytes]:
    return paths.settings_file().read_bytes(), csdm_settings.settings_file(paths.csdm_home()).read_bytes()


class World:
    def __init__(self, tmp_path: Path):
        self.csdm_dir = tmp_path / "Programs" / "cs-demo-manager"
        self.fetched: list[str] = []            # the files downloaded, in order
        self.calls: list[str] = []              # what was run, in order
        self.running: dict[Path, int] = {}      # data folder -> port, for each Postgres that is up
        self.busy_ports: set[int] = set()       # held by other programs
        self.on_path: dict[str, str] = {}       # program name -> where `which` finds it
        self.fail: dict[str, Exception] = {}    # what goes wrong: "fetch <file>", "initdb", "start", ...
        self.during: dict[str, object] = {}     # a call's name, or "half of <file>" -> called at that point
        self.postgres_files = POSTGRES_FILES
        self.free = 100 * GB
        self.missing_runtime: list[str] = []
        self.hlae_latest = "2.192.6"
        self.installer_exit = 0
        self.installer_installs = True
        self.installer_args: list[str] = []
        self.initdb_args: list[str] = []
        self.pwfile: Path | None = None
        self.pwfile_text = ""
        self.database_made_with: tuple[int, str] | None = None

    def tools(self) -> provision.Tools:
        return provision.Tools(
            fetch=self.fetch, run=self.run, which=self.on_path.get, latest_hlae=self.latest_hlae,
            port_free=lambda port: port not in self.busy_ports and port not in self.running.values(),
            create_database=self.create_database, ensure_postgres=self.start, stop_postgres=self.stop,
            new_password=lambda: PASSWORD, free_bytes=lambda path: self.free,
            missing_runtime=lambda pg_bin: list(self.missing_runtime), sleep=lambda seconds: None)

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
        if name == components.POSTGRES.name:
            return {PG_TOP + file: "x" for file in self.postgres_files}
        if name == components.FFMPEG.name:
            return {FF_TOP + file: "x" for file in FFMPEG_FILES}
        return {"HLAE.exe": "x", "changelog.xml": CHANGELOG.format(self.hlae_latest), "x64/AfxHookSource2.dll": "x"}

    def fetch(self, url, dest, *, sha256, size, progress):
        name = url.rsplit("/", 1)[1]
        assert dest == downloads() / name
        self.fetched.append(name)
        self._check(f"fetch {name}")
        asset = {a.name: a for a in (components.CSDM, components.POSTGRES, components.FFMPEG, self.hlae_asset())}[name]
        assert (url, sha256, size) == (asset.url, asset.sha256, asset.size)     # the pinned file, and checked
        progress(size // 2, size)
        self._check(f"half of {name}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        if name == components.CSDM.name:
            dest.write_bytes(b"installer")
        else:
            with zipfile.ZipFile(dest, "w") as zf:
                for entry, body in self._entries(name).items():
                    zf.writestr(entry, body)
        progress(size, size)
        return dest

    def run(self, args, *, capture=True):
        if Path(args[0]).name == "initdb.exe":
            return self._initdb(args, capture)
        assert Path(args[0]).name == components.CSDM.name, args
        return self._installer(args, capture)

    def _installer(self, args, capture):
        self.calls.append("installer")
        assert not capture          # anything the installer starts would hold a captured pipe open
        assert Path(args[0]).read_bytes() == b"installer"
        self.installer_args = args
        if self.installer_installs and self.installer_exit == 0:
            touch(self.csdm_dir / "cs-demo-manager.exe")
        return self.installer_exit, ""

    def _initdb(self, args, capture):
        self.calls.append("initdb")
        assert capture
        self.initdb_args = args
        self.pwfile = Path(args[args.index("--pwfile") + 1])
        self.pwfile_text = self.pwfile.read_text(encoding="utf-8")
        if "initdb" in self.fail:
            return 1, f"The files belonging to this database system...\n{self.fail['initdb']}\n"
        data = Path(args[args.index("-D") + 1])
        data.mkdir(parents=True)                # a leftover folder would make the real initdb fail too
        touch(data / "PG_VERSION")
        return 0, "Success."

    def start(self, pg_bin, data, port):
        self.calls.append(f"start {data.name} on {port}")
        self._check("start")
        assert (pg_bin / "pg_ctl.exe").exists() and (data / "PG_VERSION").exists()
        assert port not in self.busy_ports
        self.running[data] = port

    def stop(self, pg_bin, data):
        self.calls.append(f"stop {data.name}")
        self._check("stop")
        self.running.pop(data, None)

    def create_database(self, port, password):
        self.calls.append("create database")
        self._check("create database")
        assert port in self.running.values()
        self.database_made_with = (port, password)


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A fresh PC: nothing is installed, and the settings name folders that are not there."""
    world = World(tmp_path)
    monkeypatch.setattr(provision, "CSDM_DIR", world.csdm_dir)
    write_settings(csdm_app_dir=world.csdm_dir, pg_bin=tmp_path / "nowhere" / "bin", pg_data=tmp_path / "nowhere" / "data")
    return world


def context(world: World, latest: str | None = "2.192.6", **more) -> provision.Context:
    return provision.Context(store=SettingsStore(paths.settings_file()), tools=world.tools(), latest_hlae=latest, **more)


def install_by_hand(world: World, tmp_path: Path, hlae: str = "2.192.6") -> None:
    """A PC set up by hand, the way the developer's is: everything in folders of the user's choosing."""
    touch(world.csdm_dir / "cs-demo-manager.exe")
    pg = tmp_path / "pg17"
    touch(pg / "pgsql" / "bin" / "pg_ctl.exe")
    touch(pg / "data" / "PG_VERSION")
    world.on_path = {name: str(touch(tmp_path / "tools" / f"{name}.exe")) for name in ("ffmpeg", "ffprobe")}
    touch(tmp_path / "HLAE" / "hlae.exe")
    (tmp_path / "HLAE" / "changelog.xml").write_text(CHANGELOG.format(hlae), encoding="utf-8")
    write_settings(pg_bin=pg / "pgsql" / "bin", pg_data=pg / "data")
    write_csdm_settings({
        "schemaVersion": 14, "steamApiKey": THEIRS, "somethingNew": {"kept": True},
        "database": {**OURS, "password": THEIRS},
        "video": {"framerate": 30,
                  "ffmpegSettings": {"customLocationEnabled": True, "customExecutableLocation": world.on_path["ffmpeg"]},
                  "hlae": {"customLocationEnabled": True, "customExecutableLocation": str(tmp_path / "HLAE" / "hlae.exe")}},
    })


# --- a fresh PC --------------------------------------------------------------------------------------


def test_a_fresh_pc_needs_every_step(world):
    assert provision.needed(context(world)) == STEPS


def test_setup_installs_everything_in_order_and_then_nothing_is_needed(world):
    ctx = context(world)

    assert provision.run(ctx) is None

    assert world.fetched == [components.CSDM.name, components.POSTGRES.name, components.FFMPEG.name, "hlae_2_192_6.zip"]
    assert world.calls == ["installer", "initdb", "start data.part on 5432", "create database", "stop data.part",
                           "start data on 5432"]
    cfg, home = ctx.cfg(), paths.csdm_home()
    assert cfg.csdm_exe.exists()
    assert (cfg.pg_bin, cfg.pg_data) == (own() / "pgsql" / "bin", own() / "data")
    assert (cfg.pg_bin / "pg_ctl.exe").exists() and (cfg.pg_data / "PG_VERSION").exists()
    assert not (own() / "data.part").exists()
    assert world.running == {own() / "data": 5432}
    assert cfg.database_conninfo() == {"host": "127.0.0.1", "port": 5432, "user": "postgres", "password": PASSWORD,
                                       "dbname": "csdm"}
    assert world.database_made_with == (5432, PASSWORD)
    assert (cfg.ffmpeg, cfg.ffprobe) == (str(home / ".csdm" / "ffmpeg" / "bin" / "ffmpeg.exe"),
                                         str(home / ".csdm" / "ffmpeg" / "bin" / "ffprobe.exe"))
    assert csdm_settings.ffmpeg_exe(home) == Path(cfg.ffmpeg)            # CS:DM finds the same copy
    assert checks.hlae_exe(home) == home / ".csdm" / "hlae" / "HLAE.exe"
    assert checks.hlae_version(checks.hlae_exe(home)) == "2.192.6"
    assert checks.startup_problems(cfg, ensure_postgres=lambda *args: None)[2:] == []   # all but SteamID and clips folder
    assert provision.needed(ctx) == []


def test_only_what_is_used_is_unpacked(world):
    provision.run(context(world))

    assert names(own() / "pgsql") == ["bin/initdb.exe", "bin/pg_ctl.exe", "lib/libpq.dll", "share/postgres.bki"]
    assert names(paths.csdm_home() / ".csdm" / "ffmpeg") == ["LICENSE", "README.txt", "bin/ffmpeg.exe", "bin/ffprobe.exe"]
    assert names(paths.csdm_home() / ".csdm" / "hlae") == ["HLAE.exe", "changelog.xml", "x64/AfxHookSource2.dll"]


def test_downloads_are_removed_once_they_are_installed(world):
    provision.run(context(world))

    assert list(downloads().iterdir()) == []


def test_a_second_run_downloads_and_runs_nothing(world):
    ctx = context(world)
    provision.run(ctx)
    world.fetched.clear()
    world.calls.clear()
    before = both_settings_files()

    assert provision.run(ctx) is None

    assert (world.fetched, world.calls) == ([], [])
    assert both_settings_files() == before


# --- a PC that already has things --------------------------------------------------------------------


def test_a_pc_that_has_everything_is_left_alone(world, tmp_path):
    install_by_hand(world, tmp_path)
    ctx = context(world)
    before = both_settings_files()

    assert provision.needed(ctx) == []
    assert provision.run(ctx) is None

    assert (world.fetched, world.calls) == ([], [])
    assert both_settings_files() == before
    assert not own().exists() and not downloads().exists()


def test_a_cs_demo_manager_in_its_usual_folder_is_used_when_the_setting_points_elsewhere(world, tmp_path):
    touch(world.csdm_dir / "cs-demo-manager.exe")
    write_settings(csdm_app_dir=tmp_path / "moved-away")
    ctx = context(world)

    assert provision.needed(ctx) == STEPS
    assert provision.run(ctx) is None

    assert components.CSDM.name not in world.fetched and "installer" not in world.calls
    assert ctx.cfg().csdm_app_dir == world.csdm_dir


def test_an_ffmpeg_already_on_the_pc_is_used_and_cs_demo_manager_is_pointed_at_it(world, tmp_path):
    world.on_path = {name: str(touch(tmp_path / "tools" / f"{name}.exe")) for name in ("ffmpeg", "ffprobe")}
    ctx = context(world)

    assert provision.run(ctx) is None

    assert components.FFMPEG.name not in world.fetched
    assert (ctx.cfg().ffmpeg, ctx.cfg().ffprobe) == ("ffmpeg", "ffprobe")
    ffmpeg = csdm_settings.read(paths.csdm_home())["video"]["ffmpegSettings"]
    assert (ffmpeg["customLocationEnabled"], ffmpeg["customExecutableLocation"]) == (True, world.on_path["ffmpeg"])
    assert provision.needed(ctx) == []


def test_an_ffmpeg_without_its_ffprobe_gives_way_to_the_pair_setup_installs(world, tmp_path):
    world.on_path = {"ffmpeg": str(touch(tmp_path / "tools" / "ffmpeg.exe"))}
    ctx = context(world)

    assert provision.run(ctx) is None

    managed = paths.csdm_home() / ".csdm" / "ffmpeg" / "bin"
    assert (ctx.cfg().ffmpeg, ctx.cfg().ffprobe) == (str(managed / "ffmpeg.exe"), str(managed / "ffprobe.exe"))
    assert csdm_settings.ffmpeg_exe(paths.csdm_home()) == managed / "ffmpeg.exe"


def test_cs_demo_managers_own_settings_keep_everything_they_hold(world):
    theirs = {"hostname": "db.example", "port": 5999, "username": "me", "password": THEIRS, "database": "other"}
    write_csdm_settings({"schemaVersion": 14, "steamApiKey": THEIRS, "somethingNew": {"kept": True}, "database": theirs,
                         "video": {"framerate": 30, "hlae": {"customLocationEnabled": True,
                                                            "customExecutableLocation": "C:\\gone\\hlae.exe"}}})

    assert provision.run(context(world)) is None

    written = csdm_settings.read(paths.csdm_home())
    assert (written["schemaVersion"], written["steamApiKey"], written["somethingNew"]) == (14, THEIRS, {"kept": True})
    assert written["video"]["framerate"] == 30
    assert written["database"] == OURS                       # the new database, not one whose data is gone
    assert written["video"]["hlae"]["customLocationEnabled"] is False
    kept = csdm_settings.settings_file(paths.csdm_home()).with_name("settings.before-setup.json")
    assert json.loads(kept.read_text(encoding="utf-8"))["database"] == theirs     # the old connection is not lost


# --- the database ------------------------------------------------------------------------------------


def test_the_database_takes_the_first_port_nothing_else_holds(world):
    world.busy_ports = {5432, 5433}
    ctx = context(world)

    assert provision.run(ctx) is None

    assert ctx.cfg().database_conninfo()["port"] == 5434
    assert world.running == {own() / "data": 5434}
    assert checks.database_port(ctx.cfg()) == 5434          # the port the app starts Postgres on from now on


def test_a_pc_with_no_free_port_is_told_so(world):
    world.busy_ports = set(provision.PORTS)

    assert provision.run(context(world)) == "Database: no port from 5432 to 5531 is free"
    assert "initdb" not in world.calls


def test_initdb_is_given_the_password_in_a_file_that_is_gone_afterwards(world):
    provision.run(context(world))

    assert world.initdb_args == [
        str(own() / "pgsql" / "bin" / "initdb.exe"), "-D", str(own() / "data.part"), "-U", "postgres",
        "--pwfile", str(world.pwfile), "-E", "UTF8", "-A", "scram-sha-256", "--no-locale"]
    assert world.pwfile_text.strip() == PASSWORD
    assert world.pwfile.parent == own() and not world.pwfile.exists()


def test_an_initdb_that_fails_is_reported_and_leaves_no_password_file(world):
    world.fail["initdb"] = RuntimeError("initdb: error: could not create directory")
    ctx = context(world)

    assert provision.run(ctx) == "Database: initdb failed: initdb: error: could not create directory"

    assert not world.pwfile.exists()
    assert world.running == {} and csdm_settings.read(paths.csdm_home()) is None
    assert provision.needed(ctx) == ["database", "ffmpeg", "hlae"]


def test_a_data_folder_an_interrupted_run_left_half_made_is_made_again(world):
    part = own() / "data.part"
    touch(part / "PG_VERSION")
    touch(part / "junk")
    world.running[part] = 5432                  # and its Postgres was left up

    assert provision.run(context(world)) is None

    assert world.calls[1:4] == ["stop data.part", "initdb", "start data.part on 5432"]
    assert names(own() / "data") == ["PG_VERSION"] and not part.exists()


@pytest.mark.parametrize("where", ["the folder the settings name", "setup's own folder"])
def test_a_database_whose_password_is_not_known_is_never_touched(world, tmp_path, where):
    data = tmp_path / "nowhere" / "data" if where == "the folder the settings name" else own() / "data"
    touch(data / "PG_VERSION")
    ctx = context(world)

    error = provision.run(ctx)

    assert error.startswith("Database: ") and str(data) in error
    assert "initdb" not in world.calls and world.running == {}
    assert names(data) == ["PG_VERSION"]
    assert csdm_settings.read(paths.csdm_home()) is None
    assert provision.needed(ctx) == ["database", "ffmpeg", "hlae"]


def test_a_folder_in_the_databases_place_that_is_not_a_database_is_left_alone(world):
    touch(own() / "data" / "notes.txt")

    error = provision.run(context(world))

    assert error.startswith("Database: ") and "is in the way" in error and str(own() / "data") in error
    assert "initdb" not in world.calls and names(own() / "data") == ["notes.txt"]


def test_cs_demo_manager_settings_that_cannot_be_read_stop_setup_before_a_database_is_made(world):
    file = csdm_settings.settings_file(paths.csdm_home())
    file.parent.mkdir(parents=True)
    file.write_text("{not json", encoding="utf-8")

    assert provision.run(context(world)) == f"Database: CS Demo Manager's settings file cannot be read: {file}"
    assert "initdb" not in world.calls and file.read_text(encoding="utf-8") == "{not json"


def test_a_database_setup_made_before_is_taken_up_again_when_the_settings_lose_track_of_it(world, tmp_path):
    ctx = context(world)
    provision.run(ctx)
    write_settings(pg_data=tmp_path / "nowhere" / "data")       # as if settings.json had been made anew
    world.calls.clear()
    world.running.clear()

    assert provision.needed(ctx) == ["database"]
    assert provision.run(ctx) is None

    assert world.calls == ["start data on 5432"]                # nothing is made again
    assert ctx.cfg().pg_data == own() / "data"
    assert csdm_settings.database(paths.csdm_home()) == OURS


def test_the_database_password_reaches_neither_the_log_nor_an_error(world, caplog):
    caplog.set_level(logging.DEBUG)
    world.fail["create database"] = RuntimeError(f"connection failed: password={PASSWORD} was refused")

    error = provision.run(context(world))

    assert error.startswith("Database: connection failed: password=")
    assert PASSWORD not in error and PASSWORD not in caplog.text
    assert world.calls[-1] == "stop data.part" and world.running == {}     # its Postgres is not left up


def test_the_error_that_leaves_the_database_step_brings_no_password_along_however_it_is_printed(world):
    world.fail["create database"] = RuntimeError(f"connection failed: password={PASSWORD} was refused")
    ctx = context(world)
    steps = {step.id: step for step in provision.STEPS}
    steps["postgres"].install(ctx)

    with pytest.raises(provision.SetupError) as raised:
        steps["database"].install(ctx)

    error = raised.value
    assert PASSWORD not in "".join(traceback.format_exception(type(error), error, error.__traceback__))


def test_a_good_run_logs_each_step_and_no_password(world, caplog):
    caplog.set_level(logging.DEBUG)

    provision.run(context(world))

    assert [r.getMessage() for r in caplog.records if r.name == "clipper.provision" and "installing" in r.getMessage()] \
        == [f"setup: installing {name}" for name in ("CS Demo Manager", "Postgres", "Database", "FFmpeg", "HLAE")]
    assert PASSWORD not in caplog.text


def test_a_pc_without_the_vc_runtime_is_told_where_to_get_it(world):
    world.missing_runtime = ["msvcp140.dll"]

    error = provision.run(context(world))

    assert error.startswith("Database: ") and "msvcp140.dll" in error
    assert "https://aka.ms/vs/17/release/vc_redist.x64.exe" in error
    assert "initdb" not in world.calls


# --- HLAE --------------------------------------------------------------------------------------------


def test_hlae_is_installed_again_when_a_newer_release_is_out(world):
    ctx = context(world)
    provision.run(ctx)
    world.fetched.clear()
    world.hlae_latest = ctx.latest_hlae = "2.193.0"

    assert provision.needed(ctx) == ["hlae"]
    assert provision.run(ctx) is None

    assert world.fetched == ["hlae_2_193_0.zip"]
    assert checks.hlae_version(checks.hlae_exe(paths.csdm_home())) == "2.193.0"
    assert provision.needed(ctx) == []


def test_an_hlae_of_the_users_own_that_is_behind_gives_way_to_the_copy_setup_installs(world, tmp_path):
    install_by_hand(world, tmp_path, hlae="2.191.0")
    ctx = context(world)

    assert provision.needed(ctx) == ["hlae"]
    assert provision.run(ctx) is None

    home = paths.csdm_home()
    assert checks.hlae_exe(home) == home / ".csdm" / "hlae" / "HLAE.exe"
    assert checks.hlae_version(checks.hlae_exe(home)) == "2.192.6"
    assert names(tmp_path / "HLAE") == ["changelog.xml", "hlae.exe"]              # theirs is still there
    assert provision.needed(ctx) == []


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
    world.calls.clear()
    assert provision.run(ctx) is None
    assert (world.fetched, world.calls) == ([components.FFMPEG.name, "hlae_2_192_6.zip"], [])
    assert provision.needed(ctx) == []


def test_a_step_that_leaves_the_pc_as_it_was_is_not_called_done(world):
    world.postgres_files = ("bin/initdb.exe", "lib/libpq.dll")          # a download without pg_ctl.exe
    ctx = context(world)

    assert provision.run(ctx) == "Postgres: it was installed, but it is still not where the app looks for it"
    assert provision.needed(ctx) == ["postgres", "database", "ffmpeg", "hlae"]


def test_a_step_that_breaks_in_a_way_nobody_expected_is_reported_not_raised(world):
    world.fail["start"] = ZeroDivisionError("boom")

    assert provision.run(context(world)) == "Database: boom"


def test_too_little_free_space_refuses_before_anything_is_downloaded(world):
    world.free = GB

    error = provision.run(context(world))

    assert "2 GB" in error and "1.0 GB" in error and str(paths.data_dir()) in error
    assert (world.fetched, world.calls) == ([], [])


def test_free_space_does_not_matter_when_nothing_is_needed(world, tmp_path):
    install_by_hand(world, tmp_path)
    world.free = 0

    assert provision.run(context(world)) is None


def test_cs_demo_managers_installer_runs_silently(world):
    provision.run(context(world))

    assert world.installer_args == [str(downloads() / components.CSDM.name), "/S"]


def test_an_installer_that_fails_is_reported_and_its_download_is_kept(world):
    world.installer_exit = 2

    assert provision.run(context(world)) == "CS Demo Manager: its installer ended with code 2"
    assert (world.fetched, names(downloads())) == ([components.CSDM.name], [components.CSDM.name])


def test_an_installer_that_installs_nothing_is_reported(world):
    world.installer_installs = False

    assert provision.run(context(world)) == (
        f"CS Demo Manager: its installer finished, but {world.csdm_dir / 'cs-demo-manager.exe'} is not there")


def test_two_setups_cannot_run_at_once(world):
    with single_instance(paths.data_dir() / "setup.lock"):
        assert provision.run(context(world)) == "Setup is already running"

    assert world.fetched == []


def test_each_step_is_reported_as_it_starts_and_ends(world):
    world.fail["latest hlae"] = DownloadError("could not read the release")
    seen = []

    provision.run(context(world), lambda step, state: seen.append((step, state)))

    assert seen == [(step, state) for step in STEPS[:4] for state in ("running", "ok")] + [
        ("hlae", "running"), ("hlae", "failed")]


def test_download_progress_is_passed_on(world):
    seen = []

    provision.run(context(world, progress=lambda done, total: seen.append((done, total))))

    assert (components.POSTGRES.size, components.POSTGRES.size) in seen


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
        ("csdm", "CS Demo Manager", "needed"), ("postgres", "Postgres", "needed"), ("database", "Database", "needed"),
        ("ffmpeg", "FFmpeg", "needed"), ("hlae", "HLAE", "needed")]
    assert status["download_bytes"] == (components.CSDM.size + components.POSTGRES.size + components.FFMPEG.size
                                        + provision.HLAE_BYTES)


def test_status_counts_only_what_would_really_be_downloaded(world, tmp_path):
    world.on_path = {name: str(touch(tmp_path / "tools" / f"{name}.exe")) for name in ("ffmpeg", "ffprobe")}
    touch(world.csdm_dir / "cs-demo-manager.exe")

    status = provision.Setup(lambda: context(world)).status()

    assert states(status) == ["ok", "needed", "needed", "needed", "needed"]     # FFmpeg: CS:DM is not pointed at it
    assert status["download_bytes"] == components.POSTGRES.size + provision.HLAE_BYTES


def test_status_on_a_pc_that_has_everything_needs_nothing(world, tmp_path):
    install_by_hand(world, tmp_path)

    status = provision.Setup(lambda: context(world)).status()

    assert (status["needed"], status["download_bytes"], states(status)) == (False, 0, ["ok"] * 5)


def test_a_run_goes_on_a_thread_one_at_a_time_and_ends_with_nothing_needed(world):
    threads, ended = Threads(), []
    setup = provision.Setup(lambda: context(world), after=lambda: ended.append(True), spawn=threads)

    assert setup.start() is True
    assert setup.status()["running"] is True
    assert setup.start() is False                       # one run at a time
    threads.run()

    status = setup.status()
    assert (status["running"], status["needed"], status["error"], status["progress"]) == (False, False, None, None)
    assert states(status) == ["ok"] * 5
    assert ended == [True]
    assert world.fetched == [components.CSDM.name, components.POSTGRES.name, components.FFMPEG.name, "hlae_2_192_6.zip"]


def test_while_it_runs_status_tells_done_under_way_and_to_come_apart(world):
    threads, seen = Threads(), {}
    setup = provision.Setup(lambda: context(world), spawn=threads)
    world.during[f"half of {components.FFMPEG.name}"] = lambda: seen.update(setup.status())

    setup.start()
    threads.run()

    assert states(seen) == ["ok", "ok", "ok", "running", "needed"]
    assert (seen["running"], seen["needed"]) == (True, True)
    assert seen["progress"] == {"done": components.FFMPEG.size // 2, "total": components.FFMPEG.size}


def test_progress_is_of_the_download_under_way_and_of_no_earlier_one(world):
    threads, seen = Threads(), {}
    setup = provision.Setup(lambda: context(world), spawn=threads)
    world.during["create database"] = lambda: seen.update(setup.status())

    setup.start()
    threads.run()

    assert states(seen) == ["ok", "ok", "running", "needed", "needed"]
    assert seen["progress"] is None


def test_a_failed_run_keeps_its_error_until_the_next_one_starts(world):
    world.fail["latest hlae"] = DownloadError("could not read the release")
    threads, ended = Threads(), []
    setup = provision.Setup(lambda: context(world), after=lambda: ended.append(True), spawn=threads)
    setup.start()
    threads.run()

    status = setup.status()
    assert (status["running"], status["needed"], status["error"]) == (False, True, "HLAE: could not read the release")
    assert states(status) == ["ok", "ok", "ok", "ok", "needed"]
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


# --- the real tools ----------------------------------------------------------------------------------


def test_a_port_is_free_only_when_nothing_listens_on_it():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        port = server.getsockname()[1]
        assert provision.port_free(port) is False

    assert provision.port_free(port) is True


def test_the_vc_runtime_counts_as_there_beside_postgres_or_in_system32(tmp_path, monkeypatch):
    monkeypatch.setenv("SystemRoot", str(tmp_path / "Windows"))
    pg_bin = tmp_path / "bin"

    assert provision.missing_runtime(pg_bin) == ["vcruntime140.dll", "msvcp140.dll"]

    touch(tmp_path / "Windows" / "System32" / "vcruntime140.dll")
    touch(pg_bin / "msvcp140.dll")
    assert provision.missing_runtime(pg_bin) == []


def test_a_program_is_run_to_its_end_for_its_exit_code_and_what_it_said():
    script = "import sys; print('first'); print('last', file=sys.stderr); sys.exit(3)"

    code, said = provision.run_program([sys.executable, "-c", script])

    assert code == 3 and "first" in said and "last" in said
    assert provision.run_program([sys.executable, "-c", "print('unheard')"], capture=False) == (0, "")


@pytest.mark.integration
def test_the_database_step_makes_a_cluster_that_can_be_connected_to(tmp_path, monkeypatch):
    pg_bin = Config.pg_bin        # this PC's own Postgres programs; its data folder and its port are not used
    if not (pg_bin / "initdb.exe").exists():
        pytest.skip("no Postgres programs on this PC")
    monkeypatch.setattr(provision, "PORTS", range(5440, 5460))
    write_settings(pg_bin=pg_bin, pg_data=tmp_path / "nowhere")
    ctx = provision.Context(store=SettingsStore(paths.settings_file()))
    step = next(step for step in provision.STEPS if step.id == "database")
    try:
        step.install(ctx)

        cfg = ctx.cfg()
        assert cfg.pg_data == own() / "data" and not step.needed(ctx)
        assert postgres.is_running(pg_bin, cfg.pg_data)
        conninfo = cfg.database_conninfo()
        assert conninfo["port"] in provision.PORTS
        with psycopg.connect(**conninfo, connect_timeout=10) as conn:
            assert conn.execute("SELECT current_database()").fetchone() == ("csdm",)
            assert conn.execute("SHOW server_encoding").fetchone() == ("UTF8",)
        with pytest.raises(psycopg.OperationalError):
            psycopg.connect(**{**conninfo, "password": "wrong"}, connect_timeout=10)
        assert list(own().glob("pw-*")) == []
    finally:
        for folder in ("data", "data.part"):
            if (own() / folder / "PG_VERSION").exists():
                postgres.stop(pg_bin, own() / folder)
