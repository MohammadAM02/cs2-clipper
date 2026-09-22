# Derive Highlights from CS:DM's analysis instead of a second parser

**Status**: accepted

CS:DM's analyzer already persists everything a Highlight needs. Its Postgres database (56 tables on a
fully analyzed match) contains a `clutches` table with `round_number`, `tick`, `clutcher_name`,
`clutcher_steam_id`, `won`, `side`, `opponent_count`, `has_clutcher_survived` and
`clutcher_kill_count`; a `kills` table with `tick`, `round_number`, killer/victim/assister Steam IDs,
`is_headshot`, `is_killer_blinded`, `is_killer_airborne` and `penetrated_objects`; and a `rounds` table
with `start_tick`, `end_tick`, `winner_name`, `end_reason` and per-team economy. Highlight detection is
therefore **SQL over CS:DM's database**, and our code starts at scoring.

## Considered Options

- **A second parser with `demoparser2`** (the original plan for issue 02). Rejected: it duplicates
  parsing that has already happened, adds a second parser to keep current with CS2 updates, and
  produces nothing that scoring needs which the database lacks.
- **SQL over CS:DM's database.** Chosen.

## Consequences

- No Python demo-parsing dependency on the critical path, and no second place for CS2 format changes
  to break us.
- **Our schema is CS:DM's schema.** A CS:DM upgrade can rename or reshape those tables, so every query
  belongs behind one narrow module rather than scattered through the orchestrator. That module is the
  only place a schema change needs to be absorbed.
- Anything CS:DM does not persist is either derived from what is there or is a deliberate no for v1.
- **`clutches` semantics are unvalidated.** On a real match it contains 17 rows, most with
  `clutcher_kill_count = 0` and some with `won = false, has_clutcher_survived = true`, and it records
  situations for the losing side too. Structure is confirmed; meaning is not. Do not let scoring depend
  on it until a validation task has checked it against known rounds.
- `.scratch/v1-pipeline/issues/02-detection-prototype.md` is superseded by this decision.
