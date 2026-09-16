"""
session.py
==========
Trading-session calendar + day/time intelligence.

Tells the bot (and the AI) WHICH session we are in, the day of the week, and
the time of day. Gold behaves differently per session:

  - Sydney (21:00-06:00 UTC)  : thin, quiet, often a range
  - Tokyo  (00:00-09:00 UTC)  : Asia flows, sometimes gold moves
  - London (07:00-16:00 UTC)  : high volume, trend moves, big intraday swings
  - New York (12:00-21:00 UTC): highest volume, news reactions, reversals

All times are UTC. MT5 local time varies, so the bot always works in UTC.

Functions:
    describe_now()      -> "WEEKEND", "DAILY BREAK", or "OPEN"
    is_market_open()    -> True when trading is allowed (session window)
    day_of_week()       -> "Monday" .. "Sunday"
    time_of_day()       -> "ASIA", "LONDON", "NEW_YORK", "OFF_HOURS"
    trading_session()   -> detailed session name + overlap info
    session_context()   -> dict for the AI prompt (day, time, session, open?)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Set, Tuple

import config

logger = logging.getLogger(__name__)

_DAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
              "Saturday", "Sunday")


def _now(now: Optional[datetime] = None) -> datetime:
    return now or datetime.now(timezone.utc)


def _trading_days() -> Set[int]:
    days = set()
    for part in str(config.TRADING_DAYS).split(","):
        part = part.strip()
        if part == "":
            continue
        try:
            d = int(part)
            if 0 <= d <= 6:
                days.add(d)
        except ValueError:
            continue
    return days or {0, 1, 2, 3, 4}


def _in_daily_break(now: datetime) -> bool:
    start = str(config.DAILY_BREAK_START or "").strip()
    end = str(config.DAILY_BREAK_END or "").strip()
    if not start or not end:
        return False
    t = now.strftime("%H:%M")
    if start <= end:
        return start <= t < end
    return t >= start or t < end


def is_market_open(now: Optional[datetime] = None,
                   enforce: Optional[bool] = None) -> bool:
    if enforce is None:
        enforce = config.SESSION_ENFORCE
    if not enforce:
        return True
    now = _now(now)
    if now.weekday() not in _trading_days():
        return False
    if _in_daily_break(now):
        return False
    return True


def day_of_week(now: Optional[datetime] = None) -> str:
    return _DAY_NAMES[_now(now).weekday()]


def hour_of_day(now: Optional[datetime] = None) -> int:
    """UTC hour (0-23)."""
    return _now(now).hour


def time_of_day(now: Optional[datetime] = None) -> str:
    """Broad time bucket: ASIA / LONDON / NEW_YORK / OFF_HOURS."""
    h = hour_of_day(now)
    if 0 <= h < 7:
        return "ASIA"
    if 7 <= h < 12:
        return "LONDON"
    if 12 <= h < 21:
        return "NEW_YORK"
    return "OFF_HOURS"


def trading_session(now: Optional[datetime] = None) -> Tuple[str, str]:
    """Return (primary_session, overlap) describing where we are right now.

    Sessions (UTC):
      Sydney 21:00-06:00, Tokyo 00:00-09:00, London 07:00-16:00,
      New York 12:00-21:00.
    Overlaps (the most active times) are flagged explicitly.
    """
    h = hour_of_day(now)
    active = []
    if 21 <= h or h < 6:
        active.append("SYDNEY")
    if h < 9:
        active.append("TOKYO")
    if 7 <= h < 16:
        active.append("LONDON")
    if 12 <= h < 21:
        active.append("NEW_YORK")

    if not active:
        return "OFF_HOURS", ""

    overlap = ""
    if "LONDON" in active and "NEW_YORK" in active:
        overlap = "LONDON+NEW_YORK (high volume)"
    elif "TOKYO" in active and "LONDON" in active:
        overlap = "TOKYO+LONDON"
    elif "SYDNEY" in active and "TOKYO" in active:
        overlap = "SYDNEY+TOKYO (thin)"

    return active[0], overlap


def session_context(now: Optional[datetime] = None) -> Dict[str, str]:
    """Everything the AI needs to know about 'when' we are (for the prompt)."""
    now = _now(now)
    session, overlap = trading_session(now)
    return {
        "day_of_week": day_of_week(now),
        "time_of_day_utc": now.strftime("%H:%M"),
        "session": session,
        "session_overlap": overlap or "none",
        "market_open": "yes" if is_market_open(now) else "no",
    }


def describe_now(now: Optional[datetime] = None) -> str:
    now = _now(now)
    if now.weekday() not in _trading_days():
        return f"WEEKEND/HOLIDAY ({_DAY_NAMES[now.weekday()]})"
    if _in_daily_break(now):
        return f"DAILY BREAK ({config.DAILY_BREAK_START}-{config.DAILY_BREAK_END} UTC)"
    return "OPEN"


def next_open_time(now: Optional[datetime] = None,
                   step_minutes: int = 15,
                   horizon_days: int = 7) -> Optional[datetime]:
    now = _now(now)
    steps = int(horizon_days * 24 * 60 / step_minutes)
    for i in range(1, steps + 1):
        t = now + timedelta(minutes=i * step_minutes)
        if is_market_open(t):
            return t
    return None
