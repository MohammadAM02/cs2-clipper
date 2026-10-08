# Analyze with csda and render with HLAE, without CS Demo Manager

**Status**: accepted; supersedes 0001 and 0003

Since CS2's update of 19 August, CS Demo Manager's add-on no longer hooks into the game (upstream issue
#1458), so `csdm video` stalls. The app therefore drives CS2 through HLAE itself: `hlae_plan` ports CS:DM
3.20.1's Sequence builders and the commands its server plugin sent, and `hlae_render` runs them. That left
CS:DM doing analysis only: `csdm analyze` into a Postgres server that Setup had to install and start, read
back with SQL (ADR 0003). CS:DM's settings file also told the app where HLAE and FFmpeg were.

`csdm analyze` runs cs-demo-analyzer (csda, `akiver/cs-demo-analyzer`, MIT, by CS:DM's author) and loads its
output into Postgres. The app now runs csda itself, keeps the part of its JSON output that it reads as
`analyses\<checksum>.json` in its data folder, and reads that instead of the database. HLAE is the only
render engine.

## Considered Options

- **Keep CS:DM for analysis only.** Rejected: a whole desktop app and a Postgres server, the most fragile
  part of Setup, installed only to parse Demos, with CS:DM's schema still ours.
- **A Python parser (`demoparser2`).** Rejected for ADR 0003's reasons: a second parser to keep current with
  CS2, whose facts would all need validating again.
- **csda itself.** Chosen. Its facts are the ones CS:DM stored: for all 15 Demos the app had analyzed through
  CS:DM, the checksums, rounds, kills, round facts and match dates from csda equal what CS:DM's database held.

## Consequences

- `clipper/analysis.py` is the only code that reads csda's output, as one module read CS:DM's schema: a csda
  change is absorbed there. csda is pinned (v1.11.0), and Setup installs it, HLAE, and FFmpeg unless the PC
  has one on PATH, into the data folder's `tools\`.
- Each match is analyzed once. A Demo analyzed through CS:DM is analyzed again by csda when it next needs
  its facts.
- Setup no longer installs CS:DM, Postgres or a database, and the Gate no longer waits for CS:DM to be
  closed. The settings `csdm_home`, `csdm_app_dir`, `pg_bin`, `pg_data` and `renderer` are gone (old values
  are ignored); `hlae_exe` names the HLAE to use, and blank means the one Setup installs.
- A change to CS2's Demo format now waits on a csda release instead of a CS:DM one. A CS2 update still
  breaks HLAE until advancedfx ships a fix.
- The clutches are the rows csda gave CS:DM, so what scoring does with them is unchanged.
