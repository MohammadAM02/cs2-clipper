# 03 — Scoring engine

Status: needs-info
Type: task
Blocked by: 02

## Question

What rules turn Highlights into a Score and reasons, and what does the user consider a "good clip"?

## Why it blocks

Selection is the product. Without a scoring vocabulary the orchestrator cannot choose what to render,
and CS:DM's built-in `--mode player` covers only kills/deaths/rounds — no clutch, ace, or context.

## Starting proposal

```
ace          = 100        4k = 70          3k = 40
clutch 1v3   =  60        clutch 1v4 = 80  clutch 1v5 = 100
collateral   =  55        knife kill = 30  match point = 20
```

Output per Highlight: `{ "score": 95, "reasons": ["1v4 clutch", "match point"] }`.

## Answer

_(blocked — needs the Highlight vocabulary from issue 02 to become concrete)_

## Comments

### 2026-09-22 — repo review

- The blocker, 02, is `wontfix`. The starting rule table already runs in `db/highlights.sql`
  (issue 07), so what's left here is choosing the rule values.
- For the rules: the analyzed match's top Highlight (round 12, `4K`, Score 80) comes from a round
  the subject's team **lost**. The 4th Frag left one opponent, who won the 1v2 and killed the
  subject. The Score ignores round outcome. Open question: should a lost round lower the Score, add
  a reason, or change nothing?
