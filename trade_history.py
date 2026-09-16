"""
trade_history.py — the robot's memory of its own trades (v4.4)

WHY THIS EXISTS
---------------
Before v4.4 the robot forgot a trade the moment it closed. Two features
need that memory:

1. LOSS MEMORY (entry side, step 4): after a losing SELL, the next SELL
   signal must clear a HIGHER bar for a while (default +5 points for 30
   minutes). "Don't poke the same fire twice" — if the market just proved
   our sell-side read wrong, demand more evidence before selling again.

2. TRADE ATTRIBUTION / AI-TCA (review side): every open and close is
   recorded with the context (AI confidence, signal strength, exit rule).
   tools/backtest.py and future AI post-mortems read this file to answer
   "which entry conditions actually make money for THIS robot?"

Also included: a small TCA (trade-cost-analysis) log — the price we
INTENDED to trade at vs the price we actually GOT. Persistent slippage
means the broker/deep-book is costing us money we cannot see otherwise.

STORAGE (all under data/, safe to delete — the robot rebuilds them)
  data/trade_memory.json   rolling list of the last ~200 closed trades
                           + open trades while they run + day PnL tracker
  data/tca_log.csv         one row per fill: intended vs actual price

THREAD MODEL: called from the main loop thread only (like the rest of
the robot), so a simple load/save is enough. Every write is atomic
(write temp file, then rename) so a crash never corrupts the file.
"""

from __future__ import annotations

import csv
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("gold_bot.trade_history")

# Roll the closed-trade list at this length (FIFO — oldest dropped).
MAX_CLOSED_TRADES = 200


# --------------------------------------------------------------------------- #
# Location helpers (kept in one place so tests can point them at /tmp)
# --------------------------------------------------------------------------- #

def _base_dir() -> str:
    # Same layout rule as the rest of the robot: file lives in the
    # package dir, data goes to ./data next to it.
    here = os.path.dirname(os.path.abspath(__file__))
    data_dir = os.path.join(here, "data")
    try:
        os.makedirs(data_dir, exist_ok=True)
    except OSError:
        data_dir = here          # fall back to package dir if read-only
    return data_dir


def memory_path() -> str:
    return os.path.join(_base_dir(), "trade_memory.json")


def tca_path() -> str:
    return os.path.join(_base_dir(), "tca_log.csv")


# --------------------------------------------------------------------------- #
# Low-level store
# --------------------------------------------------------------------------- #

def _now_ts() -> float:
    return time.time()


def _utc_iso(ts: Optional[float] = None) -> str:
    dt = datetime.fromtimestamp(ts if ts is not None else time.time(),
                                tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _load() -> Dict[str, Any]:
    """Load the memory file. ANY problem -> fresh empty store (safe default)."""
    try:
        with open(memory_path(), "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        data.setdefault("open", {})        # ticket -> entry record
        data.setdefault("closed", [])      # list of close records
        data.setdefault("day", {})         # {utc_date: {pnl_pts_usd: x, trades: n}}
        return data
    except FileNotFoundError:
        return {"open": {}, "closed": [], "day": {}}
    except Exception as exc:
        logger.warning("trade_memory unreadable (%s) — starting fresh", exc)
        return {"open": {}, "closed": [], "day": {}}


def _save(data: Dict[str, Any]) -> None:
    """Atomic write: temp file + rename. Never raises into the trading loop."""
    try:
        path = memory_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
        os.replace(tmp, path)
    except Exception as exc:
        logger.warning("trade_memory write failed: %s", exc)


def _today_key(ts: Optional[float] = None) -> str:
    dt = datetime.fromtimestamp(ts if ts is not None else time.time(),
                                tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


# --------------------------------------------------------------------------- #
# 1) ENTRY / CLOSE RECORDS  (trade attribution)
# --------------------------------------------------------------------------- #

def record_entry(ticket: Any, side: str, price: float, sl: float, tp: float,
                 volume: float, ai_confidence: float = 0.0,
                 signal_strength: float = 0.0,
                 signal_score: float = 0.0) -> None:
    """Remember the conditions under which we OPENED a trade."""
    if ticket is None:
        return
    data = _load()
    data["open"][str(ticket)] = {
        "ticket": str(ticket),
        "side": str(side or "").upper(),
        "opened_utc": _utc_iso(),
        "opened_ts": _now_ts(),
        "price": float(price or 0.0),
        "sl": float(sl or 0.0),
        "tp": float(tp or 0.0),
        "volume": float(volume or 0.0),
        "ai_confidence": float(ai_confidence or 0.0),
        "signal_strength": float(signal_strength or 0.0),
        "signal_score": float(signal_score or 0.0),
    }
    _save(data)


def record_close(ticket: Any, side: str, price: float, reason: str,
                 gain_pts: float = 0.0, volume: float = 0.0,
                 estimated: bool = False,
                 contract_size: float = 100.0,
                 pnl_usd: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Remember a CLOSED trade (any reason: rule exit, SL/TP hit, gone).

    Joins the entry record (AI confidence etc.) onto the close record —
    that join is what makes later "which entries work?" analysis possible.
    Approximate USD PnL = gain_pts * volume_lots * contract_size unless an
    exact `pnl_usd` (from MT5 deal history) is provided.
    Returns the record for callers that want to log it.
    """
    if ticket is None:
        return None
    data = _load()
    entry_rec = data["open"].pop(str(ticket), None)
    ts = _now_ts()
    vol_eff = float(volume or (entry_rec or {}).get("volume", 0.0))
    if pnl_usd is None:
        pnl_usd = float(gain_pts or 0.0) * vol_eff * contract_size
    rec = {
        "ticket": str(ticket),
        "side": str(side or "").upper(),
        "closed_utc": _utc_iso(ts),
        "closed_ts": ts,
        "close_price": float(price or 0.0),
        "exit_reason": str(reason or "UNKNOWN")[:40],
        "gain_pts": round(float(gain_pts or 0.0), 2),
        "volume": vol_eff,
        "pnl_usd_est": round(float(pnl_usd), 2),
        "estimated": bool(estimated),
        "ai_confidence": float((entry_rec or {}).get("ai_confidence", 0.0)),
        "signal_strength": float((entry_rec or {}).get("signal_strength", 0.0)),
        "signal_score": float((entry_rec or {}).get("signal_score", 0.0)),
        "age_minutes": round(
            (ts - float((entry_rec or {}).get("opened_ts", ts))) / 60.0, 1),
    }
    data["closed"].append(rec)
    if len(data["closed"]) > MAX_CLOSED_TRADES:
        data["closed"] = data["closed"][-MAX_CLOSED_TRADES:]
    # day tracker (UTC day of the CLOSE, approximate equity impact)
    key = _today_key(ts)
    day = data["day"].setdefault(key, {"pnl_usd_est": 0.0, "closes": 0})
    day["pnl_usd_est"] = round(day["pnl_usd_est"] + rec["pnl_usd_est"], 2)
    day["closes"] += 1
    data["day"] = {k: v for k, v in list(data["day"].items())[-10:]}
    _save(data)
    return rec


def forget_open(ticket: Any) -> None:
    """Drop an open record without journaling a close (e.g. manual close)."""
    if ticket is None:
        return
    data = _load()
    if data["open"].pop(str(ticket), None) is not None:
        _save(data)


def closed_trades(limit: int = 50) -> List[Dict[str, Any]]:
    """Most recent closes, newest last."""
    return _load()["closed"][-limit:]


# --------------------------------------------------------------------------- #
# 2) LOSS MEMORY  (entry gate, step 4)
# --------------------------------------------------------------------------- #

def recent_loss(side: str, within_minutes: float = 30.0,
                now_ts: Optional[float] = None) -> Dict[str, Any]:
    """Did we close a LOSING trade in this DIRECTION recently?

    Returns {"hit": bool, "minutes_ago": float, "reason": str}.
    A "loss" = pnl_usd_est < 0 on the same side (BUY/SELL).
    """
    side = str(side or "").upper()
    now_ts = now_ts if now_ts is not None else time.time()
    horizon = float(within_minutes or 0.0) * 60.0
    out = {"hit": False, "minutes_ago": 0.0, "reason": ""}
    if horizon <= 0 or side not in ("BUY", "SELL"):
        return out
    for rec in reversed(_load()["closed"]):
        if rec.get("side") != side:
            continue
        age = now_ts - float(rec.get("closed_ts", 0.0))
        if age < 0 or age > horizon:
            continue
        if float(rec.get("pnl_usd_est", 0.0)) < 0:
            out = {"hit": True, "minutes_ago": round(age / 60.0, 1),
                   "reason": str(rec.get("exit_reason", ""))}
            break
    return out


def day_pnl_pct(equity: float) -> float:
    """Approximate realized PnL for the current UTC day, as % of equity.

    Built from our own close records (works even when MT5 history is
    unavailable; SL/TP hits are captured by the position manager's
    gone-position detector, so they land here too).
    """
    if equity <= 0:
        return 0.0
    day = _load()["day"].get(_today_key(), {})
    return float(day.get("pnl_usd_est", 0.0)) / equity * 100.0


# --------------------------------------------------------------------------- #
# 3) TCA LOG  (intended vs actual fill price)
# --------------------------------------------------------------------------- #

def record_tca(kind: str, ticket: Any, intended: float, actual: float,
               volume: float = 0.0, note: str = "") -> None:
    """Append one fill-quality row to data/tca_log.csv.

    kind: "ENTRY" or "EXIT". Slippage is in price points, signed so that
    POSITIVE always means "worse than intended" (paid more / got less).
    """
    try:
        intended = float(intended or 0.0)
        actual = float(actual or 0.0)
        if intended <= 0 or actual <= 0:
            return
        slip = round(actual - intended, 2)
        new_file = not os.path.exists(tca_path())
        with open(tca_path(), "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new_file:
                w.writerow(["utc_time", "kind", "ticket", "intended",
                            "actual", "slippage_pts", "volume", "note"])
            w.writerow([_utc_iso(), kind, ticket, intended, actual,
                        slip, volume, note[:40]])
    except Exception as exc:
        logger.warning("tca log write failed: %s", exc)


# --------------------------------------------------------------------------- #
# Self-test:  python trade_history.py
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    # Smoke test in a THROWAWAY directory — never touches real data/.
    import tempfile
    tmp = tempfile.mkdtemp(prefix="trade_history_test_")
    _orig = _base_dir
    _base_dir = lambda: tmp                      # noqa: E731 (test stub)
    try:
        record_entry(1001, "SELL", 4292.5, 4317.7, 4217.7, 0.01,
                     ai_confidence=82.0, signal_strength=-34.0)
        record_tca("ENTRY", 1001, 4292.5, 4292.7, 0.01, "selftest")
        rec = record_close(1001, "SELL", 4295.0, "SL_HIT",
                           gain_pts=-2.5, volume=0.01)
        assert rec and rec["pnl_usd_est"] == -2.5, rec
        mem = recent_loss("SELL", 30)
        assert mem["hit"] and mem["minutes_ago"] < 1, mem
        assert not recent_loss("BUY", 30)["hit"]
        assert day_pnl_pct(1000.0) == -0.25, day_pnl_pct(1000.0)
        print("SELFTEST OK —", len(closed_trades()), "close recorded")
        print("memory:", memory_path())
        print("tca   :", tca_path())
    finally:
        _base_dir = _orig                       # noqa: F811 (restore)

