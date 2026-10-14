#!/usr/bin/env python3
"""apply_doorman_patch.py -- THE DOORMAN FIX (Team 5, 2026-10-14).

What the owner's log proved (2026-10-13): key #1 failed 118 times, and every
failure was retried FIRST on every later cycle, each waiting on a ~20s
timeout -- the source of the 62-228s wall-clock cycles (the brain itself is
fast: the truck camera measured median 1882ms). The code already parks a key
on QUOTA errors (cooldown, self-recovering, persisted); it just never counted
NETWORK/timeout failures. This patch adds exactly that:

  step3_ai_decision.py (2 surgical inserts):
    1. `_note_key_failure()` -- counts a key's non-quota failures per day in
       the SAME state file (hashed key IDs only, never the raw key). After
       AI_KEY_DAILY_FAIL_CAP (default 5) failures the key is PARKED with the
       same cooldown machinery (AI_KEY_COOLDOWN_MINUTES, default 20) and one
       clear log line says so. The fail counter is kept for the whole day:
       after the cooldown expires, a still-broken key re-parks on its FIRST
       new failure -- worst case ~6 wasted attempts per key per DAY, not 118.
    2. the network-error branch now calls `_note_key_failure()` before moving
       to the next key.

Fail-closed: any OSError in the counter leaves the old behavior (try next
key). No behavior change on the happy path. The quota path is untouched.

Run from the project root:  python apply_doorman_patch.py
"""
import os
import shutil
import sys

TARGET = "step3_ai_decision.py"

INSERT_BEFORE = '''@dataclass
class Decision:'''

HELPERS = '''def _note_key_failure(key: str, key_index: int = 0) -> None:
    """DOORMAN FIX (2026-10-14): count a NON-quota failure (network/timeout).

    A quota error always parked the key; a network error was retried first on
    every cycle -- the owner's 2026-10-13 log showed key #1 failing 118 times
    in one day, each attempt waiting on a ~20s timeout (the speed-tail
    culprit). After AI_KEY_DAILY_FAIL_CAP failures the key is parked with the
    same cooldown machinery; it self-recovers, and because the day's counter
    is kept, a still-broken key re-parks on its first new failure after the
    cooldown. Bounded waste: ~cap+1 attempts per broken key per day.
    """
    def _cap() -> int:
        import os
        raw = os.environ.get("AI_KEY_DAILY_FAIL_CAP")
        if raw is None:
            raw = getattr(config, "AI_KEY_DAILY_FAIL_CAP", 5)
        try:
            return max(1, int(float(raw)))
        except (TypeError, ValueError):
            return 5
    try:
        path = _key_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        state = {}
        try:
            if path.exists():
                state = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
        today = datetime.now(timezone.utc).date().isoformat()
        if state.get("date") != today:
            state = {"date": today}
        kid = _key_id(key)
        fails = dict(state.get("fails") or {})
        fails[kid] = int(fails.get(kid) or 0) + 1
        state["fails"] = fails
        if fails[kid] >= _cap():
            cooldowns = dict(state.get("cooldowns") or {})
            cooldowns[kid] = (datetime.now(timezone.utc)
                              + timedelta(minutes=_key_cooldown_minutes())).isoformat()
            state["cooldowns"] = cooldowns
            logger.warning(
                "STEP 3: key #%d parked for %d min after %d failed calls "
                "today (network/timeout, not quota); trying the next key.",
                key_index, _key_cooldown_minutes(), fails[kid])
        path.write_text(json.dumps(state), encoding="utf-8")
    except OSError:
        pass


'''

FAIL_BRANCH_OLD = '''                    # other error (network etc.) -> try the next key
                    logger.warning("STEP 3: call failed (key #%d, %s): %s",
                                   key_index, model, last_error[:120])
                    continue'''
FAIL_BRANCH_NEW = '''                    # other error (network etc.) -> try the next key
                    logger.warning("STEP 3: call failed (key #%d, %s): %s",
                                   key_index, model, last_error[:120])
                    _note_key_failure(key, key_index)
                    continue'''


def _show_context(path, needle, around=2, max_hits=4):
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


def main():
    path = TARGET
    if not os.path.exists(path):
        print(f"[abort] {path} not found (run from the project root)")
        return 1
    src = open(path, encoding="utf-8").read()
    if "_note_key_failure" in src:
        print(f"[skip] {path} already carries the doorman fix -- nothing to do")
        return 0
    for name, needle in (("Decision anchor", INSERT_BEFORE),
                         ("network-failure branch anchor", FAIL_BRANCH_OLD)):
        n = src.count(needle)
        if n != 1:
            print(f"[abort] {path}: anchor '{name}' found {n}x (expected 1x) -- "
                  "your file differs; NOTHING was written. Your handwriting:")
            _show_context(path, "class Decision:" if "Decision" in name else "call failed")
            return 1
    out = src.replace(INSERT_BEFORE, HELPERS + INSERT_BEFORE, 1)
    out = out.replace(FAIL_BRANCH_OLD, FAIL_BRANCH_NEW, 1)

    import py_compile
    tmp = path + ".doorman_tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(out)
    try:
        py_compile.compile(tmp, doraise=True)
    except Exception as e:
        os.remove(tmp)
        print(f"[abort] patched file does not compile: {e}; NOTHING was written")
        return 1
    shutil.copy(path, path + ".pre_doorman")
    os.replace(tmp, path)
    print(f"[ok] {path}: 2 edits applied (fail counter + parked-after-cap; "
          f"backup: {path}.pre_doorman)")
    print()
    print("DOORMAN FIXED. A broken key now wastes at most ~6 attempts per day")
    print("instead of being retried first 118 times. Optional .env knob:")
    print("    AI_KEY_DAILY_FAIL_CAP=5      (default 5, no .env change needed)")
    print("Prove it:  python rig_doorman.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
