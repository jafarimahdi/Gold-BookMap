"""Stage E1 (Section #9): Escort Team & Paper-Entry Simulator Unit Rig (2026-11-07).

Covers:
  1. Zero-second entry grace (eliminates 2026-11-06 3/3 0.000s entry-tick suicides).
  2. Stage 4b / PM-1 wave parity in Escort (`ESCORT_MIN_AGE_SECONDS`, pre-existing
     entry divergence, `ESCORT_DIVERGENCE_CLOSE_ENABLED=False`, `1.2x ATR` stop floor).
  3. Simulated/replay clock fidelity (`now=datetime|epoch`) & Law #11 timeframe
     time-stops (`12m` A2 burst, `45m` M3 wave, `75m` M5 wave).
  4. Executable quote `mark` on `"ESCORT"` close + no-op `new_target == t.target` guard.
  5. Deferred `shadow_naive` post-close counterfactual tracking (`r_if_left_alone`,
     `escort_helped`, `naive_outcome` honest in both directions).
  6. Paper-Entry Simulator `:01/:02` `A1` bar-service deduplication (`ALREADY_FILLED`
     and `same_working_limit` ahead of unstaged `:00` `valid_until` / `reference_price`
     drift checks) + Law #11 `SHOOT_PAPER_LIMIT_TTL_SECONDS` (`180s` M3 / `300s` M5).
  7. `tools/raw_replay.py` singleton reset isolation + `[8h]` Section #9 scorecard.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import config
import escort_team as et
import paper_entry_simulator as pes
from tools import raw_replay as rr


def _wave_cfg(**overrides):
    base = dict(
        STAGE4B_WAVE_TRAIL_ENABLED=True,
        ESCORT_PROFIT_LOCK_ENABLE=True,
        ESCORT_WAVE_LOCK_TRIGGER_R=1.0,
        ESCORT_WAVE_LOCK_GIVEBACK_ATR=1.5,
        ESCORT_TIME_STOP_MINUTES=45.0,
        ESCORT_MIN_AGE_SECONDS=180.0,
        ESCORT_DIVERGENCE_CLOSE_ENABLED=False,
        PM_WAVE_WEAK_BE_R=0.75,
        PM_WAVE_MAJOR_WALL_TP_LOTS=40.0,
        A2_BURST_TIME_STOP_MINUTES=12.0,
        SHOOT_PAPER_LIMIT_TTL_SECONDS=180.0,
        BASE_TF_MINUTES=3,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _filled_aggr_plan(
    side: str = "BUY",
    entry: float = 4080.40,
    stop: float = 4074.40,
    target: float = 4094.40,
    door_px: float = 4095.00,
    a2_burst: bool = False,
):
    return {
        "shot": "GO",
        "side": side,
        "entry": entry,
        "stop": stop,
        "target": target,
        "door": {"price": door_px, "size": 55.0, "resistance": 0.65},
        "entry_filled": True,
        "execution_status": "PAPER_ONLY",
        "entry_style": "AGGRESSIVE_MARKET",
        "paper_fill_model": "aggressive_quote_estimate",
        "a2_burst": a2_burst,
        "stop_source": "A2_BURST_FIXED" if a2_burst else "ATR_WAVE_STOP",
        "entry_options": {
            "AGGRESSIVE_MARKET": {
                "eligible": True,
                "trigger_reason": "power_expansion_confirmed",
                "target_source": "A2_BURST_TP" if a2_burst else "ATR_WAVE_TARGET",
            }
        },
    }


def test_e1_zero_second_entry_grace_prevents_nov06_suicide():
    """Exact reproduction of 2026-11-06 12:24:56Z BUY @ 4080.40 (div = -1.20)."""
    et.reset_escort()
    cfg = _wave_cfg()
    t0 = datetime(2026, 11, 6, 12, 24, 56, tzinfo=timezone.utc)
    plan = _filled_aggr_plan(side="BUY", entry=4080.40, stop=4074.40, target=4094.40)

    out = et.escort_cycle(
        plan,
        {"above": [{"price": 4095.0, "size": 55.0, "resistance": 0.65}]},
        price=4080.25,
        atr=4.0,
        spread=0.30,
        footprint=SimpleNamespace(delta_imbalance=-0.65),
        divergence=-1.20,
        config=cfg,
        bid=4080.10,
        ask=4080.40,
        now=t0,
    )
    assert out["opened_now"] is True
    assert out["watching"] == 1
    assert out["finished"] == 0
    assert len(out["reports"]) == 1
    assert out["reports"][0]["actions"] == []
    assert et.describe(out).startswith("ESCORT: guarding 1 |")


def test_e1_wave_mode_divergence_parity_and_min_age_gate():
    """Wave trades ignore pre-existing/early divergence and respect the 1.2x ATR stop floor."""
    et.reset_escort()
    cfg = _wave_cfg(ESCORT_MIN_AGE_SECONDS=180.0, ESCORT_DIVERGENCE_CLOSE_ENABLED=False)
    t0 = datetime(2026, 11, 6, 14, 22, 0, tzinfo=timezone.utc)
    # Open BUY @ 4000.0, stop=3994.0 (risk=6.0), target=4014.0 with pre-existing div=-0.80
    plan = _filled_aggr_plan(side="BUY", entry=4000.0, stop=3994.0, target=4014.0, door_px=4015.0)
    et.escort_cycle(
        plan, {}, price=4000.0, atr=3.0, spread=0.30,
        divergence=-0.80, config=cfg, bid=3999.85, ask=4000.15, now=t0,
    )

    # +60s (< 180s min age): strong negative divergence + negative footprint must NOT kill or halve stop
    out_60 = et.escort_cycle(
        {}, {}, price=3999.5, atr=3.0, spread=0.30,
        footprint=SimpleNamespace(delta_imbalance=-0.60),
        divergence=-1.10, config=cfg, bid=3999.35, ask=3999.65,
        now=t0 + timedelta(seconds=60),
    )
    assert out_60["watching"] == 1
    assert out_60["finished"] == 0
    assert et.get_escort().open[0].stop == 3994.0

    # +240s (>= 180s), pre-existing divergence (-0.90 vs entry -0.80) -> still ignored
    out_240_pre = et.escort_cycle(
        {}, {}, price=4001.0, atr=3.0, spread=0.30,
        divergence=-0.90, config=cfg, bid=4000.85, ask=4001.15,
        now=t0 + timedelta(seconds=240),
    )
    assert out_240_pre["watching"] == 1
    assert et.get_escort().open[0].stop == 3994.0

    # Open a second trade on a different door with positive entry divergence (+0.20),
    # then flip divergence freshly against it (-0.75):
    # - below +0.75R -> does NOT close when ESCORT_DIVERGENCE_CLOSE_ENABLED=False
    # - above +0.75R -> tightens safely while keeping >= 1.2x ATR distance from mark
    et.reset_escort()
    plan2 = _filled_aggr_plan(side="BUY", entry=4000.0, stop=3994.0, target=4014.0, door_px=4015.0)
    et.escort_cycle(
        plan2, {}, price=4000.0, atr=3.0, spread=0.30,
        divergence=+0.20, config=cfg, bid=3999.85, ask=4000.15, now=t0,
    )
    out_loss = et.escort_cycle(
        {}, {}, price=3999.0, atr=3.0, spread=0.30,
        divergence=-0.75, config=cfg, bid=3998.85, ask=3999.15,
        now=t0 + timedelta(seconds=240),
    )
    assert out_loss["watching"] == 1
    assert out_loss["finished"] == 0

    # Advance into decent open profit (+0.85R -> mark = 4005.10, < 1.0R profit lock)
    out_prof = et.escort_cycle(
        {}, {}, price=4005.25, atr=3.0, spread=0.30,
        divergence=-0.75, config=cfg, bid=4005.10, ask=4005.40,
        now=t0 + timedelta(seconds=300),
    )
    assert out_prof["watching"] == 1
    new_sl = et.get_escort().open[0].stop
    assert new_sl > 3994.0
    # Must never choke closer than 1.2 * ATR (3.60 pts) from mark 4005.10 -> <= 4001.50
    assert (4005.10 - new_sl) >= 3.60 - 1e-6


def test_e1_replay_clock_fidelity_and_law11_timeframe_stops():
    """Simulated `now` drives `t.opened` and Law #11 scales time-stop (12m A2 / 45m M3 / 75m M5)."""
    t0 = datetime(2026, 10, 30, 10, 0, 0, tzinfo=timezone.utc)

    # 1) A2 burst closes after 12m on simulated clock
    et.reset_escort()
    cfg_m3 = _wave_cfg(ESCORT_TIME_STOP_MINUTES=45.0, A2_BURST_TIME_STOP_MINUTES=12.0)
    p_a2 = _filled_aggr_plan(entry=4000.0, stop=3997.0, target=4006.0, a2_burst=True)
    et.escort_cycle(p_a2, {}, price=4000.0, atr=2.5, spread=0.30, config=cfg_m3, now=t0)
    out_11m = et.escort_cycle({}, {}, price=4000.5, atr=2.5, spread=0.30, config=cfg_m3, now=t0 + timedelta(minutes=11))
    assert out_11m["watching"] == 1
    out_13m = et.escort_cycle({}, {}, price=4000.5, atr=2.5, spread=0.30, config=cfg_m3, now=t0 + timedelta(minutes=13))
    assert out_13m["watching"] == 0
    assert out_13m["last_finished"]["reason"] == "TIME"

    # 2) M5 wave trade stays alive at 50m and closes after 75m
    et.reset_escort()
    cfg_m5 = _wave_cfg(ESCORT_TIME_STOP_MINUTES=75.0, BASE_TF_MINUTES=5)
    p_m5 = _filled_aggr_plan(entry=4000.0, stop=3994.0, target=4014.0, a2_burst=False)
    et.escort_cycle(p_m5, {}, price=4000.0, atr=3.0, spread=0.30, config=cfg_m5, now=t0)
    out_50m = et.escort_cycle({}, {}, price=4001.0, atr=3.0, spread=0.30, config=cfg_m5, now=t0 + timedelta(minutes=50))
    assert out_50m["watching"] == 1
    out_76m = et.escort_cycle({}, {}, price=4001.0, atr=3.0, spread=0.30, config=cfg_m5, now=t0 + timedelta(minutes=76))
    assert out_76m["watching"] == 0
    assert out_76m["last_finished"]["reason"] == "TIME"


def test_e1_executable_mark_on_escort_close_and_no_op_target_guard():
    """`ESCORT` close exits at executable quote (`bid` for BUY) and ignores no-op `new_target`."""
    et.reset_escort()
    cfg = _wave_cfg()
    t0 = datetime(2026, 11, 5, 13, 0, 0, tzinfo=timezone.utc)
    plan = _filled_aggr_plan(side="BUY", entry=4000.0, stop=3994.0, target=4010.0, door_px=4010.0)
    smap = {"above": [{"price": 4010.0, "size": 60.0, "resistance": 0.80, "trend": "growing"}]}
    et.escort_cycle(plan, smap, price=4000.0, atr=3.0, spread=0.40, config=cfg, bid=3999.80, ask=4000.20, now=t0)

    # At +0.6R (mark=4003.60 >= 0.5R breakeven), growing target door returns `new_target == t.target`.
    # `_apply` must reject the no-op so `actions` stays empty!
    out_hold = et.escort_cycle(
        {}, smap, price=4003.80, atr=3.0, spread=0.40, config=cfg,
        bid=4003.60, ask=4004.00, now=t0 + timedelta(minutes=3),
    )
    assert out_hold["watching"] == 1
    assert et.get_escort().open[0].actions == []

    # High-impact news bomb in 5 min -> closes at executable `bid` (4003.50), NOT mid/last `price` (4003.90)!
    bomb_news = SimpleNamespace(impact_level="HIGH", minutes_to_next_event=5.0)
    out_close = et.escort_cycle(
        {}, smap, price=4003.90, atr=3.0, spread=0.40, news=bomb_news, config=cfg,
        bid=4003.50, ask=4003.90, now=t0 + timedelta(minutes=6),
    )
    assert out_close["watching"] == 0
    assert out_close["last_finished"]["reason"] == "ESCORT"
    assert out_close["last_finished"]["exit"] == 4003.50


def test_e1_shadow_naive_deferred_counterfactual_honesty():
    """Post-close `shadow_naive` resolves honest counterfactuals when Escort exits early."""
    # Case A: Escort exits early on news at +0.20R; price later crashes through original stop (-1.00R)
    et.reset_escort()
    cfg = _wave_cfg()
    t0 = datetime(2026, 11, 4, 15, 0, 0, tzinfo=timezone.utc)
    plan = _filled_aggr_plan(side="BUY", entry=4000.0, stop=3995.0, target=4010.0, door_px=4010.0)
    et.escort_cycle(plan, {}, price=4000.0, atr=2.5, spread=0.20, config=cfg, bid=3999.90, ask=4000.10, now=t0)
    bomb = SimpleNamespace(impact_level="HIGH", minutes_to_next_event=5.0)
    et.escort_cycle({}, {}, price=4001.10, atr=2.5, spread=0.20, news=bomb, config=cfg,
                    bid=4001.00, ask=4001.20, now=t0 + timedelta(minutes=3))
    rec_a = et.get_escort().done[-1]
    assert rec_a["counterfactual_resolved"] is False
    # Subsequent crash through orig_stop 3995.0
    et.escort_cycle({}, {}, price=3994.50, atr=2.5, spread=0.20, config=cfg,
                    bid=3994.40, ask=3994.60, now=t0 + timedelta(minutes=9))
    assert rec_a["counterfactual_resolved"] is True
    assert rec_a["naive_outcome"] == "STOP"
    assert rec_a["r_if_left_alone"] == -1.0
    assert rec_a["escort_helped"] == 1.2

    # Case B: Escort exits early at +0.20R; price later rallies to orig_target (+2.00R) -> negative help!
    et.reset_escort()
    et.escort_cycle(plan, {}, price=4000.0, atr=2.5, spread=0.20, config=cfg, bid=3999.90, ask=4000.10, now=t0)
    et.escort_cycle({}, {}, price=4001.10, atr=2.5, spread=0.20, news=bomb, config=cfg,
                    bid=4001.00, ask=4001.20, now=t0 + timedelta(minutes=3))
    rec_b = et.get_escort().done[-1]
    et.escort_cycle({}, {}, price=4010.30, atr=2.5, spread=0.20, config=cfg,
                    bid=4010.20, ask=4010.40, now=t0 + timedelta(minutes=12))
    assert rec_b["counterfactual_resolved"] is True
    assert rec_b["naive_outcome"] == "TARGET"
    assert rec_b["r_if_left_alone"] == 2.0
    assert rec_b["escort_helped"] == -1.8


def test_e1_paper_simulator_a1_serve_dedup_and_working_limit_preservation(monkeypatch):
    """`:01/:02` A1 serve cycles deduplicate `ALREADY_FILLED` and preserve working `PASSIVE_LIMIT`."""
    pes.reset_entry_simulator()
    t0 = datetime(2026, 11, 3, 10, 0, 0, tzinfo=timezone.utc)
    cfg_m3 = SimpleNamespace(
        SHOOT_SIMULATE_AGGRESSIVE_PAPER=True,
        SHOOT_PAPER_LIMIT_TTL_SECONDS=180.0,
        SHOOT_MAX_SPREAD_DOLLARS=0.60,
        SHOOT_QUOTE_STALE_SECONDS=5.0,
        SHOOT_REPLAN_PRICE_TOL=0.35,
        BASE_TF_MINUTES=3,
    )

    # 1) Aggressive GO fills at :00 (`valid_until = t0 + 30s`); :01 (+60s) & :02 (+120s) serve cached plan
    ag_plan = {
        "shot": "GO",
        "side": "BUY",
        "entry": 4050.20,
        "stop": 4044.20,
        "target": 4062.20,
        "door": {"price": 4063.00},
        "entry_style": "AGGRESSIVE_MARKET",
        "execution_status": "PLAN_ONLY",
        "valid_until": t0.timestamp() + 30.0,
        "reference_price": 4050.00,
        "atr": 2.0,
        "spread": 0.30,
        "entry_options": {
            "AGGRESSIVE_MARKET": {
                "eligible": True,
                "trigger_reason": "power_expansion_confirmed",
                "stop": 4044.20,
                "target": 4062.20,
            }
        },
    }
    res_00 = pes.simulate_entry(dict(ag_plan), 4050.00, config=cfg_m3, now=t0.timestamp())
    assert res_00["status"] == "FILLED"

    # :01 (+60s): `valid_until` expired AND price drifted +$1.80 -> must STILL say `ALREADY_FILLED`, never `WAIT`!
    res_01 = pes.simulate_entry(dict(ag_plan), 4051.80, config=cfg_m3, now=t0.timestamp() + 60.0)
    res_02 = pes.simulate_entry(dict(ag_plan), 4051.80, config=cfg_m3, now=t0.timestamp() + 120.0)
    assert res_01["status"] == "ALREADY_FILLED"
    assert res_02["status"] == "ALREADY_FILLED"
    # :03 (+180s): bar switches to WAIT -> clears _LAST_FILLED_KEY so a brand-new later GO at same level can fill
    res_wait = pes.simulate_entry({"shot": "WAIT"}, 4050.00, config=cfg_m3, now=t0.timestamp() + 180.0)
    assert res_wait["status"] == "NO_PLAN"
    fresh_plan = dict(ag_plan, created_at=t0.timestamp() + 1800.0, valid_until=t0.timestamp() + 1830.0)
    res_reentry = pes.simulate_entry(fresh_plan, 4050.00, config=cfg_m3, now=t0.timestamp() + 1805.0)
    assert res_reentry["status"] == "FILLED"

    # 2) Passive limit staged at :00 (`entry = 4049.50`, TTL = 180s) stays `PENDING` at :01 despite drift
    #    and fills cleanly at :02 on a 1-tick trade-through (`4049.40 <= 4049.50 - 0.10`).
    pes.reset_entry_simulator()
    pl_plan = {
        "shot": "GO",
        "side": "BUY",
        "entry": 4049.50,
        "stop": 4044.50,
        "target": 4060.50,
        "door": {"price": 4061.00},
        "entry_style": "PASSIVE_LIMIT",
        "execution_status": "PLAN_ONLY",
        "valid_until": t0.timestamp() + 30.0,
        "reference_price": 4050.00,
        "atr": 2.0,
        "spread": 0.30,
        "entry_options": {"PASSIVE_LIMIT": {"eligible": True}},
    }
    p_00 = pes.simulate_entry(dict(pl_plan), 4050.00, config=cfg_m3, now=t0.timestamp())
    assert p_00["status"] == "PENDING"

    p_01 = pes.simulate_entry(dict(pl_plan), 4051.20, config=cfg_m3, now=t0.timestamp() + 60.0)
    assert p_01["status"] == "PENDING"

    p_02 = pes.simulate_entry(dict(pl_plan), 4049.40, config=cfg_m3, now=t0.timestamp() + 120.0)
    assert p_02["status"] == "FILLED"
    assert p_02["filled_plan"]["entry"] == 4049.50
    assert p_02["filled_plan"]["paper_fill_model"] == "trade_through_one_tick"

    # 3) Law #11 config scaling check: M3 -> 180s, M5 -> 300s (isolated from any host `.env`)
    try:
        monkeypatch.setenv("TIMEFRAME", "M3")
        monkeypatch.delenv("A1_BASE_TF_MINUTES", raising=False)
        monkeypatch.delenv("POWER_BAR_SECONDS", raising=False)
        monkeypatch.delenv("SHOOT_PAPER_LIMIT_TTL_SECONDS", raising=False)
        monkeypatch.delenv("ESCORT_TIME_STOP_MINUTES", raising=False)
        monkeypatch.delenv("ESCORT_MIN_AGE_SECONDS", raising=False)
        config._refresh()
        assert config.SHOOT_PAPER_LIMIT_TTL_SECONDS == 180.0
        assert config.ESCORT_TIME_STOP_MINUTES == 45.0
        assert config.ESCORT_MIN_AGE_SECONDS == 180

        monkeypatch.setenv("TIMEFRAME", "M5")
        config._refresh()
        assert config.SHOOT_PAPER_LIMIT_TTL_SECONDS == 300.0
        assert config.ESCORT_TIME_STOP_MINUTES == 75.0
        assert config.ESCORT_MIN_AGE_SECONDS == 300
    finally:
        config.reload_env()


def test_e1_raw_replay_reset_isolation_and_section_8h_scorecard(tmp_path: Path):
    """`_reset_step2_singletons` resets Paper/Escort state and `[8h]` renders cleanly."""
    et.reset_escort()
    pes.reset_entry_simulator()
    et.escort_cycle(
        _filled_aggr_plan(), {}, price=4080.4, atr=3.0, spread=0.3,
        config=_wave_cfg(), now=datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc),
    )
    assert len(et.get_escort().open) == 1
    rr._reset_step2_singletons()
    assert len(et.get_escort().open) == 0
    assert len(et.get_escort().done) == 0
    assert pes._PENDING is None
    assert pes._LAST_FILLED_KEY is None

    day_res = {
        "day": "2026-11-06",
        "ok": True,
        "instrument": "GC 12-26",
        "timeframe": "M3",
        "tf_horizons": [3, 6, 9, 12],
        "prints": 1200,
        "raw_tick_rows": 4000,
        "mbo_streamed": 8000,
        "day_range": 42.5,
        "day_net": -18.2,
        "trendiness": 0.43,
        "day_type": "MIXED",
        "conf_threshold": 55.0,
        "cycles": [],
        "judge_stats": {},
        "bench_judge_stats": {},
        "paper_sim_stats": {
            "bell": {"FILLED": 3, "ALREADY_FILLED": 0, "PENDING": 0, "CANCELLED": 0, "WAIT": 0, "NO_PLAN": 57, "EXPIRED_WAIT": 0},
            "serve": {"FILLED": 0, "ALREADY_FILLED": 6, "PENDING": 0, "CANCELLED": 0, "WAIT": 0, "NO_PLAN": 114, "EXPIRED_WAIT": 0},
            "legacy_0s_suicides": 3,
            "legacy_0s_suicide_pts": -0.9,
        },
        "escort_done": [
            {
                "day": "2026-11-06",
                "side": "SELL",
                "entry": 4045.3,
                "exit": 4035.3,
                "orig_target": 4031.3,
                "orig_stop": 4051.3,
                "entry_style": "AGGRESSIVE_MARKET",
                "paper_fill_model": "aggressive_quote_estimate",
                "is_a2_burst": False,
                "reason": "STOP",
                "r": 1.67,
                "pts": 10.0,
                "r_if_left_alone": 2.33,
                "pts_if_left_alone": 14.0,
                "escort_helped": -0.66,
                "escort_helped_pts": -4.0,
                "naive_outcome": "TARGET",
                "counterfactual_resolved": True,
                "actions": 2,
                "action_jobs": {"profit_lock": 2},
                "cycles": 18,
                "legacy_0s_suicide": True,
                "legacy_pts": -0.3,
            }
        ],
        "passive_shadow_rows": [
            {"day": "2026-11-06", "side": "SELL", "eligible": True, "filled": False, "passive_pm4b_pts": None, "aggr_pm4b_pts": 10.0},
            {"day": "2026-11-06", "side": "BUY", "eligible": True, "filled": True, "passive_pm4b_pts": 6.5, "aggr_pm4b_pts": 5.8},
        ],
        "errors": 0,
        "elapsed_s": 1.2,
    }
    txt, html, meta = rr.synthesize_multi_day([day_res])
    assert meta["days"] == 1
    assert "[8h] SECTION #9: ESCORT TEAM & PAPER-ENTRY SIMULATOR SCORECARD (Stage E1)" in txt
    assert "Expired_Serve_Clones=0" in txt
    assert "1) No Escort (Pure Planned SL/TP + TimeStop)" in txt
    assert "2) Stage-E1 Escort (Wave Lock + Major Walls)" in txt
    assert "3) Legacy Pre-E1 Escort (Old 0s Div-Close)" in txt
    assert "0s_Entry_Suicides: 0/1 (Legacy Pre-E1 killed 3/1" in txt
    assert "Passive-Limit Shadow Audit" in txt
    assert "<html" in html.lower()


def test_e1_step2_bell_to_a1_serve_full_pipeline_no_suicide_or_wait_clones(tmp_path: Path, monkeypatch):
    """End-to-end `s2.analyze_market` `:00 -> :01 -> :02` M3 bar-service integration check."""
    import step2_market_analysis as s2
    from test_nov06_stage_a2_aggressive_trigger_and_scorecard import (
        _make_m3_md,
        _reset_all_a2_state,
        _seed_warm_road_for_test,
    )

    _reset_all_a2_state()
    et.reset_escort()
    pes.reset_entry_simulator()
    try:
        monkeypatch.setenv("DATA_SOURCE", "replay")
        monkeypatch.setenv("TIMEFRAME", "M3")
        monkeypatch.setenv("L3_WHALE_THRESHOLD", "10.0")
        monkeypatch.setenv("SCOUT_MIN_TARGET_USD", "0.0")
        monkeypatch.setenv("SHOOT_MIN_REWARD_USD", "0.0")
        monkeypatch.setenv("LIMIT_ORDER_ENABLED", "1")
        config._refresh()
        monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(config, "A1_BAR_SERVICE_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "STAGE4B_WAVE_TRAIL_ENABLED", True, raising=False)
        monkeypatch.setattr(config, "ESCORT_MIN_AGE_SECONDS", 180, raising=False)
        monkeypatch.setattr(config, "ESCORT_DIVERGENCE_CLOSE_ENABLED", False, raising=False)
        monkeypatch.setattr(config, "ESCORT_TIME_STOP_MINUTES", 45.0, raising=False)
        monkeypatch.setattr(config, "SHOOT_PAPER_LIMIT_TTL_SECONDS", 180.0, raising=False)
        monkeypatch.setattr(config, "EXECUTION_MODE", "python", raising=False)

        def _strong_sell(_self, *_a, **_kw):
            return (
                -70.0,
                "SELL",
                74.0,
                [
                    "MTF M9:DOWN M36:DOWN -> SELL w 0.50",
                    "L3 NET FLOW SELL -65.0 (buys 5.0 vs sells 70.0) w 1.00",
                ],
            )

        monkeypatch.setattr(s2.SignalEngine, "aggregate", _strong_sell)
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

        # :00 Bell (`14:21:05Z`): SELL GO stages passive limit (`PENDING`, TTL=180s)
        t00 = datetime(2026, 11, 6, 14, 21, 5, tzinfo=timezone.utc)
        _seed_warm_road_for_test(t00)
        snap_00 = s2.analyze_market(_make_m3_md(t00, price=4045.3, buy_heavy=False), now=t00)
        assert snap_00.shot.get("shot") == "GO"
        assert snap_00.entry_simulation.get("status") == "PENDING"
        lim_px = float(snap_00.entry_simulation["pending"]["entry"])

        # :01 Serve (`14:22:05Z`, +60s): cached `:00` `valid_until` (+30s) has expired AND price drifted -$1.20,
        # yet `same_working_limit` keeps the passive limit `PENDING` across the 180s M3 bar!
        t01 = datetime(2026, 11, 6, 14, 22, 5, tzinfo=timezone.utc)
        snap_01 = s2.analyze_market(_make_m3_md(t01, price=4044.1, buy_heavy=False), now=t01)
        assert any("A1 bar-service:" in n for n in (snap_01.notes or []))
        assert snap_01.entry_simulation.get("status") == "PENDING"

        # :02 Serve (`14:23:05Z`, +120s): price trades 1 tick through `lim_px` -> `FILLED`!
        # Escort opens and guards (`watching == 1`, `finished == 0` -> zero entry-tick suicide!)
        t02 = datetime(2026, 11, 6, 14, 23, 5, tzinfo=timezone.utc)
        snap_02 = s2.analyze_market(_make_m3_md(t02, price=round(lim_px + 0.20, 2), buy_heavy=True), now=t02)
        assert snap_02.entry_simulation.get("status") == "FILLED"
        assert snap_02.escort.get("opened_now") is True
        assert snap_02.escort.get("watching") == 1
        assert snap_02.escort.get("finished") == 0

        # Intra-bar follow-up (`14:23:45Z`, +160s, still inside 14:21..14:24 M3 bucket):
        # Simulator deduplicates as `ALREADY_FILLED` (never a bogus `WAIT / expired` clone!)
        t02b = datetime(2026, 11, 6, 14, 23, 45, tzinfo=timezone.utc)
        snap_02b = s2.analyze_market(_make_m3_md(t02b, price=4044.8, buy_heavy=True), now=t02b)
        assert snap_02b.entry_simulation.get("status") == "ALREADY_FILLED"
        assert snap_02b.escort.get("watching") == 1
        # Cached bar-close snapshot remains untouched (T3g invariant)
        assert s2._A1_BAR_SERVICE_CACHE["entry"]["snapshot"].entry_simulation.get("status") == "PENDING"
    finally:
        _reset_all_a2_state()
        et.reset_escort()
        pes.reset_entry_simulator()
