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

__all__ = ["run_power_v2", "run_power_v2_shadow", "last_completed_m5_end"]


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


def _voice_gain_value() -> float:
    """VOICE FIX (2026-10-15): the microphone calibration factor.

    The four tick sensors measure volume-imbalance ratios, which in real gold
    tape live at ~0.03-0.12 even during genuine moves (measured 2026-10-14
    14:40: down_power 100%, net_force -1.0, activity 0.0624 -- a unanimous
    referee muzzled by vote MAGNITUDE). POWER's gates expect >= 0.18 average
    loudness. The gain rescales the sensors to their real operating range so
    fish-sized force is audible. This calibrates the MICROPHONES; every gate
    (min_activity, min_family_signal, min_agreeing_families, dominance) stays
    exactly as it was. Default 3.0 = the smallest gain that makes the proven
    monster bar audible (0.0624 x 3 = 0.187 >= 0.18).
    """
    import os
    try:
        g = float(os.environ.get("VOICE_GAIN", "3.0"))
    except (TypeError, ValueError):
        return 3.0
    return g if g > 0 else 3.0


def _apply_voice_gain(judges):
    """VOICE FIX (2026-10-15): rescale tick-judge vote magnitudes.

    Calibration, not verdicts: signs are preserved exactly (no direction is
    invented or flipped), values clamp to [-1, 1], quality fields are
    untouched, judges without a numeric value pass through unchanged.
    Fail-closed: any problem returns the judges untouched (old behavior).
    """
    try:
        import os
        if not isinstance(judges, dict):
            return judges
        default_gain = _voice_gain_value()
        out = {}
        for name, reading in judges.items():
            try:
                if not isinstance(reading, dict):
                    out[name] = reading
                    continue
                value = reading.get("value")
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    out[name] = reading
                    continue
                gain = default_gain
                raw = os.environ.get("VOICE_GAIN_" + str(name).upper())
                if raw:
                    try:
                        per = float(raw)
                        if per > 0:
                            gain = per
                    except (TypeError, ValueError):
                        pass
                new = dict(reading)
                new["value"] = max(-1.0, min(1.0, float(value) * gain))
                out[name] = new
            except Exception:
                out[name] = reading
        return out
    except Exception:
        return judges


def run_power_v2(
    tick_data: List[Dict[str, Any]],
    *,
    now: Any,
    symbol: str,
    tick_size: float,
    memory_root: Any = None,
    market_regime: str = "UNKNOWN",
    breakout_confirmed: bool = False,
    high_impact_news: bool = False,
    regime_diagnostics: Optional[Mapping[str, Any]] = None,
    grace_seconds: float = 5.0,
) -> Dict[str, Any]:
    """Evaluate the active v2 Power contract from Step 2 market data.

    Repeated calls use a stable per-symbol/per-M5-bar memory event ID. The regime
    defaults to UNKNOWN and fails closed to NEITHER until separately confirmed.
    Memory errors are reported but do not alter the Power decision; no orders are
    executed or submitted here.
    """
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol is required")
    bar_end = last_completed_m5_end(now, grace_seconds=grace_seconds)
    # Exclude a trade stamped exactly at the next bar boundary.
    evaluation_end = bar_end - timedelta(microseconds=1)
    built = build_m5_inputs(
        tick_data or [], now=evaluation_end, tick_size=tick_size)
    # VOICE FIX (2026-10-15): calibrate the tick judges' vote magnitudes so
    # fish-sized force is audible to POWER's (unchanged) activity gate.
    built["judges"] = _apply_voice_gain(built["judges"])
    context = dict(built["context"])
    context["market_regime"] = str(market_regime or "UNKNOWN").upper()
    context["breakout_confirmed"] = bool(breakout_confirmed)
    # Only an explicit calendar BLACKOUT is a Power veto; WARNING/QUIET do not
    # become force votes. Other context flags stay unset until their semantics
    # are defined and wired by the caller.
    context["high_impact_news"] = bool(high_impact_news)
    answer = decide(built["judges"], context=context)
    try:
        answer["voice_gain"] = _voice_gain_value()
    except Exception:
        pass
    availability = built.get("judge_availability") or {}
    excluded = answer.get("excluded_judges") or {}
    for judge_name, status in availability.items():
        if judge_name in excluded and isinstance(status, Mapping):
            excluded[judge_name] = str(status.get("reason") or excluded[judge_name])
    answer["excluded_judges"] = excluded
    answer.update({
        "shadow_only": False,
        "bar_end_utc": bar_end.isoformat().replace("+00:00", "Z"),
        "input_judges": built["judges"],
        "judge_availability": availability,
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


def run_power_v2_shadow(*args: Any, **kwargs: Any) -> Dict[str, Any]:
    """Backward-compatible research wrapper; the main app uses run_power_v2."""
    answer = run_power_v2(*args, **kwargs)
    answer["shadow_only"] = True
    return answer
