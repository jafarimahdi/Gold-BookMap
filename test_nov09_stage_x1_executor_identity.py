"""
test_nov09_stage_x1_executor_identity.py
========================================
Section `#14` Stage X1 -- Step-4 MT5 Executor fill-identity hardening.

The defect (Pass 1/2 of the Section `#14` manager check, `step4_mt5_execution.py`):

    def _wait_for_bot_position(self, order_id=None, timeout=None):
        ...
        for position in positions:
            ticket = getattr(position, "ticket", None)
            if order_id is None or ticket == order_id or positions:   # <-- always true
                return position

Three independent failures in one line:

  1. `or positions:` is always truthy, so the FIRST bot position is returned no
     matter what `order_id` says -- the ticket filter never filters.
  2. It compares an ORDER ticket with a POSITION ticket. In MT5 `order.ticket`,
     `deal.ticket` and `position.ticket` are three different integers, so the
     comparison would usually miss even without (1).
  3. With no identity at all it still returned a position.

`_place_order` then stores that object's ticket through
`trade_history.record_entry(...)` / `record_tca(...)`, so a stale or unrelated
position could be remembered as THIS entry's trade and every downstream
scorecard (`[8c]` / `[8d]` / `[8j]`) inherited a false attribution.

Stage X1 resolves the position by identity, in order:

  1. deal -> `position_id` (authoritative; `_position_id_from_deal`),
  2. position ticket == order ticket,
  3. exactly ONE position opened at/after `not_before_epoch` whose volume matches
     `expect_volume` -- two or more candidates means REFUSE TO GUESS (`None`).

Fail-closed everywhere: an unidentifiable fill returns `None` (the caller then
reports `ERROR` / `DEFERRED`), never a bystander position.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

import step4_mt5_execution as s4
from step4_mt5_execution import MT5Executor

MAGIC = 234000
CYCLE_EPOCH = 1_000.0


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
def _pos(ticket, volume=0.01, t=CYCLE_EPOCH + 5.0, magic=MAGIC, ptype=0):
    return SimpleNamespace(ticket=ticket, volume=volume, time=t,
                           magic=magic, type=ptype)


def _deal(ticket=9001, position_id=222):
    return SimpleNamespace(ticket=ticket, position_id=position_id)


def _fake_mt5(positions=None, deals=None, *, deals_raise=False,
              no_deals_api=False):
    m = SimpleNamespace()
    m.POSITION_TYPE_BUY = 0
    m.POSITION_TYPE_SELL = 1
    m.ORDER_TYPE_BUY = 0
    m.ORDER_TYPE_SELL = 1
    m.TRADE_RETCODE_DONE = 10009
    m.positions_get = lambda symbol=None: positions
    if not no_deals_api:
        def _history_deals_get(**kwargs):
            if deals_raise:
                raise RuntimeError("history unavailable")
            return deals
        m.history_deals_get = _history_deals_get
    return m


@pytest.fixture
def engine(monkeypatch):
    eng = MT5Executor.__new__(MT5Executor)
    eng.symbol = "XAUUSD"
    eng.magic = MAGIC
    eng._server_offset_s = None
    monkeypatch.setattr(s4.config, "EXECUTION_VERIFY_SECONDS", 0, raising=False)
    return eng


# --------------------------------------------------------------------------- #
# (1) the defect itself: a bystander position must never be reported
# --------------------------------------------------------------------------- #
def test_x1_bystander_position_is_not_returned(engine, monkeypatch):
    """The old code returned the stale position 111. X1 must return None."""
    stale = _pos(111)                     # somebody else's / previous position
    monkeypatch.setattr(s4, "mt5", _fake_mt5([stale], [_deal(position_id=222)]))
    assert engine._wait_for_bot_position(order_id=555, deal_id=9001,
                                         expect_volume=0.01,
                                         not_before_epoch=CYCLE_EPOCH) is None


def test_x1_deal_identity_wins_over_a_older_bystander(engine, monkeypatch):
    stale = _pos(111)
    mine = _pos(222, volume=0.02)
    monkeypatch.setattr(s4, "mt5", _fake_mt5([stale, mine], [_deal(position_id=222)]))
    got = engine._wait_for_bot_position(order_id=555, deal_id=9001,
                                        expect_volume=0.02,
                                        not_before_epoch=CYCLE_EPOCH)
    assert got is mine and got.ticket == 222


def test_x1_order_ticket_fallback_when_deal_api_missing(engine, monkeypatch):
    """Netting brokers can return the order ticket; that path must still work."""
    target = _pos(555)
    monkeypatch.setattr(s4, "mt5",
                        _fake_mt5([_pos(111), target], None, no_deals_api=True))
    got = engine._wait_for_bot_position(order_id=555, deal_id=9001,
                                        expect_volume=0.01,
                                        not_before_epoch=CYCLE_EPOCH)
    assert got is target


def test_x1_deal_api_error_falls_back_without_crashing(engine, monkeypatch):
    target = _pos(555)
    monkeypatch.setattr(s4, "mt5",
                        _fake_mt5([target], None, deals_raise=True))
    got = engine._wait_for_bot_position(order_id=555, deal_id=9001,
                                        expect_volume=0.01,
                                        not_before_epoch=CYCLE_EPOCH)
    assert got is target


# --------------------------------------------------------------------------- #
# (2) the safe "single new position" path still works
# --------------------------------------------------------------------------- #
def test_x1_single_new_matching_position_is_accepted(engine, monkeypatch):
    mine = _pos(777)
    monkeypatch.setattr(s4, "mt5", _fake_mt5([mine], None, no_deals_api=True))
    got = engine._wait_for_bot_position(order_id=None, deal_id=None,
                                        expect_volume=0.01,
                                        not_before_epoch=CYCLE_EPOCH)
    assert got is mine


def test_x1_volume_mismatch_is_rejected(engine, monkeypatch):
    monkeypatch.setattr(s4, "mt5",
                        _fake_mt5([_pos(777, volume=0.01)], None, no_deals_api=True))
    assert engine._wait_for_bot_position(expect_volume=0.02,
                                        not_before_epoch=CYCLE_EPOCH) is None


def test_x1_position_opened_before_the_cycle_is_rejected(engine, monkeypatch):
    old = _pos(777, t=CYCLE_EPOCH - 600.0)
    monkeypatch.setattr(s4, "mt5", _fake_mt5([old], None, no_deals_api=True))
    assert engine._wait_for_bot_position(expect_volume=0.01,
                                        not_before_epoch=CYCLE_EPOCH) is None


def test_x1_two_ambiguous_candidates_refuse_to_guess(engine, monkeypatch):
    a = _pos(777)
    b = _pos(888)
    monkeypatch.setattr(s4, "mt5", _fake_mt5([a, b], None, no_deals_api=True))
    assert engine._wait_for_bot_position(expect_volume=0.01,
                                        not_before_epoch=CYCLE_EPOCH) is None


def test_x1_ambiguity_is_logged_as_a_warning(engine, monkeypatch, caplog):
    monkeypatch.setattr(s4, "mt5",
                        _fake_mt5([_pos(777), _pos(888)], None, no_deals_api=True))
    with caplog.at_level("WARNING", logger=s4.logger.name):
        engine._wait_for_bot_position(expect_volume=0.01,
                                      not_before_epoch=CYCLE_EPOCH)
    assert any("refusing to guess" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- #
# (3) foreign magic numbers are never touched
# --------------------------------------------------------------------------- #
def test_x1_foreign_magic_position_is_ignored(engine, monkeypatch):
    manual = _pos(999, magic=123456)
    monkeypatch.setattr(s4, "mt5", _fake_mt5([manual], None, no_deals_api=True))
    assert engine._wait_for_bot_position(expect_volume=0.01,
                                        not_before_epoch=CYCLE_EPOCH) is None


# --------------------------------------------------------------------------- #
# (4) `_position_id_from_deal` corners
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", [None, 0, -5, "", "abc", "9001x"])
def test_x1_position_id_resolver_rejects_bad_input(engine, monkeypatch, bad):
    monkeypatch.setattr(s4, "mt5", _fake_mt5([], [_deal()]))
    assert engine._position_id_from_deal(bad) is None


def test_x1_position_id_resolver_handles_missing_position_id(engine, monkeypatch):
    monkeypatch.setattr(s4, "mt5",
                        _fake_mt5([], [SimpleNamespace(ticket=9001, position_id=0)]))
    assert engine._position_id_from_deal(9001) is None


def test_x1_position_id_resolver_reads_the_deal(engine, monkeypatch):
    monkeypatch.setattr(s4, "mt5", _fake_mt5([], [_deal(ticket=9001, position_id=222)]))
    assert engine._position_id_from_deal(9001) == 222


def test_x1_position_id_resolver_without_sdk_is_safe(engine, monkeypatch):
    monkeypatch.setattr(s4, "mt5", None)
    assert engine._position_id_from_deal(9001) is None


# --------------------------------------------------------------------------- #
# (5) robustness / wiring guards
# --------------------------------------------------------------------------- #
def test_x1_positions_query_failure_returns_none_without_raising(engine, monkeypatch):
    broken = _fake_mt5(None, None, no_deals_api=True)   # positions_get -> None
    monkeypatch.setattr(s4, "mt5", broken)
    assert engine._wait_for_bot_position(expect_volume=0.01,
                                        not_before_epoch=CYCLE_EPOCH) is None


def test_x1_the_always_true_expression_is_gone():
    src = inspect.getsource(s4.MT5Executor._wait_for_bot_position)
    # ignore the docstring (it quotes the old code on purpose)
    body = src.split('"""')[2]
    assert "or positions:" not in body
    assert "or ticket == order_id" not in body
    assert "deal_id" in src and "expect_volume" in src and "not_before_epoch" in src


def test_x1_both_call_sites_pass_the_identity():
    src = inspect.getsource(s4.MT5Executor)
    market = src.split("Stage X1: verify THIS entry")[1]
    assert "deal_id=deal_id" in market and "expect_volume=lot_size" in market
    limit = src.split("Stage X1: identify the fill by deal/order identity")[1]
    assert 'deal_id=getattr(result, "deal", None)' in limit
    assert "expect_volume=lot_size" in limit


def test_x1_verified_ticket_is_what_gets_recorded_and_returned():
    src = inspect.getsource(s4.MT5Executor._place_order)
    # both the memo write and the ExecutionResult use the VERIFIED position
    assert "ticket = getattr(verified_position, \"ticket\", None)" in src
    assert "position_id=getattr(verified_position, \"ticket\", None)" in src
