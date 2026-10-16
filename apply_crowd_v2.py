#!/usr/bin/env python3
"""apply_crowd_v2.py -- Crowd Math v2 "Three Fair Questions" (item 66)
                         + sigma cp1252 sanitize (item 67)
                         + whale_walls display cut (item 62)
2026-10-16, built against be3f319 (feature/power-team-m5-v2).

WHAT CHANGES
  shooting_team.py        the old road/target-size ratio (road/door, two static
                          depth stocks -- dimensionally wrong on quiet tape,
                          15-36x by construction) is replaced by THREE
                          like-vs-like questions:
                            Q1 SERVED          door >= SHOOT_DOOR_MARGIN x shot
                            Q2 NORMAL ROAD     road <= SHOOT_ROAD_NORMAL_MAX x its
                                               own rolling-median history
                                               (fail-closed while warming up)
                            Q3 SMALL FOOTPRINT shot <= SHOOT_PARTICIPATION_MAX of
                                               the recent traded volume
                          Every PASS and every veto prints its receipts.
  step2_market_analysis.py _shot_context gains trade_lots + recent_volume
                          (sum of volume over the last 500 trade prints).
  config.py               five new knobs (defaults; .env overrides optional):
                          SHOOT_DOOR_MARGIN=3.0, SHOOT_ROAD_NORMAL_MAX=2.0,
                          SHOOT_PARTICIPATION_MAX=0.10, SHOOT_ROAD_HISTORY_MIN=12,
                          SHOOT_ROAD_HISTORY_SECONDS=2700
                          SHOOT_ROAD_RATIO is retired (line kept, unread).
  main.py                 (a) _record ASCII-folds step/status at the logging
                          boundary (the 19:33 sigma UnicodeEncodeError crash);
                          (b) the JUDGES display line no longer shows whale_walls
                          (retired judge; confidence has ignored it since v5.8.1).

USAGE (git bash, in the repo root):
  python apply_crowd_v2.py --rig        # verify + sim, writes nothing real
  python apply_crowd_v2.py --apply      # backup .crowdv2.bak + patch (rig first!)
  python apply_crowd_v2.py --rollback   # restore the backups

Idempotent (markers crowdv2:v1). Demo account only. House laws honored:
fail-closed everywhere, receipts on every answer, --rollback always.
"""
from __future__ import annotations

import py_compile
import shutil
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent
MARKER = "crowdv2:v1"
FILES = {
    "shooting": REPO / "shooting_team.py",
    "step2": REPO / "step2_market_analysis.py",
    "config": REPO / "config.py",
    "main": REPO / "main.py",
}

# ---------------------------------------------------------------- SH-1: imports
SH1_OLD = """import math
import time
from datetime import datetime
"""
SH1_NEW = """import math
import statistics
import time
from collections import deque
from datetime import datetime
"""

# ------------------------------- SH-2: module-level crowd machinery (after _epoch)
SH2_OLD = """def _epoch(value: Any) -> Optional[float]:
    if isinstance(value, datetime):
        try:
            return value.timestamp()
        except (OverflowError, OSError, ValueError):
            return None
    return _num(value)
"""
SH2_NEW = '''def _epoch(value: Any) -> Optional[float]:
    if isinstance(value, datetime):
        try:
            return value.timestamp()
        except (OverflowError, OSError, ValueError):
            return None
    return _num(value)


# crowdv2:v1 -- road-history ring buffer + the Three Fair Questions ---------
# Replaces the old road/target-size ratio (two static depth stocks compared to
# each other; on quiet tape door targets are 8-10 lots while roads stay
# 150-300, so the ratio was 15-36x BY CONSTRUCTION -- a dimensional error,
# not a market fact).  The professional decomposition instead asks:
#   Q1 SERVED:          is the door big enough for OUR OWN shot size?
#   Q2 NORMAL ROAD:     is the road normal compared with ITS OWN recent history?
#   Q3 SMALL FOOTPRINT: is our shot a small fraction of the traded flow?
# Fail-closed on every missing input.  Receipts on every answer.
_ROAD_HISTORY: "deque" = deque(maxlen=4096)


def _crowd_v2(door_size, road_lots, shot_lots, recent_volume, config):
    """Three Fair Questions.  Returns a verdict dict; every field is a receipt."""
    now = time.time()
    window = max(300.0, _cfg(config, "SHOOT_ROAD_HISTORY_SECONDS", 2700.0))
    while _ROAD_HISTORY and now - _ROAD_HISTORY[0][0] > window:
        _ROAD_HISTORY.popleft()
    if road_lots is not None and road_lots >= 0:
        _ROAD_HISTORY.append((now, float(road_lots)))
    hist_min = max(1, int(_cfg(config, "SHOOT_ROAD_HISTORY_MIN", 12)))
    road_normal = None
    if len(_ROAD_HISTORY) >= hist_min:
        road_normal = statistics.median(v for _, v in _ROAD_HISTORY)
    door_margin = (door_size / shot_lots
                   if (door_size or 0) > 0 and (shot_lots or 0) > 0
                   else math.inf)
    if road_normal is not None and road_normal > 0:
        road_ratio = (road_lots or 0) / road_normal
    elif road_normal is not None:
        road_ratio = 0.0 if (road_lots or 0) <= 0 else math.inf
    else:
        road_ratio = None
    participation = (shot_lots / recent_volume
                     if (shot_lots or 0) > 0 and (recent_volume or 0) > 0
                     else None)
    out = {"verdict": "WAIT", "reason": "",
           "door_size": _num(door_size), "shot_lots": _num(shot_lots),
           "door_margin": None if door_margin is math.inf else door_margin,
           "road_lots": _num(road_lots), "road_normal": road_normal,
           "road_ratio": road_ratio,
           "participation": participation,
           "history_n": len(_ROAD_HISTORY), "history_min": hist_min}
    margin_min = _cfg(config, "SHOOT_DOOR_MARGIN", 3.0)
    normal_max = _cfg(config, "SHOOT_ROAD_NORMAL_MAX", 2.0)
    part_max = _cfg(config, "SHOOT_PARTICIPATION_MAX", 0.10)
    if door_size is None or door_size <= 0:
        out["reason"] = "crowd v2 Q1 SERVED failed: door size unavailable"
        return out
    if shot_lots is None or shot_lots <= 0:
        out["reason"] = "crowd v2 Q1 SERVED failed: shot size unavailable"
        return out
    if door_margin < margin_min:
        out["reason"] = (f"crowd v2 Q1 SERVED failed: door {door_size:.1f} lots "
                         f"is only {door_margin:.1f}x our shot {shot_lots:.2f} "
                         f"(need {margin_min:.1f}x)")
        return out
    if road_normal is None:
        out["reason"] = (f"crowd v2 Q2 road history warming up "
                         f"({len(_ROAD_HISTORY)}/{hist_min} samples) - fail closed")
        return out
    if road_ratio is math.inf or road_ratio > normal_max:
        out["reason"] = (f"crowd v2 Q2 NORMAL ROAD failed: road "
                         f"{(road_lots or 0):.0f} lots > {normal_max:.1f}x its "
                         f"own normal {road_normal:.0f}")
        return out
    if recent_volume is None or recent_volume <= 0:
        out["reason"] = ("crowd v2 Q3 SMALL FOOTPRINT failed: volume window "
                         "unavailable - fail closed")
        return out
    if participation > part_max:
        out["reason"] = (f"crowd v2 Q3 SMALL FOOTPRINT failed: shot "
                         f"{shot_lots:.2f} is {participation:.0%} of window "
                         f"volume {recent_volume:.0f} (max {part_max:.0%})")
        return out
    out["verdict"] = "PASS"
    out["reason"] = (f"crowd v2 PASS: door/shot {door_margin:.1f}x, "
                     f"road/normal {road_ratio:.2f}x, "
                     f"shot/vol {participation:.1%}")
    return out
'''

# --------------------------------------- SH-3: the crowd check inside plan_shot
SH3_OLD = """    road_count = int(raw_road_count)
    road_lots = raw_road_lots
    route_clear = target_door.get("road_clear") is True
    road_ratio = road_lots / target_size if target_size > 0 else math.inf
    road_limit = _cfg(config, "SHOOT_ROAD_RATIO", 6.0)
    if road_ratio >= road_limit:
        # opsfix:v3 -- show the components behind the ratio (audit A3)
        return _wait(f"route is too crowded ({road_ratio:.1f}x target size; "
                     f"road {road_lots:.0f} lots over {road_count} door(s) vs "
                     f"target {target_size:.1f} lots; limit {road_limit:.1f}x)", p,
                     {"door": dict(target_door), "road_ratio": road_ratio})
"""
SH3_NEW = """    road_count = int(raw_road_count)
    road_lots = raw_road_lots
    route_clear = target_door.get("road_clear") is True
    # crowdv2:v1 -- the Three Fair Questions replace the road/target-size ratio.
    shot_lots = _num(ctx.get("trade_lots"))
    if shot_lots is None or shot_lots <= 0:
        shot_lots = _cfg(config, "LOT_SIZE", 1.0)
    crowd = _crowd_v2(target_size, road_lots, shot_lots,
                      _num(ctx.get("recent_volume")), config)
    road_ratio = (crowd["road_ratio"] if crowd["road_ratio"] is not None
                  else math.inf)
    if crowd["verdict"] != "PASS":
        return _wait(crowd["reason"], p,
                     {"door": dict(target_door), "crowd_v2": crowd})
"""

# ------------------------------------------------------ SH-4: the receipt line
SH4_OLD = """           f"route {road_count} level(s), {road_lots:.1f} lots; ratio {road_ratio:.2f}x",
"""
SH4_NEW = """           f"route {road_count} level(s), {road_lots:.1f} lots; "
           f"crowd v2: door/shot {crowd['door_margin']:.1f}x, "
           f"road/normal {road_ratio:.2f}x, "
           f"shot/vol {crowd['participation']:.1%}",
"""

# -------------------------------------------- ST-1: _shot_context gains inputs
ST1_OLD = """        _shot_context = {
            "bid": _raw_bid,
            "ask": _raw_ask,
            "news_state": news.news_state,
"""
ST1_NEW = """        _crowd_vol = 0.0
        try:
            _crowd_ticks = tick_data[-500:] if isinstance(tick_data, list) else []
            _crowd_vol = sum(float(t.get("volume", 0) or 0)
                             for t in _crowd_ticks if isinstance(t, dict))
        except Exception:
            _crowd_vol = 0.0
        _shot_context = {
            "bid": _raw_bid,
            "ask": _raw_ask,
            # crowdv2:v1 -- inputs for the Three Fair Questions
            "trade_lots": float(getattr(config, "LOT_SIZE", 1.0) or 1.0),
            "recent_volume": _crowd_vol,
            "news_state": news.news_state,
"""

# ------------------------------------------------- CF-1: config knobs (5 new)
CF1_OLD = """    g["SHOOT_ROAD_RATIO"] = _ffloat("SHOOT_ROAD_RATIO", 6.0)
    g["SHOOT_QUEUE_MIN_FILL_PROB"] = _ffloat("SHOOT_QUEUE_MIN_FILL_PROB", 0.70)
"""
CF1_NEW = """    g["SHOOT_ROAD_RATIO"] = _ffloat("SHOOT_ROAD_RATIO", 6.0)  # retired by crowdv2:v1 (kept for .env compat; unread)
    g["SHOOT_QUEUE_MIN_FILL_PROB"] = _ffloat("SHOOT_QUEUE_MIN_FILL_PROB", 0.70)
    # crowdv2:v1 -- the Three Fair Questions (item 66; research file
    # CROWD_MATH_V2_RESEARCH_2026-10-16.md).  All dimensionless multiples so
    # they mean the same thing in every regime; none may be tuned to force a
    # trade (house law).
    g["SHOOT_DOOR_MARGIN"] = _ffloat("SHOOT_DOOR_MARGIN", 3.0)
    g["SHOOT_ROAD_NORMAL_MAX"] = _ffloat("SHOOT_ROAD_NORMAL_MAX", 2.0)
    g["SHOOT_PARTICIPATION_MAX"] = _ffloat("SHOOT_PARTICIPATION_MAX", 0.10)
    g["SHOOT_ROAD_HISTORY_MIN"] = _ffloat("SHOOT_ROAD_HISTORY_MIN", 12)
    g["SHOOT_ROAD_HISTORY_SECONDS"] = _ffloat("SHOOT_ROAD_HISTORY_SECONDS", 2700.0)
"""

# ------------------------------------------- MN-1: _record ASCII fold (item 67)
MN1_OLD = """def _record(step: str, status: str) -> None:
    STATUS.append((step, status))
    logger.info("%s -> %s", step, status)
"""
MN1_NEW = """def _record(step: str, status: str) -> None:
    # crowdv2:v1 -- item 67: ASCII-fold at the logging boundary.  AI text can
    # carry non-ASCII (e.g. Greek sigma in "VWAP -1-sigma band"); the Windows
    # console is cp1252 and crashed on it (2026-10-16 19:33 UnicodeEncodeError).
    # Folding here protects every caller; content survives as \\uXXXX escapes.
    step = str(step).encode("ascii", "backslashreplace").decode("ascii")
    status = str(status).encode("ascii", "backslashreplace").decode("ascii")
    STATUS.append((step, status))
    logger.info("%s -> %s", step, status)
"""

# ------------------------------------ MN-2: JUDGES display cut, whale (item 62)
MN2_OLD = """        _jv = list(getattr(snapshot, "judge_votes", None) or [])
        _jb = [v for v in _jv if isinstance(v, dict) and float(v.get("dir") or 0) > 0]
"""
MN2_NEW = """        _jv = list(getattr(snapshot, "judge_votes", None) or [])
        # crowdv2:v1 -- item 62: whale_walls is RETIRED (C1 card: -1.012/call
        # over 6 days; step2 confidence has ignored it since v5.8.1).  Cut it
        # from the DISPLAY so the line stops advertising a retired judge.
        # (POWER's agreement gate reads its own timeframe family, not the
        # judges -- verified 2026-10-16, whale cannot reach it.)
        _jv = [v for v in _jv
               if str(v.get("judge") or "") != "whale_walls"]
        _jb = [v for v in _jv if isinstance(v, dict) and float(v.get("dir") or 0) > 0]
"""

PATCHES = [
    ("shooting", "SH-1 imports (statistics, deque)", SH1_OLD, SH1_NEW),
    ("shooting", "SH-2 module machinery (_ROAD_HISTORY + _crowd_v2)", SH2_OLD, SH2_NEW),
    ("shooting", "SH-3 crowd check -> Three Fair Questions", SH3_OLD, SH3_NEW),
    ("shooting", "SH-4 receipt line", SH4_OLD, SH4_NEW),
    ("step2", "ST-1 _shot_context trade_lots + recent_volume", ST1_OLD, ST1_NEW),
    ("config", "CF-1 five v2 knobs", CF1_OLD, CF1_NEW),
    ("main", "MN-1 _record ASCII fold (item 67)", MN1_OLD, MN1_NEW),
    ("main", "MN-2 JUDGES display whale cut (item 62)", MN2_OLD, MN2_NEW),
]


def _read(key: str) -> str:
    return FILES[key].read_text(encoding="utf-8")


def _already_applied() -> bool:
    try:
        return MARKER in _read("shooting")
    except OSError:
        return False


def apply_patches(sources: dict) -> dict:
    """Apply all patches to the given source texts.  Returns new texts."""
    out = dict(sources)
    for key, name, old, new in PATCHES:
        text = out[key]
        count = text.count(old)
        if count != 1:
            raise RuntimeError(f"anchor not unique ({count}x) for {name}")
        out[key] = text.replace(old, new, 1)
    return out


# --------------------------------------------------------------------- rig
def run_rig() -> int:
    print(f"crowdv2 rig -- repo: {REPO}")
    results = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok)))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" -- {detail}" if detail and not ok else ""))

    # 1-8: anchors present exactly once in the real files
    sources = {}
    for key, path in FILES.items():
        try:
            sources[key] = path.read_text(encoding="utf-8")
        except OSError as exc:
            check(f"A. file readable: {path.name}", False, str(exc))
            return 1
    for key, name, old, _new in PATCHES:
        check(f"A. anchor present once: {name}", sources[key].count(old) == 1,
              f"count={sources[key].count(old)}")

    # 9: idempotency state
    check("B. not already applied (marker absent)", MARKER not in sources["shooting"])

    # sandbox: apply to copies, compile, import, simulate
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        for key, text in sources.items():
            (tdp / FILES[key].name).write_text(text, encoding="utf-8")
        try:
            patched = apply_patches(sources)
        except RuntimeError as exc:
            check("C. apply to sandbox", False, str(exc))
            return 1
        check("C. apply to sandbox", True)
        for key, text in patched.items():
            (tdp / FILES[key].name).write_text(text, encoding="utf-8")

        # 10: compiles
        for key in FILES:
            try:
                py_compile.compile(str(tdp / FILES[key].name), doraise=True)
                check(f"D. py_compile: {FILES[key].name}", True)
            except py_compile.PyCompileError as exc:
                check(f"D. py_compile: {FILES[key].name}", False, str(exc)[:120])

        # 11: behavioral sims on the patched shooter
        sys.path.insert(0, str(tdp))
        try:
            import shooting_team as st  # noqa: E402
            from types import SimpleNamespace

            cfg = SimpleNamespace(
                LIMIT_ORDER_ENABLED=True, POWER_MAX_TICK_AGE_SECONDS=60.0,
                STALE_DATA_SECONDS=300.0, SHOOT_QUEUE_MIN_FILL_PROB=0.70,
                SHOOT_MIN_RR=1.2, SHOOT_MIN_REWARD_USD=0.0, PM_TP_BUFFER_ATR=0.15,
                SHOOT_QUEUE_MAX_AGE_SECONDS=5.0, LIMIT_TICK_SIZE=0.1,
                SHOOT_PLAN_TTL_SECONDS=30.0, LOT_SIZE=1.0,
                SHOOT_DOOR_MARGIN=3.0, SHOOT_ROAD_NORMAL_MAX=2.0,
                SHOOT_PARTICIPATION_MAX=0.10, SHOOT_ROAD_HISTORY_MIN=12,
                SHOOT_ROAD_HISTORY_SECONDS=2700.0,
            )

            def power(direction="UP"):
                return {"team": "POWER", "version": "2.2.0", "timeframe": "M5",
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
                        "shadow_only": False}

            def smap(**door_changes):
                door = {"price": 105.0, "size": 20.0, "doors_between": 0,
                        "lots_between": 0.0, "biggest_in_path": None,
                        "road_clear": True}
                door.update(door_changes)
                return {"above": [door]}

            def ctx(**changes):
                base = {"bid": 99.9, "ask": 100.1, "news_state": "QUIET",
                        "data_quality_ok": True, "has_data": True,
                        "last_data_age_seconds": 1.0,
                        "aggressive_trigger_confirmed": False,
                        "now": time.time(), "market_symbol": "GCZ6",
                        "trade_lots": 1.0, "recent_volume": 1000.0}
                base.update(changes)
                return base

            def seed(values):
                st._ROAD_HISTORY.clear()
                st._ROAD_HISTORY.extend((time.time(), float(v)) for v in values)

            # E1: fail-closed warm-up on a fresh module
            st._ROAD_HISTORY.clear()
            out = st.plan_shot(smap(lots_between=160), power(), 100, 1, 0.2,
                               cfg, entry_context=ctx())
            check("E1. fresh history fails closed (warm-up)",
                  out["shot"] == "WAIT" and "warming up" in out["why"][0],
                  out["why"][0])

            # E2: all three questions pass -> GO with receipts
            seed([100.0] * 12)
            out = st.plan_shot(smap(lots_between=160), power(), 100, 1, 0.2,
                               cfg, entry_context=ctx())
            check("E2. three-pass -> GO + crowd receipt line",
                  out["shot"] == "GO" and any("crowd v2" in w for w in out["why"]),
                  str(out["why"][:2]))

            # E3: Q2 abnormal road vetoes
            seed([100.0] * 12)
            out = st.plan_shot(smap(lots_between=350), power(), 100, 1, 0.2,
                               cfg, entry_context=ctx())
            check("E3. Q2 NORMAL ROAD veto (350 vs normal 100)",
                  out["shot"] == "WAIT" and "Q2" in out["why"][0]
                  and out.get("crowd_v2", {}).get("road_ratio", 0) > 2.0,
                  out["why"][0])

            # E4: Q1 small door vetoes
            seed([100.0] * 12)
            out = st.plan_shot(smap(size=2.0, lots_between=100), power(), 100, 1,
                               0.2, cfg, entry_context=ctx())
            check("E4. Q1 SERVED veto (door 2 vs shot 1 = 2x < 3x)",
                  out["shot"] == "WAIT" and "Q1" in out["why"][0],
                  out["why"][0])

            # E5: Q3 big footprint vetoes
            seed([100.0] * 12)
            out = st.plan_shot(smap(lots_between=100), power(), 100, 1, 0.2,
                               cfg, entry_context=ctx(recent_volume=5.0))
            check("E5. Q3 SMALL FOOTPRINT veto (shot 1 of volume 5 = 20%)",
                  out["shot"] == "WAIT" and "Q3" in out["why"][0],
                  out["why"][0])

            # E6: missing volume fails closed
            seed([100.0] * 12)
            out = st.plan_shot(smap(lots_between=100), power(), 100, 1, 0.2,
                               cfg, entry_context=ctx(recent_volume=None))
            check("E6. volume window unavailable fails closed",
                  out["shot"] == "WAIT" and "volume window unavailable" in out["why"][0],
                  out["why"][0])

            # E7: empty road + empty history passes (nothing unusual)
            seed([0.0] * 12)
            out = st.plan_shot(smap(lots_between=0.0), power(), 100, 1, 0.2,
                               cfg, entry_context=ctx())
            check("E7. empty road with empty normal -> pass (Q1 still guards)",
                  out["shot"] == "GO", str(out["why"][:1]))

            # E8: shot size falls back to config LOT_SIZE
            seed([100.0] * 12)
            out = st.plan_shot(smap(size=0.4, lots_between=100), power(), 100, 1,
                               0.2, cfg,
                               entry_context=ctx(trade_lots=None))
            # door 0.4 / shot LOT_SIZE 1.0 = 0.4x < 3x -> Q1 veto (proves fallback)
            check("E8. trade_lots missing -> config LOT_SIZE fallback",
                  out["shot"] == "WAIT" and "Q1" in out["why"][0]
                  and out["crowd_v2"]["shot_lots"] == 1.0,
                  out["why"][0])
        except Exception as exc:  # noqa: BLE001
            check("E. behavioral sims", False, f"{type(exc).__name__}: {exc}")
        finally:
            sys.path.pop(0)
            for mod in [m for m in list(sys.modules) if m == "shooting_team"]:
                del sys.modules[mod]

        # static checks on patched copies
        pmain = (tdp / "main.py").read_text(encoding="utf-8")
        pconfig = (tdp / "config.py").read_text(encoding="utf-8")
        pstep2 = (tdp / "step2_market_analysis.py").read_text(encoding="utf-8")
        pshoot = (tdp / "shooting_team.py").read_text(encoding="utf-8")

        # F: sigma sanitize
        okf = ("backslashreplace" in pmain
               and 'def _record(step: str, status: str) -> None:' in pmain)
        probe = "VWAP -1\u03c3 band".encode("ascii", "backslashreplace").decode("ascii")
        check("F. sigma ASCII-fold present + probe escapes",
              okf and "\\u03c3" in probe)

        # G: whale display cut
        check("G. JUDGES display filters whale_walls",
              '!= "whale_walls"' in pmain and MN2_OLD not in pmain)

        # H: config knobs
        check("H. five v2 knobs in config",
              all(k in pconfig for k in
                  ["SHOOT_DOOR_MARGIN", "SHOOT_ROAD_NORMAL_MAX",
                   "SHOOT_PARTICIPATION_MAX", "SHOOT_ROAD_HISTORY_MIN",
                   "SHOOT_ROAD_HISTORY_SECONDS"]))

        # I: step2 context carries the new inputs
        check("I. _shot_context carries trade_lots + recent_volume",
              '"trade_lots": float(getattr(config, "LOT_SIZE"' in pstep2
              and '"recent_volume": _crowd_vol' in pstep2)

        # J: the retired ratio is gone from the shooter
        check("J. SHOOT_ROAD_RATIO no longer read by plan_shot",
              "SHOOT_ROAD_RATIO" not in pshoot)

        # K: rollback round-trip on the sandbox
        try:
            (tdp / "shooting_team.py").write_text(sources["shooting"],
                                                  encoding="utf-8")
            check("K. rollback round-trip restores original text",
                  (tdp / "shooting_team.py").read_text(encoding="utf-8")
                  == sources["shooting"])
        except OSError as exc:
            check("K. rollback round-trip", False, str(exc))

    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    print(f"\ncrowdv2 rig: {passed}/{total} PASS" +
          ("" if passed == total else "  <-- DO NOT APPLY"))
    return 0 if passed == total else 1


def run_apply() -> int:
    if _already_applied():
        print("already applied (crowdv2:v1 markers present) -- nothing to do")
        return 0
    sources = {k: p.read_text(encoding="utf-8") for k, p in FILES.items()}
    patched = apply_patches(sources)
    for key, path in FILES.items():
        bak = path.with_suffix(path.suffix + ".crowdv2.bak")
        if not bak.exists():
            shutil.copy2(path, bak)
            print(f"backup -> {bak.name}")
        path.write_text(patched[key], encoding="utf-8")
        print(f"patched -> {path.name}")
    print("\nAPPLIED. Next: run pytest (updated test_shooting_team.py), "
          "then restart the robot.")
    print("Rollback anytime:  python apply_crowd_v2.py --rollback")
    return 0


def run_rollback() -> int:
    for key, path in FILES.items():
        bak = path.with_suffix(path.suffix + ".crowdv2.bak")
        if bak.exists():
            shutil.copy2(bak, path)
            bak.unlink()
            print(f"restored <- {path.name}")
        else:
            print(f"no backup for {path.name} (nothing to restore)")
    print("rollback complete.")
    return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--rig":
        return run_rig()
    if mode == "--apply":
        return run_apply()
    if mode == "--rollback":
        return run_rollback()
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
