"""POWER Team v2: M5 force balance with an explicit NEITHER result.

Pure research/paper logic only: no I/O, broker calls, orders, or hidden fallback.
POWER measures relative directional force; it does not select a price/door.
The percentages are shares of directional evidence, NOT probabilities.

Caller contract:
- Supply normalized signed judge readings from one consistent rolling M5 window.
- +1 means upward evidence, -1 downward, 0 neutral.
- A value may be {"value": x, "quality": q}; q is freshness/reliability [0,1].
- Missing/stale/invalid evidence is excluded, never silently voted as neutral.
- Session-cumulative readings must not be passed as M5 readings.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional
import math

__all__ = ["decide", "describe", "DIRECTIONAL_WEIGHTS", "DIRECTIONAL_FAMILIES",
           "CONTEXT_JUDGES"]

VERSION = "2.2.0"
TIMEFRAME = "M5"

# Group correlated inputs. Executed-flow readings together can contribute no
# more than 60% of total evidence, regardless of how many related metrics exist.
# Weights are conservative design priors, not learned/trading-validated values.
DIRECTIONAL_FAMILIES: Dict[str, Dict[str, Any]] = {
    "executed_flow": {
        "weight": 0.60,
        "members": {
            "footprint_delta": 0.40,
            "l3_aggr_limit": 0.35,
            "cvd_momentum": 0.25,
        },
    },
    "large_executed_trades": {
        "weight": 0.20,
        "members": {"big_prints": 1.0},
    },
    "breadth": {
        "weight": 0.20,
        "members": {"footprint_levels": 1.0},
    },
}

# Flattened per-judge weights are informational; the calculation first caps by
# family so correlated judges cannot overwhelm independent evidence families.
DIRECTIONAL_WEIGHTS: Dict[str, float] = {
    name: family["weight"] * member_weight
    for family in DIRECTIONAL_FAMILIES.values()
    for name, member_weight in family["members"].items()
}

CONTEXT_JUDGES = {
    "sweep": "exclude for now: current project sweep is a stop-hunt/reversal flag, not signed aggressive execution",
    "volume_roc": "activity / fuel; non-directional",
    "absorption": "opposing resistance / force efficiency",
    "flow_efficiency": "new candidate: effort-versus-price-response diagnostic, not a force vote",
    "cvd_divergence": "contradiction / warning",
    "stall_clock": "compression context; not itself a range verdict",
    "value_area": "auction regime context",
    "mtf": "higher-timeframe context; M15 secondary to M5",
    "vwap_bands": "location / stretch context",
    "vwap_trend": "location / slow context",
    "news_sentiment": "event-risk / stand-down context",
    "macro_risk": "slow background context, not M5 force",
    "poc_day": "map / remembered price",
    "htf_poc": "map / remembered price",
    "supply_demand": "map / remembered price",
}

DEFAULTS = {
    "min_coverage": 0.55,
    "min_activity": 0.18,
    "min_dominance": 0.60,
    "min_valid_judges": 3,
    "min_valid_families": 2,
    "min_family_signal": 0.15,
    "min_agreeing_families": 2,
    "range_dominance": 0.70,
    "range_activity": 0.50,
    "range_coverage": 0.70,
    "range_min_valid_judges": 4,
    "range_min_valid_families": 3,
    "range_min_agreeing_families": 3,
}


def _finite_number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _judge_value(raw: Any) -> Optional[tuple[float, float]]:
    if isinstance(raw, Mapping):
        value = _finite_number(raw.get("value"))
        quality = _finite_number(raw.get("quality", 1.0))
    else:
        value = _finite_number(raw)
        quality = 1.0
    if value is None or quality is None or quality <= 0:
        return None
    return max(-1.0, min(1.0, value)), max(0.0, min(1.0, quality))


def decide(
    judges: Mapping[str, Any],
    *,
    context: Optional[Mapping[str, Any]] = None,
    thresholds: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Return UP, DOWN, or NEITHER from normalized signed M5 readings.

    Hard blockers in ``context``: ``feed_stale``, ``data_quality_ok=False``,
    ``high_impact_news``, ``major_contradiction``, ``force_unreliable`` or
    ``out_of_session``. Caller must explicitly provide ``market_regime`` as
    TREND or RANGE. Missing/unknown regime returns NEITHER. RANGE yields NEITHER
    unless ``breakout_confirmed`` is independently established; confirmed range
    breakouts face stricter thresholds and require all evidence families to agree.
    """
    judges = judges if isinstance(judges, Mapping) else {}
    context = context if isinstance(context, Mapping) else {}
    params = dict(DEFAULTS)
    if isinstance(thresholds, Mapping):
        for key in params:
            number = _finite_number(thresholds.get(key))
            if number is not None:
                params[key] = number

    used: Dict[str, Dict[str, float]] = {}
    excluded: Dict[str, str] = {
        str(name): "not in the v2 directional roster; classify as context/other team first"
        for name in judges
        if name not in DIRECTIONAL_WEIGHTS
    }
    family_scores: Dict[str, Dict[str, float]] = {}
    up_force = down_force = available_weight = 0.0

    for family_name, family in DIRECTIONAL_FAMILIES.items():
        member_total = sum(family["members"].values())
        member_available = 0.0
        member_signed = 0.0
        for judge_name, member_weight in family["members"].items():
            if judge_name not in judges:
                excluded[judge_name] = "missing"
                continue
            parsed = _judge_value(judges[judge_name])
            if parsed is None:
                excluded[judge_name] = "invalid, stale, or zero-quality input"
                continue
            value, quality = parsed
            member_effective = member_weight * quality
            member_available += member_effective
            member_signed += member_effective * value
            used[judge_name] = {"value": round(value, 4), "quality": round(quality, 4),
                                "family_weight": family["weight"],
                                "member_weight": member_weight}

        if member_available <= 0:
            family_scores[family_name] = {"score": 0.0, "coverage": 0.0}
            continue

        family_coverage = member_available / member_total if member_total else 0.0
        family_score = member_signed / member_available
        effective_family_weight = family["weight"] * family_coverage
        available_weight += effective_family_weight
        signed_family_force = effective_family_weight * family_score
        up_force += max(signed_family_force, 0.0)
        down_force += max(-signed_family_force, 0.0)
        family_scores[family_name] = {
            "score": round(family_score, 4),
            "coverage": round(family_coverage, 4),
        }

    coverage = available_weight  # total configured family weights equal 1.0
    total_force = up_force + down_force
    if total_force > 1e-12:
        up_pct = 100.0 * up_force / total_force
        down_pct = 100.0 - up_pct
        net_force = (up_force - down_force) / total_force
    else:
        up_pct = down_pct = 50.0
        net_force = 0.0

    activity = total_force / available_weight if available_weight > 1e-12 else 0.0
    activity = max(0.0, min(1.0, activity))
    dominant_share = max(up_pct, down_pct) / 100.0
    valid_family_count = sum(1 for f in family_scores.values() if f["coverage"] > 0)
    family_floor = params["min_family_signal"]
    supporting_up = sum(1 for values in family_scores.values()
                        if values["coverage"] > 0 and values["score"] >= family_floor)
    supporting_down = sum(1 for values in family_scores.values()
                          if values["coverage"] > 0 and values["score"] <= -family_floor)
    regime = str(context.get("market_regime", "UNKNOWN") or "UNKNOWN").upper()
    breakout_confirmed = bool(context.get("breakout_confirmed", False))
    in_range = regime == "RANGE"

    blockers = (
        ("feed_stale", "STALE_FEED"),
        ("data_quality_ok", "BAD_DATA_QUALITY"),
        ("high_impact_news", "HIGH_IMPACT_EVENT"),
        ("major_contradiction", "MAJOR_CONTRADICTION"),
        ("force_unreliable", "FORCE_UNRELIABLE"),
        ("out_of_session", "OUT_OF_SESSION"),
    )
    direction, reason = "NEITHER", "UNKNOWN"
    for flag, code in blockers:
        if (flag == "data_quality_ok" and context.get(flag) is False) or (
                flag != "data_quality_ok" and bool(context.get(flag))):
            direction, reason = "NEITHER", code
            break
    else:
        if regime not in {"TREND", "RANGE"}:
            direction, reason = "NEITHER", "REGIME_UNKNOWN_OR_UNCONFIRMED"
        elif in_range and not breakout_confirmed:
            direction, reason = "NEITHER", "RANGE_NO_CONFIRMED_BREAKOUT"
        else:
            strict_range = in_range and breakout_confirmed
            min_coverage = params["range_coverage"] if strict_range else params["min_coverage"]
            min_activity = params["range_activity"] if strict_range else params["min_activity"]
            min_dominance = params["range_dominance"] if strict_range else params["min_dominance"]
            min_judges = int(params["range_min_valid_judges"] if strict_range
                             else params["min_valid_judges"])
            min_families = int(params["range_min_valid_families"] if strict_range
                               else params["min_valid_families"])
            min_agreeing = int(params["range_min_agreeing_families"] if strict_range
                               else params["min_agreeing_families"])
            if (len(used) < min_judges or valid_family_count < min_families
                    or coverage < min_coverage):
                direction, reason = "NEITHER", "INSUFFICIENT_COVERAGE"
            elif activity < min_activity or total_force <= 1e-12:
                direction, reason = "NEITHER", "WEAK_OR_NO_FORCE"
            elif dominant_share < min_dominance:
                direction, reason = "NEITHER", "FORCE_TOO_BALANCED"
            else:
                if up_pct > down_pct and supporting_up >= min_agreeing:
                    direction, reason = "UP", "UPWARD_FORCE_DOMINATES"
                elif down_pct > up_pct and supporting_down >= min_agreeing:
                    direction, reason = "DOWN", "DOWNWARD_FORCE_DOMINATES"
                elif up_pct > down_pct or down_pct > up_pct:
                    direction, reason = "NEITHER", "INSUFFICIENT_FAMILY_AGREEMENT"
                else:
                    direction, reason = "NEITHER", "FORCE_TOO_BALANCED"

    context_keys = (
        "market_regime", "breakout_confirmed", "high_impact_news", "feed_stale",
        "data_quality_ok", "major_contradiction", "force_unreliable",
        "out_of_session", "volume_roc", "absorption", "flow_efficiency",
        "cvd_divergence", "sweep", "stall_clock", "value_area", "mtf",
        "vwap_bands", "vwap_trend", "news_sentiment", "macro_risk",
    )
    return {
        "team": "POWER",
        "version": VERSION,
        "timeframe": TIMEFRAME,
        "direction": direction,
        "up_power_pct": round(up_pct, 1),
        "down_power_pct": round(down_pct, 1),
        "power_total_pct": 100.0,
        "net_force": round(net_force, 4),
        "activity": round(activity, 4),
        "coverage": round(coverage, 4),
        "valid_families": [k for k, v in family_scores.items() if v["coverage"] > 0],
        "supporting_up_families": supporting_up,
        "supporting_down_families": supporting_down,
        "family_scores": family_scores,
        "valid_judges": list(used),
        "judge_readings": used,
        "excluded_judges": excluded,
        "context": {key: context.get(key) for key in context_keys if key in context},
        "reason_code": reason,
        "meaning": "force share, not probability; NEITHER means do not pass a side",
    }


def describe(answer: Mapping[str, Any]) -> str:
    if not isinstance(answer, Mapping):
        return "POWER M5: NEITHER — no result"
    return (
        f"POWER M5: {answer.get('direction', 'NEITHER')} | "
        f"up {answer.get('up_power_pct', 50.0):.1f}% / "
        f"down {answer.get('down_power_pct', 50.0):.1f}% "
        f"(total {answer.get('power_total_pct', 100.0):.0f}%) | "
        f"activity {answer.get('activity', 0.0):.2f}, "
        f"coverage {answer.get('coverage', 0.0):.0%} | "
        f"{answer.get('reason_code', 'UNKNOWN')} — force share, not probability"
    )
