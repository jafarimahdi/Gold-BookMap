"""Analysis-only fill simulator between Shooting and Escort.

This module never contacts a broker or submits an order. It models passive limit
orders as pending until price trades through the limit by a tick, and models a
validated aggressive plan at its quoted market estimate. Escort only receives a
plan after this simulator reports FILLED.
"""
from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any, Dict, Optional

__all__ = ["simulate_entry", "reset_entry_simulator"]

_PENDING: Optional[Dict[str, Any]] = None
_LAST_FILLED_KEY: Optional[tuple] = None
_LAST_STATUS: Dict[str, Any] = {"status": "IDLE", "reason": "no paper entry yet"}


def _num(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _now_seconds(value: Any = None) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    number = _num(value)
    return number if number is not None else time.time()


def reset_entry_simulator() -> None:
    """Clear in-memory paper state; useful for isolated tests/replay boundaries."""
    global _PENDING, _LAST_FILLED_KEY, _LAST_STATUS
    _PENDING = None
    _LAST_FILLED_KEY = None
    _LAST_STATUS = {"status": "IDLE", "reason": "reset"}


def _key(plan: Dict[str, Any]) -> tuple:
    door = plan.get("door") or {}
    door_price = _num(door.get("price"))
    entry, target, stop = (_num(plan.get(k)) for k in ("entry", "target", "stop"))
    return (str(plan.get("side")), round(door_price or 0.0, 4),
            str(plan.get("entry_style")), round(entry or 0.0, 4),
            round(target or 0.0, 4), round(stop or 0.0, 4))


def _filled_plan(plan: Dict[str, Any], fill_price: float, fill_model: str) -> Dict[str, Any]:
    out = dict(plan)
    out["entry"] = round(fill_price, 6)
    out["entry_filled"] = True
    out["paper_fill_model"] = fill_model
    out["execution_status"] = "PAPER_ONLY"
    return out


def _wait_or_cancel(reason: str) -> Dict[str, Any]:
    global _PENDING, _LAST_STATUS
    status = "CANCELLED" if _PENDING is not None else "WAIT"
    _PENDING = None
    _LAST_STATUS = {"status": status, "reason": reason}
    return {**_LAST_STATUS, "pending": None, "filled_plan": None}


def simulate_entry(plan: Dict[str, Any], price: float, *, config: Any = None,
                   now: Any = None) -> Dict[str, Any]:
    """Stage/fill/cancel a paper entry and return the plan Escort may manage."""
    global _PENDING, _LAST_FILLED_KEY, _LAST_STATUS
    current = _num(price)
    timestamp = _now_seconds(now)
    if not isinstance(plan, dict) or plan.get("shot") != "GO" or not plan.get("side"):
        if _PENDING is not None:
            _PENDING = None
            _LAST_STATUS = {"status": "CANCELLED", "reason": "Shooting no longer says GO"}
        else:
            _LAST_STATUS = {"status": "NO_PLAN", "reason": "no eligible Shooting plan"}
        return {**_LAST_STATUS, "pending": None, "filled_plan": None}
    if current is None or current <= 0:
        return _wait_or_cancel("invalid last price; any pending paper limit was cancelled")

    style = plan.get("entry_style")
    side = str(plan.get("side", "")).upper()
    entry, target, stop = (_num(plan.get(k)) for k in ("entry", "target", "stop"))
    if (style not in {"PASSIVE_LIMIT", "AGGRESSIVE_MARKET"} or side not in {"BUY", "SELL"}
            or entry is None or target is None or stop is None or entry <= 0):
        return _wait_or_cancel("invalid entry style, side, or bracket prices")
    bracket_ok = target > entry > stop if side == "BUY" else target < entry < stop
    if not bracket_ok:
        return _wait_or_cancel("target/stop are on unsafe sides of entry")
    desired_key = _key(plan)
    if _LAST_FILLED_KEY == desired_key:
        _LAST_STATUS = {"status": "ALREADY_FILLED", "reason": "same plan was already paper-filled"}
        return {**_LAST_STATUS, "pending": None, "filled_plan": None}
    if _LAST_FILLED_KEY is not None and _LAST_FILLED_KEY != desired_key:
        _LAST_FILLED_KEY = None

    if style == "AGGRESSIVE_MARKET":
        options = plan.get("entry_options") or {}
        aggressive = options.get("AGGRESSIVE_MARKET") or {}
        reason = str(aggressive.get("trigger_reason") or "").strip()
        if aggressive.get("eligible") is not True or not reason:
            return _wait_or_cancel("aggressive plan lacks a validated trigger")
        _PENDING = None
        _LAST_FILLED_KEY = desired_key
        filled = _filled_plan(plan, entry, "aggressive_quote_estimate")
        _LAST_STATUS = {"status": "FILLED", "reason": "paper market-entry estimate",
                        "entry_style": style, "fill_price": entry}
        return {**_LAST_STATUS, "pending": None, "filled_plan": filled}

    # A passive order needs a real future trade-through to count as a fill; merely
    # touching the quote is not treated as served because queue priority is unknown.
    ttl = _num(getattr(config, "SHOOT_PAPER_LIMIT_TTL_SECONDS", 300.0)) if config else 300.0
    ttl = max(1.0, ttl or 300.0)
    tick = _num(getattr(config, "LIMIT_TICK_SIZE", 0.1)) if config else 0.1
    tick = tick if tick is not None and tick > 0 else 0.1
    if _PENDING and _PENDING.get("key") != desired_key:
        _PENDING = None
        _LAST_STATUS = {"status": "CANCELLED", "reason": "Shooting plan changed; old paper limit cancelled"}

    if _PENDING is None:
        _PENDING = {"key": desired_key, "plan": dict(plan), "side": plan["side"],
                    "entry": entry, "placed_at": timestamp,
                    "expires_at": timestamp + ttl, "fill_model": "trade_through_one_tick"}
        _LAST_STATUS = {"status": "PENDING", "reason": "paper limit staged; awaiting trade-through",
                        "side": plan["side"], "entry": entry,
                        "expires_at": timestamp + ttl}
        return {**_LAST_STATUS, "pending": {k: v for k, v in _PENDING.items() if k != "plan"},
                "filled_plan": None}

    pending = _PENDING
    if timestamp >= float(pending["expires_at"]):
        _PENDING = None
        _LAST_STATUS = {"status": "CANCELLED", "reason": "paper limit expired before fill"}
        return {**_LAST_STATUS, "pending": None, "filled_plan": None}

    limit_price = float(pending["entry"])
    side = str(pending["side"]).upper()
    crossed = current <= limit_price - tick if side == "BUY" else current >= limit_price + tick
    if crossed:
        old_plan = dict(pending["plan"])
        _PENDING = None
        _LAST_FILLED_KEY = desired_key
        filled = _filled_plan(old_plan, limit_price, "trade_through_one_tick")
        _LAST_STATUS = {"status": "FILLED", "reason": "price traded through passive limit",
                        "entry_style": "PASSIVE_LIMIT", "fill_price": limit_price}
        return {**_LAST_STATUS, "pending": None, "filled_plan": filled}

    _LAST_STATUS = {"status": "PENDING", "reason": "paper limit remains unfilled",
                    "side": side, "entry": limit_price,
                    "expires_at": pending["expires_at"]}
    return {**_LAST_STATUS, "pending": {k: v for k, v in pending.items() if k != "plan"},
            "filled_plan": None}
