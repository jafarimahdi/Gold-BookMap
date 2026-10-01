#!/usr/bin/env python3
"""Read-only summary of a POWER History Probe CSV; does not touch live bridge files."""
import argparse
import csv
from pathlib import Path

BAR_SECONDS = 300
NANOSECONDS = 1_000_000_000
BAR_NS = BAR_SECONDS * NANOSECONDS


def summarize(path: Path) -> dict:
    historical_rows = 0
    live_rows = 0
    history_buckets = set()
    all_buckets = set()
    max_history_ns = None
    max_market_ns = None
    first_ns = None
    last_ns = None
    aggressor_true = 0
    aggressor_false = 0
    realtime_start_marker = False
    aliases = set()

    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            record_type = (row.get("record_type") or "").strip().upper()
            if record_type == "REALTIME_START":
                realtime_start_marker = True
                marker_alias = (row.get("alias") or "").strip().strip('"')
                if marker_alias:
                    aliases.add(marker_alias)
                continue
            if record_type != "TRADE":
                continue
            try:
                timestamp_ns = int(row.get("event_time_ns", ""))
                price = float(row.get("price", ""))
            except (TypeError, ValueError):
                continue
            if timestamp_ns <= 0 or price <= 0:
                continue
            bucket = (timestamp_ns // BAR_NS) * BAR_SECONDS
            all_buckets.add(bucket)
            max_market_ns = timestamp_ns if max_market_ns is None else max(max_market_ns, timestamp_ns)
            first_ns = timestamp_ns if first_ns is None else min(first_ns, timestamp_ns)
            last_ns = timestamp_ns if last_ns is None else max(last_ns, timestamp_ns)
            alias = (row.get("alias") or "").strip().strip('"')
            if alias:
                aliases.add(alias)
            phase = (row.get("phase") or "").strip().upper()
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
        longest = current = 1
        for previous, current_bucket in zip(buckets, buckets[1:]):
            if current_bucket - previous == BAR_SECONDS:
                current += 1
            else:
                current = 1
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
            "probe_aliases": sorted(aliases),
        }

    # Crucial: a later LIVE timestamp must not promote a partial final historical
    # bucket to a completed pre-realtime bar. Historical completion is assessed
    # against the latest PRE_REALTIME market event only.
    history_cutoff_ns = max_history_ns if max_history_ns is not None else 0
    history_completed = sorted(
        b for b in history_buckets if (b + BAR_SECONDS) * NANOSECONDS <= history_cutoff_ns)
    all_completed = sorted(
        b for b in all_buckets if (b + BAR_SECONDS) * NANOSECONDS <= max_market_ns)
    history_longest, history_tail = run_metrics(history_completed)
    all_longest, all_tail = run_metrics(all_completed)
    return {
        "historical_rows": historical_rows,
        "live_rows": live_rows,
        "history_bars": len(history_completed),
        "history_longest_run": history_longest,
        "history_tail_run": history_tail,
        "all_bars": len(all_completed),
        "all_longest_run": all_longest,
        "all_tail_run": all_tail,
        "first_ns": first_ns,
        "last_ns": last_ns,
        "aggressor_true": aggressor_true,
        "aggressor_false": aggressor_false,
        "realtime_start_marker": realtime_start_marker,
        "probe_aliases": sorted(aliases),
    }


def _ready(result: dict, key: str) -> bool:
    return (
        result.get("realtime_start_marker", False)
        and len(result.get("probe_aliases", [])) == 1
        and result.get(key, 0) >= 28
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv_file", type=Path, help="power_history_probe_*.csv")
    args = parser.parse_args()
    if not args.csv_file.is_file():
        raise SystemExit("File not found: " + str(args.csv_file))
    result = summarize(args.csv_file)
    print("POWER History Probe — read-only report")
    print("File:", args.csv_file)
    print("Pre-realtime trade rows:", result["historical_rows"])
    print("Live trade rows:", result["live_rows"])
    print("Realtime-start completion marker:",
          "PRESENT" if result["realtime_start_marker"] else "MISSING")
    print("Instrument aliases:", ", ".join(result["probe_aliases"]) or "NONE")
    print("Pre-realtime completed M5 buckets:", result["history_bars"])
    print("Longest contiguous pre-realtime M5 run:", result["history_longest_run"])
    print("Latest pre-realtime contiguous run:",
          f"{result['history_tail_run']}/28",
          "READY" if _ready(result, "history_tail_run") else "NOT READY")
    print("All completed M5 buckets:", result["all_bars"])
    print("Longest contiguous all-data M5 run:", result["all_longest_run"])
    print("Latest all-data contiguous run:",
          f"{result['all_tail_run']}/28",
          "READY" if _ready(result, "all_tail_run") else "NOT READY")
    print("Raw Bookmap aggressor flag counts (not yet mapped to BUY/SELL):",
          result["aggressor_true"], "true /", result["aggressor_false"], "false")
    print("Any 28-bar contiguous sequence exists in pre-realtime history:",
          "PASS" if result["history_longest_run"] >= 28 else "NO")
    print("Note: historical maximum run proves a 28-bar sequence exists somewhere; POWER requires the latest completed contiguous run plus freshness checks. Status is NOT READY if the realtime-start marker is missing or aliases are mixed/absent. This report does not certify feed quality, aggressor-side semantics, regime, or POWER readiness.")


if __name__ == "__main__":
    main()
