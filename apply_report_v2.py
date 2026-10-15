#!/usr/bin/env python3
"""
apply_report_v2.py -- REPORT TEAM v2: teach the report office the new organs
============================================================================
(2026-10-15, owner's order: "update and solve ALL of the report-team problems
now - one file to implement.")

The 2026-10-15 report-team audit found the tools healthy but BLIND to every
organ installed after 2026-10-08 (ledger O12), plus three more defects. This
installer fixes all of it, REPORTING ONLY - the robot is not touched:

  FIX 1 (O12) audit_day.py  - new TEST 17 "NEW ORGANS AND AI COST":
          * POWER receipts: voice_gain (the 10-15 voice fix), force-check
            stamps, reason codes, regime - read from the diary by recursive
            census (works wherever the keys nest)
          * the A2 fast lane: gate runs / FIRES / refusal reasons
            (aggressive_trigger_* stamps, born 10-13)
          * the memory book: memory_status (born 10-14)
          * AI COST: real paid decisions (log), throttled rests, and BELLS
            LOST TO THE REST (strength>=6 while throttled - ledger O11
            evidence), cross-checked with data/ai_calls.json
          * era-aware: organs absent BEFORE their birth date are marked
            "correct, predates birth", never a false alarm
  FIX 2 (O7/O12) audit_day.py test 11 - the config ruler is now era-aware:
          depth 250 -> 150 from 2026-10-13 (owner's .env), the M3 era from
          2026-10-15 (TIMEFRAME=M3, A1_BASE_TF_MINUTES=3, VOICE_GAIN=3.0).
          Old days are graded under the rule that was lawful when they ran.
  FIX 3 (O6) morning_check.sh - the September-era section is retired:
          no more stale BUILD stamp, no more byte-size expectations, and the
          DANGEROUS "re-copy it from the kit" advice (which would erase
          A1/A2/memory/voice) is gone. Capability markers + compile checks only.
  FIX 4 (O11) report_evening.sh v1.4 - new desk 8, the AI COST line
          (paid calls from data/ai_calls.json + a pointer to TEST 17).
  FIX 5    selftest_audit_day.py - the doctor knows test 17 (expected-tests
          list, header regex, pass message) and its fake day carries the
          current lawful .env (150 depth, M3 era) so test 11 stays clean.
  FIX 6    "16 tests" -> "17 tests" everywhere (audit_day header/HTML/help,
          daily_check.sh).

Safety laws (the same as every install in this repo):
  * refuses to run outside the project root (the folder with main.py)
  * refuses on ANY anchor mismatch: prints your file's actual lines, writes
    NOTHING (atomic per file: all edits must anchor, then one write)
  * idempotent: already-patched files answer [skip] (re-running is safe)
  * backups: <file>.pre_reportv2 next to each file, written once, never staged
  * python -m py_compile after .py edits, bash -n after .sh edits; a failed
    check restores the backup automatically (fail-closed)
  * the reconciliation line and every other desk are untouched

Run from the project root:
    python apply_report_v2.py            # install (expect [ok] x5 or [skip])
    python apply_report_v2.py --rig      # prove it: markers + compile + smoke
                                        # + the doctor (selftest, ~1 min)
    python apply_report_v2.py --rollback # put the .pre_reportv2 copies back

Then push (never the .pre_* backups):
    git add audit_day.py tools/selftest_audit_day.py morning_check.sh \\
        daily_check.sh report_evening.sh apply_report_v2.py
"""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path.cwd()
BACKUP_SUFFIX = ".pre_reportv2"

# ------------------------------------------------------------------ test 17 --
TEST17 = '''
def test_17_new_organs(day, diary, text, decisions):
    """NEW ORGANS AND AI COST (report v2, 2026-10-15) - the machinery installed
    after 2026-10-08 was invisible to every desk (the 2026-10-15 report-team
    audit, ledger O12). This test reads the diary's NEW fields and the AI cost:
      * POWER receipts: voice_gain (the 10-15 voice fix), force-check stamps,
        reason codes, regime
      * the A2 fast lane: gate runs, fires, refusal reasons (born 10-13)
      * the memory book: memory_status (born 10-14)
      * AI COST: real paid decisions (log + data/ai_calls.json), throttled
        rests, and bells (strength >= 6) that rang while the doorman rested
        (ledger O11 evidence: the 5-min rest vs the 3-min eyes)
    Grades REPORTING COVERAGE, not trading: a quiet market is not a failure,
    and an organ absent BEFORE its birth date is marked correct, not missing."""
    r = Report(17)
    why = []
    txt = text or ""
    BORN = {"a2": date_cls(2026, 10, 13), "memory": date_cls(2026, 10, 14),
            "voice": date_cls(2026, 10, 15)}

    # ---- 1) recursive census over the diary ------------------------------------
    vg, ms, reasons, regimes = [], [], Counter(), Counter()
    a2_runs, a2_fires, a2_reasons = 0, 0, []
    fc_pass, fc_total, pv2 = 0, 0, 0

    def _walk(o):
        nonlocal a2_runs, a2_fires, fc_pass, fc_total, pv2
        if isinstance(o, dict):
            for k, v in o.items():
                kl = str(k).lower()
                if kl == "power_v2" and isinstance(v, dict):
                    pv2 += 1
                if kl == "memory_status":
                    ms.append(v)
                if not isinstance(v, (dict, list)):
                    if kl == "voice_gain" and isinstance(v, (int, float)):
                        vg.append(round(float(v), 3))
                    elif kl == "aggressive_trigger_confirmed":
                        a2_runs += 1
                        if v is True:
                            a2_fires += 1
                    elif kl == "aggressive_trigger_reason" and v:
                        a2_reasons.append(str(v))
                    elif kl == "reason_code" and v:
                        reasons[str(v)] += 1
                    elif kl == "regime" and isinstance(v, str) and v:
                        regimes[v] += 1
                    elif kl.endswith("force_checks_pass"):
                        fc_total += 1
                        fc_pass += (v is True)
                if isinstance(v, (dict, list)):
                    _walk(v)
        elif isinstance(o, list):
            for v in o:
                _walk(v)

    try:
        for d in diary or []:
            _walk(d)
    except Exception as e:
        why.append(f"census error (survived): {type(e).__name__}: {e}")
    why.append(f"diary records scanned: {len(diary or [])} (recursive key census)")
    why.append(f"POWER answers: {pv2} | top reason codes: "
               + (", ".join(f"{k} x{v}" for k, v in reasons.most_common(3)) or "none seen"))
    if fc_total:
        why.append(f"force-check stamps: {fc_pass}/{fc_total} passing")
    missing = []
    if day >= BORN["voice"] and not vg:
        missing.append("voice_gain (born 10-15, expected from that day)")
    if day >= BORN["memory"] and not ms:
        missing.append("memory_status (born 10-14, expected from that day)")
    if day >= BORN["a2"] and a2_runs == 0:
        missing.append("aggressive_trigger stamps (born 10-13, expected from that day)")
    for m in missing:
        why.append(f"MISSING: {m} -> installed but the diary never shows it: "
                   "tell the assistant (a reporting gap, not necessarily a robot fault)")
    if vg:
        why.append(f"voice_gain: stamped on {len(vg)} records, values {sorted(set(vg))} "
                   "(the 10-15 voice fix, live and visible)")
    if ms:
        appended = sum(1 for m in ms if isinstance(m, dict) and m.get("appended"))
        why.append(f"memory book: {len(ms)} memory_status stamps, {appended} appended")
    if a2_runs:
        top_ref = Counter(str(x).split("(")[0][:60] for x in a2_reasons).most_common(2)
        why.append(f"A2 fast lane: gate ran {a2_runs}x, FIRES {a2_fires}; top refusals: "
                   + (", ".join(f"{k} x{v}" for k, v in top_ref) or "none recorded"))
    if regimes:
        why.append("regime field: " + ", ".join(f"{k} x{v}" for k, v in regimes.most_common(3)))

    # ---- 2) the AI cost truth ---------------------------------------------------
    real_calls = len(re.findall(r"AI DECISION\\s+(?:HOLD|BUY|SELL)\\s*@\\s*[\\d.]+%", txt))
    throttled = skipped_weak = missed_bells = 0
    last_strength = 0.0
    try:
        for line in txt.splitlines():
            m = re.search(r"strength ([\\d.]+)", line)
            if m:
                try:
                    last_strength = float(m.group(1))
                except ValueError:
                    pass
            if "AI throttled" in line:
                throttled += 1
                if last_strength >= 6.0:
                    missed_bells += 1
            elif "AI skipped" in line:
                skipped_weak += 1
    except Exception as e:
        why.append(f"cost scan error (survived): {type(e).__name__}: {e}")
    ai_json = ""
    try:
        with open(DATA_DIR() / "ai_calls.json", encoding="utf-8", errors="ignore") as f:
            j = json.load(f)
        if str(j.get("date")) == f"{day:%Y-%m-%d}":
            ai_json = f"data/ai_calls.json agrees: {j.get('calls')} paid call(s) that day"
        else:
            ai_json = (f"data/ai_calls.json holds {j.get('date')} ({j.get('calls')} calls) - "
                       "it resets daily; the log count above is the day's own")
    except Exception:
        ai_json = "data/ai_calls.json not readable for this day (fine on old days)"
    why.append(f"AI COST: real paid decisions {real_calls} | doorman refusals: "
               f"{throttled} resting, {skipped_weak} below-bell | BELLS LOST TO THE REST "
               f"(strength>=6 while throttled): {missed_bells}")
    if missed_bells:
        why.append(f"-> {missed_bells} moment(s) were interesting enough to ring while the "
                   "doorman rested (ledger O11 evidence: the 5-min rest vs the 3-min eyes)")
    why.append(ai_json)

    if not diary:
        r.add("NA", "NEW ORGANS AND AI COST",
              "do the reports see the NEW machinery, and what did the AI cost?",
              ["no diary records for this day - nothing to census",
               "run the robot for a day, then re-audit"])
        return r, {}
    grade = "WARN" if missing else "PASS"
    if missed_bells >= 3 and grade == "PASS":
        grade = "WARN"
    r.add(grade, "NEW ORGANS AND AI COST",
          "do the reports see the NEW machinery, and what did the AI cost?",
          why)
    return r, {"voice_gain_records": len(vg), "a2_gate_runs": a2_runs,
               "a2_fires": a2_fires, "missed_bells": missed_bells,
               "real_ai_calls": real_calls, "throttled": throttled}

'''

HOOK = '''    # report16: the team's money path + the 12 acceptance points
    reports["cycle"], _cycle = _safe_test(16, "TEAM CYCLE & ACCEPTANCE",
                                          test_16_team_cycle, day, diary, text, decisions)
'''

HOOK_NEW = '''    # report16: the team's money path + the 12 acceptance points
    reports["cycle"], _cycle = _safe_test(16, "TEAM CYCLE & ACCEPTANCE",
                                          test_16_team_cycle, day, diary, text, decisions)
    # report v2 (2026-10-15): the new organs + the AI cost truth (ledger O12/O11)
    reports["organs"], _organs = _safe_test(17, "NEW ORGANS AND AI COST",
                                            test_17_new_organs, day, diary, text, decisions)
    METRICS["new_organs"] = {"voice_gain_records": (_organs or {}).get("voice_gain_records", 0),
                             "a2_gate_runs": (_organs or {}).get("a2_gate_runs", 0),
                             "a2_fires": (_organs or {}).get("a2_fires", 0),
                             "missed_bells": (_organs or {}).get("missed_bells", 0),
                             "real_ai_calls": (_organs or {}).get("real_ai_calls", 0)}
'''

FUNC_ANCHOR = '''    return r, {"paper_fills": paper_log.get("FILLED", 0),
               "vetoes": len(veto_lines),
               "checklist_pass": cg.get("PASS", 0),
               "checklist_fail": cg.get("FAIL", 0)}

def _safe_test(no, name, fn, *a, **kw):
'''

FUNC_NEW = '''    return r, {"paper_fills": paper_log.get("FILLED", 0),
               "vetoes": len(veto_lines),
               "checklist_pass": cg.get("PASS", 0),
               "checklist_fail": cg.get("FAIL", 0)}
''' + TEST17 + '''
def _safe_test(no, name, fn, *a, **kw):
'''

WANT_OLD = '''    want = {"BOOKMAP_WINDOW_SECONDS": "10800", "BOOKMAP_MAX_DEPTH_LEVELS": "250",
            "AI_MIN_SIGNAL_STRENGTH": "6", "CONFIDENCE_THRESHOLD": "50",
            "V6_CFD_SPREAD_MAX": "0.50", "TRADING_ENABLED": "1"}
'''

WANT_NEW = '''    want = {"BOOKMAP_WINDOW_SECONDS": "10800", "BOOKMAP_MAX_DEPTH_LEVELS": "250",
            "AI_MIN_SIGNAL_STRENGTH": "6", "CONFIDENCE_THRESHOLD": "50",
            "V6_CFD_SPREAD_MAX": "0.50", "TRADING_ENABLED": "1"}
    # report v2 (2026-10-15): grade each day under the rule that was lawful when
    # it ran - depth 250 -> 150 on 2026-10-13 (owner's .env), the M3 era from
    # 2026-10-15 (TIMEFRAME / A1_BASE_TF_MINUTES / the voice fix VOICE_GAIN).
    if day >= date_cls(2026, 10, 13):
        want["BOOKMAP_MAX_DEPTH_LEVELS"] = "150"
    if day >= date_cls(2026, 10, 15):
        want.update({"TIMEFRAME": "M3", "A1_BASE_TF_MINUTES": "3", "VOICE_GAIN": "3.0"})
'''

EXPECTED_OLD = '''("16", "TEAM CYCLE"),'''

EXPECTED_NEW = '''("16", "TEAM CYCLE"), ("17", "NEW ORGANS"),'''

ENVFIX_OLD = r'"BOOKMAP_WINDOW_SECONDS=10800\nBOOKMAP_MAX_DEPTH_LEVELS=250\n"'
ENVFIX_NEW = (r'"BOOKMAP_WINDOW_SECONDS=10800\nBOOKMAP_MAX_DEPTH_LEVELS=150\n'
              r'TIMEFRAME=M3\nA1_BASE_TF_MINUTES=3\nVOICE_GAIN=3.0\n"')

M1_OLD = '''# audit_day.py is verified by its BUILD marker, not by size: sizes change every
# time we fix something, and a stale constant here once called a good install bad.
if [ -f audit_day.py ] && grep -q "BUILD = \\"audit-2026-09-24j\\"" audit_day.py; then
  ok "audit_day.py is the build the kit shipped ($(stat -c %s audit_day.py) bytes, marker present)"
elif [ -f audit_day.py ]; then
  no "audit_day.py has no kit build marker ($(stat -c %s audit_day.py) bytes) - re-copy it from the kit"
else
  no "audit_day.py missing from $(pwd)"
fi'''

M1_NEW = '''# report v2 (2026-10-15): builds are verified by CAPABILITY markers, never by
# size or old kit stamps - the September stamp was stale and its advice
# ("re-copy from the kit") would have erased A1/A2/memory/voice. Never again.
if [ -f audit_day.py ] && grep -q "def test_17_new_organs" audit_day.py; then
  ok "audit_day.py carries the report-v2 organs test (test 17 present)"
elif [ -f audit_day.py ]; then
  no "audit_day.py has no test_17_new_organs - report-v2 not installed (or an older build). Tell the assistant; do NOT copy old kits over live files"
else
  no "audit_day.py missing from $(pwd)"
fi'''

M2_OLD = '''# daily_check.sh and morning_check.sh check themselves by parsing, never by size -
# a size constant inside a file it measures can never be right after an edit.
for f in dashboard.py:1226 tools/dashboard.py:31843 judge_panel.py:15606 main.py:48991; do
  n=${f%%:*}; want=${f##*:}
  if [ -f "$n" ]; then
    sz=$(stat -c %s "$n")
    [ "$sz" = "$want" ] && ok "$n ($sz bytes)" || no "$n is $sz bytes, expected $want - re-copy it from the kit"
  else
    no "$n missing from $(pwd)"
  fi
done'''

M2_NEW = '''# report v2 (2026-10-15): existence + the compile section below are the truth;
# byte sizes from any old kit are not. No re-copy advice, ever again.
for n in dashboard.py tools/dashboard.py judge_panel.py main.py; do
  if [ -f "$n" ]; then
    info "$n present ($(stat -c %s "$n") bytes)"
  else
    info "$n not present (fine if this build never had it)"
  fi
done'''

M3_OLD = '''  no "tools/insight.py missing or broken - re-copy it from the kit (the 15-test day audit still runs; --explain, --weight-ab, --walk-forward and the two HTML pages stay quiet)"'''

M3_NEW = '''  no "tools/insight.py missing or broken - tell the assistant; NEVER copy an old kit over live files (the day audit still runs; --explain, --weight-ab, --walk-forward and the two HTML pages stay quiet)"'''

R1_OLD = '''# v1.3: desks 6+7 added (the Universes + the Clocks). --fast skips desks 4-7.'''

R1_NEW = '''# v1.3: desks 6+7 added (the Universes + the Clocks). --fast skips desks 4-7.
# v1.4 (report v2, 2026-10-15): desk 8 added - the AI COST line (paid calls +
# where the full cost story lives). Reconciliation line untouched.'''

R2_OLD = '''# ---- the reconciliation line'''

R2_NEW = '''# ---- v1.4 (report v2): the AI COST line ------------------------------------------
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

# ---- the reconciliation line'''


# ------------------------------------------------------------------ engine --
def py_ok(path: Path) -> bool:
    try:
        subprocess.run([sys.executable, "-m", "py_compile", str(path)],
                       check=True, capture_output=True)
        return True
    except Exception:
        return False


def sh_ok(path: Path) -> bool:
    try:
        subprocess.run(["bash", "-n", str(path)], check=True, capture_output=True)
        return True
    except Exception:
        return False


def apply_file(rel, edits, marker, kind):
    """Atomic per file: every edit must anchor, or nothing is written."""
    path = ROOT / rel
    if not path.exists():
        print(f"[miss] {rel}: not found in this project - skipped")
        return "miss"
    src = path.read_text(encoding="utf-8", errors="surrogateescape")
    if marker in src:
        print(f"[skip] {rel}: report v2 already present")
        return "skip"
    failures = []
    for name, anchor, repl, count in edits:
        n = src.count(anchor)
        if n == 0:
            failures.append(name)
            continue
        if count == "all":
            src = src.replace(anchor, repl)
        else:
            src = src.replace(anchor, repl, 1)
    if failures:
        print(f"[STOP] {rel}: anchor(s) not found -> NOTHING written: {', '.join(failures)}")
        for name, anchor, _, _ in edits:
            if name in failures:
                head = anchor.strip().splitlines()[0][:90]
                print(f"       wanted ({name}): {head}")
        print("       your file differs from the expected build - paste this output to the assistant")
        return "fail"
    bak = Path(str(path) + BACKUP_SUFFIX)
    if not bak.exists():
        shutil.copy2(path, bak)
    path.write_text(src, encoding="utf-8", errors="surrogateescape")
    good = py_ok(path) if kind == "py" else sh_ok(path)
    if not good:
        shutil.copy2(bak, path)
        print(f"[FAIL] {rel}: syntax check failed after edit -> backup restored, nothing changed")
        return "fail"
    print(f"[ok] {rel}: {len(edits)} edit(s) applied, verified, backup {bak.name}")
    return "ok"


def rig():
    print("=" * 78)
    print("REPORT v2 RIG - proving the install with real checks")
    print("=" * 78)
    results = []

    def check(name, cond):
        results.append((name, bool(cond)))
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")

    # 1) markers
    for rel, marker in (
            ("audit_day.py", "def test_17_new_organs"),
            ("tools/selftest_audit_day.py", '"17", "NEW ORGANS"'),
            ("morning_check.sh", "def test_17_new_organs"),
            ("report_evening.sh", "the AI COST line"),
            ("daily_check.sh", "17 tests")):
        p = ROOT / rel
        check(f"marker in {rel}", p.exists() and marker in p.read_text(encoding="utf-8", errors="ignore"))

    # 2) syntax
    check("py_compile audit_day.py", py_ok(ROOT / "audit_day.py"))
    check("py_compile tools/selftest_audit_day.py", py_ok(ROOT / "tools" / "selftest_audit_day.py"))
    check("bash -n morning_check.sh", sh_ok(ROOT / "morning_check.sh"))
    check("bash -n report_evening.sh", sh_ok(ROOT / "report_evening.sh"))
    check("bash -n daily_check.sh", sh_ok(ROOT / "daily_check.sh"))

    # 3) unit smoke: test_17 on a synthetic record with the new organs
    smoke = r'''
import sys, datetime
sys.path.insert(0, ".")
import audit_day as A
day = datetime.date(2026, 10, 15)
diary = [{"ts": "2026-10-15T12:55:00",
          "power_v2": {"direction": "UP", "reason_code": "REGIME_WARMUP",
                       "voice_gain": 3.0, "net_force": 1.0, "activity": 0.6094,
                       "checks_pass": True, "memory_status": {"appended": True}},
          "aggressive_trigger_confirmed": False,
          "aggressive_trigger_reason": "eyes: junk",
          "regime": "TREND"},
         {"ts": "2026-10-15T13:00:00",
          "power_v2": {"direction": "NEITHER", "reason_code": "WEAK_OR_NO_FORCE",
                       "voice_gain": 3.0, "memory_status": {"appended": False}},
          "aggressive_trigger_confirmed": False}]
text = ("INFO [main] STEP 2  MARKET ANALYSIS   OK -> NEUTRAL (strength 12.6, confidence 8.6)\n"
        "INFO [main] STEP 3  AI DECISION   HOLD (AI throttled, 3 min until next call)\n"
        "INFO [main] STEP 2  MARKET ANALYSIS   OK -> NEUTRAL (strength 4.7, confidence 8.6)\n"
        "INFO [main] STEP 3  AI DECISION   HOLD (signal 4.7 < 6, AI skipped)\n"
        "INFO [main] STEP 3  AI DECISION   HOLD @ 8.6%\n")
r, p = A.test_17_new_organs(day, diary, text, [])
assert r.rows and r.rows[0][0] in ("PASS", "WARN", "NA"), "no grade"
assert p.get("voice_gain_records") == 2, p
assert p.get("a2_gate_runs") == 2, p
assert p.get("missed_bells") == 1, p      # 12.6 rang while throttled
assert p.get("real_ai_calls") == 1, p
r2, p2 = A.test_17_new_organs(day, [], "", [])
assert r2.rows and r2.rows[0][0] == "NA", "empty diary must grade NA"
old, _oldp = A.test_17_new_organs(datetime.date(2026, 10, 10), diary, text, [])
assert old.rows, "old day must still grade"
print("SMOKE-OK")
'''
    try:
        pr = subprocess.run([sys.executable, "-c", smoke], cwd=str(ROOT),
                            capture_output=True, text=True, timeout=120)
        check("unit smoke: test_17 grades (PASS/WARN + NA + era logic)",
              "SMOKE-OK" in pr.stdout)
        if "SMOKE-OK" not in pr.stdout:
            print(pr.stdout[-1500:])
            print(pr.stderr[-1500:])
    except Exception as e:
        check(f"unit smoke (error: {e})", False)

    # 4) the doctor (the report team's own selftest, ~1 min)
    try:
        pr = subprocess.run([sys.executable, "tools/selftest_audit_day.py"],
                            cwd=str(ROOT), capture_output=True, text=True, timeout=600)
        check("the doctor: tools/selftest_audit_day.py PASSED",
              "SELFTEST PASSED" in pr.stdout)
        if "SELFTEST PASSED" not in pr.stdout:
            print(pr.stdout[-2000:])
            print(pr.stderr[-1000:])
    except Exception as e:
        check(f"the doctor (error: {e})", False)

    print("-" * 78)
    npass = sum(1 for _, c in results if c)
    if npass == len(results):
        print(f"RIG VERDICT: ALL PASS ({npass}/{len(results)}) - the report team v2 is proven.")
        return 0
    print(f"RIG VERDICT: {len(results) - npass} FAILED - paste this whole output to the assistant.")
    return 1


def rollback():
    for rel in ("audit_day.py", "tools/selftest_audit_day.py", "morning_check.sh",
                "daily_check.sh", "report_evening.sh"):
        p = ROOT / rel
        bak = Path(str(p) + BACKUP_SUFFIX)
        if bak.exists():
            shutil.copy2(bak, p)
            print(f"[back] {rel} restored from {bak.name}")
        else:
            print(f"[ --  ] {rel}: no {BACKUP_SUFFIX} backup")
    print("rollback done. (The backups stay on disk until you delete them by hand.)")


def main():
    if not (ROOT / "main.py").exists():
        print("ERROR: run this from the project root (the folder with main.py)")
        return 1
    if "--rig" in sys.argv:
        return rig()
    if "--rollback" in sys.argv:
        rollback()
        return 0

    print("== report team v2 installer (2026-10-15) ==")
    print("   fixes: O12 blind organs / O7+stale rulers / O6 morning danger / O11 cost line\n")

    apply_file("audit_day.py", [
        ("insert test_17 function", FUNC_ANCHOR, FUNC_NEW, 1),
        ("hook test_17 into the runner", HOOK, HOOK_NEW, 1),
        ("era-aware config ruler (test 11)", WANT_OLD, WANT_NEW, 1),
        ("header 16->17 tests", "16 tests", "17 tests", "all"),
    ], "def test_17_new_organs", "py")

    apply_file("tools/selftest_audit_day.py", [
        ("expected tests list", EXPECTED_OLD, EXPECTED_NEW, 1),
        ("doctor tag 16-test header", "16-test header", "17-test header", "all"),
        ("doctor fake .env -> M3 era", ENVFIX_OLD, ENVFIX_NEW, "all"),
        ("doctor message 16->17", "16 tests", "17 tests", "all"),
    ], '"17", "NEW ORGANS"', "py")

    apply_file("morning_check.sh", [
        ("retire the September BUILD stamp", M1_OLD, M1_NEW, 1),
        ("retire the byte-size kit checks", M2_OLD, M2_NEW, 1),
        ("remove the re-copy advice (insight)", M3_OLD, M3_NEW, 1),
    ], "def test_17_new_organs", "sh")

    apply_file("report_evening.sh", [
        ("version note v1.4", R1_OLD, R1_NEW, 1),
        ("desk 8: the AI COST line", R2_OLD, R2_NEW, 1),
    ], "the AI COST line", "sh")

    apply_file("daily_check.sh", [
        ("header 16->17 tests", "16 tests", "17 tests", "all"),
    ], "17 tests", "sh")

    print("\nNext: prove it ->   python apply_report_v2.py --rig")
    print("Then push (never the .pre_* backups):")
    print("  git add audit_day.py tools/selftest_audit_day.py morning_check.sh \\")
    print("      daily_check.sh report_evening.sh apply_report_v2.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
