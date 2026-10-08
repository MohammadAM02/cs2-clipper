# Analyze Demos with csda, record Clips through HLAE

**Status**: accepted

Turning a Demo into Reels takes two things: the facts of the match (rounds, Frags, clutches, players,
tick rate) and a way to make CS2 play the Demo and record exact tick ranges. CS2 has no `.vdm`
scripting, so recording means driving the game through HLAE and running console commands at exact
Ticks.

**Analysis.** The app runs cs-demo-analyzer (csda, `akiver/cs-demo-analyzer`, MIT), keeps the part of
its JSON output that it reads as `analyses\<checksum>.json` in its data folder, and reads that copy.

**Recording.** The app starts CS2 through HLAE.exe with `+exec cs2clipper`, and HLAE's
`mirv_cmd addAtTick` runs a console script at the right ticks: `hlae_plan` writes it and `hlae_render`
runs it. The Sequence builders and the commands the script sends are ported from CS Demo Manager
3.20.1 (MIT), so a request gives the same Sequences, Clip names and look.

## Considered Options

- **A Python parser (`demoparser2`).** Rejected: a second parser to keep current with CS2, whose facts
  would all need validating.
- **csda.** Chosen: one pinned binary with JSON output and no database. Its checksums, rounds, kills,
  round facts and match dates were checked on 15 real Demos.

## Consequences

- `clipper/analysis.py` is the only code that reads csda's output: a csda change is absorbed there.
  csda is pinned (v1.11.0), and Setup installs it, HLAE, and FFmpeg unless the PC has one on PATH,
  into the data folder's `tools\`.
- Each match is analyzed once; scoring and every render read the kept copy.
- No database server: the app's own index is SQLite.
- `hlae_exe` names the HLAE to use, and blank means the one Setup installs.
- A change to CS2's Demo format waits on a csda release. A CS2 update breaks HLAE until advancedfx
  ships a fix.
