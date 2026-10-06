"""aggressive_trigger.py — the reviewed impulse trigger for aggressive entries.

THE MISSING PIECE
    The Shooting pipeline plans two entry styles: passive limit (default) and
    aggressive market. Aggressive is only permitted when an independent caller
    stamps the entry context with ``aggressive_trigger_confirmed=True`` plus a
    reason. The pipeline has hard-coded that flag to False ("no empirically
    reviewed aggressive trigger is available in this build"). This module IS
    that trigger.

USER DECISION (2026-10-06)
    Opening positions use aggressive (marketable) entries only. Passive limit
    entries are a later version. So this trigger is the gate that decides when
    an aggressive opening is justified.

DESIGN LAW
    Fail-closed and deterministic. On ANY missing evidence or doubt it returns
    confirmed=False with a human-readable reason. It NEVER sends orders; it
    only returns a permission flag + reason for the Shooting entry context.
    The paper-entry simulator estimates the fill; Escort only wakes on a
    confirmed paper fill. Real order flow remains a separate, gated path.

CONDITIONS (all must hold, checked in order)
    1. AGGRESSIVE_ENTRY_ENABLED=1 in .env (default 0 -> trigger never fires)
    2. POWER v2 direction is UP or DOWN (NEITHER can never trigger)
    3. POWER regime_gate and safety_gate are PASS when those fields exist
       (a directional POWER decision already implies force checks passed;
        these two are belt-and-braces and are treated FAIL if missing-and-
       required-by-strict-mode, PASS-if-absent in normal mode only when the
       direction itself is directional and force checks are not present)
    4. fresh, quality market data (has_data + quality + tick age)
    5. spread within AGGRESSIVE_SPREAD_MAX_PCT (default: MAX_SPREAD_PCT)
    6. news is not in a blackout
    7. IMPULSE: aggressive order flow agrees with the direction
       (buy% - sell% for UP, sell% - buy% for DOWN >= AGGRESSIVE_FLOW_EDGE,
        in percentage points). If L3 net-flow sign is available and opposes
        the direction, the trigger is refused.

CONFIG (read from environment first, then config module, then default)
    AGGRESSIVE_ENTRY_ENABLED        default 0    master switch
    AGGRESSIVE_FLOW_EDGE            default 2.0  pp of flow agreement needed
    AGGRESSIVE_SPREAD_MAX_PCT       default MAX_SPREAD_PCT (or 0.05)
    AGGRESSIVE_MAX_TICK_AGE_SECONDS default POWER_MAX_TICK_AGE_SECONDS (or 60)
"""
from __future__ import annotations

import math
import os
from typing import Any, Dict, Mapping, Optional

__all__ = ["evaluate_aggressive_trigger", "TRIGGER_KEYS"]

TRIGGER_KEYS = (
    "AGGRESSIVE_ENTRY_ENABLED",
    "AGGRESSIVE_FLOW_EDGE",
    "AGGRESSIVE_SPREAD_MAX_PCT",
    "AGGRESSIVE_MAX_TICK_AGE_SECONDS",
)


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return x if math.isfinite(x) else default


def _cfg(config: Any, key: str, default: Any) -> Any:
    """Environment first (user-editable .env), then config object, then default."""
    raw = os.environ.get(key)
    if raw is not None and str(raw).strip() != "":
        try:
            return float(str(raw).strip())
        except ValueError:
            return default
    value = getattr(config, key, None)
    if value is not None:
        return value
    return default


def _get(obj: Any, *names: str) -> Any:
    """Read the first present field from a mapping OR an object (dataclass)."""
    for n in names:
        if isinstance(obj, Mapping):
            v = obj.get(n)
        else:
            v = getattr(obj, n, None)
        if v is not None:
            return v
    return None


def _flow_edge(order_flow: Any, direction: str) -> Optional[float]:
    """Signed flow agreement in percentage points for `direction`."""
    buy = _num(_get(order_flow, "buy_pct", "buy_percent"))
    sell = _num(_get(order_flow, "sell_pct", "sell_percent"))
    if buy is None or sell is None:
        bv = _num(_get(order_flow, "aggressive_buy_volume"))
        sv = _num(_get(order_flow, "aggressive_sell_volume"))
        if bv is None or sv is None:
            bv = _num(_get(order_flow, "aggressive_buys", "aggressive_buy_count"))
            sv = _num(_get(order_flow, "aggressive_sells", "aggressive_sell_count"))
        if bv is not None and sv is not None and (bv + sv) > 0:
            buy, sell = 100.0 * bv / (bv + sv), 100.0 * sv / (bv + sv)
    if buy is None or sell is None:
        return None
    return (buy - sell) if direction == "UP" else (sell - buy)


def _l3_opposes(level3: Any, direction: str) -> Optional[bool]:
    """True if L3 net-flow sign clearly opposes the direction (None = unknown)."""
    net = _num(_get(level3, "net_aggressive_flow", "aggressive_net_flow", "net_flow"))
    if net is None:
        buy = _num(_get(level3, "aggressive_buy_volume", "aggressive_buys",
                        "aggressive_buy_count"))
        sell = _num(_get(level3, "aggressive_sell_volume", "aggressive_sells",
                         "aggressive_sell_count"))
        if buy is not None and sell is not None:
            net = buy - sell
    if net is None or net == 0:
        return None
    return (net < 0) if direction == "UP" else (net > 0)


def evaluate_aggressive_trigger(
    *,
    power: Any,
    shot_context: Mapping[str, Any],
    config: Any,
    price: float,
    atr: float = 0.0,
    order_flow: Any = None,
    level3: Any = None,
    signal_map: Any = None,
) -> Dict[str, Any]:
    """Return {'confirmed': bool, 'reason': str, 'checks': {...}} — fail-closed."""
    checks: Dict[str, Any] = {}
    p = power if isinstance(power, Mapping) else {}
    ctx = dict(shot_context or {})

    def no(reason: str) -> Dict[str, Any]:
        return {"confirmed": False, "reason": reason, "checks": checks}

    # 1. master switch
    enabled = _cfg(config, "AGGRESSIVE_ENTRY_ENABLED", 0)
    checks["enabled"] = enabled
    if not (enabled == 1 or enabled is True or str(enabled) == "1"):
        return no("aggressive trigger disabled (AGGRESSIVE_ENTRY_ENABLED!=1)")

    # 2. POWER must be directional (production dict: direction + reason_code)
    direction = str(_get(p, "direction", "decision") or "NEITHER").upper()
    checks["direction"] = direction
    if direction not in ("UP", "DOWN"):
        return no(f"POWER direction is {direction or 'UNKNOWN'}; aggressive needs UP/DOWN")

    # 3. POWER gates. NOTE: in the live pipeline regime_gate/safety_gate are
    #    computed inside step2 and only printed to notes; the decision dict
    #    carries the outcome through `reason_code`. Accept either evidence:
    #    explicit PASS gates, or the matching dominant-force reason code.
    reason_code = str(_get(p, "reason_code", "reason") or "").upper()
    expected = "UPWARD_FORCE_DOMINATES" if direction == "UP" else "DOWNWARD_FORCE_DOMINATES"
    force_reason = (reason_code == expected)
    if reason_code.startswith(("UPWARD", "DOWNWARD")) and not force_reason:
        return no(f"POWER reason_code {reason_code} disagrees with direction {direction}")
    ctx_p = p.get("context") if isinstance(p.get("context"), Mapping) else {}
    diag = p.get("regime_diagnostics") if isinstance(p.get("regime_diagnostics"), Mapping) else {}
    regime_gate = str(_get(p, "regime_gate") or _get(ctx_p, "regime_gate")
                      or _get(diag, "regime_gate") or "").upper()
    safety_gate = str(_get(p, "safety_gate") or _get(ctx_p, "safety_gate")
                      or _get(diag, "safety_gate") or "").upper()
    checks["regime_gate"] = regime_gate or "ABSENT"
    checks["safety_gate"] = safety_gate or "ABSENT"
    checks["reason_code"] = reason_code or "ABSENT"
    if regime_gate and regime_gate != "PASS":
        return no(f"POWER regime_gate={regime_gate}; aggressive refused")
    if safety_gate and safety_gate != "PASS":
        return no(f"POWER safety_gate={safety_gate}; aggressive refused")
    if not regime_gate and not safety_gate and not force_reason:
        return no(f"POWER gates absent and reason_code={reason_code or '?'}; "
                  "aggressive refused (fail-closed)")

    # 4. data freshness + quality
    if not bool(ctx.get("has_data", False)):
        return no("no market data; aggressive refused")
    if not bool(ctx.get("data_quality_ok", False)):
        return no("data quality not ok; aggressive refused")
    age = _num(ctx.get("last_data_age_seconds"))
    max_age = _num(_cfg(config, "AGGRESSIVE_MAX_TICK_AGE_SECONDS",
                        _cfg(config, "POWER_MAX_TICK_AGE_SECONDS", 60.0)), 60.0)
    checks["tick_age"] = age
    checks["max_tick_age"] = max_age
    if age is None or age < 0 or age > (max_age or 60.0):
        return no(f"tick age {age} beyond {max_age}s; aggressive refused")

    # 5. spread
    bid = _num(ctx.get("bid"))
    ask = _num(ctx.get("ask"))
    spread_pct = None
    if bid and ask and ask > bid > 0:
        spread_pct = 100.0 * (ask - bid) / bid
    checks["spread_pct"] = spread_pct
    max_spread = _num(_cfg(config, "AGGRESSIVE_SPREAD_MAX_PCT",
                           _cfg(config, "MAX_SPREAD_PCT", 0.05)), 0.05)
    if spread_pct is None:
        return no("no valid bid/ask; aggressive refused")
    if spread_pct > (max_spread or 0.05):
        return no(f"spread {spread_pct:.3f}% above {max_spread}%; aggressive refused")

    # 6. news blackout
    news_state = str(ctx.get("news_state") or "").upper()
    checks["news_state"] = news_state or "ABSENT"
    if news_state in ("BLACKOUT", "WARNING"):
        return no(f"news state {news_state}; aggressive refused")

    # 7. impulse: flow must agree with the direction
    edge = _flow_edge(order_flow, direction)
    checks["flow_edge_pp"] = edge
    min_edge = _num(_cfg(config, "AGGRESSIVE_FLOW_EDGE", 2.0), 2.0)
    if edge is None:
        return no("no order-flow evidence; aggressive refused (fail-closed)")
    if edge < (min_edge or 2.0):
        return no(f"flow edge {edge:+.2f}pp below {min_edge}pp; aggressive refused")
    if _l3_opposes(level3, direction):
        return no("L3 net flow opposes the direction; aggressive refused")

    px = _num(price)
    checks["price"] = px
    checks["atr"] = _num(atr)
    return {
        "confirmed": True,
        "reason": (f"aggressive {direction} impulse confirmed: flow edge "
                   f"{edge:+.2f}pp >= {min_edge}pp, spread {spread_pct:.3f}%, "
                   f"tick age {age:.1f}s, POWER gates green"),
        "checks": checks,
    }
