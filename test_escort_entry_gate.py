from escort_team import EscortBook


def base_plan():
    return {"shot": "GO", "side": "BUY", "entry": 100.0,
            "target": 105.0, "stop": 98.0, "door": {"price": 105.0},
            "entry_style": "PASSIVE_LIMIT", "execution_status": "PAPER_ONLY",
            "paper_fill_model": "trade_through_one_tick"}


def test_escort_does_not_open_on_shooter_go_alone():
    book = EscortBook()
    plan = base_plan()
    plan.pop("entry_filled", None)
    assert book.maybe_open(plan, 100.0) is None
    assert book.open == []


def test_escort_opens_only_after_simulated_passive_fill():
    book = EscortBook()
    plan = base_plan()
    plan["entry_filled"] = True
    trade = book.maybe_open(plan, 100.0)
    assert trade is not None
    assert trade.entry_style == "PASSIVE_LIMIT"
    assert trade.paper_fill_model == "trade_through_one_tick"


def test_escort_rejects_aggressive_fill_without_trigger_audit():
    book = EscortBook()
    plan = base_plan()
    plan.update(entry_style="AGGRESSIVE_MARKET", entry_filled=True,
                paper_fill_model="aggressive_quote_estimate")
    plan["entry_options"] = {"AGGRESSIVE_MARKET": {"eligible": True, "trigger_reason": ""}}
    assert book.maybe_open(plan, 100.0) is None
    assert book.open == []


def test_escort_accepts_audited_aggressive_paper_fill():
    book = EscortBook()
    plan = base_plan()
    plan.update(entry_style="AGGRESSIVE_MARKET", entry_filled=True,
                paper_fill_model="aggressive_quote_estimate")
    plan["entry_options"] = {"AGGRESSIVE_MARKET": {
        "eligible": True, "trigger_reason": "independent confirmed impulse"}}
    trade = book.maybe_open(plan, 100.0)
    assert trade is not None
    assert trade.entry_style == "AGGRESSIVE_MARKET"
