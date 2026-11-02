"""
test_nov02_stage3a_family_gate_and_replay_clock.py
==================================================
Stage 3a (Path A — Step 1 of Stage 3, Law #9 Causal Isolation):
  1. Family Independence Gate promoted to live default (`FAMILY_GATE_LIVE_ENABLED = True`)
     after 5-day M3 raw replay proof (`current_version_replay-5.txt`: >=2 Agreeing Families =
     +580.0 FIX / +127.9 C3 / +169.9 TR / 60.4% +12m vs <2 Families = -2,697.9 FIX).
  2. Fixes `step2_market_analysis.py` L5935 attribute bug (`AI_CONFIDENCE_THRESHOLD` vs
     missing `CONFIDENCE_THRESHOLD`), capping single-family solo signals to `<= 30.0%`
     (`< 35%` owner `.env` threshold) while preserving raw confidence on `>=2` families.
  3. Non-overridable fail-closed guard in `main._shooting_execution_gate` when
     `FAMILY_GATE_LIVE_ENABLED` is True and `family_gate.ok is False`.
  4. `shooting_team._observe_passive_road_sample` & `_crowd_v2` honor simulated replay
     clock `ctx["now"]` so fast offline replay never starves Crowd v2 Q2 road history.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

import config
import main
import shooting_team
import step2_market_analysis as s2
from tools import raw_replay as rr


def _build_trending_md(now_dt: datetime, *, include_l3_book_buy: bool = False) -> dict:
    idx = pd.date_range(now_dt - timedelta(minutes=180), periods=180, freq="1min")
    closes = [4000.0 + 0.08 * i for i in range(180)]
    candles = pd.DataFrame(
        {
            "open": [c - 0.04 for c in closes],
            "high": [c + 0.25 for c in closes],
            "low": [c - 0.20 for c in closes],
            "close": closes,
            "volume": [120.0] * 180,
        },
        index=idx,
    )
    ep = now_dt.timestamp()
    px = closes[-1]
    tick_data = []
    order_events = []
    if include_l3_book_buy:
        tick_data = [
            {"timestamp": ep - 12.0 + i, "price": px + 0.05, "volume": 18.0, "side": "BUY", "is_direct": True}
            for i in range(10)
        ]
        order_events = [
            {"timestamp": ep - 6.0, "type": "BID_NEW", "side": "BID", "price": round(px - 0.20 - 0.1 * i, 2), "size": 120.0, "order_id": f"b{i}"}
            for i in range(4)
        ]
    return {
        "symbol": "GCZ6.COMEX@RITHMIC",
        "data_symbol": "GCZ6.COMEX@RITHMIC",
        "trade_symbol": "XAUUSD",
        "price": px,
        "bid": round(px - 0.10, 2),
        "ask": round(px + 0.10, 2),
        "volume": 5000.0,
        "timestamp": now_dt,
        "candles": candles,
        "candles_is_m1": True,
        "tick_data": tick_data,
        "order_book": {
            "bids": [(round(px - 0.10 - 0.1 * i, 2), 20.0) for i in range(20)],
            "asks": [(round(px + 0.10 + 0.1 * i, 2), 20.0) for i in range(20)],
        },
        "order_events": order_events,
        "macro": {},
        "news": {"headlines": [], "events": [], "fetch_calendar": False},
        "has_data": True,
        "data_quality_ok": True,
        "last_data_age_seconds": 0.5,
    }


def test_stage3a_family_gate_caps_single_family_below_35_and_passes_two_families(monkeypatch, tmp_path):
    """Solo MAP signal (100% raw agreement) is capped to <=30% (<35% .env gate); MAP+BOOK passes untouched."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "AI_CONFIDENCE_THRESHOLD", 35.0)
    monkeypatch.setattr(config, "FAMILY_GATE_LIVE_ENABLED", True)
    monkeypatch.setattr(config, "FAMILY_GATE_MIN_FAMILIES", 2)
    monkeypatch.setattr(config, "V6_4TEAMS_ENABLED", False)
    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None

    now1 = datetime(2026, 10, 30, 14, 15, 0, tzinfo=timezone.utc)
    snap_solo = s2.analyze_market(_build_trending_md(now1, include_l3_book_buy=False), now=now1)
    fg_solo = snap_solo.shadow_candidates["family_gate"]
    assert snap_solo.signal_direction == "BUY"
    assert fg_solo["ok"] is False
    assert fg_solo["agree_families"] == ["MAP"]
    assert fg_solo["raw_confidence"] >= 70.0
    assert fg_solo["live_capped"] is True
    assert snap_solo.confidence == 30.0
    assert snap_solo.confidence < config.AI_CONFIDENCE_THRESHOLD
    assert any("FAMILY_GATE live cap:" in n for n in snap_solo.notes)

    # Next bar: MAP (mtf/vwap_trend) + BOOK/TAPE (l3_large_ofi / l3_aggr_limit / cvd_momentum) agree -> >=2 families!
    now2 = now1 + timedelta(minutes=6)
    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None
    snap_multi = s2.analyze_market(_build_trending_md(now2, include_l3_book_buy=True), now=now2)
    fg_multi = snap_multi.shadow_candidates["family_gate"]
    assert snap_multi.signal_direction == "BUY"
    assert fg_multi["ok"] is True
    assert len(fg_multi["agree_families"]) >= 2
    assert fg_multi["live_capped"] is False
    assert snap_multi.confidence >= 65.0

    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None


def test_stage3a_shooting_execution_gate_blocks_llm_override_on_failed_family_gate(monkeypatch):
    """Even if an external LLM returns BUY @ 80%, _shooting_execution_gate fails closed when family_gate.ok is False."""
    monkeypatch.setattr(config, "AI_CONFIDENCE_THRESHOLD", 35.0)
    monkeypatch.setattr(config, "FAMILY_GATE_LIVE_ENABLED", True)
    monkeypatch.setattr(config, "EXECUTION_MODE", "python")

    decision = SimpleNamespace(action="BUY", confidence=80.0, model="gemini-test", rationale="hallucinated")
    snap_bad_fg = SimpleNamespace(
        confidence=30.0,
        signal_direction="BUY",
        power={"direction": "UP", "reason_code": "UPWARD_FORCE_DOMINATES"},
        shot={"shot": "GO", "side": "BUY", "entry_style": "AGGRESSIVE_MARKET"},
        shadow_candidates={"family_gate": {"ok": False, "agree_families": ["MAP"], "min_families": 2}},
    )
    ok_bad, reason_bad = main._shooting_execution_gate(decision, snap_bad_fg)
    assert ok_bad is False
    assert "Family Independence Gate failed (1/2 agreeing families)" in reason_bad

    snap_good_fg = SimpleNamespace(
        confidence=82.0,
        signal_direction="BUY",
        power={"direction": "UP", "reason_code": "UPWARD_FORCE_DOMINATES"},
        shot={"shot": "GO", "side": "BUY", "entry_style": "AGGRESSIVE_MARKET"},
        shadow_candidates={"family_gate": {"ok": True, "agree_families": ["BOOK", "MAP"], "min_families": 2}},
    )
    ok_good, reason_good = main._shooting_execution_gate(decision, snap_good_fg)
    assert ok_good is True
    assert "corridor open" in reason_good


def test_stage3a_shooting_crowd_v2_uses_simulated_ctx_now_clock_in_fast_replay():
    """12 historical cycles run inside <0.02s real wall-clock warm _ROAD_HISTORY via ctx['now'] cleanly."""
    shooting_team._ROAD_HISTORY.clear()
    cfg = SimpleNamespace(
        LIMIT_ORDER_ENABLED=True,
        AGGRESSIVE_ENTRY_ENABLED=False,
        LOT_SIZE=1.0,
        SHOOT_DOOR_MARGIN=3.0,
        SHOOT_ROAD_NORMAL_MAX=2.0,
        SHOOT_PARTICIPATION_MAX=0.10,
        SHOOT_ROAD_HISTORY_MIN=12,
        SHOOT_ROAD_HISTORY_SECONDS=2700.0,
        SHOOT_ROAD_PASSIVE_SAMPLE_GAP_SECONDS=5.0,
        STALE_DATA_SECONDS=300.0,
        POWER_MAX_TICK_AGE_SECONDS=60.0,
        SHOOT_MIN_RR=1.2,
        SHOOT_PLAN_TTL_SECONDS=30.0,
        QUEUE_POS_ENABLED=False,
    )
    smap = {
        "above": [{"price": 4004.5, "size": 15.0, "kind": "OFFER_WALL", "doors_between": 1, "lots_between": 120.0, "road_clear": True}],
        "below": [{"price": 3995.5, "size": 15.0, "kind": "BID_WALL", "doors_between": 1, "lots_between": 120.0, "road_clear": True}],
    }
    pw_neither = {
        "team": "POWER", "direction": "NEITHER", "up_power_pct": 50.0, "down_power_pct": 50.0,
        "power_total_pct": 100.0, "reason_code": "NO_DOMINANT_FORCE",
        "context": {"market_regime": "TREND", "data_quality_ok": True, "feed_stale": False},
        "regime_diagnostics": {"market_regime": "TREND", "latest_tick_age_seconds": 1.0},
        "shadow_only": False,
    }
    pw_up = {
        "team": "POWER", "direction": "UP", "up_power_pct": 70.0, "down_power_pct": 30.0,
        "power_total_pct": 100.0, "reason_code": "UPWARD_FORCE_DOMINATES",
        "context": {"market_regime": "TREND", "data_quality_ok": True, "feed_stale": False},
        "regime_diagnostics": {"market_regime": "TREND", "latest_tick_age_seconds": 1.0},
        "shadow_only": False,
    }

    t_base = datetime(2026, 10, 30, 12, 0, 0, tzinfo=timezone.utc)
    # Run 11 historical cycles 60s apart in simulated time (0ms real wall-clock sleep!)
    for step in range(11):
        out_w = shooting_team.plan_shot(
            smap, pw_neither, 4000.0, 1.5, 0.2, cfg,
            entry_context={
                "bid": 3999.9, "ask": 4000.1, "news_state": "QUIET",
                "has_data": True, "data_quality_ok": True, "last_data_age_seconds": 1.0,
                "trade_lots": 1.0, "recent_volume": 400.0, "market_symbol": "GCZ6",
                "now": t_base + timedelta(minutes=step),
            },
        )
        assert out_w["shot"] == "WAIT"
    assert len(shooting_team._ROAD_HISTORY) == 11

    out_go = shooting_team.plan_shot(
        smap, pw_up, 4000.0, 1.5, 0.2, cfg,
        entry_context={
            "bid": 3999.9, "ask": 4000.1, "news_state": "QUIET",
            "has_data": True, "data_quality_ok": True, "last_data_age_seconds": 1.0,
            "trade_lots": 1.0, "recent_volume": 400.0, "market_symbol": "GCZ6",
            "now": t_base + timedelta(minutes=11),
        },
    )
    assert out_go["shot"] == "GO"
    assert out_go["side"] == "BUY"
    shooting_team._ROAD_HISTORY.clear()


def test_stage3a_raw_replay_filters_actionable_regime_and_hour_tables():
    """raw_replay [5] & [6] exclude <conf_thr blocked cycles while [2] & [4] show exact blocked counts."""
    fake_day = {
        "day": "2026-10-30",
        "ok": True,
        "day_type": "TREND",
        "prints": 1000,
        "mbo_streamed": 5000,
        "day_range": 45.0,
        "day_net": -30.0,
        "elapsed_s": 1.2,
        "conf_threshold": 35.0,
        "tf_min": 3,
        "judge_stats": {},
        "bench_judge_stats": {},
        "cycles": [
            {
                "error": None,
                "direction": "BUY",
                "confidence": 30.0,
                "family_ok": False,
                "gate_stage": "BLOCKED: <2 Families (Family Gate)",
                "regime": "RANGE",
                "buda_hour": 19,
                "shot_action": "WAIT",
                "trick_code": "CLEAN_AGAINST",
                "signed_4b": [-0.5, -1.0, -1.5, -2.0],
                "fix_out": "SL",
                "fix_pts": -3.5,
                "c3_out": "SL",
                "c3_pts": -2.0,
                "tr10_out": "SL",
                "tr10_pts": -2.0,
                "b4_pts": -2.0,
                "a2_fix_pts": None,
                "a2_atr_pts": None,
            },
            {
                "error": None,
                "direction": "SELL",
                "confidence": 78.0,
                "family_ok": True,
                "gate_stage": "CONF>=35% + POWER ALIGNED (Shooting=WAIT)",
                "regime": "TREND",
                "buda_hour": 16,
                "shot_action": "WAIT",
                "trick_code": "CLEAN_FOLLOW",
                "signed_4b": [0.8, 1.6, 2.4, 3.2],
                "fix_out": "TP",
                "fix_pts": 4.5,
                "c3_out": "TRAIL",
                "c3_pts": 3.0,
                "tr10_out": "TRAIL",
                "tr10_pts": 3.5,
                "b4_pts": 3.2,
                "a2_fix_pts": None,
                "a2_atr_pts": None,
            },
        ],
    }
    txt, _, _ = rr.synthesize_multi_day([fake_day])
    assert "BLOCKED: <2 Families (Family Gate)" in txt
    sec5 = txt.split("[5] DIRECTION x REGIME MATRIX")[1].split("[6] BUDAPEST HOUR")[0]
    assert "SELL | TREND" in sec5
    assert "BUY | RANGE" not in sec5  # blocked 30% cycle excluded from actionable [5]!
    sec6 = txt.split("[6] BUDAPEST HOUR")[1].split("[7] CURRENT ACTIVE")[0]
    assert "16:00" in sec6
    assert "19:00" not in sec6  # blocked 30% cycle excluded from actionable [6]!
