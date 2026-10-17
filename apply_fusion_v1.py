#!/usr/bin/env python3
"""apply_fusion_v1.py - FUSION v1 "POWER-LEAD" install (Option B) + TIMEFRAME unification.

Owner-ordered 2026-10-17. One design, two halves:

  HALF 1 - THE FUSION GATE (power-lead v1)
    When the AI doorman is quiet and POWER speaks with a dominant force reason,
    POWER may carry the trade if the classroom's confidence >= POWER_LEAD_CONF_FLOOR
    (default 30, was the AI gate 45) AND the classroom's direction (when it has
    one) agrees with POWER. Evidence: council_shadow on 10-15 + 10-16 - the old
    floor passed 0 minutes both days; floor 30 + agreement graded 88-100%.
    Every carried trade is stamped: decision.model="power-lead-v1" (countable in
    the decisions log ai_model column, night-report visible) + rationale receipt
    + a "STEP 3  POWER-LEAD" line in the loop console.
    Off-switch: POWER_LEAD_ENABLED=0 in .env restores the old 45-only behavior.

  HALF 2 - ONE TIMEFRAME KNOB
    TIMEFRAME in .env (e.g. M3) now drives POWER's bar clock too:
    TIMEFRAME -> BASE_TF_MINUTES -> POWER_BAR_SECONDS (180 for M3), overridable
    directly via POWER_BAR_SECONDS. The classroom already follows
    A1_BASE_TF_MINUTES; set it to match TIMEFRAME (M3 -> 3). POWER's adapter,
    boundary math, and regime ladder all take the bar length from config now.

Usage:
    python apply_fusion_v1.py            # install (writes .fusionv1.bak backups)
    python apply_fusion_v1.py --rollback # restore originals from backups

Never stage the .bak files. Demo only. Fail-closed everywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKUP_SUFFIX = ".fusionv1.bak"
MARKER = "fusionv1"

PATCHES = [
    # ------------------------------------------------------------------ config
    {
        "file": "config.py",
        "anchor": '    g["POWER_M5_TICK_SIZE"] = _ffloat("POWER_M5_TICK_SIZE", 0.0)\n',
        "insert_after_anchor": True,
        "patch": '''
    # fusionv1 (2026-10-17, owner-ordered): one timeframe knob drives POWER.
    # TIMEFRAME (M1..H1) -> BASE_TF_MINUTES -> POWER_BAR_SECONDS; the explicit
    # POWER_BAR_SECONDS env overrides. The classroom follows A1_BASE_TF_MINUTES
    # (set it to match: M3 -> A1_BASE_TF_MINUTES=3).
    _tf_minutes = {"M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5, "M6": 6,
                   "M10": 10, "M12": 12, "M15": 15, "M20": 20, "M30": 30,
                   "H1": 60}
    g["BASE_TF_MINUTES"] = _tf_minutes.get(str(g["TIMEFRAME"]).upper(), 5)
    g["POWER_BAR_SECONDS"] = int(_ffloat(
        "POWER_BAR_SECONDS", 60.0 * g["BASE_TF_MINUTES"]))
    # fusionv1: the power-lead advisory (Option B) - POWER speaks + panel floor
    # + agreement. Evidence: council_shadow 10-15/10-16 (floor 45 passed 0 both
    # days; floor 30 + agreement graded 88-100%). Off-switch below.
    g["POWER_LEAD_ENABLED"] = _fget("POWER_LEAD_ENABLED", "1") == "1"
    g["POWER_LEAD_CONF_FLOOR"] = _ffloat("POWER_LEAD_CONF_FLOOR", 30.0)
''',
        "marker": 'g["POWER_LEAD_CONF_FLOOR"]',
    },
    # ------------------------------------------------------------------ regime
    {
        "file": "power_v2_regime.py",
        "anchor": "BAR_SECONDS = 300\n",
        "replacement": '''BAR_SECONDS = 300  # default M5; fusionv1: set_bar_seconds() re-times the ladder


def set_bar_seconds(seconds: int) -> None:
    """fusionv1: re-time the regime ladder (TIMEFRAME from .env).

    Sets the module bar length used by bucketing, contiguity and gap math.
    History bars backfilled from the (currently empty) probe CSV assume 300s
    buckets; at other lengths the contiguity check refuses them and the ladder
    fails closed to live-bar warm-up, which is the safe behavior.
    """
    global BAR_SECONDS
    seconds = int(seconds)
    if seconds < 60:
        raise ValueError("bar seconds must be >= 60")
    BAR_SECONDS = seconds
''',
        "marker": "def set_bar_seconds(",
    },
    # ------------------------------------------------------------------ adapter
    {
        "file": "power_m5_adapter.py",
        "anchor": "    min_big_print_size: float = 1.0,\n",
        "replacement": """    min_big_print_size: float = 1.0,
    bar_seconds: int = 300,  # fusionv1: TIMEFRAME-driven window (M3 -> 180)
""",
        "marker": "bar_seconds: int = 300,",
    },
    {
        "file": "power_m5_adapter.py",
        "anchor": """    current_start = current_time - timedelta(minutes=5)
    previous_start = current_time - timedelta(minutes=10)
""",
        "replacement": """    # fusionv1: window length follows POWER_BAR_SECONDS (was hardcoded 5 min).
    _bar_s = max(60, int(bar_seconds))
    current_start = current_time - timedelta(seconds=_bar_s)
    previous_start = current_time - timedelta(seconds=2 * _bar_s)
""",
        "marker": "_bar_s = max(60, int(bar_seconds))",
    },
    {
        "file": "power_m5_adapter.py",
        "anchor": '            "timeframe": "M5",\n',
        "replacement": '            "timeframe": f"M{max(1, int(bar_seconds) // 60)}",  # fusionv1\n',
        "marker": 'f"M{max(1, int(bar_seconds) // 60)}"',
    },
    # ------------------------------------------------------------------ shadow
    {
        "file": "power_v2_shadow.py",
        "anchor": "def last_completed_m5_end(now: Any, grace_seconds: float = 5.0) -> datetime:\n    \"\"\"Return the latest M5 boundary safely past its close grace period.\"\"\"\n",
        "replacement": """def last_completed_m5_end(now: Any, grace_seconds: float = 5.0,
                         bar_seconds: int = 300) -> datetime:
    \"\"\"Return the latest bar boundary safely past its close grace period.

    fusionv1: the bar length follows POWER_BAR_SECONDS (was hardcoded 300s).
    \"\"\"
""",
        "marker": "bar_seconds: int = 300) -> datetime:",
    },
    {
        "file": "power_v2_shadow.py",
        "anchor": "    boundary = math.floor((epoch - grace) / 300.0) * 300.0\n",
        "replacement": """    _bar = float(max(60, int(bar_seconds)))  # fusionv1
    boundary = math.floor((epoch - grace) / _bar) * _bar
""",
        "marker": "_bar = float(max(60, int(bar_seconds)))",
    },
    {
        "file": "power_v2_shadow.py",
        "anchor": """    grace_seconds: float = 5.0,
) -> Dict[str, Any]:
    \"\"\"Evaluate the active v2 Power contract from Step 2 market data.
""",
        "replacement": """    grace_seconds: float = 5.0,
    bar_seconds: int = 300,  # fusionv1: TIMEFRAME-driven bar length
) -> Dict[str, Any]:
    \"\"\"Evaluate the active v2 Power contract from Step 2 market data.
""",
        "marker": "bar_seconds: int = 300,  # fusionv1: TIMEFRAME-driven bar length",
    },
    {
        "file": "power_v2_shadow.py",
        "anchor": """    bar_end = last_completed_m5_end(now, grace_seconds=grace_seconds)
""",
        "replacement": """    bar_end = last_completed_m5_end(now, grace_seconds=grace_seconds,
                                    bar_seconds=bar_seconds)
    tf_label = f"M{max(1, int(bar_seconds) // 60)}"  # fusionv1
""",
        "marker": 'tf_label = f"M{max(1, int(bar_seconds) // 60)}"',
    },
    {
        "file": "power_v2_shadow.py",
        "anchor": """    built = build_m5_inputs(
        tick_data or [], now=evaluation_end, tick_size=tick_size)
""",
        "replacement": """    built = build_m5_inputs(
        tick_data or [], now=evaluation_end, tick_size=tick_size,
        bar_seconds=bar_seconds)  # fusionv1
""",
        "marker": "bar_seconds=bar_seconds)  # fusionv1",
    },
    {
        "file": "power_v2_shadow.py",
        "anchor": '    event_id = f"{symbol.strip()}|M5|{bar_end.isoformat()}|FINAL"\n',
        "replacement": '    event_id = f"{symbol.strip()}|{tf_label}|{bar_end.isoformat()}|FINAL"  # fusionv1\n',
        "marker": '|{tf_label}|{bar_end.isoformat()}|FINAL"',
    },
    {
        "file": "power_v2_shadow.py",
        "anchor": """    answer.update({
        "shadow_only": False,
        "bar_end_utc": bar_end.isoformat().replace("+00:00", "Z"),
""",
        "replacement": """    answer.update({
        "shadow_only": False,
        "timeframe": tf_label,  # fusionv1: overrides decide()'s static M5 label
        "bar_end_utc": bar_end.isoformat().replace("+00:00", "Z"),
""",
        "marker": '"timeframe": tf_label,  # fusionv1: overrides decide',
    },
    # ------------------------------------------------------------------ step2
    {
        "file": "step2_market_analysis.py",
        "anchor": "        from power_v2_regime import classify_m5_regime\n",
        "replacement": """        from power_v2_regime import classify_m5_regime
        from power_v2_regime import set_bar_seconds as _p2_set_bar_seconds
        # fusionv1: the regime ladder follows TIMEFRAME from .env.
        try:
            _p2_set_bar_seconds(int(getattr(config, "POWER_BAR_SECONDS", 300) or 300))
        except Exception:
            pass  # fail closed to the module default (M5)
""",
        "marker": "_p2_set_bar_seconds(int(getattr(config, \"POWER_BAR_SECONDS\", 300) or 300))",
    },
    {
        "file": "step2_market_analysis.py",
        "anchor": '            tick_size=float(getattr(config, "POWER_M5_TICK_SIZE", 0.0) or 0.0),\n',
        "replacement": """            tick_size=float(getattr(config, "POWER_M5_TICK_SIZE", 0.0) or 0.0),
            bar_seconds=int(getattr(config, "POWER_BAR_SECONDS", 300) or 300),  # fusionv1
""",
        "marker": 'bar_seconds=int(getattr(config, "POWER_BAR_SECONDS", 300) or 300),',
    },
    {
        "file": "step2_market_analysis.py",
        "anchor": '            "team": "POWER", "version": "2.2.0", "timeframe": "M5",\n',
        "replacement": """            "team": "POWER", "version": "2.2.0",
            "timeframe": f"M{max(1, int(getattr(config, 'POWER_BAR_SECONDS', 300) or 300) // 60)}",  # fusionv1
""",
        "marker": 'f"M{max(1, int(getattr(config, \'POWER_BAR_SECONDS\', 300) or 300) // 60)}"',
    },
    # ------------------------------------------------------------------ step3
    {
        "file": "step3_ai_decision.py",
        "anchor": "    SYSTEM_PROMPT = (\n        \"You are Captain 5 of Gold-BookMap v7.0 M5 scalper. 4 other captains vote: \"",
        "replacement": (
            "    try:  # fusionv1: the Captain's frame follows TIMEFRAME from .env\n"
            "        import config as _cfg\n"
            "        _TF_LABEL = str(getattr(_cfg, \"TIMEFRAME\", \"M5\") or \"M5\").upper()\n"
            "    except Exception:\n"
            "        _TF_LABEL = \"M5\"\n"
            "    SYSTEM_PROMPT = (\n"
            "        f\"You are Captain 5 of Gold-BookMap v7.0 {_TF_LABEL} scalper. "
            "4 other captains vote: \""
        ),
        "marker": "_TF_LABEL = str(getattr(_cfg,",
    },
    # ------------------------------------------------------------------ main
    {
        "file": "main.py",
        "anchor": """            if _dom and _rc == _dom:
                _pc = float(getattr(snapshot, "confidence", 0.0) or 0.0)
                if _pc >= float(getattr(config, "AI_CONFIDENCE_THRESHOLD", 50.0)):
                    decision.action = "BUY" if _pd == "UP" else "SELL"
                    decision.confidence = _pc
                    action = decision.action
""",
        "replacement": """            if _dom and _rc == _dom:
                _pc = float(getattr(snapshot, "confidence", 0.0) or 0.0)
                # fusionv1 "power-lead" (2026-10-17, owner-ordered, Option B):
                # POWER speaks + panel floor (POWER_LEAD_CONF_FLOOR, default 30)
                # + agreement with the classroom's direction. Evidence 10-15 and
                # 10-16 (council_shadow): the old 45 floor passed 0 minutes both
                # days; floor 30 + agreement graded 88-100%. The old
                # >=AI_CONFIDENCE_THRESHOLD path is kept unchanged; the lead
                # only EXTENDS coverage below it, fail-closed. Off-switch:
                # POWER_LEAD_ENABLED=0 in .env.
                _old_gate = float(getattr(config, "AI_CONFIDENCE_THRESHOLD", 50.0))
                _floor = float(getattr(config, "POWER_LEAD_CONF_FLOOR", 30.0))
                _lead_on = bool(getattr(config, "POWER_LEAD_ENABLED", True))
                _cdir = str(getattr(snapshot, "signal_direction", "") or "").upper()
                _agree = _cdir not in ("BUY", "SELL") or \\
                    _cdir == ("BUY" if _pd == "UP" else "SELL")
                _passed = _pc >= _old_gate
                _via_lead = False
                if not _passed and _lead_on and _pc >= _floor and _agree:
                    _passed = True
                    _via_lead = True
                if _passed:
                    decision.action = "BUY" if _pd == "UP" else "SELL"
                    decision.confidence = _pc
                    if _via_lead:
                        decision.model = "power-lead-v1"
                        decision.rationale = (
                            (getattr(decision, "rationale", "") or "AI quiet")
                            + " | power-lead v1: conf {:.0f} >= floor {:.0f}, "
                            "agree={}, POWER {} ({})".format(
                                _pc, _floor, _agree, _pd, _rc))
                        try:
                            _record("STEP 3  POWER-LEAD",
                                    "fusion v1 carries the trade: conf {:.0f} "
                                    "(floor {:.0f}, agree={}) | POWER {} | {}"
                                    .format(_pc, _floor, _agree, _pd, _rc))
                        except Exception:
                            pass
                    action = decision.action
""",
        "marker": 'decision.model = "power-lead-v1"',
    },
]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="")


def apply_all() -> int:
    changed = []
    for spec in PATCHES:
        path = ROOT / spec["file"]
        if not path.is_file():
            print(f"ERROR: {spec['file']} not found")
            return 1
        text = read(path)
        if spec["marker"] in text:
            print(f"  SKIP (already patched): {spec['file']}")
            continue
        anchor = spec["anchor"]
        if anchor not in text:
            print(f"ERROR: anchor not found in {spec['file']}:")
            print("   " + anchor[:90].replace("\n", "\\n"))
            return 1
        backup = path.with_name(path.name + BACKUP_SUFFIX)
        if not backup.exists():
            write(backup, text)
        if "replacement" in spec:
            new = text.replace(anchor, spec["replacement"], 1)
        else:
            new = text.replace(
                anchor, anchor + spec["patch"], 1)
        if spec["marker"] not in new:
            print(f"ERROR: marker missing after patch in {spec['file']}")
            return 1
        write(path, new)
        changed.append(spec["file"])
        print(f"  PATCHED: {spec['file']}")
    print()
    print(f"fusionv1 install complete: {len(changed)} patch(es) applied, "
          f"{len(PATCHES) - len(changed)} already present.")
    print("Backups: *.fusionv1.bak (never stage them).")
    print("New .env knobs (all optional):")
    print("  TIMEFRAME=M3              (drives POWER bar length; M3 -> 180s)")
    print("  POWER_BAR_SECONDS=180     (explicit override)")
    print("  POWER_LEAD_ENABLED=1      (off-switch: 0 restores old 45-only)")
    print("  POWER_LEAD_CONF_FLOOR=30  (the fusion floor)")
    print("Also set A1_BASE_TF_MINUTES=3 to match TIMEFRAME=M3 (classroom).")
    return 0


def rollback() -> int:
    restored = 0
    for spec in PATCHES:
        path = ROOT / spec["file"]
        backup = path.with_name(path.name + BACKUP_SUFFIX)
        if backup.is_file():
            write(path, read(backup))
            print(f"  RESTORED: {spec['file']}")
            restored += 1
    print(f"rollback complete: {restored} file(s) restored.")
    return 0


def main() -> int:
    if "--rollback" in sys.argv:
        return rollback()
    return apply_all()


if __name__ == "__main__":
    sys.exit(main())
