from pathlib import Path

from clipper.gate import CS2_REASON, FACEIT_REASON, GB, GUI_REASON, Gate, GateStatus
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


def gate(names=(), services=(), free=100 * GB):
    return Gate(FakeProbe(names, services), Path("E:/cs2clips"), 5, free_bytes=lambda _: free)


def test_clear_when_nothing_is_in_the_way():
    assert gate().check() == GateStatus(ok=True)


def test_a_running_cs2_blocks():
    assert gate(["cs2.exe"]).check().reasons == (CS2_REASON,)


def test_the_faceit_service_blocks():
    assert gate(services=["FACEITService"]).check().reasons == (FACEIT_REASON,)


def test_a_faceit_client_process_blocks():
    assert gate(["FACEIT.exe"]).check().reasons == (FACEIT_REASON,)


def test_the_csdm_gui_blocks():
    assert gate(["cs-demo-manager.exe"]).check().reasons == (GUI_REASON,)


def test_low_disk_space_blocks():
    status = gate(free=4 * GB).check()
    assert not status.ok
    assert status.reasons[0].startswith("less than 5 GB free")


def test_a_missing_drive_blocks():
    def gone(_):
        raise FileNotFoundError("E:\\")

    status = Gate(FakeProbe(), Path("E:/cs2clips"), 5, free_bytes=gone).check()
    assert status.reasons == ("E:\\ is not available",)


def test_every_reason_is_reported_in_order():
    status = gate(["cs2.exe", "cs-demo-manager.exe"], ["FACEITService"]).check()
    assert status.reasons == (CS2_REASON, FACEIT_REASON, GUI_REASON)


def test_faceit_running_on_its_own():
    assert gate(services=["FACEITService"]).faceit_running()
    assert not gate().faceit_running()


def test_the_system_probe_sees_this_test_process():
    probe = SystemProbe()
    assert "python.exe" in probe.names()
    assert probe.service_running("NoSuchServiceForClipperTests") is False
