"""
data_providers.py
=================
STEP 1 (data acquisition) — provider layer.

A thin, swappable abstraction over market-data vendors so the rest of the
pipeline never cares WHERE the data comes from. Every provider returns the
exact `market_data` dict that step2_market_analysis.analyze_market() consumes:

    {
      "symbol":       str,
      "price":        float, "bid": float, "ask": float, "volume": float,
      "tick_data":    [{"price","volume","side"}, ...],      # order flow
      "bid_depth":    {price: size},                          # LEVEL 2
      "ask_depth":    {price: size},
      "book_updates": [{"bids": {..}, "asks": {..}}, ...],    # L2 snapshots -> OFI
      "order_events": [{"type","side","price","size","is_market_order",...}, ...],  # LEVEL 3
      "order_book":   {"bids": [(p,s),...], "asks": [(p,s),...]},
      "candles":      {"open","high","low","close","volume"}, # numpy arrays (optional)
      "macro":        {...}, "news": {...},
    }

Providers:
    DemoProvider     - synthetic data, no credentials (default).
    RithmicProvider  - Rithmic R|API (order book = L2, order events = L3).
    DatabentoProvider- Databento MBO (L3) + MBP-10 (L2) + trades (L1).

Switching provider = set DATA_SOURCE in .env (demo | rithmic | databento).
Credentials are read from .env / environment at connect() time, so updating
the Rithmic username/password (or adding a Databento key) is just an edit to
.env followed by a restart.

NOTE ON RITHMIC'S PYTHON BINDINGS
---------------------------------
Rithmic's official R|API is C++/.NET; there is no official pip package. Three
ways to use it from Python:
  1) A community Python wrapper (set RITHMIC_LIB to its module name; the code
     below imports it if present and calls the callbacks you wire up).
  2) The R|Protocol API directly (socket protocol) — implement _start_stream().
  3) A small side-process/bridge that pushes Rithmic callbacks into the
     handle_*() methods below (the recommended robust approach).

The mapping layer (handle_depth / handle_order_event / handle_trade and the
Databento row mappers) is fully implemented and unit-tested, so whatever
transport you choose, you only need to forward raw callbacks into it.

IMPORTANT ROUTING NOTE
----------------------
Rithmic and Databento provide CME *futures* data (GC = 100oz gold, MGC = 10oz).
Spot XAUUSD (OTC) has no central order book, so L2/L3 for gold live on the
futures market. The standard design is: analyse GC/MGC futures order flow and
trade the correlated XAUUSD (or GC) on MT5. Correlation between GC futures and
XAUUSD spot is ~1 (kept aligned by arbitrage), so the signals transfer cleanly.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

import config

logger = logging.getLogger(__name__)


class ProviderNotAvailable(RuntimeError):
    """Raised when a provider cannot run (missing credentials / SDK / network)."""


# ============================================================================= #
# Schema-assembly helpers (shared by all providers)
# ============================================================================= #

def trades_to_candles(trades: List[Dict], timeframe: str = "1min") -> Dict[str, np.ndarray]:
    """Aggregate a list of {timestamp, price, volume} trades into OHLCV candles.

    v4.4.1: rewritten without pandas. The old implementation used
    pd.to_datetime(..., errors="coerce"), which infers ONE timestamp format
    and silently coerces every string that does not match it to NaT. Real
    NT bridge data mixes ISO strings WITH microseconds ("...:34.123000+02:00")
    and WITHOUT ("...:34+02:00" - events stamped exactly on a whole second).
    On live recordings pandas could lock onto the rare variant and drop
    ~99% of trades, collapsing hours of data into 2-3 candles (technicals,
    MTF, order blocks and HTF POC stayed asleep). This version parses every
    element with datetime.fromisoformat (accepts both variants) and buckets
    by wall-clock epoch, so candle history no longer depends on the pandas
    version or the timestamp mix.

    Semantics (unchanged): one candle per `timeframe` bucket that contains
    at least one trade, ordered oldest -> newest; empty minutes are skipped
    (same as the old resample().dropna()).
    """
    if not trades:
        return {}
    m = re.match(r"^\s*(\d+)\s*min", str(timeframe or "1min"), re.IGNORECASE)
    bucket = (max(1, int(m.group(1))) * 60) if m else 60

    rows: List[Tuple[int, float, float]] = []
    for tr in trades:
        ts = tr.get("timestamp")
        dt: Optional[datetime] = None
        if isinstance(ts, datetime):
            dt = ts
        elif isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts.strip())
            except ValueError:
                continue
        if dt is None:
            continue
        if dt.tzinfo is None:          # naive -> assume local (bridge writes local)
            try:
                dt = dt.astimezone()
            except (OSError, ValueError):
                pass
        try:
            price = float(tr.get("price"))
            size = float(tr.get("volume") or 0.0)
        except (TypeError, ValueError):
            continue
        rows.append((dt.timestamp(), price, size))
    if not rows:
        return {}
    rows.sort(key=lambda r: r[0])      # full time order (not just bucket) -> correct OHLC

    o: List[float] = []
    h: List[float] = []
    l: List[float] = []
    c: List[float] = []
    v: List[float] = []
    cur: Optional[int] = None
    for epoch, price, size in rows:
        key = int(epoch // bucket)
        if key != cur:
            o.append(price)
            h.append(price)
            l.append(price)
            c.append(price)
            v.append(0.0)
            cur = key
        if price > h[-1]:
            h[-1] = price
        if price < l[-1]:
            l[-1] = price
        c[-1] = price
        v[-1] += size
    return {
        "open": np.asarray(o, dtype=float),
        "high": np.asarray(h, dtype=float),
        "low": np.asarray(l, dtype=float),
        "close": np.asarray(c, dtype=float),
        "volume": np.asarray(v, dtype=float),
    }


class BaseProvider:
    """Common plumbing shared by all providers."""

    name = "base"

    def __init__(self):
        self.connected = False
        self.bid_depth: Dict[float, float] = {}
        self.ask_depth: Dict[float, float] = {}
        self.book_updates: List[Dict] = []
        self.order_events: List[Dict] = []
        self.order_book: Dict[str, List] = {"bids": [], "asks": []}
        self.tick_data: List[Dict] = []
        self._trades: List[Dict] = []
        self.last_data_ts: Optional[datetime] = None   # newest feed event
        self.last_depth_ts: Optional[datetime] = None  # newest quote/depth event
        self.last_trade_ts: Optional[datetime] = None
        self.last_order_event_ts: Optional[datetime] = None

    # -- heartbeat -------------------------------------------------------------
    def _touch(self, ts: Optional[datetime] = None) -> None:
        candidate = ts or datetime.now(timezone.utc)
        if candidate.tzinfo is None:
            candidate = candidate.replace(tzinfo=timezone.utc)
        if self.last_data_ts is None:
            self.last_data_ts = candidate
            return
        previous = self.last_data_ts
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
        if candidate > previous:
            self.last_data_ts = candidate

    def data_age_seconds(self, now: Optional[datetime] = None) -> float:
        """Signed seconds since the newest data event (0.0 if never received).

        A negative age is deliberately retained so future-dated vendor records
        can be rejected instead of appearing to have zero latency.
        """
        if self.last_data_ts is None:
            return 0.0
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        previous = self.last_data_ts
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
        return (now - previous).total_seconds()

    def has_fresh_data(self, max_age: Optional[float] = None) -> bool:
        if self.last_data_ts is None:
            return False
        raw_max_age = max_age if max_age is not None else config.STALE_DATA_SECONDS
        if isinstance(raw_max_age, bool):
            return False
        try:
            age_limit = float(raw_max_age)
        except (TypeError, ValueError, OverflowError):
            return False
        if not math.isfinite(age_limit) or age_limit <= 0:
            return False
        age = self.data_age_seconds()
        return math.isfinite(age) and 0.0 <= age <= age_limit

    # -- state maintenance -----------------------------------------------------
    def reset(self) -> None:
        self.bid_depth, self.ask_depth = {}, {}
        self.book_updates, self.order_events = [], []
        self.order_book = {"bids": [], "asks": []}
        self.tick_data, self._trades = [], []
        self.last_data_ts = None
        self.last_depth_ts = None
        self.last_trade_ts = None
        self.last_order_event_ts = None

    # -- LEVEL 2: depth update -------------------------------------------------
    def handle_depth(self, bids: Any, asks: Any, ts: Optional[datetime] = None) -> None:
        """Ingest one Level-2 depth snapshot.

        Accepts dicts {price: size} or lists of (price, size) tuples.
        Also tracks the order book for the L3 analyzer.
        """
        bids = self._normalize(bids)
        asks = self._normalize(asks)
        event_ts = ts or datetime.now(timezone.utc)
        if event_ts.tzinfo is None:
            event_ts = event_ts.replace(tzinfo=timezone.utc)
        self.bid_depth = bids
        self.ask_depth = asks
        self.book_updates.append({"bids": bids, "asks": asks,
                                  "timestamp": event_ts.isoformat()})
        self.order_book = {
            "bids": [(p, s) for p, s in sorted(bids.items(), reverse=True)],
            "asks": [(p, s) for p, s in sorted(asks.items())],
        }
        if self.last_depth_ts is None or event_ts > self.last_depth_ts:
            self.last_depth_ts = event_ts
        self._touch(event_ts)

    # -- LEVEL 3: order event --------------------------------------------------
    def handle_order_event(self, event: Dict,
                           ts: Optional[datetime] = None) -> None:
        """Ingest one timestamped order event ({type, side, price, size})."""
        stored = dict(event)
        event_ts = ts
        has_source_timestamp = event_ts is not None
        if event_ts is None and stored.get("timestamp"):
            try:
                event_ts = datetime.fromisoformat(
                    str(stored["timestamp"]).replace("Z", "+00:00"))
                has_source_timestamp = True
            except (TypeError, ValueError, OverflowError):
                event_ts = None
        event_ts = event_ts or datetime.now(timezone.utc)
        if event_ts.tzinfo is None:
            event_ts = event_ts.replace(tzinfo=timezone.utc)
        stored.setdefault("timestamp", event_ts.isoformat())
        stored.setdefault("timestamp_is_source", has_source_timestamp)
        stored.setdefault("timestamp_source", "provider_event_time"
                          if has_source_timestamp else "local_receive_time")
        self.order_events.append(stored)
        if (self.last_order_event_ts is None or
                event_ts > self.last_order_event_ts):
            self.last_order_event_ts = event_ts
        self._touch(event_ts)

    # -- L1: trade -------------------------------------------------------------
    def handle_trade(self, price: float, volume: float, side: str,
                     ts: Optional[datetime] = None,
                     is_direct: bool = False) -> None:
        """Ingest a trade and preserve whether its aggressor side is explicit.

        An inferred/tick-rule side remains useful to general order flow, but is
        not mislabeled as an observed aggressive market-order print in L3.
        """
        has_source_timestamp = ts is not None
        event_ts = ts or datetime.now(timezone.utc)
        if event_ts.tzinfo is None:
            event_ts = event_ts.replace(tzinfo=timezone.utc)
        # Local receive time is useful for general feed telemetry, but it must
        # never upgrade an untimestamped vendor print into direct L3 evidence.
        direct = is_direct is True and has_source_timestamp
        self.tick_data.append({"price": float(price), "volume": float(volume),
                               "side": side, "is_direct": direct,
                               "timestamp": event_ts.isoformat()})
        self._trades.append({"timestamp": event_ts.isoformat(),
                             "price": float(price), "volume": float(volume),
                             "is_direct": direct})
        if self.last_trade_ts is None or event_ts > self.last_trade_ts:
            self.last_trade_ts = event_ts
        self._touch(event_ts)

    # -- assembly --------------------------------------------------------------
    def build_market_data(self, symbol: str, candles: Optional[Dict] = None,
                          macro: Optional[Dict] = None,
                          news: Optional[Dict] = None) -> Dict[str, Any]:
        """Assemble an analyzer payload with explicit quote/feed health data.

        Tags DATA-side versus TRADE-side identity and never represents a missing
        or crossed quote as a zero-spread quote.
        """
        from markets import normalize_symbol
        bid, ask = self._best_prices()
        quote_status = "unavailable"
        spread_pct = None
        quote_age = None
        quote_timestamp = None
        quote_mid = 0.0
        now = datetime.now(timezone.utc)
        if (math.isfinite(float(bid)) and math.isfinite(float(ask))
                and bid > 0 and ask > 0):
            if ask <= bid:
                quote_status = "crossed_or_locked"
            else:
                quote_mid = (bid + ask) / 2.0
                candidate_spread = (ask - bid) / quote_mid * 100.0
                if math.isfinite(candidate_spread) and candidate_spread >= 0:
                    spread_pct = candidate_spread
                    if self.last_depth_ts is None:
                        quote_status = "timestamp_unknown"
                    else:
                        quote_ts = self.last_depth_ts
                        if quote_ts.tzinfo is None:
                            quote_ts = quote_ts.replace(tzinfo=timezone.utc)
                        quote_age = (now - quote_ts).total_seconds()
                        if not math.isfinite(quote_age) or quote_age < 0:
                            quote_status = "future_timestamp"
                            quote_age = None
                        else:
                            try:
                                max_quote_age = float(getattr(
                                    config, "STALE_DATA_SECONDS", 300.0))
                                if not math.isfinite(max_quote_age) or max_quote_age <= 0:
                                    raise ValueError("invalid quote-age limit")
                            except (TypeError, ValueError, OverflowError):
                                max_quote_age = None
                            if max_quote_age is None:
                                quote_status = "invalid_age_limit"
                            elif quote_age > max_quote_age:
                                quote_status = "stale"
                            else:
                                quote_status = "valid"
                                quote_timestamp = quote_ts.isoformat()
                else:
                    quote_status = "invalid_spread"
        price = quote_mid if quote_mid > 0 else (bid or ask or 0.0)
        if candles is None:
            candles = trades_to_candles(self._trades)
        trade_symbol = os.environ.get("MT5_SYMBOL", config.MT5_SYMBOL)
        has_data = bool(self.tick_data or self.order_events or self.book_updates
                        or self.bid_depth or self.ask_depth)
        l2_status = ("unavailable" if not (bid > 0 and ask > 0) else
                     "crossed" if ask <= bid else
                     "quote_unvalidated" if quote_status != "valid" else
                     "available")
        valid_event_sides = {"BUY", "SELL", "BID", "ASK"}
        direct_trades = any(tick.get("is_direct") is True and
                            str(tick.get("side", "")).upper()
                            in {"BUY", "SELL", "B", "S"}
                            for tick in self.tick_data)
        mbo_market = any(event.get("is_market_order") is True and
                         str(event.get("side", "")).upper() in valid_event_sides
                         for event in self.order_events)
        passive_events = any(
            str(event.get("type", "")).upper()
            in {"NEW", "ADD", "OPEN", "BID_NEW", "ASK_NEW"} and
            str(event.get("side", "")).upper() in valid_event_sides
            for event in self.order_events)
        if mbo_market and passive_events:
            l3_aggr_limit = "partial_mbo_tags"
        elif mbo_market:
            l3_aggr_limit = "market_order_events_only"
        elif passive_events:
            l3_aggr_limit = "passive_order_events_only"
        else:
            l3_aggr_limit = "not_emitted"
        now_ts = now
        def _age_for(event_ts):
            if event_ts is None:
                return None
            if event_ts.tzinfo is None:
                event_ts = event_ts.replace(tzinfo=timezone.utc)
            age = (now_ts - event_ts).total_seconds()
            return age if math.isfinite(age) else None

        trade_age = _age_for(self.last_trade_ts)
        event_age = _age_for(self.last_order_event_ts)
        depth_age = _age_for(self.last_depth_ts)
        return {
            "symbol": symbol,
            "price": price,
            "bid": bid if quote_status == "valid" else 0.0,
            "ask": ask if quote_status == "valid" else 0.0,
            "spread_pct": spread_pct,
            "quote_age_seconds": quote_age if quote_status not in {
                "unavailable", "crossed_or_locked", "future_timestamp",
                "timestamp_unknown"} else None,
            "quote_timestamp": quote_timestamp,
            "depth_age_seconds": depth_age,
            "depth_timestamp": (self.last_depth_ts.isoformat()
                                if self.last_depth_ts else None),
            "last_trade_age_seconds": trade_age,
            "last_trade_timestamp": (self.last_trade_ts.isoformat()
                                      if self.last_trade_ts else None),
            "last_order_event_age_seconds": event_age,
            "last_order_event_timestamp": (
                self.last_order_event_ts.isoformat()
                if self.last_order_event_ts is not None else None),
            "volume": float(sum(t["volume"] for t in self.tick_data)),
            "tick_data": list(self.tick_data),
            "bid_depth": dict(self.bid_depth),
            "ask_depth": dict(self.ask_depth),
            "book_updates": list(self.book_updates),
            "order_events": list(self.order_events),
            "order_book": {"bids": list(self.order_book["bids"]),
                           "asks": list(self.order_book["asks"])},
            "candles": candles or {},
            "macro": macro or {},
            "news": news or {"fetch_calendar": True},
            # market identity (futures data side vs CFD trade side)
            "data_symbol": symbol,
            "data_market": normalize_symbol(symbol),
            "trade_symbol": trade_symbol,
            "trade_market": normalize_symbol(trade_symbol),
            # feed health (no-data / weekend resilience)
            "has_data": has_data,
            "last_data_age_seconds": (self.data_age_seconds(now)
                                      if self.last_data_ts is not None else None),
            "last_data_timestamp": (self.last_data_ts.isoformat()
                                     if self.last_data_ts else None),
            "data_quality": {
                "quote": quote_status,
                "level2": l2_status,
                "level3": "available" if self.order_events else "unavailable",
                "level3_events": "partial_window" if self.order_events else "unavailable",
                "l3_aggressive_flow": ("partial_mbo_market_order_events" if mbo_market
                                        else "direct_trade_prints_only" if direct_trades
                                        else "unavailable"),
                "l3_aggr_limit": l3_aggr_limit,
                "trade_prints": "available" if self._trades else "unavailable",
                "order_flow": ("direct_trade_side" if direct_trades else
                               "provider_ticks" if self._trades else "unavailable"),
            },
        }

    def _best_prices(self) -> Tuple[float, float]:
        bid = max(self.bid_depth) if self.bid_depth else 0.0
        ask = min(self.ask_depth) if self.ask_depth else 0.0
        return bid, ask

    @staticmethod
    def _normalize(levels: Any) -> Dict[float, float]:
        """Normalize one depth side, rejecting malformed snapshots atomically."""
        if levels is None:
            return {}
        items = levels.items() if isinstance(levels, dict) else levels
        normalized = {}
        try:
            for item in items:
                if not isinstance(item, (list, tuple)) and not hasattr(item, "__iter__"):
                    return {}
                try:
                    price, size = item
                except (TypeError, ValueError):
                    return {}
                if isinstance(price, (bool, np.bool_)) or isinstance(size, (bool, np.bool_)):
                    return {}
                price, size = float(price), float(size)
                if not math.isfinite(price) or not math.isfinite(size):
                    return {}
                if price > 0 and size > 0:
                    normalized[price] = size
        except (TypeError, ValueError, OverflowError):
            return {}
        return normalized

    # -- lifecycle -------------------------------------------------------------
    def connect(self) -> None:
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def acquire(self, symbol: str, **kwargs) -> Dict[str, Any]:
        raise NotImplementedError


# ============================================================================= #
# DEMO provider (default — no credentials needed)
# ============================================================================= #

class DemoProvider(BaseProvider):
    """Synthetic data so the whole pipeline runs without any vendor."""

    name = "demo"

    def acquire(self, symbol: str = "", **kwargs) -> Dict[str, Any]:
        from step2_market_analysis import _synthetic_market_data
        from markets import normalize_symbol
        symbol = symbol or config.DATA_SYMBOL
        data = _synthetic_market_data()
        data["symbol"] = symbol
        # tag market identity so the demo shows the same DATA->TRADE mapping
        data["data_symbol"] = symbol
        data["data_market"] = normalize_symbol(symbol)
        data["trade_symbol"] = config.MT5_SYMBOL
        data["trade_market"] = normalize_symbol(config.MT5_SYMBOL)
        logger.info("DEMO provider: %d ticks, %d candles for %s",
                    len(data["tick_data"]), len(data["candles"].get("close", [])),
                    symbol)
        return data


# ============================================================================= #
# RITHMIC provider  (R|API)
# ============================================================================= #

class RithmicProvider(BaseProvider):
    """Rithmic R|API adapter.

    Credentials are read fresh from .env on connect(), so rotating the demo
    username/password is a one-line .env edit (+ restart). The mapping methods
    below (handle_depth / handle_order_event / handle_trade) accept the exact
    shapes the various Rithmic wrappers emit, so you only wire callbacks once.
    """

    name = "rithmic"

    # -- credentials -----------------------------------------------------------
    def credentials(self) -> Dict[str, str]:
        config.reload_env()  # pick up freshly edited .env
        return {
            "username": os.environ.get("RITHMIC_USERNAME", config.RITHMIC_USERNAME),
            "password": os.environ.get("RITHMIC_PASSWORD", config.RITHMIC_PASSWORD),
            "system": os.environ.get("RITHMIC_SYSTEM", config.RITHMIC_SYSTEM),
            "app_name": os.environ.get("RITHMIC_APP_NAME", config.RITHMIC_APP_NAME),
            "app_version": os.environ.get("RITHMIC_APP_VERSION",
                                          config.RITHMIC_APP_VERSION),
            "url": os.environ.get("RITHMIC_URL", config.RITHMIC_URL),
            "lib": os.environ.get("RITHMIC_LIB", config.RITHMIC_LIB),
            "symbol": os.environ.get("RITHMIC_SYMBOL", config.RITHMIC_SYMBOL),
            "exchange": os.environ.get("RITHMIC_EXCHANGE",
                                       config.RITHMIC_EXCHANGE),
        }

    # -- transport -------------------------------------------------------------
    def connect(self) -> None:
        """Authenticate with Rithmic and subscribe to gold order-book data.

        Uses the community `async-rithmic` package (recommended, pip-installable).
        If you use a different wrapper, set RITHMIC_LIB to its module name and
        adapt `_start_async_bridge` accordingly.
        """
        creds = self.credentials()
        if not creds["username"] or not creds["password"]:
            raise ProviderNotAvailable(
                "Rithmic username/password not set. Add RITHMIC_USERNAME and "
                "RITHMIC_PASSWORD to .env (use your Rithmic demo credentials).")

        lib_name = (creds.get("lib") or "async_rithmic").lower()
        if lib_name != "async_rithmic":
            raise ProviderNotAvailable(
                f"Unknown Rithmic transport '{lib_name}'. This build supports "
                "'async_rithmic' (pip install async-rithmic). If you use a "
                "different wrapper, wire its callbacks into "
                "handle_depth()/handle_order_event()/handle_trade().")

        try:
            import async_rithmic  # noqa: F401
        except ImportError as exc:
            raise ProviderNotAvailable(
                "The 'async-rithmic' package is not installed. Run: "
                "pip install async-rithmic   (then verify with: "
                "python rithmic_test.py)") from exc

        # start the streaming bridge in a background thread
        from rithmic_bridge import AsyncRithmicBridge
        self._bridge = AsyncRithmicBridge(self, creds)
        self._bridge.start()
        if not self._bridge.wait_ready(timeout=20.0):
            err = getattr(self._bridge, "_error", None) or "timed out"
            raise ProviderNotAvailable(
                f"Rithmic bridge did not become ready: {err}")
        self.connected = True
        logger.info("Rithmic provider connected as %s (system=%s)",
                    creds["username"], creds["system"])

    def disconnect(self) -> None:
        bridge = getattr(self, "_bridge", None)
        if bridge is not None:
            try:
                bridge.stop()
            except Exception:
                pass
        self.connected = False

    # -- acquisition -----------------------------------------------------------
    def acquire(self, symbol: str = "", **kwargs) -> Dict[str, Any]:
        symbol = symbol or config.DATA_SYMBOL or os.environ.get(
            "RITHMIC_SYMBOL", config.RITHMIC_SYMBOL)
        if not self.connected:
            self.connect()
        # give the stream a moment to buffer data on the first call
        import time as _time
        waited = 0.0
        while waited < 5.0 and not (self.tick_data or self.bid_depth
                                    or self.order_events):
            _time.sleep(0.25)
            waited += 0.25
        data = self.build_market_data(symbol=symbol)
        logger.info("Rithmic provider: %d ticks, %d order events, %d depth "
                    "updates for %s", len(self.tick_data), len(self.order_events),
                    len(self.book_updates), symbol)
        return data


# ============================================================================= #
# DATABENTO provider  (MBO + MBP-10 + trades)
# ============================================================================= #

def databento_mbo_row_to_event(row: Any) -> Optional[Dict]:
    """Map one Databento MBO row to a Step-2 order event.

    MBO actions: 'A' add, 'C' cancel, 'M' modify, 'R' reduce, 'F' fill,
                 'T' trade. Side: 'B' bid/buy, 'A' ask/sell.
    A 'T' (trade) is the aggressive side -> market fill (L3 aggressor).
    """
    action = str(row.get("action", "")).upper()
    side_code = str(row.get("side", "")).upper()
    side = {"B": "BUY", "A": "SELL"}.get(side_code)
    if side is None:
        return None
    try:
        price = float(row.get("price", 0) or 0)
        size = float(row.get("size", 0) or 0)
    except (TypeError, ValueError):
        return None
    base = {"side": side, "price": price, "size": size,
            "is_market_order": False}
    if action == "A":
        return {**base, "type": "NEW"}
    if action in ("C", "R"):
        return {**base, "type": "CANCEL"}
    if action == "M":
        return {**base, "type": "MODIFY", "old_size": 0.0}
    if action == "T":
        # trade event: aggressor side (matches Step 2's market-order convention)
        return {**base, "type": "FILL", "is_market_order": True}
    if action == "F":
        return {**base, "type": "FILL"}  # resting order fill (maker side)
    return None


def databento_mbo_row_to_tick(row: Any) -> Optional[Dict]:
    """Map a Databento MBO 'T' row to a Step-2 tick (aggressor = side)."""
    if str(row.get("action", "")).upper() != "T":
        return None
    side_code = str(row.get("side", "")).upper()
    side = {"B": "BUY", "A": "SELL"}.get(side_code)
    if side is None:
        return None
    try:
        return {"price": float(row["price"]), "volume": float(row["size"]),
                "side": side, "is_direct": True}
    except (TypeError, ValueError, KeyError):
        return None


def databento_mbp10_row_to_depths(row: Any, levels: int = 10) -> Tuple[Dict, Dict]:
    """Map one Databento MBP-10 row to (bids, asks) {price: size} dicts."""
    bids: Dict[float, float] = {}
    asks: Dict[float, float] = {}
    for i in range(levels):
        bp = row.get(f"bid_px_{i:02d}")
        bs = row.get(f"bid_sz_{i:02d}")
        ap = row.get(f"ask_px_{i:02d}")
        az = row.get(f"ask_sz_{i:02d}")
        try:
            if bp is not None and bs is not None and not np.isnan(float(bs)) and float(bs) > 0:
                bids[float(bp)] = float(bs)
            if ap is not None and az is not None and not np.isnan(float(az)) and float(az) > 0:
                asks[float(ap)] = float(az)
        except (TypeError, ValueError):
            continue
    return bids, asks


class DatabentoProvider(BaseProvider):
    """Databento MBO + MBP-10 + trades -> Step-2 schema.

    Uses the official `databento` Python SDK (pip install databento). Settings
    in .env: DATABENTO_API_KEY, DATABENTO_DATASET, DATABENTO_SCHEMA, and
    DATABENTO_SYMBOL. Switching from Rithmic to Databento = set DATA_SOURCE=
    databento + put a DATABENTO_API_KEY in .env.
    """

    name = "databento"

    def credentials(self) -> Dict[str, str]:
        config.reload_env()
        return {
            "api_key": os.environ.get("DATABENTO_API_KEY", config.DATABENTO_API_KEY),
            "dataset": os.environ.get("DATABENTO_DATASET", config.DATABENTO_DATASET),
            "schema": os.environ.get("DATABENTO_SCHEMA", config.DATABENTO_SCHEMA),
            "symbol": os.environ.get("DATABENTO_SYMBOL", config.DATABENTO_SYMBOL),
        }

    def connect(self) -> None:
        creds = self.credentials()
        if not creds["api_key"]:
            raise ProviderNotAvailable(
                "Databento API key not set. Add DATABENTO_API_KEY to .env "
                "(get one free at databento.com).")
        try:
            import databento as db
        except ImportError as exc:
            raise ProviderNotAvailable(
                "The 'databento' package is not installed. Run: "
                "pip install databento") from exc
        self._db = db
        self._creds = creds
        self.connected = True
        logger.info("Databento provider ready (dataset=%s, schema=%s, symbol=%s)",
                    creds["dataset"], creds["schema"], creds["symbol"])

    def _ingest_mbo(self, data) -> None:
        """Map an MBO result set into order events + ticks + candles."""
        for row in data:
            event_ts = _ts_from_row(row)
            if event_ts is None:
                continue
            evt = databento_mbo_row_to_event(row)
            if evt:
                self.handle_order_event(evt, ts=event_ts)
            tick = databento_mbo_row_to_tick(row)
            if tick:
                self.handle_trade(tick["price"], tick["volume"], tick["side"],
                                  ts=event_ts,
                                  is_direct=(event_ts is not None and
                                             tick.get("is_direct") is True))

    def _ingest_mbp10(self, data) -> None:
        """Map an MBP-10 result set into depth snapshots."""
        for row in data:
            event_ts = _ts_from_row(row)
            if event_ts is None:
                continue
            bids, asks = databento_mbp10_row_to_depths(row)
            if bids or asks:
                self.handle_depth(bids, asks, ts=event_ts)

    def _ingest_trades(self, data) -> None:
        """Map a trades result set into tick_data + candles."""
        for row in data:
            try:
                price = float(row["price"])
                size = float(row["size"])
                # Side B/A is explicit. Unknown aggressor side is not guessed.
                side = {"B": "BUY", "A": "SELL"}.get(
                    str(row.get("side", "")).upper())
                if side is None:
                    continue
            except (TypeError, ValueError):
                continue
            event_ts = _ts_from_row(row)
            if event_ts is None:
                continue
            self.handle_trade(price, size, side, ts=event_ts, is_direct=True)

    def acquire(self, symbol: str = "", **kwargs) -> Dict[str, Any]:
        if not self.connected:
            self.connect()
        creds = self._creds
        db = self._db
        symbol = symbol or creds["symbol"]

        # --- historical MBO (L3) ---------------------------------------------
        if creds["schema"] == "mbo":
            hist = db.Historical(key=creds["api_key"])
            try:
                mbo = hist.timeseries.get_range(
                    dataset=creds["dataset"], schema="mbo",
                    stype_in="raw_symbol", symbols=[symbol],
                    start=kwargs.get("start"), end=kwargs.get("end"))
                self._ingest_mbo(mbo)
            except Exception as exc:  # schema/dataset/parents issues
                logger.warning("Databento MBO fetch failed (%s); continuing "
                               "with whatever is buffered.", exc)
        elif creds["schema"] == "mbp-10":
            hist = db.Historical(key=creds["api_key"])
            try:
                mbp = hist.timeseries.get_range(
                    dataset=creds["dataset"], schema="mbp-10",
                    stype_in="raw_symbol", symbols=[symbol],
                    start=kwargs.get("start"), end=kwargs.get("end"))
                self._ingest_mbp10(mbp)
            except Exception as exc:
                logger.warning("Databento MBP-10 fetch failed (%s).", exc)
        else:  # trades
            hist = db.Historical(key=creds["api_key"])
            try:
                tr = hist.timeseries.get_range(
                    dataset=creds["dataset"], schema="trades",
                    stype_in="raw_symbol", symbols=[symbol],
                    start=kwargs.get("start"), end=kwargs.get("end"))
                self._ingest_trades(tr)
            except Exception as exc:
                logger.warning("Databento trades fetch failed (%s).", exc)

        data = self.build_market_data(symbol=symbol)
        logger.info("Databento provider: %d ticks, %d order events, %d depth "
                    "updates for %s", len(self.tick_data), len(self.order_events),
                    len(self.book_updates), symbol)
        return data


def _ts_from_row(row: Any) -> Optional[datetime]:
    """Best-effort timestamp extraction from vendor rows (ns/ms/s/ISO)."""
    if not hasattr(row, "get"):
        return None
    ts = row.get("ts_event") or row.get("ts_recv") or row.get("timestamp")
    if ts is None:
        return None
    if isinstance(ts, (int, float, np.integer, np.floating)):
        try:
            value = float(ts)
            if not math.isfinite(value) or value <= 0:
                return None
            if value >= 1e17:       # Unix nanoseconds (Databento)
                value /= 1e9
            elif value >= 1e14:     # Unix microseconds
                value /= 1e6
            elif value >= 1e11:     # Unix milliseconds (MT5-like adapters)
                value /= 1e3
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError, OverflowError):
        return None


# ============================================================================= #
# MT5 provider (FREE real data from your own MetaTrader 5 demo account)
# ============================================================================= #

# MT5 tick flag bits (aggressor side of the trade)
TICK_FLAG_BUY = 32
TICK_FLAG_SELL = 64


def mt5_book_to_depths(book: Any, mt5_module: Any) -> Tuple[Dict[float, float], Dict[float, float]]:
    """Convert MT5 BookInfo entries to (bids, asks).

    MQL5/MT5 uses BOOK_TYPE_SELL=1 for sell/ask entries and
    BOOK_TYPE_BUY=2 for buy/bid entries. A type-0 bid fallback is retained for
    simple test/fallback feeds, but real type-1/type-2 values are never
    collapsed onto one side.
    """
    bids: Dict[float, float] = {}
    asks: Dict[float, float] = {}
    sell_type = int(getattr(mt5_module, "BOOK_TYPE_SELL", 1))
    buy_type = int(getattr(mt5_module, "BOOK_TYPE_BUY", 2))
    for entry in book or []:
        try:
            px = float(entry.price)
            vol = float(entry.volume)
            entry_type = int(entry.type)
            if vol <= 0:
                continue
            if entry_type == buy_type or (entry_type == 0 and buy_type != 0):
                bids[px] = vol
            elif entry_type == sell_type:
                asks[px] = vol
        except (TypeError, ValueError, AttributeError):
            continue
    return bids, asks


class MT5Provider(BaseProvider):
    """Reads live data straight from a running MetaTrader 5 terminal.

    This is the FREE, no-card option: a demo MT5 account gives you
      - Level 2 depth     via market_book_get()
      - ticks (with buy/sell aggressor from tick flags) via copy_ticks_from()
      - OHLCV candles     via copy_rates_from_pos()

    Limitation (honest): CFD brokers publish an aggregated order book (L2) but
    NOT individual order events (L3). The L3 analyzer therefore stays empty;
    the rest of the pipeline (CVD, footprint, L2 depth, OFI, technicals) works
    fully from ticks + depth.

    Requirements: Windows + MT5 terminal running + a demo account logged in.
    """

    name = "mt5"

    def __init__(self):
        super().__init__()
        self._mt5 = None
        self._symbol = config.MT5_SYMBOL

    def credentials(self) -> Dict[str, str]:
        config.reload_env()
        return {
            "symbol": os.environ.get("MT5_SYMBOL", config.MT5_SYMBOL),
            "login": config.MT5_LOGIN,
            "password": config.MT5_PASSWORD,
            "server": config.MT5_SERVER,
            "terminal_path": config.MT5_TERMINAL_PATH,
        }

    def connect(self) -> None:
        try:
            import MetaTrader5 as mt5
        except ImportError as exc:
            raise ProviderNotAvailable(
                "The 'MetaTrader5' package is not installed. It only works on "
                "Windows with a running MT5 terminal. Install it with: "
                "pip install MetaTrader5") from exc

        creds = self.credentials()
        init_kwargs = {}
        if creds["terminal_path"]:
            # Explicit path is important when several broker terminals are
            # installed. Without it, MT5 uses the running/default terminal.
            init_kwargs["path"] = creds["terminal_path"]
        if creds["login"]:
            init_kwargs.update(login=creds["login"],
                               password=creds["password"],
                               server=creds["server"])
        ok = mt5.initialize(**init_kwargs)
        if not ok:
            raise ProviderNotAvailable(
                f"MT5 initialize() failed: {mt5.last_error()}. Is the MT5 "
                "terminal running and logged in?")

        self._mt5 = mt5
        self._symbol = creds["symbol"]
        if not mt5.symbol_select(self._symbol, True):
            err = getattr(mt5, "last_error", lambda: "unknown error")()
            try:
                mt5.shutdown()
            except Exception:
                pass
            raise ProviderNotAvailable(
                f"MT5 symbol '{self._symbol}' could not be selected: {err}. "
                "Confirm the exact broker symbol in Market Watch.")
        try:
            mt5.market_book_add(self._symbol)   # subscribe to depth
        except Exception:
            pass
        self.connected = True
        logger.info("MT5 provider connected (symbol=%s, %s)",
                    self._symbol, "demo/live via terminal")

    def disconnect(self) -> None:
        mt5 = self._mt5
        if mt5 is not None:
            try:
                mt5.market_book_release(self._symbol)
                mt5.shutdown()
            except Exception:
                pass
        self.connected = False

    def acquire(self, symbol: str = "", **kwargs) -> Dict[str, Any]:
        if not self.connected:
            self.connect()
        mt5 = self._mt5
        symbol = symbol or self._symbol

        # ---- 1) current bid/ask -----------------------------------------
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            logger.warning("MT5: no tick for %s (is the symbol in Market Watch?)",
                           symbol)

        # ---- 2) Level 2 depth (sampled twice so OFI/absorption have a delta)
        for _ in range(2):
            book = mt5.market_book_get(symbol)
            if book:
                bids, asks = mt5_book_to_depths(book, mt5)
                if bids or asks:
                    self.handle_depth(bids, asks)
            time.sleep(0.5)

        # ---- 3) ticks (last ~30 min, with aggressor side) ---------------
        real_trade_ticks = 0
        try:
            since = datetime.now(timezone.utc).replace(tzinfo=None) - \
                timedelta(minutes=30)
            ticks = mt5.copy_ticks_from(symbol, since, 100000, mt5.COPY_TICKS_ALL)
            if ticks is not None and len(ticks):
                for t in ticks:
                    try:
                        flags = int(t["flags"])
                        last = float(t["last"])
                        vol = float(t["volume_real"]) if t["volume_real"] else \
                            float(t["volume"])
                        if last <= 0 or vol <= 0:
                            continue
                        if flags & TICK_FLAG_BUY:
                            side = "BUY"
                        elif flags & TICK_FLAG_SELL:
                            side = "SELL"
                        else:
                            side = ""              # tick rule will infer
                        tick_ts = None
                        try:
                            _time_msc = int(t["time_msc"])
                            if _time_msc > 0:
                                tick_ts = datetime.fromtimestamp(
                                    _time_msc / 1000.0, tz=timezone.utc)
                        except (KeyError, TypeError, ValueError, OverflowError, OSError):
                            try:
                                _time_s = int(t["time"])
                                if _time_s > 0:
                                    tick_ts = datetime.fromtimestamp(
                                        _time_s, tz=timezone.utc)
                            except (KeyError, TypeError, ValueError, OverflowError, OSError):
                                pass
                        self.handle_trade(last, vol, side, ts=tick_ts,
                                          is_direct=bool(side) and tick_ts is not None)
                        real_trade_ticks += 1
                    except (KeyError, TypeError, ValueError):
                        continue
        except Exception as exc:
            logger.warning("MT5: tick copy failed (%s)", exc)

        # ---- 4) OHLCV candles -------------------------------------------
        candles: Dict[str, Any] = {}
        candles_h1: Dict[str, Any] = {}
        candles_m15: Dict[str, Any] = {}
        candles_m5: Dict[str, Any] = {}
        try:
            tf_map = {
                "M1": mt5.TIMEFRAME_M1, "M5": mt5.TIMEFRAME_M5,
                "M15": mt5.TIMEFRAME_M15, "M30": mt5.TIMEFRAME_M30,
                "H1": mt5.TIMEFRAME_H1, "H4": mt5.TIMEFRAME_H4,
                "D1": mt5.TIMEFRAME_D1,
            }
            tf = tf_map.get(config.TIMEFRAME, mt5.TIMEFRAME_M1)
            rates = mt5.copy_rates_from_pos(symbol, tf, 0, 500)
            if rates is not None and len(rates):
                candles = {
                    "open": np.asarray([r["open"] for r in rates], dtype=float),
                    "high": np.asarray([r["high"] for r in rates], dtype=float),
                    "low": np.asarray([r["low"] for r in rates], dtype=float),
                    "close": np.asarray([r["close"] for r in rates], dtype=float),
                    "volume": np.asarray(
                        [r["real_volume"] if r["real_volume"] else r["tick_volume"]
                         for r in rates], dtype=float),
                }

            # ---- higher-timeframe candles for the multi-TF confirmation ----
            # H1 -> M15 -> M5 must all agree with the M1 signal (configurable).
            if getattr(config, "CONFIRM_ENABLED", True):
                confirm_tfs = [t.strip().upper()
                               for t in str(getattr(config, "CONFIRM_TIMEFRAMES",
                                                     "H1,M15,M5")).split(",")
                               if t.strip()]
                per_tf = {"M5": (candles_m5, 100), "M15": (candles_m15, 60),
                          "H1": (candles_h1, 60)}
                for tf_name in confirm_tfs:
                    if tf_name == config.TIMEFRAME.upper():
                        continue
                    mt5_tf = tf_map.get(tf_name)
                    if mt5_tf is None:
                        continue
                    try:
                        nbars = per_tf.get(tf_name, (None, 60))[1]
                        rr = mt5.copy_rates_from_pos(symbol, mt5_tf, 0, nbars)
                        if rr is not None and len(rr):
                            per_tf[tf_name][0]["close"] = \
                                np.asarray([r["close"] for r in rr], dtype=float)
                    except Exception:
                        continue
        except Exception as exc:
            logger.warning("MT5: candle copy failed (%s)", exc)

        # ---- 5) live spread (% of the current MT5 quote) -------------------
        # Missing/locked/crossed quotes are unknown, never a synthetic 0.000%.
        spread_pct = None
        quote_status = "unavailable"
        quote_bid = quote_ask = 0.0
        if tick is not None:
            try:
                quote_bid = float(getattr(tick, "bid", 0) or 0)
                quote_ask = float(getattr(tick, "ask", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                quote_bid = quote_ask = 0.0
            if (math.isfinite(quote_bid) and math.isfinite(quote_ask)
                    and quote_bid > 0 and quote_ask > quote_bid):
                mid = (quote_bid + quote_ask) / 2.0
                candidate = (quote_ask - quote_bid) / mid * 100.0
                if math.isfinite(candidate) and candidate >= 0:
                    spread_pct = candidate
                    quote_status = "valid"
                else:
                    quote_status = "invalid"
            elif quote_bid > 0 and quote_ask > 0:
                quote_status = "crossed_or_locked"

        # ---- 6) CFD feeds have no trade prints (last=0, volume=0). --------
        # If no real ticks arrived, APPROXIMATE order flow from candle
        # direction (close >= open -> buy bar, else sell bar) so CVD / delta /
        # footprint still have meaningful data. This is a known CFD compromise.
        if not self.tick_data and candles and candles.get("close") is not None:
            close = candles["close"]
            open_ = candles.get("open")
            vol = candles.get("volume")
            n = len(close)
            if open_ is not None and n > 0:
                for i in range(max(0, n - 50), n):
                    side = "BUY" if close[i] >= open_[i] else "SELL"
                    v = float(vol[i]) if (vol is not None and i < len(vol)
                                          and vol[i] > 0) else 1.0
                    self.handle_trade(float(close[i]), v, side, is_direct=False)

        data = self.build_market_data(symbol=symbol, candles=candles)
        data["candles_m5"] = candles_m5
        data["candles_m15"] = candles_m15
        data["candles_h1"] = candles_h1
        has_l2 = bool(data.get("bid_depth") and data.get("ask_depth"))
        data["data_quality"] = {
            "quote": quote_status,
            "level2": "live" if has_l2 else "unavailable",
            "level3": "unavailable",
            "level3_events": "not_emitted",
            "l3_aggressive_flow": "unavailable",
            "l3_aggr_limit": "not_emitted",
            "trade_prints": "live" if real_trade_ticks else "unavailable",
            "order_flow": ("real_trade_ticks" if real_trade_ticks
                            else "estimated_from_candle_direction"),
        }
        data["quote_age_seconds"] = None
        data["quote_timestamp"] = None
        quote_epoch = None
        if tick is not None:
            try:
                _quote_msc = int(getattr(tick, "time_msc", 0) or 0)
                quote_epoch = (_quote_msc / 1000.0 if _quote_msc > 0 else
                               float(getattr(tick, "time", 0) or 0))
                if not math.isfinite(quote_epoch) or quote_epoch <= 0:
                    quote_epoch = None
            except (TypeError, ValueError, OverflowError):
                quote_epoch = None

        if quote_status == "valid" and quote_epoch is not None:
            try:
                max_quote_age = float(getattr(
                    config, "STALE_DATA_SECONDS", 300.0))
                if not math.isfinite(max_quote_age) or max_quote_age <= 0:
                    raise ValueError("invalid quote-age limit")
            except (TypeError, ValueError, OverflowError):
                max_quote_age = None
            from step4_mt5_execution import resolve_mt5_quote_age
            quote_age, self._server_offset_s = resolve_mt5_quote_age(
                quote_epoch,
                time.time(),
                max_age=(max_quote_age if max_quote_age is not None else 300.0),
                cached_offset_s=getattr(self, "_server_offset_s", None),
                config_mod=config,
            )
            if not math.isfinite(quote_age) or quote_age < 0:
                quote_status = "future_timestamp"
                spread_pct = None
            else:
                data["quote_age_seconds"] = quote_age
                eff_utc_epoch = time.time() - quote_age
                data["quote_timestamp"] = datetime.fromtimestamp(
                    eff_utc_epoch, tz=timezone.utc).isoformat()
                if max_quote_age is None:
                    quote_status = "invalid_age_limit"
                elif quote_age > max_quote_age:
                    quote_status = "stale"
                if quote_status == "valid":
                    data["bid"] = quote_bid
                    data["ask"] = quote_ask
                    data["price"] = (quote_bid + quote_ask) / 2.0
        else:
            if quote_status == "valid":
                quote_status = "timestamp_unknown"
            spread_pct = None

        if quote_status != "valid":
            data["bid"] = data["ask"] = 0.0
            if tick is not None:
                try:
                    last = float(getattr(tick, "last", 0) or 0)
                    if math.isfinite(last) and last > 0:
                        data["price"] = last
                except (TypeError, ValueError, OverflowError):
                    pass
        data["spread_pct"] = spread_pct
        data["data_quality"]["quote"] = quote_status
        if self.last_data_ts is not None:
            data["last_data_timestamp"] = self.last_data_ts.isoformat()
        logger.info("MT5 provider: %d ticks, %d book updates for %s "
                    "(spread %s, quote=%s age=%s, H1/M15/M5 bars %d/%d/%d)",
                    len(self.tick_data), len(self.book_updates), symbol,
                    f"{spread_pct:.4f}%" if spread_pct is not None else "n/a",
                    quote_status,
                    f"{data['quote_age_seconds']:.2f}s"
                    if data.get("quote_age_seconds") is not None else "n/a",
                    len(candles_h1.get("close", [])),
                    len(candles_m15.get("close", [])),
                    len(candles_m5.get("close", [])))
        return data


# ============================================================================= #
# REPLAY provider (recorded data — offline / weekend testing)
# ============================================================================= #

class ReplayProvider(BaseProvider):
    """Replay a previously recorded market_data JSON (e.g. captured during the
    week) so the full pipeline can be exercised offline / on weekends."""

    name = "replay"

    def __init__(self, path: Optional[str] = None):
        super().__init__()
        self.path = path or config.REPLAY_FILE

    def acquire(self, symbol: str = "", **kwargs) -> Dict[str, Any]:
        path = Path(self.path) if self.path else None
        if not path or not path.exists():
            raise ProviderNotAvailable(
                "Replay file not found. Set REPLAY_FILE in .env to a recorded "
                "market_data JSON (see data_providers.record_market_data), or "
                "set RECORD_DATA=1 during a live session to capture one.")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ProviderNotAvailable("Replay file must contain a market_data object")
        # restore numpy arrays
        candles = data.get("candles") or {}
        if not isinstance(candles, dict):
            candles = {}
        for k in ("open", "high", "low", "close", "volume"):
            if k in candles and candles[k] is not None:
                candles[k] = np.asarray(candles[k], dtype=float)
        data["candles"] = candles
        data["data_source"] = "replay"
        data_quality = data.get("data_quality")
        if not isinstance(data_quality, dict):
            data_quality = {}
            data["data_quality"] = data_quality

        def _recorded_epoch(value):
            if value is None or isinstance(value, bool):
                return None
            try:
                if isinstance(value, (int, float, np.integer, np.floating)):
                    epoch = float(value)
                    if not math.isfinite(epoch) or epoch <= 0:
                        return None
                    if epoch >= 1e17:
                        epoch /= 1e9
                    elif epoch >= 1e14:
                        epoch /= 1e6
                    elif epoch >= 1e11:
                        epoch /= 1e3
                    return epoch
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return float(parsed.timestamp())
            except (TypeError, ValueError, OverflowError, OSError):
                return None

        event_times = []
        for key in ("last_data_timestamp", "quote_timestamp", "depth_timestamp",
                    "last_trade_timestamp", "last_order_event_timestamp"):
            epoch = _recorded_epoch(data.get(key))
            if epoch is not None:
                event_times.append(epoch)
        for collection_key in ("tick_data", "order_events", "book_updates"):
            collection = data.get(collection_key) or []
            if isinstance(collection, (list, tuple)):
                for event in collection:
                    if isinstance(event, dict):
                        epoch = _recorded_epoch(event.get("timestamp"))
                        if epoch is not None:
                            event_times.append(epoch)
        now_epoch = time.time()
        latest_event = max(event_times, default=None)
        data["last_data_age_seconds"] = (
            now_epoch - latest_event if latest_event is not None else None)
        data["last_data_timestamp"] = (
            datetime.fromtimestamp(latest_event, tz=timezone.utc).isoformat()
            if latest_event is not None else None)
        for timestamp_key, age_key in (
                ("quote_timestamp", "quote_age_seconds"),
                ("depth_timestamp", "depth_age_seconds"),
                ("last_trade_timestamp", "last_trade_age_seconds"),
                ("last_order_event_timestamp", "last_order_event_age_seconds")):
            epoch = _recorded_epoch(data.get(timestamp_key))
            data[age_key] = now_epoch - epoch if epoch is not None else None

        try:
            max_age = float(getattr(config, "STALE_DATA_SECONDS", 300.0))
            if not math.isfinite(max_age) or max_age <= 0:
                raise ValueError("invalid replay freshness limit")
        except (TypeError, ValueError, OverflowError):
            max_age = None
        quote_status = str(data_quality.get("quote", "unknown") or "unknown").lower()
        quote_age = data.get("quote_age_seconds")
        if quote_status == "valid":
            if max_age is None:
                quote_status = "invalid_age_limit"
            elif quote_age is None:
                quote_status = "timestamp_unknown"
            elif not math.isfinite(float(quote_age)) or float(quote_age) < 0:
                quote_status = "future_timestamp"
            elif float(quote_age) > max_age:
                quote_status = "stale"
        if quote_status == "valid":
            try:
                raw_bid, raw_ask = data.get("bid"), data.get("ask")
                if (isinstance(raw_bid, bool) or isinstance(raw_ask, bool) or
                        raw_bid is None or raw_ask is None):
                    raise ValueError("missing replay bid/ask")
                bid, ask = float(raw_bid), float(raw_ask)
                if (not math.isfinite(bid) or not math.isfinite(ask) or
                        bid <= 0 or ask <= 0):
                    quote_status = "invalid_quote"
                elif ask <= bid:
                    quote_status = "crossed_quote"
            except (TypeError, ValueError, OverflowError):
                quote_status = "invalid_quote"
            if quote_status == "valid":
                raw_spread = data.get("spread_pct")
                if raw_spread is None:
                    quote_status = "spread_unknown"
                else:
                    try:
                        if isinstance(raw_spread, bool):
                            raise ValueError("boolean replay spread")
                        spread = float(raw_spread)
                        if not math.isfinite(spread) or spread <= 0:
                            quote_status = "invalid_spread"
                        else:
                            expected = (ask - bid) / ((ask + bid) / 2.0) * 100.0
                            if not math.isclose(spread, expected,
                                                rel_tol=1e-6, abs_tol=1e-8):
                                quote_status = "spread_quote_mismatch"
                    except (TypeError, ValueError, OverflowError):
                        quote_status = "invalid_spread"
        data_quality["quote"] = quote_status
        if quote_status in {"crossed", "crossed_or_locked", "crossed_quote"}:
            data_quality["level2"] = "crossed_book"
        elif quote_status in {"stale", "future_timestamp", "timestamp_unknown",
                              "invalid_age_limit", "invalid_quote", "invalid_spread",
                              "spread_unknown", "spread_quote_mismatch"}:
            data_quality["level2"] = "stale_or_missing_levels"
        data["has_data"] = data.get("has_data") is True or bool(
            data.get("tick_data") or data.get("order_events") or
            data.get("bid_depth") or data.get("ask_depth"))
        logger.info("REPLAY provider: loaded %s (%d ticks, %d order events; "
                    "quote=%s age=%s)",
                    path.name, len(data.get("tick_data", [])),
                    len(data.get("order_events", [])), quote_status,
                    f"{quote_age:.3f}s" if isinstance(quote_age, (int, float))
                    and math.isfinite(quote_age) else "unknown")
        return data


def record_market_data(data: Dict[str, Any], path: Optional[str] = None) -> Path:
    """Save an acquired market_data dict to disk (json-safe) for later replay."""
    from step2_market_analysis import _json_safe
    out = Path(path) if path else \
        config.DATA_DIR / f"record_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(_json_safe(data), indent=2), encoding="utf-8")
    logger.info("Recorded market data -> %s", out)
    return out


# ============================================================================= #
# Factory
# ============================================================================= #

SUPPORTED_SOURCES = ("demo", "rithmic", "databento", "mt5", "replay",
                    "bookmapbridge", "bookmap", "bmbridge", "ninjabridge")

_PROVIDERS = {
    "demo": DemoProvider,
    "rithmic": RithmicProvider,
    "databento": DatabentoProvider,
    "mt5": MT5Provider,
    "replay": ReplayProvider,
}


def get_provider(source: Optional[str] = None) -> BaseProvider:
    """Return the provider selected by DATA_SOURCE.

    Sources: demo | rithmic | databento | mt5 | replay | bookmapbridge (primary) |
             bookmap / bmbridge (short aliases) | ninjabridge (legacy alias).

    BookMap is now the primary bridge. Legacy ninjabridge name still works.
    """
    source = (source or config.DATA_SOURCE or "demo").lower().strip()
    # Normalize all bridge names to bookmapbridge
    if source in ("ninjabridge", "ntbridge", "ninja", "bookmapbridge", "bookmap", "bmbridge", "bookmap_bridge"):
        from bookmap_bridge_provider import BookmapBridgeProvider
        return BookmapBridgeProvider()
    if source not in _PROVIDERS:
        raise ValueError(f"Unknown data source '{source}'. "
                         f"Choose from {SUPPORTED_SOURCES}")
    return _PROVIDERS[source]()
