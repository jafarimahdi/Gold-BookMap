#!/usr/bin/env python3
"""rig_selftest.py -- the TF LENS test lake (run BEFORE trusting real output).

Five tests, all offline (synthetic data only, nothing to do with the robot):
  A. candle math: buckets, OHLC, close-of-bar timestamps
  B. the ADX port is a faithful port: per-bar series == recomputing step2's
     compute_adx from scratch on every prefix (max diff < 1e-9)
  C. a designed day with a known up-ramp and a known down-ramp:
     moments fire, warm-up respected, finer clocks shout first
  D. empty / too-short days end cleanly (no crash, honest message)
  E. end-to-end: a fake ticks.csv in the Bookmap line format -> the full
     tool runs, saves data/tf_lens_<date>.txt, exits 0

Run:  python rig_selftest.py     (from the folder holding tf_lens.py)
"""
import os, random, statistics, subprocess, sys, tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tf_lens
from tf_lens import (BUDA, adx_series, build_candles, detect_moments,
                     travel_stats)
from datetime import timezone

FAILURES = []


def check(name, cond, extra=""):
    tag = "PASS" if cond else "FAIL"
    print(f"  [{tag}] {name}{(' -- ' + extra) if extra and not cond else ''}")
    if not cond:
        FAILURES.append(name)


def brute_adx(high, low, close, period=14):
    """step2's TechnicalAnalyzer.compute_adx recomputed from scratch on this
    exact prefix (pure-python mirror of the numpy code, full-array style).
    Returns (adx, pdi_last, mdi_last) or (None, ...) where the robot would
    not answer yet."""
    n = len(high)
    if n < 2 * period + 1:
        return None, None, None
    up = [high[i] - high[i - 1] for i in range(1, n)]
    dn = [-(low[i] - low[i - 1]) for i in range(1, n)]
    pdm = [u if (u > d and u > 0) else 0.0 for u, d in zip(up, dn)]
    mdm = [d if (d > u and d > 0) else 0.0 for u, d in zip(up, dn)]
    pc = close[:-1]
    tr = [max(high[i + 1] - low[i + 1], abs(high[i + 1] - pc[i]),
              abs(low[i + 1] - pc[i])) for i in range(n - 1)]

    def wsum(data, p):                      # Wilder seed='sum' over full array
        if len(data) < p:
            return None
        out = [None] * len(data)
        out[p - 1] = sum(data[:p])
        for i in range(p, len(data)):
            out[i] = out[i - 1] - out[i - 1] / p + data[i]
        return out

    tr_s, pdm_s, mdm_s = wsum(tr, period), wsum(pdm, period), wsum(mdm, period)
    if tr_s is None or pdm_s is None or mdm_s is None:
        return None, None, None
    pdi_arr, mdi_arr = [None] * len(tr), [None] * len(tr)
    for k in range(period - 1, len(tr)):
        s = tr_s[k] + 1e-10
        pdi_arr[k] = 100.0 * pdm_s[k] / s
        mdi_arr[k] = 100.0 * mdm_s[k] / s
    dx = []
    for k in range(len(tr)):
        if pdi_arr[k] is not None:
            dx.append(100.0 * abs(pdi_arr[k] - mdi_arr[k]) /
                      (pdi_arr[k] + mdi_arr[k] + 1e-10))
    if len(dx) < period:
        return None, pdi_arr[-1], mdi_arr[-1]
    adx_v = statistics.fmean(dx[:period])   # Wilder seed='mean' (ADX)
    for i in range(period, len(dx)):
        adx_v = (adx_v * (period - 1) + dx[i]) / period
    return (min(100.0, max(0.0, adx_v)), pdi_arr[-1], mdi_arr[-1])


def make_day():
    """2026-10-15, Budapest 08:00-18:00: chop, UP ramp 12:30-13:05 (+~24 pts),
    plateau, DOWN ramp 15:00-15:25 (-15), chop. Prints every ~10 s."""
    start = datetime(2026, 10, 15, 8, 0, tzinfo=BUDA).astimezone(timezone.utc)
    rng = random.Random(42)
    prints = []
    price = 2000.0
    t = start
    end = start + timedelta(hours=10)
    while t < end:
        local_min = (t.astimezone(BUDA) - datetime(2026, 10, 15, 8, 0, tzinfo=BUDA)).total_seconds() / 60.0
        if 270 <= local_min < 305:            # 12:30-13:05 up-ramp
            price += 0.75
        elif 420 <= local_min < 445:          # 15:00-15:25 down-ramp
            price -= 0.65
        else:
            price += rng.uniform(-0.15, 0.15)  # chop
        prints.append((t, round(price + rng.uniform(-0.05, 0.05), 2)))
        t += timedelta(seconds=10)
    return prints


def main():
    print("TF LENS rig -- the test lake")
    print("=" * 60)

    # ---- A: candle math -----------------------------------------------------
    print("A. candle math")
    from datetime import timezone as tz
    base = datetime(2026, 10, 15, 10, 1, tzinfo=BUDA).astimezone(tz.utc)
    trades = [
        (base, 2000.0),
        (base + timedelta(minutes=2), 2001.0),
        (base + timedelta(minutes=6), 2002.0),
        (base + timedelta(minutes=7), 2001.5),
    ]
    keys, candles = build_candles(trades, 5)
    check("A1 two buckets at M5", len(keys) == 2 and
          keys[0].minute == 0 and keys[1].minute == 5)
    c0 = candles[0]
    check("A2 first bar OHLCV", c0[0] == 2000.0 and c0[1] == 2001.0 and
          c0[2] == 2000.0 and c0[3] == 2001.0 and c0[4] == 2.0)
    check("A3 second bar close", candles[1][3] == 2001.5)

    # ---- B: ADX port == step2 prefix recomputation ---------------------------
    print("B. ADX port vs step2-style prefix recomputation")
    rng = random.Random(7)
    n = 130
    closes, highs, lows = [], [], []
    p = 2000.0
    for _ in range(n):
        p += rng.uniform(-1.0, 1.0)
        c = p
        h = c + abs(rng.uniform(0, 0.8))
        l = c - abs(rng.uniform(0, 0.8))
        closes.append(round(c, 4)); highs.append(round(h, 4)); lows.append(round(l, 4))
    a_s, p_s, m_s = adx_series(highs, lows, closes)
    worst = 0.0
    worst_p = 0.0
    for i in range(28, n):
        a_b, p_b, m_b = brute_adx(highs[:i + 1], lows[:i + 1], closes[:i + 1])
        if a_b is None:
            continue
        worst = max(worst, abs(a_s[i] - a_b))
        worst_p = max(worst_p, abs(p_s[i] - p_b))
    check("B1 ADX series matches prefix recomputation", worst < 1e-9,
          f"max diff {worst:.2e}")
    check("B2 +DI series matches", worst_p < 1e-9, f"max diff {worst_p:.2e}")

    # ---- C: the designed day --------------------------------------------------
    print("C. designed day: known ramps, warm-up, ordering")
    prints = make_day()
    times = [dt for dt, _ in prints]
    res = {}
    for tf in (2, 3, 5):
        keys, candles = build_candles(prints, tf)
        moms = detect_moments(keys, candles, tf)
        res[tf] = (keys, candles, moms)
    for tf in (2, 3, 5):
        warm_end = prints[0][0] + timedelta(minutes=50 * tf)
        ok = all(m["t"] >= warm_end for m in res[tf][2])
        check(f"C{tf} warm-up respected at M{tf}", ok)
    ups = {tf: [m for m in res[tf][2] if m["dir"] == "UP"] for tf in (2, 3, 5)}

    def in_window(m, d0, d1, dirn):
        lt = m["t"].astimezone(BUDA)
        return (m["dir"] == dirn and
                datetime(2026, 10, 15, d0[0], d0[1], tzinfo=BUDA) <= lt <=
                datetime(2026, 10, 15, d1[0], d1[1], tzinfo=BUDA))

    # the designed UP ramp 12:30-13:05: every clock must catch it, finer first
    ramp_up = {tf: [m for m in res[tf][2] if in_window(m, (12, 30), (13, 35), "UP")]
               for tf in (2, 3, 5)}
    for tf in (2, 3, 5):
        check(f"C-ramp M{tf} catches the UP ramp", len(ramp_up[tf]) >= 1)
    if all(ramp_up.values()):
        check("C-ramp finer shouts first (M2<=M3<=M5)",
              ramp_up[2][0]["t"] <= ramp_up[3][0]["t"] <= ramp_up[5][0]["t"])
        check("C-ramp finer enters cheaper",
              ramp_up[2][0]["entry"] < ramp_up[5][0]["entry"],
              f"M2 {ramp_up[2][0]['entry']:.2f} vs M5 {ramp_up[5][0]['entry']:.2f}")
        print("       ramp UP caught at: " + " | ".join(
            f"M{tf} {ramp_up[tf][0]['t'].astimezone(BUDA).strftime('%H:%M')} "
            f"@ {ramp_up[tf][0]['entry']:.2f}" for tf in (2, 3, 5)))
    # the designed DOWN ramp 15:00-15:25: M3 catches it, M5 does not
    # (the BASE-MISS column demonstrated: coarser clocks go blind on short moves)
    dn_m3 = [m for m in res[3][2] if in_window(m, (15, 0), (15, 40), "DOWN")]
    dn_m5 = [m for m in res[5][2] if in_window(m, (15, 0), (15, 40), "DOWN")]
    check("C-ramp M3 catches the DOWN ramp", len(dn_m3) >= 1)
    check("C-ramp M5 misses the DOWN ramp (base-miss shown)", len(dn_m5) == 0)
    # noise honesty: the finer clock also sees MORE moments overall (noise incl.)
    check("C-noise finer clock sees more moments overall",
          len(res[2][2]) > len(res[5][2]),
          f"M2 {len(res[2][2])} vs M5 {len(res[5][2])}")
    # travel on the M5 up moment: the ramp is real, favourable travel must be positive
    if ups[5]:
        st = travel_stats(prints, times, ups[5][:1], 6, 1.0)
        check("C-travel real fish on the designed ramp",
              st["fav_med"] is not None and st["fav_med"] >= 1.0,
              f"fav {st['fav_med']}")
        # no-window handling: a moment at the very end of the tape
        fake_late = [{"t": prints[-1][0], "dir": "UP", "entry": 2000.0}]
        st2 = travel_stats(prints, times, fake_late, 6, 1.0)
        check("C-travel no-window counted honestly", st2["n"] == 0 and st2["no_window"] == 1)

    # ---- D: empty / short days ------------------------------------------------
    print("D. empty and too-short days")
    err = tf_lens.read_day_trades("2026-01-20")
    check("D1 no tape -> honest message", err is not None and err[0] is None)
    short = [(datetime(2026, 10, 15, 9, 0, tzinfo=BUDA).astimezone(tz.utc) +
              timedelta(minutes=i), 2000.0 + i * 0.01) for i in range(30)]
    keys, candles = build_candles(short, 5)
    moms = detect_moments(keys, candles, 5)
    check("D2 30 minutes of tape -> zero moments (warm-up), no crash",
          len(candles) <= 10 and moms == [])

    # ---- E: end-to-end with a fake ticks.csv -----------------------------------
    print("E. end-to-end (subprocess, Bookmap line format)")
    tmp = tempfile.mkdtemp(prefix="tf_lens_rig_")
    old_cwd = os.getcwd()
    try:
        os.chdir(tmp)
        with open("ticks.csv", "w", encoding="utf-8") as f:
            f.write("time,type,price,size,extra1,extra2\n")
            for dt, price in prints:
                f.write(f"{dt.astimezone(BUDA).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]},"
                        f"Last,{price:.2f},2,bookmap,rig\n")
        r = subprocess.run(
            [sys.executable, os.path.join(old_cwd, "tf_lens.py"),
             "--date", "2026-10-15", "--tfs", "2,3", "--windows", "6,9"],
            capture_output=True, text=True, timeout=300)
        out = r.stdout
        check("E1 exit code 0", r.returncode == 0, out[-400:] + r.stderr[-400:])
        check("E2 header present", "TF LENS (rung 1)" in out)
        check("E3 tape read from ticks.csv", "tape: " in out and "trade prints" in out)
        check("E4 baseline row present", "BASE (robot's home)" in out)
        check("E5 saved to data/", "[saved]" in out and os.path.exists("data/tf_lens_2026-10-15.txt"))
    finally:
        os.chdir(old_cwd)

    print("=" * 60)
    if FAILURES:
        print(f"RIG RESULT: {len(FAILURES)} FAILED -> {', '.join(FAILURES)}")
        sys.exit(1)
    print("RIG RESULT: ALL PASS -- the lens counts correctly on the test lake.")


if __name__ == "__main__":
    main()
