"""
main.py
=======
Entry point — runs the complete 5-step gold trading pipeline.

    STEP 0 (config)  -> STEP 1 (data) -> STEP 2 (analysis)
          -> STEP 3 (AI) -> STEP 4 (execution) -> STEP 5 (monitoring)

Usage:
    python3 main.py                 # one full pass (demo data by default)
    python3 main.py --loop          # continuous loop (Step 5 run_loop)

Each step is isolated in try/except so a failure in one never takes down
the rest; the final summary prints the status of every step.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from pathlib import Path
from datetime import datetime

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


def _record(step: str, status: str) -> None:
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


def _safety_gates(data) -> tuple:
    """Return (allowed: bool, reason: str) for the pre-execution safety gates.

    Order of checks:
      0. master switch (TRADING_ENABLED=0 -> analyse only)
      1. trading session (weekend / holiday / daily break)
      2. stale feed (Rithmic silent for too long)
      3. risk circuit-breaker (daily loss / max drawdown)
    """
    from session import is_market_open, describe_now

    # 0) master on/off switch
    if not config.TRADING_ENABLED:
        _record("SAFETY: SWITCH", "TRADING OFF (TRADING_ENABLED=0) — analyse only")
        return False, "trading disabled (TRADING_ENABLED=0 in .env)"

    # 1) session
    if not is_market_open():
        _record("SAFETY: SESSION", f"CLOSED — {describe_now()} — no trading")
        return False, f"market closed ({describe_now()})"

    # 2) stale feed (only meaningful for live providers)
    if config.DATA_SOURCE not in ("demo", "replay") and data is not None:
        age = float(data.get("last_data_age_seconds", 0.0) or 0.0)
        if not data.get("has_data") or age > config.STALE_DATA_SECONDS:
            _record("SAFETY: FEED", f"STALE/EMPTY — age {age:.0f}s — no trading")
            return False, f"no fresh data (age {age:.0f}s)"

    # 2b) spread guard — brokers widen the spread at news/low liquidity;
    #     trading into a wide spread is an instant loss of edge.
    if data is not None:
        spread = float(data.get("spread_pct") or 0.0)
        if 0 < spread and config.MAX_SPREAD_PCT and spread > config.MAX_SPREAD_PCT:
            _record("SAFETY: SPREAD", f"too wide {spread:.3f}% > "
                    f"{config.MAX_SPREAD_PCT:.3f}% — no trading")
            return False, f"spread too wide ({spread:.3f}%)"

    # 3) risk limits
    try:
        from risk_manager import RiskManager
        ok, reason = RiskManager().check()
        if not ok:
            _record("SAFETY: RISK", f"HALTED — {reason}")
            return False, f"risk halt: {reason}"
    except Exception as exc:
        logger.warning("Risk check failed (proceeding): %s", exc)

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
    "exec_status", "order_id", "news_state", "minutes_to_event",
    "next_event_title", "reason",
]


def log_decision(snapshot, decision, exec_result) -> None:
    """Append one row to data/decisions_log.csv (a decision journal)."""
    path = DATA_DIR / "decisions_log.csv"
    write_header = not path.exists() or path.stat().st_size == 0
    row = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "symbol": getattr(snapshot, "symbol", config.SYMBOL),
        "price": getattr(snapshot, "price", 0.0),
        "signal_direction": getattr(snapshot, "signal_direction", ""),
        "signal_strength": getattr(snapshot, "signal_strength", 0.0),
        "signal_confidence": getattr(snapshot, "confidence", 0.0),
        "regime": getattr(snapshot, "regime", ""),
        "divergence": getattr(snapshot, "divergence", 0.0),
        "ai_action": getattr(decision, "action", ""),
        "ai_confidence": getattr(decision, "confidence", 0.0),
        "exec_status": getattr(exec_result, "status", ""),
        "order_id": getattr(exec_result, "order_id", ""),
        "news_state": getattr(getattr(snapshot, "news", None), "news_state", ""),
        "minutes_to_event": getattr(getattr(snapshot, "news", None),
                                    "minutes_to_next_event", ""),
        "next_event_title": getattr(getattr(snapshot, "news", None),
                                    "next_event_title", ""),
        "reason": getattr(exec_result, "reason", ""),
    }
    try:
        with open(path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_DECISION_LOG_FIELDS)
            if write_header:
                writer.writeheader()
            writer.writerow(row)
        logger.info("Decision logged -> %s", path)
    except OSError as exc:
        logger.warning("Could not write decision log: %s", exc)


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


def run_step2(data):
    from step2_market_analysis import (analyze_market, format_snapshot,
                                       snapshot_to_json)
    snapshot = analyze_market(data)
    print(format_snapshot(snapshot))
    out = DATA_DIR / "market_snapshot.json"
    out.write_text(snapshot_to_json(snapshot))
    logger.info("STEP 2: snapshot saved -> %s", out)
    _record("STEP 2  MARKET ANALYSIS",
            f"OK -> {snapshot.signal_direction} "
            f"(strength {snapshot.signal_strength:.1f}, "
            f"confidence {snapshot.confidence:.1f})")
    return snapshot


_LAST_AI_CALL = 0.0   # timestamp of the last Gemini call (throttling)
_AI_STATE_FILE = None


def _ai_state_path():
    global _AI_STATE_FILE
    if _AI_STATE_FILE is None:
        _AI_STATE_FILE = DATA_DIR / "ai_calls.json"
    return _AI_STATE_FILE


def _ai_calls_today() -> int:
    """How many Gemini calls we have already made today (persisted)."""
    import json
    path = _ai_state_path()
    try:
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
            if state.get("date") == datetime.now().date().isoformat():
                return int(state.get("calls", 0) or 0)
    except (json.JSONDecodeError, OSError):
        pass
    return 0


def _record_ai_call() -> None:
    """Persist one more Gemini call for today (resets at midnight)."""
    import json
    try:
        _ai_state_path().write_text(
            json.dumps({"date": datetime.now().date().isoformat(),
                        "calls": _ai_calls_today() + 1}),
            encoding="utf-8")
    except OSError:
        pass


def run_step3(snapshot):
    """Consult the AI — but only when it is worth it.

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

    if strength < min_strength:
        decision = Decision(
            action="HOLD", confidence=0.0,
            rationale=f"signal {strength:.1f} below AI threshold "
                      f"{min_strength:.0f} (AI skipped)")
        _record("STEP 3  AI DECISION",
                f"HOLD (signal {strength:.1f} < {min_strength:.0f}, AI skipped)")
        return decision

    now = _time.time()
    if now - _LAST_AI_CALL < min_interval:
        wait = (min_interval - (now - _LAST_AI_CALL)) / 60.0
        decision = Decision(
            action="HOLD", confidence=0.0,
            rationale=f"AI throttled ({wait:.0f} min until next call)")
        _record("STEP 3  AI DECISION",
                f"HOLD (AI throttled, {wait:.0f} min until next call)")
        return decision

    if max_per_day > 0 and _ai_calls_today() >= max_per_day:
        decision = Decision(
            action="HOLD", confidence=0.0,
            rationale=f"daily AI cap reached ({max_per_day})")
        _record("STEP 3  AI DECISION",
                f"HOLD (daily AI cap {max_per_day} reached)")
        return decision

    _LAST_AI_CALL = now
    _record_ai_call()
    decision = AIDecisionEngine().decide(snapshot)
    _record("STEP 3  AI DECISION", f"{decision.action} @ {decision.confidence:.1f}%")
    return decision


def run_step4(decision, snapshot):
    """Route execution to exactly one owner: none, Python, or EA."""
    from step4_mt5_execution import ExecutionResult, MT5Executor

    mode = getattr(config, "EXECUTION_MODE", "none")
    if not config.TRADING_ENABLED:
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
        result = MT5Executor().execute(decision, snapshot)
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


def run_step5():
    from step5_monitoring import TradeMonitor
    positions = TradeMonitor().single_pass()
    _record("STEP 5  MONITORING", "OK")
    return positions


def run_signal_bridge(snapshot, decision) -> None:
    """Write the MT5 signal file, with EA as the only actionable owner."""
    from mt5_signal_bridge import write_signal
    from step3_ai_decision import Decision

    mode = getattr(config, "EXECUTION_MODE", "none")
    if mode != "ea":
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
        "timestamp": _dt.now().isoformat(timespec="seconds"),
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
def run_pipeline() -> None:
    """One full pass through all 5 steps."""
    STATUS.clear()
    config.reload_env()   # pick up edited .env (credentials/markets) each cycle
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
    try:
        data = run_step1()
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
    snapshot = None
    if data is not None:
        try:
            snapshot = run_step2(data)
        except Exception as exc:
            logger.exception("STEP 2 failed")
            _record("STEP 2  MARKET ANALYSIS", f"ERROR — {exc}")

    # STEP 3 (skipped entirely when the PAUSE file exists — saves AI quota)
    decision = None
    if _entries_paused():
        _record("STEP 3  AI DECISION", "PAUSED — PAUSE file present")
    elif snapshot is not None:
        try:
            decision = run_step3(snapshot)
        except Exception as exc:
            logger.exception("STEP 3 failed")
            _record("STEP 3  AI DECISION", f"ERROR — {exc}")

    # ------------------------------------------------------------------ #
    # SAFETY GATES before execution: session, stale feed, risk limits.
    # On weekends / holidays / feed drops / big losses the bot must NOT trade.
    # ------------------------------------------------------------------ #
    gate_ok, gate_reason = _safety_gates(data)
    if not gate_ok:
        from step3_ai_decision import Decision
        decision = Decision(action="HOLD", confidence=0.0,
                            rationale=f"safety gate: {gate_reason}")
        if snapshot is not None:
            snapshot.notes.append(gate_reason)

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

    # STEP 4
    exec_result = None
    if decision is not None:
        try:
            exec_result = run_step4(decision, snapshot)
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
            log_trade_outcome(
                order_id=deal_id or deal.get("position_id"),
                symbol=deal.get("symbol", config.MT5_SYMBOL),
                side=deal.get("side", deal.get("type", "")),
                pnl=deal.get("pnl", 0.0),
                exit_price=deal.get("price", 0.0),
                comment="closed deal")
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
            run_signal_bridge(snapshot, decision)
        except Exception as exc:
            logger.warning("MT5 signal bridge failed: %s", exc)

    # decision journal (for later tuning / backtesting)
    if snapshot is not None and decision is not None:
        try:
            log_decision(snapshot, decision, exec_result)
        except Exception as exc:
            logger.warning("decision log failed: %s", exc)

    print_summary()


def print_summary() -> None:
    print("\n" + "=" * 70)
    print("PIPELINE SUMMARY")
    print("=" * 70)
    for step, status in STATUS:
        print(f"  {step:<28} {status}")
    print("=" * 70)


def main() -> int:
    parser = argparse.ArgumentParser(description="Gold trading system pipeline")
    parser.add_argument("--loop", action="store_true",
                        help="run continuously (Step 5 monitoring loop)")
    args = parser.parse_args()

    setup_logging()
    creds = config.credentials_configured()
    logger.info("Credentials: %s", creds)
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

    try:
        if args.loop:
            from step5_monitoring import TradeMonitor
            TradeMonitor().run_loop(run_pipeline)
        else:
            run_pipeline()
    finally:
        _release_lock()
    return 0


if __name__ == "__main__":
    sys.exit(main())
