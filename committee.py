#!/usr/bin/env python3
"""THE THREE-GATE COMMITTEE - standalone, purely additive (STRATEGY Entry 4, Code Day 1).

ELI5: three doors before the shooter.
  Gate 1 reads the town's mood (bell or crowd)  - always talks, never votes.
  Gate 2 measures where price stands on the ladder - a ruler, not a ballot.
  Gate 3 is the ONLY ballot box: 18 chosen eyes collapse into 7 desk-voices,
     two verdict laws sit ON TOP of the tally, and a GO must pass seven
     fire-laws before a ticket exists.

House laws implemented here (HOLY_BOOK.md, STRATEGY, Entry 4 - G1 modes/roster,
G2 ladder/ranking/audit, G3 members/audit/signing, G3-only voting law, G3 fire-laws):
  * NOTHING deleted: this module imports no engine state at load time and only
    CONSUMES what the engine already computes (judges, ATR, rungs, session levels).
  * M3 frame: every distance in ATR(M3) units - yard 0.75, candidate cutoff 2.0.
  * One duck, one chair: rungs within MERGE_ATR fuse; the strongest kind names it.
  * G1 silence is FORBIDDEN (card always rendered); G3 silence (UNDECIDED) is SAFE.
  * Fail-closed: any dead sensor => UNDECIDED => no ticket.
  * Stale eyes => the verdict may speak, the ticket stays in the pocket (L3Q law).
  * One jump per question: ticket id = symbol:cycle_id:G3, deduped by a ledger.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

COMMITTEE_VERSION = "2026-11-12.day1"

# ---- frame constants (all ATR-relative; absolutes are only sanity caps) ----
YARD_ATR = 0.75            # G2: standing on a rung means price within 0.75*ATR(M3)
CUTOFF_ATR = 2.0           # G2: only rungs within 2 ATR are candidates at all
MERGE_ATR = 0.05           # G2: one-duck fuse radius
FORTRESS_RADIUS_ATR = 0.5  # G2: >=2 anchors inside this fuses the gold star
DESK_VOICE_MIN = 0.4       # G3: |weighted sum| below this => the desk says nothing
FRESH_MAX_SECONDS = 15.0   # fire-law (1) fresh eyes (Book receipts: 18.6s ages seen)
NEWS_DEADBAND = 0.15       # Book #12-sealed crier floor: |news| below => no vote
FLAT_LAKE_ER = 0.22        # G1 guard: ER below this FORCES hard BELLY
MAX_SPREAD_PCT = 0.010     # fire-law (4) look-both-ways: sanity cap, tuned at rules-time
TOLL_CLOSES = 2            # verdict law 1: 2 consecutive M3 closes beyond = moved in
TOLL_HOLD_MIN = 5.0        # verdict law 1: or 5+ minutes holding with volume building

# ---- G3: the closed club, exactly as pinned in "GATE 3 JUDGE SIGNING" ----
G3_DESKS: Dict[str, Tuple[str, ...]] = {
    "delta":     ("footprint_delta", "l3_net_flow", "delta_pressure",
                  "l3_large_ofi", "cvd_momentum"),
    "absorb":    ("absorption", "l3_aggr_limit"),
    "iceberg":   ("iceberg", "iceberg_legacy", "iceberg_noise"),
    "tired":     ("cvd_divergence",),
    "creak":     ("l3_ofi_streak", "l3_imbalance"),
    "scarecrow": ("spoof_invert", "spoof_invert_loose"),
    "tilt":      ("microprice",),
}
G3_TOLL_EYE = "footprint_levels"   # breadth witness for verdict law 1 (the toll)
G3_TRAP_EYE = "sweep"              # snap-back friend (stop pool just harvested)
G3_ALL_EYES = sorted(
    {j for members in G3_DESKS.values() for j in members} | {G3_TOLL_EYE, G3_TRAP_EYE}
)

# G2 rung kinds by tier (MASTER RANKING page); strongest kind names a fused rung
KIND_PRIORITY = ("walls", "pdh", "pdl", "va_prior", "vwap_bands", "naked_poc",
                 "settlement", "day_open", "onh", "onl", "supply", "demand",
                 "va_dev", "fvg", "avwap")
HARD_KINDS = frozenset({"walls", "pdh", "pdl", "va_prior"})
LEARNER_KINDS = frozenset({"fvg", "avwap"})   # full members with report-check tags


def kind_tier(kind: str) -> str:
    if kind in HARD_KINDS:
        return "hard"
    if kind in LEARNER_KINDS:
        return "learner"
    return "soft"


# ---------------------------------------------------------------- Gate 1 ----
def evaluate_mood(sigs: Dict[str, Any], prev: Optional[Dict[str, Any]],
                  cycle_id: int) -> Dict[str, Any]:
    """G1 = a BROADCASTER, never a judge. Inputs are the engine's existing
    witnesses; every key optional - missing data means the witness abstains,
    the card STILL goes out (silence is forbidden)."""
    votes: List[Tuple[str, float]] = []          # (mood, weight)
    lean = 0.0
    regime = str(sigs.get("regime") or "")
    if regime == "RANGE":
        votes.append(("BELLY", 1.0))
    elif regime == "TREND":
        votes.append(("WALK", 1.0))
    elif regime == "UNKNOWN":            # an explicit shrug votes; absence abstains
        votes.append(("SLEEP", 0.3))
    er = sigs.get("er")
    forced_belly = er is not None and float(er) < FLAT_LAKE_ER   # flat-lake guard
    if forced_belly:
        votes.append(("BELLY", 0.8))
    if sigs.get("vr_chop"):
        votes.append(("SLEEP", 0.5))
    slope = int(sigs.get("slope") or 0)
    if slope:
        votes.append(("WALK", 0.6))
        lean += float(slope) * 0.6
    if sigs.get("migration"):                                       # trio, compare-form
        votes.append(("WALK", 0.6))
    fuel = float(sigs.get("fuel_roc") or 0.0)
    fuel_dir = int(sigs.get("fuel_dir") or 0)
    if fuel > 0 and fuel_dir:
        votes.append(("WALK", 0.4))
        lean += float(fuel_dir) * 0.4
    elif fuel < -10:
        votes.append(("SLEEP", 0.2))
    news = float(sigs.get("news") or 0.0)
    if abs(news) >= NEWS_DEADBAND:                                  # the FLIPPER
        votes.append(("WALK", 0.6))
        lean += float(news)
    for w in (sigs.get("weather") or {}).values():                  # four winds
        w = int(w or 0)
        if w:
            votes.append(("WALK", 0.25))
            lean += float(w) * 0.25
    oyva = str(sigs.get("open_vs_yva") or "")
    if oyva.startswith("outside"):
        votes.append(("WALK", 0.3))
        lean += 0.3 if oyva.endswith("up") else -0.3
    elif oyva == "inside":
        votes.append(("BELLY", 0.3))
    ib_ratio = float(sigs.get("ib_ratio") or 0.0)
    if ib_ratio >= 2.0:
        votes.append(("WALK", 0.3))
    elif 0 < ib_ratio <= 0.8:
        votes.append(("SLEEP", 0.2))
    totals = {"BELLY": 0.0, "WALK": 0.0, "SLEEP": 0.0}
    for mood, w in votes:
        totals[mood] += w
    total = sum(totals.values())
    best = "SLEEP" if total == 0 else max(
        totals, key=lambda m: (totals[m], m == "BELLY"))           # tie => BALANCE bias
    if forced_belly:
        best = "BELLY"                                              # guard outranks
    share = (totals[best] / total) if total > 0 else 0.0
    certainty = int(min(100, 100 * share * min(1.0, total)))
    prev_mood = (prev or {}).get("mood")
    if prev_mood is None:
        moving = "SETTLING"
        since = 0
    elif prev_mood != best:
        moving = "FLIPPING"
        since = 0
    else:
        since = int((prev or {}).get("since", 0)) + 1
        moving = "FIRM" if certainty >= max(70, int(prev.get("certainty", 0))) else "SETTLING"
    lean_word = "UP" if lean > 0.5 else ("DOWN" if lean < -0.5 else "FLAT")
    card = (f"MOOD={best} | CERTAINTY={certainty} | MOVING={moving} "
            f"| SINCE={since} | LEAN={lean_word}")
    return {"mood": best, "certainty": certainty, "moving": moving,
            "since": since, "lean": lean_word, "card": card,
            "abstained": [n for n in ("regime", "er", "slope", "news")
                          if sigs.get(n) is None or sigs.get(n) == ""]}


# ---------------------------------------------------------------- Gate 2 ----
@dataclass
class Rung:
    name: str
    price: float
    kind: str
    accepted: int = 0          # ACCEPTED stamp (classify_market_trick, history side)
    uob: int = 0               # unfinished-business magnet star (draw-time, owner catch)
    touches: int = 0           # freshness: times tested TODAY only
    anchors_near: int = 0      # fortress: anchor kinds within 0.5*ATR
    note: str = ""

    @property
    def tier(self) -> str:
        return kind_tier(self.kind)


def build_rungs(raw_levels: Iterable[Dict[str, Any]], atr: float) -> List[Rung]:
    """One duck, one chair: fuse rungs inside MERGE_ATR*ATR; the strongest kind
    (tier, then KIND_PRIORITY) names the fused rung; stamps merge by OR/max."""
    atr = max(float(atr or 0.0), 1e-9)
    rad = max(atr * MERGE_ATR, 1e-9)
    buckets: List[List[Rung]] = []
    usable = []
    for lv in raw_levels:
        try:
            float(lv["price"])
        except (TypeError, KeyError, ValueError):
            continue                     # an incomplete level is skipped, never guessed
        usable.append(lv)
    for lv in sorted(usable, key=lambda d: float(d["price"])):
        r = Rung(name=str(lv.get("name") or lv.get("kind") or "rung"),
                 price=float(lv["price"]), kind=str(lv.get("kind") or "vwap_bands"),
                 accepted=int(lv.get("accepted") or 0), uob=int(lv.get("uob") or 0),
                 touches=int(lv.get("touches") or 0),
                 anchors_near=int(lv.get("anchors_near") or 0),
                 note=str(lv.get("note") or ""))
        if buckets and abs(r.price - buckets[-1][-1].price) <= rad:
            buckets[-1].append(r)
        else:
            buckets.append([r])
    out: List[Rung] = []
    for group in buckets:
        group.sort(key=lambda g: (-_rank(g), KIND_PRIORITY.index(g.kind)
                                  if g.kind in KIND_PRIORITY else 99))
        lead = group[0]
        lead.price = sum(g.price for g in group) / len(group)
        lead.accepted = max(g.accepted for g in group)
        lead.uob = max(g.uob for g in group)
        lead.touches = max(g.touches for g in group)
        lead.anchors_near = max(lead.anchors_near,
                                sum(1 for g in group if g.kind in HARD_KINDS))
        if len(group) > 1:
            lead.note = (lead.note + f" fused[{len(group)}]").strip()
        out.append(lead)
    return out


def _rank(r: Rung) -> int:
    return {"hard": 2, "learner": 1, "soft": 0}[r.tier]


def evaluate_location(price: float, atr: float, rungs: List[Rung],
                      mood: str = "") -> Dict[str, Any]:
    """G2 pass is GEOMETRIC: standing within YARD_ATR*ATR of a rung names it and
    hands its card forward. Re-asked every M3 cycle - a WAIT costs nothing."""
    atr = max(float(atr or 0.0), 1e-9)
    cands = sorted(rungs, key=lambda r: abs(r.price - price))
    near = [r for r in cands if abs(r.price - price) <= CUTOFF_ATR * atr]
    standing = next((r for r in near if abs(r.price - price) <= YARD_ATR * atr), None)
    ok = standing is not None
    size_hint = 1.0
    sleep_note = ""
    if ok and standing is not None and mood == "SLEEP":
        # Q3 middle path: SLEEP closes the committee EXCEPT fortress wall + ACCEPTED
        fortress = standing.kind == "walls" and standing.anchors_near >= 2
        if fortress and standing.accepted == 1:
            size_hint = 0.5
            sleep_note = " SLEEP-EXCEPTION(half)"
        else:
            ok, standing, sleep_note = False, None, " SLEEP-LOCK"
    if ok and standing is not None:
        q = "ok"
        if (standing.tier == "hard" or standing.anchors_near >= 2) and standing.touches <= 1:
            q = "strong"
        if standing.touches >= 3:
            q = "weak"
        if standing.uob == 1 and q == "ok":
            q = "strong"                                   # magnet star earns it
        card = (f"LOCATION={standing.name} | DIST_ATR={abs(standing.price - price)/atr:.2f} "
                f"| QUALITY={q} | SOFT={0 if standing.tier == 'hard' else 1} "
                f"| ACCEPTED={standing.accepted} | TIER={standing.tier}"
                f" | CANDS={len(near)}{sleep_note}")
    else:
        card = f"LOCATION=NONE | DIST_ATR=9.99 | QUALITY=weak | SOFT=1 | ACCEPTED=0{sleep_note}"
    return {"pass": ok, "rung": standing, "dist_atr":
            (abs(standing.price - price) / atr) if standing else 9.99,
            "size_hint": size_hint, "candidates": near, "card": card}


# ---------------------------------------------------------------- Gate 3 ----
def judges_from_notes(notes: List[str]) -> List[Dict[str, Any]]:
    """Consume the engine's OWN parse (judge_panel); never re-regex anything.
    Import is lazy and fail-safe: no engine here => empty ballot => UNDECIDED."""
    try:
        import judge_panel  # type: ignore
        return list(judge_panel.parse_judge_panel(list(notes or [])) or [])
    except Exception:
        return []


def desk_voices(judges: List[Dict[str, Any]]) -> Dict[str, int]:
    """Desk-collapse rule: ONE DESK = ONE VOTE (the independence law - sibling
    judges merge, they never double-count)."""
    by_name: Dict[str, Tuple[int, float]] = {}
    for jd in judges:
        name = str(jd.get("judge") or "")
        if name in G3_ALL_EYES and name not in by_name:
            by_name[name] = (int(jd.get("dir") or 0), float(jd.get("weight") or 0.0))
    out: Dict[str, int] = {}
    for desk, members in G3_DESKS.items():
        net = sum(d * w for m in members if m in by_name
                  for (d, w) in (by_name[m],))
        out[desk] = 1 if net >= DESK_VOICE_MIN else (-1 if net <= -DESK_VOICE_MIN else 0)
    breadth_name = G3_TOLL_EYE
    out["breadth"] = by_name.get(breadth_name, (0, 0.0))[0]
    out["trap_eye"] = by_name.get(G3_TRAP_EYE, (0, 0.0))[0]
    return out


def toll_paid(toll: Dict[str, Any]) -> bool:
    """Verdict law 1 (the toll): BREAK may not be spoken until acceptance -
    2 consecutive closes beyond OR 5+ min held with volume building beyond."""
    return bool(toll.get("closes_beyond", 0) >= TOLL_CLOSES
                or (float(toll.get("holds_minutes", 0.0)) >= TOLL_HOLD_MIN
                    and toll.get("volume_building")))


def evaluate_fight(rung: Optional[Rung], judges: List[Dict[str, Any]],
                   toll: Optional[Dict[str, Any]] = None,
                   trap: Optional[Dict[str, Any]] = None,
                   l3q: str = "ok", oi: str = "",
                   effort: Optional[float] = None) -> Dict[str, Any]:
    """G3 = the only ballot box. Inputs: the fused rung card from G2, the parsed
    judge panel (the 18 eyes), the toll state, the snap-back stamp, feed quality."""
    toll = dict(toll or {})
    trap = dict(trap or {})
    if rung is None:
        return {"verdict": "UNDECIDED", "dir": 0,
                "card": "RUNG=NONE | VERDICT=UNDECIDED (no ladder answer to fight over)"}
    v = desk_voices(judges)
    d = v["delta"] or v["creak"] or v["tilt"]
    paid = toll_paid(toll)
    snapback = bool(trap.get("snapback"))
    if d == 0:
        verdict = "UNDECIDED"
    else:
        brk = (1 if v["creak"] == d else 0) + (1 if v["tilt"] == d else 0) \
            + (1 if v["iceberg"] == d else 0) + (1 if v["breadth"] == d else 0)
        bon = (1 if v["absorb"] == -d else 0) + (1 if v["tired"] == -d else 0) \
            + (1 if v["scarecrow"] == -d else 0) + (1 if v["iceberg"] == -d else 0)
        if paid and brk >= 3:
            verdict = "BREAK"
        elif (snapback or bon >= 2) and not paid:
            verdict = "BOUNCE"
        else:
            verdict = "UNDECIDED"
    desks = " ".join(f"{k}{'' if v[k] == 0 else ('+' if v[k] > 0 else '-')}"
                     for k in ("delta", "absorb", "iceberg", "tired",
                               "creak", "scarecrow", "tilt"))
    card = (f"RUNG={rung.name} | PUSH={'UP' if d > 0 else ('DOWN' if d < 0 else 'NONE')} "
            f"| DESKS {desks} | TOLL={'paid' if paid else 'unpaid'} "
            f"| EXCESS=c{int(toll.get('closes_beyond', 0))}/h{float(toll.get('holds_minutes', 0.0)):.0f}m "
            f"| EFFORT={'n/a' if effort is None else f'{effort:.2f}'} | OI={oi or 'BLIND'} "
            f"| SNAPBACK={'1' if snapback else '0'} | L3Q={l3q} | VERDICT={verdict}")
    return {"verdict": verdict, "dir": d, "desks": v, "toll_paid": paid, "card": card}


# ------------------------------------------------------------- fire-laws ----
class TicketLedger:
    """Fire-law (2): one jump per question. A (symbol, cycle, gate) ticket is
    issued at most once; a retry after a network timeout can never double-fire."""

    def __init__(self) -> None:
        self._seen: set = set()

    def issue(self, symbol: str, cycle_id: Any, gate: str = "G3") -> Optional[str]:
        key = f"{symbol}:{cycle_id}:{gate}"
        if key in self._seen:
            return None
        self._seen.add(key)
        return key

    def seen(self) -> int:
        return len(self._seen)


def attempt_fire(verdict: Dict[str, Any], rung: Optional[Rung], pre: Dict[str, Any],
                 ledger: TicketLedger) -> Dict[str, Any]:
    """Fire a G3 GO through the seven fire-laws + the L3Q law. Every check reads
    a plain field of `pre` so the executor can feed real values at wiring time.
    A verdict can exist while the ticket stays in the pocket (that is the point)."""
    fails: List[str] = []
    if verdict.get("verdict") not in ("BOUNCE", "BREAK"):
        return {"allow": False, "ticket": None, "fails": ["NO-VERDICT"]}
    age = float(pre.get("age_s", 9e9))
    if not (age >= 0 and age <= FRESH_MAX_SECONDS):
        fails.append("FRESH-EYES")                               # law 1
    if not pre.get("bar_closed"):
        fails.append("DECIDE-AT-THE-CLOSE")                      # law 7
    if not pre.get("sensors_ok", False):
        fails.append("FAIL-CLOSED")                              # law 3
    if str(verdict.get("card", "")).find("L3Q=ok") < 0:
        fails.append("L3Q")                                      # quality BEFORE trigger
    spread_pct = float(pre.get("spread_pct", 9e9))
    if spread_pct > MAX_SPREAD_PCT:
        fails.append("LOOK-BOTH-WAYS:spread")                    # law 4
    if pre.get("news_blackout"):
        fails.append("LOOK-BOTH-WAYS:news")
    if int(pre.get("position_count", 0)) >= int(pre.get("max_positions", 1)):
        fails.append("LOOK-BOTH-WAYS:count")
    if not pre.get("daily_loss_ok", False):
        fails.append("LOOK-BOTH-WAYS:loss")
    if pre.get("kill_switch"):
        fails.append("KILL-SWITCH")                              # law 5
    if not callable(pre.get("diary")):
        fails.append("DIARY")                                    # law 6
    ticket = ledger.issue(str(pre.get("symbol", "?")), pre.get("cycle_id"), "G3") \
        if not fails else None
    if not fails and ticket is None:
        fails.append("ONE-JUMP")                                 # law 2
    if ticket is not None:
        try:
            pre["diary"]({"ticket": ticket, "verdict": verdict["verdict"],
                          "rung": rung.name if rung else "", "card": verdict["card"]})
        except Exception:
            pass
    return {"allow": bool(ticket), "ticket": ticket, "fails": fails}


# ------------------------------------------------------------- the chain ----
def committee_evaluate(cyc: Dict[str, Any], ledger: TicketLedger) -> Dict[str, Any]:
    """One M3 cycle of the whole committee: G1 -> G2 -> G3 -> fire-laws.
    Fail-closed wrapper: any exception still RENDERS the cards (G1 must not go
    silent) and NEVER a ticket."""
    out: Dict[str, Any] = {"version": COMMITTEE_VERSION}
    try:
        g1 = evaluate_mood(dict(cyc.get("sigs") or {}), cyc.get("prev"),
                           int(cyc.get("cycle_id") or 0))
        out["g1"] = g1["card"]
        rungs = build_rungs(cyc.get("levels") or [], float(cyc.get("atr") or 1.0))
        g2 = evaluate_location(float(cyc["price"]), float(cyc["atr"]), rungs,
                               mood=g1["mood"])
        out["g2"] = g2["card"]
        if not g2["pass"]:
            out["final"] = {"allow": False, "ticket": None, "fails": ["G2-VETO"]}
            out["g3"] = "SKIPPED (no rung under the feet - waiting is the answer)"
            return out
        judges = cyc.get("judges")
        if judges is None:
            judges = judges_from_notes(list(cyc.get("notes") or []))
        g3 = evaluate_fight(g2["rung"], list(judges), cyc.get("toll"),
                            cyc.get("trap"), l3q=str(cyc.get("l3q") or "ok"),
                            oi=str(cyc.get("oi") or ""), effort=cyc.get("effort"))
        out["g3"] = g3["card"]
        pre = dict(cyc.get("prefire") or {})
        pre.setdefault("symbol", str(cyc.get("symbol", "?")))
        pre.setdefault("cycle_id", int(cyc.get("cycle_id") or 0))
        out["final"] = attempt_fire(g3, g2["rung"], pre, ledger)
        return out
    except Exception as exc:                                      # fail-closed
        out.setdefault("g1", "MOOD=SLEEP | CERTAINTY=0 | MOVING=FLIPPING | SINCE=0 | LEAN=FLAT")
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["final"] = {"allow": False, "ticket": None, "fails": ["COMMITTEE-ERROR"]}
        return out
