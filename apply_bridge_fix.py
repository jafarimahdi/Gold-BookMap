#!/usr/bin/env python3
"""apply_bridge_fix.py — fixes the frozen-quote sensor (bridge-fix-2026-10-07).

ROOT CAUSE (proven 2026-10-07): ticks.csv contains only Mbo/DepthBid/DepthAsk/Last
events — the exporter never writes Bid/Ask touch events, so the bridge's
best_bid/best_ask never update and the published quotes freeze (ask stuck at
4169.70 while the real market was 4152.5).

THE FIX (all inside bookmap_bridge_provider.py):
  1. TRUE TOUCH  — track fresh DepthBid/DepthAsk prices in a rolling 30s window
                   and publish bid=max(window bids), ask=min(window asks).
  2. PRICE ANCHOR — analysis price = LAST TRADE (Last event), not the midpoint
                   of two quotes. A frozen quote can never poison the price.
  3. FEED GUARD   — if quotes look implausible (spread > 0.5% or mid disagrees
                   with the last trade) -> loud warning + data['feed_warnings'].

Run from the repo root:  python *bridge_fix*.py     (safe to run twice)
Backup: bookmap_bridge_provider.py.pre_bridge_fix
"""
import shutil
import sys
from pathlib import Path

TARGET = Path("bookmap_bridge_provider.py")

P1_FIND = '''    __slots__ = ("bids", "asks", "ticks", "best_bid", "best_ask", "last_dt", "mbo_events")

    def __init__(self):
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self.ticks: List[Dict[str, Any]] = []
        self.best_bid: float = 0.0
        self.best_ask: float = 0.0
        self.last_dt: Optional[datetime] = None
        self.mbo_events: List[Dict[str, Any]] = []

    def reset(self) -> None:
        self.bids, self.asks = {}, {}
        self.ticks = []
        self.best_bid = self.best_ask = 0.0
        self.last_dt = None
        self.mbo_events = []'''

P1_NEW = '''    __slots__ = ("bids", "asks", "ticks", "best_bid", "best_ask", "last_dt",
                 "mbo_events", "depth_touch")

    def __init__(self):
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self.ticks: List[Dict[str, Any]] = []
        self.best_bid: float = 0.0
        self.best_ask: float = 0.0
        self.last_dt: Optional[datetime] = None
        self.mbo_events: List[Dict[str, Any]] = []
        # bridge-fix-2026-10-07: rolling (ts, "B"/"A", price) of fresh depth
        # events — the only live top-of-book source (no Bid/Ask events exist).
        self.depth_touch: List[Any] = []

    def reset(self) -> None:
        self.bids, self.asks = {}, {}
        self.ticks = []
        self.best_bid = self.best_ask = 0.0
        self.last_dt = None
        self.mbo_events = []
        self.depth_touch = []'''

P2_FIND = '''        elif event == "DepthBid":
            if operation == "Remove" or size <= 0:
                st.bids.pop(price, None)
            else:
                st.bids[price] = size
            st.last_dt = dt
        elif event == "DepthAsk":
            if operation == "Remove" or size <= 0:
                st.asks.pop(price, None)
            else:
                st.asks[price] = size
            st.last_dt = dt'''

P2_NEW = '''        elif event == "DepthBid":
            if operation == "Remove" or size <= 0:
                st.bids.pop(price, None)
            else:
                st.bids[price] = size
                st.depth_touch.append((dt, "B", price))
                if len(st.depth_touch) > 1200:
                    del st.depth_touch[:600]
            st.last_dt = dt
        elif event == "DepthAsk":
            if operation == "Remove" or size <= 0:
                st.asks.pop(price, None)
            else:
                st.asks[price] = size
                st.depth_touch.append((dt, "A", price))
                if len(st.depth_touch) > 1200:
                    del st.depth_touch[:600]
            st.last_dt = dt'''

P3_FIND = '''        symbol_out = name or symbol or getattr(config, "DATA_SYMBOL", "MGC 12-26")
        data = self.build_market_data(symbol=symbol_out)'''

P3_NEW = '''        symbol_out = name or symbol or getattr(config, "DATA_SYMBOL", "MGC 12-26")
        data = self.build_market_data(symbol=symbol_out)
        # bridge-fix-2026-10-07: TRUE touch + last-trade anchor + feed guard.
        try:
            with self.tail.lock:
                st_fix = self.tail.instruments.get(symbol_out)
            if st_fix is not None:
                if st_fix.ticks:
                    data["price"] = float(st_fix.ticks[-1]["price"])
                tb = ta = 0.0
                if st_fix.depth_touch:
                    cutoff_ts = st_fix.depth_touch[-1][0].timestamp() - 30.0
                    cb = [p for ts, sd, p in st_fix.depth_touch
                          if sd == "B" and ts.timestamp() >= cutoff_ts and p > 0]
                    ca = [p for ts, sd, p in st_fix.depth_touch
                          if sd == "A" and ts.timestamp() >= cutoff_ts and p > 0]
                    if cb:
                        tb = max(cb)
                    if ca:
                        ta = min(ca)
                if tb > 0 and ta > 0 and ta >= tb:
                    data["bid"], data["ask"] = tb, ta
                last_px = float(data.get("price") or 0.0)
                bid = float(data.get("bid") or 0.0)
                ask = float(data.get("ask") or 0.0)
                warnings = []
                if bid > 0 and ask > 0:
                    spread_pct = 100.0 * (ask - bid) / bid
                    if spread_pct > 0.5:
                        warnings.append(f"quote spread {spread_pct:.3f}% implausible")
                    mid = (ask + bid) / 2.0
                    if last_px > 0 and abs(mid - last_px) > max(1.0, 0.001 * last_px):
                        warnings.append(
                            f"mid {mid:.2f} vs last trade {last_px:.2f} disagree")
                if warnings:
                    logger.warning("FEED GUARD (bridge-fix): %s", "; ".join(warnings))
                data["feed_warnings"] = warnings
        except Exception:
            logger.exception("bridge-fix touch calculation failed")'''

MARKER = "bridge-fix-2026-10-07"


def load(path):
    raw = path.read_bytes()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    return raw.decode("utf-8").replace("\r\n", "\n"), nl


def save(path, text, nl):
    path.write_bytes((text.replace("\n", nl) if nl != "\n" else text).encode("utf-8"))


def apply_op(text, find, new, marker):
    if marker in text:                      # marker FIRST (idempotency)
        return text, "ALREADY"
    n = text.count(find)
    if n == 1:
        return text.replace(find, new), "PATCHED"
    if n == 0:
        return text, "NOT FOUND"
    return text, "AMBIGUOUS (%dx)" % n


def main():
    print("== bridge fix installer (frozen-quote sensor) ==")
    if not TARGET.exists():
        print("ERROR: run from the repo root (bookmap_bridge_provider.py)")
        return 1
    text, nl = load(TARGET)
    results = []
    text, s1 = apply_op(text, P1_FIND, P1_NEW, "self.depth_touch: List[Any] = []")
    results.append(("P1 state (depth_touch tracker)", s1))
    text, s2 = apply_op(text, P2_FIND, P2_NEW, 'st.depth_touch.append((dt, "B", price))')
    results.append(("P2 handlers (record fresh touch)", s2))
    text, s3 = apply_op(text, P3_FIND, P3_NEW, "TRUE touch + last-trade anchor")
    results.append(("P3 publish (touch + anchor + guard)", s3))

    problems = []
    width = max(len(n) for n, _ in results) + 4
    for i, (name, status) in enumerate(results, 1):
        print("[{}/3] {} {}".format(i, name.ljust(width, "."), status))
        if status.startswith(("NOT FOUND", "AMBIGUOUS")):
            problems.append(name)

    if any(s == "PATCHED" for _, s in results):
        if not Path(str(TARGET) + ".pre_bridge_fix").exists():
            shutil.copy2(str(TARGET), str(TARGET) + ".pre_bridge_fix")
        save(TARGET, text, nl)
        print("backup: bookmap_bridge_provider.py.pre_bridge_fix (kept out of git)")

    if problems:
        print("\nPROBLEMS:", ", ".join(problems), "-> change nothing, paste output to chat")
        return 1
    print("\nALL DONE -> RESTART the robot (Ctrl+C, then: python main.py --loop)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
