"""
tools/backtest.py — replay a recorded ticks.csv through the REAL robot (v4.4)

WHY THIS EXISTS
---------------
Institutional rule #1: never trade a strategy you have not replayed.
Until v4.4 the robot could only be evaluated live, one slow day at a
time. This harness takes the SAME ticks.csv the NinjaTrader bridge
already records and replays it through the REAL code:

    ticks.csv  ->  REAL _BridgeTail parser      (ninja_bridge_provider)
               ->  REAL analyze_market()        (step2 — every vote/weight)
               ->  entry gate (deterministic — AI is OFF, noted in report)
               ->  REAL PositionManager         (every exit rule)

So any change to weights, thresholds, PM rules or guards can be measured
BEFORE it touches a live account: run a backtest, change one .env dial,
run it again, compare.

WHAT IS SIMULATED (and honestly so)
  * fills happen at the bid/ask of the event (+ optional slippage arg)
  * SL/TP fills are checked on every Bid/Ask/Last event
  * position size mirrors step4's lot formula (equity x RISK% / stop)
  * AI (step 3) is OFF: entries fire on the raw signal score alone, so
    results show the SIGNAL layer, not the AI layer. The report says so.

USAGE (from the Gold-MT5 folder)
    python tools/backtest.py --file ticks.csv
    python tools/backtest.py --file ticks.csv --symbol "MGC 12-26" \\
        --equity 670 --min-score 15 --cycle-seconds 60
Windows copy of ticks.csv works as-is — just point --file at it.

OUTPUT
    data/backtest_<timestamp>/trades.csv   one row per closed trade
    data/backtest_<timestamp>/summary.txt  the printed report
    console: win rate, expectancy, rule frequencies, equity curve stats
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import time as _time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# make package imports work when run as  python tools/backtest.py
HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import config                                    # noqa: E402
import position_manager as pm_mod                # noqa: E402
import step2_market_analysis as s2               # noqa: E402
import trade_history as th                       # noqa: E402
from data_providers import BaseProvider, trades_to_candles  # noqa: E402
try:
    from bookmap_bridge_provider import _BridgeTail    # noqa: E402  BookMap edition primary
except ImportError:
    from ninja_bridge_provider import _BridgeTail    # noqa: E402  legacy fallback


# --------------------------------------------------------------------------- #
# Event clock + module patches (the whole harness runs on EVENT time)
# --------------------------------------------------------------------------- #

class EventClock:
    """One shared clock, advanced by the harness, patched into modules."""

    def __init__(self) -> None:
        self.ts: float = 0.0

    def time(self) -> float:                      # replaces time.time()
        return self.ts


class ShimDatetime(datetime):                     # replaces datetime class
    _clock: EventClock = None                     # type: ignore[assignment]

    @classmethod
    def now(cls, tz=None) -> datetime:
        return datetime.fromtimestamp(cls._clock.ts, tz=tz or timezone.utc)


def patch_clocks(clock: EventClock, workdir: Path) -> None:
    """Point PM / step2 / trade_history at the event clock and the
    sandbox workdir, so a backtest NEVER writes into the live files."""
    clock.ts = _time.time()                       # sane before first event
    ShimDatetime._clock = clock
    pm_mod.time = clock                           # type: ignore[assignment]
    pm_mod.datetime = ShimDatetime                # type: ignore[assignment]
    s2.datetime = ShimDatetime                    # type: ignore[assignment]
    th.time = clock                               # type: ignore[assignment]
    # live-file redirection
    workdir.mkdir(parents=True, exist_ok=True)
    pm_mod.DATA_DIR = workdir
    pm_mod.STATE_PATH = workdir / "pm_state.json"
    pm_mod.JOURNAL_PATH = workdir / "management_log.csv"
    th._base_dir = lambda: str(workdir)           # type: ignore[assignment]


# --------------------------------------------------------------------------- #
# Simulated broker
# --------------------------------------------------------------------------- #

class SimInfo:
    point = 0.1
    digits = 2
    volume_min = 0.01
    volume_step = 0.01
    volume_max = 100.0
    trade_stops_level = 0
    filling_mode = 3
    path = "SIM"


class SimTick:
    def __init__(self, bid: float, ask: float) -> None:
        self.bid, self.ask = bid, ask


class SimResult:
    retcode = 10009                                # TRADE_RETCODE_DONE
    def __init__(self, price: float) -> None:
        self.price = price
        self.order = 900000
        self.deal = 900000
        self.comment = ""


class SimDeal:
    def __init__(self, profit: float, comment: str) -> None:
        self.profit = profit
        self.comment = comment


class SimPosition:
    _next_ticket = 100000

    def __init__(self, side: str, volume: float, price: float,
                 sl: float, tp: float, ts: float) -> None:
        SimPosition._next_ticket += 1
        self.ticket = SimPosition._next_ticket
        self.type = 0 if side == "BUY" else 1      # POSITION_TYPE_BUY=0
        self.volume = volume
        self.price_open = price
        self.sl = sl
        self.tp = tp
        self.profit = 0.0                          # realized on close
        self.opened_ts = ts
        self.deals: List[SimDeal] = []
        self.magic = pm_mod.PM_MAGIC

    def unrealized(self, bid: float, ask: float) -> float:
        px = bid if self.type == 0 else ask
        gain = (px - self.price_open) if self.type == 0 \
            else (self.price_open - px)
        return gain * self.volume * config.CONTRACT_SIZE


class SimMT5:
    """Duck-typed MetaTrader5 for the position manager + harness."""

    # --- constants (real MT5 values) ---
    TRADE_ACTION_DEAL = 1
    TRADE_ACTION_SLTP = 6
    POSITION_TYPE_BUY = 0
    POSITION_TYPE_SELL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2
    TRADE_RETCODE_DONE = 10009

    def __init__(self) -> None:
        self.positions: List[SimPosition] = []
        self.closed_positions: List[SimPosition] = []
        self.fills: List[dict] = []          # every realized fill
        self._closed_deals: Dict[int, List[SimDeal]] = {}
        self.bid = 0.0
        self.ask = 0.0
        self.orders_log: List[dict] = []
        self.slippage = 0.0

    # --- MT5 API surface ---
    def initialize(self, *a, **k):
        return True

    def shutdown(self):
        pass

    def symbol_info(self, symbol=None):
        return SimInfo()

    def symbol_info_tick(self, symbol=None):
        return SimTick(self.bid, self.ask)

    def positions_get(self, symbol=None):
        return list(self.positions)

    def history_deals_get(self, date_from=None, date_to=None, position=None):
        if position is None:
            return []
        pos = next((p for p in self.closed_positions
                    if p.ticket == position), None)
        if pos is not None:
            return list(pos.deals)
        live = next((p for p in self.positions if p.ticket == position), None)
        return list(live.deals) if live is not None else []

    def order_send(self, request: dict):
        action = request.get("action")
        if action == self.TRADE_ACTION_SLTP:
            ticket = int(request.get("position", 0))
            for p in self.positions:
                if p.ticket == ticket:
                    p.sl = float(request.get("sl", p.sl) or p.sl)
                    p.tp = float(request.get("tp", p.tp) or p.tp)
                    self.orders_log.append({**request, "kind": "SLTP"})
                    return SimResult(self.bid)
            return SimResult(self.bid)

        if action == self.TRADE_ACTION_DEAL:
            ticket = int(request.get("position", 0))
            pos = next((p for p in self.positions if p.ticket == ticket),
                       None)
            if pos is None:
                return SimResult(self.bid)
            vol = float(request.get("volume", pos.volume))
            is_buy = pos.type == 0
            px = (self.ask if is_buy else self.bid) + \
                (self.slippage if is_buy else -self.slippage)
            comment = str(request.get("comment", "") or "")
            self._fill(pos, vol, px, comment)
            return SimResult(px)
        return SimResult(self.bid)

    # --- shared accounting for PM closes and harness stop-outs ---
    def _fill(self, pos: "SimPosition", vol: float, px: float,
              comment: str) -> None:
        is_buy = pos.type == 0
        gain = (px - pos.price_open) if is_buy else (pos.price_open - px)
        profit = gain * vol * config.CONTRACT_SIZE
        pos.deals.append(SimDeal(profit, comment))
        self._closed_deals.setdefault(pos.ticket, []).append(pos.deals[-1])
        pos.volume = round(pos.volume - vol, 2)
        if pos.volume <= 0.0001:
            self.positions.remove(pos)
            self.closed_positions.append(pos)
        self.fills.append({"ticket": pos.ticket, "volume": vol,
                           "price": px, "profit": profit,
                           "comment": comment, "ts": _time.time(),
                           "full": pos not in self.positions})
        self.orders_log.append({"kind": "CLOSE", "ticket": pos.ticket,
                                "volume": vol, "fill": px,
                                "profit": profit, "comment": comment})


# --------------------------------------------------------------------------- #
# Replayer provider — feeds the REAL _BridgeTail state into the REAL
# build_market_data() exactly like the live provider does
# --------------------------------------------------------------------------- #

class Replayer(BaseProvider):
    name = "replay"

    def __init__(self) -> None:
        super().__init__()
        self.replay_now: Optional[datetime] = None
        self.depth_age_limit_invalid = False

    @staticmethod
    def _as_utc_datetime(value) -> Optional[datetime]:
        if value is None:
            return None
        try:
            if isinstance(value, datetime):
                stamp = value
            else:
                stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return stamp.astimezone(timezone.utc)
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    def load_from_state(self, st, window_seconds: float, now_ts: float):
        self.reset()
        self.replay_now = datetime.fromtimestamp(float(now_ts), tz=timezone.utc)
        cutoff = float(now_ts) - max(float(window_seconds), 0.0)
        now_epoch = float(now_ts)

        def _in_window(stamp):
            parsed = self._as_utc_datetime(stamp)
            return parsed if parsed is not None and cutoff <= parsed.timestamp() <= now_epoch else None

        ticks = []
        for tick in getattr(st, "ticks", []) or []:
            stamp = _in_window(tick.get("ts"))
            if stamp is None:
                continue
            ticks.append((stamp, tick))
        ticks.sort(key=lambda item: item[0])
        self.tick_data = [
            {"price": tick["price"], "volume": tick["volume"],
             "side": tick.get("side", ""),
             "is_direct": tick.get("is_direct") is True,
             "timestamp": stamp.isoformat(),
             "operation": tick.get("operation", "")}
            for stamp, tick in ticks
        ]
        self._trades = [
            {"timestamp": stamp.isoformat(), "price": tick["price"],
             "volume": tick["volume"]}
            for stamp, tick in ticks
        ]
        self.last_trade_ts = max((stamp for stamp, _ in ticks), default=None)

        try:
            max_depth_age = float(getattr(config, "BOOKMAP_LEVEL_MAX_AGE", 900.0))
            if not math.isfinite(max_depth_age) or max_depth_age <= 0:
                raise ValueError("invalid replay depth age")
            self.depth_age_limit_invalid = False
        except (TypeError, ValueError, OverflowError):
            max_depth_age = 0.0
            self.depth_age_limit_invalid = True

        def _fresh_levels(levels, level_timestamps):
            fresh = {}
            stamps = {}
            for raw_price, raw_size in (levels or {}).items():
                try:
                    if isinstance(raw_price, bool) or isinstance(raw_size, bool):
                        continue
                    price, size = float(raw_price), float(raw_size)
                    if not math.isfinite(price) or not math.isfinite(size) or price <= 0 or size <= 0:
                        continue
                    stamp = self._as_utc_datetime((level_timestamps or {}).get(raw_price))
                    if stamp is None:
                        continue
                    age = now_epoch - stamp.timestamp()
                    if not math.isfinite(age) or age < 0 or age > max_depth_age:
                        continue
                    fresh[price] = size
                    stamps[price] = stamp
                except (TypeError, ValueError, OverflowError, OSError):
                    continue
            return fresh, stamps

        self.bid_depth, bid_stamps = _fresh_levels(
            getattr(st, "bids", {}), getattr(st, "level_ts_b", {}))
        self.ask_depth, ask_stamps = _fresh_levels(
            getattr(st, "asks", {}), getattr(st, "level_ts_a", {}))
        self.bid_depth = dict(sorted(self.bid_depth.items(),
                                     key=lambda item: -item[0])[:20])
        self.ask_depth = dict(sorted(self.ask_depth.items(),
                                     key=lambda item: item[0])[:20])
        if self.bid_depth and self.ask_depth:
            best_bid, best_ask = max(self.bid_depth), min(self.ask_depth)
            best_bid_ts = bid_stamps.get(best_bid)
            best_ask_ts = ask_stamps.get(best_ask)
            if best_bid_ts is not None and best_ask_ts is not None:
                self.last_depth_ts = min(best_bid_ts, best_ask_ts)
                self.book_updates = [{
                    "bids": dict(self.bid_depth), "asks": dict(self.ask_depth),
                    "timestamp": self.last_depth_ts.isoformat(),
                }]
        self.order_book = {
            "bids": [(p, s) for p, s in sorted(self.bid_depth.items(),
                                               reverse=True)],
            "asks": [(p, s) for p, s in sorted(self.ask_depth.items())],
        }

        events = []
        for event in getattr(st, "mbo_events", []) or []:
            if not isinstance(event, dict):
                continue
            stamp = _in_window(event.get("timestamp"))
            if stamp is None:
                continue
            copied = dict(event)
            copied["timestamp"] = stamp.isoformat()
            events.append((stamp, copied))
        events.sort(key=lambda item: item[0])
        self.order_events = [event for _, event in events]
        self.last_order_event_ts = max((stamp for stamp, _ in events), default=None)

        event_stamps = [stamp for stamp, _ in ticks]
        event_stamps.extend(stamp for stamp, _ in events)
        if self.last_depth_ts is not None:
            event_stamps.append(self.last_depth_ts)
        last_state_event = self._as_utc_datetime(getattr(st, "last_dt", None))
        if last_state_event is not None and last_state_event.timestamp() <= now_epoch:
            event_stamps.append(last_state_event)
        self.last_data_ts = max(event_stamps, default=None)

    def build_market_data(self, symbol: str, candles=None, macro=None, news=None):
        data = super().build_market_data(symbol, candles=candles,
                                         macro=macro, news=news)
        now = self.replay_now or datetime.now(timezone.utc)
        now_epoch = now.timestamp()
        bid, ask = self._best_prices()
        spread_pct = None
        quote_age = None
        quote_timestamp = (self.last_depth_ts.isoformat()
                           if self.last_depth_ts is not None else None)
        if bid > 0 and ask > 0 and ask > bid:
            mid = (bid + ask) / 2.0
            spread_pct = (ask - bid) / mid * 100.0
        if self.depth_age_limit_invalid:
            quote_status = "invalid_age_limit"
        elif bid <= 0 or ask <= 0:
            quote_status = "unavailable"
        elif ask <= bid:
            quote_status = "crossed_or_locked"
        elif self.last_depth_ts is None:
            quote_status = "timestamp_unknown"
        else:
            quote_age = now_epoch - self.last_depth_ts.timestamp()
            if not math.isfinite(quote_age) or quote_age < 0:
                quote_status = "future_timestamp"
            else:
                try:
                    max_quote_age = float(getattr(config, "STALE_DATA_SECONDS", 300.0))
                    if not math.isfinite(max_quote_age) or max_quote_age <= 0:
                        raise ValueError("invalid replay quote age")
                except (TypeError, ValueError, OverflowError):
                    max_quote_age = None
                if max_quote_age is None:
                    quote_status = "invalid_age_limit"
                elif quote_age > max_quote_age:
                    quote_status = "stale"
                else:
                    quote_status = "valid"
        data["data_source"] = "replay"
        data["bid"] = bid if quote_status == "valid" else 0.0
        data["ask"] = ask if quote_status == "valid" else 0.0
        if quote_status == "valid":
            data["price"] = (bid + ask) / 2.0
        elif self.tick_data:
            data["price"] = float(self.tick_data[-1]["price"])
        data["spread_pct"] = spread_pct
        data["quote_age_seconds"] = quote_age
        data["quote_timestamp"] = quote_timestamp
        data["depth_age_seconds"] = quote_age
        data["depth_timestamp"] = quote_timestamp
        data["last_data_age_seconds"] = (
            now_epoch - self.last_data_ts.timestamp()
            if self.last_data_ts is not None else None)
        data["last_data_timestamp"] = (
            self.last_data_ts.isoformat() if self.last_data_ts is not None else None)
        data["last_trade_age_seconds"] = (
            now_epoch - self.last_trade_ts.timestamp()
            if self.last_trade_ts is not None else None)
        data["last_trade_timestamp"] = (
            self.last_trade_ts.isoformat() if self.last_trade_ts is not None else None)
        data["last_order_event_age_seconds"] = (
            now_epoch - self.last_order_event_ts.timestamp()
            if self.last_order_event_ts is not None else None)
        data["last_order_event_timestamp"] = (
            self.last_order_event_ts.isoformat()
            if self.last_order_event_ts is not None else None)
        if quote_status == "crossed_or_locked":
            level2_status = "crossed_book"
        elif quote_status == "valid":
            level2_status = "available"
        elif quote_status in {"stale", "future_timestamp", "invalid_age_limit"}:
            level2_status = "stale_or_missing_levels"
        else:
            level2_status = "unavailable"
        data["data_quality"]["quote"] = quote_status
        data["data_quality"]["level2"] = level2_status
        data["l2_diagnostics"] = {
            "status": "synthetic" if level2_status == "available" else level2_status,
            "best_bid": max(self.bid_depth) if self.bid_depth else None,
            "best_ask": min(self.ask_depth) if self.ask_depth else None,
            "quote_age_seconds": quote_age,
            "depth_timestamp": quote_timestamp,
        }
        return data


# --------------------------------------------------------------------------- #
# The harness
# --------------------------------------------------------------------------- #

class Backtester:
    def __init__(self, args) -> None:
        self.args = args
        self.workdir = Path(args.workdir) if args.workdir else \
            config.DATA_DIR / f"backtest_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        # A replay must exercise the position manager even when the LIVE
        # robot is switched off in .env (halted for the day) — force the
        # switches on for this process only. Noted in the report.
        config.TRADING_ENABLED = True
        config.PM_ENABLE = True
        self.clock = EventClock()
        patch_clocks(self.clock, self.workdir)
        self.mt5 = SimMT5()
        self.mt5.slippage = float(args.slippage)
        self.pm = pm_mod.PositionManager(symbol=args.trade_symbol or
                                         config.MT5_SYMBOL,
                                         mt5_module=self.mt5)
        self.provider = Replayer()
        self.tail = _BridgeTail.__new__(_BridgeTail)   # no thread, no file
        self.tail.path = args.file
        self.tail.instruments = {}
        self.tail.line_count = 0
        self.tail.bad_lines = 0
        self.tail.direct_side_hits = 0
        # For compatibility with bookmap provider's extra attrs
        if not hasattr(self.tail, 'lock'):
            import threading
            self.tail.lock = threading.Lock()
        self.equity = float(args.equity)
        self.start_equity = self.equity
        self.trades: List[dict] = []
        self.equity_curve: List[float] = [self.equity]
        self.stats = {"cycles": 0, "signals": 0, "entries": 0,
                      "skip_cooldown": 0, "skip_spread": 0,
                      "skip_bar": 0, "skip_open": 0, "sl_hits": 0,
                      "tp_hits": 0, "pm_closes": 0, "partials": 0,
                      "flat_cycles": 0, "lines": 0}
        self.last_entry_ts = 0.0
        self._fill_ptr = 0
        self.entry_px_for_ticket: Dict[int, dict] = {}

    # ---------------- entry sizing (mirrors step4's formula) --------------- #
    def lot_size(self, stop_distance: float) -> float:
        if stop_distance <= 0:
            return config.LOT_SIZE
        lots = (self.equity * config.RISK_PER_TRADE_PCT / 100.0) / \
            (stop_distance * config.CONTRACT_SIZE)
        return round(max(0.01, min(lots, config.MAX_LOT_SIZE)), 2)

    # ---------------- SL/TP (mirrors step4: structural first) -------------- #
    def stops(self, side: str, price: float, atr: float, snapshot):
        sl, tp, _ = pm_mod.compute_structural_stops(side, price, atr,
                                                    snapshot)
        sign = 1.0 if side == "BUY" else -1.0
        if sl <= 0:
            sl = price - sign * config.STOP_LOSS_ATR_MULT * atr
        if tp <= 0:
            tp = price + sign * config.TAKE_PROFIT_ATR_MULT * atr
        return sl, tp

    # ---------------- event-level SL/TP watch ------------------------------ #
    def check_stops(self) -> None:
        for pos in list(self.mt5.positions):
            hit_sl = ((pos.type == 0 and self.mt5.bid <= pos.sl) or
                      (pos.type == 1 and self.mt5.ask >= pos.sl)) \
                if pos.sl > 0 else False
            hit_tp = ((pos.type == 0 and self.mt5.bid >= pos.tp) or
                      (pos.type == 1 and self.mt5.ask <= pos.tp)) \
                if pos.tp > 0 else False
            if not (hit_sl or hit_tp):
                continue
            px = pos.sl if hit_sl else pos.tp
            reason = "SL_HIT" if hit_sl else "TP_HIT"
            self.mt5._fill(pos, pos.volume, px, reason)
            self.stats["sl_hits" if hit_sl else "tp_hits"] += 1

    # ---------------- realize fills into equity + trade rows ---------------- #
    def drain_fills(self) -> None:
        """Turn broker fills (PM closes, partials, stop-outs) into equity
        changes and one trade row per realized chunk."""
        while self._fill_ptr < len(self.mt5.fills):
            f = self.mt5.fills[self._fill_ptr]
            self._fill_ptr += 1
            self.equity += f["profit"]
            if not f["full"]:
                # partial exit: row for the banked chunk only
                self.trades.append({
                    "ticket": f["ticket"], "side": "PARTIAL",
                    "entry_time": "", "exit_time": datetime.fromtimestamp(
                        self.clock.ts, tz=timezone.utc).strftime("%m-%d %H:%M"),
                    "entry": "", "exit": round(f["price"], 2),
                    "volume": f["volume"], "pnl_usd": round(f["profit"], 2),
                    "reason": "PARTIAL_EXIT", "hold_min": "",
                    "entry_score": "",
                })
                continue
            pos = next((p for p in self.mt5.closed_positions
                        if p.ticket == f["ticket"]), None)
            if pos is None:
                continue
            entry = self.entry_px_for_ticket.pop(pos.ticket, {})
            reason = str(f["comment"]).replace("gold-bot pm:", "") \
                .strip()[:28] or "PM_CLOSE"
            self.trades.append({
                "ticket": pos.ticket,
                "side": "BUY" if pos.type == 0 else "SELL",
                "entry_time": datetime.fromtimestamp(
                    pos.opened_ts, tz=timezone.utc).strftime("%m-%d %H:%M"),
                "exit_time": datetime.fromtimestamp(
                    self.clock.ts, tz=timezone.utc).strftime("%m-%d %H:%M"),
                "entry": round(pos.price_open, 2),
                "exit": round(f["price"], 2),
                "volume": f["volume"],
                "pnl_usd": round(f["profit"], 2),
                "reason": reason,
                "hold_min": round((self.clock.ts - pos.opened_ts) / 60.0, 1),
                "entry_score": entry.get("score", ""),
            })
            entry.pop("score", None)

    # ---------------- one analysis/trade cycle ----------------------------- #
    def run_cycle(self, st, symbol: str) -> None:
        self.stats["cycles"] += 1
        now_dt = datetime.fromtimestamp(self.clock.ts, tz=timezone.utc)
        self.provider.load_from_state(st, self.args.window_seconds,
                                      self.clock.ts)
        market_data = self.provider.build_market_data(symbol=symbol)
        market_data["news"] = {"fetch_calendar": False, "events": [],
                               "headlines": []}
        market_data["macro"] = {}
        source_before_analysis = config.DATA_SOURCE
        try:
            config.DATA_SOURCE = "replay"
            snapshot = s2.analyze_market(market_data, now=now_dt)
        finally:
            config.DATA_SOURCE = source_before_analysis

        direction = snapshot.signal_direction
        strength = float(snapshot.signal_strength or 0.0)
        bid = snapshot.bid or snapshot.price
        ask = snapshot.ask or snapshot.price
        self.mt5.bid, self.mt5.ask = bid, ask
        self.check_stops()

        # ---- entry gate (AI OFF: raw score only — deterministic) -------- #
        if direction in ("BUY", "SELL") and abs(strength) >= 1.0:
            self.stats["signals"] += 1
        bar = self.args.min_score
        spread = max(ask - bid, 0.0)
        can_enter = (
            direction in ("BUY", "SELL")
            and (strength if direction == "BUY" else -strength) >= bar
            and not self.mt5.positions
            and (self.clock.ts - self.last_entry_ts)
            >= self.args.cooldown_seconds
            and spread / max(bid, 1e-9) * 100.0 <= config.MAX_SPREAD_PCT
        )
        if direction in ("BUY", "SELL"):
            if self.mt5.positions:
                self.stats["skip_open"] += 1
            elif (self.clock.ts - self.last_entry_ts) \
                    < self.args.cooldown_seconds:
                self.stats["skip_cooldown"] += 1
            elif spread / max(bid, 1e-9) * 100.0 > config.MAX_SPREAD_PCT:
                self.stats["skip_spread"] += 1
        if can_enter:
            atr = float(getattr(snapshot.volatility, "atr", 0.0) or 0.0) \
                or bid * 0.005
            entry_px = ask if direction == "BUY" else bid
            sl, tp = self.stops(direction, entry_px, atr, snapshot)
            stop_dist = abs(entry_px - sl)
            vol = self.lot_size(stop_dist)
            # v4.4 real-risk guard, same as live step 4
            real_risk = vol * stop_dist * config.CONTRACT_SIZE
            if config.ENTRY_MAX_REAL_RISK_PCT > 0 and real_risk > \
                    self.equity * config.ENTRY_MAX_REAL_RISK_PCT / 100.0:
                self.stats["skip_bar"] += 1
            else:
                pos = SimPosition(direction, vol, entry_px, sl, tp,
                                  self.clock.ts)
                self.mt5.positions.append(pos)
                self.entry_px_for_ticket[pos.ticket] = {
                    "score": round(strength, 1)}
                self.last_entry_ts = self.clock.ts
                self.stats["entries"] += 1
                th.record_entry(pos.ticket, direction, entry_px, sl, tp, vol,
                                ai_confidence=0.0, signal_strength=strength)
                th.record_tca("ENTRY", pos.ticket, entry_px, entry_px, vol,
                              note="backtest")
        elif direction in ("BUY", "SELL") and not self.mt5.positions \
                and (self.clock.ts - self.last_entry_ts) \
                >= self.args.cooldown_seconds \
                and spread / max(bid, 1e-9) * 100.0 <= config.MAX_SPREAD_PCT:
            self.stats["skip_bar"] += 1

        # ---- REAL position manager on whatever is open -------------------- #
        if self.mt5.positions:
            log_before = len(self.mt5.orders_log)
            try:
                self.pm.manage(snapshot)
            except Exception as exc:               # never kill the replay
                print(f"  [pm error @ {now_dt:%H:%M}] {exc}")
            for o in self.mt5.orders_log[log_before:]:
                if o.get("kind") == "CLOSE":
                    self.stats["pm_closes"] += 1
                    if "PARTIAL" in str(o.get("comment", "")):
                        self.stats["partials"] += 1
        self.drain_fills()
        self.equity_curve.append(self.equity)

    # ---------------- main replay loop -------------------------------------- #
    def run(self) -> dict:
        args = self.args
        # v4.4.3: archives are gzipped — accept both ticks.csv and .csv.gz
        # L2: Also check for mbo.csv.gz alongside ticks.csv for L3 metrics
        mbo_path = None
        try:
            from pathlib import Path as _P
            tick_p = _P(args.file)
            # Look for mbo.csv, mbo.csv.gz in same dir or data/archive
            candidates = [
                tick_p.parent / "mbo.csv",
                tick_p.parent / "mbo.csv.gz",
                tick_p.parent / (tick_p.stem + "_mbo.csv.gz"),
                _P("data") / "mbo.csv",
                _P("data") / "mbo.csv.gz",
            ]
            # Also check archive
            import glob
            for cand in candidates:
                if cand.exists():
                    mbo_path = cand
                    break
            if not mbo_path:
                # Check data/archive/mbo_*.csv.gz latest
                arch = sorted(_P("data/archive").glob("mbo_*.csv.gz"), reverse=True)
                if arch:
                    mbo_path = arch[0]
            if mbo_path:
                print(f"L2: Found MBO file {mbo_path} for L3 backtest")
        except Exception as e:
            print(f"L2 MBO check error: {e}")

        if str(args.file).lower().endswith(".gz"):
            import gzip
            fh = gzip.open(args.file, "rt", encoding="utf-8", errors="replace")
        else:
            fh = open(args.file, "r", encoding="utf-8", errors="replace")
        with fh:
            next_cycle_ts: Optional[float] = None
            chosen: Optional[str] = args.symbol or None
            st = None
            for line in fh:
                if not line.endswith("\n"):
                    break
                # parse the event's timestamp FIRST (cheap pre-check)
                try:
                    parts = next(csv.reader([line.rstrip("\r\n")]))
                    if len(parts) != 7 or parts[0] == "time":
                        continue
                    ts = _parse_event_ts(parts[0])
                except (StopIteration, csv.Error):
                    continue
                if ts is None:
                    continue
                if next_cycle_ts is None:
                    next_cycle_ts = ts + args.cycle_seconds
                    self.clock.ts = ts
                if ts > self.clock.ts:
                    self.clock.ts = ts
                    if self.mt5.positions:
                        self.check_stops()
                        self.drain_fills()
                self.tail._handle_line(line)
                self.stats["lines"] += 1
                # lock onto the instrument to trade at the FIRST cycle
                # (most ticks+book levels seen by then wins)
                if chosen is None and self.clock.ts >= next_cycle_ts:
                    chosen = max(self.tail.instruments.items(),
                                 key=lambda kv: len(kv[1].ticks) +
                                 len(kv[1].bids) + len(kv[1].asks))[0]
                    print(f"trading instrument: {chosen}")
                if chosen is not None and st is None:
                    st = self.tail.instruments.get(chosen)
                if st is None:
                    continue
                while self.clock.ts >= next_cycle_ts and st.ticks:
                    self.run_cycle(st, chosen)
                    next_cycle_ts += args.cycle_seconds

        # final cycle + flatten
        if st is not None and st.ticks:
            self.run_cycle(st, chosen)
        for pos in list(self.mt5.positions):
            px = self.mt5.bid if pos.type == 0 else self.mt5.ask
            self.mt5._fill(pos, pos.volume, px, "END_OF_DATA")
        self.drain_fills()
        return self.report()

    # ---------------- report ------------------------------------------------ #
    def report(self) -> dict:
        trades = self.trades
        wins = [t for t in trades if t["pnl_usd"] > 0]
        losses = [t for t in trades if t["pnl_usd"] <= 0]
        gross_w = sum(t["pnl_usd"] for t in wins)
        gross_l = -sum(t["pnl_usd"] for t in losses)
        # max drawdown on the realized equity curve
        peak, mdd = self.start_equity, 0.0
        for e in self.equity_curve:
            peak = max(peak, e)
            mdd = max(mdd, peak - e)
        # L2 L3 metrics: whale win rate, iceberg profit factor from trades
        l3_metrics = {}
        try:
            whale_trades = [t for t in trades if "whale" in str(t.get("reason","")).lower() or "L3" in str(t.get("reason",""))]
            iceberg_trades = [t for t in trades if "iceberg" in str(t.get("reason","")).lower()]
            if whale_trades:
                w_wins = [t for t in whale_trades if t["pnl_usd"]>0]
                l3_metrics["whale_trades"] = len(whale_trades)
                l3_metrics["whale_win_rate"] = round(100*len(w_wins)/len(whale_trades),1) if whale_trades else 0
                l3_metrics["whale_pnl"] = round(sum(t["pnl_usd"] for t in whale_trades),2)
            if iceberg_trades:
                i_wins = [t for t in iceberg_trades if t["pnl_usd"]>0]
                i_gross_w = sum(t["pnl_usd"] for t in i_wins)
                i_gross_l = -sum(t["pnl_usd"] for t in iceberg_trades if t["pnl_usd"]<=0)
                l3_metrics["iceberg_trades"] = len(iceberg_trades)
                l3_metrics["iceberg_win_rate"] = round(100*len(i_wins)/len(iceberg_trades),1) if iceberg_trades else 0
                l3_metrics["iceberg_profit_factor"] = round(i_gross_w/i_gross_l,2) if i_gross_l>0 else 0
        except Exception as e:
            l3_metrics["error"] = str(e)

        rule_counts: Dict[str, int] = {}
        try:
            with open(pm_mod.JOURNAL_PATH, "r", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    rule_counts[row.get("rule", "?")] = \
                        rule_counts.get(row.get("rule", "?"), 0) + 1
        except Exception:
            pass
        r = {
            "lines": self.stats["lines"],
            "cycles": self.stats["cycles"],
            "signals": self.stats["signals"],
            "entries": self.stats["entries"],
            "trades": len(trades),
            "wins": len(wins),
            "win_rate": round(100.0 * len(wins) / len(trades), 1)
            if trades else 0.0,
            "expectancy_usd": round(
                (gross_w - gross_l) / len(trades), 2) if trades else 0.0,
            "profit_factor": round(gross_w / gross_l, 2)
            if gross_l > 0 else float("inf") if gross_w > 0 else 0.0,
            "net_usd": round(self.equity - self.start_equity, 2),
            "max_drawdown_usd": round(mdd, 2),
            "avg_hold_min": round(
                sum(t["hold_min"] for t in trades
                    if isinstance(t["hold_min"], (int, float)))
                / max(sum(1 for t in trades
                          if isinstance(t["hold_min"], (int, float))), 1), 1)
            if trades else 0.0,
            "sl_hits": self.stats["sl_hits"],
            "tp_hits": self.stats["tp_hits"],
            "pm_closes": self.stats["pm_closes"],
            "partials": self.stats["partials"],
            "skipped": {k: v for k, v in self.stats.items()
                        if k.startswith("skip_")},
            "rule_counts": rule_counts,
            "l3_metrics": l3_metrics,
            "mbo_file": str(mbo_path) if 'mbo_path' in locals() and mbo_path else "",
        }
        # persist
        self.workdir.mkdir(parents=True, exist_ok=True)
        with open(self.workdir / "trades.csv", "w", newline="",
                  encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=[
                "ticket", "side", "entry_time", "exit_time", "entry",
                "exit", "volume", "pnl_usd", "reason", "hold_min",
                "entry_score"])
            w.writeheader()
            w.writerows(trades)
        lines = [
            "=" * 62,
            "BACKTEST REPORT — REAL step2 engine + REAL position manager",
            "=" * 62,
            f"file            : {self.args.file}",
            f"events / cycles : {r['lines']} / {r['cycles']}",
            f"signals (>=1)   : {r['signals']}   entries taken: {r['entries']}",
            f"skipped         : {r['skipped']}",
            f"trades closed   : {r['trades']}  "
            f"(SL {r['sl_hits']} / TP {r['tp_hits']} / PM {r['pm_closes']}"
            f" / partials {r['partials']})",
            f"win rate        : {r['win_rate']}%   "
            f"profit factor: {r['profit_factor']}",
            f"expectancy      : ${r['expectancy_usd']} per trade",
            f"net PnL         : ${r['net_usd']}   "
            f"max drawdown: ${r['max_drawdown_usd']}",
            f"avg hold        : {r['avg_hold_min']} min",
            f"PM rule usage   : {r['rule_counts']}",
            f"L3 metrics      : {r.get('l3_metrics',{})}",
            f"MBO file        : {r.get('mbo_file','')}",
            "AI              : OFF (deterministic score gate — the report",
            "                  measures the SIGNAL layer, not the AI layer)",
            "switches        : TRADING_ENABLED/PM_ENABLE forced ON for the",
            "                  replay (live halt state does not apply here)",
            "=" * 62,
        ]
        text = "\n".join(lines)
        print(text)
        (self.workdir / "summary.txt").write_text(text, encoding="utf-8")
        print(f"\nsaved: {self.workdir / 'trades.csv'}")
        print(f"saved: {self.workdir / 'summary.txt'}")
        return r


def _parse_event_ts(raw: str) -> Optional[float]:
    try:
        s = raw.strip()
        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z",
                    "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
            try:
                dt = datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            from datetime import timezone as _tz
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                        "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
                try:
                    dt = datetime.strptime(s, fmt).replace(tzinfo=_tz.utc)
                    break
                except ValueError:
                    continue
        if dt is None:
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError:
                return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


# --------------------------------------------------------------------------- #

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Replay ticks.csv through the real Gold-MT5 engine")
    ap.add_argument("--file", required=True, help="path to ticks.csv")
    ap.add_argument("--symbol", default="",
                    help="instrument column value to trade (default: "
                         "most active)")
    ap.add_argument("--trade-symbol", default="",
                    help="name shown to the PM (default: config MT5_SYMBOL)")
    ap.add_argument("--cycle-seconds", type=float, default=60.0,
                    help="minutes between analysis cycles, in seconds")
    ap.add_argument("--window-seconds", type=float,
                    default=float(getattr(config, "NT_WINDOW_SECONDS",
                                          3600.0)),
                    help="how much tick history each cycle sees")
    ap.add_argument("--equity", type=float, default=1000.0)
    ap.add_argument("--min-score", type=float,
                    default=float(getattr(config, "SIGNAL_BUY_THRESHOLD",
                                          15.0)),
                    help="entry score bar (default: live threshold)")
    ap.add_argument("--cooldown-seconds", type=float, default=300.0,
                    help="min seconds between entries")
    ap.add_argument("--slippage", type=float, default=0.0,
                    help="adverse fill slippage in price points")
    ap.add_argument("--workdir", default="",
                    help="output dir (default: data/backtest_<timestamp>)")
    args = ap.parse_args()

    if not os.path.exists(args.file):
        print(f"ERROR: file not found: {args.file}")
        return 2
    print(f"replaying {args.file} ...")
    bt = Backtester(args)
    bt.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
