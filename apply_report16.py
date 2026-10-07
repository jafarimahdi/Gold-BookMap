#!/usr/bin/env python3
"""
apply_report16.py - REPORT UPGRADE: TEAM CYCLE & ACCEPTANCE card (test 16)
==========================================================================
The robot's diary has recorded the whole team's work since v7.1
(power / shot / entry_simulation / escort), but the day report never read
those fields, so POWER, Shooting, paper fills and Escort were invisible in
the daily audit. This upgrade fixes the REPORTING only (no robot changes):

  1. audit_day.py      - new test 16 "TEAM CYCLE & ACCEPTANCE":
                         * the team's day: POWER verdicts, Shooting plans,
                         paper fills, Escort states (diary + log cross-check)
                         * POWER vetoes (canonical panel overruled the AI)
                         * the 12 acceptance points from
                         TASK5_TASK6_ACCEPTANCE_AND_RISK_CHECKLIST.md B
                         (graded PASS/WARN/FAIL/NA with the evidence named)
                       - test 5 GUARD CHECK also counts: L4 kill switch /
                         flash crash, POWER v2 vetoes, cooldown refusals
                       - headers say 16 tests
  2. tools/dashboard.py       - test-16 card in the dashboard
  3. tools/selftest_audit_day.py - selftest knows the 16th test
  4. daily_check.sh           - header text

Run from the project root (the folder with main.py):

    python apply_report16.py

Each file is backed up next to itself as <name>.pre_report16 before the
first edit. The script is idempotent: re-running it reports ALREADY and
changes nothing.
"""
from pathlib import Path
import shutil
import sys

ROOT = Path.cwd()

# ---------------------------------------------------------------- test 16 --
TEST16 = '''
def test_16_team_cycle(day, diary, text, decisions):
    """TEAM CYCLE & ACCEPTANCE (report16) - the money path and the 12 acceptance
    points. The diary has carried power / shot / entry_simulation / escort since
    v7.1; before report16 no test read them, so the team's work was invisible."""
    r = Report(16)
    why = []
    txt = text or ""

    def _dicts(key):
        out = []
        for d in diary or []:
            v = d.get(key)
            if isinstance(v, dict) and v:
                out.append(v)
        return out

    powers = _dicts("power") or _dicts("power_v2")
    shots = _dicts("shot")
    sims = _dicts("entry_simulation")
    escorts = _dicts("escort")

    shooter_go = len(re.findall(r"SHOOTER: GO\\b", txt))
    shooter_wait = len(re.findall(r"SHOOTER: WAIT\\b", txt))
    shooter_none = len(re.findall(r"SHOOTER: no plan\\b", txt))
    paper_log = Counter(re.findall(r"PAPER ENTRY: ([A-Z_]+)", txt))
    escort_guard = len(re.findall(r"ESCORT: guarding", txt))
    escort_asleep = len(re.findall(r"ESCORT: asleep", txt))

    why.append("TEAM CYCLE (POWER -> SHOOTING -> PAPER -> ESCORT) from "
               f"{len(diary or [])} diary snapshot(s) + the log:")
    if powers:
        dirs = Counter(str(p.get("direction", "?")) for p in powers)
        rcs = Counter(str(p.get("reason_code", "?")) for p in powers)
        acts = [float(p.get("activity") or 0.0) for p in powers
                if p.get("activity") is not None]
        act_s = (f"activity min {min(acts):.3f} / med {sorted(acts)[len(acts)//2]:.3f}"
                 f" / max {max(acts):.3f}" if acts else "activity n/a")
        lastp = powers[-1]
        why.append("   POWER (canonical v2): "
                   f"{len(powers)} answer(s) | direction "
                   + ", ".join(f"{k} x{v}" for k, v in dirs.most_common())
                   + " | reason codes "
                   + ", ".join(f"{k} x{v}" for k, v in rcs.most_common(5))
                   + f" | {act_s} | last: {lastp.get('direction', '?')} force "
                   f"{lastp.get('up_power_pct', '?')}/{lastp.get('down_power_pct', '?')} "
                   f"({lastp.get('reason_code', '?')})")
    else:
        why.append("   POWER (canonical v2): no 'power' entries in this day's diary "
                   "(build before the POWER-v2 diary field, or STEP 2 never ran)")
    if shots:
        sc = Counter(str(s.get("shot", "?")) for s in shots)
        sides = Counter(str(s.get("side", "?")) for s in shots
                        if s.get("shot") == "GO")
        why.append(f"   SHOOTING: {len(shots)} plan(s) -> "
                   + ", ".join(f"{k} x{v}" for k, v in sc.most_common())
                   + ("" if not sides else " | GO sides "
                      + ", ".join(f"{k} x{v}" for k, v in sides.most_common()))
                   + f" | log lines: GO x{shooter_go}, WAIT x{shooter_wait}, "
                   f"no plan x{shooter_none}")
    else:
        why.append("   SHOOTING: no 'shot' plans in this day's diary | log lines: "
                   f"GO x{shooter_go}, WAIT x{shooter_wait}, no plan x{shooter_none}")
    if sims:
        st = Counter(str(s.get("status", "?")) for s in sims)
        fills = [s for s in sims if s.get("status") in ("FILLED", "ALREADY_FILLED")]
        px = ", ".join(str(s.get("fill_price")) for s in fills[:6]) or "-"
        why.append(f"   PAPER: {len(sims)} simulation(s) -> "
                   + ", ".join(f"{k} x{v}" for k, v in st.most_common())
                   + f" | fill prices: {px} | log PAPER ENTRY: "
                   + (", ".join(f"{k} x{v}" for k, v in paper_log.most_common())
                      or "none"))
    else:
        why.append("   PAPER: no 'entry_simulation' entries in this day's diary | "
                   "log PAPER ENTRY: "
                   + (", ".join(f"{k} x{v}" for k, v in paper_log.most_common())
                      or "none"))
    if escorts or escort_guard or escort_asleep:
        why.append(f"   ESCORT: {len(escorts)} diary state(s) | log: "
                   f"guarding x{escort_guard}, asleep x{escort_asleep}")
    else:
        why.append("   ESCORT: nothing recorded today (no paper position handed "
                   "to Escort yet)")

    veto_lines = [ln for ln in txt.splitlines() if "POWER v2 veto" in ln]
    if veto_lines:
        veto_times = []
        for ln in veto_lines[:8]:
            m = re.search(r"(\\d{4}-\\d{2}-\\d{2}[ T]\\d{2}:\\d{2}(?::\\d{2})?)", ln)
            veto_times.append(m.group(1) if m else ln.strip()[:60])
        why.append(f"   POWER VETOES: {len(veto_lines)}x the canonical panel "
                   "overruled the AI (AI side did not agree with POWER v2) - each "
                   "is in decisions_log with the full reason: "
                   + "; ".join(veto_times))
    else:
        why.append("   POWER VETOES: none in the log for this day")

    # ---------------- the 12 acceptance points (checklist section B) --------
    checks = []

    def _chk(grade, label, detail):
        checks.append((grade, label, detail))

    rc = None
    try:
        rc = _load_run_config(day)
    except Exception:
        rc = None

    probes = re.findall(
        r"POWER HISTORY PROBE \\| status=(\\w+)[^\\n]*?loaded_bars=(\\d+)"
        r"[^\\n]*?scale=(\\w+)", txt)
    fb = len(re.findall(r"FUTURE_BAR|NO_HISTORY", txt))
    if probes:
        st_p, bars_p, scale_p = probes[-1]
        ok = (st_p == "LOADED" and bars_p == "28" and scale_p == "OK")
        _chk("PASS" if ok else "WARN", "1  probe 28 bars + scale=OK",
             f"last line: status={st_p} loaded_bars={bars_p} scale={scale_p}"
             + (f" | transients FUTURE_BAR/NO_HISTORY x{fb} (self-heals at the "
                "next completed M5 bar)" if fb else ""))
    else:
        _chk("NA", "1  probe 28 bars + scale=OK",
             "no POWER HISTORY PROBE line in the kept log for this day"
             + (f" | FUTURE_BAR/NO_HISTORY x{fb}" if fb else ""))

    te = (rc or {}).get("TRADING_ENABLED")
    if te in (None, ""):
        te = ENV.get("TRADING_ENABLED")
    dis = len(re.findall(r"trading disabled", txt, re.I))
    if dis:
        _chk("WARN", "2  TRADING ON",
             f"'trading disabled' seen x{dis} - correct if this was the "
             "deliberate belt test (point 12); otherwise check TRADING_ENABLED")
    elif str(te) == "1":
        _chk("PASS", "2  TRADING ON",
             "TRADING_ENABLED=1 and no 'trading disabled' line in the log")
    else:
        _chk("NA", "2  TRADING ON",
             f"TRADING_ENABLED={te!r} (recorded for the day: "
             f"{'yes' if rc else 'no run_config file'})")

    em = (rc or {}).get("EXECUTION_MODE")
    if em in (None, ""):
        em = ENV.get("EXECUTION_MODE")
    if str(em).lower() == "python":
        _chk("PASS", "3  EXECUTION_MODE=python", f"recorded mode = {em}")
    elif em in (None, ""):
        _chk("NA", "3  EXECUTION_MODE=python",
             "the day's record does not show EXECUTION_MODE (older build)")
    else:
        _chk("FAIL", "3  EXECUTION_MODE=python",
             f"recorded mode = {em} - the executor would not send orders")

    belt = len(re.findall(r"real account blocked", txt, re.I))
    if belt == 0:
        _chk("PASS", "4  real-account belt quiet",
             "no 'real account blocked' line - the belt never had to catch "
             "a real-order attempt")
    else:
        _chk("WARN", "4  real-account belt quiet",
             f"the belt caught {belt} real-order attempt(s) - money is safe, "
             "but something tried to trade for real: read the line before each")

    lot_hits = [float(x) for x in
                re.findall(r"(?:volume|lot|lots)\\s*[=:]\\s*([0-9]+(?:\\.[0-9]+)?)", txt)]
    bad = sorted({x for x in lot_hits if abs(x - 0.01) > 1e-9})
    if lot_hits and not bad:
        _chk("PASS", "5+6  every order exactly 0.01 lot",
             f"{len(lot_hits)} order-size print(s) in the log, all 0.01")
    elif bad:
        _chk("FAIL", "5+6  every order exactly 0.01 lot",
             f"non-0.01 order sizes seen: {bad[:8]}")
    else:
        _chk("NA", "5+6  every order exactly 0.01 lot",
             "no order-size prints in the kept log - check MT5 Account History "
             "(Task 4 table): every deal must say volume 0.01")

    lq = re.search(r"B3 limit queue: (True|False)", txt)
    if lq and lq.group(1) == "False":
        _chk("PASS", "7  aggressive-only (no limit queue)",
             "startup recorded 'B3 limit queue: False' - no limit/queue entries")
    elif lq:
        _chk("WARN", "7  aggressive-only (no limit queue)",
             "startup recorded 'B3 limit queue: True' - LIMIT_ORDER_ENABLED=0 "
             "is your setting; check .env")
    else:
        _chk("NA", "7  aggressive-only (no limit queue)",
             "no 'B3 limit queue' startup line in the kept log; .env says "
             f"LIMIT_ORDER_ENABLED={ENV.get('LIMIT_ORDER_ENABLED', '?')}")

    cd = 10.0
    try:
        cd = float(_envf("COOLDOWN_MINUTES", 10.0))
    except Exception:
        pass
    times = []
    for d in decisions or []:
        if str(d.get("exec_status") or "").upper() in ("EXECUTED", "PENDING"):
            dt = d.get("_dt")
            if dt is not None:
                try:
                    times.append(dt.replace(tzinfo=None))
                except Exception:
                    pass
    for m in re.finditer(
            r"(\\d{4}-\\d{2}-\\d{2}[ T]\\d{2}:\\d{2}:\\d{2})(?:\\.\\d+)?"
            r"[^\\n]*PAPER ENTRY: FILLED", txt):
        try:
            times.append(datetime.strptime(
                m.group(1).replace("T", " "), "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            pass
    times.sort()
    merged = 0
    kept = []
    for t in times:
        if kept and (t - kept[-1]).total_seconds() < 30:
            merged += 1          # same entry recorded twice (MT5 send + paper fill)
            continue
        kept.append(t)
    times = kept
    gaps = [(b - a).total_seconds() / 60.0 for a, b in zip(times, times[1:])]
    cd_ref = len(re.findall(r"cooldown active", txt, re.I))
    dup = (f" | {merged} double-recorded moment(s) (MT5 send + paper fill) "
           "counted once" if merged else "")
    if len(times) < 2:
        _chk("NA", f"8  cooldown {cd:.0f} min between entries",
             f"only {len(times)} entry moment(s) today - nothing to compare yet"
             + dup
             + (f" | trade_guard cooldown refusals x{cd_ref}" if cd_ref else ""))
    else:
        gmin = min(gaps)
        _chk("PASS" if gmin >= cd - 1e-6 else "FAIL",
             f"8  cooldown {cd:.0f} min between entries",
             f"{len(times)} entry moments (MT5 sends + paper fills), closest "
             f"pair {gmin:.1f} min apart"
             + dup
             + (f" | trade_guard cooldown refusals x{cd_ref}" if cd_ref else ""))

    KNOWN = {"UPWARD_FORCE_DOMINATES", "DOWNWARD_FORCE_DOMINATES",
             "WEAK_OR_NO_FORCE"}
    if powers:
        good = sum(1 for p in powers if str(p.get("reason_code", "")) in KNOWN)
        if good / len(powers) >= 0.9:
            _chk("PASS", "9  POWER verdict carries a reason",
                 f"{good}/{len(powers)} POWER answers have a reason code "
                 "(UPWARD/DOWNWARD_FORCE_DOMINATES or WEAK_OR_NO_FORCE)")
        else:
            _chk("WARN", "9  POWER verdict carries a reason",
                 f"only {good}/{len(powers)} POWER answers carry a known "
                 "reason code - others: "
                 + str(Counter(str(p.get("reason_code", "?"))
                               for p in powers).most_common(3)))
    else:
        _chk("NA", "9  POWER verdict carries a reason",
             "no POWER entries in the diary to grade")

    warm = len(re.findall(r"POWER WARM-UP", txt))
    if probes and probes[-1][0] == "LOADED":
        _chk("PASS", "10  warm-up only before 28 bars",
             f"steady state reached (last probe LOADED "
             f"loaded_bars={probes[-1][1]}) | POWER WARM-UP notes in the log "
             f"x{warm} (before steady state they are expected)")
    elif warm:
        _chk("WARN", "10  warm-up only before 28 bars",
             f"POWER WARM-UP notes x{warm} but no steady LOADED probe line "
             "in the kept log")
    else:
        _chk("NA", "10  warm-up only before 28 bars",
             "no probe/warm-up evidence in the kept log")

    flats = re.findall(r"daily flatten[^\\n]*|SESSION_FLATTEN[^\\n]*", txt, re.I)
    stamps = re.findall(r"\\d{4}-\\d{2}-\\d{2} (\\d{2}:\\d{2}):\\d{2}", txt)
    last_hm = stamps[-1] if stamps else None
    if flats:
        _chk("PASS", "11  PM daily flatten 21:30 UTC",
             "flatten evidence in the log, last: " + flats[-1].strip()[:90])
    elif last_hm and last_hm < "23:00":
        _chk("NA", "11  PM daily flatten 21:30 UTC",
             f"the log ends at {last_hm} local - the robot was not running at "
             "23:30 Budapest flatten time (nothing open to flatten is fine too)")
    elif last_hm:
        _chk("WARN", "11  PM daily flatten 21:30 UTC",
             f"the log runs to {last_hm} local (past the 23:30 flatten) but no "
             "'daily flatten' line was found - check position_manager")
    else:
        _chk("NA", "11  PM daily flatten 21:30 UTC",
             "no timestamps readable in the log")

    l4 = len(re.findall(r"L4 KILL SWITCH TRIGGERED", txt))
    fc = len(re.findall(r"SAFETY: FLASH_CRASH", txt))
    if l4:
        why.append(f"   L4 flash-crash kill switch fired x{l4} today "
                   f"(SAFETY: FLASH_CRASH records x{fc}) - that guard works "
                   "live; the entries around it are the ones to audit")
    _chk("NA", "12  kill-switch belt (TRADING_ENABLED=0)",
         "manual belt test - checklist F5 wants it done once ON PURPOSE "
         "(TRADING_ENABLED=0 mid-run -> analyse only, no orders)")

    for g, label, detail in checks:
        why.append(f"[{g:4}] {label} -> {detail}")
    why.append("checklist source: TASK5_TASK6_ACCEPTANCE_AND_RISK_CHECKLIST.md "
               "section B (12 points). Points 5/6/12 need MT5 history or a "
               "manual belt test - the report says NA instead of guessing.")
    cg = Counter(c[0] for c in checks)
    if cg.get("FAIL"):
        grade = "FAIL"
    elif cg.get("WARN") or cg.get("NA"):
        grade = "WARN"
    else:
        grade = "PASS"
    r.add(grade, "TEAM CYCLE & ACCEPTANCE",
          "the team's day (POWER -> Shooting -> paper -> Escort) and the "
          "12 acceptance points", why)
    return r, {"paper_fills": paper_log.get("FILLED", 0),
               "vetoes": len(veto_lines),
               "checklist_pass": cg.get("PASS", 0),
               "checklist_fail": cg.get("FAIL", 0)}

'''

REPLACES = {
    "audit_day.py": [
        # --- test 5: three more guard families counted in the log ---
        ('                      (r"trading disabled", "trading disabled"),\n'
         '                      (r"AI (rate|quota)|quota", "AI quota guard")):',
         '                      (r"trading disabled", "trading disabled"),\n'
         '                      (r"AI (rate|quota)|quota", "AI quota guard"),\n'
         '                      (r"L4 KILL SWITCH TRIGGERED|SAFETY: FLASH_CRASH",\n'
         '                       "L4 kill switch / flash crash"),\n'
         '                      (r"POWER v2 veto", "POWER v2 veto (canonical over AI)"),\n'
         '                      (r"cooldown active", "cooldown refusal")):'),
        # --- the new test, inserted just before _safe_test ---
        ("def _safe_test(no, name, fn, *a, **kw):",
         TEST16 + "def _safe_test(no, name, fn, *a, **kw):"),
        # --- wiring in the runner ---
        ('    reports["hour"], _hour = _safe_test(15, "HOUR CHECK", test_15_hourly, day, ticks, decisions)',
         '    reports["hour"], _hour = _safe_test(15, "HOUR CHECK", test_15_hourly, day, ticks, decisions)\n'
         '    # report16: the team\'s money path + the 12 acceptance points\n'
         '    reports["cycle"], _cycle = _safe_test(16, "TEAM CYCLE & ACCEPTANCE",\n'
         '                                          test_16_team_cycle, day, diary, text, decisions)'),
        # --- order tuples / headers: 15 -> 16 tests ---
        ('             "cov", "ver", "lat", "money", "files", "hour")',
         '             "cov", "ver", "lat", "money", "files", "hour", "cycle")'),
        ('<h2>15 tests</h2>', '<h2>16 tests</h2>'),
        ('"The full night report (all 15 tests)"', '"The full night report (all 16 tests)"'),
        ('(all 15 tests, judges, what-if)', '(all 16 tests, judges, what-if)'),
        ('Run one of the 15 tests;', 'Run one of the 16 tests;'),
        ('(Budapest) - 15 tests', '(Budapest) - 16 tests'),
        ('record, all 15 tests, every decision', 'record, all 16 tests, every decision'),
    ],
    "tools/dashboard.py": [
        ('             "cov", "ver", "lat", "money", "files", "hour")',
         '             "cov", "ver", "lat", "money", "files", "hour", "cycle")'),
        ('"All 15 tests graded with real data and no warnings. Test 7 and 9 are trustworthy as-is."',
         '"All 16 tests graded with real data and no warnings. Test 7 and 9 are trustworthy as-is."'),
        ('All 15 tests - click a card for the details',
         'All 16 tests - click a card for the details'),
    ],
    "tools/selftest_audit_day.py": [
        ('("15", "HOUR"),', '("15", "HOUR"), ("16", "TEAM CYCLE"),'),
        ('("15-test header", r"DAY AUDIT - .* - 15 tests"),',
         '("16-test header", r"DAY AUDIT - .* - 16 tests"),'),
        ('all 15 tests still graded, no crash', 'all 16 tests still graded, no crash'),
        ('SELFTEST PASSED: all 15 tests produced real grades',
         'SELFTEST PASSED: all 16 tests produced real grades'),
    ],
    "daily_check.sh": [
        ('== 2/4 day audit (15 tests) ==', '== 2/4 day audit (16 tests) =='),
    ],
}

MARKERS = {
    "audit_day.py": "def test_16_team_cycle",
    "tools/dashboard.py": '"hour", "cycle")',
    "tools/selftest_audit_day.py": '("16", "TEAM CYCLE")',
    "daily_check.sh": "day audit (16 tests)",
}


def main() -> int:
    if not (ROOT / "main.py").exists():
        print("ERROR: run this from the project root (the folder with main.py)")
        return 2
    overall_ok = True
    for rel, pairs in REPLACES.items():
        path = ROOT / rel
        if not path.exists():
            print(f"MISSING {rel} - skipped (not in this checkout)")
            continue
        text = path.read_text(encoding="utf-8")
        if MARKERS[rel] in text:
            print(f"ALREADY {rel}")
            continue
        ok = True
        for old, new in pairs:
            n = text.count(old)
            if n != 1:
                print(f"ERROR {rel}: anchor found {n}x (want 1) for: {old[:70]!r}")
                ok = False
                break
        if not ok:
            overall_ok = False
            continue
        bak = path.with_name(path.name + ".pre_report16")
        if not bak.exists():
            shutil.copy2(path, bak)
            print(f"backup {bak.name}")
        for old, new in pairs:
            text = text.replace(old, new, 1)
        path.write_text(text, encoding="utf-8")
        print(f"PATCHED {rel} ({len(pairs)} edit(s))")
    if overall_ok:
        print("\nDone. Next: python -m py_compile audit_day.py && "
              "python tools/selftest_audit_day.py")
    else:
        print("\nNOTHING was written for the failing file - report this output.")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
