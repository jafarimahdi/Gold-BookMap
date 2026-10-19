#!/usr/bin/env python3
"""Rig for power_report_card.py — synthetic diary + decisions log with
hand-calculable expected results. Expected (hand-computed):
  +15m: 6/6 right   +30m: 6/6 right   +60m: 4/5 right (call at bar 13 wrong)
  dominance buckets @+30m: 60-70: 2/2   70-80: 2/2   80+: 2/2
  census: 3 UP, 3 DOWN, 34 NEITHER across 6 reason codes
  comparison: co-brave >= 1 (1 agree at bar5, 1 disagree at bar8 -> POWER wins +9)
"""
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent / "rig_power_card_tmp"
MEM = BASE / "power_v2_memory"
MEM.mkdir(parents=True, exist_ok=True)
DAY = "2026-10-16"
START = datetime(2026, 10, 16, 8, 0, 0, tzinfo=timezone.utc)

N = 40
CALLS = {5: ("UP", 80.0), 8: ("UP", 72.0), 13: ("UP", 66.0),
         22: ("DOWN", 85.0), 26: ("DOWN", 75.0), 30: ("DOWN", 62.0)}
NEITHER_REASONS = (["WEAK_OR_NO_FORCE"] * 8 + ["FORCE_TOO_BALANCED"] * 6 +
                   ["REGIME_UNKNOWN_OR_UNCONFIRMED"] * 5 +
                   ["RANGE_NO_CONFIRMED_BREAKOUT"] * 5 +
                   ["INSUFFICIENT_COVERAGE"] * 5 +
                   ["INSUFFICIENT_FAMILY_AGREEMENT"] * 5)


def ref(i):
    if i <= 18:
        return 4100.0 + 1.5 * i
    if i >= 34:
        return 4098.5          # frozen stretch (feed dead)
    return 4127.0 - 1.5 * (i - 18)


rows = []
k = 0
for i in range(N):
    end = START + timedelta(minutes=5 * (i + 1))
    if i in CALLS:
        d, dom = CALLS[i]
        up, down = (dom, 100.0 - dom) if d == "UP" else (100.0 - dom, dom)
        reason = ("UPWARD_FORCE_DOMINATES" if d == "UP"
                  else "DOWNWARD_FORCE_DOMINATES")
        votes = {"footprint_delta": {"value": 0.8 if d == "UP" else -0.8, "quality": 1.0},
                 "big_prints": {"value": 0.7 if d == "UP" else -0.7, "quality": 1.0},
                 "footprint_levels": {"value": 0.6 if d == "UP" else -0.6, "quality": 1.0},
                 "cvd_momentum": {"value": 0.2, "quality": 1.0}}
    else:
        d, up, down, reason = "NEITHER", 50.0, 50.0, NEITHER_REASONS[k % len(NEITHER_REASONS)]
        k += 1
        if i >= 34:
            reason = "STALE_FEED"
        votes = {"footprint_delta": {"value": 0.05, "quality": 1.0},
                 "footprint_levels": {"value": -0.1, "quality": 1.0}}
    record = {
        "schema_version": 1,
        "record_id": f"RIG|M5|{end.isoformat()}|FINAL",
        "timestamp_utc": end.isoformat().replace("+00:00", "Z"),
        "symbol": "RIG",
        "timeframe": "M5",
        "phase": "FINAL",
        "reference_price": ref(i),
        "judges": votes,
        "context": {},
        "result": {"direction": d, "reason_code": reason,
                   "up_power_pct": up, "down_power_pct": down,
                   "activity": (0.0 if i >= 34 else 0.4), "coverage": 0.79,
                   "input_judges": votes},
        "previous_hash": None,
        "record_hash": "0" * 16,
    }
    rows.append(record)

def phantom(record):
    import copy
    p = copy.deepcopy(record)
    p["record_id"] = "PHANTOM|" + record["record_id"]
    p["result"] = {"direction": "NEITHER", "reason_code": "BAD_DATA_QUALITY",
                   "up_power_pct": 50.0, "down_power_pct": 50.0,
                   "activity": 0.0, "coverage": 0.0, "input_judges": {}}
    return p

def twin(record):
    import copy
    p = copy.deepcopy(record)
    p["symbol"] = "XAUUSD"
    p["record_id"] = "XAUUSD|M5|" + record["record_id"].split("|", 2)[2]
    p["result"] = {"direction": "NEITHER", "reason_code": "BAD_DATA_QUALITY",
                   "up_power_pct": 50.0, "down_power_pct": 50.0,
                   "activity": 0.0, "coverage": 0.0, "input_judges": {}}
    p["judges"] = {}
    return p

with (MEM / f"power_{DAY}.jsonl").open("w", encoding="utf-8") as out:
    for record in rows:
        out.write(json.dumps(record) + "\n")
        out.write(json.dumps(phantom(record)) + "\n")
        out.write(json.dumps(twin(record)) + "\n")

# decisions log: every minute 08:01-11:19; crafted brave minutes
with (BASE / "decisions_log.csv").open("w", encoding="utf-8", newline="") as out:
    out.write("timestamp,action,signal_confidence,signal_direction\n")
    brave = {"08:27": (50.0, "BUY"),      # bar 5 window -> co-brave, agree
             "08:47": (52.0, "SELL"),     # bar 8 window -> co-brave, DISAGREE (POWER right)
             "09:12": (47.0, "BUY"),      # bar 13 window -> agree
             "09:40": (55.0, "SELL")}     # bar 20 window -> class brave, POWER silent
    for m in range(1, 200):
        ts = START + timedelta(minutes=m)
        hhmm = ts.strftime("%H:%M")
        conf, direction = brave.get(hhmm, (22.0, "HOLD"))
        out.write(f"{ts.isoformat().replace('+00:00', 'Z')},HOLD,{conf},{direction}\n")

print("rig files written; expected values (v3 hand calc):")
print("  graded symbol: RIG (40 bars); twins: XAUUSD x40 (not graded)")
print("  same-symbol duplicates removed: 40")
print("  census: 3 UP, 3 DOWN, 34 NEITHER (6 reasons + STALE_FEED x5 in freeze)")
print("  +15m 6/6  +30m 6/6  +60m 4/5   buckets 2/2 2/2 2/2 (unchanged by freeze)")
print("  frozen-window caveats: one per horizon (10:15 +60m, 10:35 +30m)")
print("  comparison: co-brave 1 (agreeing 1, disagreeing 0);")
print("  class-brave-with-POWER-silent: 3 (08:47, 09:12, 09:40 fall in NEITHER bars)")
print("=" * 70)
result = subprocess.run(
    [sys.executable, str(Path(__file__).resolve().parent / "power_report_card.py"), DAY,
     "--root", str(MEM)],
    capture_output=True, text=True)
print(result.stdout)
print(result.stderr)

checks = [
    "+15m: N=6   right=6", "+30m: N=6   right=6", "+60m: N=5   right=4",
    "agreeing 1, disagreeing 0", "BOTH brave on the same minute: 1",
    "class brave (conf >= 45): 4", "WEAK_OR_NO_FORCE",
    "same-symbol duplicate records removed: 40",
    "graded symbol: RIG",
    "twins in same diary (NOT graded): XAUUSD x40",
    "frozen-window caveats: 1 (10:15 +60m)",
    "frozen-window caveats: 1 (10:35 +30m)",
    "bar interval: 300s (M5) - horizons are true minutes",
]
ok = all(c in result.stdout for c in checks)

# ---------------------------------------------------------------- M3 day
# 2026-10-19: 40 bars, 180s apart (M3 diary like Monday's robot).
# ref rises +1.5/bar except a one-bar dip to 4090.0 at i=8.
# Refs rise monotonically, so every UP call is right and every DOWN call
# is wrong. With TRUE-minute horizons (steps 5/10/20): +15m N=6 right=3,
# +30m N=5 right=3, +60m N=3 right=3. The old hardcoded M5 steps (3/6/12)
# would land call 5 on the dip at i=8 (+15m right=2) and mislabel every
# horizon (N=6/6/5) - this scenario locks the interval-aware fix.
DAY2 = "2026-10-19"
START2 = datetime(2026, 10, 19, 8, 0, 0, tzinfo=timezone.utc)


def ref2(i):
    if i == 8:
        return 4090.0
    return 4100.0 + 1.5 * i


rows2 = []
k2 = 0
for i in range(N):
    end = START2 + timedelta(seconds=180 * (i + 1))
    if i in CALLS:
        d, dom = CALLS[i]
        up, down = (dom, 100.0 - dom) if d == "UP" else (100.0 - dom, dom)
        reason = ("UPWARD_FORCE_DOMINATES" if d == "UP"
                  else "DOWNWARD_FORCE_DOMINATES")
        votes = {"footprint_delta": {"value": 0.8 if d == "UP" else -0.8, "quality": 1.0},
                 "big_prints": {"value": 0.7 if d == "UP" else -0.7, "quality": 1.0}}
    else:
        d, up, down = "NEITHER", 50.0, 50.0
        reason = NEITHER_REASONS[k2 % len(NEITHER_REASONS)]
        k2 += 1
        votes = {"footprint_delta": {"value": 0.05, "quality": 1.0}}
    record = {
        "schema_version": 1,
        "record_id": f"RIG|M3|{end.isoformat()}|FINAL",
        "timestamp_utc": end.isoformat().replace("+00:00", "Z"),
        "symbol": "RIG", "timeframe": "M3", "phase": "FINAL",
        "reference_price": ref2(i),
        "judges": votes, "context": {},
        "result": {"direction": d, "reason_code": reason,
                   "up_power_pct": up, "down_power_pct": down,
                   "input_judges": votes},
    }
    rows2.append(record)

with (MEM / f"power_{DAY2}.jsonl").open("w", encoding="utf-8") as out:
    for record in rows2:
        out.write(json.dumps(record) + "\n")
        out.write(json.dumps(phantom(record)) + "\n")
        out.write(json.dumps(twin(record)) + "\n")

# day-2 decisions log rows (APPEND: one log serves both days):
#   08:16 conf50 SELL -> bar5 (UP spoke) co-brave DISAGREE: +30m POWER wins
#       (ref15 4122.5 - ref5 4107.5 = +15 up, class said SELL)
#   08:25 conf50 BUY  -> bar8 (UP spoke) co-brave agree
#   08:20 conf22 HOLD -> quiet minute on bar6 (NEITHER)
with (BASE / "decisions_log.csv").open("a", encoding="utf-8", newline="") as out:
    out.write("2026-10-19T08:20:00Z,HOLD,22,HOLD\n")
    out.write("2026-10-19T08:16:00Z,HOLD,50,SELL\n")
    out.write("2026-10-19T08:25:00Z,HOLD,50,BUY\n")

result2 = subprocess.run(
    [sys.executable, str(Path(__file__).resolve().parent / "power_report_card.py"), DAY2,
     "--root", str(MEM)],
    capture_output=True, text=True)
print(result2.stdout)
print(result2.stderr)

checks2 = [
    "bar interval: 180s (M3) - horizons are true minutes",
    "graded symbol: RIG",
    "+15m: N=6   right=3",
    "+30m: N=5   right=3",
    "+60m: N=3   right=3",
    "matched minutes: 3",
    "class brave (conf >= 45): 2 (67%)",
    "BOTH brave on the same minute: 2",
    "of those: agreeing 1, disagreeing 1",
    "disagreement winners (+30m): POWER 1, CLASS 0, neither 0",
]
ok2 = all(c in result2.stdout for c in checks2)

print("RIG day1 (M5):", "PASS" if ok else "FAIL")
for c in checks:
    if c not in result.stdout:
        print("  missing:", c)
print("RIG day2 (M3):", "PASS" if ok2 else "FAIL")
for c in checks2:
    if c not in result2.stdout:
        print("  missing:", c)
sys.exit(0 if (ok and ok2) else 1)
