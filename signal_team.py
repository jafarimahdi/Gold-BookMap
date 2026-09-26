"""
signal_team.py — THE SIGNAL TEAM ("the scout")
===============================================

The scout walks the hallway and draws the map of doors. He never says which way
to walk. That is the legs' job (POWER), and later the friends' job (ESCORT).

    POWER   "which way is it going, and how hard?"        -> direction
    SIGNAL  "where are the doors, and can I trust them?"   -> THE MAP   <- this file
    ESCORT  "is my open trade still safe?"                 -> built later

WHY THE SCOUT NEEDS A MEMORY  (owner's idea, 2026-09-25)
  Looking once tells you only how thick a door is. Looking every cycle tells you
  the things that actually matter:

      "that door has been there all morning"     -> strong
      "that door appeared 30 seconds ago"        -> probably fake
      "that door is getting thicker"             -> someone is building it
      "that door is getting thinner"             -> they are leaving, it will break
      "people keep pushing and it will not move" -> someone big is holding it

  None of that exists in a single snapshot. A photo becomes a film.

TWO SCORES PER DOOR, NOT ONE  (forced by measurement, 2026-09-25)
  On real tape: price REACHED the wall 75% of the time (median 5 minutes) but
  STALLED there only 8% of the time, and came back past it 83% of the time.
  So "will I get there" and "will it stop me" are different questions:

      ATTRACTION  will price travel to this level?   mostly closeness + size
                  -> this picks the TARGET
      RESISTANCE  will it hold when price arrives?   mostly age + growth, minus
                  how hard it is being eaten
                  -> this decides take-profit in front of it, or ride through

  A thick old growing wall  -> exit in front of it.
  A thick wall being eaten  -> it is about to fall, ride through.

HOW THE NINE JUDGES ARE USED
  THREE CREATE LEVELS (they can name a price):
      whale_walls   a pile of resting size at a price
      iceberg       hidden size that keeps refilling at a price
      spoof_invert  a known liar -> the level is BANNED, not merely penalised

  SIX DESCRIBE LEVELS (they cannot name a price, they qualify the ones that exist):
      l3_net_flow   is size being added or pulled          -> growth
      l3_large_ofi  is it the big players doing it         -> big-player flag
      l3_imbalance  which side is heavier right now        -> side context
      microprice    the earliest tilt, sub-tick            -> side context
      absorption    a wall being eaten without price moving -> under attack
      (queue_pos moved to the SHOOTER 2026-09-25 - "would we get filled" is an
       execution question, not a map question)

THIS MODULE CASTS NO VOTE AND SENDS NO ORDER.
Pure bookkeeping over data the robot already has. Delete it and the robot behaves
exactly as before.
"""
from __future__ import annotations

import statistics
import time
from typing import Any, Dict, List, Optional

__all__ = ["build_signal_map", "describe", "SIGNAL_JUDGES", "LevelBook", "get_book"]

SIGNAL_JUDGES = {
    # they NAME a live price -> they make doors
    "creators": ["whale_walls", "iceberg", "spoof_invert"],
    # they cannot name a price; they qualify the doors that exist
    "describers": ["l3_net_flow", "l3_large_ofi", "l3_imbalance",
                   "microprice", "absorption"],
    # D7a (2026-09-25): moved here from POWER. They name REMEMBERED prices, and naming
    # a price is a door-maker's job. They also answer the owner's rule L2 - "what
    # happened previously when the price was here before".
    "history": ["poc_day", "htf_poc", "supply_demand"],
    # hired 2026-09-25: nobody else watches walls DISAPPEAR, and an emptying corridor
    # is what lets price run
    "change": ["road_vacuum"],
    # LEFT THIS TEAM: queue_pos -> moved to the SHOOTER (D7b). "Would we get filled"
    # is a question about this moment of execution, not about the map.
}

# The feed gives 20 levels per side (BOOKMAP_MAX_DEPTH_LEVELS). Measured on real
# tape, only the top 1-2% are walls, so 0-8 qualify per side. Remember 8 - more
# than the feed usually produces, so nothing real is lost - and report 3, because
# you only ever trade toward the nearest reachable one.
REMEMBER_PER_SIDE = 8
REPORT_PER_SIDE = 3
BAND_ATR = 4.0          # reach band in ATR...
BAND_PCT = 0.0035       # ...or this much of price, whichever is WIDER. A pure ATR band
                        # goes blind when ATR collapses (seen 2026-09-25: 4 ATR shrank
                        # to $2.96 and the real target 14 points away was unreachable).
OUTLIER_X = 2.5         # a door must be this many times its NEIGHBOURS, not a fixed size.
                        # From the owner's Bookmap screenshot: levels near price hold
                        # 7-16 lots of market-maker quote, while the one real wall held
                        # 23 against neighbours of 4 = 5.1x. Absolute size cannot tell
                        # those apart; a ratio can.
MIN_SPREADS = 10.0      # a door closer than this many spreads is not worth trading -
                        # the round trip eats the profit. 4415 was 3x spread away.
ROUND_BONUS = 0.08      # institutions cluster on round numbers (4400.0 held 23 lots)
STALE_CYCLES = 5        # not seen this many cycles -> fading
FORGET_CYCLES = 15      # not seen this many -> drop it entirely


def _f(x, d=0.0):
    try:
        return float(x)
    except Exception:
        return d


def _saturate(x: float, half: float) -> float:
    """0..1, reaching 0.5 at `half`. Saturating on purpose: a 40-lot wall is
    remarkable next to an 11-lot p99, but it is not four times as likely to hold."""
    if half <= 0:
        return 0.5
    r = max(0.0, x / half)
    return r / (1.0 + r)


class Level:
    """One door in the hallway, and everything the scout remembers about it."""

    __slots__ = ("price", "side", "first_seen", "last_seen", "cycles_seen",
                 "missing", "size_now", "size_first", "size_max", "sources",
                 "refills", "spoofed", "attacked", "big_player", "outlier_x")

    def __init__(self, price: float, side: str, size: float, now: float):
        self.price = price
        self.side = side
        self.first_seen = now
        self.last_seen = now
        self.cycles_seen = 1
        self.missing = 0
        self.size_now = size
        self.size_first = size
        self.size_max = size
        self.sources: List[str] = []
        self.refills = 0
        self.spoofed = False
        self.attacked = 0
        self.big_player = False
        self.outlier_x = 0.0

    # -- what the memory makes possible -------------------------------------
    @property
    def age_sec(self) -> float:
        return max(0.0, self.last_seen - self.first_seen)

    @property
    def growth(self) -> float:
        """+1 growing strongly, 0 stable, -1 melting away.

        Needs TWO cycles. Seen live 2026-09-25 22:54: a door was labelled "growing"
        on its very first sighting, because the book reported one size and an order
        event reported another inside the same cycle. There is nothing to compare
        against on cycle one, so the honest answer is "no trend yet".
        """
        if self.size_first <= 0 or self.cycles_seen < 2:
            return 0.0
        chg = (self.size_now - self.size_first) / self.size_first
        return max(-1.0, min(1.0, chg))

    @property
    def trend_word(self) -> str:
        if self.cycles_seen < 2:
            return "new"
        g = self.growth
        return "growing" if g > 0.15 else ("shrinking" if g < -0.15 else "steady")


class LevelBook:
    """The scout's notebook. Survives between cycles - that is the whole point."""

    def __init__(self):
        self.levels: Dict[float, Level] = {}
        self.last_ladder: List = []
        self.band_history: List[float] = []      # total lots in the band, per cycle
        self.cycles = 0

    # -- one cycle -----------------------------------------------------------
    def ingest(self, level3: Any, price: float, atr: float, config: Any = None,
               order_flow: Any = None, spread: float = 0.0) -> Dict[str, Any]:
        self.cycles += 1
        now = time.time()
        price = _f(price)
        atr = max(_f(atr), 1e-9)
        if price <= 0:
            return self._empty("no price")

        # ATR SANITY FLOOR.  Everything here is measured in ATR, so a collapsed ATR
        # makes the scout go blind: seen live 2026-09-25 22:38, ATR fell to 0.74 on
        # 47 thin Friday-evening bars (normal is ~3.18). The 4-ATR band shrank from
        # $12.72 to $2.96 and real doors were discarded as "too far" - the map went
        # from 7 doors to 1. The floor keeps the field of view open, and says so.
        floor_pct = _f(getattr(config, "SCOUT_ATR_FLOOR_PCT", 0.0004), 0.0004) \
            if config is not None else 0.0004
        atr_floor = price * floor_pct
        atr_raw = atr
        atr_floored = atr < atr_floor
        if atr_floored:
            atr = atr_floor

        g = (lambda k, d: _f(getattr(config, k, d), d)) if config is not None else (
            lambda k, d: d)
        whale_thr = g("L3_WHALE_THRESHOLD", 10.0)
        ice_min = g("ICEBERG_MIN_REFILLS", 3.0)

        ob = getattr(level3, "order_book", None) or {}
        spoof_levels = getattr(level3, "spoof_levels", None) or {}
        ice_levels = getattr(level3, "iceberg_levels", None) or {}
        ice_meta = getattr(level3, "iceberg_meta", None) or {}

        seen_now = set()

        # Keep this cycle's RAW ladder. The "road" to a target is made of all the little
        # groups standing in the corridor - exactly the levels the outlier filter throws
        # away as market-maker noise. They are wrong as TARGETS but they are the honest
        # measure of how crowded the walk is, so the shooter needs them.
        ladder = []
        for side_key in ("bids", "asks"):
            for row in (ob.get(side_key) or []):
                try:
                    ladder.append((round(_f(row[0]), 2), _f(row[1])))
                except Exception:
                    continue
        ladder.sort()
        self.last_ladder = ladder

        # ---- JUDGE: road_vacuum ------------------------------------------------
        # Everyone else hunts for WALLS - the things that STOP price. Nobody watches
        # for walls DISAPPEARING, which is what lets price RUN. A corridor holding 180
        # lots that empties to 40 is the single loudest thing in an order book, and no
        # other judge in this robot can see it, because they all read the book as a
        # photograph. This one reads the difference between two photographs.
        band_now = sum(s for pp, s in ladder if abs(pp - price) <= max(BAND_ATR * atr,
                                                                      price * BAND_PCT))
        self.band_history.append(band_now)
        if len(self.band_history) > 30:
            self.band_history.pop(0)

        band = max(BAND_ATR * atr, price * BAND_PCT)

        def touch(p, side, size=0.0) -> Optional[Level]:
            p = round(_f(p), 2)
            if p <= 0 or abs(p - price) > band:
                return None
            lv = self.levels.get(p)
            if lv is None:
                lv = Level(p, side, size, now)
                self.levels[p] = lv
            else:
                lv.last_seen = now
                lv.side = side
                first_touch_this_cycle = p not in seen_now
                if first_touch_this_cycle:
                    lv.cycles_seen += 1
                lv.missing = 0
                if size > 0:
                    # Two sources can report the same level in one cycle (book snapshot
                    # and order events). Within a cycle keep the LARGEST reading; across
                    # cycles the size must be free to fall, or shrinking is never seen.
                    lv.size_now = size if first_touch_this_cycle else max(lv.size_now, size)
                    lv.size_max = max(lv.size_max, size)
                    # While still on cycle one, the baseline must follow the largest
                    # reading too - otherwise the book's first number becomes the
                    # baseline, the event's bigger number becomes "now", and cycle two
                    # reports growth that never happened.
                    if lv.cycles_seen <= 1:
                        lv.size_first = lv.size_now
            seen_now.add(p)
            return lv

        # --- CREATORS --------------------------------------------------------
        # whale_walls: resting size at a price
        for side, key in (("bid", "bids"), ("ask", "asks")):
            for row in (ob.get(key) or []):
                try:
                    p, s = _f(row[0]), _f(row[1])
                except Exception:
                    continue
                # The threshold decides what becomes a DOOR, not what we keep watching.
                # Seen live 2026-09-25: a 20-lot wall melted to 9, fell under the
                # threshold, and the map froze it at 20 lots labelled "growing" -
                # exactly the collapse the memory exists to catch. A door already in
                # the notebook is updated whatever its size; only NEW doors must clear
                # the bar.
                known = round(p, 2) in self.levels
                # OUTLIER TEST: is this level big compared with the ones AROUND it?
                # A fixed "10 lots" cannot tell a market-maker quote from a real wall.
                # Near price every level holds 7-16 lots because somebody is obliged to
                # quote there; those vanish the moment price arrives. A level holding 5x
                # its neighbours is a decision, not an obligation.
                nb = [_f(r[1]) for r in (ob.get(key) or [])
                      if abs(_f(r[0]) - p) <= 1.0 and abs(_f(r[0]) - p) > 1e-9]
                local = statistics.median(nb) if nb else 0.0
                stands_out = (s >= OUTLIER_X * local) if local > 0 else (s >= whale_thr)
                if not known and not (s >= whale_thr and stands_out):
                    continue
                lv = touch(p, side, s)
                if lv and s >= whale_thr and "whale" not in lv.sources:
                    lv.sources.append("whale")
                if lv and local > 0:
                    lv.outlier_x = round(s / local, 2)

        # whale_walls, SECOND SOURCE: the order-event stream.
        # Seen live 2026-09-25 22:49 - the whale judge reported an 11-lot wall at
        # 4327.2 while the map reported nothing at all. The judge reads BOTH the book
        # snapshot AND level3.order_events[-500:]; the scout only read the snapshot,
        # so any wall that lives in the event stream was invisible to it.
        #
        # One deliberate difference: the judge counts ANY event over the threshold,
        # including CANCEL. A cancelled order is not a wall - it is the opposite of
        # one - so the scout counts only orders being PLACED.
        for ev in (getattr(level3, "order_events", None) or [])[-500:]:
            try:
                ev_price = _f(ev.get("price"))
                ev_size = _f(ev.get("size"))
                ev_type = str(ev.get("type", "") or "").upper()
                ev_side = str(ev.get("side", "") or "").upper()
            except Exception:
                continue
            if ev_size < whale_thr or ev_price <= 0:
                continue
            # Reject only what REMOVES size. Everything else means "an order of this
            # size exists at this price", which is exactly what a door is.
            # Seen live 2026-09-25 22:59: the judge reported 3 walls / 33 lots while
            # the map said "no door in range". The feed writes BID_NEW / ASK_NEW /
            # CANCEL / REPLACE, and REPLACE is 22% of the file - a modified order is
            # still a resting order, but the old "must contain NEW" filter binned it.
            if any(k in ev_type for k in ("CANCEL", "DELETE", "REMOVE")):
                continue
            if "BID" in ev_type or ev_side in ("BUY", "BID", "B"):
                side = "bid"
            elif "ASK" in ev_type or ev_side in ("SELL", "ASK", "S"):
                side = "ask"
            else:
                side = "bid" if ev_price < price else "ask"
            lv = touch(ev_price, side, ev_size)
            if lv and "event" not in lv.sources:
                lv.sources.append("event")

        # iceberg: hidden size that keeps coming back
        for p, refills in (ice_levels or {}).items():
            if _f(refills) < ice_min:
                continue
            pp = _f(p)
            lv = touch(pp, "bid" if pp < price else "ask")
            if lv is None:
                continue
            lv.refills = int(_f(refills))
            meta = (ice_meta or {}).get(p) or {}
            if _f(meta.get("age_sec")) > 0:
                lv.first_seen = min(lv.first_seen, now - _f(meta.get("age_sec")))
            if "iceberg" not in lv.sources:
                lv.sources.append("iceberg")

        # spoof_invert: a known liar is BANNED, not penalised
        banned = 0
        for p in (spoof_levels or {}):
            lv = self.levels.get(round(_f(p), 2))
            if lv is not None and not lv.spoofed:
                lv.spoofed = True
                banned += 1

        # --- DESCRIBERS ------------------------------------------------------
        # absorption: a wall being eaten while price does not move. The detector
        # is global, not per price, so it is attributed to the nearest level on the
        # side being eaten - an approximation, and labelled as one.
        abs_net = int(_f(getattr(order_flow, "absorption_net", 0)))
        if abs_net:
            side_eaten = "bid" if abs_net < 0 else "ask"
            cands = [lv for lv in self.levels.values()
                     if lv.side == side_eaten and not lv.spoofed]
            if cands:
                nearest = min(cands, key=lambda l: abs(l.price - price))
                nearest.attacked += abs(abs_net)

        # l3_large_ofi: were the adds big-player sized
        if _f(getattr(level3, "large_order_events", 0)) > 0:
            for p in seen_now:
                lv = self.levels.get(p)
                if lv and lv.size_now >= whale_thr * 1.5:
                    lv.big_player = True

        # --- age out what stopped appearing ----------------------------------
        for p, lv in list(self.levels.items()):
            if p not in seen_now:
                lv.missing += 1
                if lv.missing >= FORGET_CYCLES or abs(p - price) / atr > BAND_ATR * 1.5:
                    del self.levels[p]

        # --- keep the notebook small -----------------------------------------
        for side in ("bid", "ask"):
            same = sorted((lv for lv in self.levels.values() if lv.side == side),
                          key=lambda l: abs(l.price - price))
            for lv in same[REMEMBER_PER_SIDE:]:
                self.levels.pop(lv.price, None)

        out = self._render(price, atr, whale_thr, banned, level3, order_flow, spread)
        out["atr_raw"] = round(atr_raw, 3)
        out["atr_floored"] = atr_floored
        return out

    # -- scoring -------------------------------------------------------------
    def _score(self, lv: Level, price: float, atr: float, whale_thr: float) -> Dict[str, Any]:
        dist_atr = abs(lv.price - price) / atr
        size_t = _saturate(lv.size_now, whale_thr)
        persist = _saturate(lv.cycles_seen, 6.0)          # 6 cycles -> 0.5
        fade = 1.0 if lv.missing == 0 else max(0.3, 1.0 - 0.15 * lv.missing)

        # ATTRACTION - will price travel here? Closeness dominates: measured on real
        # tape, 75% of walls were reached at a median distance of 0.5 ATR.
        attraction = (0.65 / (1.0 + dist_atr) + 0.35 * size_t) * fade

        # RESISTANCE - will it hold? Old, thick and growing holds. Being eaten breaks.
        attack_pen = min(0.4, 0.05 * lv.attacked)
        # Growth is SYMMETRIC. Seen live 2026-09-25 22:44: a wall melted 12 -> 10 lots
        # and its hold score went UP, because persistence kept climbing while
        # max(0, growth) refused to score the shrink. A door getting thinner is the
        # clearest sign it is about to break - noticing that is the point of a memory.
        #
        # And a melting door's history is worth less: being watched for six cycles
        # while you dissolve is not a sign of strength, so persistence is discounted
        # when the level is losing size.
        health = 1.0 if lv.growth >= -0.05 else 0.55
        resistance = (0.45 * size_t + 0.30 * persist * health
                      + 0.20 * lv.growth + (0.10 if lv.big_player else 0.0)
                      - attack_pen) * fade
        if "iceberg" in lv.sources:
            resistance = min(1.0, resistance + 0.15)      # hidden size is real size
        # Institutions cluster on round numbers - 4400.0 held 23 lots while every
        # neighbour held 4. A round price is a chosen price.
        if abs(lv.price - round(lv.price)) < 1e-9 and int(round(lv.price)) % 10 == 0:
            resistance = min(1.0, resistance + ROUND_BONUS)
        # standing far above your neighbours is itself evidence of intent
        if lv.outlier_x >= OUTLIER_X:
            resistance = min(1.0, resistance + 0.10)

        return {
            "price": lv.price, "side": lv.side, "size": round(lv.size_now, 1),
            "dist_atr": round(dist_atr, 3), "above": lv.price > price,
            "cycles_seen": lv.cycles_seen, "age_sec": round(lv.age_sec, 1),
            "trend": lv.trend_word, "growth": round(lv.growth, 2),
            "attacked": lv.attacked, "big_player": lv.big_player,
            "sources": list(lv.sources), "missing": lv.missing,
            "outlier_x": lv.outlier_x,
            "attraction": round(max(0.0, min(1.0, attraction)), 3),
            "resistance": round(max(0.0, min(1.0, resistance)), 3),
        }

    def _render(self, price, atr, whale_thr, banned, level3, order_flow, spread=0.0) -> Dict[str, Any]:
        rows = [self._score(lv, price, atr, whale_thr)
                for lv in self.levels.values() if not lv.spoofed]

        # PROFIT FILTER. A door closer than MIN_SPREADS spreads cannot pay for the round
        # trip - from the owner's screenshot, 4415 was 3x the spread away and 4412.5 was
        # 5x, while the one real target was 47x. Too-close doors stay in the notebook
        # (their history still matters) but are never offered as a target.
        min_gap = MIN_SPREADS * _f(spread)
        too_close = 0
        for r in rows:
            r["too_close"] = bool(min_gap > 0 and abs(r["price"] - price) < min_gap)
            if r["too_close"]:
                too_close += 1
        # A remembered door (POC, supply zone) marks a price as IMPORTANT, but with no
        # resting size it is not yet a door you can aim at. Keep it in the notebook - so
        # the moment real size appears there we already know its history - but never
        # offer a 0-lot level as a target.
        usable = [r for r in rows if not r["too_close"] and r["size"] > 0]
        above = sorted([r for r in usable if r["above"]],
                       key=lambda r: -r["attraction"])[:REPORT_PER_SIDE]
        below = sorted([r for r in usable if not r["above"]],
                       key=lambda r: -r["attraction"])[:REPORT_PER_SIDE]

        # context from the six describers - FACTS, never a direction
        ctx = {
            "book_imbalance": round(_f(getattr(level3, "order_book_imbalance", 0.0)), 3),
            "ofi": round(_f(getattr(level3, "ofi", 0.0)), 1),
            "absorption_net": int(_f(getattr(order_flow, "absorption_net", 0))),
            "large_orders": int(_f(getattr(level3, "large_order_events", 0))),
        }
        for r in (above + below):
            r.update(self.road_to(price, r["price"]))

        return {
            "ladder": list(self.last_ladder),
            "targets": above + below,
            "above": above, "below": below,
            "best_above": above[0] if above else None,
            "best_below": below[0] if below else None,
            "n": len(usable), "tracked": len(self.levels), "cycles": self.cycles,
            "too_close": too_close, "min_gap": round(min_gap, 2),
            "road_vacuum": self.vacuum(),
            "banned_spoof": banned, "price": round(price, 2), "atr": round(atr, 3),
            "context": ctx,
        }

    def add_history(self, price: float, atr: float, hist: List[Dict[str, Any]],
                    config: Any = None) -> Dict[str, Any]:
        """D7a: poc_day / htf_poc / supply_demand are DOOR-MAKERS, not force judges.

        A POC is a price. A supply zone is a price. Naming a price is the scout's job,
        and these three name REMEMBERED doors where the rest of the scout names LIVE
        ones. Moving them here also answers the owner's rule L2 - "what happened
        previously when the price was here before" - which nothing else answers.

        hist: [{"price": 4326.0, "kind": "poc_day"|"htf_poc"|"supply"|"demand"}]
        """
        now = time.time()
        atr = max(_f(atr), 1e-9)
        band = max(BAND_ATR * atr, price * BAND_PCT)
        added = 0
        for h in (hist or []):
            hp = round(_f(h.get("price")), 2)
            kind = str(h.get("kind") or "history")
            if hp <= 0 or abs(hp - price) > band:
                continue
            lv = self.levels.get(hp)
            if lv is None:
                lv = Level(hp, "bid" if hp < price else "ask", 0.0, now)
                self.levels[hp] = lv
                added += 1
            else:
                lv.last_seen = now
            if kind not in lv.sources:
                lv.sources.append(kind)
        return {"history_added": added}

    def road_to(self, price: float, target: float) -> Dict[str, Any]:
        """How crowded is the corridor between price and that door?

        Counts every resting level in between - including the small market-maker
        groups, because they still have to be pushed through. Also finds the biggest
        one, which is where price is most likely to pause: a natural first stop and
        partial take-profit, not only an obstacle.
        """
        lo, hi = (target, price) if target < price else (price, target)
        road = [(p, s) for p, s in (self.last_ladder or []) if lo < p < hi]
        if not road:
            return {"doors_between": 0, "lots_between": 0.0,
                    "biggest_in_path": None, "road_clear": True}
        big = max(road, key=lambda x: x[1])
        return {
            "doors_between": len(road),
            "lots_between": round(sum(s for _, s in road), 1),
            "biggest_in_path": {"price": big[0], "size": round(big[1], 1)},
            "road_clear": False,
        }

    def shelter_behind(self, price: float, going_up: bool) -> Optional[Dict[str, Any]]:
        """Who has our back? The nearest solid level on the OPPOSITE side.

        Going up, that is the biggest level just below us: if price is shoved back we
        bump into it and stop. That is where the stop-loss belongs, and it is also
        where a tired move can rest and wait for help.
        """
        side = [(p, s) for p, s in (self.last_ladder or [])
                if (p < price if going_up else p > price)]
        if not side:
            return None
        near = sorted(side, key=lambda x: abs(x[0] - price))[:8]
        if not near:
            return None
        best = max(near, key=lambda x: x[1])
        return {"price": best[0], "size": round(best[1], 1),
                "distance": round(abs(best[0] - price), 2)}

    def vacuum(self) -> Dict[str, Any]:
        """Is the corridor emptying or filling? -> the road_vacuum judge's reading."""
        h = self.band_history
        if len(h) < 4:
            return {"state": "unknown", "now": (h[-1] if h else 0.0), "change_pct": 0.0}
        now = h[-1]
        then = statistics.median(h[-4:-1])          # the last three cycles
        if then <= 0:
            return {"state": "unknown", "now": now, "change_pct": 0.0}
        chg = (now - then) / then * 100.0
        state = ("EMPTYING" if chg <= -25 else
                 "FILLING" if chg >= 25 else "steady")
        return {"state": state, "now": round(now, 1), "was": round(then, 1),
                "change_pct": round(chg, 1)}

    def _empty(self, why) -> Dict[str, Any]:
        return {"targets": [], "above": [], "below": [], "best_above": None,
                "best_below": None, "n": 0, "tracked": len(self.levels),
                "cycles": self.cycles, "banned_spoof": 0, "why": why, "context": {}}


# one notebook for the life of the process - the memory IS the feature
_BOOK: Optional[LevelBook] = None


def get_book() -> LevelBook:
    global _BOOK
    if _BOOK is None:
        _BOOK = LevelBook()
    return _BOOK


def build_signal_map(level3: Any, price: float, atr: float, config: Any = None,
                     order_flow: Any = None, spread: float = 0.0) -> Dict[str, Any]:
    """Update the scout's notebook with this cycle and hand back the map."""
    return get_book().ingest(level3, price, atr, config, order_flow, spread)


def describe(smap: Dict[str, Any]) -> str:
    """One line for the log, so the map is visible without opening a report."""
    if not smap or not smap.get("n"):
        return "SIGNAL MAP: no trusted door in range"

    def one(t, name):
        return (f"{name} {t['price']:.2f} ({t['size']:.0f} lots, {t['dist_atr']:.2f} ATR, "
                f"{t['trend']}, seen {t['cycles_seen']}x, "
                f"reach {t['attraction']:.2f} hold {t['resistance']:.2f})")

    bits = []
    if smap.get("best_above"):
        bits.append(one(smap["best_above"], "ceiling"))
    if smap.get("best_below"):
        bits.append(one(smap["best_below"], "floor"))
    vac = smap.get("road_vacuum") or {}
    tail = ""
    if vac.get("state") == "EMPTYING":
        tail += (f" | ROAD EMPTYING {vac.get('was')} -> {vac.get('now')} lots "
                 f"({vac.get('change_pct'):+.0f}%) - price can run")
    elif vac.get("state") == "FILLING":
        tail += (f" | road filling {vac.get('was')} -> {vac.get('now')} lots "
                 f"({vac.get('change_pct'):+.0f}%) - getting harder")
    tail += f" | tracking {smap.get('tracked', 0)}"
    if smap.get("atr_floored"):
        tail += (f" | ATR {smap.get('atr_raw')} too small, floored to "
                 f"{smap.get('atr')} so the map does not go blind")
    if smap.get("banned_spoof"):
        tail += f", {smap['banned_spoof']} spoof banned"
    return "SIGNAL MAP: " + " | ".join(bits) + tail
