#!/usr/bin/env bash
# report_evening.sh -- the ONE evening command (the report team's chief of staff).
#   bash report_evening.sh              # today's paper
#   bash report_evening.sh 2026-10-09   # a specific day
#   bash report_evening.sh --full       # also re-run the Editor (audit_day) first, ~1-3 min
#
# Runs the whole report team in order and prints TODAY'S PAPER:
#   1. the Accountant (decision_ledger.py)  - who stopped each signal, was it right
#   2. the Geometer   (tf_study.py)         - edge persistence across exit geometries
#   3. the Shadow     (shadow_replay.py)    - the robot's OWN wall logic on the day
#   4. the Lens       (tf_lens.py)          - would finer clocks have SEEN more? (M2/M3/M8/M10 vs M5)
#   5. the Anatomist  (fish_anatomy.py)     - the day's fish opened up: WHEN/SIZE/HEAT/PANEL
#   6. the Editor's verdict (from data/day_metrics_<date>.json if present)
#   7. the Watchman's day (data/tape_watchdog.log lines for the date)
# plus the RECONCILIATION LINE: Accountant total vs Geometer 'M5 (audit)' row vs
# the Editor's what-if. All three must tell the same story.
# v1.2: desks 4+5 added (the measurement tools). --fast skips them (old 3-desk paper).
#
# Read-only with respect to trading: these are report tools; the robot never
# imports them. Outputs land in data/ as always.
cd "$(dirname "$0")" || { echo "put this file in the project folder"; exit 1; }

PY=$(command -v python || command -v python3)
DATE=""
FULL=0
FAST=0
for a in "$@"; do
  case "$a" in
    --full) FULL=1 ;;
    --fast) FAST=1 ;;
    *) DATE="$a" ;;
  esac
done
[ -z "$DATE" ] && DATE=$(date +%Y-%m-%d)
COMPACT=${DATE//-/}

hr() { printf '%s\n' "--------------------------------------------------------------------------------------------------------------"; }
title() { printf '\n%s\n%s\n%s\n' "==============================================================================================================" "$1" "=============================================================================================================="; }

title "TODAY'S PAPER - $DATE  (report_evening.sh - the one evening command)"

if [ "$FULL" = 1 ]; then
  echo "[chief of staff] --full: running the Editor first (audit_day, ~1-3 min)..."
  GBM_NO_BROWSER=1 "$PY" audit_day.py --date "$DATE" --no-browser >/dev/null 2>&1 \
    && echo "[chief of staff] Editor done." \
    || echo "[chief of staff] Editor failed or no data - continuing with the desks."
fi

echo "[chief of staff] desk 1/5: the Accountant (decision ledger)..."
"$PY" decision_ledger.py --date "$DATE" | sed 's/^/  /'
hr
echo "[chief of staff] desk 2/5: the Geometer (edge persistence)..."
"$PY" tf_study.py --date "$DATE" | sed 's/^/  /'
hr
echo "[chief of staff] desk 3/5: the Shadow (the robot's own wall logic)..."
"$PY" shadow_replay.py --date "$DATE" | sed 's/^/  /'
hr

# ---- v1.2: the measurement desks (skip with --fast) ----------------------------
if [ "$FAST" = 1 ]; then
  echo "[chief of staff] desks 4-5 skipped (--fast: the quick paper)"
else
  if [ -f tf_lens.py ]; then
    echo "[chief of staff] desk 4/5: the Lens (timeframe glasses, ~1-3 min)..."
    "$PY" tf_lens.py --date "$DATE" | sed 's/^/  /'
  else
    echo "[chief of staff] desk 4/5: the Lens is not installed (tf_lens.py missing) - skipping."
  fi
  hr
  if [ -f fish_anatomy.py ] && [ -f tf_lens.py ]; then
    echo "[chief of staff] desk 5/5: the Anatomist (fish dissection, ~1-3 min)..."
    "$PY" fish_anatomy.py --date "$DATE" | sed 's/^/  /'
  else
    echo "[chief of staff] desk 5/5: the Anatomist needs fish_anatomy.py + tf_lens.py - skipping."
  fi
fi
hr

# ---- the Editor's own numbers, if the day was audited -------------------------
# (audit_day saves day_metrics with DASHES in the date: day_metrics_2026-10-08.json)
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
    print("  (no verdict/what-if keys found - re-run: bash daily_check.sh " + sys.argv[1].split("day_metrics_")[-1].split(".")[0].replace("-","-") + ")")
for p, v in rows[:12]:
    print(f"  {p:<40} {v}")
PYEOF
else
  echo "[chief of staff] the Editor has not graded this day yet (no $METRICS)."
  echo "  for the Editor's own grade:  bash daily_check.sh $DATE   (or rerun with --full)"
fi
hr

# ---- the Watchman's day --------------------------------------------------------
WL="data/tape_watchdog.log"
if [ -f "$WL" ]; then
  echo "[chief of staff] the Watchman's day (from $WL):"
  grep "^$DATE " "$WL" | sed 's/^/  /' || echo "  (no entries for this date - watchdog not running that day?)"
else
  echo "[chief of staff] no watchdog log (data/tape_watchdog.log) - the Watchman did not run."
fi
hr

# ---- the reconciliation line ----------------------------------------------------
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
echo "[chief of staff] today's outputs:"
ls -la "data/decision_ledger_${DATE}".* "data/tf_study_${DATE}".* "data/shadow_replay_${DATE}".* "data/tf_lens_${DATE}".* "data/fish_anatomy_${DATE}".* 2>/dev/null | awk '{print "  " $NF " (" $5 " bytes)"}'
echo ""
echo "[chief of staff] done. The paper is above; the files are in data/."
