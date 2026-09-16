"""
step5_monitoring.py
===================
STEP 5: MONITORING & LOOP

Tracks open trades and drives the continuous loop back to Step 2.

`single_pass()` reports open positions (requires MT5; safe no-op otherwise).
`run_loop()` repeatedly calls a caller-supplied pipeline function (e.g.
main.run_pipeline) on a fixed interval.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable, Iterable, List, Optional

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None

import config

logger = logging.getLogger(__name__)


class TradeMonitor:
    """Monitors open trades and re-runs the pipeline in a loop."""

    def __init__(self, poll_seconds: Optional[int] = None):
        self.poll_seconds = (config.MONITOR_POLL_SECONDS if poll_seconds is None
                             else poll_seconds)

    # ------------------------------------------------------------------ #
    def check_open_trades(self) -> List[dict]:
        """Return open positions (empty list when MT5 unavailable)."""
        if mt5 is None:
            logger.info("STEP 5: MetaTrader5 SDK not installed -> no live "
                        "positions to monitor (demo mode).")
            return []
        if not config.mt5_initialize(mt5):
            logger.error("STEP 5: mt5.initialize() failed")
            return []
        try:
            positions = mt5.positions_get()
            mt5.shutdown()
            out = []
            for pos in positions or []:
                out.append({
                    "ticket": pos.ticket, "symbol": pos.symbol,
                    "type": "BUY" if pos.type == mt5.POSITION_TYPE_BUY else "SELL",
                    "volume": pos.volume, "price_open": pos.price_open,
                    "sl": pos.sl, "tp": pos.tp, "profit": pos.profit,
                })
            return out
        except Exception:
            logger.exception("STEP 5: position query failed")
            return []

    # ------------------------------------------------------------------ #
    @staticmethod
    def _deal_rows(deals: Iterable, position_ids: Optional[set] = None) -> List[dict]:
        """Convert MT5 deal objects to close-deal rows without duplicates."""
        out = []
        seen = set()
        position_ids = position_ids or set()
        opening_sides = {}
        for opening in deals or []:
            if getattr(opening, "entry", None) in (0, 2):
                opening_position = getattr(
                    opening, "position_id", getattr(opening, "ticket", None))
                opening_sides[opening_position] = (
                    "BUY" if getattr(opening, "type", 1) == 0 else "SELL")
        for d in deals or []:
            entry = getattr(d, "entry", None)
            if entry != 1:            # DEAL_ENTRY_OUT == 1
                continue
            deal_id = getattr(d, "ticket", None)
            position_id = getattr(d, "position_id", getattr(d, "ticket", None))
            # A position-specific query is already scoped to the bot's known
            # position. The extra magic/position check protects date fallbacks.
            magic = getattr(d, "magic", None)
            if position_ids and position_id not in position_ids and magic != 234000:
                continue
            unique_key = deal_id if deal_id not in (None, "", 0) else (
                position_id, getattr(d, "symbol", ""), entry,
                getattr(d, "time_msc", getattr(d, "time", "")))
            if unique_key in seen:
                continue
            seen.add(unique_key)
            closing_side = "BUY" if getattr(d, "type", 1) == 0 else "SELL"
            out.append({
                # `ticket` is the unique MT5 deal ID. Keep it separate
                # from position_id because one position can have more
                # than one deal (partial closes, reversals, etc.).
                "deal_id": deal_id,
                "position_id": position_id,
                "symbol": getattr(d, "symbol", ""),
                # `side` is the original entry direction. `type` remains the
                # actual closing transaction side for compatibility/debugging.
                "side": opening_sides.get(position_id, closing_side),
                "type": closing_side,
                "price": float(getattr(d, "price", 0.0) or 0.0),
                "pnl": round(float(getattr(d, "profit", 0.0) or 0.0)
                             + float(getattr(d, "swap", 0.0) or 0.0)
                             + float(getattr(d, "commission", 0.0) or 0.0), 2),
            })
        return out

    def check_closed_deals(self, since: Optional[datetime] = None,
                           position_ids: Optional[Iterable[int]] = None) -> List[dict]:
        """Return closed bot deals for tracked positions.

        Pepperstone's date-range history query can return balance records while
        omitting the XAUUSD trade deals. When position IDs are known, query MT5
        by position instead; this is the verified working method. The date
        query remains only as a compatibility fallback when no IDs are known.
        """
        if mt5 is None:
            return []
        tracked = set()
        for value in position_ids or []:
            try:
                number = int(value)
                if number > 0:
                    tracked.add(number)
            except (TypeError, ValueError):
                continue
        try:
            if not config.mt5_initialize(mt5):
                return []

            if tracked:
                # This is the reliable Pepperstone path: the user's verified
                # position 86160935 returns both deal 57028893 and deal
                # 57028912 when queried with position=86160935.
                deals = []
                for position_id in sorted(tracked):
                    try:
                        deals.extend(mt5.history_deals_get(
                            position=position_id) or [])
                    except (TypeError, ValueError):
                        # Older MT5 Python builds may not support the keyword;
                        # the normal date fallback below remains available.
                        continue
                rows = self._deal_rows(deals, tracked)
                if rows or deals:
                    return rows

            since = since or datetime.now(timezone.utc).replace(
                hour=0, minute=0, second=0, microsecond=0)
            now = datetime.now(timezone.utc)
            deals = mt5.history_deals_get(since, now) or []
            # Do not use a future end date (such as 2100-01-01): this broker
            # terminal returns no deals for that wide range. A date fallback
            # is retained only for callers with no tracked position IDs.
            return self._deal_rows(deals)
        except Exception as exc:
            logger.warning("STEP 5: closed-deal query failed: %s", exc)
            return []
        finally:
            try:
                mt5.shutdown()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    def single_pass(self) -> List[dict]:
        """One monitoring pass (used by main.py); return open positions."""
        positions = self.check_open_trades()
        if positions:
            for p in positions:
                logger.info("STEP 5: open %s %s %s @ %s (profit %s)",
                            p["type"], p["symbol"], p["volume"],
                            p["price_open"], p["profit"])
        else:
            logger.info("STEP 5: no open trades.")
        return positions

    # ------------------------------------------------------------------ #
    def run_loop(self, pipeline_fn: Callable[[], None],
                 max_iterations: Optional[int] = None) -> None:
        """Run pipeline_fn every poll_seconds; Ctrl-C to stop.

        pipeline_fn is typically main.run_pipeline.
        """
        iteration = 0
        logger.info("STEP 5: starting monitoring loop "
                    "(every %ds). Ctrl-C to stop.", self.poll_seconds)
        try:
            while max_iterations is None or iteration < max_iterations:
                iteration += 1
                logger.info("STEP 5: --- loop iteration %d ---", iteration)
                pipeline_fn()
                time.sleep(self.poll_seconds)
        except KeyboardInterrupt:
            logger.info("STEP 5: loop stopped by user.")
