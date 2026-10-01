import sys
import types
from types import SimpleNamespace

import main
from main import _shooting_execution_gate


def snap(status="PLAN_ONLY", side="BUY", direction="UP"):
    return SimpleNamespace(
        shot={"shot": "GO", "side": side, "entry_style": "PASSIVE_LIMIT",
              "execution_status": status},
        power={"direction": direction},
    )


def test_analysis_only_shooting_plan_cannot_authorize_order():
    ok, reason = _shooting_execution_gate(SimpleNamespace(action="BUY"), snap())
    assert not ok
    assert "analysis-only" in reason


def test_power_and_shooting_must_agree_on_side():
    ok, reason = _shooting_execution_gate(
        SimpleNamespace(action="BUY"), snap(status="BROKER_READY", direction="DOWN"))
    assert not ok
    assert "POWER v2" in reason


def test_wrong_shooting_side_cannot_authorize_order():
    ok, reason = _shooting_execution_gate(
        SimpleNamespace(action="BUY"), snap(status="BROKER_READY", side="SELL"))
    assert not ok
    assert "Shooting did not approve" in reason


def test_broker_ready_label_alone_cannot_enable_unimplemented_adapter():
    ok, reason = _shooting_execution_gate(
        SimpleNamespace(action="BUY"), snap(status="BROKER_READY"))
    assert not ok
    assert "no reviewed Step-4/EA adapter" in reason


def test_non_entry_action_does_not_need_new_entry_authorization():
    ok, _ = _shooting_execution_gate(SimpleNamespace(action="HOLD"), None)
    assert ok


def test_unknown_action_fails_closed():
    ok, reason = _shooting_execution_gate(SimpleNamespace(action="SHORT"), None)
    assert not ok
    assert "unrecognized Step-3 action" in reason


def test_actual_step4_handoff_cannot_execute_actionable_ai_decision(monkeypatch):
    class Result:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class Executor:
        def execute(self, *_args):
            raise AssertionError("Step 4 must not call the executor")

    fake_step4 = types.ModuleType("step4_mt5_execution")
    fake_step4.ExecutionResult = Result
    fake_step4.MT5Executor = Executor
    monkeypatch.setitem(sys.modules, "step4_mt5_execution", fake_step4)
    monkeypatch.setattr(main.config, "TRADING_ENABLED", True)
    monkeypatch.setattr(main.config, "EXECUTION_MODE", "python")
    monkeypatch.setattr(main, "_record", lambda *_args, **_kwargs: None)

    result = main.run_step4(
        SimpleNamespace(action="BUY"), snap(status="BROKER_READY"))
    assert result.status == "SKIPPED"
    assert "Shooting/POWER v2 veto" in result.reason


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

    main.run_signal_bridge(snap(status="BROKER_READY"), SimpleNamespace(action="BUY"))
    assert captured["decision"].action == "HOLD"
    assert "neutralised by Shooting/POWER v2" in captured["decision"].rationale
