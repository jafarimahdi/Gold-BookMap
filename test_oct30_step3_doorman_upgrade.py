"""Regression & institutional tests for 2026-10-30 Step 3 Gemini Doorman Upgrade (G-01..G-08)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import config
import main
import step2_market_analysis as s2
import step3_ai_decision as s3


@pytest.mark.parametrize("v6_teams", [False, True])
def test_prompt_compression_dedup_retired_filter_and_missing_context_delivered(
    monkeypatch, tmp_path, v6_teams
):
    expected_bars = 15 if not v6_teams else 20
    for _cfg_mod in {config, getattr(s2, "config", config), getattr(s3, "config", config)}:
        monkeypatch.setattr(_cfg_mod, "reload_env", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(_cfg_mod, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(_cfg_mod, "V6_4TEAMS_ENABLED", v6_teams, raising=False)
        monkeypatch.setattr(_cfg_mod, "AI_MAX_PROMPT_BARS", expected_bars, raising=False)
        monkeypatch.setattr(_cfg_mod, "AI_MAX_HEADLINES", 5, raising=False)
    monkeypatch.setattr(s2, "DATA_DIR", lambda: str(tmp_path), raising=False)

    now_dt = datetime(2026, 10, 30, 11, 15 if v6_teams else 0, 0, tzinfo=timezone.utc)
    # Seed trade_memory.json with 2 past closed trades + 1 future trade (must not leak)
    mem_payload = {
        "open": {},
        "closed": [
            {
                "ticket": "101",
                "side": "SELL",
                "pnl_pts": -2.4,
                "exit_reason": "SL_HIT",
                "closed_utc": "2026-10-30 10:15:00",
                "closed_ts": now_dt.timestamp() - 2700,
            },
            {
                "ticket": "102",
                "side": "BUY",
                "pnl_pts": 4.8,
                "exit_reason": "TP_HIT",
                "closed_utc": "2026-10-30 10:45:00",
                "closed_ts": now_dt.timestamp() - 900,
            },
            {
                "ticket": "999",
                "side": "BUY",
                "pnl_pts": 99.0,
                "exit_reason": "FUTURE_LEAK",
                "closed_utc": "2026-10-30 15:00:00",
                "closed_ts": now_dt.timestamp() + 14400,
            },
        ],
        "day": {},
    }
    (tmp_path / "trade_memory.json").write_text(json.dumps(mem_payload), encoding="utf-8")

    idx = pd.date_range("2026-10-30 09:01:00+00:00", periods=120, freq="1min")
    closes = [3985.0 + 0.08 * i for i in range(120)]
    candles = pd.DataFrame(
        {
            "open": [c - 0.05 for c in closes],
            "high": [c + 0.35 for c in closes],
            "low": [c - 0.30 for c in closes],
            "close": closes,
            "volume": [160.0] * 120,
        },
        index=idx,
    )
    snap = s2.analyze_market(
        {
            "symbol": "GCZ6.COMEX@RITHMIC",
            "data_symbol": "GCZ6.COMEX@RITHMIC",
            "data_market": "GC",
            "trade_symbol": "XAUUSD",
            "trade_market": "XAUUSD",
            "price": 3994.6,
            "bid": 3994.5,
            "ask": 3994.7,
            "cfd_bid": 3989.10,
            "cfd_ask": 3989.38,
            "volume": 5120.0,
            "timestamp": now_dt,
            "candles": candles,
            "order_book": {
                "bids": [(3994.5 - i * 0.1, 18.0) for i in range(25)],
                "asks": [(3994.7 + i * 0.1, 18.0) for i in range(25)],
            },
            "order_events": [
                {
                    "order_id": f"oid_{i}",
                    "type": "BID_NEW",
                    "side": "BID",
                    "price": 3994.4,
                    "size": 5.0,
                    "timestamp": now_dt.timestamp() - i,
                }
                for i in range(150)
            ],
            "news": {
                "headlines": [f"Gold macro update #{i}" for i in range(12)],
                "events": [{"title": f"Macro #{i}", "impact": "LOW", "minutes_to_event": 90 + i} for i in range(8)],
                "fetch_calendar": False,
            },
            "has_data": True,
            "data_quality_ok": True,
            "last_data_age_seconds": 0.4,
        },
        now=now_dt,
    )
    # Inject iceberg persistence metadata + one retired judge row to verify filtering
    snap.level3.iceberg_levels = {3994.4: 6, 3995.2: 2}
    snap.level3.iceberg_meta = {
        3994.4: {"refills": 6, "total_size": 180.0, "age_sec": 2400.0, "score": 132000.0},
        3995.2: {"refills": 2, "total_size": 20.0, "age_sec": 90.0, "score": 1200.0},
    }
    snap.judge_votes.append({"judge": "whale_walls", "direction": "SELL", "weight": 1.0})
    snap.notes.append("Whale wall resistance @3996.0 -> SELL")

    prompt = s3.AIDecisionEngine().build_prompt(snap)
    assert len(prompt) < 15000, f"Expected compressed prompt <15k chars, got {len(prompt)}"

    metrics_tail = prompt.split("MARKET ANALYSIS METRICS (JSON)")[1]
    payload = json.loads(metrics_tail[metrics_tail.index("{") :])

    # G-03: Duplicates & raw order_events bloat removed
    assert "power_v2" not in payload
    assert "power_legacy" not in payload
    assert "l3_summary" not in payload
    assert "order_events" not in payload.get("level3", {})

    # G-06a: Retired judges stripped + active_family_summary populated
    judges_seen = {j["judge"] for j in payload.get("judge_votes", [])}
    assert "whale_walls" not in judges_seen
    assert "poc_day" not in judges_seen
    assert "l3_aggr_limit" not in judges_seen
    assert "active_family_summary" in payload
    assert "agreeing_families_buy" in payload["active_family_summary"]
    assert not any("whale wall" in n.lower() for n in payload.get("notes", []))

    # G-06b: Configured OHLCV bars (default 15, or .env AI_MAX_PROMPT_BARS=20) + momentum summary present
    rb = payload.get("recent_bars", {})
    assert rb.get("count") == expected_bars
    assert len(rb.get("bars", [])) == expected_bars
    assert rb.get("up_bars", 0) == expected_bars
    assert rb.get("net_change_pts", 0.0) > 0.0

    # G-06c: Iceberg age_sec, score, side & INSTITUTIONAL tier preserved
    ice = payload["l3_enhanced"]["top_icebergs"]
    assert ice[0]["price"] == pytest.approx(3994.4)
    assert ice[0]["age_sec"] == pytest.approx(2400.0)
    assert ice[0]["score"] == pytest.approx(132000.0)
    assert ice[0]["tier"] == "INSTITUTIONAL"
    assert ice[0]["side"] == "BID"
    assert ice[1]["tier"] == "NOISE"

    # G-06d: Execution bridge (GC vs CFD basis + spread) & non-leaking closed trades present
    eb = payload["execution_bridge"]
    assert eb["cfd_bid"] == pytest.approx(3989.10)
    assert eb["cfd_spread_pts"] == pytest.approx(0.28)
    assert eb["gc_minus_cfd_basis_pts"] == pytest.approx(3994.6 - 3989.24, abs=0.02)

    rct = payload.get("recent_closed_trades", [])
    assert len(rct) == 2
    assert [t["result"] for t in rct] == ["LOSS", "WIN"]
    assert all(t["exit_reason"] != "FUTURE_LEAK" for t in rct)


def test_call_with_key_sets_temperature_02_json_mime_and_thinking_level(monkeypatch):
    captured = []

    class FakeTypes:
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
        def ThinkingConfig(thinking_level="low"):
            return {"thinking_level": thinking_level}

        @staticmethod
        def GenerateContentConfig(**kw):
            return kw

    class FakeModels:
        def generate_content(self, *, model, contents, config):
            captured.append((model, config))
            return SimpleNamespace(text='{"action":"HOLD","confidence":15,"rationale":"ok"}')

    class FakeGenAI:
        types = FakeTypes

        class Client:
            def __init__(self, api_key, http_options=None):
                self.models = FakeModels()

            def close(self):
                pass

    monkeypatch.setattr(s3, "genai", FakeGenAI)
    monkeypatch.setattr(s3, "_modern_genai", FakeGenAI, raising=False)
    monkeypatch.setitem(sys.modules, "google", SimpleNamespace(genai=FakeGenAI))
    monkeypatch.setitem(sys.modules, "google.genai", SimpleNamespace(types=FakeTypes))
    monkeypatch.setattr(config, "GEMINI_THINKING_LEVEL", "minimal", raising=False)
    monkeypatch.setattr(config, "AI_TEMPERATURE", 0.2, raising=False)
    monkeypatch.setattr(config, "AI_JSON_MODE_ENABLED", True, raising=False)

    eng = s3.AIDecisionEngine(api_keys=["TEST_KEY_01"], model="gemini-3.8-flash")
    eng._call_with_key("TEST_KEY_01", "gemini-3.8-flash", "test")
    eng._call_with_key("TEST_KEY_01", "gemini-3.6-flash", "test")

    assert len(captured) == 2
    assert captured[0][1]["temperature"] == pytest.approx(0.2)
    assert captured[0][1]["response_mime_type"] == "application/json"
    assert captured[0][1]["thinking_config"]["thinking_level"] == "low"
    assert captured[1][1]["thinking_config"]["thinking_level"] == "minimal"


def test_doorman_meta_gate_vetoes_opposite_side_flip_and_logs_neutral_shadow(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(s3, "_KEY_STATE_FILE", None)
    monkeypatch.setattr(s3, "genai", SimpleNamespace())
    monkeypatch.setattr(config, "AI_CACHE_MINUTES", 0, raising=False)
    monkeypatch.setattr(config, "AI_DOORMAN_META_GATE_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "AI_NEUTRAL_PANEL_VETO_ENABLED", False, raising=False)

    eng = s3.AIDecisionEngine(api_keys=["K1_VALID_001"], model="gemini-3.6-flash")
    eng.build_prompt = lambda _s: "prompt"

    # 1) Primary=BUY, AI tries to flip to SELL -> vetoed to HOLD @ 0.0
    eng._call_with_key = lambda k, m, p: '{"action":"SELL","confidence":76,"rationale":"ask wall fade"}'
    snap_buy = SimpleNamespace(
        price=4020.0, signal_direction="BUY", signal_strength=48.0,
        regime="TREND", level3=None, order_flow=None, shadow_candidates={},
    )
    d_flip = eng.decide(snap_buy)
    assert d_flip.action == "HOLD"
    assert d_flip.confidence == 0.0
    assert "DOORMAN META-GATE: primary=BUY conflicts with AI SELL -> HOLD veto" in d_flip.rationale

    # 2) Primary=BUY, AI approves BUY -> passes through cleanly
    eng._call_with_key = lambda k, m, p: '{"action":"BUY","confidence":78,"rationale":"3 families buy"}'
    d_ok = eng.decide(snap_buy)
    assert d_ok.action == "BUY"
    assert d_ok.confidence == pytest.approx(78.0)

    # 3) Primary=NEUTRAL, AI outputs SELL -> shadow telemetry recorded (Law #5 default =0)
    eng._call_with_key = lambda k, m, p: '{"action":"SELL","confidence":71,"rationale":"standalone sell"}'
    snap_neu = SimpleNamespace(
        price=4020.0, signal_direction="NEUTRAL", signal_strength=18.0,
        regime="RANGE", power={"direction": "DOWN"}, level3=None, order_flow=None,
        shadow_candidates={},
    )
    d_neu = eng.decide(snap_neu)
    assert d_neu.action == "SELL"
    sh = snap_neu.shadow_candidates["ai_neutral_panel_veto"]
    assert sh["shadow_only"] is True and sh["execution_enabled"] is False
    assert sh["would_veto"] is True and sh["ai_action"] == "SELL"

    # 4) Opt-in AI_NEUTRAL_PANEL_VETO_ENABLED=True -> vetoes standalone NEUTRAL AI trade to HOLD
    monkeypatch.setattr(config, "AI_NEUTRAL_PANEL_VETO_ENABLED", True, raising=False)
    d_neu_on = eng.decide(snap_neu)
    assert d_neu_on.action == "HOLD" and d_neu_on.confidence == 0.0


def test_monotonic_veto_invariance_blocks_power_carry_over_paid_ai_hold_and_throttle(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "_AI_STATE_FILE", None)
    monkeypatch.setattr(main, "_LAST_AI_CALL", 0.0)
    monkeypatch.setattr(main, "_STANDING_AI_SLIP", None)
    monkeypatch.setattr(main, "_LAST_PAID_AI_VETO", None)
    monkeypatch.setattr(main.config, "EXECUTION_MODE", "python")
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 45.0)
    monkeypatch.setattr(main.config, "AI_MIN_SIGNAL_STRENGTH", 10.0)
    monkeypatch.setattr(main.config, "AI_MIN_INTERVAL_MINUTES", 3.0)
    monkeypatch.setattr(main.config, "AI_RESPECT_PAID_HOLD_VETO", True)
    monkeypatch.setattr(main, "_record", lambda *_a, **_k: None)

    clock = [70_000.0]
    monkeypatch.setattr("time.time", lambda: clock[0])

    def _make_snap():
        return SimpleNamespace(
            signal_strength=40.0,
            confidence=55.0,
            signal_direction="BUY",
            regime="TREND",
            price=4050.0,
            power={"direction": "UP", "reason_code": "UPWARD_FORCE_DOMINATES"},
            shot={"shot": "WAIT"},
            execution_bridge={"trade_symbol": "XAUUSD", "cfd_bid": None, "cfd_ask": None},
        )

    monkeypatch.setattr(
        s3.AIDecisionEngine,
        "decide",
        lambda _self, _snap: s3.Decision(
            action="HOLD", confidence=15.0, rationale="confluence fail veto", model="gemini-3.6-flash"
        ),
    )

    main.set_async_loop_mode(False)
    # 1) Paid AI explicitly returns HOLD -> POWER carry MUST NOT override it!
    d1 = main.run_step3(_make_snap())
    assert d1.action == "HOLD" and d1.model == "gemini-3.6-flash"
    ok1, _ = main._shooting_execution_gate(d1, _make_snap())
    assert ok1
    assert d1.action == "HOLD"
    assert getattr(d1, "decision_source", "") == "AI_EXPLICIT_HOLD_VETO"

    # 2) 20s later inside throttle window -> model stays 'none-throttled', paid_ai_veto_active=True
    clock[0] = 70_020.0
    d2 = main.run_step3(_make_snap())
    assert d2.action == "HOLD" and d2.model == "none-throttled"
    assert getattr(d2, "paid_ai_veto_active", False) is True
    ok2, _ = main._shooting_execution_gate(d2, _make_snap())
    assert ok2
    assert d2.action == "HOLD"
    assert getattr(d2, "decision_source", "") == "AI_EXPLICIT_HOLD_VETO"

    # 3) Genuine none-quiet (AI was never consulted) STILL carries at configured threshold
    d_quiet = SimpleNamespace(action="HOLD", confidence=0.0, rationale="", model="none-quiet")
    snap_go = _make_snap()
    snap_go.shot = {"shot": "GO", "side": "BUY", "entry_style": "PASSIVE_LIMIT", "execution_status": "BROKER_READY"}
    ok_q, _ = main._shooting_execution_gate(d_quiet, snap_go)
    assert ok_q
    assert d_quiet.action == "BUY"
    assert d_quiet.decision_source == "POWER_V2_CONFIGURED_THRESHOLD_CARRY"


def test_startup_judge_report_card_refresh_and_evening_script_wiring(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    panel_csv = (
        "judge,votes,participation_pct,buy,sell,quiet,right_pct,scored,points_after_spread,avg_points\n"
        "vwap_bands,90,45.0,50,40,0,64.0,50,28.5,0.57\n"
        "footprint_delta,80,40.0,40,40,0,38.0,40,-18.0,-0.45\n"
    )
    (tmp_path / "judge_panel_2026-10-28.csv").write_text(panel_csv, encoding="utf-8")
    (tmp_path / "judge_panel_2026-10-29.csv").write_text(panel_csv, encoding="utf-8")

    main._ensure_judge_report_card_ready()
    card_file = tmp_path / "judge_report_card.json"
    assert card_file.is_file()
    card = json.loads(card_file.read_text(encoding="utf-8"))
    assert card["source_days"] == ["2026-10-28", "2026-10-29"]
    assert len(card["judges"]) == 2

    ev_sh = (Path(__file__).resolve().parent / "report_evening.sh").read_text(encoding="utf-8")
    assert "judge_report_card.py --days 5 --as-of" in ev_sh
