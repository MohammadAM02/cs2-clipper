#!/usr/bin/env python3
"""One-shot FACEIT capability probe. Read-only, makes no changes. Never prints the API key.

Answers, in order:
  1. Does the key authenticate against the Data API at all?
  2. Can it resolve your player_id and SteamID64 (needed to pick out your Frags)?
  3. Does it return your recent CS2 match history?
  4. Do those matches expose `demo_url` / `instances[].demos` resource URLs?
  5. Can the Download API turn such a resource URL into a signed download URL?
     This is the question that decides whether FACEIT Demo acquisition can be automated.

    python scripts/faceit_probe.py                    # reads .env
    python scripts/faceit_probe.py --nickname NAME
    python scripts/faceit_probe.py --match-id 1-xxxx  # probe one specific match
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_API = "https://open.faceit.com/data/v4"
DOWNLOAD_API = "https://open.faceit.com/download/v2/demos/download"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_AUTH = {"scheme": "raw"}


def auth_header(key: str, scheme: str | None = None) -> str:
    """FACEIT documents server-side keys both raw and as Bearer; support both."""
    return key if (scheme or _AUTH["scheme"]) == "raw" else f"Bearer {key}"


def call(
    method: str, url: str, key: str, body: dict | None = None, scheme: str | None = None
) -> tuple[int | None, str]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": auth_header(key, scheme),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "cs2-clipper-probe",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")
    except Exception as error:  # noqa: BLE001 - diagnostic tool
        return None, f"{type(error).__name__}: {error}"


def redact(url: str) -> str:
    """Keep the shape of a URL, drop any query string, which may be signed."""
    match = re.match(r"^(https?://[^/]+)(/[^?]*)(\?.*)?$", url or "")
    if not match:
        return "(none)"
    host, path, query = match.groups()
    return host + path + ("?<redacted>" if query else "")


def head(text: str, limit: int = 400) -> str:
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def main(argv: list[str]) -> int:
    load_env(ROOT / ".env")

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--nickname", default=os.environ.get("FACEIT_NICKNAME") or None)
    parser.add_argument("--player-id", default=None)
    parser.add_argument("--match-id", default=None, help="probe this match instead of taking the newest one")
    parser.add_argument("--history-limit", type=int, default=5)
    parser.add_argument(
        "--auth-scheme",
        choices=("auto", "raw", "bearer"),
        default="auto",
        help="Authorization header style; 'auto' tries raw then Bearer (default: auto)",
    )
    args = parser.parse_args(argv)

    key = os.environ.get("FACEIT_API_KEY", "").strip()
    if not key:
        print("No FACEIT_API_KEY found in .env — add it there (not in chat, not in source).")
        print(f"Expected file: {ROOT / '.env'}")
        return 2
    print(f"key loaded: {len(key)} chars (not printed)")

    player_id = args.player_id

    # 1 + 2 — auth, identity, SteamID64
    if not player_id:
        if not args.nickname:
            print("\n[1] SKIPPED — pass --nickname or set FACEIT_NICKNAME in .env")
            return 2
        candidates = ["raw", "bearer"] if args.auth_scheme == "auto" else [args.auth_scheme]
        url = f"{DATA_API}/players?nickname={urllib.parse.quote(args.nickname)}"
        status, body = None, ""
        for candidate in candidates:
            status, body = call("GET", url, key, scheme=candidate)
            print(f"\n[1] GET /players?nickname={args.nickname} -> HTTP {status}  (auth scheme: {candidate})")
            if status == 200:
                _AUTH["scheme"] = candidate
                print(f"    working auth scheme: {candidate}")
                break
        if status != 200:
            print("    body:", head(body))
            print("\nVERDICT: the key cannot read the Data API with either auth scheme.")
            print("         A 'Server side' key should work here — check it was copied whole.")
            return 1
        player = json.loads(body)
        player_id = player.get("player_id")
        game = (player.get("games") or {}).get("cs2") or {}
        steamid = game.get("game_player_id")
        print(f"    player_id : {player_id}")
        print(f"    nickname  : {player.get('nickname')}")
        print(f"    steamid64 : {steamid}   <- used to select your Frags")
        print(f"    region    : {game.get('region')}  elo: {game.get('faceit_elo')}")

    # 3 — match history
    status, body = call(
        "GET",
        f"{DATA_API}/players/{player_id}/history?game=cs2&offset=0&limit={args.history_limit}",
        key,
    )
    matches: list[dict] = []
    print(f"\n[3] GET /players/{player_id}/history?game=cs2 -> HTTP {status}")
    if status == 200:
        matches = json.loads(body).get("items", [])
        print(f"    matches returned: {len(matches)}")
        for match in matches[:5]:
            print(f"      {match.get('match_id')}  finished={match.get('finished_at')}  {match.get('competition_name') or ''}")
    else:
        print("    body:", head(body))

    # 4 — demo resource URLs on the newest match
    target = args.match_id or (matches[0]["match_id"] if matches else None)
    resource_urls: list[str] = []
    if target:
        status, body = call("GET", f"{DATA_API}/matches/{target}", key)
        print(f"\n[4] GET /matches/{target} -> HTTP {status}")
        if status == 200:
            match = json.loads(body)
            top_level = match.get("demo_url") or []
            nested = [d for instance in (match.get("instances") or []) for d in (instance.get("demos") or [])]
            resource_urls = [*top_level, *nested]
            print(f"    demo_url entries      : {len(top_level)}")
            print(f"    instances[].demos     : {len(nested)}")
            for url in resource_urls[:4]:
                print(f"      {redact(url)}")
            if not resource_urls:
                print("    (no demo resource URL exposed for this match)")
        else:
            print("    body:", head(body))
    else:
        print("\n[4] SKIPPED — no match id available")

    # 5 — the decisive question: can a signed download URL be obtained?
    print("\n[5] POST /download/v2/demos/download")
    if not resource_urls:
        print("    SKIPPED — need a demo resource URL to try")
        print("\nVERDICT: Data API works; demo download unproven.")
        return 0
    status, body = call("POST", DOWNLOAD_API, key, {"resource_url": resource_urls[0]})
    print(f"    -> HTTP {status}")
    print("    body:", head(body, 300))
    if status == 200:
        print("\nVERDICT: this key CAN obtain signed demo download URLs — acquisition is automatable.")
    else:
        print("\nVERDICT: this key cannot download demos. Expect a manual matchroom download to be required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
