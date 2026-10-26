#!/usr/bin/env python3
"""council_shadow.py - the Classroom-vs-POWER fusion evidence tool.

Read-only. Stdlib only. ASCII only. Changes nothing on the machine.

Reads the POWER diary (power_<day>.jsonl) and the decisions log, aligns every
decision minute to its closed M5 bar, and prints:

  1. THE MATRIX   - class-confidence buckets x POWER state, with forward-travel
                    outcomes (the cell "conf 40-49 + POWER spoke" is the
                    toxicity check: is the class's weak-agreement band still
                    toxic when real force confirms?)
  2. THE SWEEP    - Chow's threshold sweep: for each class floor (45..20),
                    with POWER speaking + agreement required: how many minutes
                    pass (coverage) and how right they are (selective risk).
                    This is the risk-coverage curve for the fusion question.
  3. DESIGNS      - counterfactuals: TODAY's rule, POWER-led floors, the
                    Council v0 blend (0.5*conf + 0.5*dominance, floors 30/55),
                    and POWER-only.

Honesty rules: minutes, not trades (a real position holds longer); one day is
weather; marks use only the diary's own reference prices; frozen-feed windows
are flagged; trade-side/MT5 twins are filtered out (per-symbol grading).

Usage:
    python council_shadow.py 2026-10-16
    python council_shadow.py 2026-10-16 --symbol GCZ6.COMEX@RITHMIC
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
CONF_BUCKETS = ((0, 20, "<20"), (20, 30, "20-29"), (30, 40, "30-39"),
                (40, 50, "40-49"), (50, 101, ">=50"))
SWEEP_FLOORS = (45, 40, 35, 30, 25, 20)
COUNCIL_CONF_FLOOR = 30.0
COUNCIL_DOM_FLOOR = 55.0
COUNCIL_GATE = 45.0
# horizons are TRUE minutes; steps are derived in main() from the
# diary's own bar interval (M3/M5/...), so labels never lie.


def parse_ts(raw):
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


def num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def dir_sign(name):
    n = str(name or "").upper()
    if n in ("UP", "BUY", "LONG", "BULLISH"):
        return 1
    if n in ("DOWN", "SELL", "SHORT", "BEARISH"):
        return -1
    return 0


def load_diary(path: Path):
    """Per-symbol diary loader (same contract as power_report_card v3)."""
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
                "votes": votes,
            })
    parsed.sort(key=lambda r: (r["ts"], r["symbol"]))

    def richness(rec):
        live = sum(1 for name in VOTE_NAMES
                   if isinstance(rec["votes"].get(name), dict))
        return (live, 1 if (num(rec["ref"]) or 0) > 0 else 0)

    best = {}
    for rec in parsed:
        key = (rec["symbol"], rec["ts"])
        if key not in best or richness(rec) > richness(best[key]):
            best[key] = rec
    all_recs = [best[k] for k in sorted(best)]
    inventory = {}
    for rec in all_recs:
        inventory[rec["symbol"]] = inventory.get(rec["symbol"], 0) + 1
    return all_recs, inventory


def find_col(cols, *names):
    for name in names:
        for col in cols:
            if col and col.strip().lower() == name:
                return col
    return None


def decision_source_counts(rows, day_start, day_end, ts_col,
                           model_col, source_col=None):
    """Count decision origins, preferring the separate source over model names.

    ``power-lead-v1`` is recognized for historical logs; current Power carries
    keep the actual AI label and identify their source in ``decision_source``.
    This is a decision-source census, not a count of broker-executed trades.
    """
    counts = {"carried": 0, "ai": 0, "fallback": 0, "quiet": 0, "other": 0}
    for row in rows or []:
        ts = parse_ts(row.get(ts_col)) if ts_col else None
        if ts is None or not (day_start <= ts < day_end):
            continue
        model = str(row.get(model_col) or "").strip().lower() if model_col else ""
        source = str(row.get(source_col) or "").strip().upper() if source_col else ""
        if (source == "POWER_V2_CONFIGURED_THRESHOLD_CARRY"
                or model.startswith("power-lead")):
            counts["carried"] += 1
        elif model.startswith("fallback"):
            counts["fallback"] += 1
        elif model.startswith("gemini"):
            counts["ai"] += 1
        elif model.startswith("none") or model == "":
            counts["quiet"] += 1
        else:
            counts["other"] += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Classroom-vs-POWER fusion evidence (read-only)")
    parser.add_argument("day", help="YYYY-MM-DD")
    parser.add_argument("--root", default="data/power_v2_memory")
    parser.add_argument("--symbol", default=None)
    args = parser.parse_args()

    root = Path(args.root)
    path = root / f"power_{args.day}.jsonl"
    if not path.is_file():
        print(f"ERROR: diary not found: {path}")
        return 2
    all_recs, inventory = load_diary(path)
    if not all_recs:
        print("ERROR: no FINAL records found.")
        return 2

    if args.symbol:
        chosen = args.symbol
    else:
        scored = {s: 0 for s in inventory}
        for rec in all_recs:
            if any(isinstance(rec["votes"].get(n), dict) for n in VOTE_NAMES):
                scored[rec["symbol"]] = scored.get(rec["symbol"], 0) + 1
        chosen = max(scored, key=lambda s: (scored[s], inventory[s]))
    bars = [r for r in all_recs if r["symbol"] == chosen]
    twins = {s: c for s, c in inventory.items() if s != chosen}
    if not bars:
        print(f"ERROR: no records for symbol {chosen!r}")
        return 2
    n = len(bars)

    # frozen-feed detection (for caveats): match power_report_card.py — a bar is
    # only dead-feed when reference price repeats AND bar activity is zero (never
    # latch forever across the rest of the session via a sticky prefix-OR).
    frozen = [False] * n
    for i in range(1, n):
        a, b = num(bars[i - 1]["ref"]), num(bars[i]["ref"])
        act = bars[i].get("activity")
        zero_act = True if act is None else ((num(act) or 0.0) == 0.0)
        if a is not None and b is not None and a > 0 and a == b and zero_act:
            frozen[i] = True

    log_path = root.parent / "decisions_log.csv"
    if not log_path.is_file():
        print(f"ERROR: decisions log not found at {log_path}")
        print("Run from the repo root, or pass --root <power_v2_memory dir>.")
        return 2
    with log_path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        print("ERROR: decisions log is empty.")
        return 2
    cols = rows[0].keys()
    ts_col = find_col(cols, "timestamp", "ts", "time", "datetime")
    conf_col = find_col(cols, "signal_confidence", "confidence", "conf")
    dir_col = find_col(cols, "signal_direction", "direction", "signal")
    stren_col = find_col(cols, "signal_strength", "strength")
    model_col = find_col(cols, "ai_model", "model")
    source_col = find_col(cols, "decision_source")
    if not (ts_col and conf_col):
        print(f"ERROR: log columns not recognised (need timestamp + confidence);"
              f" found: {', '.join(list(cols)[:10])}")
        return 2

    day_start = datetime.fromisoformat(args.day).replace(tzinfo=timezone.utc)
    day_end = day_start + timedelta(days=1)
    bar_ends = [rec["ts"] for rec in bars]
    # fusionv1: derive horizon steps + alignment window from the diary's
    # own bar interval (M3/M5/...), not a hardcoded M5 assumption.
    deltas = [(bars[i]["ts"] - bars[i - 1]["ts"]).total_seconds()
              for i in range(1, n)]
    deltas = [d for d in deltas if d > 0]
    bar_interval = statistics.median(deltas) if deltas else 300.0
    bar_interval = min(max(bar_interval, 60.0), 3600.0)

    def _steps(minutes):
        return max(1, int(round(minutes * 60.0 / bar_interval)))

    HORIZON_30 = _steps(30)
    HORIZON_60 = _steps(60)

    minutes = []   # aligned decision minutes
    for row in rows:
        ts = parse_ts(row.get(ts_col))
        conf = num(row.get(conf_col))
        if ts is None or conf is None or not (day_start <= ts < day_end):
            continue
        idx = None
        for i, end in enumerate(bar_ends):
            if end - timedelta(seconds=bar_interval) <= ts <= end:
                idx = i
                break
        if idx is None:
            continue
        rec = bars[idx]
        dom = max(num(rec["up"]) or 0.0, num(rec["down"]) or 0.0)
        minutes.append({
            "idx": idx, "conf": conf,
            "csign": dir_sign(row.get(dir_col)) if dir_col else 0,
            "stren": num(row.get(stren_col)) if stren_col else None,
            "psign": 1 if rec["direction"] == "UP" else (-1 if rec["direction"] == "DOWN" else 0),
            "dom": dom,
        })
    if not minutes:
        print("ERROR: no decision minutes matched diary bars.")
        return 2

    def mark(idx, sign, horizon):
        """Forward-travel outcome for a minute; None if unusable."""
        j = idx + horizon
        if j >= n or sign == 0:
            return None
        fwd, ref = num(bars[j]["ref"]), num(bars[idx]["ref"])
        if fwd is None or ref is None or fwd <= 0 or ref <= 0:
            return None
        return (fwd - ref) * sign

    def frozen_flag(idx, horizon):
        return sum(1 for f in frozen[idx + 1:idx + horizon + 1] if f) >= 2

    print("=" * 74)
    print(f"COUNCIL SHADOW - fusion evidence - {args.day}")
    print(f"graded symbol: {chosen}   bars: {n}   aligned minutes: {len(minutes)}")
    print(f"bar interval: {bar_interval:.0f}s (M{bar_interval / 60.0:.0f}) - horizons are true minutes")

    # Decision-source census. Current Power carry attribution is separate from
    # ai_model; the historical power-lead-v1 model label remains recognized.
    print()
    print("0. DECISION-SOURCE CENSUS (not an executed-trade count)")
    if model_col or source_col:
        source_counts = decision_source_counts(
            rows, day_start, day_end, ts_col, model_col, source_col)
        print(f"  POWER carries: {source_counts['carried']} "
              "(decision_source=POWER_V2_CONFIGURED_THRESHOLD_CARRY or legacy "
              "ai_model=power-lead-v1) | "
              f"AI: {source_counts['ai']} | fallback: {source_counts['fallback']} | "
              f"none/quiet: {source_counts['quiet']} | other: {source_counts['other']}")
    else:
        print("  decision_source/ai_model columns not found in decisions_log - skipped")
    if twins:
        others = ", ".join(f"{s} x{c}" for s, c in sorted(twins.items()))
        print(f"twins filtered (not graded): {others}")
    print("minutes, not trades - a real position holds longer than a minute;")
    print("this measures door-openings and their direction quality.")
    print("weather, not climate: one day proves nothing.")
    print("=" * 74)

    # 1. the matrix ---------------------------------------------------------
    print()
    print("-" * 74)
    print("1. THE MATRIX - class confidence x POWER state -> outcomes")
    print("-" * 74)
    print("  (outcomes in the effective direction: POWER's when he spoke,")
    print("   otherwise the class's BUY/SELL; right = travel > 0 at +30m)")
    for lo, hi, label in CONF_BUCKETS:
        cells = {"spoke": [], "silent": []}
        for m in minutes:
            if not (lo <= m["conf"] < hi):
                continue
            state = "spoke" if m["psign"] != 0 else "silent"
            sign = m["psign"] if m["psign"] != 0 else m["csign"]
            t30 = mark(m["idx"], sign, HORIZON_30)
            cells[state].append(t30)
        for state in ("spoke", "silent"):
            marks = [t for t in cells[state] if t is not None]
            ntotal = len(cells[state])
            if ntotal == 0:
                continue
            if marks:
                hits = sum(1 for t in marks if t > 0)
                print(f"  conf {label:<5} | {state}: N={ntotal} "
                      f"right@30m {hits}/{len(marks)} "
                      f"({100.0 * hits / len(marks):.0f}%) "
                      f"median@30m {statistics.median(marks):+.2f} pts")
            else:
                print(f"  conf {label:<5} | {state}: N={ntotal} (no directional marks)")

    # 2. the sweep ----------------------------------------------------------
    print()
    print("-" * 74)
    print("2. THE SWEEP - Chow threshold curve (POWER speaks + agreement)")
    print("-" * 74)
    print("  floor | passed | blocked-disagree | right@30m | right@60m | median@60m")
    for floor in SWEEP_FLOORS:
        passed, blocked = [], 0
        for m in minutes:
            if m["psign"] == 0 or m["conf"] < floor:
                continue
            if m["csign"] != 0 and m["csign"] != m["psign"]:
                blocked += 1
                continue
            passed.append(m)
        marks30 = [t for t in (mark(m["idx"], m["psign"], HORIZON_30)
                               for m in passed) if t is not None]
        marks60 = [t for t in (mark(m["idx"], m["psign"], HORIZON_60)
                               for m in passed) if t is not None]
        r30 = f"{sum(1 for t in marks30 if t > 0)}/{len(marks30)}" if marks30 else "-"
        r60 = f"{sum(1 for t in marks60 if t > 0)}/{len(marks60)}" if marks60 else "-"
        med = (f"{statistics.median(marks60):+.2f}" if marks60 else "-")
        print(f"  {floor:>5} | {len(passed):>6} | {blocked:>16} | "
              f"{r30:>9} | {r60:>9} | {med}")

    # 3. design counterfactuals --------------------------------------------
    print()
    print("-" * 74)
    print("3. DESIGNS - what each rule would have opened (direction quality)")
    print("-" * 74)

    def design_line(name, passed, sign_of):
        marks30 = [t for t in (mark(m["idx"], sign_of(m), HORIZON_30)
                               for m in passed) if t is not None]
        marks60 = [t for t in (mark(m["idx"], sign_of(m), HORIZON_60)
                               for m in passed) if t is not None]
        r30 = (f"{sum(1 for t in marks30 if t > 0)}/{len(marks30)} "
               f"({100.0 * sum(1 for t in marks30 if t > 0) / len(marks30):.0f}%)"
               ) if marks30 else "no complete windows"
        med = (f" median@60m {statistics.median(marks60):+.2f} pts"
               if marks60 else "")
        print(f"  {name}: passed {len(passed)}  right@30m {r30}{med}")

    today = [m for m in minutes
             if m["psign"] != 0 and m["conf"] >= 45
             and not (m["csign"] != 0 and m["csign"] != m["psign"])]
    design_line("TODAY  (conf>=45 + POWER + agree)", today, lambda m: m["psign"])

    for floor in (30, 20):
        passed = [m for m in minutes
                  if m["psign"] != 0 and m["conf"] >= floor
                  and not (m["csign"] != 0 and m["csign"] != m["psign"])]
        design_line(f"OPT B  (POWER-led, class floor {floor}, agree)",
                    passed, lambda m: m["psign"])

    council = []
    for m in minutes:
        if m["psign"] == 0 or m["conf"] < COUNCIL_CONF_FLOOR:
            continue
        if m["dom"] < COUNCIL_DOM_FLOOR:
            continue
        if m["csign"] != 0 and m["csign"] != m["psign"]:
            continue
        conviction = 0.5 * m["conf"] + 0.5 * m["dom"]
        if conviction >= COUNCIL_GATE:
            council.append(m)
    design_line(f"OPT A  (Council v0: conf>=30, dom>=55, 0.5c+0.5d>={COUNCIL_GATE:.0f})",
                council, lambda m: m["psign"])

    power_only = [m for m in minutes if m["psign"] != 0]
    design_line("OPT D  (POWER-only, no class floor)", power_only,
                lambda m: m["psign"])

    # 4. strength ladder ----------------------------------------------------
    if any(m["stren"] is not None for m in minutes):
        print()
        print("-" * 74)
        print("4. STRENGTH LADDER (class loudness vs outcome when POWER spoke)")
        print("-" * 74)
        for lo, hi, label in ((0, 6, "stren <6"), (6, 101, "stren >=6")):
            marks = []
            for m in minutes:
                if m["psign"] == 0 or m["stren"] is None:
                    continue
                if not (lo <= m["stren"] < hi):
                    continue
                t = mark(m["idx"], m["psign"], HORIZON_30)
                if t is not None:
                    marks.append(t)
            if marks:
                hits = sum(1 for t in marks if t > 0)
                print(f"  {label:<10} N={len(marks):<4} right@30m {hits}/{len(marks)} "
                      f"({100.0 * hits / len(marks):.0f}%) "
                      f"median {statistics.median(marks):+.2f} pts")
            else:
                print(f"  {label:<10} N=0")

    # frozen caveat ---------------------------------------------------------
    flagged = sum(1 for m in minutes
                  if frozen_flag(m["idx"], HORIZON_60)
                  and (m["psign"] != 0 or m["csign"] != 0))
    if flagged:
        print()
        print(f"frozen-window caveat: {flagged} directional minutes had dead-feed")
        print("stretches inside their +60m window - their travel may be fictitious.")

    print()
    print("Marked only from the diary's own reference prices. This tool")
    print("measures; it never tunes. Any change to the live gates goes")
    print("through design review + shadow + the one-change law.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
