"""
Verification tests for the Pre-Stage-2 Retired, Dormant & Previous Judges Audit (2026-10-31):
  1. Smoking-gun parser fix: `vwap_zscore` mean-reversion fade (`+z -> SELL -1.0`,
     `-z -> BUY +1.0`, adaptive weight extracted) in BOTH `judge_panel.py` and
     `step2_market_analysis.py`, while preserving `whale_walls` / `htf_poc` skip contracts.
  2. Unit verification of `_evaluate_retired_and_previous_bench` across all 4 bug-repaired
     candidates (`supply_demand[fix]` unmitigated M5 zones, `whale_walls[fix]` live-book-only
     ignoring `BID_CANCEL` & `spoof_levels`, `htf_poc[fix]` equilibrium silence + RANGE fade +
     TREND migration, `queue_pos[ck2017]`) plus retired, dormant (`_ghost_on`), and previous
     (`v5.8.1` / `U-16` `session_ib_on` / `asian_range_brk` / `rsi_range_fade` / `round_numbers`)
     bench judges.
  3. End-to-end `tools/raw_replay.py` verification: Section `[7]` Gross vs Net columns,
     Section `[7b]` Non-Voting Bench Scorecard, zero leakage into active `judge_stats` or
     `SignalEngine` votes, and `--workers 2 == --workers 1` determinism.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import config
import judge_panel as jp
import step2_market_analysis as s2
from test_oct31_law8_raw_replay import _write_synthetic_raw_day
from tools import raw_replay as rr


def test_vwap_zscore_parser_sign_and_adaptive_weight_fix():
    """Both copies of parse_judge_panel must grade +z fade as SELL (-1.0) and -z fade as BUY (+1.0)."""
    notes_pos_z = [
        "VWAP z-score 2.1 -> mean reversion fade (adaptive 0.40)",
        "Whale wall BID 45 lots -> ignored by parser contract",
        "HTF POC H1 4000.0 -> ignored by parser contract",
    ]
    notes_neg_z = [
        "VWAP z-score -2.3 -> mean reversion fade (adaptive 0.80)",
    ]

    for fn in (jp.parse_judge_panel, s2.parse_judge_panel):
        res_pos = fn(notes_pos_z)
        vz_pos = [r for r in res_pos if r["judge"] == "vwap_zscore"]
        assert len(vz_pos) == 1
        assert vz_pos[0]["dir"] == -1.0, f"{fn.__module__} graded +2.1 z-score fade backwards!"
        assert vz_pos[0]["weight"] == pytest.approx(0.40)
        # Exclusion contract from test_oct29_evening_fixes.py must stay intact
        names = {r["judge"] for r in res_pos}
        assert "whale_walls" not in names
        assert "htf_poc" not in names

        res_neg = fn(notes_neg_z)
        vz_neg = [r for r in res_neg if r["judge"] == "vwap_zscore"]
        assert len(vz_neg) == 1
        assert vz_neg[0]["dir"] == 1.0, f"{fn.__module__} graded -2.3 z-score fade backwards!"
        assert vz_neg[0]["weight"] == pytest.approx(0.80)


def test_whale_walls_fix_ignores_bid_cancel_and_spoof_levels():
    """Legacy whale_walls counts `BID_CANCEL` as bullish support; whale_walls[fix] rejects cancels & spoofs."""
    snap_fake = SimpleNamespace(
        price=4000.0,
        regime="RANGE",
        volatility=SimpleNamespace(atr=3.0),
        volume_profile=SimpleNamespace(vwap_zscore=0.0, poc=0.0),
        nearest_support=0.0,
        nearest_resistance=0.0,
        order_blocks=[],
        htf_poc={},
        order_flow=SimpleNamespace(
            mid_price=4000.0, microprice=4000.0, bid_ask_ratio=1.0, delta=0.0,
            buying_pressure=50.0, selling_pressure=50.0,
            level2_bid_depth=20.0, level2_ask_depth=20.0, ofi=0.0, depth_imbalance=0.0,
        ),
        level3=SimpleNamespace(
            # Spoofed bid resting in book + a giant BID_CANCEL in order_events
            order_book={"bids": [(3999.0, 45.0)], "asks": []},
            order_events=[{"type": "BID_CANCEL", "price": 3998.0, "size": 60.0}],
            spoof_levels=[{"side": "BID", "price": 3999.0}],
            spoof_events=1,
            iceberg_levels=[],
            iceberg_events=0,
            order_book_imbalance=0.0,
            buy_streak=0,
            sell_streak=0,
        ),
        trend=SimpleNamespace(macd_histogram=0.0, sma_20=0.0, trend_direction="NEUTRAL", rsi=50.0),
        macro=None,
    )
    tc_dt = datetime(2026, 10, 29, 14, 0, tzinfo=timezone.utc)
    res_fake = dict(
        (name, d)
        for name, _cat, _clk, d in rr._evaluate_retired_and_previous_bench(snap_fake, {}, tc_dt, {}, {})
    )
    assert res_fake.get("whale_walls") == 1.0
    assert "whale_walls[fix]" not in res_fake

    # Clean real resting bid wall within 2*ATR (no spoof) -> fires +1.0 in RANGE, blocked in TREND DOWN
    snap_fake.level3.spoof_levels = []
    snap_fake.level3.spoof_events = 0
    res_real_range = dict(
        (name, d)
        for name, _cat, _clk, d in rr._evaluate_retired_and_previous_bench(snap_fake, {}, tc_dt, {}, {})
    )
    assert res_real_range.get("whale_walls[fix]") == 1.0

    snap_fake.regime = "TREND"
    snap_fake.trend.trend_direction = "DOWN"
    res_counter_trend = dict(
        (name, d)
        for name, _cat, _clk, d in rr._evaluate_retired_and_previous_bench(snap_fake, {}, tc_dt, {}, {})
    )
    assert "whale_walls[fix]" not in res_counter_trend


def test_htf_poc_fix_and_unmitigated_supply_demand_fix():
    """htf_poc[fix] fixes <=1 ATR magnet inversion; supply_demand[fix] prunes mitigated zones."""
    tc_dt = datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc)

    # 1) Inside 0.3 ATR of H1 POC: legacy htf_poc pushes away (+1), htf_poc[fix] stays silent
    snap = SimpleNamespace(
        price=4001.0,
        regime="RANGE",
        volatility=SimpleNamespace(atr=3.0),
        volume_profile=SimpleNamespace(vwap_zscore=2.0, poc=4000.0),
        nearest_support=0.0,
        nearest_resistance=0.0,
        order_blocks=[],
        htf_poc={"H1": 4000.0, "H4": 4000.0},
        order_flow=SimpleNamespace(
            mid_price=4001.0, microprice=4001.15, bid_ask_ratio=1.6, delta=25.0,
            buying_pressure=68.0, selling_pressure=32.0,
            level2_bid_depth=30.0, level2_ask_depth=15.0, ofi=12.0, depth_imbalance=0.33,
        ),
        level3=None,
        trend=SimpleNamespace(macd_histogram=0.15, sma_20=3998.0, trend_direction="UP", rsi=24.0),
        macro=None,
    )
    votes_near = dict(
        (n, d) for n, _c, _k, d in rr._evaluate_retired_and_previous_bench(
            snap, {}, tc_dt, {"hi": 4000.5, "lo": 3992.0}, {"ibh": 4001.2, "ibl": 3994.0, "ib_complete": True}
        )
    )
    assert votes_near["vwap_zscore"] == -1.0
    assert votes_near["htf_poc"] == 1.0
    assert "htf_poc[fix]" not in votes_near
    assert votes_near["microprice"] == 1.0
    assert votes_near["queue_pos[ck2017]"] == 1.0
    assert votes_near["delta_pressure"] == 1.0
    assert votes_near["rsi_range_fade"] == 1.0
    assert votes_near["asian_range_brk"] == 1.0
    assert votes_near["session_ib_on"] == -1.0  # within 0.6 ATR below IBH in RANGE -> fade IBH
    assert votes_near["round_numbers"] == 1.0   # 4001.0 is +1.0 above 4000 ($50/$100 level) <= 1.2 (0.4*ATR)

    # 2) Stretched +2.5 ATR above H1 POC in RANGE -> htf_poc[fix] fades (-1.0)
    snap.price = 4007.5
    votes_stretch = dict(
        (n, d) for n, _c, _k, d in rr._evaluate_retired_and_previous_bench(snap, {}, tc_dt, {}, {})
    )
    assert votes_stretch["htf_poc[fix]"] == -1.0

    # 3) Supply/Demand M5 unmitigated vs mitigated check
    # Build 50 M1 candles (= 10 M5 bars):
    # M5 bar 2 (M1 10..14) dips to 3990.0 (pivot low) with body top 3991.0, then M5 bars 3..5 rally to 4002.0!
    opens, highs, lows, closes, vols = [], [], [], [], []
    m5_plan = [
        (3996.0, 3997.0, 3995.0, 3996.0),  # 0
        (3995.0, 3996.0, 3993.0, 3993.5),  # 1
        (3991.0, 3992.0, 3990.0, 3991.2),  # 2 -> demand pivot [3990.0 .. 3991.0]
        (3991.5, 3998.0, 3991.2, 3997.5),  # 3 -> strong impulse departure!
        (3997.5, 4002.0, 3997.0, 4001.0),  # 4
        (4001.0, 4001.5, 3998.0, 3998.5),  # 5
        (3998.5, 3999.0, 3995.0, 3995.5),  # 6
        (3995.5, 3996.0, 3993.0, 3993.5),  # 7
        (3993.5, 3994.0, 3992.0, 3992.5),  # 8
        (3992.5, 3993.0, 3991.5, 3992.0),  # 9 (current bar retesting 3992.0 above unmitigated 3991.0!)
    ]
    for o, h, l, c in m5_plan:
        for sub in range(5):
            opens.append(o if sub == 0 else (o + c) / 2.0)
            highs.append(h)
            lows.append(l)
            closes.append(c if sub == 4 else (o + c) / 2.0)
            vols.append(20.0)
    md_unmit = {"candles": {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols}}
    snap.price = 3992.0
    snap.regime = "RANGE"
    v_unmit = dict(
        (n, d) for n, _c, _k, d in rr._evaluate_retired_and_previous_bench(snap, md_unmit, tc_dt, {}, {})
    )
    assert v_unmit.get("supply_demand[fix]") == 1.0

    # Now mitigate that demand zone on closed M5 bar 7 (close at 3988.0 < 3990.0 bottom) -> zone must be pruned!
    closes_mit = list(closes)
    lows_mit = list(lows)
    lows_mit[7 * 5 + 4] = 3987.5
    closes_mit[7 * 5 + 4] = 3988.0
    md_mit = {"candles": {"open": opens, "high": highs, "low": lows_mit, "close": closes_mit, "volume": vols}}
    v_mit = dict(
        (n, d) for n, _c, _k, d in rr._evaluate_retired_and_previous_bench(snap, md_mit, tc_dt, {}, {})
    )
    assert "supply_demand[fix]" not in v_mit


def test_raw_replay_bench_section_7b_and_worker_parity(tmp_path, monkeypatch):
    """raw_replay outputs Section [7] Gross/Net + Section [7b] Bench Checkup with zero active contamination."""
    monkeypatch.setattr(config, "reload_env", lambda *a, **k: None)
    monkeypatch.setattr(config, "V6_4TEAMS_ENABLED", False)
    monkeypatch.setattr(config, "AI_CONFIDENCE_THRESHOLD", 35.0)
    monkeypatch.setenv("CONFIDENCE_THRESHOLD", "35")

    proj = tmp_path / "bench_proj"
    data_dir = proj / "data"
    _write_synthetic_raw_day(data_dir, "2026-10-28", start_px=3990.0, drift_per_min=0.25, minutes=130)
    _write_synthetic_raw_day(data_dir, "2026-10-29", start_px=4015.0, drift_per_min=-0.15, minutes=130)

    r_seq = [
        rr.replay_single_day("2026-10-28", root_dir=proj, data_dir=data_dir, every_s=180),
        rr.replay_single_day("2026-10-29", root_dir=proj, data_dir=data_dir, every_s=180),
    ]
    txt_seq, _, _ = rr.synthesize_multi_day(r_seq)

    rc = rr.main([
        "--dates", "2026-10-28,2026-10-29", "--every", "180", "--workers", "2",
        "--root-dir", str(proj), "--data-dir", str(data_dir), "--no-browser",
    ])
    assert rc == 0

    txt_out = (data_dir / "current_version_replay.txt").read_text(encoding="utf-8")
    assert "[7] CURRENT ACTIVE JUDGES SCORECARD" in txt_out
    assert "GROSS_PTS" in txt_out
    assert "[7b] RETIRED, PREVIOUS & REPAIRED BENCH JUDGES -- FAIR-TRIAL CHECKUP" in txt_out

    # Active judge_stats must remain 100% free of retired/bench names
    retired_lower = {j.lower() for j in config.RETIRED_JUDGES}
    for r in r_seq:
        assert len(r["bench_judge_stats"]) > 0
        for j in r["judge_stats"]:
            assert j.lower() not in retired_lower

    # Compare all analytical sections from [2] onward (ignoring wall-clock timestamp & SEC column in [1])
    assert txt_out.rstrip().split("[2] GATE WATERFALL")[1] == txt_seq.rstrip().split("[2] GATE WATERFALL")[1]


def test_wall_clock_resampler_and_weekend_monday_knowledge(tmp_path):
    """True wall-clock H1/M15/M5/M3 resampler skips incomplete newborn bars & discovers Friday->Monday ticks."""
    b = rr._IncrementalM1Builder(max_bars=1800)
    t0 = datetime(2026, 10, 26, 8, 0, 0, tzinfo=timezone.utc).timestamp()
    # Add 3 closed M5 buckets (08:00..08:15) even with a missing minute at 08:03, plus 1m of incomplete 08:15..08:16
    for m in range(16):
        if m == 3:
            continue
        b.add_trade(t0 + m * 60 + 10, 4000.0 + m * 0.5, 10.0)

    # At 08:16:00, bucket 08:15..08:20 is still forming -> only 3 closed M5 buckets (08:00, 08:05, 08:10) emitted!
    r5 = b.to_resampled_dict(5, completed_before_ep=t0 + 16 * 60)
    assert len(r5["close"]) == 3
    assert float(r5["volume"][0]) == 40.0  # 4 active minutes in 08:00..08:05 despite missing 08:03!
    assert float(r5["volume"][1]) == 50.0

    # Friday (2026-10-23) ticks file must be discovered when replaying Monday (2026-10-26), while MBO stays 1-day
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    fri_ticks = data_dir / "ticks_20261023_180000.csv"
    fri_mbo = data_dir / "mbo_20261023_180000.csv"
    mon_ticks = data_dir / "ticks_20261026_080000.csv"
    fri_ticks.write_text("ts,type,px,sz\n", encoding="utf-8", newline="\n")
    fri_mbo.write_text("ts,ev,oid,px,sz,inst\n", encoding="utf-8", newline="\n")
    mon_ticks.write_text("ts,type,px,sz\n", encoding="utf-8", newline="\n")

    disc_t = [p.name for p in rr._discover_raw_files("ticks", tmp_path, data_dir, day_str="2026-10-26")]
    disc_m = [p.name for p in rr._discover_raw_files("mbo", tmp_path, data_dir, day_str="2026-10-26")]
    assert "ticks_20261023_180000.csv" in disc_t
    assert "ticks_20261026_080000.csv" in disc_t
    assert "mbo_20261023_180000.csv" not in disc_m


def test_complete_census_stage2_fix_and_power_bench_judges():
    """Verify Stage-2 ACT_FIX shadow twins, POWER instruments, and historical db0bf52 judges fire cleanly."""
    tc_dt = datetime(2026, 10, 28, 10, 30, 0, tzinfo=timezone.utc)
    tc_ep = tc_dt.timestamp()

    # Build 24 closed M3 bars ending in a bullish liquidity sweep + high-RVOL strong-CLV reversal bar!
    o3 = [4000.0] * 24
    h3 = [4001.5] * 24
    l3_b = [3999.0] * 24
    c3 = [4000.2] * 24
    v3 = [100.0] * 24
    # Bar -1 pokes 3998.0 (< 3999.0 - 0.65) on 250 vol (2.5x median) and closes at 4001.2 (CLV > 0.65)!
    o3[-1], h3[-1], l3_b[-1], c3[-1], v3[-1] = 3999.2, 4001.5, 3998.0, 4001.2, 250.0

    snap = SimpleNamespace(
        price=4001.2,
        regime="RANGE",
        mtf={"H1": "UP", "M15": "UP", "M5": "UP"},
        volatility=SimpleNamespace(atr=2.0),
        volume_profile=SimpleNamespace(vwap=4005.0, vwap_std=2.0, vwap_zscore=-1.9, poc=4000.0, value_area_high=4008.0, value_area_low=4000.8),
        order_flow=SimpleNamespace(mid_price=4001.2, microprice=4001.3, bid_ask_ratio=1.6, delta=45.0, buying_pressure=68.0, selling_pressure=32.0, level2_bid_depth=80.0, level2_ask_depth=40.0, ofi=25.0, depth_imbalance=0.33, absorption_net=3.0, cvd=120.0),
        footprint=SimpleNamespace(delta_imbalance=0.28),
        level3=SimpleNamespace(
            order_book={"bids": [(4000.5, 45.0)], "asks": [(4002.5, 10.0)]},
            order_events=[],
            spoof_levels={3995.0: 1},
            iceberg_levels={4000.8: 3},
            iceberg_meta={4000.8: {"refills": 4, "last_seen": tc_ep - 30.0, "score": 15000.0}},
            buy_streak=4, sell_streak=0, iceberg_events=2, spoof_events=0, order_book_imbalance=0.35,
        ),
        trend=SimpleNamespace(macd_histogram=0.10, sma_20=3999.5, sma_50=3998.0, trend_direction="UP", rsi=28.0),
        macro=None,
        divergence=0.55,
        nearest_support=4000.0, nearest_resistance=4010.0, order_blocks=[],
        htf_poc={"H1": 4006.0, "H4": 4005.5},
        power={
            "direction": "UP",
            "input_judges": {
                "big_prints": {"value": 0.42},
                "footprint_levels": {"value": 0.50},
            },
        },
        signal_map={},
    )
    md = {
        "candles": {"open": [4000.0, 4000.2, 4000.5], "high": [4000.5, 4000.8, 4001.5], "low": [3999.8, 4000.0, 4000.2], "close": [4000.2, 4000.5, 4001.2], "volume": [50.0, 60.0, 90.0]},
        "candles_m3": {"open": o3, "high": h3, "low": l3_b, "close": c3, "volume": v3},
        "order_events": [
            {"timestamp": tc_ep - 15.0, "price": 4000.8, "size": 30.0, "type": "BID_ADD", "side": "BID"},
        ],
    }
    sess_state = {
        "ibh": 4008.0, "ibl": 4000.5, "ib_complete": True,
        "onh": 4009.0, "onl": 3997.0,
        "prev_high": 4012.0, "prev_low": 4000.6, "prev_settlement": 4001.0,
    }
    res = {(n, cat): d for n, cat, _clk, d in rr._evaluate_retired_and_previous_bench(snap, md, tc_dt, {"hi": 4000.5, "lo": 3996.0}, sess_state)}

    # Stage-2 ACT_FIX shadow twins
    assert res[("l3_large_ofi[fix]", "ACT_FIX")] == 1.0
    assert res[("iceberg[fix]", "ACT_FIX")] == 1.0
    assert res[("volume_roc[fix]", "ACT_FIX")] == 1.0
    assert res[("sweep[fix]", "ACT_FIX")] == 1.0
    assert res[("vwap_bands[fix]", "ACT_FIX")] == 1.0
    assert res[("value_area[fix]", "ACT_FIX")] == 1.0
    assert res[("cvd_divergence[fix]", "ACT_FIX")] == 1.0
    assert res[("absorption[fix]", "ACT_FIX")] == 1.0
    assert res[("footprint_delta[fix]", "ACT_FIX")] == 1.0

    # POWER exclusive judges + historical db0bf52 / session judges
    assert res[("power_v2_dir", "POWER")] == 1.0
    assert res[("power_big_prints", "POWER")] == 1.0
    assert res[("power_breadth_sqrt", "POWER")] == 1.0
    assert res[("session_pdh_pdl", "PREVIOUS")] == 1.0
    assert res[("round_numbers_5st", "PREVIOUS")] == 1.0
    assert res[("sma50", "DORMANT")] == 1.0
    assert res[("ghost_vwap_side", "DORMANT")] == -1.0


def test_law11_master_timeframe_knob_scales_all_subsystems_m1_m3_m5_m15_h1(monkeypatch):
    """Law #11: Changing TIMEFRAME alone dynamically scales BASE_TF_MINUTES,
    A1_BASE_TF_MINUTES, POWER_BAR_SECONDS, ORDER_FLOW_WINDOW_SECONDS, and MTF_LADDER."""
    import config

    for k in ("A1_BASE_TF_MINUTES", "POWER_BAR_SECONDS", "ORDER_FLOW_WINDOW_SECONDS"):
        monkeypatch.delenv(k, raising=False)

    cases = [
        ("M1", 1, 60, [("M5", 5), ("M15", 15), ("H1", 60)]),
        ("M3", 3, 180, [("M9", 9), ("M15", 15), ("H1", 60)]),
        ("M5", 5, 300, [("M15", 15), ("H1", 60), ("H4", 240)]),
        ("M10", 10, 600, [("M30", 30), ("H1", 60), ("H4", 240)]),
        ("M15", 15, 900, [("H1", 60), ("H4", 240), ("D1", 1440)]),
        ("H1", 60, 3600, [("H4", 240), ("H12", 720), ("D1", 1440)]),
    ]
    try:
        for tf_str, exp_min, exp_sec, exp_ladder in cases:
            monkeypatch.setenv("TIMEFRAME", tf_str)
            config._refresh()
            assert config.BASE_TF_MINUTES == exp_min
            assert config.A1_BASE_TF_MINUTES == exp_min
            assert config.POWER_BAR_SECONDS == exp_sec
            assert config.ORDER_FLOW_WINDOW_SECONDS == exp_sec
            assert config.MTF_LADDER == exp_ladder
            # Every higher-TF confirmation rung must be strictly higher than active chart TF
            for _lbl, r_min in exp_ladder:
                assert r_min > exp_min
    finally:
        monkeypatch.undo()
        config._refresh()


def test_law11_step2_chart_candles_and_judge_clocks_follow_active_timeframe(monkeypatch):
    """Law #11: Step 2 chart indicators/MTF, insight judge_clock, and bench clocks
    dynamically adapt between M3, M5, and M15 without stale M1/M5 hardcoding."""
    import numpy as np
    import config
    import step2_market_analysis as s2
    from tools import insight as ins

    monkeypatch.delenv("A1_BASE_TF_MINUTES", raising=False)
    monkeypatch.setattr(s2, "A1_BASE_TF_MINUTES", 5)

    # 1) Insight judge_clock scales bar horizons (2/3/6/12/24 bars) by active timeframe
    assert ins.judge_clock("microprice", bar_seconds=180) == 6
    assert ins.judge_clock("footprint_delta", bar_seconds=180) == 9
    assert ins.judge_clock("iceberg", bar_seconds=180) == 18
    assert ins.judge_clock("vwap_bands", bar_seconds=180) == 36
    assert ins.judge_clock("htf_poc", bar_seconds=180) == 72

    assert ins.judge_clock("footprint_delta", bar_seconds=300) == 15
    assert ins.judge_clock("iceberg", bar_seconds=300) == 30
    assert ins.judge_clock("vwap_bands", bar_seconds=300) == 60
    assert ins.judge_clock("htf_poc", bar_seconds=300) == 120

    assert ins.judge_clock("footprint_delta", bar_seconds=900) == 45
    assert ins.judge_clock("vwap_bands", bar_seconds=900) == 180

    # 2) Bench judge clocks scale identically across M3 (tf_min=3), M5 (5), M15 (15)
    tc_dt = datetime(2026, 10, 30, 14, 0, tzinfo=timezone.utc)
    snap_b = SimpleNamespace(
        price=4001.5,
        regime="RANGE",
        volatility=SimpleNamespace(atr=2.5),
        volume_profile=SimpleNamespace(vwap_zscore=2.2, poc=4000.0),
        nearest_support=0.0,
        nearest_resistance=0.0,
        order_blocks=[],
        htf_poc={"H1": 4000.0, "H4": 4000.0},
        order_flow=SimpleNamespace(
            mid_price=4001.5, microprice=4001.7, bid_ask_ratio=1.7, delta=30.0,
            buying_pressure=68.0, selling_pressure=32.0,
            level2_bid_depth=40.0, level2_ask_depth=15.0, ofi=15.0, depth_imbalance=0.40,
        ),
        level3=None,
        trend=SimpleNamespace(macd_histogram=0.2, sma_20=3999.0, sma_50=3998.0, trend_direction="UP", rsi=25.0),
        macro=None,
    )
    clks_m3 = {n: clk for n, _c, clk, _d in rr._evaluate_retired_and_previous_bench(snap_b, {}, tc_dt, {}, {}, tf_min=3)}
    clks_m5 = {n: clk for n, _c, clk, _d in rr._evaluate_retired_and_previous_bench(snap_b, {}, tc_dt, {}, {}, tf_min=5)}
    clks_m15 = {n: clk for n, _c, clk, _d in rr._evaluate_retired_and_previous_bench(snap_b, {}, tc_dt, {}, {}, tf_min=15)}
    assert clks_m3["microprice"] == 6 and clks_m5["microprice"] == 10 and clks_m15["microprice"] == 30
    assert clks_m3["queue_pos[ck2017]"] == 9 and clks_m5["queue_pos[ck2017]"] == 15 and clks_m15["queue_pos[ck2017]"] == 45
    assert clks_m3["rsi_range_fade"] == 18 and clks_m5["rsi_range_fade"] == 30 and clks_m15["rsi_range_fade"] == 90
    assert clks_m3["vwap_zscore"] == 36 and clks_m5["vwap_zscore"] == 60 and clks_m15["vwap_zscore"] == 180
    assert clks_m3["htf_poc"] == 72 and clks_m5["htf_poc"] == 120 and clks_m15["htf_poc"] == 360

    # 3) Step 2 analyze_market dynamically switches chart bars & MTF ladder when TIMEFRAME changes
    n_m1 = 360  # 6 hours of M1 bars -> >=20 bars on M9 (40), M15 (24)
    idx = np.arange(n_m1, dtype=float)
    c_m1 = 4000.0 + 0.08 * idx
    o_m1 = c_m1 - 0.04
    h_m1 = c_m1 + 0.40
    l_m1 = o_m1 - 0.40
    v_m1 = 10.0 + (idx % 5) * 4.0
    md_base = {
        "symbol": "GCZ6",
        "price": float(c_m1[-1]),
        "bid": float(c_m1[-1] - 0.10),
        "ask": float(c_m1[-1] + 0.10),
        "candles_is_m1": True,
        "candles": {"open": o_m1, "high": h_m1, "low": l_m1, "close": c_m1, "volume": v_m1},
        "tick_data": [],
        "macro": {},
        "news": {"headlines": [], "events": [], "fetch_calendar": False},
    }
    try:
        monkeypatch.setenv("TIMEFRAME", "M3")
        config._refresh()
        s2._A1_BAR_SERVICE_CACHE.clear()
        assert s2._active_base_tf_minutes() == 3
        snap_m3 = s2.analyze_market(dict(md_base), now=tc_dt)
        assert "M9" in snap_m3.mtf_trends and "M15" in snap_m3.mtf_trends
        assert "M5" not in snap_m3.mtf_trends  # M5 never slices across M3 bars!

        monkeypatch.setenv("TIMEFRAME", "M5")
        config._refresh()
        s2._A1_BAR_SERVICE_CACHE.clear()
        assert s2._active_base_tf_minutes() == 5
        snap_m5 = s2.analyze_market(dict(md_base), now=tc_dt)
        assert "M15" in snap_m5.mtf_trends
        assert "M5" not in snap_m5.mtf_trends  # M5 never votes on itself!
        # 5-minute chart ATR is wider than 3-minute chart ATR on steady trending M1 bars
        assert snap_m5.volatility.atr > snap_m3.volatility.atr > 0.80
    finally:
        monkeypatch.undo()
        config._refresh()
        s2._A1_BAR_SERVICE_CACHE.clear()
