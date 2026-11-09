"""
test_nov09_stage_x2_executor_retcode_taxonomy.py
================================================
Section `#14` Stage X2 -- broker-agnostic retcode taxonomy for `order_send()`.

Why this stage exists (Section `#14` manager check, defect X2):

    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        return ExecutionResult(status="ERROR", ...)

`10009 DONE` was the ONLY accepted outcome. So:

  * `10010 DONE_PARTIAL` -- the broker filled LESS than requested, i.e. **money
    is already live** -- was reported as `ERROR`, with no `record_entry`, no trade
    memory and no position bookkeeping. The robot believed nothing happened while
    it was holding a position.
  * `10004 REQUOTE` / `10020 PRICE_CHANGED` / `10021 PRICE_OFF` -- transient
    broker price races that usually succeed on a refreshed tick -- were terminal
    `ERROR`s. A fast gold tape (our `M3` scalper's normal weather) loses trades to
    this, and nothing retried.

Design constraint the owner raised: he may move the app between different MT5
platforms / CFD brokers, and the account may be demo now, live later, hedging or
netting. MT5 **retcodes are identical across platforms and brokers** and mean the
same thing for our purposes, so this taxonomy is deliberately account-type
independent and needs no per-broker `.env`. Nothing here reads `margin_mode`.

Stage X2 therefore:
  1. classifies every retcode (`_retcode_taxonomy`),
  2. sends market orders through `_send_market_order()` which retries the
     retryable family on a **fresh tick**, re-deriving SL/TP offsets so a retried
     order never carries the stale bracket the broker just rejected,
  3. treats `PARTIAL` as a LIVE fill (verify, then record),
  4. reuses one `request_id` across attempts (dedupe key where honoured),
  5. contains send exceptions instead of letting them escape into the loop.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import step4_mt5_execution as s4
from step4_mt5_execution import MT5Executor

NOW = datetime(2026, 11, 9, 20, 0, tzinfo=timezone.utc)
SELL_BUY = {"BAR": 1}


def _info():
    return SimpleNamespace(trade_tick_size=0.01, point=0.01, digits=2,
                           volume_min=0.01, volume_step=0.01, volume_max=100.0)


def _res(retcode, order=555, deal=9001, price=4100.0):
    return SimpleNamespace(retcode=retcode, order=order, deal=deal, price=price)


def _tick(bid=4100.0, ask=4100.2):
    return SimpleNamespace(bid=bid, ask=ask)


def _fake_mt5(sends, ticks=None, *, raise_on_send=None):
    """`sends` = list of results (or exceptions) returned in order."""
    m = SimpleNamespace()
    m.POSITION_TYPE_BUY = 0
    m.POSITION_TYPE_SELL = 1
    m.ORDER_TYPE_BUY = 0
    m.ORDER_TYPE_SELL = 1
    m.TRADE_RETCODE_DONE = 10009
    m.TRADE_RETCODE_DONE_PARTIAL = 10010
    seq = list(sends)
    ticks = list(ticks or [_tick()])

    def _order_send(request):
        if raise_on_send:
            raise raise_on_send
        return seq.pop(0) if seq else _res(10009)

    def _tick_fn(symbol=None):
        return ticks.pop(0) if len(ticks) > 1 else ticks[0]

    m.order_send = _order_send
    m.symbol_info_tick = _tick_fn
    m.last_error = lambda: (0, "ok")
    return m


@pytest.fixture
def engine(monkeypatch):
    eng = MT5Executor.__new__(MT5Executor)
    eng.symbol = "XAUUSD"
    eng.magic = 234000
    monkeypatch.setattr(s4.config, "MT5_RETRY_MAX", 2, raising=False)
    monkeypatch.setattr(s4.config, "MT5_RETRY_BACKOFF_MS", 0, raising=False)
    monkeypatch.setattr(s4.config, "MT5_DEVIATION", 20, raising=False)
    return eng


def _req(action="BUY", price=4100.2, sl=4090.0, tp=4120.0, volume=0.01):
    return {"action": 1, "symbol": "XAUUSD", "volume": volume,
            "type": 0, "price": price, "sl": sl, "tp": tp,
            "deviation": 20, "magic": 234000}


def _send(eng, sends, ticks=None, action="BUY", price=4100.2):
    result, bucket, reason, attempts = eng._send_market_order(
        _req(action=action, price=price), action=action, lot_size=0.01,
        info=_info(), now=NOW, sl=4090.0, tp=4120.0, price=price)
    return result, bucket, reason, attempts


# --------------------------------------------------------------------------- #
# (1) the taxonomy itself
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("rc,expected", [
    (10009, "OK"),
    (10010, "PARTIAL"),
    (10008, "PLACED"),
    (10004, "RETRYABLE"),
    (10020, "RETRYABLE"),
    (10021, "RETRYABLE"),
    (10006, "FATAL"),
    (10007, "FATAL"),
    (10016, "FATAL"),
    (10019, "FATAL"),
    (10013, "FATAL"),
    (12345, "UNKNOWN"),
    (None, "NONE"),
])
def test_x2_taxonomy_buckets(engine, rc, expected):
    assert engine._retcode_taxonomy(rc) == expected


def test_x2_retryable_and_fatal_sets_do_not_overlap(engine):
    assert engine._RETCODE_RETRYABLE.isdisjoint(engine._RETCODE_FATAL)
    assert engine._RETCODE_OK not in engine._RETCODE_RETRYABLE


# --------------------------------------------------------------------------- #
# (2) partial fill = LIVE money, never an error
# --------------------------------------------------------------------------- #
def test_x2_partial_fill_is_reported_live_not_error(engine, monkeypatch):
    monkeypatch.setattr(s4, "mt5", _fake_mt5([_res(10010)]))
    result, bucket, _reason, attempts = _send(engine, [_res(10010)])
    assert bucket == "PARTIAL"
    assert getattr(result, "retcode") == 10010
    assert attempts == 1


def test_x2_partial_does_not_trigger_a_second_send(engine, monkeypatch):
    calls = []
    m = _fake_mt5([_res(10010)])
    orig = m.order_send
    m.order_send = lambda r: (calls.append(r), orig(r))[1]
    monkeypatch.setattr(s4, "mt5", m)
    _send(engine, [_res(10010)])
    assert len(calls) == 1, "a live partial fill must never be re-sent"


# --------------------------------------------------------------------------- #
# (3) transient codes are retried on a fresh tick
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("transient", [10004, 10020, 10021])
def test_x2_transient_then_done_is_a_success(engine, monkeypatch, transient):
    monkeypatch.setattr(s4, "mt5", _fake_mt5([_res(transient), _res(10009)]))
    result, bucket, _reason, attempts = _send(engine, [_res(transient), _res(10009)])
    assert bucket == "OK" and attempts == 2
    assert getattr(result, "retcode") == 10009


def test_x2_retry_uses_a_fresh_tick_price(engine, monkeypatch):
    seen = []
    # `symbol_info_tick` is only consulted on the retry, so the FIRST entry of
    # `ticks` is the refreshed quote the resend must use.
    m = _fake_mt5([_res(10004), _res(10009)], ticks=[_tick(4101.0, 4101.2)])
    orig = m.order_send
    m.order_send = lambda r: (seen.append(dict(r)), orig(r))[1]
    monkeypatch.setattr(s4, "mt5", m)
    _send(engine, [_res(10004), _res(10009)])
    assert len(seen) == 2
    assert seen[0]["price"] == pytest.approx(4100.2)      # original tick
    assert seen[1]["price"] == pytest.approx(4101.2)      # refreshed tick


def test_x2_retry_re_derives_the_bracket_offsets(engine, monkeypatch):
    seen = []
    m = _fake_mt5([_res(10020), _res(10009)], ticks=[_tick(4101.0, 4101.2)])
    orig = m.order_send
    m.order_send = lambda r: (seen.append(dict(r)), orig(r))[1]
    monkeypatch.setattr(s4, "mt5", m)
    _send(engine, [_res(10020), _res(10009)], price=4100.2)
    # original: SL 10.2 behind, TP 19.8 ahead; the retry must keep the DISTANCES
    assert seen[1]["sl"] == pytest.approx(4101.2 - (4100.2 - 4090.0), abs=0.02)
    assert seen[1]["tp"] == pytest.approx(4101.2 + (4120.0 - 4100.2), abs=0.02)


def test_x2_retry_budget_is_bounded(engine, monkeypatch):
    monkeypatch.setattr(s4.config, "MT5_RETRY_MAX", 1, raising=False)
    sends = [_res(10004), _res(10004), _res(10009)]
    monkeypatch.setattr(s4, "mt5", _fake_mt5(sends))
    result, bucket, _reason, attempts = _send(engine, sends)
    assert attempts == 2 and bucket == "RETRYABLE"
    assert getattr(result, "retcode") == 10004


def test_x2_retry_disabled_by_config(engine, monkeypatch):
    monkeypatch.setattr(s4.config, "MT5_RETRY_MAX", 0, raising=False)
    sends = [_res(10004), _res(10009)]
    monkeypatch.setattr(s4, "mt5", _fake_mt5(sends))
    _result, bucket, _reason, attempts = _send(engine, sends)
    assert attempts == 1 and bucket == "RETRYABLE"


# --------------------------------------------------------------------------- #
# (4) fatal / unknown codes terminate immediately
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("fatal", [10006, 10016, 10019])
def test_x2_fatal_codes_are_not_retried(engine, monkeypatch, fatal):
    calls = []
    m = _fake_mt5([_res(fatal), _res(10009)])
    orig = m.order_send
    m.order_send = lambda r: (calls.append(r), orig(r))[1]
    monkeypatch.setattr(s4, "mt5", m)
    _result, bucket, _reason, attempts = _send(engine, [_res(fatal)])
    assert bucket == "FATAL" and attempts == 1 and len(calls) == 1


# --------------------------------------------------------------------------- #
# (5) containment: a raising send never escapes
# --------------------------------------------------------------------------- #
def test_x2_send_exception_is_returned_not_raised(engine, monkeypatch):
    monkeypatch.setattr(s4, "mt5", _fake_mt5([], raise_on_send=RuntimeError("ipc down")))
    result, bucket, reason, attempts = _send(engine, [])
    assert result is None
    assert bucket == "NONE"          # no retcode at all -> classified as NONE
    assert "ipc down" in reason or attempts >= 1


def test_x2_missing_tick_on_retry_stops_cleanly(engine, monkeypatch):
    m = _fake_mt5([_res(10004)])
    m.symbol_info_tick = lambda symbol=None: None
    monkeypatch.setattr(s4, "mt5", m)
    result, bucket, reason, attempts = _send(engine, [_res(10004)])
    assert bucket == "RETRYABLE" and "fresh tick" in reason.lower()


# --------------------------------------------------------------------------- #
# (6) wiring guards (source level)
# --------------------------------------------------------------------------- #
def test_x2_dead_done_only_check_is_gone_for_the_market_path():
    """The DONE-only check may survive ONLY on the pending-limit path."""
    src = inspect.getsource(s4.MT5Executor._place_order)
    assert "_send_market_order(" in src
    marker = "Stage X2: status handling moved above"
    assert marker in src
    # after the market-path status handling not one DONE-only check may remain
    assert "retcode != mt5.TRADE_RETCODE_DONE" not in src.split(marker, 1)[1]


def test_x2_partial_is_logged_as_live():
    src = inspect.getsource(s4.MT5Executor._place_order)
    assert "PARTIAL" in src and "money is LIVE" in src


def test_x2_request_id_is_attached_before_the_send():
    src = inspect.getsource(s4.MT5Executor._place_order)
    assert 'request.setdefault("request_id"' in src
    assert src.index("request.setdefault(\"request_id\"") < src.index("_send_market_order(")


def test_x2_deviation_comes_from_config():
    src = inspect.getsource(s4.MT5Executor)
    assert "deviation\": 20" not in src
    assert 'int(getattr(config, "MT5_DEVIATION", 20))' in src


def test_x2_executor_never_reads_margin_mode():
    """Owner moves between platforms/brokers: the send path must stay
    account-type agnostic (no hedging/netting branches, no margin_mode read)."""
    src = inspect.getsource(s4)
    assert "margin_mode" not in src
    assert "ACCOUNT_MARGIN_MODE" not in src


def test_x2_netting_safe_single_lot_contract():
    """A netting account merges fills; X1's exact-volume rule must be documented
    as the reason a grown position is never adopted as this entry."""
    src = inspect.getsource(s4.MT5Executor._wait_for_bot_position)
    assert "EXACTLY the requested volume" in src
    assert "grown/merged position" in src
