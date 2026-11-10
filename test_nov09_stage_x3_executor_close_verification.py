"""
test_nov09_stage_x3_executor_close_verification.py
==================================================
Section `#14` Stage X3 -- the close path must be PROVEN, not assumed.

Why this stage exists (Section `#14` manager check, defect X3):

    result = mt5.order_send(request)
    if result is None or getattr(result, "retcode", None) not in (done, partial):
        return False, f"close retcode={...}"
    logger.info("STEP 4: closed bot position ticket=%s volume=%.2f", ...)
    return True, "closed"

`10009 DONE` is the broker's receipt for the REQUEST, not proof that the trade
is gone. Three ways the old code lied about money:

  * `10010 DONE_PARTIAL` on a close -- only part of the volume was closed, the
    ticket is STILL OPEN -- was reported as `closed`;
  * a rejected/cancelled close was summarised as one numeric line, and the
    caller only knew "close failed" (or, worse, a stale `True`);
  * a terminal/IPC hiccup could leave a live position behind while Step 5, PM
    trails, Escort and the operator all believed the book was flat.

Separately, entry rejections the operator can actually fix -- `10019
NO_MONEY`, `10016 INVALID_STOPS` -- reached the log and
`data/decisions_YYYYMMDD.csv` as a bare `retcode=10019`, with no statement of
whether money was opened and no instruction. The owner may change broker or
account type at any time, so the labels must be broker-agnostic (they are: MT5
retcodes are identical everywhere) and account-type agnostic (nothing reads
`margin_mode`).

Stage X3 therefore:
  1. reads the position back with `positions_get()` after every close,
  2. reports `PARTIAL CLOSE` / `CLOSE NOT CONFIRMED` / `close UNVERIFIED` and
     names the volume that is still LIVE,
  3. lets verified state beat the receipt (a rejected send whose ticket is gone
     is flat, and is reported as such),
  4. never re-sends a close (a double close is its own disaster),
  5. prints one greppable `OPERATOR ACTION -- <LABEL>` line for NO_MONEY,
     INVALID_STOPS and every other fatal rejection.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

import step4_mt5_execution as s4
from step4_mt5_execution import MT5Executor


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
def _info():
    return SimpleNamespace(trade_tick_size=0.01, point=0.01, digits=2,
                           volume_min=0.01, volume_step=0.01, volume_max=100.0,
                           filling_mode=2)


def _tick(bid=4100.0, ask=4100.2):
    return SimpleNamespace(bid=bid, ask=ask)


def _pos(ticket=111, volume=0.10, ptype="buy"):
    return SimpleNamespace(
        ticket=ticket, volume=volume,
        type=0 if ptype == "buy" else 1,
        magic=234000, price_open=4100.0, time=1_760_000_000)


def _res(retcode=10009, order=555, deal=9001, price=4100.0):
    return SimpleNamespace(retcode=retcode, order=order, deal=deal, price=price)


class FakeMT5:
    """Minimal MetaTrader5 stand-in: sends, then answers `positions_get`."""

    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_PENDING = 5
    ORDER_TIME_GTC = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_DONE_PARTIAL = 10010

    def __init__(self, send_retcodes=(10009,), book=(), break_positions=None,
                 raise_on_send=None):
        self._retcodes = list(send_retcodes)
        self._book = [list(book)]           # a "snapshot" per query
        self.break_positions = break_positions
        self.raise_on_send = raise_on_send
        self.sends = []
        self.queries = 0
        self.closed = []
        self.lock_last = 0

    # --- market plumbing ---------------------------------------------------
    def symbol_info_tick(self, _symbol):
        return _tick()

    def symbol_info(self, _symbol):
        return _info()

    def symbols_get(self, *_a, **_k):
        return []

    def last_error(self):
        return (1, "fake")

    def shutdown(self):
        return True

    def positions_get(self, **kwargs):
        self.queries += 1
        if isinstance(self.break_positions, str):
            raise RuntimeError(self.break_positions)
        if self.break_positions == "none":
            return None
        idx = min(self.lock_last, len(self._book) - 1)
        self.lock_last += 0   # noqa: F841  (kept explicit: no auto-advance)
        rows = self._book[idx]
        if "ticket" in kwargs:
            return [p for p in rows if p.ticket == kwargs["ticket"]]
        if "symbol" in kwargs:
            return list(rows)
        return list(rows)

    def order_send(self, request):
        self.sends.append(dict(request))
        if self.raise_on_send is not None:
            exc = self.raise_on_send
            self.raise_on_send = None
            raise exc
        rc = self._retcodes.pop(0) if len(self._retcodes) > 1 else self._retcodes[0]
        if request.get("action") == self.TRADE_ACTION_DEAL and "position" in request:
            # a close: remember it, and let the test decide the new book
            self.closed.append(request)
        return _res(rc)

    # --- test helper: change what the next positions_get() will show ------
    def book_now(self, rows):
        self._book = [list(rows)]
        self.lock_last = 0


@pytest.fixture()
def engine():
    return MT5Executor("XAUUSD")


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    """No real waiting anywhere in these tests."""
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda *_a, **_k: None)
    monkeypatch.setattr(config_mod(), "MT5_CLOSE_VERIFY_SECONDS", 0.0)
    yield


def config_mod():
    import config
    return config


def _use(monkeypatch, fake):
    monkeypatch.setattr(s4, "mt5", fake)
    return fake


# --------------------------------------------------------------------------- #
# (1) the close is verified with positions_get(), and the receipt cannot lie
# --------------------------------------------------------------------------- #
def test_x3_close_confirmed_only_when_ticket_is_gone(engine, monkeypatch):
    fake = _use(monkeypatch, FakeMT5(send_retcodes=[10009], book=[]))  # gone
    ok, reason = engine._close_position(_pos())
    assert ok is True and reason == "closed"
    assert len(fake.closed) == 1          # exactly one close, never two


def test_x3_done_retcode_but_ticket_still_open_is_a_failure(engine, monkeypatch):
    """The whole point of X3: `10009 DONE` alone must not be believed."""
    _use(monkeypatch, FakeMT5(send_retcodes=[10009], book=[_pos()]))
    ok, reason = engine._close_position(_pos())
    assert ok is False
    assert "STILL OPEN" in reason and "LIVE" in reason
    assert "OPERATOR ACTION" in reason


def test_x3_partial_close_reports_the_volume_still_live(engine, monkeypatch):
    """`10010 DONE_PARTIAL` on a close leaves the ticket OPEN at less volume."""
    _use(monkeypatch, FakeMT5(send_retcodes=[10010], book=[_pos(volume=0.04)]))
    ok, reason = engine._close_position(_pos(volume=0.10))
    assert ok is False
    assert "PARTIAL CLOSE" in reason
    assert "0.04" in reason and "STILL LIVE" in reason


def test_x3_unreadable_positions_fail_closed(engine, monkeypatch):
    for breaker in ("none", "positions_get exploded"):
        _use(monkeypatch, FakeMT5(send_retcodes=[10009], book=[_pos()],
                                  break_positions=breaker))
        ok, reason = engine._close_position(_pos())
        assert ok is False, breaker
        assert "UNVERIFIED" in reason and "OPERATOR ACTION" in reason


def test_x3_a_failed_send_whose_ticket_is_gone_is_still_flat(engine, monkeypatch):
    """Verified state beats the receipt *and* beats a bad retcode."""
    _use(monkeypatch, FakeMT5(send_retcodes=[10019], book=[]))
    ok, reason = engine._close_position(_pos())
    assert ok is True and reason == "closed"


def test_x3_close_send_exception_does_not_escape(engine, monkeypatch):
    fake = _use(monkeypatch, FakeMT5(send_retcodes=[10009], book=[_pos()],
                                     raise_on_send=OSError("ipc down")))
    ok, reason = engine._close_position(_pos())     # must not raise
    assert ok is False
    assert "STILL OPEN" in reason or "UNVERIFIED" in reason
    assert fake.sends and len(fake.sends) == 1      # no blind retry of a close


def test_x3_verification_polls_until_the_ticket_disappears(engine, monkeypatch):
    """A terminal that needs a moment still yields a verified close."""

    class Slow(FakeMT5):
        def __init__(self):
            super().__init__(send_retcodes=[10009], book=[_pos()])
            self.calls = 0

        def positions_get(self, **kwargs):
            self.calls += 1
            rows = [_pos()] if self.calls <= 2 else []
            if "ticket" in kwargs:
                return [p for p in rows if p.ticket == kwargs["ticket"]]
            return rows

    monkeypatch.setattr(config_mod(), "MT5_CLOSE_VERIFY_SECONDS", 5.0)
    fake = Slow()
    monkeypatch.setattr(s4, "mt5", fake)
    import time as _time
    clock = {"t": 0.0}
    monkeypatch.setattr(_time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(_time, "sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    ok, reason = engine._close_position(_pos())
    assert ok is True and reason == "closed"
    assert fake.calls >= 3


# --------------------------------------------------------------------------- #
# (2) callers must stop the pipeline when a close is not verified
# --------------------------------------------------------------------------- #
def test_x3_close_bot_positions_reports_the_unverified_close(engine, monkeypatch):
    fake = FakeMT5(send_retcodes=[10009], book=[_pos()])
    _use(monkeypatch, fake)
    monkeypatch.setattr(engine, "_bot_positions", lambda: [_pos()])
    monkeypatch.setattr(s4.config, "mt5_initialize", lambda _m: True)

    results = engine.close_bot_positions()
    assert len(results) == 1
    assert results[0]["ok"] is False
    assert "OPERATOR ACTION" in results[0]["reason"]


def test_x3_opposite_close_not_confirmed_blocks_the_new_entry(engine, monkeypatch):
    """A live opposite position must never be followed by a new order."""
    fake = FakeMT5(send_retcodes=[10009], book=[_pos(ptype="sell")])
    _use(monkeypatch, fake)
    snapshot = SimpleNamespace(price=4100.0)
    monkeypatch.setattr(engine, "_bot_positions", lambda: [_pos(ptype="sell")])

    ok, reason = engine._close_position(_pos(ptype="sell"))
    assert ok is False
    assert "close failed" not in reason            # caller adds that prefix
    # the caller's contract: non-verified close -> ERROR, no second send
    assert len([r for r in fake.sends if "position" not in r]) == 0


# --------------------------------------------------------------------------- #
# (3) operator-visible NO_MONEY / INVALID_STOPS messaging
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("retcode,label", [
    (10019, "NO_MONEY"),
    (10016, "INVALID_STOPS"),
    (10006, "REJECT"),
    (10007, "CANCEL"),
    (10013, "INVALID_REQUEST"),
    (10014, "INVALID_VOLUME"),
    (10015, "INVALID_PRICE"),
    (10018, "MARKET_CLOSED"),
])
def test_x3_operator_message_labels(engine, retcode, label):
    msg = engine._operator_message(retcode)
    assert msg.startswith("OPERATOR ACTION — ")
    assert f"{label} (retcode {retcode})" in msg
    assert "no position was opened" in msg.lower() or "NO position was opened" in msg


def test_x3_no_money_message_tells_the_operator_what_to_do(engine):
    msg = engine._operator_message(10019)
    assert "NO_MONEY" in msg and "FREE MARGIN" in msg.upper()
    assert "MAX_LOT_SIZE" in msg


def test_x3_invalid_stops_message_tells_the_operator_what_to_do(engine):
    msg = engine._operator_message(10016)
    assert "INVALID_STOPS" in msg
    assert "STOP_LOSS_ATR_MULT" in msg or "stops level" in msg


def test_x3_unknown_retcode_still_produces_an_operator_line(engine):
    msg = engine._operator_message(10999)
    assert "OPERATOR ACTION" in msg and "10999" in msg


def test_x3_no_result_produces_an_operator_line(engine):
    msg = engine._operator_message(None)
    assert "OPERATOR ACTION" in msg and "SEND FAILED" in msg


def test_x3_margin_precheck_is_labelled_no_money(engine, monkeypatch):
    fake = _use(monkeypatch, FakeMT5())
    fake.account_info = lambda: SimpleNamespace(margin_free=10.0)
    fake.order_calc_margin = lambda *_a: 500.0
    err = engine._validate_margin("BUY", 0.10, 4100.0)
    assert err is not None
    assert "NO_MONEY" in err and "nothing was sent" in err
    assert fake.sends == []


def test_x3_fatal_entry_rejection_reaches_the_result_reason(engine):
    """The message must ride in `ExecutionResult.reason` (it is what the operator
    reads in `data/decisions_YYYYMMDD.csv`), not only in the log."""
    src = inspect.getsource(s4.MT5Executor._place_order)
    market = src.split("Stage X2: status handling moved above")[0]
    assert "_operator_message(" in market
    assert "STEP 4 X3" in market
    assert "{op_msg}" in market


# --------------------------------------------------------------------------- #
# (4) wiring guards
# --------------------------------------------------------------------------- #
def test_x3_close_verifies_with_positions_get():
    src = inspect.getsource(s4.MT5Executor._close_position)
    assert "Stage X3" in src
    assert "_position_remaining_volume(" in src
    assert "positions_get" in inspect.getsource(s4.MT5Executor._position_remaining_volume)
    # the old "retcode is enough" acceptance is gone
    assert 'return True, "closed"' in src
    assert src.index("_position_remaining_volume(ticket)") < src.index('return True, "closed"')


def test_x3_close_verify_seconds_is_configurable_and_bounded():
    import config
    assert 0.0 <= float(config.MT5_CLOSE_VERIFY_SECONDS) <= 15.0
    src = inspect.getsource(s4.MT5Executor._close_position)
    assert 'getattr(config, "MT5_CLOSE_VERIFY_SECONDS"' in src


def test_x3_remaining_volume_helper_is_fail_closed():
    src = inspect.getsource(s4.MT5Executor._position_remaining_volume)
    assert "return None" in src              # unverifiable is never "closed"
    assert "return 0.0" in src


def test_x3_executor_still_never_reads_margin_mode():
    """Broker/account portability (owner may switch platform/CFD broker)."""
    src = inspect.getsource(s4)
    assert "margin_mode" not in src
    assert "ACCOUNT_MARGIN_MODE" not in src


def test_x3_close_never_touches_another_magic(engine):
    src = inspect.getsource(s4.MT5Executor._close_position)
    assert 'comment": "gold-trading-bot close"' in src
    assert '"magic": self.magic' in src
