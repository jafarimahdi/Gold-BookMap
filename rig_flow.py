#!/usr/bin/env python3
"""rig_flow.py -- the SPY'S NOTEBOOK test lake. Planted flows, known answers.

  A. tick rule: exact sign sequence on planted prices
  B. bucket math: cvd/vol/n per tape-time bucket on planted prints
  C. tilt + persistence: 12 quiet buckets then 10 strong-buy buckets ->
     exactly ONE UP episode starting at the first buy bucket; alternating
     noise -> ZERO episodes (persistence blocks); dead buckets transparent
  D. warning matcher: same-dir episode before the fish -> WARNED with head;
     wrong-dir -> CONFUSED; nothing -> quiet
  E. false alarms: episode with a fish within 10 min = justified; without = false
  F. end-to-end (subprocess, Bookmap line format): sustained buying then a
     ramp -> the fish is WARNED, file saved, exit 0

Run:  python rig_flow.py     (from the folder holding flow_probe.py + tf_lens.py)
"""
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from flow_probe import (baseline_move_rate, build_buckets, episodes,
                        false_alarms, in_session, move_after, sign_prints,
                        warn_moments)
from tf_lens import BUDA

FAILURES = []


def check(name, cond, extra=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' -- ' + str(extra)) if (extra and not cond) else ''}")
    if not cond:
        FAILURES.append(name)


def main():
    print("FLOW PROBE rig -- planted flows, known answers")
    print("=" * 60)

    # ---- A: tick rule -----------------------------------------------------
    print("A. tick rule (who hit the tape)")
    signs = sign_prints([(None, p, 1.0) for p in (100, 100, 101, 100, 100, 102)])
    check("A1 exact signs [0,0,+1,-1,-1,+1]",
          signs == [0, 0, 1, -1, -1, 1], signs)

    # ---- B: bucket math ----------------------------------------------------
    print("B. tape-time buckets")
    t0 = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    trades = [(t0, 100.0, 2.0), (t0 + timedelta(seconds=5), 101.0, 3.0),
              (t0 + timedelta(seconds=12), 100.0, 1.0)]
    bs = build_buckets(trades, [1, 1, -1], 10)
    check("B1 two buckets", len(bs) == 2, len(bs))
    check("B2 bucket1 cvd 5 vol 5 n 2",
          bs[0]["cvd"] == 5 and bs[0]["vol"] == 5 and bs[0]["n"] == 2, bs[0])
    check("B3 bucket2 cvd -1 vol 1 n 1",
          bs[1]["cvd"] == -1 and bs[1]["vol"] == 1 and bs[1]["n"] == 1, bs[1])

    # ---- C: tilt + persistence ----------------------------------------------
    print("C. tilt, persistence, dead buckets")
    K = 1770000000  # any epoch base

    def mk(k, rate, vol=10.0):
        return {"k": k, "cvd": rate * vol, "vol": vol, "n": 2}

    quiet = [mk(K + 10 * i, 0.0) for i in range(12)]
    buys = [mk(K + 10 * (12 + i), 0.9) for i in range(10)]
    eps = episodes(quiet + buys)
    check("C1 exactly one episode", len(eps) == 1, len(eps))
    check("C2 it is UP", eps and eps[0]["dir"] == "UP", eps)
    check("C3 it starts at the first buy bucket",
          eps and eps[0]["start"] == buys[0]["k"], eps)
    check("C4 it ends at the last buy bucket",
          eps and eps[0]["end"] == buys[-1]["k"], eps)

    chop = [mk(K + 10 * i, 0.9 if i % 2 == 0 else -0.9) for i in range(20)]
    check("C5 alternating noise -> zero episodes (persistence blocks)",
          episodes(chop) == [], episodes(chop))

    dead_inside = [mk(K + 10 * i, 0.9) for i in range(10)]
    dead_inside.insert(4, {"k": K + 10 * 4 + 5, "cvd": 0.0, "vol": 1.0, "n": 1})
    dead_inside.sort(key=lambda b: b["k"])
    epsd = episodes(quiet + dead_inside)
    check("C6 a dead bucket mid-lean is transparent (still one UP episode)",
          len(epsd) == 1 and epsd[0]["dir"] == "UP", epsd)

    # ---- D: warning matcher ---------------------------------------------------
    print("D. fish warnings")
    t_fish = datetime(2026, 10, 15, 12, 33, 0, tzinfo=timezone.utc)
    ep_up = {"dir": "UP", "start": t_fish.timestamp() - 150,
             "end": t_fish.timestamp() - 30}
    mom_up = {"t": t_fish, "dir": "UP", "entry": 2000.0, "adx": 30.0}
    r = warn_moments([mom_up], [ep_up], 5.0)
    check("D1 same-dir tilt before the fish -> WARNED",
          r[0]["status"] == "WARNED", r)
    check("D2 head start 2.5 min", r[0]["status"] == "WARNED"
          and abs(r[0]["head"] - 2.5) < 0.01, r)
    mom_dn = {"t": t_fish, "dir": "DOWN", "entry": 2000.0, "adx": 30.0}
    r2 = warn_moments([mom_dn], [ep_up], 5.0)
    check("D3 wrong-dir tilt -> CONFUSED", r2[0]["status"] == "CONFUSED", r2)
    r3 = warn_moments([mom_up], [], 5.0)
    check("D4 no tilts -> quiet", r3[0]["status"] == "quiet", r3)

    # ---- E: false alarms ---------------------------------------------------------
    print("E. noise judging")
    e1 = {"dir": "UP", "start": t_fish.timestamp() - 300, "end": t_fish.timestamp()}
    e2 = {"dir": "DOWN", "start": t_fish.timestamp() - 300, "end": t_fish.timestamp()}
    mbs = {2: [], 3: [mom_up]}
    just, false = false_alarms([e1, e2], mbs, 10.0)
    check("E1 UP lean with a fish within 10 min -> justified",
          e1 in just and e1 not in false, (just, false))
    check("E2 DOWN lean with no DOWN fish -> false alarm",
          e2 in false and e2 not in just, (just, false))

    # ---- F: end-to-end --------------------------------------------------------------
    print("F. end-to-end (synthetic tape: quiet, sustained buying, then a ramp)")
    tmp = tempfile.mkdtemp(prefix="flow_rig_")
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp)
        import random
        rng = random.Random(2026)
        t = datetime(2026, 10, 15, 8, 0, tzinfo=BUDA).astimezone(timezone.utc)
        end = datetime(2026, 10, 15, 16, 0, tzinfo=BUDA).astimezone(timezone.utc)
        price = 2650.0
        lines = ["time,type,price,size,extra1,extra2"]
        while t < end:
            bud = t.astimezone(BUDA)
            mins = (bud - datetime(2026, 10, 15, 8, 0, tzinfo=BUDA)).total_seconds() / 60.0
            if 285 <= mins < 291:            # 12:45-12:51 sustained buying
                price += 0.10
                size = 3
            elif 291 <= mins < 325:          # 12:51-13:25 the ramp
                price += 0.22
                size = 3
            else:
                price += rng.uniform(-0.15, 0.15)
                size = 3
            lines.append(f"2026-10-15 {bud.strftime('%H:%M:%S')}.000,Last,"
                         f"{price:.2f},{size},bookmap,rig")
            t += timedelta(seconds=rng.choice([3, 5, 7]))
        open("ticks.csv", "w").write("\n".join(lines) + "\n")
        r = subprocess.run([sys.executable, os.path.join(old_cwd, "flow_probe.py"),
                            "--date", "2026-10-15", "--moment-tf", "3"],
                           capture_output=True, text=True, timeout=600)
        out = r.stdout
        check("F1 exit code 0", r.returncode == 0, out[-300:] + r.stderr[-300:])
        check("F2 header present", "FLOW PROBE" in out)
        check("F3 the ramp fish is WARNED", "WARNED" in out)
        check("F4 noise summary present", "NOISE:" in out)
        check("F5 move view with lift present", "LIFT" in out)
        check("F6 saved to data/", os.path.exists("data/flow_probe_2026-10-15.txt"))
    finally:
        os.chdir(old_cwd)

    # ---- G: v1.1 session filter + move view -------------------------------------
    print("G. v1.1: session filter and the move view")
    from datetime import time as dtime
    sess = (dtime(8, 0), dtime(23, 0))
    t_night = datetime(2026, 10, 15, 2, 0, tzinfo=BUDA).timestamp()
    t_day = datetime(2026, 10, 15, 12, 0, tzinfo=BUDA).timestamp()
    check("G1 overnight lean excluded", not in_session(t_night, sess))
    check("G2 midday lean kept", in_session(t_day, sess))

    tm = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    # a lean at tm, price ramps +1.5 within 6 min -> True
    times = [tm + timedelta(seconds=i) for i in range(0, 420, 20)]
    prices = [2000.0 + 0.06 * i for i in range(len(times))]  # 0.06/20s -> +1.08 by 6 min
    check("G3 move_after: ramp after the lean counts",
          move_after(times, prices, tm.timestamp(), "UP", 6.0, 1.0) is True)
    flat = [2000.0] * len(times)
    check("G4 move_after: flat tape does not count",
          move_after(times, flat, tm.timestamp(), "UP", 6.0, 1.0) is False)
    # baseline: flat tape -> 0.0; rampy tape -> > 0
    b_flat = baseline_move_rate(times, flat, sess, 6.0, 1.0)
    b_ramp = baseline_move_rate(times, prices, sess, 6.0, 1.0)
    check("G5 baseline flat tape = 0", b_flat == 0.0, b_flat)
    check("G6 baseline rampy tape > 0", b_ramp and b_ramp > 0.0, b_ramp)

    print("=" * 60)
    if FAILURES:
        print(f"RIG RESULT: {len(FAILURES)} FAILED -> {', '.join(FAILURES)}")
        sys.exit(1)
    print("RIG RESULT: ALL PASS -- the spy's notebook measures the planted flows exactly.")


if __name__ == "__main__":
    main()
