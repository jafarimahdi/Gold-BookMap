"""Stage P1 unit & regression tests (`2026-11-03`):
1) Eliminates the 1-bar (`180s` on `M3`) `POWER` & `classify_m5_regime` lag inside `analyze_market()`
   at `:00..:04` bells (`POWER_BELL_GRACE_SECONDS=0.0` default, synced with `A1` `_a1_last_closed_bucket`)
   while preserving `grace_seconds=5.0` default on standalone `last_completed_m5_end` / `run_power_v2`.
2) Verifies `[8f] POWER TEAM RAW-DATA SCORECARD` rendering in `tools/raw_replay.py`.
"""
from datetime import datetime, timedelta, timezone

import config
import step2_market_analysis as s2
from power_v2_shadow import last_completed_m5_end
from test_oct31_law8_raw_replay import _write_synthetic_raw_day
from tools import raw_replay as rr


def _build_trending_m3_market_data(bell_dt: datetime):
    start = bell_dt - timedelta(minutes=100)
    ticks = []
    opens, highs, lows, closes, vols = [], [], [], [], []
    # 100 M1 bars = >30 M3 bars so Wilder ADX(14) on M3 (>=28 bars) is warm!
    for m in range(100):
        bar_t = start + timedelta(minutes=m)
        base_px = 4000.0 + 0.25 * m
        opens.append(base_px)
        highs.append(base_px + 0.40)
        lows.append(base_px - 0.10)
        closes.append(base_px + 0.25)
        vols.append(30.0)
        # Put strong upward prints in the JUST-CLOSED [12:00..12:03] bar vs calm prior bars
        in_last_m3 = (bell_dt - timedelta(minutes=3)) <= bar_t < bell_dt
        n_prints = 8 if in_last_m3 else 3
        for k in range(n_prints):
            ts = bar_t + timedelta(seconds=5 + k * 6)
            ticks.append({
                "timestamp": ts.isoformat().replace("+00:00", "Z"),
                "price": round(base_px + 0.03 * k, 2),
                "volume": 6.0 if (in_last_m3 and k == 0) else 1.0,
                "side": "BUY" if in_last_m3 else ("BUY" if (k % 2 == 0) else "SELL"),
                "is_direct": True,
            })
    mid = closes[-1]
    return {
        "symbol": "GCZ6.COMEX@RITHMIC",
        "price": mid,
        "bid": round(mid - 0.10, 2),
        "ask": round(mid + 0.10, 2),
        "volume": 3000.0,
        "tick_size": 0.10,
        "candles": {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols},
        "tick_data": ticks,
        "order_events": [],
        "data_quality": {"source": "replay", "trade_prints": "available", "quote": "valid"},
    }


def test_stage_p1_analyze_market_syncs_power_bar_end_to_a1_closed_bell(monkeypatch):
    monkeypatch.setattr(config, "TIMEFRAME", "M3", raising=False)
    monkeypatch.setattr(config, "BASE_TF_MINUTES", 3, raising=False)
    monkeypatch.setattr(config, "A1_BASE_TF_MINUTES", 3, raising=False)
    monkeypatch.setattr(config, "POWER_BAR_SECONDS", 180, raising=False)
    monkeypatch.setattr(config, "POWER_BELL_GRACE_SECONDS", 0.0, raising=False)
    monkeypatch.setattr(config, "POWER_REGIME_BAR_CACHE_ENABLED", False, raising=False)
    s2._A1_BAR_SERVICE_CACHE.clear()

    # Exact :00 bell (12:03:00Z): A1 closes [12:00..12:03]; POWER & regime must evaluate 12:03:00Z (not 12:00:00Z!)
    bell = datetime(2026, 11, 3, 12, 3, 0, tzinfo=timezone.utc)
    md_bell = _build_trending_m3_market_data(bell)
    snap_bell = s2.analyze_market(md_bell, now=bell)
    assert snap_bell.power_v2.get("bar_end_utc") == "2026-11-03T12:03:00Z"
    assert (snap_bell.power_v2.get("regime_diagnostics") or {}).get("bar_end_utc") == "2026-11-03T12:03:00Z"
    assert snap_bell.power_v2.get("direction") == "UP"

    # Intra-bar :01 serve (12:04:00Z) preserves exact A1 cached verdict identity
    serve_dt = bell + timedelta(seconds=60)
    md_serve = dict(md_bell, price=md_bell["price"] + 0.40)
    snap_serve = s2.analyze_market(md_serve, now=serve_dt)
    assert "A1 bar-service:" in " ".join(snap_serve.notes or [])
    assert snap_serve.power_v2.get("bar_end_utc") == "2026-11-03T12:03:00Z"
    assert snap_serve.power_v2.get("direction") == snap_bell.power_v2.get("direction")

    # Standalone helper retains 5.0s default grace for backward compatibility
    assert last_completed_m5_end(bell, bar_seconds=180) == datetime(2026, 11, 3, 12, 0, 0, tzinfo=timezone.utc)
    assert last_completed_m5_end(bell + timedelta(seconds=6), bar_seconds=180) == bell


def test_stage_p1_raw_replay_renders_section_8f_power_scorecard(tmp_path, monkeypatch):
    monkeypatch.setenv("TIMEFRAME", "M3")
    monkeypatch.setenv("A1_BASE_TF_MINUTES", "3")
    monkeypatch.setattr(config, "TIMEFRAME", "M3", raising=False)
    monkeypatch.setattr(config, "BASE_TF_MINUTES", 3, raising=False)
    monkeypatch.setattr(config, "A1_BASE_TF_MINUTES", 3, raising=False)
    monkeypatch.setattr(config, "POWER_BAR_SECONDS", 180, raising=False)
    monkeypatch.setattr(config, "POWER_BELL_GRACE_SECONDS", 0.0, raising=False)
    monkeypatch.setattr(s2, "A1_BASE_TF_MINUTES", 3, raising=False)

    data_dir = tmp_path / "data"
    day_str = "2026-10-29"
    _write_synthetic_raw_day(data_dir, day_str, start_px=4000.0, drift_per_min=0.25, minutes=105)

    res = rr.replay_single_day(
        day_str=day_str,
        root_dir=tmp_path,
        data_dir=data_dir,
        every_s=60,
        window_str="10:00-12:00",
    )
    assert res["ok"] is True
    assert any(isinstance(c.get("power_diag"), dict) for c in res["cycles"])
    bell_lags = [
        c["power_diag"]["bar_lag_s"]
        for c in res["cycles"]
        if isinstance(c.get("power_diag"), dict)
        and not c["power_diag"].get("is_serve")
        and int(round(float(c.get("entry_ep") or 0.0))) % 180 == 0
        and c["power_diag"].get("bar_lag_s") is not None
    ]
    assert bell_lags and max(bell_lags) == 0.0

    txt, _html, _meta = rr.synthesize_multi_day([res])
    assert "[8f] POWER TEAM RAW-DATA SCORECARD (+12m = 4x M3 FORWARD TAPE" in txt
    assert "1) Stage-P1 Bell Sync & Census: Bell(:00) bar_lag avg=0.0s (max=0.0s)" in txt
    assert "2) Bell(:00) Directional Edge:" in txt
    assert "3) Heart x POWER" in txt
    assert "4) Step-P2 Sensor Health" in txt
