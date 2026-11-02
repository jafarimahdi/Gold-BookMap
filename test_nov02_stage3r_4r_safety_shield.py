from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import config
import main
import step2_market_analysis as s2
import trade_guard
import trade_history
from tools import raw_replay as rr


def _build_bookmap_md(
    now_dt: datetime,
    *,
    mbo_mode: str = "fresh",
    trade_prints: str = "available",
) -> dict:
    bid = 4100.0
    ask = 4100.2
    mid = 0.5 * (bid + ask)
    spread_pct = (ask - bid) / mid * 100.0
    order_events = []
    if mbo_mode == "fresh":
        order_events = [
            {
                "order_id": "mbo_1",
                "type": "NEW",
                "side": "SELL",
                "price": 4100.2,
                "size": 12.0,
                "is_market_order": False,
                "timestamp": (now_dt - timedelta(seconds=2)).isoformat(),
            },
            {
                "order_id": "mbo_2",
                "type": "FILL",
                "side": "SELL",
                "price": 4100.0,
                "size": 4.0,
                "is_market_order": True,
                "timestamp": (now_dt - timedelta(seconds=1)).isoformat(),
            },
        ]
    elif mbo_mode == "stale_oct15":
        # Exact 2026-10-15 failure mode: morning MBO events remain in ring buffer hours later
        order_events = [
            {
                "order_id": "mbo_old",
                "type": "NEW",
                "side": "SELL",
                "price": 4115.0,
                "size": 10.0,
                "is_market_order": False,
                "timestamp": (now_dt - timedelta(hours=3)).isoformat(),
            }
        ]

    l3_ev_raw = "partial_window" if order_events else "unavailable"
    return {
        "has_data": True,
        "symbol": "GC 12-26",
        "data_symbol": "GC 12-26",
        "price": 4100.1,
        "bid": bid,
        "ask": ask,
        "spread_pct": spread_pct,
        "last_data_age_seconds": 0.5,
        "last_trade_age_seconds": 0.5,
        "quote_age_seconds": 0.5,
        "depth_age_seconds": 0.5,
        "quote_timestamp": (now_dt - timedelta(milliseconds=500)).isoformat(),
        "depth_timestamp": (now_dt - timedelta(milliseconds=500)).isoformat(),
        "bid_depth": {4100.0: 15.0, 4099.9: 12.0, 4094.0: 55.0},
        "ask_depth": {4100.2: 14.0, 4100.3: 11.0},
        "order_book": {
            "bids": [(4100.0, 15.0), (4099.9, 12.0), (4094.0, 55.0)],
            "asks": [(4100.2, 14.0), (4100.3, 11.0)],
        },
        "order_events": order_events,
        "data_quality": {
            "quote": "valid",
            "level2": "validated",
            "level3": "available" if order_events else "unavailable",
            "level3_events": l3_ev_raw,
            "trade_prints": trade_prints,
            "instrument": "matched",
        },
        "l2_diagnostics": {
            "status": "validated",
            "instrument_match": True,
            "best_bid": bid,
            "best_ask": ask,
            "fresh_bid_levels": 3,
            "fresh_ask_levels": 2,
        },
        "tick_data": [
            {
                "price": 4100.5 - 0.01 * i,
                "volume": 3.0,
                "side": "SELL",
                "is_direct": True,
                "timestamp": (now_dt - timedelta(seconds=45 - i)).isoformat(),
            }
            for i in range(40)
        ],
        "candles": {
            "open": [4115.0 - 0.20 * i for i in range(75)],
            "high": [4115.4 - 0.20 * i for i in range(75)],
            "low": [4114.6 - 0.20 * i for i in range(75)],
            "close": [4114.8 - 0.20 * i for i in range(75)],
            "volume": [60.0 for _ in range(75)],
        },
        "macro": {},
        "news": {"headlines": [], "events": [], "fetch_calendar": False},
    }


def test_stage3r_4r_defaults_and_law11_timeframe_cooldown_calibration(monkeypatch):
    with monkeypatch.context() as mp:
        for k in (
            "TIMEFRAME",
            "COOLDOWN_MINUTES",
            "STAGE3R_MBO_FEED_GUARD_ENABLED",
            "STAGE4R_POST_EXIT_COOLDOWN_ENABLED",
            "STAGE4R_POST_EXIT_COOLDOWN_MINUTES",
            "STAGE4R_DAILY_LOSS_BREAKER_ENABLED",
            "STAGE4R_DAILY_MAX_LOSS_PTS",
        ):
            mp.delenv(k, raising=False)

        mp.setenv("TIMEFRAME", "M3")
        config._refresh()
        assert config.STAGE3R_MBO_FEED_GUARD_ENABLED is True
        assert config.STAGE4R_POST_EXIT_COOLDOWN_ENABLED is True
        assert config.STAGE4R_POST_EXIT_COOLDOWN_MINUTES == pytest.approx(3.0)
        assert config.COOLDOWN_MINUTES == 3
        assert config.STAGE4R_DAILY_LOSS_BREAKER_ENABLED is True
        assert config.STAGE4R_DAILY_MAX_LOSS_PTS == pytest.approx(12.0)

        mp.setenv("TIMEFRAME", "M5")
        config._refresh()
        assert config.STAGE4R_POST_EXIT_COOLDOWN_MINUTES == pytest.approx(5.0)
        assert config.COOLDOWN_MINUTES == 5

        mp.setenv("TIMEFRAME", "M15")
        config._refresh()
        assert config.STAGE4R_POST_EXIT_COOLDOWN_MINUTES == pytest.approx(15.0)

        mp.setenv("STAGE4R_POST_EXIT_COOLDOWN_MINUTES", "4.5")
        config._refresh()
        assert config.STAGE4R_POST_EXIT_COOLDOWN_MINUTES == pytest.approx(4.5)
    config._refresh()


def test_stage3r_blocks_unplugged_or_stale_mbo_while_preserving_fresh_mbo_and_synthetic(monkeypatch, tmp_path):
    now_dt = datetime(2026, 10, 15, 13, 30, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(s2.time, "time", lambda: now_dt.timestamp())
    monkeypatch.setattr(main.time, "time", lambda: now_dt.timestamp())

    for mod in {config, getattr(s2, "config", config), getattr(main, "config", config)}:
        monkeypatch.setattr(mod, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(mod, "DATA_SOURCE", "bookmapbridge", raising=False)
        monkeypatch.setattr(mod, "TIMEFRAME", "M3", raising=False)
        monkeypatch.setattr(mod, "BASE_TF_MINUTES", 3, raising=False)
        monkeypatch.setattr(mod, "CONFIDENCE_THRESHOLD", 35.0, raising=False)
        monkeypatch.setattr(mod, "AI_CONFIDENCE_THRESHOLD", 35.0, raising=False)
        monkeypatch.setattr(mod, "STALE_DATA_SECONDS", 300.0, raising=False)
        monkeypatch.setattr(mod, "MAX_SPREAD_PCT", 0.05, raising=False)
        monkeypatch.setattr(mod, "STAGE3R_MBO_FEED_GUARD_ENABLED", True, raising=False)
        monkeypatch.setattr(mod, "FAMILY_GATE_LIVE_ENABLED", True, raising=False)
        monkeypatch.setattr(mod, "STAGE3B_GATE_LIVE_ENABLED", True, raising=False)
        monkeypatch.setattr(mod, "STAGE3C_SESSION_GATE_LIVE_ENABLED", True, raising=False)
        monkeypatch.setattr(mod, "STAGE3D_SNIPER_GATE_LIVE_ENABLED", True, raising=False)

    monkeypatch.setattr(
        s2.SignalEngine,
        "aggregate",
        lambda self, *a, **k: (
            -58.0,
            "SELL",
            72.0,
            [
                "MTF M9:DOWN M36:DOWN -> SELL w 0.50",
                "L3 NET FLOW SELL -60.0 (buys 5.0 vs sells 65.0) w 1.00",
            ],
        ),
    )

    # 1) Unplugged MBO (`order_events == []`) while `trade_prints == "available"` -> BLOCKED
    rr._reset_step2_singletons()
    md_empty = _build_bookmap_md(now_dt, mbo_mode="empty")
    snap_empty = s2.analyze_market(md_empty, now=now_dt)
    s3r_empty = snap_empty.shadow_candidates["stage3r_gate"]
    assert s3r_empty["ok"] is False
    assert s3r_empty["live_capped"] is True
    assert s3r_empty["reason"] == "mbo_feed_unavailable"
    assert snap_empty.confidence <= 30.0
    assert snap_empty.shot.get("shot", "WAIT") == "WAIT"
    pf_empty = main._preflight_data_reason(md_empty)
    assert pf_empty is not None and "MBO Level-3 feed unplugged/stale" in pf_empty

    # 2) Frozen/stale MBO (`2026-10-15` ring buffer >3h old) while trades print -> BLOCKED
    rr._reset_step2_singletons()
    md_stale = _build_bookmap_md(now_dt, mbo_mode="stale_oct15")
    snap_stale = s2.analyze_market(md_stale, now=now_dt)
    s3r_stale = snap_stale.shadow_candidates["stage3r_gate"]
    assert s3r_stale["ok"] is False
    assert s3r_stale["live_capped"] is True
    assert s3r_stale["reason"] == "mbo_feed_stale_or_unverifiable"
    assert snap_stale.confidence <= 30.0
    assert snap_stale.shot.get("shot", "WAIT") == "WAIT"
    pf_stale = main._preflight_data_reason(md_stale)
    assert pf_stale is not None and "stale_or_unverifiable" in pf_stale

    # 3) Fresh MBO (`<=300s`) -> PASSES Stage 3R cleanly
    rr._reset_step2_singletons()
    md_fresh = _build_bookmap_md(now_dt, mbo_mode="fresh")
    snap_fresh = s2.analyze_market(md_fresh, now=now_dt)
    s3r_fresh = snap_fresh.shadow_candidates["stage3r_gate"]
    assert s3r_fresh["ok"] is True
    assert s3r_fresh["live_capped"] is False
    assert snap_fresh.confidence == pytest.approx(72.0)
    assert main._preflight_data_reason(md_fresh) is None

    # 4) Non-BookMap synthetic/MT5 source (`DATA_SOURCE="mt5"`) -> never blocked by Stage 3R
    for mod in {config, getattr(s2, "config", config), getattr(main, "config", config)}:
        monkeypatch.setattr(mod, "DATA_SOURCE", "mt5", raising=False)
    rr._reset_step2_singletons()
    snap_mt5 = s2.analyze_market(_build_bookmap_md(now_dt, mbo_mode="empty"), now=now_dt)
    assert snap_mt5.shadow_candidates["stage3r_gate"]["ok"] is True
    assert snap_mt5.confidence == pytest.approx(72.0)


def test_stage4r_trade_guard_post_exit_cooldown_and_daily_loss_circuit_breaker(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "LOCAL_TZ", "Europe/Budapest", raising=False)
    monkeypatch.setattr(config, "BASE_TF_MINUTES", 3, raising=False)
    monkeypatch.setattr(config, "COOLDOWN_MINUTES", 10, raising=False)  # stale M5 .env value
    monkeypatch.setattr(config, "STAGE4R_POST_EXIT_COOLDOWN_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "STAGE4R_POST_EXIT_COOLDOWN_MINUTES", 3.0, raising=False)
    monkeypatch.setattr(config, "STAGE4R_DAILY_LOSS_BREAKER_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "STAGE4R_DAILY_MAX_LOSS_PTS", 12.0, raising=False)

    t0 = datetime(2026, 10, 20, 10, 0, 0, tzinfo=timezone.utc)
    tg = trade_guard.TradeGuard()
    assert tg.cooldown_minutes == pytest.approx(3.0)
    assert tg.post_exit_cooldown_minutes == pytest.approx(3.0)
    assert tg.max_daily_loss_pts == pytest.approx(12.0)

    # Trade #1 opens at 10:00Z and closes 11 minutes later at 10:11Z (-5.5 pts)
    tg.record_trade(now=t0)
    t_close1 = t0 + timedelta(minutes=11)
    monkeypatch.setattr(trade_history, "_now_ts", lambda: t_close1.timestamp())
    trade_history.record_close(101, "SELL", 4105.5, "SL_HIT", gain_pts=-5.5, volume=0.1)

    # At 10:12:30Z (1.5m after actual exit), post-exit 3.0m cooldown MUST block re-entry
    ok_mid, why_mid = tg.can_trade(now=t0 + timedelta(minutes=12, seconds=30))
    assert ok_mid is False
    assert "post-exit cooldown active" in why_mid

    # At 10:14:05Z (>3.0m after actual exit, day PnL = -5.5 > -12.0), continuation wave is ALLOWED
    ok_next, why_next = tg.can_trade(now=t0 + timedelta(minutes=14, seconds=5))
    assert ok_next is True and why_next == "ok"

    # Trade #2 closes at 10:25Z with -7.0 pts -> cumulative day PnL = -12.50 pts <= -12.00 pts
    t_close2 = t0 + timedelta(minutes=25)
    monkeypatch.setattr(trade_history, "_now_ts", lambda: t_close2.timestamp())
    trade_history.record_close(102, "SELL", 4112.0, "SL_HIT", gain_pts=-7.0, volume=0.1)

    ok_halt, why_halt = tg.can_trade(now=t0 + timedelta(minutes=40))
    assert ok_halt is False
    assert "daily loss circuit breaker hit (-12.50 pts <= -12.00 pts)" in why_halt

    # Next Budapest local trading day -> circuit breaker resets cleanly!
    next_day = datetime(2026, 10, 21, 9, 0, 0, tzinfo=timezone.utc)
    ok_tmrw, why_tmrw = tg.can_trade(now=next_day)
    assert ok_tmrw is True and why_tmrw == "ok"


def test_raw_replay_stage4r_portfolio_day_stop_sim():
    t0 = 1760954400.0
    fake_days = [
        {
            "day": "2026-10-15",
            "day_type": "MIXED",
            "cycles": [
                {
                    "entry_ep": t0,
                    "direction": "SELL",
                    "confidence": 70.0,
                    "shot_action": "GO",
                    "shot_pebble_bypass": False,
                    "shot_pm4b_pts": -6.5,
                    "shot_pm4b_exit_ep": t0 + 300.0,
                },
                {
                    "entry_ep": t0 + 600.0,
                    "direction": "SELL",
                    "confidence": 72.0,
                    "shot_action": "GO",
                    "shot_pebble_bypass": False,
                    "shot_pm4b_pts": -6.0,
                    "shot_pm4b_exit_ep": t0 + 900.0,
                },
                {
                    # Cumulative day PnL is now -12.5 <= -12.0 -> this 3rd loser MUST be halted!
                    "entry_ep": t0 + 1200.0,
                    "direction": "SELL",
                    "confidence": 74.0,
                    "shot_action": "GO",
                    "shot_pebble_bypass": False,
                    "shot_pm4b_pts": -8.0,
                    "shot_pm4b_exit_ep": t0 + 1500.0,
                },
            ],
        },
        {
            "day": "2026-10-29",
            "day_type": "TREND",
            "cycles": [
                {
                    "entry_ep": t0 + 86400.0,
                    "direction": "SELL",
                    "confidence": 78.0,
                    "shot_action": "GO",
                    "shot_pebble_bypass": False,
                    "shot_pm4b_pts": 15.0,
                    "shot_pm4b_exit_ep": t0 + 86400.0 + 600.0,
                },
            ],
        },
    ]

    sim_no_stop = rr.simulate_one_pos_portfolio(
        fake_days, 35.0, "shot_pm4b_pts", "shot_pm4b_exit_ep",
        cooldown_min=3.0, daily_max_loss_pts=0.0,
    )
    assert sim_no_stop["trades"] == 4
    assert sim_no_stop["tot_pts"] == pytest.approx(-5.5)
    assert sim_no_stop["max_dd"] == pytest.approx(20.5)

    sim_with_stop = rr.simulate_one_pos_portfolio(
        fake_days, 35.0, "shot_pm4b_pts", "shot_pm4b_exit_ep",
        cooldown_min=3.0, daily_max_loss_pts=12.0,
    )
    assert sim_with_stop["trades"] == 3
    assert sim_with_stop["skipped_day_stop"] == 1
    assert sim_with_stop["tot_pts"] == pytest.approx(2.5)
    assert sim_with_stop["max_dd"] == pytest.approx(12.5)
    assert sim_with_stop["by_day"][0]["halted"] is True
    assert sim_with_stop["by_day"][1]["halted"] is False
