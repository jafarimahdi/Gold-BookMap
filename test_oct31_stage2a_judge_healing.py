"""Stage 2a Judge-Healing regression tests (2026-10-31, Laws #6-#11).

Verifies that all broken active and bench judges inside `step2_market_analysis.py`
and `config.py` are healed according to institutional textbook & microstructure rules
WITHOUT prematurely benching any active judge before the 5-day raw replay:
  1. `l3_large_ofi`: `analyze_market` initializes `l3._ref_mid` BEFORE processing
     `order_events` (Cont, Kukanov & Stoikov 2014 near-touch filter).
  2. `value_area`: Dalton (2013) regime split (TREND acceptance vs RANGE fade; zero double-votes).
  3. `vwap_bands`: No blind +/-2sig fading in TREND; +/-2.5sig requires climax rejection CLV.
  4. `cvd_divergence`: Requires >=0.35 strength + bar rejection + TREND seatbelt (Easley et al. 2012).
  5. `volume_roc`: Wyckoff Effort-vs-Result / Coulling (2013) Stopping/Climax vs Trend Initiation.
  6. `absorption` & `iceberg`: Frey & Sandas (2009) broken-wall invalidation & fresh <=180s near-touch gate.
  7. `footprint_levels`, `footprint_delta`, `sweep`, `_detect_order_blocks` (unmitigated zones),
     `whale_walls` (no BID_CANCEL bug), and `htf_poc` (Dalton migration vs H4 magnet).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import numpy as np

import config
import judge_panel as jp
import step2_market_analysis as s2


def _mk_of(**kw) -> s2.OrderFlowMetrics:
    defaults = dict(
        cvd=0.0, delta=0.0, buying_pressure=50.0, selling_pressure=50.0,
        level2_bid_depth=20.0, level2_ask_depth=20.0, bid_ask_ratio=1.0,
        large_orders=0,
    )
    defaults.update(kw)
    return s2.OrderFlowMetrics(**defaults)


def _panel_map(notes):
    return {row["judge"]: row["dir"] for row in jp.parse_judge_panel(notes)}


def test_stage2c_pure_12_active_winners_and_7_healthy_benched(monkeypatch):
    """Stage 2c (after 5-day healed M3 replay): 12 winners stay active; 7 healthy underperformers sit on bench."""
    active_12 = {
        "mtf", "vwap_trend", "value_area", "cvd_divergence", "cvd_momentum",
        "l3_large_ofi", "sweep", "spoof_invert", "l3_net_flow", "l3_aggr_limit",
        "macro_risk", "news_sentiment",
    }
    benched_7 = {
        "footprint_delta", "iceberg", "volume_roc", "l3_imbalance",
        "footprint_levels", "absorption", "vwap_bands",
    }
    assert active_12.isdisjoint(config.RETIRED_JUDGES)
    assert benched_7.issubset(config.RETIRED_JUDGES)

    # Even with an older .env RETIRED_JUDGES list, config._refresh() unions all 7 benched judges:
    with monkeypatch.context() as mp:
        mp.setenv("RETIRED_JUDGES", "macro_yield,macro_dxy,whale_walls,htf_poc")
        mp.delenv("UNRETIRE_JUDGES", raising=False)
        config._refresh()
        assert active_12.isdisjoint(config.RETIRED_JUDGES)
        assert benched_7.issubset(config.RETIRED_JUDGES)
    config._refresh()

    # Benched judges cast 0 active votes in SignalEngine while still emitting notes for [7b] & Scout Map:
    eng = s2.SignalEngine()
    _, d_benched, c_benched, notes_benched = eng.aggregate(
        4200.0,
        s2.VolatilityMetrics(atr=2.0),
        s2.TrendMetrics(adx=15.0),
        _mk_of(absorption_net=3),
        s2.FootprintMetrics(delta_imbalance=0.35, dominant_level=4200.0, footprint_strength=0.8),
        s2.Level3Events(order_book={"bids": [(4199.9, 45.0)], "asks": [(4200.1, 10.0)]}),
        s2.VolumeProfileMetrics(vwap=4205.0, vwap_std=2.0),
        s2.MacroMetrics(),
        s2.NewsAndEvents(),
        regime="RANGE",
    )
    assert d_benched == "NEUTRAL"
    assert c_benched == 0.0
    pm_benched = {row["judge"]: row for row in jp.parse_judge_panel(notes_benched)}
    for bj in ("absorption", "footprint_delta", "l3_imbalance", "vwap_bands"):
        assert bj in pm_benched and pm_benched[bj].get("retired") is True


def test_analyze_market_initializes_l3_ref_mid_before_order_events(monkeypatch, tmp_path):
    """Cont, Kukanov & Stoikov (2014): $20-away MBO orders in analyze_market must NOT trigger l3_large_ofi."""
    monkeypatch.setattr(s2, "DATA_DIR", lambda: str(tmp_path), raising=False)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    now = datetime(2026, 10, 30, 14, 30, 0, tzinfo=timezone.utc)
    ep = now.timestamp()
    base_md = {
        "price": 4200.0,
        "bid": 4199.9,
        "ask": 4200.1,
        "bids": [[4199.9, 15.0], [4199.8, 12.0]],
        "asks": [[4200.1, 15.0], [4200.2, 12.0]],
        "candles": {
            "open": [4200.0] * 30,
            "high": [4200.8] * 30,
            "low": [4199.2] * 30,
            "close": [4200.0] * 30,
            "volume": [100.0] * 30,
        },
        "tick_data": [
            {"timestamp": ep - 10.0 + i, "price": 4200.1, "volume": 5.0, "side": "BUY", "is_direct": True}
            for i in range(8)
        ],
        "news": {"fetch_calendar": False, "headlines": [], "events": []},
    }
    # 1) Far-book orders $20 away -> near_touch_ofi == 0 -> l3_large_ofi silent!
    md_far = dict(base_md)
    md_far["order_events"] = [
        {"timestamp": ep - 5.0, "type": "BID_NEW", "side": "BID", "price": 4180.0 - i * 0.5, "size": 120.0, "order_id": f"f{i}"}
        for i in range(5)
    ]
    s2._PERSISTENT_L2 = None
    snap_far = s2.analyze_market(md_far, now=now)
    assert snap_far.level3.near_large_order_events == 0
    assert snap_far.level3.near_touch_ofi == 0.0
    assert "l3_large_ofi" not in _panel_map(snap_far.notes)

    # 2) Near-touch orders within $0.30 of mid + aggressive buy alignment (next bucket) -> l3_large_ofi votes BUY!
    now2 = now + timedelta(minutes=10)
    ep2 = now2.timestamp()
    md_near = dict(base_md)
    md_near["tick_data"] = [
        {"timestamp": ep2 - 10.0 + i, "price": 4200.1, "volume": 5.0, "side": "BUY", "is_direct": True}
        for i in range(8)
    ]
    md_near["order_events"] = [
        {"timestamp": ep2 - 5.0, "type": "BID_NEW", "side": "BID", "price": 4199.8 - i * 0.1, "size": 120.0, "order_id": f"n{i}"}
        for i in range(4)
    ]
    s2._PERSISTENT_L2 = None
    snap_near = s2.analyze_market(md_near, now=now2)
    assert snap_near.level3.near_large_order_events == 4
    assert snap_near.level3.near_touch_ofi > 25.0
    assert _panel_map(snap_near.notes).get("l3_large_ofi") == 1.0


def test_dalton_value_area_and_vwap_bands_respect_trend_vs_range():
    """Dalton (2013): Never fade VA/VWAP 2sig against a TREND waterfall; fade cleanly in RANGE."""
    eng = s2.SignalEngine()
    vol = s2.VolatilityMetrics(atr=2.0)
    tr_dn = s2.TrendMetrics(trend_direction="DOWN", adx=32.0)
    of = _mk_of()
    fp = s2.FootprintMetrics()
    l3 = s2.Level3Events()
    mac = s2.MacroMetrics()
    nw = s2.NewsAndEvents()

    # Downward TREND waterfall: price 4195.8 is just above VAL 4195.0 (old code faded BUY!) and at -2.2sig VWAP
    vp_waterfall = s2.VolumeProfileMetrics(
        vwap=4202.0, vwap_std=2.8, value_area_high=4205.0, value_area_low=4195.0,
    )
    c_dn = np.array([4198.0, 4197.0, 4195.8])
    h_dn = np.array([4198.5, 4197.4, 4197.1])
    l_dn = np.array([4197.2, 4195.9, 4195.7])  # closes on its low (CLV ~ 0.07, NOT a rejection!)
    _, _, _, notes_tr = eng.aggregate(
        4195.8, vol, tr_dn, of, fp, l3, vp_waterfall, mac, nw,
        regime="TREND", mtf_trends={"H1": "DOWN", "M15": "DOWN", "M9": "DOWN"},
        recent_closes=c_dn, recent_highs=h_dn, recent_lows=l_dn,
    )
    pm_tr = _panel_map(notes_tr)
    assert "value_area" not in pm_tr  # did NOT fade near_val in TREND!
    assert "vwap_bands" not in pm_tr  # did NOT fade -2sig in TREND without climax rejection!

    # Accepted breakdown below VAL in TREND -> votes SELL (-1.0)
    c_brk = np.array([4196.5, 4195.2, 4194.2])
    _, _, _, notes_brk = eng.aggregate(
        4194.2, vol, tr_dn, of, fp, l3, vp_waterfall, mac, nw,
        regime="TREND", mtf_trends={"H1": "DOWN", "M15": "DOWN", "M9": "DOWN"},
        recent_closes=c_brk,
    )
    assert _panel_map(notes_brk).get("value_area") == -1.0

    # Balanced RANGE test of VAL / -2sig -> responsive BUY (+1.0), single vote only
    _, _, _, notes_rng = eng.aggregate(
        4195.8, vol, s2.TrendMetrics(adx=15.0), of, fp, l3, vp_waterfall, mac, nw,
        regime="RANGE", recent_closes=np.array([4196.2, 4195.6, 4195.8]),
    )
    pm_rng = _panel_map(notes_rng)
    assert pm_rng.get("value_area") == 1.0
    assert pm_rng.get("vwap_bands") == 1.0


def test_wyckoff_volume_roc_stopping_climax_vs_trend_initiation():
    """Wyckoff Effort-vs-Result (Coulling 2013): Buying climax wick -> SELL; TREND breakout -> BUY."""
    eng = s2.SignalEngine()
    vol = s2.VolatilityMetrics(atr=2.0)
    of = _mk_of()
    fp = s2.FootprintMetrics()
    l3 = s2.Level3Events()
    mac = s2.MacroMetrics()
    nw = s2.NewsAndEvents()

    # 1) Buying Climax in RANGE: pushes 6-bar high 4205.0 on 2.5x vol, rejects & closes at 4202.3 (CLV=0.10)
    h_clx = np.array([4202.5] * 14 + [4205.0])
    l_clx = np.array([4201.0] * 14 + [4202.0])
    c_clx = np.array([4202.0] * 14 + [4202.3])
    v_clx = np.array([100.0] * 14 + [250.0])
    vp_clx = s2.VolumeProfileMetrics(vwap=4199.0, vwap_zscore=1.8, volume_rate_of_change=1.5)
    _, _, _, notes_clx = eng.aggregate(
        4202.3, vol, s2.TrendMetrics(adx=16.0), of, fp, l3, vp_clx, mac, nw,
        regime="RANGE", recent_closes=c_clx, recent_highs=h_clx, recent_lows=l_clx, recent_volumes=v_clx,
    )
    assert _panel_map(notes_clx).get("volume_roc") == -1.0  # Bearish climax absorption -> SELL!

    # 2) Unstretched TREND+MTF initiation: breaks high & closes strong at 4204.8 (CLV=0.93) -> BUY!
    h_ini = np.array([4202.5] * 14 + [4205.0])
    l_ini = np.array([4201.0] * 14 + [4202.0])
    c_ini = np.array([4202.0] * 14 + [4204.8])
    v_ini = np.array([100.0] * 14 + [250.0])
    vp_ini = s2.VolumeProfileMetrics(vwap=4203.0, vwap_zscore=0.8, volume_rate_of_change=1.5)
    _, _, _, notes_ini = eng.aggregate(
        4204.8, vol, s2.TrendMetrics(trend_direction="UP", adx=30.0), of, fp, l3, vp_ini, mac, nw,
        regime="TREND", mtf_trends={"H1": "UP", "M15": "UP", "M9": "UP"},
        recent_closes=c_ini, recent_highs=h_ini, recent_lows=l_ini, recent_volumes=v_ini,
    )
    assert _panel_map(notes_ini).get("volume_roc") == 1.0


def test_absorption_wall_break_invalidation_and_fresh_iceberg_gate():
    """Frey & Sandas (2009): Broken bid wall is pruned immediately; stale >180s iceberg is ignored."""
    l2 = s2.OrderBookDepthAnalyzer(wall_size=25.0)
    t0 = 1_760_000_000.0
    l2.update([(4200.0, 45.0)], [(4200.2, 12.0)], now_ts=t0)
    l2.update([(4200.0, 25.0)], [(4200.2, 12.0)], now_ts=t0 + 5.0)
    assert l2.summary([(4200.0, 25.0)], [(4200.2, 12.0)], now_ts=t0 + 5.0)["absorption_net"] > 0

    # Price smashes through 4200.0 down to 4199.4 -> broken wall immediately invalidated!
    l2.update([(4199.4, 15.0)], [(4199.6, 20.0)], now_ts=t0 + 10.0)
    assert l2.summary([(4199.4, 15.0)], [(4199.6, 20.0)], now_ts=t0 + 10.0)["absorption_net"] == 0

    # Stale iceberg (last_seen 600s ago > 180s window) does NOT vote even if 0.5 pts away
    eng = s2.SignalEngine()
    l3_stale = s2.Level3Events(
        iceberg_events=2,
        iceberg_levels={4199.5: 4},
        iceberg_meta={4199.5: {"score": 25000, "age_sec": 600, "refills": 4, "last_seen": t0 - 600.0}},
    )
    _, _, _, notes_st = eng.aggregate(
        4200.0, s2.VolatilityMetrics(atr=2.0), s2.TrendMetrics(),
        _mk_of(delta=30.0), s2.FootprintMetrics(), l3_stale,
        s2.VolumeProfileMetrics(), s2.MacroMetrics(), s2.NewsAndEvents(),
        regime="RANGE", now_epoch=t0,
    )
    assert "iceberg" not in _panel_map(notes_st)

    # Fresh iceberg (last_seen 20s ago, <= 1.0 ATR, delta >= 0) DOES vote BUY!
    l3_fresh = s2.Level3Events(
        iceberg_events=2,
        iceberg_levels={4199.5: 4},
        iceberg_meta={4199.5: {"score": 25000, "age_sec": 45, "refills": 4, "last_seen": t0 - 20.0}},
    )
    _, _, _, notes_fr = eng.aggregate(
        4200.0, s2.VolatilityMetrics(atr=2.0), s2.TrendMetrics(),
        _mk_of(delta=30.0), s2.FootprintMetrics(), l3_fresh,
        s2.VolumeProfileMetrics(), s2.MacroMetrics(), s2.NewsAndEvents(),
        regime="RANGE", now_epoch=t0,
    )
    assert _panel_map(notes_fr).get("iceberg") == 1.0


def test_footprint_stacked_imbalance_trapped_delta_and_unmitigated_order_blocks():
    """Footprint filters 2-vs-1 lot noise & trapped delta; _detect_order_blocks drops broken zones."""
    ticks = [
        # 2 noisy 2-vs-1 levels (overall_vol >= 25 lots -> must be ignored as noise!)
        {"price": 4200.0, "volume": 2.0, "side": "BUY", "is_direct": True},
        {"price": 4200.0, "volume": 1.0, "side": "SELL", "is_direct": True},
        {"price": 4200.1, "volume": 2.0, "side": "BUY", "is_direct": True},
        {"price": 4200.1, "volume": 1.0, "side": "SELL", "is_direct": True},
        # 2 genuine >=2.5x stacked buying levels (12 vs 2, 10 vs 1)
        {"price": 4200.2, "volume": 12.0, "side": "BUY", "is_direct": True},
        {"price": 4200.2, "volume": 2.0, "side": "SELL", "is_direct": True},
        {"price": 4200.3, "volume": 10.0, "side": "BUY", "is_direct": True},
        {"price": 4200.3, "volume": 1.0, "side": "SELL", "is_direct": True},
    ]
    fpm = s2.FootprintBuilder().build_footprint(ticks)
    assert set(fpm.buying_levels) == {4200.2, 4200.3}

    # Trapped positive delta (+0.32) on a candle closing at its low (CLV=0.10) stays silent!
    eng = s2.SignalEngine()
    _, _, _, notes_trap = eng.aggregate(
        4200.1, s2.VolatilityMetrics(atr=2.0), s2.TrendMetrics(),
        _mk_of(),
        s2.FootprintMetrics(delta_imbalance=0.32, dominant_level=4200.2, footprint_strength=0.35),
        s2.Level3Events(), s2.VolumeProfileMetrics(), s2.MacroMetrics(), s2.NewsAndEvents(),
        regime="RANGE",
        recent_closes=np.array([4201.0, 4200.2]),
        recent_highs=np.array([4201.5, 4202.0]),
        recent_lows=np.array([4200.5, 4200.0]),
    )
    assert "footprint_delta" not in _panel_map(notes_trap)

    # _detect_order_blocks: demand swing at bar 4 (low=4195.0) later closed below at bar 9 (4193.0) is pruned!
    o = np.full(14, 4200.0)
    h = np.full(14, 4201.0)
    l = np.full(14, 4199.0)
    c = np.full(14, 4200.0)
    l[4], c[4] = 4195.0, 4196.0
    h[5], c[5] = 4202.0, 4201.5  # strong impulse up
    l[9], c[9] = 4192.5, 4193.0  # later smashes & closes below 4195.0 -> mitigated!
    zones = s2._detect_order_blocks(h, l, c, o, k=3)
    assert not any(z["type"] == "demand" and abs(z["bottom"] - 4195.0) < 1e-6 for z in zones)
