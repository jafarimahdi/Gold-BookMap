#!/usr/bin/env python3
"""decision_ledger.py -- one row per signal: who stopped it, and what it would have done.

The end-of-day report grades the judges, the team and the gates in separate
sections. This ledger is the unified view the owner asked for (2026-10-08):
EVERY BUY/SELL decision of the day on one line --

    when | direction | price | confidence | who stopped it | why
    | what actually happened next on the tape (TP / SL / timeout, points)

so that over weeks we can see, gate by gate and signal by signal, which parts
of the robot's logic earn their keep -- including the ones whose job is to
say NO.

Honest limits, said out loud:
- The counterfactual is a replay with the standard geometry (SL 2.0 x ATR(M5),
  TP 3.5 x ATR(M5), max 180 min, cost 0.5 pts/round trip) -- the same family
  as TEST 7's what-if. It is an estimate, not a fill.
- "stopped_by" is classified from the reason text the robot itself wrote;
  the raw reason is always shown next to it, so the classification can be
  checked, never trusted blindly.
- Decisions with direction NEUTRAL are counted, not graded (they were never
  signals). Signals inside the tape's last candle cannot be scored (marked
  last_bar) -- same honesty rule as TEST 7.

Usage (git bash, project folder, after the robot's day is done):
    python decision_ledger.py --date 2026-10-08

Output: console summary + data/decision_ledger_<date>.csv (one row per signal)
        + the same text saved to data/decision_ledger_<date>.txt
The M5 'ALL PTS' line should land near the day report's what-if total.
"""
import argparse, bisect, csv, os, sys
from datetime import datetime

import tf_study as T  # one engine, two reports: same tape reader, candles, ATR


def classify(reason, exec_status):
    """Best-effort 'who stopped it' from the robot's own reason text."""
    if exec_status not in ("SKIPPED", "", None):
        return "WENT THROUGH"
    r = (reason or "").lower()
    if "power v2 veto" in r or "shooting" in r:
        return "TEAM veto (POWER/Shooting)"
    if "news" in r or "blackout" in r:
        return "GUARD news"
    if "spread" in r:
        return "GUARD spread"
    if "stale" in r or "feed" in r:
        return "GUARD feed"
    if "confidence" in r:
        return "CONF GATE (<50%)"
    if "cooldown" in r or "daily" in r or "loss" in r:
        return "GUARD risk"
    if not r:
        return "(no reason recorded)"
    return "OTHER (see reason)"


def replay_one(ts, direction, entry, keys, candles, atrs, tf, sl_m, tp_m, cost, max_min):
    """Replay a single signal under M5 geometry. Returns (status, outcome, pts)."""
    i = bisect.bisect_right(keys, ts) - 1
    if i < 0:
        return ("no_history", "-", None)
    if i >= len(keys) - 1:
        return ("last_bar", "-", None)      # inside the tape's last candle: nothing after it
    atr = atrs[i]
    if not atr or atr <= 0:
        return ("no_atr", "-", None)
    max_bars = max(1, int(max_min // tf))
    slp, tpp = sl_m * atr, tp_m * atr
    buy = direction == "BUY"
    for j in range(i + 1, min(i + 1 + max_bars, len(candles))):
        _, h, l, c, _ = candles[j]
        hit_sl = (l <= entry - slp) if buy else (h >= entry + slp)
        hit_tp = (h >= entry + tpp) if buy else (l <= entry - tpp)
        if hit_sl:                            # same-bar SL+TP -> count the STOP (conservative)
            return ("replayed", "SL", -slp - cost)
        if hit_tp:
            return ("replayed", "TP", tpp - cost)
    j = min(i + max_bars, len(candles) - 1)
    resid = (candles[j][3] - entry) if buy else (entry - candles[j][3])
    return ("replayed", "TIMEOUT", resid - cost)


def main():
    ap = argparse.ArgumentParser(description="per-signal decision ledger")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--tf", type=int, default=5)
    ap.add_argument("--sl", type=float, default=2.0)
    ap.add_argument("--tp", type=float, default=3.5)
    ap.add_argument("--cost", type=float, default=0.5)
    ap.add_argument("--max-min", type=int, default=180)
    args = ap.parse_args()

    lines = []
    say = lines.append
    say("=" * 100)
    say(f" DECISION LEDGER - {args.date}  (every signal: who stopped it, and what it would have done)")
    say(f" replay geometry: SL {args.sl}xATR(M{args.tf}) | TP {args.tp}xATR(M{args.tf}) | max hold {args.max_min} min | cost {args.cost} pts/round trip")
    say("=" * 100)

    trades, err = T.read_day_trades(args.date)
    if err:
        say(f" [no tape] {err}")
        print("\n".join(lines)); sys.exit(0)
    compact = args.date.replace("-", "")
    dpath = os.path.join("data", f"decisions_{compact}.csv")
    if not os.path.exists(dpath):
        say(f" [no decisions] missing {dpath}")
        print("\n".join(lines)); sys.exit(0)

    keys, candles = T.build_candles(trades, args.tf)
    atrs = T.atr_series(candles)

    rows_out = []
    n_total = n_neutral = 0
    buckets = {}   # stopped_by -> {n, pts, wins, unreplayed}
    with open(dpath, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            d = (row.get("signal_direction") or "").strip().upper()
            n_total += 1
            if d not in ("BUY", "SELL"):
                n_neutral += 1
                continue
            try:
                price = float(row.get("price") or 0)
            except ValueError:
                continue
            if price <= 0:
                continue
            ts = T.parse_any_ts(row.get("timestamp"))
            if ts is None:
                continue
            try:
                conf = float(row.get("signal_confidence") or 0)
            except ValueError:
                conf = 0.0
            exec_status = (row.get("exec_status") or "").strip()
            reason = (row.get("reason") or "").strip()
            stopped_by = classify(reason, exec_status)
            status, outcome, pts = replay_one(ts, d, price, keys, candles, atrs,
                                              args.tf, args.sl, args.tp,
                                              args.cost, args.max_min)
            b = buckets.setdefault(stopped_by, {"n": 0, "pts": 0.0, "wins": 0, "unreplayed": 0})
            b["n"] += 1
            if pts is None:
                b["unreplayed"] += 1
            else:
                b["pts"] += pts
                b["wins"] += 1 if pts > 0 else 0
            rows_out.append({
                "timestamp": row.get("timestamp"), "direction": d,
                "price": f"{price:.2f}", "conf": f"{conf:.1f}",
                "ai_action": row.get("ai_action") or "",
                "exec_status": exec_status, "stopped_by": stopped_by,
                "reason": (reason[:80]), "replay_status": status,
                "outcome": outcome,
                "pts": "" if pts is None else f"{pts:+.2f}",
                "would_have": "" if pts is None else ("WON" if pts > 0 else "LOST"),
            })

    signals = len(rows_out)
    replayed = [r for r in rows_out if r["replay_status"] == "replayed"]
    say(f" decisions this day: {n_total} | NEUTRAL (never signals): {n_neutral} | signals graded here: {signals}")
    say(f" replayed: {len(replayed)} | not replayable (last candle of the tape / no history): {signals - len(replayed)}")
    say("")
    say(" WHO SAID NO, AND WAS IT RIGHT?  (counterfactual pts of the signals each gate stopped)")
    say(f" {'STOPPED BY':<28} {'N':>5} {'REPLAYED':>8} {'WOULD-BE PTS':>13} {'PTS/SIG':>9} {'WR%':>6}")
    say(" " + "-" * 74)
    for name in sorted(buckets, key=lambda k: buckets[k]["pts"]):
        b = buckets[name]
        n_r = b["n"] - b["unreplayed"]
        ppc = b["pts"] / n_r if n_r else 0.0
        wr = 100.0 * b["wins"] / n_r if n_r else 0.0
        say(f" {name:<28} {b['n']:>5} {n_r:>8} {b['pts']:>+13.1f} {ppc:>+9.2f} {wr:>6.1f}")
    total_pts = sum(b["pts"] for b in buckets.values())
    say(" " + "-" * 74)
    say(f" {'ALL SIGNALS':<28} {signals:>5} {len(replayed):>8} {total_pts:>+13.1f}")
    say("")
    say(" how to read it:")
    say("   a NEGATIVE would-be pts row = the gate SAVED that money (stopping was right).")
    say("   a POSITIVE row = the gate THREW AWAY that money (stopping cost us) -- the")
    say("   candidates to revisit are positive rows with many replayed signals, not one-off rows.")
    say(f"   'ALL SIGNALS' should land near the day report's TEST 7 what-if (same geometry family).")

    out_csv = os.path.join("data", f"decision_ledger_{args.date}.csv")
    try:
        os.makedirs("data", exist_ok=True)
        with open(out_csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
            w.writeheader()
            w.writerows(rows_out)
        say(f" [saved] {out_csv}  <- one row per signal: open it in any spreadsheet")
    except OSError as e:
        say(f" [could not save {out_csv}: {e}]")
    out_txt = os.path.join("data", f"decision_ledger_{args.date}.txt")
    try:
        with open(out_txt, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        say(f" [could not save {out_txt}: {e}]")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
