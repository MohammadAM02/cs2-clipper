import pytest

from clipper import paths, postgres, settings

pytestmark = pytest.mark.integration


def test_ensure_running_leaves_the_cluster_up():
    cfg = settings.load(paths.settings_file()).config
    if not (cfg.pg_bin / "pg_ctl.exe").exists():
        pytest.skip("the portable Postgres is not installed")
    postgres.ensure_running(cfg.pg_bin, cfg.pg_data)
    assert postgres.is_running(cfg.pg_bin, cfg.pg_data)
