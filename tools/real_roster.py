#!/usr/bin/env python3
"""real_roster.py - the weights your robot ACTUALLY used, measured from the diary.

WHY THIS EXISTS
  There are at least three weight tables in this project and they disagree:
    audit_day.py   JUDGE_DEFAULT_W      <- what the AUDITOR assumes
    config.py      SIGNAL_W_*           <- what the ROBOT is configured with
    step2          hardcoded at the vote site for judges with no config key
  PANEL_ROSTER.md was generated from the first one and claimed to describe the
  robot. It does not. On 2026-09-25, 10 of 11 checked judges disagreed.

  Guessing a fourth table would not help. So this tool reads the only source that
  cannot lie: every judge_votes entry in the diary carries the weight that was
  ACTUALLY APPLIED at that moment, after every runtime multiplier.

WHAT IT SHOWS
  per judge: how often it voted, and the median / min / max weight it really had.
  The min..max spread is the runtime multiplier problem, measured rather than claimed
  (regime x killzone x volatility can multiply a judge by up to ~4).

USAGE
    python tools/real_roster.py                 # yesterday
    python tools/real_roster.py --days 5
    python tools/real_roster.py --date 2026-09-24

Report only. Touches nothing, changes nothing.
"""
from __future__ import annotations

import argparse
import importlib.util
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_audit():
    spec = importlib.util.spec_from_file_location("ad_roster", ROOT / "audit_day.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


A = _load_audit()

POWER = {
    "footprint_delta", "sweep", "l3_aggr_limit", "cvd_divergence", "cvd_momentum",
    "footprint_levels", "volume_roc", "vwap_bands", "htf_poc", "vwap_trend",
    "supply_demand", "poc_day", "value_area", "mtf", "news_sentiment", "macro_risk",
}
SIGNAL = {
    "l3_net_flow", "whale_walls", "iceberg", "l3_imbalance", "l3_large_ofi",
    "microprice", "spoof_invert", "queue_pos", "absorption",
}
RETIRED = {
    "macro_yield", "macro_dxy", "macro_vix", "vwap_zscore", "spoof_invert_loose",
    "iceberg_legacy", "l3_ofi_streak", "delta_pressure",
}


def team_of(name):
    if name in RETIRED:
        return "retired"
    if name in POWER:
        return "POWER"
    if name in SIGNAL:
        return "SIGNAL"
    return "?unknown"


def main(argv=None):
    ap = argparse.ArgumentParser(description="The weights the robot really used")
    ap.add_argument("--date", help="YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--days", type=int, default=1)
    args = ap.parse_args(argv)

    if args.date:
        end = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        end = (datetime.now(A.BUDA) - timedelta(days=1)).date()
    days = sorted(end - timedelta(days=i) for i in range(max(1, args.days)))

    seen = defaultdict(list)
    dirs = defaultdict(lambda: [0, 0, 0])      # buy, sell, flat
    used_days, skipped = [], []
    for d in days:
        diary, _p = A.load_diary(d)
        if not diary:
            skipped.append((d, "no diary"))
            continue
        n = 0
        for rec in diary:
            for v in (rec.get("judge_votes") or []):
                try:
                    name = str(v.get("judge", "?"))
                    w = float(v.get("weight", 0) or 0)
                    dv = float(v.get("dir", 0) or 0)
                except Exception:
                    continue
                seen[name].append(w)
                dirs[name][0 if dv > 0 else 1 if dv < 0 else 2] += 1
                n += 1
        if n:
            used_days.append(d)
            print(f"[read] {d:%Y-%m-%d}: {n} judge votes")
        else:
            skipped.append((d, "diary has no judge_votes (older build)"))

    if not seen:
        print("\nNo judge votes found. Nothing to report - and that is the honest answer, "
              "not an error.")
        for d, w in skipped:
            print(f"  {d:%Y-%m-%d}  {w}")
        return 2

    audit_w = getattr(A, "JUDGE_DEFAULT_W", {}) or {}

    print("\n" + "=" * 104)
    print(f"THE REAL ROSTER   {len(used_days)} day(s), {sum(len(v) for v in seen.values())} votes")
    print("  'real' = the weight actually attached to the vote, after every runtime multiplier")
    print("  'audit' = what audit_day.py / PANEL_ROSTER.md assumed")
    print("=" * 104)
    hdr = (f"{'judge':<20}{'team':<9}{'votes':>7}{'real med':>10}{'min':>7}{'max':>7}"
           f"{'swing':>7}{'audit':>7}  note")
    print(hdr)
    print("-" * 104)

    totals = defaultdict(float)
    mismatches = swingers = 0
    for name in sorted(seen, key=lambda k: (team_of(k), -statistics.median(seen[k]))):
        ws = seen[name]
        med, lo, hi = statistics.median(ws), min(ws), max(ws)
        swing = (hi / lo) if lo > 0 else float("inf")
        aw = audit_w.get(name)
        note = []
        if aw is not None and abs(aw - med) > 0.05:
            note.append(f"MISMATCH ({med - aw:+.2f})")
            mismatches += 1
        if swing >= 1.5:
            note.append(f"weight swings {swing:.1f}x at runtime")
            swingers += 1
        t = team_of(name)
        if t in ("POWER", "SIGNAL"):
            totals[t] += med
        print(f"{name:<20}{t:<9}{len(ws):>7}{med:>10.2f}{lo:>7.2f}{hi:>7.2f}"
              f"{swing:>6.1f}x{(f'{aw:.2f}' if aw is not None else '-'):>7}  "
              + "; ".join(note))

    print("-" * 104)
    print(f"{'TEAM TOTALS (median weights)':<36} POWER {totals['POWER']:.2f}   "
          f"SIGNAL {totals['SIGNAL']:.2f}   total {totals['POWER'] + totals['SIGNAL']:.2f}")
    print(f"\n  {mismatches} judge(s) disagree with the audit table -> PANEL_ROSTER.md is stale")
    print(f"  {swingers} judge(s) had their weight changed at runtime by 1.5x or more")
    if swingers:
        print("     (REGIME_ADAPTIVE, V6_KILLZONES_ENABLED, L3_RANGE_BOOST, VOLATILITY_HIGH_MULT)")
    unknown = [n for n in seen if team_of(n) == "?unknown"]
    if unknown:
        print(f"\n  [!] {len(unknown)} judge(s) vote but belong to NO team: {', '.join(sorted(unknown))}")
        print("      Either they need a team, or they should not be voting.")
    ret = [n for n in seen if n in RETIRED]
    if ret:
        print(f"\n  note: {len(ret)} retired judge(s) still appear in the diary "
              f"({', '.join(sorted(ret))}).")
        print("      That is expected and deliberate - they write notes so they can still be")
        print("      graded. Check the robot was restarted if their weight is not 0.")
    if skipped:
        print("\n  days skipped, named on purpose:")
        for d, w in skipped:
            print(f"    {d:%Y-%m-%d}  {w}")
    print("=" * 104)
    return 0


if __name__ == "__main__":
    sys.exit(main())
