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

import calendar
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
    # Budapest trading hours filter: 08:00-23:00 Budapest = 06:00-21:00 UTC
    try:
        if getattr(config, "BUDAPEST_TRADING_ONLY", False):
            if not is_budapest_trading_hours(now):
                return False
    except:
        pass
    return True


def day_of_week(now: Optional[datetime] = None) -> str:
    return _DAY_NAMES[_now(now).weekday()]


def hour_of_day(now: Optional[datetime] = None) -> int:
    """UTC hour (0-23)."""
    return _now(now).hour


def time_of_day(now: Optional[datetime] = None) -> str:
    """Broad time bucket: ASIA / LONDON / NEW_YORK / OFF_HOURS + BUDAPEST."""
    h = hour_of_day(now)
    if 0 <= h < 7:
        return "ASIA"
    if 7 <= h < 12:
        return "LONDON"
    if 12 <= h < 21:
        return "NEW_YORK"
    return "OFF_HOURS"

def _last_sunday(year: int, month: int):
    """Naive datetime of the last Sunday of that month (EU DST switches)."""
    last = datetime(year, month, calendar.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() + 1) % 7)


def _eu_offset_hours(utc_naive: datetime) -> int:
    """2 while EU summer time is on, 1 in winter. Rule: last Sunday of March 01:00 UTC
    until last Sunday of October 01:00 UTC (what Budapest has done since 1996)."""
    start = _last_sunday(utc_naive.year, 3).replace(hour=1)
    end = _last_sunday(utc_naive.year, 10).replace(hour=1)
    return 2 if start <= utc_naive < end else 1


def budapest_hour(now: Optional[datetime] = None) -> int:
    """Hour in Budapest, correct all year - including the day the clock changes.

    This used to be a hardcoded "+2 (Sep)", which is right for summer and silently an
    hour wrong from the last Sunday of October on: the 08:00-23:00 trading gate would
    have slid to 07:00-22:00 on its own. Tries the system tz database first and falls
    back to the EU rule above, so a Windows box without tzdata still gets the true hour
    instead of a wrong one.
    """
    n = now or datetime.now(timezone.utc)
    if n.tzinfo is None:
        n = n.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return n.astimezone(ZoneInfo("Europe/Budapest")).hour
    except Exception:
        pass
    u = n.astimezone(timezone.utc).replace(tzinfo=None)
    return (u.hour + _eu_offset_hours(u)) % 24

def is_budapest_trading_hours(now: Optional[datetime] = None) -> bool:
    """User requested: trade 08:00-23:00 Budapest = 06:00-21:00 UTC."""
    try:
        # Configurable: BUDAPEST_START=08:00, BUDAPEST_END=23:00 local
        start_str = str(getattr(config, "BUDAPEST_START", "08:00") or "08:00")
        end_str = str(getattr(config, "BUDAPEST_END", "23:00") or "23:00")
        # Parse Budapest local times, convert to UTC
        # real Budapest local time (DST-correct), not a fixed offset constant
        b_h = budapest_hour(now)
        # Parse start/end hour
        s_h = int(start_str.split(":")[0])
        e_h = int(end_str.split(":")[0])
        if s_h <= e_h:
            return s_h <= b_h < e_h
        else:
            return b_h >= s_h or b_h < e_h
    except:
        # Fallback: 06-21 UTC = 08-23 Budapest
        h = hour_of_day(now)
        return 6 <= h < 21


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


def budapest_offset_hours(now: Optional[datetime] = None) -> int:
    """UTC offset in hours for Europe/Budapest (+1 CET winter, +2 CEST summer)."""
    n = now or datetime.now(timezone.utc)
    if n.tzinfo is None:
        n = n.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        off = n.astimezone(ZoneInfo("Europe/Budapest")).utcoffset()
        if off is not None:
            return int(round(off.total_seconds() / 3600.0))
    except Exception:
        pass
    u = n.astimezone(timezone.utc).replace(tzinfo=None)
    return _eu_offset_hours(u)


def session_context(now: Optional[datetime] = None) -> Dict[str, str]:
    """Everything the AI needs to know about 'when' we are (for the prompt)."""
    now = _now(now)
    session, overlap = trading_session(now)
    b_h = budapest_hour(now)
    b_off = budapest_offset_hours(now)
    is_bud = is_budapest_trading_hours(now)
    return {
        "day_of_week": day_of_week(now),
        "time_of_day_utc": now.strftime("%H:%M"),
        "time_of_day_budapest": f"{b_h:02d}:{now.strftime('%M')} (Budapest, UTC{b_off:+d})",
        "session": session,
        "session_overlap": overlap or "none",
        "budapest_trading": "yes" if is_bud else "no (outside 08:00-23:00 Budapest)",
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
