"""Regression tests for 2026-10-26 #1-#12 scalper & audit fixes."""

from __future__ import annotations

import datetime as dt
import json
import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import aggressive_trigger
import audit_day
import config
import judge_panel as jp_mod
import main as main_mod
import position_manager
import step2_market_analysis as s2
import step3_ai_decision as s3
import step5_monitoring
from tools import dashboard, report_office_bundle


def test_fix1_order_flow_window_tracks_timeframe_and_keeps_session_cvd(monkeypatch):
    try:
        monkeypatch.setenv("TIMEFRAME", "M3")
        monkeypatch.delenv("POWER_BAR_SECONDS", raising=False)
        monkeypatch.delenv("ORDER_FLOW_WINDOW_SECONDS", raising=False)
        config._refresh()
        assert config.ORDER_FLOW_WINDOW_SECONDS == 180

        monkeypatch.setenv("TIMEFRAME", "M5")
        config._refresh()
        assert config.ORDER_FLOW_WINDOW_SECONDS == 300

        monkeypatch.setenv("TIMEFRAME", "M3")
        config._refresh()

        base = dt.datetime(2026, 10, 26, 12, 0, 0, tzinfo=dt.timezone.utc)
        ticks = []
        # 2 hours of balanced 50/50 background tape (10 lots BUY, 10 lots SELL)
        for idx in range(200):
            ts = base + dt.timedelta(seconds=idx * 30)
            ticks.append({"timestamp": ts.isoformat(), "price": 5050.0, "volume": 10.0, "side": "BUY"})
            ticks.append({"timestamp": ts.isoformat(), "price": 5049.9, "volume": 10.0, "side": "SELL"})
        # Final 2.5 minutes: strong institutional BUY burst (within last 180s)
        burst_start = base + dt.timedelta(hours=2)
        for idx in range(20):
            ts = burst_start + dt.timedelta(seconds=idx * 6)
            ticks.append({"timestamp": ts.isoformat(), "price": 5052.0, "volume": 15.0, "side": "BUY"})
        ticks.append({
            "timestamp": (burst_start + dt.timedelta(seconds=125)).isoformat(),
            "price": 5051.9, "volume": 5.0, "side": "SELL",
        })

        ofa = s2.OrderFlowAnalyzer()
        metrics = ofa.analyze_tick_data(ticks)
        # Recent 3m buying pressure must reflect the live burst (>95%), NOT the 2h 50/50 history
        assert metrics.buying_pressure > 95.0
        assert metrics.selling_pressure < 5.0
        # Session CVD must still equal total session delta (20*15 - 5 = +295, since 200*10 - 200*10 = 0)
        assert abs(metrics.cvd - 295.0) < 1e-6
    finally:
        monkeypatch.undo()
        config._refresh()


def test_fix1b_burst_eyes_resets_hold_clock_on_sign_flip():
    eyes = aggressive_trigger.BurstEyes()
    t0 = dt.datetime(2026, 10, 26, 14, 0, 0, tzinfo=dt.timezone.utc)
    # 70s of strong SELL (-0.8), then sudden single huge BUY (+2.0) flipping EMA > +0.25 at t0+75s
    for s in (0, 20, 40, 65):
        eyes.sample(t0 + dt.timedelta(seconds=s), -0.8)
    eyes.sample(t0 + dt.timedelta(seconds=75), 2.0)
    # Only 5 seconds after sign flip, UP lean must NOT claim 75s held!
    res = eyes.lean(t0 + dt.timedelta(seconds=80), min_abs=0.25, hold_s=60.0)
    assert res is None


def test_fix2_aggressive_spread_default_allows_normal_live_spread(monkeypatch):
    try:
        monkeypatch.delenv("AGGRESSIVE_SPREAD_MAX_PCT", raising=False)
        config._refresh()
        assert abs(config.AGGRESSIVE_SPREAD_MAX_PCT - 0.035) < 1e-9

        cfg = SimpleNamespace(
            AGGRESSIVE_ENTRY_ENABLED=True,
            AGGRESSIVE_SPREAD_MAX_PCT=config.AGGRESSIVE_SPREAD_MAX_PCT,
            AGGRESSIVE_FLOW_EDGE=2.0,
            AGGRESSIVE_MAX_TICK_AGE_SECONDS=60.0,
        )
        power_up = {
            "direction": "UP",
            "reason_code": "UPWARD_FORCE_DOMINATES",
            "gates": {"data_ok": True, "fresh_ticks": True, "enough_evidence": True},
        }
        flow = SimpleNamespace(buying_pressure=58.0, selling_pressure=42.0)

        # Normal $1.25 spread at $5050 (~0.0248%) -> CONFIRMED (used to fail at 0.015%)
        ok_res = aggressive_trigger.evaluate_aggressive_trigger(
            power=power_up,
            shot_context={
                "has_data": True, "data_quality_ok": True,
                "last_data_age_seconds": 1.0, "news_state": "QUIET",
                "bid": 5050.0, "ask": 5051.25,
            },
            config=cfg,
            price=5050.0,
            atr=4.0,
            order_flow=flow,
        )
        assert ok_res["confirmed"] is True

        # Wide $2.30 news spread at $5050 (~0.0455%) -> STILL BLOCKED
        wide_res = aggressive_trigger.evaluate_aggressive_trigger(
            power=power_up,
            shot_context={
                "has_data": True, "data_quality_ok": True,
                "last_data_age_seconds": 1.0, "news_state": "QUIET",
                "bid": 5050.0, "ask": 5052.30,
            },
            config=cfg,
            price=5050.0,
            atr=4.0,
            order_flow=flow,
        )
        assert wide_res["confirmed"] is False
        assert "spread" in wide_res["reason"]
    finally:
        monkeypatch.undo()
        config._refresh()


def test_fix3_gemini_models_chain_attempt_failover_and_404_memory(tmp_path, monkeypatch):
    try:
        monkeypatch.delenv("GEMINI_MODEL", raising=False)
        monkeypatch.delenv("GEMINI_MODELS", raising=False)
        config._refresh()
        assert config.GEMINI_MODEL == "gemini-3.8-flash"
        assert config.GEMINI_MODELS == [
            "gemini-3.8-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash-lite",
            "gemini-2.5-flash",
        ]

        # Explicit GEMINI_MODEL without GEMINI_MODELS promotes that model first
        monkeypatch.setenv("GEMINI_MODEL", "gemini-3.6-flash")
        config._refresh()
        assert config.GEMINI_MODELS[0] == "gemini-3.6-flash"

        # 404 model is remembered across cycles so later cycles skip it in 0 calls
        monkeypatch.setattr(s3, "_KEY_STATE_FILE", tmp_path / "keys.json")
        monkeypatch.setattr(s3, "genai", object())
        s3.AIDecisionEngine._UNAVAILABLE_MODELS.clear()
        monkeypatch.setenv("GEMINI_MODELS", "retired-ghost-model,gemini-3.8-flash")
        config._refresh()

        seen_models = []

        def fake_call(key, model, prompt):
            seen_models.append(model)
            if model == "retired-ghost-model":
                raise RuntimeError("404 Model retired-ghost-model not found")
            return '{"action":"BUY","confidence":66.0,"rationale":"live model ok"}'

        eng = s3.AIDecisionEngine(api_keys=["KEY_A_123456"])
        eng._check_cache = lambda snap: None
        eng.build_prompt = lambda snap: "prompt"
        eng._update_cache = lambda snap, d: None
        eng._call_with_key = fake_call

        d1 = eng.decide(SimpleNamespace(price=5050.0, level3=None, order_flow=None))
        assert d1.action == "BUY"
        assert d1.model == "gemini-3.8-flash"
        assert seen_models == ["retired-ghost-model", "gemini-3.8-flash"]

        seen_models.clear()
        d2 = eng.decide(SimpleNamespace(price=5051.0, level3=None, order_flow=None))
        assert d2.action == "BUY"
        assert seen_models == ["gemini-3.8-flash"]
    finally:
        s3.AIDecisionEngine._UNAVAILABLE_MODELS.clear()
        monkeypatch.undo()
        config._refresh()


def test_fix4_ai_interval_tracks_timeframe_and_failed_calls_do_not_burn_quota(tmp_path, monkeypatch):
    try:
        for tf, expected in (("M1", 1), ("M3", 3), ("M5", 5), ("M15", 15)):
            monkeypatch.setenv("TIMEFRAME", tf)
            monkeypatch.setenv("AI_MIN_INTERVAL_MINUTES", "auto")
            config._refresh()
            assert config.AI_MIN_INTERVAL_MINUTES == expected

        monkeypatch.setenv("TIMEFRAME", "M3")
        monkeypatch.delenv("AI_MIN_INTERVAL_MINUTES", raising=False)
        config._refresh()
        assert config.AI_MIN_INTERVAL_MINUTES == 3

        ai_file = tmp_path / "ai_calls.json"
        monkeypatch.setattr(main_mod, "_AI_STATE_FILE", ai_file)
        monkeypatch.setattr(main_mod, "_LAST_AI_CALL", 0.0)

        snap = SimpleNamespace(signal_strength=22.0)

        # 1) Timeout call must NOT increment ai_calls.json and must NOT lock out 3 full minutes
        monkeypatch.setattr(
            s3.AIDecisionEngine, "decide",
            lambda self, sn: s3.Decision(action="HOLD", confidence=0.0, rationale="timeout", model="none-timeout-budget"),
        )
        t_now = [10000.0]
        monkeypatch.setattr("time.time", lambda: t_now[0])
        res_to = main_mod.run_step3(snap)
        assert res_to.model == "none-timeout-budget"
        assert main_mod._ai_calls_today() == 0

        # 65s later (next 1m loop), robot can already retry instead of waiting 300s!
        t_now[0] = 10065.0
        monkeypatch.setattr(
            s3.AIDecisionEngine, "decide",
            lambda self, sn: s3.Decision(action="BUY", confidence=72.0, rationale="ok", model="gemini-3.8-flash"),
        )
        res_ok = main_mod.run_step3(snap)
        assert res_ok.action == "BUY"
        assert main_mod._ai_calls_today() == 1

        # Exact next M3 bar (~177s later with 2-3s loop jitter) is NOT throttled thanks to 5s jitter grace
        t_now[0] = 10065.0 + 177.0
        res_next_bar = main_mod.run_step3(snap)
        assert res_next_bar.action == "BUY"
        assert main_mod._ai_calls_today() == 2
    finally:
        monkeypatch.undo()
        config._refresh()


def test_fix5_6_8_judge_parsers_and_macro_dxy_complete_removal():
    notes = [
        "L3 large orders 14 OFI -62.5 -> SELL",
        "L3 large orders 9 OFI +41.0 -> BUY",
        "volume RoC +92% with price down -> bearish",
        "volume RoC +88% with price up -> bullish",
        "DXY rising 0.45%/5d (w 0.6)",
        "L3 whale RESISTANCE 3 walls 320.0 lots",
        "HIGH impact sentiment -0.65 weight 1.0 (boosted)",
        "MTF H1:DOWN M15:DOWN M5:UP",
        "near supply zone 5055.0",
        "near demand zone 5045.0",
    ]
    for parser in (s2.parse_judge_panel, jp_mod.parse_judge_panel):
        panel = parser(notes)
        by_name = {}
        for row in panel:
            by_name.setdefault(row["judge"], []).append(row["dir"])

        assert by_name["l3_large_ofi"] == [-1.0, 1.0]
        assert by_name["volume_roc"] == [-1.0, 1.0]
        assert "macro_dxy" not in by_name
        assert "whale_walls" not in by_name
        assert by_name["news_sentiment"] == [-1.0]
        assert by_name["mtf"] == [-1.0]
        # supply_demand deduplicated to at most 1 entry per snapshot
        assert len(by_name["supply_demand"]) == 1

    # Also verify audit_day fallback parser handles multi-token MTF, news_sentiment, and volume_roc
    ad_panel, src = audit_day.judge_panel({"notes": [
        "MTF H1:UP M15:DOWN M5:DOWN",
        "HIGH impact sentiment +0.55 weight 1.0 (boosted)",
        "volume RoC +90% with price down -> bearish",
    ]})
    assert src == "notes"
    assert dict((j, d) for j, d, _ in ad_panel) == {
        "mtf": -1.0,
        "news_sentiment": 1.0,
        "volume_roc": -1.0,
    }


def test_fix7_mt5_startup_warning_and_balance_deal_filter(monkeypatch, caplog):
    pm = position_manager.PositionManager.__new__(position_manager.PositionManager)
    pm._mt5 = SimpleNamespace()
    pm._state = {}
    pm._mt5_init_warn_ts = 0.0
    monkeypatch.setattr(config, "PM_ENABLE", True)
    monkeypatch.setattr(config, "TRADING_ENABLED", True)
    monkeypatch.setattr(config, "mt5_initialize", lambda _m: False)

    with caplog.at_level(logging.WARNING):
        pm.manage(SimpleNamespace())
    assert any(r.levelname == "WARNING" and "mt5.initialize() failed" in r.message for r in caplog.records)
    assert not any(r.levelname == "ERROR" for r in caplog.records)

    # But if an active position IS in memory, losing MT5 still logs ERROR
    caplog.clear()
    pm._state = {12345: SimpleNamespace()}
    with caplog.at_level(logging.WARNING):
        pm.manage(SimpleNamespace())
    assert any(r.levelname == "ERROR" and "active position" in r.message for r in caplog.records)

    # _mt5_truth ignores DEAL_TYPE_BALANCE (type=2) so DEMO deposit doesn't trigger opened=1 WARN
    fake_mt5 = SimpleNamespace(
        initialize=lambda: True,
        shutdown=lambda: None,
        positions_get=lambda: [],
        history_deals_get=lambda frm, to: [
            SimpleNamespace(type=2, entry=0, profit=10000.0, time=1761480000, position_id=0, ticket=1, symbol="", volume=0.0, price=0.0, commission=0.0, swap=0.0, comment="Initial balance"),
        ],
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)
    truth, err = audit_day._mt5_truth(dt.date(2026, 10, 26))
    assert err is None
    assert truth["deals_total"] == 0
    assert truth["opened"] == 0
    assert truth["balance_deals"] == 1


def test_fix9_council_shadow_frozen_feed_not_sticky(tmp_path):
    mem_dir = tmp_path / "data" / "power_v2_memory"
    mem_dir.mkdir(parents=True)
    day = "2026-10-26"
    t0 = dt.datetime(2026, 10, 26, 9, 0, tzinfo=dt.timezone.utc)
    rows = []
    # Bar 0 & Bar 1 repeat price with activity=0 (1 dead-feed bar), then 20 normal active bars
    for i in range(22):
        ts = (t0 + dt.timedelta(minutes=3 * i)).isoformat()
        ref = 5050.0 if i <= 1 else 5050.0 + i * 0.5
        act = 0.0 if i == 1 else 120.0
        rows.append(json.dumps({
            "phase": "FINAL",
            "timestamp_utc": ts,
            "symbol": "GCZ6",
            "reference_price": ref,
            "result": {
                "direction": "UP" if i % 2 == 0 else "NEITHER",
                "reason_code": "UPWARD_FORCE_DOMINATES",
                "up_power_pct": 68.0,
                "down_power_pct": 32.0,
                "activity": act,
                "input_judges": {"tape_speed": {"direction": "UP", "score": 0.7}},
            },
        }))
    (mem_dir / f"power_{day}.jsonl").write_text("\n".join(rows) + "\n", encoding="utf-8")
    (tmp_path / "data" / "decisions_log.csv").write_text(
        "timestamp,symbol,price,signal_direction,signal_strength,signal_confidence,ai_action,ai_confidence,ai_model,executed,reason\n"
        f"2026-10-26T09:12:00+00:00,GCZ6,5052.0,BUY,18.0,55.0,BUY,60.0,gemini-3.8-flash,False,test\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [sys.executable, "council_shadow.py", day, "--root", str(mem_dir)],
        capture_output=True, text=True, check=True,
    )
    # Only bar #1 at 09:03 was frozen (not all 21 remaining bars!), so the 09:12
    # decision never touches a dead-feed window (flagged == 0 -> no CAVEAT emitted!)
    assert "dead-feed" not in proc.stdout.lower()


def test_fix10_11_12_audit_pipeline_stats_data_quality_and_html_ui(tmp_path):
    # #10: test_3_pipeline returns real cycle & error counts even when FAIL
    log_txt = (
        "2026-10-26 10:00:00 INFO pipeline start\n"
        "2026-10-26 10:00:01 INFO STEP 1 -> OK\n"
        "2026-10-26 10:00:02 ERROR boom\n"
    )
    rep3, meta3 = audit_day.test_3_pipeline(log_txt, [{"x": 1}])
    assert rep3.rows[0][0] == "FAIL"
    assert meta3 == {"cycles": 1, "errors": 1, "warnings": 0}

    # #11: CME MBO split fills of different sizes or <15% equal-lot same-ms pass cleanly
    ts = dt.datetime(2026, 10, 26, 12, 0, 0, 123000, tzinfo=dt.timezone.utc)
    ticks = [
        (ts, 5050.0, 1.0),
        (ts, 5050.0, 2.0),
        (ts, 5050.0, 5.0),
    ] + [(ts + dt.timedelta(milliseconds=i), 5050.1, 1.0) for i in range(1, 50)]
    rep4, meta4 = audit_day.test_4_data_quality(dt.date(2026, 10, 26), ticks, "feed age 0.5s")
    assert rep4.rows[0][0] == "PASS"
    assert meta4["dup_pct"] == 0.0

    # #12: dashboard renders dynamic 17-test count (including Test 17 organs) & single By hand header
    keys_17 = ("alive", "feed", "pipe", "dq", "guards", "sig", "whatif", "teams", "judges",
               "cov", "ver", "lat", "money", "files", "hour", "cycle", "organs")
    reports_17 = {
        k: SimpleNamespace(rows=[("PASS", f"TEST {idx}", "question?", ["ok"])])
        for idx, k in enumerate(keys_17, 1)
    }
    html = dashboard.day_dashboard(
        dt.date(2026, 10, 26),
        reports_17,
        {"date": "2026-10-26", "checklist": ["1. Check chart"]},
        "raw report",
    )
    assert "All 17 tests" in html
    assert "#17" in html
    assert "<h2>By hand</h2>" not in html
    assert "By hand (saved on this PC only - no upload)" in html

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "day_report_2026-10-26.html").write_text("<html><body><div>sub</div></body></html>", encoding="utf-8")
    (data_dir / "report_2026-10-26.html").write_text("<html><body><div class=\"wrap\">main</div></body></html>", encoding="utf-8")
    report_office_bundle.inject_into_report_html(tmp_path, "2026-10-26")
    sub_after = (data_dir / "day_report_2026-10-26.html").read_text(encoding="utf-8")
    assert "Open Full Report (report_2026-10-26.html)" in sub_after
