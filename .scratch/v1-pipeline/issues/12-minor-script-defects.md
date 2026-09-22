# 12 — Minor script defects

Status: ready-for-agent
Type: task

Two low-impact defects found in the 2026-09-22 review.

## `render_reel.sh` builds a broken PATH entry

Line 62 prepends `$LOCALAPPDATA/pg17/pgsql/bin` to `PATH`. In Git Bash `$LOCALAPPDATA` is
`C:\Users\AMG\AppData\Local`, so the entry splits at the drive colon into `C` and
`\Users\AMG\AppData\Local/pg17/pgsql/bin`. Whether csdm still finds `psql` depends on how MSYS
converts those pieces (unverified). Low impact today: CS:DM shells out to `psql` only to create the
`csdm` database, which already exists.

Fix: convert first, e.g. `export PATH="$(cygpath -u "$LOCALAPPDATA")/pg17/pgsql/bin:$PATH"`.

## `faceit_probe.py --player-id` always sends the rejected auth scheme

`_AUTH` starts as `raw` (line 45) and switches to `bearer` only inside step 1. `--player-id` skips
step 1, so every later call sends the raw key, which FACEIT rejects with `403 err_f0` (issue 05).
`--auth-scheme` is ignored on that path too.

Fix: default to `bearer`, the scheme issue 05 found works, and honour `--auth-scheme` when step 1
is skipped.
