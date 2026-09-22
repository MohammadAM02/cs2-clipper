# 10 — Reels come out of order and pick up old Clips

Status: ready-for-agent
Type: task

## Problem

`scripts/render_reel.sh` builds its FFmpeg concat list from `"$OUT_DIR"/sequence-*.mp4` (line 152).

1. **Order.** The shell sorts the names as text, so `sequence-10-…` lands before `sequence-2-…`
   (reproduced). Any Reel with 10 or more Sequences is out of Sequence order, which breaks the Reel
   definition in `CONTEXT.md`. Rendering every round of the analyzed match (34 subject Frags) would
   hit this.
2. **Stale Clips.** `OUT_DIR` is `E:/cs2clips/<perspective>`, shared by every run and never cleared,
   so the next Reel also includes whatever earlier runs left there — today, the three round-12 Clips
   in `E:\cs2clips\player` and `E:\cs2clips\enemy`.

## Fix

- Give each run its own folder (e.g. `<out>/<demo>/<perspective>/<run-id>/`), or concatenate only
  the files this run produced.
- Order by the Sequence number in the file name (numeric sort) or by start Tick.

## Done when

A run with 10+ Sequences yields a Reel in ascending start-Tick order that contains only that run's
Clips.
