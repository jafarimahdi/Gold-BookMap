#!/usr/bin/env python3
"""
apply_ops_fix3.py -- OPS FIX v3: FULL VISIBILITY (the owner's complete order)
==============================================================================
(2026-10-15 night. Owner: "I want ALL of these visible in each cycle, live.")

v1.3 (2026-10-15 ~21:45): RIG ISOLATION FIX -- the v1.2 rig wrote its 3.0MB
  test fixture into the REAL data/power_v2_memory/ day-file and never removed
  it -> the live book died (MemoryIntegrityError, fail-closed; "memory=error"
  every cycle; "book: today 3.0MB" was the garbage). The INSTALL sections are
  UNCHANGED (v1.2 install = current; [skip] if already installed). The rig now
  runs every fixture inside a TEMP working dir and adds check 13: a before/
  after state-diff proving the real data/ was untouched. If the v1.2 rig ever
  ran on a machine, run fix_memory_book.py there first (repairs the day-file).

REQUIRES ops fix v1 AND v2 (apply_ops_fix.py + apply_ops_fix2.py) first.

WHAT YOU GET - four new/extended lines EVERY cycle, all read-only:

  A2 LANE (opsfix3)   enabled=True fires 0/10 today | eye1 lean: DOWN +0.41 |
                      eye2 panel: SELL (84s ago) | ready
      -> the fast lane's three eyes, live (A1 of the audit). Eye 3 (scout door)
         still speaks through the SHOOTER note - it lives in plan_shot.

  ARMOR (opsfix3)     guard: 0 trades today/999 | risk: no halt | spread: 0.20
                      pts (0.005%) | flatten in 2.4h | disk free 38GB | book:
                      today 2.1MB | MTF H1:UP M15:DOWN M5:DOWN | maintenance ran 2026-10-15
      -> the guards' armor state (B5/B6/B7/B8) + the bookshelves (C9) + the
         silent servant (D10). The spread shown is the REAL book spread from
         bid/ask (the 0.000% display stays broken until the design session -
         see audit O17d; the CFD-basis orphan stays unwired, needs an MT5 tick).

  STEP 3 line now ends with the doorman's rationale tail (A6) - so the
      "[L3 CONFIRMS ... +X%]" / "L3 conflict" word is finally visible live.

  POWER note now carries fam: (A4) - the top-3 family scores by magnitude.

  The brake notes now carry NUMBERS (A2/O14): "confidence 49.6->44.6 (brake)".
  The crowd veto now carries COMPONENTS (A3): "road 174 lots over 12 door(s)
      vs target 4.2 lots".

NOT touched (unchanged law): every gate, every threshold, every decision.
The news-ladder detail and the score arithmetic stay evening-paper territory
(the votes are already itemized in notes; the events list is in the report).

Safety: project root only; refuses on ANY anchor mismatch (writes NOTHING);
idempotent ([skip]); backups *.pre_opsfix3; py_compile + auto-restore;
--rig proves with real checks; --rollback restores.

Run from the project root:
    python apply_ops_fix3.py             # install (expect [ok] x3 files)
    python apply_ops_fix3.py --rig       # prove it
    python apply_ops_fix3.py --rollback  # back to ops-v2 state
"""
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path.cwd()
SUFFIX = ".pre_opsfix3"

# ================================================== main.py edit 1: the lines =
JUDGES_TAIL = '''        _record("JUDGES (opsfix2)",
                f"{len(_jb)} BUY / {len(_js)} SELL / {_jf} flat of {len(_jv)} | "
                f"top BUY: {_jtop(_jb)} | top SELL: {_jtop(_js)}")
    except Exception as _j_err:
        _record("JUDGES (opsfix2)", f"(status unavailable: {type(_j_err).__name__})")
    # opsfix:v2-judges-end'''

V3_BLOCK = JUDGES_TAIL + '''
    # opsfix:v3-a2-start -- the fast lane's eyes, live (audit A1)
    try:
        import datetime as _v3_dt
        from aggressive_trigger import EYES as _v3_eyes, a2_fire_count as _v3_fires
        _v3_now = _v3_dt.datetime.now(_v3_dt.timezone.utc)
        _v3_max = int(getattr(config, "A2_BURST_MAX_PER_DAY", 10) or 10)
        # opsfix:v3.2-enabled: mirror the REAL gate (_enabled in aggressive_trigger):
        # os.environ FIRST (.env loads there via load_dotenv), then the config
        # attr (which config.py does not define for this key), same acceptance
        # set ("1", "1.0", "true"...). v1.1 read only the config attr and
        # wrongly showed enabled=False while the lane was ON.
        _v3_raw = None
        try:
            import os as _v3_os
            _v3_raw = _v3_os.environ.get("AGGRESSIVE_ENTRY_ENABLED")
        except Exception:
            pass
        if _v3_raw is None:
            _v3_raw = getattr(config, "AGGRESSIVE_ENTRY_ENABLED", 0)
        _v3_enabled = str(_v3_raw).strip() in ("1", "1.0", "true", "True", "TRUE")
        _v3_lean = None
        try:
            _v3_lean = _v3_eyes.lean(_v3_now)
        except Exception:
            _v3_lean = None
        if isinstance(_v3_lean, dict):
            _v3_ls = _v3_lean.get("strength", _v3_lean.get("value",
                        _v3_lean.get("ema3", 0.0)))
            try:
                _v3_lean_s = f"{_v3_lean.get('direction')} {float(_v3_ls):+.2f}"
            except Exception:
                _v3_lean_s = str(_v3_lean.get("direction"))
        else:
            _v3_lean_s = "none"
        _v3_panel_s = "none"
        try:
            if _v3_eyes.panel:
                _v3_t, _v3_d = _v3_eyes.panel[-1]
                _v3_panel_s = f"{_v3_d} ({(_v3_now - _v3_t).total_seconds():.0f}s ago)"
        except Exception:
            pass
        _v3_cool_s = "ready"
        try:
            if _v3_eyes.last_fire:
                _v3_cm = float(getattr(config, "A2_BURST_COOLDOWN_MINUTES", 5) or 5)
                _v3_age = (_v3_now - _v3_eyes.last_fire).total_seconds() / 60.0
                _v3_cool_s = (f"cooldown {max(0.0, _v3_cm - _v3_age):.0f} min"
                              if _v3_age < _v3_cm else "ready")
        except Exception:
            pass
        _record("A2 LANE (opsfix3)",
                f"enabled={_v3_enabled} fires {_v3_fires(_v3_now)}/{_v3_max} today | "
                f"eye1 lean: {_v3_lean_s} | eye2 panel: {_v3_panel_s} | {_v3_cool_s}")
    except Exception as _v3_err:
        _record("A2 LANE (opsfix3)", f"(status unavailable: {type(_v3_err).__name__})")
    # opsfix:v3-a2-end
    # opsfix:v3-armor-start -- the guards' armor + the bookshelves (B5-B8/C9/D10)
    try:
        import datetime as _v3_dt, json as _v3_json, shutil as _v3_shutil
        _v3_now = _v3_dt.datetime.now(_v3_dt.timezone.utc)
        _v3_dd = Path("data")
        _v3_tg = {}
        try:
            _v3_tg = _v3_json.loads((_v3_dd / "trade_guard_state.json")
                                    .read_text(encoding="utf-8"))
        except Exception:
            pass
        # opsfix:v3-armor-datefix: the state file keeps OLD days too - only
        # count trades when the file's date is TODAY (local), exactly like
        # trade_guard._today_state does; else a stale count shows as "today".
        _v3_trades = 0
        try:
            if _v3_tg.get("date") == _v3_dt.datetime.now().astimezone().date().isoformat():
                _v3_trades = int(_v3_tg.get("trades_today", 0) or 0)
        except Exception:
            pass
        _v3_cap = int(getattr(config, "MAX_TRADES_PER_DAY", 0) or 0)
        _v3_guard_s = f"guard: {_v3_trades} trades today"
        if _v3_cap:
            _v3_guard_s += f"/{_v3_cap}"
        _v3_risk_s = "risk: no halt"
        try:
            _v3_rs = _v3_json.loads((_v3_dd / "risk_state.json")
                                    .read_text(encoding="utf-8"))
            if _v3_rs.get("halt_until"):
                _v3_hu = None
                try:
                    _v3_hu = _v3_dt.datetime.fromisoformat(str(_v3_rs["halt_until"]))
                    if _v3_hu.tzinfo is None:
                        _v3_hu = _v3_hu.replace(tzinfo=_v3_dt.timezone.utc)
                except Exception:
                    _v3_hu = None
                # an EXPIRED halt reads as "no halt" (same as risk_manager.check);
                # an unparseable one still shows, so it is never hidden
                if _v3_hu is None or _v3_hu > _v3_now:
                    _v3_risk_s = (f"risk: HALT until {_v3_rs.get('halt_until')} "
                                  f"({_v3_rs.get('reason', '')})")
        except Exception:
            pass
        _v3_bid = float(getattr(snapshot, "bid", 0) or 0)
        _v3_ask = float(getattr(snapshot, "ask", 0) or 0)
        if _v3_ask > _v3_bid > 0:
            _v3_pts = _v3_ask - _v3_bid
            _v3_spread_s = f"spread: {_v3_pts:.2f} pts ({100.0 * _v3_pts / _v3_bid:.3f}%)"
        else:
            _v3_spread_s = "spread: n/a"
        _v3_flat = _v3_now.replace(hour=21, minute=30, second=0, microsecond=0)
        if _v3_flat < _v3_now:
            _v3_flat = _v3_flat + _v3_dt.timedelta(days=1)
        _v3_flat_s = f"flatten in {(_v3_flat - _v3_now).total_seconds() / 3600.0:.1f}h"
        try:
            _v3_free = _v3_shutil.disk_usage(".").free / (1024 ** 3)
            _v3_disk_s = f"disk free {_v3_free:.0f}GB"
        except Exception:
            _v3_disk_s = "disk n/a"
        # opsfix:v3.2-book: the book writes ONE file per DAY (power_YYYY-MM-DD
        # .jsonl); the pages are LINES inside it. v1.1 counted files ("pages: 1").
        # Report today's file size - O(1), no re-read of a growing file.
        _v3_mem_s = "book: none yet"
        try:
            _v3_mf = (_v3_dd / "power_v2_memory"
                      / f"power_{_v3_now.date().isoformat()}.jsonl")
            if _v3_mf.exists():
                _v3_mem_s = f"book: today {_v3_mf.stat().st_size / 1048576:.1f}MB"
        except Exception:
            pass
        _v3_mtf = " ".join(f"{_k}:{_v}" for _k, _v in
                           sorted((getattr(snapshot, "mtf_trends", None) or {}).items())
                           ) or "n/a"
        _v3_maint_s = "maintenance: never/state n/a"
        try:
            _v3_ms = _v3_json.loads((_v3_dd / "maintenance_state.json")
                                    .read_text(encoding="utf-8"))
            _v3_ran = _v3_ms.get("last_run", _v3_ms.get("date", "?"))
            _v3_maint_s = f"maintenance ran {_v3_ran}"
        except Exception:
            pass
        _record("ARMOR (opsfix3)",
                f"{_v3_guard_s} | {_v3_risk_s} | {_v3_spread_s} | {_v3_flat_s} | "
                f"{_v3_disk_s} | {_v3_mem_s} | MTF {_v3_mtf} | {_v3_maint_s}")
    except Exception as _v3_err:
        _record("ARMOR (opsfix3)", f"(status unavailable: {type(_v3_err).__name__})")
    # opsfix:v3-armor-end'''

# ==================================== main.py edit 2: the doorman's tail (A6) =
S3_OLD = '''    _record("STEP 3  AI DECISION", f"{decision.action} @ {decision.confidence:.1f}%")'''
S3_NEW = '''    _rat_txt = str(getattr(decision, "rationale", "") or "")[-110:]
    if " " in _rat_txt:
        _rat_txt = _rat_txt[_rat_txt.find(" ") + 1:]      # opsfix:v3.2: no mid-word cuts
    _record("STEP 3  AI DECISION",
            f"{decision.action} @ {decision.confidence:.1f}%"
            + (f" | {_rat_txt}" if _rat_txt.strip() else ""))'''

# ============================================== step2 edit 1: the brake (A2) ==
BRAKE_OLD = '''        if news.news_state == "WARNING":
            confidence *= 0.7
            notes.append("news WARNING -> confidence reduced")
        if news.impact_level == "HIGH":
            confidence *= 0.9
            notes.append("high-impact event upcoming -> confidence reduced")'''
BRAKE_NEW = '''        if news.news_state == "WARNING":
            _conf_before = confidence
            confidence *= 0.7
            notes.append(f"news WARNING -> confidence {_conf_before:.1f}->{confidence:.1f} (brake)")
        if news.impact_level == "HIGH":
            _conf_before = confidence
            confidence *= 0.9
            notes.append(f"high-impact event upcoming -> confidence {_conf_before:.1f}->{confidence:.1f} (brake)")'''

# ==================================== step2 edit 2: family scores in POWER (A4)
FAMVAR_ANCHOR = '''    _gate_status = lambda _gate: "PASS" if _gate.get("pass", False) else "FAIL"'''
FAMVAR_NEW = '''    _gate_status = lambda _gate: "PASS" if _gate.get("pass", False) else "FAIL"
    # opsfix:v3-fam -- top-3 POWER family scores by magnitude (audit A4)
    try:
        _fam_txt = ", ".join(
            f"{_fk} {_fv.get('score', 0.0):+.2f}"
            for _fk, _fv in sorted((_power.get("family_scores") or {}).items(),
                                   key=lambda _kv: -abs(float((_kv[1] or {}).get("score", 0.0) or 0.0)))[:3]
        ) or "n/a"
    except Exception:
        _fam_txt = "n/a"'''

FAMLINE_OLD = '''        f"activity={_power.get('activity', 0.0):.3f} coverage={_power.get('coverage', 0.0):.2f} | "'''
FAMLINE_NEW = '''        f"activity={_power.get('activity', 0.0):.3f} coverage={_power.get('coverage', 0.0):.2f} | "
        f"fam: {_fam_txt} | "'''

# ======================================= shooting: crowd components (A3) ======
CROWD_OLD = '''    if road_ratio >= road_limit:
        return _wait(f"route is too crowded ({road_ratio:.1f}x target size; limit {road_limit:.1f}x)", p,
                     {"door": dict(target_door), "road_ratio": road_ratio})'''
CROWD_NEW = '''    if road_ratio >= road_limit:
        # opsfix:v3 -- show the components behind the ratio (audit A3)
        return _wait(f"route is too crowded ({road_ratio:.1f}x target size; "
                     f"road {road_lots:.0f} lots over {road_count} door(s) vs "
                     f"target {target_size:.1f} lots; limit {road_limit:.1f}x)", p,
                     {"door": dict(target_door), "road_ratio": road_ratio})'''


def py_ok(path):
    try:
        subprocess.run([sys.executable, "-m", "py_compile", str(path)],
                       check=True, capture_output=True)
        return True
    except Exception:
        return False


def install():
    if not (ROOT / "main.py").exists():
        print("ERROR: run this from the project root (the folder with main.py)")
        return 1
    main = ROOT / "main.py"
    src = main.read_text(encoding="utf-8", errors="surrogateescape")
    if "opsfix:v3-a2-start" in src:
        if "opsfix:v3-armor-datefix" in src and "opsfix:v3.2-enabled" in src:
            print("[skip] main.py: ops fix v3 (v1.2 install = current; this patcher is v1.3: isolated rig)")
            return 0
        # v1.0/v1.1 is installed but lacks the v1.2 line fixes -> auto-repair:
        # restore the .pre_opsfix3 backups (back to the v2 state), then
        # install fresh. One command, no manual rollback needed.
        print("[repair] older ops-v3 found (pre-v1.2) -> restoring backups, re-applying...")
        for _rel in ("main.py", "step2_market_analysis.py", "shooting_team.py"):
            _p = ROOT / _rel
            _b = Path(str(_p) + SUFFIX)
            if _b.exists():
                shutil.copy2(_b, _p)
            elif _rel == "main.py":
                print("[STOP] main.py.pre_opsfix3 backup missing - run --rollback, paste output.")
                return 1
        src = main.read_text(encoding="utf-8", errors="surrogateescape")
    if "opsfix:v2-judges-end" not in src:
        print("[STOP] ops fix v2 is not installed - run apply_ops_fix2.py first. (Nothing written.)")
        return 1
    if src.count(JUDGES_TAIL) != 1:
        print("[STOP] the ops-fix-v2 JUDGES tail was not found exactly once in main.py.")
        print("       Paste this output to the assistant. (Nothing written.)")
        return 1
    src = src.replace(JUDGES_TAIL, V3_BLOCK, 1)
    if src.count(S3_OLD) != 1:
        print("[STOP] the STEP 3 record line was not found exactly once in main.py.")
        print("       (Nothing written.)")
        return 1
    src = src.replace(S3_OLD, S3_NEW, 1)
    bak = Path(str(main) + SUFFIX)
    if not bak.exists():
        shutil.copy2(main, bak)
    main.write_text(src, encoding="utf-8", errors="surrogateescape")
    if not py_ok(main):
        shutil.copy2(bak, main)
        print("[FAIL] main.py py_compile failed -> backup restored, nothing changed")
        return 1
    print(f"[ok] main.py: 2 edits (A2 lane + armor lines, doorman tail), backup {bak.name}")

    step2 = ROOT / "step2_market_analysis.py"
    if not step2.exists():
        print("[miss] step2_market_analysis.py not found - skipped")
    else:
        s2 = step2.read_text(encoding="utf-8", errors="surrogateescape")
        bad = []
        if s2.count(BRAKE_OLD) != 1:
            bad.append("brake block")
        if s2.count(FAMVAR_ANCHOR) != 1:
            bad.append("family var anchor")
        if s2.count(FAMLINE_OLD) != 1:
            bad.append("family line anchor")
        if bad:
            print(f"[STOP] step2_market_analysis.py: anchor(s) not found -> NOTHING written: {', '.join(bad)}")
            print("       Paste this output to the assistant.")
        else:
            s2 = s2.replace(BRAKE_OLD, BRAKE_NEW, 1)
            s2 = s2.replace(FAMVAR_ANCHOR, FAMVAR_NEW, 1)
            s2 = s2.replace(FAMLINE_OLD, FAMLINE_NEW, 1)
            b2 = Path(str(step2) + SUFFIX)
            if not b2.exists():
                shutil.copy2(step2, b2)
            step2.write_text(s2, encoding="utf-8", errors="surrogateescape")
            if not py_ok(step2):
                shutil.copy2(b2, step2)
                print("[FAIL] step2 py_compile failed -> backup restored")
                return 1
            print(f"[ok] step2_market_analysis.py: 2 edits (brake numbers + fam scores), backup {b2.name}")

    shoot = ROOT / "shooting_team.py"
    if not shoot.exists():
        print("[miss] shooting_team.py not found - skipped")
    else:
        ss = shoot.read_text(encoding="utf-8", errors="surrogateescape")
        if ss.count(CROWD_OLD) != 1:
            print("[STOP] shooting_team.py: crowd anchor not found exactly once -> NOTHING written.")
            print("       Paste this output to the assistant.")
        else:
            ss = ss.replace(CROWD_OLD, CROWD_NEW, 1)
            b3 = Path(str(shoot) + SUFFIX)
            if not b3.exists():
                shutil.copy2(shoot, b3)
            shoot.write_text(ss, encoding="utf-8", errors="surrogateescape")
            if not py_ok(shoot):
                shutil.copy2(b3, shoot)
                print("[FAIL] shooting_team py_compile failed -> backup restored")
                return 1
            print(f"[ok] shooting_team.py: 1 edit (crowd components), backup {b3.name}")

    print("\nNext: prove it ->   python apply_ops_fix3.py --rig")
    print("Then RESTART the robot, and push:")
    print("  git add main.py step2_market_analysis.py shooting_team.py apply_ops_fix3.py")
    return 0


def rig():
    print("=" * 78)
    print("OPS FIX v3 RIG (v1.3, isolated) - proving the install with real checks")
    print("=" * 78)
    results = []

    def _watched_state():
        """v1.3: the isolation witness -- names+sizes+mtimes of every real
        file the rig's fixtures could touch. The rig must leave this snapshot
        byte-identical (check 13). Run --rig with the robot STOPPED."""
        watched = {}
        mem_dir = ROOT / "data" / "power_v2_memory"
        if mem_dir.is_dir():
            for p in sorted(mem_dir.glob("power_*.jsonl")):
                st = p.stat()
                watched[str(p.relative_to(ROOT))] = (st.st_size, st.st_mtime_ns)
        for name in ("trade_guard_state.json", "risk_state.json",
                     "maintenance_state.json"):
            p = ROOT / "data" / name
            if p.is_file():
                st = p.stat()
                watched[str(p.relative_to(ROOT))] = (st.st_size, st.st_mtime_ns)
        return watched

    _pre_state = _watched_state()

    def check(name, cond):
        results.append((name, bool(cond)))
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")

    main = ROOT / "main.py"
    ms = main.read_text(encoding="utf-8", errors="ignore") if main.exists() else ""
    check("marker: A2 lane block", "opsfix:v3-a2-start" in ms and "opsfix:v3-a2-end" in ms)
    check("marker: armor block", "opsfix:v3-armor-start" in ms and "opsfix:v3-armor-end" in ms)
    check("marker: v2 blocks still intact", "opsfix:v2-judges-end" in ms and "opsfix:v1-start" in ms)
    check("py_compile main.py", py_ok(main))

    s2 = ROOT / "step2_market_analysis.py"
    s2s = s2.read_text(encoding="utf-8", errors="ignore") if s2.exists() else ""
    check("marker: brake numbers in step2", "(brake)\")" in s2s.replace('f"', '') or "_conf_before:.1f}" in s2s)
    check("marker: fam scores in step2", "opsfix:v3-fam" in s2s and "fam: {_fam_txt}" in s2s)
    check("py_compile step2_market_analysis.py", py_ok(s2))

    sh = ROOT / "shooting_team.py"
    shs = sh.read_text(encoding="utf-8", errors="ignore") if sh.exists() else ""
    check("marker: crowd components in shooting", "opsfix:v3 -- show the components" in shs)
    check("py_compile shooting_team.py", py_ok(sh))

    # behavioral: run the REAL A2+armor blocks with stubs
    _real_cwd = None      # v1.3: set once the temp dir exists; the finally is
    _tmpdir = None        # safe even if the try fails before that
    try:
        import types, textwrap, datetime, logging
        block = ms.split("opsfix:v3-a2-start", 1)[1].split("opsfix:v3-armor-end", 1)[0]
        code = textwrap.dedent("\n".join(block.splitlines()[1:]))
        recorded = []

        # stub the eyes module BEFORE exec (the block imports it)
        eyes_mod = types.ModuleType("aggressive_trigger")
        now = datetime.datetime.now(datetime.timezone.utc)

        class _FakeLean(dict):
            pass

        eyes_mod.EYES = types.SimpleNamespace(
            lean=lambda n: {"direction": "DOWN", "strength": 0.41},
            panel=[(now, "SELL")],
            last_fire=None)
        eyes_mod.a2_fire_count = lambda n: 2
        sys.modules["aggressive_trigger"] = eyes_mod

        import os as _rig_os, tempfile as _rig_tf
        _rig_os.environ["AGGRESSIVE_ENTRY_ENABLED"] = "1"      # the REAL source (.env loads here)
        # opsfix:v1.3-rig-isolation: v1.2 wrote this fixture into the REAL
        # data/power_v2_memory/ day-file and never removed it -> the live book
        # died with MemoryIntegrityError (fail-closed) until fix_memory_book.py
        # repaired it. LAW: a rig owns NOTHING outside its process. Fixtures
        # live in a TEMP working dir; the real data/ is diffed before/after
        # (check 13, "isolation").
        _real_cwd = _rig_os.getcwd()
        _tmpdir = _rig_tf.mkdtemp(prefix="opsfix3_rig_")
        _today_name = "power_" + datetime.datetime.now(datetime.timezone.utc).date().isoformat() + ".jsonl"
        (Path(_tmpdir) / "data" / "power_v2_memory").mkdir(parents=True, exist_ok=True)
        (Path(_tmpdir) / "data" / "power_v2_memory" / _today_name).write_bytes(
            b"x" * (3 * 1048576))                                   # 3.0MB, ISOLATED
        for _st_name in ("trade_guard_state.json", "risk_state.json",
                         "maintenance_state.json"):
            _st_src = ROOT / "data" / _st_name      # copies of the real state, so
            if _st_src.is_file():                   # the armor line carries the
                shutil.copy2(_st_src,               # same values, read in isolation
                             Path(_tmpdir) / "data" / _st_name)
        _rig_os.chdir(_tmpdir)
        snap = types.SimpleNamespace(bid=4172.50, ask=4172.70, mtf_trends={"M5": "DOWN", "H1": "UP"})
        cfg = types.SimpleNamespace(A2_BURST_MAX_PER_DAY=10, AGGRESSIVE_ENTRY_ENABLED=1,
                                     MAX_TRADES_PER_DAY=999, A2_BURST_COOLDOWN_MINUTES=5)
        ns = {"snapshot": snap, "config": cfg, "Path": Path, "_record": lambda n, s: recorded.append((n, s))}
        exec(code, ns)
        a2_line = next(s for n, s in recorded if "A2 LANE" in n)
        armor_line = next(s for n, s in recorded if "ARMOR" in n)
        check(f"behavior: A2 line reads the eyes ({a2_line[:58]}...)",
              "enabled=True fires 2/10 today" in a2_line
              and "eye1 lean: DOWN +0.41" in a2_line
              and "eye2 panel: SELL (" in a2_line)
        import json as _tj
        _exp_trades = 0
        try:
            _tg = _tj.loads((ROOT / "data" / "trade_guard_state.json")
                            .read_text(encoding="utf-8"))
            import datetime as _td
            if _tg.get("date") == _td.datetime.now().astimezone().date().isoformat():
                _exp_trades = int(_tg.get("trades_today", 0) or 0)
        except Exception:
            pass
        check(f"behavior: armor line carries the state ({armor_line[:58]}...)",
              f"guard: {_exp_trades} trades today/999" in armor_line
              and "risk: no halt" in armor_line
              and "spread: 0.20 pts (0.005%)" in armor_line
              and "MTF H1:UP M5:DOWN" in armor_line
              and "flatten in" in armor_line
              and "book: today 3.0MB" in armor_line)
        # fail-closed: broken snapshot must not raise
        recorded2 = []
        ns2 = {"snapshot": types.SimpleNamespace(bid=None, ask=None, mtf_trends=None),
               "config": cfg, "Path": Path, "_record": lambda n, s: recorded2.append((n, s))}
        exec(code, ns2)
        check("behavior: broken snapshot -> lines still produced, no crash",
              any("A2 LANE" in n for n, _ in recorded2)
              and any("ARMOR" in n for n, _ in recorded2))
    except Exception as e:
        check(f"behavioral tests (error: {type(e).__name__}: {e})", False)
    finally:
        import os as _rig_os2
        if _real_cwd:
            try:
                _rig_os2.chdir(_real_cwd)
            except Exception:
                pass
        if _tmpdir:
            shutil.rmtree(_tmpdir, ignore_errors=True)
        sys.modules.pop("aggressive_trigger", None)
        _rig_os2.environ.pop("AGGRESSIVE_ENTRY_ENABLED", None)

    # opsfix:v1.3 check 13: the isolation proof -- the rig must leave the
    # real data/ byte-identical. This is the check that would have caught
    # the v1.2 rig destroying the live book's day-file.
    _post_state = _watched_state()
    _iso_ok = _pre_state == _post_state
    check(f"isolation: real data/ untouched by the rig ({len(_pre_state)} watched file(s))",
          _iso_ok)
    if not _iso_ok:
        print("       NOTE: watched data/ files changed DURING the rig. The rig itself")
        print("       writes nothing real -- was the robot running? Stop it and re-run.")

    print("-" * 78)
    npass = sum(1 for _, c in results if c)
    if npass == len(results):
        print(f"RIG VERDICT: ALL PASS ({npass}/{len(results)}) - ops fix v3 is proven.")
        print("Next: restart the robot, then push:")
        print("  git add main.py step2_market_analysis.py shooting_team.py apply_ops_fix3.py")
        return 0
    print(f"RIG VERDICT: {len(results) - npass} FAILED - paste this whole output to the assistant.")
    return 1


def rollback():
    ok = True
    for rel in ("main.py", "step2_market_analysis.py", "shooting_team.py"):
        p = ROOT / rel
        bak = Path(str(p) + SUFFIX)
        if bak.exists():
            shutil.copy2(bak, p)
            print(f"[back] {rel} restored from {bak.name}")
        else:
            print(f"[ --  ] {rel}: no {SUFFIX} backup")
    print("rollback done (back to ops-v2 state). RESTART the robot if running.")
    return 0


def main():
    if "--rig" in sys.argv:
        return rig()
    if "--rollback" in sys.argv:
        return rollback()
    return install()


if __name__ == "__main__":
    sys.exit(main())
