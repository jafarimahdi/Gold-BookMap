"""Regression tests for the 2026-10-28 Five-Step Audit & Institutional Wisdom Upgrades."""
from __future__ import annotations

import pathlib
import sys
import time
import types
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import config
import escort_team
import main
import position_manager
import power_v2_regime
import step2_market_analysis as step2
import step3_ai_decision as step3
import step4_mt5_execution as step4
import step5_monitoring as step5
import wall_brackets


def test_wall_brackets_shifts_comex_futures_doors_onto_cfd_scale():
    """COMEX GC doors (~4180) must shift by additive basis onto XAUUSD (~4140)."""
    snap = SimpleNamespace(
        price=4180.0,
        bid=4179.9,
        ask=4180.1,
        data_symbol="GCZ6",
        trade_symbol="XAUUSD",
        signal_map={
            "above": [{"price": 4188.0, "size": 18.0}],
            "below": [{"price": 4176.0, "size": 22.0}],
        },
    )
    # BUY at CFD ask 4140.1 -> basis = 4180.1 - 4140.1 = +40.0
    # Opposing wall 4188.0 -> 4148.0 CFD; shelter wall 4176.0 -> 4136.0 CFD
    sl, tp, notes = wall_brackets.compute_wall_brackets(
        "BUY", 4140.1, atr=3.0, snapshot=snap, cfd_spread=0.30
    )
    assert sl is not None and tp is not None
    # buf = max(0.30*2, 0.15*3.0) = 0.60 -> TP = 4148.0 - 0.60 = 4147.40; SL = 4136.0 - 0.60 = 4135.40
    assert tp == pytest.approx(4147.40, abs=0.02)
    assert sl == pytest.approx(4135.40, abs=0.02)
    assert any("basis +40.00" in n for n in notes)

    # Backwardation (futures below spot by -5.0) works symmetrically
    snap_back = SimpleNamespace(
        price=4135.0,
        bid=4134.9,
        ask=4135.1,
        data_symbol="GCZ6",
        trade_symbol="XAUUSD",
        signal_map={
            "above": [{"price": 4142.0, "size": 12.0}],
            "below": [{"price": 4131.0, "size": 15.0}],
        },
    )
    # SELL at CFD bid 4139.9 -> basis = 4134.9 - 4139.9 = -5.0
    # Below wall 4131.0 - (-5.0) = 4136.0 CFD (< 4139.9); above wall 4142.0 -> 4147.0 CFD
    sl_s, tp_s, notes_s = wall_brackets.compute_wall_brackets(
        "SELL", 4139.9, atr=2.0, snapshot=snap_back, cfd_spread=0.20
    )
    assert tp_s == pytest.approx(4136.40, abs=0.02)
    assert sl_s == pytest.approx(4147.40, abs=0.02)
    assert any("basis -5.00" in n for n in notes_s)


def test_step4_uses_real_mt5_cfd_spread_and_a2_burst_fixed_brackets(monkeypatch):
    """Step 4 must check real MT5 tick spread and honor A2_BURST_FIXED 1.8/1.8 stops."""
    sent_requests = []

    class FakeMT5:
        TRADE_ACTION_DEAL = 1
        TRADE_ACTION_PENDING = 5
        ORDER_TYPE_BUY = 0
        ORDER_TYPE_SELL = 1
        ORDER_TYPE_BUY_LIMIT = 2
        ORDER_TYPE_SELL_LIMIT = 3
        POSITION_TYPE_BUY = 0
        POSITION_TYPE_SELL = 1
        ACCOUNT_TRADE_MODE_REAL = 2
        ORDER_TIME_GTC = 0
        ORDER_FILLING_FOK = 0
        ORDER_FILLING_IOC = 1
        ORDER_FILLING_RETURN = 2
        TRADE_RETCODE_DONE = 10009
        TRADE_RETCODE_PLACED = 10008

        def __init__(self, bid=4140.0, ask=4140.2):
            self._bid = bid
            self._ask = ask
            self._positions = []

        def initialize(self, **_kw):
            return True

        def shutdown(self):
            return None

        def account_info(self):
            return SimpleNamespace(
                login=12345, server="Pepperstone-Demo", trade_mode=0,
                balance=10000.0, equity=10000.0, margin_free=9500.0
            )

        def symbol_info(self, _sym):
            return SimpleNamespace(
                visible=True, digits=2, point=0.01,
                volume_min=0.01, volume_max=100.0, volume_step=0.01,
                trade_tick_value=1.0, trade_tick_size=0.01, filling_mode=2
            )

        def symbol_select(self, _sym, _enable):
            return True

        def symbol_info_tick(self, _sym):
            return SimpleNamespace(
                bid=self._bid, ask=self._ask, last=self._bid,
                time=int(time.time())
            )

        def positions_get(self, **_kw):
            return list(self._positions)

        def order_calc_margin(self, *_a):
            return 200.0

        def order_send(self, req):
            sent_requests.append(dict(req))
            self._positions.append(SimpleNamespace(
                ticket=777001, magic=req["magic"], type=req["type"],
                volume=req["volume"], price_open=req["price"],
                sl=req["sl"], tp=req["tp"], comment=req["comment"],
                symbol=req["symbol"]
            ))
            return SimpleNamespace(
                retcode=self.TRADE_RETCODE_DONE, order=777001, deal=888001,
                price=req["price"], volume=req["volume"], comment="done"
            )

    monkeypatch.setattr(config, "TRADING_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "EXECUTION_MODE", "python", raising=False)
    monkeypatch.setattr(config, "ALLOW_LIVE_TRADING", False, raising=False)
    monkeypatch.setattr(config, "V6_4TEAMS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "V6_CFD_SPREAD_MAX", 0.60, raising=False)
    monkeypatch.setattr(config, "A2_BURST_SL_PTS", 1.8, raising=False)
    monkeypatch.setattr(config, "A2_BURST_TP_PTS", 1.8, raising=False)
    monkeypatch.setattr(config, "A2_MIN_RR", 1.0, raising=False)
    monkeypatch.setattr(config, "ENTRY_MIN_RR", 1.2, raising=False)
    monkeypatch.setattr(config, "ENTRY_MIN_REWARD_ATR", 1.0, raising=False)
    monkeypatch.setattr(config, "AI_CONFIDENCE_THRESHOLD", 35.0, raising=False)

    snap = SimpleNamespace(
        symbol="GCZ6",
        data_symbol="GCZ6",
        trade_symbol="XAUUSD",
        price=4180.0,
        bid=4179.95,
        ask=4180.05,  # tight futures spread (0.10)
        confidence=72.0,
        regime="TREND",
        volatility=SimpleNamespace(atr=4.5),  # 4.5 ATR would reject 1.8 TP under normal 1.0*ATR value_gate!
        power={"direction": "UP", "reason_code": "UPWARD_FORCE_DOMINATES"},
        shot={
            "shot": "GO",
            "side": "BUY",
            "entry_style": "AGGRESSIVE_MARKET",
            "stop_source": "A2_BURST_FIXED",
        },
        signal_map={
            "above": [{"price": 4210.0, "size": 25.0}],
            "below": [{"price": 4150.0, "size": 25.0}],
        },
        news=SimpleNamespace(news_state="QUIET"),
    )
    decision = SimpleNamespace(action="BUY", confidence=72.0, rationale="test A2")

    # 1) Wide real MT5 CFD spread (0.85 > 0.60) MUST defer despite 0.10 futures spread
    wide_mt5 = FakeMT5(bid=4140.00, ask=4140.85)
    monkeypatch.setattr(step4, "mt5", wide_mt5)
    monkeypatch.setattr(config, "BASIS_MAX_DOLLARS", 100.0, raising=False)
    monkeypatch.setattr(config, "ENTRY_LOSS_MEMORY_MINUTES", 0, raising=False)
    monkeypatch.setattr(config, "ENTRY_DAY_RATCHET_ENABLE", False, raising=False)

    ex = step4.MT5Executor()
    res_wide = ex.execute(decision, snap, execution_authorized=True)
    assert res_wide.status == "DEFERRED"
    assert "CFD spread wide 0.85" in res_wide.reason

    # 2) Tight MT5 CFD spread (0.20) executes A2_BURST_FIXED with ~1.78 CFD-scaled SL/TP and "gold-bot A2 burst" comment
    tight_mt5 = FakeMT5(bid=4140.00, ask=4140.20)
    monkeypatch.setattr(step4, "mt5", tight_mt5)
    res_ok = ex.execute(decision, snap, execution_authorized=True)
    assert res_ok.status == "EXECUTED", res_ok.reason
    assert len(sent_requests) == 1
    req = sent_requests[0]
    expected_pts = 1.8 * (4140.20 / 4180.0)
    assert req["tp"] == pytest.approx(4140.20 + expected_pts, abs=0.03)
    assert req["sl"] == pytest.approx(4140.20 - expected_pts, abs=0.03)
    assert req["comment"] == "gold-bot A2 burst"


def test_position_manager_and_escort_time_stops_respect_a2_12m_and_m3_45m(monkeypatch, tmp_path):
    """A2 bursts time-stop at 12m; M3 scalps time-stop at 45m instead of 180m."""
    monkeypatch.setattr(config, "PM_TIME_STOP_MINUTES", 180, raising=False)
    monkeypatch.setattr(config, "PM_M3_TIME_STOP_MINUTES", 45, raising=False)
    monkeypatch.setattr(config, "A2_BURST_TIME_STOP_MINUTES", 12.0, raising=False)
    monkeypatch.setattr(config, "PM_TIME_STOP_MIN_PROGRESS", 0.30, raising=False)
    monkeypatch.setattr(config, "POWER_BAR_SECONDS", 180, raising=False)
    monkeypatch.setattr(config, "PM_BREAKEVEN_TRIGGER_R", 9.0, raising=False)
    monkeypatch.setattr(config, "PM_TRAIL_START_R", 9.0, raising=False)
    monkeypatch.setattr(config, "PM_ORDERFLOW_FLIP_EXIT", False, raising=False)
    monkeypatch.setattr(config, "PM_REGIME_FLIP_TIGHTEN", False, raising=False)

    pm = position_manager.PositionManager()
    pm._state_path = tmp_path / "pm_state.json"
    pm._state = {}
    closed = []
    monkeypatch.setattr(pm, "_close", lambda pos, tag, why, _ctx=None: closed.append((pos.ticket, tag, why)))

    now = time.time()
    pm._state["101"] = {
        "initial_sl": 4138.2, "be_done": False,
        "adopted_ts": now - 13 * 60, "last_modify_ts": 0.0,
        "is_a2_burst": False,
    }
    pos_a2 = SimpleNamespace(
        ticket=101, type=0, price_open=4140.0, sl=4138.2, tp=4141.8,
        volume=0.05, profit=0.5, comment="gold-bot A2 burst"
    )
    snap = SimpleNamespace(
        price=4140.1, bid=4140.05, ask=4140.15,
        indicators=SimpleNamespace(atr_14=2.0),
        regime="TREND", order_flow=None, level3=None,
        volume_profile=None, news=SimpleNamespace(news_state="QUIET"),
    )
    tick_obj = SimpleNamespace(bid=4140.05, ask=4140.15)
    info_obj = SimpleNamespace(digits=2, point=0.01)
    pm._manage_one(pos_a2, snap, tick_obj, info_obj)
    assert len(closed) == 1
    assert closed[0][0] == 101 and closed[0][1] == "TIME_STOP"
    assert "[A2 burst]" in closed[0][2]

    # Regular M3 trade stays open at 13m, closes at 46m
    pm._state["102"] = {
        "initial_sl": 4135.0, "be_done": False,
        "adopted_ts": now - 13 * 60, "last_modify_ts": 0.0,
        "is_a2_burst": False,
    }
    pos_m3 = SimpleNamespace(
        ticket=102, type=0, price_open=4140.0, sl=4135.0, tp=4150.0,
        volume=0.05, profit=0.5, comment="gold-trading-bot"
    )
    pm._manage_one(pos_m3, snap, tick_obj, info_obj)
    assert len(closed) == 1  # still open at 13m!

    pm._state["102"]["adopted_ts"] = now - 46 * 60
    pm._manage_one(pos_m3, snap, tick_obj, info_obj)
    assert len(closed) == 2 and closed[1][0] == 102 and "limit 45m" in closed[1][2]

    # Escort paper babysitter: A2_BURST_FIXED closes at >12m; standard AGGRESSIVE_MARKET stays open at 15m
    eb = escort_team.get_escort()
    eb.open.clear()
    eb.done.clear()
    escort_team.escort_cycle(
        {"shot": "GO", "side": "BUY", "entry": 4140.0, "target": 4141.8, "stop": 4138.2,
         "entry_filled": True, "execution_status": "PAPER_ONLY",
         "entry_style": "AGGRESSIVE_MARKET", "paper_fill_model": "aggressive_quote_estimate",
         "entry_options": {"AGGRESSIVE_MARKET": {"eligible": True, "trigger_reason": "A2 burst"}},
         "stop_source": "A2_BURST_FIXED"},
        {"above": [], "below": []}, 4140.1, atr=2.0, spread=0.2, config=config
    )
    assert len(eb.open) == 1
    eb.open[0].opened = time.time() - 13 * 60
    escort_team.escort_cycle({}, {"above": [], "below": []}, 4140.1, atr=2.0, spread=0.2, config=config)
    assert len(eb.open) == 0
    assert eb.done[-1]["reason"] == "TIME"
    eb.open.clear()
    eb.done.clear()


def test_local_m3_regime_bar_cache_recovers_28_bars_and_rejects_bad_inputs(monkeypatch, tmp_path):
    """M3 local bar cache warms 28/28 bars on restart while rejecting wrong scale/symbol."""
    monkeypatch.setenv("POWER_REGIME_BAR_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(config, "POWER_REGIME_BAR_CACHE_ENABLED", True, raising=False)

    power_v2_regime.set_bar_seconds(180)
    try:
        base_dt = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
        boundary_ep = int(base_dt.timestamp()) - (int(base_dt.timestamp()) % 180)
        bars_in = []
        for idx in range(30):
            st = boundary_ep - (30 - idx) * 180
            px = 4150.0 + idx * 0.8
            bars_in.append({
                "start": float(st),
                "open": px - 0.3,
                "high": px + 0.6,
                "low": px - 0.5,
                "close": px + 0.4,
            })
        step2._save_local_regime_bar_cache("GCZ6", 180, bars_in)

        # Wrong symbol is rejected
        assert step2._load_local_regime_bar_cache("SIZ6", base_dt, 180) == []
        # Wrong bar_seconds is rejected
        assert step2._load_local_regime_bar_cache("GCZ6", base_dt, 300) == []

        loaded = step2._load_local_regime_bar_cache("GCZ6", base_dt, 180)
        assert len(loaded) == 30
        live_ticks = [{"timestamp": base_dt - timedelta(seconds=2), "price": 4174.5}]
        reg = power_v2_regime.classify_m5_regime(live_ticks, now=base_dt, history_bars=loaded)
        assert reg["bars_used"] == 28
        assert reg["history_timeframe_compatible"] is True
        assert reg["market_regime"] in {"TREND", "RANGE", "TRANSITION"}

        # 10x price-scale mismatch fails closed
        bad_ticks = [{"timestamp": base_dt - timedelta(seconds=2), "price": 417.45}]
        reg_bad = power_v2_regime.classify_m5_regime(bad_ticks, now=base_dt, history_bars=loaded)
        assert reg_bad["market_regime"] == "UNKNOWN"
        assert reg_bad["reason"] == "HISTORY_PRICE_SCALE_MISMATCH"
        assert reg_bad["completed_bars"] == []
    finally:
        power_v2_regime.set_bar_seconds(300)


def test_depth_normalized_ofi_conflict_threshold():
    """Cont-Kukanov-Stoikov depth normalization scales conflict threshold linearly in [40, 250]."""
    cfg = SimpleNamespace(AI_L3_OFI_DEPTH_NORM_ENABLED=True, AI_L3_OFI_REF_DEPTH_LOTS=100.0)
    thin = SimpleNamespace(order_flow=SimpleNamespace(level2_bid_depth=15.0, level2_ask_depth=15.0))
    normal = SimpleNamespace(order_flow=SimpleNamespace(level2_bid_depth=50.0, level2_ask_depth=50.0))
    deep = SimpleNamespace(order_flow=SimpleNamespace(level2_bid_depth=110.0, level2_ask_depth=110.0))
    empty = SimpleNamespace(order_flow=SimpleNamespace(level2_bid_depth=0.0, level2_ask_depth=0.0))

    assert step3.compute_ofi_conflict_threshold(thin, cfg) == pytest.approx(40.0)
    assert step3.compute_ofi_conflict_threshold(normal, cfg) == pytest.approx(100.0)
    assert step3.compute_ofi_conflict_threshold(deep, cfg) == pytest.approx(220.0)
    assert step3.compute_ofi_conflict_threshold(empty, cfg) == pytest.approx(100.0)


def test_standing_ai_permission_slip_and_async_off_hot_path_refresh(monkeypatch, tmp_path):
    """Standing slip serves -cached during 3m window with 0 extra paid calls and invalidates on flips."""
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "_ai_state_path", lambda: tmp_path / "ai_calls.json")
    monkeypatch.setattr(main, "_LAST_AI_CALL", 0.0)
    monkeypatch.setattr(main, "_STANDING_AI_SLIP", None)
    monkeypatch.setattr(main, "_ASYNC_LOOP_ACTIVE", False)
    monkeypatch.setattr(config, "AI_MIN_SIGNAL_STRENGTH", 15.0, raising=False)
    monkeypatch.setattr(config, "AI_MIN_INTERVAL_MINUTES", 3.0, raising=False)
    monkeypatch.setattr(config, "AI_MAX_CALLS_PER_DAY", 500, raising=False)
    monkeypatch.setattr(config, "AI_STANDING_PERMISSION_ENABLED", True, raising=False)

    t_now = [20_000.0]
    monkeypatch.setattr("time.time", lambda: t_now[0])

    calls = [0]

    def fake_decide(_self, _snap):
        calls[0] += 1
        return step3.Decision(
            action="BUY", confidence=74.0,
            rationale="strong COMEX bid absorption", model="gemini-3.8-flash"
        )

    monkeypatch.setattr(step3.AIDecisionEngine, "decide", fake_decide)

    def make_snap(**kw):
        base = dict(
            signal_strength=30.0,
            regime="TREND",
            price=4180.0,
            indicators=SimpleNamespace(atr_14=3.0),
            power={"direction": "UP", "reason_code": "UPWARD_FORCE_DOMINATES"},
            order_flow=SimpleNamespace(level2_bid_depth=50.0, level2_ask_depth=50.0),
            level3=SimpleNamespace(
                aggressive_buy_volume=40.0, aggressive_sell_volume=10.0, ofi=25.0
            ),
        )
        base.update(kw)
        return SimpleNamespace(**base)

    # 1) Fresh synchronous call issues slip + counts 1 paid call
    d1 = main.run_step3(make_snap())
    assert d1.action == "BUY" and d1.model == "gemini-3.8-flash"
    assert calls[0] == 1
    assert main._ai_calls_today() == 1

    # 2) 30s later inside 3m window: serves standing slip (-cached) with ZERO extra paid calls
    t_now[0] = 20_030.0
    d2 = main.run_step3(make_snap(price=4181.2))
    assert d2.action == "BUY"
    assert d2.confidence == pytest.approx(74.0)
    assert d2.model == "gemini-3.8-flash-cached"
    assert getattr(d2, "decision_source", "") == "AI_STANDING_PERMISSION"
    assert calls[0] == 1
    assert main._ai_calls_today() == 1

    # 3) Opposing POWER flip immediately invalidates standing slip -> HOLD (none-throttled)
    d3 = main.run_step3(make_snap(power={"direction": "DOWN", "reason_code": "DOWNWARD_FORCE_DOMINATES"}))
    assert d3.action == "HOLD" and d3.model == "none-throttled"

    # 4) Excursion > 2*ATR (6.0 pts) invalidates standing slip
    d4 = main.run_step3(make_snap(price=4187.5))
    assert d4.action == "HOLD" and d4.model == "none-throttled"

    # 4b) News BLACKOUT on snapshot immediately invalidates standing slip
    d4b = main.run_step3(make_snap(news=SimpleNamespace(news_state="BLACKOUT")))
    assert d4b.action == "HOLD" and d4b.model == "none-throttled"

    # 5) Async loop mode dispatches off hot path (< 50ms) and harvests on next pass
    t_now[0] = 20_250.0
    main.set_async_loop_mode(True)
    try:
        def slow_decide(_self, _snap):
            time.sleep(0.12)
            calls[0] += 1
            return step3.Decision(
                action="SELL", confidence=78.0,
                rationale="async offer wall", model="gemini-3.8-flash"
            )

        monkeypatch.setattr(step3.AIDecisionEngine, "decide", slow_decide)
        sell_snap = make_snap(power={"direction": "DOWN", "reason_code": "DOWNWARD_FORCE_DOMINATES"})
        t0 = time.perf_counter()
        d_async = main.run_step3(sell_snap)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert d_async.action == "HOLD" and d_async.model == "none-async-pending"
        assert elapsed_ms < 60.0  # returned immediately without waiting 120ms!

        # Wait for background worker to finish, then harvest on next cycle
        deadline = time.perf_counter() + 2.0
        while main._AI_BG_STATE["in_flight"] and time.perf_counter() < deadline:
            time.sleep(0.01)
        t_now[0] = 20_255.0
        d_harv = main.run_step3(sell_snap)
        assert d_harv.action == "SELL" and d_harv.confidence == pytest.approx(78.0)
        assert d_harv.model == "gemini-3.8-flash"
        assert main._ai_calls_today() == 2
    finally:
        main.set_async_loop_mode(False)


def test_pre_gate_quote_revalidation_and_fast_burst_watch_and_env_parity(monkeypatch):
    """Slow Step 2/3 re-acquires fresh Step 1 quote safely; 10s burst watch wakes on A2; .env.example matches 20."""
    # Check .env.example MAX_TRADES_PER_DAY=20 parity with config.py
    env_example = (pathlib.Path(__file__).resolve().parent / ".env.example").read_text(encoding="utf-8")
    assert "MAX_TRADES_PER_DAY=20" in env_example

    # Fast burst wakeup wakes ONLY when flat + POWER active + matching A2 burst on tape
    now_dt = datetime.now(timezone.utc)
    burst_ticks = [
        {"price": 4180.2, "volume": 12.0, "side": "BUY", "is_direct": True, "ts": now_dt - timedelta(seconds=2)},
        {"price": 4180.3, "volume": 15.0, "side": "BUY", "is_direct": True, "ts": now_dt - timedelta(seconds=1)},
        {"price": 4180.4, "volume": 10.0, "side": "BUY", "is_direct": True, "ts": now_dt},
    ]
    monkeypatch.setattr(config, "AGGRESSIVE_ENTRY_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "A2_MIN_BURST_LOTS", 25.0, raising=False)
    monkeypatch.setattr(config, "A2_BURST_RATIO", 2.0, raising=False)
    monkeypatch.setattr(main, "_entries_paused", lambda: False)
    monkeypatch.setattr(main, "run_step1", lambda: {
        "has_data": True, "bid": 4180.3, "ask": 4180.4, "price": 4180.4, "tick_data": burst_ticks
    })

    main._LAST_PIPELINE_CONTEXT.clear()
    main._LAST_PIPELINE_CONTEXT.update({
        "open_positions_count": 0,
        "power_dir": "UP",
        "power_reason": "UPWARD_FORCE_DOMINATES",
        "last_shot": "WAIT",
        "allow_new_entries": True,
    })
    assert main.check_fast_burst_wakeup() is True

    # Must NOT wake when already in an open position
    main._LAST_PIPELINE_CONTEXT["open_positions_count"] = 1
    assert main.check_fast_burst_wakeup() is False


def test_run_pipeline_revalidates_fresh_quote_after_slow_step3_and_blocks_large_drift(monkeypatch):
    """After a slow 16s Step 3, fresh Step-1 quote within 1*ATR saves the cycle; >1*ATR drift fails closed."""
    class PositionManager:
        def manage(self, _snap):
            return None

    class TradeMonitor:
        def check_closed_deals(self, **_kw):
            return []

    for mod_name, attrs in {
        "maintenance": {"run_maintenance": lambda: None},
        "news": {"enrich_market_news": lambda _d: None},
        "macro": {"enrich_macro": lambda _d: None},
        "position_manager": {"get_position_manager": lambda: PositionManager()},
        "step5_monitoring": {"TradeMonitor": TradeMonitor},
    }.items():
        m = types.ModuleType(mod_name)
        for k, v in attrs.items():
            setattr(m, k, v)
        monkeypatch.setitem(sys.modules, mod_name, m)

    monkeypatch.setattr(main.config, "reload_env", lambda: None)
    monkeypatch.setattr(main.config, "RL_ENABLED", False, raising=False)
    monkeypatch.setattr(main.config, "LATENCY_LOG_ENABLED", False, raising=False)
    monkeypatch.setattr(main.config, "EXECUTION_FRESH_REACQUIRE_SECONDS", 2.0, raising=False)
    monkeypatch.setattr(main, "_record", lambda *_a, **_kw: None)
    monkeypatch.setattr(main, "_record_latency", lambda *_a, **_kw: None)
    monkeypatch.setattr(main, "_load_plumbing_test_position_ids", set)
    monkeypatch.setattr(main, "_load_tracked_position_ids", set)
    monkeypatch.setattr(main, "_save_tracked_position_ids", lambda *_a: None)
    monkeypatch.setattr(main, "_touch_lock", lambda: None)
    monkeypatch.setattr(main, "_check_flash_crash", lambda _s: "")
    monkeypatch.setattr(main, "_entries_paused", lambda: False)
    monkeypatch.setattr(main, "_update_last_diary_ai", lambda *_a, **_kw: None)
    monkeypatch.setattr(main, "_market_quality_evidence", lambda *_a, **_kw: {"safety_gate_status": "PASS"})
    monkeypatch.setattr(main, "_attach_quality_evidence_to_shadow_candidates", lambda *_a: None)
    monkeypatch.setattr(main, "_persist_market_quality_snapshot", lambda *_a: True)
    monkeypatch.setattr(main, "_load_logged_outcome_keys", set)
    monkeypatch.setattr(main, "_save_logged_outcome_keys", lambda *_a: None)
    monkeypatch.setattr(main, "run_step5", lambda: [])
    monkeypatch.setattr(main, "run_signal_bridge", lambda *_a, **_kw: None)
    monkeypatch.setattr(main, "log_decision", lambda *_a: None)
    monkeypatch.setattr(main, "print_summary", lambda: None)

    mono_val = [100.0]
    monkeypatch.setattr(main.time, "monotonic", lambda: mono_val[0])

    # Case A: Step 3 took 16s, fresh quote moved only +0.4 pts (<= 1.0*ATR=3.0) -> SAVED!
    step1_seq = [
        {"has_data": True, "price": 4180.0, "bid": 4179.9, "ask": 4180.1, "tag": "initial"},
        {"has_data": True, "price": 4180.4, "bid": 4180.3, "ask": 4180.5, "tag": "fresh"},
    ]
    monkeypatch.setattr(main, "run_step1", lambda: step1_seq.pop(0))
    snap_a = SimpleNamespace(
        timestamp=None, notes=[], regime="TREND", price=4180.0,
        bid=4179.9, ask=4180.1, indicators=SimpleNamespace(atr_14=3.0)
    )
    monkeypatch.setattr(main, "run_step2", lambda _d: snap_a)

    def slow_step3(_s):
        mono_val[0] = 116.0  # 16s elapsed (>10s stale threshold!)
        return step3.Decision(action="BUY", confidence=75.0, rationale="ok", model="gemini-3.8-flash")

    monkeypatch.setattr(main, "run_step3", slow_step3)
    # Old initial data would fail if elapsed > 10s; fresh data passes!
    monkeypatch.setattr(main, "_preflight_data_reason",
                        lambda d, elapsed=0.0: "STALE QUOTE" if elapsed > 10.0 else None)
    monkeypatch.setattr(main, "_safety_gates",
                        lambda d, elapsed=0.0: (elapsed <= 10.0, "STALE QUOTE" if elapsed > 10.0 else "ok"))

    step4_calls = []
    monkeypatch.setattr(main, "run_step4",
                        lambda dec, sn, *, execution_authorized=False, authorization_reason="":
                        step4_calls.append((dec, sn, execution_authorized, authorization_reason))
                        or SimpleNamespace(status="EXECUTED", reason="ok"))

    main.run_pipeline(allow_new_entries=True)
    assert len(step4_calls) == 1
    assert step4_calls[0][2] is True  # execution_authorized == True!
    assert snap_a.price == pytest.approx(4180.4)

    # Case B: Fresh quote drifted +5.5 pts (> 1.0*ATR=3.0) -> BLOCKED by EXECUTION DRIFT!
    mono_val[0] = 200.0
    step1_seq_b = [
        {"has_data": True, "price": 4180.0, "bid": 4179.9, "ask": 4180.1, "tag": "initial"},
        {"has_data": True, "price": 4185.5, "bid": 4185.4, "ask": 4185.6, "tag": "drifted"},
    ]
    monkeypatch.setattr(main, "run_step1", lambda: step1_seq_b.pop(0))
    snap_b = SimpleNamespace(
        timestamp=None, notes=[], regime="TREND", price=4180.0,
        bid=4179.9, ask=4180.1, indicators=SimpleNamespace(atr_14=3.0)
    )
    monkeypatch.setattr(main, "run_step2", lambda _d: snap_b)

    def slow_step3_b(_s):
        mono_val[0] = 216.0
        return step3.Decision(action="BUY", confidence=75.0, rationale="ok", model="gemini-3.8-flash")

    monkeypatch.setattr(main, "run_step3", slow_step3_b)
    step4_calls.clear()
    main.run_pipeline(allow_new_entries=True)
    assert len(step4_calls) == 1
    assert step4_calls[0][2] is False  # blocked!
    assert "quote drifted" in step4_calls[0][3]
