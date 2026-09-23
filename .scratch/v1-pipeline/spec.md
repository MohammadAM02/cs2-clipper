# Spec: v1 pipeline

## Problem

Turning CS2 Demos into shareable Clips requires driving the game engine; everything around that is
commodity work. The product is not a clipper — it is a **hands-off** clipper: a new Demo appears,
Highlights are found and scored automatically, Clips arrive ready, with no per-match configuration.

## Settled decisions

- **Scope is FACEIT-only.** No Valve Matchmaking, no Game Coordinator, no Steam integration at all.
  Every Demo comes from a FACEIT match. See `docs/adr/0002-faceit-only-ingestion.md`.
- **Render engine**: CS Demo Manager (`csdm` CLI), not from-scratch HLAE. See
  `docs/adr/0001-adopt-cs-demo-manager-as-render-engine.md`.
- **Subject POV *and* Enemy POV**, paired per Highlight: the same moment rendered once from the
  subject's camera and once from the victim's. 1080p60 H.264 for v1. No cinematic multi-camera.
  CS:DM supports this natively via `--perspective player|enemy`.
- **Stack**: Fastify + SQLite + in-process job queue. Postgres/Redis only if concurrency demands it.
- **Platform**: Windows renders; the phone/other devices consume output files.
- **Safety**: CS2 is only ever launched hooked (HLAE) for offline Demo playback, always `-insecure`.
  Never join a VAC-secured server with a hooked process.

## Demo acquisition (the one manual step)

FACEIT exposes `demo_url` / `instances[].demos` resource URLs through its Data API, but downloading
them requires a **partner-only, server-side** service. CS Demo Manager was granted a partner key and
*still* disabled the feature because it needs a hosted backend, and an unauthenticated probe of
`POST /download/v2/demos/download` returns `403 err_f0 no valid scope provided`.

`scripts/faceit_probe.py` settles empirically whether a personal key can do it. Expected answer: no.

Consequences:

- v1 acquisition = **manual matchroom download** ("Watch Demo"), which yields a **`.gz` archive**;
  the pipeline must gunzip before analysis.
- FACEIT Demo downloads expire roughly **30 days** after the match, so a dropped Demo should be
  processed promptly rather than archived for later.

## What already exists vs. what we build

| Capability | Owner |
| --- | --- |
| Demo analysis → events in a database (`csdm analyze --source faceit`) | CS:DM |
| Render: launch CS2 via HLAE, seek Ticks, record, FFmpeg encode | CS:DM (`csdm video`) |
| Render queue | CS:DM (shared with its GUI) |
| Clip boundary definition (Sequences, or `--mode player` events) | CS:DM |
| **Highlight detection beyond kills/deaths/rounds** | **us** |
| **Scoring and selection (Ace, clutch, collateral, match point)** | **us** |
| **Hands-off orchestration: Demo arrives → analyze → score → render → publish** | **us** |
| **Opinionated presets so nothing needs configuring** | **us** |

## Pipeline

```
FACEIT matchroom download → <name>.dem.gz in the watched folder
  → gunzip → <name>.dem
  → csdm analyze <demo> --source faceit     (CS:DM persists events + metadata)
  → detection: Highlights from CS:DM's tables (kills/clutches/rounds)  ← us, SQL
  → scoring: Score + reasons per Highlight           ← us
  → select top N → Sequences (start tick, end tick)      ← us
  → csdm video --perspective player  ─┐                  ← CS:DM renders both cameras
  → csdm video --perspective enemy   ─┘
  → Clips on disk + index in SQLite                      ← us
```

## Phases

1. **Verify CS:DM end-to-end on one real FACEIT Demo** (gunzip → analyze → video → watchable Clip).
   Measure wall clock and file size. Retires the remaining integration risk: HLAE path configuration,
   app-folder/database location, `--configFile` shape, and whether `--source faceit` analysis behaves.
2. **Detection prototype**: `demoparser2` → `[{type, tick}]` for the subject SteamID.
3. **Scoring engine**: rules producing `{score, reasons[]}`, using the glossary's terms.
4. **Orchestrator**: watched folder → gunzip → analyze → detect → score → render → index.
5. Presets/publish polish.

## Render preset (derived from the phase-1 runs, not guessed)

```
csdm video <demo> --mode player \
  --steamids <subject-steamid64> \
  --event kills --rounds <selected rounds> \
  --perspective player|enemy \
  --output <out-dir> --close-game-after-recording

# then assemble the Reel ourselves (CS:DM's own concatenation flags are unverified):
ffmpeg -y -f concat -safe 0 -i <list.txt> -c copy <reel>.mp4
```

- **Both perspectives per Highlight**, as two passes: `player`, then `enemy`. Verified that
  `--perspective enemy` switches the camera to each victim's slot at the right Ticks.
- **Concatenate with FFmpeg, not CS:DM.** CS:DM's `--concatenate-sequences` / `--output-file-name`
  are unverified (no longer suspected of breaking runs — that was a path bug — but untested).
- **Padding is still CS:DM's default 2s/2s**, which yields ~4-second Clips. `--start-seconds-before` /
  `--end-seconds-after` are the knobs; they need a clean test.
- **The `--output` folder must already exist** or CS:DM aborts with `Output folder does not exist`.
- **Every path passed to CS:DM or FFmpeg must be a NATIVE path** (`C:/...`, `E:/...`). An MSYS path
  (`/c/...`, `/tmp/...`) breaks CS:DM's database lookup and FFmpeg's input handling in confusing ways.
- **Aspect ratio is a resolution choice, not a pipeline change.** CS:DM forwards `--width`/`--height`
  to CS2 as `-width`/`-height`. `scripts/render_reel.sh` exposes it as `REEL_RATIO`:
  `16:9` = 1920×1080, `4:3` = 1440×1080 (a genuinely 4:3 video), `4:3-stretched` = render 1440×1080
  then bake `scale=1920:1080` into the Reel. Only the stretched variant costs a re-encode; the other
  two are stream copies. `REEL_EVENT` picks the Sequence unit: `kills` (per-frag) or `rounds` (whole
  rounds, ~70–95 s each).
- Every headless invocation needs `USERPROFILE=<native repo>/home` and `psql` on `PATH`.
- Never wrap `csdm` in a timeout: killing the CLI orphans CS2 in a reconnect loop.

## Out of scope

- Valve Matchmaking, Steam login, Game Coordinator, share codes.
- Live gameplay capture (Demos only).
- Multi-user, cloud rendering, accounts.
- Competing with CS:DM's GUI feature set.

## Acceptance for phase 1

See `spike/ACCEPTANCE.md` — the criteria are unchanged; the executor changes from
`spike/launch_spike.cmd` to `csdm video`.
