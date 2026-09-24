#!/usr/bin/env bash
# ============================================================================
#  daily_check.sh - the ONE daily command.
#    bash daily_check.sh                # audit the newest log day + open the dashboard
#    bash daily_check.sh --latest         # same thing, said explicitly
#    bash daily_check.sh 2026-09-23    # audit a specific day (whole day, no hour filter)
#    bash daily_check.sh --days 3      # roll-up: last 3 calendar days, day by day + totals
#    bash daily_check.sh --days 8      # roll-up: last 8 calendar days (the week view)
#    bash daily_check.sh --window      # grade ONLY the trading hours (old behaviour)
#    bash daily_check.sh --check        # prove the tester itself is honest (fake day)
#    bash daily_check.sh --quiet        # verdict + history rows only (no browser popup)
#    bash daily_check.sh --open         # re-open the dashboard, run nothing
#  Writes: day_report_<date>.txt, data/day_report_<date>.html (the dashboard),
#          data/day_metrics_<date>.json, data/history_report.html, data/index.html
# ============================================================================
cd "$(dirname "$0")" || { echo "put this file in the project folder"; exit 1; }

PY=$(command -v python || command -v python3)
MODE=""; DAY=""; QUIET=""; NOB=""; WINDOW=""
for a in "$@"; do
  case "$a" in
    --check) MODE=check ;;
    --open) MODE=open ;;
    --days) MODE=days ;;
    --window) WINDOW=--window ;;
    --quiet|--no-browser) NOB=--no-browser; [ "$a" = "--quiet" ] && QUIET=1 ;;
    *) DAY="$a" ;;
  esac
done

hr(){ [ -n "$QUIET" ] || printf '%s\n' "------------------------------------------------------------------------------"; }

# ---------- roll-up: "how did the last N days go" (whole days, no hour filter) -----
if [ "$MODE" = "days" ]; then
  N="${DAY:-3}"
  case "$N" in *[!0-9]*) echo "[FAIL] --days needs a number:  bash daily_check.sh --days 3   (or 8)"; exit 2 ;; esac
  [ -n "$QUIET" ] || echo "== roll-up: last $N calendar days - whole days, nothing cut by hour =="
  GBM_NO_BROWSER=1 "$PY" audit_day.py --days "$N" --no-browser
  rc=$?
  hr
  echo "   report: data\\last${N}days_report.html   (day by day + judge leaderboard)"
  echo "   text  : data\\last${N}days_report.txt"
  echo "   one day in full detail:  bash daily_check.sh 2026-09-23"
  [ -z "$NOB" ] && [ -f "data/last${N}days_report.html" ] && {
    "$PY" audit_day.py --open-html "data/last${N}days_report.html" >/dev/null 2>&1 || true
    echo "   (a browser tab should have opened)"
  }
  exit $rc
fi

# ---------- just reopen the last dashboard (nothing is computed) ----------
if [ "$MODE" = "open" ]; then
  L=$(ls data/day_report_*.html 2>/dev/null | sort | tail -1)
  [ -n "$L" ] && { "$PY" audit_day.py --open-html "$L"; exit 0; }
  "$PY" audit_day.py --open && exit 0
  echo "[warn] no report found - run:  bash daily_check.sh"; exit 1
fi

# ---------- 0. is the toolbox itself OK? ---------------------------------
if [ "$MODE" = "check" ]; then
  echo "== verifier: does the testing system itself work? =="
  [ -f audit_day.py ] || { echo "[FAIL] audit_day.py missing"; exit 1; }
  "$PY" -W error::SyntaxWarning -m py_compile audit_day.py && echo "[OK] audit_day.py compiles"
  grep -q 'BUILD = "audit-2026-09-24d"' audit_day.py && echo "[OK] audit_day.py carries build marker audit-2026-09-24d (sizes no longer matter)" || echo "[warn] no kit build marker in audit_day.py - you are on a file from an older kit, day numbers may be off"
  for f in tools/dashboard.py dashboard.py; do
    [ -f "$f" ] || { echo "[FAIL] $f missing - re-copy it from the kit"; exit 1; }
  done
  "$PY" -c "import sys;sys.path.insert(0,'tools');import dashboard;print('[OK] visual layer loads, '+str(len(dashboard._STYLE))+' bytes of style, '+str(len(dashboard._JS))+' bytes of script, from '+dashboard.__file__)" || { echo "[FAIL] tools/dashboard.py will not import"; exit 1; }
  "$PY" -c "
import sys; sys.path.insert(0,'tools')
import dashboard as d
for n in ('day_dashboard','write_index','history_block'):
    assert callable(getattr(d,n)), n
print('[OK] dashboard API complete (day report + index + trend table)')" || { echo "[FAIL] dashboard API incomplete"; exit 1; }
  DEMO=$(GBM_NO_BROWSER=1 "$PY" audit_day.py --demo 2>&1); rc1=$?
  echo "$DEMO" | tail -4
  SELF=""; rc2=0
  [ -f tools/selftest_audit_day.py ] && { SELF=$(GBM_NO_BROWSER=1 "$PY" tools/selftest_audit_day.py 2>&1); rc2=$?; echo "$SELF" | tail -1; }
  if [ $rc1 -ne 0 ] || [ $rc2 -ne 0 ]; then
    echo "[FAIL] the tester itself is not clean - do NOT trust day numbers yet."
    echo "       Most likely the auditor or tools/dashboard.py was half-copied:"
    echo "       ls -la audit_day.py dashboard.py tools/dashboard.py"
    echo "       want audit_day.py to contain BUILD = \"audit-2026-09-24d\" any size is fine if the marker matches — do NOT chase byte counts"
    echo "       and clear stale bytecode:  find . -name __pycache__ -type d -exec rm -rf {} +"
    exit 1
  fi
  echo "[OK] auditor and selftest both clean - every number it prints is trustworthy"
  exit 0
fi

# ---------- resolve "latest" so every file name carries the real date -----
# bare run and --latest mean the same thing; passing "--latest" through to preflight
# used to grep for a day literally named "--latest" and scream MISSING about everything
[ "$DAY" = "--latest" ] && DAY=""
if [ -z "$DAY" ]; then
  L=$(ls logs/trading_*.log 2>/dev/null | sort | tail -1)
  if [ -n "$L" ]; then
    B=$(basename "$L" .log); D8=${B#trading_}
    DAY="${D8:0:4}-${D8:4:2}-${D8:6:2}"
    echo "[info] no date given -> newest log: $DAY"
  else
    DAY=$(date +%F -d yesterday 2>/dev/null || date +%F)
    echo "[info] no logs found -> defaulting to $DAY"
  fi
fi

hr
[ -n "$QUIET" ] || echo "== 1/4 setup check =="
if [ -f preflight.sh ]; then
  OUT=$(bash preflight.sh "$DAY" 2>&1)
  if [ -n "$QUIET" ]; then
    echo "$OUT" | grep -E "RESULT|MISSING|DIFFERENT" || true
  else
    echo "$OUT" | sed -n '/--- 3/,$p'
  fi
  BLOCK=$(echo "$OUT" | grep -c "\[MISSING\]\|\[DIFFERENT\]")
else
  echo "[warn] preflight.sh missing - auditing anyway"; BLOCK=0
fi

hr
[ -n "$QUIET" ] || echo "== 2/4 day audit (15 tests) =="
GBM_NO_BROWSER=1 "$PY" audit_day.py --date "$DAY" --out "day_report_$DAY.txt" ${WINDOW:-} ${NOB:-}
[ "$WINDOW" = "--window" ] && echo "[info] --window: this run grades only the trading hours; the default grades the whole day"

hr
[ -n "$QUIET" ] || echo "== 3/4 history across days =="
if [ -f tools/check_history.py ]; then
  GBM_NO_BROWSER=1 "$PY" tools/check_history.py --html
else
  echo "[warn] tools/check_history.py missing"
fi

hr
echo "== 4/4 open the dashboard =="
ls -la "day_report_$DAY.txt" "data/day_report_$DAY.html" "data/day_metrics_$DAY.json" \
       "data/history_report.html" "data/index.html" 2>/dev/null | awk '{printf "   %-44s %8d bytes\n", $9, $5}'
# the audit call above already opened the dashboard tab (unless --quiet/--no-browser);
# this only makes sure the all-days page exists next to it
[ -z "$NOB" ] && [ -f "data/day_report_$DAY.html" ] && {
  "$PY" audit_day.py --open-html "data/day_report_$DAY.html" >/dev/null 2>&1 || true
  echo
  echo "   dashboard: data\\day_report_$DAY.html   (a new browser tab should have opened)"
  echo "   all days : data\\index.html              (double-click any time, nothing runs)"
  echo "   inside the dashboard: button 'Copy everything for chat' = the exact text you paste to me"
}
echo
[ "${BLOCK:-0}" -gt 0 ] && echo "[note] $BLOCK setup warning(s) above - the audit is still valid; read the FAIL cards first."
exit 0
