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
import csv
import glob
import gzip
import html
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
    prev_compact = None
    next_compact = None
    if day_str:
        d_obj = date.fromisoformat(day_str)
        prev_compact = f"{d_obj - timedelta(days=1):%Y%m%d}"
        next_compact = f"{d_obj + timedelta(days=1):%Y%m%d}"

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
                if compact is not None:
                    m = re.match(rf"^{kind}_(\d{{8}})_", f.name, re.I)
                    if m and m.group(1) not in {compact, prev_compact, next_compact}:
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

    def to_candles_dict(self) -> Dict[str, np.ndarray]:
        if not self.bars:
            return {}
        keys = sorted(self.bars.keys())[-self.max_bars:]
        return {
            "open": np.array([self.bars[k][0] for k in keys], dtype=float),
            "high": np.array([self.bars[k][1] for k in keys], dtype=float),
            "low": np.array([self.bars[k][2] for k in keys], dtype=float),
            "close": np.array([self.bars[k][3] for k in keys], dtype=float),
            "volume": np.array([self.bars[k][4] for k in keys], dtype=float),
        }


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


def _iter_filtered_csv_events(
    files: Sequence[Path],
    start_ep: float,
    end_ep: float,
    allowed_prefixes: set[str],
    inst_filter: Optional[str] = None,
) -> Iterator[Tuple[float, str]]:
    """Yield `(epoch_utc, raw_line)` within `[start_ep, end_ep]` from ordered chunks."""
    seen_recent: set[Tuple[str, str]] = set()
    recent_ring: collections.deque = collections.deque()
    inst_suffix = f",{inst_filter}" if inst_filter else None
    for path in files:
        op = gzip.open if path.name.endswith(".gz") else open
        try:
            with op(path, "rt", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if not line or line[0] == "t" or len(line) < 20:
                        continue
                    if line[:10] not in allowed_prefixes:
                        continue
                    if inst_suffix and inst_filter not in line:
                        continue
                    comma = line.find(",")
                    if comma <= 0:
                        continue
                    ts_raw = line[:comma]
                    ep = _fast_ts_epoch(ts_raw)
                    if ep is None or ep < start_ep or ep > end_ep:
                        continue
                    sig = (ts_raw, line)
                    if sig in seen_recent:
                        continue
                    seen_recent.add(sig)
                    recent_ring.append(sig)
                    if len(recent_ring) > 30_000:
                        old_sig = recent_ring.popleft()
                        seen_recent.discard(old_sig)
                    yield ep, line if line.endswith("\n") else (line + "\n")
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
    # Keep +3h post-window trades for honest 180-minute forward outcome walks
    walk_end_ep = (win_end_utc + timedelta(minutes=185)).timestamp()
    allowed_dates = {
        f"{d_obj - timedelta(days=1):%Y-%m-%d}",
        day_str,
        f"{d_obj + timedelta(days=1):%Y-%m-%d}",
    }

    tick_files = _discover_raw_files("ticks", root_dir, data_dir, day_str=day_str)
    mbo_files = _discover_raw_files("mbo", root_dir, data_dir, day_str=day_str)

    if not tick_files:
        return {"day": day_str, "ok": False, "reason": "no raw ticks files found"}

    # Pass 1: collect deduplicated Last trade prints for fast forward outcome grading
    # AND detect the active instrument symbol on that date.
    trades: List[Tuple[float, float]] = []
    seen_prints: set[Tuple[float, float, float]] = set()
    inst_counts: Counter = Counter()
    raw_tick_rows = 0

    for ep, line in _iter_filtered_csv_events(tick_files, stream_start_ep, walk_end_ep, allowed_dates):
        raw_tick_rows += 1
        parts = line.rstrip("\r\n").split(",") if '"' not in line else next(csv.reader([line.rstrip("\r\n")]))
        if len(parts) < 7:
            continue
        ev = parts[1].strip()
        inst = parts[6].strip()
        if inst:
            inst_counts[inst] += 1
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
                    trades.append((ep, px))

    if not trades:
        return {"day": day_str, "ok": False, "reason": f"0 trade prints on {day_str}"}

    trades.sort(key=lambda x: x[0])
    trade_eps = [t[0] for t in trades]
    day_prints = sum(1 for ep, _ in trades if win_start_utc.timestamp() <= ep <= win_end_utc.timestamp())
    if day_prints == 0:
        return {"day": day_str, "ok": False, "reason": f"0 in-window prints on {day_str}"}

    bar_keys, walk_bars = _build_m1_walk_bars(trades)
    dominant_inst = inst_counts.most_common(1)[0][0] if inst_counts else getattr(config, "DATA_SYMBOL", "GC 12-26")

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

    # Pass 2: stream raw ticks + raw MBO chronologically into isolated Step 1 + Step 2!
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
        lambda: {"calls": 0, "scored": 0, "hits": 0, "pts": 0.0, "buy": 0, "sell": 0, "w": 0.0}
    )
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
            m1_builder = _IncrementalM1Builder(max_bars=360)

            inst_flt = dominant_inst if inst_counts else None
            tick_iter = iter(_iter_filtered_csv_events(
                tick_files, stream_start_ep, win_end_utc.timestamp(), allowed_dates, inst_filter=inst_flt
            ))
            mbo_iter = iter(_iter_filtered_csv_events(
                mbo_files, stream_start_ep, win_end_utc.timestamp(), allowed_dates, inst_filter=inst_flt
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
                                m1_builder.add_trade(t_ep, float(ps[2]), float(ps[3] or 1.0))
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

                # Keep 3h rolling tick window (exact BOOKMAP_WINDOW_SECONDS=10800)
                if step_idx % 15 == 1:
                    tail._prune_old_ticks(10800.0)
                provider._prebuilt_candles = m1_builder.to_candles_dict()

                md = provider.acquire(symbol=dominant_inst)
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
                shot_act = str(shot.get("action") or "WAIT").upper()
                shot_style = str(shot.get("entry_style") or "NONE")
                shot_why = str(shot.get("reason") or shot.get("blocker_code") or "")

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

                # Active judges & per-judge clock grading
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
                        if vd > 0:
                            jst["buy"] += 1
                        else:
                            jst["sell"] += 1
                        h_min = ins.judge_clock(jname)
                        p_fut = _price_after(trade_eps, trades, tc_ep, h_min)
                        if p_fut is not None:
                            mv = (p_fut - px) if vd > 0 else (px - p_fut)
                            jst["scored"] += 1
                            if mv > 0:
                                jst["hits"] += 1
                            jst["pts"] += (mv - spread_cost)

                # Forward tape outcomes for this cycle
                p5 = _price_after(trade_eps, trades, tc_ep, 5)
                p15 = _price_after(trade_eps, trades, tc_ep, 15)
                p30 = _price_after(trade_eps, trades, tc_ep, 30)
                hi30, lo30 = _excursion_30m(trade_eps, trades, tc_ep, 30)

                fav30 = adv30 = None
                fix_out, fix_pts = "NO_SIGNAL", 0.0
                c3_out, c3_pts = "NO_SIGNAL", 0.0
                tr10_out, tr10_pts = "NO_SIGNAL", 0.0
                a2_fix_pts = a2_atr_pts = None

                if sig_dir in ("BUY", "SELL") and px > 0:
                    is_b = sig_dir == "BUY"
                    if hi30 is not None and lo30 is not None:
                        fav30 = (hi30 - px) if is_b else (px - lo30)
                        adv30 = (px - lo30) if is_b else (hi30 - px)

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
                    if shot_act == "GO" or "AGGRESSIVE" in shot_style.upper():
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
                    "gate_stage": gate_stage,
                    "active_votes": active_votes,
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
        "errors": errors,
        "elapsed_s": round(time.perf_counter() - t_wall0, 2),
    }


# --------------------------------------------------------------------------- #
# HTML Builder 1: Single-Day "Every decision" Page (Current Version on Raw Data)
# --------------------------------------------------------------------------- #
def build_day_decisions_html(res: Dict[str, Any]) -> str:
    day_str = res["day"]
    cycles = [c for c in res["cycles"] if not c.get("error")]
    cards: List[str] = []

    for c in cycles:
        d = c["direction"]
        conf = c["confidence"]
        px = c["price"]
        p5, p15, p30 = c["p5"], c["p15"], c["p30"]
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
    <span class="num">+15m {_fmt((p15 - px) if (p15 and px) else None, 2, True)}</span>
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
      <div><h4>What Happened Next (Real Future Tape)</h4>
        <table>
          <tr><td>+5 min</td><td>{_fmt(p5, 2)}</td><td>{_fmt((p5 - px) if (p5 and px) else None, 2, True)}</td></tr>
          <tr><td>+15 min</td><td>{_fmt(p15, 2)}</td><td>{_fmt((p15 - px) if (p15 and px) else None, 2, True)}</td></tr>
          <tr><td>+30 min</td><td>{_fmt(p30, 2)}</td><td>{_fmt((p30 - px) if (p30 and px) else None, 2, True)}</td></tr>
          <tr><td>30m in favour / against</td><td colspan="2">{_fmt(c['fav30'], 2)} / {_fmt(c['adv30'], 2)}</td></tr>
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
        f"{day_str} ({res['day_type']}, net {res['day_net']:+.1f} / range {res['day_range']:.1f} pts) — "
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

    def _new_bucket() -> Dict[str, Any]:
        return {"n": 0, "tp": 0, "sl": 0, "to": 0, "fix_pts": 0.0, "c3_pts": 0.0, "c3_n": 0,
                "tr10_pts": 0.0, "m15_hit": 0, "m15_n": 0, "m15_pts": 0.0}

    by_gate = defaultdict(_new_bucket)
    by_conf = defaultdict(_new_bucket)
    by_regime = defaultdict(_new_bucket)
    by_dir_reg = defaultdict(_new_bucket)
    by_hour = defaultdict(_new_bucket)
    by_fams = defaultdict(_new_bucket)
    judge_tot = defaultdict(lambda: {"days": 0, "pos_days": 0, "calls": 0, "scored": 0, "hits": 0, "pts": 0.0, "w": 0.0})

    a2_bm8 = {"n": 0, "fix18_pts": 0.0, "atr10_pts": 0.0}
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
        if c["p15"] is not None and c["price"] > 0:
            mv15 = (c["p15"] - c["price"]) if c["direction"] == "BUY" else (c["price"] - c["p15"])
            b["m15_n"] += 1
            b["m15_pts"] += mv15
            if mv15 > 0:
                b["m15_hit"] += 1

    for r in ok_days:
        for j, jst in r["judge_stats"].items():
            jt = judge_tot[j]
            jt["days"] += 1
            if jst["pts"] > 0:
                jt["pos_days"] += 1
            jt["calls"] += jst["calls"]
            jt["scored"] += jst["scored"]
            jt["hits"] += jst["hits"]
            jt["pts"] += jst["pts"]
            jt["w"] = jst["w"]

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

            if c["a2_fix_pts"] is not None and c["a2_atr_pts"] is not None:
                a2_bm8["n"] += 1
                a2_bm8["fix18_pts"] += c["a2_fix_pts"]
                a2_bm8["atr10_pts"] += c["a2_atr_pts"]

    # Build shareable plain-text report
    lines: List[str] = []
    lines.append("=" * 100)
    lines.append(" LAW #8 CURRENT-VERSION RAW-DATA REPLAY & DIAGNOSTIC REPORT")
    lines.append(f" Generated: {datetime.now(BUDA_TZ):%Y-%m-%d %H:%M:%S %Z} | Days: {len(ok_days)} ({ok_days[0]['day']} .. {ok_days[-1]['day']})")
    lines.append(f" Total Replayed Cycles: {total_cycles:,} | Signals: {total_signals:,} | Active Conf Threshold: {conf_thr:.0f}%")
    lines.append(" Zero old decisions/reports trusted -- 100% computed fresh from raw ticks + raw mbo on CURRENT code.")
    lines.append("=" * 100)

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

    def _fmt_bucket_table(title: str, mapping: Dict[Any, Dict[str, Any]], key_order: Optional[Sequence[Any]] = None) -> None:
        lines.append(f"\n{title}")
        lines.append(f"  {'BUCKET':<42} {'N':>5} {'TP/SL':>9} {'WR%':>6} {'+15m%':>6} {'FIX_PTS':>9} {'PTS/SIG':>8} {'C3_0.5A':>9} {'TR_1.0A':>9}")
        lines.append("  " + "-" * 108)
        keys = key_order if key_order is not None else [k for k, _ in sorted(mapping.items(), key=lambda kv: -kv[1]["n"])]
        for k in keys:
            if k not in mapping or mapping[k]["n"] == 0:
                continue
            b = mapping[k]
            denom = b["tp"] + b["sl"]
            wr = (100.0 * b["tp"] / denom) if denom else 0.0
            m15p = (100.0 * b["m15_hit"] / b["m15_n"]) if b["m15_n"] else 0.0
            tp_sl_str = f"{b['tp']}/{b['sl']}"
            lines.append(
                f"  {str(k):<42} {b['n']:>5} {tp_sl_str:>9} {wr:>5.1f}% {m15p:>5.1f}% "
                f"{b['fix_pts']:>+9.1f} {b['fix_pts']/b['n']:>+8.2f} {b['c3_pts']:>+9.1f} {b['tr10_pts']:>+9.1f}"
            )

    _fmt_bucket_table("[2] GATE WATERFALL (CURRENT VERSION)", by_gate)
    _fmt_bucket_table("[3] CONFIDENCE BUCKETS (CURRENT ACTIVE PANEL)", by_conf, ["<25%", "25-34%", "35-49%", "50-64%", ">=65%"])
    _fmt_bucket_table("[4] FAMILY INDEPENDENCE CHECK", by_fams)
    _fmt_bucket_table("[5] DIRECTION x REGIME MATRIX", by_dir_reg)
    _fmt_bucket_table("[6] BUDAPEST HOUR OF DAY (LOCAL CET/CEST)", {f"{h:02d}:00": by_hour[h] for h in sorted(by_hour)}, [f"{h:02d}:00" for h in sorted(by_hour)])

    lines.append("\n[7] CURRENT ACTIVE JUDGES SCORECARD (GRADED ON EACH JUDGE'S OWN HORIZON CLOCK, NET OF SPREAD)")
    lines.append(f"  {'JUDGE':<22} {'W':>5} {'POS_DAYS':>8} {'SCORED':>7} {'RIGHT%':>7} {'NET_PTS':>10} {'PTS/CALL':>9}")
    lines.append("  " + "-" * 74)
    for j, jt in sorted(judge_tot.items(), key=lambda kv: -kv[1]["pts"]):
        if jt["scored"] == 0:
            continue
        acc = 100.0 * jt["hits"] / jt["scored"]
        pdays_str = f"{jt['pos_days']}/{jt['days']}"
        lines.append(
            f"  {j:<22} {jt['w']:>5.2f} {pdays_str:>8} "
            f"{jt['scored']:>7} {acc:>6.1f}% {jt['pts']:>+10.1f} {jt['pts']/jt['scored']:>+9.3f}"
        )

    if a2_bm8["n"] > 0:
        lines.append(
            f"\n[8] BOOKMARK #8 SPRINT STOP CHECK (N={a2_bm8['n']} Shooting GO / Aggressive cycles): "
            f"Fixed 1.80/1.80 = {a2_bm8['fix18_pts']:+.1f} pts vs max(1.80, 1.0xATR) = {a2_bm8['atr10_pts']:+.1f} pts"
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

    lines.append("\n[9] AUTO-DETECTED CURRENT-VERSION STRENGTHS & WEAKNESSES")
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

    print(f"[Law #8 Raw Replay] Replaying {len(target_days)} day(s) on CURRENT app version: {', '.join(target_days)}")
    results: List[Dict[str, Any]] = []
    for d in target_days:
        res = replay_single_day(
            d, root_dir=root_dir, data_dir=data_dir, every_s=max(10, int(args.every)), window_str=args.window
        )
        results.append(res)
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
