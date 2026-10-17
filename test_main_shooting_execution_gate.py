import sys
import types
from types import SimpleNamespace

import main
from main import _shooting_execution_gate

# gates:v1 (2026-10-15 night) — updated for THE FRIDAY GATES:
# the corridor is now OPEN in python execution mode (owner-ordered reviewed
# change; the old "never execute" tests are inverted to pin the NEW policy),
# the doorman is advisory when absent, and every veto that still protects
# is kept exactly as it was.


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
    ok, reason = _shooting_execution_gate(SimpleNamespace(action="BUY"), snap())
    assert ok
    assert "corridor open" in reason


def test_corridor_stays_closed_outside_python_mode(monkeypatch):
    monkeypatch.setattr(main.config, "EXECUTION_MODE", "none")
    ok, reason = _shooting_execution_gate(
        SimpleNamespace(action="BUY"), snap(status="BROKER_READY"))
    assert not ok
    assert "corridor closed" in reason


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


def test_doorman_advisory_denies_when_panel_confidence_below_gate(monkeypatch):
    # fusionv1 (2026-10-17, owner-ordered): this test WAS the old policy's
    # witness (conf 30 + POWER dominant -> deny). The power-lead advisory now
    # carries exactly that minute (floor 30 + agreement; evidence:
    # council_shadow 10-15/10-16). Pinned to the NEW policy below; the old
    # denial survives one floor lower (29 < 30) and via the off-switch.
    monkeypatch.setattr(main.config, "EXECUTION_MODE", "python")
    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 45.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_CONF_FLOOR", 30.0)
    monkeypatch.setattr(main.config, "POWER_LEAD_ENABLED", True)
    d = SimpleNamespace(action="HOLD", confidence=0.0, rationale="",
                        model="none-quiet")
    ok, reason = _shooting_execution_gate(
        d, snap(status="BROKER_READY", direction="UP",
                reason_code="UPWARD_FORCE_DOMINATES", confidence=30.0))
    assert ok
    assert "corridor open" in reason          # NEW: the lead carries conf 30
    assert d.action == "BUY"
    assert d.model == "power-lead-v1"
    # the floor itself still denies below 30
    d2 = SimpleNamespace(action="HOLD", confidence=0.0, rationale="",
                         model="none-quiet")
    ok2, reason2 = _shooting_execution_gate(
        d2, snap(status="BROKER_READY", direction="UP",
                 reason_code="UPWARD_FORCE_DOMINATES", confidence=29.0))
    assert ok2
    assert "no new-entry action" in reason2
    assert d2.action == "HOLD"


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
        def execute(self, decision, snapshot):
            captured["decision"] = decision
            captured["snapshot"] = snapshot
            return Result(status="EXECUTED", reason="stub executor ran")

    fake_step4 = types.ModuleType("step4_mt5_execution")
    fake_step4.ExecutionResult = Result
    fake_step4.MT5Executor = Executor
    monkeypatch.setitem(sys.modules, "step4_mt5_execution", fake_step4)
    monkeypatch.setattr(main.config, "TRADING_ENABLED", True)
    monkeypatch.setattr(main.config, "EXECUTION_MODE", "python")
    monkeypatch.setattr(main, "_record", lambda *_args, **_kwargs: None)

    d = SimpleNamespace(action="BUY", confidence=55.0)
    result = main.run_step4(d, snap(status="BROKER_READY"))
    assert result.status == "EXECUTED"
    assert captured["decision"] is d


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
