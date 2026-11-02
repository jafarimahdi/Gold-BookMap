"""Stage 4a (2026-11-02): Entry SL/TP Bracket Geometry, Bookmark #8 & [8c] Exit Scorecard.

Verifies strictly isolated Stage-4a upgrades per Holy Book Law #9:
1. Sub-ATR near-touch `shelter_behind` quotes (`< 1.5x ATR`) no longer create `$0.60` stop-hunt
   traps (`9/18 = 33.3% WR, -18.7 pts` in `replay-10`); genuine `1.5x..2.5x ATR` shelters remain `BOOK_SHELTER`.
2. Routine `<40`-lot front-porch quotes closer than `min_rr * risk` (`108` Sniper waves in `replay-10`!)
   scan forward to (a) a viable further Scout door, (b) a viable structural memory door (`POC/VA/OB`),
   or (c) on Stage-3d Sniper waves with clean corridors (`Crowd v2 PASS`, `road_clear`), the `3.5x ATR` wave target.
3. Major opposing walls (`>= 40` lots or active `iceberg`) closer than `min_rr * risk` fail closed (`WAIT`)
   and preserve their exact geometry reason in `why[1]` (`AGGRESSIVE: ...`).
4. Bookmark #8: `A2` burst sprint stops apply `max(A2_BURST_SL_PTS, A2_BURST_ATR_FLOOR_MULT * ATR)` on high-ATR days.
5. `tools/raw_replay.py` renders `[8c] STAGE 4 EXIT RULER SCORECARD` comparing Planned Shot vs Chandelier & Fixed waves.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import config
import shooting_team
import wall_brackets
from tools import raw_replay as rr


def _valid_power(direction: str = "DOWN") -> dict:
    is_up = direction == "UP"
    return {
        "team": "POWER",
        "direction": direction,
        "up_power_pct": 72.0 if is_up else 28.0,
        "down_power_pct": 28.0 if is_up else 72.0,
        "power_total_pct": 100.0,
        "reason_code": "UPWARD_FORCE_DOMINATES" if is_up else "DOWNWARD_FORCE_DOMINATES",
        "shadow_only": False,
        "authorized_by": "STAGE3D_SNIPER",
        "context": {"market_regime": "TREND", "feed_stale": False, "data_quality_ok": True},
        "regime_diagnostics": {"market_regime": "TREND", "latest_tick_age_seconds": 1.0},
    }


def _seed_warm_road(now_ts: float, normal_lots: float = 120.0) -> None:
    shooting_team._ROAD_HISTORY.clear()
    for k in range(15):
        shooting_team._ROAD_HISTORY.append((now_ts - (15 - k) * 10.0, normal_lots))


def _base_ctx(now_ts: float, price: float = 4000.0, spread: float = 0.20, **extra) -> dict:
    half = spread / 2.0
    ctx = {
        "bid": round(price - half, 4),
        "ask": round(price + half, 4),
        "news_state": "QUIET",
        "has_data": True,
        "data_quality_ok": True,
        "last_data_age_seconds": 1.0,
        "trade_lots": 1.0,
        "recent_volume": 300.0,
        "now": now_ts,
        "stage3d_sniper_authorized": True,
        "stage3d_sniper_reason": "Stage 3d Sniper (SELL @ 74.0%, raw_power=NEITHER)",
        "stage3d_power_envelope": _valid_power("DOWN"),
    }
    ctx.update(extra)
    return ctx


class _MockBook:
    def __init__(self, shelter_px: float, shelter_sz: float = 12.0):
        self._px = shelter_px
        self._sz = shelter_sz

    def shelter_behind(self, entry: float, is_buy: bool):
        return {"price": self._px, "size": self._sz, "distance": abs(entry - self._px)}


def test_stage4a_sub_atr_shelter_rejected_while_structural_shelter_preserved():
    now_ts = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc).timestamp()
    _seed_warm_road(now_ts)
    smap = {
        "below": [
            {"price": 3992.0, "size": 30.0, "doors_between": 3, "lots_between": 90.0, "road_clear": True}
        ]
    }
    # 1) Near-touch $0.40 shelter (`4000.30` on SELL @ `3999.90`, ATR=2.0 -> risk ~0.80 < 1.5*2.0) -> ATR_FALLBACK (4.0 pts)
    near_book = _MockBook(4000.30, 8.0)
    shot_near = shooting_team.plan_shot(
        smap, _valid_power("DOWN"), 4000.0, atr=2.0, spread=0.20,
        config=config, book=near_book, entry_context=_base_ctx(now_ts),
    )
    assert shot_near["shot"] == "GO"
    assert shot_near["stop_source"] == "ATR_FALLBACK"
    assert shot_near["risk"] == pytest.approx(4.0, abs=0.05)

    # 2) Genuine structural shelter (`4003.50` -> buffered stop `4003.90`, risk `4.00` = `2.0x ATR` in [1.5x, 2.5x]) -> BOOK_SHELTER
    struct_book = _MockBook(4003.50, 28.0)
    shot_struct = shooting_team.plan_shot(
        smap, _valid_power("DOWN"), 4000.0, atr=2.0, spread=0.20,
        config=config, book=struct_book, entry_context=_base_ctx(now_ts),
    )
    assert shot_struct["shot"] == "GO"
    assert shot_struct["stop_source"] == "BOOK_SHELTER"
    assert shot_struct["risk"] == pytest.approx(4.0, abs=0.05)


def test_stage4a_scans_past_routine_pebble_to_viable_door_or_wave_target():
    now_ts = datetime(2026, 10, 30, 12, 6, tzinfo=timezone.utc).timestamp()
    _seed_warm_road(now_ts)

    # Case A: Door #1 at 3998.50 (12 lots, R:R=0.25) + Door #2 at 3993.00 (32 lots, R:R=1.60) -> picks 3993.00!
    smap_two = {
        "below": [
            {"price": 3998.5, "size": 12.0, "doors_between": 1, "lots_between": 40.0, "road_clear": True},
            {"price": 3993.0, "size": 32.0, "doors_between": 4, "lots_between": 110.0, "road_clear": False},
        ]
    }
    shot_door2 = shooting_team.plan_shot(
        smap_two, _valid_power("DOWN"), 4000.0, atr=2.0, spread=0.20,
        config=config, book=None, entry_context=_base_ctx(now_ts),
    )
    assert shot_door2["shot"] == "GO"
    assert shot_door2["door"]["price"] == pytest.approx(3993.0)
    assert shot_door2["entry_options"]["AGGRESSIVE_MARKET"]["target_source"] == "SCOUT_VIABLE_DOOR"
    assert shot_door2["rr"] >= 1.20

    # Case B: Only routine 12-lot pebble at 3998.50 (no further whale door), clean corridor -> 3.5x ATR Wave Target!
    smap_pebble_only = {
        "below": [
            {"price": 3998.5, "size": 12.0, "doors_between": 1, "lots_between": 40.0, "road_clear": True},
        ]
    }
    shot_wave = shooting_team.plan_shot(
        smap_pebble_only, _valid_power("DOWN"), 4000.0, atr=2.0, spread=0.20,
        config=config, book=None, entry_context=_base_ctx(now_ts + 10.0),
    )
    assert shot_wave["shot"] == "GO"
    assert shot_wave["entry_options"]["AGGRESSIVE_MARKET"]["target_source"] == "ATR_WAVE_TARGET"
    assert shot_wave["reward"] == pytest.approx(7.0, abs=0.05)  # 3.5 * ATR=2.0
    assert shot_wave["risk"] == pytest.approx(4.0, abs=0.05)    # 2.0 * ATR=2.0
    assert shot_wave["rr"] == pytest.approx(1.75, abs=0.02)


def test_stage4a_major_wall_or_iceberg_blocks_wave_and_preserves_reason():
    now_ts = datetime(2026, 10, 30, 12, 12, tzinfo=timezone.utc).timestamp()
    _seed_warm_road(now_ts)

    # 45-lot solid wall at 3998.50 (< min_rr * risk) MUST fail closed and preserve exact AGGRESSIVE reason!
    smap_major = {
        "below": [
            {"price": 3998.5, "size": 45.0, "doors_between": 1, "lots_between": 50.0, "road_clear": True},
        ]
    }
    shot_blk = shooting_team.plan_shot(
        smap_major, _valid_power("DOWN"), 4000.0, atr=2.0, spread=0.20,
        config=config, book=None, entry_context=_base_ctx(now_ts),
    )
    assert shot_blk["shot"] == "WAIT"
    assert "major opposing wall 45 lots" in shot_blk["why"][1]
    assert "no separately confirmed aggressive-entry trigger" not in shot_blk["why"][1]


def test_stage4a_bookmark8_a2_burst_atr_floor():
    now_ts = datetime(2026, 10, 30, 14, 0, tzinfo=timezone.utc).timestamp()
    _seed_warm_road(now_ts)
    smap = {
        "above": [
            {"price": 4012.0, "size": 30.0, "doors_between": 2, "lots_between": 80.0, "road_clear": True},
        ]
    }
    ctx_a2 = {
        "bid": 3999.9,
        "ask": 4000.1,
        "news_state": "QUIET",
        "has_data": True,
        "data_quality_ok": True,
        "last_data_age_seconds": 1.0,
        "trade_lots": 1.0,
        "recent_volume": 300.0,
        "now": now_ts,
        "aggressive_trigger_confirmed": True,
        "aggressive_trigger_reason": "A2 burst confirmed",
        "a2_burst": True,
    }
    # High volatility ATR = 3.20 > 1.80 -> Bookmark #8 lifts A2 burst risk/reward from 1.80 to 3.20!
    shot_a2 = shooting_team.plan_shot(
        smap, _valid_power("UP"), 4000.0, atr=3.20, spread=0.20,
        config=config, book=None, entry_context=ctx_a2,
    )
    assert shot_a2["shot"] == "GO"
    assert shot_a2["stop_source"] == "A2_BURST_FIXED"
    assert shot_a2["risk"] == pytest.approx(3.20, abs=0.02)
    assert shot_a2["reward"] == pytest.approx(3.20, abs=0.02)


def test_stage4a_wall_brackets_enforces_sl_floor_and_raw_replay_renders_8c(tmp_path, monkeypatch):
    # 1) wall_brackets rejects $0.40 sub-ATR behind wall (`4199.7` on BUY @ `4200.0`, ATR=2.0 -> 2xATR=4196.0)
    #    and skips 12-lot pebble (`4201.0`) for viable wall (`4207.0`)
    snap = SimpleNamespace(
        bid=4199.9,
        ask=4200.1,
        signal_map={
            "above": [{"price": 4201.0, "size": 12.0}, {"price": 4207.0, "size": 28.0}],
            "below": [{"price": 4199.7, "size": 8.0}],
        },
    )
    sl, tp, _ = wall_brackets.compute_wall_brackets("BUY", 4200.0, 2.0, snap)
    assert sl == pytest.approx(4196.0, abs=0.02)
    assert tp == pytest.approx(4206.6, abs=0.02)

    # 2) raw_replay renders [8c] STAGE 4 EXIT RULER SCORECARD
    monkeypatch.setattr(config, "reload_env", lambda *a, **k: None)
    monkeypatch.setattr(config, "TIMEFRAME", "M3", raising=False)
    monkeypatch.setenv("TIMEFRAME", "M3")
    monkeypatch.setenv("CONFIDENCE_THRESHOLD", "35")
    from test_stage1_timeframe_whatif_and_replay import _write_synthetic_raw_day
    import step2_market_analysis as s2

    proj = tmp_path / "proj"
    data_dir = proj / "data"
    _write_synthetic_raw_day(data_dir, "2026-10-29", start_px=4000.0, drift_per_min=0.30, minutes=140)
    real_analyze = s2.analyze_market

    def _patched_analyze(md, now=None):
        sn = real_analyze(md, now=now)
        if getattr(sn, "signal_direction", "NEUTRAL") in ("BUY", "SELL"):
            px = float(sn.price)
            sn.confidence = 72.0
            sn.shot = {
                "shot": "GO",
                "side": sn.signal_direction,
                "entry_style": "AGGRESSIVE_MARKET",
                "entry": px,
                "stop": (px - 3.0) if sn.signal_direction == "BUY" else (px + 3.0),
                "target": (px + 5.25) if sn.signal_direction == "BUY" else (px - 5.25),
                "rr": 1.75,
                "entry_options": {"AGGRESSIVE_MARKET": {"target_source": "ATR_WAVE_TARGET"}},
                "why": ["Stage 4a wave bracket"],
            }
        return sn

    monkeypatch.setattr(s2, "analyze_market", _patched_analyze)
    res = rr.replay_single_day("2026-10-29", root_dir=proj, data_dir=data_dir, every_s=180)
    txt, _, _ = rr.synthesize_multi_day([res])
    assert "[8c] STAGE 4 EXIT RULER SCORECARD" in txt
    assert "Stage-4a Planned Shot (Pure SL/TP)" in txt
    assert "Stage-4b Preview: Planned + 1.5xATR Trail@1R" in txt
