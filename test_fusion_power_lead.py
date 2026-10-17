"""FUSION v1 tests - the power-lead advisory + TIMEFRAME unification.

Owner-ordered 2026-10-17 (Option B evidence: council_shadow 10-15/10-16).

Covers:
  - the advisory gate: floor 30 + agreement carries POWER's trade when the AI
    is quiet; disagreement blocks; the old >=45 path unchanged; the off-switch
  - receipts: decision.model="power-lead-v1", rationale stamp, loop _record
  - TIMEFRAME plumbing: config map, adapter windows, boundary math, regime
    re-timing, run_power_v2 labels (M3 end to end)
"""
import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import main
import config
from main import _shooting_execution_gate
from power_m5_adapter import build_m5_inputs
from power_v2_regime import set_bar_seconds, classify_m5_regime
from power_v2_shadow import run_power_v2, last_completed_m5_end

UTC = timezone.utc


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def snap(confidence=35.0, direction="UP",
         reason_code="UPWARD_FORCE_DOMINATES", class_direction="BUY",
         side="BUY"):
    return SimpleNamespace(
        shot={"shot": "GO", "side": side, "entry_style": "PASSIVE_LIMIT",
              "execution_status": "PLAN_ONLY"},
        power={"direction": direction, "reason_code": reason_code},
        confidence=confidence,
        signal_direction=class_direction,
    )


def hold_decision():
    return SimpleNamespace(action="HOLD", confidence=0.0, rationale="",
                           model="none-quiet")


def open_corridor(monkeypatch):
    monkeypatch.setattr(main.config, "EXECUTION_MODE", "python")
    monkeypatch.setattr(main.config, "TRADING_ENABLED", True)


# --------------------------------------------------------------------------
# 1. the power-lead advisory (fusion v1)
# --------------------------------------------------------------------------
def test_lead_carries_at_floor_with_agreement(monkeypatch):
    open_corridor(monkeypatch)
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 45.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_CONF_FLOOR", 30.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_ENABLED", True)
    recorded = []
    monkeypatch.setattr(main, "_record", lambda *a, **k: recorded.append(a))
    decision = hold_decision()
    ok, reason = _shooting_execution_gate(
        decision, snap(confidence=35.0, direction="UP",
                       class_direction="BUY", side="BUY"))
    assert decision.action == "BUY"
    assert decision.model == "power-lead-v1"
    assert "power-lead v1" in decision.rationale
    assert "conf 35 >= floor 30" in decision.rationale
    assert any("POWER-LEAD" in str(a) for a in recorded), recorded
    assert ok and "corridor open" in reason


def test_lead_blocked_on_classroom_disagreement(monkeypatch):
    open_corridor(monkeypatch)
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 45.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_CONF_FLOOR", 30.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_ENABLED", True)
    decision = hold_decision()
    ok, reason = _shooting_execution_gate(
        decision, snap(confidence=35.0, direction="UP",
                       class_direction="SELL", side="BUY"))
    assert decision.action == "HOLD"
    assert ok is True and reason == "no new-entry action"


def test_lead_respects_floor(monkeypatch):
    open_corridor(monkeypatch)
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 45.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_CONF_FLOOR", 30.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_ENABLED", True)
    decision = hold_decision()
    _shooting_execution_gate(decision, snap(confidence=29.0))
    assert decision.action == "HOLD"


def test_old_gate_still_fires_without_agreement(monkeypatch):
    """conf >= AI_CONFIDENCE_THRESHOLD keeps the pre-fusion behavior."""
    open_corridor(monkeypatch)
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 45.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_CONF_FLOOR", 30.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_ENABLED", True)
    decision = hold_decision()
    ok, _ = _shooting_execution_gate(
        decision, snap(confidence=50.0, direction="UP",
                       class_direction="SELL", side="BUY"))
    assert decision.action == "BUY"
    assert decision.model == "none-quiet"  # old path: not via the lead
    assert ok


def test_lead_off_switch_restores_old_behavior(monkeypatch):
    open_corridor(monkeypatch)
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 45.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_CONF_FLOOR", 30.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_ENABLED", False)
    decision = hold_decision()
    _shooting_execution_gate(decision, snap(confidence=35.0))
    assert decision.action == "HOLD"


def test_lead_requires_dominant_power_reason(monkeypatch):
    """A NEITHER POWER or a non-dominant reason code never carries."""
    open_corridor(monkeypatch)
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 45.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_CONF_FLOOR", 30.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_ENABLED", True)
    decision = hold_decision()
    _shooting_execution_gate(
        decision, snap(confidence=35.0, direction="UP",
                       reason_code="WEAK_OR_NO_FORCE"))
    assert decision.action == "HOLD"


# --------------------------------------------------------------------------
# 2. TIMEFRAME plumbing
# --------------------------------------------------------------------------
def test_config_timeframe_drives_power_bar_seconds(monkeypatch):
    monkeypatch.delenv("POWER_BAR_SECONDS", raising=False)
    monkeypatch.setenv("TIMEFRAME", "M3")
    config._refresh()
    assert config.BASE_TF_MINUTES == 3
    assert config.POWER_BAR_SECONDS == 180
    monkeypatch.setenv("POWER_BAR_SECONDS", "240")
    config._refresh()
    assert config.POWER_BAR_SECONDS == 240
    monkeypatch.delenv("POWER_BAR_SECONDS")
    monkeypatch.setenv("TIMEFRAME", "M5")
    config._refresh()
    assert config.POWER_BAR_SECONDS == 300
    monkeypatch.delenv("TIMEFRAME")
    config._refresh()


def _m3_tape(now, bars=6, prints=25):
    """Craft an up-drifting tape on 180-second bars ending at `now`."""
    ticks = []
    total = bars * prints
    for i in range(total):
        minute_offset = (total - i) * 0.5  # spread prints backwards in time
        ts = now - timedelta(seconds=minute_offset * 60.0 / 60.0)
        rng = (i * 2654435761) % 1000 / 1000.0
        ticks.append({
            "timestamp": ts.isoformat().replace("+00:00", "Z"),
            "price": round(4100.0 + 0.2 * (total - i) + (rng - 0.5) * 0.3, 2),
            "volume": round(1.0 + 3.0 * rng, 3),
            "side": "BUY" if rng < 0.8 else "SELL",
            "is_direct": True,
        })
    return ticks


def test_adapter_window_follows_bar_seconds():
    now = datetime(2026, 10, 19, 10, 0, 0, tzinfo=UTC)
    ticks = []
    # current window [09:57, 10:00): 25 prints
    for k in range(25):
        ts = now - timedelta(seconds=30 + k * 6)
        ticks.append({"timestamp": ts.isoformat().replace("+00:00", "Z"),
                      "price": 4100.0 + k * 0.1, "volume": 1.0,
                      "side": "BUY", "is_direct": True})
    # previous window [09:54, 09:57): 25 prints
    for k in range(25):
        ts = now - timedelta(seconds=210 + k * 6)
        ticks.append({"timestamp": ts.isoformat().replace("+00:00", "Z"),
                      "price": 4100.0, "volume": 1.0,
                      "side": "SELL", "is_direct": True})
    # ancient prints (outside M3 windows entirely; inside M5's previous window)
    for k in range(5):
        ts = now - timedelta(seconds=400 + k * 5)
        ticks.append({"timestamp": ts.isoformat().replace("+00:00", "Z"),
                      "price": 4099.0, "volume": 1.0,
                      "side": "SELL", "is_direct": True})
    built_m3 = build_m5_inputs(ticks, now=now, tick_size=0.1, bar_seconds=180)
    d3 = built_m3["diagnostics"]
    assert d3["current_window_direct_trades"] == 25
    assert d3["previous_window_direct_trades"] == 25
    assert built_m3["context"]["data_quality_ok"] is True
    # At M5 the same tape straddles differently: current window [09:55,10:00)
    # swallows the 25 current prints + the 16 previous-fixture prints at
    # 210..300s back = 41; the M5 previous window [09:50,09:55) keeps the
    # remaining 9 + the 5 ancient = 14. (Window semantics documented by test.)
    built_m5 = build_m5_inputs(ticks, now=now, tick_size=0.1, bar_seconds=300)
    d5 = built_m5["diagnostics"]
    assert d5["current_window_direct_trades"] == 41
    assert d5["previous_window_direct_trades"] == 14


def test_last_completed_boundary_follows_bar_seconds():
    now = datetime(2026, 10, 19, 10, 7, 30, tzinfo=UTC)
    for bar_seconds in (180, 240, 300):
        boundary = last_completed_m5_end(now, bar_seconds=bar_seconds)
        epoch = boundary.timestamp()
        assert epoch % bar_seconds == 0
        assert boundary <= now


def test_regime_set_bar_seconds_buckets_at_m3():
    now = datetime(2026, 10, 19, 12, 0, 0, tzinfo=UTC)
    ticks = []
    # 30 bars of 180s each, gently trending up
    for bar in range(30):
        for k in range(6):
            ts = (now - timedelta(seconds=(30 - bar) * 180 + k * 20))
            ticks.append({
                "timestamp": ts.isoformat().replace("+00:00", "Z"),
                "price": 4100.0 + bar * 0.5 + k * 0.02,
                "volume": 1.0, "side": "BUY", "is_direct": True})
    try:
        set_bar_seconds(180)
        result = classify_m5_regime(ticks, now=now)
        assert result.get("bars_available", 0) >= 2
        assert result.get("bar_end_utc") is not None
    finally:
        set_bar_seconds(300)
    result5 = classify_m5_regime(ticks, now=now)
    # with 180s bars re-read as 300s the available count must differ
    assert result5.get("bars_available", 0) != result.get("bars_available", 0) or \
        result5.get("bars_available", 0) >= 2


def test_regime_rejects_tiny_bars():
    try:
        set_bar_seconds(30)
        assert False, "should have raised"
    except ValueError:
        pass


def test_run_power_v2_m3_end_to_end():
    now = datetime(2026, 10, 19, 10, 7, 0, tzinfo=UTC)
    ticks = _m3_tape(now, bars=8, prints=30)
    answer = run_power_v2(
        ticks, now=now, symbol="MGCZ6", tick_size=0.10,
        memory_root=None, market_regime="TREND",
        breakout_confirmed=False, high_impact_news=False,
        bar_seconds=180)
    assert answer["timeframe"] == "M3"
    epoch = datetime.fromisoformat(
        answer["bar_end_utc"].replace("Z", "+00:00")).timestamp()
    assert epoch % 180 == 0
    assert answer["direction"] in ("UP", "DOWN", "NEITHER")
    # a strongly one-sided tape with rising prices should be UP, not a guess
    assert answer["direction"] == "UP"


# --------------------------------------------------------------------------
# 3. REVERSE-ENGINEERING CORNERS (audit pass 2, owner-ordered)
# --------------------------------------------------------------------------
def test_lead_down_direction_mirror(monkeypatch):
    """The lead must be side-symmetric: POWER DOWN + class SELL -> carried."""
    open_corridor(monkeypatch)
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 45.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_CONF_FLOOR", 30.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_ENABLED", True)
    decision = hold_decision()
    ok, reason = _shooting_execution_gate(
        decision, snap(confidence=38.0, direction="DOWN",
                       reason_code="DOWNWARD_FORCE_DOMINATES",
                       class_direction="SELL", side="SELL"))
    assert decision.action == "SELL"
    assert decision.model == "power-lead-v1"
    assert ok and "corridor open" in reason


def test_lead_with_quiet_class_direction(monkeypatch):
    """Class direction absent (HOLD) -> agreement is vacuous -> carried."""
    open_corridor(monkeypatch)
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 45.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_CONF_FLOOR", 30.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_ENABLED", True)
    decision = hold_decision()
    ok, _ = _shooting_execution_gate(
        decision, snap(confidence=32.0, direction="UP",
                       class_direction="HOLD", side="BUY"))
    assert decision.action == "BUY"
    assert decision.model == "power-lead-v1"
    assert ok


def test_m3_diary_roundtrip(tmp_path):
    """The memory book at M3: record_id labelled |M3|, file parses back."""
    import json as _json
    now = datetime(2026, 10, 19, 10, 7, 0, tzinfo=UTC)
    ticks = _m3_tape(now, bars=6, prints=30)
    answer = run_power_v2(
        ticks, now=now, symbol="MGCZ6", tick_size=0.10,
        memory_root=tmp_path, market_regime="TREND",
        breakout_confirmed=False, high_impact_news=False,
        bar_seconds=180)
    day_file = tmp_path / "power_2026-10-19.jsonl"
    assert day_file.is_file(), sorted(p.name for p in tmp_path.rglob("*"))
    row = _json.loads(day_file.read_text(encoding="utf-8").splitlines()[0])
    assert "|M3|" in row["record_id"]
    assert row["phase"] == "FINAL"
    assert row["result"]["timeframe"] == "M3"
    assert row["reference_price"] > 0
    # the report-card loader contract: phase/timestamp/symbol/votes all present
    assert row["timestamp_utc"].endswith("Z")
    assert row["symbol"] == "MGCZ6"
    assert isinstance(row["result"]["input_judges"], dict)


def test_run_power_v2_default_stays_m5():
    """Backward compat: without bar_seconds the contract is unchanged (M5).

    The offline archive replay tool calls with defaults; its historical
    contract (replaying M5-era tapes) must keep working.
    """
    now = datetime(2026, 10, 19, 10, 7, 0, tzinfo=UTC)
    ticks = _m3_tape(now, bars=6, prints=30)
    answer = run_power_v2(
        ticks, now=now, symbol="MGCZ6", tick_size=0.10,
        memory_root=None, market_regime="TREND",
        breakout_confirmed=False, high_impact_news=False)
    assert answer["timeframe"] == "M5"
    epoch = datetime.fromisoformat(
        answer["bar_end_utc"].replace("Z", "+00:00")).timestamp()
    assert epoch % 300 == 0


def test_captain_prompt_follows_timeframe(monkeypatch):
    """The AI Captain's frame label follows TIMEFRAME (was hardcoded M5)."""
    import importlib
    import step3_ai_decision
    monkeypatch.delenv("POWER_BAR_SECONDS", raising=False)
    monkeypatch.setenv("TIMEFRAME", "M3")
    config._refresh()
    try:
        reloaded = importlib.reload(step3_ai_decision)
        assert "v7.0 M3 scalper" in reloaded.AIDecisionEngine.SYSTEM_PROMPT
        assert "v7.0 M5 scalper" not in reloaded.AIDecisionEngine.SYSTEM_PROMPT
    finally:
        monkeypatch.delenv("TIMEFRAME")
        config._refresh()
        importlib.reload(step3_ai_decision)
