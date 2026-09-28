import math
import unittest

from power_team_v2 import decide


ALL = {
    "footprint_delta": 0.8,
    "l3_aggr_limit": 0.8,
    "cvd_momentum": 0.7,
    "big_prints": 0.9,
    "footprint_levels": 0.7,
}
TREND = {"market_regime": "TREND"}


class PowerTeamV2Tests(unittest.TestCase):
    def test_strong_up_returns_up_and_100_total(self):
        result = decide(ALL, context=TREND)
        self.assertEqual(result["direction"], "UP")
        self.assertAlmostEqual(result["up_power_pct"] + result["down_power_pct"], 100.0)
        self.assertGreaterEqual(result["up_power_pct"], 60.0)

    def test_strong_down_returns_down(self):
        result = decide({name: -value for name, value in ALL.items()}, context=TREND)
        self.assertEqual(result["direction"], "DOWN")
        self.assertAlmostEqual(result["up_power_pct"] + result["down_power_pct"], 100.0)

    def test_opposing_equal_force_returns_neither(self):
        # Match weighted up/down evidence exactly (weights are intentionally unequal).
        signals = {
            "footprint_delta": 0.8,
            "cvd_momentum": -0.0666666667,
            "big_prints": 0.8,
            "l3_aggr_limit": -0.8666666667,
            "sweep": -0.8666666667,
            "footprint_levels": -0.8666666667,
        }
        result = decide(signals, context=TREND)
        self.assertEqual(result["direction"], "NEITHER")
        self.assertEqual(result["reason_code"], "FORCE_TOO_BALANCED")
        self.assertGreater(result["up_power_pct"], 40.0)
        self.assertLess(result["up_power_pct"], 60.0)
        self.assertIn("sweep", result["excluded_judges"])

    def test_weak_market_returns_neither_even_if_barely_one_sided(self):
        result = decide({name: 0.05 for name in ALL}, context=TREND)
        self.assertEqual(result["direction"], "NEITHER")
        self.assertEqual(result["reason_code"], "WEAK_OR_NO_FORCE")

    def test_one_correlated_family_cannot_authorize_direction_alone(self):
        result = decide({
            "footprint_delta": 0.9,
            "l3_aggr_limit": 0.9,
            "cvd_momentum": 0.9,
        }, context=TREND)
        self.assertEqual(result["direction"], "NEITHER")
        self.assertEqual(result["reason_code"], "INSUFFICIENT_COVERAGE")
        self.assertEqual(result["valid_families"], ["executed_flow"])

    def test_unknown_regime_fails_closed_even_with_one_sided_evidence(self):
        result = decide(ALL)
        self.assertEqual(result["direction"], "NEITHER")
        self.assertEqual(result["reason_code"], "REGIME_UNKNOWN_OR_UNCONFIRMED")

    def test_one_large_trade_family_cannot_create_direction_by_itself(self):
        # Even a very large reading from one family cannot authorize a side alone.
        result = decide({
            "footprint_delta": 0.058,
            "big_prints": 0.8,
            "footprint_levels": -0.059,
        }, context=TREND)
        self.assertEqual(result["direction"], "NEITHER")
        self.assertEqual(result["reason_code"], "INSUFFICIENT_FAMILY_AGREEMENT")

    def test_tiny_net_force_returns_neither_even_if_share_is_skewed(self):
        result = decide({
            "footprint_delta": 0.058,
            "big_prints": 0.04,
            "footprint_levels": -0.059,
        }, context=TREND)
        self.assertEqual(result["direction"], "NEITHER")
        self.assertEqual(result["reason_code"], "WEAK_OR_NO_FORCE")

    def test_range_without_confirmed_breakout_returns_neither(self):
        result = decide(ALL, context={"market_regime": "RANGE"})
        self.assertEqual(result["direction"], "NEITHER")
        self.assertEqual(result["reason_code"], "RANGE_NO_CONFIRMED_BREAKOUT")

    def test_range_breakout_needs_stricter_confirmation(self):
        result = decide(ALL, context={"market_regime": "RANGE", "breakout_confirmed": True})
        self.assertEqual(result["direction"], "UP")
        weak_coverage = decide(
            {"footprint_delta": 1.0, "l3_aggr_limit": 1.0,
             "cvd_momentum": 1.0},
            context={"market_regime": "RANGE", "breakout_confirmed": True},
        )
        self.assertEqual(weak_coverage["direction"], "NEITHER")
        self.assertEqual(weak_coverage["reason_code"], "INSUFFICIENT_COVERAGE")

    def test_hard_quality_flags_block_direction(self):
        for context, code in [
            ({"feed_stale": True}, "STALE_FEED"),
            ({"data_quality_ok": False}, "BAD_DATA_QUALITY"),
            ({"high_impact_news": True}, "HIGH_IMPACT_EVENT"),
            ({"major_contradiction": True}, "MAJOR_CONTRADICTION"),
        ]:
            result = decide(ALL, context=context)
            self.assertEqual(result["direction"], "NEITHER")
            self.assertEqual(result["reason_code"], code)

    def test_missing_and_invalid_judges_are_excluded(self):
        result = decide({
            "footprint_delta": 1.0,
            "l3_aggr_limit": 1.0,
            "cvd_momentum": float("nan"),
        })
        self.assertEqual(result["direction"], "NEITHER")
        self.assertIn("cvd_momentum", result["excluded_judges"])

    def test_quality_reduces_coverage(self):
        result = decide({
            "footprint_delta": {"value": 1, "quality": 0.2},
            "l3_aggr_limit": {"value": 1, "quality": 0.2},
            "cvd_momentum": {"value": 1, "quality": 0.2},
        })
        self.assertLess(result["coverage"], 0.2)
        self.assertEqual(result["direction"], "NEITHER")

    def test_no_evidence_is_neither_not_a_fake_50_percent_signal(self):
        result = decide({})
        self.assertEqual(result["direction"], "NEITHER")
        self.assertEqual(result["up_power_pct"], 50.0)
        self.assertEqual(result["down_power_pct"], 50.0)
        self.assertEqual(result["activity"], 0.0)
        self.assertEqual(result["coverage"], 0.0)


if __name__ == "__main__":
    unittest.main()
