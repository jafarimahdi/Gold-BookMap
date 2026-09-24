#!/usr/bin/env bash
# preflight.sh -- "do I actually have everything, and is it the right version?"
# Run from the project folder:   bash preflight.sh [YYYY-MM-DD]
# Read-only. It changes nothing. Paste the whole output back to me if anything is [MISSING]/[DIFFERENT].

cd "$(dirname "$0")" || exit 1
# if we were run from elsewhere, fall back to the current folder when it has main.py
if [ ! -f main.py ] && [ -f "$PWD/main.py" ]; then cd "$PWD" || exit 1; fi
DAY="${1:-$(date -d yesterday +%F 2>/dev/null || date +%F)}"
ok=0; warn=0; bad=0
P(){ printf "\033[32m[OK]\033[0m %s\n" "$1"; ok=$((ok+1)); }
W(){ printf "\033[33m[WARN]\033[0m %s\n" "$1"; warn=$((warn+1)); }
F(){ printf "\033[31m[MISSING]\033[0m %s\n" "$1"; bad=$((bad+1)); }
D(){ printf "\033[31m[DIFFERENT]\033[0m %s\n" "$1"; bad=$((bad+1)); }

echo "=============================================================="
echo " GOLD-BOOKMAP PREFLIGHT   day under test: $DAY"
echo " folder: $(pwd)"
echo "=============================================================="

# ---------- 1. the auditor itself -----------------------------------------
echo; echo "--- 1. audit tool"
if [ -f audit_day.py ]; then
  SZ=$(stat -c %s audit_day.py 2>/dev/null || wc -c < audit_day.py)
  [ "$SZ" -gt 60000 ] && P "audit_day.py present ($SZ bytes)" \
                      || D "audit_day.py only $SZ bytes - that is not the 15-test version, re-copy it"
else
  F "audit_day.py NOT in this folder - this is why python says 'can't open file'"
  echo "     fix:  cp ~/Downloads/Gold-BookMap-AUDIT-KIT/audit_day.py ."
fi
if [ -f ../audit_day.py ]; then W "audit_day.py found in the PARENT folder, not here -> cp ../audit_day.py ."; fi

# ---------- 2. files the auditor and the patch need -----------------------
echo; echo "--- 2. key files (size tells me which version you hold)"
if [ -f main.py ]; then
  SZ=$(stat -c %s main.py)
  if grep -q "_judge_panel_from_notes" main.py; then P "main.py $SZ = judge-patched (diary keeps per-judge votes)"
  elif grep -q "snapshots_history.jsonl" main.py; then W "main.py $SZ = P3 -> diary saves notes[:50] only; auditor re-parses them (optional v7.1 patch makes it exact)"
  else F "main.py $SZ has no snapshots_history writer -> too old to audit the diary; apply the v7.1 patch"; fi
else F "main.py missing - wrong folder?"; fi

if [ -f step2_market_analysis.py ]; then
  SZ=$(stat -c %s step2_market_analysis.py)
  if [ "$SZ" -gt 205000 ] && grep -q "parse_judge_panel" step2_market_analysis.py; then
    P "step2_market_analysis.py $SZ = judge-patched (writes structured judge_votes)"
  elif grep -q "parse_judge_panel" step2_market_analysis.py; then
    P "step2_market_analysis.py has parse_judge_panel ($SZ bytes)"
  else
    W "step2_market_analysis.py $SZ = base build, no judge panel (expected; optional patch fixes it)"
  fi
else F "step2_market_analysis.py missing"; fi
[ -f step2_market_analysis_v5.9_backup.py ] && P "step2 v5.9 backup present (nice, you can always revert)"

# ---------- 2b. judge recorder -------------------------------------------
echo; echo "--- 2b. judge recorder (needed for test 9 = per-judge behaviour)"
if [ -f judge_panel.py ]; then P "judge_panel.py present -> diary will store every judge vote";
else W "judge_panel.py absent -> diary keeps notes only; auditor re-parses them (partial)"; fi
if grep -q "_judge_panel_from_notes" main.py 2>/dev/null; then P "main.py is the judge-patched version";
else W "main.py is P3 -> diary writes notes[:50], no judge_votes (optional patch fixes it)"; fi
if [ -f data/snapshots_history.jsonl ]; then
  N=$(grep -c judge_votes data/snapshots_history.jsonl 2>/dev/null); N=${N:-0}
  [ "$N" -gt 0 ] && P "diary already contains judge_votes in $N lines" || W "diary has no judge_votes field -> test 9 runs in retro-parse mode"
fi

# ---------- 3. configuration ---------------------------------------------
echo; echo "--- 3. .env (ls never shows it, so it needs its own check)"
if [ -f .env ]; then
  P ".env exists ($(stat -c %s .env) bytes)"
  for kv in "BOOKMAP_WINDOW_SECONDS=10800" "BOOKMAP_MAX_DEPTH_LEVELS=20" \
            "CONFIDENCE_THRESHOLD=50" "AI_MIN_SIGNAL_STRENGTH=6" "V6_CFD_SPREAD_MAX=0.50" \
            "TRADING_ENABLED=1" "BUDAPEST_START=08:00" "BUDAPEST_END=23:00" "DATA_SOURCE=bookmapbridge"; do
    k="${kv%%=*}"
    if grep -q "^${k}=" .env; then
      v=$(grep "^${k}=" .env | tail -1 | cut -d= -f2 | tr -d ' \r')
      [ "$v" = "${kv#*=}" ] && P "  $k = $v" || D "  $k = $v   (expected ${kv#*=})"
    else
      F "  $k not set in .env"
    fi
  done
  for p in BOOKMAP_BRIDGE_FILE BOOKMAP_MBO_FILE; do
    line=$(grep -m1 "^${p}=" .env || true)
    if [ -n "$line" ]; then
      val=$(echo "$line" | cut -d= -f2- | tr -d ' \r')
      slash=$(printf '%s' "$val" | sed -e 's#\\#/#g' -e 's#^\([A-Za-z]\):#/\1#')
      if [ -f "$val" ]; then P "  $p -> $val exists"
      elif [ -f "$slash" ]; then P "  $p -> $slash exists (windows path read as unix)"
      else D "  $p -> $val NOT FOUND (this alone kills every signal)"; fi
    else
      F "  $p not set in .env - the robot will not know where BookMap writes"
    fi
  done
  GK=$(grep "^GEMINI_API_KEY=" .env | tail -1 | cut -d= -f2 | tr -d ' \r')
  CAP=$(grep "^AI_MAX_CALLS_PER_DAY=" .env | tail -1 | cut -d= -f2 | tr -d ' \r')
  if [ -z "$GK" ]; then
    F "  GEMINI_API_KEY is EMPTY -> STEP 3 AI cannot run (0 AI calls all day)"
  else
    P "  GEMINI_API_KEY set (${#GK} chars)"
    if [ "$CAP" = "0" ]; then
      W "  AI_MAX_CALLS_PER_DAY=0 -> UNLIMITED Gemini calls; with a real key set it to 2000"
    else
      P "  AI quota guard active (AI_MAX_CALLS_PER_DAY=${CAP:-2000 default})"
    fi
  fi
  if [ -n "$GK" ] && [ "$CAP" = "0" ]; then
    grep -q "_judge_panel_from_notes" main.py 2>/dev/null \
      || W "  key is live but main.py is P3 -> diary has no judge_votes; apply v7.1 before the AI day"
  fi
  DUP=$(grep -E "^[A-Z0-9_]+=" .env | cut -d= -f1 | sort | uniq -d | tr '\n' ' ')
  if [ -n "$DUP" ]; then W "  .env has DUPLICATE keys (last wins, same as config.py): $DUP"; else P "  no duplicate keys in .env"; fi
else
  F ".env MISSING in this folder -> everything falls back to old defaults (conf 70, AI gate 10, spread 0.60)"
fi

# ---------- 4. the evidence for that day ---------------------------------
echo; echo "--- 4. evidence for $DAY"
T="${BOOKMAP_BRIDGE_FILE:-ticks.csv}"
for f in ticks.csv mbo.csv; do
  if [ -f "$f" ]; then
    SZ=$(stat -c %s "$f")
    N=$(grep -c "^$DAY" "$f" 2>/dev/null); N=${N:-0}
    if [ "$N" -gt 1000 ]; then P "  $f: $N rows dated $DAY ($SZ bytes)"
    elif [ "$SZ" -gt 50000 ]; then W "  $f: $SZ bytes but $N rows for $DAY -> the day you want is not inside (rotated?) ; total rows: $(wc -l < "$f")"
    else F "  $f: only $SZ bytes -> BookMap addon wrote nothing"; fi
  else F "  $f missing"; fi
done
for f in data/snapshots_history.jsonl data/decisions_log.csv; do
  if [ -f "$f" ]; then
    N=$(grep -c "$DAY" "$f" 2>/dev/null); N=${N:-0}
    [ "$N" -gt 0 ] && P "  $f: $N lines for $DAY" || W "  $f: present, 0 lines for $DAY -> test 9 (judges) has nothing to grade"
  else F "  $f missing"; fi
done
L="logs/trading_$(echo "$DAY" | tr -d '-').log"
if [ -f "$L" ]; then P "  $L: $(wc -l < "$L") lines"; else F "  $L missing -> the run for that day left no log"; fi
S=$(ls data/ | grep -c jsonl) ; [ "$S" -gt 0 ] && P "  data/ has jsonl history file(s)"

# ---------- 5. python sanity ---------------------------------------------
echo; echo "--- 5. python"
PY=$(command -v python || command -v python3)
if [ -n "$PY" ]; then P "using $PY ($($PY -V 2>&1))"; else F "python not on PATH"; fi
[ -f requirements.txt ] && P "requirements.txt present"
if [ -n "$PY" ] && [ -f audit_day.py ]; then
  $PY -c "import ast,sys;ast.parse(open(sys.argv[1],encoding='utf-8').read())" audit_day.py \
     && P "audit_day.py parses cleanly" || D "audit_day.py does not parse - copy is broken"
fi

echo; echo "=============================================================="
echo " RESULT: $ok ok, $warn warnings, $bad blocking"
[ "$bad" -gt 0 ] && echo " Fix the [MISSING]/[DIFFERENT] lines first, then run:" && echo "   python audit_day.py --date $DAY" || echo " Good to go:  python audit_day.py --date $DAY"
echo "=============================================================="
