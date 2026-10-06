"""Full-cycle paper validation tests (Law 7 homework).

Proves the four teams now work TOGETHER end to end in paper mode:

    POWER (green, directional)  ->  SHOOTER: GO AGGRESSIVE_MARKET
    ->  PAPER ENTRY: FILLED (aggressive quote estimate)
    ->  ESCORT: wakes, guards, closes on TARGET, double-scored

Plus the discipline tests: trigger off -> no cycle at all; passive entries stay
disabled while LIMIT_ORDER_ENABLED=0 (user decision 2026-10-06); escort One Law
(never widens a stop, never adds size).

NOTE: `test_passive_disabled_requires_limit_order_flag` requires Patch 2 from
AGGRESSIVE_TRIGGER_BUILD_PLAN.md to be applied to shooting_team.py first.

These tests mark the homework; they never place orders. The chain is paper-only:
shooter plans, simulator estimates fills, escort sends nothing.
"""
import time
import unittest
from types import SimpleNamespace

from aggressive_trigger import evaluate_aggressive_trigger
from shooting_team import plan_shot, describe as shot_describe
from paper_entry_simulator import simulate_entry, reset_entry_simulator
from escort_team import escort_cycle, describe as escort_describe, get_escort

CONFIG = SimpleNamespace(
    MAX_SPREAD_PCT=0.05, POWER_MAX_TICK_AGE_SECONDS=60.0, STALE_DATA_SECONDS=300.0,
    AGGRESSIVE_ENTRY_ENABLED=1, AGGRESSIVE_FLOW_EDGE=2.0, AGGRESSIVE_SPREAD_MAX_PCT=0.05,
    PM_TP_BUFFER_ATR=0.15, SHOOT_MIN_RR=1.2, SHOOT_MIN_REWARD_USD=0.0,
    SHOOT_QUEUE_MIN_FILL_PROB=0.70, LIMIT_ORDER_ENABLED=False,
    LIMIT_TICK_SIZE=0.1, SHOOT_PAPER_LIMIT_TTL_SECONDS=300.0,
    LOT_SIZE=0.01, QUEUE_POS_ENABLED=True,
)

PRICE = 4196.0
BID, ASK = 4195.8, 4196.1
ATR = 2.0
SPREAD = 0.3

POWER_GREEN_UP = {
    "direction": "UP", "team": "POWER", "shadow_only": False,
    "reason_code": "UPWARD_FORCE_DOMINATES", "reason": "UPWARD_FORCE_DOMINATES",
    "up_power_pct": 100.0, "down_power_pct": 0.0, "power_total_pct": 100.0,
    "context": {"market_regime": "TREND", "feed_stale": False, "data_quality_ok": True},
    "regime_diagnostics": {"market_regime": "TREND", "latest_tick_age_seconds": 5.0,
                           "reason": "ADX_TREND"},
}
SIGNAL_MAP = {"above": [{"price": 4205.0, "size": 20.0, "biggest_in_path": 20.0,
                         "doors_between": 0, "lots_between": 0.0, "road_clear": True}],
              "below": [{"price": 4185.0, "size": 20.0, "biggest_in_path": 20.0,
                         "doors_between": 0, "lots_between": 0.0, "road_clear": True}]}
FLOW_AGREE_UP = {"buy_pct": 56.0, "sell_pct": 44.0}
QUEUE_POOR = {"fill_prob": 0.50, "queue_total_vol": 100.0, "timestamp": 0.0,
              "symbol": "TEST", "quote_bid": BID, "quote_ask": ASK}
QUEUE_GOOD = {"fill_prob": 0.90, "queue_total_vol": 100.0, "timestamp": 0.0,
              "symbol": "TEST", "quote_bid": BID, "quote_ask": ASK}


def make_ctx(queue=None, **over):
    base = {"bid": BID, "ask": ASK, "news_state": "QUIET", "has_data": True,
            "last_data_age_seconds": 5.0, "data_quality_ok": True,
            "queue_estimate": queue, "queue_enabled": True,
            "market_symbol": "TEST", "now": time.time()}
    base.update(over)
    return base


def stamp_trigger(ctx, config, power):
    """Run the real trigger and stamp the context exactly like the step2 patch."""
    result = evaluate_aggressive_trigger(
        power=power, shot_context=ctx, config=config, price=PRICE, atr=ATR,
        order_flow=FLOW_AGREE_UP, level3=None, signal_map=SIGNAL_MAP)
    ctx["aggressive_trigger_confirmed"] = bool(result["confirmed"])
    ctx["aggressive_trigger_reason"] = str(result.get("reason") or "")
    return result


class FullCycleTests(unittest.TestCase):
    def setUp(self):
        reset_entry_simulator()
        book = get_escort()
        book.open.clear()
        book.done.clear()

    def test_full_cycle_power_to_shooting_to_paper_fill_to_escort(self):
        ctx = make_ctx(queue=QUEUE_POOR)  # poor queue -> aggressive is the chosen style
        trig = stamp_trigger(ctx, CONFIG, POWER_GREEN_UP)
        self.assertTrue(trig["confirmed"])

        plan = plan_shot(SIGNAL_MAP, POWER_GREEN_UP, PRICE, ATR, spread=SPREAD,
                         config=CONFIG, book=None, entry_context=ctx)
        self.assertEqual(plan.get("shot"), "GO")
        self.assertEqual(plan.get("entry_style"), "AGGRESSIVE_MARKET")
        self.assertIn("aggressive UP impulse confirmed", " ".join(plan.get("why", [])))
        self.assertIn("GO", shot_describe(plan))

        sim = simulate_entry(plan, PRICE, config=CONFIG, now=time.time())
        self.assertEqual(sim.get("status"), "FILLED")
        self.assertEqual(sim.get("fill_price"), plan["entry"])
        filled = sim.get("filled_plan") or {}
        self.assertTrue(filled.get("entry_filled"))
        self.assertEqual(filled.get("execution_status"), "PAPER_ONLY")
        self.assertEqual(filled.get("paper_fill_model"), "aggressive_quote_estimate")

        out = escort_cycle(filled, SIGNAL_MAP, PRICE, ATR, SPREAD,
                           order_flow=FLOW_AGREE_UP, footprint=None, level3=None,
                           news=None, divergence=0.0, book=None, config=CONFIG,
                           bid=BID, ask=ASK)
        self.assertEqual(out.get("watching"), 1)
        self.assertIn("guarding", escort_describe(out))
        trade = get_escort().open[0]
        self.assertEqual(trade.side, "BUY")
        first_stop = trade.stop

        # adverse tick first: escort must NOT widen the stop (One Law)
        escort_cycle({}, SIGNAL_MAP, PRICE - 1.0, ATR, SPREAD,
                     order_flow={"buy_pct": 40.0, "sell_pct": 60.0}, footprint=None,
                     level3=None, news=None, divergence=0.0, book=None, config=CONFIG,
                     bid=PRICE - 1.2, ask=PRICE - 0.9)
        self.assertGreaterEqual(get_escort().open[0].stop, first_stop)

        # price reaches the target -> closed on TARGET, double-scored
        out2 = escort_cycle({}, SIGNAL_MAP, 4205.0, ATR, SPREAD,
                            order_flow=FLOW_AGREE_UP, footprint=None, level3=None,
                            news=None, divergence=0.0, book=None, config=CONFIG,
                            bid=4205.0, ask=4205.3)
        self.assertEqual(out2.get("watching"), 0)
        done = get_escort().done
        self.assertTrue(done)
        fin = done[-1]
        self.assertIn("r", fin)
        self.assertIn("r_if_left_alone", fin)  # double scoring: escort vs do-nothing
        self.assertIn("asleep", escort_describe(out2))

    def test_trigger_off_means_no_cycle_at_all(self):
        cfg = SimpleNamespace(**{**CONFIG.__dict__, "AGGRESSIVE_ENTRY_ENABLED": 0})
        ctx = make_ctx(queue=QUEUE_POOR)
        trig = stamp_trigger(ctx, cfg, POWER_GREEN_UP)
        self.assertFalse(trig["confirmed"])

        plan = plan_shot(SIGNAL_MAP, POWER_GREEN_UP, PRICE, ATR, spread=SPREAD,
                         config=cfg, book=None, entry_context=ctx)
        self.assertEqual(plan.get("shot"), "WAIT")
        sim = simulate_entry(plan, PRICE, config=cfg, now=time.time())
        self.assertEqual(sim.get("status"), "NO_PLAN")
        self.assertIsNone(sim.get("filled_plan"))

    def test_passive_disabled_requires_limit_order_flag(self):
        # GOOD queue would normally prefer PASSIVE_LIMIT; with
        # LIMIT_ORDER_ENABLED=False (user decision: aggressive-only openings)
        # the chooser must pick AGGRESSIVE_MARKET. Requires Patch 2.
        ctx = make_ctx(queue=QUEUE_GOOD)
        stamp_trigger(ctx, CONFIG, POWER_GREEN_UP)
        plan = plan_shot(SIGNAL_MAP, POWER_GREEN_UP, PRICE, ATR, spread=SPREAD,
                         config=CONFIG, book=None, entry_context=ctx)
        self.assertEqual(plan.get("shot"), "GO")
        self.assertEqual(plan.get("entry_style"), "AGGRESSIVE_MARKET")

    def test_escort_never_widens_stop_or_adds_size(self):
        ctx = make_ctx(queue=QUEUE_POOR)
        stamp_trigger(ctx, CONFIG, POWER_GREEN_UP)
        plan = plan_shot(SIGNAL_MAP, POWER_GREEN_UP, PRICE, ATR, spread=SPREAD,
                         config=CONFIG, book=None, entry_context=ctx)
        sim = simulate_entry(plan, PRICE, config=CONFIG, now=time.time())
        filled = sim["filled_plan"]
        escort_cycle(filled, SIGNAL_MAP, PRICE, ATR, SPREAD, bid=BID, ask=ASK,
                     config=CONFIG)
        trade = get_escort().open[0]
        stop0, size0 = trade.stop, getattr(trade, "size", None)
        for drift in (-2.0, -1.0, 0.5, -0.5, 1.0):
            escort_cycle({}, SIGNAL_MAP, PRICE + drift, ATR, SPREAD,
                         order_flow={"buy_pct": 30.0, "sell_pct": 70.0},
                         footprint=None, level3=None,
                         news=None, divergence=-1.0, book=None, config=CONFIG,
                         bid=PRICE + drift - 0.2, ask=PRICE + drift + 0.1)
            if not get_escort().open:
                break  # a protective close is always allowed (One Law direction)
            t = get_escort().open[0]
            self.assertGreaterEqual(t.stop, stop0)   # never wider
            if size0 is not None:
                self.assertEqual(getattr(t, "size", size0), size0)  # never bigger


if __name__ == "__main__":
    unittest.main()
