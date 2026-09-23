"""Scoring: a round's facts become a Highlight with a Score and reasons. Pure — no I/O.

Base rules name the Highlight Type; bonus rules add to the Score and appear only as reasons.
"""

from __future__ import annotations

from collections.abc import Iterable

from clipper.model import Highlight, RoundFacts

# Frags in the round -> (Highlight Type, base Score).
KILL_RULES: dict[int, tuple[str, int]] = {
    1: ("FRAG", 5),
    2: ("2K", 15),
    3: ("3K", 40),
    4: ("4K", 70),
    5: ("ACE", 100),
}


def _base_rules(facts: RoundFacts) -> list[tuple[str, int]]:
    """Every base rule that matches; the highest-scoring one names the Type. Clutch rules join
    here once issue 07 validates CS:DM's clutches table."""
    return [KILL_RULES[min(facts.frags, 5)]]


def _bonus_rules(facts: RoundFacts) -> list[tuple[str, int]]:
    multi = facts.frags >= 2
    candidates = [
        ("all headshots", 10, multi and facts.headshots == facts.frags),
        ("knife kill", 30, facts.knife_frags > 0),
        ("no-scope", 10, multi and facts.noscope_frags > 0),
        ("through smoke", 5, multi and facts.smoke_frags > 0),
        ("blind kill", 10, multi and facts.blind_frags > 0),
    ]
    return [(reason, points) for reason, points, matched in candidates if matched]


def score_round(facts: RoundFacts) -> Highlight | None:
    """The round as a Highlight, or None when the subject has no Frag in it."""
    if facts.frags == 0:
        return None
    bases = sorted(_base_rules(facts), key=lambda rule: rule[1], reverse=True)
    highlight_type, base_score = bases[0]
    bonuses = _bonus_rules(facts)
    return Highlight(
        round=facts.round,
        type=highlight_type,
        score=base_score + sum(points for _, points in bonuses),
        reasons=tuple(name.lower() for name, _ in bases) + tuple(reason for reason, _ in bonuses),
        frag_ticks=facts.frag_ticks,
        round_start_tick=facts.round_start_tick,
        round_end_tick=facts.round_end_tick,
    )


def score_match(facts: Iterable[RoundFacts]) -> list[Highlight]:
    """Every round with a Frag, as Highlights, in round order."""
    return [h for f in sorted(facts, key=lambda f: f.round) if (h := score_round(f)) is not None]


def select(highlights: Iterable[Highlight], n: int) -> list[Highlight]:
    """The n best Highlights by Score; a tie goes to the earlier round."""
    return sorted(highlights, key=lambda h: (-h.score, h.round))[:n]
