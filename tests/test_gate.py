from datetime import datetime
from pathlib import Path

import pytest

from clipper.gate import (
    CS2_REASON, FACEIT_REASON, GB, IDLE_REASON, NIGHT_REASON, STEAM_REASON, Gate, GateStatus, Schedule,
)
from clipper.procs import SystemProbe


class FakeProbe:
    def __init__(self, names=(), services=()):
        self._names = {name.lower() for name in names}
        self._services = set(services)

    def names(self):
        return set(self._names)

    def running(self, name):
        return name.lower() in self._names

    def service_running(self, name):
        return name in self._services

    def hooked_cs2_running(self):
        return False

    def kill_hooked_cs2(self):
        pass

    def children_named(self, pid, name):
        return set()

    def kill_processes(self, processes):
        pass


def gate(names=(), services=(), free=100 * GB, steam=True, schedule=Schedule()):
    """A Gate on E:, with Steam running unless `steam` is False."""
    names = (*names, "steam.exe") if steam else names
    return Gate(FakeProbe(names, services), Path("E:/cs2clips"), 5, free_bytes=lambda _: free, schedule=schedule)


def test_clear_when_nothing_is_in_the_way():
    assert gate().check() == GateStatus(ok=True)


def test_a_running_cs2_blocks():
    assert gate(["cs2.exe"]).check().reasons == (CS2_REASON,)


def test_the_faceit_service_blocks():
    assert gate(services=["FACEITService"]).check().reasons == (FACEIT_REASON,)


def test_a_faceit_client_process_blocks():
    assert gate(["FACEIT.exe"]).check().reasons == (FACEIT_REASON,)


def test_low_disk_space_blocks():
    status = gate(free=4 * GB).check()
    assert not status.ok
    assert status.reasons[0].startswith("less than 5 GB free")


def test_a_missing_drive_blocks():
    def gone(_):
        raise FileNotFoundError("E:\\")

    status = Gate(FakeProbe(["steam.exe"]), Path("E:/cs2clips"), 5, free_bytes=gone).check()
    assert status.reasons == ("E:\\ is not available",)


def test_every_reason_is_reported_in_order():
    status = gate(["cs2.exe"], ["FACEITService"], free=4 * GB).check()
    assert status.reasons == (CS2_REASON, FACEIT_REASON, "less than 5 GB free on E:\\")


def test_steam_not_running_blocks_since_cs2_cannot_start_without_it():
    assert gate(steam=False).check().reasons == (STEAM_REASON,)


def test_cs2_running_on_its_own():
    assert gate(["cs2.exe"]).cs2_running()
    assert not gate().cs2_running()


@pytest.mark.parametrize("idle, reasons", [(599.0, (IDLE_REASON,)), (600.0, ())])
def test_render_when_idle_waits_for_ten_minutes_without_input(idle, reasons):
    assert gate(schedule=Schedule("idle", idle_seconds=lambda: idle)).check().reasons == reasons


@pytest.mark.parametrize("hour, reasons", [(0, (NIGHT_REASON,)), (1, ()), (6, ()), (7, (NIGHT_REASON,)),
                                           (22, (NIGHT_REASON,))])
def test_render_overnight_waits_for_one_to_seven_am(hour, reasons):
    schedule = Schedule("overnight", local_now=lambda: datetime(2026, 10, 13, hour, 30))
    assert gate(schedule=schedule).check().reasons == reasons


def test_render_anytime_never_waits_for_the_clock():
    schedule = Schedule("anytime", idle_seconds=lambda: 0.0, local_now=lambda: datetime(2026, 10, 13, 15))
    assert gate(schedule=schedule).check().ok


def test_faceit_running_on_its_own():
    assert gate(services=["FACEITService"]).faceit_running()
    assert not gate().faceit_running()


def test_the_system_probe_sees_this_test_process():
    probe = SystemProbe()
    assert "python.exe" in probe.names()
    assert probe.service_running("NoSuchServiceForClipperTests") is False
