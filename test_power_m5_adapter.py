import unittest
from datetime import datetime, timedelta, timezone

from power_m5_adapter import build_m5_inputs
from power_team_v2 import decide


class PowerM5AdapterTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)
        self.rows = []
        # Previous window: weaker selling pressure. Current window: direct buys.
        for i in range(30):
            ts = self.now - timedelta(minutes=10) + timedelta(seconds=i * 9)
            self.rows.append({
                "timestamp": ts.isoformat(), "price": 4150 + (i % 4) * 0.1,
                "volume": 1.0, "side": "SELL", "is_direct": True,
            })
        for i in range(40):
            ts = self.now - timedelta(minutes=4, seconds=45) + timedelta(seconds=i * 7)
            self.rows.append({
                "timestamp": ts.isoformat(), "price": 4151 + (i % 5) * 0.1,
                "volume": 1.0, "side": "BUY", "is_direct": True,
            })
        # A recent timestamp keeps the feed-freshness guard satisfied.
        self.rows.append({
            "timestamp": (self.now - timedelta(seconds=5)).isoformat(),
            "price": 4152.0, "volume": 2.0, "side": "BUY", "is_direct": True,
        })

    def test_builds_timestamp_bounded_inputs_and_can_choose_up(self):
        built = build_m5_inputs(self.rows, now=self.now, tick_size=0.1, min_trades=20)
        self.assertFalse(built["context"]["feed_stale"])
        self.assertTrue(built["context"]["data_quality_ok"])
        self.assertIn("footprint_delta", built["judges"])
        self.assertIn("big_prints", built["judges"])
        self.assertIn("footprint_levels", built["judges"])
        self.assertNotIn("sweep", built["judges"])
        answer = decide(built["judges"], context=built["context"])
        self.assertEqual(answer["direction"], "UP")
        self.assertAlmostEqual(answer["up_power_pct"] + answer["down_power_pct"], 100.0)

    def test_untrusted_side_labels_fail_closed_by_default(self):
        rows = [dict(row, is_direct=False) for row in self.rows]
        built = build_m5_inputs(rows, now=self.now, tick_size=0.1, min_trades=20)
        self.assertFalse(built["context"]["data_quality_ok"])
        self.assertEqual(built["judges"], {})
        answer = decide(built["judges"], context=built["context"])
        self.assertEqual(answer["direction"], "NEITHER")
        self.assertEqual(answer["reason_code"], "BAD_DATA_QUALITY")

    def test_stale_feed_is_neither(self):
        old_now = self.now + timedelta(minutes=3)
        built = build_m5_inputs(self.rows, now=old_now, tick_size=0.1,
                                min_trades=20, max_tick_age_seconds=30)
        self.assertTrue(built["context"]["feed_stale"])
        self.assertEqual(decide(built["judges"], context=built["context"])["direction"],
                         "NEITHER")

    def test_bad_or_naive_time_and_invalid_tick_size_rejected(self):
        with self.assertRaises(ValueError):
            build_m5_inputs(self.rows, now=datetime(2026, 9, 28), tick_size=0.1)
        with self.assertRaises(ValueError):
            build_m5_inputs(self.rows, now=self.now, tick_size=0)

    def test_boundary_tick_is_not_counted_in_both_windows(self):
        boundary = self.now - timedelta(minutes=5)
        rows = [
            {"timestamp": (boundary - timedelta(seconds=1)).isoformat(),
             "price": 4150.0, "volume": 1, "side": "SELL", "is_direct": True},
            {"timestamp": boundary.isoformat(),
             "price": 4150.1, "volume": 1, "side": "BUY", "is_direct": True},
        ]
        built = build_m5_inputs(rows, now=self.now, tick_size=0.1, min_trades=1)
        self.assertEqual(built["diagnostics"]["current_window_direct_trades"], 1)
        self.assertEqual(built["diagnostics"]["previous_window_direct_trades"], 1)


if __name__ == "__main__":
    unittest.main()
