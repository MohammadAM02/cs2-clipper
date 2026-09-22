# Adopt CS Demo Manager as the render engine

**Status**: accepted

Turning a Demo into Clips requires driving CS2 through HLAE and executing console commands at exact
Ticks — CS2 dropped `.vdm` scripting, so this is not a small piece of work. `akiver/cs-demo-manager`
does exactly this already: MIT licensed, 2,009 stars, v3.20.1, actively maintained, with a headless
CLI (`csdm video`), a render queue shared with its GUI, HLAE/FFmpeg integration, Sequences, and Demo
ingestion from Valve. We therefore build only the differentiated layer on top — automatic Highlight
detection, scoring, and hands-off orchestration — instead of reimplementing rendering.

## Considered Options

- **From-scratch HLAE driver** (this repo's `spike/`, a generated cfg driving `mirv_cmd`). Kept as a
  fallback and benchmark: it is one generated config file, and CS:DM may not expose something we
  eventually need.
- **Fork `Rovniy/cs2-highlights-maker`**. Rejected: 4 stars, created and last pushed on the same day,
  and **no license**, so there is no right to reuse its code. Read for ideas only.
- **Adopt CS:DM.** Chosen.

## Consequences

- Rendering depends on a third-party application we do not control. CS2 updates break HLAE injection
  until advancedfx ships a fix (usually days), so the pipeline needs a version/health check.
- `spike/` is not dead code: it is the fallback path and the measurement harness
  (`spike/ACCEPTANCE.md`) that we apply to CS:DM too.
- FACEIT demo ingestion is unavailable to us. FACEIT moved demo download behind a private,
  partner-only API — CS:DM was granted a partner key and still disabled the feature, because it
  requires a hosted backend. Ingestion is the Valve Game Coordinator, share codes, or a manual
  FACEIT drop.
- Rendering runs at roughly real time on this machine (≈10 clips × 20 s ≈ 10–15 min), so throughput,
  not correctness, is the scaling limit.
