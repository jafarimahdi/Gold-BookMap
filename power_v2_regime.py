"""Conservative, Power-v2-only M5 regime gate from timestamped trade prints.

The classifier consumes completed 5-minute OHLC bars built from print prices.
It does not read legacy POWER/Shooting/Escort decisions or infer bars from MBO
and depth updates. Missing history, gaps, a stale tape, a history/live price
scale mismatch, or the ADX gray zone all return UNKNOWN. Range breakouts are
intentionally not authorized in this first pass: breakout_confirmed is always
False, so RANGE fails closed to NEITHER.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any, Dict, List, Mapping, Optional

from power_history_probe_loader import check_price_scale_compatibility
from power_v2_shadow import last_completed_m5_end

__all__ = ["classify_m5_regime", "wilder_adx", "check_price_scale_compatibility",
           "PERIOD", "TREND_ADX_MIN", "RANGE_ADX_MAX"]

PERIOD = 14
TREND_ADX_MIN = 25.0
RANGE_ADX_MAX = 20.0
BAR_SECONDS = 300


def _utc(value: Any) -> Optional[datetime]:
    if isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _finite_positive(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _completed_bars(
    tick_data: List[Dict[str, Any]], boundary: datetime
) -> tuple[List[Dict[str, float]], Optional[datetime]]:
    """Aggregate timestamped print prices into closed UTC M5 bars only."""
    boundary_epoch = boundary.timestamp()
    points = []
    for order, row in enumerate(tick_data or []):
        if not isinstance(row, Mapping):
            continue
        raw_time = row.get("timestamp", row.get("ts", row.get("time")))
        ts = _utc(raw_time)
        price = _finite_positive(row.get("price"))
        if ts is None or price is None or ts.timestamp() >= boundary_epoch:
            continue
        bucket = math.floor(ts.timestamp() / BAR_SECONDS) * BAR_SECONDS
        if bucket + BAR_SECONDS <= boundary_epoch:
            points.append((bucket, ts, order, price))
    if not points:
        return [], None

    points.sort(key=lambda item: (item[0], item[1], item[2]))
    bars: List[Dict[str, float]] = []
    latest_ts: Optional[datetime] = None
    current_bucket = None
    current = None
    for bucket, ts, _order, price in points:
        if latest_ts is None or ts > latest_ts:
            latest_ts = ts
        if bucket != current_bucket:
            if current is not None:
                bars.append(current)
            current_bucket = bucket
            current = {"start": float(bucket), "high": price, "low": price,
                       "close": price}
        else:
            current["high"] = max(current["high"], price)
            current["low"] = min(current["low"], price)
            current["close"] = price
    if current is not None:
        bars.append(current)
    return bars, latest_ts


def _latest_valid_tick(tick_data: List[Dict[str, Any]], as_of: datetime) -> Optional[datetime]:
    """Latest valid incoming tick, including the unfinished M5 bar, for freshness only."""
    latest: Optional[datetime] = None
    for row in tick_data or []:
        if not isinstance(row, Mapping):
            continue
        ts = _utc(row.get("timestamp", row.get("ts", row.get("time"))))
        if ts is None or ts > as_of or _finite_positive(row.get("price")) is None:
            continue
        if latest is None or ts > latest:
            latest = ts
    return latest

def wilder_adx(bars: List[Mapping[str, float]], period: int = PERIOD) -> Optional[float]:
    """Compute Wilder ADX; needs at least 2*period contiguous OHLC bars."""
    if period < 2 or len(bars) < 2 * period:
        return None
    tr_values: List[float] = []
    plus_dm: List[float] = []
    minus_dm: List[float] = []
    for previous, current in zip(bars, bars[1:]):
        try:
            prev_high = float(previous["high"])
            prev_low = float(previous["low"])
            prev_close = float(previous["close"])
            high = float(current["high"])
            low = float(current["low"])
        except (KeyError, TypeError, ValueError, OverflowError):
            return None
        if not all(math.isfinite(x) for x in (prev_high, prev_low, prev_close, high, low)):
            return None
        up_move = high - prev_high
        down_move = prev_low - low
        plus_dm.append(up_move if up_move > down_move and up_move > 0 else 0.0)
        minus_dm.append(down_move if down_move > up_move and down_move > 0 else 0.0)
        tr_values.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    if len(tr_values) < 2 * period - 1:
        return None
    smooth_tr = sum(tr_values[:period])
    smooth_plus = sum(plus_dm[:period])
    smooth_minus = sum(minus_dm[:period])
    dx_values: List[float] = []
    for index in range(period - 1, len(tr_values)):
        if index >= period:
            smooth_tr = smooth_tr - smooth_tr / period + tr_values[index]
            smooth_plus = smooth_plus - smooth_plus / period + plus_dm[index]
            smooth_minus = smooth_minus - smooth_minus / period + minus_dm[index]
        if smooth_tr <= 1e-12:
            dx_values.append(0.0)
            continue
        plus_di = 100.0 * smooth_plus / smooth_tr
        minus_di = 100.0 * smooth_minus / smooth_tr
        denom = plus_di + minus_di
        dx_values.append(100.0 * abs(plus_di - minus_di) / denom if denom > 1e-12 else 0.0)

    if len(dx_values) < period:
        return None
    adx = sum(dx_values[:period]) / period
    for dx in dx_values[period:]:
        adx = (adx * (period - 1) + dx) / period
    return max(0.0, min(100.0, adx))


def _label_adx(adx: float, trend_adx_min: float, range_adx_max: float) -> tuple[str, str]:
    if adx >= trend_adx_min:
        return "TREND", "ADX_TREND"
    if adx < range_adx_max:
        return "RANGE", "ADX_RANGE_BREAKOUT_DISABLED"
    return "UNKNOWN", "ADX_NEUTRAL_BAND"


def classify_m5_regime(
    tick_data: List[Dict[str, Any]],
    *,
    now: Any,
    history_bars: Optional[List[Mapping[str, Any]]] = None,
    period: int = PERIOD,
    trend_adx_min: float = TREND_ADX_MIN,
    range_adx_max: float = RANGE_ADX_MAX,
    max_tick_age_seconds: float = 60.0,
    grace_seconds: float = 5.0,
) -> Dict[str, Any]:
    """Return a fail-closed regime label and diagnostics from completed M5 bars.

    Labels: ADX >= 25 => TREND; ADX < 20 => RANGE; 20..25 => UNKNOWN.
    No side direction is produced here. RANGE breakout authorization is disabled.
    Before any history bar is merged with live bars, the two price levels are
    compared (check_price_scale_compatibility); if they are not the same scale
    the history is refused and the call fails closed with reason
    HISTORY_PRICE_SCALE_MISMATCH (market_regime stays UNKNOWN).
    """
    current = _utc(now)
    if current is None:
        raise ValueError("now must be timezone-aware")
    if period < 2 or max_tick_age_seconds <= 0:
        raise ValueError("period must be >=2 and max_tick_age_seconds positive")
    if not (0 <= range_adx_max < trend_adx_min <= 100):
        raise ValueError("ADX thresholds must satisfy 0 <= range < trend <= 100")

    boundary = last_completed_m5_end(current, grace_seconds=grace_seconds)
    live_bars, _latest_completed_tick = _completed_bars(tick_data or [], boundary)
    # Fail closed on history/live price-scale disagreement. Probe bars and live
    # prints must quote the same price units before they may share one OHLC
    # series. The check NEVER rescales either side; a mismatch (for example the
    # probe writing 10x units) refuses the history and returns UNKNOWN so a
    # human must resolve the feed configuration before regime can resume.
    live_prices: List[float] = []
    for row in tick_data or []:
        if not isinstance(row, Mapping):
            continue
        ts = _utc(row.get("timestamp", row.get("ts", row.get("time"))))
        price = _finite_positive(row.get("price"))
        if ts is not None and price is not None and ts <= current:
            live_prices.append(price)
    history_price_pool = [v for raw in (history_bars or []) if isinstance(raw, Mapping)
                          for v in (raw.get("open"), raw.get("high"),
                                    raw.get("low"), raw.get("close"))]
    scale_check = check_price_scale_compatibility(history_price_pool, live_prices)
    use_history = scale_check["status"] == "OK"
    history_bars_refused = bool(history_price_pool) and not use_history
    # The bounded probe supplies verified historical OHLC bars directly. Merge
    # these only into regime classification, only after the scale gate passes;
    # live tick_data remains independently responsible for freshness and is
    # still passed unchanged to run_power_v2.
    accepted_history = history_bars if use_history else []
    bars_by_start: Dict[int, Dict[str, float]] = {}
    for raw in accepted_history:
        if not isinstance(raw, Mapping):
            continue
        try:
            start = int(float(raw.get("start")))
            high = float(raw.get("high"))
            low = float(raw.get("low"))
            close = float(raw.get("close"))
            open_price = float(raw.get("open", close))
        except (TypeError, ValueError, OverflowError):
            continue
        if (start % BAR_SECONDS != 0 or start + BAR_SECONDS > int(boundary.timestamp())
                or not all(math.isfinite(v) and v > 0
                           for v in (open_price, high, low, close))
                or high < max(open_price, close, low) or low > min(open_price, close, high)):
            continue
        bars_by_start[start] = {"start": float(start), "open": open_price,
                                "high": high, "low": low, "close": close}
    for live in live_bars:
        start = int(live["start"])
        existing = bars_by_start.get(start)
        if existing is None:
            bars_by_start[start] = live
        else:
            existing["high"] = max(existing["high"], live["high"])
            existing["low"] = min(existing["low"], live["low"])
            # The live stream is later than startup backfill for an overlapping bar.
            existing["close"] = live["close"]
    bars = [bars_by_start[key] for key in sorted(bars_by_start)]
    latest_tick = _latest_valid_tick(tick_data or [], current)
    needed = 2 * period
    recent = bars[-needed:]

    def bar_end_text(bar: Mapping[str, float]) -> str:
        return datetime.fromtimestamp(
            int(bar["start"]) + BAR_SECONDS, tz=timezone.utc
        ).isoformat().replace("+00:00", "Z")

    recent_gaps = []
    for previous, following in zip(recent, recent[1:]):
        delta_seconds = int(following["start"] - previous["start"])
        if delta_seconds != BAR_SECONDS:
            recent_gaps.append({
                "previous_bar_end_utc": bar_end_text(previous),
                "next_bar_start_utc": datetime.fromtimestamp(
                    int(following["start"]), tz=timezone.utc
                ).isoformat().replace("+00:00", "Z"),
                "missing_m5_intervals": max(0, delta_seconds // BAR_SECONDS - 1),
            })

    latest_contiguous_bars = 0
    if recent:
        latest_contiguous_bars = 1
        for previous, following in zip(reversed(recent[:-1]), reversed(recent[1:])):
            if int(following["start"] - previous["start"]) != BAR_SECONDS:
                break
            latest_contiguous_bars += 1

    result: Dict[str, Any] = {
        "market_regime": "UNKNOWN",
        "breakout_confirmed": False,
        "adx": None,
        "period": period,
        "bars_used": len(recent),
        "bars_available": len(bars),
        "bars_required": needed,
        "bar_end_utc": boundary.isoformat().replace("+00:00", "Z"),
        "history_first_bar_end_utc": bar_end_text(recent[0]) if recent else None,
        "history_last_bar_end_utc": bar_end_text(recent[-1]) if recent else None,
        "recent_gap_count": len(recent_gaps),
        "recent_gaps": recent_gaps,
        "latest_contiguous_bars": latest_contiguous_bars,
        "recent_history_contiguous": bool(recent) and not recent_gaps,
        "latest_tick_age_seconds": None,
        "reason": "UNKNOWN",
        "price_scale_check": scale_check,
        "history_bars_refused": history_bars_refused,
    }
    if scale_check["status"] == "MISMATCH":
        # Data-integrity incident: probe history and live ticks disagree about
        # price units (e.g. one side is 10x the other). Refusing the history is
        # not enough -- produce no regime at all until a human resolves which
        # side is mis-scaled. Never auto-rescale either side.
        result["reason"] = "HISTORY_PRICE_SCALE_MISMATCH"
        return result
    if latest_tick is None:
        result["reason"] = "NO_TIMESTAMPED_PRICE_TICKS"
        return result

    # Freshness is measured against wall-clock now, not the historical bar close;
    # otherwise a stalled feed could look fresh throughout the next M5 interval.
    tick_age = max(0.0, (current - latest_tick).total_seconds())
    result["latest_tick_age_seconds"] = round(tick_age, 3)
    if tick_age > max_tick_age_seconds:
        result["reason"] = "STALE_FEED"
        return result
    if not bars or int(bars[-1]["start"]) + BAR_SECONDS != int(boundary.timestamp()):
        result["reason"] = "NO_TRADE_IN_LATEST_COMPLETED_M5_BAR"
        return result
    if len(recent) < needed:
        result["bars_used"] = len(recent)
        result["reason"] = "INSUFFICIENT_COMPLETED_M5_HISTORY"
        return result
    if recent_gaps:
        result["bars_used"] = len(recent)
        result["reason"] = "GAP_IN_COMPLETED_M5_HISTORY"
        return result

    adx = wilder_adx(recent, period=period)
    result["bars_used"] = len(recent)
    if adx is None or not math.isfinite(adx):
        result["reason"] = "ADX_UNAVAILABLE"
        return result
    result["adx"] = round(adx, 2)
    result["market_regime"], result["reason"] = _label_adx(
        adx, trend_adx_min, range_adx_max)
    return result
