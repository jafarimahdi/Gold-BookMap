import unittest
from datetime import datetime, timedelta, timezone

from power_m5_adapter import build_m5_inputs
from power_team_v2 import decide
from power_v2_regime import classify_m5_regime
from power_v2_shadow import run_power_v2


BASE = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def make_regime_ticks(kind="trend", bars=36, *, omit_bar=None):
    rows = []
    for i in range(bars):
        if i == omit_bar:
            continue
        start = BASE + timedelta(minutes=5 * i)
        center = 4300.0 + i * 0.5 if kind == "trend" else 4300.0 + (0.1 if i % 2 else -0.1)
        prices = [center, center + 0.2, center - 0.2, center + (0.15 if kind == "trend" else 0.0)]
        for offset, price in zip((10, 60, 120, 295), prices):
            rows.append({"timestamp": start + timedelta(seconds=offset), "price": price,
                         "volume": 1.0, "side": "BUY", "is_direct": True})
    return rows


def make_adapter_ticks(now, *, uniform=False):
    rows = []
    for i in range(30):
        ts = now - timedelta(minutes=10) + timedelta(seconds=i * 9)
        rows.append({"timestamp": ts.isoformat(), "price": 4150 + (i % 4) * 0.1,
                     "volume": 1.0, "side": "SELL", "is_direct": True})
    for i in range(40):
        ts = now - timedelta(minutes=4, seconds=45) + timedelta(seconds=i * 7)
        volume = 1.0 if uniform or i >= 3 else 10.0
        rows.append({"timestamp": ts.isoformat(), "price": 4151 + (i % 5) * 0.1,
                     "volume": volume, "side": "BUY", "is_direct": True})
    rows.append({"timestamp": (now - timedelta(seconds=5)).isoformat(),
                 "price": 4152.0, "volume": 1.0 if uniform else 2.0,
                 "side": "BUY", "is_direct": True})
    return rows


def make_runner_ticks(now):
    bar_end = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(30):
        ts = bar_end - timedelta(minutes=10) + timedelta(seconds=i * 9)
        rows.append({"timestamp": ts.isoformat(), "price": 4150 + (i % 3) * 0.1,
                     "volume": 1.0, "side": "SELL", "is_direct": True})
    for i in range(40):
        ts = bar_end - timedelta(minutes=5) + timedelta(seconds=i * 7)
        rows.append({"timestamp": ts.isoformat(), "price": 4151 + (i % 4) * 0.1,
                     "volume": 10.0 if i < 3 else 1.0, "side": "BUY", "is_direct": True})
    return rows


class PowerV2DiagnosticUpdateTests(unittest.TestCase):
    def test_regime_reports_insufficient_count_without_lowering_requirement(self):
        ticks = make_regime_ticks(bars=20)
        end = BASE + timedelta(minutes=100)
        result = classify_m5_regime(ticks, now=end + timedelta(seconds=5))
        self.assertEqual(result["reason"], "INSUFFICIENT_COMPLETED_M5_HISTORY")
        self.assertEqual(result["bars_used"], 20)
        self.assertEqual(result["bars_required"], 28)
        self.assertEqual(result["latest_contiguous_bars"], 20)
        self.assertEqual(result["recent_gap_count"], 0)

    def test_regime_reports_missing_intervals_inside_required_window(self):
        ticks = make_regime_ticks(bars=36, omit_bar=30)
        end = BASE + timedelta(minutes=180)
        result = classify_m5_regime(ticks, now=end + timedelta(seconds=5))
        self.assertEqual(result["reason"], "GAP_IN_COMPLETED_M5_HISTORY")
        self.assertEqual(result["bars_used"], 28)
        self.assertEqual(result["bars_required"], 28)
        self.assertEqual(result["recent_gap_count"], 1)
        self.assertEqual(result["recent_gaps"][0]["missing_m5_intervals"], 1)
        self.assertFalse(result["recent_history_contiguous"])

    def test_force_gate_report_does_not_bypass_unknown_regime(self):
        result = decide({"footprint_delta": -0.5, "cvd_momentum": -0.2,
                         "big_prints": -0.1, "footprint_levels": -0.5})
        self.assertEqual(result["direction"], "NEITHER")
        self.assertEqual(result["reason_code"], "REGIME_UNKNOWN_OR_UNCONFIRMED")
        self.assertTrue(result["trend_force_diagnostics"]["down_force_checks_pass"])
        self.assertTrue(result["trend_force_diagnostics"]["diagnostic_only"])

    def test_unreliable_force_is_data_quality_and_context_flags_are_not_new_vetoes(self):
        all_up = {"footprint_delta": 0.8, "l3_aggr_limit": 0.8,
                  "cvd_momentum": 0.7, "big_prints": 0.9,
                  "footprint_levels": 0.7}
        bad = decide(all_up, context={"market_regime": "TREND", "force_unreliable": True})
        self.assertEqual(bad["direction"], "NEITHER")
        self.assertEqual(bad["reason_code"], "BAD_DATA_QUALITY")
        for context_flag in ("major_contradiction", "out_of_session"):
            result = decide(all_up, context={"market_regime": "TREND", context_flag: True})
            self.assertEqual(result["direction"], "UP")

    def test_adapter_reports_l3_source_and_large_print_absence_separately(self):
        now = datetime(2026, 9, 28, 16, 0, tzinfo=timezone.utc)
        varied = build_m5_inputs(make_adapter_ticks(now), now=now, tick_size=0.1, min_trades=20)
        self.assertEqual(varied["judge_availability"]["l3_aggr_limit"]["status"], "NOT_EMITTED")
        self.assertEqual(varied["judge_availability"]["big_prints"]["status"], "VALID")
        uniform = build_m5_inputs(make_adapter_ticks(now, uniform=True), now=now,
                                  tick_size=0.1, min_trades=20)
        self.assertNotIn("big_prints", uniform["judges"])
        self.assertEqual(uniform["judge_availability"]["big_prints"]["status"], "MISSING")
        self.assertIn("large-print tail", uniform["judge_availability"]["big_prints"]["reason"])

    def test_runner_maps_explicit_blackout_and_surfaces_l3_reason(self):
        now = datetime(2026, 9, 28, 16, 0, 10, tzinfo=timezone.utc)
        result = run_power_v2(make_runner_ticks(now), now=now,
                              symbol="GCZ6.COMEX@RITHMIC", tick_size=0.1,
                              market_regime="TREND", high_impact_news=True)
        self.assertEqual(result["direction"], "NEITHER")
        self.assertEqual(result["reason_code"], "HIGH_IMPACT_EVENT")
        self.assertEqual(result["judge_availability"]["l3_aggr_limit"]["status"], "NOT_EMITTED")
        self.assertIn("validated L3 executed-flow field",
                      result["excluded_judges"]["l3_aggr_limit"])


if __name__ == "__main__":
    unittest.main()
