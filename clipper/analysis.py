"""What happened in each Demo, as cs-demo-analyzer (csda) reads it (ADR 0004). The only code that reads csda's
output: if csda renames a field, this file is what changes.

csda writes everything it finds in a Demo to one JSON file of a few MB. The app keeps a copy of the parts it
reads, about a tenth of that, as ``<checksum>.json`` in its own folder, so each Demo is analyzed once: scoring
and every render read that copy. The functions below read it the way the app read CS Demo Manager's database,
which filled its tables from the same csda output."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from clipper import paths, winjob
from clipper.hlae_plan import Kill, Player, RenderInputs, Round
from clipper.model import MatchInfo, RoundFacts

ANALYZE_TIMEOUT_SECONDS = 600      # csda reads a Demo in seconds; it never launches CS2, so stopping it orphans nothing
KEPT = ("checksum", "date", "mapName", "tickrate", "tickCount", "demoFileName", "source", "type", "serverName",
        "buildNumber", "networkProtocol", "maxRounds", "overtimeCount", "duration", "winner", "teamA", "teamB",
        "players", "rounds", "kills", "clutches")
_CHECKSUM = re.compile(r"[0-9a-f]{1,16}")

Data = Mapping[str, Any]


class MissingAnalysis(LookupError):
    """The app has no analysis of this match."""


def is_checksum(text: str) -> bool:
    """Whether `text` is a match checksum as csda writes it: hex, without its leading zeros."""
    return _CHECKSUM.fullmatch(text) is not None


def trim(data: Data) -> dict[str, Any]:
    """The part of csda's output the app keeps: the match, its teams and players, its rounds, kills and clutches."""
    return {key: data[key] for key in KEPT if key in data}


def _steam_id(value: object) -> str:
    """A Steam ID as the app writes it. csda writes 0 for nobody, such as the world as a killer."""
    return str(value) if value else ""


def _team_of(data: Data, steamid: str) -> str | None:
    player = data["players"].get(steamid) if steamid else None
    return None if player is None else player["team"]["name"]


def match_info(data: Data, steamid: str) -> MatchInfo | None:
    """Map, date and final score from the subject's side, or None if the subject is not in the match. The date is
    in this PC's time zone, to the second, as the index has kept every date."""
    team = _team_of(data, steamid)
    teams = (data["teamA"], data["teamB"])
    us = next((t for t in teams if t["name"] == team), None)
    them = next((t for t in teams if t["name"] != team), None)
    if us is None or them is None:
        return None
    return MatchInfo(
        checksum=data["checksum"],
        map_name=data["mapName"],
        played_at=datetime.fromisoformat(data["date"]).astimezone().replace(microsecond=0),
        team_score=us["score"],
        opponent_score=them["score"],
    )


def round_facts(data: Data, steamid: str) -> list[RoundFacts]:
    """What the subject did in each round where they got at least one Frag: every kill credited to them, without
    team kills and suicides (same side), in a round csda has a row for."""
    team = _team_of(data, steamid)
    if team is None:
        return []
    rounds = {r["number"]: r for r in data["rounds"]}
    frags: dict[int, list[Data]] = defaultdict(list)
    for kill in data["kills"]:
        if (_steam_id(kill["killerSteamId"]) == steamid and kill["killerSide"] != kill["victimSide"]
                and kill["roundNumber"] in rounds):
            frags[kill["roundNumber"]].append(kill)
    facts = []
    for number in sorted(frags):
        kills, row = frags[number], rounds[number]
        facts.append(RoundFacts(
            round=number,
            frag_ticks=tuple(sorted(k["tick"] for k in kills)),
            headshots=sum(1 for k in kills if k["isHeadshot"]),
            blind_frags=sum(1 for k in kills if k["is_killer_blinded"]),
            knife_frags=sum(1 for k in kills if k["weaponType"] == "melee"),
            noscope_frags=sum(1 for k in kills if k["isNoScope"]),
            smoke_frags=sum(1 for k in kills if k["isThroughSmoke"]),
            bot_frags=sum(1 for k in kills if k["isKillerControllingBot"]),
            round_won=row["winnerName"] == team,
            round_start_tick=row["startTick"],
            round_end_tick=row["endTick"],
        ))
    return facts


def render_inputs(data: Data) -> RenderInputs:
    """What the HLAE plan builds its Sequences and cfg files from. A player's slot is the number `spec_player`
    takes, which counts from 1 where csda's userId counts from 0."""
    return RenderInputs(
        tickrate=float(data["tickrate"]),
        tick_count=data["tickCount"],
        kills=tuple(Kill(tick=k["tick"], round_number=k["roundNumber"], killer_steam_id=_steam_id(k["killerSteamId"]),
                         victim_steam_id=_steam_id(k["victimSteamId"]))
                    for k in sorted(data["kills"], key=lambda k: k["tick"])),
        rounds=tuple(Round(number=r["number"], end_tick=r["endTick"], freeze_time_end_tick=r["freezeTimeEndTick"])
                     for r in sorted(data["rounds"], key=lambda r: r["number"])),
        players=tuple(Player(steam_id=_steam_id(p["steamId"]), name=p["name"], slot=p["userId"] + 1)
                      for p in sorted(data["players"].values(), key=lambda p: p["name"])),
    )


def run_logged(command: list[str], log_path: Path) -> int:
    """Run `command`, keep what it printed in `log_path`, and give its exit code. Runs inside the app's
    kill-on-close job object, and is killed on a timeout or any other failure, as subprocess.run would."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
        creationflags=subprocess.CREATE_NO_WINDOW,
    ) as proc:
        winjob.guard(proc)
        try:
            stdout, stderr = proc.communicate(timeout=ANALYZE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as exc:
            proc.kill()
            exc.stdout, exc.stderr = proc.communicate()   # reap it and collect what it had written
            log_path.write_text((exc.stdout or "") + (exc.stderr or ""), encoding="utf-8")
            raise
        except Exception:  # noqa: BLE001 - mirrors subprocess.run: kill on any failure, not only a timeout
            proc.kill()
            raise
    log_path.write_text(stdout + stderr, encoding="utf-8")
    return proc.returncode


class Analyses:
    """The analyses the app keeps, one per match, in `folder`; csda at `csda_exe` makes them."""

    def __init__(self, folder: Path, csda_exe: Path, *, run: Callable[[list[str], Path], int] = run_logged):
        self.folder = folder
        self.csda_exe = csda_exe
        self._run = run

    def _path(self, checksum: str) -> Path:
        if not is_checksum(checksum):
            raise ValueError(f"not a checksum: {checksum!r}")
        return self.folder / f"{checksum}.json"

    def analyze(self, dem: Path, log_path: Path) -> str:
        """Analyze the Demo at `dem`, keep the analysis, and give its match checksum. What csda printed goes to
        `log_path`. Its exit code is not trusted: what counts is whether it wrote the analysis."""
        self.folder.mkdir(parents=True, exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix="csda-", dir=self.folder))
        try:
            code = self._run([str(self.csda_exe), "-demo-path", str(dem), "-output", str(output),
                              "-format", "json", "-minify", "-source", "faceit"], log_path)
            written = output / f"{dem.stem}.json"
            if not written.is_file():
                raise RuntimeError(f"csda wrote no analysis (exit code {code}): see {log_path.name}")
            data = json.loads(written.read_text(encoding="utf-8"))
            checksum = str(data.get("checksum", ""))
            if not is_checksum(checksum):
                raise RuntimeError(f"csda gave {checksum[:40]!r}, which is not a checksum")
            paths.atomic_write_text(self._path(checksum), json.dumps(trim(data), separators=(",", ":")))
            return checksum
        finally:
            shutil.rmtree(output, ignore_errors=True)

    def _load(self, checksum: str) -> dict[str, Any]:
        try:
            return json.loads(self._path(checksum).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise MissingAnalysis(f"no analysis of match {checksum}") from None

    def has(self, checksum: str) -> bool:
        return self._path(checksum).is_file()

    def forget(self, checksum: str) -> None:
        self._path(checksum).unlink(missing_ok=True)

    def match_info(self, checksum: str, steamid: str) -> MatchInfo | None:
        return match_info(self._load(checksum), steamid)

    def round_facts(self, checksum: str, steamid: str) -> list[RoundFacts]:
        return round_facts(self._load(checksum), steamid)

    def render_inputs(self, checksum: str) -> RenderInputs | None:
        """None when the app has no analysis of the match."""
        try:
            return render_inputs(self._load(checksum))
        except MissingAnalysis:
            return None
