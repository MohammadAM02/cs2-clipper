# 14 — Bring docs and ticket statuses up to date

Status: ready-for-agent
Type: task

Stale statements found in the 2026-09-22 review. Each is a correction, not a new decision, except
the one marked **needs a decision**.

## `.scratch/v1-pipeline/spec.md`

- Still says FACEIT yields `.gz` and the pipeline gunzips (lines 34–35, 55–56, 68). Issue 05 and
  ADR 0002 found `.zst`. Add `*.dem.zst` to `.gitignore` too.
- Phase 2 still names `demoparser2` (line 71), superseded by ADR 0003. Nothing in the repo imports
  the `.venv`'s demoparser2, pandas, polars or pyarrow.
- "Stack: Fastify + SQLite + in-process job queue. Postgres/Redis only if concurrency demands it"
  (line 18). **Decided 2026-09-22** in `.scratch/orchestrator/spec.md`: Python 3.13 (uv), our index
  in SQLite, CS:DM keeps its own Postgres, no Fastify. Update the line; the Safety line also gains
  the FACEIT AC rule from that spec.

## Tickets

- **01** is still `ready-for-human` with no answer, though `spike/ACCEPTANCE.md` records A1–A7
  passing (see the comment on 01 for A8). It also mentions an "SQLite database location"; CS:DM
  uses Postgres.
- **06** is still `ready-for-human`, but `scripts/setup_local_postgres.py` provisioned Postgres as a
  portable cluster in `%LOCALAPPDATA%\pg17`, not `C:\Program Files\PostgreSQL\17\bin`.
- **03** and **04** are blocked by **02**, which is `wontfix`. 04's shape still says "poll Valve GC"
  (ruled out by ADR 0002) and `--configFile` (the verified preset uses `--rounds`).

## Other files

- ADR 0001, Consequences: ingestion via "the Valve Game Coordinator, share codes" is superseded by
  ADR 0002, and the "≈10–15 min" render estimate predates the measured ≈5 min in `ACCEPTANCE.md`.
  Add an update note, as ADR 0002 did, rather than rewriting it.
- `scripts/render_reel.sh`: header lines 16–17 (6s/4s padding, concatenation flags) and line 146
  contradict the corrected note at lines 89–92.
- `spike/README.md` says no Demo exists; `spike/launch_spike.cmd` expects `spike_demo.dem`, but the
  Demo is `1-2b882547-…-1-1.dem`.
- `spike/ACCEPTANCE.md`: findings skip number 9, and the run log lists 2 runs while the findings
  describe more.
