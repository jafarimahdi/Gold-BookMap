#!/usr/bin/env python3
"""test_a1_rig.py -- A1 unit rig: proves the bar-service semantics on the
PATCHED step2_market_analysis.py BEFORE any twin-day run.

Tests:
  T1 constants + engine present
  T2 bucket math (incl. the DST-change weekend: UTC buckets unaffected)
  T3 serve identity: same bucket -> served snapshot, fresh quotes,
     verdict fields identical to the stored full compute, live pair re-run
  T4 bell: cross a 5-min boundary -> full compute (no serve note)
  T5 news force rule: WARNING/BLACKOUT state -> full compute
  T6 history shrink -> full compute
  T7 POWER history cap helper (300 ring)
  T8 fail-open: broken cache entry -> full compute, no crash
  T9 speed receipt: serve vs full compute wall time (informational)

Run from the folder holding the patched file:
    python test_a1_rig.py [step2_market_analysis.py]
"""
import importlib.util
import os
import sys
import time as _time
from datetime import datetime, timedelta, timezone

FAILURES = []


def check(name, cond, extra=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' -- ' + extra) if (extra and not cond) else ''}")
    if not cond:
        FAILURES.append(name)


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def synth_market(n_minutes=90, start=None, seed=11):
    """Deterministic M1 candles + tick_data; ramps included so judges wake up."""
    import random
    rng = random.Random(seed)
    t0 = start or datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)
    price = 2650.0
    trades = []
    for m in range(n_minutes):
        # a real up-ramp 10:00-10:40 so the trend brain has something to see
        if 120 <= m < 160:
            price += 0.35
        elif 200 <= m < 230:
            price -= 0.30
        else:
            price += rng.uniform(-0.25, 0.25)
        for s in range(6):
            p = price + rng.uniform(-0.10, 0.10)
            trades.append((t0 + timedelta(minutes=m, seconds=s * 10), round(p, 2),
                           round(rng.uniform(0.5, 3.0), 2)))
    return trades


def md_at(trades, tc, start):
    """market_data as of cycle time tc: M1 candles from start, ticks last 30 min."""
    upto = [(t, p, v) for (t, p, v) in trades if t <= tc]
    bars = {}
    for t, p, v in upto:
        b = t.replace(second=0, microsecond=0)
        if b not in bars:
            bars[b] = [p, p, p, p, v]
        else:
            bars[b][1] = max(bars[b][1], p)
            bars[b][2] = min(bars[b][2], p)
            bars[b][3] = p
            bars[b][4] += v
    keys = sorted(bars)
    candles = {"open": [bars[k][0] for k in keys], "high": [bars[k][1] for k in keys],
               "low": [bars[k][2] for k in keys], "close": [bars[k][3] for k in keys],
               "volume": [bars[k][4] for k in keys]}
    tick_data = [{"timestamp": t.isoformat(), "price": p, "volume": v}
                 for (t, p, v) in upto if t >= tc - timedelta(minutes=30)]
    last = upto[-1] if upto else (tc, 2650.0, 1.0)
    return {"symbol": "XAUUSD", "price": last[1], "bid": round(last[1] - 0.25, 2),
            "ask": round(last[1] + 0.25, 2), "volume": last[2],
            "tick_data": tick_data, "candles": candles, "has_data": True,
            "data_quality": {"source": "a1-rig"}}


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "step2_market_analysis.py"
    print(f"A1 unit rig -- {path}")
    print("=" * 64)
    s2 = load_module(os.path.abspath(path), "step2_a1_under_test")

    # ---- T1 ----------------------------------------------------------------
    print("T1 constants + engine")
    check("T1a A1_BASE_TF_MINUTES == 5", s2.A1_BASE_TF_MINUTES == 5)
    check("T1b POWER_HISTORY_MAX_BARS == 300", s2.POWER_HISTORY_MAX_BARS == 300)
    check("T1c engine functions present",
          all(hasattr(s2, n) for n in
              ("_a1_try_serve", "_a1_cache_store", "_a1_bucket_index",
               "_a1_last_closed_bucket", "_a1_cap_history_bars")))

    # ---- T2 ----------------------------------------------------------------
    print("T2 bucket math (UTC, DST-immune)")
    t = datetime(2026, 10, 9, 12, 3, tzinfo=timezone.utc)
    check("T2a 12:03 -> last closed bucket is 11:55",
          s2._a1_bucket_index(t) - 1 == s2._a1_bucket_index(t) - 1 and
          s2._a1_last_closed_bucket(t) == s2._a1_bucket_index(t) - 1)
    b1203 = s2._a1_bucket_index(datetime(2026, 10, 9, 12, 3, tzinfo=timezone.utc))
    b1200 = s2._a1_bucket_index(datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc))
    check("T2b 12:03 and 12:00 share a bucket", b1203 == b1200)
    b1205 = s2._a1_bucket_index(datetime(2026, 10, 9, 12, 5, tzinfo=timezone.utc))
    check("T2c 12:05 starts a new bucket", b1205 == b1200 + 1)
    ok = True
    for i in range(12):  # across the Oct-25 DST-change weekend, UTC buckets tick on
        a = datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc) + timedelta(minutes=5 * i)
        if s2._a1_bucket_index(a) != s2._a1_bucket_index(
                datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc)) + i:
            ok = False
    check("T2d DST weekend: buckets advance 1 per 5 min in UTC", ok)

    # ---- T3 ----------------------------------------------------------------
    print("T3 serve identity (same bucket, new quotes)")
    start = datetime(2026, 10, 9, 8, 0, tzinfo=timezone.utc)
    trades = synth_market(start=start)
    t_bell = datetime(2026, 10, 9, 10, 5, 1, tzinfo=timezone.utc)   # just after a bell
    md1 = md_at(trades, t_bell, start)
    snap1 = s2.analyze_market(md1, now=t_bell)
    s1_notes = " ".join(snap1.notes or [])
    check("T3a bell compute is a full compute (no serve note)",
          "A1 bar-service:" not in s1_notes)
    t_mid = t_bell + timedelta(seconds=90)                            # same bucket
    md2 = md_at(trades, t_mid, start)
    md2["price"] = md2["price"] + 1.7                                 # new quote
    md2["bid"] = round(md2["price"] - 0.25, 2); md2["ask"] = round(md2["price"] + 0.25, 2)
    snap2 = s2.analyze_market(md2, now=t_mid)
    s2_notes = " ".join(snap2.notes or [])
    check("T3b mid-bar cycle SERVED", "A1 bar-service:" in s2_notes)
    check("T3c served timestamp is the cycle's", snap2.timestamp == t_mid)
    check("T3d served price is the NEW quote", abs(snap2.price - md2["price"]) < 1e-9)
    check("T3e verdict fields identical to the bell compute",
          snap1.signal_direction == snap2.signal_direction and
          abs((snap1.confidence or 0) - (snap2.confidence or 0)) < 1e-12 and
          snap1.regime == snap2.regime and
          (snap1.power or {}).get("direction") == (snap2.power or {}).get("direction"))
    pair_notes = [n for n in snap2.notes if str(n).startswith("PAPER ENTRY:")]
    check("T3f live pair re-run on serve (exactly one PAPER ENTRY note)",
          len(pair_notes) == 1)
    check("T3g cached snapshot not mutated by the serve",
          "A1 bar-service:" not in " ".join(
              s2._A1_BAR_SERVICE_CACHE["entry"]["snapshot"].notes or []))

    # ---- T4 ----------------------------------------------------------------
    print("T4 bell boundary -> full compute")
    t_next = datetime(2026, 10, 9, 10, 10, 1, tzinfo=timezone.utc)   # next bell
    md3 = md_at(trades, t_next, start)
    snap3 = s2.analyze_market(md3, now=t_next)
    check("T4a after the next bell: full compute (no serve note)",
          "A1 bar-service:" not in " ".join(snap3.notes or []))
    check("T4b store refreshed to the new bucket",
          s2._A1_BAR_SERVICE_CACHE["entry"]["key"][1] ==
          s2._a1_last_closed_bucket(t_next))

    # ---- T5 ----------------------------------------------------------------
    print("T5 news force rule")
    t_mid2 = t_next + timedelta(seconds=60)
    md4 = md_at(trades, t_mid2, start)
    cached = s2._A1_BAR_SERVICE_CACHE["entry"]["snapshot"]
    old_state = getattr(cached.news, "news_state", None)
    try:
        cached.news.news_state = "WARNING"
        snap4 = s2.analyze_market(md4, now=t_mid2)
        check("T5a WARNING news -> full compute",
              "A1 bar-service:" not in " ".join(snap4.notes or []))
    finally:
        if old_state is not None:
            cached.news.news_state = old_state

    # ---- T6 ----------------------------------------------------------------
    print("T6 history shrink -> full compute")
    t_mid3 = t_mid2 + timedelta(seconds=30)
    md5 = md_at(trades, t_mid3, start)
    md5["candles"] = {k: v[:-40] for k, v in md5["candles"].items()}  # feed reset
    snap5 = s2.analyze_market(md5, now=t_mid3)
    check("T6a shrunk candle history -> full compute",
          "A1 bar-service:" not in " ".join(snap5.notes or []))

    # ---- T7 ----------------------------------------------------------------
    print("T7 POWER history ring cap")
    bars = list(range(400))
    capped, diag = s2._a1_cap_history_bars(bars, {})
    check("T7a 400 bars capped to 300 (last kept)",
          len(capped) == 300 and capped[-1] == 399 and diag.get("history_capped_from") == 400)
    bars100, diag100 = s2._a1_cap_history_bars(list(range(100)), {})
    check("T7b 100 bars untouched", len(bars100) == 100 and "history_capped_from" not in diag100)

    # ---- T8 ----------------------------------------------------------------
    print("T8 fail-open")
    t_mid4 = t_mid3 + timedelta(seconds=30)
    md6 = md_at(trades, t_mid4, start)
    s2._A1_BAR_SERVICE_CACHE["entry"]["stored_at"] = "not-a-datetime"  # poison
    try:
        snap6 = s2.analyze_market(md6, now=t_mid4)
        check("T8a poisoned cache -> full compute, no crash",
              "A1 bar-service:" not in " ".join(snap6.notes or []) and
              snap6.signal_direction is not None)
    except Exception as e:
        check("T8a poisoned cache -> full compute, no crash", False, repr(e))

    # ---- T9 ----------------------------------------------------------------
    print("T9 speed receipt (informational)")
    t_a = datetime(2026, 10, 9, 10, 15, 2, tzinfo=timezone.utc)
    md7 = md_at(trades, t_a, start)
    s2.analyze_market(md7, now=t_a)                      # ensure a fresh store
    md8 = md_at(trades, t_a + timedelta(seconds=45), start)
    t0 = _time.perf_counter()
    serves = 0
    for i in range(5):
        snap = s2.analyze_market(md8, now=t_a + timedelta(seconds=45 + i))
        if "A1 bar-service:" in " ".join(snap.notes or []):
            serves += 1
    t_serve = (_time.perf_counter() - t0) / 5
    t0 = _time.perf_counter()
    for i in range(3):
        s2.analyze_market(md7, now=t_a + timedelta(minutes=5 * (i + 1), seconds=1))
    t_full = (_time.perf_counter() - t0) / 3
    print(f"       serve avg {t_serve*1000:.1f} ms vs full compute avg {t_full*1000:.1f} ms "
          f"({serves}/5 serves hit)")
    check("T9a serves did happen", serves >= 1)

    print("=" * 64)
    if FAILURES:
        print(f"RIG RESULT: {len(FAILURES)} FAILED -> {', '.join(FAILURES)}")
        sys.exit(1)
    print("RIG RESULT: ALL PASS -- bar-service semantics proven on the patched module.")


if __name__ == "__main__":
    main()
