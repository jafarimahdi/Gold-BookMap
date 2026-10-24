"""Regression tests for BookMap warm-up/catch-up offsets."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import bookmap_bridge_provider as bookmap
import config


def _tick_row(ts: datetime, price: float) -> str:
    return (f"{ts.isoformat()},Last,{price:.2f},1,0,BUY,GCZ6\n")


def _write_live_file(path: Path, rows: list[str]) -> None:
    path.write_text(
        "time,event,price,size,level,operation,instrument\n" + "".join(rows),
        encoding="utf-8",
    )


def test_first_catchup_does_not_replay_live_tail_twice(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    path = tmp_path / "ticks.csv"
    rows = [_tick_row(now - timedelta(seconds=n), 2000.0 + n)
            for n in (3, 2, 1)]
    _write_live_file(path, rows)
    monkeypatch.setattr(config, "BOOKMAP_WINDOW_SECONDS", 10_800, raising=False)

    tail = bookmap._BridgeTail(str(path))
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        tail._catch_up(fh)

    state = tail.instruments["GCZ6"]
    assert tail.line_count == len(rows)
    assert len(state.ticks) == len(rows)
    assert state.last_dt == now - timedelta(seconds=1)


def test_catchup_processes_rows_appended_during_history_replay(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    path = tmp_path / "ticks.csv"
    original_rows = [_tick_row(now - timedelta(seconds=n), 2000.0 + n)
                     for n in (3, 2, 1)]
    appended = _tick_row(now, 2010.0)
    _write_live_file(path, original_rows)
    monkeypatch.setattr(config, "BOOKMAP_WINDOW_SECONDS", 10_800, raising=False)

    tail = bookmap._BridgeTail(str(path))
    replay = tail._replay_recorded_history

    def replay_then_append():
        offset = replay()
        with path.open("a", encoding="utf-8") as fh:
            fh.write(appended)
        return offset

    tail._replay_recorded_history = replay_then_append
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        tail._catch_up(fh)

    state = tail.instruments["GCZ6"]
    assert tail.line_count == len(original_rows) + 1
    assert len(state.ticks) == len(original_rows) + 1
    assert state.ticks[-1]["price"] == 2010.0


def test_replay_scans_past_stale_rows_without_assuming_timestamp_order(
        tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    path = tmp_path / "ticks.csv"
    rows = [
        _tick_row(now - timedelta(seconds=400), 1900.0),
        _tick_row(now - timedelta(seconds=1), 2001.0),
        _tick_row(now - timedelta(seconds=500), 1901.0),
        _tick_row(now, 2002.0),
    ]
    _write_live_file(path, rows)
    monkeypatch.setattr(config, "BOOKMAP_WINDOW_SECONDS", 60, raising=False)
    messages = []
    monkeypatch.setattr(
        bookmap.logger, "info",
        lambda message, *args: messages.append(message % args if args else message),
    )

    tail = bookmap._BridgeTail(str(path))
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        tail._catch_up(fh)

    state = tail.instruments["GCZ6"]
    assert tail.line_count == 2
    assert [tick["price"] for tick in state.ticks] == [2001.0, 2002.0]
    assert any("timestamp_regressions=1" in message for message in messages)
