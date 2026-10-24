#!/usr/bin/env python3
"""tf_robot_replay.py -- THE PARALLEL-UNIVERSE MACHINE (nightly, rung 2).

The owner's request (2026-10-12): "every night I want to see how the robot
would have worked that day at 1, 3, 5, 8 and 10 minutes."  This runs the REAL
robot brain (your patched step2_market_analysis.py, the one that trades) on
the recorded day once per timeframe -- A1_BASE_TF_MINUTES=<tf> via env, the
A1 knob built for exactly this -- and reports, per universe:

  SIGNALS   how many BUY/SELL the real panel produced
  CONFIDENT how many cleared the 50 gate
  WHAT-IF   those signals scored on the SAME audit ruler for every TF
            (SL 2.0 x 3.18, TP 3.5 x 3.18, max hold 180 min, cost 0.5) --
            comparable ACROSS timeframes; NOT the robot's own exits
  BELLS     full computes vs A1 serves (the boots working at that TF)
  HONESTY   warm-up is the robot's own law: 50 bars x TF minutes.  At M10
            that is 500 minutes -- the robot literally cannot wake before
            mid-afternoon.  The table says so instead of hiding it.

Reuses the twin-day harness (read_day_trades, build_md, run_child) -- the
same proven way of running the real brain on tape with a replay clock.
Read-only.  Writes data/tf_robot_<date>.txt (+ per-TF jsonl in data/).

Usage:  python tf_robot_replay.py --date 2026-10-12
        python tf_robot_replay.py --date 2026-10-12 --tfs 1,3,5,8,10 --window 08:00-23:00
"""
import argparse
import bisect
import json
import os
import statistics
import subprocess
import sys
from datetime import datetime, timedelta, timezone

from twin_day_runner import BUDA, build_md, read_day_trades

SL = 2.0 * 3.18      # audit ruler, same for every TF (comparability)
TP = 3.5 * 3.18
COST = 0.5
MAXHOLD = 180


def build_m1(trades):
    bars = {}
    for t, p in trades:
        k = t.replace(second=0, microsecond=0)
        b = bars.get(k)
        if b is None:
            bars[k] = [p, p, p, p]
        else:
            b[1] = max(b[1], p); b[2] = min(b[2], p); b[3] = p
    keys = sorted(bars)
    return keys, [bars[k] for k in keys]


def ruler(keys, bars, t0, direction, entry):
    """Audit-style walk from the first M1 bar at/after t0. SL checked first
    inside a minute (conservative). Returns pts (float) or None (no bars)."""
    i = bisect.bisect_left(keys, t0)
    end_i = min(i + MAXHOLD, len(bars))
    if i >= len(bars):
        return None
    if direction == "BUY":
        for b in bars[i:end_i]:
            if b[2] <= entry - SL:
                return -SL - COST
            if b[1] >= entry + TP:
                return TP - COST
    else:
        for b in bars[i:end_i]:
            if b[1] >= entry + SL:
                return -SL - COST
            if b[2] <= entry - TP:
                return TP - COST
    last = bars[end_i - 1][3]
    move = (last - entry) if direction == "BUY" else (entry - last)
    return move - COST


def find_field(snap, names):
    """Key-tolerant lookup: top level first, then one dict level deep."""
    for n in names:
        if n in snap and snap[n] is not None:
            return snap[n]
    for v in snap.values():
        if isinstance(v, dict):
            for n in names:
                if n in v and v[n] is not None:
                    return v[n]
    return None


def run_child(tf, brain, plan, out):
    tf_min = max(1, int(tf))
    caller_aggr = os.environ.get("AGGRESSIVE_ENTRY_ENABLED")
    # Import config FIRST so its module-load .env pass (override=True) runs
    # before we stamp the child process environment; otherwise step2's
    # `import config` clobbers A1_BASE_TF_MINUTES/TIMEFRAME back to .env.
    import config
    os.environ["A1_BASE_TF_MINUTES"] = str(tf_min)
    os.environ["TIMEFRAME"] = f"M{tf_min}"
    os.environ["POWER_BAR_SECONDS"] = str(tf_min * 60)
    os.environ["POWER_V2_MEMORY_ENABLED"] = "0"
    os.environ["AGGRESSIVE_ENTRY_ENABLED"] = caller_aggr if caller_aggr is not None else "0"
    os.environ["TRADING_ENABLED"] = "0"
    os.environ["ALLOW_LIVE_TRADING"] = "0"
    config._refresh()
    from twin_day_runner import run_child as rc   # env is set before brain load
    import types
    ns = types.SimpleNamespace(child=brain, plan=plan, out=out)
    rc(ns)


def parse_run(out_path, cycles_by_i):
    sigs, confs, served, full, errs = [], [], 0, 0, 0
    first_sig = None
    peek_keys = None
    with open(out_path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("error"):
                errs += 1
                continue
            if rec.get("served"):
                served += 1
            else:
                full += 1
            snap = rec.get("snap") or {}
            if peek_keys is None and snap:
                peek_keys = sorted(snap.keys())
            d = find_field(snap, ("signal_direction", "direction", "signal"))
            c = find_field(snap, ("confidence", "conf", "signal_confidence"))
            try:
                c = float(c) if c is not None else None
            except (TypeError, ValueError):
                c = None
            if isinstance(d, str) and d.upper() in ("BUY", "SELL"):
                d = d.upper()
                cyc = cycles_by_i.get(rec.get("i"), {})
                sigs.append({"i": rec.get("i"), "d": d, "conf": c,
                             "now": rec.get("now"), "price": cyc.get("price")})
                if first_sig is None:
                    first_sig = rec.get("now")
                if c is not None:
                    confs.append(c)
    return {"sigs": sigs, "confs": confs, "served": served, "full": full,
            "errs": errs, "first": first_sig, "peek": peek_keys}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        # dispatch BEFORE argparse: child mode has its own positional layout
        run_child(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
        return 0
    ap = argparse.ArgumentParser(description="parallel-universe machine (nightly TF replay)")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--tfs", default="1,3,5,8,10")
    ap.add_argument("--window", default="08:00-23:00")
    ap.add_argument("--every", type=int, default=60)
    ap.add_argument("--brain", default="step2_market_analysis.py")
    ap.add_argument("--peek", action="store_true")
    ap.add_argument("--out-dir", default="data")
    args = ap.parse_args()
    lines = []
    def say(s=""):
        lines.append(s)
        print(s)
    tfs = [int(x) for x in args.tfs.split(",") if x.strip()]

    trades, err = read_day_trades(args.date)
    if err:
        say(f"[no tape] {err}")
        return 0
    times = [t for t, _ in trades]
    say("=" * 100)
    say(f" TF ROBOT REPLAY (the parallel-universe machine) - {args.date}")
    say(f" the REAL robot brain at base TF {', '.join('M' + str(t) for t in tfs)}"
        f" | ruler: SL {SL:.2f} TP {TP:.2f} hold {MAXHOLD}m cost {COST} (same for every TF)")
    say(" informs, never switches -- rung 2 evidence for the A6/Level B review")
    say("=" * 100)
    say(f" tape: {len(trades)} prints | window {args.window} Budapest, cycle every {args.every}s")

    day0 = times[0].astimezone(BUDA).date()
    h0, m0 = (int(x) for x in args.window.split("-")[0].split(":"))
    h1, m1 = (int(x) for x in args.window.split("-")[1].split(":"))
    win_start = datetime(day0.year, day0.month, day0.day, h0, m0, tzinfo=BUDA).astimezone(timezone.utc)
    win_end = datetime(day0.year, day0.month, day0.day, h1, m1, tzinfo=BUDA).astimezone(timezone.utc)

    marks = set()
    t = win_start
    while t <= win_end:
        marks.add(t)
        t += timedelta(seconds=args.every)
    for tf in tfs:                      # every TF's own bell times
        t = win_start
        while t <= win_end:
            if t.minute % tf == 0:
                marks.add(t)
            t += timedelta(minutes=1)
    cycles = sorted(c for c in marks if times[0] <= c <= win_end)

    plan = {"cycles": []}
    cycles_by_i = {}
    for i, c in enumerate(cycles):
        md = build_md(trades, times, c, win_start)
        if md is None:
            continue
        plan["cycles"].append({"i": i, "now": c.isoformat(),
                               "bucket": int(c.timestamp() // 60 // 5),
                               "market_data": md})
        cycles_by_i[i] = {"price": md["price"], "dt": c}
    say(f" cycles per universe: {len(plan['cycles'])}")

    os.makedirs(args.out_dir, exist_ok=True)
    plan_path = os.path.join(args.out_dir, f"tfrobot_plan_{args.date}.json")
    json.dump(plan, open(plan_path, "w"))
    keys, bars = build_m1(trades)

    rows = []
    for tf in tfs:
        out_path = os.path.join(args.out_dir, f"tfrobot_M{tf}_{args.date}.jsonl")
        env = dict(os.environ, PYTHONHASHSEED="0")
        r = subprocess.run([sys.executable, __file__, "--child", str(tf),
                            args.brain, plan_path, out_path], env=env,
                           capture_output=True, text=True)
        if r.returncode != 0:
            say(f" [M{tf}] child failed: {r.stderr[-400:]}")
            continue
        res = parse_run(out_path, cycles_by_i)
        pts, wins = [], 0
        for s in res["sigs"]:
            if s["price"] is None:
                continue
            dt = cycles_by_i[s["i"]]["dt"]
            p = ruler(keys, bars, dt, s["d"], s["price"])
            if p is not None:
                pts.append(p)
                wins += 1 if p > 0 else 0
        total = sum(pts) if pts else 0.0
        conf_share = (100 * sum(1 for c in res["confs"] if c >= 50) / len(res["confs"])) \
            if res["confs"] else 0.0
        rows.append((tf, res, pts, wins, total, conf_share))
        if args.peek and res["peek"]:
            say(f" [peek] M{tf} snapshot keys: {res['peek'][:14]}")

    say("")
    say("  UNIVERSE  CYCLES  ERR  SIGNALS  BUY/S    CONF>=50  MED-CONF  WHAT-IF  PTS/SIG  WR%   BELLS(full/serve)  FIRST SIGNAL")
    say("  " + "-" * 96)
    for tf, res, pts, wins, total, conf_share in rows:
        nb = sum(1 for s in res["sigs"] if s["d"] == "BUY")
        ns = len(res["sigs"]) - nb
        med_c = f"{statistics.median(res['confs']):.0f}" if res["confs"] else "-"
        wif = f"{total:+.1f}" if pts else "-"
        pps = f"{total / len(pts):+.2f}" if pts else "-"
        wr = f"{100 * wins / len(pts):.0f}%" if pts else "-"
        first = "-"
        if res["first"]:
            try:
                first = datetime.fromisoformat(res["first"]).astimezone(BUDA).strftime("%H:%M")
            except ValueError:
                first = res["first"][:16].replace("T", " ")
        say(f"  M{tf:<8} {len(cycles):>6} {res['errs']:>4} {len(res['sigs']):>8}"
            f"  {nb}/{ns:<4} {conf_share:>7.0f}%  {med_c:>8}  {wif:>7}  {pps:>7}  {wr:>5}"
            f"  {res['full']:>4}/{res['served']:<4}      {first}")
    say("")
    say("  how to read it:")
    say("    SIGNALS/CONF = the REAL panel at that clock (rung 2 evidence).")
    say("    WHAT-IF = those signals on the SAME ruler for every TF -- compare")
    say("    universes with each other, not with TEST 7 (different ruler owner).")
    say("    warm-up is the robot's own law (50 bars): M10 needs 500 min of tape,")
    say("    so an M10 universe honestly starts mid-afternoon.  M1 pays 1x spread")
    say("    tax per minute of churn -- read its WHAT-IF with that in mind.")
    say("  informs, never switches: any real switch is A6, after this census +")
    say("  the clocks verdict + its own design review.")
    out = os.path.join(args.out_dir, f"tf_robot_{args.date}.txt")
    try:
        os.makedirs(args.out_dir, exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        say(f" [saved] {out}")
    except OSError as e:
        say(f" [could not save {out}: {e}]")
    return 0


if __name__ == "__main__":
    main()
