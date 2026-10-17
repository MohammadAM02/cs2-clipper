"""Breaks: where in a match the sides switch or an overtime starts, and which of them someone talks in.

On FACEIT a Break is short: about 8 s of the round's end, then a freeze time of 26 s, where a round between has 7 s
and 20 s (24 matches, October 2026). Overtimes are MR3, as FACEIT and Valve play them, so there is a Break before
each overtime and one at its half."""

from __future__ import annotations

import bisect
from collections.abc import Iterable, Sequence

from clipper.hlae_plan import Round
from clipper.model import Break
from clipper.voice import FRAME_S

OT_HALF = 3             # rounds in each half of an overtime
MIN_TALK_S = 1.0        # less voice than this in a Break is nobody talking


def windows(rounds: Sequence[Round], max_rounds: int) -> list[Break]:
    """Each Break of the match whose rounds were both played: after the first half (`max_rounds` / 2), after the
    last round of regulation and after each overtime half."""
    if max_rounds < 2:
        return []
    by_number = {round_.number: round_ for round_ in rounds}
    half = max_rounds // 2
    found = []
    after = half
    while after + 1 in by_number:
        if after in by_number:
            found.append(Break(after, by_number[after].end_tick, by_number[after + 1].freeze_time_end_tick,
                               _label(after, max_rounds)))
        after = max_rounds if after < max_rounds else after + OT_HALF
    return found


def _label(after: int, max_rounds: int) -> str:
    if after < max_rounds:
        return "HALFTIME"
    overtime, half = divmod(after - max_rounds, 2 * OT_HALF)
    return f"OT {overtime + 1}" + (" HALF" if half else "")


def talking(breaks: Iterable[Break], ticks: Sequence[int]) -> list[Break]:
    """The `breaks` with MIN_TALK_S or more of voice in them, counting each voice message (its tick in `ticks`, which
    are in order) as FRAME_S of talk."""
    kept = []
    for found in breaks:
        messages = bisect.bisect_right(ticks, found.end_tick) - bisect.bisect_left(ticks, found.start_tick)
        if messages * FRAME_S >= MIN_TALK_S:
            kept.append(found)
    return kept
