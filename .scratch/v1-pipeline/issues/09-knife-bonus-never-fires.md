# 09 — Knife bonus never fires

Status: resolved
Type: task

## Problem

`db/highlights.sql` line 23 finds knife Frags with `k.weapon_type ILIKE '%knife%'`, but CS:DM never
writes such a value. Its `WeaponType` enum (read from the installed 3.20.1 bundle,
`resources/app.asar`) is `unknown`, `pistol`, `smg`, `shotgun`, `rifle`, `sniper`, `machine_gun`,
`grenade`, `equipment`, `melee`, `world`. Knives are `melee`, so the +30 bonus and the `knife kill`
reason can never appear.

The analyzed match has no knife Frags, which is why the current output doesn't show it.

## Fix

Test `k.weapon_type = 'melee'`.

## Done when

A knife Frag earns `knife kill` and +30. Needs a Demo with a knife Frag, or a fixture row.

## Answer

Fixed: `clipper/csdm_db.py` counts knife Frags with `weapon_type = 'melee'`, and
`clipper/scoring.py` gives them the +30 bonus
(`tests/test_scoring.py::test_a_knife_frag_earns_thirty`).
