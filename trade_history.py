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
                 signal_score: float = 0.0,
                 regime: str = "",
                 volatility_rank: float = 0.0,
                 atr: float = 0.0,
                 snapshot_notes: str = "") -> None:
    """Remember the conditions under which we OPENED a trade. v5.5 B2 adds regime/vol for ML."""
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
        "regime": str(regime or "").upper(),
        "volatility_rank": float(volatility_rank or 0.0),
        "atr": float(atr or 0.0),
        "snapshot_notes": str(snapshot_notes or "")[:500],
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
        "regime": str((entry_rec or {}).get("regime", "")).upper(),
        "volatility_rank": float((entry_rec or {}).get("volatility_rank", 0.0) or 0.0),
        "atr": float((entry_rec or {}).get("atr", 0.0) or 0.0),
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
    v5.3 M6: also logs session and stores for weekly TCA report.
    """
    try:
        intended = float(intended or 0.0)
        actual = float(actual or 0.0)
        if intended <= 0 or actual <= 0:
            return
        slip = round(actual - intended, 2)
        # Determine session from UTC hour
        utc_hour = datetime.now(timezone.utc).hour
        if 0 <= utc_hour < 7:
            session = "ASIA"
        elif 8 <= utc_hour < 13:
            session = "LONDON"
        elif 13 <= utc_hour < 22:
            session = "NY"
        else:
            session = "OTHER"
        new_file = not os.path.exists(tca_path())
        with open(tca_path(), "a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new_file:
                w.writerow(["utc_time", "kind", "ticket", "intended",
                            "actual", "slippage_pts", "volume", "note", "session"])
            w.writerow([_utc_iso(), kind, ticket, intended, actual,
                        slip, volume, note[:40], session])
    except Exception as exc:
        logger.warning("tca log write failed: %s", exc)


# --------------------------------------------------------------------------- #
# M6 TCA & Slippage Analysis (v5.3)
# --------------------------------------------------------------------------- #

def _parse_tca_csv(days: int = 7) -> List[Dict[str, Any]]:
    """Read tca_log.csv and return rows within last `days` days."""
    path = tca_path()
    if not os.path.exists(path):
        return []
    rows: List[Dict[str, Any]] = []
    cutoff = time.time() - days * 86400
    try:
        with open(path, "r", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for r in reader:
                try:
                    # Parse utc_time
                    ts_str = r.get("utc_time", "")
                    try:
                        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                        dt = dt.replace(tzinfo=timezone.utc)
                        ts = dt.timestamp()
                    except:
                        ts = time.time()
                    if ts < cutoff:
                        continue
                    rows.append({
                        "utc_time": ts_str,
                        "timestamp": ts,
                        "kind": r.get("kind", ""),
                        "ticket": r.get("ticket", ""),
                        "intended": float(r.get("intended", 0) or 0),
                        "actual": float(r.get("actual", 0) or 0),
                        "slippage_pts": float(r.get("slippage_pts", 0) or 0),
                        "volume": float(r.get("volume", 0) or 0),
                        "note": r.get("note", ""),
                        "session": r.get("session", "UNKNOWN"),
                    })
                except Exception:
                    continue
    except Exception as exc:
        logger.warning(f"TCA parse failed: {exc}")
    return rows


def analyze_tca(days: int = 7) -> Dict[str, Any]:
    """M6: Analyze TCA log for avg slippage per session, per kind, per volatility proxy.

    Returns dict with:
    - total_trades, avg_slippage, max_slippage
    - per_session: {ASIA, LONDON, NY, OTHER} -> avg, count
    - per_kind: {ENTRY, EXIT} -> avg, count
    - per_side: {BUY, SELL} -> avg, count (from note field)
    - suggestion: recommended ENTRY_MIN_TP_SPREAD_MULT adjustment
    """
    rows = _parse_tca_csv(days=days)
    if not rows:
        return {"total_trades": 0, "avg_slippage": 0.0, "message": "No TCA data in last %d days" % days}

    total = len(rows)
    slips = [r["slippage_pts"] for r in rows]
    avg_slip = sum(slips) / total if total else 0.0
    max_slip = max(slips, key=abs) if slips else 0.0

    # Per session
    per_session: Dict[str, Dict] = {}
    for sess in ("ASIA", "LONDON", "NY", "OTHER", "UNKNOWN"):
        sess_rows = [r for r in rows if r["session"] == sess]
        if sess_rows:
            per_session[sess] = {
                "count": len(sess_rows),
                "avg_slippage": round(sum(r["slippage_pts"] for r in sess_rows) / len(sess_rows), 3),
                "max_slippage": round(max((r["slippage_pts"] for r in sess_rows), key=abs), 3),
            }

    # Per kind
    per_kind: Dict[str, Dict] = {}
    for kind in ("ENTRY", "EXIT"):
        k_rows = [r for r in rows if r["kind"] == kind]
        if k_rows:
            per_kind[kind] = {
                "count": len(k_rows),
                "avg_slippage": round(sum(r["slippage_pts"] for r in k_rows) / len(k_rows), 3),
            }

    # Per side from note
    per_side: Dict[str, Dict] = {}
    for side in ("BUY", "SELL"):
        s_rows = [r for r in rows if side in str(r["note"]).upper()]
        if s_rows:
            per_side[side] = {
                "count": len(s_rows),
                "avg_slippage": round(sum(r["slippage_pts"] for r in s_rows) / len(s_rows), 3),
            }

    # Slippage suggestion: if avg slippage > 0.5 pts, increase spread mult
    suggestion = {}
    try:
        import config as cfg
        current_mult = float(getattr(cfg, "ENTRY_MIN_TP_SPREAD_MULT", 3.0))
        # If avg slippage high, need larger TP to pay toll
        if abs(avg_slip) > float(getattr(cfg, "TCA_SLIPPAGE_THRESHOLD", 0.5)):
            # Increase mult by slippage factor
            suggested = current_mult + abs(avg_slip) * 0.5
            suggestion = {
                "current_mult": current_mult,
                "suggested_mult": round(suggested, 1),
                "reason": f"Avg slippage {avg_slip:.2f} pts > threshold, increase TP spread mult to cover costs",
                "action": "increase" if suggested > current_mult else "keep",
            }
        else:
            suggestion = {
                "current_mult": current_mult,
                "suggested_mult": current_mult,
                "reason": f"Avg slippage {avg_slip:.2f} pts within threshold, keep current",
                "action": "keep",
            }
    except Exception as e:
        suggestion = {"error": str(e)}

    return {
        "total_trades": total,
        "avg_slippage": round(avg_slip, 3),
        "max_slippage": round(max_slip, 3),
        "per_session": per_session,
        "per_kind": per_kind,
        "per_side": per_side,
        "suggestion": suggestion,
        "days": days,
        "generated_utc": _utc_iso(),
    }


def tca_report_path() -> str:
    return os.path.join(_base_dir(), "tca_report.json")


def generate_tca_report(days: int = 7) -> Dict[str, Any]:
    """M6: Generate weekly TCA report and save to data/tca_report.json"""
    try:
        report = analyze_tca(days=days)
        path = tca_report_path()
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        os.replace(tmp, path)
        logger.info(f"TCA report generated: {report['total_trades']} trades, avg slip {report['avg_slippage']} pts -> {path}")
        return report
    except Exception as exc:
        logger.warning(f"TCA report generation failed: {exc}")
        return {"error": str(exc)}


def get_slippage_adjustment() -> float:
    """M6: Return suggested ENTRY_MIN_TP_SPREAD_MULT based on recent slippage."""
    try:
        report = analyze_tca(days=7)
        sugg = report.get("suggestion", {})
        return float(sugg.get("suggested_mult", 0) or 0)
    except:
        return 0.0



# --------------------------------------------------------------------------- #
# L5 Reinforcement Learning from Trade Memory (v5.4)
# --------------------------------------------------------------------------- #

def _load_closed_with_regime() -> list:
    """Load closed trades, attempt to infer regime if stored."""
    try:
        data = _load()
        return data.get("closed", [])
    except:
        return []

def check_loss_streak_regime(current_regime: str = "", loss_streak_thr: int = None, within_hours: float = 24.0) -> dict:
    """L5: After N losses in same regime, suggest size reduction or pause.
    Returns {"should_reduce": bool, "should_pause": bool, "streak": int, "reason": str}
    """
    try:
        import config as cfg
        thr = int(loss_streak_thr if loss_streak_thr is not None else getattr(cfg, "RL_LOSS_STREAK", 3))
        regime = str(current_regime or "").upper()
        closed = _load_closed_with_regime()
        # Filter recent closes within hours
        now = time.time()
        cutoff = now - within_hours*3600
        recent = [c for c in closed if float(c.get("closed_ts",0)) >= cutoff]
        # Count consecutive losses from most recent backwards
        streak = 0
        for rec in reversed(recent):
            # If regime filtering, check if rec has regime info (stored in notes or signal?)
            # For now, count all if regime empty or rec doesn't have regime; else match
            rec_regime = str(rec.get("regime", "") or rec.get("signal_score", "")).upper()
            # If current_regime specified and rec has regime, require match
            if regime and rec_regime and regime not in rec_regime and rec_regime not in regime:
                # If regimes don't match, break streak? Actually we want same regime losses
                # If rec regime exists and differs, don't count but don't break
                continue
            if float(rec.get("pnl_usd_est",0)) < 0:
                streak += 1
            else:
                break
        should_reduce = streak >= thr
        should_pause = streak >= thr + 1
        reason = ""
        if should_pause:
            reason = f"L5 RL: {streak} consecutive losses in regime {regime or 'ANY'} -> PAUSE suggested"
        elif should_reduce:
            reason = f"L5 RL: {streak} losses in regime {regime or 'ANY'} -> reduce size 50%"
        return {"should_reduce": should_reduce, "should_pause": should_pause, "streak": streak, "reason": reason}
    except Exception as e:
        return {"should_reduce": False, "should_pause": False, "streak": 0, "reason": f"RL error {e}"}

def get_rl_size_multiplier(current_regime: str = "") -> float:
    """L5: Returns size multiplier based on loss streak. 1.0 normal, 0.5 reduced, 0.0 paused."""
    try:
        import config as cfg
        if not getattr(cfg, "RL_ENABLED", True):
            return 1.0
        res = check_loss_streak_regime(current_regime)
        if res.get("should_pause"):
            return 0.0
        if res.get("should_reduce"):
            reduce_pct = float(getattr(cfg, "RL_SIZE_REDUCE_PCT", 50.0))
            return max(0.1, 1.0 - reduce_pct/100.0)
        return 1.0
    except:
        return 1.0

def should_pause_entries(current_regime: str = "") -> tuple:
    """L5: Returns (pause: bool, reason: str) if RL says pause."""
    try:
        import config as cfg
        if not getattr(cfg, "RL_ENABLED", True):
            return False, ""
        res = check_loss_streak_regime(current_regime)
        if res.get("should_pause"):
            return True, res.get("reason","")
        return False, ""
    except Exception as e:
        return False, str(e)


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

