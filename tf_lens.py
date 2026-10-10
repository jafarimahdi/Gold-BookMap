#!/usr/bin/env python3
"""tf_lens.py -- the TIMEFRAME LENS (TF A/B replay, rung 1).

The owner's question: "would the robot SEE more (and earlier, and better)
directional moments if its bar clock ran at 2, 3, 8, 10... minutes instead
of 5?" This tool answers it on RECORDED days. It never touches the robot.

WHAT THE ENGINE IS (said out loud, no overclaiming):
  The robot's OWN trend brain, ported verbatim from step2_market_analysis.py:
    - direction: TechnicalAnalyzer.compute_trend -- UP when sma_9 > sma_20 >
      sma_50 AND +DI > -DI; DOWN when all inverted; else NEUTRAL.
    - regime: _detect_regime / TREND_ADX_THRESHOLD -- TREND when ADX >= 25
      (Wilder, period 14).
    - warm-up: compute_trend needs 50 closed bars (the robot's own rule).
  A MOMENT = a closed bar where direction is UP or DOWN *and* regime is TREND,
  entered from a different state. Moments fire on bar CLOSE -> no look-ahead:
  the travel window only uses prints strictly AFTER the close.

WHAT IT IS NOT: the full 30-judge panel + POWER vote (that is rung 2, the
Level B engine). Rung 1 measures SEEING (more? earlier? real?), not voting.
It informs, it never switches.

Tape reader, time conventions, dedupe and day rule are the audit/Geometer
conventions, copied verbatim from tf_study.py (2026-10-09, reconciled 0.00
against the Editor and the Accountant).

Usage (git bash, project folder, evening):
    python tf_lens.py --date 2026-10-09
    python tf_lens.py --date 2026-10-09 --tfs 2,3,8,10
    python tf_lens.py --date 2026-10-08 --tfs 1,2,3,4,6,8,10,15
The baseline (default M5, the robot's home clock) is always included for
comparison. Output: console + data/tf_lens_<date>.txt (same text).
"""
import argparse, bisect, csv, glob, gzip, os, statistics, sys
from datetime import datetime, timedelta, timezone, tzinfo


# ----------------------------------------------------------- Budapest time ---
class Budapest(tzinfo):
    """Europe/Budapest, DST-aware without zoneinfo/tzdata (corrected EU rule:
    last Sunday of March -> LAST Sunday of October), matching audit_day.py."""

    def utcoffset(self, dt):
        return timedelta(hours=2 if self._dst(dt) else 1)

    def dst(self, dt):
        return timedelta(hours=1 if self._dst(dt) else 0)

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
    """audit_day.py's parse_ts, ported: naive = Budapest local, aware is
    converted, epoch s/ms supported. Returns aware datetime in UTC or None."""
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


def on_day(dt, date_str):
    if not dt:
        return False
    try:
        return dt.astimezone(BUDA).date() == datetime.fromisoformat(date_str).date()
    except Exception:
        return False


# ------------------------------------------------------------ tape reading ---
def read_day_trades(date_str):
    """Trade prints for that Budapest day -- audit_day.py's reader, verbatim
    port (same files, same ',Last,' prefilter, same dedupe, same bounds)."""
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
    if trades:
        return trades, None
    sample = ""
    try:
        op = gzip.open if files[0].endswith(".gz") else open
        with op(files[0], "rt", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line and line[0] != "t" and "," in line:
                    sample = line.strip()[:100]
                    break
    except OSError:
        pass
    return None, (f"trade prints found in {files} but none on {date_str}"
                  + (f" | first data line of {files[0]}: {sample!r}" if sample else ""))


def build_candles(trades, tf_min):
    """OHLCV candles at tf_min (audit convention: bucket on the stamp's minute).
    Returns (keys, candles): keys are bucket START stamps, candles [o,h,l,c,v]."""
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


# --------------------------------------------- the robot's trend brain -------
def sma(values, period, i):
    """Simple mean of values[i-period+1 .. i] (caller guarantees the window)."""
    return statistics.fmean(values[i - period + 1: i + 1])


def adx_series(high, low, close, period=14):
    """Per-bar Wilder ADX / +DI / -DI, a faithful port of step2's
    TechnicalAnalyzer.compute_adx (same seeds: 'sum' for TR/DM, 'mean' for
    ADX). adx[i] equals what compute_adx would return on candles[:i+1].
    None until the robot's own warm-up is satisfied."""
    n = len(high)
    adx = [None] * n
    pdi = [None] * n
    mdi = [None] * n
    if n < 2 * period + 1:
        return adx, pdi, mdi

    plus_dm, minus_dm, tr = [], [], []
    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = -(low[i] - low[i - 1])
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)
        pc = close[i - 1]
        tr.append(max(high[i] - low[i], abs(high[i] - pc), abs(low[i] - pc)))

    def wsum(data, p):                      # Wilder seed='sum' (TR / DM)
        out = [None] * len(data)
        if len(data) < p:
            return out
        out[p - 1] = sum(data[:p])
        for i in range(p, len(data)):
            out[i] = out[i - 1] - out[i - 1] / p + data[i]
        return out

    tr_s, pdm_s, mdm_s = wsum(tr, period), wsum(plus_dm, period), wsum(minus_dm, period)
    for k in range(period - 1, len(tr)):    # tr[k] belongs to bar k+1
        s = tr_s[k] + 1e-10
        pdi[k + 1] = 100.0 * pdm_s[k] / s
        mdi[k + 1] = 100.0 * mdm_s[k] / s

    dx, dx_idx = [], []
    for i in range(n):
        if pdi[i] is not None:
            dx.append(100.0 * abs(pdi[i] - mdi[i]) / (pdi[i] + mdi[i] + 1e-10))
            dx_idx.append(i)
    if len(dx) < period:
        return adx, pdi, mdi

    adx_v = [None] * len(dx)                # Wilder seed='mean' (ADX)
    adx_v[period - 1] = statistics.fmean(dx[:period])
    for i in range(period, len(dx)):
        adx_v[i] = (adx_v[i - 1] * (period - 1) + dx[i]) / period
    for j, i in enumerate(dx_idx):
        if j >= period - 1:
            adx[i] = min(100.0, max(0.0, adx_v[j]))
    return adx, pdi, mdi


def detect_moments(keys, candles, tf_min, adx_trend=25.0, warm_bars=50,
                   session=None):
    """The robot's trend brain on tf_min candles.

    Moment = closed bar where compute_trend says UP or DOWN *and* ADX >= the
    robot's TREND threshold, entered from a different state. Fires at the
    bar's CLOSE (key + tf_min): no look-ahead by construction.
    Returns a list of dicts {t, dir, entry, adx}."""
    closes = [c[3] for c in candles]
    highs = [c[1] for c in candles]
    lows = [c[2] for c in candles]
    adx, pdi, mdi = adx_series(highs, lows, closes)
    moments, state = [], None
    for i in range(warm_bars, len(candles)):        # robot's own 50-bar rule
        if adx[i] is None or pdi[i] is None:
            continue
        s9, s20, s50 = sma(closes, 9, i), sma(closes, 20, i), sma(closes, 50, i)
        if s9 > s20 > s50 and pdi[i] > mdi[i]:
            d = "UP"
        elif s9 < s20 < s50 and mdi[i] > pdi[i]:
            d = "DOWN"
        else:
            d = "NEUTRAL"
        if d == "NEUTRAL":
            state = None
            continue
        if adx[i] < adx_trend:                      # regime gate: not TREND
            continue
        if d != state:
            t_close = keys[i] + timedelta(minutes=tf_min)
            if session and not session[0] <= t_close.astimezone(BUDA).time() <= session[1]:
                state = d                           # noted, outside session: skip
                continue
            moments.append({"t": t_close, "dir": d, "entry": closes[i], "adx": adx[i]})
            state = d
    return moments


def travel_stats(prints, times, moments, window_min, need_pts):
    """Favourable / adverse travel (pts) after each moment, prints strictly
    after the moment close, over the next window_min. Fish = favourable
    travel >= need_pts."""
    fav, adv, fish, no_window = [], [], 0, 0
    for m in moments:
        lo = bisect.bisect_right(times, m["t"])
        hi = bisect.bisect_right(times, m["t"] + timedelta(minutes=window_min))
        seg = prints[lo:hi]
        if not seg:
            no_window += 1
            continue
        prices = [p for _, p in seg]
        if m["dir"] == "UP":
            f = max(prices) - m["entry"]
            a = m["entry"] - min(prices)
        else:
            f = m["entry"] - min(prices)
            a = max(prices) - m["entry"]
        fav.append(f)
        adv.append(a)
        if f >= need_pts:
            fish += 1
    n = len(fav)
    return {
        "n": n, "no_window": no_window,
        "fav_med": statistics.median(fav) if fav else None,
        "adv_med": statistics.median(adv) if adv else None,
        "fish": fish,
        "fish_rate": (100.0 * fish / n) if n else None,
    }


def match_against_baseline(moments_x, moments_b, match_min):
    """For each X moment: nearest same-direction baseline moment within
    +/- match_min. Returns (matched pairs, unique-to-X, baseline moments
    missed by X). head = (t_base - t_x): positive means X was EARLIER."""
    matched, unique_x = [], []
    used_b = set()
    for mx in moments_x:
        best, best_dt = None, None
        for j, mb in enumerate(moments_b):
            if mb["dir"] != mx["dir"] or j in used_b:
                continue
            dt_min = abs((mb["t"] - mx["t"]).total_seconds()) / 60.0
            if dt_min <= match_min and (best_dt is None or dt_min < best_dt):
                best, best_dt = j, dt_min
        if best is None:
            unique_x.append(mx)
        else:
            used_b.add(best)
            head = (moments_b[best]["t"] - mx["t"]).total_seconds() / 60.0
            matched.append({"x": mx, "b": moments_b[best], "head": head})
    missed_b = [mb for j, mb in enumerate(moments_b) if j not in used_b]
    return matched, unique_x, missed_b


def read_decisions(date_str):
    """The REAL robot's own signals that day (M5, full panel) -- context only."""
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
            if price <= 1000:
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


# ------------------------------------------------------------------- main ----
def run(args, lines):
    say = lines.append
    tfs = sorted({int(x) for x in args.tfs.split(",") if x.strip()} | {args.baseline})
    base = args.baseline
    cost = args.cost

    say("=" * 100)
    say(f" TF LENS (rung 1) - {args.date}   baseline M{base} = the robot's home clock")
    say(f" engine: the robot's OWN trend brain on closed bars -- compute_trend (SMA 9>20>50 stack + DI)")
    say(f"         + regime TREND when ADX14 >= {args.adx_trend:g} (step2 thresholds, verbatim port), warm-up 50 bars")
    say(f" moments fire on bar CLOSE (no look-ahead) | travel windows {args.windows} min | "
        f"fish = favourable >= {args.fish_need:.2f} pts (cost {cost:.2f})")
    say(f" session filter: {args.session} Budapest | NOT the 30-judge panel (that is rung 2) -- informs, never switches")
    say("=" * 100)

    trades, err = read_day_trades(args.date)
    if err:
        say(f" [no tape] {err}")
        return 0
    say(f" tape: {len(trades)} trade prints")

    real, real_err = read_decisions(args.date)
    if real:
        conf = sum(1 for r in real if r[3] >= 50)
        say(f" the REAL robot that day (M5, full panel): {len(real)} signals, {conf} of them confident (>=50%) -- for scale, not for matching")
    else:
        say(f" real-robot context: {real_err}")

    h0, m0 = (int(x) for x in args.session.split("-")[0].split(":"))
    h1, m1 = (int(x) for x in args.session.split("-")[1].split(":"))
    from datetime import time as dtime
    session = (dtime(h0, m0), dtime(h1, m1))

    windows = [int(x) for x in args.windows.split(",") if x.strip()]
    w_primary = windows[0]

    # baseline first (the comparison anchor)
    results = {}
    for tf in tfs:
        keys, candles = build_candles(trades, tf)
        moments = detect_moments(keys, candles, tf, args.adx_trend,
                                 session=session)
        results[tf] = {"keys": keys, "candles": candles, "moments": moments}

    base_moments = results[base]["moments"]

    say("")
    hdr = (f" {'TF':>4} {'BARS':>5} {'SCAN':>5} {'MOMENTS':>8} {'U/D':>9} "
           f"{'UNIQUE':>7} {'BASE-MISS':>9} {'HEAD':>7} "
           f"{'U-FAV':>7} {'U-FISH%':>8}  VERDICT")
    say(hdr)
    say(" " + "-" * (len(hdr) - 1))

    detail = []
    for tf in tfs:
        r = results[tf]
        keys, candles, moments = r["keys"], r["candles"], r["moments"]
        n_bars = len(candles)
        scannable = max(0, n_bars - 50)
        label = f"M{tf}"
        if scannable <= 0:
            say(f" {label:>4} {n_bars:>5} {scannable:>5} {'-':>8} {'-':>9} "
                f"{'-':>7} {'-':>9} {'-':>7} {'-':>7} {'-':>8}  "
                f"WARM-UP ONLY ({n_bars} bars < 50 -- the day is too short at this clock)")
            continue
        n_up = sum(1 for m in moments if m["dir"] == "UP")
        n_dn = len(moments) - n_up
        if tf == base:
            say(f" {label:>4} {n_bars:>5} {scannable:>5} {len(moments):>8} {n_up}/{n_dn:>7} "
                f"{'base':>7} {'base':>9} {'base':>7} {'base':>7} {'base':>8}  BASE (robot's home)")
            continue
        matched, unique_x, missed_b = match_against_baseline(moments, base_moments, args.match_min)
        heads = [p["head"] for p in matched]
        head_med = statistics.median(heads) if heads else None
        times = [dt for dt, _ in trades]
        ust = travel_stats(trades, times, unique_x, w_primary, args.fish_need)
        n_u = len(unique_x)
        u_fav = f"{ust['fav_med']:+.2f}" if ust["fav_med"] is not None else "-"
        u_fish = f"{ust['fish_rate']:.0f}%" if ust["fish_rate"] is not None else "-"
        head_s = f"{head_med:+.1f}m" if head_med is not None else "-"
        # verdict (non-binding, said out loud)
        if n_u >= args.min_unique and ust["fish_rate"] is not None:
            if ust["fish_rate"] >= 50.0 and (ust["fav_med"] or 0) >= args.fish_need:
                verdict = "MORE + REAL FISH (rung-2 candidate)"
            else:
                verdict = "MORE + MOSTLY ROCKS"
        elif missed_b and not unique_x and len(moments) < len(base_moments):
            verdict = "FEWER EYES (misses baseline moments)"
        elif not moments and base_moments:
            verdict = "SILENT at this clock (baseline saw some)"
        elif not moments and not base_moments:
            verdict = "SILENT (lake day at both clocks)"
        else:
            verdict = "MIXED -- read the detail"
        say(f" {label:>4} {n_bars:>5} {scannable:>5} {len(moments):>8} {f'{n_up}/{n_dn}':>9} "
            f"{n_u:>7} {len(missed_b):>9} {head_s:>7} {u_fav:>7} {u_fish:>8}  {verdict}")
        detail.append((tf, moments, matched, unique_x, missed_b, ust))

    # per-TF detail block
    say("")
    say(" DETAIL (travel in pts after the moment's bar close; fish windows "
        + "/".join(str(w) for w in windows) + " min):")

    def fmt(v, spec):
        return format(v, spec) if v is not None else "-"

    def fmtp(v):
        return f"{v:.0f}%" if v is not None else "-"

    times = [dt for dt, _ in trades]
    for tf, moments, matched, unique_x, missed_b, ust in detail:
        allst = travel_stats(trades, times, moments, w_primary, args.fish_need)
        extra_parts = []
        for w in windows[1:]:
            st = travel_stats(trades, times, unique_x, w, args.fish_need)
            extra_parts.append(
                f"w{w}: fav {fmt(st['fav_med'], '+.2f')}, fish {fmtp(st['fish_rate'])}")
        say(f"   M{tf}: all moments fav/adv {fmt(allst['fav_med'], '+.2f')}/{fmt(allst['adv_med'], '+.2f')} "
            f"(fish {fmtp(allst['fish_rate'])}) | "
            f"unique-to-M{tf}: {len(unique_x)} moments, fav/adv {fmt(ust['fav_med'], '+.2f')}/{fmt(ust['adv_med'], '+.2f')} "
            f"(fish {fmtp(ust['fish_rate'])}, {ust['no_window']} no-window)"
            f"{' | ' + ' | '.join(extra_parts) if extra_parts else ''} | "
            f"baseline moments M{tf} missed: {len(missed_b)}")

    say("")
    say(" how to read it:")
    say(f"   MOMENTS  = direction changes the robot's own trend brain would catch at that clock (TREND regime only).")
    say(f"   UNIQUE   = moments seen at M{base} NEVER (no same-direction baseline moment within +/-{args.match_min} min).")
    say("   HEAD     = median (baseline time - this TF's time); positive = this clock shouted FIRST.")
    say(f"   U-FAV/U-FISH = median favourable travel and fish-rate of the UNIQUE moments over {w_primary} min --")
    say(f"   the question 'more fish or more rocks?' is answered in THESE two columns, after cost {cost:.2f} pts.")
    say("   BASE-MISS = baseline moments this clock never saw (coarser clocks go blind -- honesty column).")
    say("   warm-up eats the first 50 bars of the day at every clock (that is the robot's own rule, not the lens').")
    say(" informs, never switches: rung 2 (full judge re-vote) decides any real TF change, then a design review.")

    out_path = os.path.join("data", f"tf_lens_{args.date}.txt")
    try:
        os.makedirs("data", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        say(f" [saved] {out_path}")
    except OSError as e:
        say(f" [could not save {out_path}: {e}]")
    return 0


def main():
    ap = argparse.ArgumentParser(description="timeframe lens (TF A/B rung 1)")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--tfs", default="2,3,8,10",
                    help="timeframes to test, e.g. 2,3,8,10 (baseline is added automatically)")
    ap.add_argument("--baseline", type=int, default=5)
    ap.add_argument("--adx-trend", type=float, default=25.0,
                    help="robot's TREND_ADX_THRESHOLD (step2 default 25)")
    ap.add_argument("--windows", default="6,9",
                    help="travel windows in minutes after the moment (first = primary)")
    ap.add_argument("--fish-need", type=float, default=None,
                    help="pts of favourable travel that count as a fish (default 2 x cost)")
    ap.add_argument("--cost", type=float, default=float(os.environ.get("AUDIT_SPREAD", "0.50")))
    ap.add_argument("--match-min", type=float, default=30.0,
                    help="same-direction moments within +/- this many minutes count as the same fish")
    ap.add_argument("--min-unique", type=int, default=3)
    ap.add_argument("--session", default="08:00-23:00")
    args = ap.parse_args()
    if args.fish_need is None:
        args.fish_need = 2.0 * args.cost
    lines = []
    run(args, lines)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
