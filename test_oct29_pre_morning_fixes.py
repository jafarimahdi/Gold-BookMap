"""Regression tests for the 2026-10-29 pre-morning-run bug fixes (#1-#5 and #8)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import aggressive_trigger as at
import config
import judge_panel
import main
import position_manager as pm_mod
import risk_manager as rm_mod
import step2_market_analysis as step2
import step4_mt5_execution as step4
import trade_history
from tools import demo_order_smoke, xray_report

_REPO_ROOT = Path(__file__).resolve().parent


def _dir_fingerprint(folder: Path) -> dict[str, tuple[int, int]]:
    if not folder.exists():
        return {}
    out: dict[str, tuple[int, int]] = {}
    for p in sorted(folder.rglob("*")):
        if p.is_file():
            st = p.stat()
            out[str(p.relative_to(folder))] = (st.st_size, st.st_mtime_ns)
    return out


def test_mt5_broker_server_clock_offset_and_stale_lock(monkeypatch):
    """#1: GMT+2/GMT+3 broker server ticks pass freshness, lock offset against 1h stale drift, and keep +1s future guard."""
    monkeypatch.setattr(config, "MAX_SPREAD_PCT", 0.05, raising=False)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 300.0, raising=False)
    monkeypatch.setattr(config, "MT5_SERVER_TZ_OFFSET_HOURS", "auto", raising=False)

    now = 1_793_268_000.0
    ex = step4.MT5Executor("XAUUSD")

    # 1) Live Pepperstone GMT+3 (+10,800s) tick that is 0.25s old
    gmt3_fresh_ep = now + 10_800.0 - 0.25
    tick_gmt3 = SimpleNamespace(
        bid=4035.10,
        ask=4035.30,
        time=int(gmt3_fresh_ep),
        time_msc=int(round(gmt3_fresh_ep * 1000)),
    )
    ok, reason, spread, age = ex._broker_quote_check(tick_gmt3, now_epoch=now)
    assert ok is True and reason == "ok"
    assert age == pytest.approx(0.25, abs=0.01)

    # 2) Same executor later sees a 1-hour-stale (3,600s old) GMT+3 tick:
    # must NOT slip from k=3 (+10800s) to k=2 (+7200s); must fail closed as stale!
    gmt3_1h_stale_ep = (now + 10_800.0) - 3_600.0
    tick_1h_stale = SimpleNamespace(
        bid=4035.10,
        ask=4035.30,
        time=int(gmt3_1h_stale_ep),
        time_msc=int(round(gmt3_1h_stale_ep * 1000)),
    )
    ok_stale, reason_stale, _, age_stale = ex._broker_quote_check(tick_1h_stale, now_epoch=now)
    assert ok_stale is False
    assert "stale" in reason_stale
    assert age_stale == pytest.approx(3600.0, abs=0.05)

    # 3) Fresh executor still rejects uncalibrated +1.0s bogus future timestamp
    ex_fresh = step4.MT5Executor("XAUUSD")
    bogus_future = SimpleNamespace(
        bid=4035.10,
        ask=4035.30,
        time=int(now + 1),
        time_msc=int((now + 1.0) * 1000),
    )
    ok_fut, reason_fut, _, _ = ex_fresh._broker_quote_check(bogus_future, now_epoch=now)
    assert ok_fut is False and "future-dated" in reason_fut

    # 4) Windows +3ms sub-millisecond clock rounding clamps cleanly to 0.0
    win_jitter = SimpleNamespace(
        bid=4035.10,
        ask=4035.30,
        time=int(now),
        time_msc=int(round((now + 0.003) * 1000)),
    )
    ok_jit, _, _, age_jit = ex_fresh._broker_quote_check(win_jitter, now_epoch=now)
    assert ok_jit is True and age_jit == 0.0

    # 5) demo_order_smoke._quote_age also accepts GMT+3 live tick and rejects +1s future tick
    smoke_age = demo_order_smoke._quote_age(tick_gmt3, now)
    assert smoke_age == pytest.approx(0.25, abs=0.01)
    with pytest.raises(demo_order_smoke.SmokeTestError, match="future-dated"):
        demo_order_smoke._quote_age(bogus_future, now)


def test_risk_manager_daily_pnl_counts_gmt3_deals_immediately(monkeypatch, tmp_path):
    """#1b: Closed deals stamped in GMT+3 broker server time count immediately in daily_pnl()."""
    now_dt = datetime(2026, 10, 29, 10, 0, 0, tzinfo=timezone.utc)
    now_ep = now_dt.timestamp()
    # Closed 5 seconds ago on a GMT+3 (+10,800s) Pepperstone server
    gmt3_deal_ep = (now_ep - 5.0) + 10_800.0
    gmt3_tick_ep = (now_ep - 0.1) + 10_800.0

    pos_file = tmp_path / "tracked_bot_positions.json"
    pos_file.write_text(json.dumps({"position_ids": [88001122]}), encoding="utf-8")
    monkeypatch.setattr(rm_mod, "POSITION_IDS_FILE", pos_file)
    monkeypatch.setattr(config, "MT5_SERVER_TZ_OFFSET_HOURS", "auto", raising=False)

    fake_deal = SimpleNamespace(
        ticket=55001,
        position_id=88001122,
        symbol="XAUUSD",
        magic=234000,
        profit=-24.50,
        swap=0.0,
        commission=-1.00,
        fee=0.0,
        time=int(gmt3_deal_ep),
        time_msc=int(gmt3_deal_ep * 1000),
    )
    range_calls = []

    def _fake_deals(*args, **kwargs):
        if args:
            range_calls.append(args)
        return [fake_deal]

    fake_mt5 = SimpleNamespace(
        symbol_info_tick=lambda _sym: SimpleNamespace(
            bid=4035.0,
            ask=4035.2,
            time=int(gmt3_tick_ep),
            time_msc=int(gmt3_tick_ep * 1000),
        ),
        history_deals_get=_fake_deals,
        shutdown=lambda: None,
    )
    monkeypatch.setattr(rm_mod, "mt5", fake_mt5)
    monkeypatch.setattr(config, "mt5_initialize", lambda _m: True)

    pnl = rm_mod.RiskManager().daily_pnl(now=now_dt)
    assert pnl == pytest.approx(-25.50)

    # Also verify date-range fallback shifts query_end forward by +3h when tracked IDs are empty
    pos_file.write_text(json.dumps({"position_ids": []}), encoding="utf-8")
    pnl_fallback = rm_mod.RiskManager().daily_pnl(now=now_dt)
    assert pnl_fallback == pytest.approx(-25.50)
    assert len(range_calls) == 1
    assert range_calls[0][1] == now_dt + timedelta(hours=3)


def test_step4_and_pm_quantize_sl_tp_to_broker_digits(monkeypatch):
    """#2: A2 burst & PM SL/TP values are quantized to broker trade_tick_size/digits before order_check/order_send."""
    now = time.time()
    sent_requests = []
    checked_requests = []

    info = SimpleNamespace(
        digits=2,
        point=0.01,
        trade_tick_size=0.01,
        trade_stops_level=5,
        volume_min=0.01,
        volume_max=10.0,
        volume_step=0.01,
        filling_mode=2,
    )
    fake_pos = SimpleNamespace(ticket=9001, magic=234000, type=0, volume=0.01, price_open=4035.12)
    fake_mt5 = SimpleNamespace(
        POSITION_TYPE_BUY=0,
        POSITION_TYPE_SELL=1,
        ORDER_TYPE_BUY=0,
        ORDER_TYPE_SELL=1,
        TRADE_ACTION_DEAL=1,
        TRADE_ACTION_SLTP=6,
        ORDER_TIME_GTC=0,
        ORDER_FILLING_IOC=1,
        TRADE_RETCODE_DONE=10009,
        symbol_info_tick=lambda _s: SimpleNamespace(
            bid=4034.92, ask=4035.12, time=int(now), time_msc=int(now * 1000)
        ),
        symbol_info=lambda _s: info,
        account_info=lambda: SimpleNamespace(equity=10000.0, margin_free=9500.0, trade_mode=0),
        order_calc_margin=lambda *_a: 80.0,
        positions_get=lambda **_kw: ([] if not sent_requests else [fake_pos]),
        order_check=lambda req: (checked_requests.append(dict(req)) or SimpleNamespace(retcode=0)),
        order_send=lambda req: (
            sent_requests.append(dict(req))
            or SimpleNamespace(retcode=10009, order=9001, deal=7001, price=4035.12)
        ),
        last_error=lambda: (0, "ok"),
        shutdown=lambda: None,
    )
    monkeypatch.setattr(step4, "mt5", fake_mt5)
    monkeypatch.setattr(config, "LIMIT_ORDER_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "A2_BURST_SL_PTS", 1.8, raising=False)
    monkeypatch.setattr(config, "A2_BURST_TP_PTS", 1.8, raising=False)
    monkeypatch.setattr(config, "A2_MIN_RR", 1.0, raising=False)

    snap = SimpleNamespace(
        price=4074.40,  # futures price -> non-terminating ratio 4035.12 / 4074.40
        bid=4074.30,
        ask=4074.50,
        signal_strength=42.0,
        regime="TREND",
        volatility=SimpleNamespace(atr=2.4, volatility_rank=0.5),
        news=SimpleNamespace(news_state="QUIET", minutes_to_next_event=999),
        shot={"shot": "GO", "side": "BUY", "stop_source": "A2_BURST_FIXED"},
        notes=[],
    )
    ex = step4.MT5Executor("XAUUSD")
    res = ex._place_order("BUY", snap, "QUIET", ai_confidence=65.0)
    assert res.status == "EXECUTED"
    assert len(checked_requests) == 1 and len(sent_requests) == 1
    sent = sent_requests[0]
    assert sent["sl"] == round(sent["sl"], 2)
    assert sent["tp"] == round(sent["tp"], 2)
    assert f"{sent['sl']:.10f}".endswith("00000000")
    assert f"{sent['tp']:.10f}".endswith("00000000")

    # PositionManager._modify_stops also quantizes SL/TP
    pm_sent = []
    pm_mt5 = SimpleNamespace(
        TRADE_ACTION_SLTP=6,
        TRADE_RETCODE_DONE=10009,
        order_send=lambda req: (pm_sent.append(dict(req)) or SimpleNamespace(retcode=10009)),
    )
    mgr = pm_mod.PositionManager()
    monkeypatch.setattr(mgr, "_mt5", pm_mt5)
    ctx = {
        "side": "BUY",
        "price": 4036.50,
        "profit": 1.38,
        "r_now": 0.8,
        "cur_sl": 4033.34,
        "cur_tp": 4036.90,
        "tick": SimpleNamespace(bid=4036.50, ask=4036.70),
        "info": info,
    }
    ok_mod = mgr._modify_stops(fake_pos, 4034.1287654, 4038.9912345, "TRAIL", "test", ctx)
    assert ok_mod is True
    assert pm_sent[0]["sl"] == 4034.13
    assert pm_sent[0]["tp"] == 4038.99


def test_flash_crash_min_floor_and_adverse_only_protection(monkeypatch):
    """#3: Normal $3.80 candle does NOT trigger flash crash; real $18 shock keeps favorable trade and flattens adverse trade."""
    monkeypatch.setattr(main, "_PRICE_HISTORY", [])
    monkeypatch.setattr(config, "FLASH_CRASH_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "FLASH_CRASH_ATR_MULT", 3.0, raising=False)
    monkeypatch.setattr(config, "FLASH_CRASH_MINUTES", 1.0, raising=False)
    monkeypatch.setattr(config, "FLASH_CRASH_MIN_PTS", 12.0, raising=False)
    monkeypatch.setattr(config, "FLASH_CRASH_MIN_PCT", 0.25, raising=False)

    s0 = SimpleNamespace(price=4053.10, volatility=SimpleNamespace(atr=1.18))
    s_small = SimpleNamespace(price=4056.90, volatility=SimpleNamespace(atr=1.18))  # +3.80 pts (0.09%)
    assert main._check_flash_crash(s0) == ""
    assert main._check_flash_crash(s_small) == ""  # must NOT false-fire on 3.80 pts!

    # Real +$18.00 spike -> triggers flash-crash string with signed move +18.00
    s_real = SimpleNamespace(price=4071.10, volatility=SimpleNamespace(atr=1.18))
    reason = main._check_flash_crash(s_real)
    assert "FLASH CRASH" in reason and "+18.00" in reason


def test_a2_deferred_commit_preserves_counter_on_wait_and_isolates_live_data(monkeypatch):
    """#4: a2_v1_gate(defer_commit=True) does not burn count/cooldown on WAIT, commits on GO, and never mutates live data/."""
    live_data_before = _dir_fingerprint(_REPO_ROOT / "data")
    live_logs_before = _dir_fingerprint(_REPO_ROOT / "logs")

    saved_eyes = at.EYES
    at.EYES = at.BurstEyes()
    try:
        monkeypatch.setenv("AGGRESSIVE_ENTRY_ENABLED", "1")
        cfg = SimpleNamespace(
            DATA_DIR=config.DATA_DIR,
            A2_LEAN_MIN=0.15,
            A2_LEAN_HOLD_SECONDS=30.0,
            A2_PANEL_WINDOW_SECONDS=300.0,
            A2_BURST_MAX_PER_DAY=100,
            A2_BURST_COOLDOWN_MINUTES=5.0,
        )
        t0 = datetime(2026, 10, 29, 9, 0, 0, tzinfo=timezone.utc)
        for dt_off in (-40, -20, -5):
            at.EYES.sample(t0 + timedelta(seconds=dt_off), 0.30)
        at.EYES.remember_panel(t0 - timedelta(seconds=10), "BUY")

        v1_ok = {"confirmed": True, "reason": "v1 impulse ok"}
        notes = []
        out_deferred = at.a2_v1_gate(
            v1_ok,
            {"direction": "UP"},
            {"buying_pressure": 65.0, "selling_pressure": 35.0},
            "BUY",
            t0,
            cfg,
            notes,
            defer_commit=True,
        )
        assert out_deferred["aggressive_trigger_confirmed"] is True
        assert out_deferred["a2_burst"] is True
        assert out_deferred.get("_a2_pending_commit") is True
        # When plan_shot returns WAIT, we do NOT call a2_commit_fire -> count stays 0 and cooldown stays None!
        assert at.a2_fire_count(t0, config=cfg) == 0
        assert at.EYES.last_fire is None

        # Next cycle 30s later: plan_shot approves GO -> a2_commit_fire commits count=1 and stamps cooldown!
        t1 = t0 + timedelta(seconds=30)
        committed_reason = at.a2_commit_fire(out_deferred["_a2_commit_meta"], t1, config=cfg, notes=notes)
        assert "FIRE 1/100" in committed_reason
        assert at.a2_fire_count(t1, config=cfg) == 1
        assert at.EYES.last_fire == t1
        assert any("A2 burst: FIRE" in n for n in notes)
    finally:
        at.EYES = saved_eyes

    assert _dir_fingerprint(_REPO_ROOT / "data") == live_data_before
    assert _dir_fingerprint(_REPO_ROOT / "logs") == live_logs_before


def test_cme_mbo_replace_iceberg_and_queue_pos_vote(monkeypatch):
    """#5: CME Rithmic REPLACE display replenishments trigger iceberg levels/meta, and QUEUE_POS votes cleanly."""
    monkeypatch.setattr(config, "ICEBERG_MIN_REFILLS", 3, raising=False)
    monkeypatch.setattr(config, "ICEBERG_SAME_PRICE_TOL", 0.10, raising=False)

    l3 = step2.Level3OrderBookAnalyzer(large_size=25.0)
    t_base = 1_793_268_000.0
    # 1) Resting CME display order added via BID_NEW (oid=900100, size=5 @ 4050.0)
    l3.process_order_event(
        {"type": "BID_NEW", "side": "BID", "price": 4050.0, "size": 5.0, "order_id": "900100", "timestamp": t_base}
    )
    # 2) Three slice-depletion + CME REPLACE replenishment cycles on SAME order_id
    for i in range(1, 4):
        l3.process_order_event(
            {
                "type": "REPLACE",
                "side": "BID",
                "price": 4050.0,
                "size": 1.0,
                "old_size": 5.0,
                "order_id": "900100",
                "timestamp": t_base + i * 6.0,
            }
        )
        l3.process_order_event(
            {
                "type": "REPLACE",
                "side": "BID",
                "price": 4050.0,
                "size": 5.0,
                "old_size": 1.0,
                "order_id": "900100",
                "timestamp": t_base + i * 6.0 + 2.0,
            }
        )

    ev = l3.analyze()
    assert ev.iceberg_events >= 1
    assert ev.iceberg_levels.get(4050.0, 0) >= 1
    assert 4050.0 in ev.iceberg_meta
    assert ev.iceberg_meta[4050.0]["refills"] >= 1

    # Verify judge_panel parses QUEUE_POS note cleanly
    parsed = judge_panel.parse_judge_panel(["QUEUE_POS good bid ratio 1.45 + whale support -> BUY queue"])
    assert any(j["judge"] == "queue_pos" and j["dir"] > 0 for j in parsed)


def test_xray_log_formatter_and_trade_memory_pnl_usd_est(tmp_path):
    """#8: X-Ray parses main.py '%(levelname)-7s [%(name)s]' logs and trade_memory.json 'pnl_usd_est'."""
    logs_dir = tmp_path / "logs"
    data_dir = tmp_path / "data"
    logs_dir.mkdir()
    data_dir.mkdir()

    (logs_dir / "trading_20261028.log").write_text(
        "2026-10-28 10:00:01 INFO    [main] loop iteration 1\n"
        "2026-10-28 16:54:30 ERROR   [main] L4 KILL SWITCH TRIGGERED: test\n"
        "2026-10-28 16:55:00 WARNING [step3_ai_decision] STEP 3: attempt slice (10.0s) reached\n",
        encoding="utf-8",
    )
    (data_dir / "trade_memory.json").write_text(
        json.dumps(
            {
                "closed": [
                    {"ticket": 101, "closed_utc": "2026-10-28T14:20:00+00:00", "pnl_usd_est": 14.25},
                    {"ticket": 102, "closed_utc": "2026-10-27T15:00:00+00:00", "pnl_usd_est": -4.00},
                ]
            }
        ),
        encoding="utf-8",
    )

    log_digest = xray_report._analyze_logs(tmp_path, "2026-10-28")
    assert log_digest["error_count"] == 1
    assert log_digest["warning_count"] == 1

    organ_digest = xray_report._analyze_diary_and_execution_organs(tmp_path, "2026-10-28")
    assert organ_digest["trades_closed_today"] == 1
    assert organ_digest["trades_today_net_pnl"] == pytest.approx(14.25)
    assert organ_digest["trades_rolling_net_pnl"] == pytest.approx(10.25)
