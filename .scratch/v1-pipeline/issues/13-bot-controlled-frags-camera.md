# 13 — Check the camera for Frags made while controlling a bot

Status: ready-for-human
Type: research

## Question

When the subject is controlling a bot, does `csdm video --perspective player` show the bot's view,
or the subject's own slot?

## Why it matters

7 of the subject's 34 Frags in match `aea4e59ccfc6c962` have `is_killer_controlling_bot = true`:

| Round | Frags while controlling a bot |
| --- | --- |
| 4 | 3 — the whole 3K, one of six 3Ks tied for the second-highest Score |
| 6, 7, 10, 15 | 1 each |

`kills.killer_steam_id` holds the controlling human's SteamID, so the Highlight query already
counts these as the subject's Frags. The camera is a separate question: the bot occupies a
different player slot from the subject.

## Steps

1. `scripts/render_reel.sh <demo> player 4` — launches CS2, so only when the machine is free.
2. Watch the round-4 Clips: is the camera on the bot doing the killing?
3. Repeat with `enemy` for the victims' view.

Needs a human: it launches CS2 and someone has to watch the result.
