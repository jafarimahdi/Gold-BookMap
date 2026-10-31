"""
Stage 1 Verification Suite (Law #9 Causal Isolation — Camera & Speed Only):
  1. `tools/insight.py`: dynamic `TIMEFRAME`-linked `1x..4x` bar lookaheads
     (`M3 -> 3, 6, 9, 12m`; `M5 -> 5, 10, 15, 20m`) + Market Trick classifier
     (`CLEAN_FOLLOW`, `FAKEOUT_WIN`, `TRAP_REVERSAL`, `CLEAN_AGAINST`), while
     preserving the strict "never import config" contract.
  2. `audit_day.py`: `test_7_what_if` bar-by-bar `1..4`-bar path & skipped-signal
     trick audit dynamically driven by recorded `POWER_BAR_SECONDS` (`M3` vs `M5`).
  3. `tools/raw_replay.py`:
     - `shot.get("shot")` ("GO" | "WAIT") + `"why"` list recognition lighting up
       `SHOOTING GO` in `[2] GATE WATERFALL` and `[8] BOOKMARK #8 SPRINT STOP CHECK`.
     - Dynamic `TIMEFRAME` (`M3 -> +3/+6/+9/+12m`, `M5 -> +5/+10/+15/+20m`) +
       Section `[9] BAR-BY-BAR PATH & MARKET TRICK DETECTOR`.
     - Exact bit-identity between `--workers 1` and `--workers 2` parallel replay.
"""
from __future__ import annotations

import ast
import contextlib
import io
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

import audit_day
import config
import step2_market_analysis as s2
from test_oct31_law8_raw_replay import _write_synthetic_raw_day
from tools import insight
from tools import raw_replay as rr


def test_insight_never_imports_config_and_scales_horizons_by_timeframe(monkeypatch):
    src = (Path(rr.ROOT) / "tools" / "insight.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "config", "tools/insight.py must never import config!"
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "config", "tools/insight.py must never import from config!"

    monkeypatch.setenv("TIMEFRAME", "M3")
    assert insight.what_if_horizons() == (3, 6, 9, 12)
    monkeypatch.setenv("TIMEFRAME", "M5")
    assert insight.what_if_horizons() == (5, 10, 15, 20)
    monkeypatch.setenv("TIMEFRAME", "M1")
    assert insight.what_if_horizons() == (1, 2, 3, 4)
    # Explicit bar_seconds overrides env
    assert insight.what_if_horizons(bar_seconds=180) == (3, 6, 9, 12)
    assert insight.what_if_horizons(bar_seconds=300) == (5, 10, 15, 20)


def test_market_trick_classifier_buy_and_sell_paths():
    # 1) Clean follow-through BUY: +0.8, +1.5, +2.4, +3.1
    code, lbl = insight.classify_market_trick("BUY", [0.8, 1.5, 2.4, 3.1])
    assert code == "CLEAN_FOLLOW"
    assert "CLEAN FOLLOW-THROUGH" in lbl

    # 2) Shakeout -> Win (dipped -0.9 at +3m, recovered +2.2 by +12m)
    code, lbl = insight.classify_market_trick("BUY", [-0.9, -0.4, +1.1, +2.2])
    assert code == "FAKEOUT_WIN"
    assert "SHAKEOUT -> WIN" in lbl

    # 3) Bait Trap -> Reversal (popped +1.1 at +3m, slammed -2.5 by +12m)
    code, lbl = insight.classify_market_trick("SELL", [+1.1, +0.5, -1.2, -2.5])
    assert code == "TRAP_REVERSAL"
    assert "BAIT TRAP -> REVERSAL" in lbl

    # 4) Straight against (good skip when blocked)
    code, lbl = insight.classify_market_trick("SELL", [-0.7, -1.4, -2.0, -2.8])
    assert code == "CLEAN_AGAINST"
    assert "STRAIGHT AGAINST" in lbl


def test_insight_decisions_page_and_explain_render_m3_3_6_9_12(monkeypatch):
    monkeypatch.setenv("TIMEFRAME", "M3")
    t0 = datetime(2026, 10, 30, 13, 0, tzinfo=timezone.utc)
    # Simulate a classic M3 stop-hunt shakeout on BUY:
    # t+0=4000.0, +3m=3999.1 (-0.9), +6m=3999.6 (-0.4), +9m=4001.5 (+1.5), +12m=4002.8 (+2.8)
    px_map = {0: 4000.0, 3: 3999.1, 6: 3999.6, 9: 4001.5, 12: 4002.8, -12: 3998.5}
    series = []
    for m in range(-15, 25):
        p = px_map.get(m, 4000.0 + 0.1 * m)
        series.append((t0 + timedelta(minutes=m), p))

    decisions = [{
        "_dt": t0,
        "price": 4000.0,
        "signal_direction": "BUY",
        "signal_strength": 7.5,
        "signal_confidence": 52.0,
        "ai_action": "HOLD",
        "ai_confidence": 45.0,
        "exec_status": "REJECTED",
        "reason": "below threshold",
        "regime": "TREND",
    }]
    diary = [{
        "_dt": t0,
        "price": 4000.0,
        "regime": "TREND",
        "killzone": "NY",
        "strength": 7.5,
        "confidence": 52.0,
        "team_scores": {"MAP": 0.8},
        "judge_votes": [{"judge": "mtf", "dir": 1.0, "weight": 0.5, "raw": "MTF UP"}],
    }]

    html_m3 = insight.decisions_page("2026-10-30", decisions, diary, series, bar_seconds=180)
    assert "What happened next (M3: 1-4 bars)" in html_m3
    assert "+3 min (1b)" in html_m3
    assert "+6 min (2b)" in html_m3
    assert "+9 min (3b)" in html_m3
    assert "+12 min (4b)" in html_m3
    assert "SHAKEOUT -&gt; WIN" in html_m3
    assert "+5 min" not in html_m3

    exp_m3 = insight.explain("2026-10-30", "14:00", decisions, diary, series, bar_seconds=180)
    assert "WHAT HAPPENED NEXT (from the tape, M3 1-4 bars)" in exp_m3
    assert "  +3 min :" in exp_m3
    assert " +12 min :" in exp_m3
    assert "SHAKEOUT -> WIN" in exp_m3

    html_m5 = insight.decisions_page("2026-10-30", decisions, diary, series, bar_seconds=300)
    assert "What happened next (M5: 1-4 bars)" in html_m5
    assert "+5 min (1b)" in html_m5
    assert "+10 min (2b)" in html_m5
    assert "+15 min (3b)" in html_m5
    assert "+20 min (4b)" in html_m5


def test_audit_day_what_if_reports_m3_vs_m5_bar_horizons_and_tricks():
    base = datetime(2026, 10, 22, 8, 0, tzinfo=timezone.utc)
    ticks = []
    # Build 40 contiguous 3-minute & 5-minute coverage (180 minutes total)
    for m in range(0, 185):
        dt = base + timedelta(minutes=m)
        # Create gentle oscillation + upward drift after minute 90
        p = 4000.0 + (0.05 * m if m < 90 else 4.5 + 0.25 * (m - 90))
        ticks.append((dt, p, 2.0))
        ticks.append((dt + timedelta(seconds=20), p + 0.4, 1.0))
        ticks.append((dt + timedelta(seconds=40), p - 0.3, 1.0))

    sig_dt = base + timedelta(minutes=90)
    dec = {
        "_dt": sig_dt,
        "price": 4004.5,
        "signal_direction": "BUY",
        "signal_confidence": 68.0,
        "ai_confidence": 68.0,
        "exec_status": "REJECTED",
        "regime": "TREND",
    }

    rc_m3 = {
        "POWER_BAR_SECONDS": 180,
        "AUDIT_MAX_HOLD_MIN": 60,
        "AUDIT_SPREAD": 0.2,
        "STOP_LOSS_ATR_MULT": 2.0,
        "TAKE_PROFIT_ATR_MULT": 3.5,
    }
    with mock.patch.object(audit_day, "_load_run_config", return_value=rc_m3), \
         contextlib.redirect_stdout(io.StringIO()):
        rep_m3, _ = audit_day.test_7_what_if(date(2026, 10, 22), ticks, [dec], None)
    why_m3 = "\n".join(rep_m3.rows[0][3])
    assert "bar-by-bar M3 path (+3m/+6m/+9m/+12m):" in why_m3
    assert "market trick check (4xM3=+12m):" in why_m3
    assert audit_day.METRICS["whatif"]["bar_horizons_min"] == [3, 6, 9, 12]

    rc_m5 = dict(rc_m3, POWER_BAR_SECONDS=300)
    with mock.patch.object(audit_day, "_load_run_config", return_value=rc_m5), \
         contextlib.redirect_stdout(io.StringIO()):
        rep_m5, _ = audit_day.test_7_what_if(date(2026, 10, 22), ticks, [dec], None)
    why_m5 = "\n".join(rep_m5.rows[0][3])
    assert "bar-by-bar M5 path (+5m/+10m/+15m/+20m):" in why_m5
    assert audit_day.METRICS["whatif"]["bar_horizons_min"] == [5, 10, 15, 20]


def test_raw_replay_shooting_go_bookmark8_m3_tricks_and_parallel_parity(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "reload_env", lambda *a, **k: None)
    monkeypatch.setattr(config, "TIMEFRAME", "M3", raising=False)
    monkeypatch.setenv("TIMEFRAME", "M3")
    monkeypatch.setenv("CONFIDENCE_THRESHOLD", "35")

    proj = tmp_path / "proj"
    data_dir = proj / "data"
    _write_synthetic_raw_day(data_dir, "2026-10-29", start_px=4000.0, drift_per_min=0.30, minutes=140)
    _write_synthetic_raw_day(data_dir, "2026-10-30", start_px=4040.0, drift_per_min=-0.25, minutes=140)

    # First verify multi-process (--workers 2) matches sequential (--workers 1) bit-for-bit!
    res_seq_29 = rr.replay_single_day("2026-10-29", root_dir=proj, data_dir=data_dir, every_s=180)
    res_seq_30 = rr.replay_single_day("2026-10-30", root_dir=proj, data_dir=data_dir, every_s=180)
    txt_seq, _, meta_seq = rr.synthesize_multi_day([res_seq_29, res_seq_30])
    assert meta_seq["timeframe"] == "M3"
    assert meta_seq["tf_horizons"] == [3, 6, 9, 12]
    assert "[9] M3 BAR-BY-BAR PATH & MARKET TRICK DETECTOR (+3m / +6m / +9m / +12m = 1x..4x M3)" in txt_seq
    assert "+12m%" in txt_seq

    rc = rr.main([
        "--dates", "2026-10-29,2026-10-30",
        "--every", "180",
        "--workers", "2",
        "--root-dir", str(proj),
        "--data-dir", str(data_dir),
        "--no-browser",
    ])
    assert rc == 0
    txt_par = (data_dir / "current_version_replay.txt").read_text(encoding="utf-8")
    # Strip timestamp line in header before comparing deterministic tables
    seq_body = "\n".join(
        line for line in txt_seq.rstrip().splitlines()
        if not line.startswith(" Generated:") and "SEC" not in line and not line.strip().endswith("s")
    )
    par_body = "\n".join(
        line for line in txt_par.rstrip().splitlines()
        if not line.startswith(" Generated:") and "SEC" not in line and not line.strip().endswith("s")
    )
    assert seq_body == par_body

    # Second, verify `shot.get("shot") == "GO"` + `"why"` list activates SHOOTING GO and Bookmark #8!
    real_analyze = s2.analyze_market

    def _patched_analyze(md, now=None):
        snap = real_analyze(md, now=now)
        if getattr(snap, "signal_direction", "NEUTRAL") in ("BUY", "SELL"):
            snap.confidence = 72.0
            snap.shooting_plan = {
                "shot": "GO",
                "entry_style": "AGGRESSIVE_MARKET",
                "why": ["A2 impulse confirmed", "route clean"],
                "a2_burst": True,
            }
        return snap

    monkeypatch.setattr(s2, "analyze_market", _patched_analyze)
    res_go = rr.replay_single_day("2026-10-29", root_dir=proj, data_dir=data_dir, every_s=180)
    txt_go, _, _ = rr.synthesize_multi_day([res_go])
    assert "SHOOTING GO (AGGRESSIVE_MARKET)" in txt_go
    assert "[8] BOOKMARK #8 SPRINT STOP CHECK (N=" in txt_go
    assert "Shooting GO=" in txt_go
    day_html = rr.build_day_decisions_html(res_go)
    assert "What Happened Next (M3 1-4 Bars Real Tape)" in day_html
    assert "+3 min (1b)" in day_html
    assert "+12 min (4b)" in day_html
    assert "A2 impulse confirmed; route clean" in day_html
