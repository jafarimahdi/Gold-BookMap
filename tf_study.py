#!/usr/bin/env python3
"""tf_study.py -- end-of-day TIMEFRAME GEOMETRY study (M3 / M5 / M8 / M15).

Question it answers (honestly and narrowly):
    The SAME day's real decisions (from data/decisions_YYYYMMDD.csv) are
    replayed under each timeframe's exit geometry: SL = SL_MULT x ATR(TF),
    TP = TP_MULT x ATR(TF), where ATR is measured on THAT timeframe's own
    candles built from the same tape. Would the same calls have scored
    better with finer (M3) or coarser (M8/M15) exits?

What it does NOT answer (Level B, a separate project):
    It does not re-vote the judges or re-run POWER/Shooting at another
    timeframe. The decisions are the day's real M5 decisions; only the
    exit geometry changes. "The study informs, it never switches."

Usage (git bash, project folder, after the robot's day is done):
    python tf_study.py --date 2026-10-09
    python tf_study.py --date 2026-10-09 --tfs 3,5,8,15 --conf-gate 50

Output: console table + data/tf_study_<date>.txt (same text).
The M5 row is the baseline: it should land near the day report's what-if
(small differences are replay-convention noise; big differences are a flag).
"""
import argparse, bisect, csv, glob, gzip, os, statistics, sys
from datetime import datetime, timedelta

TRADE_EVENTS = ("Last", "Trade", "trade")


def parse_any_ts(s):
    """Wall-clock time AS WRITTEN (naive).

    The tape stamps carry the local offset (+02:00) and the decision stamps
    are naive local; both describe the same Budapest wall clock, so we keep
    the wall time and drop the offset. Converting to the machine's zone
    instead would make the two sources disagree whenever this tool is run
    from a different timezone.
    """
    if not s:
        return None
    s = str(s).strip().replace("Z", "+00:00")
    for attempt in (s, s.replace(" ", "T", 1)):
        try:
            return datetime.fromisoformat(attempt).replace(tzinfo=None)
        except ValueError:
            continue
    return None


def read_day_trades(date_str):
    """Trade prints for that local day from ticks.csv + its rotated chunks."""
    compact = date_str.replace("-", "")
    files = sorted(glob.glob(f"ticks_{compact}_*.csv.gz"))
    if os.path.exists("ticks.csv"):
        files.append("ticks.csv")
    if os.path.exists("data/ticks.csv"):
        files.append("data/ticks.csv")
    if not files:
        return None, "no ticks.csv and no rotated chunks for that date"
    trades = []
    for path in files:
        op = gzip.open if path.endswith(".gz") else open
        try:
            with op(path, "rt", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not line or line[0] not in "2":
                        continue  # header / junk
                    parts = line.rstrip("\n").split(",")
                    if len(parts) < 3 or parts[1] not in TRADE_EVENTS:
                        continue
                    try:
                        price = float(parts[2])
                    except ValueError:
                        continue
                    if price <= 0:
                        continue
                    dt = parse_any_ts(parts[0])
                    if dt is None or dt.strftime("%Y-%m-%d") != date_str:
                        continue
                    trades.append((dt, price))
        except OSError:
            continue
    trades.sort(key=lambda x: x[0])
    return (trades, None) if trades else (None, f"trade prints found in {files} but none on {date_str}")


def build_candles(trades, tf_min):
    """OHLCV candles at tf_min from (dt, price) prints."""
    bars = {}
    for dt, price in trades:
        bucket = dt.replace(minute=(dt.minute // tf_min) * tf_min, second=0, microsecond=0)
        b = bars.get(bucket)
        if b is None:
            bars[bucket] = [price, price, price, price, 1.0]
        else:
            b[1] = max(b[1], price)
            b[2] = min(b[2], price)
            b[3] = price
            b[4] += 1.0
    keys = sorted(bars)
    return keys, [bars[k] for k in keys]


def atr_series(candles, period=14):
    """Rolling ATR (mean true range of last `period` bars). None until warmed."""
    out = [None] * len(candles)
    trs = []
    for i, (o, h, l, c, v) in enumerate(candles):
        if i == 0:
            trs.append(h - l)
        else:
            pc = candles[i - 1][3]
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        if i >= period - 1:
            out[i] = statistics.fmean(trs[-period:])
    return out


def read_decisions(date_str):
    compact = date_str.replace("-", "")
    path = os.path.join("data", f"decisions_{compact}.csv")
    if not os.path.exists(path):
        return None, f"missing {path}"
    rows = []
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            d = (row.get("signal_direction") or "").strip().upper()
            if d not in ("BUY", "SELL"):
                continue
            try:
                price = float(row.get("price") or 0)
            except ValueError:
                continue
            if price <= 0:
                continue
            ts = parse_any_ts(row.get("timestamp"))
            if ts is None:
                continue
            try:
                conf = float(row.get("signal_confidence") or 0)
            except ValueError:
                conf = 0.0
            rows.append((ts, d, price, conf))
    rows.sort(key=lambda x: x[0])
    return (rows, None) if rows else (None, f"{path} has no BUY/SELL rows with a price")


def replay(signals, keys, candles, atrs, tf_min, sl_m, tp_m, cost, max_min, conf_gate):
    """Replay each signal under this TF's geometry. Returns stats dict."""
    max_bars = max(1, int(max_min // tf_min))
    scored, wins, pts_total = 0, 0, 0.0
    gate_p, gate_n = 0.0, 0
    low_p, low_n = 0.0, 0
    no_history, last_bar = 0, 0
    for ts, direction, entry, conf in signals:
        i = bisect.bisect_right(keys, ts) - 1
        if i < 0:
            no_history += 1
            continue
        if i >= len(keys) - 1:
            last_bar += 1          # sits inside the tape's last candle: nothing after it
            continue
        atr = atrs[i]
        if not atr or atr <= 0:
            no_history += 1
            continue
        slp, tpp = sl_m * atr, tp_m * atr
        buy = direction == "BUY"
        result = None
        for j in range(i + 1, min(i + 1 + max_bars, len(candles))):
            _, h, l, c, _ = candles[j]
            hit_sl = (l <= entry - slp) if buy else (h >= entry + slp)
            hit_tp = (h >= entry + tpp) if buy else (l <= entry - tpp)
            if hit_sl:                 # same-bar SL+TP -> count the STOP (conservative)
                result = -slp
                break
            if hit_tp:
                result = tpp
                break
        if result is None:             # timeout: close at the last walked bar's close
            j = min(i + max_bars, len(candles) - 1)
            result = (candles[j][3] - entry) if buy else (entry - candles[j][3])
        result -= cost
        scored += 1
        pts_total += result
        wins += 1 if result > 0 else 0
        if conf >= conf_gate:
            gate_p += result; gate_n += 1
        else:
            low_p += result; low_n += 1
    return {
        "scored": scored, "wins": wins, "pts": pts_total,
        "gate_p": gate_p, "gate_n": gate_n, "low_p": low_p, "low_n": low_n,
        "no_history": no_history, "last_bar": last_bar,
    }


def main():
    ap = argparse.ArgumentParser(description="timeframe geometry study")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--tfs", default="3,5,8,15")
    ap.add_argument("--sl", type=float, default=2.0)
    ap.add_argument("--tp", type=float, default=3.5)
    ap.add_argument("--cost", type=float, default=0.5, help="points per round trip")
    ap.add_argument("--max-min", type=int, default=180)
    ap.add_argument("--conf-gate", type=float, default=50.0)
    args = ap.parse_args()

    lines = []
    say = lines.append
    say("=" * 100)
    say(f" TIMEFRAME GEOMETRY STUDY - {args.date}  (same day's real decisions, each TF's own exit geometry)")
    say(f" SL {args.sl}xATR(TF) | TP {args.tp}xATR(TF) | max hold {args.max_min} min | cost {args.cost} pts/round trip | conf gate {args.conf_gate:.0f}%")
    say(" informs, never switches: this replays exits, it does NOT re-vote the panel (that is Level B)")
    say("=" * 100)

    trades, err = read_day_trades(args.date)
    if err:
        say(f" [no tape] {err}")
        print("\n".join(lines)); sys.exit(0)
    signals, err = read_decisions(args.date)
    if err:
        say(f" [no decisions] {err}")
        print("\n".join(lines)); sys.exit(0)

    say(f" tape: {len(trades)} trade prints | decisions to replay: {len(signals)} BUY/SELL with a price")
    say("")
    hdr = (f" {'TF':>4} {'BARS':>5} {'ATRmed':>7} {'TP pts':>7} {'cost%TP':>7} "
           f"{'SCORED':>6} {'WR%':>5} {'ALL PTS':>9} {'PTS/CALL':>9} "
           f"{'GATE+ PTS':>10} {'GATE- PTS':>10} {'NOHIST':>6} {'LASTBAR':>7}")
    say(hdr)
    say(" " + "-" * (len(hdr) - 1))

    for tf in [int(x) for x in args.tfs.split(",") if x.strip()]:
        keys, candles = build_candles(trades, tf)
        atrs = atr_series(candles)
        if not candles:
            say(f" {tf:>4}   (no candles)")
            continue
        warm = [a for a in atrs if a]
        if not warm:
            say(f" M{tf:<3} {len(candles):>5}   (ATR not warmed: {len(candles)} bars is less than 15 - too little history at this TF)")
            continue
        atr_med = statistics.median(warm)
        tp_pts = args.tp * atr_med
        st = replay(signals, keys, candles, atrs, tf, args.sl, args.tp,
                    args.cost, args.max_min, args.conf_gate)
        wr = 100.0 * st["wins"] / st["scored"] if st["scored"] else 0.0
        ppc = st["pts"] / st["scored"] if st["scored"] else 0.0
        cost_share = 100.0 * args.cost / tp_pts if tp_pts > 0 else 0.0
        say(f" M{tf:<3} {len(candles):>5} {atr_med:>7.2f} {tp_pts:>7.2f} {cost_share:>6.1f}% "
            f"{st['scored']:>6} {wr:>5.1f} {st['pts']:>9.1f} {ppc:>+9.2f} "
            f"{st['gate_p']:>+10.1f} {st['low_p']:>+10.1f} {st['no_history']:>6} {st['last_bar']:>7}")

    say("")
    say(" how to read it:")
    say("   M5 is the baseline - its ALL PTS should sit near the day report's what-if (small gaps are")
    say("   replay-convention noise; a big gap is a flag to look closer).")
    say("   cost%TP rising as TF shrinks = the spread eats a bigger share of smaller targets.")
    say("   A TF that wins on exits does NOT mean the panel would vote the same way at that TF.")

    out_path = os.path.join("data", f"tf_study_{args.date}.txt")
    try:
        os.makedirs("data", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        say(f" [saved] {out_path}")
    except OSError as e:
        say(f" [could not save {out_path}: {e}]")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
