# Spec: Pick your rounds

Status: designed 2026-11-04 with the user; built the same day on branch `pick-rounds` (not merged yet).
Replaces the top-N selection of the orchestrator (`.scratch/orchestrator/spec.md`) and changes the
Reels page of the app shell (`.scratch/app-shell/spec.md`, Pages, Reels).

## Problem

The clipper records the top N Highlights of every match by Score (`top_n`, Settings → Rendering →
Highlights per match, default 5). The Score ranks rounds but never says a round is good enough, so a
fixed count misfires both ways. On the 54 matches in the index on 2026-11-04:

- 47 of the 299 Highlights recorded were single-Frag rounds, there only to make up the count;
- a 3K on Mirage was left out because its match had five rounds that scored higher;
- the count had been set to 5, 6, 7 and 10 by hand.

The user wants to choose, as on Allstar.gg: each match lists its rounds worth clipping, and the
clipper records the ones the user picks.

## Decisions

| Topic | Decision |
| --- | --- |
| When to record | **Only after you pick.** A scored match finishes with no Reels and waits on the Reels page; nothing records until you press Clip |
| What is listed | Highlights **worth clipping**: 2+ Frags, or a single Frag with something special about it (Worth clipping, below), and the match's Breaks |
| How a pick records | **A pick sends a done Demo back to rendering** for what each view lacks, the way `Index.ask_render` sends one back for a missing view today. A first pick and a later one take the same path |
| Where you pick | **The Reels page, in each match's card**: rows with tick boxes and a Clip button (layout A of the mockups) |
| Breaks | **Picked like Highlights**; no longer recorded along with whatever is selected |
| Views | Each pick records in the views of Settings → Views to render, as today |
| Phone | Unchanged: the Reels you have, no tick boxes. Picking is a PC action, like every action today |
| `top_n` | Removed. A `top_n` left in settings.json is ignored (settings.py keeps and ignores unknown keys) |
| Score | Unchanged, and no longer selects. It still decides which Reel a notification's Watch opens (`Index.best_reel`) |

Rejected (2026-11-04):

- **Auto-picking above a Score floor** ("every 2K or better, at least 2, at most 10"), the first
  proposal: the user wants to choose.
- **The app's picks unless you change them within a few hours**: a timer and a fallback rule for
  little gain.
- **The app's picks now, more added later**: keeps the filler.
- **A new waiting state that keeps the unpacked Demo until you pick** (approach B): unpacked Demos
  pile up for matches not picked yet, and adding rounds later needs approach A's path anyway.
- **Round chips under a thumbnail** (layout B): the tags get cramped, and a match with no Reel has
  no frame to show. **The round strip as the picker** (layout C): small targets, and a round's tags
  are hidden until you hover it.

## Worth clipping

A Highlight is worth clipping when the subject has 2+ Frags in the round, or one of these holds:

| Signal | Reason (shown as a tag) | From the kept analysis |
| --- | --- | --- |
| Desert Eagle | `deagle` | a Frag with `weaponName == "Desert Eagle"` |
| Knife | `knife kill` (today's) | a Frag with `weaponType == "melee"` |
| Low HP | `<n> HP`, `n` the lowest | a Frag made on 20 HP or less (HP at a Frag, below) |
| No-scope | `no-scope` (today's) | a Frag with `isNoScope` |
| Jump shot | `jump shot` | a Frag with `is_killer_airborne` |
| Wallbang | `wallbang` | a Frag with `penetratedObjects > 0` |
| Through smoke | `through smoke` (today's) | a Frag with `isThroughSmoke` |
| Flashed | `blind kill` (today's; the mockups called it Flashed) | a Frag with `is_killer_blinded` |
| Clutch | `clutch 1v<n>`, `n` the opponents | a `clutches` entry of the round with the subject as `clutcherSteamId`, `opponentCount >= 2` and `hasWon` |

- **A reason shows whatever the Frag count.** Today `no-scope`, `through smoke` and `blind kill`
  appear only with 2+ Frags. They still add to the Score only with 2+ (the Score table is
  unchanged), but the tag now shows on a single Frag too. The new reasons add nothing to the Score.
- **A Clutch with no subject Frag is no Highlight** (`score_round` returns None), as Clips are cut
  around Frags.
- **Plain single-Frag Highlights stay in the index, unlisted.** There is no "show all".
- **The page shows a single Frag's Highlight Type, `FRAG`, as `1K`**, beside 2K to ACE. The stored
  type is unchanged.
- **`scoring.score_round` decides it**, pure as now: `Highlight` gains `worthy: bool`. `RoundFacts`
  gains `deagle_frags`, `airborne_frags`, `wallbang_frags`, `lowest_hp` (`int | None`: None when
  the analysis has no hits kept) and `clutch_opponents` (0 without a won Clutch).
- Reasons are stored as shown less the first capital (`reasonTags` in `app.js` capitalises it):
  `14 HP`, `clutch 1v3`.

On the 54 matches of 2026-11-04, before low HP (they have no hits kept yet), the list shows a median
of 7 rounds a match (3 to 14). 84 single-Frag rounds join it: through smoke 38, wallbang 23, blind
kill 16, Desert Eagle 12, jump shot 1, Clutch 1.

### HP at a Frag

csda's full output has every hit (`damages`, each with `tick`, `roundNumber`, `victimSteamId` and
`healthDamage` among others). `Analyses` reads it for Stats and then drops it. The kept analysis
(`analysis.KEPT`) now keeps `damages` too, each hit trimmed to those four fields.

A player's HP at a Frag is 100 less the `healthDamage` they took in that round at or before the
Frag's tick (a hit in the same tick counts). Every round of competitive CS2 starts at 100 HP.

A kept analysis without `damages` (every match analyzed before this change) scores as before, without
the low-HP signal. The Stats backfill (`backfill.py`) analyzes those matches again the way it fills
in missing Stats: one csda run a tick, newest match first, only while the pipeline has nothing to do
and neither CS2 nor FACEIT AC is open, from the kept download. Each is then scored again, keeping
its picks, with no notification. A match whose download is gone keeps scoring without it.

## Picking

### The index

- **`highlights.selected` keeps its meaning** (this row should have Reels) **but you set it**, not
  `top_n`. The queries that list Reels (`reel_matches`, `match_reels`) keep filtering on it.
- **New column `highlights.worthy`** (0 or 1, default 0), set by scoring. A Break's row (round −N) is
  listed whenever it exists: only Breaks someone talks in are saved.
- **`save_highlights(checksum, highlights)` loses `selected_rounds`, and saving again keeps
  `selected`**, so a match scored again (the HP backfill, a later rule change) keeps your picks.
- **`pick(checksum, rounds, breaks)`**, one transaction like `ask_render`: checks that every round is
  a Highlight of the match worth clipping and every Break one of its Breaks; sets `selected`; and,
  when the Demo is `done`, queues a Render Job for each wanted view that lacks a Reel of something
  picked (a Break counts for your view only) and moves the Demo to `rendering`. A Demo in another
  active state is left to the worker (below). Something already picked is left as it is. Raises
  with a plain reason when picks cannot be recorded (Refusals).

### The worker

- **Scoring** saves every Highlight with `worthy`, then finds the match's Breaks whenever it has something
  worth clipping (before, only when something was selected), while the unpacked Demo is still there. A match
  with nothing worth clipping is not listed, so its talk is not looked for: it would only list a match of talk.
- **After scoring**, a Demo whose picks all have their Reels (a new Demo has no picks) finishes
  (`done`) without a Render Job: its unpacked Demo is deleted as after a render, and its download is
  kept. The first time a Demo finishes with something worth clipping, a notification says so
  (Notifications).
- **A render records only what each view lacks**: the picked Highlights the view has neither a Reel nor
  recorded Clips of, and, in your view, the picked Breaks with neither (`Index.highlights_to_record`,
  `breaks_to_record`). Each view was already its own CS2 launch (`_render` hands `_try_render` one Render Job),
  so a launch's `RenderRequest.rounds` is just what its view lacks. A request with no rounds (a Break picked
  alone) records no round at all: `hlae_render` no longer reads no rounds as every round.
- **A picked Break the render had no room for** beside the rounds' Sequences (`hlae_plan.add_breaks`) is given
  back, so it can be picked again (alone, it has room) rather than asked for in every launch.
- **Joining skips a view a Highlight was never recorded in** (picked after that view last recorded, the view
  since turned off in Settings) rather than joining no Clips into a Reel.
- **Before a Demo finishes**, the worker looks for picks a wanted view still lacks (made while it
  rendered) and queues another Render Job instead of finishing.
- **A view that fails `MAX_RENDER_ATTEMPTS` (3) times gives its picks back**, and so does a Demo whose
  download is gone when it has to be unpacked again: what was recorded is joined first, then picked Highlights
  and Breaks with no Reel in any view are unpicked, so their tick boxes come back; the reason stays as the
  Demo's `last_error` for the card, and the Demo is done, not failed. Something with a Reel in one view keeps
  it, and the other view can be asked for with the existing render button. `PAUSE_AFTER_FAILURES` (3 failed
  launches in a row) still pauses rendering.
- **The Status page's estimate** counts, for each view left, the Highlights it still lacks, at the pace of the
  last renders; a render's pace counts the Highlights it has Clips of (`render_seconds`), not all those picked.
- `scoring.select` and `Config.top_n` are removed. `clipper highlights <demo>` marks Highlights
  worth clipping with `+` and picked ones with `*`.

### Upgrading

On the first start of this version, once (a flag in `app_state`):

1. Every match is scored again from its kept analysis (no csda run), which sets `worthy` and the new
   reasons and keeps `selected`.
2. A Demo that is `rendering` or `joined` finishes as it is, with what `top_n` picked.
3. Every other Highlight that is selected but has no Reel is unpicked. A Demo `scored` and not yet
   rendering therefore finishes ready to pick.

Matches with Reels keep their picks; their other Highlights worth clipping get tick boxes.

### Notifications

- **Ready to pick**, once per Demo, the first time it finishes with something worth clipping:
  "Dust2 is ready to pick: 8 rounds worth clipping." (with ", and halftime talk" when it has a
  Break). Its **Pick rounds** opens the window at the Reels page with that match open, through a new
  link form, `cs2clipper://reels?match=<checksum>`, that `links.valid_page` allows. Never for a
  match scored again or analyzed again by the backfill.
- **Rendering highlights** (the heads-up) says what that launch records: "2 Highlights and the talk at
  halftime from de_dust2 (player view)". **Highlights ready** is as before, counting the picked Highlights
  with Reels; a launch of talk alone gets no "ready" toast.
- **Couldn't record your picks**, when picks are given back: "2 picks from de_dust2 couldn't be recorded:
  <reason>. Tick them again to try again." When nothing is given back (only a view is missing), the toast
  is the one from before: "Couldn't render the Enemy POV".

### Refusals

Picks cannot be recorded, and the card says why in place of the Clip button, when:

- the match has no Demo: "This match has no Demo." (as today);
- the Demo's download is gone: "This Demo's download is gone, so its rounds can't be recorded.";
- the Demo has failed: "This Demo failed: <last_error>. Retry it on Status."

## The Reels page

Layout A, as mocked up on 2026-11-04.

- **Every match with anything worth clipping is listed**, newest first, whether or not it has Reels
  (`reel_matches` lists only matches with Reels today). A match with nothing worth clipping is not
  listed, as today.
- **A match's rows** are its Highlights worth clipping and its Breaks, in round order (a Break after
  the round it follows). Each row is in one of four states:

  | State | The row shows |
  | --- | --- |
  | Open | a tick box (on the PC) |
  | Queued | "Queued" |
  | Recording | "Recording": its Demo's Render Job is running |
  | Clipped | Play, and "Your view · Enemy POV" for the views it has, as today |

  A row with Reels in some views counts as clipped while another view renders.
- **A match with nothing picked** shows "Ready to pick" (only when picks can record) and opens expanded. A match
  with nothing clipped says how many rounds are worth clipping where it would count its Highlights.
- **The footer** (on the PC) adds "N of M ticked", a hint for when picks record, and **Clip N
  rounds**. The hint follows the Gate, the Schedule and the pause: "records once CS2 and FACEIT AC
  are closed", "records when the PC is idle", "records overnight", "rendering is paused". Clip with
  nothing ticked says "Tick at least one round first." and sends nothing. Stats, the render-a-view
  buttons and Open folder stay.
- **A render-a-view button asks for a view that a picked Highlight lacks** (`_render_options`; today:
  a view the match has no Reel of at all), so a match clipped in your view only can still get its
  Enemy POV.
- **A refusal** replaces the Clip button with its reason, and the rows have no tick boxes.
- **Talk rows show only when your view is among the views to render.** A picked Break records in
  your view, with both teams' voices, as today.
- **Off the PC** the page is as today: clipped rows only, no tick boxes, no Clip.
- The filter chips apply to every row. The hero carousel lists clipped Highlights only.

### Endpoints

- **`GET /api/reels`** lists every match with anything worth clipping. A match gains `ready`
  (nothing picked yet), `pick_refusal` (why picks can't record now, or null) and `pick_hint`. Each
  row, Highlight or Break, gains `state` (`open`, `queued`, `recording`, `clipped`); an open row has
  no Reel ids.
- **`POST /api/reels/<checksum>/pick`**, body `{"rounds": [3, 9], "breaks": [12]}` (a Break by the
  round it follows): PC only and with the marker header, like every action. 202 with
  `{"queued": {"rounds": [...], "breaks": [...]}}`; 400 for a body of another shape, a round that
  is not a Highlight of the match worth clipping, or a Break the match does not have; 404 for an
  unknown match; 409 with the plain reason for a refusal.

## Code units

| Module | Change |
| --- | --- |
| `scoring.py` | `worthy` and the new reasons; `select` removed |
| `model.py` | `RoundFacts` gains the new facts; `Highlight` gains `worthy` |
| `analysis.py` | keeps the trimmed `damages`; `round_facts` works out the new facts, HP at a Frag and Clutches |
| `index.py` | the `worthy` column; `save_highlights` keeps `selected`; `pick`; the Reels queries list matches and rows worth clipping with their state; the one-time upgrade |
| `worker.py` | scoring finds Breaks for a match worth clipping and finishes without rendering; renders record what each view lacks; picks made mid-render; giving picks back; Ready to pick; the one-time move to picks |
| `hlae_render.py` | a request with no rounds records only its Breaks |
| `backfill.py` | analyzes again the matches whose kept analysis has no `damages`, and scores them again |
| `web.py` | `/api/reels` match and row fields; `POST /api/reels/<checksum>/pick` |
| `pages/reels.html`, `pages/app.css` | the rows, the footer, Ready to pick, `FRAG` shown as `1K` |
| `links.py` | `reels?match=<checksum>` |
| `config.py`, `settings.py` | `top_n` removed |
| `cli.py` | `highlights` marks worth clipping and picked |

## Testing

Written test-first, as before, with the fakes in `tests/fakes.py` and the csda fixtures in
`tests/csda_json.py`.

- **Scoring:** each signal makes a single Frag worth clipping, and a plain one isn't; 2+ Frags always
  are; 20 HP counts and 21 doesn't; a Clutch needs 2+ opponents, a win and a subject Frag; the new
  reasons add nothing to the Score; no-scope, smoke and blind still score only with 2+ Frags.
- **Analysis:** HP at a Frag from the kept hits, a hit in the same tick included; the kept analysis
  trims each hit to four fields; one without `damages` gives `lowest_hp` None.
- **Index:** saving again keeps `selected`; `pick` refuses a round not worth clipping and a Break the
  match lacks, leaves earlier picks alone, and queues only the views that lack a Reel; the upgrade
  unpicks selected rows with no Reel, except for a Demo rendering or joined.
- **Worker:** a new Demo finishes without a Render Job and notifies once; a pick records only what
  each view lacks; a pick made mid-render is recorded before the Demo finishes; a view that keeps
  failing gives its picks back and notifies.
- **Backfill:** a match without `damages` is analyzed again and scored again, its picks kept, with no
  notification.
- **Web**, through Flask's test client: `/api/reels` lists ready matches and each row's state; the
  pick endpoint's 202, 400, 404 and 409; it refuses other devices and requests without the marker
  header.
- **Links:** `reels?match=` takes a checksum and nothing else.

### To confirm on this PC (by hand)

1. A real FACEIT Demo end to end: Ready to pick opens Reels at the match; pick 2 rounds; only those 2
   record, in both views.
2. Pick one more round of the same match later: only it records, and the first two keep their Reels.
3. Pick a halftime row: your view only, with both teams' voices.
4. After the first start of this version: the 54 matches keep their Reels, and their other rounds
   worth clipping have tick boxes.
5. After a day with the PC idle at times: old matches have `<n> HP` tags where they apply.

## Open

- **The list may feel long.** Through-smoke single Frags are the largest group joining it (38 of 84
  on the 54 matches). If it does, through smoke is the first signal to drop for single Frags.

## Out of scope

- Picking from the phone.
- Unticking a round after Clip, and deleting a clipped round's Reels.
- Choosing views per round.
- Scoring Clutches (the orchestrator's proposed 1v3 = 60, 1v4 = 80, 1v5 = 100) or any other Score
  change.
- A Ready to pick line on Status.

## Changes to other documents

- `.scratch/orchestrator/spec.md`: Decisions, "What to render", and Scoring, "Selection", give way
  to this spec; the `scored` step no longer marks the top 5 `selected`.
- `.scratch/app-shell/spec.md`: Pages, Reels, gives way to this spec's Reels page; Out of scope's
  "The clip selection change (3+ Frags) and any other selection rule" is done here.
- `CONTEXT.md`: adds **Pick** (a Highlight or Break you chose to clip) and **Worth clipping**;
  **Score** drives ordering (selection is yours); a **Break** is picked like a Highlight.
- `README.md`: "The best five of each match get recorded" becomes "you pick which get recorded"; the
  settings list loses Highlights per match.
- `CHANGELOG.md`: a section for the release that ships it.
