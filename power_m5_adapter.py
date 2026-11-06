"""Build conservative normalized POWER inputs from timestamped trade ticks.

This is an adapter prototype, not connected to Step 2. It accepts Bookmap-style
trade rows ({timestamp, price, volume, side, is_direct}) and uses only the last
5 minutes. It fails closed on missing timestamps, untrusted side labels, stale
feed, or insufficient data. It intentionally does not fabricate L3 votes,
resting-book votes, range states, or breakout confirmations.
"""
from __future__ import annotations

from bisect import bisect_right
from datetime import datetime, timedelta, timezone
import math
from typing import Any, Dict, List, Mapping, Optional, Tuple

__all__ = ["build_m5_inputs"]

_INVALID_MBO_OIDS = {"", "0", "-1", "NONE", "NULL"}
_UNTRUSTED_TS_SOURCES = {
    "receive", "receive_time", "local", "local_receive", "local_receive_time",
}


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


def _collect_timed(
    rows: List[Dict[str, Any]],
    start: datetime,
    end: datetime,
    *,
    trust_explicit_side: bool,
    include_end: bool = True,
) -> Tuple[List[Tuple[datetime, float, float, int]], int, int]:
    out: List[Tuple[datetime, float, float, int]] = []
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
        out.append((ts, price, volume, direction))
    out.sort(key=lambda item: item[0])
    return out, timestamped, in_window


def _collect(
    rows: List[Dict[str, Any]],
    start: datetime,
    end: datetime,
    *,
    trust_explicit_side: bool,
    include_end: bool = True,
) -> Tuple[List[Tuple[float, float, int]], int, int]:
    timed, timestamped, in_window = _collect_timed(
        rows,
        start,
        end,
        trust_explicit_side=trust_explicit_side,
        include_end=include_end,
    )
    return [(px, vol, sgn) for _, px, vol, sgn in timed], timestamped, in_window


def _mbo_book_side(raw_type: str, raw_side: str) -> Optional[int]:
    if "BID" in raw_type or raw_side in {"BUY", "BID", "B"}:
        return 1
    if "ASK" in raw_type or raw_side in {"SELL", "ASK", "S", "A"}:
        return -1
    return None


def _ref_price_at(
    ts: datetime,
    trade_times: List[datetime],
    trade_prices: List[float],
    fallback_px: float,
) -> float:
    if not trade_times:
        return fallback_px
    idx = bisect_right(trade_times, ts) - 1
    if idx < 0:
        return trade_prices[0]
    return trade_prices[min(idx, len(trade_prices) - 1)]


def _collect_near_mbo_flow(
    order_events: List[Dict[str, Any]],
    start: datetime,
    end: datetime,
    *,
    current_timed: List[Tuple[datetime, float, float, int]],
    step: float,
    near_touch_usd: float,
) -> Dict[str, Any]:
    """Compute Cont-Kukanov-Stoikov (2014) near-touch passive MBO order-flow imbalance.

    Uses contemporaneous trade price lookup so fast trending bars never mismeasure
    touch distance against a stale bar-open or bar-close anchor. Rejects unkeyed
    order_ids, local-receive timestamps, deep-book spoofing (> near_cap), orphan
    modifications without explicit old_size, and any event outside [start, end].
    """
    near_cap = max(float(near_touch_usd), 10.0 * step)
    decay_len = max(0.5 * near_cap, step)
    trade_times = [ts for ts, _, _, _ in current_timed]
    trade_prices = [px for _, px, _, _ in current_timed]
    fallback_px = trade_prices[-1] if trade_prices else 0.0

    bid_add = bid_pull = ask_add = ask_pull = 0.0
    near_events = 0
    in_window_events = 0
    first_near_ts: Optional[datetime] = None

    for ev in order_events:
        if not isinstance(ev, Mapping):
            continue
        if ev.get("timestamp_is_source") is False or ev.get("has_source_timestamp") is False:
            continue
        ts_src = str(ev.get("timestamp_source", "") or "").strip().lower()
        if ts_src in _UNTRUSTED_TS_SOURCES:
            continue
        ts = _timestamp(ev.get("timestamp", ev.get("time")))
        if ts is None or ts < start or ts > end:
            continue
        oid = str(ev.get("order_id") or ev.get("id") or ev.get("orderId") or "").strip()
        if oid.upper() in _INVALID_MBO_OIDS:
            continue
        raw_type = str(ev.get("type", ev.get("event_type", "")) or "").strip().upper()
        raw_side = str(ev.get("side", "") or "").strip().upper()
        bside = _mbo_book_side(raw_type, raw_side)
        if bside is None:
            continue
        in_window_events += 1
        ref_px = _ref_price_at(ts, trade_times, trade_prices, fallback_px)
        if ref_px <= 0:
            continue

        price = _number(ev.get("price"))
        size = _number(ev.get("size", ev.get("volume")))
        is_new = ("NEW" in raw_type) or (raw_type in {"ADD", "OPEN"})
        is_cancel = any(k in raw_type for k in ("CANCEL", "DELETE", "REMOVE"))
        is_modify = any(k in raw_type for k in ("REPLACE", "MODIFY", "UPDATE"))
        is_fill = raw_type in {"FILL", "TRADE", "EXECUTED", "MATCH"}

        touched = False
        if is_new:
            if price is not None and price > 0 and size is not None and size > 0:
                dist = abs(price - ref_px)
                if dist <= near_cap:
                    w = math.exp(-dist / decay_len)
                    if bside > 0:
                        bid_add += size * w
                    else:
                        ask_add += size * w
                    touched = True
        elif is_cancel:
            if price is not None and price > 0 and size is not None and size > 0:
                dist = abs(price - ref_px)
                if dist <= near_cap:
                    w = math.exp(-dist / decay_len)
                    if bside > 0:
                        bid_pull += size * w
                    else:
                        ask_pull += size * w
                    touched = True
        elif is_modify:
            old_sz = _number(ev.get("old_size"))
            if old_sz is not None and old_sz >= 0 and size is not None and size >= 0:
                old_px = _number(ev.get("old_price"))
                if old_px is None or old_px <= 0:
                    old_px = price
                if (
                    price is not None and price > 0
                    and old_px is not None and old_px > 0
                    and abs(price - old_px) > 0.5 * step
                ):
                    if old_sz > 0 and abs(old_px - ref_px) <= near_cap:
                        w_old = math.exp(-abs(old_px - ref_px) / decay_len)
                        if bside > 0:
                            bid_pull += old_sz * w_old
                        else:
                            ask_pull += old_sz * w_old
                        touched = True
                    if size > 0 and abs(price - ref_px) <= near_cap:
                        w_new = math.exp(-abs(price - ref_px) / decay_len)
                        if bside > 0:
                            bid_add += size * w_new
                        else:
                            ask_add += size * w_new
                        touched = True
                elif price is not None and price > 0:
                    dist = abs(price - ref_px)
                    diff = size - old_sz
                    if dist <= near_cap and abs(diff) > 1e-9:
                        w = math.exp(-dist / decay_len)
                        if diff > 0:
                            if bside > 0:
                                bid_add += diff * w
                            else:
                                ask_add += diff * w
                        else:
                            if bside > 0:
                                bid_pull += (-diff) * w
                            else:
                                ask_pull += (-diff) * w
                        touched = True
        elif is_fill and bool(ev.get("is_market_order", False)):
            if price is not None and price > 0 and size is not None and size > 0:
                dist = abs(price - ref_px)
                if dist <= near_cap:
                    w = math.exp(-dist / decay_len)
                    if bside > 0:
                        ask_pull += size * w
                    else:
                        bid_pull += size * w
                    touched = True

        if touched:
            near_events += 1
            if first_near_ts is None or ts < first_near_ts:
                first_near_ts = ts

    gross = bid_add + bid_pull + ask_add + ask_pull
    net = (bid_add - bid_pull) - (ask_add - ask_pull)
    imb = max(-1.0, min(1.0, net / gross)) if gross > 0 else 0.0
    return {
        "in_window_events": in_window_events,
        "near_events": near_events,
        "first_near_ts": first_near_ts,
        "bid_add": bid_add,
        "bid_pull": bid_pull,
        "ask_add": ask_add,
        "ask_pull": ask_pull,
        "gross": gross,
        "net": net,
        "imbalance": imb,
    }


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
    bar_seconds: int = 300,  # fusionv1: TIMEFRAME-driven window (M3 -> 180)
    order_events: Optional[List[Dict[str, Any]]] = None,
    min_mbo_events: int = 5,
    near_touch_usd: float = 2.50,
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
    # fusionv1: window length follows POWER_BAR_SECONDS (was hardcoded 5 min).
    _bar_s = max(60, int(bar_seconds))
    current_start = current_time - timedelta(seconds=_bar_s)
    previous_start = current_time - timedelta(seconds=2 * _bar_s)
    current_timed, timestamped_n, current_raw_n = _collect_timed(
        rows, current_start, current_time, trust_explicit_side=trust_explicit_side)
    current = [(px, vol, sgn) for _, px, vol, sgn in current_timed]
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
    big_absorption_damped = False
    bar_disp = (
        (current_timed[-1][1] - current_timed[0][1])
        if len(current_timed) >= 2
        else 0.0
    )
    total_current_volume = sum(volume for _, volume, _ in current)
    if volumes and len(volumes) >= min_trades:
        idx = min(len(volumes) - 1, max(0, math.ceil(0.98 * len(volumes)) - 1))
        median = volumes[len(volumes) // 2]
        if volumes[idx] > median:
            big_cutoff = max(float(min_big_print_size), volumes[idx])
            big = [(volume, side) for _, volume, side in current if volume >= big_cutoff]
            big_count = len(big)
            big_total = sum(volume for volume, _ in big)
            if big_total > 0 and total_current_volume > 0:
                big_value = sum(volume * side for volume, side in big) / total_current_volume
                # Stage P3 Kyle's lambda absorption damper (Easley et al. 2012; Bouchaud 2018):
                # when large aggressive block prints push one way yet actual completed bar price
                # displaces >= 2 ticks the opposite way, passive limit liquidity absorbed the burst.
                if big_value * bar_disp < 0.0 and abs(bar_disp) >= 2.0 * step:
                    big_value *= 0.35
                    big_absorption_damped = True
                big_volume_share = big_total / total_current_volume
                big_quality = current_quality

    # Breadth: sqrt(volume)-weighted signed imbalance across instrument-tick bins
    # so 1-lot edge prints cannot overpower active traded price levels while still
    # measuring price-ladder breadth rather than duplicating raw footprint_delta.
    by_level: Dict[int, List[float]] = {}
    for price, volume, side in current:
        level = int(round(price / step))
        totals = by_level.setdefault(level, [0.0, 0.0])
        totals[0 if side > 0 else 1] += volume
    level_imbalances = []
    level_weights = []
    for buy, sell in by_level.values():
        tot_bin = buy + sell
        if tot_bin > 0:
            level_imbalances.append((buy - sell) / tot_bin)
            level_weights.append(math.sqrt(tot_bin))
    w_sum = sum(level_weights)
    breadth = (
        sum(imb * w for imb, w in zip(level_imbalances, level_weights)) / w_sum
        if w_sum > 0 else 0.0
    )
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

    # Stage P2 (2026-11-06): Validated near-touch MBO passive queue flow + matched-horizon
    # direct executed flow (`l3_aggr_limit`, Cont, Kukanov & Stoikov 2014; Easley et al. 2012).
    # Remains NOT_EMITTED when caller omits `order_events` (tick-only callers/tests).
    _min_mbo = max(1, int(min_mbo_events))
    mbo_flow: Optional[Dict[str, Any]] = None
    l3_aggr_val = 0.0
    l3_aggr_quality = 0.0
    l3_matched_aggr_imb = 0.0
    l3_absorption_damped = False
    if order_events is not None:
        mbo_flow = _collect_near_mbo_flow(
            list(order_events),
            current_start,
            current_time,
            current_timed=current_timed,
            step=step,
            near_touch_usd=near_touch_usd,
        )
        if (
            enough_current
            and mbo_flow["near_events"] >= _min_mbo
            and mbo_flow["gross"] > 0.0
        ):
            first_mbo_ts = mbo_flow["first_near_ts"]
            mbo_span_start = (
                max(current_start, first_mbo_ts - timedelta(seconds=5))
                if first_mbo_ts is not None
                else current_start
            )
            sub_trades = [
                (px, vol, sgn)
                for ts, px, vol, sgn in current_timed
                if ts >= mbo_span_start
            ]
            if len(sub_trades) < max(5, min_trades // 2):
                sub_trades = current
            l3_matched_aggr_imb = _delta_imbalance(sub_trades)
            mbo_imb = float(mbo_flow["imbalance"])
            l3_aggr_val = 0.55 * mbo_imb + 0.45 * l3_matched_aggr_imb
            # Kyle's lambda / passive absorption check (Easley et al. 2012; Bouchaud 2018):
            # if net order flow pushes one way while actual bar price displaces >= 2 ticks
            # the opposite way, passive institutional limit liquidity is absorbing aggressors.
            if l3_aggr_val * bar_disp < 0.0 and abs(bar_disp) >= 2.0 * step:
                l3_aggr_val *= 0.35
                l3_absorption_damped = True
            _tf_scale = max(0.20, float(_bar_s) / 300.0)
            _micro_scale = 0.40 if float(min_big_print_size) <= 2.5 else 1.0
            _min_bar_vol = max(8.0, 60.0 * _tf_scale * _micro_scale)
            vol_q = min(1.0, total_current_volume / _min_bar_vol)
            mbo_q = min(1.0, mbo_flow["near_events"] / float(max(_min_mbo * 2, 10)))
            l3_aggr_quality = current_quality * max(0.25, min(1.0, vol_q * mbo_q))

    judges: Dict[str, Any] = {}
    if current:
        judges["footprint_delta"] = {
            "value": current_imbalance,
            "quality": current_quality,
        }
        if l3_aggr_quality > 0:
            judges["l3_aggr_limit"] = {
                "value": max(-1.0, min(1.0, l3_aggr_val)),
                "quality": l3_aggr_quality,
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
        elif name == "l3_aggr_limit" and order_events is None:
            judge_availability[name] = {
                "status": "NOT_EMITTED",
                "reason": "current M5 adapter does not map a validated L3 executed-flow field",
            }
        elif name == "l3_aggr_limit" and not enough_current:
            judge_availability[name] = {
                "status": "MISSING",
                "reason": f"current window has {len(current)} usable direct trades; {min_trades} required",
            }
        elif name == "l3_aggr_limit":
            _near_n = int(mbo_flow["near_events"]) if mbo_flow is not None else 0
            judge_availability[name] = {
                "status": "MISSING",
                "reason": f"near-touch window has {_near_n} usable MBO events; {_min_mbo} required",
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
            "timeframe": f"M{max(1, int(bar_seconds) // 60)}",  # fusionv1
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
            "big_print_absorption_damped": bool(big_absorption_damped),
            "big_print_signed_share_of_total_volume": round(big_value, 4),
            "current_delta_imbalance": round(current_imbalance, 4),
            "previous_delta_imbalance": round(previous_imbalance, 4),
            "mbo_in_window_events": int(mbo_flow["in_window_events"]) if mbo_flow is not None else None,
            "mbo_near_touch_events": int(mbo_flow["near_events"]) if mbo_flow is not None else None,
            "mbo_near_touch_imbalance": round(float(mbo_flow["imbalance"]), 4) if mbo_flow is not None else None,
            "mbo_matched_aggr_imbalance": round(l3_matched_aggr_imb, 4) if mbo_flow is not None else None,
            "l3_aggr_limit_absorption_damped": bool(l3_absorption_damped),
            "note": "No M5 range/breakout judgement is inferred here; l3_aggr_limit requires validated MBO events.",
        },
    }
