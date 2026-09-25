# Spec: Clip library (draft — paused)

Status: design paused after section 1 of 5. The orchestrator
(`.scratch/v1-pipeline/issues/04-orchestrator.md`) is built first; the library's remaining sections
are designed once real automatic output exists.

## Problem

An Allstar.gg-style place to watch the Clips the pipeline makes, from the phone, anywhere.

## Decided (2026-09-22)

- **v1 scope: Clip library only.** A feed of each match's automatically made Clips, with Highlight
  Type badges (ACE, 4K, Clutch…) and Score; play with a Player / Enemy POV toggle; download. No
  pipeline control, Reel editor or share-ready exports in v1.
- **CS:DM's GUI is not the library.** It stays an optional power tool for manual work: it reads the
  same database, and `cs-demo-manager.exe <demo path>` opens a Demo in it. It must be launched with
  `USERPROFILE=<repo>/home` and kept closed while the pipeline renders (see the comment on issue 04).
- **Access: phone, anywhere, while the PC is on**, through a private Tailscale tunnel; nothing is
  exposed to the internet. Tailscale is not installed yet; it needs the app on the PC and the phone,
  and the user signs in.
- **Language: Python** for the orchestrator and exporter. The page is plain HTML/CSS/JS with no build
  step.
- **Approach A: static library.**
  - The orchestrator's SQLite index (`data/clipper.sqlite`) is the single source of truth. The
    library never reads CS:DM's database.
  - An exporter writes `library.json` (atomically), thumbnails and the page files into the shared
    folder `E:\cs2clips\library\` after each finished Render Job. Re-running it gives the same
    result. Nothing outside `library\` is ever shared (see `.scratch/orchestrator/spec.md`).
  - `tailscale serve` shares that folder. The page only reads.
  - A Python web app later (for pipeline control or a Reel editor) would reuse the same index.
- **Update 2026-09-25: something of ours now listens on the network.** The match-alerts worker
  serves its Demos to grab page to the PC, the LAN and Tailscale (`.scratch/match-alerts/spec.md`).
  The user dropped this spec's old "nothing of ours listens on the network" rule to allow it.
  Whether the library reuses that server instead of `tailscale serve` is decided when this design
  resumes.
- **Build order: orchestrator first**, then the library on top of its real output.
- **One card plays one video.** The orchestrator joins each Highlight's Clips into one Reel per
  Perspective (`.scratch/orchestrator/spec.md`, Joining), so a card plays one file and downloads
  one file.

Rejected: forking CS:DM (a large Electron app to carry through every CS2 update, desktop-only);
a Python web app now (more to build and supervise than a read-only v1 needs); Jellyfin (a generic
video library, no Score-first feed or POV toggle).

## What the library needs from the index

- **Match:** checksum, map, date, final score, the subject's result (win/loss).
- **Highlight:** match, round, Highlight Type, Score, reasons, start and end Tick.
- **Clip:** Highlight, Perspective (`player` / `enemy`), Sequence number, start and end Tick, file
  path, duration, render status.
- **Reel:** Highlight, Perspective, file path, duration — what a card plays.

The orchestrator's index (`.scratch/orchestrator/spec.md`, Index) carries all of these.

## Still to design

- Screens (feed, match, player), sorting and filters.
- Sharing details: whether `tailscale serve` can share a folder on Windows (fallback: a small Python
  file server with HTTP Range support, which iPhones need for video), the home-screen icon, and
  whether the phone plays CS:DM's H.264 + MP3 files.
- Error handling and tests.
