"""
main.py
=======
Entry point — runs the complete 5-step gold trading pipeline.

    STEP 0 (config)  -> STEP 1 (data) -> STEP 2 (analysis)
          -> STEP 3 (AI) -> STEP 4 (execution) -> STEP 5 (monitoring)

Usage:
    python3 main.py --doctor       # local read-only readiness checks
    python3 main.py --no-new-entries [--loop]  # analyze/manage, but open no new entries
    python3 main.py                 # one full pass (can open a demo entry if all gates pass)
    python3 main.py --loop          # continuous loop (Step 5 run_loop)

Each step is isolated in try/except so a failure in one never takes down
the rest; the final summary prints the status of every step.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import logging
import math
import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

import config
from config import DATA_DIR, LOGS_DIR

logger = logging.getLogger("main")
STATUS: list = []  # (step label, status string)


# --------------------------------------------------------------------------- #
# Single-instance lock — only ONE bot may run at a time.
#
# We use an ATOMIC lock: a second instance cannot steal the lock while the
# first is still holding it (no race, no two windows). The lock file stores
# the PID; when the old bot exits it removes the lock, and a lock that is too
# old (crashed process) is treated as stale and replaced.
# --------------------------------------------------------------------------- #
_LOCK_FILE = None
_LOCK_STALE_SECONDS = 300     # older than this -> assume the bot crashed


def _lock_path():
    global _LOCK_FILE
    if _LOCK_FILE is None:
        _LOCK_FILE = DATA_DIR / "bot.lock"
    return _LOCK_FILE


def _pid_alive(pid: int) -> bool:
    """True if a process with this PID is currently alive (Windows + Unix)."""
    try:
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if h:
            k32.CloseHandle(h)
            return True
        # access denied (error 5) still means the process EXISTS
        return ctypes.get_last_error() == 5
    except Exception:
        import os as _os
        try:
            _os.kill(pid, 0)
            return True
        except OSError:
            return False


def _acquire_lock() -> bool:
    """Atomically take the single-instance lock. Returns True if we own it.

    A second instance is refused ONLY when the lock holder is genuinely alive
    (fresh lock + live PID). If the holder crashed (dead PID) or the lock is
    very old, the new instance takes over.
    """
    import os as _os
    import time as _time
    path = _lock_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        if path.exists():
            old_pid = 0
            try:
                old_pid = int(path.read_text(encoding="utf-8").strip() or 0)
            except (ValueError, OSError):
                old_pid = 0
            try:
                age = _time.time() - path.stat().st_mtime
            except OSError:
                age = 0
            holder_alive = old_pid and age < _LOCK_STALE_SECONDS and \
                _pid_alive(old_pid)
            if holder_alive:
                return False                  # another bot is really running
            # stale or dead holder -> remove and take over
            try:
                path.unlink()
            except OSError:
                return False

        # create EXCLUSIVELY: fails immediately if another process just won
        try:
            fd = _os.open(str(path), _os.O_CREAT | _os.O_EXCL | _os.O_WRONLY)
        except FileExistsError:
            return False
        with _os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{_os.getpid()}\n")
        return True
    except OSError:
        return True                    # cannot check -> proceed (best effort)


def _touch_lock() -> None:
    """Keep the lock fresh so a second instance knows we are alive."""
    path = _lock_path()
    try:
        path.touch()
    except OSError:
        pass


def _release_lock() -> None:
    path = _lock_path()
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def setup_logging() -> None:
    """Console + daily rotating file under logs/.

    File logging is BEST-EFFORT: if the log file cannot be written (read-only
    folder, file locked by another process, no permission), the bot continues
    with console logging only instead of crashing.
    """
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(name)s] %(message)s")
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.DEBUG if config.DEBUG else logging.INFO)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    root.addHandler(console)

    # try the logs folder first, then the data folder, else console-only
    log_dirs = []
    try:
        LOGS_DIR.mkdir(exist_ok=True)
        log_dirs.append(LOGS_DIR)
    except OSError:
        pass
    try:
        DATA_DIR.mkdir(exist_ok=True)
        log_dirs.append(DATA_DIR)
    except OSError:
        pass

    file_handler = None
    for directory in log_dirs:
        try:
            fh = logging.FileHandler(
                directory / f"trading_{datetime.now():%Y%m%d}.log")
            fh.setFormatter(fmt)
            file_handler = fh
            break
        except OSError as exc:
            logging.getLogger("main").warning(
                "Could not write log file to %s (%s); trying next location.",
                directory, exc)

    if file_handler is not None:
        root.addHandler(file_handler)
    else:
        logging.getLogger("main").warning(
            "No writable log location found — continuing with console logs only.")

    for noisy in ("urllib3", "requests", "httpx", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---- v5.4 L4: Latency tracking + Flash Crash Kill Switch ----
_PRICE_HISTORY = []  # list of (timestamp, price)
_LATENCY_LOG = []

def _record_latency(stage: str, ms: float):
    if not getattr(config, "LATENCY_LOG_ENABLED", True):
        return
    _LATENCY_LOG.append((stage, ms))
    target = int(getattr(config, "LATENCY_TARGET_MS", 500))
    if ms > target:
        logger.warning(f"LATENCY {stage} {ms:.0f}ms > target {target}ms")
    else:
        logger.info(f"LATENCY {stage} {ms:.0f}ms")

def _check_flash_crash(snapshot) -> str:
    """L4: If price moves beyond max(mult*ATR, min_pts, min_pct) in FLASH_CRASH_MINUTES, trigger guard."""
    if not getattr(config, "FLASH_CRASH_ENABLED", True):
        return ""
    try:
        price = float(getattr(snapshot, "price", 0.0) or 0.0)
        atr = float(getattr(getattr(snapshot, "volatility", None), "atr", 0.0) or 0.0)
        if price <= 0 or atr <= 0:
            return ""
        mult = float(getattr(config, "FLASH_CRASH_ATR_MULT", 3.0))
        minutes = float(getattr(config, "FLASH_CRASH_MINUTES", 1.0))
        min_pts = max(0.0, float(getattr(config, "FLASH_CRASH_MIN_PTS", 12.0) or 0.0))
        min_pct = max(0.0, float(getattr(config, "FLASH_CRASH_MIN_PCT", 0.25) or 0.0))
        now = time.time()
        _PRICE_HISTORY.append((now, price))
        cutoff = now - minutes * 120
        while _PRICE_HISTORY and _PRICE_HISTORY[0][0] < cutoff:
            _PRICE_HISTORY.pop(0)
        target_ts = now - minutes * 60
        old_price = None
        for ts, p in _PRICE_HISTORY:
            if ts <= target_ts:
                old_price = p
            else:
                break
        if old_price is None and len(_PRICE_HISTORY) >= 2:
            old_price = _PRICE_HISTORY[0][1]
        if old_price is None or old_price <= 0:
            return ""
        signed_move = price - old_price
        move = abs(signed_move)
        pct_floor = old_price * (min_pct / 100.0) if min_pct > 0 else 0.0
        threshold = max(mult * atr, min_pts, pct_floor)
        if move > threshold:
            return (
                f"FLASH CRASH: price {old_price:.2f} -> {price:.2f} "
                f"move {signed_move:+.2f} ({move:.2f} > {threshold:.2f} "
                f"[max({mult}*ATR {atr:.2f}, {min_pts:.1f}pts, {min_pct:.2f}%)]) "
                f"in {minutes:.0f}min"
            )
    except Exception as e:
        logger.debug(f"Flash crash check error: {e}")
    return ""

def _judge_panel_from_notes(notes):
    """v7.1 add-on hook: per-judge panel for the diary; safe if judge_panel.py absent."""
    try:
        from judge_panel import parse_judge_panel
        return parse_judge_panel(list(notes or []))
    except Exception:
        return []


def _record(step: str, status: str) -> None:
    # crowdv2:v1 -- item 67: ASCII-fold at the logging boundary.  AI text can
    # carry non-ASCII (e.g. Greek sigma in "VWAP -1-sigma band"); the Windows
    # console is cp1252 and crashed on it (2026-10-16 19:33 UnicodeEncodeError).
    # Folding here protects every caller; content survives as \uXXXX escapes.
    step = str(step).encode("ascii", "backslashreplace").decode("ascii")
    status = str(status).encode("ascii", "backslashreplace").decode("ascii")
    STATUS.append((step, status))
    logger.info("%s -> %s", step, status)


_PAUSE_LOG_TS = 0.0


def _entries_paused() -> bool:
    """v4.4 PAUSE FILE: a file named PAUSE next to main.py suspends NEW
    entries (AI decision + order sending). Open positions keep being
    MANAGED (trailing/BE/exits) and monitoring keeps running — this is a
    pause, not a shutdown. Delete the file to resume trading.
    How to use (Windows PowerShell, in the Gold-MT5 folder):
        New-Item PAUSE          # pause new entries
        Remove-Item PAUSE       # resume
    """
    global _PAUSE_LOG_TS
    try:
        paused = (Path(__file__).resolve().parent / "PAUSE").exists()
    except Exception:
        return False
    if paused and (time.time() - _PAUSE_LOG_TS) > 900.0:
        _PAUSE_LOG_TS = time.time()
        logger.info("PAUSE file present — new entries suspended; position "
                    "management and monitoring continue. Delete the PAUSE "
                    "file to resume.")
    return paused


def _preflight_data_reason(data, elapsed_since_acquire_seconds: float = 0.0):
    """Return a fail-closed feed/quote/spread reason, or None when valid.

    The data age captured by a provider is extended by monotonic pipeline time
    before Step 4. This catches feeds that became stale during analysis/AI.
    Demo/replay keep their historical synthetic-data behavior; live providers
    must explicitly identify a valid quote and numeric spread.
    """
    if data is None or not isinstance(data, dict):
        return "no market data available"
    source = str(getattr(config, "DATA_SOURCE", "unknown") or "unknown").strip().lower()
    maximum = getattr(config, "MAX_SPREAD_PCT", 0.05)
    try:
        if isinstance(maximum, bool):
            return "invalid MAX_SPREAD_PCT configuration; no trading"
        maximum = float(maximum)
        if not math.isfinite(maximum) or maximum < 0:
            return "invalid MAX_SPREAD_PCT configuration; no trading"
    except (TypeError, ValueError, OverflowError):
        return "invalid MAX_SPREAD_PCT configuration; no trading"

    spread_raw = data.get("spread_pct")
    spread = None
    if isinstance(spread_raw, bool):
        return "invalid spread value; no trading"
    if spread_raw is not None:
        try:
            spread = float(spread_raw)
            if not math.isfinite(spread) or spread < 0:
                return "invalid spread value; no trading"
        except (TypeError, ValueError, OverflowError):
            return "invalid spread value; no trading"

    if source in ("demo", "replay"):
        if spread is not None and maximum > 0 and spread > maximum:
            return f"data spread too wide {spread:.3f}% > {maximum:.3f}% — no trading"
        return None

    if data.get("has_data") is not True:
        return "live feed empty/invalid; no trading"
    try:
        raw_recorded_age = data.get("last_data_age_seconds")
        if isinstance(raw_recorded_age, bool):
            return "live feed age unknown/invalid; no trading"
        recorded_age = float(raw_recorded_age)
        if isinstance(elapsed_since_acquire_seconds, bool):
            return "live feed age unknown/invalid; no trading"
        elapsed = float(elapsed_since_acquire_seconds or 0.0)
        if (not math.isfinite(recorded_age) or recorded_age < 0 or
                not math.isfinite(elapsed) or elapsed < 0):
            return "live feed age unknown/invalid; no trading"
    except (TypeError, ValueError, OverflowError):
        return "live feed age unknown/invalid; no trading"
    effective_age = recorded_age + elapsed
    if not math.isfinite(effective_age):
        return "live feed age unknown/invalid; no trading"
    try:
        raw_max_age = getattr(config, "STALE_DATA_SECONDS", 300.0)
        if isinstance(raw_max_age, bool):
            return "invalid STALE_DATA_SECONDS configuration; no trading"
        max_age = float(raw_max_age)
        if not math.isfinite(max_age) or max_age <= 0:
            return "invalid STALE_DATA_SECONDS configuration; no trading"
    except (TypeError, ValueError, OverflowError):
        return "invalid STALE_DATA_SECONDS configuration; no trading"
    data["decision_time_data_age_seconds"] = effective_age
    _record_latency("feed age at pre-execution", effective_age * 1000.0)
    if effective_age > max_age:
        return (f"live feed stale at decision: {effective_age:.3f}s "
                f"> {max_age:.3f}s — no trading")

    quality = data.get("data_quality") or {}
    if not isinstance(quality, dict):
        return "live quote quality metadata invalid; no trading"
    if str(quality.get("quote", "")).lower() != "valid":
        return (f"live quote not validated ({quality.get('quote', 'unknown')}); "
                "no trading")
    try:
        raw_bid, raw_ask = data.get("bid"), data.get("ask")
        if isinstance(raw_bid, bool) or isinstance(raw_ask, bool):
            raise ValueError("boolean quote")
        bid, ask = float(raw_bid), float(raw_ask)
        if (not math.isfinite(bid) or not math.isfinite(ask) or
                bid <= 0 or ask <= 0):
            return "live quote bid/ask missing or invalid; no trading"
        if ask <= bid:
            return "live quote locked/crossed; no trading"
        if spread is not None and spread > 0:
            quote_mid = (bid + ask) / 2.0
            quote_spread = (ask - bid) / quote_mid * 100.0
            if (not math.isfinite(quote_spread) or
                    not math.isclose(spread, quote_spread,
                                     rel_tol=1e-6, abs_tol=1e-8)):
                return "live spread does not match bid/ask quote; no trading"
    except (TypeError, ValueError, OverflowError):
        return "live quote bid/ask missing or invalid; no trading"
    try:
        raw_quote_age = data.get("quote_age_seconds")
        if isinstance(raw_quote_age, bool):
            return "live quote age unknown/invalid; no trading"
        quote_age = float(raw_quote_age)
        if not math.isfinite(quote_age) or quote_age < 0:
            return "live quote age unknown/invalid; no trading"
    except (TypeError, ValueError, OverflowError):
        return "live quote age unknown/invalid; no trading"
    decision_quote_age = quote_age + elapsed
    if not math.isfinite(decision_quote_age):
        return "live quote age unknown/invalid; no trading"
    data["decision_time_quote_age_seconds"] = decision_quote_age
    _record_latency("quote age at pre-execution", decision_quote_age * 1000.0)
    if decision_quote_age > max_age:
        return (f"live quote stale at decision: {decision_quote_age:.3f}s "
                f"> {max_age:.3f}s — no trading")

    raw_depth_age = data.get("depth_age_seconds")
    if raw_depth_age is not None:
        try:
            if isinstance(raw_depth_age, bool):
                return "live L2 depth age unknown/invalid; no trading"
            depth_age = float(raw_depth_age)
            if not math.isfinite(depth_age) or depth_age < 0:
                return "live L2 depth age unknown/invalid; no trading"
        except (TypeError, ValueError, OverflowError):
            return "live L2 depth age unknown/invalid; no trading"
        decision_depth_age = depth_age + elapsed
        if not math.isfinite(decision_depth_age):
            return "live L2 depth age unknown/invalid; no trading"
        data["decision_time_depth_age_seconds"] = decision_depth_age
        _record_latency("depth age at pre-execution", decision_depth_age * 1000.0)
        if decision_depth_age > max_age:
            return (f"live L2 depth stale at decision: {decision_depth_age:.3f}s "
                    f"> {max_age:.3f}s — no trading")
    if spread is None or spread <= 0:
        return "live spread unknown/invalid; no trading"
    if maximum > 0 and spread > maximum:
        return f"data spread too wide {spread:.3f}% > {maximum:.3f}% — no trading"

    if source in {"bookmapbridge", "ninjabridge", "bookmap", "bmbridge",
                  "bookmap_bridge", "ntbridge", "ninja"}:
        l2_status = str(quality.get("level2", "unknown")).lower()
        if l2_status != "validated":
            return f"BookMap L2 not validated ({l2_status}); no trading"
        if raw_depth_age is None:
            return "BookMap L2 depth age unknown; no trading"
        instrument_status = str(quality.get("instrument", "unknown")).lower()
        if instrument_status != "matched":
            if instrument_status in {"mismatch", "instrument_mismatch"}:
                return "BookMap instrument mismatch; no trading"
            return f"BookMap instrument not verified ({instrument_status}); no trading"
        diagnostics = data.get("l2_diagnostics")
        if not isinstance(diagnostics, dict):
            return "BookMap L2 diagnostics unavailable; no trading"
        if str(diagnostics.get("status", "")).lower() != "validated":
            return "BookMap L2 diagnostics not validated; no trading"
        if diagnostics.get("instrument_match") is not True:
            return "BookMap L2 diagnostic instrument mismatch; no trading"
        try:
            diagnostic_bid = diagnostics.get("best_bid")
            diagnostic_ask = diagnostics.get("best_ask")
            if isinstance(diagnostic_bid, bool) or isinstance(diagnostic_ask, bool):
                raise ValueError("boolean diagnostic quote")
            diagnostic_bid = float(diagnostic_bid)
            diagnostic_ask = float(diagnostic_ask)
            if (not math.isfinite(diagnostic_bid) or
                    not math.isfinite(diagnostic_ask) or
                    not math.isclose(diagnostic_bid, bid, rel_tol=0.0, abs_tol=1e-9) or
                    not math.isclose(diagnostic_ask, ask, rel_tol=0.0, abs_tol=1e-9)):
                return "BookMap L2 quote does not match bid/ask; no trading"
        except (TypeError, ValueError, OverflowError):
            return "BookMap L2 quote diagnostics invalid; no trading"
    return None


def _market_quality_evidence(data, elapsed_since_acquire_seconds: float = 0.0,
                             preflight_ok: Optional[bool] = None,
                             preflight_reason: str = "") -> dict:
    """Build a timestamped, read-only feed-quality receipt for shadow review."""
    data = data if isinstance(data, dict) else {}
    quality = data.get("data_quality") or {}
    quality = quality if isinstance(quality, dict) else {}
    try:
        from step2_market_analysis import _snapshot_data_quality
        quality = _snapshot_data_quality(data, data.get("tick_data") or [])
    except Exception:
        # Keep evidence capture resilient; raw provider metadata remains visible.
        pass
    try:
        elapsed = float(elapsed_since_acquire_seconds or 0.0)
        if not math.isfinite(elapsed) or elapsed < 0:
            elapsed = None
    except (TypeError, ValueError, OverflowError):
        elapsed = None

    def _age_at_decision(decision_key: str, age_key: str):
        value = data.get(decision_key)
        if value is None and elapsed is not None:
            value = data.get(age_key)
            if value is not None:
                try:
                    if isinstance(value, bool):
                        return None
                    value = float(value) + elapsed
                except (TypeError, ValueError, OverflowError):
                    return None
        try:
            if isinstance(value, bool):
                return None
            value = float(value)
            return value if math.isfinite(value) and value >= 0 else None
        except (TypeError, ValueError, OverflowError):
            return None

    spread = data.get("spread_pct")
    try:
        if isinstance(spread, bool):
            raise ValueError("boolean spread")
        spread = float(spread) if spread is not None else None
        if spread is not None and (not math.isfinite(spread) or spread < 0):
            spread = "invalid"
    except (TypeError, ValueError, OverflowError):
        spread = "invalid" if data.get("spread_pct") is not None else None
    quote_status = str(quality.get("quote", "unknown"))
    if quote_status.lower() == "valid" and spread == "invalid":
        quote_status = "invalid_spread"
    elif quote_status.lower() == "valid" and spread is None:
        quote_status = "spread_unknown"
    elif quote_status.lower() == "valid" and spread <= 0:
        quote_status = "invalid_spread"
    if quote_status.lower() == "valid":
        try:
            raw_bid, raw_ask = data.get("bid"), data.get("ask")
            if isinstance(raw_bid, bool) or isinstance(raw_ask, bool):
                raise ValueError("boolean quote")
            bid, ask = float(raw_bid), float(raw_ask)
            if (not math.isfinite(bid) or not math.isfinite(ask) or
                    bid <= 0 or ask <= 0):
                quote_status = "invalid_quote"
            elif ask <= bid:
                quote_status = "crossed_quote"
            elif spread is not None and isinstance(spread, (int, float)) and spread > 0:
                quote_mid = (bid + ask) / 2.0
                quote_spread = (ask - bid) / quote_mid * 100.0
                if not math.isclose(spread, quote_spread,
                                    rel_tol=1e-6, abs_tol=1e-8):
                    quote_status = "spread_quote_mismatch"
        except (TypeError, ValueError, OverflowError):
            quote_status = "invalid_quote"
    diagnostics = data.get("l2_diagnostics") or {}
    if not isinstance(diagnostics, dict):
        diagnostics = {"status": "invalid_metadata"}
    warnings = data.get("feed_warnings") or []
    if isinstance(warnings, str):
        warnings = [warnings]
    elif not isinstance(warnings, (list, tuple)):
        warnings = [str(warnings)]
    def _finite_number(value):
        if isinstance(value, bool):
            return None
        try:
            value = float(value)
            return value if math.isfinite(value) else None
        except (TypeError, ValueError, OverflowError):
            return None

    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "data_source": str(getattr(config, "DATA_SOURCE", "unknown") or "unknown"),
        "data_symbol": str(data.get("data_symbol") or data.get("symbol") or ""),
        "trade_symbol": str(data.get("trade_symbol") or ""),
        "price": _finite_number(data.get("price")),
        "bid": _finite_number(data.get("bid")),
        "ask": _finite_number(data.get("ask")),
        "quote_timestamp": data.get("quote_timestamp"),
        "depth_timestamp": data.get("depth_timestamp"),
        "last_trade": _finite_number(data.get(
            "last_trade", diagnostics.get("last_trade"))),
        "last_trade_timestamp": data.get(
            "last_trade_timestamp", diagnostics.get("last_trade_timestamp")),
        "last_trade_age_seconds_at_decision": _age_at_decision(
            "decision_time_last_trade_age_seconds", "last_trade_age_seconds"),
        "last_order_event_timestamp": data.get("last_order_event_timestamp"),
        "last_order_event_age_seconds_at_decision": _age_at_decision(
            "decision_time_last_order_event_age_seconds",
            "last_order_event_age_seconds"),
        "quote_status": quote_status,
        "spread_pct": spread,
        "max_spread_pct": getattr(config, "MAX_SPREAD_PCT", None),
        "feed_age_seconds_at_decision": _age_at_decision(
            "decision_time_data_age_seconds", "last_data_age_seconds"),
        "quote_age_seconds_at_decision": _age_at_decision(
            "decision_time_quote_age_seconds", "quote_age_seconds"),
        "depth_age_seconds_at_decision": _age_at_decision(
            "decision_time_depth_age_seconds", "depth_age_seconds"),
        "depth_age_seconds": data.get("depth_age_seconds"),
        "tick_size": diagnostics.get("tick_size"),
        "tick_value": diagnostics.get("tick_value"),
        "price_units": diagnostics.get("price_units", "unknown"),
        "tick_value_units": diagnostics.get("tick_value_units", "unknown"),
        "depth_size_units": diagnostics.get("depth_size_units", "unknown"),
        "timestamp_basis": diagnostics.get("timestamp_basis", "provider_unverified"),
        "source_event_time_verified": diagnostics.get(
            "source_event_time_verified"),
        "level2_status": str(quality.get("level2", "unknown")),
        "l2_diagnostics": dict(diagnostics),
        "l3_capabilities": {
            key: str(quality.get(key, "unknown"))
            for key in ("level3", "level3_events", "l3_aggressive_flow",
                        "l3_aggr_limit")
        },
        "feed_warnings": [str(item) for item in list(warnings)[:20]],
        "safety_gate_status": ("PASS" if preflight_ok is True else
                                "BLOCKED" if preflight_ok is False else "UNKNOWN"),
        "safety_gate_reason": str(preflight_reason or ""),
    }


def _attach_quality_evidence_to_shadow_candidates(snapshot) -> None:
    """Attach this cycle's receipt to every non-executable shadow candidate.

    Step 3 evaluates candidates before the post-analysis safety gate, so the
    first copy on the candidate may be empty or from the previous cycle. Refresh
    it after preflight, before decision-journal serialization; authorization
    and candidate eligibility fields are deliberately untouched.
    """
    if snapshot is None:
        return
    evidence = getattr(snapshot, "quality_evidence", None)
    if not isinstance(evidence, dict) or not evidence:
        return

    def _attach(container):
        if not isinstance(container, dict):
            return
        for candidate in container.values():
            if isinstance(candidate, dict):
                candidate["market_quality_evidence"] = dict(evidence)

    _attach(getattr(snapshot, "shadow_candidates", None))
    shot = getattr(snapshot, "shot", None)
    if isinstance(shot, dict):
        _attach(shot.get("shadow_candidates"))


def _persist_market_quality_snapshot(snapshot) -> bool:
    """Refresh the latest snapshot after its post-analysis quality receipt exists."""
    if snapshot is None:
        return False
    temporary = None
    try:
        from step2_market_analysis import _json_safe, snapshot_to_json
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        destination = DATA_DIR / "market_snapshot.json"
        temporary = destination.with_name(destination.name + ".quality.tmp")
        temporary.write_text(snapshot_to_json(snapshot), encoding="utf-8")
        os.replace(temporary, destination)

        snapshot_timestamp = getattr(snapshot, "timestamp", None)
        receipt = {
            "snapshot_timestamp": snapshot_timestamp,
            "quality_evidence": getattr(snapshot, "quality_evidence", {}) or {},
            "shadow_candidates": getattr(snapshot, "shadow_candidates", {}) or {},
        }
        evidence_path = DATA_DIR / "market_quality_evidence.jsonl"
        with evidence_path.open("a", encoding="utf-8") as evidence_file:
            evidence_file.write(json.dumps(
                _json_safe(receipt), sort_keys=True, separators=(",", ":")) + "\n")
        return True
    except Exception as exc:
        logger.warning("market-quality snapshot persistence failed: %s", exc)
        try:
            if temporary is not None and temporary.exists():
                temporary.unlink()
        except OSError:
            pass
        return False


def _demo_order_test_pending_reason() -> str:
    """Block strategy entries while the explicit one-shot DEMO test is unresolved."""
    marker = DATA_DIR / "demo_order_test_pending.json"
    if not marker.exists():
        return ""
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("marker is not an object")
        symbol = str(payload.get("symbol", "?") or "?")
        attempt = str(payload.get("attempt_id", "unknown") or "unknown")
        return (f"DEMO order smoke test {attempt} is unresolved for {symbol}; "
                "verify/close it with tools/demo_order_smoke.py --recover")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return f"DEMO order smoke-test marker is unreadable; operator recovery required ({exc})"


def _safety_gates(data, elapsed_since_acquire_seconds: float = 0.0) -> tuple:
    """Return (allowed: bool, reason: str) for pre-execution safety gates.

    Live data must remain fresh through analysis, carry an explicit valid quote
    and spread, and (for BookMap) have a validated L2 book. The separate MT5
    broker-spread guard still runs immediately before order submission.
    """
    from session import is_market_open, describe_now

    # 0) master on/off switch
    if not config.TRADING_ENABLED:
        _record("SAFETY: SWITCH", "TRADING OFF (TRADING_ENABLED=0) — analyse only")
        return False, "trading disabled (TRADING_ENABLED=0 in .env)"

    # A one-shot DEMO plumbing test is deliberately outside strategy records.
    # Keep normal entries blocked until its position is verified closed and the
    # operator's recovery command clears the durable marker.
    plumbing_reason = _demo_order_test_pending_reason()
    if plumbing_reason:
        _record("SAFETY: DEMO TEST", f"BLOCKED — {plumbing_reason}")
        return False, plumbing_reason

    # 1) session
    if not is_market_open():
        _record("SAFETY: SESSION", f"CLOSED — {describe_now()} — no trading")
        return False, f"market closed ({describe_now()})"

    # 2) live feed / quote / spread / L2 integrity
    feed_reason = _preflight_data_reason(data, elapsed_since_acquire_seconds)
    if feed_reason:
        _record("SAFETY: FEED", feed_reason)
        return False, feed_reason

    # 3) risk limits
    try:
        from risk_manager import RiskManager
        ok, reason = RiskManager().check()
        if not ok:
            _record("SAFETY: RISK", f"HALTED — {reason}")
            return False, f"risk halt: {reason}"
    except Exception as exc:
        logger.warning("Risk check failed (proceeding): %s", exc)

    # 4b) L5 RL adaptive: after 3 losses same regime, reduce size or pause
    try:
        if getattr(config, "RL_ENABLED", True):
            import trade_history as th
            rl_res = th.check_loss_streak_regime("", within_hours=24.0)
            if rl_res.get("should_pause"):
                _record("SAFETY: RL_PAUSE", f"BLOCKED — {rl_res.get('reason')}")
                return False, rl_res.get('reason', 'RL pause: 4 losses streak')
    except Exception as exc:
        logger.warning(f"RL check failed: {exc}")

    # 4) anti-overtrading guard (cooldown + daily cap)
    try:
        from trade_guard import TradeGuard
        ok, reason = TradeGuard().can_trade()
        if not ok:
            _record("SAFETY: OVERTRADE", f"BLOCKED — {reason}")
            return False, reason
    except Exception as exc:
        logger.warning("Trade-guard check failed (proceeding): %s", exc)

    return True, "ok"

_DECISION_LOG_FIELDS = [
    "timestamp", "symbol", "price", "signal_direction", "signal_strength",
    "signal_confidence", "regime", "divergence", "ai_action", "ai_confidence",
    # 24k: WHO answered. "gemini-3.5-flash-lite" is the AI; anything starting with
    # "fallback" is this robot's own rule stamped at AI_FALLBACK_CONFIDENCE (70 by
    # default); downstream configured execution gates still apply. Without this
    # column a rule and the AI are indistinguishable in every report ever written.
    "ai_model", "decision_source",
    "power_direction", "power_reason_code", "session_state", "execution_mode",
    "shooting_status", "shooting_reason", "shooting_sub_checks",
    "paper_entry_status", "exec_status", "outcome", "replay_status", "order_id",
    "news_state", "minutes_to_event", "next_event_title",
    "shadow_candidates", "ai_rationale", "reason",
]


_LEGACY_DECISION_FIELDS_17 = [
    "timestamp", "symbol", "price", "signal_direction", "signal_strength",
    "signal_confidence", "regime", "divergence", "ai_action", "ai_confidence",
    "ai_model", "exec_status", "order_id", "news_state", "minutes_to_event",
    "next_event_title", "reason",
]


def _decision_csv_schema(path):
    """Migrate old journal headers atomically while preserving every old column/row."""
    if not path.exists() or path.stat().st_size == 0:
        return list(_DECISION_LOG_FIELDS), True
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.reader(source)
        existing = list(next(reader, []) or [])
    if not existing:
        raise ValueError(f"decision journal has data but no readable header: {path}")
    fields = list(existing)
    fields.extend(name for name in _DECISION_LOG_FIELDS if name not in fields)
    if fields == existing:
        return fields, False

    # Read historical rows only when a migration is necessary. Repair the known
    # pre-24k stale header (16 names, 17 emitted values) using its verified field
    # order; refuse unknown extra-column shapes rather than shifting/losing data.
    with path.open("r", newline="", encoding="utf-8-sig") as source:
        reader = csv.reader(source)
        next(reader, None)
        raw_rows = [values for values in reader if values]
    migrated_rows = []
    known_stale_header_16 = [
        name for name in _LEGACY_DECISION_FIELDS_17 if name != "ai_model"]
    for values in raw_rows:
        if (existing == known_stale_header_16
                and len(values) == len(_LEGACY_DECISION_FIELDS_17)):
            old_row = dict(zip(_LEGACY_DECISION_FIELDS_17, values))
        elif len(values) <= len(existing):
            padded = values + [""] * (len(existing) - len(values))
            old_row = dict(zip(existing, padded))
        else:
            raise ValueError(
                f"unrecognized decision journal row width ({len(values)} values, "
                f"{len(existing)} header columns); left unchanged: {path}")
        migrated_rows.append({name: old_row.get(name, "") for name in fields})

    temporary = path.with_name(path.name + ".schema.tmp")
    with temporary.open("w", newline="", encoding="utf-8") as target:
        writer = csv.DictWriter(target, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(migrated_rows)
    os.replace(temporary, path)
    return fields, False


def log_decision(snapshot, decision, exec_result) -> None:
    """Append one row to data/decisions_log.csv (a decision journal)."""
    path = DATA_DIR / "decisions_log.csv"
    try:
        _decision_fields, write_header = _decision_csv_schema(path)
    except (OSError, csv.Error, ValueError) as exc:
        logger.warning("Could not prepare decision journal schema: %s", exc)
        return
    _shot = getattr(snapshot, "shot", None) or {}
    _shot = _shot if isinstance(_shot, dict) else {}
    _receipt = _shot.get("visibility") or _shot.get("veto_details") or {}
    _receipt = _receipt if isinstance(_receipt, dict) else {}
    _power = getattr(snapshot, "power", None) or {}
    _power = _power if isinstance(_power, dict) else {}
    _paper_entry = getattr(snapshot, "entry_simulation", None) or {}
    _paper_entry = _paper_entry if isinstance(_paper_entry, dict) else {}
    _session_state = _receipt.get("session_state", "UNKNOWN")
    try:
        from session import session_context as _session_context
        _session = _session_context(getattr(snapshot, "timestamp", None))
        _session_state = (
            f"{_session.get('session', 'UNKNOWN')} | "
            f"market_open={_session.get('market_open', 'UNKNOWN')} | "
            f"budapest_trading={_session.get('budapest_trading', 'UNKNOWN')}")
    except Exception:
        pass
    row = {
        # 24k: tz-aware UTC. A bare "2026-09-24T17:14:29" means nothing on its own -
        # every reader had to guess a timezone. "+00:00" removes the guess forever.
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "symbol": getattr(snapshot, "symbol", config.SYMBOL),
        "price": getattr(snapshot, "price", 0.0),
        "signal_direction": getattr(snapshot, "signal_direction", ""),
        "signal_strength": getattr(snapshot, "signal_strength", 0.0),
        "signal_confidence": getattr(snapshot, "confidence", 0.0),
        "regime": getattr(snapshot, "regime", ""),
        "divergence": getattr(snapshot, "divergence", 0.0),
        "ai_action": getattr(decision, "action", ""),
        "ai_confidence": getattr(decision, "confidence", 0.0),
        "ai_model": (getattr(decision, "model", "") or "unknown"),
        "decision_source": getattr(decision, "decision_source", "STEP3"),
        "power_direction": _power.get("direction", ""),
        "power_reason_code": _power.get("reason_code", ""),
        "session_state": _session_state,
        "execution_mode": getattr(config, "EXECUTION_MODE", "UNKNOWN"),
        "shooting_status": _receipt.get("status", "UNKNOWN"),
        "shooting_reason": _receipt.get("primary_reason", ""),
        "shooting_sub_checks": json.dumps(
            _receipt.get("sub_checks", []) or [], default=str,
            separators=(",", ":")),
        "paper_entry_status": _paper_entry.get("status", "UNKNOWN"),
        "exec_status": getattr(exec_result, "status", ""),
        "outcome": getattr(exec_result, "status", ""),
        "replay_status": "NOT_RUN_IN_LIVE_CYCLE",
        "order_id": getattr(exec_result, "order_id", ""),
        "news_state": getattr(getattr(snapshot, "news", None), "news_state", ""),
        "minutes_to_event": getattr(getattr(snapshot, "news", None),
                                    "minutes_to_next_event", ""),
        "next_event_title": getattr(getattr(snapshot, "news", None),
                                    "next_event_title", ""),
        "shadow_candidates": json.dumps(
            getattr(snapshot, "shadow_candidates", {}) or {},
            sort_keys=True, default=str, separators=(",", ":")),
        "ai_rationale": getattr(decision, "rationale", ""),
        "reason": getattr(exec_result, "reason", ""),
    }
    try:
        with open(path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_decision_fields, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerow(row)
        logger.info("Decision logged -> %s", path)
    except (OSError, csv.Error, ValueError) as exc:
        logger.warning("Could not write decision log: %s", exc)

    # 24k (A2): the day's own copy. decisions_log.csv is capped at
    # DECISIONS_LOG_MAX_ROWS (5000) and maintenance deletes the OLDEST rows - at ~850
    # decisions a day that silently erases the start of the history about every six
    # days, and a past-day audit would then honestly report "the robot logged nothing
    # that day". This mirror is per-day and never trimmed, so a graded day stays
    # gradeable forever. Same idea as diary_YYYYMMDD.jsonl, which has done this since 24f.
    try:
        _day_path = DATA_DIR / f"decisions_{datetime.now(timezone.utc):%Y%m%d}.csv"
        _day_fields, _day_header = _decision_csv_schema(_day_path)
        with open(_day_path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_day_fields, extrasaction="ignore")
            if _day_header:
                writer.writeheader()
            writer.writerow(row)
    except (OSError, csv.Error, ValueError) as exc:
        logger.warning("Could not write per-day decision mirror: %s", exc)


# --------------------------------------------------------------------------- #
# Pipeline steps
# --------------------------------------------------------------------------- #
def run_step1():
    from step1_data_acquisition import DataAcquisition
    # no symbol passed -> the provider uses its own DATA-side market (GC/MGC/...)
    data = DataAcquisition(source=config.DATA_SOURCE).acquire_market_data()
    _record("STEP 1  DATA ACQUISITION",
            f"OK ({config.DATA_SOURCE}, data={data.get('data_symbol', '?')} "
            f"-> trade={data.get('trade_symbol', config.MT5_SYMBOL)}"
            f", has_data={data.get('has_data', True)})")

    # optionally record live data for offline replay / weekend testing
    if config.RECORD_DATA and data.get("has_data"):
        try:
            from data_providers import record_market_data
            record_market_data(data)
        except Exception as exc:
            logger.warning("recording failed: %s", exc)
    return data


def _record_power_line(snapshot) -> None:
    """fusionv1 (visibility pack): one console line per cycle - POWER's own state.

    Shows timeframe, direction, activity, dominance and reason every cycle so
    the new design is observable live, not only in the night diary.
    """
    try:
        _pw = getattr(snapshot, "power", None) or {}
        if _pw:
            _record("STEP 2  POWER",
                    "{} {} act {:.2f} dom {:.0f}% ({})".format(
                        str(_pw.get("timeframe", "?")),
                        str(_pw.get("direction", "?")),
                        float(_pw.get("activity", 0.0) or 0.0),
                        max(float(_pw.get("up_power_pct", 0.0) or 0.0),
                            float(_pw.get("down_power_pct", 0.0) or 0.0)),
                        str(_pw.get("reason_code", ""))))
    except Exception:
        pass


def run_step2(data):
    from step2_market_analysis import (analyze_market, format_snapshot,
                                       snapshot_to_json)
    import json as _json
    import re as _re
    snapshot = analyze_market(data)
    formatted = format_snapshot(snapshot)
    print(formatted)
    out = DATA_DIR / "market_snapshot.json"
    out.write_text(snapshot_to_json(snapshot))
    logger.info("STEP 2: snapshot saved -> %s", out)
    # v4.2: save history for team/judge audit (hourly, KillZone, regime)
    try:
        hist_path = DATA_DIR / "snapshots_history.jsonl"
        # Extract team scores from notes if present
        team_scores = {}
        notes_text = " ".join(getattr(snapshot, "notes", []) or [])
        m_teams = _re.search(r"4 Teams ensemble:\s*flow\s*([-\d\.]+)\*[\d\.]+\s*whale\s*([-\d\.]+)\*[\d\.]+\s*struct\s*([-\d\.]+)\*[\d\.]+\s*trend\s*([-\d\.]+)\*[\d\.]+", notes_text)
        if m_teams:
            team_scores = {"flow": float(m_teams.group(1)), "whale": float(m_teams.group(2)), "struct": float(m_teams.group(3)), "trend": float(m_teams.group(4))}
        # KillZone
        killzone = "UNKNOWN"
        if "KillZone OFF" in notes_text: killzone = "OFF_LUNCH"
        elif "KillZone NY" in notes_text: killzone = "NY"
        elif "LONDON+NEW_YORK" in notes_text: killzone = "OVERLAP"
        elif "LONDON" in notes_text: killzone = "LONDON"
        record = {
            "timestamp": getattr(snapshot, "timestamp", None).isoformat() if hasattr(getattr(snapshot, "timestamp", None), "isoformat") else str(getattr(snapshot, "timestamp", "")),
            "price": float(getattr(snapshot, "price", 0) or 0),
            "signal_direction": getattr(snapshot, "signal_direction", "NEUTRAL"),
            "signal_strength": float(getattr(snapshot, "signal_strength", 0) or 0),
            "confidence": float(getattr(snapshot, "confidence", 0) or 0),
            "regime": getattr(snapshot, "regime", "UNKNOWN"),
            "team_scores": team_scores,
            "killzone": killzone,
            # v7.1: the whole judge panel, so every "who voted what" question can
            # be answered after the day. 200 notes (not 50) so no judge is cut off.
            # v7.1: per-judge panel. Works with EITHER a patched step2 (the snapshot
            # already carries judge_votes) OR the plain P3 step2 (this module derives
            # the panel from the notes). No judge_panel.py -> field becomes [].
            "judge_votes": (list(getattr(snapshot, "judge_votes", []) or [])
                            or _judge_panel_from_notes(getattr(snapshot, "notes", []) or [])),
            # 26o: the SIGNAL team's map, stored as structured data rather than a
            # sentence. signal_map.py had to parse note strings to find wall prices;
            # from here the prices, sizes, distances and trust scores are first-class.
            "signal_map": (getattr(snapshot, "signal_map", None) or {}),
            # Canonical POWER-v2 result. Force shares are not probabilities.
            "power": (getattr(snapshot, "power", None) or {}),
            # Backward-compatible alias; contains the same v2 result.
            "power_v2": (getattr(snapshot, "power_v2", None) or {}),
            # Deprecated schema key kept empty for old readers; legacy Power is inactive.
            "power_legacy": {},
            # Shooting's pre-entry plan and separate paper fill simulation.
            "shot": (getattr(snapshot, "shot", None) or {}),
            "entry_simulation": (getattr(snapshot, "entry_simulation", None) or {}),
            # Escort only receives filled paper positions to manage.
            "escort": (getattr(snapshot, "escort", None) or {}),
            "notes": _notes_for_diary(getattr(snapshot, "notes", []) or []),
        }
        _append_diary(record, hist_path)
        # opsfix:v1-start (2026-10-15 / 2026-10-27) -- byte-capped rotation.
        # The old rule kept the last 20k lines assuming ~1KB lines (~20MB, per
        # its own comment). Rich diary lines are now ~28KB (voice/A2/memory +
        # structured judge_votes + signal_map + power), so even a 2000-line floor
        # was ~56.2MB (>50MB trigger) and caused a 56MB rewrite every minute on
        # 2026-10-27. New floor: 500 lines (~14MB, ~8h), UTF-8 byte-measured so
        # rotation always lands well below 40MB; per-day mirrors (diary_YYYYMMDD)
        # keep the complete daily history intact.
        try:
            if hist_path.stat().st_size > 50*1024*1024:
                lines = hist_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-20000:]
                while len(lines) > 500:
                    _nbytes = sum(len(_l.encode("utf-8", "ignore")) + 1 for _l in lines)
                    if _nbytes <= 38*1024*1024:
                        break
                    lines = lines[len(lines) // 2:]
                hist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                logger.info(f"Rotated snapshots_history.jsonl >50MB -> kept {len(lines)} lines "
                            f"(byte-capped, opsfix v1)")
        except:
            pass
        # opsfix:v1-end
    except Exception as he:
        try:
            logger.debug(f"snapshot history save failed: {he}")
        except:
            pass
    _record("STEP 2  MARKET ANALYSIS",
            f"OK -> {snapshot.signal_direction} "
            f"(strength {snapshot.signal_strength:.1f}, "
            f"confidence {snapshot.confidence:.1f})")
    _record_power_line(snapshot)  # fusionv1 visibility pack
    return snapshot


_LAST_AI_CALL = 0.0   # timestamp of the last Gemini call (throttling)
_AI_STATE_FILE = None


def _ai_state_path():
    if _AI_STATE_FILE is not None:
        return _AI_STATE_FILE
    live_data_dir = getattr(config, "DATA_DIR", DATA_DIR)
    return Path(live_data_dir) / "ai_calls.json"


def _local_day_key() -> str:
    """24m/C3 sibling: AI_MAX_CALLS_PER_DAY must reset on the same day boundary the
    report and the trade budget use - the operator's local date, not a naive machine
    date and not UTC (which rolls over at 02:00 Budapest, mid-night-session)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(timezone.utc).astimezone(
            ZoneInfo(str(getattr(config, "LOCAL_TZ", "Europe/Budapest")))).date().isoformat()
    except Exception:
        return datetime.now().date().isoformat()


def _ai_calls_today() -> int:
    """How many Gemini calls we have already made today (persisted)."""
    import json
    path = _ai_state_path()
    try:
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
            if state.get("date") == _local_day_key():
                return int(state.get("calls", 0) or 0)
    except (json.JSONDecodeError, OSError):
        pass
    return 0


def _record_ai_call() -> None:
    """Persist one more Gemini call for today (resets at midnight)."""
    import json
    try:
        _ai_state_path().write_text(
            json.dumps({"date": _local_day_key(),
                        "calls": _ai_calls_today() + 1}),
            encoding="utf-8")
    except OSError:
        pass


import threading as _threading

_STANDING_AI_SLIP = None
_AI_BG_LOCK = _threading.Lock()
_AI_BG_STATE = {
    "in_flight": False,
    "result": None,
    "finished_epoch": 0.0,
    "snapshot_meta": None,
}
_ASYNC_LOOP_ACTIVE = False
_LAST_PIPELINE_CONTEXT = {}


def set_async_loop_mode(enabled: bool) -> None:
    """Enable off-hot-path async Gemini refresh during continuous --loop mode."""
    global _ASYNC_LOOP_ACTIVE
    _ASYNC_LOOP_ACTIVE = bool(enabled)


def _snapshot_atr(snapshot) -> float:
    """Read ATR from canonical snapshot.volatility.atr (or synthetic indicators fallback)."""
    vol = getattr(snapshot, "volatility", None)
    if vol is not None:
        v = float(getattr(vol, "atr", 0.0) or 0.0)
        if v > 0.0:
            return v
    ind = getattr(snapshot, "indicators", None)
    if ind is not None:
        v = float(getattr(ind, "atr_14", 0.0) or getattr(ind, "atr", 0.0) or 0.0)
        if v > 0.0:
            return v
    return 0.0


def _extract_slip_meta(snapshot) -> dict:
    pw = getattr(snapshot, "power", None) or {}
    return {
        "regime": str(getattr(snapshot, "regime", "") or "").upper(),
        "power_dir": str(pw.get("direction", "") or "").upper(),
        "price": float(getattr(snapshot, "price", 0.0) or 0.0),
        "atr": _snapshot_atr(snapshot),
    }


def _is_paid_ai_model_tag(m_tag: str) -> bool:
    return bool(
        m_tag
        and not m_tag.startswith("none-")
        and not m_tag.endswith("-cached")
        and not m_tag.startswith("fallback-")
        and not m_tag.startswith("power-lead")
    )


def _store_standing_ai_slip(decision, meta: dict, issued_epoch: float) -> None:
    """Persist or clear the in-memory Standing AI Permission Slip."""
    global _STANDING_AI_SLIP
    m_tag = str(getattr(decision, "model", "") or "")
    if not _is_paid_ai_model_tag(m_tag):
        return
    act = str(getattr(decision, "action", "HOLD") or "HOLD").upper()
    conf = float(getattr(decision, "confidence", 0.0) or 0.0)
    if act in ("BUY", "SELL") and conf > 0.0:
        _STANDING_AI_SLIP = {
            "action": act,
            "confidence": conf,
            "rationale": str(getattr(decision, "rationale", "") or ""),
            "model": m_tag,
            "issued_epoch": float(issued_epoch),
            "regime": meta.get("regime", ""),
            "power_dir": meta.get("power_dir", ""),
            "price": float(meta.get("price", 0.0) or 0.0),
            "atr": float(meta.get("atr", 0.0) or 0.0),
        }
    else:
        _STANDING_AI_SLIP = None


def _evaluate_standing_ai_slip(snapshot, now: float, min_interval: float,
                               effective_interval: float):
    """Return a cached standing Decision if still valid for live tape, else None."""
    if not bool(getattr(config, "AI_STANDING_PERMISSION_ENABLED", True)):
        return None
    slip = _STANDING_AI_SLIP
    if not isinstance(slip, dict) or _LAST_AI_CALL <= 0.0:
        return None
    issued = float(slip.get("issued_epoch", 0.0) or 0.0)
    if abs(issued - _LAST_AI_CALL) > 1.0:
        return None
    age = now - issued
    if age < 0.0 or age >= effective_interval:
        return None
    act = str(slip.get("action", "") or "").upper()
    if act not in ("BUY", "SELL"):
        return None
    # 1) News blackout immediately voids standing permission
    news_obj = getattr(snapshot, "news", None)
    news_state = str(
        (news_obj.get("news_state") if isinstance(news_obj, dict)
         else getattr(news_obj, "news_state", "QUIET")) or "QUIET"
    ).upper()
    if news_state == "BLACKOUT":
        return None
    # 2) Regime flip check
    live_reg = str(getattr(snapshot, "regime", "") or "").upper()
    slip_reg = str(slip.get("regime", "") or "").upper()
    if (slip_reg in ("TREND", "RANGE")
            and live_reg in ("TREND", "RANGE", "VOLATILE", "HIGH_VOL")
            and live_reg != slip_reg):
        return None
    # 3) POWER v2 opposing flip check
    pw = getattr(snapshot, "power", None) or {}
    live_pdir = str(pw.get("direction", "") or "").upper()
    if (act == "BUY" and live_pdir == "DOWN") or (act == "SELL" and live_pdir == "UP"):
        return None
    # 4) Price excursion check (<= 2.0 * ATR or 0.3% fallback)
    slip_px = float(slip.get("price", 0.0) or 0.0)
    live_px = float(getattr(snapshot, "price", 0.0) or 0.0)
    live_atr = _snapshot_atr(snapshot)
    ref_atr = live_atr if live_atr > 0.0 else float(slip.get("atr", 0.0) or 0.0)
    if slip_px > 0.0 and live_px > 0.0:
        max_exc = (2.0 * ref_atr) if ref_atr > 0.0 else (slip_px * 0.003)
        if abs(live_px - slip_px) > max_exc:
            return None
    # 5) Live L3 flow / depth-normalized OFI opposing sweep check
    try:
        from step3_ai_decision import compute_ofi_conflict_threshold
        l3 = getattr(snapshot, "level3", None)
        if l3 is not None:
            net_flow = (float(getattr(l3, "aggressive_buy_volume", 0.0) or 0.0)
                        - float(getattr(l3, "aggressive_sell_volume", 0.0) or 0.0))
            ofi_l3 = float(getattr(l3, "ofi", 0.0) or 0.0)
            ofi_thr = compute_ofi_conflict_threshold(snapshot, config)
            if act == "BUY" and (net_flow < -50.0 or ofi_l3 < -ofi_thr):
                return None
            if act == "SELL" and (net_flow > 50.0 or ofi_l3 > ofi_thr):
                return None
    except Exception:
        pass

    from step3_ai_decision import Decision
    dec = Decision(
        action=act,
        confidence=float(slip.get("confidence", 0.0) or 0.0),
        rationale=f"standing AI slip ({age:.0f}s/{min_interval:.0f}s): {slip.get('rationale', '')}",
        model=f"{slip.get('model', 'gemini')}-cached",
    )
    dec.decision_source = "AI_STANDING_PERMISSION"
    return dec


def _harvest_async_ai_result(now: float, min_interval: float):
    """Harvest a completed background Gemini refresh if one finished."""
    global _LAST_AI_CALL
    with _AI_BG_LOCK:
        dec = _AI_BG_STATE.get("result")
        if dec is None:
            return None
        fin_epoch = float(_AI_BG_STATE.get("finished_epoch", now) or now)
        meta = dict(_AI_BG_STATE.get("snapshot_meta") or {})
        _AI_BG_STATE["result"] = None
        _AI_BG_STATE["finished_epoch"] = 0.0
        _AI_BG_STATE["snapshot_meta"] = None
    m_tag = str(getattr(dec, "model", "") or "")
    if _is_paid_ai_model_tag(m_tag):
        _LAST_AI_CALL = fin_epoch
        _record_ai_call()
        _store_standing_ai_slip(dec, meta, fin_epoch)
    elif m_tag in ("none-timeout-budget", "none-call-failed", "none-model-overloaded"):
        if min_interval > 60.0:
            _LAST_AI_CALL = fin_epoch - (min_interval - 60.0)
        else:
            _LAST_AI_CALL = fin_epoch
    return dec


def _dispatch_async_ai_refresh(snapshot) -> bool:
    """Launch a single off-hot-path background Gemini refresh if none is running."""
    with _AI_BG_LOCK:
        if _AI_BG_STATE.get("in_flight"):
            return False
        _AI_BG_STATE["in_flight"] = True
    meta = _extract_slip_meta(snapshot)

    def _bg_worker():
        import time as _t
        from step3_ai_decision import AIDecisionEngine, Decision
        try:
            res = AIDecisionEngine().decide(snapshot)
        except Exception as exc:
            res = Decision(
                action="HOLD", confidence=0.0,
                rationale=f"async AI error: {type(exc).__name__}",
                model="none-call-failed",
            )
        fin = _t.time()
        with _AI_BG_LOCK:
            _AI_BG_STATE["result"] = res
            _AI_BG_STATE["finished_epoch"] = fin
            _AI_BG_STATE["snapshot_meta"] = meta
            _AI_BG_STATE["in_flight"] = False
            _AI_BG_STATE["thread"] = None

    t = _threading.Thread(target=_bg_worker, name="step3-ai-async", daemon=True)
    with _AI_BG_LOCK:
        _AI_BG_STATE["thread"] = t
    t.start()
    return True


def _snapshot_has_live_go_shot(snapshot) -> bool:
    """True when Step 2 already built an executable Shooting=GO plan on this cycle."""
    shot = getattr(snapshot, "shot", None)
    if not isinstance(shot, dict):
        return False
    return (
        str(shot.get("shot", "") or "").upper() == "GO"
        and str(shot.get("side", "") or "").upper() in ("BUY", "SELL")
        and str(shot.get("entry_style", "") or "").upper()
        in ("PASSIVE_LIMIT", "AGGRESSIVE_MARKET")
    )


def run_step3(snapshot):
    """Consult the AI — off hot path in --loop mode, with Standing Permission Slips.

    The free Gemini tier allows ~20 requests/day. Calling every 60 seconds
    would burn the whole quota in ~20 minutes. So the AI is consulted only
    when (a) the Step-2 signal is strong enough, (b) enough time has passed
    since the last call, and (c) we have not hit the daily call cap.
    """
    from step3_ai_decision import AIDecisionEngine, Decision
    import time as _time
    global _LAST_AI_CALL

    strength = float(getattr(snapshot, "signal_strength", 0.0) or 0.0)
    min_strength = config.AI_MIN_SIGNAL_STRENGTH
    min_interval = config.AI_MIN_INTERVAL_MINUTES * 60.0
    max_per_day = config.AI_MAX_CALLS_PER_DAY
    now = _time.time()
    effective_interval = (min_interval - 5.0) if min_interval >= 60.0 else min_interval
    live_go = _snapshot_has_live_go_shot(snapshot)

    # Harvest any completed background AI call first so quota & slips stay exact.
    harvested = _harvest_async_ai_result(now, min_interval)
    if harvested is not None:
        h_tag = str(getattr(harvested, "model", "") or "")
        if _is_paid_ai_model_tag(h_tag):
            # Re-verify the just-harvested directional slip against current snapshot;
            # if still valid, emit the real model tag once for audit visibility.
            h_act = str(getattr(harvested, "action", "HOLD") or "HOLD").upper()
            if h_act in ("BUY", "SELL"):
                checked = _evaluate_standing_ai_slip(
                    snapshot, now, min_interval, max(effective_interval, 60.0))
                if checked is None:
                    harvested = Decision(
                        action="HOLD", confidence=0.0,
                        rationale="async AI slip invalidated by tape change before apply",
                        model=h_tag)
            _rat_txt = str(getattr(harvested, "rationale", "") or "")[-110:]
            if " " in _rat_txt:
                _rat_txt = _rat_txt[_rat_txt.find(" ") + 1:]
            _record("STEP 3  AI DECISION",
                    f"{harvested.action} @ {harvested.confidence:.1f}%"
                    + (f" | {_rat_txt}" if _rat_txt.strip() else ""))
            return harvested

    if strength < min_strength:
        decision = Decision(
            action="HOLD", confidence=0.0,
            rationale=f"signal {strength:.1f} below AI threshold "
                      f"{min_strength:.0f} (AI skipped)",
            model="none-weak-signal")
        _record("STEP 3  AI DECISION",
                f"HOLD (signal {strength:.1f} < {min_strength:.0f}, AI skipped)")
        return decision

    if now - _LAST_AI_CALL < effective_interval:
        standing = _evaluate_standing_ai_slip(
            snapshot, now, min_interval, effective_interval)
        if standing is not None:
            logger.info("STEP 3: Using cached AI decision (%s -> %s @ %.1f%%)",
                        standing.model, standing.action, standing.confidence)
            _record("STEP 3  AI DECISION",
                    f"{standing.action} @ {standing.confidence:.1f}% "
                    f"(standing slip, {standing.model})")
            return standing
        # When an executable Shooting=GO plan fires mid-window with no valid
        # standing slip (e.g. an A2 burst or fresh POWER breakout right after an
        # earlier NEITHER->HOLD call), re-consult AI instead of dropping GO behind
        # none-throttled, subject to a 15s anti-burst floor.
        min_go_reask_s = float(getattr(config, "AI_GO_REASK_MIN_SECONDS", 15.0) or 15.0)
        if not (live_go and (now - _LAST_AI_CALL) >= max(0.0, min_go_reask_s)):
            wait = max(0.0, (min_interval - (now - _LAST_AI_CALL)) / 60.0)
            decision = Decision(
                action="HOLD", confidence=0.0,
                rationale=f"AI throttled ({wait:.0f} min until next call)",
                model="none-throttled")
            _record("STEP 3  AI DECISION",
                    f"HOLD (AI throttled, {wait:.0f} min until next call)")
            return decision

    if max_per_day > 0 and _ai_calls_today() >= max_per_day:
        decision = Decision(
            action="HOLD", confidence=0.0,
            rationale=f"daily AI cap reached ({max_per_day})",
            model="none-daily-cap")
        _record("STEP 3  AI DECISION",
                f"HOLD (daily AI cap {max_per_day} reached)")
        return decision

    if _ASYNC_LOOP_ACTIVE and bool(getattr(config, "AI_ASYNC_REFRESH_ENABLED", True)):
        if not live_go:
            launched = _dispatch_async_ai_refresh(snapshot)
            note = "AI async refresh dispatched (off hot path)" if launched else "AI async refresh in flight"
            decision = Decision(
                action="HOLD", confidence=0.0,
                rationale=note,
                model="none-async-pending")
            _record("STEP 3  AI DECISION", f"HOLD ({note})")
            return decision
        # Executable Shooting=GO cycle with cold slip: if an async worker is
        # already in flight, join it briefly so we do not waste A2's 5m cooldown
        # on none-async-pending; otherwise run synchronous bounded decide() below.
        with _AI_BG_LOCK:
            bg_thread = _AI_BG_STATE.get("thread") if _AI_BG_STATE.get("in_flight") else None
        if bg_thread is not None:
            join_cap = max(1.0, float(getattr(config, "AI_GO_AWAIT_SECONDS", 12.0) or 12.0))
            bg_thread.join(timeout=join_cap)
            joined = _harvest_async_ai_result(_time.time(), min_interval)
            if joined is not None:
                j_tag = str(getattr(joined, "model", "") or "")
                if _is_paid_ai_model_tag(j_tag):
                    j_act = str(getattr(joined, "action", "HOLD") or "HOLD").upper()
                    if j_act in ("BUY", "SELL"):
                        checked = _evaluate_standing_ai_slip(
                            snapshot, _time.time(), min_interval, max(effective_interval, 60.0))
                        if checked is None:
                            joined = Decision(
                                action="HOLD", confidence=0.0,
                                rationale="async AI slip invalidated by tape change before GO apply",
                                model=j_tag)
                    _rat_txt = str(getattr(joined, "rationale", "") or "")[-110:]
                    if " " in _rat_txt:
                        _rat_txt = _rat_txt[_rat_txt.find(" ") + 1:]
                    _record("STEP 3  AI DECISION",
                            f"{joined.action} @ {joined.confidence:.1f}%"
                            + (f" | {_rat_txt}" if _rat_txt.strip() else ""))
                    return joined
            note = "AI async refresh still in flight past GO await window"
            decision = Decision(action="HOLD", confidence=0.0, rationale=note, model="none-async-pending")
            _record("STEP 3  AI DECISION", f"HOLD ({note})")
            return decision

    meta = _extract_slip_meta(snapshot)
    decision = AIDecisionEngine().decide(snapshot)
    m_tag = str(getattr(decision, "model", "") or "")
    if _is_paid_ai_model_tag(m_tag):
        _LAST_AI_CALL = now
        _record_ai_call()
        _store_standing_ai_slip(decision, meta, now)
    elif m_tag in ("none-timeout-budget", "none-call-failed", "none-model-overloaded"):
        # Do not count failed attempts as paid calls and do not lock out a full
        # bar; pause at most ~60s so the loop stays responsive without hammering.
        if min_interval > 60.0:
            _LAST_AI_CALL = now - (min_interval - 60.0)
        else:
            _LAST_AI_CALL = now
    _rat_txt = str(getattr(decision, "rationale", "") or "")[-110:]
    if " " in _rat_txt:
        _rat_txt = _rat_txt[_rat_txt.find(" ") + 1:]      # opsfix:v3.2: no mid-word cuts
    _record("STEP 3  AI DECISION",
            f"{decision.action} @ {decision.confidence:.1f}%"
            + (f" | {_rat_txt}" if _rat_txt.strip() else ""))
    return decision


def _shooting_execution_gate(decision, snapshot):
    """Fail closed to owner-configured thresholds; shadow never authorizes orders.

    A Power-lead candidate below AI_CONFIDENCE_THRESHOLD is recorded for
    offline/paper review only. Executable routing still requires the configured
    threshold, a GO plan, canonical POWER agreement, and the existing Python
    executor's independent gates. EXECUTION_MODE ownership is unchanged.
    """
    action = str(getattr(decision, "action", "HOLD") or "HOLD").upper()
    if action in {"HOLD", "WAIT", "NONE", "NO_TRADE", "NEUTRAL"}:
        # The prior sub-threshold extension is now shadow-only; configured-threshold
        # Power carry remains distinct from the non-executable candidate path below.
        try:
            _pw = getattr(snapshot, "power", None) or {}
            _pd = str(_pw.get("direction", "NEITHER")).upper()
            _rc = str(_pw.get("reason_code", _pw.get("reason", "")) or "").upper()
            _dom = {"UP": "UPWARD_FORCE_DOMINATES",
                    "DOWN": "DOWNWARD_FORCE_DOMINATES"}.get(_pd)
            if _dom and _rc == _dom:
                import math as _math
                def _safe_percent(value, fallback):
                    if isinstance(value, bool):
                        return fallback
                    try:
                        parsed = float(value)
                        return parsed if _math.isfinite(parsed) else fallback
                    except (TypeError, ValueError, OverflowError):
                        return fallback

                _pc = _safe_percent(
                    getattr(snapshot, "confidence", 0.0), float("nan"))
                _old_gate = _safe_percent(
                    getattr(config, "AI_CONFIDENCE_THRESHOLD", 50.0), float("nan"))
                _floor = _safe_percent(
                    getattr(config, "POWER_LEAD_CONF_FLOOR", 30.0), float("nan"))
                _live_threshold_valid = (
                    _math.isfinite(_old_gate) and 0.0 <= _old_gate <= 100.0)
                _shadow_floor_valid = _math.isfinite(_floor) and 0.0 <= _floor <= 100.0
                # Invalid thresholds fail closed rather than silently lowering a gate.
                _old_gate = _old_gate if _live_threshold_valid else 100.0
                _floor = _floor if _shadow_floor_valid else 100.0
                _power_confidence_valid = _math.isfinite(_pc) and 0.0 <= _pc <= 100.0
                def _as_flag(value, default=True):
                    if value is None:
                        return default
                    if isinstance(value, str):
                        return value.strip().lower() in {"1", "true", "yes", "on"}
                    return bool(value)

                _legacy_lead_on = _as_flag(
                    getattr(config, "POWER_LEAD_ENABLED", True))
                _shadow_on = _as_flag(
                    getattr(config, "POWER_LEAD_SHADOW_ENABLED", True))
                _cdir = str(getattr(snapshot, "signal_direction", "") or "").upper()
                _expected_action = "BUY" if _pd == "UP" else "SELL"
                _agree = _cdir not in ("BUY", "SELL") or _cdir == _expected_action
                _passed = (_power_confidence_valid and _live_threshold_valid
                           and _pc >= _old_gate)

                if _passed:
                    # Preserve the pre-existing configured-threshold carry path;
                    # only the former sub-threshold extension becomes shadow-only.
                    try:
                        decision.decision_source = "POWER_V2_CONFIGURED_THRESHOLD_CARRY"
                    except Exception:
                        pass
                    decision.rationale = (
                        (getattr(decision, "rationale", "") or "AI quiet")
                        + " | POWER carry at configured confidence threshold: "
                        "conf {:.1f} >= {:.1f}; POWER {} ({})".format(
                            _pc, _old_gate, _pd, _rc))
                    decision.confidence = _pc
                    # Commit the action last. If an earlier receipt assignment
                    # fails, the original HOLD remains untouched (fail closed).
                    try:
                        decision.action = _expected_action
                    except Exception:
                        return False, "Power carry action assignment failed; fail closed"
                    try:
                        _record("STEP 3  POWER-LEAD",
                                "configured-threshold carry: conf {:.1f} >= {:.1f} "
                                "| POWER {} | {}".format(
                                    _pc, _old_gate, _pd, _rc))
                    except Exception:
                        pass
                    action = _expected_action
                elif (_power_confidence_valid and _live_threshold_valid
                      and _shadow_floor_valid and _pc >= _floor):
                    _plan = getattr(snapshot, "shot", None) or {}
                    _plan = _plan if isinstance(_plan, dict) else {}
                    _plan_side = str(_plan.get("side", "")).upper()
                    _plan_shot = str(_plan.get("shot", "")).upper()
                    _plan_style = str(_plan.get("entry_style", "")).upper()
                    _plan_visibility = _plan.get("visibility") or {}
                    _plan_visibility = (_plan_visibility
                                        if isinstance(_plan_visibility, dict) else {})
                    _plan_reason = str(
                        _plan_visibility.get("primary_reason") or
                        _plan.get("reason") or _plan.get("why") or "")
                    _plan_shadows = _plan.get("shadow_candidates") or {}
                    _passive_shadow = (_plan_shadows.get("passive_limit")
                                       if isinstance(_plan_shadows, dict) else None)
                    _passive_shadow_ok = (
                        isinstance(_passive_shadow, dict) and
                        _passive_shadow.get("candidate") is True and
                        _passive_shadow.get("candidate_eligible") is True and
                        _passive_shadow.get("execution_enabled") is False)
                    _plan_go_ok = (
                        _plan_shot == "GO" and _plan_side == _expected_action and
                        _plan_style in {"PASSIVE_LIMIT", "AGGRESSIVE_MARKET"})
                    # A WAIT caused solely by the disabled passive policy can
                    # still contribute a *shadow* route candidate when its
                    # own geometry/queue checks passed. It never becomes GO.
                    _plan_shadow_ok = (_plan_shot == "WAIT" and _passive_shadow_ok)
                    _plan_ok = _plan_go_ok or _plan_shadow_ok
                    _blockers = []
                    if not _shadow_on:
                        _blockers.append("POWER_LEAD_SHADOW_DISABLED")
                    if not _agree:
                        _blockers.append("SIGNAL_DIRECTION_DISAGREES_WITH_POWER")
                    if not _plan_ok:
                        _blockers.append("SHOOTING_PLAN_NOT_GO_OR_SHADOW_ROUTE_INELIGIBLE")
                    _eligible = not _blockers
                    _candidate = {
                        "kind": "POWER_LEAD",
                        "status": ("ELIGIBLE_SHADOW_CANDIDATE" if _eligible
                                   else "SHADOW_REJECTED"),
                        "shadow_only": True,
                        "execution_enabled": False,
                        "eligible": _eligible,
                        "candidate_reason": (
                            ("meets shadow floor and agreement checks; below the owner-configured "
                             "live confidence threshold; passive policy route remains disabled"
                             if _plan_shadow_ok else
                             "meets shadow floor and current plan/agreement checks; "
                             "below owner-configured live confidence threshold")
                            if _eligible else ";".join(_blockers)),
                        "blockers": _blockers,
                        "power_direction": _pd,
                        "power_reason_code": _rc,
                        "signal_direction": _cdir or "UNKNOWN",
                        "signal_confidence": _pc,
                        "shadow_confidence_floor": _floor,
                        "live_confidence_threshold": _old_gate,
                        "legacy_power_lead_enabled": _legacy_lead_on,
                        "shadow_recording_enabled": _shadow_on,
                        "agreement": _agree,
                        "plan_status": _plan_shot or "UNKNOWN",
                        "plan_side": _plan_side or "UNKNOWN",
                        "plan_entry_style": _plan_style or "UNKNOWN",
                        "plan_shadow_only": _plan_shadow_ok,
                        "passive_route_candidate": dict(_passive_shadow)
                        if _passive_shadow_ok else None,
                        "plan_reason": _plan_reason,
                        "market_quality_evidence": dict(
                            getattr(snapshot, "quality_evidence", {}) or {}),
                        "execution_outcome": ("BLOCKED_SHADOW_ONLY" if _eligible
                                              else "NOT_AUTHORIZED_SHADOW_REJECTED"),
                    }
                    if _shadow_on:
                        try:
                            _shadow = getattr(snapshot, "shadow_candidates", None)
                            if not isinstance(_shadow, dict):
                                _shadow = {}
                                setattr(snapshot, "shadow_candidates", _shadow)
                            _shadow["power_lead"] = _candidate
                            if isinstance(_plan, dict):
                                _plan_shadow = _plan.setdefault("shadow_candidates", {})
                                if isinstance(_plan_shadow, dict):
                                    _plan_shadow["power_lead"] = _candidate
                            _record(
                                "STEP 3  POWER-LEAD SHADOW",
                                f"{_candidate['status']} | signal_conf={_pc:.1f} "
                                f"floor={_floor:.1f} live_threshold={_old_gate:.1f} "
                                f"POWER={_pd} reason={_rc} agreement={_agree} "
                                f"plan={_plan_shot}/{_plan_side} "
                                f"blockers={','.join(_blockers) or 'none'}; not executable")
                        except Exception:
                            pass
                    else:
                        try:
                            _record(
                                "STEP 3  POWER-LEAD",
                                f"shadow recording disabled; no candidate stored | "
                                f"signal_conf={_pc:.1f} floor={_floor:.1f} "
                                f"live_threshold={_old_gate:.1f} POWER={_pd} reason={_rc}")
                        except Exception:
                            pass
                    if _eligible:
                        # Do not let a shadow candidate reach the executor, even
                        # as HOLD; expose a distinct fail-closed gate result.
                        return False, (
                            "POWER-LEAD shadow candidate only; confidence "
                            f"{_pc:.1f} is below owner-configured threshold "
                            f"{_old_gate:.1f}")
                else:
                    if not _power_confidence_valid:
                        _hold_reason = f"invalid signal confidence {_pc!r}; expected 0..100"
                    elif not _live_threshold_valid:
                        _hold_reason = "owner confidence threshold invalid; fail closed"
                    elif not _shadow_floor_valid:
                        _hold_reason = "shadow confidence floor invalid; no candidate recorded"
                    else:
                        _hold_reason = ("holds below shadow floor: signal_conf={:.1f} "
                                        "floor={:.1f}".format(_pc, _floor))
                    try:
                        _record("STEP 3  POWER-LEAD", f"{_hold_reason} | POWER {_pd} | {_rc}")
                    except Exception:
                        pass
        except Exception:
            pass
        if action in {"HOLD", "WAIT", "NONE", "NO_TRADE", "NEUTRAL"}:
            return True, "no new-entry action"
    if action not in {"BUY", "SELL"}:
        return False, f"unrecognized Step-3 action {action!r}"
    if snapshot is None:
        return False, "no market snapshot"
    import math as _math
    _raw_threshold = getattr(config, "AI_CONFIDENCE_THRESHOLD", 50.0)
    _raw_confidence = getattr(decision, "confidence", float("nan"))
    if isinstance(_raw_threshold, bool) or isinstance(_raw_confidence, bool):
        return False, "decision confidence/owner threshold is invalid"
    try:
        _live_threshold = float(_raw_threshold)
        _live_confidence = float(_raw_confidence)
    except (TypeError, ValueError, OverflowError):
        return False, "decision confidence/owner threshold is invalid"
    if (not _math.isfinite(_live_threshold) or
            not 0.0 <= _live_threshold <= 100.0):
        return False, "owner confidence threshold is invalid; fail closed"
    if not _math.isfinite(_live_confidence):
        return False, "decision confidence is missing or invalid"
    if not 0.0 <= _live_confidence <= 100.0:
        return False, "decision confidence is outside the valid 0..100 range"
    if _live_confidence < _live_threshold:
        return False, (
            f"decision confidence {_live_confidence:.1f} is below owner-configured "
            f"threshold {_live_threshold:.1f}")
    plan = getattr(snapshot, "shot", None) or {}
    power = getattr(snapshot, "power", None) or {}
    if not isinstance(plan, dict):
        return False, "Shooting plan is malformed or missing"
    if not isinstance(power, dict):
        return False, "POWER v2 receipt is malformed or missing"
    expected_power = "UP" if action == "BUY" else "DOWN"
    if str(power.get("direction", "NEITHER")).upper() != expected_power:
        return False, "AI side does not agree with canonical POWER v2"
    if plan.get("shot") != "GO" or str(plan.get("side", "")).upper() != action:
        _why_list = plan.get("why") if isinstance(plan.get("why"), list) else []
        _joined_why = "; ".join(str(x).strip() for x in _why_list if str(x).strip())
        _sub_why = str(
            _joined_why
            if _joined_why
            else (plan.get("reason")
                  or ((plan.get("veto_details") or {}).get("reason")
                      if isinstance(plan.get("veto_details"), dict) else "")
                  or "")
        ).strip()
        if plan.get("shot") == "GO" and str(plan.get("side", "")).upper() != action:
            _sub_why = f"Shooting planned {plan.get('side')} while AI requested {action}"
        return False, (
            f"Shooting did not approve this side ({_sub_why})"
            if _sub_why else "Shooting did not approve this side"
        )
    if plan.get("entry_style") not in {"PASSIVE_LIMIT", "AGGRESSIVE_MARKET"}:
        return False, "Shooting entry style is missing or unsupported"
    # gates:v1: THE CORRIDOR (was: two unconditional refusals -- the deepest
    # reason 10 days had zero trades). Reviewed change, owner-ordered, demo first.
    if str(getattr(config, "EXECUTION_MODE", "none")).lower() != "python":
        return False, "corridor closed (EXECUTION_MODE != python)"
    return True, "corridor open: plan GO + POWER agree -> python executor"


def run_step4(decision, snapshot, *, execution_authorized: bool = False,
              authorization_reason: str = ""):
    """Route a new entry only after a non-overridable pipeline authorization.

    The explicit permit is separate from the Step-3 action: a HOLD can be
    promoted by the configured POWER carry, so a safety-generated HOLD must
    never be used as the only encoding of a failed hard gate. Direct callers
    fail closed unless they deliberately pass the permit.
    """
    from step4_mt5_execution import ExecutionResult, MT5Executor

    if execution_authorized is not True:
        reason = (str(authorization_reason or
                      "hard safety gate did not authorize a new entry"))
        result = ExecutionResult(
            status="SKIPPED",
            reason=f"new-entry authorization blocked: {reason}",
            symbol=config.MT5_SYMBOL,
            timestamp=datetime.now().astimezone())
        _record("STEP 4  EXECUTION", f"{result.status} — {result.reason}")
        return result

    plumbing_reason = _demo_order_test_pending_reason()
    if plumbing_reason:
        result = ExecutionResult(
            status="SKIPPED",
            reason=plumbing_reason,
            symbol=config.MT5_SYMBOL,
            timestamp=datetime.now().astimezone())
        _record("STEP 4  EXECUTION", f"{result.status} — {result.reason}")
        return result

    mode = getattr(config, "EXECUTION_MODE", "none")
    allowed, gate_reason = _shooting_execution_gate(decision, snapshot)
    if not allowed:
        result = ExecutionResult(
            status="SKIPPED",
            reason=f"Shooting/POWER v2 veto: {gate_reason}",
            symbol=config.MT5_SYMBOL,
            timestamp=datetime.now().astimezone())
    elif not config.TRADING_ENABLED:
        result = ExecutionResult(
            status="SKIPPED",
            reason="trading disabled (TRADING_ENABLED=0)",
            symbol=config.MT5_SYMBOL,
            timestamp=datetime.now().astimezone())
    elif mode == "ea":
        result = ExecutionResult(
            status="DEFERRED",
            reason="EA is the execution owner (signal bridge only)",
            symbol=config.MT5_SYMBOL,
            timestamp=datetime.now().astimezone())
    elif mode == "none":
        result = ExecutionResult(
            status="SKIPPED",
            reason="execution disabled (EXECUTION_MODE=none)",
            symbol=config.MT5_SYMBOL,
            timestamp=datetime.now().astimezone())
    elif mode == "python":
        # run_step4 only reaches this point after the caller's hard permit and
        # this independent strategy/execution gate both pass.
        result = _get_mt5_executor(MT5Executor).execute(
            decision, snapshot, execution_authorized=True)
    else:
        # config.py normalises invalid values to none, but keep this defensive
        # fallback in case a caller changes the module value directly.
        result = ExecutionResult(
            status="SKIPPED",
            reason=f"unknown execution mode: {mode}",
            symbol=config.MT5_SYMBOL,
            timestamp=datetime.now().astimezone())

    _record("STEP 4  EXECUTION", f"{result.status} — {result.reason}"
            if result.reason else f"{result.status}")
    return result


_CACHED_MT5_EXECUTOR = None


def _get_mt5_executor(executor_cls):
    """Reuse one MT5Executor instance across live loop cycles to retain server-clock offset."""
    global _CACHED_MT5_EXECUTOR
    cfg_magic = getattr(config, "MT5_MAGIC", 234000)
    if (
        _CACHED_MT5_EXECUTOR is None
        or type(_CACHED_MT5_EXECUTOR) is not executor_cls
        or getattr(_CACHED_MT5_EXECUTOR, "symbol", None) != config.MT5_SYMBOL
        or getattr(_CACHED_MT5_EXECUTOR, "magic", 234000) != cfg_magic
    ):
        _CACHED_MT5_EXECUTOR = executor_cls()
    return _CACHED_MT5_EXECUTOR
    from step5_monitoring import TradeMonitor
def run_step5():
    from step5_monitoring import TradeMonitor
    positions = TradeMonitor().single_pass()
    _record("STEP 5  MONITORING", "OK")
    return positions


def run_signal_bridge(snapshot, decision, *, allow_new_entries: bool = True) -> None:
    """Write a neutral or approved MT5 signal; blocked modes clear old signals."""
    from mt5_signal_bridge import write_signal
    from step3_ai_decision import Decision

    mode = getattr(config, "EXECUTION_MODE", "none")
    if allow_new_entries is not True:
        allowed = False
        gate_reason = "new entries are disabled by the selected run mode"
        decision = Decision(
            action="HOLD", confidence=0.0,
            rationale=f"signal neutralised: {gate_reason}")
    else:
        allowed, gate_reason = _shooting_execution_gate(decision, snapshot)
    action = str(getattr(decision, "action", "HOLD") or "HOLD").upper()
    if allow_new_entries is True and not allowed:
        decision = Decision(
            action="HOLD", confidence=0.0,
            rationale=f"signal neutralised by Shooting/POWER v2: {gate_reason}")
    elif allowed and mode != "ea":
        # Clear any old actionable EA signal when Python or no execution owns
        # the run. This prevents a previously attached EA from acting on stale
        # instructions after the mode is changed.
        decision = Decision(
            action="HOLD", confidence=0.0,
            rationale=f"signal neutralised (EXECUTION_MODE={mode})")

    path = write_signal(snapshot, decision)
    if path is not None:
        _record("MT5 SIGNAL BRIDGE", f"OK -> {path.name}")
    else:
        _record("MT5 SIGNAL BRIDGE", "SKIPPED (write failed)")

    # opsfix:v2 (2026-10-15) -- FAST LANE visibility (v2 wording) + THE JUDGES
    # LINE (owner's order: see the judges work each cycle). Read-only, fail-
    # closed, changes nothing. v2 fixes: no "memory=memory ..." duplication;
    # clear doorman wording right after a restart; NEW JUDGES summary line.
    try:
        import time as _fl_time
        _pv = getattr(snapshot, "power_v2", None) or {}
        _vg = _pv.get("voice_gain")
        _vg_s = f"{_vg:g}" if isinstance(_vg, (int, float)) else "n/a"
        _ms = _pv.get("memory_status") or {}
        if _ms.get("appended"):
            _ms_s = "appended"
        elif _ms.get("error"):
            _ms_s = f"error {_ms.get('error')}"
        else:
            _ms_r = str(_ms.get("reason") or "off").replace("memory ", "", 1)
            _ms_s = "idle" if _ms_r == "off" else _ms_r    # opsfix:v3.5-idle
        _pd = str(_pv.get("direction") or "n/a")
        _calls = _ai_calls_today()
        if _LAST_AI_CALL:
            _rest = (config.AI_MIN_INTERVAL_MINUTES * 60.0
                     - (_fl_time.time() - _LAST_AI_CALL)) / 60.0
            _dm_s = (f"resting {max(0.0, _rest):.0f} min" if _rest > 0
                     else "ready")
            _doorman = f"{_calls} paid call(s) today, {_dm_s}"
        else:
            _doorman = f"{_calls} in file today; this process has not called yet"
        _record("FAST LANE (opsfix)",
                f"POWER {_pd} voice_gain={_vg_s} memory={_ms_s} | "
                f"doorman: {_doorman}")
    except Exception as _fl_err:
        _record("FAST LANE (opsfix)",
                f"(status unavailable: {type(_fl_err).__name__})")
    # opsfix:v2-judges-start -- the judges, one line per cycle
    # (panel votes: {judge, dir +1 BUY / -1 SELL / 0 flat, weight})
    try:
        _jv = list(getattr(snapshot, "judge_votes", None) or [])
        # crowdv2:v1 -- item 62: whale_walls is RETIRED (C1 card: -1.012/call
        # over 6 days; step2 confidence has ignored it since v5.8.1).  Cut it
        # from the DISPLAY so the line stops advertising a retired judge.
        # (POWER's agreement gate reads its own timeframe family, not the
        # judges -- verified 2026-10-16, whale cannot reach it.)
        try:
            from judge_families import RETIRED as _RETIRED_JUDGES
        except Exception:
            _RETIRED_JUDGES = frozenset({"whale_walls", "macro_dxy", "htf_poc"})
        try:
            import config as _cfg_j
            _CFG_RET = frozenset(getattr(_cfg_j, "RETIRED_JUDGES", ()) or ())
        except Exception:
            _CFG_RET = frozenset()
        _ALL_RET = (
            frozenset(_RETIRED_JUDGES)
            | _CFG_RET
            | frozenset({"whale_walls", "macro_dxy", "htf_poc"})
        )
        _jv = [v for v in _jv
               if not isinstance(v, dict)
               or str(v.get("judge") or "") not in _ALL_RET]
        _jb = [v for v in _jv if isinstance(v, dict) and float(v.get("dir") or 0) > 0]
        _js = [v for v in _jv if isinstance(v, dict) and float(v.get("dir") or 0) < 0]
        _jf = len(_jv) - len(_jb) - len(_js)

        def _jtop(vs):
            vs = sorted(vs, key=lambda v: -float(v.get("weight") or 0.0))
            return ", ".join(f"{v.get('judge')} (w{float(v.get('weight') or 0.0):g})"
                             for v in vs[:2]) or "none"

        _record("JUDGES (opsfix2)",
                f"{len(_jb)} BUY / {len(_js)} SELL / {_jf} flat of {len(_jv)} | "
                f"top BUY: {_jtop(_jb)} | top SELL: {_jtop(_js)}")
    except Exception as _j_err:
        _record("JUDGES (opsfix2)", f"(status unavailable: {type(_j_err).__name__})")
    # opsfix:v2-judges-end
    # opsfix:v3-a2-start -- the fast lane's eyes, live (audit A1)
    try:
        import datetime as _v3_dt
        from aggressive_trigger import EYES as _v3_eyes, a2_fire_count as _v3_fires
        _v3_now = _v3_dt.datetime.now(_v3_dt.timezone.utc)
        _v3_max = int(getattr(config, "A2_BURST_MAX_PER_DAY", 10) or 10)
        # opsfix:v3.2-enabled: mirror the REAL gate (_enabled in aggressive_trigger):
        # os.environ FIRST (.env loads there via load_dotenv), then the config
        # attr (which config.py does not define for this key), same acceptance
        # set ("1", "1.0", "true"...). v1.1 read only the config attr and
        # wrongly showed enabled=False while the lane was ON.
        _v3_raw = None
        try:
            import os as _v3_os
            _v3_raw = _v3_os.environ.get("AGGRESSIVE_ENTRY_ENABLED")
        except Exception:
            pass
        if _v3_raw is None:
            _v3_raw = getattr(config, "AGGRESSIVE_ENTRY_ENABLED", 0)
        _v3_enabled = str(_v3_raw).strip() in ("1", "1.0", "true", "True", "TRUE")
        _v3_lean = None
        try:
            _v3_lmin_raw = None
            _v3_hold_raw = None
            try:
                import os as _v3_os2
                _v3_lmin_raw = _v3_os2.environ.get("A2_LEAN_MIN")
                _v3_hold_raw = _v3_os2.environ.get("A2_LEAN_HOLD_SECONDS")
            except Exception:
                pass
            _v3_lmin = float(_v3_lmin_raw if _v3_lmin_raw not in (None, "") else getattr(config, "A2_LEAN_MIN", 0.25) or 0.25)
            _v3_hold = float(_v3_hold_raw if _v3_hold_raw not in (None, "") else getattr(config, "A2_LEAN_HOLD_SECONDS", 60.0) or 60.0)
            _v3_lean = _v3_eyes.lean(_v3_now, min_abs=_v3_lmin, hold_s=_v3_hold)
        except Exception:
            _v3_lean = None
        if isinstance(_v3_lean, dict):
            _v3_ls = _v3_lean.get("strength", _v3_lean.get("value",
                        _v3_lean.get("ema3", 0.0)))
            try:
                _v3_lean_s = f"{_v3_lean.get('direction')} {float(_v3_ls):+.2f}"
            except Exception:
                _v3_lean_s = str(_v3_lean.get("direction"))
        else:
            _v3_lean_s = "none"
        _v3_panel_s = "none"
        try:
            if _v3_eyes.panel:
                _v3_t, _v3_d = _v3_eyes.panel[-1]
                _v3_panel_s = f"{_v3_d} ({(_v3_now - _v3_t).total_seconds():.0f}s ago)"
        except Exception:
            pass
        _v3_cool_s = "ready"
        try:
            if _v3_eyes.last_fire:
                _v3_cm = float(getattr(config, "A2_BURST_COOLDOWN_MINUTES", 5) or 5)
                _v3_age = (_v3_now - _v3_eyes.last_fire).total_seconds() / 60.0
                _v3_cool_s = (f"cooldown {max(0.0, _v3_cm - _v3_age):.0f} min"
                              if _v3_age < _v3_cm else "ready")
        except Exception:
            pass
        _record("A2 LANE (opsfix3)",
                f"enabled={_v3_enabled} fires {_v3_fires(_v3_now)}/{_v3_max} today | "
                f"eye1 lean: {_v3_lean_s} | eye2 panel: {_v3_panel_s} | {_v3_cool_s}")
    except Exception as _v3_err:
        _record("A2 LANE (opsfix3)", f"(status unavailable: {type(_v3_err).__name__})")
    # opsfix:v3-a2-end
    # opsfix:v3-armor-start -- the guards' armor + the bookshelves (B5-B8/C9/D10)
    try:
        import datetime as _v3_dt, json as _v3_json, shutil as _v3_shutil
        _v3_now = _v3_dt.datetime.now(_v3_dt.timezone.utc)
        _v3_dd = Path("data")
        _v3_tg = {}
        try:
            _v3_tg = _v3_json.loads((_v3_dd / "trade_guard_state.json")
                                    .read_text(encoding="utf-8"))
        except Exception:
            pass
        # opsfix:v3-armor-datefix: the state file keeps OLD days too - only
        # count trades when the file's date is TODAY (local), exactly like
        # trade_guard._today_state does; else a stale count shows as "today".
        _v3_trades = 0
        try:
            if _v3_tg.get("date") == _v3_dt.datetime.now().astimezone().date().isoformat():
                _v3_trades = int(_v3_tg.get("trades_today", 0) or 0)
        except Exception:
            pass
        _v3_cap = int(getattr(config, "MAX_TRADES_PER_DAY", 0) or 0)
        _v3_guard_s = f"guard: {_v3_trades} trades today"
        if _v3_cap:
            _v3_guard_s += f"/{_v3_cap}"
        _v3_risk_s = "risk: no halt"
        try:
            _v3_rs = _v3_json.loads((_v3_dd / "risk_state.json")
                                    .read_text(encoding="utf-8"))
            if _v3_rs.get("halt_until"):
                _v3_hu = None
                try:
                    _v3_hu = _v3_dt.datetime.fromisoformat(str(_v3_rs["halt_until"]))
                    if _v3_hu.tzinfo is None:
                        _v3_hu = _v3_hu.replace(tzinfo=_v3_dt.timezone.utc)
                except Exception:
                    _v3_hu = None
                # an EXPIRED halt reads as "no halt" (same as risk_manager.check);
                # an unparseable one still shows, so it is never hidden
                if _v3_hu is None or _v3_hu > _v3_now:
                    _v3_risk_s = (f"risk: HALT until {_v3_rs.get('halt_until')} "
                                  f"({_v3_rs.get('reason', '')})")
        except Exception:
            pass
        _v3_bid = float(getattr(snapshot, "bid", 0) or 0)
        _v3_ask = float(getattr(snapshot, "ask", 0) or 0)
        if _v3_ask > _v3_bid > 0:
            _v3_pts = _v3_ask - _v3_bid
            _v3_spread_s = f"spread: {_v3_pts:.2f} pts ({100.0 * _v3_pts / _v3_bid:.3f}%)"
        else:
            _v3_spread_s = "spread: n/a"
        _v3_flat = _v3_now.replace(hour=21, minute=30, second=0, microsecond=0)
        if _v3_flat < _v3_now:
            _v3_flat = _v3_flat + _v3_dt.timedelta(days=1)
        _v3_flat_s = f"flatten in {(_v3_flat - _v3_now).total_seconds() / 3600.0:.1f}h"
        try:
            _v3_free = _v3_shutil.disk_usage(".").free / (1024 ** 3)
            _v3_disk_s = f"disk free {_v3_free:.0f}GB"
        except Exception:
            _v3_disk_s = "disk n/a"
        # opsfix:v3.4-book: the book writes ONE file per DAY (power_YYYY-MM-DD
        # .jsonl); the pages are LINES inside it. v1.2 showed MB only, but a
        # day is ~14KB, so it read "0.0MB" all day (my flaw #5). KB + pages.
        _v3_mem_s = "book: none yet"
        try:
            _v3_mf = (_v3_dd / "power_v2_memory"
                      / f"power_{_v3_now.date().isoformat()}.jsonl")
            if _v3_mf.exists():
                _v3_sz = _v3_mf.stat().st_size
                _v3_pg = sum(1 for _ in _v3_mf.open("rb"))
                _v3_sz_s = (f"{_v3_sz / 1048576:.1f}MB" if _v3_sz >= 1048576
                            else f"{_v3_sz / 1024:.0f}KB")
                _v3_mem_s = f"book: today {_v3_sz_s}/{_v3_pg}p"
        except Exception:
            pass
        _v3_mtf = " ".join(f"{_k}:{_v}" for _k, _v in
                           sorted((getattr(snapshot, "mtf_trends", None) or {}).items())
                           ) or "n/a"
        _v3_maint_s = "maintenance: never/state n/a"
        try:
            _v3_ms = _v3_json.loads((_v3_dd / "maintenance_state.json")
                                    .read_text(encoding="utf-8"))
            _v3_ran = _v3_ms.get("last_run", _v3_ms.get("date", "?"))
            _v3_maint_s = f"maintenance ran {_v3_ran}"
        except Exception:
            pass
        _record("ARMOR (opsfix3)",
                f"{_v3_guard_s} | {_v3_risk_s} | {_v3_spread_s} | {_v3_flat_s} | "
                f"{_v3_disk_s} | {_v3_mem_s} | MTF {_v3_mtf} | {_v3_maint_s}")
    except Exception as _v3_err:
        _record("ARMOR (opsfix3)", f"(status unavailable: {type(_v3_err).__name__})")
    # opsfix:v3-armor-end


_OUTCOME_LOG_FIELDS = ["timestamp", "order_id", "symbol", "side", "pnl",
                       "exit_price", "comment"]
_OUTCOME_KEYS_FILE = DATA_DIR / "logged_outcome_deals.json"
_POSITION_IDS_FILE = DATA_DIR / "tracked_bot_positions.json"
_PLUMBING_DEALS_FILE = DATA_DIR / "plumbing_test_deals.json"


def _as_position_id(value):
    """Return a positive integer position ID, or None for invalid values."""
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _load_plumbing_test_position_ids() -> set:
    """Load position IDs from the explicit demo plumbing test only."""
    ids = set()
    test_result = DATA_DIR / "demo_order_test_result.json"
    try:
        if test_result.exists():
            result = json.loads(test_result.read_text(encoding="utf-8"))
            for value in result.get("position_ids", []) or []:
                number = _as_position_id(value)
                if number:
                    ids.add(number)
    except (json.JSONDecodeError, OSError):
        pass
    return ids


def _load_tracked_position_ids() -> set:
    """Load strategy position IDs whose close deals should be checked.

    Position-specific MT5 history is reliable on Pepperstone, while the
    date-range history call can omit XAUUSD deals. Explicit plumbing-test
    positions are excluded so they never enter strategy performance or the
    strategy risk calculation.
    """
    ids = set()
    plumbing_ids = _load_plumbing_test_position_ids()
    try:
        if _POSITION_IDS_FILE.exists():
            state = json.loads(_POSITION_IDS_FILE.read_text(encoding="utf-8"))
            for value in state.get("position_ids", []) or []:
                number = _as_position_id(value)
                if number and number not in plumbing_ids:
                    ids.add(number)
    except (json.JSONDecodeError, OSError):
        pass

    # Decision rows store the opening order ID. Use it as a compatibility
    # candidate because Pepperstone's netting account returned the opening
    # order and position with the same ID in the verified demo test.
    decisions = DATA_DIR / "decisions_log.csv"
    try:
        if decisions.exists():
            with open(decisions, "r", newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    if (row.get("exec_status") or "").strip() != "EXECUTED":
                        continue
                    number = _as_position_id(row.get("order_id"))
                    if number and number not in plumbing_ids:
                        ids.add(number)
    except (OSError, csv.Error):
        pass
    return ids


def _save_plumbing_test_deals(deals: list) -> None:
    """Persist deal IDs belonging to explicit broker plumbing tests.

    This is metadata only. It does not rewrite or delete the audit CSV. The
    report uses these IDs to exclude plumbing tests from strategy statistics.
    """
    if not deals:
        return
    records = []
    try:
        if _PLUMBING_DEALS_FILE.exists():
            state = json.loads(_PLUMBING_DEALS_FILE.read_text(encoding="utf-8"))
            records = list(state.get("deals", []) or [])
    except (json.JSONDecodeError, OSError):
        records = []
    by_id = {str(row.get("deal_id")): row for row in records
             if isinstance(row, dict) and row.get("deal_id") not in (None, "", 0)}
    for deal in deals:
        deal_id = deal.get("deal_id")
        if deal_id in (None, "", 0):
            continue
        record = by_id.get(str(deal_id))
        values = {
            "deal_id": deal_id,
            "position_id": deal.get("position_id"),
            "symbol": deal.get("symbol", config.MT5_SYMBOL),
            "side": deal.get("side", deal.get("type", "")),
            "pnl": deal.get("pnl", 0.0),
            "price": deal.get("price", 0.0),
        }
        if record is None:
            record = {**values,
                      "recorded_at": datetime.now().isoformat(timespec="seconds")}
            records.append(record)
            by_id[str(deal_id)] = record
        else:
            # Complete metadata created by an older package without duplicating
            # the already-known audit deal.
            for key, value in values.items():
                if record.get(key) in (None, "") and value not in (None, ""):
                    record[key] = value
    try:
        _PLUMBING_DEALS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _PLUMBING_DEALS_FILE.write_text(json.dumps({
            "deals": records,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }, indent=2), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not persist plumbing-test deal IDs: %s", exc)


def _save_tracked_position_ids(position_ids: set) -> None:
    """Persist bot position IDs for reliable post-close history lookup."""
    clean = sorted({_as_position_id(value) for value in position_ids} - {None})
    try:
        _POSITION_IDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _POSITION_IDS_FILE.write_text(json.dumps({
            "position_ids": clean,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not persist tracked position IDs: %s", exc)


def _outcome_signature(deal: dict) -> str:
    """Stable fallback identity for old rows that have no MT5 deal ID."""
    return "sig:" + "|".join(str(deal.get(k, "")) for k in (
        "position_id", "symbol", "type", "pnl", "price"))


def _load_logged_outcome_keys() -> set:
    """Load persisted deal IDs and seed signatures from the existing CSV.

    The CSV already contains rows from older versions that did not store a
    unique MT5 deal ID. Their stable fields are used as a one-time fallback so
    the first run after this fix does not append the same historical deals
    again. If the CSV is empty, ignore a stale state file so verified deals can
    be rebuilt instead of being silently hidden from the report.
    """
    keys = set()
    csv_path = DATA_DIR / "trade_outcomes.csv"
    csv_has_rows = False

    try:
        if csv_path.exists():
            with open(csv_path, "r", newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    if any((value or "").strip() for value in row.values()):
                        csv_has_rows = True
                    keys.add(_outcome_signature({
                        "position_id": row.get("order_id", ""),
                        "symbol": row.get("symbol", ""),
                        "type": row.get("side", ""),
                        "pnl": row.get("pnl", ""),
                        "price": row.get("exit_price", ""),
                    }))
    except (OSError, csv.Error):
        pass

    # A state file without its corresponding CSV is not enough to suppress a
    # report row: the deal details can still be recovered from MT5 history.
    if csv_has_rows:
        try:
            if _OUTCOME_KEYS_FILE.exists():
                state = json.loads(_OUTCOME_KEYS_FILE.read_text(encoding="utf-8"))
                keys.update(str(k) for k in (state.get("keys") or []))
        except (json.JSONDecodeError, OSError):
            pass
    return keys


def _save_logged_outcome_keys(keys: set) -> None:
    try:
        _OUTCOME_KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
        _OUTCOME_KEYS_FILE.write_text(json.dumps({
            "keys": sorted(keys),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not persist logged outcome IDs: %s", exc)


def log_trade_outcome(order_id, symbol, side, pnl, exit_price,
                      comment: str = "") -> None:
    """Append a realised trade outcome to data/trade_outcomes.csv.

    This closes the loop started by log_decision(): signal -> decision ->
    execution -> outcome, so win/loss can be measured per confidence level.
    """
    from datetime import datetime as _dt
    path = DATA_DIR / "trade_outcomes.csv"
    write_header = not path.exists() or path.stat().st_size == 0
    row = {
        "timestamp": _dt.now(timezone.utc).isoformat(timespec="seconds"),
        "order_id": order_id or "",
        "symbol": symbol,
        "side": side,
        "pnl": pnl,
        "exit_price": exit_price,
        "comment": comment,
    }
    try:
        with open(path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_OUTCOME_LOG_FIELDS)
            if write_header:
                writer.writeheader()
            writer.writerow(row)
        logger.info("Trade outcome logged -> %s", path)
    except OSError as exc:
        logger.warning("Could not write trade outcome: %s", exc)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #
def run_pipeline(allow_new_entries: bool = True) -> None:
    """One full pass; no-new-entry mode still manages and monitors open positions."""
    STATUS.clear()
    config.reload_env()   # pick up edited .env (credentials/markets) each cycle
    if allow_new_entries is True:
        _record("ENTRY MODE", "new entries subject to all hard safety gates")
    else:
        _record("ENTRY MODE", "NO NEW ENTRIES; position management/monitoring continue")
    t_pipeline_start = time.time()
    _LATENCY_LOG.clear()
    plumbing_position_ids = _load_plumbing_test_position_ids()
    tracked_position_ids = _load_tracked_position_ids()
    _touch_lock()         # keep the single-instance lock fresh

    # automatic file maintenance (runs at most once per day — keeps the app
    # light: rotates logs, trims CSVs, cleans old recordings)
    try:
        from maintenance import run_maintenance
        run_maintenance()
    except Exception as exc:
        logger.warning("maintenance skipped: %s", exc)

    logger.info("=" * 70)
    logger.info("GOLD TRADING SYSTEM — pipeline start "
                "(trade=%s, timeframe=%s, source=%s)",
                config.MT5_SYMBOL, config.TIMEFRAME, config.DATA_SOURCE)

    # STEP 1
    t0 = time.time()
    data_acquired_monotonic = None
    try:
        data = run_step1()
        data_acquired_monotonic = time.monotonic()
        _record_latency("STEP1 acquire", (time.time()-t0)*1000)
        if data:
            age = data.get("last_data_age_seconds")
            if age is None:
                logger.warning("FEED AGE at acquisition is unknown")
            else:
                try:
                    if isinstance(age, bool):
                        raise ValueError("boolean feed age")
                    age = float(age)
                    if not math.isfinite(age):
                        raise ValueError("non-finite feed age")
                    _record_latency(f"feed age at acquisition {age:.3f}s",
                                    age * 1000.0)
                except (TypeError, ValueError, OverflowError):
                    logger.warning("FEED AGE at acquisition is invalid")
            quote_age = data.get("quote_age_seconds")
            _quote_age_value = None
            if quote_age is None:
                _quote_age_display = "unknown"
            else:
                try:
                    if isinstance(quote_age, bool):
                        raise ValueError("boolean quote age")
                    _quote_age_value = float(quote_age)
                    if not math.isfinite(_quote_age_value):
                        raise ValueError("non-finite quote age")
                    _quote_age_display = f"{_quote_age_value:.3f}s"
                except (TypeError, ValueError, OverflowError):
                    _quote_age_value = None
                    _quote_age_display = "invalid"
            logger.info("QUOTE AGE at acquisition: %s", _quote_age_display)
            if _quote_age_value is not None:
                _record_latency("quote age at acquisition",
                                _quote_age_value * 1000.0)
            _dq = data.get("data_quality") or {}
            if not isinstance(_dq, dict):
                _dq = {}
            _spread = data.get("spread_pct")
            try:
                _spread_display = f"{float(_spread):.4f}%" if _spread is not None else "unknown"
            except (TypeError, ValueError, OverflowError):
                _spread_display = "invalid"
            logger.info(
                "FEED QUALITY at acquisition: quote=%s spread=%s L2=%s L3=%s "
                "L3_aggressor=%s l3_aggr_limit=%s",
                _dq.get("quote", "unknown"),
                _spread_display,
                _dq.get("level2", "unknown"), _dq.get("level3", "unknown"),
                _dq.get("l3_aggressive_flow", "unknown"),
                _dq.get("l3_aggr_limit", "unknown"))
    except NotImplementedError as exc:
        _record("STEP 1  DATA ACQUISITION", f"NOT IMPLEMENTED — {exc}")
        data = None
    except Exception as exc:
        _record("STEP 1  DATA ACQUISITION", f"ERROR — {exc}")
        data = None

    # ---- live news (free, no key) — real headlines feed the AI decision ------
    # Cached, so the network is only hit every NEWS_CACHE_MINUTES. A failure
    # here never stops the bot: the AI simply sees no headlines that cycle.
    if data is not None:
        try:
            from news import enrich_market_news
            enrich_market_news(data)
        except Exception as exc:
            logger.warning("news fetch skipped: %s", exc)

    # ---- live macro (DXY / 10Y yield / VIX, free) — feeds the AI too --------
    if data is not None:
        try:
            from macro import enrich_macro
            enrich_macro(data)
        except Exception as exc:
            logger.warning("macro fetch skipped: %s", exc)

    # STEP 2
    t1 = time.time()
    snapshot = None
    _flash_crash_reason = ""
    if data is not None:
        try:
            snapshot = run_step2(data)
            _record_latency("STEP2 analyze", (time.time()-t1)*1000)
            # L4 Flash crash kill switch check immediately after fresh snapshot
            crash_reason = _check_flash_crash(snapshot)
            if crash_reason:
                _flash_crash_reason = crash_reason
                logger.error(f"L4 KILL SWITCH TRIGGERED: {crash_reason} -> protecting open positions")
                _record("SAFETY: FLASH_CRASH", f"TRIGGERED — {crash_reason}")
                try:
                    import re as _fc_re
                    from step4_mt5_execution import MT5Executor
                    import trade_history as _fc_th
                    _fc_signed = 0.0
                    _fc_m = _fc_re.search(r"price\s+([0-9.]+)\s*->\s*([0-9.]+)", crash_reason)
                    if _fc_m:
                        _fc_signed = float(_fc_m.group(2)) - float(_fc_m.group(1))
                    import MetaTrader5 as mt5
                    if mt5 and config.mt5_initialize(mt5):
                        try:
                            positions = mt5.positions_get(symbol=config.MT5_SYMBOL)
                            info = mt5.symbol_info(config.MT5_SYMBOL)
                            filling = (
                                MT5Executor._select_filling_mode(info)
                                if info is not None
                                else getattr(mt5, "ORDER_FILLING_IOC", 1)
                            )
                            if positions:
                                for p in positions:
                                    if int(getattr(p, "magic", -1)) != 234000:
                                        continue
                                    is_buy = p.type == mt5.POSITION_TYPE_BUY
                                    # Never panic-dump a position when price surged IN FAVOR of it;
                                    # only emergency-flatten when the shock moved ADVERSELY (or direction unknown).
                                    if (_fc_signed > 0 and is_buy) or (_fc_signed < 0 and not is_buy):
                                        logger.info(
                                            "L4 FLASH CRASH favorable to open %s ticket=%s (%+.2f) — keeping position for PM trail",
                                            "BUY" if is_buy else "SELL",
                                            getattr(p, "ticket", "?"),
                                            _fc_signed,
                                        )
                                        continue
                                    tick = mt5.symbol_info_tick(config.MT5_SYMBOL)
                                    if tick:
                                        price = tick.bid if is_buy else tick.ask
                                        vol = float(p.volume)
                                        ticket_id = int(p.ticket)
                                        req = {
                                            "action": mt5.TRADE_ACTION_DEAL,
                                            "symbol": config.MT5_SYMBOL,
                                            "volume": vol,
                                            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                                            "position": ticket_id,
                                            "price": price,
                                            "deviation": 20,
                                            "magic": 234000,
                                            "comment": "flash crash flatten",
                                            "type_time": mt5.ORDER_TIME_GTC,
                                            "type_filling": filling,
                                        }
                                        res = mt5.order_send(req)
                                        done_codes = (
                                            getattr(mt5, "TRADE_RETCODE_DONE", 10009),
                                            getattr(mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010),
                                        )
                                        if res is not None and getattr(res, "retcode", None) in done_codes:
                                            fill_px = float(getattr(res, "price", 0.0) or price)
                                            open_px = float(getattr(p, "price_open", 0.0) or fill_px)
                                            gain_pts = (fill_px - open_px) if is_buy else (open_px - fill_px)
                                            try:
                                                _fc_th.record_tca("EXIT", ticket_id, price, fill_px, vol, note="FLASH_CRASH")
                                                _fc_th.record_close(
                                                    ticket_id,
                                                    "BUY" if is_buy else "SELL",
                                                    fill_px,
                                                    "FLASH_CRASH",
                                                    gain_pts=gain_pts,
                                                    volume=vol,
                                                )
                                            except Exception:
                                                pass
                        finally:
                            mt5.shutdown()
                except Exception as fe:
                    logger.error(f"Flash crash flatten failed: {fe}")
        except Exception as exc:
            logger.exception("STEP 2 failed")
            _record("STEP 2  MARKET ANALYSIS", f"ERROR — {exc}")

    # STEP 3 (skipped entirely when the PAUSE file exists or L4 flash crash fired — saves AI quota)
    t2 = time.time()
    decision = None
    if _entries_paused():
        _record("STEP 3  AI DECISION", "PAUSED — PAUSE file present")
    elif _flash_crash_reason:
        from step3_ai_decision import Decision as _FcDecision
        decision = _FcDecision(
            action="HOLD",
            confidence=0.0,
            rationale=f"safety gate: {_flash_crash_reason}",
            model="none-safety-gate",
        )
        _record("STEP 3  AI DECISION", "HOLD (L4 flash-crash guard active)")
    elif snapshot is not None:
        try:
            decision = run_step3(snapshot)
            _record_latency("STEP3 AI", (time.time()-t2)*1000)
            # 24k (A5+A6): attach the AI answer to the record it actually belongs to,
            # and replace only that record.
            #
            # Before: this read the WHOLE diary, edited lines[-1] - whatever it was -
            # and wrote the WHOLE file back, every cycle. Two defects in one block:
            #   A5  the comment said "only update if timestamp matches" but nothing
            #       checked, so on a cycle where step 2 wrote no record the answer
            #       landed on the PREVIOUS minute's record (which already had one).
            #   A6  at the 20k-line cap that is ~20 MB read + 20 MB written every ~66 s,
            #       non-atomically: a kill mid-write could truncate the whole diary,
            #       the only file the judges can be graded from.
            try:
                _update_last_diary_ai(
                    DATA_DIR / "snapshots_history.jsonl",
                    getattr(snapshot, "timestamp", None),
                    action=getattr(decision, "action", "HOLD"),
                    confidence=float(getattr(decision, "confidence", 0) or 0),
                    rationale=str(getattr(decision, "rationale", ""))[:200],
                    model=(getattr(decision, "model", "") or "unknown"),
                )
            except Exception as _ae:
                try:
                    logger.debug(f"AI history update failed: {_ae}")
                except Exception:
                    pass
        except Exception as exc:
            logger.exception("STEP 3 failed")
            _record("STEP 3  AI DECISION", f"ERROR — {exc}")

    # ------------------------------------------------------------------ #
    # SAFETY GATES before execution: session, stale feed, risk limits.
    # On weekends / holidays / feed drops / big losses the bot must NOT trade.
    # If Step 2/3 took >= EXECUTION_FRESH_REACQUIRE_SECONDS (e.g. slow AI call),
    # re-acquire a fresh Step-1 quote and verify <= 1.0*ATR drift so an AI
    # timeout never falsely kills a live Bookmap cycle as STALE QUOTE.
    # ------------------------------------------------------------------ #
    _drift_block_reason = ""
    _reacquire_after = float(
        getattr(config, "EXECUTION_FRESH_REACQUIRE_SECONDS", 2.0) or 0.0)
    _pre_gate_elapsed = (
        max(0.0, time.monotonic() - data_acquired_monotonic)
        if data_acquired_monotonic is not None else 0.0)
    if (allow_new_entries
            and snapshot is not None
            and _reacquire_after > 0.0
            and _pre_gate_elapsed >= _reacquire_after):
        try:
            _fresh_data = run_step1()
            _fresh_bad = _preflight_data_reason(_fresh_data, 0.0)
            if not _fresh_bad and isinstance(_fresh_data, dict):
                _old_px = float(getattr(snapshot, "price", 0.0) or 0.0)
                _new_px = float(_fresh_data.get("price", 0.0) or 0.0)
                _atr = _snapshot_atr(snapshot)
                _max_drift = (_atr * 1.0) if _atr > 0.0 else (_old_px * 0.0015 if _old_px > 0.0 else 5.0)
                if _old_px <= 0.0 or _new_px <= 0.0 or abs(_new_px - _old_px) <= _max_drift:
                    data = _fresh_data
                    data_acquired_monotonic = time.monotonic()
                    if _new_px > 0.0:
                        snapshot.price = _new_px
                    if float(_fresh_data.get("bid", 0.0) or 0.0) > 0.0:
                        snapshot.bid = float(_fresh_data["bid"])
                    if float(_fresh_data.get("ask", 0.0) or 0.0) > 0.0:
                        snapshot.ask = float(_fresh_data["ask"])
                    _record("STEP 1  REVALIDATE",
                            f"re-acquired fresh quote ({_new_px:.2f}) after {_pre_gate_elapsed:.1f}s Step 2/3")
                else:
                    _drift_block_reason = (
                        f"quote drifted {abs(_new_px - _old_px):.2f} > {_max_drift:.2f} "
                        f"during {_pre_gate_elapsed:.1f}s Step 2/3")
        except Exception as _re_exc:
            logger.warning("Pre-gate quote re-acquire failed: %s", _re_exc)

    _feed_elapsed = (max(0.0, time.monotonic() - data_acquired_monotonic)
                    if data_acquired_monotonic is not None else 0.0)
    if _flash_crash_reason:
        gate_ok, gate_reason = False, _flash_crash_reason
    elif _drift_block_reason:
        gate_ok, gate_reason = False, _drift_block_reason
        _record("SAFETY: EXECUTION DRIFT", _drift_block_reason)
    else:
        gate_ok, gate_reason = _safety_gates(data, _feed_elapsed)
    if not gate_ok:
        from step3_ai_decision import Decision
        # 24m/A1d: the AI is never called when a safety gate blocks the cycle, so the
        # record must not imply it was. The gate's own reason is in the reason column.
        decision = Decision(action="HOLD", confidence=0.0,
                            rationale=f"safety gate: {gate_reason}",
                            model="none-safety-gate")
        if snapshot is not None:
            snapshot.notes.append(gate_reason)
    if snapshot is not None:
        try:
            snapshot.quality_evidence = _market_quality_evidence(
                data, _feed_elapsed, gate_ok, gate_reason)
        except Exception as exc:
            logger.warning("market-quality evidence capture failed: %s", exc)
            snapshot.quality_evidence = {
                "captured_at": datetime.now(timezone.utc).isoformat(),
                "safety_gate_status": "UNKNOWN",
                "capture_error": type(exc).__name__,
            }
        _attach_quality_evidence_to_shadow_candidates(snapshot)

    # L5 RL size multiplier check after snapshot
    if snapshot is not None:
        try:
            import trade_history as th
            if getattr(config, "RL_ENABLED", True):
                mult = th.get_rl_size_multiplier(getattr(snapshot, "regime", ""))
                if mult < 1.0:
                    logger.info(f"L5 RL: size multiplier {mult:.2f} due to loss streak regime {getattr(snapshot,'regime','')}")
                    _record("RL_ADAPTIVE", f"size x{mult:.2f} regime {getattr(snapshot,'regime','')}")
                pause, reason = th.should_pause_entries(getattr(snapshot, "regime", ""))
                if pause:
                    logger.warning(f"L5 RL PAUSE: {reason}")
                    _record("SAFETY: RL_PAUSE", reason)
                    from step3_ai_decision import Decision as _Dec
                    decision = _Dec(action="HOLD", confidence=0.0, rationale=f"RL pause: {reason}",
                                    model="none-rl-pause")
                    # Skip execution but still manage
        except Exception as e:
            logger.warning(f"L5 RL snapshot check failed: {e}")

    # STEP 4a: manage any OPEN positions with the fresh market data (v4).
    # Runs before new entries so a flip-exit frees the slot in the same
    # cycle. Management only ever reduces risk (tighten/BE/close) and must
    # never break the pipeline — all errors are contained.
    if snapshot is not None:
        try:
            from position_manager import get_position_manager
            get_position_manager().manage(snapshot)
        except Exception:
            logger.exception("position manager failed (non-fatal)")

    # Recheck the data-side feed immediately before the execution step. The
    # earlier full safety gate also checked risk/session/trade guards; this
    # second check only ages the acquired quote/depth through Step 3/4 prep.
    if decision is not None and gate_ok:
        _execution_feed_elapsed = (
            max(0.0, time.monotonic() - data_acquired_monotonic)
            if data_acquired_monotonic is not None else 0.0)
        _execution_feed_reason = _preflight_data_reason(
            data, _execution_feed_elapsed)
        if _execution_feed_reason:
            _record("SAFETY: EXECUTION FEED", _execution_feed_reason)
            gate_ok = False
            gate_reason = _execution_feed_reason
            from step3_ai_decision import Decision as _ExecutionDecision
            decision = _ExecutionDecision(
                action="HOLD", confidence=0.0,
                rationale=f"execution feed gate: {_execution_feed_reason}",
                model="none-execution-feed-gate")
            if snapshot is not None:
                if _execution_feed_reason not in snapshot.notes:
                    snapshot.notes.append(_execution_feed_reason)
        if snapshot is not None:
            try:
                snapshot.quality_evidence = _market_quality_evidence(
                    data, _execution_feed_elapsed, gate_ok, gate_reason)
            except Exception as exc:
                logger.warning("execution-time quality capture failed: %s", exc)
                snapshot.quality_evidence = {
                    "captured_at": datetime.now(timezone.utc).isoformat(),
                    "safety_gate_status": "UNKNOWN",
                    "capture_error": type(exc).__name__,
                }
            _attach_quality_evidence_to_shadow_candidates(snapshot)

    # STEP 4
    exec_result = None
    entry_authorized = (gate_ok is True and allow_new_entries is True)
    if not allow_new_entries:
        authorization_reason = "operator selected --no-new-entries"
    elif gate_ok is not True:
        authorization_reason = str(gate_reason or "safety gate blocked")
    else:
        authorization_reason = ""
    if decision is not None:
        try:
            exec_result = run_step4(
                decision, snapshot,
                execution_authorized=entry_authorized,
                authorization_reason=authorization_reason)
            # A position was actually opened -> remember its position ID for
            # reliable post-close history lookup and record the cooldown/cap.
            if exec_result is not None and getattr(exec_result, "status", "") == "EXECUTED":
                position_id = (getattr(exec_result, "position_id", None)
                               or getattr(exec_result, "order_id", None))
                position_id = _as_position_id(position_id)
                if position_id:
                    tracked_position_ids.add(position_id)
                try:
                    from trade_guard import TradeGuard
                    TradeGuard().record_trade()
                except Exception as exc:
                    logger.warning("trade-guard record failed: %s", exc)
        except Exception as exc:
            logger.exception("STEP 4 failed")
            _record("STEP 4  EXECUTION", f"ERROR — {exc}")
    else:
        _record("STEP 4  EXECUTION", "SKIPPED (no decision)")

    # STEP 5
    open_positions = []
    try:
        open_positions = run_step5() or []
        # Also learn about positions that were already open before this
        # process started. Their IDs remain tracked after they close.
        for position in open_positions:
            position_id = _as_position_id(
                position.get("position_id", position.get("ticket")))
            if position_id:
                tracked_position_ids.add(position_id)
    except Exception as exc:
        logger.exception("STEP 5 failed")
        _record("STEP 5  MONITORING", f"ERROR — {exc}")
    _save_tracked_position_ids(tracked_position_ids)

    # ---- trade-outcome logging (closed MT5 deals -> data/trade_outcomes.csv) --
    # Pepperstone reliably returns these deals when queried by position ID.
    # Persist unique IDs/signatures so the same closed deal is logged only once.
    try:
        from step5_monitoring import TradeMonitor
        monitor = TradeMonitor()
        if plumbing_position_ids:
            # Record the deal IDs separately so robot_report.py can preserve
            # the audit evidence while excluding these tests from strategy
            # performance statistics.
            _save_plumbing_test_deals(
                monitor.check_closed_deals(position_ids=plumbing_position_ids))
        logged_keys = _load_logged_outcome_keys()
        new_keys = set()
        for deal in monitor.check_closed_deals(
                position_ids=tracked_position_ids):
            deal_id = deal.get("deal_id")
            id_key = f"deal:{deal_id}" if deal_id not in (None, "", 0) else ""
            sig_key = _outcome_signature(deal)
            if (id_key and id_key in logged_keys) or sig_key in logged_keys:
                continue
            pos_id = deal.get("position_id")
            _comment = (
                f"closed deal pos={pos_id}"
                if pos_id not in (None, "", 0)
                else "closed deal"
            )
            log_trade_outcome(
                order_id=deal_id or pos_id,
                symbol=deal.get("symbol", config.MT5_SYMBOL),
                side=deal.get("side", deal.get("type", "")),
                pnl=deal.get("pnl", 0.0),
                exit_price=deal.get("price", 0.0),
                comment=_comment)
            if id_key:
                new_keys.add(id_key)
            new_keys.add(sig_key)
        if new_keys:
            _save_logged_outcome_keys(logged_keys | new_keys)
    except Exception as exc:
        logger.warning("trade-outcome logging failed: %s", exc)

    # MT5 signal bridge (for the EA / indicator on MT5)
    if snapshot is not None and decision is not None:
        try:
            run_signal_bridge(
                snapshot, decision, allow_new_entries=entry_authorized)
        except Exception as exc:
            logger.warning("MT5 signal bridge failed: %s", exc)

    # Persist only after Step 4 has had a chance to create/update its shadow
    # candidate, so the receipt and candidate carry the same current-cycle data.
    if snapshot is not None:
        _persist_market_quality_snapshot(snapshot)

    # decision journal (for later tuning / backtesting)
    if snapshot is not None and decision is not None:
        try:
            log_decision(snapshot, decision, exec_result)
        except Exception as exc:
            logger.warning("decision log failed: %s", exc)

    total_ms = (time.time()-t_pipeline_start)*1000
    _record_latency("TOTAL pipeline", total_ms)
    if getattr(config, "LATENCY_LOG_ENABLED", True):
        logger.info(f"L4 Latency summary: total {total_ms:.0f}ms target {getattr(config,'LATENCY_TARGET_MS',500)}ms")

    try:
        _pw_ctx = (getattr(snapshot, "power", None) or {}) if snapshot is not None else {}
        _sh_ctx = (getattr(snapshot, "shot", None) or {}) if snapshot is not None else {}
        _nw_ctx = getattr(snapshot, "news", None) if snapshot is not None else None
        _nw_state = (
            _nw_ctx.get("news_state") if isinstance(_nw_ctx, dict)
            else getattr(_nw_ctx, "news_state", "QUIET")
        )
        _LAST_PIPELINE_CONTEXT.clear()
        _LAST_PIPELINE_CONTEXT.update({
            "open_positions_count": len(open_positions or []),
            "power_dir": str(_pw_ctx.get("direction", "") or "").upper(),
            "power_reason": str(_pw_ctx.get("reason_code", _pw_ctx.get("reason", "")) or "").upper(),
            "last_shot": str(_sh_ctx.get("shot", "") or "").upper() if isinstance(_sh_ctx, dict) else "",
            "news_state": str(_nw_state or "QUIET").upper(),
            "allow_new_entries": bool(allow_new_entries),
        })
    except Exception:
        pass

    print_summary()


def check_fast_burst_wakeup() -> bool:
    """Mid-minute 10s sub-poll: wake early ONLY when flat, POWER v2 is active,
    and a fresh aggressive impulse ignites on the live Bookmap tape.
    """
    try:
        if not bool(_LAST_PIPELINE_CONTEXT.get("allow_new_entries", False)):
            return False
        if int(_LAST_PIPELINE_CONTEXT.get("open_positions_count", 0) or 0) > 0:
            return False
        if _entries_paused():
            return False
        pdir = str(_LAST_PIPELINE_CONTEXT.get("power_dir", "") or "").upper()
        preason = str(_LAST_PIPELINE_CONTEXT.get("power_reason", "") or "").upper()
        if pdir not in ("UP", "DOWN") or "FORCE_DOMINATES" not in preason:
            return False
        if str(_LAST_PIPELINE_CONTEXT.get("last_shot", "") or "").upper() == "GO":
            return False
        if not bool(getattr(config, "AGGRESSIVE_ENTRY_ENABLED", False)):
            return False
        md = run_step1()
        if not isinstance(md, dict) or not md.get("has_data"):
            return False
        from aggressive_trigger import evaluate_aggressive_trigger
        ticks = md.get("tick_data") or []
        now_ep = time.time()
        bv = sv = 0.0
        for tk in ticks:
            if not isinstance(tk, dict):
                continue
            ts_v = tk.get("ts") or tk.get("timestamp")
            if isinstance(ts_v, datetime) and (now_ep - ts_v.timestamp()) > 30.0:
                continue
            vol = float(tk.get("volume", 0.0) or 0.0)
            if vol <= 0.0:
                continue
            sd = str(tk.get("side") or tk.get("operation") or "").upper()
            if sd in ("BUY", "BID_HIT_BUY", "AGGR_BUY"):
                bv += vol
            elif sd in ("SELL", "ASK_HIT_SELL", "AGGR_SELL"):
                sv += vol
        if (bv + sv) <= 0.0:
            return False
        trig = evaluate_aggressive_trigger(
            power={"direction": pdir, "reason_code": preason},
            shot_context={
                "has_data": bool(md.get("has_data")),
                "data_quality_ok": True,
                "last_data_age_seconds": float(md.get("last_data_age_seconds", 0.5) or 0.5),
                "bid": float(md.get("bid", 0.0) or 0.0),
                "ask": float(md.get("ask", 0.0) or 0.0),
                "news_state": str(_LAST_PIPELINE_CONTEXT.get("news_state", "QUIET") or "QUIET"),
            },
            config=config,
            price=float(md.get("price", 0.0) or 0.0),
            order_flow={"aggressive_buy_volume": bv, "aggressive_sell_volume": sv},
        )
        if bool(trig.get("confirmed")):
            logger.info("FAST BURST WATCH: %s -> waking pipeline!", trig.get("reason", ""))
            return True
    except Exception:
        return False
    return False


def print_summary() -> None:
    print("\n" + "=" * 70)
    print("PIPELINE SUMMARY")
    print("=" * 70)
    for step, status in STATUS:
        print(f"  {step:<28} {status}")
    entry_mode = next((status for step, status in STATUS
                       if step == "ENTRY MODE"), "mode unavailable")
    entry_result = next((status for step, status in reversed(STATUS)
                         if step == "STEP 4  EXECUTION"), "not reached")
    if len(entry_result) > 180:
        entry_result = entry_result[:177] + "..."
    print(f"  QUICK STATUS: {entry_mode} | ENTRY: {entry_result}")
    print("=" * 70)


def _env_snapshot() -> dict:
    """The values that decide behaviour, under their .env names (what audit_day.py reads)."""
    g = lambda k, d=None: getattr(config, k, d)
    gm = g("GEMINI_MODELS")
    return {
        "CONFIDENCE_THRESHOLD": g("AI_CONFIDENCE_THRESHOLD"),
        "AI_MIN_SIGNAL_STRENGTH": g("AI_MIN_SIGNAL_STRENGTH"),
        "AI_MIN_INTERVAL_MINUTES": g("AI_MIN_INTERVAL_MINUTES"),
        "V6_CFD_SPREAD_MAX": g("V6_CFD_SPREAD_MAX"),
        "BOOKMAP_WINDOW_SECONDS": g("BOOKMAP_WINDOW_SECONDS"),
        "ORDER_FLOW_WINDOW_SECONDS": g("ORDER_FLOW_WINDOW_SECONDS"),
        "BOOKMAP_MAX_DEPTH_LEVELS": g("BOOKMAP_MAX_DEPTH_LEVELS"),
        "GEMINI_MODEL": g("GEMINI_MODEL"),
        "GEMINI_MODELS": ",".join(gm) if isinstance(gm, (list, tuple)) else gm,
        "GEMINI_THINKING_LEVEL": g("GEMINI_THINKING_LEVEL"),
        "EXECUTION_MODE": g("EXECUTION_MODE"),
        "TRADING_ENABLED": g("TRADING_ENABLED"),
        "ALLOW_LIVE_TRADING": g("ALLOW_LIVE_TRADING"),
        "TIMEFRAME": g("TIMEFRAME"),
        "A1_BASE_TF_MINUTES": os.environ.get("A1_BASE_TF_MINUTES"),
        "VOICE_GAIN": os.environ.get("VOICE_GAIN"),
        "POWER_BAR_SECONDS": g("POWER_BAR_SECONDS"),
        "STOP_LOSS_ATR_MULT": g("STOP_LOSS_ATR_MULT"),
        "TAKE_PROFIT_ATR_MULT": g("TAKE_PROFIT_ATR_MULT"),
        # Audit assumptions are recorded for reproducibility, not consumed by
        # the trading path. They do not change execution behavior.
        "AUDIT_SPREAD": os.environ.get("AUDIT_SPREAD", "0.50"),
        "AUDIT_MAX_HOLD_MIN": os.environ.get("AUDIT_MAX_HOLD_MIN", "180"),
        "POWER_LEAD_ENABLED": g("POWER_LEAD_ENABLED"),
        "POWER_LEAD_CONF_FLOOR": g("POWER_LEAD_CONF_FLOOR"),
        "POWER_LEAD_SHADOW_ENABLED": g("POWER_LEAD_SHADOW_ENABLED"),
        "LIMIT_ORDER_ENABLED": g("LIMIT_ORDER_ENABLED"),
        "AGGRESSIVE_ENTRY_ENABLED": g("AGGRESSIVE_ENTRY_ENABLED"),
        "AGGRESSIVE_SPREAD_MAX_PCT": g("AGGRESSIVE_SPREAD_MAX_PCT"),
        "A2_LEAN_MIN": g("A2_LEAN_MIN"),
        "A2_LEAN_HOLD_SECONDS": g("A2_LEAN_HOLD_SECONDS"),
        "A2_PANEL_WINDOW_SECONDS": g("A2_PANEL_WINDOW_SECONDS"),
        "A2_BURST_MAX_PER_DAY": g("A2_BURST_MAX_PER_DAY"),
        "A2_BURST_COOLDOWN_MINUTES": g("A2_BURST_COOLDOWN_MINUTES"),
        "A2_BURST_SL_PTS": g("A2_BURST_SL_PTS"),
        "A2_BURST_TP_PTS": g("A2_BURST_TP_PTS"),
        "A2_MIN_RR": g("A2_MIN_RR"),
        "V6_4TEAMS_ENABLED": g("V6_4TEAMS_ENABLED"),
        "AI_AS_VOTE": g("AI_AS_VOTE"),
        "MT5_SYMBOL": g("MT5_SYMBOL"),
        "DATA_SOURCE": g("DATA_SOURCE"),
    }


def _write_run_config() -> None:
    """Record the config THIS day really ran with - once per day, first start wins.

    audit_day.py grades a day against this file, so editing .env tomorrow can never
    rewrite yesterday's grade; the auditor prints the difference when there is one.
    """
    now = datetime.now()
    path = DATA_DIR / f"run_config_{now:%Y%m%d}.json"
    if path.exists():
        return                      # the first start of the day defines the day
    try:
        payload = {"date": f"{now:%Y-%m-%d}", "written_at": now.isoformat(timespec="seconds"),
                   "pid": os.getpid(), "mode": "loop" if "--loop" in sys.argv else "once"}
        payload.update(_env_snapshot())
        path.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
        logger.info("RUN CONFIG recorded -> %s (gate=%s ai_gate=%s spread=%s window=%ss depth=%s)",
                    path, payload.get("CONFIDENCE_THRESHOLD"), payload.get("AI_MIN_SIGNAL_STRENGTH"),
                    payload.get("V6_CFD_SPREAD_MAX"), payload.get("BOOKMAP_WINDOW_SECONDS"),
                    payload.get("BOOKMAP_MAX_DEPTH_LEVELS"))
    except OSError as exc:
        logger.warning("Could not write run_config: %s", exc)


_ERR_NOTE = re.compile(r"\berror\b\s*:", re.I)


def _notes_for_diary(notes, cap=200):
    """24l/C4: the diary keeps at most `cap` notes. A judge that crashed writes
    'xxx error: ...' into notes, and that is the ONLY record that it crashed - so
    error notes are never the ones dropped. Errors first (in order), then the rest
    in order, then the cap."""
    notes = [str(x) for x in notes]
    errs = [x for x in notes if _ERR_NOTE.search(x)]
    if len(notes) <= cap:
        return notes
    rest = [x for x in notes if not _ERR_NOTE.search(x)]
    keep = errs[:cap] + rest[:max(0, cap - len(errs))]
    # preserve original order for readability
    order = {id(x): i for i, x in enumerate(notes)}
    kept = set(map(id, keep))
    return [x for x in notes if id(x) in kept][:cap]


def _same_stamp(a, b) -> bool:
    """True when two timestamps mean the same instant (or the same naive text).

    Diary records are written by step 2 from snapshot.timestamp; the AI update arrives
    moments later with the same object. Comparing text alone breaks as soon as one side
    carries an offset and the other does not, so compare instants when both are aware.
    """
    if a is None or b is None:
        return False
    if hasattr(a, "isoformat") and hasattr(b, "isoformat"):
        try:
            if a.tzinfo is not None and b.tzinfo is not None:
                return abs((a - b).total_seconds()) < 1.0
        except Exception:
            pass
    sa, sb = str(getattr(a, "isoformat", lambda: a)()), str(getattr(b, "isoformat", lambda: b)())
    return sa[:19] == sb[:19]


def _update_last_diary_ai(hist_path, snap_ts, action, confidence, rationale, model) -> bool:
    """Write the AI vote into the LAST diary record, but only if it is the right one.

    Returns True when the record was updated. Never rewrites the whole file: it finds the
    start of the final line, truncates there and appends the corrected record, then flushes
    and fsyncs. Cost is independent of how long the diary has grown.
    """
    import json as _json2
    if not hist_path.exists() or hist_path.stat().st_size == 0:
        return False
    with open(hist_path, "r+b") as fh:
        size = fh.seek(0, 2)
        # walk backwards to the newline that starts the final record
        chunk, pos, start = 4096, size, 0
        tail = b""
        while pos > 0:
            step = min(chunk, pos)
            pos -= step
            fh.seek(pos)
            tail = fh.read(step) + tail
            stripped = tail.rstrip(b"\n")
            idx = stripped.rfind(b"\n")
            if idx != -1:
                start = pos + idx + 1
                break
        fh.seek(start)
        raw = fh.read().strip()
        if not raw:
            return False
        try:
            last = _json2.loads(raw.decode("utf-8", "replace"))
        except Exception:
            return False
        # A5: the record must be the one this AI answer is about
        if snap_ts is not None and not _same_stamp(_parse_iso_safe(last.get("timestamp")), snap_ts):
            logger.debug("AI vote not attached: last diary record is not this snapshot")
            return False
        if last.get("ai_action") is not None and last.get("ai_rationale"):
            logger.debug("AI vote not attached: this record already carries one")
            return False
        last["ai_action"] = action
        last["ai_confidence"] = confidence
        last["ai_rationale"] = rationale
        last["ai_model"] = model
        fh.seek(start)
        fh.truncate()
        fh.write((_json2.dumps(last) + "\n").encode("utf-8"))
        fh.flush()
        try:
            import os as _os2
            _os2.fsync(fh.fileno())
        except Exception:
            pass
    return True


def _parse_iso_safe(s):
    """Best-effort ISO parse; naive strings keep their old meaning (machine local)."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _append_diary(record: dict, hist_path) -> None:
    """Append one diary record: master file AND a per-day mirror.

    The master rotates at 50 MB keeping the last 20k lines, which silently dropped the
    oldest days and made their judge panel ungradeable. The per-day mirror
    (data/diary_<yyyymmdd>.jsonl) is what lets audit_day.py grade any day you still have.
    """
    line = json.dumps(record)
    with open(hist_path, "a", encoding="utf-8") as hf:
        hf.write(line + "\n")
    when = None
    try:
        when = datetime.fromisoformat(str(record.get("timestamp", "")).replace("Z", "+00:00"))
    except Exception:
        when = None
    when = (when or datetime.now().astimezone()).astimezone()
    mirror = DATA_DIR / f"diary_{when:%Y%m%d}.jsonl"
    try:
        with open(mirror, "a", encoding="utf-8") as mf:
            mf.write(line + "\n")
    except OSError as exc:
        logger.warning("Could not write per-day diary mirror: %s", exc)


def _today_counts() -> dict:
    """The day's counters, read back from the files the robot itself wrote."""
    out = {"decisions": 0, "executed": 0, "ai_calls": 0, "iterations": 0}
    today = f"{datetime.now():%Y-%m-%d}"
    try:
        p = DATA_DIR / "decisions_log.csv"
        if p.exists():
            with open(p, encoding="utf-8", errors="ignore", newline="") as f:
                for r in csv.DictReader(f):
                    if str(r.get("timestamp", ""))[:10] == today:
                        out["decisions"] += 1
                        if (r.get("exec_status") or "").upper() == "EXECUTED":
                            out["executed"] += 1
    except Exception:
        pass
    try:
        out["ai_calls"] = _ai_calls_today()
    except Exception:
        pass
    try:
        lp = LOGS_DIR / f"trading_{datetime.now():%Y%m%d}.log"
        if lp.exists():
            with open(lp, encoding="utf-8", errors="ignore") as f:
                out["iterations"] = sum(1 for line in f if "loop iteration" in line)
    except Exception:
        pass
    return out


def _session_end_summary() -> None:
    """The robot's own closing line: what the day holds, and how to grade it."""
    c = _today_counts()
    ymd, iso = f"{datetime.now():%Y%m%d}", f"{datetime.now():%Y-%m-%d}"
    logger.info("SESSION END | cycles=%d decisions=%d sent_to_mt5=%d ai_calls=%d | "
                "records written: decisions_log.csv, snapshots_history.jsonl (+ diary_%s.jsonl), "
                "run_config_%s.json | grade the day with: bash daily_check.sh %s",
                c["iterations"], c["decisions"], c["executed"], c["ai_calls"], ymd, ymd, iso)


def _log_session_start(mode: str) -> None:
    """The day's opening line: what this run is, with which rules, on which data."""
    logger.info("SESSION START | mode=%s pid=%d | gate=%s ai_gate=%s spread=%s window=%ss "
                "depth=%s | %s -> %s",
                mode, os.getpid(),
                getattr(config, "AI_CONFIDENCE_THRESHOLD", "?"),
                getattr(config, "AI_MIN_SIGNAL_STRENGTH", "?"),
                getattr(config, "V6_CFD_SPREAD_MAX", "?"),
                getattr(config, "BOOKMAP_WINDOW_SECONDS", "?"),
                getattr(config, "BOOKMAP_MAX_DEPTH_LEVELS", "?"),
                getattr(config, "DATA_SOURCE", "?"), getattr(config, "MT5_SYMBOL", "?"))


def run_doctor() -> int:
    """Run local readiness checks without starting the trading pipeline.

    This command does not call the AI, initialize/connect MT5, acquire the bot
    lock, write run-config/evidence files, edit .env, or submit orders. Importing
    the existing config module may ensure the empty data/logs folders exist.
    Live feed/account facts are clearly reported as unknown.
    """
    root = Path(__file__).resolve().parent
    blockers = 0
    print("GOLD BOOKMAP — LOCAL READINESS CHECK")
    print("Read-only: no AI request, MT5 connection, live-feed check, or order.")
    print("No .env or evidence writes; config import may ensure empty data/logs folders exist.")

    def report(level: str, message: str) -> None:
        nonlocal blockers
        print(f"[{level}] {message}")
        if level == "BLOCK":
            blockers += 1

    required_files = (
        "main.py", "config.py", "step1_data_acquisition.py",
        "step2_market_analysis.py", "step3_ai_decision.py",
        "step4_mt5_execution.py", "step5_monitoring.py",
        "requirements.txt",
    )
    for name in required_files:
        if (root / name).is_file():
            report("PASS", f"required file present: {name}")
        else:
            report("BLOCK", f"required file missing: {name}")

    env_file = root / ".env"
    if env_file.is_file():
        report("PASS", ".env exists (contents hidden; not changed)")
    else:
        report("WARN", ".env not found; configured defaults may be used")

    source = str(getattr(config, "DATA_SOURCE", "unknown") or "unknown").strip().lower()
    report("PASS" if source else "WARN",
           f"configured data source: {source or 'unknown'}")
    if source in {"bookmapbridge", "bookmap_bridge", "bookmap", "bmbridge",
                  "ntbridge", "ninjabridge", "ninja"}:
        bridge_path = str(getattr(config, "BOOKMAP_BRIDGE_FILE", "") or "").strip()
        if not bridge_path:
            report("WARN", "BookMap bridge path is not configured")
        else:
            try:
                exists = Path(bridge_path).expanduser().is_file()
            except (OSError, ValueError):
                exists = False
            report("PASS" if exists else "WARN",
                   "BookMap bridge file is present" if exists else
                   "configured BookMap bridge file is not visible from this folder")

    def module_available(name: str) -> bool:
        try:
            return importlib.util.find_spec(name) is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            return False

    for module in ("numpy", "pandas", "requests"):
        if module_available(module):
            report("PASS", f"required package importable: {module}")
        else:
            report("BLOCK", f"required package unavailable: {module}")

    if module_available("google.genai"):
        report("PASS", "AI client package is importable")
    else:
        report("WARN", "AI client package is missing; AI decisions may be unavailable")

    execution_mode = str(getattr(config, "EXECUTION_MODE", "none") or "none")
    trading_enabled = bool(getattr(config, "TRADING_ENABLED", False))
    live_flag = bool(getattr(config, "ALLOW_LIVE_TRADING", False))
    if execution_mode == "python" and not module_available("MetaTrader5"):
        report("WARN", "Python execution selected but MetaTrader5 package is not importable")
    if trading_enabled and execution_mode in {"python", "ea"}:
        report("WARN", "normal mode may open DEMO entries when all gates pass; "
               "ALLOW_LIVE_TRADING only guards REAL accounts")
    else:
        report("PASS", "current execution settings do not enable a new-entry route")
    report("INFO", f"execution mode={execution_mode}; TRADING_ENABLED={trading_enabled}; "
           f"ALLOW_LIVE_TRADING={live_flag}; actual MT5 account type not checked")
    report("INFO", "live feed freshness and broker connection are not tested by doctor")

    if blockers:
        print(f"RESULT: BLOCKED — {blockers} required local check(s) failed")
        return 1
    print("RESULT: local checks complete; this is not a live-feed/account certification")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Gold trading system pipeline")
    parser.add_argument("--doctor", action="store_true",
                        help="local read-only readiness check; does not start trading")
    parser.add_argument("--no-new-entries", action="store_true",
                        help="block new entries/signals; position management and monitoring continue")
    parser.add_argument("--loop", action="store_true",
                        help="run continuously (Step 5 monitoring loop)")
    args = parser.parse_args()

    if args.doctor:
        if args.loop or args.no_new_entries:
            parser.error("--doctor must be used by itself")
        return run_doctor()

    setup_logging()
    creds = config.credentials_configured()
    logger.info("Credentials: %s", creds)
    # v5.5 B2/B3/B6 status
    logger.info("B2 ML trainer: %s min_trades=%s | B3 limit queue: %s offset=%s ticks | B6 walk-forward: %s days MC %s",
                getattr(config, "ML_TRAINER_ENABLED", True),
                getattr(config, "ML_MIN_TRADES", 30),
                getattr(config, "LIMIT_ORDER_ENABLED", False),
                getattr(config, "LIMIT_OFFSET_TICKS", 1),
                getattr(config, "WALK_FORWARD_DAYS", 30),
                getattr(config, "WALK_FORWARD_MONTE_CARLO", 1000))
    logger.info("Runtime: broker=%s execution=%s trading=%s symbol=%s",
                config.BROKER_NAME or "(not configured)",
                config.EXECUTION_MODE,
                "enabled" if config.TRADING_ENABLED else "disabled",
                config.MT5_SYMBOL)

    # only ONE bot at a time (a second copy would double the work and fight
    # over the signal file). Refuse to start if another instance is running.
    if not _acquire_lock():
        print()
        print("  ⚠  ANOTHER GOLD TRADING BOT IS ALREADY RUNNING.")
        print("  This extra window will close automatically in 5 seconds.")
        print("  Keep the OTHER window — it is the real, running bot.")
        print()
        import time as _time
        _time.sleep(5)
        return 1

    _write_run_config()
    session_mode = "loop" if args.loop else "once"
    if args.no_new_entries:
        session_mode += "/no-new-entries"
        logger.warning("NO-NEW-ENTRIES mode: no new Python orders or actionable EA signals; "
                       "open-position management and monitoring continue.")
    _log_session_start(session_mode)

    def run_selected_cycle():
        return run_pipeline(allow_new_entries=not args.no_new_entries)

    try:
        if args.loop:
            from step5_monitoring import TradeMonitor
            TradeMonitor().run_loop(run_selected_cycle)
        else:
            run_selected_cycle()
    except KeyboardInterrupt:
        logger.info("SESSION interrupted from the console (Ctrl-C) - closing the day cleanly")
    finally:
        try:
            _session_end_summary()
        except Exception as _se:
            logger.warning("Could not write the session summary: %s", _se)
        _release_lock()
    return 0


if __name__ == "__main__":
    sys.exit(main())

