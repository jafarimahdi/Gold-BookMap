#!/usr/bin/env python3
"""
apply_ops_fix2.py -- OPS FIX v2: the JUDGES line + FAST LANE wording fixes
===========================================================================
(2026-10-15 evening, owner's orders: "I want to see the judges in each cycle"
+ fix the two warts seen in the first live hours of the FAST LANE line.)

REQUIRES ops fix v1 (apply_ops_fix.py) to be installed first.

  FIX 1 (owner's order) THE JUDGES LINE -- one new line per cycle:
       JUDGES (opsfix2)  7 BUY / 5 SELL / 9 flat of 21 | top BUY: ... | top SELL: ...
       The panel votes are already computed each cycle (snapshot.judge_votes:
       {judge, dir, weight}); this line COUNTS them and names the heaviest
       buyers/sellers. Read-only; fail-closed; changes nothing.

  FIX 2 (F21a) "memory=memory disabled" -> "memory=off|appended|error X"
       (v1 printed the reason string after "memory=" - duplicated words.)

  FIX 3 (F21b) post-restart doorman wording: was "49 paid call(s) today,
       not called yet today" (file count vs new-process clock, both true,
       reads wrong). Now: "51 in file today; this process has not called yet".

NOT in this pack (unchanged law: brain surgery needs the design session):
  the doorman rest (owner changes it in .env: AI_MIN_INTERVAL_MINUTES=3),
  the RANGE-regime muteness (O20), the Scout doors, the brake (O14).

Safety: project root only; refuses on anchor mismatch (writes NOTHING);
idempotent ([skip]); backup main.py.pre_opsfix2; py_compile + auto-restore
on failure; --rig proves with real checks; --rollback restores v1 state.

Run from the project root:
    python apply_ops_fix2.py             # install (expect [ok] main.py: 1 edit)
    python apply_ops_fix2.py --rig       # prove it (markers + compile + behavior)
    python apply_ops_fix2.py --rollback  # back to ops-fix-v1 state
"""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path.cwd()
BACKUP_SUFFIX = ".pre_opsfix2"

# ---- the v1 FAST LANE block exactly as apply_ops_fix.py installs it --------
V1_BLOCK = '''    # opsfix:v1 (2026-10-15) -- FAST LANE visibility: the console was the last
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
                f"(status unavailable: {type(_fl_err).__name__})")'''

# ---- the v2 replacement ------------------------------------------------------
V2_BLOCK = '''    # opsfix:v2 (2026-10-15) -- FAST LANE visibility (v2 wording) + THE JUDGES
    # LINE (owner's order: see the judges work each cycle). Read-only, fail-
    # closed, changes nothing. v2 fixes: no "memory=memory ..." duplication;
    # clear doorman wording right after a restart; NEW JUDGES summary line.
    try:
        import time as _fl_time
        _pv = getattr(snapshot, "power_v2", None) or {}
        _vg = _pv.get("voice_gain")
        _vg_s = f"{_vg:g}" if isinstance(_vg, (int, float)) else "n/a"
        _ms = _pv.get("memory_status") or {}
        if _ms.get("appended"):
            _ms_s = "appended"
        elif _ms.get("error"):
            _ms_s = f"error {_ms.get('error')}"
        else:
            _ms_s = str(_ms.get("reason") or "off").replace("memory ", "", 1)
        _pd = str(_pv.get("direction") or "n/a")
        _calls = _ai_calls_today()
        if _LAST_AI_CALL:
            _rest = (config.AI_MIN_INTERVAL_MINUTES * 60.0
                     - (_fl_time.time() - _LAST_AI_CALL)) / 60.0
            _dm_s = (f"resting {max(0.0, _rest):.0f} min" if _rest > 0
                     else "ready")
            _doorman = f"{_calls} paid call(s) today, {_dm_s}"
        else:
            _doorman = f"{_calls} in file today; this process has not called yet"
        _record("FAST LANE (opsfix)",
                f"POWER {_pd} voice_gain={_vg_s} memory={_ms_s} | "
                f"doorman: {_doorman}")
    except Exception as _fl_err:
        _record("FAST LANE (opsfix)",
                f"(status unavailable: {type(_fl_err).__name__})")
    # opsfix:v2-judges-start -- the judges, one line per cycle
    # (panel votes: {judge, dir +1 BUY / -1 SELL / 0 flat, weight})
    try:
        _jv = list(getattr(snapshot, "judge_votes", None) or [])
        _jb = [v for v in _jv if isinstance(v, dict) and float(v.get("dir") or 0) > 0]
        _js = [v for v in _jv if isinstance(v, dict) and float(v.get("dir") or 0) < 0]
        _jf = len(_jv) - len(_jb) - len(_js)

        def _jtop(vs):
            vs = sorted(vs, key=lambda v: -float(v.get("weight") or 0.0))
            return ", ".join(f"{v.get('judge')} (w{float(v.get('weight') or 0.0):g})"
                             for v in vs[:2]) or "none"

        _record("JUDGES (opsfix2)",
                f"{len(_jb)} BUY / {len(_js)} SELL / {_jf} flat of {len(_jv)} | "
                f"top BUY: {_jtop(_jb)} | top SELL: {_jtop(_js)}")
    except Exception as _j_err:
        _record("JUDGES (opsfix2)", f"(status unavailable: {type(_j_err).__name__})")
    # opsfix:v2-judges-end'''


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
    if "opsfix:v2-judges-start" in src:
        print("[skip] main.py: ops fix v2 already present")
        return 0
    if "opsfix:v1-start" not in src:
        print("[STOP] ops fix v1 is not installed - run apply_ops_fix.py first.")
        print("       (Nothing was written.)")
        return 1
    if src.count(V1_BLOCK) != 1:
        print("[STOP] the ops-fix-v1 FAST LANE block was not found exactly once.")
        print("       Your main.py differs from the expected build - paste this output")
        print("       to the assistant. (Nothing was written.)")
        return 1
    src = src.replace(V1_BLOCK, V2_BLOCK, 1)
    bak = Path(str(path) + BACKUP_SUFFIX)
    if not bak.exists():
        shutil.copy2(path, bak)
    path.write_text(src, encoding="utf-8", errors="surrogateescape")
    if not py_ok(path):
        shutil.copy2(bak, path)
        print("[FAIL] py_compile failed after edit -> backup restored, nothing changed")
        return 1
    print(f"[ok] main.py: ops fix v2 applied (JUDGES line + FAST LANE wording), "
          f"py_compile OK, backup {bak.name}")
    print("\nNext: prove it ->   python apply_ops_fix2.py --rig")
    print("Then RESTART the robot, and push:  git add main.py apply_ops_fix2.py")
    return 0


def rig():
    print("=" * 78)
    print("OPS FIX v2 RIG - proving the install with real checks")
    print("=" * 78)
    results = []

    def check(name, cond):
        results.append((name, bool(cond)))
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")

    main = ROOT / "main.py"
    src = main.read_text(encoding="utf-8", errors="ignore") if main.exists() else ""
    check("marker: ops fix v2 present", "opsfix:v2-judges-start" in src and "opsfix:v2-judges-end" in src)
    check("marker: v1 rotation still intact", "opsfix:v1-start" in src)
    check("py_compile main.py", py_ok(main))
    check("F21a fix in source (no more 'memory=memory')",
          'str(_ms.get("reason") or "off").replace("memory ", "", 1)' in src)
    check("F21b fix in source (post-restart wording)",
          "this process has not called yet" in src)

    # behavioral: run the REAL shipped JUDGES block on a fake panel
    try:
        block = src.split("opsfix:v2-judges-start", 1)[1].split("opsfix:v2-judges-end", 1)[0]
        import textwrap
        code = textwrap.dedent("\n".join(block.splitlines()[1:]))  # drop marker, dedent
        import types
        recorded = []
        fake_snapshot = types.SimpleNamespace(judge_votes=[
            {"judge": "footprint_delta", "dir": 1.0, "weight": 0.8, "raw": "x"},
            {"judge": "l3_net_flow", "dir": -1.0, "weight": 1.0, "raw": "x"},
            {"judge": "vwap_trend", "dir": 1.0, "weight": 0.6, "raw": "x"},
            {"judge": "absorption", "dir": -1.0, "weight": 0.5, "raw": "x"},
            {"judge": "iceberg_noise", "dir": 0.0, "weight": 0.5, "raw": "x"},
            {"judge": "poc_position", "dir": -1.0, "weight": 0.4, "raw": "x"},
            {"judge": "cvd_momentum", "dir": 1.0, "weight": 0.5, "raw": "x"},
            "junk-non-dict",  # must be ignored, not crash
        ])
        ns = {"snapshot": fake_snapshot,
              "_record": lambda name, status: recorded.append((name, status))}
        exec(code, ns)
        line = recorded[-1][1]
        check(f"behavior: judges counted (line: {line[:60]}...)",
              "3 BUY / 3 SELL / 2 flat of 8" in line)
        _buy_part = line.split("top BUY: ")[1].split(" | top SELL: ")[0]
        _sell_part = line.split("top SELL: ")[1]
        check("behavior: heaviest voters named first within each side",
              _buy_part.index("footprint_delta") < _buy_part.index("vwap_trend")
              and _sell_part.index("l3_net_flow") < _sell_part.index("absorption"))
        # fail-closed: a broken snapshot must not raise
        try:
            ns2 = {"snapshot": types.SimpleNamespace(judge_votes=None),
                   "_record": lambda name, status: recorded.append((name, status))}
            exec(code, ns2)
            check("behavior: empty panel -> a line, no crash",
                  "0 BUY / 0 SELL" in recorded[-1][1])
        except Exception as e:
            check(f"behavior: empty panel (raised {type(e).__name__})", False)
    except Exception as e:
        check(f"behavioral tests (error: {type(e).__name__}: {e})", False)

    print("-" * 78)
    npass = sum(1 for _, c in results if c)
    if npass == len(results):
        print(f"RIG VERDICT: ALL PASS ({npass}/{len(results)}) - ops fix v2 is proven.")
        print("Next: restart the robot, then push:  git add main.py apply_ops_fix2.py")
        return 0
    print(f"RIG VERDICT: {len(results) - npass} FAILED - paste this whole output to the assistant.")
    return 1


def rollback():
    p = ROOT / "main.py"
    bak = Path(str(p) + BACKUP_SUFFIX)
    if bak.exists():
        shutil.copy2(bak, p)
        print(f"[back] main.py restored from {bak.name} (back to ops-fix-v1 state)")
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
