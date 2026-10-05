from types import SimpleNamespace
import time

from shooting_team import plan_shot


CFG = SimpleNamespace(
    POWER_MAX_TICK_AGE_SECONDS=60.0,
    STALE_DATA_SECONDS=300.0,
    SHOOT_ROAD_RATIO=6.0,
    SHOOT_QUEUE_MIN_FILL_PROB=0.70,
    SHOOT_MIN_RR=1.2,
    SHOOT_MIN_REWARD_USD=0.0,
    PM_TP_BUFFER_ATR=0.15,
    SHOOT_QUEUE_MAX_AGE_SECONDS=5.0,
    LIMIT_TICK_SIZE=0.1,
    SHOOT_PLAN_TTL_SECONDS=30.0,
)


def power(direction="UP"):
    return {
        "team": "POWER", "version": "2.2.0", "timeframe": "M5",
        "direction": direction,
        "up_power_pct": 70.0 if direction == "UP" else 30.0,
        "down_power_pct": 30.0 if direction == "UP" else 70.0,
        "power_total_pct": 100.0,
        "reason_code": "UPWARD_FORCE_DOMINATES" if direction == "UP" else "DOWNWARD_FORCE_DOMINATES",
        "context": {"market_regime": "TREND", "data_quality_ok": True, "feed_stale": False},
        "regime_diagnostics": {"market_regime": "TREND", "latest_tick_age_seconds": 2.0},
        "shadow_only": False,
    }


def smap(direction="UP", **door_changes):
    door = {"price": 105.0 if direction == "UP" else 95.0,
            "size": 20.0, "doors_between": 0, "lots_between": 0.0,
            "biggest_in_path": None, "road_clear": True}
    door.update(door_changes)
    return {"above" if direction == "UP" else "below": [door]}


def ctx(**changes):
    base = {"bid": 99.9, "ask": 100.1, "news_state": "QUIET",
            "data_quality_ok": True, "has_data": True,
            "last_data_age_seconds": 1.0, "aggressive_trigger_confirmed": False,
            "now": time.time(), "market_symbol": "GCZ6"}
    base.update(changes)
    return base


def valid_queue(fill_prob=0.30, direction="UP", **changes):
    base = {"fill_prob": fill_prob, "queue_total_vol": 25.0,
            "queue_ahead_vol": 20.0, "price": 99.9 if direction == "UP" else 100.1,
            "side": "BUY" if direction == "UP" else "SELL",
            "timestamp": time.time(), "symbol": "GCZ6",
            "quote_bid": 99.9, "quote_ask": 100.1}
    base.update(changes)
    return base


def test_neither_fails_closed():
    out = plan_shot(smap(), power("NEITHER"), 100, 1, 0.2, CFG,
                    entry_context=ctx())
    assert out["shot"] == "WAIT"
    assert out["side"] is None


def test_legacy_power_is_not_accepted():
    out = plan_shot(smap(), {"pick": "above", "confidence": 0.99, "push": 0.9},
                    100, 1, 0.2, CFG, entry_context=ctx())
    assert out["shot"] == "WAIT"


def test_power_data_quality_and_news_block():
    bad = power()
    bad["regime_diagnostics"]["latest_tick_age_seconds"] = 90
    assert plan_shot(smap(), bad, 100, 1, 0.2, CFG,
                     entry_context=ctx())["shot"] == "WAIT"
    assert plan_shot(smap(), power(), 100, 1, 0.2, CFG,
                     entry_context=ctx(news_state="BLACKOUT"))["shot"] == "WAIT"
    missing_quality = power()
    missing_quality["context"].pop("data_quality_ok")
    assert plan_shot(smap(), missing_quality, 100, 1, 0.2, CFG,
                     entry_context=ctx())["shot"] == "WAIT"
    missing_freshness = power()
    missing_freshness["context"].pop("feed_stale")
    assert plan_shot(smap(), missing_freshness, 100, 1, 0.2, CFG,
                     entry_context=ctx())["shot"] == "WAIT"


def test_missing_market_quality_or_age_fails_closed():
    assert plan_shot(smap(), power(), 100, 1, 0.2, CFG,
                     entry_context=ctx(data_quality_ok=None))["shot"] == "WAIT"
    assert plan_shot(smap(), power(), 100, 1, 0.2, CFG,
                     entry_context=ctx(last_data_age_seconds=None))["shot"] == "WAIT"


def test_news_state_missing_or_warning_fails_closed():
    for values in ({"news_state": None}, {"news_state": "UNKNOWN"},
                   {"news_state": "WARNING"}):
        out = plan_shot(smap(), power(), 100, 1, 0.2, CFG,
                        entry_context=ctx(**values))
        assert out["shot"] == "WAIT"


def test_buy_limit_plan_has_both_advisory_styles_and_expiry():
    out = plan_shot(smap(), power(), 100, 1, 0.2, CFG, entry_context=ctx())
    assert out["shot"] == "GO"
    assert out["side"] == "BUY"
    assert out["entry_style"] == "PASSIVE_LIMIT"
    assert out["order_type"] == "LIMIT"
    assert out["entry"] == 99.9
    assert out["target"] > out["entry"] > out["stop"]
    assert out["stop_source"] == "ATR_FALLBACK"
    assert out["execution_status"] == "PLAN_ONLY"
    assert out["entry_options"]["PASSIVE_LIMIT"]["eligible"] is True
    assert out["entry_options"]["PASSIVE_LIMIT"]["order_type"] == "LIMIT"
    assert out["entry_options"]["AGGRESSIVE_MARKET"]["order_type"] == "MARKET"
    assert out["entry_options"]["AGGRESSIVE_MARKET"]["geometry_eligible"] is True
    assert out["entry_options"]["AGGRESSIVE_MARKET"]["eligible"] is False
    assert out["valid_until"] > out["created_at"]
    assert out["portfolio_risk_status"] == "NOT_ASSESSED"


def test_invalid_queue_estimate_is_unknown_not_trusted():
    malformed = valid_queue(fill_prob=1.5)
    out = plan_shot(smap(), power(), 100, 1, 0.2, CFG,
                    entry_context=ctx(queue_estimate=malformed))
    assert out["shot"] == "GO"
    assert out["queue_estimate"] is None
    assert out["queue_validation"] == "queue estimate has an invalid fill probability"
    assert out["entry_style"] == "PASSIVE_LIMIT"


def test_queue_estimate_stale_or_quote_misaligned_is_unknown():
    stale = valid_queue(timestamp=time.time() - 100)
    out = plan_shot(smap(), power(), 100, 1, 0.2, CFG,
                    entry_context=ctx(queue_estimate=stale))
    assert out["queue_estimate"] is None
    assert "stale" in out["queue_validation"]
    mismatch = valid_queue(quote_bid=98.0)
    out2 = plan_shot(smap(), power(), 100, 1, 0.2, CFG,
                     entry_context=ctx(queue_estimate=mismatch))
    assert out2["queue_estimate"] is None
    assert "snapshot" in out2["queue_validation"]


def test_sell_limit_plan_geometry():
    out = plan_shot(smap("DOWN"), power("DOWN"), 100, 1, 0.2, CFG,
                    entry_context=ctx())
    assert out["shot"] == "GO"
    assert out["side"] == "SELL"
    assert out["entry_style"] == "PASSIVE_LIMIT"
    assert out["target"] < out["entry"] < out["stop"]


def test_nearest_viable_door_is_used_not_attraction_order():
    doors = {"above": [
        {"price": 110, "size": 30, "doors_between": 0, "lots_between": 0},
        {"price": 103, "size": 10, "doors_between": 0, "lots_between": 0},
    ]}
    out = plan_shot(doors, power(), 100, 1, 0.2, CFG, entry_context=ctx())
    assert out["door"]["price"] == 103


def test_crowded_route_waits():
    out = plan_shot(smap(lots_between=160), power(), 100, 1, 0.2, CFG,
                    entry_context=ctx())
    assert out["shot"] == "WAIT"
    assert "crowded" in out["why"][0]


def test_poor_queue_waits_unless_triggered_aggressive():
    poor_queue = valid_queue()
    out = plan_shot(smap(), power(), 100, 1, 0.2, CFG,
                    entry_context=ctx(queue_estimate=poor_queue))
    assert out["shot"] == "WAIT"
    assert out["entry_options"]["PASSIVE_LIMIT"]["eligible"] is False
    assert out["entry_options"]["AGGRESSIVE_MARKET"]["eligible"] is False
    assert out["entry_options"]["AGGRESSIVE_MARKET"]["geometry_eligible"] is True
    triggered = plan_shot(
        smap(), power(), 100, 1, 0.2, CFG,
        entry_context=ctx(queue_estimate=poor_queue,
                          aggressive_trigger_confirmed=True,
                          aggressive_trigger_reason="independent impulse trigger"))
    assert triggered["shot"] == "GO"
    assert triggered["entry_style"] == "AGGRESSIVE_MARKET"
    assert triggered["entry"] == 100.1


def test_aggressive_trigger_requires_a_reason():
    poor_queue = valid_queue()
    out = plan_shot(smap(), power(), 100, 1, 0.2, CFG,
                    entry_context=ctx(queue_estimate=poor_queue,
                                      aggressive_trigger_confirmed=True))
    assert out["shot"] == "WAIT"


def test_aggressive_entry_requires_explicitly_clear_scout_route():
    poor_queue = valid_queue()
    out = plan_shot(
        smap(road_clear=False), power(), 100, 1, 0.2, CFG,
        entry_context=ctx(queue_estimate=poor_queue,
                          aggressive_trigger_confirmed=True,
                          aggressive_trigger_reason="independent impulse trigger"))
    assert out["shot"] == "WAIT"
    assert any("clear route" in reason for reason in out["why"])


def test_missing_scout_route_data_fails_closed():
    door = {"price": 105.0, "size": 20.0}
    out = plan_shot({"above": [door]}, power(), 100, 1, 0.2, CFG,
                    entry_context=ctx())
    assert out["shot"] == "WAIT"
    assert "route obstruction data is missing" in out["why"][0]


def test_bad_quotes_do_not_pass():
    out = plan_shot(smap(), power(), 100, 1, 0.2, CFG,
                    entry_context=ctx(ask=99.8))
    assert out["shot"] == "WAIT"
