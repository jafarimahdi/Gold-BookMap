import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
TOOLS_DIR = REPO_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import report_office_bundle as rob  # noqa: E402
import xray_report as xr  # noqa: E402


def _dir_fingerprint(p: Path):
    if not p.exists():
        return {}
    return {
        f.relative_to(p).as_posix(): (f.stat().st_size, f.stat().st_mtime_ns)
        for f in sorted(p.rglob("*"))
        if f.is_file()
    }


def test_xray_report_funnel_drift_redaction_and_vault_unpack(tmp_path: Path):
    day = "2026-10-28"
    compact = "20261028"
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    data_dir.mkdir(parents=True)
    logs_dir.mkdir(parents=True)

    # Ensure live repo data/logs are never touched when running against tmp_path
    before_data = _dir_fingerprint(REPO_ROOT / "data")
    before_logs = _dir_fingerprint(REPO_ROOT / "logs")

    # 1. Seed run_config with a synthetic secret to verify two-layer redaction
    (data_dir / f"run_config_{compact}.json").write_text(
        json.dumps(
            {
                "date": day,
                "mode": "loop",
                "CONFIDENCE_THRESHOLD": 35.0,
                "GEMINI_MODELS": "gemini-3.8-flash,gemini-3.6-flash,gemini-3.5-flash-lite",
                "GEMINI_API_KEY": "AIzaSyFAKE_SUPER_SECRET_KEY_1234567890",
                "MT5_PASSWORD": "TopSecretPassword999",
                "max_tokens": 1024,
            }
        ),
        encoding="utf-8",
        newline="\n",
    )

    # 2. Seed decisions CSV with directional POWER + Crowd v2 Q2 vetoes + async/cached/failed rows
    csv_lines = [
        "timestamp,symbol,price,signal_direction,signal_strength,signal_confidence,regime,divergence,"
        "ai_action,ai_confidence,ai_model,decision_source,power_direction,power_reason_code,session_state,"
        "execution_mode,shooting_status,shooting_reason,shooting_sub_checks,paper_entry_status,exec_status,"
        "outcome,replay_status,order_id,news_state,minutes_to_event,next_event_title,shadow_candidates,ai_rationale,reason",
        '2026-10-28T10:00:00+00:00,GCZ6.COMEX@RITHMIC,4080.0,SELL,45.0,55.0,TREND,0.0,'
        'SELL,62.0,gemini-3.6-flash,STEP3,DOWN,DOWNWARD_FORCE_DOMINATES,"LONDON | market_open=yes | budapest_trading=yes",'
        'python,WAIT,"crowd v2 Q2 warming up","[""crowd v2 Q2 road history warming up (4/12 samples) - fail closed""]",'
        'NO_PLAN,SKIPPED,SKIPPED,NOT_RUN_IN_LIVE_CYCLE,,QUIET,120.0,GDP,{},"[A2] lean ok","Shooting veto"',
        '2026-10-28T10:01:00+00:00,GCZ6.COMEX@RITHMIC,4081.0,SELL,46.0,58.0,TREND,0.0,'
        'SELL,64.0,gemini-3.6-flash-cached,AI_STANDING_PERMISSION,DOWN,DOWNWARD_FORCE_DOMINATES,"LONDON | market_open=yes | budapest_trading=yes",'
        'python,WAIT,"crowd v2 Q2 warming up","[""crowd v2 Q2 road history warming up (5/12 samples) - fail closed""]",'
        'NO_PLAN,SKIPPED,SKIPPED,NOT_RUN_IN_LIVE_CYCLE,,QUIET,119.0,GDP,{},"cached permission","Shooting veto"',
        '2026-10-28T10:02:00+00:00,GCZ6.COMEX@RITHMIC,4082.0,NEUTRAL,12.0,15.0,RANGE,0.0,'
        'HOLD,0.0,none-call-failed,STEP3,NEITHER,RANGE_NO_CONFIRMED_BREAKOUT,"LONDON | market_open=yes | budapest_trading=yes",'
        'python,WAIT,"POWER v2 says NEITHER","[""POWER v2 says NEITHER; no directional shot""]",'
        'NO_PLAN,SKIPPED,SKIPPED,NOT_RUN_IN_LIVE_CYCLE,,QUIET,118.0,GDP,{},"slice timeout","confidence 0.0% below threshold 35%"',
    ]
    (data_dir / f"decisions_{compact}.csv").write_text("\n".join(csv_lines) + "\n", encoding="utf-8", newline="\n")

    # 3. Seed day_metrics (0 MT5 orders) + drifted trade_guard_state (trades_today=2)
    (data_dir / f"day_metrics_{day}.json").write_text(
        json.dumps(
            {
                "date": day,
                "counts": {"PASS": 15, "WARN": 2, "FAIL": 0, "NA": 0},
                "verdict_line": f"VERDICT {day}: pass 15 warn 2 fail 0 nodata 0",
                "feed_prints": 12000,
                "decisions": 3,
                "orders_to_mt5": 0,
            }
        ),
        encoding="utf-8",
        newline="\n",
    )
    (data_dir / "trade_guard_state.json").write_text(
        json.dumps({"date": day, "trades_today": 2, "halted": False}),
        encoding="utf-8",
        newline="\n",
    )
    (data_dir / "gemini_keys_state.json").write_text(
        json.dumps({"cooldowns": {"abcdef0123456789": "2026-10-28T12:00:00+00:00"}}),
        encoding="utf-8",
        newline="\n",
    )
    (data_dir / f"judge_panel_{day}.csv").write_text(
        "judge,votes,right_pct,points_after_spread,weight_now\n"
        "poc_day,20,75.0,42.5,0.5\n"
        "news_sentiment,10,50.0,-18.2,1.0\n",
        encoding="utf-8",
        newline="\n",
    )
    (logs_dir / f"trading_{compact}.log").write_text(
        "2026-10-28 10:00:01,000 - ERROR - Gemini key AIzaSyLEAKED_LOG_KEY_9876543210 failed\n"
        "2026-10-28 10:00:02,000 - WARNING - Step 4 basis check GC-XAUUSD=+38.90\n",
        encoding="utf-8",
        newline="\n",
    )

    txt_path, json_path, summary = xr.write_xray_files(tmp_path, day)
    assert txt_path.exists() and json_path.exists()

    full_txt = txt_path.read_text(encoding="utf-8")
    assert "\r\n" not in (txt_path.read_bytes().decode("utf-8"))

    # Secrets must be completely scrubbed everywhere
    assert "AIzaSyFAKE_SUPER_SECRET_KEY_1234567890" not in full_txt
    assert "AIzaSyLEAKED_LOG_KEY_9876543210" not in full_txt
    assert "TopSecretPassword999" not in full_txt
    assert "abcdef0123456789" not in full_txt
    assert "max_tokens=1024" in full_txt

    # Funnel & normalized sample grouping check: (4/12 samples) + (5/12 samples) -> 2x (<k>/12 samples)
    assert summary["overall_status"] == "ATTENTION"
    assert summary["funnel"]["total_cycles"] == 3
    assert summary["funnel"]["power_v2_directional"] == 2
    assert summary["funnel"]["a2_aggressive_fires"] == 1
    assert "2x  crowd v2 Q2 road history warming up (<k>/12 samples) - fail closed" in full_txt

    # State-hygiene drift check caught trades_today=2 vs 0 MT5 orders
    assert any("trades_today=2" in d for d in summary["state_hygiene"]["drifts"])
    assert "[DRIFT] trade_guard_state.json trades_today=2" in full_txt

    # Judge net points parsed from points_after_spread
    assert "poc_day(+42.5pts, 20v, 75.0%)" in full_txt

    # Round-trip unpack from the single plain-text file
    unpacked_dir = tmp_path / "unpacked"
    unpacked = xr.unpack_vault(txt_path, unpacked_dir)
    unpacked_names = {p.name for p in unpacked}
    assert f"decisions_{compact}.csv" in unpacked_names
    assert f"run_config_{compact}.json" in unpacked_names
    assert f"judge_panel_{day}.csv" in unpacked_names
    # Even unpacked run_config must remain sanitized
    unpacked_cfg = (unpacked_dir / f"run_config_{compact}.json").read_text(encoding="utf-8")
    assert "TopSecretPassword999" not in unpacked_cfg

    # HTML bundle embeds X-Ray section, copy button, and stays strictly idempotent
    (tmp_path / "tools").mkdir(exist_ok=True)
    shutil.copy2(REPO_ROOT / "tools" / "dashboard.py", tmp_path / "tools" / "dashboard.py")
    (data_dir / f"signal_health_{day}.txt").write_text("SIGNAL HEALTH OK: whale_walls voted 12 times\n", encoding="utf-8", newline="\n")

    html_p1 = rob.inject_into_report_html(tmp_path, day)
    h1 = html_p1.read_text(encoding="utf-8")
    assert "id='xray_overview'" in h1
    assert "Copy X-Ray Overview for Chat" in h1
    assert f"data/xray_{day}.txt" in h1
    assert "Signal Team Doctor" in h1
    # Vault bulk data must NOT bloat the HTML page
    assert "XRAY_ARTIFACT_BEGIN" not in h1

    html_p2 = rob.inject_into_report_html(tmp_path, day)
    h2 = html_p2.read_text(encoding="utf-8")
    assert h1 == h2

    assert _dir_fingerprint(REPO_ROOT / "data") == before_data
    assert _dir_fingerprint(REPO_ROOT / "logs") == before_logs


def test_report_evening_sh_builds_single_file_xray_txt_and_embeds_suite_log(tmp_path: Path):
    import audit_day

    proj = tmp_path / "Gold-BookMap"
    proj.mkdir(parents=True)
    audit_day._make_demo_day(proj)
    demo_date = audit_day.DEMO_DAY.isoformat()

    for rel in (
        "report_evening.sh",
        "daily_check.sh",
        "preflight.sh",
        "audit_day.py",
        "dashboard.py",
        "tail_probe.py",
        "decision_ledger.py",
        "tf_study.py",
        "shadow_replay.py",
        "power_report_card.py",
        "council_shadow.py",
        "clocks_verdict.py",
    ):
        src = REPO_ROOT / rel
        if src.exists():
            shutil.copy2(src, proj / rel)

    (proj / "tools").mkdir(exist_ok=True)
    for rel in (
        "tools/dashboard.py",
        "tools/insight.py",
        "tools/check_history.py",
        "tools/report_office_bundle.py",
        "tools/xray_report.py",
        "tools/signal_health.py",
        "tools/signal_map.py",
    ):
        src = REPO_ROOT / rel
        if src.exists():
            shutil.copy2(src, proj / rel)

    env = dict(os.environ)
    env["GBM_NO_BROWSER"] = "1"
    env.pop("GBM_EVENING_SUITE_ACTIVE_LOG", None)

    proc = subprocess.run(
        ["bash", "report_evening.sh", "--fast", "--no-check", "--no-tests", "--no-browser"],
        cwd=str(proj),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"

    xray_txt = proj / "data" / f"xray_{demo_date}.txt"
    xray_json = proj / "data" / f"xray_{demo_date}.json"
    master_html = proj / "data" / f"report_{demo_date}.html"
    assert xray_txt.exists() and xray_txt.stat().st_size > 1500
    assert xray_json.exists()
    assert master_html.exists()

    txt_content = xray_txt.read_text(encoding="utf-8")
    # Post-tee refresh must embed the closed evening_suite_<DATE>.log and see report_<DATE>.html PRESENT
    assert f'XRAY_ARTIFACT_BEGIN name="evening_suite_{demo_date}.log"' in txt_content
    assert f"report_{demo_date}.html" in txt_content

    html_content = master_html.read_text(encoding="utf-8")
    assert "id='xray_overview'" in html_content
    assert "Copy X-Ray Overview for Chat" in html_content
