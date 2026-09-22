# 05 — FACEIT Demo acquisition

Status: resolved
Type: research

## Question

Can a personal FACEIT API key obtain a demo download URL, and if not, what is the acquisition loop?

## Answer — tested 2026-09-22 with the real key

`python scripts/faceit_probe.py`, app `baggaclipper`, account `cheesebagga`:

| Step | Result |
| --- | --- |
| Data API auth | ✅ **`Authorization: Bearer <key>`** → 200. Raw key → `403 err_f0`. The Bearer form is the one that works. |
| Identity | player_id `79e2de86-3b50-48de-b0b0-437c5472e717`, region EU, elo 1098 |
| **SteamID64** | **`76561198192858303`** — this is how we select the subject player's Frags |
| Match history | ✅ 5 recent CS2 matches (Middle East 5v5 Queue, Europe 5v5 Queue) |
| `demo_url` / `instances[].demos` | ✅ present, e.g. `https://demos-europe-central.backblaze.faceit-cdn.net/cs2/<match>-1-1.dem.zst` |
| `POST /download/v2/demos/download` | ❌ **403 `{"code":"err_f0","message":"no valid scope provided"}`** |

**Conclusion: the API can tell us everything *about* a match but cannot hand us the file.** The demo
download is partner-only, exactly as FACEIT's restriction and CS:DM's disabled feature implied.

Two corrections to earlier assumptions:

- API key auth is **Bearer**, not raw.
- FACEIT serves Demos as **`.zst`**, not `.gz`. (The Demo the user dropped was already decompressed —
  magic bytes `PBDEMS2`, 220,878,511 bytes.)

## Consequence

Acquisition is either a **manual matchroom download**, or **browser-driven** automation of the
logged-in matchroom. Everything after the file lands is fully automatable, including knowing *when* a
new FACEIT match with a Demo exists (that is pure Data API).
