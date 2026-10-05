"""Safely load the separate Bookmap history-probe CSV for POWER regime prices only.

This module never reads/writes ticks.csv or mbo.csv and never maps Bookmap's raw
aggressor flag. Its output is intended only for completed-M5 OHLC/ADX price history.
"""
from __future__ import annotations

import csv
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

_PREFIX = "power_history_probe_"
_SUFFIX = ".csv"
_MAX_FILE_BYTES = 64 * 1024 * 1024
_MAX_ROWS = 1_000_000
_BAR_SECONDS = 300


def _as_utc(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed.astimezone(timezone.utc)
    return None


def _row_time_key(row: Mapping[str, Any]) -> Optional[int]:
    timestamp = _as_utc(row.get("timestamp", row.get("ts", row.get("time"))))
    if timestamp is None:
        return None
    return int(timestamp.timestamp() * 1_000_000)


def _price_key(row: Mapping[str, Any]) -> Optional[float]:
    try:
        value = float(row.get("price"))
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _positive_price(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _default_dir() -> Path:
    override = os.environ.get("POWER_HISTORY_PROBE_DIR", "").strip()
    if override:
        return Path(os.path.expandvars(override)).expanduser()
    return Path.home() / "BookmapPowerHistoryProbe"


def _has_realtime_marker(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            for row in csv.DictReader(stream):
                if str(row.get("record_type", "")).strip().upper() == "REALTIME_START":
                    return True
    except (OSError, UnicodeError, csv.Error):
        return False
    return False


def _latest_probe_file(directory: Path) -> Optional[Path]:
    # The bounded v2 add-on uses one fixed snapshot so files do not accumulate.
    compact = directory / "power_history_probe_latest.csv"
    try:
        if compact.is_file():
            return compact
        candidates = [p for p in directory.glob(_PREFIX + "*" + _SUFFIX) if p.is_file()]
        candidates.sort(key=lambda p: p.stat().st_mtime_ns, reverse=True)
    except OSError:
        return None
    if not candidates:
        return None
    # Prefer the newest completed run; an in-progress newer CSV must not hide the
    # last finished probe. If none is complete, return the newest for diagnostics.
    for candidate in candidates:
        if _has_realtime_marker(candidate):
            return candidate
    return candidates[0]


def _to_timestamp_ns(raw: Any) -> Optional[datetime]:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError, OverflowError):
        return None
    if value <= 0:
        return None
    seconds, remainder = divmod(value, 1_000_000_000)
    try:
        # Keep microsecond precision; the regime classifier only buckets to M5.
        return datetime.fromtimestamp(seconds, tz=timezone.utc).replace(
            microsecond=remainder // 1_000)
    except (OverflowError, OSError, ValueError):
        return None


# Price-scale compatibility band between probe history prices and live tick
# prices. Both sides must quote the same instrument in the same price units
# before their bars may be combined into one OHLC series. A median-price ratio
# outside this band means the units differ (for example one side is 10x the
# other) and the history must be refused. The band is far wider than any
# realistic short-window price move so ordinary market movement never trips it.
PRICE_SCALE_RATIO_MIN = 0.8
PRICE_SCALE_RATIO_MAX = 1.25


def _median(values: List[float]) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def check_price_scale_compatibility(
    history_prices: Optional[Iterable[Any]],
    live_prices: Optional[Iterable[Any]],
) -> Dict[str, Any]:
    """Compare probe-history price level against live tick price level.

    The two sources must quote the same instrument in the same price units
    before their bars may be combined into one OHLC series. The median price of
    each side is compared; a ratio outside PRICE_SCALE_RATIO_MIN..MAX means the
    units differ (for example one side is 10x the other). This check NEVER
    rescales either side. Status values:

    - ``OK``: same scale, history may be used.
    - ``MISMATCH``: scales differ, the caller must refuse the history.
    - ``NO_HISTORY``: nothing to check.
    - ``NO_LIVE_PRICES``: history cannot be verified against live data; the
      caller must refuse the history (fail closed).
    """
    history_values = [p for p in (_positive_price(v) for v in (history_prices or []))
                      if p is not None]
    live_values = [p for p in (_positive_price(v) for v in (live_prices or []))
                   if p is not None]
    history_median = _median(history_values)
    live_median = _median(live_values)
    base = {
        "history_prices": len(history_values),
        "live_prices": len(live_values),
        "history_median_price": history_median,
        "live_median_price": live_median,
        "ratio": None,
        "min_ratio": PRICE_SCALE_RATIO_MIN,
        "max_ratio": PRICE_SCALE_RATIO_MAX,
    }
    if history_median is None:
        base["status"] = "NO_HISTORY"
        return base
    if live_median is None:
        base["status"] = "NO_LIVE_PRICES"
        return base
    ratio = history_median / live_median
    base["ratio"] = round(ratio, 6)
    base["status"] = ("OK" if PRICE_SCALE_RATIO_MIN <= ratio <= PRICE_SCALE_RATIO_MAX
                      else "MISMATCH")
    return base


def m5_tail_diagnostics(
    ticks: Iterable[Mapping[str, Any]], now: Any, grace_seconds: float = 5.0,
    completion_cutoff_from_latest_tick: bool = False,
) -> Dict[str, Any]:
    """Measure the latest observed contiguous completed UTC M5 tail.

    This reports the latest run present in the supplied data independently of
    whether it reaches the current expected bar. For probe-only diagnostics, use
    ``completion_cutoff_from_latest_tick=True`` so later live time cannot complete
    a partial historical bucket. The regime classifier performs separate
    freshness/latest-bar/gap gates on the combined stream.
    """
    current = _as_utc(now)
    if current is None:
        return {"completed_m5_buckets": 0, "latest_contiguous_m5_bars": 0,
                "latest_completed_bar_end_utc": None,
                "latest_expected_bar_present": False}
    try:
        grace = min(60.0, max(0.0, float(grace_seconds)))
    except (TypeError, ValueError, OverflowError):
        grace = 5.0
    boundary = math.floor((current.timestamp() - grace) / _BAR_SECONDS) * _BAR_SECONDS
    points = []
    for row in ticks or []:
        if not isinstance(row, Mapping):
            continue
        timestamp = _as_utc(row.get("timestamp", row.get("ts", row.get("time"))))
        if timestamp is None or timestamp > current or _price_key(row) is None:
            continue
        points.append(timestamp.timestamp())
    cutoff_epoch = boundary
    if completion_cutoff_from_latest_tick and points:
        cutoff_epoch = min(current.timestamp(), max(points))
    buckets = set()
    for timestamp_epoch in points:
        bucket = math.floor(timestamp_epoch / _BAR_SECONDS) * _BAR_SECONDS
        if bucket + _BAR_SECONDS <= cutoff_epoch:
            buckets.add(bucket)
    if not buckets:
        return {"completed_m5_buckets": 0, "latest_contiguous_m5_bars": 0,
                "latest_completed_bar_end_utc": None,
                "latest_expected_bar_present": False}
    latest = max(buckets)
    tail = 1
    cursor = latest - _BAR_SECONDS
    while cursor in buckets:
        tail += 1
        cursor -= _BAR_SECONDS
    latest_end = datetime.fromtimestamp(latest + _BAR_SECONDS, tz=timezone.utc)
    return {
        "completed_m5_buckets": len(buckets),
        "latest_contiguous_m5_bars": tail,
        "latest_completed_bar_end_utc": latest_end.isoformat().replace("+00:00", "Z"),
        "latest_expected_bar_present": latest == (
            math.floor(cutoff_epoch / _BAR_SECONDS) * _BAR_SECONDS - _BAR_SECONDS),
    }


def load_probe_regime_prices(
    *,
    symbol: str,
    now: Any,
    directory: Any = None,
    max_age_seconds: float = 6 * 60 * 60,
    existing_ticks: Optional[Iterable[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Load completed-probe PRE_REALTIME trade prices for regime calculation only.

    Fails closed (returns no ticks) when the file is missing/partial, its instrument
    alias does not exactly match the active Bookmap symbol, timestamps are invalid,
    or the rows exceed the age bound. Raw aggressor flags are intentionally ignored.
    """
    current = _as_utc(now)
    if current is None:
        return {"status": "INVALID_NOW", "ticks": [], "path": None}
    active_symbol = str(symbol or "").strip()
    if not active_symbol:
        return {"status": "NO_ACTIVE_SYMBOL", "ticks": [], "path": None}
    try:
        max_age = float(max_age_seconds)
    except (TypeError, ValueError, OverflowError):
        max_age = 6 * 60 * 60
    if not math.isfinite(max_age) or max_age <= 0:
        max_age = 6 * 60 * 60

    base = Path(directory).expanduser() if directory else _default_dir()
    path = _latest_probe_file(base)
    if path is None:
        return {"status": "NO_FILE", "ticks": [], "path": str(base)}
    try:
        if path.stat().st_size > _MAX_FILE_BYTES:
            return {"status": "FILE_TOO_LARGE", "ticks": [], "path": str(path)}
    except OSError:
        return {"status": "FILE_UNREADABLE", "ticks": [], "path": str(path)}

    cutoff = current - timedelta(seconds=max_age)
    marker_seen = False
    alias_seen = set()
    pre_rows = 0
    old_rows = 0
    invalid_rows = 0
    raw_ticks: List[Tuple[datetime, float]] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            for row_number, row in enumerate(reader, start=2):
                if row_number > _MAX_ROWS + 1:
                    return {"status": "TOO_MANY_ROWS", "ticks": [], "path": str(path)}
                kind = str(row.get("record_type", "")).strip().upper()
                phase = str(row.get("phase", "")).strip().upper()
                if kind == "REALTIME_START":
                    marker_seen = True
                    marker_alias = str(row.get("alias", "")).strip().strip('"')
                    if marker_alias:
                        alias_seen.add(marker_alias.casefold())
                    continue
                if kind != "TRADE" or phase != "PRE_REALTIME":
                    continue
                pre_rows += 1
                alias = str(row.get("alias", "")).strip().strip('"')
                if alias:
                    alias_seen.add(alias.casefold())
                timestamp = _to_timestamp_ns(row.get("event_time_ns"))
                price = _price_key(row)
                if timestamp is None or price is None:
                    invalid_rows += 1
                    continue
                if timestamp > current:
                    invalid_rows += 1
                    continue
                if timestamp < cutoff:
                    old_rows += 1
                    continue
                raw_ticks.append((timestamp, price))
    except (OSError, UnicodeError, csv.Error):
        return {"status": "FILE_READ_ERROR", "ticks": [], "path": str(path)}

    if not marker_seen:
        return {"status": "INCOMPLETE_NO_REALTIME_MARKER", "ticks": [], "path": str(path),
                "pre_realtime_rows": pre_rows}
    expected_alias = active_symbol
    match_basis = "exact_runtime_symbol"
    configured_alias = os.environ.get("POWER_HISTORY_PROBE_ALIAS", "").strip()
    confirmed_runtime_symbol = os.environ.get("POWER_HISTORY_PROBE_RUNTIME_SYMBOL", "").strip()
    if configured_alias and configured_alias.casefold() != active_symbol.casefold():
        if confirmed_runtime_symbol.casefold() != active_symbol.casefold():
            return {"status": "UNCONFIRMED_ALIAS_MAPPING", "ticks": [], "path": str(path),
                    "runtime_symbol": active_symbol, "configured_alias": configured_alias,
                    "pre_realtime_rows": pre_rows}
        expected_alias = configured_alias
        match_basis = "explicit_runtime_symbol_alias_pair"
    expected = expected_alias.casefold()
    if not alias_seen:
        return {"status": "NO_ALIAS", "ticks": [], "path": str(path),
                "pre_realtime_rows": pre_rows}
    if alias_seen != {expected}:
        return {"status": "SYMBOL_MISMATCH", "ticks": [], "path": str(path),
                "runtime_symbol": active_symbol, "expected_alias": expected_alias,
                "probe_aliases": sorted(alias_seen), "pre_realtime_rows": pre_rows}

    existing_keys = set()
    for row in existing_ticks or []:
        if not isinstance(row, Mapping):
            continue
        timestamp_key = _row_time_key(row)
        price_key = _price_key(row)
        if timestamp_key is not None and price_key is not None:
            existing_keys.add((timestamp_key, round(price_key, 8)))

    ticks: List[Dict[str, Any]] = []
    seen = set(existing_keys)
    duplicate_rows = 0
    for timestamp, price in raw_ticks:
        timestamp_key = int(timestamp.timestamp() * 1_000_000)
        key = (timestamp_key, round(price, 8))
        if key in seen:
            duplicate_rows += 1
            continue
        seen.add(key)
        ticks.append({
            "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
            "price": price,
            "source": "bookmap_probe_pre_realtime",
        })
    ticks.sort(key=lambda item: item["timestamp"])

    return {
        "status": "LOADED" if ticks else "NO_USABLE_RECENT_HISTORY",
        "path": str(path),
        "alias": expected_alias,
        "runtime_symbol": active_symbol,
        "match_basis": match_basis,
        "pre_realtime_rows": pre_rows,
        "loaded_ticks": len(ticks),
        "duplicate_rows": duplicate_rows,
        "ignored_old_rows": old_rows,
        "invalid_rows": invalid_rows,
        "oldest_utc": ticks[0]["timestamp"] if ticks else None,
        "newest_utc": ticks[-1]["timestamp"] if ticks else None,
        "ticks": ticks,
        "side_data_used": False,
    }


def load_probe_regime_bars(
    *,
    symbol: str,
    now: Any,
    directory: Any = None,
    max_age_seconds: float = 6 * 60 * 60,
    required_bars: int = 28,
) -> Dict[str, Any]:
    """Load a bounded snapshot of completed PRE_REALTIME M5 OHLC bars.

    The v2 probe file contains at most 28 bars. This loader is intentionally
    separate from the legacy raw-trade loader and never reads or changes the
    GoldBridge tick/MBO files. Any malformed, stale, gapped, wrong-symbol, or
    incomplete snapshot returns no bars so the regime path fails closed.
    """
    current = _as_utc(now)
    if current is None:
        return {"status": "INVALID_NOW", "bars": [], "path": None}
    active_symbol = str(symbol or "").strip()
    if not active_symbol:
        return {"status": "NO_ACTIVE_SYMBOL", "bars": [], "path": None}
    try:
        max_age = float(max_age_seconds)
    except (TypeError, ValueError, OverflowError):
        max_age = 6 * 60 * 60
    if not math.isfinite(max_age) or max_age <= 0:
        max_age = 6 * 60 * 60
    if required_bars < 1 or required_bars > 28:
        return {"status": "INVALID_REQUIRED_BARS", "bars": [], "path": None}

    base = Path(directory).expanduser() if directory else _default_dir()
    path = base / "power_history_probe_latest.csv"
    try:
        if not path.is_file():
            return {"status": "NO_COMPACT_FILE", "bars": [], "path": str(path)}
        if path.stat().st_size > 16 * 1024:
            return {"status": "FILE_TOO_LARGE", "bars": [], "path": str(path)}
    except OSError:
        return {"status": "FILE_UNREADABLE", "bars": [], "path": str(path)}

    bars: List[Dict[str, Any]] = []
    aliases = set()
    completion_status = ""
    captured_at: Optional[datetime] = None
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            required_columns = {
                "record_type", "phase", "bar_start_ns", "bar_end_ns", "alias",
                "open", "high", "low", "close", "volume", "captured_at_utc", "status",
            }
            if not required_columns.issubset(set(reader.fieldnames or [])):
                return {"status": "BAD_SCHEMA", "bars": [], "path": str(path)}
            for row_number, row in enumerate(reader, start=2):
                if row_number > 32:
                    return {"status": "TOO_MANY_ROWS", "bars": [], "path": str(path)}
                kind = str(row.get("record_type", "")).strip().upper()
                if kind == "PROBE_COMPLETE":
                    completion_status = str(row.get("status", "")).strip().upper()
                    captured_at = _as_utc(row.get("captured_at_utc"))
                    continue
                if kind != "M5_BAR":
                    continue
                if str(row.get("phase", "")).strip().upper() != "PRE_REALTIME":
                    return {"status": "BAD_PHASE", "bars": [], "path": str(path)}
                alias = str(row.get("alias", "")).strip().strip('"')
                if alias:
                    aliases.add(alias.casefold())
                start_dt = _to_timestamp_ns(row.get("bar_start_ns"))
                end_dt = _to_timestamp_ns(row.get("bar_end_ns"))
                if start_dt is None or end_dt is None:
                    return {"status": "INVALID_BAR_TIME", "bars": [], "path": str(path)}
                try:
                    o, h, low, close = (float(row.get(k, "")) for k in ("open", "high", "low", "close"))
                    volume = int(str(row.get("volume", "0")).strip())
                except (TypeError, ValueError, OverflowError):
                    return {"status": "INVALID_BAR_VALUE", "bars": [], "path": str(path)}
                values = (o, h, low, close)
                if (not all(math.isfinite(v) and v > 0 for v in values)
                        or h < max(o, close, low) or low > min(o, close, h)
                        or volume < 0):
                    return {"status": "INVALID_BAR_VALUE", "bars": [], "path": str(path)}
                if end_dt - start_dt != timedelta(seconds=_BAR_SECONDS):
                    return {"status": "INVALID_BAR_WIDTH", "bars": [], "path": str(path)}
                if end_dt > current:
                    return {"status": "FUTURE_BAR", "bars": [], "path": str(path)}
                bars.append({
                    "start": start_dt.timestamp(),
                    "start_utc": start_dt.isoformat().replace("+00:00", "Z"),
                    "end_utc": end_dt.isoformat().replace("+00:00", "Z"),
                    "open": o, "high": h, "low": low, "close": close,
                    "volume": volume, "alias": alias,
                })
    except (OSError, UnicodeError, csv.Error):
        return {"status": "FILE_READ_ERROR", "bars": [], "path": str(path)}

    if not captured_at:
        return {"status": "MISSING_COMPLETION_MARKER", "bars": [], "path": str(path)}
    age = (current - captured_at).total_seconds()
    if age < -5 or age > max_age:
        return {"status": "STALE_SNAPSHOT", "bars": [], "path": str(path),
                "snapshot_age_seconds": round(age, 3)}
    if aliases != {active_symbol.casefold()}:
        return {"status": "SYMBOL_MISMATCH", "bars": [], "path": str(path),
                "runtime_symbol": active_symbol, "probe_aliases": sorted(aliases)}
    if completion_status != "READY":
        return {"status": completion_status or "INCOMPLETE", "bars": [], "path": str(path),
                "available_bars": len(bars), "captured_at_utc": captured_at.isoformat()}
    if len(bars) > required_bars:
        return {"status": "TOO_MANY_BARS", "bars": [], "path": str(path),
                "loaded_bars": len(bars), "maximum_bars": required_bars}

    bars.sort(key=lambda bar: bar["start"])
    for previous, following in zip(bars, bars[1:]):
        if int(round(following["start"] - previous["start"])) != _BAR_SECONDS:
            return {"status": "GAP_IN_HISTORY", "bars": [], "path": str(path),
                    "loaded_bars": len(bars)}
    if len(bars) != required_bars:
        return {"status": "BAR_COUNT_MISMATCH", "bars": [], "path": str(path),
                "loaded_bars": len(bars), "required_bars": required_bars}
    latest_end = _as_utc(bars[-1]["end_utc"])
    if latest_end is None or (current - latest_end).total_seconds() > max_age:
        return {"status": "STALE_BARS", "bars": [], "path": str(path)}
    return {
        "status": "LOADED", "path": str(path), "alias": active_symbol,
        "loaded_bars": len(bars), "latest_contiguous_m5_bars": len(bars),
        "captured_at_utc": captured_at.isoformat().replace("+00:00", "Z"),
        "latest_completed_bar_end_utc": bars[-1]["end_utc"], "bars": bars,
    }


def merge_regime_price_ticks(
    live_ticks: Iterable[Mapping[str, Any]],
    history_ticks: Iterable[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge history prices with live ticks; dedupe price/time for OHLC only.

    Fail closed on price-scale disagreement: history rows are dropped (never
    rescaled) unless their price level is verified compatible with the live
    prices. With no live prices to verify against, history is refused too.
    """
    live_rows = [row for row in (live_ticks or []) if isinstance(row, Mapping)]
    history_rows = [row for row in (history_ticks or []) if isinstance(row, Mapping)]
    scale_check = check_price_scale_compatibility(
        (_price_key(row) for row in history_rows),
        (_price_key(row) for row in live_rows))
    if scale_check["status"] != "OK":
        history_rows = []
    output: List[Dict[str, Any]] = []
    seen = set()
    for source_rows in (history_rows, live_rows):
        for row in source_rows:
            ts = _as_utc(row.get("timestamp", row.get("ts", row.get("time"))))
            price = _price_key(row)
            if ts is None or price is None:
                continue
            key = (int(ts.timestamp() * 1_000_000), round(price, 8))
            if key in seen:
                continue
            seen.add(key)
            output.append({"timestamp": ts.isoformat().replace("+00:00", "Z"),
                           "price": price})
    output.sort(key=lambda item: item["timestamp"])
    return output
