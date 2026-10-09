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

The counterfactual is the EDITOR'S OWN replay convention, ported line for
line from audit_day.py's simulate(): constant ATR (AUDIT_ATR, default 3.18),
spread cost (AUDIT_SPREAD, default 0.50), SL_MULT/TP_MULT from the same env
keys, the walk starts at the candle AFTER the entry minute, SL is checked
before TP inside a candle, timeout closes at the last walked candle's close.
Because it is the same convention, the ledger's ALL-SIGNALS total must land
near the day report's TEST 7 what-if. It is an estimate, not a fill.

Honest limits, said out loud:
- "stopped_by" is classified from the reason text the robot itself wrote;
  the raw reason is always shown next to it, so the classification can be
  checked, never trusted blindly.
- Decisions with direction NEUTRAL are counted, not graded (they were never
  signals). Signals inside the tape's last candle cannot be scored (marked
  last_bar) -- same honesty rule as TEST 7.
- Small residuals vs TEST 7 (a few percent) come from tick-dedupe
  conventions; a big gap is a flag to look closer, not a mystery.

Usage (git bash, project folder, after the robot's day is done):
    python decision_ledger.py --date 2026-10-08

Output: console summary + data/decision_ledger_<date>.csv (one row per signal)
        + the same text saved to data/decision_ledger_<date>.txt
"""
import argparse, bisect, csv, os, sys
from datetime import datetime

import tf_study as T  # one engine, two reports: same tape reader, candles, walk


def _envf(key, default):
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return float(default)


ATR_CONST = _envf("AUDIT_ATR", "3.18")            # the Editor's own constant
SPREAD = _envf("AUDIT_SPREAD", "0.50")
SL_MULT = _envf("STOP_LOSS_ATR_MULT", "2.0")
TP_MULT = _envf("TAKE_PROFIT_ATR_MULT", "3.5")
MAX_HOLD_MIN = _envf("AUDIT_MAX_HOLD_MIN", "180")


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


def replay_one(ts, direction, entry, keys, candles, tf, sl_m, tp_m, spread, max_min):
    """Exact port of audit_day.py simulate(): constant ATR, walk starts at the
    candle after the entry minute, SL before TP, timeout at last walked close.
    Returns (status, outcome, pts)."""
    i = T.walk_start(keys, ts)
    if i is None:
        return ("last_bar", "-", None)
    max_bars = max(1, int(max_min // tf))
    seg = candles[i:i + max_bars]
    if not seg:
        return ("last_bar", "-", None)
    slp, tpp = sl_m * ATR_CONST, tp_m * ATR_CONST
    buy = direction == "BUY"
    for c in seg:
        _, h, l, cl, _ = c
        hit_sl = (l <= entry - slp) if buy else (h >= entry + slp)
        hit_tp = (h >= entry + tpp) if buy else (l <= entry - tpp)
        if hit_sl:                            # same-candle SL+TP -> count the STOP (conservative)
            return ("replayed", "SL", (-slp if buy else -slp) - spread)
        if hit_tp:
            return ("replayed", "TP", tpp - spread)
    resid = (seg[-1][3] - entry) if buy else (entry - seg[-1][3])
    return ("replayed", "TIMEOUT", resid - spread)


def main():
    ap = argparse.ArgumentParser(description="per-signal decision ledger")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--tf", type=int, default=5)
    args = ap.parse_args()
    sl_m, tp_m, spread, max_min = SL_MULT, TP_MULT, SPREAD, MAX_HOLD_MIN

    lines = []
    say = lines.append
    say("=" * 100)
    say(f" DECISION LEDGER - {args.date}  (every signal: who stopped it, and what it would have done)")
    say(f" Editor's own geometry: SL {sl_m}x{ATR_CONST} | TP {tp_m}x{ATR_CONST} (constant ATR, AUDIT_ATR) "
        f"| max hold {max_min:.0f} min | spread {spread} pts/round trip")
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
            status, outcome, pts = replay_one(ts, d, price, keys, candles,
                                              args.tf, sl_m, tp_m, spread, max_min)
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
    say("   'ALL SIGNALS' uses the Editor's own replay convention (constant ATR) and must land")
    say("   near the day report's TEST 7 what-if; a big gap is a flag, not a mystery.")

    out_csv = os.path.join("data", f"decision_ledger_{args.date}.csv")
    try:
        os.makedirs("data", exist_ok=True)
        if rows_out:
            with open(out_csv, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
                w.writeheader()
                w.writerows(rows_out)
            say(f" [saved] {out_csv}  <- one row per signal: open it in any spreadsheet")
        else:
            say(f" [no signal rows to save]")
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
