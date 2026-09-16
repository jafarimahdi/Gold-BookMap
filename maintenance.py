"""
maintenance.py
==============
Automatic file maintenance — keeps the app light over weeks/months.

Without this, a few files would grow forever:
  - logs/trading_YYYYMMDD.log   (one new log file per day)
  - data/decisions_log.csv      (one row per analysis cycle)
  - data/trade_outcomes.csv     (one row per closed trade)
  - data/record_*.json          (one file per recorded session)

This module trims/rotates them automatically, so the app can run for months
without ever filling the disk or slowing down.

Settings (in .env):
  MAINTENANCE_ENABLED=1        master switch
  LOG_RETENTION_DAYS=7         delete log files older than this many days
  DECISIONS_LOG_MAX_ROWS=5000  keep only the latest N decision rows
  OUTCOMES_LOG_MAX_ROWS=2000   keep only the latest N outcome rows
  RECORD_RETENTION_DAYS=30     delete recordings older than this many days

run_maintenance() is called at the start of each pipeline cycle but only does
real work ONCE PER DAY (tracked in data/maintenance_state.json), so it costs
almost nothing.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

import config

logger = logging.getLogger(__name__)

_STATE_FILE = config.DATA_DIR / "maintenance_state.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _last_run_date() -> Optional[str]:
    try:
        if _STATE_FILE.exists():
            return json.loads(_STATE_FILE.read_text(encoding="utf-8")).get("date")
    except Exception:
        pass
    return None


def _set_last_run(date_str: str) -> None:
    try:
        _STATE_FILE.write_text(json.dumps({"date": date_str}),
                               encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write maintenance state: %s", exc)


def _trim_csv(path: Path, max_rows: int) -> None:
    """Keep only the latest max_rows data rows of a CSV (header preserved).

    Reads the whole file, then rewrites it — the files are small, so this is
    fast and safe. If the file is missing or unreadable, nothing happens.
    """
    if max_rows <= 0 or not path.exists():
        return
    try:
        rows = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    if len(rows) <= max_rows + 1:      # header + rows within limit
        return
    header = rows[0]
    keep = rows[-max_rows:]            # latest rows
    try:
        path.write_text("\n".join([header] + keep) + "\n", encoding="utf-8")
        logger.info("Maintenance: trimmed %s from %d to %d rows",
                    path.name, len(rows) - 1, len(keep))
    except OSError as exc:
        logger.warning("Maintenance: could not trim %s: %s", path.name, exc)


def rotate_logs() -> None:
    """Delete daily log files older than LOG_RETENTION_DAYS."""
    days = config.LOG_RETENTION_DAYS
    if days <= 0:
        return
    cutoff = _now() - timedelta(days=days)
    logs_dir = config.LOGS_DIR
    if not logs_dir.exists():
        return
    removed = 0
    for f in logs_dir.glob("trading_*.log"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("Maintenance: removed %d old log file(s)", removed)


def clean_recordings() -> None:
    """Delete record_*.json files older than RECORD_RETENTION_DAYS."""
    days = config.RECORD_RETENTION_DAYS
    if days <= 0:
        return
    cutoff = _now() - timedelta(days=days)
    removed = 0
    for f in config.DATA_DIR.glob("record_*.json"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
            if mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            continue
    if removed:
        logger.info("Maintenance: removed %d old recording(s)", removed)


def trim_csvs() -> None:
    _trim_csv(config.DATA_DIR / "decisions_log.csv",
              config.DECISIONS_LOG_MAX_ROWS)
    _trim_csv(config.DATA_DIR / "trade_outcomes.csv",
              config.OUTCOMES_LOG_MAX_ROWS)


def run_maintenance(force: bool = False) -> None:
    """Run all maintenance tasks (at most once per day unless forced)."""
    if not getattr(config, "MAINTENANCE_ENABLED", True):
        return
    today = _now().date().isoformat()
    if not force and _last_run_date() == today:
        return                      # already done today

    try:
        rotate_logs()
        trim_csvs()
        clean_recordings()
        _clean_tick_archives()
        _set_last_run(today)
    except Exception as exc:
        logger.warning("Maintenance failed (non-fatal): %s", exc)


def _clean_tick_archives() -> None:
    """v4.4.3: delete gzipped tick archives (data/archive/ticks_*.csv.gz)
    older than NT_ARCHIVE_KEEP_DAYS days. Default 0 = keep forever — they
    are the backtest record. Set e.g. 30 to keep one month of raw data."""
    keep_days = float(getattr(config, "NT_ARCHIVE_KEEP_DAYS", 0) or 0)
    if keep_days <= 0:
        return
    archive_dir = Path(config.DATA_DIR) / "archive"
    if not archive_dir.exists():
        return
    cutoff = time.time() - keep_days * 86400.0
    for gz in archive_dir.glob("ticks_*.csv.gz"):
        try:
            if gz.stat().st_mtime < cutoff:
                gz.unlink()
                logger.info("maintenance: deleted old tick archive %s", gz.name)
        except OSError:
            continue


def report_sizes() -> Dict[str, int]:
    """Return {filename: bytes} for the files the app writes (for debugging)."""
    out: Dict[str, int] = {}
    for f in list(config.DATA_DIR.glob("*")) + list(config.LOGS_DIR.glob("*")):
        if f.is_file() and not f.name.startswith("."):
            try:
                out[str(f.relative_to(config.BASE_DIR))] = f.stat().st_size
            except OSError:
                continue
    return out


if __name__ == "__main__":
    # manual run:  python maintenance.py  (force a cleanup now)
    import sys
    config.reload_env()
    logging.basicConfig(level=logging.INFO)
    run_maintenance(force=True)
    print("\nCurrent file sizes:")
    for name, size in sorted(report_sizes().items()):
        print(f"  {name:<45} {size:>10,} bytes")
