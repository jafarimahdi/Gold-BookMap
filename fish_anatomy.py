#!/usr/bin/env python3
"""fish_anatomy.py -- the FISH ANATOMY probe (lens rung 1.5).

The lens answered "do finer clocks SEE more real fish?" (yes: M2/M3, 3/3 days).
This probe opens the fish up and answers the four questions the reports cannot:

  WHEN   - at what hours (Budapest) do the moments happen? (killzone map)
  SIZE   - how does a fish grow: favourable travel at 3/6/9/12/15 min
           (the real target-size curve for A3, measured not guessed)
  HEAT   - how far does price go AGAINST the moment before +1.0 pt pays
           (the entry-pain number for A2: the burst entry must survive this)
  PANEL  - did the REAL robot's panel vote on the same fish?
           (nearest same-direction signal within +/-5 min, its confidence;
            the rung-2 prior for A6: if the panel never sees these fish,
            a TF switch changes everything, not just the exits)

Engine: identical to the lens (imports its tape reader, candle builder and the
robot's own trend brain). informs, never switches. Reads only; writes one file:
data/fish_anatomy_<date>.txt

Usage (git bash, project root, next to tf_lens.py):
    python fish_anatomy.py --date 2026-10-09
    python fish_anatomy.py --date 2026-10-08 --tf 3 --compare 2,5
"""
import argparse
import bisect
import os
import statistics
import sys
from datetime import datetime, time as dtime, timedelta

from tf_lens import (BUDA, build_candles, detect_moments,
                     match_against_baseline, read_day_trades, read_decisions)


def hour_label(h):
    if 8 <= h < 14:
        return "London morning"
    if 14 <= h < 18:
        return "London+NY overlap"
    return "NY afternoon/evening"


def travel_curve(prints, times, m, horizons):
    """Favourable travel (pts) at each horizon, prints strictly after close."""
    lo = bisect.bisect_right(times, m["t"])
    out = {}
    for h in horizons:
        hi = bisect.bisect_right(times, m["t"] + timedelta(minutes=h))
        seg = prints[lo:hi]
        if not seg:
            out[h] = None
            continue
        prices = [p for _, p in seg]
        out[h] = (max(prices) - m["entry"]) if m["dir"] == "UP" else \
                 (m["entry"] - min(prices))
    return out


def heat_and_payoff(prints, times, m, need, window):
    """Max ADVERSE travel before favourable reaches `need` (heat), and whether
    it ever did inside `window` minutes. Heat stops counting once paid."""
    lo = bisect.bisect_right(times, m["t"])
    hi = bisect.bisect_right(times, m["t"] + timedelta(minutes=window))
    worst, reached = 0.0, False
    for _, p in prints[lo:hi]:
        fav = (p - m["entry"]) if m["dir"] == "UP" else (m["entry"] - p)
        if not reached:
            worst = max(worst, -min(fav, 0.0))
            if fav >= need:
                reached = True
    return worst, reached


def crossmatch(m, dec_rows, dec_times, match_min):
    """Max confidence of same-direction real signals within +/-match_min."""
    want = "BUY" if m["dir"] == "UP" else "SELL"
    lo = bisect.bisect_left(dec_times, m["t"] - timedelta(minutes=match_min))
    hi = bisect.bisect_right(dec_times, m["t"] + timedelta(minutes=match_min))
    best = None
    for i in range(lo, hi):
        ts, d, price, conf = dec_rows[i]
        if d == want and (best is None or conf > best):
            best = conf
    return best


def fmt(v, spec):
    return format(v, spec) if v is not None else "-"


def run(args, lines):
    say = lines.append
    horizons = [int(x) for x in args.horizons.split(",") if x.strip()]
    tfs = [args.tf] + [int(x) for x in args.compare.split(",") if x.strip()]
    h0, m0 = (int(x) for x in args.session.split("-")[0].split(":"))
    h1, m1 = (int(x) for x in args.session.split("-")[1].split(":"))
    session = (dtime(h0, m0), dtime(h1, m1))

    say("=" * 100)
    say(f" FISH ANATOMY - {args.date}   primary clock M{args.tf} "
        f"(compare: {', '.join('M' + str(t) for t in tfs[1:]) or 'none'})")
    say(f" fish = favourable >= {args.fish_need:.2f} pts | heat window {args.heat_window} min | "
        f"panel crossmatch +/-{args.match_min} min | engine: the robot's own trend brain (as the lens)")
    say(" informs, never switches")
    say("=" * 100)

    trades, err = read_day_trades(args.date)
    if err:
        say(f" [no tape] {err}")
        return 0
    times = [dt for dt, _ in trades]
    prices = [p for _, p in trades]
    say(f" tape: {len(trades)} prints")

    # ---- weather: the day's own shape -------------------------------------
    if prices:
        rng = max(prices) - min(prices)
        net = prices[-1] - prices[0]
        trendiness = abs(net) / rng if rng > 0 else 0.0
        say(f" WEATHER: range {rng:.1f} pts | net {net:+.1f} | trendiness "
            f"{trendiness:.2f} (0 = flat lake, 1 = one-way river)")

    dec_rows, dec_err = read_decisions(args.date)
    dec_times = [r[0] for r in dec_rows] if dec_rows else []

    # ---- moments per clock -------------------------------------------------
    results = {}
    for tf in tfs:
        keys, candles = build_candles(trades, tf)
        results[tf] = detect_moments(keys, candles, tf, session=session)
    base = results[tfs[0]]
    say(f" moments: " + " | ".join(f"M{tf}: {len(results[tf])}" for tf in tfs))
    say("")

    # ---- per-fish log (primary clock) --------------------------------------
    say(f" THE FISH LOG (M{args.tf} moments; * = M5 never saw it):")
    matched_base = results.get(5) or results[tfs[-1]]
    _, unique_primary, _ = match_against_baseline(base, matched_base, 30.0)
    unique_ids = {id(m) for m in unique_primary}
    heats, pays, confs = [], 0, []
    hour_hist = {}
    for m in base:
        lb = m["t"].astimezone(BUDA)
        hour_hist[lb.hour] = hour_hist.get(lb.hour, 0) + 1
        curve = travel_curve(trades, times, m, horizons)
        heat, paid = heat_and_payoff(trades, times, m, args.fish_need,
                                     args.heat_window)
        cm = crossmatch(m, dec_rows, dec_times, args.match_min) if dec_rows else None
        if paid:
            heats.append(heat)
            pays += 1
        if cm is not None:
            confs.append(cm)
        star = "*" if id(m) in unique_ids else " "
        say(f"  {lb.strftime('%H:%M')} {m['dir']:>4}{star} adx {m['adx']:4.1f} | "
            + " ".join(f"{h}m {fmt(curve[h], '+.2f')}" for h in horizons)
            + f" | heat {heat:.2f}{' (paid)' if paid else ' (no payoff)'}"
            + f" | panel {'saw conf ' + format(cm, '.0f') if cm is not None else 'did NOT see'}")

    # ---- WHEN ---------------------------------------------------------------
    say("")
    say(" WHEN (Budapest hours of the primary clock's moments):")
    if not hour_hist:
        say("   (no moments this day)")
    for h in sorted(hour_hist):
        say(f"   {h:02d}:00-{h:02d}:59  {hour_hist[h]}  ({hour_label(h)})")
    for lab in ("London morning", "London+NY overlap", "NY afternoon/evening"):
        n = sum(c for h, c in hour_hist.items() if hour_label(h) == lab)
        if n:
            say(f"   -> {lab}: {n}")

    # ---- SIZE ----------------------------------------------------------------
    say("")
    say(f" SIZE (median favourable travel by horizon, all moments):")
    hdr = "        " + "".join(f"{h:>8}m" for h in horizons)
    say(hdr)
    for tf in tfs:
        meds = []
        for h in horizons:
            vals = [travel_curve(trades, times, m, [h])[h] for m in results[tf]]
            vals = [v for v in vals if v is not None]
            meds.append(statistics.median(vals) if vals else None)
        say(f"   M{tf:>3} n={len(results[tf]):<3}" + "".join(f"{fmt(v, '>8.2f')}" for v in meds))

    # ---- HEAT ------------------------------------------------------------------
    say("")
    if base:
        say(f" HEAT (M{args.tf}): paid +{args.fish_need:.1f} pts within {args.heat_window} min: "
            f"{pays}/{len(base)} | median heat before payoff: "
            f"{fmt(statistics.median(heats) if heats else None, '.2f')} pts")
    else:
        say(" HEAT: (no moments)")

    # ---- PANEL ------------------------------------------------------------------
    say("")
    if base:
        if dec_rows:
            seen = len(confs)
            say(f" PANEL CROSSMATCH (M{args.tf} moments vs real signals +/-{args.match_min} min, "
                f"same direction): {seen}/{len(base)} seen"
                + (f" | median conf {statistics.median(confs):.0f}"
                   f" | confident(>=50): {sum(1 for c in confs if c >= 50)}" if confs else ""))
        else:
            say(f" PANEL CROSSMATCH: {dec_err}")
    say("")
    say(" how to read it:")
    say("   SIZE row = the honest target ladder for burst exits (A3): what a fish")
    say("   actually gives at each minute, measured, not guessed.")
    say("   HEAT = how deep price dips against you before +1 pt pays (A2 entry pain).")
    say("   PANEL 'did NOT see' = the real robot had no same-direction signal near")
    say("   that minute -- the fish swam past the whole panel (the A6/rung-2 question).")
    say(" informs, never switches.")

    out_path = os.path.join("data", f"fish_anatomy_{args.date}.txt")
    try:
        os.makedirs("data", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        say(f" [saved] {out_path}")
    except OSError as e:
        say(f" [could not save {out_path}: {e}]")
    return 0


def main():
    ap = argparse.ArgumentParser(description="fish anatomy probe (lens rung 1.5)")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--tf", type=int, default=3)
    ap.add_argument("--compare", default="2,5")
    ap.add_argument("--fish-need", type=float, default=1.0)
    ap.add_argument("--heat-window", type=int, default=9)
    ap.add_argument("--match-min", type=float, default=5.0)
    ap.add_argument("--horizons", default="3,6,9,12,15")
    ap.add_argument("--session", default="08:00-23:00")
    args = ap.parse_args()
    lines = []
    run(args, lines)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
