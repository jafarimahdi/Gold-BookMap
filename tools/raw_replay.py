#!/usr/bin/env python3
"""
tools/raw_replay.py -- Law #8 Current-Version Raw-Data Replay & Diagnostic Engine
=================================================================================
Permanent Law #8 (2026-10-31):
  Historical decisions, ledgers, and reports written by older versions of the app
  reflect bugs, weights, and retired judges that no longer exist. Never trust old
  app outputs to judge the current app. Always feed past days' RAW market data
  (ticks_*.csv[.gz] + mbo_*.csv[.gz]) through the CURRENT app version to discover
  how today's system behaves, where it is strong, and where it is weak.

What this tool does (100% read-only on live trading state, $0 API cost):
  1. Discovers raw BookMap `ticks` (Last prints + DepthBid/DepthAsk/Bid/Ask L2)
     and raw `mbo` (ADD/REPLACE/CANCEL/FILL order_id L3 events) for 1..N past days.
  2. Streams them strictly chronologically (zero look-ahead) into an isolated,
     thread-free `BookmapBridgeProvider` + `step2_market_analysis.analyze_market`
     running the CURRENT codebase and current `.env` configuration.
  3. Grades every cycle ("Every decision: each cycle, the panel behind it, and
     what happened next") against the real forward trade tape:
       - +5m / +15m / +30m price moves & 30m MFE ("in favour") / MAE ("against")
       - Benchmark Fixed SL/TP ruler (2.0x / 3.5x ATR_CONST, 180m hold, spread)
       - Current C3 Wall + 0.5xATR Escort Trail vs wider 1.0xATR Trail
       - A2 Sprint 1.80/1.80 bracket vs 1.0xATR-floored bracket (Bookmark #8)
       - Every active judge graded on its own horizon clock (15m..120m)
  4. Writes:
       - data/current_version_decisions_<DATE>.html  ("Every decision" interactive page)
       - data/current_version_replay.html            (Multi-day strengths & weaknesses)
       - data/current_version_replay.txt             (Compact shareable plain-text report)

Usage (Windows Git Bash, project root):
    python tools/raw_replay.py --days 11
    python tools/raw_replay.py --date 2026-10-30
    python tools/raw_replay.py --dates 2026-10-27,2026-10-28,2026-10-29,2026-10-30
"""
from __future__ import annotations

import argparse
import bisect
import concurrent.futures
import csv
import glob
import gzip
import html
import io
import json
import math
import os
import re
import sys
import tempfile
import time
import webbrowser
import collections
from collections import Counter, defaultdict, deque
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

try:
    from zoneinfo import ZoneInfo
    BUDA_TZ = ZoneInfo("Europe/Budapest")
except Exception:  # pragma: no cover
    BUDA_TZ = timezone(timedelta(hours=1))

import bookmap_bridge_provider as bbp
import config
import judge_families
import session_levels
import shadow_replay as sr
import step2_market_analysis as s2
from tools import insight as ins


# --------------------------------------------------------------------------- #
# Helpers: formatting, timestamps, and raw file discovery
# --------------------------------------------------------------------------- #
def _esc(x: Any) -> str:
    return html.escape(str(x if x is not None else ""))


def _fmt(x: Any, nd: int = 1, sign: bool = False) -> str:
    try:
        v = float(x)
        if not math.isfinite(v):
            return "-"
    except Exception:
        return "-"
    return f"{v:+.{nd}f}" if sign else f"{v:.{nd}f}"


_ISO_SEC_CACHE: Dict[Tuple[str, str], float] = {}


def _parse_ts_utc(raw: str) -> Optional[datetime]:
    s = (raw or "").strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        try:
            dt = datetime.fromisoformat(s.replace(" ", "T", 1))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=BUDA_TZ)
    return dt.astimezone(timezone.utc)


def _fast_ts_epoch(raw: str) -> Optional[float]:
    """20x faster exact ISO-8601 -> UTC epoch parser for multi-million-row MBO/tick CSVs."""
    s = raw.strip()
    n = len(s)
    if n < 19 or s[4] != "-" or s[7] != "-" or s[13] != ":" or s[16] != ":":
        dt = _parse_ts_utc(s)
        return dt.timestamp() if dt is not None else None
    sec19 = s[:19]
    rest = s[19:]
    frac = 0.0
    tz_str = ""
    if rest:
        if rest[-1] in ("Z", "z"):
            tz_str = "Z"
            mid = rest[:-1]
        else:
            p_plus = rest.find("+")
            p_minus = rest.find("-")
            p_tz = p_plus if p_plus >= 0 else p_minus
            if p_tz >= 0:
                tz_str = rest[p_tz:]
                mid = rest[:p_tz]
            else:
                mid = rest
        if mid:
            if mid[0] == "." and mid[1:].isdigit():
                frac = int(mid[1:]) / (10.0 ** (len(mid) - 1))
            else:
                dt = _parse_ts_utc(s)
                return dt.timestamp() if dt is not None else None
    key = (sec19, tz_str)
    base_ep = _ISO_SEC_CACHE.get(key)
    if base_ep is None:
        probe = sec19 + ("+00:00" if tz_str == "Z" else tz_str)
        dt = _parse_ts_utc(probe)
        if dt is None:
            dt2 = _parse_ts_utc(s)
            return dt2.timestamp() if dt2 is not None else None
        base_ep = dt.timestamp()
        if len(_ISO_SEC_CACHE) > 200_000:
            _ISO_SEC_CACHE.clear()
        _ISO_SEC_CACHE[key] = base_ep
    return base_ep + frac


def _discover_raw_files(
    kind: str,
    root_dir: Path,
    data_dir: Path,
    day_str: Optional[str] = None,
    use_env_paths: Optional[bool] = None,
) -> List[Path]:
    """Return ordered raw CSV / CSV.GZ paths for `ticks` or `mbo` without mutating anything.

    `BOOKMAP_BRIDGE_FILE` / `BOOKMAP_MBO_FILE` from `.env` are consulted ONLY when
    replaying the real repository (`root_dir == REPO_ROOT`), never when an explicit
    custom `--root-dir` / `--data-dir` (e.g. unit-test `tmp_path`) is supplied.
    """
    assert kind in ("ticks", "mbo")
    if use_env_paths is None:
        try:
            use_env_paths = (
                root_dir.resolve() == ROOT.resolve()
                and data_dir.resolve() == (ROOT / "data").resolve()
            )
        except OSError:
            use_env_paths = False

    seen_real: set[str] = set()
    chunks: List[Path] = []
    live_files: List[Path] = []

    def _add(p: Optional[Path], is_live: bool = False) -> None:
        if p is None or not p.exists() or not p.is_file():
            return
        rp = os.path.realpath(str(p))
        if rp in seen_real:
            return
        seen_real.add(rp)
        if is_live:
            live_files.append(p)
        else:
            chunks.append(p)

    compact = day_str.replace("-", "") if day_str else None
    allowed_compacts: Optional[set[str]] = None
    if day_str:
        d_obj = date.fromisoformat(day_str)
        # Allow 3 prior calendar days for `ticks` so Monday discovers Friday + Sunday prints
        # for H1/M15 MTF & PDH/PDL/Prev_Settlement; keep `mbo` at 1 prior day for speed.
        lb_days = 3 if kind == "ticks" else 1
        allowed_compacts = {f"{d_obj + timedelta(days=1):%Y%m%d}", compact}
        for d_back in range(1, lb_days + 1):
            allowed_compacts.add(f"{d_obj - timedelta(days=d_back):%Y%m%d}")

    search_dirs = [root_dir, data_dir, data_dir / "archive", root_dir / "data", root_dir / "data" / "archive"]
    env_key = "BOOKMAP_BRIDGE_FILE" if kind == "ticks" else "BOOKMAP_MBO_FILE"
    env_val = (os.environ.get(env_key) or str(getattr(config, env_key, "") or "")).strip() if use_env_paths else ""
    if env_val:
        ep = Path(env_val)
        if ep.parent.exists():
            search_dirs.insert(0, ep.parent)
            search_dirs.append(ep.parent / "data" / "archive")

    for d in search_dirs:
        if not d.exists():
            continue
        for pat in (f"{kind}_*.csv", f"{kind}_*.csv.gz"):
            for f in sorted(d.glob(pat)):
                if allowed_compacts is not None:
                    m = re.match(rf"^{kind}_(\d{{8}})_", f.name, re.I)
                    if m and m.group(1) not in allowed_compacts:
                        continue
                _add(f, is_live=False)

    if env_val:
        _add(Path(env_val), is_live=True)
    _add(root_dir / f"{kind}.csv", is_live=True)
    _add(data_dir / f"{kind}.csv", is_live=True)

    chunks.sort(key=lambda p: p.name)
    return chunks + live_files


def discover_available_days(root_dir: Path, data_dir: Path, max_days: int = 11) -> List[str]:
    """Find trading days that actually have raw `ticks` rows on disk (newest N)."""
    found: set[str] = set()
    files = _discover_raw_files("ticks", root_dir, data_dir, day_str=None)
    date_re = re.compile(r"^(\d{4}-\d{2}-\d{2})[T ]")
    chunk_re = re.compile(r"^ticks_(\d{4})(\d{2})(\d{2})_", re.I)

    for f in files:
        m = chunk_re.match(f.name)
        if m:
            cand = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            try:
                date.fromisoformat(cand)
                found.add(cand)
            except ValueError:
                pass

    # Also sample live ticks.csv for dates that have not rotated yet
    for f in files:
        if f.name.lower() == "ticks.csv":
            op = gzip.open if f.name.endswith(".gz") else open
            try:
                with op(f, "rt", encoding="utf-8", errors="replace") as fh:
                    for idx, line in enumerate(fh):
                        if idx % 200 != 0 and idx > 2000:
                            continue
                        if ",Last," not in line and ",Trade," not in line:
                            continue
                        dm = date_re.match(line)
                        if dm:
                            found.add(dm.group(1))
            except OSError:
                continue

    ordered = sorted(found)
    if max_days > 0 and len(ordered) > max_days:
        ordered = ordered[-max_days:]
    return ordered


# --------------------------------------------------------------------------- #
# Offline non-threaded BookmapBridgeProvider
# --------------------------------------------------------------------------- #
class _OfflineBridgeProvider(bbp.BookmapBridgeProvider):
    """Exact BookmapBridgeProvider.acquire() without background threads or live files."""

    def __init__(self, tail: bbp._BridgeTail, mbo_tail: Optional[bbp._MboTail]):
        bbp.BaseProvider.__init__(self)
        self.tail = tail
        self.mbo_tail = mbo_tail
        self._prebuilt_candles: Optional[Dict[str, np.ndarray]] = None

    def build_market_data(self, symbol: str, candles: Optional[Dict] = None,
                          macro: Optional[Dict] = None,
                          news: Optional[Dict] = None) -> Dict[str, Any]:
        use_candles = candles if candles is not None else self._prebuilt_candles
        return super().build_market_data(symbol=symbol, candles=use_candles, macro=macro, news=news)


class _IncrementalM1Builder:
    """Maintains exact 1-minute OHLCV candles from raw Last prints in O(1) per print."""

    def __init__(self, max_bars: int = 360):
        self.max_bars = max_bars
        self.bars: Dict[int, List[float]] = {}

    def add_trade(self, epoch_s: float, price: float, size: float) -> None:
        if price <= 0 or not math.isfinite(price):
            return
        vol = size if (math.isfinite(size) and size > 0) else 1.0
        b = int(epoch_s // 60) * 60
        cur = self.bars.get(b)
        if cur is None:
            self.bars[b] = [price, price, price, price, vol]
            if len(self.bars) > self.max_bars + 60:
                for old_k in sorted(self.bars.keys())[:-self.max_bars]:
                    self.bars.pop(old_k, None)
        else:
            if price > cur[1]:
                cur[1] = price
            if price < cur[2]:
                cur[2] = price
            cur[3] = price
            cur[4] += vol

    def to_candles_dict(self, max_tail: Optional[int] = None) -> Dict[str, np.ndarray]:
        if not self.bars:
            return {}
        cap = max_tail if (max_tail is not None and max_tail > 0) else self.max_bars
        keys = sorted(self.bars.keys())[-cap:]
        return {
            "open": np.array([self.bars[k][0] for k in keys], dtype=float),
            "high": np.array([self.bars[k][1] for k in keys], dtype=float),
            "low": np.array([self.bars[k][2] for k in keys], dtype=float),
            "close": np.array([self.bars[k][3] for k in keys], dtype=float),
            "volume": np.array([self.bars[k][4] for k in keys], dtype=float),
        }

    def to_resampled_dict(
        self,
        bucket_min: int,
        completed_before_ep: Optional[float] = None,
    ) -> Dict[str, np.ndarray]:
        """Resample M1 bars by true UTC wall-clock buckets (immune to empty overnight minutes).
        When `completed_before_ep` is given, only closed buckets (`bk + b_sec <= completed_before_ep`)
        are emitted so RVOL / EMA / sweep checks never compare a newborn partial bar against full bars.
        """
        if not self.bars or bucket_min <= 1:
            return self.to_candles_dict()
        b_sec = int(bucket_min) * 60
        grouped: Dict[int, List[float]] = {}
        for k in sorted(self.bars.keys()):
            if completed_before_ep is not None and k > completed_before_ep:
                break
            bk = (k // b_sec) * b_sec
            if completed_before_ep is not None and (bk + b_sec) > (completed_before_ep + 1e-6):
                continue
            o, h, l, c, v = self.bars[k]
            g = grouped.get(bk)
            if g is None:
                grouped[bk] = [o, h, l, c, v]
            else:
                if h > g[1]:
                    g[1] = h
                if l < g[2]:
                    g[2] = l
                g[3] = c
                g[4] += v
        if not grouped:
            return {}
        gkeys = sorted(grouped.keys())
        return {
            "open": np.array([grouped[k][0] for k in gkeys], dtype=float),
            "high": np.array([grouped[k][1] for k in gkeys], dtype=float),
            "low": np.array([grouped[k][2] for k in gkeys], dtype=float),
            "close": np.array([grouped[k][3] for k in gkeys], dtype=float),
            "volume": np.array([grouped[k][4] for k in gkeys], dtype=float),
        }


def _cme_session_open_ep(d_obj: date) -> float:
    """Return exact UTC epoch of 18:00 US/Eastern on `d_obj - 1 day` (CME Globex session open)."""
    target = d_obj.isoformat()
    base = datetime(d_obj.year, d_obj.month, d_obj.day, 0, 0, tzinfo=timezone.utc) - timedelta(hours=12)
    for m_off in range(0, 15 * 60, 15):
        cand = base + timedelta(minutes=m_off)
        if session_levels._cme_trade_day(session_levels._to_us_eastern(cand)) == target:
            return cand.timestamp()
    return (datetime(d_obj.year, d_obj.month, d_obj.day, 0, 0, tzinfo=timezone.utc) - timedelta(hours=6)).timestamp()


def _fast_mbo_track_only(mbo_tail: bbp._MboTail, line: str) -> None:
    """Exact `active_orders` + `best_bid`/`best_ask` state transition for evicted (>3000-ago) MBO rows."""
    s = line.rstrip("\r\n")
    parts = s.split(",") if '"' not in s else next(csv.reader([s]))
    if len(parts) < 5:
        mbo_tail.bad_lines += 1
        return
    try:
        if len(parts) == 6:
            _ts_raw, ev_type, order_id, price_raw, size_raw, instrument = parts
        elif len(parts) == 7:
            _ts_raw, ev_type, price_raw, size_raw, level_raw, op_raw, instrument = parts
            order_id = level_raw if level_raw and level_raw != "-1" else op_raw
            if op_raw and op_raw not in ("Add", "Remove", "Update"):
                ev_type = op_raw
        else:
            _ts_raw, ev_type, order_id, price_raw, size_raw = parts[:5]
            instrument = parts[5] if len(parts) > 5 else "?"
        price = float(price_raw)
        size = float(size_raw or 0)
    except ValueError:
        mbo_tail.bad_lines += 1
        return

    st = mbo_tail._state_for(instrument.strip() or "?")
    mbo_tail.line_count += 1
    oid = order_id.strip()
    valid_oid = bool(oid and oid not in ("-1", "0", "None", "null"))
    active = getattr(st, "active_orders", None)
    if active is None:
        active = {}
        st.active_orders = active

    ev_up = ev_type.upper()
    is_new = ("NEW" in ev_up or ev_up in ("ADD", "OPEN"))
    is_cancel = any(k in ev_up for k in ("CANCEL", "DELETE", "REMOVE"))
    is_replace = any(k in ev_up for k in ("REPLACE", "MODIFY", "UPDATE"))

    if is_new:
        if "BID" in ev_up:
            side = "BID"
            if price > 0 and (st.best_ask <= 0 or price < st.best_ask):
                st.best_bid = price
        elif "ASK" in ev_up:
            side = "ASK"
            if price > 0 and (st.best_bid <= 0 or price > st.best_bid):
                st.best_ask = price
        elif price > 0 and st.best_bid > 0 and st.best_ask > 0:
            side = "BID" if price <= 0.5 * (st.best_bid + st.best_ask) else "ASK"
        else:
            side = ""
        if valid_oid and side in ("BID", "ASK") and price > 0:
            active[oid] = (side, price, size)
            if len(active) > 50_000:
                for stale_oid in list(active.keys())[:10_000]:
                    active.pop(stale_oid, None)
    elif is_replace:
        prev = active.get(oid) if valid_oid else None
        if "BID" in ev_up:
            side = "BID"
        elif "ASK" in ev_up:
            side = "ASK"
        elif prev is not None:
            side = prev[0]
        elif price > 0 and st.best_bid > 0 and st.best_ask > 0:
            side = "BID" if price <= 0.5 * (st.best_bid + st.best_ask) else "ASK"
        else:
            side = ""
        eff_price = price
        if prev is not None:
            if eff_price <= 0:
                eff_price = float(prev[1])
            if valid_oid and side in ("BID", "ASK"):
                active[oid] = (side, eff_price, size)
        elif valid_oid and side in ("BID", "ASK") and eff_price > 0:
            active[oid] = (side, eff_price, size)
    elif is_cancel:
        if valid_oid:
            active.pop(oid, None)


def _open_fast_text(path: Path):
    """1 MB buffered streaming reader (2x faster than default 8 KB gzip.open on large MBO .gz files)."""
    if path.name.endswith(".gz"):
        raw_fh = gzip.GzipFile(path, "rb")
        return io.TextIOWrapper(io.BufferedReader(raw_fh, buffer_size=1048576), encoding="utf-8", errors="replace")
    return open(path, "rt", encoding="utf-8", errors="replace", buffering=1048576)


def _iter_filtered_csv_events(
    files: Sequence[Path],
    start_ep: float,
    end_ep: float,
    allowed_prefixes: set[str],
    inst_filter: Optional[str] = None,
) -> Iterator[Tuple[float, str]]:
    """Yield `(epoch_utc, raw_line)` within `[start_ep, end_ep]` from ordered chunks."""
    seen_recent: set[str] = set()
    recent_ring: collections.deque = collections.deque()
    prev_ts_raw = ""
    prev_ep: Optional[float] = None
    early_stop_ep = end_ep + 300.0
    for path in files:
        is_live_tail = path.name.lower() in ("ticks.csv", "mbo.csv")
        over_end_streak = 0
        try:
            with _open_fast_text(path) as fh:
                for line in fh:
                    if not line or line[0] == "t" or len(line) < 20:
                        continue
                    if line[:10] not in allowed_prefixes:
                        continue
                    if inst_filter and inst_filter not in line:
                        continue
                    comma = line.find(",")
                    if comma <= 0:
                        continue
                    ts_raw = line[:comma]
                    if ts_raw == prev_ts_raw:
                        ep = prev_ep
                    else:
                        ep = _fast_ts_epoch(ts_raw)
                        prev_ts_raw = ts_raw
                        prev_ep = ep
                    if ep is None or ep < start_ep:
                        continue
                    if ep > end_ep:
                        if not is_live_tail and ep > early_stop_ep:
                            over_end_streak += 1
                            if over_end_streak >= 500:
                                break
                        continue
                    over_end_streak = 0
                    norm_line = line if line.endswith("\n") else (line + "\n")
                    if norm_line in seen_recent:
                        continue
                    seen_recent.add(norm_line)
                    recent_ring.append(norm_line)
                    if len(recent_ring) > 30_000:
                        old_line = recent_ring.popleft()
                        seen_recent.discard(old_line)
                    yield ep, norm_line
        except OSError:
            continue


# --------------------------------------------------------------------------- #
# Forward Tape Outcomes (Zero Look-Ahead: strictly after decision epoch `tc`)
# --------------------------------------------------------------------------- #
def _build_m1_walk_bars(trades: List[Tuple[float, float]]) -> Tuple[List[int], List[Tuple[int, float, float, float, float]]]:
    """Build M1 `(minute_ep, open, high, low, close)` list sorted by minute_ep."""
    buckets: Dict[int, List[float]] = {}
    for ep, px in trades:
        m = int(ep // 60) * 60
        cur = buckets.get(m)
        if cur is None:
            buckets[m] = [px, px, px, px]
        else:
            if px > cur[1]:
                cur[1] = px
            if px < cur[2]:
                cur[2] = px
            cur[3] = px
    keys = sorted(buckets.keys())
    bars = [(k, buckets[k][0], buckets[k][1], buckets[k][2], buckets[k][3]) for k in keys]
    return keys, bars


def _price_after(
    trade_eps: List[float],
    trades: List[Tuple[float, float]],
    when_ep: float,
    minutes: float,
    tol_s: float = 90.0,
) -> Optional[float]:
    target = when_ep + minutes * 60.0
    idx = bisect.bisect_left(trade_eps, target)
    best_px = None
    best_d = None
    for cand_idx in (idx - 1, idx):
        if 0 <= cand_idx < len(trades):
            d = abs(trades[cand_idx][0] - target)
            if best_d is None or d < best_d:
                best_d = d
                best_px = trades[cand_idx][1]
    return best_px if (best_d is not None and best_d <= tol_s) else None


def _excursion_30m(
    trade_eps: List[float],
    trades: List[Tuple[float, float]],
    when_ep: float,
    minutes: float = 30.0,
) -> Tuple[Optional[float], Optional[float]]:
    i0 = bisect.bisect_right(trade_eps, when_ep)
    i1 = bisect.bisect_right(trade_eps, when_ep + minutes * 60.0)
    if i1 <= i0:
        return None, None
    pxs = [trades[k][1] for k in range(i0, i1)]
    return max(pxs), min(pxs)


def _walk_bracket(
    bar_keys: List[int],
    bars: List[Tuple[int, float, float, float, float]],
    entry_ep: float,
    direction: str,
    entry_px: float,
    sl_px: float,
    tp_px: float,
    max_hold_min: int,
    spread_cost: float,
    trail_atr: float = 0.0,
    trail_dist_mult: float = 0.0,
) -> Tuple[str, float]:
    """Walk forward strictly after entry minute (`m > entry_min`). Conservative SL-first."""
    entry_min = int(entry_ep // 60) * 60
    start_idx = bisect.bisect_right(bar_keys, entry_min)
    if start_idx >= len(bars):
        return "last_bar", 0.0
    end_min = entry_min + max_hold_min * 60
    is_buy = direction.upper() == "BUY"
    cur_sl = sl_px
    init_risk = abs(entry_px - sl_px)
    hwm = entry_px
    last_close = entry_px

    for idx in range(start_idx, len(bars)):
        b_min, _o, hi, lo, cl = bars[idx]
        if b_min > end_min:
            break
        last_close = cl
        # Check SL before TP on current bar (no intra-bar peek)
        if is_buy:
            if lo <= cur_sl:
                return ("TRAIL" if cur_sl > sl_px + 1e-9 else "SL"), round((cur_sl - entry_px) - spread_cost, 2)
            if hi >= tp_px:
                return "TP", round((tp_px - entry_px) - spread_cost, 2)
            if hi > hwm:
                hwm = hi
            if trail_atr > 0 and trail_dist_mult > 0 and init_risk > 0 and (hwm - entry_px) >= 0.5 * init_risk:
                cand_sl = hwm - trail_dist_mult * trail_atr
                if cand_sl > cur_sl:
                    cur_sl = cand_sl
        else:
            if hi >= cur_sl:
                return ("TRAIL" if cur_sl < sl_px - 1e-9 else "SL"), round((entry_px - cur_sl) - spread_cost, 2)
            if lo <= tp_px:
                return "TP", round((entry_px - tp_px) - spread_cost, 2)
            if lo < hwm:
                hwm = lo
            if trail_atr > 0 and trail_dist_mult > 0 and init_risk > 0 and (entry_px - hwm) >= 0.5 * init_risk:
                cand_sl = hwm + trail_dist_mult * trail_atr
                if cand_sl < cur_sl:
                    cur_sl = cand_sl

    pts = (last_close - entry_px) if is_buy else (entry_px - last_close)
    return "TIMEOUT", round(pts - spread_cost, 2)


# --------------------------------------------------------------------------- #
# Retired, Dormant, Previous & Textbook-Repaired Bench Judge Checkup (Non-Voting)
# --------------------------------------------------------------------------- #
def _evaluate_retired_and_previous_bench(
    snap: Any,
    md: Dict[str, Any],
    tc_dt: datetime,
    asia_state: Dict[str, float],
    sess_state: Dict[str, Any],
    tf_min: Optional[int] = None,
) -> List[Tuple[str, str, int, float]]:
    """
    Evaluate all retired (`config.RETIRED_JUDGES`), dormant (`_ghost_on`),
    previous (`v5.8.1` / `U-16`), and textbook-repaired candidate bench judges
    directly on `(snap, md)` with zero effect on live active voting.
    Law #11: Every bench judge's bar window and forward grading clock scale
    dynamically with `tf_min` (`config.BASE_TF_MINUTES`).
    Returns list of `(judge_name, category, clock_min, dir_sign)`.
    """
    out: List[Tuple[str, str, int, float]] = []
    px = float(getattr(snap, "price", 0.0) or 0.0)
    if px <= 0.0:
        return out

    tf_eff = max(1, int(tf_min if tf_min is not None else getattr(config, "BASE_TF_MINUTES", 3) or 3))
    tf_sec = float(tf_eff * 60.0)

    regime = str(getattr(snap, "regime", "NEUTRAL") or "NEUTRAL").upper()
    vol = getattr(snap, "volatility", None)
    atr_live = float(getattr(vol, "atr", 0.0) or 0.0)
    atr_eff = atr_live if atr_live > 0 else max(px * 0.001, 1.5)
    vp = getattr(snap, "volume_profile", None)
    of = getattr(snap, "order_flow", None)
    fp = getattr(snap, "footprint", None)
    l3 = getattr(snap, "level3", None)
    tr = getattr(snap, "trend", None)
    mac = getattr(snap, "macro", None)
    tc_ep = tc_dt.timestamp()

    # Determine higher-timeframe bias dynamically across active MTF ladder (or M15/M1 fallback)
    mtf_bias = 0
    mtf_dict = getattr(snap, "mtf_trends", None) or getattr(snap, "mtf", None)
    if isinstance(mtf_dict, dict) and mtf_dict:
        m_up = sum(1 for v in mtf_dict.values() if str(v or "").upper() == "UP")
        m_dn = sum(1 for v in mtf_dict.values() if str(v or "").upper() == "DOWN")
        if m_up > m_dn:
            mtf_bias = 1
        elif m_dn > m_up:
            mtf_bias = -1

    candles = md.get("candles") if isinstance(md, dict) else None
    c_raw = candles.get("close") if isinstance(candles, dict) else None
    n_m1 = len(c_raw) if c_raw is not None else 0
    o_arr = np.asarray(candles["open"], dtype=float) if n_m1 > 0 else None
    h_arr = np.asarray(candles["high"], dtype=float) if n_m1 > 0 else None
    l_arr = np.asarray(candles["low"], dtype=float) if n_m1 > 0 else None
    c_arr = np.asarray(c_raw, dtype=float) if n_m1 > 0 else None
    v_arr = np.asarray(candles.get("volume") if candles.get("volume") is not None else np.ones(n_m1), dtype=float) if n_m1 > 0 else None

    c15_d = md.get("candles_m15") if isinstance(md, dict) else None
    if mtf_bias == 0 and isinstance(c15_d, dict) and c15_d.get("close") is not None and len(c15_d["close"]) >= 3:
        cl15 = np.asarray(c15_d["close"], dtype=float)
        sma_short = float(np.mean(cl15[-3:]))
        if cl15[-1] > sma_short and cl15[-1] > cl15[-2]:
            mtf_bias = 1
        elif cl15[-1] < sma_short and cl15[-1] < cl15[-2]:
            mtf_bias = -1
    if mtf_bias == 0 and n_m1 >= 45 and c_arr is not None:
        try:
            _o15, _h15, _l15, cl15, _v15 = s2._aggregate_m1(o_arr, h_arr, l_arr, c_arr, v_arr, 15)
            if len(cl15) >= 3:
                sma_short = float(np.mean(cl15[-3:]))
                if cl15[-1] > sma_short and cl15[-1] > cl15[-2]:
                    mtf_bias = 1
                elif cl15[-1] < sma_short and cl15[-1] < cl15[-2]:
                    mtf_bias = -1
        except Exception:
            mtf_bias = 0
    if mtf_bias == 0 and tr is not None:
        td_str = str(getattr(tr, "trend_direction", "") or "").upper()
        mtf_bias = 1 if td_str == "UP" else (-1 if td_str == "DOWN" else 0)

    def _emit(name: str, cat: str, clk_m5_equiv: int, d: float) -> None:
        bars = 2 if name == "microprice" else max(1, int(round(float(clk_m5_equiv) / 5.0)))
        clk_dyn = max(1, int(bars * tf_eff))
        if d > 0:
            out.append((name, cat, clk_dyn, 1.0))
        elif d < 0:
            out.append((name, cat, clk_dyn, -1.0))

    # --- 1. vwap_zscore (RETIRED; L3652-3656, RANGE-gated -sign(z) fade) ---
    if vp is not None:
        z = float(getattr(vp, "vwap_zscore", 0.0) or 0.0)
        if regime == "RANGE" and abs(z) > 1.5:
            _emit("vwap_zscore", "RETIRED", 60, -1.0 if z > 0 else 1.0)

        # --- 2. poc_day (RETIRED; L3617-3619, 1-tick sign(px - poc)) ---
        poc = float(getattr(vp, "poc", 0.0) or 0.0)
        if poc > 0 and px != poc:
            _emit("poc_day", "RETIRED", 60, 1.0 if px > poc else -1.0)

    # --- 3. supply_demand [legacy] (RETIRED; L3760-3786 + U-14 key fix) ---
    ns = float(getattr(snap, "nearest_support", 0.0) or 0.0)
    nr = float(getattr(snap, "nearest_resistance", 0.0) or 0.0)
    obs = getattr(snap, "order_blocks", None) or []
    atr_z = atr_live if atr_live > 0 else px * 0.002
    b_sd = bool(ns > 0 and 0 < (px - ns) <= 1.5 * atr_z)
    s_sd = bool(nr > 0 and 0 < (nr - px) <= 1.5 * atr_z)
    if b_sd and not s_sd:
        _emit("supply_demand", "RETIRED", 60, 1.0)
    elif s_sd and not b_sd:
        _emit("supply_demand", "RETIRED", 60, -1.0)
    elif b_sd and s_sd:
        n_obs = max(len(obs), 1)
        b_idx = max((z.get("bar", 0) for z in obs if z.get("type") == "demand" and float(z.get("bottom", 0) or 0) <= px), default=0)
        s_idx = max((z.get("bar", 0) for z in obs if z.get("type") == "supply" and float(z.get("top", 0) or 0) >= px), default=0)
        net_sd = (0.6 + 0.3 * (b_idx / n_obs)) - (0.6 + 0.3 * (s_idx / n_obs))
        if abs(net_sd) > 1e-6:
            _emit("supply_demand", "RETIRED", 60, net_sd)

    # --- 4. supply_demand[fix] (REPAIRED: true wall-clock active-TIMEFRAME unmitigated zones + >=1.0 ATR impulse + TREND seatbelt) ---
    ctf_sd = (md.get("candles_tf") or md.get(f"candles_m{tf_eff}") or md.get("candles_m5")) if isinstance(md, dict) else None
    if (isinstance(ctf_sd, dict) and ctf_sd.get("close") is not None and len(ctf_sd["close"]) >= 8) or (n_m1 >= 8 * tf_eff and c_arr is not None):
        try:
            used_closed_m5 = bool(isinstance(ctf_sd, dict) and ctf_sd.get("close") is not None and len(ctf_sd["close"]) >= 8)
            if used_closed_m5:
                o5 = np.asarray(ctf_sd["open"], dtype=float)
                h5 = np.asarray(ctf_sd["high"], dtype=float)
                l5 = np.asarray(ctf_sd["low"], dtype=float)
                c5 = np.asarray(ctf_sd["close"], dtype=float)
            else:
                o5, h5, l5, c5, _v5 = s2._aggregate_m1(o_arr, h_arr, l_arr, c_arr, v_arr, int(tf_eff))
            n5 = len(c5)
            if n5 >= 8:
                tr5 = [max(float(h5[i] - l5[i]), abs(float(h5[i] - c5[i - 1])), abs(float(l5[i] - c5[i - 1]))) for i in range(1, n5)]
                atr5 = float(sum(tr5[-14:]) / min(len(tr5), 14)) if tr5 else atr_eff
                best_dem = 0.0
                best_sup = 0.0
                k = 2
                mit_end = n5 if used_closed_m5 else (n5 - 1)
                # Evaluate pivots on closed M5 bars; check mitigation across subsequent closed M5 bars
                for i in range(k, n5 - 3):
                    win_h = h5[i - k : i + k + 1]
                    win_l = l5[i - k : i + k + 1]
                    if float(h5[i]) >= float(np.max(win_h)):
                        z_top = float(h5[i])
                        z_bot = float(max(o5[i], c5[i]))
                        dep_drop = z_bot - float(np.min(l5[i + 1 : min(i + 4, n5)]))
                        mitigated = any(float(c5[j]) > z_top for j in range(i + 1, max(i + 1, mit_end)))
                        if dep_drop >= 1.0 * atr5 and not mitigated and z_bot >= px:
                            if best_sup == 0.0 or z_bot < best_sup:
                                best_sup = z_bot
                    if float(l5[i]) <= float(np.min(win_l)):
                        z_bot = float(l5[i])
                        z_top = float(min(o5[i], c5[i]))
                        dep_rise = float(np.max(h5[i + 1 : min(i + 4, n5)])) - z_top
                        mitigated = any(float(c5[j]) < z_bot for j in range(i + 1, max(i + 1, mit_end)))
                        if dep_rise >= 1.0 * atr5 and not mitigated and z_top <= px:
                            if z_top > best_dem:
                                best_dem = z_top
                allow_buy = not (regime == "TREND" and mtf_bias < 0)
                allow_sell = not (regime == "TREND" and mtf_bias > 0)
                hit_dem = allow_buy and best_dem > 0 and 0 <= (px - best_dem) <= 1.2 * atr_eff
                hit_sup = allow_sell and best_sup > 0 and 0 <= (best_sup - px) <= 1.2 * atr_eff
                if hit_dem and not hit_sup:
                    _emit("supply_demand[fix]", "REPAIRED", 60, 1.0)
                elif hit_sup and not hit_dem:
                    _emit("supply_demand[fix]", "REPAIRED", 60, -1.0)
        except Exception:
            pass

    # --- 5. htf_poc [legacy] (RETIRED; L3795-3825, inverted <=1 ATR magnet bug) ---
    hpoc = getattr(snap, "htf_poc", None) or {}
    h1_p = float(hpoc.get("H1", 0.0) or 0.0) if isinstance(hpoc, dict) else 0.0
    h4_p = float(hpoc.get("H4", 0.0) or 0.0) if isinstance(hpoc, dict) else 0.0
    if h4_p <= 0.0 and isinstance(md, dict) and isinstance(md.get("_htf_m1_240"), dict):
        try:
            fb_poc = s2._compute_htf_poc(md["_htf_m1_240"])
            if h1_p <= 0.0:
                h1_p = float(fb_poc.get("H1", 0.0) or 0.0)
            h4_p = float(fb_poc.get("H4", 0.0) or 0.0)
        except Exception:
            pass
    if atr_live > 0 and (h1_p > 0 or h4_p > 0):
        net_hp = 0.0
        if h1_p > 0:
            dh1 = abs(px - h1_p) / atr_live
            if dh1 <= 1.0:
                net_hp += 0.7 if px >= h1_p else -0.7
            elif dh1 > 2.0:
                net_hp += -0.4 if px > h1_p else 0.4
        if h4_p > 0:
            dh4 = abs(px - h4_p) / atr_live
            if dh4 <= 1.5:
                net_hp += 0.9 if px >= h4_p else -0.9
        if abs(net_hp) > 1e-6:
            _emit("htf_poc", "RETIRED", 120, net_hp)

        # --- 6. htf_poc[fix] (REPAIRED: silent inside 0.6 ATR; RANGE >=2 ATR fade; TREND H1+H4 migration) ---
        if h1_p > 0:
            dh1 = abs(px - h1_p) / atr_live
            if regime == "RANGE" and dh1 >= 2.0:
                _emit("htf_poc[fix]", "REPAIRED", 60, -1.0 if px > h1_p else 1.0)
            elif regime == "TREND" and h4_p > 0 and dh1 >= 0.8:
                if px > h1_p and px > h4_p and mtf_bias >= 0:
                    _emit("htf_poc[fix]", "REPAIRED", 60, 1.0)
                elif px < h1_p and px < h4_p and mtf_bias <= 0:
                    _emit("htf_poc[fix]", "REPAIRED", 60, -1.0)

    # --- 7 & 8. whale_walls [legacy] vs whale_walls[fix] (live book only, no BID_CANCEL, <=2 ATR, no spoof) ---
    whale_bid_any = False
    whale_ask_any = False
    ib_bid_any = False
    ib_ask_any = False
    if l3 is not None:
        ob = getattr(l3, "order_book", None) or {}
        bids_l3 = ob.get("bids") or [] if isinstance(ob, dict) else []
        asks_l3 = ob.get("asks") or [] if isinstance(ob, dict) else []
        evs_l3 = getattr(l3, "order_events", None) or []
        w_thr = float(getattr(config, "WHALE_LOT_THRESHOLD", 30.0) or 30.0)

        # Legacy L3320-3380 (1% radius + `"BID" in ev_type` cancel bug)
        wb_leg = 0.0
        wa_leg = 0.0
        for bp, bsz in bids_l3:
            if float(bsz) >= w_thr and abs(float(bp) - px) / px < 0.01:
                wb_leg += float(bsz)
        for ap, asz in asks_l3:
            if float(asz) >= w_thr and abs(float(ap) - px) / px < 0.01:
                wa_leg += float(asz)
        for ev in evs_l3[-500:]:
            if not isinstance(ev, dict):
                continue
            esz = float(ev.get("size", 0.0) or 0.0)
            epx = float(ev.get("price", 0.0) or 0.0)
            etp = str(ev.get("type") or ev.get("event_type") or "").upper()
            if esz >= w_thr and epx > 0 and abs(epx - px) / px < 0.01:
                if "BID" in etp:
                    wb_leg += esz
                elif "ASK" in etp:
                    wa_leg += esz
        whale_bid_any = wb_leg > 0
        whale_ask_any = wa_leg > 0
        if wb_leg > 0 or wa_leg > 0:
            if wb_leg > wa_leg * 1.2:
                _emit("whale_walls", "RETIRED", 30, 1.0)
            elif wa_leg > wb_leg * 1.2:
                _emit("whale_walls", "RETIRED", 30, -1.0)

        # Repaired whale_walls[fix]: ONLY live resting book <= max(2.0*ATR, 1.50), exclude spoof_levels, TREND guard
        sp_lvls = getattr(l3, "spoof_levels", None) or {}
        if isinstance(sp_lvls, dict):
            sp_prices = [float(sp_k) for sp_k, sp_cnt in sp_lvls.items() if float(sp_cnt or 0) > 0]
        else:
            sp_prices = [float(sp.get("price", 0.0) or 0.0) for sp in sp_lvls if isinstance(sp, dict)]
        max_w_dist = max(2.0 * atr_eff, 1.50)
        wb_fix = 0.0
        wa_fix = 0.0
        for bp, bsz in bids_l3:
            bpf = float(bp)
            if float(bsz) >= w_thr and 0 <= (px - bpf) <= max_w_dist:
                if not any(abs(bpf - spx) <= 0.20 for spx in sp_prices):
                    wb_fix += float(bsz)
        for ap, asz in asks_l3:
            apf = float(ap)
            if float(asz) >= w_thr and 0 <= (apf - px) <= max_w_dist:
                if not any(abs(apf - spx) <= 0.20 for spx in sp_prices):
                    wa_fix += float(asz)
        if wb_fix > wa_fix * 1.4 and not (regime == "TREND" and mtf_bias < 0):
            _emit("whale_walls[fix]", "REPAIRED", 30, 1.0)
        elif wa_fix > wb_fix * 1.4 and not (regime == "TREND" and mtf_bias > 0):
            _emit("whale_walls[fix]", "REPAIRED", 30, -1.0)

        # Iceberg side flags for legacy queue_pos (supports real Dict[price, count] & test list[dict])
        ib_lvls = getattr(l3, "iceberg_levels", None) or {}
        if isinstance(ib_lvls, dict):
            for ib_k, ib_cnt in ib_lvls.items():
                ibp = float(ib_k or 0.0)
                if float(ib_cnt or 0) > 0 and ibp > 0 and abs(ibp - px) / px < 0.003:
                    if ibp <= px:
                        ib_bid_any = True
                    else:
                        ib_ask_any = True
        else:
            for ib in ib_lvls:
                if isinstance(ib, dict):
                    ibs = str(ib.get("side") or "").upper()
                    ibp = float(ib.get("price", 0.0) or 0.0)
                    if ibp > 0 and abs(ibp - px) / px < 0.003:
                        if ibs == "BID":
                            ib_bid_any = True
                        elif ibs == "ASK":
                            ib_ask_any = True

        # --- 13. l3_ofi_streak (RETIRED; L3287-3294) ---
        bs = int(getattr(l3, "buy_streak", 0) or 0)
        ss = int(getattr(l3, "sell_streak", 0) or 0)
        if (bs >= 3 or ss >= 3) and bs != ss:
            _emit("l3_ofi_streak", "RETIRED", 15, 1.0 if bs > ss else -1.0)

        # --- 14. iceberg_legacy (RETIRED; L3469-3477) ---
        ib_ev = int(getattr(l3, "iceberg_events", 0) or 0)
        l3_imb = float(getattr(l3, "order_book_imbalance", 0.0) or 0.0)
        if ib_ev >= 1 and not (ib_bid_any or ib_ask_any) and abs(l3_imb) > 0.2:
            _emit("iceberg_legacy", "RETIRED", 30, 1.0 if l3_imb > 0 else -1.0)

        # --- 15. spoof_invert_loose (RETIRED; L3512-3519) ---
        sp_ev = int(getattr(l3, "spoof_events", 0) or 0)
        if sp_ev >= 1 and sp_lvls:
            if isinstance(sp_lvls, dict):
                sp_b = sum(int(v or 0) for k, v in sp_lvls.items() if float(k) <= px and abs(float(k) - px) / px < 0.003)
                sp_a = sum(int(v or 0) for k, v in sp_lvls.items() if float(k) > px and abs(float(k) - px) / px < 0.003)
            else:
                sp_b = sum(1 for sp in sp_lvls if isinstance(sp, dict) and str(sp.get("side") or "").upper() == "BID" and abs(float(sp.get("price", 0) or 0) - px) / px < 0.003)
                sp_a = sum(1 for sp in sp_lvls if isinstance(sp, dict) and str(sp.get("side") or "").upper() == "ASK" and abs(float(sp.get("price", 0) or 0) - px) / px < 0.003)
            if sp_b > 0 and sp_a > 0 and sp_b != sp_a:
                _emit("spoof_invert_loose", "RETIRED", 30, -1.0 if sp_b > sp_a else 1.0)

    # --- 9, 10, 11, 12, 20, 21. Order-flow / L2 retired & dormant judges ---
    if of is not None:
        mid_p = float(getattr(of, "mid_price", 0.0) or 0.0)
        mic_p = float(getattr(of, "microprice", 0.0) or 0.0)
        if mid_p > 0 and mic_p > 0:
            m_dev_bps = ((mic_p - mid_p) / px) * 10000.0
            if abs(m_dev_bps) > 0.2:
                _emit("microprice", "RETIRED", 15, 1.0 if m_dev_bps > 0 else -1.0)

        ba_rat = float(getattr(of, "bid_ask_ratio", 1.0) or 1.0)
        if ba_rat > 1.2 and (whale_bid_any or ib_bid_any):
            _emit("queue_pos", "RETIRED", 15, 1.0)
        elif 0 < ba_rat < 0.8 and (whale_ask_any or ib_ask_any):
            _emit("queue_pos", "RETIRED", 15, -1.0)

        dlt = float(getattr(of, "delta", 0.0) or 0.0)
        if ba_rat >= 1.5 and dlt > 0:
            _emit("queue_pos[ck2017]", "REPAIRED", 15, 1.0)
        elif 0 < ba_rat <= 0.67 and dlt < 0:
            _emit("queue_pos[ck2017]", "REPAIRED", 15, -1.0)

        bp_pct = float(getattr(of, "buying_pressure", 50.0) or 50.0)
        sp_pct = float(getattr(of, "selling_pressure", 50.0) or 50.0)
        if bp_pct > 60.0:
            _emit("delta_pressure", "RETIRED", 15, 1.0)
        elif sp_pct > 60.0:
            _emit("delta_pressure", "RETIRED", 15, -1.0)

        b_dep = float(getattr(of, "level2_bid_depth", 0.0) or 0.0)
        a_dep = float(getattr(of, "level2_ask_depth", 0.0) or 0.0)
        ofi_l2 = float(getattr(of, "ofi", 0.0) or 0.0)
        if (b_dep + a_dep) > 0 and abs(ofi_l2 / (b_dep + a_dep)) > 0.15:
            _emit("l2_ofi", "DORMANT", 15, 1.0 if ofi_l2 > 0 else -1.0)

        d_imb = float(getattr(of, "depth_imbalance", 0.0) or 0.0)
        if abs(d_imb) >= 0.15:
            _emit("l2_depth_top5", "DORMANT", 15, 1.0 if d_imb > 0 else -1.0)

    # --- 16. Macro retired judges (when 5d macro series is non-zero) ---
    if mac is not None:
        uchg = float(getattr(mac, "usd_change_5d", 0.0) or 0.0)
        ychg = float(getattr(mac, "yield_change_5d", 0.0) or 0.0)
        vspk = bool(getattr(mac, "vix_spike", False))
        vchg = float(getattr(mac, "vix_change_5d", 0.0) or 0.0)
        if abs(uchg) > 0.3:
            _emit("macro_dxy", "RETIRED", 120, -1.0 if uchg > 0 else 1.0)
        if abs(ychg) > 2.0:
            _emit("macro_yield", "RETIRED", 120, -1.0 if ychg > 0 else 1.0)
        if vspk and abs(vchg) > 5.0:
            _emit("macro_vix", "RETIRED", 120, 1.0 if vchg > 0 else -1.0)

    # --- 17, 18, 19, 22. Technical M1 dormant & v5.8.1 previous judges ---
    if tr is not None:
        m_hist = float(getattr(tr, "macd_histogram", 0.0) or 0.0)
        if abs(m_hist) > 0.02:
            _emit("trend_macd", "DORMANT", 60, 1.0 if m_hist > 0 else -1.0)

        sma20 = float(getattr(tr, "sma_20", 0.0) or 0.0)
        if sma20 > 0 and px != sma20:
            _emit("sma20", "DORMANT", 30, 1.0 if px > sma20 else -1.0)

        sma50 = float(getattr(tr, "sma_50", 0.0) or 0.0)
        if sma50 > 0 and px != sma50:
            _emit("sma50", "DORMANT", 60, 1.0 if px > sma50 else -1.0)

        t_dir = str(getattr(tr, "trend_direction", "NEUTRAL") or "NEUTRAL").upper()
        if t_dir == "UP":
            _emit("trend_ma_adx", "DORMANT", 60, 1.0)
        elif t_dir == "DOWN":
            _emit("trend_ma_adx", "DORMANT", 60, -1.0)

        rsi_v = float(getattr(tr, "rsi", 50.0) or 50.0)
        if regime == "RANGE":
            if rsi_v < 30.0:
                _emit("rsi_range_fade", "PREVIOUS", 30, 1.0)
            elif rsi_v > 70.0:
                _emit("rsi_range_fade", "PREVIOUS", 30, -1.0)
        if rsi_v > 55.0:
            _emit("rsi_ema_mom", "PREVIOUS", 30, 1.0)
        elif 0.0 < rsi_v < 45.0:
            _emit("rsi_ema_mom", "PREVIOUS", 30, -1.0)

    # --- Remaining _ghost_on dormant directional baselines (L3183, L3185, L3605) ---
    if vp is not None:
        vw_px = float(getattr(vp, "vwap", 0.0) or 0.0)
        if vw_px > 0 and px != vw_px:
            _emit("ghost_vwap_side", "DORMANT", 60, 1.0 if px > vw_px else -1.0)
    if of is not None:
        cvd_v = float(getattr(of, "cvd", 0.0) or 0.0)
        if cvd_v != 0.0:
            _emit("ghost_cvd_sign", "DORMANT", 30, 1.0 if cvd_v > 0 else -1.0)
        ba_r2 = float(getattr(of, "bid_ask_ratio", 1.0) or 1.0)
        if ba_r2 > 1.2:
            _emit("ghost_bid_ask", "DORMANT", 15, 1.0)
        elif 0.0 < ba_r2 < 0.8:
            _emit("ghost_bid_ask", "DORMANT", 15, -1.0)

    # --- 23. asian_range_brk (PREVIOUS v5.8.1: 00-07Z box -> 08-12Z London breakout) ---
    a_hi = float(asia_state.get("hi", 0.0) or 0.0)
    a_lo = float(asia_state.get("lo", 0.0) or 0.0)
    if 8 <= tc_dt.hour <= 12 and a_hi > a_lo > 0 and (a_hi - a_lo) >= 1.0:
        if px > a_hi:
            _emit("asian_range_brk", "PREVIOUS", 60, 1.0)
        elif px < a_lo:
            _emit("asian_range_brk", "PREVIOUS", 60, -1.0)

    # --- 24 & 24b. session_ib_on & session_pdh_pdl (U-16 session_levels.py: IB/ON + PDH/PDL/Prev_Settlement) ---
    if isinstance(sess_state, dict) and sess_state:
        ibh = float(sess_state.get("ibh", 0.0) or 0.0)
        ibl = float(sess_state.get("ibl", 0.0) or 0.0)
        ib_ok = bool(sess_state.get("ib_complete", False)) and ibh > ibl > 0
        onh = float(sess_state.get("onh", 0.0) or 0.0)
        onl = float(sess_state.get("onl", 0.0) or 0.0)
        if ib_ok and regime == "TREND":
            if px > ibh:
                _emit("session_ib_on", "PREVIOUS", 60, 1.0)
            elif px < ibl:
                _emit("session_ib_on", "PREVIOUS", 60, -1.0)
        elif regime == "RANGE":
            if ib_ok and 0 <= (ibh - px) <= 0.6 * atr_eff:
                _emit("session_ib_on", "PREVIOUS", 60, -1.0)
            elif ib_ok and 0 <= (px - ibl) <= 0.6 * atr_eff:
                _emit("session_ib_on", "PREVIOUS", 60, 1.0)
            elif onh > onl > 0 and 0 <= (onh - px) <= 0.6 * atr_eff:
                _emit("session_ib_on", "PREVIOUS", 60, -1.0)
            elif onh > onl > 0 and 0 <= (px - onl) <= 0.6 * atr_eff:
                _emit("session_ib_on", "PREVIOUS", 60, 1.0)

        pdh = float(sess_state.get("prev_high", 0.0) or 0.0)
        pdl = float(sess_state.get("prev_low", 0.0) or 0.0)
        pset = float(sess_state.get("prev_settlement", 0.0) or 0.0)
        if pdh > pdl > 0:
            if regime == "TREND":
                if px > pdh and mtf_bias >= 0:
                    _emit("session_pdh_pdl", "PREVIOUS", 60, 1.0)
                elif px < pdl and mtf_bias <= 0:
                    _emit("session_pdh_pdl", "PREVIOUS", 60, -1.0)
            else:
                if 0 <= (pdh - px) <= 0.6 * atr_eff:
                    _emit("session_pdh_pdl", "PREVIOUS", 60, -1.0)
                elif 0 <= (px - pdl) <= 0.6 * atr_eff:
                    _emit("session_pdh_pdl", "PREVIOUS", 60, 1.0)
                elif pset > 0 and 0 < abs(px - pset) <= 0.4 * atr_eff:
                    _emit("session_pdh_pdl", "PREVIOUS", 60, -1.0 if px > pset else 1.0)

    # --- 25 & 25b. round_numbers ($50 RANGE bounce) & round_numbers_5st (exact db0bf52 5-state flow judge) ---
    if regime == "RANGE":
        r50 = round(px / 50.0) * 50.0
        dist_r = px - r50
        if 0 < dist_r <= 0.4 * atr_eff:
            _emit("round_numbers", "PREVIOUS", 30, 1.0)
        elif 0 < (-dist_r) <= 0.4 * atr_eff:
            _emit("round_numbers", "PREVIOUS", 30, -1.0)

    # Exact historical db0bf52 3-tier ($100/$50, $25, $10) 5-state flow-dependent round-number judge
    best_rn = None
    best_rn_d = 1e9
    for step_rn, mult_rn in ((50.0, 1.0), (25.0, 0.7), (10.0, 0.4)):
        lvl_rn = round(px / step_rn) * step_rn
        d_rn = abs(px - lvl_rn)
        if d_rn < best_rn_d:
            best_rn_d = d_rn
            best_rn = (lvl_rn, mult_rn)
    if best_rn is not None and best_rn_d <= max(0.8 * atr_eff, 1.25):
        lvl_rn, _m_rn = best_rn
        of_dlt = float(getattr(of, "delta", 0.0) or 0.0) if of is not None else 0.0
        abs_net = float(getattr(of, "absorption_net", 0.0) or 0.0) if of is not None else 0.0
        prev_px = float(c_arr[-2]) if (c_arr is not None and n_m1 >= 2) else px
        if prev_px <= lvl_rn < px and of_dlt > 0:
            _emit("round_numbers_5st", "PREVIOUS", 30, 1.0)
        elif prev_px >= lvl_rn > px and of_dlt < 0:
            _emit("round_numbers_5st", "PREVIOUS", 30, -1.0)
        elif px < lvl_rn and abs_net < 0:
            _emit("round_numbers_5st", "PREVIOUS", 30, -1.0)
        elif px > lvl_rn and abs_net > 0:
            _emit("round_numbers_5st", "PREVIOUS", 30, 1.0)
        elif regime == "RANGE" and px != lvl_rn:
            _emit("round_numbers_5st", "PREVIOUS", 30, -1.0 if px < lvl_rn else 1.0)

    # --- 26-29. POWER v1 & v2 Exclusive Instruments (never before graded side-by-side with Classroom) ---
    pw = getattr(snap, "power", None) or {}
    if isinstance(pw, dict) and pw:
        pw_d = str(pw.get("direction") or "NEITHER").upper()
        if pw_d == "UP":
            _emit("power_v2_dir", "POWER", 30, 1.0)
        elif pw_d == "DOWN":
            _emit("power_v2_dir", "POWER", 30, -1.0)
        inp_j = pw.get("input_judges") or {}
        if isinstance(inp_j, dict):
            bp_j = inp_j.get("big_prints")
            if isinstance(bp_j, dict) and isinstance(bp_j.get("value"), (int, float)):
                bp_v = float(bp_j["value"])
                if abs(bp_v) >= 0.15:
                    _emit("power_big_prints", "POWER", 15, 1.0 if bp_v > 0 else -1.0)
            fl_j = inp_j.get("footprint_levels")
            if isinstance(fl_j, dict) and isinstance(fl_j.get("value"), (int, float)):
                fl_v = float(fl_j["value"])
                if abs(fl_v) >= 0.25:
                    _emit("power_breadth_sqrt", "POWER", 15, 1.0 if fl_v > 0 else -1.0)

    sig_map = getattr(snap, "signal_map", None)
    if isinstance(sig_map, dict) and sig_map:
        try:
            import power_team as pt_v1
            v1_res = pt_v1.decide(
                sig_map,
                order_flow=of,
                footprint=fp,
                level3=l3,
                volume_profile=vp,
                trend=tr,
                volatility=vol,
                news=getattr(snap, "news", None),
                macro=mac,
                price=px,
                divergence=float(getattr(snap, "divergence", 0.0) or 0.0),
                mtf=getattr(snap, "mtf", None),
                config=config,
                tick_data=md.get("tick_data") if isinstance(md, dict) else None,
                atr=atr_eff,
            )
            v1_sc = float(v1_res.get("score", 0.0) or 0.0)
            v1_door = str(v1_res.get("door") or "").upper()
            if v1_sc >= 0.35 and v1_door in ("ABOVE", "BELOW"):
                _emit("power_v1_legs", "POWER", 30, 1.0 if v1_door == "ABOVE" else -1.0)
        except Exception:
            pass

    # --- 30-38. ACT_FIX: Stage-2 Textbook Shadow Twins of Buggy Active Judges (Law #5/#9/#10) ---
    # 30. l3_large_ofi[fix]: cures L5586 `_ref_mid == 0.0` bug using true `px` mid + $2.50 Gaussian weight
    raw_evs = md.get("order_events") if isinstance(md, dict) else None
    if isinstance(raw_evs, list) and raw_evs:
        lg_lots = float(getattr(config, "L3_LARGE_ORDER_LOTS", 20.0) or 20.0)
        nt_usd = float(getattr(config, "L3_NEAR_TOUCH_USD", 2.50) or 2.50)
        lg_ofi = 0.0
        for ev in raw_evs[-1500:]:
            if not isinstance(ev, dict):
                continue
            ts_raw = ev.get("epoch") if ev.get("epoch") is not None else ev.get("timestamp")
            ev_ts = 0.0
            if isinstance(ts_raw, (int, float)):
                ev_ts = float(ts_raw)
            elif isinstance(ts_raw, datetime):
                ev_ts = ts_raw.timestamp()
            elif isinstance(ts_raw, str) and ts_raw:
                p_dt = _parse_ts_utc(ts_raw)
                if p_dt is not None:
                    ev_ts = p_dt.timestamp()
            if ev_ts > 0 and (tc_ep - ev_ts) > tf_sec:
                continue
            esz = float(ev.get("size", 0.0) or 0.0)
            epx = float(ev.get("price", 0.0) or 0.0)
            if esz < lg_lots or epx <= 0.0:
                continue
            dist_u = abs(epx - px)
            if dist_u > 3.0 * nt_usd:
                continue
            w_d = math.exp(-0.5 * (dist_u / max(nt_usd, 0.25)) ** 2)
            etp = str(ev.get("type") or ev.get("event_type") or "").upper()
            esd = str(ev.get("side") or "").upper()
            is_bid = ("BID" in etp) or (esd == "BID")
            is_ask = ("ASK" in etp) or (esd == "ASK")
            is_rem = any(tok in etp for tok in ("CANCEL", "DELETE", "REMOVE"))
            if is_bid and not is_ask:
                lg_ofi += (-esz * w_d) if is_rem else (esz * w_d)
            elif is_ask and not is_bid:
                lg_ofi += (esz * w_d) if is_rem else (-esz * w_d)
        if abs(lg_ofi) >= 15.0 and not (regime == "TREND" and ((lg_ofi > 0 and mtf_bias < 0) or (lg_ofi < 0 and mtf_bias > 0))):
            _emit("l3_large_ofi[fix]", "ACT_FIX", 15, 1.0 if lg_ofi > 0 else -1.0)

    # 31. iceberg[fix]: fresh <=180s refills, <=max(1.0*ATR, $1.50), unbroken bounce + TREND seatbelt
    if l3 is not None:
        ib_meta = getattr(l3, "iceberg_meta", None) or {}
        if isinstance(ib_meta, dict) and ib_meta:
            max_ib_d = max(1.0 * atr_eff, 1.50)
            best_ib_sc = 0.0
            best_ib_px = 0.0
            for k_px, m_ib in ib_meta.items():
                if not isinstance(m_ib, dict):
                    continue
                ibp = float(k_px or 0.0)
                l_seen = float(m_ib.get("last_seen", 0.0) or 0.0)
                refs = int(m_ib.get("refills", 0) or 0)
                sc_ib = float(m_ib.get("score", 0.0) or 0.0)
                if ibp <= 0 or refs < 2 or abs(ibp - px) > max_ib_d:
                    continue
                if l_seen > 0 and (tc_ep - l_seen) > tf_sec:
                    continue
                if sc_ib > best_ib_sc:
                    best_ib_sc = sc_ib
                    best_ib_px = ibp
            if best_ib_px > 0:
                of_d = float(getattr(of, "delta", 0.0) or 0.0) if of is not None else 0.0
                if best_ib_px <= px and of_d >= 0 and not (regime == "TREND" and mtf_bias < 0):
                    _emit("iceberg[fix]", "ACT_FIX", 30, 1.0)
                elif best_ib_px > px and of_d <= 0 and not (regime == "TREND" and mtf_bias > 0):
                    _emit("iceberg[fix]", "ACT_FIX", 30, -1.0)

    # Closed wall-clock active-TIMEFRAME bars (`candles_tf`) for volume_roc[fix], sweep[fix], vwap_bands[fix], cvd_divergence[fix]
    c3_d = (md.get("candles_tf") or md.get(f"candles_m{tf_eff}") or md.get("candles_m3")) if isinstance(md, dict) else None
    o3 = h3_a = l3_a = c3 = v3 = None
    if isinstance(c3_d, dict) and c3_d.get("close") is not None and len(c3_d["close"]) >= 6:
        o3 = np.asarray(c3_d["open"], dtype=float)
        h3_a = np.asarray(c3_d["high"], dtype=float)
        l3_a = np.asarray(c3_d["low"], dtype=float)
        c3 = np.asarray(c3_d["close"], dtype=float)
        v3 = np.asarray(c3_d["volume"], dtype=float)
    elif n_m1 >= 7 * tf_eff and c_arr is not None:
        try:
            o3, h3_a, l3_a, c3, v3 = s2._aggregate_m1(o_arr, h_arr, l_arr, c_arr, v_arr, tf_eff)
        except Exception:
            o3 = c3 = None

    # 32. volume_roc[fix]: closed M3 RVOL >= 1.8x 20-bar median + body >= 0.45*(H-L) + CLV >=0.65 / <=0.35
    if c3 is not None and len(c3) >= 12:
        med_v3 = max(float(np.median(v3[-min(21, len(v3)) : -1])), 1.0)
        rng3 = float(h3_a[-1] - l3_a[-1])
        body3 = abs(float(c3[-1] - o3[-1]))
        if float(v3[-1]) >= 1.8 * med_v3 and rng3 >= 0.30 and body3 >= 0.45 * rng3:
            clv3 = float(c3[-1] - l3_a[-1]) / rng3
            if clv3 >= 0.65:
                _emit("volume_roc[fix]", "ACT_FIX", 30, 1.0)
            elif clv3 <= 0.35:
                _emit("volume_roc[fix]", "ACT_FIX", 30, -1.0)

    # 33. sweep[fix]: cures V6_SWEEP_THRESHOLD_PCT=0.10 ($4/1m) bug -> closed M3, poke>=max(0.30,0.25*ATR), 1.5x vol
    if c3 is not None and len(c3) >= 22:
        hi20 = float(np.max(h3_a[-21:-1]))
        lo20 = float(np.min(l3_a[-21:-1]))
        med_v_sw = max(float(np.median(v3[-21:-1])), 1.0)
        bar_atr3 = max(float(np.median(h3_a[-20:] - l3_a[-20:])), 0.40)
        poke_thr = max(0.30, 0.25 * bar_atr3)
        if float(v3[-1]) >= 1.5 * med_v_sw:
            if float(h3_a[-1]) >= hi20 + poke_thr and float(c3[-1]) < hi20 and not (regime == "TREND" and mtf_bias > 0):
                _emit("sweep[fix]", "ACT_FIX", 30, -1.0)
            elif float(l3_a[-1]) <= lo20 - poke_thr and float(c3[-1]) > lo20 and not (regime == "TREND" and mtf_bias < 0):
                _emit("sweep[fix]", "ACT_FIX", 30, 1.0)

    # 34 & 35. vwap_bands[fix] & value_area[fix] (Dalton 2013: TREND seatbelt + responsive fade only in balance)
    if vp is not None:
        vw_b = float(getattr(vp, "vwap", 0.0) or 0.0)
        vw_sd = float(getattr(vp, "vwap_std", 0.0) or 0.0)
        if vw_b > 0 and vw_sd > 0:
            z_vw = (px - vw_b) / vw_sd
            if regime != "TREND":
                thr_z = 1.25 if regime == "RANGE" else 2.0
                if z_vw >= thr_z:
                    _emit("vwap_bands[fix]", "ACT_FIX", 60, -1.0)
                elif z_vw <= -thr_z:
                    _emit("vwap_bands[fix]", "ACT_FIX", 60, 1.0)
            elif c3 is not None and len(c3) >= 2 and float(h3_a[-1] - l3_a[-1]) > 0:
                clv_climax = float(c3[-1] - l3_a[-1]) / float(h3_a[-1] - l3_a[-1])
                if z_vw >= 2.5 and clv_climax <= 0.35:
                    _emit("vwap_bands[fix]", "ACT_FIX", 60, -1.0)
                elif z_vw <= -2.5 and clv_climax >= 0.65:
                    _emit("vwap_bands[fix]", "ACT_FIX", 60, 1.0)

        vah = float(getattr(vp, "value_area_high", 0.0) or 0.0)
        val_p = float(getattr(vp, "value_area_low", 0.0) or 0.0)
        if vah > val_p > 0:
            if regime == "TREND":
                if px > vah and mtf_bias >= 0:
                    _emit("value_area[fix]", "ACT_FIX", 60, 1.0)
                elif px < val_p and mtf_bias <= 0:
                    _emit("value_area[fix]", "ACT_FIX", 60, -1.0)
            else:
                if 0 <= (vah - px) <= 0.8 * atr_eff:
                    _emit("value_area[fix]", "ACT_FIX", 60, -1.0)
                elif 0 <= (px - val_p) <= 0.8 * atr_eff:
                    _emit("value_area[fix]", "ACT_FIX", 60, 1.0)

    # 36. cvd_divergence[fix]: missing L3546 TREND seatbelt + closed-bar price rejection against passive absorption
    div_v = float(getattr(snap, "divergence", 0.0) or 0.0)
    if abs(div_v) >= 0.35 and c3 is not None and len(c3) >= 2:
        if div_v > 0 and float(c3[-1]) > float(o3[-1]) and not (regime == "TREND" and mtf_bias < 0):
            _emit("cvd_divergence[fix]", "ACT_FIX", 60, 1.0)
        elif div_v < 0 and float(c3[-1]) < float(o3[-1]) and not (regime == "TREND" and mtf_bias > 0):
            _emit("cvd_divergence[fix]", "ACT_FIX", 60, -1.0)

    # 37. absorption[fix]: >=2 net absorbed walls + active-TIMEFRAME price has not broken through wall
    if of is not None:
        ab_net = float(getattr(of, "absorption_net", 0.0) or 0.0)
        dp3 = float(c_arr[-1] - o_arr[-min(tf_eff, n_m1)]) if (c_arr is not None and n_m1 >= 2) else 0.0
        if ab_net >= 2.0 and dp3 >= -0.20 * atr_eff and not (regime == "TREND" and mtf_bias < 0):
            _emit("absorption[fix]", "ACT_FIX", 15, 1.0)
        elif ab_net <= -2.0 and dp3 <= 0.20 * atr_eff and not (regime == "TREND" and mtf_bias > 0):
            _emit("absorption[fix]", "ACT_FIX", 15, -1.0)

    # 38. footprint_delta[fix]: >=0.18 deadband + Kyle's lambda > 0 over active-TIMEFRAME bar
    if fp is not None:
        fp_imb = float(getattr(fp, "delta_imbalance", 0.0) or 0.0)
        dp3_fp = float(c_arr[-1] - o_arr[-min(tf_eff, n_m1)]) if (c_arr is not None and n_m1 >= 2) else 0.0
        if fp_imb >= 0.18 and dp3_fp > 0.0:
            _emit("footprint_delta[fix]", "ACT_FIX", 15, 1.0)
        elif fp_imb <= -0.18 and dp3_fp < 0.0:
            _emit("footprint_delta[fix]", "ACT_FIX", 15, -1.0)

    return out


# --------------------------------------------------------------------------- #
# Reset stateful Step 2 singletons between replayed days (100% isolation)
# --------------------------------------------------------------------------- #
def _reset_step2_singletons() -> None:
    s2._PERSISTENT_L2 = None
    s2._PERSISTENT_L2_TIMESTAMP = None
    s2._PERSISTENT_L2_INSTRUMENT = None
    if hasattr(s2, "_A1_CACHE") and isinstance(s2._A1_CACHE, dict):
        s2._A1_CACHE.clear()
    if hasattr(s2, "_POWER_STREAK_STATE") and isinstance(s2._POWER_STREAK_STATE, dict):
        s2._POWER_STREAK_STATE.update({"symbol": None, "direction": "NEITHER", "cycles": 0})
    try:
        import signal_team as st_mod
        if hasattr(st_mod, "_BOOK"):
            st_mod._BOOK = None
    except Exception:
        pass
    try:
        import aggressive_trigger as ag_mod
        if hasattr(ag_mod, "EYES") and hasattr(ag_mod.EYES, "reset"):
            ag_mod.EYES.reset()
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Core Single-Day Raw Replay through Current App Version
# --------------------------------------------------------------------------- #
def replay_single_day(
    day_str: str,
    root_dir: Path,
    data_dir: Path,
    every_s: int = 60,
    window_str: str = "08:00-23:00",
    prewarm_min: int = 120,
) -> Dict[str, Any]:
    t_wall0 = time.perf_counter()
    d_obj = date.fromisoformat(day_str)
    w0_s, w1_s = window_str.split("-")
    h0, m0 = (int(x) for x in w0_s.split(":"))
    h1, m1 = (int(x) for x in w1_s.split(":"))

    win_start_local = datetime(d_obj.year, d_obj.month, d_obj.day, h0, m0, tzinfo=BUDA_TZ)
    win_end_local = datetime(d_obj.year, d_obj.month, d_obj.day, h1, m1, tzinfo=BUDA_TZ)
    win_start_utc = win_start_local.astimezone(timezone.utc)
    win_end_utc = win_end_local.astimezone(timezone.utc)

    stream_start_ep = (win_start_utc - timedelta(minutes=prewarm_min)).timestamp()
    cme_open_ep = _cme_session_open_ep(d_obj)
    tick_stream_start_ep = min(stream_start_ep, cme_open_ep)
    day_00z_ep = datetime(d_obj.year, d_obj.month, d_obj.day, 0, 0, tzinfo=timezone.utc).timestamp()
    # Scan up to 78h prior for ticks (captures Friday + Sunday history for Monday H1/M15 MTF & PDH/PDL/Settle)
    htf_scan_start_ep = min(tick_stream_start_ep, cme_open_ep - 78 * 3600.0)
    # Keep +3h post-window trades for honest 180-minute forward outcome walks
    walk_end_ep = (win_end_utc + timedelta(minutes=185)).timestamp()
    tick_allowed_dates = {
        f"{d_obj - timedelta(days=3):%Y-%m-%d}",
        f"{d_obj - timedelta(days=2):%Y-%m-%d}",
        f"{d_obj - timedelta(days=1):%Y-%m-%d}",
        day_str,
        f"{d_obj + timedelta(days=1):%Y-%m-%d}",
    }
    mbo_allowed_dates = {
        f"{d_obj - timedelta(days=1):%Y-%m-%d}",
        day_str,
        f"{d_obj + timedelta(days=1):%Y-%m-%d}",
    }

    tick_files = _discover_raw_files("ticks", root_dir, data_dir, day_str=day_str)
    mbo_files = _discover_raw_files("mbo", root_dir, data_dir, day_str=day_str)

    if not tick_files:
        return {"day": day_str, "ok": False, "reason": "no raw ticks files found"}

    # Pass 1 (single-pass tick read):
    # - hist_trades_sz (`htf_scan_start_ep <= ep < cme_open_ep`): prior trading day prints for H1/M15 MTF & PDH/PDL/Settle
    # - trades (`ep >= min(cme_open_ep, day_00z_ep, stream_start_ep)`): today's CME session + walkout prints
    # - buffered_ticks (`tick_stream_start_ep <= ep <= win_end_ep`): raw tick rows for Step 1 tail & pure session VWAP/Profile
    hist_trades_sz: List[Tuple[float, float, float]] = []
    trades: List[Tuple[float, float]] = []
    trades_sz: List[Tuple[float, float, float]] = []
    seen_prints: set[Tuple[float, float, float]] = set()
    inst_counts: Counter = Counter()
    buffered_ticks: List[Tuple[float, str, str]] = []
    raw_tick_rows = 0
    win_end_ep = win_end_utc.timestamp()
    today_session_floor_ep = min(cme_open_ep, day_00z_ep, stream_start_ep)

    for ep, line in _iter_filtered_csv_events(tick_files, htf_scan_start_ep, walk_end_ep, tick_allowed_dates):
        raw_tick_rows += 1
        parts = line.rstrip("\r\n").split(",") if '"' not in line else next(csv.reader([line.rstrip("\r\n")]))
        if len(parts) < 7:
            continue
        ev = parts[1].strip()
        inst = parts[6].strip()
        if inst and ep >= stream_start_ep:
            inst_counts[inst] += 1
        if tick_stream_start_ep <= ep <= win_end_ep:
            buffered_ticks.append((ep, line, inst))
        if ev in ("Last", "Trade", "trade"):
            try:
                px = float(parts[2])
                sz = float(parts[3] or 0.0)
            except ValueError:
                continue
            if 500.0 < px < 20000.0:
                pk = (round(ep, 4), px, sz)
                if pk not in seen_prints:
                    seen_prints.add(pk)
                    sz_eff = sz if sz > 0 else 1.0
                    if ep < today_session_floor_ep:
                        hist_trades_sz.append((ep, px, sz_eff))
                    else:
                        trades.append((ep, px))
                        trades_sz.append((ep, px, sz_eff))

    if not trades:
        return {"day": day_str, "ok": False, "reason": f"0 trade prints on {day_str}"}

    hist_trades_sz.sort(key=lambda x: x[0])
    trades.sort(key=lambda x: x[0])
    trades_sz.sort(key=lambda x: x[0])
    trade_eps = [t[0] for t in trades]
    day_prints = sum(1 for ep, _ in trades if win_start_utc.timestamp() <= ep <= win_end_utc.timestamp())
    if day_prints == 0:
        return {"day": day_str, "ok": False, "reason": f"0 in-window prints on {day_str}"}

    bar_keys, walk_bars = _build_m1_walk_bars(trades)
    if not inst_counts and buffered_ticks:
        for _ep_b, _ln_b, inst_b in buffered_ticks:
            if inst_b:
                inst_counts[inst_b] += 1
    dominant_inst = inst_counts.most_common(1)[0][0] if inst_counts else getattr(config, "DATA_SYMBOL", "GC 12-26")

    # Active TIMEFRAME-linked 1x, 2x, 3x, 4x bar horizons (3, 6, 9, 12m on M3; 5, 10, 15, 20m on M5)
    tf_raw = os.environ.get("TIMEFRAME") or getattr(config, "TIMEFRAME", "M3") or "M3"
    tf_min = ins._parse_tf_minutes(tf_raw) or 3
    h1, h2, h3, h4 = tf_min, 2 * tf_min, 3 * tf_min, 4 * tf_min
    tf_label = f"M{tf_min}" if tf_min < 60 else f"H{tf_min // 60}"
    tol_tf_s = max(90.0, tf_min * 45.0)

    # Ruler constants (matching audit_day / decision_ledger / shadow_replay)
    atr_const = float(os.environ.get("AUDIT_ATR", "3.18") or 3.18)
    spread_cost = float(os.environ.get("AUDIT_SPREAD", "0.50") or 0.50)
    sl_mult = float(getattr(config, "STOP_LOSS_ATR_MULT", 2.0) or 2.0)
    tp_mult = float(getattr(config, "TAKE_PROFIT_ATR_MULT", 3.5) or 3.5)
    conf_thr = float(
        os.environ.get("CONFIDENCE_THRESHOLD")
        or getattr(config, "CONFIDENCE_THRESHOLD", None)
        or getattr(config, "AI_CONFIDENCE_THRESHOLD", 35.0)
        or 35.0
    )
    max_hold_min = int(float(os.environ.get("AUDIT_MAX_HOLD_MIN", "180") or 180))

    # Pass 2: stream buffered ticks + raw MBO chronologically into isolated Step 1 + Step 2!
    orig_time = time.time
    orig_now_utc = getattr(bbp, "_now_utc_epoch", None)
    orig_data_dir = getattr(config, "DATA_DIR", Path("data"))
    orig_data_source = getattr(config, "DATA_SOURCE", "bookmapbridge")
    orig_data_symbol = getattr(config, "DATA_SYMBOL", "")
    clock_ref = {"ep": win_start_utc.timestamp()}

    def _fake_time() -> float:
        return clock_ref["ep"]

    cycles_out: List[Dict[str, Any]] = []
    judge_stats: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "calls": 0, "scored": 0, "hits": 0, "gross_pts": 0.0, "pts": 0.0,
            "b1_sum": 0.0, "b1_n": 0, "b4_sum": 0.0, "b4_n": 0,
            "buy": 0, "sell": 0, "w": 0.0, "clk": 30,
        }
    )
    bench_judge_stats: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "cat": "RETIRED", "clk": 30, "calls": 0, "scored": 0, "hits": 0,
            "gross_pts": 0.0, "pts": 0.0, "b1_sum": 0.0, "b1_n": 0,
            "b4_sum": 0.0, "b4_n": 0, "buy": 0, "sell": 0,
        }
    )
    asia_state: Dict[str, float] = {"hi": 0.0, "lo": 0.0}
    asia_end_ep = day_00z_ep + 7 * 3600.0
    sess_trade_ptr = 0
    mbo_streamed = 0
    errors = 0

    with tempfile.TemporaryDirectory(prefix=f"raw_replay_{day_str}_") as tmp_dir:
        try:
            config.DATA_DIR = Path(tmp_dir)
            config.DATA_SOURCE = "bookmapbridge"
            config.DATA_SYMBOL = dominant_inst
            time.time = _fake_time
            if orig_now_utc is not None:
                bbp._now_utc_epoch = _fake_time
            _reset_step2_singletons()

            tail = bbp._BridgeTail.__new__(bbp._BridgeTail)
            tail.path = ""
            import threading as _th
            tail.lock = _th.Lock()
            tail.instruments = {}
            tail.line_count = 0
            tail.bad_lines = 0
            tail.direct_side_hits = 0

            mbo_tail = bbp._MboTail.__new__(bbp._MboTail)
            mbo_tail.path = ""
            mbo_tail.lock = _th.Lock()
            mbo_tail.instruments = {}
            mbo_tail.line_count = 0
            mbo_tail.bad_lines = 0

            provider = _OfflineBridgeProvider(tail, mbo_tail if mbo_files else None)
            # Dual causal candle builders:
            # 1) `session_m1_builder` (max_bars=960 = 16h): pure current CME Globex session (18:00 ET -> tc_ep)
            #    so VWAP, vwap_bands, vwap_zscore, poc_day, value_area never mix yesterday's session and never roll off mid-day.
            # 2) `htf_m1_builder` (max_bars=1800 = 30h): multi-day M1 history for true wall-clock H1, M15, M5, M3 resampling!
            session_m1_builder = _IncrementalM1Builder(max_bars=960)
            htf_m1_builder = _IncrementalM1Builder(max_bars=1800)

            # Pre-seed prior-trading-day history (`< today_session_floor_ep`) into `htf_m1_builder` & `session_levels`
            # (PDH, PDL, PDC, and 13:29-13:30 ET CME volume-weighted prev_settlement — 100% causal before session open!)
            last_hist_min = -1
            for hep, hpx, hsz in hist_trades_sz:
                htf_m1_builder.add_trade(hep, hpx, hsz)
                h_dt = datetime.fromtimestamp(hep, tz=timezone.utc)
                h_et = session_levels._to_us_eastern(h_dt)
                is_settle_win = (h_et.hour == 13 and h_et.minute in (28, 29, 30))
                h_min_k = int(hep // 60.0)
                if is_settle_win or h_min_k != last_hist_min:
                    last_hist_min = h_min_k
                    session_levels.update(hpx, tmp_dir, volume=hsz, now=h_dt)

            inst_flt = dominant_inst if inst_counts else None
            tick_iter = (
                (ep_t, ln_t)
                for ep_t, ln_t, inst_t in buffered_ticks
                if not inst_flt or inst_t == inst_flt
            )
            buffered_ticks = []  # free reference once generator wraps the underlying list
            mbo_iter = iter(_iter_filtered_csv_events(
                mbo_files, stream_start_ep, win_end_ep, mbo_allowed_dates, inst_filter=inst_flt
            )) if mbo_files else iter(())

            nxt_tick = next(tick_iter, None)
            nxt_mbo = next(mbo_iter, None)
            mbo_step_ring: deque = deque()

            # Build cycle grid inside [first_in_window_trade .. last_in_window_trade]
            in_win_eps = [ep for ep in trade_eps if win_start_utc.timestamp() <= ep <= win_end_utc.timestamp()]
            first_ep = math.ceil(in_win_eps[0] / every_s) * every_s
            last_ep = in_win_eps[-1]

            tc_ep = float(first_ep)
            step_idx = 0
            while tc_ep <= last_ep:
                step_idx += 1
                clock_ref["ep"] = tc_ep
                tc_dt = datetime.fromtimestamp(tc_ep, tz=timezone.utc)

                # Advance raw ticks strictly <= tc_ep (ZERO look-ahead!)
                while nxt_tick is not None and nxt_tick[0] <= tc_ep:
                    t_ep, t_line = nxt_tick
                    tail._handle_line(t_line)
                    if ",Last," in t_line or ",Trade," in t_line:
                        ps = t_line.rstrip("\r\n").split(",") if '"' not in t_line else next(csv.reader([t_line.rstrip("\r\n")]))
                        if len(ps) >= 4:
                            try:
                                p_val = float(ps[2])
                                s_val = float(ps[3] or 1.0)
                                htf_m1_builder.add_trade(t_ep, p_val, s_val)
                                if t_ep >= cme_open_ep:
                                    session_m1_builder.add_trade(t_ep, p_val, s_val)
                            except ValueError:
                                pass
                    nxt_tick = next(tick_iter, None)

                # Advance raw MBO strictly <= tc_ep (ZERO look-ahead!):
                # Overflow rows (>3000 before tc_ep) fast-update active_orders; final <=3000 run exact _handle_line.
                mbo_step_ring.clear()
                while nxt_mbo is not None and nxt_mbo[0] <= tc_ep:
                    if len(mbo_step_ring) >= 3000:
                        _fast_mbo_track_only(mbo_tail, mbo_step_ring.popleft())
                    mbo_step_ring.append(nxt_mbo[1])
                    mbo_streamed += 1
                    nxt_mbo = next(mbo_iter, None)
                for m_line in mbo_step_ring:
                    mbo_tail._handle_line(m_line)
                for mst in mbo_tail.instruments.values():
                    if len(mst.mbo_events) > 5000:
                        del mst.mbo_events[:-3200]

                # Keep 8h rolling tick window matching live `config.BOOKMAP_WINDOW_SECONDS = 28800`
                if step_idx % 15 == 1:
                    tail._prune_old_ticks(28800.0)
                sess_c_dict = session_m1_builder.to_candles_dict()
                provider._prebuilt_candles = sess_c_dict if sess_c_dict else htf_m1_builder.to_candles_dict(max_tail=360)

                md = provider.acquire(symbol=dominant_inst)
                md["candles_is_m1"] = True
                md["candles_tf_minutes"] = tf_min
                md["candles_tf"] = (
                    md["candles"] if tf_min <= 1
                    else htf_m1_builder.to_resampled_dict(tf_min, completed_before_ep=tc_ep)
                )
                ladder_fn = getattr(config, "mtf_ladder_for_base_tf", None)
                active_ladder = ladder_fn(tf_min) if callable(ladder_fn) else [("M9", 9), ("M15", 15), ("H1", 60)]
                for _r_lbl, _r_min in active_ladder:
                    md[f"candles_{str(_r_lbl).lower()}"] = htf_m1_builder.to_resampled_dict(
                        int(_r_min), completed_before_ep=tc_ep
                    )
                md.setdefault("candles_h1", htf_m1_builder.to_resampled_dict(60, completed_before_ep=tc_ep))
                md.setdefault("candles_m15", htf_m1_builder.to_resampled_dict(15, completed_before_ep=tc_ep))
                md.setdefault("candles_m5", htf_m1_builder.to_resampled_dict(5, completed_before_ep=tc_ep))
                md.setdefault("candles_m3", htf_m1_builder.to_resampled_dict(3, completed_before_ep=tc_ep))
                md["_htf_m1_240"] = htf_m1_builder.to_candles_dict(max_tail=240)
                md["macro"] = {}
                md["news"] = {"headlines": [], "events": [], "fetch_calendar": False}

                last_age = md.get("last_trade_age_seconds")
                if not md.get("price") or (last_age is not None and float(last_age) > 120.0):
                    tc_ep += every_s
                    continue

                try:
                    snap = s2.analyze_market(md, now=tc_dt)
                except Exception as exc:
                    errors += 1
                    cycles_out.append({
                        "dt_utc": tc_dt, "price": float(md.get("price") or 0.0),
                        "error": f"{type(exc).__name__}: {exc}",
                    })
                    tc_ep += every_s
                    continue

                px = float(getattr(snap, "price", 0.0) or md.get("price") or 0.0)
                sig_dir = str(getattr(snap, "signal_direction", "NEUTRAL") or "NEUTRAL").upper()
                sig_conf = float(getattr(snap, "confidence", 0.0) or 0.0)
                sig_str = float(getattr(snap, "signal_strength", 0.0) or 0.0)
                regime = str(getattr(snap, "regime", "NEUTRAL") or "NEUTRAL").upper()
                atr_live = float(getattr(getattr(snap, "volatility", None), "atr", 0.0) or atr_const)
                if atr_live <= 0:
                    atr_live = atr_const

                pw = getattr(snap, "power", None) or {}
                pw_dir = str(pw.get("direction", "NEITHER") or "NEITHER").upper()
                pw_reg = str((pw.get("regime_diagnostics") or {}).get("market_regime") or regime).upper()
                pw_act = float(pw.get("activity", 0.0) or 0.0)
                pw_bars = int(((pw.get("regime_diagnostics") or {}).get("bars_used") or 0))

                shot = getattr(snap, "shooting_plan", None) or {}
                # Stage 1 Fix: shooting_team.plan_shot() uses key "shot" ("GO" | "WAIT") + "why" list
                shot_act = str(shot.get("shot") or shot.get("action") or "WAIT").upper()
                shot_style = str(shot.get("entry_style") or "NONE")
                raw_why = shot.get("why")
                if isinstance(raw_why, (list, tuple)) and raw_why:
                    shot_why = "; ".join(str(x) for x in raw_why[:3])
                else:
                    shot_why = str(
                        (shot.get("details") or {}).get("primary_reason")
                        or shot.get("reason")
                        or shot.get("blocker_code")
                        or ""
                    )
                shot_a2_burst = bool(shot.get("a2_burst") or shot.get("aggressive_trigger_confirmed"))

                fg = (getattr(snap, "shadow_candidates", None) or {}).get("family_gate") or {}
                fg_ok = bool(fg.get("ok", False))
                fg_fams = list(fg.get("agree_families") or [])

                # Current-version gate classification (Step 2 -> Shooting -> Threshold)
                pw_side = "BUY" if pw_dir == "UP" else ("SELL" if pw_dir == "DOWN" else "NEITHER")
                if sig_dir not in ("BUY", "SELL"):
                    gate_stage = "NEUTRAL (no panel signal)"
                elif sig_conf < conf_thr:
                    gate_stage = f"BLOCKED: Conf < {conf_thr:.0f}%"
                elif shot_act == "GO":
                    gate_stage = f"SHOOTING GO ({shot_style})"
                elif pw_side == sig_dir:
                    gate_stage = f"CONF>={conf_thr:.0f}% + POWER ALIGNED (Shooting={shot_act})"
                else:
                    gate_stage = f"CONF>={conf_thr:.0f}% ONLY (POWER={pw_dir})"

                # Forward tape outcomes for this cycle:
                # Stage 1: TIMEFRAME-linked 1x, 2x, 3x, 4x bar horizons (3, 6, 9, 12m on M3; 5, 10, 15, 20m on M5)
                pb1 = _price_after(trade_eps, trades, tc_ep, h1, tol_s=tol_tf_s)
                pb2 = _price_after(trade_eps, trades, tc_ep, h2, tol_s=tol_tf_s)
                pb3 = _price_after(trade_eps, trades, tc_ep, h3, tol_s=tol_tf_s)
                pb4 = _price_after(trade_eps, trades, tc_ep, h4, tol_s=tol_tf_s)
                hi4b, lo4b = _excursion_30m(trade_eps, trades, tc_ep, h4)

                # Advance causal Asia box (00:00..07:00Z) and CME/COMEX session_levels strictly <= tc_ep
                sess_state: Dict[str, Any] = {}
                last_sess_min = -1
                while sess_trade_ptr < len(trades_sz) and trades_sz[sess_trade_ptr][0] <= tc_ep:
                    tep, tpx, tsz = trades_sz[sess_trade_ptr]
                    if day_00z_ep <= tep < asia_end_ep:
                        asia_state["hi"] = max(asia_state["hi"], tpx)
                        asia_state["lo"] = min(asia_state["lo"], tpx) if asia_state["lo"] > 0 else tpx
                    t_dt_s = datetime.fromtimestamp(tep, tz=timezone.utc)
                    t_et_s = session_levels._to_us_eastern(t_dt_s)
                    is_settle_s = (t_et_s.hour == 13 and t_et_s.minute in (28, 29, 30))
                    cur_min_k = int(tep // 60.0)
                    if is_settle_s or cur_min_k != last_sess_min:
                        last_sess_min = cur_min_k
                        sess_state = session_levels.update(tpx, tmp_dir, volume=tsz, now=t_dt_s)
                    sess_trade_ptr += 1
                if not sess_state and px > 0:
                    sess_state = session_levels.update(px, tmp_dir, now=tc_dt)

                # Active judges & per-judge clock grading (with Gross vs Net + M3 1b/4b path)
                jvotes_raw = getattr(snap, "judge_votes", None) or []
                active_votes: List[Dict[str, Any]] = []
                for v in jvotes_raw:
                    if not isinstance(v, dict) or bool(v.get("retired")):
                        continue
                    jname = str(v.get("judge") or "").strip()
                    if not jname or jname.lower() in config.RETIRED_JUDGES or jname.lower() in judge_families.RETIRED:
                        continue
                    vd = float(v.get("dir", 0.0) or 0.0)
                    vw = float(v.get("weight", 1.0) or 0.0)
                    if vw <= 0.0:
                        continue
                    active_votes.append({"judge": jname, "dir": vd, "weight": vw, "raw": str(v.get("raw") or "")})
                    if vd != 0.0:
                        jst = judge_stats[jname]
                        jst["calls"] += 1
                        jst["w"] = vw
                        h_min = ins.judge_clock(jname, bar_seconds=tf_min * 60)
                        jst["clk"] = h_min
                        if vd > 0:
                            jst["buy"] += 1
                        else:
                            jst["sell"] += 1
                        if pb1 is not None:
                            jst["b1_sum"] += (pb1 - px) if vd > 0 else (px - pb1)
                            jst["b1_n"] += 1
                        if pb4 is not None:
                            jst["b4_sum"] += (pb4 - px) if vd > 0 else (px - pb4)
                            jst["b4_n"] += 1
                        p_fut = _price_after(trade_eps, trades, tc_ep, h_min)
                        if p_fut is not None:
                            mv = (p_fut - px) if vd > 0 else (px - p_fut)
                            jst["scored"] += 1
                            if mv > 0:
                                jst["hits"] += 1
                            jst["gross_pts"] += mv
                            jst["pts"] += (mv - spread_cost)

                # Retired, Dormant, Previous & Repaired-Candidate Bench Checkup (100% Non-Voting Shadow)
                for b_name, b_cat, b_clk, b_dir in _evaluate_retired_and_previous_bench(
                    snap, md, tc_dt, asia_state, sess_state, tf_min=tf_min
                ):
                    bst = bench_judge_stats[b_name]
                    bst["cat"] = b_cat
                    bst["clk"] = b_clk
                    bst["calls"] += 1
                    if b_dir > 0:
                        bst["buy"] += 1
                    else:
                        bst["sell"] += 1
                    if pb1 is not None:
                        bst["b1_sum"] += (pb1 - px) if b_dir > 0 else (px - pb1)
                        bst["b1_n"] += 1
                    if pb4 is not None:
                        bst["b4_sum"] += (pb4 - px) if b_dir > 0 else (px - pb4)
                        bst["b4_n"] += 1
                    p_bfut = _price_after(trade_eps, trades, tc_ep, b_clk)
                    if p_bfut is not None:
                        bmv = (p_bfut - px) if b_dir > 0 else (px - p_bfut)
                        bst["scored"] += 1
                        if bmv > 0:
                            bst["hits"] += 1
                        bst["gross_pts"] += bmv
                        bst["pts"] += (bmv - spread_cost)

                # Retain legacy 5/15/30m pointers for backward compatibility
                p5 = pb1 if h1 == 5 else _price_after(trade_eps, trades, tc_ep, 5)
                p15 = pb3 if h3 == 15 else _price_after(trade_eps, trades, tc_ep, 15)
                p30 = _price_after(trade_eps, trades, tc_ep, 30)
                hi30, lo30 = _excursion_30m(trade_eps, trades, tc_ep, 30)

                fav4b = adv4b = None
                fav30 = adv30 = None
                signed_4b: List[Optional[float]] = [None, None, None, None]
                trick_code, trick_lbl = "NO_SIGNAL", "neutral (no directional call)"
                fix_out, fix_pts = "NO_SIGNAL", 0.0
                c3_out, c3_pts = "NO_SIGNAL", 0.0
                tr10_out, tr10_pts = "NO_SIGNAL", 0.0
                a2_fix_pts = a2_atr_pts = None

                if sig_dir in ("BUY", "SELL") and px > 0:
                    is_b = sig_dir == "BUY"
                    if hi4b is not None and lo4b is not None:
                        fav4b = (hi4b - px) if is_b else (px - lo4b)
                        adv4b = (px - lo4b) if is_b else (hi4b - px)
                    if hi30 is not None and lo30 is not None:
                        fav30 = (hi30 - px) if is_b else (px - lo30)
                        adv30 = (px - lo30) if is_b else (hi30 - px)
                    signed_4b = [
                        ((pf - px) if is_b else (px - pf)) if pf is not None else None
                        for pf in (pb1, pb2, pb3, pb4)
                    ]
                    trick_code, trick_lbl = ins.classify_market_trick(sig_dir, signed_4b)

                    # 1) Fixed benchmark SL/TP ruler
                    sl_fix = (px - sl_mult * atr_const) if is_b else (px + sl_mult * atr_const)
                    tp_fix = (px + tp_mult * atr_const) if is_b else (px - tp_mult * atr_const)
                    fix_out, fix_pts = _walk_bracket(
                        bar_keys, walk_bars, tc_ep, sig_dir, px, sl_fix, tp_fix, max_hold_min, spread_cost
                    )

                    # 2) Current C3 Wall + 0.5xATR Trail vs 1.0xATR Trail
                    w_sl, w_tp, _ = sr.compute_wall_brackets(sig_dir, px, atr_live, snap)
                    use_sl = w_sl if w_sl is not None else ((px - sl_mult * atr_live) if is_b else (px + sl_mult * atr_live))
                    use_tp = w_tp if w_tp is not None else ((px + tp_mult * atr_live) if is_b else (px - tp_mult * atr_live))
                    vg_ok, _ = sr.value_gate(sig_dir, px, use_sl, use_tp, atr_live, config)
                    if vg_ok:
                        c3_out, c3_pts = _walk_bracket(
                            bar_keys, walk_bars, tc_ep, sig_dir, px, use_sl, use_tp,
                            max_hold_min, spread_cost, trail_atr=atr_live, trail_dist_mult=0.5,
                        )
                        tr10_out, tr10_pts = _walk_bracket(
                            bar_keys, walk_bars, tc_ep, sig_dir, px, use_sl, use_tp,
                            max_hold_min, spread_cost, trail_atr=atr_live, trail_dist_mult=1.0,
                        )
                    else:
                        c3_out, c3_pts = "VG_SKIP", 0.0
                        tr10_out, tr10_pts = "VG_SKIP", 0.0

                    # 3) Bookmark #8 check on Shooting GO / A2 cycles: 1.80 fixed vs 1.0xATR floor
                    if shot_act == "GO" or "AGGRESSIVE" in shot_style.upper() or shot_a2_burst:
                        d18 = 1.80
                        _, a2_fix_pts = _walk_bracket(
                            bar_keys, walk_bars, tc_ep, sig_dir, px,
                            (px - d18) if is_b else (px + d18),
                            (px + d18) if is_b else (px - d18),
                            45, spread_cost,
                        )
                        datr = max(1.80, round(1.0 * atr_live, 2))
                        _, a2_atr_pts = _walk_bracket(
                            bar_keys, walk_bars, tc_ep, sig_dir, px,
                            (px - datr) if is_b else (px + datr),
                            (px + datr) if is_b else (px - datr),
                            45, spread_cost,
                        )

                dq = md.get("data_quality") or {}
                l2d = md.get("l2_diagnostics") or {}
                cycles_out.append({
                    "dt_utc": tc_dt,
                    "buda_hhmmss": tc_dt.astimezone(BUDA_TZ).strftime("%H:%M:%S"),
                    "buda_hour": tc_dt.astimezone(BUDA_TZ).hour,
                    "price": px,
                    "bid": float(md.get("bid") or 0.0),
                    "ask": float(md.get("ask") or 0.0),
                    "spread_pts": round(float(md.get("ask") or 0.0) - float(md.get("bid") or 0.0), 2)
                    if (md.get("ask") and md.get("bid")) else None,
                    "l2_status": str(dq.get("level2") or "?"),
                    "depth_levels": f"{l2d.get('fresh_bid_levels', 0)}/{l2d.get('fresh_ask_levels', 0)}",
                    "mbo_window": len(md.get("order_events") or []),
                    "direction": sig_dir,
                    "confidence": round(sig_conf, 1),
                    "strength": round(sig_str, 1),
                    "regime": regime,
                    "atr": round(atr_live, 2),
                    "power_dir": pw_dir,
                    "power_regime": pw_reg,
                    "power_activity": round(pw_act, 3),
                    "power_bars": pw_bars,
                    "family_ok": fg_ok,
                    "agree_families": fg_fams,
                    "shot_action": shot_act,
                    "shot_style": shot_style,
                    "shot_reason": shot_why,
                    "shot_a2_burst": shot_a2_burst,
                    "gate_stage": gate_stage,
                    "active_votes": active_votes,
                    "pb1": pb1, "pb2": pb2, "pb3": pb3, "pb4": pb4,
                    "signed_4b": signed_4b,
                    "fav4b": fav4b, "adv4b": adv4b,
                    "trick_code": trick_code, "trick_label": trick_lbl,
                    "p5": p5, "p15": p15, "p30": p30,
                    "fav30": fav30, "adv30": adv30,
                    "fix_out": fix_out, "fix_pts": fix_pts,
                    "c3_out": c3_out, "c3_pts": c3_pts,
                    "tr10_out": tr10_out, "tr10_pts": tr10_pts,
                    "a2_fix_pts": a2_fix_pts, "a2_atr_pts": a2_atr_pts,
                })
                tc_ep += every_s
        finally:
            time.time = orig_time
            if orig_now_utc is not None:
                bbp._now_utc_epoch = orig_now_utc
            config.DATA_DIR = orig_data_dir
            config.DATA_SOURCE = orig_data_source
            config.DATA_SYMBOL = orig_data_symbol
            _reset_step2_singletons()

    pxs_win = [px for ep, px in trades if win_start_utc.timestamp() <= ep <= win_end_utc.timestamp()]
    day_range = (max(pxs_win) - min(pxs_win)) if pxs_win else 0.0
    day_net = (pxs_win[-1] - pxs_win[0]) if pxs_win else 0.0
    trendiness = (abs(day_net) / day_range) if day_range > 0 else 0.0

    return {
        "day": day_str,
        "ok": True,
        "instrument": dominant_inst,
        "timeframe": tf_label,
        "tf_horizons": [h1, h2, h3, h4],
        "prints": day_prints,
        "raw_tick_rows": raw_tick_rows,
        "mbo_streamed": mbo_streamed,
        "day_range": round(day_range, 2),
        "day_net": round(day_net, 2),
        "trendiness": round(trendiness, 2),
        "day_type": "TREND" if trendiness >= 0.45 else ("LAKE/RANGE" if trendiness <= 0.25 else "MIXED"),
        "conf_threshold": conf_thr,
        "cycles": cycles_out,
        "judge_stats": dict(judge_stats),
        "bench_judge_stats": dict(bench_judge_stats),
        "errors": errors,
        "elapsed_s": round(time.perf_counter() - t_wall0, 2),
    }


def _replay_worker(task: Tuple[str, str, str, int, str]) -> Dict[str, Any]:
    """Top-level multiprocessing worker for parallel multi-day replay."""
    day_s, root_s, data_s, every_s, win_s = task
    return replay_single_day(
        day_s, root_dir=Path(root_s), data_dir=Path(data_s), every_s=every_s, window_str=win_s
    )


# --------------------------------------------------------------------------- #
# HTML Builder 1: Single-Day "Every decision" Page (Current Version on Raw Data)
# --------------------------------------------------------------------------- #
def build_day_decisions_html(res: Dict[str, Any]) -> str:
    day_str = res["day"]
    tf_lbl = str(res.get("timeframe") or "M3")
    h_list = list(res.get("tf_horizons") or [3, 6, 9, 12])
    h1, h2, h3, h4 = h_list[0], h_list[1], h_list[2], h_list[3]
    cycles = [c for c in res["cycles"] if not c.get("error")]
    cards: List[str] = []

    for c in cycles:
        d = c["direction"]
        conf = c["confidence"]
        px = c["price"]
        pb1 = c.get("pb1")
        pb2 = c.get("pb2")
        pb3 = c.get("pb3")
        pb4 = c.get("pb4")
        t_code = str(c.get("trick_code") or "NO_SIGNAL")
        t_lbl = str(c.get("trick_label") or "-")
        is_sig = d in ("BUY", "SELL")
        is_c35 = is_sig and conf >= res["conf_threshold"]
        is_c65 = is_sig and conf >= 65.0
        is_pw = c["power_dir"] in ("UP", "DOWN")
        is_go = c["shot_action"] == "GO"

        tags = ["all"]
        if is_sig:
            tags.append("sig")
        if is_c35:
            tags.append("c35")
        if is_c65:
            tags.append("c65")
        if is_pw:
            tags.append("pw")
        if is_go:
            tags.append("go")
        if t_code == "FAKEOUT_WIN":
            tags.append("shake")
        if t_code == "TRAP_REVERSAL":
            tags.append("trap")

        pill_cls = "ok" if is_go else ("warn" if is_c35 else "")
        d_cls = "up" if d == "BUY" else ("dn" if d == "SELL" else "q")
        jrows = "".join(
            f"<tr><td>{_esc(v['judge'])}</td>"
            f"<td class={'up' if v['dir'] > 0 else 'dn'}>{'BUY' if v['dir'] > 0 else 'SELL'}</td>"
            f"<td>{_fmt(v['weight'], 2)}</td><td class='raw'>{_esc(v['raw'])}</td></tr>"
            for v in c["active_votes"]
        ) or "<tr><td colspan='4' class='q'>all active judges quiet this cycle</td></tr>"

        cards.append(f"""
<div class="card" data-tags="{' '.join(tags)}">
  <div class="head" onclick="this.parentNode.classList.toggle('open')">
    <span class="t">{_esc(c['buda_hhmmss'])}</span>
    <span class="pill {pill_cls}">{_esc(c['gate_stage'])}</span>
    <span class="dir {d_cls}">{_esc(d)}</span>
    <span class="num">px {_fmt(px, 2)}</span>
    <span class="num">conf {_fmt(conf, 1)}%</span>
    <span class="num">POWER {_esc(c['power_dir'])} ({_esc(c['power_regime'])})</span>
    <span class="num">+{h4}m {_fmt((pb4 - px) if (pb4 and px) else None, 2, True)}</span>
    <span class="num">SL/TP {_esc(c['fix_out'])} {_fmt(c['fix_pts'], 2, True) if is_sig else '-'}</span>
  </div>
  <div class="body">
    <div class="cols">
      <div><h4>What Current Version Saw (Raw Tape + Book)</h4>
        <table>
          <tr><td>Regime / ATR</td><td>{_esc(c['regime'])} | ATR {_fmt(c['atr'], 2)}</td></tr>
          <tr><td>L2 / Depth / Spread</td><td>{_esc(c['l2_status'])} | {_esc(c['depth_levels'])} lvls | spr {_fmt(c['spread_pts'], 2)}</td></tr>
          <tr><td>MBO Window</td><td>{c['mbo_window']} L3 order events</td></tr>
          <tr><td>POWER v2</td><td>{_esc(c['power_dir'])} (act {_fmt(c['power_activity'], 3)}, bars {c['power_bars']}/28)</td></tr>
          <tr><td>Family Gate</td><td>{'PASS' if c['family_ok'] else 'SINGLE/NONE'} ({_esc(','.join(c['agree_families']) or 'none')})</td></tr>
          <tr><td>Shooting Plan</td><td>{_esc(c['shot_action'])} ({_esc(c['shot_style'])}) — {_esc(c['shot_reason'])}</td></tr>
        </table>
      </div>
      <div><h4>Current Active Panel ({len(c['active_votes'])} active votes, 0 retired)</h4>
        <table><tr><th>judge</th><th>vote</th><th>w</th><th>raw reason</th></tr>{jrows}</table>
      </div>
      <div><h4>What Happened Next ({_esc(tf_lbl)} 1-4 Bars Real Tape)</h4>
        <table>
          <tr><td>+{h1} min (1b)</td><td>{_fmt(pb1, 2)}</td><td>{_fmt((pb1 - px) if (pb1 and px) else None, 2, True)}</td></tr>
          <tr><td>+{h2} min (2b)</td><td>{_fmt(pb2, 2)}</td><td>{_fmt((pb2 - px) if (pb2 and px) else None, 2, True)}</td></tr>
          <tr><td>+{h3} min (3b)</td><td>{_fmt(pb3, 2)}</td><td>{_fmt((pb3 - px) if (pb3 and px) else None, 2, True)}</td></tr>
          <tr><td>+{h4} min (4b)</td><td>{_fmt(pb4, 2)}</td><td>{_fmt((pb4 - px) if (pb4 and px) else None, 2, True)}</td></tr>
          <tr><td>{h4}m in favour / against</td><td colspan="2">{_fmt(c.get('fav4b'), 2)} / {_fmt(c.get('adv4b'), 2)}</td></tr>
          <tr><td>Market Trick Check</td><td colspan="2">{_esc(t_lbl)}</td></tr>
          <tr><td>Fixed SL/TP (180m)</td><td>{_esc(c['fix_out'])}</td><td>{_fmt(c['fix_pts'], 2, True) if is_sig else '-'}</td></tr>
          <tr><td>C3 Wall + 0.5xATR Trail</td><td>{_esc(c['c3_out'])}</td><td>{_fmt(c['c3_pts'], 2, True) if is_sig else '-'}</td></tr>
          <tr><td>Wall + 1.0xATR Trail</td><td>{_esc(c['tr10_out'])}</td><td>{_fmt(c['tr10_pts'], 2, True) if is_sig else '-'}</td></tr>
        </table>
      </div>
    </div>
  </div>
</div>""")

    filter_bar = """
<div style="padding:12px 22px;display:flex;gap:8px;flex-wrap:wrap;border-bottom:1px solid var(--line)">
  <button onclick="flt('all')" style="cursor:pointer;padding:4px 10px;background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:6px">All Cycles</button>
  <button onclick="flt('sig')" style="cursor:pointer;padding:4px 10px;background:#21262d;color:#e6edf3;border:1px solid #30363d;border-radius:6px">Signals Only</button>
  <button onclick="flt('c35')" style="cursor:pointer;padding:4px 10px;background:#21262d;color:#58a6ff;border:1px solid #30363d;border-radius:6px">Conf &gt;= Threshold</button>
  <button onclick="flt('c65')" style="cursor:pointer;padding:4px 10px;background:#21262d;color:#3fb950;border:1px solid #30363d;border-radius:6px">Conf &gt;= 65%</button>
  <button onclick="flt('pw')" style="cursor:pointer;padding:4px 10px;background:#21262d;color:#d2a8ff;border:1px solid #30363d;border-radius:6px">POWER Active</button>
  <button onclick="flt('go')" style="cursor:pointer;padding:4px 10px;background:#21262d;color:#3fb950;border:1px solid #3fb950;border-radius:6px">Shooting GO</button>
  <button onclick="flt('shake')" style="cursor:pointer;padding:4px 10px;background:#21262d;color:#f0883e;border:1px solid #30363d;border-radius:6px">Shakeout-&gt;Win</button>
  <button onclick="flt('trap')" style="cursor:pointer;padding:4px 10px;background:#21262d;color:#f85149;border:1px solid #30363d;border-radius:6px">Bait Traps</button>
</div>
<script>
function flt(tag){
  document.querySelectorAll('.card').forEach(function(el){
    var t=(' '+el.getAttribute('data-tags')+' ');
    el.style.display=(t.indexOf(' '+tag+' ')>=0)?'block':'none';
  });
}
</script>
"""
    head = (
        f"{day_str} ({res['day_type']}, {tf_lbl} +{h1}/+{h2}/+{h3}/+{h4}m, net {res['day_net']:+.1f} / range {res['day_range']:.1f} pts) — "
        f"{len(cycles)} cycles replayed on CURRENT app version from {res['prints']:,} raw prints & "
        f"{res['mbo_streamed']:,} raw MBO events (Law #8)"
    )
    html_doc = ins._page(
        f"Every Decision (Current Version) — {day_str}",
        head,
        filter_bar + ("".join(cards) or "<p style='padding:14px 22px'>No active cycles.</p>"),
    )
    return html_doc.replace(
        ".pill.ok{color:var(--ok);border-color:var(--ok)}",
        ".pill.ok{color:var(--ok);border-color:var(--ok)}.pill.warn{color:#58a6ff;border-color:#58a6ff}",
    )


# --------------------------------------------------------------------------- #
# Multi-Day Synthesis: Strengths, Weaknesses, Text + HTML Diagnostic
# --------------------------------------------------------------------------- #
def synthesize_multi_day(results: List[Dict[str, Any]]) -> Tuple[str, str, Dict[str, Any]]:
    ok_days = [r for r in results if r.get("ok")]
    if not ok_days:
        msg = "No raw-data days could be replayed."
        return msg, ins._page("Current-Version Raw Replay", "No data", f"<p>{_esc(msg)}</p>"), {"days": 0}

    conf_thr = ok_days[-1]["conf_threshold"]
    tf_lbl = str(ok_days[-1].get("timeframe") or "M3")
    h_list = list(ok_days[-1].get("tf_horizons") or [3, 6, 9, 12])
    h1, h2, h3, h4 = h_list[0], h_list[1], h_list[2], h_list[3]

    def _new_bucket() -> Dict[str, Any]:
        return {"n": 0, "tp": 0, "sl": 0, "to": 0, "fix_pts": 0.0, "c3_pts": 0.0, "c3_n": 0,
                "tr10_pts": 0.0, "b4_hit": 0, "b4_n": 0, "b4_pts": 0.0}

    def _new_trick_bucket() -> Dict[str, Any]:
        return {
            "n": 0,
            "m": [[], [], [], []],
            "CLEAN_FOLLOW": 0,
            "FAKEOUT_WIN": 0,
            "TRAP_REVERSAL": 0,
            "CLEAN_AGAINST": 0,
            "FLAT_CHOP": 0,
        }

    by_gate = defaultdict(_new_bucket)
    by_conf = defaultdict(_new_bucket)
    by_regime = defaultdict(_new_bucket)
    by_dir_reg = defaultdict(_new_bucket)
    by_hour = defaultdict(_new_bucket)
    by_fams = defaultdict(_new_bucket)
    by_trick_lens = defaultdict(_new_trick_bucket)
    judge_tot = defaultdict(
        lambda: {
            "days": 0, "pos_days": 0, "gross_pos_days": 0, "calls": 0, "scored": 0,
            "hits": 0, "gross_pts": 0.0, "pts": 0.0, "b1_sum": 0.0, "b1_n": 0,
            "b4_sum": 0.0, "b4_n": 0, "w": 0.0, "clk": 30,
        }
    )
    bench_tot = defaultdict(
        lambda: {
            "cat": "RETIRED", "clk": 30, "days": 0, "pos_days": 0, "gross_pos_days": 0,
            "calls": 0, "scored": 0, "hits": 0, "gross_pts": 0.0, "pts": 0.0,
            "b1_sum": 0.0, "b1_n": 0, "b4_sum": 0.0, "b4_n": 0,
        }
    )

    a2_bm8 = {"n": 0, "go_n": 0, "fix18_pts": 0.0, "atr10_pts": 0.0}
    total_cycles = 0
    total_signals = 0

    def _acc_bucket(b: Dict[str, Any], c: Dict[str, Any]) -> None:
        if c["fix_out"] not in ("TP", "SL", "TIMEOUT", "TRAIL"):
            return
        b["n"] += 1
        b["fix_pts"] += c["fix_pts"]
        if c["fix_out"] == "TP":
            b["tp"] += 1
        elif c["fix_out"] == "SL":
            b["sl"] += 1
        else:
            b["to"] += 1
        if c["c3_out"] not in ("NO_SIGNAL", "VG_SKIP", "last_bar"):
            b["c3_n"] += 1
            b["c3_pts"] += c["c3_pts"]
            b["tr10_pts"] += c["tr10_pts"]
        pb4 = c.get("pb4")
        if pb4 is not None and c["price"] > 0:
            mv4 = (pb4 - c["price"]) if c["direction"] == "BUY" else (c["price"] - pb4)
            b["b4_n"] += 1
            b["b4_pts"] += mv4
            if mv4 > 0:
                b["b4_hit"] += 1

    def _acc_trick(tb: Dict[str, Any], c: Dict[str, Any]) -> None:
        tc_code = str(c.get("trick_code") or "")
        if tc_code not in ("CLEAN_FOLLOW", "FAKEOUT_WIN", "TRAP_REVERSAL", "CLEAN_AGAINST", "FLAT_CHOP"):
            return
        tb["n"] += 1
        tb[tc_code] += 1
        s4 = c.get("signed_4b") or [None, None, None, None]
        for idx_b in range(min(4, len(s4))):
            if s4[idx_b] is not None:
                tb["m"][idx_b].append(float(s4[idx_b]))

    for r in ok_days:
        for j, jst in r["judge_stats"].items():
            jt = judge_tot[j]
            jt["days"] += 1
            if jst["pts"] > 0:
                jt["pos_days"] += 1
            if float(jst.get("gross_pts", 0.0)) > 0:
                jt["gross_pos_days"] += 1
            jt["calls"] += jst["calls"]
            jt["scored"] += jst["scored"]
            jt["hits"] += jst["hits"]
            jt["gross_pts"] += float(jst.get("gross_pts", 0.0))
            jt["pts"] += jst["pts"]
            jt["b1_sum"] += float(jst.get("b1_sum", 0.0))
            jt["b1_n"] += int(jst.get("b1_n", 0))
            jt["b4_sum"] += float(jst.get("b4_sum", 0.0))
            jt["b4_n"] += int(jst.get("b4_n", 0))
            jt["w"] = jst["w"]
            jt["clk"] = int(jst.get("clk") or ins.judge_clock(j))

        for bj, bst in (r.get("bench_judge_stats") or {}).items():
            bt = bench_tot[bj]
            bt["cat"] = str(bst.get("cat") or "RETIRED")
            bt["clk"] = int(bst.get("clk") or 30)
            bt["days"] += 1
            if bst["pts"] > 0:
                bt["pos_days"] += 1
            if float(bst.get("gross_pts", 0.0)) > 0:
                bt["gross_pos_days"] += 1
            bt["calls"] += bst["calls"]
            bt["scored"] += bst["scored"]
            bt["hits"] += bst["hits"]
            bt["gross_pts"] += float(bst.get("gross_pts", 0.0))
            bt["pts"] += bst["pts"]
            bt["b1_sum"] += float(bst.get("b1_sum", 0.0))
            bt["b1_n"] += int(bst.get("b1_n", 0))
            bt["b4_sum"] += float(bst.get("b4_sum", 0.0))
            bt["b4_n"] += int(bst.get("b4_n", 0))

        for c in r["cycles"]:
            if c.get("error"):
                continue
            total_cycles += 1
            d = c["direction"]
            if d not in ("BUY", "SELL"):
                continue
            total_signals += 1
            conf = c["confidence"]
            cb = "<25%" if conf < 25 else ("25-34%" if conf < 35 else ("35-49%" if conf < 50 else ("50-64%" if conf < 65 else ">=65%")))
            fam_k = ">=2 Agreeing Families" if c["family_ok"] else "<2 Families (Single-Family)"

            _acc_bucket(by_gate[c["gate_stage"]], c)
            _acc_bucket(by_conf[cb], c)
            _acc_bucket(by_regime[f"{c['regime']} (Day:{r['day_type']})"], c)
            _acc_bucket(by_dir_reg[f"{d} | {c['regime']}"], c)
            _acc_bucket(by_hour[c["buda_hour"]], c)
            _acc_bucket(by_fams[fam_k], c)

            _acc_trick(by_trick_lens["ALL DIRECTIONAL SIGNALS"], c)
            if c["shot_action"] == "GO":
                _acc_trick(by_trick_lens["TAKEN / SHOOTING GO"], c)
            else:
                if conf < conf_thr:
                    _acc_trick(by_trick_lens[f"SKIPPED: Conf < {conf_thr:.0f}%"], c)
                else:
                    _acc_trick(by_trick_lens["SKIPPED: Conf>=Thr but Shooting=WAIT"], c)
            if conf >= 65.0:
                _acc_trick(by_trick_lens["HIGH CONVICTION (Conf >= 65%)"], c)
            _acc_trick(by_trick_lens[f"REGIME: {c['regime']}"], c)

            if c["a2_fix_pts"] is not None and c["a2_atr_pts"] is not None:
                a2_bm8["n"] += 1
                if c["shot_action"] == "GO":
                    a2_bm8["go_n"] += 1
                a2_bm8["fix18_pts"] += c["a2_fix_pts"]
                a2_bm8["atr10_pts"] += c["a2_atr_pts"]

    # Build shareable plain-text report
    lines: List[str] = []
    lines.append("=" * 108)
    lines.append(" LAW #8 CURRENT-VERSION RAW-DATA REPLAY & DIAGNOSTIC REPORT")
    lines.append(f" Generated: {datetime.now(BUDA_TZ):%Y-%m-%d %H:%M:%S %Z} | Days: {len(ok_days)} ({ok_days[0]['day']} .. {ok_days[-1]['day']})")
    lines.append(
        f" Total Replayed Cycles: {total_cycles:,} | Signals: {total_signals:,} | "
        f"Conf Threshold: {conf_thr:.0f}% | Active Chart Setup: {tf_lbl} (+{h1}/+{h2}/+{h3}/+{h4}m)"
    )
    lines.append(" Zero old decisions/reports trusted -- 100% computed fresh from raw ticks + raw mbo on CURRENT code.")
    lines.append("=" * 108)

    lines.append("\n[1] DAY-BY-DAY RAW REPLAY RECEIPT")
    lines.append(f"  {'DATE':<12} {'TYPE':<11} {'PRINTS':>8} {'MBO_EV':>9} {'RANGE':>6} {'NET':>7} {'CYC':>5} {'SIG>=' + str(int(conf_thr)):>8} {'FIX_PTS(>=' + str(int(conf_thr)) + ')':>14} {'C3_TRAIL':>10} {'SEC':>5}")
    lines.append("  " + "-" * 96)
    for r in ok_days:
        c35_rows = [c for c in r["cycles"] if not c.get("error") and c["direction"] in ("BUY", "SELL") and c["confidence"] >= conf_thr and c["fix_out"] in ("TP", "SL", "TIMEOUT", "TRAIL")]
        f_pts = sum(c["fix_pts"] for c in c35_rows)
        c3_pts = sum(c["c3_pts"] for c in c35_rows if c["c3_out"] not in ("NO_SIGNAL", "VG_SKIP", "last_bar"))
        lines.append(
            f"  {r['day']:<12} {r['day_type']:<11} {r['prints']:>8,} {r['mbo_streamed']:>9,} "
            f"{r['day_range']:>6.1f} {r['day_net']:>+7.1f} {len(r['cycles']):>5} "
            f"{len(c35_rows):>8} {f_pts:>+14.1f} {c3_pts:>+10.1f} {r['elapsed_s']:>4.1f}s"
        )

    b4_col_hdr = f"+{h4}m%"

    def _fmt_bucket_table(title: str, mapping: Dict[Any, Dict[str, Any]], key_order: Optional[Sequence[Any]] = None) -> None:
        lines.append(f"\n{title}")
        lines.append(f"  {'BUCKET':<42} {'N':>5} {'TP/SL':>9} {'WR%':>6} {b4_col_hdr:>6} {'FIX_PTS':>9} {'PTS/SIG':>8} {'C3_0.5A':>9} {'TR_1.0A':>9}")
        lines.append("  " + "-" * 108)
        keys = key_order if key_order is not None else [k for k, _ in sorted(mapping.items(), key=lambda kv: -kv[1]["n"])]
        for k in keys:
            if k not in mapping or mapping[k]["n"] == 0:
                continue
            b = mapping[k]
            denom = b["tp"] + b["sl"]
            wr = (100.0 * b["tp"] / denom) if denom else 0.0
            b4p = (100.0 * b["b4_hit"] / b["b4_n"]) if b["b4_n"] else 0.0
            tp_sl_str = f"{b['tp']}/{b['sl']}"
            lines.append(
                f"  {str(k):<42} {b['n']:>5} {tp_sl_str:>9} {wr:>5.1f}% {b4p:>5.1f}% "
                f"{b['fix_pts']:>+9.1f} {b['fix_pts']/b['n']:>+8.2f} {b['c3_pts']:>+9.1f} {b['tr10_pts']:>+9.1f}"
            )

    _fmt_bucket_table("[2] GATE WATERFALL (CURRENT VERSION)", by_gate)
    _fmt_bucket_table("[3] CONFIDENCE BUCKETS (CURRENT ACTIVE PANEL)", by_conf, ["<25%", "25-34%", "35-49%", "50-64%", ">=65%"])
    _fmt_bucket_table("[4] FAMILY INDEPENDENCE CHECK", by_fams)
    _fmt_bucket_table("[5] DIRECTION x REGIME MATRIX", by_dir_reg)
    _fmt_bucket_table("[6] BUDAPEST HOUR OF DAY (LOCAL CET/CEST)", {f"{h:02d}:00": by_hour[h] for h in sorted(by_hour)}, [f"{h:02d}:00" for h in sorted(by_hour)])

    jb1_hdr = f"+{h1}m(1b)"
    jb4_hdr = f"+{h4}m(4b)"
    lines.append("\n[7] CURRENT ACTIVE JUDGES SCORECARD (LAW #10: GROSS DIRECTIONAL ALPHA vs NET OF SPREAD)")
    lines.append(
        f"  {'JUDGE':<20} {'W':>5} {'CLK':>4} {'POS_DAYS':>8} {'SCORED':>7} {'RIGHT%':>7} "
        f"{'GROSS_PTS':>10} {'GROSS/C':>8} {'NET_PTS':>10} {'PTS/CALL':>9} {jb1_hdr:>8} {jb4_hdr:>8}"
    )
    lines.append("  " + "-" * 116)
    for j, jt in sorted(judge_tot.items(), key=lambda kv: -kv[1]["pts"]):
        if jt["scored"] == 0:
            continue
        acc = 100.0 * jt["hits"] / jt["scored"]
        pdays_str = f"{jt['pos_days']}/{jt['days']}"
        clk_s = f"{jt['clk']}m"
        b1_s = f"{(jt['b1_sum']/jt['b1_n']):+.2f}" if jt["b1_n"] else "   -   "
        b4_s = f"{(jt['b4_sum']/jt['b4_n']):+.2f}" if jt["b4_n"] else "   -   "
        lines.append(
            f"  {j:<20} {jt['w']:>5.2f} {clk_s:>4} {pdays_str:>8} "
            f"{jt['scored']:>7} {acc:>6.1f}% {jt['gross_pts']:>+10.1f} {jt['gross_pts']/jt['scored']:>+8.3f} "
            f"{jt['pts']:>+10.1f} {jt['pts']/jt['scored']:>+9.3f} {b1_s:>8} {b4_s:>8}"
        )

    lines.append(
        "\n[7b] RETIRED, PREVIOUS & REPAIRED BENCH JUDGES -- FAIR-TRIAL CHECKUP (+ POWER & STAGE-2 [FIX], 100% NON-VOTING)"
    )
    lines.append(
        f"  {'BENCH_JUDGE':<22} {'CAT':<8} {'CLK':>4} {'POS_DAYS':>8} {'SCORED':>7} {'RIGHT%':>7} "
        f"{'GROSS_PTS':>10} {'GROSS/C':>8} {'NET_PTS':>10} {'PTS/CALL':>9} {jb1_hdr:>8} {jb4_hdr:>8}"
    )
    lines.append("  " + "-" * 121)
    cat_rank = {
        "ACT_FIX": 0,
        "REPAIRED": 1,
        "POWER": 2,
        "RETIRED": 3,
        "PREVIOUS": 4,
        "DORMANT": 5,
    }
    for bj, bt in sorted(
        bench_tot.items(),
        key=lambda kv: (cat_rank.get(kv[1]["cat"], 9), -kv[1]["gross_pts"]),
    ):
        if bt["scored"] == 0:
            continue
        acc = 100.0 * bt["hits"] / bt["scored"]
        pdays_str = f"{bt['pos_days']}/{bt['days']}"
        clk_s = f"{bt['clk']}m"
        b1_s = f"{(bt['b1_sum']/bt['b1_n']):+.2f}" if bt["b1_n"] else "   -   "
        b4_s = f"{(bt['b4_sum']/bt['b4_n']):+.2f}" if bt["b4_n"] else "   -   "
        lines.append(
            f"  {bj:<22} {bt['cat']:<8} {clk_s:>4} {pdays_str:>8} "
            f"{bt['scored']:>7} {acc:>6.1f}% {bt['gross_pts']:>+10.1f} {bt['gross_pts']/bt['scored']:>+8.3f} "
            f"{bt['pts']:>+10.1f} {bt['pts']/bt['scored']:>+9.3f} {b1_s:>8} {b4_s:>8}"
        )
    if not any(bt["scored"] > 0 for k, bt in bench_tot.items() if k.startswith("macro_")):
        lines.append(
            "  * Note: macro_risk / news_sentiment (active) & macro_dxy / macro_yield / macro_vix (retired) require external"
            " Yahoo/Calendar feeds absent from offline BookMap ticks/mbo CSVs."
        )

    if a2_bm8["n"] > 0:
        lines.append(
            f"\n[8] BOOKMARK #8 SPRINT STOP CHECK (N={a2_bm8['n']} cycles, Shooting GO={a2_bm8['go_n']}): "
            f"Fixed 1.80/1.80 = {a2_bm8['fix18_pts']:+.1f} pts vs max(1.80, 1.0xATR) = {a2_bm8['atr10_pts']:+.1f} pts"
        )
    else:
        lines.append(
            "\n[8] BOOKMARK #8 SPRINT STOP CHECK: 0 Shooting GO / A2 cycles fired on this sample."
        )

    # [9] Stage 1: Timeframe-linked 1..4-bar path & Market Trick Detector
    lines.append(
        f"\n[9] {tf_lbl} BAR-BY-BAR PATH & MARKET TRICK DETECTOR "
        f"(+{h1}m / +{h2}m / +{h3}m / +{h4}m = 1x..4x {tf_lbl})"
    )
    h1_c, h2_c, h3_c, h4_c = f"+{h1}m(1b)", f"+{h2}m(2b)", f"+{h3}m(3b)", f"+{h4}m(4b)"
    lines.append(
        f"  {'LENS':<36} {'N':>5} {h1_c:>9} {h2_c:>9} {h3_c:>9} {h4_c:>9} "
        f"{'FOLLOW':>7} {'SHAKE->W':>8} {'TRAP->L':>8} {'AGAINST':>8}"
    )
    lines.append("  " + "-" * 116)
    trick_order = [
        "ALL DIRECTIONAL SIGNALS",
        "TAKEN / SHOOTING GO",
        "HIGH CONVICTION (Conf >= 65%)",
        "SKIPPED: Conf>=Thr but Shooting=WAIT",
        f"SKIPPED: Conf < {conf_thr:.0f}%",
        "REGIME: TREND",
        "REGIME: RANGE",
        "REGIME: NEUTRAL",
    ]
    for tk in trick_order:
        tb = by_trick_lens.get(tk)
        if not tb or tb["n"] == 0:
            continue
        avgs = []
        for idx_b in range(4):
            arr = tb["m"][idx_b]
            avgs.append(f"{(sum(arr)/len(arr)):+.2f}" if arr else "   -   ")
        lines.append(
            f"  {tk:<36} {tb['n']:>5} {avgs[0]:>9} {avgs[1]:>9} {avgs[2]:>9} {avgs[3]:>9} "
            f"{tb['CLEAN_FOLLOW']:>7} {tb['FAKEOUT_WIN']:>8} {tb['TRAP_REVERSAL']:>8} {tb['CLEAN_AGAINST']:>8}"
        )

    # Automatic Strengths & Weaknesses Finder
    strengths: List[str] = []
    weaknesses: List[str] = []
    for label, mp in (("Conf", by_conf), ("Gate", by_gate), ("Regime", by_dir_reg)):
        for k, b in mp.items():
            if b["n"] >= 5:
                avg = b["fix_pts"] / b["n"]
                if avg >= 0.50:
                    strengths.append(f"{label} [{k}]: n={b['n']}, {b['fix_pts']:+.1f} pts ({avg:+.2f}/sig)")
                elif avg <= -1.00:
                    weaknesses.append(f"{label} [{k}]: n={b['n']}, {b['fix_pts']:+.1f} pts ({avg:+.2f}/sig)")
    for j, jt in judge_tot.items():
        if jt["scored"] >= 10:
            ppc = jt["pts"] / jt["scored"]
            if ppc >= 0.25:
                strengths.append(f"Judge [{j}]: {jt['scored']} calls, {100.0*jt['hits']/jt['scored']:.1f}% right, {jt['pts']:+.1f} pts ({ppc:+.2f}/call)")
            elif ppc <= -0.50:
                weaknesses.append(f"Judge [{j}]: {jt['scored']} calls, {100.0*jt['hits']/jt['scored']:.1f}% right, {jt['pts']:+.1f} pts ({ppc:+.2f}/call)")

    lines.append("\n[10] AUTO-DETECTED CURRENT-VERSION STRENGTHS & WEAKNESSES")
    lines.append("  STRONGEST PARTS:")
    for s in (strengths[:8] or ["(run across more raw tape days for >=5-signal buckets)"]):
        lines.append(f"    + {s}")
    lines.append("  WEAKEST PARTS / WATCH ITEMS:")
    for w in (weaknesses[:8] or ["(none below -1.00/sig threshold on this sample)"]):
        lines.append(f"    - {w}")
    lines.append("")

    txt_report = "\n".join(lines)
    day_links = "".join(
        f"<li><a href='current_version_decisions_{_esc(r['day'])}.html'>{_esc(r['day'])}</a> "
        f"— {_esc(r['day_type'])}, {r['prints']:,} prints, {r['mbo_streamed']:,} MBO events, {len(r['cycles'])} cycles</li>"
        for r in ok_days
    )
    html_body = (
        f"<div style='padding:16px 22px'>"
        f"<h3>Per-Day 'Every Decision' Pages (Current Version on Raw Data)</h3>"
        f"<ul>{day_links}</ul>"
        f"<h3>Multi-Day Strengths &amp; Weaknesses Console Diagnostic</h3>"
        f"<pre style='background:#161b22;border:1px solid #21262d;border-radius:8px;padding:14px;overflow-x:auto'>{_esc(txt_report)}</pre>"
        f"</div>"
    )
    html_report = ins._page(
        "Law #8 Current-Version Raw-Data Diagnostic",
        f"{len(ok_days)} day(s) replayed from raw ticks + raw mbo ({total_cycles:,} cycles)",
        html_body,
    )
    summary_meta = {
        "days": len(ok_days),
        "total_cycles": total_cycles,
        "total_signals": total_signals,
        "day_list": [r["day"] for r in ok_days],
        "timeframe": tf_lbl,
        "tf_horizons": [h1, h2, h3, h4],
    }
    return txt_report, html_report, summary_meta


# --------------------------------------------------------------------------- #
# CLI Entrypoint
# --------------------------------------------------------------------------- #
def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Law #8: Replay past raw ticks+mbo days through the CURRENT app version."
    )
    ap.add_argument("--date", default=None, help="Single YYYY-MM-DD date to replay")
    ap.add_argument("--dates", default=None, help="Comma-separated YYYY-MM-DD dates")
    ap.add_argument("--days", type=int, default=11, help="Auto-discover last N raw-data days (default: 11)")
    ap.add_argument("--every", type=int, default=60, help="Seconds between evaluation cycles (default: 60)")
    ap.add_argument("--window", default="08:00-23:00", help="Budapest local window (default: 08:00-23:00)")
    ap.add_argument("--workers", type=int, default=0, help="Parallel worker processes across days (0=auto up to 3, 1=sequential)")
    ap.add_argument("--root-dir", default=str(ROOT), help="Project root directory")
    ap.add_argument("--data-dir", default=None, help="Data/output directory (default: <root>/data)")
    ap.add_argument("--no-browser", action="store_true", help="Do not auto-open HTML report in browser")
    args = ap.parse_args(argv)

    root_dir = Path(args.root_dir).resolve()
    data_dir = Path(args.data_dir).resolve() if args.data_dir else (root_dir / "data")
    data_dir.mkdir(parents=True, exist_ok=True)

    if args.date:
        target_days = [args.date.strip()]
    elif args.dates:
        target_days = [d.strip() for d in args.dates.split(",") if d.strip()]
    else:
        target_days = discover_available_days(root_dir, data_dir, max_days=args.days)

    if not target_days:
        print(f"[fail] No raw ticks files found in {root_dir} or {data_dir}.")
        return 1

    every_s = max(10, int(args.every))
    req_workers = int(args.workers)
    if req_workers <= 0:
        # Auto-parallelize multi-day runs on real repo runs; keep custom test fixtures sequential unless requested
        is_default_repo = (root_dir == ROOT.resolve())
        n_workers = min(3, len(target_days), os.cpu_count() or 1) if (is_default_repo and len(target_days) >= 2) else 1
    else:
        n_workers = max(1, min(req_workers, len(target_days)))

    print(
        f"[Law #8 Raw Replay] Replaying {len(target_days)} day(s) on CURRENT app version "
        f"(workers={n_workers}): {', '.join(target_days)}"
    )
    results: List[Dict[str, Any]] = []
    if n_workers > 1 and len(target_days) > 1:
        tasks = [(d, str(root_dir), str(data_dir), every_s, args.window) for d in target_days]
        try:
            with concurrent.futures.ProcessPoolExecutor(max_workers=n_workers) as pool:
                results = list(pool.map(_replay_worker, tasks))
        except Exception:
            results = [
                replay_single_day(d, root_dir=root_dir, data_dir=data_dir, every_s=every_s, window_str=args.window)
                for d in target_days
            ]
    else:
        for d in target_days:
            results.append(
                replay_single_day(d, root_dir=root_dir, data_dir=data_dir, every_s=every_s, window_str=args.window)
            )

    for res in results:
        d = res["day"]
        if res.get("ok"):
            day_html = build_day_decisions_html(res)
            out_day_html = data_dir / f"current_version_decisions_{d}.html"
            out_day_html.write_text(day_html, encoding="utf-8", newline="\n")
            print(
                f"  [ok] {d}: {res['prints']:,} prints, {res['mbo_streamed']:,} MBO events, "
                f"{len(res['cycles'])} cycles in {res['elapsed_s']:.1f}s -> {out_day_html.name}"
            )
        else:
            print(f"  [skip] {d}: {res.get('reason')}")

    txt_rep, html_rep, meta = synthesize_multi_day(results)
    if meta.get("days", 0) == 0:
        print("[fail] 0 days produced usable raw trade prints.")
        return 1

    out_txt = data_dir / "current_version_replay.txt"
    out_html = data_dir / "current_version_replay.html"
    out_txt.write_text(txt_rep + "\n", encoding="utf-8", newline="\n")
    out_html.write_text(html_rep, encoding="utf-8", newline="\n")

    print("\n" + txt_rep)
    print(f"[saved] {out_txt}   <- share this single .txt file in chat!")
    print(f"[saved] {out_html}  <- interactive overview + links to each day's 'Every decision' page")

    if not args.no_browser and os.environ.get("GBM_NO_BROWSER") != "1":
        try:
            webbrowser.open(out_html.resolve().as_uri())
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
