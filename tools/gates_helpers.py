# -*- coding: utf-8 -*-
"""tools/gates_helpers.py — the six little helpers of THE NEW FACTORY (COMPANY 3).

All-new files (owner inversion order 2026-11-13); imports nothing heavy: stdlib only,
so the meadow tests run anywhere. Each helper answers ONE question the committee
cannot answer from its own input (see HOLY_BOOK ENTRY 5 field table + ENTRY 7):

  rung_state     toll bookkeeping (closes beyond / hold minutes / volume beyond),
                 ACCEPTED history, yesterday's value-area capture (va_prior day-2)
  find_gaps      FVG wick-to-wick ledger (LEARNER rung) from a bar list
  Avwap          anchored-vwap accumulator (LEARNER rung, volume-fed)
  uob_check      unfinished-business magnet star at the birth bar of a level
  session_note   session_anchor context voice (weight 0, card-only)
  oi_read        daily OI stamp from tools/oi_loader's file; "" until born

Data decides, no fiat: every approximation stamps a `note` or `mode` word into the
diary line; nothing here invents a value it does not have.
"""
from __future__ import annotations

import json
import math
import os
import time
from typing import Any, Dict, List, Optional

STATE_VERSION = "gates.helpers.2026-11-13.1"

# The fortress-anchor family, verbatim from the Book (ENTRY 5 §U / ENTRY 3 R1):
# pure comparison over the memory-map kinds the scout already tracks.
ANCHOR_KINDS = frozenset({
    "poc_day", "htf_poc", "vwap", "value_edge", "cme_settlement",
    "onh", "onl", "ibh", "ibl",
})

# supplier kind -> committee ladder vocabulary (ENTRY 5 §U kind map)
KIND_MAP = {
    "poc_day": "naked_poc", "htf_poc": "naked_poc", "vwap": "vwap_bands",
    "prev_day_high": "pdh", "prev_day_low": "pdl",
    "cme_settlement": "settlement", "today_open": "day_open",
    "value_edge": "va_dev",
}


def map_kind(kind: str) -> str:
    return KIND_MAP.get(str(kind or ""), str(kind or "rung"))


def _f(x: Any, d: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else d
    except (TypeError, ValueError):
        return d


class RungState:
    """The toll-keeper. Owns ONE small json file beside the diary (never the
    boat's files). Cross-restart truth stays in the diary per ENTRY 6 (D)/(b);
    this file only remembers what the committee needs from cycle to cycle."""

    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.data: Dict[str, Any] = {"version": STATE_VERSION, "day": "",
                                     "rungs": {}, "prev": {}, "prev_z": None}
        self.load()

    # ------------------------------------------------------------- persistence
    def load(self) -> None:
        if not self.path or not os.path.exists(self.path):
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                d = json.load(fh)
            if isinstance(d, dict):
                d.setdefault("version", STATE_VERSION)
                self.data = d
        except Exception:
            # a corrupt state file is NAMED, then reset (law 8: one line, carry on)
            self.data["load_error"] = "state file unreadable - reset, diary keeps truth"

    def save(self) -> None:
        if not self.path:
            return
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self.data, fh, sort_keys=True)
            os.replace(tmp, self.path)
        except OSError:
            pass

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d")

    # ------------------------------------------------------------------ day roll
    def roll_day(self, va_high: float, va_low: float) -> None:
        """At the end of a day we remember its value-area: tomorrow's va_prior."""
        day = self._today()
        if self.data.get("day") and self.data["day"] != day:
            va = self.data.get("va_today") or {}
            if va.get("high") and va.get("low"):
                self.data["va_prior"] = dict(va)
            self.data["rungs"] = {}          # today-only freshness law
        self.data["day"] = day
        if va_high and va_low:
            self.data.setdefault("va_today", {})
            self.data["va_today"] = {"high": float(va_high), "low": float(va_low)}

    def prior_va(self) -> Dict[str, float]:
        return dict(self.data.get("va_prior") or {})

    # ------------------------------------------------------------------ toll ring
    def observe(self, price: float, ts: float, volume: Optional[float]) -> None:
        """Track, per remembered rung price, how long price keeps closing beyond it."""
        rungs = self.data.setdefault("rungs", {})
        for key in list(rungs.keys()):
            r = rungs[key]
            rp = _f(key)
            if rp <= 0:
                continue
            above = price > rp
            same = "up" if above else "down"
            prev_above = r.get("side")            # read BEFORE the side is rewritten
            r["side"] = same                      # (an earlier draft wrote it too early
            beyond = (prev_above == same) if prev_above in ("up", "down") else False
            if beyond:
                r["closes"] = int(r.get("closes", 0)) + 1
                if r.get("since") is None:
                    r["since"] = ts - _f(r.get("step_s", 180.0))
                vols = r.setdefault("vols", [])
                if volume is not None:
                    vols.append(float(volume))
                    del vols[:-12]
            else:
                # crossed the OTHER way from a prior beyond-side? stamp a snap-back
                if prev_above in ("up", "down") and r.get("closes", 0) >= 1:
                    r["snapback"] = 1
                r["closes"] = 1
                r["since"] = ts
            r["last"] = ts
            r["step_s"] = _f(ts - _f(r.get("prev_ts", ts), ts), 180.0)
            r["prev_ts"] = ts
        prices = self.data.setdefault("prices", [])
        prices.append(round(float(price), 2))
        del prices[:-400]
        # a rung nobody touched for an hour and that never paid is forgotten:
        # the toll book is a pocket card, not a growing ledger (footprint law)
        rungs = self.data.get("rungs") or {}
        for key in [k for k, v in rungs.items()
                    if _f(v.get("closes", 0)) == 0 and not v.get("accepted")
                    and _f(v.get("last"), ts) < ts - 3600.0]:
            rungs.pop(key, None)

    def watch(self, rung_price: float) -> None:
        key = f"{_f(rung_price):.1f}"
        self.data.setdefault("rungs", {}).setdefault(
            key, {"closes": 0, "side": None, "since": None})

    def toll_for(self, rung_price: float, now_ts: float) -> Dict[str, Any]:
        key = f"{_f(rung_price):.1f}"
        r = (self.data.get("rungs") or {}).get(key) or {}
        closes = int(r.get("closes", 0))
        since = _f(r.get("since"), now_ts)
        hold_min = max(0.0, (now_ts - since) / 60.0) if r.get("closes", 0) else 0.0
        vols = r.get("vols") or []
        building = False
        if len(vols) >= 4:
            half = len(vols) // 2
            building = (sum(vols[half:]) / max(1, len(vols) - half)) > \
                       (sum(vols[:half]) / max(1, half))
        return {"closes_beyond": closes, "holds_minutes": hold_min,
                "volume_building": building, "snapback": 1 if r.get("snapback") else 0}

    def clear_snap(self, rung_price: float) -> None:
        key = f"{_f(rung_price):.1f}"
        r = (self.data.get("rungs") or {}).get(key)
        if r:
            r["snapback"] = 0          # a stamp lives for one verdict

    # ----------------------------------------------------- ACCEPTED (history side)
    def accepted_for(self, rung_price: float) -> int:
        key = f"{_f(rung_price):.1f}"
        r = (self.data.get("rungs") or {}).get(key) or {}
        return 1 if int(r.get("accepted", 0)) or int(r.get("closes", 0)) >= 2 else 0

    def stamp_accepted(self, rung_price: float) -> None:
        key = f"{_f(rung_price):.1f}"
        self.data.setdefault("rungs", {}).setdefault(key, {})["accepted"] = 1

    # --------------------------------------------------------------- prev card
    def set_prev(self, card: Dict[str, Any]) -> None:
        self.data["prev"] = dict(card or {})

    def prev(self) -> Optional[Dict[str, Any]]:
        p = self.data.get("prev")
        return p if p else None

    def set_prev_z(self, z: Optional[float]) -> None:
        cur = self.data.get("prev_z")
        self.data["prev_z"] = z
        if z is None or cur is None:
            return 0
        d = float(z) - float(cur)
        return 1 if d > 0.15 else (-1 if d < -0.15 else 0)


# ---------------------------------------------------------------- other helpers
def find_gaps(bars: List[Dict[str, Any]], atr: float,
              max_open: int = 6) -> List[Dict[str, Any]]:
    """FVG wick-to-wick (R10): 3 bars, gap if bar1.high < bar3.low (bull) or
    bar1.low > bar3.high (bear); floor 1.0xATR; a body CLOSE back inside retires it."""
    out: List[Dict[str, Any]] = []
    atr = max(_f(atr), 1e-9)
    seq = [b for b in (bars or []) if isinstance(b, dict)]
    for i in range(2, len(seq)):
        b1, b3 = seq[i - 2], seq[i]
        h1, l1 = _f(b1.get("h")), _f(b1.get("l"))
        h3, l3 = _f(b3.get("h")), _f(b3.get("l"))
        if min(h1, l1) <= 0 or min(h3, l3) <= 0:
            continue
        if l3 - h1 >= max(1.0 * atr, 0.01):
            out.append({"price": (h1 + l3) / 2.0, "kind": "fvg", "name": "fvg_up",
                        "top": l3, "bottom": h1})
        elif l1 - h3 >= max(1.0 * atr, 0.01):
            out.append({"price": (l1 + h3) / 2.0, "kind": "fvg", "name": "fvg_dn",
                        "top": l1, "bottom": h3})
    # retire on a body CLOSE back inside the gap (v1 rule: the last bar's close)
    live: List[Dict[str, Any]] = []
    last_c = _f(seq[-1].get("c")) if seq else 0.0
    for g in out:
        if last_c and g["bottom"] < last_c < g["top"]:
            continue                      # closed back through -> the gap is filled
        live.append(g)
    return live[-max_open:]


class Avwap:
    """AVWAP accumulator (R11 learner): (PV,V) sums from the chosen anchor.
    Volume-fed only - no volume, no vote (abstains cleanly)."""

    def __init__(self):
        self.pv = 0.0
        self.v = 0.0

    def feed(self, price: float, volume: Optional[float]) -> None:
        v = _f(volume, 0.0)
        if v <= 0:
            return
        self.pv += _f(price) * v
        self.v += v

    def price(self) -> Optional[float]:
        return (self.pv / self.v) if self.v > 0 else None

    def to_dict(self) -> Dict[str, float]:
        return {"pv": self.pv, "v": self.v}

    def from_dict(self, d: Dict[str, Any]) -> None:
        self.pv = _f((d or {}).get("pv"))
        self.v = _f((d or {}).get("v"))


def uob_check(bars: List[Dict[str, Any]], level_price: float,
              touch_tol_pts: float = 0.5) -> int:
    """The magnet star (owner catch): the bar that BIRTHED this line printed
    ~zero volume at its tails -> the town never finished business there."""
    lp = _f(level_price)
    if lp <= 0:
        return 0
    for b in bars or []:
        if not isinstance(b, dict):
            continue
        lo, hi, v = _f(b.get("l")), _f(b.get("h")), _f(b.get("v"), -1.0)
        if lo <= 0:
            continue
        touched = abs(lo - lp) <= touch_tol_pts or abs(hi - lp) <= touch_tol_pts
        if touched and v == 0.0:
            return 1
    return 0


def session_note(session_state: Optional[Dict[str, Any]], price: float,
                 atr: float, yard_atr: float = 0.75) -> str:
    """session_anchor: the context voice, weight 0, card text only."""
    st = session_state or {}
    for key, name in (("prev_high", "PDH"), ("prev_low", "PDL"),
                      ("prev_settlement", "CME-SETTLE"), ("open", "DAY-OPEN"),
                      ("onh", "ONH"), ("onl", "ONL")):
        v = _f(st.get(key))
        if v > 0 and abs(price - v) <= yard_atr * max(_f(atr, 1.0), 1e-9):
            return f"ON_ANCHOR={name}|{abs(price - v):.2f}"
    return ""


def oi_read(path: Optional[str]) -> str:
    """Daily OI stamp written by the (future) tools/oi_loader.py. Absent = BLIND."""
    if not path or not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            d = json.load(fh)
        return str(d.get("oi_day") or "")
    except Exception:
        return ""
