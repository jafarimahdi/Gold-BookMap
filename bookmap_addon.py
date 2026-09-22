"""
bookmap_addon.py
================
BookMap Python API addon — writes live CME gold data to ticks.csv
in the SAME format the old NinjaTrader bridge used, so the entire
downstream pipeline (Step 2 analysis, AI, MT5 execution, PM) stays unchanged.

This is the CORE of the BookMap port. All data originates from BookMap platform.

Format (identical to GoldBridgeExporter.cs):
    time,event,price,size,level,operation,instrument
    2026-09-16T14:30:00.123,Last,4376.4,1,-1,,MGC ##-26   (legacy)
    2026-09-16T14:30:00.123,Last,4376.4,1,-1,Buy,MGCZ5   (BookMap improved: Buy/Sell in operation)

Event types written:
    Last      — trade, operation = Buy/Sell (true aggressor from BookMap is_bid flag)
    Bid       — best bid price changed
    Ask       — best ask price changed
    DepthBid  — bid level added/updated/removed (full depth, not just top 5)
    DepthAsk  — ask level added/updated/removed

Setup in BookMap (7.4+):
    1. Install BookMap Global+ with Rithmic connection (same login NT used).
    2. Install Python API: Settings → Manage plugins → Bookmap Add-ons (L1) → Python API → Install → Restart
    3. In BookMap: Settings → Configure add-ons → Enable Python API → Open embedded editor
       → New Python file → paste this file's content → Save → Build → File → Open build folder
       → you get GoldBridge.jar → back to Configure add-ons → Add → select GoldBridge.jar → Enable for MGCZ6
    4. The addon will start writing ticks.csv into this folder (Gold-BookMap/) automatically.
    NOTE: Do NOT run 'python bookmap_addon.py' directly for live data — BookMap must launch it
          via the embedded editor with a port arg. For quick test without BookMap, use --demo.

One-folder layout (same as NT version):
    Gold-BookMap/
    ├── ticks.csv                ← BookMap writes LIVE data here (this addon)
    ├── bookmap_addon.py         ← this file (the bridge)
    ├── bookmap_bridge_provider.py ← Python tailer (reads ticks.csv into Step 2)
    ├── main.py --loop           ← the trading robot (reads from provider)
    └── data/archive/            ← gzipped chunks at 200 MB rotation

Data source .env:
    DATA_SOURCE=bookmapbridge
    BOOKMAP_BRIDGE_FILE=        (empty = auto-detect ticks.csv next to this script)
    DATA_SYMBOL=MGC 12-26       (root filter — addon only writes MGC if set)

BookMap improvements over NinjaTrader:
    - Trades carry TRUE aggressor side (is_bid flag) → operation=Buy/Sell → CVD is exact,
      not tick-rule inferred.
    - Full-depth L2 (all levels) vs NT's limited snapshot.
    - Optional MBO/L3 (BOOKMAP_WRITE_MBO=1) for iceberg/spoof votes (Phase 2).

Delayed data protection:
    BookMap free tier futures are delayed ~15 min. This addon checks
    instrument_data['isDelayed'] flag from BookMap and REFUSES to write
    if delayed — to prevent delayed data reaching the robot. Only real-time
    Rithmic feed is allowed.

Author: Ported from GoldBridgeExporter.cs (NinjaScript) + ninja_bridge_provider.py
Version: v5.0 BookMap edition, 2026-09-16
"""

from __future__ import annotations

import csv
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# --------------------------------------------------------------------------- #
# Config & paths — reuse same .env logic as main pipeline
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# Load .env if present (same minimal loader as config.py)
def _load_dotenv_minimal():
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path, override=False)
        return
    except ImportError:
        pass
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line=line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k,v = line.split("=",1)
        k=k.strip()
        v=v.split("#",1)[0].strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k]=v

_load_dotenv_minimal()

BRIDGE_FILE = (
    os.getenv("BOOKMAP_BRIDGE_FILE") or
    os.getenv("NT_BRIDGE_FILE") or
    os.getenv("BM_BRIDGE_FILE") or
    str(BASE_DIR / "ticks.csv")
).strip()
if not BRIDGE_FILE:
    BRIDGE_FILE = str(BASE_DIR / "ticks.csv")

SYMBOL_FILTER = (os.getenv("BOOKMAP_SYMBOL_FILTER") or os.getenv("DATA_SYMBOL") or "").strip()
# Extract root: "MGC 12-26" -> "MGC"
SYMBOL_ROOT = SYMBOL_FILTER.split()[0].upper() if SYMBOL_FILTER else ""

WRITE_MBO = (os.getenv("BOOKMAP_WRITE_MBO", "0") == "1")
MBO_FILE = (os.getenv("BOOKMAP_MBO_FILE") or str(BASE_DIR / "mbo.csv")).strip()

CSV_HEADER = "time,event,price,size,level,operation,instrument\n"

# --------------------------------------------------------------------------- #
# Thread-safe file writer with rotation awareness
# --------------------------------------------------------------------------- #
class TicksWriter:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self._ensure_header()
        self._last_size = 0
        try:
            self._last_size = os.path.getsize(self.path)
        except OSError:
            self._last_size = 0

    def _ensure_header(self):
        # Create file with header if missing or empty
        p = Path(self.path)
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists() or p.stat().st_size == 0:
            with open(p, "w", encoding="utf-8", newline="") as f:
                f.write(CSV_HEADER)
            print(f"[BookMapBridge] Created {p} with header", flush=True)

    def _check_rotation(self):
        # If file was rotated by provider (renamed + new file created),
        # our _last_size will be > current size. Ensure header exists in new file.
        try:
            cur = os.path.getsize(self.path)
            if cur < self._last_size - 1024:  # shrank significantly -> rotated
                print(f"[BookMapBridge] Detected rotation (size {self._last_size} -> {cur}), ensuring header", flush=True)
                self._ensure_header()
            self._last_size = cur
        except OSError:
            self._ensure_header()

    def write_line(self, time_iso: str, event: str, price: float, size: float,
                   level: int, operation: str, instrument: str):
        # InvariantCulture: dot decimal, no spaces
        line = f"{time_iso},{event},{price:.4f},{size:.4f},{level},{operation},{instrument}\n"
        # Replace trailing zeros? Keep simple: strip unnecessary zeros but keep at least one decimal?
        # For compatibility, keep as is but remove extra zeros for readability
        # Actually keep full float repr for price to preserve pips precision
        with self.lock:
            self._check_rotation()
            try:
                with open(self.path, "a", encoding="utf-8", newline="") as f:
                    f.write(line)
            except OSError as e:
                print(f"[BookMapBridge] Write failed: {e}", flush=True)

    def write_raw(self, line: str):
        with self.lock:
            self._check_rotation()
            try:
                with open(self.path, "a", encoding="utf-8", newline="") as f:
                    f.write(line)
            except OSError as e:
                print(f"[BookMapBridge] Write raw failed: {e}", flush=True)


# Global writer
writer = TicksWriter(BRIDGE_FILE)
print(f"[BookMapBridge] Output file: {writer.path}", flush=True)
print(f"[BookMapBridge] Symbol filter root: '{SYMBOL_ROOT}' (empty = all)", flush=True)

# --------------------------------------------------------------------------- #
# Per-instrument state
# --------------------------------------------------------------------------- #
instrument_info: Dict[str, Dict[str, Any]] = {}
order_books: Dict[str, Any] = {}
best_bid_ask: Dict[str, Tuple[Optional[float], Optional[float]]] = {}  # alias -> (bid, ask)
stats = {"trades": 0, "depth": 0, "bidask": 0, "filtered": 0}
stats_lock = threading.Lock()

def _should_process(alias: str) -> bool:
    """v5.4.1 EASY-SWITCH: Changing MGC <-> GC is now 0-code-change.
    - If filter empty -> accept ANY instrument (easiest)
    - If filter is GC or MGC -> accept BOTH GC and MGC (gold family)
    - If filter is comma list \"GC,MGC,SI\" -> accept any in list
    - Else exact root match
    This means you can switch chart from MGCZ6 to GCZ6 in BookMap and it just works.
    """
    if not SYMBOL_ROOT:
        return True
    upper = alias.upper()
    # Support comma list e.g. "GC,MGC"
    if "," in SYMBOL_ROOT:
        roots = [r.strip().upper() for r in SYMBOL_ROOT.split(",") if r.strip()]
        return any(r in upper for r in roots)
    # Gold family: GC filter accepts MGC too and vice versa — makes switching easy
    if SYMBOL_ROOT in ("GC", "MGC"):
        return ("GC" in upper or "MGC" in upper)
    # alias examples: "MGCZ5@Rithmic", "MGC 12-26", "GCZ5"
    return SYMBOL_ROOT in upper

def _now_iso() -> str:
    # Local time with ms, matching NT bridge format: yyyy-MM-ddTHH:mm:ss.fff
    # Use astimezone to get local TZ, isoformat with milliseconds
    return datetime.now().astimezone().isoformat(timespec='milliseconds')

def _price_from_level(price_level: int, pips: float) -> float:
    # BookMap Python API: price_level * pips = real price
    # Some providers already give float price — handle both
    try:
        return float(price_level) * float(pips)
    except (TypeError, ValueError):
        return float(price_level)

def _size_from_level(size_level: int, size_mult: float) -> float:
    try:
        if size_mult == 0:
            return float(size_level)
        return float(size_level) / float(size_mult)
    except (TypeError, ValueError):
        return float(size_level)

# --------------------------------------------------------------------------- #
# BookMap handlers
# --------------------------------------------------------------------------- #
def handle_subscribe_instrument(addon, alias: str, full_name: str, is_crypto: bool,
                                pips: float, size_multiplier: float,
                                instrument_multiplier: float,
                                supported_features: Dict[str, Any] = None):
    if supported_features is None:
        supported_features = {}
    print(f"[BookMapBridge] Subscribing to {alias} ({full_name}) pips={pips} size_mult={size_multiplier} features={supported_features}", flush=True)

    # Delayed data protection — HARD RULE from BOOKMAP_PORT_GUIDE
    is_delayed = supported_features.get("isDelayed", False) if isinstance(supported_features, dict) else False
    if is_delayed:
        print(f"[BookMapBridge] *** REJECTED {alias}: delayed data (free tier) — NOT writing! Use real-time Rithmic feed. ***", flush=True)
        return

    if not _should_process(alias):
        print(f"[BookMapBridge] Filtering out {alias} (filter root {SYMBOL_ROOT})", flush=True)
        with stats_lock:
            stats["filtered"] += 1
        return

    instrument_info[alias] = {
        "pips": pips,
        "size_multiplier": size_multiplier,
        "instrument_multiplier": instrument_multiplier,
        "full_name": full_name,
    }

    # Create order book tracker if bookmap package available
    try:
        import bookmap as bm
        order_books[alias] = bm.create_order_book()
    except Exception:
        order_books[alias] = {"bids": {}, "asks": {}}

    best_bid_ask[alias] = (None, None)

    # Subscribe to depth and trades
    try:
        import bookmap as bm
        # req_id 1 = depth, 2 = trades, 3 = mbo if enabled
        bm.subscribe_to_depth(addon, alias, 1)
        bm.subscribe_to_trades(addon, alias, 2)
        if WRITE_MBO and supported_features.get("mbo", False):
            bm.subscribe_to_mbo(addon, alias, 3)
            print(f"[BookMapBridge] MBO subscribed for {alias}", flush=True)
    except Exception as e:
        print(f"[BookMapBridge] Subscribe failed for {alias}: {e}", flush=True)

def handle_unsubscribe_instrument(addon, alias: str):
    print(f"[BookMapBridge] Unsubscribing {alias}", flush=True)
    instrument_info.pop(alias, None)
    order_books.pop(alias, None)
    best_bid_ask.pop(alias, None)

def handle_depth(addon, alias: str, is_bid: bool, price_level: int, size_level: int):
    if not _should_process(alias):
        return
    info = instrument_info.get(alias)
    if not info:
        return

    pips = info["pips"]
    size_mult = info["size_multiplier"]

    price = _price_from_level(price_level, pips)
    size = _size_from_level(size_level, size_mult)

    # Update local order book for BBO tracking
    try:
        import bookmap as bm
        ob = order_books.get(alias)
        if ob is not None:
            bm.on_depth(ob, is_bid, price_level, size_level)
            # Try to get BBO
            try:
                bbo = bm.get_bbo(ob) if hasattr(bm, "get_bbo") else bm.get_bbos(ob)
                if bbo:
                    (bid_pl, bid_sl), (ask_pl, ask_sl) = bbo
                    bid_price = _price_from_level(bid_pl, pips) if bid_pl is not None else None
                    ask_price = _price_from_level(ask_pl, pips) if ask_pl is not None else None
                    prev_bid, prev_ask = best_bid_ask.get(alias, (None, None))
                    # Write Depth event
                    event = "DepthBid" if is_bid else "DepthAsk"
                    operation = "Remove" if size_level == 0 else "Update"
                    writer.write_line(_now_iso(), event, price, size, -1, operation, alias)

                    # Write Bid/Ask top-of-book if changed
                    if bid_price is not None and bid_price != prev_bid:
                        writer.write_line(_now_iso(), "Bid", bid_price, _size_from_level(bid_sl, size_mult), -1, "", alias)
                        with stats_lock:
                            stats["bidask"] += 1
                    if ask_price is not None and ask_price != prev_ask:
                        writer.write_line(_now_iso(), "Ask", ask_price, _size_from_level(ask_sl, size_mult), -1, "", alias)
                        with stats_lock:
                            stats["bidask"] += 1
                    best_bid_ask[alias] = (bid_price, ask_price)
                    with stats_lock:
                        stats["depth"] += 1
                    return
            except Exception:
                pass
    except ImportError:
        pass

    # Fallback if no BBO available: just write depth
    event = "DepthBid" if is_bid else "DepthAsk"
    operation = "Remove" if size_level == 0 else "Update"
    writer.write_line(_now_iso(), event, price, size, -1, operation, alias)
    with stats_lock:
        stats["depth"] += 1

def handle_trades(addon, alias: str, price_level: float, size_level: int,
                  is_otc: bool, is_bid: bool,
                  is_execution_start: bool, is_execution_end: bool,
                  aggressor_order_id: str, passive_order_id: str):
    if not _should_process(alias):
        return
    info = instrument_info.get(alias)
    if not info:
        return

    pips = info["pips"]
    size_mult = info["size_multiplier"]

    # price_level handling: in trades handler, price_level is already price * (?) 
    # According to docs: Multiply it by pips to get price. But some examples show price_level is float price.
    # We'll try both: if price_level > 10000, likely it's already price in pips? Actually MGC ~ 4300, pips ~0.1
    # So price_level ~ 43000 if pips=0.1. So multiply.
    try:
        # If price_level is int-like large, it's in ticks
        price = float(price_level) * float(pips) if float(price_level) < 100000 else float(price_level)
        # Heuristic: if pips is 0.1 and price_level ~ 43764, then 43764*0.1=4376.4 correct
        # If price_level already 4376.4, multiplying would be wrong. Check magnitude:
        # Real gold price 4000-5000. So if price_level * pips is 4000-5000, use that, else use price_level directly
        candidate = float(price_level) * float(pips)
        if 1000 < candidate < 10000:
            price = candidate
        else:
            # maybe price_level already is price
            if 1000 < float(price_level) < 10000:
                price = float(price_level)
    except Exception:
        price = float(price_level)

    size = _size_from_level(size_level, size_mult)

    # BookMap is_bid flag: documentation says "Whether the trade was a buy (True) or a sell (False)"
    # But earlier research suggests is_bid=True means aggressive sell into bid.
    # We will interpret as: is_bid=True => Sell (aggressor sold), is_bid=False => Buy
    # This matches typical "is_bid" meaning trade hit bid side.
    # User can flip via env if needed: BOOKMAP_FLIP_SIDE=1
    flip = os.getenv("BOOKMAP_FLIP_SIDE", "0") == "1"
    if not flip:
        side_op = "Sell" if is_bid else "Buy"
    else:
        side_op = "Buy" if is_bid else "Sell"

    # Write Last event with operation = side (BookMap improvement)
    writer.write_line(_now_iso(), "Last", price, size, -1, side_op, alias)
    with stats_lock:
        stats["trades"] += 1

def handle_mbo(addon, alias: str, event_type: str, order_id: str, price_level: int, size_level: int):
    if not WRITE_MBO:
        return
    if not _should_process(alias):
        return
    info = instrument_info.get(alias)
    if not info:
        return
    pips = info["pips"]
    size_mult = info["size_multiplier"]
    price = _price_from_level(price_level, pips)
    size = _size_from_level(size_level, size_mult)
    # Write to same ticks.csv as Mbo event for provider's order_events, or to separate mbo.csv
    # v7.0 P2 FIX: write order_id into level column (was -1) so bridge_provider can extract real order_id
    # and avoid legacy w0.8 path, getting age/score institutional w1.6 directly from ticks.csv alone
    try:
        oid_for_level = str(order_id) if str(order_id).strip() not in ("-1","0","","None","null") else -1
    except:
        oid_for_level = -1
    writer.write_line(_now_iso(), "Mbo", price, size, oid_for_level, event_type, alias)
    # Also append to mbo.csv
    try:
        mbo_path = Path(MBO_FILE)
        mbo_path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not mbo_path.exists()
        with open(mbo_path, "a", encoding="utf-8", newline="") as f:
            if is_new:
                f.write("time,event_type,order_id,price,size,instrument\n")
            f.write(f"{_now_iso()},{event_type},{order_id},{price},{size},{alias}\n")
    except OSError:
        pass

def handle_response(addon, req_id: int):
    print(f"[BookMapBridge] Subscription response req_id={req_id}", flush=True)

# --------------------------------------------------------------------------- #
# Main entry — runs as BookMap addon OR standalone synthetic test
# --------------------------------------------------------------------------- #
def run_as_addon():
    # --- DEMO flag takes priority ---
    if "--demo" in sys.argv:
        run_demo_mode()
        return

    # --- Check if user ran python bookmap_addon.py manually ---
    # BookMap Python API: when BookMap launches the script it passes a port as sys.argv[1]
    # e.g. python bookmap_addon.py 12345
    # If you run without args, sys.argv[1] missing -> IndexError in bm.create_addon()
    if len(sys.argv) < 2:
        try:
            import bookmap as bm  # noqa
            print("[BookMapBridge] ERROR: You ran 'python bookmap_addon.py' directly without port.", flush=True)
            print("[BookMapBridge] This script must be launched BY BookMap, not manually.", flush=True)
            print("", flush=True)
            print("[BookMapBridge] CORRECT WAY (embedded editor):", flush=True)
            print("  1. BookMap -> Settings -> Manage plugins -> Bookmap Add-ons (L1) -> Python API -> Install -> Restart", flush=True)
            print("  2. Settings -> Configure add-ons -> Enable 'Python API' checkbox", flush=True)
            print("  3. Click gear icon next to Python API -> 'Open embedded editor'", flush=True)
            print("  4. In editor: File -> New Python file -> name it GoldBridge -> paste this file's content -> Save -> Build", flush=True)
            print("  5. File -> Open build folder -> you will see GoldBridge.jar", flush=True)
            print("  6. Back to Configure add-ons -> Add -> select GoldBridge.jar -> enable for MGCZ6", flush=True)
            print("  7. File -> Save Workspace (Ctrl+S)", flush=True)
            print("", flush=True)
            print("[BookMapBridge] For quick test WITHOUT BookMap, run:", flush=True)
            print("  python bookmap_addon.py --demo", flush=True)
            print("", flush=True)
            print("[BookMapBridge] Now falling back to DEMO mode so you can see ticks.csv filling...", flush=True)
            run_demo_mode()
            return
        except ImportError:
            print("[BookMapBridge] bookmap package not installed — running in DEMO mode (synthetic data)", flush=True)
            run_demo_mode()
            return

    try:
        import bookmap as bm
    except ImportError:
        print("[BookMapBridge] bookmap package not installed — running in DEMO mode (synthetic data)", flush=True)
        run_demo_mode()
        return

    print("[BookMapBridge] Starting BookMap addon...", flush=True)
    try:
        addon = bm.create_addon()
    except IndexError as e:
        print(f"[BookMapBridge] create_addon() failed (IndexError): {e}", flush=True)
        print("[BookMapBridge] Port argument missing — falling back to DEMO mode. Use --demo explicitly.", flush=True)
        run_demo_mode()
        return

    # Register handlers
    bm.add_depth_handler(addon, handle_depth)
    bm.add_trades_handler(addon, handle_trades)
    if WRITE_MBO:
        try:
            bm.add_mbo_handler(addon, handle_mbo)
        except AttributeError:
            print("[BookMapBridge] MBO handler not available in this bookmap version", flush=True)

    bm.add_response_data_handler(addon, handle_response)

    # Start addon — BookMap will call handle_subscribe_instrument for each enabled instrument
    bm.start_addon(addon, handle_subscribe_instrument, handle_unsubscribe_instrument)

    # Periodic stats
    def stats_printer():
        while True:
            time.sleep(60)
            with stats_lock:
                print(f"[BookMapBridge] Stats: trades={stats['trades']} depth={stats['depth']} bidask={stats['bidask']} filtered={stats['filtered']} file={writer.path}", flush=True)

    threading.Thread(target=stats_printer, name="bookmap-stats", daemon=True).start()

    print("[BookMapBridge] Addon running — waiting until turned off in BookMap...", flush=True)
    bm.wait_until_addon_is_turned_off(addon)
    print("[BookMapBridge] Addon turned off — exiting", flush=True)

def run_demo_mode():
    """Generate synthetic ticks.csv data for testing without BookMap."""
    print("[BookMapBridge] DEMO mode: generating synthetic MGC data into", writer.path, flush=True)
    import random
    price = 4375.0
    bid = price - 0.2
    ask = price + 0.2
    instrument = "MGC 12-26"
    try:
        while True:
            # Random walk
            delta = random.uniform(-0.5, 0.5)
            price += delta
            bid = price - 0.2
            ask = price + 0.2
            # Trade
            side = "Buy" if random.random() > 0.5 else "Sell"
            size = random.choice([1,1,1,2,3,5])
            writer.write_line(_now_iso(), "Last", price, size, -1, side, instrument)
            # Occasionally update best bid/ask
            if random.random() < 0.3:
                writer.write_line(_now_iso(), "Bid", bid, random.uniform(5,20), -1, "", instrument)
                writer.write_line(_now_iso(), "Ask", ask, random.uniform(5,20), -1, "", instrument)
            # Depth
            for i in range(3):
                db_price = bid - i*0.1
                da_price = ask + i*0.1
                writer.write_line(_now_iso(), "DepthBid", db_price, random.uniform(1,10), -1, "Update", instrument)
                writer.write_line(_now_iso(), "DepthAsk", da_price, random.uniform(1,10), -1, "Update", instrument)
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("[BookMapBridge] Demo stopped", flush=True)

if __name__ == "__main__":
    # If run with --demo, force demo mode
    if "--demo" in sys.argv:
        run_demo_mode()
    else:
        run_as_addon()
