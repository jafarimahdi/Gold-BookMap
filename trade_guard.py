"""
trade_guard.py
==============
Anti-overtrading guard — cooldown + daily trade cap.

Why this is important: a scalper bot that opens a new position on every tiny
signal will "churn" — flip-flop BUY/SELL rapidly and pay the spread on every
flip, bleeding the account even when the market barely moves. Two simple
limits stop that:

  - COOLDOWN_MINUTES   : no new position within N minutes of the last one.
  - MAX_TRADES_PER_DAY : no more than N new positions per day.

The state is persisted to data/trade_guard_state.json, so a restart does NOT
reset the cooldown or the daily count. It resets automatically at UTC midnight.

Used by main.py's safety gates (can_trade) and after a successful execution
(record_trade).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

import config

logger = logging.getLogger(__name__)

STATE_FILE = config.DATA_DIR / "trade_guard_state.json"


class TradeGuard:
    """Cooldown + daily trade cap."""

    def __init__(self, cooldown_minutes: Optional[int] = None,
                 max_trades_per_day: Optional[int] = None):
        self.cooldown_minutes = (cooldown_minutes if cooldown_minutes is not None
                                 else config.COOLDOWN_MINUTES)
        self.max_trades_per_day = (max_trades_per_day if max_trades_per_day is not None
                                   else config.MAX_TRADES_PER_DAY)

    # -- persisted state -------------------------------------------------------
    def load_state(self) -> dict:
        if STATE_FILE.exists():
            try:
                return json.loads(STATE_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def save_state(self, state: dict) -> None:
        try:
            STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not persist trade-guard state: %s", exc)

    def reset(self) -> None:
        try:
            STATE_FILE.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _now(now: Optional[datetime] = None) -> datetime:
        return now or datetime.now(timezone.utc)

    def _today_state(self, now: datetime) -> dict:
        """Return the state normalised to today (resets daily counters)."""
        today = now.date().isoformat()
        state = self.load_state()
        if state.get("date") != today:
            state = {"date": today, "trades_today": 0, "last_trade_ts": None}
        return state

    # -- checks ----------------------------------------------------------------
    def can_trade(self, now: Optional[datetime] = None) -> Tuple[bool, str]:
        """Return (allowed, reason)."""
        now = self._now(now)
        state = self._today_state(now)

        # daily cap
        if self.max_trades_per_day > 0:
            done = int(state.get("trades_today", 0) or 0)
            if done >= self.max_trades_per_day:
                return False, f"daily trade cap reached ({self.max_trades_per_day})"

        # cooldown
        if self.cooldown_minutes > 0:
            last = state.get("last_trade_ts")
            if last:
                try:
                    last_dt = datetime.fromisoformat(last)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    elapsed = (now - last_dt).total_seconds()
                    if elapsed < self.cooldown_minutes * 60:
                        left = self.cooldown_minutes - elapsed / 60.0
                        return False, f"cooldown active ({left:.0f} min left)"
                except (ValueError, TypeError):
                    pass

        return True, "ok"

    def record_trade(self, now: Optional[datetime] = None) -> None:
        """Call this AFTER a position was actually opened."""
        now = self._now(now)
        state = self._today_state(now)
        state["trades_today"] = int(state.get("trades_today", 0) or 0) + 1
        state["last_trade_ts"] = now.isoformat()
        self.save_state(state)
        logger.info("TradeGuard: recorded trade #%d today (cooldown %d min)",
                    state["trades_today"], self.cooldown_minutes)
