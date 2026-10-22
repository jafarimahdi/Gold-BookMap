import sys
import types
from types import SimpleNamespace

import pytest

import main
from main import _shooting_execution_gate

# Shadow-only safety regression coverage: executable paths retain the
# owner-configured confidence threshold; sub-threshold Power lead is observable
# but never reaches the executor.


def snap(status="PLAN_ONLY", side="BUY", direction="UP",
         reason_code=None, confidence=None):
    s = SimpleNamespace(
        shot={"shot": "GO", "side": side, "entry_style": "PASSIVE_LIMIT",
              "execution_status": status},
        power={"direction": direction},
    )
    if reason_code is not None:
        s.power = {"direction": direction, "reason_code": reason_code}
    if confidence is not None:
        s.confidence = confidence
    return s


def test_go_plan_opens_the_corridor_in_python_mode(monkeypatch):
    monkeypatch.setattr(main.config, "EXECUTION_MODE", "python")
    ok, reason = _shooting_execution_gate(SimpleNamespace(action="BUY", confidence=75.0), snap())
    assert ok
    assert "corridor open" in reason


def test_corridor_stays_closed_outside_python_mode(monkeypatch):
    monkeypatch.setattr(main.config, "EXECUTION_MODE", "none")
    ok, reason = _shooting_execution_gate(
        SimpleNamespace(action="BUY", confidence=75.0), snap(status="BROKER_READY"))
    assert not ok
    assert "corridor closed" in reason


def test_power_and_shooting_must_agree_on_side():
    ok, reason = _shooting_execution_gate(
        SimpleNamespace(action="BUY", confidence=75.0), snap(status="BROKER_READY", direction="DOWN"))
    assert not ok
    assert "POWER v2" in reason


def test_wrong_shooting_side_cannot_authorize_order():
    ok, reason = _shooting_execution_gate(
        SimpleNamespace(action="BUY", confidence=75.0), snap(status="BROKER_READY", side="SELL"))
    assert not ok
    assert "Shooting did not approve" in reason


def test_doorman_advisory_when_power_dominant_and_panel_agrees(monkeypatch):
    monkeypatch.setattr(main.config, "EXECUTION_MODE", "python")
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 45.0)
    d = SimpleNamespace(action="HOLD", confidence=0.0)
    ok, reason = _shooting_execution_gate(
        d, snap(status="BROKER_READY", direction="UP",
                reason_code="UPWARD_FORCE_DOMINATES", confidence=46.0))
    assert ok
    assert d.action == "BUY"
    assert d.confidence == 46.0


def test_subthreshold_power_lead_is_shadow_candidate_and_blocked(monkeypatch):
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 70.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_CONF_FLOOR", 30.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_ENABLED", True)
    monkeypatch.setattr(main.config, "POWER_LEAD_SHADOW_ENABLED", True)
    monkeypatch.setattr(main, "_record", lambda *_args, **_kwargs: None)
    d = SimpleNamespace(action="HOLD", confidence=0.0, rationale="",
                        model="none-quiet")
    s = snap(status="BROKER_READY", direction="UP",
             reason_code="UPWARD_FORCE_DOMINATES", confidence=35.0)
    s.signal_direction = "BUY"
    s.shadow_candidates = {}

    ok, reason = _shooting_execution_gate(d, s)

    assert not ok
    assert "shadow candidate only" in reason
    assert d.action == "HOLD"
    candidate = s.shadow_candidates["power_lead"]
    assert candidate["eligible"] is True
    assert candidate["shadow_only"] is True
    assert candidate["execution_enabled"] is False
    assert candidate["signal_confidence"] == 35.0
    assert candidate["live_confidence_threshold"] == 70.0


def test_subthreshold_power_lead_shadow_records_specific_blockers(monkeypatch):
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 70.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_CONF_FLOOR", 30.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_SHADOW_ENABLED", True)
    monkeypatch.setattr(main, "_record", lambda *_args, **_kwargs: None)
    d = SimpleNamespace(action="HOLD", confidence=0.0, rationale="",
                        model="none-quiet")
    s = snap(status="BROKER_READY", direction="UP",
             reason_code="UPWARD_FORCE_DOMINATES", confidence=35.0)
    s.signal_direction = "SELL"
    s.shadow_candidates = {}

    ok, reason = _shooting_execution_gate(d, s)

    assert ok
    assert "no new-entry action" in reason
    assert d.action == "HOLD"
    candidate = s.shadow_candidates["power_lead"]
    assert candidate["eligible"] is False
    assert "SIGNAL_DIRECTION_DISAGREES_WITH_POWER" in candidate["blockers"]


def test_shadow_off_does_not_store_a_power_candidate_or_open_the_gate(monkeypatch):
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 70.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_CONF_FLOOR", 30.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_SHADOW_ENABLED", "0")
    recorded = []
    monkeypatch.setattr(main, "_record", lambda *args, **kwargs: recorded.append(args))
    d = SimpleNamespace(action="HOLD", confidence=0.0, rationale="",
                        model="none-quiet")
    s = snap(direction="UP", reason_code="UPWARD_FORCE_DOMINATES", confidence=35.0)
    s.signal_direction = "BUY"
    s.shadow_candidates = {}

    ok, reason = _shooting_execution_gate(d, s)

    assert ok and reason == "no new-entry action"
    assert d.action == "HOLD"
    assert "power_lead" not in s.shadow_candidates
    assert any("shadow recording disabled" in str(row) for row in recorded)


def test_power_lead_candidate_can_be_recorded_when_only_passive_policy_blocks_plan(
        monkeypatch):
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 70.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_CONF_FLOOR", 30.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_SHADOW_ENABLED", True)
    monkeypatch.setattr(main, "_record", lambda *_args, **_kwargs: None)
    d = SimpleNamespace(action="HOLD", confidence=0.0, rationale="",
                        model="none-quiet")
    s = snap(status="PLAN_ONLY", direction="UP",
             reason_code="UPWARD_FORCE_DOMINATES", confidence=35.0)
    s.signal_direction = "BUY"
    passive = {"candidate": True, "candidate_eligible": True,
               "execution_enabled": False, "mode": "SHADOW_ONLY",
               "entry": 99.9, "stop": 97.9, "target": 104.8}
    s.shot = {"shot": "WAIT", "side": None, "entry_style": None,
              "shadow_candidates": {"passive_limit": passive},
              "visibility": {"primary_reason": "no executable entry style passed"}}
    s.shadow_candidates = {"passive_limit": passive}

    ok, reason = _shooting_execution_gate(d, s)

    assert not ok
    assert "shadow candidate only" in reason
    candidate = s.shadow_candidates["power_lead"]
    assert candidate["eligible"] is True
    assert candidate["plan_status"] == "WAIT"
    assert candidate["plan_shadow_only"] is True
    assert candidate["passive_route_candidate"]["entry"] == 99.9


def test_failed_carry_receipt_assignment_cannot_partially_open_the_action(monkeypatch):
    class BrokenRationaleDecision:
        def __init__(self):
            self.action = "HOLD"
            self.confidence = 0.0
            self.model = "none-quiet"
            self._rationale = ""

        @property
        def rationale(self):
            return self._rationale

        @rationale.setter
        def rationale(self, _value):
            raise AttributeError("read-only receipt")

    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 70.0)
    decision = BrokenRationaleDecision()
    snapshot = snap(direction="UP", reason_code="UPWARD_FORCE_DOMINATES",
                    confidence=70.0)

    ok, reason = _shooting_execution_gate(decision, snapshot)

    assert ok and reason == "no new-entry action"
    assert decision.action == "HOLD"
    assert decision.confidence == 0.0


def test_configured_threshold_power_carry_is_preserved(monkeypatch):
    monkeypatch.setattr(main.config, "EXECUTION_MODE", "python")
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 70.0)
    d = SimpleNamespace(action="HOLD", confidence=0.0, rationale="",
                        model="none-quiet")
    s = snap(status="BROKER_READY", direction="UP",
             reason_code="UPWARD_FORCE_DOMINATES", confidence=70.0)
    ok, reason = _shooting_execution_gate(d, s)
    assert ok
    assert "corridor open" in reason
    assert d.action == "BUY"
    assert d.confidence == 70.0
    assert d.model == "none-quiet"
    assert d.decision_source == "POWER_V2_CONFIGURED_THRESHOLD_CARRY"


def test_step4_never_calls_executor_for_a_shadow_only_candidate(monkeypatch):
    class Result:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    calls = []

    class Executor:
        def execute(self, decision, snapshot, *, execution_authorized=False):
            calls.append((decision, snapshot, execution_authorized))
            return Result(status="EXECUTED", reason="must not be reached")

    fake_step4 = types.ModuleType("step4_mt5_execution")
    fake_step4.ExecutionResult = Result
    fake_step4.MT5Executor = Executor
    monkeypatch.setitem(sys.modules, "step4_mt5_execution", fake_step4)
    monkeypatch.setattr(main.config, "TRADING_ENABLED", True)
    monkeypatch.setattr(main.config, "EXECUTION_MODE", "python")
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 70.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_CONF_FLOOR", 30.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_SHADOW_ENABLED", True)
    monkeypatch.setattr(main, "_record", lambda *_args, **_kwargs: None)

    decision = SimpleNamespace(action="HOLD", confidence=0.0, rationale="",
                               model="none-quiet")
    snapshot = snap(direction="UP", reason_code="UPWARD_FORCE_DOMINATES",
                    confidence=35.0)
    snapshot.signal_direction = "BUY"
    snapshot.shadow_candidates = {}

    result = main.run_step4(decision, snapshot, execution_authorized=True)

    assert result.status == "SKIPPED"
    assert "shadow candidate only" in result.reason
    assert calls == []
    assert decision.action == "HOLD"


def test_failed_safety_gate_cannot_be_overridden_by_power_carry(monkeypatch):
    class Result:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    calls = []

    class Executor:
        def execute(self, decision, snapshot, *, execution_authorized=False):
            calls.append((decision, snapshot, execution_authorized))
            return Result(status="EXECUTED", reason="must not be reached")

    fake_step4 = types.ModuleType("step4_mt5_execution")
    fake_step4.ExecutionResult = Result
    fake_step4.MT5Executor = Executor
    monkeypatch.setitem(sys.modules, "step4_mt5_execution", fake_step4)
    monkeypatch.setattr(main.config, "TRADING_ENABLED", True)
    monkeypatch.setattr(main.config, "EXECUTION_MODE", "python")
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 35.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_CONF_FLOOR", 30.0)
    monkeypatch.setattr(main, "_record", lambda *_args, **_kwargs: None)

    # This is the HOLD that run_pipeline creates after a blocked feed gate.
    decision = SimpleNamespace(action="HOLD", confidence=0.0,
                               rationale="safety gate: stale quote",
                               model="none-safety-gate")
    snapshot = snap(status="BROKER_READY", direction="UP",
                    reason_code="UPWARD_FORCE_DOMINATES", confidence=35.0)
    snapshot.quality_evidence = {
        "quote_status": "stale", "safety_gate_status": "BLOCKED"}

    result = main.run_step4(
        decision, snapshot, execution_authorized=False,
        authorization_reason="live quote not validated (stale)")

    assert result.status == "SKIPPED"
    assert "live quote not validated" in result.reason
    assert decision.action == "HOLD"
    assert decision.confidence == 0.0
    assert calls == []


@pytest.mark.parametrize(
    "allow_new_entries, gate_ok, expected_authorized",
    [(True, False, False), (False, True, False), (True, True, True)],
)
def test_pipeline_passes_hard_authorization_to_step4(
        monkeypatch, allow_new_entries, gate_ok, expected_authorized):
    import main

    class Decision:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class TradeMonitor:
        def single_pass(self):
            return []
        def check_closed_deals(self, **_kwargs):
            return []

    manager_calls = []
    bridge_calls = []
    monitor_calls = []

    class PositionManager:
        def manage(self, _snapshot):
            manager_calls.append(True)
            return None

    modules = {
        "maintenance": {"run_maintenance": lambda: None},
        "news": {"enrich_market_news": lambda _data: None},
        "macro": {"enrich_macro": lambda _data: None},
        "position_manager": {"get_position_manager": lambda: PositionManager()},
        "step3_ai_decision": {"Decision": Decision},
        "step5_monitoring": {"TradeMonitor": TradeMonitor},
    }
    for name, attributes in modules.items():
        module = types.ModuleType(name)
        for key, value in attributes.items():
            setattr(module, key, value)
        monkeypatch.setitem(sys.modules, name, module)

    snapshot = SimpleNamespace(timestamp=None, notes=[], regime="TREND")
    monkeypatch.setattr(main.config, "reload_env", lambda: None)
    monkeypatch.setattr(main.config, "RL_ENABLED", False, raising=False)
    monkeypatch.setattr(main.config, "LATENCY_LOG_ENABLED", False, raising=False)
    monkeypatch.setattr(main, "_record", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "_record_latency", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "_load_plumbing_test_position_ids", set)
    monkeypatch.setattr(main, "_load_tracked_position_ids", set)
    monkeypatch.setattr(main, "_save_tracked_position_ids", lambda *_args: None)
    monkeypatch.setattr(main, "_touch_lock", lambda: None)
    monkeypatch.setattr(main, "run_step1", lambda: {"has_data": True})
    monkeypatch.setattr(main, "run_step2", lambda _data: snapshot)
    monkeypatch.setattr(main, "_check_flash_crash", lambda _snapshot: "")
    monkeypatch.setattr(main, "_entries_paused", lambda: False)
    monkeypatch.setattr(main, "run_step3", lambda _snapshot: SimpleNamespace(
        action="BUY", confidence=100.0, rationale="synthetic", model="test"))
    monkeypatch.setattr(main, "_update_last_diary_ai", lambda *_args, **_kwargs: None)
    gate_reason = "live quote not validated (stale)" if not gate_ok else "ok"
    monkeypatch.setattr(main, "_safety_gates", lambda *_args, **_kwargs: (
        gate_ok, gate_reason))
    monkeypatch.setattr(main, "_preflight_data_reason", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main, "_market_quality_evidence", lambda *_args, **_kwargs: {
        "safety_gate_status": "PASS" if gate_ok else "BLOCKED",
        "quote_status": "valid" if gate_ok else "stale"})
    monkeypatch.setattr(main, "_attach_quality_evidence_to_shadow_candidates",
                        lambda *_args: None)
    monkeypatch.setattr(main, "_as_position_id", lambda value: value)
    monkeypatch.setattr(main, "_persist_market_quality_snapshot", lambda *_args: True)
    monkeypatch.setattr(main, "_load_logged_outcome_keys", set)
    monkeypatch.setattr(main, "_save_logged_outcome_keys", lambda *_args: None)
    monkeypatch.setattr(main, "_outcome_signature", lambda _deal: "sig")
    monkeypatch.setattr(main, "run_step5", lambda: monitor_calls.append(True) or [])
    monkeypatch.setattr(main, "run_signal_bridge",
                        lambda _snapshot, _decision, **kwargs:
                        bridge_calls.append(kwargs.get("allow_new_entries")))
    monkeypatch.setattr(main, "log_decision", lambda *_args: None)
    monkeypatch.setattr(main, "print_summary", lambda: None)

    calls = []
    def fake_run_step4(decision, snap, *, execution_authorized=False,
                       authorization_reason=""):
        calls.append((decision, snap, execution_authorized, authorization_reason))
        return SimpleNamespace(status="SKIPPED", reason="blocked")
    monkeypatch.setattr(main, "run_step4", fake_run_step4)

    main.run_pipeline(allow_new_entries=allow_new_entries)

    assert len(calls) == 1
    decision, seen_snapshot, authorized, reason = calls[0]
    assert seen_snapshot is snapshot
    assert authorized is expected_authorized
    if not gate_ok:
        assert decision.action == "HOLD"  # gate-generated HOLD remains intact
        assert "stale" in reason
    elif not allow_new_entries:
        assert decision.action == "BUY"  # analysis is preserved; entry is separately blocked
        assert "no-new-entries" in reason
    else:
        assert decision.action == "BUY"
        assert reason == ""
    assert bridge_calls == [expected_authorized]
    assert manager_calls == [True]  # position management is not disabled
    assert monitor_calls == [True]


def test_direct_decision_below_owner_threshold_fails_closed(monkeypatch):
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 70.0)
    ok, reason = _shooting_execution_gate(
        SimpleNamespace(action="BUY", confidence=69.9), snap())
    assert not ok
    assert "below owner-configured threshold" in reason


def test_out_of_range_confidence_never_authorizes_a_power_carry(monkeypatch):
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 70.0)
    recorded = []
    monkeypatch.setattr(main, "_record", lambda *args, **kwargs: recorded.append(args))
    s = snap(status="BROKER_READY", direction="UP",
             reason_code="UPWARD_FORCE_DOMINATES", confidence=101.0)
    s.signal_direction = "BUY"
    d = SimpleNamespace(action="HOLD", confidence=0.0)

    ok, reason = _shooting_execution_gate(d, s)

    assert ok
    assert reason == "no new-entry action"
    assert d.action == "HOLD"
    assert "invalid signal confidence" in recorded[-1][1]
    direct_ok, direct_reason = _shooting_execution_gate(
        SimpleNamespace(action="BUY", confidence=101.0), snap())
    assert not direct_ok
    assert "outside the valid 0..100 range" in direct_reason


def test_boolean_confidence_or_threshold_is_not_a_numeric_authorization(monkeypatch):
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 0.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_CONF_FLOOR", 0.0)
    monkeypatch.setattr(main, "_record", lambda *_args, **_kwargs: None)
    snapshot = snap(direction="UP", reason_code="UPWARD_FORCE_DOMINATES",
                    confidence=True)
    snapshot.signal_direction = "BUY"
    decision = SimpleNamespace(action="HOLD", confidence=0.0)

    ok, reason = _shooting_execution_gate(decision, snapshot)

    assert ok and reason == "no new-entry action"
    assert decision.action == "HOLD"
    direct_ok, direct_reason = _shooting_execution_gate(
        SimpleNamespace(action="BUY", confidence=True), snap())
    assert not direct_ok
    assert "invalid" in direct_reason

    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", True)
    direct_ok, direct_reason = _shooting_execution_gate(
        SimpleNamespace(action="BUY", confidence=75.0), snap())
    assert not direct_ok
    assert "invalid" in direct_reason


def test_doorman_advisory_requires_dominant_force_reason(monkeypatch):
    monkeypatch.setattr(main.config, "EXECUTION_MODE", "python")
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 45.0)
    d = SimpleNamespace(action="HOLD", confidence=0.0)
    ok, reason = _shooting_execution_gate(
        d, snap(status="BROKER_READY", direction="UP", confidence=60.0))
    assert ok
    assert "no new-entry action" in reason
    assert d.action == "HOLD"


def test_non_entry_action_does_not_need_new_entry_authorization():
    ok, _ = _shooting_execution_gate(SimpleNamespace(action="HOLD"), None)
    assert ok


def test_unknown_action_fails_closed():
    ok, reason = _shooting_execution_gate(SimpleNamespace(action="SHORT"), None)
    assert not ok
    assert "unrecognized Step-3 action" in reason


def test_actual_step4_handoff_routes_to_the_python_executor(monkeypatch):
    class Result:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    captured = {}

    class Executor:
        def execute(self, decision, snapshot, *, execution_authorized=False):
            captured["decision"] = decision
            captured["snapshot"] = snapshot
            captured["execution_authorized"] = execution_authorized
            return Result(status="EXECUTED", reason="stub executor ran")

    fake_step4 = types.ModuleType("step4_mt5_execution")
    fake_step4.ExecutionResult = Result
    fake_step4.MT5Executor = Executor
    monkeypatch.setitem(sys.modules, "step4_mt5_execution", fake_step4)
    monkeypatch.setattr(main.config, "TRADING_ENABLED", True)
    monkeypatch.setattr(main.config, "EXECUTION_MODE", "python")
    monkeypatch.setattr(main, "_record", lambda *_args, **_kwargs: None)

    d = SimpleNamespace(action="BUY", confidence=75.0)
    result = main.run_step4(
        d, snap(status="BROKER_READY"), execution_authorized=True)
    assert result.status == "EXECUTED"
    assert captured["decision"] is d
    assert captured["execution_authorized"] is True


def test_no_new_entries_signal_bridge_clears_actionable_signal(monkeypatch):
    captured = {}

    class Decision:
        def __init__(self, action, confidence, rationale):
            self.action = action
            self.confidence = confidence
            self.rationale = rationale

    def capture_signal(_snapshot, decision):
        captured["decision"] = decision
        return None

    fake_decision = types.ModuleType("step3_ai_decision")
    fake_decision.Decision = Decision
    fake_bridge = types.ModuleType("mt5_signal_bridge")
    fake_bridge.write_signal = capture_signal
    monkeypatch.setitem(sys.modules, "step3_ai_decision", fake_decision)
    monkeypatch.setitem(sys.modules, "mt5_signal_bridge", fake_bridge)
    monkeypatch.setattr(main.config, "EXECUTION_MODE", "ea")
    monkeypatch.setattr(main, "_shooting_execution_gate",
                        lambda *_args: (_ for _ in ()).throw(
                            AssertionError("no-entry mode must veto before the strategy gate")))
    monkeypatch.setattr(main, "_record", lambda *_args, **_kwargs: None)

    main.run_signal_bridge(
        snap(status="BROKER_READY"),
        SimpleNamespace(action="BUY", confidence=95.0),
        allow_new_entries=False)

    assert captured["decision"].action == "HOLD"
    assert captured["decision"].confidence == 0.0
    assert "new entries are disabled" in captured["decision"].rationale


def test_decision_journal_keeps_power_veto_model_outcome_and_shadow_receipt(
        monkeypatch, tmp_path):
    import csv
    import json

    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    snapshot = SimpleNamespace(
        symbol="XAUUSD", price=4200.0, signal_direction="BUY",
        signal_strength=82.0, confidence=68.0, regime="TREND",
        divergence=0.0, timestamp=None,
        power={"direction": "UP", "reason_code": "UPWARD_FORCE_DOMINATES"},
        shot={"visibility": {"status": "WAIT",
                              "primary_reason": "A2 trigger absent",
                              "sub_checks": ["aggressive trigger not confirmed"],
                              "session_state": "LONDON | market_open=yes"}},
        entry_simulation={"status": "NO_PLAN"},
        shadow_candidates={"passive_limit": {"candidate": True,
                                              "execution_enabled": False}})
    decision = SimpleNamespace(action="BUY", confidence=70.0,
                               model="none-throttled", rationale="Power carry at threshold",
                               decision_source="POWER_V2_CONFIGURED_THRESHOLD_CARRY")
    result = SimpleNamespace(status="SKIPPED", order_id="",
                             reason="no executable Shooting plan")

    main.log_decision(snapshot, decision, result)

    with (tmp_path / "decisions_log.csv").open(newline="", encoding="utf-8") as fh:
        row = next(csv.DictReader(fh))
    assert row["power_direction"] == "UP"
    assert row["shooting_status"] == "WAIT"
    assert "aggressive trigger not confirmed" in row["shooting_sub_checks"]
    assert row["ai_model"] == "none-throttled"
    assert row["decision_source"] == "POWER_V2_CONFIGURED_THRESHOLD_CARRY"
    assert row["exec_status"] == row["outcome"] == "SKIPPED"
    assert row["replay_status"] == "NOT_RUN_IN_LIVE_CYCLE"
    assert json.loads(row["shadow_candidates"])["passive_limit"]["execution_enabled"] is False


def test_decision_journal_schema_upgrade_preserves_older_rows(monkeypatch, tmp_path):
    import csv

    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    path = tmp_path / "decisions_log.csv"
    path.write_text(
        "timestamp,ai_model,exec_status,reason\n"
        "old-time,legacy-model,SKIPPED,legacy reason\n",
        encoding="utf-8")
    snapshot = SimpleNamespace(
        timestamp=None, symbol="XAUUSD", price=4200.0,
        signal_direction="NEUTRAL", signal_strength=0.0, confidence=0.0,
        regime="UNKNOWN", power={}, shot={}, entry_simulation={},
        shadow_candidates={})
    decision = SimpleNamespace(action="HOLD", confidence=0.0,
                               model="none-weak-signal", rationale="quiet")
    result = SimpleNamespace(status="SKIPPED", reason="no action", order_id="")

    main.log_decision(snapshot, decision, result)

    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fields = reader.fieldnames
    assert "shadow_candidates" in fields
    assert rows[0]["ai_model"] == "legacy-model"
    assert rows[0]["reason"] == "legacy reason"
    assert rows[1]["ai_model"] == "none-weak-signal"
    assert rows[1]["exec_status"] == "SKIPPED"
    assert None not in rows[0] and None not in rows[1]


def test_decision_journal_repairs_known_16_header_17_value_history(monkeypatch, tmp_path):
    import csv

    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    path = tmp_path / "decisions_log.csv"
    legacy_fields = list(main._LEGACY_DECISION_FIELDS_17)
    stale_header = [field for field in legacy_fields if field != "ai_model"]
    old_values = ["old-time", "XAUUSD", "4200", "BUY", "82", "68", "TREND",
                  "0", "HOLD", "0", "none-quiet", "SKIPPED", "", "QUIET",
                  "0", "", "legacy reason"]
    path.write_text(",".join(stale_header) + "\n" + ",".join(old_values) + "\n",
                    encoding="utf-8")
    snapshot = SimpleNamespace(
        timestamp=None, symbol="XAUUSD", price=4200.0,
        signal_direction="NEUTRAL", signal_strength=0.0, confidence=0.0,
        regime="UNKNOWN", power={}, shot={}, entry_simulation={},
        shadow_candidates={})
    decision = SimpleNamespace(action="HOLD", confidence=0.0,
                               model="none-weak-signal", rationale="quiet")
    result = SimpleNamespace(status="SKIPPED", reason="no action", order_id="")

    main.log_decision(snapshot, decision, result)

    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["ai_model"] == "none-quiet"
    assert rows[0]["exec_status"] == "SKIPPED"
    assert rows[0]["reason"] == "legacy reason"
    assert rows[1]["ai_model"] == "none-weak-signal"


def test_decision_journal_refuses_unknown_extra_width_without_rewriting(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    path = tmp_path / "decisions_log.csv"
    original = b"timestamp,ai_model\nold-time,legacy-model,unmapped-extra\n"
    path.write_bytes(original)
    snapshot = SimpleNamespace(
        timestamp=None, symbol="XAUUSD", price=4200.0,
        signal_direction="NEUTRAL", signal_strength=0.0, confidence=0.0,
        regime="UNKNOWN", power={}, shot={}, entry_simulation={},
        shadow_candidates={})
    decision = SimpleNamespace(action="HOLD", confidence=0.0,
                               model="none-quiet", rationale="quiet")
    result = SimpleNamespace(status="SKIPPED", reason="no action", order_id="")

    main.log_decision(snapshot, decision, result)

    assert path.read_bytes() == original
    assert not list(tmp_path.glob("decisions_20*.csv"))


def test_decision_journal_rejects_unknown_16_header_17_value_shape(monkeypatch, tmp_path):
    import main as main_module

    monkeypatch.setattr(main_module, "DATA_DIR", tmp_path)
    path = tmp_path / "decisions_log.csv"
    unknown_header = [
        name for name in main_module._LEGACY_DECISION_FIELDS_17
        if name != "ai_model"]
    unknown_header[0], unknown_header[1] = unknown_header[1], unknown_header[0]
    original = (",".join(unknown_header) + "\n"
                + ",".join(f"value-{i}" for i in range(17)) + "\n").encode()
    path.write_bytes(original)
    snapshot = SimpleNamespace(
        timestamp=None, symbol="XAUUSD", price=4200.0,
        signal_direction="NEUTRAL", signal_strength=0.0, confidence=0.0,
        regime="UNKNOWN", power={}, shot={}, entry_simulation={},
        shadow_candidates={})
    decision = SimpleNamespace(action="HOLD", confidence=0.0,
                               model="none-quiet", rationale="quiet")
    result = SimpleNamespace(status="SKIPPED", reason="no action", order_id="")

    main_module.log_decision(snapshot, decision, result)

    assert path.read_bytes() == original
    assert not list(tmp_path.glob("decisions_20*.csv"))


def test_actual_ea_bridge_handoff_neutralizes_ai_entry(monkeypatch):
    captured = {}

    class Decision:
        def __init__(self, action, confidence, rationale):
            self.action = action
            self.confidence = confidence
            self.rationale = rationale

    def capture_signal(_snapshot, decision):
        captured["decision"] = decision
        return None

    fake_decision = types.ModuleType("step3_ai_decision")
    fake_decision.Decision = Decision
    fake_bridge = types.ModuleType("mt5_signal_bridge")
    fake_bridge.write_signal = capture_signal
    monkeypatch.setitem(sys.modules, "step3_ai_decision", fake_decision)
    monkeypatch.setitem(sys.modules, "mt5_signal_bridge", fake_bridge)
    monkeypatch.setattr(main.config, "EXECUTION_MODE", "ea")
    monkeypatch.setattr(main, "_record", lambda *_args, **_kwargs: None)

    main.run_signal_bridge(snap(status="BROKER_READY"), SimpleNamespace(action="BUY", confidence=75.0))
    assert captured["decision"].action == "HOLD"
    assert "neutralised by Shooting/POWER v2" in captured["decision"].rationale
