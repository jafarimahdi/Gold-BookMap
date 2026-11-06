"""Stage P2 unit, integration & replay tests (`2026-11-06`):
1) Preserves tick-only `NOT_EMITTED` contract when `order_events` is omitted (`None`).
2) Emits validated `l3_aggr_limit` from near-touch MBO passive queue flow + matched-horizon
   direct executed trade flow (Cont, Kukanov & Stoikov 2014), raising POWER coverage to 1.00.
3) Rejects deep-book spoofing (> near_touch_usd), invalid MBO order_ids, local-receive
   timestamps, orphan modifications without `old_size`, and post-bar look-ahead events.
4) Applies Kyle's lambda / passive absorption damping (Easley et al. 2012; Bouchaud 2018)
   when aggressive + MBO flow pushes one way while bar price displaces >= 2 ticks against it.
5) Wires `order_events` cleanly through `step2_market_analysis.analyze_market` (`:00` bell + `:01` A1
   serve parity + `POWER_L3_MBO_SENSOR_ENABLED=False` toggle + synthetic-demo L3 rejection)
   and verifies `[8f]` `l3_valid` reporting in `tools/raw_replay.py`.
"""
from datetime import datetime, timedelta, timezone
import gzip
from pathlib import Path

import config
import step2_market_analysis as s2
from power_m5_adapter import build_m5_inputs
from power_team_v2 import decide
from power_v2_shadow import run_power_v2
from tools import raw_replay as rr


def _make_m3_ticks_and_mbo(
    bell_dt: datetime,
    *,
    upward: bool = True,
    adverse_price_displacement: bool = False,
):
    ticks = []
    # Previous M3 window [bell-6m, bell-3m): balanced/mildly opposite so cvd_momentum is valid
    for k in range(16):
        ts = bell_dt - timedelta(seconds=350 - k * 10)
        ticks.append({
            "timestamp": ts.isoformat().replace("+00:00", "Z"),
            "price": round(4050.0 + (k % 3) * 0.10, 2),
            "volume": 2.0,
            "side": "SELL" if upward else "BUY",
            "is_direct": True,
        })
    # Current M3 window [bell-3m, bell): 18 direct prints (~60 lots)
    for k in range(18):
        ts = bell_dt - timedelta(seconds=170 - k * 9)
        if adverse_price_displacement:
            # Buyers sweep volume, yet passive sellers push bar price DOWN by 0.60 pts (6 ticks)
            px = round(4051.0 - 0.04 * k, 2) if upward else round(4050.0 + 0.04 * k, 2)
        else:
            px = round(4050.2 + 0.06 * k, 2) if upward else round(4051.2 - 0.06 * k, 2)
        vol = 8.0 if k in (2, 10) else 2.5
        side = ("BUY" if k % 5 != 0 else "SELL") if upward else ("SELL" if k % 5 != 0 else "BUY")
        ticks.append({
            "timestamp": ts.isoformat().replace("+00:00", "Z"),
            "price": px,
            "volume": vol,
            "side": side,
            "is_direct": True,
        })

    mbo = []
    for j in range(12):
        ts = bell_dt - timedelta(seconds=150 - j * 11)
        ref_px = 4050.2 + (0.06 * j if not adverse_price_displacement else -0.04 * j)
        if upward:
            # Passive bids step in near touch + passive asks pull near touch
            mbo.append({
                "timestamp": ts.isoformat().replace("+00:00", "Z"),
                "type": "BID_NEW",
                "side": "BID",
                "price": round(ref_px - 0.10, 2),
                "size": 6.0,
                "order_id": f"b_{100 + j}",
            })
            if j % 2 == 0:
                mbo.append({
                    "timestamp": (ts + timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                    "type": "ASK_CANCEL",
                    "side": "ASK",
                    "price": round(ref_px + 0.20, 2),
                    "size": 4.0,
                    "order_id": f"a_{200 + j}",
                })
        else:
            mbo.append({
                "timestamp": ts.isoformat().replace("+00:00", "Z"),
                "type": "ASK_NEW",
                "side": "ASK",
                "price": round(ref_px + 0.10, 2),
                "size": 6.0,
                "order_id": f"a_{300 + j}",
            })
            if j % 2 == 0:
                mbo.append({
                    "timestamp": (ts + timedelta(seconds=2)).isoformat().replace("+00:00", "Z"),
                    "type": "BID_CANCEL",
                    "side": "BID",
                    "price": round(ref_px - 0.20, 2),
                    "size": 4.0,
                    "order_id": f"b_{400 + j}",
                })
    return ticks, mbo


def test_stage_p2_adapter_emits_l3_aggr_limit_and_preserves_not_emitted_default():
    bell = datetime(2026, 11, 6, 15, 0, 0, tzinfo=timezone.utc)
    ticks, mbo = _make_m3_ticks_and_mbo(bell, upward=True)

    # 1) Omitting order_events preserves exact tick-only NOT_EMITTED contract
    tick_only = build_m5_inputs(ticks, now=bell, tick_size=0.10, min_trades=12, bar_seconds=180)
    assert "l3_aggr_limit" not in tick_only["judges"]
    assert tick_only["judge_availability"]["l3_aggr_limit"]["status"] == "NOT_EMITTED"

    # 2) Supplying validated near-touch MBO order_events emits l3_aggr_limit & reaches 1.00 coverage
    with_mbo = build_m5_inputs(
        ticks,
        now=bell,
        tick_size=0.10,
        min_trades=12,
        bar_seconds=180,
        order_events=mbo,
        min_mbo_events=4,
    )
    assert with_mbo["judge_availability"]["l3_aggr_limit"]["status"] == "VALID"
    l3_reading = with_mbo["judges"]["l3_aggr_limit"]
    assert l3_reading["value"] > 0.20
    assert l3_reading["quality"] >= 0.80
    assert with_mbo["diagnostics"]["l3_aggr_limit_absorption_damped"] is False

    verdict = decide(with_mbo["judges"], context={**with_mbo["context"], "market_regime": "TREND"})
    assert verdict["direction"] == "UP"
    assert verdict["coverage"] >= 0.95
    assert "l3_aggr_limit" in verdict["valid_judges"]


def test_stage_p2_rejects_deep_spoofs_bad_oids_future_events_and_damps_absorption():
    bell = datetime(2026, 11, 6, 15, 3, 0, tzinfo=timezone.utc)
    ticks, _ = _make_m3_ticks_and_mbo(bell, upward=True)

    # Inject only untrusted / out-of-range / post-bell MBO rows -> must fail closed to MISSING
    hostile_mbo = [
        # Deep-book spoof $12 away from price (> $2.50 near_touch_usd)
        {"timestamp": (bell - timedelta(seconds=20)).isoformat(), "type": "ASK_NEW",
         "side": "ASK", "price": 4065.0, "size": 250.0, "order_id": "spoof_1"},
        # Invalid fallback order_id "-1"
        {"timestamp": (bell - timedelta(seconds=18)).isoformat(), "type": "BID_NEW",
         "side": "BID", "price": 4050.8, "size": 50.0, "order_id": "-1"},
        # Untrusted local receive timestamp
        {"timestamp": (bell - timedelta(seconds=15)).isoformat(), "type": "BID_NEW",
         "side": "BID", "price": 4050.8, "size": 50.0, "order_id": "loc_1",
         "timestamp_source": "local_receive"},
        # Orphan REPLACE without explicit old_size
        {"timestamp": (bell - timedelta(seconds=12)).isoformat(), "type": "BID_REPLACE",
         "side": "BID", "price": 4050.8, "size": 50.0, "order_id": "orph_1"},
        # Future look-ahead event after evaluation_end
        {"timestamp": (bell + timedelta(seconds=2)).isoformat(), "type": "BID_NEW",
         "side": "BID", "price": 4050.8, "size": 50.0, "order_id": "fut_1"},
    ]
    built_bad = build_m5_inputs(
        ticks, now=bell, tick_size=0.10, min_trades=12, bar_seconds=180,
        order_events=hostile_mbo, min_mbo_events=4,
    )
    assert "l3_aggr_limit" not in built_bad["judges"]
    assert built_bad["judge_availability"]["l3_aggr_limit"]["status"] == "MISSING"
    assert built_bad["diagnostics"]["mbo_near_touch_events"] == 0

    # Passive absorption check: buyers lift & bids post, yet price drops 6 ticks -> damped
    ticks_abs, mbo_abs = _make_m3_ticks_and_mbo(
        bell, upward=True, adverse_price_displacement=True
    )
    built_abs = build_m5_inputs(
        ticks_abs, now=bell, tick_size=0.10, min_trades=12, bar_seconds=180,
        order_events=mbo_abs, min_mbo_events=4,
    )
    built_clean = build_m5_inputs(
        ticks, now=bell, tick_size=0.10, min_trades=12, bar_seconds=180,
        order_events=mbo_abs, min_mbo_events=4,
    )
    assert built_abs["diagnostics"]["l3_aggr_limit_absorption_damped"] is True
    assert built_abs["judges"]["l3_aggr_limit"]["value"] < 0.5 * built_clean["judges"]["l3_aggr_limit"]["value"]


def test_stage_p2_step2_wires_mbo_sensor_and_respects_toggle_and_synthetic_guard(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(config, "TIMEFRAME", "M3", raising=False)
    monkeypatch.setattr(config, "BASE_TF_MINUTES", 3, raising=False)
    monkeypatch.setattr(config, "A1_BASE_TF_MINUTES", 3, raising=False)
    monkeypatch.setattr(config, "POWER_BAR_SECONDS", 180, raising=False)
    monkeypatch.setattr(config, "POWER_BELL_GRACE_SECONDS", 0.0, raising=False)
    monkeypatch.setattr(config, "POWER_REGIME_BAR_CACHE_ENABLED", False, raising=False)
    monkeypatch.setattr(config, "POWER_L3_MBO_SENSOR_ENABLED", True, raising=False)
    s2._A1_BAR_SERVICE_CACHE.clear()

    bell = datetime(2026, 11, 6, 15, 6, 0, tzinfo=timezone.utc)
    start = bell - timedelta(minutes=100)
    ticks, opens, highs, lows, closes, vols = [], [], [], [], [], []
    for m in range(100):
        bar_t = start + timedelta(minutes=m)
        base_px = 4020.0 + 0.25 * m
        opens.append(base_px)
        highs.append(base_px + 0.40)
        lows.append(base_px - 0.10)
        closes.append(base_px + 0.25)
        vols.append(35.0)
        in_last_m3 = (bell - timedelta(minutes=3)) <= bar_t < bell
        n_p = 8 if in_last_m3 else 4
        for k in range(n_p):
            ts = bar_t + timedelta(seconds=4 + k * 6)
            ticks.append({
                "timestamp": ts.isoformat().replace("+00:00", "Z"),
                "price": round(base_px + 0.03 * k, 2),
                "volume": 6.0 if (in_last_m3 and k == 0) else 2.0,
                "side": "BUY" if in_last_m3 else ("BUY" if k % 2 == 0 else "SELL"),
                "is_direct": True,
            })
    mid = closes[-1]
    mbo = [
        {
            "timestamp": (bell - timedelta(seconds=120 - j * 8)).isoformat().replace("+00:00", "Z"),
            "type": "BID_NEW",
            "side": "BID",
            "price": round(mid - 0.20, 2),
            "size": 8.0,
            "order_id": f"live_oid_{j}",
        }
        for j in range(10)
    ]
    md = {
        "symbol": "GCZ6.COMEX@RITHMIC",
        "price": mid,
        "bid": round(mid - 0.10, 2),
        "ask": round(mid + 0.10, 2),
        "spread_pct": 0.005,
        "volume": 3500.0,
        "tick_size": 0.10,
        "candles": {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols},
        "tick_data": ticks,
        "order_events": mbo,
        "news": {"headlines": [], "events": [], "fetch_calendar": False},
        "data_quality": {"source": "replay", "trade_prints": "available", "quote": "valid", "level3": "available"},
    }
    snap = s2.analyze_market(md, now=bell)
    assert snap.power_v2["judge_availability"]["l3_aggr_limit"]["status"] == "VALID"
    assert "l3_aggr_limit" in snap.power_v2["valid_judges"]
    assert snap.power_v2["coverage"] >= 0.99
    assert snap.power_v2["direction"] == "UP"

    # Intra-bar :01 serve preserves cached A1 POWER verdict & VALID l3_aggr_limit
    snap_serve = s2.analyze_market(dict(md, price=mid + 0.30), now=bell + timedelta(seconds=60))
    assert "A1 bar-service:" in " ".join(snap_serve.notes or [])
    assert snap_serve.power_v2["judge_availability"]["l3_aggr_limit"]["status"] == "VALID"

    # Synthetic demo L3 must never emit into POWER v2
    s2._A1_BAR_SERVICE_CACHE.clear()
    md_synth = dict(md, data_quality={**md["data_quality"], "level3": "synthetic"})
    snap_synth = s2.analyze_market(md_synth, now=bell)
    assert snap_synth.power_v2["judge_availability"]["l3_aggr_limit"]["status"] == "NOT_EMITTED"

    # Explicit config toggle disables Stage P2 cleanly for A/B comparison
    s2._A1_BAR_SERVICE_CACHE.clear()
    monkeypatch.setattr(config, "POWER_L3_MBO_SENSOR_ENABLED", False, raising=False)
    snap_off = s2.analyze_market(md, now=bell)
    assert snap_off.power_v2["judge_availability"]["l3_aggr_limit"]["status"] == "NOT_EMITTED"


def test_stage_p2_raw_replay_reports_l3_valid_on_active_mbo_day(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TIMEFRAME", "M3")
    monkeypatch.setenv("A1_BASE_TF_MINUTES", "3")
    monkeypatch.setattr(config, "TIMEFRAME", "M3", raising=False)
    monkeypatch.setattr(config, "BASE_TF_MINUTES", 3, raising=False)
    monkeypatch.setattr(config, "A1_BASE_TF_MINUTES", 3, raising=False)
    monkeypatch.setattr(config, "POWER_BAR_SECONDS", 180, raising=False)
    monkeypatch.setattr(config, "POWER_BELL_GRACE_SECONDS", 0.0, raising=False)
    monkeypatch.setattr(config, "POWER_L3_MBO_SENSOR_ENABLED", True, raising=False)
    monkeypatch.setattr(s2, "A1_BASE_TF_MINUTES", 3, raising=False)

    data_dir = tmp_path / "data"
    arch = data_dir / "archive"
    arch.mkdir(parents=True, exist_ok=True)
    day_str = "2026-10-29"
    d_compact = "20261029"
    base_dt = datetime.fromisoformat("2026-10-29T09:00:00+00:00")
    inst = "GC 12-26"

    t_lines = ["time,event,price,size,level,operation,instrument\n"]
    m_lines = ["time,event_type,order_id,price,size,instrument\n"]
    px = 4000.0
    oid = 1000
    prev_bids, prev_asks = [], []
    for m in range(110):
        cur_dt = base_dt + timedelta(minutes=m)
        px = round(px + 0.20, 2)
        bid, ask = round(px - 0.10, 2), round(px + 0.10, 2)
        iso0 = (cur_dt + timedelta(seconds=2)).isoformat()
        new_bids = [round(bid - 0.10 * i, 2) for i in range(5)]
        new_asks = [round(ask + 0.10 * i, 2) for i in range(5)]
        for ob in prev_bids:
            if ob not in new_bids:
                t_lines.append(f"{iso0},DepthBid,{ob:.2f},0,0,Remove,{inst}\n")
        for oa in prev_asks:
            if oa not in new_asks:
                t_lines.append(f"{iso0},DepthAsk,{oa:.2f},0,0,Remove,{inst}\n")
        for i, (bp, ap) in enumerate(zip(new_bids, new_asks)):
            t_lines.append(f"{iso0},DepthBid,{bp:.2f},{15 + i * 2},{i},Update,{inst}\n")
            t_lines.append(f"{iso0},DepthAsk,{ap:.2f},{9 + i * 2},{i},Update,{inst}\n")
        prev_bids, prev_asks = new_bids, new_asks
        # 5 prints/min -> 15 prints per M3 bar (>= 12 min_trades)
        for k in range(5):
            ts_p = (cur_dt + timedelta(seconds=8 + k * 10)).isoformat()
            side_s = "Buy" if k < 4 else "Sell"
            p_trd = ask if side_s == "Buy" else bid
            t_lines.append(f"{ts_p},Last,{p_trd:.2f},3,0,{side_s},{inst}\n")
        # 3 MBO events/min -> 9 MBO events per M3 bar (>= 4 min_mbo_events)
        for j in range(3):
            oid += 1
            ts_m = (cur_dt + timedelta(seconds=6 + j * 15)).isoformat()
            m_lines.append(f"{ts_m},BID_NEW,oid_{oid},{bid:.2f},6,{inst}\n")

    with gzip.open(arch / f"ticks_{d_compact}_160000.csv.gz", "wt", encoding="utf-8", newline="\n") as fh:
        fh.writelines(t_lines)
    with gzip.open(arch / f"mbo_{d_compact}_160000.csv.gz", "wt", encoding="utf-8", newline="\n") as fh:
        fh.writelines(m_lines)

    res = rr.replay_single_day(
        day_str=day_str, root_dir=tmp_path, data_dir=data_dir,
        every_s=60, window_str="10:00-11:30",
    )
    assert res["ok"] is True
    txt, _html, _meta = rr.synthesize_multi_day([res])
    assert "l3_valid=" in txt
    assert "l3_not_emitted=0/" in txt
    assert "l3_aggr_limit: N=" in txt and "l3_aggr_limit: N=0" not in txt
