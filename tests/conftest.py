"""Autouse: gives every test its own CLIPPER_DATA_DIR, so none can touch the real app data folder, and keeps every
test out of the registry and the real Videos folder: what registers the app with Windows writes nowhere, toasts
start out unregistered, and a fresh install's clips folder is made in a folder of the test's own."""
from __future__ import annotations

import pytest

from clipper import notify, windows
from clipper.paths import ENV_VAR


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path_factory, monkeypatch):
    monkeypatch.setenv(ENV_VAR, str(tmp_path_factory.mktemp("appdata")))


@pytest.fixture(autouse=True)
def _no_registry(monkeypatch, tmp_path_factory):
    videos = tmp_path_factory.mktemp("videos")
    monkeypatch.setattr(windows, "videos_dir", lambda: videos)        # a fresh install's clips folder goes here
    monkeypatch.setattr(windows, "set_user_value", lambda key, name, value: None)
    monkeypatch.setattr(windows, "set_app_id", lambda app_id: None)
    monkeypatch.setattr(notify, "_app_id", notify.POWERSHELL_ID)
