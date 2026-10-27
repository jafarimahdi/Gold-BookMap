"""End-to-end regression suite for all 2026-10-27 evening audit & live blocker fixes."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import audit_day
import bookmap_bridge_provider as bookmap
import config
import judge_panel as jp_mod
import main
import shooting_team
import signal_team
import step2_market_analysis as s2
import step3_ai_decision as s3
from tools import insight


def _warm_crowd_history(n: int = 12, lots: float = 95.0) -> None:
    shooting_team._ROAD_HISTORY.clear()
    now_t = time.time()
    for _ in range(n):
        shooting_team._ROAD_HISTORY.append((now_t, float(lots)))


def _valid_power(direction: str = "UP") -> dict:
    is_up = direction == "UP"
    reason = "UPWARD_FORCE_DOMINATES" if is_up else "DOWNWARD_FORCE_DOMINATES"
    return {
        "team": "POWER",
        "shadow_only": False,
        "direction": direction,
        "reason_code": reason,
        "up_power_pct": 68.0 if is_up else 32.0,
        "down_power_pct": 32.0 if is_up else 68.0,
        "power_total_pct": 100.0,
        "context": {
            "market_regime": "TREND",
            "feed_stale": False,
            "data_quality_ok": True,
        },
        "regime_diagnostics": {
            "market_regime": "TREND",
            "reason": "CONFIRMED",
            "latest_tick_age_seconds": 1.0,
        },
        "decision_gate_diagnostics": {
            "actionable_direction_allowed": True,
            "active_hard_blockers": [],
            "reason_code": reason,
        },
    }


def test_scout_road_clear_allows_normal_mm_corridor_and_blocks_real_walls():
    """Real CME ladders have 4-8 lot MM quotes at every $0.10 tick.
    Nearest wall must get road_clear=True unless a real wall or iceberg blocks the path.
    """
    cfg = SimpleNamespace(
        L3_WHALE_THRESHOLD=10.0,
        ICEBERG_MIN_REFILLS=3.0,
        SCOUT_MIN_SPREADS=5.0,
        SCOUT_MIN_TARGET_USD=1.0,
        PM_TP_BUFFER_ATR=0.15,
        SHOOT_MIN_RR=1.1,
        SHOOT_DOOR_MARGIN=3.0,
        SHOOT_ROAD_NORMAL_MAX=2.0,
        SHOOT_PARTICIPATION_MAX=0.10,
        SHOOT_CROWD_HISTORY_MIN=5,
        SHOOT_ROAD_HISTORY_MIN=5,
        LIMIT_ORDER_ENABLED=True,
        LIMIT_TICK_SIZE=0.10,
        SHOOT_QUEUE_MAX_AGE_SECONDS=5.0,
        SHOOT_FILL_PROB_MIN=0.55,
        SHOOT_PLAN_TTL_SECONDS=30.0,
        POWER_MAX_TICK_AGE_SECONDS=60.0,
        STALE_DATA_SECONDS=300.0,
    )
    price = 4050.00
    spread = 0.20
    atr = 1.50

    # Build realistic $0.10 ladder: 5-lot MM quotes everywhere, shelter bid wall at 4048.50 (30 lots),
    # nearest ask wall at 4053.00 (35 lots), second ask wall at 4055.00 (45 lots).
    bids = [(round(price - 0.10 * i, 2), 30.0 if abs((price - 0.10 * i) - 4048.50) < 1e-6 else 5.0)
            for i in range(1, 35)]
    asks = [(round(price + 0.10 * i, 2),
             35.0 if abs((price + 0.10 * i) - 4053.00) < 1e-6
             else (45.0 if abs((price + 0.10 * i) - 4055.00) < 1e-6 else 5.0))
            for i in range(1, 65)]

    l3 = SimpleNamespace(
        order_book={"bids": bids, "asks": asks},
        spoof_levels={},
        iceberg_levels={},
        iceberg_meta={},
        order_events=[],
        order_book_imbalance=0.25,
        ofi=40.0,
        large_order_events=2,
    )
    of = SimpleNamespace(absorption_net=0)

    mem = signal_team.LevelBook()
    smap = mem.ingest(price=price, atr=atr, level3=l3, order_flow=of, config=cfg, spread=spread)
    above_by_px = {round(d["price"], 2): d for d in smap["above"]}

    # Nearest real ask wall (4053.00) has 29 intermediate 5-lot MM ticks (145 lots)
    # -> crowd is measured honestly, AND road_clear is True (no blocking wall in between)!
    near_door = above_by_px[4053.00]
    assert near_door["doors_between"] == 29
    assert abs(near_door["lots_between"] - 145.0) < 1e-6
    assert near_door["road_clear"] is True

    # Farther wall (4055.00) sits BEHIND the 35-lot wall at 4053.00 -> road_clear MUST be False!
    if 4055.00 in above_by_px:
        assert above_by_px[4055.00]["road_clear"] is False

    # End-to-end: Shooting approves an A2 aggressive shot to 4053.00 when Crowd v2 passes!
    _warm_crowd_history(n=10, lots=130.0)
    now_ts = time.time()
    ctx = {
        "bid": 4049.90,
        "ask": 4050.10,
        "now": now_ts,
        "market_symbol": "XAUUSD",
        "news_state": "QUIET",
        "data_quality_ok": True,
        "last_data_age_seconds": 1.0,
        "has_data": True,
        "shot_lots": 0.5,
        "recent_volume": 200.0,
        "aggressive_trigger_confirmed": True,
        "aggressive_trigger_reason": "A2 flow+book burst",
        "a2_burst": True,
    }
    shot = shooting_team.plan_shot(smap, _valid_power("UP"), price, atr, spread, cfg, entry_context=ctx)
    assert shot["shot"] == "GO", f"Expected GO on clear MM corridor, got: {shot}"
    assert shot["side"] == "BUY"
    assert shot["entry_style"] == "AGGRESSIVE_MARKET"

    # Case B: Place a real too-close 28-lot wall at 4050.40 (< min_gap $1.00) right in our face
    asks_blocked = [
        (p, 28.0 if abs(p - 4050.40) < 1e-6 else s) for p, s in asks
    ]
    l3_blocked = SimpleNamespace(**{**l3.__dict__, "order_book": {"bids": bids, "asks": asks_blocked}})
    mem2 = signal_team.LevelBook()
    smap_blocked = mem2.ingest(price=price, atr=atr, level3=l3_blocked, order_flow=of, config=cfg, spread=spread)
    near_blocked = {round(d["price"], 2): d for d in smap_blocked["above"]}[4053.00]
    assert near_blocked["road_clear"] is False
    shot_blocked = shooting_team.plan_shot(
        smap_blocked, _valid_power("UP"), price, atr, spread, cfg, entry_context=ctx
    )
    assert shot_blocked["shot"] == "WAIT"
    assert "clear route" in " ".join(shot_blocked["why"])

    # Case C: If 4050.40 is a banned SPOOF liar, it does not falsely block road_clear
    l3_spoof = SimpleNamespace(
        **{**l3_blocked.__dict__, "spoof_levels": {4050.40: 3}}
    )
    mem3 = signal_team.LevelBook()
    smap_spoof = mem3.ingest(price=price, atr=atr, level3=l3_spoof, order_flow=of, config=cfg, spread=spread)
    assert {round(d["price"], 2): d for d in smap_spoof["above"]}[4053.00]["road_clear"] is True

    # Case D: Active non-spoofed iceberg at 4051.20 (even with 5 visible lots) blocks road_clear
    l3_ice = SimpleNamespace(
        **{**l3.__dict__, "iceberg_levels": {4051.20: 4}, "iceberg_meta": {4051.20: {"age_sec": 30.0}}}
    )
    mem4 = signal_team.LevelBook()
    smap_ice = mem4.ingest(price=price, atr=atr, level3=l3_ice, order_flow=of, config=cfg, spread=spread)
    assert {round(d["price"], 2): d for d in smap_ice["above"]}[4053.00]["road_clear"] is False


def test_shooting_execution_gate_surfaces_specific_sub_reason(monkeypatch):
    monkeypatch.setattr(config, "AI_CONFIDENCE_THRESHOLD", 35.0, raising=False)
    monkeypatch.setattr(config, "CONFIDENCE_THRESHOLD", 35.0, raising=False)
    monkeypatch.setattr(config, "EXECUTION_MODE", "python", raising=False)

    snap = SimpleNamespace(
        power={"direction": "UP"},
        shot={
            "shot": "WAIT",
            "side": None,
            "why": ["crowd v2 Q1 SERVED failed: door 2.0 lots is only 2.0x our shot 1.00 (need 3.0x)"],
        },
    )
    dec = SimpleNamespace(action="BUY", confidence=72.0, model="gemini-3.8-flash")
    ok, reason = main._shooting_execution_gate(dec, snap)
    assert ok is False
    assert reason.startswith("Shooting did not approve this side")
    assert "crowd v2 Q1 SERVED failed" in reason


def test_mbo_order_id_lifecycle_conserves_ofi_and_stops_false_step3_buy_veto(monkeypatch):
    tail = object.__new__(bookmap._MboTail)
    tail.instruments = {}
    tail.line_count = 0
    tail.bad_lines = 0

    t0 = datetime(2026, 10, 27, 14, 0, 0, tzinfo=timezone.utc)
    lines = [
        # Symmetric bid and ask lifecycle: NEW 10 -> REPLACE 16 -> REPLACE 16 (same size/px) -> CANCEL 0
        f"{(t0 + timedelta(seconds=1)).isoformat()},BID_NEW,oid-b1,4050.0,10,GCZ6\n",
        f"{(t0 + timedelta(seconds=2)).isoformat()},ASK_NEW,oid-a1,4050.4,10,GCZ6\n",
        f"{(t0 + timedelta(seconds=3)).isoformat()},REPLACE,oid-b1,4050.0,16,GCZ6\n",
        f"{(t0 + timedelta(seconds=4)).isoformat()},REPLACE,oid-b1,4050.0,16,GCZ6\n",
        f"{(t0 + timedelta(seconds=5)).isoformat()},REPLACE,oid-a1,4050.4,16,GCZ6\n",
        f"{(t0 + timedelta(seconds=6)).isoformat()},CANCEL,oid-b1,0.0,0,GCZ6\n",
        f"{(t0 + timedelta(seconds=7)).isoformat()},CANCEL,oid-a1,0.0,0,GCZ6\n",
        # Orphan pre-catchup REPLACE & CANCEL must NOT fabricate negative ASK OFI
        f"{(t0 + timedelta(seconds=8)).isoformat()},REPLACE,oid-orphan,4049.8,25,GCZ6\n",
        f"{(t0 + timedelta(seconds=9)).isoformat()},CANCEL,oid-unknown,0.0,0,GCZ6\n",
        # Net active bid build (+30 lots) vs ask (+5 lots) -> net OFI = +25.0
        f"{(t0 + timedelta(seconds=10)).isoformat()},BID_NEW,oid-b2,4050.1,20,GCZ6\n",
        f"{(t0 + timedelta(seconds=11)).isoformat()},REPLACE,oid-b2,4050.1,30,GCZ6\n",
        f"{(t0 + timedelta(seconds=12)).isoformat()},ASK_NEW,oid-a2,4050.5,5,GCZ6\n",
    ]
    for ln in lines:
        tail._handle_line(ln)

    evs = tail.instruments["GCZ6"].mbo_events
    # Sequential REPLACE events on oid-b1 at 4050.0 have distinct timestamps and carry side="BID" + old_size
    b1_replaces = [e for e in evs if e["order_id"] == "oid-b1" and e["type"] == "REPLACE"]
    assert len(b1_replaces) == 2
    assert b1_replaces[0]["side"] == "BID" and b1_replaces[0]["old_size"] == 10.0
    assert b1_replaces[1]["side"] == "BID" and b1_replaces[1]["old_size"] == 16.0
    b1_cancel = [e for e in evs if e["order_id"] == "oid-b1" and e["type"] == "CANCEL"][0]
    assert b1_cancel["side"] == "BID" and b1_cancel["size"] == 16.0 and b1_cancel["price"] == 4050.0

    # Replay through Level3OrderBookAnalyzer -> net OFI must be +25.0 (30 bid - 5 ask), NOT negative!
    l3_analyzer = s2.Level3OrderBookAnalyzer()
    for ev in evs:
        l3_analyzer.process_order_event(ev)
    l3_res = l3_analyzer.analyze()
    assert abs(l3_res.ofi - 25.0) < 1e-6, f"Expected exact +25.0 OFI conservation, got {l3_res.ofi}"

    # Verify Step 3 Gemini BUY @ 68% survives L3 calibration cleanly instead of getting -20% -> HOLD
    monkeypatch.setattr(s3, "genai", object())
    eng = s3.AIDecisionEngine(api_keys=["K1_0000001"], model="gemini-3.8-flash")
    eng._check_cache = lambda snap: None
    eng.build_prompt = lambda snap: "p"
    eng._update_cache = lambda snap, d: None
    eng._call_with_key = lambda k, m, p: '{"action":"BUY","confidence":68,"rationale":"bullish breakout"}'

    snap = SimpleNamespace(
        price=4050.2,
        level3=SimpleNamespace(
            aggressive_buy_volume=120.0,
            aggressive_sell_volume=80.0,
            ofi=l3_res.ofi,
            order_book={"bids": [(4050.1, 30.0)], "asks": [(4050.5, 5.0)]},
        ),
        order_flow=SimpleNamespace(cvd=150.0, depth_imbalance=0.3),
    )
    dec = eng.decide(snap)
    assert dec.action == "BUY"
    assert dec.confidence >= 68.0


def test_step3_503_overload_fast_model_failover_keeps_keys_healthy_and_retires_fake_fallback(
    tmp_path, monkeypatch
):
    state_file = tmp_path / "gemini_keys_state.json"
    monkeypatch.setattr(s3, "_KEY_STATE_FILE", state_file)
    monkeypatch.setattr(s3, "genai", object())
    monkeypatch.setattr(
        config,
        "GEMINI_MODELS",
        ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"],
        raising=False,
    )
    # Even if a legacy .env still sets AI_FALLBACK_ENABLED=True, synthetic fallback stays retired!
    monkeypatch.setattr(config, "AI_FALLBACK_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "AI_ALLOW_SYNTHETIC_FALLBACK", False, raising=False)

    calls = []

    def overload_38_ok_36(key, model, prompt):
        calls.append((key, model))
        if model == "gemini-3.8-flash":
            raise RuntimeError(
                "503 UNAVAILABLE. {'error': {'code': 503, 'message': "
                "'This model is currently experiencing high demand.', 'status': 'UNAVAILABLE'}}"
            )
        return '{"action":"SELL","confidence":74,"rationale":"3.6 answered cleanly"}'

    keys = [f"KEY_HEALTHY_{i:05d}" for i in range(1, 7)]
    eng = s3.AIDecisionEngine(api_keys=keys)
    eng._check_cache = lambda snap: None
    eng.build_prompt = lambda snap: "prompt"
    eng._update_cache = lambda snap, d: None
    eng._call_with_key = overload_38_ok_36

    snap = SimpleNamespace(
        price=4055.0,
        signal_strength=65.0,
        signal_direction="SELL",
        level3=SimpleNamespace(
            aggressive_buy_volume=10.0,
            aggressive_sell_volume=300.0,
            ofi=-250.0,
            iceberg_events=5,
            spoof_events=2,
            spoof_levels={4050.0: 2},
            order_book={"bids": [], "asks": [(4056.0, 200.0)]},
        ),
        order_flow=SimpleNamespace(cvd=-500.0, depth_imbalance=-0.6),
    )

    # 1) Immediate same-cycle failover from 3.8-flash -> 3.6-flash on Key #1 (only 2 calls, NOT 6+1!)
    dec = eng.decide(snap)
    assert dec.action == "SELL"
    assert dec.model == "gemini-3.6-flash"
    assert calls == [(keys[0], "gemini-3.8-flash"), (keys[0], "gemini-3.6-flash")]
    # Zero keys parked or blamed, and 3.8-flash is NOT permanently banned!
    assert list(s3._exhausted_keys()) == []
    assert "gemini-3.8-flash" not in eng._UNAVAILABLE_MODELS

    # 2) If ALL models return 503 across multiple cycles, zero keys are parked and NO 70% fallback-rule leaks!
    eng._call_with_key = lambda k, m, p: (_ for _ in ()).throw(
        RuntimeError("503 UNAVAILABLE: currently experiencing high demand")
    )
    for _ in range(8):
        dec_fail = eng.decide(snap)
        assert dec_fail.action == "HOLD"
        assert dec_fail.confidence == 0.0
        assert dec_fail.model == "none-model-overloaded"
    assert list(s3._exhausted_keys()) == []

    # 3) Verify main.py never counts fallback-* or none-* models in ai_calls.json
    ai_state = tmp_path / "ai_calls.json"
    monkeypatch.setattr(main, "_AI_STATE_FILE", ai_state)
    monkeypatch.setattr(main, "_LAST_AI_CALL", 0.0)
    monkeypatch.setattr(config, "AI_MIN_SIGNAL_STRENGTH", 1.0, raising=False)
    monkeypatch.setattr(config, "AI_MAX_CALLS_PER_DAY", 0, raising=False)

    for fake_model in ("fallback-l3", "fallback-spoof", "fallback-rule", "none-model-overloaded"):
        monkeypatch.setattr(
            s3.AIDecisionEngine,
            "decide",
            lambda self, s, fm=fake_model: s3.Decision(
                action="HOLD", confidence=0.0, rationale="test", model=fm
            ),
        )
        main.run_step3(SimpleNamespace(signal_strength=50.0, signal_direction="BUY"))
    assert main._ai_calls_today() == 0


def test_value_area_supply_demand_insight_rotation_and_audit_blackout(tmp_path):
    # 1) All 4 value_area note templates parse to exact non-zero directions across all 3 parsers
    cases = [
        ("near VAL 4020.0 (VLL) support -> BUY", 1.0),
        ("price 4069.5 above VAH 4046.8 breakout -> BUY", 1.0),
        ("near VAH 4046.8 (VHL) resistance -> SELL", -1.0),
        ("price 4009.5 below VAL 4012.0 breakdown -> SELL", -1.0),
    ]
    for note, expected_dir in cases:
        for parser in (s2.parse_judge_panel, jp_mod.parse_judge_panel):
            res = parser([note])
            assert len(res) == 1 and res[0]["judge"] == "value_area"
            assert res[0]["dir"] == expected_dir, f"{parser.__module__} failed on {note}: {res}"
        ad_res, _ = audit_day.judge_panel({"notes": [note]})
        assert ad_res == [("value_area", expected_dir, 0.5)], f"audit_day failed on {note}: {ad_res}"

    # 2) Duplicate value_area / supply_demand notes deduplicate cleanly and label balanced vs net
    dup_sd = s2.parse_judge_panel([
        "near demand zone 4040.0 (bounce) recency 1.20x w 0.96 -> BUY",
        "near supply zone 4050.0 (rejection) recency 1.20x w 0.96 -> SELL",
    ])
    assert len(dup_sd) == 1
    assert dup_sd[0]["dir"] == 0.0
    assert "balanced (0)" in dup_sd[0]["raw"]

    skew_sd = s2.parse_judge_panel([
        "near demand zone 4040.0 (bounce) recency 1.50x w 1.20 -> BUY",
        "near supply zone 4050.0 (rejection) recency 1.00x w 0.80 -> SELL",
    ])
    assert len(skew_sd) == 1
    assert skew_sd[0]["dir"] == 1.0
    assert abs(skew_sd[0]["weight"] - 0.40) < 1e-6
    assert "net BUY" in skew_sd[0]["raw"]

    # 3) tools/insight.py formats timestamp cleanly without leaking "if dt else '--:--'"
    dt = datetime(2026, 10, 27, 14, 32, 5, tzinfo=timezone.utc)
    html_out = insight.decisions_page(
        "2026-10-27",
        decisions=[{
            "_dt": dt, "price": 4055.2, "exec_status": "SKIPPED",
            "signal_direction": "BUY", "signal_strength": 42.0,
            "signal_confidence": 58.0, "ai_action": "BUY", "ai_confidence": 70.0,
            "ai_model": "gemini-3.8-flash", "exec_reason": "test", "ai_rationale": "ok",
        }],
        diary=[],
        series=[(dt, 4055.2)],
    )
    assert ":32:05</span>" in html_out
    assert "if dt else" not in html_out

    # 4) audit_day TEST 10 passes when CPI BLACKOUT rows legitimately carry judge_votes=[]
    diary_rows = [
        {
            "timestamp": "2026-10-27T12:30:00+00:00",
            "judge_votes": [],
            "notes": ["HIGH-impact event BLACKOUT: US CPI"],
        },
        {
            "timestamp": "2026-10-27T13:05:00+00:00",
            "judge_votes": [{"judge": "mtf", "dir": 1.0, "weight": 0.5, "raw": "MTF H1:UP M15:UP"}],
            "notes": ["MTF H1:UP M15:UP"],
        },
    ]
    rep10, _ = audit_day.test_10_judge_coverage("2026-10-27", diary_rows)
    assert rep10.rows[0][0] == "PASS", f"Expected TEST 10 PASS with CPI blackout rows, got {rep10.rows}"

    # Legacy pre-v7.1 diary without judge_votes still grades WARN
    legacy_rows = [{"timestamp": "2026-10-01T10:00:00+00:00", "notes": ["MTF H1:UP M15:UP"]}]
    rep10_legacy, _ = audit_day.test_10_judge_coverage("2026-10-01", legacy_rows)
    assert rep10_legacy.rows[0][0] == "WARN"

    # 4b) whale_balanced retired alongside whale_walls, and TEST 17 does not 2x-count power_v2 alias
    for parser in (s2.parse_judge_panel, jp_mod.parse_judge_panel):
        assert parser(["L3 whales balanced bid 40.0 ask 42.0"]) == []
    p_obj = {"voice_gain": 3.0, "memory_status": {"appended": True}, "reason_code": "WEAK_OR_NO_FORCE"}
    rep17, info17 = audit_day.test_17_new_organs(
        datetime(2026, 10, 27).date(),
        [{"power": dict(p_obj), "power_v2": dict(p_obj), "shot": {"aggressive_trigger_confirmed": False}}],
        "",
        [],
    )
    assert info17["voice_gain_records"] == 1

    # 5) main.py opsfix:v1 byte-capped rotation keeps ~28KB rich lines well below 50MB
    import textwrap
    src = Path(main.__file__).read_text(encoding="utf-8")
    rot_block = src.split("opsfix:v1-start", 1)[1].split("opsfix:v1-end", 1)[0]
    code_str = textwrap.dedent("\n".join(rot_block.splitlines()[1:]))
    big_hist = tmp_path / "snapshots_history.jsonl"
    rich_line = json.dumps({"pad": "X" * 28_000})
    big_hist.write_text("\n".join([rich_line] * 2000) + "\n", encoding="utf-8")
    assert big_hist.stat().st_size > 50 * 1024 * 1024
    ns = {"hist_path": big_hist, "logger": SimpleNamespace(info=lambda *a, **k: None)}
    exec(code_str, ns)
    new_size = big_hist.stat().st_size
    assert new_size < 40 * 1024 * 1024, f"Rotated file still too big ({new_size} bytes)!"
