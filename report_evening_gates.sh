#!/usr/bin/env bash
# ==============================================================================
# report_evening_gates.sh -- THE NEW FACTORY's own evening command (COMPANY 3).
#
#   bash report_evening_gates.sh                    # grade the new hall for TODAY
#   bash report_evening_gates.sh 2026-11-12         # grade a specific day
#   bash report_evening_gates.sh --sea 3            # replay the last 3 raw days first, then grade
#   bash report_evening_gates.sh --sea-once         # replay ONLY today's tape (fast sea), then grade
#   GATES_DATA_DIR=/path/to/tapes bash report_evening_gates.sh --sea-once   # your tape folder
#   bash report_evening_gates.sh --sea-once --max-minutes 240               # small machine: RAM cap
# Anything this script does not know (--window, --prewarm, --max-minutes, --depth off,
# --data-dir ...) is forwarded to the sea untouched.
#   bash report_evening_gates.sh --fold             # gzip finished diary days, then grade
#
# What it runs, in order:
#   1) (optional) THE SEA:  python tools/gates_replay.py  -- raw ticks/mbo -> the real
#      brain -> the committee -> one diary line per cycle + a graded json.
#   2) THE HEARTBEAT ONCE:  python factory2_gates.py --live --once  (or --demo --once
#      when the feed is off) - proves the loop, the pen and the fold in one shot.
#   3) THE NIGHT TEACHER:   python tools/gates_report.py -- writes
#      data/gates_<DATE>.txt + data/gates_<DATE>_metrics.json in the office's own
#      row grammar ([PASS]/[WARN]/[FAIL] -> what it measures -> what happened).
#
# The old factory is NOT touched: report_evening.sh, audit_day.py, main.py, config.py,
# .env and every old file stay bit-identical (COMPANY 3 law N1). This script writes
# ONLY data/gates_* files. It never trades: ALLOW_LIVE_TRADING is never read here.
# ==============================================================================
set -o pipefail
cd "$(dirname "$0")" || { echo "put this file in the project folder"; exit 1; }

# where the boat's raw tapes live. The shell NEVER read this before (it only
# printed --data-dir from an undefined variable), so GATES_DATA_DIR=/x did
# nothing for the sea - found by running the shell once and reading its output.
DATA_DIR="${GATES_DATA_DIR:-$DATA_DIR}"
export GATES_DATA_DIR="$DATA_DIR"      # factory2_gates.py reads it too

PY=$(command -v python || command -v python3)
[ -n "$PY" ] || { echo "[fail] no python on PATH"; exit 1; }

DATE=""; SEA_DAYS=0; SEA_ONCE=0; DO_FOLD=0; SKIP_HEARTBEAT=0
SEA_EXTRA=""      # unknown flags are FORWARDED to the sea (e.g. --max-minutes 240)
set -- "$@"
while [ "$#" -gt 0 ]; do
  a="$1"; shift
  case "$a" in
    --sea)
      # v7 (item 29): the old guard refused any value starting with "2" so a bare
      # date could not be eaten as a count - but that ALSO ate `--sea 2`, `--sea 20`
      # and left the number in the forwarded pile, so the sea died on
      # "unrecognized arguments: 2" and the night silently became days=3.
      # A count is now recognised by being ALL DIGITS and small; a date (which
      # contains `-`) falls through to the date case below, untouched.
      n="${1:-}"
      case "$n" in
        ""|*[!0-9]*) SEA_DAYS=3 ;;
        *) if [ "$n" -le 99 ]; then SEA_DAYS="$n"; shift; else SEA_DAYS=3; fi ;;
      esac ;;
    --sea-once) SEA_ONCE=1 ;;
    --fold) DO_FOLD=1 ;;
    --no-heartbeat) SKIP_HEARTBEAT=1 ;;
    20[0-9][0-9]-[0-1][0-9]-[0-3][0-9]) DATE="$a" ;;
    *)
      # A small machine still needs the RAM cap, and a different tape folder
      # still needs a path. Swallowing those words ("ignoring unknown argument")
      # would have made the owner's --max-minutes vanish into the sand, so any
      # flag we do not know is handed to the sea instead (Book Law #12).
      SEA_EXTRA="$SEA_EXTRA $a"
      case "$a" in
        --max-minutes|--window|--prewarm|--every|--max-cycles|--data-dir|--root-dir)
          if [ "$#" -gt 0 ]; then SEA_EXTRA="$SEA_EXTRA $1"; shift; fi ;;
      esac ;;
  esac
done
[ -n "$DATE" ] || DATE=$("$PY" -c "import time;print(time.strftime('%Y-%m-%d'))")
DAYC=$(echo "$DATE" | tr -d '-')

echo "=============================================================================="
echo " FACTORY 2 EVENING - $DATE   (new hall only; the old factory is untouched)"
echo "=============================================================================="

if [ "$SEA_DAYS" != "0" ] || [ "$SEA_ONCE" = "1" ]; then
  echo "-- [1/3] THE SEA (raw tapes through the borrowed brain) --"
  # Never grade YESTERDAY's sea because today's sea found no tape: clear the
  # day's own sea papers first, so "no --sea file given" means exactly that.
  mkdir -p data
  rm -f "data/gates_sea_${DAYC}.json" "data/gates_replay_${DAYC}.jsonl" \
        "data/gates_sea_err.txt"
  # v7 (item 21): this file was TAILED since the first draft and written by
  # NOTHING - a warn line that reads "its own words: " with nothing after it is
  # worse than no line, because it looks like a clean run. Now the sea's stderr
  # really goes there (fresh every night, so a stale trace can never be read as
  # tonight's). Keep it: the sea prints its refusal reasons to stderr.
  if [ "$SEA_ONCE" = "1" ]; then
    # shellcheck disable=SC2086
    "$PY" tools/gates_replay.py --date "$DATE" --every 180 --quiet \
        $([ -n "$DATA_DIR" ] && printf ' --data-dir %s' "$DATA_DIR") \
        --json-out "data/gates_sea_${DAYC}.json" \
        --diary-out "data/gates_replay_${DAYC}.jsonl" $SEA_EXTRA \
        2> "data/gates_sea_err.txt" || {
        echo "[warn] the sea did not finish (no tape? no brain?) - graded anyway"
        echo "[warn] its own words: $(tail -3 "data/gates_sea_err.txt" 2>/dev/null || true)"; }
  else
    # shellcheck disable=SC2086
    "$PY" tools/gates_replay.py --days "$SEA_DAYS" --every 180 --quiet \
        $([ -n "$DATA_DIR" ] && printf ' --data-dir %s' "$DATA_DIR") \
        --json-out "data/gates_sea_${DAYC}.json" \
        --diary-out "data/gates_replay_${DAYC}.jsonl" $SEA_EXTRA \
        2> "data/gates_sea_err.txt" || {
        echo "[warn] the sea did not finish (no tape? no brain?) - graded anyway"
        echo "[warn] its own words: $(tail -3 "data/gates_sea_err.txt" 2>/dev/null || true)"; }
  fi
else
  echo "-- [1/3] THE SEA: skipped (pass --sea N or --sea-once to sail it) --"
fi

SEA_JSON=""
[ -f "data/gates_sea_${DAYC}.json" ] && SEA_JSON="data/gates_sea_${DAYC}.json"

if [ "$SKIP_HEARTBEAT" = "0" ]; then
  echo "-- [2/3] ONE HEARTBEAT through the real loop (diary + fold proof) --"
  if [ "$DO_FOLD" = "1" ]; then
    "$PY" factory2_gates.py --live --once || "$PY" factory2_gates.py --demo --once || \
        echo "[warn] heartbeat skipped (feed off) - the diary from today is still graded"
  else
    "$PY" factory2_gates.py --live --once || "$PY" factory2_gates.py --demo --once || \
        echo "[warn] heartbeat skipped (feed off) - the diary from today is still graded"
  fi
else
  echo "-- [2/3] HEARTBEAT skipped (--no-heartbeat) --"
fi

echo "-- [3/3] THE NIGHT TEACHER --"
ARGS=(--date "$DATE")
[ -n "$SEA_JSON" ] && ARGS+=(--sea "$SEA_JSON")
# v7 (item 24): the teacher must know the hall was OFF on purpose, or a clean
# tape night is graded FAIL for "no diary" - a false alarm on a borrowed window.
[ "$SKIP_HEARTBEAT" = "1" ] && ARGS+=(--hall-off)
[ "$DO_FOLD" = "1" ] && ARGS+=(--fold)
"$PY" tools/gates_report.py "${ARGS[@]}"
RC=$?

echo ""
echo "=============================================================================="
echo " DONE. The new hall's night report for $DATE:"
echo "   1. Text (office row grammar) : data/gates_${DAYC}.txt"
echo "   2. Metrics (json, \"gates\")   : data/gates_${DAYC}_metrics.json"
echo "   3. The hall's own diary      : data/gates_shadow_${DAYC}.jsonl (gzipped when folded)"
echo " Nothing was traded. Nothing in the old factory was edited."
echo "=============================================================================="
exit $RC
