"""
test_nov10_stage_x4a_audit_error_bucket.py
==========================================
Section `#14` Stage X4a -- the Evening Audit must be able to SEE an `ERROR` row.

Why this stage exists (found by the `#14` Evening Audit, code half, `2026-11-10`):

`audit_day.py::test_5_guards` sorted the day's decision ledger into exactly three
buckets:

    exec_rows     = EXECUTED | PENDING     ("orders that reached MT5")
    deferred_rows = DEFERRED               ("a guard refused")
    skipped_rows  = SKIPPED  | ""          ("never reached the executor")

`ERROR` was in **none** of them. So a day made only of `ERROR` rows printed

    0 orders were sent to MT5 -> the robot only thought, it never acted

while MT5 could be holding a live position. That was harmless while `ERROR`
always meant "the broker said no": nothing was opened. Stage `X2`/`X3` changed
that **on purpose** --

  * `X2`: `10010 DONE_PARTIAL` is a LIVE fill (smaller than requested);
  * `X3`: a close that `positions_get` cannot confirm is reported as
    `OPERATOR ACTION — CLOSE NOT CONFIRMED … money is LIVE` and the executor
    returns `ERROR`, because it refuses to claim a close it cannot prove.

So after X2/X3 an `ERROR` row can mean "money is live and the book may be
wrong" -- the exact statement the whole `#14` hardening exists to prevent, and
the audit layer was blind to it.

Stage X4a therefore:
  1. gives `ERROR` its own bucket and counts it,
  2. prints up to five distinct error reasons with their counts,
  3. detects the money-may-be-live wording and escalates the whole day to
     `FAIL` with an explicit "check MT5 by hand" instruction,
  4. keeps `ERROR` out of the order count (an error is not a fill) but says so
     out loud instead of implying the robot only sat and thought.
"""
from __future__ import annotations

import inspect
import re

import pytest

import audit_day as A


def _dec(status, reason="", **extra):
    d = {"exec_status": status, "reason": reason}
    d.update(extra)
    return d


def _texts(res):
    r = _r(res)
    return "\n".join(w for _g, _n, _h, ws in r.rows for w in ws)


def _r(res):
    """`test_5_guards` returns `(Report, summary)` -- take the Report."""
    return res[0] if isinstance(res, tuple) else res


def _grades(res):
    return [g for g, _n, _h, _w in _r(res).rows]


# --------------------------------------------------------------------------- #
# (1) an ERROR row that admits money may be live escalates the day to FAIL
# --------------------------------------------------------------------------- #
def test_x4a_live_money_error_is_a_fail():
    decisions = [
        _dec("ERROR", "OPERATOR ACTION — CLOSE NOT CONFIRMED (retcode 10009): ticket 111 "
                      "is STILL OPEN with 0.10 lots; money is LIVE"),
    ]
    r = A.test_5_guards("", decisions, None)
    assert "FAIL" in _grades(r)
    txt = _texts(r)
    assert "STILL LIVE" in txt
    assert "check" in txt.lower() or "by hand" in txt.lower()


@pytest.mark.parametrize("reason", [
    "OPERATOR ACTION — PARTIAL CLOSE (retcode 10010): 0.04 of 0.10 closed — 0.06 lots are STILL LIVE",
    "OPERATOR ACTION — close UNVERIFIED (retcode None): positions_get could not confirm ticket 111",
    "close failed: OPERATOR ACTION — CLOSE NOT CONFIRMED (retcode 10006): ticket 111 is STILL OPEN",
])
def test_x4a_every_x3_wording_is_recognised(reason):
    r = A.test_5_guards("", [_dec("ERROR", reason)], None)
    assert "FAIL" in _grades(r), reason


def test_x4a_partial_entry_error_alone_is_not_a_false_alarm():
    """A plain broker reject opens nothing and must not be graded as live money."""
    r = A.test_5_guards("", [_dec("ERROR", "OPERATOR ACTION — NO_MONEY (retcode 10019): "
                                          "no position was opened")], None)
    assert "FAIL" not in _grades(r)


# --------------------------------------------------------------------------- #
# (2) ERROR rows are counted and named, never silently dropped
# --------------------------------------------------------------------------- #
def test_x4a_error_rows_are_narrated():
    r = A.test_5_guards("", [_dec("ERROR", "order_send retcode=10006 (FATAL)")], None)
    txt = _texts(r)
    assert "ERRORED" in txt
    assert "10006" in txt


def test_x4a_zero_order_line_admits_the_error():
    r = A.test_5_guards("", [_dec("ERROR", "order_send retcode=10006 (FATAL)")], None)
    txt = _texts(r)
    assert "0 orders were sent to MT5" in txt
    assert "BUT 1 attempt(s) ERRORED" in txt


def test_x4a_errors_are_not_counted_as_orders():
    decisions = [
        _dec("EXECUTED", "ok"),
        _dec("ERROR", "order_send retcode=10006 (FATAL)"),
        _dec("ERROR", "order_send retcode=10007 (FATAL)"),
    ]
    r = A.test_5_guards("", decisions, None)
    txt = _texts(r)
    assert "1 decision(s) were actually sent to MT5" in txt
    assert "2 decision(s) ERRORED" in txt


def test_x4a_reasons_are_deduplicated_with_counts():
    decisions = [_dec("ERROR", "order_send retcode=10006 (FATAL)")] * 3
    r = A.test_5_guards("", decisions, None)
    txt = _texts(r)
    assert "error x3: order_send retcode=10006" in txt


# --------------------------------------------------------------------------- #
# (3) nothing changes when there is no ERROR row
# --------------------------------------------------------------------------- #
def test_x4a_clean_day_is_unchanged():
    r = A.test_5_guards("", [_dec("EXECUTED", "ok"), _dec("SKIPPED", "confidence 0.0 < 35")], None)
    txt = _texts(r)
    assert "ERRORED" not in txt
    assert "0 orders were sent" not in txt


# --------------------------------------------------------------------------- #
# (4) source guards
# --------------------------------------------------------------------------- #
def test_x4a_source_has_an_error_bucket():
    src = inspect.getsource(A.test_5_guards)
    assert "Stage X4a" in src
    assert 'error_rows = [d for d in decisions if _st(d) == "ERROR"]' in src
    assert "_live_now" in src
    # the live-money detector must cover the X3 phrasings
    for token in ("money is (?:still )?LIVE", "STILL LIVE", "UNVERIFIED", "NOT CONFIRMED"):
        assert token in src


def test_x4a_error_rows_never_enter_exec_or_skipped_buckets():
    src = inspect.getsource(A.test_5_guards)
    assert '"ERROR"' not in src.split("error_rows =", 1)[0].split("exec_rows =")[1]


def test_x4a_live_money_fail_precedes_the_other_grades():
    src = inspect.getsource(A.test_5_guards)
    i_fail = src.index("if _live_now:")
    i_snapshot = src.index('"empty or invalid market snapshot" in reasons')
    assert i_fail < i_snapshot
