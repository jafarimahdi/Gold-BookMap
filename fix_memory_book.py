#!/usr/bin/env python3
"""
fix_memory_book.py -- the POWER memory book DOCTOR (2026-10-15, ~21:45 Bp)
==========================================================================
WHY THIS EXISTS (the honest story):
  The ops-v3 rig v1.2 wrote its 3.0MB test fixture straight into the REAL
  day-file data/power_v2_memory/power_<today>.jsonl and never removed it
  (cleanup only popped the env var). write_bytes TRUNCATES first, so the
  real pages inside were destroyed, and because the book is fail-closed
  (append_snapshot validates the whole chain before writing), it has
  refused every append since -> "memory=error MemoryIntegrityError" in
  every FAST LANE line, and ARMOR shows "book: today 3.0MB" (the garbage).

WHAT THIS DOES (a doctor, not a rewrite):
  - scans EVERY data/power_v2_memory/power_*.jsonl day-file
  - validates line by line with the BOOK'S OWN rules (it imports
    power_memory_book and reuses its real _hash/_scan chain logic)
  - HEALTHY file                    -> untouched (not even rewritten)
  - valid pages + torn/garbage tail -> backup, keep ONLY the valid pages
  - garbage with NO valid page      -> backup, remove (nothing salvageable)
                                       -- tonight's case
  - every repaired file is re-verified with the book's real reader
Backups are named power_<day>.jsonl.corrupt_<timestamp> -- the book only
ever opens the exact power_<day>.jsonl name, so backups are invisible to it.
Never touches a healthy file. Never recomputes a hash. Read-only on the
robot's code and config.

Run from the project root, with the robot STOPPED:
    python fix_memory_book.py
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path.cwd()
BOOK_DIR = ROOT / "data" / "power_v2_memory"


def load_real_reader():
    """Import the book's REAL module (lives next to this script / repo root)."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import power_memory_book as pmb  # the organ's own code
    return pmb


def valid_prefix(raw: bytes, pmb):
    """Walk the file exactly like _scan does; return (pages_kept, prefix_bytes).

    Stops at the first line that fails the book's rules (invalid JSON, not an
    object, or a hash-chain break). Everything after that point is unprovable.
    """
    previous = pmb._GENESIS
    kept = 0
    pos = 0
    for line in raw.splitlines(keepends=True):
        body = line.rstrip(b"\r\n")
        if body.strip() == b"":
            pos += len(line)          # blank lines are chain-neutral (skipped)
            continue
        try:
            row = json.loads(body.decode("utf-8"))
        except Exception:
            break
        if not isinstance(row, dict):
            break
        claimed_hash = row.get("record_hash")
        claimed_prev = row.get("previous_hash")
        payload = {k: v for k, v in row.items()
                   if k not in ("record_hash", "previous_hash")}
        try:
            ok = (claimed_prev == previous
                  and claimed_hash == pmb._hash(payload | {"previous_hash": previous}))
        except Exception:
            break
        if not ok:
            break
        previous = claimed_hash
        kept += 1
        pos += len(line)
    return kept, raw[:pos]


def verify(pmb, path: Path, expect_pages: int):
    """Re-verify with the book's REAL reader (the law: mirror the organ)."""
    rows, _, _ = pmb._scan(path)
    return len(rows) == expect_pages


def main():
    print("=" * 78)
    print("POWER MEMORY BOOK DOCTOR - data/power_v2_memory")
    print("=" * 78)
    if not BOOK_DIR.is_dir():
        print("[ -- ] no data/power_v2_memory directory found - nothing to doctor.")
        return 0
    files = sorted(BOOK_DIR.glob("power_*.jsonl"))
    if not files:
        print("[ -- ] no power_*.jsonl day-files found - nothing to doctor.")
        return 0
    try:
        pmb = load_real_reader()
    except Exception as e:
        print(f"[STOP] cannot import power_memory_book ({type(e).__name__}: {e}).")
        print("       Run this from the project root, next to the module.")
        return 1

    stamp = time.strftime("%Y%m%d_%H%M%S")
    healthy, repaired, removed, problems = [], [], [], []
    for path in files:
        raw = path.read_bytes()
        kept, prefix = valid_prefix(raw, pmb)
        tail = raw[len(prefix):]
        if not tail.strip():
            # fully valid chain. Only heal a missing trailing newline,
            # because the next append would otherwise glue onto the last page.
            if raw and not raw.endswith(b"\n"):
                bak = path.with_name(path.name + f".corrupt_{stamp}")
                os.replace(path, bak)
                path.write_bytes(raw + b"\n")
                print(f"[heal] {path.name}: valid {kept} page(s); missing final "
                      f"newline added (backup {bak.name})")
                repaired.append(path.name)
                if not verify(pmb, path, kept):
                    problems.append(path.name)
            else:
                print(f"[ ok ] {path.name}: healthy - {kept} page(s), "
                      f"{len(raw) / 1048576:.1f}MB - UNTOUCHED")
                healthy.append(path.name)
            continue
        # there is garbage: either after valid pages, or from the very start
        bak = path.with_name(path.name + f".corrupt_{stamp}")
        if kept == 0:
            os.replace(path, bak)          # backup (rename), then remove the rest
            path.unlink(missing_ok=True)
            print(f"[cut ] {path.name}: NO salvageable page ({len(raw)} bytes of "
                  f"garbage) -> removed; backup {bak.name}")
            print(f"        (this was the rig-damage case: the book starts a fresh "
                  f"chain on its next append)")
            removed.append(path.name)
        else:
            os.replace(path, bak)
            data = prefix if prefix.endswith(b"\n") else prefix + b"\n"
            path.write_bytes(data)
            print(f"[keep] {path.name}: kept {kept} valid page(s), dropped "
                  f"{len(tail)} garbage byte(s); backup {bak.name}")
            repaired.append(path.name)
            if not verify(pmb, path, kept):
                problems.append(path.name)

    print("-" * 78)
    if problems:
        print(f"[FAIL] {len(problems)} file(s) still fail the book's real reader: "
              f"{', '.join(problems)} - paste this to the assistant.")
        return 1
    print(f"[done] {len(healthy)} healthy, {len(repaired)} repaired, "
          f"{len(removed)} removed. All files pass the book's REAL reader.")
    if removed or repaired:
        print("       The book can append again. RESTART the robot and look for:")
        print("         FAST LANE ... memory=appended ...")
        print("         ARMOR    ... book: today 0.0MB ...   (fresh file; grows as pages land)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
