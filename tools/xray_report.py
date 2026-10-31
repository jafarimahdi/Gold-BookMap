#!/usr/bin/env python3
"""tools/xray_report.py -- Desk 11: THE X-RAY (Full-App Nightly Diagnostic & Vault).

Produces ONE universal plain-text file (`data/xray_<DATE>.txt`, plus structured
`data/xray_<DATE>.json`) that combines:
  - PART 1: Executive X-Ray Synthesis (Step 1 -> Step 5 funnel, exact Shooting/Crowd
            veto sub-reasons, Step 3 AI model/async/key-pool breakdown, Step 4
            COMEX-MT5 basis/spread/market-quality/Escort/PositionManager/TCA audit,
            Signal Health + Wall Map + Flow/Book Spies, cross-organ state-drift
            hygiene check, live loop log error/warning digest, 17-test & 10-desk
            scorecard, sanitized run_config, and 30+ artifact manifest).
  - PART 2: Machine-parsable Plain-Text Artifact Vault embedding all non-HTML
            evening reports, probes, CSVs, sanitized state JSONs, and logs into
            the SAME `.txt` file so sharing the entire night in chat requires
            dragging ONE `.txt` file (no `.zip` required).

Read-only contract:
  - Never imports robot trading/execution runtime modules or MetaTrader5.
  - Never reads `.env` and scrubs secret patterns across all text/JSON blocks.
  - Writes only `data/xray_<DATE>.txt` and `data/xray_<DATE>.json` (unless `--unpack`
    is explicitly invoked).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import statistics
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

VAULT_INDEX_BANNER = "===== XRAY_VAULT_INDEX ====="
ARTIFACT_BEGIN_PREFIX = "===== XRAY_ARTIFACT_BEGIN "
ARTIFACT_END_PREFIX = "===== XRAY_ARTIFACT_END "

# Secret scrubbing patterns (defense in depth; .env is never read).
_SECRET_PATTERNS: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"AIza[0-9A-Za-z\-_]{16,}"), "AIza[REDACTED]"),
    (re.compile(r"sk-[0-9A-Za-z\-_]{16,}"), "sk-[REDACTED]"),
    (re.compile(r"(Bearer\s+)[0-9A-Za-z\-_\.]{16,}", re.I), r"\1[REDACTED]"),
    (
        re.compile(
            r"((?:GEMINI_API_KEYS?|OPENAI_API_KEY|ANTHROPIC_API_KEY|MT5_PASSWORD|"
            r"TELEGRAM_BOT_TOKEN|API_KEY|APIKEY|SECRET_KEY|PASSWORD|PASSWD|WEBHOOK_URL)"
            r"\s*[=:]\s*)([^\s\"',;}\]]+)",
            re.I,
        ),
        r"\1[REDACTED]",
    ),
]

_SENSITIVE_KEY_SUBSTRINGS = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "passwd",
    "private_key",
    "bot_token",
    "access_token",
    "refresh_token",
    "auth_token",
    "webhook",
    "credential",
)

_SAFE_KEY_EXCEPTIONS = {
    "max_tokens",
    "thinking_budget_tokens",
    "token_limit",
    "tokens",
}


def redact_text(text: str) -> str:
    if not text:
        return ""
    out = text
    for pat, repl in _SECRET_PATTERNS:
        out = pat.sub(repl, out)
    return out


def _is_sensitive_key(key: str) -> bool:
    kl = str(key).strip().lower()
    if kl in _SAFE_KEY_EXCEPTIONS or kl.endswith("_tokens"):
        return False
    return any(s in kl for s in _SENSITIVE_KEY_SUBSTRINGS)


def sanitize_json_obj(obj: Any, parent_key: str = "") -> Any:
    if isinstance(obj, dict):
        cleaned: Dict[str, Any] = {}
        for k, v in obj.items():
            ks = str(k)
            if _is_sensitive_key(ks):
                cleaned[ks] = "[REDACTED]"
            elif parent_key == "cooldowns":
                # gemini_keys_state.json stores SHA-256 prefixes; anonymize further to short slot tag.
                short_hash = hashlib.sha256(ks.encode("utf-8")).hexdigest()[:8]
                cleaned[f"key_{short_hash}"] = sanitize_json_obj(v, ks)
            else:
                cleaned[ks] = sanitize_json_obj(v, ks)
        return cleaned
    if isinstance(obj, list):
        return [sanitize_json_obj(x, parent_key) for x in obj]
    if isinstance(obj, str):
        return redact_text(obj)
    return obj


def _read_text(path: Path) -> str:
    try:
        if path.exists() and path.is_file():
            return redact_text(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        pass
    return ""


def _read_json(path: Path) -> Any:
    try:
        if path.exists() and path.is_file():
            raw = path.read_text(encoding="utf-8", errors="replace")
            return sanitize_json_obj(json.loads(raw))
    except Exception:
        pass
    return None


def _safe_float(val: Any, default: Optional[float] = None) -> Optional[float]:
    if val is None or val == "":
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val: Any, default: int = 0) -> int:
    if val is None or val == "":
        return default
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def _pct(num: int, den: int) -> str:
    if den <= 0:
        return "0.0%"
    return f"{(100.0 * num / den):.1f}%"


def _git_receipt(root: Path) -> Dict[str, str]:
    info = {"commit": "unknown", "branch": "unknown"}
    try:
        p1 = subprocess.run(
            ["git", "rev-parse", "--short=10", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=3,
        )
        if p1.returncode == 0 and p1.stdout.strip():
            info["commit"] = p1.stdout.strip()
        p2 = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=3,
        )
        if p2.returncode == 0 and p2.stdout.strip():
            info["branch"] = p2.stdout.strip()
    except Exception:
        pass
    return info


def _normalize_veto_reason(text: str) -> str:
    s = str(text or "").strip()
    if not s:
        return "(empty)"
    # Normalize volatile per-cycle floats/timestamps/sample counters so identical gate rules group cleanly.
    s = re.sub(r"\(\d+/(\d+)\s+samples\)", r"(<k>/\1 samples)", s)
    s = re.sub(r"\b\d+\.\d+s\b", "<N>s", s)
    s = re.sub(r"@[ \t]*\d+(?:\.\d+)?", "@<PX>", s)
    s = re.sub(r"\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^\s]*", "<TS>", s)
    if "Crowd v2" in s or "crowd v2" in s or "Q1" in s or "Q2" in s or "Q3" in s:
        s = re.sub(r"[-+]?\d+\.\d+", "<N>", s)
    elif "road history" in s.lower() or "stale" in s.lower() or "spread" in s.lower():
        s = re.sub(r"[-+]?\d+\.\d+", "<N>", s)
    return s[:130]


def _analyze_decisions_csv(csv_path: Path, conf_threshold: float) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "exists": csv_path.exists(),
        "rows": 0,
        "first_ts": "",
        "last_ts": "",
        "symbols": [],
        "funnel": {},
        "power_directions": {},
        "power_reasons": [],
        "signal_directions": {},
        "ai_models": [],
        "ai_actions": {},
        "shooting_statuses": {},
        "shooting_reasons_top": [],
        "shooting_subchecks_top": [],
        "shooting_directional_subchecks_top": [],
        "paper_entry_statuses": {},
        "exec_statuses": {},
        "top_block_reasons": [],
        "a2_fires_in_csv": 0,
        "async_pending_count": 0,
        "cached_ai_count": 0,
        "live_gemini_completions": 0,
        "call_failed_count": 0,
        "rate_limited_count": 0,
        "timeout_budget_count": 0,
        "throttled_count": 0,
        "power_carry_count": 0,
        "executed_count": 0,
    }
    if not csv_path.exists():
        return out

    try:
        with open(csv_path, encoding="utf-8", errors="replace", newline="") as fh:
            rows = list(csv.DictReader(fh))
    except Exception:
        return out

    n = len(rows)
    out["rows"] = n
    if n == 0:
        return out

    out["first_ts"] = str(rows[0].get("timestamp") or "")
    out["last_ts"] = str(rows[-1].get("timestamp") or "")

    symbols = Counter()
    pwr_dir = Counter()
    pwr_reason = Counter()
    sig_dir = Counter()
    ai_act = Counter()
    model_src = Counter()
    model_act: Dict[Tuple[str, str], Counter] = {}
    model_conf_sum: Counter = Counter()
    shoot_status = Counter()
    shoot_reason = Counter()
    shoot_sub = Counter()
    shoot_dir_sub = Counter()
    paper_status = Counter()
    exec_status = Counter()
    block_reason = Counter()

    market_open_n = 0
    buda_open_n = 0
    news_quiet_n = 0
    pregate_ok_n = 0
    pwr_directional_n = 0
    sig_directional_n = 0
    sig_above_conf_n = 0
    ai_reached_n = 0
    ai_buy_sell_n = 0
    ai_above_conf_n = 0
    a2_fires_n = 0
    shoot_go_n = 0
    shoot_go_episodes_n = 0
    last_go_epoch: Optional[float] = None
    exec_authorized_n = 0
    executed_n = 0

    for r in rows:
        ts = str(r.get("timestamp") or "").strip()
        sym = str(r.get("symbol") or "").strip()
        if sym:
            symbols[sym] += 1

        sess = str(r.get("session_state") or "")
        if "market_open=yes" in sess or ("CLOSED" not in sess.upper() and sess != ""):
            market_open_n += 1
        if "budapest_trading=yes" in sess:
            buda_open_n += 1

        news_st = str(r.get("news_state") or "QUIET").strip().upper()
        if news_st not in ("BLOCK", "BLOCKED", "HALT"):
            news_quiet_n += 1

        ai_mod = str(r.get("ai_model") or "").strip() or "(blank)"
        dec_src = str(r.get("decision_source") or "").strip() or "(blank)"
        act = str(r.get("ai_action") or "").strip().upper() or "(blank)"
        ai_conf = _safe_float(r.get("ai_confidence"), 0.0) or 0.0
        sig_d = str(r.get("signal_direction") or "").strip().upper() or "(blank)"
        sig_conf = _safe_float(r.get("signal_confidence"), 0.0) or 0.0
        pd = str(r.get("power_direction") or "").strip().upper() or "(blank)"
        prc = str(r.get("power_reason_code") or "").strip() or "(blank)"
        sh_st = str(r.get("shooting_status") or "").strip().upper() or "(blank)"
        sh_rsn = str(r.get("shooting_reason") or "").strip()
        pe_st = str(r.get("paper_entry_status") or "").strip() or "(blank)"
        ex_st = str(r.get("exec_status") or "").strip().upper() or "(blank)"
        rsn = str(r.get("reason") or "").strip()
        rat = str(r.get("ai_rationale") or "").strip()

        if ai_mod not in ("none-safety-gate", "none-execution-feed-gate", "none-rl-pause"):
            pregate_ok_n += 1

        pwr_dir[pd] += 1
        pwr_reason[f"{pd}:{prc}"] += 1
        if pd in ("UP", "DOWN", "BUY", "SELL"):
            pwr_directional_n += 1

        sig_dir[sig_d] += 1
        if sig_d in ("BUY", "SELL"):
            sig_directional_n += 1
            if sig_conf >= conf_threshold:
                sig_above_conf_n += 1

        ai_act[act] += 1
        key = (dec_src, ai_mod)
        model_src[key] += 1
        model_act.setdefault(key, Counter())[act] += 1
        model_conf_sum[key] += ai_conf

        mod_low = ai_mod.lower()
        if mod_low == "none-async-pending":
            out["async_pending_count"] += 1
        elif mod_low.endswith("-cached"):
            out["cached_ai_count"] += 1
            ai_reached_n += 1
        elif "power carry" in mod_low:
            out["power_carry_count"] += 1
            ai_reached_n += 1
        elif mod_low == "none-call-failed":
            out["call_failed_count"] += 1
        elif mod_low == "none-rate-limited":
            out["rate_limited_count"] += 1
        elif mod_low == "none-timeout-budget":
            out["timeout_budget_count"] += 1
        elif mod_low == "none-throttled":
            out["throttled_count"] += 1
        elif not mod_low.startswith("none") and not mod_low.startswith("fallback") and ai_mod != "(blank)":
            out["live_gemini_completions"] += 1
            ai_reached_n += 1

        if act in ("BUY", "SELL"):
            ai_buy_sell_n += 1
            if ai_conf >= conf_threshold:
                ai_above_conf_n += 1

        if (
            "[A2]" in rat
            or "[A2]" in rsn
            or "A2 burst" in rat
            or "A2 burst" in rsn
            or "A2 burst" in sh_rsn
            or dec_src.upper() == "A2"
        ):
            a2_fires_n += 1

        shoot_status[sh_st] += 1
        if sh_st == "GO":
            shoot_go_n += 1
            row_ep: Optional[float] = None
            if ts:
                try:
                    row_ep = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except ValueError:
                    row_ep = None
            # Step 2 serves one M3 bar plan across ~3 consecutive 60s cycles;
            # group served GO cycles within 190s into one distinct GO episode.
            if last_go_epoch is None or row_ep is None or (row_ep - last_go_epoch) > 190.0:
                shoot_go_episodes_n += 1
            if row_ep is not None:
                last_go_epoch = row_ep
        else:
            last_go_epoch = None
            if sh_rsn:
                shoot_reason[_normalize_veto_reason(sh_rsn)] += 1

        raw_subs = str(r.get("shooting_sub_checks") or "").strip()
        if raw_subs and sh_st != "GO":
            parsed_subs: List[str] = []
            if raw_subs.startswith("["):
                try:
                    arr = json.loads(raw_subs)
                    if isinstance(arr, list):
                        parsed_subs = [str(x) for x in arr if str(x).strip()]
                except Exception:
                    parsed_subs = [raw_subs]
            else:
                parsed_subs = [raw_subs]
            for item in parsed_subs:
                norm_item = _normalize_veto_reason(item)
                shoot_sub[norm_item] += 1
                if pd in ("UP", "DOWN", "BUY", "SELL"):
                    shoot_dir_sub[norm_item] += 1

        paper_status[pe_st] += 1
        exec_status[ex_st] += 1
        if ex_st not in ("SKIPPED", "BLOCKED", "(BLANK)", "NONE", "HOLD"):
            exec_authorized_n += 1
        if ex_st in ("EXECUTED", "FILLED", "OPENED", "PLACED"):
            executed_n += 1
        if rsn and ex_st != "EXECUTED":
            block_reason[_normalize_veto_reason(rsn)] += 1

    out["symbols"] = [k for k, _ in symbols.most_common(5)]
    out["a2_fires_in_csv"] = a2_fires_n
    out["shooting_go_episodes"] = shoot_go_episodes_n
    out["executed_count"] = executed_n
    effective_a2_or_go_eps = a2_fires_n if a2_fires_n > 0 else shoot_go_episodes_n
    out["funnel"] = {
        "total_cycles": n,
        "market_open": market_open_n,
        "budapest_trading_window": buda_open_n,
        "news_not_blocked": news_quiet_n,
        "safety_and_feed_pregate_ok": pregate_ok_n,
        "power_v2_directional": pwr_directional_n,
        "panel_directional_buy_sell": sig_directional_n,
        "panel_ge_conf_threshold": sig_above_conf_n,
        "step3_live_or_cached_or_carry": ai_reached_n,
        "step3_action_buy_sell": ai_buy_sell_n,
        "step3_buy_sell_ge_conf_threshold": ai_above_conf_n,
        "a2_aggressive_fires": effective_a2_or_go_eps,
        "shooting_status_go": shoot_go_n,
        "mt5_executed_in_csv": executed_n,
    }
    out["power_directions"] = dict(pwr_dir.most_common())
    out["power_reasons"] = pwr_reason.most_common(10)
    out["signal_directions"] = dict(sig_dir.most_common())
    out["ai_actions"] = dict(ai_act.most_common())
    out["shooting_statuses"] = dict(shoot_status.most_common())
    out["shooting_reasons_top"] = shoot_reason.most_common(12)
    out["shooting_subchecks_top"] = shoot_sub.most_common(12)
    out["shooting_directional_subchecks_top"] = shoot_dir_sub.most_common(12)
    out["paper_entry_statuses"] = dict(paper_status.most_common())
    out["exec_statuses"] = dict(exec_status.most_common())
    out["top_block_reasons"] = block_reason.most_common(10)

    ai_table = []
    for (src, mod), cnt in model_src.most_common():
        acts = model_act[(src, mod)]
        avg_c = model_conf_sum[(src, mod)] / max(cnt, 1)
        ai_table.append(
            {
                "decision_source": src,
                "ai_model": mod,
                "count": cnt,
                "pct": round(100.0 * cnt / n, 1),
                "buy": acts.get("BUY", 0),
                "sell": acts.get("SELL", 0),
                "hold": acts.get("HOLD", 0),
                "avg_conf": round(avg_c, 1),
            }
        )
    out["ai_models"] = ai_table
    return out


def _analyze_diary_and_execution_organs(root: Path, day_str: str) -> Dict[str, Any]:
    """Read-only inspection of diary_<YYYYMMDD>.jsonl, market_quality_evidence.json,
    management_log.csv, pm_state.json, trade_outcomes.csv, trade_memory.json, and TCA files."""
    data_dir = root / "data"
    compact = day_str.replace("-", "")
    res: Dict[str, Any] = {
        "diary_path": "",
        "diary_records": 0,
        "escort_active_cycles": 0,
        "escort_actions": {},
        "entry_sim_statuses": {},
        "signal_map_walls_seen": 0,
        "spread_samples": 0,
        "spread_min": None,
        "spread_median": None,
        "spread_p95": None,
        "spread_max": None,
        "basis_samples": 0,
        "basis_min": None,
        "basis_median": None,
        "basis_max": None,
        "market_quality_latest": {},
        "pm_actions_today": 0,
        "pm_actions_breakdown": {},
        "trades_closed_today": 0,
        "trades_today_net_pnl": 0.0,
        "trades_rolling_total": 0,
        "trades_rolling_win_rate": None,
        "trades_rolling_net_pnl": 0.0,
        "tca_rows_today": 0,
        "tca_avg_slippage_pts": None,
        "rl_paused_regimes": [],
    }

    diary_candidates = [
        data_dir / f"diary_{compact}.jsonl",
        data_dir / f"diary_{compact}.json",
    ]
    diary_file = next((p for p in diary_candidates if p.exists()), None)
    spreads: List[float] = []
    bases: List[float] = []
    escort_acts = Counter()
    sim_sts = Counter()

    if diary_file is not None:
        res["diary_path"] = diary_file.name
        try:
            with open(diary_file, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    res["diary_records"] += 1
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    sp = _safe_float(rec.get("spread") or rec.get("cfd_spread"))
                    if sp is not None and 0.0 <= sp < 500.0:
                        spreads.append(sp)
                    bs = _safe_float(rec.get("basis") or rec.get("price_basis"))
                    if bs is not None and -500.0 < bs < 500.0:
                        bases.append(bs)
                    qe = rec.get("quality_evidence")
                    if isinstance(qe, dict):
                        qsp = _safe_float(qe.get("spread") or qe.get("trade_spread"))
                        if qsp is not None and 0.0 <= qsp < 500.0:
                            spreads.append(qsp)
                        qbs = _safe_float(qe.get("basis") or qe.get("comex_mt5_basis"))
                        if qbs is not None and -500.0 < qbs < 500.0:
                            bases.append(qbs)
                    smap = rec.get("signal_map")
                    if isinstance(smap, dict) and smap:
                        if smap.get("walls") or smap.get("above") or smap.get("below") or smap.get("nearest_above") or smap.get("nearest_below"):
                            res["signal_map_walls_seen"] += 1
                    esc = rec.get("escort")
                    if isinstance(esc, dict) and esc:
                        act = str(esc.get("action") or esc.get("verdict") or esc.get("status") or "").strip()
                        if act and act.upper() not in ("FLAT", "NONE", "SLEEP", "IDLE", "NO_POSITION"):
                            res["escort_active_cycles"] += 1
                            escort_acts[act] += 1
                    esim = rec.get("entry_simulation")
                    if isinstance(esim, dict) and esim:
                        est = str(esim.get("status") or "").strip()
                        if est:
                            sim_sts[est] += 1
        except OSError:
            pass

    if spreads:
        spreads_sorted = sorted(spreads)
        res["spread_samples"] = len(spreads_sorted)
        res["spread_min"] = round(spreads_sorted[0], 3)
        res["spread_median"] = round(statistics.median(spreads_sorted), 3)
        res["spread_p95"] = round(spreads_sorted[min(int(0.95 * len(spreads_sorted)), len(spreads_sorted) - 1)], 3)
        res["spread_max"] = round(spreads_sorted[-1], 3)

    if bases:
        bases_sorted = sorted(bases)
        res["basis_samples"] = len(bases_sorted)
        res["basis_min"] = round(bases_sorted[0], 3)
        res["basis_median"] = round(statistics.median(bases_sorted), 3)
        res["basis_max"] = round(bases_sorted[-1], 3)

    res["escort_actions"] = dict(escort_acts.most_common(8))
    res["entry_sim_statuses"] = dict(sim_sts.most_common(8))

    mqe = _read_json(data_dir / "market_quality_evidence.json")
    if isinstance(mqe, dict):
        res["market_quality_latest"] = {
            k: mqe.get(k)
            for k in (
                "captured_at",
                "safety_gate_status",
                "gate_reason",
                "quote_age_seconds",
                "depth_age_seconds",
                "book_crossed",
                "data_symbol",
                "trade_symbol",
            )
            if k in mqe
        }

    # Step 4a Position Manager log
    mgmt_csv = data_dir / "management_log.csv"
    if mgmt_csv.exists():
        pm_cnt = Counter()
        try:
            with open(mgmt_csv, encoding="utf-8", errors="replace", newline="") as fh:
                for row in csv.DictReader(fh):
                    ts = str(row.get("timestamp") or row.get("time") or "")
                    if ts.startswith(day_str) or ts.startswith(compact):
                        res["pm_actions_today"] += 1
                        act = str(row.get("action") or row.get("rule") or "action")
                        pm_cnt[act] += 1
            res["pm_actions_breakdown"] = dict(pm_cnt.most_common(8))
        except Exception:
            pass

    # Closed trades & L5 RL memory
    tm = _read_json(data_dir / "trade_memory.json")
    if isinstance(tm, dict):
        closed = tm.get("closed") or tm.get("trades") or []
        if isinstance(closed, list) and closed:
            res["trades_rolling_total"] = len(closed)
            wins = 0
            net_all = 0.0
            for c in closed:
                if not isinstance(c, dict):
                    continue
                raw_pnl = (
                    c.get("profit")
                    if "profit" in c
                    else (c.get("pnl") if "pnl" in c else c.get("pnl_usd_est"))
                )
                pnl = _safe_float(raw_pnl, 0.0) or 0.0
                net_all += pnl
                if pnl > 0:
                    wins += 1
                cts = str(c.get("closed_utc") or c.get("close_time") or c.get("timestamp") or "")
                if cts.startswith(day_str):
                    res["trades_closed_today"] += 1
                    res["trades_today_net_pnl"] = round(res["trades_today_net_pnl"] + pnl, 2)
            res["trades_rolling_win_rate"] = round(100.0 * wins / max(len(closed), 1), 1)
            res["trades_rolling_net_pnl"] = round(net_all, 2)

    outcomes_csv = data_dir / "trade_outcomes.csv"
    if outcomes_csv.exists() and res["trades_closed_today"] == 0:
        try:
            with open(outcomes_csv, encoding="utf-8", errors="replace", newline="") as fh:
                for row in csv.DictReader(fh):
                    ts = str(row.get("closed_at") or row.get("timestamp") or row.get("close_time") or "")
                    if ts.startswith(day_str):
                        res["trades_closed_today"] += 1
                        pnl = _safe_float(row.get("profit") or row.get("pnl"), 0.0) or 0.0
                        res["trades_today_net_pnl"] = round(res["trades_today_net_pnl"] + pnl, 2)
        except Exception:
            pass

    # TCA slippage log
    tca_csv = data_dir / "tca_log.csv"
    if tca_csv.exists():
        slips: List[float] = []
        try:
            with open(tca_csv, encoding="utf-8", errors="replace", newline="") as fh:
                for row in csv.DictReader(fh):
                    ts = str(row.get("timestamp") or row.get("time") or "")
                    if ts.startswith(day_str) or ts.startswith(compact):
                        res["tca_rows_today"] += 1
                        sl = _safe_float(row.get("slippage") or row.get("slippage_pts"))
                        if sl is not None:
                            slips.append(sl)
            if slips:
                res["tca_avg_slippage_pts"] = round(statistics.mean(slips), 3)
        except Exception:
            pass

    return res


def _analyze_state_hygiene(
    root: Path,
    day_str: str,
    dec_summary: Dict[str, Any],
    metrics: Dict[str, Any],
) -> Dict[str, Any]:
    """Cross-check persistent data/*.json state files against decisions CSV & MT5 audit truth."""
    data_dir = root / "data"
    drifts: List[str] = []
    states: Dict[str, Any] = {}

    mt5_orders = _safe_int(metrics.get("orders_to_mt5"), -1) if isinstance(metrics, dict) else -1
    csv_exec = _safe_int(dec_summary.get("executed_count"), 0)

    # 1. trade_guard_state.json
    tgs = _read_json(data_dir / "trade_guard_state.json")
    if isinstance(tgs, dict):
        states["trade_guard_state"] = tgs
        tg_date = str(tgs.get("date") or "")
        tg_trades = _safe_int(tgs.get("trades_today"), 0)
        if tg_date == day_str:
            ref_trades = mt5_orders if mt5_orders >= 0 else csv_exec
            if dec_summary.get("exists") and tg_trades != ref_trades:
                drifts.append(
                    f"trade_guard_state.json trades_today={tg_trades} on {tg_date} vs recorded executions={ref_trades}"
                )
        if tgs.get("halted"):
            drifts.append(f"trade_guard_state.json has halted=True (reason={tgs.get('halt_reason', '?')})")

    # 2. risk_state.json
    rs = _read_json(data_dir / "risk_state.json")
    if isinstance(rs, dict):
        states["risk_state"] = rs
        if rs.get("kill_switch") or rs.get("halted"):
            drifts.append("risk_state.json has kill_switch/halted active")

    # 3. ai_calls.json
    aic = _read_json(data_dir / "ai_calls.json")
    if isinstance(aic, dict):
        states["ai_calls"] = aic

    # 4. a2_burst_count.json
    a2s = _read_json(data_dir / "a2_burst_count.json")
    if isinstance(a2s, dict):
        states["a2_burst_count"] = a2s
        a2_date = str(a2s.get("date") or "")
        a2_cnt = _safe_int(a2s.get("count") if "count" in a2s else a2s.get("fires"), -1)
        if a2_date == day_str and a2_cnt >= 0 and dec_summary.get("exists"):
            csv_a2 = _safe_int(dec_summary.get("a2_fires_in_csv"), 0)
            go_eps = _safe_int(dec_summary.get("shooting_go_episodes"), 0)
            go_rows = _safe_int((dec_summary.get("funnel") or {}).get("shooting_status_go"), 0)
            # Step 2 commits A2 burst fires once per M3 Shooting=GO episode without
            # stamping literal '[A2]' into decisions_log.csv reason columns.
            ref_cnt = csv_a2 if csv_a2 > 0 else go_eps
            if abs(a2_cnt - ref_cnt) > 2 and not (go_eps <= a2_cnt <= go_rows):
                drifts.append(
                    f"a2_burst_count.json count={a2_cnt} on {a2_date} vs decisions GO episodes={go_eps} (rows={go_rows}, [A2]={csv_a2})"
                )

    # 5. gemini_keys_state.json (sanitized)
    gks = _read_json(data_dir / "gemini_keys_state.json")
    if isinstance(gks, dict):
        cooldowns = gks.get("cooldowns") if isinstance(gks.get("cooldowns"), dict) else {}
        states["gemini_keys_state"] = {
            "tracked_cooldown_slots": len(cooldowns),
            "cooldowns": cooldowns,
        }

    # 6. judge_report_card.json
    jrc = _read_json(data_dir / "judge_report_card.json")
    if isinstance(jrc, dict):
        states["judge_report_card"] = {
            "generated_at": jrc.get("generated_at") or jrc.get("updated_at") or jrc.get("date"),
            "days_used": jrc.get("days") or jrc.get("dates") or jrc.get("window_days"),
            "judges_count": len(jrc.get("judges") or {}) if isinstance(jrc.get("judges"), dict) else None,
        }

    # 7. power_v2_memory/power_<DATE>.jsonl (canonical) or legacy power_memory_book/power_<DATE>.json
    day_compact = day_str.replace("-", "")
    pmb_candidates = [
        data_dir / "power_v2_memory" / f"power_{day_str}.jsonl",
        data_dir / "power_v2_memory" / f"power_{day_compact}.jsonl",
        data_dir / "power_memory_book" / f"power_{day_str}.json",
        data_dir / f"power_{day_str}.json",
    ]
    pmb = next((c for c in pmb_candidates if c.exists()), None)
    if pmb is not None and pmb.suffix == ".jsonl":
        try:
            jl_lines = [ln for ln in pmb.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
            states["power_memory_book_day"] = {
                "file": f"{pmb.parent.name}/{pmb.name}",
                "present": True,
                "entries": len(jl_lines),
            }
        except OSError:
            states["power_memory_book_day"] = {"file": pmb.name, "present": False}
    elif pmb is not None:
        pjson = _read_json(pmb)
        states["power_memory_book_day"] = {
            "file": pmb.name,
            "present": True,
            "keys": sorted(list(pjson.keys()))[:10] if isinstance(pjson, dict) else [],
        }
    else:
        states["power_memory_book_day"] = {"file": f"power_v2_memory/power_{day_str}.jsonl", "present": False}

    # 8. power_history_probe_latest.csv
    php = data_dir / "power_history_probe_latest.csv"
    if php.exists():
        try:
            lines = php.read_text(encoding="utf-8", errors="replace").splitlines()
            states["power_history_probe_latest"] = {
                "present": True,
                "rows": max(0, len(lines) - 1),
                "last_line": redact_text(lines[-1][:140]) if len(lines) > 1 else "",
            }
        except OSError:
            pass

    # 9. tracked_bot_positions.json & demo smoke files
    tbp = _read_json(data_dir / "tracked_bot_positions.json")
    if tbp is not None:
        if isinstance(tbp, dict) and isinstance(tbp.get("position_ids"), list):
            open_n = len(tbp["position_ids"])
        elif isinstance(tbp, (list, dict)):
            open_n = len(tbp)
        else:
            open_n = 0
        states["tracked_bot_positions_count"] = open_n
    if (data_dir / "demo_order_test_pending.json").exists():
        drifts.append("demo_order_test_pending.json is present in data/ (unconsumed one-shot smoke request)")

    return {"drifts": drifts, "states": states}


def _normalize_log_sig(line: str) -> str:
    # Strip leading timestamp and normalize volatile numbers/IDs to group recurring log issues.
    s = re.sub(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\s*", "", line.strip())
    s = re.sub(r"\b\d+\.\d+\b", "<F>", s)
    s = re.sub(r"\b\d{4,}\b", "<ID>", s)
    return redact_text(s)[:150]


def _analyze_logs(root: Path, day_str: str) -> Dict[str, Any]:
    compact = day_str.replace("-", "")
    log_path = root / "logs" / f"trading_{compact}.log"
    watchdog_candidates = [
        root / "data" / "tape_watchdog.log",
        root / "logs" / "tape_watchdog.log",
    ]
    watchdog_path = next((p for p in watchdog_candidates if p.exists()), None)

    out: Dict[str, Any] = {
        "trading_log_exists": log_path.exists(),
        "trading_log_name": log_path.name,
        "trading_log_lines": 0,
        "critical_count": 0,
        "error_count": 0,
        "warning_count": 0,
        "traceback_count": 0,
        "basis_mentions": 0,
        "last_basis_lines": [],
        "top_errors": [],
        "top_warnings": [],
        "watchdog_exists": watchdog_path is not None,
        "watchdog_day_lines": [],
    }

    if log_path.exists():
        err_counter: Counter = Counter()
        warn_counter: Counter = Counter()
        basis_lines: List[str] = []
        try:
            with open(log_path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    out["trading_log_lines"] += 1
                    ul = line.upper()
                    if "TRACEBACK (MOST RECENT CALL LAST)" in ul:
                        out["traceback_count"] += 1
                    if (
                        " - CRITICAL - " in ul
                        or "[CRITICAL]" in ul
                        or re.search(r"\bCRITICAL\s+\[", ul)
                    ):
                        out["critical_count"] += 1
                        err_counter[_normalize_log_sig(line)] += 1
                    elif (
                        " - ERROR - " in ul
                        or "[ERROR]" in ul
                        or re.search(r"\bERROR\s+\[", ul)
                    ):
                        out["error_count"] += 1
                        err_counter[_normalize_log_sig(line)] += 1
                    elif (
                        " - WARNING - " in ul
                        or "[WARN" in ul
                        or re.search(r"\bWARNING\s+\[", ul)
                    ):
                        out["warning_count"] += 1
                        warn_counter[_normalize_log_sig(line)] += 1
                    if "BASIS" in ul:
                        out["basis_mentions"] += 1
                        if len(basis_lines) < 5:
                            basis_lines.append(redact_text(line.strip())[:160])
        except OSError:
            pass
        out["top_errors"] = err_counter.most_common(10)
        out["top_warnings"] = warn_counter.most_common(12)
        out["last_basis_lines"] = basis_lines

    if watchdog_path is not None:
        try:
            wlines = [
                redact_text(ln.strip())
                for ln in watchdog_path.read_text(encoding="utf-8", errors="replace").splitlines()
                if ln.startswith(day_str)
            ]
            out["watchdog_day_lines"] = wlines[-20:]
        except OSError:
            pass

    return out


def _extract_audit_issues(day_audit_txt: str) -> List[str]:
    issues: List[str] = []
    for ln in day_audit_txt.splitlines():
        s = ln.strip()
        if any(tag in s for tag in ("[FAIL]", "[WARN]", "FAIL ", "WARN ")) and (
            "TEST " in s or s.startswith("[FAIL]") or s.startswith("[WARN]")
        ):
            issues.append(s[:150])
    return issues[:20]


def _analyze_judges_csv(judge_csv_path: Path) -> Dict[str, Any]:
    if not judge_csv_path.exists():
        return {"exists": False, "count": 0, "top": [], "bottom": []}
    rows = []
    try:
        with open(judge_csv_path, encoding="utf-8", errors="replace", newline="") as fh:
            for r in csv.DictReader(fh):
                name = str(r.get("judge") or r.get("name") or "").strip()
                votes = _safe_int(r.get("votes") or r.get("n") or r.get("calls"), 0)
                net = _safe_float(
                    r.get("points_after_spread")
                    if r.get("points_after_spread") not in (None, "")
                    else (r.get("net_pts") or r.get("net") or r.get("total_pts")),
                    0.0,
                ) or 0.0
                acc = _safe_float(r.get("right_pct") or r.get("win_rate") or r.get("acc_pct"), None)
                wt = _safe_float(r.get("weight_now") if r.get("weight_now") not in (None, "") else r.get("weight"), None)
                if name:
                    rows.append(
                        {
                            "judge": name,
                            "votes": votes,
                            "net_pts": round(net, 2),
                            "right_pct": round(acc, 1) if acc is not None else None,
                            "weight": wt,
                        }
                    )
    except Exception:
        return {"exists": True, "count": 0, "top": [], "bottom": []}

    _retired_set: set = set()
    try:
        import config as _cfg
        _retired_set = set(getattr(_cfg, "RETIRED_JUDGES", None) or ())
    except Exception:
        _retired_set = set()

    active = [x for x in rows if x["votes"] > 0 and x["judge"] not in _retired_set]
    retired_bench = sorted(
        [x for x in rows if x["votes"] > 0 and x["judge"] in _retired_set],
        key=lambda x: x["net_pts"],
        reverse=True,
    )
    active_sorted = sorted(active, key=lambda x: x["net_pts"], reverse=True)
    return {
        "exists": True,
        "count": len(rows),
        "active_count": len(active),
        "retired_count": len(retired_bench),
        "silent_count": len(rows) - len(active) - len(retired_bench),
        "top": active_sorted[:5],
        "bottom": list(reversed(active_sorted[-5:])) if active_sorted else [],
        "retired_bench": retired_bench,
    }


def _collect_artifact_specs(root: Path, day_str: str) -> List[Tuple[str, Path, str, int]]:
    """Ordered allowlist of (label_name, path, fmt, max_lines_cap) for the X-Ray Vault.
    0 max_lines_cap means embed complete file verbatim.
    HTML files and raw multi-hundred-MB tick/MBO/diary lakes are inventoried in the
    manifest table, while all text/JSON/CSV diagnostic files are embedded in full.
    """
    d = root / "data"
    compact = day_str.replace("-", "")
    day_audit_path = d / f"day_audit_{day_str}.txt"
    if not day_audit_path.exists() and (root / f"day_report_{day_str}.txt").exists():
        day_audit_path = root / f"day_report_{day_str}.txt"
    pmb_path = d / "power_v2_memory" / f"power_{day_str}.jsonl"
    if not pmb_path.exists() and (d / "power_v2_memory" / f"power_{compact}.jsonl").exists():
        pmb_path = d / "power_v2_memory" / f"power_{compact}.jsonl"
    elif not pmb_path.exists() and (d / f"power_{day_str}.jsonl").exists():
        pmb_path = d / f"power_{day_str}.jsonl"

    return [
        (f"evening_test_gate_{day_str}.txt", d / f"evening_test_gate_{day_str}.txt", "txt", 0),
        (f"evening_test_gate_{day_str}.json", d / f"evening_test_gate_{day_str}.json", "json", 0),
        (f"preflight_{day_str}.txt", d / f"preflight_{day_str}.txt", "txt", 0),
        (f"run_config_{compact}.json", d / f"run_config_{compact}.json", "json", 0),
        (f"day_metrics_{day_str}.json", d / f"day_metrics_{day_str}.json", "json", 0),
        (f"day_audit_{day_str}.txt", day_audit_path, "txt", 0),
        (f"judge_panel_{day_str}.csv", d / f"judge_panel_{day_str}.csv", "csv", 0),
        (f"tail_probe_{day_str}.txt", d / f"tail_probe_{day_str}.txt", "txt", 0),
        (f"flow_probe_{day_str}.txt", d / f"flow_probe_{day_str}.txt", "txt", 0),
        (f"book_probe_{day_str}.txt", d / f"book_probe_{day_str}.txt", "txt", 0),
        (f"signal_health_{day_str}.txt", d / f"signal_health_{day_str}.txt", "txt", 0),
        (f"signal_map_{day_str}.txt", d / f"signal_map_{day_str}.txt", "txt", 0),
        (f"decision_ledger_{day_str}.txt", d / f"decision_ledger_{day_str}.txt", "txt", 0),
        (f"decision_ledger_{day_str}.csv", d / f"decision_ledger_{day_str}.csv", "csv", 0),
        (f"shadow_replay_{day_str}.txt", d / f"shadow_replay_{day_str}.txt", "txt", 0),
        (f"shadow_replay_{day_str}.csv", d / f"shadow_replay_{day_str}.csv", "csv", 0),
        (f"power_report_card_{day_str}.txt", d / f"power_report_card_{day_str}.txt", "txt", 0),
        (f"power_{day_str}.jsonl", pmb_path, "jsonl", 2500),
        (f"council_shadow_{day_str}.txt", d / f"council_shadow_{day_str}.txt", "txt", 0),
        (f"tf_study_{day_str}.txt", d / f"tf_study_{day_str}.txt", "txt", 0),
        (f"tf_lens_{day_str}.txt", d / f"tf_lens_{day_str}.txt", "txt", 0),
        (f"fish_anatomy_{day_str}.txt", d / f"fish_anatomy_{day_str}.txt", "txt", 0),
        (f"tf_robot_{day_str}.txt", d / f"tf_robot_{day_str}.txt", "txt", 0),
        ("clocks_verdict.txt", d / "clocks_verdict.txt", "txt", 0),
        (f"history_summary_{day_str}.txt", d / f"history_summary_{day_str}.txt", "txt", 0),
        (f"evening_paper_{day_str}.txt", d / f"evening_paper_{day_str}.txt", "txt", 0),
        (f"evening_suite_{day_str}.log", d / f"evening_suite_{day_str}.log", "log", 1800),
        (f"prices_{compact}.json", d / f"prices_{compact}.json", "json", 0),
        (f"decisions_{compact}.csv", d / f"decisions_{compact}.csv", "csv", 2500),
    ]


def _html_and_lake_inventory(root: Path, day_str: str) -> List[Tuple[str, Path]]:
    d = root / "data"
    compact = day_str.replace("-", "")
    return [
        (f"report_{day_str}.html", d / f"report_{day_str}.html"),
        (f"day_report_{day_str}.html", d / f"day_report_{day_str}.html"),
        (f"decisions_{day_str}.html", d / f"decisions_{day_str}.html"),
        (f"judges_{day_str}.html", d / f"judges_{day_str}.html"),
        (f"scoreboard_{day_str}.html", d / f"scoreboard_{day_str}.html"),
        (f"trades_{day_str}.html", d / f"trades_{day_str}.html"),
        (f"diary_{compact}.jsonl", d / f"diary_{compact}.jsonl"),
        (f"logs/trading_{compact}.log", root / "logs" / f"trading_{compact}.log"),
    ]


def build_xray(root: Path, day_str: str, include_vault: bool = True) -> Tuple[str, Dict[str, Any]]:
    """Build (full_xray_text, xray_summary_dict) for `day_str` (`YYYY-MM-DD`)."""
    root = root.resolve()
    data_dir = root / "data"
    compact = day_str.replace("-", "")

    git_info = _git_receipt(root)
    run_cfg = _read_json(data_dir / f"run_config_{compact}.json")
    if not isinstance(run_cfg, dict):
        run_cfg = {}

    conf_threshold = _safe_float(run_cfg.get("CONFIDENCE_THRESHOLD"), 35.0) or 35.0
    metrics = _read_json(data_dir / f"day_metrics_{day_str}.json")
    if not isinstance(metrics, dict):
        metrics = {}

    gate_json = _read_json(data_dir / f"evening_test_gate_{day_str}.json")
    if not isinstance(gate_json, dict):
        gate_json = {}

    day_audit_txt = _read_text(data_dir / f"day_audit_{day_str}.txt")
    if not day_audit_txt:
        day_audit_txt = _read_text(root / f"day_report_{day_str}.txt")
    preflight_txt = _read_text(data_dir / f"preflight_{day_str}.txt")
    tail_txt = _read_text(data_dir / f"tail_probe_{day_str}.txt")
    flow_txt = _read_text(data_dir / f"flow_probe_{day_str}.txt")
    book_txt = _read_text(data_dir / f"book_probe_{day_str}.txt")
    sig_health_txt = _read_text(data_dir / f"signal_health_{day_str}.txt")
    sig_map_txt = _read_text(data_dir / f"signal_map_{day_str}.txt")
    paper_txt = _read_text(data_dir / f"evening_paper_{day_str}.txt")
    shadow_txt = _read_text(data_dir / f"shadow_replay_{day_str}.txt")
    power_card_txt = _read_text(data_dir / f"power_report_card_{day_str}.txt")
    council_txt = _read_text(data_dir / f"council_shadow_{day_str}.txt")
    clocks_txt = _read_text(data_dir / "clocks_verdict.txt")

    dec_summary = _analyze_decisions_csv(data_dir / f"decisions_{compact}.csv", conf_threshold)
    organ_summary = _analyze_diary_and_execution_organs(root, day_str)
    hygiene = _analyze_state_hygiene(root, day_str, dec_summary, metrics)
    log_summary = _analyze_logs(root, day_str)
    judge_summary = _analyze_judges_csv(data_dir / f"judge_panel_{day_str}.csv")
    audit_issues = _extract_audit_issues(day_audit_txt)

    # High-signal attention items (synthesized automatically from evidence)
    attention: List[str] = []
    checks = gate_json.get("checks") if isinstance(gate_json.get("checks"), list) else []
    failed_gates = [str(c.get("name", "?")) for c in checks if str(c.get("status", "")).upper() == "FAIL"]
    if failed_gates:
        attention.append(f"Stage 1 Test Gate FAILED: {', '.join(failed_gates)}")

    counts = metrics.get("counts") if isinstance(metrics.get("counts"), dict) else {}
    fail_17 = _safe_int(counts.get("FAIL"), 0)
    warn_17 = _safe_int(counts.get("WARN"), 0)
    if fail_17 > 0:
        attention.append(f"17-Test Audit reported {fail_17} FAIL check(s)")
    if warn_17 > 0:
        attention.append(f"17-Test Audit reported {warn_17} WARN check(s)")

    for d_msg in hygiene.get("drifts", []):
        attention.append(f"State Drift: {d_msg}")

    fn = dec_summary.get("funnel") or {}
    if dec_summary.get("exists") and _safe_int(fn.get("total_cycles")) > 0:
        if _safe_int(fn.get("power_v2_directional")) > 0 and _safe_int(fn.get("shooting_status_go")) == 0:
            dir_v = dec_summary.get("shooting_directional_subchecks_top") or dec_summary.get("shooting_subchecks_top")
            top_v_str = ", ".join(f"{k} [{v}x]" for k, v in dir_v[:2]) if dir_v else "see [X1]"
            attention.append(
                f"{fn.get('power_v2_directional')} POWER-directional cycles -> 0 Shooting GO (top directional vetoes: {top_v_str})"
            )
        elif _safe_int(fn.get("step3_buy_sell_ge_conf_threshold")) > 0 and _safe_int(fn.get("shooting_status_go")) == 0:
            top_v = dec_summary.get("shooting_subchecks_top") or dec_summary.get("shooting_reasons_top")
            top_v_str = f"{top_v[0][0]} ({top_v[0][1]}x)" if top_v else "see [X1]"
            attention.append(
                f"Shooting Veto 100% of {fn.get('step3_buy_sell_ge_conf_threshold')} conf-passing signals (top: {top_v_str})"
            )
        if _safe_int(dec_summary.get("call_failed_count")) > 0:
            attention.append(f"Step 3 none-call-failed={dec_summary['call_failed_count']} cycle(s)")
        if _safe_int(dec_summary.get("rate_limited_count")) > 0:
            attention.append(f"Step 3 none-rate-limited={dec_summary['rate_limited_count']} cycle(s)")

    if log_summary.get("critical_count", 0) > 0 or log_summary.get("traceback_count", 0) > 0:
        attention.append(
            f"Trading log has critical={log_summary['critical_count']}, tracebacks={log_summary['traceback_count']}"
        )

    if failed_gates or fail_17 > 0 or log_summary.get("critical_count", 0) > 0:
        overall_status = "CRITICAL"
    elif attention:
        overall_status = "ATTENTION"
    else:
        overall_status = "CLEAN"

    # Build Artifact Manifest
    vault_specs = _collect_artifact_specs(root, day_str)
    manifest_rows: List[Dict[str, Any]] = []
    vault_blocks: List[str] = []

    for name, path, fmt, max_lines in vault_specs:
        present = path.exists() and path.is_file()
        size_b = path.stat().st_size if present else 0
        raw_txt = _read_text(path) if present else ""
        if present and fmt == "json":
            parsed = _read_json(path)
            if parsed is not None:
                raw_txt = json.dumps(parsed, indent=2 if size_b < 25000 else None, ensure_ascii=False)
        line_list = raw_txt.splitlines() if raw_txt else []
        orig_lines = len(line_list)
        truncated = False
        if present and max_lines > 0 and orig_lines > max_lines:
            truncated = True
            if fmt == "csv":
                # Keep header + first 200 + last (max_lines - 200) rows
                head_n = min(201, max_lines // 2)
                tail_n = max(1, max_lines - head_n)
                kept = (
                    line_list[:head_n]
                    + [f"# ... [X-RAY TRUNCATED {orig_lines - head_n - tail_n} MIDDLE CSV ROWS] ..."]
                    + line_list[-tail_n:]
                )
            else:
                head_n = max_lines // 3
                tail_n = max_lines - head_n
                kept = (
                    line_list[:head_n]
                    + [f"... [X-RAY TRUNCATED {orig_lines - head_n - tail_n} MIDDLE LINES] ..."]
                    + line_list[-tail_n:]
                )
            embed_txt = "\n".join(kept)
        else:
            embed_txt = raw_txt.rstrip("\n")

        manifest_rows.append(
            {
                "name": name,
                "status": "PRESENT" if present else "MISSING",
                "bytes": size_b,
                "lines": orig_lines,
                "vault": ("EMBEDDED_TRUNC" if truncated else "EMBEDDED") if present else "NONE",
            }
        )
        if include_vault and present:
            emb_bytes = len(embed_txt.encode("utf-8"))
            emb_lines = len(embed_txt.splitlines())
            vault_blocks.append(
                f'{ARTIFACT_BEGIN_PREFIX}name="{name}" format="{fmt}" lines={emb_lines} bytes={emb_bytes} =====\n'
                f"{embed_txt}\n"
                f'{ARTIFACT_END_PREFIX}name="{name}" ====='
            )

    for name, path in _html_and_lake_inventory(root, day_str):
        present = path.exists() and path.is_file()
        size_b = path.stat().st_size if present else 0
        manifest_rows.append(
            {
                "name": name,
                "status": "PRESENT" if present else "MISSING",
                "bytes": size_b,
                "lines": 0,
                "vault": "SUMMARY_ONLY" if present else "NONE",
            }
        )

    generated_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Assemble PART 1 Executive X-Ray Text
    L: List[str] = []
    sep = "=" * 108
    subsep = "-" * 108
    L.append(sep)
    L.append(f"GOLD-BOOKMAP X-RAY REPORT  |  DAY: {day_str}  |  STATUS: {overall_status}")
    L.append(
        f"Generated: {generated_utc}  |  Git: {git_info['branch']}@{git_info['commit']}  |  "
        f"Share this ONE file in chat: data/xray_{day_str}.txt (no .zip needed)"
    )
    L.append(sep)

    if attention:
        L.append("TOP FINDINGS & ATTENTION ITEMS:")
        for idx, item in enumerate(attention, 1):
            L.append(f"  {idx}. [!] {item}")
    else:
        L.append("TOP FINDINGS: All gates, state files, and desk reconciliations reported CLEAN.")
    L.append("")

    # [X1] FUNNEL
    L.append(subsep)
    L.append(f"[X1] END-TO-END PIPELINE FUNNEL (Step 1 -> Step 5 | decisions_{compact}.csv | conf_gate={conf_threshold:.1f}%)")
    L.append(subsep)
    if not dec_summary["exists"] or dec_summary["rows"] == 0:
        L.append(f"  (decisions_{compact}.csv not present or 0 rows for {day_str})")
    else:
        tot = fn["total_cycles"]
        L.append(
            f"  Span: {dec_summary['first_ts']} -> {dec_summary['last_ts']}  |  "
            f"Symbols: {', '.join(dec_summary['symbols']) or '?'}"
        )
        funnel_rows = [
            ("1. Total Recorded Cycles", fn["total_cycles"]),
            ("2. Market Open", fn["market_open"]),
            ("3. Budapest Trading Window", fn["budapest_trading_window"]),
            ("4. News Gate Clear (not BLOCK)", fn["news_not_blocked"]),
            ("5. Feed & Safety Pre-Gate OK", fn["safety_and_feed_pregate_ok"]),
            ("6. POWER v2 Directional (UP/DOWN)", fn["power_v2_directional"]),
            ("7. Signal Panel Directional (BUY/SELL)", fn["panel_directional_buy_sell"]),
            (f"8. Signal Panel >= {conf_threshold:.0f}% Conf", fn["panel_ge_conf_threshold"]),
            ("9. Step 3 Live/Cached/Carry Reached", fn["step3_live_or_cached_or_carry"]),
            ("10. Step 3 Action BUY/SELL", fn["step3_action_buy_sell"]),
            (f"11. Step 3 BUY/SELL >= {conf_threshold:.0f}% Conf", fn["step3_buy_sell_ge_conf_threshold"]),
            ("12. A2 Aggressive Trigger Fires ([A2])", fn["a2_aggressive_fires"]),
            ("13. Shooting Team Verdict == GO", fn["shooting_status_go"]),
            ("14. Step 4 Executed in CSV", fn["mt5_executed_in_csv"]),
        ]
        for label, val in funnel_rows:
            L.append(f"  {label:<42} : {val:>6d}  ({_pct(val, tot):>6s})")

        L.append("")
        L.append(f"  POWER v2 Directions : {dec_summary['power_directions']}")
        if dec_summary["power_reasons"]:
            pr_str = ", ".join(f"{k}={v}" for k, v in dec_summary["power_reasons"][:6])
            L.append(f"  POWER v2 Reasons    : {pr_str}")
        L.append(f"  Panel Directions    : {dec_summary['signal_directions']}")
        L.append(f"  Shooting Statuses   : {dec_summary['shooting_statuses']}")
        L.append(f"  Paper Entry Sim     : {dec_summary['paper_entry_statuses']}")
        L.append(f"  Step 4 Exec Status  : {dec_summary['exec_statuses']}")

        if dec_summary["shooting_directional_subchecks_top"]:
            L.append("")
            L.append("  Top Shooting / Crowd Vetoes on POWER-Directional Cycles (UP/DOWN):")
            for reason_txt, cnt in dec_summary["shooting_directional_subchecks_top"][:10]:
                L.append(f"    {cnt:>5d}x  {reason_txt}")

        if dec_summary["shooting_subchecks_top"]:
            L.append("")
            L.append("  Top Shooting / Crowd Sub-Check Vetoes (All Cycles):")
            for reason_txt, cnt in dec_summary["shooting_subchecks_top"][:8]:
                L.append(f"    {cnt:>5d}x  {reason_txt}")
        elif dec_summary["shooting_reasons_top"]:
            L.append("")
            L.append("  Top Shooting Veto Reasons:")
            for reason_txt, cnt in dec_summary["shooting_reasons_top"][:8]:
                L.append(f"    {cnt:>5d}x  {reason_txt}")

        if dec_summary["top_block_reasons"]:
            L.append("")
            L.append("  Top Cycle Block Reasons (`reason` column):")
            for reason_txt, cnt in dec_summary["top_block_reasons"][:8]:
                L.append(f"    {cnt:>5d}x  {reason_txt}")
    L.append("")

    # [X2] STEP 3 AI & LATENCY
    L.append(subsep)
    L.append("[X2] STEP 3 AI ROUTING, ASYNC ORGAN, KEY POOL & PIPELINE LATENCY")
    L.append(subsep)
    if dec_summary["ai_models"]:
        L.append(
            f"  Live Gemini={dec_summary['live_gemini_completions']} | Cached={dec_summary['cached_ai_count']} | "
            f"Async-Pending={dec_summary['async_pending_count']} | POWER-Carry={dec_summary['power_carry_count']} | "
            f"Throttled={dec_summary['throttled_count']} | Call-Failed={dec_summary['call_failed_count']} | "
            f"Rate-Limited={dec_summary['rate_limited_count']} | Timeout-Budget={dec_summary['timeout_budget_count']}"
        )
        L.append(f"  {'SOURCE':<12} {'AI_MODEL':<34} {'CYCLES':>7} {'SHARE':>7} {'BUY':>5} {'SELL':>5} {'HOLD':>6} {'AVG_CONF':>9}")
        for row in dec_summary["ai_models"]:
            L.append(
                f"  {row['decision_source']:<12} {row['ai_model']:<34} {row['count']:>7d} "
                f"{row['pct']:>6.1f}% {row['buy']:>5d} {row['sell']:>5d} {row['hold']:>6d} {row['avg_conf']:>8.1f}%"
            )
    else:
        L.append("  (No Step 3 model rows recorded)")

    aic = hygiene["states"].get("ai_calls")
    gks = hygiene["states"].get("gemini_keys_state")
    if aic:
        L.append(f"  ai_calls.json       : date={aic.get('date')} calls={aic.get('calls')}")
    if gks is not None:
        L.append(f"  gemini_keys_state   : active/recorded cooldown slots={gks.get('tracked_cooldown_slots', 0)}")
    if tail_txt:
        L.append("  Truck Camera (tail_probe.py):")
        tail_key_lines = [
            tl.rstrip()
            for tl in tail_txt.splitlines()
            if any(
                k in tl
                for k in (
                    "cycles parsed:",
                    "cycles:",
                    "slow cycles",
                    "WHO owns",
                    "STEP1 (",
                    "STEP2 (",
                    "STEP3 (",
                    "-> STEP",
                    "owner:",
                )
            )
        ]
        for tl in (tail_key_lines or [x.rstrip() for x in tail_txt.splitlines() if x.strip()][:8])[:10]:
            L.append(f"    {tl}")
    L.append("")

    # [X3] STEP 4 BASIS, SPREAD, QUALITY, ESCORT & TCA
    L.append(subsep)
    L.append("[X3] STEP 4 BASIS, SPREAD, MARKET QUALITY, POSITION MANAGER, ESCORT & TCA")
    L.append(subsep)
    L.append(
        f"  Diary inspected     : {organ_summary['diary_path'] or '(none)'} "
        f"({organ_summary['diary_records']} snapshots, signal_map walls seen={organ_summary['signal_map_walls_seen']})"
    )
    if organ_summary["spread_samples"] > 0:
        L.append(
            f"  Spread Telemetry    : n={organ_summary['spread_samples']} | min={organ_summary['spread_min']} "
            f"| median={organ_summary['spread_median']} | p95={organ_summary['spread_p95']} | max={organ_summary['spread_max']}"
        )
    else:
        L.append("  Spread Telemetry    : (no numeric spread field in diary; check run_config V6_CFD_SPREAD_MAX & logs)")
    if organ_summary["basis_samples"] > 0:
        L.append(
            f"  COMEX-MT5 Basis     : n={organ_summary['basis_samples']} | min={organ_summary['basis_min']} "
            f"| median={organ_summary['basis_median']} | max={organ_summary['basis_max']}"
        )
    elif log_summary["basis_mentions"] > 0:
        L.append(f"  COMEX-MT5 Basis Log : {log_summary['basis_mentions']} log line(s); sample: {log_summary['last_basis_lines'][-1]}")
    else:
        L.append("  COMEX-MT5 Basis     : 0 live Step 4 basis conversions logged today (no entry order reached Step 4 fill path)")

    if organ_summary["market_quality_latest"]:
        L.append(f"  Market Quality Last : {json.dumps(organ_summary['market_quality_latest'], ensure_ascii=False)}")
    L.append(
        f"  Step 4a PosManager  : actions_today={organ_summary['pm_actions_today']} {organ_summary['pm_actions_breakdown'] or ''}"
    )
    L.append(
        f"  Escort Team         : active_cycles={organ_summary['escort_active_cycles']} {organ_summary['escort_actions'] or '(flat all day)'}"
    )
    L.append(
        f"  Closed Trades & TCA : closed_today={organ_summary['trades_closed_today']} (net=${organ_summary['trades_today_net_pnl']:+.2f}) | "
        f"rolling_memory={organ_summary['trades_rolling_total']} trades "
        f"(win_rate={organ_summary['trades_rolling_win_rate']}%, net=${organ_summary['trades_rolling_net_pnl']:+.2f}) | "
        f"TCA_rows_today={organ_summary['tca_rows_today']} (avg_slip={organ_summary['tca_avg_slippage_pts']})"
    )
    L.append("")

    # [X4] SIGNAL HEALTH, WALL MAP & SPIES
    L.append(subsep)
    L.append("[X4] SIGNAL HEALTH, WALL MAP & FLOW / ORDER-BOOK SPIES")
    L.append(subsep)
    for label, txt_blob, max_l in (
        ("Signal Health (tools/signal_health.py)", sig_health_txt, 22),
        ("Wall Magnet & Barrier Test (tools/signal_map.py)", sig_map_txt, 18),
        ("Print Flow Spy (flow_probe.py)", flow_txt, 12),
        ("Order-Book Spy (book_probe.py)", book_txt, 14),
    ):
        if txt_blob:
            L.append(f"  --- {label} ---")
            picked = [ln.rstrip() for ln in txt_blob.splitlines() if ln.strip() and not ln.startswith("====")]
            for ln in picked[:max_l]:
                L.append(f"    {ln[:140]}")
        else:
            L.append(f"  --- {label}: (not generated for {day_str}) ---")
    L.append("")

    # [X5] STATE HYGIENE
    L.append(subsep)
    L.append("[X5] PERSISTENT STATE-HYGIENE & CROSS-ORGAN DRIFT CHECK")
    L.append(subsep)
    if hygiene["drifts"]:
        for d_msg in hygiene["drifts"]:
            L.append(f"  [DRIFT] {d_msg}")
    else:
        L.append("  [OK] Zero cross-organ state drift detected across trade_guard, risk_state, a2_burst_count, and MT5 metrics.")
    for sk, sv in hygiene["states"].items():
        L.append(f"  {sk:<28} : {json.dumps(sv, ensure_ascii=False)[:140]}")
    L.append("")

    # [X6] LIVE TRADING LOG & WATCHDOG
    L.append(subsep)
    L.append(f"[X6] LIVE TRADING LOG & WATCHDOG DIGEST ({log_summary['trading_log_name']})")
    L.append(subsep)
    if log_summary["trading_log_exists"]:
        L.append(
            f"  Lines={log_summary['trading_log_lines']} | CRITICAL={log_summary['critical_count']} | "
            f"ERROR={log_summary['error_count']} | Tracebacks={log_summary['traceback_count']} | "
            f"WARNING={log_summary['warning_count']}"
        )
        if log_summary["top_errors"]:
            L.append("  Top Deduplicated ERROR / CRITICAL Signatures:")
            for sig, cnt in log_summary["top_errors"]:
                L.append(f"    {cnt:>5d}x  {sig}")
        if log_summary["top_warnings"]:
            L.append("  Top Deduplicated WARNING Signatures:")
            for sig, cnt in log_summary["top_warnings"][:8]:
                L.append(f"    {cnt:>5d}x  {sig}")
    else:
        L.append(f"  ({log_summary['trading_log_name']} not present in logs/)")
    if log_summary["watchdog_exists"]:
        L.append(f"  Watchdog entries on {day_str}: {len(log_summary['watchdog_day_lines'])}")
        for wl in log_summary["watchdog_day_lines"][-5:]:
            L.append(f"    {wl}")
    L.append("")

    # [X7] 17-TEST AUDIT, JUDGES & 10 DESKS
    L.append(subsep)
    L.append("[X7] 17-TEST AUDIT, JUDGE PANEL & COMPANY 2 DESKS SCORECARD")
    L.append(subsep)
    if checks:
        gate_str = ", ".join(f"{c.get('name')}={c.get('status')}" for c in checks)
        L.append(f"  Stage 1 Test Gate   : {gate_str}")
    if preflight_txt:
        pf_m = re.search(r"RESULT:.*", preflight_txt)
        if pf_m:
            L.append(f"  Stage 2 Preflight   : {pf_m.group(0).strip()}")
    if metrics:
        L.append(
            f"  Stage 2 Day Metrics : verdict='{metrics.get('verdict_line', '')}' | "
            f"feed_prints={metrics.get('feed_prints')} | decisions={metrics.get('decisions')} | "
            f"orders_to_mt5={metrics.get('orders_to_mt5')}"
        )
    if audit_issues:
        L.append("  17-Test WARN / FAIL Headlines:")
        for iss in audit_issues:
            L.append(f"    {iss}")
    if judge_summary["exists"]:
        L.append(
            f"  Judge Panel Summary : total={judge_summary['count']} "
            f"(active={judge_summary.get('active_count', 0)}, "
            f"retired_bench={judge_summary.get('retired_count', 0)}, "
            f"silent={judge_summary.get('silent_count', 0)})"
        )
        if judge_summary["top"]:
            L.append("    Top 5 Active Judges    : " + ", ".join(
                f"{j['judge']}({j['net_pts']:+.1f}pts, {j['votes']}v, {j['right_pct']}%)" for j in judge_summary["top"]
            ))
        if judge_summary["bottom"]:
            L.append("    Bottom 5 Active Judges : " + ", ".join(
                f"{j['judge']}({j['net_pts']:+.1f}pts, {j['votes']}v, {j['right_pct']}%)" for j in judge_summary["bottom"]
            ))
        if judge_summary.get("retired_bench"):
            L.append("    Retired Bench (Shadow) : " + ", ".join(
                f"{j['judge']}({j['net_pts']:+.1f}pts, {j['votes']}v, {j['right_pct']}%)" for j in judge_summary["retired_bench"]
            ))
    if shadow_txt:
        L.append("  Desk 3 Shadow Replay:")
        for sl in shadow_txt.splitlines():
            if any(k in sl for k in ("SHADOW (", "BENCHMARK ")):
                L.append(f"    {sl.strip()}")
    if paper_txt:
        rec_m = re.search(r"\[chief of staff\] RECONCILIATION.*?(?=\n-{10,}|\Z)", paper_txt, re.S)
        if rec_m:
            L.append("  3-Desk Reconciliation:")
            for rl in rec_m.group(0).strip().splitlines()[1:8]:
                L.append(f"    {rl.strip()}")
    for dlabel, dtxt in (
        ("Desk 9 POWER Card", power_card_txt),
        ("Desk 10 Council Shadow", council_txt),
        ("Desk 7 Clocks", clocks_txt),
    ):
        if dtxt:
            first_lines = [x.strip() for x in dtxt.splitlines() if x.strip() and not x.startswith("====")][:4]
            L.append(f"  {dlabel:<22}: {' | '.join(first_lines)[:150]}")
    L.append("")

    # [X8] SANITIZED RUN CONFIG
    L.append(subsep)
    L.append(f"[X8] ACTIVE RUNTIME CONFIGURATION (run_config_{compact}.json, sanitized)")
    L.append(subsep)
    if run_cfg:
        items = list(run_cfg.items())
        for i in range(0, len(items), 2):
            left = f"{items[i][0]}={items[i][1]}"
            right = f"{items[i + 1][0]}={items[i + 1][1]}" if i + 1 < len(items) else ""
            L.append(f"  {left:<50} {right}")
    else:
        L.append(f"  (run_config_{compact}.json not present)")
    L.append("")

    # [X9] ARTIFACT MANIFEST
    L.append(subsep)
    L.append("[X9] FULL X-RAY ARTIFACT MANIFEST (All Evening & Companion Files)")
    L.append(subsep)
    L.append(f"  {'ARTIFACT':<40} {'STATUS':<10} {'BYTES':>10} {'LINES':>8} {'VAULT_MODE':<16}")
    for m in manifest_rows:
        L.append(
            f"  {m['name']:<40} {m['status']:<10} {m['bytes']:>10d} {m['lines']:>8d} {m['vault']:<16}"
        )
    L.append("")

    part1_text = redact_text("\n".join(L))

    if include_vault and vault_blocks:
        full_text = (
            part1_text
            + "\n"
            + VAULT_INDEX_BANNER
            + "\n"
            + f"# Embedded plain-text artifacts: {len(vault_blocks)} | Unpack via: python tools/xray_report.py --unpack data/xray_{day_str}.txt --out <dir>\n\n"
            + "\n\n".join(vault_blocks)
            + "\n"
        )
    else:
        full_text = part1_text + "\n"

    full_text = redact_text(full_text)

    summary_json: Dict[str, Any] = sanitize_json_obj(
        {
            "date": day_str,
            "generated_utc": generated_utc,
            "overall_status": overall_status,
            "git": git_info,
            "attention_items": attention,
            "funnel": dec_summary.get("funnel", {}),
            "power_directions": dec_summary.get("power_directions", {}),
            "shooting_statuses": dec_summary.get("shooting_statuses", {}),
            "shooting_subchecks_top": dec_summary.get("shooting_subchecks_top", []),
            "ai_models": dec_summary.get("ai_models", []),
            "organs": organ_summary,
            "state_hygiene": hygiene,
            "logs": {
                "trading_log_exists": log_summary["trading_log_exists"],
                "lines": log_summary["trading_log_lines"],
                "critical": log_summary["critical_count"],
                "errors": log_summary["error_count"],
                "tracebacks": log_summary["traceback_count"],
                "warnings": log_summary["warning_count"],
            },
            "judges": judge_summary,
            "manifest_present_count": sum(1 for m in manifest_rows if m["status"] == "PRESENT"),
            "manifest_total_count": len(manifest_rows),
            "embedded_vault_count": len(vault_blocks),
            "part1_overview_text": part1_text,
        }
    )
    return full_text, summary_json


def extract_overview_only(xray_txt: str) -> str:
    """Return Part 1 Executive Overview (strip Part 2 raw vault) for clean HTML display."""
    if not xray_txt:
        return ""
    idx = xray_txt.find(VAULT_INDEX_BANNER)
    if idx >= 0:
        return xray_txt[:idx].rstrip()
    return xray_txt.rstrip()


def unpack_vault(xray_file: Path, out_dir: Path) -> List[Path]:
    """Reconstruct embedded vault files from a single `xray_<DATE>.txt` file."""
    txt = xray_file.read_text(encoding="utf-8", errors="replace")
    out_dir.mkdir(parents=True, exist_ok=True)
    rx = re.compile(
        r'^===== XRAY_ARTIFACT_BEGIN name="([^"]+)" format="[^"]+" lines=\d+ bytes=\d+ =====\n'
        r"(.*?)\n"
        r'^===== XRAY_ARTIFACT_END name="\1" =====$',
        re.M | re.S,
    )
    written: List[Path] = []
    for m in rx.finditer(txt):
        raw_name, body = m.group(1), m.group(2)
        safe_name = Path(raw_name).name
        if not safe_name or safe_name in (".", ".."):
            continue
        dest = out_dir / safe_name
        dest.write_text(body + "\n", encoding="utf-8", newline="\n")
        written.append(dest)
    return written


def write_xray_files(root: Path, day_str: str, include_vault: bool = True) -> Tuple[Path, Path, Dict[str, Any]]:
    data_dir = root.resolve() / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    full_txt, summary_json = build_xray(root, day_str, include_vault=include_vault)
    txt_path = data_dir / f"xray_{day_str}.txt"
    json_path = data_dir / f"xray_{day_str}.json"
    txt_path.write_text(full_txt, encoding="utf-8", newline="\n")
    json_path.write_text(json.dumps(summary_json, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return txt_path, json_path, summary_json


def _auto_detect_date(root: Path) -> str:
    logs_dir = root / "logs"
    if logs_dir.exists():
        logs = sorted(logs_dir.glob("trading_*.log"))
        for p in reversed(logs):
            d8 = p.stem.replace("trading_", "")
            if len(d8) == 8 and d8.isdigit():
                return f"{d8[:4]}-{d8[4:6]}-{d8[6:8]}"
    data_dir = root / "data"
    if data_dir.exists():
        decs = sorted(data_dir.glob("decisions_20*.csv"))
        for p in reversed(decs):
            d8 = p.stem.replace("decisions_", "")
            if len(d8) == 8 and d8.isdigit():
                return f"{d8[:4]}-{d8[4:6]}-{d8[6:8]}"
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build single-file Gold-BookMap X-Ray report + plain-text artifact vault")
    ap.add_argument("--date", default="", help="Trading date YYYY-MM-DD (default: auto-detect newest trading log)")
    ap.add_argument("--dir", default=".", help="Project root directory (default: current directory)")
    ap.add_argument("--no-vault", action="store_true", help="Write Part 1 executive overview only without Part 2 vault")
    ap.add_argument("--quiet", action="store_true", help="Suppress console banner")
    ap.add_argument("--unpack", default="", help="Path to an existing xray_<DATE>.txt file to unpack")
    ap.add_argument("--out", default="", help="Destination directory for --unpack")
    args = ap.parse_args(argv)

    if args.unpack:
        src = Path(args.unpack).resolve()
        if not src.exists():
            print(f"[xray] file not found: {src}", file=sys.stderr)
            return 1
        dest = Path(args.out).resolve() if args.out else (src.parent / f"unpacked_{src.stem}")
        files = unpack_vault(src, dest)
        if not args.quiet:
            print(f"[xray] unpacked {len(files)} artifact(s) -> {dest}")
        return 0

    root = Path(args.dir).resolve()
    day_str = args.date.strip() or _auto_detect_date(root)
    txt_path, json_path, summary = write_xray_files(root, day_str, include_vault=(not args.no_vault))
    if not args.quiet:
        sz_kb = txt_path.stat().st_size / 1024.0
        print(
            f"[xray] ready: {txt_path} ({sz_kb:.1f} KB, status={summary['overall_status']}, "
            f"present={summary['manifest_present_count']}/{summary['manifest_total_count']}, "
            f"vault={summary['embedded_vault_count']} embedded files) + {json_path.name}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
