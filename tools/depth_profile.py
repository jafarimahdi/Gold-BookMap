#!/usr/bin/env python3
"""depth_profile.py - what does a 'big' order actually look like in YOUR book?

WHY THIS EXISTS
  On 2026-09-24 the bridge reported 20 bid levels holding 84 lots in total - about
  4 lots per level - while the settings demanded:
      L3_WHALE_THRESHOLD=100     a wall must be 100 lots
      ABSORPTION_WALL_SIZE=50    a wall must be 50 lots
      SPOOF_SIZE_THRESHOLD=100   a fake must be 100 lots
  A 100-lot wall cannot exist in an 84-lot book, so whale_walls, iceberg,
  spoof_invert and queue_pos never fired once in a full trading day.

  This tool does not guess replacements. It rebuilds the order book from mbo.csv
  and reports what sizes REALLY occur, so the thresholds can be set from evidence.

WHAT IT DOES
  1. reports the event vocabulary actually present in mbo.csv (no assumptions)
  2. replays events to maintain live resting size per price level
  3. samples the book periodically and reports percentiles of:
       - single order size
       - TOTAL resting size at one price level  <- this is what a "wall" means
  4. suggests thresholds from those percentiles and compares them to your .env

USAGE
    python tools/depth_profile.py                    # last 200k events
    python tools/depth_profile.py --rows 500000
    python tools/depth_profile.py --file mbo.csv

Report only. Reads one file. Changes nothing.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Rithmic/Bookmap writes the side into the event name: BID_NEW / ASK_NEW.
# Anything ending in _NEW (or plainly NEW/ADD) is an add.
ADD = {"NEW", "ADD", "INSERT", "PLACE", "A", "SUBMIT", "BID_NEW", "ASK_NEW",
       "BID_ADD", "ASK_ADD"}
DEL = {"CANCEL", "CANCELLED", "REMOVE", "DELETE", "D", "C"}
FILL = {"FILL", "FILLED", "TRADE", "EXEC", "EXECUTE", "T", "F"}
MOD = {"MODIFY", "UPDATE", "REPLACE", "M", "U"}


def pct(vals, p):
    if not vals:
        return 0.0
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round((p / 100.0) * (len(s) - 1)))))
    return s[i]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Measure real order/wall sizes from mbo.csv")
    ap.add_argument("--file", default=None, help="path to mbo.csv (default: project root)")
    ap.add_argument("--rows", type=int, default=200000, help="how many of the LAST rows to read")
    ap.add_argument("--sample-every", type=int, default=250,
                    help="snapshot the book every N events")
    args = ap.parse_args(argv)

    path = Path(args.file) if args.file else (ROOT / "mbo.csv")
    if not path.exists():
        print(f"mbo.csv not found at {path}")
        return 2

    print(f"reading the last {args.rows:,} rows of {path.name} "
          f"({path.stat().st_size/1_000_000:.1f} MB)")

    rows = []
    with path.open("r", encoding="utf-8", errors="replace", newline="") as fh:
        rdr = csv.DictReader(fh)
        if not rdr.fieldnames:
            print("mbo.csv has no header - cannot read")
            return 2
        cols = [c.strip() for c in rdr.fieldnames]
        print(f"columns: {cols}")
        for r in rdr:
            rows.append(r)
            if len(rows) > args.rows:
                rows.pop(0)
    if not rows:
        print("no rows")
        return 2

    # --- 1. what event types really exist ------------------------------------
    key_t = next((c for c in cols if "event" in c.lower() or c.lower() == "type"), None)
    key_p = next((c for c in cols if c.lower() == "price"), None)
    key_s = next((c for c in cols if c.lower() in ("size", "qty", "quantity", "volume")), None)
    key_o = next((c for c in cols if "order" in c.lower() and "id" in c.lower()), None)
    if not (key_t and key_p and key_s):
        print(f"could not identify columns (type={key_t} price={key_p} size={key_s})")
        return 2

    types = Counter((r.get(key_t) or "?").strip().upper() for r in rows)
    print(f"\n--- event vocabulary in your file ({len(rows):,} rows read)")
    for t, c in types.most_common(12):
        bucket = ("ADD" if t in ADD else "CANCEL" if t in DEL else "FILL" if t in FILL
                  else "MODIFY" if t in MOD else "?UNKNOWN")
        print(f"   {t:<14} {c:>9,}   treated as {bucket}")
    unknown = [t for t in types if t not in ADD | DEL | FILL | MOD]
    unknown_n = sum(types[t] for t in unknown)
    unknown_pct = 100.0 * unknown_n / max(len(rows), 1)
    if unknown:
        print(f"   [!] unrecognised types: {unknown} = {unknown_n:,} rows "
              f"({unknown_pct:.1f}% of the file)")

    # --- 2. single order sizes ------------------------------------------------
    sizes = []
    for r in rows:
        try:
            v = abs(float(r.get(key_s) or 0))
        except Exception:
            continue
        if v > 0:
            sizes.append(v)

    # --- 3. replay to get TOTAL resting size per price level -----------------
    sides = Counter()
    book = {}                      # order_id -> (price, size)
    level = defaultdict(float)     # price -> total resting size
    level_totals = []
    for i, r in enumerate(rows):
        t = (r.get(key_t) or "").strip().upper()
        try:
            price = round(float(r.get(key_p) or 0), 2)
            size = abs(float(r.get(key_s) or 0))
        except Exception:
            continue
        oid = (r.get(key_o) or "").strip() if key_o else ""
        if t in ADD or t.endswith("_NEW") or t.endswith("_ADD"):
            if oid:
                book[oid] = (price, size)
            level[price] += size
            sides["BID" if t.startswith("BID") else
                  "ASK" if t.startswith("ASK") else "?"] += 1
        elif t in DEL or t in FILL:
            if oid and oid in book:
                p0, s0 = book.pop(oid)
                level[p0] = max(0.0, level[p0] - s0)
            else:
                level[price] = max(0.0, level[price] - size)
        elif t in MOD:
            if oid and oid in book:
                p0, s0 = book[oid]
                level[p0] = max(0.0, level[p0] - s0)
            book[oid] = (price, size)
            level[price] += size
        if i % max(1, args.sample_every) == 0:
            live = [v for v in level.values() if v > 0]
            level_totals.extend(live)

    print(f"\n--- SINGLE ORDER SIZE (what one participant posts)  n={len(sizes):,}")
    if sizes:
        for p in (50, 75, 90, 95, 99, 99.9):
            print(f"   p{p:<5} {pct(sizes, p):>10.1f} lots")
        print(f"   max    {max(sizes):>10.1f} lots   median {statistics.median(sizes):.1f}")

    print(f"\n--- TOTAL RESTING SIZE AT ONE PRICE (this is what a WALL is)  "
          f"n={len(level_totals):,} samples")
    if level_totals:
        for p in (50, 75, 90, 95, 99, 99.9):
            print(f"   p{p:<5} {pct(level_totals, p):>10.1f} lots")
        print(f"   max    {max(level_totals):>10.1f} lots")

    if sides:
        print(f"\n--- adds by side: " + ", ".join(f"{k}={v:,}" for k, v in sides.most_common()))

    # --- 4. suggestions -------------------------------------------------------
    # HONESTY GUARD: if a meaningful share of events was not understood, the
    # rebuilt book is wrong and any threshold taken from it would be wrong too.
    if unknown_pct > 2.0:
        print("\n" + "=" * 84)
        print("NO SUGGESTIONS - THE REPLAY IS INCOMPLETE")
        print("=" * 84)
        print(f"  {unknown_pct:.1f}% of events ({unknown_n:,} rows) were not understood, so the")
        print(f"  rebuilt book is missing data and every number below it would be wrong.")
        print(f"  Unrecognised: {unknown}")
        print("  Tell me these names and I will teach the replay what they mean.")
        print("=" * 84)
        return 0

    if level_totals:
        whale = round(pct(level_totals, 99), 0)
        absorb = round(pct(level_totals, 90), 0)
        spoof = round(pct(sizes, 99), 0) if sizes else whale
        print("\n" + "=" * 84)
        print("SUGGESTED THRESHOLDS, measured from your own book")
        print("=" * 84)
        print(f"  {'setting':<28}{'yours now':>12}{'measured':>12}   meaning")
        print(f"  {'L3_WHALE_THRESHOLD':<28}{'100':>12}{whale:>12.0f}   "
              f"top 1% of levels = a real wall")
        print(f"  {'ABSORPTION_WALL_SIZE':<28}{'50':>12}{absorb:>12.0f}   "
              f"top 10% of levels = worth watching")
        print(f"  {'SPOOF_SIZE_THRESHOLD':<28}{'100':>12}{spoof:>12.0f}   "
              f"top 1% of single orders")
        print("\n  These are SUGGESTIONS from data, not commands. Nothing was changed.")
        print("  Set them too low and every small order becomes a 'wall' (noise).")
        print("  Set them too high and you get what you have now: total silence.")
        print("  Start at the measured numbers, then watch signal_health.py for a week.")
        if whale >= 100:
            print("\n  [!] measured whale size is >= 100, so the current setting is NOT the")
            print("      reason for the silence. Look at the feed wiring instead.")
    print("=" * 84)
    return 0


if __name__ == "__main__":
    sys.exit(main())
