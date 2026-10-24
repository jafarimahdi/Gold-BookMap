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
import math
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
    __slots__ = ("bids", "asks", "ticks", "best_bid", "best_ask", "last_dt",
                 "mbo_events", "depth_touch", "level_ts_b", "level_ts_a")

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
        # book-hygiene-2026-10-07: last-touched timestamp per price level so
        # ghost levels expire (root cure of the frozen-quote disease).
        self.level_ts_b: Dict[float, Any] = {}
        self.level_ts_a: Dict[float, Any] = {}

    def reset(self) -> None:
        self.bids, self.asks = {}, {}
        self.ticks = []
        self.best_bid = self.best_ask = 0.0
        self.last_dt = None
        self.mbo_events = []
        self.depth_touch = []
        self.level_ts_b, self.level_ts_a = {}, {}


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
            self.bad_lines += 1
            return

        st = self._state_for(instrument)
        self.line_count += 1

        if event == "Last":
            op_low = operation.lower()
            if op_low in ("buy", "b", "sell", "s"):
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
                    side = ""  # genuinely unknown; do not fabricate BUY flow
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
                st.level_ts_b.pop(price, None)
            else:
                st.bids[price] = size
                st.level_ts_b[price] = dt
                st.depth_touch.append((dt, "B", price))
                if len(st.depth_touch) > 1200:
                    del st.depth_touch[:600]
            st.last_dt = dt
        elif event == "DepthAsk":
            if operation == "Remove" or size <= 0:
                st.asks.pop(price, None)
                st.level_ts_a.pop(price, None)
            else:
                st.asks[price] = size
                st.level_ts_a[price] = dt
                st.depth_touch.append((dt, "A", price))
                if len(st.depth_touch) > 1200:
                    del st.depth_touch[:600]
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
                # 26j/C1: this throws away the entire tick history. It fires when every
                # tick is older than the window (overnight gap, weekend, restart with
                # stale catch-up). It used to happen in complete silence, which hid the
                # cause of "candle history shallow". Say it out loud; behaviour unchanged.
                logger.warning(
                    "BookMap window: no tick newer than %.0fs - collapsing history from "
                    "%d ticks to 200. Candles/ATR/MTF will be shallow until it refills.",
                    window, len(st.ticks))
                del st.ticks[:-200]
            if st.mbo_events:
                try:
                    if len(st.mbo_events) > 5000:
                        st.mbo_events = st.mbo_events[-5000:]
                except Exception:
                    pass

    def _replay_recorded_history(self) -> Optional[int]:
        """Rebuild state from recorded history and return the live-file offset.

        The returned text-file seek cookie is the exact prefix of ``self.path``
        already replayed. ``_catch_up`` uses it to continue with rows appended
        during replay instead of rereading the same tail a second time.
        History remains sourced only from the recorded CSV stream.
        """
        replay_started = time.monotonic()
        live_offset: Optional[int] = None
        live_key = os.path.normcase(os.path.abspath(self.path))
        try:
            folder = os.path.dirname(self.path) or "."
            names = [n for n in os.listdir(folder) if _CHUNK_RE.match(n)]
            outdir = os.path.join(folder, "data", "archive")
            if os.path.isdir(outdir):
                names += [n for n in os.listdir(outdir) if n.endswith(".csv.gz")]
            names = sorted(set(names))[-2:]
            candidates = []
            for n in names:
                p1 = os.path.join(folder, n)
                p2 = os.path.join(outdir, n)
                if os.path.exists(p1):
                    candidates.append(p1)
                elif os.path.exists(p2):
                    candidates.append(p2)
            try:
                win = max(60, int(getattr(config, "BOOKMAP_WINDOW_SECONDS",
                                          getattr(config, "NT_WINDOW_SECONDS", 28800))))
            except Exception:
                win = 10800
            cutoff_ts = datetime.now().astimezone().timestamp() - (win + 300.0)
            replayed = skipped = scanned = 0
            timestamp_order_regressions = 0
            last_timestamp_epoch = None
            budget = 3000000
            # book-hygiene replay v2: the LIVE file is replayed with the same
            # time filter (kills the 64MB catch-up hole) and is always LAST.
            ordered = sorted(candidates)
            if os.path.exists(self.path) and self.path not in ordered:
                ordered = ordered + [self.path]
            for src in ordered:
                is_live_file = os.path.normcase(os.path.abspath(src)) == live_key
                source_started = time.monotonic()
                source_replayed = replayed
                source_skipped = skipped
                source_scanned = 0
                source_regressions = timestamp_order_regressions
                try:
                    opener = gzip.open if src.endswith(".gz") else open
                    with opener(src, "rt", encoding="utf-8", errors="replace") as fh:
                        for line in fh:
                            scanned += 1
                            source_scanned += 1
                            if not line.endswith("\n"):
                                continue
                            ts_raw = line.split(",", 1)[0]
                            try:
                                ts = _parse_ts(ts_raw)
                                ts_epoch = ts.timestamp() if ts is not None else None
                                if ts_epoch is not None:
                                    if (last_timestamp_epoch is not None
                                            and ts_epoch < last_timestamp_epoch):
                                        timestamp_order_regressions += 1
                                    last_timestamp_epoch = ts_epoch
                                ts_ok = (ts_epoch is not None
                                         and ts_epoch >= cutoff_ts)
                            except Exception:
                                ts_ok = True
                            if not ts_ok:
                                skipped += 1
                                continue
                            self._handle_line(line)
                            replayed += 1
                            if replayed >= budget:
                                break
                        if is_live_file:
                            # TextIOWrapper.tell() is a seek cookie, not a byte
                            # count. Reuse it on the tailer's identically-opened
                            # live file so it resumes exactly after the replayed
                            # prefix, including rows appended while replay ran.
                            live_offset = fh.tell()
                except Exception as exc:
                    if is_live_file:
                        live_offset = None
                    logger.warning("BookMap history replay failed for %s: %s",
                                   os.path.basename(src), exc)
                source_ms = (time.monotonic() - source_started) * 1000.0
                logger.info(
                    "BookMap replay source %s: scanned=%d fresh=%d stale=%d "
                    "timestamp_regressions=%d elapsed=%.0fms",
                    os.path.basename(src), source_scanned,
                    replayed - source_replayed, skipped - source_skipped,
                    timestamp_order_regressions - source_regressions, source_ms)
                if replayed >= budget:
                    break
            replay_ms = (time.monotonic() - replay_started) * 1000.0
            if replayed:
                logger.info(
                    "BookMap history replay v2: %d fresh lines kept (%d stale "
                    "skipped; %d lines scanned; %d timestamp-order regressions) "
                    "from %d recorded file(s) (single-source warm-up; window "
                    "restored; %.0fms)", replayed, skipped, scanned,
                    timestamp_order_regressions, len(ordered), replay_ms)
            else:
                logger.info(
                    "BookMap history replay v2: no fresh lines in the recorded "
                    "history (day start/overnight gap; %d stale skipped; %d "
                    "lines scanned; %d timestamp-order regressions) - "
                    "candles/ATR stay honestly shallow until the window fills "
                    "(%.0fms)", skipped, scanned, timestamp_order_regressions,
                    replay_ms)
            return live_offset
        except Exception:
            logger.exception("BookMap history replay failed")
            return None

    def _catch_up(self, f) -> None:
        try:
            live_offset = None
            if not getattr(self, "_history_replayed", False):
                self._history_replayed = True
                live_offset = self._replay_recorded_history()
            if live_offset is not None:
                # History replay already consumed this live-file prefix. Starting
                # at EOF here used to replay the last catch-up window twice,
                # inflating tick/depth state and wasting startup time. Continue
                # at the exact replay offset so concurrent appends are retained.
                f.seek(live_offset)
            else:
                # Recovery path if the replay could not expose its live offset.
                size = os.path.getsize(self.path)
                if size > _CATCHUP_BYTES:
                    f.seek(size - _CATCHUP_BYTES)
                    f.readline()
            for line in f:
                if line.endswith("\n"):
                    self._handle_line(line)
        except (OSError, ValueError) as exc:
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
            self.bad_lines += 1
            return

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


def _instrument_root(value: str) -> str:
    """Return a stable feed root for GC/MGC contracts and vendor aliases."""
    text = str(value or "").strip().upper()
    token = text.split()[0] if text else ""
    token = re.split(r"[.@:=/\\-]", token, maxsplit=1)[0]
    if token.startswith("MGC"):
        return "MGC"
    if token.startswith("GC"):
        return "GC"
    return token


_MONTH_CODES = {1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
                7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z"}
def _instrument_contract(value: str):
    """Normalize common vendor symbols and ``ROOT MM-YY`` contract labels."""
    text = str(value or "").strip().upper()
    if not text:
        return _instrument_root(""), None, None
    parts = text.split()
    token = re.split(r"[.@:=/\\]", parts[0], maxsplit=1)[0]
    match = re.fullmatch(r"(MGC|GC)([FGHJKMNQUVXZ])(\d{1,4})", token)
    if match:
        return match.group(1), match.group(2), match.group(3)
    root = _instrument_root(token)
    expiry = None
    for part in parts[1:]:
        expiry = re.fullmatch(r"(\d{1,2})-(\d{2,4})", part)
        if expiry:
            month = int(expiry.group(1))
            if month in _MONTH_CODES:
                return root, _MONTH_CODES[month], expiry.group(2)
        reverse = re.fullmatch(r"(\d{4})-(\d{1,2})", part)
        if reverse:
            month = int(reverse.group(2))
            if month in _MONTH_CODES:
                return root, _MONTH_CODES[month], reverse.group(1)
    return root, None, None


def _contract_years_match(left: str, right: str) -> bool:
    left, right = str(left), str(right)
    if left == right:
        return True
    if len(left) == 1 and right.endswith(left):
        return True
    if len(right) == 1 and left.endswith(right):
        return True
    if len(left) == 2 and len(right) == 4:
        return right.endswith(left)
    if len(right) == 2 and len(left) == 4:
        return left.endswith(right)
    return False


def _instrument_matches(desired: str, selected: str) -> bool:
    """Compare roots and expiries when the configured symbol names one."""
    desired_root, desired_month, desired_year = _instrument_contract(desired)
    selected_root, selected_month, selected_year = _instrument_contract(selected)
    if not desired_root or desired_root != selected_root:
        return False
    if desired_month is None:
        return True
    return (selected_month == desired_month and selected_year is not None and
            _contract_years_match(desired_year, selected_year))


def _choose_instrument(instruments: Dict[str, _InstrumentState]) -> str:
    """Choose the configured data contract; never silently fall back to another."""
    desired = (getattr(config, "DATA_SYMBOL", "") or "").strip()
    desired_root = _instrument_root(desired)
    if desired_root:
        active = [(name, st) for name, st in instruments.items()
                  if _instrument_root(name) == desired_root and
                  (st.ticks or st.bids or st.asks or st.mbo_events)]
        if _instrument_contract(desired)[1] is not None:
            active = [(name, st) for name, st in active
                      if _instrument_matches(desired, name)]
        return active[0][0] if len(active) == 1 else ""
    best_name, best_score = "", -1
    for name, st in instruments.items():
        score = len(st.ticks) + len(st.bids) + len(st.asks) + len(st.mbo_events)
        if score > best_score:
            best_name, best_score = name, score
    return best_name


def _fresh_depth_levels(levels: Dict[float, float], timestamps: Dict[float, Any],
                        now_epoch: float, max_age_seconds: float):
    """Return only finite, positive levels with a valid recent per-level stamp.

    Missing, future-dated, expired, or age-limit-unverified levels are rejected
    rather than allowed to influence the book. Counts are retained for diagnostics.
    """
    fresh: Dict[float, float] = {}
    fresh_ts: Dict[float, float] = {}
    counts = {"kept": 0, "missing_timestamp": 0, "future_timestamp": 0,
              "stale": 0, "invalid": 0}
    try:
        if isinstance(max_age_seconds, bool):
            raise ValueError("boolean level age limit")
        max_age_seconds = float(max_age_seconds)
        if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise ValueError("invalid level age limit")
    except (TypeError, ValueError, OverflowError):
        try:
            counts["invalid_age_limit"] = len(levels or {})
        except (TypeError, ValueError, OverflowError):
            counts["invalid_age_limit"] = 0
        return fresh, fresh_ts, counts

    for raw_price, raw_size in (levels or {}).items():
        try:
            if isinstance(raw_price, bool) or isinstance(raw_size, bool):
                counts["invalid"] += 1
                continue
            price, size = float(raw_price), float(raw_size)
            if not math.isfinite(price) or not math.isfinite(size) or price <= 0 or size <= 0:
                counts["invalid"] += 1
                continue
            stamp = (timestamps or {}).get(raw_price)
            if stamp is None:
                counts["missing_timestamp"] += 1
                continue
            if isinstance(stamp, str):
                stamp = _parse_ts(stamp)
            if stamp is None:
                counts["invalid"] += 1
                continue
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            epoch = float(stamp.timestamp())
            age = now_epoch - epoch
            if not math.isfinite(age):
                counts["invalid"] += 1
                continue
            if age < 0:
                counts["future_timestamp"] += 1
                continue
            if age > max_age_seconds:
                counts["stale"] += 1
                continue
            fresh[price] = size
            fresh_ts[price] = epoch
            counts["kept"] += 1
        except (TypeError, ValueError, OverflowError, AttributeError, OSError):
            counts["invalid"] += 1
    return fresh, fresh_ts, counts


def _top_depth_receipt(levels: Dict[float, float], timestamps: Dict[float, Any],
                       now_epoch: float, max_age_seconds: float,
                       side: str) -> List[Dict[str, Any]]:
    """Return top-five levels with per-level timestamp/age status.

    A missing or invalid age limit must never cause a raw level to be labeled
    fresh; valid prices/sizes are retained only as explicitly unverified receipt.
    """
    try:
        if isinstance(max_age_seconds, bool):
            raise ValueError("boolean level age limit")
        max_age_seconds = float(max_age_seconds)
        age_limit_valid = math.isfinite(max_age_seconds) and max_age_seconds > 0
    except (TypeError, ValueError, OverflowError):
        max_age_seconds = 0.0
        age_limit_valid = False

    candidates = []
    for raw_price, raw_size in (levels or {}).items():
        try:
            if isinstance(raw_price, bool):
                continue
            price = float(raw_price)
            if math.isfinite(price) and price > 0:
                candidates.append((price, raw_price, raw_size))
        except (TypeError, ValueError, OverflowError):
            continue
    reverse = str(side).lower() == "bid"
    candidates.sort(key=lambda row: row[0], reverse=reverse)
    receipt = []
    for price, raw_price, raw_size in candidates[:5]:
        item: Dict[str, Any] = {"price": price, "size": None,
                                "timestamp": None, "age_seconds": None,
                                "status": "invalid_size"}
        try:
            if isinstance(raw_size, bool):
                raise ValueError("boolean size")
            size = float(raw_size)
            if math.isfinite(size) and size > 0:
                item["size"] = size
                item["status"] = "missing_timestamp"
        except (TypeError, ValueError, OverflowError):
            size = None

        stamp = (timestamps or {}).get(raw_price)
        if stamp is None:
            stamp = (timestamps or {}).get(price)
        try:
            if isinstance(stamp, str):
                stamp = _parse_ts(stamp)
            if stamp is None:
                if item["status"] != "invalid_size":
                    item["status"] = "missing_timestamp"
            else:
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                epoch = float(stamp.timestamp())
                age = now_epoch - epoch
                if not math.isfinite(age):
                    item["status"] = "invalid_timestamp"
                else:
                    item["timestamp"] = stamp.isoformat()
                    item["age_seconds"] = age
                    if age < 0:
                        item["status"] = "future_timestamp"
                    elif not age_limit_valid and item["size"] is not None:
                        item["status"] = "invalid_age_limit"
                    elif age_limit_valid and age > max_age_seconds:
                        item["status"] = "stale"
                    elif item["size"] is not None:
                        item["status"] = "fresh"
        except (TypeError, ValueError, OverflowError, AttributeError, OSError):
            item["status"] = "invalid_timestamp"
        receipt.append(item)
    return receipt


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
        wait_max = float(getattr(config, "BOOKMAP_WAIT_SECONDS",
                                 getattr(config, "NT_WAIT_SECONDS", 5)))
        while waited < wait_max:
            with self.tail.lock:
                has = any(st.ticks or st.bids or st.asks
                          for st in self.tail.instruments.values())
            if has:
                break
            time.sleep(0.25)
            waited += 0.25

        # Snapshot the selected instrument under the tail lock. Never combine
        # its depth with MBO events from a different instrument.
        with self.tail.lock:
            name = _choose_instrument(self.tail.instruments)
            st = self.tail.instruments.get(name) if name else None
            if st is not None:
                ticks = list(st.ticks)
                raw_bids = dict(st.bids)
                raw_asks = dict(st.asks)
                bid_level_ts = dict(st.level_ts_b)
                ask_level_ts = dict(st.level_ts_a)
                last_data_dt = st.last_dt
                mbo_from_ticks = list(st.mbo_events[-2000:])
            else:
                ticks = []
                raw_bids, raw_asks = {}, {}
                bid_level_ts, ask_level_ts = {}, {}
                last_data_dt = None
                mbo_from_ticks = []
            direct_side_hits = self.tail.direct_side_hits
            total_lines = self.tail.line_count

        mbo_from_file: List[Dict] = []
        if name and self.mbo_tail:
            try:
                with self.mbo_tail.lock:
                    mbo_state = self.mbo_tail.instruments.get(name)
                    if mbo_state and mbo_state.mbo_events:
                        mbo_from_file = list(mbo_state.mbo_events[-3000:])
            except Exception:
                logger.exception("BookMap MBO snapshot failed for %s", name)

        combined_raw = mbo_from_ticks + mbo_from_file
        filtered = [e for e in combined_raw
                    if str(e.get("order_id", "")).strip()
                    not in ("-1", "0", "", "None", "null")]
        dedup: Dict[tuple, Dict] = {}
        for event in filtered:
            try:
                key = (str(event.get("order_id", "")),
                       str(event.get("type", "")).upper(),
                       round(float(event.get("price", 0)), 2))
                dedup[key] = event
            except (TypeError, ValueError, OverflowError):
                continue
        order_events = list(dedup.values())
        order_events.sort(key=lambda event: str(event.get("timestamp", "")))
        if len(order_events) > 2000:
            order_events = order_events[-2000:]

        # The level-age setting was previously read but not exposed by config;
        # now the documented fallback is also the actual source default.
        max_age_limit_invalid = False
        try:
            max_age = float(getattr(config, "BOOKMAP_LEVEL_MAX_AGE", 900.0))
            if not math.isfinite(max_age) or max_age <= 0:
                raise ValueError("invalid level age")
        except (TypeError, ValueError, OverflowError):
            max_age = 0.0
            max_age_limit_invalid = True
        now_epoch = time.time()
        fresh_bids, fresh_bid_ts, bid_counts = _fresh_depth_levels(
            raw_bids, bid_level_ts, now_epoch, max_age)
        fresh_asks, fresh_ask_ts, ask_counts = _fresh_depth_levels(
            raw_asks, ask_level_ts, now_epoch, max_age)

        raw_best_bid = max(fresh_bids) if fresh_bids else 0.0
        raw_best_ask = min(fresh_asks) if fresh_asks else 0.0
        crossed = bool(raw_best_bid and raw_best_ask and raw_best_bid >= raw_best_ask)
        # Do not silently "repair" a crossed book by dropping levels: that
        # invents a different book. Preserve the raw top in diagnostics and
        # pass no L2 levels to analytics until the source sends a valid book.
        if crossed:
            analysis_bids, analysis_asks = {}, {}
        else:
            analysis_bids = dict(sorted(fresh_bids.items(),
                                        key=lambda item: -item[0])[:_MAX_BOOK_LEVELS])
            analysis_asks = dict(sorted(fresh_asks.items(),
                                        key=lambda item: item[0])[:_MAX_BOOK_LEVELS])

        self.tick_data = [
            {"price": t["price"], "volume": t["volume"], "side": t["side"],
             "is_direct": bool(t.get("is_direct", False)),
             "is_bookmap_direct": bool(t.get("is_direct", False)),
             "timestamp": t["ts"].isoformat(),
             "operation": t.get("operation", "")}
            for t in ticks
        ]
        self._trades = [
            {"timestamp": t["ts"].isoformat(), "price": t["price"],
             "volume": t["volume"]} for t in ticks
        ]
        self.order_events = order_events
        self.book_updates = []
        self.bid_depth = analysis_bids
        self.ask_depth = analysis_asks
        self.order_book = {
            "bids": [(p, s) for p, s in sorted(analysis_bids.items(), reverse=True)],
            "asks": [(p, s) for p, s in sorted(analysis_asks.items())],
        }
        self.last_data_ts = last_data_dt

        symbol_out = name or symbol or getattr(config, "DATA_SYMBOL", "MGC 12-26")
        data = self.build_market_data(symbol=symbol_out)
        data["bid_depth"] = dict(analysis_bids)
        data["ask_depth"] = dict(analysis_asks)
        data["order_book"] = {"bids": list(self.order_book["bids"]),
                              "asks": list(self.order_book["asks"])}
        data["order_events"] = list(order_events)
        data["book_updates"] = []

        configured_symbol = str(getattr(config, "DATA_SYMBOL", "") or "").strip()
        desired_root = _instrument_root(configured_symbol)
        instrument_match = (not desired_root or
                            (bool(name) and _instrument_matches(
                                configured_symbol, symbol_out)))
        if not instrument_match:
            analysis_bids, analysis_asks = {}, {}
            data["bid_depth"] = {}
            data["ask_depth"] = {}
            data["order_book"] = {"bids": [], "asks": []}
            data["order_events"] = []
            data["bid"] = data["ask"] = 0.0
            self.bid_depth, self.ask_depth = {}, {}
            self.order_book = {"bids": [], "asks": []}

        best_bid = max(analysis_bids) if analysis_bids else 0.0
        best_ask = min(analysis_asks) if analysis_asks else 0.0
        quote_valid = bool(instrument_match and not crossed and
                           best_bid > 0 and best_ask > best_bid)
        quote_mid = (best_bid + best_ask) / 2.0 if quote_valid else 0.0
        quote_age = None
        quote_ts = None
        quote_stale = False
        quote_future = False
        quote_age_limit_invalid = max_age_limit_invalid
        if quote_valid:
            bid_epoch = fresh_bid_ts.get(best_bid)
            ask_epoch = fresh_ask_ts.get(best_ask)
            if bid_epoch is not None and ask_epoch is not None:
                quote_age = max(now_epoch - bid_epoch, now_epoch - ask_epoch)
                quote_ts = datetime.fromtimestamp(min(bid_epoch, ask_epoch),
                                                   tz=timezone.utc).isoformat()
                try:
                    max_quote_age = float(getattr(
                        config, "STALE_DATA_SECONDS", 300.0))
                    if not math.isfinite(max_quote_age) or max_quote_age <= 0:
                        raise ValueError("invalid quote-age limit")
                except (TypeError, ValueError, OverflowError):
                    max_quote_age = None
                if max_quote_age is None:
                    quote_valid = False
                    quote_age_limit_invalid = True
                elif not math.isfinite(quote_age) or quote_age < 0:
                    quote_valid = False
                    quote_future = True
                elif quote_age > max_quote_age:
                    quote_valid = False
                    quote_stale = True
            else:
                quote_valid = False

        latest_tick = max(ticks, key=lambda item: item["ts"].timestamp()) if ticks else None
        last_price = 0.0
        last_trade_age = None
        last_trade_ts = None
        if latest_tick is not None:
            try:
                last_price = float(latest_tick.get("price", 0.0) or 0.0)
                last_epoch = float(latest_tick["ts"].timestamp())
                last_trade_age = now_epoch - last_epoch
                last_trade_ts = latest_tick["ts"].isoformat()
                if not math.isfinite(last_trade_age) or last_trade_age < 0:
                    last_trade_age = None
            except (TypeError, ValueError, OverflowError, AttributeError, OSError):
                last_price = 0.0
                last_trade_age = None
                last_trade_ts = None

        last_order_event_epoch = None
        for event in order_events:
            try:
                raw_event_ts = event.get("timestamp")
                event_ts = (raw_event_ts if isinstance(raw_event_ts, datetime)
                            else _parse_ts(str(raw_event_ts)))
                if event_ts is None:
                    continue
                event_epoch = float(event_ts.timestamp())
                if math.isfinite(event_epoch) and event_epoch <= now_epoch:
                    last_order_event_epoch = max(
                        last_order_event_epoch or event_epoch, event_epoch)
            except (TypeError, ValueError, OverflowError, AttributeError, OSError):
                continue
        last_order_event_age = (now_epoch - last_order_event_epoch
                                if last_order_event_epoch is not None else None)
        last_order_event_ts = (datetime.fromtimestamp(
            last_order_event_epoch, tz=timezone.utc).isoformat()
            if last_order_event_epoch is not None else None)

        raw_spread_pct = (100.0 * (best_ask - best_bid) / quote_mid
                          if best_bid > 0 and best_ask > best_bid and quote_mid > 0
                          else None)
        if quote_valid:
            data["bid"], data["ask"] = best_bid, best_ask
        else:
            data["bid"] = data["ask"] = 0.0
        # Keep measured spread as telemetry when quote age/validation fails;
        # quality status below prevents it being treated as executable.
        data["spread_pct"] = raw_spread_pct
        if last_price > 0 and instrument_match:
            data["price"] = last_price
        elif quote_mid > 0:
            data["price"] = quote_mid

        if last_data_dt is not None:
            data_age = now_epoch - last_data_dt.timestamp()
            data["last_data_age_seconds"] = (data_age if math.isfinite(data_age)
                                               and data_age >= 0 else None)
            data["last_data_timestamp"] = last_data_dt.isoformat()
        else:
            data["last_data_age_seconds"] = None
            data["last_data_timestamp"] = None
        data["quote_age_seconds"] = quote_age
        data["quote_timestamp"] = quote_ts
        # Depth freshness is the older of the two current best-side levels.
        # Keep the same receipt available under the depth-specific schema too.
        data["depth_age_seconds"] = quote_age
        data["depth_timestamp"] = quote_ts
        data["last_trade_age_seconds"] = last_trade_age
        data["last_trade_timestamp"] = last_trade_ts
        data["last_order_event_age_seconds"] = last_order_event_age
        data["last_order_event_timestamp"] = last_order_event_ts

        warnings = []
        if not instrument_match:
            l2_status = "instrument_mismatch"
            quote_status = "unavailable"
            warnings.append(f"configured data root {desired_root or '?'} did not match selected instrument")
        elif crossed:
            l2_status = "crossed_book"
            quote_status = "crossed"
            warnings.append(f"crossed fresh depth: bid {raw_best_bid:.2f} >= ask {raw_best_ask:.2f}; L2 suppressed")
        elif quote_stale:
            l2_status = "stale_or_missing_levels"
            quote_status = "stale"
            warnings.append(f"quote/depth is stale at {quote_age:.3f}s; L2 suppressed")
        elif quote_future:
            l2_status = "future_timestamp"
            quote_status = "future_timestamp"
            warnings.append("quote/depth timestamp is future-dated; L2 suppressed")
        elif quote_age_limit_invalid:
            l2_status = "unverified_quote_age_limit"
            quote_status = "invalid_age_limit"
            warnings.append("quote-age threshold invalid; L2 suppressed")
        elif not (fresh_bids and fresh_asks and quote_valid):
            l2_status = "stale_or_missing_levels"
            quote_status = "unavailable"
            warnings.append("no valid fresh two-sided depth quote; L2 suppressed")
        else:
            quote_status = "valid"
            if last_price <= 0 or last_trade_age is None:
                l2_status = "unverified_last_trade_missing"
                warnings.append("no valid last-trade anchor; L2 not independently checked")
            else:
                try:
                    max_trade_age = float(getattr(config, "POWER_MAX_TICK_AGE_SECONDS", 60.0))
                    if not math.isfinite(max_trade_age) or max_trade_age <= 0:
                        max_trade_age = 60.0
                except (TypeError, ValueError, OverflowError):
                    max_trade_age = 60.0
                deviation = abs(quote_mid - last_price)
                allowed_deviation = max(1.0, 0.001 * last_price)
                if last_trade_age > max_trade_age:
                    l2_status = "unverified_last_trade_stale"
                    warnings.append(f"last-trade anchor stale ({last_trade_age:.1f}s); L2 not independently checked")
                elif deviation > allowed_deviation:
                    l2_status = "misaligned_quote_vs_last"
                    warnings.append(
                        f"fresh depth mid {quote_mid:.2f} vs last trade {last_price:.2f} "
                        f"differ by {deviation:.2f} (existing feed tolerance {allowed_deviation:.2f}); L2 suppressed")
                else:
                    l2_status = "validated"

        spread = data.get("spread_pct")
        if spread is not None and (not math.isfinite(float(spread)) or float(spread) < 0):
            data["spread_pct"] = None
            quote_status = "invalid"
            warnings.append("spread calculation invalid")
        elif spread is not None and float(spread) > 0.5:
            warnings.append(f"quote spread {float(spread):.3f}% implausible")

        direct_ticks = sum(1 for tick in self.tick_data
                           if tick.get("is_direct") is True and
                           str(tick.get("side", "")).upper() in ("BUY", "SELL"))
        valid_event_sides = {"BUY", "SELL", "BID", "ASK"}
        mbo_market_events = sum(1 for event in order_events
                                if event.get("is_market_order") is True and
                                str(event.get("side", "")).upper() in valid_event_sides)
        passive_order_events = sum(
            1 for event in order_events
            if str(event.get("type", "")).upper() in
            {"NEW", "ADD", "OPEN", "BID_NEW", "ASK_NEW"} and
            str(event.get("side", "")).upper() in valid_event_sides)
        if mbo_market_events and passive_order_events:
            l3_aggr_limit = "partial_mbo_tags"
        elif mbo_market_events:
            l3_aggr_limit = "market_order_events_only"
        elif passive_order_events:
            l3_aggr_limit = "passive_order_events_only"
        else:
            l3_aggr_limit = "not_emitted"
        try:
            from markets import MARKETS
            _market_profile = MARKETS.get(_instrument_root(symbol_out))
        except Exception:
            _market_profile = None
        tick_size = (_market_profile.tick_size
                     if _market_profile is not None else None)
        tick_value = (_market_profile.tick_value
                      if _market_profile is not None else None)
        data["data_quality"] = {
            "quote": quote_status,
            "level2": l2_status,
            "level3": "available" if order_events else "unavailable",
            "level3_events": "partial_window" if order_events else "unavailable",
            "l3_aggressive_flow": ("partial_mbo_market_order_events" if mbo_market_events else
                                    "direct_trade_prints_only" if direct_ticks else
                                    "unavailable"),
            "l3_aggr_limit": l3_aggr_limit,
            "trade_prints": "available" if ticks else "unavailable",
            "order_flow": "direct_trade_side" if direct_ticks else "tick_rule_or_unavailable",
            "instrument": "matched" if instrument_match else "mismatch",
        }
        data["feed_warnings"] = warnings
        data["l2_diagnostics"] = {
            "status": l2_status,
            "configured_data_symbol": str(getattr(config, "DATA_SYMBOL", "") or ""),
            "selected_data_symbol": symbol_out,
            "instrument_match": instrument_match,
            "fresh_level_max_age_seconds": (None if max_age_limit_invalid else max_age),
            "fresh_bid_levels": len(fresh_bids),
            "fresh_ask_levels": len(fresh_asks),
            "bid_level_counts": bid_counts,
            "ask_level_counts": ask_counts,
            "best_bid": raw_best_bid,
            "best_ask": raw_best_ask,
            "depth_mid": quote_mid,
            "top5_bid_levels": _top_depth_receipt(
                raw_bids, bid_level_ts, now_epoch, max_age, "bid"),
            "top5_ask_levels": _top_depth_receipt(
                raw_asks, ask_level_ts, now_epoch, max_age, "ask"),
            "tick_size": tick_size,
            "tick_value": tick_value,
            "price_units": ("USD per troy ounce" if _market_profile is not None
                            else "source units not verified"),
            "tick_value_units": ("USD per contract/lot" if _market_profile is not None
                                 else "source units not verified"),
            "depth_size_units": "feed-provided size; contract/lot semantics not verified",
            "last_trade": last_price,
            "last_trade_age_seconds": last_trade_age,
            "last_trade_timestamp": last_trade_ts,
            "quote_age_seconds": quote_age,
            "quote_timestamp": quote_ts,
            "depth_age_seconds": quote_age,
            "depth_timestamp": quote_ts,
            # The bundled BookMap addon stamps rows in its callback using local
            # wall time; this measures bridge receipt age, not exchange latency.
            "timestamp_basis": "bridge_receive_time",
            "source_event_time_verified": False,
            "mid_last_deviation_points": (abs(quote_mid - last_price)
                                           if quote_mid > 0 and last_price > 0 else None),
        }
        if warnings:
            logger.warning("BookMap feed quality for %s: %s", symbol_out,
                           "; ".join(warnings))
        logger.info(
            "BookMapBridge quality: %s instrument=%s quote=%s spread=%s L2=%s "
            "depth=%d/%d last_age=%s stale_levels=%d/%d MBO=%d direct_ticks=%d (lines=%d)",
            symbol_out, data["data_quality"]["instrument"], quote_status,
            f"{float(data['spread_pct']):.4f}%" if data.get("spread_pct") is not None else "n/a",
            l2_status, len(fresh_bids), len(fresh_asks),
            f"{last_trade_age:.1f}s" if last_trade_age is not None else "n/a",
            bid_counts["stale"], ask_counts["stale"], len(order_events),
            direct_ticks, total_lines)
        return data

class NinjaBridgeProvider(BookmapBridgeProvider):
    name = "ninjabridge"

class BookMapProvider(BookmapBridgeProvider):
    name = "bookmap"

BookmapBridgeProvider = BookmapBridgeProvider
