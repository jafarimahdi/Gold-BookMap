#!/usr/bin/env bash
# ==============================================================================
# report_evening.sh -- THE ONE EVENING COMMAND (Company 2: The Report Office).
#
#   bash report_evening.sh                # newest trading day: ALL tests + ALL reports + X-Ray + opens ONE HTML page
#   bash report_evening.sh 2026-10-23     # same, for a specific trading day
#   bash report_evening.sh --fast         # ~45s quick evening check (skips slow replay Desks 4-6 & flow/book/wall probes)
#   bash report_evening.sh --paper-only   # run only Today's Paper desks (Desks 1-10) + X-Ray + update master HTML
#   bash report_evening.sh --no-book-probe # skip heavy MBO book_probe.py (enabled by default in full runs)
#   bash report_evening.sh --no-tests     # skip pytest/rigs (still runs daily_check.sh --check auditor gate)
#   bash report_evening.sh --no-browser   # write all reports, X-Ray & HTML without opening a browser tab
#   bash report_evening.sh --open         # re-open the latest master HTML report immediately, run nothing
#
# Covers EVERY department of COMPANY 2 -- THE REPORT OFFICE in one run:
#   Stage 1/6: Toolkit & Code Self-Tests (daily_check.sh --check, pytest -q, rig_g11, rig_memory, rig_tf_robot)
#   Stage 2/6: Setup Preflight + 17-Test Day Audit (audit_day.py + 4 diagnostics) + All-Days History
#   Stage 3/6: The Truck Camera (tail_probe.py -- STEP1/STEP2/STEP3 full-pipeline latency attribution)
#   Stage 4/6: The Spies & Signal Doctors (signal_health.py, signal_map.py, flow_probe.py, book_probe.py)
#   Stage 5/6: Today's Paper (Desks 1 Accountant, 2 Geometer, 3 Shadow, 9 POWER Card, 10 Council Shadow,
#              4 Lens, 5 Anatomist, 6 Universes M1-M10, 7 Clocks C1/C2/C3/C6, Watchman, 8 the AI COST line,
#              + 3-Desk Reconciliation Law)
#   Stage 6/6: Desk 11 X-Ray (`data/xray_<DATE>.txt` single-file overview + plain-text artifact vault, no .zip needed)
#              + Master One-Page Browser Report (`data/report_<DATE>.html`) + `data/evening_suite_<DATE>.log`
#
# Read-only with respect to trading: no robot runtime state, .env, threshold, or order is ever touched.
# ==============================================================================
set -o pipefail
cd "$(dirname "$0")" || { echo "put this file in the project folder"; exit 1; }

PY=$(command -v python || command -v python3)
DATE=""
FAST=0
PAPER_ONLY=0
RUN_TESTS=1
RUN_CHECK=1
BOOK_PROBE=1
NO_BROWSER=0
OPEN_ONLY=0
CONTINUE_ON_TEST_FAIL=0
WINDOW=""

for a in "$@"; do
  case "$a" in
    --full) PAPER_ONLY=0 ;;
    --fast) FAST=1 ;;
    --paper-only) PAPER_ONLY=1 ;;
    --no-tests) RUN_TESTS=0 ;;
    --no-check) RUN_CHECK=0 ;;
    --with-book-probe|--book-probe) BOOK_PROBE=1 ;;
    --no-book-probe) BOOK_PROBE=0 ;;
    --no-browser|--quiet) NO_BROWSER=1 ;;
    --open) OPEN_ONLY=1 ;;
    --continue-on-test-fail) CONTINUE_ON_TEST_FAIL=1 ;;
    --window) WINDOW=--window ;;
    --latest) DATE="" ;;
    *) DATE="$a" ;;
  esac
done

[ -n "${GBM_NO_BROWSER:-}" ] && NO_BROWSER=1

# ---------- "--open": reopen the master report in <1s, run nothing ----------
if [ "$OPEN_ONLY" = 1 ]; then
  if [ -n "$DATE" ] && [ -f "data/report_${DATE}.html" ]; then
    TARGET="data/report_${DATE}.html"
  else
    TARGET=$(ls data/report_*.html 2>/dev/null | sort | tail -1)
    [ -z "$TARGET" ] && TARGET=$(ls data/day_report_*.html 2>/dev/null | sort | tail -1)
  fi
  if [ -n "$TARGET" ] && [ -f "$TARGET" ]; then
    [ -f audit_day.py ] && "$PY" audit_day.py --open-html "$TARGET" >/dev/null 2>&1 || true
    echo "[chief of staff] opened: $TARGET"
    exit 0
  fi
  echo "[warn] no evening report found yet - run:  bash report_evening.sh"
  exit 1
fi

# ---------- Smart date resolution: newest trading log day when omitted ----------
DATE_NOTE=""
if [ -z "$DATE" ]; then
  NEWEST_LOG=$(ls logs/trading_*.log 2>/dev/null | sort | tail -1)
  if [ -n "$NEWEST_LOG" ]; then
    BASE_LOG=$(basename "$NEWEST_LOG" .log)
    D8=${BASE_LOG#trading_}
    if [ "${#D8}" = 8 ]; then
      DATE="${D8:0:4}-${D8:4:2}-${D8:6:2}"
      DATE_NOTE="[chief of staff] no date given -> newest trading log: $DATE"
    fi
  fi
  if [ -z "$DATE" ]; then
    DATE=$(date +%Y-%m-%d)
    DATE_NOTE="[chief of staff] no trading log found -> defaulting to calendar date: $DATE"
  fi
fi

mkdir -p data

# ---------- Auto-capture complete evening transcript to data/evening_suite_<DATE>.log ----------
SUITE_LOG="data/evening_suite_${DATE}.log"
ABS_SUITE_LOG="$(pwd)/$SUITE_LOG"
if [ "${GBM_EVENING_SUITE_ACTIVE_LOG:-}" != "$ABS_SUITE_LOG" ] && [ -z "${GBM_DISABLE_AUTO_TEE:-}" ]; then
  GBM_EVENING_SUITE_ACTIVE_LOG="$ABS_SUITE_LOG" bash "$0" "$@" 2>&1 | tee "$SUITE_LOG"
  SUITE_RC="${PIPESTATUS[0]}"
  if [ "$SUITE_RC" -eq 0 ] && [ -f tools/xray_report.py ]; then
    "$PY" tools/xray_report.py --date "$DATE" --quiet >/dev/null 2>&1 || true
  fi
  exit "$SUITE_RC"
fi

hr() { printf '%s\n' "--------------------------------------------------------------------------------------------------------------"; }
title() { printf '\n%s\n%s\n%s\n' "==============================================================================================================" "$1" "=============================================================================================================="; }

[ -n "$DATE_NOTE" ] && echo "$DATE_NOTE"
title "COMPANY 2 -- THE REPORT OFFICE -- $DATE  (report_evening.sh: one command, one report)"

GATE_TXT="data/evening_test_gate_${DATE}.txt"
GATE_JSON="data/evening_test_gate_${DATE}.json"

# ==============================================================================
# STAGE 1/6: Toolkit & Code Self-Tests (fail-closed if broken)
# ==============================================================================
if [ "$PAPER_ONLY" = 0 ]; then
  echo "== [1/6] Toolkit & Code Self-Tests =="
  : > "$GATE_TXT"
  GATE_FAILED=0

  "$PY" - "$DATE" "$GATE_JSON" <<'PYEOF'
import json, sys
json.dump({"date": sys.argv[1], "checks": []}, open(sys.argv[2], "w", encoding="utf-8"), indent=2)
PYEOF

  record_gate() {
    local name="$1" status="$2" secs="$3" detail="$4"
    printf '[%s] %-28s (%ss) %s\n' "$status" "$name" "$secs" "$detail" | tee -a "$GATE_TXT"
    "$PY" - "$GATE_JSON" "$name" "$status" "$secs" "$detail" <<'PYEOF'
import json, sys
p, name, status, secs, detail = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]), sys.argv[5]
d = json.load(open(p, encoding="utf-8"))
d.setdefault("checks", []).append({"name": name, "status": status, "seconds": secs, "detail": detail})
json.dump(d, open(p, "w", encoding="utf-8"), indent=2)
PYEOF
  }

  if [ "$RUN_CHECK" = 1 ] && [ -f daily_check.sh ]; then
    T0=$(date +%s)
    CHK_OUT=$(GBM_NO_BROWSER=1 bash daily_check.sh --check 2>&1)
    CHK_RC=$?
    T1=$(date +%s)
    CHK_LAST=$(printf '%s\n' "$CHK_OUT" | tail -1)
    if [ "$CHK_RC" -eq 0 ]; then
      record_gate "daily_check.sh --check" "PASS" "$((T1 - T0))" "$CHK_LAST"
    else
      record_gate "daily_check.sh --check" "FAIL" "$((T1 - T0))" "$CHK_LAST"
      printf '%s\n' "$CHK_OUT" | tee -a "$GATE_TXT"
      echo "[FAIL] Auditor self-check failed -- stopping so untrustworthy day numbers are never printed."
      exit 1
    fi
  else
    record_gate "daily_check.sh --check" "SKIP" "0" "skipped by flag"
  fi

  if [ "$RUN_TESTS" = 1 ] && [ -z "${GBM_INSIDE_EVENING_PYTEST:-}" ]; then
    if "$PY" -c "import pytest" >/dev/null 2>&1; then
      T0=$(date +%s)
      PT_OUT=$(GBM_INSIDE_EVENING_PYTEST=1 GBM_NO_BROWSER=1 "$PY" -m pytest -q 2>&1)
      PT_RC=$?
      T1=$(date +%s)
      PT_LAST=$(printf '%s\n' "$PT_OUT" | tail -1)
      if [ "$PT_RC" -eq 0 ]; then
        record_gate "pytest -q" "PASS" "$((T1 - T0))" "$PT_LAST"
      else
        record_gate "pytest -q" "FAIL" "$((T1 - T0))" "$PT_LAST"
        printf '%s\n' "$PT_OUT" | tee -a "$GATE_TXT"
        GATE_FAILED=1
      fi
    else
      record_gate "pytest -q" "SKIP" "0" "pytest module not installed"
    fi

    for rig in rig_g11.py rig_memory.py rig_tf_robot.py; do
      [ "$FAST" = 1 ] && [ "$rig" = "rig_tf_robot.py" ] && {
        record_gate "$rig" "SKIP" "0" "skipped in --fast mode"
        continue
      }
      if [ -f "$rig" ]; then
        T0=$(date +%s)
        RIG_OUT=$("$PY" "$rig" 2>&1)
        RIG_RC=$?
        T1=$(date +%s)
        RIG_LAST=$(printf '%s\n' "$RIG_OUT" | grep -v '^$' | tail -1)
        if [ "$RIG_RC" -eq 0 ]; then
          record_gate "$rig" "PASS" "$((T1 - T0))" "$RIG_LAST"
        else
          record_gate "$rig" "FAIL" "$((T1 - T0))" "$RIG_LAST"
          printf '%s\n' "$RIG_OUT" | tee -a "$GATE_TXT"
          GATE_FAILED=1
        fi
      fi
    done
  else
    record_gate "code & rig tests" "SKIP" "0" "skipped (--no-tests or nested pytest)"
  fi

  if [ "$GATE_FAILED" -ne 0 ] && [ "$CONTINUE_ON_TEST_FAIL" = 0 ]; then
    echo "[FAIL] One or more code/rig tests failed (see above and $GATE_TXT)."
    echo "       Fix the failing test first, or pass --continue-on-test-fail to inspect reports anyway."
    exit 1
  fi
  hr
fi

# ==============================================================================
# STAGE 2/6: Setup Preflight + 17-Test Day Audit + All-Days History
# ==============================================================================
if [ "$PAPER_ONLY" = 0 ]; then
  echo "== [2/6] Setup Preflight + 17-Test Day Audit + Multi-Day History =="
  if [ -f preflight.sh ]; then
    PF_OUT=$(bash preflight.sh "$DATE" 2>&1)
    printf '%s\n' "$PF_OUT" | sed -n '/--- 3/,$p' | tee "data/preflight_${DATE}.txt"
  else
    echo "[warn] preflight.sh not found - continuing"
  fi
  hr
  if [ -f audit_day.py ]; then
    GBM_NO_BROWSER=1 "$PY" audit_day.py --date "$DATE" --out "day_report_${DATE}.txt" ${WINDOW:-} --no-browser
  else
    echo "[warn] audit_day.py not found - skipping 17-test day audit"
  fi
  hr
  if [ -f tools/check_history.py ]; then
    GBM_NO_BROWSER=1 "$PY" tools/check_history.py --html | tee "data/history_summary_${DATE}.txt"
  fi
  hr
fi

# ==============================================================================
# STAGE 3/6: The Truck Camera (tail_probe.py -- STEP1/STEP2/STEP3 latency)
# ==============================================================================
if [ "$PAPER_ONLY" = 0 ]; then
  echo "== [3/6] The Truck Camera (tail_probe.py -- pipeline latency attribution) =="
  if [ -f tail_probe.py ]; then
    "$PY" tail_probe.py --date "$DATE" --peek
  else
    echo "  (tail_probe.py not installed - skipping)"
  fi
  hr
fi

# ==============================================================================
# STAGE 4/6: The Spies & Signal Doctors (signal_health + signal_map + flow_probe + book_probe)
# ==============================================================================
if [ "$PAPER_ONLY" = 0 ]; then
  echo "== [4/6] The Flow, Order-Book & Signal Spies =="
  if [ -f tools/signal_health.py ] && [ -f audit_day.py ]; then
    "$PY" tools/signal_health.py --date "$DATE" --quality --teams > "data/signal_health_${DATE}.txt" 2>&1 || true
    head -n 28 "data/signal_health_${DATE}.txt" 2>/dev/null || true
    hr
  fi
  if [ "$FAST" = 1 ]; then
    echo "  (signal_map.py / flow_probe.py / book_probe.py skipped in --fast mode)"
  else
    if [ -f tools/signal_map.py ] && [ -f audit_day.py ]; then
      "$PY" tools/signal_map.py --date "$DATE" > "data/signal_map_${DATE}.txt" 2>&1 || true
      tail -n 24 "data/signal_map_${DATE}.txt" 2>/dev/null || true
      hr
    fi
    if [ -f flow_probe.py ] && [ -f tf_lens.py ]; then
      "$PY" flow_probe.py --date "$DATE"
    else
      echo "  (flow_probe.py not installed - skipping)"
    fi
    if [ "$BOOK_PROBE" = 1 ] && [ -f book_probe.py ] && [ -f flow_probe.py ] && [ -f tf_lens.py ]; then
      hr
      "$PY" book_probe.py --date "$DATE"
    fi
  fi
  hr
fi

# ==============================================================================
# STAGE 5/6: TODAY'S PAPER (Desks 1-10 + Watchman + AI Cost + Reconciliation)
# ==============================================================================
PAPER_FILE="data/evening_paper_${DATE}.txt"
{
  title "TODAY'S PAPER - $DATE  (report_evening.sh - the one evening command)"

  echo "[chief of staff] desk 1/7: the Accountant (decision ledger)..."
  "$PY" decision_ledger.py --date "$DATE" | sed 's/^/  /'
  hr
  echo "[chief of staff] desk 2/7: the Geometer (edge persistence)..."
  "$PY" tf_study.py --date "$DATE" | sed 's/^/  /'
  hr
  echo "[chief of staff] desk 3/7: the Shadow (the robot's own wall logic)..."
  "$PY" shadow_replay.py --date "$DATE" | sed 's/^/  /'
  hr

  # ---- fusion v1: the POWER desks (what POWER said, and what it carried) -----
  echo "[chief of staff] desk 9: the POWER card (what POWER said all day)..."
  if [ -f power_report_card.py ]; then
    "$PY" power_report_card.py "$DATE" | tee "data/power_report_card_${DATE}.txt" | sed 's/^/  /'
  else
    echo "  (power_report_card.py not installed - skipping)"
  fi
  hr
  echo "[chief of staff] desk 10: the Council shadow (what fusion would have carried)..."
  if [ -f council_shadow.py ]; then
    "$PY" council_shadow.py "$DATE" | tee "data/council_shadow_${DATE}.txt" | sed 's/^/  /'
  else
    echo "  (council_shadow.py not installed - skipping)"
  fi
  hr

  # ---- v1.2+: the measurement desks (skip slow replay desks 4-6 with --fast) -
  if [ "$FAST" = 1 ]; then
    echo "[chief of staff] desks 4-6 skipped (--fast: the quick paper)"
  else
    if [ -f tf_lens.py ]; then
      echo "[chief of staff] desk 4/7: the Lens (timeframe glasses, ~1-3 min)..."
      "$PY" tf_lens.py --date "$DATE" | sed 's/^/  /'
    else
      echo "[chief of staff] desk 4/7: the Lens is not installed (tf_lens.py missing) - skipping."
    fi
    hr
    if [ -f fish_anatomy.py ] && [ -f tf_lens.py ]; then
      echo "[chief of staff] desk 5/7: the Anatomist (fish dissection, ~1-3 min)..."
      "$PY" fish_anatomy.py --date "$DATE" | sed 's/^/  /'
    else
      echo "[chief of staff] desk 5/7: the Anatomist needs fish_anatomy.py + tf_lens.py - skipping."
    fi
    hr
    if [ -f tf_robot_replay.py ] && [ -f twin_day_runner.py ] && [ -f step2_market_analysis.py ]; then
      echo "[chief of staff] desk 6/7: the Universes (REAL brain at 1/3/5/8/10 min, SLOW ~5-10 min)..."
      "$PY" tf_robot_replay.py --date "$DATE" | sed 's/^/  /'
    else
      echo "[chief of staff] desk 6/7: the Universes need tf_robot_replay.py + twin_day_runner.py + the brain - skipping."
    fi
  fi
  hr

  if [ -f clocks_verdict.py ]; then
    echo "[chief of staff] desk 7/7: the Clocks (the 5-day rules, C1/C2/C3/C6)..."
    "$PY" clocks_verdict.py | sed 's/^/  /'
  else
    echo "[chief of staff] desk 7/7: the Clocks are not installed (clocks_verdict.py missing) - skipping."
  fi
  hr

  # ---- the Editor's own numbers, if the day was audited ----------------------
  METRICS="data/day_metrics_${DATE}.json"
  if [ -f "$METRICS" ]; then
    echo "[chief of staff] the Editor's recorded verdict ($METRICS):"
    "$PY" - "$METRICS" <<'PYEOF' | sed 's/^/  /'
import json, sys
def find(obj, needles, path=""):
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if any(n in str(k).lower() for n in needles) and not isinstance(v, (dict, list)):
                out.append((p, v))
            out.extend(find(v, needles, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5]):
            out.extend(find(v, needles, f"{path}[{i}]"))
    return out
try:
    d = json.load(open(sys.argv[1], encoding="utf-8", errors="ignore"))
except Exception as e:
    print(f"  (could not read: {e})"); sys.exit(0)
rows = find(d, ["verdict", "pass", "warn", "fail", "whatif", "what_if", "win_rate", "wr"])
if not rows:
    print("  (no verdict/what-if keys found - re-run: bash report_evening.sh " + sys.argv[1].split("day_metrics_")[-1].split(".")[0] + ")")
for p, v in rows[:12]:
    print(f"  {p:<40} {v}")
PYEOF
  else
    echo "[chief of staff] the Editor has not graded this day yet (no $METRICS)."
    echo "  for the Editor's own grade:  bash report_evening.sh $DATE"
  fi
  hr

  # ---- the Watchman's day ----------------------------------------------------
  WL="data/tape_watchdog.log"
  if [ -f "$WL" ]; then
    echo "[chief of staff] the Watchman's day (from $WL):"
    grep "^$DATE " "$WL" | sed 's/^/  /' || echo "  (no entries for this date - watchdog not running that day?)"
  else
    echo "[chief of staff] no watchdog log (data/tape_watchdog.log) - the Watchman did not run."
  fi
  hr

  # ---- v1.4 (report v2): the AI COST line ------------------------------------
  echo "[chief of staff] desk 8: the AI COST line (report v2)..."
  "$PY" - "$DATE" <<'PYEOF' | sed 's/^/  /'
import json, sys
date = sys.argv[1]
try:
    d = json.load(open("data/ai_calls.json", encoding="utf-8"))
    if str(d.get("date")) == date:
        print(f"paid AI calls (data/ai_calls.json): {d.get('calls')}")
    else:
        print(f"paid AI calls: the file holds {d.get('date')} ({d.get('calls')} calls) - it resets daily;")
        print("the day's own count is in the Editor's TEST 17 (read from the log)")
except Exception as e:
    print(f"paid AI calls: (data/ai_calls.json unreadable: {e})")
print("the full cost story (real calls / throttled rests / bells lost to the rest):")
print("the Editor's TEST 17:  python audit_day.py --date " + date)
PYEOF
  hr

  # ---- the reconciliation line -----------------------------------------------
  echo "[chief of staff] RECONCILIATION (all three must tell the same story):"
  "$PY" - "$DATE" <<'PYEOF' | sed 's/^/  /'
import os, re, sys
date = sys.argv[1]
def rd(path):
    try:
        return open(path, encoding="utf-8", errors="ignore").read()
    except OSError:
        return ""
led = rd(os.path.join("data", f"decision_ledger_{date}.txt"))
tf  = rd(os.path.join("data", f"tf_study_{date}.txt"))
sh  = rd(os.path.join("data", f"shadow_replay_{date}.txt"))
def grab(txt, pat):
    m = re.search(pat, txt)
    return float(m.group(1)) if m else None
led_all = grab(led, r"ALL SIGNALS\s+\d+\s+\d+\s+([+-]?[\d.]+)")
tf_audit = None
m = re.search(r"M5 \(audit\)\s+\d+\s+[\d.]+\s+[\d.]+\s+[\d.]+%\s+\d+\s+[\d.]+\s+([+-]?[\d.]+)", tf)
tf_audit = float(m.group(1)) if m else None
bm = grab(sh, r"BENCHMARK fixed ruler, ALL signals\s+\d+\s+([+-]?[\d.]+)")
bm_same = grab(sh, r"BENCHMARK fixed ruler, same subset\s+\d+\s+([+-]?[\d.]+)")
shd = grab(sh, r"SHADOW \(walls\+gate\+trail\)\s+\d+\s+([+-]?[\d.]+)")
def fmt(x): return f"{x:+.1f}" if x is not None else "(missing)"
print(f"  Accountant ALL SIGNALS : {fmt(led_all)}")
print(f"  Geometer M5 (audit)    : {fmt(tf_audit)}")
print(f"  Shadow BENCHMARK row   : {fmt(bm)}   (same ruler, computed independently)")
if None not in (led_all, tf_audit, bm):
    spread = max(abs(led_all - tf_audit), abs(led_all - bm))
    if spread <= max(0.5, 0.02 * abs(led_all or 1)):
        print(f"  RECONCILED: all three agree within {spread:.2f} pts.")
    else:
        print(f"  FLAG: spread {spread:.2f} pts - a desk is telling a different story. Look closer before believing either.")
else:
    print("  (one of the desks produced no number - read its section above)")
if shd is not None:
    print(f"  Shadow (robot's own walls) vs same-subset benchmark: {fmt(shd)} vs {fmt(bm_same)} "
          f"- the gap is the honest size of 'the walls change the answer'.")
PYEOF
  hr
  echo "[chief of staff] today's desk outputs:"
  ls -la "data/decision_ledger_${DATE}".* "data/tf_study_${DATE}".* "data/shadow_replay_${DATE}".* \
         "data/power_report_card_${DATE}.txt" "data/council_shadow_${DATE}.txt" \
         "data/tf_lens_${DATE}".* "data/fish_anatomy_${DATE}".* "data/tf_robot_${DATE}".* \
         "data/tail_probe_${DATE}.txt" "data/flow_probe_${DATE}.txt" "data/clocks_verdict.txt" 2>/dev/null \
         | awk '{print "  " $NF " (" $5 " bytes)"}'
} 2>&1 | tee "$PAPER_FILE"

hr

# ==============================================================================
# STAGE 6/6: Build Desk 11 X-Ray + Stitch EVERYTHING into ONE HTML page & open it
# ==============================================================================
echo "== [6/6] Desk 11 X-Ray + Master One-Page Report (all tests + all desks in one place) =="
if [ -f tools/xray_report.py ]; then
  "$PY" tools/xray_report.py --date "$DATE"
fi
MASTER_HTML="data/report_${DATE}.html"
if [ -f tools/report_office_bundle.py ]; then
  "$PY" tools/report_office_bundle.py --date "$DATE"
fi

if [ "$NO_BROWSER" = 0 ] && [ -f "$MASTER_HTML" ]; then
  if [ -f audit_day.py ]; then
    "$PY" audit_day.py --open-html "$MASTER_HTML" >/dev/null 2>&1 || true
  fi
fi

echo ""
echo "=============================================================================================================="
echo " DONE -- EVERYTHING FOR $DATE IS READY FROM ONE COMMAND:"
echo "   1. Share in Chat (1 .txt, NO .zip!) : data\\xray_${DATE}.txt"
echo "      (Executive X-Ray Funnel/Vetoes/AI/Basis/Hygiene + ALL embedded text/CSV/JSON reports in one file)"
echo "   2. Browser Dashboard (1 HTML page)  : data\\report_${DATE}.html"
echo "   3. All Audited Days / Raw Log       : data\\index.html  |  data\\evening_suite_${DATE}.log"
echo "=============================================================================================================="
exit 0
