"""
bookmap_bridge_provider.py
==========================
STEP 1 data provider: live CME gold (L1 trades/quotes + L2 full depth + optional L3 MBO)
from the BookMap bridge.

Data flow:
    BookMap (Global plan, Rithmic real-time feed, MGC/GC chart)
        + bookmap_addon_l3.py (Python API addon)
        -> ticks.csv  (time,event,price,size,level,operation,instrument)
           operation for Last = Buy/Sell when BookMap knows aggressor side
        -> mbo.csv (time,event_type,order_id,price,size,instrument) — M3 order_id tracking
        -> THIS PROVIDER (background tailers keep live price-keyed book + rolling window)
        -> Step 2 market_data schema (tick_data / bid_depth / ask_depth / book_updates / order_events)

v5.3 MEDIUM:
- M1: tick_data now includes is_direct flag preserved for footprint
- M3: mbo.csv tailed with order_id for iceberg/spoof detection
- M5: mbo.csv rotation + gzip archival (100 MB default) same as ticks.csv
- v5.2 fix: uncrossed book logic (bid < ask) preserved
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

_CATCHUP_BYTES = max(1, int(getattr(config, "BOOKMAP_CATCHUP_MB", getattr(config, "NT_CATCHUP_MB", 64)))) * 1024 * 1024
_PRUNE_INTERVAL = 2.0
# v5.6 improved: 20 bid/20 ask is Rithmic max for GC (MGC only 10), but make configurable and allow 40 for future full depth
# GC institutional = 20 levels each side, MGC micro = 10, full depth via MBO = 3000 orders
try:
    _MAX_BOOK_LEVELS = max(10, int(__import__('os').getenv('BOOKMAP_MAX_DEPTH_LEVELS', '40')))
except:
    _MAX_BOOK_LEVELS = 40

_CSV_HEADER = "time,event,price,size,level,operation,instrument\n"
_MBO_CSV_HEADER = "time,event_type,order_id,price,size,instrument\n"
_CHUNK_RE = re.compile(r"^ticks_\d{8}_\d{6}\.csv$")
_MBO_CHUNK_RE = re.compile(r"^mbo_\d{8}_\d{6}\.csv$")
_ARCHIVE_LOCK = threading.Lock()
_ARCHIVE_STATE = {"running": False}
_MBO_ARCHIVE_LOCK = threading.Lock()
_MBO_ARCHIVE_STATE = {"running": False}


def _parse_ts(raw: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(raw.strip())
    except (ValueError, AttributeError):
        return None
    if dt.tzinfo is None:
        # 24m/C5. `dt.astimezone()` on a naive value applies the offset in force RIGHT
        # NOW, not the one in force on the row's own date. Replaying a 24 Oct row on
        # 26 Oct therefore stamped it an hour wrong, while audit_day.py resolved the same
        # row with the date-aware zone - the two disagreed for exactly one day a year.
        # Attaching the zone to the row's OWN date makes both sides agree always.
        try:
            from zoneinfo import ZoneInfo
            import os as _os
            dt = dt.replace(tzinfo=ZoneInfo(_os.getenv("LOCAL_TZ", "Europe/Budapest")))
        except Exception:
            try:
                dt = dt.astimezone()
            except (OSError, ValueError):
                dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _resolve_bridge_file() -> str:
    candidates: List[str] = []
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
    candidates.append(r"C:\NinjaBridge\ticks.csv")
    for cand in candidates:
        if cand and os.path.exists(cand):
            return cand
    for key in ("BOOKMAP_BRIDGE_FILE", "NT_BRIDGE_FILE"):
        cfg = (getattr(config, key, "") or "").strip() if hasattr(config, key) else ""
        if cfg:
            return cfg
    return os.path.join(here, "ticks.csv")


def _resolve_mbo_file() -> str:
    candidates: List[str] = []
    for key in ("BOOKMAP_MBO_FILE", "MBO_FILE"):
        cfg = (getattr(config, key, "") or "").strip() if hasattr(config, key) else ""
        if cfg:
            candidates.append(cfg)
    for env_key in ("BOOKMAP_MBO_FILE", "MBO_FILE", "BM_MBO_FILE"):
        env = os.environ.get(env_key, "").strip()
        if env:
            candidates.append(env)
    here = os.path.dirname(os.path.abspath(__file__))
    # Derive from bridge file path
    bridge = _resolve_bridge_file()
    candidates.append(os.path.join(os.path.dirname(bridge) or here, "mbo.csv"))
    candidates.append(os.path.join(here, "mbo.csv"))
    candidates.append(os.path.join(os.path.dirname(here), "mbo.csv"))
    candidates.append(r"A:\gitHub\Gold-BookMap\mbo.csv")
    candidates.append(r"A:\gitHub\Rhitmic\Gold-MT5\mbo.csv")
    candidates.append(r"C:\BookMapBridge\mbo.csv")
    for cand in candidates:
        if cand and os.path.exists(cand):
            return cand
    # Even if not exists, return first candidate (cold start)
    for key in ("BOOKMAP_MBO_FILE",):
        cfg = (getattr(config, key, "") or "").strip() if hasattr(config, key) else ""
        if cfg:
            return cfg
    return os.path.join(here, "mbo.csv")


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
        self.mbo_events = []


class _BridgeTail:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self.instruments: Dict[str, _InstrumentState] = {}
        self.line_count = 0
        self.bad_lines = 0
        self.direct_side_hits = 0
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
        operation = parts[5].strip()
        dt = _parse_ts(parts[0])
        if dt is None:
            dt = datetime.now().astimezone()

        st = self._state_for(instrument)
        self.line_count += 1

        if event == "Last":
            op_low = operation.lower()
            if op_low in ("buy", "b", "bid", "sell", "s", "ask"):
                side = "BUY" if op_low in ("buy", "b") else "SELL"
                try:
                    self.direct_side_hits += 1
                except AttributeError:
                    self.direct_side_hits = 1
                is_direct = True
            else:
                if st.best_ask and price >= st.best_ask - 1e-9:
                    side = "BUY"
                elif st.best_bid and price <= st.best_bid + 1e-9:
                    side = "SELL"
                elif st.ticks:
                    side = st.ticks[-1]["side"]
                else:
                    side = "BUY"
                is_direct = False
            st.ticks.append({"price": price, "volume": size, "side": side, "ts": dt, "is_direct": is_direct, "operation": operation})
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
            try:
                # v5.3 fix: skip invalid order_ids from ticks.csv Mbo fallback (level=-1 causes 929 fake icebergs)
                raw_oid = parts[4] if len(parts) > 4 else ""
                if str(raw_oid).strip() in ("-1", "0", "", "None"):
                    return
                op_up = (operation or "").upper()
                if "BID" in op_up:
                    side = "BID"
                elif "ASK" in op_up:
                    side = "ASK"
                else:
                    side = "BID" if price and st.best_bid and price <= st.best_bid + 0.5 else "ASK"
                st.mbo_events.append({
                    "type": operation or "UNKNOWN",
                    "side": side,
                    "price": price,
                    "size": size,
                    "timestamp": dt.isoformat(),
                    "order_id": raw_oid,
                })
            except Exception:
                pass

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
            candidate = os.path.join(os.path.dirname(self.path) or ".", "ticks_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv")
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
        logger.info("BookMap bridge: rotated ticks.csv at %.0f MB -> %s", size / 1048576.0, os.path.basename(chunk))
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
                            logger.info("BookMap bridge: archived %s (%.0f MB -> %.1f MB gz)", name, raw_mb, dst.stat().st_size / 1048576.0)
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
            if st.mbo_events:
                try:
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
                        self._prune_old_ticks(max(60, int(getattr(config, "BOOKMAP_WINDOW_SECONDS", getattr(config, "NT_WINDOW_SECONDS", 28800)))))
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


# M5: MBO file tailer with order_id tracking
class _MboTail:
    def __init__(self, path: str):
        self.path = path
        self.lock = threading.Lock()
        self.instruments: Dict[str, _InstrumentState] = {}
        self.line_count = 0
        self.bad_lines = 0
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run, name="bookmap-mbo-tail", daemon=True)

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
        # Expected: time,event_type,order_id,price,size,instrument (6 cols)
        # Also support legacy 7 cols? Be flexible
        if not parts or parts[0] == "time":
            return
        if len(parts) < 5:
            self.bad_lines += 1
            return
        try:
            # Detect format
            if len(parts) == 6:
                # time,event_type,order_id,price,size,instrument
                ts_raw, ev_type, order_id, price_raw, size_raw, instrument = parts
            elif len(parts) == 7:
                # time,event,price,size,level,operation,instrument (ticks.csv Mbo fallback)
                ts_raw, ev_type, price_raw, size_raw, level_raw, op_raw, instrument = parts
                order_id = level_raw if level_raw and level_raw != "-1" else op_raw
                # ev_type is actually event (Mbo), op_raw is operation (BID_NEW etc)
                if op_raw and op_raw not in ("Add", "Remove", "Update"):
                    ev_type = op_raw
            else:
                # Try to parse as 5: time,event_type,order_id,price,size (no instrument)
                ts_raw = parts[0]
                ev_type = parts[1]
                order_id = parts[2]
                price_raw = parts[3]
                size_raw = parts[4]
                instrument = parts[5] if len(parts) > 5 else "?"
            price = float(price_raw)
            size = float(size_raw or 0)
        except ValueError:
            self.bad_lines += 1
            return

        dt = _parse_ts(ts_raw)
        if dt is None:
            dt = datetime.now().astimezone()

        st = self._state_for(instrument.strip() or "?")
        self.line_count += 1

        # Determine side
        ev_up = ev_type.upper()
        if "BID" in ev_up:
            side = "BID"
        elif "ASK" in ev_up:
            side = "ASK"
        else:
            # Infer from price vs best
            side = "BID" if price and st.best_bid and price <= st.best_bid + 0.5 else "ASK"

        # For CANCEL with 0 price, keep order_id but price 0 is ok for tracking
        st.mbo_events.append({
            "type": ev_type,
            "side": side,
            "price": price,
            "size": size,
            "timestamp": dt.isoformat(),
            "order_id": order_id.strip(),
        })
        st.last_dt = dt

    def _maybe_rotate(self, f) -> Any:
        threshold = float(getattr(config, "BOOKMAP_MBO_ROTATE_MB", 100.0) or 0) * 1048576.0
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
            candidate = os.path.join(os.path.dirname(self.path) or ".", "mbo_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv")
            try:
                os.rename(self.path, candidate)
                chunk = candidate
                break
            except OSError:
                time.sleep(0.05)
        if chunk is None:
            logger.warning("BookMap MBO: rotation deferred (rename blocked)")
            return open(self.path, "r", encoding="utf-8", errors="replace")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(_MBO_CSV_HEADER)
        logger.info("BookMap MBO: rotated mbo.csv at %.0f MB -> %s", size / 1048576.0, os.path.basename(chunk))
        self._archive_pending_async()
        return open(self.path, "r", encoding="utf-8", errors="replace")

    def _archive_pending_async(self) -> None:
        with _MBO_ARCHIVE_LOCK:
            if _MBO_ARCHIVE_STATE["running"]:
                return
            _MBO_ARCHIVE_STATE["running"] = True

        def _worker() -> None:
            try:
                folder = os.path.dirname(self.path) or "."
                while not self._stop.is_set():
                    try:
                        names = sorted(n for n in os.listdir(folder) if _MBO_CHUNK_RE.match(n))
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
                            logger.info("BookMap MBO: archived %s (%.0f MB -> %.1f MB gz)", name, raw_mb, dst.stat().st_size / 1048576.0)
                        except OSError as exc:
                            logger.warning("BookMap MBO: archiving %s failed (%s)", name, exc)
                            return
            finally:
                with _MBO_ARCHIVE_LOCK:
                    _MBO_ARCHIVE_STATE["running"] = False

        threading.Thread(target=_worker, name="bookmap-mbo-archive", daemon=True).start()

    def _prune_old(self, window: float) -> None:
        cutoff = time.time() - window
        for st in self.instruments.values():
            if not st.mbo_events:
                continue
            # Keep only recent window + max 5000
            if len(st.mbo_events) > 5000:
                st.mbo_events = st.mbo_events[-5000:]

    def _catch_up(self, f) -> None:
        try:
            size = os.path.getsize(self.path)
            catchup = max(1, int(getattr(config, "BOOKMAP_CATCHUP_MB", 64))) * 1024 * 1024
            if size > catchup:
                f.seek(size - catchup)
                f.readline()
            for line in f:
                if line.endswith("\n"):
                    self._handle_line(line)
        except OSError as exc:
            logger.warning("BookMap MBO catch-up failed: %s", exc)

    def _run(self) -> None:
        logger.info("BookMap MBO tailer: watching %s", self.path)
        if not os.path.exists(self.path):
            logger.info("BookMap MBO tailer: waiting for file (optional, L3) ...")
            # Don't block forever if mbo.csv not used
            wait = 0
            while not os.path.exists(self.path) and not self._stop.is_set() and wait < 30:
                time.sleep(1.0)
                wait += 1
            if not os.path.exists(self.path):
                logger.info("BookMap MBO tailer: mbo.csv not found after 30s, will keep watching")
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
                        self._prune_old(max(60, int(getattr(config, "BOOKMAP_WINDOW_SECONDS", 28800))))
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
                    logger.info("BookMap MBO tailer: file reset/rotated — rebuilding")
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
_MBO_TAIL: Optional[_MboTail] = None
_TAIL_LOCK = threading.Lock()


def _get_tail() -> _BridgeTail:
    global _TAIL
    with _TAIL_LOCK:
        if _TAIL is None:
            _TAIL = _BridgeTail(_resolve_bridge_file())
            _TAIL.start()
        return _TAIL


def _get_mbo_tail() -> _MboTail:
    global _MBO_TAIL
    with _TAIL_LOCK:
        if _MBO_TAIL is None:
            _MBO_TAIL = _MboTail(_resolve_mbo_file())
            _MBO_TAIL.start()
        return _MBO_TAIL


def _choose_instrument(instruments: Dict[str, _InstrumentState]) -> str:
    desired = (getattr(config, "DATA_SYMBOL", "") or "").strip()
    if desired:
        root = desired.split()[0].upper()
        for name, st in instruments.items():
            if name.split()[0].upper() == root and (st.ticks or st.bids or st.asks or st.mbo_events):
                return name
    best_name, best_score = "", -1
    for name, st in instruments.items():
        score = len(st.ticks) + len(st.bids) + len(st.asks) + len(st.mbo_events)
        if score > best_score:
            best_name, best_score = name, score
    return best_name


class BookmapBridgeProvider(BaseProvider):
    """Step-1 provider that reads the BookMap bridge file (ticks.csv) + optional mbo.csv."""

    name = "bookmapbridge"

    def __init__(self):
        super().__init__()
        self.tail = _get_tail()
        try:
            self.mbo_tail = _get_mbo_tail()
        except Exception:
            self.mbo_tail = None

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
                # M1: Preserve is_direct flag for footprint
                self.tick_data = [
                    {"price": t["price"], "volume": t["volume"], "side": t["side"],
                     "is_direct": t.get("is_direct", False), "is_bookmap_direct": t.get("is_direct", False),
                     "timestamp": t["ts"].isoformat(), "operation": t.get("operation", "")}
                    for t in st.ticks
                ]
                self._trades = [{"timestamp": t["ts"].isoformat(), "price": t["price"], "volume": t["volume"]} for t in st.ticks]
                raw_bids = dict(st.bids)
                raw_asks = dict(st.asks)
                if raw_bids and raw_asks:
                    best_bid_depth = max(raw_bids.keys()) if raw_bids else 0.0
                    best_ask_depth = min(raw_asks.keys()) if raw_asks else 0.0
                    if best_bid_depth and best_ask_depth and best_bid_depth >= best_ask_depth:
                        raw_bids = {p: s for p, s in raw_bids.items() if p < best_ask_depth}
                        raw_asks = {p: s for p, s in raw_asks.items() if p > best_bid_depth}
                        if raw_bids and raw_asks:
                            best_bid_depth = max(raw_bids.keys())
                            best_ask_depth = min(raw_asks.keys())
                            if best_bid_depth >= best_ask_depth:
                                sorted_bids = sorted(raw_bids.items(), key=lambda kv: -kv[0])
                                sorted_asks = sorted(raw_asks.items(), key=lambda kv: kv[0])
                                mid_price = 0.0
                                if st.ticks:
                                    mid_price = st.ticks[-1]["price"]
                                elif sorted_bids and sorted_asks:
                                    mid_price = (sorted_bids[0][0] + sorted_asks[0][0]) / 2.0
                                if mid_price > 0:
                                    raw_bids = {p: s for p, s in sorted_bids if p < mid_price}
                                    raw_asks = {p: s for p, s in sorted_asks if p > mid_price}
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
                # Merge MBO events from ticks.csv + mbo.csv
                mbo_from_ticks = list(st.mbo_events[-2000:]) if st.mbo_events else []
                mbo_from_file: List[Dict] = []
                if self.mbo_tail:
                    try:
                        with self.mbo_tail.lock:
                            mbo_state = self.mbo_tail.instruments.get(name)
                            if mbo_state and mbo_state.mbo_events:
                                mbo_from_file = list(mbo_state.mbo_events[-3000:])
                            else:
                                # Try any instrument with events
                                for ist in self.mbo_tail.instruments.values():
                                    if ist.mbo_events:
                                        mbo_from_file.extend(ist.mbo_events[-1000:])
                    except Exception:
                        pass
                # Combine, deduplicate by (order_id, type, price) keeping latest
                # v5.3 fix: filter invalid order_ids (-1) that cause iceberg spam
                # v7.0 P2 FIX: proper dedup to avoid double counting same MBO from ticks.csv + mbo.csv
                combined_raw = mbo_from_ticks + mbo_from_file
                filtered = [e for e in combined_raw if str(e.get("order_id","")).strip() not in ("-1","0","","None")]
                # Deduplicate: keep latest by (order_id, type, price) key
                dedup_dict = {}
                for ev in filtered:
                    key = (str(ev.get("order_id","")), str(ev.get("type","")).upper(), round(float(ev.get("price",0)),2))
                    # Keep latest timestamp (overwrite)
                    dedup_dict[key] = ev
                combined = list(dedup_dict.values())
                # Sort by timestamp to preserve order
                try:
                    combined.sort(key=lambda x: x.get("timestamp",""))
                except Exception:
                    pass
                # Keep last 5000
                if len(combined) > 2000:  # P3 M5 FAIR
                    combined = combined[-2000:]  # P3
                self.order_events = combined
                if st.last_dt is not None:
                    self._touch(st.last_dt)
            else:
                name = symbol or getattr(config, "DATA_SYMBOL", "MGC 12-26")

        symbol_out = name or symbol or getattr(config, "DATA_SYMBOL", "MGC 12-26")
        data = self.build_market_data(symbol=symbol_out)
        with self.tail.lock:
            direct = self.tail.direct_side_hits
            total = self.tail.line_count
        mbo_count = len(data.get("order_events", []))
        # v5.6 improved depth logging: show total size and spread
        try:
            bid_depth = data.get("bid_depth", {})
            ask_depth = data.get("ask_depth", {})
            total_bid_size = sum(float(v) for v in bid_depth.values()) if bid_depth else 0.0
            total_ask_size = sum(float(v) for v in ask_depth.values()) if ask_depth else 0.0
            spread = 0.0
            if bid_depth and ask_depth:
                best_bid = max(bid_depth.keys()) if bid_depth else 0.0
                best_ask = min(ask_depth.keys()) if ask_depth else 0.0
                if best_bid and best_ask:
                    spread = best_ask - best_bid
            logger.info("BookMapBridge v5.6: %d ticks (%d direct), %d bid/%.1f lots / %d ask/%.1f lots spread %.2f, %d MBO (L3) for %s (lines: %d) [GC max 20 levels, MGC 10, MBO 3000 = real depth]",
                        len(data.get("tick_data", [])), direct,
                        len(bid_depth), total_bid_size,
                        len(ask_depth), total_ask_size,
                        spread,
                        mbo_count, symbol_out, total)
        except Exception as e:
            logger.info("BookMapBridge v5.3: %d ticks (%d direct), %d bid/%d ask, %d MBO (ticks+file) for %s (lines: %d)",
                        len(data.get("tick_data", [])), direct,
                        len(data.get("bid_depth", {})), len(data.get("ask_depth", {})),
                        mbo_count, symbol_out, total)
        return data


class NinjaBridgeProvider(BookmapBridgeProvider):
    name = "ninjabridge"

class BookMapProvider(BookmapBridgeProvider):
    name = "bookmap"

BookmapBridgeProvider = BookmapBridgeProvider
