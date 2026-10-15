#!/usr/bin/env python3
"""rig_voice.py -- the test rig for THE VOICE FIX (v1.1, 2026-10-15).

v1.1: install-order independent. Accepts a project root that already carries
the voice patch: a patcher answering [skip] (already applied) counts as
healthy (same fix pattern as rig_memory v1.1). Fixes the V1 assumption bug
that cascaded KeyError('dir') into V2-V9 on an already-patched machine.

9 checks. The heart is V3, THE MONSTER TEST: it reconstructs the exact
judges POWER measured at the 2026-10-14 14:40 monster bar (all four tick
sensors at -0.0624, quality 1.0 -- reproducing the live dump's coverage
0.79, activity 0.0624, supporting 0, verdict NEITHER / WEAK_OR_NO_FORCE),
then proves the voice fix flips that SAME bar to DOWN while every gate
stays at its original value. V4/V5 prove the discrimination survives:
quiet days and the noisy 14:35 flip bar stay NEITHER.

Runs on a scratch COPY of the real files; your live files are never touched.
Run from the project root (next to power_v2_shadow.py, power_m5_adapter.py,
power_team_v2.py):   python rig_voice.py
"""
import importlib.util
import os
import py_compile
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHADOW = HERE / "power_v2_shadow.py"
ADAPTER = HERE / "power_m5_adapter.py"
TEAM = HERE / "power_team_v2.py"
APPLY = HERE / "apply_voice_patch.py"

# The 2026-10-14 14:40 monster bar, exactly as POWER measured it:
# down_power 100%, net_force -1.0, activity 0.0624, supporting_down 0.
MONSTER = {
    "footprint_delta": {"value": -0.0624, "quality": 1.0},
    "cvd_momentum": {"value": -0.0624, "quality": 1.0},
    "big_prints": {"value": -0.0624, "quality": 1.0},
    "footprint_levels": {"value": -0.0624, "quality": 1.0},
}
QUIET = {k: {"value": 0.02 if k != "footprint_delta" else -0.02, "quality": 1.0}
         for k in MONSTER}
FLIP_BAR = {k: {"value": 0.05, "quality": 1.0} for k in MONSTER}   # the 14:35 noise
UP_FISH = {k: {"value": 0.08, "quality": 1.0} for k in MONSTER}
CONTEXT = {"market_regime": "TREND", "breakout_confirmed": False,
           "high_impact_news": False, "feed_stale": False, "data_quality_ok": True}

WORK = None
STATE = {}
RESULTS = []


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def v1_patch_applies():
    d = WORK / "patched"
    d.mkdir(parents=True, exist_ok=True)
    for f in (SHADOW, ADAPTER, TEAM):
        shutil.copy2(f, d / f.name)
    r = subprocess.run([sys.executable, str(APPLY)], cwd=d, capture_output=True, text=True)
    expect(r.returncode == 0, f"patcher exit {r.returncode}: {r.stdout} {r.stderr}")
    # v1.1: the rig may run on a root that already carries the patch (the README
    # install order patches the live file BEFORE the owner re-runs the rig).
    # A patcher answering [skip] (already applied) is just as healthy as [ok].
    expect(("[ok]" in r.stdout) or ("[skip]" in r.stdout),
           f"patcher neither applied nor skipped cleanly: {r.stdout}")
    # The real guarantee: the copied file carries the patch either way.
    src = (d / "power_v2_shadow.py").read_text(encoding="utf-8")
    expect("def _apply_voice_gain" in src and "built[\"judges\"] = _apply_voice_gain" in src,
           "the copied file must carry the voice patch (applied now or already present)")
    if "[ok]" in r.stdout:
        expect((d / "power_v2_shadow.py.pre_voice").exists(), "no backup made on fresh apply")
    py_compile.compile(str(d / "power_v2_shadow.py"), doraise=True)
    STATE["dir"] = d


def _mods():
    if "shadow" not in STATE:
        STATE["shadow"] = _load("pv_shadow_patched", STATE["dir"] / "power_v2_shadow.py")
        STATE["team"] = _load("pv_team_patched", STATE["dir"] / "power_team_v2.py")
    return STATE["shadow"], STATE["team"]


def v2_gain_math():
    shadow, _ = _mods()
    j = {"a": {"value": -0.0624, "quality": 0.9},
         "b": {"value": 0.9, "quality": 0.5},
         "c": {"value": 0.0, "quality": 0.3},
         "d": {"value": "not-a-number", "quality": 0.3},
         "e": None}
    out = shadow._apply_voice_gain(j)
    expect(abs(out["a"]["value"] + 0.1872) < 1e-9, f"a: {out['a']['value']}")
    expect(out["a"]["quality"] == 0.9, "quality must not change")
    expect(out["b"]["value"] == 1.0, f"b clamp: {out['b']['value']}")
    expect(out["c"]["value"] == 0.0, "zero must stay zero")
    expect(out["d"]["value"] == "not-a-number", "non-numeric passes through")
    expect(out["e"] is None, "non-dict passes through")
    old = os.environ.get("VOICE_GAIN")
    try:
        os.environ["VOICE_GAIN"] = "5"
        out5 = shadow._apply_voice_gain({"a": {"value": -0.1, "quality": 1.0}})
        expect(abs(out5["a"]["value"] + 0.5) < 1e-9, f"VOICE_GAIN=5: {out5['a']['value']}")
        os.environ["VOICE_GAIN"] = "abc"
        expect(shadow._voice_gain_value() == 3.0, "garbage VOICE_GAIN must fall back to 3.0")
        os.environ["VOICE_GAIN"] = "5"
        os.environ["VOICE_GAIN_CVD_MOMENTUM"] = "2"
        outp = shadow._apply_voice_gain({"cvd_momentum": {"value": -0.1, "quality": 1.0},
                                         "x": {"value": -0.1, "quality": 1.0}})
        expect(abs(outp["cvd_momentum"]["value"] + 0.2) < 1e-9, "per-judge override")
        expect(abs(outp["x"]["value"] + 0.5) < 1e-9, "global still applies to others")
    finally:
        os.environ.pop("VOICE_GAIN", None)
        os.environ.pop("VOICE_GAIN_CVD_MOMENTUM", None)
        if old is not None:
            os.environ["VOICE_GAIN"] = old


def v3_monster_test():
    shadow, team = _mods()
    raw = team.decide(MONSTER, context=CONTEXT)
    expect(raw["direction"] == "NEITHER", f"bug not reproduced: {raw['direction']}")
    expect(raw["reason_code"] == "WEAK_OR_NO_FORCE", f"reason: {raw['reason_code']}")
    expect(abs(raw["activity"] - 0.0624) < 0.002, f"activity {raw['activity']} != 0.0624")
    expect(raw["supporting_down_families"] == 0, "expected 0 supporting (the muzzle)")
    loud = shadow._apply_voice_gain(MONSTER)
    fixed = team.decide(loud, context=CONTEXT)
    expect(fixed["direction"] == "DOWN", f"monster must be DOWN, got {fixed['direction']}")
    expect(fixed["activity"] >= 0.18, f"activity {fixed['activity']} < 0.18")
    expect(fixed["supporting_down_families"] >= 2,
           f"supporting {fixed['supporting_down_families']} < 2")
    expect(fixed["down_power_pct"] >= 60.0, "dominance")


def v4_quiet_stays_neither():
    shadow, team = _mods()
    fixed = team.decide(shadow._apply_voice_gain(QUIET), context=CONTEXT)
    expect(fixed["direction"] == "NEITHER", f"quiet day must stay NEITHER: {fixed['direction']}")


def v5_flip_bar_stays_muzzled():
    shadow, team = _mods()
    fixed = team.decide(shadow._apply_voice_gain(FLIP_BAR), context=CONTEXT)
    expect(fixed["direction"] == "NEITHER",
           f"the 14:35-style noise bar must stay NEITHER: {fixed['direction']}")


def v6_up_symmetry():
    shadow, team = _mods()
    fixed = team.decide(shadow._apply_voice_gain(UP_FISH), context=CONTEXT)
    expect(fixed["direction"] == "UP", f"up fish must be UP: {fixed['direction']}")


def v7_double_apply():
    target = STATE["dir"] / "power_v2_shadow.py"
    before = target.read_bytes()
    r = subprocess.run([sys.executable, str(APPLY)], cwd=STATE["dir"],
                       capture_output=True, text=True)
    expect(r.returncode == 0 and "[skip]" in r.stdout, f"double apply: {r.stdout}")
    expect(target.read_bytes() == before, "double apply changed the file!")


def v8_fail_closed():
    shadow, _ = _mods()
    garbage = {"ok": {"value": -0.05, "quality": 1.0}, "weird": object(), "none": None}
    out = shadow._apply_voice_gain(garbage)
    expect(abs(out["ok"]["value"] + 0.15) < 1e-9, "valid entry must still be amplified")
    expect(shadow._apply_voice_gain(None) is None, "None must pass through")
    expect(shadow._apply_voice_gain("junk") == "junk", "non-dict must pass through")


def v9_stamp_present():
    src = (STATE["dir"] / "power_v2_shadow.py").read_text(encoding="utf-8")
    expect("answer[\"voice_gain\"] = _voice_gain_value()" in src,
           "the diary receipt stamp is missing")
    expect("VOICE FIX (2026-10-15)" in src, "the comment receipt is missing")


CHECKS = [
    ("V1  patch applies clean on a copy (+backup, py_compile)", v1_patch_applies),
    ("V2  gain math: scale, clamp, zero, quality, env knobs", v2_gain_math),
    ("V3  MONSTER TEST: 14:40 bar NEITHER -> DOWN, gates untouched", v3_monster_test),
    ("V4  quiet day (±0.02) stays NEITHER (discrimination)", v4_quiet_stays_neither),
    ("V5  the 14:35-style noise bar (0.05) stays NEITHER", v5_flip_bar_stays_muzzled),
    ("V6  up-fish symmetry (+0.08 -> UP)", v6_up_symmetry),
    ("V7  double-apply -> [skip], file unchanged", v7_double_apply),
    ("V8  fail-closed on garbage inputs (no crash, pass-through)", v8_fail_closed),
    ("V9  diary receipt stamp (answer['voice_gain']) present", v9_stamp_present),
]


def main() -> int:
    global WORK
    print("=" * 78)
    print(" VOICE FIX RIG -- the referees' microphones, calibrated")
    print("=" * 78)
    missing = [p.name for p in (SHADOW, ADAPTER, TEAM, APPLY) if not p.exists()]
    if missing:
        print(f" [fail] missing files next to this rig: {', '.join(missing)}")
        print("        Run it from the project root with the pack files present.")
        return 1
    WORK = Path(tempfile.mkdtemp(prefix="rig_voice_"))
    for name, fn in CHECKS:
        try:
            fn()
            RESULTS.append(True)
            print(f"  PASS  {name}")
        except Exception as e:
            RESULTS.append(False)
            print(f"  FAIL  {name}")
            print(f"        {type(e).__name__}: {e}")
    print("-" * 78)
    n = len(RESULTS)
    if all(RESULTS):
        print(f" ALL PASS ({n}/{n}) -- the voice fix is safe to install.")
        return 0
    print(f" FAILURES: {RESULTS.count(False)}/{n} -- do NOT install. Scratch kept: {WORK}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
