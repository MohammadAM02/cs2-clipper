"""The sign-in task that starts the app (spec: Running it). Registering it is a persistent change to
the user's system: run `clipper install` only with the user's approval; `clipper uninstall` reverses it."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

TASK_NAME = "cs2-clipper"


def _ps_quote(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def register_script(repo_root: Path, pythonw: Path) -> str:
    return "\n".join([
        "$ErrorActionPreference = 'Stop'",
        f"$action = New-ScheduledTaskAction -Execute {_ps_quote(pythonw)} -Argument '-m clipper run'"
        f" -WorkingDirectory {_ps_quote(repo_root)}",
        "$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME",
        "$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries"
        " -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)"
        " -MultipleInstances IgnoreNew",
        "$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited",
        f"Register-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -Action $action -Trigger $trigger"
        " -Settings $settings -Principal $principal -Force | Out-Null",
    ])


def unregister_script() -> str:
    return f"Unregister-ScheduledTask -TaskName {_ps_quote(TASK_NAME)} -Confirm:$false"


def _powershell(script: str) -> None:
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=True,
    )


def install(repo_root: Path) -> None:
    """Start `pythonw -m clipper run` at every sign-in, from this environment's Python."""
    _powershell(register_script(repo_root, Path(sys.executable).with_name("pythonw.exe")))


def uninstall() -> None:
    _powershell(unregister_script())
