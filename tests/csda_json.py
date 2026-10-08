"""A made-up match in the shape cs-demo-analyzer (csda) writes it with `-format json`: only the fields the app
reads, plus a few it drops when it keeps its own copy. No real player is in it."""

from __future__ import annotations

import copy
from typing import Any

CHECKSUM = "0123456789abcdef"
DEMO_NAME = "1-00000000-0000-4000-8000-0000000000aa-1-1"
SUBJECT = "76561190000000001"
ALLY = "76561190000000002"
ENEMY = "76561190000000003"
OTHER_ENEMY = "76561190000000004"
ALPHA, BRAVO = "team_Alpha", "team_Bravo"
T, CT = 2, 3


def player(steam_id: str, name: str, user_id: int, team: str) -> dict[str, Any]:
    return {"steamId": int(steam_id), "name": name, "userId": user_id, "killCount": 0,
            "team": {"name": team, "letter": "A" if team == ALPHA else "B", "score": 0, "currentSide": T,
                     "scoreFirstHalf": 0, "scoreSecondHalf": 0}}


def round_(number: int, start: int, freeze_end: int, end: int, winner: str) -> dict[str, Any]:
    return {"number": number, "startTick": start, "freezeTimeEndTick": freeze_end, "endTick": end,
            "endOfficiallyTick": end + 448, "winnerName": winner, "winnerSide": T if winner == ALPHA else CT}


def kill(tick: int, round_number: int, killer: str | None, victim: str, *, killer_side: int = T,
         victim_side: int = CT, **flags: Any) -> dict[str, Any]:
    fields = {"tick": tick, "roundNumber": round_number, "killerSteamId": int(killer) if killer else 0,
              "victimSteamId": int(victim), "killerSide": killer_side, "victimSide": victim_side,
              "isHeadshot": False, "is_killer_blinded": False, "weaponType": "rifle", "weaponName": "AK-47",
              "isNoScope": False, "isThroughSmoke": False, "isKillerControllingBot": False}
    fields.update(flags)
    return fields


MATCH: dict[str, Any] = {
    "checksum": CHECKSUM,
    "date": "2026-10-03T21:54:05.2968446+04:00",
    "mapName": "de_mirage",
    "tickrate": 64,
    "tickCount": 40_000,
    "demoFileName": DEMO_NAME,
    "source": "faceit",
    "teamA": {"name": ALPHA, "letter": "A", "score": 13, "scoreFirstHalf": 8, "scoreSecondHalf": 5, "currentSide": CT},
    "teamB": {"name": BRAVO, "letter": "B", "score": 9, "scoreFirstHalf": 4, "scoreSecondHalf": 5, "currentSide": T},
    "players": {
        SUBJECT: player(SUBJECT, "subject", 3, ALPHA),
        ALLY: player(ALLY, "Ally", 0, ALPHA),
        ENEMY: player(ENEMY, "enemy", 7, BRAVO),
        OTHER_ENEMY: player(OTHER_ENEMY, "Bravo Two", 1, BRAVO),
    },
    "rounds": [
        round_(1, 1000, 2000, 9000, ALPHA),
        round_(2, 9500, 10500, 18000, BRAVO),
        round_(3, 18500, 19500, 27000, ALPHA),
    ],
    "kills": [
        kill(3100, 1, SUBJECT, ENEMY, isHeadshot=True),
        kill(3000, 1, SUBJECT, OTHER_ENEMY, isThroughSmoke=True, isNoScope=True),
        kill(3000, 1, ENEMY, ALLY, killer_side=CT, victim_side=T),
        kill(11000, 2, SUBJECT, ALLY, victim_side=T),                         # a team kill
        kill(11500, 2, None, SUBJECT, killer_side=0, victim_side=T),         # the world
        kill(20000, 3, SUBJECT, ENEMY, weaponType="melee", is_killer_blinded=True,
             isKillerControllingBot=True),
        kill(30000, 4, SUBJECT, OTHER_ENEMY),                                # a round with no row
    ],
    "clutches": [{"roundNumber": 3, "clutcherSteamId": int(SUBJECT), "opponentCount": 2, "hasWon": True}],
    "damages": [{"tick": 3000, "healthDamage": 100}],
    "playerPositions": [{"tick": 3000, "x": 1.0}],
    "grenadePositions": [],
    "shots": [{"tick": 2999}],
}


def match(**changes: Any) -> dict[str, Any]:
    """MATCH, or a copy of it with some top-level fields changed."""
    data = copy.deepcopy(MATCH)
    data.update(copy.deepcopy(changes))
    return data
