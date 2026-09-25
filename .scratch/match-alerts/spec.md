# Spec: Match alerts — which Demos are worth grabbing

Status: design approved 2026-09-25; write-up reviewed the same day (the clutch rule dropped).
The first piece of the faceitperf integration (the user's fork of `iffypixy/faceitperf`, MIT).
Builds on ADR-0002 (FACEIT-only, manual acquisition) and the orchestrator
(`.scratch/orchestrator/spec.md`). The mockups the choices below were made on (private to the user):
https://claude.ai/artifact/L46nisUy3Y42RvhCxx3Lxm

## Problem

Acquisition is the one manual step left. After playing, the user has to remember which matches to
open on FACEIT, press Watch Demo in each, and do it before the download link expires (about 30 days
after the match). Nothing tells them which matches hold anything worth clipping.

FACEIT's Data API already knows. `/matches/{id}/stats` gives each player's 3K, 4K and 5K counts
(verified 2026-09-25 on the user's last five matches): exactly the rounds with 3+ Frags that the
clipping rule looks for. So the app can tell which matches have Highlights **before** a Demo is
downloaded, and put them in front of the user at the right moment.

## Decisions

| Topic | Decision |
| --- | --- |
| When | One **summary notification after the user stops playing**: CS2 has been closed for 5 minutes |
| Which matches | Finished FACEIT matches in which the subject has a round with **3+ Frags** (a 3K, 4K or Ace), and whose Demo FACEIT lists. Clutches play no part (dropped by the user 2026-09-25) |
| Notification | **Detailed**: which maps had which Highlights, with **Show matches** and **Not now** buttons |
| Show matches | Opens the **Demos to grab** page |
| Page reach | Served by the worker to the PC, the LAN and Tailscale; **no password** |
| Page on the PC | **Open** (the FACEIT matchroom, in a new tab) and **Skip** for each match |
| Page elsewhere | **View and Skip only**, with a note to grab Demos at the PC: Watch Demo downloads to whichever device it is pressed on |
| Page look | A **table**, **newest first**, with the subject's stat line: **Rating 2.0 · K–D · ADR** |
| Skipped matches | Never mentioned again |
| Ignored matches | **One reminder** about 3 days before the Demo link expires, then dropped |
| First run | The first summary also covers the past 30 days' qualifying matches that are still downloadable and whose Demo is not already in the index |
| Acquisition | Stays manual (ADR-0002): the user presses Watch Demo, and intake takes it from there |
| Credentials | `FACEIT_API_KEY` and `FACEIT_NICKNAME` from the repo's `.env`, read the way `scripts/faceit_probe.py` reads them; the key is never logged |

## Flow

1. **Notice the user stopped playing.** Each worker tick already lists processes for the Gate. When
   `cs2.exe` has been running and then stays closed for `stopped_playing_minutes`, the worker checks
   FACEIT once. It also checks once at start-up, to catch up on matches played while it was not
   running. Reminders come from the index and need no FACEIT call, so the API is never polled while
   the user plays or while the PC sits idle.
2. **Ask FACEIT.** List the subject's matches finished since the last check (on the first run, the
   past 30 days). For each one, fetch its stats (does it qualify?) and its details (is the Demo listed?
   the matchroom URL, map, score). A match whose stats or Demo are not ready is checked again every
   few minutes for up to 30 minutes; after that the summary goes out without it, and it joins the
   next one.
3. **Announce.** One notification covers every qualifying match not yet announced. Title
   "3 new matches have Highlights"; the body lists up to three matches
   ("Inferno 4K + 2× 3K · Ancient 2× 3K · Nuke 3K"), then "+N more". **Show matches** opens
   `http://127.0.0.1:<page_port>/demos`; **Not now** dismisses it and records nothing.
4. **Pick on the page.** See below.
5. **Grabbed.** When intake takes a Demo whose file name carries an announced match's ID
   (`1-<uuid>-<n>-<n>.dem.zst`), that match becomes grabbed and its row shows "✓ Got it", plus the
   Gate's reason when rendering has to wait (for example "FACEIT AC is running").
6. **Remind.** When a match that is neither grabbed nor skipped has 3 days or less left on its Demo
   link (taken to expire 30 days after the match finished), it gets its one Reminder: "Overpass demo expires in 3 days", body "Ace + 3K · you haven't
   grabbed or skipped it", buttons **Show matches** and **Dismiss**. Reminders that fall due together
   share one notification and never go out while CS2 is running. If the worker was off at the 3-day
   mark, the Reminder goes out at the next chance before the link expires. After expiry the match is
   dropped.

## The Demos to grab page

- **Address.** `http://<pc>:<page_port>/demos` (default port 8765; if it is taken, the next free
  port, and notifications link to that). It listens on every interface, so the phone reaches it on
  the home network or over Tailscale.
- **Header.** "Demos to grab" and a summary line: "4 matches · 8 Highlights · Overpass expires in
  3 days", or "All caught up. New matches show up here after you play." when nothing is waiting.
- **Rows.** One table row per match, newest first: map and time · result and score · Highlight
  badges (ACE, 4K, 3K, with counts such as "2× 3K") · Rating 2.0 · K–D · ADR · when the
  link expires (marked at 3 days or less, with a Reminder pill) · actions.
- **On the PC.** Open goes to the matchroom (FACEIT's `faceit_url`) in a new tab, and the row shows
  "Waiting for the download…" on that screen until the Demo arrives. Skip dims the row and offers
  Undo. "The PC" means a request from one of the PC's own addresses: loopback, LAN or Tailscale.
- **On other devices.** Skip and Undo only, under the note "Grab these at your PC".
- **Afterwards.** Skipped and grabbed rows stay, dimmed, for 24 hours, then drop off.
- **Live.** The page refreshes its list from the worker every few seconds, so a Demo landing in
  Downloads flips its row to ✓ without a reload.
- **Look.** faceitperf's dark, HLTV-style look, in plain HTML/CSS/JS with no build step.
- **Surface.** The server answers only the page, its list, and Skip / Undo. It serves no files, so
  the orchestrator's "only `library\` is ever shared" still holds.

## Rating 2.0

faceitperf estimates HLTV Rating 2.0 from what FACEIT reports, with linear models fitted on HLTV
data (`apps/web/src/features/stats.ts`; MIT, Copyright (c) 2024 Ansat Euler). For one map, with
KPR, APR and DPR the subject's kills, assists and deaths per round, ADR from FACEIT, and MKPR the
rounds with 2+ Frags per round ((double + triple + quadro + penta kills) / rounds):

```
Rating = 0.6844811040150518 + 0.65597945·KPR + 0.31304591·APR − 0.75999214·DPR
         + 0.00370714·ADR + 0.72169367·MKPR          (never below 0)
```

Rounds come from the match's `Rounds` stat. Only matches from the last 30 days are ever shown, and
FACEIT has reported ADR and double kills since late June 2024, so faceitperf's fallback estimates for
those two are not needed. The ported module keeps faceitperf's MIT notice.

## Index

One new table; adding it leaves existing data alone.

| Column | Meaning |
| --- | --- |
| `match_id` | FACEIT match ID (`1-<uuid>`), primary key |
| `finished_at` | When the match ended; the Demo link expires about 30 days later |
| `map`, `score`, `result` | For the page and notifications |
| `highlights` | JSON counts: `{"3k": n, "4k": n, "5k": n}` |
| `kills`, `deaths`, `assists`, `adr`, `rounds`, `rating` | The subject's stat line |
| `matchroom_url` | FACEIT's `faceit_url` |
| `state` | `waiting` (stats or Demo not ready) → `ready` → `announced` → `grabbed` / `skipped` / `expired`; `no_highlights` for matches that do not qualify |
| `announced_at`, `reminded_at`, `decided_at` | When the summary and Reminder went out; when Skip or the grab happened |
| `demo_id` | The `demos` row, once grabbed |

A match in any state before `grabbed` becomes `grabbed` when its Demo arrives, announced or not.
Undo returns a skipped match to `announced`. `app_state` keeps the FACEIT player ID and the time of
the last check.

## Settings

New `clipper.toml` settings, all optional:

| Setting | Default | Purpose |
| --- | --- | --- |
| `match_alerts` | `true` | Turns match alerts off |
| `stopped_playing_minutes` | `5.0` | How long CS2 must be closed before the summary |
| `page_port` | `8765` | Port of the Demos to grab page |

## Where it lives

- `clipper/faceit.py`: the Data API client — player lookup, match history, match details, match
  stats; Bearer auth; timeouts; never logs the key.
- `clipper/rating.py`: the Rating 2.0 port, with faceitperf's notice.
- `clipper/alerts.py`: the decisions as plain functions over plain data and a clock — does a match
  qualify, has the user stopped playing, what to announce, which Reminders are due, and the
  notification text.
- `clipper/page.py`: the page server (standard-library `http.server`, a thread in the worker) and the
  page's HTML, CSS and JS.
- Changes: `worker.py` (an alerts step in `tick`), `notify.py` (toast buttons), `index.py` (the
  table), `intake.py` (grabbed on take), `config.py` (the settings and `.env`), `cli.py` (`status`
  shows whether alerts are on, why not, and the page's address).

## When something goes wrong

| Case | What happens |
| --- | --- |
| FACEIT key missing or rejected | Alerts switch off; `clipper status` says why; no notifications about it |
| `FACEIT_NICKNAME` belongs to another player | The FACEIT account's SteamID must equal `subject_steamid`; otherwise alerts stay off and `clipper status` names the mismatch |
| FACEIT down, slow or rate-limited | Retried at the next check; unannounced matches stay queued for the next summary |
| Stats or Demo not ready after 30 minutes | The summary goes out with what is ready; the rest join the next summary, and the Reminder still catches them |
| Worker off while the user played | The start-up check catches up on everything since the last check |
| Demo downloaded without an alert | Intake matches it by file name; it counts as grabbed, so no Reminder |
| A notification cannot be shown | Logged, as today; the page still lists everything |
| `page_port` taken | The next free port; notifications link to it |
| The Windows firewall blocks other devices | The page still works on the PC; `clipper status` shows its address. Windows asks once whether to allow the worker on the network — allow private networks |

## Testing

- **Rules, with a fake clock:** what qualifies, the 5-minute stopped-playing check, the summary
  text, the one Reminder (never after a Skip or a grab), and the 30-day first run.
- **FACEIT client:** against saved, scrubbed API responses, including errors, timeouts and missing
  fields.
- **Rating 2.0:** the Python port against faceitperf's formula on the same inputs.
- **Page server:** started on a spare port in tests; the list renders, Skip and Undo reach the index,
  and a request from another device gets no Open button.
- **Index:** the new table appears in an existing `clipper.sqlite` without touching its data.
- **With the user at the PC:** one real session end (notification → page → Watch Demo → the worker
  takes the Demo), and the page opened from the phone over Wi-Fi.

## Language

Terms used here that `CONTEXT.md` does not define yet; add them with `/domain-modeling` once they
settle:

- **Match Alert**: the summary notification after the user stops playing.
- **Reminder**: the one notification before a Demo link expires.
- **Expected Highlights**: Highlights predicted from FACEIT's stats before the Demo is downloaded.
- **Grab** / **Skip**: download a match's Demo through its matchroom / decline it.

## Open

- **Toast buttons that open a URL** are untested with the PowerShell app ID `notify.py` borrows.
  Check on the PC before building on them.
- **Whether the Clip library reuses this server** instead of `tailscale serve` is decided when the
  library design resumes.
- **Tailscale** is not installed yet; until it is, other devices reach the page only on the home
  network.

## Later (not in this spec)

From the same FACEIT data, for the Clip library: a match page (the 10-player scoreboard with the
match's Reels) and a "Me" page (faceitperf's profile, with Clips attached and a Get demo button on
good matches not yet grabbed). Also floated: a session recap, stat title cards in Reels,
teammates' Highlights, and picking extra rounds from the phone.
