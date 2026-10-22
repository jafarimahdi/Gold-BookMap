"""Reverse-engineering coverage for quote/L2 quality, spread guards and evidence."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import bookmap_bridge_provider as bookmap
import config
import data_providers
import main
import step2_market_analysis as step2
import step4_mt5_execution as step4
import tools.backtest as backtest
from step4_mt5_execution import MT5Executor


def _live_market_data(**overrides):
    now = datetime.now(timezone.utc)
    data = {
        "symbol": "GCZ6",
        "data_symbol": "GCZ6",
        "trade_symbol": "XAUUSD",
        "price": 2000.25,
        "bid": 2000.0,
        "ask": 2000.5,
        "spread_pct": 0.02499687539057618,
        "has_data": True,
        "last_data_age_seconds": 1.0,
        "quote_age_seconds": 1.0,
        "quote_timestamp": now.isoformat(),
        "depth_age_seconds": 1.0,
        "depth_timestamp": now.isoformat(),
        "data_quality": {"quote": "valid", "level2": "live",
                          "level3": "unavailable",
                          "level3_events": "not_emitted",
                          "l3_aggressive_flow": "unavailable",
                          "l3_aggr_limit": "not_emitted"},
        "bid_depth": {2000.0: 10.0, 1999.5: 5.0},
        "ask_depth": {2000.5: 9.0, 2001.0: 4.0},
        "order_book": {"bids": [(2000.0, 10.0), (1999.5, 5.0)],
                       "asks": [(2000.5, 9.0), (2001.0, 4.0)]},
        "book_updates": [],
        "order_events": [],
        "tick_data": [],
        "feed_warnings": [],
    }
    data.update(overrides)
    return data


def _bookmap_state(now=None, *, bid=2000.0, ask=2000.5,
                   last=2000.25, tick_side="BUY", direct=True,
                   depth_age_seconds=0.0):
    now = now or datetime.now(timezone.utc)
    stamp = now - timedelta(seconds=depth_age_seconds)
    state = bookmap._InstrumentState()
    state.bids = {bid: 10.0}
    state.asks = {ask: 9.0}
    state.level_ts_b = {bid: stamp}
    state.level_ts_a = {ask: stamp}
    state.best_bid = bid
    state.best_ask = ask
    state.last_dt = now
    state.ticks = [{"price": last, "volume": 2.0, "side": tick_side,
                    "is_direct": direct, "operation": tick_side,
                    "ts": now}]
    return state


def _bookmap_provider(monkeypatch, state, data_symbol="MGC 12-26"):
    monkeypatch.setattr(config, "DATA_SOURCE", "bookmapbridge", raising=False)
    monkeypatch.setattr(config, "DATA_SYMBOL", data_symbol, raising=False)
    monkeypatch.setattr(config, "BOOKMAP_WAIT_SECONDS", 0, raising=False)
    monkeypatch.setattr(config, "BOOKMAP_LEVEL_MAX_AGE", 900.0, raising=False)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 300.0, raising=False)
    monkeypatch.setattr(config, "POWER_MAX_TICK_AGE_SECONDS", 60.0, raising=False)
    tail = SimpleNamespace(lock=threading.Lock(), instruments={
        "MGCZ6": state}, direct_side_hits=1, line_count=1)
    provider = object.__new__(bookmap.BookmapBridgeProvider)
    data_providers.BaseProvider.__init__(provider)
    provider.tail = tail
    provider.mbo_tail = None
    return provider


def test_bookmap_instrument_root_and_selection_fail_closed(monkeypatch):
    monkeypatch.setattr(config, "DATA_SYMBOL", "MGC 12-26", raising=False)
    assert bookmap._instrument_root("MGC 12-26") == "MGC"
    assert bookmap._instrument_root("MGCZ6.COMEX") == "MGC"
    assert bookmap._instrument_root("GC 12-26") == "GC"
    states = {"GCZ6": bookmap._InstrumentState(),
              "MGCZ6": bookmap._InstrumentState(),
              "MGCZ7": bookmap._InstrumentState()}
    states["GCZ6"].ticks.append({"price": 1})
    states["MGCZ6"].ticks.append({"price": 1})
    states["MGCZ7"].ticks.append({"price": 1})
    assert bookmap._choose_instrument(states) == "MGCZ6"
    monkeypatch.setattr(config, "DATA_SYMBOL", "MGC 12-27", raising=False)
    assert bookmap._choose_instrument(states) == "MGCZ7"
    monkeypatch.setattr(config, "DATA_SYMBOL", "GC 12-26", raising=False)
    assert bookmap._choose_instrument(states) == "GCZ6"
    monkeypatch.setattr(config, "DATA_SYMBOL", "MGC 12-25", raising=False)
    assert bookmap._choose_instrument(states) == ""
    monkeypatch.setattr(config, "DATA_SYMBOL", "SI 12-26", raising=False)
    assert bookmap._choose_instrument(states) == ""


def test_bookmap_level_age_config_loads_env_and_uses_finite_fallback(monkeypatch):
    original_env = os.environ.get("BOOKMAP_LEVEL_MAX_AGE")
    monkeypatch.setenv("BOOKMAP_LEVEL_MAX_AGE", "37.5")
    config.reload_env()
    assert config.BOOKMAP_LEVEL_MAX_AGE == pytest.approx(37.5)
    monkeypatch.setenv("BOOKMAP_LEVEL_MAX_AGE", "not-a-number")
    config.reload_env()
    assert config.BOOKMAP_LEVEL_MAX_AGE == pytest.approx(900.0)
    if original_env is None:
        monkeypatch.delenv("BOOKMAP_LEVEL_MAX_AGE", raising=False)
    else:
        monkeypatch.setenv("BOOKMAP_LEVEL_MAX_AGE", original_env)
    config.reload_env()


def test_bookmap_fresh_depth_filters_stale_future_missing_and_invalid_levels():
    now = datetime.now(timezone.utc)
    now_epoch = now.timestamp()
    levels = {2000.0: 10.0, 1999.5: 8.0, 1999.0: 7.0,
              1998.5: 0.0, 1998.0: 3.0, float("nan"): 3.0}
    stamps = {2000.0: now,
              1999.5: now - timedelta(seconds=901),
              1999.0: now + timedelta(seconds=1),
              1998.5: now,
              float("nan"): now}
    fresh, fresh_ts, counts = bookmap._fresh_depth_levels(
        levels, stamps, now_epoch, 900.0)
    assert fresh == {2000.0: 10.0}
    assert set(fresh_ts) == {2000.0}
    assert counts["stale"] == 1
    assert counts["future_timestamp"] == 1
    assert counts["missing_timestamp"] == 1
    assert counts["invalid"] == 2
    naive_fresh, _, _ = bookmap._fresh_depth_levels(
        {2000.0: 1.0}, {2000.0: now.replace(tzinfo=None)}, now_epoch, 1.0)
    assert naive_fresh == {2000.0: 1.0}

    receipt = bookmap._top_depth_receipt(
        levels, stamps, now_epoch, 900.0, "bid")
    assert [row["price"] for row in receipt] == [2000.0, 1999.5, 1999.0,
                                                  1998.5, 1998.0]
    assert [row["status"] for row in receipt] == [
        "fresh", "stale", "future_timestamp", "invalid_size",
        "missing_timestamp"]


def test_bookmap_acquire_emits_quote_spread_and_validated_l2(monkeypatch):
    state = _bookmap_state()
    provider = _bookmap_provider(monkeypatch, state)
    data = provider.acquire()
    assert data["data_quality"]["quote"] == "valid"
    assert data["data_quality"]["level2"] == "validated"
    assert data["data_quality"]["instrument"] == "matched"
    assert data["bid"] == 2000.0 and data["ask"] == 2000.5
    assert data["spread_pct"] > 0
    assert data["quote_age_seconds"] >= 0
    assert data["depth_age_seconds"] == pytest.approx(data["quote_age_seconds"])
    assert data["depth_timestamp"] == data["quote_timestamp"]
    assert data["l2_diagnostics"]["status"] == "validated"
    assert data["l2_diagnostics"]["top5_bid_levels"][0]["status"] == "fresh"
    assert data["l2_diagnostics"]["top5_ask_levels"][0]["status"] == "fresh"
    assert data["l2_diagnostics"]["tick_size"] == pytest.approx(0.1)
    assert data["l2_diagnostics"]["price_units"] == "USD per troy ounce"
    assert data["l2_diagnostics"]["timestamp_basis"] == "bridge_receive_time"
    assert data["l2_diagnostics"]["source_event_time_verified"] is False
    assert data["data_quality"]["l3_aggr_limit"] == "not_emitted"


def test_bookmap_l3_capability_requires_both_market_and_passive_tags(monkeypatch):
    state = _bookmap_state()
    state.mbo_events = [
        {"order_id": "1", "type": "FILL", "price": 2000.25,
         "side": "BUY", "is_market_order": True,
         "timestamp": datetime.now(timezone.utc).isoformat()},
    ]
    market_only = _bookmap_provider(monkeypatch, state).acquire()
    assert market_only["data_quality"]["l3_aggressive_flow"] == \
        "partial_mbo_market_order_events"
    assert market_only["data_quality"]["l3_aggr_limit"] == \
        "market_order_events_only"
    assert market_only["last_order_event_age_seconds"] >= 0
    assert market_only["last_order_event_timestamp"] is not None

    state.mbo_events.append(
        {"order_id": "2", "type": "NEW", "price": 2000.0,
         "side": "SELL", "is_market_order": False,
         "timestamp": datetime.now(timezone.utc).isoformat()})
    both = _bookmap_provider(monkeypatch, state).acquire()
    assert both["data_quality"]["l3_aggr_limit"] == "partial_mbo_tags"


def test_bookmap_acquire_rejects_stale_crossed_misaligned_and_wrong_instrument(monkeypatch):
    old = datetime.now(timezone.utc) - timedelta(seconds=901)
    stale = _bookmap_state(now=datetime.now(timezone.utc))
    stale.level_ts_b = {2000.0: old}
    stale.level_ts_a = {2000.5: old}
    data = _bookmap_provider(monkeypatch, stale).acquire()
    assert data["data_quality"]["level2"] == "stale_or_missing_levels"
    assert data["bid_depth"] == {} and data["ask_depth"] == {}
    assert data["l2_diagnostics"]["top5_bid_levels"][0]["status"] == "stale"

    crossed = _bookmap_state(bid=2001.0, ask=2000.5, last=2000.75)
    data = _bookmap_provider(monkeypatch, crossed).acquire()
    assert data["data_quality"]["level2"] == "crossed_book"
    assert data["bid_depth"] == {} and data["ask_depth"] == {}

    misaligned = _bookmap_state(last=2010.0)
    data = _bookmap_provider(monkeypatch, misaligned).acquire()
    assert data["data_quality"]["level2"] == "misaligned_quote_vs_last"
    assert data["data_quality"]["quote"] == "valid"
    assert data["spread_pct"] is not None

    mismatch = _bookmap_provider(monkeypatch, _bookmap_state(), data_symbol="GC 12-26")
    data = mismatch.acquire()
    assert data["data_quality"]["instrument"] == "mismatch"
    assert data["data_quality"]["level2"] == "instrument_mismatch"
    assert data["order_events"] == []

    wrong_expiry = _bookmap_provider(
        monkeypatch, _bookmap_state(), data_symbol="MGC 12-27").acquire()
    assert wrong_expiry["data_quality"]["instrument"] == "mismatch"
    assert wrong_expiry["data_quality"]["level2"] == "instrument_mismatch"
    assert wrong_expiry["bid"] == wrong_expiry["ask"] == 0.0

    stale_quote = _bookmap_provider(
        monkeypatch, _bookmap_state(depth_age_seconds=301.0)).acquire()
    assert stale_quote["data_quality"]["quote"] == "stale"
    assert stale_quote["data_quality"]["level2"] == "stale_or_missing_levels"
    assert stale_quote["spread_pct"] is not None
    assert stale_quote["quote_age_seconds"] > 300

    invalid_limit_provider = _bookmap_provider(monkeypatch, _bookmap_state())
    monkeypatch.setattr(config, "BOOKMAP_LEVEL_MAX_AGE", float("nan"),
                        raising=False)
    invalid_limit = invalid_limit_provider.acquire()
    assert invalid_limit["data_quality"]["level2"] == "unverified_quote_age_limit"
    assert invalid_limit["data_quality"]["quote"] == "invalid_age_limit"
    assert invalid_limit["l2_diagnostics"]["fresh_level_max_age_seconds"] is None
    assert invalid_limit["bid_depth"] == invalid_limit["ask_depth"] == {}
    assert "live quote not validated" in main._preflight_data_reason(invalid_limit)


def test_bookmap_mbo_tail_rejects_unparseable_source_timestamps():
    tail = object.__new__(bookmap._MboTail)
    tail.instruments = {}
    tail.line_count = 0
    tail.bad_lines = 0
    stamp = datetime.now(timezone.utc).isoformat()
    tail._handle_line(f"{stamp},BID_NEW,order-1,2000,1,MGCZ6\n")
    assert len(tail.instruments["MGCZ6"].mbo_events) == 1
    prior_bad_lines = tail.bad_lines
    tail._handle_line("bad-timestamp,BID_NEW,order-2,2000,1,MGCZ6\n")
    assert tail.bad_lines == prior_bad_lines + 1
    assert len(tail.instruments["MGCZ6"].mbo_events) == 1


def test_bookmap_unknown_last_side_is_not_fabricated_as_direct_buy():
    tail = object.__new__(bookmap._BridgeTail)
    tail.instruments = {}
    tail.line_count = 0
    tail.bad_lines = 0
    tail.direct_side_hits = 0
    stamp = datetime.now(timezone.utc).isoformat()
    tail._handle_line(f"{stamp},Last,2000,1,0,,MGCZ6\n")
    tick = tail.instruments["MGCZ6"].ticks[-1]
    assert tick["side"] == ""
    assert tick["is_direct"] is False
    tail._handle_line(f"{stamp},Last,2000,1,0,Buy,MGCZ6\n")
    direct = tail.instruments["MGCZ6"].ticks[-1]
    assert direct["side"] == "BUY"
    assert direct["is_direct"] is True
    prior_bad_lines = tail.bad_lines
    prior_ticks = len(tail.instruments["MGCZ6"].ticks)
    tail._handle_line("bad-timestamp,Last,2000,1,0,Buy,MGCZ6\n")
    assert tail.bad_lines == prior_bad_lines + 1
    assert len(tail.instruments["MGCZ6"].ticks) == prior_ticks


def test_databento_unknown_aggressor_side_is_skipped_and_mbo_timestamps_survive():
    provider = data_providers.DatabentoProvider()
    now_ns = int(time.time() * 1e9)
    rows = [
        {"action": "T", "side": "N", "price": 2000.0, "size": 1.0,
         "ts_event": now_ns},
        {"action": "T", "side": "B", "price": 2000.5, "size": 2.0,
         "ts_event": now_ns},
    ]
    provider._ingest_mbo(rows)
    assert len(provider.tick_data) == 1
    assert provider.tick_data[0]["side"] == "BUY"
    assert provider.tick_data[0]["is_direct"] is True
    assert len(provider.order_events) == 1
    assert provider.order_events[0]["is_market_order"] is True
    assert provider.order_events[0]["timestamp"]
    assert data_providers.databento_mbo_row_to_tick(rows[0]) is None

    missing_stamp = data_providers.DatabentoProvider()
    missing_stamp._ingest_mbo([{"action": "T", "side": "A",
                                "price": 2000.25, "size": 1.0}])
    assert missing_stamp.tick_data == []
    assert missing_stamp.order_events == []
    missing_stamp._ingest_trades([{"price": 2000.5, "size": 1.0,
                                   "side": "B"}])
    assert missing_stamp.tick_data == []
    missing_stamp._ingest_mbp10([{"bid_px_00": 2000.0, "bid_sz_00": 1.0,
                                  "ask_px_00": 2000.5, "ask_sz_00": 1.0}])
    assert missing_stamp.book_updates == []


def test_provider_timestamp_parsing_and_order_event_age_are_source_time_based():
    now = time.time()
    for raw in (int(now * 1e9), int(now * 1e6), int(now * 1e3), now):
        parsed = data_providers._ts_from_row({"timestamp": raw})
        assert parsed is not None
        assert abs(parsed.timestamp() - now) < 2.0
    iso = datetime.now(timezone.utc).isoformat()
    assert data_providers._ts_from_row({"timestamp": iso}) is not None
    assert data_providers._ts_from_row({"ts_event": 0, "timestamp": "bad"}) is None

    provider = data_providers.BaseProvider()
    old = datetime.now(timezone.utc) - timedelta(seconds=20)
    provider.handle_order_event({"type": "NEW", "timestamp": old.isoformat()})
    data = provider.build_market_data("GCZ6")
    assert data["has_data"] is True
    assert data["last_data_age_seconds"] >= 19.0
    assert data["last_order_event_age_seconds"] >= 19.0
    assert data["last_order_event_timestamp"] == old.isoformat()
    assert data["data_quality"]["level3"] == "available"
    assert data["data_quality"]["l3_aggr_limit"] == "not_emitted"


def test_base_provider_direct_flags_monotone_clock_reset_and_mbo_labels():
    provider = data_providers.BaseProvider()
    now = datetime.now(timezone.utc)
    provider.handle_trade(2000.0, 1.0, "BUY", ts=now,
                          is_direct="false")
    assert provider.tick_data[-1]["is_direct"] is False
    provider.handle_trade(2000.0, 1.0, "BUY", ts=None, is_direct=True)
    assert provider.tick_data[-1]["is_direct"] is False
    newer = now + timedelta(seconds=2)
    provider.handle_trade(2000.5, 1.0, "SELL", ts=newer, is_direct=True)
    provider.handle_trade(2000.25, 1.0, "BUY", ts=now, is_direct=True)
    assert provider.last_data_ts == newer
    assert provider.last_trade_ts == newer
    assert provider.tick_data[-1]["is_direct"] is True
    naive_now = datetime.now(timezone.utc).replace(tzinfo=None)
    expected_naive_age = (naive_now.replace(tzinfo=timezone.utc) - newer).total_seconds()
    assert provider.data_age_seconds(now=naive_now) == pytest.approx(expected_naive_age)
    assert not provider.has_fresh_data(max_age=True)
    assert not provider.has_fresh_data(max_age=float("nan"))

    malformed = data_providers.BaseProvider()
    malformed.handle_depth([(2000.0, 5.0), ("bad-price", 4.0)],
                           [(2000.5, 6.0)], ts=now)
    assert malformed.bid_depth == {}
    assert malformed.ask_depth == {2000.5: 6.0}
    malformed_data = malformed.build_market_data("GCZ6")
    assert malformed_data["data_quality"]["quote"] == "unavailable"

    market_only = data_providers.BaseProvider()
    market_only.handle_order_event({"type": "FILL", "side": "SELL",
                                    "is_market_order": True}, ts=now)
    assert market_only.build_market_data("GCZ6")["data_quality"][
        "l3_aggr_limit"] == "market_order_events_only"
    both = data_providers.BaseProvider()
    both.handle_order_event({"type": "FILL", "side": "SELL",
                             "is_market_order": True}, ts=now)
    both.handle_order_event({"type": "NEW", "side": "BUY",
                             "is_market_order": False}, ts=now)
    assert both.build_market_data("GCZ6")["data_quality"][
        "l3_aggr_limit"] == "partial_mbo_tags"
    receive_only = data_providers.BaseProvider()
    receive_only.handle_order_event({"type": "FILL", "side": "BUY",
                                     "is_market_order": True})
    receive_event = receive_only.order_events[0]
    assert receive_event["timestamp_is_source"] is False
    assert step2._fresh_l3_order_events(
        {"data_quality": {"level3": "available"},
         "order_events": [receive_event]}, time.time()) == []

    provider.reset()
    assert provider.last_data_ts is None and provider.last_depth_ts is None
    assert provider.last_trade_ts is None and provider.last_order_event_ts is None


def test_base_provider_quote_spread_age_and_crossed_book_are_explicit(monkeypatch):
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 10.0, raising=False)
    empty = data_providers.BaseProvider().build_market_data("GCZ6")
    assert empty["has_data"] is False
    assert empty["last_data_age_seconds"] is None
    assert empty["data_quality"]["quote"] == "unavailable"
    assert empty["spread_pct"] is None
    provider = data_providers.BaseProvider()
    now = datetime.now(timezone.utc)
    provider.handle_depth({2000.0: 5.0}, {2000.5: 6.0}, ts=now)
    provider.handle_trade(2000.25, 1.0, "", ts=now - timedelta(seconds=1),
                          is_direct=False)
    data = provider.build_market_data("GCZ6")
    assert data["data_quality"]["quote"] == "valid"
    assert 0 < data["spread_pct"] < 0.1
    assert data["quote_age_seconds"] >= 0
    assert data["depth_timestamp"] is not None
    assert data["last_data_age_seconds"] >= 0
    assert data["tick_data"][0]["is_direct"] is False

    provider.handle_depth({2001.0: 5.0}, {2000.5: 6.0}, ts=now)
    crossed = provider.build_market_data("GCZ6")
    assert crossed["data_quality"]["quote"] == "crossed_or_locked"
    assert crossed["spread_pct"] is None
    assert crossed["bid"] == crossed["ask"] == 0.0


def test_base_provider_flags_stale_and_future_depth_timestamps(monkeypatch):
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 5.0, raising=False)
    provider = data_providers.BaseProvider()
    provider.handle_depth({2000.0: 5.0}, {2000.5: 6.0},
                          ts=datetime.now(timezone.utc) - timedelta(seconds=10))
    stale = provider.build_market_data("GCZ6")
    assert stale["data_quality"]["quote"] == "stale"
    assert stale["spread_pct"] is not None
    provider.reset()
    provider.handle_depth({2000.0: 5.0}, {2000.5: 6.0},
                          ts=datetime.now(timezone.utc) + timedelta(seconds=2))
    future = provider.build_market_data("GCZ6")
    assert future["data_quality"]["quote"] == "future_timestamp"
    assert future["last_data_age_seconds"] < 0


class _FakeMT5ProviderFeed:
    BOOK_TYPE_SELL = 1
    BOOK_TYPE_BUY = 2
    COPY_TICKS_ALL = 0
    TIMEFRAME_M1 = 1
    TIMEFRAME_M5 = 5
    TIMEFRAME_M15 = 15
    TIMEFRAME_M30 = 30
    TIMEFRAME_H1 = 60
    TIMEFRAME_H4 = 240
    TIMEFRAME_D1 = 1440

    def __init__(self, tick):
        self.tick = tick

    def symbol_info_tick(self, _symbol):
        return self.tick

    def market_book_get(self, _symbol):
        return [SimpleNamespace(price=2000.0, volume=10.0, type=2),
                SimpleNamespace(price=2000.5, volume=9.0, type=1)]

    def copy_ticks_from(self, *_args):
        return None

    def copy_rates_from_pos(self, *_args):
        return None


def _mt5_acquire(monkeypatch, tick, *, stale_limit=300.0, tick_rows=None):
    monkeypatch.setattr(config, "TIMEFRAME", "M1", raising=False)
    monkeypatch.setattr(config, "CONFIRM_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", stale_limit, raising=False)
    monkeypatch.setattr(data_providers.time, "sleep", lambda *_args: None)
    provider = data_providers.MT5Provider()
    provider.connected = True
    provider._symbol = "XAUUSD"
    provider._mt5 = _FakeMT5ProviderFeed(tick)
    if tick_rows is not None:
        provider._mt5.copy_ticks_from = lambda *_args: tick_rows
    return provider.acquire()


def test_mt5_provider_spread_quote_age_and_l3_capabilities(monkeypatch):
    now = time.time()
    good = SimpleNamespace(bid=2000.0, ask=2000.5,
                           time_msc=int(now * 1000), time=int(now), last=2000.25)
    data = _mt5_acquire(monkeypatch, good)
    assert data["data_quality"]["quote"] == "valid"
    assert data["spread_pct"] == pytest.approx(0.0249968754)
    assert data["quote_age_seconds"] >= 0
    assert data["data_quality"]["l3_aggr_limit"] == "not_emitted"
    assert data["data_quality"]["l3_aggressive_flow"] == "unavailable"

    stale = SimpleNamespace(bid=2000.0, ask=2000.5,
                            time_msc=int((now - 301) * 1000),
                            time=int(now - 301), last=2000.25)
    stale_data = _mt5_acquire(monkeypatch, stale, stale_limit=300.0)
    assert stale_data["data_quality"]["quote"] == "stale"
    assert stale_data["spread_pct"] is not None
    assert stale_data["bid"] == stale_data["ask"] == 0.0

    missing_data = _mt5_acquire(monkeypatch, None)
    assert missing_data["data_quality"]["quote"] == "unavailable"
    assert missing_data["spread_pct"] is None

    untimestamped_print = _mt5_acquire(
        monkeypatch, good, tick_rows=[{"flags": data_providers.TICK_FLAG_BUY,
                                      "last": 2000.25, "volume_real": 1.0,
                                      "volume": 1.0}])
    assert len(untimestamped_print["tick_data"]) == 1
    assert untimestamped_print["tick_data"][0]["is_direct"] is False
    assert not step2._is_fresh_direct_trade_tick(untimestamped_print["tick_data"][0])


def test_preflight_data_spread_quote_and_decision_age_guards(monkeypatch):
    monkeypatch.setattr(config, "DATA_SOURCE", "mt5", raising=False)
    monkeypatch.setattr(config, "MAX_SPREAD_PCT", 0.05, raising=False)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 10.0, raising=False)
    good = _live_market_data()
    assert main._preflight_data_reason(good, 1.0) is None
    assert good["decision_time_data_age_seconds"] == pytest.approx(2.0)
    assert good["decision_time_quote_age_seconds"] == pytest.approx(2.0)

    assert "empty" in main._preflight_data_reason({"has_data": False})
    assert "empty/invalid" in main._preflight_data_reason(
        _live_market_data(has_data="false"))
    missing_quote = _live_market_data(data_quality={"quote": "unknown"})
    assert "not validated" in main._preflight_data_reason(missing_quote)
    missing_spread = _live_market_data(spread_pct=None)
    assert "spread unknown" in main._preflight_data_reason(missing_spread)
    assert "spread unknown/invalid" in main._preflight_data_reason(
        _live_market_data(spread_pct=0.0))
    crossed = _live_market_data(spread_pct=-0.01)
    assert "invalid spread" in main._preflight_data_reason(crossed)
    assert "invalid spread" in main._preflight_data_reason(
        _live_market_data(spread_pct=True))
    assert "bid/ask missing" in main._preflight_data_reason(
        _live_market_data(bid=0.0, ask=0.0))
    assert "locked/crossed" in main._preflight_data_reason(
        _live_market_data(bid=2000.5, ask=2000.0))
    assert "does not match bid/ask" in main._preflight_data_reason(
        _live_market_data(spread_pct=0.04))
    wide_ask = 2001.03
    wide_spread = (wide_ask - 2000.0) / ((wide_ask + 2000.0) / 2.0) * 100.0
    wide = _live_market_data(ask=wide_ask, spread_pct=wide_spread)
    assert "too wide" in main._preflight_data_reason(wide)
    aged_during_analysis = _live_market_data(quote_age_seconds=9.5)
    assert "quote stale at decision" in main._preflight_data_reason(
        aged_during_analysis, 1.0)
    unknown_age = _live_market_data(quote_age_seconds=None)
    assert "quote age unknown" in main._preflight_data_reason(unknown_age)
    future_age = _live_market_data(last_data_age_seconds=-0.1)
    assert "age unknown/invalid" in main._preflight_data_reason(future_age)
    assert "quote age unknown/invalid" in main._preflight_data_reason(
        _live_market_data(quote_age_seconds=True))
    assert "depth stale at decision" in main._preflight_data_reason(
        _live_market_data(depth_age_seconds=9.5), 1.0)
    assert "depth age unknown/invalid" in main._preflight_data_reason(
        _live_market_data(depth_age_seconds=True))
    monkeypatch.setattr(config, "MAX_SPREAD_PCT", True, raising=False)
    assert "invalid MAX_SPREAD_PCT" in main._preflight_data_reason(
        _live_market_data())
    monkeypatch.setattr(config, "MAX_SPREAD_PCT", 0.05, raising=False)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 0.0, raising=False)
    assert "invalid STALE_DATA_SECONDS" in main._preflight_data_reason(
        _live_market_data())


def test_safety_gates_apply_preflight_before_risk_and_overtrade(monkeypatch):
    import risk_manager
    import session
    import trade_guard

    monkeypatch.setattr(config, "TRADING_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "DATA_SOURCE", "mt5", raising=False)
    monkeypatch.setattr(config, "MAX_SPREAD_PCT", 0.05, raising=False)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 10.0, raising=False)
    monkeypatch.setattr(config, "RL_ENABLED", False, raising=False)
    monkeypatch.setattr(session, "is_market_open", lambda: True)
    monkeypatch.setattr(session, "describe_now", lambda: "open")
    risk_calls = []
    guard_calls = []
    monkeypatch.setattr(risk_manager, "RiskManager",
                        lambda: SimpleNamespace(check=lambda: (
                            risk_calls.append(True) or (True, "ok"))))
    monkeypatch.setattr(trade_guard, "TradeGuard",
                        lambda: SimpleNamespace(can_trade=lambda: (
                            guard_calls.append(True) or (True, "ok"))))
    monkeypatch.setattr(main, "_record", lambda *_args, **_kwargs: None)
    allowed, reason = main._safety_gates(_live_market_data())
    assert allowed and reason == "ok"
    assert len(risk_calls) == len(guard_calls) == 1
    blocked, reason = main._safety_gates(
        _live_market_data(data_quality={"quote": "unavailable"}))
    assert not blocked and "quote not validated" in reason
    assert len(risk_calls) == len(guard_calls) == 1


def test_executor_defaults_to_denied_without_pipeline_permit(monkeypatch):
    init_calls = []
    monkeypatch.setattr(config, "TRADING_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "EXECUTION_MODE", "python", raising=False)
    monkeypatch.setattr(config, "mt5_initialize",
                        lambda *_args: init_calls.append(True) or True)
    monkeypatch.setattr(step4, "mt5", object())

    result = MT5Executor("XAUUSD").execute(
        SimpleNamespace(action="BUY", confidence=100.0),
        SimpleNamespace(price=2000.0))

    assert result.status == "SKIPPED"
    assert "pipeline did not authorize" in result.reason
    assert init_calls == []


def test_preflight_bookmap_requires_validated_instrument_and_l2(monkeypatch):
    monkeypatch.setattr(config, "DATA_SOURCE", "bookmapbridge", raising=False)
    monkeypatch.setattr(config, "MAX_SPREAD_PCT", 0.05, raising=False)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 300.0, raising=False)
    diagnostics = {"status": "validated", "instrument_match": True,
                   "best_bid": 2000.0, "best_ask": 2000.5}
    good = _live_market_data(
        data_quality={"quote": "valid", "level2": "validated",
                      "instrument": "matched"},
        l2_diagnostics=diagnostics)
    assert main._preflight_data_reason(good) is None
    for alias in ("bookmap_bridge", "bmbridge", "ntbridge", "ninja"):
        monkeypatch.setattr(config, "DATA_SOURCE", alias, raising=False)
        assert main._preflight_data_reason(good) is None
        alias_unverified = _live_market_data(
            data_quality={"quote": "valid", "level2": "available",
                          "instrument": "matched"},
            l2_diagnostics=diagnostics)
        assert "L2 not validated" in main._preflight_data_reason(alias_unverified)
    monkeypatch.setattr(config, "DATA_SOURCE", "bookmapbridge", raising=False)
    no_depth_age = _live_market_data(
        depth_age_seconds=None,
        data_quality={"quote": "valid", "level2": "validated",
                      "instrument": "matched"},
        l2_diagnostics=diagnostics)
    assert "depth age unknown" in main._preflight_data_reason(no_depth_age)
    bad_l2 = _live_market_data(data_quality={"quote": "valid", "level2": "misaligned",
                                              "instrument": "matched"})
    assert "L2 not validated" in main._preflight_data_reason(bad_l2)
    mismatch = _live_market_data(data_quality={"quote": "valid", "level2": "validated",
                                                "instrument": "mismatch"})
    assert "instrument mismatch" in main._preflight_data_reason(mismatch)
    unverified = _live_market_data(data_quality={"quote": "valid", "level2": "validated"})
    assert "instrument not verified" in main._preflight_data_reason(unverified)
    missing_diagnostics = _live_market_data(
        data_quality={"quote": "valid", "level2": "validated",
                      "instrument": "matched"})
    assert "diagnostics unavailable" in main._preflight_data_reason(
        missing_diagnostics)
    mismatched_diagnostics = _live_market_data(
        data_quality={"quote": "valid", "level2": "validated",
                      "instrument": "matched"},
        l2_diagnostics={"status": "validated", "instrument_match": True,
                        "best_bid": 1999.5, "best_ask": 2000.5})
    assert "L2 quote does not match" in main._preflight_data_reason(
        mismatched_diagnostics)


def test_broker_spread_guard_requires_fresh_valid_tick_and_applies_cap(monkeypatch):
    monkeypatch.setattr(config, "MAX_SPREAD_PCT", 0.05, raising=False)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 10.0, raising=False)
    now = time.time()
    executor = MT5Executor("XAUUSD")
    tick = SimpleNamespace(bid=2000.0, ask=2000.5,
                           time_msc=int(now * 1000), time=int(now))
    ok, reason, spread, age = executor._broker_quote_check(tick, now_epoch=now)
    assert ok and reason == "ok"
    assert spread == pytest.approx(0.025)
    assert age == pytest.approx(0.0, abs=0.001)

    wide = SimpleNamespace(bid=2000.0, ask=2002.0,
                           time_msc=int(now * 1000), time=int(now))
    assert "too wide" in executor._broker_quote_check(wide, now_epoch=now)[1]
    assert "missing or invalid" in executor._broker_quote_check(
        SimpleNamespace(bid=0, ask=0, time=int(now)), now_epoch=now)[1]
    assert "locked/crossed" in executor._broker_quote_check(
        SimpleNamespace(bid=2000, ask=2000, time=int(now)), now_epoch=now)[1]
    assert "timestamp unavailable" in executor._broker_quote_check(
        SimpleNamespace(bid=2000, ask=2000.5), now_epoch=now)[1]
    assert "future-dated" in executor._broker_quote_check(
        SimpleNamespace(bid=2000, ask=2000.5,
                        time_msc=int((now + 1) * 1000)), now_epoch=now)[1]
    assert "stale" in executor._broker_quote_check(
        SimpleNamespace(bid=2000, ask=2000.5,
                        time_msc=int((now - 11) * 1000)), now_epoch=now)[1]

    monkeypatch.setattr(config, "MAX_SPREAD_PCT", 0.0, raising=False)
    assert executor._broker_quote_check(wide, now_epoch=now)[0]
    # Disabling only the width cap does not make a missing/crossed quote valid.
    assert not executor._broker_quote_check(
        SimpleNamespace(bid=1, ask=1, time=int(now)), now_epoch=now)[0]
    monkeypatch.setattr(config, "MAX_SPREAD_PCT", float("nan"), raising=False)
    invalid_cap = executor._broker_quote_check(tick, now_epoch=now)
    assert not invalid_cap[0] and "invalid MAX_SPREAD_PCT" in invalid_cap[1]
    monkeypatch.setattr(config, "MAX_SPREAD_PCT", 0.05, raising=False)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 0.0, raising=False)
    invalid_age_limit = executor._broker_quote_check(tick, now_epoch=now)
    assert not invalid_age_limit[0] and "invalid STALE_DATA_SECONDS" in invalid_age_limit[1]


def test_place_order_does_not_call_mt5_order_send_when_broker_spread_blocks(monkeypatch):
    now = time.time()
    calls = []
    fake_mt5 = SimpleNamespace(
        symbol_info_tick=lambda _symbol: SimpleNamespace(
            bid=2000.0, ask=2002.0, time_msc=int(now * 1000), time=int(now)),
        order_send=lambda request: calls.append(request))
    monkeypatch.setattr(step4, "mt5", fake_mt5)
    monkeypatch.setattr(config, "MAX_SPREAD_PCT", 0.05, raising=False)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 10.0, raising=False)
    result = MT5Executor("XAUUSD")._place_order(
        "BUY", SimpleNamespace(price=2000.0), "QUIET")
    assert result.status == "DEFERRED"
    assert "broker spread too wide" in result.reason
    assert calls == []


def test_l2_inputs_reject_crossed_misaligned_stale_and_unvalidated_books(monkeypatch):
    monkeypatch.setattr(config, "DATA_SOURCE", "mt5", raising=False)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 300.0, raising=False)
    valid = _live_market_data()
    usable, bids, asks, book, updates, reason = step2._l2_inputs(valid)
    assert usable and bids[2000.0] == 10.0 and asks[2000.5] == 9.0
    assert book["bids"][0][0] == 2000.0 and not reason

    crossed = _live_market_data(bid_depth={2001.0: 1.0},
                                ask_depth={2000.5: 2.0})
    assert "crossed/locked" in step2._l2_inputs(crossed)[-1]
    misaligned = _live_market_data(price=2010.0)
    assert "misalignment" in step2._l2_inputs(misaligned)[-1]
    stale = _live_market_data(depth_age_seconds=301.0)
    assert "stale" in step2._l2_inputs(stale)[-1]
    bad_quality = _live_market_data(data_quality={"quote": "invalid",
                                                   "level2": "live"})
    assert "quote" in step2._l2_inputs(bad_quality)[-1]
    bad_spread = _live_market_data(spread_pct=0.04)
    assert "spread does not match" in step2._l2_inputs(bad_spread)[-1]
    bad_quote = _live_market_data(bid=0.0, ask=0.0)
    assert "quote invalid or crossed" in step2._l2_inputs(bad_quote)[-1]
    bad_update = _live_market_data(book_updates=[{
        "bids": {2001.0: 1.0}, "asks": {2000.5: 2.0},
        "timestamp": datetime.now(timezone.utc).isoformat()}])
    assert "update crossed" in step2._l2_inputs(bad_update)[-1]
    malformed = _live_market_data(bid_depth={2000.0: float("nan")})
    assert "invalid price/size" in step2._l2_inputs(malformed)[-1]

    monkeypatch.setattr(config, "DATA_SOURCE", "bookmapbridge", raising=False)
    strict = _live_market_data(data_quality={"quote": "valid", "level2": "validated",
                                              "instrument": "matched"},
                               l2_diagnostics={"status": "validated"})
    assert step2._l2_inputs(strict)[0]
    for alias in ("bookmap_bridge", "bmbridge", "ntbridge", "ninja"):
        monkeypatch.setattr(config, "DATA_SOURCE", alias, raising=False)
        assert step2._l2_inputs(strict)[0]
        unvalidated_alias = dict(strict)
        unvalidated_alias["data_quality"] = dict(strict["data_quality"])
        unvalidated_alias["data_quality"]["level2"] = "available"
        assert "requires validated" in step2._l2_inputs(unvalidated_alias)[-1]
    monkeypatch.setattr(config, "DATA_SOURCE", "bookmapbridge", raising=False)
    strict["data_quality"]["instrument"] = "unknown"
    assert "instrument is not verified" in step2._l2_inputs(strict)[-1]
    strict["data_quality"]["instrument"] = "matched"
    strict["data_quality"]["level2"] = "available"
    assert "requires validated" in step2._l2_inputs(strict)[-1]
    strict["data_quality"]["level2"] = "validated"
    strict["l2_diagnostics"] = {"status": "synthetic"}
    assert "diagnostics status=synthetic" in step2._l2_inputs(strict)[-1]

    monkeypatch.setattr(config, "DATA_SOURCE", "replay", raising=False)
    replay = _live_market_data(
        data_quality={"quote": "valid", "level2": "synthetic"},
        l2_diagnostics={"status": "synthetic"})
    assert step2._l2_inputs(replay)[0]


def test_l2_reset_prevents_ofi_bridging_crossed_feed_and_bad_updates(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_SOURCE", "mt5", raising=False)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 300.0, raising=False)
    monkeypatch.setattr(step2, "_BOOK_STATE_PATH", tmp_path / "book.json")
    monkeypatch.setattr(step2, "_PERSISTENT_L2_TIMESTAMP", None)
    monkeypatch.setattr(step2, "_PERSISTENT_L2_INSTRUMENT", None)
    l2 = step2.OrderBookDepthAnalyzer()
    first = _live_market_data()
    _, _, metrics, usable, reason = step2._l2_metrics_for(first, l2)
    assert usable and not reason
    assert l2._prev_bids and l2._prev_asks

    crossed = _live_market_data(data_quality={"quote": "valid", "level2": "crossed"})
    _, _, metrics, usable, reason = step2._l2_metrics_for(crossed, l2)
    assert not usable and "crossed" in reason
    assert not l2._prev_bids and not l2._prev_asks
    assert step2._PERSISTENT_L2_TIMESTAMP is None

    recovered = _live_market_data()
    _, _, metrics, usable, reason = step2._l2_metrics_for(recovered, l2)
    assert usable
    assert metrics["ofi"] == 0.0
    assert l2.cumulative_ofi == 0.0

    # A stale transition in an otherwise fresh batch resets continuity and only
    # establishes the final snapshot as a new baseline.
    bad_update = _live_market_data(book_updates=[{
        "bids": {2000.0: 100.0}, "asks": {2000.5: 1.0},
        "timestamp": (datetime.now(timezone.utc) - timedelta(seconds=500)).isoformat(),
    }])
    _, _, metrics, usable, reason = step2._l2_metrics_for(bad_update, l2)
    assert usable and "continuity reset" in reason
    assert metrics["ofi"] == 0.0
    assert l2.cumulative_ofi == 0.0

    changed_instrument = _live_market_data(data_symbol="MGCZ6", symbol="MGCZ6")
    _, _, metrics, usable, reason = step2._l2_metrics_for(changed_instrument, l2)
    assert usable and "instrument/source changed" in reason
    assert metrics["ofi"] == 0.0
    assert step2._PERSISTENT_L2_INSTRUMENT == "mt5:MGCZ6"

    future_ts = datetime.now(timezone.utc) + timedelta(seconds=5)
    future = _live_market_data(depth_timestamp=future_ts.isoformat(),
                               quote_timestamp=future_ts.isoformat(),
                               depth_age_seconds=0.0, quote_age_seconds=0.0)
    _, _, _, usable, reason = step2._l2_metrics_for(future, l2)
    assert not usable and "future-dated" in reason


def test_replay_l2_clock_reversal_resets_continuity(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_SOURCE", "replay", raising=False)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 300.0, raising=False)
    monkeypatch.setattr(step2, "_BOOK_STATE_PATH", tmp_path / "replay-book.json")
    monkeypatch.setattr(step2, "_PERSISTENT_L2_TIMESTAMP", None)
    monkeypatch.setattr(step2, "_PERSISTENT_L2_INSTRUMENT", None)
    l2 = step2.OrderBookDepthAnalyzer()
    first_time = datetime.now(timezone.utc)
    first = _live_market_data(
        depth_timestamp=first_time.isoformat(),
        quote_timestamp=first_time.isoformat())
    _, _, first_metrics, first_ok, first_reason = step2._l2_metrics_for(first, l2)
    assert first_ok and not first_reason
    assert first_metrics["ofi"] == 0.0

    rewind_time = first_time - timedelta(seconds=10)
    rewound = _live_market_data(
        depth_timestamp=rewind_time.isoformat(),
        quote_timestamp=rewind_time.isoformat())
    _, _, metrics, usable, reason = step2._l2_metrics_for(rewound, l2)
    assert usable
    assert "observation gap" in reason
    assert metrics["ofi"] == 0.0


def test_direct_aggressor_ticks_are_explicit_fresh_and_side_valid(monkeypatch):
    monkeypatch.setattr(config, "DATA_SOURCE", "mt5", raising=False)
    monkeypatch.setattr(config, "POWER_MAX_TICK_AGE_SECONDS", 60.0, raising=False)
    monkeypatch.setattr(config, "AGGRESSIVE_FROM_TICKS", True, raising=False)
    now = datetime.now(timezone.utc)
    ticks = [
        {"price": 2000, "volume": 1, "side": "BUY", "is_direct": True,
         "timestamp": now.isoformat()},
        {"price": 2000, "volume": 1, "side": "SELL", "is_direct": False,
         "timestamp": now.isoformat()},
        {"price": 2000, "volume": 1, "side": "", "is_direct": True,
         "timestamp": now.isoformat()},
        {"price": 2000, "volume": 1, "side": "BID", "is_direct": True,
         "timestamp": now.isoformat()},
        {"price": 2000, "volume": 1, "side": "SELL", "is_direct": True,
         "timestamp": (now - timedelta(seconds=61)).isoformat()},
        {"price": float("nan"), "volume": 1, "side": "BUY", "is_direct": True,
         "timestamp": now.isoformat()},
        {"price": 2000, "volume": 1, "side": "SELL", "is_direct": True,
         "timestamp": (now + timedelta(seconds=1)).isoformat()},
        {"price": 2000, "volume": 1, "side": "BUY", "is_direct": "false",
         "timestamp": now.isoformat()},
    ]
    assert step2._is_fresh_direct_trade_tick(ticks[0])
    assert not step2._is_fresh_direct_trade_tick(ticks[1])
    assert not step2._is_fresh_direct_trade_tick(ticks[2])
    assert not step2._is_fresh_direct_trade_tick(ticks[3])
    assert not step2._is_fresh_direct_trade_tick(ticks[4])
    assert not step2._is_fresh_direct_trade_tick(ticks[7])
    naive_now = datetime.now(timezone.utc).replace(tzinfo=None)
    naive_tick = {"price": 2000, "volume": 1, "side": "BUY", "is_direct": True,
                  "timestamp": naive_now.isoformat()}
    assert step2._is_fresh_direct_trade_tick(
        naive_tick, now_epoch=naive_now.replace(tzinfo=timezone.utc).timestamp())
    l3 = step2.Level3OrderBookAnalyzer()
    assert step2._feed_direct_trade_ticks(l3, ticks) == 1
    assert l3.market_orders == 1 and l3.aggressive_buys == 1
    monkeypatch.setattr(config, "AGGRESSIVE_FROM_TICKS", False, raising=False)
    l3_disabled = step2.Level3OrderBookAnalyzer()
    assert step2._feed_direct_trade_ticks(l3_disabled, ticks) == 0
    assert l3_disabled.market_orders == 0

    # Replay uses its event clock for freshness rather than treating every
    # recorded direct-side tick as fresh just because the source is offline.
    monkeypatch.setattr(config, "AGGRESSIVE_FROM_TICKS", True, raising=False)
    monkeypatch.setattr(config, "DATA_SOURCE", "replay", raising=False)
    replay_now = datetime.now(timezone.utc)
    replay_ticks = [
        {"price": 2000, "volume": 1, "side": "BUY", "is_direct": True,
         "timestamp": (replay_now - timedelta(seconds=5)).isoformat()},
        {"price": 2000, "volume": 1, "side": "SELL", "is_direct": True,
         "timestamp": (replay_now - timedelta(seconds=61)).isoformat()},
    ]
    replay_epoch = replay_now.timestamp()
    assert step2._is_fresh_direct_trade_tick(
        replay_ticks[0], now_epoch=replay_epoch)
    assert not step2._is_fresh_direct_trade_tick(
        replay_ticks[1], now_epoch=replay_epoch)
    replay_l3 = step2.Level3OrderBookAnalyzer()
    assert step2._feed_direct_trade_ticks(
        replay_l3, replay_ticks, now_epoch=replay_epoch) == 1
    replay_quality = step2._snapshot_data_quality(
        _live_market_data(), replay_ticks, now_epoch=replay_epoch)
    assert replay_quality["l3_aggressive_flow"] == "direct_trade_prints_only"

    # A missing/blank source is unknown, not an implicit demo freshness bypass.
    unclocked = {"price": 2000, "volume": 1, "side": "BUY",
                 "is_direct": True}
    monkeypatch.setattr(config, "DATA_SOURCE", "demo", raising=False)
    assert not step2._is_fresh_direct_trade_tick(unclocked)
    monkeypatch.setattr(config, "DATA_SOURCE", "", raising=False)
    assert not step2._is_fresh_direct_trade_tick(unclocked)


def test_full_and_cached_analysis_suppress_untrusted_l2_and_inferred_l3_ticks(
        monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_SOURCE", "demo", raising=False)
    monkeypatch.setattr(step2, "_BOOK_STATE_PATH", tmp_path / "book.json")
    monkeypatch.setattr(step2, "_PERSISTENT_L2", None)
    monkeypatch.setattr(step2, "_PERSISTENT_L2_TIMESTAMP", None)
    monkeypatch.setattr(step2, "_PERSISTENT_L2_INSTRUMENT", None)
    monkeypatch.setattr(step2, "_A1_BAR_SERVICE_CACHE", {"entry": None})
    market_data = step2._synthetic_market_data(seed=41)
    market_data["order_events"] = []
    market_data["tick_data"] = [
        {"price": market_data["price"], "volume": 5.0, "side": "BUY",
         "is_direct": False}]
    now = datetime(2026, 10, 22, 12, 1, 0, tzinfo=timezone.utc)
    full = step2.analyze_market(market_data, now=now)
    assert full.level3.market_orders == 0
    assert full.data_quality["l3_aggressive_flow"] == "synthetic"
    full.quality_evidence = {"quote_status": "prior_cycle"}
    full.shadow_candidates = {"power_lead": {
        "market_quality_evidence": {"quote_status": "prior_cycle"}}}
    full.shot = {"shadow_candidates": {"passive_limit": {
        "market_quality_evidence": {"quote_status": "prior_cycle"}}}}

    bad = dict(market_data)
    bad["data_quality"] = {**market_data["data_quality"], "level2": "crossed"}
    cached = step2.analyze_market(bad, now=now + timedelta(seconds=1))
    assert any("A1 bar-service" in note for note in cached.notes)
    assert cached.data_quality["level2"] == "suppressed"
    assert cached.order_flow.ofi == 0.0
    assert cached.level3.market_orders == 0
    assert cached.quality_evidence == {}
    assert "market_quality_evidence" not in cached.shadow_candidates["power_lead"]
    assert "market_quality_evidence" not in cached.shot["shadow_candidates"]["passive_limit"]
    assert any("L2 analytics/continuity" in note for note in cached.notes)


def test_cached_replay_marks_stale_l2_as_suppressed(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_SOURCE", "replay", raising=False)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 30.0, raising=False)
    monkeypatch.setattr(step2, "_BOOK_STATE_PATH", tmp_path / "replay-book.json")
    monkeypatch.setattr(step2, "_PERSISTENT_L2", None)
    monkeypatch.setattr(step2, "_PERSISTENT_L2_TIMESTAMP", None)
    monkeypatch.setattr(step2, "_PERSISTENT_L2_INSTRUMENT", None)
    monkeypatch.setattr(step2, "_A1_BAR_SERVICE_CACHE", {"entry": None})
    now = datetime(2026, 10, 22, 12, 1, 0, tzinfo=timezone.utc)
    market_data = step2._synthetic_market_data(seed=53)
    for key in ("depth_timestamp", "quote_timestamp", "last_data_timestamp"):
        market_data[key] = (now - timedelta(seconds=1)).isoformat()
    market_data["depth_age_seconds"] = 1.0
    market_data["quote_age_seconds"] = 1.0
    market_data["data_quality"]["quote"] = "valid"
    market_data["l2_diagnostics"] = {"status": "synthetic"}

    full = step2.analyze_market(market_data, now=now)
    assert full is not None
    stale = dict(market_data)
    for key in ("depth_timestamp", "quote_timestamp", "last_data_timestamp"):
        stale[key] = (now - timedelta(seconds=60)).isoformat()
    stale["depth_age_seconds"] = 60.0
    stale["quote_age_seconds"] = 60.0
    cached = step2.analyze_market(stale, now=now + timedelta(seconds=1))

    assert any("A1 bar-service" in note for note in cached.notes)
    assert any("L2 snapshot stale" in note for note in cached.notes)
    assert cached.data_quality["level2"] == "suppressed"
    assert cached.order_flow.ofi == 0.0


def test_l3_quality_labels_differentiate_direct_tape_from_mbo(monkeypatch):
    monkeypatch.setattr(config, "DATA_SOURCE", "mt5", raising=False)
    now = datetime.now(timezone.utc)
    direct = {"price": 2000, "volume": 2, "side": "BUY", "is_direct": True,
              "timestamp": now.isoformat()}
    quality = step2._snapshot_data_quality(
        _live_market_data(), [direct])
    assert quality["l3_aggressive_flow"] == "direct_trade_prints_only"
    assert quality["l3_aggr_limit"] == "not_emitted"

    events = [
        {"type": "NEW", "side": "BUY", "is_market_order": False,
         "timestamp": now.isoformat()},
        {"type": "FILL", "side": "SELL", "is_market_order": True,
         "timestamp": now.isoformat()},
    ]
    quality = step2._snapshot_data_quality(
        _live_market_data(order_events=events), [direct])
    assert quality["l3_aggressive_flow"] == "partial_mbo_market_order_events"
    assert quality["l3_aggr_limit"] == "partial_mbo_tags"

    market_only = step2._snapshot_data_quality(
        _live_market_data(order_events=[events[1]]), [direct])
    assert market_only["l3_aggr_limit"] == "market_order_events_only"
    passive_only = step2._snapshot_data_quality(
        _live_market_data(order_events=[events[0]]), [direct])
    assert passive_only["l3_aggressive_flow"] == "direct_trade_prints_only"
    assert passive_only["l3_aggr_limit"] == "passive_order_events_only"

    stale = {**direct, "timestamp": (now - timedelta(seconds=61)).isoformat()}
    quality = step2._snapshot_data_quality(_live_market_data(), [stale])
    assert quality["l3_aggressive_flow"] == "unavailable"
    assert quality["l3_aggr_limit"] == "not_emitted"


def test_l3_order_events_require_fresh_source_timestamps(monkeypatch):
    monkeypatch.setattr(config, "DATA_SOURCE", "replay", raising=False)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 60.0, raising=False)
    now = datetime.now(timezone.utc)
    now_epoch = now.timestamp()
    market = {"type": "FILL", "side": "BUY", "is_market_order": True,
              "timestamp": now.isoformat()}
    passive = {"type": "NEW", "side": "SELL", "is_market_order": False,
               "timestamp": now.isoformat()}
    events = [market, passive,
              {"type": "FILL", "side": "SELL", "is_market_order": True},
              {"type": "FILL", "side": "SELL", "is_market_order": True,
               "timestamp": (now - timedelta(seconds=61)).isoformat()},
              {"type": "FILL", "side": "SELL", "is_market_order": True,
               "timestamp": (now + timedelta(seconds=1)).isoformat()},
              {"type": "NEW", "side": "UNKNOWN", "is_market_order": False,
               "timestamp": now.isoformat()}]
    data = {"data_quality": {"level3": "available"},
            "order_events": events}
    assert step2._fresh_l3_order_events(data, now_epoch) == [market, passive]
    quality = step2._snapshot_data_quality(data, [], now_epoch=now_epoch)
    assert quality["level3"] == "available"
    assert quality["level3_events"] == "fresh_window"
    assert quality["l3_aggressive_flow"] == "partial_mbo_market_order_events"
    assert quality["l3_aggr_limit"] == "partial_mbo_tags"

    unclocked_data = {"data_quality": {"level3": "available"},
                      "order_events": events[2:]}
    assert step2._fresh_l3_order_events(unclocked_data, now_epoch) == []
    unclocked_quality = step2._snapshot_data_quality(
        unclocked_data, [], now_epoch=now_epoch)
    assert unclocked_quality["level3"] == "stale_or_unverifiable"
    assert unclocked_quality["level3_events"] == "stale_or_unverifiable"
    assert unclocked_quality["l3_aggressive_flow"] == "unavailable"

    synthetic = {"data_quality": {"level3": "synthetic"},
                 "order_events": [events[2]]}
    assert step2._fresh_l3_order_events(synthetic, now_epoch) == [events[2]]
    assert step2._snapshot_data_quality(
        synthetic, [], now_epoch=now_epoch)["l3_aggressive_flow"] == "synthetic"


def test_snapshot_format_shows_unknown_spread_as_na_and_serializes_evidence():
    now = datetime.now(timezone.utc)
    snapshot = step2.MarketSnapshot(
        timestamp=now, price=2000.0, bid=0.0, ask=0.0, volume=1.0,
        order_flow=step2.OrderFlowMetrics(0, 0, 0, 0, 0, 0, 1, 0),
        footprint=step2.FootprintMetrics(), level3=step2.Level3Events(),
        volatility=step2.VolatilityMetrics(), trend=step2.TrendMetrics(),
        volume_profile=step2.VolumeProfileMetrics(), macro=step2.MacroMetrics(),
        news=step2.NewsAndEvents(),
        data_quality={"quote": "unknown", "l3_aggr_limit": "not_emitted"})
    snapshot.quality_evidence = {"quote_status": "unknown", "spread_pct": None}
    rendered = step2.format_snapshot(snapshot)
    assert "spread: n/a (unknown)" in rendered
    assert "l3_aggr_limit=not_emitted" in rendered
    assert step2.snapshot_to_dict(snapshot)["quality_evidence"]["spread_pct"] is None
    snapshot.spread_pct = 0.04
    snapshot.data_quality["quote"] = "stale"
    assert "spread: 0.040% (untrusted: stale)" in step2.format_snapshot(snapshot)


def test_market_quality_evidence_is_timestamped_and_read_only(monkeypatch):
    monkeypatch.setattr(config, "DATA_SOURCE", "mt5", raising=False)
    monkeypatch.setattr(config, "MAX_SPREAD_PCT", 0.05, raising=False)
    data = _live_market_data(last_data_age_seconds=2.0,
                             quote_age_seconds=3.0,
                             depth_age_seconds=4.0,
                             last_trade_age_seconds=6.0,
                             quote_timestamp="2026-10-22T12:00:00+00:00",
                             depth_timestamp="2026-10-22T12:00:00+00:00",
                             last_trade_timestamp="2026-10-22T11:59:57+00:00",
                             l2_diagnostics={
                                 "status": "validated", "fresh_bid_levels": 5,
                                 "tick_size": 0.1, "price_units": "USD/oz",
                                 "depth_size_units": "feed units",
                                 "timestamp_basis": "bridge_receive_time",
                                 "source_event_time_verified": False,
                                 "top5_bid_levels": [{"price": 2000.0, "size": 10.0,
                                                       "age_seconds": 1.0}],
                             },
                             feed_warnings=["sample warning"])
    data_before = {key: value for key, value in data.items()}
    evidence = main._market_quality_evidence(data, 5.0, False,
                                             "live spread too wide")
    assert evidence["captured_at"].endswith("+00:00")
    assert evidence["feed_age_seconds_at_decision"] == pytest.approx(7.0)
    assert evidence["quote_age_seconds_at_decision"] == pytest.approx(8.0)
    assert evidence["level2_status"] == "live"
    assert evidence["price"] == pytest.approx(2000.25)
    assert evidence["bid"] == pytest.approx(2000.0)
    assert evidence["ask"] == pytest.approx(2000.5)
    assert evidence["quote_timestamp"] == "2026-10-22T12:00:00+00:00"
    assert evidence["depth_timestamp"] == evidence["quote_timestamp"]
    assert evidence["depth_age_seconds_at_decision"] == pytest.approx(9.0)
    assert evidence["last_trade_age_seconds_at_decision"] == pytest.approx(11.0)
    assert evidence["tick_size"] == pytest.approx(0.1)
    assert evidence["price_units"] == "USD/oz"
    assert evidence["timestamp_basis"] == "bridge_receive_time"
    assert evidence["source_event_time_verified"] is False
    assert evidence["l2_diagnostics"]["top5_bid_levels"][0]["size"] == 10.0
    assert evidence["l2_diagnostics"]["fresh_bid_levels"] == 5
    assert data == data_before
    assert evidence["safety_gate_status"] == "BLOCKED"
    assert evidence["safety_gate_reason"] == "live spread too wide"
    missing_spread = main._market_quality_evidence(
        _live_market_data(spread_pct=None), 0.0, False, "spread unknown")
    assert missing_spread["quote_status"] == "spread_unknown"
    zero_spread = main._market_quality_evidence(
        _live_market_data(spread_pct=0.0), 0.0, False, "zero spread")
    assert zero_spread["quote_status"] == "invalid_spread"
    mismatched_spread = main._market_quality_evidence(
        _live_market_data(spread_pct=0.04), 0.0, False, "spread mismatch")
    assert mismatched_spread["quote_status"] == "spread_quote_mismatch"
    invalid_quote = main._market_quality_evidence(
        _live_market_data(bid=0.0, ask=0.0), 0.0, False, "invalid quote")
    assert invalid_quote["quote_status"] == "invalid_quote"
    invalid_spread = main._market_quality_evidence(
        _live_market_data(spread_pct=True), 0.0, False, "invalid spread")
    assert invalid_spread["quote_status"] == "invalid_spread"
    negative_spread = main._market_quality_evidence(
        _live_market_data(spread_pct=-0.01), 0.0, False, "invalid spread")
    assert negative_spread["quote_status"] == "invalid_spread"


def test_market_quality_snapshot_persistence_includes_receipt(monkeypatch, tmp_path):
    snapshot = step2.MarketSnapshot(
        timestamp=datetime.now(timezone.utc), price=2000.25, bid=2000.0,
        ask=2000.5, volume=1.0,
        order_flow=step2.OrderFlowMetrics(0, 0, 0, 0, 0, 0, 1, 0),
        footprint=step2.FootprintMetrics(), level3=step2.Level3Events(),
        volatility=step2.VolatilityMetrics(), trend=step2.TrendMetrics(),
        volume_profile=step2.VolumeProfileMetrics(), macro=step2.MacroMetrics(),
        news=step2.NewsAndEvents(), quality_evidence={
            "captured_at": "2026-10-22T12:00:00+00:00",
            "quote_status": "stale", "spread_pct": 0.04,
            "safety_gate_status": "BLOCKED"},
        shadow_candidates={"power_lead": {
            "execution_enabled": False,
            "market_quality_evidence": {"quote_status": "stale"}}})
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    assert main._persist_market_quality_snapshot(snapshot)
    saved = (tmp_path / "market_snapshot.json").read_text(encoding="utf-8")
    assert '"quote_status": "stale"' in saved
    assert '"safety_gate_status": "BLOCKED"' in saved
    assert not (tmp_path / "market_snapshot.json.quality.tmp").exists()
    evidence_lines = (tmp_path / "market_quality_evidence.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(evidence_lines) == 1
    receipt = json.loads(evidence_lines[0])
    assert receipt["quality_evidence"]["quote_status"] == "stale"
    assert receipt["quality_evidence"]["safety_gate_status"] == "BLOCKED"
    assert receipt["shadow_candidates"]["power_lead"]["market_quality_evidence"][
        "quote_status"] == "stale"


def test_power_shadow_candidate_carries_quality_evidence_without_authorizing(monkeypatch):
    monkeypatch.setattr(config, "AI_CONFIDENCE_THRESHOLD", 70.0, raising=False)
    monkeypatch.setattr(config, "POWER_LEAD_CONF_FLOOR", 30.0, raising=False)
    monkeypatch.setattr(config, "POWER_LEAD_SHADOW_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "EXECUTION_MODE", "python", raising=False)
    monkeypatch.setattr(main, "_record", lambda *_args, **_kwargs: None)
    decision = SimpleNamespace(action="HOLD", confidence=0.0, rationale="",
                               model="none-quiet")
    snapshot = SimpleNamespace(
        shot={"shot": "GO", "side": "BUY", "entry_style": "AGGRESSIVE_MARKET"},
        power={"direction": "UP", "reason_code": "UPWARD_FORCE_DOMINATES"},
        confidence=35.0, signal_direction="BUY", shadow_candidates={},
        quality_evidence={"quote_status": "stale", "spread_pct": 0.04,
                          "safety_gate_status": "BLOCKED"})
    allowed, reason = main._shooting_execution_gate(decision, snapshot)
    assert not allowed and "shadow candidate only" in reason
    candidate = snapshot.shadow_candidates["power_lead"]
    assert candidate["execution_enabled"] is False
    assert candidate["market_quality_evidence"]["quote_status"] == "stale"
    assert candidate["market_quality_evidence"]["safety_gate_status"] == "BLOCKED"


def test_post_gate_receipt_refreshes_shadow_journal_candidates_without_policy_change():
    evidence = {"captured_at": "2026-10-22T12:00:00+00:00",
                "quote_status": "stale", "safety_gate_status": "BLOCKED"}
    power_candidate = {"execution_enabled": False, "eligible": True,
                       "status": "ELIGIBLE_SHADOW_CANDIDATE"}
    passive_candidate = {"execution_enabled": False, "candidate": True,
                         "candidate_eligible": True}
    snapshot = SimpleNamespace(
        quality_evidence=evidence,
        shadow_candidates={"power_lead": power_candidate},
        shot={"shadow_candidates": {"passive_limit": passive_candidate}})
    main._attach_quality_evidence_to_shadow_candidates(snapshot)
    assert power_candidate["market_quality_evidence"] == evidence
    assert passive_candidate["market_quality_evidence"] == evidence
    assert power_candidate["execution_enabled"] is False
    assert power_candidate["eligible"] is True
    assert passive_candidate["execution_enabled"] is False
    assert passive_candidate["candidate_eligible"] is True


def test_replay_provider_recomputes_recorded_freshness_and_unknown_timestamps(
        monkeypatch, tmp_path):
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 5.0, raising=False)
    now = datetime.now(timezone.utc)
    stale_file = tmp_path / "stale_market_data.json"
    stale_data = _live_market_data(
        last_data_timestamp=(now - timedelta(seconds=12)).isoformat(),
        quote_timestamp=(now - timedelta(seconds=10)).isoformat(),
        depth_timestamp=(now - timedelta(seconds=10)).isoformat())
    stale_file.write_text(json.dumps(stale_data), encoding="utf-8")
    replayed = data_providers.ReplayProvider(str(stale_file)).acquire()
    assert replayed["data_source"] == "replay"
    assert replayed["last_data_age_seconds"] >= 9.0
    assert replayed["quote_age_seconds"] >= 9.0
    assert replayed["data_quality"]["quote"] == "stale"
    assert replayed["data_quality"]["level2"] == "stale_or_missing_levels"

    mismatch_file = tmp_path / "mismatched_market_data.json"
    mismatched = _live_market_data(
        last_data_timestamp=now.isoformat(), quote_timestamp=now.isoformat(),
        depth_timestamp=now.isoformat(), spread_pct=0.04)
    mismatch_file.write_text(json.dumps(mismatched), encoding="utf-8")
    mismatch_replay = data_providers.ReplayProvider(str(mismatch_file)).acquire()
    assert mismatch_replay["data_quality"]["quote"] == "spread_quote_mismatch"
    assert mismatch_replay["data_quality"]["level2"] == "stale_or_missing_levels"

    legacy_file = tmp_path / "legacy_market_data.json"
    legacy = _live_market_data()
    for key in ("last_data_timestamp", "quote_timestamp", "depth_timestamp"):
        legacy.pop(key, None)
    legacy_file.write_text(json.dumps(legacy), encoding="utf-8")
    legacy_replay = data_providers.ReplayProvider(str(legacy_file)).acquire()
    assert legacy_replay["last_data_age_seconds"] is None
    assert legacy_replay["quote_age_seconds"] is None
    assert legacy_replay["data_quality"]["quote"] == "timestamp_unknown"


def test_replayer_preserves_event_clock_direct_ticks_depth_and_mbo(
        monkeypatch, tmp_path):
    # A historical replay timestamp must use the replay clock, not wall time.
    now = datetime(2020, 1, 2, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(config, "BOOKMAP_LEVEL_MAX_AGE", 900.0, raising=False)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 30.0, raising=False)
    monkeypatch.setattr(config, "DATA_SOURCE", "replay", raising=False)
    monkeypatch.setattr(step2, "_BOOK_STATE_PATH", tmp_path / "book.json")
    monkeypatch.setattr(step2, "_PERSISTENT_L2", None)
    monkeypatch.setattr(step2, "_PERSISTENT_L2_TIMESTAMP", None)
    monkeypatch.setattr(step2, "_PERSISTENT_L2_INSTRUMENT", None)
    state = SimpleNamespace(
        ticks=[
            {"ts": now - timedelta(seconds=1), "price": 2000.25,
             "volume": 2.0, "side": "BUY", "is_direct": True,
             "operation": "BUY"},
            {"ts": now - timedelta(seconds=61), "price": 2000.0,
             "volume": 1.0, "side": "SELL", "is_direct": True,
             "operation": "SELL"},
            {"ts": now + timedelta(seconds=1), "price": 2000.5,
             "volume": 1.0, "side": "SELL", "is_direct": True,
             "operation": "SELL"},
        ],
        bids={2000.0: 10.0, 1999.5: 5.0, 1999.0: 4.0},
        asks={2000.5: 9.0, 2001.0: 4.0},
        level_ts_b={2000.0: now - timedelta(seconds=2),
                    1999.5: now - timedelta(seconds=2),
                    1999.0: now - timedelta(seconds=901)},
        level_ts_a={2000.5: now - timedelta(seconds=3),
                    2001.0: now - timedelta(seconds=3)},
        mbo_events=[{"id": "order-1", "type": "NEW", "side": "BID",
                     "price": 2000.0, "size": 2.0,
                     "timestamp": (now - timedelta(seconds=2)).isoformat()}],
        last_dt=now,
    )
    provider = backtest.Replayer()
    provider.load_from_state(state, window_seconds=60,
                             now_ts=now.timestamp())
    data = provider.build_market_data(symbol="MGCZ6")

    assert data["data_source"] == "replay"
    assert data["data_quality"]["quote"] == "valid"
    assert data["data_quality"]["level2"] == "available"
    assert data["bid"] == pytest.approx(2000.0)
    assert data["ask"] == pytest.approx(2000.5)
    assert data["quote_age_seconds"] == pytest.approx(3.0)
    assert data["last_trade_age_seconds"] == pytest.approx(1.0)
    assert data["last_order_event_age_seconds"] == pytest.approx(2.0)
    assert len(data["tick_data"]) == 1
    assert data["tick_data"][0]["is_direct"] is True
    assert data["tick_data"][0]["timestamp"] == (now - timedelta(seconds=1)).isoformat()
    assert 1999.0 not in data["bid_depth"]
    assert [event["id"] for event in data["order_events"]] == ["order-1"]
    assert data["data_quality"]["level3"] == "available"
    assert data["data_quality"]["l3_aggressive_flow"] == "direct_trade_prints_only"
    usable, *_ = step2._l2_inputs(data)
    assert usable
    l2 = step2.OrderBookDepthAnalyzer()
    _, _, _, replay_l2_usable, replay_reason = step2._l2_metrics_for(
        data, l2, now=now)
    assert replay_l2_usable, replay_reason


def test_backtester_restores_data_source_when_analysis_raises(monkeypatch):
    monkeypatch.setattr(config, "DATA_SOURCE", "mt5", raising=False)
    backtester = backtest.Backtester.__new__(backtest.Backtester)
    backtester.stats = {"cycles": 0}
    backtester.clock = SimpleNamespace(ts=1_800_000_000.0)
    backtester.args = SimpleNamespace(window_seconds=60.0)
    backtester.provider = SimpleNamespace(
        load_from_state=lambda *_args: None,
        build_market_data=lambda **_kwargs: {"data_source": "replay"})

    def raise_analysis(*_args, **_kwargs):
        raise RuntimeError("synthetic analysis failure")

    monkeypatch.setattr(backtest.s2, "analyze_market", raise_analysis)
    with pytest.raises(RuntimeError, match="synthetic analysis failure"):
        backtester.run_cycle(SimpleNamespace(), "MGCZ6")
    assert backtester.stats["cycles"] == 1
    assert config.DATA_SOURCE == "mt5"


def test_replayer_does_not_fallback_to_stale_ticks_or_repair_crossed_books(monkeypatch):
    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(config, "BOOKMAP_LEVEL_MAX_AGE", 900.0, raising=False)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 30.0, raising=False)
    stale_state = SimpleNamespace(
        ticks=[{"ts": now - timedelta(seconds=100), "price": 2000.0,
                "volume": 1.0, "side": "BUY", "is_direct": True}],
        bids={2000.0: 10.0}, asks={2000.5: 9.0},
        level_ts_b={2000.0: now - timedelta(seconds=901)},
        level_ts_a={2000.5: now - timedelta(seconds=1)},
        mbo_events=[], last_dt=now)
    provider = backtest.Replayer()
    provider.load_from_state(stale_state, window_seconds=60,
                             now_ts=now.timestamp())
    stale_data = provider.build_market_data(symbol="MGCZ6")
    assert stale_data["tick_data"] == []
    assert stale_data["bid_depth"] == {}
    assert stale_data["data_quality"]["quote"] == "unavailable"
    assert stale_data["quote_age_seconds"] is None

    crossed_state = SimpleNamespace(
        ticks=[], bids={2001.0: 10.0}, asks={2000.5: 9.0},
        level_ts_b={2001.0: now - timedelta(seconds=1)},
        level_ts_a={2000.5: now - timedelta(seconds=1)},
        mbo_events=[], last_dt=now)
    provider.load_from_state(crossed_state, window_seconds=60,
                             now_ts=now.timestamp())
    crossed_data = provider.build_market_data(symbol="MGCZ6")
    assert crossed_data["data_quality"]["quote"] == "crossed_or_locked"
    assert crossed_data["data_quality"]["level2"] == "crossed_book"
    assert crossed_data["bid"] == 0.0 and crossed_data["ask"] == 0.0
    assert crossed_data["l2_diagnostics"]["best_bid"] == 2001.0
    assert crossed_data["l2_diagnostics"]["best_ask"] == 2000.5

    monkeypatch.setattr(config, "BOOKMAP_LEVEL_MAX_AGE", 0.0, raising=False)
    invalid_limit_state = SimpleNamespace(
        ticks=[], bids={2000.0: 10.0}, asks={2000.5: 9.0},
        level_ts_b={2000.0: now}, level_ts_a={2000.5: now},
        mbo_events=[], last_dt=now)
    provider.load_from_state(invalid_limit_state, window_seconds=60,
                             now_ts=now.timestamp())
    invalid_limit_data = provider.build_market_data(symbol="MGCZ6")
    assert invalid_limit_data["data_quality"]["quote"] == "invalid_age_limit"
    assert invalid_limit_data["data_quality"]["level2"] == "stale_or_missing_levels"
    assert invalid_limit_data["bid"] == invalid_limit_data["ask"] == 0.0
