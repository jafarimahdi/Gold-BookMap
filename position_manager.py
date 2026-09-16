"""
position_manager.py
===================
v4 — TRADE MANAGEMENT (the robot no longer "opens and forgets")

WHY THIS EXISTS
---------------
Before v4 the robot placed SL/TP as blind ATR multiples (1.5x / 3.0x) at
entry and then never touched them again. Step 5 only *reported* open
positions. Two problems:

  1. ATR stops ignore WHERE the big players actually sit. A stop that
     lands just above a round number or just inside an order block gets
     "hunted" (price spikes through it, then reverses). A take-profit
     placed past a wall of resting liquidity never fills because price
     reverses AT the wall.
  2. Every 60s cycle Step 2 produces a fresh read of flow, structure and
     score — and the open position ignored all of it.

WHAT THIS MODULE DOES
---------------------
A) ENTRY-TIME STRUCTURAL STOPS  (used by step4)
   `compute_structural_stops()` replaces the blind ATR multiples:
     - SL is placed BEYOND the nearest demand/supply zone (order block),
       the POC, or a strong round number — plus a buffer — so a normal
       liquidity hunt does not take us out. ATR floor/cap keep the risk
       distance sane (this is what fixes "SL too far").
     - TP is placed just IN FRONT of the nearest opposing structure
       (supply zone / POC / VAH / strong round) so we exit where the
       big traders start banking profit, not after.
   All zone levels come from the futures feed (Step 2) and are scaled to
   the CFD trade price before use (futures 4350 != CFD 4350 exactly).

B) OPEN-POSITION MANAGEMENT  (runs every loop cycle)
   `PositionManager.manage(snapshot)` adopts every bot position (magic
   234000 — manual trades are never touched), including positions that
   were opened BEFORE the manager existed (restart-safe), and applies:

     ADOPT_TIGHTEN  first time it sees a position whose SL is wider than
                    PM_MAX_SL_ATR x ATR it tightens it to the structural
                    stop (fixes far-SL positions opened by older code).
     BE             at +PM_BE_TRIGGER_R x initial risk the SL moves to
                    entry +- spread buffer (trade is now free).
     TRAIL          SL trails behind the fresh structure (nearest demand/
                    supply zone / POC) — it only ever TIGHTENS, never
                    widens, with a cooldown between modifications.
     TP_UPDATE      TP front-runs newly-formed opposing structure. It may
                    move closer any time, and further away only once the
                    trade is break-even (a locked trade may run).
     FLIP_EXIT      the composite signal flips HARD against the position
                    (>= PM_FLIP_EXIT_SCORE) AND order flow confirms
                    (delta/pressure against) -> close early.
     DIVERGENCE_EXIT CVD diverges against the position while it is not yet
                    in decent profit -> momentum is failing -> close.
     TIME_STOP      position is older than PM_TIME_STOP_MINUTES and has
                    moved less than PM_TIME_STOP_MIN_PROGRESS of the way
                    to TP -> dead trade, free the margin/mind.

C) v4.1 IN-TRADE INTELLIGENCE (the five tools, used as a pro would)
   HTF POC        the 1h/4h volume magnets (Step 2 now attaches
                   snapshot.htf_poc) join the anchor pool: POC of a big
                   timeframe behind you = institutional floor; ahead of
                   you = magnet/target.
   FOOTPRINT      stacked-imbalance clusters are extracted live from the
   ZONES           footprint's per-price buy/sell volumes and act as
                   fresh demand/supply zones (3:1 one-sided volume over
                   >= 3 consecutive prices, with real volume behind it).
   FLOW HEALTH     the manager keeps a rolling CVD/price history across
                   cycles and classifies the move: price moving while CVD
                   disagrees = "aggressors exhausted" -> the break-even
                   trigger drops from 1.0R to 0.5R (protect early). The
                   classification + the "next step guess" is logged and
                   journaled with every action.
   VWAP REGIME     trend day (z >= +0.5 for a long): VWAP is the trail
                   anchor — SL trails behind VWAP (institutions defend
                   their average). Stretched (z >= 2.0): lock half the
                   open gain (mean-reversion risk). Range day: VWAP
                   becomes a TP magnet — the target front-runs it.

D) v4.2 THE RISK PERIMETER (what no stop-loss can protect you from)
   NEWS_PROTECT   a HIGH-impact event (CPI / NFP / FOMC) minutes away:
                   profitable positions lock their gains; in "flatten"
                   mode everything closes before the release. Losers keep
                   their structural stop (tightening a stop into pre-news
                   noise just feeds the hunt).
   SESSION_FLATTEN at PM_DAILY_FLATTEN_UTC (default 21:30) every bot
                   position closes BEFORE the XAUUSD CFD daily break —
                   a gap can jump straight OVER a stop, so the only real
                   protection is being flat. Also covers the weekend.
   SPREAD GUARD   SL/TP edits and non-urgent closes are postponed while
                   the CFD spread exceeds PM_ACTION_MAX_SPREAD_PCT —
                   never donate a spiked spread (news seconds). Urgent
                   closes (flatten) still go through.
   FLOW MEMORY    the CVD/price history survives restarts (pm_state.json),
                   so flow health no longer needs a warm-up after a
                   restart.

E) v4.3 PROFIT PROTECTION (from live observation 2026-09-15)
   PROFIT_LOCK    a profit ratchet: once the open gain reaches
                   PM_PROFIT_LOCK_MIN_ATR x ATR, the SL never gives back
                   more than PM_PROFIT_GIVEBACK (50%) of the BEST gain
                   seen. A winner can pull back half way to the lock, but
                   no further — the trade closes in profit, not at entry.
   MOMENTUM_EXIT  when a PROFITABLE position's momentum visibly rolls
                   over — flow health says "flow against the position"
                   AND the composite signal turns against it (>=
                   PM_MOMENTUM_SIGNAL, softer than the 55 flip exit) —
                   the position closes AT MARKET immediately. No waiting
                   for the TP to be touched and no giving profit back to
                   a trailing stop.
   ATR LADDER     (step 2) on a fresh/shallow bridge file the M1 ATR is
                   now estimated from the mean M1 range or the tick
                   range instead of collapsing to 0 — so entry stops,
                   buffers and every distance-based rule keep their TRUE
                   scale right after an NT restart.

Every action (and the reason for it) is journaled to
data/management_log.csv so you can audit exactly why the robot did what
it did — same philosophy as decisions_log.csv.

SAFETY
------
- SL can only move in the favourable direction. Never widened. Ever.
- Broker minimum stop distance (trade_stops_level) respected on every
  modification; invalid distances are skipped, not forced.
- One modification per position per PM_MODIFY_COOLDOWN_SECONDS.
- Manual positions (different magic) are never touched.
- Runs only when PM_ENABLE=1 AND TRADING_ENABLED=1 AND MT5 is reachable.
- Any internal error is logged and swallowed — management must never
  break the analysis pipeline.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None

import config
import trade_history

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
STATE_PATH = DATA_DIR / "pm_state.json"
JOURNAL_PATH = DATA_DIR / "management_log.csv"

PM_MAGIC = 234000          # must match step4.MT5Executor.magic

_MGMT_LOG_FIELDS = [
    "timestamp", "ticket", "side", "rule", "action", "price", "profit",
    "r_multiple", "old_sl", "new_sl", "old_tp", "new_tp", "reason",
]


# --------------------------------------------------------------------------- #
#  Small helpers
# --------------------------------------------------------------------------- #

def _round_tier(price: float) -> float:
    """Round-number strength of a price (mirrors step2._round_level_tier).

    x00 -> 1.0, x50 -> 0.8, x25/x75 -> 0.6, x10-multiples -> 0.4, else 0.
    """
    m = abs(price) % 100.0
    for centre, tier in ((0.0, 1.0), (50.0, 0.8), (25.0, 0.6), (75.0, 0.6)):
        if abs(m - centre) < 1e-9:
            return tier
    if abs(m % 10.0) < 1e-9:
        return 0.4
    return 0.0


def _nearest_strong_round(price: float, below: bool) -> Optional[float]:
    """Nearest x00/x50 round level strictly below/above `price`."""
    step = 50.0
    level = (int(price // step)) * step
    if not below:
        level += step
    for _ in range(40):                     # 40 * 50 = 2000 units of search
        if below and level < price - 1e-9 and _round_tier(level) >= 0.8:
            return level
        if not below and level > price + 1e-9 and _round_tier(level) >= 0.8:
            return level
        level += -step if below else step
    return None


def _parse_event_dt(value) -> Optional[datetime]:
    """Parse an economic-event date (ISO or 'YYYY-MM-DD HH:MM:SS') to UTC."""
    s = str(value or "").strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _footprint_clusters(footprint, ratio: Optional[float] = None,
                        min_run: Optional[int] = None) -> List[Dict]:
    """v4.1: extract stacked-imbalance zones from the footprint.

    The footprint holds per-price buy/sell volumes. A run of consecutive
    prices where one side dominates by >= PM_FP_IMB_RATIO (default 3:1)
    is a stacked imbalance — the "bruise" left by one-sided aggression,
    which tends to act as fresh support (bull cluster) or resistance
    (bear cluster). Only clusters with real volume behind them count.
    """
    if footprint is None:
        return []
    ratio = config.PM_FP_IMB_RATIO if ratio is None else ratio
    min_run = config.PM_FP_MIN_RUN if min_run is None else min_run
    levels = getattr(footprint, "price_levels", None) or {}
    if len(levels) < min_run:
        return []
    buckets = sorted(levels)
    gaps = [b - a for a, b in zip(buckets, buckets[1:]) if b > a]
    res = min(gaps) if gaps else 0.1
    avg_vol = (sum(levels[b].get("buy", 0.0) + levels[b].get("sell", 0.0)
                   for b in buckets) / len(buckets)) or 1.0

    clusters: List[Dict] = []
    run: List[Tuple[float, str]] = []

    def _flush():
        if len(run) >= min_run:
            vol = sum(levels[b].get("buy" if s == "bull" else "sell", 0.0)
                      for b, s in run)
            if vol >= avg_vol * min_run:      # real volume, not noise
                clusters.append({
                    "side": run[0][1],
                    "bottom": min(b for b, _ in run) - res,
                    "top": max(b for b, _ in run) + res,
                    "volume": vol})
        run.clear()

    for b in buckets:
        buy = float(levels[b].get("buy", 0.0) or 0.0)
        sell = float(levels[b].get("sell", 0.0) or 0.0)
        side = None
        if buy > 0 and buy >= ratio * max(sell, 1e-9):
            side = "bull"
        elif sell > 0 and sell >= ratio * max(buy, 1e-9):
            side = "bear"
        if side is None:
            _flush()
            continue
        if run and (run[-1][1] != side or b - run[-1][0] > 3.0 * res):
            _flush()
        run.append((b, side))
    _flush()
    return clusters


def _snapshot_zones(snapshot) -> Dict[str, List[Tuple[float, str]]]:
    """Collect structural levels from the snapshot (FUTURES price scale).

    v4.1: every level now carries its SOURCE, and the pool is richer:
      - order-block supply/demand zones (swing structure)
      - footprint stacked-imbalance clusters (fresh one-sided flow)
      - session POC / VAH / VAL + H1 / H4 POC (volume magnets)
      - VWAP — only on RANGE days, where it behaves as a magnet
    Returns {"demand_bottoms": [(price, src)], "demand_tops": [...],
             "supply_bottoms": [...], "supply_tops": [...],
             "vps": [(price, src)]}. Lists sorted by price.
    """
    demand_b: List[Tuple[float, str]] = []
    demand_t: List[Tuple[float, str]] = []
    supply_b: List[Tuple[float, str]] = []
    supply_t: List[Tuple[float, str]] = []
    vps: List[Tuple[float, str]] = []

    for z in list(getattr(snapshot, "order_blocks", None) or []):
        try:
            top = float(z.get("top", 0.0))
            bottom = float(z.get("bottom", 0.0))
        except (AttributeError, ValueError, TypeError):
            continue
        if top <= 0 or bottom <= 0:
            continue
        if z.get("type") == "demand":
            demand_b.append((bottom, "order block"))
            demand_t.append((top, "order block"))
        elif z.get("type") == "supply":
            supply_b.append((bottom, "order block"))
            supply_t.append((top, "order block"))

    # v4.1: footprint stacked-imbalance clusters as fresh zones
    if getattr(config, "PM_FP_ZONES_ENABLE", True):
        for c in _footprint_clusters(getattr(snapshot, "footprint", None)):
            if c["side"] == "bull":
                demand_b.append((c["bottom"], "footprint imbalance"))
                demand_t.append((c["top"], "footprint imbalance"))
            else:
                supply_b.append((c["bottom"], "footprint imbalance"))
                supply_t.append((c["top"], "footprint imbalance"))

    vp = getattr(snapshot, "volume_profile", None)
    for key, src in (("poc", "session POC"), ("value_area_high", "VAH"),
                     ("value_area_low", "VAL")):
        val = float(getattr(vp, key, 0.0) or 0.0)
        if val > 0:
            vps.append((val, src))

    # v4.1: higher-timeframe POC ("POC in the bigger candles")
    if getattr(config, "PM_HTF_POC_ENABLE", True):
        for tf in ("H4", "H1"):
            val = float((getattr(snapshot, "htf_poc", None) or {}).get(tf, 0.0)
                        or 0.0)
            if val > 0:
                vps.append((val, f"{tf} POC"))

    # v4.1: on RANGE days VWAP behaves as a magnet -> target/anchor.
    # On TREND days it is handled by the dedicated VWAP trail rule instead.
    vwap = float(getattr(vp, "vwap", 0.0) or 0.0)
    if vwap > 0 and str(getattr(snapshot, "regime", "") or "") == "RANGE":
        vps.append((vwap, "VWAP (range day)"))

    out = {"demand_bottoms": sorted(demand_b), "demand_tops": sorted(demand_t),
           "supply_bottoms": sorted(supply_b), "supply_tops": sorted(supply_t),
           "vps": sorted(vps)}
    return out


# --------------------------------------------------------------------------- #
#  A) ENTRY-TIME STRUCTURAL STOPS  (used by step4 at order placement)
# --------------------------------------------------------------------------- #

def compute_structural_stops(action: str, price: float, atr: float,
                             snapshot, news_state: str = "QUIET"
                             ) -> Tuple[float, float, List[str]]:
    """Structural SL/TP for a new trade. Returns (sl, tp, notes).

    `price` and `atr` must already be on the CFD trade scale (step4
    re-anchors them). Zone levels inside `snapshot` are on the futures
    scale and are converted here. Falls back to plain ATR multiples
    when no structure exists.
    """
    notes: List[str] = []
    atr = max(atr, price * 0.0005)           # never divide by ~0
    src_price = float(getattr(snapshot, "price", 0.0) or 0.0)
    scale = (price / src_price) if src_price > 0 else 1.0
    z = _snapshot_zones(snapshot)
    # futures levels -> CFD scale (v4.1: each level carries its source)
    demand = sorted((v * scale, src) for v, src in z["demand_bottoms"])
    demand_tops = sorted((v * scale, src) for v, src in z["demand_tops"])
    supply = sorted((v * scale, src) for v, src in z["supply_bottoms"])
    supply_tops = sorted((v * scale, src) for v, src in z["supply_tops"])
    vps = sorted((v * scale, src) for v, src in z["vps"])

    sl_mult = config.STOP_LOSS_ATR_MULT
    if news_state == "WARNING":
        sl_mult *= config.NEWS_WIDEN_STOP_MULT
    tp_mult = config.TAKE_PROFIT_ATR_MULT

    buf = config.PM_ZONE_BUFFER_ATR * atr            # beyond-zone buffer
    hunt = config.PM_ROUND_HUNT_BUFFER_ATR * atr     # round-number hunt buffer
    min_sl = config.PM_MIN_SL_ATR * atr
    max_sl = config.PM_MAX_SL_ATR * atr * (
        config.NEWS_WIDEN_STOP_MULT if news_state == "WARNING" else 1.0)
    min_tp = config.PM_TP_MIN_ATR * atr
    max_tp = config.PM_TP_MAX_ATR * atr

    is_buy = action == "BUY"
    sign = 1.0 if is_buy else -1.0

    # ---- stop loss: behind the nearest structure on the invalidation side --
    sl = price - sign * sl_mult * atr               # ATR fallback
    # priority: order-block zone > POC/VA edge > strong round number
    struct_level = None
    struct_name = ""
    if is_buy:
        cands = [(v, src) for v, src in demand if v < price - 0.1 * atr]
        if cands:
            struct_level, src = cands[-1]
            struct_name = f"demand zone bottom ({src})"
        else:
            cands = [(v, src) for v, src in vps if v < price - 0.5 * atr]
            if cands:
                struct_level, src = cands[-1]
                struct_name = f"volume node ({src})"
            else:
                r = _nearest_strong_round(price, below=True)
                if r is not None:
                    struct_level, struct_name = r, "round number"
    else:
        cands = [(v, src) for v, src in supply_tops if v > price + 0.1 * atr]
        if cands:
            struct_level, src = cands[0]
            struct_name = f"supply zone top ({src})"
        else:
            cands = [(v, src) for v, src in vps if v > price + 0.5 * atr]
            if cands:
                struct_level, src = cands[0]
                struct_name = f"volume node ({src})"
            else:
                r = _nearest_strong_round(price, below=False)
                if r is not None:
                    struct_level, struct_name = r, "round number"

    # use the structure only if it sits inside the sane-risk window; a zone
    # or round number half the world away is not structure for THIS trade
    if struct_level is not None and abs(price - struct_level) - buf <= max_sl:
        sl = struct_level - sign * buf
        notes.append(f"SL behind {struct_name} {struct_level:.2f} "
                     f"(buffer {buf:.2f})")
    else:
        sl = price - sign * sl_mult * atr
        notes.append(f"SL ATR fallback ({sl_mult:.2f}xATR)")

    # clamp distance to [min_sl, max_sl]
    dist = abs(price - sl)
    if dist < min_sl:
        sl = price - sign * min_sl
        notes.append(f"SL widened to floor {config.PM_MIN_SL_ATR:.2f}xATR")
        dist = min_sl
    elif dist > max_sl:
        sl = price - sign * max_sl
        notes.append(f"SL capped at {config.PM_MAX_SL_ATR:.2f}xATR "
                     f"(structure too far)")
        dist = max_sl

    # never park the stop just above/below a strong round number — that is
    # exactly where a liquidity hunt reaches. Push it behind the hunt.
    # For BUY: a round just BELOW the SL is the hunt target (price dips to
    # it). For SELL: a round just ABOVE the SL.
    r = _nearest_strong_round(sl, below=is_buy)
    if r is not None:
        gap = (sl - r) if is_buy else (r - sl)
        if 0 < gap < hunt:
            new_sl = r - sign * hunt
            # keep the floor respected after the push
            if abs(price - new_sl) >= min_sl:
                sl = new_sl
                notes.append(f"SL moved behind round {r:.2f} (hunt-avoid)")

    # ---- take profit: in front of the nearest opposing structure ----------
    tp = price + sign * tp_mult * atr               # ATR fallback
    opposing: List[Tuple[float, str]] = []
    if is_buy:
        opposing += [(v, f"supply zone ({src})") for v, src in supply
                     if v > price + 0.5 * atr]
        opposing += [(v, f"volume node ({src})") for v, src in vps
                     if v > price + 0.5 * atr]
        rr = _nearest_strong_round(price, below=False)
        if rr is not None and rr > price + 0.5 * atr:
            opposing.append((rr, "round number"))
    else:
        # mirrored: TP front-runs the demand zone's near (top) edge
        opposing += [(v, f"demand zone ({src})") for v, src in demand_tops
                     if v < price - 0.5 * atr]
        opposing += [(v, f"volume node ({src})") for v, src in vps
                     if v < price - 0.5 * atr]
        rr = _nearest_strong_round(price, below=True)
        if rr is not None and rr < price - 0.5 * atr:
            opposing.append((rr, "round number"))
    if opposing:
        # nearest opposing level (works for both sides)
        lvl, name = min(opposing, key=lambda t: abs(t[0] - price))
        tp = lvl - sign * config.PM_TP_BUFFER_ATR * atr
        notes.append(f"TP front-runs {name} {lvl:.2f}")

    tp_dist = abs(tp - price)
    if tp_dist < min_tp:
        tp = price + sign * min_tp
        notes.append(f"TP widened to floor {config.PM_TP_MIN_ATR:.2f}xATR")
    elif tp_dist > max_tp:
        tp = price + sign * max_tp
        notes.append(f"TP capped at {config.PM_TP_MAX_ATR:.2f}xATR")

    return float(sl), float(tp), notes


# --------------------------------------------------------------------------- #
#  B) OPEN-POSITION MANAGEMENT
# --------------------------------------------------------------------------- #

class PositionManager:
    """Manages the bot's open positions every pipeline cycle."""

    def __init__(self, symbol: Optional[str] = None, mt5_module=None):
        self.symbol = symbol or config.MT5_SYMBOL or config.SYMBOL
        self.magic = PM_MAGIC
        self._mt5 = mt5_module if mt5_module is not None else mt5
        self._state: Dict[str, dict] = {}
        # v4.1: rolling (timestamp, cvd, mid) history across cycles — this is
        # what lets the manager judge whether CVD agrees with the price move.
        # In-memory only: after a restart it needs ~3 cycles to warm up.
        self._hist: List[Tuple[float, float, float]] = []
        self._load_state()

    # ---------------- state persistence ---------------- #
    def _load_state(self) -> None:
        try:
            if STATE_PATH.exists():
                raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    # v4.2: the "_flow" key holds the CVD/price history so
                    # flow health survives a restart without a warm-up
                    flow = raw.pop("_flow", [])
                    self._state = raw
                    if isinstance(flow, list):
                        self._hist = [tuple(h) for h in flow
                                      if isinstance(h, (list, tuple))
                                      and len(h) == 3][-15:]
        except (OSError, ValueError):
            self._state = {}

    def _save_state(self) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            data = dict(self._state)
            data["_flow"] = [list(h) for h in self._hist[-15:]]
            STATE_PATH.write_text(json.dumps(data, indent=1),
                                  encoding="utf-8")
        except OSError as exc:
            logger.warning("PM: could not save state: %s", exc)

    def _prune_state(self, live_tickets: set) -> None:
        stale = [t for t in self._state
                 if t != "_flow" and t not in live_tickets]
        if stale:
            for t in stale:
                self._record_gone(t, self._state.get(t, {}))
                self._state.pop(t, None)
            self._save_state()

    def _record_gone(self, ticket: str, st: dict) -> None:
        """v4.4: a known position disappeared between cycles — almost
        always its SL or TP fired at the broker. Try MT5's deal history
        first (exact PnL); fall back to the last observed state. This is
        what makes LOSS MEMORY complete (a stop-out IS a loss)."""
        try:
            side = str(st.get("side", "") or "")
            vol = float(st.get("last_volume", 0.0) or 0.0)
            gain = float(st.get("last_gain_pts", 0.0) or 0.0)
            entry = float(st.get("entry", 0.0) or 0.0)
            last_sl = float(st.get("last_sl", 0.0) or 0.0)
            pnl_usd = None
            reason = "GONE (SL/TP hit or manual close)"
            try:
                now_dt = datetime.now(timezone.utc)
                from_dt = datetime.fromtimestamp(
                    now_dt.timestamp() - 48 * 3600.0, tz=timezone.utc)
                deals = self._mt5.history_deals_get(
                    from_dt, now_dt, position=int(ticket))
                if deals:
                    pnl_usd = sum(float(getattr(d, "profit", 0.0) or 0.0)
                                  for d in deals)
                    for d in reversed(deals):
                        c = str(getattr(d, "comment", "") or "")
                        if c:
                            reason = f"GONE (broker: {c[:24]})"
                            break
            except Exception:
                pass
            if pnl_usd is None and entry > 0 and last_sl > 0 and gain < 0:
                # no history available: if we last saw the position LOSING,
                # assume the stop fired (loss memory must not miss these)
                sl_gain = (last_sl - entry) if side == "BUY" else (entry - last_sl)
                gain = min(gain, sl_gain)
            trade_history.record_close(ticket, side, 0.0, reason,
                                       gain_pts=gain, volume=vol,
                                       estimated=True, pnl_usd=pnl_usd)
        except Exception:
            logger.exception("PM: gone-position record failed (non-fatal)")

    # ---------------- journal ---------------- #
    @staticmethod
    def _journal(row: dict) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            new_file = not JOURNAL_PATH.exists() or \
                JOURNAL_PATH.stat().st_size == 0
            row = {k: row.get(k, "") for k in _MGMT_LOG_FIELDS}
            # v4.4: stamp the row here so no caller can forget it
            if not row.get("timestamp"):
                row["timestamp"] = datetime.now(timezone.utc).strftime(
                    "%Y-%m-%d %H:%M:%S")
            with open(JOURNAL_PATH, "a", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=_MGMT_LOG_FIELDS)
                if new_file:
                    writer.writeheader()
                writer.writerow(row)
            logger.info("PM: journaled -> %s", JOURNAL_PATH)
        except OSError as exc:
            logger.warning("PM: could not write journal: %s", exc)

    # ---------------- MT5 plumbing ---------------- #
    def _bot_positions(self) -> List:
        positions = self._mt5.positions_get(symbol=self.symbol)
        if positions is None:
            raise RuntimeError(
                f"positions_get failed: {self._mt5.last_error()}")
        return [p for p in positions
                if int(getattr(p, "magic", -1) or -1) == self.magic]

    def _min_stop_distance(self, info) -> float:
        point = float(getattr(info, "point", 0.0) or 0.0)
        stops = float(getattr(info, "trade_stops_level", 0.0) or 0.0)
        return point * stops

    # ---------------- v4.2 risk perimeter ---------------- #
    def _news_threat(self, snapshot) -> Tuple[float, str]:
        """(minutes, title) of the next HIGH-impact event inside the
        protect window — scanned from the snapshot's event list directly,
        so MEDIUM/LOW events never trigger it."""
        if not getattr(config, "PM_NEWS_PROTECT_ENABLE", True):
            return 0.0, ""
        window = float(getattr(config, "PM_NEWS_PROTECT_MINUTES", 10.0))
        events = list(getattr(getattr(snapshot, "news", None),
                              "upcoming_events", None) or [])
        now = datetime.now(timezone.utc)
        best_mins, best_title = 0.0, ""
        for e in events:
            if str(e.get("impact", "")).upper() != "HIGH":
                continue
            dt = _parse_event_dt(e.get("date"))
            if dt is None:
                continue
            mins = (dt - now).total_seconds() / 60.0
            if 0 < mins <= window and (best_mins == 0.0 or mins < best_mins):
                best_mins, best_title = mins, str(
                    e.get("title", "") or "HIGH-impact event")
        return best_mins, best_title

    def _session_flatten_due(self, now: Optional[datetime] = None) -> str:
        """Reason string when the daily flatten time (UTC) has passed."""
        cfg_time = str(getattr(config, "PM_DAILY_FLATTEN_UTC", "") or "").strip()
        if not cfg_time:
            return ""
        try:
            hh, mm = (int(x) for x in cfg_time.split(":")[:2])
        except ValueError:
            return ""
        now = now or datetime.now(timezone.utc)
        flatten_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now >= flatten_at:
            return (f"daily flatten {cfg_time} UTC — closing before the "
                    f"CFD break (a gap can jump over a stop)")
        return ""

    def _base_ctx(self, position, tick, info) -> dict:
        """Minimal context for flatten closes (no state needed)."""
        is_buy = getattr(position, "type", None) == \
            getattr(self._mt5, "POSITION_TYPE_BUY", 0)
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        return {"side": "BUY" if is_buy else "SELL",
                "price": bid if is_buy else ask,
                "profit": float(getattr(position, "profit", 0.0) or 0.0),
                "r_now": 0.0,
                "cur_sl": float(getattr(position, "sl", 0.0) or 0.0),
                "cur_tp": float(getattr(position, "tp", 0.0) or 0.0),
                "tick": tick, "info": info}

    @staticmethod
    def _spread_pct(ctx: dict) -> float:
        tick = ctx.get("tick")
        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        mid = (bid + ask) / 2.0
        return (ask - bid) / mid * 100.0 if (bid > 0 and ask > 0 and mid > 0) \
            else 0.0

    def _modify_stops(self, position, new_sl: float, new_tp: float,
                      rule: str, reason: str, ctx: dict) -> bool:
        """Send TRADE_ACTION_SLTP for one position. Returns True on success."""
        # v4.2: never donate the spread — SL/TP edits can always wait a
        # minute; during a news-spike spread they would only churn
        spread_pct = self._spread_pct(ctx)
        if spread_pct > config.PM_ACTION_MAX_SPREAD_PCT:
            logger.info("PM: postponed %s — CFD spread %.3f%% too wide",
                        rule, spread_pct)
            return False
        request = {
            "action": self._mt5.TRADE_ACTION_SLTP,
            "symbol": self.symbol,
            "position": int(getattr(position, "ticket", 0)),
            "sl": float(new_sl),
            "tp": float(new_tp),
        }
        result = self._mt5.order_send(request)
        done = getattr(self._mt5, "TRADE_RETCODE_DONE", 10009)
        if result is None or getattr(result, "retcode", None) != done:
            logger.warning("PM: SLTP modify failed ticket=%s retcode=%s",
                           getattr(position, "ticket", "?"),
                           getattr(result, "retcode", None))
            return False
        st = self._state.setdefault(str(getattr(position, "ticket", 0)), {})
        st["last_modify_ts"] = time.time()
        self._save_state()
        self._journal({"ticket": getattr(position, "ticket", ""),
                       "side": ctx.get("side", ""), "rule": rule,
                       "action": "MODIFY", "price": round(ctx.get("price", 0.0), 2),
                       "profit": round(ctx.get("profit", 0.0), 2),
                       "r_multiple": round(ctx.get("r_now", 0.0), 2),
                       "old_sl": round(ctx.get("cur_sl", 0.0), 2),
                       "new_sl": round(new_sl, 2),
                       "old_tp": round(ctx.get("cur_tp", 0.0), 2),
                       "new_tp": round(new_tp, 2),
                       "reason": reason})
        logger.info("PM: %s ticket=%s SL %.2f -> %.2f, TP %.2f -> %.2f (%s)",
                    rule, getattr(position, "ticket", "?"),
                    ctx.get("cur_sl", 0.0), new_sl,
                    ctx.get("cur_tp", 0.0), new_tp, reason)
        return True

    def _partial_close(self, position, fraction: float, rule: str,
                       reason: str, ctx: dict) -> bool:
        """v4.4: close a FRACTION of a position at market, keep the rest.

        Returns True when the partial fill happened. Refuses when the
        position cannot be split cleanly (either half would be below the
        broker minimum lot) — 0.01-lot positions simply skip this rule.
        """
        if not 0.0 < float(fraction or 0.0) < 1.0:
            return False
        spread_pct = self._spread_pct(ctx)
        if spread_pct > config.PM_ACTION_MAX_SPREAD_PCT:
            logger.info("PM: postponed PARTIAL — spread %.3f%% too wide",
                        spread_pct)
            return False
        tick = ctx.get("tick")
        info = ctx.get("info")
        pos_type = getattr(position, "type", None)
        is_buy = pos_type == getattr(self._mt5, "POSITION_TYPE_BUY", 0)
        price = (getattr(tick, "bid", 0.0) if is_buy
                 else getattr(tick, "ask", 0.0))
        volume = float(getattr(position, "volume", 0.0) or 0.0)
        ticket = getattr(position, "ticket", None)
        if not ticket or volume <= 0 or not price:
            return False
        step = float(getattr(info, "volume_step", 0.0) or 0.0) or 0.01
        min_lot = float(getattr(info, "volume_min", 0.0) or 0.0) or 0.01
        half = int(volume * fraction / step) * step      # round DOWN
        half = round(half, 2)
        if half < min_lot or (volume - half) < min_lot:
            logger.info("PM: PARTIAL skipped — %s lots cannot be split "
                        "(min %s, step %s)", volume, min_lot, step)
            return False
        request = {
            "action": self._mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": half,
            "type": (getattr(self._mt5, "ORDER_TYPE_SELL", 1) if is_buy
                     else getattr(self._mt5, "ORDER_TYPE_BUY", 0)),
            "position": int(ticket),
            "price": price,
            "deviation": 20,
            "magic": self.magic,
            "comment": f"gold-bot pm: {rule}"[:31],
            "type_time": getattr(self._mt5, "ORDER_TIME_GTC", 0),
            "type_filling": (self._select_filling(info) if info else
                             getattr(self._mt5, "ORDER_FILLING_IOC", 1)),
        }
        result = self._mt5.order_send(request)
        done = getattr(self._mt5, "TRADE_RETCODE_DONE", 10009)
        if result is None or getattr(result, "retcode", None) != done:
            logger.warning("PM: partial close failed ticket=%s retcode=%s",
                           ticket, getattr(result, "retcode", None))
            return False
        self._journal({"ticket": ticket, "side": ctx.get("side", ""),
                       "rule": rule, "action": "PARTIAL",
                       "price": round(price, 2),
                       "profit": round(ctx.get("profit", 0.0), 2),
                       "r_multiple": round(ctx.get("r_now", 0.0), 2),
                       "old_sl": round(ctx.get("cur_sl", 0.0), 2),
                       "new_sl": "", "old_tp": round(ctx.get("cur_tp", 0.0), 2),
                       "new_tp": "",
                       "reason": reason})
        try:
            trade_history.record_tca(
                "EXIT", ticket, price,
                float(getattr(result, "price", 0.0) or 0.0), half,
                note=rule)
            trade_history.record_close(f"{ticket}:P1", ctx.get("side", ""),
                                       price, "PARTIAL_EXIT",
                                       gain_pts=float(ctx.get("gain_pts",
                                                              0.0) or 0.0),
                                       volume=half)
        except Exception:
            logger.exception("PM: partial memory write failed (non-fatal)")
        logger.info("PM: PARTIAL ticket=%s banked %.2f of %.2f lots @ %.2f "
                    "(%s)", ticket, half, volume, price, reason)
        return True

    def _close(self, position, rule: str, reason: str, ctx: dict,
               urgent: bool = False) -> bool:
        """Close one position at market. Returns True on success.

        v4.2: non-urgent closes are postponed while the CFD spread is
        spiked (news seconds) — never donate the spread. Urgent closes
        (session flatten, news flatten) go through regardless.
        """
        spread_pct = self._spread_pct(ctx)
        if not urgent and spread_pct > config.PM_ACTION_MAX_SPREAD_PCT:
            logger.info("PM: postponed %s close — CFD spread %.3f%% too "
                        "wide (will retry next cycle)", rule, spread_pct)
            return False
        tick = ctx.get("tick")
        pos_type = getattr(position, "type", None)
        is_buy = pos_type == getattr(self._mt5, "POSITION_TYPE_BUY", 0)
        price = (getattr(tick, "bid", 0.0) if is_buy
                 else getattr(tick, "ask", 0.0))
        volume = float(getattr(position, "volume", 0.0) or 0.0)
        ticket = getattr(position, "ticket", None)
        if not ticket or volume <= 0 or not price:
            return False
        info = ctx.get("info")
        request = {
            "action": self._mt5.TRADE_ACTION_DEAL,
            "symbol": self.symbol,
            "volume": volume,
            "type": (getattr(self._mt5, "ORDER_TYPE_SELL", 1) if is_buy
                     else getattr(self._mt5, "ORDER_TYPE_BUY", 0)),
            "position": int(ticket),
            "price": price,
            "deviation": 20,
            "magic": self.magic,
            "comment": f"gold-bot pm: {rule}"[:31],
            "type_time": getattr(self._mt5, "ORDER_TIME_GTC", 0),
            "type_filling": (self._select_filling(info) if info else
                             getattr(self._mt5, "ORDER_FILLING_IOC", 1)),
        }
        result = self._mt5.order_send(request)
        done = getattr(self._mt5, "TRADE_RETCODE_DONE", 10009)
        if result is None or getattr(result, "retcode", None) != done:
            logger.warning("PM: close failed ticket=%s retcode=%s", ticket,
                           getattr(result, "retcode", None))
            return False
        self._state.pop(str(ticket), None)
        self._save_state()
        self._journal({"ticket": ticket, "side": ctx.get("side", ""),
                       "rule": rule, "action": "CLOSE",
                       "price": round(price, 2),
                       "profit": round(ctx.get("profit", 0.0), 2),
                       "r_multiple": round(ctx.get("r_now", 0.0), 2),
                       "old_sl": round(ctx.get("cur_sl", 0.0), 2),
                       "new_sl": "", "old_tp": round(ctx.get("cur_tp", 0.0), 2),
                       "new_tp": "", "reason": reason})
        logger.info("PM: CLOSED ticket=%s (%s: %s)", ticket, rule, reason)
        # v4.4: fill quality (intended vs actual) + close memory record —
        # this feeds loss memory, the day ratchet and later AI review
        try:
            trade_history.record_tca(
                "EXIT", ticket, price,
                float(getattr(result, "price", 0.0) or 0.0), volume,
                note=rule)
            trade_history.record_close(
                ticket, ctx.get("side", ""), price, rule,
                gain_pts=float(ctx.get("gain_pts", 0.0) or 0.0),
                volume=volume)
        except Exception:
            logger.exception("PM: trade memory write failed (non-fatal)")
        return True

    def _select_filling(self, info) -> int:
        raw = int(getattr(info, "filling_mode", 0) or 0)
        if raw & 2:
            return getattr(self._mt5, "ORDER_FILLING_IOC", 1)
        if raw & 1:
            return getattr(self._mt5, "ORDER_FILLING_FOK", 0)
        return getattr(self._mt5, "ORDER_FILLING_RETURN", 2)

    # ---------------- main entry point ---------------- #
    def manage(self, snapshot) -> None:
        """Manage all bot positions using the fresh Step 2 snapshot."""
        if not getattr(config, "PM_ENABLE", True):
            return
        if not getattr(config, "TRADING_ENABLED", True):
            return
        if self._mt5 is None:
            logger.debug("PM: MetaTrader5 SDK not installed -> nothing to "
                         "manage (demo mode).")
            return
        if not config.mt5_initialize(self._mt5):
            logger.error("PM: mt5.initialize() failed")
            return
        try:
            try:
                positions = self._bot_positions()
            except Exception as exc:
                logger.error("PM: position query failed: %s", exc)
                return
            if not positions:
                self._prune_state(set())
                return
            tick = self._mt5.symbol_info_tick(self.symbol)
            info = self._mt5.symbol_info(self.symbol)
            if tick is None or info is None:
                logger.warning("PM: no tick/symbol info — skipping cycle")
                return

            # v4.2: record the flow sample ONCE per cycle (not per position)
            try:
                _mid = (float(getattr(tick, "bid", 0.0) or 0.0) +
                        float(getattr(tick, "ask", 0.0) or 0.0)) / 2.0
                _cvd = float(getattr(getattr(snapshot, "order_flow", None),
                                     "cvd", 0.0) or 0.0)
                if _mid > 0:
                    self._hist.append((time.time(), _cvd, _mid))
                    if len(self._hist) > 15:
                        del self._hist[:-15]
            except Exception:
                pass

            # v4.2: daily session flatten — never hold through the CFD break
            # (a gap can jump straight over a stop; this also covers Friday)
            flatten_reason = self._session_flatten_due()
            if flatten_reason:
                logger.info("PM: %s", flatten_reason)
                for position in positions:
                    try:
                        self._close(position, "SESSION_FLATTEN", flatten_reason,
                                    self._base_ctx(position, tick, info),
                                    urgent=True)
                    except Exception:
                        logger.exception("PM: flatten failed ticket=%s",
                                         getattr(position, "ticket", "?"))
                self._prune_state(set())
                return

            live = set()
            for position in positions:
                try:
                    self._manage_one(position, snapshot, tick, info)
                except Exception:
                    logger.exception("PM: error managing ticket=%s",
                                     getattr(position, "ticket", "?"))
                live.add(str(getattr(position, "ticket", "")))
            self._prune_state(live)
        finally:
            self._mt5.shutdown()

    # ---------------- per-position logic ---------------- #
    def _manage_one(self, position, snapshot, tick, info) -> None:
        pm = self._mt5
        is_buy = getattr(position, "type", None) == \
            getattr(pm, "POSITION_TYPE_BUY", 0)
        side = "BUY" if is_buy else "SELL"
        sign = 1.0 if is_buy else -1.0
        ticket = str(getattr(position, "ticket", ""))
        entry = float(getattr(position, "price_open", 0.0) or 0.0)
        cur_sl = float(getattr(position, "sl", 0.0) or 0.0)
        cur_tp = float(getattr(position, "tp", 0.0) or 0.0)
        profit = float(getattr(position, "profit", 0.0) or 0.0)
        volume = float(getattr(position, "volume", 0.0) or 0.0)

        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        price = bid if is_buy else ask          # exit-side price
        mid = (bid + ask) / 2.0 if (bid and ask) else price
        spread = max(ask - bid, 0.0) if (bid and ask) else 0.0
        min_dist = self._min_stop_distance(info)

        # futures -> CFD scale for everything Step 2 produced
        fut_price = float(getattr(snapshot, "price", 0.0) or 0.0)
        scale = (mid / fut_price) if fut_price > 0 else 1.0
        fut_atr = float(getattr(getattr(snapshot, "volatility", None),
                                "atr", 0.0) or 0.0)
        atr = (fut_atr * scale) if fut_atr > 0 else mid * 0.005

        # ---- v4.1: flow health (CVD vs price across recent cycles) ---------
        # (the sample itself is recorded once per cycle in manage())
        now = time.time()
        of_obj = getattr(snapshot, "order_flow", None)
        cvd_now = float(getattr(of_obj, "cvd", 0.0) or 0.0)
        lookback = max(3, int(getattr(config, "PM_FLOW_LOOKBACK_MINUTES", 5))) * 60
        hist = [h for h in self._hist if h[0] >= now - lookback]
        flow_label = "warming up"
        flow_weak = False
        if len(hist) >= 3:
            cvd_move = cvd_now - hist[0][1]
            price_move = mid - hist[0][2]
            move_fav = (price_move >= 0.1 * atr) if is_buy else \
                       (price_move <= -0.1 * atr)
            cvd_fav = (cvd_move > 0) if is_buy else (cvd_move < 0)
            if move_fav and cvd_fav:
                flow_label = "flow confirms the move"
            elif move_fav and not cvd_fav:
                flow_weak = True
                flow_label = ("price moving but CVD disagrees — aggressors "
                              "exhausted; expect a stall/reversal")
            elif not move_fav and cvd_fav and abs(price_move) < 0.1 * atr:
                flow_label = ("flow pushing but price stalled — passive side "
                              "absorbing; expect a resolution move")
            elif not move_fav and not cvd_fav:
                flow_label = "flow against the position"
            else:
                flow_label = "quiet: price and CVD both flat"

        # ---- v4.1: VWAP regime -----------------------------------------------
        vp_obj = getattr(snapshot, "volume_profile", None)
        vwap_z = float(getattr(vp_obj, "vwap_zscore", 0.0) or 0.0)
        vwap_c = float(getattr(vp_obj, "vwap", 0.0) or 0.0) * scale
        z_fav = vwap_z if is_buy else -vwap_z        # signed toward profit
        vwap_label = "n/a"
        if vwap_c > 0:
            if z_fav >= getattr(config, "PM_VWAP_STRETCH_Z", 2.0):
                vwap_label = (f"STRETCHED (z={vwap_z:+.2f}) — "
                              f"mean-reversion risk")
            elif z_fav >= getattr(config, "PM_VWAP_TREND_Z", 0.5):
                vwap_label = (f"TREND side (z={vwap_z:+.2f}) — "
                              f"VWAP is the trail anchor")
            else:
                vwap_label = f"range-ish (z={vwap_z:+.2f}) — VWAP is a magnet"
        logger.info("PM: ticket=%s | flow: %s | vwap: %s | regime=%s",
                    ticket, flow_label, vwap_label,
                    str(getattr(snapshot, "regime", "") or "?"))

        # ---- adopt / restore state ----------------------------------------
        now = time.time()
        st = self._state.get(ticket)
        adopted = False
        if st is None:
            adopted = True
            st = {"initial_sl": cur_sl if cur_sl > 0 else 0.0,
                  "be_done": False, "adopted_ts": now, "last_modify_ts": 0.0}
            self._state[ticket] = st
            self._save_state()
            logger.info("PM: adopted %s position ticket=%s (entry %.2f, "
                        "SL %.2f, TP %.2f)", side, ticket, entry, cur_sl, cur_tp)
        initial_sl = float(st.get("initial_sl", 0.0) or 0.0)
        if initial_sl <= 0:
            # position had no SL at all — synthesize one for R math
            initial_sl = entry - sign * config.PM_MIN_SL_ATR * atr
            st["initial_sl"] = initial_sl
        initial_r = abs(entry - initial_sl) or (config.PM_MIN_SL_ATR * atr)
        r_now = ((mid - entry) if is_buy else (entry - mid)) / initial_r

        # v4.3: track the best gain this position has reached — the input
        # to the profit ratchet (PROFIT_LOCK)
        open_gain_pts = (mid - entry) if is_buy else (entry - mid)
        best_gain = float(st.get("high_water_pts", 0.0) or 0.0)
        if open_gain_pts > best_gain:
            best_gain = open_gain_pts
            st["high_water_pts"] = best_gain
            self._save_state()

        ctx = {"side": side, "price": price, "profit": profit,
               "r_now": r_now, "cur_sl": cur_sl, "cur_tp": cur_tp,
               "gain_pts": open_gain_pts, "volume": volume,
               "tick": tick, "info": info}

        # v4.4: keep a trail of the last observed facts so a position that
        # vanishes between cycles (SL/TP hit at the broker) can still be
        # recorded in trade memory with a decent estimate
        st["side"] = side
        st["entry"] = float(entry)
        st["last_gain_pts"] = float(open_gain_pts)
        st["last_sl"] = float(cur_sl)
        st["last_tp"] = float(cur_tp)
        st["last_volume"] = float(volume)

        # ---- structural stops from the fresh snapshot ----------------------
        s_sl, s_tp, _notes = compute_structural_stops(
            "BUY" if is_buy else "SELL", price, atr, snapshot)

        # ================= RULES: EXITS FIRST ================= #
        # 0) v4.2 NEWS — a HIGH-impact event is minutes away (flatten mode)
        threat_mins, threat_title = self._news_threat(snapshot)
        if threat_mins and str(getattr(config, "PM_NEWS_PROTECT_MODE",
                                       "tighten")).lower() == "flatten":
            self._close(position, "NEWS_FLATTEN",
                        f"HIGH-impact event '{threat_title}' in "
                        f"{threat_mins:.0f} min — flatten mode", ctx,
                        urgent=True)
            return

        # 1) FLIP_EXIT — signal turned hard against us AND flow agrees
        direction = str(getattr(snapshot, "signal_direction", "") or "")
        strength = float(getattr(snapshot, "signal_strength", 0.0) or 0.0)
        of = getattr(snapshot, "order_flow", None)
        delta = float(getattr(of, "delta", 0.0) or 0.0)
        buy_press = float(getattr(of, "buying_pressure", 50.0) or 50.0)
        flow_against = (delta < 0 or buy_press < 45.0) if is_buy else \
                       (delta > 0 or buy_press > 55.0)
        if (direction == ("SELL" if is_buy else "BUY")
                and strength >= config.PM_FLIP_EXIT_SCORE
                and (flow_against or not config.PM_FLIP_REQUIRE_FLOW)):
            self._close(position, "FLIP_EXIT",
                        f"signal flipped {direction} ({strength:.0f}), "
                        f"flow against={flow_against}", ctx)
            return

        # 2) DIVERGENCE_EXIT — CVD diverges while the trade isn't paid yet
        div = float(getattr(snapshot, "divergence", 0.0) or 0.0)
        if (config.PM_DIVERGENCE_EXIT and div != 0.0
                and ((div < 0 and is_buy) or (div > 0 and not is_buy))
                and r_now < config.PM_DIVERGENCE_MIN_R):
            self._close(position, "DIVERGENCE_EXIT",
                        f"CVD divergence {div:+.0f} against {side} at "
                        f"{r_now:+.2f}R", ctx)
            return

        # 3) TIME_STOP — old and going nowhere
        age_min = (now - float(st.get("adopted_ts", now))) / 60.0
        if cur_tp > 0 and entry > 0:
            progress = ((mid - entry) if is_buy else (entry - mid)) / \
                abs(cur_tp - entry)
        else:
            progress = r_now / 2.0
        if (config.PM_TIME_STOP_MINUTES > 0
                and age_min >= config.PM_TIME_STOP_MINUTES
                and progress < config.PM_TIME_STOP_MIN_PROGRESS):
            self._close(position, "TIME_STOP",
                        f"{age_min:.0f} min old, only {progress*100:.0f}% "
                        f"of the way to TP", ctx)
            return

        # 3b) v4.3 MOMENTUM_EXIT — a PROFITABLE position whose momentum is
        # visibly rolling over closes AT MARKET: flow health confirms the
        # move has turned against us AND the composite signal agrees
        # (softer than the 55 flip exit — this protects profit, and profit
        # deserves a faster trigger than loss-cutting). Armed once the
        # trade has EARNED >= PM_MOMENTUM_MIN_R at its best; fires only
        # while still in profit (losers belong to the stop, not this rule)
        if (getattr(config, "PM_MOMENTUM_EXIT_ENABLE", True)
                and open_gain_pts > 0
                and (best_gain / initial_r) >= config.PM_MOMENTUM_MIN_R
                and flow_label == "flow against the position"
                and direction == ("SELL" if is_buy else "BUY")
                and strength >= config.PM_MOMENTUM_SIGNAL):
            self._close(position, "MOMENTUM_EXIT",
                        f"momentum rolled over: flow against the position "
                        f"+ signal {direction} {strength:.0f} — taking the "
                        f"profit at market instead of waiting for the TP",
                        ctx)
            return

        # 3c) v4.4 PARTIAL EXIT — at +PM_PARTIAL_TRIGGER_R of risk (measured
        # on the CURRENT price), bank half the position at market and let
        # the runner ride with the ratchet. Automatically skipped when the
        # position cannot be split (minimum-lot positions) or already split.
        if (getattr(config, "PM_PARTIAL_EXIT_ENABLE", True)
                and not st.get("partial_done", False)
                and config.PM_PARTIAL_TRIGGER_R > 0
                and r_now >= config.PM_PARTIAL_TRIGGER_R):
            if self._partial_close(position, config.PM_PARTIAL_FRACTION,
                                   "PARTIAL_EXIT",
                                   f"+{r_now:.2f}R reached — banked half, "
                                   f"runner keeps the ratchet", ctx):
                st["partial_done"] = True
                # fall through: the runner still gets this cycle's SL/TP
                # candidates (BE / PROFIT_LOCK protect the remaining half)

        # ================= RULES: SL/TP UPDATES ================= #
        if now - float(st.get("last_modify_ts", 0.0)) < \
                config.PM_MODIFY_COOLDOWN_SECONDS:
            return                                   # respect the cooldown

        min_step = max(0.05 * atr, 2.0 * float(getattr(info, "point", 0.0) or 0.0))

        # -- SL candidates (tighten only; the tightest wins) -------------------
        # v4.1: each rule proposes a stop; the tightest valid proposal wins.
        cands: List[Tuple[float, str, str]] = []      # (level, rule, reason)
        if config.PM_TRAIL_ENABLE and s_sl * sign > cur_sl * sign + min_step:
            cands.append((s_sl, "ADOPT_TIGHTEN" if adopted else "TRAIL",
                          f"structural stop {s_sl:.2f} tighter than SL "
                          f"{cur_sl:.2f}"))
        # v4.1: a move that lacks CVD support protects its gains earlier
        be_trigger = config.PM_BE_TRIGGER_R
        if config.PM_FLOW_ENABLE and flow_weak:
            be_trigger = min(be_trigger, config.PM_FLOW_WEAK_BE_R)
        be_level = entry + sign * max(0.1 * atr, spread, min_dist)
        if r_now >= be_trigger and be_level * sign > cur_sl * sign + min_step:
            be_reason = f"{r_now:+.2f}R reached — stop to break-even"
            if flow_weak:
                be_reason += (" (early: the move lacks CVD support — "
                              "aggressors exhausted)")
            cands.append((be_level, "BE", be_reason))
        # v4.1: trend day -> trail behind VWAP (institutions defend it)
        if (config.PM_VWAP_ENABLE and vwap_c > 0
                and z_fav >= config.PM_VWAP_TREND_Z
                and z_fav < config.PM_VWAP_STRETCH_Z):
            vwap_trail = vwap_c - sign * config.PM_VWAP_TRAIL_BUFFER_ATR * atr
            if vwap_trail * sign > cur_sl * sign + min_step:
                cands.append((vwap_trail, "VWAP_TRAIL",
                              f"trend day: trailing behind VWAP {vwap_c:.2f} "
                              f"(institutions defend their average)"))
        # v4.1: stretched far beyond VWAP -> lock half the open gain
        if config.PM_VWAP_ENABLE and vwap_c > 0 and \
                z_fav >= config.PM_VWAP_STRETCH_Z:
            lock = entry + sign * max(0.1 * atr, abs(mid - entry) * 0.5)
            if lock * sign > cur_sl * sign + min_step:
                cands.append((lock, "VWAP_STRETCH",
                              f"price {vwap_z:+.1f}σ beyond VWAP — stretched; "
                              f"locking half the gain"))
        # v4.2: HIGH-impact event minutes away — protect the gains before
        # the release (losers keep their structural stop: tightening into
        # pre-news noise just feeds the hunt)
        if threat_mins:
            if r_now > 0:
                lock = entry + sign * max(0.1 * atr, spread, min_dist,
                                          abs(mid - entry) * 0.5)
                if lock * sign > cur_sl * sign + min_step:
                    cands.append((lock, "NEWS_PROTECT",
                                  f"HIGH-impact event '{threat_title}' in "
                                  f"{threat_mins:.0f} min — locking gains "
                                  f"before the release"))
            else:
                logger.info("PM: HIGH-impact event '%s' in %.0f min — "
                            "position not in profit; structural SL stays "
                            "the protection", threat_title, threat_mins)
        # v4.3: profit ratchet — never give back more than PM_PROFIT_GIVEBACK
        # of the best gain once the trade has earned >= 1 x ATR
        if getattr(config, "PM_PROFIT_LOCK_ENABLE", True) and best_gain > 0:
            # v4.4 adaptive lock: arm earlier and give back less when the
            # market is a RANGE, or when the macro backdrop fights us
            lock_min_atr = config.PM_PROFIT_LOCK_MIN_ATR
            giveback = config.PM_PROFIT_GIVEBACK
            if getattr(config, "PM_REGIME_ADAPTIVE", True):
                if str(getattr(snapshot, "regime", "") or "").upper() == "RANGE":
                    lock_min_atr = min(lock_min_atr, config.PM_REGIME_LOCK_ATR)
                    giveback = min(giveback, config.PM_REGIME_GIVEBACK)
            if getattr(config, "PM_MACRO_DEFENSE", True):
                pos_sign = 1.0 if is_buy else -1.0
                macro_bias = float(getattr(snapshot, "macro_bias", 0.0) or 0.0)
                if (-pos_sign * macro_bias) >= config.PM_MACRO_OPP_THRESHOLD:
                    lock_min_atr = min(lock_min_atr, config.PM_MACRO_LOCK_ATR)
                    giveback = min(giveback, config.PM_MACRO_GIVEBACK)
            if best_gain >= lock_min_atr * atr:
                lock = entry + sign * best_gain * (1.0 - giveback)
                if lock * sign > cur_sl * sign + min_step:
                    cands.append((lock, "PROFIT_LOCK",
                                  f"profit ratchet: best gain {best_gain:.1f} "
                                  f"pts — never give back more than "
                                  f"{giveback*100:.0f}%"
                                  + (" (adaptive: range/macro defense)" if
                                     giveback < config.PM_PROFIT_GIVEBACK
                                     else "")))
        if cands:
            sl_candidate, rule, reason = max(cands, key=lambda c: c[0] * sign)
        else:
            sl_candidate, rule, reason = cur_sl, "", ""

        # broker distance check for the SL
        sl_ok = True
        if sl_candidate != cur_sl:
            if is_buy and price - sl_candidate < min_dist:
                sl_ok = False
            if not is_buy and sl_candidate - price < min_dist:
                sl_ok = False
        if sl_candidate * sign < cur_sl * sign - 1e-9:
            sl_ok = False                        # would widen — never
        if sl_candidate != cur_sl and abs(sl_candidate - cur_sl) < min_step:
            sl_ok = False                        # change too small to bother

        # -- TP candidate (front-run fresh opposing structure) --
        tp_candidate = cur_tp
        tp_reason = ""
        if s_tp > 0:
            closer = (s_tp * sign < cur_tp * sign) if cur_tp > 0 else True
            further = (s_tp * sign > cur_tp * sign) and st.get("be_done", False)
            big_enough = cur_tp <= 0 or abs(s_tp - cur_tp) >= 0.2 * atr
            if (closer or further) and big_enough:
                tp_candidate = s_tp
                tp_reason = "TP front-runs fresh opposing structure"
        tp_ok = True
        if tp_candidate != cur_tp and tp_candidate > 0:
            if is_buy and tp_candidate - price < min_dist:
                tp_ok = False
            if not is_buy and price - tp_candidate < min_dist:
                tp_ok = False

        new_sl = sl_candidate if sl_ok else cur_sl
        new_tp = tp_candidate if tp_ok else cur_tp
        # The trade is effectively break-even once the stop sits at/beyond
        # the entry buffer — from that moment TP may extend (a locked trade
        # may run). This is true no matter WHICH rule produced the stop.
        if r_now >= config.PM_BE_TRIGGER_R and \
                new_sl * sign >= be_level * sign - 1e-9:
            if not st.get("be_done", False):
                st["be_done"] = True
                self._save_state()
        if new_sl != cur_sl or new_tp != cur_tp:
            combined_rule = rule if rule else "TP_UPDATE"
            combined_reason = " | ".join(
                x for x in (reason, tp_reason) if x) or "structure update"
            self._modify_stops(position, new_sl, new_tp, combined_rule,
                               combined_reason, ctx)
        elif adopted:
            self._journal({"ticket": ticket, "side": side, "rule": "ADOPT",
                           "action": "HOLD", "price": round(price, 2),
                           "profit": round(profit, 2),
                           "r_multiple": round(r_now, 2),
                           "old_sl": round(cur_sl, 2), "new_sl": "",
                           "old_tp": round(cur_tp, 2), "new_tp": "",
                           "reason": "adopted existing position, SL/TP OK"})


_MANAGER: Optional[PositionManager] = None


def get_position_manager() -> PositionManager:
    """Process-wide singleton (keeps per-ticket state in memory)."""
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = PositionManager()
    return _MANAGER
