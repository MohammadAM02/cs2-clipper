# Spec: Progress, Mistakes and Heatmaps

Status: built (2026-11-25). What scope.gg shows that the app did not: what the subject did wrong in a match, how
their game moves from match to match, and where on the map they win and lose fights.

## Decided

- **No new csda run for what the kept analysis already has.** The kills csda writes carry each player's place, side
  and whether the victim was blind, and the app keeps the kills whole. So Mistakes (all but team flashes), Progress
  and Heatmaps work for every match the app still has an analysis of.
- **Flashes are kept from now on**, cut to who blinded whom and for how long (`analysis.FLASH_FIELDS`), like hits.
  A match analyzed before that is analyzed again by the backfill, one a tick while CS2 and FACEIT AC are closed, the
  way hits were. Until then its review says `flashes_kept: false` and the pages say team flashes are not counted.
- **A review per match is kept beside its analysis** (`<checksum>.review.json`: the subject's line, Mistakes and
  places), so Progress and Heatmaps read one small file a match. It is worked out again when the analysis or the
  stats change (FACEIT's line added), when the subject changes, and when `review.VERSION` is raised.
- **Nothing here touches CS2 while it is open.** A radar Heatmaps lacks is asked for with a flag and read from the
  game's files on a clear backfill tick, as Replay's is.

## Mistakes

Worked out for the subject only, from what the Demo says happened. A death is a Mistake for at most one reason, the
first of these that fits:

1. `BOMB_DEATH` / `OWN_DEATH`: killed by the bomb, or by the world or their own grenade.
2. `DIED_BLIND`: csda says the victim was blind. With flashes kept, a teammate's (or their own) flash that still
   covered the death is named, and the tip is the one about waiting for the pop.
3. `OPENING_DEATH`: the round's first enemy kill, and no teammate killed the killer within the trade window (5 s,
   `stats.TRADE_WINDOW_S`).
4. `THREW_ADVANTAGE`: untraded, with their team two or more players ahead before the death (4v2, 3v1).

And apart from deaths: `TEAM_KILL` (they killed a teammate), and `TEAM_FLASH` (a flashbang of theirs blinded a
teammate for 2 s or longer; one Mistake a flashbang, however many it blinded).

Not built, and why: dying with unused utility and missed trades need inventories and positions the kept analysis
does not have; a lost clutch is not a Mistake.

## Progress

The Form is the subject's numbers over their last 10, 20 (the default), 50 or all matches with stats, each against
the same number of matches before. Ratios (K/D, opening duels won, win rate) are of the totals; Rating, ADR, HS% and
KAST are the mean of each match's. Headline numbers are FACEIT's when the match has its line, as on Stats. The maps
and the records cover every match with stats. A HS% record needs 10 kills behind it.

## Heatmaps

A place is where the subject stood for a Frag, or where they died; team kills and deaths to the world are left out.
The page draws the radar, then the heat in square cells (green where Frags outnumber deaths, red where deaths do,
brighter the more happens there), then a square a place. Without the radar the places are fitted to the canvas to
scale.

## Pages

The pages moved from a top bar to a side rack (Clips: Status, Demos to grab, Reels; Your game: Progress, Heatmaps;
Settings at the foot) with a slim strip along the top for the page's name, the status pill and the window's buttons.
Below 1264 px the rack is a rail of icons. A page lays itself out by its own width (a container query on `main`),
not the window's, so it is the same page with the rack, with the rail, and on the phone, which has neither.
Everything read is set in Space Grotesk; Silkscreen stays for titles, buttons and the pages' names. Pixelify Sans is
gone.
