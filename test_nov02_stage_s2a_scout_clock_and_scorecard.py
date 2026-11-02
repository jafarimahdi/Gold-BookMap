"""
Stage S2a + [8e] Scout Raw-Data Scorecard Regression Tests (2026-11-02):
  1. `signal_team.LevelBook.ingest` / `add_history` / `build_signal_map` accept
     deterministic `now` (`datetime` or epoch `float`), preventing fast-replay CPU
     wall-clock `burn_rate` inflation (false `"melting-fast"` tags) and enabling
     deterministic `600s` spoof rehabilitation without wall-clock `time.sleep`.
  2. `>4h` (`SESSION_GAP_RESET_SEC`) overnight/weekend session gaps and `<-60s`
     tape rewinds flush stale non-protected walls while preserving `book.protect(...)`
     open-trade doors.
  3. `step2_market_analysis` passes snapshot `now` through both `analyze_market`
     (`:00`) and `_a1_refresh_scout` (`:01/:02`) and preserves `banned_spoof`,
     `atr_raw`, and `atr_floored` across `_hist` re-renders.
  4. `tools/raw_replay.py` builds `[8e] SCOUT TEAM RAW-DATA SCORECARD` with
     `Stage-S1 Parity & Coverage` (`Bell(:00)` vs `Serve(:01/:02)`), `Door Reach & Hold`,
     `Actionable Signal Corridors`, `Burn-Rate & Attack Health`, and `road_vacuum`.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import config
import signal_team as st
import step2_market_analysis as s2
from test_oct31_law8_raw_replay import _write_synthetic_raw_day
from tools import raw_replay as rr


def _mk_l3_book(wall_px: float = 4010.0, wall_sz: float = 25.0, spoof_px: float | None = None):
    bids = [(3999.5 - 0.5 * i, 4.0) for i in range(8)]
    asks = [
        (4000.5 + 0.5 * i, 4.0)
        for i in range(18)
        if abs((4000.5 + 0.5 * i) - wall_px) > 1e-9
    ]
    asks.append((wall_px, wall_sz))
    asks.sort(key=lambda r: r[0])
    spoof = {spoof_px: 3} if spoof_px is not None else {}
    return SimpleNamespace(
        order_book={"bids": bids, "asks": asks},
        spoof_levels=spoof,
        iceberg_levels={},
        iceberg_meta={},
        large_order_events=0,
        order_book_imbalance=0.0,
        ofi=0.0,
    )


def test_stage_s2a_deterministic_now_prevents_fast_replay_melting_fast_and_rehabs_spoof():
    book = st.LevelBook()
    cfg = SimpleNamespace(
        L3_WHALE_THRESHOLD=10.0,
        SCOUT_ATR_FLOOR_PCT=0.0004,
        SCOUT_MIN_SPREADS=10.0,
        SCOUT_MIN_TARGET_USD=0.0,
        ICEBERG_MIN_REFILLS=3.0,
    )
    t0 = datetime(2026, 10, 30, 10, 0, 0, tzinfo=timezone.utc)

    # Simulate 6 cycles separated by 3 market minutes each, executed in <1ms CPU time.
    # Wall shrinks gently from 25 -> 24 lots over 15 market minutes (0.067 lots/min = solid).
    # Without deterministic `now`, CPU dt ~0.0002 min inflated burn_rate > 1000 lots/min ("melting-fast").
    smap = {}
    for idx in range(6):
        sz = 25.0 - 0.2 * idx
        smap = book.ingest(
            _mk_l3_book(wall_px=4006.0, wall_sz=sz),
            price=4000.0,
            atr=4.0,
            config=cfg,
            spread=0.20,
            now=t0 + timedelta(minutes=3 * idx),
        )

    best_above = smap["best_above"]
    assert best_above is not None
    assert abs(best_above["price"] - 4006.0) < 1e-6
    assert best_above["burn_word"] == "solid"
    assert best_above["burn"] < 0.20
    assert abs(best_above["age_sec"] - 900.0) < 1e-3

    # Spoof ban at t=+18m, then deterministic rehab at t=+29m (>=600s clean + >=3 cycles).
    smap_sp = book.ingest(
        _mk_l3_book(wall_px=4006.0, wall_sz=24.0, spoof_px=4006.0),
        price=4000.0,
        atr=4.0,
        config=cfg,
        spread=0.20,
        now=t0 + timedelta(minutes=18),
    )
    assert smap_sp["banned_spoof"] == 1
    assert book.levels[4006.0].spoofed is True

    # 5 minutes later (<600s): still spoof-banned
    book.ingest(
        _mk_l3_book(wall_px=4006.0, wall_sz=24.0),
        price=4000.0,
        atr=4.0,
        config=cfg,
        spread=0.20,
        now=t0 + timedelta(minutes=23),
    )
    assert book.levels[4006.0].spoofed is True

    # 11 minutes after spoof (>=600s): rehabilitated deterministically without time.sleep!
    smap_rehab = book.ingest(
        _mk_l3_book(wall_px=4006.0, wall_sz=24.0),
        price=4000.0,
        atr=4.0,
        config=cfg,
        spread=0.20,
        now=t0 + timedelta(minutes=29),
    )
    assert book.levels[4006.0].spoofed is False
    assert smap_rehab["best_above"] is not None
    assert abs(smap_rehab["best_above"]["price"] - 4006.0) < 1e-6


def test_stage_s2a_session_gap_resets_stale_doors_except_protected():
    book = st.LevelBook()
    cfg = SimpleNamespace(
        L3_WHALE_THRESHOLD=10.0,
        SCOUT_ATR_FLOOR_PCT=0.0004,
        SCOUT_MIN_SPREADS=10.0,
        SCOUT_MIN_TARGET_USD=0.0,
        ICEBERG_MIN_REFILLS=3.0,
    )
    t_fri = datetime(2026, 10, 30, 20, 0, 0, tzinfo=timezone.utc)
    book.ingest(_mk_l3_book(wall_px=4006.0, wall_sz=25.0), 4000.0, 4.0, cfg, spread=0.2, now=t_fri)
    book.add_history(4000.0, 4.0, [{"price": 3995.0, "kind": "poc_day"}], cfg, now=t_fri)
    book.protect([3995.0])
    assert 4006.0 in book.levels and 3995.0 in book.levels

    # Jump +6 hours (>4h session gap) with a new wall at 4008.0: stale 4006.0 is flushed, protected 3995.0 stays.
    t_mon = t_fri + timedelta(hours=6)
    book.ingest(_mk_l3_book(wall_px=4008.0, wall_sz=22.0), 4000.0, 4.0, cfg, spread=0.2, now=t_mon)
    assert book.session_resets == 1
    assert 4006.0 not in book.levels
    assert 3995.0 in book.levels
    assert 4008.0 in book.levels


def test_stage_s2a_raw_replay_renders_section_8e_scout_scorecard(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "TIMEFRAME", "M3", raising=False)
    monkeypatch.setattr(config, "BASE_TF_MINUTES", 3, raising=False)
    monkeypatch.setattr(config, "A1_BASE_TF_MINUTES", 3, raising=False)
    monkeypatch.setattr(s2, "A1_BASE_TF_MINUTES", 3, raising=False)

    data_dir = tmp_path / "data"
    day_str = "2026-10-29"
    _write_synthetic_raw_day(data_dir, day_str, start_px=4000.0, drift_per_min=0.20, minutes=95)

    res = rr.replay_single_day(
        day_str=day_str,
        root_dir=tmp_path,
        data_dir=data_dir,
        every_s=60,
        window_str="10:00-12:00",
    )
    assert res["ok"] is True
    assert len(res["cycles"]) > 10
    assert any(c.get("scout", {}).get("is_serve") is True for c in res["cycles"])
    assert any(c.get("scout", {}).get("is_serve") is False for c in res["cycles"])

    txt_rep, _html_rep, _meta = rr.synthesize_multi_day([res])
    assert "[8e] SCOUT TEAM RAW-DATA SCORECARD (+12m = 4x M3 FORWARD TAPE" in txt_rep
    assert "1) Stage-S1 Parity & Coverage: Bell(:00)=" in txt_rep
    assert "Creator L3 Handoff (Step-S2b Check):" in txt_rep
    assert "2) Door Reach & Hold (+12m):" in txt_rep
    assert "3) Actionable Signal Corridors" in txt_rep
    assert "Burn-Rate & Attack Health (Step-S2a/S2c Check):" in txt_rep
    assert "4) road_vacuum Corridor Travel:" in txt_rep
