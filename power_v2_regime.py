"""Conservative, Power-v2-only regime gate from timestamped trade prints.

The classifier consumes completed POWER-timeframe OHLC bars (M5 by default;
Fusion may set M3) built from print prices. It does not read legacy
POWER/Shooting/Escort decisions or infer bars from MBO and depth updates.
Missing history, gaps, a stale tape, a history/live price-scale mismatch, or
the ADX gray zone all return UNKNOWN. Range breakouts are intentionally not
authorized in this first pass: breakout_confirmed is always False, so RANGE
fails closed to NEITHER.
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
BAR_SECONDS = 300  # default M5; fusionv1: set_bar_seconds() re-times the ladder


def set_bar_seconds(seconds: int) -> None:
    """Set the active POWER interval (M5 by default; e.g. 180 for M3).

    The interval governs bucketing, completion boundaries, contiguity and gap
    math. The current probe format is fixed at 300s; Step 2 rejects those rows
    when this interval differs, and the classifier independently refuses
    fixed-M5 history at non-M5 intervals.
    """
    global BAR_SECONDS
    seconds = int(seconds)
    if seconds < 60:
        raise ValueError("bar seconds must be >= 60")
    BAR_SECONDS = seconds


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
    """Aggregate timestamped print prices into closed active-timeframe bars."""
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
    """Latest valid tick, including the unfinished active bar, for freshness only."""
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
    """Return a fail-closed regime label from completed active-timeframe bars.

    The public ``classify_m5_regime`` name is retained for compatibility; its
    interval follows BAR_SECONDS. Labels: ADX >= 25 => TREND; ADX < 20 =>
    RANGE; 20..25 => UNKNOWN. No side direction is produced here. RANGE
    breakout authorization is disabled. Before probe history is merged, price
    scale and timeframe compatibility are checked; neither source is silently
    rescaled or relabeled.
    """
    current = _utc(now)
    if current is None:
        raise ValueError("now must be timezone-aware")
    if period < 2 or max_tick_age_seconds <= 0:
        raise ValueError("period must be >=2 and max_tick_age_seconds positive")
    if not (0 <= range_adx_max < trend_adx_min <= 100):
        raise ValueError("ADX thresholds must satisfy 0 <= range < trend <= 100")

    # TIMEFRAME unification must govern BOTH tick bucketing and bar completion.
    # Omitting BAR_SECONDS here leaves the classifier on a hidden 300s clock
    # while POWER may be running M3/180s, causing a false fail-closed veto.
    boundary = last_completed_m5_end(
        current, grace_seconds=grace_seconds, bar_seconds=BAR_SECONDS)
    timeframe = f"M{max(1, BAR_SECONDS // 60)}"
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
    # The probe's compact OHLC format is fixed M5/300s unless each row carries
    # explicit verified ``bar_seconds`` metadata matching ``BAR_SECONDS`` (e.g.
    # from the local verified active-timeframe bar cache). Never reinterpret
    # untagged M5 candles as a different active interval.
    def _bar_interval_matches(raw: Any) -> bool:
        if not isinstance(raw, Mapping):
            return False
        if "bar_seconds" in raw and raw.get("bar_seconds") is not None:
            try:
                return int(raw.get("bar_seconds")) == BAR_SECONDS
            except (TypeError, ValueError, OverflowError):
                return False
        return BAR_SECONDS == 300

    history_timeframe_compatible = (
        not bool(history_bars)
        or all(_bar_interval_matches(raw) for raw in history_bars)
    )
    history_bars_refused = bool(history_bars) and (
        not history_timeframe_compatible or (bool(history_price_pool) and not use_history))
    # The bounded probe supplies verified historical OHLC bars directly. Merge
    # these only into regime classification, only after the scale gate passes;
    # live tick_data remains independently responsible for freshness and is
    # still passed unchanged to run_power_v2.
    accepted_history = (history_bars if use_history and history_timeframe_compatible
                        else [])
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
                "missing_bar_intervals": max(0, delta_seconds // BAR_SECONDS - 1),
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
        "timeframe": timeframe,
        "bar_seconds": BAR_SECONDS,
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
        "history_timeframe_compatible": history_timeframe_compatible,
        "history_timeframe_mismatch": bool(history_bars) and not history_timeframe_compatible,
        "history_bars_refused": history_bars_refused,
        "completed_bars": (
            [dict(b, bar_seconds=BAR_SECONDS) for b in bars[-60:]]
            if scale_check["status"] != "MISMATCH" else []
        ),
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
    # otherwise a stalled feed could look fresh throughout the next bar interval.
    tick_age = max(0.0, (current - latest_tick).total_seconds())
    result["latest_tick_age_seconds"] = round(tick_age, 3)
    if tick_age > max_tick_age_seconds:
        result["reason"] = "STALE_FEED"
        return result
    if not bars or int(bars[-1]["start"]) + BAR_SECONDS != int(boundary.timestamp()):
        result["reason"] = f"NO_TRADE_IN_LATEST_COMPLETED_{timeframe}_BAR"
        return result
    if len(recent) < needed:
        result["bars_used"] = len(recent)
        try:
            _prev = {"informational_only": True, "bars": len(recent)}
            _prev["window_high"] = round(max(float(b["high"]) for b in recent), 2)
            _prev["window_low"] = round(min(float(b["low"]) for b in recent), 2)
            _prev["last_close"] = round(float(recent[-1]["close"]), 2)
        except Exception:
            _prev = {"informational_only": True, "bars": len(recent)}
        result["provisional"] = _prev
        result["reason"] = f"INSUFFICIENT_COMPLETED_{timeframe}_HISTORY"
        return result
    if recent_gaps:
        result["bars_used"] = len(recent)
        result["reason"] = f"GAP_IN_COMPLETED_{timeframe}_HISTORY"
        return result

    adx = wilder_adx(recent, period=period)
    result["bars_used"] = len(recent)
    if adx is None or not math.isfinite(adx):
        result["reason"] = "ADX_UNAVAILABLE"
        return result
    result["adx"] = round(adx, 2)
    regime_label, reason_label = _label_adx(adx, trend_adx_min, range_adx_max)
    # Kaufman Efficiency Ratio (10-bar) flat-lake tail-wave guard:
    # On flat-lake whipsaw days (trendiness ~0.27), 3-minute splashes briefly push
    # Wilder ADX into 25..30 right at the top/bottom of a ping-pong wave even though
    # net displacement is a tiny fraction of path length.
    er_10 = None
    if len(recent) >= 10:
        c_tail = [float(b["close"]) for b in recent[-10:]]
        path_len = sum(abs(c_tail[i] - c_tail[i - 1]) for i in range(1, len(c_tail)))
        if path_len > 1e-9:
            er_10 = round(abs(c_tail[-1] - c_tail[0]) / path_len, 4)
        else:
            er_10 = 0.0
    result["efficiency_ratio_10"] = er_10
    # Lo & MacKinlay (1988) Variance Ratio VR(q=4) diagnostic:
    # VR(4) = Var(r_4) / (4 * Var(r_1)); VR < 0.80 indicates mean-reverting ping-pong
    # even when Wilder ADX smoothing temporarily stays >= 25. Default SHADOW_ONLY (=0).
    vr_4 = None
    if len(recent) >= 16:
        closes_vr = [float(b["close"]) for b in recent[-20:]]
        r1 = [closes_vr[i] - closes_vr[i - 1] for i in range(1, len(closes_vr))]
        r4 = [closes_vr[i] - closes_vr[i - 4] for i in range(4, len(closes_vr))]
        if len(r1) >= 8 and len(r4) >= 5:
            m1 = sum(r1) / len(r1)
            m4 = sum(r4) / len(r4)
            v1 = sum((x - m1) ** 2 for x in r1) / max(len(r1) - 1, 1)
            v4 = sum((x - m4) ** 2 for x in r4) / max(len(r4) - 1, 1)
            if v1 > 1e-9:
                vr_4 = round(v4 / (4.0 * v1), 4)
    result["variance_ratio_4"] = vr_4
    try:
        import config as _cfg_mod
        _fl_on = bool(getattr(_cfg_mod, "POWER_FLAT_LAKE_GUARD_ENABLED", False))
        _fl_er_min = float(getattr(_cfg_mod, "POWER_FLAT_LAKE_ER_MIN", 0.22) or 0.0)
        _vr_on = bool(getattr(_cfg_mod, "POWER_VR_GUARD_ENABLED", False))
        _vr_min = float(getattr(_cfg_mod, "POWER_VR_MIN", 0.80) or 0.80)
    except Exception:
        _fl_on = False
        _fl_er_min = 0.22
        _vr_on = False
        _vr_min = 0.80
    _would_flag_flat_lake = bool(
        _fl_er_min > 0
        and regime_label == "TREND"
        and adx < (trend_adx_min + 6.0)
        and er_10 is not None
        and er_10 < _fl_er_min
    )
    _would_flag_vr_chop = bool(
        _vr_min > 0
        and regime_label == "TREND"
        and vr_4 is not None
        and vr_4 < _vr_min
    )
    result["shadow_flat_lake"] = _would_flag_flat_lake
    result["shadow_vr_mean_reverting"] = _would_flag_vr_chop
    if _fl_on and _would_flag_flat_lake:
        regime_label = "RANGE"
        reason_label = "FLAT_LAKE_LOW_EFFICIENCY"
    elif _vr_on and _would_flag_vr_chop:
        regime_label = "RANGE"
        reason_label = "VARIANCE_RATIO_MEAN_REVERTING"
    result["market_regime"] = regime_label
    result["reason"] = reason_label
    return result
