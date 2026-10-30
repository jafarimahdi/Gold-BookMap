"""Regression tests for the 2026-10-29 Evening First-DEMO-Day 7-Organ Upgrade."""
from __future__ import annotations

import csv
import datetime as dt
import gzip
import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import audit_day
import book_probe
import config
import main
import position_manager
import session
import step3_ai_decision as s3_mod
import step4_mt5_execution as s4_mod
from bookmap_bridge_provider import _BridgeTail
from tools import xray_report

_REPO_ROOT = Path(__file__).resolve().parent


def _dir_fingerprint(path: Path) -> list[tuple[str, int, int]]:
    if not path.exists():
        return []
    out = []
    for p in sorted(path.rglob("*")):
        if p.is_file():
            st = p.stat()
            out.append((str(p.relative_to(path)), st.st_size, st.st_mtime_ns))
    return out


def test_fix1_ntp_skew_tolerance_and_persistent_executor(monkeypatch, tmp_path):
    """#1: GMT+3 sub-5s NTP skew clamps to 0.0, k=0 future ticks still fail closed, and main caches MT5Executor."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "MT5_SERVER_CLOCK_SKEW_TOLERANCE_SECONDS", 5.0)
    monkeypatch.setattr(config, "MT5_SERVER_TZ_OFFSET_HOURS", "")

    now_ep = 1793299965.0  # 2026-10-29 16:12:45 UTC
    # Laptop clock lags Pepperstone GMT+3 (+10,800s) by 1.35s:
    gmt3_ahead_tick = now_ep + 10800.0 + 1.35
    age, offset = s4_mod.resolve_mt5_quote_age(
        gmt3_ahead_tick, now_ep, max_age=300.0, cached_offset_s=None, config_mod=config
    )
    assert age == 0.0
    assert offset == 10800.0
    assert (tmp_path / "mt5_server_offset.json").exists()
    assert s4_mod.load_persisted_mt5_server_offset_seconds(config) == 10800.0

    # Subsequent tick on locked cached_offset_s=10800 with 2.1s NTP skew also clamps to 0.0:
    age2, offset2 = s4_mod.resolve_mt5_quote_age(
        now_ep + 10800.0 + 2.10, now_ep, max_age=300.0, cached_offset_s=offset, config_mod=config
    )
    assert age2 == 0.0
    assert offset2 == 10800.0

    # 1-hour stale tick on locked GMT+3 offset stays 3,600s stale (never snaps down to k=2):
    stale_age, stale_off = s4_mod.resolve_mt5_quote_age(
        now_ep + 10800.0 - 3600.0, now_ep, max_age=300.0, cached_offset_s=offset, config_mod=config
    )
    assert abs(stale_age - 3600.0) < 0.01
    assert stale_off == 10800.0

    # Uncalibrated UTC bogus future tick (+1.0s) STILL fails closed (< 0 'future-dated'):
    bogus_age, _ = s4_mod.resolve_mt5_quote_age(
        now_ep + 1.0, now_ep, max_age=300.0, cached_offset_s=None, config_mod=config
    )
    assert bogus_age < 0.0

    # Persistent executor reuse in main._get_mt5_executor:
    monkeypatch.setattr(main, "_CACHED_MT5_EXECUTOR", None)
    e1 = main._get_mt5_executor(s4_mod.MT5Executor)
    e2 = main._get_mt5_executor(s4_mod.MT5Executor)
    assert e1 is e2

    class _FakeExec:
        symbol = config.MT5_SYMBOL
        magic = getattr(config, "MT5_MAGIC", 234000)

    f1 = main._get_mt5_executor(_FakeExec)
    assert isinstance(f1, _FakeExec)


def test_fix2_step3_slice_cap_once_only_demotion_and_model_rpm_failover(monkeypatch, caplog):
    """#2: 7.5s slice cap, once-only demotion WARNING, and multi-key 429 model-RPM failover preserving keys."""
    monkeypatch.setattr(config, "AI_MODEL_SLICE_CAP_SECONDS", 7.5, raising=False)
    monkeypatch.setattr(config, "AI_MODEL_SLOW_STRIKES", 2, raising=False)
    monkeypatch.setattr(config, "AI_MODEL_DEMOTE_SECONDS", 900.0, raising=False)
    monkeypatch.setattr(config, "AI_DECIDE_BUDGET_SECONDS", 25.0, raising=False)
    monkeypatch.setattr(config, "GEMINI_MODEL", "gemini-3.8-flash", raising=False)
    monkeypatch.setattr(
        config,
        "GEMINI_MODELS",
        ["gemini-3.8-flash", "gemini-3.6-flash", "gemini-3.5-flash-lite"],
        raising=False,
    )
    monkeypatch.setattr(s3_mod, "genai", SimpleNamespace(), raising=False)
    # Real _exhausted_keys() returns a list/dict, not a set — test against list!
    monkeypatch.setattr(s3_mod, "_exhausted_keys", lambda: [])
    parked = []
    monkeypatch.setattr(s3_mod, "_mark_exhausted", lambda k: parked.append(k))

    s3_mod.AIDecisionEngine._MODEL_SLOW_STRIKES.clear()
    s3_mod.AIDecisionEngine._MODEL_DEMOTED_UNTIL.clear()

    # Transition-only WARNING check (prevents 157x log spam):
    with caplog.at_level(logging.WARNING):
        s3_mod.AIDecisionEngine._record_model_slow_or_overloaded("gemini-3.8-flash", "timeout")
        s3_mod.AIDecisionEngine._record_model_slow_or_overloaded("gemini-3.8-flash", "timeout")
        s3_mod.AIDecisionEngine._record_model_slow_or_overloaded("gemini-3.8-flash", "timeout")
        s3_mod.AIDecisionEngine._record_model_slow_or_overloaded("gemini-3.8-flash", "timeout")
    demote_warns = [r for r in caplog.records if "demoting slow/congested model" in r.message]
    assert len(demote_warns) == 1

    # Reset and verify 2x 429 on 3.8-flash fails over to 3.6-flash WITHOUT parking keys 2..5:
    s3_mod.AIDecisionEngine._MODEL_SLOW_STRIKES.clear()
    s3_mod.AIDecisionEngine._MODEL_DEMOTED_UNTIL.clear()
    adv = s3_mod.AIDecisionEngine(
        api_keys=["key-1", "key-2", "key-3", "key-4", "key-5"],
    )
    calls = []

    def _fake_invoke(key, model, _prompt, budget):
        calls.append((key, model, round(budget, 2)))
        if model == "gemini-3.8-flash":
            raise RuntimeError("429 RESOURCE_EXHAUSTED quota exceeded")
        return '{"action":"BUY","confidence":72,"rationale":"clean breakout"}'

    monkeypatch.setattr(adv, "_invoke_bounded", _fake_invoke)
    snap = SimpleNamespace(
        price=4025.0,
        signal_direction="BUY",
        signal_strength=70.0,
        signal_confidence=68.0,
        regime="TREND",
        power_v2={"direction": "UP", "reason_code": "UPWARD_FORCE_DOMINATES"},
        shooting_plan={"shot": "GO", "side": "BUY"},
    )
    monkeypatch.setattr(adv, "build_prompt", lambda _s: "test prompt")
    dec = adv.decide(snap)
    assert dec.action == "BUY"
    assert dec.model == "gemini-3.6-flash"
    # Only key-1 was parked; key-2 triggered immediate model failover and keys 2..5 stayed unparked!
    assert parked == ["key-1"]
    assert calls[0][2] == 7.5
    assert calls[2][0] == "key-2" and calls[2][1] == "gemini-3.6-flash"


def test_fix3_mt5_truth_gmt3_late_deal_and_deal_id_reconciliation(monkeypatch, tmp_path):
    """#3: _mt5_truth captures 23:28 GMT+3 (20:28Z) deal, stamps UTC, and reconciles deal_id/pos=<id>."""
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(audit_day, "DATA_DIR", lambda: tmp_path)
    monkeypatch.setattr(audit_day, "SKIP_MT5", False, raising=False)

    # Persist GMT+3 (+10,800s) offset calibrated during the session:
    s4_mod.persist_mt5_server_offset(10800.0, config_mod=config)

    day = dt.date(2026, 10, 29)
    # Historical September deal in append-only tracked_bot_positions.json (must NOT leak into Oct 29):
    t_sep = int(datetime(2026, 9, 24, 11, 10, 0, tzinfo=timezone.utc).timestamp())
    d_sep_close = SimpleNamespace(type=1, entry=1, profit=-3.21, time=t_sep + 10800, position_id=90001722, ticket=90009999, symbol="XAUUSD", volume=0.01, price=3950.0, commission=-0.04, swap=0.0, comment="[sl]")

    # Deal 1: closed 19:04 UTC = 22:04 GMT+3; Deal 2: closed 20:28 UTC = 23:28 GMT+3
    t1_utc = int(datetime(2026, 10, 29, 19, 4, 0, tzinfo=timezone.utc).timestamp())
    t2_utc = int(datetime(2026, 10, 29, 20, 28, 0, tzinfo=timezone.utc).timestamp())
    d1_open = SimpleNamespace(type=1, entry=0, profit=0.0, time=t1_utc - 480 + 10800, position_id=97403261, ticket=97403262, symbol="XAUUSD", volume=0.01, price=4014.40, commission=-0.04, swap=0.0, comment="open")
    d1_close = SimpleNamespace(type=0, entry=1, profit=-1.80, time=t1_utc + 10800, position_id=97403261, ticket=97409335, symbol="XAUUSD", volume=0.01, price=4016.20, commission=0.0, swap=0.0, comment="[sl 4016.20]")
    d2_open = SimpleNamespace(type=0, entry=0, profit=0.0, time=t2_utc - 420 + 10800, position_id=97419929, ticket=97419930, symbol="XAUUSD", volume=0.01, price=4025.71, commission=-0.04, swap=0.0, comment="open")
    d2_close = SimpleNamespace(type=1, entry=1, profit=1.03, time=t2_utc + 10800, position_id=97419929, ticket=97422118, symbol="XAUUSD", volume=0.01, price=4026.74, commission=0.0, swap=0.0, comment="[sl 4026.74]")

    (tmp_path / "tracked_bot_positions.json").write_text(
        json.dumps({"position_ids": [90001722, 97403261, 97419929], "updated_at": "2026-10-29T20:29:00+00:00"}),
        encoding="utf-8",
    )
    # trade_outcomes.csv written with order_id = closing deal ticket (just like live 2026-10-29):
    with open(tmp_path / "trade_outcomes.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["timestamp", "order_id", "symbol", "side", "pnl", "exit_price", "comment"])
        w.writeheader()
        w.writerow({"timestamp": "2026-10-29T19:04:10+00:00", "order_id": "97409335", "symbol": "XAUUSD", "side": "SELL", "pnl": "-1.80", "exit_price": "4016.20", "comment": "closed deal"})
        w.writerow({"timestamp": "2026-10-29T20:28:15+00:00", "order_id": "97422118", "symbol": "XAUUSD", "side": "BUY", "pnl": "1.03", "exit_price": "4026.74", "comment": "closed deal pos=97419929"})

    fake_mt5 = SimpleNamespace(
        initialize=lambda: True,
        shutdown=lambda: None,
        positions_get=lambda: [],
        symbol_info_tick=lambda _s: SimpleNamespace(time=t2_utc + 120 + 10800, time_msc=(t2_utc + 120 + 10800) * 1000),
        history_deals_get=lambda *args, **kwargs: [d_sep_close, d1_open, d1_close, d2_open, d2_close],
    )
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_mt5)
    truth, err = audit_day._mt5_truth(day)
    assert err is None
    assert truth["closed"] == 2
    assert round(truth["pnl"], 2) == -0.77
    assert truth["closed_deals"][0]["time_utc"].startswith("2026-10-29T19:04")
    assert truth["closed_deals"][1]["time_utc"].startswith("2026-10-29T20:28")

    rec_rows, _ = audit_day._outcomes_for_day(day)
    _, n_written, n_missing = audit_day.write_mt5_reconciliation(day, truth, rec_rows)
    assert n_written == 2
    assert n_missing == 0

    # Verify TEST 13 grades PASS on today when open_now==0 and all today's closed deals match,
    # even though tracked_bot_positions.json carries historical September IDs:
    today_buda = datetime.now(audit_day.BUDA).date()
    monkeypatch.setattr(audit_day, "_mt5_truth", lambda _d: (truth, None))
    monkeypatch.setattr(audit_day, "_outcomes_for_day", lambda _d: (rec_rows, tmp_path / "trade_outcomes.csv"))
    rep13, _ = audit_day.test_13_money(today_buda, decisions=[{"exec_status": "EXECUTED"}, {"exec_status": "EXECUTED"}])
    assert rep13.rows[0][0] == "PASS"


def test_fix4_cme_break_dst_prebreak_flatten_and_audit_gap_grading(monkeypatch, caplog, tmp_path):
    """#4: CME 17:00-18:00 NY break adapts across EDT/EST, flattens 5m pre-break, and avoids false audit WARNs."""
    monkeypatch.setattr(config, "DAILY_BREAK_START", "")
    monkeypatch.setattr(config, "DAILY_BREAK_END", "")
    monkeypatch.setattr(config, "CME_DAILY_BREAK_AUTO", True)
    monkeypatch.setattr(config, "SESSION_ENFORCE", True)
    monkeypatch.setattr(config, "BUDAPEST_TRADING_ONLY", False)

    # Oct 29, 2026 (EU CET +1, US still EDT -4): break is 21:00-22:00 UTC (22:00-23:00 Budapest)
    oct29_open = datetime(2026, 10, 29, 20, 50, tzinfo=timezone.utc)
    oct29_break = datetime(2026, 10, 29, 21, 15, tzinfo=timezone.utc)
    assert session.cme_daily_break_utc_window(oct29_open) == ("21:00", "22:00")
    assert session.is_market_open(oct29_open) is True
    assert session.is_market_open(oct29_break) is False
    assert "17:00-18:00 NY CME maintenance" in session.describe_now(oct29_break)

    # Nov 3, 2026 (both EU CET +1 and US EST -5): break shifts to 22:00-23:00 UTC
    nov3_2115 = datetime(2026, 11, 3, 21, 15, tzinfo=timezone.utc)
    nov3_2215 = datetime(2026, 11, 3, 22, 15, tzinfo=timezone.utc)
    assert session.cme_daily_break_utc_window(nov3_2115) == ("22:00", "23:00")
    assert session.is_market_open(nov3_2115) is True
    assert session.is_market_open(nov3_2215) is False

    # PositionManager pre-break flatten at 20:56 UTC on Oct 29 + 0-open daily receipt:
    monkeypatch.setattr(config, "PM_PRE_CME_BREAK_MINUTES", 5.0)
    monkeypatch.setattr(config, "PM_DAILY_FLATTEN_LOCAL", "23:30")
    monkeypatch.setattr(config, "PM_DAILY_FLATTEN_UTC", "")
    pm = position_manager.PositionManager.__new__(position_manager.PositionManager)
    pm._mt5 = SimpleNamespace()
    pm._state = {}
    pm._mt5_init_warn_ts = 0.0
    pm._bot_positions = lambda: []
    pm._prune_state = lambda _s: None
    reason_pre = pm._session_flatten_due(datetime(2026, 10, 29, 20, 56, tzinfo=timezone.utc))
    assert "pre-break" in reason_pre and "20:55 UTC" in reason_pre
    # After 22:00 UTC reopen (23:05 Budapest, before 23:30 local flatten), no pre-break flatten:
    assert pm._session_flatten_due(datetime(2026, 10, 29, 22, 5, tzinfo=timezone.utc)) == ""

    # audit_day _is_cme_maintenance_gap recognizes 21:59:59 -> 23:00:01 CET on 2026-10-29:
    a_dt = datetime(2026, 10, 29, 20, 59, 59, tzinfo=timezone.utc)
    b_dt = datetime(2026, 10, 29, 22, 0, 1, tzinfo=timezone.utc)
    assert audit_day._is_cme_maintenance_gap(a_dt, b_dt) is True
    # Random midday 60m gap at 14:00->15:00 UTC is NOT excused:
    assert audit_day._is_cme_maintenance_gap(
        datetime(2026, 10, 29, 14, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 29, 15, 0, 0, tzinfo=timezone.utc),
    ) is False

    # TEST 16 #1 & #11 on 2026-10-29: full M3 28/28 tail during day + post-CME-break 3/28 at 23:12 local
    # (before 23:30 flatten) -> #1 PASS and #11 NA (no false WARNs!):
    monkeypatch.setattr(
        audit_day,
        "_load_run_config",
        lambda _d: {"POWER_BAR_SECONDS": 180, "TRADING_ENABLED": 1, "EXECUTION_MODE": "python", "PM_DAILY_FLATTEN_LOCAL": "23:30"},
    )
    log_txt = (
        "2026-10-29 15:00:00,000 INFO B3 limit queue: False\n"
        "2026-10-29 21:55:00,000 INFO POWER HISTORY PROBE | status=TIMEFRAME_MISMATCH:M5_probe_for_M3_regime "
        "probe_tf=M5 regime_tf=M3 regime_bar_seconds=180 loaded_probe_bars=28 usable_probe_bars=0 "
        "probe_compatible=False regime_tail=28/28 regime_reason=ADX_TREND regime_gaps=0 "
        "latest_bar_present=True tick_age_s=1.2 scale=NO_HISTORY history_refused=True\n"
        "2026-10-29 23:12:00,000 INFO POWER HISTORY PROBE | status=TIMEFRAME_MISMATCH:M5_probe_for_M3_regime "
        "probe_tf=M5 regime_tf=M3 regime_bar_seconds=180 loaded_probe_bars=28 usable_probe_bars=0 "
        "probe_compatible=False regime_tail=3/28 regime_reason=GAP_IN_COMPLETED_M3_HISTORY regime_gaps=1 "
        "latest_bar_present=True tick_age_s=4.2 scale=NO_HISTORY history_refused=True | LIVE WARM-UP 3/28 M3 bars\n"
    )
    rep16, _ = audit_day.test_16_team_cycle(
        dt.date(2026, 10, 29),
        [{"power_v2": {"direction": "UP", "reason_code": "UPWARD_FORCE_DOMINATES"}}],
        log_txt,
        [{"exec_status": "EXECUTED", "timestamp": "2026-10-29T18:56:03+00:00"}],
    )
    blob16 = " ".join(rep16.rows[0][3])
    assert "[PASS] 1  active-timeframe regime history" in blob16
    assert "[NA  ] 11  PM daily flatten" in blob16
    assert "[WARN]" not in blob16


def test_fix5_bridge_skips_closed_stale_gz_chunk_but_replays_fresh_chunk(tmp_path):
    """#5: Closed yesterday .csv.gz chunk is skipped without decompression; fresh live file replays cleanly."""
    now = time.time()
    stale_gz = tmp_path / "ticks_20261027_105915.csv.gz"
    # Write deliberately corrupted bytes to prove gzip.open is NEVER called on closed stale chunk:
    stale_gz.write_bytes(b"NOT_A_VALID_GZIP_STREAM_IF_OPENED_IT_WOULD_FAIL")
    old_mtime = now - 30 * 3600
    os.utime(stale_gz, (old_mtime, old_mtime))

    live_csv = tmp_path / "ticks.csv"
    ts_live = datetime.fromtimestamp(now - 5.0, tz=timezone.utc).isoformat()
    live_csv.write_text(
        f"{ts_live},Bid,4025.4,10,1,,GCZ6\n"
        f"{ts_live},Ask,4025.6,10,2,,GCZ6\n"
        f"{ts_live},Last,4025.6,3,3,BUY,GCZ6\n",
        encoding="utf-8",
    )

    tail = _BridgeTail(str(live_csv))
    tail._replay_recorded_history()
    assert "GCZ6" in tail.instruments
    assert len(tail.instruments["GCZ6"].ticks) == 1


def test_fix6_xray_a2_go_episode_reconciliation_and_power_jsonl_memory(tmp_path):
    """#6: X-Ray reconciles a2_burst_count=5 against 5 M3 Shooting=GO episodes (15 rows) and finds power_<DATE>.jsonl."""
    data_dir = tmp_path / "data"
    (data_dir / "power_v2_memory").mkdir(parents=True)
    (data_dir / "power_v2_memory" / "power_2026-10-29.jsonl").write_text(
        '{"ts":"2026-10-29T16:12:45Z","dir":"UP"}\n{"ts":"2026-10-29T18:56:03Z","dir":"DOWN"}\n',
        encoding="utf-8",
    )
    (data_dir / "a2_burst_count.json").write_text(
        json.dumps({"date": "2026-10-29", "count": 5}), encoding="utf-8"
    )
    dec_csv = data_dir / "decisions_20261029.csv"
    ep_starts = [
        datetime(2026, 10, 29, 16, 12, 45, tzinfo=timezone.utc),
        datetime(2026, 10, 29, 16, 42, 43, tzinfo=timezone.utc),
        datetime(2026, 10, 29, 18, 54, 4, tzinfo=timezone.utc),
        datetime(2026, 10, 29, 20, 6, 27, tzinfo=timezone.utc),
        datetime(2026, 10, 29, 20, 21, 11, tzinfo=timezone.utc),
    ]
    with open(dec_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=["timestamp", "symbol", "ai_action", "ai_confidence", "ai_model", "decision_source", "shooting_status", "shooting_reason", "exec_status", "ai_rationale", "reason"],
        )
        w.writeheader()
        for base in ep_starts:
            for k in range(3):
                w.writerow({
                    "timestamp": (base + timedelta(seconds=60 * k)).isoformat(),
                    "symbol": "XAUUSD",
                    "ai_action": "BUY",
                    "ai_confidence": "70",
                    "ai_model": "gemini-3.6-flash",
                    "decision_source": "AI",
                    "shooting_status": "GO",
                    "shooting_reason": "Shooting plan passed",
                    "exec_status": "DEFERRED",
                    "ai_rationale": "ok",
                    "reason": "deferred",
                })

    summary = xray_report._analyze_decisions_csv(dec_csv, 35.0)
    assert summary["shooting_go_episodes"] == 5
    assert summary["funnel"]["shooting_status_go"] == 15
    assert summary["funnel"]["a2_aggressive_fires"] == 5

    hygiene = xray_report._analyze_state_hygiene(tmp_path, "2026-10-29", summary, {})
    assert hygiene["drifts"] == []
    assert hygiene["states"]["power_memory_book_day"]["present"] is True
    assert hygiene["states"]["power_memory_book_day"]["entries"] == 2


def test_fix7_book_probe_cme_calibration_and_live_data_invariance(monkeypatch, tmp_path, capsys):
    """#7 + #12: book_probe defaults to CME thick-book 0.12 tilt (detecting 0.14 leans) and keeps legacy 0.25 comparison."""
    import tf_lens

    before_data = _dir_fingerprint(_REPO_ROOT / "data")
    before_logs = _dir_fingerprint(_REPO_ROOT / "logs")

    base = datetime(2026, 10, 29, 13, 0, 0, tzinfo=timezone.utc)
    fake_events = []
    fake_trades = []
    oid = 1000
    # 30 ten-second buckets: first 10 balanced (50/50 -> baseline), next 20 CME p90 lean (57/43 -> +0.14):
    for b_idx in range(30):
        t_b = base + timedelta(seconds=10 * b_idx)
        px = 4020.0 + (0.08 * max(0, b_idx - 10))
        fake_trades.append((t_b, px))
        b_sz, a_sz = (50.0, 50.0) if b_idx < 10 else (57.0, 43.0)
        oid += 1
        fake_events.append((t_b + timedelta(seconds=1), "BID_NEW", f"b{oid}", px - 0.1, b_sz))
        oid += 1
        fake_events.append((t_b + timedelta(seconds=2), "ASK_NEW", f"a{oid}", px + 0.1, a_sz))

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(book_probe, "read_day_mbo", lambda _d: (fake_events, None))
    monkeypatch.setattr(tf_lens, "read_day_trades", lambda _d: (fake_trades, None))

    # Default calibrated --tilt 0.12 catches the +0.14 CME thick-book lean:
    monkeypatch.setattr(sys, "argv", ["book_probe.py", "--date", "2026-10-29"])
    rc = book_probe.main()
    out = capsys.readouterr().out
    assert rc in (None, 0)
    assert "lean threshold 0.12" in out
    assert "book leans: 1 sustained" in out
    assert "CME thick-book calibrated @ tilt=0.12 (vs legacy 0.25 print-spy yardstick: 0 lean(s))" in out

    # Explicit --tilt 0.25 still shows honest zero + p90 companion note:
    monkeypatch.setattr(sys, "argv", ["book_probe.py", "--date", "2026-10-29", "--tilt", "0.25"])
    rc2 = book_probe.main()
    out2 = capsys.readouterr().out
    assert rc2 in (None, 0)
    assert "honest zero" in out2
    assert "CME thick-book companion @ p90=" in out2

    assert _dir_fingerprint(_REPO_ROOT / "data") == before_data
    assert _dir_fingerprint(_REPO_ROOT / "logs") == before_logs


def test_fix8_part_c_judges_c1_c2_cut_and_chop_deadbands(monkeypatch):
    """#9: C1 whale_walls & C2 htf_poc retired from directional voting (even with pre-Oct-29 .env RETIRED_JUDGES) while remaining map doors; chop deadbands active."""
    import judge_families
    import judge_panel as jp_mod
    import step2_market_analysis as s2

    assert "whale_walls" in config.RETIRED_JUDGES
    assert "htf_poc" in config.RETIRED_JUDGES
    assert "whale_walls" in judge_families.RETIRED
    assert "htf_poc" in judge_families.RETIRED

    # Simulate an older local .env that had RETIRED_JUDGES before C2 htf_poc matured:
    try:
        monkeypatch.setenv(
            "RETIRED_JUDGES",
            "macro_yield,macro_dxy,macro_vix,vwap_zscore,spoof_invert_loose,iceberg_legacy,l3_ofi_streak,delta_pressure,whale_walls",
        )
        monkeypatch.delenv("UNRETIRE_JUDGES", raising=False)
        config._refresh()
        assert "whale_walls" in config.RETIRED_JUDGES
        assert "htf_poc" in config.RETIRED_JUDGES

        # Explicit UNRETIRE_JUDGES still works if the owner intentionally re-enables one:
        monkeypatch.setenv("UNRETIRE_JUDGES", "htf_poc")
        config._refresh()
        assert "htf_poc" not in config.RETIRED_JUDGES

        # Verify clean code-level defaults independent of any owner .env weight overrides:
        for k in (
            "L3_IMB_DEADBAND",
            "FOOTPRINT_DELTA_DEADBAND",
            "VOLUME_ROC_MIN_PCT",
            "SIGNAL_W_TREND",
            "SIGNAL_W_L3_OFI",
            "V6_VWAP_BAND_1SIG_WEIGHT",
        ):
            monkeypatch.delenv(k, raising=False)
        config._refresh()
        assert config.L3_IMB_DEADBAND == pytest.approx(0.12)
        assert config.FOOTPRINT_DELTA_DEADBAND == pytest.approx(0.08)
        assert config.VOLUME_ROC_MIN_PCT == pytest.approx(0.35)
        assert config.SIGNAL_W_TREND == pytest.approx(1.0)
        assert config.SIGNAL_W_L3_OFI == pytest.approx(0.8)
        assert config.V6_VWAP_BAND_1SIG_WEIGHT == pytest.approx(0.8)
    finally:
        monkeypatch.undo()
        config._refresh()

    # Panel parsers exclude retired C1 whale_walls and C2 htf_poc directional votes:
    notes = [
        "L3 whale SUPPORT 3 walls 350 lots closest 4018.0 dist 0.05% w 2.0 -> BUY",
        "HTF H1 POC 4015.0 below price 4018.5 (dist +0.80 ATR) w 0.7 -> BUY magnet",
        "L3 NET FLOW BUY +120 (buys 210 vs sells 90) w 1.5 -> BUY",
    ]
    for parser in (s2.parse_judge_panel, jp_mod.parse_judge_panel):
        panel = parser(notes)
        names = [r["judge"] for r in panel]
        assert "whale_walls" not in names
        assert "htf_poc" not in names
        assert names == ["l3_net_flow"]


def test_fix9_part_c_power_flat_lake_guard_and_shooting_class_floor(monkeypatch):
    """#10: Multi-Day Evidence Law — Flat-Lake ER_10 & Shooting class gates default to SHADOW_ONLY, and enforce when explicitly enabled."""
    import power_v2_regime
    import shooting_team

    # Default config must be SHADOW_ONLY (live gates disabled so Oct 27 trend & coil breakouts are never blocked):
    assert config.POWER_FLAT_LAKE_GUARD_ENABLED is False
    assert config.SHOOT_MIN_CLASS_CONFIDENCE == pytest.approx(0.0)
    assert config.SHOOT_BLOCK_CLASS_OPPOSITION is False
    assert config.SHOOT_CLASS_SHADOW_LOG is True

    power_v2_regime.set_bar_seconds(180)
    t_end = datetime(2026, 10, 29, 18, 0, 0, tzinfo=timezone.utc)
    bars = []
    start0 = int(t_end.timestamp()) - 28 * 180
    px = 4000.0
    for i in range(28):
        b_start = start0 + i * 180
        if i < 18:
            nxt = px + 0.55
            bars.append({"start": b_start, "open": px, "high": nxt + 0.1, "low": px - 0.05, "close": nxt, "bar_seconds": 180})
            px = nxt
        else:
            step = 1.6 if (i % 2 == 0) else -1.6
            nxt = px + step
            hi = max(px, nxt) + 0.1
            lo = min(px, nxt) - 0.1
            bars.append({"start": b_start, "open": px, "high": hi, "low": lo, "close": nxt, "bar_seconds": 180})
            px = nxt
    live_ticks = [{"timestamp": t_end + timedelta(seconds=1), "price": px}]

    # 1) Default SHADOW mode: records shadow_flat_lake=True while preserving TREND
    reg_shadow = power_v2_regime.classify_m5_regime(live_ticks, now=t_end + timedelta(seconds=5), history_bars=bars)
    if reg_shadow["adx"] is not None and 25.0 <= reg_shadow["adx"] < 31.0:
        assert reg_shadow["efficiency_ratio_10"] < 0.22
        assert reg_shadow["shadow_flat_lake"] is True
        assert reg_shadow["market_regime"] == "TREND"

    # 2) Explicit opt-in mode: downgrades borderline ADX splash to RANGE
    monkeypatch.setattr(config, "POWER_FLAT_LAKE_GUARD_ENABLED", True)
    monkeypatch.setattr(config, "POWER_FLAT_LAKE_ER_MIN", 0.22)
    reg_live = power_v2_regime.classify_m5_regime(live_ticks, now=t_end + timedelta(seconds=5), history_bars=bars)
    if reg_live["adx"] is not None and 25.0 <= reg_live["adx"] < 31.0:
        assert reg_live["market_regime"] == "RANGE"
        assert reg_live["reason"] == "FLAT_LAKE_LOW_EFFICIENCY"
    power_v2_regime.set_bar_seconds(300)

    # Shooting: default SHADOW mode allows GO while logging class_gate_shadow in shadow_candidates:
    monkeypatch.setattr(config, "SHOOT_ROAD_HISTORY_MIN", 0)
    smap = {"above": [{"price": 4030.0, "size": 25.0, "doors_between": 0, "lots_between": 0.0, "road_clear": True}]}
    pwr = {
        "team": "POWER", "version": "2.2.0", "direction": "UP",
        "up_power_pct": 82.0, "down_power_pct": 18.0, "power_total_pct": 100.0,
        "reason_code": "UPWARD_FORCE_DOMINATES",
        "context": {"market_regime": "TREND", "data_quality_ok": True, "feed_stale": False},
        "regime_diagnostics": {"market_regime": "TREND", "latest_tick_age_seconds": 2.0},
    }
    base_ctx = {
        "bid": 4020.0, "ask": 4020.2, "news_state": "QUIET", "has_data": True,
        "data_quality_ok": True, "last_data_age_seconds": 1.0,
        "recent_volume": 250.0, "trade_lots": 0.1,
        "aggressive_trigger_confirmed": True, "aggressive_trigger_reason": "A2 burst", "a2_burst": True,
    }
    sh_go = shooting_team.plan_shot(
        smap, pwr, 4020.1, 2.0, spread=0.2, config=config,
        entry_context={**base_ctx, "signal_confidence": 14.0, "signal_direction": "NEUTRAL"},
    )
    assert sh_go["shot"] == "GO"
    cgs = sh_go["visibility"]["shadow_candidates"]["class_gate_shadow"]
    assert cgs["mode"] == "SHADOW_ONLY"
    assert cgs["would_block_low_conf"] is True

    # Explicit opt-in mode enforces both floor and opposition veto:
    monkeypatch.setattr(config, "SHOOT_MIN_CLASS_CONFIDENCE", 25.0)
    monkeypatch.setattr(config, "SHOOT_BLOCK_CLASS_OPPOSITION", True)
    w_low = shooting_team.plan_shot(
        smap, pwr, 4020.1, 2.0, spread=0.2, config=config,
        entry_context={**base_ctx, "signal_confidence": 14.0, "signal_direction": "NEUTRAL"},
    )
    assert w_low["shot"] == "WAIT"
    assert "minimum floor" in w_low["visibility"]["primary_reason"]

    w_opp = shooting_team.plan_shot(
        smap, pwr, 4020.1, 2.0, spread=0.2, config=config,
        entry_context={**base_ctx, "signal_confidence": 42.0, "signal_direction": "SELL"},
    )
    assert w_opp["shot"] == "WAIT"
    assert "classroom opposes POWER" in w_opp["visibility"]["primary_reason"]


def test_fix10_part_c_c3_wall_exits_early_trail_and_1pt_be(monkeypatch):
    """#11: PositionManager runs C3 wall shelter/opposing-TP front-run, +0.5R early trail, and +1.0pt small-fish BE."""
    monkeypatch.setattr(config, "PM_C3_WALL_TRAIL_ENABLED", True)
    monkeypatch.setattr(config, "PM_C3_EARLY_TRAIL_R", 0.50)
    monkeypatch.setattr(config, "PM_C3_TRAIL_ATR", 0.50)
    monkeypatch.setattr(config, "PM_BURST_QUICK_BE_PTS", 1.0)
    monkeypatch.setattr(config, "PM_BURST_MODIFY_COOLDOWN_SECONDS", 20)

    pm = position_manager.PositionManager.__new__(position_manager.PositionManager)
    pm._mt5 = SimpleNamespace(
        POSITION_TYPE_BUY=0,
        POSITION_TYPE_SELL=1,
        symbol_info_tick=lambda _s: SimpleNamespace(bid=4021.10, ask=4021.30),
        symbol_info=lambda _s: SimpleNamespace(point=0.01, trade_stops_level=5, digits=2),
    )
    pm._hist = []
    pm._state = {
        "974999": {
            "initial_sl": 4018.20,
            "be_done": False,
            "adopted_ts": time.time() - 90.0,
            "last_modify_ts": 0.0,
            "is_a2_burst": True,
            "high_water_pts": 1.20,
        }
    }
    pm._save_state = lambda: None
    pm._journal = lambda _d: None
    mods = []
    pm._modify_stops = lambda pos, nsl, ntp, rule, reason, ctx: mods.append((nsl, ntp, rule, reason))

    pos = SimpleNamespace(
        ticket=974999, symbol="XAUUSD", type=0, price_open=4020.00,
        sl=4018.20, tp=4023.60, volume=0.01, profit=1.10, comment="gold-bot A2 burst",
    )
    snap = SimpleNamespace(
        price=4021.20, bid=4021.10, ask=4021.30, data_symbol="XAUUSD", trade_symbol="XAUUSD",
        volatility=SimpleNamespace(atr=1.60, volatility_rank=0.4),
        order_flow=SimpleNamespace(delta=15.0, buying_pressure=62.0, cvd=40.0),
        volume_profile=SimpleNamespace(vwap=4019.50, vwap_zscore=0.6, poc=4019.80, value_area_high=4022.5, value_area_low=4018.0),
        regime="TREND", signal_direction="BUY", signal_strength=55.0, divergence=0.0,
        news=SimpleNamespace(news_state="QUIET", impact_level="LOW", minutes_to_next_event=999),
        # Fresh opposing wall at 4022.40 (18 lots) closer than 4023.60 TP!
        signal_map={
            "above": [{"price": 4022.40, "size": 18.0}],
            "below": [{"price": 4019.50, "size": 15.0}],
        },
        shot={"side": "BUY", "stop_source": "A2_BURST_FIXED"},
        order_blocks=[], footprint=None, htf_poc={}, macro_bias=0.0,
    )
    tick = SimpleNamespace(bid=4021.10, ask=4021.30)
    info = SimpleNamespace(point=0.01, trade_stops_level=5, digits=2)
    pm._manage_one(pos, snap, tick, info)
    assert len(mods) == 1
    new_sl, new_tp, rule, reason = mods[0]
    # Gain +1.20 pts (>= +1.0pt AND >= 0.5R of 1.80) -> SL locked above entry (>= 4020.20), TP pulled in front of 4022.40 wall!
    assert new_sl >= 4020.20, f"Expected break-even/trail >= 4020.20, got {new_sl}"
    assert new_tp < 4022.40, f"Expected opposing wall front-run < 4022.40, got {new_tp}"
    assert "BookMap wall" in reason
