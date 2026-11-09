"""
Stage A2 (2026-11-06) Regression Suite — Section #8 (`A2` "The Now Button" Aggressive Impulse Trigger):
- Law #11 dynamic timeframe scaling: `A2_PANEL_WINDOW_SECONDS`, `A2_BURST_COOLDOWN_MINUTES`, and
  `A2_BURST_TIME_STOP_MINUTES` scale cleanly to `BASE_TF_MINUTES` (`180s / 3m / 12m` on M3;
  `300s / 5m / 20m` on M5; `900s / 15m / 60m` on M15).
- Law #5 shadow-first discipline: `AGGRESSIVE_ENTRY_ENABLED` and `A2_PROMOTE_LIVE_EXECUTION` both
  default to `False` (`=0`) so unverified bursts stay in non-executing shadow without phantom
  `data/a2_burst_count.json` increments.
- Qualified Eye-2 Panel Hygiene: single-family (`1/2`) or dead-zone signals (`29.5%` noise) never
  contaminate `EYES.panel` or `SHADOW_EYES.panel`; only multi-family `>= threshold` qualified
  signals are memorized.
- Intra-bar A1 serve (`_a1_try_serve` + `a2_serve_eval` + `extract_live_order_flow`):
  (a) extracts live intra-bar buying/selling pressure from `tick_data`,
  (b) requires `shot == "GO"` + `entry_style == "AGGRESSIVE_MARKET"` before committing fire,
  (c) clears one-shot `:00` `a2_burst` GO clones on `:01`/`:02` serves without mutating cached
      `entry['snapshot']` or `T3e` verdict fields (`signal_direction`, `confidence`, `regime`, `power`).
- `tools/raw_replay.py` renders `[8g] SECTION #8: A2 ('THE NOW BUTTON') AGGRESSIVE TRIGGER SCORECARD`.
"""
from datetime import datetime, timedelta, timezone
import os
from types import SimpleNamespace
from unittest.mock import patch

import aggressive_trigger as at
import config
import main
import shooting_team
import signal_team
import step2_market_analysis as s2
from tools import raw_replay as rr


def _reset_all_a2_state():
    import time as _t
    at.EYES.reset()
    at.SHADOW_EYES.reset()
    signal_team._BOOK = None
    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None
    shooting_team._ROAD_HISTORY.clear()
    now_ep = _t.time()
    for _ in range(12):
        shooting_team._ROAD_HISTORY.append((now_ep, 0.0))


def _seed_warm_road_for_test(now_dt: datetime, normal_lots: float = 120.0) -> None:
    shooting_team._ROAD_HISTORY.clear()
    now_ts = now_dt.timestamp()
    for k in range(15):
        shooting_team._ROAD_HISTORY.append((now_ts - (15 - k) * 10.0, normal_lots))


def _make_m3_md(now_dt: datetime, *, price: float = 4050.0, buy_heavy: bool = False):
    import numpy as np
    n_m1 = 180
    drift = 0.25 if buy_heavy else -0.25
    closes = np.array([price - drift * (n_m1 - 1 - i) for i in range(n_m1)], dtype=float)
    opens = closes - drift * 0.6
    highs = np.maximum(opens, closes) + 0.35
    lows = np.minimum(opens, closes) - 0.35
    vols = np.full(n_m1, 120.0, dtype=float)

    ticks = []
    for s_off in range(44, 0, -2):
        t_b = now_dt - timedelta(seconds=s_off)
        ticks.append({
            "timestamp": t_b.isoformat(),
            "ts": t_b,
            "price": price,
            "volume": 15.0,
            "size": 15.0,
            "side": "BUY" if buy_heavy else "SELL",
            "is_direct": True,
        })
    bid = round(price - 0.10, 2)
    ask = round(price + 0.10, 2)
    bids_l = [[round(price - 0.10 - 0.20 * j, 2), 3.0] for j in range(1, 10)]
    asks_l = [[round(price + 0.10 + 0.20 * j, 2), 3.0] for j in range(1, 10)]
    bids_l.append([round(price - 6.0, 2), 45.0])
    asks_l.append([round(price + 6.0, 2), 45.0])
    mbo_ev = [
        {
            "type": "ADD",
            "side": "BID" if buy_heavy else "ASK",
            "price": bid if buy_heavy else ask,
            "size": 12.0,
            "timestamp": (now_dt - timedelta(seconds=5)).isoformat(),
            "order_id": f"ord-{k}",
        }
        for k in range(8)
    ]
    return {
        "symbol": "GCZ6",
        "data_source": "replay",
        "price": price,
        "bid": bid,
        "ask": ask,
        "spread": 0.20,
        "volume": 3200.0,
        "has_data": True,
        "data_quality_ok": True,
        "last_data_age_seconds": 0.4,
        "last_trade_age_seconds": 0.4,
        "quote_age_seconds": 0.4,
        "depth_age_seconds": 0.4,
        "quote_timestamp": now_dt.isoformat(),
        "depth_timestamp": now_dt.isoformat(),
        "data_quality": {"quote": "valid", "level2": "available", "level3": "available"},
        "l2_diagnostics": {"tick_size": 0.1, "fresh_bid_levels": 10, "fresh_ask_levels": 10},
        "candles_is_m1": True,
        "candles": {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols},
        "bid_depth": {row[0]: row[1] for row in bids_l},
        "ask_depth": {row[0]: row[1] for row in asks_l},
        "order_book": {"bids": bids_l, "asks": asks_l},
        "tick_data": ticks,
        "order_events": mbo_ev,
        "macro": {},
        "news": {"headlines": [], "events": [], "fetch_calendar": False},
    }


def test_stage_a2_law11_timeframe_scaling_and_law5_defaults(monkeypatch):
    with monkeypatch.context() as mp:
        for k in (
            "TIMEFRAME",
            "A1_BASE_TF_MINUTES",
            "AGGRESSIVE_ENTRY_ENABLED",
            "A2_PROMOTE_LIVE_EXECUTION",
            "A2_PANEL_WINDOW_SECONDS",
            "A2_BURST_COOLDOWN_MINUTES",
            "A2_BURST_TIME_STOP_MINUTES",
        ):
            mp.delenv(k, raising=False)

        mp.setenv("TIMEFRAME", "M3")
        config._refresh()
        assert config.AGGRESSIVE_ENTRY_ENABLED is False
        assert config.A2_PROMOTE_LIVE_EXECUTION is False
        assert config.A2_PANEL_WINDOW_SECONDS == 180.0
        assert config.A2_BURST_COOLDOWN_MINUTES == 3.0
        assert config.A2_BURST_TIME_STOP_MINUTES == 12.0

        mp.setenv("TIMEFRAME", "M5")
        config._refresh()
        assert config.A2_PANEL_WINDOW_SECONDS == 300.0
        assert config.A2_BURST_COOLDOWN_MINUTES == 5.0
        assert config.A2_BURST_TIME_STOP_MINUTES == 20.0

        mp.setenv("TIMEFRAME", "M15")
        config._refresh()
        assert config.A2_PANEL_WINDOW_SECONDS == 900.0
        assert config.A2_BURST_COOLDOWN_MINUTES == 15.0
        assert config.A2_BURST_TIME_STOP_MINUTES == 60.0
    config._refresh()


def test_stage_a2_serve_eval_requires_go_and_live_order_flow_extraction():
    now_dt = datetime(2026, 11, 6, 14, 26, 0, tzinfo=timezone.utc)
    md = {
        "has_data": True,
        "data_quality_ok": True,
        "last_data_age_seconds": 0.5,
        "tick_data": [
            {"ts": now_dt - timedelta(seconds=10), "volume": 30.0, "side": "SELL"},
            {"ts": now_dt - timedelta(seconds=5), "volume": 10.0, "side": "BUY"},
        ],
    }
    live_flow = at.extract_live_order_flow(md, {"buying_pressure": 50.0, "selling_pressure": 50.0}, now_dt)
    assert live_flow["selling_pressure"] == 75.0
    assert live_flow["buying_pressure"] == 25.0

    # When plan_shot returns shot="WAIT" even with entry_style="AGGRESSIVE_MARKET", a2_serve_eval MUST NOT fire!
    served = SimpleNamespace(
        power_v2={"direction": "DOWN", "context": {"market_regime": "TREND"}},
        order_flow=live_flow,
        signal_direction="SELL",
        signal_map={},
        confidence=65.0,
        signal_strength=60.0,
        regime="TREND",
        ai_model="fixture",
        news=SimpleNamespace(news_state="QUIET"),
    )
    cfg = SimpleNamespace(AGGRESSIVE_ENTRY_ENABLED=1, EXECUTION_MODE="python")
    with patch.dict(os.environ, {"AGGRESSIVE_ENTRY_ENABLED": "1"}, clear=False), \
            patch("aggressive_trigger.a2_eval", return_value={"fire": True, "reason": "ok", "lean": {"direction": "DOWN", "strength": 0.5, "held_s": 65.0}, "panel": {"direction": "SELL", "age_s": 20.0}}), \
            patch("shooting_team.plan_shot", return_value={"shot": "WAIT", "entry_style": "AGGRESSIVE_MARKET", "why": ["door blocked"]}):
        out_shot, out_notes = at.a2_serve_eval(served, md, now_dt, 4050.0, 0.2, cfg, bid=4049.9, ask=4050.1, atr=2.5)
    assert out_shot is None
    assert any("plan_shot refused" in n for n in out_notes)


def test_stage_a2_eye2_hygiene_shadow_holdback_and_promoted_serve_clone_cleanup(tmp_path, monkeypatch):
    _reset_all_a2_state()
    try:
        monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setenv("DATA_SOURCE", "replay")
        monkeypatch.setenv("TIMEFRAME", "M3")
        monkeypatch.setenv("L3_WHALE_THRESHOLD", "10.0")
        monkeypatch.setenv("SCOUT_MIN_TARGET_USD", "0.0")
        monkeypatch.setenv("SHOOT_MIN_REWARD_USD", "0.0")
        monkeypatch.setenv("AGGRESSIVE_ENTRY_ENABLED", "1")
        monkeypatch.setenv("A2_LEAN_MIN", "0.15")
        monkeypatch.setenv("A2_LEAN_HOLD_SECONDS", "30")
        monkeypatch.setenv("A2_PANEL_WINDOW_SECONDS", "300")
        monkeypatch.delenv("A2_PROMOTE_LIVE_EXECUTION", raising=False)
        config._refresh()
        monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)

        # 1) Unqualified 1-family SELL signal (`29.5%` noise) MUST NOT enter EYES.panel!
        t_noise = datetime(2026, 11, 6, 14, 18, 5, tzinfo=timezone.utc)
        _seed_warm_road_for_test(t_noise)

        def _one_fam_sell(_self, *_a, **_kw):
            return (
                -25.0,
                "SELL",
                52.0,
                ["MTF M9:DOWN -> SELL w 0.65"],
            )

        monkeypatch.setattr(s2.SignalEngine, "aggregate", _one_fam_sell)
        monkeypatch.setattr("power_v2_shadow.run_power_v2", lambda *_a, **_kw: {
            "direction": "NEITHER",
            "reason_code": "WEAK_OR_NO_FORCE",
            "regime_gate": "PASS",
            "safety_gate": "PASS",
            "regime_diagnostics": {"market_regime": "TREND", "bars_used": 20},
            "context": {"market_regime": "TREND"},
        })
        snap_noise = s2.analyze_market(_make_m3_md(t_noise, price=4052.0, buy_heavy=False), now=t_noise)
        assert snap_noise.confidence < 35.0
        assert len(at.EYES.panel) == 0
        assert len(at.SHADOW_EYES.panel) == 0

        # 2) Qualified 2-family SELL signal (`14:21:05Z`, `POWER=NEITHER`) -> memorized in Eye 2!
        t_panel = datetime(2026, 11, 6, 14, 21, 5, tzinfo=timezone.utc)
        _seed_warm_road_for_test(t_panel)

        def _two_fam_sell(_self, *_a, **_kw):
            return (
                -55.0,
                "SELL",
                74.0,
                [
                    "MTF M9:DOWN M36:DOWN -> SELL w 0.50",
                    "L3 NET FLOW SELL -60.0 (buys 5.0 vs sells 65.0) w 1.00",
                ],
            )

        monkeypatch.setattr(s2.SignalEngine, "aggregate", _two_fam_sell)
        snap_panel = s2.analyze_market(_make_m3_md(t_panel, price=4051.0, buy_heavy=False), now=t_panel)
        assert snap_panel.confidence >= 35.0
        assert len(at.EYES.panel) == 1
        assert at.EYES.panel[-1][1] == "SELL"

        # 3) Next M3 bar (`14:24:05Z`): Panel goes NEUTRAL while POWER flips DOWN (`DELAYED_FOLLOW`).
        #    With default `A2_PROMOTE_LIVE_EXECUTION=False` (Law #5), shadow captures `DELAYED_FOLLOW`,
        #    `a2_burst_count` stays 0 (zero phantom burns!), and `snap.shot` stays WAIT.
        t_burst = datetime(2026, 11, 6, 14, 24, 5, tzinfo=timezone.utc)
        _seed_warm_road_for_test(t_burst)

        def _neutral_agg(_self, *_a, **_kw):
            return (0.0, "NEUTRAL", 17.0, [])

        monkeypatch.setattr(s2.SignalEngine, "aggregate", _neutral_agg)
        monkeypatch.setattr("power_v2_shadow.run_power_v2", lambda *_a, **_kw: {
            "direction": "DOWN",
            "decision": "DOWN",
            "up_power_pct": 25.0,
            "down_power_pct": 75.0,
            "power_total_pct": 100.0,
            "reason_code": "DOWNWARD_FORCE_DOMINATES",
            "reason": "DOWNWARD_FORCE_DOMINATES",
            "regime_gate": "PASS",
            "safety_gate": "PASS",
            "regime_diagnostics": {"market_regime": "TREND", "bars_used": 20, "latest_tick_age_seconds": 0.4},
            "context": {"market_regime": "TREND", "feed_stale": False, "data_quality_ok": True},
        })
        snap_hold = s2.analyze_market(_make_m3_md(t_burst, price=4049.0, buy_heavy=False), now=t_burst)
        a2_sh = (snap_hold.shadow_candidates or {}).get("a2_shadow") or {}
        assert a2_sh.get("subtype") == "DELAYED_FOLLOW"
        assert a2_sh.get("active_scout_go") is True
        assert snap_hold.shot.get("shot") == "WAIT"
        assert at.a2_fire_count(t_burst, config=config) == 0

        # 4) Now enable `A2_PROMOTE_LIVE_EXECUTION=1` on the next bar (`14:27:05Z`) while Eye-2 is still
        #    inside the 300s window -> `:00` fires GO (count=1), main._shooting_execution_gate approves,
        #    and `:01` (`14:28:05Z` A1 serve) clears the one-shot GO clone without mutating cached snapshot!
        monkeypatch.setattr(config, "A2_PROMOTE_LIVE_EXECUTION", True, raising=False)
        monkeypatch.setattr(config, "EXECUTION_MODE", "python", raising=False)
        t_prom = datetime(2026, 11, 6, 14, 25, 5, tzinfo=timezone.utc)  # still in 14:24..14:27 bucket? Force new bucket 14:27:05 with panel refreshed at 14:24:30
        at.EYES.remember_panel(datetime(2026, 11, 6, 14, 25, 30, tzinfo=timezone.utc), "SELL")
        t_bell2 = datetime(2026, 11, 6, 14, 27, 5, tzinfo=timezone.utc)
        _seed_warm_road_for_test(t_bell2)
        snap_go = s2.analyze_market(_make_m3_md(t_bell2, price=4048.5, buy_heavy=False), now=t_bell2)
        assert snap_go.shot.get("shot") == "GO"
        assert snap_go.shot.get("a2_burst") is True
        assert at.a2_fire_count(t_bell2, config=config) == 1

        gate_ok, gate_msg = main._shooting_execution_gate(
            SimpleNamespace(action="SELL", confidence=80.0),
            snap_go,
        )
        assert gate_ok is True, gate_msg

        # Intra-bar A1 serve 60s later (`14:28:05Z`, same M3 bucket): cooldown is active -> must NOT clone stale GO!
        t_serve = datetime(2026, 11, 6, 14, 28, 5, tzinfo=timezone.utc)
        snap_srv = s2.analyze_market(_make_m3_md(t_serve, price=4047.8, buy_heavy=False), now=t_serve)
        assert any("A1 bar-service:" in n for n in (snap_srv.notes or []))
        assert snap_srv.shot.get("shot") == "WAIT"
        assert at.a2_fire_count(t_serve, config=config) == 1
        # Cached bar-close snapshot in _A1_BAR_SERVICE_CACHE remains untouched (T3g invariant)
        assert s2._A1_BAR_SERVICE_CACHE["entry"]["snapshot"].shot.get("shot") == "GO"
    finally:
        _reset_all_a2_state()


def test_stage_a2_raw_replay_renders_8g_scorecard(tmp_path, monkeypatch):
    _reset_all_a2_state()
    try:
        monkeypatch.setattr(config, "reload_env", lambda *a, **k: None)
        monkeypatch.setattr(config, "TIMEFRAME", "M3", raising=False)
        monkeypatch.setenv("TIMEFRAME", "M3")
        monkeypatch.setenv("CONFIDENCE_THRESHOLD", "35")

        from test_stage1_timeframe_whatif_and_replay import _write_synthetic_raw_day
        proj = tmp_path / "proj_a2"
        data_dir = proj / "data"
        _write_synthetic_raw_day(data_dir, "2026-10-29", start_px=4000.0, drift_per_min=0.30, minutes=140)

        real_analyze = s2.analyze_market
        call_idx = {"n": 0}

        def _patched_analyze(md, now=None):
            snap = real_analyze(md, now=now)
            call_idx["n"] += 1
            if call_idx["n"] == 12:
                sh = dict(getattr(snap, "shadow_candidates", None) or {})
                sh["a2_shadow"] = {
                    "power_dir": "UP",
                    "side": "BUY",
                    "is_serve": False,
                    "env_ok": True,
                    "env_block_reason": "",
                    "v1_confirmed": True,
                    "active_lean_ok": True,
                    "strict_lean_ok": True,
                    "active_panel_ok": True,
                    "strict_panel_ok": True,
                    "panel_qualified_in_window": True,
                    "subtype": "DELAYED_FOLLOW",
                    "scout_go": True,
                    "active_scout_go": True,
                    "strict_scout_go": True,
                }
                snap.shadow_candidates = sh
            return snap

        monkeypatch.setattr(s2, "analyze_market", _patched_analyze)
        res = rr.replay_single_day("2026-10-29", root_dir=proj, data_dir=data_dir, every_s=180)
        txt, _, _ = rr.synthesize_multi_day([res])
        assert "[8g] SECTION #8: A2 ('THE NOW BUTTON') AGGRESSIVE TRIGGER SCORECARD" in txt
        assert "Active .env 3-Eye + Scout GO" in txt
        assert "Canonical Strict (0.25/60s/1b) GO" in txt
        assert "-> Subtype: DELAYED_FOLLOW" in txt
    finally:
        _reset_all_a2_state()


def test_stage_a2_intrabar_serve_triggers_now_button_and_preserves_t3e_t3g(tmp_path, monkeypatch):
    _reset_all_a2_state()
    try:
        monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setenv("DATA_SOURCE", "replay")
        monkeypatch.setenv("TIMEFRAME", "M3")
        monkeypatch.setenv("L3_WHALE_THRESHOLD", "10.0")
        monkeypatch.setenv("SCOUT_MIN_TARGET_USD", "0.0")
        monkeypatch.setenv("SHOOT_MIN_REWARD_USD", "0.0")
        monkeypatch.setenv("AGGRESSIVE_ENTRY_ENABLED", "1")
        monkeypatch.setenv("A2_PROMOTE_LIVE_EXECUTION", "1")
        monkeypatch.setenv("A2_LEAN_MIN", "0.25")
        monkeypatch.setenv("A2_LEAN_HOLD_SECONDS", "45")
        monkeypatch.setenv("A2_PANEL_WINDOW_SECONDS", "180")
        config._refresh()
        monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(config, "EXECUTION_MODE", "python", raising=False)

        # Pre-seed qualified Eye-2 panel memory 30s before the 15:00 M3 bell, but NO prior Eye-1 samples yet
        t_bell = datetime(2026, 11, 6, 15, 0, 5, tzinfo=timezone.utc)
        at.EYES.remember_panel(t_bell - timedelta(seconds=30), "SELL")
        at.SHADOW_EYES.remember_panel(t_bell - timedelta(seconds=30), "SELL")
        _seed_warm_road_for_test(t_bell)

        monkeypatch.setattr(s2.SignalEngine, "aggregate", lambda _s, *_a, **_kw: (0.0, "NEUTRAL", 18.0, []))
        monkeypatch.setattr("power_v2_shadow.run_power_v2", lambda *_a, **_kw: {
            "direction": "DOWN",
            "decision": "DOWN",
            "up_power_pct": 22.0,
            "down_power_pct": 78.0,
            "power_total_pct": 100.0,
            "reason_code": "DOWNWARD_FORCE_DOMINATES",
            "reason": "DOWNWARD_FORCE_DOMINATES",
            "regime_gate": "PASS",
            "safety_gate": "PASS",
            "regime_diagnostics": {"market_regime": "TREND", "bars_used": 20, "latest_tick_age_seconds": 0.4},
            "context": {"market_regime": "TREND", "feed_stale": False, "data_quality_ok": True},
        })

        # At 15:00:05Z (:00 bell): Eye-1 only has 1 sample (0s < 45s hold) -> WAIT!
        snap_bell = s2.analyze_market(_make_m3_md(t_bell, price=4045.0, buy_heavy=False), now=t_bell)
        assert snap_bell.shot.get("shot") == "WAIT"
        assert at.a2_fire_count(t_bell, config=config) == 0

        # At 15:01:05Z (:01 A1 serve, 60s >= 45s hold): live intra-bar ticks complete Eye-1 hold -> GO!
        t_srv = datetime(2026, 11, 6, 15, 1, 5, tzinfo=timezone.utc)
        _seed_warm_road_for_test(t_srv)
        snap_srv = s2.analyze_market(_make_m3_md(t_srv, price=4044.2, buy_heavy=False), now=t_srv)
        assert any("A1 bar-service:" in n for n in (snap_srv.notes or []))
        assert snap_srv.shot.get("shot") == "GO"
        assert snap_srv.shot.get("entry_style") == "AGGRESSIVE_MARKET"
        assert at.a2_fire_count(t_srv, config=config) == 1
        assert snap_srv.shadow_candidates["a2_shadow"]["subtype"] == "SERVE_INTRABAR"

        # T3e verdict fields & T3g cached snapshot remain 100% untouched!
        assert snap_srv.signal_direction == snap_bell.signal_direction
        assert snap_srv.confidence == snap_bell.confidence
        assert snap_srv.regime == snap_bell.regime
        assert (snap_srv.power or {}).get("direction") == (snap_bell.power or {}).get("direction")
        assert s2._A1_BAR_SERVICE_CACHE["entry"]["snapshot"].shot.get("shot") == "WAIT"
    finally:
        _reset_all_a2_state()
