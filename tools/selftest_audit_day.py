#!/usr/bin/env python3
"""
selftest_audit_day.py -- proves audit_day.py actually reads and scores data.

It builds a FAKE but complete trading day (14 hours of ticks, 200 decisions,
a snapshot diary, a log with latency lines) in a temp folder, runs
audit_day.py against it, and checks that every test produced a real grade
instead of "no data". If this passes, the audit is trustworthy on real data.

    python tools/selftest_audit_day.py
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DAY = "2099-01-05"          # deliberately impossible date, never collides
UTC_DAY = datetime(2099, 1, 5, tzinfo=timezone.utc)

EXPECTED_TESTS = [
    ("1", "ALIVE"), ("2", "FEED"), ("3", "PIPELINE"), ("4", "DATA QUALITY"),
    ("5", "GUARD"), ("6", "SIGNAL"), ("7", "WHAT-IF"), ("8", "TEAM/JUDGE"),
    ("9", "JUDGE PANEL"), ("10", "DIARY COVERAGE"), ("11", "VERSION"),
    ("12", "SPEED"), ("13", "MONEY"), ("14", "EVIDENCE"), ("15", "HOUR"),
]


def build_fake_day(dst: Path, n_prints=54000, n_decisions=200, n_diary=120, n_cycles=400):
    """Build a fake but COMPLETE day. Sizes are knobs: the selftest uses small
    numbers so it runs in a second; a stress run asks for a real day's volume."""
    rnd = random.Random(7)
    (dst / "data").mkdir(parents=True, exist_ok=True)
    (dst / "logs").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "audit_day.py", dst / "audit_day.py")
    # the fixture is a complete mini-project: the auditor's optional layers must be found
    # the same way they are in the real folder, or the pages/modes silently disappear
    (dst / "tools").mkdir(parents=True, exist_ok=True)
    for rel in ("tools/insight.py", "tools/dashboard.py", "dashboard.py"):
        if (ROOT / rel).exists():
            shutil.copy(ROOT / rel, dst / rel)
    (dst / ".env").write_text(
        "BOOKMAP_WINDOW_SECONDS=10800\nBOOKMAP_MAX_DEPTH_LEVELS=20\n"
        "AI_MIN_SIGNAL_STRENGTH=6\nCONFIDENCE_THRESHOLD=50\n"
        "V6_CFD_SPREAD_MAX=0.50\nTRADING_ENABLED=1\n", encoding="utf-8")

    # ---- ticks: 06:00-21:00 UTC, one print per second-ish, random walk ----
    ticks, price = [], 2032.0
    t = UTC_DAY + timedelta(hours=6)
    end = UTC_DAY + timedelta(hours=21)
    step = max(0.05, (end - t).total_seconds() / max(1, n_prints))
    while t < end:
        price = max(1900.0, price + rnd.gauss(0, 0.30))
        ticks.append(f"{t:%Y-%m-%d %H:%M:%S}.{int((t.microsecond // 1000) % 1000):03d}"
                     f",Last,{price:.2f},{rnd.randint(1, 40)},,,GC 12-26")
        t += timedelta(seconds=step)
    (dst / "ticks.csv").write_text(
        "time,event,price,size,level,operation,instrument\n" + "\n".join(ticks) + "\n",
        encoding="utf-8")

    # ---- decisions: 200 of them, 8 over the 50% gate ----------------------
    header = ("timestamp,symbol,price,signal_direction,signal_strength,signal_confidence,"
              "regime,divergence,ai_action,ai_confidence,exec_status,order_id,news_state,"
              "minutes_to_event,next_event_title,reason")
    rows = []
    gap = (end - (UTC_DAY + timedelta(hours=6))).total_seconds() / max(1, n_decisions) / 60.0
    for i in range(n_decisions):
        dt = UTC_DAY + timedelta(hours=6) + timedelta(minutes=i * gap + 1)
        idx = min(len(ticks) - 1, int(i * gap * 60 / step) + 1)
        px = float(ticks[idx].split(",")[2])
        strong = i % 25 == 3
        conf = rnd.uniform(52, 64) if strong else rnd.uniform(0, 45)
        d = rnd.choice(["BUY", "SELL"]) if conf > 30 else "NEUTRAL"
        rows.append(",".join([
            f"{dt:%Y-%m-%dT%H:%M:%S}", "GC 12-26", f"{px:.2f}", d,
            f"{conf/2.2:.2f}", f"{conf:.2f}", rnd.choice(["TREND", "RANGE", "VOLATILE"]),
            "0.0", "BUY" if (strong and d == "BUY") else ("SELL" if strong else "HOLD"),
            f"{conf + 2:.1f}", "SKIPPED", "", "QUIET", "120", "ECB speaker",
            "confidence below threshold" if not strong else "signal accepted"]))
    (dst / "data" / "decisions_log.csv").write_text(header + "\n" + "\n".join(rows) + "\r\n",
                                                    encoding="utf-8")

    # ---- diary: per-team and per-judge scores ----------------------------
    with open(dst / "data" / "snapshots_history.jsonl", "w", encoding="utf-8") as f:
        dgap = (end - (UTC_DAY + timedelta(hours=6))).total_seconds() / max(1, n_diary) / 60.0
        for i in range(n_diary):
            dt = UTC_DAY + timedelta(hours=6) + timedelta(minutes=i * dgap + 2)
            idx = min(len(ticks) - 1, int(i * dgap * 60 / step) + 2)
            px = float(ticks[idx].split(",")[2])
            side = rnd.choice(["BUY", "SELL"])
            notes = [
                f"footprint delta {rnd.uniform(-0.6, 0.6):+.3f} dominant {px-0.4:.1f} "
                f"strength {rnd.uniform(0.2, 0.9):.2f} -> {side}",
                f"L3 distance-weighted imbalance {rnd.uniform(-0.4,0.4):+.3f} bid "
                f"{rnd.randint(200,900)} ask {rnd.randint(200,900)} -> {'SELL' if side=='BUY' else 'BUY'}",
                f"L3 NET FLOW {side} {rnd.uniform(-400,400):+.0f} (buys {rnd.randint(200,900)}.0 "
                f"vs sells {rnd.randint(200,900)}.0) w 1.5 -> {side}",
                f"ICEBERG_{'SUPPORT' if side=='BUY' else 'RESISTANCE'} 4 @ {px-0.6:.1f} "
                f"refills 3 w {rnd.uniform(0.6,1.4):.1f} -> {side} (persistent)",
                f"L3 whale {'SUPPORT' if side=='BUY' else 'RESISTANCE'} 2 walls "
                f"{rnd.uniform(400,1500):.0f} lots closest {px:.1f} dist 0.11% w 1.4 -> {side}",
                "VWAP trend UP price %.1f vs VWAP %.1f" % (px, px - 1.2),
                "near %s zone %.1f (bounce) recency 1.10x -> BUY" % ("demand", px - 1.5),
                f"CVD {'rising' if side=='BUY' else 'falling'} delta {rnd.uniform(-200,200):+.0f} -> {side} momentum",
                f"v6.0 4 Teams ensemble: flow {rnd.uniform(-1,1):+.2f}*1.5 whale {rnd.uniform(-1,1):+.2f}*1.4 "
                f"struct {rnd.uniform(-1,1):+.2f}*1.2 trend {rnd.uniform(-1,1):+.2f}*0.8 -> {rnd.uniform(-1,1):+.3f} confluence OK",
            ]
            f.write(json.dumps({
                "timestamp": f"{dt:%Y-%m-%dT%H:%M:%S+00:00}", "price": px,
                "signal_direction": side if i % 5 == 0 else "NEUTRAL",
                "regime": rnd.choice(["TREND", "RANGE", "NEUTRAL"]),
                "strength": round(rnd.uniform(2, 48), 1),
                "confidence": round(rnd.uniform(30, 70), 1),
                "killzone": rnd.choice(["LONDON", "NY", "OVERLAP", "OFF_LUNCH"]),
                "team_scores": {"flow": rnd.choice([1.2, -0.8, 0.4, -1.5]),
                                "whale": rnd.choice([0.9, -1.1, 0.0, 1.4]),
                                "struct": rnd.choice([-0.6, 1.0, 0.2, -1.3]),
                                "trend": rnd.choice([0.5, -0.5, 0.8, -0.2])},
                "notes": notes,
                # v7.1 real shape: judge_votes is a LIST of dicts, not a dict. Without
                # this the selftest could not see the 'list has no .items()' crash that
                # killed a live report on 2026-09-24.
                "judge_votes": [
                    {"judge": "footprint_delta", "dir": rnd.choice([1.0, -1.0]),
                     "weight": 1.5, "raw": "footprint delta"},
                    {"judge": "l3_net_flow", "dir": rnd.choice([1.0, -1.0, 0.0]),
                     "weight": 1.5, "raw": "L3 NET FLOW"},
                    {"judge": "vwap_trend", "dir": 1.0, "weight": 1.0, "raw": "VWAP trend UP"},
                    {"judge": "poc_day", "dir": 0.0, "weight": 1.0, "raw": "POC day"},
                ],
            }) + "\n")
    (dst / "data" / "tracked_bot_positions.json").write_text(
        json.dumps({"position_ids": [], "updated_at": f"{DAY}T21:00:00"}), encoding="utf-8")
    (dst / "data" / "market_snapshot.json").write_text(
        json.dumps({"price": 2033.1, "direction": "BUY"}), encoding="utf-8")
    (dst / "data" / "mt5_signal.txt").write_text(
        f"timestamp={DAY}T21:00:00+00:00\ndirection=NEUTRAL\nconfidence=0.0\nlots=0.0\n"
        f"sl=0.0\ntp=0.0\n", encoding="utf-8")

    # ---- log -------------------------------------------------------------
    lines = []
    cgap = (end - (UTC_DAY + timedelta(hours=6))).total_seconds() / max(1, n_cycles)
    for i in range(n_cycles):
        st = UTC_DAY + timedelta(hours=6) + timedelta(seconds=i * cgap + 1)
        local = st + timedelta(hours=1)
        lat = rnd.randint(120, 900)
        lines.append(f"{local:%Y-%m-%d %H:%M:%S},{rnd.randint(0,999):03d} INFO    [main] "
                     f"GOLD TRADING SYSTEM — pipeline start (trade=XAUUSD, timeframe=M5, source=bookmapbridge)")
        lines.append(f"{local:%Y-%m-%d %H:%M:%S},{rnd.randint(0,999):03d} INFO    [main] LATENCY STEP1 acquire "
                     f"{rnd.randint(80,160)}ms")
        lines.append(f"{local:%Y-%m-%d %H:%M:%S},{rnd.randint(0,999):03d} INFO    [main] LATENCY STEP2 analyze "
                     f"{rnd.randint(150,300)}ms")
        lines.append(f"{local:%Y-%m-%d %H:%M:%S},{rnd.randint(0,999):03d} INFO    [main] LATENCY STEP3 AI "
                     f"{rnd.choice([1, 1, 1, 900, 1400])}ms")
        lines.append(f"{local:%Y-%m-%d %H:%M:%S},{rnd.randint(0,999):03d} INFO    [main] STEP 2  MARKET "
                     f"ANALYSIS -> OK -> NEUTRAL (strength {rnd.uniform(0,40):.1f}, confidence {rnd.uniform(0,60):.1f})")
        lines.append(f"{local:%Y-%m-%d %H:%M:%S},{rnd.randint(0,999):03d} INFO    [main] STEP 3  AI DECISION "
                     f"-> HOLD (signal {rnd.uniform(0,12):.1f} < 6, AI skipped)")
        lines.append(f"{local:%Y-%m-%d %H:%M:%S},{rnd.randint(0,999):03d} INFO    [main] STEP 4  EXECUTION "
                     f"-> SKIPPED — confidence below threshold")
        lines.append(f"{local:%Y-%m-%d %H:%M:%S},{rnd.randint(0,999):03d} WARNING [main] LATENCY TOTAL "
                     f"pipeline {lat}ms > target 500ms" if lat > 500 else
                     f"{local:%Y-%m-%d %H:%M:%S},{rnd.randint(0,999):03d} INFO    [main] L4 Latency summary: "
                     f"total {lat}ms target 500ms")
        lines.append(f"{local:%Y-%m-%d %H:%M:%S},{rnd.randint(0,999):03d} INFO    [main] LATENCY TOTAL "
                     f"pipeline {lat}ms target 500ms")
    (dst / "logs" / f"trading_{DAY.replace('-', '')}.log").write_text("\n".join(lines) + "\n",
                                                                      encoding="utf-8")


# =========================================================================== #
# 24f: THE WRITING SIDE OF THE DAY.
# The audit can only be as good as what the robot writes before it is read, so
# these cases drive the ROBOT'S OWN writer functions and then grade the result
# with the REAL auditor. A writer/reader mismatch is otherwise invisible until
# the night you need the report.
# =========================================================================== #

WRITER_SCRIPT = r"""
import sys, types
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, ".")
import main

# 24u: freeze this fixture at midday UTC. main stamps rows and names day-files from
# datetime.now(); at 23:58 UTC that lands on a different Budapest day than the one the
# auditor reads, and the contract check fails for a reason that has nothing to do with
# the robot. Midday is the one hour where every calendar agrees.
class _FrozenDT(datetime):
    _BASE = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0,
                                               microsecond=0)

    @classmethod
    def now(cls, tz=None):
        return cls._BASE.astimezone(tz) if tz is not None else cls._BASE.replace(tzinfo=None)

    @classmethod
    def utcnow(cls):
        return cls._BASE.replace(tzinfo=None)

main.datetime = _FrozenDT
# every path the writers use, pointed at this scratch project
main.DATA_DIR = Path("data")
main.LOGS_DIR = Path("logs")
main._POSITION_IDS_FILE = Path("data/tracked_bot_positions.json")
main._OUTCOME_KEYS_FILE = Path("data/logged_outcome_deals.json")
main._PLUMBING_DEALS_FILE = Path("data/plumbing_test_deals.json")
main.setup_logging()

main._write_run_config()
main._log_session_start("once")

snap = types.SimpleNamespace(
    symbol="XAUUSD", price=2033.1, signal_direction="BUY", signal_strength=12.0,
    confidence=55.0, regime="TREND", divergence=0.0, notes=["contract test"],
    judge_votes=[], timestamp=_FrozenDT.now(timezone.utc),
    news=types.SimpleNamespace(news_state="CALM", minutes_to_next_event=60,
                               next_event_title="none"))
dec = types.SimpleNamespace(action="BUY", confidence=55.0, rationale="contract test")
main.log_decision(snap, dec, types.SimpleNamespace(status="EXECUTED", order_id="999001",
                                                   reason="contract test"))
main.log_decision(snap, dec, types.SimpleNamespace(status="SKIPPED", order_id="",
                                                   reason="confidence below threshold"))
main._append_diary({"timestamp": snap.timestamp.isoformat(), "price": 2033.1,
                    "signal_direction": "BUY", "signal_strength": 12.0, "confidence": 55.0,
                    "regime": "TREND", "team_scores": {"flow": 0.2}, "killzone": "LONDON",
                    "judge_votes": [], "notes": ["contract test"]},
                   Path("data/snapshots_history.jsonl"))
main._save_tracked_position_ids({999001})
try:
    import mt5_signal_bridge as msb
    for attr in ("DATA_DIR", "_DATA_DIR", "SIGNAL_FILE"):
        if hasattr(msb, attr):
            setattr(msb, attr, Path("data/mt5_signal.txt") if attr == "SIGNAL_FILE" else Path("data"))
    main.run_signal_bridge(snap, dec)
except Exception as exc:
    print("SIGNAL-BRIDGE-SKIPPED", type(exc).__name__, exc)
main._session_end_summary()
print("WROTE-OK")
"""


def _copy_project(dst: Path) -> bool:
    (dst / "data").mkdir(parents=True, exist_ok=True)
    (dst / "logs").mkdir(parents=True, exist_ok=True)
    (dst / "tools").mkdir(parents=True, exist_ok=True)
    for name in ("audit_day.py", "config.py", "main.py"):
        if not (ROOT / name).exists():
            return False
        shutil.copy2(ROOT / name, dst / name)
    if (ROOT / "tools" / "dashboard.py").exists():
        shutil.copy2(ROOT / "tools" / "dashboard.py", dst / "tools" / "dashboard.py")
    if (ROOT / "dashboard.py").exists():
        shutil.copy2(ROOT / "dashboard.py", dst / "dashboard.py")
    (dst / ".env").write_text(
        "BOOKMAP_WINDOW_SECONDS=10800\nBOOKMAP_MAX_DEPTH_LEVELS=20\n"
        "AI_MIN_SIGNAL_STRENGTH=6\nCONFIDENCE_THRESHOLD=50\nV6_CFD_SPREAD_MAX=0.50\n"
        "TRADING_ENABLED=1\nGEMINI_API_KEY=dummy_key_only_for_this_test\n"
        "EXECUTION_MODE=none\n", encoding="utf-8")
    return True


def _child_env() -> dict:
    """Force UTF-8 on every child we spawn, whatever codepage this machine uses.

    On Windows an output PIPE is encoded with the ANSI codepage, so non-ASCII text from
    the auditor arrived mangled and a test asserting on that text failed on the
    operator's PC while passing everywhere else. Never let a test depend on the locale.
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_audit(proj: Path, *extra: str, timeout=600, html: bool = False) -> str:
    # html=True keeps the optional HTML on: --no-html means "no HTML at all", so the
    # learning pages are only produced when HTML is wanted. The pages case needs it.
    flags = ["--no-index", "--no-browser", "--no-refresh"] + ([] if html else ["--no-html"])
    p = subprocess.run([sys.executable, "audit_day.py", *extra, *flags], env=_child_env(),
                       cwd=proj, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    return p.stdout + "\n" + p.stderr


def case_writer_reader(fails: list) -> None:
    """The robot's OWN writers -> the auditor's OWN readers, end to end."""
    proj = Path(tempfile.mkdtemp(prefix="bm_contract_"))
    try:
        if not _copy_project(proj):
            print("[contract] skipped: config.py/main.py not next to the selftest "
                  "(run this from your project folder)")
            return
        (proj / "_write.py").write_text(WRITER_SCRIPT, encoding="utf-8")
        w = subprocess.run([sys.executable, "_write.py"], env=_child_env(), cwd=proj,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=300)
        if w.returncode != 0 or "WROTE-OK" not in w.stdout:
            fails.append("contract: the robot's writers failed: " + (w.stderr or w.stdout)[-300:])
            return
        # 24u: TWO calendars are in play here and they are not the same one.
        #   - the robot STAMPS every row with datetime.now(timezone.utc) and names its
        #     per-day files from the UTC date  -> that is the writers' calendar
        #   - audit_day BUCKETS rows into days by Budapest local time
        #     -> that is the readers' calendar
        # Between 22:00 and 24:00 UTC (00:00-02:00 Budapest in summer) they disagree by
        # one day. A naive datetime.now() matched neither and the contract check failed
        # every night after midnight. Ask each side in its own calendar.
        # the fixture's clock is frozen at 12:00 UTC (see _FrozenDT in the writer),
        # so the UTC date and the Budapest date are the same day by construction.
        _now = datetime.now(timezone.utc)
        day, ymd = _now.strftime("%Y-%m-%d"), _now.strftime("%Y%m%d")
        for want in (f"run_config_{ymd}.json", f"diary_{ymd}.jsonl", "decisions_log.csv",
                     "snapshots_history.jsonl", "tracked_bot_positions.json"):
            if not (proj / "data" / want).exists():
                fails.append(f"contract: the robot did not write data/{want}")
        out = _run_audit(proj, "--date", day)
        if "Traceback" in out:
            fails.append("contract: the auditor crashed on files the robot just wrote")
        for tag, pat in (("run_config read back", r"the robot's own record: run_config_"),
                         ("diary mirror seen", r"diary_\d{8}\.jsonl"),
                         ("the day's decisions counted", r"decisions logged that day: 2"),
                         ("sent to MT5", r"signals that reached MT5: 1"),
                         ("session story", r"the robot recorded its own session"),
                         ("verdict", r"VERDICT \d{4}-\d{2}-\d{2}")):
            if not re.search(pat, out):
                fails.append(f"contract: {tag} missing from the report")
        if not fails:
            print("[contract] robot writers -> auditor readers, end to end: OK")
    finally:
        shutil.rmtree(proj, ignore_errors=True)


def case_no_phantom_files(fails: list) -> None:
    """Every file the auditor reads must be written by something in the project."""
    a = (ROOT / "audit_day.py").read_text(encoding="utf-8", errors="ignore")
    wanted = set(re.findall(r'DATA_DIR\(\)\s*/\s*"([^"]+)"', a))
    wanted |= set(re.findall(r'LOGS_DIR\(\)\s*/\s*"([^"]+)"', a))
    blob = ""
    for f in sorted(ROOT.glob("*.py")) + sorted((ROOT / "tools").glob("*.py")):
        blob += f.read_text(encoding="utf-8", errors="ignore")
    ignore = (".env", "ticks.csv", "mbo.csv", "weight_suggestions.json")
    skip_pref = ("day_audit", "day_report", "day_metrics", "last", "index.html", "judge_panel_")
    phantom = [w for w in sorted(wanted) if w not in blob and w not in ignore
               and not w.startswith(skip_pref) and "{" not in w]
    if phantom:
        fails.append("phantom files (the auditor waits for files nobody writes): "
                     + ", ".join(phantom))
    else:
        print(f"[contract] no phantom files: all {len(wanted)} file names the auditor reads "
              f"are produced inside the project")


def case_odd_shapes(dst: Path, fails: list) -> None:
    """One odd file must not kill the report (it used to end the whole run)."""
    (dst / "data" / "tracked_bot_positions.json").write_text("[111111, 222222]", encoding="utf-8")
    out = _run_audit(dst, "--date", DAY)
    if "Traceback" in out:
        fails.append("odd shapes: the auditor crashed on a list-shaped tracking file")
    for num, name in EXPECTED_TESTS:
        if not re.search(rf"TEST\s+{num}\s+{name}", out):
            fails.append(f"odd shapes: test {num} ({name}) vanished after the odd file")
    if not re.search(r"VERDICT \d{4}-\d{2}-\d{2}", out):
        fails.append("odd shapes: no verdict line")
    if not fails:
        print("[case] odd file shapes: all 15 tests still graded, no crash")


def case_then_now(dst: Path, fails: list) -> None:
    """A past day's grade must not move when TODAY's files change."""
    (dst / "data" / "tracked_bot_positions.json").write_text(
        '{"position_ids": [], "updated_at": "%sT21:00:00"}' % DAY, encoding="utf-8")
    (dst / "data" / f"run_config_{DAY.replace('-', '')}.json").write_text(
        json.dumps({"date": DAY, "written_at": f"{DAY}T07:00:00", "CONFIDENCE_THRESHOLD": 50,
                    "AI_MIN_SIGNAL_STRENGTH": 6, "V6_CFD_SPREAD_MAX": 0.50,
                    "BOOKMAP_WINDOW_SECONDS": 10800, "BOOKMAP_MAX_DEPTH_LEVELS": 20}),
        encoding="utf-8")
    before = _run_audit(dst, "--date", DAY)
    v1 = re.search(r"VERDICT \d{4}-\d{2}-\d{2}: [^\n]+", before)
    env = dst / ".env"
    env.write_text(env.read_text(encoding="utf-8").replace("CONFIDENCE_THRESHOLD=50",
                                                           "CONFIDENCE_THRESHOLD=45"),
                   encoding="utf-8")
    (dst / "data" / "mt5_signal.txt").write_text("direction=BUY\nconfidence=99.9\nlots=9.99\n",
                                                 encoding="utf-8")
    (dst / "data" / "market_snapshot.json").write_text('{"price":9999.9}', encoding="utf-8")
    (dst / "data" / "tracked_bot_positions.json").write_text(
        '{"position_ids": [777777], "updated_at": "2099-02-01T00:00:00"}', encoding="utf-8")
    after = _run_audit(dst, "--date", DAY)
    v2 = re.search(r"VERDICT \d{4}-\d{2}-\d{2}: [^\n]+", after)
    if not (v1 and v2):
        fails.append("then/now: no verdict line to compare")
    elif v1.group(0) != v2.group(0):
        fails.append("then/now: the day's grade moved when today's files changed:\n"
                     f"        before: {v1.group(0)}\n        after : {v2.group(0)}")
    elif not re.search(r"advisory", after):
        fails.append("then/now: the live values are not shown as advisory")
    else:
        print("[case] then/now: same day, today's files changed -> verdict identical, "
              "live values shown as advisory")
    if re.search(r"Traceback", after):
        fails.append("then/now: the auditor crashed")


def case_midnight(dst: Path, fails: list) -> None:
    """A row at 00:30 Budapest belongs to THAT day, not the one before."""
    def count(out):
        m = re.search(r"decisions logged that day: (\d+)", out)
        return int(m.group(1)) if m else None
    base = count(_run_audit(dst, "--date", DAY))
    with open(dst / "data" / "decisions_log.csv", "a", newline="", encoding="utf-8") as fh:
        import csv as _csv
        _csv.writer(fh).writerow(["%sT00:30:00" % DAY, "XAUUSD", "2033.1", "BUY", "12.0", "55.0",
                                  "TREND", "0.0", "BUY", "55.0", "SKIPPED", "", "CALM", "60",
                                  "none", "midnight boundary row"])
    with open(dst / "data" / "snapshots_history.jsonl", "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"timestamp": f"{DAY}T00:30:00+00:00", "price": 2033.1,
                             "signal_direction": "BUY", "signal_strength": 12.0,
                             "confidence": 55.0, "regime": "TREND", "team_scores": {},
                             "killzone": "ASIA", "judge_votes": [],
                             "notes": ["midnight boundary row"]}) + "\n")
    after = count(_run_audit(dst, "--date", DAY))
    if base is None or after is None:
        fails.append("midnight: could not read the day's decision count")
    elif after == base + 1:
        print(f"[case] midnight boundary: the 00:30 local row is counted on its own day "
              f"({base} -> {after})")
    else:
        fails.append(f"midnight: a 00:30 local decision was filed under another day "
                     f"({base} -> {after}, expected +1)")


def case_filtered_files(dst: Path, fails: list) -> None:
    """A filtered grade must not overwrite the day's whole-day card."""
    card = dst / "data" / f"day_metrics_{DAY}.json"
    if not card.exists():
        _run_audit(dst, "--date", DAY)
    before = card.read_bytes() if card.exists() else b""
    out = _run_audit(dst, "--date", DAY, "--from", "10:00", "--to", "14:00")
    if not re.search(r"time filter, not the whole day", out):
        fails.append("filtered: the header does not state the time filter")
    suffixed = list((dst / "data").glob(f"day_metrics_{DAY}_*.json"))
    if not suffixed:
        fails.append("filtered: the filtered grade did not get its own metrics file")
    if card.exists() and card.read_bytes() != before:
        fails.append("filtered: the whole-day card was overwritten by a filtered grade")
    if not fails:
        print("[case] filtered grade: own files, day card untouched, header states the filter")


def case_session_story(dst: Path, fails: list) -> None:
    """The log's own START/END lines become the day's story in test 1."""
    log = dst / "logs" / f"trading_{DAY.replace('-', '')}.log"
    txt = log.read_text(encoding="utf-8")
    txt += (f"{DAY} 07:00:03,001 INFO    [main] SESSION START | mode=loop pid=4242 | gate=50 "
            f"ai_gate=6 spread=0.5 window=10800s depth=20 | bookmapbridge -> XAUUSD\n")
    txt += (f"{DAY} 21:04:12,999 INFO    [main] SESSION END | cycles=800 decisions=200 "
            f"sent_to_mt5=0 ai_calls=40 | records written: decisions_log.csv\n")
    log.write_text(txt, encoding="utf-8")
    out = _run_audit(dst, "--date", DAY)
    if not re.search(r"the robot recorded its own session: START 07:00:03", out):
        fails.append("session story: test 1 did not report the robot's own START line")
    elif not re.search(r"clean start and stop", out):
        fails.append("session story: test 1 did not recognise the clean END")
    else:
        print("[case] session story: the robot's own START/END lines are read back by test 1")


# =========================================================================== #
# 24g: the LEARNING layer (tools/insight.py), driven through the real CLI.
# =========================================================================== #

def _clone_day(dst: Path, new_day: str) -> None:
    """Copy the fixture day onto another date (tape, log, decisions, diary) so the
    multi-day modes have more than one day to fold over."""
    old = DAY
    rows = (dst / "ticks.csv").read_text(encoding="utf-8", errors="ignore").splitlines()
    (dst / "ticks.csv").write_text(
        "\n".join(rows + [r.replace(old, new_day) for r in rows[1:]]) + "\n", encoding="utf-8")
    log = (dst / "logs" / f"trading_{old.replace('-', '')}.log").read_text(encoding="utf-8",
                                                                          errors="ignore")
    (dst / "logs" / f"trading_{new_day.replace('-', '')}.log").write_text(
        log.replace(old, new_day), encoding="utf-8")
    for name in ("decisions_log.csv", "snapshots_history.jsonl"):
        p = dst / "data" / name
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        head = [] if name == "decisions_log.csv" else []
        body = lines[1:] if name == "decisions_log.csv" else lines
        p.write_text("\n".join(lines + [r.replace(old, new_day) for r in body if r.strip()]) + "\n",
                     encoding="utf-8")


def case_pages(dst: Path, fails: list) -> None:
    """Every normal day run leaves a decisions page and a trades page behind."""
    out = _run_audit(dst, "--date", DAY, html=True)
    dec = dst / "data" / f"decisions_{DAY}.html"
    tra = dst / "data" / f"trades_{DAY}.html"
    if not dec.exists():
        fails.append("pages: no decisions_<date>.html was written")
    else:
        h = dec.read_text(encoding="utf-8", errors="ignore")
        for need, what in (("What happened next", "the next-move table"), ("The panel", "the judge panel"),
                           ("class=\"card\"", "clickable decision cards")):
            if need not in h:
                fails.append(f"pages: the decisions page is missing {what}")
    if not tra.exists():
        fails.append("pages: no trades_<date>.html was written")
    elif "Nothing was sent" not in tra.read_text(encoding="utf-8", errors="ignore"):
        fails.append("pages: the trades page does not say plainly that nothing traded")
    if not fails:
        print("[case] insight pages: decisions_<date>.html + trades_<date>.html written and complete")


def case_explain(dst: Path, fails: list) -> None:
    """--explain HH:MM prints one decision with the panel that stood behind it."""
    out = _run_audit(dst, "--date", DAY, "--explain", "12:30")
    # ASCII-only anchors on purpose: a passing test must not need a particular glyph
    for need, what in (("WHY", "the header"), ("decision nearest 12:30", "the header"),
                       ("WHAT IT SAW", "the diary snapshot"),
                       ("judges (", "the vote list"),
                       ("WHAT HAPPENED NEXT", "the tape context")):
        if need not in out:
            fails.append(f"explain: missing {what}")
    if not list((dst / "data").glob(f"explain_{DAY}_*.txt")):
        fails.append("explain: nothing was saved to data/")
    if not fails:
        print("[case] --explain: one decision, its panel, and the tape after it")


def case_weight_ab(dst: Path, fails: list) -> None:
    """--weight-ab compares weight sets on the same snapshots and says when data is thin."""
    out = _run_audit(dst, "--date", DAY, "--weight-ab", "1")
    for need, what in (("WEIGHT A/B", "the header"), ("current (as used)", "the current set"),
                       ("suggested (from accuracy)", "the suggested set"),
                       ("equal weight (1.0)", "the control set"),
                       ("same snapshots for every weight set", "the fairness note")):
        if need not in out:
            fails.append(f"weight-ab: missing {what}")
    if not list((dst / "data").glob("weights_ab_*.txt")):
        fails.append("weight-ab: nothing was saved to data/")
    if not fails:
        print("[case] --weight-ab: three weight sets scored on identical snapshots")


def case_walk_forward(dst: Path, fails: list) -> None:
    """walk-forward: honest refusal on one day, a real fold once a second day exists."""
    thin = _run_audit(dst, "--date", DAY, "--walk-forward", "2")
    if "need at least 3 days" not in thin:
        fails.append("walk-forward: did not refuse honestly when there is not enough history")
    _clone_day(dst, "2099-01-06")
    for f in (dst / "data").glob("prices_*.json"):
        f.unlink()
    out = _run_audit(dst, "--date", "2099-01-06", "--walk-forward", "1")
    for need, what in (("WALK-FORWARD", "the header"), ("no peeking", "the no-peeking note"),
                       ("folds scored: 1", "one scored fold"), ("VERDICT:", "a verdict")):
        if need not in out:
            fails.append(f"walk-forward: missing {what}")
    if not list((dst / "data").glob("walkforward_*.txt")):
        fails.append("walk-forward: nothing was saved to data/")
    if not fails:
        print("[case] --walk-forward: refuses on thin history, runs a real fold with two days")


def case_price_cache(dst: Path, fails: list) -> None:
    """The minute-price cache is a cache, not a memory of the past.

    During a live day the tape grows every second. A cache that is merely *present* would
    serve the 09:00 prices to a 15:00 run and quietly blank out the "what happened next"
    columns of every later decision - the exact kind of silent wrong number this whole
    system exists to prevent. So: touch the tape, and the cache must rebuild.
    """
    _run_audit(dst, "--date", DAY, "--explain", "12:30")
    cache_p = dst / "data" / f"prices_{DAY.replace('-', '')}.json"
    if not cache_p.exists():
        fails.append("cache: no prices_<ymd>.json was written")
        return
    before = json.loads(cache_p.read_text(encoding="utf-8"))
    lines = (dst / "ticks.csv").read_text(encoding="utf-8", errors="ignore").splitlines()
    day_rows = [l for l in lines if l.startswith(DAY) and l.count(",") == 6]
    if not day_rows:
        fails.append("cache: fixture tape has no 7-column rows for the day")
        return
    last = day_rows[-1].split(",")
    stamp = datetime.strptime(last[0][:19], "%Y-%m-%d %H:%M:%S") + timedelta(minutes=2)
    last[0] = stamp.strftime("%Y-%m-%d %H:%M:%S")
    last[2] = "2199.99"
    (dst / "ticks.csv").write_text("\n".join(lines + [",".join(last)]) + "\n", encoding="utf-8")
    out = _run_audit(dst, "--date", DAY, "--explain", "12:30")
    after = json.loads(cache_p.read_text(encoding="utf-8"))
    if len(after["points"]) <= len(before["points"]):
        fails.append("cache: the tape grew but the cached price series did not")
    elif after["points"][-1][1] != 2199.99:
        fails.append("cache: rebuilt series does not contain the newest print")
    elif float(after["tape_mtime"]) <= float(before["tape_mtime"]):
        fails.append("cache: the tape moved but the cache still claims the old tape")
    if not fails:
        print(f"[case] price cache: tape grew -> cache rebuilt "
              f"({len(before['points'])} -> {len(after['points'])} minutes, newest print visible)")


def case_session_buckets(dst: Path, fails: list) -> None:
    """The four session lines must add up to the hourly table - no hour in no session.

    They did not: hour 12 belonged to no bucket, so the report said 185 of 200 decisions
    and the dashboard under-counted noon. The report now states the sum out loud; this
    case fails if the two ever disagree again.
    """
    out = _run_audit(dst, "--date", DAY)
    m = re.search(r"the four sessions cover (\d+) of the (\d+) decision", out)
    if not m:
        fails.append("sessions: the report no longer states how many decisions the sessions cover")
    elif m.group(1) != m.group(2):
        fails.append(f"sessions: buckets cover {m.group(1)} of {m.group(2)} decisions "
                     f"- an hour is in no session")
    elif "BUG: the session buckets" in out:
        fails.append("sessions: the report itself flagged a bucket hole")
    if not fails:
        print(f"[case] session buckets: all {m.group(2)} decisions fall inside a session")


def case_judges_page(dst: Path, fails: list) -> None:
    """Every judge that voted must appear on the judges page, with its reason text.

    The operator could not see most of his judges: the console table only lists judges
    with >=3 scored calls. The page has no such filter - if a judge voted, it is there.
    """
    _run_audit(dst, "--date", DAY, html=True)
    page = dst / "data" / f"judges_{DAY}.html"
    if not page.exists():
        fails.append("judges page: no judges_<date>.html was written")
        return
    h = page.read_text(encoding="utf-8", errors="ignore")
    voted = set()
    diary = dst / "data" / "snapshots_history.jsonl"
    if diary.exists():
        for line in diary.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                rec = json.loads(line)
            except Exception:
                continue
            for v in (rec.get("judge_votes") or []):
                if isinstance(v, dict) and v.get("judge"):
                    voted.add(str(v["judge"]))
    missing = sorted(j for j in voted if j not in h)
    if missing:
        fails.append(f"judges page: these judges voted but are not on the page: {missing}")
    for need, what in (("the reason it wrote", "the raw reason column"),
                       ("Graded on its own clock", "the per-judge clock note")):
        if need not in h:
            fails.append(f"judges page: missing {what}")
    if not missing:
        print(f"[case] judges page: all {len(voted)} judge(s) that voted are on the page, "
              f"with their reasons")


def case_mt5_mismatch(fails: list) -> None:
    """MT5 says 2 closed, our records say 0 -> the report must shout, not shrug."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("ad_mm", ROOT / "audit_day.py")
    A = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(A)

    def fake_truth(day):
        return {"closed_ids": [91403564, 91410041], "open_now": 0, "open_detail": [],
                "deals_total": 4, "closed": 2, "opened": 2, "pnl": -3.21,
                "last_deal": "09-24 11:10"}, None

    A._mt5_truth = fake_truth
    A._outcomes_for_day = lambda day: ([], Path("trade_outcomes.csv"))
    A.SKIP_MT5 = False
    day = datetime.now(A.BUDA).date()
    rep, _ = A.test_13_money(day, decisions=[])
    blob = " ".join(" ".join(w) for _g, _n, _h, w in rep.rows)
    grades = [g for g, _n, _h, _w in rep.rows]
    if "MISSING" not in blob:
        fails.append("mt5 mismatch: 2 closed deals in MT5 vs 0 in our records was not reported")
    if "91403564" not in blob:
        fails.append("mt5 mismatch: the missing position ids were not named")
    if "WARN" not in grades:
        fails.append(f"mt5 mismatch: money check graded {grades}, expected WARN")
    if not fails:
        print("[case] mt5 cross-check: closed deals missing from our records -> WARN + ids named")


def case_clock_lookup(fails: list) -> None:
    """The tape must be read at the decision's own minute, not hours away.

    Regression for the 24j defect: insight.py used one +2h variable for BOTH display and
    price lookups, so every "+15 min" column was really "+2h15m" and panel_rows() scored
    judges against the wrong prices (feeding --weight-ab and --walk-forward).
    """
    import importlib.util
    from datetime import datetime, timedelta, timezone as _tz
    spec = importlib.util.spec_from_file_location("ins_clock", ROOT / "tools" / "insight.py")
    I = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(I)

    base = datetime(2026, 7, 1, 10, 0, tzinfo=_tz.utc)          # summer: UTC+2
    series = [(base + timedelta(minutes=i), 2000.0 + i) for i in range(0, 120)]
    idx = I.series_index(series)
    got = I.price_at(series, base, 15, idx)
    if got != 2015.0:
        fails.append(f"clock: +15m from a UTC stamp read {got}, expected 2015.0 "
                     f"(a 2h shift would give {2000.0 + 135})")
    # display must still be local, and DST-correct
    if I._loc(base).strftime("%H:%M") != "12:00":
        fails.append(f"clock: July 10:00 UTC displayed as {I._loc(base):%H:%M}, expected 12:00 (CEST)")
    winter = datetime(2026, 12, 1, 10, 0, tzinfo=_tz.utc)
    if I._loc(winter).strftime("%H:%M") != "11:00":
        fails.append(f"clock: December 10:00 UTC displayed as {I._loc(winter):%H:%M}, expected 11:00 (CET)")
    # panel_rows must measure the move from the decision's own minute
    diary = [{"_dt": base, "price": 2000.0, "signal_direction": "BUY",
              "judge_votes": [{"judge": "t", "dir": 1, "weight": 1.0, "raw": "x"}]}]
    rows = I.panel_rows("2026-07-01", diary, series)
    if not rows:
        fails.append("clock: panel_rows returned nothing for a snapshot inside the tape")
    elif abs(rows[0][4] - 15.0) > 0.001:
        fails.append(f"clock: panel_rows measured a {rows[0][4]} move, expected 15.0 "
                     f"(2h-shifted would be 135.0)")
    if not fails:
        print("[case] clock: tape read at the decision's own minute; display local and DST-correct")


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="bm_selftest_"))
    try:
        build_fake_day(tmp)
        proc = subprocess.run(
            [sys.executable, str(tmp / "audit_day.py"), "--date", DAY],
            capture_output=True, text=True, encoding="utf-8", errors="ignore", timeout=600)
        out = proc.stdout + "\n" + proc.stderr
        print(out[-4000:])
        fails = []
        if proc.returncode != 0:
            fails.append(f"audit_day.py exited {proc.returncode}")
        for num, name in EXPECTED_TESTS:
            m = re.search(rf"TEST\s+{num}\s+{name}", out)
            if not m:
                fails.append(f"test {num} ({name}) never printed")
        for tag, pat in (("feed counted real prints", r"\d+,\.?\d* ?trade prints|\d+ trade prints"),
                         ("what-if simulated", r"if ALL (\d+) had been taken: total ([+-][\d.]+) pts"),
                         ("teams ranked", r"teams \(votes that pointed up vs down\): "),
                         ("hourly table", r"London AM \(08-12\)\s+decisions"),
                         ("judge table rows", r"JUDGE\s+VOTES\s+PART%"),
                         ("footprint line", r"footprint \(your BookMap aggressive buyer vs seller\)"),
                         ("judge csv saved", r"judge_panel_\d{4}-\d{2}-\d{2}\.csv"),
                         ("judge weights json", r"judge_weight_suggestions\.json"),
                         ("coverage test", r"DIARY COVERAGE"),
                         ("scoreboard", r" SCOREBOARD"),
                         ("15-test header", r"DAY AUDIT - .* - 15 tests"),
                         ("manual checklist", r"BY HAND, IN THIS ORDER")):
            if not re.search(pat, out):
                fails.append(f"missing: {tag}")
        mm = re.search(r"if ALL (\d+) had been taken: total ([+-][\d.]+) pts", out)
        if mm:
            print(f"\n[check] what-if replayed {mm.group(1)} decisions, net {mm.group(2)} pts "
                  f"(candles were built from the fake tape -> the replay engine runs)")
        if "NO-ORDERS" in out:
            fails.append("unexpected NO-ORDERS")
        # ---- 24f: the writing side of the day + the six regressions
        contract = Path(tempfile.mkdtemp(prefix="bm_contract_"))
        try:
            case_writer_reader(fails)
            case_no_phantom_files(fails)
            reg = Path(tempfile.mkdtemp(prefix="bm_regress_"))
            try:
                build_fake_day(reg)
                case_odd_shapes(reg, fails)
                case_then_now(reg, fails)
                case_midnight(reg, fails)
                case_filtered_files(reg, fails)
                case_session_story(reg, fails)
                case_pages(reg, fails)
                case_explain(reg, fails)
                case_weight_ab(reg, fails)
                case_walk_forward(reg, fails)
                case_price_cache(reg, fails)
                case_session_buckets(reg, fails)
                case_judges_page(reg, fails)
                case_mt5_mismatch(fails)
                case_clock_lookup(fails)
            finally:
                shutil.rmtree(reg, ignore_errors=True)
        finally:
            shutil.rmtree(contract, ignore_errors=True)

        if fails:
            print("\nSELFTEST FAILED:")
            for f in fails:
                print("  -", f)
            return 1
        print("\nSELFTEST PASSED: all 15 tests produced real grades on a full day of data.")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
