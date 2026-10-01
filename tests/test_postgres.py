import subprocess
from pathlib import Path

import pytest

from clipper import paths, postgres, settings

BIN, DATA = Path("C:/pg/bin"), Path("C:/pg/data")


@pytest.mark.integration
def test_ensure_running_leaves_the_cluster_up():
    cfg = settings.load(paths.settings_file()).config
    if not (cfg.pg_bin / "pg_ctl.exe").exists():
        pytest.skip("the portable Postgres is not installed")
    postgres.ensure_running(cfg.pg_bin, cfg.pg_data)
    assert postgres.is_running(cfg.pg_bin, cfg.pg_data)


# --- the pg_ctl commands, with pg_ctl faked ----------------------------------------------------------


class FakePgCtl:
    """Stands in for subprocess.run: records each pg_ctl call's arguments and answers with the exit
    code set for its last word (`status`, `start`, `stop`). A cluster is down (status 3) by default."""

    def __init__(self):
        self.calls: list[list[str]] = []
        self.codes = {"status": 3}

    def __call__(self, args, **kwargs):
        assert args[0] == str(BIN / "pg_ctl.exe")
        self.calls.append(args[1:])
        return subprocess.CompletedProcess(args, self.codes.get(args[-1], 0))


@pytest.fixture
def pg_ctl(monkeypatch):
    fake = FakePgCtl()
    monkeypatch.setattr(postgres.subprocess, "run", fake)
    return fake


def test_a_cluster_that_is_down_is_started_on_the_port_asked_for(pg_ctl):
    postgres.ensure_running(BIN, DATA, 5433)

    assert pg_ctl.calls == [
        ["-D", str(DATA), "status"],
        ["-D", str(DATA), "-l", str(DATA.parent / "postgres.log"),
         "-o", "-p 5433 -c listen_addresses=127.0.0.1", "-w", "start"],
    ]


def test_the_port_is_5432_when_none_is_asked_for(pg_ctl):
    postgres.ensure_running(BIN, DATA)

    assert "-p 5432 -c listen_addresses=127.0.0.1" in pg_ctl.calls[1]


def test_a_cluster_that_is_up_is_not_started_again(pg_ctl):
    pg_ctl.codes["status"] = 0

    postgres.ensure_running(BIN, DATA, 5433)

    assert pg_ctl.calls == [["-D", str(DATA), "status"]]


def test_a_cluster_that_will_not_start_names_its_log(pg_ctl):
    pg_ctl.codes["start"] = 1

    with pytest.raises(RuntimeError, match="Postgres did not start; see .*postgres.log"):
        postgres.ensure_running(BIN, DATA, 5433)


def test_stop_waits_until_the_cluster_is_down(pg_ctl):
    pg_ctl.codes["status"] = 0

    postgres.stop(BIN, DATA)

    assert pg_ctl.calls == [["-D", str(DATA), "status"], ["-D", str(DATA), "-m", "fast", "-w", "stop"]]


def test_stop_leaves_a_cluster_that_is_down_alone(pg_ctl):
    postgres.stop(BIN, DATA)

    assert pg_ctl.calls == [["-D", str(DATA), "status"]]


def test_a_cluster_that_will_not_stop_is_reported(pg_ctl):
    pg_ctl.codes.update(status=0, stop=1)

    with pytest.raises(RuntimeError, match="Postgres did not stop"):
        postgres.stop(BIN, DATA)
