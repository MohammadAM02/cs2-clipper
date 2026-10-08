# 01 — Check the camera for Frags made while controlling a bot

Status: ready-for-human
Type: research

## Question

When the subject is controlling a bot, does the Clip show the bot's view, or the subject's own slot?

## Why it matters

7 of the subject's 34 Frags in match `aea4e59ccfc6c962` were made while controlling a bot:

| Round | Frags while controlling a bot |
| --- | --- |
| 4 | 3 — the whole 3K |
| 6, 7, 10, 15 | 1 each |

csda credits these Frags to the controlling human's SteamID, so scoring counts them as the subject's
Frags. The camera is a separate question: `hlae_plan` aims it at a SteamID's player slot
(`spec_player`), and the bot occupies a different slot from the subject.

## Steps

1. Render round 4 of that match from the player's view. It launches CS2, so only when the PC is free.
2. Watch the round-4 Clips: is the camera on the bot doing the killing?
3. Repeat from the enemy view for the victims.

Needs a human: it launches CS2 and someone has to watch the result.
