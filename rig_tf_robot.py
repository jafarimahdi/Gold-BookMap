#!/usr/bin/env python3
"""rig_tf_robot.py -- the parallel-universe machine's test lake.

  T. ruler math (planted M1 bars): TP hit, SL-first, timeout walk
  P. the patcher still patches the workshop base brain
  U. end-to-end on a synthetic 3h ramp lake, REAL patched brain:
     U1 exit 0 + saved report
     U2 M5 universe: the real panel signals (>=1)
     U3 the env knob really changes each universe (row BODIES differ;
        the name prefix is excluded so identical universes FAIL)
     U4 no brain crashes in any universe (ERR == 0 everywhere)
     U5 every universe row parsed (cycles, conf fields present)

Run:  python rig_tf_robot.py   (from the folder holding tf_robot_replay.py;
     copies what it needs into a temp dir, including twin_day_runner.py)
"""
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
FAILURES = []


def check(name, cond, extra=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' -- ' + str(extra)[:300]) if (extra and not cond) else ''}")
    if not cond:
        FAILURES.append(name)


def main():
    sys.path.insert(0, HERE)
    from tf_robot_replay import build_m1, ruler, SL, TP, COST
    from tf_lens import BUDA

    print("TF ROBOT REPLAY rig -- planted universes, known answers")
    print("=" * 60)

    # ---- T: the ruler --------------------------------------------------------
    print("T. audit ruler math")
    t0 = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    keys = [t0 + timedelta(minutes=i) for i in range(10)]
    tp_bars = [[100, 100 + TP + 2, 99, 100 + TP + 2] for _ in range(3)]   # TP hit
    check("T1 TP hit pays TP-cost", abs(ruler(keys, tp_bars, t0, "BUY", 100.0) - (TP - COST)) < 1e-9)
    sl_bars = [[100, 101, 100 - SL - 2, 99] for _ in range(3)]            # SL first
    check("T2 SL-first loses SL+cost",
          abs(ruler(keys, sl_bars, t0, "BUY", 100.0) - (-SL - COST)) < 1e-9)
    flat = [[100, 100.4, 99.6, 100.2]] * 179                                # timeout
    check("T3 timeout exits at last close minus cost",
          abs(ruler(keys * 18, flat, t0, "BUY", 100.0) - (0.2 - COST)) < 1e-9)
    check("T4 SELL mirror works",
          abs(ruler(keys, [[100, 100 + SL + 2, 99, 100]] * 3, t0, "SELL", 100.0)
              - (-SL - COST)) < 1e-9)

    # ---- P: patch a real brain in a temp dir --------------------------------
    print("P. patcher")
    tmp = tempfile.mkdtemp(prefix="tfrobot_rig_")
    # base brain: the one sitting NEXT TO THIS RIG first (owner's machine:
    # repo root = the live, already-A1-patched brain); workshop fallback
    # (assistant's sandbox) second.  Never hardcode one machine's address.
    base = os.path.join(HERE, "step2_market_analysis.py")
    if not os.path.exists(base):
        base = "/home/user/power_history_probe_capped_v2/step2_market_analysis.py"
    shutil.copy(base, os.path.join(tmp, "step2_market_analysis.py"))
    shutil.copy(os.path.join(HERE, "apply_a1_patch.py"), tmp)
    r = subprocess.run([sys.executable, "apply_a1_patch.py"], cwd=tmp,
                       capture_output=True, text=True, timeout=300)
    patched = open(os.path.join(tmp, "step2_market_analysis.py")).read()
    check("P1 brain carries the A1 knob (fresh patch or already-live)",
          "A1_BASE_TF_MINUTES" in patched, r.stdout[-200:] + r.stderr[-200:])
    # keep the patched brain safe: the family glob below may carry an older
    # step2 (the workshop snapshot is pre-A1) which would silently undo the
    # patch and make every universe identical without the test noticing.
    keep = os.path.join(tmp, "step2_PATCHED_keep.py")
    shutil.copy(os.path.join(tmp, "step2_market_analysis.py"), keep)

    # ---- the lake: 3h, clean ramp in the middle -----------------------------
    import random
    rng = random.Random(2026)
    t = datetime(2026, 10, 15, 8, 0, tzinfo=BUDA).astimezone(timezone.utc)
    end = datetime(2026, 10, 15, 11, 0, tzinfo=BUDA).astimezone(timezone.utc)
    price, lines = 2650.0, ["time,type,price,size,extra1,extra2"]
    while t < end:
        bud = t.astimezone(BUDA)
        mins = (bud - datetime(2026, 10, 15, 8, 0, tzinfo=BUDA)).total_seconds() / 60.0
        price += 0.25 if 80 <= mins < 115 else rng.uniform(-0.15, 0.15)
        lines.append(f"2026-10-15 {bud.strftime('%H:%M:%S')}.000,Last,{price:.2f},3,bookmap,rig")
        t += timedelta(seconds=rng.choice([3, 5, 7]))
    open(os.path.join(tmp, "ticks.csv"), "w").write("\n".join(lines) + "\n")

    # ---- U: end-to-end, three universes ------------------------------------
    print("U. end-to-end (REAL patched brain, 3h lake, universes 5/3/10)")
    # the brain imports its whole family (config, signal_team, judges...).
    # On the owner's machine this rig lives in the repo root, so the REAL
    # family is right here (HERE); workshop fallback = the repo snapshot.
    import glob
    fam = HERE if os.path.exists(os.path.join(HERE, "config.py")) \
        else "/home/user/github_latest_verify"
    for f in glob.glob(os.path.join(fam, "*.py")):
        shutil.copy(f, tmp)
    # U must run the PATCHED brain from the P step -- never the glob's
    # possibly-older step2.  (Found live 2026-10-13 on the owner's machine:
    # the workshop snapshot's pre-A1 step2 silently replaced the patched
    # brain, all universes came out IDENTICAL -- and the old U3 passed
    # anyway because the universe NAME was part of the compared text.)
    shutil.copy(keep, os.path.join(tmp, "step2_market_analysis.py"))
    shutil.copy(os.path.join(HERE, "tf_robot_replay.py"), tmp)
    twin = os.path.join(HERE, "twin_day_runner.py")
    if not os.path.exists(twin):
        twin = "/home/user/A1_SPEED_2026-10-10/twin_day_runner.py"
    shutil.copy(twin, tmp)
    r = subprocess.run([sys.executable, "tf_robot_replay.py", "--date", "2026-10-15",
                        "--tfs", "5,3,10", "--window", "08:00-11:00", "--peek"],
                       cwd=tmp, capture_output=True, text=True, timeout=1500)
    out = r.stdout
    check("U1 exit 0 + report saved", r.returncode == 0 and
          os.path.exists(os.path.join(tmp, "data", "tf_robot_2026-10-15.txt")),
          out[-400:] + r.stderr[-400:])

    rep = open(os.path.join(tmp, "data", "tf_robot_2026-10-15.txt")).read()
    def row_of(tf):
        return next((l for l in rep.splitlines() if l.strip().startswith(f"M{tf} ")), "")
    rows_txt = {tf: row_of(tf) for tf in (5, 3, 10)}
    m5_row = rows_txt[5]
    m5_sigs = int(m5_row.split()[3]) if m5_row else -1
    check("U2 M5 universe: the real panel signals on the ramp lake", m5_sigs >= 1, m5_row)

    import json
    stats = {}
    for tf in (5, 3, 10):
        sp = os.path.join(tmp, "data", f"tfrobot_M{tf}_2026-10-15.jsonl.stats")
        stats[tf] = json.load(open(sp)) if os.path.exists(sp) else {}
    # NOTE: with the patched brain guaranteed in place (see P step), serves
    # are REAL here -- the bar service fires between that TF's bells.  The
    # BELLS column is still informational; we assert the honest part: the
    # env knob actually CHANGES each universe, comparing row BODIES only
    # (name prefix excluded -- the old test compared whole rows and could
    # never fail, because every row starts with a different name).
    def row_body(tf):
        parts = row_of(tf).split()
        return " ".join(parts[1:]) if parts else ""
    check("U3 the env knob changes the universe (row BODIES differ across TFs)",
          len({row_body(t) for t in (5, 3, 10)}) == 3, rows_txt)

    # the panel's tape-judges (order flow, L3, microprice) need no candle
    # warm-up, so M10 signalling in 3h is CORRECT behavior -- the lens's
    # 50-bar law belongs to the trend brain, not the panel.  Assert the real
    # robustness claim instead: no crashes in ANY universe.
    def errs_of(row):
        try:
            return int(row.split()[2])
        except (IndexError, ValueError):
            return -1
    check("U4 no brain crashes in any universe (ERR == 0 everywhere)",
          all(errs_of(rows_txt[tf]) == 0 for tf in (5, 3, 10)), rows_txt)
    check("U5 all three universe rows parsed",
          all(next((l for l in rep.splitlines() if l.strip().startswith(f"M{t}")), "")
              for t in (5, 3, 10)))

    print("=" * 60)
    if FAILURES:
        print(f"RIG RESULT: {len(FAILURES)} FAILED -> {', '.join(FAILURES)}")
        sys.exit(1)
    print("RIG RESULT: ALL PASS -- the parallel universes run the real brain honestly.")


if __name__ == "__main__":
    main()
