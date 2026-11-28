#!/usr/bin/env bash
# start_hall v2 - the owner's one-line morning (order 2026-11-26: "easy, like python main.py --loop").
# NEW FILE JUSTIFIED (#14): python discovery, one-hall guard, dated log, fast-death autopsy and a
# stop-pedestal belong in ONE ritual, not inside the hall file the loop imports.
# v2 from the field test: wait stays in THIS shell (a subshell cannot wait the parent's child),
# Ctrl-C kills the hall through a trap, and 'bash start_hall.sh stop' works from any tab.
# usage:  bash start_hall.sh          start (and watch) today's hall
#         bash start_hall.sh stop     stop it from wherever you are
echo "[start_hall v2]"
cd "$(dirname "$0")" || exit 9
PIDF="$HOME/hall.pid"

if [ "${1:-}" = "stop" ]; then
  if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
    kill "$(cat "$PIDF")" 2>/dev/null; rm -f "$PIDF"
    echo "[start_hall] hall stopped, as asked. The day's log keeps the story."
  else
    rm -f "$PIDF"; echo "[start_hall] no hall is running (nothing to stop)."
  fi
  exit 0
fi

# ---- find python: a fresh tab that forgot $PY must never stop the day ----
if [ -n "${PY:-}" ] && command -v "$PY" >/dev/null 2>&1; then PYBIN="$PY"
elif command -v py >/dev/null 2>&1; then PYBIN="py -3"
elif command -v python >/dev/null 2>&1; then PYBIN=python
elif command -v python3 >/dev/null 2>&1; then PYBIN=python3
else echo "[start_hall] NO PYTHON FOUND - tell the keeper"; exit 2; fi

# ---- one hall only: two halls = two pens on one diary; never ----
if [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; then
  echo "[start_hall] hall is ALREADY RUNNING (pid $(cat "$PIDF")) - one tab, one hall. To stop: bash start_hall.sh stop"
  exit 3
fi

LOG="$HOME/hall_$(date +%Y%m%d).txt"
echo "[start_hall] python: $PYBIN | log: $LOG (one file per day, appending)"
t0=$(date +%s)
$PYBIN -u factory2_gates.py --live --interval 180 --hands-demo >> "$LOG" 2>&1 &
HALL=$!
echo "$HALL" > "$PIDF"
echo "[start_hall] hall alive (pid $HALL). This tab is its window; Ctrl-C closes it kindly."
trap 'kill "$HALL" 2>/dev/null; rm -f "$PIDF"; echo "[start_hall] hall stopped by you."; exit 130' INT TERM
wait $HALL; code=$?
trap - INT TERM
rm -f "$PIDF"
dur=$(( $(date +%s) - t0 ))
echo "[start_hall] hall exited code=$code after ${dur}s"
if [ "$dur" -lt 30 ] && [ "$code" -ne 130 ]; then
  echo "[start_hall] DIED FAST - last 15 log lines (paste me these):"
  tail -15 "$LOG"
fi
exit $code
