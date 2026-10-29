"""What the Stats page shows for one match (spec: Stats): the stored stats with each player's headline numbers, the
round timeline with each kill's place in the Reels, and the subject's numbers against their last matches with stats.
Reads only: nothing here runs csda or FACEIT, so it is safe to ask for at any time."""

from __future__ import annotations

from typing import Any

from pathlib import Path

from clipper import radar, stats
from clipper.analysis import Analyses
from clipper.index import Index

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
    players = [{**player, "steam_id": steam, "you": steam == subject, "headline": stats.headline(player)}
               for steam, player in worked["players"].items()]
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
        earlier.append(stats.headline(worked["players"][subject]))
        if len(earlier) == COMPARE_WITH:
            break
    return stats.comparison(stats.headline(you), earlier)
