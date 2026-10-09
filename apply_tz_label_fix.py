#!/usr/bin/env python3
"""apply_tz_label_fix.py -- fix the audit's Europe/Budapest DST end date.

The bug (reproduced 2026-10-08): the Budapest tzinfo class inside audit_day.py
ends summer time on the FIRST Sunday of October; the EU rule is the LAST Sunday.
Every year from the first to the last Sunday of October the auditor therefore
prints "CET" and offset +1 while the truth is "CEST" and +2. On 2026-10-08 the
report header said "22:46:08 CET" -- the name was wrong (cosmetic), and any
UTC->local conversion through this class is off by one hour in that window.

The ROBOT is not touched: session.py already has the correct last-Sunday rule.
This patches ONLY audit_day.py (the day-report tool).

All or nothing: if the anchor is not found exactly once, NOTHING is written.
Backup: audit_day.py.pre_tzfix (gitignored). Undo: copy the backup back.
"""
import sys, shutil, datetime

TARGET = "audit_day.py"
BACKUP = TARGET + ".pre_tzfix"

OLD = (
    "        # last Sunday of March 02:00 local -> first Sunday of October 03:00 local\n"
    "        mar_last = datetime(y, 3, 31)\n"
    "        dst_on = mar_last - timedelta(days=(mar_last.weekday() + 1) % 7)\n"
    "        oct_first = datetime(y, 10, 1)\n"
    "        dst_off = oct_first + timedelta(days=(6 - oct_first.weekday()) % 7)\n"
)

NEW = (
    "        # last Sunday of March 02:00 local -> LAST Sunday of October 03:00 local (EU rule)\n"
    "        mar_last = datetime(y, 3, 31)\n"
    "        dst_on = mar_last - timedelta(days=(mar_last.weekday() + 1) % 7)\n"
    "        oct_last = datetime(y, 10, 31)\n"
    "        dst_off = oct_last - timedelta(days=(oct_last.weekday() + 1) % 7)\n"
)

MARKER = "oct_last = datetime(y, 10, 31)"


def verify_class(text):
    """Exec the Budapest class from the patched source and check the EU rule."""
    start = text.index("class Budapest")
    end = text.index("BUDA = Budapest()")
    ns = {"timedelta": datetime.timedelta, "tzinfo": datetime.tzinfo,
          "datetime": datetime.datetime}
    exec(text[start:end], ns)
    B = ns["Budapest"]()
    d = lambda s: datetime.datetime.fromisoformat(s)
    checks = [
        (d("2026-10-08"), "CEST", "mid-October is still summer time"),
        (d("2026-10-24"), "CEST", "the day before the last Sunday is still summer"),
        (d("2026-10-26"), "CET",  "the Monday after the switch is winter time"),
        (d("2026-04-01"), "CEST", "April is summer time"),
        (d("2026-03-20"), "CET",  "before the last Sunday of March is winter"),
    ]
    bad = []
    for dt, want, why in checks:
        got = B.tzname(dt)
        if got != want:
            bad.append(f"  {dt.date()} -> {got}, expected {want} ({why})")
    return bad


def main():
    try:
        src = open(TARGET, encoding="utf-8").read()
    except OSError as e:
        print(f"ERROR: cannot read {TARGET}: {e}")
        sys.exit(1)

    if MARKER in src:
        print(f"ALREADY PATCHED {TARGET} (marker found) - nothing to do.")
        bad = verify_class(src)
        if bad:
            print("WARNING: marker present but the DST rule still fails checks:")
            print("\n".join(bad))
            sys.exit(1)
        print("DST rule verified: CEST until the last Sunday of October, then CET.")
        return

    n = src.count(OLD)
    if n != 1:
        print(f"ERROR: anchor found {n}x in {TARGET} (need exactly 1). NOTHING was written.")
        print("The file differs from the expected shape - paste the class Budapest block to your assistant.")
        sys.exit(1)

    new_src = src.replace(OLD, NEW, 1)

    bad = verify_class(new_src)
    if bad:
        print("ERROR: patched class failed self-verification - NOTHING was written:")
        print("\n".join(bad))
        sys.exit(1)

    shutil.copy2(TARGET, BACKUP)
    print(f"backup {BACKUP}")
    open(TARGET, "w", encoding="utf-8").write(new_src)
    print(f"PATCHED {TARGET} (DST ends on the LAST Sunday of October, EU rule)")
    print("Effect: report header prints CEST/CET correctly; UTC->local conversions")
    print("inside the auditor are right during the Oct first-Sunday..last-Sunday window.")
    print("Next:  python -m py_compile audit_day.py   (silence = good)")
    print("       python tools/selftest_audit_day.py  (16/16 PASS)")


if __name__ == "__main__":
    main()
