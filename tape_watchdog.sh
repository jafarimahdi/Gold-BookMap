#!/usr/bin/env bash
# tape_watchdog.sh -- "Bookmap is open... but is the TAPE still writing?"
# =====================================================================
# The 2026-10-07 mystery: ticks.csv stopped growing at 21:59 while Bookmap
# stayed open ("alive and blind"). This watcher shouts the moment the tape
# freezes, so the blind window becomes seconds instead of 1.6 hours.
#
# READ-ONLY. It never touches the tape; it only looks at file size + mtime.
#
# Usage (git bash, second terminal, before the session):
#
#     bash tape_watchdog.sh                 # finds ./ticks.csv or ./data/ticks.csv
#     bash tape_watchdog.sh path/to/ticks.csv
#
# Tunables (environment variables):
#
#     FROZEN_AFTER=120   seconds without growth before the shout (default 120)
#     CHECK_EVERY=30     seconds between looks (default 30)
#     HEARTBEAT_EVERY=20 checks between quiet "still watching" lines (0 = never)
#     MAX_CHECKS=0       stop after N checks (0 = run forever; used by tests)
#     TAPE_WATCH_LOG=data/tape_watchdog.log
#
# What it prints (state changes only):
#   RED   === TAPE FROZEN: ...   -> remove + re-attach the Bookmap add-on
#                                 (Bookmap stays open). If it dies AGAIN near
#                                 22:00 -> schedule/subscription cut, not the add-on.
#   GREEN === TAPE FLOWING again -> the add-on re-attach worked (or it was a pause).
#   Rotation is NOT a freeze: the size dropping to a fresh file counts as flow.

FILE="${1:-}"
if [ -z "$FILE" ]; then
  if [ -f ticks.csv ]; then FILE="ticks.csv"
  elif [ -f data/ticks.csv ]; then FILE="data/ticks.csv"
  else FILE="ticks.csv"
  fi
fi

FROZEN_AFTER="${FROZEN_AFTER:-120}"
CHECK_EVERY="${CHECK_EVERY:-30}"
HEARTBEAT_EVERY="${HEARTBEAT_EVERY:-20}"
MAX_CHECKS="${MAX_CHECKS:-0}"
LOG="${TAPE_WATCH_LOG:-data/tape_watchdog.log}"
mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

stamp() { date '+%Y-%m-%d %H:%M:%S'; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
dim()   { printf '\033[90m%s\033[0m\n' "$*"; }
logline() { printf '%s %s\n' "$(stamp)" "$*" >> "$LOG" 2>/dev/null || true; }

echo "tape_watchdog: watching $FILE  (shout after ${FROZEN_AFTER}s without growth, look every ${CHECK_EVERY}s)"
if [ ! -f "$FILE" ]; then
  red "tape_watchdog: $FILE does not exist yet - pass the right path:"
  red "  bash tape_watchdog.sh path/to/ticks.csv     (find it first: ls -la ticks.csv data/ticks.csv)"
fi
logline "START file=$FILE frozen_after=${FROZEN_AFTER}s every=${CHECK_EVERY}s"

frozen=0
missing=0
last_size=""
checks=0

while :; do
  checks=$((checks + 1))
  if [ ! -f "$FILE" ]; then
    if [ "$missing" != 1 ]; then
      red "TAPE FILE MISSING: $FILE  (Bookmap not recording? wrong path?)"
      logline "MISSING file=$FILE"
      missing=1
    fi
  else
    if [ "$missing" = 1 ]; then
      green "TAPE FILE BACK: $FILE"
      logline "FILE_BACK file=$FILE"
      missing=0
      last_size=""
    fi
    size=$(stat -c %s "$FILE" 2>/dev/null || echo "?")
    mtime=$(stat -c %Y "$FILE" 2>/dev/null || echo 0)
    now=$(date +%s)
    age=$((now - mtime))
    if [ "$size" = "$last_size" ] && [ "$age" -gt "$FROZEN_AFTER" ]; then
      if [ "$frozen" != 1 ]; then
        red "=== TAPE FROZEN: $FILE has not grown for ${age}s (size ${size}) ==="
        red "    Bookmap may still look fine (alive and blind). Fix: remove the Bookmap"
        red "    add-on and re-attach it (Bookmap itself stays open). If it freezes AGAIN"
        red "    near 22:00 -> suspect the schedule/subscription cut, not the add-on."
        logline "FROZEN age=${age}s size=${size}"
        frozen=1
      fi
    else
      if [ "$frozen" = 1 ]; then
        green "=== TAPE FLOWING again: $FILE grew (size ${last_size} -> ${size}, gap ${age}s) ==="
        logline "RECOVERED size=${size} gap=${age}s"
        frozen=0
      fi
      last_size="$size"
    fi
    if [ "$HEARTBEAT_EVERY" -gt 0 ] && [ $((checks % HEARTBEAT_EVERY)) -eq 0 ]; then
      dim "tape_watchdog: still watching $FILE - size ${size}, last write ${age}s ago$([ "$frozen" = 1 ] && echo ' [FROZEN]')"
    fi
  fi
  if [ "$MAX_CHECKS" -gt 0 ] && [ "$checks" -ge "$MAX_CHECKS" ]; then
    echo "tape_watchdog: MAX_CHECKS=$MAX_CHECKS reached, exiting."
    exit 0
  fi
  sleep "$CHECK_EVERY"
done
