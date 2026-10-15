#!/usr/bin/env python3
"""
apply_ops_fix.py -- OPS FIX v1: stop the diary treadmill + light up the fast lane
==================================================================================
(2026-10-15 evening, owner's order after the 17:24-17:28 cycles + the disk receipt.)

TWO fixes, ONE file (main.py only - the robot's plumbing, NOT its brain):

  FIX 1 (F20) THE DIARY TREADMILL -- proven by the owner's receipt:
       data/snapshots_history.jsonl = 187,056,257 bytes AFTER a rotation that
       "kept last 20k lines". The rotation rule was written when diary lines
       were ~1KB (its own comment says "20k lines (~20MB)"). Lines are now
       ~9KB (voice/A2/memory stamps + the notes), so 20k lines = ~187MB --
       STILL over the 50MB trigger -> the file rotated EVERY MINUTE: a full
       ~187MB read + ~187MB write inside STEP 2, every cycle (~11 GB/hour of
       disk churn on a 90%-full drive), landing exactly on the latency spikes
       (analyze 2982ms, total 4120ms vs 500ms target).
       NEW LAW: after the line cap, cap BY BYTES -- while the result is over
       40MB, halve the kept lines (floor 2000). One bounded rotation; the
       per-day mirror files (diary_YYYYMMDD.jsonl) keep full history.

  FIX 2 (O16) FAST LANE VISIBILITY -- the console was the last blind surface:
       no POWER voice_gain, no memory state, no doorman cost/next-wake. One
       compact read-only line per cycle in the PIPELINE SUMMARY:
         FAST LANE (opsfix)  POWER DOWN voice_gain=3 memory=appended | doorman: 4 paid call(s) today, resting 2 min
       (At 17:24 the doorman said SELL @ 55.0% and POWER said DOWN with all
       six checks passing - and the console never showed either organ's state.
       That blindness ends with this line.)

NOT in this pack (deliberately - they touch the trading brain and need
tonight's data + a design review, per the owner's own law):
  * O14 the never-sleeping confidence brake (code receipt already in hand:
    step2: `if news.impact_level == "HIGH": confidence *= 0.9` - no distance
    check, while the boost rule says ">120 min = normal day")
  * the Scout-door question (why Shooting found no door on the DOWN side)

Safety laws (same as every install in this repo):
  * run from the project root only (the folder with main.py)
  * refuses on ANY anchor mismatch: prints your file's lines, writes NOTHING
  * idempotent: [skip] when already applied
  * backup main.py.pre_opsfix (written once, never staged)
  * py_compile after the edit; a failed check restores the backup (fail-closed)
  * --rig proves the install with real checks; --rollback restores

Run from the project root:
    python apply_ops_fix.py             # install (expect [ok] main.py: 2 edit(s))
    python apply_ops_fix.py --rig       # prove it (markers + compile + behavior)
    python apply_ops_fix.py --rollback  # put the .pre_opsfix copy back

The robot should be RESTARTED after installing (main.py loads at startup).
Then push (never the .pre_* backup):
    git add main.py apply_ops_fix.py
"""
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

ROOT = Path.cwd()
BACKUP_SUFFIX = ".pre_opsfix"

# ------------------------------------------------------------------ FIX 1 --
ROT_OLD = '''        _append_diary(record, hist_path)
        # v5.0: rotate snapshots_history if >50MB to prevent big file crash
        try:
            if hist_path.stat().st_size > 50*1024*1024:
                # Keep last 20k lines (~20MB)
                lines = hist_path.read_text(encoding="utf-8").splitlines()[-20000:]
                hist_path.write_text("\\n".join(lines) + "\\n", encoding="utf-8")
                logger.info(f"Rotated snapshots_history.jsonl >50MB -> kept last 20k")
        except:
            pass
'''

ROT_NEW = '''        _append_diary(record, hist_path)
        # opsfix:v1-start (2026-10-15) -- byte-capped rotation.
        # The old rule kept the last 20k lines assuming ~1KB lines (~20MB, per
        # its own comment). Diary lines are now ~9KB (voice/A2/memory stamps +
        # notes), so 20k lines = ~187MB -- STILL over the 50MB trigger, so the
        # file rotated EVERY minute (a ~187MB read+write inside STEP 2 every
        # cycle; receipt 2026-10-15: 187,056,257 bytes on disk). New law: after
        # the line cap, cap BY BYTES -- while the result is over 40MB, halve
        # the kept lines (floor 2000). One bounded rotation; the per-day
        # mirrors (diary_YYYYMMDD.jsonl) keep the full history for grading.
        try:
            if hist_path.stat().st_size > 50*1024*1024:
                lines = hist_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-20000:]
                while len(lines) > 2000:
                    _nbytes = sum(len(_l) + 1 for _l in lines)
                    if _nbytes <= 40*1024*1024:
                        break
                    lines = lines[len(lines) // 2:]
                hist_path.write_text("\\n".join(lines) + "\\n", encoding="utf-8")
                logger.info(f"Rotated snapshots_history.jsonl >50MB -> kept {len(lines)} lines "
                            f"(byte-capped, opsfix v1)")
        except:
            pass
        # opsfix:v1-end
'''

# ------------------------------------------------------------------ FIX 2 --
MTL_OLD = '''    path = write_signal(snapshot, decision)
    if path is not None:
        _record("MT5 SIGNAL BRIDGE", f"OK -> {path.name}")
    else:
        _record("MT5 SIGNAL BRIDGE", "SKIPPED (write failed)")
'''

MTL_NEW = '''    path = write_signal(snapshot, decision)
    if path is not None:
        _record("MT5 SIGNAL BRIDGE", f"OK -> {path.name}")
    else:
        _record("MT5 SIGNAL BRIDGE", "SKIPPED (write failed)")

    # opsfix:v1 (2026-10-15) -- FAST LANE visibility: the console was the last
    # blind surface (no POWER voice/memory, no doorman cost or next wake;
    # ledger O16). One compact read-only line per cycle. It reports state only;
    # it changes nothing. Fail-closed: if anything is missing, the line says so
    # and the cycle is untouched.
    try:
        import time as _fl_time
        _pv = getattr(snapshot, "power_v2", None) or {}
        _vg = _pv.get("voice_gain")
        _vg_s = f"{_vg:g}" if isinstance(_vg, (int, float)) else "n/a"
        _ms = _pv.get("memory_status") or {}
        _ms_s = "appended" if _ms.get("appended") else str(_ms.get("reason") or "not")
        _pd = str(_pv.get("direction") or "n/a")
        _calls = _ai_calls_today()
        if _LAST_AI_CALL:
            _rest = (config.AI_MIN_INTERVAL_MINUTES * 60.0
                     - (_fl_time.time() - _LAST_AI_CALL)) / 60.0
            _dm_s = (f"resting {max(0.0, _rest):.0f} min" if _rest > 0
                     else "ready")
        else:
            _dm_s = "not called yet today"
        _record("FAST LANE (opsfix)",
                f"POWER {_pd} voice_gain={_vg_s} memory={_ms_s} | "
                f"doorman: {_calls} paid call(s) today, {_dm_s}")
    except Exception as _fl_err:
        _record("FAST LANE (opsfix)",
                f"(status unavailable: {type(_fl_err).__name__})")
'''


# ------------------------------------------------------------------ engine --
def py_ok(path: Path) -> bool:
    try:
        subprocess.run([sys.executable, "-m", "py_compile", str(path)],
                       check=True, capture_output=True)
        return True
    except Exception:
        return False


def install():
    if not (ROOT / "main.py").exists():
        print("ERROR: run this from the project root (the folder with main.py)")
        return 1
    path = ROOT / "main.py"
    src = path.read_text(encoding="utf-8", errors="surrogateescape")
    if "opsfix:v1-start" in src:
        print("[skip] main.py: ops fix v1 already present")
        return 0
    failures = []
    for name, anchor, repl in (("byte-capped rotation (F20)", ROT_OLD, ROT_NEW),
                               ("FAST LANE visibility line (O16)", MTL_OLD, MTL_NEW)):
        if src.count(anchor) != 1:
            failures.append(name)
            print(f"[STOP] anchor not found for: {name}")
            print("       wanted (first line): " + anchor.strip().splitlines()[0][:90])
            continue
        src = src.replace(anchor, repl, 1)
    if failures:
        print("[STOP] NOTHING written - your main.py differs from the expected build.")
        print("       Paste this whole output to the assistant. (The robot is untouched.)")
        return 1
    bak = Path(str(path) + BACKUP_SUFFIX)
    if not bak.exists():
        shutil.copy2(path, bak)
    path.write_text(src, encoding="utf-8", errors="surrogateescape")
    if not py_ok(path):
        shutil.copy2(bak, path)
        print("[FAIL] py_compile failed after edit -> backup restored, nothing changed")
        return 1
    print(f"[ok] main.py: 2 edits applied (F20 rotation + O16 fast-lane line), "
          f"py_compile OK, backup {bak.name}")
    print("\nNext: prove it ->   python apply_ops_fix.py --rig")
    print("Then RESTART the robot (main.py loads at startup), and push:")
    print("  git add main.py apply_ops_fix.py")
    return 0


def rig():
    print("=" * 78)
    print("OPS FIX v1 RIG - proving the install with real checks")
    print("=" * 78)
    results = []

    def check(name, cond):
        results.append((name, bool(cond)))
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")

    main = ROOT / "main.py"
    src = main.read_text(encoding="utf-8", errors="ignore") if main.exists() else ""
    check("marker: byte-capped rotation present", "opsfix:v1-start" in src and "opsfix:v1-end" in src)
    check("marker: FAST LANE line present", "FAST LANE (opsfix)" in src)
    check("py_compile main.py", py_ok(main))
    check("the old 'kept last 20k' treadmill is gone",
          "kept last 20k" not in src and "byte-capped, opsfix v1" in src)

    # behavioral test 1: a fat file (60MB, ~9KB lines) gets capped under 41MB
    # by RUNNING THE REAL SHIPPED BLOCK (extracted verbatim between the markers)
    import tempfile, os
    import logging
    try:
        block = src.split("opsfix:v1-start", 1)[1].split("opsfix:v1-end", 1)[0]
        code = textwrap.dedent("\n".join(block.splitlines()[1:]))  # drop the marker comment line
        logs = []
        fake_logger = type("L", (), {"info": staticmethod(lambda m, *a: logs.append(m))})()
        with tempfile.TemporaryDirectory() as td:
            big = Path(td) / "snapshots_history.jsonl"
            line = '{"i":%d,"pad":"%s"}'
            with open(big, "w", encoding="utf-8") as f:
                for i in range(6500):
                    f.write((line % (i, "x" * 9200)) + "\n")
            size_before = big.stat().st_size
            last_before = big.read_text(encoding="utf-8").splitlines()[-1]
            ns = {"hist_path": big, "logger": fake_logger, "__name__": "opsfix_test"}
            exec(code, ns)
            size_after = big.stat().st_size
            lines_after = big.read_text(encoding="utf-8").splitlines()
            check(f"behavior: 60.4MB fat file capped ({size_before//1048576}MB -> {size_after//1048576}MB, "
                  f"{len(lines_after)} lines)",
                  size_after < 41 * 1024 * 1024 and len(lines_after) >= 2000)
            check("behavior: the newest record survives the cap",
                  lines_after[-1] == last_before)
            check("behavior: the rotation logged the byte-capped message",
                  any("byte-capped" in m for m in logs))
        # behavioral test 2: a small file is left completely alone
        with tempfile.TemporaryDirectory() as td:
            small = Path(td) / "snapshots_history.jsonl"
            small.write_text("\n".join(f'{{"i":{i}}}' for i in range(1000)) + "\n",
                             encoding="utf-8")
            before = small.read_text(encoding="utf-8")
            n_logs_before = len(logs)
            ns = {"hist_path": small, "logger": fake_logger, "__name__": "opsfix_test"}
            exec(code, ns)
            check("behavior: a 29KB file is untouched (no needless rotation)",
                  small.read_text(encoding="utf-8") == before and len(logs) == n_logs_before)
    except Exception as e:
        check(f"behavioral tests (error: {type(e).__name__}: {e})", False)

    print("-" * 78)
    npass = sum(1 for _, c in results if c)
    if npass == len(results):
        print(f"RIG VERDICT: ALL PASS ({npass}/{len(results)}) - ops fix v1 is proven.")
        print("Next: restart the robot, then push:  git add main.py apply_ops_fix.py")
        return 0
    print(f"RIG VERDICT: {len(results) - npass} FAILED - paste this whole output to the assistant.")
    return 1


def rollback():
    p = ROOT / "main.py"
    bak = Path(str(p) + BACKUP_SUFFIX)
    if bak.exists():
        shutil.copy2(bak, p)
        print(f"[back] main.py restored from {bak.name}")
    else:
        print(f"[ --  ] main.py: no {BACKUP_SUFFIX} backup")
    print("rollback done. RESTART the robot if it is running.")
    return 0


def main():
    if "--rig" in sys.argv:
        return rig()
    if "--rollback" in sys.argv:
        return rollback()
    return install()


if __name__ == "__main__":
    sys.exit(main())
