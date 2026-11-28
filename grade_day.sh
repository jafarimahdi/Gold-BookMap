#!/usr/bin/env bash
# grade_day v2 - one-command evening for factory 2 (owner order 2026-11-26: the nightly ritual
# is ONE line, forever). NEW FILE JUSTIFIED: the evening is a fixed ORDER of tools; a human
# retyping them nightly is a typo waiting to grade the wrong day (law 102 family).
# v2 (2026-11-28, the Thanksgiving parcel) changes the ORDER and adds an organ:
#   - the BELT (kindcheck) now runs BEFORE the teacher - a day with a hole in it must never
#     collect a gold star first and an apology after;
#   - a folded day is un-folded kindly (gunzip -k: the coat stays on the shelf);
#   - the bank's OWN notebook is read aloud (tools/gates_statement.py) - the +15.18 coin that
#     came home unwatched on 11-26 is why this step is not optional.
# usage:  bash grade_day.sh            (grades TODAY)
#         bash grade_day.sh 2026-11-26 (grades that date)
echo "[grade_day v2]"
cd "$(dirname "$0")" || exit 9

if [ -n "${PY:-}" ] && command -v "$PY" >/dev/null 2>&1; then
  PYBIN="$PY"
elif command -v py >/dev/null 2>&1; then PYBIN="py -3"
elif command -v python >/dev/null 2>&1; then PYBIN=python
elif command -v python3 >/dev/null 2>&1; then PYBIN=python3
else echo "[grade_day] NO PYTHON FOUND - tell the keeper"; exit 2; fi

D="${1:-$(date +%F)}"
case "$D" in [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]) ;; *) echo "[grade_day] bad date '$D' - use YYYY-MM-DD"; exit 2;; esac
DS="${D//-/}"
SBOX="data/gates_sandbox"
DIARY="$SBOX/gates_shadow_$DS.jsonl"
SAIL_LOG="$HOME/sail_$DS.txt"

# --- the coat: if the day is folded, read the coat without cutting it (fold law: NEVER edit)
if [ ! -f "$DIARY" ] && [ -f "$DIARY.gz" ]; then
  echo "[grade_day] the day is folded - un-folding a COPY (the .gz coat stays)"
  gzip -dk "$DIARY.gz"
fi

# --- THE BELT, before any grading: count the pages before the teacher sees the book (A4)
if [ -f "$DIARY" ]; then
  echo "[grade_day] THE BELT, before any grading:"
  $PYBIN tools/kindcheck.py "$DIARY" || echo "[grade_day] BELT DID NOT CLOSE - the grade below must be read with care"
else
  echo "[grade_day] no diary for $D yet ($DIARY) - the teacher will grade the sea alone and say so"
fi

echo "[grade_day] sailing $D (quiet, shadow hands) ..."
$PYBIN -u tools/gates_replay.py --date "$D" --window 06:00-23:00 --every 180 --hands-shadow --quiet --json-out "$SBOX/gates_sea_$DS.json" > "$SAIL_LOG" 2>&1
sc=$?
echo "[grade_day] sail exit $sc  (tail: $(tail -1 "$SAIL_LOG" | cut -c1-90))"
[ "$sc" -ne 0 ] && { echo "[grade_day] sail FAILED - paste me: tail -20 $SAIL_LOG"; exit $sc; }

echo "[grade_day] the teacher reads $D ..."
$PYBIN tools/gates_report.py --date "$D" --sea "$SBOX/gates_sea_$DS.json" | tee "$HOME/grade_$DS.txt"
echo "[grade_day] grade sheet also saved: ~/grade_$DS.txt"

if [ -f "$DIARY" ]; then
  echo "[grade_day] the day's equation, once more on the finished book:"
  $PYBIN tools/kindcheck.py "$DIARY"
fi

echo "[grade_day] the bank's own notebook (statement vs paper):"
$PYBIN tools/gates_statement.py "$D" || true

echo "[grade_day] done. paste me the VERDICT line from above."
