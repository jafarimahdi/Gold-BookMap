"""
mt5_signal_bridge.py
====================
Bridge between the Python pipeline and MetaTrader 5.

Writes a tiny `key=value` signal file that the MQL5 Expert Advisor
(mt5_ea/GoldTradingEA.mq5) and indicator (mt5_ea/GoldSignalIndicator.mq5)
read from the MT5 "Common Files" folder.

This gives you TWO execution paths, both driven by the same analysis:
  1) Step 4 via the `MetaTrader5` Python SDK (runs on Windows, needs MT5
     terminal + a bot login), or
  2) The MQL5 EA reading this file (attach it to an XAUUSD chart).

The file is deliberately simple (no JSON parsing in MQL5 required):

    id=1700000000
    timestamp=2026-08-19T10:00:00+00:00
    symbol=XAUUSD
    direction=BUY
    confidence=82.5
    strength=61.2
    price=2032.6
    sl=2010.0
    tp=2060.0
    lots=0.12
    news_state=QUIET
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import config

logger = logging.getLogger(__name__)

_FIELDS = ["id", "timestamp", "expires_unix", "symbol", "direction", "confidence", "strength",
           "price", "sl", "tp", "sl_pct", "tp_pct", "lots", "news_state",
           "data_symbol", "trade_symbol"]


def risk_based_lots(snapshot, equity: Optional[float] = None) -> float:
    """Risk-based lot size using ATR stop distance (same math as Step 4)."""
    atr = getattr(getattr(snapshot, "volatility", None), "atr", 0.0) or 0.0
    price = getattr(snapshot, "price", 0.0) or 0.0
    if atr <= 0:
        atr = price * 0.005
    stop_dist = atr * config.STOP_LOSS_ATR_MULT
    equity = equity or config.ACCOUNT_EQUITY
    if stop_dist <= 0 or config.CONTRACT_SIZE <= 0:
        return config.LOT_SIZE
    lots = (equity * config.RISK_PER_TRADE_PCT / 100.0) / \
        (stop_dist * config.CONTRACT_SIZE)
    return round(max(0.01, min(lots, config.MAX_LOT_SIZE)), 2)


def build_signal(snapshot, decision) -> Dict[str, Any]:
    """Build the signal dict from a Step-2 snapshot + Step-3 decision.

    SL/TP are emitted BOTH as absolute values (indicative, anchored to the
    DATA-feed price) and as PERCENTAGE offsets (sl_pct / tp_pct). The MT5 EA
    uses the percentage offsets against its own live CFD price, so the signal
    is correct even though futures and CFD prices differ.
    """
    price = getattr(snapshot, "price", 0.0) or 0.0
    atr = getattr(getattr(snapshot, "volatility", None), "atr", 0.0) or 0.0
    if atr <= 0:
        atr = price * 0.005

    action = getattr(decision, "action", "HOLD")
    direction = action if action in ("BUY", "SELL") else "NEUTRAL"

    # news blackout overrides everything (defence-in-depth with the EA)
    news_state = getattr(getattr(snapshot, "news", None), "news_state", "QUIET")
    if news_state == "BLACKOUT":
        direction = "NEUTRAL"

    # movement as % of price (price-agnostic across futures/CFD)
    atr_pct = (atr / price) if price else 0.0
    sl_pct = config.STOP_LOSS_ATR_MULT * atr_pct * 100.0
    tp_pct = config.TAKE_PROFIT_ATR_MULT * atr_pct * 100.0

    # indicative absolute SL/TP (anchored to the DATA price, for logging only)
    if direction == "BUY":
        sl = price - config.STOP_LOSS_ATR_MULT * atr
        tp = price + config.TAKE_PROFIT_ATR_MULT * atr
    elif direction == "SELL":
        sl = price + config.STOP_LOSS_ATR_MULT * atr
        tp = price - config.TAKE_PROFIT_ATR_MULT * atr
    else:
        sl = tp = 0.0

    lots = risk_based_lots(snapshot) if direction != "NEUTRAL" else 0.0
    signal_ts = getattr(snapshot, "timestamp", datetime.now(timezone.utc))
    if signal_ts.tzinfo is None:
        signal_ts = signal_ts.replace(tzinfo=timezone.utc)
    signal_id = int(signal_ts.timestamp())
    expires_unix = signal_id + max(0, int(getattr(
        config, "SIGNAL_MAX_AGE_SECONDS", 180)))

    return {
        "id": signal_id,
        "timestamp": signal_ts.isoformat(),
        "expires_unix": expires_unix,
        "symbol": getattr(snapshot, "trade_symbol", "") or
                  getattr(snapshot, "symbol", config.MT5_SYMBOL) or config.MT5_SYMBOL,
        "direction": direction,
        "confidence": round(float(getattr(decision, "confidence", 0.0) or 0.0), 2),
        "strength": round(float(getattr(snapshot, "signal_strength", 0.0) or 0.0), 2),
        "price": round(price, 2),
        "sl": round(sl, 2),
        "tp": round(tp, 2),
        "sl_pct": round(sl_pct, 4),
        "tp_pct": round(tp_pct, 4),
        "lots": lots,
        "news_state": news_state,
        "data_symbol": getattr(snapshot, "data_symbol", "") or "",
        "trade_symbol": getattr(snapshot, "trade_symbol", "") or "",
    }


def signal_to_text(sig: Dict[str, Any]) -> str:
    return "\n".join(f"{k}={sig.get(k, '')}" for k in _FIELDS) + "\n"


def signal_path() -> Optional[Path]:
    """Where the signal file should be written."""
    cfg = os.environ.get("MT5_SIGNAL_FILE", config.MT5_SIGNAL_FILE)
    if cfg:
        return Path(cfg)
    return config.DATA_DIR / "mt5_signal.txt"


def write_signal(snapshot, decision) -> Optional[Path]:
    """Write the signal file ATOMICALLY; returns the path, or None on failure.

    ATOMIC means: write to a temp file first, then replace the real file in
    one step. Without this, MT5 can read the file while it is half-written
    (empty or truncated) and report "unreadable". The swap guarantees the
    file is ALWAYS complete when MT5 reads it.
    """
    sig = build_signal(snapshot, decision)
    path = signal_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(signal_to_text(sig), encoding="utf-8")
        os.replace(tmp, path)          # atomic swap on Windows
        logger.info("MT5 signal written -> %s (direction=%s, conf=%.1f)",
                    path, sig["direction"], sig["confidence"])
        return path
    except OSError as exc:
        logger.warning("Could not write MT5 signal file %s: %s", path, exc)
        return None


def parse_signal(text: str) -> Dict[str, Any]:
    """Parse a signal file back into a dict (used by tests / debugging)."""
    out: Dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out
