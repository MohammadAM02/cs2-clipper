# Ingest FACEIT demos only

**Status**: accepted

Every Demo this tool processes comes from a FACEIT match. There is no Valve Matchmaking path, no
Steam login, no Game Coordinator integration, and no share-code support. Adding any of those would
mean holding Steam credentials or driving a second acquisition channel for a different class of
match, and the user only plays FACEIT.

## Consequences

- Acquisition is a **manual** matchroom download for the foreseeable future. FACEIT moved Demo
  download behind a partner-only, server-side API: an unauthenticated probe of
  `POST /download/v2/demos/download` returns `403 err_f0 no valid scope provided`.
  `scripts/faceit_probe.py` tests whether a personal key changes that.
- FACEIT's CDN serves Demos as **`.zst` (Zstandard) archives** and download links expire roughly
  **30 days** after the match, so the pipeline must decompress and should process a Demo promptly.
- The API key authenticates the Data API as **`Authorization: Bearer <key>`** — a raw key is rejected
  with `403 err_f0`, while `Bearer` returns `200`. The Download API returns
  `403 no valid scope provided` either way: it is partner-only.
- No Steam dependency means no risk of the tool touching a Steam account or a VAC-secured session.
- If acquisition is ever automated, it will be by driving FACEIT's own web matchroom, not by a
  public API — and that decision will need its own ADR.

## Update 2026-09-22 — manual confirmed

- **Decision: acquisition stays manual for v1.** The post-drop pipeline is where the value is; the
  manual step costs ~30 seconds per match.
- Valve matches stay out of scope while scope is FACEIT-only. Revisit if scope ever widens.
