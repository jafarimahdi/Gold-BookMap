import unittest
from datetime import datetime, timedelta, timezone

from power_v2_regime import (
    _label_adx,
    classify_m5_regime,
    wilder_adx,
)


BASE = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def make_ticks(kind="trend", bars=36, *, omit_bar=None, incomplete_spike=False):
    ticks = []
    for i in range(bars):
        start = BASE + timedelta(minutes=5 * i)
        if i == omit_bar:
            continue
        if kind == "trend":
            center = 4300.0 + i * 0.5
        else:
            center = 4300.0 + (0.1 if i % 2 else -0.1)
        prices = [center, center + 0.2, center - 0.2, center + (0.15 if kind == "trend" else 0.0)]
        offsets = (10, 60, 120, 295)
        for offset, price in zip(offsets, prices):
            ticks.append({
                "timestamp": start + timedelta(seconds=offset),
                "price": price,
                "volume": 1.0,
                "side": "BUY",
                "is_direct": True,
            })
    if incomplete_spike:
        last_start = BASE + timedelta(minutes=5 * bars)
        ticks.append({"timestamp": last_start + timedelta(seconds=10),
                      "price": 9999.0, "volume": 1.0, "side": "BUY", "is_direct": True})
    return ticks


class PowerV2RegimeTests(unittest.TestCase):
    def test_adx_labels_use_trend_range_and_gray_band(self):
        self.assertEqual(_label_adx(25.0, 25.0, 20.0), ("TREND", "ADX_TREND"))
        self.assertEqual(_label_adx(19.99, 25.0, 20.0), ("RANGE", "ADX_RANGE_BREAKOUT_DISABLED"))
        self.assertEqual(_label_adx(22.5, 25.0, 20.0), ("UNKNOWN", "ADX_NEUTRAL_BAND"))

    def test_strong_monotonic_m5_series_is_trend(self):
        ticks = make_ticks("trend")
        final_end = BASE + timedelta(minutes=5 * 36)
        result = classify_m5_regime(ticks, now=final_end + timedelta(seconds=5))
        self.assertEqual(result["market_regime"], "TREND")
        self.assertGreaterEqual(result["adx"], 25.0)
        self.assertFalse(result["breakout_confirmed"])
        self.assertEqual(result["bars_used"], 28)

    def test_choppy_m5_series_is_range_but_breakout_stays_off(self):
        ticks = make_ticks("range")
        final_end = BASE + timedelta(minutes=5 * 36)
        result = classify_m5_regime(ticks, now=final_end + timedelta(seconds=5))
        self.assertEqual(result["market_regime"], "RANGE")
        self.assertLess(result["adx"], 20.0)
        self.assertFalse(result["breakout_confirmed"])

    def test_insufficient_history_fails_closed(self):
        ticks = make_ticks("trend", bars=20)
        final_end = BASE + timedelta(minutes=5 * 20)
        result = classify_m5_regime(ticks, now=final_end + timedelta(seconds=5))
        self.assertEqual(result["market_regime"], "UNKNOWN")
        self.assertEqual(result["reason"], "INSUFFICIENT_COMPLETED_M5_HISTORY")

    def test_gap_in_required_completed_bars_fails_closed(self):
        ticks = make_ticks("trend", bars=36, omit_bar=30)
        final_end = BASE + timedelta(minutes=5 * 36)
        result = classify_m5_regime(ticks, now=final_end + timedelta(seconds=5))
        self.assertEqual(result["market_regime"], "UNKNOWN")
        self.assertEqual(result["reason"], "GAP_IN_COMPLETED_M5_HISTORY")

    def test_stale_feed_fails_closed(self):
        ticks = make_ticks("trend", bars=36)
        final_end = BASE + timedelta(minutes=5 * 36)
        result = classify_m5_regime(ticks, now=final_end + timedelta(seconds=90))
        self.assertEqual(result["market_regime"], "UNKNOWN")
        self.assertEqual(result["reason"], "STALE_FEED")

    def test_in_progress_bar_is_not_used(self):
        ticks = make_ticks("trend", bars=36, incomplete_spike=True)
        final_end = BASE + timedelta(minutes=5 * 36)
        result = classify_m5_regime(ticks, now=final_end + timedelta(seconds=5))
        self.assertEqual(result["market_regime"], "TREND")
        self.assertEqual(result["bars_used"], 28)

    def test_naive_now_rejected_and_bad_ticks_ignored(self):
        with self.assertRaises(ValueError):
            classify_m5_regime([], now=datetime(2026, 9, 25, 12, 0))
        final_end = BASE + timedelta(minutes=5 * 36)
        ticks = make_ticks("trend") + [
            {"timestamp": "not-a-time", "price": 9999},
            {"timestamp": datetime(2026, 9, 25, 12, 0), "price": 9999},
        ]
        result = classify_m5_regime(ticks, now=final_end + timedelta(seconds=5))
        self.assertEqual(result["market_regime"], "TREND")

    def test_adx_needs_sufficient_bars(self):
        self.assertIsNone(wilder_adx([], period=14))


if __name__ == "__main__":
    unittest.main()
