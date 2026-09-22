# Phase 0 — Render Spike (fallback path)

> **Superseded as the primary path.** CS Demo Manager now provides the render engine — see
> `docs/adr/0001-adopt-cs-demo-manager-as-render-engine.md` and `.scratch/v1-pipeline/spec.md`.
> This spike is kept as the fallback if `csdm video` cannot do something we need, and its
> `ACCEPTANCE.md` criteria remain the yardstick for measuring the CS:DM path.

**Goal:** prove a CS2 Demo can be turned into a video Clip with no human input after launch.

This is the project's riskiest assumption. Phases 1–2 (detection, scoring) are commodity work and stay
out of scope here. Nothing else gets built until this spike passes or fails on the record.

## Known-good hardware/software (verified 2026-09-22)

| Component | State |
| --- | --- |
| CS2 | installed — `G:\SteamLibrary\...\game\bin\win64\cs2.exe` |
| HLAE 2.192.2 (AfxHookSource2 0.41.2) | `C:\HLAE` (portable zip), hook DLL `x64\AfxHookSource2.dll` |
| FFmpeg | wired to HLAE via `C:\HLAE\ffmpeg\ffmpeg.ini` → system FFmpeg 8.1.2 |
| CS2 cfg dir | `G:\SteamLibrary\...\game\csgo\cfg` |
| Demo | **absent** — must be downloaded from CS2 match history or FACEIT/HLTV |

## Procedure

```bash
# 1. put a Demo here (any recent match; no replays folder exists yet)
spike/demos/spike_demo.dem

# 2. optionally edit the Clip boundaries in the Render Plan
spike/render_plan.example.json

# 3. generate the CS2 recording config
python spike/scripts/gen_spike_cfg.py spike/render_plan.example.json --out-dir spike/cfg

# 4. launch unattended; stages the Demo + cfg, runs CS2 through HLAE, waits for the marker
spike\launch_spike.cmd
```

`launch_spike.cmd` exits `0` only when it sees `SPIKE_DONE` in
`game\csgo\console.log`, after finding each `CLIP_DONE <name>` marker.

## How the automation works

```
HLAE.exe -customLoader -noGui -autoStart -hookDllPath ...AfxHookSource2.dll
         -programPath ...cs2.exe -cmdLine "-steam -insecure -console -condebug +exec spike_autoexec"
   └─ CS2 starts, executes spike_autoexec.cfg
        ├─ engine_no_focus_sleep 0 / snd_mute_losefocus 0   (survive losing focus)
        ├─ mirv_streams record setup + afxFfmpegYuv420p preset
        ├─ mirv_cmd addAtTick <start> "… record start"       (per-Clip boundaries)
        ├─ mirv_cmd addAtTick <end>   "… record end; echo CLIP_DONE <name>"
        └─ playdemo spike_demo
```

`mirv_cmd` is the load-bearing feature: it runs console commands at an exact Demo Tick, so the whole
Clip schedule is declared up front and the render needs no further input.

## Must answer before Phase 3 is considered de-risked

1. Does CS2 keep rendering correctly while the window is unfocused/minimised?
2. Does `mirv_streams record name` set a **directory** or a **filename stem**? Nested quoting inside
   `mirv_cmd` is unverified — hence the no-spaces constraint on the output path.
3. Does repeated `record start` / `record end` inside one Demo pass produce **separate files per Clip**,
   or does it overwrite/append one file?
4. Wall-clock cost: how many seconds to render 10 s of output at 300 fps input, and how large is the file?
5. Do scheduled ticks drift (i.e. is the Clip boundary accurate to ±1 tick)?
6. Repeatability: does a second run produce an identically-sized/lengthed Clip?

Record answers in `ACCEPTANCE.md`.

## Safety

HLAE injects a hooking DLL and is launched with `-insecure`. **Never join a VAC-secured server with a
hooked CS2.** These configs are for offline Demo playback only.

## Fallback ladder

1. Single-pass, one launch per Demo (default) — fastest.
2. Per-clip configs (`--strategy per-clip`) — one launch per Clip; use if boundaries drift or files collide.
3. Drive the CS2 console externally (input injection) between manual `playdemo` runs — most control,
   most fragility.
