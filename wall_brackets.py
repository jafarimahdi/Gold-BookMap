"""wall_brackets.py — v8 exit philosophy: walls decide where, force decides if.

USER DECISION (2026-10-07): the TEAMS path is the real market (stop-hunting and
liquidity pools live at the walls). So the money path gets the same brackets:

    TP = just BEFORE the opposing wall   (take profit before the liquidity pool)
    SL = just BEHIND the wall behind us  (only a real break of the wall kills us)
    fallback = 2xATR behind entry + ATR-multiple target when no trusted wall

PLUS the pre-entry VALUE GATE (user requirement): open a position only when the
reward beats the risk (ENTRY_MIN_RR) AND is worth taking at all
(ENTRY_MIN_REWARD_ATR x ATR). If the setup is not worth it -> say so and skip.

Trust rule: a wall counts only if it is big enough (WALL_MIN_LOTS lots).
Analysis-only module: it computes numbers, it never sends orders.
"""
from __future__ import annotations

import math
from typing import Any, List, Optional, Tuple

__all__ = ["compute_wall_brackets", "value_gate"]


def _num(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        x = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return x if math.isfinite(x) else default


def _doors(snapshot: Any, key: str) -> List[dict]:
    sm = getattr(snapshot, "signal_map", None)
    if not isinstance(sm, dict):
        return []
    out = []
    for raw in (sm.get(key) or []):
        if not isinstance(raw, dict):
            continue
        px = _num(raw.get("price"))
        size = _num(raw.get("size"), 0.0) or 0.0
        if px is None or px <= 0 or size <= 0:
            continue
        out.append({"price": px, "size": size})
    return out


def _futures_to_cfd_basis(px: float, is_buy: bool, snapshot: Any,
                          eff_spread: float) -> float:
    """Return additive (futures_ref - cfd_px) basis to shift signal_map doors onto CFD scale.

    Returns 0.0 when ``snapshot.price`` is absent/invalid or when ``px`` is already on
    the same price scale as ``snapshot`` (e.g., unit tests or futures-scale replay).
    """
    src_price = _num(getattr(snapshot, "price", None))
    if src_price is None or src_price <= 0:
        return 0.0
    data_sym = str(getattr(snapshot, "data_symbol", "") or "").strip().upper()
    trade_sym = str(getattr(snapshot, "trade_symbol", "") or "").strip().upper()
    same_symbol = (not data_sym or not trade_sym or data_sym == trade_sym)
    if same_symbol and abs(src_price - px) <= max(eff_spread * 2.0, 1e-6):
        return 0.0
    bid = _num(getattr(snapshot, "bid", 0.0), 0.0) or 0.0
    ask = _num(getattr(snapshot, "ask", 0.0), 0.0) or 0.0
    fut_ref = (ask if is_buy else bid) if (ask > bid > 0) else src_price
    return fut_ref - px


def compute_wall_brackets(action: str, price: float, atr: float,
                          snapshot: Any, cfd_spread: float = 0.0
                          ) -> Tuple[Optional[float], Optional[float], List[str]]:
    """Return (sl, tp, notes) on ``price``'s (CFD) scale. (None, None, notes) -> fallback."""
    notes: List[str] = []
    px = _num(price)
    a = _num(atr, 0.0) or 0.0
    if px is None or px <= 0:
        return None, None, ["wall brackets: no valid price"]

    bid = _num(getattr(snapshot, "bid", 0.0), 0.0) or 0.0
    ask = _num(getattr(snapshot, "ask", 0.0), 0.0) or 0.0
    fut_spread = (ask - bid) if (ask > bid > 0) else 0.0
    broker_sp = _num(cfd_spread, 0.0) or 0.0
    spread = max(fut_spread, broker_sp if broker_sp > 0 else 0.0)
    buf = max(spread * 2.0, 0.15 * a)
    min_lots = 5.0  # WALL_MIN_LOTS default; trusts big walls only

    is_buy = str(action).upper() == "BUY"
    basis = _futures_to_cfd_basis(px, is_buy, snapshot, spread)

    def _shifted_doors(key: str) -> List[dict]:
        shifted: List[dict] = []
        for d in _doors(snapshot, key):
            cfd_px = d["price"] - basis
            if cfd_px > 0 and math.isfinite(cfd_px):
                shifted.append({
                    "price": cfd_px,
                    "fut_price": d["price"],
                    "size": d["size"],
                })
        return shifted

    opposing = [d for d in _shifted_doors("above" if is_buy else "below")
                if d["size"] >= min_lots and (d["price"] > px if is_buy else d["price"] < px)]
    behind = [d for d in _shifted_doors("below" if is_buy else "above")
              if d["size"] >= min_lots and (d["price"] < px if is_buy else d["price"] > px)]
    if not opposing:
        return None, None, ["wall brackets: no trusted opposing wall -> fallback"]
    target_wall = min(opposing, key=lambda d: d["price"] - px if is_buy else px - d["price"])
    tp = (target_wall["price"] - buf) if is_buy else (target_wall["price"] + buf)

    if behind:
        shelter = max(behind, key=lambda d: d["price"] if is_buy else -d["price"])
        sl = (shelter["price"] - buf) if is_buy else (shelter["price"] + buf)
        sl_src = f"wall {shelter['price']:.2f} ({shelter['size']:.0f} lots)"
    else:
        sl = (px - 2.0 * a) if is_buy else (px + 2.0 * a)
        sl_src = "2xATR fallback (no wall behind)"

    ok = (tp > px > sl) if is_buy else (tp < px < sl)
    if not ok:
        return None, None, ["wall brackets: unsafe geometry -> fallback"]
    basis_note = (f", basis {basis:+.2f} from fut {target_wall['fut_price']:.2f}"
                  if abs(basis) > 1e-6 else "")
    notes.append(f"wall brackets: TP {tp:.2f} (wall {target_wall['price']:.2f}/"
                 f"{target_wall['size']:.0f} lots{basis_note}), SL {sl:.2f} ({sl_src})")
    return sl, tp, notes


def value_gate(action: str, price: float, sl: float, tp: float, atr: float,
               config: Any, *, min_rr_override: Optional[float] = None,
               min_reward_atr_override: Optional[float] = None) -> Tuple[bool, str]:
    """Reward must beat risk AND be worth taking. Fail-closed on missing numbers."""
    px, s, t, a = _num(price), _num(sl), _num(tp), _num(atr, 0.0) or 0.0
    if px is None or s is None or t is None:
        return False, "value gate: missing price/SL/TP numbers"
    reward = abs(t - px)
    risk = abs(px - s)
    if risk <= 0 or reward <= 0:
        return False, "value gate: degenerate reward/risk"
    rr = reward / risk
    min_rr = (_num(min_rr_override, 1.0) if min_rr_override is not None
              else _num(getattr(config, "ENTRY_MIN_RR", 1.2), 1.2))
    min_reward_atr = (_num(min_reward_atr_override, 0.0)
                      if min_reward_atr_override is not None
                      else _num(getattr(config, "ENTRY_MIN_REWARD_ATR", 1.0), 1.0))
    if rr < min_rr:
        return False, (f"value gate: reward/risk {rr:.2f} < {min_rr:.2f} "
                       f"(reward {reward:.2f}, risk {risk:.2f}) - not worth the risk")
    if a > 0 and min_reward_atr > 0 and reward < min_reward_atr * a:
        return False, (f"value gate: reward {reward:.2f} < {min_reward_atr:.2f} ATR "
                       f"({min_reward_atr * a:.2f}) - no value in the target")
    return True, f"value gate: R:R {rr:.2f}, reward {reward:.2f} vs risk {risk:.2f}"
