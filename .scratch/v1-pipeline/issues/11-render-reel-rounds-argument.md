# 11 — Leaving out the rounds argument corrupts the csdm call

Status: ready-for-agent
Type: task

## Problem

`scripts/render_reel.sh` documents `[rounds]` as optional (default `all`), but line 37 runs
`shift 3 2>/dev/null || true`. With two arguments, `shift 3` fails and shifts nothing, so the Demo
path and perspective stay in `"$@"` and get appended to the `csdm video` arguments (line 103).

Reproduced: `render_reel.sh match.dem player` forwards `match.dem player` as extra csdm arguments.

## Fix

Shift only what is there: `shift $(( $# < 3 ? $# : 3 ))`.

## Done when

`render_reel.sh <demo> player` renders all rounds and passes no stray positional arguments to csdm.
