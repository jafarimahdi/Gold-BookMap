"""Regression tests for 2026-10-30 Weekend X-Ray Forensic Audit Fixes (B1-B7).

Covers:
  B1: Step 3 _filter_active_judges_and_families reads judge_panel 'dir' key + trend_macd/sma20 MAP family.
  B2: CSV shadow_candidates non-mutating compaction (-80%+ bloat), news/macro stale-while-revalidate
      non-blocking refresh, and settled tracked_position_ids skipping redundant MT5 IPC queries.
  B3: CME Globex Friday 17:00 America/New_York weekend close in session.py + post-close audit hygiene.
  B4: Retired judges bench separation across judge_panel, judge_report_card, audit_day, signal_health, xray_report.
  B5: FootprintBuilder slices timestamped tick_data to ORDER_FLOW_WINDOW_SECONDS (180s) instead of full 3h lake.
  B6: BookmapBridgeProvider skips old closed rotated ticks/mbo chunks via mtime and caches ts_iso on append.
  B7: OrderFlowAnalyzer._detect_divergence scans full time window across high-print minutes and never falls
      back to comparing 1-minute price against 3-hour cumulative delta.
"""
from __future__ import annotations

import csv
import gzip
import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import audit_day
import bookmap_bridge_provider as bbp
import config
import judge_families
import judge_panel
import judge_report_card
import macro
import main
import news
import session
import step2_market_analysis as s2
import step3_ai_decision as s3
from tools import xray_report


def test_b1_step3_reads_judge_panel_dir_key_and_family_consensus(monkeypatch):
    """B1: parse_judge_panel emits 'dir' (-1.0/+1.0); Step 3 must read 'dir' (never zeroing to 0)."""
    monkeypatch.setattr(config, "reload_env", lambda *a, **k: None)
    monkeypatch.setattr(
        config,
        "RETIRED_JUDGES",
        ("poc_day", "supply_demand", "microprice", "queue_pos", "vwap_zscore", "mtf"),
        raising=False,
    )

    assert judge_families.JUDGE_FAMILY["trend_macd"] == "MAP"
    assert judge_families.JUDGE_FAMILY["sma20"] == "MAP"

    notes = [
        "footprint delta -42.0 dominant 3980.0 strength 0.85 -> SELL",
        "L3 NET FLOW SELL -18.5 (buys 12.0 vs sells 30.5) w 1.5",
        "ICEBERG_RESISTANCE @ 3982.5 refills 4 w 1.2 -> SELL",
        "ICEBERG weak support @ 3979.0 (1 refills) -> no vote",
        "v6.0 VWAP +2.0 -> SELL",
        "trend DOWN MACD -1.2 -> SELL",
        "SMA20 slope down -> SELL",
        "POC day 3990.0 price 3980.0 -> below",  # retired -> must not enter Step 3 active panel
    ]
    panel = judge_panel.parse_judge_panel(notes)
    by_name = {row["judge"]: row for row in panel}
    assert by_name["poc_day"].get("retired") is True
    assert "retired" not in by_name["footprint_delta"]

    active_votes, fam_summary = s3.AIDecisionEngine._filter_active_judges_and_families(panel)
    active_by_name = {row["judge"]: row for row in active_votes}
    assert "poc_day" not in active_by_name
    assert "iceberg_noise" not in active_by_name
    assert active_by_name["footprint_delta"]["vote"] == -1
    assert active_by_name["l3_net_flow"]["vote"] == -1
    assert active_by_name["iceberg"]["vote"] == -1
    assert active_by_name["vwap_bands"]["vote"] == -1
    assert active_by_name["trend_macd"]["family"] == "MAP"
    assert active_by_name["sma20"]["family"] == "MAP"
    assert fam_summary["agreeing_families_sell"] >= 4


def test_b2a_csv_shadow_candidates_compaction_non_mutating(monkeypatch):
    """B2a: executable shadows keep full market_quality_evidence in memory; CSV copy shrinks >75%."""
    monkeypatch.setattr(config, "reload_env", lambda *a, **k: None)
    big_l2 = {
        "status": "OK",
        "bids": [{"price": 3980.0 - 0.1 * i, "volume": 15 + i} for i in range(20)],
        "asks": [{"price": 3980.1 + 0.1 * i, "volume": 15 + i} for i in range(20)],
    }
    evidence = {
        "captured_at": "2026-10-30T15:00:00+00:00",
        "data_source": "bookmap_bridge",
        "data_symbol": "GCZ6.CME@RITHMIC",
        "trade_symbol": "XAUUSD",
        "quote_status": "LIVE",
        "level2_status": "LIVE",
        "safety_gate_status": "READY",
        "safety_gate_reason": "OK",
        "price": 3980.0,
        "bid": 3979.9,
        "ask": 3980.1,
        "spread_pct": 0.005,
        "feed_age_seconds_at_decision": 0.4,
        "quote_age_seconds_at_decision": 0.4,
        "depth_age_seconds_at_decision": 0.2,
        "feed_warnings": [],
        "l2_diagnostics": big_l2,
    }
    passive_cand = {"route": "passive_limit", "action": "SELL", "eligible": True}
    power_cand = {
        "route": "power_lead",
        "action": "SELL",
        "passive_route_candidate": dict(passive_cand),
    }
    snap = SimpleNamespace(
        quality_evidence=evidence,
        shadow_candidates={
            "power_lead": power_cand,
            "passive_limit": passive_cand,
            "family_gate": {"active_families": 4, "would_block": False},
            "l3_passive_mbo_shadow": {"net_passive": -12.0, "would_block": False},
            "class_gate_shadow": {"trade_class": "A_TREND_FLOW", "would_block": False},
            "fresh_wave_shadow": {"climax_exhaustion": False, "would_block": False},
            "ai_neutral_panel_veto": {"would_veto": False},
        },
        shot={"shadow_candidates": {"passive_limit": passive_cand}},
    )

    main._attach_quality_evidence_to_shadow_candidates(snap)
    # Executable candidates retain full in-memory evidence contract:
    assert snap.shadow_candidates["power_lead"]["market_quality_evidence"] == evidence
    assert snap.shadow_candidates["passive_limit"]["market_quality_evidence"] == evidence
    # Telemetry-only Law #5 shadows are not bloated with 4KB L2 copies:
    assert "market_quality_evidence" not in snap.shadow_candidates["class_gate_shadow"]
    assert "market_quality_evidence" not in snap.shadow_candidates["family_gate"]

    # Simulate legacy/nested case where nested passive_route_candidate also had full evidence:
    snap.shadow_candidates["power_lead"]["passive_route_candidate"]["market_quality_evidence"] = dict(evidence)
    raw_json = json.dumps(snap.shadow_candidates, separators=(",", ":"))
    compact_dict = main._compact_shadow_candidates_for_csv(snap.shadow_candidates, evidence)
    compact_json = json.dumps(compact_dict, separators=(",", ":"))

    assert len(compact_json) < len(raw_json) * 0.30
    assert compact_dict["market_quality_evidence"]["safety_gate_status"] == "READY"
    assert compact_dict["market_quality_evidence"]["l2_status"] == "OK"
    # In-memory snapshot.shadow_candidates must remain unmutated:
    assert "l2_diagnostics" in snap.shadow_candidates["power_lead"]["market_quality_evidence"]


def test_b2b_news_and_macro_stale_while_revalidate_non_blocking(monkeypatch, tmp_path):
    """B2b: expired cache returns immediately in <50ms while background thread refreshes."""
    monkeypatch.setattr(config, "reload_env", lambda *a, **k: None)
    monkeypatch.setattr(config, "NEWS_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "NEWS_ASYNC_REFRESH", True, raising=False)
    monkeypatch.setattr(config, "MACRO_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "MACRO_ASYNC_REFRESH", True, raising=False)

    # Seed stale in-memory caches (1 hour old)
    monkeypatch.setattr(news, "_MEMO", {"fetched_at": time.time() - 3600, "headlines": ["Old headline"]})
    monkeypatch.setattr(news, "_BG_REFRESHING", False)
    monkeypatch.setattr(macro, "_MEMO", {"fetched_at": time.time() - 3600, "macro": {"usd_index": 104.2}})
    monkeypatch.setattr(macro, "_BG_REFRESHING", False)

    def _slow_news():
        time.sleep(0.20)
        news._MEMO["fetched_at"] = time.time()
        news._MEMO["headlines"] = ["Fresh headline"]
        return ["Fresh headline"]

    def _slow_macro():
        time.sleep(0.20)
        macro._MEMO["fetched_at"] = time.time()
        macro._MEMO["macro"] = {"usd_index": 104.9}
        return {"usd_index": 104.9}

    monkeypatch.setattr(news, "fetch_headlines", _slow_news)
    monkeypatch.setattr(news, "_DEFAULT_FETCH_HEADLINES", _slow_news)
    monkeypatch.setattr(macro, "fetch_macro", _slow_macro)
    monkeypatch.setattr(macro, "_DEFAULT_FETCH_MACRO", _slow_macro)

    t0 = time.perf_counter()
    d = {}
    news.enrich_market_news(d)
    macro.enrich_macro(d)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert elapsed_ms < 60.0
    assert d["news"]["headlines"] == ["Old headline"]
    assert d["macro"]["usd_index"] == 104.2

    # Wait briefly for background refresh to complete and verify fresh data is ready next cycle
    deadline = time.time() + 1.5
    while time.time() < deadline and (news._BG_REFRESHING or macro._BG_REFRESHING):
        time.sleep(0.02)
    d2 = {}
    news.enrich_market_news(d2)
    macro.enrich_macro(d2)
    assert d2["news"]["headlines"] == ["Fresh headline"]
    assert d2["macro"]["usd_index"] == 104.9


def test_b2c_settled_tracked_positions_parse_pos_comment_and_sig(monkeypatch, tmp_path):
    """B2c: _load_logged_outcome_keys extracts pos:<pid> from comment so settled IDs skip MT5 IPC."""
    csv_p = tmp_path / "trade_outcomes.csv"
    keys_p = tmp_path / "logged_outcome_keys.json"
    with open(csv_p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "order_id", "symbol", "side", "pnl", "exit_price", "comment"])
        w.writerow(["2026-10-30T11:29:05", "555111", "XAUUSD", "SELL", "-1.84", "3973.78", "closed deal pos=97541102"])
    keys_p.write_text(
        json.dumps({"keys": ["deal:555111", "sig:97540001|XAUUSD|BUY|1.2000|3970.00000"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(main, "_OUTCOME_KEYS_FILE", keys_p, raising=False)
    monkeypatch.setattr(config, "DECISIONS_LOG_PATH", str(tmp_path / "decisions_log.csv"), raising=False)

    keys = main._load_logged_outcome_keys()
    assert "pos:97541102" in keys
    settled = set()
    for k in keys:
        if k.startswith("pos:"):
            settled.add(main._as_position_id(k[4:]))
        elif k.startswith("sig:"):
            settled.add(main._as_position_id(k[4:].split("|", 1)[0]))
    assert {97541102, 97540001}.issubset(settled)


def test_b3_cme_friday_1700_ny_weekend_close_and_audit_hygiene(monkeypatch, tmp_path):
    """B3: Friday >= 17:00 America/New_York stays WEEKEND (never reopening at 18:00 NY on Friday)."""
    monkeypatch.setattr(config, "reload_env", lambda *a, **k: None)
    monkeypatch.setattr(config, "SESSION_ENFORCE", True, raising=False)
    monkeypatch.setattr(config, "CME_DAILY_BREAK_AUTO", True, raising=False)
    monkeypatch.setattr(config, "DAILY_BREAK_START", "", raising=False)
    monkeypatch.setattr(config, "DAILY_BREAK_END", "", raising=False)
    monkeypatch.setattr(config, "TRADING_DAYS", "0,1,2,3,4", raising=False)

    fri_open = datetime(2026, 10, 30, 20, 55, tzinfo=timezone.utc)      # Fri 16:55 EDT
    fri_1705 = datetime(2026, 10, 30, 21, 5, tzinfo=timezone.utc)       # Fri 17:05 EDT
    fri_1830 = datetime(2026, 10, 30, 22, 30, tzinfo=timezone.utc)      # Fri 18:30 EDT (was false OPEN!)
    thu_break = datetime(2026, 10, 29, 21, 30, tzinfo=timezone.utc)     # Thu 17:30 EDT (daily break)
    fri_est_open = datetime(2026, 11, 6, 21, 30, tzinfo=timezone.utc)   # Fri 16:30 EST (post US DST)
    fri_est_closed = datetime(2026, 11, 6, 22, 15, tzinfo=timezone.utc) # Fri 17:15 EST

    assert session.is_market_open(fri_open) is True
    assert session.is_cme_weekend_closed(fri_1705) is True
    assert session.is_cme_daily_maintenance_break(fri_1705) is False
    assert session.is_market_open(fri_1705) is False
    assert session.is_market_open(fri_1830) is False
    assert "WEEKEND" in session.describe_now(fri_1830)

    assert session.is_cme_daily_maintenance_break(thu_break) is True
    assert session.is_cme_weekend_closed(thu_break) is False
    assert "17:00-18:00 NY CME maintenance" in session.describe_now(thu_break)

    assert session.is_market_open(fri_est_open) is True
    assert session.is_market_open(fri_est_closed) is False

    # Audit TEST 1 post-close overlap: 21:00Z -> 22:57Z on Fri 2026-10-30 is 100% closed CME weekend
    t0 = datetime(2026, 10, 30, 21, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 30, 22, 57, tzinfo=timezone.utc)
    overlap = audit_day._closed_cme_overlap_seconds(t0, t1)
    assert abs(overlap - (117 * 60)) <= 60


def test_b4_retired_judges_bench_separation(monkeypatch, tmp_path):
    """B4: Retired judges stay in diary for Law #5 shadow watching but never top active leaderboards."""
    monkeypatch.setattr(config, "reload_env", lambda *a, **k: None)
    monkeypatch.setattr(
        config,
        "RETIRED_JUDGES",
        ("poc_day", "supply_demand", "microprice", "queue_pos", "vwap_zscore", "mtf"),
        raising=False,
    )

    csv_path = tmp_path / "judge_panel_2026-10-30.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "judge", "votes", "participation_pct", "buy", "sell", "quiet",
            "right_pct", "scored", "points_after_spread", "avg_points",
            "agree_with_ensemble_pct", "weight_now", "weight_suggested",
        ])
        w.writerow(["poc_day", 676, 80.0, 0, 676, 0, 90.3, 676, 1835.2, 2.71, 85.0, 0.0, 0.0])
        w.writerow(["vwap_zscore", 53, 6.0, 0, 53, 0, 98.1, 53, 149.0, 2.81, 80.0, 0.0, 0.0])
        w.writerow(["value_area", 550, 65.0, 100, 450, 0, 82.5, 550, 379.4, 0.69, 78.0, 0.5, 0.66])
        w.writerow(["vwap_trend", 100, 12.0, 10, 90, 0, 82.7, 100, 217.8, 2.18, 75.0, 0.6, 0.80])

    # 1. judge_report_card.load_day skips retired judges
    day_loaded = judge_report_card.load_day(csv_path)
    assert "poc_day" not in day_loaded
    assert "vwap_zscore" not in day_loaded
    assert "value_area" in day_loaded

    # 2. xray_report._analyze_judges_csv separates active top from retired_bench
    xr = xray_report._analyze_judges_csv(csv_path)
    assert xr["active_count"] == 2
    assert xr["retired_count"] == 2
    assert [r["judge"] for r in xr["top"]] == ["value_area", "vwap_trend"]
    assert {r["judge"] for r in xr["retired_bench"]} == {"poc_day", "vwap_zscore"}

    # 3. audit_day.panel_roster marks retired judges off (w=0.0, on=False)
    roster, n_active = audit_day.panel_roster()
    by_n = {r["name"]: r for r in roster}
    assert by_n["poc_day"]["on"] is False
    assert by_n["vwap_zscore"]["on"] is False
    assert by_n["value_area"]["on"] is True
    assert n_active < 30


def test_b5_footprint_builder_slices_timestamped_ticks_to_180s_window():
    """B5: FootprintBuilder uses only the recent ORDER_FLOW_WINDOW_SECONDS (180s), not 3h stale delta."""
    base_ts = 1_761_840_000.0
    ticks = []
    # 3 hours ago: massive +5000 BUY volume that should NOT pollute the 180s footprint
    for i in range(50):
        ticks.append({
            "timestamp": base_ts - 7200 + i,
            "price": 4000.0,
            "volume": 100.0,
            "side": "BUY",
        })
    # Recent 120 seconds: clean -300 SELL flow
    for i in range(30):
        ticks.append({
            "timestamp": base_ts - 120 + i * 4,
            "price": 3980.0,
            "volume": 10.0,
            "side": "SELL",
        })

    fb = s2.FootprintBuilder(price_resolution=0.5)
    fp = fb.build_footprint(ticks, window_seconds=180.0)
    assert fp.delta_imbalance == pytest.approx(-1.0)
    assert fp.dominant_level == pytest.approx(3980.0)
    assert 4000.0 not in fp.price_levels

    # Timestamp-free synthetic ticks still use full-list fallback cleanly
    legacy_ticks = [{"price": 3990.0, "volume": 12.0, "side": "BUY"}]
    fp_leg = fb.build_footprint(legacy_ticks, window_seconds=180.0)
    assert fp_leg.delta_imbalance == pytest.approx(1.0)


def test_b6_bookmap_tail_skips_closed_rotated_chunks_and_caches_ts_iso(monkeypatch, tmp_path):
    """B6: Old closed rotated ticks_*.csv.gz chunks before cutoff-600s are never opened/gunzipped."""
    monkeypatch.setattr(config, "reload_env", lambda *a, **k: None)
    monkeypatch.setattr(config, "BOOKMAP_WINDOW_SECONDS", 3600, raising=False)
    now_dt = datetime.now(timezone.utc)
    now_s = now_dt.timestamp()
    # Create an old closed .csv.gz chunk with CORRUPT gzip bytes — if _BridgeTail tries to open it,
    # gzip.open would raise BadGzipFile or fail! Skipping via st_mtime proves 0 disk decompression.
    old_gz = tmp_path / "ticks_20261030_060000.csv.gz"
    old_gz.write_bytes(b"NOT_EVEN_VALID_GZIP_SHOULD_BE_SKIPPED")
    old_mtime = now_s - (5 * 3600)  # 5 hours old (> 1h window + 600s safety buffer)
    os.utime(old_gz, (old_mtime, old_mtime))

    active_csv = tmp_path / "ticks.csv"
    ts_str = (now_dt - timedelta(seconds=5)).isoformat()
    active_csv.write_text(
        f"time,event,price,size,aggressor,operation,instrument\n"
        f"{ts_str},Last,3981.5,3,SELL,,GCZ6.CME@RITHMIC\n",
        encoding="utf-8",
    )

    tail = bbp._BridgeTail(str(active_csv))
    with open(active_csv, "r", encoding="utf-8") as fh:
        tail._catch_up(fh)
    st = tail.instruments["GCZ6.CME@RITHMIC"]
    assert len(st.ticks) == 1
    assert st.ticks[0]["price"] == pytest.approx(3981.5)
    assert isinstance(st.ticks[0].get("ts_iso"), str) and "T" in st.ticks[0]["ts_iso"]


def test_b7_cvd_divergence_handles_high_tick_rate_and_short_tape():
    """B7: _detect_divergence scans full 2*lookback minute window even at >1000 ticks/min."""
    base_min = 29_364_000  # arbitrary epoch minute
    # +1.5 pts/bar -> +6.0 pts across lookback=5 (>0.1% price_roc threshold on $3,980 Gold)
    np_close = s2.np.array([3980.0 + 1.5 * i for i in range(10)])

    # Case 1: Only 2 minutes of ticks (< lookback=5) -> must return 0.0, NEVER compare to 3h cvd!
    short_ticks = [
        {"timestamp": (base_min + m) * 60.0 + i * 0.5, "price": 3980.0 + m, "volume": 5.0, "side": "SELL"}
        for m in range(2)
        for i in range(20)
    ]
    assert s2._detect_divergence(np_close, -9999.0, lookback=5, tick_data=short_ticks) == 0.0

    # Case 2: 10 minutes at 300 ticks/min = 3,000 ticks (> old tick_data[-1000:] cap!).
    # Earlier 5m (m=0..4): lower price 3980.0, strong positive delta (+300/min).
    # Recent  5m (m=5..9): higher price 3985.0, negative delta (-300/min) -> bearish divergence (< 0)!
    dense_ticks = []
    for m in range(10):
        px = 3980.0 if m < 5 else 3985.0
        side = "BUY" if m < 5 else "SELL"
        for i in range(300):
            dense_ticks.append({
                "timestamp": (base_min + m) * 60.0 + (i * 0.15),
                "price": px,
                "volume": 1.0,
                "side": side,
            })
    div = s2._detect_divergence(np_close, 0.0, lookback=5, tick_data=dense_ticks)
    assert div < 0.0


def test_b8_book_state_file_honors_config_data_dir_isolation(monkeypatch, tmp_path):
    """B8: _book_state_file() follows config.DATA_DIR so pytest never overwrites live data/last_book_state.json."""
    monkeypatch.setattr(s2, "_BOOK_STATE_PATH", None, raising=False)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    assert s2._book_state_file() == tmp_path / "last_book_state.json"

    explicit = tmp_path / "custom_book.json"
    monkeypatch.setattr(s2, "_BOOK_STATE_PATH", explicit, raising=False)
    assert s2._book_state_file() == explicit
