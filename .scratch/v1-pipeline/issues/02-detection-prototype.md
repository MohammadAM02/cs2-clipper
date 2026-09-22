# 02 — Highlight detection prototype

Status: wontfix
Type: prototype

## Superseded

Superseded by `docs/adr/0003-highlights-from-csdm-database.md`. CS:DM's analyzer already computes
Highlights: a fully analyzed FACEIT Demo produced 56 tables in Postgres, including a `clutches` table
(`round_number`, `tick`, `clutcher_steam_id`, `opponent_count`, `won`, `has_clutcher_survived`,
`clutcher_kill_count`), plus `kills` and `rounds` with start/end ticks.

Writing a `demoparser2` prototype would duplicate parsing that has already happened and add a second
parser to maintain. Detection is now a query, not a prototype. See issue 07.

## Answer

Not pursued — deliberately dropped rather than left undone.
