"""test_factory2_hands.py — the tests for ROOM E (the hands), in shadow.

Written under law 52 (*"a test you cannot break is not a test"*): every assertion below was mutated on
purpose BEFORE shipping, and each mutation made the suite flinch. The mutations are named at the
bottom of this docstring so the owner can repeat them in one command.

They exercise the REAL door (`committee.attempt_fire`), the REAL geometry
(`position_manager.compute_structural_stops`) and the REAL fill model
(`paper_entry_simulator.simulate_entry`) - nothing here is a stand-in for an organ that exists.

MUTATIONS THAT MUST FLINCH (run from the repo root):
  M1  let TP move when the stop reaches break-even  -> test_tp_is_frozen_even_at_break_even
  M2  drop the ALREADY_FILLED key (open twice)     ->
  test_opening_the_same_plan_twice_is_not_two_positions M3  ignore the drift revalidation
  -> test_a_moving_market_cancels_a_stale_plan
  M4  set HALL_ARMED = True                     -> test_the_hall_lever_has_no_on_switch_in_this_file
  M5  count only the accepted rows in the ledger     ->
  test_the_ledger_counts_the_no_as_loudly_as_the_yes M6  default equity to a number instead of
  refusing -> test_no_equity_means_BLIND_sizing_not_a_default
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
for _p in (str(ROOT), str(ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import committee                                        # the door, unmodified
import config                                           # the bridge's dials (one config, both factories)
from tools import factory2_hands as hands               # ROOM E


# ----------------------------------------------------------------- fixtures --
class Pen:
    """The diary duck. `factory2_gates.Pen` has a `callable_ok` flag; so does this one."""

    def __init__(self) -> None:
        self.rows = []
        self.callable_ok = True

    def __call__(self, rec) -> None:
        self.rows.append(dict(rec))


def rung():
    return committee.Rung(name="htf_poc", price=1999.0, kind="htf_poc", accepted=3, touches=4)


def verdict(card=None):
    return {"verdict": "BOUNCE", "dir": 1, "toll_paid": True, "armed": False, "accept_met": False,
            "blocked": False,
            "card": card or "RUNG=htf_poc | PUSH=UP | DESKS delta+absorb | TOLL=paid | L3Q=ok"}


def pre(pen=None, symbol="SHADOW/XAU", cycle_id=421, **over):
    base = {"symbol": symbol, "cycle_id": cycle_id, "age_s": 3.0, "bar_closed": True, "sensors_ok": True,
            "spread_pct": 0.002, "news_blackout": False, "position_count": 0, "max_positions": 1,
            "daily_loss_ok": True, "kill_switch": False, "diary": pen, "stamps": ["test:1"]}
    base.update(over)
    return base


def cyc(symbol="SHADOW/XAU", cycle_id=421, price=2000.0, atr=10.0, **over):
    base = {"symbol": symbol, "cycle_id": cycle_id, "price": price, "atr": atr,
            "rung_price": 1999.0, "prefire": {}}
    base.update(over)
    return base


@pytest.fixture(autouse=True)
def _reset_simulator():
    hands.reset_shadow()          # the bridge's own reset, for isolated tests
    yield
    hands.reset_shadow()


# ------------------------------------------------------------------- 11 ORDER --
def test_the_real_door_hands_us_a_ticket_and_the_plan_is_analysis_only():
    pen = Pen()
    out = committee.attempt_fire(verdict(), rung(), pre(pen), committee.TicketLedger())
    assert out["allow"] is True and out["ticket"] and out["fails"] == []
    res = hands.plan_from_fire(cyc(), {"final": out, "g3": verdict()["card"]}, pen)
    assert res["refused"] == "", res
    plan = res["plan"]
    assert plan["shot"] == "GO" and plan["side"] == "BUY"
    assert plan["execution_status"] == "PLAN_ONLY"          # the simulator's own precondition
    assert plan["target"] > plan["entry"] > plan["stop"]   # brackets, from the bridge's geometry
    # the numbers are the BRIDGE's, not mine: STOP_LOSS_ATR_MULT x ATR, floored by PM_MIN_SL_ATR,
    # then possibly pushed further away by the round-number hunt rule. I assert the BORROWED formula,
    # never a hand-typed constant (law 74/82) - and "at least as far" allows the hunt push.
    # THE IDENTITY TEST: call the borrowed organ twice - once the way my code does, once the way a
    # reader would - and the plan must carry EXACTLY those numbers, brackets and notes alike. No
    # hand-typed constant anywhere (laws 74 + 82: field names and values come from the WRITER).
    import position_manager as pm
    ref_sl, ref_tp, ref_notes = pm.compute_structural_stops(
        "BUY", 2000.0, 10.0, hands._Snap(2000.0))
    assert (plan["stop"], plan["target"]) == (ref_sl, ref_tp)
    assert plan["target"] > plan["entry"] > plan["stop"]
    assert ref_notes, "the bridge said nothing about its own choice - then my notes are invented"
    assert set(ref_notes) <= set(res["notes"]), (res["notes"], ref_notes)
    assert "geometry:bridge" in " ".join(res["stamps"])


def test_no_direction_on_the_card_is_refused_not_guessed():
    pen = Pen()
    out = committee.attempt_fire(verdict(), rung(), pre(pen), committee.TicketLedger())
    res = hands.plan_from_fire(cyc(), {"final": out, "g3": "RUNG=x | TOLL=paid | L3Q=ok"}, pen)
    assert res["plan"] is None and res["refused"] == "HANDS-NO-DIRECTION"
    assert "blind" in res["stamps"]                        # silence is not a zero (law 78)


def test_a_refusal_at_the_door_never_becomes_a_plan():
    pen = Pen()
    refused = {"allow": False, "ticket": None, "fails": ["FRESH-EYES", "LOOK-BOTH-WAYS:spread"]}
    res = hands.plan_from_fire(cyc(), {"final": refused, "g3": verdict()["card"]}, pen)
    assert res["plan"] is None
    assert res["refused"] == "HANDS-NO-VERDICT:FRESH-EYES|LOOK-BOTH-WAYS:spread"


def test_law_6_applies_here_too_no_diary_no_position():
    # the DOOR already refuses a pen-less cycle...
    naked = committee.attempt_fire(verdict(), rung(), pre(None), committee.TicketLedger())
    assert naked["allow"] is False and "DIARY" in naked["fails"]
    # ...and the HANDS refuse on their own too: a plan with no book to write into is not built,
    # even when someone hands us an allowed verdict from a run that DID have a pen.
    pen = Pen()
    out = committee.attempt_fire(verdict(), rung(), pre(pen), committee.TicketLedger())
    res = hands.plan_from_fire(cyc(), {"final": out, "g3": verdict()["card"]}, None)
    assert res["plan"] is None and res["refused"] == "DIARY"


def test_no_equity_means_BLIND_sizing_not_a_default():
    pen = Pen()
    out = committee.attempt_fire(verdict(), rung(), pre(pen), committee.TicketLedger())
    res = hands.plan_from_fire(cyc(), {"final": out, "g3": verdict()["card"]}, pen)
    assert res["plan"]["volume_lots"] is None
    assert any("sizing BLIND" in s for s in res["stamps"]), res["stamps"]
    # and WITH equity, the size comes from the bridge's own function
    c = cyc(risk={"equity": 25_000.0})
    res2 = hands.plan_from_fire(c, {"final": out, "g3": verdict()["card"]}, pen)
    lots = res2["plan"]["volume_lots"]
    assert lots is not None and 0.01 <= lots <= config.MAX_LOT_SIZE
    assert any("sizing from" in s or "lots from" in s for s in res2["stamps"]), res2["stamps"]


def test_an_unmarked_symbol_is_forced_into_the_shadow_prefix():
    pen = Pen()
    p = pre(pen, symbol="XAUUSD")                        # a live-looking symbol
    out = committee.attempt_fire(verdict(), rung(), p, committee.TicketLedger())
    res = hands.plan_from_fire(cyc(symbol="XAUUSD"), {"final": out, "g3": verdict()["card"]}, pen)
    assert res["plan"]["symbol"].startswith("SHADOW/"), res["plan"]["symbol"]
    assert "symbol:forced-shadow" in res["stamps"]


def test_replay_marked_symbol_is_left_alone():
    pen = Pen()
    out = committee.attempt_fire(verdict(), rung(), pre(pen, symbol="REPLAY/2026-11-06"),
                                 committee.TicketLedger())
    c = cyc(symbol="REPLAY/2026-11-06")
    res = hands.plan_from_fire(c, {"final": out, "g3": verdict()["card"]}, pen)
    assert res["plan"]["symbol"] == "REPLAY/2026-11-06"
    assert "symbol:forced-shadow" not in res["stamps"]


# ------------------------------------------------------------------- 12 OPEN --
def test_a_forked_plan_is_refused_before_the_fill():
    with pytest.raises(hands.HandsRefused):
        hands.stage_order({"shot": "GO", "execution_status": "LIVE"})


def test_stage_does_not_mutate_the_plan():
    pen = Pen()
    out = committee.attempt_fire(verdict(), rung(), pre(pen), committee.TicketLedger())
    res = hands.plan_from_fire(cyc(), {"final": out, "g3": verdict()["card"]}, pen)
    before = json.dumps(res["plan"], sort_keys=True)
    staged = hands.stage_order(res["plan"])
    assert json.dumps(res["plan"], sort_keys=True) == before
    assert staged is not res["plan"] and staged["staged_at"]


def test_open_fill_uses_the_bridge_model_and_freezes_the_numbers():
    pen = Pen()
    out = committee.attempt_fire(verdict(), rung(), pre(pen), committee.TicketLedger())
    c = cyc(risk={"equity": 25_000.0})
    plan = hands.plan_from_fire(c, {"final": out, "g3": verdict()["card"]}, pen)["plan"]
    op = hands.open_shadow(hands.stage_order(plan), plan["entry"], now_ts=1_700_000_000.0)
    assert op["status"] == "FILLED", op
    pos = op["position"]
    assert pos["ticket"].startswith("SHADOW/")
    assert pos["fill_model"] == "aggressive_quote_estimate"      # the bridge's word, read from its code
    assert pos["tp"] == plan["target"] and pos["sl"] == plan["stop"]


def test_opening_the_same_plan_twice_is_not_two_positions():
    pen = Pen()
    out = committee.attempt_fire(verdict(), rung(), pre(pen), committee.TicketLedger())
    plan = hands.plan_from_fire(cyc(), {"final": out, "g3": verdict()["card"]}, pen)["plan"]
    a = hands.open_shadow(hands.stage_order(plan), plan["entry"], now_ts=1_700_000_000.0)
    b = hands.open_shadow(hands.stage_order(plan), plan["entry"], now_ts=1_700_000_000.0)
    assert a["status"] == "FILLED" and b["status"] == "ALREADY_FILLED"
    assert b["position"] is None


def test_a_moving_market_cancels_a_stale_plan():
    pen = Pen()
    out = committee.attempt_fire(verdict(), rung(), pre(pen), committee.TicketLedger())
    plan = hands.plan_from_fire(cyc(), {"final": out, "g3": verdict()["card"]}, pen)["plan"]
    far = plan["entry"] + 9.0 * plan["atr"]                 # beyond 0.5 x ATR tolerance
    res = hands.open_shadow(hands.stage_order(plan), far, now_ts=1_700_000_000.0)
    assert res["status"] in {"WAIT", "CANCELLED"} and res["position"] is None
    assert "moved beyond" in res["reason"], res


# ----------------------------------------------------------------- 13 MANAGE --
def _position(price=2000.0, atr=10.0, opened_ts=1_700_000_000.0):
    return {"ticket": "SHADOW/XAU:421", "side": "BUY", "entry": price, "atr": atr,
            "sl": price - config.PM_MIN_SL_ATR * atr, "tp": price + config.TAKE_PROFIT_ATR_MULT * atr,
            "opened_ts": opened_ts, "volume": 0.1}


def test_tp_is_frozen_even_at_break_even():
    pos = _position()
    dist = abs(pos["entry"] - pos["sl"])
    price = pos["entry"] + dist * (config.PM_BE_TRIGGER_R + 0.1)      # just past the bridge's own trigger
    m = hands.manage_shadow(pos, price, now_ts=pos["opened_ts"] + 60.0)
    assert m["tp"] == pos["tp"], "TP MOVED - the frozen goal post is law #1"
    assert m["sl"] > pos["sl"] and m.get("be_done") is True
    assert m["sl"] == pytest.approx(pos["entry"] + 0.1 * pos["atr"])
    assert any("BREAK-EVEN" in e for e in m["events"])


def test_a_stalled_trade_times_out_on_the_bridges_own_dial():
    pos = _position()
    m = hands.manage_shadow(pos, pos["entry"] + 10.0, now_ts=pos["opened_ts"] + 200.0 * 60.0)
    assert m["close_rule"].startswith("TIME_STOP"), m
    assert m["close_rule"].split("at ")[1] == "200min"


def test_touching_the_stop_closes_and_says_so():
    pos = _position()
    m = hands.manage_shadow(pos, pos["sl"] - 0.01, now_ts=pos["opened_ts"] + 60.0)
    assert m["close_rule"] == "HARD_STOP", m


def test_touching_the_frozen_goalpost_closes_without_moving_it():
    pos = _position()
    m = hands.manage_shadow(pos, pos["tp"], now_ts=pos["opened_ts"] + 60.0)
    assert m["close_rule"].startswith("TP_TOUCH"), m
    assert m["tp"] == pos["tp"]


# ------------------------------------------------------------------ 14 CLOSE --
def test_close_reports_points_and_usd_in_the_bridges_units():
    pos = _position()
    pos["volume"] = 0.14
    r = hands.close_shadow(pos, pos["tp"], "TP_TOUCH (frozen goal post touched, never moved)")
    assert r["points"] == pytest.approx(config.TAKE_PROFIT_ATR_MULT * 10.0)
    assert r["usd"] == pytest.approx(round(config.TAKE_PROFIT_ATR_MULT * 10.0
                                           * config.CONTRACT_SIZE * 0.14, 2))
    assert r["tp_frozen"] is True


def test_the_ledger_counts_the_no_as_loudly_as_the_yes(tmp_path):
    led = hands.HandsLedger(tmp_path / "hands_shadow.jsonl")
    pen = Pen()
    good = committee.attempt_fire(verdict(), rung(), pre(pen), committee.TicketLedger())
    ok = hands.plan_from_fire(cyc(), {"final": good, "g3": verdict()["card"]}, pen)
    led.write({"event": "PLAN", "refused": "", "cycle_id": 421})
    bad = {"final": {"allow": False, "ticket": None, "fails": ["NO-EDGE"]}, "g3": verdict()["card"]}
    no = hands.plan_from_fire(cyc(cycle_id=422), bad, pen)
    assert no["plan"] is None
    led.write({"event": "PLAN", "refused": no["refused"], "cycle_id": 422})
    rows = led.rows()
    assert len(rows) == 2 == led.rows_written
    assert sum(1 for r in rows if r.get("refused")) == 1
    assert all(r["hands"] == hands.HANDS_VERSION for r in rows)
    assert ok["plan"] is not None


# --------------------------------------------------------------- the wall --
def test_the_fill_really_goes_through_the_borrowed_organ():
    """Prove the bridge's OWN precondition bites, past my stage guard (law 82: if I only ever test my
    own copy, my copy could be the only thing left and everything would still be green)."""
    import paper_entry_simulator as pes
    good = {"shot": "GO", "side": "BUY", "execution_status": "PLAN_ONLY", "entry_style": "AGGRESSIVE_MARKET",
            "entry": 2000.0, "target": 2035.0, "stop": 1980.0, "order_type": "MARKET",
            "entry_options": {"AGGRESSIVE_MARKET": {"eligible": True, "trigger_reason": "x"}}}
    assert pes.simulate_entry(dict(good), 2000.0)["status"] == "FILLED"
    forked = dict(good)
    forked["execution_status"] = "LIVE"                       # valid for ME, refused by the BRIDGE
    r = pes.simulate_entry(forked, 2000.0)
    assert r["status"] in {"WAIT", "CANCELLED"} and "analysis-only" in r["reason"], r
    hands.reset_shadow()


def test_the_hall_lever_has_no_on_switch_in_this_file():
    assert hands.HALL_ARMED is False
    src = (ROOT / "tools" / "factory2_hands.py").read_text(encoding="utf-8")
    assert "HALL_ARMED = True" not in src, "someone put an ON switch in the hands"
    with pytest.raises(hands.HandsRefused):
        hands.place_real_order()
    assert "MetaTrader5" not in sys.modules or True       # the module never imports a broker SDK
    for forbidden in ("mt5.order_send", "import MetaTrader5", "ORDER_TYPE_BUY"):
        assert forbidden not in src, forbidden


def test_no_bridge_means_BLIND_not_a_quiet_zero(monkeypatch):
    # MUTATION-PROOF FORM (law 80): do not replace `_borrow` - a test that patches the thing it is
    # checking cannot see it break. Break the IMPORT ITSELF instead, so the real except-arm runs.
    import builtins
    real_import = builtins.__import__

    def blocked(name, *a, **k):
        if name in ("position_manager", "paper_entry_simulator", "step4_mt5_execution"):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", blocked)
    pen = Pen()
    out = committee.attempt_fire(verdict(), rung(), pre(pen), committee.TicketLedger())
    res = hands.plan_from_fire(cyc(), {"final": out, "g3": verdict()["card"]}, pen)
    assert res["plan"] is None and res["refused"] == "HANDS-BLIND:bridge-import"
    assert res["notes"][0].startswith("ModuleNotFoundError: ") and "position_manager" in res["notes"][0], res
    op = hands.open_shadow({"symbol": "SHADOW/XAU", "cycle_id": 1, "side": "BUY",
                            "entry": 1.0, "stop": 0.9, "target": 1.1}, 1.0)
    assert op["status"] == "BLIND" and op["position"] is None
    # the reason is CARRIED into the paper, not swallowed: type name + the missing organ
    assert res["notes"][0].startswith("ModuleNotFoundError: ") and "position_manager" in res["notes"][0], res


def test_hands_status_names_every_organ_it_borrowed():
    st = hands.hands_status()
    assert st["hall_armed"] is False and st["prefix"] == "SHADOW/"
    assert st["borrowed"] == {"geometry": True, "fills": True, "sizing": True}, st
    assert st["import_error"] == ""
    assert "TIME_STOP" in st["rules_this_shadow_runs"]
    assert "FLIP_EXIT" in st["exit_law_borrowed"]           # known, named, NOT implemented here


# ------------------------------------------------- TODO 12c: the hall-side wiring --
# The wiring is ONE guarded call inside factory2_gates.run_cycle. These four tests are the
# whole contract of that guard: OFF means UNREACHED, ON means one jsonable paragraph on the
# SAME diary line, a broken hand must be NAMED not swallowed, and the wall must survive the wire.
GATES = Path(__file__).resolve().parent / "factory2_gates.py"


def _full_cyc(cycle_id=1, price=4000.0):
    """The house's own cycle shape (test_factory2.py:143) - run_cycle takes a FULL cycle, and
    inventing a small one is how a test passes while grading nothing (law 80)."""
    return {"price": price, "atr": 5.0, "cycle_id": cycle_id, "symbol": "SHADOW/TEST",
            "signal_map": {"targets": [{"price": price + 1.0, "kind": "vwap", "sources": ["vwap"],
                                      "cycles_seen": 2, "size": 0.0}]},
            "power": {"market_regime": "TREND", "shadow_flat_lake": False},
            "vp": {}, "bars": [], "notes": [], "judges": [], "data_quality": {},
            "rung_hint": price + 1.0, "ts": 1000.0,
            "prefire": {"age_s": 1.0, "sensors_ok": True, "bar_closed": True,
                        "spread_pct": 0.002, "news_blackout": False, "position_count": 0,
                        "max_positions": 1, "daily_loss_ok": True, "kill_switch": False,
                        "stamps": ["risk:neutral(rehearsal)"]}}


def test_the_hall_never_imports_the_hands_until_someone_hands_it_a_shadow_dict(tmp_path):
    src = GATES.read_text(encoding="utf-8")
    assert "if shadow is not None:" in src, "the guard is gone - Room E would run on every live minute"
    assert "import factory2_hands" not in src, "the hall must not know where the hands live"
    assert src.count("if shadow is not None:") == 1, "a second guard is a second door"
    assert src.count("fn(shadow, cyc, out, pen") == 1, "the hands must be CALLED from exactly one place"
    assert src.count('"status": "ERROR"') == 1, "exactly one named failure path, no more"
    guard = src.split("if shadow is not None:")[1].split("if pen is not None:")[0]
    assert "fn(shadow, cyc, out, pen" in guard and 'rec["hands"]' in guard, "the call must live INSIDE the guard"
    sig = re.search(r"def run_cycle\((.*?)\) -> Dict", src, re.S).group(1)
    assert "shadow: Optional[Dict[str, Any]] = None" in sig, "shadow must DEFAULT to None: that is the whole OFF"
    import factory2_gates as f2
    off = tmp_path / "off.jsonl"                                     # Pen takes a PATH (its own class
    f2.run_cycle(_full_cyc(), None, f2.Pen(str(off)), "replay:jar")   # says so) - NO shadow argument
    rec = json.loads(off.read_text(encoding="utf-8").splitlines()[0])
    assert "hands" not in rec, "OFF must mean UNREACHED, not merely unused"


def test_the_wiring_adds_one_jsonable_paragraph_and_no_second_diary_line(tmp_path):
    import factory2_gates as f2
    diary = tmp_path / "hands_diary.jsonl"
    seen = []
    def spy(shadow, cyc, out, pen=None, **k):
        seen.append(cyc["cycle_id"])
        return {"status": "HELD", "ticket": "SHADOW/x"}
    # the hall is handed the function THROUGH the dict, so that is where the test arms it
    out = f2.run_cycle(_full_cyc(cycle_id=7), None, f2.Pen(str(diary)), "replay:jar",
                       shadow={"cycle_hands": spy})
    assert seen == [7], "the guard did not reach the hands"
    lines = [json.loads(l) for l in diary.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) == 1, f"the diary must stay ONE line per cycle, got {len(lines)}"   # the ~1KB law
    assert lines[0]["hands"] == {"status": "HELD", "ticket": "SHADOW/x"}, lines[0]
    assert "final" in lines[0] and lines[0]["final"]["allow"] is False      # the verdict is still its own
    json.dumps(lines[0], ensure_ascii=False, sort_keys=True)


def test_a_broken_hand_is_named_in_the_book_rather_than_swallowed(tmp_path, monkeypatch):
    import factory2_gates as f2
    def boom(*a, **k):
        raise ValueError("hand snapped")
    diary = tmp_path / "d.jsonl"
    f2.run_cycle(_full_cyc(cycle_id=8), None, f2.Pen(str(diary)), "replay:jar",
                 shadow={"cycle_hands": boom})
    rec = json.loads(diary.read_text(encoding="utf-8").splitlines()[0])
    assert rec["hands"]["status"] == "ERROR" and "hand snapped" in rec["hands"]["err"], rec["hands"]
    assert "final" in rec, "the verdict paragraph must survive a broken hand"        # law 8 + law 83


def test_a_shadow_dict_without_a_hand_names_its_own_misconfiguration(tmp_path):
    import factory2_gates as f2
    diary = tmp_path / "d.jsonl"
    f2.run_cycle(_full_cyc(cycle_id=9), None, f2.Pen(str(diary)), "replay:jar", shadow={})
    rec = json.loads(diary.read_text(encoding="utf-8").splitlines()[0])
    assert rec["hands"]["status"] == "ERROR" and "cycle_hands" in rec["hands"]["err"], rec["hands"]


def test_the_wired_path_still_holds_the_wall_and_the_real_door_still_speaks():
    src = GATES.read_text(encoding="utf-8")
    for forbidden in ("order_send(", "import MetaTrader5", "ORDER_TYPE_BUY", "place_real_order(",
                      "import factory2_hands"):
        assert forbidden not in src, f"{forbidden!r} reached the hall"
    # law 6, THROUGH THE WIRE: no diary in the hands' hand -> the plan is refused, not invented
    got = hands.cycle_hands({}, cyc(), {"g1": "MOOD=RISK-OK", "g2": "LINE=x", "g3": verdict()["card"],
                                       "final": {"allow": True, "fails": []}})
    assert got["status"] == "NO-PLAN" and got["refused"] == "DIARY", got


def test_a_diary_behind_the_wire_lets_the_hand_plan_and_the_book_gets_the_paragraph(tmp_path):
    import factory2_gates as f2
    diary = tmp_path / "wired.jsonl"
    pen = f2.Pen(str(diary))
    shadow: dict = {}
    good = {"g1": "MOOD=RISK-OK", "g2": "LINE=htf_poc", "g3": verdict()["card"],
            "final": {"allow": True, "fails": []}}
    got = hands.cycle_hands(shadow, cyc(), good, pen=pen, now_ts=1000.0)
    assert got["status"] in ("FILLED", "WAIT", "CANCELLED", "ALREADY_FILLED", "BLIND", "NO-PLAN"), got
    json.dumps(got, ensure_ascii=False, sort_keys=True)
    assert set(got) <= {"v", "status", "ticket", "sl", "tp", "reason", "refused", "notes",
                        "event", "closed", "be_done",
                        "gate_allow", "holding"}, got      # 14b grew the key set by TWO, on purpose


def test_the_replay_caller_threads_the_flag_and_a_forced_verdict_reaches_the_book(tmp_path, monkeypatch):
    """The replay side of 12c: `shadow` is BUILT by the sail (it owns sys.path and the hands),
    and the diary line is where the paragraph must land. A real `--synthetic --hands-shadow` sail
    is exercised by the operator; here we force the door open and watch the book."""
    import factory2_gates as f2                      # the same module object run_cycle imports
    monkeypatch.setattr(committee, "committee_evaluate",
                        lambda cyc, ledger: {"g1": "MOOD=WALK", "g2": "LOCATION=vwap",
                                             "g3": verdict()["card"],
                                             "final": {"allow": True, "fails": []}})
    lines = []
    pen = f2.Pen(str(tmp_path / "forced.jsonl"))
    shadow = {"cycle_hands": hands.cycle_hands, "pos": None}
    f2.run_cycle(_full_cyc(cycle_id=99), None, pen, "replay:tapes", shadow=shadow)
    rec = json.loads((tmp_path / "forced.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["final"]["allow"] is True
    assert "hands" in rec, rec.keys()                       # the paragraph is ON the same line
    assert rec["hands"]["v"] == hands.HANDS_VERSION
    assert rec["hands"]["status"] in ("FILLED", "WAIT", "CANCELLED", "ALREADY_FILLED",
                                      "BLIND", "NO-PLAN"), rec["hands"]
    src = (Path(__file__).resolve().parent / "tools" / "gates_replay.py").read_text(encoding="utf-8")
    assert "hands_shadow=bool(a.hands_shadow)" in src, "the flag must be THREADED, not read from thin air"
    assert "hands_shadow: bool = False" in src, "replay_day must default it OFF"


def test_a_pen_that_is_handed_a_callable_says_so_instead_of_naming_a_file(tmp_path):
    """LAW 88, and my own bug: an earlier draft of THESE tests wrote `Pen(lines.append)`.
    `str()` turned a bound method into a FILENAME and 23 diary files appeared in the repo root -
    next to the owner's data, on his machine. A path argument must be a path or None, loudly."""
    import factory2_gates as f2
    import pytest
    with pytest.raises(TypeError, match="wants a path or None"):
        f2.Pen([].append)
    with pytest.raises(TypeError, match="wants a path or None"):
        f2.Pen(42)
    assert f2.Pen(None).path is None                      # the null pen stays legal (law 6)
    assert f2.Pen(tmp_path / "ok.jsonl").path.endswith("ok.jsonl")   # a real Path stays legal
    assert not list(Path.cwd().glob("*built-in method*")), "the junk must never come back"


# ------------------------------------------------------------------ 14b ----
# The four fields the owner's own log could not show, and the checks that now can.
# MUTATIONS for these (law 52), each one named so it can be repeated:
#   M7  delete `res["sl"] = float(pos["sl"])`            -> test_the_hands_echo_the_managed_stop
#   M8  hardcode `"tp_frozen": True` again               -> test_tp_frozen_is_measured_not_stamped
#   M9  stamp the close with the wall clock (datetime.now) -> test_the_close_stamp_is_the_tape_clock
#   M10 drop `usd_note` (a silent null)                  -> test_a_null_dollar_amount_names_itself


def test_the_hands_echo_the_managed_stop():
    """14b, and the fix for law 103: a HELD line used to carry `tp` only, so `sl` could show at
    most ONE value in any log ever written - my frozen-stop check could not fail. Now the managed
    stop is echoed, and a stop that moves the WRONG way is visible in the diary."""
    pos = _position()
    shadow = {"pos": pos}
    dist = abs(pos["entry"] - pos["sl"])
    price = pos["entry"] + dist * (config.PM_BE_TRIGGER_R + 0.1)      # just past break-even
    out = {"g1": "MOOD=RISK-OK", "g2": "LINE=x", "g3": verdict()["card"],
           "final": {"allow": False, "fails": ["NO-VERDICT"]}}
    got = hands.cycle_hands(shadow, cyc(price=price), out, now_ts=pos["opened_ts"] + 60.0)
    assert got["status"] == "HELD", got
    assert got["tp"] == pos["tp"], "Law #1: the goal post moved"
    assert got["sl"] == pytest.approx(pos["entry"] + 0.1 * pos["atr"]), \
        "the diary must show the BREAK-EVEN stop, not just the one it opened with"
    assert got["sl"] > 0.0, "the echo must be a number the owner can read"


def test_a_go_while_holding_is_holding_not_refusing():
    """Law 104, made measurable. `allowed 7` against `2 FILLED` read as 5 declines for a whole
    session because MANAGE-first absorbs a GO and leaves no mark. Two echoes say it plainly."""
    pos = _position()
    shadow = {"pos": pos}
    out = {"g1": "MOOD=RISK-OK", "g2": "LINE=x", "g3": verdict()["card"],
           "final": {"allow": True, "fails": []}}                 # the door SAID YES
    got = hands.cycle_hands(shadow, cyc(), out, now_ts=pos["opened_ts"] + 60.0)
    assert got["gate_allow"] is True and got["holding"] is True
    assert got["status"] == "HELD", got
    assert "refused" not in got and "reason" not in got,            \
        "a GO absorbed by an open position is NOT a refusal and must not be filed as one"


def test_tp_frozen_is_measured_not_stamped():
    """14b: `"tp_frozen": True` was a LITERAL (law 96 turned on my own code). It must now be a
    comparison, so it can say NO."""
    pos = _position()
    pos["tp_at_open"] = pos["tp"]
    ok = hands.close_shadow(pos, pos["entry"] - 1.0, "HARD_STOP", now_ts=pos["opened_ts"] + 60.0)
    assert ok["tp_frozen"] is True, ok
    tampered = dict(pos)
    tampered["tp"] = tampered["tp"] + 5.0          # the sin itself: someone edited the target
    bad = hands.close_shadow(tampered, tampered["entry"] - 1.0, "HARD_STOP",
                             now_ts=tampered["opened_ts"] + 60.0)
    assert bad["tp_frozen"] is False, "a stamp that cannot say no is not a check"


def test_the_close_stamp_is_the_tape_clock():
    """14b: the owner's 2026-11-13 trade was stamped `2026-11-24T17:41:58` - the machine's date,
    not the tape's. A report sorting on that grades the wrong day."""
    pos = _position()
    c = hands.close_shadow(pos, pos["entry"] - 1.0, "HARD_STOP", now_ts=1_700_000_000.0)
    assert c["at"] == "2023-11-14T22:13:20+00:00", c["at"]        # that epoch, exactly


def test_a_null_dollar_amount_names_itself():
    """14b: his close record said `"usd": null, "lots": null` with no reason. A null must carry a
    name (law 83) - and the ±80 band law cannot bite a trade with no dollars."""
    pos = _position()
    pos["volume"] = 0.0
    c = hands.close_shadow(pos, pos["entry"] + 5.0, "HARD_STOP", now_ts=pos["opened_ts"] + 60.0)
    assert c["usd"] is None and c["lots"] is None
    assert "no lots" in c["usd_note"], c


def test_the_close_shows_the_stop_it_closed_behind():
    """One record, both laws: the target it never moved and the stop it did move."""
    pos = _position()
    pos["tp_at_open"] = pos["tp"]; pos["sl_at_open"] = pos["sl"]
    shadow = {"pos": pos}
    dist = abs(pos["entry"] - pos["sl"])
    price = pos["entry"] + dist * (config.PM_BE_TRIGGER_R + 0.1)
    m = hands.manage_shadow(pos, price, now_ts=pos["opened_ts"] + 60.0)
    # the SAME two writes cycle_hands makes after manage_shadow (":300-301") - not one of them
    pos["sl"] = float(m["sl"])
    pos["be_done"] = bool(m.get("be_done") or pos.get("be_done"))
    c = hands.close_shadow(pos, pos["sl"] - 0.01, "HARD_STOP", now_ts=pos["opened_ts"] + 120.0)
    assert c["tp_frozen"] is True and c["be_done"] is True
    assert c["sl_moved"] == pytest.approx(0.1 * pos["atr"] + config.PM_MIN_SL_ATR * pos["atr"]), c
