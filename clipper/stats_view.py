"""What the Stats page shows for one match (spec: Stats): the stored stats with each player's headline numbers, the
round timeline with each kill's place in the Reels, the subject's Mistakes, and their numbers against their last
matches with stats. And what Progress and Heatmaps show over every match (spec: Progress): the subject's form, maps,
Mistakes and records, and where their Frags and deaths happened on a map.
Reads only: nothing here runs csda or FACEIT, so it is safe to ask for at any time."""

from __future__ import annotations

from typing import Any

from pathlib import Path

from clipper import mistakes, progress, radar, stats
from clipper.analysis import Analyses
from clipper.backfill import radar_requested
from clipper.index import Index
from clipper.model import DEATH_KEYS

COMPARE_WITH = 20      # the subject's most recent other matches with stats that the match is compared with


def replay_view(index: Index, analyses: Analyses, subject: str, radar_dir: Path, checksum: str,
                requested: bool, failure: str | None = None) -> dict[str, Any] | None:
    """The replay page's data for match `checksum`, or None when the app has no such match. `state` is "ready" once
    the positions are worked out, "failed" when the last try failed (`failure` says why), "requested" while they wait
    for CS2 and FACEIT AC to close, else "not_requested". The radar is None when CS2 has not been read yet; the samples
    are only sent when the replay is ready."""
    row = index.match(checksum)
    if row is None:
        return None
    worked = analyses.stats(checksum)
    positions = analyses.positions_of(checksum)
    if worked is None:
        state = "not_computed"
    elif positions is not None:
        state = "ready"
    elif failure is not None:
        state = "failed"
    else:
        state = "requested" if requested else "not_requested"
    try:
        picture = radar.ready(row["map"], radar_dir)
    except radar.RadarError:
        picture = None
    body: dict[str, Any] = {"state": state, "requested": requested, "failure": failure, "checksum": checksum,
                            "map": row["map"], "subject": subject, "radar": picture}
    if worked is not None:
        body["tickrate"] = worked["tickrate"]
        body["players"] = [{**player, "steam_id": steam, "you": steam == subject}
                           for steam, player in worked["players"].items()]
        body["rounds"] = worked["rounds"]
    if state == "ready":
        body["fields"] = positions["fields"]
        body["samples"] = positions["samples"]
    return body


def match_view(index: Index, analyses: Analyses, subject: str, checksum: str) -> dict[str, Any] | None:
    """The page's data for match `checksum`, or None when the app has no such match. `state` is "not_computed" while
    the match's stats are still to be worked out, and "ready" once they are."""
    row = index.match(checksum)
    if row is None:
        return None
    head = {"checksum": checksum, "map": row["map"], "played_at": row["played_at"], "team_score": row["team_score"],
            "opponent_score": row["opponent_score"], "result": row["result"], "subject": subject}
    worked = analyses.stats(checksum)
    if worked is None:
        return {**head, "state": "not_computed"}
    tickrate = worked["tickrate"]
    players = [{**player, "steam_id": steam, "you": steam == subject, "headline": stats.headline(player),
                "official": stats.official(player)} for steam, player in worked["players"].items()]
    rounds = []
    for round_ in worked["rounds"]:
        events = []
        for event in round_["events"]:
            if event["type"] == "kill":
                event = {**event, "clips": index.clip_moments(checksum, round_["number"], event["tick"], tickrate)}
            events.append(event)
        rounds.append({**round_, "events": events})
    return {**head, "state": "ready", "tickrate": tickrate,
            "faceit": any("faceit" in player for player in worked["players"].values()),
            "players": players, "rounds": rounds,
            "mistakes": _mistakes(index, analyses, subject, checksum, worked),
            "comparison": _comparison(index, analyses, subject, checksum, worked["players"].get(subject))}


def _comparison(index: Index, analyses: Analyses, subject: str, checksum: str,
                you: dict[str, Any] | None) -> dict[str, Any] | None:
    """The subject's headline numbers in this match against their last COMPARE_WITH other matches with stats."""
    if you is None:
        return None
    earlier = []
    for past in index.other_matches(checksum):
        worked = analyses.stats(past["checksum"])
        if worked is None or subject not in worked["players"]:
            continue
        earlier.append(stats.official(worked["players"][subject]))
        if len(earlier) == COMPARE_WITH:
            break
    return stats.comparison(stats.official(you), earlier)


def _mistakes(index: Index, analyses: Analyses, subject: str, checksum: str,
              worked: dict[str, Any]) -> dict[str, Any] | None:
    """The subject's Mistakes in the match, each with its name, what happened in words, what to try next time, and
    its moment in the Reels when a Reel has it (a death's own Reel, with Clip your deaths, or its round's). None when
    the subject is not in the match or the app has no analysis of it."""
    if subject not in worked["players"]:
        return None
    kept = analyses.review(checksum, subject)
    if kept is None:
        return None
    names = {steam: player["name"] for steam, player in worked["players"].items()}
    listed = []
    for mistake in kept["mistakes"]:
        kind = mistakes.KINDS[mistake["type"]]
        tip = (mistakes.BLIND_BY_TEAM_TIP if mistake["type"] == mistakes.DIED_BLIND and mistake.get("team_flash")
               else kind["tip"])
        clips = [*index.clip_moments(checksum, mistake["round"] + DEATH_KEYS, mistake["tick"], worked["tickrate"]),
                 *index.clip_moments(checksum, mistake["round"], mistake["tick"], worked["tickrate"])]
        listed.append({**mistake, "title": kind["title"], "tip": tip, "detail": _said(mistake, names, subject),
                       "clips": clips})
    return {"list": listed, "by_type": mistakes.tally(kept["mistakes"]), "flashes_kept": kept["flashes_kept"]}


def _said(mistake: dict[str, Any], names: dict[str, str], subject: str) -> str:
    """What happened, in a sentence said to the player."""
    who = names.get(mistake.get("other", ""), "someone")
    weapon = mistake.get("weapon") or "a weapon"
    kind = mistake["type"]
    if kind == mistakes.OPENING_DEATH:
        return f"{who} got you with the {weapon} before anyone else fell, and nobody traded it."
    if kind == mistakes.DIED_BLIND:
        if mistake.get("team_flash"):
            whose = "your own" if mistake.get("other") == subject else f"{who}'s"
            return f"You were still blind from {whose} flash when you died."
        return f"{who} got you with the {weapon} while you were flashed."
    if kind == mistakes.THREW_ADVANTAGE:
        return f"A {mistake['ours']}v{mistake['theirs']} in your favour: {who} got you with the {weapon}, untraded."
    if kind == mistakes.TEAM_FLASH:
        more = mistake["blinded"] - 1
        also = f", and {more} more of your team with them" if more > 0 else ""
        return f"Your flash blinded {who} for {mistake['blind_s']} s{also}."
    if kind == mistakes.TEAM_KILL:
        return f"You killed {who} with the {weapon}."
    if kind == mistakes.BOMB_DEATH:
        return "The bomb went off with you still in range."
    if mistake.get("weapon") and mistake["weapon"] != "World":
        return f"Your own {mistake['weapon']} killed you."
    return "You died to the world: a fall, most likely."


def _played(index: Index, analyses: Analyses, subject: str) -> tuple[list[progress.Played], int]:
    """Every match the subject has stats in, newest first, and how many matches were left out for having none."""
    played, left_out = [], 0
    for row in index.all_matches():
        kept = analyses.review(row["checksum"], subject) if subject else None
        if kept is None or kept["line"] is None:
            left_out += 1
            continue
        played.append(progress.Played(
            checksum=row["checksum"], map=row["map"], played_at=row["played_at"], result=row["result"],
            team_score=row["team_score"], opponent_score=row["opponent_score"], line=kept["line"],
            mistakes=kept["mistakes"], flashes_kept=kept["flashes_kept"]))
    return played, left_out


def progress_view(index: Index, analyses: Analyses, subject: str, window: int) -> dict[str, Any]:
    """The Progress page's data: `progress.summary` over the matches the subject has stats in. `state` is "empty"
    when there are none yet. `left_out` counts the matches the app knows that have no stats to go by."""
    played, left_out = _played(index, analyses, subject)
    return {"state": "ready" if played else "empty", "subject": subject, "left_out": left_out,
            **progress.summary(played, window)}


def heatmap_view(index: Index, analyses: Analyses, subject: str, radar_dir: Path, *, map_name: str | None = None,
                 checksum: str | None = None, last: int = 0) -> dict[str, Any]:
    """The Heatmaps page's data: the maps the subject has matches on, and for one of them (`map_name`, else the map of
    match `checksum`, else the most played) where each of their Frags and deaths happened, over that one match or the
    `last` most recent matches on the map (0: all of them). Each place says which match it is from by its number in
    `matches`. The radar is None until CS2's picture of the map has been read; `radar_requested` says it is asked
    for."""
    rows = [row for row in index.all_matches() if analyses.has(row["checksum"])]
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["map"]] = counts.get(row["map"], 0) + 1
    maps = [{"map": name, "matches": count} for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    only = next((row for row in rows if row["checksum"] == checksum), None) if checksum else None
    chosen = only["map"] if only is not None else map_name if map_name in counts else maps[0]["map"] if maps else None
    body: dict[str, Any] = {"state": "ready" if chosen else "empty", "subject": subject, "maps": maps, "map": chosen,
                            "match": only["checksum"] if only is not None else None, "last": last,
                            "radar": None, "radar_requested": False, "matches": [], "places": []}
    if chosen is None:
        return body
    try:
        body["radar"] = radar.ready(chosen, radar_dir)
    except radar.RadarError:
        pass
    body["radar_requested"] = body["radar"] is None and radar_requested(index, chosen)
    on_map = [only] if only is not None else [row for row in rows if row["map"] == chosen]
    if only is None and last > 0:
        on_map = on_map[:last]
    for number, row in enumerate(on_map):
        kept = analyses.review(row["checksum"], subject) if subject else None
        body["matches"].append({"checksum": row["checksum"], "played_at": row["played_at"], "result": row["result"],
                                "team_score": row["team_score"], "opponent_score": row["opponent_score"]})
        body["places"].extend({**place, "m": number} for place in (kept["places"] if kept else ()))
    return body
