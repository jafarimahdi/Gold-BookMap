#!/usr/bin/env python3
"""tail_probe.py -- THE TRUCK CAMERA (G11): where do the slow cycles live?

A1 (2026-10-12 receipt) fixed the median (2.45-2.92s -> 1.88s) but the p95
tail (~10-16s) survived -- and since the twin proved serves cost ~4ms, the
tail is NOT the verdict layer. The loop's measured stages are STEP1 (ingest),
STEP2 (live eyes), STEP3 (AI), and LATENCY TOTAL pipeline. This camera prefers
the full pipeline total when logged, then attributes the tail to its stages:

  WHO     - of the slow cycles, is STEP1 (ingest), STEP2 (live eyes), or STEP3 (AI) the owner?
  WHEN    - which hours do slow cycles cluster in?  do they hug the M5 bells
            (minute % 5 in {4,0}) or the news windows?
  HOW BIG - the honest distribution: median / p95 / max, top-N slowest cycles
            with their times of day.

Read-only. Self-diagnosing: --peek prints matched stage/total lines and a few
unmatched 'ms' lines. A full pipeline total is used when present; older logs
without it fall back to a component sum and are explicitly marked partial.
Writes data/tail_probe_<date>.txt.

Usage (git bash, project root):
    python tail_probe.py --date 2026-10-12            # logs/trading_<date>.log
    python tail_probe.py --date 2026-10-12 --peek     # show what it matched
"""
import argparse
import glob
import os
import re
import statistics
from datetime import datetime

TIME_RE = re.compile(r"^(\d{4}[-/]\d{2}[-/]\d{2}[ T])?(\d{1,2}:\d{2}:\d{2})")
STEP_RE = {"STEP1": re.compile(r"STEP1[^0-9]*(\d+)\s*ms"),
           "STEP2": re.compile(r"STEP2[^0-9]*(\d+)\s*ms"),
           "STEP3": re.compile(r"STEP3[^0-9]*(\d+)\s*ms")}
TOTAL_RE = re.compile(r"LATENCY\s+TOTAL\s+pipeline\s+(\d+)\s*ms", re.IGNORECASE)
MS_LINE_RE = re.compile(r"\d+\s*ms")


def find_log(date_str):
    cands = [f"logs/trading_{date_str}.log", f"logs/trading_{date_str.replace('-', '')}.log"]
    cands += sorted(glob.glob(f"logs/*{date_str}*.log"))
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def parse_cycles(path):
    """Parse stage lines and the authoritative full-pipeline total when present.

    Stage events may be on one line or separate lines. A new STEP1 closes any
    prior incomplete cycle; LATENCY TOTAL pipeline closes a complete cycle.
    Older logs without a total fall back to STEP1+STEP2+STEP3 and are marked
    ``step-sum`` so callers can distinguish partial timing from full L4 time.
    Returns (cycles, peek_matched, peek_unmatched).
    """
    cycles, current = [], None
    matched, unmatched = [], []

    def finish(total=None):
        nonlocal current
        if current is None:
            return
        if total is None:
            current["total"] = current["s1"] + current["s2"] + current["s3"]
            current["total_source"] = "step-sum"
        else:
            current["total"] = int(total)
            current["total_source"] = "pipeline"
        cycles.append(current)
        current = None

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = TIME_RE.match(line)
            tod = m.group(2) if m else None
            hits = {k: int(rx.search(line).group(1))
                    for k, rx in STEP_RE.items() if rx.search(line)}
            total_match = TOTAL_RE.search(line)
            if hits or total_match:
                matched.append(line.rstrip()[:160])
                if "STEP1" in hits:
                    # A fresh STEP1 is the only reliable boundary for a legacy
                    # cycle that did not emit a full-pipeline total.
                    finish()
                    current = {"tod": tod, "s1": 0, "s2": 0, "s3": 0}
                elif current is None:
                    current = {"tod": tod, "s1": 0, "s2": 0, "s3": 0}
                elif current.get("tod") is None and tod:
                    current["tod"] = tod

                for stage, value in hits.items():
                    current[{"STEP1": "s1", "STEP2": "s2", "STEP3": "s3"}[stage]] = value
                if total_match:
                    finish(int(total_match.group(1)))
            elif MS_LINE_RE.search(line):
                unmatched.append(line.rstrip()[:160])
    finish()
    return cycles, matched, unmatched


def pct(vals, p):
    if not vals:
        return None
    vals = sorted(vals)
    return vals[min(int(p * (len(vals) - 1)), len(vals) - 1)]


def hhmm_bucket(tod):
    try:
        return int(tod.split(":")[0])
    except (AttributeError, ValueError):
        return None


def bell_proximity(tod):
    """minute%5: 4 or 0 = hugging an M5 bell boundary."""
    try:
        return int(tod.split(":")[1]) % 5 in (0, 4)
    except (AttributeError, ValueError):
        return False


def run(args, lines):
    say = lines.append
    say("=" * 100)
    say(f" TAIL PROBE (the truck camera, G11) - {args.date}")
    say(" reads full LATENCY TOTAL pipeline lines plus STEP1/STEP2/STEP3 attribution - read-only")
    say("=" * 100)

    path = args.log or find_log(args.date)
    if not path:
        say(f" [no log] no logs/trading_{args.date}.log found - check the file name")
        return 0
    say(f" log: {path}")

    cycles, matched, unmatched = parse_cycles(path)
    if args.peek:
        say(" [peek] first lines MATCHED (the camera sees these):")
        for l in matched[:10]:
            say("   | " + l)
        if unmatched:
            say(" [peek] 'ms' lines NOT matched (if these carry the real latency,")
            say("        paste them to the assistant and the camera learns them):")
            for l in unmatched[:5]:
                say("   ? " + l)
    if len(cycles) < 20:
        say(f" [too few] only {len(cycles)} cycles parsed - run with --peek and check")
        say(" the formats above; if the log uses another shape, paste a peek line.")
        return 0

    tots = [c["total"] for c in cycles]
    full_n = sum(c["total_source"] == "pipeline" for c in cycles)
    fallback_n = len(cycles) - full_n
    say(f" cycles parsed: {len(cycles)} | full-pipeline totals: {full_n}"
        f" | step-sum fallback: {fallback_n} | median {statistics.median(tots):.0f}ms"
        f" | p95 {pct(tots, 0.95):.0f}ms | max {max(tots):.0f}ms")
    if fallback_n:
        say(" [partial] fallback cycles use STEP sums, not the full L4 duration; "
            "distribution is incomplete for those rows.")
    slow = [c for c in cycles if c["total"] >= args.slow_ms]
    say(f" slow cycles (>= {args.slow_ms}ms): {len(slow)} ({100 * len(slow) / len(cycles):.1f}%)")
    say("")

    say(f" WHO owns the tail (of the {len(slow)} slow cycles):")
    if slow:
        components = (("s1", "STEP1 (ingest)"),
                      ("s2", "STEP2 (live eyes)"),
                      ("s3", "STEP3 (AI)"))
        medians = {}
        for key, label in components:
            values = [c[key] for c in slow if c[key] > 0]
            if values:
                medians[label] = statistics.median(values)
                say(f"   {label} median {medians[label]:.0f}ms")
        if medians:
            owner = max(medians, key=medians.get)
            say(f"   -> {owner} is the main owner")
        else:
            say("   no stage timings were available for the slow cycles")
    say("")
    say(" WHEN (hours of the slow cycles):")
    hours = {}
    for c in slow:
        h = hhmm_bucket(c["tod"])
        if h is not None:
            hours[h] = hours.get(h, 0) + 1
    for h in sorted(hours):
        say(f"   {h:02d}:00-{h:02d}:59   {hours[h]}")
    at_bell = sum(1 for c in slow if bell_proximity(c["tod"]))
    say(f"   hugging an M5 bell (minute%5 in 0,4): {at_bell}/{len(slow)}"
        + (f" ({100 * at_bell / len(slow):.0f}%)" if slow else ""))
    say("")
    say(f" TOP {args.top} slowest cycles:")
    for c in sorted(slow, key=lambda x: -x["total"])[:args.top]:
        source = "full" if c["total_source"] == "pipeline" else "step-sum"
        say(f"   {c['tod'] or '--:--:--'}  total {c['total']:>7}ms [{source}]"
            f"  (STEP1 {c['s1']}ms + STEP2 {c['s2']}ms + STEP3 {c['s3']}ms)")
    say("")
    say(" how to read it:")
    say("   STEP1 = ingest; STEP2 = live eyes; STEP3 = synchronous AI/model calls.")
    say("   [full] totals come from LATENCY TOTAL pipeline; [step-sum] rows are")
    say("   a legacy partial fallback and must not be treated as end-to-end time.")
    say("   Slow cycles may hug bar-close or news windows; use stage values to")
    say("   identify where time was spent. This is read-only and never switches.")

    out = os.path.join("data", f"tail_probe_{args.date}.txt")
    try:
        os.makedirs("data", exist_ok=True)
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        say(f" [saved] {out}")
    except OSError as e:
        say(f" [could not save {out}: {e}]")
    return 0


def main():
    ap = argparse.ArgumentParser(description="truck camera (G11 tail hunt)")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--log", default="")
    ap.add_argument("--slow-ms", type=int, default=5000)
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--peek", action="store_true")
    args = ap.parse_args()
    lines = []
    run(args, lines)
    print("\n".join(lines))


if __name__ == "__main__":
    main()
