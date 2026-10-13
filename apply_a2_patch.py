#!/usr/bin/env python3
"""apply_a2_patch.py -- WIRE THE NOW BUTTON (A2) -- v1.2 (2026-10-13).

v1.2 (after the owner's second live run revealed the truth): the live brain
has been wired to the v1 trigger since 2026-10-06 (apply_aggressive_patches.py
"Full Cycle" -- P1A/P1B wiring + P2 passive-off). v1 never fired because its
flow eye read a field that does not exist (buy_pct vs the real buying_pressure)
-- the TRUE zero-trades root cause, one level deeper than "unwired". The v2
trigger file (already copied by the owner) fixes the blindness. This patcher
now wraps the LIVE v1 wiring in the A2 rails instead of looking for the old
hard-coded False pair:

  step2_market_analysis.py (2 surgical inserts, anchored on the v1 wiring's
  verbatim text -- apply_aggressive_patches.py's own P1A/P1B blocks):
    1. the A2 gate import, right after the v1 trigger call's except block
    2. the two stamp lines become the gated stamp: passthrough when the lane
       is disabled (identical behavior), three-eyes + caps when enabled, and
       a fire sets a2_burst=True which activates the burst bracket in the
       (already-installed) shooting patch. Counting happens in the gate.

  shooting_team.py (only if not already patched -- the owner's file is):
    3. burst bracket: fixed SL/TP (A2_BURST_SL_PTS / A2_BURST_TP_PTS, default
       1.8/1.8 pts), stop_source "A2_BURST_FIXED".
    4. A2's own R:R floor (A2_MIN_RR, default 1.0) for burst plans only.
    5. a confirmed burst forces the AGGRESSIVE_MARKET style.

Safety laws: refuses to patch twice ([skip] when already done); refuses on ANY
anchor mismatch (nothing written; prints your file's actual lines); .pre_a2
backups; py_compile after; fail-closed (gate import failure -> passthrough to
the untouched v1 stamp). The lane is OFF until .env sets
AGGRESSIVE_ENTRY_ENABLED=1. ALLOW_LIVE_TRADING stays 0. Demo only.

Run from the project root:  python apply_a2_patch.py
"""
import os
import re
import shutil
import sys

TARGET_STEP2 = "step2_market_analysis.py"
TARGET_SHOOT = "shooting_team.py"

# ---- step2: anchors = apply_aggressive_patches.py's OWN inserted text ------- #

# P1B's except block (the tail of the v1 trigger call, verbatim from the
# installer that built the live file)
STEP2_G1_ANCHOR = '''        except Exception as _ag_err:
            _aggr = {"confirmed": False,
                     "reason": "aggressive trigger error (fail-closed): "
                               + type(_ag_err).__name__}
'''
STEP2_G1_INSERT = STEP2_G1_ANCHOR + '''        # A2 (2026-10-13): the gate import. Missing module -> passthrough below.
        try:
            from aggressive_trigger import a2_v1_gate as _a2_gate
        except Exception:
            _a2_gate = None
'''

# P1A's stamp block (verbatim from the installer)
STEP2_G2_ANCHOR = '''            # Aggressive impulse trigger (aggressive_trigger.py) - reviewed
            # 2026-10-06, fail-closed: fires only with AGGRESSIVE_ENTRY_ENABLED=1.
            "aggressive_trigger_confirmed": bool(_aggr.get("confirmed")),
            "aggressive_trigger_reason": str(_aggr.get("reason") or ""),'''
STEP2_G2_INSERT = '''            # Aggressive impulse trigger (aggressive_trigger.py) - reviewed
            # 2026-10-06, fail-closed: fires only with AGGRESSIVE_ENTRY_ENABLED=1.
            # A2 (2026-10-13): gated -- three measured eyes + caps + burst
            # bracket; passthrough (identical behavior) when lane disabled.
            **(_a2_gate(_aggr, _power_v2, order_flow, locals().get("direction"),
                        now, config, notes) if _a2_gate is not None else
               {"aggressive_trigger_confirmed": bool(_aggr.get("confirmed")),
                "aggressive_trigger_reason": str(_aggr.get("reason") or "")}),'''

V1_WIRING_MARKER = "evaluate_aggressive_trigger as _aggr_eval"

# ---- shooting_team edits (unchanged from v1.0/v1.1) ------------------------- #

SHOOT_S1_ANCHOR = "    aggressive = _geometry(aggressive_entry, direction, target_door, aggressive_shelter, a, sp, config)"
SHOOT_S1_INSERT = SHOOT_S1_ANCHOR + '''
    if ctx.get("a2_burst") is True:
        # A2 burst bracket: measured fixed geometry (anatomy 3-day: p90 heat
        # ~1.6, targets +1.4..+2.0 by 9-12 min). Never wall-based for bursts.
        _a2_sl = _num(_cfg(config, "A2_BURST_SL_PTS", 1.8))
        _a2_tp = _num(_cfg(config, "A2_BURST_TP_PTS", 1.8))
        if _a2_sl and _a2_sl > 0 and _a2_tp and _a2_tp > 0:
            _a2_stop = aggressive_entry - _a2_sl if is_buy else aggressive_entry + _a2_sl
            _a2_target = aggressive_entry + _a2_tp if is_buy else aggressive_entry - _a2_tp
            aggressive = {**aggressive, "eligible": True, "reason": None,
                          "stop": _a2_stop, "target": _a2_target,
                          "risk": _a2_sl, "reward": _a2_tp,
                          "rr": _a2_tp / _a2_sl, "stop_source": "A2_BURST_FIXED"}'''

SHOOT_S2_ANCHOR = '    min_rr = _cfg(config, "SHOOT_MIN_RR", 1.2)'
SHOOT_S2_INSERT = ('    min_rr = (_cfg(config, "A2_MIN_RR", 1.0) if ctx.get("a2_burst") is True\n'
                   '              else _cfg(config, "SHOOT_MIN_RR", 1.2))')

SHOOT_S3_ANCHOR = "    choose_aggressive = aggressive_allowed and (queue_blocks_limit or not passive_allowed)"
SHOOT_S3_INSERT = ("    choose_aggressive = aggressive_allowed and (queue_blocks_limit"
                   " or not passive_allowed\n                                                   "
                   "  or ctx.get(\"a2_burst\") is True)")


def _show_context(path, needle, around=2, max_hits=6):
    """Print the actual lines around every hit -- one paste is always enough."""
    try:
        lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
    except OSError:
        print(f"   (cannot read {path})")
        return
    hits = [i for i, l in enumerate(lines) if needle in l]
    if not hits:
        print(f"   ({path} never mentions {needle!r})")
        return
    print(f"   --- diagnostic: lines mentioning {needle!r} in {path} ---")
    shown = set()
    for i in hits[:max_hits]:
        for j in range(max(0, i - around), min(len(lines), i + around + 1)):
            if j not in shown:
                print(f"   {j + 1:5d}: {lines[j]}")
                shown.add(j)
        print("        ...")


def _compile_ok(tmp):
    import py_compile
    try:
        py_compile.compile(tmp, doraise=True)
        return None
    except Exception as e:
        return str(e)


def patch_step2(path):
    if not os.path.exists(path):
        print(f"[abort] {path} not found (run from the project root)")
        return None
    src = open(path, encoding="utf-8").read()
    if "A2 rails" in src or "a2_v1_gate" in src:
        print(f"[skip] {path} already carries the A2 gate -- nothing to do")
        return "already"
    if "A1 BAR SERVICE" not in src:
        print(f"[abort] {path} lacks the A1 bar service -- install A1 first; NOTHING was written")
        return None
    if V1_WIRING_MARKER not in src:
        print(f"[abort] {path} lacks the v1 aggressive wiring "
              f"({V1_WIRING_MARKER!r}). Install apply_aggressive_patches.py "
              "(the 2026-10-06 Full Cycle) first; NOTHING was written. Your handwriting:")
        _show_context(path, "aggressive_trigger")
        return None
    edits = [
        ("v1 except-block anchor (gate import)", STEP2_G1_ANCHOR, STEP2_G1_INSERT),
        ("v1 stamp anchor (gated stamp)", STEP2_G2_ANCHOR, STEP2_G2_INSERT),
    ]
    for name, anchor, _ins in edits:
        n = src.count(anchor)
        if n != 1:
            print(f"[abort] {path}: anchor '{name}' found {n}x (expected 1x) -- "
                  "your file differs; NOTHING was written. Your handwriting:")
            _show_context(path, "_ag_err" if "except" in name else "Aggressive impulse trigger")
            return None
    for _n, anchor, insert in edits:
        src = src.replace(anchor, insert, 1)
    tmp = path + ".a2_tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(src)
    err = _compile_ok(tmp)
    if err:
        os.remove(tmp)
        print(f"[abort] {path}: patched file does not compile: {err}; NOTHING was written")
        return None
    shutil.copy(path, path + ".pre_a2")
    os.replace(tmp, path)
    print(f"[ok] {path}: 2 edits applied (gate import + gated stamp; "
          f"backup: {path}.pre_a2)")
    return "patched"


def patch_shoot(path):
    if not os.path.exists(path):
        print(f"[abort] {path} not found (run from the project root)")
        return None
    src = open(path, encoding="utf-8").read()
    if "A2_BURST_FIXED" in src:
        print(f"[skip] {path} already carries the A2 burst bracket -- nothing to do")
        return "already"
    edits = [
        ("aggressive geometry anchor", SHOOT_S1_ANCHOR, SHOOT_S1_INSERT),
        ("min_rr anchor", SHOOT_S2_ANCHOR, SHOOT_S2_INSERT),
        ("choose_aggressive anchor", SHOOT_S3_ANCHOR, SHOOT_S3_INSERT),
    ]
    for name, anchor, _ins in edits:
        n = src.count(anchor)
        if n != 1:
            print(f"[abort] {path}: anchor '{name}' found {n}x (expected 1x) -- "
                  "your file differs; NOTHING was written. Your handwriting:")
            _show_context(path, "aggressive = _geometry" if "geometry" in name else
                          ("min_rr" if "min_rr" in name else "choose_aggressive"))
            return None
    for _n, anchor, insert in edits:
        src = src.replace(anchor, insert, 1)
    tmp = path + ".a2_tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(src)
    err = _compile_ok(tmp)
    if err:
        os.remove(tmp)
        print(f"[abort] {path}: patched file does not compile: {err}; NOTHING was written")
        return None
    shutil.copy(path, path + ".pre_a2")
    os.replace(tmp, path)
    print(f"[ok] {path}: 3 edits applied (backup: {path}.pre_a2)")
    return "patched"


def main():
    r1 = patch_step2(TARGET_STEP2)
    r2 = patch_shoot(TARGET_SHOOT)
    if r1 is None or r2 is None:
        print("[INCOMPLETE] one target refused -- nothing half-applied is "
              "active; re-run after fixing (paste the diagnostic)")
        sys.exit(1)
    print()
    print("A2 WIRED." if "patched" in (r1, r2) else "A2 ALREADY IN PLACE.")
    print("The lane is OFF until .env sets AGGRESSIVE_ENTRY_ENABLED=1.")
    print("Prove it:   python rig_a2.py")
    print("Then edit .env (owner's hand), restart the app, and the first burst can fire.")


if __name__ == "__main__":
    main()
