#!/usr/bin/env python3
"""tools/latency_spans.py -- Stage L1: where do the 2 seconds actually go?

`audit_day.py` (TEST 12) tells the owner the *size* of the problem
(`median 2,069 ms` against a `500 ms` target, `0/910` cycles passing) and
`tail_probe.py` attributes the tail to `STEP1/STEP2/STEP3`. Neither answers the
question that decides the next patch: **what is inside the rest of the cycle?**

From `Stage L1a` every untimed block in `run_pipeline()` reports its own
`LATENCY <name> <ms>ms` line, so this tool can rebuild each cycle and say:

  * per span: count, median, p95, max, and share of the cycle total
  * per cycle: `named` (sum of spans) vs `TOTAL pipeline` -> the leftover
  * the slowest cycles with a timestamp, so the owner can look at what the tape
    and the broker were doing at that moment

Read-only: it opens one log file and prints/writes a text report. It never
imports the trading runtime and never touches MT5.

Usage (git bash, project root):
    python tools/latency_spans.py --date 2026-11-11
    python tools/latency_spans.py --date 2026-11-11 --top 5
"""

from __future__ import annotations

import argparse
import re
import statistics
from datetime import datetime
from pathlib import Path

STAMP_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})")
SPAN_RE = re.compile(r"LATENCY\s+(?P<name>[A-Za-z][^>]*?)\s+(?P<ms>[\d.]+)\s*ms")
STAGE_ORDER = [
    "PREFLIGHT env+idharvest+lock+maint",
    "STEP1 acquire",
    "STEP2 analyze",
    "STEP3 AI",
    "STEP4 execute",
    "STEP5 monitor",
    "CLOSED-DEAL log (MT5 history)",
    "SIGNAL bridge + persist",
    "DECISION journal write",
    "SUMMARY blocks",
]
TOTAL = "TOTAL pipeline"


def _log_path(date_str: str) -> Path:
    compact = date_str.replace("-", "")
    for cand in (Path(f"logs/trading_{compact}.log"), Path(f"logs/trading_{date_str}.log")):
        if cand.exists():
            return cand
    raise SystemExit(f"no log for {date_str} (looked for logs/trading_{compact}.log)")


def parse_cycles(path: Path):
    """Group `LATENCY` spans into cycles: a `TOTAL pipeline` line closes one."""
    cycles, current, clock = [], {}, None
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = STAMP_RE.match(line)
            if m:
                clock = f"{m.group(1)} {m.group(2)}"
            for span in SPAN_RE.finditer(line):
                name, ms = span.group("name").strip(), float(span.group("ms"))
                if name.startswith("feed age") or name.startswith("quote age") or name.startswith("depth age"):
                    continue
                if name == TOTAL:
                    current[TOTAL] = ms
                    cycles.append((clock, current))
                    current = {}
                else:
                    current[name] = ms
    return cycles


def _pct(values, q):
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def report(cycles, top: int) -> str:
    if not cycles:
        return "no complete cycles (no 'LATENCY TOTAL pipeline' line) -- was L1a installed?"
    names = [n for n in STAGE_ORDER if any(n in c for _, c in cycles)]
    extra = sorted({n for _, c in cycles for n in c} - set(names) - {TOTAL})
    names += extra

    totals = [c[TOTAL] for _, c in cycles]
    med_total = statistics.median(totals)
    out = []
    out.append(f"cycles           : {len(cycles)}")
    out.append(f"total pipeline   : median {med_total:.0f} ms | p95 {_pct(totals,0.95):.0f} ms | max {max(totals):.0f} ms")
    out.append("")
    head = f"{'SPAN':<38}{'n':>5}{'median':>9}{'p95':>9}{'max':>9}{'share':>8}"
    out.append(head)
    out.append("-" * len(head))
    for name in names:
        vals = [c[name] for _, c in cycles if name in c]
        if not vals:
            continue
        share = (statistics.median(vals) / med_total * 100.0) if med_total else 0.0
        out.append(f"{name:<38}{len(vals):>5}{statistics.median(vals):>9.0f}"
                   f"{_pct(vals,0.95):>9.0f}{max(vals):>9.0f}{share:>7.1f}%")
    named = [sum(c[n] for n in names if n in c) for _, c in cycles]
    out.append("-" * len(head))
    out.append(f"{'NAMED (sum of spans)':<38}{len(named):>5}{statistics.median(named):>9.0f}"
               f"{_pct(named,0.95):>9.0f}{max(named):>9.0f}"
               f"{(statistics.median(named)/med_total*100.0) if med_total else 0:>7.1f}%")
    leftover = [c[TOTAL] - sum(c[n] for n in names if n in c) for _, c in cycles]
    out.append(f"{'LEFT OVER (unmeasured)':<38}{len(leftover):>5}{statistics.median(leftover):>9.0f}"
               f"{_pct(leftover,0.95):>9.0f}{max(leftover):>9.0f}"
               f"{(statistics.median(leftover)/med_total*100.0) if med_total else 0:>7.1f}%")
    out.append("")
    out.append(f"slowest {top} cycles:")
    for clock, c in sorted(cycles, key=lambda x: x[1][TOTAL], reverse=True)[:top]:
        parts = ", ".join(f"{n.split(' ')[0]}={c[n]:.0f}" for n in names if n in c)
        out.append(f"  {clock}  total={c[TOTAL]:.0f} ms  [{parts}]")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Stage L1 latency span report (read-only).")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--top", type=int, default=5)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    path = _log_path(args.date)
    text = report(parse_cycles(path), args.top)
    header = f"LATENCY SPAN REPORT -- {args.date} -- {path}\n" + "=" * 60 + "\n"
    body = header + text + "\n"
    print(body)
    out = Path(args.out) if args.out else Path("data") / f"latency_spans_{args.date}.txt"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")
        print(f"[saved] {out}")
    except OSError as exc:
        print(f"[warn] could not write {out}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
