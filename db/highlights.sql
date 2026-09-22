-- Highlight candidates for one subject player, derived from CS:DM's analyzed data.
--
-- This is the ONLY place that knows CS:DM's schema (see docs/adr/0003). If CS:DM renames a table or
-- column, this file is what changes; nothing else in the codebase writes SQL against these tables.
--
-- Usage:
--   psql -h 127.0.0.1 -U postgres -d csdm -v steamid=76561198192858303 -f db/highlights.sql
--   psql ... -v steamid=... -v checksum=<match_checksum> -f db/highlights.sql   (single match)
--
-- Output: one row per round in which the subject got a Frag, scored and explained.

\if :{?checksum}
\set match_filter ' AND k.match_checksum = :''checksum'''
\else
\set match_filter ''
\endif

WITH frags AS (
  SELECT k.round_number,
         count(*)                                    AS frags,
         count(*) FILTER (WHERE k.is_headshot)       AS headshots,
         count(*) FILTER (WHERE k.is_killer_blinded) AS blind_kills,
         bool_or(k.weapon_type ILIKE '%knife%')      AS has_knife,
         bool_or(k.is_no_scope)                      AS has_noscope,
         bool_or(k.is_through_smoke)                 AS has_smoke_kill,
         min(k.tick)                                 AS first_kill_tick,
         max(k.tick)                                 AS last_kill_tick
  FROM kills k
  WHERE k.killer_steam_id = :'steamid'
    :match_filter
  GROUP BY k.round_number
)
SELECT f.round_number AS round,
       f.first_kill_tick,
       f.last_kill_tick,
       r.start_tick,
       r.end_tick,
       f.frags,
       CASE f.frags
         WHEN 5 THEN 'ACE'
         WHEN 4 THEN '4K'
         WHEN 3 THEN '3K'
         WHEN 2 THEN '2K'
         ELSE 'FRAG'
       END AS type,
       ( CASE f.frags WHEN 5 THEN 100 WHEN 4 THEN 70 WHEN 3 THEN 40 WHEN 2 THEN 15 ELSE 5 END
       + CASE WHEN f.frags >= 2 AND f.headshots  = f.frags THEN 10 ELSE 0 END
       + CASE WHEN f.has_knife                          THEN 30 ELSE 0 END
       + CASE WHEN f.has_noscope     AND f.frags >= 2   THEN 10 ELSE 0 END
       + CASE WHEN f.has_smoke_kill  AND f.frags >= 2   THEN  5 ELSE 0 END
       + CASE WHEN f.blind_kills > 0 AND f.frags >= 2   THEN 10 ELSE 0 END
       ) AS score,
       array_remove(ARRAY[
         CASE WHEN f.frags = 5 THEN 'ace' END,
         CASE WHEN f.frags = 4 THEN '4k' END,
         CASE WHEN f.frags = 3 THEN '3k' END,
         CASE WHEN f.frags = 2 THEN '2k' END,
         CASE WHEN f.frags >= 2 AND f.headshots = f.frags THEN 'all headshots' END,
         CASE WHEN f.has_knife THEN 'knife kill' END,
         CASE WHEN f.has_noscope AND f.frags >= 2 THEN 'no-scope' END,
         CASE WHEN f.has_smoke_kill AND f.frags >= 2 THEN 'through smoke' END,
         CASE WHEN f.blind_kills > 0 AND f.frags >= 2 THEN 'blind kill' END
       ], NULL) AS reasons
FROM frags f
JOIN rounds r ON r.number = f.round_number
ORDER BY score DESC, f.round_number;

-- Not yet covered: MATCH_POINT (needs the match format / score state at round start) and
-- CLUTCH (CS:DM's `clutches` table exists but its semantics are unvalidated — see issue 07).
