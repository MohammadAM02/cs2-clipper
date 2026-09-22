# 07 — Highlight queries + scoring over CS:DM's database

Status: ready-for-agent
Type: task

## Question

What single module turns CS:DM's analyzed Postgres data into scored, renderable Highlights?

## Deliverable

One module (the only place that knows CS:DM's schema) exposing:

```
highlights_for_match(match_checksum) -> [{ type, score, reasons[], start_tick, end_tick }]
```

backed by SQL over CS:DM's tables, plus a scoring rule table.

## Data confirmed present (real match, 18 rounds, 34 subject frags)

- `kills` — `tick`, `round_number`, `killer_steam_id`, `victim_steam_id`, `assister_steam_id`,
  `is_headshot`, `is_killer_blinded`, `is_killer_airborne`, `penetrated_objects`
- `clutches` — `round_number`, `tick`, `clutcher_steam_id`, `opponent_count`, `won`,
  `has_clutcher_survived`, `clutcher_kill_count`
- `rounds` — `start_tick`, `end_tick`, `winner_name`, `winner_side`, `end_reason`,
  `team_a_score`/`team_b_score`, economy per team
- `matches`, `players`, `demos`, `demo_paths` for lookup

## Derivable Highlight types

| Highlight | Source |
| --- | --- |
| `ACE` / `4K` / `3K` | count of `kills` per (`round_number`, subject SteamID) |
| `CLUTCH_1vN` | `clutches` rows for the subject SteamID (`opponent_count` gives the N) |
| `HEADSHOT_ONLY_ROUND`, `BLIND_KILL` | `is_headshot`, `is_killer_blinded` |
| `MATCH_POINT` | `rounds.team_a_score`/`team_b_score` vs match format |
| Clip boundaries | `rounds.start_tick` → `rounds.end_tick`, or kill tick ± padding |

## Constraints

- Subject is identified by SteamID64 `76561198192858303` (from `scripts/faceit_probe.py`).
- Emit Ticks, never seconds; Tickrate is 64 ticks per second.
- No `demoparser2` (see ADR 0003). No second parser.
- Must not need CS2 or CS:DM running — only the database.
- Schema access isolated in this module; nothing else in the codebase writes SQL against CS:DM's tables.

## Must validate before trusting `clutches`

Real observed rows from this match:

```
round 1  | DM708         vs 4 | won=false | survived=false | kills=0 | tick=11029
round 5  | Soldier       vs 2 | won=false | survived=true  | kills=0 | tick=36409
round 12 | Cheeniabbu    vs 2 | won=true  | survived=true  | kills=2 | tick=78361
```

17 rows in one match, mostly `clutcher_kill_count = 0`, some `won=false` while
`has_clutcher_survived=true`, and rows exist for the losing side. The structure is certain; the
semantics are not. Validate against rounds you can verify by eye before scoring uses it — or derive
clutches from `kills` + `rounds` instead.

## Answer

_(unresolved)_

## Comments

### 2026-09-22 — repo review

**`clutches` semantics, partly validated** against the kill feed of `aea4e59ccfc6c962`:

- Each row is a clutch *situation* (one player left against N opponents) for either team,
  whatever the outcome. `won` is whether the clutcher's team won the round.
- Round 12 matches exactly: the subject's 4th Frag (Tick 78361) left Cheeniabbu alone against 2,
  the subject and a bot. Cheeniabbu killed both (Ticks 80761, 81477), and team_Cheeniabbu won the
  round: `won = t`, `has_clutcher_survived = t`, `clutcher_kill_count = 2`.
- Every odd `won = f, has_clutcher_survived = t` row (rounds 5 and 12) belongs to the bot "Soldier"
  (`clutcher_steam_id = '0'`). When a human controls a bot and dies, the Kill records the human as
  victim (`is_victim_controlling_bot = t`), so CS:DM never sees the bot die.
- So a subject Clutch is `clutcher_steam_id = <subject> AND won AND opponent_count >= 2`. A clutch
  the subject plays while controlling a bot would be recorded under the bot's `'0'` and missed.
- The subject has no clutch situations in this match, so this Demo can't test clutch scoring end
  to end.

**Related tickets:** 08 (rounds mixed across matches) and 09 (knife bonus never fires) are bugs in
`db/highlights.sql`; 13 covers the 7 of 34 subject Frags made while controlling a bot.
