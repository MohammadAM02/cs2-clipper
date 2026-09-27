from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from clipper.alerts import MatchAlerts
from clipper.faceit import AuthError, FaceitError, MatchDetails, Player, PlayerNotFound
from clipper.index import Index
from clipper.model import FaceitStats
from clipper.protect import ProtectError

SUBJECT = "76561198192858303"
PAGE = "http://127.0.0.1:8765/demos"
START = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)
SHOW = (("Show matches", PAGE), ("Not now", None))
THREE_K = FaceitStats(map_name="de_inferno", team_score=13, opponent_score=9, won=True, rounds=22, kills=24,
                      deaths=15, assists=5, adr=94.0, double_kills=3, triple_kills=2, quadro_kills=1)
ANCIENT_3K = FaceitStats(map_name="de_ancient", team_score=13, opponent_score=11, won=True, rounds=24,
                         kills=21, deaths=15, assists=6, adr=88.0, triple_kills=2)
NOTHING = FaceitStats(map_name="de_nuke", team_score=10, opponent_score=13, rounds=23, kills=12, deaths=18,
                      assists=3, adr=64.0, double_kills=2)


def mid(n: int) -> str:
    return f"1-00000000-0000-0000-0000-{n:012d}"


class FakeFaceit:
    def __init__(self):
        self.steamid = SUBJECT
        self.finished: dict[str, datetime] = {}
        self.stats_of: dict[str, FaceitStats | None] = {}
        self.listed: dict[str, bool] = {}
        self.error: Exception | None = None
        self.calls: list[str] = []

    def add(self, n, finished_at, stats, listed=True):
        self.finished[mid(n)], self.stats_of[mid(n)], self.listed[mid(n)] = finished_at, stats, listed

    def player(self, nickname):
        self.calls.append("player")
        if self.error:
            raise self.error
        return Player("p-1", nickname, self.steamid)

    def finished_since(self, player_id, since):
        self.calls.append("history")
        if self.error:
            raise self.error
        return [(m, t) for m, t in self.finished.items() if t >= since]

    def stats(self, match_id, player_id):
        self.calls.append(f"stats {match_id}")
        return self.stats_of.get(match_id)

    def details(self, match_id):
        self.calls.append(f"details {match_id}")
        return MatchDetails(self.listed.get(match_id, True), f"https://www.faceit.com/en/cs2/room/{match_id}")


@dataclass
class World:
    index: Index
    faceit: FakeFaceit
    notices: list = field(default_factory=list)
    playing: bool = False
    now: datetime = START
    alerts: MatchAlerts | None = None

    def tick(self, **later):
        self.now += timedelta(**later)
        self.alerts.tick()

    def state(self, n):
        return self.index.faceit_match(mid(n))["state"]

    def titles(self):
        return [title for title, _, _ in self.notices]


@pytest.fixture
def world(tmp_path):
    index = Index(tmp_path / "clipper.sqlite")
    w = World(index, FakeFaceit())
    w.alerts = MatchAlerts(
        index, w.faceit, lambda title, body, actions=(): w.notices.append((title, body, actions)),
        lambda: w.playing, nickname="someone", subject_steamid=SUBJECT, page_url=PAGE,
        stopped_playing_minutes=5.0, clock=lambda: w.now)
    yield w
    index.close()


def test_the_start_up_check_announces_the_matches_with_highlights(world):
    world.faceit.add(1, START - timedelta(hours=2), THREE_K)
    world.faceit.add(2, START - timedelta(hours=1), NOTHING)
    world.tick()
    assert world.notices == [("1 new match has Highlights", "Inferno 4K + 2× 3K", SHOW)]
    assert (world.state(1), world.state(2)) == ("announced", "no_highlights")
    assert f"details {mid(2)}" not in world.faceit.calls        # no need to ask about its Demo
    assert world.index.get_flag("alerts_status") == "on"


def test_nothing_is_asked_while_cs2_runs(world):
    world.playing = True
    world.faceit.add(1, START - timedelta(hours=1), THREE_K)
    world.tick()
    world.tick(minutes=30)
    assert world.faceit.calls == []
    assert world.notices == []


def test_the_match_alert_comes_5_minutes_after_cs2_closes(world):
    world.tick()                                                  # start-up check: nothing yet
    world.playing = True
    world.tick(minutes=1)
    world.faceit.add(1, START + timedelta(minutes=40), THREE_K)
    world.playing = False
    world.tick(minutes=45)                                        # CS2 closed at 20:46
    world.tick(minutes=4)
    assert world.notices == []
    world.tick(minutes=1)
    assert world.titles() == ["1 new match has Highlights"]


def test_a_match_whose_stats_are_not_out_yet_holds_the_alert(world):
    world.faceit.add(1, START - timedelta(minutes=10), None)
    world.faceit.add(2, START - timedelta(minutes=50), ANCIENT_3K)
    world.tick()
    assert world.notices == [] and world.state(1) == "waiting"
    world.faceit.stats_of[mid(1)] = THREE_K
    world.tick(minutes=3)
    assert world.notices == [("2 new matches have Highlights", "Inferno 4K + 2× 3K · Ancient 2× 3K", SHOW)]


def test_the_alert_waits_30_minutes_at_most(world):
    world.faceit.add(1, START - timedelta(minutes=10), None)     # FACEIT never publishes its stats
    world.faceit.add(2, START - timedelta(minutes=50), ANCIENT_3K)
    world.tick()
    world.tick(minutes=27)
    assert world.notices == []
    world.tick(minutes=3)
    assert world.notices == [("1 new match has Highlights", "Ancient 2× 3K", SHOW)]
    assert world.state(1) == "waiting"


def test_a_demo_that_is_not_listed_yet_waits_like_missing_stats(world):
    world.faceit.add(1, START - timedelta(minutes=10), THREE_K, listed=False)
    world.tick()
    assert world.state(1) == "waiting" and world.notices == []
    world.faceit.listed[mid(1)] = True
    world.tick(minutes=3)
    assert world.state(1) == "announced"


def test_one_reminder_in_the_last_3_days_and_never_while_cs2_runs(world):
    world.index.save_faceit_match(mid(1), START - timedelta(days=27, hours=1), "ready", THREE_K)
    world.index.announce([mid(1)], START - timedelta(days=27))
    world.index.save_faceit_match(mid(2), START - timedelta(days=27, hours=1), "ready", ANCIENT_3K)
    world.index.skip_match(mid(2), START - timedelta(days=27))
    world.playing = True
    world.tick()
    assert world.notices == []
    world.playing = False
    world.tick(minutes=1)
    assert world.notices == [("Inferno demo expires in 3 days", "4K + 2× 3K · you haven't grabbed or skipped it",
                              (("Show matches", PAGE), ("Dismiss", None)))]
    world.tick(minutes=1)
    assert len(world.notices) == 1


def test_a_match_announced_in_its_last_3_days_gets_no_second_reminder(world):
    world.faceit.add(1, START - timedelta(days=29), THREE_K)     # the first check looks back 30 days
    world.faceit.add(2, START - timedelta(days=31), ANCIENT_3K)  # its link has expired
    world.tick()
    world.tick(minutes=1)
    assert world.titles() == ["1 new match has Highlights"]
    assert world.index.faceit_match(mid(1))["reminded_at"] is not None
    assert world.index.faceit_match(mid(2)) is None


def test_a_downloaded_demo_grabs_its_match(world):
    world.faceit.add(1, START - timedelta(hours=1), THREE_K)
    world.tick()
    name = f"{mid(1)}-1-1.dem.zst"
    demo_id = world.index.add_demo(name, "a" * 64, Path("E:/cs2clips/demos") / name)
    world.tick(minutes=1)
    assert world.state(1) == "grabbed"
    assert world.index.faceit_match(mid(1))["demo_id"] == demo_id


def test_a_demo_already_in_the_index_is_never_announced(world):
    name = f"{mid(1)}-1-1.dem.zst"
    world.index.add_demo(name, "a" * 64, Path("E:/cs2clips/demos") / name)
    world.faceit.add(1, START - timedelta(days=3), THREE_K)
    world.tick()
    assert world.notices == []
    assert world.state(1) == "grabbed"


def test_the_wrong_faceit_account_turns_alerts_off(world):
    world.faceit.steamid = "76561198000000009"
    world.tick()
    world.tick(minutes=10)
    assert world.faceit.calls == ["player"]
    assert world.index.get_flag("alerts_status") == (
        "off: FACEIT nickname someone plays as SteamID 76561198000000009, not your SteamID 76561198192858303")


def test_a_rejected_key_turns_alerts_off(world):
    world.faceit.error = AuthError("HTTP 401 from FACEIT", 401)
    world.tick()
    world.faceit.error = None
    world.tick(minutes=10)
    assert world.faceit.calls == ["player"]
    assert world.index.get_flag("alerts_status") == "off: FACEIT rejected the key (HTTP 401 from FACEIT)"


def test_an_unknown_faceit_nickname_turns_alerts_off(world):
    world.faceit.error = PlayerNotFound("FACEIT has no player called someone", 404)
    world.tick()
    world.tick(minutes=10)
    assert world.faceit.calls == ["player"]
    assert world.index.get_flag("alerts_status") == (
        "off: FACEIT has no player called someone; check the FACEIT nickname in Settings")


def test_an_unreadable_saved_key_turns_alerts_off(world):
    world.faceit.error = ProtectError("cannot be decrypted")
    world.tick()
    world.faceit.error = None
    world.tick(minutes=10)
    assert world.faceit.calls == ["player"]
    assert world.index.get_flag("alerts_status") == (
        "off: the saved FACEIT key can't be read on this Windows account; enter it again in Settings")


def test_faceit_trouble_is_tried_again_5_minutes_later(world):
    world.faceit.error = FaceitError("HTTP 503 from FACEIT", 503)
    world.faceit.add(1, START - timedelta(hours=1), THREE_K)
    world.tick()
    world.faceit.error = None
    world.tick(minutes=4)
    assert world.notices == [] and world.faceit.calls == ["player"]
    world.tick(minutes=1)
    assert world.titles() == ["1 new match has Highlights"]


def test_matches_whose_link_expired_drop_out(world):
    world.index.save_faceit_match(mid(1), START - timedelta(days=31), "ready", THREE_K)
    world.index.announce([mid(1)], START - timedelta(days=31))
    world.tick()
    assert world.state(1) == "expired"
    assert world.notices == []
