from datetime import datetime, timezone
from types import SimpleNamespace

import config
import shooting_team
import signal_team
from tools import raw_replay as rr


def _seed_crowd_history(now_ep: float = 1_793_530_000.0):
    shooting_team._ROAD_HISTORY.clear()
    for i in range(15):
        shooting_team._ROAD_HISTORY.append((now_ep - (15 - i) * 60.0, 90.0))


def _sniper_cfg(**overrides):
    base = dict(
        LIMIT_ORDER_ENABLED=False,
        SHOOT_MIN_RR=1.2,
        SHOOT_MIN_SL_ATR=1.5,
        SHOOT_MAX_SL_ATR=2.5,
        SHOOT_WAVE_TP_ATR_MULT=3.5,
        SHOOT_MAJOR_WALL_BLOCK_LOTS=40.0,
        STAGE4A_WAVE_BRACKET_ENABLED=True,
        STAGE4C_SNIPER_PEBBLE_BYPASS_ENABLED=True,
        CROWD_V2_RECENT_VOL_MIN_MULT=1.0,
        CROWD_V2_ROAD_HISTORY_MAX_MULT=2.0,
        CROWD_V2_MIN_ROAD_SAMPLES=12,
        CROWD_V2_OUR_SIZE_MAX_FRAC=0.20,
        LOT_SIZE=0.01,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_stage4c_signal_team_stamps_iceberg_in_path_without_changing_road_clear():
    bm = signal_team.LevelBook()
    bm.cfg = SimpleNamespace(L3_WHALE_THRESHOLD=10.0)
    bm.last_ladder = [(4000.50, 4.0), (4001.00, 14.0), (4001.50, 4.0)]
    r_peb = bm.road_to(4000.00, 4002.50, whale_thr=10.0)
    assert r_peb["road_clear"] is False
    assert r_peb["iceberg_in_path"] is False
    assert r_peb["biggest_in_path"] == {"price": 4001.0, "size": 14.0}

    lv = signal_team.Level(price=4001.00, side="ask", size=6.0, now=1000.0)
    lv.sources = ["iceberg"]
    lv.spoofed = False
    lv.missing = 0
    bm.levels[4001.00] = lv
    bm.last_ladder = [(4000.50, 4.0), (4001.00, 6.0), (4001.50, 4.0)]
    r_ice = bm.road_to(4000.00, 4002.50, whale_thr=10.0)
    assert r_ice["road_clear"] is False
    assert r_ice["iceberg_in_path"] is True


def test_stage4c_sniper_wave_bypasses_sub40_pebble_but_blocks_major_wall_and_iceberg():
    from test_nov02_stage4a_entry_brackets_and_bookmark8 import _base_ctx, _seed_warm_road, _valid_power

    now_ep = 1_793_530_000.0
    _seed_warm_road(now_ep, normal_lots=120.0)
    cfg = _sniper_cfg()
    pw = _valid_power("DOWN")
    ctx = _base_ctx(now_ep, price=4000.00, spread=0.20, trade_lots=0.01, recent_volume=450.0)

    # Case 1: 14-lot non-iceberg pebble inside $2.00 made road_clear=False -> Stage 4c unlocks GO!
    smap_pebble = {
        "below": [
            {
                "price": 3997.80,
                "size": 16.0,
                "doors_between": 3,
                "lots_between": 80.0,
                "biggest_in_path": {"price": 3999.00, "size": 14.0},
                "iceberg_in_path": False,
                "road_clear": False,
            }
        ],
        "above": [],
    }
    out_ok = shooting_team.plan_shot(smap_pebble, pw, 4000.00, atr=3.50, spread=0.20, config=cfg, entry_context=ctx)
    assert out_ok["shot"] == "GO", out_ok["why"]
    eo_ok = out_ok["entry_options"]["AGGRESSIVE_MARKET"]
    assert eo_ok["sniper_pebble_bypass"] is True
    assert eo_ok["target_source"] == "ATR_WAVE_TARGET"

    # Case 2: 52-lot institutional wall in path -> MUST FAIL CLOSED (`WAIT`)!
    smap_major = {
        "below": [
            {
                "price": 3997.80,
                "size": 16.0,
                "doors_between": 3,
                "lots_between": 95.0,
                "biggest_in_path": {"price": 3999.00, "size": 52.0},
                "iceberg_in_path": False,
                "road_clear": False,
            }
        ],
        "above": [],
    }
    out_maj = shooting_team.plan_shot(smap_major, pw, 4000.00, atr=3.50, spread=0.20, config=cfg, entry_context=ctx)
    assert out_maj["shot"] == "WAIT"
    assert any("52 lots" in w for w in out_maj["why"])

    # Case 3: Active iceberg in path (even 12 visible lots) -> MUST FAIL CLOSED (`WAIT`)!
    smap_ice = {
        "below": [
            {
                "price": 3997.80,
                "size": 16.0,
                "doors_between": 3,
                "lots_between": 70.0,
                "biggest_in_path": {"price": 3999.00, "size": 12.0},
                "iceberg_in_path": True,
                "road_clear": False,
            }
        ],
        "above": [],
    }
    out_ice = shooting_team.plan_shot(smap_ice, pw, 4000.00, atr=3.50, spread=0.20, config=cfg, entry_context=ctx)
    assert out_ice["shot"] == "WAIT"
    assert any("iceberg" in w for w in out_ice["why"])


def test_stage4c_portfolio_sim_enforces_single_open_position_and_cooldown():
    t0 = 1_793_530_000.0
    fake_days = [
        {
            "day": "2026-10-29",
            "day_type": "MIXED",
            "cycles": [
                # Trade 1 opens at t0, exits at t0 + 600s (+10m), earns +10.0 pts
                {
                    "entry_ep": t0,
                    "dt_utc": datetime.fromtimestamp(t0, tz=timezone.utc),
                    "direction": "SELL",
                    "confidence": 68.0,
                    "shot_action": "GO",
                    "shot_pebble_bypass": False,
                    "shot_pm4b_pts": 10.0,
                    "shot_pm4b_exit_ep": t0 + 600.0,
                },
                # Overlapping signal at t0 + 180s (+3m, while Trade 1 still open!) -> SKIPPED!
                {
                    "entry_ep": t0 + 180.0,
                    "dt_utc": datetime.fromtimestamp(t0 + 180.0, tz=timezone.utc),
                    "direction": "SELL",
                    "confidence": 70.0,
                    "shot_action": "GO",
                    "shot_pebble_bypass": True,
                    "shot_pm4b_pts": 12.0,
                    "shot_pm4b_exit_ep": t0 + 780.0,
                },
                # Cooldown signal at t0 + 720s (+12m, 2m after exit < 5m cooldown!) -> SKIPPED!
                {
                    "entry_ep": t0 + 720.0,
                    "dt_utc": datetime.fromtimestamp(t0 + 720.0, tz=timezone.utc),
                    "direction": "SELL",
                    "confidence": 66.0,
                    "shot_action": "GO",
                    "shot_pebble_bypass": False,
                    "shot_pm4b_pts": -7.0,
                    "shot_pm4b_exit_ep": t0 + 1020.0,
                },
                # Trade 2 opens at t0 + 960s (+16m >= exit 10m + 5m CD), loses -4.0 pts
                {
                    "entry_ep": t0 + 960.0,
                    "dt_utc": datetime.fromtimestamp(t0 + 960.0, tz=timezone.utc),
                    "direction": "BUY",
                    "confidence": 65.0,
                    "shot_action": "GO",
                    "shot_pebble_bypass": True,
                    "shot_pm4b_pts": -4.0,
                    "shot_pm4b_exit_ep": t0 + 1260.0,
                },
                # Trade 3 opens at t0 + 1620s (+27m), earns +8.0 pts
                {
                    "entry_ep": t0 + 1620.0,
                    "dt_utc": datetime.fromtimestamp(t0 + 1620.0, tz=timezone.utc),
                    "direction": "SELL",
                    "confidence": 72.0,
                    "shot_action": "GO",
                    "shot_pebble_bypass": False,
                    "shot_pm4b_pts": 8.0,
                    "shot_pm4b_exit_ep": t0 + 2100.0,
                },
            ],
        }
    ]
    sim_all = rr.simulate_one_pos_portfolio(
        fake_days, 35.0, "shot_pm4b_pts", "shot_pm4b_exit_ep", cooldown_min=5.0, exclude_pebble_bypass=False
    )
    assert sim_all["trades"] == 3
    assert sim_all["wins"] == 2 and sim_all["losses"] == 1
    assert sim_all["tot_pts"] == 14.0
    assert sim_all["max_dd"] == 4.0
    assert sim_all["pf"] == 4.5
    assert sim_all["skipped_open"] == 1
    assert sim_all["skipped_cd"] == 1

    sim_4b = rr.simulate_one_pos_portfolio(
        fake_days, 35.0, "shot_pm4b_pts", "shot_pm4b_exit_ep", cooldown_min=5.0, exclude_pebble_bypass=True
    )
    assert sim_4b["trades"] == 2
    assert sim_4b["tot_pts"] == 18.0
