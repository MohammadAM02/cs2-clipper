"""What is running on this PC, and stopping it. The only code that touches other processes."""

from __future__ import annotations

from typing import Protocol

import psutil

HOOKED_FLAG = "-insecure"   # only renders start CS2 with it (CS:DM passes it through HLAE)


class ProcessProbe(Protocol):
    def names(self) -> set[str]: ...
    def running(self, name: str) -> bool: ...
    def service_running(self, name: str) -> bool: ...
    def hooked_cs2_running(self) -> bool: ...
    def kill_hooked_cs2(self) -> None: ...
    def children_named(self, pid: int, name: str) -> set[tuple[int, float]]: ...
    def kill_processes(self, processes: set[tuple[int, float]]) -> None: ...


class SystemProbe:
    """ProcessProbe backed by psutil. Process names are compared in lower case."""

    def names(self) -> set[str]:
        return {(p.info["name"] or "").lower() for p in psutil.process_iter(["name"])}

    def running(self, name: str) -> bool:
        return name.lower() in self.names()

    def service_running(self, name: str) -> bool:
        """True unless the service is stopped; a service that does not exist is not running."""
        try:
            return psutil.win_service_get(name).status() != "stopped"
        except psutil.NoSuchProcess:
            return False

    def _hooked_cs2(self) -> list[psutil.Process]:
        found = []
        for process in psutil.process_iter(["name"]):
            if (process.info["name"] or "").lower() != "cs2.exe":
                continue
            try:
                if any(arg.lower() == HOOKED_FLAG for arg in process.cmdline()):
                    found.append(process)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return found

    def hooked_cs2_running(self) -> bool:
        return bool(self._hooked_cs2())

    def kill_hooked_cs2(self) -> None:
        for process in self._hooked_cs2():
            try:
                process.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

    def children_named(self, pid: int, name: str) -> set[tuple[int, float]]:
        """(pid, create_time) of every descendant of `pid` called `name`."""
        try:
            children = psutil.Process(pid).children(recursive=True)
        except psutil.NoSuchProcess:
            return set()
        found = set()
        for child in children:
            try:
                if child.name().lower() == name.lower():
                    found.add((child.pid, child.create_time()))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return found

    def kill_processes(self, processes: set[tuple[int, float]]) -> None:
        """Kill each (pid, create_time) that is still the same process — PIDs get reused."""
        for pid, created in processes:
            try:
                process = psutil.Process(pid)
                if process.create_time() == created:
                    process.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
