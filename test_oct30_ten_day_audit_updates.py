"""Comprehensive tests for the 2026-10-30 10-Day Audit Master Update List (U-01..U-18)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np

import aggressive_trigger
import config
import judge_families
import power_m5_adapter
import power_v2_regime
import power_v2_shadow
import session_levels
import shooting_team
import signal_team
import step2_market_analysis as s2
from tools import xray_report


def _mk_of(**kwargs) -> s2.OrderFlowMetrics:
    defaults = dict(
        cvd=0.0, delta=0.0, buying_pressure=50.0, selling_pressure=50.0,
        level2_bid_depth=100.0, level2_ask_depth=100.0, bid_ask_ratio=1.0,
        large_orders=0,
    )
    defaults.update(kwargs)
    return s2.OrderFlowMetrics(**defaults)


def _valid_power(direction: str = "UP", **extra) -> dict:
    is_up = direction == "UP"
    reason = "UPWARD_FORCE_DOMINATES" if is_up else "DOWNWARD_FORCE_DOMINATES"
    out = {
        "team": "POWER",
        "version": "2.2.0",
        "shadow_only": False,
        "direction": direction,
        "reason_code": reason,
        "up_power_pct": 78.0 if is_up else 22.0,
        "down_power_pct": 22.0 if is_up else 78.0,
        "power_total_pct": 100.0,
        "activity": 0.42,
        "coverage": 0.78,
        "context": {
            "market_regime": "TREND",
            "feed_stale": False,
            "high_impact_news": False,
            "data_quality_ok": True,
        },
        "regime_diagnostics": {
            "market_regime": "TREND",
            "reason": "CONFIRMED",
            "latest_tick_age_seconds": 1.0,
        },
        "decision_gate_diagnostics": {
            "actionable_direction_allowed": True,
            "active_hard_blockers": [],
            "reason_code": reason,
        },
    }
    out.update(extra)
    return out


def test_u01_retired_judges_union_and_notes_preserved(monkeypatch):
    """U-01: 14 canonical retired judges union with .env RETIRED_JUDGES, cast 0 votes, keep notes."""
    expected_new = {"poc_day", "supply_demand", "microprice", "queue_pos"}
    assert expected_new.issubset(judge_families.RETIRED)
    assert len(judge_families.RETIRED) == 14
    assert set(judge_families.RETIRED).issubset(config.RETIRED_JUDGES)

    with monkeypatch.context() as mp:
        mp.setenv("RETIRED_JUDGES", "whale_walls,htf_poc")
        mp.delenv("UNRETIRE_JUDGES", raising=False)
        config._refresh()
        assert expected_new.issubset(config.RETIRED_JUDGES)
        assert len(config.RETIRED_JUDGES) >= 14

        mp.setenv("UNRETIRE_JUDGES", "microprice")
        config._refresh()
        assert "microprice" not in config.RETIRED_JUDGES
        assert {"poc_day", "supply_demand", "queue_pos"}.issubset(config.RETIRED_JUDGES)
    config._refresh()

    eng = s2.SignalEngine()
    vol = s2.VolatilityMetrics(atr=2.0, volatility_rank=0.4)
    tr = s2.TrendMetrics()
    of = _mk_of(microprice=4200.5, mid_price=4200.0, bid_ask_ratio=1.5)
    fp = s2.FootprintMetrics()
    # Balanced top-of-book (10 vs 10) keeps active l3_imbalance silent under both
    # V6_4TEAMS_ENABLED=false (.env) and true, while iceberg count=2 wakes queue_pos note.
    l3 = s2.Level3Events(order_book={"bids": [(4199.9, 10.0)], "asks": [(4200.1, 10.0)]},
                         iceberg_levels={4199.5: 2})
    vp = s2.VolumeProfileMetrics(poc=4195.0, vwap=4199.5)
    mac = s2.MacroMetrics()
    nw = s2.NewsAndEvents()
    closes = np.array([4199.6, 4199.8, 4200.0])

    # In RANGE: retired judges + vwap_trend cast 0 votes, so confidence == 0.0, while notes still emit
    _, dir_r, conf_r, notes_r = eng.aggregate(
        4200.0, vol, tr, of, fp, l3, vp, mac, nw,
        regime="RANGE", nearest_support=4199.0, recent_closes=closes,
    )
    assert conf_r == 0.0
    assert dir_r == "NEUTRAL"
    panel_r = {row["judge"] for row in s2.parse_judge_panel(notes_r)}
    assert {"poc_day", "supply_demand", "microprice", "queue_pos"}.issubset(panel_r)
    assert "vwap_trend" not in panel_r

    # In TREND: vwap_trend IS permitted to vote
    _, _, _, notes_t = eng.aggregate(
        4200.0, vol, tr, of, fp, l3, vp, mac, nw,
        regime="TREND", nearest_support=4199.0, recent_closes=closes,
    )
    panel_t = {row["judge"] for row in s2.parse_judge_panel(notes_t)}
    assert "vwap_trend" in panel_t


def test_u02_family_gate_shadow_default_and_v6_4teams_domain_partition(monkeypatch):
    """U-02 + U-04: FAMILY_GATE is shadow-only by default; classical & V6_4TEAMS modes both work cleanly."""
    assert config.FAMILY_GATE_SHADOW_ENABLED is True
    assert config.FAMILY_GATE_LIVE_ENABLED is False
    assert config.LEGACY_GHOST_VOTES_ENABLED is False

    eng = s2.SignalEngine()
    vol = s2.VolatilityMetrics(atr=2.0, volatility_rank=0.3)
    tr = s2.TrendMetrics()
    of = _mk_of(delta=25.0, cvd=120.0, buying_pressure=66.0, selling_pressure=34.0)
    fp = s2.FootprintMetrics(delta_imbalance=0.35, dominant_level=4200.0, footprint_strength=0.8)
    l3 = s2.Level3Events(
        order_book={"bids": [(4199.9, 45.0)], "asks": [(4200.1, 10.0)]},
        order_book_imbalance=0.55,
        aggressive_buy_volume=120.0,
        aggressive_sell_volume=20.0,
    )
    vp = s2.VolumeProfileMetrics(vwap=4206.0, value_area_high=4208.0, value_area_low=4201.0)
    mac = s2.MacroMetrics()
    nw = s2.NewsAndEvents()

    # 1) Classical weighted mode (owner Windows .env V6_4TEAMS_ENABLED=false)
    with monkeypatch.context() as mp:
        mp.setattr(config, "V6_4TEAMS_ENABLED", False)
        _, dir_cls, conf_cls, _ = eng.aggregate(
            4200.0, vol, tr, of, fp, l3, vp, mac, nw, regime="RANGE",
        )
        assert dir_cls == "BUY"
        assert conf_cls >= 55.0

    # 2) V6 4-Teams mode: fewer than 15 votes total, yet flow + whale + structure agree -> BUY
    with monkeypatch.context() as mp:
        mp.setattr(config, "V6_4TEAMS_ENABLED", True)
        _, direction, conf, notes = eng.aggregate(
            4200.0, vol, tr, of, fp, l3, vp, mac, nw, regime="RANGE",
        )
        assert direction == "BUY"
        assert conf >= 55.0
        assert any("v6.0 Confluence BUY:" in n for n in notes)

        # Opposing World (high-impact bearish news <=120m) triggers real v6.0 World veto -> NEUTRAL!
        nw_opp = s2.NewsAndEvents(sentiment_score=-0.95, impact_level="HIGH", minutes_to_next_event=15.0)
        _, dir_veto, _, notes_veto = eng.aggregate(
            4200.0, vol, tr, of, fp, l3, vp, mac, nw_opp, regime="RANGE",
        )
        assert any("v6.0 World veto:" in n for n in notes_veto)
        assert dir_veto == "NEUTRAL"

    # Direct family_gate check on single-family vs 2-family panels
    fg_1 = judge_families.family_gate(
        [{"judge": "footprint_delta", "dir": 1.0, "weight": 1.0},
         {"judge": "cvd_momentum", "dir": 1.0, "weight": 0.5}],
        "BUY", min_families=2,
    )
    assert fg_1["ok"] is False and fg_1["agree_families"] == ["TAPE"]
    fg_2 = judge_families.family_gate(
        [{"judge": "footprint_delta", "dir": 1.0, "weight": 1.0},
         {"judge": "vwap_bands", "direction": "BUY", "weight": 0.8}],
        "BUY", min_families=2,
    )
    assert fg_2["ok"] is True and fg_2["agree_count"] == 2


def test_u03_power_voice_gain_diagnostics_and_l3_aggr_limit_not_emitted():
    """U-03: l3_aggr_limit remains NOT_EMITTED while saturation diagnostics are surfaced."""
    now = datetime(2026, 10, 29, 14, 0, 10, tzinfo=timezone.utc)
    ticks = []
    for i in range(45):
        ts = now - timedelta(seconds=20 + i * 3)
        ticks.append({
            "timestamp": ts.isoformat().replace("+00:00", "Z"),
            "price": 4200.0 + i * 0.05,
            "volume": 8.0 if i % 5 == 0 else 1.0,
            "side": "BUY",
            "is_direct": True,
        })
    out = power_v2_shadow.run_power_v2(
        ticks, now=now, symbol="GCZ6", tick_size=0.10,
        market_regime="TREND", bar_seconds=180,
    )
    assert out["judge_availability"]["l3_aggr_limit"]["status"] == "NOT_EMITTED"
    assert out.get("voice_gain") == 3.0
    ad = out.get("adapter_diagnostics") or {}
    assert ad.get("voice_gain_saturated_count", 0) >= 1
    assert "footprint_delta" in (ad.get("pre_voice_gain_values") or {})


def test_u04_ghost_votes_off_and_l3_ofi_streak_retired():
    """U-04: 11 unnamed Ghost Votes stay off by default and l3_ofi_streak honors retirement."""
    eng = s2.SignalEngine()
    vol = s2.VolatilityMetrics(atr=2.0)
    tr = s2.TrendMetrics(trend_direction="UP", trend_strength=80.0, macd_histogram=0.5, sma_20=4190.0)
    of = _mk_of(
        buying_pressure=58.0, selling_pressure=42.0, cvd=500.0,
        bid_ask_ratio=1.4, ofi=80.0, level2_bid_depth=100.0, level2_ask_depth=60.0,
        depth_imbalance=0.25,
    )
    fp = s2.FootprintMetrics()
    l3 = s2.Level3Events(buy_streak=5, ofi=60.0, aggressive_buy_volume=40.0,
                         aggressive_sell_volume=10.0, aggressive_flow_ratio=0.8)
    vp = s2.VolumeProfileMetrics(vwap=4199.8)
    mac = s2.MacroMetrics()
    nw = s2.NewsAndEvents()

    strength, direction, conf, _ = eng.aggregate(
        4200.0, vol, tr, of, fp, l3, vp, mac, nw, regime="RANGE",
    )
    assert direction == "NEUTRAL"
    assert strength == 0.0
    assert conf == 0.0


def test_u05_absorption_sign_fix_near_touch_and_rolling_decay():
    """U-05: Passive bid wall holding near touch -> +absorption_net (BUY), far cancel ignored, rolling decay."""
    l2 = s2.OrderBookDepthAnalyzer(wall_size=25.0)
    t0 = 1_760_000_000.0

    bids1 = [(4199.90, 40.0), (4199.80, 10.0), (4192.00, 50.0)]
    asks1 = [(4200.10, 12.0), (4200.20, 10.0)]
    l2.update(bids1, asks1, now_ts=t0)

    # Far bid at 4192.00 shrinks/cancels $7.90 away -> NOT near-touch absorption!
    bids2 = [(4199.90, 40.0), (4199.80, 10.0), (4192.00, 5.0)]
    l2.update(bids2, asks1, now_ts=t0 + 5.0)
    assert l2.summary(bids2, asks1, now_ts=t0 + 5.0)["absorption_net"] == 0

    # Near-touch resting bid wall at 4199.90 absorbs 18 lots (40 -> 22) while best bid holds at 4199.90!
    bids3 = [(4199.90, 22.0), (4199.80, 10.0), (4192.00, 5.0)]
    l2.update(bids3, asks1, now_ts=t0 + 10.0)
    summ = l2.summary(bids3, asks1, now_ts=t0 + 10.0)
    assert summ["bid_absorbed"] >= 1
    assert summ["absorption_net"] > 0  # Bullish passive bid absorption!

    # Beyond ORDER_FLOW_WINDOW_SECONDS (300s), old absorption event expires cleanly
    summ_expired = l2.summary(bids3, asks1, now_ts=t0 + 400.0)
    assert summ_expired["absorption_events"] == 0
    assert summ_expired["absorption_net"] == 0


def test_u06_l3_near_touch_ofi_ignores_deep_book_spoofing():
    """U-06: Far-book 30-lot orders $15 away do not move near_touch_ofi or near_large_order_events."""
    l3a = s2.Level3OrderBookAnalyzer(large_size=20.0)
    now_ts = 1_760_000_100.0
    l3a.update_order_book([(4199.9, 10.0)], [(4200.1, 10.0)])
    for i in range(6):
        l3a.process_order_event({
            "type": "ASK_NEW", "side": "ASK", "price": 4215.0 + i * 0.5,
            "size": 30.0, "order_id": f"far_{i}", "timestamp": now_ts,
        })
    res_far = l3a.analyze()
    assert res_far.large_order_events >= 6
    assert res_far.near_large_order_events == 0
    assert res_far.near_touch_ofi == 0.0

    l3b = s2.Level3OrderBookAnalyzer(large_size=20.0)
    l3b.update_order_book([(4199.9, 10.0)], [(4200.1, 10.0)])
    for i in range(4):
        l3b.process_order_event({
            "type": "BID_NEW", "side": "BID", "price": 4199.8 - i * 0.1,
            "size": 25.0, "order_id": f"near_{i}", "timestamp": now_ts,
        })
    res_near = l3b.analyze()
    assert res_near.near_large_order_events == 4
    assert res_near.near_touch_ofi >= 70.0


def test_u07_liquidity_sweep_fires_on_real_gold_high_low_volume():
    """U-07: _detect_liquidity_sweep detects $4,200 Gold stop-hunt wick + snapback on real H/L/V."""
    n = 25
    highs = np.full(n, 4201.0)
    lows = np.full(n, 4199.0)
    closes = np.full(n, 4200.0)
    vols = np.full(n, 100.0)
    highs[-1] = 4202.10
    lows[-1] = 4200.10
    closes[-1] = 4200.40
    vols[-1] = 240.0
    sw = s2._detect_liquidity_sweep(highs, lows, closes, vols, lookback=20, threshold_pct=0.03, vol_roc_thr=1.8)
    assert sw == 1


def test_u08_gold_news_lexicon_and_high_window_vote_gate():
    """U-08: Safe-haven crisis headlines score bullish for Gold; hawkish hikes score bearish; non-HIGH silent."""
    na = s2.NewsAnalyzer()
    score_safe, _ = na.analyze_headlines(["Safe-haven gold rises on war escalation and geopolitical crisis"])
    assert score_safe > 0.0

    score_hawk, _ = na.analyze_headlines(["Fed signals hawkish rate hike as yields surge"])
    assert score_hawk < 0.0

    nw_far = s2.NewsAndEvents(sentiment_score=score_safe, impact_level="HIGH", minutes_to_next_event=300.0)
    eng = s2.SignalEngine()
    _, d_off, c_off, _ = eng.aggregate(
        4200.0, s2.VolatilityMetrics(atr=2.0), s2.TrendMetrics(),
        _mk_of(), s2.FootprintMetrics(), s2.Level3Events(),
        s2.VolumeProfileMetrics(), s2.MacroMetrics(), nw_far, regime="RANGE",
    )
    assert d_off == "NEUTRAL" and c_off == 0.0


def test_u09_a2_neutral_panel_flag_and_front_door_room_guard():
    """U-09: A2_ALLOW_NEUTRAL_PANEL defaults False (=0) and plan_shot blocks bursts into close walls."""
    assert config.A2_ALLOW_NEUTRAL_PANEL is False
    assert config.A2_MIN_CLEAR_WALL_PTS == 1.50

    smap_wall = {
        "above": [{"price": 4200.60, "size": 28.0, "kind": "whale", "verdict": "solid", "burn_word": "holding",
                   "road_clear": True, "doors_between": 3, "lots_between": 15.0, "size_now": 28.0}],
        "below": [{"price": 4198.50, "size": 28.0, "kind": "whale", "verdict": "solid", "spoofed": False, "size_now": 28.0}],
        "vacuum": {"state": "open"},
    }
    now_ts = time.time()
    shooting_team._ROAD_HISTORY.clear()
    for k in range(15):
        shooting_team._ROAD_HISTORY.append((now_ts - (15 - k) * 10.0, 20.0))

    pw = _valid_power("UP")
    ctx = {
        "bid": 4199.9, "ask": 4200.1, "now": now_ts, "market_symbol": "GCZ6",
        "news_state": "QUIET", "data_quality_ok": True, "last_data_age_seconds": 1.0,
        "has_data": True, "trade_lots": 0.5, "recent_volume": 250.0,
        "aggressive_trigger_confirmed": True, "aggressive_trigger_reason": "A2 burst",
        "a2_burst": True,
    }
    cfg = SimpleNamespace(A2_MIN_CLEAR_WALL_PTS=1.50, A2_BURST_SL_PTS=1.8, A2_BURST_TP_PTS=1.8,
                          SHOOT_ROAD_HISTORY_MIN=5)
    shot_blocked = shooting_team.plan_shot(smap_wall, pw, 4200.0, 2.0, 0.20, cfg, entry_context=ctx)
    assert shot_blocked["shot"] == "WAIT"
    assert "solid front door" in " ".join(shot_blocked["why"])


def test_u10_iceberg_proximity_and_cvd_momentum_noise_gate():
    """U-10: Far-away stale iceberg (>2.5 ATR & >0.3%) is ignored; +2 lot delta noise does not vote."""
    eng = s2.SignalEngine()
    vol = s2.VolatilityMetrics(atr=2.0)
    tr = s2.TrendMetrics()
    of_noise = _mk_of(delta=2.0, cvd=400.0, buying_pressure=51.0, selling_pressure=49.0)
    fp = s2.FootprintMetrics()
    l3_far_ib = s2.Level3Events(
        iceberg_events=2,
        iceberg_levels={4150.0: 6},
        iceberg_meta={4150.0: {"score": 60000, "age_sec": 3600, "refills": 6}},
    )
    _, d, c, notes = eng.aggregate(
        4200.0, vol, tr, of_noise, fp, l3_far_ib,
        s2.VolumeProfileMetrics(), s2.MacroMetrics(), s2.NewsAndEvents(), regime="RANGE",
    )
    assert d == "NEUTRAL" and c == 0.0
    assert not any("ICEBERG_SUPPORT" in n for n in notes)


def test_u11_variance_ratio_diagnostic_and_opt_in_chop_guard():
    """U-11: classify_m5_regime computes variance_ratio_4 (<0.80 on ping-pong chop) with live default=0."""
    assert config.POWER_VR_GUARD_ENABLED is False
    now = datetime(2026, 10, 29, 15, 0, 10, tzinfo=timezone.utc)
    ticks_chop = []
    for bar in range(30):
        c = 4200.0 + (1.5 if bar % 2 == 0 else -1.5)
        for k in range(6):
            ts = datetime(2026, 10, 29, 15, 0, 0, tzinfo=timezone.utc) - timedelta(seconds=(30 - bar) * 300 - k * 30)
            ticks_chop.append({
                "timestamp": ts.isoformat().replace("+00:00", "Z"),
                "price": c + (k - 3) * 0.05,
                "volume": 2.0, "side": "BUY", "is_direct": True,
            })
    ticks_chop.append({
        "timestamp": "2026-10-29T15:00:05Z",
        "price": 4200.0, "volume": 2.0, "side": "BUY", "is_direct": True,
    })
    res = power_v2_regime.classify_m5_regime(ticks_chop, now=now)
    assert res["variance_ratio_4"] is not None
    assert res["variance_ratio_4"] < 0.60


def test_u12_power_streak_and_climax_opt_in_guards():
    """U-12: plan_shot exposes fresh_wave_shadow and honors opt-in streak & climax gates."""
    smap = {
        "above": [{"price": 4204.0, "size": 30.0, "kind": "whale", "verdict": "solid", "burn_word": "holding",
                   "road_clear": True, "doors_between": 5, "lots_between": 20.0, "size_now": 30.0}],
        "below": [{"price": 4198.5, "size": 28.0, "kind": "whale", "verdict": "solid", "spoofed": False,
                   "size_now": 28.0}],
        "vacuum": {"state": "open"},
    }
    now_ts = time.time()
    shooting_team._ROAD_HISTORY.clear()
    for k in range(15):
        shooting_team._ROAD_HISTORY.append((now_ts - (15 - k) * 10.0, 22.0))

    pw = _valid_power("UP", power_streak_cycles=8)
    ctx = {
        "bid": 4199.9, "ask": 4200.1, "now": now_ts, "market_symbol": "GCZ6",
        "news_state": "QUIET", "data_quality_ok": True, "last_data_age_seconds": 1.0,
        "has_data": True, "trade_lots": 0.5, "recent_volume": 250.0,
        "signal_direction": "BUY", "signal_confidence": 65.0,
        "aggressive_trigger_confirmed": True, "aggressive_trigger_reason": "A2 burst",
        "a2_burst": True,
    }
    cfg_default = SimpleNamespace(
        SHOOT_MAX_POWER_STREAK_CYCLES=0,
        SHOOT_BLOCK_CLIMAX_AGREEMENT=False,
        SHOOT_EFFECTIVE_CFD_SPREAD_PTS=0.35,
        SHOOT_ROAD_HISTORY_MIN=5,
    )
    shot_def = shooting_team.plan_shot(smap, pw, 4200.0, 2.0, 0.20, cfg_default, entry_context=ctx)
    assert shot_def["shot"] == "GO"
    fws = shot_def["shadow_candidates"]["fresh_wave_shadow"]
    assert fws["would_block_stale_streak"] is True
    assert fws["would_block_climax_agreement"] is True
    assert fws["fresh_non_climax_candidate"] is False

    cfg_guarded = SimpleNamespace(
        SHOOT_MAX_POWER_STREAK_CYCLES=5,
        SHOOT_BLOCK_CLIMAX_AGREEMENT=True,
        SHOOT_EFFECTIVE_CFD_SPREAD_PTS=0.35,
        SHOOT_ROAD_HISTORY_MIN=5,
    )
    shot_blk = shooting_team.plan_shot(smap, pw, 4200.0, 2.0, 0.20, cfg_guarded, entry_context=ctx)
    assert shot_blk["shot"] == "WAIT"
    assert any("streak age" in w or "climax" in w for w in shot_blk["why"])


def test_u13_power_adapter_5lot_gc_filter_and_sqrt_volume_breadth():
    """U-13: 2-lot prints on GC do not count as big_prints; sqrt(volume) weights active levels."""
    now = datetime(2026, 10, 29, 14, 0, 10, tzinfo=timezone.utc)
    ticks_small = [
        {"timestamp": (now - timedelta(seconds=15 + i * 3)).isoformat().replace("+00:00", "Z"),
         "price": 4200.0 + (i % 4) * 0.10, "volume": 2.0 if i == 0 else 1.0,
         "side": "BUY", "is_direct": True}
        for i in range(25)
    ]
    out_gc = power_v2_shadow.run_power_v2(
        ticks_small, now=now, symbol="GCZ6", tick_size=0.10,
        market_regime="TREND", bar_seconds=180,
    )
    assert "big_prints" in out_gc["excluded_judges"]

    ticks_breadth = []
    for i in range(18):
        ticks_breadth.append({
            "timestamp": (now - timedelta(seconds=10 + i * 2)).isoformat().replace("+00:00", "Z"),
            "price": 4200.00, "volume": 2.0, "side": "BUY", "is_direct": True,
        })
    for i in range(2):
        ticks_breadth.append({
            "timestamp": (now - timedelta(seconds=60 + i * 2)).isoformat().replace("+00:00", "Z"),
            "price": 4199.80 - i * 0.10, "volume": 1.0, "side": "SELL", "is_direct": True,
        })
    built = power_m5_adapter.build_m5_inputs(ticks_breadth, now=now, tick_size=0.10, min_big_print_size=5.0)
    assert built["judges"]["footprint_levels"]["value"] > 0.40


def test_u14_u15_scout_order_block_keys_dead_event_pruning_and_shelter():
    """U-14 + U-15: Scout reads bottom/top OB keys, prunes cancelled MBO events, guards shelter."""
    obs = [{"bottom": 4196.5, "top": 4197.2, "type": "demand"}]
    book = signal_team.LevelBook()
    hist = []
    for _ob in obs:
        _lo = float(_ob.get("bottom", _ob.get("low", 0)) or 0)
        _hi = float(_ob.get("top", _ob.get("high", 0)) or 0)
        hist.append({"price": _lo, "kind": "demand"})
        hist.append({"price": _hi, "kind": "demand"})
    res_hist = book.add_history(4200.0, 2.0, hist, config)
    assert res_hist["history_added"] == 2

    l3_dead = SimpleNamespace(
        order_book={"bids": [(4199.9, 5.0)], "asks": [(4200.1, 5.0)]},
        spoof_levels={},
        iceberg_levels={},
        iceberg_meta={},
        order_events=[
            {"order_id": "oid_99", "type": "BID_NEW", "side": "ASK", "price": 4202.0, "size": 35.0},
            {"order_id": "oid_99", "type": "CANCEL", "side": "ASK", "price": 4202.0, "size": 35.0},
        ],
    )
    smap_dead = signal_team.LevelBook().ingest(l3_dead, 4200.0, 2.0, config, spread=0.20)
    assert all(abs(d["price"] - 4202.0) > 1e-6 for d in smap_dead["above"])

    # Populate real LevelBook with a melting-fast level at 4199.2 and a solid 28-lot bid wall at 4198.4
    book_sh = signal_team.LevelBook()
    t0 = time.time() - 60.0
    lv_fast = signal_team.Level(4199.2, "bid", 30.0, t0)
    lv_fast.last_seen = time.time()
    lv_fast.size_now = 10.0
    lv_fast.cycles_seen = 4
    lv_fast.samples = [(t0, 30.0), (t0 + 60.0, 10.0)]  # burn_rate = 20 lots/min -> melting-fast!
    lv_fast.sources = ["whale"]

    lv_solid = signal_team.Level(4198.4, "bid", 28.0, t0)
    lv_solid.last_seen = time.time()
    lv_solid.size_now = 28.0
    lv_solid.cycles_seen = 4
    lv_solid.samples = [(t0, 28.0), (t0 + 60.0, 28.0)]
    lv_solid.sources = ["whale"]

    book_sh.levels[4199.2] = lv_fast
    book_sh.levels[4198.4] = lv_solid
    book_sh.last_ladder = [(4198.4, 28.0), (4199.2, 10.0), (4199.8, 3.0)]
    sh = book_sh.shelter_behind(4200.0, True)
    assert sh is not None
    assert abs(sh["price"] - 4198.4) < 1e-6


def test_u16_session_levels_globex_18et_roll_cme_settlement_onh_ib(tmp_path):
    """U-16: 18:00 ET Globex roll, 13:29:30 ET CME settlement, ONH/ONL, IBH/IBL, DST helper."""
    et_summer = session_levels._to_us_eastern(datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc))
    et_winter = session_levels._to_us_eastern(datetime(2026, 12, 15, 12, 0, tzinfo=timezone.utc))
    assert et_summer.hour == 8   # UTC-4 EDT
    assert et_winter.hour == 7   # UTC-5 EST

    # Day 1 at 13:29:30 EDT (17:29:30 UTC): captures CME settlement!
    t_settle = datetime(2026, 10, 28, 17, 29, 30, tzinfo=timezone.utc)
    st1 = session_levels.update(4210.5, str(tmp_path), now=t_settle)
    assert st1["cme_settlement"] == 4210.5
    assert st1["day"] == "2026-10-28"

    # Same UTC evening at 18:05 EDT (22:05 UTC): rolls to 2026-10-29 Globex session + carries prev_settlement!
    t_roll = datetime(2026, 10, 28, 22, 5, 0, tzinfo=timezone.utc)
    st2 = session_levels.update(4215.0, str(tmp_path), now=t_roll)
    assert st2["day"] == "2026-10-29"
    assert st2.get("prev_settlement") == 4210.5 or st2.get("prev_close") == 4210.5
    assert st2["onh"] == 4215.0 and st2["onl"] == 4215.0

    # RTH IB window 08:30 EDT (12:30 UTC on Oct 29): updates IBH/IBL without mutating ONH/ONL
    t_ib = datetime(2026, 10, 29, 12, 30, 0, tzinfo=timezone.utc)
    st3 = session_levels.update(4222.0, str(tmp_path), now=t_ib)
    assert st3["onh"] == 4215.0
    assert st3["ibh"] == 4222.0
    kinds = {lv["kind"] for lv in session_levels.levels_for_map(st3)}
    assert {"onh", "today_open"}.issubset(kinds)


def test_u17_cont_kukanov_queue_fill_prob_and_fill_subtraction():
    """U-17: FILLs subtract from _order_map, ahead_vol <= total_vol, outflow drives fill_prob."""
    l3a = s2.Level3OrderBookAnalyzer()
    t0 = time.time() - 5.0
    l3a.update_order_book([(4199.9, 14.0)], [(4200.1, 10.0)])
    l3a.process_order_event({"order_id": "b1", "type": "BID_NEW", "side": "BID", "price": 4199.9, "size": 10.0, "timestamp": t0})
    l3a.process_order_event({"order_id": "b1", "type": "FILL", "side": "BID", "price": 4199.9, "size": 6.0, "timestamp": t0 + 1.0})
    l3a.process_order_event({"order_id": "b2", "type": "CANCEL", "side": "BID", "price": 4199.9, "size": 12.0, "timestamp": t0 + 2.0})

    q = l3a.estimate_queue_position(4199.9, "BUY", order_size=1.0)
    assert q["queue_ahead_vol"] <= q["queue_total_vol"]
    assert q["queue_ahead_vol"] == 4.0
    assert q["fill_prob"] >= 0.70

    # Even if level empties completely, ahead_vol clamps to 0.0 <= queue_total_vol
    l3a.update_order_book([], [(4200.1, 10.0)])
    q_empty = l3a.estimate_queue_position(4199.9, "BUY", order_size=1.0)
    assert q_empty.get("queue_ahead_vol", 0.0) <= q_empty.get("queue_total_vol", 0.0)


def test_u18_xray_packs_and_unpacks_power_jsonl(tmp_path):
    """U-18: X-Ray packs data/power_v2_memory/power_<DATE>.jsonl and unpack_vault restores it."""
    day = "2026-10-29"
    data_dir = tmp_path / "data"
    pmb_dir = data_dir / "power_v2_memory"
    logs_dir = tmp_path / "logs"
    pmb_dir.mkdir(parents=True)
    logs_dir.mkdir()

    pw_row = {"ts": "2026-10-29T10:00:00+00:00", "direction": "UP", "power_streak_cycles": 2}
    (pmb_dir / f"power_{day}.jsonl").write_text(json.dumps(pw_row) + "\n", encoding="utf-8")
    (data_dir / f"decisions_{day}.jsonl").write_text(
        json.dumps({"ts": "2026-10-29T10:00:00+00:00", "price": 4200.0, "direction": "BUY", "confidence": 60.0}) + "\n",
        encoding="utf-8",
    )

    txt_path, _, _ = xray_report.write_xray_files(tmp_path, day, include_vault=True)
    txt = txt_path.read_text(encoding="utf-8")
    assert "power_2026-10-29.jsonl" in txt

    unpack_dst = tmp_path / "unpacked"
    xray_report.unpack_vault(txt_path, unpack_dst)
    restored = (unpack_dst / f"power_{day}.jsonl").read_text(encoding="utf-8").strip()
    assert json.loads(restored)["power_streak_cycles"] == 2
