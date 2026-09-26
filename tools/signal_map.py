#!/usr/bin/env python3
"""signal_map.py - PHASE 4: the SIGNAL map, and the first real test of "walls are targets".

WHY THIS EXISTS
  The v8 architecture says: SIGNAL names the targets, POWER says whether we can reach
  them, TP goes just before the wall and SL just behind the one behind us.

  That rests on a claim nobody has ever checked on this tape:

        does price actually GO to a wall, and does it STOP there?

  Until tonight it could not be checked, because whale_walls had never produced a single
  line. After the 2026-09-25 threshold fix it does, and the note carries the exact price:

        L3 whale SUPPORT 3 walls 240 lots closest 2043.6 dist 0.02% w 1.2 -> BUY
                                          ^^^^^^^^^^^^^^ the target

  So this tool reads the walls the robot already saw, then asks the tape two questions:

    MAGNET  - did price REACH the wall within the horizon?
    BARRIER - once it arrived, did it STOP/REVERSE there, or slice straight through?

  A wall that price never reaches is a useless target.
  A wall that price reaches and blows through is a useless TP.
  Both must be true for the architecture to work, and this measures both.

USAGE
    python tools/signal_map.py --date 2026-09-28
    python tools/signal_map.py --days 5
    python tools/signal_map.py --date 2026-09-28 --horizon 6     # in M5 bars

Report only. Reads the diary and ticks. Changes nothing, sends nothing.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import statistics
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_audit():
    spec = importlib.util.spec_from_file_location("ad_map", ROOT / "audit_day.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


A = _load_audit()

# L3 whale SUPPORT 3 walls 240 lots closest 2043.6 dist 0.02% w 1.2 -> BUY
RX_WALL = re.compile(
    r"L3 whale (SUPPORT|RESISTANCE) (\d+) walls ([\d.]+) lots closest ([\d.]+) "
    r"dist ([\d.]+)%")
# ICEBERG_SUPPORT 4 @ 2043.6 refills 5 ...
RX_ICE = re.compile(r"ICEBERG_(SUPPORT|RESISTANCE) \d+ @ ([\d.]+) refills (\d+)")


def parse_dt(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def collect_walls(day):
    """-> (list of wall observations, n_snapshots)"""
    diary, _p = A.load_diary(day)
    if not diary:
        return None, 0
    walls = []
    for rec in diary:
        dt = parse_dt(rec.get("timestamp"))
        if dt is None:
            continue
        try:
            price = float(rec.get("price") or 0.0)
        except Exception:
            continue
        for n in (rec.get("notes") or []):
            if not isinstance(n, str):
                continue
            m = RX_WALL.search(n)
            if m:
                side, cnt, lots, wall_px, dist = m.groups()
                walls.append({"dt": dt, "price": price, "kind": "whale",
                              "side": side, "n": int(cnt), "lots": float(lots),
                              "wall": float(wall_px), "dist_pct": float(dist)})
                continue
            m = RX_ICE.search(n)
            if m:
                side, wall_px, refills = m.groups()
                walls.append({"dt": dt, "price": price, "kind": "iceberg",
                              "side": side, "n": int(refills), "lots": 0.0,
                              "wall": float(wall_px), "dist_pct": 0.0})
    return walls, len(diary)


def test_wall(candles, w, nbars, atr):
    """Did price REACH the wall, and did it STOP there? -> dict or None."""
    i = None
    key = w["dt"].replace(second=0, microsecond=0)
    for k, c in enumerate(candles):
        if c["dt"] >= key:
            i = k + 1
            break
    if i is None:
        return None
    seg = candles[i:i + nbars]
    if len(seg) < max(1, nbars // 2):
        return None
    wall = w["wall"]
    above = wall > w["price"]
    hi = max(c["high"] for c in seg)
    lo = min(c["low"] for c in seg)
    reached = (hi >= wall) if above else (lo <= wall)

    result = {"reached": reached, "dist_atr": abs(wall - w["price"]) / atr,
              "through_atr": None, "bars_to_reach": None, "rejected": None,
              "run_atr": None}
    if reached:
        touch = None
        for j, c in enumerate(seg):
            if (c["high"] >= wall) if above else (c["low"] <= wall):
                touch = j
                result["bars_to_reach"] = j + 1
                break
        # A barrier test must look at what happens AT the wall, not at the rest of the
        # horizon. Measuring max excursion over all 6 bars conflates "did the wall hold"
        # with "did the market trend afterwards" - in any trending tape that always reads
        # as "sliced through". So: measure the 2 bars from the touch, and separately ask
        # whether price came back to our side of the wall before the horizon ended.
        near = seg[touch:touch + 2]
        if above:
            push = max(c["high"] for c in near) - wall
        else:
            push = wall - min(c["low"] for c in near)
        result["through_atr"] = push / atr

        after = seg[touch + 1:]
        if after:
            if above:
                result["rejected"] = min(c["low"] for c in after) < wall
            else:
                result["rejected"] = max(c["high"] for c in after) > wall
        # how far it eventually ran, kept separately - this is the trend, not the wall
        result["run_atr"] = (((hi - wall) if above else (wall - lo)) / atr)
    return result


def simulate_rule(candles, w, nbars, atr, buffer_atr=0.15, sl_atr=2.0):
    """Trade the wall two opposite ways and see which one the tape rewards.

      TOWARD  - price is drawn to liquidity: enter now, TP just BEFORE the wall.
                This is what "targets and force" assumes.
      BOUNCE  - the wall holds: enter AWAY from it, expecting a rejection.
                This is what whale_walls currently votes (BUY on support).

    Both use the robot's real stop, SL = 2.0 x ATR, so the answer is in the same
    money the account is in. -> dict or None
    """
    i = None
    key = w["dt"].replace(second=0, microsecond=0)
    for k, c in enumerate(candles):
        if c["dt"] >= key:
            i = k + 1
            break
    if i is None:
        return None
    seg = candles[i:i + nbars]
    if len(seg) < max(1, nbars // 2):
        return None
    entry, wall = w["price"], w["wall"]
    above = wall > entry
    buf = buffer_atr * atr
    out = {}

    # TOWARD the wall: buy if the wall is above, sell if below
    tp = (wall - buf) if above else (wall + buf)
    sl = (entry - sl_atr * atr) if above else (entry + sl_atr * atr)
    out["toward"] = _first_touch(seg, above, tp, sl, entry, atr)

    # BOUNCE off the wall: the opposite direction
    tp2 = (entry - sl_atr * atr * 0.75) if above else (entry + sl_atr * atr * 0.75)
    sl2 = (wall + buf) if above else (wall - buf)
    out["bounce"] = _first_touch(seg, not above, tp2, sl2, entry, atr)
    return out


def _first_touch(seg, long_side, tp, sl, entry, atr):
    """Which came first: TP or SL? Returns the RESULT IN ATR, not a win/loss flag.

    Win rate lies. A target 0.5 ATR away with a stop 2.0 ATR away wins most of the
    time and still loses money, because the rare loss is four times the common win.
    So this returns what the account would feel: +reward or -risk, measured in ATR.
    """
    for c in seg:
        if long_side:
            if c["low"] <= sl:
                return -(entry - sl) / atr
            if c["high"] >= tp:
                return (tp - entry) / atr
        else:
            if c["high"] >= sl:
                return -(sl - entry) / atr
            if c["low"] <= tp:
                return (entry - tp) / atr
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description="The SIGNAL map: are walls real targets?")
    ap.add_argument("--date", help="YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--horizon", type=int, default=6, help="M5 bars to allow (default 6 = 30 min)")
    args = ap.parse_args(argv)

    if args.date:
        end = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        end = (datetime.now(A.BUDA) - timedelta(days=1)).date()
    days = sorted(end - timedelta(days=i) for i in range(max(1, args.days)))
    atr = float(getattr(A, "ATR", 3.18)) or 3.18

    rows, skipped, snaps = [], [], 0
    for d in days:
        walls, n = collect_walls(d)
        if not walls:
            skipped.append((d, "no wall observations in the diary"))
            continue
        ticks, _tp, _sc = A.load_ticks(d)
        if not ticks:
            skipped.append((d, "no ticks to test the walls against"))
            continue
        candles = A.build_candles(ticks)
        if not candles:
            skipped.append((d, "tape too short for M5 candles"))
            continue
        snaps += n
        got = 0
        for w in walls:
            r = test_wall(candles, w, args.horizon, atr)
            if r:
                w.update(r)
                w["sim"] = simulate_rule(candles, w, args.horizon, atr)
                rows.append(w)
                got += 1
        print(f"[read] {d:%Y-%m-%d}: {len(walls)} wall observation(s), {got} testable")

    if not rows:
        print("\nNo testable wall observations.")
        for d, why in skipped:
            print(f"  {d:%Y-%m-%d}  {why}")
        print("\nThis is the honest answer, not an error. Before 2026-09-25 the wall")
        print("thresholds were set so high that whale_walls never produced a line at all.")
        return 2

    print("\n" + "=" * 96)
    print(f"THE SIGNAL MAP   {len(rows)} wall observations, horizon {args.horizon} M5 bars "
          f"({args.horizon*5} min), ATR {atr}")
    print("=" * 96)

    kinds = Counter(r["kind"] for r in rows)
    sides = Counter(r["side"] for r in rows)
    print(f"  sources: " + ", ".join(f"{k}={v}" for k, v in kinds.items())
          + "   |   sides: " + ", ".join(f"{k}={v}" for k, v in sides.items()))

    dists = [r["dist_atr"] for r in rows]
    print(f"  distance to the wall, in ATR: median {statistics.median(dists):.2f}, "
          f"min {min(dists):.2f}, max {max(dists):.2f}")

    reached = [r for r in rows if r["reached"]]
    print("\n--- QUESTION 1: MAGNET - does price actually go to the wall?")
    print(f"    reached within {args.horizon*5} min : {len(reached)} of {len(rows)} "
          f"({100.0*len(reached)/len(rows):.1f}%)")
    if reached:
        bars = [r["bars_to_reach"] for r in reached if r["bars_to_reach"]]
        if bars:
            print(f"    time to reach it           : median {statistics.median(bars):.0f} "
                  f"M5 bars ({statistics.median(bars)*5:.0f} min)")

    print("\n--- QUESTION 2: BARRIER - once there, does it STOP?")
    if reached:
        over = [r["through_atr"] for r in reached if r["through_atr"] is not None]
        rej = [r["rejected"] for r in reached if r["rejected"] is not None]
        run = [r["run_atr"] for r in reached if r["run_atr"] is not None]
        if over:
            med = statistics.median(over)
            held = sum(1 for o in over if o <= 0.25)
            print(f"    push past the wall, 2 bars : median {med:.2f} ATR")
            print(f"    stalled within 0.25 ATR    : {held} of {len(over)} "
                  f"({100.0*held/len(over):.0f}%)  <- good TP candidates")
        if rej:
            back = sum(1 for r_ in rej if r_)
            print(f"    came BACK past the wall    : {back} of {len(rej)} "
                  f"({100.0*back/len(rej):.0f}%)  <- the wall pushed price away")
        if run:
            print(f"    total run afterwards       : median {statistics.median(run):.2f} ATR "
                  f"(this is the TREND, not the wall)")
    else:
        print("    nothing reached a wall, so there is nothing to say about stopping")

    # --- QUESTION 3: trade it both ways and let the tape choose -----------------
    sims = [r["sim"] for r in rows if r.get("sim")]
    if sims:
        print("\n--- QUESTION 3: TRADE IT - two opposite readings, same walls, real 2.0 ATR stop")
        print(f"    {'reading':<42}{'win':>5}{'loss':>6}{'open':>6}{'win%':>7}"
              f"{'total ATR':>11}{'per trade':>11}")
        for name, label in (("toward", "GO TO the wall (targets-and-force)"),
                            ("bounce", "BOUNCE off it (what whale_walls votes)")):
            res = [s[name] for s in sims if s.get(name) is not None]
            opens = sum(1 for s in sims if s.get(name) is None)
            w_ = sum(1 for x in res if x > 0)
            l_ = sum(1 for x in res if x < 0)
            tot = w_ + l_
            wr = (100.0 * w_ / tot) if tot else 0.0
            net = sum(res)
            per = (net / tot) if tot else 0.0
            print(f"    {label:<42}{w_:>5}{l_:>6}{opens:>6}{wr:>6.0f}%"
                  f"{net:>+11.2f}{per:>+11.2f}")
        print("    ATR is the unit, so this is money, not opinion. A high win% with a")
        print("    negative total means the losses are bigger than the wins - the exact")
        print("    trap that makes an 83%-right judge lose points.")

    print("\n" + "=" * 96)
    n = len(rows)
    if n < 30:
        print(f"NO VERDICT - only {n} observations. This needs a few hundred before it means")
        print("anything. The numbers above are a smoke test that the pipeline works, not an")
        print("answer about whether walls are tradeable targets.")
    else:
        hit = 100.0 * len(reached) / n
        over = [r["through_atr"] for r in reached if r["through_atr"] is not None]
        rej = [r["rejected"] for r in reached if r["rejected"] is not None]
        stalled = sum(1 for o in over if o <= 0.25) if over else 0
        pushed = sum(1 for r_ in rej if r_) if rej else 0
        base = max(len(over), len(rej), 1)
        held = 100.0 * max(stalled, pushed) / base
        if hit >= 60 and held >= 50:
            print("WALLS LOOK LIKE REAL TARGETS - price goes to them and tends to stop there.")
            print("  This supports TP just in front of the wall. Proceed to Phase 4b in shadow.")
        elif hit >= 60:
            print("WALLS ATTRACT BUT DO NOT HOLD - price reaches them and carries on through.")
            print("  Good for choosing DIRECTION, bad as a take-profit. Do not front-run them.")
        elif held >= 50:
            print("WALLS HOLD BUT ARE RARELY REACHED - useful as a STOP-LOSS shelter,")
            print("  not as a profit target within this horizon.")
        else:
            print("WALLS ARE NOT BEHAVING AS TARGETS on this tape, at this horizon.")
            print("  Before abandoning the idea, retry with a longer horizon and only the")
            print("  biggest walls - a 10-lot wall is not the same animal as a 40-lot wall.")
    if skipped:
        print("\n  days skipped, named on purpose:")
        for d, why in skipped:
            print(f"    {d:%Y-%m-%d}  {why}")
    print("=" * 96)
    return 0


if __name__ == "__main__":
    sys.exit(main())
