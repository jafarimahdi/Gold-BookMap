"""tools/factory2_hands.py — ROOM E, the HANDS, in SHADOW (steps 11-14 of the map).

Built `2026-11-18` on the owner's instruction: *"check the old factory and build now the whole Room E
for our new factory 2 … after Room E, put it after Room B — when all 3 gates are confirmed and
passed,
the position will be executed."*

WHAT THIS ROOM IS
-----------------
Room B renders a verdict behind a doorman. This room turns an ALLOWED verdict into a **paper position**:
11 ORDER (a plan) · 12 OPEN (a fill) · 13 MANAGE (a stop that moves, a TP that does NOT) · 14
CLOSE (a realised number). It never touches a broker.

THE LAW THIS ROOM BUILDS UNDER (every line here traces to one)
--------------------------------------------------------------
* **N1 / law 82** — the old factory is never edited; here it is READ and *imported*. The three
hard
  parts (brackets, fills, sizing) are the bridge's OWN functions, not my re-typing of them:
  `position_manager.compute_structural_stops` (:379) · `paper_entry_simulator.simulate_entry` (:83)
  · `step4_mt5_execution.MT5Executor.compute_lot_size` (:421). A second implementation would have
  been a second chance to be wrong.
* **Law #66 (the owner's)** — "the fix is a lawn, not a threshold": the exit ladder is a short list of
  NAMED rules, borrowed from `position_manager.manage` (`SESSION_FLATTEN` :1184, `NEWS_FLATTEN` :1363,
  `FLIP_EXIT` :1380, `DIVERGENCE_EXIT` :1434, `TIME_STOP` :1455, `MOMENTUM_EXIT` :1473). This
  shadow room implements only the two a recorded minute can prove (`TIME_STOP`,
  `HARD_STOP`-from-the-fill) and names every other one `NOT-MEASURED` rather than pretending.
* **TP = FROZEN (Law #1).** `compute_structural_stops` sets TP once. Nothing in this file may move it —
  including the break-even step, which moves the STOP only. The old factory's own hatch
  (`position_manager.py:1710-1721`: "from that moment TP may extend") is the thing the Book says must be
  re-graded on fresh tapes the day the hands exist; until the owner re-grades it, THIS room stays
  closed.
* **Law 6 (no diary, no ticket)** — a plan is refused with `DIARY` when no pen was handed in,
exactly
  like the doorman's; and **law 4** — refusals are rendered too, so the ledger counts "no" as loudly as "yes".
* **A clock you can hand in.** `open_shadow`/`manage_shadow` take `now_ts` like `run_cycle` does
  (`factory2_gates.py:338`) - a run must be reproducible, and a test must not wait three hours.
* **Law 52** — every gate in the test file was mutated on purpose before shipping (see
  `test_factory2_hands.py`: disarm the frozen-TP rule, the idempotency key, the drift
  revalidation, or the hall lever, and a test flinches).
* **The hall lever.** `HALL_ARMED` is False and there is **no code path to turn it on** in this file:
  `place_real_order()` exists only to refuse, and this module never imports `MetaTrader5`/`step4`
  as a
  sender. The mark convention is the factory's own (`REPLAY/` for v8f): hands are graded on marked tape
  first, `SHADOW/` always, `LIVE` never from here.

WHAT IS DELIBERATELY NOT HERE
-----------------------------
no order send, no broker id, no MT5 import, no partial close (the old factory has one; a shadow
fill has one size), no chandelier/wall trails (they need tick data the sea does not carry), no lot
beyond what the borrowed sizing function returns, and no signal of any kind to any process outside
this repo.
"""
from __future__ import annotations

import copy
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (str(ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

HANDS_VERSION = "factory2.hands.2026-11-18.1"


def _iso(ts: float) -> str:
    """A UTC stamp from the clock you are GIVEN (the tape), never from the wall clock.

    14b: a replayed trade used to be stamped with the machine's date - a 2026-11-13 close
    printed `2026-11-24T17:41:58` in the owner's log. Anything sorting by that stamp grades
    the wrong day (law 105). Pass `now_ts` from the cycle; omit it and you get the wall clock,
    which is what a LIVE run wants.
    """
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat(timespec="seconds")

# ---------------------------------------------------------------- the lever --
HALL_ARMED = False                     # NOTHING in this file may set it. Read the docstring.
SHADOW_PREFIX = "SHADOW/"              # a shadow ticket can never look like a broker ticket
SHADOW_MARK_RE = re.compile(r"^(REPLAY|SHADOW)/")
EXIT_RULES_BORROWED = ("SESSION_FLATTEN", "NEWS_FLATTEN", "FLIP_EXIT",
                       "DIVERGENCE_EXIT", "TIME_STOP", "MOMENTUM_EXIT")
RULES_THIS_SHADOW_RUNS = ("TIME_STOP", "HARD_STOP")


class HandsRefused(Exception):
    """Raised instead of touching a broker. The name is the finding."""


class _Snap:
    """The smallest object `compute_structural_stops` can read honestly.

    That function takes its zones with `getattr(snapshot, ..., None) or []` for every pool
    (`order_blocks`, `footprint`, `volume_profile`, `htf_poc`, `vwap`, `regime`, `news`,
    `order_flow`), and it re-scales futures levels by `price / snapshot.price`. Giving it ONLY a
    price therefore means scale 1.0 and "no structure", which is exactly what a sea cycle is today —
    and the function says so in its own notes (`SL ATR fallback`), which we carry into the plan
    instead of quietly inventing a level.
    """

    __slots__ = ("price",)

    def __init__(self, price: float) -> None:
        self.price = float(price)


def push_dir(card: str) -> int:
    """+1 / -1 / 0 from the G3 card's own `PUSH=` word.

    WHY PARSE THE CARD: `committee_evaluate` publishes `g3` as the RENDERED card
    (`committee.py:472`), and `dir` never leaves the gate. In this house the card IS the wire
    (Book: "a field invented on someone else's dict is dead code", law 68) — so we read the word the
    engine wrote rather than fabricate a field it does not have.
    """
    m = re.search(r"PUSH=(UP|DOWN|NONE)", str(card or ""))
    if not m:
        return 0
    return {"UP": 1, "DOWN": -1, "NONE": 0}[m.group(1)]


def _borrow():
    """Import the organs. A failure is reported, never swallowed (law 78: silence is not a zero)."""
    try:
        import position_manager as pm           # noqa: WPS433 - the bridge's geometry
        import paper_entry_simulator as pes     # noqa: WPS433 - analysis-only fills
        import step4_mt5_execution as ex        # noqa: WPS433 - lot sizing
        import config                            # noqa: WPS433
        return {"pm": pm, "pes": pes, "ex": ex, "config": config, "error": ""}
    except Exception as exc:                    # fail CLOSED: no geometry, no position
        return {"pm": None, "pes": None, "ex": None, "config": None,
                "error": f"{type(exc).__name__}: {exc}"}


# ------------------------------------------------------------------- 11 ORDER --
def plan_from_fire(cyc: Dict[str, Any], out: Dict[str, Any],
                   pen: Optional[Callable[[Dict[str, Any]], Any]] = None) -> Dict[str, Any]:
    """Step 11. The doorman's YES becomes a PLAN_ONLY paper plan, or a NAMED refusal.

    Returns {"plan": dict|None, "refused": str, "stamps": [...], "notes": [...]}.
    Never raises: a plan that cannot be built must still be WRITEABLE to the book.
    """
    stamps: List[str] = [f"hands:{HANDS_VERSION}"]
    final = out.get("final") or {}
    if not final.get("allow"):
        fails = final.get("fails") or ["NO-VERDICT"]
        return {"plan": None, "refused": f"HANDS-NO-VERDICT:{'|'.join(str(f) for f in fails)}",
                "stamps": stamps + ["refused:door"], "notes": []}
    if pen is None or not getattr(pen, "callable_ok", callable(pen)):
        # law 6, applied to this room too: no diary, no ticket.
        return {"plan": None, "refused": "DIARY",
                "stamps": stamps + ["refused:no-pen"], "notes": []}

    org = _borrow()
    if org["error"]:
        return {"plan": None, "refused": "HANDS-BLIND:bridge-import",
                "stamps": stamps + ["blind"], "notes": [org["error"]]}
    pm, config = org["pm"], org["config"]

    d = push_dir(out.get("g3") or "")
    if d == 0:
        return {"plan": None, "refused": "HANDS-NO-DIRECTION",
                "stamps": stamps + ["blind"], "notes": ["no PUSH= word on the G3 card"]}
    side = "BUY" if d > 0 else "SELL"

    price = float(cyc.get("price") or 0.0)
    atr = float(cyc.get("atr") or 0.0)
    if price <= 0 or atr <= 0:
        return {"plan": None, "refused": "HANDS-NO-PRICE", "stamps": stamps, "notes": []}
    rung = cyc.get("rung_price")
    try:
        sl, tp, notes = pm.compute_structural_stops(
            "BUY" if d > 0 else "SELL", price, atr, _Snap(price))
    except Exception as exc:
        return {"plan": None, "refused": "HANDS-BLIND:geometry",
                "stamps": stamps, "notes": [f"{type(exc).__name__}: {exc}"]}

    lots: Optional[float] = None
    try:
        lots = org["ex"].MT5Executor.compute_lot_size(
            object.__new__(org["ex"].MT5Executor), _equity(cyc), abs(price - sl))
        sizing_note = "lots from the bridge's own sizing"
    except Exception as exc:
        sizing_note = f"sizing BLIND ({type(exc).__name__})"
        lots = None

    plan = {
        "shot": "GO", "side": side, "execution_status": "PLAN_ONLY",
        "entry": price, "target": float(tp), "stop": float(sl),
        "atr": atr, "spread": float((cyc.get("prefire") or {}).get("spread") or 0.0),
        "reference_price": price,
        "valid_until": time.time() + float(
            getattr(config, "SHOOT_PLAN_TTL_SECONDS", 180.0) or 180.0),
        "order_type": "MARKET", "entry_style": "AGGRESSIVE_MARKET",
        "entry_options": {"AGGRESSIVE_MARKET": {"eligible": True,
                                                "trigger_reason": str(out.get("g3") or "")[:120]}},
        "rung": str((out.get("final") or {}).get("ticket") or ""),
        "rung_price": float(rung) if rung else None,
        "symbol": str(cyc.get("symbol") or "?"),
        "cycle_id": int(cyc.get("cycle_id") or 0),
        "volume_lots": lots,
        "origin": SHADOW_PREFIX + str(cyc.get("symbol") or "?"),
    }
    stamps += ["geometry:bridge", "fill:paper_simulator", sizing_note]
    if not str(plan["symbol"]).startswith(("REPLAY/", "SHADOW/")):
        plan["symbol"] = SHADOW_PREFIX + str(plan["symbol"])
        stamps.append("symbol:forced-shadow")
    return {"plan": plan, "refused": "", "stamps": stamps,
            "notes": list(notes) + ([] if notes else ["no structure on this cycle: ATR fallback"])}


def _equity(cyc: Dict[str, Any]) -> float:
    """Equity comes from the caller's cycle; there is NO default and no guessing (law 51b)."""
    eq = float((cyc.get("risk") or {}).get("equity") or 0.0)
    if eq <= 0:
        raise RuntimeError("no equity on the cycle")
    return eq


# ------------------------------------------------------------------ 12 OPEN --
def stage_order(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Step 12a. Copy, stamp, and hand it to the ONLY fill model we trust. Never mutates the plan."""
    if not isinstance(plan, dict):
        raise HandsRefused("HANDS-NO-PLAN")
    if plan.get("execution_status") != "PLAN_ONLY":
        raise HandsRefused("HANDS-NOT-ANALYSIS-ONLY")
    staged = copy.deepcopy(plan)
    staged["staged_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    staged["hands_version"] = HANDS_VERSION
    return staged


def open_shadow(staged: Dict[str, Any], price: float,
                config: Any = None, now_ts: Optional[float] = None) -> Dict[str, Any]:
    """Step 12b. Fill through the bridge's analysis-only simulator; a FILL becomes a shadow position."""
    org = _borrow()
    if org["error"] or org["pes"] is None:
        return {"status": "BLIND", "reason": f"no simulator: {org['error']}", "position": None}
    res = org["pes"].simulate_entry(staged, float(price), config=config, now=now_ts) or {}
    status = str(res.get("status") or "?")
    if status != "FILLED":
        return {"status": status, "reason": str(res.get("reason") or ""), "position": None}
    filled = res.get("filled_plan") or staged
    pos = {
        "ticket": f"{SHADOW_PREFIX}{staged['symbol']}:{staged['cycle_id']}",
        "side": staged["side"], "entry": float(filled.get("entry") or staged["entry"]),
        "sl": float(staged["stop"]), "tp": float(staged["target"]),          # TP FROZEN: set once, here.
        "tp_at_open": float(staged["target"]),       # 14b: what FROZEN means, measured at open
        "sl_at_open": float(staged["stop"]),          # so break-even is visible from ONE record
        "volume": float(staged.get("volume_lots") or 0.0),
        "opened_ts": float(now_ts) if now_ts else time.time(),
        "opened_at": _iso(now_ts if now_ts else time.time()),      # 14b: tape clock, not wall
        # the bridge's OWN word for it (paper_entry_simulator._filled_plan:102) - not my invention
        "fill_model": str(filled.get("paper_fill_model") or ""),
        "atr": float(staged.get("atr") or 0.0),
    }
    return {"status": "FILLED", "reason": "paper fill", "position": pos}


# ----------------------------------------------------------------- 13 MANAGE --
def manage_shadow(pos: Dict[str, Any], price: float,
                  config: Any = None, now_ts: Optional[float] = None) -> Dict[str, Any]:
    """Step 13. The lawn: TP never moves; the stop may reach break-even; two exits can fire."""
    if config is None:
        import config as _c           # the bridge's dials, not my own
        config = _c
    out = {"close_rule": "", "sl": float(pos["sl"]), "tp": float(pos["tp"]), "events": []}
    sign = 1.0 if pos["side"] == "BUY" else -1.0
    entry, atr = float(pos["entry"]), float(pos.get("atr") or 0.0)
    r_now = (float(price) - entry) * sign / max(abs(float(pos["sl"]) - entry), 1e-9)
    be_r = float(getattr(config, "PM_BE_TRIGGER_R", 0.75) or 0.75)
    if r_now >= be_r and not pos.get("be_done"):
        out["sl"] = entry + sign * max(0.1 * atr, 0.0)          # break-even, never a target
        out["events"].append(f"BREAK-EVEN at r={r_now:.2f}>={be_r}")
        out["be_done"] = True
    _now = float(now_ts) if now_ts else time.time()
    held_min = (_now - float(pos.get("opened_ts") or _now)) / 60.0
    ts_min = float(getattr(config, "PM_TIME_STOP_MINUTES", 180.0) or 180.0)
    if held_min >= ts_min and r_now < 1.0:
        out["close_rule"] = f"TIME_STOP at {held_min:.0f}min"
    elif (float(price) - float(pos["sl"])) * sign <= 0.0:
        out["close_rule"] = "HARD_STOP"
    elif (float(price) - float(pos["tp"])) * sign >= 0.0:
        out["close_rule"] = "TP_TOUCH (frozen goal post touched, never moved)"
    return out


# ------------------------------------------------------------------- 12c WIRE --
def cycle_hands(shadow: Dict[str, Any], cyc: Dict[str, Any], out: Dict[str, Any],
                pen: Any = None, config: Any = None, now_ts: Optional[float] = None) -> Dict[str, Any]:
    """TODO 12c. The ONE call Room B makes after its verdict. It JUDGES NOTHING.

    `out` already carries the doorman's decision, so this reads `final.allow` rather than
    asking the committee twice (two calls, two ledgers, two answers - the reason the map says
    the hands write INTO the book and never replace it). `shadow` is a plain dict owned by the
    CALLER, not by this module: state lives with the sail, so a replay can never leak a
    position into the next day. Returns one flat, jsonable dict for the diary line. It must
    never raise: a broken paragraph must not kill a heartbeat (law 8) - but a swallowed failure
    must be NAMED, or silence becomes a verdict (law 83).
    """
    res: Dict[str, Any] = {"v": HANDS_VERSION}
    # 14b (law 104): `allowed 7` vs `2 FILLED` looked like 5 declines for a whole session,
    # because a GO that finds a position open is absorbed by MANAGE-first and leaves NO trace.
    # Two echoes fix that: the door's word, and whether the hands were holding. Neither is a
    # judgement - the hands still decide nothing.
    res["gate_allow"] = bool((out.get("final") or {}).get("allow"))
    res["holding"] = bool(shadow.get("pos"))
    pos = shadow.get("pos")
    # the SAME coercion the rest of this file uses (`float(cyc.get("price") or 0.0)`). I first
    # wrote `_f(...)`, a helper that exists only in factory2_gates.py - a NameError on a path no
    # existing test reached. My own 4 new tests caught it; that is what they are for.
    price = float(cyc.get("price") or 0.0)
    ts = float(now_ts) if now_ts is not None else time.time()
    if pos:                                                   # MANAGE first: an open position
        m = manage_shadow(pos, price, config=config, now_ts=ts)
        pos["sl"] = float(m["sl"]); pos["be_done"] = bool(m.get("be_done") or pos.get("be_done"))
        res["event"] = ",".join(m["events"]) or ""
        res["tp"] = float(pos["tp"])                          # echo, never move: Law #1
        res["sl"] = float(pos["sl"])   # 14b: echo the managed stop, so `sl 1 value(s)` is not
                                        # the only thing a diary can ever show (law 103)
        if m["close_rule"]:
            c = close_shadow(pos, price, m["close_rule"], config=config, now_ts=ts)
            res["closed"] = c; shadow["pos"] = None
            res["status"] = "CLOSED"
        else:
            res["status"] = "HELD"; res["ticket"] = pos["ticket"]
        return res
    built = plan_from_fire(cyc, out, pen)                     # step 11, reading the verdict
    if not built.get("plan"):
        res["status"] = "NO-PLAN"; res["refused"] = built.get("refused") or "HANDS-NO-VERDICT"
        res["notes"] = list(built.get("notes") or [])[:3]
        return res
    staged = stage_order(built["plan"])
    opened = open_shadow(staged, price, config=config, now_ts=ts)   # step 12b, the borrowed fill
    res["status"] = str(opened.get("status") or "?")
    if opened.get("position"):
        shadow["pos"] = opened["position"]
        res["ticket"] = opened["position"]["ticket"]
        res["sl"] = float(opened["position"]["sl"]); res["tp"] = float(opened["position"]["tp"])
    else:
        res["reason"] = str(opened.get("reason") or "")[:120]
    return res


# ------------------------------------------------------------------ 14 CLOSE --
def close_shadow(pos: Dict[str, Any], price: float, rule: str, config: Any = None,
                 now_ts: Optional[float] = None) -> Dict[str, Any]:
    """Step 14. One realised number, in the bridge's own units (contract size), never invented here."""
    if config is None:
        import config as _c
        config = _c
    sign = 1.0 if pos["side"] == "BUY" else -1.0
    pts = (float(price) - float(pos["entry"])) * sign
    contract = float(getattr(config, "CONTRACT_SIZE", 0.0) or 0.0)
    lots = float(pos.get("volume") or 0.0)
    tp0 = pos.get("tp_at_open")
    sl0 = pos.get("sl_at_open")
    # 14b: `"tp_frozen": True` used to be a LITERAL - a stamp, not a check (law 96 came for my own
    # code). Now it compares the goal post to what it was when the trade opened. A position opened
    # before this field existed (tp0 is None) is reported frozen rather than lied about: nothing
    # moved it, because nothing had the chance.
    tp_frozen = True if tp0 is None else abs(float(pos["tp"]) - float(tp0)) <= 1e-9
    has_money = contract > 0 and lots > 0
    return {"ticket": pos["ticket"], "rule": rule, "side": pos["side"],
            "entry": float(pos["entry"]), "exit": float(price), "points": round(pts, 4),
            "usd": round(pts * contract * lots, 2) if has_money else None,
            # a null needs a NAME (law 83): "we could not" must never read as "we did not"
            "usd_note": ("" if has_money else "no dollars: sizing gave no lots, so the number "
                                              "would be invented (see the plan's sizing stamp)"),
            "lots": lots or None, "be_done": bool(pos.get("be_done")),
            "sl_at_open": (None if sl0 is None else float(sl0)),
            "sl_moved": (None if sl0 is None else round(float(pos["sl"]) - float(sl0), 4)),
            "tp_at_open": (None if tp0 is None else float(tp0)), "tp_frozen": bool(tp_frozen),
            "at": _iso(float(now_ts) if now_ts else time.time())}


# ------------------------------------------------------------------ the book --
class HandsLedger:
    """Append-only jsonl, the diary's discipline: rows are counted from the FILE, never from memory."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.rows_written = 0

    def write(self, rec: Dict[str, Any]) -> None:
        rec.setdefault("hands", HANDS_VERSION)
        rec.setdefault("at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
        self.rows_written += 1

    def rows(self) -> List[Dict[str, Any]]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    out.append({"UNPARSEABLE": line[:120]})
        return out


# ------------------------------------------------------------------- the wall --
def place_real_order(*a: Any, **k: Any) -> Dict[str, Any]:
    """This function exists so that the ONLY way to send is to change the code and get caught."""
    raise HandsRefused("HALL-OFF: Room E is shadow-only; TP is FROZEN (Law #1); "
                       "no order leaves this file")


def hands_status() -> Dict[str, Any]:
    org = _borrow()
    return {"version": HANDS_VERSION, "hall_armed": HALL_ARMED, "prefix": SHADOW_PREFIX,
            "borrowed": {"geometry": bool(org["pm"]), "fills": bool(org["pes"]), "sizing": bool(org["ex"])},
            "import_error": org["error"], "exit_law_borrowed": list(EXIT_RULES_BORROWED),
            "rules_this_shadow_runs": list(RULES_THIS_SHADOW_RUNS)}


def reset_shadow() -> None:
    """Replay-boundary helper: clears the bridge's OWN paper state (its docstring names this use)."""
    org = _borrow()
    if org["pes"] is not None:
        org["pes"].reset_entry_simulator()
