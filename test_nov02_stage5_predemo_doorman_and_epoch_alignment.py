"""Regression & institutional tests for 2026-11-02 Stage 5 Pre-DEMO Live Bridge Alignment."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import config
import judge_report_card as jrc
import main
import step2_market_analysis as s2
import step3_ai_decision as s3


def test_step3_prompt_reconciles_canonical_family_gate_and_stage3d_sniper(monkeypatch, tmp_path):
    """2-family MAP+BOOK (mtf + l3_aggr_limit) Sniper GO must show 2 agreeing families and STAGE3D_SNIPER."""
    for mod in {config, getattr(s2, "config", config), getattr(s3, "config", config)}:
        monkeypatch.setattr(mod, "reload_env", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(mod, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(mod, "TIMEFRAME", "M3", raising=False)
    monkeypatch.setattr(s2, "DATA_DIR", lambda: str(tmp_path), raising=False)

    now_dt = datetime(2026, 11, 3, 10, 15, 0, tzinfo=timezone.utc)
    idx = pd.date_range("2026-11-03 08:15:00+00:00", periods=120, freq="1min")
    closes = [4010.0 - 0.06 * i for i in range(120)]
    candles = pd.DataFrame(
        {
            "open": [c + 0.05 for c in closes],
            "high": [c + 0.30 for c in closes],
            "low": [c - 0.35 for c in closes],
            "close": closes,
            "volume": [150.0] * 120,
        },
        index=idx,
    )
    snap = s2.analyze_market(
        {
            "symbol": "GCZ6.COMEX@RITHMIC",
            "data_symbol": "GCZ6.COMEX@RITHMIC",
            "data_market": "GC",
            "trade_symbol": "XAUUSD",
            "trade_market": "XAUUSD",
            "price": 4002.8,
            "bid": 4002.7,
            "ask": 4002.9,
            "cfd_bid": 3997.30,
            "cfd_ask": 3997.58,
            "volume": 4800.0,
            "timestamp": now_dt,
            "candles": candles,
            "order_book": {
                "bids": [(4002.7 - i * 0.1, 16.0) for i in range(20)],
                "asks": [(4002.9 + i * 0.1, 16.0) for i in range(20)],
            },
            "order_events": [
                {
                    "order_id": f"oid_{i}",
                    "type": "ASK_NEW",
                    "side": "ASK",
                    "price": 4003.0,
                    "size": 6.0,
                    "timestamp": now_dt.timestamp() - i,
                }
                for i in range(60)
            ],
            "news": {"headlines": ["Gold steady"], "events": [], "fetch_calendar": False},
            "has_data": True,
            "data_quality_ok": True,
            "last_data_age_seconds": 0.3,
        },
        now=now_dt,
    )
    snap.signal_direction = "SELL"
    snap.confidence = 75.0
    snap.judge_votes = [
        {"judge": "mtf", "direction": "SELL", "weight": 1.15},
        {"judge": "l3_aggr_limit", "direction": "SELL", "weight": 0.55},
    ]
    snap.power = {
        "timeframe": "M3",
        "direction": "NEITHER",
        "up_power_pct": 38.0,
        "down_power_pct": 62.0,
        "activity": "MODERATE",
        "reason_code": "FORCE_NOT_DECISIVE",
    }
    snap.shot = {
        "shot": "GO",
        "side": "SELL",
        "entry_style": "AGGRESSIVE_MARKET",
        "entry": 4002.7,
        "stop": 4004.8,
        "target": 3998.5,
        "rr": 2.0,
        "reason": "Sniper road clear",
        "authorized_by": "STAGE3D_SNIPER",
        "sniper_pebble_bypass": True,
    }
    snap.shadow_candidates["family_gate"] = {
        "ok": True,
        "agree_count": 2,
        "agree_families": ["BOOK", "MAP"],
    }

    prompt = s3.AIDecisionEngine().build_prompt(snap)
    assert "v7.0 M3 scalper" in prompt
    assert "STAGE3D_SNIPER" in prompt
    assert len(prompt) < 15000

    metrics_tail = prompt.split("MARKET ANALYSIS METRICS (JSON)")[1]
    payload = json.loads(metrics_tail[metrics_tail.index("{") :])

    # Preserves G-06a contract: l3_aggr_limit excluded from raw judge_votes
    judges_seen = {j["judge"] for j in payload.get("judge_votes", [])}
    assert "l3_aggr_limit" not in judges_seen

    # Reconciles canonical 2-family agreement so Rule #1 never falsely vetoes MAP+BOOK waves
    afs = payload["active_family_summary"]
    assert afs["family_gate_ok"] is True
    assert afs["canonical_agreeing_families"] == ["BOOK", "MAP"]
    assert afs["agreeing_families_sell"] == 2

    # Preserves Stage-3d Sniper authorization & Stage-4c pebble stamp in payload["shot"]
    assert payload["shot"]["authorized_by"] == "STAGE3D_SNIPER"
    assert payload["shot"]["sniper_pebble_bypass"] is True


def test_post_surgery_report_card_epoch_blocks_october_cards_on_tuesday_and_uses_fresh_on_wednesday(
    monkeypatch, tmp_path
):
    """Offline Monday 2026-11-02: Tuesday 2026-11-03 ignores pre-surgery Oct cards; Wednesday uses >=11-03."""
    for mod in {config, getattr(s3, "config", config)}:
        monkeypatch.setattr(mod, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(mod, "JUDGE_REPORT_CARD_EPOCH_DATE", "2026-11-03", raising=False)
    s3._JUDGE_CARD_CACHE.clear()

    oct_csv = (
        "judge,votes,participation_pct,buy,sell,quiet,right_pct,scored,points_after_spread,avg_points\n"
        "value_area,120,40.0,60,60,0,36.0,60,-42.0,-0.70\n"
        "vwap_trend,110,38.0,55,55,0,39.0,50,-25.0,-0.50\n"
    )
    (tmp_path / "judge_panel_2026-10-30.csv").write_text(oct_csv, encoding="utf-8")

    # Day 1 (Tuesday 2026-11-03): pre-surgery card exists on disk, but Step 3 withholds it from Gemini
    used_tue, judges_tue, _ = jrc.build_card(tmp_path, "2026-11-03", 5, 20)
    (tmp_path / "judge_report_card.json").write_text(
        json.dumps({"source_days": used_tue, "judges": judges_tue}),
        encoding="utf-8",
    )
    tue_snap = SimpleNamespace(timestamp=datetime(2026, 11, 3, 9, 30, tzinfo=timezone.utc))
    assert s3._judge_report_card_block(tue_snap) == ""

    # Day 2 (Wednesday 2026-11-04): Tuesday's healed CSV exists -> builder drops Oct 30 and Step 3 injects card
    nov03_csv = (
        "judge,votes,participation_pct,buy,sell,quiet,right_pct,scored,points_after_spread,avg_points\n"
        "value_area,60,25.0,20,40,0,58.0,30,18.0,0.60\n"
        "vwap_trend,80,30.0,30,50,0,60.0,40,26.0,0.65\n"
    )
    (tmp_path / "judge_panel_2026-11-03.csv").write_text(nov03_csv, encoding="utf-8")
    used_wed, judges_wed, _ = jrc.build_card(tmp_path, "2026-11-04", 5, 20)
    assert used_wed == ["2026-11-03"]
    (tmp_path / "judge_report_card.json").write_text(
        json.dumps({"source_days": used_wed, "judges": judges_wed}),
        encoding="utf-8",
    )
    s3._JUDGE_CARD_CACHE.clear()
    wed_snap = SimpleNamespace(timestamp=datetime(2026, 11, 4, 9, 30, tzinfo=timezone.utc))
    wed_block = s3._judge_report_card_block(wed_snap)
    assert "JUDGE REPORT CARDS" in wed_block
    assert "2026-11-03..2026-11-03" in wed_block
    assert "2026-10-30" not in wed_block
    assert "[STRONG]" in wed_block


def test_shooting_execution_gate_enforces_stage3r_unplugged_mbo_guard(monkeypatch):
    monkeypatch.setattr(config, "STAGE3R_MBO_FEED_GUARD_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "FAMILY_GATE_LIVE_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "STAGE3B_GATE_LIVE_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "STAGE3C_SESSION_GATE_LIVE_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "STAGE3D_SNIPER_GATE_LIVE_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "EXECUTION_MODE", "python", raising=False)

    snap = SimpleNamespace(
        shadow_candidates={
            "stage3r_gate": {"ok": False, "reason": "unplugged_mbo_feed (level3=stale_or_unverifiable)"}
        },
        shot={"shot": "GO", "side": "SELL", "entry_style": "AGGRESSIVE_MARKET"},
        power={"direction": "DOWN"},
    )
    ok, reason = main._shooting_execution_gate(
        SimpleNamespace(action="SELL", confidence=80.0),
        snap,
    )
    assert ok is False
    assert "Stage 3R MBO Feed Gate failed" in reason


def test_standing_ai_slip_invalidates_on_step2_signal_direction_flip(monkeypatch):
    monkeypatch.setattr(config, "AI_STANDING_PERMISSION_ENABLED", True, raising=False)
    monkeypatch.setattr(main, "_LAST_AI_CALL", 1000.0)
    monkeypatch.setattr(
        main,
        "_STANDING_AI_SLIP",
        {
            "issued_epoch": 1000.0,
            "action": "SELL",
            "confidence": 76.0,
            "rationale": "clean sell wave",
            "model": "gemini-3.8-flash",
            "regime": "TREND",
            "price": 4000.0,
            "atr": 3.0,
        },
    )
    base_kw = dict(
        regime="TREND",
        price=3999.5,
        indicators=SimpleNamespace(atr_14=3.0),
        power={"direction": "NEITHER"},
        news=SimpleNamespace(news_state="QUIET"),
        level3=SimpleNamespace(aggressive_buy_volume=10.0, aggressive_sell_volume=25.0, ofi=-10.0),
    )

    # Same signal_direction == SELL -> serves cached slip
    same_snap = SimpleNamespace(signal_direction="SELL", **base_kw)
    d_ok = main._evaluate_standing_ai_slip(same_snap, 1030.0, 180.0, 180.0)
    assert d_ok is not None and d_ok.action == "SELL"

    # Synthetic mock without signal_direction -> still serves cached slip
    anon_snap = SimpleNamespace(**base_kw)
    d_anon = main._evaluate_standing_ai_slip(anon_snap, 1030.0, 180.0, 180.0)
    assert d_anon is not None and d_anon.action == "SELL"

    # Step 2 flipped to BUY or dropped to NEUTRAL -> invalidates stale SELL slip immediately
    for flipped in ("BUY", "NEUTRAL"):
        flip_snap = SimpleNamespace(signal_direction=flipped, **base_kw)
        assert main._evaluate_standing_ai_slip(flip_snap, 1030.0, 180.0, 180.0) is None


def test_morning_check_sh_parses_and_notes_winter_cet_offset():
    sh_path = Path(__file__).resolve().parent / "morning_check.sh"
    res = subprocess.run(["bash", "-n", str(sh_path)], capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr
    text = sh_path.read_text(encoding="utf-8")
    assert "+0100 CET winter" in text


def test_stage5b_pm_record_gone_extracts_exact_broker_sl_gain_pts_for_stage4r_shield(tmp_path, monkeypatch):
    import sys as _sys
    import position_manager as pm_mod
    import trade_history as th

    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "CONTRACT_SIZE", 100.0, raising=False)

    pm = pm_mod.PositionManager.__new__(pm_mod.PositionManager)
    pm._state = {}
    pm._hist = []

    class _FakeDeal:
        def __init__(self, entry, price, profit, volume=0.10, comment=""):
            self.entry = entry
            self.price = price
            self.profit = profit
            self.swap = 0.0
            self.commission = 0.0
            self.volume = volume
            self.comment = comment

    class _FakeMT5:
        @staticmethod
        def history_deals_get(_from_dt, _now_dt, position=0):
            assert position == 778899
            return [
                _FakeDeal(entry=0, price=4100.00, profit=0.0, volume=0.10, comment="gold-trading-bot"),
                _FakeDeal(entry=1, price=4092.00, profit=-80.0, volume=0.10, comment="[sl 4092.00]"),
            ]

    pm._mt5 = _FakeMT5()
    # Position was +0.80 pts on the previous 60s poll, then spiked down to SL @ 4092.00 (-8.00 pts)
    pm._record_gone(
        "778899",
        {
            "side": "BUY",
            "entry": 4100.00,
            "last_sl": 4092.00,
            "last_volume": 0.10,
            "last_gain_pts": 0.80,
        },
    )
    closed = th.closed_trades(limit=5)
    assert len(closed) == 1
    rec = closed[0]
    assert rec["ticket"] == "778899"
    assert abs(rec["close_price"] - 4092.00) < 1e-6
    assert abs(rec["gain_pts"] - (-8.00)) < 1e-6
    assert abs(rec["pnl_usd_est"] - (-80.00)) < 1e-6
    assert rec["estimated"] is False
    assert abs(th.day_realized_pts() - (-8.00)) < 1e-6
    assert th.recent_loss("BUY", within_minutes=30.0)["hit"] is True


def test_stage5b_a1_intrabar_serve_refreshes_stage3r_mbo_drop_fail_closed(monkeypatch):
    from datetime import datetime, timezone
    import pandas as pd
    import step2_market_analysis as s2

    monkeypatch.setattr(config, "DATA_SOURCE", "bookmapbridge", raising=False)
    monkeypatch.setattr(config, "TIMEFRAME", "M3", raising=False)
    monkeypatch.setattr(config, "A1_BASE_TF_MINUTES", 3, raising=False)
    monkeypatch.setattr(config, "CONFIDENCE_THRESHOLD", 35.0, raising=False)
    monkeypatch.setattr(config, "AI_CONFIDENCE_THRESHOLD", 35.0, raising=False)
    monkeypatch.setattr(config, "STAGE3R_MBO_FEED_GUARD_ENABLED", True, raising=False)

    t_bar = datetime(2026, 11, 3, 10, 3, 5, tzinfo=timezone.utc)
    t_srv = datetime(2026, 11, 3, 10, 4, 5, tzinfo=timezone.utc)
    idx = pd.date_range("2026-11-03 09:00:00", periods=21, freq="3min", tz="UTC")
    candles = pd.DataFrame(
        {
            "open": [4000.0] * 21,
            "high": [4001.5] * 21,
            "low": [3998.5] * 21,
            "close": [4000.2] * 21,
            "volume": [120.0] * 21,
        },
        index=idx,
    )
    cached_snap = SimpleNamespace(
        timestamp=t_bar,
        price=4000.2,
        bid=4000.1,
        ask=4000.3,
        volume=120.0,
        spread_pct=0.005,
        signal_direction="SELL",
        signal_strength=48.0,
        confidence=75.0,
        regime="TREND",
        power={"direction": "NEITHER"},
        power_v2={"direction": "NEITHER"},
        news=SimpleNamespace(news_state="QUIET"),
        divergence=SimpleNamespace(),
        footprint=SimpleNamespace(),
        order_flow=SimpleNamespace(),
        level3=SimpleNamespace(),
        signal_map={},
        shot={
            "shot": "GO",
            "side": "SELL",
            "entry_style": "AGGRESSIVE_MARKET",
            "authorized_by": "STAGE3D_SNIPER",
            "why": ["Stage-3d Sniper setup"],
        },
        shadow_candidates={
            "stage3d_gate": {"ok": True, "sniper_authorized": True},
            "stage3r_gate": {"ok": True, "reason": "ok", "level3_events": "available"},
        },
        data_quality={"trade_prints": "available", "level3_events": "available", "quote": "valid"},
        notes=["initial bar close"],
    )
    md_bar = {"symbol": "GCG6", "candles": candles}
    s2._a1_cache_store(cached_snap, md_bar, t_bar, spread_now=0.20)
    try:
        ticks = [
            {"timestamp": t_srv.timestamp() - i, "price": 4000.1, "volume": 2.0, "side": "SELL"}
            for i in range(25)
        ]
        md_srv = {
            "symbol": "GCG6",
            "source": "bookmapbridge",
            "candles": candles,
            "bid": 4000.0,
            "ask": 4000.2,
            "spread_pct": 0.005,
            "volume": 50.0,
            "tick_data": ticks,
            "order_events": [],  # MBO feed dropped mid-bar!
            "data_quality": {"trade_prints": "available", "quote": "valid"},
            "bid_depth": {4000.0: 15.0, 3999.9: 20.0},
            "ask_depth": {4000.2: 15.0, 4000.3: 20.0},
        }
        served = s2._a1_try_serve(md_srv, t_srv, 4000.1)
        assert served is not None
        s3r = served.shadow_candidates["stage3r_gate"]
        assert s3r["ok"] is False
        assert s3r["a1_serve_refreshed"] is True
        assert served.confidence <= 30.0
        assert served.shot["shot"] == "WAIT"
        ok, reason = main._shooting_execution_gate(
            SimpleNamespace(action="SELL", confidence=80.0),
            served,
        )
        assert ok is False
        assert "Stage 3R MBO Feed Gate failed" in reason
    finally:
        s2._A1_BAR_SERVICE_CACHE.clear()


def test_stage5b_rig_memory_evening_gate_passes_12_of_12():
    import sys
    rig_path = Path(__file__).resolve().parent / "rig_memory.py"
    res = subprocess.run([sys.executable, str(rig_path)], capture_output=True, text=True, check=False)
    assert res.returncode == 0, f"{res.stdout}\n{res.stderr}"
    assert "ALL PASS (12/12)" in res.stdout


def test_stage_s1_a1_refresh_scout_uses_active_m3_candles_tf_for_atr_and_keeps_m3_doors(monkeypatch):
    from datetime import datetime, timezone
    import signal_team
    import step2_market_analysis as s2

    monkeypatch.setattr(config, "TIMEFRAME", "M3", raising=False)
    monkeypatch.setattr(config, "A1_BASE_TF_MINUTES", 3, raising=False)
    monkeypatch.setattr(config, "L3_WHALE_THRESHOLD", 10.0, raising=False)
    signal_team._BOOK = None
    try:
        # Seed a real M3 outer whale door $16.00 above price (3.2x M3 ATR=5.0 -> inside 4*ATR=20.0 band,
        # whereas under old M1 ATR=1.50 -> 10.7x ATR > 6.0*ATR eviction cutoff!).
        book = signal_team.get_book()
        l3_seed = SimpleNamespace(
            order_book={"bids": [(3999.0, 8.0)], "asks": [(4016.0, 35.0), (4015.5, 5.0), (4016.5, 5.0)]},
            spoof_levels={},
            iceberg_levels={},
            iceberg_meta={},
            order_events=[],
            large_order_events=1,
            order_book_imbalance=-0.2,
            ofi=-10.0,
        )
        smap0 = book.ingest(l3_seed, 4000.0, 5.0, config, spread=0.20)
        assert any(abs(d["price"] - 4016.0) < 1e-6 for d in smap0["above"])

        # Intra-bar A1 serve: `candles` are tight M1 bars (range=1.50), `candles_tf` are M3 bars (range=5.00)
        m1_candles = {
            "open": [4000.0] * 30,
            "high": [4000.75] * 30,
            "low": [3999.25] * 30,
            "close": [4000.0] * 30,
            "volume": [40.0] * 30,
        }
        m3_candles = {
            "open": [4000.0] * 20,
            "high": [4002.50] * 20,
            "low": [3997.50] * 20,
            "close": [4000.0] * 20,
            "volume": [120.0] * 20,
        }
        served = SimpleNamespace(
            timestamp=datetime(2026, 11, 3, 10, 4, 5, tzinfo=timezone.utc),
            data_quality={},
        )
        md_srv = {
            "symbol": "GCG6",
            "candles": m1_candles,
            "candles_is_m1": True,
            "candles_tf": m3_candles,
            "bid": 3999.9,
            "ask": 4000.1,
            "bid_depth": {3999.9: 8.0, 3999.0: 8.0},
            "ask_depth": {4000.1: 8.0, 4015.5: 5.0, 4016.0: 35.0, 4016.5: 5.0},
            "tick_data": [],
            "order_events": [],
        }
        _notes, atr_live = s2._a1_refresh_scout(md_srv, served, 4000.0, 0.20)
        assert atr_live >= 4.5, f"Expected M3 ATR (~5.0), got {atr_live}"
        assert 4016.0 in signal_team.get_book().levels, "M3 outer door at 4016.0 must not be evicted by M1 ATR!"
    finally:
        signal_team._BOOK = None
