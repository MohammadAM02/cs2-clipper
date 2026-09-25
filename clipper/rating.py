"""HLTV Rating 2.0, estimated from what FACEIT reports for one map (spec: Rating 2.0).

Ported from faceitperf's estimateRating (https://github.com/iffypixy/faceitperf,
apps/web/src/features/stats.ts). One deliberate difference: with no rounds there is nothing to rate,
so the result is 0, where faceitperf's divide() would pass the raw count through.

MIT License

Copyright (c) 2024 Ansat Euler

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

from __future__ import annotations

from clipper.model import FaceitStats

INTERCEPT = 0.6844811040150518
PER_KILL = 0.65597945          # × kills per round
PER_ASSIST = 0.31304591        # × assists per round
PER_DEATH = -0.75999214        # × deaths per round
PER_ADR = 0.00370714           # × average damage per round
PER_MULTI_KILL = 0.72169367    # × rounds with 2+ Frags per round


def rating(stats: FaceitStats) -> float:
    if stats.rounds <= 0:
        return 0.0
    value = (INTERCEPT
             + PER_KILL * stats.kills / stats.rounds
             + PER_ASSIST * stats.assists / stats.rounds
             + PER_DEATH * stats.deaths / stats.rounds
             + PER_ADR * stats.adr
             + PER_MULTI_KILL * stats.multi_kill_rounds / stats.rounds)
    return max(value, 0.0)
