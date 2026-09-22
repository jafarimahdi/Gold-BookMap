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

    def compute_vol_adjusted_lot_size(self, equity: float, atr: float,
                                      risk_pct: Optional[float] = None) -> float:
        """M2: Volatility-adjusted lot sizing.

        Formula: lots = (equity * risk%) / (ATR * contract_size)
        This ensures 1% risk = same dollar risk regardless of volatility.
        Example: $1000 equity, ATR $5, contract 100 -> lots = (1000*0.01)/(5*100) = 0.02
        If ATR high, lots smaller (protect), if low, larger (but capped).

        Returns raw lots before broker clamping.
        """
        try:
            risk_pct = risk_pct if risk_pct is not None else float(getattr(config, "VOL_LOT_RISK_PCT", 1.0))
        except:
            risk_pct = 1.0
        if atr <= 0 or config.CONTRACT_SIZE <= 0 or equity <= 0:
            return config.LOT_SIZE
        # Fallback ATR % if atr too small
        try:
            fallback_pct = float(getattr(config, "VOL_ATR_FALLBACK_PCT", 0.5)) / 100.0
            if atr < equity * fallback_pct * 0.01:  # sanity
                pass
        except:
            pass
        risk_amount = equity * risk_pct / 100.0
        lots = risk_amount / (atr * config.CONTRACT_SIZE)
        # Don't clamp to 0.01 here — let caller decide to skip if too small
        lots = min(lots, float(getattr(config, "MAX_LOT_SIZE", 1.0)))
        return float(lots)

    def _check_min_lot_skip(self, calculated_lots: float, info) -> Optional[str]:
        """M2: If broker min lot > calculated risk-based lots, skip trade (don't over-risk)."""
        try:
            if not bool(getattr(config, "MIN_LOT_SKIP", True)):
                return None
            min_lot = float(getattr(info, "volume_min", 0.01) or 0.01)
            if calculated_lots < min_lot - 1e-9:
                return (f"Vol-adjusted lots {calculated_lots:.4f} < broker min {min_lot:.2f} — "
                        f"would over-risk, SKIPPED (equity too small for ATR)")
        except Exception:
            pass
        return None

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

        # v5.2 C4: Basis risk check + v6.0 fast widen + CFD spread
        # Futures 4310 vs Spot 4265 = $45 basis normal, but if >50 or widening fast -> risk
        try:
            futures_price = float(getattr(snapshot, "price", 0.0) or 0.0)
            basis = futures_price - price if futures_price > 0 and price > 0 else 0.0
            basis_max = float(getattr(config, "BASIS_MAX", 50.0))
            basis_buffer_mult = float(getattr(config, "BASIS_BUFFER_MULT", 1.5))
            # v6.0 fast widen check: if basis jumps >1% in 5 min -> pause
            try:
                if getattr(config, "V6_4TEAMS_ENABLED", False):
                    fast_pct = float(getattr(config, "V6_BASIS_FAST_WIDEN_PCT", 1.0))
                    # Store last basis in memory
                    last_basis = float(getattr(self, "_last_basis_stored", basis) or basis)
                    basis_change_pct = abs(basis - last_basis) / max(abs(last_basis), 1.0) * 100.0
                    if basis_change_pct > fast_pct:
                        logger.warning("STEP 4 v6.0: Basis fast widen %.2f%% (%.2f -> %.2f) > %.2f%% — DEFERRED", basis_change_pct, last_basis, basis, fast_pct)
                        reason = f"Basis fast widen {basis_change_pct:.2f}% > {fast_pct}% — DEFERRED"
                        return ExecutionResult(status="DEFERRED", reason=reason, symbol=self.symbol, price=price, timestamp=now)
                    self._last_basis_stored = basis
            except Exception as be:
                logger.warning("STEP 4 v6.0 basis fast check error: %s", be)

            # v6.0 CFD spread max check
            try:
                if getattr(config, "V6_4TEAMS_ENABLED", False):
                    spread_max = float(getattr(config, "V6_CFD_SPREAD_MAX", 0.60))
                    # Estimate spread from tick if available
                    spread = 0.0
                    try:
                        # Try to get spread from market_data or tick
                        bid = float(getattr(snapshot, "bid", 0) or 0)
                        ask = float(getattr(snapshot, "ask", 0) or 0)
                        if bid>0 and ask>0:
                            spread = ask - bid
                    except:
                        spread = 0.0
                    if spread > spread_max and spread_max>0:
                        reason = f"CFD spread wide {spread:.2f} > max {spread_max} — DEFERRED"
                        logger.warning("STEP 4 v6.0: %s", reason)
                        return ExecutionResult(status="DEFERRED", reason=reason, symbol=self.symbol, price=price, timestamp=now)
            except Exception as se:
                logger.warning("STEP 4 v6.0 spread check error: %s", se)

            if abs(basis) > basis_max and basis_max > 0:
                logger.warning("STEP 4: Basis wide: futures %.2f spot %.2f basis %.2f > max %.2f — widening SL buffer %.1fx",
                               futures_price, price, basis, basis_max, basis_buffer_mult)
                # Will widen SL later via buffer multiplier
                # For now just log, but if basis > 1.5*max, skip
                if abs(basis) > basis_max * 1.5:
                    reason = f"Basis too wide: {basis:.2f} > {basis_max*1.5:.2f} (futures {futures_price:.2f} vs spot {price:.2f}) — DEFERRED"
                    logger.warning("STEP 4: %s", reason)
                    return ExecutionResult(status="DEFERRED", reason=reason,
                                           symbol=self.symbol, price=price,
                                           timestamp=now)
            # Store basis for later SL widening
            self._last_basis = basis
            self._last_futures = futures_price
        except Exception as e:
            logger.warning("STEP 4: Basis calc failed: %s", e)
            self._last_basis = 0.0
            self._last_futures = 0.0

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

        # v5.2 C4: Widen ATR if basis wide
        try:
            basis = float(getattr(self, "_last_basis", 0.0) or 0.0)
            basis_max = float(getattr(config, "BASIS_MAX", 50.0))
            basis_buffer_mult = float(getattr(config, "BASIS_BUFFER_MULT", 1.5))
            if abs(basis) > basis_max and basis_max > 0:
                atr *= basis_buffer_mult
                logger.info("STEP 4: Basis %.2f > max %.2f — ATR widened to %.2f (mult %.1fx)",
                            basis, basis_max, atr, basis_buffer_mult)
        except:
            pass

        # v4: structural stops — SL behind demand/supply zones (order blocks),
        # POC or strong round numbers; TP in front of opposing structure.
        # Falls back to the old ATR multiples when no structure exists.
        try:
            from position_manager import compute_structural_stops
            sl, tp, struct_notes = compute_structural_stops(
                action, price, atr, snapshot, news_state)
            if struct_notes:
                # Add basis info to notes
                try:
                    if abs(self._last_basis) > 5:
                        struct_notes.append(f"basis {self._last_basis:.2f} futures {self._last_futures:.2f}")
                except:
                    pass
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
        # v5.3 M2: Volatility-adjusted sizing + min lot skip guard
        equity = self._account_equity()
        stop_distance = abs(price - sl)

        # Calculate both traditional (stop-based) and vol-adjusted (ATR-based)
        traditional_lots = self.compute_lot_size(equity, stop_distance) if equity else config.LOT_SIZE

        # M2 vol-adjusted: lots = (equity * 1%) / (ATR * contract_size)
        vol_lots_raw = 0.0
        try:
            if bool(getattr(config, "VOL_ADJUSTED_LOTS", True)) and equity and atr > 0:
                vol_lots_raw = self.compute_vol_adjusted_lot_size(equity, atr)
                # Use the MORE CONSERVATIVE (smaller) of the two to avoid over-risk
                lot_size_candidate = min(traditional_lots, vol_lots_raw) if vol_lots_raw > 0 else traditional_lots
                logger.info(f"STEP 4 M2: equity ${equity:.2f} ATR {atr:.2f} stop {stop_distance:.2f} "
                            f"trad {traditional_lots:.4f} vol-adj {vol_lots_raw:.4f} -> using {lot_size_candidate:.4f}")
                lot_size = lot_size_candidate
            else:
                lot_size = traditional_lots
        except Exception as e:
            logger.warning(f"STEP 4 M2 calc failed {e}, using traditional")
            lot_size = traditional_lots

        # M2: Check if calculated lots < broker min -> skip (don't over-risk $1000 account with 0.01 lots)
        try:
            skip_reason = self._check_min_lot_skip(vol_lots_raw if vol_lots_raw > 0 else lot_size, info)
            if skip_reason:
                logger.info(f"STEP 4 M2: {skip_reason}")
                return ExecutionResult(status="SKIPPED", reason=skip_reason,
                                       symbol=self.symbol, price=price, sl=sl, tp=tp,
                                       volume=float(getattr(info, 'volume_min', 0.01)),
                                       timestamp=now)
        except Exception:
            pass

        if news_state == "WARNING":
            lot_size = lot_size * config.NEWS_REDUCE_SIZE_PCT  # shrink during news

        # L5 RL adaptive: reduce size after loss streak
        try:
            if getattr(config, "RL_ENABLED", True):
                import trade_history as th
                regime = str(getattr(snapshot, "regime", "") or "")
                mult = th.get_rl_size_multiplier(regime)
                if mult < 1.0:
                    if mult <= 0.0:
                        reason = f"RL pause: loss streak in regime {regime} -> no new entries"
                        logger.info(f"STEP 4 L5: {reason}")
                        return ExecutionResult(status="SKIPPED", reason=reason,
                                               symbol=self.symbol, price=price, sl=sl, tp=tp,
                                               volume=lot_size, timestamp=now)
                    old_lot = lot_size
                    lot_size = lot_size * mult
                    logger.info(f"STEP 4 L5 RL: size {old_lot:.4f} * {mult:.2f} -> {lot_size:.4f} regime {regime}")
        except Exception as e:
            logger.warning(f"STEP 4 L5 RL error: {e}")

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

        # B3 Smart Limit Queue v2 IMPROVED (2026-09-17): queue-aware limit placement
        # WHAT: Instead of market chase (cross spread, bad queue), place limit at best queue position
        # WHEN TO ENABLE: After GC live stable (feed age <1s, lines <100k, 20 bid/20 ask + 3000 MBO)
        # HOW IT WORKS:
        #  1. L2 imbalance against us -> place limit 1 tick better (don't chase)
        #  2. Iceberg support/resistance within $5 -> place limit AT iceberg (whale fills you)
        #  3. Queue position model (L3) -> if fill_prob < threshold, wait or place deeper
        #  4. Vol-adjusted offset: high vol -> closer to market (ensure fill), low vol -> further for price improvement
        # BENEFIT: -0.2 to -0.5 slippage vs market, better TCA, avoid spoof traps
        use_limit = False
        limit_price = price
        b3_reason = ""
        try:
            if getattr(config, "LIMIT_ORDER_ENABLED", False):
                l3 = getattr(snapshot, "level3", None)
                of = getattr(snapshot, "order_flow", None)
                vol_rank = float(getattr(getattr(snapshot, "volatility", None), "volatility_rank", 0.5) or 0.5)
                atr = float(getattr(getattr(snapshot, "volatility", None), "atr", 1.0) or 1.0)

                tick_size = float(getattr(config, "LIMIT_TICK_SIZE", 0.1))
                offset_ticks = int(getattr(config, "LIMIT_OFFSET_TICKS", 1))
                # Vol-adjusted offset: high vol (rank>0.6) -> 0 ticks (closer), low vol (<0.3) -> +1 tick extra for price improvement
                if vol_rank > 0.6:
                    offset_ticks = max(0, offset_ticks - 1)
                elif vol_rank < 0.3:
                    offset_ticks = offset_ticks + 1

                l2_imb = float(getattr(of, "depth_imbalance", 0.0) or 0.0)
                l3_imb = float(getattr(l3, "imbalance", 0.0) or 0.0) if l3 else 0.0
                microprice = float(getattr(getattr(snapshot, "order_flow", None), "microprice", 0.0) or 0.0)

                # Decision 1: L2 imbalance against us -> limit better
                if is_buy and l2_imb < -0.2:
                    use_limit = True
                    # Place at bid + offset, but not worse than microprice - 0.1
                    base = tick.bid if tick.bid>0 else price - tick_size
                    limit_price = base + offset_ticks * tick_size
                    b3_reason = f"L2 ask heavy imb {l2_imb:+.2f} -> limit bid+{offset_ticks}t"
                elif not is_buy and l2_imb > 0.2:
                    use_limit = True
                    base = tick.ask if tick.ask>0 else price + tick_size
                    limit_price = base - offset_ticks * tick_size
                    b3_reason = f"L2 bid heavy imb {l2_imb:+.2f} -> limit ask-{offset_ticks}t"

                # Decision 2: L3 imbalance stronger signal (institutional)
                if l3 and abs(l3_imb) > 0.3:
                    if is_buy and l3_imb < -0.3 and not use_limit:
                        use_limit = True
                        limit_price = tick.bid + offset_ticks * tick_size if tick.bid>0 else price - tick_size
                        b3_reason = f"L3 sell pressure imb {l3_imb:+.2f} -> limit"
                    elif not is_buy and l3_imb > 0.3 and not use_limit:
                        use_limit = True
                        limit_price = tick.ask - offset_ticks * tick_size if tick.ask>0 else price + tick_size
                        b3_reason = f"L3 buy pressure imb {l3_imb:+.2f} -> limit"

                # Decision 3: Iceberg support/resistance — BEST queue position (whale refill)
                # Place 0.1 behind iceberg so we are first after iceberg refills (queue advantage)
                if l3 and getattr(l3, "iceberg_levels", None):
                    if is_buy:
                        supports = [p for p in l3.iceberg_levels.keys() if p < price and p > price - 5.0]
                        if supports:
                            # Choose support with most refills (strongest)
                            best_support = max(supports, key=lambda p: l3.iceberg_levels.get(p,0))
                            refills = l3.iceberg_levels.get(best_support,0)
                            if refills >= 2:
                                use_limit = True
                                # Place 0.1 above iceberg to be next in queue after whale
                                limit_price = best_support + 0.1
                                b3_reason = f"iceberg support {best_support:.2f} x{refills} -> limit {limit_price:.2f} (queue behind whale)"
                                logger.info(f"STEP 4 B3: BUY limit at iceberg support {best_support:.2f} refills {refills} -> queue optimized {limit_price:.2f}")
                    else:
                        resistances = [p for p in l3.iceberg_levels.keys() if p > price and p < price + 5.0]
                        if resistances:
                            best_res = min(resistances, key=lambda p: ( -l3.iceberg_levels.get(p,0), p))
                            refills = l3.iceberg_levels.get(best_res,0)
                            if refills >= 2:
                                use_limit = True
                                limit_price = best_res - 0.1
                                b3_reason = f"iceberg resistance {best_res:.2f} x{refills} -> limit {limit_price:.2f}"
                                logger.info(f"STEP 4 B3: SELL limit at iceberg resistance {best_res:.2f} refills {refills} -> {limit_price:.2f}")

                # Decision 4: Spoof check — don't place limit where spoof wall exists (trap)
                if l3 and getattr(l3, "spoof_levels", None) and use_limit:
                    for spoof_price, count in l3.spoof_levels.items():
                        if abs(spoof_price - limit_price) < 0.3 and count >= 1:
                            # Spoof near our limit -> adjust away
                            if is_buy:
                                limit_price = spoof_price - 0.5
                            else:
                                limit_price = spoof_price + 0.5
                            b3_reason += f" + spoof avoid {spoof_price:.2f}"
                            logger.info(f"STEP 4 B3: spoof avoid {spoof_price:.2f} count {count} -> adjusted limit {limit_price:.2f}")

                # Decision 5: Queue position model — if enabled, check fill prob
                try:
                    if getattr(config, "QUEUE_POS_ENABLED", True) and l3 and hasattr(l3, "order_book"):
                        # Simple fill prob: if our limit is far from microprice, low prob
                        if microprice > 0:
                            distance = abs(limit_price - microprice)
                            # If distance > 2*ATR, fill prob low -> don't use limit, use market instead
                            if distance > atr * 2.0 and vol_rank > 0.5:
                                logger.info(f"STEP 4 B3: limit {limit_price:.2f} far from micro {microprice:.2f} dist {distance:.2f} > 2*ATR {atr*2:.2f} high vol -> use market instead")
                                use_limit = False
                except Exception:
                    pass

                if use_limit:
                    logger.info(f"STEP 4 B3 Smart Limit v2: {action} market {price:.2f} -> limit {limit_price:.2f} reason: {b3_reason} (L2 {l2_imb:+.2f} L3 {l3_imb:+.2f} vol_rank {vol_rank:.2f})")
        except Exception as e:
            logger.warning(f"STEP 4 B3 queue check failed: {e}")
            use_limit = False

        if use_limit:
            # Place LIMIT order
            request = {
                "action": mt5.TRADE_ACTION_PENDING,
                "symbol": self.symbol,
                "volume": lot_size,
                "type": mt5.ORDER_TYPE_BUY_LIMIT if is_buy else mt5.ORDER_TYPE_SELL_LIMIT,
                "price": limit_price,
                "sl": sl,
                "tp": tp,
                "deviation": 20,
                "magic": self.magic,
                "comment": "gold-bot B3 limit",
                "type_time": mt5.ORDER_TIME_GTC,
                "type_filling": self._select_filling_mode(info),
            }
        else:
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
        # For pending limit orders, DONE means order placed, not filled yet
        if use_limit:
            if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
                logger.error("STEP 4 B3: limit order_send failed retcode=%s",
                             getattr(result, "retcode", None))
                return ExecutionResult(status="ERROR",
                                       reason=f"limit order_send retcode={getattr(result, 'retcode', None)}",
                                       symbol=self.symbol, volume=lot_size,
                                       price=limit_price, sl=sl, tp=tp, timestamp=now)
            logger.info(f"STEP 4 B3: LIMIT {action} placed @ {limit_price:.2f} lots {lot_size} (market was {price:.2f}) — waiting fill")
            try:
                timeout = int(getattr(config, "LIMIT_TIMEOUT_SECONDS", 10))
                time.sleep(min(timeout, 2))
                pos = self._wait_for_bot_position(timeout=timeout)
                if pos:
                    verified_position = pos
                else:
                    return ExecutionResult(status="DEFERRED",
                                           reason=f"B3 limit {action} @ {limit_price:.2f} placed, waiting fill (queue optimized)",
                                           order_id=getattr(result, "order", None),
                                           symbol=self.symbol, volume=lot_size,
                                           price=limit_price, sl=sl, tp=tp, timestamp=now)
            except Exception as e:
                logger.warning(f"STEP 4 B3 limit wait error: {e}")
                return ExecutionResult(status="DEFERRED",
                                       reason=f"B3 limit placed @ {limit_price:.2f}, wait error {e}",
                                       order_id=getattr(result, "order", None),
                                       symbol=self.symbol, volume=lot_size,
                                       price=limit_price, sl=sl, tp=tp, timestamp=now)

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
                                           0.0) or 0.0),
                regime=str(getattr(snapshot, "regime", "") or ""),
                volatility_rank=float(getattr(getattr(snapshot, "volatility", None), "volatility_rank", 0.0) or 0.0),
                atr=float(getattr(getattr(snapshot, "volatility", None), "atr", 0.0) or 0.0),
                snapshot_notes="; ".join(getattr(snapshot, "notes", [])[-3:]))
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
