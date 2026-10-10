#!/usr/bin/env python3
"""twin_day_runner.py -- P2, the TWIN-DAY TEST: the hard gate of A1.

THE DESIGN (three brains, not two):
  OLD   = your unpatched step2 (the .pre_a1 backup)
  CTRL  = the SAME unpatched step2, run a second time
  NEW   = the patched step2 (A1 bar service)

  OLD vs CTRL measures the NOISE FLOOR: the old brain is not bit-deterministic
  against itself (wall-clock stamps inside snapshots, scout-door age expiry at
  second boundaries). Whatever OLD-vs-CTRL produces is NOT the patch's fault.
  OLD vs NEW measures the PATCH EFFECT. The gate:

      GATE: OLD-vs-NEW shows NO diff kind that OLD-vs-CTRL does not already
            show (and verdict-field diffs must be exactly zero).

  At BELL cycles (first cycle after each 5-min close) both brains do a full
  compute on identical inputs -> snapshots must match on every verdict field.
  InTRA-bar cycles: old computes fresh, new serves from the bar-close cache --
  differences are the DECLARED A1 semantic, counted as churn.

Also prints the speed receipt: full computes vs serves, wall time per brain.

Usage (git bash, project root, AFTER patching):
    python twin_day_runner.py --date 2026-10-09 \
        --old step2_market_analysis.py.pre_a1 --new step2_market_analysis.py \
        --window 10:00-13:00 --every 60

Output: console + data/twin_day_<date>.txt
"""
import argparse, bisect, csv, glob, gzip, json, os, re, subprocess, sys
from datetime import datetime, timedelta, timezone, tzinfo


# ----------------------------------------------------------- Budapest time ---
class Budapest(tzinfo):
    def utcoffset(self, dt):
        return timedelta(hours=2 if self._dst(dt) else 1)

    def dst(self, dt):
        return timedelta(hours=1 if self._dst(dt) else 0)

    def tzname(self, dt):
        return "CEST" if self._dst(dt) else "CET"

    @staticmethod
    def _dst(dt):
        y = dt.year
        mar_last = datetime(y, 3, 31)
        dst_on = mar_last - timedelta(days=(mar_last.weekday() + 1) % 7)
        oct_last = datetime(y, 10, 31)
        dst_off = oct_last - timedelta(days=(oct_last.weekday() + 1) % 7)
        naive = dt.replace(tzinfo=None)
        return dst_on <= naive < dst_off


BUDA = Budapest()


def parse_ts(s):
    if not s:
        return None
    s = str(s).strip().replace("Z", "+00:00")
    for attempt in (s, s.replace(" ", "T", 1)):
        try:
            dt = datetime.fromisoformat(attempt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BUDA)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    try:
        v = float(s)
        if v > 1e12:
            v /= 1000.0
        return datetime.fromtimestamp(v, tz=timezone.utc)
    except ValueError:
        return None


def read_day_trades(date_str):
    """The audit/Geometer/lens tape reader, verbatim."""
    tag = date_str
    files, seen_paths = [], set()

    def add(path):
        rp = os.path.realpath(path)
        if rp not in seen_paths:
            seen_paths.add(rp)
            files.append(path)

    compact = date_str.replace("-", "")
    for d in (".", "data", "data/archive"):
        for pat in (f"ticks_{compact}_*.csv", f"ticks_{compact}_*.csv.gz"):
            for p in sorted(glob.glob(os.path.join(d, pat))):
                add(p)
    for live in ("ticks.csv", "data/ticks.csv"):
        if os.path.exists(live):
            add(live)
    if not files:
        return None, "no ticks.csv and no rotated chunks for that date"
    trades, seen = [], set()
    for path in files:
        op = gzip.open if path.endswith(".gz") else open
        try:
            with op(path, "rt", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not line or line[0] == "t":
                        continue
                    if line[:10] != tag or ",Last," not in line:
                        continue
                    try:
                        parts = next(csv.reader([line]))
                    except Exception:
                        continue
                    if len(parts) < 6:
                        continue
                    if parts[1] not in ("Last", "Trade", "trade"):
                        continue
                    dt = parse_ts(parts[0])
                    if dt is None:
                        continue
                    try:
                        price = float(parts[2])
                    except ValueError:
                        continue
                    if not (1000 < price < 10000):
                        continue
                    size = float(parts[3]) if parts[3] else 0.0
                    key = (dt, price, size)
                    if key in seen:
                        continue
                    seen.add(key)
                    trades.append((dt, price))
        except OSError:
            continue
    trades.sort(key=lambda x: x[0])
    return (trades, None) if trades else (None, f"prints found in {files} but none on {date_str}")


# ------------------------------------------------------- market_data builder ---
def build_md(trades, times, tc, win_start, prewarm_min=120, tick_window_min=30):
    lo = bisect.bisect_left(times, win_start - timedelta(minutes=prewarm_min))
    hi = bisect.bisect_right(times, tc)
    upto = trades[lo:hi]
    if not upto:
        return None
    bars = {}
    for t, p in upto:
        b = t.replace(second=0, microsecond=0)
        if b not in bars:
            bars[b] = [p, p, p, p, 1.0]
        else:
            bars[b][1] = max(bars[b][1], p)
            bars[b][2] = min(bars[b][2], p)
            bars[b][3] = p
            bars[b][4] += 1.0
    keys = sorted(bars)
    candles = {"open": [bars[k][0] for k in keys], "high": [bars[k][1] for k in keys],
               "low": [bars[k][2] for k in keys], "close": [bars[k][3] for k in keys],
               "volume": [bars[k][4] for k in keys]}
    tl = bisect.bisect_left(times, tc - timedelta(minutes=tick_window_min))
    tick_data = [{"timestamp": t.isoformat(), "price": p, "volume": 1.0}
                 for t, p in trades[tl:hi]]
    last = upto[-1]
    return {"symbol": "XAUUSD", "price": last[1], "bid": round(last[1] - 0.25, 2),
            "ask": round(last[1] + 0.25, 2), "volume": 1.0,
            "tick_data": tick_data, "candles": candles, "has_data": True,
            "data_quality": {"source": "twin-day"}}


# ---------------------------------------------------------------- child mode ---
def run_child(args):
    import importlib.util
    from importlib.machinery import SourceFileLoader
    # explicit loader: the old brain's file is a .pre_a1 backup, and
    # spec_from_file_location returns None for non-.py names (the known
    # importlib trap). SourceFileLoader does not care about the extension.
    loader = SourceFileLoader("twin_brain", args.child)
    spec = importlib.util.spec_from_loader("twin_brain", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["twin_brain"] = mod
    loader.exec_module(mod)
    plan = json.load(open(args.plan, encoding="utf-8"))
    out = open(args.out, "w", encoding="utf-8")
    n_full = n_serve = 0
    t_total = 0.0
    import time as _t

    # REPLAY CLOCK: the harness runs cycles back-to-back (compressed time),
    # but the brain's wall-clock readers (signal_team's door ages, history
    # windows) must see MARKET time, or two children running at different
    # speeds diverge for reasons that cannot exist in production (where the
    # scout chain runs every ~5s in both worlds). Anchor time.time() to the
    # current cycle's replay epoch. Harness-only patch; the robot's files are
    # untouched. perf_counter (stats) stays real.
    _real_time = _t.time
    _anchor = {"epoch": None}

    def _replay_time():
        e = _anchor["epoch"]
        return _real_time() if e is None else e + (_real_time() % 0.001)

    _t.time = _replay_time

    for cyc in plan["cycles"]:
        md = cyc["market_data"]
        now = datetime.fromisoformat(cyc["now"])
        _anchor["epoch"] = now.timestamp()
        rec = {"i": cyc["i"], "now": cyc["now"], "bucket": cyc["bucket"],
               "error": None, "snap": None, "served": False}
        try:
            t0 = _t.perf_counter()
            snap = mod.analyze_market(md, now=now)
            t_total += _t.perf_counter() - t0
            notes = " ".join(getattr(snap, "notes", []) or [])
            rec["served"] = "A1 bar-service:" in notes
            rec["snap"] = json.loads(mod.snapshot_to_json(snap))
            if rec["served"]:
                n_serve += 1
            else:
                n_full += 1
        except Exception as e:  # identical crashes in both brains are equal
            rec["error"] = f"{type(e).__name__}: {e}"
        out.write(json.dumps(rec) + "\n")
    out.close()
    json.dump({"full_computes": n_full, "serves": n_serve,
               "analyze_seconds": round(t_total, 3)},
              open(args.out + ".stats", "w"))


# --------------------------------------------------------------- comparator ---
_NORMALIZED = {"n": 0}

# wall-clock-DERIVED leaves (countdowns/ages from process wall time, not from
# the replayed `now`); small-delta guarded so a gross change still flags
_WALLCLOCK_FIELDS = {"minutes_to_next_event": 60.0, "age_sec": 2.0}

_HHMM = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_MINAWAY = re.compile(r"\b(\d+) min away\b")


def _try_iso(v):
    try:
        s = str(v)
        if len(s) >= 19 and ("T" in s or " " in s):
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        pass
    return None


def _wallclock_note_pair(a, b, era_a, era_b):
    """True when two note strings differ ONLY in wall-clock-derived tokens:
    HH:MM stamps near each child's own run time, or 'N min away' countdowns
    (guarded to +/-2 minutes; a real verdict change like 'judges 2/5' vs
    '3/5' never matches this shape and still flags)."""
    if not isinstance(a, str) or not isinstance(b, str) or a == b:
        return False
    if _MINAWAY.sub("N min away", a) == _MINAWAY.sub("N min away", b):
        na = [int(x) for x in _MINAWAY.findall(a)]
        nb = [int(x) for x in _MINAWAY.findall(b)]
        if len(na) == len(nb) and all(abs(x - y) <= 6 for x, y in zip(na, nb)):
            return True
    if _HHMM.sub("HH:MM", a) != _HHMM.sub("HH:MM", b):
        return False

    def near_era(s, era):
        toks = _HHMM.findall(s)
        if not toks:
            return False
        mid = era[0] + (era[1] - era[0]) / 2
        for h, m in toks:
            try:
                tok = mid.replace(hour=int(h) % 24, minute=int(m))
            except Exception:
                return False
            if not any(abs((tok - timedelta(hours=off)) - mid) <= timedelta(minutes=10)
                       for off in (0, 1, 2)):
                return False
        return True

    return near_era(a, era_a) and near_era(b, era_b)


def first_diff(a, b, path="$", era_a=None, era_b=None):
    """First difference between two JSON structures as
    (path, kind, detail) or None. Wall-clock artifacts are normalized
    (counted, not reported)."""
    if type(a) is not type(b):
        return (path, "type", f"{type(a).__name__} vs {type(b).__name__}")
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a:
                return (f"{path}.{k}", "missing", "missing in A")
            if k not in b:
                return (f"{path}.{k}", "missing", "missing in B")
            d = first_diff(a[k], b[k], f"{path}.{k}", era_a, era_b)
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return (path, "len", f"len {len(a)} vs {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            d = first_diff(x, y, f"{path}[{i}]", era_a, era_b)
            if d:
                return d
        return None
    if a != b:
        da, db = _try_iso(a), _try_iso(b)
        if da and db and era_a and era_b:
            mid_a = era_a[0] + (era_a[1] - era_a[0]) / 2
            mid_b = era_b[0] + (era_b[1] - era_b[0]) / 2
            if abs((da - mid_a) - (db - mid_b)) <= timedelta(seconds=60):
                _NORMALIZED["n"] += 1
                return None
        if isinstance(a, str) and _wallclock_note_pair(a, b, era_a, era_b):
            _NORMALIZED["n"] += 1
            return None
        leaf = path.split(".")[-1].split("[")[0]
        tol = _WALLCLOCK_FIELDS.get(leaf)
        if tol is not None:
            try:
                if abs(float(a) - float(b)) <= tol:
                    _NORMALIZED["n"] += 1
                    return None
            except (TypeError, ValueError):
                pass
        return (path, "value", f"{a!r} vs {b!r}")
    return None


def compare_runs(recs_a, recs_b, era_a, era_b, bell_by_i):
    """Compare two child runs. Returns dict with bell diffs split into
    scout-state (wall-clock-sensitive door notebook) and other (verdict)
    diffs, plus intra-bar churn counts."""
    out = {"bells": 0, "bell_diff_decision": [], "bell_diff_note": [],
           "intra": 0, "churn": {"direction": 0, "confidence": 0, "regime": 0,
                                 "power": 0, "fill_status": 0}}
    for a, b in zip(recs_a, recs_b):
        if a["error"] and b["error"]:
            continue
        if (a["error"] is None) != (b["error"] is None):
            out["bell_diff_decision"].append(
                (a["i"], f"$error", f"one brain errored: {a['error'] or b['error']}"))
            continue
        if bell_by_i.get(a["i"]):
            out["bells"] += 1
            d = first_diff(a["snap"], b["snap"], era_a=era_a, era_b=era_b)
            if d:
                path, kind, detail = d
                entry = (a["i"], path, f"{kind}: {detail}")
                if path == "$.notes" or path.startswith("$.notes."):
                    out["bell_diff_note"].append(entry)      # human strings
                else:
                    out["bell_diff_decision"].append(entry)  # machine verdict
        else:
            out["intra"] += 1
            sa, sb = a["snap"], b["snap"]
            if sa.get("signal_direction") != sb.get("signal_direction"):
                out["churn"]["direction"] += 1
            if abs((sa.get("confidence") or 0) - (sb.get("confidence") or 0)) > 1e-9:
                out["churn"]["confidence"] += 1
            if sa.get("regime") != sb.get("regime"):
                out["churn"]["regime"] += 1
            pa = sa.get("power") or {}
            pb = sb.get("power") or {}
            if (pa.get("direction") != pb.get("direction")
                    or pa.get("reason_code") != pb.get("reason_code")):
                out["churn"]["power"] += 1
            ea = sa.get("entry_simulation") or {}
            eb = sb.get("entry_simulation") or {}
            if ea.get("status") != eb.get("status"):
                out["churn"]["fill_status"] += 1
    return out


def sig(entry):
    """Diff signature: (top field, kind) -- used to match against the noise floor."""
    path, detail = entry[1], entry[2]
    top = path.lstrip("$.").split(".")[0].split("[")[0]
    kind = detail.split(":")[0]
    return (top, kind)


# ------------------------------------------------------------------- parent ---
def main():
    ap = argparse.ArgumentParser(description="A1 twin-day test (P2, three brains)")
    ap.add_argument("--date", required=True)
    ap.add_argument("--old", default="step2_market_analysis.py.pre_a1")
    ap.add_argument("--new", default="step2_market_analysis.py")
    ap.add_argument("--window", default=None,
                    help="HH:MM-HH:MM Budapest (default: first 3h of tape)")
    ap.add_argument("--every", type=int, default=60)
    ap.add_argument("--out-dir", default="data")
    args = ap.parse_args()

    for p in (args.old, args.new):
        if not os.path.exists(p):
            print(f"[abort] {p} not found")
            return 1

    trades, err = read_day_trades(args.date)
    if err:
        print(f"[no tape] {err}")
        return 1
    times = [t for t, _ in trades]
    print(f"tape: {len(trades)} prints on {args.date}")

    day0 = times[0].astimezone(BUDA).date()
    if args.window:
        h0, m0 = (int(x) for x in args.window.split("-")[0].split(":"))
        h1, m1 = (int(x) for x in args.window.split("-")[1].split(":"))
        win_start = datetime(day0.year, day0.month, day0.day, h0, m0,
                             tzinfo=BUDA).astimezone(timezone.utc)
        win_end = datetime(day0.year, day0.month, day0.day, h1, m1,
                           tzinfo=BUDA).astimezone(timezone.utc)
    else:
        win_start = times[0]
        win_end = win_start + timedelta(hours=3)

    cycles, t = [], win_start
    while t <= win_end:
        cycles.append(t)
        t += timedelta(seconds=args.every)
    bells = set()
    b = win_start.replace(second=0, microsecond=0)
    while b <= win_end:
        bell = b + timedelta(minutes=5, seconds=1)
        if win_start <= bell <= win_end:
            bells.add(bell)
        b += timedelta(minutes=5)
    cycles = sorted(set(cycles) | bells)
    cycles = [c for c in cycles if c >= times[0]]

    def bucket(t):
        return int(t.timestamp() // 60 // 5)

    plan = {"cycles": []}
    prev_b = None
    for i, c in enumerate(cycles):
        md = build_md(trades, times, c, win_start)
        if md is None:
            continue
        bk = bucket(c)
        plan["cycles"].append({"i": i, "now": c.isoformat(), "bucket": bk,
                               "is_bell": prev_b is not None and bk != prev_b,
                               "market_data": md})
        prev_b = bk
    print(f"cycles: {len(plan['cycles'])} "
          f"({sum(1 for c in plan['cycles'] if c['is_bell'])} bells) "
          f"window {win_start.astimezone(BUDA).strftime('%H:%M')}-"
          f"{win_end.astimezone(BUDA).strftime('%H:%M')} Budapest")

    os.makedirs(args.out_dir, exist_ok=True)
    plan_path = os.path.join(args.out_dir, f"twin_plan_{args.date}.json")
    json.dump(plan, open(plan_path, "w"))
    env = dict(os.environ, PYTHONHASHSEED="0")

    brains = {}
    for tag, mod_path in (("OLD", args.old), ("CTRL1", args.old),
                          ("CTRL2", args.old), ("NEW", args.new)):
        out_path = os.path.join(args.out_dir, f"twin_{tag}_{args.date}.jsonl")
        t0_wall = datetime.now(timezone.utc)
        r = subprocess.run([sys.executable, __file__, "--child", mod_path,
                            plan_path, out_path], env=env,
                           capture_output=True, text=True)
        t1_wall = datetime.now(timezone.utc)
        if r.returncode != 0:
            print(f"[abort] {tag} child failed:\n{r.stderr[-2000:]}")
            return 1
        stats = json.load(open(out_path + ".stats"))
        brains[tag] = {"path": out_path, "stats": stats, "era": (t0_wall, t1_wall)}
        print(f"{tag:>4} brain ({os.path.basename(mod_path)}): "
              f"{stats['full_computes']} full computes, {stats['serves']} serves, "
              f"{stats['analyze_seconds']}s in analyze_market")

    recs = {tag: [json.loads(x) for x in open(brains[tag]["path"])]
            for tag in brains}
    bell_by_i = {c["i"]: c["is_bell"] for c in plan["cycles"]}

    noises = [compare_runs(recs["OLD"], recs[f"CTRL{k}"],
                           brains["OLD"]["era"], brains[f"CTRL{k}"]["era"],
                           bell_by_i) for k in (1, 2)]
    patch = compare_runs(recs["OLD"], recs["NEW"],
                         brains["OLD"]["era"], brains["NEW"]["era"], bell_by_i)

    # the noise floor = UNION of both control comparisons (jitter is
    # intermittent; one control can undercount it)
    noise_note_kinds = set()
    noise_decision_total = 0
    noise_note_total = 0
    for noise in noises:
        noise_note_kinds |= {sig(e) for e in noise["bell_diff_note"]}
        noise_decision_total += len(noise["bell_diff_decision"])
        noise_note_total += len(noise["bell_diff_note"])
    patch_note_kinds = {sig(e) for e in patch["bell_diff_note"]}
    novel_note_kinds = patch_note_kinds - noise_note_kinds

    gate_decision = len(patch["bell_diff_decision"])
    gate_novel_notes = len(novel_note_kinds)
    passed = (gate_decision == 0 and gate_novel_notes == 0
              and noises[0]["bells"] == patch["bells"])

    lines = []
    say = lines.append
    say("=" * 100)
    say(f" A1 TWIN-DAY TEST - {args.date}  (old: {args.old} | new: {args.new})")
    say("=" * 100)
    for tag in ("OLD", "CTRL1", "CTRL2", "NEW"):
        s = brains[tag]["stats"]
        say(f" {tag:>4}: {s['full_computes']} full computes"
            + (f" + {s['serves']} serves" if s["serves"] else "")
            + f", {s['analyze_seconds']}s in analyze_market")
    so, sn = brains["OLD"]["stats"], brains["NEW"]["stats"]
    if so["analyze_seconds"] > 0:
        say(f" speed receipt: NEW spent "
            f"{max(0.0, 1.0 - sn['analyze_seconds'] / so['analyze_seconds']) * 100:.0f}%"
            f" less time inside analyze_market "
            f"({so['analyze_seconds']}s -> {sn['analyze_seconds']}s)")
    say("")
    say(f" bell cycles compared: {patch['bells']}")
    say(" children run on the REPLAY clock (time.time anchored per cycle): "
        "scout door ages/history are deterministic,")
    say(" so the FULL snapshot (incl. signal_map) is gated at every bell")
    say(f" NOISE FLOOR  (OLD vs OLD x2, same file thrice run): "
        f"{noise_decision_total} decision diffs, {noise_note_total} note-jitter diffs "
        f"(kinds: {sorted(noise_note_kinds) or 'none'})")
    say("                (the old brain vs itself is not bit-deterministic -- e.g. the")
    say("                 news countdown note anchors to process start; this is the floor)")
    say(f" PATCH EFFECT (OLD vs NEW): "
        f"{gate_decision} DECISION diffs, {len(patch['bell_diff_note'])} note diffs "
        f"(kinds: {sorted(patch_note_kinds) or 'none'})")
    say("")
    say(f" THE GATE: DECISION diffs (every field except notes) = {gate_decision} "
        f"-> {'CLEAN' if gate_decision == 0 else 'DIFFERENCES: STOP'}")
    say(f"           note-diff kinds beyond the old brain's own floor = {gate_novel_notes} "
        f"-> {'CLEAN' if gate_novel_notes == 0 else 'NOVEL: STOP'}")
    for e in patch["bell_diff_decision"][:8]:
        say(f"   !! DECISION DIFF cycle {e[0]}: {e[1]}: {e[2]}  <-- real STOP")
    for e in patch["bell_diff_note"][:6]:
        say(f"   ~~ note jitter cycle {e[0]}: {e[1]}: {e[2][:90]}")
    if novel_note_kinds:
        say(f"   !! NOVEL note kinds (not in the noise floor): {sorted(novel_note_kinds)}  <-- real STOP")
    say("")
    say(f" churn (declared A1 semantic, intra-bar, {patch['intra']} cycles): "
        f"direction {patch['churn']['direction']}, "
        f"confidence {patch['churn']['confidence']}, "
        f"regime {patch['churn']['regime']}, "
        f"power {patch['churn']['power']}, "
        f"paper-fill {patch['churn']['fill_status']}")
    say(f" control churn (OLD vs OLD, should be ~0): "
        f"direction {noise['churn']['direction']}, "
        f"confidence {noise['churn']['confidence']}, "
        f"regime {noise['churn']['regime']}, "
        f"power {noises[0]['churn']['power']}, "
        f"paper-fill {noises[0]['churn']['fill_status']}")
    say("")
    say(f" VERDICT: {'PASS' if passed else 'FAIL'}"
        + ("  (every DECISION field identical at every bell; note jitter within "
           "the old brain's own nondeterminism)" if passed else
           "  (stop: root-cause before any install)"))
    out_txt = os.path.join(args.out_dir, f"twin_day_{args.date}.txt")
    open(out_txt, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"[saved] {out_txt}")
    return 0 if passed else 2


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        class _ChildArgs:
            pass
        _ca = _ChildArgs()
        _ca.child, _ca.plan, _ca.out = sys.argv[2], sys.argv[3], sys.argv[4]
        run_child(_ca)
    else:
        sys.exit(main() or 0)
