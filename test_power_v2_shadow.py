import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from power_memory_book import read_day
from power_v2_shadow import last_completed_m5_end, run_power_v2, run_power_v2_shadow


class PowerV2ShadowTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 28, 16, 0, 10, tzinfo=timezone.utc)
        self.ticks = []
        bar_end = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)
        for i in range(30):
            ts = bar_end - timedelta(minutes=10) + timedelta(seconds=i * 9)
            self.ticks.append({"timestamp": ts.isoformat(), "price": 4150 + (i % 3) * .1,
                               "volume": 1, "side": "SELL", "is_direct": True})
        for i in range(40):
            ts = bar_end - timedelta(minutes=5) + timedelta(seconds=i * 7)
            self.ticks.append({"timestamp": ts.isoformat(), "price": 4151 + (i % 4) * .1,
                               "volume": 10 if i < 3 else 1, "side": "BUY", "is_direct": True})

    def test_grace_keeps_just_closed_bar_pending(self):
        before_close = datetime(2026, 9, 28, 16, 0, 3, tzinfo=timezone.utc)
        after_grace = datetime(2026, 9, 28, 16, 0, 6, tzinfo=timezone.utc)
        self.assertEqual(last_completed_m5_end(before_close),
                         datetime(2026, 9, 28, 15, 55, tzinfo=timezone.utc))
        self.assertEqual(last_completed_m5_end(after_grace),
                         datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc))

    def test_unknown_regime_fails_closed_and_records_once_per_bar(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "power_memory"
            first = run_power_v2_shadow(
                self.ticks, now=self.now, symbol="GCZ6.COMEX@RITHMIC",
                tick_size=.1, memory_root=root)
            second = run_power_v2_shadow(
                self.ticks, now=self.now, symbol="GCZ6.COMEX@RITHMIC",
                tick_size=.1, memory_root=root)
            self.assertTrue(first["shadow_only"])
            self.assertEqual(first["direction"], "NEITHER")
            self.assertEqual(first["reason_code"], "REGIME_UNKNOWN_OR_UNCONFIRMED")
            self.assertTrue(first["memory_status"]["appended"])
            self.assertFalse(second["memory_status"]["appended"])
            rows = read_day(root, "2026-09-28")
            self.assertEqual(len(rows), 1)
            self.assertAlmostEqual(rows[0]["reference_price"], 4151.3)

    def test_explicit_test_regime_only_changes_shadow_result(self):
        result = run_power_v2_shadow(
            self.ticks, now=self.now, symbol="GCZ6.COMEX@RITHMIC",
            tick_size=.1, market_regime="TREND")
        self.assertTrue(result["shadow_only"])
        self.assertEqual(result["direction"], "UP")
        self.assertEqual(result["power_total_pct"], 100.0)

    def test_active_runner_is_not_shadow_and_keeps_power_shares_non_probability(self):
        result = run_power_v2(
            self.ticks, now=self.now, symbol="GCZ6.COMEX@RITHMIC",
            tick_size=.1, market_regime="TREND")
        self.assertFalse(result["shadow_only"])
        self.assertEqual(result["direction"], "UP")
        self.assertAlmostEqual(result["up_power_pct"] + result["down_power_pct"], 100.0)
        self.assertIn("not probability", result["meaning"])

    def test_invalid_tick_size_is_rejected(self):
        with self.assertRaises(ValueError):
            run_power_v2_shadow(self.ticks, now=self.now,
                                symbol="GCZ6.COMEX@RITHMIC", tick_size=0)


if __name__ == "__main__":
    unittest.main()
