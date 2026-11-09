"""Section #10 (Stage B1): Live BookMap Bridge & Feed Integrity Rig (`2026-11-07`).

Covers all 5 Stage B1 engineering pillars under both clean M5 defaults and
simulated live Windows M3 `.env`:
  1. Windows `WinError 32` safe file rotation (`saved_pos` seek-restore + 60s
     retry cooldown + <=30ms lock hold in `_BridgeTail` & `_MboTail`).
  2. Time-windowed MBO retention + near-corridor (`+-BOOKMAP_MBO_NEAR_CORRIDOR_USD`)
     priority selector so heavy outer-book churn (`~151/s`) never evicts near-touch
     OFI/spoof/iceberg events across a full `M3` (`180s`) / `M5` (`300s`) bar.
  3. True numeric UTC epoch sort on `order_events` across mixed ISO offsets,
     cross-format MBO contract matching (`GCZ6.COMEX@RITHMIC` <-> `GCZ6`), and
     `_MboTail._replay_recorded_history` rotated-archive warmup with seek cookie.
  4. Dynamic `config.BOOKMAP_MAX_DEPTH_LEVELS` runtime lookup + zero-reparse
     `fast_m1_candles` parity against `BaseProvider.trades_to_candles` + preserved
     `crossed_book` fail-closed contract.
  5. `tools/raw_replay.py` `[8i]` Section #10 Feed Integrity Scorecard & Law #11
     `M3 <-> M5` active MBO window scaling.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import gzip
import threading
import time

import numpy as np
import pytest

import bookmap_bridge_provider as bbp
import config
from data_providers import BaseProvider, trades_to_candles


def _offline_provider(tail: bbp._BridgeTail, mbo_tail: bbp._MboTail | None = None) -> bbp.BookmapBridgeProvider:
    p = object.__new__(bbp.BookmapBridgeProvider)
    BaseProvider.__init__(p)
    p.name = "BookMap-Bridge-Test"
    p.tail = tail
    p.mbo_tail = mbo_tail
    return p


def _bare_bridge_tail(path: str = "") -> bbp._BridgeTail:
    t = bbp._BridgeTail.__new__(bbp._BridgeTail)
    t.path = path
    t.lock = threading.Lock()
    t.instruments = {}
    t.line_count = 0
    t.bad_lines = 0
    t.direct_side_hits = 0
    return t


def _bare_mbo_tail(path: str = "") -> bbp._MboTail:
    m = bbp._MboTail.__new__(bbp._MboTail)
    m.path = path
    m.lock = threading.Lock()
    m.instruments = {}
    m.line_count = 0
    m.bad_lines = 0
    return m


# --------------------------------------------------------------------------- #
# 1. Windows WinError 32 Safe Rotation (seek-restore + 60s cooldown)
# --------------------------------------------------------------------------- #
def test_stage_b1_deferred_rotation_preserves_seek_offset_and_cooldown(tmp_path, monkeypatch):
    """When Windows blocks `os.rename` (`WinError 32`), `_maybe_rotate` restores `saved_pos`
    instead of rewinding to offset 0 and backs off for 60s instead of sleeping 1,000ms/line."""
    ticks_file = tmp_path / "ticks.csv"
    header = "time,event,price,size,level,operation,instrument\n"
    row1 = "2026-11-06T12:00:00Z,Last,2700.0,2,0,BUY,GCZ6.COMEX@RITHMIC\n"
    row2 = "2026-11-06T12:00:01Z,Last,2700.2,3,0,BUY,GCZ6.COMEX@RITHMIC\n"
    ticks_file.write_text(header + row1 + row2, encoding="utf-8")

    mbo_file = tmp_path / "mbo.csv"
    mbo_hdr = "time,event_type,order_id,price,size,instrument\n"
    mrow1 = "2026-11-06T12:00:00Z,BID_NEW,9001,2699.9,5,GCZ6.COMEX@RITHMIC\n"
    mrow2 = "2026-11-06T12:00:01Z,ASK_NEW,9002,2700.3,4,GCZ6.COMEX@RITHMIC\n"
    mbo_file.write_text(mbo_hdr + mrow1 + mrow2, encoding="utf-8")

    # Force immediate rotation threshold and simulate Windows WinError 32 on rename
    monkeypatch.setattr(config, "BOOKMAP_ROTATE_MB", 0.00001)
    monkeypatch.setattr(config, "BOOKMAP_MBO_ROTATE_MB", 0.00001)
    rename_calls = {"n": 0}

    def _blocked_rename(src, dst):
        rename_calls["n"] += 1
        raise PermissionError(13, "The process cannot access the file (WinError 32)")

    monkeypatch.setattr(bbp.os, "rename", _blocked_rename)

    bt = _bare_bridge_tail(str(ticks_file))
    with open(ticks_file, "r", encoding="utf-8") as fh:
        assert fh.readline() == header
        assert fh.readline() == row1
        pos_before = fh.tell()
        t0 = time.perf_counter()
        nf = bt._maybe_rotate(fh)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        try:
            assert rename_calls["n"] == 3  # 3 quick attempts, never 20x50ms
            assert elapsed_ms < 400.0
            assert nf.tell() == pos_before
            # Next read MUST yield row2, NOT re-read header + row1 from byte 0!
            assert nf.readline() == row2
            # Immediate second call inside 60s cooldown must NOT touch os.rename again
            nf2 = bt._maybe_rotate(nf)
            assert nf2 is nf
            assert rename_calls["n"] == 3
        finally:
            nf.close()

    mt = _bare_mbo_tail(str(mbo_file))
    with open(mbo_file, "r", encoding="utf-8") as mfh:
        assert mfh.readline() == mbo_hdr
        assert mfh.readline() == mrow1
        mpos_before = mfh.tell()
        mnf = mt._maybe_rotate(mfh)
        try:
            assert rename_calls["n"] == 6
            assert mnf.tell() == mpos_before
            assert mnf.readline() == mrow2
            assert mt._maybe_rotate(mnf) is mnf
            assert rename_calls["n"] == 6
        finally:
            mnf.close()


# --------------------------------------------------------------------------- #
# 2. Near-Corridor MBO Priority Across Full 1-Bar Window (Cont et al. 2014)
# --------------------------------------------------------------------------- #
def test_stage_b1_near_corridor_mbo_survives_heavy_outer_book_churn(monkeypatch):
    """At 150+ MBO/sec, 4,500 far-away outer-book updates (+-$30 from price) must NOT
    evict near-touch (+-$8) MBO events from 170 seconds ago inside the active M3 bar."""
    t0 = datetime(2026, 11, 6, 14, 0, 0, tzinfo=timezone.utc)
    now_dt = t0 + timedelta(seconds=175)
    monkeypatch.setattr(bbp, "_now_utc_epoch", lambda: now_dt.timestamp())
    monkeypatch.setattr(config, "ORDER_FLOW_WINDOW_SECONDS", 180)
    monkeypatch.setattr(config, "POWER_BAR_SECONDS", 180)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 300)
    monkeypatch.setattr(config, "BOOKMAP_MAX_ORDER_EVENTS", 2000)
    monkeypatch.setattr(config, "BOOKMAP_MBO_NEAR_CORRIDOR_USD", 8.0)

    inst = "GCZ6.COMEX@RITHMIC"
    bt = _bare_bridge_tail()
    bt._handle_line(f"{now_dt.isoformat()},DepthBid,2700.0,12,0,Update,{inst}\n")
    bt._handle_line(f"{now_dt.isoformat()},DepthAsk,2700.2,10,0,Update,{inst}\n")
    bt._handle_line(f"{now_dt.isoformat()},Last,2700.1,3,0,BUY,{inst}\n")

    mt = _bare_mbo_tail()
    # 50 early near-corridor passive wall/iceberg events 170s ago (inside the 180s M3 bar)
    for i in range(50):
        ts_early = (t0 + timedelta(seconds=5 + i * 0.1)).isoformat()
        mt._handle_line(f"{ts_early},BID_NEW,early_{i},2698.5,25,{inst}\n")

    # Followed by 4,500 outer-book churn events $30 away ($2670.0 / $2730.0) in last 30s
    for j in range(4500):
        ts_late = (t0 + timedelta(seconds=145 + j * 0.006)).isoformat()
        if j % 2 == 0:
            mt._handle_line(f"{ts_late},BID_NEW,far_{j},2670.0,1,{inst}\n")
        else:
            mt._handle_line(f"{ts_late},ASK_NEW,far_{j},2730.0,1,{inst}\n")

    mt._prune_old(0.0)
    # Full 4,550 in-window events remain in _MboTail (> old 3,000 slice truncation)
    assert len(mt.instruments[inst].mbo_events) == 4550

    prov = _offline_provider(bt, mt)
    md = prov.acquire(symbol=inst)
    oe = md["order_events"]
    assert len(oe) == 2000
    early_ids = {e["order_id"] for e in oe if str(e["order_id"]).startswith("early_")}
    # ALL 50 early near-corridor events from 170s ago are preserved!
    assert len(early_ids) == 50
    # Public order_events schema remains 100% clean (no private epoch keys leaked)
    assert set(oe[0].keys()) <= {"type", "side", "price", "size", "timestamp", "order_id", "old_size", "old_price"}
    # Chronological order preserved
    epochs = [bbp._event_epoch(e) for e in oe]
    assert epochs == sorted(epochs)


# --------------------------------------------------------------------------- #
# 3. Numeric Epoch Sort, Cross-Format Contract Match & MBO History Warmup
# --------------------------------------------------------------------------- #
def test_stage_b1_numeric_epoch_sort_and_cross_format_mbo_contract_match(monkeypatch):
    """`order_events` sort by true UTC instant (not string order) and match `GCZ6` MBO to
    `GCZ6.COMEX@RITHMIC` ticks while still rejecting mismatched contracts (`SILZ6` / `GCG7`)."""
    now_dt = datetime(2026, 11, 6, 15, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(bbp, "_now_utc_epoch", lambda: now_dt.timestamp())

    bt = _bare_bridge_tail()
    bt._handle_line("2026-11-06T14:59:58Z,DepthBid,2700.0,8,0,Update,GCZ6.COMEX@RITHMIC\n")
    bt._handle_line("2026-11-06T14:59:58Z,DepthAsk,2700.2,8,0,Update,GCZ6.COMEX@RITHMIC\n")
    bt._handle_line("2026-11-06T14:59:59Z,Last,2700.1,1,0,BUY,GCZ6.COMEX@RITHMIC\n")

    # Direct mutation of `mbo_events` without `mbo_epochs` (backward-compat test pattern)
    # Note: `15:59:55+01:00` == `14:59:55Z`, which is EARLIER than `14:59:57Z`, even though
    # ASCII string `"2026-11-06T15:59:55+01:00"` sorts AFTER `"2026-11-06T14:59:57+00:00"`!
    mt = _bare_mbo_tail()
    st_mbo = bbp._InstrumentState()
    st_mbo.mbo_events = [
        {"type": "ADD", "side": "ASK", "price": 2700.4, "size": 5.0,
         "timestamp": "2026-11-06T14:59:57+00:00", "order_id": "later_utc"},
        {"type": "ADD", "side": "BID", "price": 2699.8, "size": 7.0,
         "timestamp": "2026-11-06T15:59:55+01:00", "order_id": "earlier_cet"},
    ]
    mt.instruments["GCZ6"] = st_mbo
    # Foreign contract must NEVER pollute GCZ6
    st_foreign = bbp._InstrumentState()
    st_foreign.mbo_events = [
        {"type": "ADD", "side": "BID", "price": 32.5, "size": 50.0,
         "timestamp": "2026-11-06T14:59:58+00:00", "order_id": "silver_oid"},
    ]
    mt.instruments["SILZ6.COMEX@RITHMIC"] = st_foreign

    prov = _offline_provider(bt, mt)
    md = prov.acquire(symbol="GCZ6.COMEX@RITHMIC")
    oids = [e["order_id"] for e in md["order_events"]]
    assert oids == ["earlier_cet", "later_utc"]


def test_stage_b1_mbo_recorded_history_replays_rotated_gz_without_double_reading(tmp_path, monkeypatch):
    """`_MboTail._catch_up` warms recent window from rotated `mbo_*.csv.gz` + live `mbo.csv`
    and uses `live_offset` seek cookie so live `mbo.csv` is never ingested twice."""
    now_dt = datetime(2026, 11, 6, 16, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(bbp, "_now_utc_epoch", lambda: now_dt.timestamp())
    monkeypatch.setattr(config, "ORDER_FLOW_WINDOW_SECONDS", 180)
    monkeypatch.setattr(config, "STALE_DATA_SECONDS", 300)

    arch_dir = tmp_path / "data" / "archive"
    arch_dir.mkdir(parents=True)
    gz_file = arch_dir / "mbo_20261106_155800.csv.gz"
    hdr = "time,event_type,order_id,price,size,instrument\n"
    too_old = "2026-11-06T15:40:00Z,BID_NEW,old_1,2699.0,10,GCZ6.COMEX@RITHMIC\n"
    fresh_gz = "2026-11-06T15:58:30Z,BID_NEW,gz_1,2699.5,12,GCZ6.COMEX@RITHMIC\n"
    with gzip.open(gz_file, "wt", encoding="utf-8") as gfh:
        gfh.write(hdr + too_old + fresh_gz)

    live_mbo = tmp_path / "mbo.csv"
    fresh_live = "2026-11-06T15:59:40Z,REPLACE,gz_1,2699.6,9,GCZ6.COMEX@RITHMIC\n"
    live_mbo.write_text(hdr + fresh_live, encoding="utf-8")

    mt = _bare_mbo_tail(str(live_mbo))
    with open(live_mbo, "r", encoding="utf-8") as fh:
        mt._catch_up(fh)

    evs = mt.instruments["GCZ6.COMEX@RITHMIC"].mbo_events
    # `old_1` skipped (> window), `gz_1` BID_NEW + REPLACE replayed ONCE (not duplicated),
    # and `REPLACE` inherited side="BID" + old_size=12.0 from the archived `gz_1` BID_NEW!
    assert len(evs) == 2
    assert evs[0]["order_id"] == "gz_1" and evs[0]["type"] == "BID_NEW" and evs[0]["side"] == "BID"
    assert evs[1]["order_id"] == "gz_1" and evs[1]["type"] == "REPLACE"
    assert evs[1]["side"] == "BID"
    assert evs[1]["old_size"] == pytest.approx(12.0)


# --------------------------------------------------------------------------- #
# 4. Dynamic Depth Levels + Fast M1 Candles Parity + Crossed-Book Fail-Closed
# --------------------------------------------------------------------------- #
def test_stage_b1_dynamic_depth_levels_fast_m1_parity_and_crossed_book_guard(monkeypatch):
    """`BOOKMAP_MAX_DEPTH_LEVELS` updates dynamically without re-import, `fast_m1_candles`
    matches `BaseProvider.trades_to_candles` bit-for-bit, and `crossed_book` still fails closed."""
    base_dt = datetime(2026, 11, 6, 13, 0, 0, tzinfo=timezone.utc)
    now_dt = base_dt + timedelta(minutes=6, seconds=20)
    monkeypatch.setattr(bbp, "_now_utc_epoch", lambda: now_dt.timestamp())

    inst = "GCZ6.COMEX@RITHMIC"
    bt = _bare_bridge_tail()
    # 75 bid levels and 75 ask levels
    for i in range(75):
        ts_d = (now_dt - timedelta(seconds=2)).isoformat()
        bt._handle_line(f"{ts_d},DepthBid,{2700.0 - 0.1 * i:.1f},5,{i},Update,{inst}\n")
        bt._handle_line(f"{ts_d},DepthAsk,{2700.2 + 0.1 * i:.1f},5,{i},Update,{inst}\n")
    # 25 trades across 7 M1 buckets
    for k in range(25):
        ts_t = (base_dt + timedelta(seconds=k * 15)).isoformat()
        px_t = 2700.0 + (k % 5) * 0.1
        bt._handle_line(f"{ts_t},Last,{px_t:.1f},{1 + (k % 3)},0,BUY,{inst}\n")

    monkeypatch.setattr(config, "BOOKMAP_MAX_DEPTH_LEVELS", 65)
    prov = _offline_provider(bt, None)
    md = prov.acquire(symbol=inst)

    assert len(md["bid_depth"]) == 65
    assert len(md["ask_depth"]) == 65
    assert md["data_quality"]["level2"] == "validated"

    # Exact OHLCV parity between fast_m1_candles and legacy string-parsed trades_to_candles
    ref_candles = trades_to_candles(prov._trades, 60)
    for col in ("open", "high", "low", "close", "volume"):
        np.testing.assert_allclose(md["candles"][col], ref_candles[col], rtol=0, atol=1e-9)

    # Crossed book MUST still fail closed (bid == ask == 0.0, empty depth)
    bt._handle_line(f"{now_dt.isoformat()},DepthBid,2701.0,9,0,Update,{inst}\n")
    md_crossed = prov.acquire(symbol=inst)
    assert md_crossed["data_quality"]["level2"] == "crossed_book"
    assert md_crossed["data_quality"]["quote"] == "crossed"
    assert md_crossed["bid"] == 0.0 and md_crossed["ask"] == 0.0
    assert md_crossed["bid_depth"] == {} and md_crossed["ask_depth"] == {}


# --------------------------------------------------------------------------- #
# 5. Law #11 M3 <-> M5 Window Scaling & Raw-Replay [8i] End-to-End Scorecard
# --------------------------------------------------------------------------- #
def test_stage_b1_law11_mbo_window_and_replay_scorecard_8i(tmp_path, monkeypatch):
    """`_active_mbo_window_seconds` scales cleanly across timeframes and `replay_single_day`
    -> `synthesize_multi_day` renders `[8i] SECTION #10` with real MBO span & near-corridor stats."""
    import tools.raw_replay as rr

    try:
        monkeypatch.delenv("A1_BASE_TF_MINUTES", raising=False)
        monkeypatch.delenv("POWER_BAR_SECONDS", raising=False)
        monkeypatch.setenv("DATA_SOURCE", "replay")
        monkeypatch.setenv("STALE_DATA_SECONDS", "120")
        monkeypatch.setenv("ORDER_FLOW_WINDOW_SECONDS", "120")

        monkeypatch.setenv("TIMEFRAME", "M3")
        config._refresh()
        assert bbp._active_mbo_window_seconds() == pytest.approx(180.0)

        monkeypatch.setenv("TIMEFRAME", "M5")
        config._refresh()
        assert bbp._active_mbo_window_seconds() == pytest.approx(300.0)
    finally:
        config.reload_env()

    # Build a causal synthetic raw `ticks.csv` + `mbo.csv` in `tmp_path` (`12:00-12:06` Budapest = `11:00-11:06Z`)
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    inst = "GCZ6.COMEX@RITHMIC"
    t_lines = ["time,event,price,size,level,operation,instrument\n"]
    m_lines = ["time,event_type,order_id,price,size,instrument\n"]
    start_dt = datetime(2026, 11, 6, 10, 56, 0, tzinfo=timezone.utc)
    for sec in range(0, 540, 10):
        dt_s = start_dt + timedelta(seconds=sec)
        ts_s = dt_s.strftime("%Y-%m-%dT%H:%M:%SZ")
        px = 2700.0 + ((sec // 10) % 2) * 0.1
        t_lines.append(f"{ts_s},DepthBid,2699.9,15,0,Update,{inst}\n")
        t_lines.append(f"{ts_s},DepthAsk,2700.2,15,0,Update,{inst}\n")
        t_lines.append(f"{ts_s},Last,{px:.1f},2,0,BUY,{inst}\n")
        # 3 near-corridor ($2700) + 1 far-corridor ($2670) MBO events every 10s -> 75% near share!
        m_lines.append(f"{ts_s},BID_NEW,n1_{sec},{px - 0.5:.1f},8,{inst}\n")
        m_lines.append(f"{ts_s},ASK_NEW,n2_{sec},{px + 0.5:.1f},8,{inst}\n")
        m_lines.append(f"{ts_s},BID_NEW,n3_{sec},{px - 1.0:.1f},6,{inst}\n")
        m_lines.append(f"{ts_s},BID_NEW,far_{sec},2670.0,3,{inst}\n")

    (tmp_path / "ticks.csv").write_text("".join(t_lines), encoding="utf-8")
    (tmp_path / "mbo.csv").write_text("".join(m_lines), encoding="utf-8")

    day_res = rr.replay_single_day(
        "2026-11-06", tmp_path, data_dir, every_s=60, window_str="12:00-12:04", prewarm_min=10
    )
    assert day_res["ok"] is True
    assert len(day_res["cycles"]) >= 3
    # Full MBO span across 3+ minutes (>150s, proving >13s truncation is gone!)
    assert max(c["mbo_span_s"] for c in day_res["cycles"]) >= 150.0

    txt, _html, _meta = rr.synthesize_multi_day([day_res])
    assert "[8i] SECTION #10: STEP 1 LIVE BOOKMAP BRIDGE & FEED INTEGRITY SCORECARD (Stage B1)" in txt
    assert "Near_Corridor_Share=75.0%" in txt
    assert "Quote Integrity (" in txt
    assert "L2 Book Hygiene (" in txt


def test_stage_b1b_raw_replay_preserves_same_ms_depth_remove_and_dedupes_chunk_overlap(
    tmp_path, monkeypatch
):
    """Stage B1b: Same-ms `Remove -> Update(k) -> Remove` transitions must never be dropped
    intra-file (preventing 15m zombie `crossed_book` levels), while true cross-file suffix-prefix
    seam overlaps and `.csv`/`.csv.gz` duplicate chunk stems are cleanly deduplicated.
    """
    import tools.raw_replay as rr

    data_dir = tmp_path / "data"
    arch_dir = data_dir / "archive"
    arch_dir.mkdir(parents=True)
    inst = "GCZ6.COMEX@RITHMIC"

    header_t = "time,event,price,size,level,operation,instrument\n"
    header_m = "time,event_type,order_id,price,size,instrument\n"

    chunk_lines = [header_t]
    mbo_lines = [header_m]
    start_dt = datetime(2026, 11, 6, 10, 56, 0, tzinfo=timezone.utc)
    for sec in range(0, 300, 10):
        dt_s = start_dt + timedelta(seconds=sec)
        ts_s = dt_s.strftime("%Y-%m-%dT%H:%M:%S.100Z")
        chunk_lines.append(f"{ts_s},DepthBid,2700.0000,15.0000,-1,Update,{inst}\n")
        chunk_lines.append(f"{ts_s},DepthAsk,2700.2000,15.0000,-1,Update,{inst}\n")
        chunk_lines.append(f"{ts_s},Last,2700.1000,2.0000,-1,Buy,{inst}\n")
        mbo_lines.append(f"{ts_s},BID_NEW,oid_{sec},2700.0,10,{inst}\n")

    # Write BOTH `.csv` and `.csv.gz` of the same chunk stem to verify stem dedup
    (tmp_path / "ticks_20261106_110100.csv").write_text("".join(chunk_lines), encoding="utf-8")
    with gzip.open(arch_dir / "ticks_20261106_110100.csv.gz", "wt", encoding="utf-8", newline="\n") as gz:
        gz.writelines(chunk_lines)

    # Live `ticks.csv` overlaps the final 3 data lines of the chunk AND then executes a
    # same-millisecond `Remove -> Update(3) -> Remove` at 2700.2000 before sweeping up to 2700.4!
    live_lines = [header_t] + chunk_lines[-3:]
    sweep_ms = "2026-11-06T11:01:05.123Z"
    live_lines.extend([
        f"{sweep_ms},DepthAsk,2700.2000,0.0000,-1,Remove,{inst}\n",
        f"{sweep_ms},DepthAsk,2700.2000,3.0000,-1,Update,{inst}\n",
        f"{sweep_ms},DepthAsk,2700.2000,0.0000,-1,Remove,{inst}\n",
        # Same-ms institutional split fills (3 x 4.0 lots in identical millisecond)
        f"{sweep_ms},Last,2700.2000,4.0000,-1,Buy,{inst}\n",
        f"{sweep_ms},Last,2700.2000,4.0000,-1,Buy,{inst}\n",
        f"{sweep_ms},Last,2700.2000,4.0000,-1,Buy,{inst}\n",
        f"{sweep_ms},DepthBid,2700.3000,12.0000,-1,Update,{inst}\n",
        f"{sweep_ms},DepthAsk,2700.5000,12.0000,-1,Update,{inst}\n",
    ])
    for sec in range(310, 540, 10):
        dt_s = start_dt + timedelta(seconds=sec)
        ts_s = dt_s.strftime("%Y-%m-%dT%H:%M:%S.100Z")
        live_lines.append(f"{ts_s},DepthBid,2700.3000,12.0000,-1,Update,{inst}\n")
        live_lines.append(f"{ts_s},DepthAsk,2700.5000,12.0000,-1,Update,{inst}\n")
        live_lines.append(f"{ts_s},Last,2700.4000,2.0000,-1,Buy,{inst}\n")
        mbo_lines.append(f"{ts_s},BID_NEW,oid_{sec},2700.3,10,{inst}\n")

    (tmp_path / "ticks.csv").write_text("".join(live_lines), encoding="utf-8")
    # Also plant a stale conflicting `data/ticks.csv` with a poisonous 2705.0 bid to prove
    # `_discover_raw_files` selects ONLY the single primary `ticks.csv`!
    (data_dir / "ticks.csv").write_text(
        header_t + f"2026-11-06T11:01:06.000Z,DepthBid,2705.0000,99.0000,-1,Update,{inst}\n",
        encoding="utf-8",
    )
    (tmp_path / "mbo.csv").write_text("".join(mbo_lines), encoding="utf-8")

    disc = rr._discover_raw_files("ticks", tmp_path, data_dir, day_str="2026-11-06", use_env_paths=False)
    assert len(disc) == 2  # 1 deduplicated chunk + 1 primary live `ticks.csv`

    # Verify exact stream count: 3 overlapping seam lines dropped once, all 3 same-ms split fills kept
    streamed = list(
        rr._iter_filtered_csv_events(
            disc,
            start_dt.timestamp(),
            (start_dt + timedelta(minutes=10)).timestamp(),
            {"2026-11-06"},
            inst_filter=inst,
        )
    )
    split_fills = [ln for _, ln in streamed if f"{sweep_ms},Last,2700.2000,4.0000" in ln]
    assert len(split_fills) == 3

    day_res = rr.replay_single_day(
        "2026-11-06", tmp_path, data_dir, every_s=60, window_str="12:00-12:04", prewarm_min=10
    )
    assert day_res["ok"] is True
    assert all(c["quote_status"] == "valid" for c in day_res["cycles"])
    assert all(c["l2_status"] == "validated" for c in day_res["cycles"])
    txt, _, _ = rr.synthesize_multi_day([day_res])
    assert "Per-Day Crossed: 2026-11-06=0/" in txt
