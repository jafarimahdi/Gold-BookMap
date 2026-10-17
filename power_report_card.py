#!/usr/bin/env python3
"""power_report_card.py — grade POWER v2 from his own memory-book diary.

Read-only. No imports from the robot. Stdlib only. ASCII output only.

Usage (from the repo root, next to data/):
    python power_report_card.py 2026-10-16
    python power_report_card.py 2026-10-16 --verbose
    python power_report_card.py 2026-10-16 --root data/power_v2_memory

What it grades:
  1. Verdict census    - how often UP / DOWN / NEITHER, and WHY (reason codes)
  2. Accuracy card     - every UP/DOWN call marked against forward reference
                         prices at +15 / +30 / +60 minutes (bar N vs N+3/6/12)
  3. Dominance buckets - do his stronger calls do better? (C6-style)
  4. Classroom compare - if data/decisions_log.csv is found: minutes the class
                         was brave (conf >= 45) vs minutes POWER spoke, the
                         co-brave count, and who won their disagreements

Honesty rules: one day = weather, not climate. This tool marks; it never
tunes. Thresholds change only through the one-change law with receipts.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

VOTE_NAMES = ("footprint_delta", "l3_aggr_limit", "cvd_momentum",
              "big_prints", "footprint_levels")
HORIZONS = {"+15m": 3, "+30m": 6, "+60m": 12}  # M5 bars ahead
DOMINANCE_BUCKETS = ((60.0, 70.0), (70.0, 80.0), (80.0, 101.0))
CLASS_BRAVE_CONF = 45.0


def parse_ts(raw) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        ts = datetime.fromisoformat(text)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def load_diary(path: Path):
    """Parse the diary, dedupe within each symbol, and report the inventory.

    The real diaries contain records for MULTIPLE symbols in one day file:
    the primary data symbol (e.g. GCZ6.COMEX@RITHMIC from the BookMap feed,
    with real votes) and trade-side/MT5 twins (e.g. XAUUSD, whose CFD ticks
    carry no aggressor labels -> fail-closed NEITHER every bar). They also
    contain occasional same-symbol duplicate records. We dedupe by
    (symbol, timestamp), keeping the richest record, and return the
    per-symbol inventory so the card grades ONE student at a time.
    """
    parsed = []
    with path.open("r", encoding="utf-8") as stream:
        for line in stream:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("phase", "FINAL")).upper() != "FINAL":
                continue
            ts = parse_ts(row.get("timestamp_utc"))
            if ts is None:
                continue
            result = row.get("result") or {}
            if not isinstance(result, dict):
                continue
            votes = result.get("input_judges") or {}
            parsed.append({
                "ts": ts,
                "symbol": str(row.get("symbol") or "?"),
                "ref": row.get("reference_price"),
                "direction": str(result.get("direction", "NEITHER")).upper(),
                "reason": str(result.get("reason_code", "UNKNOWN")),
                "up": result.get("up_power_pct"),
                "down": result.get("down_power_pct"),
                "activity": result.get("activity"),
                "coverage": result.get("coverage"),
                "votes": votes,
            })
    parsed.sort(key=lambda r: (r["ts"], r["symbol"]))

    def richness(rec) -> tuple:
        live_votes = sum(1 for name in VOTE_NAMES
                         if isinstance(rec["votes"].get(name), dict))
        ref_ok = 1 if (num(rec["ref"]) or 0) > 0 else 0
        return (live_votes, ref_ok, num(rec["activity"]) or 0.0)

    best: dict = {}
    dropped = 0
    for rec in parsed:
        key = (rec["symbol"], rec["ts"])
        if key not in best:
            best[key] = rec
        elif richness(rec) > richness(best[key]):
            best[key] = rec
            dropped += 1
        else:
            dropped += 1
    all_recs = [best[k] for k in sorted(best)]
    inventory: dict = {}
    for rec in all_recs:
        inventory[rec["symbol"]] = inventory.get(rec["symbol"], 0) + 1
    return all_recs, dropped, inventory


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def votes_line(rec) -> str:
    parts = []
    for name in VOTE_NAMES:
        raw = rec["votes"].get(name)
        if not isinstance(raw, dict):
            parts.append(f"{name}=--")
        else:
            parts.append(f"{name}={num(raw.get('value')) or 0:+.2f}")
    return " ".join(parts)


def section(title: str) -> None:
    print()
    print("-" * 74)
    print(title)
    print("-" * 74)


def main() -> int:
    parser = argparse.ArgumentParser(description="POWER v2 report card (read-only)")
    parser.add_argument("day", help="YYYY-MM-DD")
    parser.add_argument("--root", default="data/power_v2_memory")
    parser.add_argument("--symbol", default=None,
                        help="grade this symbol only (default: auto-detect the "
                             "one with real votes)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    root = Path(args.root)
    path = root / f"power_{args.day}.jsonl"
    if not path.is_file():
        print(f"ERROR: diary not found: {path}")
        print("Run from the repo root, or pass --root <power_v2_memory dir>.")
        return 2

    all_recs, phantoms, inventory = load_diary(path)
    if not all_recs:
        print("ERROR: no FINAL records found in the diary.")
        return 2

    # pick the student: explicit --symbol, else the one with the most
    # records that carry real votes (the primary data symbol)
    def real_votes(rec) -> int:
        return sum(1 for name in VOTE_NAMES
                   if isinstance(rec["votes"].get(name), dict))
    if args.symbol:
        chosen = args.symbol
    else:
        scored = {sym: 0 for sym in inventory}
        for rec in all_recs:
            if real_votes(rec) > 0:
                scored[rec["symbol"]] = scored.get(rec["symbol"], 0) + 1
        chosen = max(scored, key=lambda s: (scored[s], inventory[s]))
    bars = [r for r in all_recs if r["symbol"] == chosen]
    twins = {s: c for s, c in inventory.items() if s != chosen}
    if not bars:
        print(f"ERROR: no records for symbol {chosen!r}")
        return 2
    n = len(bars)
    first, last = bars[0]["ts"], bars[-1]["ts"]

    # frozen-feed flags: a bar with no activity whose price repeats the previous
    # bar's price (dead/frozen feed stretch - marks through it are fictitious)
    frozen = [False] * n
    for i in range(1, n):
        a, b = num(bars[i - 1]["ref"]), num(bars[i]["ref"])
        if (a is not None and b is not None and a > 0 and a == b
                and (num(bars[i]["activity"]) or 0.0) == 0.0
                and (num(bars[i - 1]["activity"]) or 0.0) == 0.0):
            frozen[i] = True

    print("=" * 74)
    print(f"POWER v2 REPORT CARD (v3, per-symbol) - {args.day}")
    print(f"graded symbol: {chosen}   bars: {n}   window: {first:%H:%M} - {last:%H:%M} UTC")
    if len(inventory) > 1:
        others = ", ".join(f"{s} x{c}" for s, c in sorted(twins.items()))
        print(f"twins in same diary (NOT graded): {others}")
        print("  (trade-side/MT5 symbols have no aggressor labels ->")
        print("   fail-closed NEITHER every bar; use --symbol to grade one)")
    if phantoms:
        print(f"same-symbol duplicate records removed: {phantoms}")
    if any(frozen):
        print(f"frozen-feed bars detected: {sum(frozen)} "
              f"(marks through them are flagged below)")
    print("weather, not climate: one day proves nothing")
    print("=" * 74)

    # 1. verdict census -----------------------------------------------------
    section("1. VERDICT CENSUS (what did he say, and why)")
    counts = {}
    for rec in bars:
        counts.setdefault(rec["reason"], []).append(rec["direction"])
    for reason in sorted(counts, key=lambda k: -len(counts[k])):
        group = counts[reason]
        dirs = {}
        for d in group:
            dirs[d] = dirs.get(d, 0) + 1
        dist = ", ".join(f"{d} x{dirs[d]}" for d in sorted(dirs))
        print(f"  {len(group):>4}  {reason:<38} {dist}")

    # 2. accuracy card ------------------------------------------------------
    section("2. ACCURACY CARD (UP/DOWN calls vs forward reference prices)")
    calls = [(i, r) for i, r in enumerate(bars)
             if r["direction"] in ("UP", "DOWN") and num(r["ref"]) is not None]
    if not calls:
        print("  no directional calls this day (all NEITHER) - nothing to mark")
    else:
        print(f"  directional calls: {len(calls)} of {n} bars "
              f"({100.0 * len(calls) / n:.0f}% speak rate)")
        for label, step in HORIZONS.items():
            marks = []
            flagged = []
            for i, rec in calls:
                j = i + step
                if j >= n:
                    continue
                fwd = num(bars[j]["ref"])
                ref = num(rec["ref"])
                if fwd is None or ref is None or fwd <= 0 or ref <= 0:
                    continue
                sign = 1 if rec["direction"] == "UP" else -1
                marks.append((fwd - ref) * sign)
                if sum(1 for f in frozen[i + 1:j + 1] if f) >= 2:
                    flagged.append(f"{rec['ts']:%H:%M} {label}")
            if not marks:
                print(f"  {label}: no complete forward windows")
                continue
            hits = sum(1 for m in marks if m > 0)
            print(f"  {label}: N={len(marks):<3} right={hits:<3} "
                  f"({100.0 * hits / len(marks):.0f}%)  "
                  f"median travel {statistics.median(marks):+.2f} pts  "
                  f"mean {statistics.fmean(marks):+.2f} pts")
            if flagged:
                print(f"    frozen-window caveats: {len(flagged)} "
                      f"({', '.join(flagged)}) - travel may be fictitious")

    # 3. dominance buckets --------------------------------------------------
    section("3. DOMINANCE BUCKETS (does stronger force mean better calls?)")
    if not calls:
        print("  no directional calls to bucket")
    else:
        print("  dominant share | N | right@+30m | median travel@+30m")
        for lo, hi in DOMINANCE_BUCKETS:
            bucket = []
            for i, rec in calls:
                dom = max(num(rec["up"]) or 0.0, num(rec["down"]) or 0.0)
                if lo <= dom < hi:
                    j = i + HORIZONS["+30m"]
                    if j >= n:
                        continue
                    fwd, ref = num(bars[j]["ref"]), num(rec["ref"])
                    if fwd is None or ref is None or fwd <= 0 or ref <= 0:
                        continue
                    sign = 1 if rec["direction"] == "UP" else -1
                    bucket.append((fwd - ref) * sign)
            if not bucket:
                print(f"  {lo:.0f}-{hi:.0f}%      | 0 | -          | -")
                continue
            hits = sum(1 for m in bucket if m > 0)
            note = "  (thin)" if len(bucket) < 5 else ""
            print(f"  {lo:.0f}-{hi:.0f}%      | {len(bucket):<1} | "
                  f"{hits}/{len(bucket)} ({100.0 * hits / len(bucket):.0f}%) | "
                  f"{statistics.median(bucket):+.2f} pts{note}")

    # 4. classroom comparison ----------------------------------------------
    section("4. CLASSROOM COMPARISON (if the decisions log is found)")
    log_path = root.parent / "decisions_log.csv"
    try:
        compare_classroom(log_path, bars, args.day)
    except Exception as exc:  # never crash the card on the optional part
        print(f"  comparison skipped: {type(exc).__name__}: {exc}")

    # verbose bar-by-bar ----------------------------------------------------
    if args.verbose:
        section("BAR-BY-BAR (his 5 votes on every closed bar)")
        for i, rec in enumerate(bars):
            dom = max(num(rec["up"]) or 0.0, num(rec["down"]) or 0.0)
            print(f"  {rec['ts']:%H:%M} ref {num(rec['ref']) or 0:>9.2f}  "
                  f"{rec['direction']:<7} dom {dom:5.1f}% act {num(rec['activity']) or 0:.3f} "
                  f"| {votes_line(rec)}")

    print()
    print("Marked from the diary's own reference prices; no other data source.")
    print("Receipts for the evening paper - thresholds change only via the")
    print("one-change law, never from a single day's card.")
    return 0


def dir_sign(name) -> int:
    """Classroom and POWER speak different direction vocabularies."""
    n = str(name or "").upper()
    if n in ("UP", "BUY", "LONG", "BULLISH", "BUY_SIGNAL"):
        return 1
    if n in ("DOWN", "SELL", "SHORT", "BEARISH", "SELL_SIGNAL"):
        return -1
    return 0


def compare_classroom(log_path: Path, bars: list, day: str) -> None:
    if not log_path.is_file():
        print(f"  decisions log not found at {log_path} - skipped")
        return
    with log_path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
    if not rows:
        print("  decisions log is empty - skipped")
        return
    cols = rows[0].keys()

    def find_col(*names):
        for name in names:
            for col in cols:
                if col and col.strip().lower() == name:
                    return col
        return None

    ts_col = find_col("timestamp", "ts", "time", "datetime")
    conf_col = find_col("signal_confidence", "confidence", "conf",
                        "panel_confidence")
    dir_col = find_col("signal_direction", "direction", "signal")
    if not (ts_col and conf_col):
        print(f"  log columns not recognised (need timestamp + confidence); "
              f"found: {', '.join(list(cols)[:10])} - skipped")
        return

    day_start = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    bar_ends = [rec["ts"] for rec in bars]

    minutes_total = class_brave = power_spoke = co_brave = agree = disagree = 0
    conf_when_power = []
    conf_when_quiet = []
    winners = {"POWER": 0, "CLASS": 0, "none": 0}
    for row in rows:
        ts = parse_ts(row.get(ts_col))
        conf = num(row.get(conf_col))
        if ts is None or conf is None or not (day_start <= ts < day_end):
            continue
        # find the diary bar whose [end-5m, end] contains this minute
        bar_idx = None
        for i, end in enumerate(bar_ends):
            if end - timedelta(minutes=5) <= ts <= end:
                bar_idx = i
                break
        if bar_idx is None:
            continue
        rec = bars[bar_idx]
        minutes_total += 1
        brave = conf >= CLASS_BRAVE_CONF
        spoke = rec["direction"] in ("UP", "DOWN")
        if brave:
            class_brave += 1
        if spoke:
            power_spoke += 1
            (conf_when_power if True else conf_when_quiet).append(conf)
        else:
            conf_when_quiet.append(conf)
        if brave and spoke:
            co_brave += 1
            psign = 1 if rec["direction"] == "UP" else -1
            csign = dir_sign(row.get(dir_col)) if dir_col else 0
            if csign == 0:
                pass  # class direction unknown/unrecognised
            elif csign == psign:
                agree += 1
            else:
                disagree += 1
                # who was right? use the +30m forward reference of this bar
                j = bar_idx + HORIZONS["+30m"]
                if j < len(bars):
                    fwd, ref = num(bars[j]["ref"]), num(rec["ref"])
                    if fwd is not None and ref is not None and fwd > 0 and ref > 0:
                        move = fwd - ref
                        if move == 0:
                            winners["none"] += 1
                        elif (move > 0) == (psign > 0):
                            winners["POWER"] += 1
                        else:
                            winners["CLASS"] += 1

    if minutes_total == 0:
        print("  no decisions matched to diary bars - skipped")
        return
    print(f"  matched minutes: {minutes_total}")
    print(f"  class brave (conf >= {CLASS_BRAVE_CONF:.0f}): {class_brave} "
          f"({100.0 * class_brave / minutes_total:.0f}%)")
    print(f"  POWER spoke: {power_spoke} ({100.0 * power_spoke / minutes_total:.0f}%)")
    print(f"  BOTH brave on the same minute: {co_brave}"
          + ("   <-- the starvation number" if co_brave == 0 else ""))
    if co_brave:
        print(f"    of those: agreeing {agree}, disagreeing {disagree}")
    if disagree:
        print(f"    disagreement winners (+30m): POWER {winners['POWER']}, "
              f"CLASS {winners['CLASS']}, neither {winners['none']}")
    if conf_when_power:
        print(f"  median class conf when POWER spoke: "
              f"{statistics.median(conf_when_power):.0f}")
    if conf_when_quiet:
        print(f"  median class conf when POWER silent: "
              f"{statistics.median(conf_when_quiet):.0f}")


if __name__ == "__main__":
    sys.exit(main())
