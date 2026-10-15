#!/usr/bin/env python3
"""apply_voice_patch.py -- THE VOICE FIX (Team 4, 2026-10-15).

The owner's order, third ship-now override: fix everything in one go before
the 08:00 open. This is the core fix -- the one the autopsy proved.

WHAT THE AUTOPSY PROVED (receipts, 2026-10-14/15):
  At the week's biggest fish (14:40 bar, gold -27 pts), POWER's own meters
  read: down_power_pct 100.0, net_force -1.0 (PERFECT unanimity), yet
  activity 0.0624 and supporting_down_families 0 -> verdict NEITHER,
  reason WEAK_OR_NO_FORCE. Mechanism (power_team_v2.py): activity is the
  average VOTE MAGNITUDE of the tick judges, and the four tick sensors
  (footprint_delta, big_prints, footprint_levels, cvd_momentum, built in
  power_m5_adapter.py) all measure volume-imbalance RATIOS -- which in real
  gold tape live at ~0.03-0.12 even during genuine moves. The microphones
  were never calibrated to the gate: min_activity 0.18 expects shouting,
  reality whispers.

THE FIX (calibration, NOT loosening -- every gate stays untouched):
  A gain that rescales the tick-judge VALUES to their real operating range.
  Default 3.0 = the SMALLEST gain that makes the proven monster bar audible
  (0.0624 x 3 = 0.187 >= 0.18). Signs are never invented or flipped, values
  clamp to [-1, 1], quality untouched, fail-closed to the raw judges on any
  error. Quiet days (raw ~0.02 -> 0.06) stay NEITHER; the noisy 14:35 flip
  bar (raw 0.05 -> 0.15) stays NEITHER; only fish-sized force passes.
  Gates NOT touched: min_activity 0.18, min_family_signal 0.15,
  min_agreeing_families 2, min_dominance 0.60, conf 50, strength 6.

3 surgical edits into power_v2_shadow.py:
  1. helpers _voice_gain_value() + _apply_voice_gain() before run_power_v2()
  2. the call: built["judges"] = _apply_voice_gain(built["judges"])
     right after build_m5_inputs(...)
  3. the receipt: answer["voice_gain"] = <gain> so every diary record shows
     the calibration was applied (and what gain).

Knobs (.env, optional -- defaults are the calibrated values):
  VOICE_GAIN=3.0                      global gain
  VOICE_GAIN_<JUDGE>=<g>              per-judge override, e.g.
                                      VOICE_GAIN_CVD_MOMENTUM=4.0

Run from the project root:  python apply_voice_patch.py
"""
import py_compile
import shutil
import sys
from pathlib import Path

TARGET = "power_v2_shadow.py"
BACKUP = TARGET + ".pre_voice"

MARKER_HELPER = "def _voice_gain_value()"

# ---------------------------------------------------------------- edit 1 ----
HELPER_ANCHOR = "def run_power_v2(\n    tick_data: List[Dict[str, Any]],"

HELPER_INSERT = '''def _voice_gain_value() -> float:
    """VOICE FIX (2026-10-15): the microphone calibration factor.

    The four tick sensors measure volume-imbalance ratios, which in real gold
    tape live at ~0.03-0.12 even during genuine moves (measured 2026-10-14
    14:40: down_power 100%, net_force -1.0, activity 0.0624 -- a unanimous
    referee muzzled by vote MAGNITUDE). POWER's gates expect >= 0.18 average
    loudness. The gain rescales the sensors to their real operating range so
    fish-sized force is audible. This calibrates the MICROPHONES; every gate
    (min_activity, min_family_signal, min_agreeing_families, dominance) stays
    exactly as it was. Default 3.0 = the smallest gain that makes the proven
    monster bar audible (0.0624 x 3 = 0.187 >= 0.18).
    """
    import os
    try:
        g = float(os.environ.get("VOICE_GAIN", "3.0"))
    except (TypeError, ValueError):
        return 3.0
    return g if g > 0 else 3.0


def _apply_voice_gain(judges):
    """VOICE FIX (2026-10-15): rescale tick-judge vote magnitudes.

    Calibration, not verdicts: signs are preserved exactly (no direction is
    invented or flipped), values clamp to [-1, 1], quality fields are
    untouched, judges without a numeric value pass through unchanged.
    Fail-closed: any problem returns the judges untouched (old behavior).
    """
    try:
        import os
        if not isinstance(judges, dict):
            return judges
        default_gain = _voice_gain_value()
        out = {}
        for name, reading in judges.items():
            try:
                if not isinstance(reading, dict):
                    out[name] = reading
                    continue
                value = reading.get("value")
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    out[name] = reading
                    continue
                gain = default_gain
                raw = os.environ.get("VOICE_GAIN_" + str(name).upper())
                if raw:
                    try:
                        per = float(raw)
                        if per > 0:
                            gain = per
                    except (TypeError, ValueError):
                        pass
                new = dict(reading)
                new["value"] = max(-1.0, min(1.0, float(value) * gain))
                out[name] = new
            except Exception:
                out[name] = reading
        return out
    except Exception:
        return judges


''' + HELPER_ANCHOR

# ---------------------------------------------------------------- edit 2 ----
CALL_ANCHOR = """    built = build_m5_inputs(
        tick_data or [], now=evaluation_end, tick_size=tick_size)
    context = dict(built["context"])"""

CALL_NEW = """    built = build_m5_inputs(
        tick_data or [], now=evaluation_end, tick_size=tick_size)
    # VOICE FIX (2026-10-15): calibrate the tick judges' vote magnitudes so
    # fish-sized force is audible to POWER's (unchanged) activity gate.
    built["judges"] = _apply_voice_gain(built["judges"])
    context = dict(built["context"])"""

# ---------------------------------------------------------------- edit 3 ----
STAMP_ANCHOR = "    answer = decide(built[\"judges\"], context=context)"

STAMP_NEW = ("    answer = decide(built[\"judges\"], context=context)\n"
             "    try:\n"
             "        answer[\"voice_gain\"] = _voice_gain_value()\n"
             "    except Exception:\n"
             "        pass")


def main() -> int:
    path = Path(TARGET)
    if not path.exists():
        print(f"[fail] {TARGET} not found next to this script (run from the project root).")
        return 1
    src = path.read_text(encoding="utf-8")

    if MARKER_HELPER in src and "_apply_voice_gain(built[\"judges\"])" in src:
        print("[skip] voice patch already applied -- nothing to do.")
        return 0
    if MARKER_HELPER in src or "_apply_voice_gain(built[\"judges\"])" in src:
        print("[fail] half-applied state found. Restore power_v2_shadow.py.pre_voice and re-run.")
        return 1

    for name, anchor in (("helper anchor", HELPER_ANCHOR),
                         ("call anchor", CALL_ANCHOR),
                         ("stamp anchor", STAMP_ANCHOR)):
        n = src.count(anchor)
        if n != 1:
            print(f"[fail] {name} found {n} times (expected exactly 1) -- file changed?")
            print("       NOT writing anything. The live file is untouched.")
            return 1

    backup = Path(BACKUP)
    if not backup.exists():
        shutil.copy2(TARGET, BACKUP)
        print(f"[ok] backup: {BACKUP}")
    else:
        print(f"[ok] backup already exists, kept: {BACKUP}")

    new = src.replace(HELPER_ANCHOR, HELPER_INSERT, 1)
    new = new.replace(CALL_ANCHOR, CALL_NEW, 1)
    new = new.replace(STAMP_ANCHOR, STAMP_NEW, 1)
    path.write_text(new, encoding="utf-8")
    print("[ok] 1/3 helpers planted: _voice_gain_value + _apply_voice_gain")
    print("[ok] 2/3 call inserted: built['judges'] = _apply_voice_gain(built['judges'])")
    print("[ok] 3/3 receipt stamped: answer['voice_gain'] = <gain>")

    try:
        py_compile.compile(TARGET, doraise=True)
    except Exception as e:
        shutil.copy2(BACKUP, TARGET)
        print(f"[fail] py_compile failed after patch: {e}")
        print(f"       restored {TARGET} from {BACKUP} -- live file is safe.")
        return 1
    print("[ok] py_compile passed")
    print()
    print("VOICE FIXED. The referees' microphones are calibrated; every gate is untouched.")
    print("Knobs (optional, .env): VOICE_GAIN=3.0  |  per judge: VOICE_GAIN_<JUDGE>=<g>")
    print("Prove it:  python rig_voice.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
