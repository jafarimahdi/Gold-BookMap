#!/usr/bin/env python3
"""Read-only report for either legacy raw-trade or capped M5-bar probe CSVs."""
import argparse
import csv
import math
from pathlib import Path

BAR_SECONDS = 300
NANOSECONDS = 1_000_000_000
BAR_NS = BAR_SECONDS * NANOSECONDS


def summarize(path: Path) -> dict:
    historical_rows = live_rows = compact_bar_rows = 0
    history_buckets, all_buckets, aliases = set(), set(), set()
    max_history_ns = max_market_ns = first_ns = last_ns = None
    aggressor_true = aggressor_false = 0
    realtime_start_marker = False
    compact_status = ""

    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            record_type = (row.get("record_type") or "").strip().upper()
            phase = (row.get("phase") or "").strip().upper()
            alias = (row.get("alias") or "").strip().strip('"')
            if alias:
                aliases.add(alias)
            if record_type in {"REALTIME_START", "PROBE_COMPLETE"}:
                realtime_start_marker = True
                if record_type == "PROBE_COMPLETE":
                    compact_status = (row.get("status") or "").strip().upper()
                continue

            if record_type == "M5_BAR":
                try:
                    start_ns = int(row.get("bar_start_ns", ""))
                    end_ns = int(row.get("bar_end_ns", ""))
                    values = [float(row.get(name, "")) for name in ("open", "high", "low", "close")]
                except (TypeError, ValueError, OverflowError):
                    continue
                if (start_ns <= 0 or end_ns - start_ns != BAR_NS
                        or not all(math.isfinite(v) and v > 0 for v in values)):
                    continue
                bucket = (start_ns // BAR_NS) * BAR_SECONDS
                history_buckets.add(bucket)
                all_buckets.add(bucket)
                compact_bar_rows += 1
                historical_rows += 1
                max_history_ns = end_ns if max_history_ns is None else max(max_history_ns, end_ns)
                max_market_ns = end_ns if max_market_ns is None else max(max_market_ns, end_ns)
                first_ns = start_ns if first_ns is None else min(first_ns, start_ns)
                last_ns = end_ns if last_ns is None else max(last_ns, end_ns)
                continue

            if record_type != "TRADE":
                continue
            try:
                timestamp_ns = int(row.get("event_time_ns", ""))
                price = float(row.get("price", ""))
            except (TypeError, ValueError, OverflowError):
                continue
            if timestamp_ns <= 0 or not math.isfinite(price) or price <= 0:
                continue
            bucket = (timestamp_ns // BAR_NS) * BAR_SECONDS
            all_buckets.add(bucket)
            max_market_ns = timestamp_ns if max_market_ns is None else max(max_market_ns, timestamp_ns)
            first_ns = timestamp_ns if first_ns is None else min(first_ns, timestamp_ns)
            last_ns = timestamp_ns if last_ns is None else max(last_ns, timestamp_ns)
            if phase == "PRE_REALTIME":
                historical_rows += 1
                history_buckets.add(bucket)
                max_history_ns = timestamp_ns if max_history_ns is None else max(max_history_ns, timestamp_ns)
            elif phase == "LIVE":
                live_rows += 1
            flag = (row.get("bid_aggressor_flag") or "").strip().lower()
            if flag == "true":
                aggressor_true += 1
            elif flag == "false":
                aggressor_false += 1

    def run_metrics(buckets):
        if not buckets:
            return 0, 0
        values = sorted(buckets)
        longest = current = 1
        for previous, following in zip(values, values[1:]):
            current = current + 1 if following - previous == BAR_SECONDS else 1
            longest = max(longest, current)
        return longest, current

    if max_market_ns is None:
        return {
            "historical_rows": 0, "live_rows": 0, "history_bars": 0,
            "history_longest_run": 0, "history_tail_run": 0,
            "all_bars": 0, "all_longest_run": 0, "all_tail_run": 0,
            "first_ns": None, "last_ns": None,
            "aggressor_true": 0, "aggressor_false": 0,
            "realtime_start_marker": realtime_start_marker,
            "probe_aliases": sorted(aliases), "compact_status": compact_status,
            "compact_bar_rows": compact_bar_rows,
        }

    # Legacy raw-trade rows use their latest pre-realtime trade as a completion
    # cutoff. Compact M5_BAR rows are already closed bars, so their end timestamp
    # is the completion boundary.
    history_cutoff_ns = max_history_ns if max_history_ns is not None else 0
    history_completed = sorted(
        b for b in history_buckets if (b + BAR_SECONDS) * NANOSECONDS <= history_cutoff_ns)
    all_completed = sorted(
        b for b in all_buckets if (b + BAR_SECONDS) * NANOSECONDS <= max_market_ns)
    history_longest, history_tail = run_metrics(history_completed)
    all_longest, all_tail = run_metrics(all_completed)
    return {
        "historical_rows": historical_rows, "live_rows": live_rows,
        "history_bars": len(history_completed),
        "history_longest_run": history_longest, "history_tail_run": history_tail,
        "all_bars": len(all_completed), "all_longest_run": all_longest,
        "all_tail_run": all_tail, "first_ns": first_ns, "last_ns": last_ns,
        "aggressor_true": aggressor_true, "aggressor_false": aggressor_false,
        "realtime_start_marker": realtime_start_marker,
        "probe_aliases": sorted(aliases), "compact_status": compact_status,
        "compact_bar_rows": compact_bar_rows,
    }


def _ready(result: dict, key: str) -> bool:
    return (result.get("realtime_start_marker", False)
            and len(result.get("probe_aliases", [])) == 1
            and result.get("compact_status", "READY") in {"", "READY"}
            and result.get(key, 0) >= 28)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", type=Path, help="power_history_probe_*.csv")
    args = parser.parse_args()
    if not args.csv_file.is_file():
        raise SystemExit("File not found: " + str(args.csv_file))
    result = summarize(args.csv_file)
    compact = result["compact_bar_rows"] > 0
    print("POWER History Probe — read-only report")
    print("File:", args.csv_file)
    if compact:
        print("Snapshot format: capped M5 OHLC bars")
        print("Snapshot status:", result["compact_status"] or "MISSING")
        print("Stored M5 bars:", result["compact_bar_rows"], "/ 28 maximum")
    else:
        print("Snapshot format: legacy raw-trade CSV")
        print("Pre-realtime trade rows:", result["historical_rows"])
        print("Live trade rows:", result["live_rows"])
    print("Realtime/start completion marker:",
          "PRESENT" if result["realtime_start_marker"] else "MISSING")
    print("Instrument aliases:", ", ".join(result["probe_aliases"]) or "NONE")
    print("Pre-realtime completed M5 buckets:", result["history_bars"])
    print("Longest contiguous pre-realtime M5 run:", result["history_longest_run"])
    print("Latest pre-realtime contiguous run:",
          f"{result['history_tail_run']}/28",
          "READY" if _ready(result, "history_tail_run") else "NOT READY")
    if not compact:
        print("All completed M5 buckets:", result["all_bars"])
        print("Longest contiguous all-data M5 run:", result["all_longest_run"])
        print("Latest all-data contiguous run:",
              f"{result['all_tail_run']}/28",
              "READY" if _ready(result, "all_tail_run") else "NOT READY")
        print("Raw Bookmap aggressor flag counts (not mapped to BUY/SELL):",
              result["aggressor_true"], "true /", result["aggressor_false"], "false")
    print("Note: 28 contiguous completed M5 bars only satisfy the history requirement. "
          "The app still applies freshness, regime, and force gates; this report does "
          "not certify feed quality, direction, or trading readiness.")


if __name__ == "__main__":
    main()
