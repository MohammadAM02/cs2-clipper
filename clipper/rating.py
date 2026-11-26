"""Two HLTV-style ratings (spec: Rating).

`hltv21` is HLTV Rating 2.1 as the app shows it wherever it has the match's Demo: worked out from kills, deaths and
assists per round, ADR and KAST. HLTV has never published the formula of Rating 2.0 or 2.1; 2.1 (October 2024) kept
2.0's parts and retuned them for CS2. What the app uses is the community's reverse-engineered formula for those parts,
the one awpy ships (awpy.stats.rating), so it is an estimate: close to HLTV's, not equal to it.

`rating` is the estimate the app had before, from FACEIT's numbers alone, for the matches it has no Demo of (Demos to
grab): FACEIT reports no KAST, so 2.1 cannot be worked out for them.

`rating`: HLTV Rating 2.0, estimated from what FACEIT reports for one map.

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


# The reverse-engineered HLTV 2.x formula (awpy.stats.rating's defaults): KAST in percent, the others per round.
KAST_W = 0.0073
KPR_W = 0.3591
DPR_W = -0.5329
IMPACT_W = 0.2372
ADR_W = 0.0032
RATING_INTERCEPT = 0.1587
IMPACT_KPR = 2.13              # impact = 2.13 x kills per round + 0.42 x assists per round - 0.41
IMPACT_APR = 0.42
IMPACT_INTERCEPT = -0.41


def impact(kills: float, assists: float, rounds: int) -> float:
    return IMPACT_KPR * kills / rounds + IMPACT_APR * assists / rounds + IMPACT_INTERCEPT if rounds > 0 else 0.0


def hltv21(kills: float, deaths: float, assists: float, adr: float, kast_pct: float | None, rounds: int) -> float | None:
    """HLTV Rating 2.1, estimated (see above). None without rounds or without KAST, which it cannot do without."""
    if rounds <= 0 or kast_pct is None:
        return None
    value = (KAST_W * kast_pct + KPR_W * kills / rounds + DPR_W * deaths / rounds
             + IMPACT_W * impact(kills, assists, rounds) + ADR_W * adr + RATING_INTERCEPT)
    return round(max(value, 0.0), 2)
