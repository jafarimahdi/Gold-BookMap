"""
markets.py
==========
Market & symbol registry for the gold trading system.

Problem this solves
-------------------
The bot reads market data from the FUTURES market (Rithmic/Databento: GC, MGC,
GCZ4, GC.n.0, ...) but trades the CFD market on MT5 (XAUUSD, GOLD, XAUUSD.i, ...).
Those are different instruments with different (slightly different) prices.

Two rules implemented here:
  1. RECOGNISE names — every common gold symbol is normalised to a canonical
     market id, whatever suffix/prefix the vendor or broker uses.
  2. NEVER mix prices — analysis runs entirely on the DATA feed (relative
     movement: returns, z-scores, ratios, ATR%); the CFD price is only used at
     execution time (orders, SL/TP), scaled from the futures data via
     percentage offsets.

Usage
-----
    from markets import normalize_symbol, resolve_market
    normalize_symbol("GCZ4")      -> "GC"
    normalize_symbol("GC.n.0")    -> "GC"
    normalize_symbol("XAUUSD.i")  -> "XAUUSD"
    normalize_symbol("GOLDUSD")   -> "XAUUSD"
    resolve_market("GC", "data")  -> (MarketProfile GC, notes)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Market profiles
# --------------------------------------------------------------------------- #


@dataclass
class MarketProfile:
    """One tradeable/quotable gold market."""
    id: str                       # canonical id ("GC", "MGC", "XAUUSD")
    label: str                    # human label
    kind: str                     # "futures" | "cfd"
    venue: str                    # where it trades
    aliases: set                  # lowercase aliases (with vendor suffixes)
    contract_size: float          # oz per contract/lot
    tick_size: float              # min price increment (in price units)
    tick_value: float             # $ per tick per contract/lot


MARKETS: Dict[str, MarketProfile] = {
    "GC": MarketProfile(
        id="GC", label="CME Gold Futures (100 oz)", kind="futures",
        venue="CME (Rithmic / Databento)",
        aliases={"gc", "gcf", "gc.f", "comex gold", "gold futures",
                 "gc futures"},
        contract_size=100.0, tick_size=0.1, tick_value=10.0),

    "MGC": MarketProfile(
        id="MGC", label="CME Micro Gold Futures (10 oz)", kind="futures",
        venue="CME (Rithmic / Databento)",
        aliases={"mgc", "mgcf", "micro gold", "micro gold futures"},
        contract_size=10.0, tick_size=0.1, tick_value=1.0),

    "XAUUSD": MarketProfile(
        id="XAUUSD", label="Spot Gold / Gold CFD (XAUUSD)", kind="cfd",
        venue="MetaTrader 5 (broker)",
        aliases={"xauusd", "xau", "gold", "goldusd", "xau usd",
                 "spot gold", "gold cfd"},
        contract_size=100.0, tick_size=0.01, tick_value=1.0),
}

# futures root month code, e.g. GCZ4, MGCZ24
_FUTURES_MONTH = re.compile(r"^([A-Za-z]{2,4})([FGHJKMNQUVXZ])(\d{1,2})$")
# vendor suffixes: ".n.0", ".i", ".m", ".r", "-USD", "=F", "_m"
_SUFFIX = re.compile(r"[.\-_=+].*$")


def list_markets() -> List[MarketProfile]:
    return list(MARKETS.values())


def normalize_symbol(name: str) -> str:
    """Normalise any gold symbol string to a canonical market id.

    Handles futures month codes, continuous-contract suffixes, broker CFD
    suffixes and separators. Returns the canonical id if recognised, else the
    cleaned upper-case token (so callers can report "unknown").
    """
    if name is None:
        return ""
    s = str(name).strip()
    if not s:
        return ""
    s = s.replace("/", "").replace(" ", "")
    s = _SUFFIX.sub("", s)                 # XAUUSD.i / GC.n.0 / GC=F / GOLD-USD
    m = _FUTURES_MONTH.match(s)            # GCZ4 -> GC, MGCZ24 -> MGC
    if m and len(m.group(1)) >= 2:
        s = m.group(1)
    up = s.upper()
    for pid, prof in MARKETS.items():
        if up == pid or up.lower() in prof.aliases:
            return pid
    return up


def resolve_market(name: str, role: str) -> Tuple[MarketProfile, List[str]]:
    """Resolve `name` to a MarketProfile, with helpful notes.

    role is "data" (feed side) or "trade" (MT5 side); it only influences the
    advisory notes, never the matching.
    """
    pid = normalize_symbol(name)
    if pid not in MARKETS:
        known = ", ".join(sorted(MARKETS))
        raise ValueError(
            f"Unrecognised market '{name}'. Recognised: {known}. "
            f"If your feed uses an unusual name, pick the closest one manually "
            f"(setup_markets.py).")
    prof = MARKETS[pid]
    notes: List[str] = []
    if role == "data" and prof.kind == "cfd":
        notes.append(
            f"'{pid}' is a CFD/spot market — order-book L2/L3 for gold usually "
            "comes from futures (GC/MGC); make sure this is really your feed.")
    if role == "trade" and prof.kind == "futures":
        notes.append(
            f"'{pid}' is a futures market — if your MT5 account trades gold "
            "CFDs, the usual trade market is XAUUSD.")
    return prof, notes


def cross_market_ok(data_market: str, trade_market: str) -> bool:
    """Whether a data/trade pairing is a sensible gold pair.

    Always True for any two gold markets (analysis is price-agnostic); exists
    so the wizard has an explicit sanity-check hook.
    """
    d = normalize_symbol(data_market)
    t = normalize_symbol(trade_market)
    return d in MARKETS and t in MARKETS


# --------------------------------------------------------------------------- #
# Price-agnostic movement helpers
# --------------------------------------------------------------------------- #

def price_pct_change(series) -> float:
    """Percent change of the last value vs the first of `series`."""
    import numpy as np
    a = np.asarray(series, dtype=float)
    if len(a) < 2 or a[0] == 0:
        return 0.0
    return float(a[-1] / a[0] - 1.0) * 100.0


def atr_pct(atr: float, price: float) -> float:
    """ATR as a fraction of price (price-agnostic volatility)."""
    return (atr / price) if price else 0.0


def scale_distance(atr_pct: float, price: float, multiplier: float) -> float:
    """Convert a relative stop distance to absolute units at `price`.

    stop_distance = multiplier * atr_pct * price
    This is how SL/TP computed from FUTURES ATR are re-anchored to the CFD
    price — the movement is identical, only the price level differs.
    """
    return multiplier * atr_pct * price
