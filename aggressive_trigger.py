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

A2 — THE NOW BUTTON (wired 2026-10-13, owner's order; see A2_DESIGN_REVIEW)
    The burst lane. Fires only when the three measured eyes ALL agree
    (AND-combination; never one alone — the 62-on-a-rock law):

      EYE 1  CVD print-spy lean: EMA3 of order-flow one-sidedness
             (buying_pressure - selling_pressure) >= A2_LEAN_MIN (0.25),
             held >= 60 s.  The measured 1.2-1.4x-lift eye (flow probe v1.1).
      EYE 2  Panel same-direction signal within +-5 min — the panel's
             DIRECTION (signal_direction), never its confidence.
      EYE 3  Scout door structure — enforced inside plan_shot (route_clear,
             nearest door); not re-implemented here.

    Rails kept from v1: AGGRESSIVE_ENTRY_ENABLED master switch, POWER must be
    directional UP/DOWN, regime/safety gates not-FAIL, spread/freshness/news
    checked downstream by plan_shot's _power_gate.  Caps: A2_BURST_MAX_PER_DAY
    trades/day (persisted counter), A2_BURST_COOLDOWN_MINUTES between fires,
    never widens a stop (Escort only tightens), ALLOW_LIVE_TRADING=0 unchanged.

    Attribution law: every fire logs "A2 burst: FIRE ..." with all three eyes;
    refusals-with-a-lean log "A2 burst: lean ... refused: <why>" (max 1/min).

DESIGN LAW (unchanged)
    Fail-closed and deterministic. On ANY missing evidence or doubt it returns
    not-confirmed with a human-readable reason. It NEVER sends orders; it only
    returns a permission flag + reason for the Shooting entry context.

CONFIG (read from environment first, then config module, then default)
    AGGRESSIVE_ENTRY_ENABLED         default 0     master switch (v1 + A2)
    A2_LEAN_MIN                      default 0.25  eye-1 one-sidedness floor
    A2_LEAN_HOLD_SECONDS             default 60    eye-1 hold time
    A2_PANEL_WINDOW_SECONDS          default 300   eye-2 look-back
    A2_BURST_MAX_PER_DAY             default 10    burst trades per day
    A2_BURST_COOLDOWN_MINUTES        default 5     min minutes between fires
    A2_BURST_SL_PTS                  default 1.8   burst stop distance (pts)
    A2_BURST_TP_PTS                  default 1.8   burst target distance (pts)
    A2_MIN_RR                        default 1.0   burst R:R floor (measured)
    (A2_BURST_SL_PTS / A2_MIN_RR are read by shooting_team's patch, same keys.)
"""
from __future__ import annotations

import json
import math
import os
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, List, Mapping, Optional, Tuple

__all__ = [
    "evaluate_aggressive_trigger", "TRIGGER_KEYS",
    "BurstEyes", "EYES", "a2_eval", "a2_full_compute_ctx", "a2_serve_eval",
    "a2_count_fire", "a2_fire_count", "a2_v1_gate",
]


# --------------------------------------------------------------------------- #
# v1 trigger (unchanged, 22-test-proven) — kept verbatim below.
# --------------------------------------------------------------------------- #

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
    buy = _num(_get(order_flow, "buy_pct", "buy_percent", "buying_pressure"))
    sell = _num(_get(order_flow, "sell_pct", "sell_percent", "selling_pressure"))
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
                           _cfg(config, "MAX_SPREAD_PCT", 0.035)), 0.035)
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


# --------------------------------------------------------------------------- #
# A2 — THE NOW BUTTON (burst lane).  All new code lives below this line.
# --------------------------------------------------------------------------- #

A2_KEYS = (
    "A2_LEAN_MIN", "A2_LEAN_HOLD_SECONDS", "A2_PANEL_WINDOW_SECONDS",
    "A2_BURST_MAX_PER_DAY", "A2_BURST_COOLDOWN_MINUTES",
    "A2_BURST_SL_PTS", "A2_BURST_TP_PTS", "A2_MIN_RR",
)

_SAMPLE_WINDOW_S = 240.0     # keep 4 minutes of one-sidedness samples
_NOTE_MIN_GAP_S = 60.0      # refusal notes rate-limit


def _enabled(config: Any) -> bool:
    raw = os.environ.get("AGGRESSIVE_ENTRY_ENABLED")
    if raw is None:
        raw = getattr(config, "AGGRESSIVE_ENTRY_ENABLED", 0)
    return str(raw).strip() in ("1", "1.0", "true", "True", "TRUE")


def _as_dt(x: Any) -> Optional[datetime]:
    if isinstance(x, datetime):
        return x
    try:
        return datetime.fromisoformat(str(x))
    except (TypeError, ValueError):
        return None


class BurstEyes:
    """The two measured eyes that live on the serve cycle (O(1) per cycle).

    Eye 1 (CVD print-spy lean): EMA3 over per-cycle one-sidedness samples
          (buying_pressure - selling_pressure, in [-1, +1]); a lean exists
          while |EMA3| >= A2_LEAN_MIN held >= A2_LEAN_HOLD_SECONDS.
    Eye 2 (panel direction memory): remembers when the panel last said
          BUY/SELL; agrees if a matching direction occurred within the window.
    """

    def __init__(self) -> None:
        self.samples: Deque[Tuple[datetime, float]] = deque()
        self.panel: Deque[Tuple[datetime, str]] = deque()
        self.last_fire: Optional[datetime] = None
        self._last_note: Optional[datetime] = None

    # -- eye 1 ---------------------------------------------------------------
    def sample(self, now: datetime, one_sidedness: Optional[float]) -> None:
        v = _num(one_sidedness)
        if v is None:
            return
        self.samples.append((now, max(-1.0, min(1.0, v))))
        self._prune(now)

    def lean(self, now: datetime, min_abs: float = 0.25,
             hold_s: float = 60.0) -> Optional[Dict[str, Any]]:
        # adaptive window: must hold enough history for the EMA + hold test at
        # ANY cadence -- serve cycles sample every ~5s, bar-close gates every
        # 300s (fixed 2026-10-13: the old fixed 240s window forgot the previous
        # BAR sample, so a bar-close lean could never exist)
        horizon = max(_SAMPLE_WINDOW_S, 6.0 * (hold_s or 60.0))
        pts = [(t, v) for t, v in self.samples
               if (now - t).total_seconds() <= horizon]
        if not pts:
            return None
        alpha = 2.0 / (3.0 + 1.0)          # EMA3 span
        ema: Optional[float] = None
        series: List[Tuple[datetime, float]] = []
        for t, v in pts:
            ema = v if ema is None else alpha * v + (1.0 - alpha) * ema
            series.append((t, ema))
        if ema is None or abs(ema) < min_abs:
            return None
        held_from = pts[0][0]              # first time the run could have started
        for t, e in series:
            if abs(e) < min_abs or (e * ema <= 0.0):
                held_from = t              # last weak/opposite crossing resets the clock
        held_s = (now - held_from).total_seconds()
        if held_s < hold_s:
            return None
        return {"direction": "UP" if ema > 0 else "DOWN",
                "strength": round(abs(ema), 3), "held_s": round(held_s, 1)}

    # -- eye 2 ---------------------------------------------------------------
    def remember_panel(self, now: datetime, direction: Any) -> None:
        d = str(direction or "").strip().upper()
        if d in ("BUY", "SELL"):
            self.panel.append((now, d))
        self._prune(now)

    def panel_agrees(self, now: datetime, direction: str,
                     window_s: float = 300.0) -> Optional[Dict[str, Any]]:
        want = "BUY" if direction == "UP" else "SELL"
        best: Optional[datetime] = None
        for t, d in self.panel:
            if d == want and (now - t).total_seconds() <= window_s:
                if best is None or t > best:
                    best = t
        if best is None:
            return None
        return {"direction": want, "age_s": round((now - best).total_seconds(), 1)}

    # -- bookkeeping -----------------------------------------------------------
    def _prune(self, now: datetime) -> None:
        horizon = timedelta(seconds=max(_SAMPLE_WINDOW_S, 900.0))
        while self.samples and (now - self.samples[0][0]) > horizon:
            self.samples.popleft()
        while self.panel and (now - self.panel[0][0]) > horizon:
            self.panel.popleft()

    def note_ok(self, now: datetime) -> bool:
        if self._last_note is not None and (now - self._last_note).total_seconds() < _NOTE_MIN_GAP_S:
            return False
        self._last_note = now
        return True


EYES = BurstEyes()      # module singleton: one day, one process, one memory


def _one_sidedness(order_flow: Any) -> Optional[float]:
    """(buying_pressure - selling_pressure) / 100 — tolerant field lookup."""
    buy = _num(_get(order_flow, "buying_pressure", "buy_pct", "buy_percent"))
    sell = _num(_get(order_flow, "selling_pressure", "sell_pct", "sell_percent"))
    if buy is None or sell is None:
        return None
    return (buy - sell) / 100.0


# -- the daily burst counter (persisted; restart-safe; twin-safe: cwd-local) ----

def _counter_path() -> str:
    return os.path.join("data", "a2_burst_count.json")


def a2_fire_count(now: datetime) -> int:
    try:
        with open(_counter_path(), encoding="utf-8") as f:
            rec = json.load(f)
        if rec.get("date") == now.strftime("%Y-%m-%d"):
            return int(rec.get("count") or 0)
    except (OSError, ValueError):
        pass
    return 0


def a2_count_fire(now: datetime) -> int:
    """Persist one more burst fire for today; returns the new count."""
    rec = {"date": now.strftime("%Y-%m-%d"), "count": a2_fire_count(now) + 1}
    try:
        os.makedirs("data", exist_ok=True)
        with open(_counter_path(), "w", encoding="utf-8") as f:
            json.dump(rec, f)
    except OSError:
        pass
    return rec["count"]


# -- the A2 verdict -------------------------------------------------------------

def a2_eval(power: Any, order_flow: Any, panel_direction: Any, now: datetime,
            config: Any) -> Dict[str, Any]:
    """The three eyes + rails. Feeds EYES first (sample + panel memory).

    Returns {fire, direction, reason, lean, panel, count}. Fail-closed:
    anything missing -> fire=False with the honest reason.
    """
    out: Dict[str, Any] = {"fire": False, "direction": None, "reason": "",
                           "lean": None, "panel": None, "count": a2_fire_count(now)}
    if not _enabled(config):
        out["reason"] = "A2 disabled (AGGRESSIVE_ENTRY_ENABLED!=1)"
        return out

    # the eyes ALWAYS eat first (even when POWER is NEITHER or gates fail) --
    # eye memory must not have gaps, and refusals must be explainable
    lean_min = _num(_cfg(config, "A2_LEAN_MIN", 0.25), 0.25)
    hold_s = _num(_cfg(config, "A2_LEAN_HOLD_SECONDS", 60.0), 60.0)
    win_s = _num(_cfg(config, "A2_PANEL_WINDOW_SECONDS", 300.0), 300.0)
    max_day = int(_num(_cfg(config, "A2_BURST_MAX_PER_DAY", 10), 10) or 10)
    cool_s = 60.0 * (_num(_cfg(config, "A2_BURST_COOLDOWN_MINUTES", 5), 5) or 5)
    EYES.sample(now, _one_sidedness(order_flow))
    EYES.remember_panel(now, panel_direction)
    out["lean"] = EYES.lean(now, min_abs=(lean_min or 0.25), hold_s=(hold_s or 60.0))

    p = power if isinstance(power, Mapping) else {}
    direction = str(_get(p, "direction", "decision") or "NEITHER").upper()
    if direction not in ("UP", "DOWN"):
        out["reason"] = f"POWER direction {direction or 'UNKNOWN'} (needs UP/DOWN)"
        return out
    out["direction"] = direction

    # belt-and-braces: explicit FAIL gates refuse (plan_shot re-checks anyway)
    for gate in ("regime_gate", "safety_gate"):
        g = str(_get(p, gate) or "").upper()
        if g and g not in ("PASS",):
            out["reason"] = f"POWER {gate}={g}; burst refused"
            return out

    lean = out["lean"]
    if lean is None:
        out["reason"] = "eye 1 (CVD lean) not confirmed"
        return out
    if lean["direction"] != direction:
        out["reason"] = (f"eye 1 lean is {lean['direction']} while POWER is {direction}")
        return out

    panel = EYES.panel_agrees(now, direction, window_s=(win_s or 300.0))
    out["panel"] = panel
    if panel is None:
        out["reason"] = f"eye 2 (panel {direction}) not seen within {win_s:.0f}s"
        return out

    count = a2_fire_count(now)
    out["count"] = count
    if count >= max_day:
        out["reason"] = f"daily burst cap reached ({count}/{max_day})"
        return out
    if EYES.last_fire is not None and (now - EYES.last_fire).total_seconds() < cool_s:
        out["reason"] = "burst cooldown active"
        return out

    out["fire"] = True
    out["reason"] = (f"A2 burst {direction}: eye1 lean {lean['strength']:.2f} "
                     f"held {lean['held_s']:.0f}s | eye2 panel {panel['direction']} "
                     f"{panel['age_s']:.0f}s ago | eye3 Scout door checked in plan_shot "
                     f"| cap {count + 1}/{max_day}")
    return out


def _eyes_state(v: Dict[str, Any]) -> str:
    lean = v.get("lean")
    panel = v.get("panel")
    e1 = (f"lean {lean['direction']} {lean['strength']:.2f}/{lean['held_s']:.0f}s"
          if lean else "lean none")
    e2 = (f"panel {panel['direction']} {panel['age_s']:.0f}s ago"
          if panel else "panel none")
    return f"eyes: {e1} | {e2} | door: plan_shot"


# -- wiring point 1: the full compute (bar close) --------------------------------

def a2_full_compute_ctx(power: Any, order_flow: Any, direction_local: Any,
                        now: datetime, config: Any,
                        notes: Optional[List[str]] = None) -> Dict[str, Any]:
    """Stamp the Shooting entry context at bar close. Returns {} when disabled
    (non-burst behavior stays byte-identical to the pre-A2 brain)."""
    if not _enabled(config):
        return {}
    v = a2_eval(power, order_flow, direction_local, now, config)
    if not v["fire"]:
        if v.get("lean") and notes is not None and EYES.note_ok(now):
            notes.append(f"A2 burst: {_eyes_state(v)} -> refused: {v['reason']}")
        return {}
    EYES.last_fire = now
    if notes is not None:
        notes.append(f"A2 burst: FIRE {_eyes_state(v)}")
    return {
        "aggressive_trigger_confirmed": True,
        "aggressive_trigger_reason": str(v["reason"]),
        "a2_burst": True,
    }


# -- A2 gate around the LIVE v1 wiring (installed on the owner's machine 2026-10-06) --

def a2_v1_gate(v1: Any, power: Any, order_flow: Any, panel_direction: Any,
               now: datetime, config: Any,
               notes: Optional[List[str]] = None) -> Dict[str, Any]:
    """A2 rails around the live v1 wiring (apply_aggressive_patches.py, 10-06).

    The v1 verdict passes through UNTOUCHED when the lane is disabled. When
    enabled, a confirmed v1 verdict additionally needs the three measured
    eyes (lean + panel direction) and the caps; a fire sets ``a2_burst=True``
    which activates the burst bracket in the (already-installed) shooting
    patch. Counting happens here, at confirm time. Fail-closed everywhere.
    """
    if notes is None:
        notes = []
    passthrough = {"aggressive_trigger_confirmed": False,
                   "aggressive_trigger_reason": ""}
    if not isinstance(v1, Mapping):
        return passthrough
    confirmed = bool(v1.get("confirmed"))
    reason = str(v1.get("reason") or "")
    passthrough.update({"aggressive_trigger_confirmed": confirmed,
                        "aggressive_trigger_reason": reason})
    if not _enabled(config):
        return passthrough

    # the eyes always eat (bar-close cadence: 2 consecutive same-direction
    # bars = a held lean, per the flow-probe measurement)
    EYES.sample(now, _one_sidedness(order_flow))
    EYES.remember_panel(now, panel_direction)
    lean_min = _num(_cfg(config, "A2_LEAN_MIN", 0.25), 0.25)
    hold_s = _num(_cfg(config, "A2_LEAN_HOLD_SECONDS", 60.0), 60.0)
    win_s = _num(_cfg(config, "A2_PANEL_WINDOW_SECONDS", 300.0), 300.0)
    lean = EYES.lean(now, min_abs=(lean_min or 0.25), hold_s=(hold_s or 60.0))

    if not confirmed:
        if lean is not None and EYES.note_ok(now):
            notes.append(f"A2 burst: eyes lean {lean['direction']} "
                         f"{lean['strength']:.2f}/{lean['held_s']:.0f}s "
                         f"-> v1 lane refused: {reason[:120]}")
        return passthrough

    # v1 confirmed -> the A2 rails decide
    direction = str(_get(power, "direction") or "").upper()
    max_day = int(_num(_cfg(config, "A2_BURST_MAX_PER_DAY", 10), 10) or 10)
    cool_s = 60.0 * (_num(_cfg(config, "A2_BURST_COOLDOWN_MINUTES", 5), 5) or 5)
    count = a2_fire_count(now)
    panel = (EYES.panel_agrees(now, direction, window_s=(win_s or 300.0))
             if direction in ("UP", "DOWN") else None)
    why = None
    if lean is None:
        why = "eye 1 (CVD lean) not confirmed"
    elif direction in ("UP", "DOWN") and lean["direction"] != direction:
        why = f"eye 1 lean {lean['direction']} vs POWER {direction}"
    elif panel is None:
        why = f"eye 2 (panel {direction or '?'}) not seen within {win_s:.0f}s"
    elif count >= max_day:
        why = f"daily burst cap reached ({count}/{max_day})"
    elif EYES.last_fire is not None and (now - EYES.last_fire).total_seconds() < cool_s:
        why = "burst cooldown active"
    if why:
        notes.append(f"A2 burst: v1 confirmed -> refused by A2 rails: {why}")
        return {"aggressive_trigger_confirmed": False,
                "aggressive_trigger_reason": f"A2 rails: {why} (v1: {reason[:100]})"}
    EYES.last_fire = now
    n = a2_count_fire(now)
    notes.append(f"A2 burst: FIRE (v1 lane + rails) | eyes: lean "
                 f"{lean['direction']} {lean['strength']:.2f}/{lean['held_s']:.0f}s"
                 f" | panel {panel['direction']} {panel['age_s']:.0f}s ago"
                 f" | burst trade {n}/{max_day}")
    return {"aggressive_trigger_confirmed": True,
            "aggressive_trigger_reason": f"A2 burst FIRE {n}/{max_day}: " + reason[:160],
            "a2_burst": True}


# -- wiring point 2: the serve cycle (between bars — the NOW in NOW button) ------

def a2_serve_eval(served: Any, market_data: Mapping[str, Any], now: datetime,
                  price: float, spread_now: float, config: Any,
                  bid: float = 0.0, ask: float = 0.0,
                  atr: float = 0.0) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Evaluate the burst lane on a serve cycle. Returns (new_shot, notes).

    new_shot is not None only when the trigger fired AND plan_shot produced an
    AGGRESSIVE_MARKET plan (Scout door + route verified inside plan_shot).
    Fail-closed: any error -> ([], None) with the honest note; the cached
    bar-close plan is left untouched.
    """
    if not _enabled(config):
        return None, []
    notes: List[str] = []
    try:
        power = getattr(served, "power_v2", None) or getattr(served, "power", None) or {}
        if not isinstance(power, Mapping):
            power = {}
        v = a2_eval(power, getattr(served, "order_flow", None),
                    getattr(served, "signal_direction", "NEUTRAL"), now, config)
        if not v["fire"]:
            if v.get("lean") and EYES.note_ok(now):
                notes.append(f"A2 burst: {_eyes_state(v)} -> refused: {v['reason']}")
            return None, notes

        session_state = "UNKNOWN"
        try:
            from session import session_context
            session = session_context(now)
            session_state = (
                f"{session.get('session', 'UNKNOWN')} | "
                f"market_open={session.get('market_open', 'UNKNOWN')} | "
                f"budapest_trading={session.get('budapest_trading', 'UNKNOWN')}")
        except Exception:
            pass
        power_context = power.get("context", {})
        if not isinstance(power_context, Mapping):
            power_context = {}
        signal_confidence = getattr(served, "confidence", None)
        if signal_confidence is None:
            signal_confidence = getattr(served, "signal_confidence", None)
        ai_model = getattr(served, "ai_model", None) or "NOT_AVAILABLE_ON_A2_SERVE"
        ctx: Dict[str, Any] = {
            "bid": bid,
            "ask": ask,
            "regime": (getattr(served, "regime", None)
                       or power_context.get("market_regime", "UNKNOWN")),
            "signal_direction": getattr(served, "signal_direction", "UNKNOWN"),
            "signal_confidence": signal_confidence,
            "signal_strength": getattr(served, "signal_strength", None),
            "ai_model": ai_model,
            "session_state": session_state,
            "execution_mode": getattr(config, "EXECUTION_MODE", "UNKNOWN"),
            "trading_enabled": getattr(config, "TRADING_ENABLED", None),
            "allow_live_trading": getattr(config, "ALLOW_LIVE_TRADING", None),
            "limit_order_enabled": getattr(config, "LIMIT_ORDER_ENABLED", None),
            "news_state": getattr(getattr(served, "news", None), "news_state", ""),
            "has_data": market_data.get("has_data", True),
            "last_data_age_seconds": market_data.get("last_data_age_seconds", 0.0),
            "data_quality_ok": market_data.get("data_quality_ok", True),
            "queue_estimate": None,
            "queue_enabled": False,          # burst lane never queues
            "aggressive_trigger_confirmed": True,
            "aggressive_trigger_reason": str(v["reason"]),
            "a2_burst": True,
        }
        from shooting_team import plan_shot
        from signal_team import get_book
        shot = plan_shot(getattr(served, "signal_map", {}) or {}, power, price,
                         atr or 0.0, spread=spread_now, config=config,
                         book=get_book(), entry_context=ctx)
        if shot.get("entry_style") == "AGGRESSIVE_MARKET":
            EYES.last_fire = now
            count = a2_count_fire(now)
            notes.append(f"A2 burst: FIRE {_eyes_state(v)} | burst trade {count}"
                         f" today | entry {shot.get('entry')} stop {shot.get('stop')}"
                         f" target {shot.get('target')}")
            return shot, notes
        why = "; ".join(str(w) for w in (shot.get("why") or []))[:200]
        notes.append(f"A2 burst: {_eyes_state(v)} -> plan_shot refused: "
                     f"{shot.get('shot')} {why}")
        return None, notes
    except Exception as err:  # fail-closed: never break the serve cycle
        return None, [f"A2 burst lane unavailable (fail-closed): "
                      f"{type(err).__name__}: {err}"]
