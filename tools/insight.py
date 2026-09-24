#!/usr/bin/env python3
"""
insight.py -- the LEARNING layer: read a day closely, then read many days together.

Four questions this answers, all from files already on disk:

  1. DECISIONS PAGE   every decision of a day, expandable: what it saw, what each of the
                      11 judges voted, what the tape did next, and what it decided
  2. TRADES PAGE      the money story: which signal opened which position, entry -> exit
                      -> P&L, and honestly "nothing traded" when nothing traded
  3. EXPLAIN HH:MM    one decision, in full, printed to the console (paste-able)
  4. WEIGHT A/B +     "would different judge weights have been better?" across N days,
     WALK-FORWARD     and "learn on days 1..k-1, test on day k" as a rolling check

Scoring rule, stated once and used everywhere in this file: a vote is RIGHT when the
price 15 minutes later moved the way the vote pointed. Same rule for every weight set,
so the comparison is fair. This is a comparison tool, not a profit promise - test 7
(what-if) is the one that prices trades.

No imports from audit_day.py: everything arrives as arguments, so this module can be
tested on its own and can never break the auditor by import order.
"""
from __future__ import annotations

import html
import json
import math
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

# Budapest local time for DISPLAY. ZoneInfo is DST-correct, so 25 Oct 2026 (CEST->CET)
# needs no edit here; the fixed offset below is only a fallback for a box with no tzdata.
try:
    from zoneinfo import ZoneInfo
    BUDA_TZ = ZoneInfo("Europe/Budapest")
except Exception:                                     # pragma: no cover
    BUDA_TZ = timezone(timedelta(hours=2))
BUDA_OFFSET = timedelta(hours=2)          # kept for backward compat; do NOT use for lookups


def _loc(ts):
    """Budapest local time, for SHOWING a stamp to a human.

    Never pass the result to price_at()/excursion(): the price series is keyed in UTC,
    and handing it a shifted stamp reads the tape hours away from the decision. Give
    those functions the original tz-aware UTC value instead.
    """
    return ts.astimezone(BUDA_TZ) if ts else None


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #
def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _fmt(x, nd=1, sign=False):
    try:
        v = float(x)
    except Exception:
        return "-"
    return f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"


def minute_series(ticks):
    """[(dt, price)] one point per minute (last price in that minute), sorted.

    ticks arrive as (dt, price, size) with dt tz-aware.
    """
    per_min = {}
    for t in ticks or []:
        try:
            dt, price = t[0], float(t[1])
        except Exception:
            continue
        key = dt.replace(second=0, microsecond=0)
        per_min[key] = price
    return sorted(per_min.items())


def series_index(series):
    return {dt: i for i, (dt, _p) in enumerate(series or [])}


def price_at(series, when, minutes=0, idx=None):
    """Price `minutes` after `when`, from the minute series. None when out of range."""
    if not series:
        return None
    if idx is None:
        idx = series_index(series)
    key = when.replace(second=0, microsecond=0) + timedelta(minutes=minutes)
    if key in idx:
        return series[idx[key]][1]
    base = when.replace(second=0, microsecond=0) + timedelta(minutes=minutes)
    best, best_d = None, None
    for dt, p in series:
        d = abs((dt - base).total_seconds())
        if best_d is None or d < best_d:
            best, best_d = p, d
    return best if best_d is not None and best_d <= 90 else None


def excursion(series, when, minutes, idx=None):
    """(best, worst) price inside the next `minutes` - the honest 'what happened next'."""
    if not series:
        return None, None
    lo_end = when + timedelta(minutes=minutes)
    vals = [p for dt, p in series if when <= dt <= lo_end]
    return (max(vals), min(vals)) if vals else (None, None)


def candle_atr(candles, when, bars=3):
    """Average true range of the last `bars` candles at `when` (for SL/TP sizing)."""
    if not candles:
        return None
    prev = None
    for c in candles:
        if c[0] > when:
            break
        prev = c
    if prev is None:
        return None
    highs = [c[2] for c in candles if c[0] <= prev[0]][-bars:]
    lows = [c[3] for c in candles if c[0] <= prev[0]][-bars:]
    if not highs or not lows:
        return None
    return max(highs) - min(lows)


# --------------------------------------------------------------------------- #
# 1) the day's decisions, one expandable row each
# --------------------------------------------------------------------------- #
def decisions_page(day, decisions, diary, series, title_extra=""):
    """Self-contained HTML: every decision + the panel that stood behind it."""
    rows = []
    for i, d in enumerate(decisions or []):
        ts = d.get("_dt")
        dt = _loc(ts)                      # display only
        at = ts                            # UTC, for every price lookup below
        price = float(d.get("price") or 0)
        # the diary snapshot that was in front of the robot at that moment
        near, nd = None, None
        for rec in diary or []:
            rdt = rec.get("_dt")
            if not rdt or not ts:
                continue
            gap = abs((rdt - ts).total_seconds())
            if nd is None or gap < nd:
                near, nd = rec, gap
        panel = (near or {}).get("judge_votes") or []
        f5 = price_at(series, at, 5) if at else None
        f15 = price_at(series, at, 15) if at else None
        f30 = price_at(series, at, 30) if at else None
        hi, lo = excursion(series, at, 30) if at else (None, None)
        fav = adv = None
        if price and hi and lo:
            if (d.get("signal_direction") or "").upper() == "BUY":
                fav, adv = hi - price, price - lo
            elif (d.get("signal_direction") or "").upper() == "SELL":
                fav, adv = price - lo, hi - price
        judges = "".join(
            f"<tr><td>{esc(v.get('judge'))}</td>"
            f"<td class={'up' if float(v.get('dir', 0) or 0) > 0 else ('dn' if float(v.get('dir', 0) or 0) < 0 else 'q')}>"
            f"{'BUY' if float(v.get('dir', 0) or 0) > 0 else ('SELL' if float(v.get('dir', 0) or 0) < 0 else 'quiet')}</td>"
            f"<td>{_fmt(v.get('weight'), 2)}</td><td class=raw>{esc(v.get('raw'))}</td></tr>"
            for v in panel if isinstance(v, dict)
        )
        st = str(d.get("exec_status") or "?")
        cls = "ok" if st.upper() == "EXECUTED" else ("bad" if st.upper() in ("DEFERRED", "REJECTED") else "")
        rows.append(f"""
<div class="card">
  <div class="head" onclick="this.parentNode.classList.toggle('open')">
    <span class="t">{dt:%H:%M:%S if dt else '--:--'}</span>
    <span class="pill {cls}">{esc(st)}</span>
    <span class="dir {(d.get('signal_direction') or '').lower()}">{esc(d.get('signal_direction'))}</span>
    <span class="num">price {_fmt(price, 2)}</span>
    <span class="num">strength {_fmt(d.get('signal_strength'))}</span>
    <span class="num">conf {_fmt(d.get('signal_confidence'))}%</span>
    <span class="ai">AI {esc(d.get('ai_action'))} {_fmt(d.get('ai_confidence'))}%</span>
    <span class="next">+15m {_fmt((f15 - price) if (f15 and price) else None, 2, True)}</span>
  </div>
  <div class="body">
    <div class="cols">
      <div><h4>What it saw (diary snapshot {('%+.0fs' % nd) if nd is not None else 'n/a'})</h4>
        <table>
          <tr><td>regime</td><td>{esc((near or {}).get('regime'))}</td></tr>
          <tr><td>killzone</td><td>{esc((near or {}).get('killzone'))}</td></tr>
          <tr><td>diary price</td><td>{_fmt((near or {}).get('price'), 2)}</td></tr>
          <tr><td>teams</td><td>{esc(json.dumps((near or {}).get('team_scores') or {}))}</td></tr>
          <tr><td>strength / conf</td><td>{_fmt((near or {}).get('strength'))} / {_fmt((near or {}).get('confidence'))}%</td></tr>
        </table>
      </div>
      <div><h4>The panel ({len(panel)} votes)</h4>
        <table><tr><th>judge</th><th>vote</th><th>w</th><th>raw</th></tr>{judges or '<tr><td colspan=4>no panel recorded</td></tr>'}</table>
      </div>
      <div><h4>What happened next</h4>
        <table>
          <tr><td>+5 min</td><td>{_fmt(f5, 2)}</td><td>{_fmt((f5 - price) if (f5 and price) else None, 2, True)}</td></tr>
          <tr><td>+15 min</td><td>{_fmt(f15, 2)}</td><td>{_fmt((f15 - price) if (f15 and price) else None, 2, True)}</td></tr>
          <tr><td>+30 min</td><td>{_fmt(f30, 2)}</td><td>{_fmt((f30 - price) if (f30 and price) else None, 2, True)}</td></tr>
          <tr><td>in favour / against</td><td colspan=2>{_fmt(fav, 2)} / {_fmt(adv, 2)}</td></tr>
        </table>
        <p class="why"><b>Why {esc(st)}:</b> {esc(d.get('reason')) or 'no reason written'}</p>
      </div>
    </div>
  </div>
</div>""")
    head = f"{day} - {len(decisions or [])} decisions {title_extra}"
    return _page(f"Decisions — {esc(day)}", head, "".join(rows) or
                 "<p>No decisions were logged for this day.</p>")


# --------------------------------------------------------------------------- #
# 2) the money story: signal -> position -> close
# --------------------------------------------------------------------------- #
def trades_page(day, decisions, outcomes, tracked, mt5=None):
    executed = [d for d in (decisions or []) if str(d.get("exec_status") or "").upper() == "EXECUTED"]
    rows = []
    for o in outcomes or []:
        rows.append(f"<tr><td>{esc(o.get('timestamp'))}</td><td>{esc(o.get('order_id'))}</td>"
                    f"<td>{esc(o.get('side'))}</td><td>{_fmt(o.get('pnl'), 2, True)}</td>"
                    f"<td>{_fmt(o.get('exit_price'), 2)}</td><td>{esc(o.get('comment'))}</td></tr>")
    ids = tracked.get("position_ids") if isinstance(tracked, dict) else None
    body = []
    body.append("<h3>The graded day</h3>")
    body.append(f"<p>{len(executed)} signal(s) reached MT5 that day"
                + (f"; {len(rows)} closed deal(s) logged" if rows else "; no closed deal was logged") + ".</p>")
    if rows:
        body.append("<table><tr><th>closed at</th><th>order</th><th>side</th><th>P&amp;L</th>"
                    "<th>exit</th><th>note</th></tr>" + "".join(rows) + "</table>")
    if executed:
        body.append("<h3>The decision behind each signal</h3><table><tr><th>time</th><th>direction</th>"
                    "<th>conf</th><th>order id</th><th>reason</th></tr>"
                    + "".join(f"<tr><td>{esc(_loc(d.get('_dt')).strftime('%H:%M:%S') if d.get('_dt') else '')}</td>"
                              f"<td>{esc(d.get('signal_direction'))}</td>"
                              f"<td>{_fmt(d.get('signal_confidence'))}%</td>"
                              f"<td>{esc(d.get('order_id'))}</td><td>{esc(d.get('reason'))}</td></tr>"
                              for d in executed) + "</table>")
    else:
        body.append("<p class='note'>Nothing was sent, so there is no lifecycle to show. The signals "
                    "that came close are priced in test 7 (what-if) of the same day's report - that is "
                    "the honest stand-in while the gate stays closed.</p>")
    if ids:
        body.append(f"<h3>Bookkeeping ids (append-only)</h3><p>{esc(ids)} - these are ids the robot "
                    f"remembers to look trades up later; they are NOT a list of open trades.</p>")
    if mt5:
        body.append(f"<h3>MT5 right now</h3><p>{esc(mt5)}</p>")
    return _page(f"Trades — {esc(day)}", f"Position lifecycle, {day}", "".join(body))


# --------------------------------------------------------------------------- #
# 2b) every judge, every call, and the reason it gave
# --------------------------------------------------------------------------- #
# 24l/B2 FIX. This table used to be hand-written here and was WRONG: it held four
# names that are not judges at all (cvd_slope, footprint_imbalance, structure, whale),
# it was missing 16 real judges, and it gave every unrecognised judge 15 minutes -
# htf_poc and the macro judges are graded on 120. The console (audit_day.JUDGE_HORIZON)
# was always right, so there is now exactly ONE table and this page reads it: horizon
# = quick_candles x 5, because the judges are scored on M5 bars.
def _load_judge_clock():
    try:
        import audit_day as _A                      # same folder, read-only import
        h = getattr(_A, "JUDGE_HORIZON", None)
        if isinstance(h, dict) and h:
            return {k: int(v[0]) * 5 for k, v in h.items()}, "audit_day.JUDGE_HORIZON"
    except Exception:
        pass
    # audit_day not importable (page rendered standalone): mirror of the same table,
    # kept in candles so the two can be diffed by eye.
    _mirror = {
        "footprint_delta": 3, "footprint_levels": 3, "l3_imbalance": 3,
        "l3_ofi_streak": 3, "l3_net_flow": 3, "l3_large_ofi": 3,
        "l3_aggr_limit": 3, "microprice": 2, "absorption": 3, "iceberg": 6,
        "iceberg_legacy": 6, "spoof_invert": 6, "spoof_invert_loose": 6,
        "queue_pos": 3, "whale_walls": 6, "sweep": 6, "vwap_trend": 12,
        "vwap_bands": 12, "vwap_zscore": 12, "poc_day": 12, "supply_demand": 12,
        "value_area": 12, "htf_poc": 24, "cvd_divergence": 12, "cvd_momentum": 6,
        "delta_pressure": 6, "volume_roc": 6, "macro_yield": 24, "macro_dxy": 24,
        "macro_vix": 24, "macro_risk": 24, "news_sentiment": 24, "mtf": 12,
    }
    return {k: v * 5 for k, v in _mirror.items()}, "built-in mirror (audit_day not importable)"


JUDGE_CLOCK, JUDGE_CLOCK_SOURCE = _load_judge_clock()
JUDGE_CLOCK_DEFAULT = 15


def judge_clock(name):
    """Minutes this judge is graded over. An unknown name is a bug, not a 15-minute
    judge, so it is reported as such instead of being silently graded."""
    return JUDGE_CLOCK.get(name, JUDGE_CLOCK_DEFAULT)


def judge_clock_note(name):
    return ("" if name in JUDGE_CLOCK else
            f" <b>[unknown judge '{html.escape(str(name))}' - graded on the "
            f"{JUDGE_CLOCK_DEFAULT}-minute default; this name is not in "
            f"{html.escape(JUDGE_CLOCK_SOURCE)}]</b>")


def judges_page(day, diary, series, decisions=None):
    """One page per day: each judge, its scorecard, and every call it made.

    A judge is graded on its OWN clock (see JUDGE_CLOCK): a footprint read is judged on
    the next 15 minutes, a macro read on the next 2 hours. Grading them all on one
    horizon is how a fast judge gets blamed for a slow move.
    """
    idx = series_index(series)
    per = {}
    for rec in diary or []:
        dt = rec.get("_dt")
        price = float(rec.get("price", 0) or 0)
        if not dt or price < 1000:
            continue
        when = dt                          # UTC: price lookups are done against a UTC series
        shown = _loc(dt)                   # Budapest local: what the reader sees
        for v in (rec.get("judge_votes") or []):
            if not isinstance(v, dict):
                continue
            name = str(v.get("judge") or "?")
            try:
                d = float(v.get("dir", 0) or 0)
            except Exception:
                d = 0.0
            mins = judge_clock(name)
            fut = price_at(series, when, mins, idx)   # when is UTC - correct by construction
            move = (fut - price) if (fut is not None) else None
            right = None
            if move is not None and d != 0:
                right = (move > 0) if d > 0 else (move < 0)
            per.setdefault(name, []).append({
                "when": shown, "dir": d, "w": v.get("weight"), "raw": v.get("raw"),
                "price": price, "mins": mins, "move": move, "right": right,
            })

    if not per:
        return _page(f"Judges — {esc(day)}", f"Judges, {day}",
                     "<p>No judge votes were recorded for this day, so there is nothing to "
                     "score. That is a diary problem, not a judge problem.</p>")

    def _stat(calls):
        scored = [c for c in calls if c["right"] is not None]
        hits = sum(1 for c in scored if c["right"])
        pts = sum((c["move"] if c["dir"] > 0 else -c["move"])
                  for c in scored if c["move"] is not None)
        return len(calls), len(scored), hits, pts

    order = sorted(per.items(), key=lambda kv: (-_stat(kv[1])[1], kv[0]))
    total_snaps = len([r for r in (diary or []) if r.get("judge_votes")])

    blocks = []
    for name, calls in order:
        n, scored, hits, pts = _stat(calls)
        acc = (100.0 * hits / scored) if scored else None
        ppc = (pts / scored) if scored else None
        part = (100.0 * n / total_snaps) if total_snaps else 0.0
        thin = scored < 3
        badge = ("<span class='pill bad'>too few calls to grade</span>" if thin else
                 f"<span class='pill {'ok' if (acc or 0) >= 55 else ''}'>{acc:.0f}% right</span>")
        money = ("<span class='pill ok'>makes money</span>" if (ppc or 0) > 0
                 else "<span class='pill bad'>loses money</span>") if scored else ""
        rows = "".join(
            f"<tr><td>{c['when']:%H:%M:%S}</td>"
            f"<td class={'up' if c['dir'] > 0 else ('dn' if c['dir'] < 0 else 'q')}>"
            f"{'BUY' if c['dir'] > 0 else ('SELL' if c['dir'] < 0 else 'quiet')}</td>"
            f"<td>{_fmt(c['w'], 2)}</td>"
            f"<td>{_fmt(c['price'], 2)}</td>"
            f"<td>+{c['mins']}m</td>"
            f"<td>{_fmt(c['move'], 2, True)}</td>"
            f"<td>{'right' if c['right'] else ('wrong' if c['right'] is False else '-')}</td>"
            f"<td class=raw>{esc(c['raw'])}</td></tr>"
            for c in calls)
        blocks.append(f"""
<div class="card">
  <div class="head" onclick="this.parentNode.classList.toggle('open')">
    <span class="t">{esc(name)}</span>
    {badge} {money}
    <span class="num">{n} vote(s)</span>
    <span class="num">{part:.0f}% of snapshots</span>
    <span class="num">scored {scored}</span>
    <span class="num">{_fmt(pts, 1, True)} pts</span>
    <span class="num">{_fmt(ppc, 3, True)} pts/call</span>
  </div>
  <div class="body">
    <p class="why">Graded on its own clock: <b>{judge_clock(name)} minutes</b>{judge_clock_note(name)}
       ahead of each vote (clock source: {html.escape(JUDGE_CLOCK_SOURCE)} - the same
       table the console grades with, so a judge has one score, not two). "right" means price moved the way this judge pointed.
       {"<b>Fewer than 3 scored calls - the percentages here are noise, shown for completeness only.</b>" if thin else ""}</p>
    <table><tr><th>time</th><th>vote</th><th>w</th><th>price</th><th>clock</th>
      <th>move</th><th>verdict</th><th>the reason it wrote</th></tr>{rows}</table>
  </div>
</div>""")

    summary = (f"{len(per)} judge(s) voted across {total_snaps} snapshot(s). "
               f"Click any judge to see every call it made and the reason it gave.")
    return _page(f"Judges — {esc(day)}", f"Judges, {day}",
                 f"<p>{summary}</p>" + "".join(blocks))


# --------------------------------------------------------------------------- #
# 3) one decision, in full, on the console
# --------------------------------------------------------------------------- #
def explain(day, hhmm, decisions, diary, series):
    if not decisions:
        return f"no decisions logged for {day}"
    want = None
    try:
        h, m = hhmm.split(":")
        want = int(h) * 60 + int(m)
    except Exception:
        return f"could not read the time '{hhmm}' - use HH:MM (24h, Budapest)"
    best, best_d = None, None
    for d in decisions:
        ts = d.get("_dt")
        if not ts:
            continue
        loc = _loc(ts)
        dmin = loc.hour * 60 + loc.minute
        gap = abs(dmin - want)
        if best_d is None or gap < best_d:
            best, best_d, best_dt = d, gap, loc
    if best is None:
        return "no decision carries a usable timestamp"
    price = float(best.get("price") or 0)
    near, nd = None, None
    for rec in diary or []:
        rdt = rec.get("_dt")
        if not rdt or not best.get("_dt"):
            continue
        gap = abs((rdt - best["_dt"]).total_seconds())
        if nd is None or gap < nd:
            near, nd = rec, gap
    out = [f"{'=' * 78}", f" WHY — the decision nearest {hhmm} on {day}", f"{'=' * 78}",
           f" time        : {best_dt:%H:%M:%S} Budapest ({nd:+.0f}s from the diary snapshot)" if nd is not None
           else f" time        : {best_dt:%H:%M:%S}",
           f" price       : {_fmt(price, 2)}",
           f" signal      : {best.get('signal_direction')} strength {_fmt(best.get('signal_strength'))} "
           f"confidence {_fmt(best.get('signal_confidence'))}%",
           f" regime      : {best.get('regime')}   news: {best.get('news_state')} "
           f"({best.get('minutes_to_event')} min to {best.get('next_event_title')})",
           f" AI said     : {best.get('ai_action')} {_fmt(best.get('ai_confidence'))}%",
           f" executor    : {best.get('exec_status')} — {best.get('reason')}",
           f" order id    : {best.get('order_id') or '-'}"]
    if near:
        out += ["", " WHAT IT SAW (diary snapshot closest to the decision)"]
        out.append(f"   teams      : {json.dumps(near.get('team_scores') or {})}")
        out.append(f"   killzone   : {near.get('killzone')}   regime: {near.get('regime')}")
        votes = [v for v in (near.get("judge_votes") or []) if isinstance(v, dict)]
        if votes:
            out.append(f"   judges ({len(votes)}):")
            for v in votes:
                dirn = "BUY " if float(v.get("dir", 0) or 0) > 0 else (
                    "SELL" if float(v.get("dir", 0) or 0) < 0 else "quiet")
                out.append(f"     {str(v.get('judge')):<20} {dirn}  w {_fmt(v.get('weight'), 2)}  {v.get('raw')}")
    if best.get("_dt"):
        dt = best["_dt"]                   # UTC: the tape block below is a price lookup
        hi, lo = excursion(series, dt, 30)
        vals = [(-30, price_at(series, dt, -30)), (5, price_at(series, dt, 5)),
                (15, price_at(series, dt, 15)), (30, price_at(series, dt, 30))]
        out += ["", " WHAT HAPPENED NEXT (from the tape)"]
        for mins, p in vals:
            if p:
                out.append(f"   {mins:>+4} min : {_fmt(p, 2)}  ({_fmt(p - price, 2, True)})")
        if hi and lo:
            out.append(f"   30 min range: {_fmt(lo, 2)} .. {_fmt(hi, 2)}")
            if (best.get("signal_direction") or "").upper() == "BUY":
                out.append(f"   in favour   : {_fmt(hi - price, 2)} pts | against: {_fmt(price - lo, 2)} pts")
            elif (best.get("signal_direction") or "").upper() == "SELL":
                out.append(f"   in favour   : {_fmt(price - lo, 2)} pts | against: {_fmt(hi - price, 2)} pts")
    out += ["", " Read it like this: the panel is what the robot actually consulted; the executor line is",
            " why it did or did not act; 'what happened next' is the market's answer, not a prediction.", ""]
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# 4) weight A/B and walk-forward across days
# --------------------------------------------------------------------------- #
def panel_rows(day, diary, series):
    """[(when, price, {judge: dir}, ens_dir, future_move)] for one day.

    future_move = price 15 minutes later minus price now (same rule for every weight set).
    """
    rows = []
    idx = series_index(series)
    for rec in diary or []:
        dt = rec.get("_dt")
        price = float(rec.get("price", 0) or 0)
        if not dt or price < 1000:
            continue
        votes = {}
        for v in (rec.get("judge_votes") or []):
            if isinstance(v, dict):
                try:
                    votes[str(v.get("judge"))] = float(v.get("dir", 0) or 0)
                except Exception:
                    continue
        if not votes:
            continue
        fut = price_at(series, dt, 15, idx)          # dt is tz-aware UTC; series is UTC
        if fut is None:
            continue
        ens = (rec.get("signal_direction") or "NEUTRAL").upper()
        ens_dir = 1 if ens == "BUY" else (-1 if ens == "SELL" else 0)
        rows.append((dt, price, votes, ens_dir, fut - price))
    return rows


def _score(rows, weights):
    """(right, total, points) for one weight set. Quiet = no vote = no trade."""
    right = tot = 0
    pts = 0.0
    for _dt, _price, votes, _ens, move in rows:
        num = den = 0.0
        for j, d in votes.items():
            w = float(weights.get(j, 1.0))
            if d:
                num += w * d
                den += w
        if den <= 0 or not num:
            continue
        tot += 1
        sign = 1 if num > 0 else -1
        if (sign > 0 and move > 0) or (sign < 0 and move < 0):
            right += 1
            pts += abs(move)
        else:
            pts -= abs(move)
    return right, tot, pts


def suggested_weights(rows, days_back=0):
    """Weight each judge by how often it was right - the same idea as test 9, computed
    here across whichever rows are handed in (one day, or the learning window of a fold)."""
    hit = defaultdict(int)
    tot = defaultdict(int)
    for _dt, _price, votes, _ens, move in rows:
        for j, d in votes.items():
            if not d or not move:
                continue
            tot[j] += 1
            if (d > 0 and move > 0) or (d < 0 and move < 0):
                hit[j] += 1
    out = {}
    for j, n in tot.items():
        if n < 20:                      # never invent a weight from a handful of calls
            continue
        acc = hit[j] / n
        out[j] = round(max(0.2, min(2.0, 2 * acc)), 2)
    return out, {j: (hit[j], tot[j]) for j in tot}


def weight_ab(days_data, weights_now=None, weights_new=None, min_n=100):
    """Compare weight sets on the same snapshots. days_data: [(day, rows)]."""
    all_rows = [r for _d, rows in days_data for r in rows]
    if not all_rows:
        return "no diary snapshots with a judge panel and a price 15 minutes later - nothing to compare", {}
    sets = {}
    auto, table = suggested_weights(all_rows)
    sets["current (as used)"] = weights_now or {}
    sets["suggested (from accuracy)"] = weights_new or auto
    equal = {j: 1.0 for j in set(auto) | set((weights_now or {}))}
    sets["equal weight (1.0)"] = equal
    lines = [f"{'=' * 78}", f" WEIGHT A/B — {len(days_data)} day(s), {len(all_rows)} snapshots", f"{'=' * 78}",
             " scoring rule: a vote is right when the price 15 minutes later moved its way.",
             " same snapshots for every weight set, so the comparison is like for like.", ""]
    out = {}
    for name, w in sets.items():
        r, t, p = _score(all_rows, w)
        out[name] = {"right": r, "n": t, "acc": (100.0 * r / t) if t else 0.0, "pts": p}
        lines.append(f"   {name:<26} n {t:>5}  right {r:>5}  = {(100.0 * r / t if t else 0):>5.1f}%"
                     f"   sum of |moves| {p:>+8.1f} pts")
    if auto:
        lines += ["", " the suggested weights and the evidence behind them (min 20 calls):"]
        for j, w in sorted(auto.items(), key=lambda kv: -kv[1]):
            h, n = table[j]
            lines.append(f"   {j:<20} {h}/{n} = {100.0 * h / n:>5.1f}%  -> weight {w}")
        lines.append("")
        lines.append(" Read it like this: if 'suggested' is not clearly ahead of 'current' on the same")
        lines.append(" snapshots, the honest move is to change NOTHING yet - weight chasing on thin")
        lines.append(" data is how a robot gets worse while looking busier.")
    else:
        lines.append(" not enough calls per judge (20+) to suggest anything yet.")
    worst = min(out.values(), key=lambda v: v["n"])["n"] if out else 0
    if worst < min_n:
        lines += ["", f" !! NOT ENOUGH DATA: the smallest set scored {worst} snapshots (want {min_n}+).",
                  "    Treat the numbers above as a hint, not a decision."]
    return "\n".join(lines), out


def walk_forward(days_data, fold=3, weights_now=None):
    """Rolling: learn on `fold` days, test on the next one. Never peeks forward.

    days_data is chronological: [(day, rows)]. Returns (text, summary dict).
    """
    days = [d for d, _r in days_data]
    if len(days_data) < fold + 1:
        return (f"need at least {fold + 1} days with a diary to walk forward "
                f"(have {len(days_data)}) - run more days, then come back"), {"folds": 0}
    lines = [f"{'=' * 78}",
             f" WALK-FORWARD — learn on {fold} day(s), test on the next, rolling", f"{'=' * 78}",
             " no peeking: the test day is scored with weights learned only from the days before it.",
             " scoring rule: the weighted vote is right when the price 15 minutes later moved its way.", ""]
    lines.append(f"   {'test day':<12} {'learn window':<26} {'fixed w.':>10} {'learned w.':>11} {'n':>6} {'verdict':>10}")
    learned_better = 0
    folds = 0
    rows_summary = []
    for i in range(fold, len(days_data)):
        test_day, test_rows = days_data[i]
        learn_rows = [r for _d, rows in days_data[i - fold:i] for r in rows]
        if not test_rows or not learn_rows:
            continue
        w_new, _t = suggested_weights(learn_rows)
        if not w_new:
            continue
        r_fix, n_fix, _p = _score(test_rows, weights_now or {})
        r_new, n_new, _p2 = _score(test_rows, w_new)
        if not n_fix or not n_new:
            continue
        acc_fix = 100.0 * r_fix / n_fix
        acc_new = 100.0 * r_new / n_new
        verdict = "learned +" if acc_new > acc_fix + 1 else ("worse" if acc_new < acc_fix - 1 else "same")
        if verdict == "learned +":
            learned_better += 1
        folds += 1
        rows_summary.append((test_day, acc_fix, acc_new, n_new, verdict))
        lines.append(f"   {str(test_day):<12} {str(days[i - fold]):<11}..{str(days[i - 1]):<12} "
                     f"{acc_fix:>9.1f}% {acc_new:>10.1f}% {n_new:>6} {verdict:>10}")
    if not folds:
        lines.append("   (no fold could be scored: the diary or the tape was missing on those days)")
        return "\n".join(lines), {"folds": 0}
    mean_fix = sum(r[1] for r in rows_summary) / folds
    mean_new = sum(r[2] for r in rows_summary) / folds
    lines += ["", f" folds scored: {folds} | mean accuracy with weights in use {mean_fix:.1f}% "
                  f"| with weights learned from the days before {mean_new:.1f}% "
                  f"| learned better in {learned_better}/{folds} folds"]
    if mean_new > mean_fix + 2 and learned_better >= folds * 0.6:
        lines.append(" VERDICT: learning is helping on this sample. Move the weights, then keep walking "
                     "forward - if the edge stops, it was noise.")
    elif mean_new < mean_fix - 2:
        lines.append(" VERDICT: learning made it WORSE on this sample. Change nothing.")
    else:
        lines.append(" VERDICT: no clear difference yet. That is a real answer: the weights you have "
                     "are not the thing holding the day back.")
    return "\n".join(lines), {"folds": folds, "mean_fixed": mean_fix, "mean_learned": mean_new,
                              "better": learned_better}


# --------------------------------------------------------------------------- #
# page shell (self-contained: no internet, no fonts, no CDN)
# --------------------------------------------------------------------------- #
def _page(title, head, body):
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>{esc(title)}</title>
<style>
:root{{--bg:#0d1117;--fg:#e6edf3;--mut:#8b949e;--line:#21262d;--card:#161b22;
--ok:#3fb950;--warn:#d29922;--bad:#f85149;--up:#3fb950;--dn:#f85149;}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);
font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}}
header{{padding:18px 22px;border-bottom:1px solid var(--line);background:var(--card)}}
h1{{margin:0 0 4px;font-size:19px}}h3{{margin:22px 0 8px;font-size:15px;color:#a5d6ff}}
h4{{margin:0 0 6px;font-size:13px;color:var(--mut);font-weight:600}}
p{{margin:6px 0}}a{{color:#58a6ff}}.sub{{color:var(--mut)}}
.card{{border:1px solid var(--line);border-radius:8px;margin:8px 22px;background:var(--card)}}
.head{{display:flex;gap:14px;align-items:center;padding:9px 12px;cursor:pointer;flex-wrap:wrap}}
.head:hover{{background:#1c2128}}.card .body{{display:none;border-top:1px solid var(--line);padding:10px 12px}}
.card.open .body{{display:block}}.t{{font-weight:700}}.pill{{padding:1px 7px;border-radius:10px;
border:1px solid var(--line);color:var(--mut)}}.pill.ok{{color:var(--ok);border-color:var(--ok)}}
.pill.bad{{color:var(--bad);border-color:var(--bad)}}.dir.up,.up{{color:var(--up)}}.dir.dn,.dn{{color:var(--dn)}}
.q,.raw{{color:var(--mut)}}.num{{color:var(--mut)}}.cols{{display:flex;gap:22px;flex-wrap:wrap}}
.cols>div{{min-width:250px;flex:1}}table{{border-collapse:collapse;width:100%;font-size:13px}}
td,th{{border-bottom:1px solid var(--line);padding:3px 6px;text-align:left;vertical-align:top}}
th{{color:var(--mut);font-weight:600}}.why{{color:#d2a8ff}}main{{padding:0 0 40px}}
</style></head><body><header><h1>{esc(title)}</h1><div class="sub">{esc(head)}</div></header>
<main>{body}</main></body></html>"""
