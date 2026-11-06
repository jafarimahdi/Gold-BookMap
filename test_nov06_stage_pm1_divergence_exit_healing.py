"""
Stage PM-1 (2026-11-06) Regression Suite — Isolated PositionManager `DIVERGENCE_EXIT` Healing
(`Bookmark #12` Leak #1: 49s-84s premature panic closes on `pos=98856419`, `98884473`, `98121449`):
- `PM_DIVERGENCE_EXIT` defaults to `False` (`0`), matching 15-day raw-replay-proven Stage-4b Wave Trail
  (`1.5x ATR @ 1.0R` + `PM_WAVE_WEAK_BE_R=0.75R` early break-even + `FLIP_EXIT>=55`).
- `PM_DIVERGENCE_MIN_AGE_SECONDS` scales dynamically via Law #11 (`POWER_BAR_SECONDS`: `180s` on M3, `300s` on M5).
- Even when `PM_DIVERGENCE_EXIT=1` is explicitly enabled, `DIVERGENCE_EXIT` refuses to panic-close on:
  (a) newly adopted or `< 1` bar-old positions (`49s-84s`),
  (b) `< 0.50` divergence noise,
  (c) pre-existing waterfall absorption already present at entry (`entry_divergence`),
  (d) unconfirmed raw `snapshot.divergence` rejected by Step 2's Stage-2R `cvd_divergence` judge,
  while cleanly closing with `{div:+.2f}` formatting (never `+0`) when all 4 institutional criteria hold.
- `tools/raw_replay.py` `[8c]` prints `* Law #9 Stage PM-1 Divergence-Exit Audit`.
"""
import time
from types import SimpleNamespace

import config
from position_manager import PositionManager
import step2_market_analysis as s2
from tools import raw_replay as rr


def _make_pm_harness():
    pm = PositionManager.__new__(PositionManager)
    pm._mt5 = SimpleNamespace(POSITION_TYPE_BUY=0, POSITION_TYPE_SELL=1)
    pm._hist = []
    pm._state = {}
    pm._save_state = lambda: None
    pm._journal = lambda _d: None
    closes = []
    mods = []
    pm._close = lambda pos, rule, reason, ctx, urgent=False: (
        closes.append((rule, reason, dict(ctx))) or True
    )
    pm._modify_stops = lambda pos, nsl, ntp, rule, reason, ctx: (
        mods.append((nsl, ntp, rule, reason)) or True
    )
    pm._news_threat = lambda _snap: (0.0, "")
    return pm, closes, mods


def _make_sell_snap(*, div: float = 0.0, judge_panel=None, notes=None, delta: float = -20.0, buy_press: float = 40.0):
    return SimpleNamespace(
        price=4000.0,
        regime="TREND",
        divergence=div,
        signal_direction="NEUTRAL",
        signal_strength=20.0,
        volatility=SimpleNamespace(atr=4.0, volatility_rank=0.5),
        order_flow=SimpleNamespace(cvd=-120.0, delta=delta, buying_pressure=buy_press),
        volume_profile=SimpleNamespace(vwap=4002.0, vwap_zscore=-0.6, poc=4002.0, vah=4005.0, val=3998.0),
        signal_map={"above": [], "below": []},
        shot={"side": "SELL", "stop_source": "SCOUT_SHELTER"},
        judge_panel=judge_panel or [],
        notes=notes or [],
        order_blocks=[],
        footprint=None,
        htf_poc={},
        macro_bias=0.0,
    )


def test_stage_pm1_config_defaults_and_law11_timeframe_scaling(monkeypatch):
    with monkeypatch.context() as mp:
        for k in (
            "PM_DIVERGENCE_EXIT",
            "PM_DIVERGENCE_STRENGTH_MIN",
            "PM_DIVERGENCE_MIN_AGE_SECONDS",
            "PM_DIVERGENCE_REQUIRE_CONFIRMED_JUDGE",
            "TIMEFRAME",
            "POWER_BAR_SECONDS",
        ):
            mp.delenv(k, raising=False)
        mp.setenv("TIMEFRAME", "M5")
        config._refresh()
        assert config.PM_DIVERGENCE_EXIT is False
        assert config.PM_DIVERGENCE_STRENGTH_MIN == 0.50
        assert config.PM_DIVERGENCE_MIN_AGE_SECONDS == 300
        assert config.PM_DIVERGENCE_REQUIRE_CONFIRMED_JUDGE is True

        mp.setenv("TIMEFRAME", "M3")
        config._refresh()
        assert config.PM_DIVERGENCE_MIN_AGE_SECONDS == 180
    config._refresh()


def test_stage_pm1_blocks_49s_preexisting_and_unconfirmed_divergence_panics(monkeypatch):
    """Reproduces Bookmark #12 Leak #1 (2026-11-06 pos=98884473 49s SELL close on pre-existing div>0)."""
    pm, closes, _mods = _make_pm_harness()
    monkeypatch.setattr(config, "STAGE4B_WAVE_TRAIL_ENABLED", True)
    monkeypatch.setattr(config, "PM_DIVERGENCE_EXIT", True)
    monkeypatch.setattr(config, "PM_DIVERGENCE_STRENGTH_MIN", 0.50)
    monkeypatch.setattr(config, "PM_DIVERGENCE_MIN_AGE_SECONDS", 180)
    monkeypatch.setattr(config, "PM_DIVERGENCE_REQUIRE_CONFIRMED_JUDGE", True)

    t_now = time.time()
    # Position opened 49 seconds ago at 4000.00 SELL, SL 4008.00, TP 3986.00 (spread puts r_now = -0.04R < 0.30R)
    pos = SimpleNamespace(
        ticket=98884473,
        type=1,  # SELL
        price_open=4000.00,
        sl=4008.00,
        tp=3986.00,
        profit=-1.92,
        volume=0.01,
        time=t_now - 49.0,
        comment="BM-Sniper",
    )
    tick = SimpleNamespace(bid=4000.20, ask=4000.40)
    info = SimpleNamespace(point=0.01, trade_stops_level=5, digits=2)

    # 1) Cycle +1 (49s after open): pre-existing waterfall absorption `div = +0.42` -> MUST ADOPT & NOT CLOSE!
    snap_adopt = _make_sell_snap(div=0.42, delta=35.0, buy_press=62.0)
    pm._manage_one(pos, snap_adopt, tick, info)
    assert closes == []
    assert pm._state["98884473"]["entry_divergence"] == 0.42

    # 2) Still < 180s old (e.g. 95s after open), even with strong confirmed divergence -> MUST NOT CLOSE!
    pm._state["98884473"]["opened_ts"] = time.time() - 95.0
    snap_early = _make_sell_snap(
        div=0.85,
        judge_panel=[{"judge": "cvd_divergence", "dir": "BUY", "w": 1.0}],
        delta=40.0,
        buy_press=65.0,
    )
    pm._manage_one(pos, snap_early, tick, info)
    assert closes == []

    # 3) >= 240s old, but divergence (+0.55) was already +0.42 at entry (only +0.13 change < +0.35) -> MUST NOT CLOSE!
    pm._state["98884473"]["opened_ts"] = time.time() - 240.0
    snap_preexisting = _make_sell_snap(
        div=0.55,
        judge_panel=[{"judge": "cvd_divergence", "dir": "BUY", "w": 1.0}],
        delta=40.0,
        buy_press=65.0,
    )
    pm._manage_one(pos, snap_preexisting, tick, info)
    assert closes == []

    # 4) >= 240s old, fresh post-entry div (+0.65 from 0.0), BUT rejected by Step-2 `cvd_divergence` judge -> MUST NOT CLOSE!
    pm._state["98884473"]["entry_divergence"] = 0.0
    snap_unconfirmed = _make_sell_snap(div=0.65, judge_panel=[], delta=40.0, buy_press=65.0)
    pm._manage_one(pos, snap_unconfirmed, tick, info)
    assert closes == []

    # 5) >= 240s old, fresh post-entry div (+0.65 from 0.0), confirmed by Step-2 `cvd_divergence` + opposing flow -> CLOSES cleanly with `+0.65` (NOT `+0`!)
    snap_confirmed = _make_sell_snap(
        div=0.65,
        judge_panel=[{"judge": "cvd_divergence", "dir": "BUY", "w": 1.0}],
        delta=40.0,
        buy_press=65.0,
    )
    pm._manage_one(pos, snap_confirmed, tick, info)
    assert len(closes) == 1
    rule, reason, _ctx = closes[0]
    assert rule == "DIVERGENCE_EXIT"
    assert "+0.65" in reason
    assert "age " in reason


def test_stage_pm1_raw_replay_renders_divergence_exit_audit_line(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "reload_env", lambda *a, **k: None)
    monkeypatch.setattr(config, "TIMEFRAME", "M3", raising=False)
    monkeypatch.setenv("TIMEFRAME", "M3")
    monkeypatch.setenv("CONFIDENCE_THRESHOLD", "35")

    from test_stage1_timeframe_whatif_and_replay import _write_synthetic_raw_day
    proj = tmp_path / "proj_pm1"
    data_dir = proj / "data"
    _write_synthetic_raw_day(data_dir, "2026-10-29", start_px=4000.0, drift_per_min=0.30, minutes=140)

    real_analyze = s2.analyze_market
    call_idx = {"n": 0}

    def _patched_analyze(md, now=None):
        snap = real_analyze(md, now=now)
        call_idx["n"] += 1
        if call_idx["n"] == 10:
            px = float(snap.price)
            snap.signal_direction = "BUY"
            snap.confidence = 72.0
            snap.shot = {
                "shot": "GO",
                "side": "BUY",
                "entry_style": "AGGRESSIVE_MARKET",
                "entry": px + 0.10,
                "stop": px - 5.00,
                "target": px + 8.00,
                "rr": 1.55,
                "why": ["Stage PM-1 test GO"],
            }
        elif call_idx["n"] == 11:
            # Next cycle has opposing raw divergence (-0.25) before +0.30R -> old PM would panic-close!
            snap.divergence = -0.25
        return snap

    monkeypatch.setattr(s2, "analyze_market", _patched_analyze)
    res = rr.replay_single_day("2026-10-29", root_dir=proj, data_dir=data_dir, every_s=180)
    txt, _, _ = rr.synthesize_multi_day([res])
    assert "* Law #9 Stage PM-1 Divergence-Exit Audit: Old_Panic_Closes=1/" in txt
    assert "Saved=+" in txt
