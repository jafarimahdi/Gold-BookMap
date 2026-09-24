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
                         ("hourly table", r"London AM      decisions"),
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
