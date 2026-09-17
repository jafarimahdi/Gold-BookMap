"""
config.py
=========
STEP 0: Configuration for Gold Trading System — BOOKMAP EDITION

Loads values from a `.env` file and exposes them as module-level constants.
This is the BookMap port: data comes from BookMap platform via bookmap_addon.py
instead of NinjaTrader.

`reload_env()` re-reads `.env` AND re-binds all constants, so editing `.env`
takes effect in a running process without a restart.

BACKWARD COMPAT: All NT_* keys are still read as fallbacks for BOOKMAP_* keys,
so an old .env keeps working after the port. New installs should use BOOKMAP_*.
"""

from __future__ import annotations

import os
from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent
LOGS_DIR = BASE_DIR / "logs"
DATA_DIR = BASE_DIR / "data"

for _d in (LOGS_DIR, DATA_DIR):
    _d.mkdir(exist_ok=True)


_DOTENV_KEYS = set()


def _load_dotenv(path: Path) -> None:
    global _DOTENV_KEYS
    if not path.exists():
        for k in _DOTENV_KEYS:
            os.environ.pop(k, None)
        _DOTENV_KEYS = set()
        return

    file_keys = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        file_keys.add(line.split("=", 1)[0].strip())

    for k in (_DOTENV_KEYS - file_keys):
        os.environ.pop(k, None)
    _DOTENV_KEYS = file_keys

    try:
        from dotenv import load_dotenv
        load_dotenv(path, override=True)
        return
    except ImportError:
        pass
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        value = line.split("=", 1)[1]
        if "#" in value:
            cut = len(value)
            for i, ch in enumerate(value):
                if ch == "#" and i > 0 and value[i - 1] in (" ", "\t"):
                    cut = i
                    break
            value = value[:cut]
        key = line.split("=", 1)[0]
        os.environ[key.strip()] = value.strip().strip('"').strip("'")

def _fget(name: str, default: str = "") -> str:
    return os.getenv(name, default)

def _fget_first(names, default=""):
    """Return first non-empty env var from list."""
    for n in names:
        v = os.getenv(n)
        if v is not None and v.strip() != "":
            return v
    return default

def _fint(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(float(raw.strip()))
    except ValueError:
        return default

def _fint_first(names, default: int) -> int:
    for n in names:
        raw = os.getenv(n)
        if raw is not None and raw.strip() != "":
            try:
                return int(float(raw.strip()))
            except ValueError:
                continue
    return default

def _ffloat(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default

def _ffloat_first(names, default: float) -> float:
    for n in names:
        raw = os.getenv(n)
        if raw is not None and raw.strip() != "":
            try:
                return float(raw.strip())
            except ValueError:
                continue
    return default


def _refresh() -> None:
    g = globals()

    g["DEBUG"] = _fget("DEBUG", "0") == "1"
    g["BROKER_NAME"] = _fget("BROKER_NAME", "")
    g["MT5_TERMINAL_PATH"] = _fget("MT5_TERMINAL_PATH", "")
    g["SYMBOL"] = _fget("TRADING_SYMBOL", "XAUUSD")
    g["MT5_SYMBOL"] = _fget("MT5_SYMBOL", g["SYMBOL"])
    g["TIMEFRAME"] = _fget("TIMEFRAME", "M1")
    g["DATA_SOURCE"] = _fget("DATA_SOURCE", "demo").lower()
    # Normalize legacy name: ninjabridge -> bookmapbridge (still supported)
    if g["DATA_SOURCE"] == "ninjabridge":
        g["DATA_SOURCE"] = "bookmapbridge"
    g["DATA_SYMBOL"] = _fget("DATA_SYMBOL") or _fget("RITHMIC_SYMBOL", "GC 12-26")
    g["DATA_MARKET"] = _fget("DATA_MARKET", "")
    g["TRADE_MARKET"] = _fget("TRADE_MARKET", "")

    g["TRADING_ENABLED"] = _fget("TRADING_ENABLED", "1") == "1"

    mode = _fget("EXECUTION_MODE", "none").strip().lower()
    g["EXECUTION_MODE"] = mode if mode in ("none", "python", "ea") else "none"
    g["ALLOW_LIVE_TRADING"] = _fget("ALLOW_LIVE_TRADING", "0") == "1"

    # ---- STEP 2 analysis parameters ----------------------------------------
    g["PRICE_RESOLUTION"] = _ffloat("PRICE_RESOLUTION", 0.1)
    g["LARGE_ORDER_THRESHOLD"] = _ffloat("LARGE_ORDER_THRESHOLD", 100.0)
    g["CORRELATION_WINDOW"] = _fint("CORRELATION_WINDOW", 30)
    g["ECONOMIC_CALENDAR_TIMEOUT"] = _fint("ECONOMIC_CALENDAR_TIMEOUT", 8)

    g["CONFIRM_ENABLED"] = _fget("CONFIRM_ENABLED", "1") == "1"
    g["CONFIRM_TIMEFRAMES"] = _fget("CONFIRM_TIMEFRAMES", "H1,M15,M5")
    g["MAX_SPREAD_PCT"] = _ffloat("MAX_SPREAD_PCT", 0.05)

    g["ORDER_BLOCKS_ENABLED"] = _fget("ORDER_BLOCKS_ENABLED", "1") == "1"

    g["COOLDOWN_MINUTES"] = _fint("COOLDOWN_MINUTES", 15)
    g["MAX_TRADES_PER_DAY"] = _fint("MAX_TRADES_PER_DAY", 20)

    # ---- STEP 3 / STEP 4 thresholds -----------------------------------------
    g["AI_CONFIDENCE_THRESHOLD"] = _ffloat("CONFIDENCE_THRESHOLD", 70.0)
    g["AI_MIN_SIGNAL_STRENGTH"] = _ffloat("AI_MIN_SIGNAL_STRENGTH", 10.0)
    g["AI_MIN_INTERVAL_MINUTES"] = _fint("AI_MIN_INTERVAL_MINUTES", 1)
    g["AI_MAX_CALLS_PER_DAY"] = _fint("AI_MAX_CALLS_PER_DAY", 2000)
    g["AI_KEY_COOLDOWN_MINUTES"] = _fint("AI_KEY_COOLDOWN_MINUTES", 20)
    g["GEMINI_REQUEST_TIMEOUT_MS"] = _fint("GEMINI_REQUEST_TIMEOUT_MS", 20000)
    g["EXECUTION_VERIFY_SECONDS"] = _fint("EXECUTION_VERIFY_SECONDS", 5)
    g["RISK_PER_TRADE_PCT"] = _ffloat("RISK_PER_TRADE_PCT", 1.0)
    g["STOP_LOSS_ATR_MULT"] = _ffloat("STOP_LOSS_ATR_MULT", 1.5)
    g["TAKE_PROFIT_ATR_MULT"] = _ffloat("TAKE_PROFIT_ATR_MULT", 3.0)
    g["LOT_SIZE"] = _ffloat("LOT_SIZE", 0.1)

    g["CONTRACT_SIZE"] = _ffloat("CONTRACT_SIZE", 100.0)
    g["ACCOUNT_EQUITY"] = _ffloat("ACCOUNT_EQUITY", 10000.0)
    g["MAX_LOT_SIZE"] = _ffloat("MAX_LOT_SIZE", 1.0)

    # ---- news-time behaviour ------------------------------------------------
    g["NEWS_WARNING_MINUTES"] = _ffloat("NEWS_WARNING_MINUTES", 30.0)
    g["NEWS_BLACKOUT_BEFORE_MINUTES"] = _ffloat("NEWS_BLACKOUT_BEFORE_MINUTES", 15.0)
    g["NEWS_BLACKOUT_AFTER_MINUTES"] = _ffloat("NEWS_BLACKOUT_AFTER_MINUTES", 30.0)
    g["NEWS_PERIMETER_IGNORE_LOW"] = _fget("NEWS_PERIMETER_IGNORE_LOW", "1") == "1"
    g["NEWS_WIDEN_STOP_MULT"] = _ffloat("NEWS_WIDEN_STOP_MULT", 1.5)
    g["NEWS_REDUCE_SIZE_PCT"] = _ffloat("NEWS_REDUCE_SIZE_PCT", 0.5)

    # ---- v4 position manager ------------------------------------------------
    g["PM_ENABLE"] = _fget("PM_ENABLE", "1") == "1"
    g["PM_BE_TRIGGER_R"] = _ffloat("PM_BE_TRIGGER_R", 1.0)
    g["PM_TRAIL_ENABLE"] = _fget("PM_TRAIL_ENABLE", "1") == "1"
    g["PM_FLIP_EXIT_SCORE"] = _ffloat("PM_FLIP_EXIT_SCORE", 55.0)
    g["PM_FLIP_REQUIRE_FLOW"] = _fget("PM_FLIP_REQUIRE_FLOW", "1") == "1"
    g["PM_DIVERGENCE_EXIT"] = _fget("PM_DIVERGENCE_EXIT", "1") == "1"
    g["PM_DIVERGENCE_MIN_R"] = _ffloat("PM_DIVERGENCE_MIN_R", 0.3)
    g["PM_TIME_STOP_MINUTES"] = _fint("PM_TIME_STOP_MINUTES", 90)
    g["PM_TIME_STOP_MIN_PROGRESS"] = _ffloat("PM_TIME_STOP_MIN_PROGRESS", 0.2)
    g["PM_MIN_SL_ATR"] = _ffloat("PM_MIN_SL_ATR", 1.2)
    g["PM_MAX_SL_ATR"] = _ffloat("PM_MAX_SL_ATR", 3.0)
    g["PM_ZONE_BUFFER_ATR"] = _ffloat("PM_ZONE_BUFFER_ATR", 0.25)
    g["PM_TP_BUFFER_ATR"] = _ffloat("PM_TP_BUFFER_ATR", 0.15)
    g["PM_ROUND_HUNT_BUFFER_ATR"] = _ffloat("PM_ROUND_HUNT_BUFFER_ATR", 0.12)
    g["PM_TP_MIN_ATR"] = _ffloat("PM_TP_MIN_ATR", 1.0)
    g["PM_TP_MAX_ATR"] = _ffloat("PM_TP_MAX_ATR", 4.0)
    g["PM_MODIFY_COOLDOWN_SECONDS"] = _fint("PM_MODIFY_COOLDOWN_SECONDS", 180)

    g["PM_HTF_POC_ENABLE"] = _fget("PM_HTF_POC_ENABLE", "1") == "1"
    g["PM_FP_ZONES_ENABLE"] = _fget("PM_FP_ZONES_ENABLE", "1") == "1"
    g["PM_FP_IMB_RATIO"] = _ffloat("PM_FP_IMB_RATIO", 3.0)
    g["PM_FP_MIN_RUN"] = _fint("PM_FP_MIN_RUN", 3)
    g["PM_FLOW_ENABLE"] = _fget("PM_FLOW_ENABLE", "1") == "1"
    g["PM_FLOW_LOOKBACK_MINUTES"] = _fint("PM_FLOW_LOOKBACK_MINUTES", 5)
    g["PM_FLOW_WEAK_BE_R"] = _ffloat("PM_FLOW_WEAK_BE_R", 0.5)
    g["PM_VWAP_ENABLE"] = _fget("PM_VWAP_ENABLE", "1") == "1"
    g["PM_VWAP_TREND_Z"] = _ffloat("PM_VWAP_TREND_Z", 0.5)
    g["PM_VWAP_STRETCH_Z"] = _ffloat("PM_VWAP_STRETCH_Z", 2.0)
    g["PM_VWAP_TRAIL_BUFFER_ATR"] = _ffloat("PM_VWAP_TRAIL_BUFFER_ATR", 0.25)

    g["PM_NEWS_PROTECT_ENABLE"] = _fget("PM_NEWS_PROTECT_ENABLE", "1") == "1"
    g["PM_NEWS_PROTECT_MINUTES"] = _ffloat("PM_NEWS_PROTECT_MINUTES", 10.0)
    g["PM_NEWS_PROTECT_MODE"] = _fget("PM_NEWS_PROTECT_MODE", "tighten")
    g["PM_DAILY_FLATTEN_UTC"] = _fget("PM_DAILY_FLATTEN_UTC", "21:30")
    g["PM_ACTION_MAX_SPREAD_PCT"] = _ffloat("PM_ACTION_MAX_SPREAD_PCT", 0.05)

    g["PM_PROFIT_LOCK_ENABLE"] = _fget("PM_PROFIT_LOCK_ENABLE", "1") == "1"
    g["PM_PROFIT_LOCK_MIN_ATR"] = _ffloat("PM_PROFIT_LOCK_MIN_ATR", 1.0)
    g["PM_PROFIT_GIVEBACK"] = _ffloat("PM_PROFIT_GIVEBACK", 0.5)
    g["PM_MOMENTUM_EXIT_ENABLE"] = _fget("PM_MOMENTUM_EXIT_ENABLE", "1") == "1"
    g["PM_MOMENTUM_MIN_R"] = _ffloat("PM_MOMENTUM_MIN_R", 0.3)
    g["PM_MOMENTUM_SIGNAL"] = _ffloat("PM_MOMENTUM_SIGNAL", 40.0)

    g["PM_PARTIAL_EXIT_ENABLE"] = _fget("PM_PARTIAL_EXIT_ENABLE", "1") == "1"
    g["PM_PARTIAL_TRIGGER_R"] = _ffloat("PM_PARTIAL_TRIGGER_R", 1.0)
    g["PM_PARTIAL_FRACTION"] = _ffloat("PM_PARTIAL_FRACTION", 0.5)
    g["PM_REGIME_ADAPTIVE"] = _fget("PM_REGIME_ADAPTIVE", "1") == "1"
    g["PM_REGIME_LOCK_ATR"] = _ffloat("PM_REGIME_LOCK_ATR", 0.75)
    g["PM_REGIME_GIVEBACK"] = _ffloat("PM_REGIME_GIVEBACK", 0.40)
    g["PM_MACRO_DEFENSE"] = _fget("PM_MACRO_DEFENSE", "1") == "1"
    g["PM_MACRO_OPP_THRESHOLD"] = _ffloat("PM_MACRO_OPP_THRESHOLD", 0.3)
    g["PM_MACRO_LOCK_ATR"] = _ffloat("PM_MACRO_LOCK_ATR", 0.75)
    g["PM_MACRO_GIVEBACK"] = _ffloat("PM_MACRO_GIVEBACK", 0.40)

    g["ENTRY_MAX_REAL_RISK_PCT"] = _ffloat("ENTRY_MAX_REAL_RISK_PCT", 1.5)
    g["ENTRY_MIN_TP_SPREAD_MULT"] = _ffloat("ENTRY_MIN_TP_SPREAD_MULT", 3.0)
    g["ENTRY_LOSS_MEMORY_MINUTES"] = _ffloat("ENTRY_LOSS_MEMORY_MINUTES", 30.0)
    g["ENTRY_LOSS_MEMORY_SCORE_PENALTY"] = _ffloat("ENTRY_LOSS_MEMORY_SCORE_PENALTY", 5.0)
    g["ENTRY_DAY_RATCHET_ENABLE"] = _fget("ENTRY_DAY_RATCHET_ENABLE", "1") == "1"
    g["ENTRY_RATCHET_1_PCT"] = _ffloat("ENTRY_RATCHET_1_PCT", 1.0)
    g["ENTRY_RATCHET_1_PENALTY"] = _ffloat("ENTRY_RATCHET_1_PENALTY", 5.0)
    g["ENTRY_RATCHET_2_PCT"] = _ffloat("ENTRY_RATCHET_2_PCT", 2.0)
    g["ENTRY_RATCHET_2_PENALTY"] = _ffloat("ENTRY_RATCHET_2_PENALTY", 10.0)

    g["SIGNAL_BUY_THRESHOLD"] = _ffloat("SIGNAL_BUY_THRESHOLD", 15.0)
    g["SIGNAL_SELL_THRESHOLD"] = _ffloat("SIGNAL_SELL_THRESHOLD", -15.0)
    g["SIGNAL_W_H1"] = _ffloat("SIGNAL_W_H1", 1.0)
    g["SIGNAL_W_M15"] = _ffloat("SIGNAL_W_M15", 0.8)
    g["SIGNAL_W_M5"] = _ffloat("SIGNAL_W_M5", 0.6)
    g["SIGNAL_W_TREND"] = _ffloat("SIGNAL_W_TREND", 0.5)
    g["SIGNAL_W_MACD"] = _ffloat("SIGNAL_W_MACD", 0.6)
    g["SIGNAL_W_EMA_CROSS"] = _ffloat("SIGNAL_W_EMA_CROSS", 0.5)
    g["SIGNAL_W_SMA50"] = _ffloat("SIGNAL_W_SMA50", 0.7)
    g["SIGNAL_W_SMA20"] = _ffloat("SIGNAL_W_SMA20", 0.5)
    g["SIGNAL_W_PRESSURE"] = _ffloat("SIGNAL_W_PRESSURE", 0.8)
    g["SIGNAL_W_CVD"] = _ffloat("SIGNAL_W_CVD", 0.6)
    g["SIGNAL_W_BIDASK"] = _ffloat("SIGNAL_W_BIDASK", 0.6)
    g["SIGNAL_W_OFI"] = _ffloat("SIGNAL_W_OFI", 0.9)
    g["SIGNAL_W_DEPTH"] = _ffloat("SIGNAL_W_DEPTH", 0.7)
    g["SIGNAL_W_MICRO"] = _ffloat("SIGNAL_W_MICRO", 0.5)
    g["SIGNAL_W_ABSORB"] = _ffloat("SIGNAL_W_ABSORB", 0.5)
    g["SIGNAL_W_FOOTPRINT"] = _ffloat("SIGNAL_W_FOOTPRINT", 0.7)
    g["SIGNAL_W_L3_IMB"] = _ffloat("SIGNAL_W_L3_IMB", 0.6)
    g["SIGNAL_W_L3_OFI"] = _ffloat("SIGNAL_W_L3_OFI", 0.8)
    g["SIGNAL_W_L3_AGGR"] = _ffloat("SIGNAL_W_L3_AGGR", 0.8)
    g["SIGNAL_W_DIVERGENCE"] = _ffloat("SIGNAL_W_DIVERGENCE", 1.2)
    g["SIGNAL_W_VWAP"] = _ffloat("SIGNAL_W_VWAP", 0.6)

    g["NEWS_ENABLED"] = _fget("NEWS_ENABLED", "1") == "1"
    g["NEWS_CACHE_MINUTES"] = _fint("NEWS_CACHE_MINUTES", 15)
    g["NEWS_MAX_HEADLINES"] = _fint("NEWS_MAX_HEADLINES", 20)
    g["NEWS_TIMEOUT"] = _fint("NEWS_TIMEOUT", 8)

    g["MACRO_ENABLED"] = _fget("MACRO_ENABLED", "1") == "1"
    g["MACRO_CACHE_MINUTES"] = _fint("MACRO_CACHE_MINUTES", 15)
    g["MACRO_TIMEOUT"] = _fint("MACRO_TIMEOUT", 8)

    g["MONITOR_POLL_SECONDS"] = _fint("MONITOR_POLL_SECONDS", 60)

    g["SESSION_ENFORCE"] = _fget("SESSION_ENFORCE", "1") == "1"
    g["TRADING_DAYS"] = _fget("TRADING_DAYS", "0,1,2,3,4")
    g["DAILY_BREAK_START"] = _fget("DAILY_BREAK_START", "")
    g["DAILY_BREAK_END"] = _fget("DAILY_BREAK_END", "")
    g["STALE_DATA_SECONDS"] = _fint("STALE_DATA_SECONDS", 300)

    g["DAILY_LOSS_LIMIT_PCT"] = _ffloat("DAILY_LOSS_LIMIT_PCT", 3.0)
    g["MAX_DRAWDOWN_PCT"] = _ffloat("MAX_DRAWDOWN_PCT", 10.0)

    g["RECORD_DATA"] = _fget("RECORD_DATA", "0") == "1"
    g["REPLAY_FILE"] = _fget("REPLAY_FILE", "")

    g["MAINTENANCE_ENABLED"] = _fget("MAINTENANCE_ENABLED", "1") == "1"
    g["LOG_RETENTION_DAYS"] = _fint("LOG_RETENTION_DAYS", 7)
    g["DECISIONS_LOG_MAX_ROWS"] = _fint("DECISIONS_LOG_MAX_ROWS", 5000)
    g["OUTCOMES_LOG_MAX_ROWS"] = _fint("OUTCOMES_LOG_MAX_ROWS", 2000)
    g["RECORD_RETENTION_DAYS"] = _fint("RECORD_RETENTION_DAYS", 30)

    g["SPREAD_ALERT_PCT"] = _ffloat("SPREAD_ALERT_PCT", 2.0)

    g["DATABENTO_API_KEY"] = _fget("DATABENTO_API_KEY", "")
    g["RITHMIC_USERNAME"] = _fget("RITHMIC_USERNAME", "")
    g["RITHMIC_PASSWORD"] = _fget("RITHMIC_PASSWORD", "")
    g["GEMINI_API_KEY"] = _fget("GEMINI_API_KEY", "")
    g["GEMINI_MODEL"] = _fget("GEMINI_MODEL", "gemini-3.7-flash")
    g["GEMINI_MODELS"] = [m.strip() for m in _fget(
        "GEMINI_MODELS",
        "gemini-3.7-flash,gemini-3.5-flash,gemini-3.6-flash,gemini-2.5-flash"
    ).split(",") if m.strip()]
    _gemini_keys = [_fget("GEMINI_API_KEY", "")]
    for _n in range(2, 21):
        _gemini_keys.append(_fget("GEMINI_API_KEY_%d" % _n, ""))
    g["GEMINI_API_KEYS"] = [k for k in _gemini_keys if k]
    g["MT5_LOGIN"] = _fint("MT5_LOGIN", 0)
    g["MT5_PASSWORD"] = _fget("MT5_PASSWORD", "")
    g["MT5_SERVER"] = _fget("MT5_SERVER", "")

    g["RITHMIC_SYSTEM"] = _fget("RITHMIC_SYSTEM", "Rithmic Paper Trading")
    g["RITHMIC_APP_NAME"] = _fget("RITHMIC_APP_NAME", "GoldTradingBot")
    g["RITHMIC_APP_VERSION"] = _fget("RITHMIC_APP_VERSION", "1.0.0")
    g["RITHMIC_LIB"] = _fget("RITHMIC_LIB", "async_rithmic")
    g["RITHMIC_SYMBOL"] = _fget("RITHMIC_SYMBOL", "GC")
    g["RITHMIC_EXCHANGE"] = _fget("RITHMIC_EXCHANGE", "COMEX")
    g["RITHMIC_URL"] = _fget("RITHMIC_URL", "rprotocol.rithmic.com:443")

    # ---- BookMap bridge (PRIMARY DATA SOURCE) ----------------------------
    # New keys — with NT_* fallbacks for backward compatibility
    g["BOOKMAP_BRIDGE_FILE"] = _fget_first(["BOOKMAP_BRIDGE_FILE", "NT_BRIDGE_FILE", "BM_BRIDGE_FILE"], "")
    g["NT_BRIDGE_FILE"] = g["BOOKMAP_BRIDGE_FILE"]  # alias for old code
    g["BOOKMAP_WINDOW_SECONDS"] = _fint_first(["BOOKMAP_WINDOW_SECONDS", "NT_WINDOW_SECONDS"], 28800)
    g["NT_WINDOW_SECONDS"] = g["BOOKMAP_WINDOW_SECONDS"]
    g["BOOKMAP_CATCHUP_MB"] = _fint_first(["BOOKMAP_CATCHUP_MB", "NT_CATCHUP_MB"], 64)
    g["NT_CATCHUP_MB"] = g["BOOKMAP_CATCHUP_MB"]
    g["BOOKMAP_WAIT_SECONDS"] = _fint_first(["BOOKMAP_WAIT_SECONDS", "NT_WAIT_SECONDS"], 5)
    g["NT_WAIT_SECONDS"] = g["BOOKMAP_WAIT_SECONDS"]
    g["BOOKMAP_ROTATE_MB"] = _ffloat_first(["BOOKMAP_ROTATE_MB", "NT_ROTATE_MB"], 200.0)
    g["NT_ROTATE_MB"] = g["BOOKMAP_ROTATE_MB"]
    g["BOOKMAP_ARCHIVE_KEEP_DAYS"] = _ffloat_first(["BOOKMAP_ARCHIVE_KEEP_DAYS", "NT_ARCHIVE_KEEP_DAYS"], 0.0)
    g["NT_ARCHIVE_KEEP_DAYS"] = g["BOOKMAP_ARCHIVE_KEEP_DAYS"]

    # BookMap-specific options
    g["BOOKMAP_SYMBOL_FILTER"] = _fget("BOOKMAP_SYMBOL_FILTER", "")  # e.g. "MGC" — only this root is written
    g["BOOKMAP_WRITE_MBO"] = _fget("BOOKMAP_WRITE_MBO", "0") == "1"  # also write MBO stream to mbo.csv
    g["BOOKMAP_MBO_FILE"] = _fget("BOOKMAP_MBO_FILE", "")  # optional separate MBO file
    g["BOOKMAP_ADDON_NAME"] = _fget("BOOKMAP_ADDON_NAME", "GoldBookMapBridge")

    # ---- CRITICAL v5.2: L3 Whale + Regime Adaptive + AI Fallback + Basis ----
    g["L3_WHALE_THRESHOLD"] = _ffloat("L3_WHALE_THRESHOLD", 100.0)  # lots
    g["L3_WHALE_PROXIMITY_PCT"] = _ffloat("L3_WHALE_PROXIMITY_PCT", 0.5)  # % near price
    g["L3_WHALE_WEIGHT"] = _ffloat("L3_WHALE_WEIGHT", 1.5)
    g["SIGNAL_W_L3_WHALE"] = _ffloat("SIGNAL_W_L3_WHALE", 1.5)

    g["REGIME_ADAPTIVE"] = _fget("REGIME_ADAPTIVE", "1") == "1"
    g["TREND_ADX_THRESHOLD"] = _ffloat("TREND_ADX_THRESHOLD", 25.0)
    g["VOLATILITY_HIGH_MULT"] = _ffloat("VOLATILITY_HIGH_MULT", 1.5)
    g["RANGE_VWAP_WEIGHT"] = _ffloat("RANGE_VWAP_WEIGHT", 2.0)

    g["AI_TIMEOUT_SECONDS"] = _fint("AI_TIMEOUT_SECONDS", 20)
    g["AI_FALLBACK_ENABLED"] = _fget("AI_FALLBACK_ENABLED", "1") == "1"
    g["AI_FALLBACK_STRENGTH"] = _ffloat("AI_FALLBACK_STRENGTH", 35.0)
    g["AI_FALLBACK_CONFIDENCE"] = _ffloat("AI_FALLBACK_CONFIDENCE", 70.0)
    g["AI_CACHE_MINUTES"] = _fint("AI_CACHE_MINUTES", 5)
    g["AI_MAX_PROMPT_BARS"] = _fint("AI_MAX_PROMPT_BARS", 15)
    g["AI_MAX_HEADLINES"] = _fint("AI_MAX_HEADLINES", 5)

    g["BASIS_MAX"] = _ffloat("BASIS_MAX", 50.0)  # max futures-spot basis $
    g["BASIS_BUFFER_MULT"] = _ffloat("BASIS_BUFFER_MULT", 1.5)
    g["BASIS_WARN_PCT"] = _ffloat("BASIS_WARN_PCT", 1.0)  # % basis change warning

    # ---- MEDIUM v5.3: M1-M6 ----
    # M1 Footprint & Absorption
    g["FOOTPRINT_ENABLED"] = _fget("FOOTPRINT_ENABLED", "1") == "1"
    g["ABSORPTION_WALL_SIZE"] = _ffloat("ABSORPTION_WALL_SIZE", 50.0)
    g["ABSORPTION_MIN_EVENTS"] = _fint("ABSORPTION_MIN_EVENTS", 2)
    g["AGGRESSIVE_FROM_TICKS"] = _fget("AGGRESSIVE_FROM_TICKS", "1") == "1"

    # M2 Volatility-adjusted position sizing
    g["VOL_ADJUSTED_LOTS"] = _fget("VOL_ADJUSTED_LOTS", "1") == "1"
    g["VOL_LOT_RISK_PCT"] = _ffloat("VOL_LOT_RISK_PCT", 1.0)
    g["MIN_LOT_SKIP"] = _fget("MIN_LOT_SKIP", "1") == "1"
    g["VOL_ATR_FALLBACK_PCT"] = _ffloat("VOL_ATR_FALLBACK_PCT", 0.5)

    # M3 Iceberg & Spoof Detection
    g["ICEBERG_ENABLED"] = _fget("ICEBERG_ENABLED", "1") == "1"
    g["ICEBERG_MIN_REFILLS"] = _fint("ICEBERG_MIN_REFILLS", 3)
    g["ICEBERG_SAME_PRICE_TOL"] = _ffloat("ICEBERG_SAME_PRICE_TOL", 0.10)
    g["SPOOF_ENABLED"] = _fget("SPOOF_ENABLED", "1") == "1"
    g["SPOOF_CANCEL_SECONDS"] = _ffloat("SPOOF_CANCEL_SECONDS", 2.0)
    g["SPOOF_SIZE_THRESHOLD"] = _ffloat("SPOOF_SIZE_THRESHOLD", 100.0)
    g["SIGNAL_W_ICEBERG"] = _ffloat("SIGNAL_W_ICEBERG", 1.0)
    g["SIGNAL_W_SPOOF"] = _ffloat("SIGNAL_W_SPOOF", 0.8)

    # M4 Macro & News weight boost
    g["NEWS_SENTIMENT_HIGH_WEIGHT"] = _ffloat("NEWS_SENTIMENT_HIGH_WEIGHT", 1.0)
    g["NEWS_SENTIMENT_LOW_WEIGHT"] = _ffloat("NEWS_SENTIMENT_LOW_WEIGHT", 0.4)
    g["DXY_VETO_ENABLED"] = _fget("DXY_VETO_ENABLED", "1") == "1"
    g["DXY_RISING_THRESHOLD_PCT"] = _ffloat("DXY_RISING_THRESHOLD_PCT", 0.3)
    g["DXY_CORR_THRESHOLD"] = _ffloat("DXY_CORR_THRESHOLD", -0.15)
    g["SIGNAL_W_MACRO_HIGH"] = _ffloat("SIGNAL_W_MACRO_HIGH", 1.0)

    # M5 MBO archival
    g["BOOKMAP_MBO_ROTATE_MB"] = _ffloat_first(["BOOKMAP_MBO_ROTATE_MB", "MBO_ROTATE_MB"], 100.0)
    g["BOOKMAP_MBO_ARCHIVE_KEEP_DAYS"] = _ffloat_first(["BOOKMAP_MBO_ARCHIVE_KEEP_DAYS", "MBO_ARCHIVE_KEEP_DAYS"], 0.0)

    # M6 TCA & Slippage
    g["TCA_ENABLED"] = _fget("TCA_ENABLED", "1") == "1"
    g["TCA_REPORT_DAYS"] = _fint("TCA_REPORT_DAYS", 7)
    g["TCA_SLIPPAGE_THRESHOLD"] = _ffloat("TCA_SLIPPAGE_THRESHOLD", 0.5)
    g["TCA_ADJUST_SPREAD_MULT"] = _fget("TCA_ADJUST_SPREAD_MULT", "1") == "1"

    # ---- LOW v5.4: L1-L6 ----
    # L4 Latency & Kill Switch
    g["LATENCY_LOG_ENABLED"] = _fget("LATENCY_LOG_ENABLED", "1") == "1"
    g["LATENCY_TARGET_MS"] = _fint("LATENCY_TARGET_MS", 500)
    g["FLASH_CRASH_ENABLED"] = _fget("FLASH_CRASH_ENABLED", "1") == "1"
    g["FLASH_CRASH_ATR_MULT"] = _ffloat("FLASH_CRASH_ATR_MULT", 3.0)
    g["FLASH_CRASH_MINUTES"] = _ffloat("FLASH_CRASH_MINUTES", 1.0)

    # L6 Volatility Regime PM (adaptive giveback)
    g["PM_VOL_ADAPTIVE"] = _fget("PM_VOL_ADAPTIVE", "1") == "1"
    g["PM_VOL_HIGH_GIVEBACK"] = _ffloat("PM_VOL_HIGH_GIVEBACK", 0.30)
    g["PM_VOL_LOW_GIVEBACK"] = _ffloat("PM_VOL_LOW_GIVEBACK", 0.60)
    g["PM_VOL_HIGH_RANK"] = _ffloat("PM_VOL_HIGH_RANK", 0.6)
    g["PM_VOL_LOW_RANK"] = _ffloat("PM_VOL_LOW_RANK", 0.3)

    # L1 Cross-Market Confirmation
    g["CROSS_MARKET_ENABLED"] = _fget("CROSS_MARKET_ENABLED", "0") == "1"
    g["CROSS_MARKET_CONF_BOOST"] = _ffloat("CROSS_MARKET_CONF_BOOST", 15.0)
    g["GC_BRIDGE_FILE"] = _fget("GC_BRIDGE_FILE", "")
    g["SI_BRIDGE_FILE"] = _fget("SI_BRIDGE_FILE", "")

    # L2 Backtest L3
    g["BACKTEST_L3_ENABLED"] = _fget("BACKTEST_L3_ENABLED", "1") == "1"

    # L3 Queue Position Model
    g["QUEUE_POS_ENABLED"] = _fget("QUEUE_POS_ENABLED", "1") == "1"
    g["QUEUE_POS_THRESHOLD"] = _ffloat("QUEUE_POS_THRESHOLD", 0.7)

    # B3 Smart Limit Queue
    g["LIMIT_ORDER_ENABLED"] = _fget("LIMIT_ORDER_ENABLED", "0") == "1"
    g["LIMIT_OFFSET_TICKS"] = _fint("LIMIT_OFFSET_TICKS", 1)
    g["LIMIT_TICK_SIZE"] = _ffloat("LIMIT_TICK_SIZE", 0.1)
    g["LIMIT_TIMEOUT_SECONDS"] = _fint("LIMIT_TIMEOUT_SECONDS", 10)

    # B2 ML Weight Trainer
    g["ML_TRAINER_ENABLED"] = _fget("ML_TRAINER_ENABLED", "1") == "1"
    g["ML_MIN_TRADES"] = _fint("ML_MIN_TRADES", 30)
    g["ML_WEIGHT_ADJUST_PCT"] = _ffloat("ML_WEIGHT_ADJUST_PCT", 15.0)

    # B6 Walk-Forward
    g["WALK_FORWARD_DAYS"] = _fint("WALK_FORWARD_DAYS", 30)
    g["WALK_FORWARD_MONTE_CARLO"] = _fint("WALK_FORWARD_MONTE_CARLO", 1000)

    # L5 Reinforcement Learning from Trade Memory
    g["RL_ENABLED"] = _fget("RL_ENABLED", "1") == "1"
    g["RL_LOSS_STREAK"] = _fint("RL_LOSS_STREAK", 3)
    g["RL_SIZE_REDUCE_PCT"] = _ffloat("RL_SIZE_REDUCE_PCT", 50.0)
    g["RL_PAUSE_MINUTES"] = _fint("RL_PAUSE_MINUTES", 30)

    # ---- Databento (optional future) --------------------------------------
    g["DATABENTO_DATASET"] = _fget("DATABENTO_DATASET", "GLBX.MDP3")
    g["DATABENTO_SCHEMA"] = _fget("DATABENTO_SCHEMA", "mbo")
    g["DATABENTO_SYMBOL"] = _fget("DATABENTO_SYMBOL", "GC.n.0")

    # ---- MT5 signal bridge -------------------------------------------------
    g["MT5_SIGNAL_FILE"] = _fget("MT5_SIGNAL_FILE", "")
    g["SIGNAL_MAX_AGE_SECONDS"] = _fint("SIGNAL_MAX_AGE_SECONDS", 180)


_load_dotenv(BASE_DIR / ".env")
_refresh()


def reload_env() -> None:
    _load_dotenv(BASE_DIR / ".env")
    _refresh()


def mt5_initialize(mt5_module) -> bool:
    kwargs = {}
    if MT5_TERMINAL_PATH:
        kwargs["path"] = MT5_TERMINAL_PATH
    if MT5_LOGIN:
        kwargs.update(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
    return bool(mt5_module.initialize(**kwargs))


def credentials_configured() -> dict:
    return {
        "databento": bool(DATABENTO_API_KEY),
        "rithmic": bool(RITHMIC_USERNAME and RITHMIC_PASSWORD),
        "gemini": bool(GEMINI_API_KEY),
        "mt5": bool((MT5_LOGIN == 0 and MT5_SYMBOL) or (MT5_LOGIN and MT5_PASSWORD and MT5_SERVER)),
        "bookmap": True,  # file-based, no key needed — BookMap itself holds the Rithmic login
    }
