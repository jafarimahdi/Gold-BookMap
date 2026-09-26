"""
shooting_team.py — THE SHOOTING TEAM ("the shooter")
=====================================================

Four teams, four jobs, never mixed:

    SIGNAL   (scout)    "where are the doors?"                   -> the map
    POWER    (legs)     "which door do we reach first?"          -> direction + strength
    SHOOTING (shooter)  "do we take the shot, and where do we    -> OPEN / CLOSE
                         put the target and the stop?"
    ESCORT   (friends)  "is the trade still safe?"               -> built later

THE JOB, IN THE OWNER'S WORDS
  Before shooting, check the FRONT DOORS - everything standing between us and the
  target - and the BACK DOORS, where we can rest and wait for help if we get tired
  and cannot go further.

WHY THIS TEAM EXISTS
  The scout finds a door. The legs say we are pushing that way. Neither of them has
  looked at the CORRIDOR. On the owner's own Bookmap screenshot:

      target 4400.0  ..  23 lots, 14.1 points away
      the road       ..  27 doors in the way, 180 lots to push through

  The road was EIGHT TIMES bigger than the destination. A target is not reachable
  because the door is big - it is reachable when the FORCE beats the ROAD.

  And behind us sat 12 lots at 4415.0. That is not just protection: it is where the
  stop-loss belongs, and it is where a tired move can lean and wait for help.

WHAT IT PRODUCES
    {"shot": "GO" | "NO_GO" | "WAIT", "why": [...],
     "entry":, "target":, "stop":, "rr":, "first_stop":, "shelter":}

IT NEVER SENDS AN ORDER. It writes its plan to the diary so the tape can mark it.
Nothing here touches step4. Delete this file and the robot behaves identically.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = ["plan_shot", "describe"]


def _f(x, d=0.0):
    try:
        v = float(x)
        return v if v == v else d
    except Exception:
        return d


def plan_shot(signal_map: Dict[str, Any], power: Dict[str, Any],
              price: float, atr: float, spread: float = 0.0,
              config: Any = None, book=None) -> Dict[str, Any]:
    """Decide whether to take the shot, and where the target and stop go."""
    why: List[str] = []
    price, atr = _f(price), max(_f(atr), 1e-9)
    spread = _f(spread)

    pick = (power or {}).get("pick")
    conf = _f((power or {}).get("confidence"))
    push = _f((power or {}).get("push"))

    if not pick:
        return _no("WAIT", ["the legs picked no door - nothing to shoot at"], price)

    going_up = (pick == "above")
    doors = (signal_map or {}).get("above" if going_up else "below") or []
    if not doors:
        return _no("NO_GO", ["the legs picked a side with no door on it"], price)
    target = doors[0]

    tgt_px = _f(target.get("price"))
    dist = abs(tgt_px - price)
    why.append(f"target {tgt_px:.2f} ({target.get('size')} lots, {dist:.2f} away, "
               f"hold {target.get('resistance')})")

    # ---- 1. THE FRONT DOORS: how crowded is the corridor? -------------------
    doors_between = int(_f(target.get("doors_between")))
    lots_between = _f(target.get("lots_between"))
    first_stop = target.get("biggest_in_path")
    tgt_lots = _f(target.get("size"))

    if doors_between:
        why.append(f"road: {doors_between} door(s), {lots_between:.0f} lots to push through")
    else:
        why.append("road: clear, nothing in the way")
    if first_stop:
        why.append(f"first stop {first_stop['price']:.2f} ({first_stop['size']:.0f} lots) "
                   f"- expect a pause, good place to take part of the profit")

    # Force must beat the road, not the door. The legs' confidence is 0..1; the road is
    # in lots. Normalise the road against the target itself: a corridor far heavier than
    # the destination needs real strength to cross.
    road_ratio = (lots_between / tgt_lots) if tgt_lots > 0 else (lots_between or 0.0)
    road_hard = road_ratio >= _f(getattr(config, "SHOOT_ROAD_RATIO", 6.0), 6.0)
    if road_hard:
        why.append(f"the road is {road_ratio:.1f}x the size of the target itself "
                   f"- that is a crowded corridor")

    # ---- 2. THE BACK DOORS: who has our back? -------------------------------
    shelter = None
    if book is not None:
        try:
            shelter = book.shelter_behind(price, going_up)
        except Exception:
            shelter = None
    if shelter:
        why.append(f"shelter {shelter['price']:.2f} ({shelter['size']:.0f} lots, "
                   f"{shelter['distance']:.2f} behind) - stop goes behind it, and a tired "
                   f"move can rest there")
    else:
        why.append("no shelter behind us - nothing to lean on if we stall")

    # ---- 2b. QUEUE (D7b: moved here from the scout) --------------------------
    # queue_pos asks "would WE actually get filled here?". That is not a question about
    # the map, it is a question about THIS moment of execution - so it belongs to the
    # shooter, not the scout. If the level we would join is already thick, we are last
    # in a long line and may never be served at this price.
    queue_note = None
    try:
        ladder = (signal_map or {}).get("ladder") or []
        side_key = (lambda pp: pp < price) if going_up else (lambda pp: pp > price)
        ours = [s for pp, s in ladder if side_key(pp) and abs(pp - price) <= max(spread * 3, 0.5)]
        if ours:
            q = max(ours)
            thr = _f(getattr(config, "QUEUE_POS_THRESHOLD", 0.7), 0.7)
            if q >= 25:
                queue_note = f"queue at our price is {q:.0f} lots deep - we may be last in line"
                why.append(queue_note)
    except Exception:
        pass

    # ---- 3. GEOMETRY: target just BEFORE the door, stop just BEHIND shelter --
    buf = max(spread * 2.0, _f(getattr(config, "PM_TP_BUFFER_ATR", 0.15), 0.15) * atr)
    if going_up:
        tp = tgt_px - buf
        sl = (shelter["price"] - buf) if shelter else (price - 2.0 * atr)
    else:
        tp = tgt_px + buf
        sl = (shelter["price"] + buf) if shelter else (price + 2.0 * atr)

    reward = abs(tp - price)
    risk = abs(price - sl)
    rr = (reward / risk) if risk > 1e-9 else 0.0
    why.append(f"reward {reward:.2f} vs risk {risk:.2f} -> R:R {rr:.2f}")

    # ---- 4. THE DECISION -----------------------------------------------------
    min_rr = _f(getattr(config, "SHOOT_MIN_RR", 1.2), 1.2)
    min_conf = _f(getattr(config, "SHOOT_MIN_CONF", 0.30), 0.30)

    if reward <= spread * 3.0:
        return _no("NO_GO", why + [f"reward {reward:.2f} is barely the spread - not a trade"],
                   price, tp, sl, rr, first_stop, shelter, target)
    if rr < min_rr:
        return _no("NO_GO", why + [f"R:R {rr:.2f} below {min_rr:.2f} - the shelter is too far "
                                   f"behind for this target"],
                   price, tp, sl, rr, first_stop, shelter, target)
    if conf < min_conf:
        return _no("WAIT", why + [f"legs only {conf:.2f} sure (need {min_conf:.2f}) - "
                                  f"wait for more push"],
                   price, tp, sl, rr, first_stop, shelter, target)
    if road_hard and conf < min_conf * 1.6:
        return _no("WAIT", why + ["crowded corridor and only ordinary push - "
                                  "wait for the road to thin or the push to grow"],
                   price, tp, sl, rr, first_stop, shelter, target)

    why.append(f"GO: push {push:+.2f}, confidence {conf:.2f}, road passable")
    return {"shot": "GO", "side": "BUY" if going_up else "SELL", "why": why,
            "entry": round(price, 2), "target": round(tp, 2), "stop": round(sl, 2),
            "rr": round(rr, 2), "reward": round(reward, 2), "risk": round(risk, 2),
            "door": target, "first_stop": first_stop, "shelter": shelter,
            "doors_between": doors_between, "lots_between": lots_between,
            "road_ratio": round(road_ratio, 2), "confidence": conf}


def _no(shot, why, price, tp=None, sl=None, rr=0.0, first_stop=None, shelter=None,
        door=None) -> Dict[str, Any]:
    return {"shot": shot, "side": None, "why": why, "entry": round(_f(price), 2),
            "target": (round(tp, 2) if tp else None), "stop": (round(sl, 2) if sl else None),
            "rr": round(_f(rr), 2), "door": door, "first_stop": first_stop,
            "shelter": shelter}


def describe(plan: Dict[str, Any]) -> str:
    if not plan:
        return "SHOOTER: no plan"
    if plan.get("shot") != "GO":
        return f"SHOOTER: {plan.get('shot')} - {(plan.get('why') or ['?'])[-1]}"
    fs = plan.get("first_stop") or {}
    return (f"SHOOTER: GO {plan['side']} @ {plan['entry']:.2f} -> target {plan['target']:.2f} "
            f"stop {plan['stop']:.2f} (R:R {plan['rr']:.2f}) | road {plan['doors_between']} "
            f"doors / {plan['lots_between']:.0f} lots"
            + (f", first stop {fs.get('price'):.2f}" if fs else ""))
