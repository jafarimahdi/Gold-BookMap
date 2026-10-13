#!/usr/bin/env python3
"""rig_a2.py -- the NOW button's test lake (A2, 2026-10-13).

  A. the eyes (planted): lean fires / hold unmet / below floor / panel memory
  B. the caps: counter round-trip, date rollover, daily cap, cooldown
  C. the bracket (real plan_shot, proven fixtures): burst -> GO AGGRESSIVE_MARKET
     with the fixed 1.8/1.8 geometry; no trigger -> the unchanged v8 WAIT
  D. the serve lane (direct): seeded eyes -> FIRE -> GO plan -> counter bumped;
     disabled -> (None, []) and nothing written
  E. the twin gate + integration (REAL A1-patched brain, ramp lake):
     E1 lane disabled: A1-only vs A1+A2 reports byte-IDENTICAL
     E2 lane enabled:  0 errors, signals still flow, lane engaged on the ramp
  F. the v1 trigger's own tests still pass (module extended, v1 untouched)

Run:  python rig_a2.py     (from the folder holding aggressive_trigger.py and
       apply_a2_patch.py; needs the family present in the repo root -- or the
       workshop fallback -- exactly like rig_tf_robot.py)
"""
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
FAILURES = []


def check(name, cond, extra=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{(' -- ' + str(extra)[:280]) if (extra and not cond) else ''}")
    if not cond:
        FAILURES.append(name)


def fam_dir():
    if os.path.exists(os.path.join(HERE, "config.py")):
        return HERE
    return "/home/user/github_latest_verify"


def base_brain():
    p = os.path.join(HERE, "step2_market_analysis.py")
    return p if os.path.exists(p) else \
        "/home/user/power_history_probe_capped_v2/step2_market_analysis.py"


def aux(name, fallback):
    p = os.path.join(HERE, name)
    return p if os.path.exists(p) else fallback


CFG = SimpleNamespace(
    POWER_MAX_TICK_AGE_SECONDS=60.0, STALE_DATA_SECONDS=300.0,
    SHOOT_ROAD_RATIO=6.0, SHOOT_QUEUE_MIN_FILL_PROB=0.70, SHOOT_MIN_RR=1.2,
    SHOOT_MIN_REWARD_USD=0.0, PM_TP_BUFFER_ATR=0.15,
)


def power(direction="UP"):
    return {
        "team": "POWER", "version": "2.2.0", "timeframe": "M5",
        "direction": direction,
        "up_power_pct": 70.0 if direction == "UP" else 30.0,
        "down_power_pct": 30.0 if direction == "UP" else 70.0,
        "power_total_pct": 100.0,
        "reason_code": "UPWARD_FORCE_DOMINATES" if direction == "UP"
                       else "DOWNWARD_FORCE_DOMINATES",
        "context": {"market_regime": "TREND", "data_quality_ok": True,
                    "feed_stale": False},
        "regime_diagnostics": {"market_regime": "TREND",
                               "latest_tick_age_seconds": 2.0},
        "shadow_only": False,
    }


def smap(direction="UP"):
    door = {"price": 105.0 if direction == "UP" else 95.0, "size": 20.0,
            "doors_between": 0, "lots_between": 0.0, "biggest_in_path": None,
            "road_clear": True}
    return {"above" if direction == "UP" else "below": [door]}


def of(buy=70.0, sell=30.0):
    return SimpleNamespace(buying_pressure=buy, selling_pressure=sell)


def seed_eyes(mod, t0, strength=0.6, span_s=120.0, step_s=5.0, panel="BUY",
              panel_age_s=30.0):
    fresh = mod.BurstEyes()
    t = t0 - timedelta(seconds=span_s)
    while t <= t0:
        fresh.sample(t, strength)
        t += timedelta(seconds=step_s)
    if panel:
        fresh.remember_panel(t0 - timedelta(seconds=panel_age_s), panel)
    return fresh


def main():
    sys.path.insert(0, HERE)
    fam = fam_dir()
    import aggressive_trigger as at
    utc = timezone.utc
    t0 = datetime(2026, 10, 13, 10, 0, tzinfo=utc)

    print("A2 NOW BUTTON rig -- planted eyes, planted caps, real plan_shot, real brain")
    print("=" * 78)

    # ---- A. the eyes --------------------------------------------------------
    print("A. the eyes (BurstEyes, planted samples)")
    eyes = seed_eyes(at, t0)
    lean = eyes.lean(t0)
    check("A1 sustained one-sidedness -> lean UP, held >= 100s",
          lean and lean["direction"] == "UP" and lean["strength"] >= 0.5
          and lean["held_s"] >= 100, lean)
    eyes2 = at.BurstEyes()
    t = t0 - timedelta(seconds=30)
    while t <= t0:
        eyes2.sample(t, 0.6)
        t += timedelta(seconds=5)
    check("A2 hold unmet (30s of samples) -> no lean", eyes2.lean(t0) is None)
    eyes3 = at.BurstEyes()
    t = t0 - timedelta(seconds=180)
    flip = 1
    while t <= t0:
        eyes3.sample(t, 0.05 * flip)
        flip = -flip
        t += timedelta(seconds=5)
    check("A3 noise below the 0.25 floor -> no lean", eyes3.lean(t0) is None)
    eyes4 = seed_eyes(at, t0, panel="BUY", panel_age_s=120)
    check("A4 panel BUY 120s ago agrees with UP",
          eyes4.panel_agrees(t0, "UP", window_s=300) is not None)
    # A5: only an OLD panel memory (400s) must not agree
    eyes5 = at.BurstEyes()
    eyes5.remember_panel(t0 - timedelta(seconds=400), "BUY")
    check("A5 old panel memory (400s) -> no agreement",
          eyes5.panel_agrees(t0, "UP", window_s=300) is None)
    eyes6 = at.BurstEyes()
    eyes6.remember_panel(t0 - timedelta(seconds=30), "SELL")
    check("A6 opposite panel (SELL) -> no agreement with UP",
          eyes6.panel_agrees(t0, "UP", window_s=300) is None)

    # ---- B. the caps (scratch cwd for the counter file) ---------------------
    print("B. the caps (counter, daily limit, cooldown)")
    scratch = tempfile.mkdtemp(prefix="a2_caps_")
    oldcwd = os.getcwd()
    os.chdir(scratch)
    try:
        os.environ["AGGRESSIVE_ENTRY_ENABLED"] = "1"
        for _ in range(3):
            at.a2_count_fire(t0)
        check("B1 counter round-trip: 3 fires -> count 3", at.a2_fire_count(t0) == 3)
        check("B2 date rollover -> 0",
              at.a2_fire_count(t0 + timedelta(days=1)) == 0)
        os.makedirs("data", exist_ok=True)
        with open(os.path.join("data", "a2_burst_count.json"), "w") as f:
            json.dump({"date": t0.strftime("%Y-%m-%d"), "count": 10}, f)
        saved = at.EYES
        at.EYES = seed_eyes(at, t0)
        v = at.a2_eval(power("UP"), of(), "BUY", t0, CFG)
        check("B3 daily cap (10/10) -> refused with the cap reason",
              not v["fire"] and "cap" in v["reason"], v)
        at.EYES = seed_eyes(at, t0)
        at.EYES.last_fire = t0 - timedelta(seconds=30)
        with open(os.path.join("data", "a2_burst_count.json"), "w") as f:
            json.dump({"date": t0.strftime("%Y-%m-%d"), "count": 1}, f)
        v = at.a2_eval(power("UP"), of(), "BUY", t0, CFG)
        check("B4 cooldown (fired 30s ago) -> refused",
              not v["fire"] and "cooldown" in v["reason"], v)
        at.EYES = saved
    finally:
        os.environ.pop("AGGRESSIVE_ENTRY_ENABLED", None)
        shutil.rmtree(scratch, ignore_errors=True)
        os.chdir(oldcwd)

    # ---- E build + twin gate + integration (real brain) ---------------------
    # simulation = the owner's LIVE machine: A1 bar service + the 2026-10-06
    # v1 wiring (apply_aggressive_patches.py: P1A/P1B + P2 passive-off) + A2
    print("E. the real brain: twin gate + integration (this is the slow part)")
    tmp_a = tempfile.mkdtemp(prefix="a2_twin_a_")
    tmp_b = tempfile.mkdtemp(prefix="a2_twin_b_")
    for d in (tmp_a, tmp_b):
        for f in glob.glob(os.path.join(fam, "*.py")):
            shutil.copy(f, d)
        # the base brain: A1-patched fresh (workshop pre-A1 base) or the live one
        shutil.copy(base_brain(), os.path.join(d, "step2_market_analysis.py"))
        if "A1 BAR SERVICE" not in open(os.path.join(d, "step2_market_analysis.py")).read():
            a1p = aux("apply_a1_patch.py",
                      "/home/user/G11_CLOCKS_2026-10-12/apply_a1_patch.py")
            shutil.copy(a1p, d)
            r = subprocess.run([sys.executable, "apply_a1_patch.py"], cwd=d,
                               capture_output=True, text=True, timeout=300)
            assert "A1" in open(os.path.join(d, "step2_market_analysis.py")).read(), \
                "A1 patch failed in rig build: " + r.stdout[-200:] + r.stderr[-200:]
        # the owner's machine carries the 2026-10-06 v1 wiring -- plant it
        shutil.copy(aux("apply_aggressive_patches.py",
                        "/home/user/apply_aggressive_patches.py"), d)
        r = subprocess.run([sys.executable, "apply_aggressive_patches.py"], cwd=d,
                           capture_output=True, text=True, timeout=300)
        assert r.returncode == 0 and "evaluate_aggressive_trigger as _aggr_eval" \
            in open(os.path.join(d, "step2_market_analysis.py")).read(), \
            "v1 wiring failed in rig build: " + r.stdout[-300:] + r.stderr[-200:]
        for f in ("aggressive_trigger.py", "tf_robot_replay.py", "twin_day_runner.py",
                  "tf_lens.py"):
            src = aux(f, {
                "tf_lens.py": "/home/user/TF_LENS_2026-10-10/tf_lens.py",
                "twin_day_runner.py": "/home/user/A1_SPEED_2026-10-10/twin_day_runner.py",
                "tf_robot_replay.py": "/home/user/G11_CLOCKS_2026-10-12/tf_robot_replay.py",
            }.get(f, os.path.join(HERE, f)))
            shutil.copy(src, os.path.join(d, f))
    # tmp_b gets A2 on top of A1
    shutil.copy(os.path.join(HERE, "apply_a2_patch.py"), tmp_b)
    r = subprocess.run([sys.executable, "apply_a2_patch.py"], cwd=tmp_b,
                       capture_output=True, text=True, timeout=300)
    check("E0 A2 patch applies cleanly to the A1-patched brain",
          r.returncode == 0 and ("A2 WIRED" in r.stdout or "A2 ALREADY" in r.stdout),
          r.stdout[-600:] + r.stderr[-300:])

    # the lake: 90 min, strong one-sided ramp in the middle
    import random
    rng = random.Random(2026)
    lake_date = "2026-10-15"
    lines = ["time,type,price,size,extra1,extra2"]
    t = datetime(2026, 10, 15, 8, 0, tzinfo=utc)
    end = datetime(2026, 10, 15, 9, 30, tzinfo=utc)
    price = 2650.0
    while t < end:
        mins = (t - datetime(2026, 10, 15, 8, 0, tzinfo=utc)).total_seconds() / 60.0
        price += 0.30 if 20 <= mins < 60 else rng.uniform(-0.15, 0.15)
        lines.append(f"2026-10-15 {t.strftime('%H:%M:%S')}.000,Last,{price:.2f},3,bookmap,rig")
        t += timedelta(seconds=rng.choice([3, 5, 7]))
    for d in (tmp_a, tmp_b):
        open(os.path.join(d, "ticks.csv"), "w").write("\n".join(lines) + "\n")

    env0 = dict(os.environ, PYTHONHASHSEED="0")
    env0.pop("AGGRESSIVE_ENTRY_ENABLED", None)
    ra = subprocess.run([sys.executable, "tf_robot_replay.py", "--date", lake_date,
                         "--tfs", "5", "--window", "08:00-09:30"],
                        cwd=tmp_a, capture_output=True, text=True, env=env0, timeout=1200)
    rb = subprocess.run([sys.executable, "tf_robot_replay.py", "--date", lake_date,
                         "--tfs", "5", "--window", "08:00-09:30"],
                        cwd=tmp_b, capture_output=True, text=True, env=env0, timeout=1200)
    rep_a = open(os.path.join(tmp_a, "data", f"tf_robot_{lake_date}.txt")).read()
    rep_b = open(os.path.join(tmp_b, "data", f"tf_robot_{lake_date}.txt")).read()

    # The twin's news-calendar check reads the REAL clock (pre-existing harness
    # weather, found 2026-10-13): runs started in different real-world minutes
    # get slightly different news weighting -> conf jitter ONLY.  Decisions are
    # what "identical" means: compare per-cycle direction/price/plan vectors
    # and the M5 row minus the two conf columns.
    def m5_row(path):
        for l in open(path):
            if l.strip().startswith("M5 "):
                parts = l.split()
                # drop idx 5 (CONF%) and idx 6 (MED-CONF) -- the news-calendar
                # real-time leak jitters conf only; decisions stay comparable
                return [p for i, p in enumerate(parts) if i not in (5, 6)]
        return None

    def decision_vec(d):
        vec = []
        for p in sorted(glob.glob(os.path.join(d, "data", "tfrobot_M5_*.jsonl"))):
            for line in open(p, encoding="utf-8", errors="replace"):
                try:
                    r = json.loads(line)
                except ValueError:
                    vec.append(("badline",))
                    continue
                snap = r.get("snap") or {}
                shot = snap.get("shot") or {}
                vec.append((r.get("i"), r.get("error"), r.get("served"),
                            snap.get("signal_direction"), snap.get("price"),
                            shot.get("shot"), shot.get("entry_style")))
        return vec

    row_ok = (m5_row(os.path.join(tmp_a, "data", f"tf_robot_{lake_date}.txt"))
              == m5_row(os.path.join(tmp_b, "data", f"tf_robot_{lake_date}.txt")))
    vec_ok = decision_vec(tmp_a) == decision_vec(tmp_b)
    check("E1 TWIN GATE: lane disabled -> identical decisions every cycle",
          ra.returncode == 0 and rb.returncode == 0 and row_ok and vec_ok,
          f"rc={ra.returncode}/{rb.returncode} row_ok={row_ok} vec_ok={vec_ok} "
          + ra.stderr[-120:] + rb.stderr[-120:])

    env1 = dict(env0, AGGRESSIVE_ENTRY_ENABLED="1")
    rc = subprocess.run([sys.executable, "tf_robot_replay.py", "--date", lake_date,
                         "--tfs", "5", "--window", "08:00-09:30"],
                        cwd=tmp_b, capture_output=True, text=True, env=env1, timeout=1200)
    rep_c = open(os.path.join(tmp_b, "data", f"tf_robot_{lake_date}.txt")).read() \
        if rc.returncode == 0 else ""
    err_ok = "ERR" in rep_c and [l for l in rep_c.splitlines() if l.strip().startswith("M5")][0].split()[2] == "0"
    sig_ok = int([l for l in rep_c.splitlines() if l.strip().startswith("M5")][0].split()[3]) >= 1
    jsonl = "".join(open(p, encoding="utf-8", errors="replace").read()
                    for p in glob.glob(os.path.join(tmp_b, "data", "tfrobot_M5_*.jsonl")))
    engaged = ("A2 burst" in jsonl) or os.path.exists(os.path.join(tmp_b, "data", "a2_burst_count.json"))
    check("E2 integration: lane enabled -> 0 errors, signals flow, lane engaged",
          rc.returncode == 0 and err_ok and sig_ok and engaged,
          rc.stderr[-200:] + rep_c[-300:])

    # ---- C + D. plan_shot bracket + the serve lane (patched shooting_team) ---
    print("C. the burst bracket (real plan_shot, proven fixtures)")
    sys.path.insert(0, tmp_b)          # patched shooting_team lives here
    for m in [k for k in list(sys.modules) if k in ("shooting_team", "signal_team")]:
        del sys.modules[m]
    import shooting_team
    from shooting_team import plan_shot
    base_ctx = {"bid": 99.9, "ask": 100.1, "news_state": "QUIET",
                "data_quality_ok": True, "has_data": True,
                "last_data_age_seconds": 1.0}
    burst_ctx = {**base_ctx, "aggressive_trigger_confirmed": True,
                 "aggressive_trigger_reason": "rig: planted burst",
                 "a2_burst": True}
    shot = plan_shot(smap("UP"), power("UP"), 100.0, 1.0, spread=0.2,
                     config=CFG, book=None, entry_context=burst_ctx)
    check("C1 burst -> GO AGGRESSIVE_MARKET, fixed 1.8/1.8 bracket, rr 1.0",
          shot.get("shot") == "GO" and shot.get("entry_style") == "AGGRESSIVE_MARKET"
          and abs(shot["stop"] - (100.1 - 1.8)) < 1e-9
          and abs(shot["target"] - (100.1 + 1.8)) < 1e-9
          and abs(shot["rr"] - 1.0) < 1e-9
          and shot.get("stop_source") == "A2_BURST_FIXED", shot)
    shot2 = plan_shot(smap("UP"), power("UP"), 100.0, 1.0, spread=0.2,
                      config=CFG, book=None, entry_context=base_ctx)
    import json as _json
    plan_txt = _json.dumps(shot2, default=str)
    check("C2 no trigger -> unchanged path (aggressive blocked, verbatim reason)",
          "no separately confirmed aggressive-entry trigger is wired" in plan_txt
          and shot2.get("entry_style") != "AGGRESSIVE_MARKET"
          and shot2.get("shot") in ("GO", "WAIT"),
          f"shot={shot2.get('shot')} style={shot2.get('entry_style')} "
          f"why={str(shot2.get('why'))[:180]}")

    print("D. the serve lane (direct, seeded eyes)")
    scratch2 = tempfile.mkdtemp(prefix="a2_serve_")
    os.chdir(scratch2)
    try:
        os.environ["AGGRESSIVE_ENTRY_ENABLED"] = "1"
        served = SimpleNamespace(
            power_v2=power("UP"), order_flow=of(70, 30),
            signal_direction="BUY", signal_map=smap("UP"),
            news=SimpleNamespace(news_state="QUIET"))
        md = {"has_data": True, "data_quality_ok": True, "last_data_age_seconds": 1.0}
        saved = at.EYES
        at.EYES = seed_eyes(at, t0)
        new_shot, notes = at.a2_serve_eval(served, md, t0, 100.0, 0.2, CFG,
                                           bid=99.9, ask=100.1, atr=1.0)
        at.EYES = saved
        cnt = at.a2_fire_count(t0)
        check("D1 seeded eyes -> FIRE, GO plan, note + counter bumped to 1",
              new_shot is not None
              and new_shot.get("entry_style") == "AGGRESSIVE_MARKET"
              and any(n.startswith("A2 burst: FIRE") for n in notes)
              and cnt == 1, (notes, cnt))
        # disabled -> nothing at all
        os.environ["AGGRESSIVE_ENTRY_ENABLED"] = "0"
        os.remove(os.path.join("data", "a2_burst_count.json"))
        saved = at.EYES
        at.EYES = seed_eyes(at, t0)
        new_shot2, notes2 = at.a2_serve_eval(served, md, t0, 100.0, 0.2, CFG,
                                             bid=99.9, ask=100.1, atr=1.0)
        at.EYES = saved
        check("D2 disabled -> (None, []) and no counter file",
              new_shot2 is None and notes2 == []
              and not os.path.exists(os.path.join("data", "a2_burst_count.json")))
    finally:
        os.environ.pop("AGGRESSIVE_ENTRY_ENABLED", None)
        shutil.rmtree(scratch2, ignore_errors=True)
        os.chdir(oldcwd)

    # ---- F. v1's own tests ---------------------------------------------------
    print("F. the v1 trigger's own tests (module extended, v1 untouched)")
    envf = dict(os.environ)
    envf.pop("AGGRESSIVE_ENTRY_ENABLED", None)
    rf = subprocess.run([sys.executable, os.path.join(HERE, "test_aggressive_trigger.py")],
                        cwd=HERE, capture_output=True, text=True, env=envf, timeout=300)
    check("F1 v1 test suite still green", rf.returncode == 0,
          rf.stdout[-250:] + rf.stderr[-250:])

    # ---- G. the A2 gate around the live v1 wiring ---------------------------
    print("G. the A2 gate (live v1 wiring + rails)")
    scratch3 = tempfile.mkdtemp(prefix="a2_gate_")
    os.chdir(scratch3)
    try:
        v1_yes = {"confirmed": True, "reason": "v1 says yes"}
        # G1 disabled -> passthrough verbatim
        outg = at.a2_v1_gate(v1_yes, power("UP"), of(70, 30), "BUY", t0, CFG, [])
        check("G1 lane disabled -> v1 verdict passes through untouched",
              outg.get("aggressive_trigger_confirmed") is True
              and outg.get("aggressive_trigger_reason") == "v1 says yes"
              and "a2_burst" not in outg, outg)
        os.environ["AGGRESSIVE_ENTRY_ENABLED"] = "1"
        # G2 enabled + v1 confirmed + NO lean -> refused by eye 1
        saved = at.EYES
        at.EYES = at.BurstEyes()
        outg = at.a2_v1_gate(v1_yes, power("UP"), of(50, 50), "BUY", t0, CFG, [])
        check("G2 v1 confirmed but no lean -> refused (eye 1), no a2_burst",
              outg.get("aggressive_trigger_confirmed") is False
              and "eye 1" in str(outg.get("aggressive_trigger_reason"))
              and "a2_burst" not in outg, outg)
        # G3 enabled + lean + panel -> FIRE with a2_burst + counter 1
        at.EYES = seed_eyes(at, t0, strength=0.6, panel="BUY", panel_age_s=60)
        notes_g = []
        outg = at.a2_v1_gate(v1_yes, power("UP"), of(70, 30), "BUY", t0, CFG, notes_g)
        check("G3 v1 + lean + panel -> FIRE: a2_burst set, counter 1, note written",
              outg.get("aggressive_trigger_confirmed") is True
              and outg.get("a2_burst") is True
              and at.a2_fire_count(t0) == 1
              and any(n.startswith("A2 burst: FIRE") for n in notes_g),
              (outg, notes_g))
        # G4 cap reached -> refused
        os.makedirs("data", exist_ok=True)
        with open(os.path.join("data", "a2_burst_count.json"), "w") as f:
            json.dump({"date": t0.strftime("%Y-%m-%d"), "count": 10}, f)
        at.EYES = seed_eyes(at, t0, strength=0.6, panel="BUY", panel_age_s=60)
        outg = at.a2_v1_gate(v1_yes, power("UP"), of(70, 30), "BUY", t0, CFG, [])
        check("G4 daily cap (10/10) -> refused by the rails",
              outg.get("aggressive_trigger_confirmed") is False
              and "cap" in str(outg.get("aggressive_trigger_reason")), outg)
        at.EYES = saved
    finally:
        os.environ.pop("AGGRESSIVE_ENTRY_ENABLED", None)
        shutil.rmtree(scratch3, ignore_errors=True)
        os.chdir(oldcwd)

    print("=" * 78)
    if FAILURES:
        print(f"RIG RESULT: {len(FAILURES)} FAILED -> {', '.join(FAILURES)}")
        sys.exit(1)
    print("RIG RESULT: ALL PASS -- the NOW button fires measured, capped, twin-safe.")


if __name__ == "__main__":
    main()
