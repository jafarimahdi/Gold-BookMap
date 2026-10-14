#!/usr/bin/env python3
"""tail_probe.py -- THE TRUCK CAMERA (G11): where do the slow cycles live?

A1 (2026-10-12 receipt) fixed the median (2.45-2.92s -> 1.88s) but the p95
tail (~10-16s) survived -- and since the twin proved serves cost ~4ms, the
tail is NOT the verdict layer.  It lives in the live-eyes path: the per-cycle
ingest of the full snapshot (STEP1) + the scout mirror chain (STEP2's live
part).  This camera reads the trading log's per-cycle STEP1/STEP2/STEP3
millisecond lines and answers:

  WHO     - of the slow cycles, is STEP1 (ingest) or STEP2 (live eyes) the owner?
  WHEN    - which hours do slow cycles cluster in?  do they hug the M5 bells
            (minute % 5 in {4,0}) or the news windows?
  HOW BIG - the honest distribution: median / p95 / max, top-N slowest cycles
            with their times of day.

Read-only.  Self-diagnosing: --peek prints the first raw lines it matched (and
a few 'ms' lines it did NOT match) so a format surprise is visible instantly.
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
MS_LINE_RE = re.compile(r"\d+\s*ms")


def find_log(date_str):
    cands = [f"logs/trading_{date_str}.log", f"logs/trading_{date_str.replace('-', '')}.log"]
    cands += sorted(glob.glob(f"logs/*{date_str}*.log"))
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def parse_cycles(path):
    """One cycle = a STEP1 value closed by the next STEP2 value (same line or
    a following line).  Returns (cycles, peek_matched, peek_unmatched)."""
    cycles, pend_s1, pend_t = [], None, None
    matched, unmatched = [], []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = TIME_RE.match(line)
            tod = m.group(2) if m else None
            hits = {k: int(rx.search(line).group(1))
                    for k, rx in STEP_RE.items() if rx.search(line)}
            if hits:
                matched.append(line.rstrip()[:160])
                if "STEP1" in hits:
                    pend_s1, pend_t = hits["STEP1"], tod
                if "STEP2" in hits and pend_s1 is not None:
                    total = pend_s1 + hits["STEP2"] + hits.get("STEP3", 0)
                    cycles.append({"tod": pend_t or tod, "s1": pend_s1,
                                   "s2": hits["STEP2"], "s3": hits.get("STEP3", 0),
                                   "total": total})
                    pend_s1, pend_t = None, None
            elif MS_LINE_RE.search(line):
                unmatched.append(line.rstrip()[:160])
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
    say(" reads the trading log's per-cycle STEP1/STEP2/STEP3 ms lines - read-only")
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
    say(f" cycles parsed: {len(cycles)} | median {statistics.median(tots):.0f}ms"
        f" | p95 {pct(tots, 0.95):.0f}ms | max {max(tots):.0f}ms")
    slow = [c for c in cycles if c["total"] >= args.slow_ms]
    say(f" slow cycles (>= {args.slow_ms}ms): {len(slow)} ({100 * len(slow) / len(cycles):.1f}%)")
    say("")

    say(f" WHO owns the tail (of the {len(slow)} slow cycles):")
    if slow:
        s1_med = statistics.median([c["s1"] for c in slow])
        s2_med = statistics.median([c["s2"] for c in slow])
        say(f"   STEP1 (ingest) median {s1_med:.0f}ms | STEP2 (live eyes) median {s2_med:.0f}ms"
            f" -> {'STEP1' if s1_med > s2_med else 'STEP2'} is the main owner")
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
        say(f"   {c['tod'] or '--:--:--'}  total {c['total']:>7}ms"
            f"  (STEP1 {c['s1']}ms + STEP2 {c['s2']}ms"
            + (f" + STEP3 {c['s3']}ms)" if c["s3"] else ")"))
    say("")
    say(" how to read it:")
    say("   STEP1 = the truck loading the full snapshot; STEP2 = the live-eyes")
    say("   chain (scout mirror) on it.  The cached verdict layer is NOT here -")
    say("   A1's serves are ~4ms (twin-proven).  Clusters at bells = bar-close")
    say("   work; clusters at news hours = event bursts; spread flat = ingest/GC.")
    say(" informs, never switches - feeds G11 and the A2 design (burst lane")
    say("   must ride its own light path, not this truck).")

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
