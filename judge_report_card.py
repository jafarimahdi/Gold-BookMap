#!/usr/bin/env python3
"""judge_report_card.py -- TEAM MEMORY: the judges' report card builder (v1.0).

The nightly audit (audit_day.py) already grades every judge and saves a
machine-readable table per day:  data/judge_panel_YYYY-MM-DD.csv
This tool aggregates the last K graded days into ONE small file:

    data/judge_report_card.json

which the doorman patch (apply_memory_patch.py) shows to the AI inside every
prompt -- so Captain 5 weighs the panel's votes by each judge's REAL track
record instead of treating every vote the same. Same numbers as the evening
report: this builder reads the audit's own tables, it never re-grades.

Laws baked in:
  * NO LOOK-AHEAD: days >= --as-of (default: today) are never included, so the
    card for a trading day only ever contains COMPLETED days before it.
  * NOISE FLOOR: a judge needs >= 20 scored calls in the window (the audit's
    own rule: "Anything scored on <20 votes is noise").
  * FAIL-CLOSED: if there is nothing usable, NOTHING is written -- the
    previous card (if any) stays untouched.

Run any time (best: in the morning, after the nightly audit, before the open):
    python judge_report_card.py                     # last 5 graded days
    python judge_report_card.py --days 3
    python judge_report_card.py --as-of 2026-10-13  # twin-replay protocol:
                                                    # rebuild the card exactly
                                                    # as it existed before that
                                                    # day, re-run the twin,
                                                    # then rebuild for live.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DAYS = 5
MIN_SCORED = 20          # the audit's own noise floor
CARD_NAME = "judge_report_card.json"


def _day_str(p: Path) -> str:
    """judge_panel_YYYY-MM-DD.csv -> YYYY-MM-DD"""
    return p.name[len("judge_panel_"):p.name.rfind(".csv")]


def find_source_files(data_dir: Path, as_of: str, days_wanted: int):
    """Newest K judge_panel files strictly BEFORE as_of. -> [(day, path)]"""
    files = []
    if data_dir.is_dir():
        for p in data_dir.glob("judge_panel_*.csv"):
            d = _day_str(p)
            if len(d) == 10 and d < as_of:
                files.append((d, p))
    files.sort(reverse=True)
    return files[:days_wanted]


def load_day(path: Path):
    """One audit day -> {judge: [votes, scored, hits, net_pts]}."""
    out = {}
    try:
        with open(path, "r", newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                try:
                    judge = (row.get("judge") or "").strip()
                    scored = int(float(row.get("scored") or 0))
                    if not judge or scored <= 0:
                        continue
                    right = float(row.get("right_pct") or 0.0)
                    pts = float(row.get("points_after_spread") or 0.0)
                    votes = int(float(row.get("votes") or 0))
                except (TypeError, ValueError):
                    continue
                hits = right * scored / 100.0
                cur = out.get(judge)
                if cur is None:
                    out[judge] = [votes, scored, hits, pts]
                else:
                    cur[0] += votes
                    cur[1] += scored
                    cur[2] += hits
                    cur[3] += pts
    except OSError:
        pass
    return out


def build_card(data_dir: Path, as_of: str, days_wanted: int, min_scored: int):
    files = find_source_files(data_dir, as_of, days_wanted)
    used_days, skipped = [], []
    agg = {}
    for d, p in files:
        day_tab = load_day(p)
        if not day_tab:
            continue
        used_days.append(d)
        for j, (v, s, h, pts) in day_tab.items():
            cur = agg.setdefault(j, [0, 0, 0.0, 0.0])
            cur[0] += v
            cur[1] += s
            cur[2] += h
            cur[3] += pts
    judges = []
    for j, (v, s, h, pts) in sorted(agg.items()):
        if s < min_scored:
            skipped.append(f"{j} ({s} scored)")
            continue
        judges.append({
            "judge": j,
            "votes": v,
            "scored": s,
            "hit_pct": round(100.0 * h / s, 1),
            "net_pts": round(pts, 1),
            "pts_per_call": round(pts / s, 3),
        })
    judges.sort(key=lambda r: (-r["pts_per_call"], -r["hit_pct"], r["judge"]))
    return used_days, judges, skipped


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build the judges' report card (data/judge_report_card.json) for the AI doorman.")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"how many graded days to include (default {DEFAULT_DAYS})")
    ap.add_argument("--as-of", default=None,
                    help="the card may only use days BEFORE this date (default: today, UTC)")
    ap.add_argument("--data-dir", default=None,
                    help="default: ./data relative to the project root")
    ap.add_argument("--min-scored", type=int, default=MIN_SCORED,
                    help=f"noise floor on scored calls in the window (default {MIN_SCORED})")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent
    data_dir = Path(args.data_dir) if args.data_dir else root / "data"
    as_of = args.as_of or datetime.now(timezone.utc).date().isoformat()
    try:
        datetime.fromisoformat(as_of)
    except ValueError:
        print(f"[fail] --as-of must be YYYY-MM-DD, got: {as_of!r}")
        return 2

    used_days, judges, skipped = build_card(data_dir, as_of, args.days, args.min_scored)

    print("=" * 78)
    print(f" JUDGE REPORT CARD BUILDER   as-of {as_of} | window {args.days} day(s) | "
          f"floor {args.min_scored} scored calls")
    print("=" * 78)
    if not used_days:
        print(" [warn] no usable judge_panel_*.csv days before this as-of date.")
        print("        Run the nightly audit first -- it writes data/judge_panel_YYYY-MM-DD.csv.")
        print("        NOTHING written: the previous card (if any) stays.")
        return 0
    if not judges:
        print(" [warn] graded day(s) found, but no judge reaches the scored-call floor.")
        print("        NOTHING written: the previous card (if any) stays.")
        return 0

    print(f" graded days used : {', '.join(sorted(used_days))}")
    print(f" judges on card   : {len(judges)}")
    if skipped:
        print(f" below floor      : {', '.join(skipped)}")
    print()
    print(" judge                   scored  hit%   pts/call    net pts")
    print(" " + "-" * 62)
    for r in judges:
        print(f" {r['judge']:<22}{r['scored']:>7}  {r['hit_pct']:>4.0f}%  "
              f"{r['pts_per_call']:>+9.2f}   {r['net_pts']:>+8.1f}")

    card = {
        "version": 1,
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": as_of,
        "window_days": len(used_days),
        "source_days": sorted(used_days),
        "min_scored": args.min_scored,
        "judges": judges,
    }
    out = data_dir / CARD_NAME
    data_dir.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(card, indent=1), encoding="utf-8")
    os.replace(tmp, out)
    print()
    print(f" saved: {out}")
    print(" The AI doorman reads this file automatically (no restart needed once the")
    print(" patch is installed; it re-reads whenever the file changes).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
