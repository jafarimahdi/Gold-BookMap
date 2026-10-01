"""Shooting: pre-entry permission and plan generation (analysis/paper only).

Signal/Scout supplies locations; POWER v2 supplies UP/DOWN/NEITHER force. Shooting
checks route, quote quality, queue context, stop/target geometry and entry style. It
never submits an order. Aggressive entry is unavailable unless a separate, explicit
and reviewed impulse trigger is supplied; Power direction alone is not that trigger.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional

__all__ = ["plan_shot", "describe"]


def _num(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


def _cfg(config: Any, key: str, default: float) -> float:
    value = _num(getattr(config, key, default)) if config is not None else default
    return value if value is not None else default


def _wait(reason: str, price: Any = 0.0, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    p = _num(price)
    result = {"shot": "WAIT", "side": None, "why": [reason],
              "entry": p, "target": None, "stop": None, "rr": 0.0,
              "entry_style": None, "execution_status": "PLAN_ONLY"}
    if extra:
        result.update(extra)
    return result


def _power_gate(power: Any, entry_context: Mapping[str, Any], config: Any) -> Optional[str]:
    if not isinstance(power, Mapping):
        return "POWER v2 result is missing; wait"
    direction = str(power.get("direction", "NEITHER") or "NEITHER").upper()
    if direction not in {"UP", "DOWN"}:
        return f"POWER v2 says {direction}; no directional shot"
    if power.get("shadow_only") is True:
        return "POWER v2 result is marked shadow-only; do not pass a side"
    if power.get("team") not in (None, "POWER"):
        return "POWER result has an unexpected team identity"
    expected_reason = "UPWARD_FORCE_DOMINATES" if direction == "UP" else "DOWNWARD_FORCE_DOMINATES"
    if power.get("reason_code") not in (None, expected_reason):
        return "POWER v2 direction and reason code disagree; wait"

    context = power.get("context") or {}
    diagnostics = power.get("regime_diagnostics") or {}
    regime = str(diagnostics.get("market_regime") or context.get("market_regime") or "UNKNOWN").upper()
    if regime != "TREND":
        return f"POWER regime is {regime}; this first pass only permits confirmed TREND"
    if context.get("feed_stale") is not False or diagnostics.get("reason") == "STALE_FEED":
        return "POWER feed is stale or freshness is unconfirmed"
    if context.get("data_quality_ok") is not True:
        return "POWER data quality is bad or unconfirmed"
    age = _num(diagnostics.get("latest_tick_age_seconds"))
    max_age = _cfg(config, "POWER_MAX_TICK_AGE_SECONDS", 60.0)
    if age is None or age < 0 or age > max_age:
        return "POWER tick freshness is missing or outside the allowed window"

    up = _num(power.get("up_power_pct"))
    down = _num(power.get("down_power_pct"))
    total = _num(power.get("power_total_pct"))
    if (up is None or down is None or total is None or not (0 <= up <= 100)
            or not (0 <= down <= 100) or abs(total - 100.0) > 0.2
            or abs(up + down - 100.0) > 0.2):
        return "POWER force-share fields are malformed; wait"

    if entry_context.get("news_state") == "BLACKOUT":
        return "news blackout; Shooting will not plan an entry"
    if entry_context.get("data_quality_ok") is not True:
        return "market input quality is bad or unconfirmed"
    age_data = _num(entry_context.get("last_data_age_seconds"))
    stale_after = _cfg(config, "STALE_DATA_SECONDS", 300.0)
    if age_data is None or age_data < 0 or age_data > stale_after:
        return "market feed age is missing, invalid, or stale"
    if entry_context.get("has_data") is not True:
        return "current market data is missing or unconfirmed"
    return None


def _nearest_door(signal_map: Mapping[str, Any], side: str, price: float) -> Optional[Dict[str, Any]]:
    key = "above" if side == "UP" else "below"
    candidates = []
    for raw in (signal_map.get(key) or []):
        if not isinstance(raw, Mapping):
            continue
        door_price = _num(raw.get("price"))
        size = _num(raw.get("size"))
        if door_price is None or door_price <= 0 or size is None or size <= 0:
            continue
        if side == "UP" and door_price > price:
            candidates.append((door_price - price, dict(raw)))
        elif side == "DOWN" and door_price < price:
            candidates.append((price - door_price, dict(raw)))
    return min(candidates, key=lambda item: item[0])[1] if candidates else None


def _geometry(entry: float, side: str, door: Mapping[str, Any], shelter: Optional[Mapping[str, Any]],
              atr: float, spread: float, config: Any) -> Dict[str, Any]:
    door_price = _num(door.get("price"))
    if door_price is None:
        return {"eligible": False, "reason": "target door has no finite price"}
    buffer = max(spread * 2.0, _cfg(config, "PM_TP_BUFFER_ATR", 0.15) * atr)
    is_buy = side == "UP"
    target = door_price - buffer if is_buy else door_price + buffer
    stop_source = "BOOK_SHELTER" if shelter else "ATR_FALLBACK"
    if shelter:
        shelter_price = _num(shelter.get("price"))
        if shelter_price is None or shelter_price <= 0:
            return {"eligible": False, "reason": "shelter price is invalid"}
        stop = shelter_price - buffer if is_buy else shelter_price + buffer
    else:
        # Explicitly approved fallback: two ATR behind the entry.
        stop = entry - 2.0 * atr if is_buy else entry + 2.0 * atr

    correct = (target > entry and stop < entry) if is_buy else (target < entry and stop > entry)
    if not correct:
        return {"eligible": False, "reason": "target/stop are not on the safe sides of entry"}
    reward, risk = abs(target - entry), abs(entry - stop)
    rr = reward / risk if risk > 1e-12 else 0.0
    return {"eligible": True, "entry": entry, "target": target, "stop": stop,
            "reward": reward, "risk": risk, "rr": rr, "stop_source": stop_source,
            "shelter": dict(shelter) if shelter else None}


def plan_shot(signal_map: Dict[str, Any], power: Dict[str, Any],
              price: float, atr: float, spread: float = 0.0,
              config: Any = None, book=None,
              entry_context: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Create a fail-closed entry plan. No order is ever transmitted.

    Passive limit is the default. Aggressive market is considered only when an
    independent caller provides ``aggressive_trigger_confirmed=True`` and a reason.
    The current pipeline intentionally provides no such trigger yet.
    """
    ctx = dict(entry_context or {})
    bad_power = _power_gate(power, ctx, config)
    if bad_power:
        return _wait(bad_power, price)

    p, a, sp = _num(price), _num(atr), _num(spread)
    if p is None or p <= 0 or a is None or a <= 0 or sp is None or sp <= 0:
        return _wait("price, ATR, or spread is missing/invalid; wait", price)
    bid, ask = _num(ctx.get("bid")), _num(ctx.get("ask"))
    if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid or (ask - bid) <= 0:
        return _wait("valid, uncrossed bid/ask quotes are required", price)
    if abs((ask - bid) - sp) > max(0.02, sp * 0.20):
        return _wait("reported spread and bid/ask quotes disagree", price)

    direction = str(power.get("direction")).upper()
    target_door = _nearest_door(signal_map or {}, direction, p)
    if not target_door:
        return _wait(f"no valid Scout door on the POWER v2 {direction} side", p)

    target_px = _num(target_door.get("price"))
    target_size = _num(target_door.get("size")) or 0.0
    raw_road_count = _num(target_door.get("doors_between"))
    raw_road_lots = _num(target_door.get("lots_between"))
    if (raw_road_count is None or raw_road_lots is None
            or raw_road_count < 0 or raw_road_lots < 0):
        return _wait("Scout route obstruction data is missing or invalid", p,
                     {"door": dict(target_door)})
    road_count = int(raw_road_count)
    road_lots = raw_road_lots
    route_clear = target_door.get("road_clear") is True
    road_ratio = road_lots / target_size if target_size > 0 else math.inf
    road_limit = _cfg(config, "SHOOT_ROAD_RATIO", 6.0)
    if road_ratio >= road_limit:
        return _wait(f"route is too crowded ({road_ratio:.1f}x target size; limit {road_limit:.1f}x)", p,
                     {"door": dict(target_door), "road_ratio": road_ratio})

    is_buy = direction == "UP"
    side = "BUY" if is_buy else "SELL"
    passive_entry = bid if is_buy else ask
    aggressive_entry = ask if is_buy else bid
    queue = ctx.get("queue_estimate") if isinstance(ctx.get("queue_estimate"), Mapping) else None
    queue_prob = _num(queue.get("fill_prob")) if queue else None
    queue_total = _num(queue.get("queue_total_vol")) if queue else None
    queue_known = queue_prob is not None and queue_total is not None and queue_total > 0
    queue_floor = _cfg(config, "SHOOT_QUEUE_MIN_FILL_PROB", 0.70)
    queue_blocks_limit = bool(ctx.get("queue_enabled", True) and queue_known and queue_prob < queue_floor)

    passive_shelter = None
    aggressive_shelter = None
    if book is not None:
        try:
            passive_shelter = book.shelter_behind(passive_entry, is_buy)
        except Exception:
            passive_shelter = None
        try:
            aggressive_shelter = book.shelter_behind(aggressive_entry, is_buy)
        except Exception:
            aggressive_shelter = None

    passive = _geometry(passive_entry, direction, target_door, passive_shelter, a, sp, config)
    aggressive = _geometry(aggressive_entry, direction, target_door, aggressive_shelter, a, sp, config)
    min_rr = _cfg(config, "SHOOT_MIN_RR", 1.2)
    min_reward_usd = _cfg(config, "SHOOT_MIN_REWARD_USD", 0.0)

    def cost_check(plan: Dict[str, Any]) -> Optional[str]:
        if not plan.get("eligible"):
            return str(plan.get("reason") or "invalid plan geometry")
        if plan["reward"] <= sp * 3.0:
            return "reward does not exceed three times the spread"
        if plan["rr"] < min_rr:
            return f"R:R {plan['rr']:.2f} is below {min_rr:.2f}"
        if min_reward_usd > 0:
            usd_per_point = _num(ctx.get("usd_per_price_unit"))
            lots = _num(ctx.get("trade_lots"))
            if usd_per_point is None or usd_per_point <= 0 or lots is None or lots <= 0:
                return "USD reward floor is enabled but verified CFD money conversion is missing"
            if plan["reward"] * usd_per_point * lots < min_reward_usd:
                return "estimated monetary reward is below the configured USD floor"
        return None

    passive_reason = cost_check(passive)
    aggressive_reason = cost_check(aggressive)
    trigger = ctx.get("aggressive_trigger_confirmed") is True
    trigger_reason = str(ctx.get("aggressive_trigger_reason") or "").strip()
    aggressive_allowed = (trigger and bool(trigger_reason) and aggressive_reason is None
                          and route_clear)
    if trigger and trigger_reason and aggressive_reason is None and not route_clear:
        aggressive_reason = "Scout has not confirmed a clear route for aggressive entry"
    passive_allowed = passive_reason is None and not queue_blocks_limit

    if queue_blocks_limit:
        passive_reason = f"estimated limit fill probability {queue_prob:.2f} is below {queue_floor:.2f}"
    if not trigger:
        aggressive_reason = "no separately confirmed aggressive-entry trigger is wired"
    elif not trigger_reason:
        aggressive_allowed = False
        aggressive_reason = "aggressive trigger has no audit reason"

    if not passive_allowed and not aggressive_allowed:
        why = [passive_reason or "passive limit is not eligible",
               aggressive_reason or "aggressive market is not eligible"]
        return {"shot": "WAIT", "side": None, "why": why, "entry": p,
                "target": None, "stop": None, "rr": 0.0, "door": dict(target_door),
                "entry_style": None, "execution_status": "PLAN_ONLY",
                "queue_estimate": dict(queue) if queue else None,
                "entry_options": {"PASSIVE_LIMIT": {**passive, "reason": passive_reason},
                                  "AGGRESSIVE_MARKET": {**aggressive, "reason": aggressive_reason}}}

    # Patient limit is preferred whenever the queue is acceptable/unknown. Use market
    # entry only if independently triggered and the limit queue is demonstrably poor
    # (or the passive geometry is invalid).
    choose_aggressive = aggressive_allowed and (queue_blocks_limit or not passive_allowed)
    chosen = aggressive if choose_aggressive else passive
    style = "AGGRESSIVE_MARKET" if choose_aggressive else "PASSIVE_LIMIT"
    mode_label = "market" if choose_aggressive else "working limit"
    force_shares = {"up": _num(power.get("up_power_pct")),
                    "down": _num(power.get("down_power_pct")),
                    "meaning": "force shares, not probabilities"}
    why = [f"POWER v2 {direction}; force shares are not probabilities",
           f"nearest viable Scout door {target_px:.2f} ({target_size:.1f} lots)",
           f"route {road_count} level(s), {road_lots:.1f} lots; ratio {road_ratio:.2f}x",
           f"{mode_label} entry; stop source {chosen['stop_source']}",
           f"reward {chosen['reward']:.2f} vs risk {chosen['risk']:.2f} -> R:R {chosen['rr']:.2f}"]
    if queue_known:
        why.append(f"estimated passive fill probability {queue_prob:.2f}")
    else:
        why.append("queue estimate unavailable; limit fill is not assumed")
    if chosen["stop_source"] == "ATR_FALLBACK":
        why.append("no shelter available; explicitly approved 2-ATR fallback used")
    if choose_aggressive:
        why.append(f"aggressive trigger: {trigger_reason}")
    return {"shot": "GO", "side": side, "why": why,
            "entry": round(chosen["entry"], 6), "target": round(chosen["target"], 6),
            "stop": round(chosen["stop"], 6), "rr": round(chosen["rr"], 4),
            "reward": round(chosen["reward"], 6), "risk": round(chosen["risk"], 6),
            "door": dict(target_door), "first_stop": target_door.get("biggest_in_path"),
            "shelter": chosen["shelter"], "stop_source": chosen["stop_source"],
            "doors_between": road_count, "lots_between": road_lots,
            "road_ratio": round(road_ratio, 4), "entry_style": style,
            "entry_options": {"PASSIVE_LIMIT": {**passive, "reason": passive_reason},
                              "AGGRESSIVE_MARKET": {**aggressive,
                                  "eligible": aggressive_allowed,
                                  "reason": aggressive_reason,
                                  "trigger_reason": trigger_reason or None}},
            "queue_estimate": dict(queue) if queue else None,
            "power_force_shares": force_shares,
            "execution_status": "PLAN_ONLY"}


def describe(plan: Dict[str, Any]) -> str:
    if not plan:
        return "SHOOTER: no plan"
    if plan.get("shot") != "GO":
        return f"SHOOTER: {plan.get('shot')} - {(plan.get('why') or ['?'])[-1]}"
    return (f"SHOOTER: GO {plan['side']} {plan.get('entry_style', 'PLAN')} @ "
            f"{plan['entry']:.2f} -> target {plan['target']:.2f}, "
            f"stop {plan['stop']:.2f} (R:R {plan['rr']:.2f}) | "
            f"road {plan['doors_between']} levels / {plan['lots_between']:.0f} lots "
            f"| PLAN ONLY")
