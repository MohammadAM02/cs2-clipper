"""Writes packaging\\shipped.env: the FACEIT Data API key a packaged build ships to every install.

The key is the *app's*, not the user's -- one key reads any player's public matches -- so an install
needs it before it can look a nickname up. FACEIT's own key type for this is the client-side one.

Where the key comes from, in order: the CS2CLIPPER_FACEIT_KEY environment variable (how the release
workflow passes its repository secret), else this machine's own install of the app, so a build taken
on a working machine ships the key that machine already uses.

packaging\\shipped.env is never committed (.gitignore) and never printed. The app protects it for the
Windows account that first runs it, so the copy on disk is not the key itself.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipper import paths, protect                       # noqa: E402
from clipper.settings import STORED_KEY                  # noqa: E402

OUT = Path(__file__).with_name("shipped.env")


def shipped_key() -> str:
    """The key to ship, or "" when this machine has none to give."""
    from_environment = os.environ.get("CS2CLIPPER_FACEIT_KEY", "").strip()
    if from_environment:
        return from_environment
    try:
        stored = json.loads((paths.data_dir() / "settings.json").read_text(encoding="utf-8"))
        return protect.unprotect(stored[STORED_KEY]).strip()
    except (OSError, KeyError, ValueError, protect.ProtectError):
        return ""


def main() -> int:
    key = shipped_key()
    if key:
        OUT.write_text(f"FACEIT_API_KEY={key}\n", encoding="utf-8")
        print(f"shipped.env: a FACEIT API key of {len(key)} characters will be bundled")
    else:
        OUT.unlink(missing_ok=True)                      # so a stale key cannot ride along
        print("shipped.env: no FACEIT API key to ship, so the installer will carry none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
