import time
from types import SimpleNamespace

import config
import escort_team
from position_manager import PositionManager
from tools.raw_replay import _walk_bracket


def _make_pm_with_capture():
    pm = PositionManager.__new__(PositionManager)
    pm._mt5 = SimpleNamespace(POSITION_TYPE_BUY=0, POSITION_TYPE_SELL=1)
    pm._hist = []
    pm._state = {}
    pm._save_state = lambda: None
    pm._journal = lambda _d: None
    mods = []
    pm._modify_stops = lambda pos, nsl, ntp, rule, reason, ctx: mods.append((nsl, ntp, rule, reason))
    return pm, mods


def test_stage4b_wave_position_not_choked_below_1r_or_by_pebble_wall(monkeypatch):
    monkeypatch.setattr(config, "STAGE4B_WAVE_TRAIL_ENABLED", True)
    monkeypatch.setattr(config, "PM_C3_WALL_TRAIL_ENABLED", True)
    monkeypatch.setattr(config, "PM_WAVE_TRAIL_TRIGGER_R", 1.0)
    monkeypatch.setattr(config, "PM_WAVE_TRAIL_ATR", 1.50)
    monkeypatch.setattr(config, "PM_WAVE_BE_TRIGGER_R", 1.0)
    monkeypatch.setattr(config, "PM_WAVE_MAJOR_WALL_TP_LOTS", 40.0)

    pm, mods = _make_pm_with_capture()
    now = time.time()
    # M3 wave BUY @ 4020.00, SL=4012.80 (7.20 = 2.0x ATR), TP=4032.60 (12.60 = 3.5x ATR)
    # Current gain = +2.40 pts (0.33R > +$1.00 quick BE, > 0.67x ATR, HIGH_VOL day, 15-lot pebble ahead)
    pm._state["701"] = {
        "initial_sl": 4012.80,
        "high_water_pts": 2.40,
        "be_done": False,
        "partial_done": False,
        "adopted_ts": now - 120.0,
        "last_modify_ts": 0.0,
        "is_a2_burst": False,
    }
    pos = SimpleNamespace(
        ticket=701,
        symbol="XAUUSD",
        type=0,
        price_open=4020.00,
        sl=4012.80,
        tp=4032.60,
        volume=0.01,
        profit=2.40,
        comment="gold-bot M3 wave",
    )
    snap = SimpleNamespace(
        price=4022.40,
        bid=4022.30,
        ask=4022.50,
        indicators=SimpleNamespace(atr_14=3.60),
        volatility=SimpleNamespace(atr=3.60, volatility_rank=0.85),
        order_flow=SimpleNamespace(delta=18.0, buying_pressure=64.0, cvd=50.0),
        volume_profile=SimpleNamespace(
            vwap=4019.00, vwap_zscore=0.8, poc=4019.50, value_area_high=4025.0, value_area_low=4016.0
        ),
        regime="TREND",
        signal_direction="BUY",
        signal_strength=62.0,
        divergence=0.0,
        news=SimpleNamespace(news_state="QUIET", impact_level="LOW", minutes_to_next_event=999),
        signal_map={
            "above": [{"price": 4024.50, "size": 15.0}],
            "below": [{"price": 4019.00, "size": 14.0}],
        },
        order_blocks=[],
        footprint=None,
        htf_poc={},
        macro_bias=0.0,
    )
    tick = SimpleNamespace(bid=4022.30, ask=4022.50)
    info = SimpleNamespace(point=0.01, trade_stops_level=5, digits=2)

    pm._manage_one(pos, snap, tick, info)
    assert mods == [], f"Wave trade at +0.33R must not tighten SL or yank TP to 15-lot pebble, got {mods}"


def test_stage4b_wave_chandelier_engages_at_1r_and_respects_major_wall_tp(monkeypatch):
    monkeypatch.setattr(config, "STAGE4B_WAVE_TRAIL_ENABLED", True)
    monkeypatch.setattr(config, "PM_C3_WALL_TRAIL_ENABLED", True)
    monkeypatch.setattr(config, "PM_WAVE_TRAIL_TRIGGER_R", 1.0)
    monkeypatch.setattr(config, "PM_WAVE_TRAIL_ATR", 1.50)
    monkeypatch.setattr(config, "PM_WAVE_BE_TRIGGER_R", 1.0)
    monkeypatch.setattr(config, "PM_WAVE_MAJOR_WALL_TP_LOTS", 40.0)
    monkeypatch.setattr(config, "PM_PARTIAL_EXIT_ENABLE", False)

    pm, mods = _make_pm_with_capture()
    now = time.time()
    # Price hits 4029.00 (+9.00 pts = 1.25R on 7.20 initial_r, ATR=3.60)
    # Chandelier 1.5x ATR = 4020.00 + 9.00 - 5.40 = 4023.60 (+3.60 locked profit, NOT choked by 0.30 HIGH_VOL giveback!)
    # Major 55-lot institutional wall at 4030.50 (> 40 lots and >= 1.0R from entry) caps TP cleanly!
    pm._state["702"] = {
        "initial_sl": 4012.80,
        "high_water_pts": 9.00,
        "be_done": False,
        "partial_done": False,
        "adopted_ts": now - 300.0,
        "last_modify_ts": 0.0,
        "is_a2_burst": False,
    }
    pos = SimpleNamespace(
        ticket=702,
        symbol="XAUUSD",
        type=0,
        price_open=4020.00,
        sl=4012.80,
        tp=4032.60,
        volume=0.01,
        profit=9.00,
        comment="gold-bot M3 wave",
    )
    snap = SimpleNamespace(
        price=4029.00,
        bid=4028.90,
        ask=4029.10,
        indicators=SimpleNamespace(atr_14=3.60),
        volatility=SimpleNamespace(atr=3.60, volatility_rank=0.85),
        order_flow=SimpleNamespace(delta=22.0, buying_pressure=66.0, cvd=80.0),
        volume_profile=SimpleNamespace(
            vwap=4021.00, vwap_zscore=0.9, poc=4021.00, value_area_high=4028.0, value_area_low=4018.0
        ),
        regime="TREND",
        signal_direction="BUY",
        signal_strength=65.0,
        divergence=0.0,
        news=SimpleNamespace(news_state="QUIET", impact_level="LOW", minutes_to_next_event=999),
        signal_map={
            "above": [{"price": 4030.50, "size": 55.0}],
            "below": [],
        },
        order_blocks=[],
        footprint=None,
        htf_poc={},
        macro_bias=0.0,
    )
    tick = SimpleNamespace(bid=4028.90, ask=4029.10)
    info = SimpleNamespace(point=0.01, trade_stops_level=5, digits=2)

    pm._manage_one(pos, snap, tick, info)
    assert len(mods) == 1
    new_sl, new_tp, rule, reason = mods[0]
    assert abs(new_sl - 4023.60) <= 0.15, f"Expected 1.5x ATR Chandelier ~4023.60, got {new_sl} ({rule}: {reason})"
    assert new_tp < 4030.50, f"Expected >=40-lot major wall to front-run TP < 4030.50, got {new_tp}"


def test_stage4b_escort_preserves_wave_target_and_uses_15x_chandelier(monkeypatch):
    monkeypatch.setattr(config, "STAGE4B_WAVE_TRAIL_ENABLED", True)
    monkeypatch.setattr(config, "ESCORT_WAVE_LOCK_TRIGGER_R", 1.0)
    monkeypatch.setattr(config, "ESCORT_WAVE_LOCK_GIVEBACK_ATR", 1.50)
    monkeypatch.setattr(config, "PM_WAVE_MAJOR_WALL_TP_LOTS", 40.0)

    plan = {
        "shot": "GO",
        "side": "BUY",
        "entry": 4020.00,
        "stop": 4012.80,
        "target": 4032.60,
        "entry_style": "AGGRESSIVE_MARKET",
        "entry_filled": True,
        "execution_status": "PAPER_ONLY",
        "paper_fill_model": "aggressive_quote_estimate",
        "door": {"price": 4022.00, "size": 12.0},
        "entry_options": {
            "AGGRESSIVE_MARKET": {"eligible": True, "target_source": "ATR_WAVE_TARGET"},
        },
    }
    t = escort_team.PaperTrade(plan, 4020.00)
    assert not t.is_a2_burst
    assert t.target_source == "ATR_WAVE_TARGET"

    # 1) Front pebble at 4022.00 swept (gone from smap) + 15-lot pebble at 4026.00 -> do NOT halve or cap target!
    j1_pebble = escort_team._job1_exit_clear(
        t, {"above": [{"price": 4026.00, "size": 15.0, "resistance": 0.65}]}, 4023.00, config=config
    )
    assert j1_pebble is None

    # 2) Major 55-lot institutional wall at 4030.00 -> DOES cap target cleanly!
    j1_major = escort_team._job1_exit_clear(
        t, {"above": [{"price": 4030.00, "size": 55.0, "resistance": 0.72}]}, 4023.00, config=config
    )
    assert j1_major is not None and j1_major["new_target"] == 4030.00

    # 3) Near-touch $0.30 shelter quote (< 1.2x ATR) ignored by _job4_move_stop on wave trades
    fake_book = SimpleNamespace(shelter_behind=lambda px, is_b: {"price": px - 0.30, "size": 14.0})
    assert escort_team._job4_move_stop(t, fake_book, 4028.00, atr=3.60, spread=0.25, config=config) is None

    # 4) _profit_lock uses 1.50x ATR Chandelier giveback (peak 4029.00 - 1.5*3.60 = 4023.60)
    t.peak = 4029.00
    pl = escort_team._profit_lock(t, 4028.50, atr=3.60, spread=0.25, config=config)
    assert pl is not None
    assert abs(pl["new_stop"] - 4023.60) < 1e-6


def test_stage4b_walk_bracket_rides_shakeout_that_05x_c3_chokes():
    # Entry BUY @ 4020.00, SL=4012.80 (7.20), TP=4032.60 (12.60), ATR=3.60
    # Bar 1 surges to +8.00 (4028.00 = 1.11R)
    # Bar 2 pulls back -3.20 pts from peak to 4024.80 (normal 0.89x ATR SHAKE->W breath!)
    # Bar 3 rallies to 4033.00 -> full +12.10 net TP!
    bar_keys = [60, 120, 180]
    bars = [
        (60, 4020.0, 4028.0, 4019.8, 4027.5),
        (120, 4027.5, 4027.8, 4024.8, 4026.5),
        (180, 4026.5, 4033.2, 4026.2, 4033.0),
    ]
    out_c3, pts_c3 = _walk_bracket(
        bar_keys, bars, 10.0, "BUY", 4020.0, 4012.8, 4032.6, 45, 0.50,
        trail_atr=3.60, trail_dist_mult=0.50, trail_trigger_r=0.50,
    )
    out_4b, pts_4b = _walk_bracket(
        bar_keys, bars, 10.0, "BUY", 4020.0, 4012.8, 4032.6, 45, 0.50,
        trail_atr=3.60, trail_dist_mult=1.50, trail_trigger_r=1.00, be_floor_atr=0.10,
    )
    assert out_c3 == "TRAIL" and pts_c3 == 5.70   # choked at 4028.0 - 1.8 = 4026.2!
    assert out_4b == "TP" and pts_4b == 12.10     # rides full wave to 4032.60!
