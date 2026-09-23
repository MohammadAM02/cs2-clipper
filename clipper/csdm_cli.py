"""How to run CS:DM's command line: the executable, and the environment phase 1 proved it needs."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

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
        is not trusted: the caller checks that the match reached CS:DM's database."""
        log_path.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            self.command("analyze", str(dem), "--source", "faceit"),
            env=self.env(), capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=ANALYZE_TIMEOUT_SECONDS, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        output = proc.stdout + proc.stderr
        log_path.write_text(output, encoding="utf-8")
        return output
