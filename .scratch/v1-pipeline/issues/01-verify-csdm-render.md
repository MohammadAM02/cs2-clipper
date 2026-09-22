# 01 — Verify CS:DM renders a Clip on a real Demo

Status: ready-for-human
Type: task

## Question

Does the installed CS Demo Manager produce a watchable Clip from a real Demo on this machine, and at
what throughput?

## Why it blocks

Everything else in the pipeline feeds `csdm video`. If it cannot render on this machine, we fall back
to `spike/launch_spike.cmd`.

## Verified already

- CS:DM 3.20.1 installed at `%LOCALAPPDATA%\Programs\cs-demo-manager` (installer exit 0,
  sha256 `eedda43b67bb079be4ffdf3e5fc3b114d07163bf2aa13eeed3d25d1c3563de3a`).
- `csdm help`, `csdm video`, `csdm analyze` all run **headlessly** with no GUI and leave no daemon
  running.
- CLI shape: `csdm analyze <demos...>` then `csdm video <demoPath> <startTick> <endTick> [options]`,
  with `--configFile <path>` accepting a JSON plan.

## Steps

1. Obtain a Demo (Valve match history / share code, or manual FACEIT matchroom download).
   No `.dem` exists on this machine yet.
2. `csdm analyze <demo>` — required first: the CLI rejects demos not in the database.
3. Configure HLAE for CS:DM: it defaults to `<appFolder>/hlae/hlae.exe`, overridable via settings
   `video.hlae.customLocationEnabled` + `customExecutableLocation`. We already have HLAE 2.192.2 at
   `C:\HLAE`.
4. `csdm video <demo> <startTick> <endTick> --output <dir> --concatenate-sequences`
5. Record measurements in `spike/ACCEPTANCE.md` (criteria unchanged).

## Open details to capture while doing this

- The exact app folder / SQLite database location CS:DM uses.
- Whether `--configFile` supports multiple Sequences per Demo in one invocation.
- Whether per-Clip output filenames are controllable (matters for indexing Clips later).

## Answer

_(unresolved)_

## Comments

### 2026-09-22 — repo review

`spike/ACCEPTANCE.md` already records A1–A7 passing on the real FACEIT Demo, so this ticket's
question looks answered; only A8 (repeatability) is open there.

Evidence for A8 is already on disk — two separate player-POV renders of the same three round-12
Sequences:

| Sequence | `E:\cs2clips` (14:27) | `E:\cs2clips\player` (14:54) |
| --- | --- | --- |
| 1 (74007→74263) | 236 frames, 3.945 s, 3.04 MB | 238 frames, 3.971 s, 3.07 MB |
| 2 (76071→76559) | 457 frames, 7.628 s, 10.25 MB | 456 frames, 7.602 s, 10.28 MB |
| 3 (78233→78489) | 239 frames, 3.997 s, 4.36 MB | 240 frames, 4.000 s, 4.49 MB |

Lengths agree within 2 frames (≈33 ms), well inside A5's ±0.5 s; sizes differ by 0.3–2.8%. The runs
are consistent but not identical — whether that passes A8 is the user's call.
