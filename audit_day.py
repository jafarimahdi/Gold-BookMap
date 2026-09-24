#!/usr/bin/env python3
"""
audit_day.py  --  "How did the robot behave on a given day?"
=================================================================

One command, 10 tests, plain-English answers.

    python audit_day.py                  # yesterday
    python audit_day.py --date 2026-09-22
    python audit_day.py --date 2026-09-22 --out daily_audit.txt

Every test prints:
    [PASS]/[WARN]/[FAIL]  <what it measures>  ->  what happened  ->  what it means

Grades are strict on purpose: a day where the robot never saw real data must
NOT look like a successful day.

No dependencies beyond the standard library. Python 3.8+.
"""
from __future__ import annotations

import argparse
import csv
import contextlib
import io
import json
import math
import os
import re
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, date as date_cls, timedelta, timezone, tzinfo
from pathlib import Path

# ROOT is the project folder. Default = the folder holding this file; override
# with --dir /a/gitHub/Gold-BookMap so the auditor can live anywhere (Downloads).
ROOT = Path(__file__).resolve().parent


def _root():
    return ROOT


def BASE_DIR():
    return ROOT


def DATA_DIR():
    return _root() / "data"


def LOGS_DIR():
    return _root() / "logs"

# ---------------------------------------------------------------- settings ---
# Everything can be overridden from .env / environment, defaults = v7.0-P3.

# BUILD MARKER - the shell scripts check for THIS string instead of a byte count,
# so an intentional edit can never make morning_check.sh yell "re-copy it from the
# kit" at a perfectly good file. Bump the date whenever you ship a new auditor.
BUILD = "audit-2026-09-24j"
BASE_DIR_FOR_ENV = Path(__file__).resolve().parent


ENV_DUPLICATES: dict = {}
ENV: dict = {}


def _load_dotenv(env_file=None):
    """Parse .env the way config.py does, so the audit grades the numbers that
    were REALLY in force - not the ones you meant to set.

    config.py's loader assigns each key as it walks the file, so the LAST
    occurrence wins (verified against both python-dotenv override=True and the
    no-dotenv fallback). Every earlier value is dead weight. We record them so
    test 11 can warn you the file is ambiguous.
    """
    global ENV_DUPLICATES
    seen: dict = {}
    dups: dict = {}
    path = Path(env_file) if env_file else None
    if path is None:
        for name in (".env", ".env.local"):
            if (BASE_DIR_FOR_ENV / name).exists():
                path = BASE_DIR_FOR_ENV / name
                break
    if path is None or not path.exists():
        ENV_DUPLICATES = {}
        return
    try:
        for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            value = line.split("=", 1)[1]
            if "#" in value:                       # same inline-comment rule as config.py
                cut = len(value)
                for i, ch in enumerate(value):
                    if ch == "#" and i > 0 and value[i - 1] in (" ", "\t"):
                        cut = i
                        break
                value = value[:cut]
            key = line.split("=", 1)[0].strip()
            if key.startswith("export "):
                key = key[7:].strip()
            value = value.strip().strip('"').strip("'")
            if not key:
                continue
            if key in seen:
                dups.setdefault(key, [seen[key]]).append(value)
            seen[key] = value            # last occurrence wins, exactly like config.py
    except Exception:
        pass
    ENV_DUPLICATES = dups
    ENV.update(seen)
    for k, v in seen.items():
        os.environ[k] = v                # mirror the file into the env so every
                                         # _envf() below sees the ACTIVE value


ENV_FILE_USED = None


ENV_FILE_USED = None
_load_dotenv()


def _env(name, default):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


def _envf(name, default):
    try:
        return float(_env(name, default))
    except Exception:
        return float(default)


ATR = _envf("AUDIT_ATR", 3.18)                 # M5 ATR of gold, points
SPREAD = _envf("AUDIT_SPREAD", 0.50)           # CFD round-trip cost, points
SL_MULT = _envf("STOP_LOSS_ATR_MULT", 2.0)
TP_MULT = _envf("TAKE_PROFIT_ATR_MULT", 3.5)
MAX_HOLD_CANDLES = int(_envf("AUDIT_MAX_HOLD_MIN", 180) / 5.0) or 36
WINDOW_START_H = int(_env("BUDAPEST_START", "08:00").split(":")[0])
WINDOW_END_H = int(_env("BUDAPEST_END", "23:00").split(":")[0])
AI_MIN = _envf("AI_MIN_SIGNAL_STRENGTH", 6.0)
CONF_MIN = _envf("CONFIDENCE_THRESHOLD", 50.0)
AI_MAX_CALLS = int(_envf("AI_MAX_CALLS_PER_DAY", 2000))
LATENCY_TARGET_MS = 500.0
def TICKS_CANDIDATES():
    e = _env("BOOKMAP_BRIDGE_FILE", "")
    return [Path(e) if e else None, _root() / "ticks.csv", DATA_DIR() / "ticks.csv"]


def MBO_CANDIDATES():
    e = _env("BOOKMAP_MBO_FILE", "")
    return [Path(e) if e else None, _root() / "mbo.csv", DATA_DIR() / "mbo.csv"]

OUT = []          # collected report lines
import datetime as _dt_mod
EPOCH = _dt_mod.datetime(1970, 1, 1, tzinfo=_dt_mod.timezone.utc)


METRICS: dict = {}


def say(line=""):
    print(line)
    OUT.append(line)


def rule(char="="):
    say(char * 108)


# ------------------------------------------------------------ small utils ---
class Budapest(tzinfo):
    """Europe/Budapest, DST-aware without needing zoneinfo/tzdata packages."""

    def utcoffset(self, dt):
        return timedelta(hours=2 if self._dst(dt) else 1)

    def dst(self, dt):
        return timedelta(hours=1) if self._dst(dt) else timedelta(0)

    def tzname(self, dt):
        return "CEST" if self._dst(dt) else "CET"

    @staticmethod
    def _dst(dt):
        y = dt.year
        # last Sunday of March 02:00 local -> first Sunday of October 03:00 local
        mar_last = datetime(y, 3, 31)
        dst_on = mar_last - timedelta(days=(mar_last.weekday() + 1) % 7)
        oct_first = datetime(y, 10, 1)
        dst_off = oct_first + timedelta(days=(6 - oct_first.weekday()) % 7)
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
                # Raw BookMap addon timestamps are written in broker/machine
                # local time (Budapest). Stamp them, don't guess twice.
                dt = dt.replace(tzinfo=BUDA)
            return dt.astimezone(timezone.utc)
        except Exception:
            continue
    try:                                            # epoch seconds / ms
        v = float(s)
        if v > 1e12:
            v /= 1000.0
        return datetime.fromtimestamp(v, tz=timezone.utc)
    except Exception:
        return None


def on_day(dt, day):
    """Is this stamp on `day` in BUDAPEST?  One day = one calendar day in YOUR timezone.

    Stamps arrive in three shapes: naive local (main.py's decisions_log.csv), aware UTC
    (the diary), and epoch.  Converting every one to Budapest before comparing is the
    only way the tape, the decisions and the diary agree on which day a row belongs to -
    they used to compare UTC dates, which silently moved every row between 00:00 and
    02:00 local into yesterday, and dropped tape prints in the same window entirely.
    """
    try:
        return bool(dt) and dt.astimezone(BUDA).date() == day
    except Exception:
        return False


def _sess_story(text):
    """The robot's own morning-to-night story (build 24f writes SESSION START/END)."""
    s = re.search(r"^.*?SESSION START\b[^\n]*", text or "", re.M)
    e = re.search(r"^.*?SESSION END\b[^\n]*", text or "", re.M)

    def _clean(line):
        m = re.search(r"(\d{2}:\d{2}:\d{2})", line or "")
        tail = line.split("|", 1)[1].strip() if "|" in (line or "") else ""
        return (m.group(1) if m else "?"), tail

    out = []
    if s and e:
        t0, d0 = _clean(s.group(0))
        t1, d1 = _clean(e.group(0))
        out.append(f"the robot recorded its own session: START {t0}  |  {d0}")
        out.append(f"                                    END   {t1}  |  {d1}")
        out.append("clean start and stop -> the day has a beginning and an end, not just a gap")
    elif s:
        t0, d0 = _clean(s.group(0))
        out.append(f"the robot recorded START ({t0} | {d0}) but NO SESSION END line -> the process "
                   f"was killed, the machine slept, or it is still running")
    else:
        out.append("no SESSION START/END lines in this log -> build older than 24f; the log's own "
                   "first/last lines above are all there is")
    return out


def hhmm(dt):
    return dt.astimezone(BUDA).strftime("%H:%M") if dt else "??"


def pct(a, b):
    return (100.0 * a / b) if b else 0.0


def fmt(x, nd=1):
    try:
        return f"{float(x):.{nd}f}"
    except Exception:
        return str(x)


class Report:
    def __init__(self, no="?"):
        self.rows = []          # (grade, test name, headline, why)
        self.no = no

    def add(self, grade, name, headline, why):
        g = {"PASS": "[PASS]", "WARN": "[WARN]", "FAIL": "[FAIL]",
             "NA": "[ -- ]"}[grade]
        self.rows.append((grade, name, headline, why))
        rule("-")
        say(f"{g}  TEST {self.no:>2}  {name}")
        say(f"        measures : {headline}")
        for i, w in enumerate(why):
            say(f"        {'->' if i == 0 else '   '} {w}")

    def score(self):
        c = Counter(r[0] for r in self.rows)
        return c


# ------------------------------------------------------------- data loaders --
TAPE_DUPLICATES = 0          # rows seen twice across sources and dropped; test 4 prints it
DAY_COUNTS = {}              # rows loaded for the graded day, so tests can state "for THIS day"


def find_ticks_file():
    for p in TICKS_CANDIDATES():
        if p and Path(p).exists():
            return Path(p)
    return None


def all_ticks_files(day=None):
    """Every ticks file that could hold `day`, newest first.

    The bridge rotates ticks.csv at BOOKMAP_ROTATE_MB (default 200 MB) into
    sibling chunks named ticks_YYYYMMDD_HHMMSS.csv, and the archiver may gzip
    them. A busy gold day is ~1 GB, so reading only ticks.csv silently grades
    the last few hours and calls the rest of the day 'thin'.
    """
    seen, out = set(), []
    prim = find_ticks_file()
    if prim:
        seen.add(str(prim.resolve())); out.append(prim)
    dirs = [prim.parent if prim else _root(), _root(), DATA_DIR(),
            DATA_DIR() / "archive"]          # the bridge gzips rotated chunks in there
    for d in dirs:
        if not d or not Path(d).exists():
            continue
        for pat in ("ticks_*.csv", "ticks_*.csv.gz"):
            for f in sorted(Path(d).glob(pat), reverse=True):
                if day is not None and not f.name.startswith(f"ticks_{day:%Y%m%d}"):
                    continue                      # chunks from other days are noise
                if str(f.resolve()) in seen:
                    continue
                seen.add(str(f.resolve())); out.append(f)
    return out


def find_mbo_file():
    for p in MBO_CANDIDATES():
        if p and Path(p).exists():
            return Path(p)
    return None


def all_mbo_files(day=None):
    """mbo.csv plus any rotated chunks (data/archive/mbo_YYYYMMDD_HHMMSS.csv[.gz]).

    Same story as ticks: the bridge rotates mbo.csv at ~100 MB and gzips the
    chunk into data/archive/, so reading only the live file under-counts the
    order-by-order footprint for every day but the last few hours.
    """
    seen, out = set(), []
    prim = find_mbo_file()
    if prim:
        seen.add(str(prim.resolve())); out.append(prim)
    for d in [prim.parent if prim else _root(), _root(), DATA_DIR(), DATA_DIR() / "archive"]:
        if not d or not Path(d).exists():
            continue
        for pat in ("mbo_*.csv", "mbo_*.csv.gz"):
            for f in sorted(Path(d).glob(pat), reverse=True):
                if day is not None and not f.name.startswith(f"mbo_{day:%Y%m%d}"):
                    continue
                if str(f.resolve()) in seen:
                    continue
                seen.add(str(f.resolve())); out.append(f)
    return out


def list_days(deep=False):
    """What can be audited, and where the data lives. Nothing is graded.

    Cheap mode scans logs + counts rows in decisions/diary (small files). Deep
    mode also streams every ticks file, so it can tell you how many trade
    prints survive for days whose tape lives only in data/archive/.
    """
    import gzip
    tag_re = re.compile(r"(20\d\d-\d\d-\d\d)")
    have = {}

    def rec(tag):
        return have.setdefault(tag, {"log": 0, "prints": 0, "mbo": 0,
                                     "decisions": 0, "diary": 0, "srcs": set()})

    for kind, fn in (("ticks", all_ticks_files), ("mbo", all_mbo_files)):
        for f in fn(None):
            if not f or not Path(f).exists():
                continue
            tags = {tag_re.search(n).group(1) for n in [f.name] if tag_re.search(n)}
            for m in tag_re.findall(f.read_text(encoding="utf-8", errors="ignore")[:0] or ""):
                tags.add(m)
            # a live file can hold several days: discover them by sampling
            try:
                opener = gzip.open if str(f).endswith(".gz") else open
                with opener(f, "rt", encoding="utf-8", errors="ignore") as fh:
                    for i, line in enumerate(fh):
                        if i > 400000:
                            break
                        m = tag_re.match(line)
                        if m:
                            tags.add(m.group(1))
            except Exception:
                pass
            for tag in tags:
                rec(tag)["srcs"].add(f"{kind}:{'archive' if 'archive' in str(f) else 'live'}")

    for f in sorted(LOGS_DIR().glob("trading_*.log")) if LOGS_DIR().exists() else []:
        try:
            txt = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for tag in sorted(set(tag_re.findall(txt))):
            rec(tag)["log"] += sum(1 for l in txt.splitlines() if l.startswith(tag))

    for name, key in (("decisions_log.csv", "decisions"), ("snapshots_history.jsonl", "diary")):
        f = DATA_DIR() / name
        if f.exists():
            try:
                for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                    m = tag_re.search(line[:60])
                    if m:
                        rec(m.group(1))[key] += 1
            except Exception:
                pass

    if deep:
        for tag in sorted(have):
            d = _dt_mod.date.fromisoformat(tag)
            n, bad = 0, []
            for kind, fn in (("ticks", all_ticks_files), ("mbo", all_mbo_files)):
                for f in fn(d):
                    c = _scan_tag(f, tag, key=kind)
                    if c < 0:
                        bad.append(f.name)        # unreadable: count it as nothing, not as -1
                    else:
                        n += c
            have[tag]["prints"] = n
            if bad:
                have[tag]["bad"] = bad
    say("")
    say(" GRADEABLE DAYS  (log lines / prints+order-lines / decisions / diary, and where the data lives)")
    say(" " + "-" * 100)
    if not have:
        say("   nothing found in " + str(_root()))
        return 0
    for tag in sorted(have):
        d = have[tag]
        src = ", ".join(sorted(d["srcs"])) or "-"
        arch = "  <- reads rotated chunks in data/archive/" if any("archive" in x for x in d["srcs"]) else ""
        pr = f"{d['prints']:,}" if deep or d["prints"] else "-"
        say(f"   {tag}   log {d['log']:>7,}   tape {pr:>9}   decisions {d['decisions']:>6,}   "
            f"diary {d['diary']:>6,}   [{src}]{arch}")
        if d.get("bad"):
            say(f"      !! {len(d['bad'])} file(s) unreadable, their rows are NOT in that tape "
                f"number: " + ", ".join(d["bad"][:4]))
    if not deep:
        say("   add --deep to count trade prints and order lines per day (a few seconds per 200 MB chunk)")
    say("")
    return 0


def _scan_tag(f, tag, key="ticks"):
    """Count matching rows in one file; prints need ',Last,' too, mbo only the date."""
    import gzip
    opener = gzip.open if str(f).endswith(".gz") else open
    n = 0
    try:
        with opener(f, "rt", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                if not line.startswith(tag):
                    continue
                if key == "ticks" and ",Last," not in line:
                    continue
                n += 1
    except Exception:
        return -1
    return n


def log_text_for(day):
    """Return (log_path, text). Uses the newest trading log if the day's is gone."""
    p = LOGS_DIR() / f"trading_{day:%Y%m%d}.log"
    if p.exists():
        return p, p.read_text(encoding="utf-8", errors="ignore")
    cands = sorted(LOGS_DIR().glob("trading_*.log"), key=lambda q: q.stem, reverse=True)
    for c in cands:
        t = c.read_text(encoding="utf-8", errors="ignore")
        if f"{day:%Y-%m-%d}" in t:
            return c, t
    if cands:
        c = cands[0]
        return c, c.read_text(encoding="utf-8", errors="ignore")
    return None, ""


def load_ticks(day, max_lines=0):
    """Trade prints only ('Last'), for that day. Streams line by line.

    Streaming matters: a 12h window at depth 40 left ~2.4M rows / 200MB in
    ticks.csv. readlines() on that eats ~1GB of RAM. max_lines>0 is only a
    safety cap for the TAIL (most recent rows); 0 = read the whole file.
    """
    files = all_ticks_files(day)
    p = find_ticks_file() or (files[0] if files else None)
    out = []
    global TAPE_DUPLICATES
    TAPE_DUPLICATES = 0
    if not files:
        return out, None, 0
    scanned = 0
    # The same print can be visible through two sources (the live tape the .env points
    # at, plus a rotated chunk): summing them double-counts the day. Dedupe on the row
    # itself when more than one file is in play, with a hard cap so a 10M-row day can
    # never blow up the RAM of the audit.
    _seen = set() if len(files) > 1 else None
    tag = f"{day:%Y-%m-%d}"
    import gzip
    for path in files:
      try:
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8", errors="ignore") as f:
            lines = f
            if max_lines:
                lines = f.readlines()[-max_lines:]
            for line in lines:
                if not line or line[0] == "t":      # header
                    continue
                if line[:10] != tag or ",Last," not in line:
                    continue                          # cheap prefilter, no parsing
                try:
                    parts = next(csv.reader([line]))
                except Exception:
                    continue
                scanned += 1
                if len(parts) < 6:
                    continue
                if parts[1] not in ("Last", "Trade", "trade"):
                    continue
                dt = parse_ts(parts[0])
                if dt is None or not on_day(dt, day):
                    continue
                try:
                    price = float(parts[2])
                except Exception:
                    continue
                if not (1000 < price < 10000):
                    continue
                size = float(parts[3]) if parts[3] else 0.0
                if _seen is not None:
                    key = (dt, price, size)
                    if key in _seen:
                        TAPE_DUPLICATES += 1
                        continue
                    if len(_seen) < 2000000:
                        _seen.add(key)
                out.append((dt, price, size))
      except Exception as e:
        print(f"[warn] ticks read failed on {path.name}: {e}")
    out.sort(key=lambda x: x[0])
    return out, p, scanned


def load_mbo(day, max_lines=0, lo_min=None, hi_min=None):
    """Order-by-order counting for that day: live file plus every rotated chunk.

    Returns a dict:
      path      the primary file (the live one, whatever .env points at)
      total     all rows dated that day, every hour of the clock
      window    rows inside the trading window (08:00-23:00 local by default)
      extras    rotated chunks that contributed (or were looked at)
      capped_in the file a MBO_MAX_LINES limit ran out inside, if any
      bad       files that could not be read at all, with the reason

    Two traps this function used to fall into, both of them mine:
      1. a 2,000,000 line cap stopped the count silently. On a real gold day
         (2026-09-23) it was reached inside the overnight rows of mbo.csv while
         the trading hours still sat unread in data/archive/.
      2. the bridge rotates by RENAMING: mbo_20260923_211101.csv.gz holds
         everything up to 21:11, and the surviving mbo.csv holds everything
         AFTER it - i.e. mostly the COMEX night session, dated 'yesterday'. So
         the live file alone is not the trading day; the window matters.
    Counting is one integer per line, so a few million rows stream in seconds.
    """
    if not max_lines:
        max_lines = int(_env("MBO_MAX_LINES", "0") or 0)
    files = all_mbo_files(day)
    prim = find_mbo_file() or (files[0] if files else None)
    if not files:
        return {"path": None, "total": 0, "window": 0, "extras": [], "capped_in": None, "bad": []}
    if lo_min is None:
        lo_min, hi_min = WINDOW_START_H * 60, WINDOW_END_H * 60
    tag = f"{day:%Y-%m-%d}"
    total = win = 0
    capped_in, bad = None, []
    import gzip
    from datetime import datetime as _d
    for path in files:
        got = 0
        try:
            opener = gzip.open if str(path).endswith(".gz") else open
            with opener(path, "rt", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line[:10] == tag:             # prefix is enough to count
                        total += 1
                        got += 1
                        hh = line[11:13]
                        mm = line[14:16]
                        if len(hh) == 2 and len(mm) == 2 and hh.isdigit() and mm.isdigit():
                            try:
                                lt = _d.fromisoformat(line[:29]).astimezone(BUDA)
                                mins = lt.hour * 60 + lt.minute
                                if lo_min <= mins < hi_min:
                                    win += 1
                            except Exception:
                                pass
                        if max_lines and total >= max_lines:
                            capped_in = path.name
                            break
        except Exception as exc:
            bad.append(f"{path.name} ({type(exc).__name__}: {exc})")
            continue
        if capped_in:
            break
    return {"path": prim, "total": total, "window": win,
            "extras": [f for f in files[1:]], "capped_in": capped_in, "bad": bad}


def load_decisions(day):
    """decisions_log.csv rows for that day."""
    rows = []
    p = DATA_DIR() / "decisions_log.csv"
    if not p.exists():
        return rows, p
    with open(p, encoding="utf-8", errors="ignore", newline="") as f:
        for r in csv.DictReader(f):
            dt = parse_ts(r.get("timestamp", ""))
            if on_day(dt, day):
                r["_dt"] = dt
                for k in ("price", "signal_strength", "signal_confidence",
                          "divergence", "ai_confidence"):
                    try:
                        r[k] = float(r.get(k) or 0)
                    except Exception:
                        r[k] = 0.0
                rows.append(r)
    rows.sort(key=lambda r: r["_dt"])
    return rows, p


def load_diary(day):
    """data/snapshots_history.jsonl entries for that day (v4.3+ diary).

    The master file rotates at 50 MB keeping the last 20k lines, so a day older than
    that loses its judge panel. main.py (build 24f+) also writes a per-day mirror,
    data/diary_<yyyymmdd>.jsonl - when the master no longer holds the day, the mirror
    is read instead, and the report says so.
    """
    def _read(path):
        rows = []
        try:
            with open(path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    dt = parse_ts(str(o.get("ts") or o.get("time") or o.get("timestamp") or ""))
                    if on_day(dt, day):
                        o["_dt"] = dt
                        rows.append(o)
        except Exception as e:
            print(f"[warn] diary read failed on {path.name}: {e}")
        return rows

    p = DATA_DIR() / "snapshots_history.jsonl"
    out = _read(p) if p.exists() else []
    if not out:
        mirror = DATA_DIR() / f"diary_{day:%Y%m%d}.jsonl"
        if mirror.exists():
            out = _read(mirror)
            if out:
                print(f"[warn] the master diary holds no rows for {day:%Y-%m-%d} (it keeps only the "
                      f"last 20k lines once it passes 50 MB) -> read {mirror.name} instead")
                return out, mirror
    return out, p


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None


def build_candles(ticks):
    """M5 OHLC from a sorted tick list."""
    buckets = defaultdict(list)
    for dt, price, _sz in ticks:
        b = dt.replace(minute=(dt.minute // 5) * 5, second=0, microsecond=0)
        buckets[b].append(price)
    out = []
    for b in sorted(buckets):
        pr = buckets[b]
        out.append({"dt": b, "open": pr[0], "high": max(pr),
                    "low": min(pr), "close": pr[-1], "n": len(pr)})
    return out


def simulate(ticks, candles, entry_dt, direction, entry_price,
             max_candles=MAX_HOLD_CANDLES, sl_mult=None, tp_mult=None):
    """Walk forward on M5 candles. Returns points captured after spread."""
    if not candles:
        return None
    i = None
    for k, c in enumerate(candles):
        if c["dt"] >= entry_dt.replace(second=0, microsecond=0):
            i = k + 1
            break
    if i is None or i >= len(candles):
        return None
    seg = candles[i:i + max_candles]
    if not seg:
        return None
    slm = SL_MULT if sl_mult is None else sl_mult
    tpm = TP_MULT if tp_mult is None else tp_mult
    if direction == "BUY":
        sl = entry_price - slm * ATR
        tp = entry_price + tpm * ATR
        for c in seg:
            if c["low"] <= sl:
                return sl - entry_price - SPREAD
            if c["high"] >= tp:
                return tp - entry_price - SPREAD
        return seg[-1]["close"] - entry_price - SPREAD
    else:
        sl = entry_price + slm * ATR
        tp = entry_price - tpm * ATR
        for c in seg:
            if c["high"] >= sl:
                return entry_price - sl - SPREAD
            if c["low"] <= tp:
                return entry_price - tp - SPREAD
        return entry_price - seg[-1]["close"] - SPREAD



# ------------------------------------------------------------- judge panel ---
# Same table step2_market_analysis.parse_judge_panel() uses. Duplicated here on
# purpose: audit_day.py must run against a diary written by an older build, and
# it must not import trading code into a read-only report.
JUDGE_DEFAULT_W = {
    "footprint_delta": 1.0, "footprint_levels": 0.4, "l3_imbalance": 0.8,
    "l3_aggr_limit": 0.7, "l3_ofi_streak": 1.0, "l3_net_flow": 1.5,
    "l3_large_ofi": 0.6, "iceberg": 1.0, "iceberg_noise": 0.0,
    "iceberg_legacy": 0.6, "spoof_invert": 0.6, "spoof_invert_loose": 0.4,
    "whale_walls": 1.4, "queue_pos": 0.5, "microprice": 0.6,
    "absorption": 0.6, "sweep": 0.9, "vwap_trend": 0.6, "vwap_bands": 0.8,
    "vwap_zscore": 0.5, "poc_day": 0.5, "supply_demand": 0.6,
    "value_area": 0.5, "htf_poc": 0.7, "cvd_divergence": 0.6,
    "cvd_momentum": 0.5, "delta_pressure": 0.6, "volume_roc": 0.4,
    "macro_yield": 0.8, "macro_dxy": 0.6, "macro_vix": 0.4, "macro_risk": 0.5,
    "news_sentiment": 1.0, "mtf": 0.5,
}

# (quick_candles, quick_thr_ATR, sim_SLmult, sim_TPmult)  -- how long each judge
# is allowed to be right for. A footprint scalper is judged on 1-3 M5 bars, a
# macro judge on 12-24 bars; scoring both on the same clock is unfair.
JUDGE_HORIZON = {
    "footprint_delta": (3, 0.5, 1.0, 1.5), "footprint_levels": (3, 0.5, 1.0, 1.5),
    "l3_imbalance": (3, 0.5, 1.0, 1.5), "l3_ofi_streak": (3, 0.5, 1.0, 1.5),
    "l3_net_flow": (3, 0.6, 1.5, 2.0), "l3_large_ofi": (3, 0.5, 1.0, 1.5),
    "l3_aggr_limit": (3, 0.4, 1.0, 1.5), "microprice": (2, 0.4, 1.0, 1.0),
    "absorption": (3, 0.4, 1.0, 1.5), "iceberg": (6, 0.6, 2.0, 2.5),
    "iceberg_legacy": (6, 0.6, 2.0, 2.5), "spoof_invert": (6, 0.5, 2.0, 2.0),
    "spoof_invert_loose": (6, 0.5, 2.0, 2.0), "queue_pos": (3, 0.4, 1.0, 1.5),
    "whale_walls": (6, 0.6, 2.0, 2.5), "sweep": (6, 0.7, 2.0, 3.0),
    "vwap_trend": (12, 0.8, 2.0, 3.0), "vwap_bands": (12, 1.0, 2.0, 3.0),
    "vwap_zscore": (12, 0.8, 2.0, 2.5), "poc_day": (12, 0.8, 2.0, 3.0),
    "supply_demand": (12, 0.8, 2.0, 3.0), "value_area": (12, 0.8, 2.0, 3.0),
    "htf_poc": (24, 1.0, 2.0, 3.5), "cvd_divergence": (12, 0.7, 2.0, 3.0),
    "cvd_momentum": (6, 0.5, 1.5, 2.5), "delta_pressure": (6, 0.5, 1.5, 2.5),
    "volume_roc": (6, 0.4, 1.0, 2.0), "macro_yield": (24, 1.0, 2.0, 3.5),
    "macro_dxy": (24, 1.0, 2.0, 3.5), "macro_vix": (24, 1.0, 2.0, 3.0),
    "macro_risk": (24, 1.0, 2.0, 3.0), "news_sentiment": (24, 0.8, 2.0, 3.0),
    "mtf": (12, 0.8, 2.0, 3.0),
}

_J = [
    ("footprint_delta",   r"footprint delta ([+-][\d.]+) dominant ([\d.]+) strength ([\d.]+) -> (BUY|SELL)"),
    ("footprint_levels",  r"footprint (buying|selling) levels (\d+) > (?:selling|buying) (\d+) -> (BUY|SELL)"),
    ("l3_imbalance",      r"(?:v6\.0 )?L3 (?:distance-weighted )?imbalance ([+-][\d.]+)(?:.*?-> (BUY|SELL))?"),
    ("l3_aggr_limit",     r"Aggressive vs limit: (buy|sell) ratio ([\d.]+) vol ([\d.]+) -> (BUY|SELL) power"),
    ("l3_ofi_streak",     r"L3 OFI time-weighted recent streak B(\d+)/S(\d+)"),
    ("l3_net_flow",       r"L3 NET FLOW (BUY|SELL) ([+-][\d.]+) \(buys ([\d.]+) vs sells ([\d.]+)\) w ([\d.]+)"),
    ("l3_large_ofi",      r"L3 large orders (\d+) OFI ([+-][\d.]+) -> (BUY|SELL)"),
    ("iceberg",           r"ICEBERG_(SUPPORT|RESISTANCE)[^\n]*?w ([\d.]+)"),
    ("iceberg_noise",     r"ICEBERG weak (support|resistance) @ ([\d.]+)"),
    ("iceberg_legacy",    r"L3 icebergs (\d+) imb ([+-][\d.]+) w ([\d.]+) -> (BUY|SELL)"),
    ("spoof_invert",      r"SPOOF_INVERT fake (bids|asks) (\d+)[^\n]*?-> (BUY|SELL)"),
    ("whale_walls",       r"L3 whale (SUPPORT|RESISTANCE) (\d+) walls ([\d.]+) lots"),
    ("whale_balanced",    r"L3 whales balanced bid ([\d.]+) ask ([\d.]+)"),
    ("queue_pos",         r"QUEUE_POS good (bid|ask) ratio ([\d.]+)"),
    ("microprice",        r"microprice ([\d.]+) vs mid ([\d.]+) dev ([+-][\d.]+)bps -> (BUY|SELL)"),
    ("sweep",             r"v6\.0 SWEEP (BULLISH|BEARISH)"),
    ("vwap_trend",        r"VWAP trend (UP|DOWN)"),
    ("vwap_bands",        r"v6\.0 VWAP [-+][\d.]+\S*[^\n]*?-> (BUY|SELL)"),
    ("poc_day",           r"POC day ([\d.]+) price ([\d.]+) -> (above|below)"),
    ("supply_demand",     r"near (supply|demand) zone ([\d.]+)"),
    ("value_area",        r"near (VAH|VAL) ([\d.]+)|price [\d.]+ (above VAH|below VAL)"),
    ("htf_poc",           r"HTF H[14] POC ([\d.]+) (?:far )?(above|below) price[^\n]*?-> (BUY|SELL)"),
    ("cvd_divergence",    r"(bullish|bearish) CVD divergence"),
    ("cvd_momentum",      r"CVD (rising|falling) delta"),
    ("delta_pressure",    r"Delta (Buy|Sell)% ([\d.]+) >60%"),
    ("macro_yield",       r"10Y (rising|falling)"),
    ("macro_dxy",         r"DXY (rising|falling)"),
    ("macro_vix",         r"VIX stress"),
    ("macro_risk",        r"risk-(off|on)"),
    ("mtf",               r"^MTF (\S+)"),
]


def judge_panel(record):
    """-> [(judge, dir -1/0/+1, weight)] for one diary record, newest source first."""
    votes = record.get("judge_votes")
    if votes:
        out = []
        for v in votes:
            try:
                out.append((str(v.get("judge", "?")), float(v.get("dir", 0) or 0),
                            float(v.get("weight", 0.5) or 0.5)))
            except Exception:
                continue
        if out:
            return out, "structured"
    panel = []
    for note in record.get("notes") or []:
        if not isinstance(note, str) or len(note) > 400:
            continue
        for judge, pat in _J:
            m = re.search(pat, note)
            if not m:
                continue
            d = 0.0
            g = [x for x in (m.groups() or ()) if x is not None]
            for tok in g:
                if tok in ("BUY", "bullish", "above", "up", "UP", "SUPPORT",
                           "VAL", "BULLISH", "demand", "off", "rising_sell"):
                    d = 1.0
                elif tok in ("SELL", "bearish", "below", "down", "DOWN",
                             "RESISTANCE", "VAH", "BEARISH", "supply", "on"):
                    d = -1.0
            if judge == "footprint_delta" and g:
                try:
                    d = 1.0 if float(g[0]) > 0 else -1.0
                except ValueError:
                    pass
            elif judge == "l3_imbalance" and g:
                try:
                    d = 1.0 if float(g[0]) > 0 else -1.0
                except ValueError:
                    pass
            elif judge == "l3_large_ofi" and len(g) > 1:
                try:
                    d = 1.0 if float(g[1]) > 0 else -1.0
                except ValueError:
                    pass
            elif judge == "poc_day" and len(g) > 2:
                d = 1.0 if g[2] == "above" else -1.0
            elif judge == "supply_demand" and g:
                d = -1.0 if g[0] == "supply" else 1.0
            elif judge == "value_area":
                d = 1.0 if "VAL" in m.group(0) else -1.0
            elif judge == "macro_yield" or judge == "macro_dxy":
                d = -1.0 if (g and g[0] == "rising") else 1.0
            elif judge == "macro_risk":
                d = 1.0 if (g and g[0] == "off") else -1.0
            elif judge == "l3_ofi_streak" and len(g) > 1:
                try:
                    b, sl = float(g[0]), float(g[1])
                    d = 1.0 if b > sl else (-1.0 if sl > b else 0.0)
                except ValueError:
                    pass
            elif judge == "spoof_invert" and g:
                d = -1.0 if g[0] == "bids" else 1.0
            elif judge == "cvd_momentum" and g:
                d = 1.0 if g[0] == "rising" else -1.0
            elif judge in ("footprint_levels", "l3_aggr_limit") and g:
                d = 1.0 if g[0] in ("buying", "buy") else -1.0
            elif judge == "iceberg_legacy" and len(g) > 1:
                try:
                    d = 1.0 if float(g[1]) > 0 else -1.0
                except (TypeError, ValueError):
                    pass
            elif judge == "delta_pressure" and g:
                d = -1.0 if g[0] == "Sell" else 1.0
            elif judge == "news_sentiment" and g:
                try:
                    v = float(g[0])
                    d = 1.0 if v > 0 else (-1.0 if v < 0 else 0.0)
                except (TypeError, ValueError):
                    pass
            w = 0.0
            mw = re.search(r"(?:\bw|weight)\s+([\d.]+)", note)
            if mw:
                try:
                    w = float(mw.group(1))
                except ValueError:
                    w = 0.0
            panel.append((judge, d, w or JUDGE_DEFAULT_W.get(judge, 0.5)))
            break
    return panel, ("notes" if panel else "none")


def score_judge_vote(candles, entry_dt, entry_price, direction, judge):
    """Horizon-aware verdict for one judge vote. -> (quick_win|None, sim_pts|None)"""
    if not candles or not direction or entry_price < 1000:
        return None, None
    nc, thr_atr, slm, tpm = JUDGE_HORIZON.get(judge, (6, 0.5, 1.5, 2.0))
    i = None
    for k, c in enumerate(candles):
        if c["dt"] >= entry_dt.replace(second=0, microsecond=0):
            i = k + 1
            break
    if i is None:
        return None, None
    seg = candles[i:i + nc]
    quick = None
    if seg:
        hi = max(c["high"] for c in seg)
        lo = min(c["low"] for c in seg)
        thr = thr_atr * ATR
        if direction > 0:
            quick = (hi - entry_price) >= thr
        else:
            quick = (entry_price - lo) >= thr
    pts = simulate(candles=candles, ticks=None, entry_dt=entry_dt,
                   direction="BUY" if direction > 0 else "SELL",
                   entry_price=entry_price, max_candles=max(nc, 6),
                   sl_mult=slm, tp_mult=tpm)
    return quick, pts



SUMMARY_ONE_LINER = ""
_DASH = None          # tools/dashboard.py, loaded lazily (see _dash())


def _dash():
    """The visual layer lives in tools/dashboard.py so it can be swapped/iterated
    without touching the auditor.  If it is missing we degrade, never crash.
    A copy is also kept next to audit_day.py, so the dashboard still works when
    someone copies only the two files out of the zip."""
    global _DASH
    if _DASH is not None:
        return _DASH
    import shutil
    import sys
    here = Path(__file__).resolve().parent
    src, dst = here / "tools" / "dashboard.py", here / "dashboard.py"
    try:
        # create the root twin if it is absent - never overwrite one the operator
        # copied himself (a 1.2 KB loader is a valid file, clobbering it is rude)
        if src.exists() and not dst.exists():
            shutil.copyfile(src, dst)
    except Exception:
        pass
    for cand in (here, here / "tools"):
        if (cand / "dashboard.py").exists():
            if str(cand) not in sys.path:
                sys.path.insert(0, str(cand))
            break
    try:
        import dashboard                          # noqa: E402
        _DASH = dashboard
    except Exception as e:                        # missing/corrupt dashboard -> text only
        print(f"[warn] dashboard layer not loaded ({e}); writing plain text only")
        _DASH = False
    return _DASH


def _esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _open_in_browser(path):
    """Show the result in the trader's default browser. Never fatal: a headless box
    or a sandbox simply skips this."""
    if os.environ.get("GBM_NO_BROWSER"):
        return False
    import subprocess
    import webbrowser
    pth = Path(path).resolve()
    if hasattr(os, "startfile"):        # Windows: ask the shell directly. Only this call
        try:                            # survives Git Bash, which rewrites 'cmd /c start'
            os.startfile(str(pth))
            return True
        except Exception:
            pass
    try:
        webbrowser.open(pth.as_uri())
        return True
    except Exception:
        pass
    for cmd in (["cmd", "/c", "start", "", str(pth)], ["xdg-open", str(pth)], ["open", str(pth)]):
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            continue
    return False


def html_report(day, reports, metrics):
    """Self-contained dashboard for one day: no CDN, no fonts, no external js.
    Falls back to a tiny built-in page if tools/dashboard.py is absent."""
    dash = _dash()
    full_text = "\n".join(OUT)
    if dash:
        try:
            hist = dash.history_block(DATA_DIR())
            return dash.day_dashboard(day, reports, metrics, full_text, hist)
        except Exception as e:
            print(f"[warn] dashboard render failed ({e}); using the minimal page")
    order = ("alive", "feed", "pipe", "dq", "guards", "sig", "whatif", "teams", "judges",
             "cov", "ver", "lat", "money", "files", "hour")
    rows = [(g, n, h, w) for k in order for g, n, h, w in reports[k].rows]
    lis = "".join("<li><b>" + str(i) + ". " + _esc(n) + "</b> [" + g + "] " + _esc(h) + "</li>"
                  for i, (g, n, h, _w) in enumerate(rows, start=1))
    css = ("body{font:14px/1.5 Segoe UI,Arial;background:#0f1117;color:#e6e6ec;padding:24px}"
           "pre{white-space:pre-wrap;background:#10131d;padding:12px;border-radius:8px}")
    return ("<!doctype html><meta charset=utf-8><title>Day report " + _esc(str(day)) +
            "</title><style>" + css + "</style><h1>Gold-BookMap day report - " + _esc(str(day)) +
            "</h1><h2>15 tests</h2><ul>" + lis + "</ul><h2>Full text</h2><pre>" + _esc(full_text) + "</pre>")


def write_index_page():
    """data/index.html - double-click this any time; it lists every audited day."""
    dash = _dash()
    if not dash:
        return None
    try:
        return dash.write_index(DATA_DIR())
    except Exception as e:
        print(f"[warn] index page: {e}")
        return None



# ------------------------------------------------------------------ tests ---
def test_1_alive(day, log_path, text, decisions):
    r = Report(1)
    stamps = re.findall(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),(\d{3})", text, re.M)
    dts = [parse_ts(f"{s}.{ms}+02:00") for s, ms in stamps]
    dts = [d for d in dts if on_day(d, day)]
    if not dts:
        r.add("FAIL", "ALIVE CHECK",
              "was it awake all day?",
              [f"no log lines dated {day:%Y-%m-%d} found ({log_path or 'no log file'})",
               "the run you want to grade is not in this folder -> check you are in the right project dir"])
        return r, None
    first, last = min(dts), max(dts)
    span_h = (last - first).total_seconds() / 3600.0
    gap_max = 0.0
    gaps_over_15min = 0
    for a, b in zip(dts, dts[1:]):
        g = (b - a).total_seconds()
        if g > gap_max:
            gap_max = g
        if g > 900:
            gaps_over_15min += 1
    why = [f"log file {log_path.name}, {len(dts)} lines",
           f"first {first.astimezone(BUDA):%H:%M:%S}  last {last.astimezone(BUDA):%H:%M:%S}  = {span_h:.1f} h awake"
           f"  (log start only - if BookMap was already writing before this, the gap is the robot, not you)",
           f"longest silence between two log lines: {gap_max/60:.1f} min, gaps >15 min: {gaps_over_15min}"]
    why += _sess_story(text)
    if span_h >= 12 and gaps_over_15min == 0:
        grade = "PASS"
        why.append("ran the whole day with no long sleep -> good")
    elif span_h >= 4:
        grade = "WARN"
        why.append("only part of the day -> the rest is missing, so any stat below is on partial data")
    else:
        grade = "FAIL"
        why.append("it was awake for much less than a working day -> it did NOT 'work a whole day'")
    r.add(grade, "ALIVE CHECK", "was it awake all day, without long silences?", why)
    return r, {"first": first, "last": last, "span_h": span_h,
               "lines": len(dts), "gap_max": gap_max, "gaps15": gaps_over_15min,
               "hours": sorted({d.astimezone(BUDA).hour for d in dts})}


def test_2_feed(day, ticks, ticks_path, ticks_scanned, mbo_n, alive, mbo_win=0):
    r = Report(2)
    why = []
    if ticks_path is None:
        r.add("FAIL", "FEED CHECK",
              "did BookMap actually hand the robot any price?",
              ["no ticks.csv found (checked .env BOOKMAP_BRIDGE_FILE, ./ticks.csv, ./data/ticks.csv)",
               "without price print data the brain has nothing to think with"])
        return r, {"n": 0, "mbo": mbo_n, "gap": None, "cov": 0.0}
    if not ticks:
        size = 0
        try:
            size = ticks_path.stat().st_size
        except Exception:
            pass
        why.append(f"file {ticks_path} exists but has 0 'Last' trade prints for {day:%Y-%m-%d} (size {size} B)")
        why.append("so the file is either empty or holds only another day -> the robot was blind that day")
        r.add("FAIL", "FEED CHECK", "did BookMap actually hand the robot any price?", why)
        return r, {"n": 0, "mbo": mbo_n, "gap": None, "cov": 0.0}
    n = len(ticks)
    # GRADED SPAN - two modes, and the report always says which one it used:
    #   whole day (default)  -> every row of the calendar day is graded, 00:00-24:00 local
    #   trading window       -> only BUDAPEST_START..END hours are graded  (--window)
    # The bridge records around the clock, so a day file legally carries rows the robot
    # never trades. An hour window is one operator question among many, not the default,
    # so nothing is cut unless it is asked for. In whole-day mode "did it record?" is
    # answered by continuity - the holes and how much of the day the tape spans - instead
    # of by a fixed % of a 15h denominator that the market's own break would sink.
    span_min = (WINDOW_END_H - WINDOW_START_H) * 60
    whole = span_min >= 1440
    inwin = [t for t in ticks
             if WINDOW_START_H <= t[0].astimezone(BUDA).hour < WINDOW_END_H] or ticks
    t_first = inwin[0][0].astimezone(BUDA)
    t_last = inwin[-1][0].astimezone(BUDA)
    per_min = len(inwin) / max(1.0, ((inwin[-1][0] - inwin[0][0]).total_seconds() / 60.0))
    gap = max(((b[0] - a[0]).total_seconds() for a, b in zip(inwin, inwin[1:])), default=0)
    cov_min = len({t[0].replace(second=0, microsecond=0) for t in inwin})
    awake_min = max(1, int((inwin[-1][0] - inwin[0][0]).total_seconds() // 60) + 1)
    if whole:
        cov = pct(cov_min, awake_min)                 # continuity of the recording
        day_cover = pct(awake_min, 1440)              # how much of the day the tape spans
    else:
        cov = pct(cov_min, span_min)
        day_cover = pct(cov_min, 1440)
        if len(inwin) != n:
            why.append(f"per-minute / hole / coverage measured on the {len(inwin):,} prints "
                       f"inside {WINDOW_START_H:02d}:00-{WINDOW_END_H:02d}:00; the other "
                       f"{n - len(inwin):,} prints the bridge wrote outside that span are "
                       f"counted, not graded")
    why.append(f"{n:,} trade prints, {per_min:.0f}/min average, biggest hole {gap:.0f}s")
    _win_h = (23 - 8) if (WINDOW_START_H == 0 and WINDOW_END_H >= 24) else (WINDOW_END_H - WINDOW_START_H)
    why.append(f"tape runs {t_first:%H:%M} -> {t_last:%H:%M} local "
               f"({awake_min / 60.0:.1f} h = {day_cover:.0f}% of the 24h day, "
               f"{pct(min(awake_min, _win_h * 60), _win_h * 60):.0f}% of your {_win_h}h trading window)")
    why.append(f"coverage: price present in {cov_min:,} distinct minutes = {cov:.0f}% of "
               + (f"the {awake_min / 60.0:.1f}h the tape spans (recording continuity)"
                  if whole else f"the {WINDOW_END_H - WINDOW_START_H}h window"))
    if mbo_win and mbo_win != mbo_n:
        why.append(f"MBO (order by order): {mbo_win:,} lines inside your window, "
                   f"{mbo_n - mbo_win:,} more outside it (night session, same date) - "
                   f"judges only ever see the in-window rows")
    else:
        why.append(f"MBO (order by order) lines for the day: {mbo_n:,}")
    app_off_hours = []
    if whole:
        hrs_all = sorted({t[0].astimezone(BUDA).hour for t in ticks})
        empty = [h for h in range(24) if h not in hrs_all]
        why.append(f"hours with a trade print: {len(hrs_all)} of 24"
                   + (f" (empty: {', '.join(f'{h:02d}' for h in empty)})" if empty else ""))
        # A day can lose hours for two very different reasons: the bridge stopped handing
        # over price (a fault), or the app was not running so nobody asked (not a fault).
        # test 1 knows which hours the robot's log has lines in - compare.
        log_hours = (alive or {}).get("hours")
        if empty and log_hours is not None:
            app_off_hours = [h for h in empty if h not in log_hours]
            if len(app_off_hours) == len(empty):
                why.append("every empty hour is also silent in the robot's own log -> the app "
                           "was not running in those hours, so no tape was ever expected there")
    else:
        # window mode: a day where trades exist in only 5 of 15 hours is a recording
        # problem, not a quiet market - say so explicitly instead of "thin"
        hrs = sorted({t[0].astimezone(BUDA).hour for t in inwin})
        first_h = t_first.hour + t_first.minute / 60.0
        late = first_h - WINDOW_START_H
        missing = [h for h in range(WINDOW_START_H, WINDOW_END_H) if h not in hrs]
        if len(hrs) < (WINDOW_END_H - WINDOW_START_H):
            tag2 = ("-> tape covers almost the whole window"
                    if len(hrs) >= (WINDOW_END_H - WINDOW_START_H) - 2
                    else "-> the robot saw depth all day but trades only in these hours")
            why.append(f"print hours present: {len(hrs)} of {WINDOW_END_H - WINDOW_START_H} "
                       f"(missing: {', '.join(f'{h:02d}' for h in missing) or 'none'}) {tag2}")
        if late > 3.0:
            why.append(f"first trade print {first_h:02.0f}:xx = {late:.0f} h after your "
                       f"{WINDOW_START_H:02d}:00 open -> BookMap/add-on was not running yet; "
                       f"the whole early session is missing, so grade this day as partial")
    if n >= 200000 and gap < 30 and cov > 80:
        grade = "PASS"
        why.append("thick, unbroken tape -> footprint, CVD and iceberg all have real material")
    elif n >= 20000 and (day_cover >= 50 if whole else cov >= 75):
        grade = "PASS"
        why.append(f"enough material: {n:,} prints across {cov_min:,} minutes, "
                   f"{day_cover:.0f}% of the day covered -> gradeable; print-counting "
                   f"judges (footprint/CVD) work with fewer samples than on a liquid day")
    elif n >= 20000 and gap < 300:
        grade = "WARN"
        why.append("usable but thin/patchy -> judges that count prints get weaker, and low "
                   "strengths are partly a data problem, not a market one")
    else:
        grade = "FAIL"
        why.append("too little data to trust any signal from this day")
    if whole and day_cover < 50 and grade == "PASS":
        grade = "WARN"
        why.append(f"the tape only spans {awake_min / 60.0:.1f} h of the 24h day "
                   f"({day_cover:.0f}%) -> read this as a PARTIAL day (the app was started "
                   f"late or stopped early), not as a quiet market")
    # Promote only when the hours the app was UP look like a healthy feed: a fixed print
    # total punishes a short-but-fine session, so the test is density while awake.
    if app_off_hours and grade in ("WARN", "FAIL") and n >= 500 and per_min >= 5:
        grade = "WARN"
        why = [w for w in why if "too little data to trust" not in w]
        why.insert(0, f"PARTIAL DAY, AND NOT BY FAULT: {len(app_off_hours)} of the 24 hours "
                      f"({', '.join(f'{h:02d}' for h in app_off_hours)}) hold no tape because the "
                      f"app was not running in them - nobody asked BookMap for that hour. The hours "
                      f"it did run are recorded normally, so this is a missing day, not a feed fault.")
        why.append("judge this day only on the hours the app was up; for a whole-day grade, "
                   "start the app before the open and keep the machine awake")
    r.add(grade, "FEED CHECK", "did BookMap actually hand the robot any price?", why)
    return r, {"n": n, "mbo": mbo_n, "gap": gap, "cov": cov, "per_min": per_min,
               "day_cover": day_cover, "awake_min": awake_min, "whole_day": whole,
               "app_off_hours": len(app_off_hours)}


def test_3_pipeline(text, decisions):
    r = Report(3)
    steps = {}
    for step in ("STEP 1", "STEP 2", "STEP 3", "STEP 4", "STEP 5", "MT5 SIGNAL BRIDGE"):
        steps[step] = len(re.findall(re.escape(step) + r".*-> (OK|HOLD|SKIPPED|EXECUTED|REJECTED|WARN)", text))
    ok = len(re.findall(r"-> OK", text))
    err = re.findall(r"(ERROR|CRITICAL|Traceback|Exception)[^\n]{0,120}", text)
    warn = re.findall(r"WARNING[^\n]{0,120}", text)
    cyc = len(re.findall(r"pipeline start", text))
    # per-cycle marker counts: STEP4/STEP5/bridge legitimately print HOLD/SKIPPED, not OK
    marks = {}
    for step in ("STEP 1", "STEP 2", "STEP 3", "STEP 4", "STEP 5", "MT5 SIGNAL BRIDGE"):
        marks[step] = len(re.findall(re.escape(step) + r"[^\n]*-> ", text))
    why = [f"pipeline cycles started: {cyc} | '-> OK' markers: {ok} | decisions logged: {len(decisions)}",
           "end-marker per step: " + ", ".join(f"{k}={v}" for k, v in marks.items())]
    if cyc == 0:
        r.add("FAIL", "PIPELINE CHECK",
              "did all 5 steps run, or did it die halfway?",
              ["no 'pipeline start' line in the log -> main.py loop never really ran",
               "the brain was not thinking, only the file watcher was sitting there"])
        return r, None
    if err:
        why.append(f"{len(err)} error/critical lines, e.g.: {err[0][:100]}")
    if warn:
        why.append(f"{len(warn)} warnings, most common: {Counter(w[8:60].strip() for w in warn).most_common(1)}")
    if err:
        r.add("FAIL", "PIPELINE CHECK", "did all 5 steps run, or did it die halfway?", why)
        return r, None       # one grade per test; without this the same test also printed
                             # a PASS row and the SCOREBOARD totals were inflated
    missing = [k for k, v in marks.items() if cyc and v < cyc]
    if len(decisions) < cyc * 0.5 and cyc > 3:
        why.append("far fewer decisions than cycles -> loops started then something stalled before the log write")
        r.add("WARN", "PIPELINE CHECK", "did all 5 steps run, or did it die halfway?", why)
    elif missing:
        why.append(f"these steps finished fewer cycles than started: {missing} -> the loop breaks inside that step")
        r.add("WARN", "PIPELINE CHECK", "did all 5 steps run, or did it die halfway?", why)
    else:
        why.append("every cycle finished through STEP 5 and the signal bridge -> plumbing is sound")
        r.add("PASS", "PIPELINE CHECK", "did all 5 steps run, or did it die halfway?", why)
    return r, {"cycles": cyc, "errors": len(err), "warnings": len(warn)}


def test_4_data_quality(day, ticks, text):
    r = Report(4)
    why = []
    n = len(ticks)
    zero_px = sum(1 for t in ticks if t[1] <= 0)
    secs = Counter(t[0].replace(second=0, microsecond=0) for t in ticks)
    busiest = secs.most_common(1)[0][1] if secs else 0
    dup = 0
    seen = set()
    for t in ticks:
        k = (t[0], t[1])
        if k in seen:
            dup += 1
        seen.add(k)
    snap_age = [float(x) for x in re.findall(r"feed age ([\d.]+)s", text)]
    zero_px_pct = pct(zero_px, n)
    dup_pct = pct(dup, n)
    why.append(f"price<=0 prints: {zero_px:,} ({zero_px_pct:.1f}% of {n:,}) | exact duplicate prints: {dup_pct:.1f}% | busiest single minute: {busiest:,} prints")
    if snap_age:
        ages = [a for a in snap_age if a > 0]
        why.append(f"'feed age' reported by STEP 1: median {statistics.median(snap_age):.1f}s, max {max(snap_age):.1f}s")
    if n == 0:
        r.add("FAIL", "DATA QUALITY CHECK", "is the data it ate clean and fresh?",
              ["no data to check"])
        return r, None
    if busiest > 60000:
        why.append("one minute with >60k prints = catch-up replay, not live tape -> those minutes fake huge volume")
    grade = "PASS" if zero_px_pct < 0.1 and dup_pct < 2.0 and busiest < 60000 else "WARN"
    if grade == "WARN":
        why.append("something above is off -> counts like 'big buyer vs big seller' can be inflated")
    else:
        why.append("clean tape: no zero prices, no flood from catch-up -> footprint numbers mean what they say")
    r.add(grade, "DATA QUALITY CHECK", "is the data it ate clean and fresh?", why)
    return r, {"zero_px": zero_px, "dup_pct": dup_pct, "busiest": busiest}


def test_5_guards(text, decisions, feed):
    r = Report(5)
    reasons = Counter((d.get("reason") or "").strip() for d in decisions)
    reasons.pop("", None)
    blocked = Counter()
    for pat, name in ((r"SAFETY: FEED", "stale/empty feed"),
                      (r"spread guard|SPREAD.*too wide|spread [0-9.]+ >", "CFD spread guard"),
                      (r"BLACKOUT", "news blackout"),
                      (r"confidence .*<|conf [0-9.]+ <|below threshold", "confidence too low"),
                      (r"trading disabled", "trading disabled"),
                      (r"AI (rate|quota)|quota", "AI quota guard")):
        c = len(re.findall(pat, text, re.I))
        if c:
            blocked[name] = c
    # "reached MT5" means the executor actually sent an order. EXECUTED = filled/accepted,
    # PENDING = placed and waiting. DEFERRED is a REFUSAL by a guard (spread/basis) - it is
    # the opposite of a send, and counting it as one made the 2026-09-23 verdict claim
    # "8 to MT5" on a day whose MT5 history is empty.
    def _st(d):
        return (d.get("exec_status") or "").upper()

    exec_rows = [d for d in decisions if _st(d) in ("EXECUTED", "PENDING")]
    deferred_rows = [d for d in decisions if _st(d) == "DEFERRED"]
    skipped_rows = [d for d in decisions if _st(d) in ("SKIPPED", "")]
    why = []
    if not decisions:
        why.append("no decisions logged for this day -> nothing to explain")
        r.add("FAIL", "GUARD CHECK", "why did it refuse to trade?", why)
        return r, None
    top = ", ".join(f"{k} x{v}" for k, v in reasons.most_common(6)) or "none"
    why.append(f"exec_status: {Counter((d.get('exec_status') or '?') for d in decisions).most_common()}")
    why.append(f"its own written reasons: {top}")
    if blocked:
        why.append("in-log guard firings: " + ", ".join(f"{k} x{v}" for k, v in blocked.most_common()))
    n_orders = len(exec_rows)
    if n_orders:
        why.append(f"{n_orders} decision(s) were actually sent to MT5 -> these are the trades to check one by one")
    else:
        why.append("0 orders were sent to MT5 -> the robot only thought, it never acted")
    if deferred_rows:
        _why_dec = Counter((d.get("reason") or "?")[:60] for d in deferred_rows).most_common(3)
        why.append(f"{len(deferred_rows)} decision(s) DEFERRED: a guard refused the order "
                   f"(spread/basis) - these never reached MT5, they are refusals, not trades")
        for _r, _c in _why_dec:
            why.append(f"   refused x{_c}: {_r}")
    if skipped_rows:
        why.append(f"{len(skipped_rows)} decision(s) SKIPPED before the executor (confidence/AI gate)")
    if feed is not None and feed.get("n", 0) < 1000:
        why.append(f"feed delivered only {feed.get('n', 0):,} prints -> the guards had nothing to judge; this is a plumbing failure, not a strategy decision")
    if len(reasons) == 1 and "empty or invalid market snapshot" in reasons:
        why.append("EVERY refusal is 'empty or invalid snapshot' = it never got data. That is a FEED problem, not a strategy problem.")
        r.add("FAIL", "GUARD CHECK", "why did it refuse to trade?", why)
    elif n_orders == 0 and len(reasons) >= 2:
        why.append("guards did real work: it saw data and decided 'not good enough' -> correct discipline, but no proof of profit from this day")
        r.add("WARN", "GUARD CHECK", "why did it refuse to trade?", why)
    else:
        why.append("mix of guard and trade outcomes -> normal for one day")
        r.add("PASS", "GUARD CHECK", "why did it refuse to trade?", why)
    return r, {"reasons": reasons, "orders": n_orders,
               "deferred": len(deferred_rows), "skipped": len(skipped_rows)}


def test_6_signals(decisions, diary):
    r = Report(6)
    dirs = Counter((d.get("signal_direction") or "?") for d in decisions)
    confs = [d["signal_confidence"] for d in decisions if "signal_confidence" in d]
    strs = [d["signal_strength"] for d in decisions if "signal_strength" in d]
    acts = Counter((d.get("ai_action") or "?") for d in decisions)
    over_conf = sum(1 for c in confs if c >= CONF_MIN)
    over_str = sum(1 for s in strs if s >= AI_MIN)
    why = []
    if not decisions and not diary:
        why.append("no decisions and no snapshot diary for this day")
        r.add("FAIL", "SIGNAL CHECK", "how loud did the brain shout, really?", why)
        return r, None
    why.append(f"directions: {dict(dirs)} | ai_action: {dict(acts)}")
    if confs:
        why.append(f"confidence: min {fmt(min(confs))} / avg {fmt(statistics.mean(confs))} / max {fmt(max(confs))} -> cleared {CONF_MIN}%: {over_conf} of {len(confs)}")
    if strs:
        why.append(f"strength:  min {fmt(min(strs))} / avg {fmt(statistics.mean(strs))} / max {fmt(max(strs))} -> cleared AI gate {AI_MIN}: {over_str} of {len(strs)}")
    if diary:
        dstr = []
        for o in diary:
            v = o.get("strength") or o.get("signal_strength") or (o.get("market_data") or {}).get("signal_strength")
            try:
                dstr.append(float(v))
            except Exception:
                pass
        if dstr:
            why.append(f"diary ({len(diary)} snapshots) strength: max {fmt(max(dstr))}, avg {fmt(statistics.mean(dstr))} -> realistic ceiling on M5 is ~50, so the gate at {fmt(AI_MIN,0)} is reasonable")
    if over_conf == 0 and len(confs) > 20:
        why.append("nothing ever cleared the 50% gate on this day -> either a quiet market, or the guard is too high, or data was thin")
        r.add("WARN", "SIGNAL CHECK", "how loud did the brain shout, really?", why)
    elif over_conf > 0:
        why.append(f"{over_conf} snapshots were trade-grade -> the day produced candidates worth grading")
        r.add("PASS", "SIGNAL CHECK", "how loud did the brain shout, really?", why)
    else:
        r.add("WARN", "SIGNAL CHECK", "how loud did the brain shout, really?", why)
    return r, {"dirs": dirs, "over_conf": over_conf}


def test_7_what_if(day, ticks, decisions, log_path):
    r = Report(7)
    if not ticks:
        why = [f"needs {day:%Y-%m-%d} price data to replay the day",
               "no ticks for this day -> 'what if it had traded' cannot be answered, and it cannot be faked"]
        if log_path:
            why.append(f"once ticks.csv has this day, re-run: python audit_day.py --date {day:%Y-%m-%d}")
        r.add("NA", "WHAT-IF CHECK", "if it had taken every signal, would we have made money?", why)
        return r, None
    candles = build_candles(ticks)
    cands = [d for d in decisions if (d.get("signal_direction") or "") in ("BUY", "SELL") and d.get("price", 0) > 1000]
    why = [f"{len(candles)} M5 candles built, {len(cands)} real BUY/SELL decisions with a price to replay"]
    if not cands:
        # fall back: grade every snapshot in the window at its own confidence
        grade = "WARN"
        why.append("the robot asked for ZERO trades that day -> so I cannot score its choices; I can only tell you the market did move, so the question stays open")
        moves = []
        for a, b in zip(candles, candles[1:]):
            moves.append(abs(b["close"] - a["close"]))
        if moves:
            why.append(f"average 5-min move that day: {fmt(statistics.mean(moves),2)} pts, biggest {fmt(max(moves),2)} pts (ATR used for SL/TP: {fmt(ATR)})")
        r.add(grade, "WHAT-IF CHECK", "if it had taken every signal, would we have made money?", why)
        return r, None
    res = []
    for d in cands:
        pts = simulate(ticks, candles, d["_dt"], d["signal_direction"], d["price"])
        if pts is not None:
            res.append((d, pts))
    if not res:
        r.add("WARN", "WHAT-IF CHECK", "if it had taken every signal, would we have made money?",
              ["no decision had candles after it (day ran out of tape) -> cannot grade"])
        return r, None
    pts_list = [p for _d, p in res]
    wins = [p for p in pts_list if p > 0]
    losses = [p for p in pts_list if p <= 0]
    total = sum(pts_list)
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")
    eq = 0.0
    dd = 0.0
    for p in pts_list:
        eq += p
        dd = max(dd, eq - max(0.0, eq)) if False else max(dd, (max(0.0, eq) - eq))
    why.append(f"SL={fmt(SL_MULT)}xATR TP={fmt(TP_MULT)}xATR max {MAX_HOLD_CANDLES*5}min, cost {fmt(SPREAD)} pts per round trip")
    if len(res) != len(cands):
        why.append(f"of those {len(cands)} signals, {len(res)} could be replayed: the other "
                   f"{len(cands) - len(res)} sat within the last M5 candle of the tape, so the "
                   f"market had already ended and there was nothing left to score them on")
    why.append(f"if ALL {len(res)} had been taken: total {total:+.1f} pts | win rate {pct(len(wins), len(res)):.0f}% | profit factor {fmt(pf,2)} | worst drawdown {fmt(dd,1)} pts")
    best = max(res, key=lambda x: x[1])
    worst = min(res, key=lambda x: x[1])
    why.append(f"best: {hhmm(best[0]['_dt'])} {best[0]['signal_direction']} {best[1]:+.1f} pts (conf {fmt(best[0]['signal_confidence'])}, {best[0].get('regime')})")
    why.append(f"worst: {hhmm(worst[0]['_dt'])} {worst[0]['signal_direction']} {worst[1]:+.1f} pts (conf {fmt(worst[0]['signal_confidence'])}, {worst[0].get('regime')})")
    hi_conf = [p for d, p in res if d["signal_confidence"] >= CONF_MIN]
    lo_conf = [p for d, p in res if d["signal_confidence"] < CONF_MIN]
    if hi_conf and lo_conf:
        why.append(f"gate test: >= {fmt(CONF_MIN,0)}% conf -> {sum(hi_conf):+.1f} pts ({len(hi_conf)} trades); below gate -> {sum(lo_conf):+.1f} pts ({len(lo_conf)})")
        if len(hi_conf) < 20 or len(lo_conf) < 20:
            why.append(f"too few trades to compare gates ({len(hi_conf)} above / {len(lo_conf)} below) - "
                       f"that is weather, not climate. 20+ a side before this line can advise anything; "
                       f"the numbers above are shown, the advice is withheld on purpose")
        else:
            why.append("if the below-gate pile is bigger, the 50% threshold is throwing away money; if it's negative, the guard earns its keep")
    grade = "PASS" if total > 0 else "WARN"
    r.add(grade, "WHAT-IF CHECK", "if it had taken every signal, would we have made money?", why)
    METRICS["whatif"] = {"n": len(res), "total_pts": round(total, 1),
                         "win_rate": round(pct(len(wins), len(res)), 1),
                         "profit_factor": (round(pf, 2) if pf != float("inf") else 999.0),
                         "max_drawdown_pts": round(dd, 1)}
    return r, {"total": total, "wins": len(wins), "losses": len(losses)}


def _score_items(blob):
    """Normalise a vote/scores field into [(name, number), ...] whatever shape it is.

    Three shapes really exist in this project's files:
      dict                 {"flow": 0.4, "whale": -0.9}            (team_scores)
      list of dicts        [{"judge": "footprint_delta", "dir": 1, "weight": 1.6, "raw": ...}]
                                                                   (v7.1 judge_votes, from
                                                                    judge_panel.parse_judge_panel)
      list of strings      ["footprint_delta BUY", ...]             (older notes-only dumps)
    A field that is a list used to crash test 8 with
    "'list' object has no attribute 'items'", which killed the whole report.
    """
    out = []
    if isinstance(blob, dict):
        for k, v in blob.items():
            try:
                out.append((str(k), float(v)))
            except (TypeError, ValueError):
                continue
        return out
    if isinstance(blob, (list, tuple)):
        for e in blob:
            if isinstance(e, dict):
                name = e.get("judge") or e.get("name") or e.get("id") or e.get("team")
                if name is None:
                    continue
                val = None
                for key in ("dir", "vote", "value", "score", "side", "weight"):
                    if key in e and e[key] is not None:
                        try:
                            val = float(e[key])
                        except (TypeError, ValueError):
                            val = None
                        if val is not None:
                            break
                if val is None:
                    continue
                out.append((str(name), val))
            elif isinstance(e, str):
                parts = e.split()
                if len(parts) >= 2:
                    try:
                        out.append((parts[0], float(parts[1])))
                    except ValueError:
                        out.append((parts[0], 1.0))
    return out


def test_8_teams(diary):
    r = Report(8)
    if not diary:
        r.add("NA", "TEAM/JUDGE CHECK",
              "which team and which judge were right more often?",
              ["data/snapshots_history.jsonl has no entries for this day -> the per-team and per-judge scorecard needs the diary",
               "the diary was added in v4.3; it only fills up while a v4.3+ run is live",
               "run the robot for one full day, then re-run this audit -> this test becomes the most useful one of all"])
        return r, None
    team = defaultdict(lambda: {"c": 0, "w": 0})
    judge = defaultdict(lambda: {"c": 0, "w": 0})
    shapes = Counter()
    for o in diary:
        for src, blob in (("teams", o.get("team_scores") or o.get("teams")),
                          ("judges", o.get("judges") or o.get("judge_votes"))):
            if blob is None:
                continue
            shapes[type(blob).__name__] += 1
            for k, v in _score_items(blob):
                if abs(v) < 0.05:
                    continue
                (team if src == "teams" else judge)[k]["c" if v > 0 else "w"] += 1
    why = [f"{len(diary)} diary snapshots used"
           + (f" (fields seen: {', '.join(f'{k} x{v}' for k, v in shapes.most_common())})"
              if shapes else "")]
    if team:
        ranked = sorted(team.items(), key=lambda kv: kv[1]["c"] / max(1, kv[1]["c"] + kv[1]["w"]), reverse=True)
        why.append("teams (votes that pointed up vs down): " + ", ".join(f"{k} {pct(v['c'], v['c']+v['w']):.0f}% n={v['c']+v['w']}" for k, v in ranked))
    if judge:
        jr = sorted(judge.items(), key=lambda kv: kv[1]["c"] / max(1, kv[1]["c"] + kv[1]["w"]), reverse=True)
        why.append("quick sign check (raw vote agreement, every clock merged - the per-judge "
                   "table below is the number to use): "
                   + ", ".join(f"{k} {pct(v['c'], v['c']+v['w']):.0f}%" for k, v in jr[:8]))
        why.append("judges near the bottom are the weights to cut tomorrow; footprint_delta near the top means the footprint file is doing its job")
    else:
        why.append("no judge breakdown in the diary -> check the diary writer includes per-judge scores")
    r.add("PASS" if (team or judge) else "WARN", "TEAM/JUDGE CHECK",
          "which team and which judge were right more often?", why)
    return r, None


def test_9_judges(day, diary, ticks, candles):
    r = Report(9)
    if not diary:
        r.add("NA", "JUDGE PANEL",
              "how did each judge behave: footprint, L3, iceberg, whale, VWAP...",
              ["no diary (data/snapshots_history.jsonl) for this day -> the judges cannot be graded",
               "start main.py with the v7.1 patch, let it run 08:00-23:00, then re-run this audit"])
        return r, None
    if not candles:
        r.add("NA", "JUDGE PANEL",
              "how did each judge behave: footprint, L3, iceberg, whale, VWAP...",
              [f"diary has {len(diary)} records but there is no ticks.csv for {day:%Y-%m-%d} to score them against",
               "judges can be listed but not graded -> I will not fake a verdict"])
        panel_counts = Counter()
        for rec in diary[-400:]:
            panel, src = judge_panel(rec)
            for j, d, w in panel:
                panel_counts[j] += 1
        if panel_counts:
            say("        votes actually recorded per judge (who even spoke):")
            for j, c in panel_counts.most_common(12):
                say(f"           {j:<20} {c:>5}")
        return r, None

    stats = defaultdict(lambda: {"votes": 0, "buy": 0, "sell": 0, "quiet": 0,
                                 "q_hit": 0, "q_tot": 0, "pts": [], "agree": 0,
                                 "agree_tot": 0, "by_hour": defaultdict(lambda: [0, 0]),
                                 "by_regime": defaultdict(lambda: [0, 0]),
                                 "w": []})
    used = 0
    srcs = Counter()
    recs = diary[-1500:]
    for rec in recs:
        dt = rec.get("_dt")
        price = float(rec.get("price", 0) or 0)
        if not dt or price < 1000:
            continue
        panel, src = judge_panel(rec)
        srcs[src] += 1
        if not panel:
            continue
        used += 1
        ens = (rec.get("signal_direction") or "NEUTRAL").upper()
        ens_dir = 1 if ens == "BUY" else (-1 if ens == "SELL" else 0)
        regime = str(rec.get("regime", "?"))
        hour = dt.astimezone(BUDA).hour
        for j, d, w in panel:
            st = stats[j]
            st["votes"] += 1
            st["w"].append(w)
            if d > 0:
                st["buy"] += 1
            elif d < 0:
                st["sell"] += 1
            else:
                st["quiet"] += 1
            if ens_dir and d:
                st["agree_tot"] += 1
                if (d > 0) == (ens_dir > 0):
                    st["agree"] += 1
            if not d:
                continue
            quick, pts = score_judge_vote(candles, dt, price, d, j)
            if quick is None:
                continue
            st["q_tot"] += 1
            st["by_hour"][hour][1] += 1
            st["by_regime"][regime][1] += 1
            if quick:
                st["q_hit"] += 1
                st["by_hour"][hour][0] += 1
                st["by_regime"][regime][0] += 1
            if pts is not None:
                st["pts"].append(pts)

    if used == 0:
        r.add("WARN", "JUDGE PANEL", "how did each judge behave: footprint, L3, iceberg, whale, VWAP...",
              [f"{len(recs)} diary records read, but none carried a judge line "
               f"(panel sources: {dict(srcs)})",
               "this is a diary from an older build that did not keep notes -> "
               "run with the v7.1 main.py so 'judge_votes' is written",
               "nothing was graded, and grading nothing is not the same as grading 'bad'"])
        return r, None

    rows, thin = [], []
    for j, st in stats.items():
        if st["q_tot"] < 3:
            # A judge with 1-2 scored calls cannot be graded, but it must not VANISH either:
            # the operator asked "where are the rest of my judges?" - the honest answer is a
            # line that names them and says why they have no row, never silent absence.
            thin.append((j, st))
            continue
        acc = pct(st["q_hit"], st["q_tot"])
        pts = sum(st["pts"]) if st["pts"] else 0.0
        avg = statistics.mean(st["pts"]) if st["pts"] else 0.0
        rows.append((j, st, acc, pts, avg))
    rows.sort(key=lambda x: (x[2], x[3]), reverse=True)

    say(f"        {used}/{len(recs)} diary records had a judge panel "
        f"({', '.join(f'{k}:{v}' for k, v in srcs.items())}) | "
        f"scoring: each judge gets its own clock (footprint 3 M5 bars, L3 3-6, "
        f"iceberg/whale 6, VWAP/structure 12, macro 24)")
    say(f"        {'JUDGE':<20}{'VOTES':>6}{'PART%':>7}{'BUY/SELL/Q':>12}"
        f"{'RIGHT%':>8}{'n':>6}{'PTS':>9}{'AVG':>7}{'AGR_ENS':>8}  SUGG_WEIGHT")
    for j, st, acc, pts, avg in rows:
        part = pct(st["votes"], used)
        agree = pct(st["agree"], st["agree_tot"])
        cur_w = statistics.median(st["w"]) if st["w"] else JUDGE_DEFAULT_W.get(j, 0.5)
        edge = (acc - 50.0) / 50.0
        sugg = max(0.0, round(cur_w * (1.0 + 0.5 * edge), 2))
        st["sugg"] = (cur_w, sugg)
        split = f"{st['buy']}/{st['sell']}/{st['quiet']}"
        say(f"        {j:<20}{st['votes']:>6}{part:>6.0f}%{split:>12}"
            f"{acc:>7.1f}%{st['q_tot']:>6}{pts:>+9.1f}{avg:>+7.2f}{agree:>7.0f}%   {cur_w:.1f} -> {sugg:.2f}")

    if thin:
        say("        too few calls to grade (needs 3 scored calls), listed so nobody goes missing:")
        say("          " + ", ".join(f"{j} ({st['votes']} vote(s), {st['q_tot']} scored)"
                                      for j, st in sorted(thin, key=lambda kv: -kv[1]["votes"])))
    quiet = [j for j, st in stats.items() if pct(st["votes"], used) < 5.0]
    if quiet:
        say("        barely spoke (<5% of snapshots): " + ", ".join(quiet[:10]))
    foot = stats.get("footprint_delta")
    if foot:
        say(f"        footprint (your BookMap aggressive buyer vs seller) spoke in "
            f"{pct(foot['votes'], used):.0f}% of snapshots, right {pct(foot['q_hit'], max(1, foot['q_tot'])):.0f}% of the time"
            f" over its 3-bar window -> it is still a real judge in the vote, not a decoration")
    top = rows[0][0] if rows else None
    bot = rows[-1][0] if len(rows) > 2 else None
    if rows:
        by_money = sorted(rows, key=lambda x: (x[4], x[2]), reverse=True)
        say(f"        best by accuracy: {rows[0][0]} {rows[0][2]:.0f}% | "
            f"best by money per call: {by_money[0][0]} {by_money[0][4]:+.2f} pts/call")
        if len(rows) > 2:
            say(f"        worst by money per call: {by_money[-1][0]} {by_money[-1][4]:+.2f} pts/call "
                f"({by_money[-1][2]:.0f}% right) -> a judge can be right often and still lose money; "
                f"weight by money, not by pride")
    say("        hourly heat (right% of that judge's calls, top 6 active judges):")
    act = sorted(stats.items(), key=lambda kv: kv[1]["votes"], reverse=True)[:6]
    say("           " + " " * 20 + "".join(f"{h:>5}" for h in (8, 10, 12, 14, 16, 18, 20, 22)))
    for j, st in act:
        cells = ""
        for h in (8, 10, 12, 14, 16, 18, 20, 22):
            hit, tot = st["by_hour"].get(h, (0, 0))
            cells += f"{(f'{pct(hit, tot):.0f}' if tot else '-'):>5}"
        say(f"           {j:<20}{cells}")
    for j, st in stats.items():
        if len(st["by_regime"]) > 1 and st["q_tot"] >= 10:
            txt = ", ".join(f"{rg} {pct(v[0], v[1]):.0f}%(n={v[1]})"
                            for rg, v in sorted(st["by_regime"].items(),
                                                key=lambda kv: -kv[1][1])[:3])
            say(f"           {j:<20} regime: {txt}")
            break

    out = DATA_DIR() / f"judge_panel_{day:%Y-%m-%d}.csv"
    try:
        with open(out, "w", newline="", encoding="utf-8") as f:
            wcsv = csv.writer(f)
            wcsv.writerow(["judge", "votes", "participation_pct", "buy", "sell", "quiet",
                           "right_pct", "scored", "points_after_spread", "avg_points",
                           "agree_with_ensemble_pct", "weight_now", "weight_suggested"])
            for j, st, acc, pts, avg in rows:
                wcsv.writerow([j, st["votes"], round(acc, 1), st["buy"], st["sell"], st["quiet"],
                               round(acc, 1), st["q_tot"], round(pts, 1),
                               round(statistics.mean(st["pts"]), 3) if st["pts"] else 0,
                               round(pct(st["agree"], st["agree_tot"]), 1),
                               *[round(x, 2) for x in st.get("sugg", (0, 0))]])
        sug = DATA_DIR() / "judge_weight_suggestions.json"
        sug.write_text(json.dumps({
            "date": f"{day:%Y-%m-%d}",
            "method": "suggested = median weight of that judge today * (1 + 0.5*(accuracy-50%)/50%)",
            "note": "review, not auto-apply. Anything scored on <20 votes is noise.",
            "judges": {j: {"accuracy_pct": round(a, 1), "scored": st["q_tot"],
                            "weight_now": round(st["sugg"][0], 2),
                            "weight_suggested": round(st["sugg"][1], 2)}
                       for j, st, a, _p, _av in rows if st["q_tot"] >= 20}
        }, indent=1), encoding="utf-8")
        say(f"        saved: {out}")
        say(f"        saved: {sug}")
    except Exception as e:
        say(f"        [warn] could not save judge csv: {e}")

    best_acc = rows[0][2] if rows else 0
    if rows:
        METRICS["judges"] = {"scored": len(rows),
                             "best": f"{rows[0][0]} {rows[0][2]:.0f}%",
                             "worst": f"{rows[-1][0]} {rows[-1][2]:.0f}%",
                             "used": used,
                             "table": [{"judge": j, "votes": st["votes"],
                                        "right_pct": round(a, 1), "scored": st["q_tot"],
                                        "buy": st["buy"], "sell": st["sell"], "quiet": st["quiet"],
                                        "part_pct": round(100.0 * st["votes"] / used, 1) if used else 0.0,
                                        "pts": round(pt, 1)}
                                       for j, st, a, pt, _av in rows[:12]]}
    grade = "PASS" if best_acc >= 55 else "WARN"
    why = [f"{len(stats)} judge(s) voted on this day | {len(rows)} graded (>=3 scored calls)"
           + (f" | {len(thin)} too few calls to grade (named below, never silently dropped)"
              if thin else "")]
    if rows and best_acc < 55:
        why.append("no judge is above 55% on its own clock -> the panel is not adding information today; do not add weight, subtract first")
    else:
        why.append("at least one judge is above 55% on its own clock -> it deserves its weight; check the SUGG_WEIGHT column")
    why.append("AGR_ENS = how often the judge agreed with the final 4-team verdict. 100% agree + low accuracy = the judge only repeats what everyone already said.")
    r.add(grade, "JUDGE PANEL", "how did each judge behave: footprint, L3, iceberg, whale, VWAP...", why)
    return r, {"rows": rows}


def test_10_judge_coverage(day, diary):
    r = Report(10)
    if not diary:
        r.add("NA", "DIARY COVERAGE", "is the diary good enough to grade the panel tomorrow?",
              ["no diary file for this day yet"])
        return r, None
    struct = notes50 = novotes = 0
    lines_max = 0
    for rec in diary:
        panel, src = judge_panel(rec)
        if src == "structured":
            struct += 1
        n = rec.get("notes") or []
        lines_max = max(lines_max, len(n))
        if len(n) >= 50:
            notes50 += 1
        if not n:
            novotes += 1
    why = [f"{len(diary)} records | structured judge_votes: {struct} | notes parsed ok: {len(diary)-struct-novotes}",
           f"longest notes list in a record: {lines_max} lines (old builds cut at 50 -> judges after the cut were invisible)",
           f"records with an empty notes list: {novotes}"]
    if struct == len(diary):
        why.append("every snapshot carries its full panel -> judge stats from test 9 are complete")
        r.add("PASS", "DIARY COVERAGE", "is the diary good enough to grade the panel tomorrow?", why)
    elif notes50:
        why.append(f"{notes50} records were truncated at 50 notes -> late judges (VWAP, macro, news) may be missing from scoring")
        why.append("fix: main.py v7.1 writes notes[:200] + judge_votes, then this test goes green")
        r.add("WARN", "DIARY COVERAGE", "is the diary good enough to grade the panel tomorrow?", why)
    else:
        why.append("notes exist but no structured panel -> retro-parsing works, weights are estimates")
        r.add("WARN", "DIARY COVERAGE", "is the diary good enough to grade the panel tomorrow?", why)
    return r, None


def test_11_version_config(text, day):
    r = Report(11)
    env = dict(ENV)              # last-wins .env, exactly what config.py saw
    rc = _load_run_config(day)   # what the ROBOT recorded while that day was running
    ran = ({k: rc.get(k) for k in ("CONFIDENCE_THRESHOLD", "AI_MIN_SIGNAL_STRENGTH",
                                   "V6_CFD_SPREAD_MAX", "BOOKMAP_WINDOW_SECONDS",
                                   "BOOKMAP_MAX_DEPTH_LEVELS")} if rc else {})
    want = {"BOOKMAP_WINDOW_SECONDS": "10800", "BOOKMAP_MAX_DEPTH_LEVELS": "20",
            "AI_MIN_SIGNAL_STRENGTH": "6", "CONFIDENCE_THRESHOLD": "50",
            "V6_CFD_SPREAD_MAX": "0.50", "TRADING_ENABLED": "1"}
    basis = {k: (ran.get(k) if ran.get(k) not in (None, "") else env.get(k)) for k in want}
    src_name = "recorded by the robot for that day" if ran else "today's .env (no run_config file)"
    def _same(a, b):
        if a is None:
            return False
        try:
            return abs(float(a) - float(b)) < 1e-9     # 0.5 == 0.50 == "0.50"
        except Exception:
            return str(a).strip() == str(b).strip()
    diff = [f"{k}: ran={basis.get(k, 'MISSING')} expected={v}" for k, v in want.items()
            if not _same(basis.get(k), v)]
    ver = re.findall(r"v\d\.\d[\.\d]*", text)
    feed_age = len(re.findall(r"STALE/EMPTY", text))
    creds = re.findall(r"Credentials: (\{[^}]*\})", text)
    why = []
    for k, vals in sorted(ENV_DUPLICATES.items()):
        why.append(f"AMBIGUOUS .env key {k}: {vals[0]!r} -> {vals[-1]!r}  (last wins; the earlier line is dead - delete it)")
    for k, v in env.items():
        if k.startswith("GEMINI_API_KEY") and v in ("", "REPLACE_ME", "your_key_here"):
            why.append(f"{k} is EMPTY -> STEP 3 AI cannot run at all (AI calls = 0)")
            break
    why.append(f"config basis  : {src_name} | window {basis.get('BOOKMAP_WINDOW_SECONDS')}s "
               f"depth {basis.get('BOOKMAP_MAX_DEPTH_LEVELS')} conf {basis.get('CONFIDENCE_THRESHOLD')}% "
               f"ai_gate {basis.get('AI_MIN_SIGNAL_STRENGTH')} spread_cap {basis.get('V6_CFD_SPREAD_MAX')}")
    if ran:
        why.append(f"the robot's own record: {Path(rc['_path']).name} (written {rc.get('written_at', '?')}) "
                   f"-> this report is graded with the day's rule, so editing .env today cannot rewrite history")
        for k in ("CONFIDENCE_THRESHOLD", "AI_MIN_SIGNAL_STRENGTH", "V6_CFD_SPREAD_MAX"):
            if str(ran.get(k)) not in ("None", "") and str(ran.get(k)) != str(env.get(k)):
                why.append(f"   the day ran with {k}={ran.get(k)}; today's .env says {env.get(k)} "
                           f"(the grade above uses {ran.get(k)})")
    else:
        why.append("no run_config_<date>.json for this day -> graded against today's .env (older "
                   "build, or the file was deleted); the 24f build pins this per day from now on")
    if creds:
        why.append(f"last credentials line seen by the robot: {creds[-1]}")
    if diff:
        why.append("MISMATCH with v7.0-P3-M5-FAIR-CFD-50PCT: " + "; ".join(diff[:4]))
        r.add("WARN", "VERSION CHECK", "did it run the version you think it ran?", why)
    else:
        why.append("matches v7.0-P3 (3h window, 20 levels, 50% gate, 0.50 spread cap) -> the numbers below describe the build you intended")
        r.add("PASS", "VERSION CHECK", "did it run the version you think it ran?", why)
    return r, None


def test_12_latency(text):
    r = Report(12)
    tot = [float(x) for x in re.findall(r"pipeline (\d+)ms", text)]
    s1 = [float(x) for x in re.findall(r"STEP1 acquire (\d+)ms", text)]
    s2 = [float(x) for x in re.findall(r"STEP2 analyze (\d+)ms", text)]
    s3 = [float(x) for x in re.findall(r"STEP3 AI (\d+)ms", text)]
    if not tot:
        r.add("NA", "SPEED CHECK", "was it fast enough to still be on time?",
              ["no LATENCY lines in the log for this day"])
        return r, None
    steady = tot[1:] if len(tot) > 3 else tot   # cycle #1 always pays for cold start
    med = statistics.median(steady)
    under = sum(1 for t in steady if t <= LATENCY_TARGET_MS)
    ai_calls = [x for x in s3 if x > 0]
    tot_all = tot
    why = [f"total pipeline (warm-up excluded): median {med:.0f}ms, p95 {sorted(steady)[max(0, int(len(steady)*0.95)-1)]:.0f}ms, max {max(steady):.0f}ms",
           f"STEP1 {fmt(statistics.median(s1),0) if s1 else '-'}ms  STEP2 {fmt(statistics.median(s2),0) if s2 else '-'}ms  STEP3 median {fmt(statistics.median(ai_calls),0) if ai_calls else '0 (AI never called)'}ms",
           f"under {LATENCY_TARGET_MS:.0f}ms target: {under}/{len(steady)} cycles ({pct(under, len(steady)):.0f}%) | AI calls needing network: {len(ai_calls)} (quota {AI_MAX_CALLS})"]
    if len(tot) < 20:
        r.add("NA", "SPEED CHECK", "was it fast enough to still be on time?",
              [f"only {len(tot)} timed cycle(s) in the log -> a sample this small proves nothing",
               f"median {med:.0f}ms (first cycle {tot[0]:.0f}ms, warm-up excluded)",
               "after a full 08:00-23:00 day this test becomes meaningful"])
        return r, {"median": med}
    if med <= LATENCY_TARGET_MS:
        grade = "PASS"
        why.append("on the first cycle the numbers are always ugly (file open, caches cold); look at the median")
    elif med <= 2000:
        grade = "WARN"
        why.append("a second-scale loop still sees the move, but you are reacting, not anticipating")
    else:
        grade = "FAIL"
        why.append("multi-second loops on M5 = by the time it decides, the move is gone")
    r.add(grade, "SPEED CHECK", "was it fast enough to still be on time?", why)
    return r, {"median": med}


def _mt5_truth(day):
    """Ask MT5 itself what is true. Read-only, never raises, never blocks a report.

    Returns (dict, None) or (None, "why not"). Called only when the MetaTrader5 SDK is
    importable - on the operator's Windows box it is, because main.py uses it to trade.
    """
    if globals().get("SKIP_MT5"):
        return None, "skipped for the demo/fixture run"
    try:
        import MetaTrader5 as mt5
    except Exception as e:
        return None, f"MetaTrader5 SDK not importable from this shell ({e.__class__.__name__})"
    try:
        if not mt5.initialize():
            return None, f"terminal not reachable ({mt5.last_error()})"
        try:
            positions = mt5.positions_get() or []
            frm = datetime.combine(day, datetime.min.time())
            to = frm + timedelta(days=1)
            deals = list(mt5.history_deals_get(frm, to) or [])
            # entry==1 means "deal that CLOSED/EXITED a position" in MT5's deal model
            closed = [d for d in deals if getattr(d, "entry", None) == 1]
            opened = [d for d in deals if getattr(d, "entry", None) == 0]
            pnl = sum(float(getattr(d, "profit", 0.0) or 0.0) for d in closed)
            last = max((getattr(d, "time", 0) for d in deals), default=0)
            return {
                "closed_ids": [int(getattr(d, "position_id", 0) or 0) for d in closed[:8]],
                "open_now": len(positions),
                "open_detail": [f"{getattr(p, 'symbol', '?')} {getattr(p, 'volume', '?')} "
                                f"@ {getattr(p, 'price_open', '?')}"
                                for p in positions[:6]],
                "deals_total": len(deals), "closed": len(closed), "opened": len(opened),
                "pnl": pnl,
                # MT5 hands back deal times as broker-SERVER-time epochs. Formatting one
                # with the local clock added the server offset AND the local offset on top
                # (a 09:39 UTC deal printed as 14:39). UTC + an explicit label is the only
                # honest reading of a number whose timezone we cannot know from here.
                "last_deal": (datetime.fromtimestamp(last, tz=timezone.utc).strftime("%m-%d %H:%M")
                              if last else "none"),
            }, None
        finally:
            try:
                mt5.shutdown()
            except Exception:
                pass
    except Exception as e:
        return None, f"query failed ({e.__class__.__name__}: {e})"


def _outcomes_for_day(day):
    """Closed deals the robot itself logged for that day (data/trade_outcomes.csv)."""
    p = DATA_DIR() / "trade_outcomes.csv"
    rows = []
    if not p.exists():
        return rows, p
    try:
        with open(p, encoding="utf-8", errors="ignore", newline="") as f:
            for r in csv.DictReader(f):
                if on_day(parse_ts(r.get("timestamp", "")), day):
                    rows.append(r)
    except Exception:
        return rows, p
    return rows, p


def _day_money_lines(day, decisions):
    """What the GRADED DAY did - from its own records, never from today's live files."""
    out = ["THE GRADED DAY (from that day's own records, not from today's files):"]
    dec = decisions or []
    if dec:
        st = Counter((d.get("exec_status") or "?") for d in dec)
        out.append(f"decisions logged that day: {len(dec)} | "
                   + ", ".join(f"{k} {v}" for k, v in st.most_common())
                   + f" | signals that reached MT5: {st.get('EXECUTED', 0)}")
    else:
        out.append("no decisions_log.csv rows for this day -> the robot logged nothing that day")
    rows, p = _outcomes_for_day(day)
    if rows:
        try:
            net = sum(float(r.get("pnl") or 0) for r in rows)
        except Exception:
            net = 0.0
        out.append(f"closed deals the robot logged that day: {len(rows)} (net {net:+.2f}) "
                   f"- {p.name}, one row per closed deal")
    else:
        out.append(f"no rows in {p.name} for this day -> nothing it sent was closed that day "
                   f"(or it was graded before the outcome was written)")
    return out


def _day_money_verdict(day, decisions):
    """The day's own money story. No live file can change it, then or later."""
    out = []
    dec = decisions or []
    sent = sum(1 for d in dec if (d.get("exec_status") or "").upper() == "EXECUTED")
    rows, _p = _outcomes_for_day(day)
    if sent and not rows:
        out.append(f"{sent} signal(s) reached MT5 that day but no closed-deal row was written -> "
                   f"check the MT5 History tab for that day by hand")
        return "WARN", out
    if rows:
        try:
            net = sum(float(r.get("pnl") or 0) for r in rows)
        except Exception:
            net = 0.0
        out.append(f"{len(rows)} closed deal(s) logged that day, net {net:+.2f} -> the day's money "
                   f"story is complete on disk (data/trade_outcomes.csv)")
        return "PASS", out
    out.append("the robot sent nothing to MT5 that day (or nothing closed) -> this day's decisions "
               "put no money at risk")
    return "PASS", out


def test_13_money(day, decisions=None):
    r = Report(13)
    pos = _as_dict(read_json(DATA_DIR() / "tracked_bot_positions.json"))
    snap = _as_dict(read_json(DATA_DIR() / "market_snapshot.json"), key="price")
    sig_p = DATA_DIR() / "mt5_signal.txt"
    sig = {}
    if sig_p.exists():
        for line in sig_p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                sig[k.strip()] = v.strip()
    open_ids = pos.get("position_ids") or []
    why = _day_money_lines(day, decisions) + [
           f"RIGHT NOW (as of {datetime.now(BUDA):%Y-%m-%d %H:%M}, NOT the graded day):",
           f"the bot's own tracking file lists {len(open_ids)} position id(s) "
           f"({open_ids}) last updated {pos.get('updated_at', 'never')}",
           "   that file is APPEND-ONLY bookkeeping (it is written to look trades up in "
           "history later; nothing ever removes an id), so it is NOT a list of open trades",
           f"current mt5_signal.txt (live file, not the day's): direction={sig.get('direction')} conf={sig.get('confidence')} lots={sig.get('lots')} sl={sig.get('sl')} tp={sig.get('tp')} at {sig.get('timestamp')}",
           f"snapshot file price={snap.get('price', snap.get('current_price', 'n/a'))} keys={len(snap)}"]
    orphan = pos.get("updated_at") and str(pos.get("updated_at"))[:10] != f"{day:%Y-%m-%d}"
    truth, why_not = _mt5_truth(day)
    if truth is not None:
        why.append(f"MT5 SAYS (this is the number to trust): {truth['open_now']} open position(s) right now"
                   + (f" [{', '.join(truth['open_detail'])}]" if truth["open_detail"] else "")
                   + f" | this day's deals: {truth['deals_total']} ({truth['opened']} opened, "
                     f"{truth['closed']} closed, net {truth['pnl']:+.2f})"
                     f" | last deal {truth['last_deal']} broker-server time (UTC)")
    else:
        if globals().get("SKIP_MT5"):
            why.append("DEMO/fixture run: MT5 is deliberately NOT consulted, so this report says "
                       "nothing about your account - a synthetic day cannot include your real "
                       "positions. To check YOUR positions and deals, run the real day:  "
                       f"python audit_day.py --date {datetime.now(BUDA).date()}")
        why.append(f"MT5 could not be asked from this shell ({why_not}) -> the file above is "
                   f"bookkeeping only, it cannot tell you what is open. Check MT5 by hand.")
    # The robot's own records are NOT the truth: MT5 is. When they disagree, the report must
    # say so out loud, because a trade that closed and was never written down is exactly how
    # a day quietly loses money in the books. (Operator, 2026-09-24: two positions in the
    # terminal, one in the report.)
    _rec_rows, _ = _outcomes_for_day(day)
    _mismatch = False
    if truth is not None:
        if truth["closed"] > len(_rec_rows):
            _mismatch = True
            why.append(f"MT5 vs OUR OWN RECORDS: MT5 closed {truth['closed']} position(s) that day"
                       + (f" (ids {truth['closed_ids']})" if truth.get("closed_ids") else "")
                       + f", but trade_outcomes.csv only records {len(_rec_rows)}"
                       + f" -> {truth['closed'] - len(_rec_rows)} closed position(s) are MISSING from "
                         f"the robot's own records. MT5 is the truth; every money line above this "
                         f"one is incomplete until the gap is explained.")
        elif len(_rec_rows) > truth["closed"]:
            why.append(f"note: our records list more closed deals ({len(_rec_rows)}) than MT5 shows "
                       f"({truth['closed']}) - check the date filter and the broker's server timezone.")
    _is_today = (day == datetime.now(BUDA).date())
    if not _is_today:
        # The live files describe TODAY, not that day. They are printed above so you can
        # hand-check, and named as advisory so they can never rewrite that day's grade.
        why.append("(advisory: the live files above describe RIGHT NOW - they are shown for "
                   "hand-checking and do NOT change this day's grade)")
        _g, _l = _day_money_verdict(day, decisions)
        why += _l
        if _mismatch:
            _g = "WARN"
        r.add(_g, "MONEY CHECK", "did anything get left open, forgotten, or half-written?",
              why + ["grade basis: the day's own records (decisions + closed deals), not today's files"])
        return r, None
    def _mgrade(g):
        return "WARN" if _mismatch else g

    if open_ids and truth is not None and truth["open_now"] == 0:
        why.append("the tracking file lists ids but MT5 has nothing open: those ids belong to "
                   "trades that are closed (or were never sent). Nothing to close - the file "
                   "does not prune itself.")
        r.add(_mgrade("PASS" if truth["deals_total"] == 0 else "WARN"), "MONEY CHECK",
              "did anything get left open, forgotten, or half-written?", why)
    elif open_ids:
        why.append("there are tracked ids -> and MT5 was NOT asked, so go look at them by hand before trusting anything")
        r.add(_mgrade("WARN"), "MONEY CHECK", "did anything get left open, forgotten, or half-written?", why)
    elif orphan:
        why.append("tracking file is from another day -> the loop ended without clearing it (harmless, but check MT5 manually once)")
        r.add(_mgrade("WARN"), "MONEY CHECK", "did anything get left open, forgotten, or half-written?", why)
    else:
        why.append("nothing left open, no half-made order -> the robot never put real money at risk that day")
        r.add(_mgrade("PASS"), "MONEY CHECK", "did anything get left open, forgotten, or half-written?", why)
    return r, None


def test_14_files(day):
    r = Report(14)
    items = []
    # test 9 writes data/judge_weight_suggestions.json; this list used to ask for
    # "weight_suggestions.json", so a file that was right there got reported MISSING.
    wanted = [("decisions_log.csv",), ("market_snapshot.json",), ("mt5_signal.txt",),
              ("snapshots_history.jsonl",),
              ("judge_weight_suggestions.json", "weight_suggestions.json"),
              ("tracked_bot_positions.json",),
              (f"diary_{day:%Y%m%d}.jsonl",), (f"run_config_{day:%Y%m%d}.json",)]
    for names in wanted:
        found = None
        for n in names:
            cand = DATA_DIR() / n
            if cand.exists():
                found = cand
                break
        if found is not None:
            st = found.stat()
            items.append((names[0], st.st_size,
                          datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).astimezone(BUDA)))
        else:
            items.append((names[0], None, None))
    why = []
    for name, size, mt in items:
        if size is None:
            _tag = ("  (per-day file - written by build 24f onwards)"
                    if name.startswith(("diary_", "run_config_")) else "")
            why.append(f"{name:<28} MISSING{_tag}")
        else:
            spread = f"{size:,} B" if size < 1024 else f"{size/1024:.0f} KB"
            why.append(f"{name:<28} {spread:>10}, last written {mt:%Y-%m-%d %H:%M} "
                       f"(CURRENT file - the day's own rows are counted below)")
    dc = dict(DAY_COUNTS or {})
    if dc:
        why.append(f"rows for THIS day ({day:%Y-%m-%d}), read back from the files above: "
                   f"decisions {dc.get('decisions', '?')} | diary {dc.get('diary', '?')} | "
                   f"trade prints {dc.get('prints', '?')}"
                   + (f" | duplicate prints dropped across sources: {dc['dups_dropped']}"
                      if dc.get("dups_dropped") else ""))
        if not dc.get("diary"):
            why.append(f"0 diary records for this day -> if the master file has rotated past it "
                       f"(it keeps the last 20k lines at 50 MB), the per-day mirror "
                       f"diary_{day:%Y%m%d}.jsonl is the only copy - and the judges need one of them")
    hist = [p for p in items if p[0] == "snapshots_history.jsonl"][0]
    if hist[1] and hist[1] > 50 * 1048576:
        why.append("diary over 50 MB -> rotation should have trimmed it; check it isn't growing forever")
    need = [p[0] for p in items if p[1] is None and p[0] in ("decisions_log.csv", "market_snapshot.json", "mt5_signal.txt")]
    if need:
        why.append(f"missing core evidence: {need} -> that day left no footprint of its thinking")
        r.add("FAIL", "EVIDENCE CHECK", "did it keep the notebooks we need to grade it?", why)
    else:
        grade = "PASS" if hist[1] else "WARN"
        why.append("diary present" if hist[1] else "diary absent -> team/judge scorecard will stay empty (test 8)")
        r.add(grade, "EVIDENCE CHECK", "did it keep the notebooks we need to grade it?", why)
    return r, None


def test_15_hourly(day, ticks, decisions):
    r = Report(15)
    if len(decisions) < 10:
        r.add("NA", "HOUR CHECK", "was it better in London morning or New York afternoon?",
              [f"only {len(decisions)} decision(s) logged for this day -> too few to say which hour was good",
               "after a full day of real tape this becomes the 'when should the robot be allowed to trade' map"])
        return r, None
    by_h = defaultdict(lambda: {"n": 0, "maxc": 0.0, "buy": 0, "sell": 0})
    for d in decisions:
        h = d["_dt"].astimezone(BUDA).hour
        e = by_h[h]
        e["n"] += 1
        e["maxc"] = max(e["maxc"], d["signal_confidence"])
        if d.get("signal_direction") == "BUY":
            e["buy"] += 1
        elif d.get("signal_direction") == "SELL":
            e["sell"] += 1
    # ONE source of truth for the session split, and it must be CONTIGUOUS and cover the
    # whole day. The ranges used to be range(2,8)/range(8,12)/range(13,18)/range(18,23):
    # hour 12 (a trading hour in the 08:00-23:00 window) belonged to NO session, so the
    # four lines added up to 185 of 200 decisions and the dashboard block under-counted
    # noon the same way. Three copies of the old list made that easy to miss - now one.
    HOUR_BUCKETS = (("Asia", range(0, 8)), ("London AM", range(8, 12)),
                    ("London PM/NY", range(12, 18)), ("NY PM", range(18, 24)))
    why = [f"{'hour':>5} {'n':>5} {'BUY/SELL':>9} {'maxConf':>8}  bar"]
    for h in range(0, 24):
        e = by_h.get(h)
        if not e:
            continue
        bar = "#" * int(min(40, e["n"] / 2))
        why.append(f"{h:>5} {e['n']:>5} {str(e['buy'])+'/'+str(e['sell']):>9} {e['maxc']:>7.1f}  {bar}")
    for label, hours in HOUR_BUCKETS:
        n = sum(by_h[h]["n"] for h in hours if h in by_h)
        mx = max([by_h[h]["maxc"] for h in hours if h in by_h] or [0])
        span = f"{min(hours):02d}-{max(hours) + 1:02d}"      # half-open, like the buckets
        why.append(f"{label + ' (' + span + ')':<22} decisions {n:>4}  best confidence {mx:5.1f}%")
    tot_sess = sum(by_h[h]["n"] for _lab, rng in HOUR_BUCKETS for h in rng if h in by_h)
    tot_rows = sum(e["n"] for e in by_h.values())
    why.append(f"the four sessions cover {tot_sess} of the {tot_rows} decision(s) in the hourly table"
               + ("" if tot_sess == tot_rows else
                  "  <-- BUG: the session buckets miss an hour - tell the auditor's author"))
    why.append("a session with lots of snapshots but 0% max confidence = the machine saw nothing there, or data was missing then")
    best_sess = max(HOUR_BUCKETS,
                    key=lambda t: max([by_h[h]["maxc"] for h in t[1] if h in by_h] or [0]))
    why.append(f"loudest hour belongs to {best_sess[0]} -> keep the robot where the noise is")
    r.add("PASS", "HOUR CHECK", "was it better in London morning or New York afternoon?", why)
    hours = {str(h): {"n": e["n"], "max": round(e["maxc"], 1), "buy": e["buy"], "sell": e["sell"]}
             for h, e in sorted(by_h.items())}
    sess = [{"label": lab, "n": sum(by_h[h]["n"] for h in rng if h in by_h),
             "max": round(max([by_h[h]["maxc"] for h in rng if h in by_h] or [0.0]), 1)}
            for lab, rng in HOUR_BUCKETS]
    return r, {"hours": hours, "sessions": sess, "best": best_sess[0]}



def _isolate_env(env_path):
    """Wipe the inherited robot settings and re-read them from ONE file.

    Used by --env (grade another config) and by --demo. Without this the demo
    inherited a real .env, whose BOOKMAP_BRIDGE_FILE points at the operator's
    ticks.csv - the demo day is not in there, so every data test graded 0 prints.
    """
    for k in list(os.environ):
        if k.startswith(("BOOKMAP_", "AI_", "GEMINI_", "V6_", "CONFIDENCE", "TRADING_",
                         "MAX_", "NT_", "BUDAPEST_", "AUDIT_", "LATENCY_")):
            del os.environ[k]
    ENV.clear()
    _load_dotenv(str(env_path))
    globals().update(
        ATR=_envf("AUDIT_ATR", 3.18), SPREAD=_envf("AUDIT_SPREAD", 0.50),
        SL_MULT=_envf("STOP_LOSS_ATR_MULT", 2.0), TP_MULT=_envf("TAKE_PROFIT_ATR_MULT", 3.5),
        MAX_HOLD_CANDLES=int(_envf("AUDIT_MAX_HOLD_MIN", 180) / 5.0) or 36,
        WINDOW_START_H=int(_env("BUDAPEST_START", "08:00").split(":")[0]),
        WINDOW_END_H=int(_env("BUDAPEST_END", "23:00").split(":")[0]),
        AI_MAX_CALLS=int(_envf("AI_MAX_CALLS_PER_DAY", 2000)),
        LATENCY_TARGET_MS=_envf("LATENCY_TARGET_MS", 500.0),
        CONF_MIN=_envf("CONFIDENCE_THRESHOLD", 63.0),
        AI_MIN=_envf("AI_MIN_SIGNAL_STRENGTH", 8.0))

# ------------------------------------------------------------------ main ---
def _rollup_src(day):
    """Which files exist for this day. Globs only - never reads a tape."""
    log = LOGS_DIR() / f"trading_{day:%Y%m%d}.log"
    arc = DATA_DIR() / "archive"
    arch = len(list(arc.glob(f"ticks_{day:%Y%m%d}*"))) if arc.exists() else 0
    mp = DATA_DIR() / f"day_metrics_{day:%Y-%m-%d}.json"
    return {"log": log.exists(), "archive": arch, "metrics": mp.exists(), "path": mp}


def _parse_saved_report(day):
    """Fallback source: the saved TEXT report. Used when a day was graded by an older
    build (no day_metrics json) or when the raw files were moved away. Without this the
    roll-up showed a perfectly graded day as "-", and claimed the app never ran."""
    p = DATA_DIR() / f"day_audit_{day:%Y-%m-%d}.txt"
    if not p.exists():
        return None
    try:
        txt = p.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return None
    m = re.search(r"VERDICT\s+(\d{4}-\d\d-\d\d):\s+pass\s+(\d+)\s+warn\s+(\d+)\s+fail\s+(\d+)"
                  r"\s+nodata\s+(\d+)\s+\|\s+feed\s+([\d,]+)\s+prints\s+\|\s+(\d+)\s+decisions"
                  r"\s+\|\s+(\d+)\s+to MT5", txt)
    if m:
        g = ("p%s w%s f%s n%s" % (m.group(2), m.group(3), m.group(4), m.group(5)))
        prints = m.group(6).replace(",", "")
        decis, mt5 = m.group(7), m.group(8)
        wi = re.search(r"whatif\s+(-?[\d.]+|n/a)\s+pts\s+\(WR\s+(-?[\d.]+|n/a)%\)", txt)
        bj = re.search(r"best judge\s+(.*?)\s+\|\s+worst\s+(.*)", txt)
        counts = {"PASS": int(m.group(2)), "WARN": int(m.group(3)),
                  "FAIL": int(m.group(4)), "NA": int(m.group(5))}
    else:                                   # very old report: count the grade tags
        counts = {k: len(re.findall(r"\[%s\]" % t, txt)) for k, t in
                  (("PASS", "PASS"), ("WARN", "WARN"), ("FAIL", "FAIL"), ("NA", " -- "))}
        g = "p%d w%d f%d n%d" % (counts["PASS"], counts["WARN"], counts["FAIL"], counts["NA"])
        prints = decis = mt5 = "-"
        wi = bj = None
    span = ("whole-day 00:00-24:00" if re.search(r"grading WHOLE DAY", txt)
            else (re.search(r"TIME FILTER: only (\d\d:\d\d-\d\d:\d\d)", txt).group(1)
                  if re.search(r"TIME FILTER: only (\d\d:\d\d-\d\d:\d\d)", txt) else "saved report"))
    try:
        mt = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).astimezone(BUDA)
        prov = f"from text report written {mt:%m-%d %H:%M}"
    except Exception:
        prov = "from saved text report"
    return {"date": f"{day:%Y-%m-%d}", "src": "saved-report", "grades": g,
            "prints": f"{int(prints):,}" if prints.isdigit() else "-", "mbo": "-",
            "decis": decis, "mt5": mt5,
            "whatif": (f"{float(wi.group(1)):+.1f}" if wi and wi.group(1) != "n/a" else "-"),
            "wr": (f"{float(wi.group(2)):.0f}%" if wi and wi.group(2) != "n/a" else "-"),
            "best": (bj.group(1) if bj else "-"), "worst": (bj.group(2) if bj else "-"),
            "cov": "-", "span": span, "note": "raw files are gone for this day - the row was read "
            "from the saved report so the totals keep it", "prov": prov + " (not re-graded)",
            "partial": False, "_m": None, "_day": day, "_counts": counts}


def _rollup_row(day, today):
    """One row of the roll-up. Never invents numbers: a day without metrics shows "-"."""
    src = _rollup_src(day)
    blank = {"date": f"{day:%Y-%m-%d}", "src": "-", "grades": "-", "prints": "-", "mbo": "-",
             "decis": "-", "mt5": "-", "whatif": "-", "wr": "-", "best": "-", "worst": "-",
             "cov": "-", "span": "-", "note": "", "prov": "-", "partial": False,
             "_m": None, "_day": day}
    if not src["metrics"]:
        saved = _parse_saved_report(day)          # a graded day whose raw files moved away
        if saved:
            return saved
        blank["note"] = ("no raw files and no saved report for this day - nothing was recorded"
                         if not (src["log"] or src["archive"])
                         else f"raw files exist but the day was never graded - run: "
                              f"python audit_day.py --date {day:%Y-%m-%d}")
        return blank
    try:
        m = json.loads(src["path"].read_text(encoding="utf-8"))
    except Exception as e:                                   # a half-written json
        blank["note"] = f"metrics unreadable ({e}) - re-run: python audit_day.py {day:%Y-%m-%d}"
        return blank
    c = m.get("counts") or {}
    wi = m.get("whatif") or {}
    j = m.get("judges") or {}
    row = {
        "date": m.get("date", f"{day:%Y-%m-%d}"), "src": "saved",
        "grades": f"p{c.get('PASS', 0)} w{c.get('WARN', 0)} f{c.get('FAIL', 0)} n{c.get('NA', 0)}",
        "prints": f"{int(m.get('feed_prints', 0) or 0):,}",
        "mbo": f"{int(m.get('feed_mbo', 0) or 0):,}",
        "decis": str(m.get("decisions", 0)), "mt5": str(m.get("orders_to_mt5", 0)),
        "whatif": (f"{float(wi.get('total_pts', 0)):+.1f}" if wi else "-"),
        "wr": (f"{float(wi.get('win_rate', 0)):.0f}%" if wi else "-"),
        "best": j.get("best", "-"), "worst": j.get("worst", "-"),
        "cov": f"{float(m.get('feed_coverage_pct', 0) or 0):.0f}%",
        "span": (str(m.get("graded_span"))[:21] if m.get("graded_span")
                 else (f"drill {m.get('time_filter')}" if m.get("time_filter")
                       else "08:00-23:00 (older)")),
        "note": str(m.get("verdict_line", "") or "")[:200], "_m": m, "_day": day,
    }
    # WHEN it was graded decides whether the row is a whole day or a slice of one
    prov, partial = "", False
    try:
        g = datetime.fromisoformat(str(m.get("generated", "")).replace("Z", "+00:00"))
        if g.tzinfo is None:
            g = g.replace(tzinfo=timezone.utc)
        gl = g.astimezone(BUDA)
        prov = f"graded {gl:%m-%d %H:%M}"
        partial = (gl.date() == day and gl.hour < 23) or gl.date() < day
    except Exception:
        pass
    if partial:
        prov += " PARTIAL"
    if day == today and not partial:
        prov += " (today)"
    elif day == today:
        prov += " (today so far)"
    row["prov"] = prov or "-"
    row["partial"] = partial
    return row


def _judge_rollup(rows):
    """Per-judge accuracy across the rolled-up days, weighted by how often it was scored."""
    agg: dict = {}
    for r in rows:
        tab = ((r.get("_m") or {}).get("judges") or {}).get("table") or []
        for t in tab:
            name = t.get("judge")
            if not name:
                continue
            a = agg.setdefault(name, {"days": 0, "votes": 0, "scored": 0, "right": 0.0})
            sc = int(t.get("scored", 0) or 0)
            a["days"] += 1
            a["votes"] += int(t.get("votes", 0) or 0)
            a["scored"] += sc
            a["right"] += float(t.get("right_pct", 0) or 0) * sc
    out = [(j, a["days"], a["votes"], a["scored"], (a["right"] / a["scored"] if a["scored"] else 0.0))
           for j, a in agg.items()]
    out.sort(key=lambda x: (-x[4], -x[3]))
    return out


def _rollup_html(rows, judges, n_days, d0, d1, audited_now, empty_days):
    def esc(s):
        return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    head = ["DAY", "GRADES", "PRINTS", "MBO LINES", "DECISIONS", "TO MT5", "WHAT-IF", "WR",
            "BEST JUDGE", "WORST JUDGE", "COV%", "GRADED SPAN", "SOURCE", "GRADED AT"]
    keys = ["date", "grades", "prints", "mbo", "decis", "mt5", "whatif", "wr", "best", "worst",
            "cov", "span", "src", "prov"]
    tr = []
    for r in rows:
        cls = ""
        if r["src"] == "-":
            cls = " class=empty"
        elif "f0" not in r["grades"].replace("f0", "f0"):
            cls = " class=bad"
        tr.append("<tr" + cls + ">" + "".join(f"<td>{esc(r.get(k, '-'))}</td>" for k in keys) + "</tr>")
    tot = "".join(f"<th>{h}</th>" for h in head)
    led = "".join(f"<tr><td>{esc(j)}</td><td>{d}</td><td>{v:,}</td><td>{s:,}</td>"
                  f"<td><b>{a:.1f}%</b></td></tr>" for j, d, v, s, a in judges[:12])
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Gold-BookMap roll-up - last {n_days} days</title></head>
<body style="margin:0;background:#0d1117;color:#e6edf3;font:14px/1.5 system-ui,Segoe UI,Arial">
<div style="max-width:1180px;margin:0 auto;padding:26px 20px 60px">
<h1 style="margin:0 0 4px;font-size:22px">Gold-BookMap - last {n_days} calendar days</h1>
<p style="color:#8b949e;margin:0 0 18px">{d0} .. {d1} &middot; whole calendar days, no hour filter
&middot; a day with no files means the app was not running that day, it is not a zero</p>
<h2 style="font-size:15px;color:#58a6ff;margin:22px 0 8px">DAY BY DAY</h2>
<table style="width:100%;border-collapse:collapse;font-size:13px">
<tr style="text-align:left;color:#8b949e">{tot}</tr>{''.join(tr)}</table>
<h2 style="font-size:15px;color:#58a6ff;margin:26px 0 8px">JUDGE LEADERBOARD ACROSS THESE DAYS</h2>
<p style="color:#8b949e;margin:0 0 8px">weighted by how many calls each judge was scored on -
this is the table the SUGG_WEIGHT / gate decision should read, never a single day</p>
<table style="width:100%;border-collapse:collapse;font-size:13px">
<tr style="text-align:left;color:#8b949e"><th>JUDGE</th><th>DAYS SEEN</th><th>CALLS</th>
<th>SCORED</th><th>ACCURACY</th></tr>{led}</table>
<p style="color:#8b949e;margin:14px 0 0">GRADED AT = when the auditor ran. A row marked
PARTIAL was graded before that day had ended, so it covers less than 24 hours - re-run it with
<code>python audit_day.py --date YYYY-MM-DD</code> to replace it.</p>
<p style="color:#8b949e;margin-top:18px">{len(audited_now)} day(s) were graded while building this
report: {", ".join(str(x) for x in audited_now) or "none"}.
Days that exist on disk but are not in this window are still one command away:
<code>python audit_day.py --date YYYY-MM-DD</code>.
The per-day visual report (all 15 tests, judges, what-if) stays at
<code>data/day_report_&lt;date&gt;.html</code>.</p>
</div></body></html>"""


def days_report(n_days, end=None, refresh=True, force=False):
    """Roll-up of the last N calendar days: per-day rows + totals + judge leaderboard.

    The operator's view: "what happened on that day", "how did the week go". Never
    filters by hour. Days with no files are printed as empty rows instead of being
    dropped, so an app that was off is visible as an off day.
    """
    n_days = max(1, min(90, int(n_days)))
    OUT.clear()                      # this process may have graded days already
    today = datetime.now(BUDA).date()
    if isinstance(end, str) and end:
        try:
            end = datetime.strptime(end, "%Y-%m-%d").date()
        except ValueError:
            print(f"[error] --date needs YYYY-MM-DD, got {end!r}")
            return 2
    end = end if isinstance(end, date_cls) else today
    days = [end - timedelta(days=i) for i in range(n_days - 1, -1, -1)]

    rule()
    say(f" GOLD-BOOKMAP ROLL-UP - last {n_days} calendar days: {days[0]:%Y-%m-%d} .. {days[-1]:%Y-%m-%d}")
    say(f" whole days, no hour filter | per-day detail stays in data/day_report_<date>.html")
    rule()

    audited_now, rows = [], []
    for d in days:
        src = _rollup_src(d)
        has_trace = src["log"] or src["archive"] or d == today
        if refresh and (force or not src["metrics"]) and has_trace:
            print(f"[{d:%Y-%m-%d}] {'re-grading on the whole-day rule' if force else 'not graded yet'}"
                  f" -> grading it now (the tape for that day is read once; ~1-3 min on a busy day)")
            buf = io.StringIO()
            try:
                with contextlib.redirect_stdout(buf):
                    rc = main(["--date", f"{d:%Y-%m-%d}", "--no-index", "--no-browser"])
            except Exception as e:
                print(f"[{d:%Y-%m-%d}] audit crashed: {e} - row left empty, other days continue")
                rc = 99
            seen = set()
            for line in buf.getvalue().splitlines():
                if line.startswith(("VERDICT ", "[saved]", "[warn]")) and line not in seen:
                    seen.add(line)
                    print("   " + line)
            if rc in (0, 1):
                audited_now.append(d)
        rows.append(_rollup_row(d, today))

    head = (f" {'DAY':<12} {'GRADES':<13} {'PRINTS':>10} {'MBO LINES':>13} {'DECIS':>6} "
            f"{'MT5':>4} {'WHAT-IF':>8} {'WR':>4}  {'BEST JUDGE':<18} {'WORST JUDGE':<18} "
            f"{'COV%':>5}  {'GRADED SPAN':<21} {'SRC'}")
    say("")
    say(head)
    say(" " + "-" * (len(head) - 1))
    for r in rows:
        say(f" {r['date']:<12} {r['grades']:<13} {r['prints']:>10} {r['mbo']:>13} "
            f"{r['decis']:>6} {r['mt5']:>4} {r['whatif']:>8} {r['wr']:>4}  "
            f"{r['best']:<18} {r['worst']:<18} {r['cov']:>5}  {r['span']:<21} {r['src']}")
    graded = [r for r in rows if r["_m"]]
    empty_days = [r["date"] for r in rows if r["src"] == "-"]

    say("")
    say(" PER-DAY NOTES (when it was graded, and what the day said)")
    for r in rows:
        if r["src"] == "-":
            say(f"   {r['date']}  {r['note']}")
        else:
            say(f"   {r['date']}  {r['prov']}")
            if r["note"]:
                say(f"              {r['note']}")
    if any(r.get("partial") for r in rows):
        say("   PARTIAL = graded before that day had ended, so it covers less than 24 h."
            " Re-run the day to replace it:  python audit_day.py --date <date>")

    say("")
    say(" TOTALS AND AVERAGES")
    whole_rows = [r for r in graded if r["span"].startswith("whole-day")]
    if graded and len(whole_rows) != len(graded):
        say(f"   graded on the whole day      : {len(whole_rows)} of {len(graded)}"
            f"   -> the others still carry the old 08:00-23:00 numbers:")
        say("                                   re-grade them in one go:  "
            "python audit_day.py --days %d --refresh" % n_days)
    say(f"   days in the window        : {len(rows)}")
    say(f"   days with data + a report : {len(graded)}"
        + (f"   (empty: {', '.join(empty_days)})" if empty_days else ""))
    if graded:
        pr = sum(int((r['_m'] or {}).get('feed_prints', 0) or 0) for r in graded)
        mb = sum(int((r['_m'] or {}).get('feed_mbo', 0) or 0) for r in graded)
        de = sum(int((r['_m'] or {}).get('decisions', 0) or 0) for r in graded)
        orr = sum(int((r['_m'] or {}).get('orders_to_mt5', 0) or 0) for r in graded)
        pf = sum(int(((r['_m'] or {}).get('counts') or {}).get('PASS', 0) or 0) for r in graded)
        fl = sum(int(((r['_m'] or {}).get('counts') or {}).get('FAIL', 0) or 0) for r in graded)
        wi = [float((r['_m'] or {}).get('whatif', {}).get('total_pts'))
              for r in graded if (r['_m'] or {}).get('whatif')]
        say(f"   trade prints over the span: {pr:,}   (MBO order lines: {mb:,})")
        say(f"   decisions: {de:,}   ->   signals sent to MT5: {orr:,}")
        say(f"   tests: {pf} PASS / {fl} FAIL totalled over the graded days")
        if wi:
            say(f"   what-if: {sum(wi):+.1f} pts over {len(wi)} graded day(s), "
                f"average {sum(wi) / len(wi):+.1f} pts/day")
        worst = min(graded, key=lambda r: float((r['_m'] or {}).get('feed_prints', 0) or 0))
        say(f"   thinnest tape: {worst['date']} with {worst['prints']} prints "
            f"-> the day to read the judges on with care")
    judges = _judge_rollup(graded)
    if judges:
        say("")
        say(" JUDGE LEADERBOARD ACROSS THESE DAYS (weighted by scored calls)")
        say(f"   {'JUDGE':<20} {'DAYS':>4} {'CALLS':>7} {'SCORED':>7} {'ACCURACY':>9}")
        for j, dd, vv, ss, aa in judges[:12]:
            say(f"   {j:<20} {dd:>4} {vv:>7,} {ss:>7,} {aa:>8.1f}%")
        if len(graded) < 3:
            say("   NOTE: fewer than 3 graded days - do NOT retune weights or the gate on this yet.")
    say("")
    if audited_now:
        say(f" graded while building this report: {', '.join(str(x) for x in audited_now)}")
    say(" every row above is a whole calendar day; nothing was cut by hour.")
    say(" for one day in detail:  python audit_day.py --date 2026-09-23")
    say(" for the old 08:00-23:00 view only:  python audit_day.py 2026-09-23 --window")

    out_txt = DATA_DIR() / f"last{n_days}days_report.txt"
    out_html = DATA_DIR() / f"last{n_days}days_report.html"
    try:
        DATA_DIR().mkdir(parents=True, exist_ok=True)
        out_txt.write_text("\n".join(OUT) + "\n", encoding="utf-8")
        out_html.write_text(_rollup_html(rows, judges, n_days, days[0], days[-1],
                                         audited_now, empty_days), encoding="utf-8")
        print(f"[saved] {out_txt}")
        print(f"[saved] {out_html}   <- double-click: the day-by-day + judge table as a page")
    except Exception as e:
        print(f"[warn] could not save the roll-up: {e}")
    try:
        if write_index_page():
            print(f"[saved] {DATA_DIR() / 'index.html'}   <- every audited day, one card each")
    except Exception:
        pass
    print(f"ROLLUP last {n_days} days: {len(graded)} day(s) with data, {len(empty_days)} empty, "
          f"{sum(int(((r['_m'] or {}).get('counts') or {}).get('FAIL', 0) or 0) for r in graded)} "
          f"FAILed test(s) total")
    return 0 if graded else 1


def _insight():
    """The learning layer (tools/insight.py). Absent = the day still grades, minus pages.

    Looked for next to THIS file first (the auditor audits other folders: --dir, --demo),
    then inside the audited project. Same rule the dashboard loader uses, so the two
    layers can never disagree about whether the install is complete.
    """
    import importlib
    here = Path(__file__).resolve().parent
    for cand in (here / "tools", here, _root() / "tools", _root()):
        if not (cand / "insight.py").exists():
            continue
        try:
            if str(cand) not in sys.path:
                sys.path.insert(0, str(cand))
            return importlib.import_module("insight")
        except Exception as e:
            print(f"[warn] insight layer found at {cand} but will not load ({e}) - "
                  f"decisions/trades pages skipped")
            return None
    print("[warn] insight layer not loaded (tools/insight.py is neither next to "
          "audit_day.py nor in the audited folder) - decisions/trades pages skipped")
    return None


def _minute_prices(day):
    """Minute prices for a day, cached in data/prices_<yyyymmdd>.json.

    The tape is ~1 GB a day; the multi-day modes would otherwise re-read all of it on
    every run. The cache is the price series only (a few hundred KB), so it is cheap to
    keep and cheap to rebuild when the tape changes.

    A cache that is merely *present* is not a cache you can trust: during a live day the
    tape keeps growing, and serving the 09:00 snapshot at 15:00 would quietly strip the
    "what happened next" columns from every later decision. So the cache records the
    newest tape mtime it was built from, and is only reused when the tape has not been
    written since. On a live day that means one extra pass over today's tape - the price
    of never showing a number the tape no longer supports.
    """
    cache = DATA_DIR() / f"prices_{day:%Y%m%d}.json"
    try:
        newest = max((os.path.getmtime(f) for f in all_ticks_files(day)), default=0.0)
    except Exception:
        newest = 0.0
    if cache.exists():
        try:
            raw = json.loads(cache.read_text(encoding="utf-8"))
            built = float(raw.get("tape_mtime", -1))
            pts = raw.get("points")
            if pts and built >= newest:
                return [(datetime.fromtimestamp(e, tz=timezone.utc), float(p)) for e, p in pts]
        except Exception:
            pass
    ticks, _p, _s = load_ticks(day)
    if not ticks:
        return []
    per = {}
    for t in ticks:
        per[t[0].replace(second=0, microsecond=0)] = float(t[1])
    series = sorted(per.items())
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({
            "day": f"{day:%Y-%m-%d}", "built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tape_mtime": newest, "points": [[int(dt.timestamp()), p] for dt, p in series]}),
            encoding="utf-8")
    except Exception:
        pass
    return series


def _diary_days(limit=21):
    """[(day, diary rows)] for the days the master diary (and the per-day mirrors) hold."""
    today = datetime.now(BUDA).date()
    grouped = {}
    p = DATA_DIR() / "snapshots_history.jsonl"
    if p.exists():
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    dt = parse_ts(str(o.get("ts") or o.get("time") or o.get("timestamp") or ""))
                    if not dt:
                        continue
                    o["_dt"] = dt
                    grouped.setdefault(dt.astimezone(BUDA).date(), []).append(o)
        except Exception as e:
            print(f"[warn] diary read failed: {e}")
    for f in sorted(DATA_DIR().glob("diary_*.jsonl")):
        m = re.search(r"(\d{8})", f.name)
        if not m:
            continue
        try:
            d = datetime.strptime(m.group(1), "%Y%m%d").date()
        except Exception:
            continue
        if grouped.get(d):
            continue                      # the master already carries this day
        rows = []
        try:
            with open(f, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    dt = parse_ts(str(o.get("ts") or o.get("time") or o.get("timestamp") or ""))
                    if dt and dt.astimezone(BUDA).date() == d:
                        o["_dt"] = dt
                        rows.append(o)
        except Exception:
            continue
        if rows:
            grouped[d] = rows
    days = sorted(grouped)
    if limit:
        days = [d for d in days if d >= today - timedelta(days=limit)]
    return [(d, grouped[d]) for d in days]


def _multi_day_data(fold=3):
    """The rows the A/B and the walk-forward work on, plus a note about missing tape."""
    ins = _insight()
    if ins is None:
        return None, "insight layer not available"
    out, skipped = [], []
    for day, rows in _diary_days(max(7, fold + 2)):
        series = _minute_prices(day)
        if not series:
            skipped.append(str(day))
            continue
        rr = ins.panel_rows(day, rows, series)
        if rr:
            out.append((str(day), rr))
    return out, skipped


def _as_dict(x, key="position_ids"):
    """Readers meet dict / list / None / garbage. None of them may kill the report."""
    if isinstance(x, dict):
        return x
    if isinstance(x, (list, tuple)):
        return {key: list(x)}
    return {}


def _safe_test(no, name, fn, *a, **kw):
    """Run one of the 15 tests; if it explodes, grade it WARN and keep the rest alive.

    An auditor that dies on one odd file prints NOTHING about the other 14 - which is
    exactly the day you needed the report most.  Nothing is skipped silently: the
    failed test stays in the scoreboard with the real exception text in it.
    """
    try:
        res = fn(*a, **kw)
        if isinstance(res, tuple) and len(res) == 2:
            r, payload = res
        else:
            r, payload = res, {}
        return r, (payload if payload is not None else {})
    except Exception as e:
        r = Report(no)
        r.add("WARN", name, "could not be graded - the auditor survived the error",
              [f"internal error: {type(e).__name__}: {e}",
               "the other 14 tests still graded; nothing was skipped silently",
               "usual cause: a data file in an unexpected shape - check "
               "data/tracked_bot_positions.json, data/mt5_signal.txt, the tape header",
               "to chase it: python audit_day.py --date <day> and read the traceback"])
        return r, {}


def _load_run_config(day):
    """The config the ROBOT recorded for that day (data/run_config_<yyyymmdd>.json).

    This is what makes a grade mean the same thing forever: if you change .env later,
    the old day is still graded with the rule it really ran under."""
    for nm in (f"run_config_{day:%Y%m%d}.json", f"run_config_{day:%Y-%m-%d}.json"):
        p = DATA_DIR() / nm
        if p.exists():
            j = read_json(p)
            if isinstance(j, dict):
                j["_path"] = str(p)
                return j
    return None


def _apply_run_config(day, args):
    """Grade the day with the day's own gate, and say when today's .env differs."""
    rc = _load_run_config(day)
    if not rc:
        return None
    used, diff = [], []
    for env_key, gname, label in (("CONFIDENCE_THRESHOLD", "CONF_MIN", "gate"),
                                  ("AI_MIN_SIGNAL_STRENGTH", "AI_MIN", "AI gate"),
                                  ("V6_CFD_SPREAD_MAX", "SPREAD", "spread cap")):
        rec = rc.get(env_key)
        if rec in (None, ""):
            continue
        def _differs(a, b):
            try:
                return abs(float(a) - float(b)) > 1e-9      # 0.5 == 0.50: same rule
            except Exception:
                return str(a).strip() != str(b).strip()
        if _differs(rec, ENV.get(env_key)):
            diff.append(f"{label} {rec} (day) vs {ENV.get(env_key)} (today's .env)")
        if getattr(args, "gate", None) and env_key == "CONFIDENCE_THRESHOLD":
            continue                      # an explicit --gate is a deliberate A/B run
        try:
            globals()[gname] = float(rec)
            used.append(f"{label} {rec}")
        except Exception:
            pass
    METRICS["run_config"] = rc
    METRICS["gate_source"] = f"recorded by the robot for {day:%Y-%m-%d}"
    METRICS["gate_today_env"] = ENV.get("CONFIDENCE_THRESHOLD")
    note = (f" config    : graded with the rule the day RAN with ({', '.join(used) or 'nothing recorded'})"
            f" - from {Path(rc['_path']).name}")
    if diff:
        note += " | today's .env differs: " + "; ".join(diff) + " (use --gate / --env to A/B it)"
    return note


def main(argv=None):
    ap = argparse.ArgumentParser(description="Grade one day of the Gold-BookMap robot.")
    ap.add_argument("--date", help="YYYY-MM-DD (default: yesterday, Budapest)")
    ap.add_argument("--out", help="also write the report to this file")
    ap.add_argument("--latest", action="store_true",
                    help="audit the newest day that has a log (no need to remember dates)")
    ap.add_argument("--html", help="write a styled report here (default: data/day_report_<date>.html)")
    ap.add_argument("--no-html", action="store_true", help="skip the HTML report")
    ap.add_argument("--no-index", action="store_true", help="skip rewriting data/index.html")
    ap.add_argument("--no-browser", action="store_true",
                    help="do not pop the dashboard open in the browser (used by daily_check.sh)")
    ap.add_argument("--open", action="store_true",
                    help="just open data/index.html in the browser and exit (no audit)")
    ap.add_argument("--dir", help="project folder if this file is not inside it "
                                   "(Git Bash style: --dir /a/gitHub/Gold-BookMap)")
    ap.add_argument("--demo", action="store_true",
                    help="grade a synthetic complete day - proves the auditor works; "
                         "needs no robot files at all")
    ap.add_argument("--env", help="grade against another .env (e.g. .env.new) - the "
                                   "robot must actually have used it, this only re-reads numbers")
    ap.add_argument("--gate", help="override CONFIDENCE_THRESHOLD for an A/B run "
                                   "(e.g. --gate 50 to ask 'what if the gate were lower')")
    ap.add_argument("--keep", action="store_true",
                    help="with --demo: keep the demo folder instead of deleting it")
    ap.add_argument("--explain", metavar="HH:MM", default=None,
                        help="one decision in full (nearest HH:MM, Budapest): the panel that "
                             "stood behind it, and what the tape did next")
    ap.add_argument("--no-pages", action="store_true",
                        help="do not write the decisions/trades HTML pages for the day")
    ap.add_argument("--weight-ab", nargs="?", const=3, type=int, metavar="N", default=None,
                        help="compare judge-weight sets on the last N days (default 3)")
    ap.add_argument("--walk-forward", type=int, metavar="N", default=0,
                        help="learn on N days, test on the next, rolling - no peeking forward")
    ap.add_argument("--open-html", dest="open_html", metavar="PATH",
                    help="open one report file in the browser, run nothing else "
                         "(daily_check.sh uses this so the tab always appears)")
    ap.add_argument("--list-days", action="store_true",
                    help="show which days can be audited and where their data lives, then exit")
    ap.add_argument("--deep", action="store_true",
                    help="with --list-days: count archived trade prints too (slower)")
    ap.add_argument("--window", action="store_true",
                    help="grade ONLY the trading window (BUDAPEST_START..END) instead of the "
                         "whole calendar day - the old behaviour, off by default")
    ap.add_argument("--days", type=int, metavar="N",
                    help="roll-up of the last N calendar days (e.g. --days 3, --days 8): "
                         "one report, per-day rows + totals + judge leaderboard")
    ap.add_argument("--refresh", action="store_true",
                    help="with --days: re-grade EVERY day in the window (use it once after "
                         "installing a build that grades differently, e.g. whole-day instead "
                         "of 08:00-23:00)")
    ap.add_argument("--no-refresh", dest="no_refresh", action="store_true",
                    help="with --days: use only the days that were graded before, do not "
                         "grade the missing ones now")
    ap.add_argument("--from", dest="t_from", metavar="HH:MM",
                    help="grade only this part of the day, e.g. --from 14:00 --to 18:00 (London PM / NY open)")
    ap.add_argument("--to", dest="t_to", metavar="HH:MM",
                    help="end of the --from window (Budapest local)")
    args = ap.parse_args(argv)

    # --days: the "past 3 days / past 8 days" view. Needs the project folder only.
    if getattr(args, "days", None):
        if getattr(args, "dir", None):
            globals()["ROOT"] = Path(args.dir).expanduser().resolve()
        return days_report(int(args.days), end=args.date,
                           refresh=not getattr(args, "no_refresh", False),
                           force=getattr(args, "refresh", False))

    if getattr(args, "list_days", False):
        return list_days(deep=getattr(args, "deep", False))

    if getattr(args, "open_html", None):
        f = Path(args.open_html)
        if not f.exists():
            print(f"[warn] not there yet: {f} - run the audit first")
            return 1
        print("[ok] opened " + str(f) if _open_in_browser(f) else
              "[info] no browser reachable from here - double-click " + str(f))
        return 0

    if getattr(args, "open", False):
        # no need to re-parse .env for a browse-only action; --dir still points at the folder
        if args.dir:
            globals()["ROOT"] = Path(args.dir).expanduser().resolve()
        idx = DATA_DIR() / "index.html"
        if not idx.exists():
            idx2 = write_index_page()
            idx = idx2 or idx
        if Path(idx).exists():
            print(f"[open] {idx}")
            if not _open_in_browser(idx):
                print("[info] could not reach a browser - double-click that file instead")
            return 0
        print("[warn] no index page yet - run:  python audit_day.py --latest")
        return 1

    global ROOT, CONF_MIN, AI_MIN, SUMMARY_ONE_LINER
    if args.env:
        f = Path(args.env).expanduser()
        if not f.is_absolute():
            f = (Path(args.dir).expanduser() if args.dir else BASE_DIR_FOR_ENV) / args.env
        if not f.exists():
            print(f"[error] --env file not found: {f}")
            return 2
        # the settings block was read at import time from the real .env; re-read it
        _isolate_env(f)
    if args.gate:
        try:
            CONF_MIN = float(args.gate)
        except ValueError:
            print(f"[error] --gate needs a number, got {args.gate!r}")
            return 2
    if args.dir:
        ROOT = Path(args.dir).expanduser().resolve()
        if not ROOT.exists():
            print(f"[error] --dir does not exist: {ROOT}")
            return 2
    # WHOLE-DAY GRADING IS THE DEFAULT. The bridge records around the clock, so a day
    # file carries rows the robot never trades; an hour window is one question among many,
    # not the frame everything must fit into. Nothing is cut unless it was asked for:
    # --window (the trading hours only) or --from/--to (an ad-hoc drill).
    if not (args.t_from or args.t_to or getattr(args, "window", False)):
        globals()["WINDOW_START_H"], globals()["WINDOW_END_H"] = 0, 24

    if args.demo:
        return run_demo(keep=args.keep)

    if args.latest:
        cands = sorted(LOGS_DIR().glob("trading_*.log"), key=lambda q: q.stem, reverse=True)
        if cands:
            stem = cands[0].stem.replace("trading_", "")
            try:
                day = datetime.strptime(stem, "%Y%m%d").date()
            except ValueError:
                day = (datetime.now(BUDA) - timedelta(days=1)).date()
        else:
            day = (datetime.now(BUDA) - timedelta(days=1)).date()
    elif args.date:
        day = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        day = (datetime.now(BUDA) - timedelta(days=1)).date()

    OUT.clear()                      # one audit = one report, also when called in a loop
    SUMMARY_ONE_LINER = ""           # never let a previous day's verdict survive into this one
    rule()
    _rc_note = _apply_run_config(day, args)   # BEFORE the header prints the gate
    say(f" GOLD-BOOKMAP DAY AUDIT - {day:%Y-%m-%d} (Budapest) - 15 tests")
    # the header must state the frame the numbers really use - --from/--to used to
    # print the old 08:00-23:00 text while grading a different slice of the day
    if args.t_from or args.t_to:
        _span_txt = (f"hours {(args.t_from or '00:00')}-{(args.t_to or '24:00')} local"
                     f" (time filter, not the whole day)")
    elif WINDOW_START_H == 0 and WINDOW_END_H >= 24:
        _span_txt = "WHOLE DAY, no hour filter (00:00-24:00 local)"
    else:
        _span_txt = f"trading window {WINDOW_START_H:02d}:00-{WINDOW_END_H:02d}:00 local"
    say(f" grading {_span_txt} | conf gate {fmt(CONF_MIN,0)}% | AI gate {fmt(AI_MIN,0)} | spread cap {fmt(SPREAD,2)} pts")
    say(f" project {_root()}")
    say(f" python {sys.version.split()[0]} | {datetime.now(BUDA):%Y-%m-%d %H:%M:%S %Z}")
    if _rc_note:
        say(_rc_note)
    rule()
    METRICS["report_header"] = "\n".join(OUT)

    def _load_safe(label, fn, dflt):
        try:
            return fn()
        except Exception as e:
            print(f"[warn] {label} could not be read: {type(e).__name__}: {e} -> graded as empty")
            return dflt

    log_path, text = _load_safe("log", lambda: log_text_for(day), (None, ""))
    decisions, dpath = _load_safe("decisions", lambda: load_decisions(day),
                                  ([], DATA_DIR() / "decisions_log.csv"))
    diary, epath = _load_safe("diary", lambda: load_diary(day),
                              ([], DATA_DIR() / "snapshots_history.jsonl"))
    ticks, ticks_path, scanned = _load_safe("tape", lambda: load_ticks(day), ([], None, 0))
    tick_files = _load_safe("tape files", lambda: all_ticks_files(day), [])
    _mbo = _load_safe("mbo", lambda: load_mbo(day),
                      {"path": None, "total": 0, "window": 0, "extras": [],
                       "capped_in": None, "bad": []})
    mbo_path, mbo_n, mbo_extra, mbo_capped = _mbo["path"], _mbo["total"], _mbo["extras"], _mbo["capped_in"]
    mbo_win, mbo_bad = _mbo["window"], _mbo["bad"]

    # ---- the learning modes -------------------------------------------------
    ins = None if args.no_pages and not (args.explain or args.weight_ab or args.walk_forward) else _insight()

    if args.explain:
        if ins is None:
            print("[fail] the insight layer is missing - re-copy tools/insight.py from the kit")
            return 1
        series = _minute_prices(day)
        text = ins.explain(day, args.explain, decisions, diary, series)
        print(text)
        out = DATA_DIR() / f"explain_{day:%Y-%m-%d}_{args.explain.replace(':', '')}.txt"
        try:
            out.write_text(text, encoding="utf-8")
            print(f"[saved] {out}")
        except Exception as e:
            print(f"[warn] could not save the explain text: {e}")
        return 0

    if args.weight_ab is not None or args.walk_forward:
        if ins is None:
            print("[fail] the insight layer is missing - re-copy tools/insight.py from the kit")
            return 1
        n = args.weight_ab if args.weight_ab is not None else args.walk_forward
        data, skipped = _multi_day_data(fold=n)
        if data is None:
            print(f"[fail] {skipped}")
            return 1
        stamp = f"{data[0][0]}_to_{data[-1][0]}" if data else "none"
        if args.weight_ab is not None:
            text, tbl = ins.weight_ab(data)
            name = f"weights_ab_{stamp}"
        else:
            text, tbl = ins.walk_forward(data, fold=args.walk_forward)
            name = f"walkforward_{stamp}"
        print(text)
        if skipped:
            print(f" (skipped {len(skipped)} day(s) with no tape left on disk: {', '.join(skipped)})")
        try:
            (DATA_DIR() / f"{name}.txt").write_text(text, encoding="utf-8")
            print(f"[saved] {DATA_DIR() / (name + '.txt')}")
        except Exception as e:
            print(f"[warn] could not save: {e}")
        return 0

    # --from/--to: cut every time-based source to one part of the day
    if args.t_from or args.t_to:
        def _hm(v, dflt):
            if not v:
                return dflt
            h, _, m = v.partition(":")
            return int(h) * 60 + int(m or 0)
        lo = _hm(args.t_from, WINDOW_START_H * 60)
        hi = _hm(args.t_to, WINDOW_END_H * 60)
        def _min(dtv):
            l = dtv.astimezone(BUDA)
            return l.hour * 60 + l.minute
        def _rec_dt(r):
            d = r.get("_dt") if isinstance(r, dict) else None
            return d or parse_ts(str(r.get("ts") or r.get("time") or "")) or EPOCH
        ticks = [t for t in ticks if lo <= _min(t[0]) < hi]
        decisions = [x for x in decisions if lo <= _min(_rec_dt(x)) < hi]
        diary = [x for x in diary if lo <= _min(_rec_dt(x)) < hi]
        globals()["WINDOW_START_H"] = lo // 60
        globals()["WINDOW_END_H"] = max(globals()["WINDOW_START_H"] + 1, (hi + 59) // 60)
        METRICS["time_filter"] = f"{lo // 60:02d}:{lo % 60:02d}-{(hi // 60):02d}:{hi % 60:02d}"
        say("")
        say(f" TIME FILTER: only {lo // 60:02d}:{lo % 60:02d}-{hi // 60:02d}:{hi % 60:02d} Budapest"
            f" -> {len(ticks):,} prints, {len(decisions)} decisions, {len(diary)} diary records graded")
        say(f"   (log coverage in test 1 still reflects the whole file; a short session is expected here)")
    say("")
    say(" FILES IT IS READING")
    say(f"   log        : {log_path}")
    say(f"   ticks      : {ticks_path}  ({len(ticks):,} trade prints for this day, {scanned:,} rows scanned)")
    ticks_inwin = sum(1 for t_ in ticks
                      if WINDOW_START_H <= t_[0].astimezone(BUDA).hour < WINDOW_END_H)
    if ticks_inwin != len(ticks):
        say(f"                of which {ticks_inwin:,} inside {WINDOW_START_H:02d}:00-"
            f"{WINDOW_END_H:02d}:00 local, {len(ticks) - ticks_inwin:,} outside it")
    if len(tick_files) > 1:
        say(f"                + {len(tick_files) - 1} rotated chunk(s) also read: "
            + ", ".join(f.name for f in tick_files[1:]))
    say(f"   mbo        : {mbo_path}  ({mbo_n:,} order lines this day)")
    if mbo_extra:
        say(f"                + {len(mbo_extra)} rotated chunk(s) also read (or looked for): "
            + ", ".join(f.name for f in mbo_extra[:4]))
    if mbo_capped:
        say(f"   !! MBO COUNT TRUNCATED at {mbo_n:,} (MBO_MAX_LINES cap) inside {mbo_capped}; "
            f"the rest of the day was not read")
        say(f"      -> this is my limit, not a gap in your recording. Unset MBO_MAX_LINES "
            f"in .env to count the whole day.")
    # only meaningful when a window is actually being graded: in whole-day mode every row
    # is inside it, and "the rest is the night session" would be a self-contradiction
    if (WINDOW_START_H, WINDOW_END_H) != (0, 24):
        say(f"                of which {mbo_win:,} inside your "
            f"{WINDOW_START_H:02d}:00-{WINDOW_END_H:02d}:00 local window - the rest is the "
            f"night session, same calendar day, not the hours you trade")
        if mbo_win * 2 < mbo_n:
            say(f"      -> {mbo_n - mbo_win:,} of {mbo_n:,} mbo rows sit outside the window; "
                f"the bridge keeps recording after 23:00 and before 08:00")
    for b_ in mbo_bad:
        say(f"   !! MBO CHUNK UNREADABLE: {b_} - those rows are NOT in the count above")
    say(f"   decisions  : {dpath}  ({len(decisions)} rows this day)")
    say(f"   diary      : {epath}  ({len(diary)} snapshots this day)")

    DAY_COUNTS.clear()
    DAY_COUNTS.update({"decisions": len(decisions), "diary": len(diary),
                       "prints": len(ticks), "dups_dropped": TAPE_DUPLICATES})
    reports = {}
    reports["alive"], alive = _safe_test(1, "ALIVE CHECK", test_1_alive, day, log_path, text, decisions)
    payloads = {}
    reports["feed"], feed = _safe_test(2, "FEED CHECK", test_2_feed, day, ticks, ticks_path,
                                       scanned, mbo_n, alive, mbo_win)
    reports["pipe"], _pipe = _safe_test(3, "PIPELINE CHECK", test_3_pipeline, text, decisions)
    reports["dq"], _ = _safe_test(4, "DATA QUALITY CHECK", test_4_data_quality, day, ticks, text)
    reports["guards"], guards = _safe_test(5, "GUARD CHECK", test_5_guards, text, decisions, feed)
    reports["sig"], sig = _safe_test(6, "SIGNAL CHECK", test_6_signals, decisions, diary)
    reports["whatif"], _whatif = _safe_test(7, "WHAT-IF CHECK", test_7_what_if, day, ticks,
                                            decisions, log_path)
    reports["teams"], _ = _safe_test(8, "TEAM/JUDGE CHECK", test_8_teams, diary)
    try:
        candles = build_candles(ticks)
    except Exception as _ce:
        print(f"[warn] candle build failed ({type(_ce).__name__}: {_ce}) -> judge clock left empty")
        candles = []
    reports["judges"], _judges = _safe_test(9, "JUDGE PANEL", test_9_judges, day, diary, ticks, candles)
    reports["cov"], _ = _safe_test(10, "DIARY COVERAGE", test_10_judge_coverage, day, diary)
    reports["ver"], _ = _safe_test(11, "VERSION CHECK", test_11_version_config, text, day)
    reports["lat"], _lat = _safe_test(12, "SPEED CHECK", test_12_latency, text)
    reports["money"], _ = _safe_test(13, "MONEY CHECK", test_13_money, day, decisions)
    reports["files"], _ = _safe_test(14, "EVIDENCE CHECK", test_14_files, day)
    reports["hour"], _hour = _safe_test(15, "HOUR CHECK", test_15_hourly, day, ticks, decisions)

    # ---- machine-readable day record: the trend table reads these, never the text
    METRICS.update({
        "date": f"{day:%Y-%m-%d}",
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "feed_prints": (feed or {}).get("n", 0),
        "feed_mbo": (feed or {}).get("mbo", 0),
        "feed_coverage_pct": round((feed or {}).get("cov", 0.0), 1),
        "decisions": len(decisions),
        "diary_records": len(diary),
        "log_lines": (alive or {}).get("lines", 0),
        "hours_awake": round((alive or {}).get("span_h", 0.0), 1),
        "biggest_silence_min": round((alive or {}).get("gap_max", 0.0) / 60.0, 1),
        "orders_to_mt5": (guards or {}).get("orders", 0),
        "orders_deferred": (guards or {}).get("deferred", 0),
        "guard_reasons": [f"{k} x{v}" for k, v in ((guards or {}).get("reasons") or Counter()).most_common(5)],
        "conf_over_gate": (sig or {}).get("over_conf", 0),
        "cycles": (_pipe or {}).get("cycles", 0),
        "errors": (_pipe or {}).get("errors", 0),
        "median_ms": round((_lat or {}).get("median", 0.0)) if _lat else 0,
        "version_mismatch": 1 if any("MISMATCH" in " ".join(w) for _g, _n, _h, w in reports["ver"].rows) else 0,
        "grades": {f"{i+1}": g for i, rep in enumerate(
            [reports[k] for k in ("alive", "feed", "pipe", "dq", "guards", "sig", "whatif",
                                  "teams", "judges", "cov", "ver", "lat", "money", "files", "hour")])
            for g in [rep.rows[0][0]]},
        "gate_used": CONF_MIN, "ai_gate_used": AI_MIN, "spread_cap": SPREAD,
        "graded_span": ("whole-day 00:00-24:00" if WINDOW_START_H == 0 and WINDOW_END_H >= 24
                        else f"{WINDOW_START_H:02d}:00-{WINDOW_END_H:02d}:00"),
        "hours_without_tape_app_off": (feed or {}).get("app_off_hours", 0),
        "hourly": _hour or {},
    })

    say("")
    rule()
    say(" SCOREBOARD")
    rule()
    say(f" {'TEST':<4} {'GRADE':<6} {'WHAT IT ANSWERS'}")
    i = 0
    tot = Counter()
    for k, rep in reports.items():
        for grade, name, headline, why in rep.rows:
            i += 1
            tot[grade] += 1
            say(f" {i:<4} {grade:<6} {name:<20} {headline}")
    say("")
    say(f" PASS {tot['PASS']}  |  WARN {tot['WARN']}  |  FAIL {tot['FAIL']}  |  NO-DATA {tot['NA']}")
    METRICS["counts"] = {g: int(tot.get(g, 0)) for g in ("PASS", "WARN", "FAIL", "NA")}
    _w = METRICS.get("whatif") or {}
    _j = METRICS.get("judges") or {}
    SUMMARY_ONE_LINER = (f"VERDICT {day:%Y-%m-%d}: pass {tot['PASS']} warn {tot['WARN']} fail {tot['FAIL']} "
                         f"nodata {tot['NA']} | feed {METRICS.get('feed_prints', 0):,} prints | "
                         f"{METRICS.get('decisions', 0)} decisions | {METRICS.get('orders_to_mt5', 0)} to MT5"
                         + (f" (+{METRICS['orders_deferred']} refused by guards)" if METRICS.get("orders_deferred") else "")
                         + " | "
                         f"whatif {_w.get('total_pts', 'n/a')} pts (WR {_w.get('win_rate', 'n/a')}%) | "
                         f"best judge {_j.get('best', 'n/a')} | worst {_j.get('worst', 'n/a')}")
    # stored AFTER it is built: writing it before meant a batch run (--days) put the
    # PREVIOUS day's verdict into this day's metrics file
    METRICS["verdict_line"] = SUMMARY_ONE_LINER
    say("")
    say(SUMMARY_ONE_LINER)
    exit_rc = 0 if (tot["FAIL"] == 0 and tot["NA"] == 0) else 1
    say("")
    say(" THE ANSWER IN ONE PARAGRAPH")
    fails = [r for r in reports.values() for g, n, h, w in r.rows if g == "FAIL"]
    nas = [r for r in reports.values() for g, n, h, w in r.rows if g == "NA"]
    if fails:
        say(f"   The robot was switched on, but the day cannot be called a test of the")
        say(f"   strategy: {len(fails)} core check(s) failed. Fix the feed/evidence first,")
        say(f"   then re-run one full day and re-audit. Judging signals now would be")
        say(f"   grading a blindfolded player.")
    elif nas:
        say(f"   Mechanics look alive; {len(nas)} deeper checks still have no data")
        say(f"   (mostly the snapshot diary). Run one more full day - those tests need")
        say(f"   a day of recording before they can say anything honest.")
    else:
        say(f"   A complete day with data, guards and scorecards. Now the strategy")
        say(f"   verdict in test 7 (what-if) and test 8 (teams/judges) is trustworthy.")
    _w2 = METRICS.get("whatif") or {}
    say("")
    say(f"   SYSTEM  : {'healthy' if tot['FAIL'] == 0 else 'NOT healthy'} - "
        f"{tot['PASS']} PASS / {tot['WARN']} WARN / {tot['FAIL']} FAIL / {tot['NA']} no-data. "
        f"Did the robot do its own job correctly?")
    _mt5n = METRICS.get("orders_to_mt5", 0)
    if _mt5n:
        _strat = (f"had skin in the game - {_mt5n} signal(s) reached MT5; the result is in "
                  f"test 13 and the trades page")
    elif _w2:
        _strat = (f"unproven - no live trade closed today. The what-if replay over "
                  f"{_w2.get('n', 0)} signal(s) says {_w2.get('total_pts', 0):+.1f} pts "
                  f"(WR {_w2.get('win_rate', 0):.0f}%): a simulation, not a result")
    else:
        _strat = "unproven - nothing to replay today"
    say(f"   STRATEGY: {_strat}")
    METRICS["paragraph"] = "\n".join(l.strip() for l in OUT[-8:] if re.match(r"^\s{2,}\S", l))
    say("")

    say("")
    rule()
    say(" BY HAND, IN THIS ORDER (2 minutes each, the machine cannot see these)")
    rule()
    say("   1. BookMap still logged in? chart open on GC 12-26 with a moving footprint?")
    say("      If the addon window was closed, nothing below matters.")
    say("   2. Is ticks.csv growing RIGHT NOW?  (dir ticks.csv twice, size must change)")
    say("      Addon writes it; the robot only reads it. Frozen file = dead bridge.")
    say("   3. Open the MT5 terminal: Tools -> Options -> Expert Advisors:")
    say("      'Allow algo trading' ON, and did the EA read the last signal file?")
    say("   4. MT5 -> History tab: any deal yesterday at all? (0 deals + 0 orders = never acted)")
    say(r"   5. Antivirus / OneDrive / compressed 'A:' drive - did they touch A:\gitHub\Gold-BookMap?")
    say("   6. Laptop sleep, screensaver, RDP disconnect during the day? (a gap in test 1 = yes)")
    say("   7. Gemini quota used today (AI Studio page) vs AI_MAX_CALLS_PER_DAY.")
    _cl = [l.strip() for l in OUT if re.match(r"^   \d+\. ", l)]
    METRICS["checklist"] = list(dict.fromkeys(_cl))
    say("")
    if args.out:
        Path(args.out).write_text("\n".join(OUT) + "\n", encoding="utf-8")
        print(f"\n[saved] {args.out}")
    # A filtered grade is an EXPERIMENT, not the day. It gets its own files so it can
    # never overwrite the whole-day card that the index and the trend table read.
    _sfx = ""
    if METRICS.get("time_filter"):
        _sfx = "_" + re.sub(r"[^0-9]", "", str(METRICS["time_filter"]))
    elif not (WINDOW_START_H == 0 and WINDOW_END_H >= 24):
        _sfx = f"_w{WINDOW_START_H:02d}{WINDOW_END_H:02d}"
    if _sfx:
        say(f" (filtered grade -> its files carry the suffix {_sfx}; the whole-day card stays as it is)")
    default_out = DATA_DIR() / f"day_audit_{day:%Y-%m-%d}{_sfx}.txt"
    try:
        default_out.parent.mkdir(parents=True, exist_ok=True)
        default_out.write_text("\n".join(OUT) + "\n", encoding="utf-8")
        print(f"[saved] {default_out}")
    except Exception as e:
        print(f"[warn] could not save report: {e}")
    try:                                     # machine-readable twin of the text report
        mp = DATA_DIR() / f"day_metrics_{day:%Y-%m-%d}{_sfx}.json"
        mp.parent.mkdir(parents=True, exist_ok=True)
        mp.write_text(json.dumps(METRICS, indent=1, default=str), encoding="utf-8")
        print(f"[saved] {mp}")
    except Exception as e:
        print(f"[warn] metrics json: {e}")
    to_open = None
    if not args.no_html:
        hp = Path(args.html) if args.html else (DATA_DIR() / f"day_report_{day:%Y-%m-%d}{_sfx}.html")
        try:
            hp.parent.mkdir(parents=True, exist_ok=True)
            hp.write_text(html_report(day, reports, METRICS), encoding="utf-8")
            print(f"[saved] {hp}")
            to_open = hp
        except Exception as e:
            print(f"[warn] html report: {e}")
    if not args.no_html and not getattr(args, "no_pages", False) and not _sfx:
        ins2 = _insight()
        if ins2 is not None:
            try:
                series = _minute_prices(day)
                if series:
                    dp = DATA_DIR() / f"decisions_{day:%Y-%m-%d}.html"
                    dp.write_text(ins2.decisions_page(day, decisions, diary, series), encoding="utf-8")
                    print(f"[saved] {dp}   <- every decision with the panel behind it and what happened next")
                    outs, _op = _outcomes_for_day(day)
                    tracked = _as_dict(read_json(DATA_DIR() / "tracked_bot_positions.json"))
                    tp = DATA_DIR() / f"trades_{day:%Y-%m-%d}.html"
                    truth, _why = _mt5_truth(day)
                    mt5_txt = (f"{truth['open_now']} open position(s) now, {truth['deals_total']} deal(s) "
                               f"that day, net {truth['pnl']:+.2f}" if truth else None)
                    tp.write_text(ins2.trades_page(day, decisions, outs, tracked, mt5_txt), encoding="utf-8")
                    print(f"[saved] {tp}   <- signal -> position -> close, or an honest 'nothing traded'")
                    if hasattr(ins2, "judges_page"):
                        gp = DATA_DIR() / f"judges_{day:%Y-%m-%d}.html"
                        gp.write_text(ins2.judges_page(day, diary, series, decisions), encoding="utf-8")
                        print(f"[saved] {gp}   <- every judge, every call it made, and what happened next")
            except Exception as e:
                print(f"[warn] decisions/trades pages: {type(e).__name__}: {e}")

    if _sfx:
        print("[index] skipped: this was a filtered grade - the day's whole-day card stays as it is")
    if not getattr(args, "no_index", False) and not _sfx:
        idx = write_index_page()
        if idx:
            print(f"[saved] {idx}   <- double-click this one any time, it lists every audited day")
        elif _dash() and not (DATA_DIR() / "index.html").exists():
            print("[warn] data/index.html not written (tools/dashboard.py problem?)")
    if to_open and not getattr(args, "no_browser", False):
        _open_in_browser(to_open)
    print(SUMMARY_ONE_LINER)
    return exit_rc


def run_demo(keep=False):
    """Self-test fixture. MT5 is deliberately NOT queried here: a fixture day has no real
    trades, and asking the live terminal during --check would print your real positions
    next to fake data."""
    globals()["SKIP_MT5"] = True
    """Self-grade a synthetic 15h day. No BookMap/MT5 files needed."""
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="bm_audit_demo_"))
    print(f"[demo] building a synthetic complete day in {tmp}")
    print("[demo] this is FAKE data - it tests the auditor, not the strategy")
    demo_dir = tmp / "Gold-BookMap"
    demo_dir.mkdir(parents=True)
    _make_demo_day(demo_dir)
    global ROOT
    ROOT = demo_dir
    rc = main(["--date", DEMO_DAY.isoformat(), "--dir", str(demo_dir),
               "--env", ".env", "--no-index"])
    ok = rc == 0
    # the demo also PROVES the visual layer really renders: a broken or missing
    # tools/dashboard.py used to slip through, because the auditor falls back to
    # plain text and still graded 15/15. Now the fallback counts as a failure.
    dash = DATA_DIR() / f"day_report_{DEMO_DAY:%Y-%m-%d}.html"
    if ok and dash.exists():
        h = dash.read_text(encoding="utf-8", errors="ignore")
        need = ("class=kpi", "id=rawtext", "bar2", "sec-jud", "<style>")
        miss = [n for n in need if n not in h]
        if _dash() is False or miss:
            print("[demo] FAIL: the dashboard layer did not render"
                  + (f" (missing {', '.join(miss)})" if miss else " (import failed)"))
            print("[demo] fix: re-copy tools/dashboard.py (31843 bytes) and dashboard.py (1226 bytes)")
            print("[demo]      from the kit, then clear stale bytecode:")
            print("[demo]      find . -name __pycache__ -type d -exec rm -rf {} +")
            ok = False
    elif ok:
        print("[demo] FAIL: no html report was written at", dash)
        ok = False
    if ok:
        pages = [DATA_DIR() / f"decisions_{DEMO_DAY:%Y-%m-%d}.html",
                 DATA_DIR() / f"trades_{DEMO_DAY:%Y-%m-%d}.html"]
        gone = [p.name for p in pages if not p.exists()]
        if gone:
            print("[demo] FAIL: the learning pages were not written: " + ", ".join(gone))
            print("[demo] fix: re-copy tools/insight.py from the kit (the day audit still "
                  "runs without it, but --explain and the two pages go quiet)")
            ok = False
        else:
            print("[demo] learning layer: decisions page + trades page written")
    print("[demo] verdict:", "auditor OK - every test produced a real grade, the dashboard rendered and the learning pages were written"
          if ok else "auditor ran but the day or the dashboard was not clean - see above")
    if keep:
        print(f"[demo] kept: {demo_dir}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)   # only now - the checks needed the files
    return 0 if ok else 1


DEMO_DAY = datetime(2099, 1, 5, tzinfo=timezone.utc).date()


def _make_demo_day(dst: Path):
    """Minimal but complete: ticks, decisions, diary, log. Used by --demo."""
    import random
    rnd = random.Random(7)
    (dst / "data").mkdir(parents=True, exist_ok=True)
    (dst / "logs").mkdir(parents=True, exist_ok=True)
    (dst / ".env").write_text(
        "BOOKMAP_WINDOW_SECONDS=10800\nBOOKMAP_MAX_DEPTH_LEVELS=20\n"
        "AI_MIN_SIGNAL_STRENGTH=6\nCONFIDENCE_THRESHOLD=50\n"
        "V6_CFD_SPREAD_MAX=0.50\nTRADING_ENABLED=1\n"
        # point the data paths INSIDE the demo tree, whatever the operator's real
        # .env says - otherwise the demo reads his live ticks.csv and finds nothing
        f"BOOKMAP_BRIDGE_FILE={dst / 'ticks.csv'}\n"
        f"BOOKMAP_MBO_FILE={dst / 'mbo.csv'}\n"
        "AI_MAX_CALLS_PER_DAY=2000\n", encoding="utf-8")
    ticks, price = [], 2032.0
    t = datetime(2099, 1, 5, 6, 0, tzinfo=timezone.utc)
    while t < datetime(2099, 1, 5, 21, 0, tzinfo=timezone.utc):
        price = max(1900.0, price + rnd.gauss(0, 0.30))
        ticks.append(f"{t.astimezone(BUDA).isoformat(timespec='milliseconds')},Last,{price:.4f},{rnd.randint(1, 40)},,,GC 12-26")
        t += timedelta(seconds=1)
    (dst / "ticks.csv").write_text(
        "time,event,price,size,level,operation,instrument\n" + "\n".join(ticks) + "\n", encoding="utf-8")
    mbo = []                                        # depth/footprint feed, same day
    for i, ln in enumerate(ticks[::4]):
        px = float(ln.split(",")[2])
        for lvl in range(3):
            mbo.append(f"{ln.split(',')[0]},ADD,{px + (lvl - 1) * 0.2:.4f},"
                       f"{rnd.randint(1, 60)},{lvl},,GC 12-26")
    (dst / "mbo.csv").write_text(
        "time,operation,price,size,level,side,instrument\n" + "\n".join(mbo) + "\n", encoding="utf-8")
    hdr = ("timestamp,symbol,price,signal_direction,signal_strength,signal_confidence,regime,"
           "divergence,ai_action,ai_confidence,exec_status,order_id,news_state,minutes_to_event,"
           "next_event_title,reason")
    rows = []
    for i in range(200):
        dt = datetime(2099, 1, 5, 6, 0, tzinfo=timezone.utc) + timedelta(minutes=i * 4 + 1)
        px = float(ticks[min(len(ticks) - 1, i * 240)].split(",")[2])
        strong = i % 25 == 3
        conf = rnd.uniform(52, 64) if strong else rnd.uniform(0, 45)
        d = rnd.choice(["BUY", "SELL"]) if conf > 30 else "NEUTRAL"
        rows.append(",".join([f"{dt:%Y-%m-%dT%H:%M:%S}", "GC 12-26", f"{px:.2f}", d,
                              f"{conf/2.2:.2f}", f"{conf:.2f}",
                              rnd.choice(["TREND", "RANGE", "NEUTRAL"]), "0.0",
                              "HOLD", f"{conf+2:.1f}", "SKIPPED", "", "QUIET", "120", "ECB",
                              "signal accepted" if strong else "confidence below threshold"]))
    (dst / "data" / "decisions_log.csv").write_text(hdr + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    with open(dst / "data" / "snapshots_history.jsonl", "w", encoding="utf-8") as f:
        for i in range(120):
            dt = datetime(2099, 1, 5, 6, 0, tzinfo=timezone.utc) + timedelta(minutes=i * 7 + 2)
            px = float(ticks[min(len(ticks) - 1, i * 420)].split(",")[2])
            side = rnd.choice(["BUY", "SELL"])
            f.write(json.dumps({
                "timestamp": f"{dt:%Y-%m-%dT%H:%M:%S+00:00}", "price": px,
                "signal_direction": side if i % 5 == 0 else "NEUTRAL", "regime": "TREND",
                "strength": round(rnd.uniform(2, 48), 1), "confidence": round(rnd.uniform(30, 70), 1),
                "killzone": "LONDON",
                "team_scores": {"flow": 0.4, "whale": -0.9, "struct": 0.2, "trend": 0.5},
                # v7.1 real shape: a LIST of dicts (judge_panel.parse_judge_panel). The
                # fixture used to omit this, which is why the 'list has no .items()' crash
                # in test 8 only appeared on the operator's live diary and never here.
                "judge_votes": [
                    {"judge": "footprint_delta", "dir": 1.0 if side == "BUY" else -1.0,
                     "weight": 1.5, "raw": f"footprint delta {rnd.uniform(-.6,.6):+.3f}"},
                    {"judge": "l3_net_flow", "dir": 1.0 if i % 2 else -1.0, "weight": 1.5,
                     "raw": "L3 NET FLOW"},
                    {"judge": "vwap_trend", "dir": 1.0, "weight": 1.0, "raw": "VWAP trend UP"},
                    {"judge": "poc_day", "dir": 0.0, "weight": 1.0, "raw": "POC day"},
                ],
                "notes": [f"footprint delta {rnd.uniform(-.6,.6):+.3f} dominant {px:.1f} strength 0.5 -> {side}",
                          f"L3 NET FLOW {side} {rnd.uniform(-400,400):+.0f} (buys 600.0 vs sells 400.0) w 1.5 -> {side}",
                          f"ICEBERG_{'SUPPORT' if side=='BUY' else 'RESISTANCE'} 4 @ {px:.1f} refills 3 w 1.1 -> {side}",
                          f"L3 whale {'SUPPORT' if side=='BUY' else 'RESISTANCE'} 2 walls 900 lots closest {px:.1f} dist 0.11% w 1.4 -> {side}",
                          "VWAP trend UP price %.1f vs VWAP %.1f" % (px, px - 1.2)]},
            ) + "\n")
    (dst / "data" / "tracked_bot_positions.json").write_text('{"position_ids": [], "updated_at": "2099-01-05T21:00:00"}', encoding="utf-8")
    (dst / "data" / "market_snapshot.json").write_text('{"price": 2033.1}', encoding="utf-8")
    (dst / "data" / "mt5_signal.txt").write_text("direction=NEUTRAL\nconfidence=0.0\n", encoding="utf-8")
    lines = []
    for i in range(400):
        st = datetime(2099, 1, 5, 7, 0, tzinfo=timezone.utc) + timedelta(minutes=i * 2 + 1)
        pre = f"{st:%Y-%m-%d %H:%M:%S},{rnd.randint(0,999):03d} INFO    [main] "
        lat = rnd.randint(150, 900)
        lines += [pre + "GOLD TRADING SYSTEM - pipeline start (trade=XAUUSD, timeframe=M5, source=bookmapbridge)",
                  pre + f"LATENCY STEP1 acquire {rnd.randint(80,160)}ms",
                  pre + f"LATENCY STEP2 analyze {rnd.randint(150,300)}ms",
                  pre + f"LATENCY STEP3 AI {rnd.choice([1,1,900])}ms",
                  pre + "STEP 1  DATA ACQUISITION -> OK (bookmapbridge, data=GC 12-26 -> trade=XAUUSD, has_data=True)",
                  pre + "STEP 2  MARKET ANALYSIS -> OK -> NEUTRAL (strength 12.0, confidence 30.0)",
                  pre + "STEP 3  AI DECISION -> HOLD",
                  pre + "STEP 4  EXECUTION -> SKIPPED - confidence below threshold",
                  pre + "STEP 5  MONITORING -> OK",
                  pre + "MT5 SIGNAL BRIDGE -> OK -> mt5_signal.txt",
                  pre + f"LATENCY TOTAL pipeline {lat}ms target 500ms"]
    (dst / "logs" / "trading_20990105.log").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
