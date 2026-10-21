"""What each player did in a Demo, worked out from its events (spec: Stats).

Pure arithmetic over the records in this module: no JSON, no I/O. `analysis.py` is the only code that reads csda's
output and turns it into these records (ADR 0002). Two players are on the same team when their team names match,
so the sides swapping at half time change nothing. A kill is an enemy kill when its killer and victim are on
different teams; a death is any death, the way the scoreboard counts it.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

VERSION = 1
TRADE_WINDOW_S = 5.0                                                          # a kill trades a death this soon after it
UTILITY_WEAPONS = frozenset({"HE Grenade", "Molotov", "Incendiary Grenade"})  # csda's names for the damaging grenades
MULTI_KILL_SIZES = (2, 3, 4, 5)                                               # rounds with exactly this many Frags (5: an ace)
CLUTCH_MIN_OPPONENTS = 2                  # CONTEXT.md: a Clutch is the last player of a team against two or more
# FACEIT's per-player fields the headline numbers and the comparison use, by the name FACEIT gives them.
FACEIT_FIELDS = {
    "kills": "Kills", "deaths": "Deaths", "assists": "Assists", "adr": "ADR", "hs_pct": "Headshots %",
    "kd": "K/D Ratio", "kr": "K/R Ratio", "damage": "Damage", "headshots": "Headshots", "mvps": "MVPs",
    "double_kills": "Double Kills", "triple_kills": "Triple Kills", "quadro_kills": "Quadro Kills",
    "penta_kills": "Penta Kills", "first_kills": "First Kills", "entry_count": "Entry Count",
    "entry_wins": "Entry Wins", "enemies_flashed": "Enemies Flashed", "flash_successes": "Flash Successes",
    "utility_damage": "Utility Damage", "clutch_kills": "Clutch Kills", "one_v1": "1v1Count",
    "one_v1_wins": "1v1Wins", "one_v2": "1v2Count", "one_v2_wins": "1v2Wins",
}


@dataclass(frozen=True)
class Player:
    steam_id: str
    name: str
    team: str


@dataclass(frozen=True)
class Round:
    number: int
    start_tick: int
    end_tick: int
    winner: str          # the winning team's name
    end_reason: str      # csda's end reason, as written


@dataclass(frozen=True)
class Kill:
    tick: int
    round: int
    killer: str          # "" for the world
    victim: str
    assister: str        # "" when nobody assisted
    weapon: str
    headshot: bool
    flash_assist: bool   # csda's isAssistedFlash


@dataclass(frozen=True)
class Damage:
    round: int
    attacker: str        # "" for the world
    victim: str
    health: int          # health the hit took from the victim
    weapon: str


@dataclass(frozen=True)
class Flash:
    tick: int
    round: int
    flasher: str
    flashed: str
    seconds: float       # how long the flashed player was blind


@dataclass(frozen=True)
class Bomb:
    tick: int
    round: int
    event: str           # "planted", "defused" or "exploded"
    player: str          # "" when csda names nobody
    site: str


@dataclass(frozen=True)
class Clutch:
    round: int
    player: str
    opponents: int
    won: bool


@dataclass(frozen=True)
class Match:
    checksum: str
    tickrate: float
    players: tuple[Player, ...]
    rounds: tuple[Round, ...]
    kills: tuple[Kill, ...]
    damages: tuple[Damage, ...] = ()
    flashes: tuple[Flash, ...] = ()
    bombs: tuple[Bomb, ...] = ()
    clutches: tuple[Clutch, ...] = ()


@dataclass
class _Tally:
    kills: int = 0
    deaths: int = 0
    assists: int = 0
    headshot_kills: int = 0
    flash_assists: int = 0
    damage: int = 0
    utility_damage: int = 0
    kast_rounds: int = 0
    opening_kills: int = 0
    opening_deaths: int = 0
    trade_kills: int = 0
    traded_deaths: int = 0
    enemies_flashed: int = 0
    blind_seconds: float = 0.0
    team_flashes: int = 0
    clutch_attempts: int = 0
    clutch_wins: int = 0
    bomb_plants: int = 0
    bomb_defuses: int = 0
    multi_kill_rounds: dict[int, int] = field(default_factory=lambda: dict.fromkeys(MULTI_KILL_SIZES, 0))


def _pct(part: float, whole: float) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def _seconds(ticks: int, tickrate: float) -> float:
    return round(ticks / tickrate, 2)


def compute(match: Match) -> dict[str, Any]:
    """The match's stats for every player, and its round timeline. JSON-ready: every key is a string."""
    team = {p.steam_id: p.team for p in match.players}
    rounds = sorted(match.rounds, key=lambda r: r.number)
    numbers = {r.number for r in rounds}
    tally = {p.steam_id: _Tally() for p in match.players}
    window = round(TRADE_WINDOW_S * match.tickrate)

    def enemies(a: str, b: str) -> bool:
        return a in team and b in team and team[a] != team[b]

    def teammates(a: str, b: str) -> bool:
        return a in team and b in team and a != b and team[a] == team[b]

    kills_in: dict[int, list[Kill]] = defaultdict(list)
    for kill in sorted(match.kills, key=lambda k: k.tick):
        kills_in[kill.round].append(kill)
    bombs_in: dict[int, list[Bomb]] = defaultdict(list)
    for bomb in sorted(match.bombs, key=lambda b: b.tick):
        bombs_in[bomb.round].append(bomb)

    # A trade: a teammate's death at the hands of an enemy, avenged by one of the dead player's teammates killing
    # that enemy within TRADE_WINDOW_S. Each death is traded at most once.
    traded: set[tuple[int, str]] = set()
    trade_kills: set[Kill] = set()
    for number in numbers:
        ks = kills_in.get(number, [])
        for death in ks:
            if not enemies(death.killer, death.victim):
                continue
            for kill in ks:
                if kill.tick < death.tick:
                    continue
                if kill.tick - death.tick > window:
                    break
                if (enemies(kill.killer, kill.victim) and kill.victim == death.killer
                        and teammates(kill.killer, death.victim)):
                    traded.add((number, death.victim))
                    trade_kills.add(kill)
                    break
    for number, victim in traded:
        tally[victim].traded_deaths += 1
    for kill in trade_kills:
        tally[kill.killer].trade_kills += 1

    for kill in match.kills:
        if kill.victim in tally:
            tally[kill.victim].deaths += 1
        if enemies(kill.killer, kill.victim):
            tally[kill.killer].kills += 1
            tally[kill.killer].headshot_kills += kill.headshot
        if enemies(kill.assister, kill.victim):
            tally[kill.assister].assists += 1
            tally[kill.assister].flash_assists += kill.flash_assist

    for number in numbers:
        enemy_kills = [k for k in kills_in.get(number, []) if enemies(k.killer, k.victim)]
        if enemy_kills:
            first = enemy_kills[0]
            tally[first.killer].opening_kills += 1
            tally[first.victim].opening_deaths += 1
        for killer, count in Counter(k.killer for k in enemy_kills).items():
            if count in MULTI_KILL_SIZES:
                tally[killer].multi_kill_rounds[count] += 1

    for r in rounds:
        ks = kills_in.get(r.number, [])
        for p in match.players:
            sid = p.steam_id
            got_kill = any(k.killer == sid and enemies(k.killer, k.victim) for k in ks)
            got_assist = any(k.assister == sid and enemies(sid, k.victim) for k in ks)
            died = any(k.victim == sid for k in ks)
            if got_kill or got_assist or not died or (r.number, sid) in traded:
                tally[sid].kast_rounds += 1

    for dmg in match.damages:
        if enemies(dmg.attacker, dmg.victim):
            tally[dmg.attacker].damage += dmg.health
            if dmg.weapon in UTILITY_WEAPONS:
                tally[dmg.attacker].utility_damage += dmg.health

    for flash in match.flashes:
        if enemies(flash.flasher, flash.flashed):
            tally[flash.flasher].enemies_flashed += 1
            tally[flash.flasher].blind_seconds += flash.seconds
        elif teammates(flash.flasher, flash.flashed):
            tally[flash.flasher].team_flashes += 1

    for clutch in match.clutches:             # csda also lists 1v1s, and the other side's last player in one
        if clutch.player in tally and clutch.opponents >= CLUTCH_MIN_OPPONENTS:
            tally[clutch.player].clutch_attempts += 1
            tally[clutch.player].clutch_wins += clutch.won

    for bomb in match.bombs:
        if bomb.player in tally and bomb.event == "planted":
            tally[bomb.player].bomb_plants += 1
        if bomb.player in tally and bomb.event == "defused":
            tally[bomb.player].bomb_defuses += 1

    played = len(rounds)
    players = {}
    for p in match.players:
        t = tally[p.steam_id]
        opening = t.opening_kills + t.opening_deaths
        players[p.steam_id] = {
            "name": p.name,
            "team": p.team,
            "rounds": played,
            "kills": t.kills,
            "deaths": t.deaths,
            "assists": t.assists,
            "headshot_kills": t.headshot_kills,
            "hs_pct": _pct(t.headshot_kills, t.kills),
            "adr": round(t.damage / played, 1) if played else 0.0,
            "damage": t.damage,
            "utility_damage": t.utility_damage,
            "kast_pct": _pct(t.kast_rounds, played),
            "opening_kills": t.opening_kills,
            "opening_deaths": t.opening_deaths,
            "opening_win_pct": _pct(t.opening_kills, opening),
            "trade_kills": t.trade_kills,
            "traded_deaths": t.traded_deaths,
            "enemies_flashed": t.enemies_flashed,
            "enemy_blind_avg_s": round(t.blind_seconds / t.enemies_flashed, 2) if t.enemies_flashed else None,
            "flash_assists": t.flash_assists,
            "team_flashes": t.team_flashes,
            "clutch_attempts": t.clutch_attempts,
            "clutch_wins": t.clutch_wins,
            "multi_kill_rounds": {str(size): t.multi_kill_rounds[size] for size in MULTI_KILL_SIZES},
            "bomb_plants": t.bomb_plants,
            "bomb_defuses": t.bomb_defuses,
        }

    timeline = []
    for r in rounds:
        events: list[dict[str, Any]] = []
        for k in kills_in.get(r.number, []):
            events.append({
                "type": "kill", "tick": k.tick, "t": _seconds(k.tick - r.start_tick, match.tickrate),
                "killer": k.killer or None, "victim": k.victim, "weapon": k.weapon, "headshot": k.headshot,
                "assister": k.assister or None, "flash_assist": k.flash_assist, "trade": k in trade_kills,
            })
        for b in bombs_in.get(r.number, []):
            events.append({
                "type": f"bomb_{b.event}", "tick": b.tick, "t": _seconds(b.tick - r.start_tick, match.tickrate),
                "player": b.player or None, "site": b.site,
            })
        events.sort(key=lambda e: e["tick"])
        timeline.append({
            "number": r.number, "winner": r.winner, "end_reason": r.end_reason,
            "start_tick": r.start_tick, "end_tick": r.end_tick, "events": events,
        })

    return {"version": VERSION, "checksum": match.checksum, "tickrate": match.tickrate,
            "rounds_played": played, "players": players, "rounds": timeline}


def attach_faceit(stats: dict[str, Any], lines: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """`stats` with each player's FACEIT line under `faceit`. `lines` maps a SteamID64 to FACEIT's name for the player
    and the `player_stats` strings FACEIT gives. A player in `lines` but not in the match is left out."""
    out = dict(stats)
    out["players"] = {sid: dict(player) for sid, player in stats["players"].items()}
    for sid, line in lines.items():
        if sid in out["players"]:
            raw = line.get("stats", {})
            out["players"][sid]["faceit"] = {"nickname": line.get("nickname", ""), **{
                key: _number(raw.get(name)) for key, name in FACEIT_FIELDS.items()}}
    return out


def _number(text: object) -> float | None:
    try:
        return float(str(text))
    except ValueError:
        return None


FACEIT_MULTI_KILLS = {"2": "double_kills", "3": "triple_kills", "4": "quadro_kills", "5": "penta_kills"}


def headline(player: Mapping[str, Any]) -> dict[str, Any]:
    """The numbers the scorecard and the table lead with: FACEIT's when the match has FACEIT's line, else the demo's.
    `multi_kills` counts the rounds with 2, 3, 4 and 5 Frags, keyed by that number as text."""
    faceit = player.get("faceit")
    if faceit:
        return {"source": "faceit", "kills": faceit["kills"], "deaths": faceit["deaths"],
                "assists": faceit["assists"], "adr": faceit["adr"], "hs_pct": faceit["hs_pct"], "kd": faceit["kd"],
                "multi_kills": {size: faceit[name] for size, name in FACEIT_MULTI_KILLS.items()}}
    kd = round(player["kills"] / player["deaths"], 2) if player["deaths"] else None
    return {"source": "demo", "kills": player["kills"], "deaths": player["deaths"], "assists": player["assists"],
            "adr": player["adr"], "hs_pct": player["hs_pct"], "kd": kd,
            "multi_kills": dict(player["multi_kill_rounds"])}


COMPARED = ("kills", "deaths", "assists", "adr", "hs_pct")


def comparison(now: Mapping[str, Any], earlier: list[Mapping[str, Any]]) -> dict[str, Any]:
    """The subject's headline numbers this match against their average over `earlier` (their other matches, newest
    first). Each metric is None when there is no value to compare with."""
    averages: dict[str, float | None] = {}
    deltas: dict[str, float | None] = {}
    for metric in COMPARED:
        values = [past[metric] for past in earlier if past.get(metric) is not None]
        averages[metric] = round(sum(values) / len(values), 2) if values else None
        current = now.get(metric)
        deltas[metric] = round(current - averages[metric], 2) if current is not None and averages[metric] is not None \
            else None
    return {"count": len(earlier), "averages": averages, "deltas": deltas}
