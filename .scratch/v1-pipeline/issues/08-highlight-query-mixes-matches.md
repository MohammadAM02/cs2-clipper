# 08 — Highlight query mixes rounds from different matches

Status: resolved
Type: task

## Problem

`db/highlights.sql` never matches on `match_checksum`:

- the `frags` CTE groups by `k.round_number` alone (line 31);
- `JOIN rounds r ON r.number = f.round_number` (line 65) has no checksum condition.

Output is correct today only because CS:DM's database holds one match (`aea4e59ccfc6c962`). Once a
second Demo is analyzed:

- **with `-v checksum=…`**, every round joins the same-numbered round of *every* analyzed match, so
  rows repeat with other matches' `start_tick` / `end_tick`;
- **without it**, Frags from different matches merge into one round: two matches' round-12 3Ks
  become a single 6-Frag row typed `FRAG` with a base Score of 5.

Every new Demo is analyzed into the same database, so this breaks on the pipeline's second match.

## Fix

- Group by `(k.match_checksum, k.round_number)` and return `match_checksum`.
- Join on `r.match_checksum = f.match_checksum AND r.number = f.round_number`.

## Done when

- With two analyzed matches, there is one row per (match, round), carrying that match's own Ticks.
- For `aea4e59ccfc6c962` the output is unchanged: 15 rows, 34 Frags in total, round 12 = `4K`,
  Score 80.

## Answer

Fixed by `clipper/csdm_db.py`, which replaced `db/highlights.sql`: the per-round facts group and
join on `match_checksum` as well as on the round number and SteamID.
`tests/test_csdm_db.py::test_round_facts_stay_within_one_match` clones the match under a second
checksum and checks both keep exactly their own facts.
