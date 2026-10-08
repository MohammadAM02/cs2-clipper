# Spec: Orchestrator — a Demo arrives, Reels appear

Status: built (merged 2026-09-23); analysis moved to csda and rendering to HLAE on 2026-10-08.
Its consumer is the Clip library (`.scratch/clip-library/spec.md`, built after this). Builds on
ADRs 0001 and 0002.

## Problem

The product is a **hands-off** clipper. After a FACEIT match, the user downloads the Demo from the
matchroom, and the match's best Highlights appear as videos from both Perspectives, with no
per-match input.

## Decisions

| Topic | Decision |
| --- | --- |
| Intake | Watch the user's **Downloads** folder for FACEIT Demo files |
| When to render | **As soon as the Gate is clear**: CS2 closed, FACEIT AC not running, enough free disk space |
| What to render | The **top 5 Highlights by Score** per Demo, each in both Perspectives |
| Run model | A **background app** started at sign-in by Task Scheduler |
| Who picks Sequences | **The app** (`hlae_plan.build_sequences`), from the selected rounds |
| Output unit | One **Reel per Highlight per Perspective**: that Highlight's Clips joined in Sequence order |
| Picture | `aspect_ratio` setting: `16:9` (default, 1920×1080), `4:3` (1280×960), `4:3-hd` (1440×1080), or `4:3-stretched` (rendered 1280×960, stretched to 1920×1080 when joining) |
| Sequence unit | `sequence_event` setting: `kills` (default — one Sequence per Frag, nearby Frags merged) or `rounds` (one Sequence per whole round, ~70–95 s) |
| Language / state | **Python 3.13** (uv); our index in **SQLite**; no database server |
| Storage | Everything under `E:\cs2clips\`; only `library\` is ever shared |
| Downloads kept | The compressed Demo is kept; the unpacked `.dem` is deleted after rendering |
| Notifications | Windows notifications before CS2 launches and when Reels are ready |

## Safety

CS2 is launched only by the app, through HLAE, for offline Demo playback with `-insecure`. New in
this spec: **a hooked CS2 never runs while FACEIT AC is running.** FACEIT AC is installed on this
machine: its kernel driver (`FACEIT_AC.sys`) loads at boot, and the phase-1 renders succeeded with it
loaded; its service (`FACEITService`) starts with the FACEIT client. The Gate refuses while that
service or a FACEIT client process runs, and a render in progress is aborted the moment one appears.

## Flow

Each Demo is one row in the index with one state at a time. Every step is idempotent, so a crash or
reboot resumes where it stopped.

| # | State reached | What happens |
| --- | --- | --- |
| 1 | `spotted` | A finished FACEIT Demo is moved from Downloads into `demos\` |
| 2 | `unpacked` | `.zst` / `.gz` → `.dem`; the file must start with `PBDEMS2` |
| 3 | `analyzed` | csda reads the `.dem`; what the app uses is kept as `analyses\<checksum>.json` (ADR 0002) |
| 4 | `scored` | Every round with a subject Frag becomes a Highlight with a Score; the top 5 are marked `selected` |
| 5 | `rendering` | Two Render Jobs: `player`, then `enemy`, each waiting for the Gate |
| 6 | `joined` | One Reel per selected Highlight per Perspective |
| 7 | `done` | The unpacked `.dem` is deleted; a notification says the Reels are ready |

Terminal alternatives: `skipped` (already processed, or the subject's SteamID is not in the match)
and `failed` (a step exhausted its retries; the reason is stored).

**Intake details.** A file qualifies when its name matches FACEIT's pattern
`1-<match uuid>-<n>-<n>.dem`, optionally with `.zst` or `.gz`
(e.g. `1-2b882547-d8dd-4ef7-b5c3-6e9558217b17-1-1.dem.zst`), and its size has not changed for 10 s,
with no `.crdownload` / `.part` / `.tmp` sibling of the same name. Downloads is polled every 5 s. A
Demo is skipped if its file name or SHA-256 is already in the index.

The Downloads folder is found with Windows' known-folder lookup, not from `USERPROFILE`.

**A match with fewer than 5 Highlights** renders what it has; one with none goes straight to `done`.

## The Gate

The Gate is the check that must pass before anything launches CS2. It is evaluated every 5 s while
a Render Job waits:

1. no `cs2.exe` is running;
2. the `FACEITService` service is stopped and no process whose name starts with `faceit`
   (case-insensitive) is running — the exact client process names are confirmed with the FACEIT
   client open during build step 5;
3. at least 5 GB (the `min_free_gb` setting) is free on the drive that holds the clips folder (the
   `data_root` setting);
4. no other Render Job is running.

**Heads-up.** When the Gate clears, the app shows a notification ("Rendering 5 Highlights from
de_inferno, about 5 minutes") and waits 30 s; if the Gate closes during that wait, the launch is
cancelled.

**While rendering**, condition 2 is re-checked every 5 s. If FACEIT AC appears, the app closes the
hooked CS2 immediately, ends HLAE.exe, and keeps closing any hooked CS2 that appears for 30 s
afterwards (HLAE may still be starting one). The attempt is recorded as `aborted`, which does not
count as a failure, and the job is queued again. While a render runs, the app also asks Windows not
to sleep.

**The hooked CS2** is the `cs2.exe` whose command line contains `-insecure`; only renders start CS2
that way (the app passes it through HLAE). Every "close CS2" in this spec means closing the hooked
CS2 — the app never closes a CS2 the user started. A render never leaves a hooked CS2 or HLAE.exe
running: a hooked CS2 still running 120 s after its last recording is closed, and one an earlier run
left behind is closed before the next launch. While one runs, the Gate stays closed (condition 1),
as for any CS2.

## Rendering

One HLAE recording per Render Job (`hlae_render`, ADR 0002):

- `hlae_plan` turns the selected rounds into Sequences (one per Frag, nearby Frags merged,
  `padding_before_s` before and `padding_after_s` after; with `sequence_event = rounds`, one per whole
  round) and writes them as cfg files into CS2's cfg folder. HLAE.exe starts CS2 with
  `-insecure -condebug`, the Clips' size and `+exec cs2clipper`, and HLAE's `mirv_cmd addAtTick` runs
  each step at its tick.
- `<W>`×`<H>` comes from `aspect_ratio`.
- Each attempt gets a fresh, pre-created output folder.
- **Progress is read from console.log**, where CS2 names each cfg it runs: the step files' names are
  the markers. **Success is judged from output**: every Sequence must reach its end marker and leave a
  recording that FFmpeg joins into a Clip `sequence-<n>-tick-<start>-to-<end>.mp4`, readable by
  `ffprobe`.
- **Stall recovery.** No new marker for `stall_seconds` (180 s), a Demo that never starts playing, or
  HLAE's error window: the app closes the hooked CS2, and starts it again when that can help.
- **Up to 3 attempts** per Render Job.
- **Pause.** After 3 failed attempts in a row across any Demos, rendering pauses (a CS2 update has
  probably broken HLAE). The app notifies "check HLAE/CS2 compatibility"; `clipper resume` restarts
  rendering.

## Joining

For each selected Highlight and each Perspective:

1. Assign each Clip (its Ticks come from the `tick-A-to-B` in its file name) to the selected round its
   middle Tick falls in: the latest selected round whose start Tick is at or before that Tick. A Render
   Job records only the rounds it was given, so this is always the Clip's own round — including Sequences
   around Kills the Highlight query does not count (such as team kills) and whole-round Sequences that
   begin a little before the round's start Tick. A Clip whose middle lies before every selected
   round, or a selected round with no Clip, fails the render attempt.
2. Order each round's Clips by start Tick, numerically.
3. One Clip: copy it. Several: concatenate with FFmpeg's concat demuxer and `-c copy`, using a list
   file written at a native path next to the output. With `4:3-stretched`, the join re-encodes
   instead — `scale=1920:1080,setsar=1`, libx264 CRF 23, audio copied — even for a single Clip.
4. Verify with `ffprobe`: the Reel's duration must be within 0.25 s, plus 0.05 s per join, of the sum
   of its Clips (joining two MP3-audio Clips measured +0.045 s on this machine).

Reels are written to `E:\cs2clips\library\videos\<match checksum>\r<round>-<perspective>.mp4`.

## Scoring

`analysis` returns plain per-round facts for the subject; `scoring` turns them into Highlights. One
Highlight per (match, round). Its Type comes from the highest-scoring base rule that matched (`ACE`
… single Frag, later `CLUTCH_1vN`); bonuses add to the Score and appear only as reasons. The reasons
list every rule that matched.

Facts per round: Frags, headshots, blind Frags, knife Frags (`weapon_type = 'melee'`), no-scope
Frags, through-smoke Frags, Frags made while controlling a bot (kept for `issues/01`; no effect on
the Score), whether the subject's team won the round (compared by team name, since sides swap at
half-time), round start and end Ticks, and the Ticks of every Frag.

Initial rule table:

| Rule | Score |
| --- | --- |
| `ACE` / `4K` / `3K` / `2K` / single Frag | 100 / 70 / 40 / 15 / 5 |
| All headshots (2+ Frags) | +10 |
| Knife Frag | +30 |
| No-scope (2+ Frags) | +10 |
| Through smoke (2+ Frags) | +5 |
| Blind Frag (2+ Frags) | +10 |

Selection: the top 5 by Score, ties broken by the earlier round. Round outcome does not affect the
Score yet (an open question). Clutch rules (proposed: 1v3 = 60, 1v4 = 80, 1v5 = 100) are added
once the analysis's clutches are validated; the facts and rule table have room for them.

## Code units

A Python package `clipper/`.

| Module | Job | Interface (sketch) |
| --- | --- | --- |
| `intake` | Find finished Demos in Downloads, move them to `demos\` | `scan(downloads, demos_dir, index) -> list[DemoId]` |
| `unpack` | Decompress and check the header | `unpack(src, out_dir) -> Path` |
| `analysis` | **The only code that reads csda's output** (ADR 0002) | `match_info(data, steamid) -> MatchInfo`, `round_facts(data, steamid) -> list[RoundFacts]` |
| `scoring` | Rules → Highlights; top-N selection; pure | `score(facts, rules) -> list[Highlight]`, `select(highlights, n) -> list[Highlight]` |
| `gate` | May CS2 launch now; the during-render watch | `check() -> GateStatus(ok, reasons)` |
| `render`, `hlae_plan`, `hlae_render` | One HLAE recording with watchdogs and the success check | `RenderRequest` → `RenderResult` |
| `join` | Clips → one Reel per Highlight per Perspective | `join(highlight, clips, out_path) -> Reel` |
| `notify` | Windows notifications (PowerShell's built-in toast API, no extra dependency) | `notify(title, body)` |
| `index` | **The only code that writes our SQLite** | typed functions per table |
| `worker` | Moves each Demo through the Flow in order | `step(demo) -> None` |
| `cli` | `run`, `status`, `retry <demo>`, `resume`, `highlights <demo>`, `install` | — |

## Index

`clipper.sqlite` in the app data folder:

| Table | Columns |
| --- | --- |
| `demos` | id, file_name (unique), sha256 (unique), archive_path, dem_path, match_checksum, state, resume_state (the step a `failed` Demo resumes at), attempts, last_error, created_at, updated_at |
| `matches` | checksum (key), map, played_at, team_score, opponent_score, result (`win` / `loss` / `tie`) — from csda's analysis at step 3 |
| `highlights` | id, match_checksum, round, type, score, reasons (JSON list), frag_ticks (JSON list), round_start_tick, round_end_tick, selected; unique (match_checksum, round) |
| `render_jobs` | id, demo_id, perspective, attempt, state (`queued` / `running` / `done` / `failed` / `aborted`), output_dir, log_path, started_at, finished_at, failure |
| `clips` | id, render_job_id, highlight_id, sequence, start_tick, end_tick, path, duration_s |
| `reels` | id, highlight_id, perspective, path, duration_s; unique (highlight_id, perspective) |
| `app_state` | key, value — `paused` and `consecutive_failures`, so a pause survives a reboot |

This carries everything the Clip library spec lists under "What the library needs from the index".

## Folders

```
E:\cs2clips\demos\     compressed downloads (kept); the unpacked .dem while in use
E:\cs2clips\renders\   <match>\<perspective>\<attempt>\  raw HLAE output
E:\cs2clips\library\   videos\<match>\r<round>-<perspective>.mp4 — later also library.json,
                       thumbnails and the page; the only folder ever shared
```

The logs live in the app data folder (`.scratch/app-shell/spec.md`).

## Running it

`clipper install`, `clipper.toml` and the in-repo index (`data/clipper.sqlite`, above) give way to
the app shell (`.scratch/app-shell/spec.md`): `uv run clipper` opens the app, its settings live in
`%LOCALAPPDATA%\CS2Clipper\settings.json` (the index, the lock file and the logs move there too), and
starting at sign-in comes with piece 2's installer. The two bullets below on `clipper install` and
`clipper.toml` are the original design.

- `uv sync` installs the package and its dependencies (and drops the unused packages in `.venv`).
- `clipper install` registers a Task Scheduler task: at sign-in, `pythonw -m clipper run`, only while
  the user is signed in (CS2 needs the desktop), restart on failure every minute up to 3 times, no
  run-time limit, not stopped on battery. This is a persistent system change the user approves.
- A lock file keeps a single instance.
- `clipper status` prints each Demo's state, each Render Job's attempts, the Gate's current answer
  (e.g. "waiting: FACEIT AC is running") and whether rendering is paused.
- Settings live in `clipper.toml`, all optional: subject SteamID, Downloads path, data root, top N,
  padding, stall seconds, minimum free space, `aspect_ratio`, `sequence_event`. An unknown setting
  or value stops the app with an error rather than being ignored.

## Failure handling

| Step | Attempts | Then |
| --- | --- | --- |
| Unpack, analyze, join | 3 | `failed` with the reason |
| Render Job | 3 (aborts don't count) | `failed`; also feeds the pause rule |
| Intake | every poll | a file that never stabilises is left alone |

`clipper retry <demo>` (by index id or file name) puts a `failed` Demo back at the step that failed.

## Testing

Written test-first.

- **Unit (pure):** scoring rules against the facts of a real match; top-5 selection with ties;
  grouping Clips into Highlights by Frag Tick; the Reel duration check; Gate decisions from fake
  process and service lists; the worker's state transitions with fake steps; intake's stable-file
  detection in a temp folder.
- **csda's output:** `analysis` against trimmed real csda JSON (`tests/csda_json.py`).
- **Rendering without CS2:** `hlae_plan` is pure, and `hlae_render` takes its launcher, probe, clock and
  `sleep` as parameters, so a whole Render Job runs against a scripted world: the success check, a
  stall, a game that never starts, an abort.

## Out of scope

The Clip library UI (its own spec), pipeline control, a Reel editor, share-ready exports, clutch
and match-point rules (added once clutches are validated), phone notifications.
