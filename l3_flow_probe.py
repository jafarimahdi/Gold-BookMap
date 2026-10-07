"""l3_flow_probe.py — blind-spot investigation (report only, touches nothing).

Answers: WHY is the l3_aggr_limit judge permanently silent, WHAT fields does the
mbo.csv stream actually carry, and WHERE is the validated L3 executed-flow
evidence hiding (if it exists at all).

Usage (repo root):  python *flow_probe*.py
Reads the first N lines of mbo.csv (BOOKMAP_MBO_FILE from .env or the default),
prints: delimiter, column/field names, a value sketch, and a verdict listing the
candidate fields an L3 executed-flow judge would need (aggressor side + executed
size + limit-order participation).
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

SAMPLE_LINES = 50000

KEYWORDS = ("aggressor", "aggr", "side", "order", "exec", "trade", "type",
            "size", "volume", "price", "bid", "ask", "buy", "sell", "flag",
            "event", "market", "limit")


def mbo_path() -> Path:
    for line in Path(".env").read_text(encoding="utf-8", errors="replace").splitlines() if Path(".env").exists() else []:
        if line.strip().startswith("BOOKMAP_MBO_FILE="):
            val = line.split("=", 1)[1].strip()
            if val:
                return Path(val)
    return Path("mbo.csv")


def main() -> int:
    path = mbo_path()
    print(f"== L3 executed-flow probe (blind-spot investigation) ==")
    print(f"file: {path}")
    if not path.exists():
        print("VERDICT: file not found -> Bookmap MBO recording is off or the")
        print("path in .env (BOOKMAP_MBO_FILE) is wrong. Fix that first:")
        print("  BOOKMAP_WRITE_MBO=1 and BOOKMAP_MBO_FILE pointing at mbo.csv.")
        return 1

    sample: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if i >= SAMPLE_LINES:
                break
            sample.append(line.rstrip("\n"))
    non_empty = [ln for ln in sample if ln.strip()]
    if not non_empty:
        print("VERDICT: file is empty -> MBO stream is not being recorded yet.")
        return 1

    head = non_empty[0]
    delim = "," if head.count(",") >= 2 else ("\t" if "\t" in head else ";")
    rows = list(csv.reader(io.StringIO("\n".join(non_empty[:200])), delimiter=delim))
    header = rows[0] if rows else []
    looks_like_header = any(c.isalpha() for c in "".join(header))
    fields = header if looks_like_header else [f"col{i}" for i in range(len(rows[0]))]

    print(f"lines sampled: {len(non_empty)}  delimiter: {delim!r}  "
          f"header-like first row: {looks_like_header}")
    print(f"fields ({len(fields)}): {fields}")
    for r in rows[1:4]:
        print("  sample row:", r[:12])

    lower = [f.lower() for f in fields]
    def has(*words):
        return [fields[i] for i, f in enumerate(lower) if any(w in f for w in words)]

    print("\ncandidate columns:")
    print("  aggressor/side :", has("aggressor", "aggr", "side", "direction") or "NONE")
    print("  executed size  :", has("size", "volume", "qty", "amount") or "NONE")
    print("  order type     :", has("type", "event", "market", "limit", "flag") or "NONE")
    print("  price          :", has("price", "px") or "NONE")

    side = has("aggressor", "aggr", "side", "direction")
    size = has("size", "volume", "qty", "amount")
    typ = has("type", "event", "market", "limit", "flag")
    print("\nVERDICT:")
    if side and size and typ:
        print("  GOOD NEWS: the stream carries side + size + type.")
        print("  -> l3_aggr_limit CAN be wired: map these columns into the M5")
        print("     adapter as executed-flow (aggressor-tagged volume split),")
        print("     validate one day against the tape, then enable the judge.")
    elif side and size:
        print("  PARTIAL: side + size exist, no explicit market/limit type.")
        print("  -> executed flow is derivable if 'side' means AGGRESSOR side;")
        print("     verify that meaning against one day of tape before wiring.")
    else:
        print("  MISSING: the stream does not carry the fields an executed-flow")
        print("  judge needs. l3_aggr_limit is silent for a GOOD reason.")
        print("  -> formally accept the silence (keep it excluded, as now), or")
        print("     record a richer MBO export from Bookmap and re-check.")
    print("\nThis probe is report-only. It changes nothing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
