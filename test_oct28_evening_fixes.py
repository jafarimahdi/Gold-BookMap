"""
test_oct28_evening_fixes.py
===========================
Regression tests for the 2026-10-28 evening audit fixes:
  1. Crowd v2 Q2 passive road-history warm-up on fresh live non-POWER / news cycles
     (curing 74/74 live POWER warm-up starvation while failing closed on stale tape).
  2. step5_monitoring.__main__ resolution for `python main.py --loop` + smart Step 3
     async-on-WAIT vs synchronous/joined wake on live `Shooting == GO` / A2 fires.
  3. Gemini lowercase `thinking_level="low"` SDK contract, retired `2.5-flash`
     pruning, adaptive slow-model demotion, no false API-key lockout on slice
     timeouts, and clean `none-timeout-budget` classification on final-model slices.
  4. Dynamic `config.DATA_DIR` binding + `conftest.py` isolation so `pytest` never
     leaks state files into live `./data` or `./logs`.
  5. Dynamic Budapest CET (`UTC+1`) vs CEST (`UTC+2`) offset in `session_context`.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

import config
import main
import maintenance
import position_manager
import session
import shooting_team
import step3_ai_decision as s3
import step5_monitoring as s5
import trade_guard
import trade_history


def _shoot_cfg(**overrides):
    base = dict(
        LIMIT_ORDER_ENABLED=True,
        AGGRESSIVE_ENTRY_ENABLED=False,
        LOT_SIZE=1.0,
        SHOOT_DOOR_MARGIN=3.0,
        SHOOT_ROAD_NORMAL_MAX=2.0,
        SHOOT_PARTICIPATION_MAX=0.10,
        SHOOT_ROAD_HISTORY_MIN=12,
        SHOOT_ROAD_HISTORY_SECONDS=2700.0,
        SHOOT_ROAD_PASSIVE_SAMPLE_GAP_SECONDS=5.0,
        STALE_DATA_SECONDS=300.0,
        POWER_MAX_TICK_AGE_SECONDS=60.0,
        SHOOT_MIN_RR=1.2,
        LIMIT_CANCEL_AFTER_SECONDS=90.0,
        SHOOT_PLAN_TTL_SECONDS=30.0,
        QUEUE_POS_ENABLED=False,
        PASSIVE_SPREAD_MAX_PCT=0.05,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _dual_smap(up_lots=140.0, down_lots=160.0, door_size=12.0):
    return {
        "above": [{
            "price": 103.5, "size": door_size, "kind": "OFFER_WALL",
            "doors_between": 1, "lots_between": up_lots, "road_clear": True,
        }],
        "below": [{
            "price": 96.5, "size": door_size, "kind": "BID_WALL",
            "doors_between": 1, "lots_between": down_lots, "road_clear": True,
        }],
    }


def _power_payload(direction="NEITHER"):
    if direction == "NEITHER":
        return {
            "team": "POWER",
            "direction": "NEITHER",
            "up_power_pct": 51.0,
            "down_power_pct": 49.0,
            "power_total_pct": 100.0,
            "reason_code": "NO_DOMINANT_FORCE",
            "context": {"market_regime": "TREND", "data_quality_ok": True, "feed_stale": False},
            "regime_diagnostics": {"market_regime": "TREND", "latest_tick_age_seconds": 1.5},
            "shadow_only": False,
        }
    return {
        "team": "POWER",
        "direction": direction,
        "up_power_pct": 68.0 if direction == "UP" else 32.0,
        "down_power_pct": 32.0 if direction == "UP" else 68.0,
        "power_total_pct": 100.0,
        "reason_code": "UPWARD_FORCE_DOMINATES" if direction == "UP" else "DOWNWARD_FORCE_DOMINATES",
        "context": {"market_regime": "TREND", "data_quality_ok": True, "feed_stale": False},
        "regime_diagnostics": {"market_regime": "TREND", "latest_tick_age_seconds": 1.5},
        "shadow_only": False,
    }


def _shoot_ctx(**overrides):
    base = {
        "bid": 99.9,
        "ask": 100.1,
        "news_state": "QUIET",
        "has_data": True,
        "data_quality_ok": True,
        "last_data_age_seconds": 1.2,
        "trade_lots": 1.0,
        "recent_volume": 500.0,
        "market_symbol": "GCZ6",
        "aggressive_trigger_confirmed": False,
    }
    base.update(overrides)
    return base


def test_crowd_v2_passive_road_warmup_cures_starvation_and_fails_closed_on_stale_tape(monkeypatch):
    shooting_team._ROAD_HISTORY.clear()
    fake_clock = [100_000.0]
    monkeypatch.setattr(shooting_team.time, "time", lambda: fake_clock[0])
    cfg = _shoot_cfg()

    # 1) Stale / bad-quality / uncrossed quotes on NEITHER cycles NEVER enter _ROAD_HISTORY
    shooting_team.plan_shot(
        _dual_smap(), _power_payload("NEITHER"), 100.0, 1.0, 0.2, cfg,
        entry_context=_shoot_ctx(last_data_age_seconds=999.0),
    )
    shooting_team.plan_shot(
        _dual_smap(), _power_payload("NEITHER"), 100.0, 1.0, 0.2, cfg,
        entry_context=_shoot_ctx(data_quality_ok=False),
    )
    shooting_team.plan_shot(
        _dual_smap(), _power_payload("NEITHER"), 100.0, 1.0, 0.2, cfg,
        entry_context=_shoot_ctx(bid=100.2, ask=100.0),
    )
    assert len(shooting_team._ROAD_HISTORY) == 0

    # 2) Sub-second duplicate NEITHER calls in one cycle only record ONE sample
    shooting_team.plan_shot(
        _dual_smap(140.0, 160.0), _power_payload("NEITHER"), 100.0, 1.0, 0.2, cfg,
        entry_context=_shoot_ctx(),
    )
    shooting_team.plan_shot(
        _dual_smap(140.0, 160.0), _power_payload("NEITHER"), 100.0, 1.0, 0.2, cfg,
        entry_context=_shoot_ctx(),
    )
    assert len(shooting_team._ROAD_HISTORY) == 1
    assert shooting_team._ROAD_HISTORY[-1][1] == pytest.approx(150.0)

    # 3) Walk 10 more live minutes (NEITHER + news WARNING cycles) -> 11 passive samples
    for step in range(10):
        fake_clock[0] += 60.0
        ns = "WARNING" if step % 3 == 0 else "QUIET"
        pw = _power_payload("UP") if ns == "WARNING" else _power_payload("NEITHER")
        out_w = shooting_team.plan_shot(
            _dual_smap(145.0, 155.0), pw, 100.0, 1.0, 0.2, cfg,
            entry_context=_shoot_ctx(news_state=ns),
        )
        assert out_w["shot"] == "WAIT"
    assert len(shooting_team._ROAD_HISTORY) == 11

    # 4) First live POWER=UP cycle on minute 12 hits 12/12 and IMMEDIATELY passes Q2 -> GO!
    fake_clock[0] += 60.0
    out_go = shooting_team.plan_shot(
        _dual_smap(165.0, 155.0), _power_payload("UP"), 100.0, 1.0, 0.2, cfg,
        entry_context=_shoot_ctx(),
    )
    assert out_go["shot"] == "GO", out_go
    assert out_go["side"] == "BUY"
    assert any("crowd v2" in w.lower() for w in out_go["why"])
    assert len(shooting_team._ROAD_HISTORY) == 12
    shooting_team._ROAD_HISTORY.clear()


def test_step5_resolves_dunder_main_and_enables_async_ai_and_fast_wake(monkeypatch):
    fake_dunder_main = ModuleType("__main__")
    events = []
    wake_checks = [0]

    def set_async_loop_mode(enabled: bool):
        events.append(("async_mode", bool(enabled)))

    def check_fast_burst_wakeup() -> bool:
        wake_checks[0] += 1
        return True

    def fake_pipeline():
        events.append(("pipeline_tick", None))

    fake_pipeline.__module__ = "__main__"
    fake_dunder_main.set_async_loop_mode = set_async_loop_mode
    fake_dunder_main.check_fast_burst_wakeup = check_fast_burst_wakeup
    fake_dunder_main.run_pipeline = fake_pipeline

    monkeypatch.delitem(sys.modules, "main", raising=False)
    monkeypatch.setitem(sys.modules, "__main__", fake_dunder_main)
    monkeypatch.setattr(s5.time, "sleep", lambda _s: None)
    monkeypatch.setattr(config, "FAST_BURST_WATCH_SECONDS", 10, raising=False)

    mon = s5.TradeMonitor(poll_seconds=60)
    mon.run_loop(fake_pipeline, max_iterations=2)

    assert events[0] == ("async_mode", True)
    assert events[-1] == ("async_mode", False)
    assert events.count(("pipeline_tick", None)) == 2
    assert wake_checks[0] == 1


def test_run_step3_async_on_wait_but_live_go_wakes_throttle_and_joins_inflight_ai(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "_AI_STATE_FILE", None)
    monkeypatch.setattr(main, "_LAST_AI_CALL", 0.0)
    monkeypatch.setattr(main, "_STANDING_AI_SLIP", None)
    monkeypatch.setattr(config, "AI_MIN_SIGNAL_STRENGTH", 6.0, raising=False)
    monkeypatch.setattr(config, "AI_MIN_INTERVAL_MINUTES", 3.0, raising=False)
    monkeypatch.setattr(config, "AI_MAX_CALLS_PER_DAY", 500, raising=False)
    monkeypatch.setattr(config, "AI_GO_REASK_MIN_SECONDS", 15.0, raising=False)

    clock = [50_000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])

    def _snap(shot_state="WAIT", pdir="NEITHER", action_side="BUY"):
        return SimpleNamespace(
            signal_strength=22.0,
            regime="TREND",
            price=4200.0,
            volatility=SimpleNamespace(atr=3.0),
            power={
                "direction": pdir,
                "reason_code": "UPWARD_FORCE_DOMINATES" if pdir == "UP" else "NO_DOMINANT_FORCE",
            },
            shot=(
                {"shot": "GO", "side": action_side, "entry_style": "AGGRESSIVE_MARKET"}
                if shot_state == "GO" else {"shot": "WAIT"}
            ),
            order_flow=SimpleNamespace(level2_bid_depth=60.0, level2_ask_depth=60.0),
            level3=SimpleNamespace(aggressive_buy_volume=35.0, aggressive_sell_volume=10.0, ofi=20.0),
        )

    calls = [0]

    def decide_seq(_self, snap):
        calls[0] += 1
        if calls[0] == 1:
            return s3.Decision(action="HOLD", confidence=20.0, rationale="quiet tape", model="gemini-3.6-flash")
        return s3.Decision(action="BUY", confidence=71.0, rationale="A2 breakout confirmed", model="gemini-3.6-flash")

    monkeypatch.setattr(s3.AIDecisionEngine, "decide", decide_seq)

    # 1) Non-async call #1 on WAIT/NEITHER -> HOLD (records _LAST_AI_CALL = 50_000)
    main.set_async_loop_mode(False)
    d1 = main.run_step3(_snap(shot_state="WAIT", pdir="NEITHER"))
    assert d1.action == "HOLD" and d1.model == "gemini-3.6-flash"
    assert calls[0] == 1

    # 2) 25s later on WAIT -> still throttled (protects quota)
    clock[0] = 50_025.0
    d_wait = main.run_step3(_snap(shot_state="WAIT", pdir="UP"))
    assert d_wait.action == "HOLD" and d_wait.model == "none-throttled"

    # 3) 25s later in --loop mode on a LIVE Shooting=GO (e.g. A2 burst!) -> bypasses
    # stale HOLD throttle AND evaluates synchronously instead of none-async-pending!
    main.set_async_loop_mode(True)
    try:
        d_go = main.run_step3(_snap(shot_state="GO", pdir="UP", action_side="BUY"))
        assert d_go.action == "BUY"
        assert d_go.confidence == pytest.approx(71.0)
        assert d_go.model == "gemini-3.6-flash"
        assert calls[0] == 2
    finally:
        main.set_async_loop_mode(False)


def test_gemini_lowercase_thinking_38_minimal_coercion_slow_demotion_and_no_false_key_lockout(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(s3, "_KEY_STATE_FILE", None)
    s3.AIDecisionEngine._UNAVAILABLE_MODELS.clear()
    s3.AIDecisionEngine._MODEL_SLOW_STRIKES.clear()
    s3.AIDecisionEngine._MODEL_DEMOTED_UNTIL.clear()

    # A) Retired gemini-2.5-flash pruned from .env GEMINI_MODELS automatically
    monkeypatch.setenv("GEMINI_MODELS", "gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash-lite,gemini-2.5-flash")
    monkeypatch.setenv("GEMINI_THINKING_LEVEL", "LOW")
    config._refresh()
    assert "gemini-2.5-flash" not in config.GEMINI_MODELS
    assert config.GEMINI_MODELS == ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"]
    assert config.GEMINI_THINKING_LEVEL == "low"

    # B) ThinkingConfig receives lowercase "low" (and coerces "minimal" -> "low" on 3.8-flash)
    recorded_thinking = []

    class FakeThinkingConfig:
        def __init__(self, *, thinking_level: str):
            if thinking_level not in ("minimal", "low", "medium", "high"):
                raise ValueError(f"invalid case: {thinking_level}")
            self.thinking_level = thinking_level
            recorded_thinking.append(thinking_level)

    class FakeTypes:
        ThinkingConfig = FakeThinkingConfig

        @staticmethod
        def HttpRetryOptions(attempts=1):
            return {"attempts": attempts}

        @staticmethod
        def HttpOptions(**kw):
            return kw

        @staticmethod
        def AutomaticFunctionCallingConfig(disable=True):
            return {"disable": disable}

        @staticmethod
        def GenerateContentConfig(**kw):
            return kw

    class FakeModels:
        def __init__(self, sink):
            self._sink = sink

        def generate_content(self, *, model, contents, config):
            self._sink.append((model, config))
            return SimpleNamespace(text='{"action":"HOLD","confidence":15,"rationale":"ok"}')

    captured_calls = []

    class FakeGenAI:
        types = FakeTypes

        class Client:
            def __init__(self, api_key, http_options=None):
                self.models = FakeModels(captured_calls)

            def close(self):
                pass

    monkeypatch.setattr(s3, "_GENAI_SDK", "google-genai")
    monkeypatch.setattr(s3, "genai", FakeGenAI)
    monkeypatch.setattr(s3, "_modern_genai", FakeGenAI, raising=False)
    monkeypatch.setitem(sys.modules, "google", SimpleNamespace(genai=FakeGenAI))
    monkeypatch.setitem(sys.modules, "google.genai", SimpleNamespace(types=FakeTypes))

    monkeypatch.setattr(config, "GEMINI_THINKING_LEVEL", "minimal", raising=False)
    eng = s3.AIDecisionEngine(api_keys=["VALID_KEY_01"], model="gemini-3.8-flash")
    eng._call_with_key("VALID_KEY_01", "gemini-3.8-flash", "hello")
    eng._call_with_key("VALID_KEY_01", "gemini-3.6-flash", "hello")
    assert recorded_thinking == ["low", "minimal"]

    # C) Slice timeouts NEVER poison healthy API keys and return none-timeout-budget on last model;
    #    3 slow strikes demote congested front model behind fast fallback!
    monkeypatch.setattr(config, "GEMINI_MODELS", ["gemini-3.8-flash", "gemini-3.5-flash-lite"], raising=False)
    monkeypatch.setattr(config, "AI_MODEL_SLOW_STRIKES", 2, raising=False)
    monkeypatch.setattr(config, "AI_MODEL_DEMOTE_SECONDS", 600.0, raising=False)
    monkeypatch.setenv("AI_DECIDE_MAX_TOTAL_SECONDS", "20.0")

    order_seen = []

    def fake_bounded_timeout_all(self_eng, key, model, prompt, timeout_s):
        order_seen.append(model)
        raise s3._Step3BudgetTimeoutError(f"{model} exceeded {timeout_s:.1f}s slice")

    eng_to = s3.AIDecisionEngine(api_keys=["HEALTHY_KEY_ABC"])
    eng_to._check_cache = lambda _s: None
    eng_to.build_prompt = lambda _s: "prompt"
    monkeypatch.setattr(s3.AIDecisionEngine, "_invoke_bounded", fake_bounded_timeout_all)

    dec_to = eng_to.decide(SimpleNamespace(price=4200.0, level3=None, order_flow=None))
    assert dec_to.model == "none-timeout-budget"
    assert not (tmp_path / "gemini_keys_state.json").exists()

    # Second slow strike demotes gemini-3.8-flash so cycle #3 calls gemini-3.5-flash-lite FIRST!
    order_seen.clear()

    def fake_bounded_38_slow_35_fast(self_eng, key, model, prompt, timeout_s):
        order_seen.append(model)
        if model == "gemini-3.8-flash":
            raise s3._Step3BudgetTimeoutError("3.8 slow")
        return '{"action":"BUY","confidence":69.0,"rationale":"lite fast"}'

    monkeypatch.setattr(s3.AIDecisionEngine, "_invoke_bounded", fake_bounded_38_slow_35_fast)
    dec_c2 = eng_to.decide(SimpleNamespace(price=4200.0, level3=None, order_flow=None))
    assert dec_c2.action == "BUY" and dec_c2.model == "gemini-3.5-flash-lite"
    assert order_seen == ["gemini-3.8-flash", "gemini-3.5-flash-lite"]

    # Cycle #3: gemini-3.8-flash is now demoted -> goes STRAIGHT to gemini-3.5-flash-lite!
    order_seen.clear()
    dec_c3 = eng_to.decide(SimpleNamespace(price=4200.0, level3=None, order_flow=None))
    assert dec_c3.action == "BUY" and dec_c3.model == "gemini-3.5-flash-lite"
    assert order_seen == ["gemini-3.5-flash-lite"]

    s3.AIDecisionEngine._MODEL_SLOW_STRIKES.clear()
    s3.AIDecisionEngine._MODEL_DEMOTED_UNTIL.clear()


def test_pytest_and_dynamic_data_dir_never_pollute_live_data_or_logs(monkeypatch, tmp_path):
    import hashlib

    live_data = Path(__file__).resolve().parent / "data"
    live_logs = Path(__file__).resolve().parent / "logs"

    def _dir_fingerprint(folder: Path) -> dict:
        if not folder.exists():
            return {}
        snap = {}
        for fp in sorted(folder.iterdir()):
            if fp.is_file():
                snap[fp.name] = (fp.stat().st_size, hashlib.sha256(fp.read_bytes()).hexdigest())
            else:
                snap[fp.name] = ("dir",)
        return snap

    before_data = _dir_fingerprint(live_data)
    before_logs = _dir_fingerprint(live_logs)

    custom_data = tmp_path / "custom_runtime_data"
    monkeypatch.setattr(config, "DATA_DIR", custom_data)

    # TradeGuard, trade_history, PositionManager, maintenance, main._ai_state_path follow config.DATA_DIR
    tg = trade_guard.TradeGuard(cooldown_minutes=5, max_trades_per_day=10)
    tg.record_trade(datetime(2099, 12, 31, 12, 0, tzinfo=timezone.utc))
    assert (custom_data / "trade_guard_state.json").exists()

    assert Path(trade_history.memory_path()).parent == custom_data
    assert Path(trade_history.tca_path()).parent == custom_data

    maintenance._set_last_run("2099-12-31")
    assert json.loads((custom_data / "maintenance_state.json").read_text(encoding="utf-8"))["date"] == "2099-12-31"

    pm = position_manager.PositionManager(mt5_module=None)
    pm._save_state()
    assert (custom_data / "pm_state.json").exists()

    monkeypatch.setattr(main, "_AI_STATE_FILE", None)
    assert main._ai_state_path() == custom_data / "ai_calls.json"

    # Live repo data/ and logs/ remain byte-for-byte untouched whether empty or populated
    assert _dir_fingerprint(live_data) == before_data
    assert _dir_fingerprint(live_logs) == before_logs

def test_budapest_cet_winter_utc_plus_1_vs_cest_summer_utc_plus_2():
    winter_ctx = session.session_context(datetime(2026, 10, 28, 13, 53, tzinfo=timezone.utc))
    summer_ctx = session.session_context(datetime(2026, 7, 15, 13, 53, tzinfo=timezone.utc))
    assert winter_ctx["time_of_day_budapest"] == "14:53 (Budapest, UTC+1)"
    assert summer_ctx["time_of_day_budapest"] == "15:53 (Budapest, UTC+2)"
