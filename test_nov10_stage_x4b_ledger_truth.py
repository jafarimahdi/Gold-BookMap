"""
test_nov10_stage_x4b_ledger_truth.py
====================================
Section `#14` Stage X4b -- the ledger must record the volume MT5 ACTUALLY opened,
and the decision row must carry the identity `X1` already resolves.

Two findings from the `#14` Evening Audit (code half, `2026-11-10`):

**F2 -- the volume that was never filled.** `execute()` called

    trade_history.record_entry(..., lot_size)

and returned `ExecutionResult(volume=lot_size)`: the **requested** size. After a
`10010 DONE_PARTIAL` fill (which Stage `X2` correctly treats as live money) the
account holds LESS. `trade_history.record_close` then estimates

    pnl_usd = gain_pts * volume_lots * contract_size      (trade_history.py:186)

and that estimate feeds `day_pnl_pct()` (`:264-274`), which arms
`ENTRY_DAY_RATCHET` and the `-3%` / `Stage-4R -12.0 pt` breakers. So a partial
fill made the robot compute its own risk state from a position size it never
held -- the ledger could arm (or fail to arm) the ratchet and the daily loss cap
on a fiction.

**F3 -- the ledger could not be joined to MT5.** `log_decision` wrote `order_id`
but not `deal_id` / `position_id`, although `ExecutionResult` has carried both
since Stage `X1`. The Evening Audit could therefore only match a decision row to
a broker deal through `trade_outcomes.csv`.

Stage X4b fixes both, in one causal unit ("the ledger tells the truth about what
happened at the broker"):
  1. `MT5Executor._filled_volume(position, requested)` reads the opened volume
     back from the position, and falls back to the requested size only when the
     position cannot be read (never to `0.0`),
  2. `record_entry` / `record_tca` / `ExecutionResult.volume` all use it,
  3. a mismatched fill is logged at `WARNING` with both numbers,
  4. the decision ledger gains `position_id`, `deal_id`, `fill_volume`.
"""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import main as M
import step4_mt5_execution as s4
from step4_mt5_execution import MT5Executor

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)


def _pos(volume=0.10, ticket=111):
    return SimpleNamespace(ticket=ticket, volume=volume, type=0, magic=234000,
                           price_open=4100.0, time=1_760_000_000)


# --------------------------------------------------------------------------- #
# (1) the filled-volume helper
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("reported,requested,expected", [
    (0.10, 0.10, 0.10),      # full fill
    (0.04, 0.10, 0.04),      # DONE_PARTIAL -> the SMALLER number wins
    (0.01, 0.01, 0.01),      # broker minimum
    (0.0, 0.10, 0.10),       # unreadable position -> requested, never zero
    (None, 0.10, 0.10),      # missing attribute -> requested
])
def test_x4b_filled_volume_never_invents_a_size(reported, requested, expected):
    got = MT5Executor._filled_volume(_pos(volume=reported), requested)
    assert got == pytest.approx(expected)


def test_x4b_filled_volume_survives_a_broken_position_object():
    class Broken:
        @property
        def volume(self):
            raise RuntimeError("terminal gone")

    assert MT5Executor._filled_volume(Broken(), 0.07) == pytest.approx(0.07)


# --------------------------------------------------------------------------- #
# (2) the executor uses it everywhere it matters
# --------------------------------------------------------------------------- #
def test_x4b_record_entry_uses_the_filled_volume():
    src = inspect.getsource(s4.MT5Executor._place_order)
    assert "fill_volume = self._filled_volume(verified_position, lot_size)" in src
    assert "trade_history.record_entry(\n                ticket, action, price, sl, tp, fill_volume," in src
    assert "lot_size, ai_confidence=ai_confidence" not in src


def test_x4b_record_tca_uses_the_filled_volume():
    src = inspect.getsource(s4.MT5Executor._place_order)
    assert 'trade_history.record_tca("ENTRY", ticket, price, fill_price,\n                                     fill_volume' in src


def test_x4b_execution_result_reports_the_filled_volume():
    src = inspect.getsource(s4.MT5Executor._place_order)
    assert "symbol=self.symbol, volume=fill_volume" in src


def test_x4b_partial_fill_is_logged_with_both_numbers():
    src = inspect.getsource(s4.MT5Executor._place_order)
    assert "PARTIAL FILL" in src
    assert "asked %.2f lots, MT5 opened %.2f lots" in src


def test_x4b_fill_volume_is_defined_before_it_is_used():
    src = inspect.getsource(s4.MT5Executor._place_order)
    assert src.index("fill_volume = self._filled_volume") < src.index("trade_history.record_entry(")


# --------------------------------------------------------------------------- #
# (3) the decision ledger carries the identity (F3)
# --------------------------------------------------------------------------- #
def test_x4c_schema_has_the_identity_columns():
    for col in ("position_id", "deal_id", "fill_volume"):
        assert col in M._DECISION_LOG_FIELDS


def test_x4c_row_writes_the_identity_columns():
    src = inspect.getsource(M.log_decision)
    assert '"position_id": getattr(exec_result, "position_id", "")' in src
    assert '"deal_id": getattr(exec_result, "deal_id", "")' in src
    assert '"fill_volume": getattr(exec_result, "volume", "")' in src


def test_x4c_old_journals_are_migrated_not_broken(tmp_path):
    """An existing day file must gain the columns and keep every old row."""
    p = tmp_path / "decisions_20261110.csv"
    p.write_text('timestamp,symbol,exec_status,reason\n'
                 '2026-11-10 09:00:00,XAUUSD,SKIPPED,confidence 0.0 < 35\n',
                 encoding="utf-8")
    fields, write_header = M._decision_csv_schema(p)
    assert write_header is False           # the file was rewritten, header included
    assert "position_id" in fields and "deal_id" in fields and "fill_volume" in fields
    text = p.read_text(encoding="utf-8")
    assert "confidence 0.0 < 35" in text          # old row preserved
    assert "position_id" in text.splitlines()[0]  # header upgraded


def test_x4c_the_audit_can_now_join_a_row_to_mt5():
    """The columns must be readable by the Evening Audit's own loader."""
    import audit_day as A
    assert "position_id" in inspect.getsource(A)


# --------------------------------------------------------------------------- #
# (4) nothing else moved
# --------------------------------------------------------------------------- #
def test_x4b_executor_still_never_reads_margin_mode():
    assert "margin_mode" not in inspect.getsource(s4)


def test_x4b_other_error_paths_still_report_the_requested_size():
    """Only a VERIFIED fill may rewrite the volume; refusals keep `lot_size`."""
    src = inspect.getsource(s4.MT5Executor._place_order)
    before_fill = src.split("fill_volume = self._filled_volume")[0]
    assert before_fill.count("volume=lot_size") >= 3
