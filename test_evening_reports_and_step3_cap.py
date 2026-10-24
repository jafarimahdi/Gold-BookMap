"""Regression tests for evening report fixes (Desks 3/6 + Audit Test 17)
and the bounded fail-closed STEP 3 total wait-time cap."""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import audit_day
import config
import decision_ledger
import shooting_team
import step3_ai_decision as s3

REPO_ROOT = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# 1. Config + Desk 6 (tf_robot_replay.py) timeframe & .env isolation
# ---------------------------------------------------------------------------

def test_config_timeframe_m8_and_custom_minutes(monkeypatch):
    try:
        monkeypatch.delenv("POWER_BAR_SECONDS", raising=False)
        monkeypatch.setenv("TIMEFRAME", "M8")
        config._refresh()
        assert config.BASE_TF_MINUTES == 8
        assert config.POWER_BAR_SECONDS == 480

        monkeypatch.setenv("TIMEFRAME", "M7")
        config._refresh()
        assert config.BASE_TF_MINUTES == 7
        assert config.POWER_BAR_SECONDS == 420

        monkeypatch.setenv("TIMEFRAME", "BOGUS")
        config._refresh()
        assert config.BASE_TF_MINUTES == 5
        assert config.POWER_BAR_SECONDS == 300
    finally:
        monkeypatch.undo()
        config._refresh()


def test_tf_robot_replay_run_child_beats_dotenv_override(tmp_path):
    """Regression for 2026-10-23 Desk 6 identical-rows bug: .env with
    TIMEFRAME=M3 and A1_BASE_TF_MINUTES=3 must not clobber child universes."""
    (tmp_path / ".env").write_text(
        "TIMEFRAME=M3\n"
        "A1_BASE_TF_MINUTES=3\n"
        "POWER_BAR_SECONDS=180\n"
        "POWER_V2_MEMORY_ENABLED=1\n"
        "AGGRESSIVE_ENTRY_ENABLED=1\n"
        "TRADING_ENABLED=1\n"
        "ALLOW_LIVE_TRADING=1\n",
        encoding="utf-8",
    )
    brain = tmp_path / "probe_brain.py"
    brain.write_text(
        "import json, os, config\n"
        "from types import SimpleNamespace\n"
        "A1_ENV = int(os.environ.get('A1_BASE_TF_MINUTES', '0'))\n"
        "def analyze_market(md, now=None):\n"
        "    return SimpleNamespace(\n"
        "        notes=[],\n"
        "        payload={\n"
        "            'a1_env': A1_ENV,\n"
        "            'timeframe': config.TIMEFRAME,\n"
        "            'base_tf': config.BASE_TF_MINUTES,\n"
        "            'power_bar_s': config.POWER_BAR_SECONDS,\n"
        "            'mem': config.POWER_V2_MEMORY_ENABLED,\n"
        "            'aggr': config.AGGRESSIVE_ENTRY_ENABLED,\n"
        "            'trading': config.TRADING_ENABLED,\n"
        "            'live': config.ALLOW_LIVE_TRADING,\n"
        "        },\n"
        "    )\n"
        "def snapshot_to_json(snap):\n"
        "    return json.dumps(snap.payload)\n",
        encoding="utf-8",
    )
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps({
            "cycles": [{
                "i": 0,
                "now": "2026-10-23T10:00:00+00:00",
                "bucket": 1,
                "market_data": {"price": 2700.0},
            }]
        }),
        encoding="utf-8",
    )
    clean_env = dict(os.environ, PYTHONPATH=str(REPO_ROOT))
    for k in ("TIMEFRAME", "A1_BASE_TF_MINUTES", "POWER_BAR_SECONDS",
              "POWER_V2_MEMORY_ENABLED", "AGGRESSIVE_ENTRY_ENABLED"):
        clean_env.pop(k, None)

    for tf in (1, 3, 5, 8, 10):
        out = tmp_path / f"out_{tf}.jsonl"
        r = subprocess.run(
            [sys.executable, str(REPO_ROOT / "tf_robot_replay.py"),
             "--child", str(tf), str(brain), str(plan), str(out)],
            cwd=tmp_path,
            env=clean_env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert r.returncode == 0, r.stderr
        rec = json.loads(out.read_text(encoding="utf-8").splitlines()[0])
        snap = rec["snap"]
        assert snap["a1_env"] == tf
        assert snap["timeframe"] == f"M{tf}"
        assert snap["base_tf"] == tf
        assert snap["power_bar_s"] == tf * 60
        assert snap["mem"] is False
        assert snap["aggr"] is False
        assert snap["trading"] is False
        assert snap["live"] is False


# ---------------------------------------------------------------------------
# 2. Desk 3 (decision_ledger.py) dynamic confidence gate label
# ---------------------------------------------------------------------------

def test_decision_ledger_conf_gate_uses_day_run_config_not_hardcoded_50(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFIDENCE_THRESHOLD", "70")
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "run_config_20261023.json").write_text(
        json.dumps({"CONFIDENCE_THRESHOLD": 35.0}), encoding="utf-8"
    )

    day_thr = decision_ledger._day_conf_gate("2026-10-23", data_dir=str(data_dir))
    assert day_thr == 35.0
    assert decision_ledger.classify(
        "SKIPPED — confidence below threshold", "SKIPPED", conf_gate=day_thr
    ) == "CONF GATE (<35%)"

    # Explicit threshold inside the row's own reason wins over run_config
    assert decision_ledger.classify(
        "confidence 29.0% below threshold 42.5%", "SKIPPED", conf_gate=day_thr
    ) == "CONF GATE (<42.5%)"

    # Historical day with no run_config never invents "<50%" or reads live .env
    missing_thr = decision_ledger._day_conf_gate("2026-09-01", data_dir=str(data_dir))
    assert missing_thr is None
    assert decision_ledger.classify(
        "confidence below threshold", "SKIPPED", conf_gate=missing_thr
    ) == "CONF GATE"

    # Malformed / boolean run_config values fail safely to generic "CONF GATE"
    (data_dir / "run_config_20261024.json").write_text(
        json.dumps({"CONFIDENCE_THRESHOLD": True}), encoding="utf-8"
    )
    assert decision_ledger._day_conf_gate("2026-10-24", data_dir=str(data_dir)) is None


# ---------------------------------------------------------------------------
# 3. Shooting receipt stamps + Audit Test 17 (real paid decisions + A2 census)
# ---------------------------------------------------------------------------

def _valid_power(direction="UP"):
    return {
        "team": "POWER",
        "version": "2.2.0",
        "timeframe": "M3",
        "direction": direction,
        "up_power_pct": 72.0 if direction == "UP" else 28.0,
        "down_power_pct": 28.0 if direction == "UP" else 72.0,
        "power_total_pct": 100.0,
        "reason_code": "UPWARD_FORCE_DOMINATES" if direction == "UP" else "DOWNWARD_FORCE_DOMINATES",
        "context": {"market_regime": "TREND", "data_quality_ok": True, "feed_stale": False},
        "regime_diagnostics": {"market_regime": "TREND", "latest_tick_age_seconds": 1.0},
        "shadow_only": False,
    }


def test_shooting_team_stamps_aggressive_trigger_receipt_on_wait_and_go():
    # Early _wait path (POWER NEITHER) preserves trigger receipt once at top level
    wait_plan = shooting_team.plan_shot(
        {}, {"direction": "NEITHER"}, 2700.0, 3.0, spread=0.2,
        entry_context={
            "aggressive_trigger_confirmed": False,
            "aggressive_trigger_reason": "lane disabled",
        },
    )
    assert wait_plan["shot"] == "WAIT"
    assert wait_plan["aggressive_trigger_confirmed"] is False
    assert wait_plan["aggressive_trigger_reason"] == "lane disabled"
    assert "aggressive_trigger_confirmed" not in wait_plan["visibility"]

    # Direct callers without trigger keys in entry_context stay untouched
    plain_wait = shooting_team.plan_shot({}, {"direction": "NEITHER"}, 2700.0, 3.0, spread=0.2)
    assert "aggressive_trigger_confirmed" not in plain_wait

    # Warm road history and verify GO path stamps receipt too
    shooting_team._ROAD_HISTORY.clear()
    for _ in range(15):
        shooting_team._crowd_v2(30.0, 10.0, 0.1, 200.0, SimpleNamespace())
    smap = {"above": [{
        "price": 2705.0, "size": 30.0, "doors_between": 1,
        "lots_between": 10.0, "biggest_in_path": None, "road_clear": True,
    }]}
    cfg = SimpleNamespace(
        LIMIT_ORDER_ENABLED=False, A2_BURST_SL_PTS=1.8, A2_BURST_TP_PTS=1.8,
        A2_MIN_RR=1.0, LOT_SIZE=0.1,
    )
    base_ctx = {
        "bid": 2699.9, "ask": 2700.1, "recent_volume": 200.0, "trade_lots": 0.1,
        "news_state": "QUIET", "data_quality_ok": True,
        "has_data": True, "last_data_age_seconds": 1.0,
    }
    # Late blocked WAIT path (LIMIT_ORDER_ENABLED=False + trigger False) also stamps receipt
    blocked_wait = shooting_team.plan_shot(
        smap, _valid_power("UP"), 2700.0, 3.0, spread=0.2, config=cfg,
        entry_context={
            **base_ctx,
            "aggressive_trigger_confirmed": False,
            "aggressive_trigger_reason": "A2 rails: eye 1 not confirmed",
        },
    )
    assert blocked_wait["shot"] == "WAIT"
    assert blocked_wait["aggressive_trigger_confirmed"] is False
    assert blocked_wait["aggressive_trigger_reason"] == "A2 rails: eye 1 not confirmed"

    go_plan = shooting_team.plan_shot(
        smap, _valid_power("UP"), 2700.0, 3.0, spread=0.2, config=cfg,
        entry_context={
            **base_ctx,
            "aggressive_trigger_confirmed": True,
            "aggressive_trigger_reason": "A2 burst confirmed",
            "a2_burst": True,
        },
    )
    assert go_plan["shot"] == "GO"
    assert go_plan["aggressive_trigger_confirmed"] is True
    assert go_plan["aggressive_trigger_reason"] == "A2 burst confirmed"
    assert go_plan["a2_burst"] is True


def test_audit_day_test17_real_paid_decisions_and_a2_stamps(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setattr(audit_day, "DATA_DIR", lambda: data_dir)
    (data_dir / "ai_calls.json").write_text(
        json.dumps({"date": "2026-10-23", "calls": 3}), encoding="utf-8"
    )

    log_txt = "\n".join([
        "2026-10-23 10:00:01,000 INFO [main] STEP 2  MARKET ANALYSIS -> OK -> BUY (strength 14.2, confidence 48.0)",
        "2026-10-23 10:00:03,000 INFO [step3] STEP 3: Gemini -> HOLD @ 55.0% (key #1, gemini-3.1-flash-lite)",
        "2026-10-23 10:00:03,010 INFO [main] STEP 3  AI DECISION -> HOLD @ 55.0% | wait",
        "2026-10-23 10:00:03,020 INFO [main] A2 LANE (opsfix3) -> enabled=True fires 0/100 today | eye1 lean: none",
        "2026-10-23 10:03:01,000 INFO [main] STEP 2  MARKET ANALYSIS -> OK -> BUY (strength 8.0, confidence 44.0)",
        "2026-10-23 10:03:01,005 INFO [main] STEP 3  AI DECISION -> HOLD (AI throttled, 2 min until next call)",
        "2026-10-23 10:03:01,010 INFO [main] A2 LANE (opsfix3) -> enabled=True fires 0/100 today | eye1 lean: none",
        "2026-10-23 10:06:01,000 INFO [main] STEP 2  MARKET ANALYSIS -> OK -> SELL (strength 16.0, confidence 52.0)",
        "2026-10-23 10:06:01,005 INFO [step3] STEP 3: Using cached AI decision (age 120s, price diff 0.01%)",
        "2026-10-23 10:06:01,010 INFO [main] STEP 3  AI DECISION -> HOLD @ 55.0% | cached",
        "2026-10-23 10:09:01,000 INFO [step3] STEP 3: Gemini -> BUY @ 62.5% (key #1, gemini-3.1-flash-lite)",
        "2026-10-23 10:09:01,010 INFO [main] STEP 3  AI DECISION -> BUY @ 62.5% | go",
    ])

    # A) Structured diary stamp present -> exact A2 gate count + paid Gemini count (2 live, 1 cached)
    diary_stamped = [{
        "voice_gain": 3.0,
        "memory_status": {"appended": True},
        "power_v2": {"reason_code": "UPWARD_FORCE_DOMINATES"},
        "shot": {
            "aggressive_trigger_confirmed": False,
            "aggressive_trigger_reason": "eye 1 (CVD lean) not confirmed",
        },
    }]
    rep, meta = audit_day.test_17_new_organs(dt.date(2026, 10, 23), diary_stamped, log_txt, [])
    body = "\n".join(rep.rows[0][3])
    assert rep.rows[0][0] == "PASS"
    assert meta["real_ai_calls"] == 2
    assert meta["a2_gate_runs"] == 1
    assert "real paid decisions 2 (3 Step-3 gate passes, 1 cached)" in body
    assert "MISSING:" not in body

    # B) Pre-stamp 2026-10-23 diary + A2 LANE log receipts -> honest log-receipt line, no false MISSING
    diary_legacy = [{
        "voice_gain": 3.0,
        "memory_status": {"appended": True},
        "power_v2": {"reason_code": "NEITHER"},
    }]
    rep2, meta2 = audit_day.test_17_new_organs(dt.date(2026, 10, 23), diary_legacy, log_txt, [])
    body2 = "\n".join(rep2.rows[0][3])
    assert rep2.rows[0][0] == "PASS"
    assert meta2["a2_gate_runs"] == 2
    assert "A2 fast lane (log receipt; pre-stamp diary): status logged 2x, FIRES 0" in body2
    assert "MISSING: aggressive_trigger" not in body2

    # C) Neither diary stamp nor log receipt on post-10-13 day -> still warns MISSING
    rep3, _ = audit_day.test_17_new_organs(dt.date(2026, 10, 23), diary_legacy, "empty log", [])
    body3 = "\n".join(rep3.rows[0][3])
    assert rep3.rows[0][0] == "WARN"
    assert "MISSING: aggressive_trigger stamps" in body3


# ---------------------------------------------------------------------------
# 4. Bounded fail-closed STEP 3 total wait-time cap
# ---------------------------------------------------------------------------

def test_step3_total_budget_default_and_invalid_env_fallback(monkeypatch):
    try:
        monkeypatch.delenv("AI_DECIDE_MAX_TOTAL_SECONDS", raising=False)
        config._refresh()
        assert config.AI_DECIDE_MAX_TOTAL_SECONDS == 25.0
        assert s3._decide_total_budget_seconds() == 25.0

        for bad in ("0", "-3", "nan", "inf", "-inf", "not-a-number", ""):
            monkeypatch.setenv("AI_DECIDE_MAX_TOTAL_SECONDS", bad)
            assert s3._decide_total_budget_seconds() == 25.0

        monkeypatch.setenv("AI_DECIDE_MAX_TOTAL_SECONDS", "999")
        assert s3._decide_total_budget_seconds() == 120.0

        monkeypatch.setenv("AI_DECIDE_MAX_TOTAL_SECONDS", "0.25")
        assert abs(s3._decide_total_budget_seconds() - 0.25) < 1e-9
    finally:
        monkeypatch.undo()
        config._refresh()


def test_step3_hung_call_capped_and_fails_closed_to_hold_never_fallback_buy(tmp_path, monkeypatch):
    """When Gemini exceeds AI_DECIDE_MAX_TOTAL_SECONDS, decide() must stop
    retrying further keys/models and return HOLD @ 0.0% (never firing a
    rule-based BUY/SELL fallback on a stale snapshot)."""
    monkeypatch.setattr(s3, "_KEY_STATE_FILE", tmp_path / "gemini_keys_state.json")
    monkeypatch.setattr(s3, "genai", object())
    monkeypatch.setenv("AI_DECIDE_MAX_TOTAL_SECONDS", "0.18")

    calls = []

    def hung_call(key, model, prompt):
        calls.append((key, model))
        time.sleep(1.5)
        return '{"action":"BUY","confidence":90,"rationale":"late"}'

    eng = s3.AIDecisionEngine(api_keys=["KEY_ONE_HUNG_0001", "KEY_TWO_OK_00002"], model="m1")
    eng._check_cache = lambda snap: None
    eng.build_prompt = lambda snap: "prompt"
    eng._call_with_key = hung_call
    # Plant a fallback that WOULD emit BUY if called -- budget timeout must NOT call it
    eng._fallback_decision = lambda snap: s3.Decision(
        action="BUY", confidence=75.0, rationale="stale fallback", model="fallback-rule"
    )

    t0 = time.monotonic()
    dec = eng.decide(SimpleNamespace(price=2700.0, level3=None, order_flow=None))
    elapsed = time.monotonic() - t0

    assert elapsed < 0.8, f"decide() failed to cap hung call: {elapsed:.2f}s"
    assert dec.action == "HOLD"
    assert dec.confidence == 0.0
    assert dec.model == "none-timeout-budget"
    assert len(calls) == 1, f"should not try key #2 after budget exhausted: {calls}"


def test_step3_fast_failover_succeeds_and_starved_key2_not_blamed(tmp_path, monkeypatch):
    state_file = tmp_path / "gemini_keys_state.json"
    monkeypatch.setattr(s3, "_KEY_STATE_FILE", state_file)
    monkeypatch.setattr(s3, "genai", object())

    # Subcase A: fast failover within budget succeeds on key #2
    monkeypatch.setenv("AI_DECIDE_MAX_TOTAL_SECONDS", "1.0")
    k1 = "FAST_FAIL_KEY_00001"
    k2 = "HEALTHY_KEY_0000002"

    def fast_failover(key, model, prompt):
        if key == k1:
            raise ConnectionError("instant reset")
        return '{"action":"BUY","confidence":68.0,"rationale":"clean failover"}'

    eng = s3.AIDecisionEngine(api_keys=[k1, k2], model="m1")
    eng._check_cache = lambda snap: None
    eng.build_prompt = lambda snap: "prompt"
    eng._update_cache = lambda snap, d: None
    eng._call_with_key = fast_failover

    dec = eng.decide(SimpleNamespace(price=2700.0, level3=None, order_flow=None))
    assert dec.action == "BUY"
    assert abs(dec.confidence - 68.0) < 1e-6

    # Subcase B: key #1 eats ~85% of budget then errors; key #2 gets a tiny sliver
    # and hits budget cap -> fails closed to HOLD without blaming key #2!
    state_file.unlink(missing_ok=True)
    monkeypatch.setenv("AI_DECIDE_MAX_TOTAL_SECONDS", "0.25")
    slow_k1 = "SLOW_EATER_KEY_0001"
    innocent_k2 = "INNOCENT_KEY_000002"

    def slow_then_sliver(key, model, prompt):
        if key == slow_k1:
            time.sleep(0.20)
            raise ConnectionError("slow socket reset")
        time.sleep(0.30)
        return '{"action":"HOLD","confidence":10,"rationale":"ok"}'

    eng2 = s3.AIDecisionEngine(api_keys=[slow_k1, innocent_k2], model="m1")
    eng2._check_cache = lambda snap: None
    eng2.build_prompt = lambda snap: "prompt"
    eng2._call_with_key = slow_then_sliver

    dec2 = eng2.decide(SimpleNamespace(price=2700.0, level3=None, order_flow=None))
    assert dec2.action == "HOLD"
    assert dec2.model == "none-timeout-budget"

    st = json.loads(state_file.read_text(encoding="utf-8"))
    fails = st.get("fails") or {}
    assert fails.get(s3._key_id(slow_k1)) == 1
    assert fails.get(s3._key_id(innocent_k2), 0) == 0
