"""Console rendering tests for the Step-2 Shooting visibility receipt."""
from datetime import datetime, timezone

from step2_market_analysis import (
    FootprintMetrics,
    Level3Events,
    MacroMetrics,
    MarketSnapshot,
    NewsAndEvents,
    OrderFlowMetrics,
    TrendMetrics,
    VolumeProfileMetrics,
    VolatilityMetrics,
    format_snapshot,
)


def make_snapshot(receipt):
    return MarketSnapshot(
        timestamp=datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc),
        price=4200.0, bid=4199.9, ask=4200.1, volume=100.0,
        order_flow=OrderFlowMetrics(0, 0, 50, 50, 10, 10, 1, 0),
        footprint=FootprintMetrics(), level3=Level3Events(),
        volatility=VolatilityMetrics(), trend=TrendMetrics(),
        volume_profile=VolumeProfileMetrics(), macro=MacroMetrics(),
        news=NewsAndEvents(), symbol="GCZ6",
        shot={"visibility": receipt},
        shadow_candidates={"power_lead": {"shadow_only": True,
                                           "execution_enabled": False}})


def test_console_receipt_shows_force_model_gates_outcome_and_specific_checks():
    receipt = {
        "status": "WAIT", "direction": "UP", "force_shares": {"up": 72, "down": 28},
        "regime": "TREND", "signal_confidence": 68, "signal_strength": 81,
        "ai_model": "NOT_RUN_STEP3", "session_state": "LONDON | market_open=yes",
        "execution": {"mode": "python", "trading_enabled": True,
                      "allow_live_trading": False, "limit_order_enabled": False},
        "outcome": "NO_EXECUTABLE_PLAN", "replay_status": "NOT_RUN",
        "primary_reason": "A2 trigger absent",
        "sub_checks": ["A2 trigger: not confirmed", "passive route: SHADOW_ONLY"],
    }

    rendered = format_snapshot(make_snapshot(receipt))

    assert "SHOOTING RECEIPT:" in rendered
    assert "force=72/28" in rendered
    assert "regime=TREND" in rendered
    assert "AI=NOT_RUN_STEP3" in rendered
    assert "allow_live=False" in rendered
    assert "passive_limit=False" in rendered
    assert "outcome=NO_EXECUTABLE_PLAN" in rendered
    assert "A2 trigger: not confirmed" in rendered
    assert "SHADOW CANDIDATES:" in rendered


def test_malformed_nested_receipt_fields_do_not_break_console_formatting():
    rendered = format_snapshot(make_snapshot({
        "status": "WAIT", "execution": "unknown", "force_shares": None,
        "sub_checks": "queue estimate unavailable",
        "primary_reason": "malformed test receipt",
    }))
    assert "execution=UNKNOWN" in rendered
    assert "SHOOTING VETO SUB-CHECKS: queue estimate unavailable" in rendered
