"""Regression tests for Stage 3c (2026-11-02):
1. DST-safe Exchange Session Dead-Zone Gate (Europe/London 16h close fix,
   America/New_York 13:29-13:30 COMEX settlement print, America/New_York 14-15h post-COMEX vacuum),
   including US/EU DST transition week (2026-10-30 EDT/GMT) & standard winter (2026-11-02 EST/GMT)
   plus pure-Python tzdata-free fallback.
2. Step 2 live cap + main._shooting_execution_gate fail-closed guard + raw_replay [2] bucket.
3. Step 3 build_prompt DATA_DIR isolation, snapshot.timestamp session_context, and <15k char headroom
   even when judge_report_card.json is populated with 12 active judges on v6_teams=True (20 bars).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

import config
import main
import session
import step2_market_analysis as s2
import step3_ai_decision as s3
import tools.raw_replay as rr


def test_stage3c_defaults_enabled_in_config():
    assert config.STAGE3C_SESSION_GATE_LIVE_ENABLED is True
    assert config.STAGE3C_BLOCK_LONDON_16H_CLOSE is True
    assert config.STAGE3C_BLOCK_POST_COMEX_14_15H_ET is True


def test_stage3c_dst_safe_exchange_dead_zone_across_oct_edt_and_nov_est(monkeypatch):
    """Anchors strictly to Europe/London and America/New_York across DST transitions & pure-Python fallback."""
    # --- A) Oct 30, 2026 (EU already winter CET=UTC+1 / UK GMT=UTC+0, US still summer EDT=UTC-4) ---
    # 14:00 UTC = 14:00 UK / 10:00 NY EDT (15:00 CET) -> OPEN
    assert session.exchange_dead_zone_reason(datetime(2026, 10, 30, 14, 0, tzinfo=timezone.utc)) is None
    # 16:15 UTC = 16:15 UK / 12:15 NY EDT (17:15 CET) -> London 16h close fix BLOCKED
    assert (
        session.exchange_dead_zone_reason(datetime(2026, 10, 30, 16, 15, tzinfo=timezone.utc))
        == "london_close_fix_16h_uk"
    )
    # 17:15 UTC = 17:15 UK / 13:15 NY EDT (18:15 CET = +77.1 FIX COMEX close run!) -> OPEN
    assert session.exchange_dead_zone_reason(datetime(2026, 10, 30, 17, 15, tzinfo=timezone.utc)) is None
    # 17:29 UTC = 13:29 NY EDT -> COMEX settlement VWAP print BLOCKED
    assert (
        session.exchange_dead_zone_reason(datetime(2026, 10, 30, 17, 29, tzinfo=timezone.utc))
        == "comex_settlement_print_1329_1330_et"
    )
    # 18:30 UTC & 19:30 UTC = 14:30 & 15:30 NY EDT (19:30 & 20:30 CET post-COMEX vacuum) -> BLOCKED
    assert (
        session.exchange_dead_zone_reason(datetime(2026, 10, 30, 18, 30, tzinfo=timezone.utc))
        == "post_comex_vacuum_14_15h_et"
    )
    assert (
        session.exchange_dead_zone_reason(datetime(2026, 10, 30, 19, 30, tzinfo=timezone.utc))
        == "post_comex_vacuum_14_15h_et"
    )
    # 20:15 UTC = 16:15 NY EDT (21:15 CET = 100% WR +142.9 FIX US equity close!) -> OPEN
    assert session.exchange_dead_zone_reason(datetime(2026, 10, 30, 20, 15, tzinfo=timezone.utc)) is None

    # --- B) Nov 2, 2026 (US switched to winter EST=UTC-5; UK GMT=UTC+0; CET=UTC+1) ---
    # 18:15 UTC = 18:15 UK / 13:15 NY EST (19:15 CET active COMEX close run) -> OPEN!
    assert session.exchange_dead_zone_reason(datetime(2026, 11, 2, 18, 15, tzinfo=timezone.utc)) is None
    # 18:30 UTC = 13:30 NY EST -> COMEX settlement VWAP print BLOCKED
    assert (
        session.exchange_dead_zone_reason(datetime(2026, 11, 2, 18, 30, tzinfo=timezone.utc))
        == "comex_settlement_print_1329_1330_et"
    )
    # 20:15 UTC = 15:15 NY EST (21:15 CET post-COMEX vacuum in winter) -> BLOCKED!
    assert (
        session.exchange_dead_zone_reason(datetime(2026, 11, 2, 20, 15, tzinfo=timezone.utc))
        == "post_comex_vacuum_14_15h_et"
    )
    # 21:15 UTC = 16:15 NY EST (22:15 CET US equity close rebalance in winter) -> OPEN!
    assert session.exchange_dead_zone_reason(datetime(2026, 11, 2, 21, 15, tzinfo=timezone.utc)) is None

    # --- C) Pure-Python fallback when Windows lacks tzdata ---
    import zoneinfo
    monkeypatch.setattr(zoneinfo, "ZoneInfo", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("no tzdata")))
    assert (
        session.exchange_dead_zone_reason(datetime(2026, 10, 30, 16, 20, tzinfo=timezone.utc))
        == "london_close_fix_16h_uk"
    )
    assert (
        session.exchange_dead_zone_reason(datetime(2026, 11, 2, 20, 20, tzinfo=timezone.utc))
        == "post_comex_vacuum_14_15h_et"
    )


def test_stage3c_step2_caps_dead_zone_and_execution_gate_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "AI_CONFIDENCE_THRESHOLD", 35.0)
    monkeypatch.setattr(config, "FAMILY_GATE_LIVE_ENABLED", True)
    monkeypatch.setattr(config, "STAGE3B_GATE_LIVE_ENABLED", True)
    monkeypatch.setattr(config, "STAGE3C_SESSION_GATE_LIVE_ENABLED", True)
    monkeypatch.setattr(s2, "_detect_regime", lambda _adx: "TREND")
    monkeypatch.setattr(
        s2.SignalEngine,
        "aggregate",
        lambda *a, **kw: (
            55.0,
            "SELL",
            78.0,
            [
                "MTF M9:DOWN M36:DOWN -> SELL w 0.50",
                "L3 NET FLOW SELL -60.0 (buys 5.0 vs sells 65.0) w 1.00",
            ],
        ),
    )

    # 19:30 UTC on 2026-10-30 = 15:30 NY EDT (20:30 CET dead zone -> 0/16 = 0.0% WR in replay-7!)
    dz_dt = datetime(2026, 10, 30, 19, 30, 0, tzinfo=timezone.utc)
    idx = pd.date_range(end=dz_dt, periods=120, freq="1min", tz=timezone.utc)
    closes = [4010.0 - 0.10 * i for i in range(120)]
    md = {
        "symbol": "GCZ6.COMEX@RITHMIC",
        "data_symbol": "GCZ6.COMEX@RITHMIC",
        "data_market": "GC",
        "trade_symbol": "XAUUSD",
        "trade_market": "XAUUSD",
        "price": closes[-1],
        "bid": closes[-1] - 0.1,
        "ask": closes[-1] + 0.1,
        "volume": 12000.0,
        "timestamp": dz_dt,
        "candles": pd.DataFrame(
            {"open": closes, "high": [c + 0.3 for c in closes], "low": [c - 0.3 for c in closes], "close": closes, "volume": [100.0] * 120},
            index=idx,
        ),
        "order_book": {"bids": [(closes[-1] - 0.1, 20.0)], "asks": [(closes[-1] + 0.1, 20.0)]},
        "order_events": [],
        "news": {"headlines": [], "events": [], "fetch_calendar": False},
        "has_data": True,
        "data_quality_ok": True,
        "last_data_age_seconds": 0.3,
    }
    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None
    snap_dz = s2.analyze_market(md, now=dz_dt)
    s3c = snap_dz.shadow_candidates["stage3c_gate"]
    assert s3c["ok"] is False
    assert s3c["reason"] == "post_comex_vacuum_14_15h_et"
    assert s3c["live_capped"] is True
    assert snap_dz.confidence == 30.0

    # Same signal 45 minutes later at 20:15 UTC = 16:15 NY EDT (21:15 CET +142.9 FIX!) -> PASSES!
    open_dt = datetime(2026, 10, 30, 20, 15, 0, tzinfo=timezone.utc)
    md2 = dict(md, timestamp=open_dt)
    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None
    snap_open = s2.analyze_market(md2, now=open_dt)
    assert snap_open.shadow_candidates["stage3c_gate"]["ok"] is True
    assert snap_open.confidence == 78.0

    # Fail-closed guard in main._shooting_execution_gate
    decision = SimpleNamespace(action="SELL", confidence=85.0, model="gemini-test", rationale="override")
    ok, why = main._shooting_execution_gate(decision, snap_dz)
    assert ok is False
    assert "Stage 3c Session Gate failed (post_comex_vacuum_14_15h_et)" in why

    # raw_replay [2] waterfall bucket
    fake_day = {
        "day": "2026-10-30",
        "ok": True,
        "day_type": "TREND",
        "prints": 1000,
        "mbo_streamed": 5000,
        "day_range": 45.0,
        "day_net": -20.0,
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
                "gate_stage": "BLOCKED: Session Dead-Zone (Stage 3c)",
                "regime": "TREND",
                "buda_hour": 20,
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
            }
        ],
    }
    txt, _, _ = rr.synthesize_multi_day([fake_day])
    assert "BLOCKED: Session Dead-Zone (Stage 3c)" in txt


def test_stage3c_step3_prompt_stays_well_under_15k_with_populated_report_card(monkeypatch, tmp_path):
    """Even with a full 12-judge report card in DATA_DIR + v6_teams=True (20 bars), prompt stays <14,500 chars."""
    for _cfg_mod in {config, getattr(s2, "config", config), getattr(s3, "config", config)}:
        monkeypatch.setattr(_cfg_mod, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(_cfg_mod, "V6_4TEAMS_ENABLED", True, raising=False)
        monkeypatch.setattr(_cfg_mod, "AI_MAX_PROMPT_BARS", 20, raising=False)
        monkeypatch.setattr(_cfg_mod, "AI_MAX_HEADLINES", 5, raising=False)

    card_payload = {
        "source_days": ["2026-10-27", "2026-10-28", "2026-10-29"],
        "judges": [
            {"judge": j, "scored": 250, "hit_pct": 62.5, "pts_per_call": 1.45}
            for j in (
                "vwap_trend", "mtf", "value_area", "cvd_divergence", "cvd_momentum",
                "l3_large_ofi", "sweep", "spoof_invert", "l3_net_flow", "l3_aggr_limit",
                "macro_risk", "news_sentiment",
            )
        ],
    }
    (tmp_path / "judge_report_card.json").write_text(json.dumps(card_payload), encoding="utf-8")

    now_dt = datetime(2026, 10, 30, 11, 15, 0, tzinfo=timezone.utc)
    idx = pd.date_range("2026-10-30 09:01:00+00:00", periods=120, freq="1min")
    closes = [3985.0 + 0.08 * i for i in range(120)]
    snap = s2.analyze_market(
        {
            "symbol": "GCZ6.COMEX@RITHMIC",
            "data_symbol": "GCZ6.COMEX@RITHMIC",
            "data_market": "GC",
            "trade_symbol": "XAUUSD",
            "trade_market": "XAUUSD",
            "price": 3994.6,
            "bid": 3994.5,
            "ask": 3994.7,
            "cfd_bid": 3989.10,
            "cfd_ask": 3989.38,
            "volume": 5120.0,
            "timestamp": now_dt,
            "candles": pd.DataFrame(
                {"open": [c - 0.05 for c in closes], "high": [c + 0.35 for c in closes], "low": [c - 0.30 for c in closes], "close": closes, "volume": [160.0] * 120},
                index=idx,
            ),
            "order_book": {"bids": [(3994.5 - i * 0.1, 18.0) for i in range(25)], "asks": [(3994.7 + i * 0.1, 18.0) for i in range(25)]},
            "order_events": [{"order_id": f"oid_{i}", "type": "BID_NEW", "side": "BID", "price": 3994.4, "size": 5.0, "timestamp": now_dt.timestamp() - i} for i in range(150)],
            "news": {"headlines": [f"Gold macro update #{i}" for i in range(12)], "events": [{"title": f"Macro #{i}", "impact": "LOW", "minutes_to_event": 90 + i} for i in range(8)], "fetch_calendar": False},
            "has_data": True,
            "data_quality_ok": True,
            "last_data_age_seconds": 0.4,
        },
        now=now_dt,
    )
    prompt = s3.AIDecisionEngine().build_prompt(snap)
    assert "JUDGE REPORT CARDS" in prompt
    assert '"time_of_day_utc":"11:15"' in prompt
    assert len(prompt) < 14500, f"Prompt should stay comfortably <14.5k chars even with report cards, got {len(prompt)}"
