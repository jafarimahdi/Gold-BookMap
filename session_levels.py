"""
session_levels.py — THE JUDGE `session_levels`  (SCOUT team, "history" group)
==============================================================================

THE GAP THIS FILLS
  The scout knew today's POC, today's VWAP and today's value area. It had no idea
  where YESTERDAY ended.

  Yesterday's high. Yesterday's low. Yesterday's close. Today's open.
  These are the most-watched prices in all of trading. Every bank desk, every algo
  and every chart on every screen has them marked. When gold reaches yesterday's
  high, something happens - not by magic, but because thousands of people are
  looking at the same line and have orders sitting there.

  A boy who knows where the crowd stood this morning, but has forgotten the shop
  was closed at this exact price yesterday, is missing the obvious.

HOW IT WORKS - deliberately cheap
  Every cycle it updates today's open / high / low in a small JSON file. When the
  date rolls over, today's numbers become yesterday's and today starts fresh. No
  history replay, no extra feed, no startup cost. The file survives restarts, which
  matters because the scout's notebook does not.

  It NAMES PRICES, so it is a door-maker and belongs to the scout - exactly like
  poc_day and vwap.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = ["update", "levels_for_map", "STATE_FILE"]

STATE_FILE = "session_levels.json"


def _f(x, d=0.0):
    try:
        v = float(x)
        return v if v == v else d
    except Exception:
        return d


def _path(data_dir) -> Path:
    try:
        return Path(data_dir) / STATE_FILE
    except Exception:
        return Path(STATE_FILE)


def _load(p: Path) -> Dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def update(price: float, data_dir, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Fold this price into today's session, rolling the day over when needed."""
    price = _f(price)
    if price <= 0:
        return {}
    now = now or datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    p = _path(data_dir)
    st = _load(p)

    if st.get("day") != day:
        # the day turned: today becomes yesterday, and today starts at this price
        if st.get("day") and st.get("high"):
            st = {"prev_day": st.get("day"), "prev_high": st.get("high"),
                  "prev_low": st.get("low"), "prev_close": st.get("last"),
                  "day": day, "open": price, "high": price, "low": price,
                  "last": price}
        else:
            st = {"day": day, "open": price, "high": price, "low": price,
                  "last": price}
    else:
        st["high"] = max(_f(st.get("high"), price), price)
        st["low"] = min(_f(st.get("low"), price) or price, price)
        st["last"] = price

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(st), encoding="utf-8")
    except Exception:
        pass
    return st


def levels_for_map(state: Dict[str, Any]) -> List[Dict[str, Any]]:
    """-> [{"price":..., "kind":...}] for the scout's notebook."""
    out = []
    for key, kind in (("prev_high", "prev_day_high"), ("prev_low", "prev_day_low"),
                      ("prev_close", "prev_day_close"), ("open", "today_open")):
        v = _f((state or {}).get(key))
        if v > 0:
            out.append({"price": v, "kind": kind})
    return out
