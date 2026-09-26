"""
escort_team.py — THE ESCORT TEAM ("the friends")
=================================================

The fourth and last team. It sleeps while flat and wakes the moment a position exists,
then runs beside the trade until the target or the stop.

    SIGNAL   (scout)    where are the doors                -> the map
    POWER    (legs)     which door do we reach first       -> direction + strength
    SHOOTING (shooter)  take the shot? target and stop?    -> the plan
    ESCORT   (friends)  is the trade still safe?           -> THIS FILE

THE ONE LAW
    ESCORT MAY ONLY EVER MAKE A TRADE SAFER.
    Tighten the stop, take profit earlier, close, part-close. It may NEVER widen a stop,
    add size, or push a target further away on a losing trade. An escort that can loosen
    protection is not an escort - it is a second gambler.
    One allowed exception, kept from the existing position manager: a target may move
    FURTHER only once the trade is break-even. A locked trade may run.

THE FIVE JOBS - one recommendation each, at most, per cycle
    1. Is my exit still clear?      the target door grew / vanished / a new one appeared
    2. Is the crowd turning?        the earliest warning, before price shows it
    3. Is my push dying?            pressure fading while price still drifts
    4. Where do I move my stop?     the next shelter as price advances
    5. Is a bomb coming?            a high-impact event approaching

    Five voices, never fourteen. Otherwise we have rebuilt the shouting problem, this
    time with an open position and real money in it.

WHY IT GUARDS PAPER TRADES TODAY
    Nothing is allowed to trade until its homework has been marked (Law 7). A real escort
    would therefore sleep forever and never be graded. So when the SHOOTER says GO, a
    PAPER position is opened here - no order, no broker, pure bookkeeping - and the escort
    guards it exactly as it would guard a real one. The tape then marks two things at once:
    was the shooter's plan any good, and did the escort's interventions help or hurt?

    Every paper trade is scored twice: what actually happened, and what WOULD have
    happened if the escort had done nothing. That comparison is the only honest way to
    judge an escort, because every individual intervention always looks sensible.

IT SENDS NOTHING. No orders, no signal file, no MT5. Delete this file and the robot
behaves identically.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

__all__ = ["escort_cycle", "describe", "get_escort", "EscortBook"]

MAX_PAPER = 3            # never guard more than a handful of pretend trades at once
TIME_STOP_MIN = 45.0     # a paper trade that has gone nowhere is closed and recorded


def _f(x, d=0.0):
    try:
        v = float(x)
        return v if v == v else d
    except Exception:
        return d


class PaperTrade:
    """A pretend position. No broker, no money - only bookkeeping and honesty."""

    __slots__ = ("side", "entry", "target", "stop", "opened", "opened_price",
                 "orig_target", "orig_stop", "door_price", "actions", "peak", "trough",
                 "closed", "close_reason", "close_price", "cycles")

    def __init__(self, plan: Dict[str, Any], price: float):
        self.side = plan.get("side")
        self.entry = _f(plan.get("entry"), price)
        self.target = _f(plan.get("target"))
        self.stop = _f(plan.get("stop"))
        self.orig_target = self.target
        self.orig_stop = self.stop
        self.door_price = _f((plan.get("door") or {}).get("price"))
        self.opened = time.time()
        self.opened_price = price
        self.actions: List[Dict[str, Any]] = []
        self.peak = price          # best price seen in our favour
        self.trough = price        # worst price seen against us
        self.closed = False
        self.close_reason = ""
        self.close_price = 0.0
        self.cycles = 0

    @property
    def is_buy(self) -> bool:
        return str(self.side).upper() == "BUY"

    def r_now(self, price: float) -> float:
        risk = abs(self.entry - self.orig_stop)
        if risk <= 1e-9:
            return 0.0
        move = (price - self.entry) if self.is_buy else (self.entry - price)
        return move / risk

    def breakeven(self, price: float) -> bool:
        return self.r_now(price) >= 0.5


class EscortBook:
    """Holds the paper trades and their history."""

    def __init__(self):
        self.open: List[PaperTrade] = []
        self.done: List[Dict[str, Any]] = []

    # -- open on a shooter GO ------------------------------------------------
    def maybe_open(self, plan: Dict[str, Any], price: float) -> Optional[PaperTrade]:
        if not plan or plan.get("shot") != "GO" or not plan.get("side"):
            return None
        if len(self.open) >= MAX_PAPER:
            return None
        # never two paper trades on the same door in the same direction
        for t in self.open:
            if t.side == plan.get("side") and abs(t.door_price -
                                                  _f((plan.get("door") or {}).get("price"))) < 0.01:
                return None
        pt = PaperTrade(plan, price)
        self.open.append(pt)
        return pt


_BOOK: Optional[EscortBook] = None


def get_escort() -> EscortBook:
    global _BOOK
    if _BOOK is None:
        _BOOK = EscortBook()
    return _BOOK


# --------------------------------------------------------------------------- #
#  the five jobs
# --------------------------------------------------------------------------- #
def _job1_exit_clear(t: PaperTrade, smap: Dict[str, Any], price: float) -> Optional[Dict]:
    """Is my exit still clear? Watch the very door the shooter aimed at."""
    doors = (smap or {}).get("above" if t.is_buy else "below") or []
    same = next((d for d in doors if abs(_f(d.get("price")) - t.door_price) < 0.05), None)

    if same is None:
        # the door we aimed at is gone from the map
        return {"job": 1, "judge": "whale_walls", "action": "TARGET_NEARER",
                "reason": "the door we aimed at is no longer on the map - do not wait "
                          "for a fill that may never come",
                "new_target": (price + (t.target - price) * 0.5)}

    # a NEW door has appeared in front of our target
    nearer = [d for d in doors
              if (price < _f(d.get("price")) < t.door_price) if t.is_buy] or \
             [d for d in doors
              if (t.door_price < _f(d.get("price")) < price) if not t.is_buy]
    if nearer:
        best = max(nearer, key=lambda d: _f(d.get("resistance")))
        if _f(best.get("resistance")) >= 0.45:
            return {"job": 1, "judge": "whale_walls", "action": "TARGET_NEARER",
                    "reason": f"a new door appeared at {_f(best.get('price')):.2f} in front "
                              f"of our target (hold {_f(best.get('resistance')):.2f}) - "
                              f"take profit before it, not after",
                    "new_target": _f(best.get("price"))}

    # our door has grown a lot - it will hold harder, so bank earlier
    if _f(same.get("resistance")) >= 0.70 and same.get("trend") == "growing":
        return {"job": 1, "judge": "iceberg", "action": "TARGET_NEARER",
                "reason": "the target door is growing and hardening - it will stop price "
                          "sooner than we hoped",
                "new_target": t.target}
    return None


def _job2_crowd_turning(t: PaperTrade, order_flow: Any, level3: Any,
                        price: float) -> Optional[Dict]:
    """Is the crowd turning against me? The earliest warning we own."""
    micro = _f(getattr(order_flow, "microprice", 0.0))
    mid = _f(getattr(order_flow, "mid_price", 0.0))
    imb = _f(getattr(level3, "order_book_imbalance", 0.0))
    tilt = 0.0
    if micro and mid:
        tilt = (micro - mid) / max(abs(mid), 1e-9) * 10000.0      # in bps
    against = (tilt < -0.4 and t.is_buy) or (tilt > 0.4 and not t.is_buy)
    book_against = (imb < -0.25 and t.is_buy) or (imb > 0.25 and not t.is_buy)
    if against and book_against:
        return {"job": 2, "judge": "microprice", "action": "TIGHTEN",
                "reason": f"microprice tilt {tilt:+.2f}bps and book imbalance {imb:+.2f} "
                          f"have both turned against us - bring the stop closer"}
    return None


def _job3_push_dying(t: PaperTrade, order_flow: Any, footprint: Any,
                     divergence: float, price: float) -> Optional[Dict]:
    """Is my push dying? Pressure fading while price still drifts."""
    div = _f(divergence)
    if abs(div) > 0.3 and ((div < 0 and t.is_buy) or (div > 0 and not t.is_buy)):
        if t.r_now(price) < 0.8:
            return {"job": 3, "judge": "cvd_divergence", "action": "CLOSE",
                    "reason": f"divergence {div:+.2f} against us and the trade is not yet "
                              f"in decent profit - the move is over"}
        return {"job": 3, "judge": "cvd_divergence", "action": "TIGHTEN",
                "reason": f"divergence {div:+.2f} against us but we are in profit - "
                          f"protect it instead of closing"}
    fp = _f(getattr(footprint, "delta_imbalance", 0.0))
    if (fp < -0.35 and t.is_buy) or (fp > 0.35 and not t.is_buy):
        return {"job": 3, "judge": "footprint_delta", "action": "TIGHTEN",
                "reason": f"aggression has flipped against us ({fp:+.2f})"}
    return None


def _job4_move_stop(t: PaperTrade, book, price: float, atr: float,
                    spread: float) -> Optional[Dict]:
    """Where do I move my stop to? Behind the next shelter, never further away."""
    if book is None or t.r_now(price) < 0.5:
        return None                                  # only trail once it is working
    try:
        sh = book.shelter_behind(price, t.is_buy)
    except Exception:
        return None
    if not sh:
        return None
    buf = max(spread * 2.0, 0.15 * atr)
    new_stop = (_f(sh["price"]) - buf) if t.is_buy else (_f(sh["price"]) + buf)
    better = (new_stop > t.stop) if t.is_buy else (new_stop < t.stop)
    if better:
        return {"job": 4, "judge": "supply_demand", "action": "TIGHTEN",
                "reason": f"price advanced - new shelter at {_f(sh['price']):.2f} "
                          f"({_f(sh['size']):.0f} lots), move the stop behind it",
                "new_stop": new_stop}
    return None


def _job5_bomb(t: PaperTrade, news: Any, config: Any) -> Optional[Dict]:
    """Is a bomb coming?"""
    impact = str(getattr(news, "impact_level", "LOW") or "LOW").upper()
    mins = abs(_f(getattr(news, "minutes_to_next_event", 0.0)))
    if impact == "HIGH" and mins <= 15.0:
        return {"job": 5, "judge": "news_sentiment", "action": "CLOSE",
                "reason": f"high-impact event in {mins:.0f} min - flatten before it lands"}
    return None


# --------------------------------------------------------------------------- #
#  the cycle
# --------------------------------------------------------------------------- #
def escort_cycle(shot_plan: Dict[str, Any], signal_map: Dict[str, Any],
                 price: float, atr: float, spread: float = 0.0,
                 order_flow: Any = None, footprint: Any = None, level3: Any = None,
                 news: Any = None, divergence: float = 0.0, book=None,
                 config: Any = None) -> Dict[str, Any]:
    """Open paper trades on a shooter GO, then guard every open one."""
    esc = get_escort()
    price, atr = _f(price), max(_f(atr), 1e-9)
    opened = esc.maybe_open(shot_plan, price)

    reports: List[Dict[str, Any]] = []
    for t in list(esc.open):
        t.cycles += 1
        # track how far it went for and against us
        if t.is_buy:
            t.peak, t.trough = max(t.peak, price), min(t.trough, price)
        else:
            t.peak, t.trough = min(t.peak, price), max(t.trough, price)

        # did it finish on its own?
        hit_tp = (price >= t.target) if t.is_buy else (price <= t.target)
        hit_sl = (price <= t.stop) if t.is_buy else (price >= t.stop)
        if hit_tp or hit_sl:
            _close(esc, t, price, "TARGET" if hit_tp else "STOP")
            continue
        if (time.time() - t.opened) / 60.0 > TIME_STOP_MIN:
            _close(esc, t, price, "TIME")
            continue

        # ---- the five jobs, one voice each ----------------------------------
        acts = [a for a in (
            _job1_exit_clear(t, signal_map, price),
            _job2_crowd_turning(t, order_flow, level3, price),
            _job3_push_dying(t, order_flow, footprint, divergence, price),
            _job4_move_stop(t, book, price, atr, spread),
            _job5_bomb(t, news, config),
        ) if a]

        applied = []
        for a in acts:
            if _apply(t, a, price):
                applied.append(a)
                t.actions.append({"at": round(price, 2), "cycle": t.cycles, **a})
        if any(a["action"] == "CLOSE" for a in applied):
            _close(esc, t, price, "ESCORT")
            continue
        reports.append({"side": t.side, "entry": round(t.entry, 2),
                        "target": round(t.target, 2), "stop": round(t.stop, 2),
                        "r": round(t.r_now(price), 2), "cycles": t.cycles,
                        "actions": applied})

    return {"watching": len(esc.open), "opened_now": bool(opened),
            "reports": reports, "finished": len(esc.done),
            "last_finished": esc.done[-1] if esc.done else None}


def _apply(t: PaperTrade, a: Dict[str, Any], price: float) -> bool:
    """THE ONE LAW is enforced here: only ever safer."""
    if a["action"] == "CLOSE":
        return True
    if "new_stop" in a:
        ns = _f(a["new_stop"])
        safer = (ns > t.stop) if t.is_buy else (ns < t.stop)
        if not safer:
            return False                      # refuse to widen a stop, ever
        t.stop = ns
        return True
    if "new_target" in a:
        nt = _f(a["new_target"])
        nearer = (nt < t.target) if t.is_buy else (nt > t.target)
        if nearer:
            t.target = nt
            return True
        # further away is allowed ONLY once the trade is locked
        if t.breakeven(price):
            t.target = nt
            return True
        return False
    if a["action"] == "TIGHTEN" and "new_stop" not in a:
        # pull the stop halfway to price, never past it
        ns = (t.stop + price) / 2.0
        safer = (ns > t.stop) if t.is_buy else (ns < t.stop)
        if safer:
            t.stop = ns
            return True
    return False


def _close(esc: EscortBook, t: PaperTrade, price: float, reason: str) -> None:
    t.closed, t.close_reason, t.close_price = True, reason, price
    move = (price - t.entry) if t.is_buy else (t.entry - price)
    risk = abs(t.entry - t.orig_stop) or 1e-9
    # what would have happened with NO escort at all: original target and stop
    naive_tp = (t.peak >= t.orig_target) if t.is_buy else (t.peak <= t.orig_target)
    naive_sl = (t.trough <= t.orig_stop) if t.is_buy else (t.trough >= t.orig_stop)
    naive_r = 1.0 * (abs(t.orig_target - t.entry) / risk) if naive_tp else (
        -1.0 if naive_sl else move / risk)
    esc.done.append({
        "side": t.side, "entry": round(t.entry, 2), "exit": round(price, 2),
        "reason": reason, "r": round(move / risk, 2), "r_if_left_alone": round(naive_r, 2),
        "escort_helped": round(move / risk - naive_r, 2),
        "actions": len(t.actions), "cycles": t.cycles,
    })
    try:
        esc.open.remove(t)
    except ValueError:
        pass


def describe(out: Dict[str, Any]) -> str:
    if not out or not out.get("watching"):
        fin = out.get("last_finished") if out else None
        if fin:
            return (f"ESCORT: asleep | last paper trade {fin['side']} closed on "
                    f"{fin['reason']} at {fin['r']:+.2f}R "
                    f"(no escort would have been {fin['r_if_left_alone']:+.2f}R)")
        return "ESCORT: asleep - no position to guard"
    bits = []
    for r in out.get("reports", []):
        acts = ", ".join(f"J{a['job']} {a['action']}" for a in r["actions"]) or "holding"
        bits.append(f"{r['side']} @{r['entry']:.2f} tgt {r['target']:.2f} stop {r['stop']:.2f} "
                    f"({r['r']:+.2f}R) -> {acts}")
    return f"ESCORT: guarding {out['watching']} | " + " | ".join(bits)
