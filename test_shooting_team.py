import time
from types import SimpleNamespace

import pytest

import shooting_team
from shooting_team import plan_shot
import config as _test_config
_test_config.LIMIT_ORDER_ENABLED = True  # G10: tests must not depend on the operator's .env


CFG = SimpleNamespace(
    LIMIT_ORDER_ENABLED=True,
    POWER_MAX_TICK_AGE_SECONDS=60.0,
    STALE_DATA_SECONDS=300.0,
    LOT_SIZE=1.0,
    SHOOT_DOOR_MARGIN=3.0,
    SHOOT_ROAD_NORMAL_MAX=2.0,
    SHOOT_PARTICIPATION_MAX=0.10,
    SHOOT_ROAD_HISTORY_MIN=12,
    SHOOT_ROAD_HISTORY_SECONDS=2700.0,
    SHOOT_QUEUE_MIN_FILL_PROB=0.70,
    SHOOT_MIN_RR=1.2,
    SHOOT_MIN_REWARD_USD=0.0,
    PM_TP_BUFFER_ATR=0.15,
    SHOOT_QUEUE_MAX_AGE_SECONDS=5.0,
    LIMIT_TICK_SIZE=0.1,
    SHOOT_PLAN_TTL_SECONDS=30.0,
)


@pytest.fixture(autouse=True)
def _seed_road_history():
    """crowdv2:v1 -- every test starts with a warm, quiet road history
    (12 samples of an empty road).  The crowd tests below re-seed their own
    history to build the scenario they are grading."""
    shooting_team._ROAD_HISTORY.clear()
    shooting_team._ROAD_HISTORY.extend(
        (time.time(), 0.0) for _ in range(12))
    yield


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
            "now": time.time(), "market_symbol": "GCZ6",
            "trade_lots": 1.0, "recent_volume": 1000.0}
    base.update(changes)
    return base


def seed_road(values):
    """crowdv2:v1 -- replace the road history with these samples."""
    shooting_team._ROAD_HISTORY.clear()
    shooting_team._ROAD_HISTORY.extend(
        (time.time(), float(v)) for v in values)


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


# ---- crowd v2: the Three Fair Questions (2026-10-16) -----------------------

def test_crowd_v2_warmup_fails_closed():
    """No road history yet -> WAIT with a warm-up reason (fail-closed)."""
    seed_road([])  # empty: below the 12-sample minimum
    out = plan_shot(smap(lots_between=160), power(), 100, 1, 0.2, CFG,
                    entry_context=ctx())
    assert out["shot"] == "WAIT"
    assert "warming up" in out["why"][0]


def test_crowd_v2_abnormal_road_waits():
    """Road 350 vs its own normal 100 = 3.5x > 2x -> Q2 veto."""
    seed_road([100.0] * 12)
    out = plan_shot(smap(lots_between=350), power(), 100, 1, 0.2, CFG,
                    entry_context=ctx())
    assert out["shot"] == "WAIT"
    assert "Q2" in out["why"][0]
    assert out["crowd_v2"]["road_ratio"] > 2.0


def test_crowd_v2_normal_road_passes():
    """Quiet tape: road 160 vs its own normal 100 = 1.6x -> pass, plan GO."""
    seed_road([100.0] * 12)
    out = plan_shot(smap(lots_between=160), power(), 100, 1, 0.2, CFG,
                    entry_context=ctx())
    assert out["shot"] == "GO"
    assert any("crowd v2" in w for w in out["why"])


def test_crowd_v2_small_door_waits():
    """Door 2 lots vs our shot 1 lot = 2x < 3x -> Q1 veto."""
    out = plan_shot(smap(size=2.0), power(), 100, 1, 0.2, CFG,
                    entry_context=ctx())
    assert out["shot"] == "WAIT"
    assert "Q1" in out["why"][0]


def test_crowd_v2_big_footprint_waits():
    """Shot 1 lot vs window volume 5 = 20% > 10% -> Q3 veto."""
    out = plan_shot(smap(), power(), 100, 1, 0.2, CFG,
                    entry_context=ctx(recent_volume=5.0))
    assert out["shot"] == "WAIT"
    assert "Q3" in out["why"][0]


def test_crowd_v2_missing_volume_fails_closed():
    out = plan_shot(smap(), power(), 100, 1, 0.2, CFG,
                    entry_context=ctx(recent_volume=None))
    assert out["shot"] == "WAIT"
    assert "volume window unavailable" in out["why"][0]


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


def test_boolean_price_is_not_accepted_as_numeric_market_data():
    out = plan_shot(smap(), power(), True, 1.0, 0.2, CFG,
                    entry_context=ctx())
    assert out["shot"] == "WAIT"
    assert "invalid" in out["why"][0]
    assert out["visibility"]["reference_price"] is None


def test_bad_quotes_do_not_pass():
    out = plan_shot(smap(), power(), 100, 1, 0.2, CFG,
                    entry_context=ctx(ask=99.8))
    assert out["shot"] == "WAIT"


def test_early_wait_paths_share_typed_visibility_receipts():
    context = ctx(regime="TREND", signal_confidence=67.0, signal_strength=81.0,
                  ai_model="NOT_RUN_STEP3", session_state="LONDON | market_open=yes",
                  execution_mode="python")
    cases = [
        plan_shot(smap(), power("NEITHER"), 100, 1, 0.2, CFG,
                  entry_context=context),
        plan_shot(smap(), power(), 100, 1, 0.0, CFG,
                  entry_context=context),
        plan_shot(smap(), power(), 100, 1, 0.2, CFG,
                  entry_context=ctx(**{**context, "ask": 99.8})),
        plan_shot({}, power(), 100, 1, 0.2, CFG, entry_context=context),
    ]
    for plan in cases:
        receipt = plan["visibility"]
        assert plan["shot"] == "WAIT"
        assert plan["veto_details"] is receipt
        assert receipt["status"] == "WAIT"
        assert receipt["plan_ttl_seconds"] == 30.0
        assert receipt["ai_model"] == "NOT_RUN_STEP3"
        assert receipt["session_state"] == "LONDON | market_open=yes"
        assert receipt["execution"]["mode"] == "python"
        assert receipt["signal_confidence"] == 67.0
        assert receipt["signal_strength"] == 81.0
        assert receipt["sub_checks"]
        assert "AI model=NOT_RUN_STEP3" in receipt["full_why_string"]


def test_disabled_passive_route_is_recorded_as_shadow_only_and_not_paper_filled():
    from paper_entry_simulator import reset_entry_simulator, simulate_entry

    config = SimpleNamespace(**vars(CFG))
    config.LIMIT_ORDER_ENABLED = False
    plan = plan_shot(smap(), power(), 100, 1, 0.2, config,
                     entry_context=ctx())

    assert plan["shot"] == "WAIT"
    passive = plan["entry_options"]["PASSIVE_LIMIT"]
    assert passive["eligible"] is False
    assert passive["candidate_eligible"] is True
    assert passive["shadow_only"] is True
    assert passive["policy_enabled"] is False
    assert passive["execution_enabled"] is False
    assert "SHADOW_ONLY" in passive["sub_check"]
    assert "A2 trigger not confirmed" in plan["entry_options"]["AGGRESSIVE_MARKET"]["sub_check"]
    candidate = plan["shadow_candidates"]["passive_limit"]
    assert candidate["candidate"] is True
    assert candidate["mode"] == "SHADOW_ONLY"
    assert candidate["executable"] is False
    assert candidate["entry"] is not None
    assert any("SHADOW_ONLY" in check
               for check in plan["visibility"]["sub_checks"])

    reset_entry_simulator()
    paper = simulate_entry(plan, 100.0, config=config, now=time.time())
    assert paper["status"] == "NO_PLAN"
    assert paper["pending"] is None
    assert paper["filled_plan"] is None
    reset_entry_simulator()
