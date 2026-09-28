"""How to run CS:DM's command line: the executable, and the environment phase 1 proved it needs."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from clipper import winjob

ANALYZE_TIMEOUT_SECONDS = 1800   # analysis never launches CS2, so stopping it orphans nothing


@dataclass(frozen=True)
class CsdmCli:
    prefix: tuple[str, ...]   # (cs-demo-manager.exe, …/app.asar/cli.js) — or a fake in tests
    home: Path                # becomes USERPROFILE, so CS:DM's app folder is <home>/.csdm
    pg_bin: Path              # CS:DM shells out to psql

    def command(self, *args: str) -> list[str]:
        return [*self.prefix, *args]

    def env(self) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if key.upper() != "PGPASSWORD"}
        env["ELECTRON_RUN_AS_NODE"] = "1"
        env["USERPROFILE"] = str(self.home)
        env["PATH"] = f"{self.pg_bin}{os.pathsep}{env.get('PATH', '')}"
        return env

    def analyze(self, dem: Path, log_path: Path) -> str:
        """Run `csdm analyze <dem> --source faceit` and keep its output in log_path. The exit code
        is not trusted: the caller checks that the match reached CS:DM's database. Runs inside the
        app's kill-on-close job object like every csdm call (spec: Closing and quitting).

        Mirrors subprocess.run's own cleanup exactly: a Popen context manager, and the process
        killed not only on a timeout but on any other exception communicate() raises."""
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with subprocess.Popen(
            self.command("analyze", str(dem), "--source", "faceit"),
            env=self.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW,
        ) as proc:
            winjob.guard(proc)
            try:
                stdout, stderr = proc.communicate(timeout=ANALYZE_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired as exc:
                proc.kill()
                exc.stdout, exc.stderr = proc.communicate()   # reap it and collect what it had written
                raise
            except Exception:  # noqa: BLE001 - mirrors subprocess.run: kill on any failure, not only a timeout
                proc.kill()
                raise
        output = stdout + stderr
        log_path.write_text(output, encoding="utf-8")
        return output
