"""Fail-closed history/live price-scale guard tests.

Probe history prices and live tick prices must be in the same price units
before their bars may be combined. A mismatch (e.g. history at 10x) must
REFUSE the history and leave the regime UNKNOWN. Nothing is ever rescaled.
"""
import unittest
from datetime import datetime, timedelta, timezone

from power_history_probe_loader import (
    check_price_scale_compatibility, merge_regime_price_ticks)
from power_v2_regime import classify_m5_regime

BASE = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)


def history_bars(count=28, *, center=4200.0, step=0.5):
    rows = []
    for i in range(count):
        start = BASE + timedelta(minutes=5 * i)
        c = center + i * step
        rows.append({"start": start.timestamp(), "open": c, "high": c + 0.2,
                     "low": c - 0.2, "close": c + 0.1})
    return rows


class PriceScaleCheckTests(unittest.TestCase):
    def test_ok_when_both_sides_same_scale(self):
        result = check_price_scale_compatibility([4200.0, 4201.0], [4215.0])
        self.assertEqual(result["status"], "OK")

    def test_mismatch_when_history_is_10x_like_real_probe_file(self):
        # Real observed pair: probe OHLC near 41649 vs live prints near 4160.
        result = check_price_scale_compatibility(
            [41649.0, 41650.0, 41640.0], [4160.0, 4159.5, 4161.0])
        self.assertEqual(result["status"], "MISMATCH")
        self.assertGreater(result["ratio"], 5.0)

    def test_mismatch_when_history_is_tenth_scale(self):
        result = check_price_scale_compatibility([416.4, 416.5], [4160.0])
        self.assertEqual(result["status"], "MISMATCH")
        self.assertLess(result["ratio"], 0.2)

    def test_no_history_and_no_live_are_reported(self):
        self.assertEqual(
            check_price_scale_compatibility([], [4200.0])["status"], "NO_HISTORY")
        self.assertEqual(
            check_price_scale_compatibility([4200.0], [])["status"], "NO_LIVE_PRICES")
        self.assertEqual(
            check_price_scale_compatibility([], [])["status"], "NO_HISTORY")

    def test_invalid_values_are_ignored_not_rescaled(self):
        result = check_price_scale_compatibility(
            [None, "x", -1, 0, float("nan"), 4200.0], [4215.0])
        self.assertEqual(result["status"], "OK")
        self.assertEqual(result["history_prices"], 1)
        self.assertEqual(result["history_median_price"], 4200.0)


class ClassifierScaleGateTests(unittest.TestCase):
    def test_mismatched_history_is_refused_and_regime_stays_unknown(self):
        now = BASE + timedelta(minutes=5 * 28, seconds=20)
        live_ticks = [{"timestamp": now - timedelta(seconds=1), "price": 4160.0}]
        result = classify_m5_regime(
            live_ticks, now=now,
            history_bars=history_bars(center=41640.0, step=1.0))
        self.assertEqual(result["market_regime"], "UNKNOWN")
        self.assertEqual(result["reason"], "HISTORY_PRICE_SCALE_MISMATCH")
        self.assertIsNone(result["adx"])
        self.assertTrue(result["history_bars_refused"])
        self.assertEqual(result["price_scale_check"]["status"], "MISMATCH")
        self.assertFalse(result["breakout_confirmed"])

    def test_matching_history_still_classifies_trend(self):
        now = BASE + timedelta(minutes=5 * 28, seconds=20)
        live_ticks = [{"timestamp": now - timedelta(seconds=1), "price": 4215.0}]
        result = classify_m5_regime(
            live_ticks, now=now, history_bars=history_bars())
        self.assertEqual(result["price_scale_check"]["status"], "OK")
        self.assertFalse(result["history_bars_refused"])
        self.assertEqual(result["bars_used"], 28)
        self.assertEqual(result["market_regime"], "TREND")
        self.assertEqual(result["reason"], "ADX_TREND")

    def test_history_without_live_prices_is_refused(self):
        now = BASE + timedelta(minutes=5 * 28, seconds=20)
        result = classify_m5_regime([], now=now, history_bars=history_bars())
        self.assertTrue(result["history_bars_refused"])
        self.assertEqual(result["price_scale_check"]["status"], "NO_LIVE_PRICES")
        self.assertEqual(result["market_regime"], "UNKNOWN")
        self.assertEqual(result["reason"], "NO_TIMESTAMPED_PRICE_TICKS")

    def test_live_only_path_is_unchanged_without_history(self):
        rows = []
        for i in range(36):
            start = BASE + timedelta(minutes=5 * i)
            center = 4300.0 + i * 0.5
            for offset, price in zip((10, 60, 120, 295),
                                     (center, center + 0.2,
                                      center - 0.2, center + 0.15)):
                rows.append({"timestamp": start + timedelta(seconds=offset),
                             "price": price})
        now = BASE + timedelta(minutes=5 * 36, seconds=5)
        result = classify_m5_regime(rows, now=now)
        self.assertEqual(result["price_scale_check"]["status"], "NO_HISTORY")
        self.assertFalse(result["history_bars_refused"])
        self.assertEqual(result["market_regime"], "TREND")
        self.assertEqual(result["reason"], "ADX_TREND")


class MergeScaleGateTests(unittest.TestCase):
    def test_merge_drops_mismatched_history_rows(self):
        merged = merge_regime_price_ticks(
            [{"timestamp": "2026-09-30T04:55:10Z", "price": 4160.0}],
            [{"timestamp": "2026-09-30T04:50:10Z", "price": 41649.0}])
        self.assertEqual([row["price"] for row in merged], [4160.0])

    def test_merge_keeps_matching_history_rows(self):
        merged = merge_regime_price_ticks(
            [{"timestamp": "2026-09-30T04:55:10Z", "price": 4200.0}],
            [{"timestamp": "2026-09-30T04:50:10Z", "price": 4201.0}])
        self.assertEqual([row["price"] for row in merged], [4201.0, 4200.0])

    def test_merge_without_live_prices_refuses_history(self):
        merged = merge_regime_price_ticks(
            [], [{"timestamp": "2026-09-30T04:50:10Z", "price": 4201.0}])
        self.assertEqual(merged, [])


if __name__ == "__main__":
    unittest.main()
