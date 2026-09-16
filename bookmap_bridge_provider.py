"""
bookmap_bridge_provider.py
==========================
STEP 1 data provider: live CME gold (L1 trades/quotes + L2 full depth + optional L3 MBO)
from the BookMap bridge.

Data flow:
    BookMap (Global plan, Rithmic real-time feed, MGC/GC chart)
        + bookmap_addon.py (Python API addon)
        -> ticks.csv  (time,event,price,size,level,operation,instrument)
           operation for Last = Buy/Sell when BookMap knows aggressor side
        -> THIS PROVIDER (background tailer keeps live price-keyed book + rolling window)
        -> Step 2 market_data schema (tick_data / bid_depth / ask_depth / book_updates)

This is the BookMap port of ninja_bridge_provider.py — behavior parity for Phase 1,
with BookMap improvements:

1. Trades carry true aggressor side (is_bid from BookMap API) in the CSV's
   operation column ("Buy"/"Sell"). Provider uses it directly for CVD,
   instead of tick-rule inference. If operation is empty, tick-rule fallback
   (same as NT) is used, so old archives keep working.

2. Full-depth L2: BookMap sends ALL price levels, not just top-of-book.
   Provider keeps up to 20 levels per side for Step 2 (same cap as before,
   but now levels are true full depth).

3. Optional MBO/L3 stream: if bookmap_addon.py writes mbo.csv (BOOKMAP_WRITE_MBO=1),
   this provider also tails it into order_events (Phase 2 votes). Main ticks.csv
   path remains the primary feed.

4. File lifecycle: same rotation + gzip archival as NT bridge (200 MB default)
   — BookMap addon only appends, Python side rotates safely while file is open.

Setup (.env):
    DATA_SOURCE=bookmapbridge   (or bookmap, or legacy ninjabridge)
    BOOKMAP_BRIDGE_FILE=        (empty = auto-detect ticks.csv next to script)
    DATA_SYMBOL=MGC 12-26       (root matters: MGC or GC)

Compatibility: NT_* keys are aliases for BOOKMAP_* (see config.py).
"""

from __future__ import annotations

import csv
import gzip
import logging
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import config
from data_providers import BaseProvider

logger = logging.getLogger(__name__)

# How much of the bridge file to re-read on start.
_CATCHUP_BYTES = max(1, int(getattr(config, "BOOKMAP_CATCHUP_MB", getattr(config, "NT_CATCHUP_MB", 64)))) * 1024 * 1024
_PRUNE_INTERVAL = 2.0
_MAX_BOOK_LEVELS = 20

_CSV_HEADER = "time,event,price,size,level,operation,instrument\n"
_CHUNK_RE = re.compile(r"^ticks_\d{8}_\d{6}\.csv$")
_ARCHIVE_LOCK = threading.Lock()
_ARCHIVE_STATE = {"running": False}


def _parse_ts(raw: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(raw.strip())
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        try:
            dt = dt.astimezone()
        except (OSError, ValueError):
            dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _resolve_bridge_file() -> str:
    """Find ticks.csv: BOOKMAP_BRIDGE_FILE -> NT_BRIDGE_FILE -> env -> auto."""
    candidates: List[str] = []
    # New keys first
    for key in ("BOOKMAP_BRIDGE_FILE", "NT_BRIDGE_FILE", "BM_BRIDGE_FILE"):
        cfg = (getattr(config, key, "") or "").strip() if hasattr(config, key) else ""
        if cfg:
            candidates.append(cfg)
    for env_key in ("BOOKMAP_BRIDGE_FILE", "NT_BRIDGE_FILE", "BM_BRIDGE_FILE", "BOOKMAP_FILE"):
        env = os.environ.get(env_key, "").strip()
        if env:
            candidates.append(env)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "ticks.csv"))
    candidates.append(os.path.join(os.path.dirname(here), "ticks.csv"))
    candidates.append(r"A:\gitHub\Rhitmic\Gold-MT5\ticks.csv")
    candidates.append(r"A:\gitHub\Rhitmic\ticks.csv")
    candidates.append(r"C:\BookMapBridge\ticks.csv")
    candidates.append(r"C:\NinjaBridge\ticks.csv")  # legacy fallback
    for cand in candidates:
        if cand and os.path.exists(cand):
            return cand
    # If explicit path given but not exist yet, return it (cold start)
    for key in ("BOOKMAP_BRIDGE_FILE", "NT_BRIDGE_FILE"):
        cfg = (getattr(config, key, "") or "").strip() if hasattr(config, key) else ""
        if cfg:
            return cfg
    return os.path.join(here, "ticks.csv")


def _file_identity(path: str):
    try:
        st = os.stat(path)
        return (st.st_dev, st.st_ino)
    except OSError:
        return (None, None)


class _InstrumentState:
    __slots__ = ("bids", "asks", "ticks", "best_bid", "best_ask", "last_dt", "mbo_events")

    def __init__(self):
        self.bids: Dict[float, float] = {}
        self.asks: Dict[float, float] = {}
        self.ticks: List[Dict[str, Any]] = []  # {price, volume, side, ts, is_bookmap_direct}
        self.best_bid: float = 0.0
        self.best_ask: float = 0.0
        self.last_dt: Optional[datetime] = None
        self.mbo_events: List[Dict[str, Any]] = []

    def reset(self) -> None:
        self.bids, self.asks = {}, {}
        self.ticks = []
        self.best_bid = self.best_ask = 0.0
        self.last_dt = None
        self.mbo_events = []


class _BridgeTail:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self.instruments: Dict[str, _InstrumentState] = {}
        self.line_count = 0
        self.bad_lines = 0
        self.direct_side_hits = 0  # how many trades used BookMap direct side
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="bookmap-bridge-tail", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _state_for(self, instrument: str) -> _InstrumentState:
        st = self.instruments.get(instrument)
        if st is None:
            st = _InstrumentState()
            self.instruments[instrument] = st
        return st

    def _handle_line(self, line: str) -> None:
        try:
            parts = next(csv.reader([line.rstrip("\r\n")]))
        except (StopIteration, csv.Error):
            self.bad_lines += 1
            return
        if len(parts) != 7 or parts[0] == "time":
            # header or malformed
            if parts and parts[0] != "time":
                self.bad_lines += 1
            return
        try:
            price = float(parts[2])
            size = float(parts[3] or 0)
        except ValueError:
            self.bad_lines += 1
            return
        event = parts[1].strip()
        instrument = parts[6].strip() or "?"
        operation = parts[5].strip()  # BookMap: Buy/Sell for Last, Add/Update/Remove for Depth
        dt = _parse_ts(parts[0])
        if dt is None:
            dt = datetime.now().astimezone()

        st = self._state_for(instrument)
        self.line_count += 1

        if event == "Last":
            # BookMap improvement: operation carries true aggressor side
            op_low = operation.lower()
            if op_low in ("buy", "b", "bid", "sell", "s", "ask"):
                if op_low in ("buy", "b"):
                    side = "BUY"
                else:
                    side = "SELL"
                try:
                    self.direct_side_hits += 1
                except AttributeError:
                    self.direct_side_hits = 1
                is_direct = True
            else:
                # Fallback: tick rule (same as NT bridge, keeps old archives working)
                if st.best_ask and price >= st.best_ask - 1e-9:
                    side = "BUY"
                elif st.best_bid and price <= st.best_bid + 1e-9:
                    side = "SELL"
                elif st.ticks:
                    side = st.ticks[-1]["side"]
                else:
                    side = "BUY"
                is_direct = False
            st.ticks.append({"price": price, "volume": size, "side": side, "ts": dt, "is_direct": is_direct})
            st.last_dt = dt

        elif event == "Bid":
            st.best_bid = price
            st.last_dt = dt
        elif event == "Ask":
            st.best_ask = price
            st.last_dt = dt
        elif event == "DepthBid":
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
            st.last_dt = dt
        elif event in ("Mbo", "MBO", "Order"):
            # L3 MBO stream in same file: operation = BID_NEW/ASK_NEW/CANCEL/REPLACE etc
            # For CANCEL price=0 size=0 is sentinel, keep but mark
            try:
                # Determine side from operation name if possible
                op_up = (operation or "").upper()
                if "BID" in op_up:
                    side = "BID"
                elif "ASK" in op_up:
                    side = "ASK"
                else:
                    side = "BID" if price and st.best_bid and price <= st.best_bid + 0.5 else "ASK"
                # Skip sentinel cancel from L2 analytics but keep for order flow
                st.mbo_events.append({
                    "type": operation or "UNKNOWN",
                    "side": side,
                    "price": price,
                    "size": size,
                    "timestamp": dt.isoformat(),
                })
            except Exception:
                pass
        # unknown events ignored silently (forward compatible)

    def _maybe_rotate(self, f) -> Any:
        threshold = float(getattr(config, "BOOKMAP_ROTATE_MB", getattr(config, "NT_ROTATE_MB", 200.0)) or 0) * 1048576.0
        if threshold <= 0:
            return f
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return f
        if size < threshold:
            return f

        try:
            f.close()
        except OSError:
            pass
        chunk = None
        for _ in range(20):
            candidate = os.path.join(
                os.path.dirname(self.path) or ".",
                "ticks_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv")
            try:
                os.rename(self.path, candidate)
                chunk = candidate
                break
            except OSError:
                time.sleep(0.05)
        if chunk is None:
            logger.warning("BookMap bridge: ticks.csv rotation deferred (rename blocked); retrying")
            return open(self.path, "r", encoding="utf-8", errors="replace")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(_CSV_HEADER)
        logger.info("BookMap bridge: rotated ticks.csv at %.0f MB -> %s",
                    size / 1048576.0, os.path.basename(chunk))
        self._archive_pending_async()
        return open(self.path, "r", encoding="utf-8", errors="replace")

    def _archive_pending_async(self) -> None:
        with _ARCHIVE_LOCK:
            if _ARCHIVE_STATE["running"]:
                return
            _ARCHIVE_STATE["running"] = True

        def _worker() -> None:
            try:
                folder = os.path.dirname(self.path) or "."
                while not self._stop.is_set():
                    try:
                        names = sorted(n for n in os.listdir(folder) if _CHUNK_RE.match(n))
                    except OSError:
                        return
                    if not names:
                        return
                    outdir = Path(__file__).resolve().parent / "data" / "archive"
                    outdir.mkdir(parents=True, exist_ok=True)
                    for name in names:
                        src = os.path.join(folder, name)
                        dst = outdir / (name + ".gz")
                        try:
                            if dst.exists():
                                os.remove(src)
                                continue
                            tmp = outdir / (name + ".gz.part")
                            with open(src, "rb") as fin, gzip.open(tmp, "wb", 6) as fout:
                                shutil.copyfileobj(fin, fout, 1 << 20)
                            with gzip.open(tmp, "rb") as v:
                                while v.read(1 << 20):
                                    pass
                            os.replace(tmp, dst)
                            raw_mb = os.path.getsize(src) / 1048576.0
                            os.remove(src)
                            logger.info("BookMap bridge: archived %s (%.0f MB -> %.1f MB gz)",
                                        name, raw_mb, dst.stat().st_size / 1048576.0)
                        except OSError as exc:
                            logger.warning("BookMap bridge: archiving %s failed (%s); retry", name, exc)
                            return
            finally:
                with _ARCHIVE_LOCK:
                    _ARCHIVE_STATE["running"] = False

        threading.Thread(target=_worker, name="bookmap-bridge-archive", daemon=True).start()

    def _prune_old_ticks(self, window: float) -> None:
        cutoff = time.time() - window
        for st in self.instruments.values():
            if not st.ticks:
                continue
            first_fresh = None
            for i, t in enumerate(st.ticks):
                if t["ts"].timestamp() >= cutoff:
                    first_fresh = i
                    break
            if first_fresh is not None:
                if first_fresh:
                    del st.ticks[:first_fresh]
            elif len(st.ticks) > 200:
                del st.ticks[:-200]
            # prune MBO events same window
            if st.mbo_events:
                try:
                    # keep last 5000 MBO events max
                    if len(st.mbo_events) > 5000:
                        st.mbo_events = st.mbo_events[-5000:]
                except Exception:
                    pass

    def _catch_up(self, f) -> None:
        try:
            size = os.path.getsize(self.path)
            if size > _CATCHUP_BYTES:
                f.seek(size - _CATCHUP_BYTES)
                f.readline()
            for line in f:
                if line.endswith("\n"):
                    self._handle_line(line)
        except OSError as exc:
            logger.warning("BookMap bridge catch-up read failed: %s", exc)

    def _run(self) -> None:
        logger.info("BookMap bridge tailer: watching %s", self.path)
        if not os.path.exists(self.path):
            logger.info("BookMap bridge tailer: waiting for file to appear ...")
        while not os.path.exists(self.path) and not self._stop.is_set():
            time.sleep(1.0)
        if self._stop.is_set():
            return

        f = open(self.path, "r", encoding="utf-8", errors="replace")
        ident = _file_identity(self.path)
        with self.lock:
            self._catch_up(f)
        self._archive_pending_async()
        last_prune = time.time()

        while not self._stop.is_set():
            pos = f.tell()
            line = f.readline()
            if line.endswith("\n"):
                with self.lock:
                    self._handle_line(line)
                    now = time.time()
                    if now - last_prune >= _PRUNE_INTERVAL:
                        last_prune = now
                        self._prune_old_ticks(
                            max(60, int(getattr(config, "BOOKMAP_WINDOW_SECONDS", getattr(config, "NT_WINDOW_SECONDS", 28800)))))
                        nf = self._maybe_rotate(f)
                        if nf is not f:
                            f = nf
                            ident = _file_identity(self.path)
                continue

            if line:
                f.seek(pos)
                time.sleep(0.05)
                continue

            time.sleep(0.25)
            try:
                gone = not os.path.exists(self.path)
                replaced = (not gone) and (_file_identity(self.path) != ident or os.path.getsize(self.path) < f.tell())
                if gone or replaced:
                    logger.info("BookMap bridge tailer: file reset/rotated — rebuilding")
                    f.close()
                    while not os.path.exists(self.path) and not self._stop.is_set():
                        time.sleep(1.0)
                    if self._stop.is_set():
                        return
                    f = open(self.path, "r", encoding="utf-8", errors="replace")
                    ident = _file_identity(self.path)
                    with self.lock:
                        for st in self.instruments.values():
                            st.reset()
                        self._catch_up(f)
            except OSError:
                pass


_TAIL: Optional[_BridgeTail] = None
_TAIL_LOCK = threading.Lock()


def _get_tail() -> _BridgeTail:
    global _TAIL
    with _TAIL_LOCK:
        if _TAIL is None:
            _TAIL = _BridgeTail(_resolve_bridge_file())
            _TAIL.start()
        return _TAIL


def _choose_instrument(instruments: Dict[str, _InstrumentState]) -> str:
    desired = (getattr(config, "DATA_SYMBOL", "") or "").strip()
    if desired:
        root = desired.split()[0].upper()
        for name, st in instruments.items():
            if name.split()[0].upper() == root and (st.ticks or st.bids or st.asks):
                return name
    best_name, best_score = "", -1
    for name, st in instruments.items():
        score = len(st.ticks) + len(st.bids) + len(st.asks)
        if score > best_score:
            best_name, best_score = name, score
    return best_name


class BookmapBridgeProvider(BaseProvider):
    """Step-1 provider that reads the BookMap bridge file (ticks.csv)."""

    name = "bookmapbridge"

    def __init__(self):
        super().__init__()
        self.tail = _get_tail()

    def acquire(self, symbol: str = "", **kwargs) -> Dict[str, Any]:
        waited = 0.0
        wait_max = float(getattr(config, "BOOKMAP_WAIT_SECONDS", getattr(config, "NT_WAIT_SECONDS", 5)))
        while waited < wait_max:
            with self.tail.lock:
                has = any(st.ticks or st.bids or st.asks for st in self.tail.instruments.values())
            if has:
                break
            time.sleep(0.25)
            waited += 0.25

        with self.tail.lock:
            name = _choose_instrument(self.tail.instruments)
            st = self.tail.instruments.get(name)
            if st is not None:
                self.tick_data = [{"price": t["price"], "volume": t["volume"], "side": t["side"]} for t in st.ticks]
                self._trades = [{"timestamp": t["ts"].isoformat(), "price": t["price"], "volume": t["volume"]} for t in st.ticks]
                raw_bids = dict(st.bids)
                raw_asks = dict(st.asks)
                if st.best_ask:
                    kept = {p: s for p, s in raw_bids.items() if p <= st.best_ask}
                    if kept:
                        raw_bids = kept
                if st.best_bid:
                    kept = {p: s for p, s in raw_asks.items() if p >= st.best_bid}
                    if kept:
                        raw_asks = kept
                raw_bids = dict(sorted(raw_bids.items(), key=lambda kv: -kv[0])[:_MAX_BOOK_LEVELS])
                raw_asks = dict(sorted(raw_asks.items(), key=lambda kv: kv[0])[:_MAX_BOOK_LEVELS])
                self.bid_depth = raw_bids
                self.ask_depth = raw_asks
                self.order_book = {
                    "bids": [(p, s) for p, s in sorted(raw_bids.items(), reverse=True)],
                    "asks": [(p, s) for p, s in sorted(raw_asks.items())],
                }
                # Optional: carry MBO events forward as order_events for Phase 2 L3 votes
                self.order_events = list(st.mbo_events[-2000:]) if st.mbo_events else []
                if st.last_dt is not None:
                    self._touch(st.last_dt)
            else:
                name = symbol or getattr(config, "DATA_SYMBOL", "MGC 12-26")

        symbol_out = name or symbol or getattr(config, "DATA_SYMBOL", "MGC 12-26")
        data = self.build_market_data(symbol=symbol_out)
        # Preserve direct-side ratio for logging / future metrics
        with self.tail.lock:
            direct = self.tail.direct_side_hits
            total = self.tail.line_count
        logger.info("BookMapBridge provider: %d ticks (%d direct side), %d bid, %d ask levels for %s (lines: %d)",
                    len(data.get("tick_data", [])), direct,
                    len(data.get("bid_depth", {})), len(data.get("ask_depth", {})),
                    symbol_out, total)
        return data


# Backward compatibility aliases — so DATA_SOURCE=ninjabridge still works
class NinjaBridgeProvider(BookmapBridgeProvider):
    name = "ninjabridge"

class BookMapProvider(BookmapBridgeProvider):
    name = "bookmap"

# Extra alias for config that expects "bookmapbridge" name
BookmapBridgeProvider = BookmapBridgeProvider
