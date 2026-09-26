"""
power_team.py — THE POWER TEAM ("the legs")
============================================

The scout draws the map of doors. The legs answer ONE question about it:

        "which door does the cart reach first — the ceiling or the floor?"

Not "is the market bullish". That question is vague, unfalsifiable, and measured on
this tape the old panel answered it 40.9% / 47.8% / 41.7% correctly at 15/30/60 min.
A coin does better. "Which door first" is a fact the tape settles within minutes, so
every judge can finally be marked against the same right answer.

THE CENTRAL IDEA: EVERY JUDGE GETS A JOB, NOT A VOTE
  Today all sixteen say BUY or SELL with a weight and the robot averages them. That is
  wrong, because most of them cannot see a direction at all. Ask volume_roc which way
  the market is going and it has no idea - it only knows the street got louder.
  Averaging that into a direction score is asking a sound-meter which way the wind blows.

  So only FOUR judges may pick a side. The other twelve make that pick stronger, weaker,
  or cancelled:

    TRIGGER     sweep                      somebody just sprinted - jumps the queue
    DIRECTION   footprint_delta            who is shoving, and how hard
                l3_aggr_limit              are they in a hurry
                cvd_momentum               speeding up or tiring
    CONFIRMER   footprint_levels           breadth: many prices or one loud print
                volume_roc                 fuel: is the street filling up  (GATE, no side)
    VETO        cvd_divergence             rolling with nobody pushing -> stand alone
    REGIME      value_area                 inside the crowd = fade, outside = follow
                mtf                        do the clocks agree
    STRETCH     vwap_bands / vwap_trend    we have come a long way already
    MAGNET      poc_day / htf_poc /        where the cart drifts back to
                supply_demand
    STAND DOWN  news_sentiment             a storm is coming
                macro_risk                 background weather

"NEITHER" IS A LEGAL ANSWER. Most minutes there is no reachable door and no real push.
A robot that must always have an opinion will always find one.

THIS MODULE TRADES NOTHING. It reads, decides, and writes its pick to the diary so it
can be marked later. No vote, no order, no change to today's behaviour.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

__all__ = ["decide", "describe", "JUDGE_ROLES"]

JUDGE_ROLES = {
    "sweep": "TRIGGER",
    "footprint_delta": "DIRECTION", "l3_aggr_limit": "DIRECTION",
    "cvd_momentum": "DIRECTION",
    "footprint_levels": "CONFIRMER", "volume_roc": "CONFIRMER",
    "cvd_divergence": "VETO",
    "value_area": "REGIME", "mtf": "REGIME",
    "vwap_bands": "STRETCH",
    "news_sentiment": "STAND_DOWN",
    # NEW 2026-09-25 - each sees something no other judge in this robot can see
    "big_prints": "DIRECTION",     # are the actual TRADES big, or algo churn?
    "stall_clock": "REGIME",       # how long have we been coiled in one place?
}
# RETIRED from this team 2026-09-25, with reasons, so nobody re-hires them by accident:
#   vwap_trend    -> redundant. It said "past fair value or before it"; vwap_bands says
#                    "2.4 sigma past fair value". The second contains the first.
#   macro_risk    -> the weather in another country. 120-minute horizon, never decisive
#                    inside five minutes. Recorded for months, used never.
#   poc_day       -> MOVED TO THE SCOUT (D7a). They name PRICES, and naming a price is
#   htf_poc          a door-maker's job. They were sitting on the force team shouting
#   supply_demand    street names at a runner who only cares about his own energy.


def _f(x, d=0.0):
    try:
        v = float(x)
        return v if v == v else d          # NaN guard
    except Exception:
        return d


def _clip(x, lo=-1.0, hi=1.0):
    return max(lo, min(hi, x))


def decide(signal_map: Dict[str, Any], order_flow: Any = None, footprint: Any = None,
           level3: Any = None, volume_profile: Any = None, trend: Any = None,
           volatility: Any = None, news: Any = None, macro: Any = None,
           price: float = 0.0, divergence: float = 0.0,
           mtf: Optional[Dict[str, str]] = None, config: Any = None,
           tick_data: Optional[List[Dict]] = None,
           candles: Optional[List[Dict]] = None,
           atr: float = 0.0) -> Dict[str, Any]:
    """Pick the door price will touch first. Returns a dict; never raises."""
    why: List[str] = []
    judges: Dict[str, Any] = {}
    price = _f(price)
    atr = max(_f(atr), 1e-9)

    above = (signal_map or {}).get("best_above")
    below = (signal_map or {}).get("best_below")
    if not above and not below:
        return _answer(None, 0.0, ["no door in range - nothing to aim at"], judges)

    # ---------------- DIRECTION: who is pushing the cart ---------------------
    # footprint_delta: aggression at each price, -1..+1
    fp_delta = _clip(_f(getattr(footprint, "delta_imbalance", 0.0)))
    judges["footprint_delta"] = round(fp_delta, 3)

    # l3_aggr_limit: are they in a hurry? impatient buy volume vs impatient sell volume
    ab = _f(getattr(level3, "aggressive_buy_volume", 0.0))
    asl = _f(getattr(level3, "aggressive_sell_volume", 0.0))
    aggr = ((ab - asl) / (ab + asl)) if (ab + asl) > 0 else 0.0
    judges["l3_aggr_limit"] = round(aggr, 3)

    # cvd_momentum: is the push accelerating or tiring?
    delta = _f(getattr(order_flow, "delta", 0.0))
    cvd = _f(getattr(order_flow, "cvd", 0.0))
    mom = _clip(delta / 400.0)                       # 400 lots of one-sided delta = full
    judges["cvd_momentum"] = round(mom, 3)

    # ---- JUDGE: big_prints --------------------------------------------------
    # Every other judge knows HOW MUCH traded and WHICH SIDE was impatient. None of
    # them knows WHO. 100 lots in one print is an institution deciding something;
    # 100 lots in 100 prints of 1 is algorithms passing a ball back and forth. Those
    # two are identical to volume_roc, footprint_delta and footprint_levels alike.
    # l3_large_ofi watches large orders RESTING (a promise); this watches large orders
    # EXECUTED (money actually spent).
    big_share, big_dir = 0.0, 0.0
    try:
        ticks = list(tick_data or [])[-1500:]
        if len(ticks) >= 30:
            vols = [abs(_f(x.get("volume"))) for x in ticks]
            tot = sum(vols) or 1.0
            cut = max(5.0, sorted(vols)[int(len(vols) * 0.98)])   # top 2% of prints
            bigs = [x for x in ticks if abs(_f(x.get("volume"))) >= cut]
            big_share = sum(abs(_f(x.get("volume"))) for x in bigs) / tot
            bb = sum(abs(_f(x.get("volume"))) for x in bigs
                     if str(x.get("side", "")).upper().startswith("B"))
            bs = sum(abs(_f(x.get("volume"))) for x in bigs) - bb
            big_dir = ((bb - bs) / (bb + bs)) if (bb + bs) > 0 else 0.0
    except Exception:
        pass
    judges["big_prints"] = {"share": round(big_share, 3), "dir": round(big_dir, 3)}

    push = _clip(0.45 * fp_delta + 0.30 * aggr + 0.15 * mom + 0.10 * big_dir)
    why.append(f"push {push:+.2f} (delta {fp_delta:+.2f}, hurry {aggr:+.2f}, "
               f"mom {mom:+.2f}, big prints {big_dir:+.2f} @ {big_share*100:.0f}% of volume)")

    # ---------------- CONFIRMERS: is this real, and is there fuel ------------
    bl = _f(getattr(footprint, "buying_levels", 0.0))
    sl = _f(getattr(footprint, "selling_levels", 0.0))
    breadth_dir = ((bl - sl) / (bl + sl)) if (bl + sl) > 0 else 0.0
    # Three cases, not two. Zero breadth is NEUTRAL - equal buying and selling
    # levels - and must not be punished as if it argued with the push.
    judges["footprint_levels"] = round(breadth_dir, 3)
    if breadth_dir * push < 0:
        breadth_mult = 0.6
        why.append(f"breadth {breadth_dir:+.2f} disagrees with the push -> 0.6x")
    elif abs(breadth_dir) < 0.05:
        breadth_mult = 0.85
        why.append("breadth flat - no confirmation either way -> 0.85x")
    else:
        breadth_mult = 1.0
        why.append(f"breadth {breadth_dir:+.2f} confirms the push")

    # volume_roc is a GATE. It has no direction and must never pick a side.
    vroc = _f(getattr(volume_profile, "volume_rate_of_change", 0.0))
    fuel_mult = 1.0 if vroc >= 0.5 else (0.75 if vroc >= 0.0 else 0.55)
    judges["volume_roc"] = round(vroc, 3)
    if fuel_mult < 1.0:
        why.append(f"volume RoC {vroc:+.2f} - thin street -> {fuel_mult:.2f}x")

    # ---------------- JUDGE: stall_clock -------------------------------------
    # How long have we been trapped in the same small range? A market that has gone
    # nowhere for a long time is coiled; one that has just travelled is tired. ADX and
    # Bollinger width gesture at this, but neither of them counts TIME.
    coil = 0.0
    try:
        cl = list(candles or [])[-20:]
        if len(cl) >= 8:
            hi = max(_f(c.get("high")) for c in cl)
            lo = min(_f(c.get("low")) for c in cl)
            rng = (hi - lo) / atr if atr > 0 else 0.0
            coil = max(0.0, min(1.0, (2.5 - rng) / 2.5))
    except Exception:
        pass
    judges["stall_clock"] = round(coil, 3)
    coil_mult = 1.0 + 0.25 * coil
    if coil >= 0.5:
        why.append(f"coiled: last 20 bars held a narrow range (coil {coil:.2f}) "
                   f"-> a real push here matters more")

    # ---------------- REGIME: which rulebook applies -------------------------
    vah = _f(getattr(volume_profile, "value_area_high", 0.0))
    val = _f(getattr(volume_profile, "value_area_low", 0.0))
    inside_value = bool(vah and val and val <= price <= vah)
    judges["value_area"] = "inside" if inside_value else "outside"
    regime_mult = 0.75 if inside_value else 1.0
    why.append("inside value area - the cart rattles, do not chase -> 0.75x"
               if inside_value else "outside value area - room to run")

    # mtf: a referee, not a player
    agree_n = 0
    if isinstance(mtf, dict) and mtf:
        ups = sum(1 for v in mtf.values() if str(v).upper().startswith("UP"))
        downs = sum(1 for v in mtf.values() if str(v).upper().startswith("DOWN"))
        agree_n = max(ups, downs)
        aligned = (ups > downs and push > 0) or (downs > ups and push < 0)
        mtf_mult = 1.15 if (aligned and agree_n >= 2) else (0.85 if agree_n >= 2 else 1.0)
        judges["mtf"] = f"{ups}up/{downs}down"
    else:
        mtf_mult = 1.0
        judges["mtf"] = "n/a"

    # ---------------- STRETCH: have we already come a long way ---------------
    z = _f(getattr(volume_profile, "vwap_zscore", 0.0))
    judges["vwap_bands"] = round(z, 2)
    stretched_with_push = (z > 1.5 and push > 0) or (z < -1.5 and push < 0)
    stretch_mult = 0.65 if stretched_with_push else 1.0
    if stretched_with_push:
        why.append(f"already stretched at {z:+.1f} sigma in the push direction -> 0.65x")

    # ---------------- TRIGGER: somebody sprinted -----------------------------
    swept = bool(_f(getattr(level3, "sweep_detected", 0.0))) or \
        bool(getattr(level3, "sweep", False))
    trigger_mult = 1.35 if swept else 1.0
    judges["sweep"] = bool(swept)
    if swept:
        why.append("SWEEP fired - someone paid worse prices to get in now -> 1.35x")

    # ---------------- VETO: rolling with nobody pushing ----------------------
    div = _f(divergence)
    judges["cvd_divergence"] = round(div, 3)
    vetoed = abs(div) > 0.3 and (div * push) < 0
    if vetoed:
        why.append(f"VETO: divergence {div:+.2f} against the push - the move is done")
        return _answer(None, 0.0, why, judges, push=push)

    # ---------------- STAND DOWN: a storm ------------------------------------
    impact = str(getattr(news, "impact_level", "LOW") or "LOW").upper()
    mins = abs(_f(getattr(news, "minutes_to_next_event", 0.0)))
    hi_win = _f(getattr(config, "NEWS_HIGH_WINDOW_MINUTES", 120.0), 120.0)
    judges["news_sentiment"] = f"{impact}@{mins:.0f}min"
    if impact == "HIGH" and mins <= min(30.0, hi_win):
        why.append(f"HIGH impact event in {mins:.0f} min - stand down")
        return _answer(None, 0.0, why, judges, push=push)

    # ---------------- WHICH DOOR ---------------------------------------------
    # Two ingredients only: how badly the cart is being pushed that way, and how
    # easy the door is to reach (the scout already folded distance and size into
    # 'attraction'). Everything above only scales the confidence.
    def side_score(door, want_positive_push):
        if not door:
            return None
        align = max(0.0, push if want_positive_push else -push)
        return 0.55 * _f(door.get("attraction")) + 0.45 * align

    s_up = side_score(above, True)
    s_dn = side_score(below, False)

    if s_up is None and s_dn is None:
        return _answer(None, 0.0, why + ["no door"], judges, push=push)

    # Only ONE door on the map. If the push clearly faces the other way, the honest
    # answer is "neither" - price is more likely to leave the map than to turn round
    # and touch the only door behind it. (Seen live 2026-09-25: a lone floor was
    # picked while the push was positive.)
    lone_against = 0.20
    if s_up is None:
        if push > lone_against:
            return _answer(None, 0.0, why + [
                f"only a floor on the map but the push is {push:+.2f} - price is walking "
                f"away from the only door -> neither"], judges, push=push)
        pick, best, other = "below", s_dn, 0.0
    elif s_dn is None:
        if push < -lone_against:
            return _answer(None, 0.0, why + [
                f"only a ceiling on the map but the push is {push:+.2f} - price is walking "
                f"away from the only door -> neither"], judges, push=push)
        pick, best, other = "above", s_up, 0.0
    elif s_up >= s_dn:
        pick, best, other = "above", s_up, s_dn
    else:
        pick, best, other = "below", s_dn, s_up

    # confidence = how much the winner beats the loser, then every modifier
    edge = (best - other) / max(best + other, 1e-9)
    conf = best * (0.5 + 0.5 * edge)
    conf *= (breadth_mult * fuel_mult * regime_mult * mtf_mult * stretch_mult
             * trigger_mult * coil_mult)
    conf = max(0.0, min(1.0, conf))

    door = above if pick == "above" else below
    why.append(f"door {door['price']:.2f} at {door['dist_atr']:.2f} ATR, "
               f"reach {door['attraction']:.2f} hold {door['resistance']:.2f}")

    # A push of nothing is not an opinion.
    if abs(push) < 0.05 and edge < 0.15:
        return _answer(None, round(conf, 3),
                       why + ["no real push and no clearly nearer door - sit still"],
                       judges, push=push)

    return _answer(pick, round(conf, 3), why, judges, push=push, door=door)


def _answer(pick, conf, why, judges, push=0.0, door=None) -> Dict[str, Any]:
    return {"pick": pick, "confidence": conf, "push": round(_f(push), 3),
            "why": why, "judges": judges,
            "door": ({"price": door["price"], "dist_atr": door["dist_atr"],
                      "resistance": door["resistance"]} if door else None),
            "roles": JUDGE_ROLES}


def describe(ans: Dict[str, Any]) -> str:
    if not ans:
        return "POWER: no answer"
    if not ans.get("pick"):
        return f"POWER: NEITHER (push {ans.get('push', 0):+.2f}) - " + \
               (ans.get("why") or ["no reason"])[-1]
    d = ans.get("door") or {}
    return (f"POWER: {ans['pick'].upper()} door {d.get('price', 0):.2f} "
            f"({d.get('dist_atr', 0):.2f} ATR, hold {d.get('resistance', 0):.2f}) "
            f"confidence {ans['confidence']:.2f}, push {ans['push']:+.2f}")
