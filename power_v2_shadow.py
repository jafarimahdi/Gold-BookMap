"""Safe, shadow-only bridge from Step 2 ticks to POWER v2 + its memory book.

This helper has no trading, broker, Shooting, or Escort dependency. It evaluates
only the last completed M5 window (with a five-second close grace), leaves regime
UNKNOWN unless supplied explicitly, and records at most one memory row per bar.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from power_m5_adapter import build_m5_inputs
from power_team_v2 import decide

__all__ = ["run_power_v2_shadow", "last_completed_m5_end"]


def _utc(value: Any) -> datetime:
    if isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        value = datetime.fromisoformat(raw)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def last_completed_m5_end(now: Any, grace_seconds: float = 5.0) -> datetime:
    """Return the latest M5 boundary safely past its close grace period."""
    current = _utc(now)
    grace = max(0.0, min(float(grace_seconds), 60.0))
    epoch = current.timestamp()
    boundary = math.floor((epoch - grace) / 300.0) * 300.0
    return datetime.fromtimestamp(boundary, tz=timezone.utc)


def _bar_close_price(ticks: List[Dict[str, Any]], end: datetime) -> Optional[float]:
    best_ts: Optional[datetime] = None
    best_price: Optional[float] = None
    for row in ticks or []:
        if not isinstance(row, Mapping):
            continue
        raw_ts = row.get("timestamp", row.get("ts", row.get("time")))
        try:
            ts = _utc(raw_ts) if raw_ts is not None else None
            price = float(row.get("price"))
        except (TypeError, ValueError, OverflowError):
            continue
        if ts is not None and ts < end and math.isfinite(price) and price > 0:
            if best_ts is None or ts > best_ts:
                best_ts, best_price = ts, price
    return best_price


def run_power_v2_shadow(
    tick_data: List[Dict[str, Any]],
    *,
    now: Any,
    symbol: str,
    tick_size: float,
    memory_root: Any = None,
    market_regime: str = "UNKNOWN",
    breakout_confirmed: bool = False,
    regime_diagnostics: Optional[Mapping[str, Any]] = None,
    grace_seconds: float = 5.0,
) -> Dict[str, Any]:
    """Evaluate v2 without affecting the legacy Power/Shooting path.

    Calling this repeatedly in the same loop is safe: the memory writer uses a
    stable per-symbol/per-M5-bar event ID. `market_regime` defaults to UNKNOWN,
    which makes v2 return NEITHER until a separately validated M5 classifier is
    connected. A memory error is reported in the result but never interrupts the
    old Step-2 loop.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol is required")
    bar_end = last_completed_m5_end(now, grace_seconds=grace_seconds)
    # Exclude a trade stamped exactly at the next bar boundary.
    evaluation_end = bar_end - timedelta(microseconds=1)
    built = build_m5_inputs(
        tick_data or [], now=evaluation_end, tick_size=tick_size)
    context = dict(built["context"])
    context["market_regime"] = str(market_regime or "UNKNOWN").upper()
    context["breakout_confirmed"] = bool(breakout_confirmed)
    answer = decide(built["judges"], context=context)
    answer.update({
        "shadow_only": True,
        "bar_end_utc": bar_end.isoformat().replace("+00:00", "Z"),
        "input_judges": built["judges"],
        "adapter_diagnostics": built["diagnostics"],
        "regime_diagnostics": dict(regime_diagnostics or {}),
    })

    if memory_root is not None:
        event_id = f"{symbol.strip()}|M5|{bar_end.isoformat()}|FINAL"
        try:
            from power_memory_book import append_snapshot
            written = append_snapshot(
                memory_root,
                timestamp=bar_end,
                symbol=symbol.strip(),
                judges=built["judges"],
                context={**context, "adapter_diagnostics": built["diagnostics"],
                         "regime_diagnostics": dict(regime_diagnostics or {})},
                result={k: v for k, v in answer.items()
                        if k not in ("memory_status",)},
                reference_price=_bar_close_price(tick_data or [], bar_end),
                phase="FINAL",
                event_id=event_id,
            )
            answer["memory_status"] = written
        except Exception as exc:  # Shadow memory must never break Step 2.
            answer["memory_status"] = {
                "appended": False,
                "error": type(exc).__name__,
            }
    else:
        answer["memory_status"] = {"appended": False, "reason": "memory disabled"}
    return answer
