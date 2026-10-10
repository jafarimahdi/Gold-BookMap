#!/usr/bin/env python3
"""rig_book.py -- the ORDER-BOOK SPY test lake. Planted books, known answers.

  A. tracker math: exact eOFI signs on planted order lifecycles
     (BID_NEW/ASK_NEW/CANCEL/REPLACE with old-state attribution)
  B. honesty: unknown-order CANCEL and unknown-order REPLACE contribute
     NOTHING and are counted; REPLACE-to-zero deletes the order
  C. lean machinery on book-shaped buckets (via flow_probe.episodes)
  D. the reader: archive chunk (.gz) + live overlap + date filter + junk
  E. end-to-end: balanced morning, bid-building wave, then a ramp -> the
     fish is WARNED by the BOOK lean, lift line present, file saved

Run:  python rig_book.py   (from the folder with book_probe.py, flow_probe.py,
                            tf_lens.py)
"""
import bisect
import gzip
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from book_probe import build_eofi_buckets, read_day_mbo
from flow_probe import episodes
from tf_lens import BUDA

FAILURES = []


def check(name, cond, extra=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' -- ' + str(extra)) if (extra and not cond) else ''}")
    if not cond:
        FAILURES.append(name)


def iso(dt):
    """The REAL mbo stamp format: ISO with +02:00 offset."""
    return dt.astimezone(BUDA).isoformat()


def ev(dt, etype, oid, size, price=4200.0):
    return (dt, etype, oid, price, size)


def mbo_line(e):
    dt, etype, oid, price, size = e
    return f"{iso(dt)},{etype},{oid},{price:.4f},{size:.4f},GCZ6.COMEX@RITHMIC"


def main():
    print("BOOK PROBE rig -- planted books, known answers")
    print("=" * 60)
    T0 = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)

    # ---- A: exact tracker math ------------------------------------------------
    print("A. eOFI signs on planted lifecycles")
    events = [
        ev(T0, "BID_NEW", "o1", 5.0),
        ev(T0 + timedelta(seconds=1), "ASK_NEW", "o2", 3.0),
        ev(T0 + timedelta(seconds=2), "CANCEL", "o1", 0.0),          # bid rem 5
        ev(T0 + timedelta(seconds=3), "REPLACE", "o2", 4.0),         # ask rem 3, ask add 4
        ev(T0 + timedelta(seconds=4), "BID_NEW", "o3", 2.0),
    ]
    buckets, st = build_eofi_buckets(events, 10)
    check("A1 one bucket", len(buckets) == 1, len(buckets))
    # cvd = +5 -3 -5 +3 -4 +2 = -2 ; vol = 5+3+5+3+4+2 = 22
    check("A2 cvd exactly -2.0 (signs right)", buckets[0]["cvd"] == -2.0, buckets[0])
    check("A3 vol exactly 22.0", buckets[0]["vol"] == 22.0, buckets[0])
    check("A4 stats: tracked 3, bid_add 7, ask_add 7, bid_rem 5, ask_rem 3",
          st["tracked"] == 3 and st["bid_add"] == 7 and st["ask_add"] == 7
          and st["bid_rem"] == 5 and st["ask_rem"] == 3, st)
    check("A5 no unresolved", st["unresolved_cancel"] == 0 and st["unknown_replace"] == 0, st)

    # ---- B: honesty on unknown orders -------------------------------------------
    print("B. unknown orders contribute nothing, are counted")
    events = [
        ev(T0, "CANCEL", "ghost", 0.0),                               # unknown birth
        ev(T0 + timedelta(seconds=1), "REPLACE", "phantom", 7.0),     # unknown birth
        ev(T0 + timedelta(seconds=2), "CANCEL", "phantom", 0.0),      # still unknown side
        ev(T0 + timedelta(seconds=3), "BID_NEW", "o9", 4.0),
        ev(T0 + timedelta(seconds=4), "REPLACE", "o9", 0.0),          # to zero = gone
        ev(T0 + timedelta(seconds=5), "CANCEL", "o9", 0.0),           # now unresolved
    ]
    buckets, st = build_eofi_buckets(events, 10)
    # 3 unresolved cancels is the honest count: the ghost, the side-unknown
    # phantom, AND the post-REPLACE-to-zero cancel (its side is gone for good)
    check("B1 unknowns counted (3 cancels + 1 replace, all unattributable)",
          st["unresolved_cancel"] == 3 and st["unknown_replace"] == 1, st)
    check("B2 only the real bid ever counted: cvd +4 then -4 = 0",
          buckets[0]["cvd"] == 0.0 and buckets[0]["vol"] == 8.0, buckets[0])

    # ---- C: lean machinery on book buckets ----------------------------------------
    print("C. book leans (via the proven print-spy machinery)")
    K = 1770000000

    def mk(k, rate, vol=100.0):
        return {"k": k, "cvd": rate * vol, "vol": vol, "n": 30}

    quiet = [mk(K + 10 * i, 0.0) for i in range(12)]
    bid_wave = [mk(K + 10 * (12 + i), 0.8) for i in range(10)]
    eps = episodes(quiet + bid_wave)
    check("C1 exactly one episode", len(eps) == 1, len(eps))
    check("C2 it is UP (the book was built on the bid side)",
          eps and eps[0]["dir"] == "UP", eps)
    check("C3 starts at the first bid-heavy bucket",
          eps and eps[0]["start"] == bid_wave[0]["k"], eps)

    # ---- D: the reader --------------------------------------------------------------
    print("D. reader: archive + live, dedupe, date filter, junk")
    tmp = tempfile.mkdtemp(prefix="book_rig_")
    old_cwd = os.getcwd()
    try:
        os.makedirs(os.path.join(tmp, "data", "archive"))
        shared = ev(T0, "BID_NEW", "A1", 2.0)
        junk = "not,a,valid,line"
        other_day = ev(datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc),
                       "BID_NEW", "X9", 9.0)
        arch = [ev(T0 + timedelta(seconds=1), "ASK_NEW", "A2", 1.0),
                ev(T0 + timedelta(seconds=2), "BID_NEW", "A3", 1.0), shared]
        with gzip.open(os.path.join(tmp, "data", "archive",
                                    "mbo_20261015_100000.csv.gz"), "wt") as f:
            f.write("time,event_type,order_id,price,size,instrument\n")
            for e in arch + [other_day]:
                f.write(mbo_line(e) + "\n")
            f.write(junk + "\n")
        live = [shared,                                   # duplicate -> deduped
                ev(T0 + timedelta(seconds=5), "BID_NEW", "L1", 3.0),
                ev(T0 + timedelta(seconds=6), "CANCEL", "A3", 0.0)]
        with open(os.path.join(tmp, "mbo.csv"), "w") as f:
            f.write("time,event_type,order_id,price,size,instrument\n")
            for e in live:
                f.write(mbo_line(e) + "\n")
        os.chdir(tmp)
        events, err = read_day_mbo("2026-10-15")
        check("D1 reads archive + live, no error", err is None, err)
        ids = [e[2] for e in events]
        check("D2 duplicate event counted once", ids.count("A1") == 1, ids)
        check("D3 other-day event filtered out", "X9" not in ids, ids)
        check("D4 junk skipped, all real events kept",
              sorted(ids) == ["A1", "A2", "A3", "A3", "L1"], ids)
        check("D5 events sorted by time",
              all(events[i][0] <= events[i + 1][0] for i in range(len(events) - 1)))
    finally:
        os.chdir(old_cwd)

    # ---- E: end-to-end ---------------------------------------------------------------
    print("E. end-to-end (balanced morning, bid-building wave, then a ramp)")
    tmp2 = tempfile.mkdtemp(prefix="book_e2e_")
    try:
        os.chdir(tmp2)
        import random
        rng = random.Random(4242)
        # the print tape (like the lens lake): noise, ramp from 12:50
        t = datetime(2026, 10, 15, 8, 0, tzinfo=BUDA).astimezone(timezone.utc)
        end = datetime(2026, 10, 15, 16, 0, tzinfo=BUDA).astimezone(timezone.utc)
        price = 2650.0
        tick_lines = ["time,type,price,size,extra1,extra2"]
        p_times, p_prices = [], []
        while t < end:
            bud = t.astimezone(BUDA)
            mins = (bud - datetime(2026, 10, 15, 8, 0, tzinfo=BUDA)).total_seconds() / 60.0
            price += 0.22 if 290 <= mins < 325 else rng.uniform(-0.15, 0.15)
            tick_lines.append(f"2026-10-15 {bud.strftime('%H:%M:%S')}.000,Last,"
                              f"{price:.2f},3,bookmap,rig")
            p_times.append(t)
            p_prices.append(price)
            t += timedelta(seconds=rng.choice([3, 5, 7]))
        open("ticks.csv", "w").write("\n".join(tick_lines) + "\n")

        def last_print(dt):
            """Realistic order pricing: orders hug the actual touch."""
            i = bisect.bisect_right(p_times, dt) - 1
            return p_prices[i] if i >= 0 else 2650.0

        # the book tape: balanced morning; bid-building wave 12:46-12:58
        events = []
        nid = 0
        pending_cancels = []          # (when, oid)
        t = datetime(2026, 10, 15, 8, 0, tzinfo=BUDA).astimezone(timezone.utc)
        ask_ids_alive = []
        while t < end:
            bud = t.astimezone(BUDA)
            mins = (bud - datetime(2026, 10, 15, 8, 0, tzinfo=BUDA)).total_seconds() / 60.0
            if 286 <= mins < 298:                       # 12:46-12:58 the wave
                # (timed to overlap the fish's warn window: ramp 12:50,
                #  M3 fish ~13:00 -> lean end ~12:58 must be within 5 min)
                nid += 1
                events.append(ev(t, "BID_NEW", f"w{nid}", 6.0,
                                 price=round(last_print(t) - 0.10, 2)))
                if ask_ids_alive:                       # tear the ask side down
                    events.append(ev(t + timedelta(milliseconds=500),
                                     "CANCEL", ask_ids_alive.pop(), 0.0))
                t += timedelta(seconds=1)
            else:                                       # balanced morning/afternoon
                nid += 1
                bid_id, ask_id = f"m{nid}b", f"m{nid}a"
                events.append(ev(t, "BID_NEW", bid_id, 2.0,
                                 price=round(last_print(t) - 0.10, 2)))
                events.append(ev(t + timedelta(milliseconds=300), "ASK_NEW", ask_id, 2.0,
                                 price=round(last_print(t) + 0.10, 2)))
                ask_ids_alive.append(ask_id)
                if nid % 3 == 0:                        # cancel BOTH sides: balanced
                    events.append(ev(t + timedelta(milliseconds=600),
                                     "CANCEL", f"m{nid - 2}b", 0.0))
                    events.append(ev(t + timedelta(milliseconds=700),
                                     "CANCEL", f"m{nid - 2}a", 0.0))
                t += timedelta(seconds=2)
        events.sort(key=lambda e: e[0])
        with open("mbo.csv", "w") as f:
            f.write("time,event_type,order_id,price,size,instrument\n")
            for e in events:
                f.write(mbo_line(e) + "\n")

        r = subprocess.run([sys.executable, os.path.join(old_cwd, "book_probe.py"),
                            "--date", "2026-10-15", "--moment-tf", "3"],
                           capture_output=True, text=True, timeout=900)
        out = r.stdout
        check("E1 exit code 0", r.returncode == 0, out[-400:] + r.stderr[-400:])
        check("E2 header present", "BOOK PROBE" in out)
        check("E2b near-touch page line present", "NEAR-TOUCH" in out)
        check("E3 unresolved rate reported", "unresolved cancels" in out)
        check("E4 the ramp fish is WARNED by the book", "WARNED" in out, out[-800:])
        check("E5 lift line present", "LIFT" in out)
        check("E6 saved to data/", os.path.exists("data/book_probe_2026-10-15.txt"))
    finally:
        os.chdir(old_cwd)

    # ---- H: v1.1 near-touch page, exact math -------------------------------------
    print("H. v1.1 near-touch page (only the shoreline counts)")
    p_times = [T0 + timedelta(seconds=s) for s in (0, 100)]
    p_prices = [2000.0, 2000.0]

    def ref(dt):
        i = bisect.bisect_right(p_times, dt) - 1
        return p_prices[i] if i >= 0 else None

    events = [
        ev(T0 - timedelta(seconds=60), "BID_NEW", "oE", 3.0, price=1999.5),  # before 1st print
        ev(T0 + timedelta(seconds=1), "BID_NEW", "oN", 5.0, price=2000.2),   # near
        ev(T0 + timedelta(seconds=2), "BID_NEW", "oF", 7.0, price=2010.0),   # far
        ev(T0 + timedelta(seconds=3), "CANCEL", "oF", 0.0),                  # far removal
        ev(T0 + timedelta(seconds=4), "ASK_NEW", "oA", 4.0, price=2000.3),   # near ask
        ev(T0 + timedelta(seconds=5), "REPLACE", "oA", 2.0, price=2000.3),   # near: rem 4, add 2
    ]
    buckets, st = build_eofi_buckets(events, 10, ref_price=ref, touch_band=1.0)
    check("H1 two buckets (early event alone)", len(buckets) == 2, len(buckets))
    # near bucket: +5 -4 +4 -2 = 3 ; vol 5+4+4+2 = 15
    check("H2 near bucket cvd exactly 3.0, vol 15.0",
          buckets[1]["cvd"] == 3.0 and buckets[1]["vol"] == 15.0, buckets[1])
    check("H3 stats exact: tracked 4, near 4, far 2, no_ref 1, bid_add 5, ask_add 6, ask_rem 4",
          st["tracked"] == 4 and st["near"] == 4 and st["far"] == 2
          and st["no_ref"] == 1 and st["bid_add"] == 5 and st["ask_add"] == 6
          and st["ask_rem"] == 4, st)
    # v1.0 fallback (no ref): every event counts -> cvd 6, vol 32, far 0
    b0, s0 = build_eofi_buckets(events, 10)
    check("H4 v1.0 whole-book fallback unchanged (cvd 6, vol 32, far 0, no_ref 0)",
          sum(b["cvd"] for b in b0) == 6.0 and sum(b["vol"] for b in b0) == 32.0
          and s0["far"] == 0 and s0["no_ref"] == 0, (b0, s0))

    print("=" * 60)
    if FAILURES:
        print(f"RIG RESULT: {len(FAILURES)} FAILED -> {', '.join(FAILURES)}")
        sys.exit(1)
    print("RIG RESULT: ALL PASS -- the order-book spy measures the planted books exactly.")


if __name__ == "__main__":
    main()
