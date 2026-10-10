#!/usr/bin/env python3
"""flow_probe.py -- the SPY'S NOTEBOOK (flow layer, rung 1: the print side).

The design question (owner, 2026-10-11): keep the judges receiving market info
between bells and let them PREPARE, voting only at the bell?  Rung 1 of the
answer: a tiny always-awake accumulator over the trade tape -- tick-rule
signed CVD, tape-TIME bucketed -- that leans 'buyers are hitting harder' or
'sellers are hitting harder' (fast EMA of one-sidedness) and must HOLD the
lean for a full minute before it counts (persistence/hysteresis).  It never trades, never touches the committee: it prepares.
(OBI/OFI/iceberg pages are rung 2 -- they need the L2/L3 recordings.)

What it measures on a recorded day:
  WARN  - did a sustained flow tilt light up BEFORE the M3 fish moments, same
          direction, and how many minutes early?  (the A2 trigger's eyes)
  NOISE - how many sustained tilts all day had NO same-direction fish within
          10 minutes?  (the A2 trigger's future spam rate -- honest column)
  MOVE  - (v1.1) leans judged against the TAPE itself: does a >=1.0 pt
          same-direction move follow within 6 min, vs the random-anchor
          baseline?  The LIFT number is the spy's real knowledge: 1.0x =
          knows nothing, >= 1.5x = worth building on.  Overnight/thin leans
          are excluded from judging (the robot never trades them).

Tape-TIME bucketing everywhere -> the whole notebook is replayable and
twin-gateable (a live day and a replayed day must produce identical pages).
informs, never switches.  Writes data/flow_probe_<date>.txt.

Usage (git bash, project root, next to tf_lens.py):
    python flow_probe.py --date 2026-10-09
"""
import argparse
import bisect
import csv
import glob
import gzip
import os
import statistics
from datetime import datetime, time as dtime, timedelta, timezone

from tf_lens import BUDA, build_candles, detect_moments, on_day, parse_ts


# ------------------------------------------------------------------ the reader
def read_day_flow(date_str):
    """The lens/audit reader verbatim (same files, same ',Last,' prefilter,
    same (dt, price, size) dedupe key, same bounds) -- but keeps SIZE."""
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
    for live in ("ticks.csv", "data/ticks.csv"):
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
                    if not line or line[0] == "t":
                        continue
                    if line[:10] != date_str or ",Last," not in line:
                        continue
                    try:
                        parts = next(csv.reader([line]))
                    except Exception:
                        continue
                    if len(parts) < 6 or parts[1] not in ("Last", "Trade", "trade"):
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
                    if key in seen:
                        continue
                    seen.add(key)
                    trades.append((dt, price, size))
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
                  + (f" | first data line: {sample!r}" if sample else ""))


# ------------------------------------------------------------- the accumulator
def sign_prints(trades):
    """Lee-Ready tick rule: uptick=+1 (buyer hit), downtick=-1 (seller hit),
    flat repeats the last sign.  First print: 0 (unknown)."""
    signs, prev, last = [], None, 0
    for _, p, _sz in trades:
        if prev is None:
            s = 0
        elif p > prev:
            s = 1
        elif p < prev:
            s = -1
        else:
            s = last
        signs.append(s)
        last = s
        prev = p
    return signs


def build_buckets(trades, signs, bucket_sec):
    """Tape-TIME buckets (epoch-aligned, never wall clock): cvd, vol, n."""
    out, cur = [], None
    for (dt, _p, sz), s in zip(trades, signs):
        k = int(dt.timestamp() // bucket_sec) * bucket_sec
        if cur is None or cur["k"] != k:
            if cur:
                out.append(cur)
            cur = {"k": k, "cvd": 0.0, "vol": 0.0, "n": 0}
        cur["cvd"] += s * sz
        cur["vol"] += sz
        cur["n"] += 1
    if cur:
        out.append(cur)
    return out


def episodes(buckets, dead_frac=0.20, fast=3, tilt=0.25, hold=6):
    """Sustained flow leans.  rate = cvd/vol per LIVE bucket (vol >=
    dead_frac * median vol).  lean score = EMA(fast) of rate; a run counts
    when |score| >= tilt on `hold` consecutive live buckets of the SAME sign
    (fast EMA for sensitivity, hold for hysteresis -- a level shift stays
    leaned, a one-bucket spike never counts).  Dead buckets are transparent:
    they neither extend nor break a run.  Returns [{'dir','start','end'}]
    with epoch-second times."""
    if not buckets:
        return []
    med = statistics.median([b["vol"] for b in buckets])
    floor = med * dead_frac
    af = 2.0 / (fast + 1.0)
    ef = None
    run_sign, run_len, run_start = 0, 0, None
    cur_ep = None
    eps = []
    last_t = None
    for b in buckets:
        if b["vol"] <= 0 or b["vol"] < floor:
            continue                                   # dead: transparent
        rate = b["cvd"] / b["vol"]
        ef = rate if ef is None else af * rate + (1 - af) * ef
        t = b["k"]
        s = 1 if ef >= tilt else (-1 if ef <= -tilt else 0)
        if s == 0:
            if cur_ep and last_t is not None:
                cur_ep["end"] = last_t
                eps.append(cur_ep)
                cur_ep = None
            run_sign, run_len, run_start = 0, 0, None
        else:
            if run_sign == s:
                run_len += 1
            else:
                run_sign, run_len, run_start = s, 1, t
            if run_len >= hold and cur_ep is None:
                cur_ep = {"dir": "UP" if s > 0 else "DOWN",
                          "start": run_start, "end": t}
            elif cur_ep is not None and last_t is not None and \
                    cur_ep["dir"] != ("UP" if s > 0 else "DOWN"):
                cur_ep["end"] = last_t
                eps.append(cur_ep)
                cur_ep = None
                run_sign, run_len, run_start = s, 1, t
        last_t = t
    if cur_ep and last_t is not None:
        cur_ep["end"] = last_t
        eps.append(cur_ep)
    return eps


# ----------------------------------------------------------------- the matching
def warn_moments(moments, eps, warn_before_min=5.0):
    """For each fish moment: a same-dir episode active in [t-warn_before, t]?"""
    res = []
    for m in moments:
        t0 = m["t"] - timedelta(minutes=warn_before_min)
        same = [e for e in eps if e["dir"] == m["dir"]
                and e["start"] <= m["t"].timestamp() and e["end"] >= t0.timestamp()]
        wrong = [e for e in eps if e["dir"] != m["dir"]
                 and e["start"] <= m["t"].timestamp() and e["end"] >= t0.timestamp()]
        if same:
            first = min(same, key=lambda e: e["start"])
            head = (m["t"].timestamp() - first["start"]) / 60.0
            res.append({"m": m, "status": "WARNED", "head": head})
        elif wrong:
            res.append({"m": m, "status": "CONFUSED", "head": None})
        else:
            res.append({"m": m, "status": "quiet", "head": None})
    return res


def false_alarms(eps, moments_by_tf, within_min=10.0):
    """An episode is justified if a same-dir moment (any listed tf) fires
    within [start, start + within_min].  Else it is a false alarm."""
    allm = [m for ms in moments_by_tf.values() for m in ms]
    just, false = [], []
    for e in eps:
        s0, s1 = e["start"], e["start"] + within_min * 60.0
        hit = any(m["dir"] == e["dir"] and s0 <= m["t"].timestamp() <= s1
                  for m in allm)
        (just if hit else false).append(e)
    return just, false


def hhmm(epoch_sec):
    return datetime.fromtimestamp(epoch_sec, tz=timezone.utc)\
        .astimezone(BUDA).strftime("%H:%M:%S")


def in_session(epoch_sec, session):
    t = datetime.fromtimestamp(epoch_sec, tz=timezone.utc).astimezone(BUDA).time()
    return session[0] <= t <= session[1]


def move_after(times, prices, t_epoch, direction, horizon_min, need):
    """Did price travel >= need pts in the lean's direction within horizon,
    measured from the last print at/just before the lean's start?  True/False."""
    t0 = datetime.fromtimestamp(t_epoch, tz=timezone.utc)
    i = bisect.bisect_right(times, t0) - 1
    if i < 0:
        return None
    ref = prices[i]
    j = bisect.bisect_right(times, t0 + timedelta(minutes=horizon_min))
    seg = prices[i + 1:j]
    if not seg:
        return False
    fav = (max(seg) - ref) if direction == "UP" else (ref - min(seg))
    return fav >= need


def baseline_move_rate(times, prices, session, horizon_min, need, step_sec=30):
    """P(a >=need same-dir move within horizon) from RANDOM in-session anchors
    in a RANDOM direction -- the honest yardstick for the lift number."""
    if not times:
        return None
    t_lo, t_hi = times[0].timestamp(), times[-1].timestamp()
    ok = tot = 0
    a = t_lo
    while a <= t_hi:
        if in_session(a, session):
            for d in ("UP", "DOWN"):
                r = move_after(times, prices, a, d, horizon_min, need)
                if r is not None:
                    tot += 1
                    ok += 1 if r else 0
        a += step_sec
    return (ok / tot) if tot else None


# ------------------------------------------------------------------------ main
def run(args, lines):
    say = lines.append
    h0, m0 = (int(x) for x in args.session.split("-")[0].split(":"))
    h1, m1 = (int(x) for x in args.session.split("-")[1].split(":"))
    session = (dtime(h0, m0), dtime(h1, m1))
    jfs = [int(x) for x in args.justify_tfs.split(",") if x.strip()]

    say("=" * 100)
    say(f" FLOW PROBE (the spy's notebook, rung 1 - print side) - {args.date}")
    say(f" tick-rule CVD in {args.bucket}s tape-time buckets | lean = EMA{args.fast} of cvd-rate,"
        f" |lean| >= {args.tilt:.2f} held {args.hold} live buckets (~{args.hold * args.bucket}s)"
        f" | fish = M{args.moment_tf} moments | warns look back {args.warn_before:.0f} min")
    say(" informs, never switches")
    say("=" * 100)

    trades, err = read_day_flow(args.date)
    if err:
        say(f" [no tape] {err}")
        return 0
    say(f" tape: {len(trades)} prints")

    signs = sign_prints(trades)
    buckets = build_buckets(trades, signs, args.bucket)
    live_vol = [b["vol"] for b in buckets]
    med = statistics.median(live_vol) if live_vol else 0.0
    live_n = sum(1 for v in live_vol if v >= max(med * args.dead_frac, 1e-9) and v > 0)
    say(f" buckets: {len(buckets)} total | {live_n} live (vol >= {args.dead_frac:.0%} of"
        f" median {med:.1f} lots) | dead buckets are transparent to runs")
    if live_n < 10:
        say(" [too quiet] fewer than 10 live buckets - the spy cannot lean on this tape.")
        return 0

    all_eps = episodes(buckets, dead_frac=args.dead_frac, fast=args.fast,
                       tilt=args.tilt, hold=args.hold)
    eps = [e for e in all_eps if in_session(e["start"], session)]
    overnight = len(all_eps) - len(eps)

    # fish moments: primary tf for the table, all justify-tfs for noise judging
    two = [(dt, p) for dt, p, _ in trades]
    moments = {}
    for tf in sorted(set([args.moment_tf] + jfs)):
        keys, candles = build_candles(two, tf)
        moments[tf] = detect_moments(keys, candles, tf, session=session)
    fish = moments[args.moment_tf]

    say(f" fish: {len(fish)} M{args.moment_tf} moments | spy leans: {len(all_eps)} sustained"
        f" ({len(eps)} in session, {overnight} overnight/thin excluded from judging)")
    say("")
    say(f" THE FISH vs THE SPY (M{args.moment_tf} moments; spy tilt same direction in the"
        f" {args.warn_before:.0f} min before the bell):")
    if not fish:
        say("   (no fish this day at this clock)")
    for r in warn_moments(fish, eps, args.warn_before):
        lb = r["m"]["t"].astimezone(BUDA)
        if r["status"] == "WARNED":
            say(f"   {lb.strftime('%H:%M')} {r['m']['dir']:>4} adx {r['m']['adx']:4.1f}"
                f" | spy: WARNED {r['head']:+.1f} min early")
        elif r["status"] == "CONFUSED":
            say(f"   {lb.strftime('%H:%M')} {r['m']['dir']:>4} adx {r['m']['adx']:4.1f}"
                f" | spy: CONFUSED (leaning the OTHER way - read as a warning sign)")
        else:
            say(f"   {lb.strftime('%H:%M')} {r['m']['dir']:>4} adx {r['m']['adx']:4.1f}"
                f" | spy: quiet")

    just, false = false_alarms(eps, moments, args.within)
    say("")
    say(" THE SPY'S DAY (every sustained tilt, judged):")
    if not eps:
        say("   (the spy never leaned hard enough today - honest zero)")
    for e in eps:
        verdict = "fish <= " + f"{args.within:.0f} min: YES" if e in just else \
                  f"fish <= {args.within:.0f} min: NO (false alarm)"
        say(f"   {hhmm(e['start'])} {e['dir']:>4} held {(e['end'] - e['start']) / 60.0:.1f} min"
            f" -> {verdict}")

    say("")
    say(f" THE MOVE VIEW (v1.1: leans judged against the TAPE, not the lagging bell):")
    times = [dt for dt, _p, _s in trades]
    prices = [p for _dt, p, _s in trades]
    jm = [e for e in eps if move_after(times, prices, e["start"], e["dir"],
                                       args.move_window, args.move_need) is True]
    rate = (len(jm) / len(eps)) if eps else None
    base = baseline_move_rate(times, prices, session, args.move_window, args.move_need)
    if rate is not None:
        say(f"   leans followed by >= {args.move_need:.1f} pts same-direction travel within"
            f" {args.move_window:.0f} min: {len(jm)}/{len(eps)} ({rate * 100:.0f}%)")
    else:
        say("   (no in-session leans to judge)")
    if base is not None:
        say(f"   random-anchor baseline (both directions): {base * 100:.0f}%"
            + (f" -> LIFT {rate / base:.1f}x" if rate is not None else "")
            + "  (1.0x = the spy knows nothing; >= 1.5x = worth building on)")

    warned = [r for r in warn_moments(fish, eps, args.warn_before) if r["status"] == "WARNED"]
    confused = [r for r in warn_moments(fish, eps, args.warn_before) if r["status"] == "CONFUSED"]
    heads = [r["head"] for r in warned]
    say("")
    say(f" WARN : {len(warned)}/{len(fish)} fish warned"
        + (f" | median {statistics.median(heads):+.1f} min early" if heads else "")
        + f" | confused {len(confused)} | quiet {len(fish) - len(warned) - len(confused)}")
    say(f" NOISE: {len(eps)} tilts | {len(just)} justified | {len(false)} false alarms"
        f" ({(len(false) / len(eps) * 100):.0f}% of leans cried wolf)" if eps else
        " NOISE: no tilts")
    say("")
    say(" how to read it:")
    say("   WARN  = the flow leaned the fish's way BEFORE the bell: the A2 burst")
    say("   trigger's future eyes.  Head start is the honest warning time.")
    say("   NOISE = leans with no fish within " + f"{args.within:.0f}" + " min: the trigger's")
    say("   future spam rate.  A usable trigger needs WARN high AND NOISE low.")
    say("   CONFUSED = flow leaning against the fish - treat as a stop sign, not noise.")
    say("   rung 1 = prints only (CVD).  OBI/OFI/iceberg pages = rung 2 (L2/L3).")
    say(" informs, never switches.")

    out_path = os.path.join("data", f"flow_probe_{args.date}.txt")
    try:
        os.makedirs("data", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        say(f" [saved] {out_path}")
    except OSError as e:
        say(f" [could not save {out_path}: {e}]")
    return 0


def main():
    ap = argparse.ArgumentParser(description="flow probe (spy's notebook, rung 1)")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--bucket", type=int, default=10)
    ap.add_argument("--fast", type=int, default=3)
    ap.add_argument("--tilt", type=float, default=0.25)
    ap.add_argument("--hold", type=int, default=6)
    ap.add_argument("--dead-frac", type=float, default=0.20)
    ap.add_argument("--moment-tf", type=int, default=3)
    ap.add_argument("--justify-tfs", default="2,3")
    ap.add_argument("--warn-before", type=float, default=5.0)
    ap.add_argument("--within", type=float, default=10.0)
    ap.add_argument("--move-need", type=float, default=1.0)
    ap.add_argument("--move-window", type=float, default=6.0)
    ap.add_argument("--session", default="08:00-23:00")
    args = ap.parse_args()
    lines = []
    run(args, lines)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
