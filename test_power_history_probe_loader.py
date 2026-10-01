import csv
import os
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

from power_history_probe_loader import (
    load_probe_regime_prices, merge_regime_price_ticks, m5_tail_diagnostics,
)

HEADER = ["record_type", "phase", "event_time_ns", "alias", "price", "size", "bid_aggressor_flag"]
ALIAS = "GCZ6.COMEX@RITHMIC"


def ns(value):
    return int(value.timestamp() * 1_000_000_000)


class ProbeLoaderTests(unittest.TestCase):
    def write_probe(self, directory, *, now, rows, marker=True, filename="power_history_probe_test.csv"):
        path = Path(directory) / filename
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(HEADER)
            writer.writerow(["PROBE_STARTED", "PRE_REALTIME", "", ALIAS, "", "", ""])
            for row in rows:
                writer.writerow(row)
            if marker:
                writer.writerow(["REALTIME_START", "LIVE", "", ALIAS, "", "", ""])
        return path

    def test_loads_only_pre_realtime_price_history_and_ignores_aggressor_flag(self):
        now = datetime(2026, 9, 30, 5, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            rows = []
            first = now - timedelta(hours=3)
            for i in range(30):
                ts = first + timedelta(minutes=5 * i, seconds=10)
                rows.append(["TRADE", "PRE_REALTIME", ns(ts), ALIAS, 4200 + i, 1, "true"])
            rows.append(["TRADE", "LIVE", ns(now - timedelta(seconds=1)), ALIAS, 4230, 1, "false"])
            self.write_probe(temp, now=now, rows=rows)
            result = load_probe_regime_prices(
                symbol=ALIAS, now=now, directory=temp, max_age_seconds=6 * 3600)
            self.assertEqual(result["status"], "LOADED")
            self.assertEqual(result["loaded_ticks"], 30)
            tail = m5_tail_diagnostics(
                result["ticks"], now, completion_cutoff_from_latest_tick=True)
            self.assertEqual(tail["latest_contiguous_m5_bars"], 29)
            self.assertFalse(result["side_data_used"])
            self.assertNotIn("side", result["ticks"][0])
            self.assertNotIn("bid_aggressor_flag", result["ticks"][0])

    def test_uses_newest_completed_file_not_newer_in_progress_file(self):
        now = datetime(2026, 9, 30, 5, 0, tzinfo=timezone.utc)
        recent = now - timedelta(minutes=5, seconds=20)
        with tempfile.TemporaryDirectory() as temp:
            completed = self.write_probe(temp, now=now, rows=[
                ["TRADE", "PRE_REALTIME", ns(recent), ALIAS, 4200, 1, "true"]],
                filename="power_history_probe_completed.csv")
            incomplete = self.write_probe(temp, now=now, rows=[], marker=False,
                                          filename="power_history_probe_in_progress.csv")
            os.utime(incomplete, (completed.stat().st_mtime + 10,
                                  completed.stat().st_mtime + 10))
            result = load_probe_regime_prices(symbol=ALIAS, now=now, directory=temp)
            self.assertEqual(result["status"], "LOADED")
            self.assertEqual(Path(result["path"]).name, completed.name)

    def test_rejects_partial_file_without_live_transition_marker(self):
        now = datetime(2026, 9, 30, 5, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            self.write_probe(temp, now=now, rows=[], marker=False)
            result = load_probe_regime_prices(symbol=ALIAS, now=now, directory=temp)
            self.assertEqual(result["status"], "INCOMPLETE_NO_REALTIME_MARKER")
            self.assertEqual(result["ticks"], [])

    def test_rejects_other_instrument_alias(self):
        now = datetime(2026, 9, 30, 5, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "power_history_probe_test.csv"
            with path.open("w", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(HEADER)
                writer.writerow(["TRADE", "PRE_REALTIME", ns(now - timedelta(minutes=10)), "MGCZ6.COMEX@RITHMIC", 4200, 1, "true"])
                writer.writerow(["REALTIME_START", "LIVE", "", "MGCZ6.COMEX@RITHMIC", "", "", ""])
            result = load_probe_regime_prices(symbol=ALIAS, now=now, directory=temp)
            self.assertEqual(result["status"], "SYMBOL_MISMATCH")
            self.assertEqual(result["ticks"], [])

    def test_runtime_symbol_mapping_requires_explicit_confirmation(self):
        now = datetime(2026, 9, 30, 5, 0, tzinfo=timezone.utc)
        recent = now - timedelta(minutes=5, seconds=20)
        with tempfile.TemporaryDirectory() as temp:
            self.write_probe(temp, now=now, rows=[
                ["TRADE", "PRE_REALTIME", ns(recent), ALIAS, 4200, 1, "true"]])
            with patch.dict("os.environ", {
                "POWER_HISTORY_PROBE_ALIAS": ALIAS,
                "POWER_HISTORY_PROBE_RUNTIME_SYMBOL": "",
            }, clear=False):
                result = load_probe_regime_prices(
                    symbol="MGC 12-26", now=now, directory=temp)
            self.assertEqual(result["status"], "UNCONFIRMED_ALIAS_MAPPING")
            with patch.dict("os.environ", {
                "POWER_HISTORY_PROBE_ALIAS": ALIAS,
                "POWER_HISTORY_PROBE_RUNTIME_SYMBOL": "MGC 12-26",
            }, clear=False):
                result = load_probe_regime_prices(
                    symbol="MGC 12-26", now=now, directory=temp)
            self.assertEqual(result["status"], "LOADED")
            self.assertEqual(result["match_basis"], "explicit_runtime_symbol_alias_pair")

    def test_ignores_old_rows_and_deduplicates_price_time_against_live_ticks(self):
        now = datetime(2026, 9, 30, 5, 0, tzinfo=timezone.utc)
        recent = now - timedelta(minutes=5, seconds=20)
        stale = now - timedelta(hours=8)
        with tempfile.TemporaryDirectory() as temp:
            rows = [
                ["TRADE", "PRE_REALTIME", ns(stale), ALIAS, 4100, 1, "true"],
                ["TRADE", "PRE_REALTIME", ns(recent), ALIAS, 4200, 1, "false"],
            ]
            self.write_probe(temp, now=now, rows=rows)
            live = [{"timestamp": recent.isoformat(), "price": 4200, "side": "BUY"}]
            result = load_probe_regime_prices(
                symbol=ALIAS, now=now, directory=temp,
                max_age_seconds=6 * 3600, existing_ticks=live)
            self.assertEqual(result["status"], "NO_USABLE_RECENT_HISTORY")
            self.assertEqual(result["ignored_old_rows"], 1)
            self.assertEqual(result["duplicate_rows"], 1)
            self.assertEqual(result["ticks"], [])

    def test_probe_tail_cutoff_does_not_use_later_wall_clock_or_live_time(self):
        now = datetime(2026, 9, 30, 6, 0, tzinfo=timezone.utc)
        ticks = [
            {"timestamp": "2026-09-30T04:50:10Z", "price": 4200},
            {"timestamp": "2026-09-30T04:59:10Z", "price": 4201},
        ]
        probe = m5_tail_diagnostics(
            ticks, now, completion_cutoff_from_latest_tick=True)
        combined = m5_tail_diagnostics(ticks, now)
        self.assertEqual(probe["latest_contiguous_m5_bars"], 1)
        self.assertEqual(probe["latest_completed_bar_end_utc"], "2026-09-30T04:55:00Z")
        self.assertEqual(combined["latest_contiguous_m5_bars"], 2)

    def test_merge_deduplicates_same_m5_price_timestamp(self):
        ts = "2026-09-30T04:55:10Z"
        merged = merge_regime_price_ticks(
            [{"timestamp": ts, "price": 4200.0, "side": "BUY"}],
            [{"timestamp": ts, "price": 4200.0, "source": "probe"},
             {"timestamp": ts, "price": 4201.0, "source": "probe"}],
        )
        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["price"], 4200.0)
        self.assertEqual(merged[1]["price"], 4201.0)


if __name__ == "__main__":
    unittest.main()
