from types import SimpleNamespace

from paper_entry_simulator import reset_entry_simulator, simulate_entry


CFG = SimpleNamespace(SHOOT_PAPER_LIMIT_TTL_SECONDS=300.0, LIMIT_TICK_SIZE=0.1)


def limit_plan():
    return {"shot": "GO", "side": "BUY", "entry": 99.9,
            "entry_style": "PASSIVE_LIMIT", "order_type": "LIMIT",
            "execution_status": "PLAN_ONLY", "created_at": 1000.0,
            "valid_until": 2000.0, "door": {"price": 105.0},
            "target": 104.8, "stop": 97.9}


def aggressive_plan():
    return {"shot": "GO", "side": "BUY", "entry": 100.1,
            "entry_style": "AGGRESSIVE_MARKET", "order_type": "MARKET",
            "execution_status": "PLAN_ONLY", "created_at": 1000.0,
            "valid_until": 2000.0, "door": {"price": 105.0},
            "target": 104.8, "stop": 97.9,
            "entry_options": {"AGGRESSIVE_MARKET": {
                "eligible": True, "trigger_reason": "independent confirmed impulse"}}}


def setup_function():
    reset_entry_simulator()


def test_limit_is_pending_until_trade_through():
    first = simulate_entry(limit_plan(), 100.0, config=CFG, now=1000.0)
    assert first["status"] == "PENDING"
    assert first["filled_plan"] is None

    touch = simulate_entry(limit_plan(), 99.9, config=CFG, now=1001.0)
    assert touch["status"] == "PENDING"
    assert touch["filled_plan"] is None

    filled = simulate_entry(limit_plan(), 99.8, config=CFG, now=1002.0)
    assert filled["status"] == "FILLED"
    assert filled["filled_plan"]["entry_filled"] is True
    assert filled["filled_plan"]["entry"] == 99.9
    assert filled["filled_plan"]["execution_status"] == "PAPER_ONLY"


def test_repeated_go_does_not_duplicate_same_fill():
    simulate_entry(limit_plan(), 100.0, config=CFG, now=1000.0)
    first_fill = simulate_entry(limit_plan(), 99.8, config=CFG, now=1001.0)
    assert first_fill["status"] == "FILLED"
    again = simulate_entry(limit_plan(), 99.7, config=CFG, now=1002.0)
    assert again["status"] == "ALREADY_FILLED"
    assert again["filled_plan"] is None


def test_no_go_cancels_pending():
    simulate_entry(limit_plan(), 100.0, config=CFG, now=1000.0)
    cancelled = simulate_entry({"shot": "WAIT"}, 100.0, config=CFG, now=1001.0)
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["pending"] is None


def test_changed_plan_replaces_old_pending_limit_and_bracket():
    simulate_entry(limit_plan(), 100.0, config=CFG, now=1000.0)
    changed = limit_plan()
    changed.update(entry=99.8, target=104.7, stop=97.8)
    replaced = simulate_entry(changed, 100.0, config=CFG, now=1001.0)
    assert replaced["status"] == "PENDING"
    assert replaced["pending"]["entry"] == 99.8
    assert simulate_entry(changed, 99.7, config=CFG, now=1002.0)["filled_plan"]["entry"] == 99.8


def test_invalid_current_plan_cancels_pending_order():
    simulate_entry(limit_plan(), 100.0, config=CFG, now=1000.0)
    invalid = limit_plan()
    invalid["stop"] = 100.2
    out = simulate_entry(invalid, 100.0, config=CFG, now=1001.0)
    assert out["status"] == "CANCELLED"
    assert out["pending"] is None
    assert out["filled_plan"] is None


def test_limit_expiry_cancels_without_fill():
    simulate_entry(limit_plan(), 100.0, config=CFG, now=1000.0)
    expired = simulate_entry(limit_plan(), 100.0, config=CFG, now=1300.0)
    assert expired["status"] == "CANCELLED"
    assert expired["filled_plan"] is None


def test_expired_plan_and_market_drift_fail_closed():
    expired = limit_plan()
    expired["valid_until"] = 999.0
    out = simulate_entry(expired, 100.0, config=CFG, now=1000.0)
    assert out["status"] == "WAIT"
    assert "expired" in out["reason"]

    drifting = limit_plan()
    drifting.update(reference_price=100.0, atr=1.0, spread=0.2)
    out = simulate_entry(drifting, 101.0, config=CFG, now=1000.0)
    assert out["status"] == "WAIT"
    assert "revalidation" in out["reason"]


def test_aggressive_requires_explicit_eligible_trigger():
    invalid = aggressive_plan()
    invalid["entry_options"]["AGGRESSIVE_MARKET"]["eligible"] = False
    out = simulate_entry(invalid, 100.0, config=CFG, now=1000.0)
    assert out["status"] == "WAIT"
    assert out["filled_plan"] is None


def test_aggressive_is_paper_fill_only():
    out = simulate_entry(aggressive_plan(), 100.0, config=CFG, now=1000.0)
    assert out["status"] == "FILLED"
    assert out["filled_plan"]["paper_fill_model"] == "aggressive_quote_estimate"
    assert out["filled_plan"]["execution_status"] == "PAPER_ONLY"
