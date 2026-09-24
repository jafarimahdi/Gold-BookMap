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
    """L4: If price moves > FLASH_CRASH_ATR_MULT * ATR in FLASH_CRASH_MINUTES, trigger flatten."""
    if not getattr(config, "FLASH_CRASH_ENABLED", True):
        return ""
    try:
        price = float(getattr(snapshot, "price", 0.0) or 0.0)
        atr = float(getattr(getattr(snapshot, "volatility", None), "atr", 0.0) or 0.0)
        if price <= 0 or atr <= 0:
            return ""
        mult = float(getattr(config, "FLASH_CRASH_ATR_MULT", 3.0))
        minutes = float(getattr(config, "FLASH_CRASH_MINUTES", 1.0))
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
        if old_price is None:
            return ""
        move = abs(price - old_price)
        if move > mult * atr:
            return f"FLASH CRASH: price {old_price:.2f} -> {price:.2f} move {move:.2f} > {mult}*ATR {atr:.2f} in {minutes:.0f}min"
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

    # 4b) L5 RL adaptive: after 3 losses same regime, reduce size or pause
    try:
        if getattr(config, "RL_ENABLED", True):
            import trade_history as th
            # Get current regime from snapshot if available? Here we only have data, not snapshot.
            # For safety gates we check general loss streak (any regime)
            rl_res = th.check_loss_streak_regime("", within_hours=24.0)
            if rl_res.get("should_pause"):
                _record("SAFETY: RL_PAUSE", f"BLOCKED — {rl_res.get('reason')}")
                return False, rl_res.get('reason','RL pause: 4 losses streak')
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
    # default, i.e. above the 50 gate). Without this column a rule and the AI are
    # indistinguishable in every report ever written.
    "ai_model",
    "exec_status", "order_id", "news_state", "minutes_to_event",
    "next_event_title", "reason",
]


def log_decision(snapshot, decision, exec_result) -> None:
    """Append one row to data/decisions_log.csv (a decision journal)."""
    path = DATA_DIR / "decisions_log.csv"
    write_header = not path.exists() or path.stat().st_size == 0
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

    # 24k (A2): the day's own copy. decisions_log.csv is capped at
    # DECISIONS_LOG_MAX_ROWS (5000) and maintenance deletes the OLDEST rows - at ~850
    # decisions a day that silently erases the start of the history about every six
    # days, and a past-day audit would then honestly report "the robot logged nothing
    # that day". This mirror is per-day and never trimmed, so a graded day stays
    # gradeable forever. Same idea as diary_YYYYMMDD.jsonl, which has done this since 24f.
    try:
        _day_path = DATA_DIR / f"decisions_{datetime.now(timezone.utc):%Y%m%d}.csv"
        _day_header = not _day_path.exists() or _day_path.stat().st_size == 0
        with open(_day_path, "a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=_DECISION_LOG_FIELDS)
            if _day_header:
                writer.writeheader()
            writer.writerow(row)
    except OSError as exc:
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
            "notes": _notes_for_diary(getattr(snapshot, "notes", []) or []),
        }
        _append_diary(record, hist_path)
        # v5.0: rotate snapshots_history if >50MB to prevent big file crash
        try:
            if hist_path.stat().st_size > 50*1024*1024:
                # Keep last 20k lines (~20MB)
                lines = hist_path.read_text(encoding="utf-8").splitlines()[-20000:]
                hist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                logger.info(f"Rotated snapshots_history.jsonl >50MB -> kept last 20k")
        except:
            pass
    except Exception as he:
        try:
            logger.debug(f"snapshot history save failed: {he}")
        except:
            pass
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
                      f"{min_strength:.0f} (AI skipped)",
            model="none-weak-signal")
        _record("STEP 3  AI DECISION",
                f"HOLD (signal {strength:.1f} < {min_strength:.0f}, AI skipped)")
        return decision

    now = _time.time()
    if now - _LAST_AI_CALL < min_interval:
        wait = (min_interval - (now - _LAST_AI_CALL)) / 60.0
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
def run_pipeline() -> None:
    """One full pass through all 5 steps."""
    STATUS.clear()
    config.reload_env()   # pick up edited .env (credentials/markets) each cycle
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
    try:
        data = run_step1()
        _record_latency("STEP1 acquire", (time.time()-t0)*1000)
        if data:
            age = float(data.get("last_data_age_seconds", 0.0) or 0.0)
            _record_latency(f"BookMap feed age {age:.1f}s", age*1000)
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
    if data is not None:
        try:
            snapshot = run_step2(data)
            _record_latency("STEP2 analyze", (time.time()-t1)*1000)
            # L4 Flash crash kill switch check immediately after fresh snapshot
            crash_reason = _check_flash_crash(snapshot)
            if crash_reason:
                logger.error(f"L4 KILL SWITCH TRIGGERED: {crash_reason} -> flattening all")
                _record("SAFETY: FLASH_CRASH", f"TRIGGERED — {crash_reason}")
                try:
                    from position_manager import get_position_manager
                    # Force flatten via manage will handle session flatten? We do manual flatten here
                    import MetaTrader5 as mt5
                    if mt5 and config.mt5_initialize(mt5):
                        positions = mt5.positions_get(symbol=config.MT5_SYMBOL)
                        if positions:
                            for p in positions:
                                if int(getattr(p, "magic", -1)) == 234000:
                                    # close urgent
                                    tick = mt5.symbol_info_tick(config.MT5_SYMBOL)
                                    if tick:
                                        is_buy = p.type == mt5.POSITION_TYPE_BUY
                                        price = tick.bid if is_buy else tick.ask
                                        req = {
                                            "action": mt5.TRADE_ACTION_DEAL,
                                            "symbol": config.MT5_SYMBOL,
                                            "volume": float(p.volume),
                                            "type": mt5.ORDER_TYPE_SELL if is_buy else mt5.ORDER_TYPE_BUY,
                                            "position": int(p.ticket),
                                            "price": price,
                                            "deviation": 20,
                                            "magic": 234000,
                                            "comment": "flash crash flatten",
                                            "type_time": mt5.ORDER_TIME_GTC,
                                            "type_filling": mt5.ORDER_FILLING_IOC,
                                        }
                                        mt5.order_send(req)
                        mt5.shutdown()
                except Exception as fe:
                    logger.error(f"Flash crash flatten failed: {fe}")
        except Exception as exc:
            logger.exception("STEP 2 failed")
            _record("STEP 2  MARKET ANALYSIS", f"ERROR — {exc}")

    # STEP 3 (skipped entirely when the PAUSE file exists — saves AI quota)
    t2 = time.time()
    decision = None
    if _entries_paused():
        _record("STEP 3  AI DECISION", "PAUSED — PAUSE file present")
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
    # ------------------------------------------------------------------ #
    gate_ok, gate_reason = _safety_gates(data)
    if not gate_ok:
        from step3_ai_decision import Decision
        decision = Decision(action="HOLD", confidence=0.0,
                            rationale=f"safety gate: {gate_reason}")
        if snapshot is not None:
            snapshot.notes.append(gate_reason)

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
                    decision = _Dec(action="HOLD", confidence=0.0, rationale=f"RL pause: {reason}")
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

    total_ms = (time.time()-t_pipeline_start)*1000
    _record_latency("TOTAL pipeline", total_ms)
    if getattr(config, "LATENCY_LOG_ENABLED", True):
        logger.info(f"L4 Latency summary: total {total_ms:.0f}ms target {getattr(config,'LATENCY_TARGET_MS',500)}ms")

    print_summary()


def print_summary() -> None:
    print("\n" + "=" * 70)
    print("PIPELINE SUMMARY")
    print("=" * 70)
    for step, status in STATUS:
        print(f"  {step:<28} {status}")
    print("=" * 70)


def _env_snapshot() -> dict:
    """The values that decide behaviour, under their .env names (what audit_day.py reads)."""
    g = lambda k, d=None: getattr(config, k, d)
    gm = g("GEMINI_MODELS")
    return {
        "CONFIDENCE_THRESHOLD": g("AI_CONFIDENCE_THRESHOLD"),
        "AI_MIN_SIGNAL_STRENGTH": g("AI_MIN_SIGNAL_STRENGTH"),
        "V6_CFD_SPREAD_MAX": g("V6_CFD_SPREAD_MAX"),
        "BOOKMAP_WINDOW_SECONDS": g("BOOKMAP_WINDOW_SECONDS"),
        "BOOKMAP_MAX_DEPTH_LEVELS": g("BOOKMAP_MAX_DEPTH_LEVELS"),
        "GEMINI_MODEL": g("GEMINI_MODEL"),
        "GEMINI_MODELS": ",".join(gm) if isinstance(gm, (list, tuple)) else gm,
        "EXECUTION_MODE": g("EXECUTION_MODE"),
        "TRADING_ENABLED": g("TRADING_ENABLED"),
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Gold trading system pipeline")
    parser.add_argument("--loop", action="store_true",
                        help="run continuously (Step 5 monitoring loop)")
    args = parser.parse_args()

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
    _log_session_start("loop" if args.loop else "once")

    try:
        if args.loop:
            from step5_monitoring import TradeMonitor
            TradeMonitor().run_loop(run_pipeline)
        else:
            run_pipeline()
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
