from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
import risk_manager as rm_mod
import tools.raw_replay as rr


def test_stage_r1_cme_18et_rollover_blocks_midnight_utc_split_session_and_handles_dst(
    tmp_path, monkeypatch
):
    """Stage R1: A daily loss at 23:30 UTC (18:30 EST, inside the new CME Globex session)
    must stay halted until 18:00 EST the NEXT evening (23:00 UTC), never unlocking 30m
    later at 00:01 UTC; DST transitions work with both ZoneInfo and pure-Python fallback.
    """
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "RISK_SESSION_ROLL_MODE", "cme_18et", raising=False)
    monkeypatch.setattr(rm_mod, "mt5", None)

    # 2026-11-09 23:30 UTC = 18:30 EST (already in Tuesday 2026-11-10 CME trade date!)
    t_loss = datetime(2026, 11, 9, 23, 30, 0, tzinfo=timezone.utc)
    s_start, s_end, s_key = rm_mod.session_bounds_utc(t_loss)
    assert s_start == datetime(2026, 11, 9, 23, 0, 0, tzinfo=timezone.utc)
    assert s_end == datetime(2026, 11, 10, 23, 0, 0, tzinfo=timezone.utc)
    assert s_key == "2026-11-10"

    # Seed an offline closed loss of -$320 on $10,000 equity (-3.2% <= -3.0%) at 23:25 UTC
    (tmp_path / "trade_memory.json").write_text(
        json.dumps({
            "closed": [{
                "ticket": "7001",
                "closed_ts": (t_loss - timedelta(minutes=5)).timestamp(),
                "gain_pts": -8.0,
                "pnl_usd_est": -320.0,
            }]
        }),
        encoding="utf-8",
    )

    rm = rm_mod.RiskManager(daily_loss_pct=3.0, max_drawdown_pct=10.0, max_daily_loss_pts=12.0)
    ok_2330, why_2330 = rm.check(equity=10_000.0, now=t_loss)
    assert ok_2330 is False
    assert "daily loss -320.00 hits limit -300.00" in why_2330

    # 31 minutes later at 00:01 UTC (old UTC-midnight bug unlocked here!): MUST remain HALTED!
    ok_0001, _ = rm.check(equity=10_000.0, now=datetime(2026, 11, 10, 0, 1, 0, tzinfo=timezone.utc))
    assert ok_0001 is False

    # Still halted during London & NY next afternoon (2026-11-10 15:00 UTC = 10:00 EST)
    ok_1500, _ = rm.check(equity=10_000.0, now=datetime(2026, 11, 10, 15, 0, 0, tzinfo=timezone.utc))
    assert ok_1500 is False

    # Unlocks cleanly at 2026-11-10 23:01 UTC (18:01 EST -> Wednesday 2026-11-11 CME session!)
    ok_next_cme, why_next_cme = rm.check(
        equity=10_000.0, now=datetime(2026, 11, 10, 23, 1, 0, tzinfo=timezone.utc)
    )
    assert ok_next_cme is True and why_next_cme == "ok"

    # Verify US Spring-Forward (2026-03-08) & Fall-Back (2026-11-01) under pure-Python fallback (_ZoneInfo=None)
    monkeypatch.setattr(rm_mod, "_ZoneInfo", None)
    st_mar, en_mar, ky_mar = rm_mod.session_bounds_utc(datetime(2026, 3, 8, 12, 0, 0, tzinfo=timezone.utc))
    assert st_mar == datetime(2026, 3, 7, 23, 0, 0, tzinfo=timezone.utc)   # Sat 18:00 EST
    assert en_mar == datetime(2026, 3, 8, 22, 0, 0, tzinfo=timezone.utc)   # Sun 18:00 EDT
    assert ky_mar == "2026-03-08"

    st_nov, en_nov, ky_nov = rm_mod.session_bounds_utc(datetime(2026, 11, 1, 12, 0, 0, tzinfo=timezone.utc))
    assert st_nov == datetime(2026, 10, 31, 22, 0, 0, tzinfo=timezone.utc)  # Sat 18:00 EDT
    assert en_nov == datetime(2026, 11, 1, 23, 0, 0, tzinfo=timezone.utc)   # Sun 18:00 EST
    assert ky_nov == "2026-11-01"


def test_stage_r1_preserves_peak_equity_across_daily_halt_and_latches_max_drawdown(
    tmp_path, monkeypatch
):
    """Stage R1: Expiring a daily halt must never erase `peak_equity`, and hitting
    `MAX_DRAWDOWN_PCT` must latch `MANUAL_RESET` (`kill_switch=True`) until `reset()`.
    """
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(rm_mod, "mt5", None)

    rm = rm_mod.RiskManager(daily_loss_pct=3.0, max_drawdown_pct=10.0, max_daily_loss_pts=12.0)
    t1 = datetime(2026, 11, 4, 14, 0, 0, tzinfo=timezone.utc)

    # Peak reaches $11,000
    assert rm.check(equity=10_000.0, now=t1) == (True, "ok")
    assert rm.check(equity=11_000.0, now=t1 + timedelta(minutes=5)) == (True, "ok")
    assert rm.load_state()["peak_equity"] == pytest.approx(11_000.0)

    # Trigger a -13.0 pt Stage-4R daily halt at $10,600 equity
    t_close = t1 + timedelta(minutes=10)
    (tmp_path / "trade_memory.json").write_text(
        json.dumps({
            "closed": [{
                "ticket": "8001",
                "closed_ts": t_close.timestamp(),
                "gain_pts": -13.0,
                "pnl_usd_est": -130.0,
            }]
        }),
        encoding="utf-8",
    )
    ok_d1, why_d1 = rm.check(equity=10_600.0, now=t_close + timedelta(seconds=5))
    assert ok_d1 is False
    assert "stage4r daily loss -13.00 pts hits limit -12.00 pts" in why_d1

    # Next day at $10,200 (< $11,000 peak): daily halt expires AND $11,000 peak stays on disk!
    t2 = datetime(2026, 11, 5, 14, 0, 0, tzinfo=timezone.utc)
    assert rm.check(equity=10_200.0, now=t2) == (True, "ok")
    assert rm.load_state()["peak_equity"] == pytest.approx(11_000.0)
    assert rm.check(equity=10_100.0, now=t2 + timedelta(minutes=5)) == (True, "ok")
    assert rm.load_state()["peak_equity"] == pytest.approx(11_000.0)

    # Equity drops to $9,850 -> 10.45% drawdown from $11,000 HWM (would have been missed if peak reset!)
    ok_dd, why_dd = rm.check(equity=9_850.0, now=t2 + timedelta(minutes=10))
    assert ok_dd is False
    assert "max drawdown 10.5% >= 10.0%" in why_dd
    st_dd = rm.load_state()
    assert st_dd["kill_switch"] is True
    assert st_dd["halted"] is True
    assert st_dd["halt_until"] == "MANUAL_RESET"

    # Must NOT auto-unlock 5 days later — stays latched until manual `reset()`!
    ok_5d, _ = rm.check(equity=10_500.0, now=t2 + timedelta(days=5))
    assert ok_5d is False
    rm.reset()
    assert rm.check(equity=10_500.0, now=t2 + timedelta(days=5, minutes=1)) == (True, "ok")

    # Zero-equity wipeout (`equity=0.0`) must NOT fall back to `config.ACCOUNT_EQUITY` ($10,000)!
    ok_zero, why_zero = rm.check(equity=0.0, now=t2 + timedelta(days=5, minutes=2))
    assert ok_zero is False
    assert "100.0%" in why_zero


def test_stage_r1_100k_demo_stage4r_points_dedup_and_asymmetric_floating_loss(
    tmp_path, monkeypatch
):
    """Stage R1:
    1) On $100k DEMO (3% = -$3,000), -13.5 pts (-$135 on 0.10 lot) in MT5 deals trips Stage-4R
       even if `trade_memory.json` was wiped, without double-counting covered tickets.
    2) Open floating bot losses count asymmetrically (`min(0, floating)`), manual magic!=234000
       is ignored, and `check()` performs a single MT5 init/shutdown roundtrip.
    """
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "MT5_SYMBOL", "XAUUSD", raising=False)
    monkeypatch.setattr(config, "CONTRACT_SIZE", 100.0, raising=False)

    now_dt = datetime(2026, 11, 6, 15, 0, 0, tzinfo=timezone.utc)
    now_ep = now_dt.timestamp()
    (tmp_path / "tracked_bot_positions.json").write_text(
        json.dumps({"position_ids": [99001, 99002]}), encoding="utf-8"
    )
    # Only 99001 (-6.50 pts) is in trade_memory.json; 99002 (-$70.00 / 0.10 lot = -7.00 pts) is in MT5 only!
    (tmp_path / "trade_memory.json").write_text(
        json.dumps({
            "closed": [{
                "ticket": "99001",
                "closed_ts": now_ep - 600.0,
                "gain_pts": -6.5,
                "pnl_usd_est": -65.0,
            }]
        }),
        encoding="utf-8",
    )

    deals_by_pos = {
        99001: [SimpleNamespace(
            ticket=1, position_id=99001, symbol="XAUUSD", magic=234000,
            entry=1, volume=0.10, profit=-65.0, swap=0.0, commission=0.0, fee=0.0,
            time=int(now_ep - 600.0), time_msc=int((now_ep - 600.0) * 1000),
        )],
        99002: [SimpleNamespace(
            ticket=2, position_id=99002, symbol="XAUUSD", magic=234000,
            entry=1, volume=0.10, profit=-70.0, swap=0.0, commission=0.0, fee=0.0,
            time=int(now_ep - 120.0), time_msc=int((now_ep - 120.0) * 1000),
        )],
    }
    open_positions = []
    init_cnt = 0
    shut_cnt = 0

    def _init(_m):
        nonlocal init_cnt
        init_cnt += 1
        return True

    def _shut():
        nonlocal shut_cnt
        shut_cnt += 1

    fake_mt5 = SimpleNamespace(
        account_info=lambda: SimpleNamespace(equity=100_000.0),
        symbol_info_tick=lambda _s: SimpleNamespace(bid=4000.0, ask=4000.2, time=int(now_ep), time_msc=int(now_ep * 1000)),
        history_deals_get=lambda position=None, *a, **kw: deals_by_pos.get(int(position), []) if position else [],
        positions_get=lambda symbol=None: list(open_positions),
        shutdown=_shut,
    )
    monkeypatch.setattr(rm_mod, "mt5", fake_mt5)
    monkeypatch.setattr(config, "mt5_initialize", _init)

    rm = rm_mod.RiskManager(daily_loss_pct=3.0, max_drawdown_pct=10.0, max_daily_loss_pts=12.0)
    # Deduplicated points = -6.5 (from memory 99001) + -7.0 (from MT5 99002) = -13.50 pts <= -12.00 pts!
    assert rm.daily_points_pnl(now=now_dt) == pytest.approx(-13.5)
    init_cnt = shut_cnt = 0
    ok_100k, why_100k = rm.check(now=now_dt)
    assert ok_100k is False
    assert "stage4r daily loss -13.50 pts hits limit -12.00 pts" in why_100k
    assert init_cnt == 1 and shut_cnt == 1  # single IPC session across the entire check()!

    # Reset and test asymmetric floating PnL:
    rm.reset()
    (tmp_path / "trade_memory.json").unlink()
    deals_by_pos.clear()
    # 1) Manual position (-$5,000, magic=777) ignored; open bot position (-13.0 pts, magic=234000) blocks!
    open_positions[:] = [
        SimpleNamespace(ticket=501, symbol="XAUUSD", magic=777, type=0, volume=1.0, price_open=4050.0, price_current=4000.0, profit=-5000.0, swap=0.0),
        SimpleNamespace(ticket=502, symbol="XAUUSD", magic=234000, type=0, volume=0.10, price_open=4013.0, price_current=4000.0, profit=-130.0, swap=0.0),
    ]
    ok_flt, why_flt = rm.check(now=now_dt)
    assert ok_flt is False
    assert "-13.00 pts" in why_flt

    # 2) Open bot floating WINNER (+$500 / +50 pts) must NEVER mask realized closed loss (-$320 on $10k)!
    rm.reset()
    open_positions[:] = [
        SimpleNamespace(ticket=503, symbol="XAUUSD", magic=234000, type=0, volume=0.10, price_open=3950.0, price_current=4000.0, profit=500.0, swap=0.0),
    ]
    deals_by_pos[99001] = [SimpleNamespace(
        ticket=3, position_id=99001, symbol="XAUUSD", magic=234000,
        entry=1, volume=0.10, profit=-320.0, swap=0.0, commission=0.0, fee=0.0,
        time=int(now_ep - 60.0), time_msc=int((now_ep - 60.0) * 1000),
    )]
    ok_mask, why_mask = rm.check(equity=10_000.0, now=now_dt)
    assert ok_mask is False
    assert "daily loss -320.00 hits limit -300.00" in why_mask


def test_stage_r1_raw_replay_scorecard_8j_proves_capital_protection(tmp_path, monkeypatch):
    """Stage R1: `synthesize_multi_day` renders `[8j]` and proves unified Stage-R1
    capital protection across both $10k and $100k DEMO accounts without touching repo data.
    """
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(config, "LOT_SIZE", 0.1, raising=False)
    monkeypatch.setattr(config, "CONTRACT_SIZE", 100.0, raising=False)
    monkeypatch.setattr(config, "DAILY_LOSS_LIMIT_PCT", 3.0, raising=False)
    monkeypatch.setattr(config, "MAX_DRAWDOWN_PCT", 10.0, raising=False)
    monkeypatch.setattr(config, "STAGE4R_POST_EXIT_COOLDOWN_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "STAGE4R_POST_EXIT_COOLDOWN_MINUTES", 3.0, raising=False)
    monkeypatch.setattr(config, "STAGE4R_DAILY_LOSS_BREAKER_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "STAGE4R_DAILY_MAX_LOSS_PTS", 12.0, raising=False)
    monkeypatch.setattr(config, "RISK_ENFORCE_STAGE4R_PTS", True, raising=False)
    monkeypatch.setattr(config, "RISK_SESSION_ROLL_MODE", "cme_18et", raising=False)
    t0 = datetime(2026, 11, 5, 13, 0, 0, tzinfo=timezone.utc).timestamp()

    def _mk_go(idx: int, pts: float) -> dict:
        ent = t0 + idx * 900.0
        dt_u = datetime.fromtimestamp(ent, tz=timezone.utc)
        return {
            "entry_ep": ent,
            "dt_utc": dt_u,
            "buda_hhmmss": "14:00:00",
            "buda_hour": 14,
            "price": 4000.0,
            "bid": 3999.9,
            "ask": 4000.1,
            "spread_pts": 0.2,
            "quote_status": "valid",
            "l2_status": "validated",
            "l3_status": "available",
            "order_flow_status": "direct_trade_side",
            "fresh_bid_levels": 50,
            "fresh_ask_levels": 50,
            "raw_best_bid": 3999.9,
            "raw_best_ask": 4000.1,
            "depth_levels": "50/50",
            "mbo_window": 2000,
            "mbo_near_cnt": 2000,
            "mbo_span_s": 42.0,
            "direction": "BUY",
            "confidence": 70.0,
            "strength": 45.0,
            "divergence": 0.0,
            "regime": "TREND",
            "atr": 4.0,
            "power_dir": "UP",
            "power_regime": "TREND",
            "power_activity": 0.8,
            "power_bars": 28,
            "family_ok": True,
            "agree_families": ["TREND", "FLOW"],
            "shot_action": "GO",
            "shot_style": "AGGRESSIVE_MARKET",
            "shot_reason": "",
            "shot_a2_burst": False,
            "gate_stage": "SHOOTING GO (AGGRESSIVE_MARKET)",
            "active_votes": [],
            "pb1": 4000.0 + pts,
            "pb2": 4000.0 + pts,
            "pb3": 4000.0 + pts,
            "pb4": 4000.0 + pts,
            "signed_4b": [pts, pts, pts, pts],
            "fav4b": 1.0,
            "adv4b": abs(pts),
            "trick_code": "CLEAN_AGAINST",
            "trick_label": "AGAINST",
            "p5": 4000.0 + pts,
            "p15": 4000.0 + pts,
            "p30": 4000.0 + pts,
            "fav30": 1.0,
            "adv30": abs(pts),
            "fix_out": "SL",
            "fix_pts": pts,
            "c3_out": "SL",
            "c3_pts": pts,
            "tr10_out": "SL",
            "tr10_pts": pts,
            "shot_br_out": "SL",
            "shot_br_pts": pts,
            "shot_rr": 1.75,
            "shot_sl_dist": 8.0,
            "shot_tp_dist": 14.0,
            "shot_tp_src": "ATR_WAVE_TARGET",
            "shot_pebble_bypass": False,
            "shot_br_exit_ep": ent + 300.0,
            "shot_pm4b_exit_ep": ent + 300.0,
            "shot_s15_out": "SL",
            "shot_s15_pts": pts,
            "shot_pm4b_out": "SL",
            "shot_pm4b_pts": pts,
            "shot_ch15_out": "SL",
            "shot_ch15_pts": pts,
            "shot_ch10_out": "SL",
            "shot_ch10_pts": pts,
            "shot_c3p_out": "SL",
            "shot_c3p_pts": pts,
            "old_div_panic": False,
            "old_div_pts": pts,
            "a2_fix_pts": None,
            "a2_atr_pts": None,
            "scout": None,
            "power": None,
            "a2_eval": None,
        }

    # Three consecutive -13.0 pt losses (-39.0 pts unprotected; Stage-R1 halts after #1 -> saves +26.0 pts!)
    day_res = {
        "ok": True,
        "day": "2026-11-05",
        "instrument": "GCZ6.COMEX@RITHMIC",
        "timeframe": "M3",
        "tf_horizons": [3, 6, 9, 12],
        "day_type": "TREND",
        "prints": 1000,
        "raw_tick_rows": 2000,
        "mbo_streamed": 10000,
        "day_range": 40.0,
        "day_net": -20.0,
        "trendiness": 0.5,
        "conf_threshold": 35.0,
        "cycles": [_mk_go(0, -13.0), _mk_go(1, -13.0), _mk_go(2, -13.0)],
        "judge_stats": {},
        "bench_judge_stats": {},
        "paper_sim_stats": {},
        "escort_done": [],
        "passive_shadow_rows": [],
        "errors": [],
        "elapsed_s": 1.0,
    }
    txt, _, _ = rr.synthesize_multi_day([day_res])
    assert "[8j] SECTION #11: ACCOUNT RISK MANAGER & HARD SAFETY CAPS SCORECARD (Stage R1)" in txt
    assert "Stage-R1 Capital Protection vs Unprotected: +26.0 pts ($+260.00)" in txt
