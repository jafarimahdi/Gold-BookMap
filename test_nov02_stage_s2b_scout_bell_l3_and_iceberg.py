"""
Stage S2b Regression Tests (2026-11-02):
  1. `analyze_market` (`:00` Bell compute) passes analyzed `level3` (`Level3Events`
     carrying `.spoof_levels`, `.iceberg_levels`, `.iceberg_meta`, `.order_book_imbalance`)
     instead of raw `l3` (`Level3OrderBookAnalyzer`) into `build_signal_map`, fixing the
     `replay-18` `[8e]` finding (`Bell(:00) spoof_cyc=0` vs `Serve(:01/:02) spoof_cyc=126`).
  2. `signal_team.LevelBook.ingest` reads clip-reload counts from `iceberg_meta[p]["refills"]`
     when `iceberg_levels[p]` stores event count `1`, enforces `<=180s` freshness, and
     clears exhausted (`resting == 0`) or breached (`price > ask_ice + spread`) icebergs
     so dead icebergs never block `Stage 4c` corridors (*Frey & Sandås 2017*).
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import config
import signal_team as st
import step2_market_analysis as s2


def _cfg():
    return SimpleNamespace(
        L3_WHALE_THRESHOLD=10.0,
        SCOUT_ATR_FLOOR_PCT=0.0004,
        SCOUT_MIN_SPREADS=10.0,
        SCOUT_MIN_TARGET_USD=0.0,
        ICEBERG_MIN_REFILLS=3.0,
    )


def test_stage_s2b_clip_reload_iceberg_freshness_and_breach_cleanup():
    book = st.LevelBook()
    cfg = _cfg()
    t0 = 1_793_600_000.0

    # Cycle 1: Active ask iceberg at 4002.0 with clip-reload meta (`iceberg_levels=1`, `meta.refills=4`)
    # and outer whale target at 4006.0.
    l3_live = SimpleNamespace(
        order_book={
            "bids": [(3999.5 - 0.5 * i, 4.0) for i in range(6)],
            "asks": [(4000.5, 4.0), (4001.0, 4.0), (4002.0, 5.0), (4005.5, 4.0), (4006.0, 25.0), (4006.5, 4.0)],
        },
        spoof_levels={},
        iceberg_levels={4002.0: 1},
        iceberg_meta={4002.0: {"refills": 4, "last_seen": t0 - 15.0, "age_sec": 40.0}},
        large_order_events=0,
        order_book_imbalance=0.0,
        ofi=0.0,
    )
    smap1 = book.ingest(l3_live, price=4000.0, atr=4.0, config=cfg, spread=0.20, now=t0)
    assert "iceberg" in smap1["tracked_sources"]
    road1 = book.road_to(4000.0, 4006.0, whale_thr=10.0)
    assert road1["iceberg_in_path"] is True
    assert road1["road_clear"] is False

    # Cycle 2 (+60s): Iceberg at 4002.0 is completely exhausted in the live book (0 lots at 4002.0)
    l3_exhausted = SimpleNamespace(
        order_book={
            "bids": [(3999.5 - 0.5 * i, 4.0) for i in range(6)],
            "asks": [(4000.5, 4.0), (4001.0, 4.0), (4005.5, 4.0), (4006.0, 25.0), (4006.5, 4.0)],
        },
        spoof_levels={},
        iceberg_levels={},
        iceberg_meta={},
        large_order_events=0,
        order_book_imbalance=0.0,
        ofi=0.0,
    )
    smap2 = book.ingest(l3_exhausted, price=4000.0, atr=4.0, config=cfg, spread=0.20, now=t0 + 60.0)
    assert "iceberg" not in smap2["tracked_sources"]
    road2 = book.road_to(4000.0, 4006.0, whale_thr=10.0)
    assert road2["iceberg_in_path"] is False
    assert road2["road_clear"] is True

    # Cycle 3 (+120s): Stale iceberg (`last_seen` 240s > 180s ago) is NOT admitted
    l3_stale = SimpleNamespace(
        order_book={
            "bids": [(3999.5 - 0.5 * i, 4.0) for i in range(6)],
            "asks": [(4000.5, 4.0), (4002.0, 5.0), (4005.5, 4.0), (4006.0, 25.0), (4006.5, 4.0)],
        },
        spoof_levels={},
        iceberg_levels={4002.0: 5},
        iceberg_meta={4002.0: {"refills": 5, "last_seen": (t0 + 120.0) - 240.0, "age_sec": 300.0}},
        large_order_events=0,
        order_book_imbalance=0.0,
        ofi=0.0,
    )
    smap3 = book.ingest(l3_stale, price=4000.0, atr=4.0, config=cfg, spread=0.20, now=t0 + 120.0)
    assert "iceberg" not in smap3["tracked_sources"]


def test_stage_s2b_analyze_market_bell_passes_level3_spoof_and_imbalance(monkeypatch):
    monkeypatch.setattr(config, "TIMEFRAME", "M3", raising=False)
    monkeypatch.setattr(config, "A1_BASE_TF_MINUTES", 3, raising=False)
    monkeypatch.setattr(config, "L3_WHALE_THRESHOLD", 10.0, raising=False)
    st._BOOK = None
    s2._A1_BAR_SERVICE_CACHE.clear()
    s2._PERSISTENT_L2 = None

    t_bell = datetime(2026, 10, 30, 10, 0, 2, tzinfo=timezone.utc)
    ep = t_bell.timestamp()

    # Seed wall at 4005.0 on prior cycle so spoof event on `:00` Bell bans it immediately
    st.get_book().ingest(
        SimpleNamespace(
            order_book={
                "bids": [(3999.5, 4.0), (3999.0, 4.0)],
                "asks": [(4004.5, 4.0), (4005.0, 30.0), (4005.5, 4.0)],
            },
            spoof_levels={},
            iceberg_levels={},
            iceberg_meta={},
            large_order_events=0,
            order_book_imbalance=0.0,
            ofi=0.0,
        ),
        price=4000.0,
        atr=4.0,
        config=config,
        spread=0.20,
        now=ep - 60.0,
    )
    assert 4005.0 in st.get_book().levels
    assert st.get_book().levels[4005.0].spoofed is False

    # Feed a fast-cancel spoof MBO sequence at 4005.0 (`ADD` then `CANCEL` 0.3s later, >= spoof size)
    # on a full `:00` Bell `analyze_market` compute!
    m3_candles = {
        "open": [4000.0] * 25,
        "high": [4002.0] * 25,
        "low": [3998.0] * 25,
        "close": [4000.0] * 25,
        "volume": [100.0] * 25,
    }
    md = {
        "symbol": "GCG6",
        "price": 4000.0,
        "bid": 3999.9,
        "ask": 4000.1,
        "volume": 50.0,
        "candles": m3_candles,
        "candles_tf": m3_candles,
        "bid_depth": {3999.9: 12.0, 3999.5: 8.0, 3999.0: 8.0},
        "ask_depth": {4000.1: 4.0, 4004.5: 4.0, 4005.0: 30.0, 4005.5: 4.0},
        "l2_timestamp": ep - 0.2,
        "tick_data": [{"timestamp": ep - 1.0, "price": 4000.0, "volume": 2.0, "side": "BUY"}],
        "order_events": [
            {"timestamp": ep - 1.5, "type": "NEW", "side": "ASK", "price": 4005.0, "size": 120.0, "order_id": "sp1"},
            {"timestamp": ep - 1.4, "type": "NEW", "side": "BID", "price": 3999.9, "size": 18.0, "order_id": "b1"},
            {"timestamp": ep - 1.2, "type": "CANCEL", "side": "ASK", "price": 4005.0, "size": 120.0, "order_id": "sp1"},
        ],
        "has_data": True,
        "data_quality": {"source": "replay", "trade_prints": "available", "quote": "valid"},
    }
    snap = s2.analyze_market(md, now=t_bell)
    assert snap.level3.spoof_events >= 1
    assert 4005.0 in snap.level3.spoof_levels
    assert snap.signal_map.get("banned_spoof", 0) >= 1
    assert st.get_book().levels[4005.0].spoofed is True
    # Also `context.book_imbalance` is populated on `:00` Bell (was always 0.0 when raw `l3` was passed)
    assert snap.signal_map.get("context", {}).get("book_imbalance") != 0.0
