from pathlib import Path
from types import SimpleNamespace

from escort_team import EscortBook, escort_cycle
from paper_entry_simulator import reset_entry_simulator, simulate_entry
from power_team_v2 import decide
from shooting_team import plan_shot

ROOT = Path(__file__).parent


CFG = SimpleNamespace(
    POWER_MAX_TICK_AGE_SECONDS=60.0,
    STALE_DATA_SECONDS=300.0,
    SHOOT_ROAD_RATIO=6.0,
    SHOOT_QUEUE_MIN_FILL_PROB=0.70,
    SHOOT_MIN_RR=1.2,
    SHOOT_MIN_REWARD_USD=0.0,
    PM_TP_BUFFER_ATR=0.15,
    SHOOT_PAPER_LIMIT_TTL_SECONDS=300.0,
    LIMIT_TICK_SIZE=0.1,
)


def test_power_v2_to_shooting_to_paper_fill_to_escort_end_to_end():
    reset_entry_simulator()
    import escort_team
    escort_team._BOOK = EscortBook()

    # Actual Power v2 judge aggregation feeds the live Shooting interface.
    power = decide({
        "footprint_delta": 0.8,
        "l3_aggr_limit": 0.8,
        "cvd_momentum": 0.7,
        "big_prints": 0.9,
        "footprint_levels": 0.7,
    }, context={"market_regime": "TREND", "data_quality_ok": True,
               "feed_stale": False})
    power["regime_diagnostics"] = {
        "market_regime": "TREND", "latest_tick_age_seconds": 2.0,
    }
    assert power["direction"] == "UP"
    assert power["up_power_pct"] + power["down_power_pct"] == 100.0

    signal_map = {"above": [{
        "price": 105.0, "size": 20.0, "doors_between": 0,
        "lots_between": 0.0,
    }]}
    entry_context = {
        "bid": 99.9, "ask": 100.1, "news_state": "QUIET",
        "has_data": True, "data_quality_ok": True,
        "last_data_age_seconds": 1.0, "queue_enabled": False,
        "aggressive_trigger_confirmed": False, "now": 1000.0,
    }
    shot = plan_shot(signal_map, power, 100.0, atr=1.0, spread=0.2,
                     config=CFG, entry_context=entry_context)
    assert shot["shot"] == "GO"
    assert shot["side"] == "BUY"
    assert shot["entry_style"] == "PASSIVE_LIMIT"

    pending = simulate_entry(shot, 100.0, config=CFG, now=1000.0)
    assert pending["status"] == "PENDING"
    before_fill = escort_cycle({}, signal_map, 100.0, 1.0, spread=0.2,
                               bid=99.9, ask=100.1, config=CFG)
    assert before_fill["watching"] == 0

    filled = simulate_entry(shot, 99.8, config=CFG, now=1001.0)
    assert filled["status"] == "FILLED"
    assert filled["filled_plan"]["execution_status"] == "PAPER_ONLY"
    managed = escort_cycle(filled["filled_plan"], signal_map, 99.8, 1.0,
                           spread=0.2, bid=99.7, ask=99.9, config=CFG)
    assert managed["opened_now"] is True
    assert managed["watching"] == 1
    assert managed["reports"][0]["entry_style"] == "PASSIVE_LIMIT"


def test_power_neither_does_not_become_a_shooting_side():
    power = decide({}, context={"market_regime": "TREND"})
    power["regime_diagnostics"] = {
        "market_regime": "TREND", "latest_tick_age_seconds": 1.0,
    }
    out = plan_shot({"above": [{"price": 105.0, "size": 20.0}]}, power,
                    100.0, atr=1.0, spread=0.2, config=CFG,
                    entry_context={"bid": 99.9, "ask": 100.1,
                                   "has_data": True, "last_data_age_seconds": 1})
    assert power["direction"] == "NEITHER"
    assert out["shot"] == "WAIT"
    assert out["side"] is None


def test_step2_no_longer_imports_or_calls_legacy_power():
    source = (ROOT / "step2_market_analysis.py").read_text(encoding="utf-8")
    assert "from power_team import" not in source
    assert "_plan(_signal_map, _power_v2" in source
    assert "power_legacy={}" in source


def test_canonical_snapshot_and_diary_use_v2():
    step2 = (ROOT / "step2_market_analysis.py").read_text(encoding="utf-8")
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "power=_power_v2, power_v2=_power_v2" in step2
    assert '"power_legacy": {}' in main
    assert '"entry_simulation": (getattr(snapshot, "entry_simulation", None) or {})' in main


def test_escort_only_opens_confirmed_paper_fills():
    source = (ROOT / "escort_team.py").read_text(encoding="utf-8")
    assert 'plan.get("entry_filled") is not True' in source
    assert 'plan.get("execution_status") != "PAPER_ONLY"' in source
