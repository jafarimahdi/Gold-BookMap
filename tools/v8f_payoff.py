#!/usr/bin/env python3
"""v8f_payoff v2 - WHAT A VERDICT ACTUALLY EARNS, IN MONEY.  Read-only. Nothing written.

v1 had a bug worth keeping in the docstring, because it nearly sold the owner a profit:
the money columns printed the mean of the RAW 9-MINUTE DRIFT and labelled it "signed".
`fwd9` carries the market's direction; a P&L needs the TICKET's direction. The two are
only the same when every ticket is right. Corrected rule:  P&L = |fwd9| if the door was
right, -|fwd9| if it was wrong (teacher's right/wrong test: BOUNCE wants a RISE, BREAK a
FALL - gates_report.py:_way). v1's 'IF FLIPPED = -(AS SAID)' therefore reported +0.89
USD/oz on the 2026-11-05 tape where the true number is -2.75 USD/oz: a loss.

Usage:
  python tools/v8f_payoff.py                       newest data/gates_sea*.json (by mtime)
  python tools/v8f_payoff.py data/gates_d05.json   one file you choose
  ... --friction 0.30                              USD/oz per round trip (default 0.20)

Everything is printed per ounce AND per contract (x100), because costs are quoted per
contract and drift is quoted per ounce, and mixing them is how a 0.89 becomes a 89.
"""
from __future__ import annotations

import glob
import json
import os
import re
import statistics
import sys
from datetime import datetime, timezone

PUSH_RE = re.compile(r"PUSH=([A-Z]+)")
RUNG_RE = re.compile(r"RUNG=([A-Za-z_0-9]+)")
TOLL_RE = re.compile(r"TOLL=(paid|unpaid)")
VERD_RE = re.compile(r"VERDICT=([A-Z]+)")
PER_OZ_TO_PER_LOT = 100.0


def _num(x):
    return float(x) if isinstance(x, (int, float)) else None


def episodes_of(doc):
    for day in (doc.get("_days") or [doc]):
        d = str(day.get("day") or doc.get("day") or "?")
        for e in (day.get("episodes") or []):
            yield d, e


def card_bits(e):
    g = str(e.get("g3") or "")

    def _one(rx):
        m = rx.search(g)
        return m.group(1) if m else "?"

    return {"push": _one(PUSH_RE), "rung": _one(RUNG_RE),
            "toll": _one(TOLL_RE), "verd": _one(VERD_RE)}


def is_right(f, verd):
    """The teacher's own rule, as a predicate - not as a sign."""
    if f is None:
        return None
    if verd == "BOUNCE":
        return f > 0
    if verd == "BREAK":
        return f < 0
    return None


def pnl(e, field="fwd9"):
    """The MONEY of the ticket, USD/oz. BOUNCE means BUY at the rung, so a bounce ticket
    earns exactly the drift that follows - `fwd9`, signed, nothing else. BREAK means SELL,
    so it earns 0 - fwd9.

    Three versions of this function were written and two were wrong; both errors are worth
    naming, because both LOOK like results:
      v1 returned raw `fwd9` and labelled it "signed". The arithmetic was right for BOUNCE
         (this tape has no BREAK tickets) and the label was wrong. Keep the number, distrust
         the name.
      v2 tried `abs(f)` and re-negated on a loss. `-abs(f)` of a negative `f` is `f` again,
         so the whole branch was a no-op - a sign written twice is a sign cancelled.
      v3 returned +|f| on wins and -|f| on losses, i.e. paid the ticket for being wrong by
         the size of its own mistake. Mean +2.75/oz with t +4.60 - a beautiful number, and
         not a directional trade: it is the payout of a STRADDLE (volatility), and the
         premium a straddle costs was never subtracted. A loss can never be a profit.
    The rule that survives testing: the drift, times +1 for a buy and -1 for a sell."""
    f = _num(e.get(field))
    if f is None:
        return None
    v = card_bits(e)["verd"]
    if v == "BOUNCE":
        return f
    if v == "BREAK":
        return 0.0 - f
    return None


def line(tag, rows, friction, field):
    if not rows:
        print(f"   {tag:26s} n=0")
        return
    net = [r - friction for r in rows]
    n = len(net)
    mean = sum(net) / n
    med = statistics.median(net)
    # counted on GROSS, so it can be compared with the teacher's `8/23`; the net win
    # count is printed after it because friction moving a ticket across zero is exactly
    # the kind of thing that must be visible, not silently folded into one number.
    win = sum(1 for e in rows if e > 0)
    winnet = sum(1 for e in net if e > 0)
    sd = statistics.stdev(net) if n > 1 else 0.0
    se = sd / (n ** 0.5) if n else 0.0
    t = (mean / se) if se else 0.0
    tcrit = {3: 3.182, 5: 2.571, 7: 2.447, 10: 2.262, 15: 2.145, 20: 2.093,
             22: 2.074, 30: 2.045, 39: 2.023, 45: 2.014}.get(n - 1, 2.0)
    lo, hi = mean - tcrit * se, mean + tcrit * se
    print(f"   {tag:26s} n={n:3d}  mean {mean:+7.2f}  sd {sd:5.2f}  t {t:+5.2f}  "
          f"95%CI [{lo:+6.2f},{hi:+6.2f}]  right {win}/{n} gross, {winnet}/{n} net ({field})")
    print(f"   {'':26s}      per CONTRACT, net of {friction * PER_OZ_TO_PER_LOT:.0f} friction: "
          f"mean {mean * PER_OZ_TO_PER_LOT:+8.1f}  95%CI bottom {lo * PER_OZ_TO_PER_LOT:+8.1f}  "
          f"median {med * PER_OZ_TO_PER_LOT:+8.1f}")


def main(argv):
    argv = list(argv[1:])
    friction = 0.20
    if "--friction" in argv:
        k = argv.index("--friction")
        friction = float(argv[k + 1])
        argv = argv[:k] + argv[k + 2:]
    print(f"friction assumption {friction:.2f} USD/oz per round trip = "
          f"{friction * PER_OZ_TO_PER_LOT:.0f} USD/contract. Take it from YOUR broker.")
    files = argv or sorted(
        [p for p in glob.glob("data/gates_sea*.json") if not p.endswith(".days.json")],
        key=lambda p: -os.path.getmtime(p))[:2]
    if not files:
        print("no data/gates_sea*.json here - pass the file, e.g. data/gates_d05.json")
        return 1
    for f in files:
        print("=" * 78)
        print(f"FILE: {f}   mtime {datetime.fromtimestamp(os.path.getmtime(f)):%Y-%m-%d %H:%M}")
        try:
            doc = json.load(open(f, encoding="utf-8"))
        except Exception as exc:
            print(f"  unreadable: {type(exc).__name__}: {exc}")
            continue
        rows = []
        for day, e in episodes_of(doc):
            b = card_bits(e)
            raw = _num(e.get("fwd9"))
            p9 = pnl(e, "fwd9")
            p12 = pnl(e, "pb4")
            r = is_right(raw, b["verd"])
            status = "abstain (tape ran out)" if raw is None else ("RIGHT" if r else "wrong")
            rows.append((day, b, raw, p9, p12, r))
            t = datetime.fromtimestamp(int(e.get("ep") or 0), tz=timezone.utc).strftime("%H:%M")
            drift = "   None" if raw is None else f"{raw:+7.2f}"
            money = "  None" if p9 is None else f"{p9:+7.2f}"
            print(f"   {day} {t}Z  {b['verd']:9s} push={b['push']:5s} rung={b['rung']:11s} "
                  f"toll={b['toll']:7s} drift {drift}  P&L {money}  -> {status}")
        ok = [r for r in rows if r[3] is not None]
        print(f"   ---- {len(rows)} verdict(s), {len(ok)} measurable ----")
        if not ok:
            print("   nothing measurable - no money number exists for this file")
            continue
        said = [r[3] for r in ok]
        nright = sum(1 for r in ok if r[5])
        line("AS SAID by the door", said, friction, "P&L")
        line("IF FLIPPED (opposite)", [0.0 - x for x in said], friction, "P&L")
        p12 = [r[4] for r in ok if r[4] is not None]
        if p12:
            line("AS SAID, +12 min", p12, friction, "P&L")
            line("FLIPPED, +12 min", [0.0 - x for x in p12], friction, "P&L")
        for p in ("DOWN", "UP"):
            sel = [r[3] for r in ok if r[1]["push"] == p]
            line(f"  push={p} as said", sel, friction, "P&L")
        # what the RAW DRIFT says, with no door involved at all - this is the question
        # the flipped count could not ask: does price keep going, or come back?
        up = [r[2] for r in ok if r[1]["push"] == "UP" and r[2] is not None]
        dn = [r[2] for r in ok if r[1]["push"] == "DOWN" and r[2] is not None]
        if up:
            print(f"   raw drift after push=UP   n={len(up):3d}  mean {sum(up)/len(up):+7.2f} USD/oz "
                  f"({sum(up)/len(up)*PER_OZ_TO_PER_LOT:+.0f}/contract) - CONTINUATION pays if this > friction")
        if dn:
            print(f"   raw drift after push=DOWN n={len(dn):3d}  mean {sum(dn)/len(dn):+7.2f} USD/oz "
                  f"({sum(dn)/len(dn)*PER_OZ_TO_PER_LOT:+.0f}/contract) - CONTINUATION pays if this < -friction")
        for key, lbl in (("toll", "toll={}"), ("verd", "verdict={}"), ("rung", "rung={}")):
            groups = {}
            for r in ok:
                groups.setdefault(r[1][key], []).append(r[3])
            for k, vv in sorted(groups.items(), key=lambda kv: -len(kv[1]))[:6]:
                line(f"  {lbl.format(k)}", vv, friction, "P&L")
        print("   arithmetic, not a law: GC = 100 oz, tick 0.10 = $10/contract/tick, so a")
        print("   1-tick spread in + 1 tick out ~= $20/contract = 0.20/oz. A mean net figure")
        print("   under that is not an edge, it is a bill. And a t under ~2 is noise, however")
        print("   nice the mean looks - which is why n is printed before everything else.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
