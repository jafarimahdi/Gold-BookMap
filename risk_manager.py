"""
risk_manager.py
===============
Risk circuit-breaker for the gold trading system (Section #11 / Stage R1).

Protects the account with unified intraday loss caps and a high-water-mark
max-drawdown kill switch:

  - Session rollover (`RISK_SESSION_ROLL_MODE`, default ``cme_18et``):
    rolls the intraday risk budget at 18:00 America/New_York (CME Globex Gold
    session open, DST-aware across EDT/EST with tzdata-free fallback) instead
    of raw 00:00 UTC midnight so a late-evening loss at 23:30 UTC (18:30 ET)
    cannot unlock 30 minutes later inside the same Globex session.
  - Daily loss limit (`DAILY_LOSS_LIMIT_PCT` % of equity AND Stage-4R
    `STAGE4R_DAILY_MAX_LOSS_PTS` Gold points when `RISK_ENFORCE_STAGE4R_PTS=1`):
    halts new entries until the next session rollover (`18:00 ET`), persisted
    atomically to `risk_state.json` so restarts cannot bypass it. Open bot
    floating losses (`min(0, floating)`) count asymmetrically so underwater
    positions block stacking while paper profits never mask realized losses.
  - Max drawdown (`MAX_DRAWDOWN_PCT` from persisted `peak_equity` high-water
    mark): latches `halt_until = "MANUAL_RESET"` (`kill_switch = True`) until
    explicitly cleared via `RiskManager().reset()`; expiring a daily halt
    never erases `peak_equity`.

Works with or without MT5 (falls back to `trade_memory.json` realized session
records when MT5 is absent, or 0.0 when no trades exist).
"""

from __future__ import annotations

import contextlib
import csv
import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo as _ZoneInfo
except Exception:  # pragma: no cover
    _ZoneInfo = None  # type: ignore[assignment]

try:
    import MetaTrader5 as mt5
except ImportError:  # pragma: no cover
    mt5 = None

import config

logger = logging.getLogger(__name__)

_DEFAULT_DATA_DIR = Path(config.DATA_DIR)
STATE_FILE = _DEFAULT_DATA_DIR / "risk_state.json"
POSITION_IDS_FILE = _DEFAULT_DATA_DIR / "tracked_bot_positions.json"
PLUMBING_DEALS_FILE = _DEFAULT_DATA_DIR / "plumbing_test_deals.json"
_DEFAULT_STATE_FILE = STATE_FILE
_DEFAULT_POS_FILE = POSITION_IDS_FILE
_DEFAULT_PLUMBING_FILE = PLUMBING_DEALS_FILE
_CONFTEST_ISO_DIR_NAME = "_pytest_isolated_data"
_HALT_TRANSIENT_KEYS = ("halt_until", "halted", "kill_switch", "halt_type", "reason", "session_key")


def _resolve_data_dir() -> Path:
    live_dir = getattr(config, "DATA_DIR", None)
    return Path(live_dir) if live_dir else _DEFAULT_DATA_DIR


def _resolve_bound_file(cur_val: Path, default_val: Path, filename: str) -> Path:
    """Honor explicit monkeypatched file paths while following dynamic `config.DATA_DIR`."""
    cur_p = Path(cur_val)
    if cur_p != default_val and cur_p.parent.name != _CONFTEST_ISO_DIR_NAME:
        return cur_p
    return _resolve_data_dir() / filename


def _resolve_state_file() -> Path:
    return _resolve_bound_file(STATE_FILE, _DEFAULT_STATE_FILE, "risk_state.json")


def _resolve_position_ids_file() -> Path:
    return _resolve_bound_file(POSITION_IDS_FILE, _DEFAULT_POS_FILE, "tracked_bot_positions.json")


def _resolve_plumbing_file() -> Path:
    return _resolve_bound_file(PLUMBING_DEALS_FILE, _DEFAULT_PLUMBING_FILE, "plumbing_test_deals.json")


def _positive_id(value):
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _plumbing_position_ids() -> set:
    """Return explicit plumbing-test positions excluded from risk PnL."""
    ids = set()
    data_dir = _resolve_data_dir()
    for path in (
        data_dir / "demo_order_test_result.json",
        _resolve_plumbing_file(),
    ):
        try:
            if not path.exists():
                continue
            state = json.loads(path.read_text(encoding="utf-8-sig"))
            values = state.get("position_ids", []) if "result" in path.name else [
                row.get("position_id") for row in state.get("deals", []) or []
            ]
            for value in values or []:
                number = _positive_id(value)
                if number:
                    ids.add(number)
        except (json.JSONDecodeError, OSError, AttributeError):
            continue
    return ids


def _tracked_strategy_position_ids() -> set:
    """Load persisted strategy positions, excluding plumbing tests."""
    ids = set()
    excluded = _plumbing_position_ids()
    pos_file = _resolve_position_ids_file()
    try:
        if pos_file.exists():
            state = json.loads(pos_file.read_text(encoding="utf-8-sig"))
            raw_vals = state if isinstance(state, list) else (state.get("position_ids", []) or [])
            for value in raw_vals:
                number = _positive_id(value)
                if number and number not in excluded:
                    ids.add(number)
    except (json.JSONDecodeError, OSError, AttributeError):
        pass

    # Compatibility for positions created before tracked_bot_positions.json.
    data_dir = _resolve_data_dir()
    cand_csvs: List[Path] = [data_dir / "decisions_log.csv"]
    try:
        if data_dir.exists():
            dated = sorted(data_dir.glob("decisions_*.csv"))[-2:]
            for dp in dated:
                if dp not in cand_csvs:
                    cand_csvs.append(dp)
    except OSError:
        pass
    for decisions in cand_csvs:
        try:
            if decisions.exists():
                with open(decisions, "r", newline="", encoding="utf-8-sig") as fh:
                    for row in csv.DictReader(fh):
                        if (row.get("exec_status") or "").strip() != "EXECUTED":
                            continue
                        number = _positive_id(row.get("order_id"))
                        if number and number not in excluded:
                            ids.add(number)
        except (OSError, csv.Error):
            pass
    return ids


def _deal_value(deal) -> float:
    return sum(float(getattr(deal, name, 0.0) or 0.0)
               for name in ("profit", "swap", "commission", "fee"))


def _deal_timestamp(deal):
    raw = getattr(deal, "time_msc", None) or getattr(deal, "time", None)
    try:
        value = float(raw)
        if value > 1e12:
            value /= 1000.0
        return value
    except (TypeError, ValueError):
        return None


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _nth_sunday(year: int, month: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (6 - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (n - 1))


def _us_eastern_offset_hours_utc(dt_utc: datetime) -> int:
    """US DST rule (since 2007): 2nd Sun Mar 07:00Z -> 1st Sun Nov 06:00Z."""
    u = _ensure_utc(dt_utc)
    yr = u.year
    dst_start = datetime.combine(_nth_sunday(yr, 3, 2), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=7)
    dst_end = datetime.combine(_nth_sunday(yr, 11, 1), datetime.min.time(), tzinfo=timezone.utc) + timedelta(hours=6)
    return -4 if (dst_start <= u < dst_end) else -5


def _to_us_eastern(dt_utc: datetime) -> datetime:
    u = _ensure_utc(dt_utc)
    if _ZoneInfo is not None:
        try:
            return u.astimezone(_ZoneInfo("America/New_York"))
        except Exception:
            pass
    off = _us_eastern_offset_hours_utc(u)
    return u.astimezone(timezone(timedelta(hours=off)))


def _et_1800_utc(cal_date: date) -> datetime:
    """Exact UTC timestamp of 18:00:00 America/New_York on `cal_date`."""
    if _ZoneInfo is not None:
        try:
            et_dt = datetime(cal_date.year, cal_date.month, cal_date.day, 18, 0, 0, tzinfo=_ZoneInfo("America/New_York"))
            return et_dt.astimezone(timezone.utc)
        except Exception:
            pass
    # By 18:00 ET on any calendar day (including DST transition Sundays at 02:00 ET),
    # the afternoon offset equals the UTC offset at 22:30Z of `cal_date`.
    probe_utc = datetime(cal_date.year, cal_date.month, cal_date.day, 22, 30, 0, tzinfo=timezone.utc)
    off = _us_eastern_offset_hours_utc(probe_utc)
    et_tz = timezone(timedelta(hours=off))
    return datetime(cal_date.year, cal_date.month, cal_date.day, 18, 0, 0, tzinfo=et_tz).astimezone(timezone.utc)


def session_bounds_utc(
    now: Optional[datetime] = None,
    mode: Optional[str] = None,
) -> Tuple[datetime, datetime, str]:
    """Return `(session_start_utc, session_end_utc, session_key)` for the active risk session."""
    u_now = _ensure_utc(now or _now())
    eff_mode = (mode or str(getattr(config, "RISK_SESSION_ROLL_MODE", "cme_18et") or "cme_18et")).strip().lower()

    if eff_mode == "utc":
        start = u_now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        return start, end, start.date().isoformat()

    if eff_mode == "local":
        tz_name = str(getattr(config, "LOCAL_TZ", "Europe/Budapest") or "Europe/Budapest")
        if _ZoneInfo is not None:
            try:
                loc_tz = _ZoneInfo(tz_name)
                loc_now = u_now.astimezone(loc_tz)
                loc_start = loc_now.replace(hour=0, minute=0, second=0, microsecond=0)
                loc_end = (loc_start + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
                return loc_start.astimezone(timezone.utc), loc_end.astimezone(timezone.utc), loc_start.date().isoformat()
            except Exception:
                pass
        start = u_now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + timedelta(days=1), start.date().isoformat()

    # Default `cme_18et`: CME Globex Gold session rolls at 18:00 America/New_York.
    et_now = _to_us_eastern(u_now)
    trade_date = et_now.date() + timedelta(days=1) if et_now.hour >= 18 else et_now.date()
    prev_date = trade_date - timedelta(days=1)
    return _et_1800_utc(prev_date), _et_1800_utc(trade_date), trade_date.isoformat()


def _end_of_day(now: datetime) -> datetime:
    """Next risk-session rollover timestamp in UTC (18:00 ET under default `cme_18et`)."""
    return session_bounds_utc(now)[1]


def _trade_memory_session_totals(
    start_ep: float,
    end_ep: float,
    data_dir: Optional[Path] = None,
) -> Tuple[float, float, set]:
    """Read `(usd_pnl, pts_pnl, covered_base_tickets)` from `trade_memory.json` in `[start_ep, end_ep]`."""
    mem_file = (Path(data_dir) if data_dir else _resolve_data_dir()) / "trade_memory.json"
    if not mem_file.exists():
        return 0.0, 0.0, set()
    try:
        data = json.loads(mem_file.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            return 0.0, 0.0, set()
    except (json.JSONDecodeError, OSError):
        return 0.0, 0.0, set()

    tot_usd = 0.0
    tot_pts = 0.0
    covered: set = set()
    for rec in data.get("closed", []) or []:
        if not isinstance(rec, dict):
            continue
        try:
            c_ts = float(rec.get("closed_ts", 0.0) or 0.0)
            if c_ts < start_ep - 1e-6 or c_ts > end_ep + 1e-6:
                continue
            tot_usd += float(rec.get("pnl_usd_est", 0.0) or 0.0)
            tot_pts += float(rec.get("gain_pts", 0.0) or 0.0)
            raw_tkt = str(rec.get("ticket") or "").split(":")[0].strip()
            if raw_tkt:
                covered.add(raw_tkt)
        except (TypeError, ValueError):
            continue
    return round(tot_usd, 4), round(tot_pts, 4), covered


class RiskManager:
    """Unified session-loss (`%` equity + Stage-4R points) + latching drawdown circuit breaker."""

    def __init__(
        self,
        daily_loss_pct: Optional[float] = None,
        max_drawdown_pct: Optional[float] = None,
        max_daily_loss_pts: Optional[float] = None,
        state_file: Optional[Path] = None,
    ):
        self.daily_loss_pct = (
            float(daily_loss_pct)
            if daily_loss_pct is not None
            else float(getattr(config, "DAILY_LOSS_LIMIT_PCT", 3.0) or 0.0)
        )
        self.max_drawdown_pct = (
            float(max_drawdown_pct)
            if max_drawdown_pct is not None
            else float(getattr(config, "MAX_DRAWDOWN_PCT", 10.0) or 0.0)
        )
        if max_daily_loss_pts is not None:
            self.max_daily_loss_pts = max(0.0, float(max_daily_loss_pts))
        elif (
            bool(getattr(config, "RISK_ENFORCE_STAGE4R_PTS", True))
            and bool(getattr(config, "STAGE4R_DAILY_LOSS_BREAKER_ENABLED", True))
        ):
            self.max_daily_loss_pts = max(
                0.0, float(getattr(config, "STAGE4R_DAILY_MAX_LOSS_PTS", 12.0) or 0.0)
            )
        else:
            self.max_daily_loss_pts = 0.0

        self._explicit_state_file: Optional[Path] = Path(state_file) if state_file is not None else None
        self._mt5_depth: int = 0
        self._mt5_ready: bool = False
        self._last_session_deals: Optional[Tuple[float, float, List[object]]] = None

    def _target_state_file(self) -> Path:
        if self._explicit_state_file is not None:
            return self._explicit_state_file
        return _resolve_state_file()

    def _target_data_dir(self) -> Path:
        if self._explicit_state_file is not None:
            return self._explicit_state_file.parent
        return _resolve_data_dir()

    @contextlib.contextmanager
    def _mt5_session(self):
        """Share a single `mt5_initialize()` / `mt5.shutdown()` pair across nested calls."""
        if mt5 is None:
            yield False
            return
        if self._mt5_depth > 0:
            yield self._mt5_ready
            return
        self._mt5_depth = 1
        self._mt5_ready = False
        try:
            self._mt5_ready = bool(config.mt5_initialize(mt5))
            yield self._mt5_ready
        except Exception:
            yield False
        finally:
            self._mt5_depth = 0
            was_ready = self._mt5_ready
            self._mt5_ready = False
            if was_ready:
                try:
                    mt5.shutdown()
                except Exception:
                    pass

    # -- persisted halt state --------------------------------------------------
    def load_state(self) -> dict:
        sf = self._target_state_file()
        if sf.exists():
            try:
                data = json.loads(sf.read_text(encoding="utf-8-sig"))
                return data if isinstance(data, dict) else {}
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def save_state(self, state: dict) -> None:
        sf = self._target_state_file()
        tmp = sf.with_name(sf.name + ".tmp")
        try:
            sf.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(state), encoding="utf-8")
            os.replace(tmp, sf)
        except OSError as exc:
            logger.warning("Could not persist risk state: %s", exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass

    def clear_state(self) -> None:
        try:
            self._target_state_file().unlink(missing_ok=True)
        except OSError:
            pass

    def _expire_transient_halt(self, state: dict) -> dict:
        """Clear expired session-halt fields while preserving `peak_equity` high-water mark."""
        cleaned = {k: v for k, v in state.items() if k not in _HALT_TRANSIENT_KEYS}
        if cleaned:
            self.save_state(cleaned)
        else:
            self.clear_state()
        return cleaned

    # -- account PnL & open floating exposure ----------------------------------
    def account_equity(self) -> float:
        if mt5 is None:
            return float(config.ACCOUNT_EQUITY)
        try:
            with self._mt5_session() as ready:
                if not ready:
                    return float(config.ACCOUNT_EQUITY)
                info = mt5.account_info()
                if info is not None and getattr(info, "equity", None) is not None:
                    return float(info.equity)
        except Exception:
            pass
        return float(config.ACCOUNT_EQUITY)

    def _query_mt5_session_deals(self, session_start: datetime, u_now: datetime) -> Optional[List[object]]:
        """Return deduplicated MT5 strategy deals inside `[session_start, u_now]`, or None if MT5 unavailable."""
        if mt5 is None:
            return None
        start_ep = session_start.timestamp()
        now_ep = u_now.timestamp()
        cached = self._last_session_deals
        if cached is not None and abs(cached[0] - start_ep) < 1e-6 and abs(cached[1] - now_ep) < 1e-6:
            return cached[2]

        with self._mt5_session() as ready:
            if not ready:
                return None
            try:
                from step4_mt5_execution import resolve_mt5_deal_epoch_utc
                live_tick = None
                sym_tick_fn = getattr(mt5, "symbol_info_tick", None)
                if callable(sym_tick_fn):
                    try:
                        live_tick = sym_tick_fn(config.MT5_SYMBOL)
                    except Exception:
                        live_tick = None

                tracked = _tracked_strategy_position_ids()
                if tracked:
                    unique: Dict[object, object] = {}
                    for position_id in sorted(tracked):
                        try:
                            deals = mt5.history_deals_get(position=int(position_id)) or []
                        except (TypeError, ValueError):
                            continue
                        for deal in deals:
                            deal_id = getattr(deal, "ticket", None)
                            key = (
                                deal_id
                                if deal_id not in (None, "", 0)
                                else (position_id, getattr(deal, "time_msc", getattr(deal, "time", "")))
                            )
                            deal_ts = _deal_timestamp(deal)
                            if deal_ts is not None:
                                deal_ts = resolve_mt5_deal_epoch_utc(
                                    deal_ts, now_ep, tick=live_tick, config_mod=config
                                )
                                if deal_ts < start_ep or deal_ts > now_ep:
                                    continue
                            unique[key] = deal
                    if unique:
                        res_list = list(unique.values())
                        self._last_session_deals = (start_ep, now_ep, res_list)
                        return res_list

                # Compatibility fallback for fresh installation or empty tracked deals today.
                query_end = u_now
                if live_tick is not None:
                    try:
                        tmsc = int(getattr(live_tick, "time_msc", 0) or 0)
                        tsec = float(getattr(live_tick, "time", 0) or 0.0)
                        q_ep = (tmsc / 1000.0) if tmsc > 0 else tsec
                        if q_ep > now_ep + 1800.0:
                            k_tz = round((q_ep - now_ep) / 3600.0)
                            if 1 <= k_tz <= 14:
                                query_end = u_now + timedelta(seconds=k_tz * 3600)
                    except Exception:
                        pass

                excluded = _plumbing_position_ids()
                raw_deals = mt5.history_deals_get(session_start, query_end) or []
                filtered: List[object] = []
                for deal in raw_deals:
                    if getattr(deal, "symbol", "") != config.MT5_SYMBOL:
                        continue
                    if int(getattr(deal, "magic", -1) or -1) != 234000:
                        continue
                    pos_id = _positive_id(getattr(deal, "position_id", None))
                    if pos_id and pos_id in excluded:
                        continue
                    deal_ts = _deal_timestamp(deal)
                    if deal_ts is not None:
                        deal_ts = resolve_mt5_deal_epoch_utc(
                            deal_ts, now_ep, tick=live_tick, config_mod=config
                        )
                        if deal_ts < start_ep or deal_ts > now_ep:
                            continue
                    filtered.append(deal)
                self._last_session_deals = (start_ep, now_ep, filtered)
                return filtered
            except Exception as exc:
                logger.warning("Risk manager could not read MT5 history: %s", exc)
                return None

    def daily_pnl(self, now: Optional[datetime] = None) -> float:
        """Current risk-session realised bot PnL in USD (`18:00 ET` CME session default).

        Uses position-specific MT5 history first (`tracked_bot_positions.json`),
        date-range `magic == 234000` history second, and `trade_memory.json`
        offline session records when MT5 is unavailable.
        """
        u_now = _ensure_utc(now or _now())
        session_start, _, _ = session_bounds_utc(u_now)
        deals = self._query_mt5_session_deals(session_start, u_now)
        if deals is not None:
            return sum(_deal_value(d) for d in deals)
        mem_usd, _, _ = _trade_memory_session_totals(
            session_start.timestamp(), u_now.timestamp(), data_dir=self._target_data_dir()
        )
        return mem_usd

    def daily_points_pnl(self, now: Optional[datetime] = None) -> float:
        """Current risk-session realised bot PnL in Gold points (`Stage 4R` alignment)."""
        u_now = _ensure_utc(now or _now())
        session_start, _, _ = session_bounds_utc(u_now)
        _, mem_pts, covered_pos = _trade_memory_session_totals(
            session_start.timestamp(), u_now.timestamp(), data_dir=self._target_data_dir()
        )
        deals = self._query_mt5_session_deals(session_start, u_now)
        if not deals:
            return mem_pts

        contract_sz = max(1.0, float(getattr(config, "CONTRACT_SIZE", 100.0) or 100.0))
        by_pos: Dict[object, List[object]] = {}
        for idx, d in enumerate(deals):
            pid = _positive_id(getattr(d, "position_id", None))
            p_key: object = str(pid) if pid is not None else f"_deal_{getattr(d, 'ticket', idx)}"
            if str(p_key) in covered_pos:
                continue
            by_pos.setdefault(p_key, []).append(d)

        mt5_pts = 0.0
        for p_deals in by_pos.values():
            has_close = any(
                getattr(d, "entry", None) in (1, 2, 3)
                or (getattr(d, "entry", None) is None and float(getattr(d, "profit", 0.0) or 0.0) != 0.0)
                or float(getattr(d, "profit", 0.0) or 0.0) != 0.0
                for d in p_deals
            )
            if not has_close:
                continue
            for d in p_deals:
                vol = float(getattr(d, "volume", 0.0) or 0.0)
                if vol > 0.0:
                    mt5_pts += _deal_value(d) / (vol * contract_sz)
        return round(mem_pts + mt5_pts, 4)

    def _open_bot_floating(self) -> Tuple[float, float]:
        """Return `(floating_usd, floating_pts)` across open bot MT5 positions (`magic == 234000`)."""
        if mt5 is None or not callable(getattr(mt5, "positions_get", None)):
            return 0.0, 0.0
        try:
            with self._mt5_session() as ready:
                if not ready:
                    return 0.0, 0.0
                positions = mt5.positions_get(symbol=config.MT5_SYMBOL)
                if positions is None:
                    positions = mt5.positions_get() or []
                excluded = _plumbing_position_ids()
                contract_sz = max(1.0, float(getattr(config, "CONTRACT_SIZE", 100.0) or 100.0))
                f_usd = 0.0
                f_pts = 0.0
                for p in positions or []:
                    if getattr(p, "symbol", config.MT5_SYMBOL) != config.MT5_SYMBOL:
                        continue
                    if int(getattr(p, "magic", -1) or -1) != 234000:
                        continue
                    tid = _positive_id(getattr(p, "ticket", getattr(p, "identifier", None)))
                    if tid and tid in excluded:
                        continue
                    val_usd = sum(
                        float(getattr(p, k, 0.0) or 0.0)
                        for k in ("profit", "swap", "commission", "fee")
                    )
                    f_usd += val_usd
                    px_open = float(getattr(p, "price_open", 0.0) or 0.0)
                    px_cur = float(getattr(p, "price_current", 0.0) or 0.0)
                    p_type = int(getattr(p, "type", 0) or 0)
                    vol = float(getattr(p, "volume", 0.0) or 0.0)
                    if px_open > 0.0 and px_cur > 0.0:
                        f_pts += (px_cur - px_open) if p_type == 0 else (px_open - px_cur)
                    elif vol > 0.0:
                        f_pts += val_usd / (vol * contract_sz)
                return round(f_usd, 4), round(f_pts, 4)
        except Exception:
            return 0.0, 0.0

    # -- main check ------------------------------------------------------------
    def check(
        self,
        equity: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """Return `(trading_allowed, reason)`."""
        u_now = _ensure_utc(now or _now())
        self._last_session_deals = None

        # 1a) Latching max-drawdown / manual kill-switch (never auto-expires at rollover)
        state = self.load_state()
        raw_until = state.get("halt_until")
        if state.get("kill_switch") or str(raw_until or "").strip().upper() == "MANUAL_RESET":
            return False, str(state.get("reason") or "max drawdown halt active (manual reset required)")

        # 1b) Timed session halt (daily `%` equity or Stage-4R points cap)
        if raw_until:
            try:
                until = _ensure_utc(datetime.fromisoformat(str(raw_until)))
            except (TypeError, ValueError):
                return False, str(state.get("reason") or "unparseable risk halt active (manual reset required)")
            if u_now < until:
                return False, str(state.get("reason") or "risk halt active")
            state = self._expire_transient_halt(state)

        with self._mt5_session():
            eff_equity = float(equity) if equity is not None else self.account_equity()

            # 2) High-water-mark max drawdown (latches until manual `reset()`)
            has_saved_peak = "peak_equity" in state and state.get("peak_equity") is not None
            peak = float(state["peak_equity"]) if has_saved_peak else eff_equity
            if eff_equity > peak or (not has_saved_peak and eff_equity > 0.0):
                peak = max(peak, eff_equity)
                state["peak_equity"] = peak
                self.save_state(state)

            if self.max_drawdown_pct > 0.0 and peak > 0.0:
                dd_pct = (peak - eff_equity) / peak * 100.0
                if dd_pct >= self.max_drawdown_pct:
                    reason = f"max drawdown {dd_pct:.1f}% >= {self.max_drawdown_pct:.1f}%"
                    self.save_state({
                        "halted": True,
                        "kill_switch": True,
                        "halt_type": "max_drawdown",
                        "halt_until": "MANUAL_RESET",
                        "reason": reason,
                        "peak_equity": peak,
                    })
                    return False, reason

            # 3) Intraday session loss limits (`18:00 ET` CME Globex roll default)
            _, session_end, session_key = session_bounds_utc(u_now)
            inc_float = bool(getattr(config, "RISK_INCLUDE_FLOATING_LOSS", True))
            float_usd, float_pts = self._open_bot_floating() if inc_float else (0.0, 0.0)

            # 3a) Account `%`-equity daily loss limit
            if self.daily_loss_pct > 0.0 and eff_equity > 0.0:
                realised_usd = self.daily_pnl(u_now)
                eff_usd = realised_usd + min(0.0, float_usd)
                limit_usd = self.daily_loss_pct / 100.0 * eff_equity
                if eff_usd <= -limit_usd:
                    self.save_state({
                        "halt_type": "daily_loss",
                        "session_key": session_key,
                        "halt_until": session_end.isoformat(),
                        "reason": f"daily loss {eff_usd:.2f} <= -{limit_usd:.2f}",
                        "peak_equity": peak,
                    })
                    return False, f"daily loss {eff_usd:.2f} hits limit -{limit_usd:.2f}"

            # 3b) Stage-4R Gold-points daily loss circuit breaker (`-12.0 pts` default)
            if self.max_daily_loss_pts > 0.0:
                realised_pts = self.daily_points_pnl(u_now)
                eff_pts = realised_pts + min(0.0, float_pts)
                if eff_pts <= -self.max_daily_loss_pts:
                    self.save_state({
                        "halt_type": "stage4r_daily_pts",
                        "session_key": session_key,
                        "halt_until": session_end.isoformat(),
                        "reason": f"stage4r daily loss {eff_pts:+.2f} pts <= -{self.max_daily_loss_pts:.2f} pts",
                        "peak_equity": peak,
                    })
                    return (
                        False,
                        f"stage4r daily loss {eff_pts:+.2f} pts hits limit -{self.max_daily_loss_pts:.2f} pts",
                    )

        return True, "ok"

    def reset(self) -> None:
        """Manually clear a halt (for operator override)."""
        self.clear_state()
        logger.info("Risk halt cleared.")
