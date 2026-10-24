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

import dashboard  # noqa: E402
import report_office_bundle as rob  # noqa: E402


def test_report_office_bundle_badges_sections_and_idempotency(tmp_path: Path):
    day = "2026-10-23"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (tmp_path / "tools").mkdir(parents=True)
    shutil.copy2(REPO_ROOT / "tools" / "dashboard.py", tmp_path / "tools" / "dashboard.py")

    (data_dir / f"evening_test_gate_{day}.json").write_text(
        json.dumps(
            {
                "date": day,
                "checks": [
                    {"name": "daily_check.sh --check", "status": "PASS", "seconds": 12, "detail": "OK"},
                    {"name": "pytest -q", "status": "PASS", "seconds": 8, "detail": "325 passed"},
                    {"name": "rig_g11.py", "status": "PASS", "seconds": 1, "detail": "ALL PASS"},
                ],
            }
        ),
        encoding="utf-8",
    )
    (data_dir / f"evening_test_gate_{day}.txt").write_text(
        "[PASS] daily_check.sh --check (12s) OK\n[PASS] pytest -q (8s) 325 passed\n",
        encoding="utf-8",
    )
    (data_dir / f"preflight_{day}.txt").write_text(
        "--- 3. .env presence\n[OK] .env exists\nRESULT: 26 ok, 0 warnings, 0 blocking\n",
        encoding="utf-8",
    )
    (data_dir / f"day_metrics_{day}.json").write_text(
        json.dumps(
            {
                "date": day,
                "counts": {"PASS": 10, "WARN": 6, "FAIL": 1, "NA": 0},
                "verdict_line": "VERDICT 2026-10-23: pass 10 warn 6 fail 1 nodata 0",
                "feed_prints": 68690,
                "decisions": 516,
                "diary_records": 516,
                "orders_to_mt5": 0,
            }
        ),
        encoding="utf-8",
    )
    (data_dir / f"decision_ledger_{day}.txt").write_text("CONF GATE (<35%) 181 -583.7\nALL SIGNALS 346 346 -1055.2\n", encoding="utf-8")
    (data_dir / f"shadow_replay_{day}.txt").write_text(
        "SHADOW (walls+gate+trail)             39       -6.7     -0.17   64.1%\n"
        "BENCHMARK fixed ruler, same subset    39      -51.2     -1.31\n"
        "BENCHMARK fixed ruler, ALL signals   346    -1055.2     -3.05\n",
        encoding="utf-8",
    )
    (data_dir / f"tf_robot_{day}.txt").write_text("M1 901 0 455\nM3 901 0 445\nM5 901 0 451\n", encoding="utf-8")
    (data_dir / f"evening_paper_{day}.txt").write_text(
        "[chief of staff] RECONCILIATION (all three must tell the same story):\n"
        "  Accountant ALL SIGNALS : -1055.2\n"
        "  Geometer M5 (audit)    : -1055.2\n"
        "  Shadow BENCHMARK row   : -1055.2   (same ruler, computed independently)\n"
        "  RECONCILED: all three agree within 0.00 pts.\n",
        encoding="utf-8",
    )
    (data_dir / f"tail_probe_{day}.txt").write_text(
        "cycles: 516 [full] | median 2,424 ms, p95 15,210 ms, max 175,303 ms\nowner: STEP3\n",
        encoding="utf-8",
    )
    (data_dir / f"flow_probe_{day}.txt").write_text("FLOW PROBE: LIFT 1.42x\n", encoding="utf-8")
    (data_dir / f"history_summary_{day}.txt").write_text("2026-10-23  68,690  516  -962.0\n", encoding="utf-8")
    (data_dir / f"evening_suite_{day}.log").write_text("full evening log\n", encoding="utf-8")

    # Seed an audit_day-style report_<DATE>.html
    report_html = data_dir / f"report_{day}.html"
    report_html.write_text(
        "<!doctype html><html><head><meta charset='utf-8'></head><body><div class='wrap'>"
        "<h1>Gold-BookMap &mdash; full day report &mdash; 2026-10-23</h1>"
        "<div class='verdict'>VERDICT 2026-10-23</div>"
        "<nav><a href='#panel'>Panel</a><a href='#report'>17 Tests</a></nav>"
        "<section id='panel'><h2>Panel</h2></section>"
        "<section id='report'><h2>17 Tests</h2></section>"
        "</div></body></html>",
        encoding="utf-8",
    )

    out1 = rob.inject_into_report_html(tmp_path, day)
    html1 = out1.read_text(encoding="utf-8")

    assert "ALL PASS" in html1
    assert "26 OK" in html1
    assert "10P / 6W / 1F" in html1
    assert "OK (0.00 pts)" in html1
    assert "-6.7 vs -51.2" in html1
    assert "med 2,424ms" in html1
    assert "id='evening_paper'" in html1
    assert "id='truck_camera'" in html1
    assert "id='flow_spies'" in html1
    assert "id='history_all'" in html1
    assert "id='office_gates'" in html1
    assert "Copy Evening Summary for Chat" in html1
    assert "Desk 6 — The Universe Machine" in html1 or "Desk 6 &mdash;" in html1 or "tf_robot_replay.py" in html1

    # Running a second time must be 100% idempotent (zero duplicate sections/navs)
    out2 = rob.inject_into_report_html(tmp_path, day)
    html2 = out2.read_text(encoding="utf-8")
    assert html1 == html2
    assert html2.count("id='office_hub'") == 1
    assert html2.count("id='evening_paper'") == 1

    # And index.html must link to report_2026-10-23.html, not day_report_2026-10-23.html
    idx_txt = (data_dir / "index.html").read_text(encoding="utf-8")
    assert f'href="report_{day}.html"' in idx_txt


def test_report_office_bundle_fallback_shell_and_failure_badges(tmp_path: Path):
    day = "2026-10-24"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)
    (data_dir / f"evening_test_gate_{day}.json").write_text(
        json.dumps({"date": day, "checks": [{"name": "pytest -q", "status": "FAIL", "seconds": 3, "detail": "1 failed"}]}),
        encoding="utf-8",
    )
    (data_dir / f"evening_paper_{day}.txt").write_text(
        "[chief of staff] RECONCILIATION:\n  FLAG: spread 9.50 pts - a desk is telling a different story.\n",
        encoding="utf-8",
    )
    out = rob.inject_into_report_html(tmp_path, day)
    html = out.read_text(encoding="utf-8")
    assert "1 FAIL" in html
    assert "FLAG (9.50 pts)" in html
    assert "<details open><summary><h2>Tests &amp; Setup Gate" in html


def test_dashboard_prefers_full_report_with_legacy_fallback(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True)

    # Legacy day: only day_report_<date>.html exists
    (data_dir / "day_metrics_2026-10-08.json").write_text(
        json.dumps({"date": "2026-10-08", "counts": {"PASS": 12, "WARN": 4, "FAIL": 1, "NA": 0}}),
        encoding="utf-8",
    )
    (data_dir / "day_report_2026-10-08.html").write_text("<html>legacy</html>", encoding="utf-8")

    # Modern day: both day_report_ and full report_ exist
    (data_dir / "day_metrics_2026-10-23.json").write_text(
        json.dumps({"date": "2026-10-23", "counts": {"PASS": 10, "WARN": 6, "FAIL": 1, "NA": 0}}),
        encoding="utf-8",
    )
    (data_dir / "day_report_2026-10-23.html").write_text("<html>partial</html>", encoding="utf-8")
    (data_dir / "report_2026-10-23.html").write_text("<html>full master</html>", encoding="utf-8")

    idx_path = dashboard.write_index(data_dir)
    idx_html = idx_path.read_text(encoding="utf-8")
    assert 'href="day_report_2026-10-08.html"' in idx_html
    assert 'href="report_2026-10-23.html"' in idx_html
    assert "location='report_2026-10-23.html'" in idx_html

    hist_html = dashboard.history_block(data_dir)
    assert 'href="day_report_2026-10-08.html"' in hist_html
    assert 'href="report_2026-10-23.html"' in hist_html


def test_report_evening_sh_end_to_end_auto_date_and_fail_closed_gate(tmp_path: Path):
    # Build a synthetic recorded day using audit_day._make_demo_day
    import audit_day

    proj = tmp_path / "Gold-BookMap"
    proj.mkdir(parents=True)
    audit_day._make_demo_day(proj)
    demo_date = audit_day.DEMO_DAY.isoformat()  # 2099-01-05

    # Copy Company 2 scripts into the isolated project
    files_to_copy = [
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
    ]
    for rel in files_to_copy:
        src = REPO_ROOT / rel
        if src.exists():
            shutil.copy2(src, proj / rel)

    (proj / "tools").mkdir(exist_ok=True)
    for rel in (
        "tools/dashboard.py",
        "tools/insight.py",
        "tools/check_history.py",
        "tools/report_office_bundle.py",
    ):
        src = REPO_ROOT / rel
        if src.exists():
            shutil.copy2(src, proj / rel)

    env = dict(os.environ)
    env["GBM_NO_BROWSER"] = "1"
    env.pop("GBM_EVENING_SUITE_ACTIVE_LOG", None)

    # 1. Run with NO date argument -> must auto-detect 2099-01-05 from logs/trading_20990105.log,
    #    tee evening_suite_2099-01-05.log, run preflight + audit_day + tail_probe + Evening Paper,
    #    and stitch everything into data/report_2099-01-05.html.
    proc = subprocess.run(
        ["bash", "report_evening.sh", "--fast", "--no-check", "--no-tests", "--no-browser"],
        cwd=str(proj),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert f"newest trading log: {demo_date}" in proc.stdout

    suite_log = proj / "data" / f"evening_suite_{demo_date}.log"
    master_html = proj / "data" / f"report_{demo_date}.html"
    index_html = proj / "data" / "index.html"
    assert suite_log.exists() and suite_log.stat().st_size > 500
    assert master_html.exists()
    html_txt = master_html.read_text(encoding="utf-8")
    assert "id='office_hub'" in html_txt
    assert "id='evening_paper'" in html_txt
    assert "id='truck_camera'" in html_txt
    assert "id='panel'" in html_txt
    assert "id='report'" in html_txt
    assert index_html.exists()
    assert f'href="report_{demo_date}.html"' in index_html.read_text(encoding="utf-8")

    # 2. Fail-closed test: if daily_check.sh --check fails, report_evening.sh MUST exit 1 immediately
    (proj / "daily_check.sh").write_text("#!/usr/bin/env bash\necho '[FAIL] broken auditor'\nexit 1\n", encoding="utf-8")
    bad_proc = subprocess.run(
        ["bash", "report_evening.sh", "2099-01-06", "--no-tests", "--no-browser"],
        cwd=str(proj),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert bad_proc.returncode != 0
    assert "Auditor self-check failed" in bad_proc.stdout
    gate_fail = json.loads((proj / "data" / "evening_test_gate_2099-01-06.json").read_text(encoding="utf-8"))
    assert gate_fail["checks"][0]["status"] == "FAIL"
