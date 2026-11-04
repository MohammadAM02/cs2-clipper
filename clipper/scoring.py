"""Scoring: a round's facts become a Highlight with a Score, reasons, and whether it is worth clipping. Pure — no I/O.

Base rules name the Highlight Type; bonus rules add to the Score. Every reason shows on the Reels page as a tag, and a
round of one Frag with any reason past its base is worth clipping (spec: Pick your rounds, Worth clipping).
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
LOW_HP = 20                 # a Frag made on this much HP or less is worth clipping
CLUTCH_OPPONENTS = 2        # a Clutch is won against this many or more (CONTEXT.md, Clutch)


def _base_rules(facts: RoundFacts) -> list[tuple[str, int]]:
    """Every base rule that matches; the highest-scoring one names the Type."""
    return [KILL_RULES[min(facts.frags, 5)]]


def _bonus_rules(facts: RoundFacts) -> list[tuple[str, int]]:
    """Each reason past the base, with what it adds to the Score: the old bonuses score only with 2+ Frags (the knife
    whatever the count), and the reasons added for picking add nothing."""
    multi = facts.frags >= 2
    hp = facts.lowest_hp
    candidates = [
        ("all headshots", 10, multi and facts.headshots == facts.frags),
        ("knife kill", 30, facts.knife_frags > 0),
        ("no-scope", 10 if multi else 0, facts.noscope_frags > 0),
        ("through smoke", 5 if multi else 0, facts.smoke_frags > 0),
        ("blind kill", 10 if multi else 0, facts.blind_frags > 0),
        (f"clutch 1v{facts.clutch_opponents}", 0, facts.clutch_opponents >= CLUTCH_OPPONENTS),
        (f"{hp} HP", 0, hp is not None and hp <= LOW_HP),
        ("deagle", 0, facts.deagle_frags > 0),
        ("jump shot", 0, facts.airborne_frags > 0),
        ("wallbang", 0, facts.wallbang_frags > 0),
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
        worthy=facts.frags >= 2 or bool(bonuses),
    )


def score_match(facts: Iterable[RoundFacts]) -> list[Highlight]:
    """Every round with a Frag, as Highlights, in round order."""
    return [h for f in sorted(facts, key=lambda f: f.round) if (h := score_round(f)) is not None]
