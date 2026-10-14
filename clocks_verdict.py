#!/usr/bin/env python3
"""clocks_verdict.py -- THE CLOCKS MACHINE: C1/C2/C3/C6, rules applied honestly.

The 5-day rules are pre-declared (owner's law, defended 2026-10-09): no judge
weight change, no exit-philosophy change before its clock matures.  This tool
does NOT bend that - it makes the clock readable every evening:

  C1 whale_walls  - cut rule: >=5 graded days AND >=300 calls AND negative
                    pts/call overall -> decide; else WAIT (shows x/5, n/300)
  C2 htf_poc      - same shape
  C3 exits        - shadow vs same-subset ruler per day (the gap), plus the
                    day-5 R-multiple t-test (t = meanR / (sdR/sqrt(n)),
                    |t| >= 2 = the walls genuinely differ from the ruler)
  C6 calibration  - confidence buckets vs replayed win-rate (feeds the review,
                    no rule yet)

Sources (all existing data/ artifacts, read-only):
  day_audit_<date>.txt          judge scoreboard rows
  shadow_replay_<date>.txt/.csv shadow + benchmark rows, per-signal outcomes
  decision_ledger_<date>.csv    per-signal conf + outcome

Self-diagnosing: --peek prints what it parsed from each source.
Writes data/clocks_verdict.txt.

Usage:  python clocks_verdict.py            (auto-finds graded days)
        python clocks_verdict.py --peek
"""
import argparse
import csv
import glob
import math
import os
import re
import statistics
from datetime import datetime

JUDGE_RE = re.compile(
    r"^\s*(whale_walls|htf_poc)\s+(\d+)\s+(\d+)%\s+(\d+)/(\d+)/(\d+)\s+"
    r"(\d+(?:\.\d+)?)%\s+(\d+)\s+([+-]?\d+(?:\.\d+)?)\s+([+-]?\d+(?:\.\d+)?)\s+(\d+)%")
SHADOW_RE = re.compile(r"SHADOW \(walls\+gate\+trail\)\s+(\d+)\s+([+-]?[\d.]+)")
BENCH_RE = re.compile(r"BENCHMARK fixed ruler, same subset\s+(\d+)\s+([+-]?[\d.]+)")
STOP_RE = re.compile(r"stop[=:\s\"']+(-?[0-9.]+)")
PTS_RE = re.compile(r"([+-]?[0-9]+\.?[0-9]*)")


def graded_days():
    days = []
    for p in glob.glob("data/day_audit_*.txt"):
        m = re.search(r"day_audit_(\d{4}-\d{2}-\d{2})\.txt", p)
        if m:
            days.append(m.group(1))
    return sorted(set(days))


def parse_judges(date):
    """whale_walls / htf_poc rows from the day audit's judge table.

    Real writer = audit_day.py's test_9 scoreboard line (fixed 2026-10-13 --
    the first version guessed a format that never existed on disk):
      f"        {j:<20}{votes:>6}{part:>6.0f}%{split:>12}{acc:>7.1f}%"
      f"{q_tot:>6}{pts:>+9.1f}{avg:>+7.2f}{agree:>7.0f}%   {cur:.1f} -> {sugg:.2f}"
    calls = SCORED calls (the accuracy denominator); percall = the AVG column.
    """
    out = {}
    p = f"data/day_audit_{date}.txt"
    if not os.path.exists(p):
        return out
    for line in open(p, encoding="utf-8", errors="replace"):
        m = JUDGE_RE.match(line)
        if m:
            out[m.group(1)] = {"votes": int(m.group(2)), "acc": float(m.group(7)),
                               "calls": int(m.group(8)), "pts": float(m.group(9)),
                               "percall": float(m.group(10))}
    return out


def parse_shadow(date):
    p = f"data/shadow_replay_{date}.txt"
    out = {"shadow": None, "bench": None, "rs": []}
    if not os.path.exists(p):
        return out
    for line in open(p, encoding="utf-8", errors="replace"):
        m = SHADOW_RE.search(line)
        if m and out["shadow"] is None:
            out["shadow"] = {"n": int(m.group(1)), "pts": float(m.group(2))}
        m = BENCH_RE.search(line)
        if m and out["bench"] is None:
            out["bench"] = {"n": int(m.group(1)), "pts": float(m.group(2))}
    cp = f"data/shadow_replay_{date}.csv"
    if os.path.exists(cp):
        with open(cp, encoding="utf-8", errors="replace") as f:
            for row in csv.DictReader(f):
                try:
                    pts = float(row.get("pts") or "nan")
                except ValueError:
                    continue
                if math.isnan(pts):
                    continue
                price = float(row.get("price") or "nan")
                stop = None
                for fld in ("bracket", "detail"):
                    m = STOP_RE.search(str(row.get(fld) or ""))
                    if m:
                        stop = float(m.group(1))
                        break
                risk = abs(price - stop) if (stop is not None and not math.isnan(price)) else None
                out["rs"].append(pts / risk if (risk and risk > 0) else None)
    return out


def parse_ledger(date):
    """conf + pts per replayed signal, for C6 calibration.

    Real writer = decision_ledger.py's CSV, one row per signal (fixed
    2026-10-13: the number lives in `pts` ("+1.23", empty when not replayed);
    `outcome` is a status WORD, not a number):
      timestamp,direction,price,conf,ai_action,exec_status,stopped_by,
      reason,replay_status,outcome,pts,would_have
    """
    p = f"data/decision_ledger_{date}.csv"
    rows = []
    if not os.path.exists(p):
        return rows
    with open(p, encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            if row.get("direction") not in ("BUY", "SELL"):
                continue
            mc = re.search(r"(\d+(?:\.\d+)?)", str(row.get("conf") or ""))
            mp = re.search(r"([+-]?\d+(?:\.\d+)?)", str(row.get("pts") or ""))
            if mc and mp:
                rows.append({"conf": float(mc.group(1)), "pts": float(mp.group(1))})
    return rows


def t_test(rs):
    rs = [r for r in rs if r is not None]
    n = len(rs)
    if n < 2:
        return None
    mean = statistics.fmean(rs)
    sd = statistics.stdev(rs)
    if sd == 0:
        return None
    return {"n": n, "mean": mean, "sd": sd, "t": mean / (sd / math.sqrt(n))}


def run(args, lines):
    say = lines.append
    days = graded_days()
    say("=" * 100)
    say(" CLOCKS VERDICT - the 5-day rules, read honestly every evening")
    say(f" graded days found: {len(days)}" + (f" ({', '.join(days)})" if days else ""))
    say(" pre-declared: no weight/exit change before its clock matures. informs, never switches.")
    say("=" * 100)

    # ---- C1 / C2 ------------------------------------------------------------
    for clock, name in (("C1", "whale_walls"), ("C2", "htf_poc")):
        say("")
        say(f" {clock}  {name} (cut rule: >=5 days AND >=300 calls AND negative pts/call)")
        per = []
        for d in days:
            j = parse_judges(d).get(name)
            if j:
                per.append((d, j))
                say(f"   {d}: {j['calls']} calls | acc {j['acc']:.0f}% | pts {j['pts']:+.1f}"
                    f" | per-call {j['percall']:+.3f}")
        nd = len(per)
        calls = sum(j["calls"] for _, j in per)
        pcalls = [j["percall"] for _, j in per]
        mean_pc = statistics.fmean(pcalls) if pcalls else None
        if nd >= 5 and calls >= 300 and mean_pc is not None:
            verdict = "DECIDE: CUT" if mean_pc < 0 else "DECIDE: KEEP"
            say(f"   -> clock MATURE (day {nd}/5, {calls} calls, mean per-call {mean_pc:+.3f})"
                f" -> {verdict} (final call = the owner, in the review)")
        else:
            if mean_pc is not None:
                say(f"   -> WAIT: day {nd}/5, {calls}/300 calls, mean per-call {mean_pc:+.3f}")
            else:
                say(f"   -> WAIT: no {name} rows parsed yet (run --peek to check the audit format)")

    # ---- C3 -------------------------------------------------------------------
    say("")
    say(" C3  exit philosophy (shadow walls vs fixed ruler; day-5 R-multiple t-test)")
    sdays, all_r = [], []
    for d in days:
        s = parse_shadow(d)
        if s["shadow"] and s["bench"]:
            gap = s["shadow"]["pts"] - s["bench"]["pts"]
            say(f"   {d}: shadow {s['shadow']['pts']:+.1f} vs ruler {s['bench']['pts']:+.1f}"
                f" -> walls {gap:+.1f} {'BETTER' if gap > 0 else 'WORSE'}")
            sdays.append(d)
            all_r += [r for r in s["rs"]]
    if len(sdays) >= 5:
        tt = t_test(all_r)
        if tt:
            sig = "SIGNIFICANT (real difference)" if abs(tt["t"]) >= 2 else \
                  "NOT significant (weather noise dominates)"
            say(f"   -> t-test on {tt['n']} shadow trades: mean R {tt['mean']:+.2f}"
                f" | sd {tt['sd']:.2f} | t = {tt['t']:+.2f} -> {sig}")
            say("      verdict feeds the A3 burst-exit design review (document next)")
        else:
            say("   -> t-test could not compute (no spread in R multiples)")
    else:
        say(f"   -> WAIT: {len(sdays)}/5 shadow days counted"
            + (f" (R-multiples collected so far: {sum(1 for r in all_r if r is not None)})"
               if all_r else ""))

    # ---- C6 --------------------------------------------------------------------
    say("")
    say(" C6  confidence calibration (conf bucket vs replayed outcome - feeds the review)")
    buckets = {"<40": [0, 0, 0.0], "40-49": [0, 0, 0.0], "50-59": [0, 0, 0.0],
               "60+": [0, 0, 0.0]}
    total = 0
    for d in days:
        for row in parse_ledger(d):
            c = row["conf"]
            k = "<40" if c < 40 else ("40-49" if c < 50 else ("50-59" if c < 60 else "60+"))
            buckets[k][0] += 1
            buckets[k][1] += 1 if row["pts"] > 0 else 0
            buckets[k][2] += row["pts"]
            total += 1
    if total:
        say("   bucket   N    WR%    sum pts   (does higher belief buy better money?)")
        for k, (n, w, pts) in buckets.items():
            if n:
                say(f"   {k:<7} {n:>4}  {100 * w / n:>4.0f}%  {pts:>8.1f}")
    else:
        say("   (no ledger rows parsed - run --peek and check the CSV columns)")

    if args.peek:
        say("")
        say(" [peek] what the machine parsed from each source (paste if it looks wrong):")
        if days:
            d = days[-1]
            j = parse_judges(d)
            say(f"   last day {d} judge rows: {j if j else 'NONE - check audit format'}")
            if not j:
                ap = f"data/day_audit_{d}.txt"
                try:
                    raw = [l.rstrip() for l in open(ap, encoding="utf-8", errors="replace")
                           if "whale_walls" in l or "htf_poc" in l][:3]
                    for l in raw:
                        say("     raw: " + l.strip()[:160])
                    if not raw:
                        say(f"     ({ap} exists but never mentions the judges - old audit build?)")
                except OSError:
                    say(f"     (cannot read {ap})")
            s = parse_shadow(d)
            say(f"   last day shadow/bench: {s['shadow']} / {s['bench']}")
            lr = parse_ledger(d)
            say(f"   last day ledger rows parsed: {len(lr)}"
                + (f" (first: {lr[0]})" if lr else ""))
            if not lr:
                lp = f"data/decision_ledger_{d}.csv"
                if os.path.exists(lp):
                    with open(lp, encoding="utf-8", errors="replace") as f:
                        say("     ledger header: " + f.readline().strip()[:200])
                else:
                    say(f"     (no {lp} - run the evening paper's Accountant desk for that day)")

    out = os.path.join("data", "clocks_verdict.txt")
    try:
        os.makedirs("data", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        say("")
        say(f" [saved] {out}")
    except OSError as e:
        say(f" [could not save {out}: {e}]")
    return 0


def main():
    ap = argparse.ArgumentParser(description="clocks verdict machine (C1/C2/C3/C6)")
    ap.add_argument("--peek", action="store_true")
    args = ap.parse_args()
    lines = []
    run(args, lines)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
