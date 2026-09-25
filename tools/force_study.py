#!/usr/bin/env python3
"""force_study.py - STEP 1 of architecture v8: does FORCE predict DISTANCE?

The whole "targets and force" design rests on one assumption:

        more force now  ->  more distance travelled next.

This tool answers that from tape you have ALREADY recorded. It changes nothing,
sends nothing, and never touches the live loop. Report only.

WHAT IT DOES
  For every diary snapshot of a day it reads two things:
    FORCE     - what the POWER judges said at that moment (weighted, signed),
                and the robot's own 'strength' number, for comparison.
    DISTANCE  - how far price ACTUALLY travelled afterwards, from ticks.csv,
                measured in ATR so every day is comparable:
                  MFE = furthest it went the RIGHT way   (the reachable target)
                  MAE = furthest it went the WRONG way   (where a stop would sit)
                  NET = where it actually was at the end of the horizon

  Then it buckets the force readings and shows the median distance per bucket,
  plus a rank correlation. If force does not predict distance, you will see it
  here in one screen, before anybody builds anything.

USAGE
    python tools/force_study.py                 # yesterday
    python tools/force_study.py --date 2026-09-24
    python tools/force_study.py --days 5        # the last 5 days pooled
    python tools/force_study.py --horizons 3,6,12     # in M5 bars (15/30/60 min)

OUTPUT
    console table + data/force_study_<tag>.html
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_audit():
    spec = importlib.util.spec_from_file_location("ad_force", ROOT / "audit_day.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


A = _load_audit()

# --- the two teams, exactly as agreed 2026-09-25 -----------------------------
POWER = {
    "footprint_delta", "sweep", "l3_aggr_limit", "cvd_divergence", "cvd_momentum",
    "footprint_levels", "volume_roc", "vwap_bands", "htf_poc", "vwap_trend",
    "supply_demand", "poc_day", "value_area", "mtf", "news_sentiment", "macro_risk",
}
SIGNAL = {
    "l3_net_flow", "whale_walls", "iceberg", "l3_imbalance", "l3_large_ofi",
    "microprice", "spoof_invert", "queue_pos", "absorption",
}
RETIRED = {
    "macro_yield", "macro_dxy", "macro_vix", "vwap_zscore", "spoof_invert_loose",
    "iceberg_legacy", "l3_ofi_streak", "delta_pressure",
}


MIN_PER_BAND = 25      # below this a band is decoration, not evidence


def spearman(xs, ys):
    """Rank correlation, no scipy. -1..+1, or None when there is nothing to say."""
    n = len(xs)
    if n < 10:
        return None

    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return (num / (dx * dy)) if dx > 0 and dy > 0 else None


def power_force(rec):
    """Signed, weighted POWER reading for one diary record, normalised to -1..+1.

    Retired judges are excluded - we study the panel as it is TODAY, not as it was.
    """
    votes = rec.get("judge_votes") or []
    num = den = 0.0
    used = 0
    for v in votes:
        try:
            name = str(v.get("judge", ""))
            if name not in POWER or name in RETIRED:
                continue
            d = float(v.get("dir", 0) or 0)
            w = float(v.get("weight", 0.5) or 0.5)
        except Exception:
            continue
        num += d * w
        den += abs(w)
        used += 1
    if den <= 0 or used < 3:
        return None, used
    return num / den, used


def travel(candles, entry_dt, entry_price, sign, nbars, atr):
    """How far price went, in ATR. -> (mfe, mae, net) or None."""
    i = None
    key = entry_dt.replace(second=0, microsecond=0)
    for k, c in enumerate(candles):
        if c["dt"] >= key:
            i = k + 1
            break
    if i is None:
        return None
    seg = candles[i:i + nbars]
    if len(seg) < max(1, nbars // 2):          # refuse a half-measured horizon
        return None
    hi = max(c["high"] for c in seg)
    lo = min(c["low"] for c in seg)
    end = seg[-1]["close"]
    if sign > 0:
        mfe, mae = (hi - entry_price), (entry_price - lo)
    else:
        mfe, mae = (entry_price - lo), (hi - entry_price)
    net = (end - entry_price) * (1 if sign > 0 else -1)
    return mfe / atr, mae / atr, net / atr


def collect(day, horizons):
    diary, dpath = A.load_diary(day)
    if not diary:
        return None, f"no diary for {day:%Y-%m-%d}"
    ticks, tpath, _sc = A.load_ticks(day)
    if not ticks:
        return None, f"no ticks for {day:%Y-%m-%d}"
    candles = A.build_candles(ticks)
    if not candles:
        return None, f"tape too short to build M5 candles for {day:%Y-%m-%d}"
    atr = float(getattr(A, "ATR", 3.18)) or 3.18

    rows = []
    for rec in diary:
        dt = A.parse_dt(rec.get("timestamp")) if hasattr(A, "parse_dt") else None
        if dt is None:
            try:
                dt = datetime.fromisoformat(str(rec.get("timestamp")).replace("Z", "+00:00"))
            except Exception:
                continue
        try:
            price = float(rec.get("price") or 0.0)
        except Exception:
            continue
        if price < 1000:
            continue
        f, used = power_force(rec)
        if f is None:
            continue
        sign = 1 if f > 0 else -1
        strength = rec.get("strength", rec.get("signal_strength"))
        try:
            strength = abs(float(strength))
        except Exception:
            strength = None
        row = {"dt": dt, "price": price, "force": f, "absforce": abs(f),
               "judges": used, "strength": strength, "out": {}}
        for nb in horizons:
            t = travel(candles, dt, price, sign, nb, atr)
            if t:
                row["out"][nb] = {"mfe": t[0], "mae": t[1], "net": t[2]}
        if row["out"]:
            rows.append(row)
    if not rows:
        return None, f"{day:%Y-%m-%d}: diary carried no POWER votes to study"
    return rows, None


def independent(rows, nb):
    """Overlapping windows are not independent samples.

    Snapshots arrive about once a minute, so a 15-minute horizon means 15 consecutive
    readings all describe the SAME stretch of tape. Counting them as 590 observations
    flatters every result. This keeps one reading per non-overlapping window, which is
    the honest sample size - much smaller, and much harder to fool.
    """
    keep, last = [], None
    span = timedelta(minutes=nb * 5)
    for r in sorted(rows, key=lambda x: x["dt"]):
        if nb not in r["out"]:
            continue
        if last is None or (r["dt"] - last) >= span:
            keep.append(r)
            last = r["dt"]
    return keep


def buckets(rows, nb, k=5):
    """Force bands with the full picture: how far it went the right way, how far the
    wrong way, and how often it picked the correct side."""
    have = [(r["absforce"], r["out"][nb]) for r in rows if nb in r["out"]]
    have.sort(key=lambda t: t[0])
    if len(have) < k * 4:
        return []
    out, size = [], len(have) // k
    for b in range(k):
        lo = b * size
        hi = (b + 1) * size if b < k - 1 else len(have)
        chunk = have[lo:hi]
        mfe = [c[1]["mfe"] for c in chunk]
        mae = [c[1]["mae"] for c in chunk]
        net = [c[1]["net"] for c in chunk]
        med_mfe, med_mae = statistics.median(mfe), statistics.median(mae)
        out.append({
            "n": len(chunk),
            "f_lo": chunk[0][0], "f_hi": chunk[-1][0],
            "median": med_mfe, "mean": statistics.mean(mfe),
            "mae": med_mae,
            "ratio": (med_mfe / med_mae) if med_mae > 1e-9 else float("inf"),
            "hit": 100.0 * sum(1 for v in net if v > 0) / len(net),
            "net": statistics.median(net),
        })
    return out


def shape_of(bands):
    """Spearman cannot see a hump. This can. -> (label, peak index 1-based)"""
    if len(bands) < 3:
        return "unknown", 0
    meds = [b["median"] for b in bands]
    peak = meds.index(max(meds))
    if peak == 0:
        return "falling - quiet markets travel furthest", 1
    if peak == len(meds) - 1:
        return "rising - louder travels further", len(meds)
    return "HUMP - middle travels furthest, loudest does not", peak + 1


def verdict(rho):
    if rho is None:
        return "NOT ENOUGH DATA", "Collect more days before deciding anything."
    if rho >= 0.30:
        return "FORCE PREDICTS DISTANCE", ("Strong enough to build on. Use it to convert a force "
                                           "reading into an expected reach in ATR.")
    if rho >= 0.15:
        return "WEAK BUT REAL", ("Force helps a little. Safer plan: let force pick the SIDE, and "
                                 "let the walls decide the TARGET. Do not size a TP from force alone.")
    if rho > -0.10:
        return "NO USEFUL RELATIONSHIP", ("On this tape, a louder panel does NOT travel further. "
                                          "Do not build the reach formula. Targets must come from "
                                          "the walls, and force only picks the side.")
    return "INVERTED - INVESTIGATE", ("More force went with LESS distance. That usually means the "
                                      "panel is loudest at exhaustion, right before a turn. "
                                      "Worth a very careful look; it may be tradeable backwards.")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Does POWER force predict how far price travels?")
    ap.add_argument("--date", help="YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--days", type=int, default=1, help="pool the last N days")
    ap.add_argument("--horizons", default="3,6,12", help="in M5 bars (default 3,6,12 = 15/30/60 min)")
    ap.add_argument("--independent", action="store_true",
                    help="use only NON-OVERLAPPING windows - the honest sample size. "
                         "Far fewer readings, but they cannot flatter each other.")
    ap.add_argument("--no-html", action="store_true")
    args = ap.parse_args(argv)

    horizons = [int(x) for x in str(args.horizons).split(",") if x.strip()]
    if args.date:
        end = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        end = (datetime.now(A.BUDA) - timedelta(days=1)).date()
    days = sorted(end - timedelta(days=i) for i in range(max(1, args.days)))

    rows, skipped = [], []
    for d in days:
        got, why = collect(d, horizons)
        if got:
            rows.extend(got)
            print(f"[read] {d:%Y-%m-%d}: {len(got)} snapshots with POWER votes")
        else:
            skipped.append((d, why))
            print(f"[skip] {why}")

    if not rows:
        print("\nNothing to study. This is not a failure - it means the diary for these days "
              "carries no judge_votes yet (older build), or there is no tape.")
        return 2

    # Honesty guard: a correlation needs force readings that actually VARY. A fixture
    # (or a day where the panel never changed its mind) produces two or three distinct
    # values and a meaningless number. Say so instead of printing a verdict.
    distinct = len({round(r["absforce"], 4) for r in rows})
    degenerate = distinct < 10
    if degenerate:
        print(f"\n[!] WARNING: only {distinct} distinct force values in {len(rows)} snapshots.")
        print("    That is not enough variety to measure anything. This happens on synthetic")
        print("    demo data, or on a day when the panel barely changed. The numbers below")
        print("    are printed for inspection only - THEY ARE NOT AN ANSWER.")

    print("\n" + "=" * 96)
    print(f"FORCE  vs  DISTANCE     {len(rows)} snapshots, ATR = {getattr(A,'ATR',3.18)}")
    print("  force = POWER judges only (16 of them), weighted, retired judges excluded")
    print("  MFE   = how far price went the RIGHT way afterwards, in ATR")
    print("=" * 96)

    summary, shapes, dirskill, solid = {}, {}, {}, {}
    for nb in horizons:
        use = independent(rows, nb) if args.independent else rows
        xs = [r["absforce"] for r in use if nb in r["out"]]
        ys = [r["out"][nb]["mfe"] for r in use if nb in r["out"]]
        rho = spearman(xs, ys)
        summary[nb] = rho
        # DIRECTION: does the SIGN of force predict the SIGN of the move? This is the
        # question the architecture actually depends on, and the MFE test never asked it.
        nets = [r["out"][nb]["net"] for r in use if nb in r["out"]]
        hit = 100.0 * sum(1 for v in nets if v > 0) / len(nets) if nets else 0.0
        dirskill[nb] = hit
        mins = nb * 5
        overlap = "" if args.independent else (
            f"  (overlapping; ~{max(1, len(xs)//max(nb,1))} independent windows)")
        print(f"\n--- horizon {nb} M5 bars ({mins} min) --- {len(xs)} usable readings{overlap}")
        print(f"    DIRECTION : force picked the right side {hit:.1f}% of the time "
              f"({'edge' if hit >= 55 else 'coin flip' if hit >= 45 else 'WRONG side more often'})")
        if rho is None:
            print("    DISTANCE  : too few readings for a correlation")
        else:
            print(f"    DISTANCE  : rank correlation force -> MFE {rho:+.3f} "
                  f"(straight-line only - see shape below)")
        bk = buckets(use, nb)
        if bk:
            label, peak = shape_of(bk)
            shapes[nb] = (label, peak)
            print(f"    SHAPE     : {label}")
            print(f"    {'force band':>18} | {'n':>5} | {'med MFE':>8} | {'med MAE':>8} "
                  f"| {'MFE/MAE':>7} | {'right side':>10}")
            for i, b in enumerate(bk, 1):
                star = "  <-- best" if i == peak else ""
                print(f"    {b['f_lo']:.2f} .. {b['f_hi']:.2f}".rjust(24)
                      + f" | {b['n']:>5} | {b['median']:>7.2f}A | {b['mae']:>7.2f}A "
                        f"| {b['ratio']:>7.2f} | {b['hit']:>9.1f}%{star}")
            lo, hi = bk[0]["median"], bk[-1]["median"]
            if lo > 0:
                print(f"    loudest band travels {hi/lo:.2f}x as far as the quietest band")
            eff = min(b["n"] for b in bk) if args.independent else min(
                b["n"] for b in bk) // max(nb, 1)
            solid[nb] = eff >= MIN_PER_BAND
            if eff < MIN_PER_BAND:
                print(f"    [!] roughly {eff} INDEPENDENT readings per band - too few to trust. "
                      f"Collect more days.")

    best = max((abs(v) for v in summary.values() if v is not None), default=None)
    rho_main = summary.get(horizons[0])
    hit_main = dirskill.get(horizons[0], 0.0)
    # A shape is only a finding if the bands behind it are big enough to mean something.
    humps = sum(1 for nb, (lbl, _) in shapes.items()
                if lbl.startswith("HUMP") and solid.get(nb))
    enough = any(solid.values())
    if degenerate:
        head = "NO VERDICT - THE DATA CANNOT ANSWER THIS"
        advice = (f"Only {distinct} distinct force values were recorded. Run this on real "
                  f"trading days with a panel that actually changes its mind.")
    elif not enough:
        head = "NO VERDICT - NOT ENOUGH INDEPENDENT DATA"
        per_day = {nb: len(independent(rows, nb)) for nb in horizons}
        need = {nb: max(0, math.ceil((MIN_PER_BAND * 5) / max(n, 1)))
                for nb, n in per_day.items() if n}
        advice = ("Every shape and correlation above rests on too few independent windows. "
                  "Overlapping snapshots flatter the numbers; with the overlap removed there "
                  "is not enough tape here to answer anything. This is not a failure - it is "
                  "the measurement refusing to guess.\n  Independent windows per day on this "
                  "sample: " + ", ".join(f"{nb*5}min={n}" for nb, n in per_day.items())
                  + ".\n  To reach " + str(MIN_PER_BAND) + " per band you need roughly: "
                  + ", ".join(f"{nb*5}min={d} trading days" for nb, d in need.items()) + ".")
    elif humps >= 2:
        peaks = {nb: p for nb, (lbl, p) in shapes.items() if lbl.startswith("HUMP")}
        head = "HUMP - MODERATE FORCE TRAVELS FURTHEST"
        advice = ("The loudest panel does NOT travel furthest; a middle band does, at "
                  f"{humps} of {len(shapes)} horizons (peak band {peaks}). A straight-line "
                  "correlation cannot see this, which is why the rho is near zero. "
                  "Read this as: maximum agreement tends to arrive LATE, near exhaustion. "
                  "Do not build 'more force = further'. Consider a force WINDOW instead of a "
                  "minimum, and confirm it on more days before trusting it.")
    else:
        head, advice = verdict(rho_main)
    if not degenerate:
        advice += (f"\n  DIRECTION: force picked the correct side {hit_main:.1f}% of the time at "
                   f"the shortest horizon. Below ~55% it is not a reliable side-picker either, "
                   f"and the targets must come from the walls.")
    print("\n" + "=" * 96)
    print(f"VERDICT: {head}")
    print(f"  {advice}")
    if skipped:
        print("\n  days named as skipped (never silently dropped):")
        for d, why in skipped:
            print(f"    {d:%Y-%m-%d}  {why}")
    print("=" * 96)

    if not args.no_html:
        try:
            tag = f"{days[0]:%Y-%m-%d}_to_{days[-1]:%Y-%m-%d}" if len(days) > 1 else f"{days[0]:%Y-%m-%d}"
            body = [f"<p class='sub'>{len(rows)} snapshots. Force = the 16 POWER judges, weighted, "
                    f"retired judges excluded. Distance in ATR ({getattr(A,'ATR',3.18)}).</p>"]
            body.append(f"<h2>Verdict: {head}</h2><p>{advice}</p>")
            for nb in horizons:
                bk = buckets(rows, nb)
                rho = summary[nb]
                body.append(f"<h3>{nb} M5 bars ({nb*5} minutes)</h3>")
                body.append(f"<p class='sub'>rank correlation force &rarr; distance: "
                            f"<b>{'n/a' if rho is None else f'{rho:+.3f}'}</b></p>")
                if bk:
                    trs = "".join(
                        f"<tr><td>{b['f_lo']:.2f} .. {b['f_hi']:.2f}</td><td>{b['n']}</td>"
                        f"<td>{b['median']:.2f}</td><td>{b['mae']:.2f}</td>"
                        f"<td>{b['ratio']:.2f}</td><td>{b['hit']:.1f}%</td></tr>" for b in bk)
                    body.append("<table><tr><th>force band</th><th>n</th>"
                                "<th>med MFE (ATR)</th><th>med MAE (ATR)</th>"
                                "<th>MFE/MAE</th><th>right side</th></tr>"
                                + trs + "</table>")
                    body.append(f"<p class='sub'>shape: {shapes.get(nb,('unknown',0))[0]} | "
                                f"force picked the right side {dirskill.get(nb,0):.1f}% of the time</p>")
            if skipped:
                body.append("<p class='sub'>Days skipped, named on purpose: "
                            + "; ".join(f"{d:%Y-%m-%d} - {w}" for d, w in skipped) + "</p>")
            html = A.one_page_shell("Force vs distance", "Does a louder panel travel further?",
                                    "".join(body))
            p = A.DATA_DIR() / f"force_study_{tag}.html"
            p.write_text(html, encoding="utf-8")
            print(f"[saved] {p}")
        except Exception as e:
            print(f"[warn] html not written: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
