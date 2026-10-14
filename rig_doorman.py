#!/usr/bin/env python3
"""rig_doorman.py -- the doorman fix's test lake (Team 5, 2026-10-14).

  D1  the patch applies cleanly to the real step3_ai_decision.py
  D2  running the patcher twice is a clean [skip]
  D3  _note_key_failure counts (3 failures -> fails=3, NOT parked)
  D4  cap (5) parks the key via the same cooldown machinery; counter kept
  D5  after the cooldown expires, ONE new failure re-parks it (bounded waste)
  D6  date rollover resets the counter
  D7  END-TO-END: a key that always fails is attempted EXACTLY 5 times all
      day, then never again; the healthy key answers every cycle
  D8  the QUOTA path is untouched: a quota error parks via cooldown with NO
      fail-count increment (two mechanisms coexist)

Run:  python rig_doorman.py     (from the folder holding apply_doorman_patch.py;
      uses the repo family next to it, or the workshop snapshot fallback)
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
    if os.path.exists(os.path.join(HERE, "step3_ai_decision.py")):
        return HERE
    if os.path.exists(os.path.join(HERE, "config.py")):
        return HERE
    return "/home/user/github_latest_verify"


def main():
    tmp = tempfile.mkdtemp(prefix="doorman_rig_")
    fam = fam_dir()
    for f in glob.glob(os.path.join(fam, "*.py")):
        shutil.copy(f, tmp)
    shutil.copy(os.path.join(HERE, "apply_doorman_patch.py"), tmp)
    state_path = os.path.join(tmp, "data", "gemini_keys_state.json")

    print("DOORMAN FIX rig -- a broken key gets parked, a quota key untouched")
    print("=" * 70)

    # ---- D1/D2: the patcher ------------------------------------------------
    r = subprocess.run([sys.executable, "apply_doorman_patch.py"], cwd=tmp,
                       capture_output=True, text=True, timeout=120)
    check("D1 patch applies cleanly to the real step3",
          r.returncode == 0 and "2 edits applied" in r.stdout,
          r.stdout[-300:] + r.stderr[-200:])
    r2 = subprocess.run([sys.executable, "apply_doorman_patch.py"], cwd=tmp,
                        capture_output=True, text=True, timeout=120)
    check("D2 patcher twice = clean [skip]",
          r2.returncode == 0 and "[skip]" in r2.stdout, r2.stdout[-200:])

    # ---- import the patched module -----------------------------------------
    sys.path.insert(0, tmp)
    os.chdir(tmp)
    from pathlib import Path as _P
    import step3_ai_decision as s3
    s3._KEY_STATE_FILE = _P(state_path)   # isolate ALL state to the rig dir

    bad = "RIG_BAD_KEY_0000000000000001"
    good = "RIG_GOOD_KEY_000000000000002"
    quota = "RIG_QUOTA_KEY_00000000000003"
    bad_id, good_id, quota_id = s3._key_id(bad), s3._key_id(good), s3._key_id(quota)

    # ---- D3: counting ------------------------------------------------------
    s3._note_key_failure(bad, 1)
    s3._note_key_failure(bad, 1)
    s3._note_key_failure(bad, 1)
    st = json.loads(open(state_path).read())
    check("D3 three failures counted, NOT parked",
          (st.get("fails") or {}).get(bad_id) == 3
          and bad_id not in (st.get("cooldowns") or {}), st)

    # ---- D4: the cap parks -------------------------------------------------
    s3._note_key_failure(bad, 1)
    s3._note_key_failure(bad, 1)
    st = json.loads(open(state_path).read())
    parked = bad_id in s3._exhausted_keys()
    check("D4 cap (5) parks the key; counter kept for the day",
          parked and (st.get("fails") or {}).get(bad_id) == 5, st)

    # ---- D5: re-park after cooldown expiry ---------------------------------
    until = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    st["cooldowns"] = {bad_id: until}
    open(state_path, "w").write(json.dumps(st))
    check("D5a cooldown expired -> key available again",
          bad_id not in s3._exhausted_keys())
    s3._note_key_failure(bad, 1)
    st = json.loads(open(state_path).read())
    check("D5b one new failure re-parks it immediately (bounded waste)",
          bad_id in s3._exhausted_keys() and (st.get("fails") or {}).get(bad_id) == 6, st)

    # ---- D6: date rollover resets ------------------------------------------
    until = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    st = json.loads(open(state_path).read())
    st["date"] = "2020-01-01"
    st["cooldowns"] = {bad_id: until}
    open(state_path, "w").write(json.dumps(st))
    s3._note_key_failure(bad, 1)
    st = json.loads(open(state_path).read())
    check("D6 date rollover resets the counter (new day, fresh chance)",
          (st.get("fails") or {}).get(bad_id) == 1
          and st.get("date") == datetime.now(timezone.utc).date().isoformat(), st)

    # ---- D7: end-to-end with a real engine ---------------------------------
    os.remove(state_path)
    attempts = {"bad": 0, "good": 0}

    def fake_call(key, model, prompt):
        if key == bad:
            attempts["bad"] += 1
            raise ValueError("connection reset by peer (rig)")
        attempts["good"] += 1
        return "ok"

    eng = s3.AIDecisionEngine(api_keys=[bad, good], model="rig-model")
    eng._check_cache = lambda snap: None
    eng.build_prompt = lambda snap: "rig prompt"
    eng._update_cache = lambda snap, dec: None
    eng._parse_response = lambda text: ("HOLD", 25.0, "rig rationale")
    eng._call_with_key = fake_call
    s3.genai = object()          # the SDK-present branch (call is stubbed anyway)
    decisions = [eng.decide(SimpleNamespace(price=100.0, level3=None, order_flow=None))
                 for _ in range(10)]
    check("D7 END-TO-END: broken key attempted exactly 5 times in 10 cycles, "
          "healthy key answered all 10",
          attempts == {"bad": 5, "good": 10}
          and all(d.action == "HOLD" for d in decisions), attempts)

    # ---- D8: the quota path untouched ---------------------------------------
    def quota_call(key, model, prompt):
        if key == quota:
            raise RuntimeError("429 RESOURCE_EXHAUSTED: per minute")
        return "ok"

    eng2 = s3.AIDecisionEngine(api_keys=[quota, good], model="rig-model")
    eng2._check_cache = lambda snap: None
    eng2.build_prompt = lambda snap: "rig prompt"
    eng2._update_cache = lambda snap, dec: None
    eng2._parse_response = lambda text: ("HOLD", 25.0, "rig rationale")
    eng2._call_with_key = quota_call
    eng2.decide(SimpleNamespace(price=100.0, level3=None, order_flow=None))
    st = json.loads(open(state_path).read())
    check("D8 quota error parks via cooldown with NO fail-count increment",
          quota_id in (st.get("cooldowns") or {})
          and (st.get("fails") or {}).get(quota_id) is None, st)

    print("=" * 70)
    if FAILURES:
        print(f"RIG RESULT: {len(FAILURES)} FAILED -> {', '.join(FAILURES)}")
        sys.exit(1)
    print("RIG RESULT: ALL PASS -- a broken doorbell gets silenced, the good ones ring on.")


if __name__ == "__main__":
    main()
