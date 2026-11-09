"""
test_nov09_stage_d2_scalper_prompt_bars.py
==========================================
Section `#13` Stage D2 -- scalper candle window for the Step-3 Doorman.

Law #11 (Universal Chart Timeframe Fitness) applied to the LLM briefing:

    a 3-minute scalper is graded on a `+3 / +6 / +9 / +12` minute clock, but the
    prompt used to hand Gemini 15 bars (code default) or 20 bars (live `.env`) of
    `M3` history = **45-60 minutes** -- a trend-swing context for a market that
    has already moved on.  4 bars = 12 minutes = the grading clock exactly.

Stage D2 changes three numbers and one name:

  * `config.py`             `AI_MAX_PROMPT_BARS` default `15 -> 4`, floor `5 -> 3`,
                            new preferred `.env` name `AI_PROMPT_BARS`.
  * `step2_market_analysis` floor `5 -> 3`, default `15 -> 4` (builds `recent_bars`).
  * `step3_ai_decision`     fallback default `15 -> 4` (trims `recent_bars.bars`).

Nothing else may change: the tests below prove the rest of the payload is
untouched, so `D2` is a pure window change with zero side effects (Law #9).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
import pytest

import config
import step2_market_analysis as s2
import step3_ai_decision as s3


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _isolate_a1_bar_service():
    """Keep the repo-wide A1 bar-service cache clean.

    `analyze_market()` stores its full snapshot and 'serves' it until the next bar
    close, so a snapshot built here at 20 bars would be handed to a LATER test that
    reuses the same symbol + bar bucket (its `recent_bars.up_bars` would then describe
    20 bars while its trimmed bar list shows 15). Clear before AND after.
    """
    s2._A1_BAR_SERVICE_CACHE.clear()
    yield
    s2._A1_BAR_SERVICE_CACHE.clear()


@pytest.fixture
def cfg_env(monkeypatch):
    """Set/clear the bar knobs, re-run `config._refresh()`, restore afterwards."""
    def _apply(**env):
        for name in ("AI_PROMPT_BARS", "AI_MAX_PROMPT_BARS"):
            monkeypatch.delenv(name, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, str(v))
        config._refresh()
        return int(config.AI_MAX_PROMPT_BARS)
    yield _apply
    config._refresh()


def _market(now_dt, n_bars=120):
    idx = pd.date_range("2026-10-30 09:01:00+00:00", periods=n_bars, freq="1min")
    closes = [3985.0 + 0.08 * i for i in range(n_bars)]
    candles = pd.DataFrame(
        {
            "open": [c - 0.05 for c in closes],
            "high": [c + 0.35 for c in closes],
            "low": [c - 0.30 for c in closes],
            "close": closes,
            "volume": [160.0 + i for i in range(n_bars)],
        },
        index=idx,
    )
    return {
        "symbol": "GCZ6.COMEX@RITHMIC",
        "data_symbol": "GCZ6.COMEX@RITHMIC",
        "data_market": "GC",
        "trade_symbol": "XAUUSD",
        "trade_market": "XAUUSD",
        "price": 3994.6,
        "bid": 3994.5,
        "ask": 3994.7,
        "cfd_bid": 3989.10,
        "cfd_ask": 3989.38,
        "volume": 5120.0,
        "timestamp": now_dt,
        "candles": candles,
        "order_book": {
            "bids": [(3994.5 - i * 0.1, 18.0) for i in range(25)],
            "asks": [(3994.7 + i * 0.1, 18.0) for i in range(25)],
        },
        "order_events": [],
        "news": {"headlines": ["Gold macro update"], "events": [], "fetch_calendar": False},
        "has_data": True,
        "data_quality_ok": True,
        "last_data_age_seconds": 0.4,
    }


def _snapshot(bars, monkeypatch, tmp_path):
    """ONE full compute at `bars`. The A1 bar-service cache is cleared first,
    otherwise it 'serves' the previous snapshot for the same bar bucket."""
    s2._A1_BAR_SERVICE_CACHE.clear()
    now_dt = datetime(2026, 10, 30, 11, 0, 0, tzinfo=timezone.utc)
    for _mod in {config, getattr(s2, "config", config), getattr(s3, "config", config)}:
        monkeypatch.setattr(_mod, "reload_env", lambda *a, **k: None, raising=False)
        monkeypatch.setattr(_mod, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(_mod, "AI_MAX_PROMPT_BARS", bars, raising=False)
        monkeypatch.setattr(_mod, "AI_MAX_HEADLINES", 5, raising=False)
    monkeypatch.setattr(s2, "DATA_DIR", lambda: str(tmp_path), raising=False)
    return s2.analyze_market(_market(now_dt), now=now_dt)


def _render(snap, monkeypatch, tmp_path, bars):
    """Render the prompt from an EXISTING snapshot at `bars` (step-3 trim gate)."""
    for _mod in {config, getattr(s2, "config", config), getattr(s3, "config", config)}:
        monkeypatch.setattr(_mod, "DATA_DIR", tmp_path, raising=False)
        monkeypatch.setattr(_mod, "AI_MAX_PROMPT_BARS", bars, raising=False)
    prompt = s3.AIDecisionEngine().build_prompt(snap)
    tail = prompt.split("MARKET ANALYSIS METRICS (JSON)")[1]
    return prompt, json.loads(tail[tail.index("{"):])


# --------------------------------------------------------------------------- #
# (1) config: default, floor, ceiling, alias precedence
# --------------------------------------------------------------------------- #
def test_d2_default_is_four_bars(cfg_env):
    assert cfg_env() == 4


def test_d2_floor_is_three_bars(cfg_env):
    assert cfg_env(AI_PROMPT_BARS=2) == 3
    assert cfg_env(AI_PROMPT_BARS=1) == 3
    assert cfg_env(AI_PROMPT_BARS=0) == 3


def test_d2_ceiling_is_fifty(cfg_env):
    assert cfg_env(AI_PROMPT_BARS=200) == 50


def test_d2_new_name_wins_over_legacy(cfg_env):
    # the owner's live `.env` still carries the legacy 20 -> the new knob must win
    assert cfg_env(AI_PROMPT_BARS=4, AI_MAX_PROMPT_BARS=20) == 4


def test_d2_legacy_name_still_honoured(cfg_env):
    assert cfg_env(AI_MAX_PROMPT_BARS=20) == 20
    assert cfg_env(AI_MAX_PROMPT_BARS=6) == 6


def test_d2_both_module_attrs_stay_in_sync(cfg_env):
    cfg_env(AI_PROMPT_BARS=4, AI_MAX_PROMPT_BARS=20)
    assert config.AI_PROMPT_BARS == config.AI_MAX_PROMPT_BARS == 4


# --------------------------------------------------------------------------- #
# (2) snapshot: `recent_bars` really is the scalper window
# --------------------------------------------------------------------------- #
def test_d2_recent_bars_count_is_four(monkeypatch, tmp_path):
    snap = _snapshot(4, monkeypatch, tmp_path)
    assert snap.recent_bars["count"] == 4
    assert len(snap.recent_bars["bars"]) == 4


def test_d2_recent_bars_are_the_last_four_and_self_consistent(monkeypatch, tmp_path):
    snap = _snapshot(4, monkeypatch, tmp_path)
    rb = snap.recent_bars
    bars = rb["bars"]
    # last four 1m closes of the synthetic tape: 3985.0 + 0.08*i, i = 116..119
    assert [b["c"] for b in bars] == [round(3985.0 + 0.08 * i, 2) for i in range(116, 120)]
    # aggregates must describe exactly these 4 bars, never a 15/20-bar window
    assert rb["up_bars"] == sum(1 for b in bars if b["c"] > b["o"])
    assert rb["down_bars"] == sum(1 for b in bars if b["c"] < b["o"])
    assert rb["period_high"] == round(max(b["h"] for b in bars), 2)
    assert rb["period_low"] == round(min(b["l"] for b in bars), 2)
    assert rb["range_pts"] == round(rb["period_high"] - rb["period_low"], 2)
    assert rb["net_change_pts"] == round(bars[-1]["c"] - bars[0]["o"], 2)


def test_d2_bar_count_is_clamped_exactly(monkeypatch, tmp_path):
    for want in (3, 4, 5, 15, 20):
        snap = _snapshot(want, monkeypatch, tmp_path)
        assert snap.recent_bars["count"] == want


# --------------------------------------------------------------------------- #
# (3) the prompt: shorter, and NOTHING else moved (Law #9 single variable)
# --------------------------------------------------------------------------- #
def test_d2_prompt_is_shorter_and_still_under_cap(monkeypatch, tmp_path):
    snap = _snapshot(20, monkeypatch, tmp_path)     # one snapshot, two renders
    p4, _ = _render(snap, monkeypatch, tmp_path, 4)
    p20, _ = _render(snap, monkeypatch, tmp_path, 20)
    assert len(p4) < len(p20)
    assert (len(p20) - len(p4)) > 500, "16 fewer bars must save real characters"
    assert len(p4) < 15000, f"prompt must stay <15000 chars, got {len(p4)}"


def test_d2_only_the_bar_block_moves(monkeypatch, tmp_path):
    snap = _snapshot(20, monkeypatch, tmp_path)     # one snapshot, two renders
    _p4, payload4 = _render(snap, monkeypatch, tmp_path, 4)
    _p20, payload20 = _render(snap, monkeypatch, tmp_path, 20)
    assert set(payload4) == set(payload20)
    diffs = [k for k in payload20 if payload20[k] != payload4[k]]
    assert diffs == ["recent_bars"], f"only recent_bars may differ, but {diffs} moved"
    # and inside the block, only the bar list + count are trimmed
    rb4, rb20 = payload4["recent_bars"], payload20["recent_bars"]
    assert set(rb4) == set(rb20)
    for k in rb4:
        if k in ("bars", "count"):
            continue
        assert rb4[k] == rb20[k], f"recent_bars.{k} must not move"
    assert len(rb4["bars"]) == 4 and rb4["count"] == 4


def test_d2_trim_never_exceeds_the_configured_window(monkeypatch, tmp_path):
    snap = _snapshot(20, monkeypatch, tmp_path)
    for bars in (3, 4, 6):
        _p, payload = _render(snap, monkeypatch, tmp_path, bars)
        assert payload["recent_bars"]["count"] <= bars
        assert len(payload["recent_bars"]["bars"]) <= bars


# --------------------------------------------------------------------------- #
# (4) TF fitness: the window is expressive in MINUTES (Law #11)
# --------------------------------------------------------------------------- #
def test_d2_window_minutes_follow_the_active_timeframe(monkeypatch):
    """On the scalper profile (`M3`) 4 bars = 12 min = the `+12 min` grading clock."""
    monkeypatch.setenv("TIMEFRAME", "M3")
    config._refresh()
    try:
        assert int(config.BASE_TF_MINUTES) == 3
        assert int(config.AI_MAX_PROMPT_BARS) * int(config.BASE_TF_MINUTES) == 12
    finally:
        config._refresh()
