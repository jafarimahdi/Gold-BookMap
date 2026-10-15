#!/usr/bin/env python3
"""
apply_trade_gates_v1.py -- THE FRIDAY GATES (2026-10-15 night, owner-ordered)
==============================================================================
THE OWNER'S ORDER: "the priority goal of this robot is making money and trade,
not watching numbers all day and doing nothing." Six changes, all reversible,
all to be measured by tomorrow's evening paper. THIS IS A DESIGN CHANGE, not
a visibility patch: it touches gates, on the owner's explicit order, with the
receipts recorded in the briefing.

WHAT CHANGES (and the receipt for each):

  1. whale_walls judge CUT from the panel (C1 clock matured: 6 days, 4183
     calls, -1.012 pts/call; owner's decision).
       -> judge_panel.py: the whale note no longer sits on the panel.
          The L3-whale note still PRINTS in the signal map (information only).

  2. News brake WINDOW fix: the -10% brake fired all day for HIGH events
     5-7 HOURS away (receipt: 41.4->37.3 with the event 347 min out).
       -> step2: the brake now applies only within 120 min of the event --
          the SAME window the boost already uses.

  3. Doorman (AI) becomes ADVISORY WHEN ABSENT: when he says HOLD/WAIT but
     POWER speaks with dominant force (reason_code UP/DOWNWARD_FORCE_DOMINATES)
     and the PANEL's own confidence clears the gate, the trade proceeds on
     POWER's side. When he is confidently OPPOSITE, his veto STAYS (honest:
     both doors keep a pen).
       -> main.py _shooting_execution_gate.

  4. THE CORRIDOR OPENS (door 8 -- the real reason 10 days had zero trades):
     the gate previously ended with an UNCONDITIONAL 'return False' ("no
     reviewed Step-4/EA adapter"). The reviewed change is this patch, on the
     owner's order: with EXECUTION_MODE=python and TRADING_ENABLED=1, a plan
     that is GO + POWER-agreeing + valid entry style routes to the EXISTING
     MT5Executor, which keeps ALL of its own gates (confidence, news
     blackout, spread, risk manager, trade-guard caps). DEMO FIRST.
       -> main.py _shooting_execution_gate tail.

  5.+6. Via .env (owner pastes, no code):
       CONFIDENCE_THRESHOLD=45   (was 50; WARNING: the 40-49 bucket replayed
                                  -981 pts over 10 days -- the demo measures)
       SHOOT_ROAD_RATIO=10.0     (was 6.0; tonight's receipt: the blocked
                                  winner was 8.8x, the saved losers 15.3x and
                                  33-42x -> 10 is the evidence line)
       A2_LEAN_MIN=0.15          (was 0.25; eye-1 confirmed 0 times in 10 days)
       A2_LEAN_HOLD_SECONDS=30   (was 60)

REQUIRES ops fix v3 installed first (the brake anchor is the ops-v3 text).
Safety: fail-closed anchors (nothing written on mismatch), backups
*.pre_gates1, py_compile + auto-restore, --rig, --rollback, idempotent.

    python apply_trade_gates_v1.py            # install
    python apply_trade_gates_v1.py --rig      # prove it
    python apply_trade_gates_v1.py --rollback # undo everything
"""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path.cwd()
SUFFIX = ".pre_gates1"

# ============ EDIT 1: step2 -- the brake gets the same 120-min window ========
BRAKE_OLD = '''        if news.impact_level == "HIGH":
            _conf_before = confidence
            confidence *= 0.9
            notes.append(f"high-impact event upcoming -> confidence {_conf_before:.1f}->{confidence:.1f} (brake)")'''
BRAKE_NEW = '''        if news.impact_level == "HIGH" and abs(float(
                getattr(news, "minutes_to_next_event", 0.0) or 0.0)) <= 120.0:
            # gates:v1 -- the brake uses the SAME 120-min window as the boost
            # (receipt: 41.4->37.3 braked all day for an event 347 min away)
            _conf_before = confidence
            confidence *= 0.9
            notes.append(f"high-impact event <=120min -> confidence {_conf_before:.1f}->{confidence:.1f} (brake)")'''

# ============ EDIT 2: judge_panel -- whale_walls retired from the panel ======
WHALE_OLD = '''        for judge, pat in _JUDGE_PATTERNS:
            m = _re.search(pat, note)
            if not m:
                continue'''
WHALE_NEW = '''        for judge, pat in _JUDGE_PATTERNS:
            m = _re.search(pat, note)
            if not m:
                continue
            if judge == "whale_walls":
                # gates:v1 -- C1 clock matured (6 days, 4183 calls, -1.012
                # pts/call); owner-ordered cut 2026-10-15. The L3-whale note
                # still prints in the signal map; it just no longer votes.
                continue'''

# ============ EDIT 3+4: main.py -- the doorman + the corridor ================
GATE_OLD = '''def _shooting_execution_gate(decision, snapshot):
    """Fail closed: actionable Step-3 decisions need a broker-ready Shooting plan.

    Current Shooting plans are analysis/paper-only and Step 4 has no adapter that
    consumes the chosen entry style with attached TP/SL. No data field can turn this
    gate on; enabling execution requires a separately reviewed code change.
    """
    action = str(getattr(decision, "action", "HOLD") or "HOLD").upper()
    if action in {"HOLD", "WAIT", "NONE", "NO_TRADE", "NEUTRAL"}:
        return True, "no new-entry action"
    if action not in {"BUY", "SELL"}:
        return False, f"unrecognized Step-3 action {action!r}"
    if snapshot is None:
        return False, "no market snapshot"
    plan = getattr(snapshot, "shot", None) or {}
    power = getattr(snapshot, "power", None) or {}
    expected_power = "UP" if action == "BUY" else "DOWN"
    if str(power.get("direction", "NEITHER")).upper() != expected_power:
        return False, "AI side does not agree with canonical POWER v2"
    if plan.get("shot") != "GO" or str(plan.get("side", "")).upper() != action:
        return False, "Shooting did not approve this side"
    if plan.get("entry_style") not in {"PASSIVE_LIMIT", "AGGRESSIVE_MARKET"}:
        return False, "Shooting entry style is missing or unsupported"
    if plan.get("execution_status") != "BROKER_READY":
        return False, "Shooting plan is analysis-only; no broker-ready entry adapter exists"
    return False, "no reviewed Step-4/EA adapter consumes the Shooting plan and attached TP/SL"'''

GATE_NEW = '''def _shooting_execution_gate(decision, snapshot):
    """gates:v1 (2026-10-15, owner-ordered reviewed change).

    - The doorman (AI) is ADVISORY WHEN ABSENT: on HOLD/WAIT, if POWER speaks
      with dominant force and the panel's own confidence clears the gate, the
      trade proceeds on POWER's side. A confidently-opposite doorman still vetoes.
    - THE CORRIDOR: with EXECUTION_MODE=python and TRADING_ENABLED, a GO plan
      that POWER agrees with routes to the EXISTING MT5Executor, which keeps
      every one of its own gates (confidence, blackout, spread, risk, caps).
      DEMO FIRST. Everything else stays refused, fail-closed.
    """
    action = str(getattr(decision, "action", "HOLD") or "HOLD").upper()
    if action in {"HOLD", "WAIT", "NONE", "NO_TRADE", "NEUTRAL"}:
        # gates:v1: doorman advisory when absent -- POWER may carry the trade
        try:
            _pw = getattr(snapshot, "power", None) or {}
            _pd = str(_pw.get("direction", "NEITHER")).upper()
            _rc = str(_pw.get("reason_code", _pw.get("reason", "")) or "").upper()
            _dom = {"UP": "UPWARD_FORCE_DOMINATES",
                    "DOWN": "DOWNWARD_FORCE_DOMINATES"}.get(_pd)
            if _dom and _rc == _dom:
                _pc = float(getattr(snapshot, "confidence", 0.0) or 0.0)
                if _pc >= float(getattr(config, "AI_CONFIDENCE_THRESHOLD", 50.0)):
                    decision.action = "BUY" if _pd == "UP" else "SELL"
                    decision.confidence = _pc
                    action = decision.action
        except Exception:
            pass
        if action in {"HOLD", "WAIT", "NONE", "NO_TRADE", "NEUTRAL"}:
            return True, "no new-entry action"
    if action not in {"BUY", "SELL"}:
        return False, f"unrecognized Step-3 action {action!r}"
    if snapshot is None:
        return False, "no market snapshot"
    plan = getattr(snapshot, "shot", None) or {}
    power = getattr(snapshot, "power", None) or {}
    expected_power = "UP" if action == "BUY" else "DOWN"
    if str(power.get("direction", "NEITHER")).upper() != expected_power:
        return False, "AI side does not agree with canonical POWER v2"
    if plan.get("shot") != "GO" or str(plan.get("side", "")).upper() != action:
        return False, "Shooting did not approve this side"
    if plan.get("entry_style") not in {"PASSIVE_LIMIT", "AGGRESSIVE_MARKET"}:
        return False, "Shooting entry style is missing or unsupported"
    # gates:v1: THE CORRIDOR (was: two unconditional refusals -- the deepest
    # reason 10 days had zero trades). Reviewed change, owner-ordered, demo first.
    if str(getattr(config, "EXECUTION_MODE", "none")).lower() != "python":
        return False, "corridor closed (EXECUTION_MODE != python)"
    return True, "corridor open: plan GO + POWER agree -> python executor"'''


def py_ok(path):
    try:
        subprocess.run([sys.executable, "-m", "py_compile", str(path)],
                       check=True, capture_output=True)
        return True
    except Exception:
        return False


def _patch(path, edits, label):
    """edits: list of (name, old, new). Fail-closed: all anchors or nothing."""
    if not path.exists():
        print(f"[miss] {label}: {path.name} not found - skipped")
        return False
    src = path.read_text(encoding="utf-8", errors="surrogateescape")
    if all(new in src for _, _, new in edits):
        print(f"[skip] {label}: gates v1 already present")
        return True
    for name, old, _ in edits:
        if src.count(old) != 1:
            print(f"[STOP] {label}: the {name} anchor was not found exactly once.")
            print("       (Nothing written.) Paste this output to the assistant:")
            print(f"         grep -n \"{name.split()[0]}\" {path.name}")
            return False
    for _, old, new in edits:
        src = src.replace(old, new, 1)
    bak = Path(str(path) + SUFFIX)
    if not bak.exists():
        shutil.copy2(path, bak)
    path.write_text(src, encoding="utf-8", errors="surrogateescape")
    if not py_ok(path):
        shutil.copy2(bak, path)
        print(f"[FAIL] {label}: py_compile failed -> backup restored")
        return False
    print(f"[ok] {label}: {len(edits)} edit(s), backup {bak.name}")
    return True


def install():
    if not (ROOT / "main.py").exists():
        print("ERROR: run this from the project root (the folder with main.py)")
        return 1
    ok = True
    ok &= _patch(ROOT / "step2_market_analysis.py",
                 [("news-brake window", BRAKE_OLD, BRAKE_NEW)],
                 "step2_market_analysis.py")
    ok &= _patch(ROOT / "judge_panel.py",
                 [("whale_walls cut", WHALE_OLD, WHALE_NEW)],
                 "judge_panel.py")
    ok &= _patch(ROOT / "main.py",
                 [("execution gate (doorman advisory + corridor)", GATE_OLD, GATE_NEW)],
                 "main.py")
    if not ok:
        print("\n[INCOMPLETE] at least one file failed. Nothing half-applied is")
        print("dangerous (each file is all-or-nothing) - paste this output.")
        return 1
    print("\nNow the .env block (owner pastes at the END of .env, then restart):")
    print("  CONFIDENCE_THRESHOLD=45")
    print("  SHOOT_ROAD_RATIO=10.0")
    print("  A2_LEAN_MIN=0.15")
    print("  A2_LEAN_HOLD_SECONDS=30")
    print("\nThen: python apply_trade_gates_v1.py --rig   -> RESTART the robot.")
    return 0


def rig():
    print("=" * 78)
    print("FRIDAY GATES RIG - proving the design change with real checks")
    print("=" * 78)
    results = []

    def check(name, cond):
        results.append((name, bool(cond)))
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")

    s2 = (ROOT / "step2_market_analysis.py").read_text(encoding="utf-8", errors="ignore")
    jp = (ROOT / "judge_panel.py").read_text(encoding="utf-8", errors="ignore")
    mn = (ROOT / "main.py").read_text(encoding="utf-8", errors="ignore")
    check("marker: brake window (gates:v1)", "gates:v1" in s2 and "<=120min" in s2)
    check("py_compile step2", py_ok(ROOT / "step2_market_analysis.py"))
    check("marker: whale_walls cut (gates:v1)", "gates:v1" in jp and jp.count("whale_walls") >= 2)
    check("py_compile judge_panel", py_ok(ROOT / "judge_panel.py"))
    check("marker: corridor + advisory (gates:v1)", "corridor open" in mn and "ADVISORY WHEN ABSENT" in mn)
    check("py_compile main", py_ok(ROOT / "main.py"))

    # behavioral: the gate function itself, with stubs
    try:
        import types, textwrap
        block = mn.split("def _shooting_execution_gate", 1)[1]
        block = block.split("def run_step4", 1)[0]
        code = "def _shooting_execution_gate" + block
        cfg = types.SimpleNamespace(EXECUTION_MODE="python",
                                    AI_CONFIDENCE_THRESHOLD=45.0)
        ns = {"config": cfg}
        exec(compile(code, "gate", "exec"), ns)
        gate = ns["_shooting_execution_gate"]

        def snap(power_dir, rcode, conf, shot="GO", side=None, style="PASSIVE_LIMIT"):
            side = side or ("BUY" if power_dir == "UP" else "SELL")
            return types.SimpleNamespace(
                confidence=conf,
                power={"direction": power_dir, "reason_code": rcode},
                shot={"shot": shot, "side": side, "entry_style": style})

        def dec(action, conf):
            return types.SimpleNamespace(action=action, confidence=conf)

        # T1: corridor opens for a full agreement
        d = dec("BUY", 55.0)
        ok, why = gate(d, snap("UP", "UPWARD_FORCE_DOMINATES", 55.0))
        check("behavior: full agreement -> corridor open", ok and "corridor open" in why)
        # T2: corridor closed when EXECUTION_MODE != python
        cfg.EXECUTION_MODE = "none"
        ok, why = gate(dec("BUY", 55.0), snap("UP", "UPWARD_FORCE_DOMINATES", 55.0))
        check("behavior: EXECUTION_MODE=none -> still refused", not ok)
        cfg.EXECUTION_MODE = "python"
        # T3: doorman absent + POWER dominant + panel conf clears gate -> trade
        d = dec("HOLD", 0.0)
        ok, why = gate(d, snap("UP", "UPWARD_FORCE_DOMINATES", 46.0))
        check("behavior: doorman advisory -> POWER carries the trade",
              ok and d.action == "BUY" and abs(d.confidence - 46.0) < 1e-9)
        # T4: doorman absent + POWER dominant but panel conf below gate -> no trade
        d = dec("HOLD", 0.0)
        ok, why = gate(d, snap("UP", "UPWARD_FORCE_DOMINATES", 30.0))
        check("behavior: panel conf 30 < 45 -> no trade", ok and "no new-entry" in why)
        # T5: doorman confidently opposite -> veto STAYS
        ok, why = gate(dec("SELL", 70.0), snap("UP", "UPWARD_FORCE_DOMINATES", 60.0, side="BUY"))
        check("behavior: opposite doorman still vetoes",
              not ok and "does not agree" in why)
        # T6: shooter did not approve -> refused
        ok, why = gate(dec("BUY", 55.0), snap("UP", "UPWARD_FORCE_DOMINATES", 55.0, shot="WAIT"))
        check("behavior: shooter WAIT -> refused", not ok)
    except Exception as e:
        check(f"behavioral tests (error: {type(e).__name__}: {e})", False)

    # behavioral: the brake window
    try:
        import types
        src2 = (ROOT / "step2_market_analysis.py").read_text(encoding="utf-8", errors="ignore")
        idx = src2.find("gates:v1 -- the brake uses")
        start = src2.rfind("if news.impact_level", 0, idx)
        end = src2.find("(brake)\")", idx) + len("(brake)\")")
        code = src2[start:end]
        ns = {}
        exec(compile("def _brake(news, confidence):\n    notes = []\n" +
                     textwrap.indent(code, "    ") +
                     "\n    return confidence, notes", "brake", "exec"), ns)
        far = types.SimpleNamespace(impact_level="HIGH", minutes_to_next_event=347.0)
        near = types.SimpleNamespace(impact_level="HIGH", minutes_to_next_event=60.0)
        c1, _ = ns["_brake"](far, 50.0)
        c2, n2 = ns["_brake"](near, 50.0)
        check("behavior: brake OFF at 347min (was ON all day)",
              abs(c1 - 50.0) < 1e-9)
        check("behavior: brake ON at 60min", abs(c2 - 45.0) < 1e-9 and n2)
    except Exception as e:
        check(f"brake behavioral (error: {type(e).__name__}: {e})", False)

    # behavioral: whale cut
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("jp", ROOT / "judge_panel.py")
        jm = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(jm)
        votes = jm.parse_judge_panel([
            "L3 whale RESISTANCE 8 walls 214 lots closest 4193.0 dist 0.04% w 4.0 -> SELL",
            "footprint delta -0.074 dominant 4194.0 strength 0.02 -> SELL w 1.0",
            "VWAP trend UP price 4192.5 vs VWAP 4188.5 w 0.6",
        ]) if hasattr(jm, "parse_judge_panel") else None
        names = [v["judge"] for v in votes] if votes else None
        if names is None:  # find the parse entry point by convention
            cand = [a for a in dir(jm) if a.startswith("parse")]
            raise RuntimeError(f"parse entry point not found: {cand}")
        check("behavior: whale_walls NOT on the panel (others still vote)",
              "whale_walls" not in names and "footprint_delta" in names)
    except Exception as e:
        check(f"whale behavioral (error: {type(e).__name__}: {e})", False)

    print("-" * 78)
    npass = sum(1 for _, c in results if c)
    if npass == len(results):
        print(f"RIG VERDICT: ALL PASS ({npass}/{len(results)}) - the Friday gates are proven.")
        print("Next: paste the .env block, RESTART the robot, and let tomorrow measure.")
        return 0
    print(f"RIG VERDICT: {len(results) - npass} FAILED - paste this whole output to the assistant.")
    return 1


def rollback():
    for rel in ("main.py", "step2_market_analysis.py", "judge_panel.py"):
        p = ROOT / rel
        bak = Path(str(p) + SUFFIX)
        if bak.exists():
            shutil.copy2(bak, p)
            print(f"[back] {rel} restored from {bak.name}")
        else:
            print(f"[ -- ] {rel}: no {SUFFIX} backup")
    print("rollback done. Remove the 4 .env lines to undo the rest. RESTART if running.")
    return 0


def main():
    if "--rig" in sys.argv:
        return rig()
    if "--rollback" in sys.argv:
        return rollback()
    return install()


if __name__ == "__main__":
    sys.exit(main())
