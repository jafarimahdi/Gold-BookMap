#!/usr/bin/env bash
# tape_watchdog_v2.sh -- "the file grows... but are PRICES still arriving?"
# =====================================================================
# v1 watched file growth. The 2026-10-08 lesson: after 21:59 the file kept
# growing on order-book events (DepthBid/Mbo) for a whole hour while trade
# prints had already stopped. v1 stayed silent for 60 min. v2 adds a PRICE
# HEARTBEAT: it finds the newest trade print ("Last"/"Trade"/"trade" rows)
# and barks when prices go silent -- even if the file keeps growing.
#
# READ-ONLY. It never touches the tape; it only reads the tail of the file.
#
# Usage (git bash, second terminal, any time - best before the session):
#
#     bash tape_watchdog_v2.sh                 # finds ./ticks.csv or ./data/ticks.csv
#     bash tape_watchdog_v2.sh path/to/ticks.csv
#
# Tunables (environment variables):
#     PRICE_SILENT_AFTER=120  seconds without a trade print before the shout
#     FROZEN_AFTER=120        seconds without ANY file growth before the shout
#     CHECK_EVERY=30          seconds between looks
#     HEARTBEAT_EVERY=20      checks between quiet "still watching" lines (0=never)
#     MAX_CHECKS=0            stop after N checks (0=forever; used by tests)
#     TAPE_WATCH_LOG=data/tape_watchdog.log
#
# What it prints (state changes only):
#   RED   === PRICES SILENT ... ===   -> trade prints stopped while the file
#                                        still grows (the 21:59 pattern).
#                                        ACTION: look at Bookmap's own chart.
#                                        Trades printing there? -> remove +
#                                        re-attach the add-on (Bookmap stays
#                                        open). Nothing there either? -> feed /
#                                        subscription side, not the add-on.
#   RED   === TAPE FROZEN ... ===     -> nothing at all is being written.
#                                        At 23:00-00:00 local and on weekends
#                                        that is the legal market close (dim
#                                        note, not red).
#   GREEN === ... back/again ===      -> recovered.
# Everything is also written to the log (same file as v1).

FILE="${1:-}"
if [ -z "$FILE" ]; then
  if [ -f ticks.csv ]; then FILE="ticks.csv"
  elif [ -f data/ticks.csv ]; then FILE="data/ticks.csv"
  else FILE="ticks.csv"
  fi
fi

PRICE_SILENT_AFTER="${PRICE_SILENT_AFTER:-120}"
FROZEN_AFTER="${FROZEN_AFTER:-120}"
CHECK_EVERY="${CHECK_EVERY:-30}"
HEARTBEAT_EVERY="${HEARTBEAT_EVERY:-20}"
MAX_CHECKS="${MAX_CHECKS:-0}"
LOG="${TAPE_WATCH_LOG:-data/tape_watchdog.log}"
TRADE_RE='^[0-9]{4}-[0-9]{2}-[0-9]{2}T[^,]+,(Last|Trade|trade),'
TAIL_BYTES=4194304   # 4 MB tail window: enough for ~1h of book-only rows

mkdir -p "$(dirname "$LOG")" 2>/dev/null || true

stamp() { date '+%Y-%m-%d %H:%M:%S'; }
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
dim()   { printf '\033[90m%s\033[0m\n' "$*"; }
logline() { printf '%s %s\n' "$(stamp)" "$*" >> "$LOG" 2>/dev/null || true; }

# newest trade print in the file: try the 4MB tail window first (fast),
# fall back to a full scan only if the window holds no trade row at all.
last_trade_line() {
  local line=""
  line=$(tail -c "$TAIL_BYTES" "$FILE" 2>/dev/null | grep -E "$TRADE_RE" | tail -1)
  if [ -z "$line" ]; then
    local size
    size=$(stat -c %s "$FILE" 2>/dev/null || echo 0)
    if [ "$size" -gt "$TAIL_BYTES" ]; then
      line=$(grep -E "$TRADE_RE" "$FILE" 2>/dev/null | tail -1)
    fi
  fi
  printf '%s' "$line"
}

# epoch of an ISO stamp like 2026-10-08T19:07:39.771+02:00
ts_epoch() {
  local ts="${1%%,*}"
  local e
  e=$(date -d "$ts" +%s 2>/dev/null) || true
  if [ -z "$e" ]; then
    e=$(date -d "$(sed 's/\.[0-9]\+//' <<<"$ts")" +%s 2>/dev/null) || true
  fi
  printf '%s' "$e"
}

# 0 = quiet hours (23:00-00:00 local, Sat, Sun): market legally closed
market_quiet_now() {
  local h dow
  h=$(date +%H); dow=$(date +%u)
  [ "$h" = "23" ] || [ "$dow" -ge 6 ]
}

echo "tape_watchdog v2: watching $FILE"
echo "  price heartbeat: shout after ${PRICE_SILENT_AFTER}s without a trade print | file growth: shout after ${FROZEN_AFTER}s | look every ${CHECK_EVERY}s"
if [ ! -f "$FILE" ]; then
  red "tape_watchdog v2: $FILE does not exist yet - pass the right path:"
  red "  bash tape_watchdog_v2.sh path/to/ticks.csv     (find it first: ls -la ticks.csv data/ticks.csv)"
fi
logline "START v2 file=$FILE price_silent_after=${PRICE_SILENT_AFTER}s frozen_after=${FROZEN_AFTER}s every=${CHECK_EVERY}s"

frozen=0; silent=0; missing=0
last_size=""; checks=0

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
      missing=0; last_size=""; silent=0; frozen=0
    fi

    size=$(stat -c %s "$FILE" 2>/dev/null || echo "?")
    mtime=$(stat -c %Y "$FILE" 2>/dev/null || echo 0)
    now=$(date +%s)
    file_age=$((now - mtime))
    growing=$([ "$size" != "$last_size" ] && echo 1 || echo 0)

    # --- v1 behaviour: whole-file freeze --------------------------------
    if [ "$size" = "$last_size" ] && [ "$file_age" -gt "$FROZEN_AFTER" ]; then
      if [ "$frozen" != 1 ]; then
        if market_quiet_now; then
          dim "tape quiet (market close/maintenance hours): $FILE unchanged for ${file_age}s - legal, not a bug"
          logline "QUIET_CLOSE age=${file_age}s size=${size}"
        else
          red "=== TAPE FROZEN: $FILE has not grown for ${file_age}s (size ${size}) ==="
          red "    Nothing at all is being written. If it is not 23:00-00:00 or a weekend:"
          red "    remove the Bookmap add-on and re-attach it (Bookmap itself stays open)."
        fi
        logline "FROZEN age=${file_age}s size=${size}"
        frozen=1
      fi
    else
      if [ "$frozen" = 1 ]; then
        green "=== TAPE FLOWING again: $FILE grew (size ${last_size} -> ${size}, gap ${file_age}s) ==="
        logline "RECOVERED size=${size} gap=${file_age}s"
        frozen=0
      fi
      last_size="$size"
    fi

    # --- v2 heartbeat: newest trade print -------------------------------
    if [ "$frozen" != 1 ]; then
      tl=$(last_trade_line)
      if [ -n "$tl" ]; then
        te=$(ts_epoch "$tl")
        if [ -n "$te" ]; then
          page=$((now - te))
          if [ "$page" -gt "$PRICE_SILENT_AFTER" ] && [ "$growing" = "1" ]; then
            if [ "$silent" != 1 ]; then
              red "=== PRICES SILENT for ${page}s - file still growing on book events only (the 21:59 pattern) ==="
              red "    Trade prints stopped, order-book rows did not. Look at Bookmap's own chart NOW:"
              red "      trades printing there?      -> remove + re-attach the Bookmap add-on (Bookmap stays open)"
              red "      no trades there either?     -> feed / subscription side - the add-on will not help"
              logline "PRICE_SILENT age=${page}s size=${size}"
              silent=1
            fi
          elif [ "$page" -gt "$PRICE_SILENT_AFTER" ] && [ "$growing" = "0" ] && [ "$file_age" -le "$FROZEN_AFTER" ]; then
            dim "prices idle ${page}s, file idle too - waiting (not yet FROZEN threshold)"
          else
            if [ "$silent" = 1 ]; then
              green "=== PRICES BACK: newest trade print ${page}s ago ==="
              logline "PRICE_BACK age=${page}s"
              silent=0
            fi
          fi
        fi
      else
        dim "no trade print found in $FILE yet (market closed, or tape starts soon)"
      fi
    fi

    if [ "$HEARTBEAT_EVERY" -gt 0 ] && [ $((checks % HEARTBEAT_EVERY)) -eq 0 ]; then
      dim "tape_watchdog v2: still watching - size ${size}, file ${file_age}s ago$([ "$frozen" = 1 ] && echo ' [FROZEN]')$([ "$silent" = 1 ] && echo ' [PRICES SILENT]')"
    fi
  fi

  if [ "$MAX_CHECKS" -gt 0 ] && [ "$checks" -ge "$MAX_CHECKS" ]; then
    echo "tape_watchdog v2: MAX_CHECKS=$MAX_CHECKS reached, exiting."
    exit 0
  fi
  sleep "$CHECK_EVERY"
done
