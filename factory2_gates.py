# -*- coding: utf-8 -*-
"""factory2_gates.py — THE NEW FACTORY's hall (COMPANY 3, Code Day 2).

The owl's house: eyes in (imported), brain on loan (imported), diary OUT.
It edits NOTHING in the old factory and touches its files read-only.

  --replay-jar PATH   walk the old boat's own per-cycle diary
                      (snapshots_history.jsonl) through the committee.
                      mode: replay:jar  (close-based toll; assumed-fresh eyes)
  --replay-file PATH  drive the REAL brain (step2.analyze_market) over a recorded
                      market_data JSON - the classroom road. mode: replay:tapes
  --demo              one synthetic heartbeat (DemoProvider) - end-to-end smoke.
  --live              heartbeat loop through the boat's own eyes + brain.
  --once              exactly one heartbeat, then stop.

Every heartbeat appends ONE ~1KB line to <data-dir>/gates_shadow_YYYYMMDD.jsonl.
fold_day() gzips FINISHED days (N4: folded, never trimmed). Missing data abstains
and is STAMPED into the line ("assumed-*"), never guessed (laws 4/5/8).

Meadow:  python -m pytest -q test_factory2.py   (stdlib only - no engine needed)
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

HERE = Path(__file__).resolve().parent
for _p in (str(HERE), str(HERE / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

FACTORY2_VERSION = "factory2.2026-11-13.5"


DATA_SANDBOX_NAME = "gates_sandbox"
_shared_data_requested = os.environ.get("GATES_SHARED_DATA", "").strip() in ("1", "yes", "true")
DATA_QUARANTINED = False


def isolate_data_dir(quiet: bool = False) -> bool:
    """Point the BORROWED machinery at our own drawer BEFORE it is touched.

    WHY (Law #12, item 20 - found while answering "may I sail the sea on the
    second rig while the loop runs on the first one?"): `analyze_market` is not
    a pure function - it WRITES. `step2_market_analysis.py:6693-6696` calls
    `session_levels.update(price, config.DATA_DIR, now=now)`, and that function
    rewrites `data/session_levels.json` (session_levels.py:119-186) - ROLLING the
    session when the day differs; `step2_market_analysis.py:5275-5282,5942`
    writes `data/last_book_state.json`. So a 5-day-old tape replayed through the
    borrowed brain overwrites TODAY's session levels and feeds a replay day back
    to the live loop as `prev_day/prev_high` - straight into the level book that
    G2 stands on. Same reason this repo has `conftest.py` ("pytest can NEVER
    create or mutate live state/log files inside ./data"). We do not edit the
    old factory (law N1); we move our OWN process's config.DATA_DIR instead.

    Our own papers (gates_*.jsonl / gates_*.txt / gates_rung_state.json) stay in
    `data/` - only the borrowed writers are diverted. Opt out with
    GATES_SHARED_DATA=1 if you ever want the live drawer on purpose.
    """
    global DATA_QUARANTINED
    if DATA_QUARANTINED or _shared_data_requested:
        return DATA_QUARANTINED
    try:
        import config
        base = Path(getattr(config, "DATA_DIR", HERE / "data"))
        if base.name == DATA_SANDBOX_NAME:
            DATA_QUARANTINED = True
            return True
        sand = base / DATA_SANDBOX_NAME
        sand.mkdir(parents=True, exist_ok=True)
        config.DATA_DIR = sand
        DATA_QUARANTINED = True
        if not quiet:
            print(f"[factory2] data quarantine: borrowed writers -> {sand}  "
                  f"(live session_levels.json / last_book_state.json untouched)")
    except Exception as exc:                          # never die over a folder
        if not quiet:
            print(f"[factory2] data quarantine unavailable: {type(exc).__name__}: {exc}")
    return DATA_QUARANTINED


def _gh():
    import gates_helpers as _m
    return _m


def _f(x: Any, d: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else d
    except (TypeError, ValueError):
        return d


def git_stamp() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             cwd=str(HERE), capture_output=True, text=True,
                             timeout=1.5)
        return (out.stdout or "").strip() or "?"
    except Exception:
        return "?"


# ---------------------------------------------------------------- level adapter
def levels_from_view(cv: Dict[str, Any]) -> List[Dict[str, Any]]:
    """signal_map rows -> committee level dicts with the ENTRY-5 kind map,
    plus the learner rungs (FVG from bars, AVWAP accumulator, va_prior day-2)."""
    gh = _gh()
    sm = cv.get("signal_map") or {}
    atr = max(_f(cv.get("atr"), 1.0), 1e-9)
    rows: List[Dict[str, Any]] = []
    for row in list(sm.get("targets") or []) + list(sm.get("memory_doors") or []):
        if not isinstance(row, dict):
            continue
        try:
            price = float(row["price"])
        except (KeyError, TypeError, ValueError):
            continue                       # incomplete level: skipped, never guessed
        srcs = [str(s) for s in (row.get("sources") or [])]
        raw_kind = str(row.get("kind") or (srcs[0] if srcs else "rung"))
        live = _f(row.get("size")) > 0
        kind = ("walls" if live and raw_kind in ("walls", "whale", "event",
                                                  "iceberg", "rung")
                else gh.map_kind(raw_kind))
        rows.append({
            "price": price, "kind": kind, "name": raw_kind,
            "touches": int(_f(row.get("cycles_seen"))),
            "anchors_near": sum(1 for s in srcs if s in gh.ANCHOR_KINDS),
            "accepted": 0,                 # RungState upgrades from its history
            "uob": gh.uob_check(cv.get("bars") or [], price),
            "note": str(row.get("burn_word") or ""),
        })
    for g in gh.find_gaps(cv.get("bars") or [], atr):
        rows.append({"price": g["price"], "kind": "fvg", "name": g["name"],
                     "accepted": 0, "uob": 0, "touches": 0, "anchors_near": 0,
                     "note": "gap"})
    av = _f(cv.get("avwap"))
    if av > 0:
        rows.append({"price": av, "kind": "avwap", "name": "avwap",
                     "accepted": 0, "uob": 0, "touches": 0, "anchors_near": 0,
                     "note": "anchored-vwap"})
    for key, nm in (("high", "va_prior_high"), ("low", "va_prior_low")):
        pv = _f((cv.get("va_prior") or {}).get(key))
        if pv > 0:
            rows.append({"price": pv, "kind": "va_prior", "name": nm,
                         "accepted": 0, "uob": 0, "touches": 0, "anchors_near": 0,
                         "note": "prior-VA(day2)"})
    return rows


# --------------------------------------------------------------- sigs assembly
def sigs_from_view(cv: Dict[str, Any]) -> Dict[str, Any]:
    """G1 witnesses - every absent field is ABSENT, never zeroed;
    er is 0.0 for a flat lake because zero IS data (check-up fix 1)."""
    pw = cv.get("power") or {}
    vp = cv.get("vp") or {}
    sigs: Dict[str, Any] = {}
    reg = str(pw.get("market_regime") or "")
    if reg:
        sigs["regime"] = reg
    if "shadow_flat_lake" in pw:
        sigs["er"] = 0.0 if bool(pw.get("shadow_flat_lake")) else 1.0
    if "shadow_vr_mean_reverting" in pw:
        sigs["vr_chop"] = bool(pw.get("shadow_vr_mean_reverting"))
    sigs["slope"] = int(cv.get("slope") or 0)
    sigs["migration"] = int(cv.get("migration") or 0)
    roc = vp.get("volume_rate_of_change")
    if roc is not None:
        sigs["fuel_roc"] = float(roc)
        sigs["fuel_dir"] = int(cv.get("fuel_dir") or 0)
    news = cv.get("news")
    if news is not None:
        sigs["news"] = float(news)
    wx = cv.get("weather")
    if isinstance(wx, dict) and wx:
        sigs["weather"] = wx
    if cv.get("ib_ratio") is not None:
        sigs["ib_ratio"] = float(cv["ib_ratio"])
    if cv.get("open_vs_yva"):
        sigs["open_vs_yva"] = str(cv["open_vs_yva"])
    return sigs


def _l3q_from_quality(q: Dict[str, Any], mode: str) -> str:
    l3e = str(q.get("level3_events") or "")
    if l3e in ("fresh_window", "available"):
        return "ok"
    if l3e:
        return l3e
    return "ok" if mode.startswith("replay") else "unknown"


# ------------------------------------------------------------------- prefire
def prefire_from_view(cv: Dict[str, Any], mode: str, now_ts: float) -> Dict[str, Any]:
    """Fire-law inputs: real values when the view has them; replay gets EXPLICIT
    assumptions, each one stamped into the diary line (no silent forgiveness)."""
    q = cv.get("data_quality") or {}
    pre: Dict[str, Any] = {}
    stamps: List[str] = []
    l3e = str(q.get("level3_events") or "")
    if l3e:
        pre["sensors_ok"] = str(q.get("quote") or "valid") == "valid"
    else:
        stamps.append("l3q:assumed")
        pre["sensors_ok"] = True
    ts = _f(cv.get("ts"))
    if ts > 0 and now_ts > 0:
        pre["age_s"] = max(0.0, float(now_ts) - ts)
    else:
        pre["age_s"] = 0.0
        stamps.append("eyes:assumed-fresh")
    pre["bar_closed"] = True               # every heartbeat is a CLOSED candle by law
    if mode.startswith("replay"):
        stamps.append("bar_closed:assumed")
    sp = cv.get("spread_pct")
    if sp is None:
        sp = 0.005
        stamps.append("spread:assumed")
    pre["spread_pct"] = float(sp)
    pre["news_blackout"] = bool(cv.get("news_blackout"))
    if mode.startswith("replay"):
        stamps.append("news:assumed-quiet")
    pm = cv.get("pm") or {}
    pre["position_count"] = int(pm.get("open", 0))
    pre["max_positions"] = 1
    pre["daily_loss_ok"] = bool(pm.get("daily_loss_ok", True))
    if not pm:
        stamps.append("risk:neutral(rehearsal)")
    pre["kill_switch"] = bool(cv.get("kill_switch"))
    pre["mode"] = mode
    pre["_ts"] = float(now_ts) if now_ts > 0 else time.time()
    pre["stamps"] = stamps
    return pre


# ---------------------------------------------------------------------- cycle
def build_cyc(cv: Dict[str, Any], state: Any, mode: str,
              now_ts: Optional[float] = None) -> Dict[str, Any]:
    gh = _gh()
    now_ts = time.time() if now_ts is None else float(now_ts)
    price = _f(cv.get("price"))
    atr = max(_f(cv.get("atr"), 1.0), 1e-9)
    sigs = sigs_from_view(cv)
    z = cv.get("zscore")
    if z is not None and hasattr(state, "set_prev_z"):
        st_slope = state.set_prev_z(float(z))
        if not sigs.get("slope") and st_slope:
            sigs["slope"] = int(st_slope)
    cv2 = dict(cv)
    cv2.setdefault("va_prior", state.prior_va() if hasattr(state, "prior_va") else {})
    levels = levels_from_view(cv2)
    for lv in levels:                       # stamp history + arm the toll book
        lv["accepted"] = state.accepted_for(lv["price"]) if hasattr(
            state, "accepted_for") else 0
        if hasattr(state, "watch"):
            state.watch(lv["price"])
    rung_px = _f(cv.get("rung_hint"))
    if rung_px <= 0 and levels:             # nearest rung within the yard = toll focus
        near = sorted(levels, key=lambda r: abs(r["price"] - price))
        if near and abs(near[0]["price"] - price) <= 0.75 * atr:
            rung_px = float(near[0]["price"])
    toll = (state.toll_for(rung_px, now_ts)
            if (rung_px > 0 and hasattr(state, "toll_for"))
            else {"closes_beyond": 0, "holds_minutes": 0.0,
                  "volume_building": False, "snapback": 0})
    if mode.startswith("replay:jar"):
        toll = dict(toll)
        # no volumes in the jar: the holds+volume branch cannot pay, closes can
        toll["volume_building"] = False
    pre = prefire_from_view(cv2, mode, now_ts)
    judges = list(cv.get("judges") or [])
    cyc = {
        "symbol": str(cv.get("symbol") or "?"),
        "cycle_id": int(cv.get("cycle_id") or 0),
        "price": price, "atr": atr,
        "sigs": sigs,
        "levels": levels,
        "prev": state.prev() if hasattr(state, "prev") else None,
        "judges": judges or None,
        "notes": list(cv.get("notes") or []),
        "toll": toll,
        "trap": {"snapback": int(toll.get("snapback") or 0)},
        "l3q": _l3q_from_quality(cv.get("data_quality") or {}, mode),
        "oi": gh.oi_read(cv.get("oi_path")),
        "effort": None,
        "prefire": pre,
    }
    cyc["rung_price"] = rung_px            # for stamp_accepted below
    anc = gh.session_note(cv.get("session_state"), price, atr)
    if anc:
        cyc["anchor_note"] = anc
    return cyc


class Pen:
    """The diary pen. Append-only json lines. A null pen makes attempt_fire
    REFUSE every ticket (fire-law 6: no diary, no deal) - on purpose."""

    def __init__(self, path: Optional[str], dry: bool = False):
        self.dry = bool(dry)
        self.path = str(path) if path else None

    def __call__(self, rec: Dict[str, Any]) -> None:
        line = json.dumps(rec, ensure_ascii=False, sort_keys=True)
        if self.dry:
            print(line)
            return
        if not self.path:
            return
        try:
            # newline="\n" ON PURPOSE (Law #12, Windows report of 11-13): text
            # mode on Windows turns every "\n" into "\r\n", so a diary written
            # on your rig would carry one invisible byte per line more than the
            # same diary from any other machine - and the fold/size law counts
            # BYTES. One byte grammar for the diary, everywhere.
            with open(self.path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(line + "\n")
        except OSError:
            pass

    @property
    def callable_ok(self) -> bool:
        return bool(self.path) or self.dry


def run_cycle(cv: Dict[str, Any], state: Any, pen: Optional[Pen], mode: str,
              now_ts: Optional[float] = None,
              ledger: Optional[Any] = None) -> Dict[str, Any]:
    import committee
    now_ts = time.time() if now_ts is None else float(now_ts)
    ledger = ledger if ledger is not None else committee.TicketLedger()
    cyc = build_cyc(cv, state, mode, now_ts=now_ts)
    pre = dict(cyc.get("prefire") or {})
    pre["diary"] = pen if (pen is not None and pen.callable_ok) else None
    cyc["prefire"] = pre
    out = committee.committee_evaluate(cyc, ledger)
    try:
        rec = {
            "t": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cycle": cyc["cycle_id"], "price": cyc["price"],
            "atr": round(cyc["atr"], 3), "mode": mode,
            "ver": FACTORY2_VERSION, "git": git_stamp(),
            "g1": out.get("g1"), "g2": out.get("g2"), "g3": out.get("g3"),
            "final": out.get("final"), "error": out.get("error", ""),
            "stamps": pre.get("stamps") or [],
            "anchor": cyc.get("anchor_note", ""),
            "rung": round(_f(cyc.get("rung_price")), 2),
            "toll": {k: toll.get(k) for k in
                     ("closes_beyond", "holds_minutes", "volume_building")}
                     if isinstance(toll := (cyc.get("toll") or {}), dict) else {},
        }
        if pen is not None:
            pen(rec)
    except Exception as exc:                      # law 8: one line, never a dead loop
        if pen is not None:
            pen({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": mode,
                 "error": f"factory2-rec:{type(exc).__name__}: {exc}"})
    if state is not None:
        try:
            cyc_ts = float(cyc.get("prefire", {}).get("_ts") or now_ts)
            words = {}
            for part in str(out.get("g1") or "").split(" | "):
                if "=" in part:
                    k, v = part.split("=", 1)
                    words[k] = v
            if words and hasattr(state, "set_prev"):
                state.set_prev({"mood": words.get("MOOD", "SLEEP"),
                                "certainty": int(_f(words.get("CERTAINTY"))),
                                "since": int(_f(words.get("SINCE")))})
            vp = cv.get("vp") or {}
            if hasattr(state, "roll_day"):
                state.roll_day(_f(vp.get("value_area_high")),
                               _f(vp.get("value_area_low")))
            if hasattr(state, "observe"):
                vol = vp.get("volume")
                state.observe(cyc["price"], cyc_ts,
                              _f(vol) if vol is not None else None)
            g3card = str(out.get("g3") or "")
            if ("VERDICT=BOUNCE" in g3card or "VERDICT=BREAK" in g3card) \
                    and hasattr(state, "stamp_accepted"):
                rung_px = _f(cyc.get("rung_price"))   # fused price, book-keyed
                if rung_px > 0:
                    state.stamp_accepted(rung_px)
            if hasattr(state, "save"):
                state.save()
        except Exception:
            pass
    return out


# ------------------------------------------------------------------ data views
def cv_from_snapshot_dict(d: Dict[str, Any], cycle_id: int) -> Dict[str, Any]:
    """A serialized MarketSnapshot (snapshot_to_dict shape) -> cycle view."""
    vp = d.get("volume_profile") or {}
    atr = _f((d.get("volatility") or {}).get("atr"))
    if atr <= 0:
        atr = _f((d.get("signal_map") or {}).get("atr"), 1.0)
    ts_raw = str(d.get("timestamp") or "")
    try:
        ts = datetime.fromisoformat(ts_raw).timestamp() if ts_raw else 0.0
    except ValueError:
        ts = 0.0
    judges = [j for j in (d.get("judge_votes") or []) if isinstance(j, dict)]
    weather: Dict[str, int] = {}
    migration = 0
    for j in judges:
        name = str(j.get("judge") or "")
        if name.startswith("macro_"):
            weather[name] = int(_f(j.get("dir")))
        if name in ("poc_day", "value_area", "htf_poc") and int(_f(j.get("dir"))) != 0:
            migration = 1
    z = _f(vp.get("vwap_zscore")) if vp else 0.0
    roc = _f(vp.get("volume_rate_of_change")) if vp else 0.0
    mb = d.get("macro_bias")
    return {
        "cycle_id": cycle_id, "price": _f(d.get("price")), "atr": max(atr, 1e-9),
        "signal_map": d.get("signal_map") or {}, "power": d.get("power") or {},
        "vp": vp, "judges": judges, "notes": [str(x) for x in (d.get("notes") or [])],
        "data_quality": d.get("data_quality") or {},
        "symbol": str(d.get("symbol") or ""), "ts": ts,
        "spread_pct": d.get("spread_pct") or None,
        "news": _f(mb) if mb is not None else None,
        "news_blackout": str(((d.get("news") or {}).get("news_state")) or "") == "BLACKOUT",
        "weather": weather, "migration": migration,
        "zscore": z or None, "fuel_dir": 1 if roc > 0 else (-1 if roc < 0 else 0),
        "bars": [dict(b) for b in ((d.get("recent_bars") or {}).get("bars") or [])],
        "rung_hint": 0.0, "oi_path": d.get("oi_path"),
    }


def cv_from_jar_line(rec: Dict[str, Any], cycle_id: int) -> Dict[str, Any]:
    """A snapshots_history.jsonl line (trimmed jar) -> cycle view. Everything the
    jar lacks is ABSENT; prefire stamps its assumptions line-by-line."""
    judges = [j for j in (rec.get("judge_votes") or []) if isinstance(j, dict)]
    migration = 1 if any(str(j.get("judge")) in ("poc_day", "value_area", "htf_poc")
                         and int(_f(j.get("dir"))) != 0 for j in judges) else 0
    sm = rec.get("signal_map") or {}
    return {
        "cycle_id": cycle_id, "price": _f(rec.get("price")),
        "atr": max(_f(sm.get("atr"), 1.0), 1e-9),
        "signal_map": sm, "power": rec.get("power") or {},
        "vp": {}, "judges": judges, "notes": [str(n) for n in (rec.get("notes") or [])],
        "data_quality": {}, "symbol": "?", "ts": 0.0,
        "spread_pct": None, "news": None, "news_blackout": False,
        "weather": {}, "migration": migration, "zscore": None, "fuel_dir": 0,
        "bars": [], "rung_hint": 0.0, "oi_path": None,
    }


# --------------------------------------------------------------------- folding
def fold_day(data_dir: Path, today: Optional[str] = None) -> List[str]:
    """N4 FOLD-NEVER-TRIM: finished days gzip; today is left alone."""
    today = today or time.strftime("%Y%m%d")
    folded: List[str] = []
    if not data_dir.exists():
        return folded
    for p in sorted(data_dir.glob("gates_shadow_*.jsonl")):
        day = p.stem.replace("gates_shadow_", "")
        if day.isdigit() and len(day) == 8 and day < today:
            gz = p.with_suffix(p.suffix + ".gz")
            try:
                with open(p, "rb") as src, gzip.open(gz, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                p.unlink()
                folded.append(gz.name)
            except OSError:
                pass
    return folded


# ----------------------------------------------------------------------- modes
def iter_jar(path: Path, max_n: int):
    n = 0
    with open(path, "r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield rec
            n += 1
            if max_n and n >= max_n:
                return


def resolve_data_dir(arg: Optional[str]) -> Path:
    if arg:
        return Path(arg)
    env = os.environ.get("GBM_DATA_DIR", "").strip()
    if env:
        return Path(env)
    try:
        import config
        dd = getattr(config, "DATA_DIR", None)
        if dd:
            return Path(dd)
    except Exception:
        pass
    for cand in (HERE / "Workspace" / "data", HERE / "data"):
        if cand.exists():
            return cand
    return HERE / "data"


def _snap_to_dict(snap: Any) -> Dict[str, Any]:
    try:
        from step2_market_analysis import snapshot_to_dict
        return dict(snapshot_to_dict(snap))
    except Exception:
        from dataclasses import asdict
        try:
            return json.loads(json.dumps(asdict(snap), default=str))
        except Exception:
            return {}


def main(argv: Optional[List[str]] = None) -> int:
    isolate_data_dir()                 # borrowed writers never touch live data
    ap = argparse.ArgumentParser(description="THE NEW FACTORY - 3-gate hall loop")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--replay-jar", type=str, help="walk snapshots_history.jsonl")
    mode.add_argument("--replay-file", type=str, help="recorded market_data JSON")
    mode.add_argument("--demo", action="store_true", help="one synthetic heartbeat")
    mode.add_argument("--live", action="store_true", help="ride the boat's own eyes")
    ap.add_argument("--once", action="store_true", help="single heartbeat")
    ap.add_argument("--max", type=int, default=0, help="cap jar records")
    ap.add_argument("--dry", action="store_true", help="print lines, write nothing")
    ap.add_argument("--data-dir", type=str, default=None)
    ap.add_argument("--interval", type=int, default=180, help="live seconds")
    a = ap.parse_args(argv)

    gh = _gh()
    dd = resolve_data_dir(a.data_dir)
    dd.mkdir(parents=True, exist_ok=True)
    diary = None if a.dry else dd / f"gates_shadow_{time.strftime('%Y%m%d')}.jsonl"
    pen = Pen(str(diary) if diary else None, dry=a.dry)
    state = gh.RungState(None if a.dry else str(dd / "gates_rung_state.json"))
    folded = fold_day(dd)
    if folded:
        print(f"[factory2] folded {len(folded)} finished diary day(s) (gzip, never trimmed)")

    if a.replay_jar:
        i = 0
        out: Dict[str, Any] = {}
        for rec in iter_jar(Path(a.replay_jar), a.max):
            i += 1
            out = run_cycle(cv_from_jar_line(rec, i), state, pen, "replay:jar",
                            now_ts=float(i) * 180.0)
            if a.once:
                break
        fin = (out.get("final") or {}) if out else {}
        print(f"[factory2] replay:jar walked {i} cycle(s) -> "
              f"{diary or '(dry)'} | last: {str(out.get('g3'))[:90]} "
              f"| allow={fin.get('allow')}")
        return 0

    if a.replay_file:
        from step2_market_analysis import analyze_market          # borrowed brain
        with open(a.replay_file, "r", encoding="utf-8") as fh:
            md = json.load(fh)
        snap = analyze_market(md)
        out = run_cycle(cv_from_snapshot_dict(_snap_to_dict(snap), 1), state, pen,
                        "replay:tapes")
        print("[factory2] replay:tapes done. g3:", str(out.get("g3"))[:140])
        return 0

    # demo / live share the same loop body, only the eyes differ
    from step1_data_acquisition import DataAcquisition        # borrowed eyes
    import config
    source = "demo" if a.demo else str(getattr(config, "DATA_SOURCE", "demo"))
    from step2_market_analysis import analyze_market
    cid = int(time.time() // max(60, a.interval))
    while True:
        try:
            data = DataAcquisition(source=source).acquire_market_data()
            snap = analyze_market(data)
            out = run_cycle(cv_from_snapshot_dict(_snap_to_dict(snap), cid),
                            state, pen, "live")
            print("[factory2] g1:", str(out.get("g1"))[:100])
            print("[factory2] g2:", str(out.get("g2"))[:100])
            print("[factory2] g3:", str(out.get("g3"))[:120])
        except Exception as exc:
            pen({"t": time.strftime("%Y-%m-%dT%H:%M:%SZ"), "mode": "live",
                 "error": f"factory2:{type(exc).__name__}: {exc}"})
        if a.once:
            return 0
        time.sleep(max(30, a.interval))


if __name__ == "__main__":
    raise SystemExit(main())
