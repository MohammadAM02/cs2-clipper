"""Tests for clipper.applog: the rotating file log and the ring buffer the Status page's Log
shows (spec: Pages, Status: Log)."""

from __future__ import annotations

import logging
import sys

import pytest

from clipper import applog


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """Every test here calls the real applog.setup(), which touches the root logger. Leave it
    exactly as found, and close every handler applog added so Windows doesn't keep the log file open."""
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    yield
    for handler in applog._added:
        handler.close()
    applog._added.clear()
    root.handlers[:] = original_handlers
    root.setLevel(original_level)
    applog._buffer.clear()


def test_setup_creates_the_log_file(tmp_path):
    applog.setup(tmp_path)
    logging.getLogger("clipper.test").info("hello")
    assert (tmp_path / "clipper.log").exists()


def test_a_logged_line_reaches_recent_formatted(tmp_path):
    applog.setup(tmp_path)
    logging.getLogger("clipper.test").info("hello %s", "world")
    lines = applog.recent()
    assert lines
    assert lines[-1].endswith("INFO clipper.test: hello world")


def test_recent_returns_the_newest_limit_lines_oldest_first(tmp_path):
    applog.setup(tmp_path)
    log = logging.getLogger("clipper.test")
    for n in range(5):
        log.info("line %d", n)
    lines = applog.recent(3)
    assert [line.rsplit(": ", 1)[-1] for line in lines] == ["line 2", "line 3", "line 4"]


def test_the_ring_keeps_only_lines_kept(tmp_path):
    applog.setup(tmp_path)
    log = logging.getLogger("clipper.test")
    total = applog.LINES_KEPT + 5
    for n in range(total):
        log.info("line %d", n)
    lines = applog.recent(total)
    assert len(lines) == applog.LINES_KEPT
    assert lines[0].endswith("line 5")                       # the first 5 fell off the ring
    assert lines[-1].endswith(f"line {total - 1}")


def test_calling_setup_twice_does_not_duplicate_lines(tmp_path):
    applog.setup(tmp_path)
    applog.setup(tmp_path)
    logging.getLogger("clipper.test").info("only once")
    matches = [line for line in applog.recent() if line.endswith("only once")]
    assert len(matches) == 1


def test_setup_again_never_touches_a_handler_it_did_not_add(tmp_path):
    root = logging.getLogger()
    someone_elses = logging.NullHandler()
    root.addHandler(someone_elses)
    try:
        applog.setup(tmp_path)
        applog.setup(tmp_path)
        assert someone_elses in root.handlers
    finally:
        root.removeHandler(someone_elses)


def test_a_stream_handler_is_added_only_when_stderr_exists(tmp_path, monkeypatch):
    def has_console_handler(handlers):
        return any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
                  for h in handlers)

    assert has_console_handler(applog.setup(tmp_path))

    monkeypatch.setattr(sys, "stderr", None)
    assert not has_console_handler(applog.setup(tmp_path))


def test_root_logger_is_set_to_info(tmp_path):
    applog.setup(tmp_path)
    assert logging.getLogger().level == logging.INFO


def test_setup_returns_the_handlers_it_added(tmp_path):
    handlers = applog.setup(tmp_path)
    assert handlers
    assert all(isinstance(h, logging.Handler) for h in handlers)
    assert set(logging.getLogger().handlers) >= set(handlers)
