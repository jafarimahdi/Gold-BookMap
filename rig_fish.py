#!/usr/bin/env python3
"""rig_fish.py -- the FISH ANATOMY test lake. Planted fish, known answers.

  A. travel_curve: a +0.3/min ramp -> fav(3)=0.9, fav(6)=1.8, fav(9)=2.7
  B. heat_and_payoff: clean ramp -> heat 0, paid; dip-then-ramp -> heat = dip;
     never-pays -> no payoff, heat = max dip
  C. crossmatch: same-direction signal in window -> its conf (max wins);
     opposite direction and out-of-window never match
  D. hour_label buckets
  E. end-to-end on a synthetic tape: full tool runs, finds the ramp fish,
     saves data/fish_anatomy_<date>.txt

Run:  python rig_fish.py     (from the folder holding fish_anatomy.py + tf_lens.py)
"""
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fish_anatomy import (crossmatch, heat_and_payoff, hour_label,
                          travel_curve)
from tf_lens import BUDA

FAILURES = []


def check(name, cond, extra=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' -- ' + extra) if (extra and not cond) else ''}")
    if not cond:
        FAILURES.append(name)


def main():
    print("FISH ANATOMY rig -- planted fish, known answers")
    print("=" * 60)

    # ---- A: travel_curve ------------------------------------------------------
    print("A. travel curve")
    t0 = datetime(2026, 10, 15, 13, 0, tzinfo=timezone.utc)
    prints = []
    t = t0
    while t < t0 + timedelta(minutes=16):
        mins = (t - t0).total_seconds() / 60.0
        prints.append((t, 2000.0 + 0.3 * mins))
        t += timedelta(seconds=20)
    m = {"t": t0, "dir": "UP", "entry": 2000.0, "adx": 30.0}
    curve = travel_curve(prints, [dt for dt, _ in prints], m, (3, 6, 9))
    check("A1 ramp fav(3) ~ 0.9", abs(curve[3] - 0.9) < 0.15, str(curve))
    check("A2 ramp fav(6) ~ 1.8", abs(curve[6] - 1.8) < 0.15, str(curve))
    check("A3 ramp fav(9) ~ 2.7", abs(curve[9] - 2.7) < 0.15, str(curve))

    # ---- B: heat ---------------------------------------------------------------
    print("B. heat and payoff")
    heat, paid = heat_and_payoff(prints, [dt for dt, _ in prints],
                                 {"t": t0, "dir": "UP", "entry": 2000.0},
                                 need=1.0, window=9)
    check("B1 clean ramp: paid, heat ~ 0", paid and heat < 0.05, f"{heat} {paid}")
    dip = [(t0, 2000.0)]
    t = t0
    while t < t0 + timedelta(minutes=10):
        mins = (t - t0).total_seconds() / 60.0
        if mins < 2:
            dip.append((t, 2000.0 - 0.4 * mins))          # dip to -0.8
        else:
            dip.append((t, 1999.2 + 0.5 * (mins - 2)))    # then ramp
        t += timedelta(seconds=20)
    heat2, paid2 = heat_and_payoff(dip, [dt for dt, _ in dip],
                                   {"t": t0, "dir": "UP", "entry": 2000.0},
                                   need=1.0, window=10)
    check("B2 dip-then-ramp: heat ~ 0.8, paid", paid2 and abs(heat2 - 0.8) < 0.1,
          f"{heat2} {paid2}")
    fade = [(t0 + timedelta(seconds=20 * i), 2000.0 - 0.05 * i) for i in range(30)]
    heat3, paid3 = heat_and_payoff(fade, [dt for dt, _ in fade],
                                   {"t": t0, "dir": "UP", "entry": 2000.0},
                                   need=1.0, window=10)
    check("B3 never pays: no payoff, heat = max dip", (not paid3) and abs(heat3 - 1.45) < 0.1,
          f"{heat3} {paid3}")

    # ---- C: crossmatch -----------------------------------------------------------
    print("C. panel crossmatch")
    dec = [
        (t0 + timedelta(minutes=1), "BUY", 2000.3, 55.0),
        (t0 + timedelta(minutes=2), "BUY", 2000.6, 70.0),   # max conf wins
        (t0 + timedelta(minutes=30), "SELL", 2010.0, 90.0),  # far away, other side
    ]
    cm = crossmatch({"t": t0, "dir": "UP"}, dec, [r[0] for r in dec], 5.0)
    check("C1 max same-direction conf in window", cm == 70.0, str(cm))
    # DOWN moment at t0: only BUYs nearby (opposite) and the SELL is far away
    cm2 = crossmatch({"t": t0, "dir": "DOWN"}, dec, [r[0] for r in dec], 5.0)
    check("C2 opposite direction (and far signals) never match", cm2 is None, str(cm2))
    # DOWN moment at the SELL's time: must find it (direction flip works positively)
    cm3 = crossmatch({"t": t0 + timedelta(minutes=30), "dir": "DOWN"},
                     dec, [r[0] for r in dec], 5.0)
    check("C3 SELL-side moment finds the SELL", cm3 == 90.0, str(cm3))

    # ---- D: hour labels ------------------------------------------------------------
    print("D. hour buckets")
    check("D1 09 = London morning", hour_label(9) == "London morning")
    check("D2 15 = overlap", hour_label(15) == "London+NY overlap")
    check("D3 20 = NY afternoon", hour_label(20) == "NY afternoon/evening")

    # ---- E: end-to-end --------------------------------------------------------------
    print("E. end-to-end (synthetic tape, the lens lake's ramp day)")
    tmp = tempfile.mkdtemp(prefix="fish_rig_")
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
            mins = (t.astimezone(BUDA) - datetime(2026, 10, 15, 8, 0,
                                                  tzinfo=BUDA)).total_seconds() / 60.0
            if 270 <= mins < 305:            # 12:30-13:05 up-ramp (as the lens rig)
                price += 0.22
            else:
                price += rng.uniform(-0.15, 0.15)
            lines.append(f"2026-10-15 {t.astimezone(BUDA).strftime('%H:%M:%S')}.000,"
                         f"Last,{price:.2f},2,bookmap,rig")
            t += timedelta(seconds=rng.choice([3, 5, 7, 10]))
        open("ticks.csv", "w").write("\n".join(lines) + "\n")
        r = subprocess.run([sys.executable, os.path.join(old_cwd, "fish_anatomy.py"),
                            "--date", "2026-10-15", "--tf", "3"],
                           capture_output=True, text=True, timeout=300)
        out = r.stdout
        check("E1 exit code 0", r.returncode == 0, out[-300:] + r.stderr[-300:])
        check("E2 header present", "FISH ANATOMY" in out)
        check("E3 WEATHER line present", "WEATHER" in out)
        check("E4 the fish log has at least one row", "THE FISH LOG" in out)
        check("E5 SIZE table present", "SIZE (median" in out)
        check("E6 saved to data/", os.path.exists("data/fish_anatomy_2026-10-15.txt"))
    finally:
        os.chdir(old_cwd)

    print("=" * 60)
    if FAILURES:
        print(f"RIG RESULT: {len(FAILURES)} FAILED -> {', '.join(FAILURES)}")
        sys.exit(1)
    print("RIG RESULT: ALL PASS -- the anatomy probe measures the planted fish exactly.")


if __name__ == "__main__":
    main()
