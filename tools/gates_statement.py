#!/usr/bin/env python3
"""tools/gates_statement.py - the bank's own notebook, read ALOUD (v.1, parcel 2026-11-28 #6).

NEW FILE, JUSTIFIED (LAW #14 applies to paper too): the evening is a fixed order of tools and
this one has a different god from every existing one - it does NOT trust our diary OR the sea;
it asks the BROKER what deals actually happened (`history_deals_get`) and reads the day's money
from the bank's mouth. Born the weekend the reconciliation was missing: a day-1 coin (pos
102984283) came home +15.18 on 2026-11-26 with NO matching act in the diary - positions were
reconciled with the bank, money never was. Every bank on earth closes the day by holding its
notebook against the money-house's; ours now does too, or it says SO in so many words.

READ-ONLY BY CONSTRUCTION: `history_deals_get` queries; no order_send exists in this file;
arming laws (Law 108 family, LAW 115) untouched - a test can never trade through here because
there is no door. If MetaTrader5 cannot be imported (any analysis box, CI, my sandbox), the
tool prints its honest skip-line and exits 2 - a missing bank is NAMED, never hidden (law 5).

usage:  python tools/gates_statement.py [YYYY-MM-DD]     (default: today)
"""
import sys
import time
from datetime import datetime, timedelta


def main(day=None):
    day = str(day or time.strftime("%Y-%m-%d"))
    try:
        y, m, d = [int(x) for x in day.split("-")]
        a = datetime(y, m, d)
    except Exception:
        print(f"[statement] bad date {day!r} - use YYYY-MM-DD")
        return 2
    b = a + timedelta(days=1)
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("[statement] MetaTrader5 not importable here - the bank's notebook lives only "
              "where the terminal lives; the organ skips, kindly and loudly")
        return 2
    if not mt5.initialize():
        print(f"[statement] mt5.initialize failed: {mt5.last_error()!r} - terminal not awake?")
        return 3
    deals = mt5.history_deals_get(a, b, group="*") or []   # group='*' - 'selected' (the Market-
    ins = [x for x in deals if int(x.entry or 0) == 0]     # Watch default) once returned 0 deals
    outs = [x for x in deals if int(x.entry or 0) != 0]   # for a day with five. That was MY bug;
    tot = sum(float(x.profit or 0.0) + float(x.commission or 0.0) + float(x.swap or 0.0)        # the
          for x in deals)                                  # default is now the lesson, not the flag.
    print(f"[statement] {day}: the bank says {len(ins)} IN / {len(outs)} OUT deal(s); "
          f"profit+fees+swap = {tot:+.2f}")
    for x in outs:
        print(f"  OUT  pos {x.position_id} price {x.price} profit {float(x.profit or 0.0):+.2f} "
              f"comm {float(x.commission or 0.0):+.2f} swap {float(x.swap or 0.0):+.2f}")
    for x in ins:
        print(f"  IN   pos {x.position_id} price {x.price} vol {x.volume} "
              f"comm {float(x.commission or 0.0):+.2f}")
    if not deals:
        print("  (zero deals - if the day's diary shows any act= line, THIS LINE IS THE FINDING: "
              "money moved somewhere the bank cannot see, or the bank was not the bank that day)")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:2]))
