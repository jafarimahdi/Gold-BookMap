"""
Gold robot - BookMap bridge edition (v5 of the gold data app)
==============================================================

Reads the market data that the BookMap Python addon `bookmap_addon.py`
writes to a CSV file and turns it into a live stream:

    [TRADE ] MGCZ5 px=4376.40 qty=1 @ 2026-09-16T14:30:00.123 Buy
    [BID   ] MGCZ5 4376.30 x 12
    [ASK   ] MGCZ5 4376.50 x 7
    [BOOK  ] bids: 4376.30x12 4376.20x8 | asks: 4376.50x7 4376.60x15
    [STATS ] 85 events/s | total 12,345 | file 8.2 MB | direct side 98%

PIPELINE:  Rithmic (real-time) -> BookMap Global (MGC chart + bookmap_addon.py)
           -> ticks.csv  ->  THIS ROBOT (and the main trading loop)

Requirements: Python 3.10+ only. No pip installs needed (stdlib only) for monitor.
For the full trading pipeline (main.py) see requirements.txt.

USAGE:
    python gold_robot_bookmap.py                     # default file, new events only
    python gold_robot_bookmap.py --from-start        # replay whole file
    python gold_robot_bookmap.py --only last         # print only trades
    python gold_robot_bookmap.py --file D:\data\ticks.csv
    python gold_robot_bookmap.py --big 10            # alert on trades >=10 lots (MGC micro)

MANAGEMENT:
    * Start order: 1) BookMap (Global plan, Rithmic connection, MGC chart, addon enabled)
      2) this robot OR main.py --loop. Stop: Ctrl+C anytime.
    * [STALE] lines appear when no data arrives for 30s — checklist:
      market halt 23:00-00:00 Budapest daily, weekend Fri 23:00 → Mon 00:00,
      BookMap disconnected, chart closed, or addon disabled.
    * To reset data file: STOP robot first (Windows locks open files),
      then delete ticks.csv — BookMap addon recreates it with header.
    * Your custom logic goes in on_event() below.

BookMap improvements vs NT:
    - Trades show Buy/Sell side from BookMap's true aggressor flag (operation column)
    - Full-depth L2 (all levels) in [BOOK] summary
    - Stats include direct-side ratio (how many trades used BookMap direct side)
"""

import argparse
import csv
import os
import sys
import time
from datetime import datetime
from pathlib import Path

DEFAULT_FILES = [
    r"C:\BookMapBridge\ticks.csv",
    r"C:\NinjaBridge\ticks.csv",  # legacy fallback
]

def resolve_bridge_file(cli_path):
    """Pick ticks.csv to use. Explicit --file wins, else auto-detect."""
    if cli_path:
        return cli_path, False
    candidates = []
    for env_key in ("BOOKMAP_BRIDGE_FILE", "NT_BRIDGE_FILE", "BM_BRIDGE_FILE"):
        env_path = os.environ.get(env_key)
        if env_path:
            candidates.append(env_path)
    # Same folder as script
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ticks.csv"))
    # Common locations
    candidates.append(r"A:\gitHub\Rhitmic\Gold-MT5\ticks.csv")
    candidates.append(r"A:\gitHub\Rhitmic\ticks.csv")
    candidates.extend(DEFAULT_FILES)
    for cand in candidates:
        if os.path.exists(cand):
            return cand, True
    # Nothing exists yet: wait next to script
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "ticks.csv"), False


class DepthBook:
    """Order book keyed by PRICE (robust for BookMap full depth)."""
    def __init__(self):
        self.bids = {}  # price -> size
        self.asks = {}

    def apply(self, side: str, price: float, size: float, operation: str):
        book = self.bids if side == "Bid" else self.asks
        if operation == "Remove" or size <= 0:
            book.pop(price, None)
        else:
            book[price] = size

    def top(self, n=5):
        bids = sorted(((p, s) for p, s in self.bids.items() if s > 0),
                      key=lambda ps: -ps[0])[:n]
        asks = sorted(((p, s) for p, s in self.asks.items() if s > 0))[:n]
        return bids, asks


def on_event(evt: dict, state: dict):
    """Your robot logic — called for every event."""
    if evt["event"] == "Last":
        state["volume"] += evt["size"]
        # Track direct side hits
        op = evt.get("operation", "")
        if op in ("Buy", "Sell"):
            state["direct_side"] += 1
        if evt["size"] >= state["big_threshold"]:
            print(f"[ALERT ] big trade {evt['size']:>4} lots @ {evt['price']:,.2f} side={op} (session vol {state['volume']:,})")

def wait_for_file(path: str):
    if os.path.exists(path):
        return
    print(f"Waiting for bridge file: {path}")
    print("  (Start BookMap with MGC chart + bookmap_addon.py enabled,")
    print("   or pass another path with --file)")
    while not os.path.exists(path):
        time.sleep(1.0)
    print("File found — starting.\n")

def file_identity(path: str):
    try:
        st = os.stat(path)
        return (st.st_dev, st.st_ino)
    except OSError:
        return (None, None)

def follow(path: str, from_start: bool):
    wait_for_file(path)
    f = open(path, "r", encoding="utf-8", errors="replace")
    ident = file_identity(path)
    if not from_start:
        f.seek(0, os.SEEK_END)

    while True:
        pos = f.tell()
        line = f.readline()
        if line.endswith("\n"):
            yield line
            continue
        if line:
            f.seek(pos)
            yield None
            time.sleep(0.05)
            continue

        time.sleep(0.25)
        try:
            gone = not os.path.exists(path)
            replaced = (not gone) and (file_identity(path) != ident or os.path.getsize(path) < f.tell())
            if gone or replaced:
                print("[BRIDGE] data file reset/rotated — switching to fresh one")
                f.close()
                wait_for_file(path)
                f = open(path, "r", encoding="utf-8", errors="replace")
                f.seek(0, os.SEEK_END)
                ident = file_identity(path)
        except OSError:
            pass
        yield None

def parse_line(line: str):
    try:
        parts = next(csv.reader([line.rstrip("\r\n")]))
    except Exception:
        return None
    if len(parts) != 7 or parts[0] == "time":
        return None
    try:
        return {
            "time": parts[0],
            "event": parts[1],
            "price": float(parts[2]),
            "size": float(parts[3] or 0),
            "level": int(parts[4]) if parts[4] else -1,
            "operation": parts[5],
            "instrument": parts[6],
        }
    except ValueError:
        return None

def main():
    ap = argparse.ArgumentParser(description="Gold robot - BookMap bridge edition")
    ap.add_argument("--file", default=None, help="path to ticks.csv from BookMap addon")
    ap.add_argument("--from-start", action="store_true", help="replay whole file")
    ap.add_argument("--only", default="last,bid,ask", help="which L1 events to print: last,bid,ask,depth")
    ap.add_argument("--book-interval", type=float, default=5.0, help="seconds between [BOOK] summaries (0=off)")
    ap.add_argument("--stale-after", type=float, default=30.0, help="seconds silence before [STALE]")
    ap.add_argument("--big", type=int, default=10, help="alert when trade >= this lots (MGC micro = small)")
    args = ap.parse_args()

    show = {s.strip().lower() for s in args.only.split(",") if s.strip()}
    path, auto = resolve_bridge_file(args.file)

    print("="*74)
    print("GOLD ROBOT - BookMap bridge edition (v5)")
    print("="*74)
    print(f"  data file : {path}" + ("   (auto-detected)" if auto else ""))
    print(f"  mode      : {'replay whole file' if args.from_start else 'live (new events only)'}")
    print(f"  printing  : {', '.join(sorted(show)) or 'nothing'}" + (f" | book every {args.book_interval:.0f}s" if args.book_interval else ""))
    print("  stop      : Ctrl+C")
    print("="*74)
    print()
    print("Waiting for market data... (lines appear once BookMap writes them)")
    print()

    books = {}
    state = {"volume": 0.0, "big_threshold": args.big, "direct_side": 0, "errors": 0}
    total = 0
    skipped = 0
    started = time.time()
    last_event_mono = time.monotonic()
    last_book_at = 0.0
    last_stats_at = 0.0
    stale_reported_at = 0.0
    events_this_sec = 0
    sec_start = time.monotonic()

    try:
        for item in follow(path, args.from_start):
            now = time.monotonic()

            if item is None:
                evt = None
            else:
                evt = parse_line(item)
                if evt is None:
                    skipped += 1
                    continue

            if evt is not None:
                total += 1
                events_this_sec += 1
                last_event_mono = now
                try:
                    ev = evt["event"]
                    if ev == "Last" and "last" in show:
                        side = evt.get("operation", "")
                        side_str = f" {side}" if side else ""
                        print(f"[TRADE ] {evt['instrument']} px={evt['price']:,.2f} qty={evt['size']:.0f} @ {evt['time']}{side_str}")
                    elif ev == "Bid" and "bid" in show:
                        print(f"[BID   ] {evt['instrument']} {evt['price']:,.2f} x {evt['size']:.0f}")
                    elif ev == "Ask" and "ask" in show:
                        print(f"[ASK   ] {evt['instrument']} {evt['price']:,.2f} x {evt['size']:.0f}")
                    elif ev in ("DepthBid", "DepthAsk") and "depth" in show:
                        print(f"[{ev.upper()}] {evt['instrument']} {evt['price']:,.2f} x {evt['size']:.0f} op={evt['operation']}")
                    elif ev in ("DepthBid", "DepthAsk"):
                        book = books.setdefault(evt["instrument"], DepthBook())
                        book.apply(ev[len("Depth"):], evt["price"], evt["size"], evt["operation"])

                    on_event(evt, state)

                except KeyboardInterrupt:
                    raise
                except Exception as exc:
                    state["errors"] += 1
                    if state["errors"] <= 3 or state["errors"] % 100 == 0:
                        print(f"[WARN  ] ignored bad event #{state['errors']}: {exc!r}")

            # book summary
            if args.book_interval and now - last_book_at >= args.book_interval and books:
                last_book_at = now
                for instr, book in books.items():
                    bids, asks = book.top(5)
                    if not bids and not asks:
                        continue
                    bid_str = "  ".join(f"{p:,.2f}x{s:.0f}" for p,s in bids) or "—"
                    ask_str = "  ".join(f"{p:,.2f}x{s:.0f}" for p,s in asks) or "—"
                    print(f"[BOOK  ] {instr} bids: {bid_str}  |  asks: {ask_str}")

            # stats
            if now - last_stats_at >= 5.0:
                last_stats_at = now
                elapsed = max(1, now - sec_start)
                rate = events_this_sec / elapsed if elapsed>0 else 0
                try:
                    fsize = os.path.getsize(path) / (1024*1024)
                except OSError:
                    fsize = 0
                direct_pct = (state["direct_side"]/total*100) if total>0 else 0
                print(f"[STATS ] {rate:.0f} events/s | total {total:,} (skip {skipped}) | file {fsize:.1f} MB | direct side {direct_pct:.0f}% | vol {state['volume']:.0f}")
                events_this_sec = 0
                sec_start = now

            # stale watchdog
            if now - last_event_mono >= args.stale_after and now - stale_reported_at >= args.stale_after:
                stale_reported_at = now
                print(f"[STALE ] no data for {args.stale_after:.0f}s — checklist:")
                print("  1) Market closed? Daily halt 23:00-00:00 Budapest, weekend Fri 23:00→Mon 00:00")
                print("  2) BookMap disconnected? Check Rithmic connection green")
                print("  3) MGC chart closed? Keep chart open in background")
                print("  4) Addon disabled? Settings → API plugins → enable GoldBookMapBridge")
                print(f"  5) File path correct? Currently watching {path}")

    except KeyboardInterrupt:
        print("\nStopped by user")
    finally:
        print(f"\nDone — total events {total:,} skipped {skipped} volume {state['volume']:.0f} direct {state['direct_side']}")

if __name__ == "__main__":
    main()
