"""Stage 2R (2026-11-02): 15-Day Surprise-Quiz Heart / Judges Cardiology Surgery.

Verifies in strict Law #9 isolation (zero gate/exit/cooldown changes):
1. `mtf` confluence weight parity across all 3 parsers + `MAP` family leadership.
2. Stacked-HTF seatbelt across `NEUTRAL`/`RANGE` pauses vs standalone `{}` unit-test neutrality.
3. `vwap_trend` 2-bar VWAP acceptance, >=0.20*ATR separation, calm `w=0.45`, and anchor-HTF non-opposition.
4. `value_area` strict `RANGE`-only (`<=0.45*ATR`) rejection fade (`NEUTRAL` silenced) + calm `w=0.45..0.50`.
5. `spoof_invert` near-touch layered spoofing (`>=2` within `max(1.5*ATR, $2.00)`) + squeeze delta + seatbelt.
6. `cvd_momentum` dynamic `TIMEFRAME` scaling (Law #11) + Effort-with-Result `CLV` & `l3_large_ofi` real tape follow-through.
"""
from __future__ import annotations

import numpy as np

import audit_day
import config
import judge_families as jf
import judge_panel as jp
import step2_market_analysis as s2


def _mk_of(**kw) -> s2.OrderFlowMetrics:
    defaults = dict(
        cvd=0.0,
        delta=0.0,
        buying_pressure=50.0,
        selling_pressure=50.0,
        level2_bid_depth=50.0,
        level2_ask_depth=50.0,
        bid_ask_ratio=1.0,
        large_orders=0,
    )
    defaults.update(kw)
    return s2.OrderFlowMetrics(**defaults)


def _panel_by_judge(notes: list[str]) -> dict[str, dict]:
    return {row["judge"]: row for row in jp.parse_judge_panel(notes)}


def test_mtf_confluence_weight_parity_and_map_family_leadership():
    """3-parser parity on MTF confluence weights + MAP family cannot be hijacked against MTF."""
    eng = s2.SignalEngine()
    vol = s2.VolatilityMetrics(atr=2.0)
    tr = s2.TrendMetrics(trend_direction="DOWN", adx=28.0)
    of = _mk_of()
    fp = s2.FootprintMetrics()
    l3 = s2.Level3Events()
    vp = s2.VolumeProfileMetrics(vwap=4202.0, value_area_high=4206.0, value_area_low=4196.0)
    mac = s2.MacroMetrics()
    nw = s2.NewsAndEvents()

    _, _, _, notes_3 = eng.aggregate(
        4198.0, vol, tr, of, fp, l3, vp, mac, nw,
        regime="TREND", mtf_trends={"H1": "DOWN", "M15": "DOWN", "M9": "DOWN"},
        recent_closes=np.array([4200.0, 4199.0, 4198.0]),
    )
    p_s2 = {r["judge"]: r for r in s2.parse_judge_panel(notes_3)}
    p_jp = {r["judge"]: r for r in jp.parse_judge_panel(notes_3)}
    p_ad_list, _ = audit_day.judge_panel({"notes": notes_3})
    p_ad = {j: (d, w) for j, d, w in p_ad_list}

    assert p_s2["mtf"]["dir"] == -1.0 and abs(p_s2["mtf"]["weight"] - 1.15) < 1e-6
    assert p_jp["mtf"]["dir"] == -1.0 and abs(p_jp["mtf"]["weight"] - 1.15) < 1e-6
    assert p_ad["mtf"] == (-1.0, 1.15)

    # Legacy unweighted note keeps exact 0.50 fallback across all 3 parsers
    legacy = ["MTF H1:UP M15:DOWN M9:DOWN"]
    assert s2.parse_judge_panel(legacy)[0]["weight"] == 0.5
    assert jp.parse_judge_panel(legacy)[0]["weight"] == 0.5
    assert audit_day.judge_panel({"notes": legacy})[0][0] == ("mtf", -1.0, 0.5)

    # MAP family consensus stays SELL (-1.0) even if a synthetic 0.45 BUY note is paired with 2/1 MTF (0.65 SELL)
    cons = jf.family_gate(
        [
            {"judge": "mtf", "dir": -1.0, "weight": 0.65},
            {"judge": "value_area", "dir": 1.0, "weight": 0.45},
        ],
        "SELL",
        retired=config.RETIRED_JUDGES,
    )
    assert cons["families"]["MAP"] == -1.0


def test_stacked_htf_seatbelt_blocks_pause_bar_knife_catching():
    """When H1+M15+M9 are stacked DOWN, a 1-bar ADX dip into RANGE/NEUTRAL cannot vote BUY."""
    eng = s2.SignalEngine()
    vol = s2.VolatilityMetrics(atr=2.0)
    tr = s2.TrendMetrics(adx=18.0)
    of_bounce = _mk_of(delta=45.0, cvd=200.0, buying_pressure=68.0, selling_pressure=32.0)
    fp = s2.FootprintMetrics()
    l3 = s2.Level3Events()
    vp = s2.VolumeProfileMetrics(vwap=4202.0, value_area_high=4205.0, value_area_low=4195.0)
    mac = s2.MacroMetrics()
    nw = s2.NewsAndEvents()

    _, d_pause, _, notes_pause = eng.aggregate(
        4195.6, vol, tr, of_bounce, fp, l3, vp, mac, nw,
        regime="RANGE",
        mtf_trends={"H1": "DOWN", "M15": "DOWN", "M9": "DOWN"},
        recent_closes=np.array([4196.2, 4195.3, 4195.6]),
        recent_highs=np.array([4196.4, 4195.8, 4195.7]),
        recent_lows=np.array([4195.5, 4195.1, 4195.1]),
    )
    pm = _panel_by_judge(notes_pause)
    assert "value_area" not in pm
    assert "cvd_momentum" not in pm
    assert d_pause == "SELL"


def test_vwap_trend_requires_separation_two_bar_acceptance_and_anchor_alignment():
    """vwap_trend ignores 1-tick VWAP pokes & H1-opposed counter-rallies; votes 0.45 when aligned."""
    eng = s2.SignalEngine()
    vol = s2.VolatilityMetrics(atr=2.0)
    tr = s2.TrendMetrics(trend_direction="UP", adx=30.0)
    of = _mk_of()
    fp = s2.FootprintMetrics()
    l3 = s2.Level3Events()
    vp = s2.VolumeProfileMetrics(vwap=4200.0)
    mac = s2.MacroMetrics()
    nw = s2.NewsAndEvents()

    # 1) Tiny $0.15 poke (< 0.20*ATR = $0.40) -> silent
    _, _, _, n_tiny = eng.aggregate(
        4200.15, vol, tr, of, fp, l3, vp, mac, nw,
        regime="TREND", recent_closes=np.array([4199.9, 4200.05, 4200.15]),
    )
    assert "vwap_trend" not in _panel_by_judge(n_tiny)

    # 2) Counter-rally above intraday VWAP while H1 anchor is DOWN (even if M15+M9 bounced UP) -> silent
    _, _, _, n_counter = eng.aggregate(
        4200.8, vol, tr, of, fp, l3, vp, mac, nw,
        regime="TREND",
        mtf_trends={"H1": "DOWN", "M15": "UP", "M9": "UP"},
        recent_closes=np.array([4200.1, 4200.4, 4200.8]),
    )
    assert "vwap_trend" not in _panel_by_judge(n_counter)

    # 3) Clean >=0.20*ATR 2-bar accepted trend aligned with H1 -> votes UP at calm w=0.45
    _, _, _, n_ok = eng.aggregate(
        4200.8, vol, tr, of, fp, l3, vp, mac, nw,
        regime="TREND",
        mtf_trends={"H1": "UP", "M15": "UP", "M9": "UP"},
        recent_closes=np.array([4200.1, 4200.4, 4200.8]),
    )
    pm_ok = _panel_by_judge(n_ok)
    assert pm_ok["vwap_trend"]["dir"] == 1.0
    assert abs(pm_ok["vwap_trend"]["weight"] - 0.45) < 1e-6


def test_value_area_silences_neutral_regime_and_outer_band_loitering():
    """value_area never fades in NEUTRAL (20<=ADX<25) or >0.45*ATR away, and requires rejection in RANGE."""
    eng = s2.SignalEngine()
    vol = s2.VolatilityMetrics(atr=2.0)
    tr = s2.TrendMetrics(adx=22.0)
    of_ok = _mk_of(delta=8.0)
    of_sell = _mk_of(delta=-15.0)
    fp = s2.FootprintMetrics()
    l3 = s2.Level3Events()
    vp = s2.VolumeProfileMetrics(vwap=4200.0, value_area_high=4205.0, value_area_low=4195.0)
    mac = s2.MacroMetrics()
    nw = s2.NewsAndEvents()

    # 1) Developing NEUTRAL regime near VAL -> silent (no responsive fade in NEUTRAL!)
    _, _, _, n_neu = eng.aggregate(
        4195.6, vol, tr, of_ok, fp, l3, vp, mac, nw,
        regime="NEUTRAL", recent_closes=np.array([4196.0, 4195.4, 4195.6]),
    )
    assert "value_area" not in _panel_by_judge(n_neu)

    # 2) RANGE outer-band loitering $1.30 above VAL (0.65*ATR > 0.45*ATR) -> silent
    _, _, _, n_far = eng.aggregate(
        4196.3, vol, s2.TrendMetrics(adx=15.0), of_ok, fp, l3, vp, mac, nw,
        regime="RANGE", recent_closes=np.array([4196.6, 4196.1, 4196.3]),
    )
    assert "value_area" not in _panel_by_judge(n_far)

    # 3) RANGE near VAL but aggressive sellers dominating (delta < 0) -> silent
    _, _, _, n_knife = eng.aggregate(
        4195.6, vol, s2.TrendMetrics(adx=15.0), of_sell, fp, l3, vp, mac, nw,
        regime="RANGE", recent_closes=np.array([4196.0, 4195.4, 4195.6]),
    )
    assert "value_area" not in _panel_by_judge(n_knife)

    # 4) Tight RANGE test of VAL (0.30*ATR) + bounce bar + positive delta -> BUY at calm w=0.45
    _, _, _, n_rng = eng.aggregate(
        4195.6, vol, s2.TrendMetrics(adx=15.0), of_ok, fp, l3, vp, mac, nw,
        regime="RANGE", recent_closes=np.array([4196.0, 4195.4, 4195.6]),
    )
    pm_rng = _panel_by_judge(n_rng)
    assert pm_rng["value_area"]["dir"] == 1.0
    assert abs(pm_rng["value_area"]["weight"] - 0.45) < 1e-6


def test_spoof_invert_ignores_far_book_cancels_and_requires_near_touch_squeeze():
    """Far-away ($15) ask cancels during crashes never vote BUY; near-touch >=2 spoofs + squeeze delta do."""
    eng = s2.SignalEngine()
    vol = s2.VolatilityMetrics(atr=2.0)
    tr = s2.TrendMetrics(adx=18.0)
    fp = s2.FootprintMetrics()
    vp = s2.VolumeProfileMetrics()
    mac = s2.MacroMetrics()
    nw = s2.NewsAndEvents()

    # 1) Crash scenario: ask spoof canceled $15 above price + negative delta -> silent!
    l3_far = s2.Level3Events(spoof_events=3, spoof_levels={4215.0: 3})
    _, _, _, n_far = eng.aggregate(
        4200.0, vol, tr, _mk_of(delta=-30.0), fp, l3_far, vp, mac, nw,
        regime="RANGE", recent_closes=np.array([4202.0, 4201.0, 4200.0]),
    )
    assert "spoof_invert" not in _panel_by_judge(n_far)

    # 2) Single near-touch ask cancel (count=1 < 2) -> silent!
    l3_one = s2.Level3Events(spoof_events=1, spoof_levels={4201.2: 1})
    _, _, _, n_one = eng.aggregate(
        4200.0, vol, tr, _mk_of(delta=20.0), fp, l3_one, vp, mac, nw,
        regime="RANGE", recent_closes=np.array([4199.6, 4199.8, 4200.0]),
    )
    assert "spoof_invert" not in _panel_by_judge(n_one)

    # 3) Near-touch layered ask spoofs (>=2 within $1.20 <= 1.5*ATR) + positive delta + rising bar -> BUY (w=0.55)
    l3_trap = s2.Level3Events(spoof_events=2, spoof_levels={4201.0: 1, 4201.4: 1})
    _, _, _, n_trap = eng.aggregate(
        4200.0, vol, tr, _mk_of(delta=18.0), fp, l3_trap, vp, mac, nw,
        regime="RANGE", recent_closes=np.array([4199.5, 4199.8, 4200.0]),
    )
    pm_trap = _panel_by_judge(n_trap)
    assert pm_trap["spoof_invert"]["dir"] == 1.0
    assert abs(pm_trap["spoof_invert"]["weight"] - 0.55) < 1e-6


def test_cvd_momentum_timeframe_scaling_clv_and_l3_large_ofi_tape_followthrough(monkeypatch):
    """Law #11 dynamic window scaling + Effort-with-Result CLV for cvd_momentum, and aggr_net>=15 for l3_large_ofi."""
    eng = s2.SignalEngine()
    vol = s2.VolatilityMetrics(atr=2.0)
    tr = s2.TrendMetrics(adx=26.0, trend_direction="UP")
    fp = s2.FootprintMetrics()
    vp = s2.VolumeProfileMetrics()
    mac = s2.MacroMetrics()
    nw = s2.NewsAndEvents()

    # On M3 (180s -> threshold 35 lots): +40 delta with upper-wick absorption (CLV = 0.20 < 0.60) -> silent!
    monkeypatch.setattr(config, "ORDER_FLOW_WINDOW_SECONDS", 180.0, raising=False)
    of_40 = _mk_of(delta=40.0, cvd=150.0, buying_pressure=64.0, selling_pressure=36.0)
    _, _, _, n_wick = eng.aggregate(
        4200.2, vol, tr, of_40, fp, s2.Level3Events(), vp, mac, nw,
        regime="TREND",
        recent_closes=np.array([4199.8, 4200.0, 4200.2]),
        recent_highs=np.array([4200.1, 4200.3, 4201.0]),
        recent_lows=np.array([4199.5, 4199.8, 4200.0]),  # CLV = (4200.2 - 4200.0)/1.0 = 0.20
    )
    assert "cvd_momentum" not in _panel_by_judge(n_wick)

    # Strong close at top of bar (CLV = 0.90 >= 0.60) on M3 -> votes BUY; on M5 (300s -> thr 58.3 lots) +40 delta is silent!
    _, _, _, n_m3 = eng.aggregate(
        4200.9, vol, tr, of_40, fp, s2.Level3Events(), vp, mac, nw,
        regime="TREND",
        recent_closes=np.array([4199.8, 4200.0, 4200.9]),
        recent_highs=np.array([4200.1, 4200.3, 4201.0]),
        recent_lows=np.array([4199.5, 4199.8, 4200.0]),  # CLV = 0.90
    )
    assert _panel_by_judge(n_m3)["cvd_momentum"]["dir"] == 1.0

    monkeypatch.setattr(config, "ORDER_FLOW_WINDOW_SECONDS", 300.0, raising=False)
    _, _, _, n_m5_sub = eng.aggregate(
        4200.9, vol, tr, of_40, fp, s2.Level3Events(), vp, mac, nw,
        regime="TREND",
        recent_closes=np.array([4199.8, 4200.0, 4200.9]),
        recent_highs=np.array([4200.1, 4200.3, 4201.0]),
        recent_lows=np.array([4199.5, 4199.8, 4200.0]),
    )
    assert "cvd_momentum" not in _panel_by_judge(n_m5_sub)

    # l3_large_ofi: passive adds with zero aggressive tape (aggr_net == 0) stay silent; real follow-through (>=15) votes!
    l3_passive_only = s2.Level3Events(
        near_large_order_events=5, near_touch_ofi=65.0,
        aggressive_buy_volume=10.0, aggressive_sell_volume=10.0,
    )
    _, _, _, n_pas = eng.aggregate(
        4200.5, vol, tr, _mk_of(), fp, l3_passive_only, vp, mac, nw,
        regime="TREND", recent_closes=np.array([4200.0, 4200.2, 4200.5]),
    )
    assert "l3_large_ofi" not in _panel_by_judge(n_pas)

    l3_real = s2.Level3Events(
        near_large_order_events=5, near_touch_ofi=65.0,
        aggressive_buy_volume=35.0, aggressive_sell_volume=10.0,
    )
    _, _, _, n_real = eng.aggregate(
        4200.5, vol, tr, _mk_of(), fp, l3_real, vp, mac, nw,
        regime="TREND", recent_closes=np.array([4200.0, 4200.2, 4200.5]),
    )
    assert _panel_by_judge(n_real)["l3_large_ofi"]["dir"] == 1.0
