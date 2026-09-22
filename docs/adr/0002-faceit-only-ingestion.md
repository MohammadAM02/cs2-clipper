# Ingest FACEIT demos only

**Status**: accepted

Every Demo this tool processes comes from a FACEIT match. There is no Valve Matchmaking path, no
Steam login, no Game Coordinator integration, and no share-code support. Adding any of those would
mean holding Steam credentials or driving a second acquisition channel for a different class of
match, and the user only plays FACEIT.

## Consequences

- Acquisition is a **manual** matchroom download for the foreseeable future. FACEIT moved Demo
  download behind a partner-only, server-side API; an unauthenticated probe of
  `POST /download/v2/demos/download` returns `403 err_f0 no valid scope provided`, and CS Demo
  Manager — which holds a partner key — disabled the feature because it requires a hosted backend.
  `scripts/faceit_probe.py` tests whether a personal key changes that.
- FACEIT's CDN serves Demos as **`.zst` (Zstandard) archives** and download links expire roughly
  **30 days** after the match, so the pipeline must decompress and should process a Demo promptly.
- The API key authenticates the Data API as **`Authorization: Bearer <key>`** — a raw key is rejected
  with `403 err_f0`, while `Bearer` returns `200`. The Download API returns
  `403 no valid scope provided` either way: it is partner-only.
- CS:DM itself needs a **PostgreSQL 17+** server, so "no database" was never on the table.
- No Steam dependency means no risk of the tool touching a Steam account or a VAC-secured session.
- If acquisition is ever automated, it will be by driving FACEIT's own web matchroom, not by a
  public API — and that decision will need its own ADR.

## Update 2026-09-22 — manual confirmed, and CS:DM cannot acquire FACEIT either

- **Decision: acquisition stays manual for v1.** The post-drop pipeline is where the value is; the
  manual step costs ~30 seconds per match.
- `csdm dl-faceit` is **hard-disabled** in the shipped CLI — the command and even `--help` print the
  "currently disabled" notice and exit. CS:DM cannot acquire FACEIT Demos for us.
- `csdm dl-valve` **does work** (tested: it retrieved the account's recent MM matches and began
  downloading a Demo into the game's `csgo/replays` folder). This is the only fully automatic
  acquisition path in the ecosystem, but it covers Valve matches only, so it remains out of scope
  while scope is FACEIT-only. Revisit if scope ever widens.
- CS:DM's auto-download flags (`download{Valve,Faceit,5EPlay,Renown}Demos{AtStartup,InBackground}`)
  are set to `false` in the managed settings so nothing is fetched unasked.
