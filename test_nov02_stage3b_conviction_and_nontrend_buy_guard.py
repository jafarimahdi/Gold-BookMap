"""Regression tests for Stage 3b (2026-11-02):
1. Multi-family conviction floor >= 50% (blocks internally conflicted 35-49% signals while keeping 50-64% & >=65%).
2. Non-TREND BUY Exhaustion Guard (blocks mid-air / unconfirmed BUY|RANGE & BUY|NEUTRAL while preserving
   all SELL regimes, BUY|TREND, and genuine discounted bottom-reversal BUYs).
3. Fail-closed Stage 3b guard in main._shooting_execution_gate.
4. Separate Stage-3b gate waterfall buckets in tools/raw_replay.py Section [2].
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd

import config
import main
import step2_market_analysis as s2
import tools.raw_replay as rr


def _build_range_md(now_dt: datetime, *, price_offset_from_vwap: float = 1.2) -> dict:
    """Balanced choppy M1 series (ADX < 20 -> RANGE) centered around 4000.0."""
    n = 180
    idx = pd.date_range(end=now_dt, periods=n, freq="1min", tz=timezone.utc)
    # Tight sine wave around 4000.0 so VWAP ~ 4000.0 and ADX < 20 (RANGE)
    closes = [4000.0 + 1.8 * math.sin(i * 0.45) for i in range(n)]
    px = round(4000.0 + price_offset_from_vwap, 2)
    closes[-1] = px
    candles = pd.DataFrame(
        {
            "open": [c - 0.10 for c in closes],
            "high": [c + 0.45 for c in closes],
            "low": [c - 0.45 for c in closes],
            "close": closes,
            "volume": [120.0] * n,
        },
        index=idx,
    )
    ep = now_dt.timestamp()
    events = [
        {
            "order_id": f"bid_add_{i}",
            "type": "BID_NEW",
            "side": "BID",
            "price": round(px - 0.2, 2),
            "size": 14.0,
            "timestamp": ep - 8.0 + i * 0.2,
        }
        for i in range(25)
    ]
    ticks = [
        {"price": round(px - 0.8 + 0.03 * i, 2), "size": 5.0, "side": "BUY", "timestamp": ep - 120.0 + i * 3.0}
        for i in range(30)
    ]
    return {
        "symbol": "GCZ6.COMEX@RITHMIC",
        "data_symbol": "GCZ6.COMEX@RITHMIC",
        "data_market": "GC",
        "trade_symbol": "XAUUSD",
        "trade_market": "XAUUSD",
        "price": px,
        "bid": round(px - 0.1, 2),
        "ask": round(px + 0.1, 2),
        "volume": 21600.0,
        "timestamp": now_dt,
        "candles": candles,
        "ticks": ticks,
        "order_book": {
            "bids": [(round(px - 0.1 - i * 0.1, 2), 20.0) for i in range(20)],
            "asks": [(round(px + 0.1 + i * 0.1, 2), 18.0) for i in range(20)],
        },
        "order_events": events,
        "news": {"headlines": [], "events": [], "fetch_calendar": False},
        "has_data": True,
        "data_quality_ok": True,
        "last_data_age_seconds": 0.4,
    }


def test_stage3b_defaults_enabled_in_config():
    assert config.STAGE3B_GATE_LIVE_ENABLED is True
    assert config.STAGE3B_MIN_CONFIDENCE == 50.0
    assert config.STAGE3B_NON_TREND_BUY_GUARD_ENABLED is True


def test_stage3b_blocks_conflicted_35_to_49_panel_and_keeps_50_to_64(monkeypatch, tmp_path):
    """Conflicted 2-family panel (<50%) is capped to <=30%; clean 50-64% 2-family panel passes untouched!"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "AI_CONFIDENCE_THRESHOLD", 35.0)
    monkeypatch.setattr(config, "FAMILY_GATE_LIVE_ENABLED", True)
    monkeypatch.setattr(config, "STAGE3B_GATE_LIVE_ENABLED", True)
    monkeypatch.setattr(config, "STAGE3B_MIN_CONFIDENCE", 50.0)
    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None

    now1 = datetime(2026, 10, 30, 14, 0, 0, tzinfo=timezone.utc)

    # Case 1: Conflicted TREND SELL @ 44.0% (2 families SELL vs 1 family BUY -> <50% civil war)
    def _mock_conflicted(*args, **kwargs):
        return (
            28.0,
            "SELL",
            44.0,
            [
                "MTF M9:DOWN M36:DOWN -> SELL w 0.50",
                "L3 NET FLOW SELL -45.0 (buys 10.0 vs sells 55.0) w 1.00",
                "CVD rising delta +220.0 CVD +800.0 -> BUY w 0.45",
            ],
        )

    monkeypatch.setattr(s2.SignalEngine, "aggregate", _mock_conflicted)
    md1 = _build_range_md(now1)
    snap_conflict = s2.analyze_market(md1, now=now1)
    fg1 = snap_conflict.shadow_candidates["family_gate"]
    s3b_1 = snap_conflict.shadow_candidates["stage3b_gate"]
    assert fg1["ok"] is True
    assert s3b_1["ok"] is False
    assert s3b_1["reason"] == "conflicted_panel_below_50"
    assert s3b_1["pre_stage3b_confidence"] == 44.0
    assert s3b_1["live_capped"] is True
    assert snap_conflict.confidence == 30.0

    # Case 2: Clean 58.0% (50-64% superstar bucket!) -> passes 100% untouched!
    now2 = now1 + timedelta(minutes=6)
    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None

    def _mock_58(*args, **kwargs):
        return (
            32.0,
            "SELL",
            58.0,
            [
                "MTF M9:DOWN M36:DOWN -> SELL w 0.50",
                "L3 NET FLOW SELL -45.0 (buys 10.0 vs sells 55.0) w 1.00",
            ],
        )

    monkeypatch.setattr(s2.SignalEngine, "aggregate", _mock_58)
    snap_58 = s2.analyze_market(_build_range_md(now2), now=now2)
    s3b_2 = snap_58.shadow_candidates["stage3b_gate"]
    assert s3b_2["ok"] is True
    assert s3b_2["live_capped"] is False
    assert snap_58.confidence == 58.0


def test_stage3b_non_trend_buy_guard_blocks_midair_and_keeps_bottom_exhaustion(monkeypatch, tmp_path):
    """Unconfirmed BUY|RANGE is capped; SELL|RANGE and discounted bottom-exhaustion BUY|RANGE pass!"""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "AI_CONFIDENCE_THRESHOLD", 35.0)
    monkeypatch.setattr(config, "FAMILY_GATE_LIVE_ENABLED", True)
    monkeypatch.setattr(config, "STAGE3B_GATE_LIVE_ENABLED", True)
    monkeypatch.setattr(config, "STAGE3B_NON_TREND_BUY_GUARD_ENABLED", True)
    monkeypatch.setattr(s2, "_detect_regime", lambda _adx: "RANGE")
    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None

    t0 = datetime(2026, 10, 30, 15, 0, 0, tzinfo=timezone.utc)

    # (A) Mid-air BUY | RANGE (mtf + cvd_momentum above VWAP, no bottom exhaustion) -> BLOCKED!
    monkeypatch.setattr(
        s2.SignalEngine,
        "aggregate",
        lambda *a, **kw: (
            40.0,
            "BUY",
            70.0,
            [
                "MTF M9:UP M36:UP -> BUY w 0.50",
                "CVD rising delta +250.0 CVD +900.0 -> BUY w 0.45",
            ],
        ),
    )
    snap_bad_buy = s2.analyze_market(_build_range_md(t0, price_offset_from_vwap=1.2), now=t0)
    s3b_bad = snap_bad_buy.shadow_candidates["stage3b_gate"]
    assert snap_bad_buy.regime == "RANGE"
    assert s3b_bad["ok"] is False
    assert s3b_bad["reason"] == "non_trend_buy_unconfirmed"
    assert s3b_bad["live_capped"] is True
    assert snap_bad_buy.confidence == 30.0

    # (B) SELL | RANGE (near VAH + L3 net flow SELL) -> PASSES 100% untouched!
    t1 = t0 + timedelta(minutes=6)
    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None
    monkeypatch.setattr(
        s2.SignalEngine,
        "aggregate",
        lambda *a, **kw: (
            48.0,
            "SELL",
            74.0,
            [
                "near VAH 4001.8 (VHL) resistance w 1.30 -> SELL",
                "L3 NET FLOW SELL -50.0 (buys 8.0 vs sells 58.0) w 1.00",
            ],
        ),
    )
    snap_sell_range = s2.analyze_market(_build_range_md(t1, price_offset_from_vwap=1.4), now=t1)
    assert snap_sell_range.shadow_candidates["stage3b_gate"]["ok"] is True
    assert snap_sell_range.confidence == 74.0

    # (C) Discounted (<VWAP) bottom-reversal BUY | RANGE (near VAL + bullish CVD divergence, 0 SELL dissent) -> PASSES!
    t2 = t1 + timedelta(minutes=6)
    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None
    monkeypatch.setattr(
        s2.SignalEngine,
        "aggregate",
        lambda *a, **kw: (
            52.0,
            "BUY",
            76.0,
            [
                "near VAL 3998.4 (VLL) support w 1.30 -> BUY",
                "bullish CVD divergence -> BUY w 1.00",
            ],
        ),
    )
    snap_good_buy = s2.analyze_market(_build_range_md(t2, price_offset_from_vwap=-1.5), now=t2)
    s3b_good = snap_good_buy.shadow_candidates["stage3b_gate"]
    assert s3b_good["ok"] is True
    assert s3b_good["live_capped"] is False
    assert snap_good_buy.confidence == 76.0


def test_stage3b_execution_gate_fails_closed_and_raw_replay_records_buckets(monkeypatch):
    """_shooting_execution_gate blocks LLM override when stage3b_gate fails; raw_replay [2] tracks both reasons."""
    monkeypatch.setattr(config, "AI_CONFIDENCE_THRESHOLD", 35.0)
    monkeypatch.setattr(config, "FAMILY_GATE_LIVE_ENABLED", True)
    monkeypatch.setattr(config, "STAGE3B_GATE_LIVE_ENABLED", True)

    decision = SimpleNamespace(action="BUY", confidence=82.0, model="gemini-test", rationale="hallucinated")
    snap_blocked = SimpleNamespace(
        confidence=30.0,
        signal_direction="BUY",
        shadow_candidates={
            "family_gate": {"ok": True, "agree_families": ["MAP", "TAPE"], "min_families": 2},
            "stage3b_gate": {"ok": False, "reason": "non_trend_buy_unconfirmed", "live_capped": True},
        },
        shot={"shot": "GO", "side": "BUY", "entry_style": "PASSIVE_LIMIT"},
        power={"direction": "UP"},
    )
    ok, why = main._shooting_execution_gate(decision, snap_blocked)
    assert ok is False
    assert "Stage 3b Gate failed (non_trend_buy_unconfirmed)" in why

    fake_day = {
        "day": "2026-10-30",
        "ok": True,
        "day_type": "MIXED",
        "prints": 1000,
        "mbo_streamed": 5000,
        "day_range": 45.0,
        "day_net": -10.0,
        "elapsed_s": 1.0,
        "conf_threshold": 35.0,
        "tf_min": 3,
        "judge_stats": {},
        "bench_judge_stats": {},
        "cycles": [
            {
                "error": None,
                "direction": "SELL",
                "confidence": 30.0,
                "family_ok": True,
                "gate_stage": "BLOCKED: Conf < 50% (Stage 3b Conflict)",
                "regime": "TREND",
                "buda_hour": 14,
                "shot_action": "WAIT",
                "trick_code": "CLEAN_AGAINST",
                "signed_4b": [-0.4, -0.8, -1.2, -1.5],
                "fix_out": "SL",
                "fix_pts": -3.5,
                "c3_out": "SL",
                "c3_pts": -1.5,
                "tr10_out": "SL",
                "tr10_pts": -1.5,
                "b4_pts": -1.5,
                "a2_fix_pts": None,
                "a2_atr_pts": None,
            },
            {
                "error": None,
                "direction": "BUY",
                "confidence": 30.0,
                "family_ok": True,
                "gate_stage": "BLOCKED: Non-Trend BUY (Stage 3b Guard)",
                "regime": "RANGE",
                "buda_hour": 15,
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
        ],
    }
    txt, _, _ = rr.synthesize_multi_day([fake_day])
    assert "BLOCKED: Conf < 50% (Stage 3b Conflict)" in txt
    assert "BLOCKED: Non-Trend BUY (Stage 3b Guard)" in txt
