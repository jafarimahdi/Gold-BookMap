#!/usr/bin/env python3
"""signal_health.py - why is the SIGNAL team silent?

On 2026-09-24 the diary carried 6761 judge votes, and whale_walls, iceberg,
spoof_invert, queue_pos and absorption cast ZERO between them. Before building
anything on walls we must know which of these is true:

  A) the detector NEVER RAN          - no L3 data reaching it
  B) it ran and FOUND NOTHING        - thresholds too high for this market
  C) it ran, found something, but the vote was not recorded

This tool does not guess. It reads every note line the robot wrote and counts
what it actually said, including the "no vote" branches that prove a detector
ran and decided against speaking.

USAGE
    python tools/signal_health.py --date 2026-09-24
    python tools/signal_health.py --date 2026-09-24 --all-notes

Report only.
"""
from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_audit():
    spec = importlib.util.spec_from_file_location("ad_health", ROOT / "audit_day.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


A = _load_audit()

# phrase -> (judge, does this phrase mean a VOTE or a deliberate SILENCE)
PROBES = [
    ("L3 whale SUPPORT",        "whale_walls",   "vote"),
    ("L3 whale RESISTANCE",     "whale_walls",   "vote"),
    ("L3 whales balanced",      "whale_walls",   "ran, chose silence"),
    ("ICEBERG_SUPPORT",         "iceberg",       "vote"),
    ("ICEBERG_RESISTANCE",      "iceberg",       "vote"),
    ("ICEBERG weak",            "iceberg",       "ran, chose silence"),
    ("L3 icebergs",             "iceberg_legacy", "vote (retired)"),
    ("SPOOF_INVERT fake",       "spoof_invert",  "vote"),
    ("SPOOF_INVERT more fake",  "spoof_invert_loose", "vote (retired)"),
    ("spoof vote error",        "spoof_invert",  "ERROR"),
    ("QUEUE_POS good",          "queue_pos",     "vote"),
    ("QUEUE_POS",               "queue_pos",     "any mention"),
    ("absorption",              "absorption",    "vote"),
    ("L3 NET FLOW",             "l3_net_flow",   "vote"),
    ("L3 large orders",         "l3_large_ofi",  "vote"),
    ("L3 distance-weighted imbalance", "l3_imbalance", "vote"),
    ("L3 imbalance",            "l3_imbalance",  "vote"),
    ("microprice",              "microprice",    "vote"),
    ("microprice vote removed", "microprice",    "DISABLED IN CODE"),
    ("iceberg persistence vote error", "iceberg", "ERROR"),
    ("M1 Footprint: absorption via icebergs", "absorption", "fallback"),
]

SIGNAL_JUDGES = ["l3_net_flow", "whale_walls", "iceberg", "l3_imbalance",
                 "l3_large_ofi", "microprice", "spoof_invert", "queue_pos", "absorption"]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Why is the SIGNAL team silent?")
    ap.add_argument("--date", help="YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--teams", action="store_true",
                    help="parse the '4 Teams ensemble' lines and show how often each team "
                         "scored exactly 0.00 - an empty team, not a neutral one")
    ap.add_argument("--all-notes", action="store_true",
                    help="also list the 40 most common note lines, whatever they are")
    args = ap.parse_args(argv)

    day = (datetime.strptime(args.date, "%Y-%m-%d").date() if args.date
           else (datetime.now(A.BUDA) - timedelta(days=1)).date())
    diary, path = A.load_diary(day)
    if not diary:
        print(f"no diary for {day:%Y-%m-%d} - nothing to inspect")
        return 2

    notes, snaps_with_notes, votes_seen = [], 0, Counter()
    for rec in diary:
        ns = rec.get("notes") or []
        if ns:
            snaps_with_notes += 1
        for n in ns:
            if isinstance(n, str):
                notes.append(n)
        for v in (rec.get("judge_votes") or []):
            votes_seen[str(v.get("judge", "?"))] += 1

    print("=" * 96)
    print(f"SIGNAL HEALTH  {day:%Y-%m-%d}   {len(diary)} snapshots, "
          f"{snaps_with_notes} with notes, {len(notes)} note lines")
    print("=" * 96)

    hits = Counter()
    for n in notes:
        for phrase, judge, kind in PROBES:
            if phrase in n:
                hits[(phrase, judge, kind)] += 1

    print(f"\n{'phrase the robot wrote':<42}{'judge':<20}{'meaning':<22}{'count':>7}")
    print("-" * 96)
    for phrase, judge, kind in PROBES:
        c = hits.get((phrase, judge, kind), 0)
        mark = "" if c else "   <-- NEVER"
        print(f"{phrase:<42}{judge:<20}{kind:<22}{c:>7}{mark}")

    print("\n" + "=" * 96)
    print("DIAGNOSIS PER SILENT JUDGE")
    print("=" * 96)
    for j in SIGNAL_JUDGES:
        v = votes_seen.get(j, 0)
        ran = sum(c for (p, jj, k), c in hits.items() if jj == j)
        silent_branch = sum(c for (p, jj, k), c in hits.items()
                            if jj == j and "silence" in k)
        if v > 0:
            verdict = f"OK - voted {v} times"
        elif silent_branch > 0:
            verdict = (f"RAN {silent_branch} times and chose not to vote "
                       f"-> THRESHOLDS too high, not missing data")
        elif ran > 0:
            verdict = f"mentioned {ran} times but never voted -> check the vote branch"
        else:
            verdict = "NEVER APPEARED AT ALL -> the detector never produced a line"
        print(f"  {j:<16} {verdict}")

    print("\n" + "=" * 96)
    print("WHAT TO CHANGE IF THE ANSWER IS 'THRESHOLDS TOO HIGH'")
    print("=" * 96)
    print("  L3_WHALE_THRESHOLD=100      lots for a wall to count as a whale")
    print("  ABSORPTION_WALL_SIZE=50     lots for a wall to be watched at all")
    print("  SPOOF_SIZE_THRESHOLD=100    lots before a cancel counts as a fake")
    print("  ICEBERG_MIN_REFILLS=3       refills before hidden size is believed")
    print("  QUEUE_POS_THRESHOLD=0.7     how near the front we must be")
    print("  BOOKMAP_MAX_DEPTH_LEVELS=20 levels collected (the analyzer reads only 5)")
    print("\n  These are YOUR .env lines. Nothing here changes them.")

    if args.teams:
        print("\n" + "=" * 96)
        print("THE 4 TEAMS, AS ACTUALLY SCORED")
        print("  teams are sliced by POSITION in the votes list (votes[:5], [5:15], [15:35],")
        print("  [35:45], [45:]). With fewer than 45 votes per cycle the later slices are EMPTY.")
        print("=" * 96)
        rx = re.compile(r"Teams ensemble: flow ([+-]?[\d.]+)\*[\d.]+ whale ([+-]?[\d.]+)"
                        r"\*[\d.]+ struct ([+-]?[\d.]+)\*[\d.]+ trend ([+-]?[\d.]+)")
        names = ["flow", "whale", "struct", "trend"]
        vals = {n: [] for n in names}
        for n in notes:
            m = rx.search(n)
            if m:
                for i, nm in enumerate(names):
                    vals[nm].append(float(m.group(i + 1)))
        tot = len(vals["flow"])
        if not tot:
            print("  no ensemble lines found on this day")
        else:
            print(f"  {tot} ensemble lines\n")
            print(f"  {'team':<10}{'exactly 0.00':>14}{'% of cycles':>13}{'min':>8}{'max':>8}")
            for nm in names:
                z = sum(1 for v in vals[nm] if abs(v) < 1e-9)
                print(f"  {nm:<10}{z:>14}{100.0*z/tot:>12.1f}%"
                      f"{min(vals[nm]):>8.2f}{max(vals[nm]):>8.2f}")
            dead = [nm for nm in names if sum(1 for v in vals[nm] if abs(v) < 1e-9) > 0.9 * tot]
            if dead:
                print(f"\n  [!] {', '.join(dead)} scored 0.00 in more than 90% of cycles.")
                print("      A team that is always 0 is not neutral - it is EMPTY, and the")
                print("      confluence rule still demands its agreement before any trade.")
        fails = sum(1 for n in notes if "Confluence FAIL" in n)
        oks = sum(1 for n in notes if "Confluence BUY" in n or "Confluence SELL" in n)
        if fails or oks:
            print(f"\n  confluence: {oks} passed, {fails} FAILED -> forced NEUTRAL "
                  f"({100.0*fails/max(fails+oks,1):.0f}% of decided cycles)")

    if args.all_notes:
        print("\n" + "=" * 96)
        print("THE 40 MOST COMMON NOTE LINES (shape only, numbers stripped)")
        print("=" * 96)
        shapes = Counter(re.sub(r"[-+]?\d+\.?\d*", "#", n)[:88] for n in notes)
        for s, c in shapes.most_common(40):
            print(f"{c:>7}  {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
