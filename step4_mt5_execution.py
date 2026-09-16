"""
step4_mt5_execution.py
======================
STEP 4: EXECUTION (MetaTrader 5)

Executes a Step 3 Decision ONLY if confidence > AI_CONFIDENCE_THRESHOLD (70%).

NEWS-TIME BEHAVIOUR
-------------------
- news_state == BLACKOUT  -> refuse new entries (SKIPPED), regardless of signal.
- news_state == WARNING   -> allow but WIDEN the stop and SHRINK position size
                             (news volatility protection).

POSITION SIZING
---------------
Risk-based: lots = (equity * risk%) / (stop_distance * contract_size).
Falls back to config.LOT_SIZE when equity is unavailable.

The MetaTrader5 SDK only works on Windows with a running MT5 terminal, so all
MT5 calls are guarded: without the SDK this module safely reports PENDING.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None

import config
import trade_history

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Outcome of a Step 4 attempt."""
    status: str                 # "EXECUTED" | "DEFERRED" | "SKIPPED" | "ERROR" | "PENDING"
    reason: str = ""
    order_id: Optional[int] = None
    deal_id: Optional[int] = None
    position_id: Optional[int] = None
    symbol: str = config.SYMBOL
    volume: float = 0.0
    price: float = 0.0
    sl: float = 0.0
    tp: float = 0.0
    timestamp: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat() if self.timestamp else None
        return d


class MT5Executor:
    """Sends orders to MetaTrader 5."""

    def __init__(self, symbol: Optional[str] = None):
        # Trade on the MT5 (CFD) market, NOT the futures data market.
        self.symbol = symbol or config.MT5_SYMBOL or config.SYMBOL
        self.magic = 234000  # unique id for this EA's orders

    # ------------------------------------------------------------------ #
    def execute(self, decision, snapshot) -> ExecutionResult:
        """Execute `decision` (from Step 3) using the Step 2 `snapshot`.

        This method is deliberately defensive because tests, integrations and
        future execution paths may call it directly rather than through
        ``main._safety_gates``. The master switch and basic snapshot validity
        must therefore be enforced here too.
        """
        now = datetime.now(timezone.utc)

        # --- gate 0: master kill switch ----------------------------------------
        # Do not rely only on main.py: a direct caller must never be able to
        # bypass TRADING_ENABLED=0 and reach mt5.order_send().
        if not getattr(config, "TRADING_ENABLED", True):
            logger.info("STEP 4: SKIPPED — trading disabled (TRADING_ENABLED=0).")
            return ExecutionResult(
                status="SKIPPED",
                reason="trading disabled (TRADING_ENABLED=0)",
                symbol=self.symbol, timestamp=now)

        # --- gate 0a: execution owner -----------------------------------------
        # Direct Python order placement is allowed only in python mode. In EA
        # mode the bridge is the only path allowed to produce an order signal.
        mode = getattr(config, "EXECUTION_MODE", "none")
        if mode != "python":
            logger.info("STEP 4: SKIPPED — Python executor disabled in %s mode.",
                        mode)
            return ExecutionResult(
                status="SKIPPED",
                reason=f"Python execution disabled (EXECUTION_MODE={mode})",
                symbol=self.symbol, timestamp=now)

        # --- gate 0b: valid market snapshot -----------------------------------
        # An empty/no-data snapshot must never be turned into a real order using
        # the fallback ATR and the current MT5 price.
        if snapshot is None or float(getattr(snapshot, "price", 0.0) or 0.0) <= 0:
            logger.info("STEP 4: SKIPPED — empty or invalid market snapshot.")
            return ExecutionResult(
                status="SKIPPED",
                reason="empty or invalid market snapshot",
                symbol=self.symbol, timestamp=now)

        # --- gate 1: confidence -------------------------------------------------
        if decision.confidence < config.AI_CONFIDENCE_THRESHOLD:
            logger.info("STEP 4: SKIPPED — confidence %.1f%% < %.0f%% "
                        "(decision: %s).", decision.confidence,
                        config.AI_CONFIDENCE_THRESHOLD, decision.action)
            return ExecutionResult(
                status="SKIPPED",
                reason=f"confidence {decision.confidence:.1f}% below threshold "
                       f"{config.AI_CONFIDENCE_THRESHOLD:.0f}%",
                symbol=self.symbol, timestamp=now)

        # --- gate 2: actionable direction ---------------------------------------
        if decision.action not in ("BUY", "SELL"):
            logger.info("STEP 4: SKIPPED — action is %s (not BUY/SELL).",
                        decision.action)
            return ExecutionResult(status="SKIPPED",
                                   reason=f"action '{decision.action}' not tradable",
                                   symbol=self.symbol, timestamp=now)

        # --- gate 3: NEWS-TIME BLACKOUT ------------------------------------------
        news_state = getattr(getattr(snapshot, "news", None), "news_state", "QUIET")
        if news_state == "BLACKOUT":
            mins = getattr(snapshot.news, "minutes_to_next_event", 0.0)
            logger.info("STEP 4: SKIPPED — news BLACKOUT (event in %.0f min). "
                        "No new entries during news.", mins)
            return ExecutionResult(
                status="SKIPPED",
                reason=f"news blackout — next event in {mins:.0f} min",
                symbol=self.symbol, timestamp=now)

        # --- gate 4: MT5 availability -------------------------------------------
        if mt5 is None:
            logger.warning("STEP 4: MetaTrader5 SDK not installed -> cannot "
                           "execute (Windows + MT5 terminal required).")
            return ExecutionResult(status="PENDING",
                                   reason="MetaTrader5 SDK not installed",
                                   symbol=self.symbol, timestamp=now)
        if not config.mt5_initialize(mt5):
            logger.error("STEP 4: mt5.initialize() failed: %s", mt5.last_error())
            return ExecutionResult(status="ERROR", reason="MT5 init failed",
                                   symbol=self.symbol, timestamp=now)

        # --- gate 4b: account type guard ----------------------------------------
        # The demo phase must never leak onto a real-money account by accident
        # (the terminal logs into whatever account was last used). Refuse to
        # trade REAL accounts unless ALLOW_LIVE_TRADING=1 in .env.
        account = mt5.account_info()
        trade_mode = getattr(account, "trade_mode", None)
        real_mode = getattr(mt5, "ACCOUNT_TRADE_MODE_REAL", 2)
        if trade_mode == real_mode and not getattr(config, "ALLOW_LIVE_TRADING", False):
            logger.error(
                "STEP 4: REAL (live) account detected — refusing to trade. "
                "Log the MT5 terminal into the DEMO account, or set "
                "ALLOW_LIVE_TRADING=1 in .env if this is intentional.")
            return ExecutionResult(
                status="ERROR",
                reason="real account blocked (ALLOW_LIVE_TRADING=0)",
                symbol=self.symbol, timestamp=now)

        try:
            return self._place_order(decision.action, snapshot, news_state,
                                     ai_confidence=float(
                                         getattr(decision, "confidence", 0.0)))
        finally:
            mt5.shutdown()

    # ------------------------------------------------------------------ #
    def _account_equity(self) -> Optional[float]:
        """Current account equity from MT5, else config fallback."""
        try:
            info = mt5.account_info()
            if info is not None and getattr(info, "equity", None):
                return float(info.equity)
        except Exception:
            pass
        return config.ACCOUNT_EQUITY if config.ACCOUNT_EQUITY > 0 else None

    def compute_lot_size(self, equity: float, stop_distance: float,
                         risk_pct: Optional[float] = None) -> float:
        """Risk-based lot sizing.

        lots = (equity * risk%) / (stop_distance * contract_size)
        Rounded down to 2 decimals, clamped to [0.01, MAX_LOT_SIZE].
        """
        risk_pct = risk_pct if risk_pct is not None else config.RISK_PER_TRADE_PCT
        if stop_distance <= 0 or config.CONTRACT_SIZE <= 0:
            return config.LOT_SIZE
        risk_amount = equity * risk_pct / 100.0
        lots = risk_amount / (stop_distance * config.CONTRACT_SIZE)
        lots = max(0.01, min(lots, config.MAX_LOT_SIZE))
        return float(np_round2(lots))

    # ------------------------------------------------------------------ #
    def _calc_sl_tp(self, action: str, price: float, atr: float,
                    news_state: str) -> tuple:
        """SL/TP from ATR multiples; widened during news WARNING."""
        sl_mult = config.STOP_LOSS_ATR_MULT
        if news_state == "WARNING":
            sl_mult *= config.NEWS_WIDEN_STOP_MULT
        tp_mult = config.TAKE_PROFIT_ATR_MULT
        if action == "BUY":
            return price - sl_mult * atr, price + tp_mult * atr
        return price + sl_mult * atr, price - tp_mult * atr

    def _bot_positions(self):
        """Return this bot's positions for the trade symbol.

        Positions from manual trading or another EA are never touched. A
        `None` result is treated as an MT5 query failure, not as "no positions".
        """
        positions = mt5.positions_get(symbol=self.symbol)
        if positions is None:
            raise RuntimeError(f"positions_get failed: {mt5.last_error()}")
        return [
            p for p in positions
            if int(getattr(p, "magic", -1) or -1) == int(self.magic)
        ]

    def _wait_for_bot_position(self, order_id: Optional[int] = None,
                               timeout: Optional[float] = None):
        """Wait briefly for MT5 to expose the newly opened bot position."""
        timeout = (config.EXECUTION_VERIFY_SECONDS if timeout is None else timeout)
        deadline = time.monotonic() + max(0.0, float(timeout))
        last_error = None
        while True:
            try:
                positions = self._bot_positions()
                for position in positions:
                    ticket = getattr(position, "ticket", None)
                    if order_id is None or ticket == order_id or positions:
                        return position
            except Exception as exc:
                last_error = str(exc)
            if time.monotonic() >= deadline:
                if last_error:
                    logger.warning("STEP 4: position verification failed: %s", last_error)
                return None
            time.sleep(0.2)

    def _close_position(self, position) -> tuple:
        """Close one bot position and return (ok, reason)."""
        tick = mt5.symbol_info_tick(self.symbol)
        if tick is None:
            return False, "no tick available while closing position"

        pos_type = getattr(position, "type", None)
        is_buy = pos_type == mt5.POSITION_TYPE_BUY
        close_type = mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY
        price = tick.bid if is_buy else tick.ask
        volume = float(getattr(position, "volume", 0.0) or 0.0)
        ticket = getattr(position, "ticket", None)
        if not ticket or volume <= 0 or not price:
            return False, "invalid position ticket, volume or price"

        info = mt5.symbol_info(self.symbol)
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": volume,
            "type": close_type,
            "position": int(ticket),
            "price": price,
            "deviation": 20,
            "magic": self.magic,
            "comment": "gold-trading-bot close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._select_filling_mode(info) if info else
                            getattr(mt5, "ORDER_FILLING_IOC", 1),
        }
        result = mt5.order_send(request)
        done = getattr(mt5, "TRADE_RETCODE_DONE", 10009)
        partial = getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010)
        if result is None or getattr(result, "retcode", None) not in (done, partial):
            return False, f"close retcode={getattr(result, 'retcode', None)}"
        logger.info("STEP 4: closed bot position ticket=%s volume=%.2f",
                    ticket, volume)
        return True, "closed"

    def close_bot_positions(self) -> list:
        """Close all positions owned by this bot for the configured symbol.

        This public helper is used by the explicit demo-order plumbing test and
        by future operator controls. It never closes positions with another
        magic number.
        """
        if mt5 is None:
            return [{"ok": False, "reason": "MetaTrader5 SDK not installed"}]
        if not config.mt5_initialize(mt5):
            return [{"ok": False, "reason": f"MT5 init failed: {mt5.last_error()}"}]
        results = []
        try:
            positions = self._bot_positions()
            for position in positions:
                ok, reason = self._close_position(position)
                results.append({
                    "ticket": getattr(position, "ticket", None),
                    "ok": ok,
                    "reason": reason,
                })
            return results
        except Exception as exc:
            return [{"ok": False, "reason": str(exc)}]
        finally:
            mt5.shutdown()

    @staticmethod
    def _volume_digits(step: float) -> int:
        if step <= 0:
            return 2
        text = f"{step:.10f}".rstrip("0")
        return len(text.split(".", 1)[1]) if "." in text else 0

    def _normalize_volume(self, volume: float, info) -> float:
        """Clamp and round volume to this broker's min/step/max rules."""
        minimum = float(getattr(info, "volume_min", 0.01) or 0.01)
        maximum = float(getattr(info, "volume_max", config.MAX_LOT_SIZE) or
                        config.MAX_LOT_SIZE)
        step = float(getattr(info, "volume_step", 0.01) or 0.01)
        volume = max(minimum, min(float(volume), maximum))
        if step > 0:
            volume = minimum + math.floor((volume - minimum + 1e-12) / step) * step
        return round(max(minimum, min(volume, maximum)), self._volume_digits(step))

    def _validate_stops(self, action: str, price: float, sl: float,
                        tp: float, info) -> Optional[str]:
        """Validate SL/TP against broker stops level; return an error reason."""
        point = float(getattr(info, "point", 0.0) or 0.0)
        stops_level = float(getattr(info, "trade_stops_level", 0.0) or 0.0)
        minimum_distance = point * stops_level
        if minimum_distance <= 0:
            return None
        if action == "BUY":
            valid = price - sl >= minimum_distance and tp - price >= minimum_distance
        else:
            valid = sl - price >= minimum_distance and price - tp >= minimum_distance
        if not valid:
            return (f"SL/TP too close for broker stops level: need "
                    f"{minimum_distance:.{getattr(info, 'digits', 2)}f}")
        return None

    @staticmethod
    def _select_filling_mode(info) -> int:
        """Choose an MT5 filling mode supported by the broker symbol."""
        raw = int(getattr(info, "filling_mode", 0) or 0)
        # SYMBOL_FILLING flags: FOK=1, IOC=2. ORDER_FILLING values are
        # FOK=0, IOC=1, RETURN=2.
        if raw & 2:
            return getattr(mt5, "ORDER_FILLING_IOC", 1)
        if raw & 1:
            return getattr(mt5, "ORDER_FILLING_FOK", 0)
        return getattr(mt5, "ORDER_FILLING_RETURN", 2)

    def _validate_margin(self, action: str, volume: float,
                         price: float) -> Optional[str]:
        """Check required margin before attempting to send an order."""
        account = mt5.account_info()
        calculator = getattr(mt5, "order_calc_margin", None)
        if account is None or not callable(calculator):
            return "account margin information unavailable"
        free_margin = getattr(account, "margin_free", None)
        if free_margin is None:
            return "account free margin unavailable"
        order_type = mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL
        required = calculator(order_type, self.symbol, volume, price)
        if required is None:
            return f"margin calculation failed: {mt5.last_error()}"
        required = float(required)
        free_margin = float(free_margin)
        if required > free_margin:
            return (f"insufficient free margin: need {required:.2f}, "
                    f"available {free_margin:.2f}")
        return None

    def _place_order(self, action: str, snapshot,
                     news_state: str,
                     ai_confidence: float = 0.0) -> ExecutionResult:
        """Send a market order with SL/TP. (Runs only when MT5 is available.)"""
        now = datetime.now(timezone.utc)
        tick = mt5.symbol_info_tick(self.symbol)
        if tick is None:
            return ExecutionResult(status="ERROR", reason="no tick data",
                                   symbol=self.symbol, timestamp=now)

        is_buy = action == "BUY"
        price = tick.ask if is_buy else tick.bid  # CFD price from MT5
        info = mt5.symbol_info(self.symbol)
        if info is None:
            return ExecutionResult(status="ERROR", reason="symbol info unavailable",
                                   symbol=self.symbol, price=price, timestamp=now)

        # CFD spread guard: main.py's spread gate watches the DATA feed
        # (futures bid/ask); this checks the actual broker spread on the trade
        # symbol right before ordering. A wide spread is an instant edge loss.
        if config.MAX_SPREAD_PCT > 0 and tick.bid > 0 and tick.ask > 0:
            cfd_spread_pct = (tick.ask - tick.bid) / tick.bid * 100.0
            if cfd_spread_pct > config.MAX_SPREAD_PCT:
                reason = (f"CFD spread too wide: {cfd_spread_pct:.3f}% > "
                          f"{config.MAX_SPREAD_PCT:.3f}% — DEFERRED")
                logger.warning("STEP 4: %s", reason)
                return ExecutionResult(status="DEFERRED", reason=reason,
                                       symbol=self.symbol, price=price,
                                       timestamp=now)

        # v4.4 MEMORY GATE: recent same-direction loss and/or a losing day
        # raise the entry bar. Fresh robot, no losses, winning day -> this
        # gate is a no-op (bar stays where Step 2 put it).
        mem_penalty = 0.0
        mem_notes = []
        if config.ENTRY_LOSS_MEMORY_MINUTES > 0:
            loss = trade_history.recent_loss(
                action, config.ENTRY_LOSS_MEMORY_MINUTES)
            if loss["hit"]:
                mem_penalty += config.ENTRY_LOSS_MEMORY_SCORE_PENALTY
                mem_notes.append(
                    f"recent {action} loss {loss['minutes_ago']:.0f}m ago "
                    f"({loss['reason']}) -> bar +"
                    f"{config.ENTRY_LOSS_MEMORY_SCORE_PENALTY:.0f}")
        if config.ENTRY_DAY_RATCHET_ENABLE:
            equity_now = float(self._account_equity() or 0.0)
            day_pct = trade_history.day_pnl_pct(equity_now) if equity_now > 0 \
                else 0.0
            if equity_now > 0 and day_pct <= -abs(config.ENTRY_RATCHET_2_PCT):
                mem_penalty += config.ENTRY_RATCHET_2_PENALTY
                mem_notes.append(f"day {day_pct:+.2f}% -> bar +"
                                 f"{config.ENTRY_RATCHET_2_PENALTY:.0f}")
            elif day_pct <= -abs(config.ENTRY_RATCHET_1_PCT):
                mem_penalty += config.ENTRY_RATCHET_1_PENALTY
                mem_notes.append(f"day {day_pct:+.2f}% -> bar +"
                                 f"{config.ENTRY_RATCHET_1_PENALTY:.0f}")
        if mem_penalty > 0:
            base_bar = (config.SIGNAL_BUY_THRESHOLD if is_buy
                        else abs(config.SIGNAL_SELL_THRESHOLD))
            score = float(getattr(snapshot, "signal_strength", 0.0) or 0.0)
            score_signed = score if is_buy else -score
            if score_signed < base_bar + mem_penalty:
                reason = (f"score {score_signed:+.0f} below raised bar "
                          f"{base_bar + mem_penalty:+.0f} ({'; '.join(mem_notes)})")
                logger.info("STEP 4: SKIPPED — %s", reason)
                return ExecutionResult(status="SKIPPED", reason=reason,
                                       symbol=self.symbol, price=price,
                                       timestamp=now)

        # Position ownership: same direction is idempotent; opposite bot
        # positions are closed before the new direction is opened. Positions
        # with another magic number (including manual trades) are untouched.
        try:
            bot_positions = self._bot_positions()
        except Exception as exc:
            logger.error("STEP 4: position query failed: %s", exc)
            return ExecutionResult(status="ERROR", reason=str(exc),
                                   symbol=self.symbol, price=price, timestamp=now)
        wanted_type = mt5.POSITION_TYPE_BUY if is_buy else mt5.POSITION_TYPE_SELL
        opposite = [p for p in bot_positions
                    if getattr(p, "type", None) != wanted_type]
        same = [p for p in bot_positions
                if getattr(p, "type", None) == wanted_type]
        if same:
            logger.info("STEP 4: SKIPPED — bot already has %s position(s).", action)
            return ExecutionResult(
                status="SKIPPED", reason=f"bot already has {action} position",
                symbol=self.symbol,
                volume=float(getattr(same[0], "volume", 0.0) or 0.0),
                price=price, timestamp=now)
        for position in opposite:
            ok, reason = self._close_position(position)
            if not ok:
                logger.error("STEP 4: could not close opposite position: %s", reason)
                return ExecutionResult(status="ERROR", reason=f"close failed: {reason}",
                                       symbol=self.symbol, price=price, timestamp=now)

        # futures <-> CFD basis check (warns if the two markets dislocate)
        try:
            from spread_monitor import monitor as spread_mon
            spread_mon.update(getattr(snapshot, "price", 0.0), price)
            if spread_mon.is_wide():
                logger.warning("STEP 4: futures/CFD basis is wide — %s",
                               spread_mon.report())
        except Exception:
            pass

        # Re-anchor the FUTURES ATR to the CFD price. ATR is a movement measure;
        # it must be expressed as % of price and re-applied to the trade market
        # so SL/TP reflect market movement, not the (different) futures price.
        src_atr = snapshot.volatility.atr if snapshot.volatility.atr > 0 else \
            price * 0.005  # 0.5% fallback
        src_price = getattr(snapshot, "price", 0.0) or price
        atr = src_atr * (price / src_price) if src_price > 0 else src_atr
        # v4: structural stops — SL behind demand/supply zones (order blocks),
        # POC or strong round numbers; TP in front of opposing structure.
        # Falls back to the old ATR multiples when no structure exists.
        try:
            from position_manager import compute_structural_stops
            sl, tp, struct_notes = compute_structural_stops(
                action, price, atr, snapshot, news_state)
            if struct_notes:
                logger.info("STEP 4: structural stops: %s",
                            "; ".join(struct_notes))
        except Exception:
            logger.exception("structural stops failed — ATR fallback")
            sl, tp = self._calc_sl_tp(action, price, atr, news_state)
        stop_error = self._validate_stops(action, price, sl, tp, info)
        if stop_error:
            logger.error("STEP 4: %s", stop_error)
            return ExecutionResult(status="ERROR", reason=stop_error,
                                   symbol=self.symbol, price=price, sl=sl,
                                   tp=tp, timestamp=now)

        # --- position sizing ----------------------------------------------------
        equity = self._account_equity()
        stop_distance = abs(price - sl)
        lot_size = self.compute_lot_size(equity, stop_distance) \
            if equity else config.LOT_SIZE
        if news_state == "WARNING":
            lot_size = lot_size * config.NEWS_REDUCE_SIZE_PCT  # shrink during news
        lot_size = self._normalize_volume(lot_size, info)

        # v4.4 COST GUARD: a target closer than ENTRY_MIN_TP_SPREAD_MULT
        # spreads cannot pay the toll. Skip — there will be another bus.
        if (config.ENTRY_MIN_TP_SPREAD_MULT > 0 and tp > 0
                and tick.bid > 0 and tick.ask > 0):
            spread = tick.ask - tick.bid
            tp_distance = abs(tp - price)
            if tp_distance < config.ENTRY_MIN_TP_SPREAD_MULT * spread:
                reason = (f"TP too close to pay the toll: target "
                          f"{tp_distance:.2f} pts < "
                          f"{config.ENTRY_MIN_TP_SPREAD_MULT:.1f} x spread "
                          f"{spread:.2f} pts")
                logger.info("STEP 4: SKIPPED — %s", reason)
                return ExecutionResult(status="SKIPPED", reason=reason,
                                       symbol=self.symbol, price=price,
                                       sl=sl, tp=tp, timestamp=now)

        # v4.4 REAL-RISK GUARD: with a minimum lot forced by the broker,
        # the ACTUAL dollar risk can be far above RISK_PER_TRADE_PCT (e.g.
        # 0.01 lots on a $670 account with a wide stop). Refuse instead of
        # quietly over-risking.
        if config.ENTRY_MAX_REAL_RISK_PCT > 0 and equity > 0 and lot_size > 0:
            real_risk_usd = lot_size * stop_distance * config.CONTRACT_SIZE
            max_risk_usd = equity * config.ENTRY_MAX_REAL_RISK_PCT / 100.0
            if real_risk_usd > max_risk_usd:
                reason = (f"real risk ${real_risk_usd:.2f} > "
                          f"{config.ENTRY_MAX_REAL_RISK_PCT:.2f}% of equity "
                          f"(${max_risk_usd:.2f}) at min lot "
                          f"{lot_size:.2f} / stop {stop_distance:.1f} pts")
                logger.info("STEP 4: SKIPPED — %s", reason)
                return ExecutionResult(status="SKIPPED", reason=reason,
                                       symbol=self.symbol, price=price,
                                       sl=sl, tp=tp, volume=lot_size,
                                       timestamp=now)

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": lot_size,
            "type": mt5.ORDER_TYPE_BUY if is_buy else mt5.ORDER_TYPE_SELL,
            "price": price,
            "sl": sl,
            "tp": tp,
            "deviation": 20,
            "magic": self.magic,
            "comment": "gold-trading-bot",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": self._select_filling_mode(info),
        }

        margin_error = self._validate_margin(action, lot_size, price)
        if margin_error:
            logger.error("STEP 4: %s", margin_error)
            return ExecutionResult(status="ERROR", reason=margin_error,
                                   symbol=self.symbol, volume=lot_size,
                                   price=price, sl=sl, tp=tp, timestamp=now)

        # Ask MT5 to validate the request before the actual send. This is a
        # preflight check only; it does not create an order.
        order_check = getattr(mt5, "order_check", None)
        if callable(order_check):
            checked = order_check(request)
            if checked is None:
                reason = f"order_check returned no result: {mt5.last_error()}"
                logger.error("STEP 4: %s", reason)
                return ExecutionResult(status="ERROR", reason=reason,
                                       symbol=self.symbol, volume=lot_size,
                                       price=price, sl=sl, tp=tp, timestamp=now)
            check_code = getattr(checked, "retcode", 0)
            if check_code not in (0, getattr(mt5, "TRADE_RETCODE_DONE", 10009)):
                reason = f"order_check retcode={check_code}"
                logger.error("STEP 4: %s", reason)
                return ExecutionResult(status="ERROR", reason=reason,
                                       symbol=self.symbol, volume=lot_size,
                                       price=price, sl=sl, tp=tp, timestamp=now)

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.error("STEP 4: order_send failed retcode=%s",
                         getattr(result, "retcode", None))
            return ExecutionResult(status="ERROR",
                                   reason=f"order_send retcode={getattr(result, 'retcode', None)}",
                                   symbol=self.symbol, volume=lot_size,
                                   price=price, sl=sl, tp=tp, timestamp=now)

        order_id = getattr(result, "order", None)
        deal_id = getattr(result, "deal", None)
        verified_position = self._wait_for_bot_position(order_id=order_id)
        if verified_position is None:
            reason = (f"order reported success but bot position was not found "
                      f"within {config.EXECUTION_VERIFY_SECONDS}s")
            logger.error("STEP 4: %s", reason)
            return ExecutionResult(status="ERROR", reason=reason,
                                   order_id=order_id, deal_id=deal_id,
                                   symbol=self.symbol, volume=lot_size,
                                   price=price, sl=sl, tp=tp, timestamp=now)

        logger.info("STEP 4: EXECUTED %s %s %.2f lots @ %.2f (SL %.2f / TP %.2f) "
                    "order=%s deal=%s position=%s", action, self.symbol,
                    lot_size, price, sl, tp, order_id, deal_id,
                    getattr(verified_position, "ticket", None))
        # v4.4: remember the trade context (for loss memory + review) and
        # log the fill quality (intended vs actual price = slippage).
        try:
            ticket = getattr(verified_position, "ticket", None)
            trade_history.record_entry(
                ticket, action, price, sl, tp, lot_size,
                ai_confidence=ai_confidence,
                signal_strength=float(getattr(snapshot, "signal_strength",
                                              0.0) or 0.0),
                signal_score=float(getattr(snapshot, "signal_strength",
                                           0.0) or 0.0))
            fill_price = float(getattr(result, "price", 0.0) or 0.0)
            if fill_price <= 0:
                fill_price = float(getattr(verified_position, "price_open",
                                           0.0) or 0.0)
            trade_history.record_tca("ENTRY", ticket, price, fill_price,
                                     lot_size, note=action)
        except Exception:
            logger.exception("STEP 4: trade memory/tca write failed "
                             "(non-fatal)")
        return ExecutionResult(status="EXECUTED", order_id=order_id,
                               deal_id=deal_id,
                               position_id=getattr(verified_position, "ticket", None),
                               symbol=self.symbol, volume=lot_size, price=price,
                               sl=sl, tp=tp, timestamp=now)


def np_round2(value: float) -> float:
    """Round to 2 decimals without numpy dependency (MT5 lot convention)."""
    return float(round(value * 100.0) / 100.0)
