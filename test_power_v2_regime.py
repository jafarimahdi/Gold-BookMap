import unittest
from datetime import datetime, timedelta, timezone

from power_v2_regime import (
    _label_adx,
    classify_m5_regime,
    set_bar_seconds,
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
    def setUp(self):
        # These fixtures use 5-minute bars; isolate them from the configured
        # runtime timeframe and other tests that may select M3.
        set_bar_seconds(300)

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

    def test_m3_completion_boundary_uses_active_180_second_clock(self):
        set_bar_seconds(180)
        try:
            now = datetime(2026, 10, 21, 12, 7, 30, tzinfo=timezone.utc)
            boundary = datetime(2026, 10, 21, 12, 6, 0, tzinfo=timezone.utc)
            first_start = boundary - timedelta(minutes=3 * 36)
            ticks = []
            for i in range(36):
                start = first_start + timedelta(minutes=3 * i)
                center = 4300.0 + i * 0.5
                for offset, price in ((10, center), (60, center + 0.2),
                                      (120, center - 0.2), (170, center + 0.15)):
                    ticks.append({"timestamp": start + timedelta(seconds=offset),
                                  "price": price})
            # Fresh feed proof belongs to the unfinished bar, not the ADX input.
            ticks.append({"timestamp": now - timedelta(seconds=1), "price": 4318.0})

            result = classify_m5_regime(ticks, now=now)

            self.assertEqual(result["timeframe"], "M3")
            self.assertEqual(result["bar_seconds"], 180)
            self.assertEqual(result["bar_end_utc"], "2026-10-21T12:06:00Z")
            self.assertEqual(result["market_regime"], "TREND")
            self.assertEqual(result["reason"], "ADX_TREND")
            self.assertEqual(result["bars_used"], 28)
            self.assertFalse(result["breakout_confirmed"])
        finally:
            set_bar_seconds(300)

    def test_m3_gap_fails_closed_on_the_active_clock(self):
        set_bar_seconds(180)
        try:
            now = datetime(2026, 10, 21, 12, 7, 30, tzinfo=timezone.utc)
            boundary = datetime(2026, 10, 21, 12, 6, 0, tzinfo=timezone.utc)
            first_start = boundary - timedelta(minutes=3 * 36)
            ticks = []
            for i in range(36):
                if i == 30:
                    continue
                start = first_start + timedelta(minutes=3 * i)
                center = 4300.0 + i * 0.5
                for offset, price in ((10, center), (60, center + 0.2),
                                      (120, center - 0.2), (170, center + 0.15)):
                    ticks.append({"timestamp": start + timedelta(seconds=offset),
                                  "price": price})
            ticks.append({"timestamp": now - timedelta(seconds=1), "price": 4318.0})
            result = classify_m5_regime(ticks, now=now)
            self.assertEqual(result["market_regime"], "UNKNOWN")
            self.assertEqual(result["reason"], "GAP_IN_COMPLETED_M3_HISTORY")
        finally:
            set_bar_seconds(300)

    def test_m3_classifier_refuses_untyped_fixed_m5_probe_rows(self):
        set_bar_seconds(180)
        try:
            now = datetime(2026, 10, 21, 12, 7, 30, tzinfo=timezone.utc)
            m5_start = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)
            history = [{"start": m5_start.timestamp(), "open": 4300.0,
                        "high": 4301.0, "low": 4299.0, "close": 4300.5}]
            ticks = [{"timestamp": now - timedelta(seconds=1), "price": 4300.0}]

            result = classify_m5_regime(ticks, now=now, history_bars=history)

            self.assertTrue(result["history_timeframe_mismatch"])
            self.assertTrue(result["history_bars_refused"])
            self.assertEqual(result["bars_available"], 0)
            self.assertEqual(result["reason"], "NO_TRADE_IN_LATEST_COMPLETED_M3_BAR")

            malformed = classify_m5_regime(
                ticks, now=now, history_bars=[None])
            self.assertTrue(malformed["history_timeframe_mismatch"])
            self.assertTrue(malformed["history_bars_refused"])
        finally:
            set_bar_seconds(300)


if __name__ == "__main__":
    unittest.main()
