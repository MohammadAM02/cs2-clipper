#!/usr/bin/env bash
# Render one Reel from an already-analyzed Demo via CS:DM's headless CLI.
#
#   scripts/render_reel.sh <demo.dem> <player|enemy> [rounds] [extra csdm args...]
#
# Examples:
#   scripts/render_reel.sh spike/demos/match.dem player 12
#   scripts/render_reel.sh spike/demos/match.dem enemy  12 18        # rounds 12 and 18
#   scripts/render_reel.sh spike/demos/match.dem player all
#
# Encodes every lesson from the phase-1 spike:
#   * refuses to start while cs2.exe is running  (never launch a second HLAE-hooked instance)
#   * USERPROFILE override   (CS:DM hardcodes homedir()/.csdm, and this profile root is read-only)
#   * psql on PATH           (CS:DM shells out to it)
#   * output dir must exist  (otherwise: "Output folder does not exist" + usage dump + exit 1)
#   * concatenation + name   (defaults give one file per Sequence instead of one Reel)
#   * 6s/4s padding          (default 2s/2s yields ~4-second Clips)

set -euo pipefail

# REPO must be a NATIVE path. CS:DM reads its app folder from homedir(), which on Windows is the raw
# USERPROFILE value: an MSYS path like /c/Users/... resolves to C:\c\Users\..., the settings file is
# never found, and every command fails with "password authentication failed for user postgres" because
# CS:DM falls back to its default database password.
if REPO_WIN="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -W 2>/dev/null)"; then
  REPO="$REPO_WIN"
else
  REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd | sed -E 's|^/([a-zA-Z])/|\1:/|')"
fi
CSDM_HOME="$REPO/home"
LOGDIR="$REPO/spike/out"
STEAMID="${SUBJECT_STEAMID64:-76561198192858303}"

DEMO="${1:-}"
PERSPECTIVE="${2:-}"
ROUNDS="${3:-all}"
shift 3 2>/dev/null || true

if [[ -z "$DEMO" || -z "$PERSPECTIVE" ]]; then
  sed -n '2,8p' "$0"
  exit 2
fi
if [[ "$PERSPECTIVE" != "player" && "$PERSPECTIVE" != "enemy" ]]; then
  echo "perspective must be 'player' or 'enemy' (got '$PERSPECTIVE')" >&2
  exit 2
fi
[[ -f "$DEMO" ]] || { echo "demo not found: $DEMO" >&2; exit 2; }

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) CSDM="$LOCALAPPDATA/Programs/cs-demo-manager/csdm.cmd" ;;
  *) CSDM="csdm" ;;
esac
[[ -x "$CSDM" || -f "$CSDM" ]] || { echo "csdm CLI not found at $CSDM" >&2; exit 2; }

# --- preflight ------------------------------------------------------------------------------------
if [[ "${DRY_RUN:-0}" != "1" ]] && tasklist 2>/dev/null | grep -qi '^cs2\.exe'; then
  echo "ABORT: cs2.exe is running. Close CS2 first — never launch a second HLAE-hooked instance." >&2
  exit 3
fi

export USERPROFILE="$CSDM_HOME"
export PATH="$LOCALAPPDATA/pg17/pgsql/bin:$PATH"
# PGPASSWORD is passed to psql inline and deliberately NOT exported into csdm's environment: the
# daemon inherits the CLI env, and runs where it was exported failed authentication intermittently.
# (Hypothesis, not proof — the same signature also appears when a daemon races on the database.)
PGPW="$(cat "$CSDM_HOME/.csdm/.pgpass.txt" 2>/dev/null || true)"

if ! PGPASSWORD="$PGPW" "$LOCALAPPDATA/pg17/pgsql/bin/psql.exe" -h 127.0.0.1 -U postgres -d csdm -tAc 'select 1' >/dev/null 2>&1; then
  echo "ABORT: PostgreSQL is not reachable. Start it with: python scripts/setup_local_postgres.py" >&2
  exit 3
fi

# CS:DM is a native Windows app: it must receive a native path (C:/...). An MSYS path (/c/...) is
# resolved to C:\c\... by Node's path.resolve and silently misses the analyzed Demo.
if DEMO_WIN="$(cd "$(dirname "$DEMO")" && pwd -W 2>/dev/null)"; then
  DEMO_ABS="$DEMO_WIN/$(basename "$DEMO")"
else
  DEMO_ABS="$(cd "$(dirname "$DEMO")" && pwd)/$(basename "$DEMO")"
  DEMO_ABS="$(printf '%s' "$DEMO_ABS" | sed -E 's|^/([a-zA-Z])/|\1:/|')"
fi

# One Reel per perspective, so both can coexist in the same folder.
OUT_DIR="${REEL_OUT_DIR:-E:/cs2clips}/$PERSPECTIVE"
REEL_NAME="$(basename "${DEMO_ABS%.dem}")_${PERSPECTIVE}_reel"
mkdir -p "$OUT_DIR"
mkdir -p "$LOGDIR"
LOG="$LOGDIR/reel_${PERSPECTIVE}_$(date '+%Y%m%d_%H%M%S').log"

# Aspect ratio. "4:3-stretched" renders 4:3 then bakes the horizontal stretch into 16:9 in the reel
# step, which is what CS players mean by 4:3; plain "4:3" stays a true 4:3 video.
# REEL_EVENT selects what a Sequence covers: kills = per-frag clips, rounds = whole rounds.
RATIO="${REEL_RATIO:-16:9}"
EVENT="${REEL_EVENT:-kills}"
case "$RATIO" in
  16:9)          WIDTH=1920; HEIGHT=1080; STRETCH=0 ;;
  4:3)           WIDTH=1280; HEIGHT=960;  STRETCH=0 ;;   # the classic 4:3 res
  4:3-hd)        WIDTH=1440; HEIGHT=1080; STRETCH=0 ;;
  4:3-stretched) WIDTH=1280; HEIGHT=960;  STRETCH=1 ;;   # 4:3 geometry baked out to 16:9
  *) echo "REEL_RATIO must be 16:9, 4:3, 4:3-hd or 4:3-stretched (got '$RATIO')" >&2; exit 2 ;;
esac

# Clip padding stays at CS:DM's default 2s/2s, and the Reel is assembled by us with FFmpeg below.
# (An earlier note here blamed CS:DM's --concatenate-sequences/--output-file-name/padding flags for
# breaking the run. That was wrong: those runs were all script runs, and the real culprit was the MSYS
# USERPROFILE fixed above.)
ARGS=(
  video "$DEMO_ABS"
  --mode player
  --steamids "$STEAMID"
  --event "$EVENT"
  --perspective "$PERSPECTIVE"
  --width "$WIDTH"
  --height "$HEIGHT"
  --output "$OUT_DIR"
  --close-game-after-recording
)
[[ "$ROUNDS" != "all" ]] && ARGS+=(--rounds "$ROUNDS")
ARGS+=("$@")

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  echo "DRY RUN — would execute:"
  printf '  %s' "$CSDM"
  printf ' %q' "${ARGS[@]}"
  printf '\n'
  [[ "$STRETCH" == "1" ]] && echo "  reel step: scale ${WIDTH}x${HEIGHT} -> 1920x1080 (baked stretch)"
  exit 0
fi

echo "reel    : $REEL_NAME"
echo "demo    : $DEMO_ABS"
echo "subject : $STEAMID"
echo "rounds  : $ROUNDS"
echo "out     : $OUT_DIR"
echo "log     : $LOG"
echo "started : $(date '+%H:%M:%S')"

# The CLI returns exit 0 even when it fails, so success is judged from the log. Observed failures that
# exit 0: "password authentication failed for user" (a foreign daemon carrying default DB settings)
# and "Game error" (the game process died mid-render).
looks_failed() {
  grep -qiE 'password authentication failed|ECONNREFUSED|Game error|Invalid argument|does not exist' "$1" 2>/dev/null
}

run_once() {
  "$CSDM" "${ARGS[@]}" >"$LOG" 2>&1 || true
}

run_once

if looks_failed "$LOG"; then
  echo "attempt 1 failed:"
  head -3 "$LOG" | sed 's/^/    /'
  # A daemon started outside our USERPROFILE resolves the app folder to the real home, finds no
  # settings there, and connects with CS:DM's default password. Clear it so a fresh one spawns here.
  echo "-- clearing any stale CS:DM daemon and retrying --"
  MSYS2_ARG_CONV_EXCL='*' cmd.exe /c 'taskkill /IM cs-demo-manager.exe /F' >/dev/null 2>&1 || true
  sleep 3
  run_once
fi

if looks_failed "$LOG"; then
  echo "FAILED at $(date '+%H:%M:%S') — see $LOG" >&2
  tail -12 "$LOG" >&2
  exit 1
fi

echo "OK at $(date '+%H:%M:%S')"
tail -6 "$LOG"

# Assemble the reel ourselves, because CS:DM's own concatenation flags break the render (see ARGS).
REEL_PATH="$OUT_DIR/$REEL_NAME.mp4"
# The list file must live at a native path too: ffmpeg is a native binary and cannot open /tmp/...
LIST="$OUT_DIR/.concat_$$.txt"
: > "$LIST"
shopt -s nullglob
for f in "$OUT_DIR"/sequence-*.mp4; do
  printf "file '%s'\n" "$(printf '%s' "$f" | sed -E 's|^/([a-zA-Z])/|\1:/|')" >> "$LIST"
done
shopt -u nullglob

if [[ -s "$LIST" ]]; then
  # A baked 4:3 stretch needs a real re-encode; everything else is a stream copy.
  if [[ "$STRETCH" == "1" ]]; then
    REEL_FFMPEG=(-vf "scale=1920:1080,setsar=1" -c:v libx264 -crf 23 -preset medium -c:a copy)
  else
    REEL_FFMPEG=(-c copy)
  fi
  if ffmpeg -y -loglevel error -f concat -safe 0 -i "$LIST" "${REEL_FFMPEG[@]}" "$REEL_PATH"; then
    echo "reel    : $REEL_PATH"
    ffprobe -v error -show_entries format=duration,size -of default=noprint_wrappers=1 "$REEL_PATH" 2>/dev/null | sed 's/^/          /'
  else
    echo "reel assembly failed — the per-Sequence Clips are still in $OUT_DIR" >&2
  fi
else
  echo "no sequence-*.mp4 in $OUT_DIR" >&2
fi
rm -f "$LIST"
