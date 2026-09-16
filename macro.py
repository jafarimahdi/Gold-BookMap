"""
macro.py
========
FREE macro data for the AI brain (no API key, no card).

Gold is driven by the US dollar, real interest rates and market fear. This
module fetches the three "macro drivers" from Yahoo Finance (free) and feeds
them into Step 2, which then hands them to Gemini:

    - DXY      (US Dollar Index)   -> strong dollar usually = weaker gold
    - ^TNX     (US 10-year yield)  -> rising yields usually = weaker gold
    - ^VIX     (fear index)        -> high fear usually = stronger gold (safe haven)
    - GC=F     (gold futures)      -> used to compute real correlations

Design goals (same as news.py):
    - FREE and keyless.
    - Cached (memory + disk), re-fetched at most every MACRO_CACHE_MINUTES.
    - Safe: any failure -> macro stays empty; the bot keeps working.

Wire-up: `main.py` calls `enrich_macro(data)` after Step 1, which fills
`data["macro"]`. Step 2's MacroAnalyzer then computes the DXY / yield / VIX
levels AND their correlation to gold, and Step 3 includes all of it in the
Gemini prompt automatically.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

import config

logger = logging.getLogger(__name__)

# Yahoo Finance symbols (all free, no key)
SYMBOLS = {
    "gold": "GC=F",       # gold futures — tracks XAUUSD ~1:1
    "usd": "DX-Y.NYB",    # US Dollar Index (DXY)
    "yield": "^TNX",      # US 10-year Treasury yield
    "vix": "^VIX",        # CBOE volatility (fear) index
}

_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 "
                   "Safari/537.36"),
    "Accept": "application/json",
}

_MEMO: Dict[str, Any] = {}


# --------------------------------------------------------------------------- #
# Caching (memory + disk)
# --------------------------------------------------------------------------- #
def _cache_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "macro_cache.json"


def _fresh_cache() -> Optional[Dict[str, Any]]:
    """Cached macro dict if fresh, else None ({} is a valid fresh result)."""
    if not config.MACRO_ENABLED:
        return None
    mem = _MEMO.get("macro")
    mem_at = _MEMO.get("fetched_at")
    if mem is not None and mem_at is not None and \
            time.time() - mem_at < config.MACRO_CACHE_MINUTES * 60:
        return mem
    try:
        path = _cache_path()
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
            fetched = float(state.get("fetched_at", 0) or 0)
            if "macro" in state and time.time() - fetched < \
                    config.MACRO_CACHE_MINUTES * 60:
                m = state.get("macro") or {}
                _MEMO["macro"] = m
                _MEMO["fetched_at"] = fetched
                return m
    except (json.JSONDecodeError, OSError, ValueError):
        pass
    return None


def _save_cache(macro: Dict[str, Any]) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"fetched_at": time.time(),
                                    "macro": macro}), encoding="utf-8")
    except OSError:
        pass
    _MEMO["macro"] = macro
    _MEMO["fetched_at"] = time.time()


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def _fetch_series(symbol: str) -> List[float]:
    """Return the 5-day / 15-minute close series for one Yahoo symbol."""
    if requests is None:
        return []
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + requests.utils.quote(symbol) + "?range=5d&interval=15m")
    resp = requests.get(url, headers=_HEADERS, timeout=config.MACRO_TIMEOUT)
    resp.raise_for_status()
    result = resp.json()["chart"]["result"][0]
    closes = result["indicators"]["quote"][0]["close"]
    return [float(c) for c in closes if c is not None]


def fetch_macro() -> Dict[str, Any]:
    """Fetch DXY / 10Y / VIX (+ gold series), aligned to a common length."""
    if not config.MACRO_ENABLED:
        return {}
    cached = _fresh_cache()
    if cached is not None:
        return cached
    if requests is None:
        logger.warning("macro: 'requests' not installed -> no macro data")
        return {}

    series: Dict[str, List[float]] = {}
    for key, sym in SYMBOLS.items():
        try:
            s = _fetch_series(sym)
        except Exception as exc:
            logger.info("macro: %s (%s) fetch failed: %s", key, sym, exc)
            s = []
        if s:
            series[key] = s

    if len(series) < 3:            # need at least DXY + yield + VIX
        logger.info("macro: not enough symbols fetched; macro stays empty.")
        _save_cache({})
        return {}

    # align all series to the same length so correlations are meaningful
    n = min(len(v) for v in series.values())
    for k in series:
        series[k] = series[k][-n:]

    out = {
        "usd_index": round(float(series["usd"][-1]), 3),
        "us_10y_yield": round(float(series["yield"][-1]), 3),
        "vix_index": round(float(series["vix"][-1]), 2),
        "inflation_expectation": 0.0,   # not available for free; left 0
        "gold_series": series.get("gold", []),
        "usd_series": series["usd"],
        "yield_series": series["yield"],
        "vix_series": series["vix"],
    }
    _save_cache(out)
    logger.info("macro: DXY %.2f, 10Y %.3f%%, VIX %.2f (%d aligned points).",
                out["usd_index"], out["us_10y_yield"], out["vix_index"], n)
    return out


def enrich_macro(data: Dict[str, Any]) -> Dict[str, Any]:
    """Fill data['macro'] with live macro levels + series (if not provided)."""
    if not config.MACRO_ENABLED:
        return data
    try:
        macro = data.setdefault("macro", {})
        if not (macro.get("usd_index") or macro.get("us_10y_yield")
                or macro.get("vix_index")):
            m = fetch_macro()
            if m:
                macro.update(m)
    except Exception as exc:
        logger.warning("macro: enrich failed (%s); continuing.", exc)
    return data


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print("Fetching macro data (free, cached) ...\n")
    m = fetch_macro()
    if m:
        print(f"  DXY : {m['usd_index']}")
        print(f"  10Y : {m['us_10y_yield']}%")
        print(f"  VIX : {m['vix_index']}")
        print(f"  points: {len(m['usd_series'])}")
    else:
        print("  (no macro data — network issue?)")
