#!/usr/bin/env python3
"""Read-only offline POWER v2 replay for Bookmap CSV and CSV.GZ files.

Consumes only positive-size `Last` rows for the selected instrument. It treats
Buy/Sell side labels as direct only when explicitly present; it never infers a
side from MBO/depth or price changes. It does not import Step 2/main, write
memory, access a broker, or modify the source archive.

The regime is deliberately UNKNOWN so every replay decision fails closed to
NEITHER. This checks archived feed parsing and M5 force/quality diagnostics,
not regime classification or predictive performance.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import gzip
import math
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from power_m5_adapter import build_m5_inputs
from power_team_v2 import decide
from power_v2_regime import classify_m5_regime

_SIDE_MAP = {
    "BUY": "BUY", "B": "BUY", "BID": "BUY",
    "SELL": "SELL", "S": "SELL", "ASK": "SELL",
}


def _parse_time(raw: Any) -> Optional[datetime]:
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        result = datetime.fromisoformat(value)
    except ValueError:
        return None
    if result.tzinfo is None or result.utcoffset() is None:
        return None
    return result.astimezone(timezone.utc)


def _load_archive(path: Path, instrument: str) -> Tuple[List[Dict[str, Any]], Counter, Counter, Counter]:
    if not path.is_file():
        raise FileNotFoundError(path)
    event_counts: Counter = Counter()
    last_operations: Counter = Counter()
    rejects: Counter = Counter()
    ticks: List[Dict[str, Any]] = []
    required = {"time", "event", "price", "size", "operation", "instrument"}

    opener = gzip.open if path.name.lower().endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"archive is missing CSV columns: {', '.join(sorted(missing))}")
        for row in reader:
            event = str(row.get("event", "")).strip()
            event_counts[event] += 1
            if event.casefold() != "last":
                continue
            if str(row.get("instrument", "")).strip() != instrument:
                rejects["other_instrument"] += 1
                continue
            operation = str(row.get("operation", "")).strip()
            last_operations[operation or "(blank)"] += 1
            ts = _parse_time(row.get("time"))
            if ts is None:
                rejects["bad_or_naive_timestamp"] += 1
                continue
            try:
                price = float(row.get("price", ""))
                volume = float(row.get("size", ""))
            except (TypeError, ValueError, OverflowError):
                rejects["bad_price_or_size"] += 1
                continue
            if not (math.isfinite(price) and math.isfinite(volume) and price > 0 and volume > 0):
                rejects["nonpositive_or_nonfinite_price_or_size"] += 1
                continue
            side = _SIDE_MAP.get(operation.upper())
            is_direct = side is not None
            if not is_direct:
                rejects["unlabelled_side_rows_kept_as_untrusted"] += 1
            ticks.append({
                "timestamp": ts,
                "price": price,
                "volume": volume,
                "side": side or "",
                "is_direct": is_direct,
            })

    ticks.sort(key=lambda row: row["timestamp"])
    return ticks, event_counts, last_operations, rejects


def replay(
    ticks: List[Dict[str, Any]],
    *,
    tick_size: float,
    min_trades: int,
    max_tick_age_seconds: float,
) -> List[Dict[str, Any]]:
    if len(ticks) < 2:
        return []
    times = [row["timestamp"] for row in ticks]
    first_epoch, last_epoch = times[0].timestamp(), times[-1].timestamp()
    # Start only after a full preceding 10-minute context exists.
    first_end = math.ceil((first_epoch + 600.0) / 300.0) * 300
    last_end = math.ceil(last_epoch / 300.0) * 300
    results: List[Dict[str, Any]] = []

    for end_epoch in range(int(first_end), int(last_end) + 1, 300):
        end = datetime.fromtimestamp(end_epoch, tz=timezone.utc)
        evaluation_time = end - timedelta(microseconds=1)
        window_start = evaluation_time - timedelta(minutes=10)
        left = bisect.bisect_left(times, window_start)
        right = bisect.bisect_right(times, evaluation_time)
        window = ticks[left:right]
        if not window:
            continue
        built = build_m5_inputs(
            window,
            now=evaluation_time,
            tick_size=tick_size,
            min_trades=min_trades,
            max_tick_age_seconds=max_tick_age_seconds,
        )
        # Regime uses a Power-v2-only ADX gate from completed M5 bars. Pass up
        # to 29 prior bars; the classifier itself rejects gaps/insufficient history.
        regime_left = bisect.bisect_left(
            times, end - timedelta(seconds=(2 * 14 + 1) * 300))
        regime_ticks = ticks[regime_left:right]
        regime = classify_m5_regime(
            regime_ticks,
            now=end + timedelta(seconds=5),
        )
        context = dict(built["context"])
        context["market_regime"] = regime["market_regime"]
        context["breakout_confirmed"] = bool(regime["breakout_confirmed"])
        answer = decide(built["judges"], context=context)
        answer["regime_diagnostics"] = regime
        results.append({
            "bar_end_utc": end,
            "answer": answer,
            "diagnostics": built["diagnostics"],
        })
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path, help="Bookmap ticks.csv or .csv.gz file")
    parser.add_argument("--instrument", default="GCZ6.COMEX@RITHMIC")
    parser.add_argument("--tick-size", type=float, default=0.10)
    parser.add_argument("--min-trades", type=int, default=20)
    parser.add_argument("--max-tick-age-seconds", type=float, default=60.0)
    parser.add_argument("--show-bars", type=int, default=12)
    args = parser.parse_args()

    if args.tick_size <= 0 or args.min_trades < 1 or args.max_tick_age_seconds <= 0:
        parser.error("tick size, min trades, and max tick age must be positive")

    ticks, event_counts, operations, rejects = _load_archive(args.archive, args.instrument)
    if not ticks:
        print(f"No valid Last rows found for instrument {args.instrument!r}.")
        print("Event counts:", event_counts.most_common())
        print("Last operation counts:", operations.most_common())
        print("Rejected rows:", rejects.most_common())
        return 2

    direct_n = sum(bool(row["is_direct"]) for row in ticks)
    first, last = ticks[0]["timestamp"], ticks[-1]["timestamp"]
    print(f"Input file: {args.archive}")
    print(f"Instrument: {args.instrument}")
    print(f"Event counts: {event_counts.most_common(20)}")
    print(f"Last operation counts: {operations.most_common()}")
    print(f"Valid Last rows: {len(ticks)}; direct-side rows: {direct_n}; untrusted-side rows: {len(ticks)-direct_n}")
    print(f"Valid Last timestamp range (UTC): {first.isoformat()} to {last.isoformat()}")
    print(f"Tick size: {args.tick_size}; regime gate: M5 ADX >=25 TREND, <20 RANGE, 20-25 UNKNOWN")
    print("Range breakout authorization is disabled in this first shadow pass.")
    print("No MBO/depth rows are treated as trades; no inferred sides, memory writes, broker calls, or app startup.")

    results = replay(
        ticks,
        tick_size=args.tick_size,
        min_trades=args.min_trades,
        max_tick_age_seconds=args.max_tick_age_seconds,
    )
    directions = Counter(row["answer"].get("direction", "?") for row in results)
    reasons = Counter(row["answer"].get("reason_code", "?") for row in results)
    regimes = Counter(row["answer"].get("regime_diagnostics", {}).get("market_regime", "?")
                      for row in results)
    quality_ok = sum(bool(row["answer"].get("context", {}).get("data_quality_ok")) for row in results)
    print(f"M5 windows evaluated: {len(results)}; data-quality-ok: {quality_ok}")
    print(f"Power-v2 regime counts: {regimes.most_common()}")
    print(f"Direction counts: {directions.most_common()}")
    print(f"Decision reason counts: {reasons.most_common()}")
    print("Recent M5 results (force shares are NOT probabilities):")
    for row in results[-max(0, args.show_bars):]:
        answer = row["answer"]
        diag = row["diagnostics"]
        regime = answer.get("regime_diagnostics", {})
        print(
            f"{row['bar_end_utc'].isoformat()} regime={regime.get('market_regime')} "
            f"ADX={regime.get('adx')} regime_reason={regime.get('reason')} "
            f"direction={answer.get('direction')} "
            f"up={answer.get('up_power_pct', 0):.1f}% "
            f"down={answer.get('down_power_pct', 0):.1f}% "
            f"reason={answer.get('reason_code')} "
            f"current_direct_trades={diag.get('current_window_direct_trades')} "
            f"side_coverage={diag.get('current_side_coverage')} "
            f"tick_age_s={diag.get('latest_tick_age_seconds')}"
        )
    print("Interpretation: only TREND may pass the force gates; RANGE breakout authorization remains off; UNKNOWN/gaps/stale data fail closed. This is a shadow diagnostic, not a trading backtest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
