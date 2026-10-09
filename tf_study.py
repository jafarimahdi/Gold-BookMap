#!/usr/bin/env python3
"""tf_study.py -- end-of-day TIMEFRAME GEOMETRY study (M3 / M5 / M8 / M15).

Two questions, two row types, said out loud so nobody confuses them:

  1. "M5 (audit)" row  -- the EDITOR'S OWN replay convention: constant ATR
     (AUDIT_ATR, default 3.18), spread cost, walk starts at the candle after
     the entry minute, SL checked before TP. It must land near the day
     report's TEST 7 what-if. It is the reconciliation line.
  2. "M3/M5/M8/M15" rows -- each TF's OWN adaptive geometry: SL/TP scale with
     that timeframe's rolling ATR (last closed bar). This answers "would
     finer/coarser exits have scored better on the same decisions?" -- a
     DIFFERENT question, so these rows are not expected to equal TEST 7.

The tape reader is a faithful port of audit_day.py's conventions (2026-10-09,
after the reconciliation chase): stamps are normalised the Editor's way
(naive = Budapest local; aware stamps converted; epoch supported), the day is
the BUDAPEST calendar day, only ",Last," rows count as trade prints, dedupe
is on (timestamp, price, size), price must sit in (1000, 10000), and signals
need price > 1000. The Budapest DST rule here is the CORRECTED EU rule (last
Sunday of October) -- the same one apply_tz_label_fix.py installs, so before
and after Oct 25 both tools agree.

What it does NOT answer (Level B, a separate project): it does not re-vote
the judges or re-run POWER/Shooting at another timeframe. The study informs,
it never switches.

Usage (git bash, project folder, after the robot's day is done):
    python tf_study.py --date 2026-10-09
    python tf_study.py --date 2026-10-09 --tfs 3,5,8,15
Env keys honoured (same ones audit_day.py reads): AUDIT_ATR, AUDIT_SPREAD,
STOP_LOSS_ATR_MULT, TAKE_PROFIT_ATR_MULT, AUDIT_MAX_HOLD_MIN.

Output: console table + data/tf_study_<date>.txt (same text).
"""
import argparse, bisect, csv, glob, gzip, os, statistics, sys
from datetime import datetime, timedelta, timezone, tzinfo


# ----------------------------------------------------------- Budapest time ---
class Budapest(tzinfo):
    """Europe/Budapest, DST-aware without zoneinfo/tzdata (corrected EU rule:
    last Sunday of March -> LAST Sunday of October), matching the patched
    audit_day.py."""

    def utcoffset(self, dt):
        return timedelta(hours=2 if self._dst(dt) else 1)

    def dst(self, dt):
        return timedelta(hours=1) if self._dst(dt) else timedelta(0)

    def tzname(self, dt):
        return "CEST" if self._dst(dt) else "CET"

    @staticmethod
    def _dst(dt):
        y = dt.year
        mar_last = datetime(y, 3, 31)
        dst_on = mar_last - timedelta(days=(mar_last.weekday() + 1) % 7)
        oct_last = datetime(y, 10, 31)
        dst_off = oct_last - timedelta(days=(oct_last.weekday() + 1) % 7)
        naive = dt.replace(tzinfo=None)
        return dst_on <= naive < dst_off


BUDA = Budapest()


def parse_ts(s):
    """audit_day.py's parse_ts, ported: naive stamps are Budapest local,
    aware stamps are converted, epoch seconds/ms supported. Returns an
    aware datetime normalised to UTC, or None."""
    if not s:
        return None
    s = str(s).strip().replace("Z", "+00:00")
    for attempt in (s, s.replace(" ", "T", 1)):
        try:
            dt = datetime.fromisoformat(attempt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BUDA)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    try:
        v = float(s)
        if v > 1e12:
            v /= 1000.0
        return datetime.fromtimestamp(v, tz=timezone.utc)
    except ValueError:
        return None


# kept for decision_ledger.py (same parser, friendlier old name)
parse_any_ts = parse_ts


def on_day(dt, date_str):
    """True when the stamp's BUDAPEST date is date_str (one day = one local day)."""
    if not dt:
        return False
    try:
        return dt.astimezone(BUDA).date() == datetime.fromisoformat(date_str).date()
    except Exception:
        return False


# ------------------------------------------------------------ tape reading ---
def read_day_trades(date_str):
    """Trade prints for that Budapest day -- a faithful port of audit_day.py's
    reader: same files (root, data/, data/archive/), same ",Last," prefilter,
    same (timestamp, price, size) dedupe, same price bounds, same day rule."""
    tag = date_str
    files, seen_paths = [], set()

    def add(path):
        rp = os.path.realpath(path)
        if rp not in seen_paths:
            seen_paths.add(rp)
            files.append(path)

    compact = date_str.replace("-", "")
    for d in (".", "data", "data/archive"):
        for pat in (f"ticks_{compact}_*.csv", f"ticks_{compact}_*.csv.gz"):
            for p in sorted(glob.glob(os.path.join(d, pat))):
                add(p)
    for live in ("ticks.csv", "data/ticks.csv"):   # live file = newest chapter
        if os.path.exists(live):
            add(live)
    if not files:
        return None, "no ticks.csv and no rotated chunks for that date"
    trades, seen = [], set()
    for path in files:
        op = gzip.open if path.endswith(".gz") else open
        try:
            with op(path, "rt", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not line or line[0] == "t":        # header
                        continue
                    if line[:10] != tag or ",Last," not in line:
                        continue                           # the Editor's cheap prefilter
                    try:
                        parts = next(csv.reader([line]))
                    except Exception:
                        continue
                    if len(parts) < 6:
                        continue
                    if parts[1] not in ("Last", "Trade", "trade"):
                        continue
                    dt = parse_ts(parts[0])
                    if dt is None or not on_day(dt, date_str):
                        continue
                    try:
                        price = float(parts[2])
                    except ValueError:
                        continue
                    if not (1000 < price < 10000):
                        continue
                    size = float(parts[3]) if parts[3] else 0.0
                    key = (dt, price, size)
                    if key in seen:                       # same print via two files
                        continue
                    seen.add(key)
                    trades.append((dt, price))
        except OSError:
            continue
    trades.sort(key=lambda x: x[0])
    return (trades, None) if trades else (None, f"trade prints found in {files} but none on {date_str}")


def build_candles(trades, tf_min):
    """OHLCV candles at tf_min (audit convention: bucket on the stamp's minute)."""
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
            if price <= 1000:                # the Editor's own candidate filter
                continue
            ts = parse_ts(row.get("timestamp"))
            if ts is None:
                continue
            try:
                conf = float(row.get("signal_confidence") or 0)
            except ValueError:
                conf = 0.0
            rows.append((ts, d, price, conf))
    rows.sort(key=lambda x: x[0])
    return (rows, None) if rows else (None, f"{path} has no BUY/SELL rows with a price")


def walk_start(keys, ts):
    """Editor convention: first candle starting at/after the entry minute,
    then begin the walk at the NEXT candle. Returns the walk-start index,
    or None when the entry sits too late in the tape."""
    k = bisect.bisect_left(keys, ts.replace(second=0, microsecond=0))
    i = k + 1
    return i if i < len(keys) else None


def last_closed_idx(keys, ts, tf_min):
    """Index of the last candle that fully CLOSED before the entry stamp."""
    return bisect.bisect_right(keys, ts - timedelta(minutes=tf_min)) - 1


def replay(signals, keys, candles, atrs, tf_min, sl_m, tp_m, cost, max_min,
           conf_gate, atr_mode="per_bar", const_atr=None):
    """Replay each signal under one geometry. Returns stats dict.

    atr_mode="const"  -> Editor's convention: every trade uses const_atr.
    atr_mode="per_bar"-> this TF's rolling ATR of the last CLOSED candle.
    Walk starts at the candle after the entry minute (Editor convention);
    inside a candle the STOP is checked before the target (conservative).
    """
    max_bars = max(1, int(max_min // tf_min))
    scored, wins, pts_total = 0, 0, 0.0
    gate_p, gate_n = 0.0, 0
    low_p, low_n = 0.0, 0
    no_history, last_bar = 0, 0
    for ts, direction, entry, conf in signals:
        i = walk_start(keys, ts)
        if i is None:
            last_bar += 1          # sits inside the tape's last candle: nothing after it
            continue
        seg = candles[i:i + max_bars]
        if not seg:
            last_bar += 1
            continue
        if atr_mode == "const":
            atr = const_atr
        else:
            j = last_closed_idx(keys, ts, tf_min)
            atr = atrs[j] if j >= 0 else None
        if not atr or atr <= 0:
            no_history += 1
            continue
        slp, tpp = sl_m * atr, tp_m * atr
        buy = direction == "BUY"
        result = None
        for c in seg:
            _, h, l, cl, _ = c
            hit_sl = (l <= entry - slp) if buy else (h >= entry + slp)
            hit_tp = (h >= entry + tpp) if buy else (l <= entry - tpp)
            if hit_sl:                 # same-candle SL+TP -> count the STOP (conservative)
                result = -slp
                break
            if hit_tp:
                result = tpp
                break
        if result is None:             # timeout: close at the last walked candle's close
            result = (seg[-1][3] - entry) if buy else (entry - seg[-1][3])
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
    ap.add_argument("--sl", type=float, default=float(os.environ.get("STOP_LOSS_ATR_MULT", "2.0")))
    ap.add_argument("--tp", type=float, default=float(os.environ.get("TAKE_PROFIT_ATR_MULT", "3.5")))
    ap.add_argument("--cost", type=float, default=float(os.environ.get("AUDIT_SPREAD", "0.50")))
    ap.add_argument("--max-min", type=int, default=int(os.environ.get("AUDIT_MAX_HOLD_MIN", "180")))
    ap.add_argument("--conf-gate", type=float, default=50.0)
    args = ap.parse_args()

    const_atr = float(os.environ.get("AUDIT_ATR", "3.18"))   # the Editor's own constant

    lines = []
    say = lines.append
    say("=" * 100)
    say(f" TIMEFRAME GEOMETRY STUDY - {args.date}  (same day's real decisions, each geometry's own exits)")
    say(f" SL {args.sl}xATR | TP {args.tp}xATR | max hold {args.max_min} min | cost {args.cost} pts/round trip | conf gate {args.conf_gate:.0f}%")
    say(f" 'M5 (audit)' uses the Editor's constant ATR {const_atr} (AUDIT_ATR) -> must land near TEST 7's what-if.")
    say(" per-TF rows use that TF's own rolling ATR (adaptive geometry) -> a different question, not comparable to TEST 7.")
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
    hdr = (f" {'GEOMETRY':>10} {'BARS':>5} {'ATRmed':>7} {'TP pts':>7} {'cost%TP':>7} "
           f"{'SCORED':>6} {'WR%':>5} {'ALL PTS':>9} {'PTS/CALL':>9} "
           f"{'GATE+ PTS':>10} {'GATE- PTS':>10} {'NOHIST':>6} {'LASTBAR':>7}")
    say(hdr)
    say(" " + "-" * (len(hdr) - 1))

    def row(label, keys, candles, atrs, tf, atr_mode, const=None):
        warm = [a for a in atrs if a]
        if not candles:
            say(f" {label:>10}   (no candles)")
            return
        if atr_mode == "per_bar" and not warm:
            say(f" {label:>10} {len(candles):>5}   (ATR not warmed: {len(candles)} bars is less than 15 - too little history at this TF)")
            return
        atr_med = statistics.median(warm) if warm else const
        tp_pts = args.tp * (atr_med if atr_mode == "per_bar" else const)
        st = replay(signals, keys, candles, atrs, tf, args.sl, args.tp,
                    args.cost, args.max_min, args.conf_gate, atr_mode, const)
        wr = 100.0 * st["wins"] / st["scored"] if st["scored"] else 0.0
        ppc = st["pts"] / st["scored"] if st["scored"] else 0.0
        cost_share = 100.0 * args.cost / tp_pts if tp_pts > 0 else 0.0
        say(f" {label:>10} {len(candles):>5} {atr_med:>7.2f} {tp_pts:>7.2f} {cost_share:>6.1f}% "
            f"{st['scored']:>6} {wr:>5.1f} {st['pts']:>9.1f} {ppc:>+9.2f} "
            f"{st['gate_p']:>+10.1f} {st['low_p']:>+10.1f} {st['no_history']:>6} {st['last_bar']:>7}")

    # the Editor-baseline row first (the reconciliation line), then the per-TF rows
    keys5, candles5 = build_candles(trades, 5)
    atrs5 = atr_series(candles5)
    row("M5 (audit)", keys5, candles5, atrs5, 5, "const", const_atr)
    for tf in [int(x) for x in args.tfs.split(",") if x.strip()]:
        if tf == 5:
            row("M5", keys5, candles5, atrs5, 5, "per_bar")
            continue
        keys, candles = build_candles(trades, tf)
        atrs = atr_series(candles)
        row(f"M{tf}", keys, candles, atrs, tf, "per_bar")

    say("")
    say(" how to read it:")
    say("   'M5 (audit)' is the reconciliation line: constant ATR, the Editor's own convention;")
    say("   it should land near the day report's TEST 7 what-if (small gaps = tick-dedupe noise,")
    say("   a big gap = a flag to look closer).")
    say("   The per-TF rows scale SL/TP with each TF's own ATR - as TF shrinks the targets shrink,")
    say("   so cost%TP rises: the spread eats a bigger share of smaller targets.")
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
