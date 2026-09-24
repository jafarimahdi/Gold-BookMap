#!/usr/bin/env bash
# ============================================================================
#  morning_check.sh - "is everything actually working?" in one command.
#     bash morning_check.sh            # right now, before you start the robot
#     bash morning_check.sh --live     # 15 min AFTER main.py is running
#  Reads nothing, writes nothing. Every line says PASS / FAIL / WAIT and why.
# ============================================================================
cd "$(dirname "$0")" || { echo "put this file in the project folder"; exit 1; }
LIVE="${1:-}"
DAY=$(date +%F)
P=0; F=0; W=0
ok(){   printf '  [PASS] %s\n' "$1"; P=$((P+1)); }
no(){   printf '  [FAIL] %s\n' "$1"; F=$((F+1)); }
wt(){   printf '  [WAIT] %s\n' "$1"; W=$((W+1)); }
info(){  printf "  [INFO] %s\n" "$*"; }

hr(){   printf '  ---------------------------------------------------------\n'; }

PY=$(command -v python || command -v python3)
TICKS=$(grep -E "^BOOKMAP_BRIDGE_FILE=" .env 2>/dev/null | tail -1 | cut -d= -f2-)
# A:\gitHub\...\file.csv  ->  /a/gitHub/.../file.csv   (Git Bash can read it)
if [ -n "$TICKS" ]; then
  case "$TICKS" in
    *:*) T="$(printf '%s' "$TICKS" | sed -e 's/\\/\//g' -e 's/^\([A-Za-z]\):/\/\L\1/')" ;;
    *)   T="$TICKS" ;;
  esac
  [ -f "$T" ] || T="ticks.csv"
else
  T="ticks.csv"
fi

echo
echo "== 1. the files you installed tonight =================================="
# audit_day.py is verified by its BUILD marker, not by size: sizes change every
# time we fix something, and a stale constant here once called a good install bad.
if [ -f audit_day.py ] && grep -q "BUILD = \"audit-2026-09-24d\"" audit_day.py; then
  ok "audit_day.py is the build the kit shipped ($(stat -c %s audit_day.py) bytes, marker present)"
elif [ -f audit_day.py ]; then
  no "audit_day.py has no kit build marker ($(stat -c %s audit_day.py) bytes) - re-copy it from the kit"
else
  no "audit_day.py missing from $(pwd)"
fi
# daily_check.sh and morning_check.sh check themselves by parsing, never by size -
# a size constant inside a file it measures can never be right after an edit.
for f in dashboard.py:1226 tools/dashboard.py:31843 judge_panel.py:15606 main.py:48991; do
  n=${f%%:*}; want=${f##*:}
  if [ -f "$n" ]; then
    sz=$(stat -c %s "$n")
    [ "$sz" = "$want" ] && ok "$n ($sz bytes)" || no "$n is $sz bytes, expected $want - re-copy it from the kit"
  else
    no "$n missing from $(pwd)"
  fi
done
for sh in morning_check.sh daily_check.sh; do
  if [ -f "$sh" ] && bash -n "$sh" 2>/dev/null; then ok "$sh present and parses"; else no "$sh missing or half-copied"; fi
done

echo
echo "== 1b. is the clock you trade on the clock the market uses? ==========="
ml=$(date '+%H:%M:%S' 2>/dev/null); mu=$(date -u '+%H:%M:%S' 2>/dev/null)
tz=$(date '+%z' 2>/dev/null)
echo "   machine-local $ml  machine-utc $mu  offset $tz  (want offset +0200 for Budapest)"
ru=""
command -v curl >/dev/null 2>&1 && ru=$(curl -sI --max-time 5 https://www.google.com 2>/dev/null \
  | sed -n 's/^[Dd]ate: //p' | tr -d '\r')
if [ -n "$ru" ]; then
  ru=$(printf '%s\n' "$ru" | date -u -f - '+%H:%M:%S' 2>/dev/null || echo "?")
  echo "   real-utc $ru  (machine-utc should match within a minute; the trading window is"
  echo "     read in real Budapest time - including the DST change - so clock drift is the"
  echo "     only thing that can still shift the 08:00 open)"
else
  echo "   real-utc unknown (no curl/no network) - not a fault, just unchecked"
fi

echo
echo "== 2. .env ============================================================"
if [ -f .env ]; then
  ok ".env exists ($(stat -c %s .env) bytes)"
  for k in BOOKMAP_WINDOW_SECONDS CONFIDENCE_THRESHOLD AI_MIN_SIGNAL_STRENGTH V6_CFD_SPREAD_MAX AI_MAX_CALLS_PER_DAY BUDAPEST_TRADING_ONLY GEMINI_API_KEY; do
    c=$(grep -c "^$k=" .env)
    v=$(grep "^$k=" .env | tail -1 | cut -d= -f2-)
    [ "$k" = "GEMINI_API_KEY" ] && v="set, ${#v} chars"
    if [ "$c" = "1" ]; then ok "$k = $v"; else no "$k present $c times (want exactly 1)"; fi
  done
  DUP=$(grep -E "^[A-Z0-9_]+=" .env | cut -d= -f1 | sort | uniq -d | tr '\n' ' ')
  [ -z "$DUP" ] && ok "no duplicate keys" || no "duplicate keys: $DUP - run the dedupe in NEXT_STEPS.md STEP 5"
  [ -f .env.bak ] || true
else
  no ".env missing - the robot will run on old defaults"
fi

echo
echo "== 3. can it even compile (nothing will crash at 08:01) ==============="
if $PY -m py_compile main.py judge_panel.py audit_day.py dashboard.py tools/dashboard.py >/dev/null 2>&1; then
  ok "main.py + judge_panel.py + audit kit all compile"
else
  no "python cannot compile the tree - paste me: $PY -m py_compile main.py"
fi
$PY -c "import config, session, datetime as d; n=d.datetime.now(d.timezone.utc); raise SystemExit(0 if not session.is_market_open(n) else 1)" 2>/dev/null \
  && ok "session guard is live: is_market_open() says closed before 08:00 (no accidental trades)" \
  || wt "is_market_open() says OPEN right now - fine after 08:00, suspicious before it"

echo
echo "== 4. is BookMap actually writing the tape ============================"
if [ -f "$T" ]; then
  sz=$(stat -c %s "$T"); t1=$(stat -c %Y "$T")
  ok "feed file found: $T ($sz bytes)"
  if [ "$sz" -gt 5000000 ]; then ok "file is $((sz/1024/1024)) MB of real data"
  elif [ "$sz" -gt 100000 ]; then wt "only $((sz/1024)) KB so far - fine at the open, wrong by 09:00"
  else no "file is $sz bytes - the add-on is not writing"; fi
  TODAY=$(grep -c "^$DAY" "$T")
  [ "$TODAY" -gt 0 ] && ok "$TODAY rows for today ($DAY)" || no "0 rows for today in $T - BookMap has not started recording this session"
  PRINTS=$(grep "^$DAY" "$T" | grep -c ",Last,")
  HR=$(date +%H)
  if [ "$PRINTS" -gt 500 ]; then
    ok "$PRINTS trade prints (Last) for today -> the footprint judges have material"
  elif [ "$PRINTS" -gt 0 ]; then
    wt "only $PRINTS prints so far - depth is flowing but trades are not. In BookMap, start the print/tape recording, then re-run this check"
  else
    if [ "$HR" -ge 8 ] 2>/dev/null; then
      no "ZERO trade prints today while the market is open ($HR:xx) - yesterday's exact failure: depth rows yes, 'Last' rows no"
    else
      wt "no prints yet (before 08:00 that is normal). Re-run this script at 08:15 - it must show hundreds"
    fi
  fi
  echo "  ... waiting 20 s to measure whether the file is growing ..."
  sleep 20
  sz2=$(stat -c %s "$T")
  if [ "${sz2:-0}" -gt "$sz" ]; then
    ok "tape is LIVE: grew $((sz2 - sz)) bytes in 20 s (~$(( (sz2 - sz) * 3 / 1024 )) KB/min)"
  elif [ "$LIVE" = "--live" ]; then
    no "file FROZEN while main.py runs - BookMap stopped writing. Fix this before you trust anything today"
  else
    wt "no growth in 20 s - one quiet minute is normal; run it again, two in a row means the add-on is stuck"
  fi
  # one pass over today's rows gives both lines plus the around-the-clock proof
  grep "^$DAY" "$T" | awk -F, '{h=substr($1,12,2)+0; r[h]++; if (index($0,",Last,")) p[h]++}
    END {printf "  rows/hour  :"; for (h=0;h<24;h++) if (r[h]) printf " %02d:%d", h, r[h]; print "";
         printf "  prints/hour:"; for (h=0;h<24;h++) if (p[h]) printf " %02d:%d", h, p[h]; print "";
         o=0; for (h=0;h<8;h++) o+=p[h]; o+=p[23];
         printf "  prints in the hours you do NOT trade (00-07, 23): %d\n", o}'
  # ---- rotated chunks: the hourly lines above are the LIVE file only -------------
  D8=$(date +%Y%m%d)          # rotated files use the compact date; $DAY above is ISO
  ROT=$(ls -1 ticks_${D8}*.csv ticks_${D8}*.csv.gz data/archive/ticks_${D8}* 2>/dev/null | sort -u)
  if [ -n "$ROT" ]; then
    echo "  rotated chunk(s) for today (the hourly lines above are the LIVE file only):"
    NROT=0
    for f in $ROT; do
      [ -f "$f" ] || continue
      case "$f" in
        *.gz) c=$(gzip -dc "$f" 2>/dev/null | grep -c "^$DAY" 2>/dev/null || true) ;;
        *)    c=$(grep -c "^$DAY" "$f" 2>/dev/null || true) ;;
      esac
      c=${c:-0}
      NROT=$((NROT + c))
      echo "    $f  ($(stat -c %s "$f") bytes, $c rows for today)"
    done
    echo "    -> those $NROT row(s) are NOT lost, they were rotated out of the live file."
    echo "       The day report reads the chunks too, so hours that look empty above are"
    echo "       still graded tonight:  python audit_day.py --date $(date +%F)"
  fi
  echo "  -> those rows are kept and graded: the report now covers the WHOLE day, so you can"
  echo "     start the app at any hour and the tape is still worth reading later."
  echo "  report commands:  python audit_day.py            (today, whole day, no hour filter)"
  echo "                    python audit_day.py --days 3   (last 3 days: day rows + totals)"
  echo "                    python audit_day.py --days 8   (last 8 days)"
  echo "                    python audit_day.py --window   (only 08:00-23:00, the old view)"
else
  no "cannot find the feed file ($T) - check BOOKMAP_BRIDGE_FILE in .env"
fi

echo
echo "== 5. MT5 side ========================================================"
for f in mt5_signal.txt data/market_snapshot.json data/tracked_bot_positions.json; do
  if [ -f "$f" ]; then
    m=$(find "$f" -newermt "-18 hours" 2>/dev/null | wc -l | tr -d ' ')
    [ "$m" = "1" ] && ok "$f touched within 18 h" || wt "$f is older than 18 h (normal before the open)"
  else
    wt "$f not written yet"
  fi
done
# that file is APPEND-ONLY bookkeeping: nothing in main.py ever removes an id, and
# step5 asks MT5 directly for open trades. So "13 ids in the file" never meant "13 open
# positions", and this line must not send you hunting in MT5 for something that is not there.
if [ -f data/tracked_bot_positions.json ]; then
  TIDS=$(grep -o '[0-9]\{5,\}' data/tracked_bot_positions.json 2>/dev/null | wc -l | tr -d ' ')
  TUPD=$(grep -o '"updated_at"[^,}]*' data/tracked_bot_positions.json 2>/dev/null | cut -d'"' -f4)
  info "position-tracking file lists $TIDS id(s), last written ${TUPD:-unknown}"
  echo "         bookkeeping, not a register: ids are kept so closed trades can be looked up"
  echo "         in MT5 history later, and they are never removed. Open trades only exist in"
  echo "         the MT5 Trade tab - if that tab is empty, nothing is open, whatever this says."
else
  wt "tracked_bot_positions.json not written yet"
fi

echo
echo "== 6. quota / AI key ==================================================="
if [ -f data/ai_calls.json ]; then
  ok "data/ai_calls.json exists ($(stat -c %s data/ai_calls.json) bytes) -> the daily counter is being written"
  $PY -c "
import json,sys
try:
    d=json.load(open('data/ai_calls.json'))
except Exception as e:
    print('  [WAIT] ai_calls.json unreadable (%s) - harmless' % e); sys.exit(0)
cap=0
try:
    cap=int(open('.env').read().split('AI_MAX_CALLS_PER_DAY=')[1].split()[0])
except Exception: pass
print('  [INFO] today\'s calls recorded: %s | cap: %s' % (json.dumps(d)[:160], cap))"
else
  no "data/ai_calls.json missing - the quota cap has nothing to count (STEP 3 compile check covers the rest)"
fi
KEYN=$($PY - <<'EOPY' 2>/dev/null
import re
from pathlib import Path
try:
    txt = Path(".env").read_text()
except Exception:
    print("?"); raise SystemExit
keys = re.findall(r"^(GEMINI_API_KEY(?:_\d+)?)\s*=\s*(\S*)", txt, re.M)
live = [k for k, v in keys if len(v) > 20]
print(f"{len(live)}/{len(keys)}")
EOPY
)
case "${KEYN:-?}" in
  0/*) no "no usable Gemini key in .env (${KEYN}) - every cycle falls back to HOLD, confidence 0.0" ;;
  ?)   wt "could not count GEMINI_API_KEY entries" ;;
  *)   ok "usable Gemini keys in .env: $KEYN (more than 1 = real rotation)" ;;
esac
if [ -f .env ] && [ "${KEYN%%/*}" = "1" ]; then
  wt "you have ONE key active - add GEMINI_API_KEY_2.. up to _6 in .env (one per line,
         same name pattern); step3 rotates to the next key on a rate-limit instead of dying"
fi
LOG="logs/trading_$(date +%Y%m%d).log"
LOCK="data/bot.lock"
# --- liveness housekeeping: the lock is what decides "running", not the log ----
# main.py keeps data/bot.lock fresh (its own mtime) once per loop iteration and
# refuses a second instance while the holder is alive. So: fresh lock = a bot is
# running; stale/absent lock = nothing is, whatever the log says.
LOCKFRESH=0; LOCKAGE=0; LPID=""
if [ -f "$LOCK" ]; then
  LOCKAGE=$(( $(date +%s) - $(stat -c %Y "$LOCK") ))
  LPID=$(tr -d ' \r\n' < "$LOCK" 2>/dev/null)
  [ "$LOCKAGE" -le 300 ] && LOCKFRESH=1
fi
LOOPN=""
[ -f "$LOG" ] && LOOPN=$(grep -c "loop iteration" "$LOG" 2>/dev/null)
H=$(date +%H); H=${H#0}; [ -z "$H" ] && H=0
if [ "$H" -ge 8 ] && [ "$H" -lt 23 ]; then INWIN=1; else INWIN=0; fi

if [ -f "$LOG" ]; then
  # what KIND of AI failure is it? one line, even when several kinds are mixed
  CLS=$(grep -h "call failed" "$LOG" 2>/dev/null | sed -E 's/.*(rate ?limit|429|quota).*/quota-exhausted/; s/.*(key not valid|PERMISSION|INVALID_ARGUMENT|API key).*/bad-key/; s/.*(timed? ?out|DEADLINE_EXCEEDED|Deadline expired|504).*/google-timeout/; s/.*(Connection|UNREACHABLE|resolve).*/network/; s/.*(500|503|unavailable|overloaded).*/google-5xx/' | sort | uniq -c | sort -rn | awk '{n=$1; $1=""; sub(/^ +/,""); printf "%s x%d, ", $0, n}' | sed 's/, $//')
  [ -n "$CLS" ] && echo "         why it fails: $CLS"
  AGE=$(( $(date +%s) - $(stat -c %Y "$LOG") ))
  if [ "$AGE" -lt 120 ]; then
    ok "log written ${AGE}s ago -> the loop is running"
  elif [ "$INWIN" = "1" ]; then
    if [ "$AGE" -lt 900 ]; then
      wt "log quiet for $((AGE/60)) min INSIDE your trading window - a lunch-time hole is possible, a 30 min hole is a dead robot"
    else
      no "NO LOG LINE for $((AGE/60)) min INSIDE your 08:00-23:00 window - the robot stopped thinking while BookMap keeps writing. This is the failure that eats a day"
    fi
  else
    # before 08:00 / after 23:00 the loop is quiet by design - the lock decides
    if [ "$LOCKFRESH" = "1" ]; then
      wt "log quiet for $((AGE/60)) min, but that is OUTSIDE your 08:00-23:00 window and data/bot.lock is fresh (${LOCKAGE}s ago) -> the loop is alive; nothing to fix"
    else
      no "log quiet for $((AGE/60)) min, OUTSIDE your window, and data/bot.lock is NOT fresh -> nothing is running. Start it: python main.py --loop"
    fi
  fi
  FAILS=$(grep -c "call failed" "$LOG" 2>/dev/null)
  # name the keys that actually failed instead of blaming whichever key we last
  # argued about, and split "the key is dead" from "Google refused the call".
  FKEYS=$(grep -h "call failed" "$LOG" 2>/dev/null | grep -o "key #[0-9]*" | sort -u | tr '\n' ' ')
  NTIME=$(grep -h "call failed" "$LOG" 2>/dev/null | grep -cE "DEADLINE_EXCEEDED|Deadline expired|504|timed? ?out")
  NBAD=$(grep -h "call failed" "$LOG" 2>/dev/null | grep -cE "key not valid|PERMISSION|INVALID_ARGUMENT|API key")
  NQUOTA=$(grep -h "call failed" "$LOG" 2>/dev/null | grep -cE "rate ?limit|429|quota")
  NSRV=$(grep -h "call failed" "$LOG" 2>/dev/null | grep -cE "500|503|unavailable|overloaded")
  if [ "${FAILS:-0}" = "0" ]; then
    ok "0 'call failed' lines in today's log so far"
  else
    no "$FAILS 'call failed' lines so far -> failing keys: ${FKEYS:-?} | deadline:$NTIME google-5xx:$NSRV key-bad:$NBAD quota:$NQUOTA"
    if [ "${NBAD:-0}" -gt 0 ]; then
      echo "         dead/invalid keys -> drop those GEMINI_API_KEY lines from .env, keep the good ones"
    elif [ "${NQUOTA:-0}" -gt 0 ]; then
      echo "         quota reached -> add another key or raise AI_MAX_CALLS_PER_DAY (you are at 2000)"
    elif [ "${NSRV:-0}" -gt 0 ] && [ "${NSRV:-0}" -ge "${NTIME:-0}" ]; then
      echo "         Gemini itself refused the calls (5xx / overloaded): every key in the rotation"
      echo "         got the same answer within seconds, so there is nothing to rotate and no"
      echo "         setting of yours to change. Those cycles fall back to HOLD. If it repeats all"
      echo "         day the honest lever is retry-with-backoff around the key rotation."
    elif [ "${NTIME:-0}" -gt 0 ]; then
      TOS=$(sed -n 's/^AI_TIMEOUT_SECONDS=[[:space:]]*\([0-9]\+\).*/\1/p' .env 2>/dev/null | tail -1)
      TOS=${TOS:-15}; [ "$TOS" -lt 15 ] && TOS=15
      echo "         these keys are NOT dead - Gemini answered slower than your deadline and step3"
      echo "         does not retry (by design). Effective deadline right now: ${TOS}s"
      if [ "$TOS" -lt 30 ]; then
        echo "         Fix the knob, not the key - add this line to .env, leave everything else,"
        echo "         then restart main.py:"
        echo "             AI_TIMEOUT_SECONDS=30"
      else
        echo "         Your deadline is already ${TOS}s, so this is Google being slow, not your config."
        echo "         Either add another GEMINI_API_KEY_n (more rotation) or accept the odd lost cycle."
      fi
    fi
  fi
else
  if [ "$LIVE" = "--live" ]; then no "main.py was started but $LOG does not exist - it died on startup, paste me the console output"
  else wt "no log for today yet ($LOG) - appears when main.py starts"; fi
fi

hr
echo "== 6b. is the robot alive, and is it LOOPING? =========================="
if [ "$LOCKFRESH" = "1" ]; then
  ok "data/bot.lock fresh (touched ${LOCKAGE}s ago, PID ${LPID:-?}) -> a bot holds the single-instance lock right now"
elif [ -f "$LOCK" ]; then
  no "data/bot.lock exists but is $((LOCKAGE/60)) min old (PID ${LPID:-?}) -> that holder is dead or the window was closed. A new 'python main.py --loop' takes it over by itself (nothing to clean up)"
else
  no "no data/bot.lock at all -> no bot has taken the lock today. Nothing is running."
fi
if [ -n "$LOOPN" ]; then
  if [ "${LOOPN:-0}" -gt 0 ]; then
    ok "today's log has $LOOPN 'loop iteration' line(s) -> main.py was started with --loop"
  else
    no "today's log has 0 'loop iteration' lines: every run today did ONE cycle and exited"
    echo "         Bare 'python main.py' runs the pipeline once and quits - that is the"
    echo "         default, and it is why a log can stop without anything being broken."
    echo "         For a full day with the brain awake:   python main.py --loop"
    echo "         (a second window is refused while the first is alive, so it is safe to try)"
  fi
fi

hr
if [ "$F" = "0" ]; then
  if [ "$W" = "0" ]; then echo "  RESULT: $P PASS, 0 FAIL, 0 WAIT - start main.py, everything is verified."
  else echo "  RESULT: $P PASS, 0 FAIL, $W WAIT - safe to start; the WAIT lines are time-of-day effects, re-check at 08:15."; fi
else
  echo "  RESULT: $P PASS, $F FAIL, $W WAIT - DO NOT start the day as-is, fix the [FAIL] lines first."
  echo "          Paste me this output and I'll tell you exactly which knob it is."
fi
echo
