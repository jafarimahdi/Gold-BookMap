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
from pathlib import Path
from typing import Optional, Tuple

import config

logger = logging.getLogger(__name__)

STATE_FILE = config.DATA_DIR / "trade_guard_state.json"
_DEFAULT_STATE_FILE = STATE_FILE


def _resolve_state_file() -> Path:
    if STATE_FILE != _DEFAULT_STATE_FILE:
        return Path(STATE_FILE)
    live_data_dir = getattr(config, "DATA_DIR", _DEFAULT_STATE_FILE.parent)
    return Path(live_data_dir) / "trade_guard_state.json"


class TradeGuard:
    """Cooldown + daily trade cap + Stage-4R post-exit cooldown & daily loss circuit breaker."""

    def __init__(self, cooldown_minutes: Optional[float] = None,
                 max_trades_per_day: Optional[int] = None,
                 post_exit_cooldown_minutes: Optional[float] = None,
                 max_daily_loss_pts: Optional[float] = None):
        s4r_cd_on = bool(getattr(config, "STAGE4R_POST_EXIT_COOLDOWN_ENABLED", True))
        s4r_cd_min = float(
            getattr(
                config,
                "STAGE4R_POST_EXIT_COOLDOWN_MINUTES",
                getattr(config, "BASE_TF_MINUTES", 3),
            ) or 0.0
        )
        if cooldown_minutes is not None:
            self.cooldown_minutes = float(cooldown_minutes)
        elif s4r_cd_on:
            self.cooldown_minutes = s4r_cd_min
        else:
            self.cooldown_minutes = float(getattr(config, "COOLDOWN_MINUTES", 3) or 0.0)

        if post_exit_cooldown_minutes is not None:
            self.post_exit_cooldown_minutes = float(post_exit_cooldown_minutes)
        elif cooldown_minutes is not None:
            self.post_exit_cooldown_minutes = float(cooldown_minutes)
        elif s4r_cd_on:
            self.post_exit_cooldown_minutes = s4r_cd_min
        else:
            self.post_exit_cooldown_minutes = 0.0

        self.max_trades_per_day = (max_trades_per_day if max_trades_per_day is not None
                                   else config.MAX_TRADES_PER_DAY)

        s4r_dl_on = bool(getattr(config, "STAGE4R_DAILY_LOSS_BREAKER_ENABLED", True))
        if max_daily_loss_pts is not None:
            self.max_daily_loss_pts = max(0.0, float(max_daily_loss_pts))
        elif s4r_dl_on:
            self.max_daily_loss_pts = max(0.0, float(getattr(config, "STAGE4R_DAILY_MAX_LOSS_PTS", 12.0) or 0.0))
        else:
            self.max_daily_loss_pts = 0.0

    # -- persisted state -------------------------------------------------------
    def load_state(self) -> dict:
        sf = _resolve_state_file()
        if sf.exists():
            try:
                return json.loads(sf.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def save_state(self, state: dict) -> None:
        sf = _resolve_state_file()
        try:
            sf.parent.mkdir(parents=True, exist_ok=True)
            sf.write_text(json.dumps(state), encoding="utf-8")
        except OSError as exc:
            logger.warning("Could not persist trade-guard state: %s", exc)

    def reset(self) -> None:
        try:
            _resolve_state_file().unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _now(now: Optional[datetime] = None) -> datetime:
        return now or datetime.now(timezone.utc)

    @staticmethod
    def _day_key(now: datetime) -> str:
        """24m/C3: 'today' for MAX_TRADES_PER_DAY must be the SAME day the report uses.
        This used to be the UTC date, which rolls over at 02:00 Budapest - so a trade at
        01:30 counted against the previous day's budget while the report filed it under
        the new one. The trading window is 08:00-23:00 local, entirely inside one local
        day, so the local date is the only reading that matches the operator's day."""
        try:
            from zoneinfo import ZoneInfo
            import config as _cfg
            tz = ZoneInfo(str(getattr(_cfg, "LOCAL_TZ", "Europe/Budapest")))
            return now.astimezone(tz).date().isoformat()
        except Exception:
            return now.date().isoformat()

    def _today_state(self, now: datetime) -> dict:
        """Return the state normalised to today (resets daily counters)."""
        today = self._day_key(now)
        state = self.load_state()
        if state.get("date") != today:
            state = {"date": today, "trades_today": 0, "last_trade_ts": None}
        return state

    # -- checks ----------------------------------------------------------------
    def can_trade(self, now: Optional[datetime] = None) -> Tuple[bool, str]:
        """Return (allowed, reason)."""
        now = self._now(now)
        state = self._today_state(now)

        # Stage 4R: intraday realized daily-loss circuit breaker (in Gold points)
        if self.max_daily_loss_pts > 0:
            try:
                import trade_history as _th
                day_pts = float(_th.day_realized_pts(now=now))
                if day_pts <= -self.max_daily_loss_pts:
                    return (
                        False,
                        f"daily loss circuit breaker hit ({day_pts:+.2f} pts <= -{self.max_daily_loss_pts:.2f} pts)",
                    )
            except Exception:
                pass

        # daily cap
        if self.max_trades_per_day > 0:
            done = int(state.get("trades_today", 0) or 0)
            if done >= self.max_trades_per_day:
                return False, f"daily trade cap reached ({self.max_trades_per_day})"

        # entry-to-entry anti-double-click cooldown
        if self.cooldown_minutes > 0:
            last = state.get("last_trade_ts")
            if last:
                try:
                    last_dt = datetime.fromisoformat(last)
                    if last_dt.tzinfo is None:
                        last_dt = last_dt.replace(tzinfo=timezone.utc)
                    elapsed = (now - last_dt).total_seconds()
                    if 0.0 <= elapsed < self.cooldown_minutes * 60.0:
                        left = self.cooldown_minutes - elapsed / 60.0
                        return False, f"cooldown active ({left:.1f} min left)"
                except (ValueError, TypeError):
                    pass

        # Stage 4R (Law #11): post-exit cooldown measured from last actual close
        if self.post_exit_cooldown_minutes > 0:
            try:
                import trade_history as _th
                last_close_ep = _th.last_close_epoch(now=now)
                if last_close_ep is not None:
                    now_ep = (now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)).timestamp()
                    elapsed_ex = now_ep - float(last_close_ep)
                    if 0.0 <= elapsed_ex < self.post_exit_cooldown_minutes * 60.0:
                        left_ex = self.post_exit_cooldown_minutes - elapsed_ex / 60.0
                        return False, f"post-exit cooldown active ({left_ex:.1f} min left)"
            except Exception:
                pass

        return True, "ok"

    def record_trade(self, now: Optional[datetime] = None) -> None:
        """Call this AFTER a position was actually opened."""
        now = self._now(now)
        state = self._today_state(now)
        state["trades_today"] = int(state.get("trades_today", 0) or 0) + 1
        state["last_trade_ts"] = now.isoformat()
        self.save_state(state)
        logger.info("TradeGuard: recorded trade #%d today (cooldown %.1f min)",
                    state["trades_today"], self.cooldown_minutes)
