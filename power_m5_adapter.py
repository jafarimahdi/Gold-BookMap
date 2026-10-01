"""Build conservative normalized POWER inputs from timestamped trade ticks.

This is an adapter prototype, not connected to Step 2. It accepts Bookmap-style
trade rows ({timestamp, price, volume, side, is_direct}) and uses only the last
5 minutes. It fails closed on missing timestamps, untrusted side labels, stale
feed, or insufficient data. It intentionally does not fabricate L3 votes,
resting-book votes, range states, or breakout confirmations.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

__all__ = ["build_m5_inputs"]


def _number(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _timestamp(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        num = _number(value)
        if num is None:
            return None
        try:
            dt = datetime.fromtimestamp(num, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None or dt.utcoffset() is None:
        return None
    return dt.astimezone(timezone.utc)


def _side(raw: Any) -> Optional[int]:
    text = str(raw or "").strip().upper()
    if text in {"BUY", "B", "BID"}:
        return 1
    if text in {"SELL", "S", "ASK"}:
        return -1
    return None


def _collect(
    rows: List[Dict[str, Any]],
    start: datetime,
    end: datetime,
    *,
    trust_explicit_side: bool,
    include_end: bool = True,
) -> Tuple[List[Tuple[float, float, int]], int, int]:
    out: List[Tuple[float, float, int]] = []
    timestamped = 0
    in_window = 0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        raw_ts = row.get("timestamp", row.get("ts", row.get("time")))
        ts = _timestamp(raw_ts)
        if ts is None:
            continue
        timestamped += 1
        if ts < start or ts > end or (not include_end and ts == end):
            continue
        price = _number(row.get("price"))
        volume = _number(row.get("volume", row.get("size")))
        if price is None or price <= 0 or volume is None or volume <= 0:
            continue
        in_window += 1
        direct = bool(row.get("is_direct", row.get("is_bookmap_direct", False)))
        if not direct and not trust_explicit_side:
            continue
        direction = _side(row.get("side"))
        if direction is None:
            continue
        out.append((price, volume, direction))
    return out, timestamped, in_window


def _delta_imbalance(trades: List[Tuple[float, float, int]]) -> float:
    total = sum(volume for _, volume, _ in trades)
    if total <= 0:
        return 0.0
    signed = sum(volume * side for _, volume, side in trades)
    return max(-1.0, min(1.0, signed / total))


def build_m5_inputs(
    tick_data: List[Dict[str, Any]],
    *,
    now: Any,
    tick_size: float,
    min_trades: int = 20,
    max_tick_age_seconds: float = 60.0,
    min_direct_side_ratio: float = 0.80,
    trust_explicit_side: bool = False,
    min_big_print_size: float = 1.0,
) -> Dict[str, Any]:
    """Return judges, per-judge availability, context, and diagnostics for Power v2.

    ``now`` must be timezone-aware. Ticks are split into the active rolling
    window [now-5m, now] and the preceding five minutes, which is used only for
    CVD acceleration. ``tick_size`` is required so footprint breadth is binned
    correctly for the data instrument (do not blindly use the CFD tick size for
    a futures feed).

    Only direct/validated trade sides are used by default. Set
    ``trust_explicit_side=True`` only if the caller has established that its
    side labels are actual aggressor classifications. No tick-rule side is
    invented here.
    """
    current_time = _timestamp(now)
    if current_time is None:
        raise ValueError("now must be a timezone-aware datetime, ISO timestamp, or epoch")
    step = _number(tick_size)
    if step is None or step <= 0:
        raise ValueError("tick_size must be a positive instrument-specific value")
    if min_trades < 1:
        raise ValueError("min_trades must be at least 1")
    if max_tick_age_seconds <= 0:
        raise ValueError("max_tick_age_seconds must be positive")

    rows = list(tick_data or [])
    current_start = current_time - timedelta(minutes=5)
    previous_start = current_time - timedelta(minutes=10)
    current, timestamped_n, current_raw_n = _collect(
        rows, current_start, current_time, trust_explicit_side=trust_explicit_side)
    previous, _prev_ts_n, previous_raw_n = _collect(
        rows, previous_start, current_start, trust_explicit_side=trust_explicit_side,
        include_end=False)

    # Timestamp coverage is measured against the caller's full sample. Timestamped
    # provider data are required for a trustworthy window; missing time is not guessed.
    timestamp_quality = timestamped_n / max(len(rows), 1)
    current_side_ratio = len(current) / max(current_raw_n, 1)
    current_sample_quality = min(1.0, len(current) / float(min_trades))
    current_quality = current_side_ratio * current_sample_quality
    enough_current = len(current) >= min_trades

    current_imbalance = _delta_imbalance(current)
    previous_imbalance = _delta_imbalance(previous)
    previous_quality = min(1.0, len(previous) / float(min_trades))
    momentum_quality = min(current_quality, previous_quality)
    # Difference between adjacent 5-minute signed-imbalance readings, rescaled to [-1,1].
    cvd_momentum = max(-1.0, min(1.0,
                         (current_imbalance - previous_imbalance) / 2.0))

    # Large-print direction: top 2% of current prints, but only when the
    # cutoff is genuinely above the sample median. Scale its signed contribution
    # by ALL M5 volume, not only the selected prints; otherwise one print could
    # create an exaggerated +/-1 family score regardless of its market share.
    volumes = sorted(volume for _, volume, _ in current)
    big_value = 0.0
    big_cutoff = None
    big_count = 0
    big_volume_share = 0.0
    big_quality = 0.0
    total_current_volume = sum(volume for _, volume, _ in current)
    if volumes and len(volumes) >= min_trades:
        idx = min(len(volumes) - 1, max(0, math.ceil(0.98 * len(volumes)) - 1))
        median = volumes[len(volumes) // 2]
        big_cutoff = max(float(min_big_print_size), volumes[idx])
        if big_cutoff > median:
            big = [(volume, side) for _, volume, side in current if volume >= big_cutoff]
            big_count = len(big)
            big_total = sum(volume for volume, _ in big)
            if big_total > 0 and total_current_volume > 0:
                big_value = sum(volume * side for volume, side in big) / total_current_volume
                big_volume_share = big_total / total_current_volume
                big_quality = current_quality

    # Breadth: average signed imbalance at each instrument-tick price bin.
    by_level: Dict[int, List[float]] = {}
    for price, volume, side in current:
        level = int(round(price / step))
        totals = by_level.setdefault(level, [0.0, 0.0])
        totals[0 if side > 0 else 1] += volume
    level_imbalances = [((buy - sell) / (buy + sell))
                        for buy, sell in by_level.values() if buy + sell > 0]
    breadth = (sum(level_imbalances) / len(level_imbalances)
               if level_imbalances else 0.0)
    breadth_quality = current_quality if len(level_imbalances) >= 2 else 0.0

    # Latest timestamp among timestamped rows, including rows outside the window,
    # lets the caller distinguish quiet tape from a disconnected/stale feed.
    parsed_times = [_timestamp(row.get("timestamp", row.get("ts", row.get("time"))))
                    for row in rows if isinstance(row, Mapping)]
    valid_times = [ts for ts in parsed_times if ts is not None and ts <= current_time]
    latest = max(valid_times) if valid_times else None
    tick_age = ((current_time - latest).total_seconds() if latest else None)
    feed_stale = (tick_age is None or tick_age > max_tick_age_seconds)
    data_quality_ok = bool(
        enough_current
        and timestamp_quality >= 0.80
        and current_side_ratio >= min_direct_side_ratio
        and not feed_stale
    )

    judges: Dict[str, Any] = {}
    if current:
        judges["footprint_delta"] = {
            "value": current_imbalance,
            "quality": current_quality,
        }
        if big_quality > 0:
            judges["big_prints"] = {
                "value": max(-1.0, min(1.0, big_value)),
                "quality": big_quality,
            }
        if breadth_quality > 0:
            judges["footprint_levels"] = {
                "value": max(-1.0, min(1.0, breadth)),
                "quality": breadth_quality,
            }
        if len(previous) >= min_trades:
            judges["cvd_momentum"] = {
                "value": cvd_momentum,
                "quality": momentum_quality,
            }

    judge_availability: Dict[str, Dict[str, Any]] = {}
    for name in ("footprint_delta", "l3_aggr_limit", "cvd_momentum",
                 "big_prints", "footprint_levels"):
        reading = judges.get(name)
        if isinstance(reading, Mapping) and _number(reading.get("quality")) is not None \
                and float(reading.get("quality", 0.0)) > 0:
            judge_availability[name] = {"status": "VALID", "reason": "usable M5 input"}
        elif name == "l3_aggr_limit":
            judge_availability[name] = {
                "status": "NOT_EMITTED",
                "reason": "current M5 adapter does not map a validated L3 executed-flow field",
            }
        elif name == "cvd_momentum" and len(previous) < min_trades:
            judge_availability[name] = {
                "status": "MISSING",
                "reason": f"previous M5 window has {len(previous)} usable direct trades; {min_trades} required",
            }
        elif name == "big_prints" and len(current) < min_trades:
            judge_availability[name] = {
                "status": "MISSING",
                "reason": f"current M5 window has {len(current)} usable direct trades; {min_trades} required",
            }
        elif name == "big_prints":
            judge_availability[name] = {
                "status": "MISSING",
                "reason": "no distinct qualifying large-print tail in this M5 window",
            }
        elif name == "footprint_levels" and len(level_imbalances) < 2:
            judge_availability[name] = {
                "status": "MISSING",
                "reason": f"only {len(level_imbalances)} usable price bins; at least 2 required",
            }
        elif name == "footprint_delta" and not current:
            judge_availability[name] = {
                "status": "MISSING", "reason": "no usable direct trades in current M5 window",
            }
        else:
            judge_availability[name] = {
                "status": "INVALID_OR_LOW_QUALITY",
                "reason": "input did not meet adapter freshness/side/timestamp quality requirements",
            }

    return {
        "judges": judges,
        "judge_availability": judge_availability,
        "context": {
            "feed_stale": feed_stale,
            "data_quality_ok": data_quality_ok,
            # The adapter deliberately cannot decide whether a market is trending
            # or ranging. Missing regime must make Power return NEITHER.
            "market_regime": "UNKNOWN",
        },
        "diagnostics": {
            "timeframe": "M5",
            "window_start_utc": current_start.isoformat().replace("+00:00", "Z"),
            "window_end_utc": current_time.isoformat().replace("+00:00", "Z"),
            "tick_size": step,
            "minimum_current_trades": min_trades,
            "maximum_tick_age_seconds": max_tick_age_seconds,
            "minimum_direct_side_ratio": min_direct_side_ratio,
            "minimum_timestamp_coverage": 0.80,
            "data_quality_ok": data_quality_ok,
            "previous_window_direct_trades": len(previous),
            "current_window_direct_trades": len(current),
            "current_window_raw_trades": current_raw_n,
            "current_side_coverage": round(current_side_ratio, 4),
            "timestamp_coverage": round(timestamp_quality, 4),
            "latest_tick_age_seconds": round(tick_age, 3) if tick_age is not None else None,
            "price_levels": len(level_imbalances),
            "big_print_cutoff": big_cutoff,
            "big_print_count": big_count,
            "big_print_volume_share": round(big_volume_share, 4),
            "big_print_signed_share_of_total_volume": round(big_value, 4),
            "current_delta_imbalance": round(current_imbalance, 4),
            "previous_delta_imbalance": round(previous_imbalance, 4),
            "note": "No M5 range/breakout judgement or L3 resting-book vote is inferred here.",
        },
    }
