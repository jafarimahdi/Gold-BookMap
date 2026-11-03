"""Stage S2c unit & regression tests (`2026-11-03`):
1) Touch-bounded + rolling/decaying `lv.attacked` (`<= 12`, never blaming $4-$8 distant walls up to `194`).
2) Microstructure `Resistance` calibration (`>= 0.55` separates real institutional/confluent/iceberg walls
   and passive touch defense from routine 12-lot quotes and melting walls).
3) `config.L3_WHALE_THRESHOLD` fallback alignment (`10.0` CME GC MBO lots) + fresh-boot PM warning guard.
"""
from types import SimpleNamespace
import logging

import config
import position_manager
import signal_team as st


def _mk_l3_with_wall(wall_px: float, wall_size: float, side: str = "ask", mid: float = 4000.0):
    bids = [(round(mid - 0.10 * i, 2), 3.0) for i in range(1, 15)]
    asks = [(round(mid + 0.10 * i, 2), 3.0) for i in range(1, 15)]
    if side == "ask":
        asks.append((wall_px, wall_size))
    else:
        bids.append((wall_px, wall_size))
    return SimpleNamespace(
        order_book={"bids": bids, "asks": asks},
        spoof_levels={},
        iceberg_levels={},
        iceberg_meta={},
    )


def test_stage_s2c_distant_and_memory_walls_ignore_touch_absorption_and_touch_decays():
    book = st.LevelBook()
    cfg = SimpleNamespace(L3_WHALE_THRESHOLD=10.0)
    # Seed a 0-lot memory mark right near 4000.1 so we verify 0-lot memory marks never accumulate attacked
    mem_lv = st.Level(4000.1, "ask", 0.0, 1_699_999_940.0)
    mem_lv.sources = ["poc_day"]
    book.levels[4000.1] = mem_lv

    # Cycle 1: distant ask wall at 4005.0 ($5.00 away from mid 4000.0, atr=3.2) + heavy touch ask_absorbed
    l3_far = _mk_l3_with_wall(4005.0, 28.0, side="ask", mid=4000.0)
    of_heavy = SimpleNamespace(bid_absorbed=0, ask_absorbed=9, absorption_net=-9)
    out1 = book.ingest(
        level3=l3_far, price=4000.0, atr=3.2, config=cfg,
        order_flow=of_heavy, spread=0.20, now=1_700_000_000.0,
    )
    assert out1["best_above"] is not None
    assert out1["best_above"]["price"] == 4005.0
    # Distant wall $5.00 away was NOT touched by absorption at 4000.0!
    assert out1["best_above"]["attacked"] == 0
    # 0-lot memory mark at 4000.1 (POC) also must NOT accumulate attacked count
    assert book.levels[4000.1].attacked == 0

    # Cycle 2..4: price rallies up to 4004.6 (within touch_band of 4005.0) under rolling ask_absorbed=9
    l3_touch = _mk_l3_with_wall(4005.0, 28.0, side="ask", mid=4004.6)
    for step_i in range(3):
        out_touch = book.ingest(
            level3=l3_touch, price=4004.6, atr=3.2, config=cfg,
            order_flow=of_heavy, spread=0.20, now=1_700_000_060.0 + step_i * 60.0,
        )
    # Bounded rolling pressure (never cumulative 9+9+9=27 -> 194!)
    assert book.levels[4005.0].attacked == 9
    assert book.levels[4005.0].attacked <= 12

    # Cycle 5: absorption stops (ask_absorbed=0) -> decays by 1 per cycle
    of_calm = SimpleNamespace(bid_absorbed=0, ask_absorbed=0, absorption_net=0)
    book.ingest(
        level3=l3_touch, price=4004.4, atr=3.2, config=cfg,
        order_flow=of_calm, spread=0.20, now=1_700_000_300.0,
    )
    assert book.levels[4005.0].attacked == 8


def test_stage_s2c_resistance_separates_institutional_and_passive_defense_from_routine_and_melting():
    book = st.LevelBook()

    # 1) Routine 12-lot MM quote (outlier 2.8x, close 0.4 ATR, no structural memory) -> < 0.55
    lv_mm = st.Level(4001.3, "ask", 12.0, 1000.0)
    lv_mm.cycles_seen = 2
    lv_mm.outlier_x = 2.8
    lv_mm.sources = ["dom_wall"]
    sc_mm = book._score(lv_mm, price=4000.0, atr=3.3, whale_thr=10.0)
    assert sc_mm["resistance"] < 0.40

    # 2) 35-lot institutional outlier wall (4.8x median, big_player) -> >= 0.55
    lv_inst = st.Level(4003.6, "ask", 35.0, 1360.0)
    lv_inst.size_first = 33.0
    lv_inst.samples = [(1000.0, 33.0), (1360.0, 35.0)]
    lv_inst.cycles_seen = 6
    lv_inst.outlier_x = 4.8
    lv_inst.big_player = True
    lv_inst.sources = ["dom_wall"]
    sc_inst = book._score(lv_inst, price=4000.0, atr=3.3, whale_thr=10.0)
    assert sc_inst["resistance"] >= 0.60

    # 3) 22-lot POC confluent wall & 22-lot active iceberg wall -> both >= 0.55
    lv_poc = st.Level(4003.0, "ask", 22.0, 1300.0)
    lv_poc.cycles_seen = 5
    lv_poc.outlier_x = 3.6
    lv_poc.sources = ["dom_wall", "poc_day"]
    assert book._score(lv_poc, price=4000.0, atr=3.3, whale_thr=10.0)["resistance"] >= 0.55

    lv_ice = st.Level(4002.3, "ask", 22.0, 1240.0)
    lv_ice.cycles_seen = 4
    lv_ice.outlier_x = 3.4
    lv_ice.sources = ["dom_wall", "iceberg"]
    assert book._score(lv_ice, price=4000.0, atr=3.3, whale_thr=10.0)["resistance"] >= 0.55

    # 4) Passive touch defense (solid wall absorbing flow) > unattacked baseline,
    #    whereas melting under attack collapses well below 0.55 even with POC confluence!
    lv_def = st.Level(4000.3, "ask", 30.0, 1300.0)
    lv_def.size_first = 29.0
    lv_def.samples = [(1000.0, 29.0), (1300.0, 30.0)]
    lv_def.cycles_seen = 5
    lv_def.outlier_x = 4.2
    lv_def.big_player = True
    lv_def.sources = ["dom_wall"]
    res_unattacked = book._score(lv_def, price=4000.0, atr=3.3, whale_thr=10.0)["resistance"]
    lv_def.attacked = 5
    res_defending = book._score(lv_def, price=4000.0, atr=3.3, whale_thr=10.0)["resistance"]
    assert res_defending > res_unattacked >= 0.55

    lv_melt = st.Level(4000.3, "ask", 22.0, 1120.0)
    lv_melt.size_first = 35.0
    lv_melt.samples = [(1000.0, 35.0), (1120.0, 22.0)]
    lv_melt.cycles_seen = 6
    lv_melt.outlier_x = 4.8
    lv_melt.big_player = True
    lv_melt.attacked = 6
    lv_melt.sources = ["dom_wall", "poc_day"]
    sc_melt = book._score(lv_melt, price=4000.0, atr=3.3, whale_thr=10.0)
    assert lv_melt.burn_word in ("melting", "melting-fast")
    assert sc_melt["resistance"] < 0.35


def test_stage_s2c_whale_threshold_default_and_fresh_boot_pm_warning(monkeypatch, caplog):
    monkeypatch.delenv("L3_WHALE_THRESHOLD", raising=False)
    config.reload_env()
    assert config.L3_WHALE_THRESHOLD == 10.0

    logging.getLogger("position_manager").propagate = True
    pm = position_manager.PositionManager.__new__(position_manager.PositionManager)
    pm._mt5 = SimpleNamespace()
    pm._state = {}
    pm._mt5_init_warn_ts = 0.0
    monkeypatch.setattr(config, "PM_ENABLE", True)
    monkeypatch.setattr(config, "TRADING_ENABLED", True)
    monkeypatch.setattr(config, "mt5_initialize", lambda _m: False)
    import time as _t
    monkeypatch.setattr(_t, "monotonic", lambda: 12.0)  # simulate fresh OS boot (<300s uptime)

    with caplog.at_level(logging.WARNING):
        pm.manage(SimpleNamespace())
    assert any(r.levelname == "WARNING" and "mt5.initialize() failed" in r.message for r in caplog.records)
