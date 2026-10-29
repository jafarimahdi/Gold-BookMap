"""Explicit, one-shot DEMO-only MT5 order-path smoke test.

This tool is separate from the strategy. It does not alter ``.env`` or lower
strategy gates. By default it performs a broker preflight only. To submit one
DEMO market order, pass ``--send`` and type the exact confirmation printed by
the tool. It uses the broker's minimum volume, current app SL/TP multipliers,
a fresh BookMap ATR snapshot, a fresh MT5 quote, and the configured risk cap.

The test order uses a distinct magic number, is recorded in
``data/demo_order_test_result.json`` (which the app excludes from strategy
risk/performance accounting), and is offered for immediate closure after the
operator has seen it in MT5. A durable pending marker blocks normal strategy
entries if the test is interrupted; ``--recover`` clears it only after the
original DEMO account and absence of all test-symbol positions/orders are verified.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config

try:  # MetaTrader5 is Windows-only; tests inject a fake module on other OSes.
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover - exercised on non-Windows hosts
    mt5 = None


TEST_MAGIC = 73261023
TEST_COMMENT = "GBM-DEMO-SMOKE"
MAX_SNAPSHOT_AGE_SECONDS = 120.0
MAX_BROKER_QUOTE_AGE_SECONDS = 5.0
POSITION_VERIFY_SECONDS = 5.0


class SmokeTestError(RuntimeError):
    """A fail-closed precondition or broker check rejected the smoke test."""


@dataclass(frozen=True)
class OrderPlan:
    symbol: str
    side: str
    volume: float
    volume_text: str
    entry: float
    sl: float
    tp: float
    atr: float
    estimated_loss: float
    risk_budget: float
    real_risk_cap: float
    equity: float
    currency: str
    account_login: int
    account_server: str
    snapshot_age: float
    quote_age: float
    spread_pct: float
    request: Dict[str, Any]


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise SmokeTestError(f"{label} is invalid")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise SmokeTestError(f"{label} is missing or invalid") from None
    if not math.isfinite(result) or (positive and result <= 0):
        raise SmokeTestError(f"{label} is missing or invalid")
    return result


def _snapshot(data_dir: Path, now_epoch: float, cfg: Any = config
              ) -> tuple[dict, float, float, str, float]:
    path = data_dir / "market_snapshot.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SmokeTestError(
            f"cannot read {path}; run one fresh --no-new-entries cycle first ({exc})"
        ) from None
    if not isinstance(payload, dict):
        raise SmokeTestError("market_snapshot.json is not an object")

    raw_timestamp = payload.get("timestamp")
    if not isinstance(raw_timestamp, str) or not raw_timestamp.strip():
        raise SmokeTestError("snapshot timestamp is missing")
    try:
        stamp = datetime.fromisoformat(raw_timestamp.strip().replace("Z", "+00:00"))
    except ValueError:
        raise SmokeTestError("snapshot timestamp is invalid") from None
    if stamp.tzinfo is None:
        raise SmokeTestError("snapshot timestamp has no timezone; refusing stale-time ambiguity")
    age = now_epoch - stamp.timestamp()
    if not math.isfinite(age) or age < 0:
        raise SmokeTestError("snapshot timestamp is future-dated or invalid")
    if age > MAX_SNAPSHOT_AGE_SECONDS:
        raise SmokeTestError(
            f"BookMap snapshot is {age:.1f}s old (limit {MAX_SNAPSHOT_AGE_SECONDS:.0f}s); "
            "refresh it with one --no-new-entries cycle"
        )

    vol = payload.get("volatility")
    if not isinstance(vol, dict):
        raise SmokeTestError("snapshot volatility/ATR is missing")
    feed_price = _number(payload.get("price"), "snapshot price", positive=True)
    feed_atr = _number(vol.get("atr"), "snapshot ATR", positive=True)
    news = payload.get("news")
    if not isinstance(news, dict):
        raise SmokeTestError("snapshot news state is unavailable; fail closed")
    news_state = str(news.get("news_state", "")).strip().upper()
    if news_state not in {"QUIET", "WARNING", "BLACKOUT"}:
        raise SmokeTestError("snapshot news state is unknown; fail closed")
    if news_state == "BLACKOUT":
        raise SmokeTestError("snapshot is in NEWS BLACKOUT; no smoke order will be sent")

    quality = payload.get("data_quality")
    if not isinstance(quality, dict):
        raise SmokeTestError("snapshot data-quality metadata is missing")
    quote_quality = str(quality.get("quote", "")).strip().lower()
    if quote_quality != "valid":
        raise SmokeTestError(f"snapshot quote quality is {quote_quality!r}, not valid")

    # Require the ordinary pipeline's latest feed/safety receipt. The smoke
    # test bypasses only strategy signal authorization; it must not bypass the
    # pipeline's feed, quote, or (for BookMap) L2 integrity gate.
    evidence = payload.get("quality_evidence")
    if not isinstance(evidence, dict):
        raise SmokeTestError(
            "snapshot has no post-analysis safety receipt; refresh with "
            "python main.py --no-new-entries"
        )
    if str(evidence.get("safety_gate_status", "")).strip().upper() != "PASS":
        reason = str(evidence.get("safety_gate_reason", "not recorded"))
        raise SmokeTestError(f"normal pipeline safety/feed gate is not PASS ({reason})")
    if str(evidence.get("quote_status", "")).strip().lower() != "valid":
        raise SmokeTestError("normal pipeline receipt does not show a validated feed quote")
    source = str(evidence.get("data_source", "")).strip().lower()
    if not source:
        raise SmokeTestError("normal pipeline receipt has no data-source identity")
    configured_source = str(getattr(cfg, "DATA_SOURCE", "") or "").strip().lower()
    if configured_source and source != configured_source:
        raise SmokeTestError("normal pipeline receipt data source differs from current config")
    bookmap_sources = {"bookmapbridge", "ninjabridge", "bookmap", "bmbridge",
                       "bookmap_bridge", "ntbridge", "ninja"}
    if source not in bookmap_sources:
        raise SmokeTestError(
            f"current data source {source!r} is not a recognized BookMap bridge; "
            "the DEMO test requires validated BookMap L2"
        )
    if str(evidence.get("level2_status", "")).strip().lower() != "validated":
        raise SmokeTestError("normal pipeline receipt does not show validated BookMap L2")
    diagnostics = evidence.get("l2_diagnostics")
    if (not isinstance(diagnostics, dict)
            or str(diagnostics.get("status", "")).strip().lower() != "validated"
            or diagnostics.get("instrument_match") is not True):
        raise SmokeTestError("normal pipeline BookMap L2/instrument diagnostics are not valid")
    max_age = _number(getattr(cfg, "STALE_DATA_SECONDS", 300),
                      "STALE_DATA_SECONDS", positive=True)
    for label, key in (("feed", "feed_age_seconds_at_decision"),
                       ("quote", "quote_age_seconds_at_decision"),
                       ("BookMap depth", "depth_age_seconds_at_decision")):
        data_age = _number(evidence.get(key), f"{label} age", positive=False)
        if data_age < 0 or data_age > max_age:
            raise SmokeTestError(
                f"normal pipeline {label} age {data_age:.2f}s exceeds "
                f"configured STALE_DATA_SECONDS {max_age:.0f}s"
            )
    return payload, age, feed_price, news_state, feed_atr


def _check_config(cfg: Any) -> None:
    if getattr(cfg, "TRADING_ENABLED", False) is not True:
        raise SmokeTestError("TRADING_ENABLED is not true; no order will be sent")
    if str(getattr(cfg, "EXECUTION_MODE", "")).strip().lower() != "python":
        raise SmokeTestError("EXECUTION_MODE is not python")
    if getattr(cfg, "ALLOW_LIVE_TRADING", True) is not False:
        raise SmokeTestError("ALLOW_LIVE_TRADING must remain false for this DEMO-only test")


def _pending_marker_path(cfg: Any) -> Path:
    return Path(cfg.DATA_DIR) / "demo_order_test_pending.json"


def _write_pending_marker(cfg: Any, plan: OrderPlan, attempt_id: str) -> None:
    path = _pending_marker_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "PENDING",
        "attempt_id": attempt_id,
        "symbol": plan.symbol,
        "side": plan.side,
        "volume": plan.volume,
        "magic": TEST_MAGIC,
        "account_login": plan.account_login,
        "account_server": plan.account_server,
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise SmokeTestError(f"could not create the persistent DEMO-test safety marker: {exc}") from None


def _clear_pending_marker(cfg: Any, attempt_id: str) -> bool:
    path = _pending_marker_path(cfg)
    if not path.exists():
        return True
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(payload, dict)
                or str(payload.get("attempt_id", "")) != str(attempt_id)):
            return False
        path.unlink()
        return True
    except (OSError, json.JSONDecodeError, ValueError):
        return False


def _true_flag(value: Any) -> bool:
    return value is True or (type(value) is int and value == 1)


def _check_runtime_gates(cfg: Any = config) -> None:
    """Recheck non-signal production gates; fail closed if any is unknown."""
    try:
        from session import is_market_open
        if is_market_open() is not True:
            raise SmokeTestError("normal strategy session gate says the market is closed")
    except SmokeTestError:
        raise
    except Exception as exc:
        raise SmokeTestError(f"could not verify market session: {exc}") from None

    try:
        from risk_manager import RiskManager
        allowed, reason = RiskManager().check()
        if allowed is not True:
            raise SmokeTestError(f"normal risk/circuit-breaker gate blocks entry: {reason}")
    except SmokeTestError:
        raise
    except Exception as exc:
        raise SmokeTestError(f"could not verify risk/circuit-breaker gate: {exc}") from None

    if getattr(cfg, "RL_ENABLED", True):
        try:
            import trade_history as history
            state = history.check_loss_streak_regime("", within_hours=24.0)
            if not isinstance(state, dict):
                raise SmokeTestError("loss-streak gate returned invalid state")
            if str(state.get("reason", "")).startswith("RL error"):
                raise SmokeTestError(f"loss-streak gate could not verify state: {state['reason']}")
            if state.get("should_pause"):
                raise SmokeTestError(
                    str(state.get("reason") or "normal RL loss-streak gate blocks entry")
                )
            multiplier = _number(history.get_rl_size_multiplier(""),
                                 "RL size multiplier", positive=False)
            if multiplier < 1.0:
                raise SmokeTestError(
                    "normal RL gate requires reduced size; broker minimum-lot smoke order "
                    "cannot apply that reduction safely"
                )
        except SmokeTestError:
            raise
        except Exception as exc:
            raise SmokeTestError(f"could not verify normal RL gate: {exc}") from None

    try:
        from trade_guard import TradeGuard
        allowed, reason = TradeGuard().can_trade()
        if allowed is not True:
            raise SmokeTestError(f"normal cooldown/daily-trade-cap gate blocks entry: {reason}")
    except SmokeTestError:
        raise
    except Exception as exc:
        raise SmokeTestError(f"could not verify cooldown/daily-trade-cap gate: {exc}") from None


def _require_demo_identity(mt5_module: Any, *,
                           require_trade_allowed: bool = False) -> tuple[Any, int, str]:
    account = mt5_module.account_info()
    if account is None:
        raise SmokeTestError("MT5 account_info unavailable")
    demo_mode = getattr(mt5_module, "ACCOUNT_TRADE_MODE_DEMO", None)
    actual_mode = getattr(account, "trade_mode", None)
    try:
        demo_match = (demo_mode is not None and actual_mode is not None
                      and not isinstance(actual_mode, bool)
                      and int(actual_mode) == int(demo_mode))
    except (TypeError, ValueError, OverflowError):
        demo_match = False
    if not demo_match:
        raise SmokeTestError(
            "connected MT5 account is not positively identified as DEMO; refusing order"
        )
    if require_trade_allowed and not _true_flag(getattr(account, "trade_allowed", None)):
        raise SmokeTestError("MT5 account does not report trading allowed")
    raw_login = getattr(account, "login", None)
    try:
        if isinstance(raw_login, bool):
            raise ValueError("boolean login")
        login = int(raw_login)
    except (TypeError, ValueError, OverflowError):
        raise SmokeTestError("MT5 account login identity is unavailable") from None
    if login <= 0:
        raise SmokeTestError("MT5 account login identity is invalid")
    server = str(getattr(account, "server", "") or "").strip()
    if not server:
        raise SmokeTestError("MT5 account server identity is unavailable")
    return account, login, server


def _require_demo(mt5_module: Any) -> Any:
    account, _login, _server = _require_demo_identity(
        mt5_module, require_trade_allowed=True)
    return account


def _check_terminal(mt5_module: Any, *, require_trade_allowed: bool = True) -> None:
    terminal = mt5_module.terminal_info()
    if terminal is None:
        raise SmokeTestError("MT5 terminal_info unavailable")
    if not _true_flag(getattr(terminal, "connected", None)):
        raise SmokeTestError("MT5 terminal does not report a connected session")
    if (require_trade_allowed
            and not _true_flag(getattr(terminal, "trade_allowed", None))):
        raise SmokeTestError("MT5 terminal does not report algorithmic trading allowed")
    if require_trade_allowed and getattr(terminal, "tradeapi_disabled", False) is True:
        raise SmokeTestError("MT5 terminal has disabled external trade API requests")


def _quote_age(tick: Any, now_epoch: float) -> float:
    try:
        millis = int(getattr(tick, "time_msc", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        millis = 0
    try:
        seconds = float(getattr(tick, "time", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        seconds = 0.0
    epoch = millis / 1000.0 if millis > 0 else seconds
    if not math.isfinite(epoch) or epoch <= 0:
        raise SmokeTestError("MT5 quote timestamp is unavailable")
    from step4_mt5_execution import resolve_mt5_quote_age
    age, _ = resolve_mt5_quote_age(
        epoch, now_epoch, max_age=MAX_BROKER_QUOTE_AGE_SECONDS, config_mod=config
    )
    if not math.isfinite(age) or age < 0:
        raise SmokeTestError("MT5 quote is future-dated or invalid")
    if age > MAX_BROKER_QUOTE_AGE_SECONDS:
        raise SmokeTestError(
            f"MT5 broker quote is {age:.2f}s old (smoke-test limit "
            f"{MAX_BROKER_QUOTE_AGE_SECONDS:.1f}s)"
        )
    return age


def _filling_mode(mt5_module: Any, info: Any) -> int:
    raw = int(getattr(info, "filling_mode", 0) or 0)
    if raw & 2:
        return getattr(mt5_module, "ORDER_FILLING_IOC", 1)
    if raw & 1:
        return getattr(mt5_module, "ORDER_FILLING_FOK", 0)
    return getattr(mt5_module, "ORDER_FILLING_RETURN", 2)


def _check_symbol_mode(mt5_module: Any, info: Any, side: str) -> None:
    mode = getattr(info, "trade_mode", None)
    if mode is None:
        raise SmokeTestError("symbol trading mode is unavailable")
    disabled = getattr(mt5_module, "SYMBOL_TRADE_MODE_DISABLED", 0)
    long_only = getattr(mt5_module, "SYMBOL_TRADE_MODE_LONGONLY", 1)
    short_only = getattr(mt5_module, "SYMBOL_TRADE_MODE_SHORTONLY", 2)
    close_only = getattr(mt5_module, "SYMBOL_TRADE_MODE_CLOSEONLY", 3)
    full = getattr(mt5_module, "SYMBOL_TRADE_MODE_FULL", 4)
    if mode in (disabled, close_only):
        raise SmokeTestError("symbol is disabled or close-only")
    if mode == long_only and side != "BUY":
        raise SmokeTestError("symbol is long-only; requested side is SELL")
    if mode == short_only and side != "SELL":
        raise SmokeTestError("symbol is short-only; requested side is BUY")
    if mode not in (long_only, short_only, full):
        raise SmokeTestError(f"unsupported symbol trade mode {mode!r}")


def _volume_digits(step: float) -> int:
    text = f"{step:.10f}".rstrip("0")
    return len(text.split(".", 1)[1]) if "." in text else 0


def _risk_budget(cfg: Any, equity: float) -> float:
    try:
        risk_pcts = [_number(getattr(cfg, "RISK_PER_TRADE_PCT", None),
                             "RISK_PER_TRADE_PCT", positive=True)]
        if getattr(cfg, "VOL_ADJUSTED_LOTS", False):
            risk_pcts.append(_number(getattr(cfg, "VOL_LOT_RISK_PCT", None),
                                     "VOL_LOT_RISK_PCT", positive=True))
    except SmokeTestError:
        raise
    return equity * min(risk_pcts) / 100.0


def _make_plan(mt5_module: Any, cfg: Any, side: str,
               now_epoch: float) -> OrderPlan:
    side = str(side).strip().upper()
    if side not in {"BUY", "SELL"}:
        raise SmokeTestError("side must be BUY or SELL")

    account, account_login, account_server = _require_demo_identity(
        mt5_module, require_trade_allowed=True)
    _check_terminal(mt5_module, require_trade_allowed=True)
    symbol = str(getattr(cfg, "MT5_SYMBOL", "") or
                 getattr(cfg, "SYMBOL", "")).strip()
    if not symbol:
        raise SmokeTestError("configured MT5 symbol is missing")
    if mt5_module.symbol_select(symbol, True) is not True:
        raise SmokeTestError(f"MT5 could not select configured symbol {symbol}")

    info = mt5_module.symbol_info(symbol)
    if info is None:
        raise SmokeTestError(f"MT5 symbol_info unavailable for {symbol}")
    _check_symbol_mode(mt5_module, info, side)

    # Refuse to overlap a manual position, another EA, or an earlier test.
    positions = mt5_module.positions_get(symbol=symbol)
    if positions is None:
        raise SmokeTestError("MT5 positions_get failed; refusing to risk overlap")
    if len(positions):
        raise SmokeTestError(
            f"{len(positions)} open {symbol} position(s) already exist; "
            "close/review them before the isolated smoke test"
        )
    orders = mt5_module.orders_get(symbol=symbol)
    if orders is None:
        raise SmokeTestError("MT5 orders_get failed; refusing to risk overlap")
    if len(orders):
        raise SmokeTestError(
            f"{len(orders)} pending {symbol} order(s) already exist; "
            "review them before the isolated smoke test"
        )

    tick = mt5_module.symbol_info_tick(symbol)
    if tick is None:
        raise SmokeTestError("MT5 symbol_info_tick returned no quote")
    age = _quote_age(tick, now_epoch)
    bid = _number(getattr(tick, "bid", None), "MT5 bid", positive=True)
    ask = _number(getattr(tick, "ask", None), "MT5 ask", positive=True)
    if ask <= bid:
        raise SmokeTestError("MT5 quote is locked or crossed")
    spread_pct = (ask - bid) / bid * 100.0
    spread_cap = _number(getattr(cfg, "MAX_SPREAD_PCT", 0.0),
                          "MAX_SPREAD_PCT")
    if spread_cap < 0:
        raise SmokeTestError("MAX_SPREAD_PCT is invalid")
    if spread_cap > 0 and spread_pct > spread_cap:
        raise SmokeTestError(
            f"MT5 spread {spread_pct:.4f}% exceeds configured cap {spread_cap:.4f}%"
        )

    snapshot, snapshot_age, feed_price, news_state, feed_atr = _snapshot(
        Path(cfg.DATA_DIR), now_epoch, cfg)
    entry = ask if side == "BUY" else bid
    atr = feed_atr * (entry / feed_price)
    if not math.isfinite(atr) or atr <= 0:
        raise SmokeTestError("re-anchored ATR is invalid")
    basis = feed_price - entry
    basis_max = _number(getattr(cfg, "BASIS_MAX", 50.0), "BASIS_MAX")
    if basis_max < 0:
        raise SmokeTestError("BASIS_MAX is invalid")
    if basis_max > 0 and abs(basis) > basis_max * 1.5:
        raise SmokeTestError("futures/CFD basis exceeds the normal execution safety limit")
    if basis_max > 0 and abs(basis) > basis_max:
        atr *= _number(getattr(cfg, "BASIS_BUFFER_MULT", 1.5),
                       "BASIS_BUFFER_MULT", positive=True)
    sl_mult = _number(getattr(cfg, "STOP_LOSS_ATR_MULT", None),
                      "STOP_LOSS_ATR_MULT", positive=True)
    if news_state == "WARNING":
        sl_mult *= _number(getattr(cfg, "NEWS_WIDEN_STOP_MULT", None),
                           "NEWS_WIDEN_STOP_MULT", positive=True)
    tp_mult = _number(getattr(cfg, "TAKE_PROFIT_ATR_MULT", None),
                      "TAKE_PROFIT_ATR_MULT", positive=True)
    digits = int(getattr(info, "digits", 2) or 2)
    sl = round(entry - sl_mult * atr if side == "BUY" else entry + sl_mult * atr,
               digits)
    tp = round(entry + tp_mult * atr if side == "BUY" else entry - tp_mult * atr,
               digits)
    if not (sl < entry < tp if side == "BUY" else tp < entry < sl):
        raise SmokeTestError("calculated stop/target geometry is invalid")

    point = _number(getattr(info, "point", 0.0), "symbol point")
    stops_level = _number(getattr(info, "trade_stops_level", 0.0),
                          "symbol stops level")
    min_distance = max(0.0, point * stops_level)
    if side == "BUY":
        valid_stops = (entry - sl >= min_distance and tp - entry >= min_distance)
    else:
        valid_stops = (sl - entry >= min_distance and entry - tp >= min_distance)
    if not valid_stops:
        raise SmokeTestError("configured ATR stops are inside the broker's stops level")

    # Keep the app's configured reward/risk constraints; this test does not
    # bypass them just to manufacture an order.
    from wall_brackets import value_gate
    ok, reason = value_gate(side, entry, sl, tp, atr, cfg)
    if not ok:
        raise SmokeTestError(reason)
    cost_mult = _number(getattr(cfg, "ENTRY_MIN_TP_SPREAD_MULT", 0.0),
                         "ENTRY_MIN_TP_SPREAD_MULT")
    if cost_mult < 0:
        raise SmokeTestError("ENTRY_MIN_TP_SPREAD_MULT is invalid")
    if cost_mult > 0 and abs(tp - entry) < cost_mult * (ask - bid):
        raise SmokeTestError("take-profit distance is too small versus the broker spread")

    volume_min = _number(getattr(info, "volume_min", None),
                         "broker minimum volume", positive=True)
    volume_step = _number(getattr(info, "volume_step", None),
                          "broker volume step", positive=True)
    volume_max = _number(getattr(info, "volume_max", None),
                         "broker maximum volume", positive=True)
    configured_max = _number(getattr(cfg, "MAX_LOT_SIZE", None),
                             "MAX_LOT_SIZE", positive=True)
    if volume_min > min(volume_max, configured_max) + 1e-12:
        raise SmokeTestError(
            "broker minimum volume exceeds the configured MAX_LOT_SIZE; refusing to raise size"
        )
    volume = volume_min
    # Require the broker's advertised minimum to lie on its own volume grid.
    grid_units = (volume - volume_min) / volume_step
    if not math.isfinite(grid_units) or abs(grid_units - round(grid_units)) > 1e-6:
        raise SmokeTestError("broker minimum volume does not align with volume_step")
    volume_text = f"{volume:.{_volume_digits(volume_step)}f}"

    equity = _number(getattr(account, "equity", None), "DEMO account equity", positive=True)
    currency = str(getattr(account, "currency", "") or "").strip()
    if not currency:
        raise SmokeTestError("DEMO account currency is unavailable")
    risk_budget = _risk_budget(cfg, equity)
    if news_state == "WARNING":
        news_size_mult = _number(getattr(cfg, "NEWS_REDUCE_SIZE_PCT", None),
                                 "NEWS_REDUCE_SIZE_PCT", positive=True)
        if news_size_mult > 1.0:
            raise SmokeTestError("NEWS_REDUCE_SIZE_PCT must not increase smoke-test risk")
        risk_budget *= news_size_mult

    # Retain the normal executor's minimum-lot skip and hard real-risk ceiling.
    # The smoke order is never upsized to make these checks pass.
    contract_size = _number(getattr(cfg, "CONTRACT_SIZE", None),
                            "CONTRACT_SIZE", positive=True)
    risk_pct = _number(getattr(cfg, "RISK_PER_TRADE_PCT", None),
                       "RISK_PER_TRADE_PCT", positive=True)
    stop_distance = abs(entry - sl)
    traditional_lots = (equity * risk_pct / 100.0) / (stop_distance * contract_size)
    vol_lots = 0.0
    if getattr(cfg, "VOL_ADJUSTED_LOTS", False):
        vol_pct = _number(getattr(cfg, "VOL_LOT_RISK_PCT", None),
                          "VOL_LOT_RISK_PCT", positive=True)
        vol_lots = (equity * vol_pct / 100.0) / (atr * contract_size)
    minimum_lot_basis = vol_lots if vol_lots > 0 else traditional_lots
    if getattr(cfg, "MIN_LOT_SKIP", True) and minimum_lot_basis < volume_min - 1e-9:
        raise SmokeTestError(
            "normal executor would skip because risk-sized volume is below the broker minimum"
        )

    order_type = (mt5_module.ORDER_TYPE_BUY if side == "BUY"
                  else mt5_module.ORDER_TYPE_SELL)
    loss_result = mt5_module.order_calc_profit(order_type, symbol, volume, entry, sl)
    if loss_result is None:
        raise SmokeTestError("MT5 order_calc_profit failed; cannot bound smoke-test loss")
    estimated_loss = -_number(loss_result, "estimated stop loss")
    if not math.isfinite(estimated_loss) or estimated_loss <= 0:
        raise SmokeTestError("calculated stop loss is not a positive loss")
    if estimated_loss > risk_budget:
        raise SmokeTestError(
            f"broker minimum lot risks {estimated_loss:.2f} {currency}, above the "
            f"configured per-trade risk budget {risk_budget:.2f} {currency}; refusing"
        )
    real_risk_pct = _number(getattr(cfg, "ENTRY_MAX_REAL_RISK_PCT", 0.0),
                            "ENTRY_MAX_REAL_RISK_PCT")
    if real_risk_pct < 0:
        raise SmokeTestError("ENTRY_MAX_REAL_RISK_PCT is invalid")
    real_risk_cap = equity * real_risk_pct / 100.0
    app_risk_estimate = volume * stop_distance * contract_size
    if real_risk_pct > 0 and max(estimated_loss, app_risk_estimate) > real_risk_cap:
        raise SmokeTestError(
            f"broker minimum lot exceeds the hard real-risk cap {real_risk_cap:.2f} "
            f"{currency} ({real_risk_pct:.2f}% of equity)"
        )
    # Use the more conservative of MT5's account-currency P/L calculation and
    # the app's own contract-size risk formula for the operator cap.
    estimated_loss = max(estimated_loss, app_risk_estimate)
    if estimated_loss > risk_budget:
        raise SmokeTestError(
            f"conservative minimum-lot risk {estimated_loss:.2f} {currency} exceeds "
            f"effective per-trade risk budget {risk_budget:.2f} {currency}"
        )

    margin = mt5_module.order_calc_margin(order_type, symbol, volume, entry)
    if margin is None:
        raise SmokeTestError("MT5 margin calculation failed")
    margin = _number(margin, "required margin")
    free_margin = _number(getattr(account, "margin_free", None),
                          "DEMO account free margin", positive=True)
    if margin > free_margin:
        raise SmokeTestError(
            f"required margin {margin:.2f} exceeds free margin {free_margin:.2f} {currency}"
        )

    filling = _filling_mode(mt5_module, info)
    request = {
        "action": mt5_module.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": entry,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": TEST_MAGIC,
        "comment": TEST_COMMENT,
        "type_time": mt5_module.ORDER_TIME_GTC,
        "type_filling": filling,
    }
    order_check = getattr(mt5_module, "order_check", None)
    if not callable(order_check):
        raise SmokeTestError("MT5 order_check is unavailable; refusing to send")
    checked = order_check(request)
    if checked is None:
        raise SmokeTestError("MT5 order_check returned no result")
    check_code = getattr(checked, "retcode", None)
    accepted_check_codes = {0, getattr(mt5_module, "TRADE_RETCODE_DONE", 10009)}
    if check_code not in accepted_check_codes:
        detail = getattr(checked, "comment", "")
        raise SmokeTestError(f"MT5 order_check refused request ({check_code}: {detail})")

    return OrderPlan(
        symbol=symbol, side=side, volume=volume, volume_text=volume_text,
        entry=entry, sl=sl, tp=tp, atr=atr,
        estimated_loss=estimated_loss, risk_budget=risk_budget,
        real_risk_cap=real_risk_cap, equity=equity,
        currency=currency, account_login=account_login,
        account_server=account_server, snapshot_age=snapshot_age, quote_age=age,
        spread_pct=spread_pct, request=request,
    )


def _position_safety_issue(mt5_module: Any, position: Any, plan: OrderPlan,
                           max_loss_cap: float, cfg: Any = config) -> Optional[str]:
    """Verify filled size, attached protection, and actual entry risk."""
    if str(getattr(position, "symbol", "")) != plan.symbol:
        return "filled position symbol differs from the requested symbol"
    expected_type = (getattr(mt5_module, "POSITION_TYPE_BUY", 0)
                     if plan.side == "BUY"
                     else getattr(mt5_module, "POSITION_TYPE_SELL", 1))
    if getattr(position, "type", None) != expected_type:
        return "filled position side differs from the requested side"
    try:
        actual_volume = _number(getattr(position, "volume", None),
                                "filled position volume", positive=True)
        actual_entry = _number(getattr(position, "price_open", None),
                               "filled position entry", positive=True)
        actual_sl = _number(getattr(position, "sl", None),
                            "filled position stop-loss", positive=True)
        actual_tp = _number(getattr(position, "tp", None),
                            "filled position take-profit", positive=True)
        info = mt5_module.symbol_info(plan.symbol)
        if info is None:
            return "symbol_info unavailable while verifying filled position protection"
        point = _number(getattr(info, "point", None), "symbol point", positive=True)
        contract_size = _number(getattr(cfg, "CONTRACT_SIZE", None),
                                "CONTRACT_SIZE", positive=True)
    except SmokeTestError as exc:
        return str(exc)
    except Exception as exc:
        return f"could not verify filled position protection ({type(exc).__name__}: {exc})"
    if not math.isclose(actual_volume, plan.volume, rel_tol=0.0, abs_tol=1e-9):
        return "filled position volume differs from the preflight volume"
    tolerance = max(point * 1.5, 1e-8)
    if abs(actual_sl - plan.sl) > tolerance:
        return "broker did not attach the requested protective stop-loss"
    if abs(actual_tp - plan.tp) > tolerance:
        return "broker did not attach the requested take-profit"
    if plan.side == "BUY":
        if not actual_sl < actual_entry < actual_tp:
            return "filled position stop/target geometry is invalid"
        order_type = getattr(mt5_module, "ORDER_TYPE_BUY", None)
    else:
        if not actual_tp < actual_entry < actual_sl:
            return "filled position stop/target geometry is invalid"
        order_type = getattr(mt5_module, "ORDER_TYPE_SELL", None)
    if order_type is None:
        return "MT5 position side constants are unavailable for post-fill risk check"
    try:
        raw_loss = mt5_module.order_calc_profit(
            order_type, plan.symbol, actual_volume, actual_entry, actual_sl)
    except Exception as exc:
        return f"MT5 post-fill risk calculation failed ({type(exc).__name__}: {exc})"
    if raw_loss is None:
        return "MT5 could not calculate post-fill stop-loss risk"
    try:
        mt5_loss = -_number(raw_loss, "post-fill broker stop-loss estimate")
    except SmokeTestError as exc:
        return str(exc)
    app_loss = actual_volume * abs(actual_entry - actual_sl) * contract_size
    actual_loss = max(mt5_loss, app_loss)
    if not math.isfinite(actual_loss) or actual_loss <= 0:
        return "post-fill stop-loss estimate is invalid"
    if actual_loss > plan.risk_budget:
        return (f"post-fill estimated loss {actual_loss:.2f} {plan.currency} exceeds "
                f"the effective risk budget {plan.risk_budget:.2f} {plan.currency}")
    if actual_loss > max_loss_cap:
        return (f"post-fill estimated loss {actual_loss:.2f} {plan.currency} exceeds "
                f"the operator cap {max_loss_cap:.2f} {plan.currency}")
    if plan.real_risk_cap > 0 and actual_loss > plan.real_risk_cap:
        return (f"post-fill estimated loss {actual_loss:.2f} {plan.currency} exceeds "
                f"the hard real-risk cap {plan.real_risk_cap:.2f} {plan.currency}")
    return None


def _print_plan(plan: OrderPlan, output: Callable[[str], None]) -> None:
    output("DEMO order-path preflight — no order has been sent")
    output(f"  Symbol/side: {plan.symbol} {plan.side}")
    output(f"  Volume: {plan.volume_text} (broker minimum; capped by MAX_LOT_SIZE)")
    output(f"  Entry / SL / TP: {plan.entry:.5f} / {plan.sl:.5f} / {plan.tp:.5f}")
    output(f"  Estimated stop loss: {plan.estimated_loss:.2f} {plan.currency} "
           f"(effective per-trade risk budget {plan.risk_budget:.2f} {plan.currency})")
    output(f"  MT5 quote age: {plan.quote_age:.2f}s; BookMap snapshot age: "
           f"{plan.snapshot_age:.1f}s; spread: {plan.spread_pct:.4f}%")
    output(f"  Protective stops: present; TEST magic: {TEST_MAGIC}")


def _format_challenge(plan: OrderPlan) -> str:
    return f"OPEN DEMO {plan.side} {plan.symbol} {plan.volume_text}"


def _positions_for_test(mt5_module: Any, symbol: str) -> list:
    positions = mt5_module.positions_get(symbol=symbol)
    if positions is None:
        raise SmokeTestError("MT5 positions_get failed during test-position verification")
    matches = [p for p in positions
               if str(getattr(p, "symbol", "")) == symbol
               and int(getattr(p, "magic", -1) or -1) == TEST_MAGIC]
    if len(matches) != len(positions):
        raise SmokeTestError(
            "a non-test position appeared on the test symbol; operator review is required"
        )
    return matches


def _orders_for_symbol(mt5_module: Any, symbol: str) -> list:
    orders = mt5_module.orders_get(symbol=symbol)
    if orders is None:
        raise SmokeTestError("MT5 orders_get failed during test-order verification")
    return list(orders)


def _wait_for_test_position(mt5_module: Any, symbol: str,
                           sleep_fn: Callable[[float], None] = time.sleep,
                           monotonic_fn: Callable[[], float] = time.monotonic) -> Any:
    deadline = monotonic_fn() + POSITION_VERIFY_SECONDS
    attempts = int(POSITION_VERIFY_SECONDS / 0.2) + 2
    for _ in range(attempts):
        matches = _positions_for_test(mt5_module, symbol)
        if matches:
            if _orders_for_symbol(mt5_module, symbol):
                raise SmokeTestError(
                    "a pending order remains on the test symbol; cancel/review it manually "
                    "before resolving the test"
                )
            return matches[0]
        if monotonic_fn() >= deadline:
            return None
        sleep_fn(0.2)
    return None


def _wait_for_test_position_closed(mt5_module: Any, symbol: str, ticket: int,
                                   sleep_fn: Callable[[float], None] = time.sleep,
                                   monotonic_fn: Callable[[], float] = time.monotonic) -> bool:
    deadline = monotonic_fn() + POSITION_VERIFY_SECONDS
    attempts = int(POSITION_VERIFY_SECONDS / 0.2) + 2
    for _ in range(attempts):
        positions = mt5_module.positions_get(symbol=symbol)
        if positions is None:
            return False
        orders = _orders_for_symbol(mt5_module, symbol)
        if not positions and not orders:
            return True
        if monotonic_fn() >= deadline:
            return False
        sleep_fn(0.2)
    return False


def _wait_for_no_test_position(mt5_module: Any, symbol: str,
                               expected_login: int, expected_server: str,
                               input_fn: Callable[[str], str],
                               output: Callable[[str], None]) -> bool:
    """Resolve an uncertain send while holding the app lock; never resend."""
    while True:
        try:
            input_fn(
                "Inspect MT5 now. Close/review any open position on this test symbol, "
                "then press Enter here to verify none remain: "
            )
        except EOFError:
            return False
        try:
            account, login, server = _require_demo_identity(mt5_module)
            _check_terminal(mt5_module, require_trade_allowed=False)
            if login != int(expected_login) or server != expected_server:
                output("Return to the original DEMO account before verification; "
                       "the app lock remains held.")
                continue
            positions = mt5_module.positions_get(symbol=symbol)
            if positions is None:
                output("Could not verify MT5 positions yet; the app lock remains held.")
                continue
            orders = mt5_module.orders_get(symbol=symbol)
            if orders is None:
                output("Could not verify MT5 pending orders yet; the app lock remains held.")
                continue
            if not positions and not orders:
                return True
            details = []
            if positions:
                details.append("position(s): " + ", ".join(
                    f"{getattr(p, 'ticket', '?')} (magic {getattr(p, 'magic', 'unknown')})"
                    for p in positions))
            if orders:
                details.append("pending order(s): " + ", ".join(
                    f"{getattr(o, 'ticket', '?')} (magic {getattr(o, 'magic', 'unknown')})"
                    for o in orders))
            output(f"{'; '.join(details)} remain on {symbol}. Close/cancel/review them manually; "
                   "the app lock remains held.")
        except Exception as exc:
            output(f"Could not verify the test position ({type(exc).__name__}: {exc}); "
                   "the app lock remains held.")


def _wait_for_manual_close(mt5_module: Any, symbol: str, ticket: int,
                           expected_login: int, expected_server: str,
                           input_fn: Callable[[str], str],
                           output: Callable[[str], None]) -> bool:
    """Keep the app lock while the operator closes an unclosed test manually."""
    while True:
        try:
            input_fn(
                "Close the DEMO test position manually in MT5, then press Enter here "
                "to verify it is gone: "
            )
        except EOFError:
            return False
        try:
            account, login, server = _require_demo_identity(mt5_module)
            _check_terminal(mt5_module, require_trade_allowed=False)
            if login != int(expected_login) or server != expected_server:
                output("Still connected to a different account. Return to the original "
                       "DEMO account; the app lock remains held.")
                continue
            positions = mt5_module.positions_get(symbol=symbol)
            if positions is None:
                output("Could not verify positions yet; the app lock remains held. Try again.")
                continue
            orders = mt5_module.orders_get(symbol=symbol)
            if orders is None:
                output("Could not verify pending orders yet; the app lock remains held. Try again.")
                continue
            open_test = any(
                int(getattr(p, "ticket", 0) or 0) == int(ticket)
                and int(getattr(p, "magic", -1) or -1) == TEST_MAGIC
                for p in positions
            )
            if not open_test and not positions and not orders:
                return True
            output("A position or pending order remains on the test symbol. "
                   "Close/cancel/review it in MT5; the app lock remains held.")
        except Exception as exc:
            output(f"Could not verify manual close ({type(exc).__name__}: {exc}); "
                   "the app lock remains held. Try again after checking MT5.")


def _save_test_result(data_dir: Path, record: dict) -> None:
    path = data_dir / "demo_order_test_result.json"
    data_dir.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("not an object")
            existing = loaded
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise SmokeTestError(
                f"cannot safely update {path}; inspect it before another test ({exc})"
            ) from None
    position_id = int(record["position_id"])
    raw_ids = existing.get("position_ids", []) or []
    if not isinstance(raw_ids, list):
        raise SmokeTestError(f"{path} has an invalid position_ids field; inspect it first")
    position_ids = []
    for raw in raw_ids:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in position_ids:
            position_ids.append(value)
    if position_id not in position_ids:
        position_ids.append(position_id)
    raw_tests = existing.get("tests", []) or []
    if not isinstance(raw_tests, list):
        raise SmokeTestError(f"{path} has an invalid tests field; inspect it first")
    tests = list(raw_tests)
    tests.append(record)
    payload = {
        **existing,
        "position_ids": position_ids,
        "tests": tests[-100:],
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise SmokeTestError(f"could not record smoke-test position ID: {exc}") from None


def _update_test_status(data_dir: Path, position_id: int, status: str,
                        close_order_id: Optional[int] = None) -> None:
    path = data_dir / "demo_order_test_result.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        found = False
        for row in reversed(payload.get("tests", []) or []):
            if not isinstance(row, dict):
                continue
            if int(row.get("position_id", 0) or 0) == int(position_id):
                row["status"] = status
                if close_order_id:
                    row["close_order_id"] = int(close_order_id)
                row["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
                found = True
                break
        if not found:
            raise SmokeTestError("position record not found while updating status")
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise SmokeTestError(f"could not update smoke-test record: {exc}") from None


def _close_test_position(mt5_module: Any, cfg: Any, position: Any,
                         expected_login: int, expected_server: str,
                         sleep_fn: Callable[[float], None] = time.sleep,
                         monotonic_fn: Callable[[], float] = time.monotonic
                         ) -> tuple[bool, str, Optional[int]]:
    account = _require_demo(mt5_module)
    _check_terminal(mt5_module)
    if (int(getattr(account, "login", 0) or 0) != int(expected_login)
            or str(getattr(account, "server", "") or "").strip() != expected_server):
        return False, "MT5 account changed; return to the original DEMO account", None
    symbol = str(getattr(position, "symbol", ""))
    ticket = int(getattr(position, "ticket", 0) or 0)
    if not symbol or ticket <= 0 or int(getattr(position, "magic", -1) or -1) != TEST_MAGIC:
        return False, "position identity/magic does not match this smoke test", None
    positions = mt5_module.positions_get(symbol=symbol)
    if positions is None:
        return False, "could not verify the test position before closing", None
    matches = [p for p in positions
               if int(getattr(p, "ticket", 0) or 0) == ticket
               and int(getattr(p, "magic", -1) or -1) == TEST_MAGIC]
    if not matches:
        orders = _orders_for_symbol(mt5_module, symbol)
        if not positions and not orders:
            return True, "position already closed manually and absence verified", None
        return False, "other position/order remains on the test symbol; review it manually", None
    position = matches[0]
    tick = mt5_module.symbol_info_tick(symbol)
    if tick is None:
        return False, "no fresh tick to close the test position; close it manually in MT5", None
    try:
        _quote_age(tick, time.time())
    except SmokeTestError as exc:
        return False, f"{exc}; close it manually in MT5", None
    info = mt5_module.symbol_info(symbol)
    if info is None:
        return False, "symbol_info unavailable; close it manually in MT5", None
    volume = _number(getattr(position, "volume", None), "test position volume", positive=True)
    position_buy = getattr(position, "type", None) == getattr(
        mt5_module, "POSITION_TYPE_BUY", 0)
    if position_buy:
        close_type = mt5_module.ORDER_TYPE_SELL
        price = _number(getattr(tick, "bid", None), "close bid", positive=True)
    else:
        if getattr(position, "type", None) != getattr(mt5_module, "POSITION_TYPE_SELL", 1):
            return False, "unknown position side; close it manually in MT5", None
        close_type = mt5_module.ORDER_TYPE_BUY
        price = _number(getattr(tick, "ask", None), "close ask", positive=True)
    request = {
        "action": mt5_module.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": close_type,
        "position": ticket,
        "price": price,
        "deviation": 20,
        "magic": TEST_MAGIC,
        "comment": "GBM-DEMO-SMOKE-CLOSE",
        "type_time": mt5_module.ORDER_TIME_GTC,
        "type_filling": _filling_mode(mt5_module, info),
    }
    order_check = getattr(mt5_module, "order_check", None)
    if not callable(order_check):
        return False, "MT5 order_check unavailable; close it manually in MT5", None
    checked = order_check(request)
    if checked is None or getattr(checked, "retcode", None) not in {
            0, getattr(mt5_module, "TRADE_RETCODE_DONE", 10009)}:
        return False, "MT5 refused close preflight; close it manually in MT5", None
    result = mt5_module.order_send(request)
    accepted = {getattr(mt5_module, "TRADE_RETCODE_DONE", 10009),
                getattr(mt5_module, "TRADE_RETCODE_DONE_PARTIAL", 10010)}
    if result is None or getattr(result, "retcode", None) not in accepted:
        return False, (f"close order failed retcode={getattr(result, 'retcode', None)}; "
                       "close it manually in MT5"), None
    close_order_id = getattr(result, "order", None)
    if not _wait_for_test_position_closed(
            mt5_module, symbol, ticket, sleep_fn=sleep_fn,
            monotonic_fn=monotonic_fn):
        return False, ("broker accepted the close request but the test position is "
                       "still visible; close/review it manually in MT5"), close_order_id
    return True, "close order accepted and position absence verified", close_order_id


def _acquire_app_lock():
    """Hold the app's single-instance lock while a test order is open."""
    import main

    if not main._acquire_lock():
        raise SmokeTestError("Gold BookMap is already running; stop it before the smoke test")
    lock_path = Path(main._lock_path())
    try:
        owner_pid = int(lock_path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        # Release only if the contents still positively prove this process owns
        # the file; never unlink an unverified concurrent owner's lock.
        try:
            if int(lock_path.read_text(encoding="utf-8").strip()) == os.getpid():
                main._release_lock()
        except (OSError, ValueError):
            pass
        raise SmokeTestError("could not verify ownership of the app lock") from None
    if owner_pid != os.getpid():
        raise SmokeTestError("app lock is not owned by this smoke-test process")
    return main


def run_smoke_test(args: argparse.Namespace, *, mt5_module: Any = None,
                   cfg: Any = config, input_fn: Callable[[str], str] = input,
                   output: Callable[[str], None] = print,
                   now_fn: Callable[[], float] = time.time,
                   sleep_fn: Callable[[float], None] = time.sleep,
                   monotonic_fn: Callable[[], float] = time.monotonic,
                   interactive: Optional[bool] = None,
                   lock_acquire: Callable[[], Any] = _acquire_app_lock,
                   runtime_check: Optional[Callable[[Any], None]] = None) -> int:
    """Preflight by default; send at most one order only after typed consent."""
    mt5_module = mt5 if mt5_module is None else mt5_module
    if mt5_module is None:
        output("BLOCKED: MetaTrader5 Python package is not available")
        return 2
    try:
        reload_env = getattr(cfg, "reload_env", None)
        if callable(reload_env):
            reload_env()
        _check_config(cfg)
        if _pending_marker_path(cfg).exists():
            raise SmokeTestError(
                "a previous DEMO test is unresolved; inspect MT5 and run "
                "python tools/demo_order_smoke.py --recover"
            )
        runtime_check = _check_runtime_gates if runtime_check is None else runtime_check
        if args.send:
            if args.max_loss is None or not math.isfinite(float(args.max_loss)) or args.max_loss <= 0:
                raise SmokeTestError("--send requires a positive --max-loss in account currency")
            if interactive is None:
                interactive = bool(sys.stdin and sys.stdin.isatty())
            if not interactive:
                raise SmokeTestError("--send requires an interactive terminal for typed confirmation")

        lock_owner = lock_acquire() if args.send else None
        initialized = False
        try:
            runtime_check(cfg)
            if not cfg.mt5_initialize(mt5_module):
                raise SmokeTestError(f"MT5 initialize failed: {mt5_module.last_error()}")
            initialized = True

            plan = _make_plan(mt5_module, cfg, args.side, now_fn())
            _print_plan(plan, output)
            if args.max_loss is not None:
                cap = _number(args.max_loss, "--max-loss", positive=True)
                output(f"  Operator estimated-loss cap: {cap:.2f} {plan.currency} "
                       "(realized loss can differ due to execution costs/slippage)")
                if plan.estimated_loss > cap:
                    raise SmokeTestError(
                        f"estimated loss {plan.estimated_loss:.2f} {plan.currency} exceeds "
                        f"your --max-loss cap {cap:.2f} {plan.currency}"
                    )
            if not args.send:
                output("PREFLIGHT PASS: no order was sent. To send, use --send --max-loss <amount>.")
                return 0

            challenge = _format_challenge(plan)
            answer = input_fn(
                f"Type exactly '{challenge}' to submit ONE DEMO order; anything else cancels: "
            ).strip()
            if answer != challenge:
                output("CANCELLED: confirmation did not match; no order was sent")
                return 1

            # Recheck session/risk/overtrade gates and then reconnect, account,
            # positions, snapshot and quote after operator input. RiskManager
            # initializes/shuts down its own MT5 session, so reconnect before
            # preparing the final request.
            if initialized:
                try:
                    mt5_module.shutdown()
                except Exception:
                    pass
                initialized = False
            runtime_check(cfg)
            if not cfg.mt5_initialize(mt5_module):
                raise SmokeTestError(f"MT5 reinitialize failed: {mt5_module.last_error()}")
            initialized = True
            refreshed = _make_plan(mt5_module, cfg, args.side, now_fn())
            if (refreshed.account_login != plan.account_login
                    or refreshed.account_server != plan.account_server):
                raise SmokeTestError("MT5 account changed during confirmation; no order sent")
            point = _number(getattr(mt5_module.symbol_info(refreshed.symbol), "point", None),
                            "symbol point", positive=True)
            allowed_move = max(point * 10.0, refreshed.atr * 0.05)
            if abs(refreshed.entry - plan.entry) > allowed_move:
                raise SmokeTestError(
                    "quote moved materially during confirmation; no order sent; rerun preflight"
                )
            if refreshed.volume != plan.volume:
                raise SmokeTestError("broker volume changed during confirmation; no order sent")
            if refreshed.estimated_loss > float(args.max_loss):
                raise SmokeTestError("updated estimated loss exceeds --max-loss; no order sent")
            attempt_id = uuid.uuid4().hex
            _write_pending_marker(cfg, refreshed, attempt_id)
            output(f"Submitting one {refreshed.side} DEMO order on {refreshed.symbol} "
                   f"at {refreshed.entry:.5f}; no retries will be made.")
            result = mt5_module.order_send(refreshed.request)
            accepted = {getattr(mt5_module, "TRADE_RETCODE_DONE", 10009),
                        getattr(mt5_module, "TRADE_RETCODE_DONE_PARTIAL", 10010)}
            if result is None or getattr(result, "retcode", None) not in accepted:
                output("ORDER NOT CONFIRMED. Inspect MT5; the test will not resend.")
                output(f"  retcode={getattr(result, 'retcode', None)} "
                       f"comment={getattr(result, 'comment', '')}")
                resolved = _wait_for_no_test_position(
                    mt5_module, refreshed.symbol, refreshed.account_login,
                    refreshed.account_server, input_fn, output)
                if resolved:
                    if _clear_pending_marker(cfg, attempt_id):
                        output("No smoke-test position or pending order remains. Do not rerun until you have "
                               "reviewed the MT5 result.")
                    else:
                        output("No position is visible, but the safety marker could not be "
                               "cleared. Run --recover before restarting the bot.")
                else:
                    output("URGENT: result remains unresolved. Do not restart the bot or "
                           "retry; inspect MT5 and close any smoke-test position manually.")
                return 3

            try:
                position = _wait_for_test_position(
                    mt5_module, refreshed.symbol, sleep_fn=sleep_fn,
                    monotonic_fn=monotonic_fn)
            except Exception as exc:
                output("Broker accepted the order but position verification failed; "
                       "inspect MT5 manually and do not rerun.")
                output(f"  verification error: {type(exc).__name__}: {exc}")
                resolved = _wait_for_no_test_position(
                    mt5_module, refreshed.symbol, refreshed.account_login,
                    refreshed.account_server, input_fn, output)
                if not resolved:
                    output("URGENT: result unresolved. Do not restart the bot or retry.")
                elif not _clear_pending_marker(cfg, attempt_id):
                    output("No position remains, but the safety marker could not be cleared. "
                           "Run --recover before restarting the bot.")
                return 3
            if position is None:
                output("Broker returned success but the test position was not visible in time.")
                resolved = _wait_for_no_test_position(
                    mt5_module, refreshed.symbol, refreshed.account_login,
                    refreshed.account_server, input_fn, output)
                if resolved:
                    if _clear_pending_marker(cfg, attempt_id):
                        output("No smoke-test position remains after manual review. Do not retry "
                               "until you have reviewed the broker result.")
                    else:
                        output("No test-symbol position/order is visible, but the safety marker "
                               "could not be cleared. Run --recover before restarting the bot.")
                else:
                    output("URGENT: result unresolved. Do not restart the bot or retry.")
                return 3
            position_id = int(getattr(position, "ticket", 0) or 0)
            if position_id <= 0:
                output("Position was found but has no usable ticket; close it manually.")
                resolved = _wait_for_no_test_position(
                    mt5_module, refreshed.symbol, refreshed.account_login,
                    refreshed.account_server, input_fn, output)
                if not resolved:
                    output("URGENT: result unresolved. Do not restart the bot or retry.")
                elif not _clear_pending_marker(cfg, attempt_id):
                    output("No position remains, but the safety marker could not be cleared. "
                           "Run --recover before restarting the bot.")
                return 3

            record = {
                "attempt_id": attempt_id,
                "account_login": refreshed.account_login,
                "account_server": refreshed.account_server,
                "position_id": position_id,
                "order_id": getattr(result, "order", None),
                "deal_id": getattr(result, "deal", None),
                "symbol": refreshed.symbol,
                "side": refreshed.side,
                "volume": refreshed.volume,
                "entry": refreshed.entry,
                "sl": refreshed.sl,
                "tp": refreshed.tp,
                "magic": TEST_MAGIC,
                "status": "OPEN",
                "recorded_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
            try:
                _save_test_result(Path(cfg.DATA_DIR), record)
            except SmokeTestError as exc:
                output(f"POSITION OPEN but test record could not be saved: {exc}")
                try:
                    closed, reason, _close_id = _close_test_position(
                        mt5_module, cfg, position, refreshed.account_login,
                        refreshed.account_server, sleep_fn=sleep_fn,
                        monotonic_fn=monotonic_fn)
                except Exception as close_exc:
                    closed = False
                    reason = f"close exception {type(close_exc).__name__}: {close_exc}"
                if closed:
                    if _clear_pending_marker(cfg, attempt_id):
                        output("The unrecorded smoke-test position was closed. Review MT5 before any retry.")
                    else:
                        output("Position closed, but safety marker remains. Run --recover before "
                               "restarting the bot.")
                else:
                    output(f"CLOSE WARNING: {reason}. The app lock stays held until manual closure.")
                    manually_closed = _wait_for_manual_close(
                        mt5_module, refreshed.symbol, position_id,
                        refreshed.account_login, refreshed.account_server,
                        input_fn, output)
                    if manually_closed:
                        if _clear_pending_marker(cfg, attempt_id):
                            output("Manual close verified. Review MT5 before any retry.")
                        else:
                            output("Manual close verified, but safety marker remains. "
                                   "Run --recover before restarting the bot.")
                    else:
                        output("URGENT: the position may remain open. Do not restart the bot; "
                               "close it manually in MT5 first.")
                return 4
            position_issue = _position_safety_issue(
                mt5_module, position, refreshed, float(args.max_loss), cfg)
            if position_issue:
                output(f"SAFETY FAILURE: {position_issue}. Attempting immediate close; "
                       "the app lock stays held.")
                try:
                    closed, close_reason, close_order_id = _close_test_position(
                        mt5_module, cfg, position, refreshed.account_login,
                        refreshed.account_server, sleep_fn=sleep_fn,
                        monotonic_fn=monotonic_fn)
                except Exception as exc:
                    closed = False
                    close_reason = f"close exception {type(exc).__name__}: {exc}"
                    close_order_id = None
                if closed:
                    try:
                        _update_test_status(Path(cfg.DATA_DIR), position_id,
                                            "CLOSED", close_order_id)
                    except SmokeTestError as exc:
                        output(f"Position is closed but test status could not be updated: {exc}")
                        return 4
                    if _clear_pending_marker(cfg, attempt_id):
                        output("SAFETY CLOSE COMPLETE: the unexpected DEMO position was "
                               "closed and its absence verified. Do not retry until the "
                               "broker discrepancy is understood.")
                    else:
                        output("Position closed, but the safety marker remains. Run --recover "
                               "before restarting the bot.")
                    return 4
                output(f"CLOSE WARNING: {close_reason}. The app lock remains held until "
                       "manual closure is verified.")
                manually_closed = _wait_for_manual_close(
                    mt5_module, refreshed.symbol, position_id,
                    refreshed.account_login, refreshed.account_server, input_fn, output)
                if manually_closed:
                    try:
                        _update_test_status(Path(cfg.DATA_DIR), position_id, "CLOSED")
                    except SmokeTestError as exc:
                        output(f"Manual close verified, but result status update failed: {exc}")
                        return 4
                    if _clear_pending_marker(cfg, attempt_id):
                        output("SAFETY CLOSE COMPLETE: manual closure was verified. "
                               "Do not retry until the broker discrepancy is understood.")
                    else:
                        output("Manual close verified, but safety marker remains. Run --recover "
                               "before restarting the bot.")
                else:
                    output("URGENT: the position may remain open. Do not restart the bot; "
                           "close it manually in MT5 first.")
                return 4

            output(f"SUCCESS: DEMO position is open and visible: ticket={position_id}, "
                   f"{refreshed.side} {refreshed.symbol} {refreshed.volume_text}.")
            output("Switch to MT5 now and verify the position. Press Enter when ready "
                   "for the tool to close it; this test has no leave-open option.")
            try:
                input_fn("Press Enter to request the close. If you close it manually, "
                         "press Enter after it is gone: ")
            except EOFError:
                pass

            try:
                closed, reason, close_order_id = _close_test_position(
                    mt5_module, cfg, position, refreshed.account_login,
                    refreshed.account_server, sleep_fn=sleep_fn,
                    monotonic_fn=monotonic_fn)
            except Exception as exc:
                closed = False
                reason = f"close exception {type(exc).__name__}: {exc}"
                close_order_id = None
            if closed:
                try:
                    _update_test_status(Path(cfg.DATA_DIR), position_id, "CLOSED", close_order_id)
                except SmokeTestError as exc:
                    output(f"Position is closed, but test status could not be updated: {exc}")
                    return 4
                if not _clear_pending_marker(cfg, attempt_id):
                    output("Position is closed, but the persistent safety marker could not be "
                           "cleared. Run --recover before restarting the bot.")
                    return 4
                output(f"CLOSED: smoke-test position {position_id} was closed after verification.")
                return 0
            output(f"CLOSE WARNING: {reason}. The app lock remains held until the test "
                   "position is closed or verified absent.")
            manually_closed = _wait_for_manual_close(
                mt5_module, refreshed.symbol, position_id,
                refreshed.account_login, refreshed.account_server, input_fn, output)
            if manually_closed:
                try:
                    _update_test_status(Path(cfg.DATA_DIR), position_id, "CLOSED")
                except SmokeTestError as exc:
                    output(f"Manual close verified, but result status update failed: {exc}")
                    return 4
                if not _clear_pending_marker(cfg, attempt_id):
                    output("Manual close verified, but the persistent safety marker remains. "
                           "Run --recover before restarting the bot.")
                    return 4
                output(f"CLOSED: smoke-test position {position_id} was closed manually "
                       "and verified absent.")
                return 0
            output("URGENT: the position may remain open. Do not restart the bot; "
                   "close it manually in MT5 first.")
            return 4
        finally:
            if initialized:
                try:
                    mt5_module.shutdown()
                except Exception:
                    pass
            if lock_owner is not None:
                try:
                    lock_owner._release_lock()
                except Exception:
                    pass
    except SmokeTestError as exc:
        output(f"BLOCKED: {exc}")
        return 2
    except Exception as exc:
        output(f"ERROR: {type(exc).__name__}: {exc}")
        return 2


def run_recovery(*, mt5_module: Any = None, cfg: Any = config,
                 input_fn: Callable[[str], str] = input,
                 output: Callable[[str], None] = print,
                 interactive: Optional[bool] = None,
                 lock_acquire: Callable[[], Any] = _acquire_app_lock) -> int:
    """Safely clear an interrupted-test marker after manual position review."""
    mt5_module = mt5 if mt5_module is None else mt5_module
    if mt5_module is None:
        output("BLOCKED: MetaTrader5 Python package is not available")
        return 2
    try:
        reload_env = getattr(cfg, "reload_env", None)
        if callable(reload_env):
            reload_env()
        marker_path = _pending_marker_path(cfg)
        if not marker_path.exists():
            output("No unresolved DEMO order-test marker exists.")
            return 0
        if interactive is None:
            interactive = bool(sys.stdin and sys.stdin.isatty())
        if not interactive:
            raise SmokeTestError("--recover requires an interactive terminal")

        lock_owner = lock_acquire()
        initialized = False
        try:
            if not cfg.mt5_initialize(mt5_module):
                raise SmokeTestError(f"MT5 initialize failed: {mt5_module.last_error()}")
            initialized = True
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise SmokeTestError(f"pending marker is unreadable; do not delete it ({exc})") from None
            if not isinstance(marker, dict):
                raise SmokeTestError("pending marker is not an object; do not delete it")
            attempt_id = str(marker.get("attempt_id", "")).strip()
            symbol = str(marker.get("symbol", "")).strip()
            if (not attempt_id or not symbol
                    or str(marker.get("status", "")).upper() != "PENDING"
                    or int(marker.get("magic", -1) or -1) != TEST_MAGIC):
                raise SmokeTestError("pending marker identity is invalid; do not delete it")
            account, login, server = _require_demo_identity(mt5_module)
            _check_terminal(mt5_module, require_trade_allowed=False)
            try:
                expected_login = int(marker.get("account_login", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                expected_login = 0
            expected_server = str(marker.get("account_server", "") or "").strip()
            if login != expected_login or server != expected_server:
                raise SmokeTestError(
                    "MT5 is not connected to the original DEMO account; return to it before recovery"
                )
            positions = mt5_module.positions_get(symbol=symbol)
            if positions is None:
                raise SmokeTestError("positions_get failed; marker remains and bot entries stay blocked")
            orders = mt5_module.orders_get(symbol=symbol)
            if orders is None:
                raise SmokeTestError("orders_get failed; marker remains and bot entries stay blocked")
            if positions or orders:
                if positions:
                    output("Open position(s) found on the test symbol: " + ", ".join(
                        f"{getattr(p, 'ticket', '?')} (magic {getattr(p, 'magic', 'unknown')})"
                        for p in positions))
                if orders:
                    output("Pending order(s) found on the test symbol: " + ", ".join(
                        f"{getattr(o, 'ticket', '?')} (magic {getattr(o, 'magic', 'unknown')})"
                        for o in orders))
                output("Close/cancel/review them manually in MT5 before recovery.")
                if not _wait_for_no_test_position(
                        mt5_module, symbol, login, server, input_fn, output):
                    output("Marker remains. Do not restart the bot until all test-symbol "
                           "positions and pending orders are resolved.")
                    return 4

            challenge = f"RESOLVE DEMO TEST {symbol} {login}"
            answer = input_fn(
                f"Type exactly '{challenge}' only after reviewing MT5 and confirming "
                "no position or pending order remains on the test symbol: "
            ).strip()
            if answer != challenge:
                output("CANCELLED: marker remains; normal strategy entries stay blocked")
                return 1

            # Verify again after the confirmation prompt.
            account, current_login, current_server = _require_demo_identity(mt5_module)
            _check_terminal(mt5_module, require_trade_allowed=False)
            if current_login != login or current_server != server:
                raise SmokeTestError("MT5 account changed during recovery; marker remains")
            positions = mt5_module.positions_get(symbol=symbol)
            orders = mt5_module.orders_get(symbol=symbol)
            if positions is None or orders is None or positions or orders:
                raise SmokeTestError(
                    "a position/order remains on the test symbol or is unverifiable; marker remains"
                )

            # If a position ID was recorded before interruption, mark its test
            # record closed before removing the entry-blocking marker.
            result_path = Path(cfg.DATA_DIR) / "demo_order_test_result.json"
            if result_path.exists():
                try:
                    result_data = json.loads(result_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise SmokeTestError(
                        f"test-result file is unreadable; pending marker remains ({exc})"
                    ) from None
                for row in result_data.get("tests", []) or []:
                    if (isinstance(row, dict)
                            and str(row.get("attempt_id", "")) == attempt_id
                            and str(row.get("status", "")).upper() == "OPEN"):
                        pos_id = int(row.get("position_id", 0) or 0)
                        if pos_id > 0:
                            _update_test_status(Path(cfg.DATA_DIR), pos_id, "CLOSED")
                        break
            if not _clear_pending_marker(cfg, attempt_id):
                raise SmokeTestError("could not clear pending marker; entries remain blocked")
            output("RECOVERY COMPLETE: no test-symbol position/order remains and the safety "
                   "marker was cleared. Run one fresh `python main.py --no-new-entries` cycle "
                   "before another smoke-test preflight or normal strategy run.")
            return 0
        finally:
            if initialized:
                try:
                    mt5_module.shutdown()
                except Exception:
                    pass
            if lock_owner is not None:
                try:
                    lock_owner._release_lock()
                except Exception:
                    pass
    except SmokeTestError as exc:
        output(f"BLOCKED: {exc}")
        return 2
    except Exception as exc:
        output(f"ERROR: {type(exc).__name__}: {exc}")
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="One-shot MT5 DEMO order-path smoke test; separate from strategy decisions."
    )
    parser.add_argument("--side", choices=("BUY", "SELL"),
                        help="test direction; the tool never chooses a side for you")
    parser.add_argument("--recover", action="store_true",
                        help="resolve an interrupted test after confirming no test-symbol position/order remains")
    parser.add_argument("--send", action="store_true",
                        help="after all DEMO-only checks, prompt before sending one order")
    parser.add_argument("--max-loss", type=float,
                        help="required with --send; maximum estimated stop loss in account currency (realized loss can differ)")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.recover:
        if args.side is not None or args.send or args.max_loss is not None:
            parser.error("--recover cannot be combined with --side, --send, or --max-loss")
        return run_recovery()
    if args.side is None:
        parser.error("--side BUY or SELL is required unless --recover is used")
    return run_smoke_test(args)


if __name__ == "__main__":  # pragma: no cover - exercised on Windows with MT5
    raise SystemExit(main())
