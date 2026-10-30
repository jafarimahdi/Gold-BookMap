"""
session_levels.py — THE JUDGE `session_levels`  (SCOUT team, "history" group)
==============================================================================

THE GAP THIS FILLS
  The scout knew today's POC, today's VWAP and today's value area. It had no idea
  where YESTERDAY ended or where official COMEX Gold benchmarks sit.

  Yesterday's high. Yesterday's low. Official CME 13:30 ET Settlement. 18:00 ET
  Globex Open. Overnight High/Low (ONH/ONL, 18:00-08:20 ET). COMEX Initial Balance
  High/Low (IBH/IBL, 08:20-09:20 ET).
  Every bank desk, clearing house, and institutional execution algo marks these
  exact anchors (CME Rule Filings 25-467 & SER-9637).

HOW IT WORKS - deliberately cheap & restart-safe
  Rolls sessions at 18:00 US Eastern (CME Globex session open) instead of script
  boot or UTC midnight, captures the 13:29-13:30 ET settlement window average, and
  persists ONH/ONL and IBH/IBL across restarts in `session_levels.json`.
  It NAMES PRICES, so it is a door-maker and belongs to the scout.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo as _ZoneInfo
except Exception:  # pragma: no cover
    _ZoneInfo = None  # type: ignore[assignment]

__all__ = ["update", "levels_for_map", "STATE_FILE"]

STATE_FILE = "session_levels.json"


def _f(x, d=0.0):
    try:
        v = float(x)
        return v if v == v else d
    except Exception:
        return d


def _nth_sunday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (6 - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _to_us_eastern(dt_utc: datetime) -> datetime:
    """Convert timezone-aware UTC datetime to US/Eastern (tzdata-free fallback safe)."""
    if dt_utc.tzinfo is None:
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    else:
        dt_utc = dt_utc.astimezone(timezone.utc)
    if _ZoneInfo is not None:
        try:
            return dt_utc.astimezone(_ZoneInfo("America/New_York"))
        except Exception:
            pass
    # Pure-Python US DST rule (since 2007):
    # Starts 2nd Sunday in March at 07:00 UTC (02:00 EST -> 03:00 EDT)
    # Ends   1st Sunday in November at 06:00 UTC (02:00 EDT -> 01:00 EST)
    yr = dt_utc.year
    dst_start_utc = datetime.combine(_nth_sunday(yr, 3, 2), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=7)
    dst_end_utc = datetime.combine(_nth_sunday(yr, 11, 1), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=6)
    offset_hours = -4 if (dst_start_utc <= dt_utc < dst_end_utc) else -5
    return dt_utc.astimezone(timezone(timedelta(hours=offset_hours)))


def _cme_trade_day(et: datetime) -> str:
    """CME Globex session rolls at 18:00 ET (18:00 ET Sunday starts Monday's trade date)."""
    trade_d = et.date() + timedelta(days=1) if et.hour >= 18 else et.date()
    return trade_d.isoformat()


def _path(data_dir) -> Path:
    try:
        repo_data = (Path(__file__).resolve().parent / "data").resolve()
        if data_dir in (None, "", "data", Path("data")) or Path(data_dir).resolve() == repo_data:
            import config as _cfg
            cfg_dir = getattr(_cfg, "DATA_DIR", None)
            if cfg_dir:
                return Path(cfg_dir) / STATE_FILE
        return Path(data_dir) / STATE_FILE
    except Exception:
        return Path(STATE_FILE)


def _load(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def update(price: float, data_dir, now: Optional[datetime] = None,
           volume: float = 1.0) -> Dict[str, Any]:
    """Fold this price into the active CME Globex session (18:00 ET roll)."""
    price = _f(price)
    if price <= 0:
        return {}
    now_utc = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    et = _to_us_eastern(now_utc)
    day = _cme_trade_day(et)
    hm = et.hour * 60 + et.minute
    # Overnight Globex + Asia + London: 18:00 ET (1080) .. 08:19 ET (499)
    in_overnight = (hm >= 18 * 60) or (hm < (8 * 60 + 20))
    # COMEX Initial Balance: 08:20 ET (500) .. 09:19 ET (559)
    in_ib = (8 * 60 + 20) <= hm < (9 * 60 + 20)
    # Official CME Gold Settlement window: 13:29:00 .. 13:30:59 ET
    in_settle_win = hm in (13 * 60 + 29, 13 * 60 + 30)

    p = _path(data_dir)
    st = _load(p)

    if st.get("day") != day:
        if st.get("day") and st.get("high"):
            prev_settle = _f(st.get("cme_settlement")) or _f(st.get("last"))
            st = {
                "prev_day": st.get("day"),
                "prev_high": st.get("high"),
                "prev_low": st.get("low"),
                "prev_close": st.get("last"),
                "prev_settlement": prev_settle,
                "day": day,
                "open": price,
                "high": price,
                "low": price,
                "last": price,
                "onh": price if in_overnight else 0.0,
                "onl": price if in_overnight else 0.0,
                "on_complete": not in_overnight,
                "ibh": price if in_ib else 0.0,
                "ibl": price if in_ib else 0.0,
                "ib_complete": hm >= (9 * 60 + 20) and et.hour < 18,
                "cme_settlement": price if in_settle_win else 0.0,
                "_settle_pv": (price * max(_f(volume, 1.0), 0.01)) if in_settle_win else 0.0,
                "_settle_v": max(_f(volume, 1.0), 0.01) if in_settle_win else 0.0,
            }
        else:
            st = {
                "day": day,
                "open": price,
                "high": price,
                "low": price,
                "last": price,
                "onh": price if in_overnight else 0.0,
                "onl": price if in_overnight else 0.0,
                "on_complete": not in_overnight,
                "ibh": price if in_ib else 0.0,
                "ibl": price if in_ib else 0.0,
                "ib_complete": hm >= (9 * 60 + 20) and et.hour < 18,
                "cme_settlement": price if in_settle_win else 0.0,
                "_settle_pv": (price * max(_f(volume, 1.0), 0.01)) if in_settle_win else 0.0,
                "_settle_v": max(_f(volume, 1.0), 0.01) if in_settle_win else 0.0,
            }
    else:
        st["high"] = max(_f(st.get("high"), price), price)
        st["low"] = min(_f(st.get("low"), price) or price, price)
        st["last"] = price
        if in_overnight:
            st["onh"] = max(_f(st.get("onh"), price), price)
            st["onl"] = min(_f(st.get("onl"), price) or price, price)
        elif hm >= (8 * 60 + 20) and et.hour < 18:
            st["on_complete"] = True
        if in_ib:
            st["ibh"] = max(_f(st.get("ibh"), price), price)
            st["ibl"] = min(_f(st.get("ibl"), price) or price, price)
        elif hm >= (9 * 60 + 20) and et.hour < 18 and _f(st.get("ibh")) > 0:
            st["ib_complete"] = True
        if in_settle_win:
            w = max(_f(volume, 1.0), 0.01)
            pv = _f(st.get("_settle_pv"), 0.0) + price * w
            vv = _f(st.get("_settle_v"), 0.0) + w
            st["_settle_pv"] = pv
            st["_settle_v"] = vv
            st["cme_settlement"] = round(pv / vv, 2)

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st), encoding="utf-8", newline="\n")
    except Exception:
        pass
    return st


def levels_for_map(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """-> [{"price":..., "kind":...}] for the scout's notebook."""
    st = state or {}
    out: List[Dict[str, Any]] = []
    for key, kind in (
        ("prev_high", "prev_day_high"),
        ("prev_low", "prev_day_low"),
        ("prev_close", "prev_day_close"),
        ("prev_settlement", "cme_settlement"),
        ("open", "today_open"),
    ):
        v = _f(st.get(key))
        if v > 0:
            out.append({"price": v, "kind": kind})
    if st.get("on_complete") or (_f(st.get("onh")) > 0 and _f(st.get("onh")) > _f(st.get("onl"))):
        for key, kind in (("onh", "onh"), ("onl", "onl")):
            v = _f(st.get(key))
            if v > 0:
                out.append({"price": v, "kind": kind})
    if st.get("ib_complete"):
        for key, kind in (("ibh", "ibh"), ("ibl", "ibl")):
            v = _f(st.get(key))
            if v > 0:
                out.append({"price": v, "kind": kind})
    return out
