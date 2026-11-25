"""The subject's Mistakes in a Demo, worked out from its kills and flashes (spec: Progress, Mistakes).

Pure arithmetic over the records in this module: no JSON, no I/O. `analysis.py` is the only code that reads csda's
output and turns it into these records (ADR 0002). Two players are on the same team when their team names match, as
in `stats.py`, so the sides swapping at half time change nothing.

A Mistake is a moment that usually costs the round and that the player could have played another way. Each one is
worked out from what the Demo says happened, never guessed from a number: a death is a Mistake for at most one reason,
the most telling one, so a round is not listed twice for the same death.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from clipper.stats import TRADE_WINDOW_S

OPENING_DEATH = "OPENING_DEATH"            # the round's first death, and no teammate traded it
DIED_BLIND = "DIED_BLIND"                  # killed while flashed
THREW_ADVANTAGE = "THREW_ADVANTAGE"        # died, untraded, with ADVANTAGE or more players in hand
TEAM_FLASH = "TEAM_FLASH"                  # a flashbang of theirs blinded a teammate for TEAM_FLASH_MIN_S or longer
TEAM_KILL = "TEAM_KILL"                    # killed a teammate
BOMB_DEATH = "BOMB_DEATH"                  # died when the bomb went off
OWN_DEATH = "OWN_DEATH"                    # died to their own grenade, or to the world (a fall)
ADVANTAGE = 2                              # players ahead that make a death a thrown advantage: 4v2, 3v1, 5v3
TEAM_FLASH_MIN_S = 2.0                     # a teammate blind for less is a graze, not a Mistake
SAME_FLASH_S = 0.25                        # players blinded this close together were blinded by one flashbang
BLIND_LOOKBACK_S = 6.0                     # how far back the flash that blinded a death is looked for
BOMB = "C4"                                # csda's weaponName for the bomb going off
# What the pages say for each Mistake Type: its name, and what to try next time. Listed in the order the pages show.
KINDS: dict[str, dict[str, str]] = {
    OPENING_DEATH: {
        "title": "First to die, untraded",
        "tip": "Take the first fight with a teammate close enough to trade you, or send a flash in first.",
    },
    DIED_BLIND: {
        "title": "Killed while blind",
        "tip": "Turn away from a flash you hear pop, and fall back behind cover until you can see again.",
    },
    THREW_ADVANTAGE: {
        "title": "Died with the numbers on your side",
        "tip": "Ahead on players, hold your angle and let them come to you: a death there gives the round back.",
    },
    TEAM_FLASH: {
        "title": "Blinded a teammate",
        "tip": "Call the flash before you throw it, and throw it so it pops out of your team's sight.",
    },
    TEAM_KILL: {
        "title": "Killed a teammate",
        "tip": "Check your fire and your grenades when a teammate is between you and the enemy.",
    },
    BOMB_DEATH: {
        "title": "Caught by the bomb",
        "tip": "With no time left to defuse, leave the site: the rifle you save is worth more than the death.",
    },
    OWN_DEATH: {
        "title": "Killed by your own grenade or a fall",
        "tip": "Mind where your molotov lands, and the long drops.",
    },
}
# The tip for a death while blind when the flash was the subject's own team's.
BLIND_BY_TEAM_TIP = "Wait for a teammate's flash to pop before you peek, and say when yours is in the air."


@dataclass(frozen=True)
class Round:
    number: int
    start_tick: int


@dataclass(frozen=True)
class Kill:
    tick: int
    round: int
    killer: str               # "" for the world
    victim: str
    weapon: str
    victim_blind: bool        # csda's is_victim_blinded


@dataclass(frozen=True)
class Flash:
    tick: int
    round: int
    flasher: str
    flashed: str
    seconds: float            # how long the flashed player was blind


@dataclass(frozen=True)
class Match:
    tickrate: float
    teams: dict[str, str]                     # each player's team name, by SteamID64
    rounds: tuple[Round, ...]
    kills: tuple[Kill, ...]
    flashes: tuple[Flash, ...] | None = None  # None: the analysis kept no flashes (one made before the app kept them)


@dataclass(frozen=True)
class Mistake:
    type: str
    round: int
    tick: int
    seconds: float            # into the round, from its start
    other: str = ""           # who else it is about: the killer, the teammate killed or blinded, the flasher
    weapon: str = ""          # what killed, for a death
    ours: int = 0             # THREW_ADVANTAGE: the subject's team alive before the death, the subject included
    theirs: int = 0           # THREW_ADVANTAGE: the enemies alive before it
    blind_s: float = 0.0      # TEAM_FLASH: the longest a teammate was blind
    blinded: int = 0          # TEAM_FLASH: how many teammates the flashbang blinded for TEAM_FLASH_MIN_S or longer
    team_flash: bool = False  # DIED_BLIND: the flash that blinded them was their own team's

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready, without the fields the Mistake's Type does not use."""
        out: dict[str, Any] = {"type": self.type, "round": self.round, "tick": self.tick, "t": self.seconds}
        if self.other:
            out["other"] = self.other
        if self.weapon:
            out["weapon"] = self.weapon
        if self.type == THREW_ADVANTAGE:
            out["ours"], out["theirs"] = self.ours, self.theirs
        if self.type == TEAM_FLASH:
            out["blind_s"], out["blinded"] = self.blind_s, self.blinded
        if self.type == DIED_BLIND:
            out["team_flash"] = self.team_flash
        return out


def find(match: Match, subject: str) -> list[Mistake]:
    """The subject's Mistakes in the match, in the order they happened. Empty when the subject is not in it."""
    team = match.teams
    if subject not in team:
        return []
    starts = {r.number: r.start_tick for r in match.rounds}
    window = round(TRADE_WINDOW_S * match.tickrate)
    sizes = Counter(team.values())

    def enemies(a: str, b: str) -> bool:
        return a in team and b in team and team[a] != team[b]

    def teammates(a: str, b: str) -> bool:
        return a in team and b in team and a != b and team[a] == team[b]

    def seconds(tick: int, number: int) -> float:
        return round(max(0, tick - starts[number]) / match.tickrate, 2)

    kills_in: dict[int, list[Kill]] = defaultdict(list)
    for kill in sorted(match.kills, key=lambda k: k.tick):
        if kill.round in starts:
            kills_in[kill.round].append(kill)
    flashes_on: dict[int, list[Flash]] = defaultdict(list)       # the flashes that blinded the subject, by round
    for flash in match.flashes or ():
        if flash.flashed == subject:
            flashes_on[flash.round].append(flash)

    found: list[Mistake] = []
    for number in sorted(starts):
        ks = kills_in.get(number, [])
        alive = dict(sizes)
        opened = False                                   # whether an enemy kill has happened in the round yet
        for at, kill in enumerate(ks):
            if kill.killer == subject and teammates(subject, kill.victim):
                found.append(Mistake(TEAM_KILL, number, kill.tick, seconds(kill.tick, number), other=kill.victim,
                                     weapon=kill.weapon))
            if kill.victim == subject:
                mistake = _death(match, kill, number, seconds(kill.tick, number), subject, first=not opened,
                                 traded=_traded(kill, ks[at:], window, enemies, teammates),
                                 ours=alive.get(team[subject], 0),
                                 theirs=sum(n for name, n in alive.items() if name != team[subject]),
                                 enemies=enemies, flashes=flashes_on.get(number, []), teams=team)
                if mistake is not None:
                    found.append(mistake)
            if enemies(kill.killer, kill.victim):
                opened = True
            if kill.victim in team:
                alive[team[kill.victim]] = max(0, alive.get(team[kill.victim], 0) - 1)
    found.extend(_team_flashes(match, subject, starts, teammates))
    found.sort(key=lambda m: m.tick)
    return found


def _traded(death: Kill, later: list[Kill], window: int, enemies, teammates) -> bool:
    """Whether a teammate of the dead player killed their killer within the trade window (`stats.TRADE_WINDOW_S`)."""
    for kill in later:
        if kill.tick - death.tick > window:
            return False
        if kill.victim == death.killer and enemies(kill.killer, kill.victim) and teammates(kill.killer, death.victim):
            return True
    return False


def _death(match: Match, kill: Kill, number: int, at: float, subject: str, *, first: bool, traded: bool, ours: int,
           theirs: int, enemies, flashes: list[Flash], teams: dict[str, str]) -> Mistake | None:
    """The Mistake the subject's death was, if it was one: the first of these that fits."""
    if not kill.killer or kill.killer == subject:
        return Mistake(BOMB_DEATH if kill.weapon == BOMB else OWN_DEATH, number, kill.tick, at, weapon=kill.weapon)
    if not enemies(kill.killer, subject):
        return None                                      # a teammate killed them: the teammate's Mistake, not theirs
    if kill.victim_blind:
        flasher = _blinded_by(flashes, kill.tick, match.tickrate)
        ours_flash = flasher is not None and teams.get(flasher) == teams[subject]
        return Mistake(DIED_BLIND, number, kill.tick, at, other=flasher if ours_flash else kill.killer,
                       weapon=kill.weapon, team_flash=ours_flash)
    if first and not traded:
        return Mistake(OPENING_DEATH, number, kill.tick, at, other=kill.killer, weapon=kill.weapon)
    if not traded and theirs >= 1 and ours - theirs >= ADVANTAGE:
        return Mistake(THREW_ADVANTAGE, number, kill.tick, at, other=kill.killer, weapon=kill.weapon, ours=ours,
                       theirs=theirs)
    return None


def _blinded_by(flashes: list[Flash], tick: int, tickrate: float) -> str | None:
    """Who threw the flash the subject was still blind from at `tick`: the latest one that covers it. None when the
    analysis has no such flash."""
    covering = [f for f in flashes
                if f.tick <= tick <= f.tick + f.seconds * tickrate and tick - f.tick <= BLIND_LOOKBACK_S * tickrate]
    return max(covering, key=lambda f: f.tick).flasher if covering else None


def _team_flashes(match: Match, subject: str, starts: dict[int, int], teammates) -> list[Mistake]:
    """One Mistake for each flashbang of the subject's that blinded a teammate for TEAM_FLASH_MIN_S or longer."""
    mine = sorted((f for f in match.flashes or ()
                   if f.flasher == subject and f.round in starts and teammates(subject, f.flashed)
                   and f.seconds >= TEAM_FLASH_MIN_S), key=lambda f: f.tick)
    same = SAME_FLASH_S * match.tickrate
    found: list[Mistake] = []
    bang: list[Flash] = []
    for flash in [*mine, None]:
        if flash is not None and (not bang or (flash.round == bang[0].round and flash.tick - bang[0].tick <= same)):
            bang.append(flash)
            continue
        if bang:
            worst = max(bang, key=lambda f: f.seconds)
            found.append(Mistake(TEAM_FLASH, worst.round, bang[0].tick,
                                 round(max(0, bang[0].tick - starts[worst.round]) / match.tickrate, 2),
                                 other=worst.flashed, blind_s=round(worst.seconds, 2), blinded=len(bang)))
        bang = [flash] if flash is not None else []
    return found


def tally(mistakes: list[dict[str, Any]]) -> dict[str, int]:
    """How many Mistakes of each Type, in KINDS order, without the Types that have none."""
    counts = Counter(m["type"] for m in mistakes)
    return {kind: counts[kind] for kind in KINDS if counts[kind]}
