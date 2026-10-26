#!/usr/bin/env python3
"""Rig for council_shadow.py - synthetic diary + decisions log, all answers
hand-computed. Scenario: 30 bars, refs rise +1.0/bar (monotone up):
every UP outcome is RIGHT (+6 @30m, +12 @60m), every DOWN is WRONG (-6/-12).
POWER direction by bar index: i%3==0 UP (dom 80), i%3==1 DOWN (dom 70),
i%3==2 NEITHER.

Crafted minutes (bar, conf, stren, classdir):
  b0 UP   conf35 s8 BUY   agree          b6 UP   conf52 s9 BUY  agree
  b1 DOWN conf48 s5 BUY   DISAGREE       b7 DOWN conf30 s5 SELL agree
  b2 -    conf55 s9 SELL  silent->DOWN   b8 -    conf22 s3 BUY  silent->UP
  b3 UP   conf25 s4 BUY   agree          b9 UP   conf20 s2 BUY  agree
  b4 DOWN conf47 s7 SELL  agree(40-49!)  b10 DOWN conf18 s1 HOLD spoke->DOWN
  b5 -    conf33 s6 HOLD  silent         b11 -   conf18 s1 HOLD silent

Expected (hand-calc):
  matrix: 40-49 spoke 0/2 (toxic band confirmed wrong); >=50 spoke 1/1;
          20-29 spoke 2/2; 30-39 spoke 1/2; <20 spoke 0/1; 20-29 silent 1/1;
          >=50 silent 0/1
  sweep floor45: passed 2, blocked 1   floor30: passed 4   floor20: passed 6
  TODAY passed 2 right 1/2 (50%)
  OPT B floor30 passed 4 right 2/4 (50%)   floor20 passed 6 right 4/6 (67%)
  OPT A council passed 4 right 2/4 (50%)
  OPT D power-only passed 8 right 4/8 (50%)
  strength: >=6: 2/3   <6: 2/5
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE / "rig_council_tmp"
MEM = BASE / "power_v2_memory"
MEM.mkdir(parents=True, exist_ok=True)
DAY = "2026-10-16"
START = datetime(2026, 10, 16, 8, 0, 0, tzinfo=timezone.utc)
N = 30

def bar_end(i):
    return START + timedelta(minutes=5 * (i + 1))

rows = []
for i in range(N):
    if i % 3 == 0:
        d, up, dn, reason = "UP", 80.0, 20.0, "UPWARD_FORCE_DOMINATES"
    elif i % 3 == 1:
        d, up, dn, reason = "DOWN", 30.0, 70.0, "DOWNWARD_FORCE_DOMINATES"
    else:
        d, up, dn, reason = "NEITHER", 50.0, 50.0, "WEAK_OR_NO_FORCE"
    votes = {"footprint_delta": {"value": 0.5, "quality": 1.0},
             "footprint_levels": {"value": 0.3, "quality": 1.0}}
    rows.append({
        "schema_version": 1,
        "record_id": f"RIG|M5|{bar_end(i).isoformat()}|FINAL",
        "timestamp_utc": bar_end(i).isoformat().replace("+00:00", "Z"),
        "symbol": "RIG", "timeframe": "M5", "phase": "FINAL",
        "reference_price": 4100.0 + 1.0 * i,
        "judges": votes, "context": {},
        "result": {"direction": d, "reason_code": reason,
                   "up_power_pct": up, "down_power_pct": dn,
                   "input_judges": votes},
    })

def twin(rec):
    t = dict(rec)
    t["record_id"] = "XAUUSD|" + rec["record_id"]
    t["symbol"] = "XAUUSD"
    t["result"] = {"direction": "NEITHER", "reason_code": "BAD_DATA_QUALITY",
                   "up_power_pct": 50.0, "down_power_pct": 50.0,
                   "input_judges": {}}
    t["judges"] = {}
    return t

with (MEM / f"power_{DAY}.jsonl").open("w", encoding="utf-8") as out:
    for rec in rows:
        out.write(json.dumps(rec) + "\n")
        out.write(json.dumps(twin(rec)) + "\n")

crafted = [(0, 35, 8, "BUY", "power-lead-v1"), (1, 48, 5, "BUY", "none-quiet"),
           (2, 55, 9, "SELL", "none-quiet"), (3, 25, 4, "BUY", "none-quiet"),
           (4, 47, 7, "SELL", "fallback-rules-v1"), (5, 33, 6, "HOLD", "none-quiet"),
           (6, 52, 9, "BUY", "gemini-2.5-flash"), (7, 30, 5, "SELL", "none-quiet"),
           (8, 22, 3, "BUY", "none-quiet"), (9, 20, 2, "BUY", "none-quiet"),
           (10, 18, 1, "HOLD", "none-quiet"), (11, 18, 1, "HOLD", "none-quiet")]

def write_log(rows_):
    with (BASE / "decisions_log.csv").open("w", encoding="utf-8", newline="") as out:
        out.write("timestamp,action,signal_confidence,signal_direction,"
                  "signal_strength,ai_model\n")
        for ts, conf, cdir, stren, model in rows_:
            out.write(f"{ts.isoformat().replace('+00:00', 'Z')},HOLD,"
                      f"{conf},{cdir},{stren},{model}\n")

write_log([(bar_end(i) - timedelta(minutes=1), conf, cdir, stren, model)
           for i, conf, stren, cdir, model in crafted])

print("rig written; expected values in the module docstring")
print("=" * 70)
result = subprocess.run(
    [sys.executable, str(HERE / "council_shadow.py"), DAY, "--root", str(MEM)],
    capture_output=True, text=True)
print(result.stdout)
print(result.stderr)

checks = [
    "bar interval: 300s (M5) - horizons are true minutes",
    "POWER carries: 1 (decision_source=POWER_V2_CONFIGURED_THRESHOLD_CARRY or legacy ai_model=power-lead-v1) | AI: 1 | fallback: 1 | none/quiet: 9 | other: 0",
    "graded symbol: RIG",
    "aligned minutes: 12",
    "twins filtered (not graded): XAUUSD x30",
    "conf 40-49 | spoke: N=2 right@30m 0/2 (0%)",
    "conf >=50  | spoke: N=1 right@30m 1/1 (100%)",
    "conf 20-29 | spoke: N=2 right@30m 2/2 (100%)",
    "conf 30-39 | spoke: N=2 right@30m 1/2 (50%)",
    "conf <20   | spoke: N=1 right@30m 0/1 (0%)",
    "conf 20-29 | silent: N=1 right@30m 1/1 (100%)",
    "TODAY  (conf>=45 + POWER + agree): passed 2  right@30m 1/2 (50%)",
    "OPT B  (POWER-led, class floor 30, agree): passed 4  right@30m 2/4 (50%)",
    "OPT B  (POWER-led, class floor 20, agree): passed 6  right@30m 4/6 (67%)",
    "OPT A  (Council v0: conf>=30, dom>=55, 0.5c+0.5d>=45): passed 4  right@30m 2/4 (50%)",
    "OPT D  (POWER-only, no class floor): passed 8  right@30m 4/8 (50%)",
    "stren >=6  N=3",
    "stren <6   N=5",
]
ok = all(c in result.stdout for c in checks)
print("RIG day1 (M5):", "PASS" if ok else "FAIL")
for c in checks:
    if c not in result.stdout:
        print("  missing:", c)

# ---------------------------------------------------------------- M3 day
# 2026-10-19: 24 bars, 180s apart (M3 diary like Monday's robot).
# refs rise +1.0/bar: every UP mark RIGHT, every DOWN mark WRONG.
# POWER speaks UP (dom 80) on bars 0,3,6,9,12,15,18 and DOWN (dom 70)
# on bars 1,4,7,10,13; craft 6 minutes. With true-minute horizons
# (HORIZON_30 = 10 bars) the old hardcoded M5 window (5 min) would
# MISALIGN every minute - this scenario locks the interval-aware fix.
DAY2 = "2026-10-19"
START2 = datetime(2026, 10, 19, 8, 0, 0, tzinfo=timezone.utc)
N2 = 24
UP_BARS = {0, 3, 6, 9, 12, 15, 18}
DOWN_BARS = {1, 4, 7, 10, 13}


def bar_end2(i):
    return START2 + timedelta(seconds=180 * (i + 1))


rows2 = []
for i in range(N2):
    if i in UP_BARS:
        d, up, dn, reason = "UP", 80.0, 20.0, "UPWARD_FORCE_DOMINATES"
    elif i in DOWN_BARS:
        d, up, dn, reason = "DOWN", 30.0, 70.0, "DOWNWARD_FORCE_DOMINATES"
    else:
        d, up, dn, reason = "NEITHER", 50.0, 50.0, "WEAK_OR_NO_FORCE"
    votes = {"footprint_delta": {"value": 0.5, "quality": 1.0}}
    rows2.append({
        "schema_version": 1,
        "record_id": f"RIG|M3|{bar_end2(i).isoformat()}|FINAL",
        "timestamp_utc": bar_end2(i).isoformat().replace("+00:00", "Z"),
        "symbol": "RIG", "timeframe": "M3", "phase": "FINAL",
        "reference_price": 4100.0 + 1.0 * i,
        "judges": votes, "context": {},
        "result": {"direction": d, "reason_code": reason,
                   "up_power_pct": up, "down_power_pct": dn,
                   "input_judges": votes},
    })

with (MEM / f"power_{DAY2}.jsonl").open("w", encoding="utf-8") as out:
    for rec in rows2:
        out.write(json.dumps(rec) + "\n")
        out.write(json.dumps(twin(rec)) + "\n")

crafted2 = [(0, 35, "BUY", "power-lead-v1"),   # agree UP   -> 30-39 spoke, R
            (1, 35, "BUY", "none-quiet"),      # DISAGREE   -> 30-39 spoke, W
            (4, 32, "SELL", "none-quiet"),     # agree DOWN -> 30-39 spoke, W
            (6, 50, "BUY", "gemini-2.5-flash"),  # agree UP  -> >=50 spoke, R
            (7, 25, "SELL", "none-quiet"),     # agree DOWN -> 20-29 spoke, W
            (9, 22, "BUY", "none-quiet")]      # agree UP   -> 20-29 spoke, R
write_log([(bar_end2(i) - timedelta(seconds=60), conf, cdir, 5, model)
           for i, conf, cdir, model in crafted2])

result2 = subprocess.run(
    [sys.executable, str(HERE / "council_shadow.py"), DAY2, "--root", str(MEM)],
    capture_output=True, text=True)
print(result2.stdout)
print(result2.stderr)

checks2 = [
    "bar interval: 180s (M3) - horizons are true minutes",
    "graded symbol: RIG",
    "aligned minutes: 6",
    "twins filtered (not graded): XAUUSD x24",
    "conf >=50  | spoke: N=1 right@30m 1/1 (100%)",
    "conf 30-39 | spoke: N=3 right@30m 1/3 (33%)",
    "conf 20-29 | spoke: N=2 right@30m 1/2 (50%)",
    "TODAY  (conf>=45 + POWER + agree): passed 1  right@30m 1/1 (100%)",
    "OPT B  (POWER-led, class floor 30, agree): passed 3  right@30m 2/3 (67%)",
    "OPT B  (POWER-led, class floor 20, agree): passed 5  right@30m 3/5 (60%)",
    "OPT A  (Council v0: conf>=30, dom>=55, 0.5c+0.5d>=45): passed 3  right@30m 2/3 (67%)",
    "OPT D  (POWER-only, no class floor): passed 6  right@30m 3/6 (50%)",
    "POWER carries: 1 (decision_source=POWER_V2_CONFIGURED_THRESHOLD_CARRY or legacy ai_model=power-lead-v1) | AI: 1 | fallback: 0 | none/quiet: 4 | other: 0",
]
ok2 = all(c in result2.stdout for c in checks2)
print("RIG day2 (M3):", "PASS" if ok2 else "FAIL")
for c in checks2:
    if c not in result2.stdout:
        print("  missing:", c)
sys.exit(0 if (ok and ok2) else 1)
