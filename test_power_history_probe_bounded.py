import csv
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bookmap_power_history_probe.analyze_probe import summarize as summarize_probe
from power_history_probe_loader import load_probe_regime_bars
from power_v2_regime import classify_m5_regime

ALIAS = "GCZ6.COMEX@RITHMIC"
BASE = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
HEADER = [
    "record_type", "phase", "bar_start_ns", "bar_end_ns", "alias",
    "open", "high", "low", "close", "volume", "captured_at_utc", "status",
]


def ns(dt):
    return int(dt.timestamp() * 1_000_000_000)


def rows_for_bars(count=28, *, gap_at=None, alias=ALIAS):
    rows = []
    for i in range(count):
        if i == gap_at:
            continue
        start = BASE + timedelta(minutes=5 * i)
        center = 4200.0 + i * 0.5
        rows.append([
            "M5_BAR", "PRE_REALTIME", ns(start), ns(start + timedelta(minutes=5)), alias,
            center, center + 0.2, center - 0.2, center + 0.1, 100,
            "2026-10-05T14:20:00Z", "",
        ])
    rows.append([
        "PROBE_COMPLETE", "PRE_REALTIME", "", "", "", "", "", "", "", "",
        "2026-10-05T14:20:00Z", "READY",
    ])
    return rows


class CappedProbeLoaderTests(unittest.TestCase):
    def write_snapshot(self, directory, rows, *, name="power_history_probe_latest.csv"):
        path = Path(directory) / name
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(HEADER)
            writer.writerows(rows)
        return path

    def test_accepts_exactly_28_fresh_contiguous_bars_and_is_compact(self):
        now = datetime(2026, 10, 5, 14, 20, 20, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_snapshot(temp, rows_for_bars())
            result = load_probe_regime_bars(symbol=ALIAS, now=now, directory=temp)
            self.assertEqual(result["status"], "LOADED")
            self.assertEqual(result["loaded_bars"], 28)
            self.assertLessEqual(path.stat().st_size, 16 * 1024)

    def test_rejects_27_bars(self):
        now = datetime(2026, 10, 5, 14, 20, 20, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            rows = rows_for_bars(27)
            rows[-1][-1] = "INSUFFICIENT_BARS"
            self.write_snapshot(temp, rows)
            result = load_probe_regime_bars(symbol=ALIAS, now=now, directory=temp)
            self.assertEqual(result["status"], "INSUFFICIENT_BARS")
            self.assertEqual(result["bars"], [])

    def test_rejects_gap_and_wrong_alias(self):
        now = datetime(2026, 10, 5, 14, 20, 20, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            rows = rows_for_bars(gap_at=13)
            self.write_snapshot(temp, rows)
            result = load_probe_regime_bars(symbol=ALIAS, now=now, directory=temp)
            self.assertEqual(result["status"], "GAP_IN_HISTORY")
        with tempfile.TemporaryDirectory() as temp:
            self.write_snapshot(temp, rows_for_bars(alias="MGCZ6.COMEX@RITHMIC"))
            result = load_probe_regime_bars(symbol=ALIAS, now=now, directory=temp)
            self.assertEqual(result["status"], "SYMBOL_MISMATCH")

    def test_rejects_more_than_28_candles(self):
        now = datetime(2026, 10, 5, 14, 25, 20, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            rows = rows_for_bars(29)
            self.write_snapshot(temp, rows)
            result = load_probe_regime_bars(symbol=ALIAS, now=now, directory=temp)
            self.assertEqual(result["status"], "TOO_MANY_BARS")
            self.assertEqual(result["bars"], [])

    def test_rejects_stale_snapshot(self):
        now = datetime(2026, 10, 5, 20, 30, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            self.write_snapshot(temp, rows_for_bars())
            result = load_probe_regime_bars(
                symbol=ALIAS, now=now, directory=temp, max_age_seconds=60)
            self.assertEqual(result["status"], "STALE_SNAPSHOT")

    def test_analyzer_reports_compact_snapshot_as_exactly_28_ready_bars(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self.write_snapshot(temp, rows_for_bars())
            result = summarize_probe(path)
            self.assertEqual(result["compact_status"], "READY")
            self.assertEqual(result["compact_bar_rows"], 28)
            self.assertEqual(result["history_tail_run"], 28)

    def test_classifier_uses_compact_ohlc_bars_and_live_tick_only_for_freshness(self):
        now = BASE + timedelta(minutes=5 * 28, seconds=20)
        compact = []
        for row in rows_for_bars()[:-1]:
            start = datetime.fromtimestamp(int(row[2]) / 1e9, tz=timezone.utc)
            compact.append({
                "start": start.timestamp(), "open": row[5], "high": row[6],
                "low": row[7], "close": row[8],
            })
        live_ticks = [{"timestamp": now - timedelta(seconds=1), "price": 4215.0}]
        result = classify_m5_regime(live_ticks, now=now, history_bars=compact)
        self.assertEqual(result["bars_used"], 28)
        self.assertEqual(result["market_regime"], "TREND")
        self.assertEqual(result["reason"], "ADX_TREND")

    def test_classifier_fails_closed_with_27_bars_or_a_gap(self):
        now = BASE + timedelta(minutes=5 * 28, seconds=20)
        live_ticks = [{"timestamp": now - timedelta(seconds=1), "price": 4215.0}]
        compact = []
        for row in rows_for_bars()[1:-1]:
            start = datetime.fromtimestamp(int(row[2]) / 1e9, tz=timezone.utc)
            compact.append({"start": start.timestamp(), "open": row[5],
                            "high": row[6], "low": row[7], "close": row[8]})
        result = classify_m5_regime(live_ticks, now=now, history_bars=compact)
        self.assertEqual(result["reason"], "INSUFFICIENT_COMPLETED_M5_HISTORY")
        gap_now = BASE + timedelta(minutes=5 * 29, seconds=20)
        gap_ticks = [{"timestamp": gap_now - timedelta(seconds=1), "price": 4215.0}]
        compact = []
        for row in rows_for_bars(29, gap_at=1)[:-1]:
            start = datetime.fromtimestamp(int(row[2]) / 1e9, tz=timezone.utc)
            compact.append({"start": start.timestamp(), "open": row[5],
                            "high": row[6], "low": row[7], "close": row[8]})
        result = classify_m5_regime(gap_ticks, now=gap_now, history_bars=compact)
        self.assertEqual(result["reason"], "GAP_IN_COMPLETED_M5_HISTORY")


if __name__ == "__main__":
    unittest.main()
