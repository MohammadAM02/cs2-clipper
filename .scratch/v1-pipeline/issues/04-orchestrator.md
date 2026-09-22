# 04 — Orchestrator: demo arrives → clips appear

Status: needs-info
Type: task
Blocked by: 01, 02, 03

## Question

What turns the pipeline hands-off — the thing CS:DM deliberately does not do?

## Shape

```
watch demo folder / poll Valve GC
  → csdm analyze <demo>
  → detection (issue 02) → scoring (issue 03) → top-N Sequences
  → write Render Plan JSON → csdm video --configFile <plan>
  → index Clips in SQLite, expose them for download
```

## Decisions still needed

- How many Clips per Demo, and is there a Score threshold instead of a fixed count?
- Where Clips are published/stored (E: has the most free space; G: is down to ~26 GB).
- Whether the pipeline may launch CS2 unattended while the user is gaming (it cannot — it needs the
  game window; so the queue must be idle-aware).

## Answer

_(blocked)_

## Comments

### 2026-09-22 — repo review

**Decision needed: who picks the Sequences?**

- The spec's pipeline and issue 07's deliverable have us emit Sequences (`start_tick`, `end_tick`).
- The render preset that has actually produced Clips passes only `--rounds`. CS:DM then picks the
  Sequences itself: each Kill ±128 Ticks (±2 s), merging Kills that are close together, so round
  12's 4 Frags became 3 Sequences.
- The explicit-Tick form (`csdm video <demo> <start> <end>`) hasn't produced a Clip yet. Run 1
  stalled, possibly the intermittent stall in `spike/ACCEPTANCE.md` finding 5.

So today scoring can only choose rounds, and the Ticks the query emits go unused. The answer sets
the output shape for issue 07.

**A running CS:DM GUI will probably break headless renders.** During the review, a GUI started from
Explorer (21:24) was running with no window:

- It runs with the real profile, whose app folder `C:\Users\AMG\.csdm` doesn't exist and can't be
  created on this machine (see the app-folder note in `ACCEPTANCE.md`), so it can't have loaded our
  settings or database password.
- Its background server (`server.js`) listens on port 4574, the same port `csdm video` opens to talk
  to the CS2 plugin (`home/.csdm/logs/csdm.log`: `WS:: server listening on port 4574`).

`render_reel.sh` already treats a foreign CS:DM instance as a cause of "password authentication
failed" and kills every `cs-demo-manager.exe` on retry, which closes the user's GUI. The orchestrator
should detect a running GUI and hold renders until it closes. If the GUI is to be used at all, it
needs a launcher that sets `USERPROFILE=<repo>/home`. Renders started from the GUI can't take our
CS2 lock, so the orchestrator can only detect them via `cs2.exe`.

Also: the shape above still says "poll Valve GC", ruled out by ADR 0002.

### 2026-09-22 — design

Answered by `.scratch/orchestrator/spec.md` (design approved, write-up awaiting review):

- **Clips per Demo:** the top 5 Highlights by Score, each in both Perspectives, joined into one Reel
  per Highlight per Perspective.
- **Storage:** everything under `E:\cs2clips\`; only `library\` is ever shared.
- **When CS2 may launch:** as soon as the Gate is clear — CS2 closed, FACEIT AC not running, CS:DM
  GUI closed, disk space free, no other render running.
- **Who picks the Sequences:** CS:DM, via `--rounds`.
- **Intake:** the Downloads folder is watched; no Valve GC.
