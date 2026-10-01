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
    try:
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


def merge_regime_price_ticks(
    live_ticks: Iterable[Mapping[str, Any]],
    history_ticks: Iterable[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Merge history prices with live ticks; dedupe price/time for OHLC only."""
    output: List[Dict[str, Any]] = []
    seen = set()
    for source_rows in (history_ticks or [], live_ticks or []):
        for row in source_rows:
            if not isinstance(row, Mapping):
                continue
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
