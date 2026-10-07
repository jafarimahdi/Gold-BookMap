#!/usr/bin/env python3
"""
apply_report16_2.py - SELFTEST CLOCK FIX (2026-10-07 late night)
===============================================================
One defect, found and reproduced exactly:

    the writers->readers contract writes its fake log LINES with the real wall
    clock (logging asctime), while the case grades them against the UTC calendar
    date. Whenever machine-local date and UTC date disagree (after local midnight
    / a machine clock that runs behind), test 1's day filter drops every fake log
    line and the selftest fails with "contract: session story missing from the
    report" - a calendar split, not a robot or report problem.

Reproduced: same code passes with TZ=UTC and fails with TZ=Europe/Budapest after
midnight. This fix gives the fixture ONE frozen calendar (same trick the fixture
already used for file names - build 24u):

  (a) the fixture day comes from the case via BM_CONTRACT_DAY - writer files,
      writer stamps and the auditor's --date can never disagree again;
  (b) the fake log LINE stamps are frozen to that day too (formatTime override);
  (c) if the session story ever goes missing again, the selftest prints a
      one-line diagnosis instead of failing silently.

Touches ONLY tools/selftest_audit_day.py. Robot and day report untouched.

Run from the project root, then re-run the selftest:

    python apply_report16_2.py
    python tools/selftest_audit_day.py
"""
from pathlib import Path
import shutil
import sys

ROOT = Path.cwd()

REPLACES = [
    # (a1) the writer's frozen clock takes its date from the case
    ('class _FrozenDT(datetime):\n'
     '    _BASE = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0,\n'
     '                                               microsecond=0)',
     'class _FrozenDT(datetime):\n'
     '    # 24u-b: the date comes from the CASE (BM_CONTRACT_DAY), so the writer\'s\n'
     '    # day-files and the auditor\'s --date can never disagree, even if the run\n'
     '    # straddles midnight or the machine clock is off.\n'
     '    _BASE = datetime.strptime(\n'
     '        __import__("os").environ.get("BM_CONTRACT_DAY")\n'
     '        or datetime.now(timezone.utc).strftime("%Y-%m-%d"),\n'
     '        "%Y-%m-%d").replace(tzinfo=timezone.utc, hour=12, minute=0, second=0,\n'
     '                            microsecond=0)'),
    # (b) freeze the fake log LINE stamps to the same day
    ('main.setup_logging()\n'
     '\n'
     'main._write_run_config()',
     'main.setup_logging()\n'
     '\n'
     '# 24u-b: the log LINES still carried the real wall clock (logging asctime)\n'
     '# while the case grades by the UTC calendar - after local midnight the two\n'
     '# disagree by one day, test 1 drops every line and the contract died with\n'
     '# "session story missing". Freeze the line stamps too. ("Never let a test\n'
     '# depend on the locale.")\n'
     'import logging as _logging\n'
     '_logging.Formatter.formatTime = (\n'
     '    lambda self, record, datefmt=None:\n'
     '    _FrozenDT.now().strftime("%Y-%m-%d %H:%M:%S") + ",000")\n'
     '\n'
     'main._write_run_config()'),
    # (a2) the case computes the day ONCE and hands it to the writer
    ('        (proj / "_write.py").write_text(WRITER_SCRIPT, encoding="utf-8")\n'
     '        w = subprocess.run([sys.executable, "_write.py"], env=_child_env(), cwd=proj,\n'
     '                           capture_output=True, text=True, encoding="utf-8",\n'
     '                           errors="replace", timeout=300)',
     '        (proj / "_write.py").write_text(WRITER_SCRIPT, encoding="utf-8")\n'
     '        _wenv = _child_env()\n'
     '        _now = datetime.now(timezone.utc)\n'
     '        day, ymd = _now.strftime("%Y-%m-%d"), _now.strftime("%Y%m%d")\n'
     '        _wenv["BM_CONTRACT_DAY"] = day   # 24u-b: one calendar for writer AND reader\n'
     '        w = subprocess.run([sys.executable, "_write.py"], env=_wenv, cwd=proj,\n'
     '                           capture_output=True, text=True, encoding="utf-8",\n'
     '                           errors="replace", timeout=300)'),
    # (a2-continued) the old day computation after the writer is now redundant
    ('        # 24u: TWO calendars are in play here and they are not the same one.\n'
     '        #   - the robot STAMPS every row with datetime.now(timezone.utc) and names its\n'
     '        #     per-day files from the UTC date  -> that is the writers\' calendar\n'
     '        #   - audit_day BUCKETS rows into days by Budapest local time\n'
     '        #     -> that is the readers\' calendar\n'
     '        # Between 22:00 and 24:00 UTC (00:00-02:00 Budapest in summer) they disagree by\n'
     '        # one day. A naive datetime.now() matched neither and the contract check failed\n'
     '        # every night after midnight. Ask each side in its own calendar.\n'
     '        # the fixture\'s clock is frozen at 12:00 UTC (see _FrozenDT in the writer),\n'
     '        # so the UTC date and the Budapest date are the same day by construction.\n'
     '        _now = datetime.now(timezone.utc)\n'
     '        day, ymd = _now.strftime("%Y-%m-%d"), _now.strftime("%Y%m%d")',
     '        # 24u/b: day + ymd are computed ONCE above and shared with the writer via\n'
     '        # BM_CONTRACT_DAY - see the 24u-b notes in WRITER_SCRIPT.'),
    # (c) self-diagnosis instead of a silent failure
    ('        if not fails:\n'
     '            print("[contract] robot writers -> auditor readers, end to end: OK")',
     '        if any("session story" in f for f in fails):\n'
     '            lg = sorted((proj / "logs").glob("trading_*.log"))\n'
     '            if lg and lg[0].stat().st_size:\n'
     '                first = lg[0].read_text(encoding="utf-8", errors="replace")\n'
     '                print("[diag] session story missing ->", lg[0].name,\n'
     '                      "first line:", first.splitlines()[0][:80] if first else "EMPTY")\n'
     '            else:\n'
     '                print("[diag] session story missing -> no/empty logs/trading_*.log "\n'
     '                      "in the fixture (the writers did not reach the log file)")\n'
     '            m1 = re.search(r"no log lines dated[^\\n]*", out)\n'
     '            if m1:\n'
     '                print("[diag] test 1 said:", m1.group(0)[:100])\n'
     '        if not fails:\n'
     '            print("[contract] robot writers -> auditor readers, end to end: OK")'),
]

MARKER = "BM_CONTRACT_DAY"


def main() -> int:
    if not (ROOT / "main.py").exists():
        print("ERROR: run this from the project root (the folder with main.py)")
        return 2
    path = ROOT / "tools" / "selftest_audit_day.py"
    if not path.exists():
        print("MISSING tools/selftest_audit_day.py")
        return 1
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print("ALREADY tools/selftest_audit_day.py")
        return 0
    for old, new in REPLACES:
        n = text.count(old)
        if n != 1:
            print(f"ERROR: anchor found {n}x (want 1) for: {old[:70]!r}")
            print("NOTHING was written - report this output.")
            return 1
    bak = path.with_name(path.name + ".pre_report16_2")
    if not bak.exists():
        shutil.copy2(path, bak)
        print(f"backup {bak.name}")
    for old, new in REPLACES:
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")
    print(f"PATCHED tools/selftest_audit_day.py ({len(REPLACES)} edit(s))")
    print("\nDone. Next: python tools/selftest_audit_day.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
