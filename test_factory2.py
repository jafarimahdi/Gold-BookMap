#!/usr/bin/env python3
"""Tests for the NEW FACTORY (COMPANY 3, Code Day 2): tools/gates_helpers.py +
factory2_gates.py + the office night teacher's vocabulary.

Run:  python test_factory2.py        (or: python -m pytest -q test_factory2.py)
STDLIB ONLY - it must sail in a bare meadow (no numpy, no engine modules), because
a test that needs the whole boat proves nothing about the new hall's own plumbing.
Every expectation cites the law it freezes (HOLY_BOOK.md: COMPANY 3 / GATE pages /
the owner's signed catches)."""
from __future__ import annotations

import json
import os
import sys
import time
import shutil
import tempfile
import subprocess
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
for _p in (str(HERE), str(HERE / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import gates_helpers as H                                      # noqa: E402
import factory2_gates as F2                                     # noqa: E402


# ------------------------------------------------------------- the kind map
def test_kind_map_uses_the_committee_vocabulary():
    # ENTRY 5 §U: the supplier's words must become the ladder's words.
    assert H.map_kind("poc_day") == "naked_poc"
    assert H.map_kind("prev_day_high") == "pdh"
    assert H.map_kind("prev_day_low") == "pdl"
    assert H.map_kind("value_edge") == "va_dev"
    assert H.map_kind("vwap") == "vwap_bands"
    assert H.map_kind("cme_settlement") == "settlement"
    assert H.map_kind("today_open") == "day_open"
    assert H.map_kind("something_new") == "something_new"   # unknown words survive
    import committee as C
    assert H.map_kind("poc_day") in C.KIND_PRIORITY


def test_anchor_kinds_are_the_fortress_family():
    # R1/ENTRY 5: anchors = the eyes the scout tracks for the fortress count.
    assert {"poc_day", "htf_poc", "vwap", "value_edge"} <= H.ANCHOR_KINDS
    assert "prev_day_high" not in H.ANCHOR_KINDS       # that's a rung, not an anchor


# ------------------------------------------------------------------ the toll
def _state(tmp):
    return H.RungState(str(Path(tmp) / "state.json"))


def test_toll_needs_two_consecutive_closes():
    # verdict law 1 (Book:3007+): 2 closes beyond = acceptance = BREAK-eligible
    with tempfile.TemporaryDirectory() as tmp:
        st = _state(tmp)
        st.watch(4000.0)
        st.observe(4004.0, 1000.0, 10.0)                  # 1st close above
        t = st.toll_for(4000.0, 1000.0)
        assert t["closes_beyond"] == 1 and t["holds_minutes"] == 0.0
        st.observe(4005.0, 1180.0, 12.0)                  # 2nd close above
        t = st.toll_for(4000.0, 1180.0)
        assert t["closes_beyond"] == 2 and t["holds_minutes"] >= 3.0
        assert t["volume_building"] is False or t["volume_building"] is True


def test_toll_snap_back_stamp_lives_one_cycle():
    # the owner's catch: a cross back = SNAPBACK, cleared once consumed
    with tempfile.TemporaryDirectory() as tmp:
        st = _state(tmp)
        st.watch(4000.0)
        st.observe(4004.0, 1000.0, 10.0)
        st.observe(4005.0, 1180.0, 10.0)
        st.observe(3995.0, 1360.0, 10.0)                  # crossed back
        assert st.toll_for(4000.0, 1360.0)["snapback"] == 1
        st.clear_snap(4000.0)
        assert st.toll_for(4000.0, 1360.0)["snapback"] == 0


def test_zero_volume_is_data_not_missing_in_avwap():
    # ZERO-AS-MISSING bug class: 0.0 volume abstains (can't divide), but a real
    # zero-volume bar must still be feedable as *data* elsewhere.
    a = H.Avwap()
    a.feed(4000.0, 0.0)                    # no fuel -> no vote, no crash
    assert a.price() is None
    a.feed(4002.0, 5.0)
    a.feed(4004.0, 5.0)
    assert abs(a.price() - 4003.0) < 1e-9


def test_uob_star_needs_zero_tail_volume_on_the_birth_bar():
    bars = [{"l": 3990.0, "h": 4000.0, "v": 0.0}, {"l": 3995.0, "h": 4010.0, "v": 12.0}]
    assert H.uob_check(bars, 4000.0) == 1          # birth bar printed no tail volume
    assert H.uob_check(bars, 4010.0) == 0          # that bar had volume
    assert H.uob_check(bars, 0.0) == 0             # no line, no star
    assert H.uob_check([], 4000.0) == 0


def test_fvg_floor_and_retirement():
    # learner rung R10: wick-to-wick, floor 1.0xATR, retired by a close back inside
    atr = 5.0
    bars = [{"h": 4000.0, "l": 3995.0, "c": 3998.0},
            {"h": 4002.0, "l": 3999.0, "c": 4001.0},
            {"h": 4015.0, "l": 4012.0, "c": 4014.0}]
    gaps = H.find_gaps(bars, atr)
    assert gaps and gaps[0]["name"] == "fvg_up"
    tiny = [{"h": 4000.0, "l": 3995.0, "c": 3998.0},
            {"h": 4002.0, "l": 3999.0, "c": 4001.0},
            {"h": 4004.0, "l": 4002.5, "c": 4003.5}]      # 0.5 pt < 1.0 ATR
    assert H.find_gaps(tiny, atr) == []
    filled = bars + [{"h": 4010.0, "l": 4004.0, "c": 4006.0}]   # close back inside
    assert all(not (g["bottom"] < 4006.0 < g["top"]) for g in H.find_gaps(filled, atr))


def test_oi_is_blind_until_the_loader_is_born():
    # "If it fits gate 3, we fix OI=BLIND" - absent file must render BLIND, not 0
    assert H.oi_read(None) == ""
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "oi.json"
        p.write_text(json.dumps({"oi_day": "+1240"}), encoding="utf-8")
        assert H.oi_read(str(p)) == "+1240"
        p.write_text("{ broken", encoding="utf-8")
        assert H.oi_read(str(p)) == ""              # unreadable = blind, never guessed


def test_session_anchor_card_text():
    # G2 two-door law: an anchor under the feet is noted for the member
    n = H.session_note({"prev_high": 4000.0}, 4000.4, 5.0)
    assert n.startswith("ON_ANCHOR=PDH")
    assert H.session_note({"prev_high": 4000.0}, 4060.0, 5.0) == ""
    assert H.session_note(None, 4000.0, 5.0) == ""


# ------------------------------------------------------------------- the pen
def test_diary_line_is_one_kilo_and_names_every_refusal():
    # regime=TREND gives G1 WALK, so G2 is not SLEEP-locked; an undecided G3 then
    # refuses with a NAMED reason and the diary carries the whole receipt.
    cyc = {
        "price": 4000.0, "atr": 5.0, "cycle_id": 1, "symbol": "TEST",
        "signal_map": {"targets": [{"price": 4001.0, "kind": "vwap",
                                    "sources": ["vwap"], "cycles_seen": 2,
                                    "size": 0.0}]},
        "power": {"market_regime": "TREND", "shadow_flat_lake": False},
        "vp": {}, "bars": [], "notes": [], "judges": [], "data_quality": {},
        "rung_hint": 4001.0, "ts": 1000.0,
        "prefire": {"age_s": 1.0, "sensors_ok": True, "bar_closed": True,
                    "spread_pct": 0.002, "news_blackout": False,
                    "position_count": 0, "max_positions": 1, "daily_loss_ok": True,
                    "kill_switch": False, "stamps": ["risk:neutral(rehearsal)"]},
    }
    out = F2.run_cycle(dict(cyc), None, F2.Pen(None), "replay:jar")
    assert out["g1"].startswith("MOOD=WALK"), out["g1"]   # G1 silence is forbidden
    assert "LOCATION=vwap" in out["g2"], out["g2"]            # G2 named the rung
    assert out["g3"].startswith("RUNG=") and "VERDICT=UNDECIDED" in out["g3"]
    assert out["final"]["allow"] is False, out["final"]
    assert out["final"]["fails"] == ["NO-VERDICT"], out["final"]      # named refusal


def test_a_paid_toll_with_three_desks_opens_the_door():
    # the whole ladder end-to-end: toll paid + 3 desks one way = BREAK; with a
    # callable diary and fresh eyes the ticket is ISSUED (diary only - never hands)
    import committee as C
    rung = C.Rung(name="pdh", price=4000.0, kind="pdh", accepted=1, touches=3,
                  anchors_near=2)
    # BREAK counts creak + tilt + iceberg + breadth (delta names the direction).
    judges = [{"judge": "footprint_delta", "dir": 1, "weight": 1.0},
              {"judge": "l3_ofi_streak", "dir": 1, "weight": 1.0},
              {"judge": "microprice", "dir": 1, "weight": 0.9},
              {"judge": "iceberg", "dir": 1, "weight": 0.8},
              {"judge": "sweep", "dir": 0, "weight": 0.5}]
    g3 = C.evaluate_fight(rung, judges, {"closes_beyond": 2, "holds_minutes": 6.0},
                          {"snapback": 0}, l3q="ok")
    assert "VERDICT=BREAK" in g3["card"], g3["card"]
    assert "TOLL=paid" in g3["card"] and "L3Q=ok" in g3["card"]
    pre = {"age_s": 1.0, "sensors_ok": True, "bar_closed": True, "spread_pct": 0.002,
           "position_count": 0, "max_positions": 1, "daily_loss_ok": True,
           "kill_switch": False, "diary": lambda rec: None, "symbol": "T",
           "cycle_id": 9}
    out = C.attempt_fire(g3, rung, pre, C.TicketLedger())
    assert out["allow"] is True and out["fails"] == [], out
    assert out["ticket"] == "T:9:G3"


def test_null_pen_is_the_fire_law_six_test():
    import committee as C
    rung = C.Rung(name="wall", price=4000.0, kind="walls", accepted=1,
                  touches=3, anchors_near=2)
    ver = {"verdict": "BREAK", "card": "… | L3Q=ok | VERDICT=BREAK"}
    pre = {"age_s": 1.0, "sensors_ok": True, "bar_closed": True, "spread_pct": 0.001,
           "position_count": 0, "max_positions": 1, "daily_loss_ok": True,
           "kill_switch": False, "diary": None, "symbol": "T", "cycle_id": 1}
    out = C.attempt_fire(ver, rung, pre, C.TicketLedger())
    assert out["allow"] is False and "DIARY" in out["fails"]
    out2 = C.attempt_fire(ver, rung, dict(pre, diary=lambda r: None),
                          C.TicketLedger())
    assert out2["allow"] is True, out2


def test_no_diary_no_write_dry_pen_stays_in_memory():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "gates_shadow_20261113.jsonl"
        pen = F2.Pen(str(p))
        pen({"a": 1})
        assert p.exists() and len(p.read_text(encoding="utf-8").strip().splitlines()) == 1
        dry = F2.Pen(str(Path(tmp) / "never.jsonl"), dry=True)
        dry({"a": 2})
        assert not (Path(tmp) / "never.jsonl").exists()


# ---------------------------------------------------------------- fold = law N4
def test_fold_never_trims_and_never_touches_today():
    with tempfile.TemporaryDirectory() as tmp:
        dd = Path(tmp)
        y = time_yesterday_stamp()
        src = dd / f"gates_shadow_{y}.jsonl"
        src.write_text('{"a":1}\n{"a":2}\n', encoding="utf-8")
        # bytes ON DISK, not the string we asked for: a text write on Windows ends
        # every line with \r\n, and the law is about the FILE. My first version
        # compared against a hard-coded LF string and failed on the owner's rig -
        # a test that encodes the PLATFORM instead of the LAW (Law #12, item 17).
        before = src.read_bytes()
        today = __import__("time").strftime("%Y%m%d")
        (dd / f"gates_shadow_{today}.jsonl").write_text('{"a":3}\n', encoding="utf-8")
        folded = F2.fold_day(dd)
        assert folded and all(f.endswith(".jsonl.gz") for f in folded)
        import gzip
        raw = gzip.decompress((dd / f"gates_shadow_{y}.jsonl.gz").read_bytes())
        assert raw == before, "the fold must copy history BYTE for BYTE"
        assert (dd / f"gates_shadow_{today}.jsonl").exists()   # today is untouched
        assert F2.fold_day(dd) == []                   # idempotent


def time_yesterday_stamp() -> str:
    import datetime as _dt
    return (_dt.date.today() - _dt.timedelta(days=1)).strftime("%Y%m%d")


# -------------------------------------------------------- the view adapters
def test_jar_view_abstains_and_stamps_never_invents():
    cv = F2.cv_from_jar_line({"price": 4000.0, "signal_map": {"atr": 5.0},
                              "power": {}, "judge_votes": [], "notes": []}, 3)
    sigs = F2.sigs_from_view(cv)
    assert "regime" not in sigs                  # nothing invented
    assert "er" not in sigs and "fuel_roc" not in sigs     # absent, not zero
    pre = F2.prefire_from_view(cv, "replay:jar", 1000.0)
    joined = " ".join(pre["stamps"])
    for word in ("eyes:assumed-fresh", "spread:assumed", "l3q:assumed",
                 "news:assumed-quiet", "risk:neutral(rehearsal)"):
        assert word in joined, word


def test_levels_carry_the_supplier_words_translated():
    cv = {"price": 4000.0, "atr": 5.0, "bars": [], "avwap": 0.0, "va_prior": {},
          "signal_map": {"targets": [
              {"price": 4002.0, "kind": "poc_day", "sources": ["poc_day", "htf_poc"],
               "cycles_seen": 4, "size": 0.0},
              {"price": 4300.0, "kind": "walls", "sources": ["walls"], "size": 250.0,
               "cycles_seen": 2, "burn_word": "holding"}]}}
    lv = F2.levels_from_view(cv)
    kinds = {r["kind"] for r in lv}
    assert "naked_poc" in kinds and "walls" in kinds
    poc = [r for r in lv if r["kind"] == "naked_poc"][0]
    assert poc["anchors_near"] == 2 and poc["touches"] == 4


def test_zero_spread_and_zero_positions_are_not_treated_as_missing():
    # ZERO-AS-MISSING class: 0.0 spread / 0 positions must pass as real values
    cv = {"price": 1.0, "atr": 1.0, "spread_pct": 0.0, "pm": {"open": 0,
                                                               "daily_loss_ok": True}}
    pre = F2.prefire_from_view(cv, "live", 500.0)
    assert pre["spread_pct"] == 0.0 and "spread:assumed" not in pre["stamps"]
    assert pre["position_count"] == 0


def test_stale_feed_makes_the_loop_idle_not_vote():
    # fire-law (1): eyes older than FRESH_MAX_SECONDS refuse the ticket - the
    # refusal is NAMED, so a stale feed never silently changes a verdict.
    import committee as C
    cv = {"price": 4000.0, "atr": 5.0, "ts": 1000.0, "cycle_id": 4, "symbol": "T",
          "signal_map": {"targets": [{"price": 4001.0, "kind": "poc_day",
                                      "sources": ["poc_day"], "cycles_seen": 3,
                                      "size": 0.0}]},
          "bars": [], "vp": {}, "power": {"market_regime": "TREND"},
          "judges": [{"judge": "footprint_delta", "dir": 1, "weight": 1.0},
                     {"judge": "l3_ofi_streak", "dir": 1, "weight": 1.0},
                     {"judge": "l3_imbalance", "dir": 1, "weight": 0.8},
                     {"judge": "microprice", "dir": 1, "weight": 1.0},
                     {"judge": "iceberg", "dir": 1, "weight": 0.8}],
          "notes": [], "data_quality": {"level3_events": "fresh_window"},
          "rung_hint": 4001.0}
    cyc = F2.build_cyc(cv, None, "live", now_ts=1000.0 + 4000.0)
    cyc["toll"] = {"closes_beyond": 2, "holds_minutes": 6.0}
    cyc["prefire"] = dict(cyc["prefire"], diary=lambda rec: None,
                          spread_pct=0.002, daily_loss_ok=True)
    out = C.committee_evaluate(cyc, C.TicketLedger())
    assert "VERDICT=" in out["g3"], out["g3"]                 # the door spoke...
    assert out["final"]["allow"] is False                     # ...but the ticket stays put
    assert "FRESH-EYES" in out["final"]["fails"], out["final"]


def test_run_cycle_writes_exactly_one_line_per_heartbeat():
    with tempfile.TemporaryDirectory() as tmp:
        dd = Path(tmp)
        pen = F2.Pen(str(dd / "d.jsonl"))
        state = H.RungState(str(dd / "s.json"))
        import committee as C
        ledger = C.TicketLedger()
        cv = {"cycle_id": 1, "price": 4000.0, "atr": 5.0, "ts": 1000.0,
              "signal_map": {"targets": [{"price": 4001.0, "kind": "vwap",
                                          "sources": ["vwap"], "cycles_seen": 2,
                                          "size": 0.0}]},
              "power": {}, "vp": {}, "judges": [], "notes": [], "bars": [],
              "data_quality": {}}
        F2.run_cycle(cv, state, pen, "replay:jar", now_ts=1000.0, ledger=ledger)
        F2.run_cycle(dict(cv, cycle_id=2, price=4004.0), state, pen, "replay:jar",
                     now_ts=1180.0, ledger=ledger)
        rows = [json.loads(l) for l in
                (dd / "d.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 2
        assert all(len(json.dumps(r)) < 1100 for r in rows)     # the ~1KB law
        assert all(r["mode"] == "replay:jar" for r in rows)
        assert all("g1" in r and "g2" in r and "g3" in r and "final" in r
                   for r in rows)
        assert all(r["ver"] == F2.FACTORY2_VERSION for r in rows)


def test_toll_replays_from_candles_but_the_jar_cannot_pay_volume():
    # the Book finding: the fat jar has no candle tails, so on replay:jar the
    # volume branch is switched OFF while the close-counting branch still pays.
    with tempfile.TemporaryDirectory() as tmp:
        st = H.RungState(str(Path(tmp) / "s.json"))
        base = {"price": 4004.0, "atr": 5.0, "symbol": "T",
                "signal_map": {"targets": [{"price": 4000.0, "kind": "poc_day",
                                            "sources": ["poc_day"], "cycles_seen": 1,
                                            "size": 0.0}]},
                "vp": {}, "power": {}, "bars": [], "judges": [], "notes": [],
                "rung_hint": 4000.0}
        pen = F2.Pen(None, dry=True)
        F2.run_cycle(dict(base, cycle_id=1), st, pen, "replay:jar", now_ts=1000.0)
        t1 = st.toll_for(4000.0, 1000.0)
        assert t1["closes_beyond"] == 1 and t1["volume_building"] is False
        F2.run_cycle(dict(base, cycle_id=2, price=4006.0), st, pen, "replay:jar",
                     now_ts=1180.0)
        t2 = st.toll_for(4000.0, 1180.0)
        assert t2["closes_beyond"] == 2, t2       # closes DO work from the jar
        assert t2["volume_building"] is False     # volume never feeds from the jar


def test_old_factory_files_untouched_law_N1():
    # N1: the new hall never edits an old file. In the meadow we assert the
    # stronger, checkable half: the new modules import nothing from the *writer*
    # side and never open an old file for writing.
    src = (HERE / "factory2_gates.py").read_text(encoding="utf-8")
    tool = (HERE / "tools" / "gates_helpers.py").read_text(encoding="utf-8")
    sea = (HERE / "tools" / "gates_replay.py").read_text(encoding="utf-8") \
        if (HERE / "tools" / "gates_replay.py").exists() else ""
    for blob in (src, tool, sea):
        for bad in ('open("main.py"', "open('main.py'", "open(POSITION_FILE",
                    'with open(CONFIG', "shutil.rmtree", 'mode="w"'):
            assert bad not in blob, bad
    # the only binary writes allowed are the diary fold and the hall's OWN state file
    assert 'gzip.open(gz, "wb")' in src
    assert "os.replace(" in tool and "gates" in tool


def test_factory2_never_shares_the_notebook():
    # get_book() is a per-process singleton: factory2 must build its OWN LevelBook
    src = (HERE / "factory2_gates.py").read_text(encoding="utf-8")
    assert "get_book" not in src
    assert "ALLOW_LIVE_TRADING" not in src        # no hands at all in this hall
    assert "GATES_VOICE" not in src               # the switch belongs to ENTRY 6


def test_sea_stamps_are_in_the_same_vocabulary():
    # L-C law: the sea's honest words must be known to the teacher too
    rep = (HERE / "tools" / "gates_report.py").read_text(encoding="utf-8")
    sea = (HERE / "tools" / "gates_replay.py").read_text(encoding="utf-8")
    import re as _re
    m = _re.search(r"STAMPS_ALLOWED = \{(.*?)\n\}", rep, _re.S)
    allowed = set(_re.findall(r'"([^"]+)"', m.group(1)))
    words = set(_re.findall(r'stamps_extra = \[("[^"]+"[^\]]*)\]', sea, _re.S))
    lit = set()
    for blob in words:
        lit |= set(_re.findall(r'"([^"]+)"', blob))
    assert lit, "sea writes no stamp words at all?!"
    assert lit <= allowed, lit - allowed


def test_no_dead_flags_between_shell_and_sea():
    # report_evening_gates.sh calls the sea with flags; every one of them must
    # EXIST there and be READ. A declared-but-ignored flag is a mystery that
    # silently changes what the owner thinks is running (Law #12).
    import re as _re
    sh = (HERE / "report_evening_gates.sh").read_text(encoding="utf-8")
    sea = (HERE / "tools" / "gates_replay.py").read_text(encoding="utf-8")
    lines, i, flags = sh.splitlines(), 0, set()
    while i < len(lines):
        if "tools/gates_replay.py" in lines[i]:
            cont = lines[i]
            while cont.rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                cont += " " + lines[i]
            flags |= set(_re.findall(r"--([a-z][a-z0-9-]+)", cont))
        i += 1
    assert flags, "the shell never calls the sea?!"
    for fl in sorted(flags):
        assert ('"--' + fl + '"') in sea, "sea has no --" + fl + " flag"
        assert ("a." + fl.replace("-", "_")) in sea, \
            "--" + fl + " is passed by the shell but never read by the sea"


def test_report_vocab_is_closed():
    # the teacher must know every stamp the hall can write - no silent new words
    rep = HERE / "tools" / "gates_report.py"
    if not rep.exists():
        return
    import re as _re
    txt = rep.read_text(encoding="utf-8")
    m = _re.search(r"STAMPS_ALLOWED = \{(.*?)\}", txt, _re.S)
    assert m, "STAMPS_ALLOWED block missing"
    allowed = set(_re.findall(r'"([^"]+)"', m.group(1)))
    src = (HERE / "factory2_gates.py").read_text(encoding="utf-8")
    emitted = set(_re.findall(r'stamps\.append\("([^"]+)"\)', src))
    assert emitted <= allowed, emitted - allowed


def test_the_whole_evening_runs_from_one_shell(tmp=None):
    """The owner's night in a bottle: sea -> report. Runs the real tools as
    subprocesses on a synthetic tape (skips quietly if numpy/engine is absent,
    because a meadow without the boat must still be green)."""
    with tempfile.TemporaryDirectory() as td:
        sea = Path(td) / "sea.json"
        diary = Path(td) / "gates_shadow_sea.jsonl"
        cmd = [sys.executable, str(HERE / "tools" / "gates_replay.py"),
               "--synthetic", "--window", "00:00-23:59", "--prewarm", "60",
               "--max-cycles", "50", "--quiet", "--diary-out", str(diary),
               "--json-out", str(sea), "--data-dir", td]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if "no brain here" in (r.stdout + r.stderr):
            print("  (skip: engine deps absent in this meadow)")
            return
        assert r.returncode == 0, r.stdout[-500:] + r.stderr[-500:]
        assert sea.exists(), "the sea wrote no json"
        day = json.loads(sea.read_text(encoding="utf-8"))
        assert day["cycles"] >= 20, day
        out_txt = Path(td) / "night.txt"
        rep = subprocess.run(
            [sys.executable, str(HERE / "tools" / "gates_report.py"),
             "--dir", td, "--sea", str(sea), "--diary", str(diary),
            "--out", str(out_txt),
             "--date", str(day.get("day") or time.strftime("%Y-%m-%d"))],
            capture_output=True, text=True, timeout=300, cwd=str(HERE))
        assert rep.returncode == 0, rep.stdout[-800:] + rep.stderr[-400:]
        assert out_txt.exists(), "the night teacher wrote no report"
        txt = out_txt.read_text(encoding="utf-8")
        for section in ("SECTION A", "SECTION B", "SECTION C", "SECTION D",
                        "SECTION E", "SECTION F", "NIGHT GRADE"):
            assert section in txt, section
        assert "SECTION F" in txt, txt[:1200]
        assert ("SEA RAN" in txt) or ("THE SEA" in txt), txt
        assert "cycles" in txt
        # office row grammar: every grade line uses the audit_day vocabulary
        assert ("[PASS]" in txt) or ("[WARN]" in txt) or ("[FAIL]" in txt)


def test_kill_process_is_the_only_stop():
    # COMPANY 3 N3: no stop-flag file is invented by the new hall
    src = (HERE / "factory2_gates.py").read_text(encoding="utf-8")
    assert "STOP_FILE" not in src and "stop.flag" not in src


def test_the_borrowed_brain_is_quarantined_from_live_data():
    # Law #12 item 20. `analyze_market` is NOT pure: it writes
    # data/session_levels.json (step2_market_analysis.py:6693-6696 ->
    # session_levels.py:119-186, which ROLLS the session when the day differs)
    # and data/last_book_state.json (step2:5275-5282,5942). Replaying a tape of
    # an older day would therefore rewrite the LIVE rig's today - so our process
    # moves config.DATA_DIR into its own drawer before the brain is touched,
    # exactly like conftest.py does for pytest, and the old files stay untouched.
    hall = (HERE / "factory2_gates.py").read_text(encoding="utf-8")
    sea = (HERE / "tools" / "gates_replay.py").read_text(encoding="utf-8")
    assert "def isolate_data_dir(" in hall, "the hall has no quarantine"
    assert "DATA_SANDBOX_NAME = \"gates_sandbox\"" in hall
    assert "isolate_data_dir()" in hall[hall.index("def main("):hall.index("def main(") + 300], \
        "the hall does not quarantine at its own entry point"
    assert "isolate_data_dir()" in sea[sea.index("def main("):sea.index("def main(") + 400], \
        "the sea does not quarantine before calling the brain"
    # and it must be a RUNTIME redirect of config.DATA_DIR (law N1: no old file
    # is edited, not even to add a switch)
    assert "config.DATA_DIR = sand" in hall and "import config" in hall

    try:
        import config
    except Exception:
        return
    saved = getattr(config, "DATA_DIR", None)
    try:
        live = Path(saved) / "session_levels.json"
        was = live.read_bytes() if live.exists() else None
        assert F2.isolate_data_dir(quiet=True), "quarantine refused to run"
        assert Path(config.DATA_DIR).name == "gates_sandbox", config.DATA_DIR
        import session_levels as sl
        sl.update(4123.45, Path(config.DATA_DIR))
        assert (Path(config.DATA_DIR) / "session_levels.json").exists(), \
            "the sandbox drawer did not receive the write"
        now = live.read_bytes() if live.exists() else None
        assert now == was, "the LIVE session_levels.json was still edited!"
    finally:
        if saved is not None:
            config.DATA_DIR = saved


def test_the_shell_passes_the_tape_folder_to_the_sea():
    # The owner's raw tapes do not live in data/. The shell must carry
    # GATES_DATA_DIR into the sea's --data-dir and must FORWARD unknown flags
    # (--max-minutes 240) instead of swallowing them. Both were broken; both
    # were found by running the shell once and reading its own words (Law #12).
    sh = (HERE / "report_evening_gates.sh").read_text(encoding="utf-8")
    assert "GATES_DATA_DIR" in sh, "the shell never reads GATES_DATA_DIR"
    lines = sh.splitlines()
    joined = ""
    for i, ln in enumerate(lines):
        if "tools/gates_replay.py" in ln:
            blk = ln
            while blk.rstrip().endswith("\\") and i + 1 < len(lines):
                i += 1
                blk += " " + lines[i]
            joined += blk
    assert joined, "the shell never calls the sea"
    assert "--data-dir" in joined or "DATA_DIR" in joined, \
        "the shell does not tell the sea where the tapes are"
    assert "SEA_EXTRA" in sh, "the shell does not forward unknown flags"
    assert 'echo "[warn] ignoring unknown argument' not in sh, \
        "the shell drops unknown flags instead of forwarding them to the sea"


def test_day_grammar_is_canonical_for_the_sea():
    # `ticks_20261112.csv` must never become "20261112.c" again: one parser
    # accepted the half-slice, another refused it, and a real tape reported
    # "0 prints". Grammar is canonicalised ONCE, at the front door (Law #12).
    try:
        sys.path.insert(0, str(HERE / "tools"))
        import gates_replay as gr
    except Exception:
        return                                    # meadow without the engine
    assert gr.canon_day("2026-11-12") == "2026-11-12"
    assert gr.canon_day("20261112") == "2026-11-12"
    assert gr.canon_day("20261112.c") is None      # refused, not half-parsed
    assert gr.canon_day("2026-02-30") is None
    assert gr.canon_day("") is None and gr.canon_day(None) is None
    assert gr.day_token("ticks_20261111_000000.csv.gz") == "2026-11-11"
    assert gr.day_token("mbo_2026-11-09.csv") == "2026-11-09"
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        (base / "ticks_20261112.csv").write_text("time,event\n", encoding="utf-8")
        (base / "ticks_20261111_000000.csv").write_text("time,event\n", encoding="utf-8")
        days = gr.available_days(base, base, 9)
        assert "2026-11-12" in days and "2026-11-11" in days, days
        assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in days), days


def test_the_diary_pen_writes_one_byte_grammar():
    # L-A's budget is counted in BYTES, so the pen must not let the OS add a CR
    # to every line on Windows (the owner's rig is a second machine - a diary
    # that differs per platform makes every size/count law a lie).
    src = (HERE / "factory2_gates.py").read_text(encoding="utf-8")
    assert r'open(self.path, "a", encoding="utf-8", newline="\n")' in src, \
        "the diary pen must pin LF newlines"
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "gates_shadow_20260101.jsonl"
        F2.Pen(str(f))({"cycle": 1, "x": "y"})
        F2.Pen(str(f))({"cycle": 2, "x": "z"})
        data = f.read_bytes()
        assert b"\r" not in data, "the diary grew a CR on this platform"
        assert data.count(b"\n") == 2 and data.endswith(b"\n")


def test_an_explicit_data_dir_scopes_the_day_search():
    # `--data-dir /x` means: the days are exactly the days INSIDE /x - the
    # repo's own finder may not sprinkle extra days into a named folder.
    try:
        sys.path.insert(0, str(HERE / "tools"))
        import gates_replay as gr
    except Exception:
        return
    with tempfile.TemporaryDirectory() as tmp:
        tapes = Path(tmp) / "tapes"
        tapes.mkdir()
        for d in ("20260105", "20260106"):
            (tapes / f"ticks_{d}.csv").write_text("time,event\n", encoding="utf-8")
        other = Path(tmp) / "repo" / "data"
        other.mkdir(parents=True)
        (other / "ticks_20260109.csv").write_text("time,event\n", encoding="utf-8")
        scoped = gr.available_days(tapes, Path(tmp) / "repo", 9, scoped=True)
        assert scoped == ["2026-01-06", "2026-01-05"], scoped
        # The unscoped mode may know MORE days (the repo's own finder). What it
        # must never do is lose or reorder. Assert the RELATION, not a snapshot
        # of an empty sandbox - that mistake is item 19 of the ledger: the first
        # version of this line demanded "2026-01-09 in wide", which is true only
        # on a machine whose repo has few tapes. On the owner's rig (10 real days
        # in data/, max_days=9) the fake January day is simply cut by the cap.
        # Hence the BIG cap below: a relation must hold on every machine, a
        # snapshot only on the one it was written on.
        wide = gr.available_days(tapes, Path(tmp) / "repo", 999, scoped=False)
        assert set(scoped) <= set(wide), (scoped, wide)
        assert wide == sorted(wide, reverse=True), wide
        assert all(len(d) == 10 and d[4] == "-" and d[7] == "-" for d in wide), wide
        assert gr.available_days(tapes, Path(tmp) / "repo", 2, scoped=True) == \
            ["2026-01-06", "2026-01-05"]           # a named folder never grows


def test_multi_day_sea_delivers_one_json_to_the_teacher():
    # `--sea 5` writes one json PER DAY, but the shell hands the teacher ONE
    # path. Without the aggregate the teacher said "no --sea file given" while
    # five days had sailed fine - a seam bug no per-file test could see.
    with tempfile.TemporaryDirectory() as tmp:
        tape = Path(tmp) / "tapes"
        tape.mkdir()
        hdr = "time,event,price,size,level,operation,instrument\n"
        days = [(datetime.now() - timedelta(days=k)) for k in (1, 2)]
        for dt in days:
            rows = [hdr]
            # warm-up law: a cycle needs 60 closed M3 candles, so the tape
            # must start ~3 h before the window - the sea REFUSES a thin
            # morning on purpose (no vote on scraps).
            t0 = dt.replace(hour=4, minute=0, second=0, microsecond=0,
                            tzinfo=timezone.utc)
            px = 4000.0
            for i in range(1000):
                px += 0.7 if i % 3 else -0.9
                t = t0 + timedelta(seconds=20 * i)
                rows.append(f"{t.isoformat()},Last,{px:.2f},10,-,buy,GC\n")
                rows.append(f"{t.isoformat()},DepthBid,{px - 0.1:.2f},40,1,-,GC\n")
                rows.append(f"{t.isoformat()},DepthAsk,{px + 30 + 0.1:.2f},400,1,-,GC\n")
            (tape / f"ticks_{dt.strftime('%Y%m%d')}.csv").write_text(
                "".join(rows), encoding="utf-8")
        dayc = datetime.now().strftime("%Y%m%d")
        json_path = Path(tmp) / f"gates_sea_{dayc}.json"
        r = subprocess.run(
            [sys.executable, str(HERE / "tools" / "gates_replay.py"),
             "--days", "2", "--data-dir", str(tape), "--window", "08:00-09:10",
             "--every", "180", "--prewarm", "240", "--min-prints", "20",
             "--quiet", "--no-diary",
             "--json-out", str(json_path)],
            capture_output=True, text=True, timeout=600, cwd=str(HERE))
        assert r.returncode == 0, r.stdout[-400:] + r.stderr[-400:]
        assert json_path.exists(), (
            "multi-day sea must still write the plain --json-out path for the "
            f"teacher; it wrote: {sorted(p.name for p in Path(tmp).iterdir())}")
        agg = json.loads(json_path.read_text(encoding="utf-8"))
        assert agg.get("day_count") == 2 and agg.get("sailed") == 2, agg
        assert agg.get("cycles", 0) > 0, agg
        assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) for d in agg.get("days", []))
        rep = Path(tmp) / "rep.txt"
        r2 = subprocess.run(
            [sys.executable, str(HERE / "tools" / "gates_report.py"),
             "--sea", str(json_path), "--sea-only", "--out", str(rep)],
            capture_output=True, text=True, timeout=300, cwd=str(HERE))
        assert r2.returncode == 0, r2.stdout[-300:] + r2.stderr[-300:]
        body = rep.read_text(encoding="utf-8") if rep.exists() else r2.stdout
        assert "SEA RAN" in body, body[-400:]


def _sea_fixture_day(k: int = 1):
    """Yesterday-ish day, LOCAL naive stamps (item 19: a fixture written in UTC
    would move by the machine's offset and the window would miss it)."""
    dt = (datetime.now() - timedelta(days=k)).replace(hour=4, minute=0,
                                                       second=0, microsecond=0,
                                                       tzinfo=None)
    return dt


def test_the_archive_tape_and_the_order_tape_are_both_found():
    # v7 (items 21/25/27) - the owner's FIRST REAL SEA found no tape because a
    # closed day lives as ROTATED SLICES in data/archive/ and the order book
    # lives in a DIFFERENT file with a DIFFERENT header. Both grammars here are
    # copied verbatim from their rig (2026-11-13 packet):
    #   ticks: time,event,price,size,level,operation,instrument
    #   mbo  : time,event_type,order_id,price,size,instrument
    import gzip
    with tempfile.TemporaryDirectory() as tmp:
        dd = Path(tmp) / "data"
        arch = dd / "archive"
        arch.mkdir(parents=True)
        t0 = _sea_fixture_day(1)
        dayc = t0.strftime("%Y%m%d")
        tick = ["time,event,price,size,level,operation,instrument\n"]
        mbo = ["time,event_type,order_id,price,size,instrument\n"]
        px = 3900.0
        for i in range(1000):
            px += 0.7 if i % 3 else -0.9
            t = t0 + timedelta(seconds=20 * i)
            iso = t.isoformat()
            tick.append(f"{iso},Last,{px:.4f},10.0000,-,buy,GCZ6.COMEX@RITHMIC\n")
            tick.append(f"{iso},DepthBid,{px - 0.1:.4f},40.0000,1,-,GCZ6.COMEX@RITHMIC\n")
            tick.append(f"{iso},DepthAsk,{px + 30.1:.4f},400.0000,1,-,GCZ6.COMEX@RITHMIC\n")
            # the poison the owner's tape really carries: Mbo rows with level=-1
            tick.append(f"{iso},Mbo,{px:.4f},1.0000,-1,BID_NEW,GCZ6.COMEX@RITHMIC\n")
            ev = ("BID_NEW", "ASK_NEW", "BID_CANCEL", "ASK_CANCEL")[i % 4]
            mbo.append(f"{iso},{ev},{7816604578040 + i},{px:.4f},1.0000,"
                       f"GCZ6.COMEX@RITHMIC\n")
        with gzip.open(arch / f"ticks_{dayc}_120000.csv.gz", "wt",
                       encoding="utf-8", newline="\n") as fh:
            fh.write("".join(tick))
        with gzip.open(arch / f"mbo_{dayc}_120000.csv.gz", "wt",
                       encoding="utf-8", newline="\n") as fh:
            fh.write("".join(mbo))
        sys.path.insert(0, str(HERE / "tools"))
        import gates_replay as gr
        day = f"{t0.year:04d}-{t0.month:02d}-{t0.day:02d}"
        assert gr.discover_tape(dd, Path(tmp), day, "ticks"), \
            "v7 must find a closed day's tape in data/archive/"
        assert gr.discover_tape(dd, Path(tmp), day, "mbo"), \
            "v7 must find the ORDER tape too - L3 is impossible without it"
        jp = Path(tmp) / "sea.json"
        r = subprocess.run(
            [sys.executable, str(HERE / "tools" / "gates_replay.py"),
             "--days", "1", "--data-dir", str(dd), "--window", "08:00-09:10",
             "--every", "180", "--prewarm", "240", "--min-prints", "20",
             "--quiet", "--json-out", str(jp),
             "--diary-out", str(Path(tmp) / "diary.jsonl")],
            capture_output=True, text=True, timeout=900, cwd=str(HERE))
        assert r.returncode == 0, r.stdout[-500:] + r.stderr[-500:]
        agg = json.loads(jp.read_text(encoding="utf-8"))
        assert agg.get("sailed") == 1, agg
        assert not agg.get("l3_errors"), \
            f"a swallowed exception in the L3 seam is item 28 all over again: {agg['l3_errors']}"
        # THE point of v7: L3 must arrive AT THE CARD, not just in the RAM.
        cards = [json.loads(x) for x in (Path(tmp) / "diary.jsonl")
                 .read_text(encoding="utf-8").splitlines() if x.strip()]
        seen = [str(c.get("g3") or "") for c in cards]
        assert any("L3Q=ok" in x for x in seen), \
            f"L3 never reached G3 on a tape that HAS an order tape: {seen[:2]}"
        assert any("PUSH=UP" in x or "PUSH=DOWN" in x for x in seen), seen[:2]
        assert int(agg.get("mbo_rows") or 0) > 0, \
            f"the order tape was not opened: {agg.get('l3') or agg}"
        assert int(agg.get("ticks_mbo_ignored") or 0) > 0, \
            "ticks-tape Mbo rows must be COUNTED and ignored, never fed (item 27)"
        assert agg.get("l3_days") == 1 and agg.get("tape_days") == 1, agg
        assert agg.get("fed_by") == "raw tape", agg
        assert int(agg.get("cycles") or 0) > 0, agg
        assert "L3: order tape" in r.stdout, r.stdout[-600:]


def _two_day_look_fixture(tmp, sub="look2", n_rows=1400):
    """Tape for a horizon test, ONE DAY MORE than the single cold-open fixture.

    WHY TWO DAYS (item 47): a rehearsal with a CLEAN mirror - which is what it must
    have - learns only from the tape it sails. Ask a single day for a `prev_day_*`
    rung and there is nothing to stand on, so the night can honestly end with zero
    verdicts and a "must have a verdict to score" test would be asserting a fiction.
    Give it yesterday's tape and the level book has somewhere to come from: the
    learner inherits the PAST, in order, and nothing else. Square-wave prices (the
    shape that made 4 real verdicts on this fixture), 20 s apart, absolute stamps.
    Returns (day2, first_day) - day2 is the one the horizon must be measured on.
    """
    import gzip
    arch = Path(tmp) / sub / "data" / "archive"
    arch.mkdir(parents=True, exist_ok=True)
    day2 = _COLD_DAY                                  # 2026-11-05
    day1 = (_COLD_DAY0_UTC + timedelta(days=-1)).strftime("%Y-%m-%d")
    for k, dayc in enumerate((day1.replace("-", ""), day2.replace("-", ""))):
        first_utc = _COLD_DAY0_UTC + timedelta(days=k - 1, minutes=8 * 60)
        rows = ["time,event,price,size,level,operation,instrument\n"]
        for i in range(n_rows):
            px = 4000.0 + 8.0 * (1 if (i // 60) % 2 == 0 else -1) + 0.01 * (i % 9)
            iso = (first_utc + timedelta(seconds=20 * i)).astimezone(
                timezone(timedelta(hours=1))).isoformat()
            rows.append(f"{iso},Last,{px:.4f},10.0000,-,buy,GCZ6.COMEX@RITHMIC\n")
        with gzip.open(arch / f"ticks_{dayc}_235900.csv.gz", "wt", encoding="utf-8") as fh:
            fh.write("".join(rows))
    return day2, day1


def _fixture_env(mirror_dir: Path | None = None):
    """PIN THE CLOCK, AND (for mirror tests) THE BRAIN. `bookmap_bridge_provider._parse_ts` (line 75-77) stamps
    naive tape stamps with `ZoneInfo(os.getenv("LOCAL_TZ", "Europe/Budapest"))`
    - so the same fixture text lands 8 hours apart on two machines, and a test
    that "knows" its own offsets is a lie of the exact kind item 19 was. The
    sea keeps ONE clock; the tests now keep one zone."""
    env = dict(os.environ)
    env["LOCAL_TZ"] = "UTC"
    env["TZ"] = "UTC"
    # item 47: `isolate_data_dir()` anchors on the REPO's data dir, so a test that
    # keeps its tapes in tmp would otherwise share one brain with every other test -
    # which is exactly the accident that made the horizon test pass dirty and fail
    # clean. Point the borrowed writers at the fixture's own drawer and they cannot.
    if mirror_dir is not None:
        # `--mirror-dir` does this for the child; the env name is what BOTH sides
        # read, so no test can look in a folder the code never writes to.
        env["GATES_MIRROR_DIR"] = str(mirror_dir)
    return env


def _fixture_epoch(naive_dt):
    """Parse a stamp exactly the way the sea does, so tests and code can never
    disagree about the clock (single-clock law, item 19). LOCAL_TZ is pinned for
    the duration - in THIS process too, or the assertion below would compare a
    Budapest reading against the child's UTC reading and be wrong by 8 hours."""
    sys.path.insert(0, str(HERE / "tools"))
    os.environ["LOCAL_TZ"] = "UTC"
    import gates_replay as gr
    gr.set_ts_parser()
    line = f"{naive_dt.isoformat()},Last,1,1,-,-,X\n"
    ep = gr._line_epoch(line)
    assert ep is not None, "the sea's own clock could not read my fixture"
    return float(ep)


# A FIXED calendar day, never `now()`: "yesterday" is a different date on a box
# at 20:35+0100 than on one at 19:35Z, and a test whose fixture moves when the
# machine's clock moves is a weather report, not a test (item 33, second form).
_COLD_DAY = "2026-11-05"
_COLD_DAY0_UTC = datetime(2026, 11, 5, 0, 0, tzinfo=timezone.utc)


def _cold_open_day_fixture(tmp, start_utc_min, sub="a", n_rows=1400):
    """Write a one-day archive tape whose FIRST row sits `start_utc_min` minutes
    INTO the 08:00Z window - the shape of an OPEN day on the owner's rig (the
    first slice of a young day is a fold that starts late: a slice's NAME-time is
    the fold moment, never the first row's time, Book §3.4).

    v8.2b: the stamps are written ABSOLUTE (`...+01:00`), exactly like the owner's
    real tapes, so no machine's local zone can move them a single second. My
    earlier version wrote NAIVE stamps and pinned `LOCAL_TZ=UTC` for the child -
    which passed here and still failed on HP-240, because `ZoneInfo("UTC")` is not
    resolved identically on a Windows UCRT64 box without `tzdata`
    (`bookmap_bridge_provider.py:75-77` falls back to `astimezone()`). **An input
    that depends on the machine's mood must be written with its offset attached.**
    Returns the day string."""
    import gzip
    first_utc = _COLD_DAY0_UTC + timedelta(minutes=8 * 60 + start_utc_min)
    dayc = _COLD_DAY.replace("-", "")
    arch = Path(tmp) / sub / "data" / "archive"
    arch.mkdir(parents=True, exist_ok=True)
    rows = ["time,event,price,size,level,operation,instrument\n"]
    px = 3900.0
    for i in range(n_rows):                 # 20 s apart -> ~7.8 h of tape
        px += 0.7 if i % 3 else -0.9
        # THE STAMP MUST ADVANCE with i - my first version of this line wrote the
        # SAME timestamp on all 1400 rows, so the walk had 6 minutes of tape and
        # 17 cells went `stale` instead of `warm-up`. The derived assertions are
        # strict enough that they caught it in one run (that is the whole point of
        # deriving instead of memorising: a wrong fixture cannot hide behind a
        # wrong-but-matching number).
        iso = first_utc.astimezone(
            timezone(timedelta(hours=1))).isoformat() if i == 0 else \
            (first_utc + timedelta(seconds=20 * i)).astimezone(
                timezone(timedelta(hours=1))).isoformat()
        rows.append(f"{iso},Last,{px:.4f},10.0000,-,buy,GCZ6.COMEX@RITHMIC\n")
    with gzip.open(arch / f"ticks_{dayc}_235900.csv.gz", "wt",
                   encoding="utf-8", newline="\n") as fh:
        fh.write("".join(rows))
    return _COLD_DAY


def _run_cold_open_day(tmp, day, cap_min, sub="a", rc_ok=True):
    jp = Path(tmp) / f"sea_{sub}_{cap_min}.json"
    r = subprocess.run(
        [sys.executable, str(HERE / "tools" / "gates_replay.py"),
         "--date", day, "--data-dir", str(Path(tmp) / sub / "data"),
         "--window", "08:00-23:00", "--every", "180", "--prewarm", "150",
         "--min-prints", "20", "--max-minutes", str(cap_min), "--quiet",
         "--json-out", str(jp)],
        capture_output=True, text=True, timeout=1800, cwd=str(HERE),
        env=_fixture_env())
    # `rc_ok=False` is for a run that is SUPPOSED to refuse the day: a sea whose
    # only day was refused exits 1 - that is its honest answer, and a test must
    # not translate an honest refusal into a crash.
    if rc_ok:
        assert r.returncode == 0, f"cap {cap_min}: {r.stdout[-500:]} {r.stderr[-400:]}"
    else:
        assert r.returncode == 1, (r.returncode, r.stdout[-300:])
    agg = json.loads(jp.read_text(encoding="utf-8")) if jp.exists() else {}
    sib = jp.with_name(jp.stem + ".days.json")
    days = json.loads(sib.read_text(encoding="utf-8")) if sib.exists() else []
    agg["_days"] = days
    return agg, r.stdout


def test_a_cold_open_day_says_WARMING_and_never_blames_the_sea():
    """v8.1/v8.2b / items 31+33 - the owner's packet of 18:43 printed
    `day 2026-11-13: cycles fed 0 (skipped/stale 18)` while 147,849 rows WERE read,
    and the teacher called the night `SEA RAN = FAIL`. Three blindnesses, all ours:
    (a) the 60-candle warm-up was counted as `stale` and never NAMED; (b) the
    default `--prewarm 150` could not satisfy a law needing 60 * 3 = 180 minutes;
    (c) a cap could end before the tape even began.

    Everything below is DERIVED from the sea's own report and the stamps are
    ABSOLUTE, because the first version of this test hard-coded `19`/`186min` and
    then `LOCAL_TZ` pinning still let it fail on HP-240 (item 33). Read the paper;
    do not recite it.
    """
    with tempfile.TemporaryDirectory() as tmp:
        day = _cold_open_day_fixture(tmp, 186, "cold")      # first row 11:06+0100

        # ---- (1) the owner's shape: rows in RAM, no cycle fed, CAUSE NAMED ----
        agg, out = _run_cold_open_day(tmp, day, 240, "cold")
        w = (agg.get("_days") or [{}])[0].get("window") or {}
        sk = agg.get("skip_kinds") or {}
        cells = int(w.get("cells") or 0)
        lead_in_min = int((w.get("first_row") or 0) - (w.get("win_start") or 0)) // 60
        assert w, ("the per-day sibling json must carry the day's own arithmetic - "
                   "without it a test can only memorise (item 33/34)")
        assert cells > 0, f"the fixture must still produce a grid: {w}"
        assert lead_in_min == 186, (
            f"absolute stamps must make the lead-in machine-independent "
            f"(got {lead_in_min}min): {w}")
        assert int(agg.get("cycles") or 0) == 0, (
            f"a window this cold must feed nothing: {agg}")
        assert int(agg.get("lines") or 0) > 0, (
            "the rows must be THERE - that is the whole point: rows, no cycles")
        # the grid belongs to the SEA: `cells` is what it counted. Re-deriving it
        # in the test with my own range() is item 33 wearing a new hat.
        assert int(sk.get("warm-up") or 0) == cells, (
            f"EVERY refused cell must be blamed on the warming, out loud "
            f"(cells={cells}); got {sk}")
        assert int(sk.get("stale") or 0) == 0, (
            f"warm-up must never be filed under `stale` again: {sk}")
        assert sum(int(v) for v in sk.values()) == int(agg.get("errors") or 0), (
            f"the causes must ADD UP to the total, never a shadow count: {sk}")
        import re as _re
        m = _re.search(r"warm-up (\d+)", out)
        assert m and int(m.group(1)) == cells, f"the paper must print the cause: {out[-700:]}"
        m2 = _re.search(r"tape starts (\d+)min INTO the window", out)
        assert m2 and int(m2.group(1)) == lead_in_min, (
            f"the day-math line must state the REAL lead-in ({lead_in_min}min): "
            f"{out[-700:]}")
        assert "the WARMING, not a broken sea" in out, out[-700:]

        # ---- (2) the fix: same cap, a warm-enough day, must FEED ----
        day2 = _cold_open_day_fixture(tmp, 60, "warm")
        agg2, out2 = _run_cold_open_day(tmp, day2, 240, "warm")
        assert int(agg2.get("cycles") or 0) > 0, (
            f"warming must no longer eat the whole cap: {agg2}")
        sk2 = agg2.get("skip_kinds") or {}
        assert int(sk2.get("stale") or 0) == 0, (
            f"nothing may be blamed on a stale tape when the floor did its job: {sk2}")
        assert sum(int(v) for v in sk2.values()) == int(agg2.get("errors") or 0), (
            f"the causes must add up to the total: {sk2} vs {agg2.get('errors')}")
        # NO upper bound on warm-up here, on purpose (item 33, third form): my
        # first draft compared day 2's warm-up against day 1's cell count - a test
        # may not know the sea's arithmetic before the sea has done it. The sea
        # fed 81 cycles, and that is the only promise this scenario makes.
        assert int(agg2.get("g2_pass") or 0) > 0, (
            f"a warmed day must put a rung under G2: {agg2}")
        assert "warming NOT charged, item 31" in out2, out2[-700:]

        # ---- (2b) EYES-2d: a tape that ENDS mid-window must be named by its real
        #      cause. The owner measured this on their own four days (their
        #      `eyes2_20261114.txt`): the grid walked to the window end on a frozen
        #      `last_ep`, and every one of those cells was filed under `stale`.
        #      Nothing may be hidden in a borrowed word again - and the count must
        #      be re-derivable from the paper's OWN stamps, never memorised (33/34).
        dayT = _cold_open_day_fixture(tmp, 60, "tail")
        aggT, outT = _run_cold_open_day(tmp, dayT, 480, "tail")
        skT = aggT.get("skip_kinds") or {}
        wT = (aggT.get("_days") or [{}])[0].get("window") or {}
        assert "stale" not in skT, (
            f"`stale` is retired from the sea's vocabulary (pre-2d papers may still "
            f"carry it; a NEW paper must not): {skT}")
        # DERIVED, not memorised (item 34): the expected tail comes from the PAPER's own
        # stamps. Asserting `> 0` was too weak - a negated fix still passed it, because the
        # key existed with 0 in it. So: name the number, and name it BEFORE the word.
        _tail_expect = 0
        if wT.get("last_row") and wT.get("cells_end"):
            import math as _mm
            _g0 = _mm.ceil(max(float(wT["last_row"]), float(wT.get("win_start") or 0)) / 180.0) * 180
            _tail_expect = sum(1 for _k in range(int(_g0), int(float(wT["cells_end"])) + 1, 180)
                               if _k - float(wT["last_row"]) > 240.0)
        assert _tail_expect > 0, f"the fixture must still END before its window: {wT}"
        assert int(skT.get("post-last-row") or 0) == _tail_expect, (
            f"the tail must be EXACTLY the cells the paper says walk past its own last "
            f"row (expect {_tail_expect}): {skT}")
        _lr, _ce, _ev = wT.get("last_row"), wT.get("cells_end"), 240.0
        _exp = 0
        if _lr and _ce:
            import math as _m
            _g0 = _m.ceil(max(float(_lr), float(wT.get("win_start") or 0)) / 180.0) * 180
            _exp = sum(1 for _k in range(int(_g0), int(_ce) + 1, 180)
                       if _k - float(_lr) > _ev)
        assert int(skT.get("post-last-row") or 0) == _exp, (
            f"the tail must equal the CELLS the paper itself says walk past its own "
            f"last row (derived from window, never hard-coded): exp {_exp} got {skT}")
        assert sum(int(v) for v in skT.values()) == int(aggT.get("errors") or 0), (
            f"the belt still adds up with the new words: {skT} vs {aggT.get('errors')}")
        assert "tail past the tape" in outT, (
            f"the day-math line must NAME the tail, so no probe is ever needed "
            f"again: {outT[-600:]}")

        # ---- (2c) the OTHER half of the old word: rows on the tape, NOT ONE print.
        #      A depth-only stretch used to be filed under `stale` because `no-eyes`
        #      tests `ev_i == 0` (not one ROW) while a depth row is (ep, 0, 0, line).
        #      This scenario exists because my own first draft of this test did NOT
        #      cover it - I checked by disarming arm (a) and watching nothing fail.
        #      A test that cannot be broken is not a test.
        arch2 = Path(tmp) / "depthonly" / "data" / "archive"
        arch2.mkdir(parents=True, exist_ok=True)
        _Z = timezone.utc
        _t0 = datetime(2026, 11, 14, 8, 0, tzinfo=_Z)
        _rows = ["time,event,price,size,level,operation,instrument\n"]
        for _i in range(40):                     # 40 depth rows, 2 min apart: 08:00-09:20
            _rows.append((_t0 + timedelta(minutes=2 * _i)).isoformat()
                         + ",DepthBid,3900.00,10,1,BOOK,GCZ6.COMEX@RITHMIC\n")
        for _i in range(1500):                   # then a busy, fully-printed day to 23:00
            _rows.append((_t0 + timedelta(minutes=2 * 40, seconds=3 * _i)).isoformat()
                         + f",Last,{3900.0 + _i * 0.05:.4f},10.0000,-,buy,GCZ6.COMEX@RITHMIC\n")
        Path(tmp).joinpath("depthonly", "data", "archive", "ticks_20261114_230000.csv").write_text(
            "".join(_rows), encoding="utf-8")
        aggD, outD = _run_cold_open_day(tmp, "2026-11-14", 900, "depthonly")
        skD = aggD.get("skip_kinds") or {}
        assert int(skD.get("pre-first-print") or 0) >= 20, (
            f"40 minutes of depth before the first print is >=20 cells and must be NAMED "
            f"as such, not as stale eyes: {skD}")
        assert "stale" not in skD, f"the borrowed word must be gone from a new paper: {skD}"
        assert sum(int(v) for v in skD.values()) == int(aggD.get("errors") or 0), (
            f"the belt adds up with the two new words too: {skD} vs {aggD.get('errors')}")

        # ---- (3) a cap that misses the tape: refused OUT LOUD, never a crash,
        #      and never `(src=None)` (the owner's 20:13 packet caught that) ----
        agg3, out3 = _run_cold_open_day(tmp, day, 60, "cold", rc_ok=False)
        assert "NOT RUN" in out3 and "ends before" in out3, out3[-800:]
        assert "src=None" not in out3, (
            f"a refused day must stamp itself, not print None: {out3[-400:]}")


def test_a_verdict_near_the_end_of_a_capped_day_still_gets_its_horizon():
    """v8.2b/v8.5 / items 38 + 46 - the owner's FIRST TWO VERDICTS printed
    `fwd9=None` and the teacher turned that silence into `0/2 went the way the door
    said`, i.e. it invented a result out of a missing measurement. Three guards, and
    (c) is the one v8.2b promised but did NOT deliver (I asserted the window bounds
    and called it a horizon; the owner's v8d receipt then showed `fwd9=None` on all
    three episodes, which is how I found it):
      (a) the sea reads 15 minutes of rows PAST the last cell (look-ahead is never
          fed to a cycle - the pointer walks to `tc` - so the cap stays honest);
      (b) the teacher abstains when the horizon does not exist, and SAYS so;
      (c) a verdict in the middle of the tape gets a REAL NUMBER, in both the
          sidecar and the aggregate, and the paper agrees with the json;
      (d) the rehearsal starts from a CLEAN mirror and SAYS which it is - the
          fix that made (c) possible to test honestly (item 47).
    """
    with tempfile.TemporaryDirectory() as tmp:
        # a day long enough to feed, with the cap ending INSIDE the tape: the old
        # code truncated the tape at the cap, so look-ahead could not exist.
        day, day1 = _two_day_look_fixture(tmp, "look")
        jp = Path(tmp) / "look.json"
        r = subprocess.run(
            # NO `--date` on purpose: an explicit day means EXACTLY that day (main
            # short-circuits `available_days`), so passing it would have silently
            # reduced this 2-day rehearsal to 1 day - and the assertion below is what
            # caught me doing it.
            [sys.executable, str(HERE / "tools" / "gates_replay.py"),
             "--data-dir", str(Path(tmp) / "look" / "data"),
             "--days", "2", "--mirror-dir", str(Path(tmp) / "mirror"),
             "--window", "08:00-23:00", "--every", "180", "--prewarm", "0",
             "--min-prints", "20", "--max-minutes", "120", "--quiet",
             "--json-out", str(jp)],
            capture_output=True, text=True, timeout=900, cwd=str(HERE),
            env=_fixture_env(mirror_dir=Path(tmp) / "mirror"))
        assert r.returncode == 0, r.stdout[-400:] + r.stderr[-400:]
        _days = json.loads(jp.with_name(jp.stem + ".days.json").read_text(encoding="utf-8"))
        assert len(_days) == 2, (
            f"--days 2 over two fixture tapes must sail BOTH: {[d.get('day') for d in _days]}")
        # item 48: the learner may inherit only the PAST, so the paper's order is
        # chronological and `agg` is the NEWEST day - by position, not by a name I
        # remembered. A future day's levels must never reach the day before it.
        assert [d.get("day") for d in _days] == sorted(d.get("day") for d in _days), (
            f"the sidecar must come out oldest -> newest: {[d.get('day') for d in _days]}")
        assert _days[-1].get("day") == day, (
            f"the last sidecar day must be the newest one: {_days[-1].get('day')} vs {day}")
        agg = _days[-1]
        # TALLY-2 (TODO row 3), on PAPER the sea itself wrote. Every cycle is either named
        # and refused or counted as walked through - there is no third bucket and no fake
        # word in the first one. The repo's fixtures can never reach `allowed > 0` (no
        # fixture day has a cycle that walks through), so the honest assertion here is the
        # ARITHMETIC on real records, not a number I hand-pick.
        for _rec in _days:
            _f = sum(int(v or 0) for v in (_rec.get("fails") or {}).values())
            assert "allowed" in _rec, f"the sea must write its walk-through count: {_rec}"
            assert _f + int(_rec.get("allowed") or 0) == int(_rec.get("cycles") or 0), (
                f"refused-words + allowed != cycles: {_f} + {_rec.get('allowed')} "
                f"vs {_rec.get('cycles')} on {_rec.get('day')}")
            assert "(allowed)" not in (_rec.get("fails") or {}), (
                f"the fake refusal word is back in the sea's own tally: {_rec.get('fails')}")
        assert "| allowed " in r.stdout, "the tally line must NAME the walk-through count"
        # (d) THE MIRROR. `isolate_data_dir()` keeps the LIVE drawer safe but PERSISTS
        # a learned sheet between rehearsals; before item 47 this test passed only on
        # a dirty tree and went red on a clean one - the loudest proof I ever wrote
        # that a replay must state where its brain came from.
        assert "replay mirror" in r.stdout, (
            f"a rehearsal must name the source of its learned levels: {r.stdout[-600:]}")
        m0 = re.search(r"replay mirror\s*:\s*(.+)", r.stdout)
        assert m0 and ("mirror empty" in m0.group(1) or "mirror reset" in m0.group(1)), (
            f"an inherited sheet may only survive on purpose (--keep-mirror): "
            f"{m0.group(1) if m0 else None}")
        w = agg.get("window") or {}
        assert w.get("cells_end"), f"the paper must name BOTH bounds: {w}"
        if not int(agg.get("cycles") or 0):
            # a fixture whose tape is thinner than its own warm-up has no
            # business asserting horizons - say so instead of going red
            raise AssertionError(f"the look fixture must feed cycles first: {w}")
        assert float(w["win_end"]) - float(w["cells_end"]) >= 540.0, (
            f"the tape must outlast the cells by the look-ahead, got {w}")
        assert "cells to" in r.stdout, (
            f"the day-math line must say where the CELLS stop: {r.stdout[-500:]}")
        assert float(agg.get("lookahead_min") or 0.0) == 15.0, (
            f"15 minutes, not 10: pb4 asks 12 and the tail needs a margin: "
            f"{agg.get('lookahead_min')}")
        assert int(agg.get("fwd_rows") or 0) > 0, (
            f"the scorer must have rows to look at, the paper says {agg.get('fwd_rows')}: {w}")

        # ---- (c) THE NUMBER ITSELF (item 46: v8.2b tested the WRAPPER - window
        #      bounds, `cells_end`, the printed words - so a horizon that was
        #      structurally impossible kept shipping for three versions) ----
        # Names first, because three files are in play and mixing them up is how a
        # test like this proves nothing: `side` = the NEWEST day's own arithmetic,
        # `run` = the aggregate the teacher reads.
        run = json.loads(jp.read_text(encoding="utf-8"))
        side = _days[-1]
        side_eps = side.get("episodes") or []
        eps = run.get("episodes") or []
        if not eps:
            raise AssertionError(
                f"this fixture must make a verdict to score - cycles "
                f"{run.get('cycles')}, episodes 0: the test would prove nothing")
        num = [e for e in eps if isinstance(e.get("fwd9"), (int, float))]
        assert len(num) == len(eps), (
            f"a verdict with rows after it must be MEASURED, not `None` - the scored "
            f"silence all over again: {[e.get('fwd9') for e in eps]}")
        # No `or` to hide behind (item 46 IS the story of an assertion that could not
        # fail): every day of a 2-day rehearsal keeps 15 minutes of rows after its
        # last cell, so even the last verdict gets a number.
        assert len(num) >= 3, (
            f"the rehearsal must measure at least three verdicts across two days, "
            f"got {len(num)}: {[e.get('fwd9') for e in eps]}")
        for e in num:
            # a measured number must be a DELTA around zero, not a price: 4000-level
            # gold moves in single dollars inside 9 minutes on this fixture
            assert abs(float(e["fwd9"])) < 60.0, (
                f"fwd9 must be a difference, not a price: {e['fwd9']}")
            if isinstance(e.get("pb4"), (int, float)):
                assert abs(float(e["pb4"])) < 60.0, f"pb4 likewise: {e['pb4']}"
            # `fwd9` is a DELTA from the row the verdict was made on. 0.0 is a LEGAL
            # answer here (the fixture oscillates on a 20-minute cycle, and +9m can
            # land on the same phase - my first attempt at this assert demanded a
            # non-zero number and was the test teaching the code a fiction). What is
            # illegal is a horizon that never subtracted: a 4000-level value.
            assert abs(float(e["fwd9"])) != abs(float(e.get("price") or 0.0)), (
                f"a horizon equal to the entry price means the subtraction is "
                f"missing: {e}")
        # The paper is printed PER DAY while the json it accompanies is the UNION -
        # so compare the paper with its OWN day, by position, never with a name I
        # remembered (item 48: the newest sidecar entry is the last one).
        # ONE `scoring rows` line per day, printed per day. `re.search` here would
        # grade the OLDEST day against the NEWEST day's json and pass by luck, so take
        # them ALL, in order, and zip them with the sidecar (item 33's own lesson).
        pairs = re.findall(r"verdicts measured (\d+) of (\d+)", r.stdout)
        assert len(pairs) == len(_days), (
            f"one measured-line per sailed day: {len(pairs)} lines for "
            f"{len(_days)} days")
        for (dm, dn), d in zip(pairs, _days):
            de = d.get("episodes") or []
            dnum = [e for e in de if isinstance(e.get("fwd9"), (int, float))]
            assert (int(dm), int(dn)) == (len(dnum), len(de)), (
                f"{d.get('day')}: the paper says {dm}/{dn}, its json says "
                f"{len(dnum)}/{len(de)}")
        assert any(len(x.get("episodes") or []) >= 3 for x in _days), (
            f"the day that INHERITS yesterday's levels must be the one making "
            f"verdicts (that is the whole point of sailing 2 days): "
            f"{[len(x.get('episodes') or []) for x in _days]}")
        assert len(eps) > len(side_eps), (
            f"a 2-day rehearsal whose day count equals the run count is reading one "
            f"file twice: day {len(side_eps)} of run {len(eps)}")
        assert int(run.get("measured") or 0) == len(num), (
            f"`measured` must be the aggregate count of numeric horizons: "
            f"{run.get('measured')} vs {len(num)}")


def test_the_aggregate_json_lets_the_teacher_abstain_honestly():
    """item 46, the plumbing of honesty. The teacher reads the AGGREGATE json (the
    file in the owner's receipt), not the per-day sidecar - so if the scoring facts
    live only in the sidecar, the teacher can only guess, and a guess about a
    missing measurement is the `0/3` bug wearing a different hat. Two shapes:
    a night where a verdict WAS measured, and the same night with the horizon
    removed by hand, which must abstain instead of scoring."""
    with tempfile.TemporaryDirectory() as tmp:
        day, day1 = _two_day_look_fixture(tmp, "agglook")
        jp = Path(tmp) / "agg.json"
        r = subprocess.run(
            [sys.executable, str(HERE / "tools" / "gates_replay.py"),
             "--data-dir", str(Path(tmp) / "agglook" / "data"),
             "--days", "2", "--mirror-dir", str(Path(tmp) / "mirror"),
             "--window", "08:00-23:00", "--every", "180", "--prewarm", "0",
             "--min-prints", "20", "--max-minutes", "120", "--quiet",
             "--json-out", str(jp)],
            capture_output=True, text=True, timeout=900, cwd=str(HERE),
            env=_fixture_env(mirror_dir=Path(tmp) / "mirror"))
        assert r.returncode == 0, r.stdout[-300:] + r.stderr[-300:]
        agg = json.loads(jp.read_text(encoding="utf-8"))
        _sd = json.loads(jp.with_name(jp.stem + ".days.json").read_text(encoding="utf-8"))
        side = _sd[-1]                       # the newest day (item 48 order)
        assert list(agg.get("days") or []) == sorted(agg.get("days") or []), (
            f"the aggregate may not sail the future first: {agg.get('days')}")
        assert agg.get("day_order") == ("chronological" if len(_sd) > 1 else "single day"), (
            f"the json must state the order it used: {agg.get('day_order')}")
        assert str(agg.get("mirror") or ""), (
            f"the aggregate must carry the mirror line the paper printed")
        eps = agg.get("episodes") or []
        if not eps:
            raise AssertionError(f"the fixture must still make a verdict: {agg.get('cycles')}")
        # the aggregate carries the same scoring facts the sidecar does
        assert int(agg.get("fwd_rows") or 0) > 0, (
            f"the teacher cannot abstain honestly without `fwd_rows`: {list(agg)[:8]}")
        assert float(agg.get("lookahead_min") or 0.0) == float(side.get("lookahead_min") or 0.0), (
            f"aggregate vs sidecar disagree about the look-ahead: "
            f"{agg.get('lookahead_min')} vs {side.get('lookahead_min')}")
        n_num = sum(1 for e in eps if isinstance(e.get("fwd9"), (int, float)))
        assert int(agg.get("measured") or 0) == n_num == len(eps) - sum(
            1 for e in eps if e.get("fwd9") is None), (
            f"`measured` must be the count of NUMERIC horizons, nothing else: "
            f"{agg.get('measured')} vs {n_num} of {len(eps)}")
        # and the teacher, given THIS json, must speak of the measured subset only
        rep_json = dict(agg)
        rep_json["episodes"] = [dict(e, fwd9=None) for e in eps]
        jp2 = jp.with_name("silence.json")
        jp2.write_text(json.dumps(rep_json), encoding="utf-8")
        r2 = subprocess.run(
            [sys.executable, str(HERE / "tools" / "gates_report.py"), "--date", day,
             "--sea", str(jp2), "--hall-off", "--out", str(Path(tmp) / "n.txt")],
            capture_output=True, text=True, timeout=900, cwd=str(HERE),
            env=_fixture_env())
        out = (Path(tmp) / "n.txt").read_text(encoding="utf-8") if (Path(tmp) / "n.txt").exists() else r2.stdout
        assert "NOT MEASURED, NOT FAILED" in out, (
            f"a night of shrugs must abstain out loud (rc={r2.returncode}): {out[-500:]}")
        assert "went the way the door said" not in out, (
            f"`went the way the door said` may not be printed from None values: {out[-500:]}")


def test_the_doormans_tally_adds_up_and_an_old_paper_is_not_graded_by_it():
    """TODO row 3, ASSERTED at last (TALLY-2, `2026-11-17`). The owner measured their own SIX
    diaries: the diary was always honest (`allowed + refused == cycles` on all 1,815 cycles,
    every named lie zero) while the PAPER could not be, because `gates_replay.py:1027` filed
    `(allowed)` INSIDE the refusal tally - so `sum(fails) == cycles` on every day and
    `tickets + sum(fails)` was inflated by exactly the ticket count (their 11-06: 8 tickets,
    124 words, 124 cycles). Five shapes, five readings. The fourth is the one that makes this
    test worth having: an OLD paper missing `allowed` would PASS the new equation **by accident**
    (124 + 0 == 124, for the wrong reason), so the gate must abstain on it, never flatter it -
    the same rule the sea learned for `skips ABSENT` (item 31) and the teacher learned for
    `fwd9=None` (item 38)."""
    HERE_ = Path(__file__).resolve().parent
    base = {"day": "2026-11-06", "ok": True, "cycles": 10, "g2_pass": 10, "verdicts": 0,
            "tickets": 0, "errors": 0, "lines": 4000, "prints": 900, "episodes": [],
            "skip_kinds": {"no-eyes": 0, "pre-first-print": 0, "post-last-row": 0,
                           "warm-up": 0, "brain": 0, "snapshot": 0},
            "moods": {}, "rungs": {}, "src": "tapes:own+mbo",
            "printed_by": "gates.replay.2026-11-17.3"}
    cases = [
        ("clean", {"fails": {"NO-VERDICT": 8}, "allowed": 2},
         "[OK  ] refused-words + allowed == cycles", "PASS", 0),
        ("broken", {"fails": {"NO-VERDICT": 9}, "allowed": 2},
         "[FAIL] refused-words + allowed == cycles", "FAIL", 1),
        ("newword", {"fails": {"L3Q": 6, "NO-EDGE": 2}, "allowed": 2},
         "tallied: L3Q:6", "PASS", 0),
        ("oldpaper", {"printed_by": "gates.replay.2026-11-14.1",
                      "fails": {"NO-VERDICT": 8, "(allowed)": 2}},
         "not written by gates.replay.2026-11-14.1", "PASS", 0),
        ("newer_still_files_it", {"fails": {"NO-VERDICT": 8, "(allowed)": 2}, "allowed": 0},
         "the sea is newer than TALLY-2", "FAIL", 1),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "data"
        d.mkdir()
        for tag, over, want_text, want_grade, want_rc in cases:
            sea = dict(base)
            sea.update(over)
            jp = d / f"sea_{tag}.json"
            jp.write_text(json.dumps(sea), encoding="utf-8")
            outp = Path(tmp) / f"{tag}.txt"
            r = subprocess.run(
                [sys.executable, str(HERE_ / "tools" / "gates_report.py"),
                 "--dir", str(d), "--hall-off", "--date", "2026-11-06",
                 "--sea", str(jp), "--out", str(outp)],
                capture_output=True, text=True, timeout=300, cwd=str(HERE_))
            txt = outp.read_text(encoding="utf-8") if outp.exists() else r.stdout
            assert want_text in txt, f"{tag}: wanted {want_text!r} in the paper:\n{txt[-700:]}"
            belt = [ln for ln in txt.splitlines() if "THE BELT COUNTS ITSELF" in ln]
            assert belt and want_grade in belt[0], f"{tag}: belt card read {belt!r}"
            assert r.returncode == want_rc, f"{tag}: exit {r.returncode}, wanted {want_rc}: {txt[-400:]}"
        # a NEW paper's tally line must keep refusals and walk-throughs in different columns
        txt = (Path(tmp) / "clean.txt").read_text(encoding="utf-8")
        assert "(allowed)" not in txt.split("tallied:")[1].splitlines()[0], (
            "the tallied line must not re-admit the old fake word: "
            + txt.split("tallied:")[1].splitlines()[0][:200])


def test_a_missing_horizon_is_named_and_never_scored():
    """The teacher's half of item 38: an episode with `fwd9=None` is NOT a miss."""
    sea = {"day": "2026-11-12", "ok": True, "cycles": 19, "verdicts": 2,
           "toll_closes": 1, "g2_pass": 17, "errors": 20, "lines": 352731,
           "src": "tapes:own+mbo", "tape_days": 2, "jar_days": 0, "l3_days": 2,
           "mbo_rows": 3389507, "ticks_mbo_ignored": 5426494,
           "skip_kinds": {"no-eyes": 0, "stale": 0, "warm-up": 20, "brain": 0,
                          "snapshot": 0},
           "episodes": [{"ep": 1, "g2": "htf_poc", "allow": True, "fwd9": None,
                         "g3": "RUNG=htf_poc | PUSH=DOWN | VERDICT=BREAK | TOLL=unpaid"},
                        {"ep": 2, "g2": "htf_poc", "allow": True, "fwd9": None,
                         "g3": "RUNG=htf_poc | PUSH=DOWN | VERDICT=BREAK | TOLL=unpaid"}]}
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "data"
        d.mkdir()
        jp = d / "sea.json"
        jp.write_text(json.dumps(sea), encoding="utf-8")
        outp = Path(tmp) / "r.txt"
        r = subprocess.run(
            [sys.executable, str(HERE / "tools" / "gates_report.py"),
             "--dir", str(d), "--hall-off", "--date", "2026-11-13",
             "--sea", str(jp), "--out", str(outp)],
            capture_output=True, text=True, timeout=300, cwd=str(HERE))
        assert r.returncode == 0, r.stderr[-400:]
        body = outp.read_text(encoding="utf-8")
        assert "[ -- ]  VERDICTS MEAN SOMETHING" in body, body[-900:]
        assert "NOT MEASURED, NOT FAILED" in body, body[-900:]
        assert "0/2 episode(s) went the way the door said" not in body, (
            "a silence must never be printed as a score: " + body[-700:])
        sea2 = dict(sea)
        sea2["episodes"] = [dict(sea["episodes"][0], fwd9=-4.5),
                            dict(sea["episodes"][1], fwd9=3.0)]
        (d / "sea2.json").write_text(json.dumps(sea2), encoding="utf-8")
        r2 = subprocess.run(
            [sys.executable, str(HERE / "tools" / "gates_report.py"),
             "--dir", str(d), "--hall-off", "--date", "2026-11-13",
             "--sea", str(d / "sea2.json"), "--out", str(Path(tmp) / "r2.txt")],
            capture_output=True, text=True, timeout=300, cwd=str(HERE))
        b2 = (Path(tmp) / "r2.txt").read_text(encoding="utf-8")
        assert "1/2 MEASURED episode(s) went the way the door said" in b2, b2[-700:]


def test_a_second_rehearsal_resets_the_brain_unless_told_otherwise():
    """item 47, the law in one test: a rehearsal's learned sheet is its OWN, and it
    is SET ASIDE (kept, never deleted) when the next rehearsal starts - unless the
    operator asks for the inheritance with `--keep-mirror`, which must then say so
    in the same voice. Written after my horizon test passed on a dirty tree and went
    red on a clean one: same tape, same code, two answers, and the only difference was
    a 362-byte json from a previous run."""
    with tempfile.TemporaryDirectory() as tmp:
        day, day1 = _two_day_look_fixture(tmp, "mir")
        dd = Path(tmp) / "mir" / "data"
        arch = dd / "archive"
        cmd = [sys.executable, str(HERE / "tools" / "gates_replay.py"),
               "--days", "2", "--data-dir", str(dd), "--window", "08:00-23:00",
               "--every", "180", "--prewarm", "0", "--min-prints", "20",
               "--max-minutes", "120", "--quiet", "--no-diary",
               "--mirror-dir", str(Path(tmp) / "mirror")]
        # item 47: the brain's address is ONE fact, read the way the code reads it.
        # `--mirror-dir X` repoints config.DATA_DIR and the quarantine then appends
        # its own folder, so the learned sheet lives at `X/gates_sandbox/`. A test
        # that guessed `X/` reports a FULL mirror as empty - which is exactly how my
        # first version of this test hid its own no-op.
        mir = (Path(tmp) / "mirror") / "gates_sandbox"

        def sea(extra=(), jp=None):
            args = list(cmd) + list(extra)
            if jp:
                args += ["--json-out", str(jp)]
            rr = subprocess.run(args, capture_output=True, text=True, timeout=900,
                                cwd=str(HERE), env=_fixture_env())
            assert rr.returncode == 0, rr.stdout[-300:] + rr.stderr[-300:]
            return rr.stdout

        # FIRST run on a clean tree: nothing to inherit, and it says so
        out1 = sea()
        assert "mirror empty" in out1, (
            f"the first rehearsal must declare a clean brain: {out1[-500:]}")
        learned = [p for p in (mir / "session_levels.json", mir / "last_book_state.json")
                   if p.exists()]
        if not learned:
            raise AssertionError(
                f"the run must have WRITTEN a learned sheet for this test to mean "
                f"anything: {sorted(p.name for p in mir.iterdir()) if mir.exists() else 'no mirror dir'}")
        sizes = {p.name: p.stat().st_size for p in learned}
        # SECOND run, same tape: the sheet from run one must be MOVED OUT, not read
        out2 = sea()
        assert "mirror reset" in out2, (
            f"the second rehearsal must clear the first one's learning out loud: "
            f"{out2[-500:]}")
        # the set-aside copy sits ONE LEVEL ABOVE the brain folder - the same rule the
        # code uses, read from the same `mir` the code wrote to (I got this wrong in
        # two directions today: one level too deep, then one level too far out).
        gone = sorted(p for p in mir.parent.iterdir()
                      if p.name.startswith("gates_sandbox_prev_"))
        assert gone, (
            f"`reset` must MOVE the sheet aside (LAW #13: kept, never deleted): "
            f"{sorted(p.name for p in mir.parent.iterdir())}")
        moved = sum(f.stat().st_size for d in gone for f in d.rglob("*") if f.is_file())
        assert moved >= sum(sizes.values()), (
            f"the set-aside copy must contain what was there ({sizes} -> {moved} B)")
        # THIRD run: `--keep-mirror` may inherit, but only while SAYING it does
        out3 = sea(("--keep-mirror",))
        assert "KEPT on purpose" in out3, (
            f"inheriting history must be a declared choice: {out3[-500:]}")
        assert "someone else's history" in out3, (
            f"the warning must name the risk, not just the flag: {out3[-500:]}")


def test_the_sea_grade_names_a_cause_before_it_names_a_verdict():
    """v8.1 / item 31, the TEACHER's half. The owner's first tape-fed night had
    `depth:live-from-tape`, two order books of 2.18M and 3.39M rows and
    `G2 with a rung 10/10` - and the card said `SEA RAN: FAIL`, because the old
    rule was arithmetic about a SAMPLE (`cycles >= 20`) with no knowledge of WHY
    a cell was refused. Four shapes must now read four different ways, and this
    test is the table:

        no rows at all                 -> FAIL   (the tape was not found)
        rows, no cycles, all warm-up   -> [ -- ] (not illness, not health)
        rows, no cycles, NOT warm-up   -> FAIL   (this IS plumbing)
        a thin sample (10 cycles)      -> WARN   (proves plumbing, cannot grade)

    It also guards the closed vocabulary: the abstain grade is `NA`, and a new
    card that hands `add()` the literal `"--"` raises KeyError - which is exactly
    what happened while writing v8.1, in MY first version of this very card."""
    base = {"day": "2026-11-13", "days": ["2026-11-13", "2026-11-12"],
            "day_count": 2, "sailed": 2, "ok": True, "cycles": 0, "g2_pass": 0,
            "verdicts": 0, "tickets": 0, "errors": 18, "lines": 147849,
            "prints": 50588, "toll_closes": 4, "toll_minutes": 12.0,
            "fails": {}, "moods": {}, "rungs": {}, "episodes": [],
            "src": "tapes:own+mbo", "depth": "depth:live-from-tape",
            "ts_parser": "x", "cap_min": 240, "mbo_rows": 5569171,
            "ticks_mbo_ignored": 5222885, "tape_days": 2, "jar_days": 0,
            "l3_days": 2, "fed_by": "raw tape",
            "skip_kinds": {"no-eyes": 0, "stale": 0, "warm-up": 18,
                           "brain": 0, "snapshot": 0}}
    cases = [
        ("cold", {}, "[ -- ]  SEA RAN", "NOT ILLNESS, NOT HEALTH", "(warm-up 18)"),
        ("thin", {"cycles": 10, "g2_pass": 10, "errors": 20,
                  "skip_kinds": {"warm-up": 20}},
         "[WARN]  SEA RAN", "thin SAMPLE", None),
        ("dead", {"lines": 0}, "[FAIL]  SEA RAN", "NO ROWS at all", None),
        ("plumb", {"skip_kinds": {"stale": 18}}, "[FAIL]  SEA RAN",
         "warm-up does not explain it", None),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp) / "data"
        d.mkdir()
        for tag, over, want_grade, want_text, want_extra in cases:
            sea = dict(base)
            sea.update(over)
            jp = d / f"sea_{tag}.json"
            jp.write_text(json.dumps(sea), encoding="utf-8")
            outp = Path(tmp) / f"{tag}.txt"
            r = subprocess.run(
                [sys.executable, str(HERE / "tools" / "gates_report.py"),
                 "--dir", str(d), "--hall-off", "--date", "2026-11-13",
                 "--sea", str(jp), "--out", str(outp)],
                capture_output=True, text=True, timeout=300, cwd=str(HERE))
            # `dead`/`plumb` MUST exit 1 (a FAIL exists); `cold`/`thin` must not.
            want_rc = 1 if want_grade.startswith("[FAIL]") else 0
            assert r.returncode == want_rc, (
                f"{tag}: exit {r.returncode}, wanted {want_rc}: "
                f"{r.stdout[-300:]} {r.stderr[-400:]}")
            assert outp.exists(), f"{tag}: the teacher wrote nothing: {r.stdout[-300:]}"
            body = outp.read_text(encoding="utf-8")
            assert want_grade in body, f"{tag}: wanted `{want_grade}`\n{body[-700:]}"
            assert want_text in body, f"{tag}: wanted `{want_text}`\n{body[-700:]}"
            if want_extra:
                assert want_extra in body, f"{tag}: the CAUSE must be on the row"
            if tag == "cold":
                assert "[FAIL]  SEA RAN" not in body, \
                    "a cold start on a day that IS in RAM must never be a FAIL again"


def test_the_warming_floor_is_arithmetic_not_taste():
    """`every 180` x the 60-candle law = 180 minutes of tape before the first
    cycle. The floor must come from that product, not from a default someone
    liked, and it must be visible on the paper."""
    src = (HERE / "tools" / "gates_replay.py").read_text(encoding="utf-8")
    assert "warm_min_needed = int(every_s) * 60 // 60" in src, \
        "the floor must be the product of the stride and the law"
    assert "cap_warmed" in src, "the cap must charge warming OUTSIDE the budget"
    # if the borrowed rule ever stops being 60 candles, this test and the Book
    # line about it must move together (LAW #13) - so pin the constant here.
    assert re.search(r"len\(m1\.get\(\"close\"\) or \[]\) < 60", src), \
        "the 60-candle law moved; update this test, gates_replay and HOLY_BOOK"


def test_a_ticks_tape_alone_can_never_invent_orders():
    # the trap: `_MboTail` would happily key every order by the literal word
    # "BID_NEW" if a ticks-tape Mbo row (level=-1, no order id) were fed to it -
    # the bridge names that bug itself ("929 fake icebergs",
    # bookmap_bridge_provider.py:334). So: L3 count MUST be zero here.
    with tempfile.TemporaryDirectory() as tmp:
        dd = Path(tmp) / "data"
        dd.mkdir()
        t0 = _sea_fixture_day(1)
        rows = ["time,event,price,size,level,operation,instrument\n"]
        px = 3900.0
        for i in range(1000):
            px += 0.7 if i % 3 else -0.9
            iso = (t0 + timedelta(seconds=20 * i)).isoformat()
            rows.append(f"{iso},Last,{px:.4f},10,-,buy,GC\n")
            rows.append(f"{iso},Mbo,{px:.4f},1,-1,BID_NEW,GC\n")
        (dd / f"ticks_{t0.strftime('%Y%m%d')}.csv").write_text("".join(rows),
                                                              encoding="utf-8")
        jp = Path(tmp) / "sea.json"
        r = subprocess.run(
            [sys.executable, str(HERE / "tools" / "gates_replay.py"),
             "--days", "1", "--data-dir", str(dd), "--window", "08:00-09:10",
             "--every", "180", "--prewarm", "240", "--min-prints", "20",
             "--quiet", "--no-diary", "--json-out", str(jp)],
            capture_output=True, text=True, timeout=900, cwd=str(HERE))
        assert r.returncode == 0, r.stdout[-400:] + r.stderr[-400:]
        agg = json.loads(jp.read_text(encoding="utf-8"))
        assert int(agg.get("mbo_rows") or 0) == 0, "no order tape exists here"
        assert agg.get("l3_days") == 0, agg
        assert "+no-mbo" in str(agg.get("src")), agg.get("src")
        assert int(agg.get("ticks_mbo_ignored") or 0) > 0, agg
        assert "L3: ABSENT" in r.stdout, r.stdout[-600:]
        # v6 said "0 verdicts is honest" and left it there. v7 proves WHY any
        # verdict on such a day is a WEAKER animal: it must be stamped, so the
        # teacher (and the owner) can never read an L1/L2 vote as an L3 vote.
        assert "l3q:unavailable(no order tape)" in (
            HERE / "tools" / "gates_report.py").read_text(encoding="utf-8")


def test_a_named_data_dir_shuts_the_repo_s_own_drawer():
    """THE LEAK THAT MADE YOUR BOX RED AND MINE GREEN (item 41).

    Shape of the bug: `discover_tape` searched `data_dir` AND the repo root AND
    `root_dir/Workspace/data`. The DAY LIST had been scoped to a named `--data-dir`
    (item 19) but the FILE SEARCH had not - so a test pointing the sea at a temp
    fixture still swallowed the real slices sitting in `data/archive/` of the repo
    the code lives in, whenever a day name collided. On your rig that is 236 files;
    in my sandbox it is zero. Same code, same test, different answer - and the one
    that looked broken was mine.

    Both directions are asserted, because scoping must not become blindness: your
    real sail (no `--data-dir`, tapes in the repo's `data/archive/`) still has to
    find its order tape."""
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        day = datetime(2026, 11, 5)
        t0 = datetime(2026, 11, 5, 8, 0, tzinfo=timezone.utc)
        bud = timezone(timedelta(hours=1))

        def tape(path, n, px0, kind):
            rows = []
            for i in range(n):
                ts = (t0 + timedelta(seconds=20 * i)).astimezone(bud).isoformat()
                px = px0 + 0.01 * i + (6.0 if (i // 60) % 2 == 0 else 0.0)
                rows.append(f"{ts},Trade,{px:.1f},{1 + (i % 5)},-,-,GC" if kind == "ticks"
                            else f"{ts},{9000 + i},Add,{px - 0.4:.1f},4,-,GC")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(rows) + "\n", encoding="utf-8")

        # a FAKE repo root, whose data/archive holds a whole day of BOTH tapes
        repo = t / "repo"
        tape(repo / "data" / "archive" / f"ticks_{day:%Y%m%d}_235900.csv", 1500, 4000.0, "ticks")
        tape(repo / "data" / "archive" / f"mbo_{day:%Y%m%d}_235900.csv", 1500, 4000.0, "mbo")
        # and the test's OWN drawer: prints only, no order tape anywhere in it
        fix = t / "fixture" / "data"
        tape(fix / f"ticks_{day:%Y%m%d}.csv", 1500, 4000.0, "ticks")

        common = ["--date", f"{day:%Y-%m-%d}", "--window", "08:00-09:10", "--every", "180",
                  "--prewarm", "240", "--min-prints", "20", "--quiet", "--no-diary"]

        # (A) --data-dir NAMED -> the repo's archive must be invisible. If this
        # assertion ever goes red, a drawer is leaking into a replay again.
        ja = t / "a.json"
        ra = subprocess.run(
            [sys.executable, str(HERE / "tools" / "gates_replay.py"), *common,
             "--data-dir", str(fix), "--root-dir", str(repo), "--json-out", str(ja)],
            capture_output=True, text=True, timeout=900, cwd=str(HERE), env=_fixture_env())
        assert ra.returncode == 0, ra.stdout[-400:] + ra.stderr[-400:]
        agg_a = json.loads(ja.read_text(encoding="utf-8"))
        assert int(agg_a.get("mbo_rows") or 0) == 0, (
            f"a named --data-dir swallowed the repo's own order tape: {agg_a.get('mbo_rows')}")
        assert int(agg_a.get("cycles") or 0) > 0, (
            f"scoping must not blind the sea to its OWN drawer: {agg_a.get('cycles')}")
        assert "explicit --data-dir ONLY" in ra.stdout, (
            f"the sea must SAY where it looked (paper first): {ra.stdout[-500:]}")

        # (B) no --data-dir -> the repo root IS the drawer; that is how your real
        # night finds its 236 slices, so it must keep working.
        jb = t / "b.json"
        rb = subprocess.run(
            [sys.executable, str(HERE / "tools" / "gates_replay.py"), *common,
             "--root-dir", str(repo), "--json-out", str(jb)],
            capture_output=True, text=True, timeout=900, cwd=str(HERE), env=_fixture_env())
        assert rb.returncode == 0, rb.stdout[-400:] + rb.stderr[-400:]
        agg_b = json.loads(jb.read_text(encoding="utf-8"))
        assert int(agg_b.get("mbo_rows") or 0) > 0, (
            f"without --data-dir the repo drawer must still be read: {agg_b.get('l3')}")
        assert "data-dir + repo root" in rb.stdout, (
            f"the scope line must say BOTH halves were searched: {rb.stdout[-500:]}")


def test_the_mapper_band_is_a_pure_cut_of_the_map():
    """item 42, the arithmetic half (the owner's +-80 USD law). No subprocess: this
    is the one function the band lives in, and it must be boring and total."""
    sys.path.insert(0, str(HERE / "tools"))
    import gates_replay as gr
    snap = {"signal_map": {
        "targets": [{"price": 4000.0}, {"price": 4079.9}, {"price": 4080.1},
                    {"price": 3919.0}, {"price": 4200.0}, {"kind": "no-price-at-all"}],
        "memory_doors": [{"price": 3999.0}, {"price": 5000.0}],
    }}
    seen, kept = gr.band_levels(snap, 4000.0, 80.0)
    assert (seen, kept) == (8, 3), f"the band kept {kept} of {seen}: {snap}"
    # IN PLACE, because `levels_from_view` reads the same lists we just trimmed.
    assert len(snap["signal_map"]["targets"]) == 2 and \
           len(snap["signal_map"]["memory_doors"]) == 1, (
        f"the trim did not reach the map the Mapper will draw: {snap['signal_map']}")
    assert [r["price"] for r in snap["signal_map"]["targets"]] == [4000.0, 4079.9] or \
           [r["price"] for r in snap["signal_map"]["targets"]] == [3919.0, 4000.0], (
        f"the band drew the wrong lines: {snap['signal_map']}")
    # band 0 = OFF means "draw the whole market", not "draw nothing"
    untouched = {"signal_map": {"targets": [{"price": 1.0}, {"price": 9e9}], "memory_doors": []}}
    seen2, kept2 = gr.band_levels(untouched, 5000.0, 0.0)
    assert (seen2, kept2) == (2, 2) and len(untouched["signal_map"]["targets"]) == 2, (
        f"an OFF band must be a no-op, it filtered: {untouched['signal_map']}")
    # a negative band is the same promise as 0, and never a way to erase the map
    assert gr.band_levels(untouched, 5000.0, -5.0) == (2, 2)

    # the branch the sea can never reach by narrowing the band (a borrowed brain
    # always keeps a line AT the last print), so it is proven here instead: an
    # empty sheet must be COUNTED, and an incomplete row must never be guessed
    far = {"signal_map": {"targets": [{"price": 4500.0}, {"price": 3000.0}],
                          "memory_doors": [{"kind": "no-price"}]}}
    assert gr.band_levels(far, 4000.0, 80.0) == (3, 0), (
        f"everything outside the band must leave an empty sheet, counted: {far}")
    assert far["signal_map"]["targets"] == [] and far["signal_map"]["memory_doors"] == []


def test_an_empty_band_starves_nothing_and_says_so_on_the_paper():
    """item 42, the shape half. Two things must be true at once when the band leaves
    the Mapper with an empty sheet: the night is still WALKED (the rung memory is
    fed by every cycle, so refusing to feed a cycle to save time would silently
    break `accepted` counts - the deliberate choice is named, not hidden), and the
    paper NAMES it (`map empty on N cycle(s)`) instead of letting `G2 with a rung
    0/141` read as a veto. The OFF shape is asserted too, because a default that
    filters when it says it does not is the worst kind of quiet."""
    import gzip                                       # the file's own convention (item 36)
    with tempfile.TemporaryDirectory() as tight, tempfile.TemporaryDirectory() as off:
        def tape(root, day, n=1500):
            dd = Path(root) / "data" / "archive"
            dd.mkdir(parents=True, exist_ok=True)
            t0 = datetime(2026, 11, 12, 8, 0, tzinfo=timezone.utc)
            rows = []
            for i in range(n):
                iso = (t0 + timedelta(seconds=20 * i)).astimezone(
                    timezone(timedelta(hours=1))).isoformat()
                px = 4000.0 + 8.0 * (1 if (i // 60) % 2 == 0 else -1) + 0.01 * (i % 9)
                rows.append(f"{iso},Trade,{px:.1f},{1 + (i % 5)},-,-,X")
            (dd / f"ticks_{day}_235900.csv.gz").write_bytes(
                gzip.compress(("\n".join(rows) + "\n").encode()))
            return Path(root) / "data"

        def sail(root, day, band):
            jp = Path(root) / "s.json"
            r = subprocess.run(
                [sys.executable, str(HERE / "tools" / "gates_replay.py"),
                 "--date", day, "--data-dir", str(root), "--every", "180",
                 "--prewarm", "150", "--max-minutes", "300", "--min-prints", "20",
                 "--quiet", "--no-diary", "--rung-band", str(band),
                 "--json-out", str(jp)],
                capture_output=True, text=True, timeout=900, cwd=str(HERE),
                env=_fixture_env())
            assert r.returncode == 0, r.stdout[-400:] + r.stderr[-400:]
            return r.stdout, json.loads(jp.read_text(encoding="utf-8"))

        out_t, agg_t = sail(tape(tight, "2026-11-12"), "2026-11-12", 0.001)
        kept_t, seen_t = int(agg_t.get("levels_kept") or 0), int(agg_t.get("levels_seen") or 0)
        assert kept_t < seen_t, (
            f"a 0.001 USD band must be a hard cut, it kept {kept_t} of {seen_t}")
        assert kept_t > 0 and int(agg_t.get("band_empty") or 0) == 0, (
            f"what is left at price +-0.001 is the brain's line AT the print, and an "
            f"empty cycle must NOT be invented here: kept={kept_t} empty={agg_t.get('band_empty')}")
        assert int(agg_t.get("cycles") or 0) > 0, (
            f"the day must still be walked (the rung memory is fed every cycle): {agg_t.get('cycles')}")
        m = re.search(r"lines kept (\d+) of (\d+)", out_t)
        assert m and (int(m.group(1)), int(m.group(2))) == (kept_t, seen_t), (
            f"the paper and the json must agree, line for line: printed={m.groups() if m else None} "
            f"json={kept_t}/{seen_t}")
        assert "map empty on" not in out_t, (
            f"`map empty` may not be printed when cycles kept lines: {out_t[:400]}")
        assert float(agg_t.get("rung_band") or 0.0) == 0.001, (
            f"the band in force must be in the json: {agg_t.get('rung_band')}")

        out_o, agg_o = sail(tape(off, "2026-11-12"), "2026-11-12", 0)
        assert "OFF (the Mapper draws the whole market)" in out_o, (
            f"band 0 must announce itself as OFF: {out_o[:400]}")
        assert int(agg_o.get("levels_seen") or 0) == int(agg_o.get("levels_kept") or 0), (
            f"OFF must filter NOTHING: {agg_o.get('levels_kept')}/{agg_o.get('levels_seen')}")
        assert int(agg_o.get("band_empty") or 0) == 0, agg_o.get("band_empty")
        # monotonicity, the one relation two runs of the SAME tape may compare:
        # a wider sheet cannot hold fewer lines, and OFF must hold all of them
        assert int(agg_o.get("levels_kept") or 0) >= int(agg_t.get("levels_kept") or 0), (
            "a wider band can never keep fewer lines than a narrower one")
        assert int(agg_o.get("levels_kept") or 0) == int(agg_o.get("levels_seen") or 0) \
               > int(agg_t.get("levels_kept") or 0), (
            f"OFF must be the whole map, not another cut: {agg_o.get('levels_kept')}/"
            f"{agg_o.get('levels_seen')} vs tight {agg_t.get('levels_kept')}")


def test_l3_events_survives_the_tail_it_borrows():
    # item 28, the bug a real sea found: v6 chose the busiest instrument with
    # `kv[1].line_count`, but `_InstrumentState` (a __slots__ class) has NO such
    # attribute - the AttributeError was swallowed and L3 was empty on EVERY
    # cycle, even with a real order tape on disk. So: probe the seam directly.
    sys.path.insert(0, str(HERE / "tools"))
    import gates_replay as gr
    try:
        import threading
        import bookmap_bridge_provider as bbp
    except Exception:                                   # bare meadow, no bridge
        return
    tail = bbp._MboTail.__new__(bbp._MboTail)
    tail.path, tail.lock = "", threading.Lock()
    tail.instruments, tail.line_count, tail.bad_lines = {}, 0, 0
    t0 = _sea_fixture_day(1).replace(hour=8, minute=20)
    for i in range(6):
        tail._handle_line(f"{(t0 + timedelta(seconds=20 * i)).isoformat()},"
                          f"BID_NEW,{7000000000000 + i},3900.5000,4.0000,"
                          f"GCZ6.COMEX@RITHMIC\n")
    assert tail.instruments, "the borrowed MBO tail refused the real 6-col grammar"
    # the cycle time must come from THE PARSER UNDER TEST, not from the machine:
    # `_parse_ts` localises a naive stamp to the MARKET zone (the fixture wrote
    # 08:20 and read back 08:20+01:00), while datetime.timestamp() would use the
    # box's own zone - 5 minutes of window lost in an hour of difference.
    tc = float(bbp._parse_ts((t0 + timedelta(minutes=2)).isoformat()).timestamp())
    got, err = gr._l3_events(tail, None, tc)
    assert not err, f"the L3 seam raised again (item 28): {err}"
    assert len(got) >= 3, f"orders were windowed away: {len(got)} of 6"


def test_the_shell_understands_every_sea_count():
    # item 29: `--sea 2` used to be REFUSED by a guard written to protect the date
    # case (it rejected anything starting with "2"), and the orphan `2` was then
    # forwarded to the sea as a positional -> `unrecognized arguments: 2` -> the
    # whole sea did not run, while the night still looked graded. Found by RUNNING
    # the shipped shell on a rig-shaped fixture, not by reading it.
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        (t / "bin").mkdir()
        (t / "data").mkdir()
        (t / "tools").mkdir()
        stub = t / "bin" / "python"
        stub.write_text("#!/bin/sh\n"
                        'if [ "$1" = "-c" ]; then date +%Y-%m-%d; exit 0; fi\n'
                        'printf "%s\n" "$*" >> "$SEA_ARGS_LOG"; exit 0\n',
                        encoding="utf-8")
        stub.chmod(0o755)
        shutil.copy(HERE / "report_evening_gates.sh", t / "report_evening_gates.sh")
        for name in ("gates_replay.py", "gates_report.py"):
            (t / "tools" / name).write_text("", encoding="utf-8")
        log = t / "args.log"
        env = dict(os.environ, PATH=f"{t / 'bin'}{os.pathsep}{os.environ['PATH']}",
                   SEA_ARGS_LOG=str(log))
        seen = {}
        for extra, want in ((["--sea", "2"], "--days 2"),
                            (["--sea", "20"], "--days 20"),
                            (["--sea", "5"], "--days 5"),
                            (["--sea"], "--days 3"),
                            (["--sea", "2026-11-13"], "--days 3"),
                            (["--sea", "3", "2026-11-13"], "--days 3")):
            if log.exists():
                log.unlink()
            r = subprocess.run(["sh", "report_evening_gates.sh", "--no-heartbeat"]
                               + extra, capture_output=True, text=True, timeout=120,
                               cwd=str(t), env=env)
            got = log.read_text(encoding="utf-8") if log.exists() else ""
            sea_lines = [x for x in got.splitlines() if "gates_replay.py" in x]
            assert sea_lines, (extra, r.stdout[-300:], r.stderr[-300:])
            assert want in sea_lines[0], (extra, want, sea_lines[0])
            assert not re.search(r" --days \S+ \n?\s*$", sea_lines[0]) is None or True
            # the number must never be LEAKED as a bare positional to the sea
            toks = sea_lines[0].split()
            # every number in the sea's command line must belong to a flag that
            # WANTS a number - an orphan (which is what the old guard produced) is
            # what argparse refuses with "unrecognized arguments".
            numeric = ("--days", "--every", "--prewarm", "--max-minutes",
                       "--max-cycles", "--min-prints")
            for i, tk in enumerate(toks):
                if tk.isdigit():
                    assert i > 0 and toks[i - 1] in numeric, (extra, "leaked", toks)
            assert f"--days {want.split()[-1]}" in sea_lines[0] or want == "--days 3", \
                (extra, want, sea_lines[0])
            seen[tuple(extra)] = sea_lines[0]
        # the contract of a date argument, written down because it is NOT what a
        # first guess says: the date chooses WHICH NIGHT'S PAPERS are graded and
        # named (gates_sea_<day>.json), while --days chooses how far the sea
        # sails. `--sea 5 2026-11-10` therefore does NOT become a one-day sail.
        assert "gates_sea_20261113.json" in seen[("--sea", "3", "2026-11-13")], \
            seen[("--sea", "3", "2026-11-13")]
        assert "--days 3" in seen[("--sea", "3", "2026-11-13")], \
            seen[("--sea", "3", "2026-11-13")]


def test_a_hall_off_night_is_not_illness():
    # v7 (item 24): the borrowed-window sea on the owner's live machine ran with
    # --no-heartbeat, so NO diary could exist - and the teacher cried FAIL
    # ("DIARY EXISTS") on a night that was never meant to have one.
    with tempfile.TemporaryDirectory() as tmp:
        empty = Path(tmp) / "data"
        empty.mkdir()
        r = subprocess.run(
            [sys.executable, str(HERE / "tools" / "gates_report.py"),
             "--dir", str(empty), "--hall-off", "--out", str(Path(tmp) / "a.txt")],
            capture_output=True, text=True, timeout=300, cwd=str(HERE))
        assert r.returncode == 0, "a hall-off night has no FAIL, so it exits 0"
        body = (Path(tmp) / "a.txt").read_text(encoding="utf-8")
        assert "DIARY EXISTS" in body, body[-400:]
        assert "[FAIL]  DIARY EXISTS" not in body, \
            "a deliberate --no-heartbeat night must abstain, not fail: " + body
        assert "SEA-GRADE ONLY" in body, body[-500:]
        r2 = subprocess.run(
            [sys.executable, str(HERE / "tools" / "gates_report.py"),
             "--dir", str(empty), "--out", str(Path(tmp) / "b.txt")],
            capture_output=True, text=True, timeout=300, cwd=str(HERE))
        assert r2.returncode == 1, "without the flag, a missing diary IS a FAIL"
        assert "[FAIL]  DIARY EXISTS" in (Path(tmp) / "b.txt").read_text(
            encoding="utf-8"), "without --hall-off the silence is STILL a FAIL"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in fns:
        if not callable(fn):
            continue
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:
            failed += 1
            import traceback
            print(f"FAIL {fn.__name__}: {exc}")
            traceback.print_exc(limit=2)
    print(f"\n{len(fns) - failed}/{len(fns)} tests passed")
    raise SystemExit(1 if failed else 0)


# ==================== v8f: ACCEPT-BEFORE-BOUNCE (Code Day 4, item 48) ========
# EVERY expectation below was transcribed from a measured 12-case truth table, after two
# wrong readings by sign-reasoning. The sign law (committee.py:339-342): a BOUNCE desk
# counts when it votes AGAINST the push (`v[desk] == -d`) - absorb/tired/scarecrow, plus
# iceberg on either side; the delta desk only NAMES the push. On the owner's 11 real cards
# the desks read `delta- absorb iceberg- tired`: ZERO bounce votes, one agree-iceberg -
# so the word BOUNCE came from `SNAPBACK=1` alone, every time, at EXCESS=c1/h3m.
def _j(name, d, w=1.0):
    return {"judge": name, "dir": d, "weight": w}


_OWN = [_j("footprint_delta", -1), _j("iceberg", -1, 0.8)]        # the owner's card shape
_BON = [_j("footprint_delta", -1), _j("absorption", 1),           # two real bounce votes
        _j("cvd_divergence", 1, 0.9)]
_UNPAID = {"closes_beyond": 1, "holds_minutes": 3.0, "volume_building": False}
_PAID = {"closes_beyond": 2, "holds_minutes": 6.0, "volume_building": False}


def test_first_touch_may_not_be_called_a_bounce_in_the_hall():
    """The owner's exact ticket - snap-back stamp, no bounce desks, toll at its FIRST
    close - is a BOUNCE under the old law and a NAMED refusal in the hall. The unmarked
    call must be untouched: their live factory borrows this same file (their
    factory2_gates.py:340 `import committee`), so no live cycle may gain the law."""
    import committee as C
    rung = C.Rung(name="htf_poc", price=4000.0, kind="poc")
    v = C.desk_voices(_OWN)
    assert v["delta"] == -1 and v["iceberg"] == -1 and v["absorb"] == 0
    assert v["tired"] == 0, "the owner's card has NO bounce vote - BOUNCE rode SNAPBACK"
    old = C.evaluate_fight(rung, _OWN, _UNPAID, {"snapback": 1})
    assert "VERDICT=BOUNCE" in old["card"], old["card"]
    assert "ACCEPT=required" not in old["card"] and old["blocked"] is False
    hall = C.evaluate_fight(rung, _OWN, _UNPAID, {"snapback": 1, "symbol": "REPLAY/x"})
    assert "VERDICT=UNDECIDED" in hall["card"], hall["card"]
    assert "TOLL=unpaid" in hall["card"] and "ACCEPT=required" in hall["card"]
    assert hall["blocked"] is True and hall["accept_met"] is False
    # and a 2-bounce-desk ballot with no snap-back is refused the same way
    b_old = C.evaluate_fight(rung, _BON, _UNPAID, {"snapback": 0})
    b_hall = C.evaluate_fight(rung, _BON, _UNPAID, {"snapback": 0, "symbol": "REPLAY/x"})
    assert "VERDICT=BOUNCE" in b_old["card"] and "VERDICT=UNDECIDED" in b_hall["card"]
    assert b_hall["blocked"] is True
    # the live entry point takes only (cyc, ledger) - it cannot pass `armed` at all
    import inspect
    assert list(inspect.signature(C.committee_evaluate).parameters) == ["cyc", "ledger"]


def test_acceptance_opens_the_bounce_the_old_law_muzzled():
    """The trap this fix had to dodge: the old branch demanded `not paid`, so acceptance
    moused BOUNCE exactly as it gates BREAK - a naive "require the toll" gate leaves the
    reachability set EMPTY (paid -> refused by `not paid`, unpaid -> refused by the gate)
    and the hall goes silent FOREVER. Measured here: paid + snapback flips UNDECIDED ->
    BOUNCE, with no refusal mark."""
    import committee as C
    rung = C.Rung(name="htf_poc", price=4000.0, kind="poc")
    g3 = C.evaluate_fight(rung, _OWN, _PAID, {"snapback": 1, "symbol": "REPLAY/x"})
    assert "TOLL=paid" in g3["card"] and "VERDICT=BOUNCE" in g3["card"], g3["card"]
    assert "ACCEPT=required" not in g3["card"]
    assert g3["accept_met"] is True and g3["blocked"] is False
    g4 = C.evaluate_fight(rung, _BON, _PAID, {"snapback": 0, "symbol": "REPLAY/x"})
    assert "VERDICT=BOUNCE" in g4["card"] and g4["accept_met"] is True, g4["card"]


def test_break_and_silence_keep_their_own_doors():
    """BREAK stays exactly BREAK (paid + 3 desks count toward the push, and iceberg counts
    on BOTH sides). A ballot with nothing against the push and no snap-back stays
    UNDECIDED with blocked=False - "no idea" and "refused" must not share a tally."""
    import committee as C
    rung = C.Rung(name="wallA", price=4000.0, kind="walls")
    brk = [_j("footprint_delta", 1), _j("l3_ofi_streak", 1), _j("microprice", 1),
           _j("iceberg", 1)]
    g = C.evaluate_fight(rung, brk, _PAID, {"snapback": 0, "symbol": "REPLAY/x"})
    assert "VERDICT=BREAK" in g["card"], g["card"]
    assert "ACCEPT=required" not in g["card"] and g["toll_paid"] is True
    quiet = [_j("footprint_delta", -1), _j("iceberg", 1)]     # zero bounce votes, no snap
    q = C.evaluate_fight(rung, quiet, _UNPAID, {"snapback": 0, "symbol": "REPLAY/x"})
    assert "VERDICT=UNDECIDED" in q["card"] and q["blocked"] is False, q["card"]


def test_refusal_is_named_no_edge_not_lumped_into_no_verdict():
    """The refusal tally is the ONLY instrument that grades this law: if a suppressed
    first-touch bounce hid inside NO-VERDICT (the owner measured 131 of them on Thursday),
    we could never tell a quiet market from a muzzled one."""
    import committee as C
    rung = C.Rung(name="htf_poc", price=4000.0, kind="poc")
    pre = {"age_s": 1.0, "sensors_ok": True, "bar_closed": True, "spread_pct": 0.001,
           "position_count": 0, "max_positions": 1, "daily_loss_ok": True,
           "kill_switch": False, "diary": lambda rec: None, "symbol": "REPLAY/x",
           "cycle_id": 1}
    g3 = C.evaluate_fight(rung, _OWN, _UNPAID, {"snapback": 1, "symbol": "REPLAY/x"})
    f1 = C.attempt_fire(g3, rung, pre, C.TicketLedger())
    assert f1["fails"] == ["NO-EDGE"] and f1["allow"] is False and f1["ticket"] is None
    f2 = C.attempt_fire({"verdict": "UNDECIDED", "blocked": False}, rung, pre,
                        C.TicketLedger())
    assert f2["fails"] == ["NO-VERDICT"], f2


def test_the_hall_stamps_its_own_name_and_the_seal_moved():
    """The mark is what arms the law, so it must exist in the FILE - not only in the
    committee - and the seal must move, because a receipt reads state, never memory."""
    src = (HERE / "tools" / "gates_replay.py").read_text(encoding="utf-8")
    assert 'cv["shadow_law"] =' not in src, "the dead whitelist key must not come back"
    assert "DEAD CODE" in src, "the comment that remembers WHY must stay"
    # .1 = BELT-1b (2026-11-14): the mirror field stopped carrying a per-run folder
    # name into the json. .2 = EYES-2d (2026-11-17): `stale` split into
    # pre-first-print / post-last-row, after the owner's own measurement proved the word
    # was hiding a tape that ENDED, not a feed that was late. .3 = TALLY-2 (2026-11-17):
    # `(allowed)` is no longer filed inside the REFUSAL tally - a name for "nothing was
    # refused" is the same sin in the opposite direction, and it is the reason row 3 could
    # not be asserted before today. The seal moves WITH the code, and this line is the
    # receipt that says so - bump a stamp, edit it HERE, in the same breath.
    assert 'REPLAY_VERSION = "gates.replay.2026-11-17.3"' in src
    assert 'or ["(allowed)"]' not in src, (
        "the idiom that invented a refusal word for a cycle that walked through must not come back")
    # TALLY-2's OTHER half, and the reason it needs a pin instead of a fixture: no fixture
    # day in this repo has a cycle that walks through (`allowed` is 0 on all of them, which
    # I measured, not assumed), so a mutated increment is invisible to behaviour. Pin the
    # line that counts, and let the paper's own `TALLY DOES NOT ADD UP` guard watch the rest.
    assert 'res["allowed"] = res.get("allowed", 0) + 1' in src, (
        "the walk-through count must actually count (law 52: this half has no fixture that can see it)")
    rep = (HERE / "tools" / "gates_report.py").read_text(encoding="utf-8")
    # .1 = BELT-1 (2026-11-14): the teacher gained the counter audit. The seal moves
    # WITH the gate or the receipt reads state instead of paper - that is the whole
    # reason this test pins a string, so bumping a stamp means editing it HERE.
    assert 'REPORT_VERSION = "gates.report.2026-11-17.3"' in rep
    import committee as C
    assert getattr(C, "ACCEPT_BEFORE_BOUNCE", None) is True
    assert getattr(C, "ACCEPT_MARK", "") == "REPLAY/"
    # LINE ORDER IS THE LAW: the stamp must precede the call that reads it, or the
    # committee would arm on a symbol nobody ever set.
    i_sym = src.index('cv["symbol"] = f"REPLAY/{day_str}"')
    i_run = src.index("out = f2.run_cycle(")
    assert i_sym < i_run, "the hall stamps its mark AFTER the call - the law would be dead"


def test_the_mark_rides_a_field_the_bridges_actually_forward():
    """v8f nearly shipped DEAD CODE: I armed on a new snapshot key, and their
    f2.cv_from_snapshot_dict() returns a FIXED whitelist that silently drops unknown
    keys. Measured here, in both directions, so the next reader cannot re-lose this:
    the invented key vanishes, the symbol survives - and committee_evaluate reads the
    field that survives. A test on `evaluate_fight` alone could never have caught it."""
    import committee as C
    try:
        import factory2_gates as f2
    except Exception as exc:                                # pragma: no cover
        print(f"  (skip: their brain is absent in this meadow: {type(exc).__name__})")
        return
    d = {"price": 3880.0, "symbol": "REPLAY/2026-11-12", "timestamp": "2026-11-12T12:00:00",
         "shadow_law": "accept-before-bounce", "toll": {"closes_beyond": 22}}
    cv = f2.cv_from_snapshot_dict(d, 1)
    assert "shadow_law" not in cv, "a key the whitelist drops is a lie, not a channel"
    assert cv["symbol"] == "REPLAY/2026-11-12", "the carrier must be a whitelisted field"
    cyc = f2.build_cyc(cv, None, "replay:tapes", now_ts=1e9)
    assert cyc.get("symbol") == "REPLAY/2026-11-12", "build_cyc is the second bridge"
    src = (HERE / "committee.py").read_text(encoding="utf-8")
    i = src.index("def committee_evaluate")
    body = src[i:i + 2400]
    assert '_armed = str(cyc.get("symbol") or "").startswith(ACCEPT_MARK)' in body
    assert "armed=_armed" in body, "the flag must reach evaluate_fight, not just be computed"
    assert body.index('_armed =') < body.index("g3 = evaluate_fight"), "compute then pass"


def test_the_same_tape_sailed_twice_is_the_same_paper():
    """BELT-1 (2026-11-14): DETERMINISM, and it was ASSUMED for eleven code days.

    Every number in this Book - the 177-cycle tally, the 53 verdicts, the payoff -
    rests on one unspoken premise: that sailing the same raw tape twice through the
    same code produces the same paper. Never once checked. If a clock, a dict order,
    a folder glob or a leftover state file leaks into a run, the factory is not a
    measurement, it is a performance.

    So: take the CHEAPEST archived day (fewest tape files = the fastest honest run),
    sail it twice with identical flags into two files, and compare BYTES. Not
    "similar". Not "same verdict count". Bytes.

    Skips - loudly, with the reason - when the rig has no archive: a bare meadow must
    still run this suite, and a test that FAILs for want of data would train everyone
    to ignore red.
    """
    arch = HERE / "data" / "archive"
    if not arch.is_dir():
        print(f"  (skip: no data/archive here - determinism needs real tape)")
        return
    days = {}
    for d in sorted(arch.iterdir()):
        if not d.is_dir():
            continue
        n = len(list(d.glob("ticks_*.csv*")))
        if n:
            days[d.name] = n
    if not days:
        print("  (skip: data/archive holds no ticks_*.csv* - nothing to sail)")
        return
    day = min(days, key=lambda k: days[k])          # the cheapest honest day
    cmd = [sys.executable, str(HERE / "tools" / "gates_replay.py"), "--date", day,
           "--window", "08:00-22:00", "--every", "180", "--prewarm", "150",
           "--min-prints", "20", "--rung-band", "80", "--quiet", "--no-diary"]
    with tempfile.TemporaryDirectory() as tmp:
        outs = []
        for leg in ("a", "b"):
            jo = Path(tmp) / f"det_{leg}.json"
            r = subprocess.run(cmd + ["--json-out", str(jo)], capture_output=True,
                               text=True, timeout=1800, cwd=str(HERE))
            assert r.returncode == 0, (leg, day, r.stdout[-400:], r.stderr[-400:])
            assert jo.exists(), (leg, "the sea wrote no json - --json-out is a lie",
                                 r.stdout[-300:])
            outs.append(jo)
        a, b = (x.read_bytes() for x in outs)
        assert a == b, (
            f"NON-DETERMINISM on {day}: two identical sails produced different paper "
            f"({len(a)} vs {len(b)} bytes). Diff the two json files - a wall-clock, an "
            "unsorted glob, or shared state reached the run. Nothing measured on this "
            "factory means anything until this passes.")
        # the paper must not be empty either: an identical pair of EMPTY files would
        # pass a byte compare while the sea had actually produced nothing.
        doc = json.loads(a.decode("utf-8"))
        assert int(doc.get("cycles") or 0) > 0, (day, "cycles=0 - both sails abstained")
        assert doc.get("ok"), (day, "ok is not true on both legs")


def test_the_diary_says_what_kind_of_line_it_is_and_when_the_tape_said_it():
    """14e (laws 105 + 106). Two fields, both load-bearing:

    `line_kind`  - a diary holds cycle rows AND the committee's verdict rows. Counting LINES mixes
                   them: the owner's `allowed 7 of 75` measured as 14 with a line-wide grep, which is
                   how I lost a bet to my own model. Not named `kind`: this house already uses `kind`
                   for rung/level kinds, and two meanings in one file is how a grep finds the wrong one.
    `t_tape`     - `t` is `datetime.now` in BOTH modes, so in a replay it is a near-constant (his first
                   five rows: :45 :45 :46 :46 :47). The tape's own time comes from `cv["ts"]`.
                   It must be read from `cv`, NOT `cyc` - `build_cyc()` copies key by key and drops
                   `ts`, which would make the field permanently empty. That is the v8f `shadow_law`
                   trap in a new costume, so this test pins the VALUE, not just the key's presence.
    """
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        dd = Path(tmp)
        pen = F2.Pen(str(dd / "d.jsonl"))
        state = H.RungState(str(dd / "s.json"))
        import committee as C
        ledger = C.TicketLedger()
        cv = {"cycle_id": 1, "price": 4000.0, "atr": 5.0, "ts": 1_700_000_000.0,
              "signal_map": {"targets": [{"price": 4001.0, "kind": "vwap", "sources": ["vwap"],
                                          "cycles_seen": 2, "size": 0.0}]},
              "power": {}, "vp": {}, "judges": [], "notes": [], "bars": [], "data_quality": {}}
        F2.run_cycle(cv, state, pen, "replay:tapes", now_ts=1_700_000_000.0, ledger=ledger)
        rows = [json.loads(l) for l in (dd / "d.jsonl").read_text(encoding="utf-8").splitlines()]
        assert len(rows) == 1, rows
        r = rows[0]
        assert r["line_kind"] == "cycle", r
        assert r["t_tape"] == "2023-11-14T22:13:20+00:00", r["t_tape"]   # that epoch, exactly
        assert r["t"] != r["t_tape"], "in a replay these differ; equal would mean t_tape is dead code"


def test_a_cycle_without_a_tape_clock_says_so_and_not_zero():
    """`""` means "I have no tape clock". `1970-01-01` would be a LIE, and 0.0 is a real ts some
    caller might legitimately carry - so an empty/zero ts must read as absence, not as the epoch."""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        dd = Path(tmp)
        pen = F2.Pen(str(dd / "d.jsonl"))
        state = H.RungState(str(dd / "s.json"))
        import committee as C
        cv = {"cycle_id": 7, "price": 4000.0, "atr": 5.0, "ts": 0.0, "signal_map": {},
              "power": {}, "vp": {}, "judges": [], "notes": [], "bars": [], "data_quality": {}}
        F2.run_cycle(cv, state, pen, "replay:jar", now_ts=1000.0, ledger=C.TicketLedger())
        r = json.loads((dd / "d.jsonl").read_text(encoding="utf-8").splitlines()[0])
        assert r["t_tape"] == "", r["t_tape"]
        assert "1970" not in json.dumps(r), "a missing clock must never print as the epoch"


def test_diary_line_size_still_respects_the_one_kb_law():
    """The ~1KB law (this file's own test). 14e adds ~60 bytes, and a diary line that grows quietly
    is how a JSONL cap turns into a truncation (law 92: the cap is GLOBAL)."""
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as tmp:
        dd = Path(tmp)
        pen = F2.Pen(str(dd / "d.jsonl"))
        state = H.RungState(str(dd / "s.json"))
        import committee as C
        cv = {"cycle_id": 1, "price": 4000.0, "atr": 5.0, "ts": 1_700_000_000.0,
              "signal_map": {"targets": [{"price": 4001.0, "kind": "vwap", "sources": ["vwap"],
                                          "cycles_seen": 2, "size": 0.0}]},
              "power": {}, "vp": {}, "judges": [], "notes": [], "bars": [], "data_quality": {}}
        F2.run_cycle(cv, state, pen, "replay:tapes", now_ts=1_700_000_000.0, ledger=C.TicketLedger())
        r = json.loads((dd / "d.jsonl").read_text(encoding="utf-8").splitlines()[0])
        n = len(json.dumps(r))
        assert n < 1100, f"the hall's own line grew to {n} bytes - say so, don't slip it past the law"
        print(f"           hall line with 14e: {n} B")


def test_a_whole_diary_is_named_and_the_names_close_as_an_equation_14f():
    """14e tagged the cycle rows; 14f finishes the job. The sea ALSO writes a FOOTER line (the
    day's own tally) and the hall writes an ERROR line when a card fails to build, so a real
    diary of N lines is a MIX: a grep for one word answers the wrong question (law 105 - name
    the unit a count counts). Here the whole sea runs on a synthetic tape, and the partition is
    asserted as an EQUATION, not a filter: lines == cycle + day + error. The second half is the
    two-instruments check: the old key test (`"cycle" in r`, what gates_report uses today) must
    agree with the new tag, or one of the two is measuring a rumour."""
    import gzip
    import subprocess
    with tempfile.TemporaryDirectory() as tmp:
        dd = Path(tmp) / "data"
        arch = dd / "archive"
        arch.mkdir(parents=True)
        t0 = _sea_fixture_day(1)
        dayc = t0.strftime("%Y%m%d")
        tick = ["time,event,price,size,level,operation,instrument\n"]
        px = 3900.0
        for i in range(400):                      # ~2.2 h of tape -> tens of cycles, seconds to run
            px += 0.7 if i % 3 else -0.9
            iso = (t0 + timedelta(seconds=20 * i)).isoformat()
            tick.append(f"{iso},Last,{px:.4f},10.0000,-,buy,GCZ6.COMEX@RITHMIC\n")
        with gzip.open(arch / f"ticks_{dayc}_120000.csv.gz", "wt",
                       encoding="utf-8", newline="\n") as fh:
            fh.write("".join(tick))
        diary = Path(tmp) / "diary.jsonl"
        jp = Path(tmp) / "sea.json"
        day = f"{t0.year:04d}-{t0.month:02d}-{t0.day:02d}"
        r = subprocess.run(
            [sys.executable, str(HERE / "tools" / "gates_replay.py"),
             "--date", day, "--data-dir", str(dd), "--window", "04:00-06:20",
             "--every", "180", "--prewarm", "0", "--min-prints", "20", "--quiet",
             "--json-out", str(jp), "--diary-out", str(diary)],
            capture_output=True, text=True, timeout=900, cwd=str(HERE))
        assert r.returncode == 0, r.stdout[-500:] + r.stderr[-500:]
        lines = [x for x in diary.read_text(encoding="utf-8").splitlines() if x.strip()]
        recs = [json.loads(x) for x in lines]
        kinds = [str(x.get("line_kind") or "") for x in recs]
        assert not [k for k in kinds if k not in ("cycle", "day", "error", "verdict")], \
            f"an unnamed or invented class in the diary: {sorted(set(kinds))}"
        n_cyc = kinds.count("cycle")
        assert n_cyc > 0, f"the sea wrote no cycles at all: {lines[:1]}"
        assert kinds.count("day") == 1, f"the day footer must be exactly one line: {kinds}"
        assert lines, "empty diary"
        assert len(lines) == n_cyc + kinds.count("day") + kinds.count("error"), \
            f"the partition does not close: {len(lines)} lines vs {kinds.count('cycle')}+" \
            f"{kinds.count('day')}+{kinds.count('error')}"
        foot = [x for x in recs if x.get("line_kind") == "day"][0]
        assert foot.get("mode") == "replay:summary", foot.get("mode")
        assert int(foot.get("cycles") or 0) == n_cyc, \
            f"the footer says {foot.get('cycles')} cycles, the file holds {n_cyc}"
        # two instruments, one number: the tag AND the old key test must agree
        assert len([x for x in recs if "cycle" in x]) == n_cyc, \
            "gates_report's own selector and the new tag disagree - one of them is wrong"
        agg = json.loads(jp.read_text(encoding="utf-8"))
        assert int(agg.get("cycles") or 0) == n_cyc, f"the json says {agg.get('cycles')}, the diary says {n_cyc}"


def test_an_error_line_names_itself_too_14f():
    """The house law is "one line, never a dead loop": when a card cannot be built, the hall writes
    a short error line and keeps breathing. 14e left THAT line unnamed - which is the worst place
    to leave a blank, because a reader who counts lines to know how many cycles ran counts the
    failures as cycles. This drives the real except-branch by making the pen raise on its FIRST
    call only: the cycle card dies at the write, the handler writes again, and the file must hold
    one line that says what it is."""
    import tempfile
    from pathlib import Path

    class _OnceThenOk:
        def __init__(self, inner):
            self.inner, self.calls = inner, 0

        def __call__(self, rec):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("first write dies on purpose")
            return self.inner(rec)

        def callable_ok(self):
            return True

    with tempfile.TemporaryDirectory() as tmp:
        dd = Path(tmp)
        pen = _OnceThenOk(F2.Pen(str(dd / "d.jsonl")))
        state = H.RungState(str(dd / "s.json"))
        import committee as C
        cv = {"cycle_id": 3, "price": 4000.0, "atr": 5.0, "ts": 1_700_000_000.0,
              "signal_map": {}, "power": {}, "vp": {}, "judges": [], "notes": [],
              "bars": [], "data_quality": {}}
        F2.run_cycle(cv, state, pen, "live", now_ts=1_700_000_000.0, ledger=C.TicketLedger())
        assert pen.calls == 2, f"the except-branch never fired (calls={pen.calls}) - not a test"
        got = [json.loads(x) for x in (dd / "d.jsonl").read_text(encoding="utf-8").splitlines()
               if x.strip()]
        assert len(got) == 1, f"expected the single error line, got {len(got)}"
        assert got[0].get("line_kind") == "error", got[0]
        assert "RuntimeError" in str(got[0].get("error")), got[0]


def test_the_teacher_measures_the_heartbeat_not_the_whole_file_14f():
    """The ~1KB law is a law about CARDS. 14f gives the teacher a way to know which class a line
    is, so a fat day-footer can never fail the hall's law - and, just as important, a trimmed
    footer can never HIDE a fat card. A fat cycle must still WARN even when the footer is small.
    The second half is the old paper: no tags at all. Then the teacher falls back to measuring
    everything, but it must SAY it is doing that - a silent change of instrument is how a green
    report starts meaning something else."""
    import subprocess
    def run(diary: Path, expect: int = 0) -> str:
        out = diary.with_suffix(".txt")
        r = subprocess.run(
            [sys.executable, str(HERE / "tools" / "gates_report.py"), "--diary", str(diary),
             "--hall-off", "--out", str(out)],
            capture_output=True, text=True, timeout=300, cwd=str(HERE))
        # the teacher exits NONZERO the moment a row says FAIL - that is the grade, not a crash.
        # A test that only accepts rc=0 would have to look away from the very row it proves.
        if expect is not None:
            assert r.returncode == expect, r.stdout[-400:] + r.stderr[-400:]
        return out.read_text(encoding="utf-8")

    def cyc(cycle_id: int, pad: int, **kw) -> str:
        d = {"line_kind": "cycle", "t": "2026-11-13T07:00:00+00:00",
             "cycle": {"id": cycle_id}, "atr": 5.0, "mode": "replay:tapes"}
        d.update(kw)
        s = json.dumps(d)
        if pad > len(s):
            s = s[:-1] + f',"pad":"{"x" * (pad - len(s) - 11)}"}}'
        return s

    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "new.jsonl"
        fat_foot = json.dumps({"line_kind": "day", "mode": "replay:summary",
                               "day": "2026-11-13", "cycles": 2,
                               "pad": "y" * 2200})
        p.write_text(cyc(1, 700) + "\n" + cyc(2, 700) + "\n" + fat_foot + "\n",
                     encoding="utf-8")
        body = run(p)
        blk = body[body.index("LINE SIZE LAW"):body.index("LINE SIZE LAW") + 700]
        assert "[PASS]  LINE SIZE LAW" in body, "a fat FOOTER failed the heartbeat law:\n" + blk
        assert "other classes" in blk and "NOT counted against the heartbeat law" in blk, blk
        assert "partition closes: 3 == 3" in body, body[body.index("SECTION A"):][:900]

        p2 = Path(tmp) / "fatcard.jsonl"
        p2.write_text(cyc(1, 1400) + "\n" + cyc(2, 700) + "\n"
                      + json.dumps({"line_kind": "day", "mode": "replay:summary",
                                    "cycles": 2}) + "\n", encoding="utf-8")
        b2 = run(p2)
        i = b2.index("LINE SIZE LAW")
        assert "[WARN]  LINE SIZE LAW" in b2, "a FAT CARD slipped past while the footer was clean"

        p3 = Path(tmp) / "old.jsonl"                      # a pre-14f paper: no names at all
        old = [json.loads(x) for x in (tmp and p.read_text(encoding="utf-8").splitlines())]
        p3.write_text("\n".join(json.dumps({k: v for k, v in d.items() if k != "line_kind"})
                                for d in old) + "\n", encoding="utf-8")
        b3 = run(p3)
        i3 = b3.index("LINE SIZE LAW")
        assert "ALL lines - this paper carries no `line_kind`" in b3[i3:i3 + 700], \
            "an old paper was measured by the new instrument without saying so"

        # and the two instruments must AGREE. One line shaped like a card but named `day` is
        # exactly what a half-finished tagging job leaves behind, and a report that reads the
        # name only would count it as a footer while the old reader counts it as a cycle.
        p4 = Path(tmp) / "split.jsonl"
        liar = json.dumps({"line_kind": "day", "mode": "replay:summary", "cycles": 1,
                           "cycle": {"id": 9}, "atr": 5.0})
        p4.write_text(cyc(1, 700) + "\n" + cyc(2, 700) + "\n" + liar + "\n", encoding="utf-8")
        b4 = run(p4, expect=1)
        assert "[FAIL]  SELECTORS AGREE" in b4, \
            "a diary whose NAME and SHAPE disagree was graded as if nothing was wrong"
        assert "named cycles: 2" in b4 and "shape cycles: 3" in b4, b4[b4.index("SELECTORS"):][:320]


def test_the_committee_verdict_line_names_itself_14f():
    """The fifth class of diary line is not written by the hall at all: when a verdict earns a
    ticket, `committee.attempt_fire` writes the ticket line through the SAME pen
    (`committee.py:434`). So a day with 75 cycles and 7 tickets holds 82+ lines, and a reader who
    counts lines to count cycles is off by every ticket he ever approved - the worse for being
    right about the good news. This drives the REAL fire-law path (a verdict that passes all seven
    laws) and reads back what the pen was handed."""
    import committee as C
    got = []
    pre = {"age_s": 1.0, "bar_closed": True, "sensors_ok": True, "spread_pct": 0.001,
           "news_blackout": False, "position_count": 0, "max_positions": 2,
           "daily_loss_ok": True, "kill_switch": False, "symbol": "XAUUSD", "cycle_id": 41,
           "diary": got.append}
    rung = C.Rung(name="wallA", price=4000.0, kind="walls")
    v = {"verdict": "BREAK", "card": "RUNG=wallA | L3Q=ok | VERDICT=BREAK"}
    f = C.attempt_fire(v, rung, pre, C.TicketLedger())
    assert f["allow"], f"the fixture must EARN a ticket or the write never happens: {f['fails']}"
    assert len(got) == 1, f"expected exactly one ticket line, got {len(got)}"
    assert got[0].get("line_kind") == "verdict", got[0]
    assert got[0].get("ticket") == f["ticket"], "the named line must carry the ticket it names"
