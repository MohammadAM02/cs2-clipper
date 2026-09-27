"""Autouse: gives every test its own CLIPPER_DATA_DIR, so none can touch the real app data folder."""
from __future__ import annotations

import pytest

from clipper.paths import ENV_VAR


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path_factory, monkeypatch):
    monkeypatch.setenv(ENV_VAR, str(tmp_path_factory.mktemp("appdata")))
