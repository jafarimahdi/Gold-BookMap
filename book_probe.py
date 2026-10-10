#!/usr/bin/env python3
"""book_probe.py -- the ORDER-BOOK SPY (flow layer, rung 2: the book side).

The MBO journal (mbo.csv + data/archive/mbo_*.csv.gz) records the lifecycle of
every order: BID_NEW / ASK_NEW / REPLACE (new price+size) / CANCEL (price 0 --
attributed via the order's last known state).  From it, in TAPE-TIME buckets:

  eOFI = [(bid adds - bid removes) - (ask adds - ask removes)] / total size
         of book events in the bucket   ->  one-sidedness in [-1, +1]

Positive = the book is being BUILT on the bid side (or torn down on the ask);
negative = the reverse.  The lean/persistence machinery, the session filter,
the MOVE VIEW and the random-anchor LIFT are imported VERBATIM from
flow_probe.py (rung 1) so the print spy and the book spy are directly
comparable, number for number.

  WARN  - did a sustained book lean light up before the M3 fish moments?
  MOVE  - leans vs the tape itself: >=1 pt same-dir travel within 6 min,
          against the random-anchor baseline -> LIFT (1.0x = knows nothing).
  HONESTY - unresolved cancels (orders whose birth predates the recording)
          are counted and reported, never guessed.

informs, never switches.  Writes data/book_probe_<date>.txt.
Needs tf_lens.py and flow_probe.py alongside.

Usage (git bash, project root):
    python book_probe.py --date 2026-10-09
"""
import argparse
import bisect
import csv
import glob
import gzip
import os
import statistics
from datetime import datetime, time as dtime, timezone

from tf_lens import BUDA, build_candles, detect_moments, on_day, parse_ts
from flow_probe import (baseline_move_rate, episodes, in_session, move_after,
                        warn_moments)


# ------------------------------------------------------------------ the reader
def read_day_mbo(date_str):
    """MBO events for that Budapest day: archive chunks + the live file,
    (dt, event_type, order_id) dedupe across files.  Same layout as the
    ticks reader: newest chunks in data/archive/, live tail in ./mbo.csv."""
    files, seen_paths = [], set()

    def add(path):
        rp = os.path.realpath(path)
        if rp not in seen_paths:
            seen_paths.add(rp)
            files.append(path)

    compact = date_str.replace("-", "")
    for d in ("data/archive", "data"):
        for pat in (f"mbo_{compact}_*.csv", f"mbo_{compact}_*.csv.gz"):
            for p in sorted(glob.glob(os.path.join(d, pat))):
                add(p)
    if os.path.exists("mbo.csv"):
        add("mbo.csv")
    if not files:
        return None, "no mbo.csv and no rotated chunks for that date"
    events, seen = [], set()
    for path in files:
        op = gzip.open if path.endswith(".gz") else open
        try:
            with op(path, "rt", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not line or line[0] == "t":          # header
                        continue
                    try:
                        parts = next(csv.reader([line]))
                    except Exception:
                        continue
                    if len(parts) < 5 or parts[1] not in \
                            ("BID_NEW", "ASK_NEW", "REPLACE", "CANCEL"):
                        continue
                    dt = parse_ts(parts[0])
                    if dt is None or not on_day(dt, date_str):
                        continue
                    try:
                        price = float(parts[3])
                        size = float(parts[4])
                    except ValueError:
                        continue
                    key = (dt, parts[1], parts[2])
                    if key in seen:                        # same event twice
                        continue
                    seen.add(key)
                    events.append((dt, parts[1], parts[2], price, size))
        except OSError:
            continue
    events.sort(key=lambda x: x[0])
    if events:
        return events, None
    return None, f"mbo events found in {files} but none on {date_str}"


# ------------------------------------------------- the tracker + eOFI buckets
def build_eofi_buckets(events, bucket_sec, ref_price=None, touch_band=None):
    """Track every order's last known (side, size, price); attribute CANCELs
    and REPLACE-removals to the right side.  Sides: +1 bid, -1 ask, 0 unknown
    (order born before the recording -> contributes NOTHING, is counted).
    eOFI sign: (bid adds - bid removes) - (ask adds - ask removes).
    v1.1 NEAR-TOUCH page: only events within touch_band pts of ref_price(dt)
    (the last trade at/before the event) contribute -- the Cont et al. queue
    zone.  Far orders are still tracked (so their later cancels resolve) but
    contribute nothing.  ref_price: callable dt -> price or None.
    Buckets: same shape as the print spy's ({k, cvd, vol, n}) so
    flow_probe.episodes() runs on them verbatim.  Returns (buckets, stats)."""
    orders = {}                      # order_id -> [side(+1/-1/0), size, price]
    buckets, cur = [], None
    st = {"tracked": 0, "unresolved_cancel": 0, "unknown_replace": 0,
          "bid_add": 0.0, "ask_add": 0.0, "bid_rem": 0.0, "ask_rem": 0.0,
          "near": 0, "far": 0, "no_ref": 0}

    def bucket(k):
        nonlocal cur
        if cur is None or cur["k"] != k:
            if cur:
                buckets.append(cur)
            cur = {"k": k, "cvd": 0.0, "vol": 0.0, "n": 0}
        return cur

    def near_status(dt, price):
        """'on' = counts toward eOFI | 'far' = tracked, not counted |
        'no-ref' = before the first print (tracked, not counted)."""
        if ref_price is None or touch_band is None:
            return "on"                                # v1.0 whole-book page
        ref = ref_price(dt)
        if ref is None:
            return "no-ref"
        return "on" if abs(price - ref) <= touch_band else "far"

    def account(s):
        if s == "on":
            st["near"] += 1
            return True
        if s == "far":
            st["far"] += 1
        else:
            st["no_ref"] += 1
        return False

    for dt, etype, oid, price, size in events:
        k = int(dt.timestamp() // bucket_sec) * bucket_sec
        b = bucket(k)
        b["n"] += 1
        if etype == "BID_NEW":
            orders[oid] = [1, size, price]
            st["tracked"] += 1
            if account(near_status(dt, price)):
                st["bid_add"] += size
                b["cvd"] += size
                b["vol"] += size
        elif etype == "ASK_NEW":
            orders[oid] = [-1, size, price]
            st["tracked"] += 1
            if account(near_status(dt, price)):
                st["ask_add"] += size
                b["cvd"] -= size
                b["vol"] += size
        elif etype == "REPLACE":
            old = orders.get(oid)
            if old is None:                      # birth predates the recording
                st["unknown_replace"] += 1
                orders[oid] = [0, max(size, 0.0), price]  # side unknown: neutral
                continue
            if old[0] != 0 and old[1] > 0 and account(near_status(dt, old[2])):
                if old[0] > 0:                    # remove the old state
                    st["bid_rem"] += old[1]
                    b["cvd"] -= old[1]
                else:
                    st["ask_rem"] += old[1]
                    b["cvd"] += old[1]
                b["vol"] += old[1]
            if size > 0:                         # add the new state
                old[1] = size                    # side persists (0 stays 0)
                if old[0] != 0 and account(near_status(dt, price)):
                    if old[0] > 0:
                        st["bid_add"] += size
                        b["cvd"] += size
                    elif old[0] < 0:
                        st["ask_add"] += size
                        b["cvd"] -= size
                    b["vol"] += size
                old[2] = price
            else:
                del orders[oid]                  # replaced to nothing = gone
        elif etype == "CANCEL":
            old = orders.pop(oid, None)
            if old is None or old[0] == 0:       # never saw (or never knew) its birth
                st["unresolved_cancel"] += 1
                continue
            if old[1] > 0 and account(near_status(dt, old[2])):
                if old[0] > 0:
                    st["bid_rem"] += old[1]
                    b["cvd"] -= old[1]
                else:
                    st["ask_rem"] += old[1]
                    b["cvd"] += old[1]
                b["vol"] += old[1]
    if cur:
        buckets.append(cur)
    return buckets, st


# ------------------------------------------------------------------------ main
def run(args, lines):
    say = lines.append
    h0, m0 = (int(x) for x in args.session.split("-")[0].split(":"))
    h1, m1 = (int(x) for x in args.session.split("-")[1].split(":"))
    session = (dtime(h0, m0), dtime(h1, m1))

    say("=" * 100)
    say(f" BOOK PROBE (the order-book spy, rung 2 - book side, NEAR-TOUCH page v1.1) - {args.date}")
    say(f" eOFI in {args.bucket}s tape-time buckets | lean = EMA{args.fast} of eOFI,"
        f" |lean| >= {args.tilt:.2f} held {args.hold} live buckets (~{args.hold * args.bucket}s)"
        f" | fish = M{args.moment_tf} moments | warns look back {args.warn_before:.0f} min")
    say(" informs, never switches")
    say("=" * 100)

    events, err = read_day_mbo(args.date)
    if err:
        say(f" [no mbo tape] {err}")
        return 0
    say(f" mbo events: {len(events)} (BID_NEW/ASK_NEW/REPLACE/CANCEL)")

    # the print tape loads FIRST: it is the touch reference for the v1.1 page
    from tf_lens import read_day_trades
    trades, terr = read_day_trades(args.date)
    if terr:
        say(f" [no print tape for touch reference] {terr}")
        return 0
    p_times = [dt for dt, _p in trades]
    p_prices = [p for _dt, p in trades]

    def ref_price(dt):
        i = bisect.bisect_right(p_times, dt) - 1
        return p_prices[i] if i >= 0 else None

    band = args.touch_ticks * args.tick
    say(f" page: NEAR-TOUCH v1.1 - only order events within {band:.1f} pts"
        f" ({args.touch_ticks} ticks) of the last trade count toward eOFI")

    buckets, st = build_eofi_buckets(events, args.bucket,
                                     ref_price=ref_price, touch_band=band)
    unresolved_pct = (100.0 * st["unresolved_cancel"] /
                      max(st["unresolved_cancel"] + st["tracked"], 1))
    say(f" orders tracked: {st['tracked']} | unresolved cancels: {st['unresolved_cancel']}"
        f" ({unresolved_pct:.1f}% - orders born before the recording; excluded, never guessed)")
    say(f" events counted (near the touch): {st['near']} | far/deep (tracked, not counted):"
        f" {st['far']} | before first print: {st['no_ref']}")
    say(f" near-touch size added  bid {st['bid_add']:.0f} / ask {st['ask_add']:.0f} lots |"
        f" removed bid {st['bid_rem']:.0f} / ask {st['ask_rem']:.0f} lots")

    live_vol = [b["vol"] for b in buckets]
    med = statistics.median(live_vol) if live_vol else 0.0
    live_n = sum(1 for v in live_vol if v >= max(med * args.dead_frac, 1e-9) and v > 0)
    say(f" buckets: {len(buckets)} total | {live_n} live (vol >= {args.dead_frac:.0%} of"
        f" median {med:.1f} lots)")
    if live_n < 10:
        say(" [too quiet] fewer than 10 live buckets - the book spy cannot lean on this tape.")
        return 0

    # the honest scale of one-sidedness on this page (so the threshold is
    # never a mystery): |cvd/vol| over live buckets
    rates = sorted(abs(b["cvd"] / b["vol"]) for b in buckets
                   if b["vol"] > 0 and b["vol"] >= max(med * args.dead_frac, 1e-9))
    if rates:
        p90 = rates[min(int(0.90 * len(rates)), len(rates) - 1)]
        say(f" one-sidedness scale (live buckets): median {rates[len(rates) // 2]:.3f}"
            f" | p90 {p90:.3f} | max {rates[-1]:.3f}  (lean threshold {args.tilt:.2f})")

    all_eps = episodes(buckets, dead_frac=args.dead_frac, fast=args.fast,
                       tilt=args.tilt, hold=args.hold)
    eps = [e for e in all_eps if in_session(e["start"], session)]
    overnight = len(all_eps) - len(eps)

    # fish moments (M3) from the PRINT tape, as in rung 1
    two = [(dt, p) for dt, p in trades]
    keys, candles = build_candles(two, args.moment_tf)
    h0s, m0s = session
    fish = detect_moments(keys, candles, args.moment_tf, session=session)

    say(f" fish: {len(fish)} M{args.moment_tf} moments | book leans: {len(all_eps)} sustained"
        f" ({len(eps)} in session, {overnight} overnight/thin excluded)")
    say("")
    say(f" THE FISH vs THE BOOK SPY (M{args.moment_tf} moments; book lean same direction in"
        f" the {args.warn_before:.0f} min before the bell):")
    if not fish:
        say("   (no fish this day at this clock)")
    for r in warn_moments(fish, eps, args.warn_before):
        lb = r["m"]["t"].astimezone(BUDA)
        if r["status"] == "WARNED":
            say(f"   {lb.strftime('%H:%M')} {r['m']['dir']:>4} adx {r['m']['adx']:4.1f}"
                f" | book: WARNED {r['head']:+.1f} min early")
        elif r["status"] == "CONFUSED":
            say(f"   {lb.strftime('%H:%M')} {r['m']['dir']:>4} adx {r['m']['adx']:4.1f}"
                f" | book: CONFUSED (leaning the OTHER way - read as a warning sign)")
        else:
            say(f"   {lb.strftime('%H:%M')} {r['m']['dir']:>4} adx {r['m']['adx']:4.1f}"
                f" | book: quiet")

    say("")
    say(f" THE BOOK SPY'S DAY (in-session leans, judged against the tape):")
    if not eps:
        say("   (the book spy never leaned hard enough today - honest zero)")
    times = [dt for dt, _p in trades]
    prices = [p for _dt, p in trades]
    for e in eps:
        jm = move_after(times, prices, e["start"], e["dir"],
                        args.move_window, args.move_need)
        verdict = ("fish: >= " + f"{args.move_need:.1f} pts within "
                   f"{args.move_window:.0f} min: YES" if jm is True else
                   ("... NO" if jm is False else "... (no data)"))
        say(f"   {datetime.fromtimestamp(e['start'], tz=timezone.utc).astimezone(BUDA).strftime('%H:%M:%S')}"
            f" {e['dir']:>4} held {(e['end'] - e['start']) / 60.0:.1f} min -> {verdict}")

    say("")
    say(f" THE MOVE VIEW (leans vs the TAPE, same yardstick as the print spy):")
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
            + "  (1.0x = knows nothing; >= 1.5x = worth building on)")

    warned = [r for r in warn_moments(fish, eps, args.warn_before) if r["status"] == "WARNED"]
    confused = [r for r in warn_moments(fish, eps, args.warn_before) if r["status"] == "CONFUSED"]
    heads = [r["head"] for r in warned]
    say("")
    say(f" WARN : {len(warned)}/{len(fish)} fish warned"
        + (f" | median {statistics.median(heads):+.1f} min early" if heads else "")
        + f" | confused {len(confused)} | quiet {len(fish) - len(warned) - len(confused)}")
    say(" compare with the PRINT spy (flow_probe) on the same day: which eye is stronger?")
    say("   v1.0 = eOFI page only.  wall-refill/iceberg page = v1.1 (needs wall definitions")
    say("   aligned with the Scout).  top-of-book pressure = possible v1.2.")
    say(" informs, never switches.")

    out_path = os.path.join("data", f"book_probe_{args.date}.txt")
    try:
        os.makedirs("data", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        say(f" [saved] {out_path}")
    except OSError as e:
        say(f" [could not save {out_path}: {e}]")
    return 0


def main():
    ap = argparse.ArgumentParser(description="book probe (order-book spy, rung 2)")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--bucket", type=int, default=10)
    ap.add_argument("--fast", type=int, default=3)
    ap.add_argument("--tilt", type=float, default=0.25)
    ap.add_argument("--hold", type=int, default=6)
    ap.add_argument("--dead-frac", type=float, default=0.20)
    ap.add_argument("--touch-ticks", type=int, default=10)
    ap.add_argument("--tick", type=float, default=0.10)
    ap.add_argument("--moment-tf", type=int, default=3)
    ap.add_argument("--warn-before", type=float, default=5.0)
    ap.add_argument("--move-need", type=float, default=1.0)
    ap.add_argument("--move-window", type=float, default=6.0)
    ap.add_argument("--session", default="08:00-23:00")
    args = ap.parse_args()
    lines = []
    run(args, lines)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
