"""
Stage 3d (2026-11-02) Regression Suite:
- Late POWER v2 Climax Veto (`N=24`: 33.3% WR, -16.7 FIX_PTS on 5-day M3 replay)
- Stage-3 Sniper Panel -> Shooting (`plan_shot`) marketable authorization (`N=177` non-climax:
  66.9% WR, +830.1 FIX_PTS, +$4.69/sig) using full Scout-door + Shelter/ATR + R:R>=1.20 + Crowd v2 geometry
- Pure `snap.power` / `snap.power_v2` preservation & pinned legacy config contract safety
- Active-timeframe M3 (`180s`, `:00/:03/:06..`) quiet-bar feed-quality verification + Law #11 `min_trades=12` & `Crowd v2` window scaling
- `tools/raw_replay.py` `snap.shot` wiring + `[8b] SHOOTING CORRIDOR & SCOUT-BRACKET DIAGNOSTIC`
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np

import config
import main
import shooting_team
import signal_team
import step2_market_analysis as s2
from tools import raw_replay as rr


def _make_clean_m3_market_data(
    now_dt: datetime,
    *,
    drift: float = -0.35,
    price: float = 4000.0,
    door_price: float = 3992.0,
    door_side: str = "bids",
    door_size: float = 35.0,
    is_direct: bool = True,
):
    n_m1 = 180
    base = price - drift * 60.0
    closes = np.array([base + drift * (i / 3.0) for i in range(n_m1)], dtype=float)
    closes[-1] = price
    opens = closes - drift * 0.25
    highs = np.maximum(opens, closes) + 0.45
    lows = np.minimum(opens, closes) - 0.45
    vols = np.full(n_m1, 120.0, dtype=float)

    ep_now = now_dt.timestamp()
    ticks = []
    for k in range(36):
        ts = datetime.fromtimestamp(ep_now - (36 - k) * 2.0, tz=timezone.utc).isoformat()
        ticks.append({
            "timestamp": ts,
            "price": round(price + (0.05 if k % 3 == 0 else -0.05), 2),
            "volume": 8.0,
            "side": "SELL" if drift < 0 else "BUY",
            "is_direct": is_direct,
        })

    bids = [[round(price - 0.10 - 0.20 * j, 2), 3.0] for j in range(1, 10)]
    asks = [[round(price + 0.10 + 0.20 * j, 2), 3.0] for j in range(1, 10)]
    if door_side == "bids":
        bids.append([round(door_price, 2), float(door_size)])
    else:
        asks.append([round(door_price, 2), float(door_size)])

    return {
        "symbol": "GCZ6",
        "data_source": "replay",
        "price": price,
        "bid": round(price - 0.10, 2),
        "ask": round(price + 0.10, 2),
        "spread": 0.20,
        "volume": 3200.0,
        "has_data": True,
        "last_data_age_seconds": 1.0,
        "last_trade_age_seconds": 1.0,
        "quote_age_seconds": 1.0,
        "depth_age_seconds": 1.0,
        "quote_timestamp": now_dt.isoformat(),
        "depth_timestamp": now_dt.isoformat(),
        "data_quality": {"quote": "valid", "level2": "available", "level3": "available"},
        "candles_is_m1": True,
        "candles": {
            "open": opens, "high": highs, "low": lows, "close": closes, "volume": vols,
        },
        "tick_data": ticks,
        "bid_depth": {row[0]: row[1] for row in bids},
        "ask_depth": {row[0]: row[1] for row in asks},
        "order_book": {"bids": bids, "asks": asks},
        "macro": {},
        "news": {"headlines": [], "events": [], "fetch_calendar": False},
    }


def _configure_stage3d_env(monkeypatch, tmp_path):
    monkeypatch.delenv("DATA_SOURCE", raising=False)
    monkeypatch.delenv("AGGRESSIVE_ENTRY_ENABLED", raising=False)
    monkeypatch.delenv("LIMIT_ORDER_ENABLED", raising=False)
    monkeypatch.delenv("SCOUT_MIN_TARGET_USD", raising=False)
    monkeypatch.delenv("SCOUT_MIN_SPREADS", raising=False)
    monkeypatch.delenv("L3_WHALE_THRESHOLD", raising=False)
    for mod in {config, getattr(s2, "config", config), getattr(main, "config", config)}:
        monkeypatch.setattr(mod, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(mod, "DATA_SOURCE", "replay", raising=False)
        monkeypatch.setattr(mod, "TIMEFRAME", "M3", raising=False)
        monkeypatch.setattr(mod, "BASE_TF_MINUTES", 3, raising=False)
        monkeypatch.setattr(mod, "A1_BASE_TF_MINUTES", 3, raising=False)
        monkeypatch.setattr(mod, "CONFIDENCE_THRESHOLD", 35.0, raising=False)
        monkeypatch.setattr(mod, "FAMILY_GATE_LIVE_ENABLED", True, raising=False)
        monkeypatch.setattr(mod, "STAGE3B_GATE_LIVE_ENABLED", True, raising=False)
        monkeypatch.setattr(mod, "STAGE3C_SESSION_GATE_LIVE_ENABLED", True, raising=False)
        monkeypatch.setattr(mod, "STAGE3D_SNIPER_GATE_LIVE_ENABLED", True, raising=False)
        monkeypatch.setattr(mod, "STAGE3D_BLOCK_POWER_CLIMAX", True, raising=False)
        monkeypatch.setattr(mod, "STAGE3D_SNIPER_ENTRY_ENABLED", True, raising=False)
        monkeypatch.setattr(mod, "SHOOTING_EXECUTION_GATE_ENABLED", True, raising=False)
        monkeypatch.setattr(mod, "EXECUTION_MODE", "python", raising=False)
        monkeypatch.setattr(mod, "LIMIT_ORDER_ENABLED", False, raising=False)
        monkeypatch.setattr(mod, "AGGRESSIVE_ENTRY_ENABLED", False, raising=False)
        monkeypatch.setattr(mod, "L3_WHALE_THRESHOLD", 10.0, raising=False)
        monkeypatch.setattr(mod, "SCOUT_MIN_SPREADS", 10.0, raising=False)
        monkeypatch.setattr(mod, "SCOUT_MIN_TARGET_USD", 0.0, raising=False)
        monkeypatch.setattr(mod, "SCOUT_ATR_FLOOR_PCT", 0.0004, raising=False)
        monkeypatch.setattr(mod, "SHOOT_DOOR_MARGIN", 3.0, raising=False)
        monkeypatch.setattr(mod, "SHOOT_ROAD_NORMAL_MAX", 2.0, raising=False)
        monkeypatch.setattr(mod, "SHOOT_PARTICIPATION_MAX", 0.10, raising=False)
        monkeypatch.setattr(mod, "SHOOT_MIN_RR", 1.20, raising=False)
        monkeypatch.setattr(mod, "SHOOT_MIN_REWARD_USD", 0.0, raising=False)
        monkeypatch.setattr(mod, "SHOOT_EFFECTIVE_CFD_SPREAD_PTS", 0.0, raising=False)
        monkeypatch.setattr(mod, "A2_MIN_CLEAR_WALL_PTS", 1.50, raising=False)


def _mock_sell_aggregate(self, *a, **k):
    return (
        -55.0,
        "SELL",
        74.0,
        [
            "MTF M9:DOWN M36:DOWN -> SELL w 0.50",
            "L3 NET FLOW SELL -60.0 (buys 5.0 vs sells 65.0) w 1.00",
        ],
    )


def test_stage3d_defaults_and_pinned_legacy_flags_intact(monkeypatch):
    with monkeypatch.context() as mp:
        for k in (
            "STAGE3D_SNIPER_GATE_LIVE_ENABLED",
            "STAGE3D_BLOCK_POWER_CLIMAX",
            "STAGE3D_SNIPER_ENTRY_ENABLED",
            "AGGRESSIVE_ENTRY_ENABLED",
            "LIMIT_ORDER_ENABLED",
        ):
            mp.delenv(k, raising=False)
        config._refresh()
        assert getattr(config, "STAGE3D_SNIPER_GATE_LIVE_ENABLED", False) is True
        assert getattr(config, "STAGE3D_BLOCK_POWER_CLIMAX", False) is True
        assert getattr(config, "STAGE3D_SNIPER_ENTRY_ENABLED", False) is True
        # Pinned legacy code defaults MUST stay False when unset in .env
        assert getattr(config, "AGGRESSIVE_ENTRY_ENABLED", None) is False
        assert getattr(config, "LIMIT_ORDER_ENABLED", None) is False
    config._refresh()


def test_stage3d_late_power_climax_caps_confidence_and_fails_closed_in_main(monkeypatch, tmp_path):
    _configure_stage3d_env(monkeypatch, tmp_path)
    signal_team._BOOK = None
    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None
    now_dt = datetime(2026, 10, 29, 10, 12, tzinfo=timezone.utc)  # Active London morning
    md = _make_clean_m3_market_data(now_dt, drift=-0.40, price=4000.0, door_price=3991.0, door_side="bids")

    monkeypatch.setattr(s2.SignalEngine, "aggregate", _mock_sell_aggregate)
    import power_v2_shadow
    monkeypatch.setattr(
        power_v2_shadow,
        "run_power_v2",
        lambda *a, **k: {
            "team": "POWER",
            "version": "2.2.0",
            "timeframe": "M5",
            "direction": "DOWN",
            "up_power_pct": 22.0,
            "down_power_pct": 78.0,
            "power_total_pct": 100.0,
            "reason_code": "DOWNWARD_FORCE_DOMINATES",
            "shadow_only": False,
            "context": {"market_regime": "TREND", "feed_stale": False, "data_quality_ok": True},
            "regime_diagnostics": {"market_regime": "TREND", "latest_tick_age_seconds": 1.0},
        },
    )

    snap = s2.analyze_market(md, now=now_dt)
    s3d = snap.shadow_candidates["stage3d_gate"]
    assert s3d["ok"] is False
    assert s3d["reason"] == "late_power_climax"
    assert s3d["live_capped"] is True
    assert s3d["sniper_authorized"] is False
    assert snap.confidence <= 30.0
    ok_exec, why_exec = main._shooting_execution_gate(
        SimpleNamespace(action="SELL", confidence=75.0), snap
    )
    assert ok_exec is False
    assert "Stage 3d Climax Gate failed" in why_exec


def test_stage3d_sniper_authorizes_shooting_go_with_scout_geometry_when_power_neither(monkeypatch, tmp_path):
    _configure_stage3d_env(monkeypatch, tmp_path)
    signal_team._BOOK = None
    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None
    now_dt = datetime(2026, 10, 29, 10, 15, tzinfo=timezone.utc)  # Exact :15 M5 boundary!
    now_ts = now_dt.timestamp()
    shooting_team._ROAD_HISTORY.clear()
    for k in range(15):
        shooting_team._ROAD_HISTORY.append((now_ts - (15 - k) * 10.0, 25.0))

    md = _make_clean_m3_market_data(
        now_dt, drift=-0.40, price=4000.0, door_price=3995.0, door_side="bids", door_size=120.0
    )
    monkeypatch.setattr(s2.SignalEngine, "aggregate", _mock_sell_aggregate)
    import power_v2_shadow
    # Simulate exact M5 boundary where POWER v2 says NEITHER + data_quality_ok=False due to <20 ticks in new 5m bar
    monkeypatch.setattr(
        power_v2_shadow,
        "run_power_v2",
        lambda *a, **k: {
            "team": "POWER",
            "version": "2.2.0",
            "timeframe": "M5",
            "direction": "NEITHER",
            "up_power_pct": 50.0,
            "down_power_pct": 50.0,
            "power_total_pct": 100.0,
            "reason_code": "BAD_DATA_QUALITY",
            "shadow_only": False,
            "context": {"market_regime": "RANGE", "feed_stale": False, "data_quality_ok": False},
            "regime_diagnostics": {"market_regime": "RANGE", "latest_tick_age_seconds": 1.0},
        },
    )

    snap = s2.analyze_market(md, now=now_dt)
    # Raw snap.power and snap.power_v2 remain 100% pure NEITHER
    assert snap.power["direction"] == "NEITHER"
    assert snap.power_v2["direction"] == "NEITHER"

    s3d = snap.shadow_candidates["stage3d_gate"]
    assert s3d["ok"] is True
    assert s3d["sniper_authorized"] is True
    assert snap.confidence == 74.0

    # Shooting plan is GO via Stage-3d Sniper using full Scout-door + 2*ATR geometry (NOT 1.8 A2 burst!)
    assert snap.shot["shot"] == "GO", f"Expected GO, got {snap.shot}"
    assert snap.shot["side"] == "SELL"
    assert snap.shot["entry_style"] == "AGGRESSIVE_MARKET"
    assert snap.shot["authorized_by"] == "STAGE3D_SNIPER"
    assert snap.shot["stop_source"] != "A2_BURST_FIXED"
    assert snap.shot["rr"] >= 1.20
    assert snap.shot["target"] < snap.shot["entry"] < snap.shot["stop"]

    ok_exec, why_exec = main._shooting_execution_gate(
        SimpleNamespace(action="SELL", confidence=75.0), snap
    )
    assert ok_exec is True
    assert "GO" in why_exec


def test_stage3d_sniper_still_fails_closed_on_bad_rr_or_untrusted_ticks(monkeypatch, tmp_path):
    _configure_stage3d_env(monkeypatch, tmp_path)
    signal_team._BOOK = None
    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None
    now_dt = datetime(2026, 10, 29, 10, 18, tzinfo=timezone.utc)
    now_ts = now_dt.timestamp()
    shooting_team._ROAD_HISTORY.clear()
    for k in range(15):
        shooting_team._ROAD_HISTORY.append((now_ts - (15 - k) * 10.0, 25.0))

    # 1) Door too close (3998.5 -> only 1.5 pts below 4000.0 -> R:R < 1.20) must WAIT!
    md_close = _make_clean_m3_market_data(
        now_dt, drift=-0.40, price=4000.0, door_price=3998.5, door_side="bids", door_size=40.0
    )
    monkeypatch.setattr(s2.SignalEngine, "aggregate", _mock_sell_aggregate)
    snap_close = s2.analyze_market(md_close, now=now_dt)
    assert snap_close.shot["shot"] == "WAIT"
    ok_exec, _ = main._shooting_execution_gate(
        SimpleNamespace(action="SELL", confidence=75.0), snap_close
    )
    assert ok_exec is False

    # 2) Untrusted side ticks (`is_direct=False`) at M5 boundary must NOT pass verified quality!
    signal_team._BOOK = None
    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None
    md_bad_side = _make_clean_m3_market_data(
        now_dt, drift=-0.40, price=4000.0, door_price=3995.0, door_side="bids", door_size=120.0, is_direct=False
    )
    snap_bad = s2.analyze_market(md_bad_side, now=now_dt)
    assert snap_bad.shot["shot"] == "WAIT"


def test_raw_replay_reads_snap_shot_and_renders_section_8b(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "reload_env", lambda *a, **k: None)
    monkeypatch.setattr(config, "TIMEFRAME", "M3", raising=False)
    monkeypatch.setenv("TIMEFRAME", "M3")
    monkeypatch.setenv("CONFIDENCE_THRESHOLD", "35")

    from test_stage1_timeframe_whatif_and_replay import _write_synthetic_raw_day
    proj = tmp_path / "proj"
    data_dir = proj / "data"
    _write_synthetic_raw_day(data_dir, "2026-10-29", start_px=4000.0, drift_per_min=0.30, minutes=140)

    real_analyze = s2.analyze_market

    def _patched_analyze(md, now=None):
        snap = real_analyze(md, now=now)
        if getattr(snap, "signal_direction", "NEUTRAL") in ("BUY", "SELL"):
            px = float(snap.price)
            snap.confidence = 72.0
            # Populate real `snap.shot` (NOT `snap.shooting_plan`) to verify `raw_replay.py` reads `snap.shot`!
            snap.shot = {
                "shot": "GO",
                "side": getattr(snap, "signal_direction", "BUY"),
                "entry_style": "AGGRESSIVE_MARKET",
                "entry": px + 0.10,
                "stop": (px - 2.50) if snap.signal_direction == "BUY" else (px + 2.50),
                "target": (px + 3.50) if snap.signal_direction == "BUY" else (px - 3.50),
                "rr": 1.36,
                "why": ["Stage 3d Sniper authorized", "Scout door clear"],
            }
        return snap

    monkeypatch.setattr(s2, "analyze_market", _patched_analyze)
    res = rr.replay_single_day("2026-10-29", root_dir=proj, data_dir=data_dir, every_s=180)
    txt, _, _ = rr.synthesize_multi_day([res])
    assert "SHOOTING GO (AGGRESSIVE_MARKET)" in txt
    assert "[8b] SHOOTING CORRIDOR & SCOUT-BRACKET DIAGNOSTIC" in txt
    assert "Scout Door Bracket:" in txt


def test_stage3d_law11_m3_clock_and_min_trades_calibration(tmp_path, monkeypatch):
    """Law #11: M3 (3m = 180s) scales POWER bar_seconds=180 (:00/:03/:06..), min_trades=12, and Crowd v2 window."""
    from power_v2_shadow import run_power_v2

    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path), raising=False)
    monkeypatch.delenv("POWER_BAR_SECONDS", raising=False)
    monkeypatch.setattr(config, "TIMEFRAME", "M3", raising=False)
    monkeypatch.setattr(config, "BASE_TF_MINUTES", 3, raising=False)
    # Even if POWER_BAR_SECONDS was still sitting at module-import 300, M3 must resolve to 180s!
    monkeypatch.setattr(config, "POWER_BAR_SECONDS", 300, raising=False)

    now_m3 = datetime(2026, 10, 29, 13, 6, 10, tzinfo=timezone.utc)  # :06 + 10s (>5s grace -> 13:03..13:06 M3 bar)
    ticks_14 = []
    for k in range(14):
        ts = now_m3 - timedelta(seconds=15 + k * 10)  # 14 prints inside [13:03, 13:06] (12 <= 14 < 20!)
        ticks_14.append(
            {
                "timestamp": ts.isoformat(),
                "price": round(4000.0 + 0.1 * (k % 3), 2),
                "volume": 2.0,
                "side": "BUY" if k % 2 == 0 else "SELL",
                "is_direct": True,
            }
        )
    # Previous M3 bar [13:00, 13:03] also has 14 prints
    for k in range(14):
        ts = now_m3 - timedelta(seconds=195 + k * 10)
        ticks_14.append(
            {
                "timestamp": ts.isoformat(),
                "price": round(3999.8 + 0.1 * (k % 3), 2),
                "volume": 2.0,
                "side": "BUY" if k % 2 == 0 else "SELL",
                "is_direct": True,
            }
        )
    ticks_14.sort(key=lambda r: r["timestamp"])

    res_m3 = run_power_v2(ticks_14, now=now_m3, symbol="GCZ6", tick_size=0.10, bar_seconds=180)
    assert res_m3["timeframe"] == "M3"
    assert res_m3["bar_end_utc"] == "2026-10-29T13:06:00Z"
    assert res_m3["adapter_diagnostics"]["minimum_current_trades"] == 12
    assert res_m3["context"]["data_quality_ok"] is True

    # And step2_market_analysis resolves _power_bar_seconds == 180 (M3) dynamically even without reload_env()
    md = _make_clean_m3_market_data(now_m3)
    snap = s2.analyze_market(md, now=now_m3)
    assert snap.power_v2.get("timeframe") == "M3"

    # Crowd v2 road-history window scales to hold >= (hist_min + 3) bars across M3 (2700s) and M5 (4500s)
    cfg_m3 = SimpleNamespace(TIMEFRAME="M3", BASE_TF_MINUTES=3, POWER_BAR_SECONDS=180, SHOOT_ROAD_HISTORY_MIN=12, SHOOT_ROAD_HISTORY_SECONDS=2700.0)
    cfg_m5 = SimpleNamespace(TIMEFRAME="M5", BASE_TF_MINUTES=5, POWER_BAR_SECONDS=300, SHOOT_ROAD_HISTORY_MIN=12, SHOOT_ROAD_HISTORY_SECONDS=2700.0)
    assert shooting_team._active_bar_seconds(cfg_m3) == 180.0
    assert shooting_team._active_bar_seconds(cfg_m5) == 300.0
    shooting_team._ROAD_HISTORY.clear()
    for i in range(12):
        # 12 M5 bar-close samples spaced 300s apart (oldest is 3300s > 2700s, <= 4500s!)
        shooting_team._ROAD_HISTORY.append((100_000.0 - (11 - i) * 300.0, 20.0))
    verdict_m5 = shooting_team._crowd_v2(door_size=25.0, road_lots=10.0, shot_lots=1.0, recent_volume=500.0, config=cfg_m5, now=100_300.0)
    assert verdict_m5["history_n"] >= 12
    assert verdict_m5["verdict"] == "PASS"


def test_stage3d_sniper_keeps_scout_geometry_even_when_env_aggressive_a2_enabled(monkeypatch, tmp_path):
    """When `.env` sets AGGRESSIVE_ENTRY_ENABLED=1, Stage-3d Sniper (POWER=NEITHER) must NOT
    get hijacked into a 1.80/1.80 A2_BURST_FIXED stop; also test raw_replay --start/--end/--out."""
    _configure_stage3d_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AGGRESSIVE_ENTRY_ENABLED", "1")
    monkeypatch.setattr(config, "AGGRESSIVE_ENTRY_ENABLED", True, raising=False)
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
    import power_v2_shadow
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
            "reason_code": "RANGE_NO_EDGE",
            "shadow_only": False,
            "context": {"market_regime": "RANGE", "feed_stale": False, "data_quality_ok": True},
            "regime_diagnostics": {"market_regime": "RANGE", "latest_tick_age_seconds": 1.0},
        },
    )
    snap = s2.analyze_market(md, now=now_dt)
    assert snap.shot["shot"] == "GO"
    assert snap.shot.get("a2_burst") is not True
    # Target is anchored to the 3995.0 Scout door (~3995.40), NOT 4000 - 1.80 = 3998.10!
    assert float(snap.shot["target"]) < 3996.0
    assert abs(float(snap.shot["stop"]) - float(snap.shot["entry"])) > 2.0

    # Also verify `--start`, `--end`, and `--out` flags in `tools/raw_replay.py`!
    from test_stage1_timeframe_whatif_and_replay import _write_synthetic_raw_day
    proj = tmp_path / "cli_proj"
    data_dir = proj / "data"
    _write_synthetic_raw_day(data_dir, "2026-10-29", start_px=4000.0, drift_per_min=0.30, minutes=140)
    out_custom = data_dir / "current_version_replay-9.txt"
    rc = rr.main([
        "--start", "2026-10-29",
        "--end", "2026-10-29",
        "--every", "180",
        "--root-dir", str(proj),
        "--data-dir", str(data_dir),
        "--out", str(out_custom),
        "--no-browser",
    ])
    assert rc == 0
    assert out_custom.exists()
    assert "[8b] SHOOTING CORRIDOR & SCOUT-BRACKET DIAGNOSTIC" in out_custom.read_text(encoding="utf-8")


def test_stage3e_passive_road_warmup_stays_warm_on_calm_ladder_without_whale_doors(monkeypatch, tmp_path):
    """Stage 3e: 12 calm live cycles with smooth ladder (0 whale outlier doors & <12 prints
    in M3 bar) still keep Crowd v2 Q2 road history warm (`12/12`) so cycle 13 fires GO!"""
    _configure_stage3d_env(monkeypatch, tmp_path)
    signal_team._BOOK = None
    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None
    shooting_team._ROAD_HISTORY.clear()

    import power_v2_shadow
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
            "reason_code": "BAD_DATA_QUALITY",
            "shadow_only": False,
            # Simulate calm 3m bar (<12 prints -> data_quality_ok=False in adapter)
            "context": {"market_regime": "RANGE", "feed_stale": False, "data_quality_ok": False},
            "regime_diagnostics": {"market_regime": "RANGE", "latest_tick_age_seconds": 1.0},
        },
    )

    # First 12 minutes: NEUTRAL panel + smooth 3-lot ladder (no outlier whale door!)
    monkeypatch.setattr(
        s2.SignalEngine,
        "aggregate",
        lambda self, *a, **k: (0.0, "NEUTRAL", 15.0, ["quiet tape"]),
    )
    base_dt = datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc)
    for m in range(12):
        t_dt = base_dt + timedelta(minutes=m)
        # Smooth ladder (door_size=3.0 == regular 3.0 lot levels -> 0 outlier whale doors!)
        md_smooth = _make_clean_m3_market_data(
            t_dt, drift=-0.10, price=4000.0, door_price=3995.0, door_side="bids", door_size=3.0
        )
        s2.analyze_market(md_smooth, now=t_dt)

    assert len(shooting_team._ROAD_HISTORY) >= 12

    # Next M3 bar close (`10:15`): Real 120-lot whale door appears + Stage-3 Sniper Panel fires SELL @ 74%!
    monkeypatch.setattr(s2.SignalEngine, "aggregate", _mock_sell_aggregate)
    t_fire = base_dt + timedelta(minutes=15)
    md_fire = _make_clean_m3_market_data(
        t_fire, drift=-0.40, price=4000.0, door_price=3995.0, door_side="bids", door_size=120.0
    )
    snap = s2.analyze_market(md_fire, now=t_fire)
    assert snap.shot["shot"] == "GO", f"Expected GO after 12-cycle calm ladder warm-up, got {snap.shot}"
