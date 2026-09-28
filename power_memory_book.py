"""Append-only, hash-chained local memory book for POWER v2 paper/research logs.

Records one decision per completed M5 bar (or a separately tagged PROVISIONAL
snapshot). It is an audit/history store only: it never feeds past outcomes back
into the Power decision and never touches trading/execution.

Single-writer use is intended. Do not have multiple independent app processes
write the same daily file until an OS-level inter-process lock is added.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import threading
from typing import Any, Dict, List, Mapping, Optional, Tuple

SCHEMA_VERSION = 1
_GENESIS = "0" * 64
_LOCK = threading.Lock()


class MemoryIntegrityError(RuntimeError):
    """Raised when an existing memory file is malformed or its hash chain breaks."""


def _utc_datetime(value: Any) -> datetime:
    if isinstance(value, str):
        raw = value.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError("timestamp must be an ISO-8601 datetime") from exc
    if not isinstance(value, datetime):
        raise TypeError("timestamp must be an aware datetime or ISO-8601 string")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone; naive times are rejected")
    return value.astimezone(timezone.utc)


def _clean(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, datetime):
        return _utc_datetime(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, Mapping):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    # Avoid serializing arbitrary objects (which may accidentally expose secrets).
    return str(value)[:500]


def _canonical(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _day_file(root: Any, timestamp_utc: datetime) -> Path:
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"power_{timestamp_utc.date().isoformat()}.jsonl"


def _scan(path: Path) -> Tuple[List[Dict[str, Any]], str, set[str]]:
    """Read and validate the whole daily chain; return rows, tail hash, IDs."""
    if not path.exists():
        return [], _GENESIS, set()
    rows: List[Dict[str, Any]] = []
    previous = _GENESIS
    record_ids: set[str] = set()
    with path.open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise MemoryIntegrityError(f"invalid JSON at {path}:{line_number}") from exc
            if not isinstance(row, dict):
                raise MemoryIntegrityError(f"record is not an object at {path}:{line_number}")
            claimed_hash = row.get("record_hash")
            claimed_previous = row.get("previous_hash")
            payload = {k: v for k, v in row.items()
                       if k not in ("record_hash", "previous_hash")}
            if claimed_previous != previous or claimed_hash != _hash(payload | {"previous_hash": previous}):
                raise MemoryIntegrityError(f"hash chain mismatch at {path}:{line_number}")
            previous = claimed_hash
            record_id = str(row.get("record_id", ""))
            if record_id:
                record_ids.add(record_id)
            rows.append(row)
    return rows, previous, record_ids


def append_snapshot(
    root: Any,
    *,
    timestamp: Any,
    symbol: str,
    judges: Mapping[str, Any],
    context: Optional[Mapping[str, Any]],
    result: Mapping[str, Any],
    reference_price: Optional[float] = None,
    phase: str = "FINAL",
    event_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Append one sanitized, UTC, hash-chained decision record.

    Use once per finalized M5 bar. Use phase='PROVISIONAL' only when a separate
    intrabar record is genuinely needed. Stable event_id makes same-process
    retries idempotent. Returns {path, record_id, appended}.
    """
    ts = _utc_datetime(timestamp)
    phase = str(phase).upper().strip()
    if phase not in {"FINAL", "PROVISIONAL"}:
        raise ValueError("phase must be FINAL or PROVISIONAL")
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol is required")
    if not isinstance(judges, Mapping) or not isinstance(result, Mapping):
        raise TypeError("judges and result must be mappings")
    ref_price = None if reference_price is None else float(reference_price)
    if ref_price is not None and not math.isfinite(ref_price):
        ref_price = None

    timestamp_text = ts.isoformat().replace("+00:00", "Z")
    stable_id = str(event_id or f"{symbol.strip()}|M5|{timestamp_text}|{phase}")
    path = _day_file(root, ts)
    with _LOCK:
        _, previous, seen_ids = _scan(path)
        if stable_id in seen_ids:
            return {"path": str(path), "record_id": stable_id, "appended": False}
        payload: Dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "record_id": stable_id,
            "timestamp_utc": timestamp_text,
            "symbol": symbol.strip(),
            "timeframe": "M5",
            "phase": phase,
            "reference_price": ref_price,
            "judges": _clean(judges),
            "context": _clean(context or {}),
            "result": _clean(result),
        }
        signed_payload = payload | {"previous_hash": previous}
        row = signed_payload | {"record_hash": _hash(signed_payload)}
        line = _canonical(row) + b"\n"
        if len(line) > 256_000:
            raise ValueError("record exceeds 256 KB safety limit")
        # One append-only write and flush. Intended for one app writer per day file.
        with path.open("ab") as stream:
            stream.write(line)
            stream.flush()
            os.fsync(stream.fileno())
        return {"path": str(path), "record_id": stable_id, "appended": True}


def read_day(root: Any, day: Any, *, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read a UTC day's records after verifying the full hash chain."""
    if isinstance(day, datetime):
        day_key = _utc_datetime(day).date().isoformat()
    elif isinstance(day, date):
        day_key = day.isoformat()
    else:
        day_key = date.fromisoformat(str(day)).isoformat()
    path = Path(root) / f"power_{day_key}.jsonl"
    rows, _, _ = _scan(path)
    if symbol is not None:
        rows = [row for row in rows if row.get("symbol") == symbol]
    return rows
