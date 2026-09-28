# Spec: Orchestrator — a Demo arrives, Reels appear

Status: design approved 2026-09-22; this write-up awaits review.
Answers `.scratch/v1-pipeline/issues/04-orchestrator.md`. Its consumer is the Clip library
(`.scratch/clip-library/spec.md`, built after this). Builds on ADRs 0001–0003.

## Problem

The product is a **hands-off** clipper. After a FACEIT match, the user downloads the Demo from the
matchroom, and the match's best Highlights appear as videos from both Perspectives, with no
per-match input. CS:DM deliberately does not do this; everything around its CLI is ours.

## Decisions

| Topic | Decision |
| --- | --- |
| Intake | Watch the user's **Downloads** folder for FACEIT Demo files |
| When to render | **As soon as the Gate is clear**: CS2 closed, FACEIT AC not running, CS:DM GUI closed |
| What to render | The **top 5 Highlights by Score** per Demo, each in both Perspectives |
| Run model | A **background app** started at sign-in by Task Scheduler |
| Who picks Sequences | **CS:DM**, via `--rounds` with the selected rounds (the only verified form) |
| Output unit | One **Reel per Highlight per Perspective**: that Highlight's Clips joined in Sequence order |
| Picture | `aspect_ratio` setting: `16:9` (default, 1920×1080), `4:3` (1280×960), `4:3-hd` (1440×1080), or `4:3-stretched` (rendered 1280×960, stretched to 1920×1080 when joining) — the same four options `render_reel.sh` gained |
| Sequence unit | `sequence_event` setting: `kills` (default — one Sequence per Frag, nearby Frags merged) or `rounds` (one Sequence per whole round, ~70–95 s) |
| Language / state | **Python 3.13** (uv); our index in **SQLite**; CS:DM keeps its own Postgres |
| Storage | Everything under `E:\cs2clips\`; only `library\` is ever shared |
| Downloads kept | The compressed Demo is kept; the unpacked `.dem` is deleted after rendering |
| Notifications | Windows notifications before CS2 launches and when Reels are ready |

## Safety

CS2 is launched only by CS:DM, hooked by HLAE, for offline Demo playback with `-insecure`. New in
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
| 3 | `analyzed` | `csdm analyze <dem> --source faceit`; succeeds only if the match then appears in CS:DM's database |
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

The Downloads folder is found with Windows' known-folder lookup, not from `USERPROFILE`: the app runs
with the user's real profile, and the `USERPROFILE=<repo>/home` override is applied only to the csdm
processes it starts.

**A match with fewer than 5 Highlights** renders what it has; one with none goes straight to `done`.

## The Gate

The Gate is the check that must pass before anything launches CS2. It is evaluated every 15 s while
a Render Job waits:

1. no `cs2.exe` is running;
2. the `FACEITService` service is stopped and no process whose name starts with `faceit`
   (case-insensitive) is running — the exact client process names are confirmed with the FACEIT
   client open during build step 5;
3. no `cs-demo-manager.exe` is running that the app did not start itself (a GUI breaks headless
   renders: it runs with the real profile, which cannot hold CS:DM's app folder on this machine, and
   it holds port 4574, the port `csdm video` uses to talk to CS2). Any `cs-demo-manager.exe` left
   behind by the app's own csdm calls is stopped after each call;
4. at least 5 GB is free on E:;
5. no other Render Job is running.

When the Gate first blocks on condition 3, the app notifies the user once ("close CS Demo Manager so
rendering can start"). It never kills a CS:DM instance it did not start.

**Heads-up.** When the Gate clears, the app shows a notification ("Rendering 5 Highlights from
de_inferno, about 5 minutes") and waits 30 s; if the Gate closes during that wait, the launch is
cancelled.

**While rendering**, condition 2 is re-checked every 5 s. If FACEIT AC appears, the app closes the
hooked CS2 immediately, stops csdm once no hooked CS2 is left, and keeps closing any hooked CS2 that
appears for 30 s afterwards (HLAE may still be starting one). The attempt is recorded as `aborted`,
which does not count as a failure, and the job is queued again. A CS:DM GUI opened mid-render is not
watched for: if it breaks the attempt, that is an ordinary failure. While a render runs, the app also
asks Windows not to sleep.

**The hooked CS2** is the `cs2.exe` whose command line contains `-insecure`; only renders start CS2
that way. Every "close CS2" in this spec means closing the hooked CS2 — the app never closes a CS2
the user started. If csdm exits normally and a hooked CS2 is still running, it is left alone and the
Gate holds further renders until it has closed.

## Rendering

One `csdm video` call per Render Job:

```
csdm video <demo.dem> --mode player --steamids <subject SteamID64> --event <sequence_event>
  --rounds <selected rounds> --perspective <player|enemy> --width <W> --height <H>
  --output <E:/cs2clips/renders/<match>/<perspective>/<attempt>/>
  --close-game-after-recording --start-seconds-before 4 --end-seconds-after 2
```

- `<W>`×`<H>` comes from `aspect_ratio`. With `sequence_event = rounds`, how the padding flags and
  the enemy view behave is unverified until checked on a real Demo (plan Task 1, optional step).

- Environment from phase 1: every path native (`E:/…`, `C:/…`), `USERPROFILE=<repo>/home`, the
  portable Postgres `bin` on `PATH`, and `PGPASSWORD` not exported into csdm's environment.
- Each attempt gets a fresh, pre-created output folder.
- **Success is judged from output, never the exit code** (csdm exits 0 on failure). An attempt
  succeeds only if its log contains `Video generated`, contains none of the known failure messages
  (`password authentication failed`, `ECONNREFUSED`, `Game error`, `Invalid argument`,
  `does not exist`), and produces at least one `sequence-*.mp4`, each readable by `ffprobe` and lying
  inside a selected round.
- **Stall recovery.** If CS2 is running and no `ffmpeg.exe` has run for 180 s, the attempt is
  stalled (the missed-seek race, `spike/ACCEPTANCE.md` finding 5). The app closes CS2 — never `csdm`,
  whose death would orphan CS2 — lets csdm exit on its own, and retries.
- **csdm never launched CS2.** If no `cs2.exe` has appeared 5 minutes after csdm started, stopping
  csdm is safe (there is no game to orphan); the attempt fails.
- **Up to 3 attempts** per Render Job.
- **Pause.** After 3 failed attempts in a row across any Demos, rendering pauses (a CS2 update has
  probably broken HLAE). The app notifies "check HLAE/CS2 compatibility"; `clipper resume` restarts
  rendering.
- The padding values (4 s before, 2 s after) and the exact `--rounds` value format are confirmed by
  the first build task, a manual run on the existing Demo.

## Joining

For each selected Highlight and each Perspective:

1. Assign each Clip (its Ticks come from the `tick-A-to-B` in its file name) to the selected round its
   middle Tick falls in: the latest selected round whose start Tick is at or before that Tick. CS:DM
   renders only the rounds it was given, so this is always the Clip's own round — including Sequences
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

`csdm_db` returns plain per-round facts for the subject; `scoring` turns them into Highlights. One
Highlight per (match, round). Its Type comes from the highest-scoring base rule that matched (`ACE`
… single Frag, later `CLUTCH_1vN`); bonuses add to the Score and appear only as reasons. The reasons
list every rule that matched.

Facts per round: Frags, headshots, blind Frags, knife Frags (`weapon_type = 'melee'`), no-scope
Frags, through-smoke Frags, Frags made while controlling a bot (kept for issue 13; no effect on the
Score), whether the subject's team won the round (compared by team name, since sides swap at
half-time), round start and end Ticks, and the Ticks of every Frag.

Initial rule table (from issue 03 and the current query):

| Rule | Score |
| --- | --- |
| `ACE` / `4K` / `3K` / `2K` / single Frag | 100 / 70 / 40 / 15 / 5 |
| All headshots (2+ Frags) | +10 |
| Knife Frag | +30 |
| No-scope (2+ Frags) | +10 |
| Through smoke (2+ Frags) | +5 |
| Blind Frag (2+ Frags) | +10 |

Selection: the top 5 by Score, ties broken by the earlier round. Round outcome does not affect the
Score yet (open question on issue 03). Clutch rules (issue 03's proposal: 1v3 = 60, 1v4 = 80,
1v5 = 100) are added once issue 07's validation lands; the facts and rule table have room for them.

## Code units

A Python package `clipper/`, dependencies `psycopg`, `zstandard`, `psutil`.

| Module | Job | Interface (sketch) |
| --- | --- | --- |
| `intake` | Find finished Demos in Downloads, move them to `demos\` | `scan(downloads, demos_dir, index) -> list[DemoId]` |
| `unpack` | Decompress and check the header | `unpack(src, out_dir) -> Path` |
| `csdm_db` | **The only code that reads CS:DM's database** (ADR 0003) | `match_for_demo(dem_path) -> Match`, `round_facts(checksum, steamid) -> list[RoundFacts]` |
| `scoring` | Rules → Highlights; top-N selection; pure | `score(facts, rules) -> list[Highlight]`, `select(highlights, n) -> list[Highlight]` |
| `gate` | May CS2 launch now; the during-render watch | `check() -> GateStatus(ok, reasons)` |
| `render` | One csdm call with watchdogs and the success check | `render(demo, perspective, rounds, out_dir) -> RenderResult` |
| `join` | Clips → one Reel per Highlight per Perspective | `join(highlight, clips, out_path) -> Reel` |
| `notify` | Windows notifications (PowerShell's built-in toast API, no extra dependency) | `notify(title, body)` |
| `index` | **The only code that writes our SQLite** | typed functions per table |
| `worker` | Moves each Demo through the Flow in order | `step(demo) -> None` |
| `cli` | `run`, `status`, `retry <demo>`, `resume`, `highlights <demo>`, `install` | — |

`db/highlights.sql` is replaced by `csdm_db` (facts) plus `scoring` (rules). That fixes issues 08
(rounds mixed across matches) and 09 (knife bonus). `scripts/render_reel.sh` stays as a manual tool
until `clipper` renders end to end, then is retired; issues 10 and 11 are fixed in `render` and `join`
instead.

## Index

`data/clipper.sqlite` (gitignored):

| Table | Columns |
| --- | --- |
| `demos` | id, file_name (unique), sha256 (unique), archive_path, dem_path, match_checksum, state, resume_state (the step a `failed` Demo resumes at), attempts, last_error, created_at, updated_at |
| `matches` | checksum (key), map, played_at, team_score, opponent_score, result (`win` / `loss` / `tie`) — copied from CS:DM at step 3 |
| `highlights` | id, match_checksum, round, type, score, reasons (JSON list), frag_ticks (JSON list), round_start_tick, round_end_tick, selected; unique (match_checksum, round) |
| `render_jobs` | id, demo_id, perspective, attempt, state (`queued` / `running` / `done` / `failed` / `aborted`), output_dir, log_path, started_at, finished_at, failure |
| `clips` | id, render_job_id, highlight_id, sequence, start_tick, end_tick, path, duration_s |
| `reels` | id, highlight_id, perspective, path, duration_s; unique (highlight_id, perspective) |
| `app_state` | key, value — `paused` and `consecutive_failures`, so a pause survives a reboot |

This carries everything the Clip library spec lists under "What the library needs from the index".

## Folders

```
E:\cs2clips\demos\     compressed downloads (kept); the unpacked .dem while in use
E:\cs2clips\renders\   <match>\<perspective>\<attempt>\  raw CS:DM output
E:\cs2clips\library\   videos\<match>\r<round>-<perspective>.mp4 — later also library.json,
                       thumbnails and the page; the only folder ever shared
E:\cs2clips\logs\      clipper.log and one log per csdm call
```

Today's phase-1 files at the root of `E:\cs2clips` move into `E:\cs2clips\_phase1\`.

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
- At start-up the app starts the portable Postgres if it is not running (it is not a Windows service
  and does not survive a reboot), reusing `scripts/setup_local_postgres.py`'s start logic.
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

- **Unit (pure):** scoring rules against facts from the analyzed match (34 Frags; round 12 = `4K`,
  Score 80; knife → +30); top-5 selection with ties; grouping Clips into Highlights by Frag Tick;
  Reel duration check; Gate decisions from fake process and service lists; the worker's state
  transitions with fake steps; intake's stable-file detection in a temp folder.
- **Integration (read-only):** `csdm_db` against the real CS:DM database for match
  `aea4e59ccfc6c962`; skipped when Postgres is down. With a second analyzed match, the facts stay per
  match (issue 08).
- **Fake csdm:** a script that writes a log and dummy `sequence-*.mp4` files, to test `render`'s
  success check, failure messages, the stall path (a fake that never starts `ffmpeg`) and the
  never-launched path — without CS2.
- **End to end, triggered by the user:** the existing Demo from Downloads to Reels, checked against
  `spike/ACCEPTANCE.md`'s criteria. Preceded by the padding and `--rounds` check.

## Build order

1. Manual check on the existing Demo: padding flags and the `--rounds` value format.
2. `csdm_db` + `scoring` (fixes 08, 09).
3. `index`.
4. `intake` + `unpack`.
5. `gate`.
6. `render` against the fake csdm.
7. `join`.
8. `worker` + `cli` + `notify`.
9. `install`, then the end-to-end run.

## Out of scope

The Clip library UI (its own spec), pipeline control, a Reel editor, share-ready exports, clutch
and match-point rules (added when issue 07 validates clutches), phone notifications.

## Changes to other documents

- `.scratch/v1-pipeline/spec.md`: the Stack line becomes Python + SQLite index + CS:DM's Postgres
  (no Fastify); the Safety line gains the FACEIT AC rule.
- `CONTEXT.md`, to be added during implementation:
  - **Gate**: the check that must pass before the pipeline launches CS2. _Avoid_: lock (the lock is
    one of its conditions).
  - **Reel**: add that the pipeline produces one per Highlight per Perspective, and that this is what
    the library plays.
