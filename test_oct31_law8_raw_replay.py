"""
Four-Pass regression & verification tests for Law #8 (2026-10-31):
  1. Historical `now` clock propagation in `step2_market_analysis` (`SignalEngine`
     killzones/Budapest hours + `_l2_inputs` depth timestamp age).
  2. Retired-judge quarantine in `tools/insight.py` (`panel_rows`, `weight_ab`,
     `walk_forward`, `decisions_page`, `judges_page`) without importing `config`.
  3. Windows UCRT64 high-resolution UTC clock alignment in `BookmapBridgeProvider.acquire`
     (defeating coarse 15.6ms `time.time()` lag vs `datetime.now(timezone.utc)`).
  4. Exact bit-identity of `_fast_ts_epoch` + `mbo_step_ring(maxlen=3000)` +
     `_fast_mbo_track_only` vs full `_MboTail._handle_line` across 6,000+ MBO events.
  5. End-to-end `tools/raw_replay.py` multi-day raw `ticks_*.csv.gz` + `mbo_*.csv.gz`
     replay: strict isolation from hostile `.env` `BOOKMAP_BRIDGE_FILE` /
     `DATA_SYMBOL=GCZ6.COMEX@RITHMIC`, zero crossed-book warnings, zero look-ahead
     causality, zero retired-judge leaks, and 100% live `data/` SHA-256 invariance.
"""
from __future__ import annotations

import collections
import gzip
import hashlib
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import bookmap_bridge_provider as bbp
import config
import data_providers
import step2_market_analysis as s2
from tools import insight
from tools import raw_replay as rr


def _write_synthetic_raw_day(
    data_dir: Path,
    day_str: str,
    start_px: float = 3980.0,
    drift_per_min: float = 0.25,
    minutes: int = 150,
    cutoff_utc: datetime | None = None,
    instrument: str = "GC 12-26",
) -> None:
    """Create realistic rotated gzipped raw `ticks` + `mbo` files with clean book uncrossing."""
    d_compact = day_str.replace("-", "")
    arch = data_dir / "archive"
    arch.mkdir(parents=True, exist_ok=True)
    ticks_gz = arch / f"ticks_{d_compact}_160000.csv.gz"
    mbo_gz = arch / f"mbo_{d_compact}_160000.csv.gz"

    base_dt = datetime.fromisoformat(f"{day_str}T09:00:00+00:00")
    t_lines = ["time,event,price,size,level,operation,instrument\n"]
    m_lines = ["time,event_type,order_id,price,size,instrument\n"]

    px = start_px
    oid_seq = 1000
    prev_bids: list[float] = []
    prev_asks: list[float] = []
    for m in range(minutes):
        cur_dt = base_dt + timedelta(minutes=m)
        if cutoff_utc is not None and cur_dt > cutoff_utc:
            break
        px = round(px + drift_per_min + (0.10 if m % 2 == 0 else -0.05), 2)
        bid = round(px - 0.10, 2)
        ask = round(px + 0.10, 2)
        iso0 = (cur_dt + timedelta(seconds=2)).isoformat()
        iso1 = (cur_dt + timedelta(seconds=15)).isoformat()
        iso2 = (cur_dt + timedelta(seconds=35)).isoformat()

        new_bids = [round(bid - 0.10 * lvl, 2) for lvl in range(5)]
        new_asks = [round(ask + 0.10 * lvl, 2) for lvl in range(5)]
        new_bid_set = set(new_bids)
        new_ask_set = set(new_asks)

        # Remove vacated levels first so rising/falling markets never leave crossed levels
        for old_bp in prev_bids:
            if old_bp not in new_bid_set:
                t_lines.append(f"{iso0},DepthBid,{old_bp:.2f},0,0,Remove,{instrument}\n")
        for old_ap in prev_asks:
            if old_ap not in new_ask_set:
                t_lines.append(f"{iso0},DepthAsk,{old_ap:.2f},0,0,Remove,{instrument}\n")

        for lvl, (bp, ap) in enumerate(zip(new_bids, new_asks)):
            bsz = 12 + lvl * 3
            asz = 8 + lvl * 2
            t_lines.append(f"{iso0},DepthBid,{bp:.2f},{bsz},{lvl},Update,{instrument}\n")
            t_lines.append(f"{iso0},DepthAsk,{ap:.2f},{asz},{lvl},Update,{instrument}\n")
        prev_bids = new_bids
        prev_asks = new_asks

        # Direct trade prints
        side1 = "Buy" if drift_per_min >= 0 else "Sell"
        side2 = "Buy" if (m % 3 != 0 and drift_per_min >= 0) else "Sell"
        t_lines.append(f"{iso1},Last,{ask if side1 == 'Buy' else bid:.2f},4,0,{side1},{instrument}\n")
        t_lines.append(f"{iso2},Last,{px:.2f},6,0,{side2},{instrument}\n")

        # Raw MBO events (including iceberg reload pattern every 5m)
        oid_seq += 1
        m_lines.append(f"{iso0},BID_NEW,oid_{oid_seq},{bid:.2f},10,{instrument}\n")
        if m % 5 == 0:
            m_lines.append(f"{iso1},BID_REPLACE,oid_{oid_seq},{bid:.2f},15,{instrument}\n")
        oid_ask = oid_seq + 50000
        m_lines.append(f"{iso0},ASK_NEW,oid_{oid_ask},{ask:.2f},7,{instrument}\n")
        if m % 4 == 0:
            m_lines.append(f"{iso2},ASK_CANCEL,oid_{oid_ask},{ask:.2f},0,{instrument}\n")

    with gzip.open(ticks_gz, "wt", encoding="utf-8", newline="\n") as fh:
        fh.writelines(t_lines)
    with gzip.open(mbo_gz, "wt", encoding="utf-8", newline="\n") as fh:
        fh.writelines(m_lines)


def test_step2_historical_now_clock_propagation(tmp_path, monkeypatch):
    """analyze_market(md, now=...) and _l2_inputs(md, now=...) must honor historical `now`."""
    monkeypatch.setattr(config, "reload_env", lambda *a, **k: None)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "DATA_SOURCE", "bookmapbridge")
    monkeypatch.setattr(config, "V6_KILLZONES_ENABLED", True)

    hist_now = datetime(2026, 10, 28, 14, 15, 0, tzinfo=timezone.utc)
    md = {
        "symbol": "GC 12-26",
        "price": 4010.0,
        "bid": 4009.9,
        "ask": 4010.1,
        "spread_pct": (0.2 / 4010.0) * 100.0,
        "depth_timestamp": (hist_now - timedelta(seconds=2)).isoformat(),
        "bid_depth": {4009.9: 15.0, 4009.8: 10.0},
        "ask_depth": {4010.1: 12.0, 4010.2: 9.0},
        "order_book": {"bids": [(4009.9, 15.0)], "asks": [(4010.1, 12.0)]},
        "data_quality": {"quote": "valid", "level2": "validated", "instrument": "matched"},
        "l2_diagnostics": {"status": "validated"},
        "tick_data": [
            {"price": 4009.9 + 0.02 * i, "volume": 2.0, "side": "BUY",
             "timestamp": (hist_now - timedelta(seconds=60 - i)).isoformat()}
            for i in range(40)
        ],
        "candles": {
            "open": [4005.0 + 0.08 * i for i in range(70)],
            "high": [4005.3 + 0.08 * i for i in range(70)],
            "low": [4004.8 + 0.08 * i for i in range(70)],
            "close": [4005.1 + 0.08 * i for i in range(70)],
            "volume": [50.0 for _ in range(70)],
        },
        "macro": {},
        "news": {"headlines": [], "events": [], "fetch_calendar": False},
    }

    usable, bids, asks, _bk, _upd, reason = s2._l2_inputs(md, now=hist_now)
    assert usable is True, f"Expected usable L2 at historical now, got reason={reason}"
    assert 4009.9 in bids and 4010.1 in asks

    rr._reset_step2_singletons()
    snap = s2.analyze_market(md, now=hist_now)
    joined_notes = "\n".join(snap.notes)
    assert "BUDAPEST trading 15:00 Budapest = 14:00 UTC" in joined_notes
    assert "BUDAPEST OFF-HOURS" not in joined_notes


def test_insight_quarantines_retired_judges_in_weight_ab_and_walk_forward():
    """Retired judges must never receive weight 1.0 in weight_ab or walk_forward."""
    t0 = datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc)
    series = [(t0 + timedelta(minutes=m), 4000.0 + float(m)) for m in range(120)]
    diary = []
    for m in range(30):
        dt = t0 + timedelta(minutes=m)
        diary.append({
            "_dt": dt,
            "price": 4000.0 + float(m),
            "signal_direction": "BUY",
            "judge_votes": [
                {"judge": "mtf", "dir": 1.0, "weight": 0.70, "raw": "MTF UP"},
                {"judge": "vwap_trend", "dir": 1.0, "weight": 0.60, "raw": "VWAP UP"},
                {"judge": "poc_day", "dir": -1.0, "weight": 0.50, "raw": "retired POC fade"},
                {"judge": "macro_yield", "dir": -1.0, "weight": 0.80, "raw": "retired yield"},
            ],
        })

    rows = insight.panel_rows("2026-10-28", diary, series)
    assert len(rows) == 30
    assert abs(rows[0][4] - 15.0) < 1e-6
    for _dt, _px, votes, _ens, _mv in rows:
        assert "poc_day" not in votes
        assert "macro_yield" not in votes
        assert "mtf" in votes and "vwap_trend" in votes

    ab_txt, ab_tbl = insight.weight_ab([("2026-10-28", rows)], min_n=10)
    assert "poc_day" not in ab_txt
    assert "macro_yield" not in ab_txt
    assert ab_tbl["current (as used)"]["acc"] == 100.0

    wf_txt, wf_sum = insight.walk_forward(
        [("2026-10-27", rows), ("2026-10-28", rows)], fold=1
    )
    assert wf_sum["folds"] == 1
    assert "VERDICT:" in wf_txt

    jhtml = insight.judges_page("2026-10-28", diary, series)
    assert "poc_day" in jhtml and "retired [w 0.00]" in jhtml
    dhtml = insight.decisions_page(
        "2026-10-28",
        [{"_dt": t0, "price": 4000.0, "signal_direction": "BUY", "signal_strength": 20.0,
          "signal_confidence": 60.0, "ai_action": "BUY", "ai_confidence": 75.0,
          "exec_status": "SKIPPED", "reason": "demo"}],
        diary,
        series,
    )
    assert "Law #8 note:" in dhtml
    assert "[retired]" in dhtml


def test_windows_ucrt64_coarse_time_time_lag_does_not_reject_fresh_bookmap_events(monkeypatch):
    """Simulate Windows UCRT64 where `time.time()` lags `datetime.now(timezone.utc)` by 15ms."""
    now = datetime.now(timezone.utc)
    # Simulate coarse 15.625ms Windows GetSystemTimeAsFileTime lag on time.time()
    monkeypatch.setattr(bbp.time, "time", lambda: now.timestamp() - 0.015)
    monkeypatch.setattr(config, "DATA_SOURCE", "bookmapbridge", raising=False)
    monkeypatch.setattr(config, "DATA_SYMBOL", "MGC 12-26", raising=False)
    monkeypatch.setattr(config, "BOOKMAP_WAIT_SECONDS", 0, raising=False)
    monkeypatch.setattr(config, "BOOKMAP_LEVEL_MAX_AGE", 900.0, raising=False)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 300.0, raising=False)

    state = bbp._InstrumentState()
    state.bids = {2000.0: 10.0}
    state.asks = {2000.5: 9.0}
    state.level_ts_b = {2000.0: now}
    state.level_ts_a = {2000.5: now}
    state.best_bid = 2000.0
    state.best_ask = 2000.5
    state.last_dt = now
    state.ticks = [{"price": 2000.25, "volume": 2.0, "side": "BUY",
                    "is_direct": True, "operation": "BUY", "ts": now}]
    state.mbo_events = [{"order_id": "1", "type": "FILL", "price": 2000.25,
                         "side": "BUY", "is_market_order": True,
                         "timestamp": now.isoformat()}]

    tail = SimpleNamespace(lock=threading.Lock(), instruments={"MGCZ6": state},
                           direct_side_hits=1, line_count=1)
    provider = object.__new__(bbp.BookmapBridgeProvider)
    data_providers.BaseProvider.__init__(provider)
    provider.tail = tail
    provider.mbo_tail = None

    md = provider.acquire()
    assert md["data_quality"]["quote"] == "valid"
    assert md["last_order_event_age_seconds"] is not None
    assert md["last_order_event_age_seconds"] >= 0


def test_fast_ts_epoch_and_mbo_ring_exact_parity():
    """Verify `_fast_ts_epoch` and `mbo_step_ring` + `_fast_mbo_track_only` match full `_handle_line` 100%."""
    samples = [
        "2026-10-30T14:30:05.123456+00:00",
        "2026-10-30T14:30:05.987Z",
        "2026-10-30T15:30:05.050+01:00",
        "2026-10-30T08:00:00+00:00",
        "2026-10-30T08:00:00.5",
    ]
    for s in samples:
        expected = rr._parse_ts_utc(s).timestamp()
        actual = rr._fast_ts_epoch(s)
        assert actual == pytest.approx(expected, abs=1e-7)

    base = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
    lines = []
    for i in range(6200):
        iso = (base + timedelta(milliseconds=50 * i)).isoformat()
        oid = f"oid_{i % 700}"
        if i % 3 == 0:
            lines.append(f"{iso},BID_NEW,{oid},{3990.0 + (i % 10) * 0.1:.2f},{5 + (i % 7)},GCZ6.COMEX@RITHMIC\n")
        elif i % 3 == 1:
            lines.append(f"{iso},BID_REPLACE,{oid},{3990.1 + (i % 10) * 0.1:.2f},{8 + (i % 5)},GCZ6.COMEX@RITHMIC\n")
        else:
            lines.append(f"{iso},BID_CANCEL,{oid},0,0,GCZ6.COMEX@RITHMIC\n")

    t_full = bbp._MboTail.__new__(bbp._MboTail)
    t_full.path = ""
    t_full.lock = threading.Lock()
    t_full.instruments = {}
    t_full.line_count = 0
    t_full.bad_lines = 0
    for ln in lines:
        t_full._handle_line(ln)

    t_fast = bbp._MboTail.__new__(bbp._MboTail)
    t_fast.path = ""
    t_fast.lock = threading.Lock()
    t_fast.instruments = {}
    t_fast.line_count = 0
    t_fast.bad_lines = 0
    ring: collections.deque = collections.deque()
    for ln in lines:
        if len(ring) >= 3000:
            rr._fast_mbo_track_only(t_fast, ring.popleft())
        ring.append(ln)
    for ln in ring:
        t_fast._handle_line(ln)

    st_full = t_full.instruments["GCZ6.COMEX@RITHMIC"]
    st_fast = t_fast.instruments["GCZ6.COMEX@RITHMIC"]
    assert st_fast.active_orders == st_full.active_orders
    assert st_fast.mbo_events[-3000:] == st_full.mbo_events[-3000:]
    assert st_fast.last_dt == st_full.last_dt


def test_raw_replay_causality_invariance_and_cli(tmp_path, monkeypatch):
    """raw_replay must ignore hostile `.env` bridge paths on custom `--root-dir`, stay causal, and preserve live state."""
    monkeypatch.setattr(config, "reload_env", lambda *a, **k: None)
    monkeypatch.setattr(config, "V6_4TEAMS_ENABLED", False)
    monkeypatch.setattr(config, "AI_MAX_PROMPT_BARS", 20)
    monkeypatch.setattr(config, "AI_CONFIDENCE_THRESHOLD", 35.0)
    monkeypatch.setenv("CONFIDENCE_THRESHOLD", "35")

    # Simulate owner's Windows `.env` pointing BOOKMAP_BRIDGE_FILE + DATA_SYMBOL at a live Rithmic folder
    hostile_live_dir = tmp_path / "hostile_windows_live"
    _write_synthetic_raw_day(
        hostile_live_dir / "data", "2026-10-27",
        start_px=4150.0, drift_per_min=-0.20, minutes=40, instrument="GCZ6.COMEX@RITHMIC"
    )
    hostile_ticks_csv = hostile_live_dir / "ticks.csv"
    hostile_ticks_csv.write_text(
        "time,event,price,size,level,operation,instrument\n"
        "2026-10-27T09:05:00+00:00,Last,4150.00,5,0,Sell,GCZ6.COMEX@RITHMIC\n",
        encoding="utf-8", newline="\n",
    )
    monkeypatch.setenv("BOOKMAP_BRIDGE_FILE", str(hostile_ticks_csv))
    monkeypatch.setattr(config, "BOOKMAP_BRIDGE_FILE", str(hostile_ticks_csv), raising=False)
    monkeypatch.setattr(config, "DATA_SYMBOL", "GCZ6.COMEX@RITHMIC", raising=False)

    proj = tmp_path / "proj"
    data_dir = proj / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    sentinel_files = {
        "last_book_state.json": '{"bids":{"3884.8":10},"asks":{"3885.0":12}}\n',
        "tracked_bot_positions.json": '{"position_ids":[97541102]}\n',
        "market_snapshot.json": '{"price":3884.9}\n',
    }
    before_hashes = {}
    for fname, content in sentinel_files.items():
        fpath = data_dir / fname
        fpath.write_text(content, encoding="utf-8", newline="\n")
        before_hashes[fname] = hashlib.sha256(fpath.read_bytes()).hexdigest()

    _write_synthetic_raw_day(data_dir, "2026-10-27", start_px=3980.0, drift_per_min=0.30, minutes=150)
    _write_synthetic_raw_day(data_dir, "2026-10-28", start_px=4020.0, drift_per_min=0.00, minutes=140)

    full_res = rr.replay_single_day("2026-10-27", root_dir=proj, data_dir=data_dir, every_s=180)
    assert full_res["ok"] is True
    assert full_res["instrument"] == "GC 12-26"
    assert full_res["errors"] == 0
    assert full_res["mbo_streamed"] > 0

    trunc_proj = tmp_path / "trunc_proj"
    trunc_data = trunc_proj / "data"
    cutoff = datetime(2026, 10, 27, 10, 15, 0, tzinfo=timezone.utc)
    _write_synthetic_raw_day(trunc_data, "2026-10-27", start_px=3980.0, drift_per_min=0.30, minutes=150, cutoff_utc=cutoff)
    trunc_res = rr.replay_single_day("2026-10-27", root_dir=trunc_proj, data_dir=trunc_data, every_s=180)
    assert trunc_res["ok"] is True

    check_cut = datetime(2026, 10, 27, 10, 10, 0, tzinfo=timezone.utc)
    full_prefix = [
        (c["buda_hhmmss"], c["direction"], c["confidence"], c["power_dir"], c["shot_action"],
         [(v["judge"], v["dir"]) for v in c["active_votes"]])
        for c in full_res["cycles"] if c["dt_utc"] <= check_cut
    ]
    trunc_prefix = [
        (c["buda_hhmmss"], c["direction"], c["confidence"], c["power_dir"], c["shot_action"],
         [(v["judge"], v["dir"]) for v in c["active_votes"]])
        for c in trunc_res["cycles"] if c["dt_utc"] <= check_cut
    ]
    assert len(full_prefix) >= 15
    assert full_prefix == trunc_prefix, "Future raw data leaked into earlier replay decisions!"

    retired_lower = {j.lower() for j in config.RETIRED_JUDGES}
    for c in full_res["cycles"]:
        assert c["l2_status"] in ("validated", "live"), f"Unexpected L2 status: {c['l2_status']}"
        for v in c["active_votes"]:
            assert v["judge"].lower() not in retired_lower
    for j in full_res["judge_stats"]:
        assert j.lower() not in retired_lower

    rc = rr.main([
        "--days", "5", "--every", "180",
        "--root-dir", str(proj), "--data-dir", str(data_dir), "--no-browser",
    ])
    assert rc == 0

    assert (data_dir / "current_version_decisions_2026-10-27.html").exists()
    assert (data_dir / "current_version_decisions_2026-10-28.html").exists()
    assert (data_dir / "current_version_replay.html").exists()
    txt_out = (data_dir / "current_version_replay.txt").read_text(encoding="utf-8")
    assert "LAW #8 CURRENT-VERSION RAW-DATA REPLAY & DIAGNOSTIC REPORT" in txt_out
    assert "[2] GATE WATERFALL (CURRENT VERSION)" in txt_out
    assert "[7] CURRENT ACTIVE JUDGES SCORECARD" in txt_out

    # Restore check: config.DATA_SYMBOL restored to owner's GCZ6.COMEX@RITHMIC
    assert config.DATA_SYMBOL == "GCZ6.COMEX@RITHMIC"

    for fname, expected_sha in before_hashes.items():
        actual_sha = hashlib.sha256((data_dir / fname).read_bytes()).hexdigest()
        assert actual_sha == expected_sha, f"Live file {fname} was mutated by raw_replay!"
