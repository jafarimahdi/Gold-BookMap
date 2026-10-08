#!/usr/bin/env python3
"""
apply_report16_3.py - TEST 3 FIX: see the whole error line (2026-10-08 night)
============================================================================
One tiny defect in report16.1's own fix (found on the real 2026-10-07 re-grade):

    test 3's error scan used a CAPTURING group: re.findall returned only the
    word "ERROR", never the rest of the line. So the protective-shout filter
    (kill switch / flash crash / real-account belt) had nothing to search and
    the L4 kill switch still failed the day as a "pipeline crash".

    re.findall(r"(ERROR|...")         -> ["ERROR"]                    (old)
    re.findall(r"(?:ERROR|...")       -> ["ERROR   [main] L4 KILL..."] (new)

One character-class change: the group becomes non-capturing, the whole line
is kept, the kill switch is recognised as the guard working, and TEST 3 also
shows a better "e.g." excerpt. Tracebacks and real crashes still FAIL.

Touch ONLY audit_day.py.

Run from the project root, then re-grade the day:

    python apply_report16_3.py
    bash daily_check.sh 2026-10-07
"""
from pathlib import Path
import shutil
import sys

ROOT = Path.cwd()

OLD = '    _all_err = re.findall(r"(ERROR|CRITICAL|Traceback|Exception)[^\\n]{0,120}", text)'
NEW = '    _all_err = re.findall(r"(?:ERROR|CRITICAL|Traceback|Exception)[^\\n]{0,120}", text)'
MARKER = 're.findall(r"(?:ERROR|CRITICAL|Traceback|Exception)'


def main() -> int:
    if not (ROOT / "main.py").exists():
        print("ERROR: run this from the project root (the folder with main.py)")
        return 2
    path = ROOT / "audit_day.py"
    if not path.exists():
        print("MISSING audit_day.py")
        return 1
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print("ALREADY audit_day.py")
        return 0
    n = text.count(OLD)
    if n != 1:
        print(f"ERROR: anchor found {n}x (want 1) for: {OLD!r}")
        print("NOTHING was written - report this output.")
        return 1
    bak = path.with_name(path.name + ".pre_report16_3")
    if not bak.exists():
        shutil.copy2(path, bak)
        print(f"backup {bak.name}")
    text = text.replace(OLD, NEW, 1)
    path.write_text(text, encoding="utf-8")
    print("PATCHED audit_day.py (1 edit: error scan now keeps the whole line)")
    print("\nDone. Next: bash daily_check.sh 2026-10-07")
    return 0


if __name__ == "__main__":
    sys.exit(main())
