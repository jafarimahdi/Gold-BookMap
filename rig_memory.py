#!/usr/bin/env python3
"""rig_memory.py -- the test rig for the TEAM MEMORY patch (v1.1, 2026-10-14).

v1.1: install-order independent. The rig accepts a project-root step3 that
already carries the doorman fix and/or the memory patch: a patcher answering
[skip] (already applied) counts as healthy. Fixes D11, which wrongly demanded
[ok] from the doorman patcher when the README order had already applied it.

12 checks, run from the project root (next to the REAL step3_ai_decision.py
and config.py). It never touches your live files: every patch test runs on a
scratch COPY in a temp folder; the builder tests run on scratch CSV fixtures
with hand-computed numbers.

  D1  builder math: 2 fixture days -> exact card (hits, pts, floor, order,
      today's file excluded)
  D2  builder refuses to write when nothing is usable (empty dir, today-only)
  D3  patch applies clean on a copy ([ok] x2, backup made, py_compile ok)
  D4  GOLDEN: with no card, the patched build_prompt is BYTE-IDENTICAL to the
      original module's prompt
  D5  fresh card -> block injected with exact expected lines, prompt tail
      unchanged (prompt == golden + card block, exactly)
  D6  look-ahead guard: a card containing today is NOT used
  D7  staleness guard: a card older than AI_CARD_MAX_STALE_DAYS is NOT used
  D8  fail-closed: corrupt card / snapshot without timestamp / all judges
      below the floor -> NOT used, never crashes
  D9  determinism: same card -> identical prompt twice (cache path included)
  D10 double-apply -> [skip], file unchanged
  D11 composition: doorman-then-memory AND memory-then-doorman both apply
      clean, compile, and keep the golden no-card prompt
  D12 the .pre_memory backup preserves the ORIGINAL file bytes
"""
import csv
import importlib.util
import json
import py_compile
import shutil
import subprocess
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEP3 = HERE / "step3_ai_decision.py"
CONFIG = HERE / "config.py"
BUILDER = HERE / "judge_report_card.py"
APPLY_MEM = HERE / "apply_memory_patch.py"
APPLY_DOOR = HERE / "apply_doorman_patch.py"

HEADER = ["judge", "votes", "participation_pct", "buy", "sell", "quiet",
          "right_pct", "scored", "points_after_spread", "avg_points",
          "agree_with_ensemble_pct", "weight_now", "weight_suggested"]

TODAY = datetime.now(timezone.utc).date()
TODAYS = TODAY.isoformat()
D1D = (TODAY - timedelta(days=2)).isoformat()
D2D = (TODAY - timedelta(days=1)).isoformat()

# hand-computed fixtures (active non-retired judges):
#   vwap_bands:      day1 (80.0% of 40) + day2 (60.0% of 60) -> 100 scored, 68.0% hit,
#                    20.0 + 34.0 = +54.0 pts -> +0.54/call  [STRONG]
#   footprint_delta: day1 (40.0% of 50) + day2 (44.0% of 50) -> 100 scored, 42.0% hit,
#                    -25.0 + -11.0 = -36.0 pts -> -0.36/call [WEAK]
#   l3_net_flow:     only 10 scored calls in the window -> below the 20 floor, excluded
DAY1_ROWS = [
    ["vwap_bands", 100, 50.0, 60, 40, 0, 80.0, 40, 20.0, 0.5, 90.0, 0.8, 0.9],
    ["footprint_delta", 100, 50.0, 50, 50, 0, 40.0, 50, -25.0, -0.5, 60.0, 1.0, 0.8],
    ["l3_net_flow", 100, 50.0, 50, 50, 0, 50.0, 10, -2.0, -0.2, 55.0, 1.5, 1.2],
]
DAY2_ROWS = [
    ["vwap_bands", 90, 45.0, 50, 40, 0, 60.0, 60, 34.0, 0.567, 88.0, 0.8, 0.9],
    ["footprint_delta", 90, 45.0, 45, 45, 0, 44.0, 50, -11.0, -0.22, 58.0, 1.0, 0.8],
]
TODAY_POISON = [["vwap_bands", 1, 100.0, 1, 0, 0, 100.0, 100, 999.0, 9.99, 100.0, 0.8, 9.9]]

EXPECTED_BLOCK = (
    "JUDGE REPORT CARDS (each judge's real track record over 2 graded "
    f"day(s) {D1D}..{D2D}, scored on its own clock by the nightly audit; weigh "
    "the panel votes accordingly):\n"
    "- vwap_bands: 68% right, +0.54 pts/call, 100 scored calls [STRONG]\n"
    "- footprint_delta: 42% right, -0.36 pts/call, 100 scored calls [WEAK]\n"
    "(tag rule: STRONG = pts/call>0 and hit>=55%; WEAK = pts/call<-0.25 or hit<40%; else MIXED)\n\n"
)

WORK = None
STATE = {}
RESULTS = []


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _healthy(r):
    """A patcher run is healthy if it applied ([ok]) or found its own patch
    already present ([skip]) -- both leave the file carrying the patch."""
    return ("[ok]" in r.stdout) or ("[skip]" in r.stdout)


def write_panel(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for r in rows:
            w.writerow(r)


def run_builder(data_dir, extra=()):
    return subprocess.run(
        [sys.executable, str(BUILDER), "--data-dir", str(data_dir), *extra],
        capture_output=True, text=True)


def snap():
    return types.SimpleNamespace(
        timestamp=datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0))


# ------------------------------------------------------------- engines ------()
_ENG = {}


def load_engines():
    if _ENG:
        return _ENG
    fake_s2 = types.ModuleType("step2_market_analysis")
    fake_s2.snapshot_to_dict = lambda s: {"price": 4000.0, "timestamp": "fixed",
                                          "order_flow": {}, "level3": {}}
    sys.modules["step2_market_analysis"] = fake_s2
    fake_se = types.ModuleType("session")
    fake_se.session_context = lambda: {"session": "RIG", "market_open": "yes"}
    sys.modules["session"] = fake_se
    orig, patched = STATE["orig"], STATE["patched"]
    sys.path.insert(0, str(orig))
    sys.path.insert(0, str(patched))

    def load(name, path):
        spec = importlib.util.spec_from_file_location(name, path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)
        return mod

    s3o = load("s3_orig", orig / "step3_ai_decision.py")
    s3p = load("s3_patched", patched / "step3_ai_decision.py")
    _ENG["o"] = s3o.AIDecisionEngine()
    _ENG["p"] = s3p.AIDecisionEngine()
    _ENG["pmod"] = s3p
    return _ENG


# --------------------------------------------------------------- checks -----
def d1_builder_math():
    b1 = WORK / "b1"
    write_panel(b1 / f"judge_panel_{D1D}.csv", DAY1_ROWS)
    write_panel(b1 / f"judge_panel_{D2D}.csv", DAY2_ROWS)
    write_panel(b1 / f"judge_panel_{TODAYS}.csv", TODAY_POISON)
    r = run_builder(b1, ["--days", "5"])
    expect(r.returncode == 0, f"builder exit {r.returncode}: {r.stdout[-300:]} {r.stderr[-300:]}")
    card = json.loads((b1 / "judge_report_card.json").read_text(encoding="utf-8"))
    expect(card["source_days"] == [D1D, D2D], f"source_days {card['source_days']}")
    expect(TODAYS not in card["source_days"], "today leaked into the card!")
    expect(card["window_days"] == 2, f"window_days {card['window_days']}")
    names = [j["judge"] for j in card["judges"]]
    expect(names == ["vwap_bands", "footprint_delta"], f"judges/order {names}")
    h, w = card["judges"]
    expect(h["scored"] == 100 and abs(h["hit_pct"] - 68.0) < 0.05
           and abs(h["net_pts"] - 54.0) < 0.05 and abs(h["pts_per_call"] - 0.54) < 0.005,
           f"vwap_bands numbers {h}")
    expect(w["scored"] == 100 and abs(w["hit_pct"] - 42.0) < 0.05
           and abs(w["net_pts"] + 36.0) < 0.05 and abs(w["pts_per_call"] + 0.36) < 0.005,
           f"footprint_delta numbers {w}")
    expect(h["votes"] == 190 and w["votes"] == 190, f"votes sums {h['votes']} {w['votes']}")


def d2_builder_fail_closed():
    b2 = WORK / "b2"
    b2.mkdir(parents=True, exist_ok=True)
    r = run_builder(b2)
    expect(r.returncode == 0, f"exit {r.returncode}")
    expect("NOTHING written" in r.stdout, f"no honest refusal: {r.stdout[-200:]}")
    expect(not (b2 / "judge_report_card.json").exists(), "wrote a card from nothing!")
    b3 = WORK / "b3"
    write_panel(b3 / f"judge_panel_{TODAYS}.csv", TODAY_POISON)
    r = run_builder(b3)
    expect("NOTHING written" in r.stdout, "today-only dir must refuse")
    expect(not (b3 / "judge_report_card.json").exists(), "today-only must not write")


def d3_patch_applies():
    orig, patched = WORK / "orig", WORK / "patched"
    for d in (orig, patched):
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(STEP3, d / "step3_ai_decision.py")
        shutil.copy2(CONFIG, d / "config.py")
    STATE["orig"], STATE["patched"] = orig, patched
    r = subprocess.run([sys.executable, str(APPLY_MEM)], cwd=patched,
                       capture_output=True, text=True)
    expect(r.returncode == 0, f"patcher exit {r.returncode}: {r.stdout} {r.stderr}")
    expect(_healthy(r), f"patcher neither applied nor skipped cleanly: {r.stdout}")
    if "[ok]" in r.stdout:
        expect((patched / "step3_ai_decision.py.pre_memory").exists(), "no backup made")
    py_compile.compile(str(patched / "step3_ai_decision.py"), doraise=True)
    src = (patched / "step3_ai_decision.py").read_text(encoding="utf-8")
    expect("def _judge_report_card_block" in src, "helper missing")
    expect("+ _judge_report_card_block(snapshot)" in src, "prompt insert missing")


def d4_golden_no_card():
    E = load_engines()
    p_o = E["o"].build_prompt(snap())
    p_p = E["p"].build_prompt(snap())
    expect(p_o == p_p, f"golden broken: {len(p_o)} vs {len(p_p)} bytes")
    STATE["golden"] = p_o


def d5_card_injected():
    E = load_engines()
    data = STATE["patched"] / "data"
    data.mkdir(parents=True, exist_ok=True)
    shutil.copy2(WORK / "b1" / "judge_report_card.json", data / "judge_report_card.json")
    p = E["p"].build_prompt(snap())
    g = STATE["golden"]
    expect(p != g, "card was not injected")
    idx = g.index("MARKET ANALYSIS METRICS")
    expect(p == g[:idx] + EXPECTED_BLOCK + g[idx:],
           "prompt is not exactly golden + card block")
    STATE["with_card"] = p


def d6_lookahead_guard():
    E = load_engines()
    dp = STATE["patched"] / "data" / "judge_report_card.json"
    card = json.loads(dp.read_text(encoding="utf-8"))
    card["source_days"] = [D1D, TODAYS]
    dp.write_text(json.dumps(card), encoding="utf-8")
    p = E["p"].build_prompt(snap())
    expect(p == STATE["golden"], "a card containing today was used!")
    shutil.copy2(WORK / "b1" / "judge_report_card.json", dp)


def d7_stale_guard():
    E = load_engines()
    dp = STATE["patched"] / "data" / "judge_report_card.json"
    card = json.loads((WORK / "b1" / "judge_report_card.json").read_text(encoding="utf-8"))
    card["source_days"] = [(TODAY - timedelta(days=10)).isoformat(),
                           (TODAY - timedelta(days=9)).isoformat()]
    dp.write_text(json.dumps(card), encoding="utf-8")
    p = E["p"].build_prompt(snap())
    expect(p == STATE["golden"], "a stale card was used!")


def d8_fail_closed():
    E = load_engines()
    g = STATE["golden"]
    dp = STATE["patched"] / "data" / "judge_report_card.json"
    dp.write_text("{not json at all", encoding="utf-8")
    expect(E["p"].build_prompt(snap()) == g, "corrupt card was not ignored")
    card = json.loads((WORK / "b1" / "judge_report_card.json").read_text(encoding="utf-8"))
    for j in card["judges"]:
        j["scored"] = 5
    dp.write_text(json.dumps(card), encoding="utf-8")
    expect(E["p"].build_prompt(snap()) == g, "below-floor card was shown")
    expect(E["pmod"]._judge_report_card_block(types.SimpleNamespace()) == "",
           "snapshot without timestamp must return empty")
    shutil.copy2(WORK / "b1" / "judge_report_card.json", dp)


def d9_determinism():
    E = load_engines()
    a = E["p"].build_prompt(snap())
    b = E["p"].build_prompt(snap())
    expect(a == b == STATE["with_card"], "non-deterministic block or cache")


def d10_double_apply():
    target = STATE["patched"] / "step3_ai_decision.py"
    before = target.read_bytes()
    r = subprocess.run([sys.executable, str(APPLY_MEM)], cwd=STATE["patched"],
                       capture_output=True, text=True)
    expect(r.returncode == 0 and "[skip]" in r.stdout, f"double apply: {r.stdout}")
    expect(target.read_bytes() == before, "double apply changed the file!")


def d11_composition():
    g = STATE["golden"]
    for tag, order in (("doorman_then_memory", (APPLY_DOOR, APPLY_MEM)),
                       ("memory_then_doorman", (APPLY_MEM, APPLY_DOOR))):
        d = WORK / tag
        d.mkdir(parents=True, exist_ok=True)
        shutil.copy2(STEP3, d / "step3_ai_decision.py")
        shutil.copy2(CONFIG, d / "config.py")
        for script in order:
            r = subprocess.run([sys.executable, str(script)], cwd=d,
                               capture_output=True, text=True)
            expect(r.returncode == 0 and _healthy(r),
                   f"{tag}: {script.name} -> {r.stdout[-300:]} {r.stderr[-300:]}")
        src = (d / "step3_ai_decision.py").read_text(encoding="utf-8")
        expect("_note_key_failure" in src and "_judge_report_card_block" in src,
               f"{tag}: a marker is missing after both patches")
        py_compile.compile(str(d / "step3_ai_decision.py"), doraise=True)
        sys.path.insert(0, str(d))
        spec = importlib.util.spec_from_file_location("s3_" + tag, d / "step3_ai_decision.py")
        mod = importlib.util.module_from_spec(spec)
        sys.modules["s3_" + tag] = mod
        spec.loader.exec_module(mod)
        p = mod.AIDecisionEngine().build_prompt(snap())
        expect(p == g, f"{tag}: golden no-card prompt broken after both patches")


def d12_backup_bytes():
    backup = STATE["patched"] / "step3_ai_decision.py.pre_memory"
    if not backup.exists():
        # Only possible when the root already carried the memory patch ([skip]):
        # then the ROOT's own .pre_memory is the real backup -- nothing to check here.
        return
    a = backup.read_bytes()
    b = (STATE["orig"] / "step3_ai_decision.py").read_bytes()
    expect(a == b, "the .pre_memory backup does not preserve the original bytes")


CHECKS = [
    ("D1  builder math (fixtures -> exact card, today excluded)", d1_builder_math),
    ("D2  builder fail-closed (nothing usable -> nothing written)", d2_builder_fail_closed),
    ("D3  patch applies clean on a copy (+backup, py_compile)", d3_patch_applies),
    ("D4  GOLDEN: no card -> prompt byte-identical to original", d4_golden_no_card),
    ("D5  fresh card -> exact block injected, tail unchanged", d5_card_injected),
    ("D6  look-ahead guard (card with today -> not used)", d6_lookahead_guard),
    ("D7  staleness guard (too-old card -> not used)", d7_stale_guard),
    ("D8  fail-closed (corrupt/no-timestamp/below-floor)", d8_fail_closed),
    ("D9  determinism (same card -> same prompt, cache ok)", d9_determinism),
    ("D10 double-apply -> [skip], file unchanged", d10_double_apply),
    ("D11 composition both orders (doorman + memory)", d11_composition),
    ("D12 .pre_memory backup preserves original bytes", d12_backup_bytes),
]


def main() -> int:
    global WORK
    print("=" * 78)
    print(" TEAM MEMORY RIG -- judge report cards for the AI doorman")
    print("=" * 78)
    missing = [p.name for p in (STEP3, CONFIG, BUILDER, APPLY_MEM, APPLY_DOOR) if not p.exists()]
    if missing:
        print(f" [fail] missing files next to this rig: {', '.join(missing)}")
        print("        Run it from the project root with all pack files present.")
        return 1
    WORK = Path(tempfile.mkdtemp(prefix="rig_memory_"))
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
        print(f" ALL PASS ({n}/{n}) -- the memory patch is safe to install.")
        return 0
    print(f" FAILURES: {RESULTS.count(False)}/{n} -- do NOT install. Scratch kept: {WORK}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
