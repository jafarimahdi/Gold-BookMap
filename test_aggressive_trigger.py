"""Fail-closed tests for the aggressive (marketable) entry impulse trigger.

The trigger must never fire on doubt, never fire while disabled, and must fire
with a real reason when every condition passes. Unit tests alone do not
authorise live orders (Law 7) — they mark the homework before paper validation.
"""
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from aggressive_trigger import a2_serve_eval, evaluate_aggressive_trigger

CONFIG = SimpleNamespace(MAX_SPREAD_PCT=0.05, POWER_MAX_TICK_AGE_SECONDS=60.0,
                         AGGRESSIVE_ENTRY_ENABLED=1)


def power_up(**over):
    direction = over.pop("direction", "UP")
    code = ("UPWARD_FORCE_DOMINATES" if direction == "UP"
            else "DOWNWARD_FORCE_DOMINATES")
    base = {"direction": direction, "decision": direction, "regime_gate": "PASS",
            "safety_gate": "PASS", "reason": code, "reason_code": code}
    base.update(over)
    return base


def ctx(**over):
    base = {"bid": 4195.8, "ask": 4196.1, "has_data": True, "data_quality_ok": True,
            "last_data_age_seconds": 5.0, "news_state": "QUIET"}
    base.update(over)
    return base


FLOW_AGREE_UP = {"buy_pct": 56.0, "sell_pct": 44.0}      # +12pp for UP
FLOW_AGREE_DOWN = {"buy_pct": 43.0, "sell_pct": 57.0}    # +14pp for DOWN


class TriggerDisabledTests(unittest.TestCase):
    def test_disabled_by_default_never_fires(self):
        cfg = SimpleNamespace(MAX_SPREAD_PCT=0.05, POWER_MAX_TICK_AGE_SECONDS=60.0)
        os.environ.pop("AGGRESSIVE_ENTRY_ENABLED", None)
        result = evaluate_aggressive_trigger(
            power=power_up(), shot_context=ctx(), config=cfg,
            price=4196.0, order_flow=FLOW_AGREE_UP)
        self.assertFalse(result["confirmed"])
        self.assertIn("disabled", result["reason"])

    def test_env_key_enables(self):
        os.environ["AGGRESSIVE_ENTRY_ENABLED"] = "1"
        try:
            result = evaluate_aggressive_trigger(
                power=power_up(), shot_context=ctx(), config=SimpleNamespace(),
                price=4196.0, order_flow=FLOW_AGREE_UP)
            self.assertTrue(result["confirmed"])
        finally:
            os.environ.pop("AGGRESSIVE_ENTRY_ENABLED", None)


class TriggerPowerGateTests(unittest.TestCase):
    def test_neither_never_triggers(self):
        result = evaluate_aggressive_trigger(
            power=power_up(direction="NEITHER", decision="NEITHER",
                           reason="WEAK_OR_NO_FORCE"),
            shot_context=ctx(), config=CONFIG, price=4196.0,
            order_flow=FLOW_AGREE_UP)
        self.assertFalse(result["confirmed"])
        self.assertIn("NEITHER", result["reason"])

    def test_regime_gate_fail_blocks(self):
        result = evaluate_aggressive_trigger(
            power=power_up(regime_gate="FAIL", reason="RANGE_NO_CONFIRMED_BREAKOUT"),
            shot_context=ctx(), config=CONFIG, price=4196.0,
            order_flow=FLOW_AGREE_UP)
        self.assertFalse(result["confirmed"])
        self.assertIn("regime_gate", result["reason"])

    def test_missing_gates_without_force_reason_blocks(self):
        result = evaluate_aggressive_trigger(
            power={"direction": "UP", "reason": "SOMETHING_ELSE"},
            shot_context=ctx(), config=CONFIG, price=4196.0,
            order_flow=FLOW_AGREE_UP)
        self.assertFalse(result["confirmed"])
        self.assertIn("fail-closed", result["reason"])

    def test_missing_gates_with_force_reason_passes(self):
        result = evaluate_aggressive_trigger(
            power={"direction": "UP", "reason": "UPWARD_FORCE_DOMINATES"},
            shot_context=ctx(), config=CONFIG, price=4196.0,
            order_flow=FLOW_AGREE_UP)
        self.assertTrue(result["confirmed"])

    def test_down_direction_with_down_flow_confirms(self):
        result = evaluate_aggressive_trigger(
            power=power_up(direction="DOWN", decision="DOWN",
                           reason="DOWNWARD_FORCE_DOMINATES"),
            shot_context=ctx(), config=CONFIG, price=4196.0,
            order_flow=FLOW_AGREE_DOWN)
        self.assertTrue(result["confirmed"])
        self.assertIn("DOWN", result["reason"])


class TriggerMarketTests(unittest.TestCase):
    def test_wide_spread_blocks(self):
        result = evaluate_aggressive_trigger(
            power=power_up(), shot_context=ctx(bid=4190.0, ask=4194.0),
            config=CONFIG, price=4196.0, order_flow=FLOW_AGREE_UP)
        self.assertFalse(result["confirmed"])
        self.assertIn("spread", result["reason"])

    def test_stale_tick_blocks(self):
        result = evaluate_aggressive_trigger(
            power=power_up(), shot_context=ctx(last_data_age_seconds=120.0),
            config=CONFIG, price=4196.0, order_flow=FLOW_AGREE_UP)
        self.assertFalse(result["confirmed"])
        self.assertIn("tick age", result["reason"])

    def test_news_blackout_blocks(self):
        result = evaluate_aggressive_trigger(
            power=power_up(), shot_context=ctx(news_state="BLACKOUT"),
            config=CONFIG, price=4196.0, order_flow=FLOW_AGREE_UP)
        self.assertFalse(result["confirmed"])
        self.assertIn("news", result["reason"])

    def test_weak_flow_edge_blocks(self):
        result = evaluate_aggressive_trigger(
            power=power_up(), shot_context=ctx(), config=CONFIG, price=4196.0,
            order_flow={"buy_pct": 50.5, "sell_pct": 49.5})
        self.assertFalse(result["confirmed"])
        self.assertIn("flow edge", result["reason"])

    def test_flow_edge_exactly_at_threshold_confirms(self):
        result = evaluate_aggressive_trigger(
            power=power_up(), shot_context=ctx(), config=CONFIG, price=4196.0,
            order_flow={"buy_pct": 51.0, "sell_pct": 49.0})
        self.assertTrue(result["confirmed"])

    def test_missing_flow_evidence_fails_closed(self):
        result = evaluate_aggressive_trigger(
            power=power_up(), shot_context=ctx(), config=CONFIG,
            price=4196.0, order_flow=None)
        self.assertFalse(result["confirmed"])
        self.assertIn("fail-closed", result["reason"])

    def test_l3_opposing_flow_blocks(self):
        result = evaluate_aggressive_trigger(
            power=power_up(), shot_context=ctx(), config=CONFIG, price=4196.0,
            order_flow=FLOW_AGREE_UP,
            level3={"aggressive_buy_volume": 100.0, "aggressive_sell_volume": 300.0})
        self.assertFalse(result["confirmed"])
        self.assertIn("L3", result["reason"])

    def test_aggressive_volumes_derive_edge_when_percentages_absent(self):
        result = evaluate_aggressive_trigger(
            power=power_up(), shot_context=ctx(), config=CONFIG, price=4196.0,
            order_flow={"aggressive_buy_volume": 1200.0,
                        "aggressive_sell_volume": 800.0})
        self.assertTrue(result["confirmed"])
        self.assertAlmostEqual(result["checks"]["flow_edge_pp"], 20.0)

    def test_confirmed_reason_is_human_readable(self):
        result = evaluate_aggressive_trigger(
            power=power_up(), shot_context=ctx(), config=CONFIG, price=4196.0,
            order_flow=FLOW_AGREE_UP)
        self.assertTrue(result["confirmed"])
        self.assertGreater(len(result["reason"]), 20)
        self.assertIn("aggressive UP impulse confirmed", result["reason"])


class A2ServeVisibilityTests(unittest.TestCase):
    def test_serve_lane_passes_decision_session_and_policy_context(self):
        served = SimpleNamespace(
            power_v2={"direction": "UP", "context": {"market_regime": "TREND"}},
            order_flow={}, signal_direction="BUY", signal_map={},
            confidence=68.0, signal_strength=74.0, regime="TREND",
            ai_model="fixture-model", news=SimpleNamespace(news_state="QUIET"))
        config = SimpleNamespace(
            AGGRESSIVE_ENTRY_ENABLED=1, EXECUTION_MODE="python",
            TRADING_ENABLED=False, ALLOW_LIVE_TRADING=False,
            LIMIT_ORDER_ENABLED=False)
        now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
        with patch.dict(os.environ, {"AGGRESSIVE_ENTRY_ENABLED": "1"}, clear=False), \
                patch("aggressive_trigger.a2_eval",
                      return_value={"fire": True, "reason": "fixture fire"}), \
                patch("shooting_team.plan_shot",
                      return_value={"shot": "WAIT", "why": ["fixture plan refusal"]}) as planner:
            result, notes = a2_serve_eval(
                served, {"has_data": True, "data_quality_ok": True,
                          "last_data_age_seconds": 1.0}, now, 100.0, 0.2,
                config, bid=99.9, ask=100.1, atr=1.0)

        context = planner.call_args.kwargs["entry_context"]
        self.assertIsNone(result)
        self.assertTrue(notes)
        self.assertEqual(context["regime"], "TREND")
        self.assertEqual(context["signal_direction"], "BUY")
        self.assertEqual(context["signal_confidence"], 68.0)
        self.assertEqual(context["signal_strength"], 74.0)
        self.assertEqual(context["ai_model"], "fixture-model")
        self.assertIn("market_open=", context["session_state"])
        self.assertEqual(context["execution_mode"], "python")
        self.assertFalse(context["trading_enabled"])
        self.assertFalse(context["allow_live_trading"])
        self.assertFalse(context["limit_order_enabled"])


class ProductionShapeTests(unittest.TestCase):
    """The live pipeline's real field names/shapes (caught 2 bugs in review)."""

    def test_reason_code_key_and_flow_object_confirm(self):
        power = {"direction": "UP", "up_power_pct": 100.0, "down_power_pct": 0.0,
                 "power_total_pct": 100.0, "reason_code": "UPWARD_FORCE_DOMINATES",
                 "context": {"market_regime": "TREND", "feed_stale": False,
                             "data_quality_ok": True},
                 "regime_diagnostics": {"market_regime": "TREND",
                                        "latest_tick_age_seconds": 5.0}}
        flow = SimpleNamespace(cvd=60, delta=60, aggressive_buys=936,
                               aggressive_sells=849,
                               aggressive_buy_volume=1100.0,
                               aggressive_sell_volume=900.0)
        result = evaluate_aggressive_trigger(
            power=power, shot_context=ctx(), config=CONFIG,
            price=4196.0, order_flow=flow)
        self.assertTrue(result["confirmed"])
        self.assertAlmostEqual(result["checks"]["flow_edge_pp"], 10.0)

    def test_reason_code_direction_mismatch_refused(self):
        power = {"direction": "DOWN", "reason_code": "UPWARD_FORCE_DOMINATES"}
        result = evaluate_aggressive_trigger(
            power=power, shot_context=ctx(), config=CONFIG, price=4196.0,
            order_flow=FLOW_AGREE_DOWN)
        self.assertFalse(result["confirmed"])
        self.assertIn("disagrees", result["reason"])


if __name__ == "__main__":
    unittest.main()
