# Phase 1 acceptance record — CS:DM render path

Executor: `csdm video` (v3.20.1), not `spike/launch_spike.cmd`. The criteria below are unchanged from
the original spike; only the thing performing the render changed.

## Environment (all verified working)

| Component | State |
| --- | --- |
| CS2 | `G:\SteamLibrary\...\game\bin\win64\cs2.exe` |
| CS:DM 3.20.1 | `%LOCALAPPDATA%\Programs\cs-demo-manager` (headless CLI verified) |
| PostgreSQL 17.6 | portable cluster at `%LOCALAPPDATA%\pg17`, port 5432, database `csdm` |
| HLAE 2.192.2 | `C:\HLAE` (wired into CS:DM settings, custom location) |
| FFmpeg 8.1.2 | system build (wired into CS:DM settings) |
| CS:DM app folder | `home/.csdm/`, selected via a `USERPROFILE` override (see note below) |
| Subject | SteamID64 `76561198192858303`, FACEIT `cheesebagga` |
| Demo | FACEIT match `1-2b882547-…`, 220,878,511 bytes, 18 rounds, 34 subject Frags |

## Run log

| Run | Date | Command | Clips | Wall clock | Output | Result |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 2026-09-22 | `video <demo> 75879 78745` (explicit ticks) | 0 | 8+ min, killed | none | ❌ no frames written |
| 2 | 2026-09-22 | `video <demo> --mode player --steamids … --event kills --rounds 12 --perspective player` | 3 | ~83 s | 16.8 MB | ✅ |

## Criteria

| # | Criterion | Verdict |
| --- | --- | --- |
| A1 | CS2 launches with no UI interaction | ✅ `Starting Counter-Strike...` → game up, unattended |
| A2 | Demo plays back | ✅ three sequences recorded |
| A3 | One video file per Clip | ✅ `sequence-N-tick-A-to-B.mp4` |
| A4 | Playable and correctly framed | ✅ h264, 1920×1080, 60 fps, mp3 stereo |
| A5 | Boundaries accurate to ±0.5 s | ✅ 3.945/7.628/3.997 s vs 4.0/7.6/4.0 s expected |
| A6 | Survives loss of focus | ✅ ran unattended, window not foregrounded |
| A7 | Completion marker / clean exit | ✅ CLI exit 0, `Video generated in E:/cs2clips` |
| A8 | Repeatable | ⏳ second run pending (enemy-POV pass) |

## Measurements

| Metric | Value |
| --- | --- |
| Output duration | 15.57 s across 3 Clips |
| Wall clock | ~83 s |
| Ratio (wall ÷ output) | **≈5.3×** — but boot dominates at this size; see caveat |
| Throughput estimate | ≈60 s boot + ~1.2 s wall per second of footage |
| MB per 10 s of 1080p60 output | ≈11 MB (CRF 23, x264) |

Extrapolation for a full match (10 Clips × 20 s = 200 s of footage): ≈5 minutes wall clock. **Based
on one data point — treat as an estimate, not a model.**

## Findings

1. **Output layout.** Clips land as `<output>/sequence-N-tick-A-to-B.mp4`, after per-sequence working
   folders `<output>/N-sequence/` (containing `video.mp4` and `take0000/audio.wav`) are consolidated.
2. **Adjacent Frags merge.** Round 12 contained 4 subject Frags; CS:DM produced 3 Sequences, one of
   which spans two kills (ticks 76071→76559 covers kills at 76199 and 76431). The scoring layer must
   expect one Clip to cover more than one Highlight.
3. **Default padding is tight.** Sequences ran 4.0–7.6 s. `--start-seconds-before` /
   `--end-seconds-after` control this and are required for a usable Clip length.
4. **The plugin contract is inspectable.** CS:DM writes its action file to `<demo path>.json` beside
   the Demo, and the CS2 server plugin logs to `<game>/bin/win64/csdm.log`. Both are useful for
   debugging a stalled render.
5. **Unresolved: the seek appears to be racy.** Runs 1 and 3 wrote no frames for 5–8 minutes while
   `ffmpeg` never spawned, yet the CS2 process burned ~2 CPU cores the whole time (so it was
   rendering, not hung) and the CLI reported "Recording in progress...". Run 2, structurally
   identical, produced clips in 83 s.

   Leading hypothesis: the `demo_gototick` scheduled at tick 96 is a **race**. If the Demo's tick 96
   passes before the plugin's scheduler is live, the jump is missed and playback continues from tick
   0 in real time — so the first recording tick (~74000) is reached only after
   `74000 / 64 ≈ 19.3 minutes`. That predicts output at ~20 minutes, not never. Prediction recorded
   rather than assumed; if it holds, the mitigation is to emit **redundant seeks** at several early
   ticks (e.g. 96, 512, 1024) so one fires even when the scheduler starts late.

   File ordering alone is **not** the cause: the working run 2 has the same pattern of a tick-96
   `demo_gototick` filed after later-tick actions.

   **Second observation supporting the race (14:28:50 enemy pass):** correct native paths, native Demo
   path, no padding or concatenation flags — the same shape that rendered in 110 s at 14:50 — produced
   no frames and no `ffmpeg` process for **13.3 minutes** until an unrelated command killed it. So the
   stall is intermittent and independent of the flag set, which is exactly what a missed seek looks
   like. The orchestrator must therefore **detect a stall** (no `ffmpeg` process N seconds in) and
   retry, rather than trusting elapsed time.
6. **HLAE path needs pinning per version.** CS2 updates break injection until advancedfx ships a fix.
7. **Every CS2-touching operation must be serialised.** `csdm dl-valve` (and any acquisition command)
   launches CS2 in the background to talk to the Game Coordinator. Running it while a render was in
   progress destroyed that render — the CLI logged `Game error` and the game process was replaced.
   The orchestrator needs one global lock around *all* operations that start CS2, not just renders.
   `scripts/render_reel.sh` enforces this on the render side; nothing enforced it on mine.
8. **Never wrap `csdm` in a timeout.** Killing the CLI orphans CS2: the bundled server plugin then
   loops on `Failed to connect to WebSocket server. Retrying in 2s...` forever, holding the game open.
   Observed live. Recovery is `taskkill /IM cs2.exe /F`. Timeouts belong around individual *checks*,
   never around the render command.
10. **Root cause of every "password authentication failed" was a PATH FORM, never credentials.**
    `psql` authenticated fine with the same password throughout. Two distinct symptoms, one cause:
    - **`USERPROFILE` must be a native path** (`C:/Users/...`). CS:DM derives its app folder from
      `homedir()`, which on Windows is the raw `USERPROFILE` value; `/c/Users/...` resolves to
      `C:\c\Users\...`, the settings file is never found, and CS:DM silently falls back to its default
      database password. This made the whole `render_reel.sh` script fail while identical manual
      commands succeeded — the script computed `REPO` with `pwd` (MSYS form).
    - **Any file handed to a native binary must be native too.** FFmpeg could not read a concat list
      created by `mktemp` (`/tmp/...`): `Error opening input file`.
    An earlier version of this file blamed CS:DM's `--concatenate-sequences` / `--output-file-name` /
    padding flags for the auth failures. **That was wrong** — every "flagged" run was a script run, and
    the script carried the MSYS `USERPROFILE`. Those flags are *not* implicated, though they also
    aren't verified yet.
11. **Reels verified.** `cheesebagga_r12_player_reel.mp4` (15.60 s, 17.8 MB) and
    `cheesebagga_r12_enemy_reel.mp4` (15.63 s, 22.7 MB), both 1920×1080 h264 60 fps + mp3, produced
    from the same three Sequences in both perspectives. The player Reel came from a full
    `scripts/render_reel.sh` run: 110 s wall clock, `OK` on the first attempt.

## App-folder note

CS:DM hardcodes its app folder as `homedir()/.csdm` with no override, and this machine's profile root
`C:\Users\AMG` rejects writes (nothing can be created there; Defender's controlled-folder access is
off, so a third-party shield is the likely cause). Launching `csdm` with
`USERPROFILE=<repo>/home` relocates the folder and works. Every headless invocation must set it.
