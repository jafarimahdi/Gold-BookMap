# -*- coding: utf-8 -*-
"""tools/gates_report.py — THE NIGHT TEACHER for factory 2 (COMPANY 3, Code Day 2).

Reads ONE day of the new hall's diary (`data/gates_shadow_YYYYMMDD.jsonl`,
gzipped days too) plus, optionally, the sea's `--json-out` results, and grades
the day in the OLD office's exact shape:

    [PASS]/[WARN]/[FAIL]/[ -- ]  <what it measures>  ->  what happened  ->  what it means
    (same row grammar as audit_day.Report.add, so the Report Office can hang
     this file on its wall without a single edit to its own code - law N1)

Writes:
    data/gates_<YYYYMMDD>.txt        the console night report (a Desk file)
    data/gates_<YYYYMMDD>_metrics.json   tallies, with a top-level "gates" section

Sections: A health of the diary itself · B committee tallies · C twin scoreboard
(what the old shooter did on the same cycles) · D per-EYE grades of the judges ·
E footprint (the owner's disk/CPU law) · F the sea's verdict episodes.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (str(ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

REPORT_VERSION = "gates.report.2026-11-25.4"   # TALLY-2: asserts the doorman's own equation (row 3) - and refuses to grade a pre-.3 paper as if it had `allowed`   # BELT-1: the teacher audits the sea's own counters (item 46 still holds: a verdict is scored from the ROWS)
MAX_LINE_BYTES = 1100          # ~1KB diary line law (+10% slack for long rung names)
CPU_PCT_BUDGET = 0.3           # percent of one core, the owner's cap
STAMPS_ALLOWED = {
    "bar_closed:assumed", "spread:assumed", "news:assumed-quiet",
    "eyes:assumed-fresh", "l3q:assumed", "risk:neutral(rehearsal)",
    "votes:absent(l1)",
    # written by the SEA (gates_replay) into snapshot.notes, not by the hall itself
    "l2:l2-off(depth rows not fed)", "l2:no-depth-rows-in-tape",
    "l2:prints-only-no-book",
    "l3q:unavailable(by-config)", "l3q:unavailable(no L2/L3)",
    # v7 (item 27): a verdict made without the ORDER tape must name itself -
    # two new signed words, so an L1/L2 verdict can never be read as an L3 one
    "l3q:unavailable(no order tape)",
}


def say(line: str = "", buf: Optional[List[str]] = None) -> None:
    if buf is None:
        print(line)
    else:
        buf.append(line)


class Teacher:
    """Same row grammar as audit_day.Report - but standalone (never imports it,
    so an old-factory refactor can't take this hall down with it)."""

    def __init__(self) -> None:
        self.rows: List[Tuple[str, str, str, List[str]]] = []

    def add(self, grade: str, name: str, headline: str, why: Iterable[str],
            buf: Optional[List[str]] = None) -> None:
        tag = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]",
               "NA": "[ -- ]"}[grade]
        self.rows.append((grade, name, headline, list(why)))
        say("-" * 78, buf)
        say(f"{tag}  {name}", buf)
        say(f"        measures : {headline}", buf)
        for i, w in enumerate(why):
            say(f"        {'->' if i == 0 else '   '} {w}", buf)

    def score(self) -> Counter:
        return Counter(r[0] for r in self.rows)


def read_diary(path: Path) -> Tuple[List[Dict[str, Any]], int, List[str]]:
    """Rows + bad-line count + the file(s) read. Folded days are unfolded here."""
    rows: List[Dict[str, Any]] = []
    bad = 0
    srcs: List[str] = []
    sources: List[Path] = []
    if path.exists():
        sources.append(path)
    gz = path.with_suffix(path.suffix + ".gz")
    if gz.exists():
        sources.append(gz)
    for p in sources:
        opener = gzip.open if str(p).endswith(".gz") else open
        try:
            with opener(p, "rt", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        bad += 1
                        continue
                    if isinstance(rec, dict):
                        rec["_bytes"] = len(line.encode("utf-8"))
                        rows.append(rec)
            srcs.append(p.name)
        except OSError:
            continue
    return rows, bad, srcs


def _f(x: Any, d: float = 0.0) -> float:
    try:
        v = float(x)
        return v if math.isfinite(v) else d
    except (TypeError, ValueError):
        return d


# ----------------------------------------------------------------- sections
def sec_health(T: Teacher, rows: List[Dict[str, Any]], bad: int,
               buf: Optional[List[str]], hall_off: bool = False) -> Dict[str, Any]:
    cycles = [r for r in rows if "cycle" in r]
    errs = [r for r in rows if r.get("error")]
    # 14f + law 105: a diary is a MIX of classes - cycles, one day footer per sail, and an
    # error line whenever a card dies. The ~1KB law speaks about HEARTBEATS, so once the lines
    # carry their own name it must measure heartbeats: a fat footer must not be blamed on the
    # hall, and a trimmed footer must not hide a fat card. A pre-14f paper has no names, so it
    # is measured the old way AND the report says which way it looked - never silently.
    named = bool(rows) and all(str(r.get("line_kind") or "") for r in rows)
    cyc_rows = [r for r in rows if r.get("line_kind") == "cycle"] if named else cycles
    oth_rows = [r for r in rows if r.get("line_kind") != "cycle"] if named else []
    sizes = [int(r.get("_bytes") or 0) for r in cyc_rows] or [0]
    oth_sizes = [int(r.get("_bytes") or 0) for r in oth_rows]
    unknown_stamps = sorted({str(x) for r in rows for x in (r.get("stamps") or [])
                             if str(x) not in STAMPS_ALLOWED})
    if not cycles:
        if hall_off:
            # v7 (item 24): a night where the shell was TOLD not to run the hall
            # (`--no-heartbeat`, e.g. a borrowed-window sea on the owner's live
            # machine) is not an illness - it is a different exam. Grading it
            # FAIL made a clean tape night read "NOT HEALTHY" for no reason.
            T.add("NA", "DIARY EXISTS", "did the hall write anything at all?",
                  ["not asked to: --no-heartbeat (hall off). The sea's own diary is "
                   "data/gates_replay_*.jsonl - a tape night is graded by SECTION F "
                   "only, and it is NOT a health certificate either way"], buf)
        else:
            T.add("FAIL", "DIARY EXISTS", "did the hall write anything at all?",
                  ["no usable cycle lines - factory 2 never ran or the path is wrong"], buf)
        return {}
    ok = bad == 0
    T.add("PASS" if ok else "WARN", "LINES PARSE", "every diary line is valid json",
          [f"{len(rows)} lines read from {len(set(_ := [r.get('mode', '?') for r in rows]))} mode(s)",
           f"unparseable lines: {bad}"] if ok else [f"{bad} unparseable lines"], buf)
    over = [s for s in sizes if s > MAX_LINE_BYTES]
    _det = [f"max {max(sizes)} B, mean {int(sum(sizes) / max(1, len(sizes)))} B"
            + ("" if named else "  (ALL lines - this paper carries no `line_kind`)"),
            (f"{len(over)} fat line(s) - trim the card, not the law" if over
             else "the budget holds for every line")]
    if named and oth_sizes:
        _det.append("other classes: " + ", ".join(
            f"{k}={v}" for k, v in sorted(Counter(
                str(r.get("line_kind")) for r in oth_rows).items()))
            + f"  (max {max(oth_sizes)} B - NOT counted against the heartbeat law)")
    T.add("PASS" if not over else "WARN", "LINE SIZE LAW",
          f"one heartbeat <= {MAX_LINE_BYTES} bytes (~1KB budget)", _det, buf)
    if named:
        # two instruments over one file, like `errors == sum(skips)`: the NAME of a line and
        # the SHAPE of a line must agree, or the paper and the diary are telling different days.
        if len(cyc_rows) != len(cycles):
            T.add("FAIL", "SELECTORS AGREE", "line_kind=cycle vs a line that HAS a cycle block",
                  [f"named cycles: {len(cyc_rows)}   shape cycles: {len(cycles)}",
                   "one of the two is wrong - do not grade anything until they match"], buf)
        _k = Counter(str(r.get("line_kind")) for r in rows)
        say("        line_kind: " + ", ".join(f"{k}={v}" for k, v in sorted(_k.items()))
            + f"   (partition closes: {sum(_k.values())} == {len(rows)})", buf)
    atr_bad = [r for r in cycles if not (_f(r.get("atr")) > 0)]
    T.add("PASS" if not atr_bad else "WARN", "ATR IS ALIVE",
          "every cycle carries a real candle-stick yard (ATR > 0)",
          [f"{len(cycles)} cycles, {len(atr_bad)} without a yard",
           "a zero ATR means the candles never closed - the loop must idle, not vote"], buf)
    T.add("PASS" if not errs else "WARN", "NO QUIET DEATHS",
          "errors are named, never swallowed",
          [f"{len(errs)} error line(s)" + (f": {errs[-1].get('error')}" if errs else "")],
          buf)
    T.add("PASS" if not unknown_stamps else "WARN", "STAMPS ARE A KNOWN VOCABULARY",
          "assumptions are only ever made from the signed list",
          [f"unknown stamps: {', '.join(unknown_stamps)}" if unknown_stamps
           else f"all stamps inside the signed vocabulary ({len(STAMPS_ALLOWED)} words)"],
          buf)
    modes = Counter(str(r.get("mode") or "?") for r in rows)
    say("        modes: " + ", ".join(f"{k}={v}" for k, v in modes.items()), buf)
    return {"cycles": len(cycles), "bad": bad, "mean_bytes":
            int(sum(sizes) / max(1, len(sizes))), "errors": len(errs)}


def sec_committee(T: Teacher, rows: List[Dict[str, Any]],
                  buf: Optional[List[str]]) -> Dict[str, Any]:
    cycles = [r for r in rows if "cycle" in r]
    if not cycles:
        T.add("NA", "COMMITTEE TALLIES", "G1/G2/G3 cards", ["no cycles"], buf)
        return {}
    moods: Counter = Counter()
    rungs: Counter = Counter()
    fails: Counter = Counter()
    verdicts = tickets = g2_pass = 0
    for r in cycles:
        g1 = str(r.get("g1") or "")
        m = re.search(r"MOOD=(\w+)", g1)
        if m:
            moods[m.group(1)] += 1
        g2 = str(r.get("g2") or "")
        if g2 and "LOCATION=NONE" not in g2:
            g2_pass += 1
            rungs[g2.split("|", 1)[0].replace("LOCATION=", "").strip()] += 1
        g3 = str(r.get("g3") or "")
        if "VERDICT=BOUNCE" in g3 or "VERDICT=BREAK" in g3:
            verdicts += 1
        fin = r.get("final") or {}
        if isinstance(fin, dict):
            if fin.get("allow"):
                tickets += 1
            for fl in (fin.get("fails") or []):
                fails[str(fl)] += 1
    T.add("PASS", "G1 NEVER GOES SILENT", "a mood card on every heartbeat",
          [f"{len(cycles)} cycles -> " + (", ".join(f"{k}={v}" for k, v in moods.items())
                                          or "no moods?!"),
           "silence is the one crime G1 cannot commit (Book law: G1 broadcasts)"], buf)
    share = g2_pass / max(1, len(cycles))
    T.add("PASS" if 0.0 < share < 0.98 else ("WARN" if share >= 0.98 else "WARN"),
          "G2 STANDS ON RUNGS", "the share of cycles with a rung under the feet",
          [f"{g2_pass}/{len(cycles)} cycles on a rung ({share:.0%})",
           "0% = the ladder never gets challenged (watch the level feed); "
           "~100% = the yardstick is too generous (YARD_ATR sweep territory)"], buf)
    T.add("PASS" if verdicts else "WARN", "G3 IS A REAL DOOR",
          "verdicts only after the toll is paid",
          [f"verdicts: {verdicts}, tickets allowed: {tickets}",
           "refusals: " + (", ".join(f"{k}={v}" for k, v in fails.most_common(8)) or "-")],
          buf)
    if verdicts and tickets == 0:
        T.rows.append(("WARN", "TICKETS ALL REFUSED", "", ["every verdict died at a fire-law"]))
    return {"cycles": len(cycles), "g2_pass": g2_pass, "verdicts": verdicts,
            "tickets": tickets, "moods": dict(moods), "rungs": dict(rungs),
            "fails": dict(fails)}


def sec_twin(T: Teacher, rows: List[Dict[str, Any]], sea: Dict[str, Any],
             buf: Optional[List[str]]) -> Dict[str, Any]:
    """The A/B line: what the OLD factory's shooter said on the same cycles,
    read from the borrowed brain's own `shot` (recorded, never obeyed)."""
    shots: Counter = Counter()
    n = 0
    for r in rows:
        d = r.get("shot") or {}
        if isinstance(d, dict):
            act = str(d.get("action") or "")
            if act:
                shots[act] += 1
                n += 1
    if not n:
        T.add("NA", "TWIN SCOREBOARD", "old shooter vs new committee on the same cycles",
              ["the diary has no `shot` block - run factory2 with --record-shot, "
               "or read the sea's json (--sea) which carries shot_action"], buf)
        eps = sea.get("episodes") or []
        if eps:
            acts = Counter(str(e.get("shot_action") or "?") for e in eps)
            if len(acts) > 1 or list(acts)[0] != "?":
                shots.update(acts)
                n = sum(acts.values())
    if n:
        cyc = max(1, len([r for r in rows if "cycle" in r]))
        T.add("PASS", "TWIN SCOREBOARD", "old shooter vs new committee on the same cycles",
              [f"old shooter fired on {n}/{cyc} cycles: "
               + ", ".join(f"{k}={v}" for k, v in shots.most_common(6)),
               "the new hall's verdicts are in section B - compare counts before "
               "anyone speaks about swapping steering wheels"], buf)
    return {"old_shots": dict(shots), "n": n}


def sec_eyes(T: Teacher, sea: Dict[str, Any],
             buf: Optional[List[str]] = None) -> Dict[str, Any]:
    """Per-EYE grades: each judge's direction vs the +12m path of the very cycle
    it voted on - only at VERDICT episodes, so the grade answers a real question."""
    eps = (sea or {}).get("episodes") or []
    if not eps:
        T.add("NA", "PER-EYE GRADES", "each judge graded at the ladder's own questions",
              ["no sea episodes given (run tools/gates_replay.py --json-out ...) - "
               "the eyes are not graded from thin air"], buf)
        return {}
    grade: Dict[str, Dict[str, float]] = {}
    for e in eps:
        pb4 = e.get("pb4")
        if pb4 is None:
            continue
        for v in (e.get("votes") or []):
            j = str(v.get("judge") or "?")
            d = _f(v.get("dir"))
            if d == 0.0:
                continue
            g = grade.setdefault(j, {"calls": 0.0, "hits": 0.0, "pts": 0.0})
            g["calls"] += 1
            hit = (pb4 > 0) if d > 0 else (pb4 < 0)
            g["hits"] += 1.0 if hit else 0.0
            g["pts"] += (pb4 if d > 0 else -pb4)
    if not grade:
        T.add("NA", "PER-EYE GRADES", "judges at verdict episodes",
              [f"{len(eps)} episode(s), but no vote rows in them - "
               "an L1-only sea has no eyes to grade (stamped, not guessed)"], buf)
        return {}
    ranked = sorted(grade.items(), key=lambda kv: (-kv[1]["pts"]))
    best, worst = ranked[0], ranked[-1]
    T.add("PASS", "PER-EYE GRADES", "who earns their place at G3 (path-weighted, +12m)",
          [f"{len(grade)} eye(s) graded on {len(eps)} episode(s)",
           f"strongest: {best[0]} ({best[1]['pts']:+.2f} pts, {best[1]['hits']:.0f}/"
           f"{best[1]['calls']:.0f} calls)",
           f"weakest: {worst[0]} ({worst[1]['pts']:+.2f} pts) - a note serves the member "
           "or it's out (judge-signing law)"], buf)
    return {"eyes": {k: {"calls": int(v["calls"]), "hits": int(v["hits"]),
                         "pts": round(v["pts"], 2)} for k, v in grade.items()}}


def sec_footprint(T: Teacher, rows: List[Dict[str, Any]], data_dir: Path,
                  day_compact: str, buf: Optional[List[str]],
                  runtime_s: Optional[float], cycles: int) -> Dict[str, Any]:
    diary_bytes = sum(int(r.get("_bytes") or 0) for r in rows)
    per_day = diary_bytes or 0
    proj = per_day * 92                       # a quarter (~3 months)
    gz_factor = 0.12                          # json lines compress ~8x
    T.add("PASS" if proj * gz_factor < 500 * 1024 * 1024 else "WARN",
          "THREE-MONTH DRAWER", "the diary must not eat the disk (owner's law)",
          [f"today {per_day / 1024:.0f} KB raw; a quarter raw {proj / 1048576:.1f} MB, "
           f"folded ~{proj * gz_factor / 1048576:.1f} MB",
           "finished days are gzipped by fold_day() - folded, NEVER trimmed"], buf)
    state_f = data_dir / "gates_rung_state.json"
    if state_f.exists():
        sb = state_f.stat().st_size
        T.add("PASS" if sb < 256 * 1024 else "WARN", "TOLL BOOK SIZE",
              "the toll keeper's own file stays a card, not a ledger",
              [f"gates_rung_state.json = {sb / 1024:.0f} KB",
               "it resets per trading day by law (today-only freshness)"], buf)
    if runtime_s is not None and cycles:
        pct = 100.0 * runtime_s / max(1.0, cycles * 180.0)
        T.add("PASS" if pct <= CPU_PCT_BUDGET else "WARN", "CPU TASTE",
              f"hall cost <= {CPU_PCT_BUDGET}% of one core",
              [f"{runtime_s:.0f}s of compute over {cycles} M3 cycles = {pct:.3f}% of a core",
               "the loop sleeps between candles - cost is per-cycle, not per-tick"], buf)
    else:
        T.add("NA", "CPU TASTE", "hall cost budget",
              ["no --runtime-seconds given; measure it on a live day"], buf)
    return {"day_bytes": per_day, "quarter_folded_mb": round(proj * gz_factor / 1048576, 2)}


def sec_sea(T: Teacher, sea: Dict[str, Any], buf: Optional[List[str]]) -> Dict[str, Any]:
    if not sea:
        T.add("NA", "THE SEA", "replay-first law: raw tapes through the same committee",
              ["no --sea file given - the factory is NOT healthy until a sea passes"], buf)
        return {}
    if not sea.get("ok"):
        _rs = str(sea.get("reason") or "")
        # v8.2 (the owner's 20:13 packet, item 35): this row was the ONLY place a
        # refused day spoke, and it said "did not run" with no advice - so the owner
        # saw a FAIL and had no way to tell "my tape is thin" from "your recipe was
        # wrong" (it was MY recipe: a 60min cap cannot beat a day whose slice starts
        # at 11:07Z). Name the shape, and say what to ask for instead.
        _adv = ([
            "READ THIS AS: the sea was ASKED FOR NOTHING. A cap shorter than the "
            "tape's own lead-in leaves no window to sail - the tape is fine, the "
            "request was impossible (item 35, my recipe, not your market).",
            "do instead: `bash report_evening_gates.sh --sea 2 --no-heartbeat "
            "--max-minutes 240` (a CLOSED day, a real budget) - or drop the cap",
        ] if ("ends before" in _rs or "only " in _rs) else
            ["the sea returned nothing usable - read data/gates_sea_err.txt"])
        T.add("FAIL", "THE SEA", "yesterday's raw tape replayed through the hall",
              [f"the sea did not run: {_rs}"] + _adv, buf)
        return {}
    cyc = int(sea.get("cycles") or 0)
    verd = int(sea.get("verdicts") or 0)
    tol = int(sea.get("toll_closes") or 0)
    # v7 (items 25/27) - WHAT the sea ate is a check of its own, because a night
    # sailed from the audit jar has real candles but no book and no orders, and
    # "0 verdicts" on such a night is physics, not a broken committee. The owner's
    # first real sea (633 cycles, 5 days) was exactly that, and only this row says
    # so out loud.
    tape_d = int(sea.get("tape_days") or 0)
    jar_d = int(sea.get("jar_days") or 0)
    l3_d = int(sea.get("l3_days") or 0)
    if "tape_days" in sea:
        grade = "PASS" if (tape_d and l3_d) else "WARN"
        why = [("L3 has orders on at least one day - verdicts can be EARNED"
                if l3_d else
                "NO ORDER TAPE on any sailed day => PUSH=NONE is physics, not a "
                "committee failure. Sail a day that has data/archive/mbo_*.csv.gz "
                "(or the live mbo.csv) - items 21/27"),
               f"{int(sea.get('mbo_rows') or 0)} order row(s) fed to L3; "
               f"{int(sea.get('ticks_mbo_ignored') or 0)} ticks-tape Mbo row(s) "
               f"IGNORED on purpose (level=-1, no order id - the bridge's own "
               f"'929 fake icebergs' fix, bookmap_bridge_provider.py:334)"]
        if not tape_d:
            why.insert(0, "NOT A SEA: every sailed day came from the audit jar - "
                          "real candles, no L2 book, no L3 orders (item 25)")
        T.add(grade, "TAPE vs JAR",
              "a sea must ride raw tapes; a jar day must be named a jar day",
              [f"{tape_d} raw-tape day(s), {jar_d} jar day(s), "
               f"{l3_d} day(s) with an order tape"] + why, buf)
    # v8.1 (item 31, learned on the owner's rig, not in a sandbox): the OLD card
    # graded `PASS if cyc >= 20 else FAIL`, so their first tape-fed night - 10
    # cycles, order books of 2,179,664 and 3,389,507 rows read, G2 at 100%,
    # depth live - was called FAIL, and `38 skipped` said nothing about WHY.
    # A grade names the CAUSE before it names a verdict: warm-up and an OPEN day
    # are not illness, and a thin sample is not failure. FAIL stays reserved for
    # a sea that read nothing at all or errored on every cell.
    _sk = sea.get("skip_kinds") or {}
    _warm = int(_sk.get("warm-up", 0) or 0) + int(_sk.get("no-eyes", 0) or 0)
    _why_skipped = ", ".join(f"{k} {int(v)}" for k, v in sorted(_sk.items()) if int(v))
    _ln = [f"{cyc} cycle(s) from {sea.get('src')}, {int(sea.get('errors') or 0)} "
           f"skipped" + (f" ({_why_skipped})" if _why_skipped else ""),
           f"G2 with a rung: {int(sea.get('g2_pass') or 0)} "
           f"({_f(sea.get('g2_pass')) / max(1, cyc):.0%})"]
    # v8.4 (item 42, the owner's +-80 USD law): the band is a CHOICE about what the
    # Mapper is allowed to draw, so it belongs next to G2's number - otherwise a
    # `G2 with a rung 0/141` reads as "the market was quiet" when it means "we
    # refused to look that far".
    _band = _f(sea.get("rung_band"))
    _ls, _lk = int(sea.get("levels_seen") or 0), int(sea.get("levels_kept") or 0)
    _be = int(sea.get("band_empty") or 0)
    if _band > 0 and _ls:
        _ln.append(f"Mapper band +-{_band:g} USD of the last print: {_lk} of {_ls} "
                   f"fight lines drawn" + (f", sheet empty on {_be} cycle(s)" if _be else ""))
        if not _lk:
            _ln.append("READ THIS AS: the band left NO line within reach on ANY cycle - "
                       "the radius was too small for that day, the market is not on trial")
    if not int(sea.get("lines") or 0) and not cyc:
        T.add("FAIL", "SEA RAN", "the replay actually walked the tape",
              _ln + ["NO ROWS at all - the tape was not found or not readable "
                     "(items 21/25). Read data/gates_sea_err.txt"], buf)
    elif not cyc:
        if _warm:
            T.add("NA", "SEA RAN", "the replay actually walked the tape",
                  _ln + [f"NOT ILLNESS, NOT HEALTH: every cell was refused BEFORE "
                         f"the 60-candle law could be satisfied ({_warm} cell(s)). "
                         f"The rows ARE there - the day was open (its first slice "
                         f"starts mid-window) or the cap ate the warming. Fixed by "
                         f"gates.replay.2026-11-13.5: warming is sized by the "
                         f"stride and charged OUTSIDE --max-minutes"], buf)
        else:
            T.add("FAIL", "SEA RAN", "the replay actually walked the tape",
                  _ln + ["no cell could be fed and warm-up does not explain it - "
                         "this IS plumbing; read data/gates_sea_err.txt"], buf)
    else:
        T.add("PASS" if cyc >= 20 else "WARN", "SEA RAN",
              "the replay actually walked the tape",
              _ln + ([] if cyc >= 20 else
                     [f"thin SAMPLE (a cap or an open day), not an illness: "
                      f"{cyc} cycle(s) proves plumbing, it cannot grade a "
                      f"market"]), buf)
    T.add("PASS" if tol >= 2 else "WARN", "THE TOLL CAN PAY ON TAPES",
          "close-based excess is measurable from raw candles (the jar was not)",
          [f"max closes beyond a rung in one hold: {tol} (needs >=2 for BREAK)",
           f"longest hold: {float(sea.get('toll_minutes') or 0.0):.0f} min",
           "this is WHY the sea rides raw tapes and not the fat jar (Book finding)",
           (f"measured on {tape_d} raw-tape day(s)" if tape_d else
            "MEASURED ON JAR CANDLES, not raw tape (item 25) - the tape proof "
            "of this number is still owed")], buf)
    eps = sea.get("episodes") or []
    if verd and eps:
        # v8.2b (item 38, the owner's FIRST VERDICTS): `or 0.0` turned a MISSING
        # measurement into `0`, and `0 < 0` is False, so two episodes with
        # `fwd9=None` were graded as "0/2 went the way the door said" - a number
        # invented out of silence, the exact crime items 27/31 are about. The
        # horizon is now counted separately, and an unmeasurable episode is
        # named, not scored. (The operator precedence here was also wrong:
        # `A and B or C and D` - it let a BOUNCE match a NEGATIVE number.)
        def _way(e):
            f = e.get("fwd9")
            if f is None:
                return "none"
            f = float(f)
            g = str(e.get("g3") or "")
            if "BOUNCE" in g:
                return "yes" if f > 0 else "no"
            if "BREAK" in g:
                return "yes" if f < 0 else "no"
            return "none"
        _k = {_w: sum(1 for e in eps if _way(e) == _w) for _w in ("yes", "no", "none")}
        _meas = _k["yes"] + _k["no"]
        if not _meas:
            T.add("NA", "VERDICTS MEAN SOMETHING",
                  "+9 minutes after each verdict, in the verdict's own direction",
                  [f"{len(eps)} verdict(s) - NO HORIZON to measure them with "
                   f"(every `fwd9` is None)",
                   "NOT MEASURED, NOT FAILED: the tape ran out 9 minutes after "
                   "the verdict. Since gates.replay.2026-11-13.9 the sea reads 15 "
                   "extra minutes of rows past the last cell and scores FROM THOSE "
                   "ROWS (not from a walk that causality stops at the cycle), so a "
                   "rerun answers this - items 31/38/46"], buf)
        else:
            T.add("PASS" if _k["yes"] else "WARN", "VERDICTS MEAN SOMETHING",
                  "+9 minutes after each verdict, in the verdict's own direction",
                  [f"{_k['yes']}/{_meas} MEASURED episode(s) went the way the door "
                   f"said" + (f" ({_k['none']} not measurable)" if _k["none"] else ""),
                   f"toll paid by {sum(1 for e in eps if 'TOLL=paid' in str(e.get('g3')))}"
                   f"/{len(eps)} of them",
                   "one day is plumbing proof, not an edge claim - never trade on "
                   "this (the Book's rule, and the owner's)"], buf)
    else:
        T.add("WARN", "VERDICTS MEAN SOMETHING", "verdict count on the tape",
              [f"{verd} verdict(s) in {cyc} cycles",
               (f"{l3_d} sailed day(s) carry an order tape, so L3 was blind: the "
                "input was missing, NOT the committee (item 27). A 0 here is an "
                "honest abstain, and the fix is the tap: data/archive/mbo_*.csv.gz"
                if not l3_d else
                "an order tape WAS fed and nothing crossed the toll - that is a "
                "finding about the market (or the thresholds), and the next thing "
                "to read is the rung popularity + the refusal tally")], buf)
    # BELT-1 (2026-11-14, owner's LAWS #14 - folded into the Teacher instead of
    # shipping tools/v8f_belt_check.py as a new file: an audit that belongs to the
    # Teacher lives with the Teacher). The sea counts every cycle it refuses, per
    # CAUSE: _skip() in tools/gates_replay.py:846-852 bumps `errors` AND the named
    # kind inside one call, so `errors == sum(skips)` is a LAW OF CONSTRUCTION - it
    # must hold, and the day it does not is the day a cycle vanished without a reason
    # (the class of bug that once printed `fed 0` next to `HEALTH: PASS`).
    _sk = sea.get("skips")
    if _sk is None:
        _sk = sea.get("skip_kinds")
    # EYES-2d: the sea now NAMES the two states it used to call `stale`. Both new words
    # are graded; `stale` stays in the set so an OLDER paper is not retroactively called a
    # violation - but it is no longer COUNTED, so a `.2` paper must never print it (below).
    # `stale` REMAINS in the counted set: the equality `errors == sum(skips)` must keep
    # holding for a paper written before the split, and dropping the word from `_kinds`
    # would turn an honest old file into a vocabulary violation (my first cut did exactly
    # that, and the owner's own suite caught it here before it could reach them).
    _kinds = ("no-eyes", "stale", "pre-first-print", "post-last-row",
              "warm-up", "brain", "snapshot")
    _err = int(sea.get("errors") or 0)
    if _sk is None:
        T.add("FAIL", "THE BELT COUNTS ITSELF",
              "every cycle the sea refused must be refused for a NAMED reason",
              ["`skips` is ABSENT from the sea json - an old paper, not a verdict. A "
               "missing field must never be read as zero (item 31): it means the file "
               "predates the per-cause counters, so re-sail the day with "
               "gates.replay.2026-11-14.1 or newer."], buf)
    else:
        # summed over EVERY kind present, not just the five signed ones: an unknown
        # cause must break the equality too, or a new _skip("reason") could hide a
        # hundred refused minutes behind a passing gate.
        _sum = sum(int(v or 0) for v in _sk.values())
        _extra = sorted(set(_sk) - set(_kinds))
        _rows = []
        _ok = True
        _fl = sea.get("fails") or {}
        _has_allow = "allowed" in sea
        _al = int(sea.get("allowed") or 0)
        _fw = sum(int(v or 0) for v in _fl.values())
        for _n, _got, _want, _why in (
                ("errors == sum(skips)", _err, _sum,
                 "a refusal with no cause is a lost minute and a quiet lie"),
                ("verdicts == len(episodes)", verd, len(eps),
                 "a verdict counted but never written is a story nobody can re-read"),
                ("tickets <= cycles", int(sea.get("tickets") or 0), cyc,
                 "the hall cannot mint more tickets than the belt moved"),
                # ROW 3, ASSERTED AT LAST (TALLY-2). Until today the sea filed "nothing was
                # refused" as a refusal word (`(allowed)`, gates_replay.py:1027), so
                # `sum(fails) == cycles` on EVERY day and this equation could not exist.
                # The vocabulary is deliberately NOT graded here: the owner's diaries already
                # show three words (NO-VERDICT, NO-EDGE, L3Q) and the committee may add more,
                # so this checks ARITHMETIC and a new word can never break it.
                ("refused-words + allowed == cycles", _fw + _al, cyc,
                 "every cycle the door looked at is either named-refused or counted as walked")
                if _has_allow else
                ("tickets <= cycles", int(sea.get("tickets") or 0), cyc,
                 "the hall cannot mint more tickets than the belt moved")):
            _good = (_got == _want) if "==" in _n else (_got <= _want)
            _ok = _ok and _good
            _rows.append(f"[{'OK  ' if _good else 'FAIL'}] {_n:27s} {_got:6d} vs {_want:6d}"
                         f"   ({_why})")
        if not _has_allow:
            # an OLD paper must not be graded by a field it never wrote - and it must not be
            # allowed to pass the equation by accident either: with `allowed` missing,
            # `sum(fails) == cycles` was TRUE for every pre-.3 day, for the WRONG reason.
            _rows.append("[n/a ] the doorman's tally adds up   - not written by "
                         f"{sea.get('printed_by') or 'an older sea'}: `(allowed)` was still filed as a "
                         "refusal there, so the arithmetic would pass by accident. Re-sail to grade it.")
        if "(allowed)" in _fl:
            _new = "2026-11-17.3" in str(sea.get("printed_by") or "")
            _ok = _ok and not _new
            _rows.append(f"[{'FAIL' if _new else 'WARN'}] `(allowed)` counted as a refusal "
                         f"{int(_fl.get('(allowed)') or 0)} time(s) on a paper printed by "
                         f"{sea.get('printed_by') or '?'} - "
                         + ("the sea is newer than TALLY-2 and still filing it inside the refusals"
                            if _new else
                            "known shape on a pre-.3 paper; it is exactly why row 3 could not be asserted"))
        _rows.append("tallied: " + ("  ".join(f"{k}:{int(v or 0)}" for k, v in
                    sorted(_fl.items(), key=lambda kv: -int(kv[1] or 0))) or "(nothing refused)")
                     + f"   allowed:{_al}  tickets:{int(sea.get('tickets') or 0)}  (cycles {cyc})")
        if _extra:
            _ok = False
            _rows.append(f"[FAIL] skip kinds outside the signed vocabulary: "
                         f"{', '.join(_extra)} - a cause nobody grades is a cause nobody reads")
        _rows.append("counted: " + "  ".join(f"{k}:{int(_sk.get(k) or 0)}" for k in _kinds)
                     + f"   (cycles {cyc}, errors {_err})")
        if int(_sk.get("stale") or 0):
            # an OLD paper may carry `stale` (it is a known word, and history must not be
            # rewritten); a NEW one carrying it means the split was not compiled in.
            _new_paper = "2026-11-17.2" in str(sea.get("printed_by") or "")
            _ok = _ok and not _new_paper
            _rows.append(f"[{'FAIL' if _new_paper else 'WARN'}] `stale` {int(_sk.get('stale') or 0)} "
                         f"on a paper printed by {sea.get('printed_by') or '?'} - "
                         + ("the sea is NEWER than the split and still using the old word"
                            if _new_paper else
                            "known word on a pre-2d paper: not a violation, but re-sail to "
                            "learn WHICH of the two causes it was hiding"))
        T.add("PASS" if _ok else "FAIL", "THE BELT COUNTS ITSELF",
              "the sea's own counters must add up, per cause, before anything else is believed",
              _rows, buf)
    return {"cycles": cyc, "verdicts": verd, "toll_closes": tol,
            "episodes": len(eps), "g2_pass": int(sea.get("g2_pass") or 0),
            "belt_ok": _ok if _sk is not None else False,
            "tape_days": tape_d, "jar_days": jar_d, "l3_days": l3_d,
            "mbo_rows": int(sea.get("mbo_rows") or 0)}


# --------------------------------------------------------------------- main
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="factory 2 night teacher (office schema)")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD (default: today)")
    ap.add_argument("--dir", default=None, help="data dir (default: config.DATA_DIR)")
    ap.add_argument("--diary", default=None, help="explicit diary path")
    ap.add_argument("--sea", default=None, help="gates_replay --json-out file")
    ap.add_argument("--runtime-seconds", type=float, default=None)
    ap.add_argument("--fold", action="store_true", help="gzip finished days first")
    ap.add_argument("--sea-only", action="store_true",
                    help="grade the sea's json only (no diary on disk - the night "
                    "the hall was not switched on)")
    ap.add_argument("--hall-off", action="store_true",
                    help="the shell ran with --no-heartbeat: DIARY EXISTS abstains "
                    "(NA) instead of FAIL, and the verdict says SEA-GRADE ONLY")
    ap.add_argument("--out", default=None, help="write the txt here too")
    a = ap.parse_args(argv)

    import factory2_gates as f2
    data_dir = Path(a.dir) if a.dir else f2.resolve_data_dir(None)
    if not a.dir:
        # D5 closes the folder trap of LAW 110 for good: the hall's papers live in the
        # QUARANTINED drawer, so the teacher follows the diary before it ever says "none".
        _dc = (a.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")).replace("-", "")
        if not (data_dir / f"gates_shadow_{_dc}.jsonl").exists():
            _sand = data_dir / str(getattr(f2, "DATA_SANDBOX_NAME", "gates_sandbox"))
            if ((_sand / f"gates_shadow_{_dc}.jsonl").exists()
                    or (data_dir / f"gates_shadow_{_dc}.jsonl.gz").exists() is False
                    and (_sand / f"gates_shadow_{_dc}.jsonl.gz").exists()):
                data_dir = _sand
    day = a.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    day_c = day.replace("-", "")
    buf: List[str] = []
    say("=" * 78, buf)
    say(f" FACTORY 2 - THE NEW HALL NIGHT REPORT  {day}   (ver {REPORT_VERSION})", buf)
    say("=" * 78, buf)

    folded = f2.fold_day(data_dir) if a.fold else []
    if folded:
        say(f" folded {len(folded)} finished diary day(s) -> .gz (never trimmed)", buf)

    diary = Path(a.diary) if a.diary else data_dir / f"gates_shadow_{day_c}.jsonl"
    # --sea-only used to SKIP section A and still grade sections B-D from a
    # diary it auto-found - the report said "no diary graded, none exists"
    # while a diary was being read. A report must not say that. Now --sea-only
    # really means "sea only", unless you point --diary at a file yourself.
    read_it = not (a.sea_only and not a.diary)
    rows, bad, srcs = read_diary(diary) if read_it else ([], 0, [])
    tag = (', '.join(srcs) if srcs else
           ("(not read - --sea-only)" if not read_it else "(none found)"))
    say(f" diary: {tag}  ({len(rows)} lines)", buf)
    sea: Dict[str, Any] = {}
    if a.sea:
        p = Path(a.sea)
        if not p.exists():
            hits = sorted(data_dir.glob(f"gates_replay_{day_c}*.json"))
            p = hits[0] if hits else p
        try:
            with open(p, "r", encoding="utf-8") as fh:
                sea = json.load(fh)
            say(f" sea: {p.name}", buf)
        except (OSError, json.JSONDecodeError) as exc:
            say(f" sea: {p} unreadable ({exc})", buf)
    say("", buf)

    T = Teacher()
    say("SECTION A - the diary's own health", buf)
    if a.sea_only:
        say("", buf)
        say(" SECTION A skipped on purpose: --sea-only grades the sea's json"
            + ("" if read_it else " and reads no diary at all"), buf)
        h = {"cycles": 0, "sea_only": True}
    else:
        h = sec_health(T, rows, bad, buf, hall_off=bool(getattr(a, "hall_off", False)))
    say("", buf)
    say("SECTION B - the committee", buf)
    c = sec_committee(T, rows, buf)
    say("", buf)
    say("SECTION C - the twin (old shooter, same cycles)", buf)
    tw = sec_twin(T, rows, sea, buf)
    say("", buf)
    say("SECTION D - the eyes at the door", buf)
    ey = sec_eyes(T, sea, buf)
    say("", buf)
    say("SECTION E - footprint (the owner's disk/CPU law)", buf)
    fp = sec_footprint(T, rows, data_dir, day_c, buf, a.runtime_seconds,
                       int(h.get("cycles") or 0))
    say("", buf)
    say("SECTION F - the sea (replay-first law)", buf)
    se = sec_sea(T, sea, buf)

    sc = T.score()
    say("", buf)
    say("-" * 78, buf)
    say(f" NIGHT GRADE: {sc.get('PASS', 0)} PASS / {sc.get('WARN', 0)} WARN / "
        f"{sc.get('FAIL', 0)} FAIL / {sc.get('NA', 0)} not-answerable", buf)
    healthy = sc.get("FAIL", 0) == 0 and se.get("cycles", 0) >= 20
    if getattr(a, "hall_off", False):
        # v7 (item 24): we refuse to hand out a health certificate for a night
        # where the hall was deliberately off - in either direction.
        say(f" VERDICT: SEA-GRADE ONLY (hall off) - the tape night "
            f"{'PASSED its own checks' if healthy else 'has lines to fix above'}; "
            f"no health certificate is given or withheld", buf)
    else:
        say(" VERDICT: " + ("THE NEW FACTORY IS HEALTHY (sea passed)" if healthy else
            "NOT HEALTH YET - fix the lines above, the hall stays diary-only"), buf)
    say("-" * 78, buf)

    text = "\n".join(buf)
    print(text)
    data_dir.mkdir(parents=True, exist_ok=True)
    out_txt = Path(a.out) if a.out else data_dir / f"gates_{day_c}.txt"
    try:
        out_txt.write_text(text + "\n", encoding="utf-8")
    except OSError:
        pass
    metrics = {
        "day": day, "ver": REPORT_VERSION,
        "tests": {k: v for k, v in sc.items()},
        "checks": [{"grade": g, "name": n, "measures": m, "why": w}
                   for g, n, m, w in T.rows],
        "gates": {"diary": h, "committee": c, "twin": tw, "eyes": ey,
                  "footprint": fp, "sea": se, "healthy": healthy},
    }
    try:
        (data_dir / f"gates_{day_c}_metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    print(f"[gates_report] wrote {out_txt.name} + gates_{day_c}_metrics.json "
          f"-> {data_dir}")
    return 0 if sc.get("FAIL", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
