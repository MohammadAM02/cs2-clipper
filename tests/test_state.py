"""Tests for clipper.state: the "what it's doing now" summary the tray tooltip and the Status page's
Now show (spec: How the app runs, The tray; Pages, Status)."""

from __future__ import annotations

import dataclasses

import pytest

from clipper.render import RenderProgress
from clipper.state import AppState, Rendering, Snapshot, summary

RENDERING = Rendering(map_name="de_mirage", perspective="player", started_at=1000.0)
PROGRESS = RenderProgress("recording", "player", done=1, total=2, overall=0.3, seconds_left=40.0)


def test_idle_by_default():
    assert summary(Snapshot()) == "Idle"


def test_waiting_joins_the_gates_reasons():
    snap = Snapshot(waiting=("CS2 is running", "FACEIT AC is running"))
    assert summary(snap) == "Waiting: CS2 is running; FACEIT AC is running"


def test_rendering_shows_the_map_and_perspective():
    assert summary(Snapshot(rendering=RENDERING)) == "Rendering Mirage (player view)"
    enemy = dataclasses.replace(RENDERING, perspective="enemy")
    assert summary(Snapshot(rendering=enemy)) == "Rendering Mirage (enemy view)"


def test_a_render_of_both_views_says_both_views():
    both = dataclasses.replace(RENDERING, perspective="both")
    assert summary(Snapshot(rendering=both)) == "Rendering Mirage (both views)"


def test_paused_by_you():
    assert summary(Snapshot(paused_by="you")) == "Paused by you"


def test_paused_by_failures():
    assert summary(Snapshot(paused_by="failures")) == "Paused after repeated failed renders"


def test_a_single_problem():
    assert summary(Snapshot(problems=("csda was not found",))) == "csda was not found"


def test_more_than_one_problem_counts_the_rest():
    snap = Snapshot(problems=("csda was not found", "FFmpeg was not found", "HLAE not found"))
    assert summary(snap) == "csda was not found (+2 more)"


def test_quitting_now():
    assert summary(Snapshot(quitting="now")) == "Quitting…"


def test_quitting_after_render_while_idle_is_still_just_quitting():
    assert summary(Snapshot(quitting="after_render")) == "Quitting…"


def test_quitting_after_render_while_rendering():
    snap = Snapshot(quitting="after_render", rendering=RENDERING)
    assert summary(snap) == "Quitting after this render"


# --- ordering: first match wins ------------------------------------------------------------------

def test_quitting_beats_everything():
    snap = Snapshot(quitting="now", problems=("disk full",), paused_by="you",
                    waiting=("CS2 is running",), rendering=RENDERING)
    assert summary(snap) == "Quitting…"


def test_problems_beat_rendering():
    snap = Snapshot(problems=("disk full",), rendering=RENDERING)
    assert summary(snap) == "disk full"


def test_rendering_beats_paused():
    snap = Snapshot(paused_by="you", rendering=RENDERING)
    assert summary(snap) == "Rendering Mirage (player view)"


def test_paused_beats_waiting():
    snap = Snapshot(paused_by="failures", waiting=("CS2 is running",))
    assert summary(snap) == "Paused after repeated failed renders"


# --- AppState --------------------------------------------------------------------------------------

def test_a_fresh_state_is_idle():
    assert summary(AppState().snapshot()) == "Idle"


def test_snapshot_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Snapshot().paused_by = "you"


def test_set_waiting_clears_rendering():
    state = AppState()
    state.set_rendering(RENDERING)
    state.set_waiting(["FACEIT AC is running"])
    snap = state.snapshot()
    assert snap.waiting == ("FACEIT AC is running",)
    assert snap.rendering is None


def test_set_rendering_clears_waiting():
    state = AppState()
    state.set_waiting(["FACEIT AC is running"])
    state.set_rendering(RENDERING)
    snap = state.snapshot()
    assert snap.rendering == RENDERING
    assert snap.waiting == ()


def test_set_idle_clears_waiting_and_rendering():
    state = AppState()
    state.set_rendering(RENDERING)
    state.set_idle()
    snap = state.snapshot()
    assert snap.rendering is None
    assert snap.waiting == ()


def test_setters_replace_the_snapshot_atomically_leaving_other_fields_alone():
    state = AppState()
    state.set_problems(["disk full"])
    state.set_paused_by("you")
    state.set_quitting("after_render")
    state.set_pages_off("no free port")
    snap = state.snapshot()
    assert snap.problems == ("disk full",)
    assert snap.paused_by == "you"
    assert snap.quitting == "after_render"
    assert snap.pages_off == "no free port"

    state.set_paused_by(None)
    state.set_quitting(None)
    state.set_pages_off(None)
    snap = state.snapshot()
    assert (snap.paused_by, snap.quitting, snap.pages_off) == (None, None, None)
    assert snap.problems == ("disk full",)          # untouched by the calls above


def test_a_rendering_has_no_progress_until_its_render_reports_some():
    assert RENDERING.progress is None
    state = AppState()
    state.set_rendering(RENDERING)
    assert state.snapshot().rendering.progress is None


def test_set_progress_is_the_progress_of_the_current_rendering():
    state = AppState()
    state.set_rendering(RENDERING)
    state.set_progress(PROGRESS)
    rendering = state.snapshot().rendering
    assert rendering.progress == PROGRESS
    assert rendering == dataclasses.replace(RENDERING, progress=PROGRESS)


def test_set_progress_does_nothing_when_nothing_is_rendering():
    state = AppState()
    state.set_progress(PROGRESS)
    assert state.snapshot().rendering is None
    state.set_rendering(RENDERING)
    state.set_idle()
    state.set_progress(PROGRESS)
    assert state.snapshot().rendering is None


def test_a_new_rendering_starts_without_the_progress_of_the_last_one():
    state = AppState()
    state.set_rendering(RENDERING)
    state.set_progress(PROGRESS)
    state.set_rendering(RENDERING)
    assert state.snapshot().rendering.progress is None
