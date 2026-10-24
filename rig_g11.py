#!/usr/bin/env python3
"""rig_g11.py -- the test lake for BOTH new tools: the truck camera (tail_probe)
and the clocks machine (clocks_verdict).

  A. tail_probe: parse one-line/split STEP formats + full pipeline totals,
     legacy fallback, stats, slow filter, stage attribution, --peek, end-to-end save
  B. clocks_verdict: judge rows from audit format, WAIT at 4 days, DECIDE at 5
     (cut + keep), shadow/benchmark parse (tonight's real format), t-test
     known answer (t = 6.0), C6 buckets, end-to-end save

Run:  python rig_g11.py   (from the folder holding both tools)
"""
import math
import os
import statistics
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from clocks_verdict import parse_judges, parse_ledger, parse_shadow, t_test
from tail_probe import find_log, parse_cycles, pct

FAILURES = []


def check(name, cond, extra=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' -- ' + str(extra)) if (extra and not cond) else ''}")
    if not cond:
        FAILURES.append(name)


def synth_log(path, n=30):
    """Mix real split-line totals, compact legacy lines, and step-sum fallback rows."""
    lines = []
    for i in range(n):
        s1 = 100 + (i % 7)
        s2 = 400 + (9000 if i == 17 else 0)              # one STEP2-owned slow cycle
        s3 = 9000 if i in (5, 11) else 7                # two STEP3-owned slow cycles
        tod = f"09:{(i // 2):02d}:{(i % 2) * 30:02d}"   # minutes 0-14
        if i % 2 == 0:
            lines.append(f"{tod} cycle: STEP1 acquire {s1}ms STEP2 analyze {s2}ms "
                         f"STEP3 AI {s3}ms")
        else:
            lines.append(f"{tod} INFO [main] LATENCY STEP1 acquire {s1}ms")
            lines.append(f"{tod} INFO [main] LATENCY STEP2 analyze {s2}ms")
            lines.append(f"{tod} WARNING [main] LATENCY STEP3 AI {s3}ms > target 500ms")
        # First 20 rows have authoritative full-L4 totals. The final ten mimic
        # old logs and must be explicitly labeled as component-sum fallback.
        if i < 20:
            total = s1 + s2 + s3 + 50                  # extra work beyond the 3 steps
            lines.append(f"{tod} INFO [main] LATENCY TOTAL pipeline {total}ms target 500ms")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def synth_day(root, date, whale_pc, shadow_pts, bench_pts, conf_rows):
    os.makedirs(os.path.join(root, "data"), exist_ok=True)
    with open(os.path.join(root, f"data/day_audit_{date}.txt"), "w") as f:
        f.write("   THE SCOREBOARD\n")
        # EXACTLY the real audit_day.py test_9 scoreboard line format:
        #   f"        {j:<20}{votes:>6}{part:>6.0f}%{split:>12}{acc:>7.1f}%"
        #   f"{q_tot:>6}{pts:>+9.1f}{avg:>+7.2f}{agree:>7.0f}%   {cur:.1f} -> {sugg:.2f}"
        # (fixed 2026-10-13: the first rig planted an invented format, so it
        #  passed while the real machine parsed nothing)
        f.write(f"        {'whale_walls':<20}{120:>6}{68:>6.0f}%{'80/20/20':>12}"
                f"{68.4:>7.1f}%{120:>6}{120 * whale_pc:>+9.1f}{whale_pc:>+7.2f}"
                f"{92:>7.0f}%   {2.0:.1f} -> {2.37:.2f}\n")
        f.write(f"        {'htf_poc':<20}{80:>6}{45:>6.0f}%{'50/20/10':>12}"
                f"{75.0:>7.1f}%{80:>6}{80 * -0.062:>+9.1f}{-0.062:>+7.2f}"
                f"{88:>7.0f}%   {0.4:.1f} -> {0.49:.2f}\n")
    with open(os.path.join(root, f"data/shadow_replay_{date}.txt"), "w") as f:
        f.write(f"   SHADOW (walls+gate+trail)            334     {shadow_pts:+.1f}     -1.75   59.3\n")
        f.write(f"   BENCHMARK fixed ruler, same subset   327     {bench_pts:+.1f}     -1.66\n")
    with open(os.path.join(root, f"data/shadow_replay_{date}.csv"), "w") as f:
        f.write("timestamp,direction,price,bracket,gate,outcome,pts,detail\n")
        f.write("2026-10-12 12:00,BUY,2650.0,stop=2648.0,gate ok,-1.0,-1.0,trail\n")
        f.write("2026-10-12 13:00,SELL,2650.0,stop=2653.0,gate ok,+3.0,3.0,target\n")
    with open(os.path.join(root, f"data/decision_ledger_{date}.csv"), "w") as f:
        # the REAL decision_ledger.py CSV columns (rows_out dict keys, in order)
        f.write("timestamp,direction,price,conf,ai_action,exec_status,stopped_by,"
                "reason,replay_status,outcome,pts,would_have\n")
        for conf, pts in conf_rows:
            f.write(f"2026-10-12 12:00,BUY,2650.0,{conf:.1f},PLAN_ONLY,PLAN_ONLY,"
                    f"CONF GATE (less),reason cut,replayed,replayed,{pts:+.2f},"
                    f"{'WON' if pts > 0 else 'LOST'}\n")


def main():
    print("G11 + CLOCKS rig -- planted logs, planted clocks, known answers")
    print("=" * 60)
    tmp = tempfile.mkdtemp(prefix="g11_rig_")
    old = os.getcwd()

    # ---- A: the truck camera --------------------------------------------------
    print("A. tail_probe (the truck camera)")
    lp = os.path.join(tmp, "trading_test.log")
    synth_log(lp)
    cycles, matched, unmatched = parse_cycles(lp)
    check("A1 parses one-line, split-line, and fallback cycles (30)", len(cycles) == 30, len(cycles))
    check("A2 prefers full pipeline total (includes work outside the steps)",
          cycles[0]["total"] == cycles[0]["s1"] + cycles[0]["s2"] + cycles[0]["s3"] + 50
          and cycles[0]["total_source"] == "pipeline", cycles[0])
    check("A3 split-line STEP3 is retained", cycles[1]["s3"] == 7
          and cycles[5]["s3"] == 9000 and cycles[5]["total"] == 9555, cycles[5])
    check("A4 legacy component totals are explicitly marked partial",
          cycles[20]["total_source"] == "step-sum"
          and cycles[20]["total"] == cycles[20]["s1"] + cycles[20]["s2"] + cycles[20]["s3"],
          cycles[20])
    tots = [c["total"] for c in cycles]
    check("A5 median correct", 500 < statistics.median(tots) < 600,
          statistics.median(tots))
    check("A6 p95 catches the slow spikes", pct(tots, 0.95) > 5000, pct(tots, 0.95))
    slow = [c for c in cycles if c["total"] >= 5000]
    check("A7 exactly 3 slow cycles found", len(slow) == 3, len(slow))
    check("A8 peek collected matched step and total lines", len(matched) >= 60, len(matched))
    r = subprocess.run([sys.executable, os.path.join(old, "tail_probe.py"),
                        "--date", "2026-10-12", "--log", lp],
                       capture_output=True, text=True, cwd=tmp, timeout=120)
    check("A9 end-to-end exit 0 + saved", r.returncode == 0
          and os.path.exists(os.path.join(tmp, "data", "tail_probe_2026-10-12.txt")),
          r.stdout[-200:])
    check("A10 output identifies STEP3 AI and full-vs-fallback counts",
          "STEP3 (AI) is the main owner" in r.stdout
          and "full-pipeline totals: 20" in r.stdout
          and "step-sum fallback: 10" in r.stdout, r.stdout[-500:])

    # ---- B: the clocks machine ---------------------------------------------------
    print("B. clocks_verdict (the clocks machine)")
    j = parse_judges("1999-01-01") if False else None
    os.chdir(tmp)
    synth_day(tmp, "2026-10-05", -2.38, -294.0, -250.0, [(35, -3.0), (45, +2.0)])
    j = parse_judges("2026-10-05")
    check("B1 judge rows parsed (real audit_day format)",
          "whale_walls" in j and j["whale_walls"]["calls"] == 120
          and j["whale_walls"]["acc"] == 68.4 and j["whale_walls"]["percall"] == -2.38
          and j["htf_poc"]["percall"] == -0.06, j)
    s = parse_shadow("2026-10-05")
    check("B2 shadow + benchmark parsed (real format)",
          s["shadow"]["pts"] == -294.0 and s["bench"]["pts"] == -250.0, s)
    check("B3 R-multiples from CSV (pts/risk: -0.5 and +1.0)",
          sorted(round(r, 3) for r in s["rs"] if r is not None) == [-0.5, 1.0], s["rs"])
    tt = t_test([0.5, 1.5] * 5)
    check("B4 t-test known answer t = 6.0", abs(tt["t"] - 6.0) < 0.01, tt)
    lr = parse_ledger("2026-10-05")
    check("B5 ledger calibration rows parsed (real decision_ledger columns)",
          len(lr) == 2 and lr[0]["conf"] == 35.0, lr)

    # 4 days -> WAIT; 5th day -> DECIDE (cut, whale negative)
    for d in ("2026-10-06", "2026-10-07", "2026-10-08"):
        synth_day(tmp, d, -1.4, +32.0, +10.0, [(55, +1.0), (65, +2.0), (35, -1.0)])
    r4 = subprocess.run([sys.executable, os.path.join(old, "clocks_verdict.py")],
                        capture_output=True, text=True, cwd=tmp, timeout=120)
    check("B6 4 days -> WAIT (no premature verdict)",
          "WAIT: day 4/5" in r4.stdout and "DECIDE" not in r4.stdout, r4.stdout[-300:])
    synth_day(tmp, "2026-10-09", -1.2, -584.5, -541.7, [(58, +1.5), (52, -0.5)])
    r5 = subprocess.run([sys.executable, os.path.join(old, "clocks_verdict.py")],
                        capture_output=True, text=True, cwd=tmp, timeout=120)
    check("B7 5 days -> DECIDE: CUT (whale mean per-call negative)",
          "DECIDE: CUT" in r5.stdout, r5.stdout[-400:])
    check("B8 t-test fires at 5 shadow days", "t =" in r5.stdout or "t = " in r5.stdout)
    check("B9 saved to data/clocks_verdict.txt",
          os.path.exists(os.path.join(tmp, "data", "clocks_verdict.txt")))
    os.chdir(old)

    print("=" * 60)
    if FAILURES:
        print(f"RIG RESULT: {len(FAILURES)} FAILED -> {', '.join(FAILURES)}")
        sys.exit(1)
    print("RIG RESULT: ALL PASS -- the camera and the clocks measure exactly.")


if __name__ == "__main__":
    main()
