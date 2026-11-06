"""
Stage P3 (2026-11-06) Regression Suite — Final POWER Section (#7) & Bookmark #12 Leak #2:
- `power_m5_adapter.py`: Kyle's lambda passive-absorption damper on `big_prints` (`42.9%` WR ->
  damps `* 0.35` when large aggressive block prints push one way yet bar price displaces >= 2 ticks
  the opposite way, while preserving aligned block prints untouched).
- `step2_market_analysis.py` (`Stage 3d`):
  1) `STAGE3D_BLOCK_POWER_OPPOSITION` (`True`): blocks Sniper marketable entry when canonical
     `POWER v2` actively opposes `direction` (`33.3%` +12m WR, `-2.18` pts/sig in `replay-22`).
  2) `STAGE3D_REQUIRE_TREND_REGIME` (`True`): prevents `_s3d_power_envelope` from fabricating
     `"market_regime": "TREND"` when NEITHER Step 2's active-timeframe ADX (`snap.regime`) NOR
     `POWER v2` (`market_regime`) confirms `"TREND"` (`Bookmark #12` Leak #2 `2026-11-04` `-$8.87`
     range-bottom SELL & `replay-22` `SELL | RANGE` `0/6` `-41.9 PM`, `SELL | NEUTRAL` `0/6` `-65.3 PM`),
     while preserving genuine `TREND` Sniper authorization when either classifier confirms `"TREND"`.
- `tools/raw_replay.py`: `[2] GATE WATERFALL` Stage-P3 reason rows + `[8f]` line `5) Step-P3 Regime & Envelope Audit`.
"""
import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np

import config
import main
from power_m5_adapter import build_m5_inputs
import power_v2_shadow
import shooting_team
import signal_team
import step2_market_analysis as s2
from test_nov02_stage3d_sniper_shooting_handoff_and_climax_guard import (
    _configure_stage3d_env,
    _make_clean_m3_market_data,
    _mock_sell_aggregate,
)
from tools import raw_replay as rr


def _make_flat_range_m3_market_data(
    now_dt: datetime,
    *,
    price: float = 4000.0,
    door_price: float = 3995.0,
    door_side: str = "bids",
    door_size: float = 120.0,
):
    """Build a 180-minute balanced sine-wave range (ADX < 20 -> snap.regime in {'RANGE', 'NEUTRAL'})."""
    md = _make_clean_m3_market_data(
        now_dt, drift=-0.35, price=price, door_price=door_price, door_side=door_side, door_size=door_size
    )
    n_m1 = 180
    closes = np.array(
        [price + 0.55 * math.sin(i * 0.45) for i in range(n_m1)],
        dtype=float,
    )
    closes[-1] = price
    opens = np.roll(closes, 1)
    opens[0] = closes[0]
    highs = np.maximum(opens, closes) + 0.35
    lows = np.minimum(opens, closes) - 0.35
    md["candles"] = {
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": np.full(n_m1, 120.0, dtype=float),
    }
    return md


def test_stage_p3_config_defaults_and_shadow_regime_flags_intact(monkeypatch):
    with monkeypatch.context() as mp:
        for k in (
            "STAGE3D_BLOCK_POWER_OPPOSITION",
            "STAGE3D_REQUIRE_TREND_REGIME",
            "POWER_FLAT_LAKE_GUARD_ENABLED",
            "POWER_VR_GUARD_ENABLED",
            "AGGRESSIVE_ENTRY_ENABLED",
            "LIMIT_ORDER_ENABLED",
        ):
            mp.delenv(k, raising=False)
        config._refresh()
        assert getattr(config, "STAGE3D_BLOCK_POWER_OPPOSITION", False) is True
        assert getattr(config, "STAGE3D_REQUIRE_TREND_REGIME", False) is True
        # Law #5: ER_10 flat-lake & VR(4) guards remain shadow-only (=0) by default
        assert getattr(config, "POWER_FLAT_LAKE_GUARD_ENABLED", True) is False
        assert getattr(config, "POWER_VR_GUARD_ENABLED", True) is False
        assert getattr(config, "AGGRESSIVE_ENTRY_ENABLED", None) is False
        assert getattr(config, "LIMIT_ORDER_ENABLED", None) is False
    config._refresh()


def test_stage_p3_big_prints_kyle_lambda_damps_absorbed_stop_run_only():
    now_dt = datetime(2026, 11, 6, 15, 6, 0, tzinfo=timezone.utc)
    bar_start = datetime(2026, 11, 6, 15, 3, 0, tzinfo=timezone.utc)

    # 1) Aligned breakout: 20 trades rising from 4000.0 -> 4001.5 with a 25-lot BUY print -> NOT damped
    aligned_rows = []
    for i in range(20):
        aligned_rows.append({
            "timestamp": (bar_start + timedelta(seconds=5 + i * 7)).isoformat(),
            "price": round(4000.0 + 0.08 * i, 2),
            "volume": 25.0 if i == 10 else 1.0,
            "side": "BUY",
            "is_direct": True,
        })
    built_aligned = build_m5_inputs(
        aligned_rows, now=now_dt, tick_size=0.10, bar_seconds=180, min_trades=12, min_big_print_size=5.0
    )
    assert built_aligned["diagnostics"]["big_print_absorption_damped"] is False
    val_aligned = built_aligned["judges"]["big_prints"]["value"]
    assert val_aligned > 0.50

    # 2) Absorbed stop-run: same 25-lot BUY print, but passive sellers push bar down 4001.5 -> 4000.0 (-15 ticks)
    absorbed_rows = []
    for i in range(20):
        absorbed_rows.append({
            "timestamp": (bar_start + timedelta(seconds=5 + i * 7)).isoformat(),
            "price": round(4001.5 - 0.08 * i, 2),
            "volume": 25.0 if i == 4 else 1.0,
            "side": "BUY",
            "is_direct": True,
        })
    built_absorbed = build_m5_inputs(
        absorbed_rows, now=now_dt, tick_size=0.10, bar_seconds=180, min_trades=12, min_big_print_size=5.0
    )
    assert built_absorbed["diagnostics"]["big_print_absorption_damped"] is True
    val_absorbed = built_absorbed["judges"]["big_prints"]["value"]
    assert abs(val_absorbed - 0.35 * val_aligned) < 1e-6


def test_stage_p3_blocks_sniper_when_power_actively_opposes_direction(monkeypatch, tmp_path):
    _configure_stage3d_env(monkeypatch, tmp_path)
    signal_team._BOOK = None
    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None
    now_dt = datetime(2026, 10, 29, 10, 15, tzinfo=timezone.utc)
    now_ts = now_dt.timestamp()
    shooting_team._ROAD_HISTORY.clear()
    for k in range(15):
        shooting_team._ROAD_HISTORY.append((now_ts - (15 - k) * 10.0, 25.0))

    md = _make_clean_m3_market_data(
        now_dt, drift=-0.40, price=4000.0, door_price=3995.0, door_side="bids", door_size=120.0
    )
    monkeypatch.setattr(s2.SignalEngine, "aggregate", _mock_sell_aggregate)
    # Canonical POWER v2 actively opposes SELL with UP (72% dominance)
    monkeypatch.setattr(
        power_v2_shadow,
        "run_power_v2",
        lambda *a, **k: {
            "team": "POWER",
            "version": "2.2.0",
            "timeframe": "M3",
            "direction": "UP",
            "up_power_pct": 72.0,
            "down_power_pct": 28.0,
            "power_total_pct": 100.0,
            "reason_code": "UPWARD_FORCE_DOMINATES",
            "shadow_only": False,
            "context": {"market_regime": "TREND", "feed_stale": False, "data_quality_ok": True},
            "regime_diagnostics": {"market_regime": "TREND", "latest_tick_age_seconds": 1.0},
        },
    )

    snap = s2.analyze_market(md, now=now_dt)
    assert snap.power["direction"] == "UP"
    s3d = snap.shadow_candidates["stage3d_gate"]
    assert s3d["ok"] is False
    assert s3d["reason"] == "power_opposes_direction"
    assert s3d["sniper_authorized"] is False
    assert s3d["live_capped"] is True
    assert snap.confidence <= 30.0
    assert snap.shot["shot"] == "WAIT"

    ok_exec, why_exec = main._shooting_execution_gate(
        SimpleNamespace(action="SELL", confidence=75.0), snap
    )
    assert ok_exec is False
    assert "power_opposes_direction" in why_exec
    shooting_team._ROAD_HISTORY.clear()


def test_stage_p3_blocks_fake_trend_stamp_in_double_non_trend_range(monkeypatch, tmp_path):
    """Bookmark #12 Leak #2 (2026-11-04 pos=98376889 & replay-22 SELL|RANGE 0/6):
    when NEITHER Step 2 nor POWER v2 sees TREND, Stage 3d must NOT stamp market_regime='TREND'."""
    _configure_stage3d_env(monkeypatch, tmp_path)
    signal_team._BOOK = None
    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None
    now_dt = datetime(2026, 10, 29, 10, 15, tzinfo=timezone.utc)
    now_ts = now_dt.timestamp()
    shooting_team._ROAD_HISTORY.clear()
    for k in range(15):
        shooting_team._ROAD_HISTORY.append((now_ts - (15 - k) * 10.0, 25.0))

    md_range = _make_flat_range_m3_market_data(
        now_dt, price=4000.0, door_price=3995.0, door_side="bids", door_size=120.0
    )
    monkeypatch.setattr(s2.SignalEngine, "aggregate", _mock_sell_aggregate)
    monkeypatch.setattr(
        power_v2_shadow,
        "run_power_v2",
        lambda *a, **k: {
            "team": "POWER",
            "version": "2.2.0",
            "timeframe": "M3",
            "direction": "NEITHER",
            "up_power_pct": 50.0,
            "down_power_pct": 50.0,
            "power_total_pct": 100.0,
            "reason_code": "RANGE_NO_CONFIRMED_BREAKOUT",
            "shadow_only": False,
            "context": {"market_regime": "RANGE", "feed_stale": False, "data_quality_ok": True},
            "regime_diagnostics": {"market_regime": "RANGE", "latest_tick_age_seconds": 1.0},
        },
    )

    snap = s2.analyze_market(md_range, now=now_dt)
    assert snap.regime in ("RANGE", "NEUTRAL")
    s3d = snap.shadow_candidates["stage3d_gate"]
    assert s3d["ok"] is True
    assert s3d["reason"] == "non_trend_regime"
    assert s3d["sniper_blocked_reason"] == "non_trend_regime"
    assert s3d["either_trend_confirmed"] is False
    assert s3d["sniper_authorized"] is False
    assert s3d["live_capped"] is False
    assert snap.confidence == 74.0
    assert snap.shot["shot"] == "WAIT"
    ok_exec_nt, why_exec_nt = main._shooting_execution_gate(
        SimpleNamespace(action="SELL", confidence=75.0), snap
    )
    assert ok_exec_nt is False
    assert "POWER v2" in why_exec_nt or "WAIT" in why_exec_nt

    # Conversely, if POWER's tick-bar classifier already confirms TREND (even while Step 2 candles
    # are transitioning out of RANGE/NEUTRAL) and POWER direction is NEITHER, Sniper GO is preserved!
    signal_team._BOOK = None
    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None
    monkeypatch.setattr(
        power_v2_shadow,
        "run_power_v2",
        lambda *a, **k: {
            "team": "POWER",
            "version": "2.2.0",
            "timeframe": "M3",
            "direction": "NEITHER",
            "up_power_pct": 50.0,
            "down_power_pct": 50.0,
            "power_total_pct": 100.0,
            "reason_code": "INSUFFICIENT_EDGE",
            "shadow_only": False,
            "context": {"market_regime": "TREND", "feed_stale": False, "data_quality_ok": True},
            "regime_diagnostics": {"market_regime": "TREND", "latest_tick_age_seconds": 1.0},
        },
    )
    snap_pw_trend = s2.analyze_market(md_range, now=now_dt)
    s3d_ok = snap_pw_trend.shadow_candidates["stage3d_gate"]
    assert s3d_ok["ok"] is True
    assert s3d_ok["either_trend_confirmed"] is True
    assert s3d_ok["sniper_authorized"] is True
    assert snap_pw_trend.shot["shot"] == "GO"
    shooting_team._ROAD_HISTORY.clear()


def test_stage_p3_raw_replay_renders_step_p3_envelope_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "reload_env", lambda *a, **k: None)
    monkeypatch.setattr(config, "TIMEFRAME", "M3", raising=False)
    monkeypatch.setenv("TIMEFRAME", "M3")
    monkeypatch.setenv("CONFIDENCE_THRESHOLD", "35")

    from test_stage1_timeframe_whatif_and_replay import _write_synthetic_raw_day
    proj = tmp_path / "proj_p3"
    data_dir = proj / "data"
    _write_synthetic_raw_day(data_dir, "2026-10-29", start_px=4000.0, drift_per_min=0.30, minutes=90)

    res = rr.replay_single_day("2026-10-29", root_dir=proj, data_dir=data_dir, every_s=180)
    txt, _, _ = rr.synthesize_multi_day([res])
    assert "5) Step-P3 Regime & Envelope Audit:" in txt
    assert "oppose_veto=" in txt
    assert "non_trend_veto=" in txt
