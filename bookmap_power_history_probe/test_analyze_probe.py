import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from analyze_probe import summarize

FIELDS = ["record_type", "phase", "event_time_ns", "alias", "price", "size", "bid_aggressor_flag"]
BASE_NS = int(datetime(2026, 9, 30, 4, 0, tzinfo=timezone.utc).timestamp() * 1_000_000_000)


def row(record_type, phase, minute_offset, alias="GCZ6.COMEX@RITHMIC"):
    return [record_type, phase, BASE_NS + minute_offset * 60 * 1_000_000_000,
            alias, "4200", "1", "true"]


class ProbeAnalyzerTests(unittest.TestCase):
    def make_csv(self, rows):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "probe.csv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(FIELDS)
            writer.writerows(rows)
        return path

    def test_live_timestamp_does_not_complete_partial_historical_bar(self):
        path = self.make_csv([
            row("TRADE", "PRE_REALTIME", 59),  # 04:59, in 04:55-05:00 bucket
            ["REALTIME_START", "LIVE", "", "GCZ6.COMEX@RITHMIC", "", "", ""],
            row("TRADE", "LIVE", 61),  # 05:01 must not backfill history's cutoff
        ])
        result = summarize(path)
        self.assertEqual(result["history_bars"], 0)
        self.assertEqual(result["history_tail_run"], 0)
        self.assertEqual(result["all_bars"], 1)

    def test_28_latest_completed_consecutive_bars_report_ready(self):
        rows = []
        # Trades at the start of 28 distinct M5 buckets; a later event establishes
        # the final bucket is closed in the historical phase.
        for i in range(28):
            rows.append(row("TRADE", "PRE_REALTIME", i * 5))
        rows.append(row("TRADE", "PRE_REALTIME", 28 * 5))
        rows.append(["REALTIME_START", "LIVE", "", "GCZ6.COMEX@RITHMIC", "", "", ""])
        result = summarize(self.make_csv(rows))
        self.assertTrue(result["realtime_start_marker"])
        self.assertEqual(result["history_tail_run"], 28)

    def test_mixed_aliases_are_reported(self):
        result = summarize(self.make_csv([
            row("TRADE", "PRE_REALTIME", 0),
            ["REALTIME_START", "LIVE", "", "MGCZ6.COMEX@RITHMIC", "", "", ""],
        ]))
        self.assertEqual(len(result["probe_aliases"]), 2)


if __name__ == "__main__":
    unittest.main()
