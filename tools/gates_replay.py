# -*- coding: utf-8 -*-
"""tools/gates_replay.py — the SEA for factory 2 (Code Day 2, replay-first law).

Feeds the owl's house the REAL tapes: raw `ticks_*.csv[.gz]` (+ optional
`mbo_*.csv[.gz]`) -> own chronological M1 walk (zero look-ahead) -> the REAL
brain `step2_market_analysis.analyze_market` -> factory2_gates.run_cycle -> one
diary line per cycle -> a night report on the same tape (verdicts, refusals,
toll payments, forward outcome of every VERDICT at +9m).

The old factory's files are read, never edited (COMPANY 3 law N1). The replay
uses the old tool's discovery/parser helpers by import; it writes ONLY into the
gates_* files. Diary mode stamp: replay:tapes (full candles -> the toll CAN pay).

Usage (repo root, Bash):
    python tools/gates_replay.py --date 2026-11-12
    python tools/gates_replay.py --days 5 --every 180 --window 08:00-23:00
    python tools/gates_replay.py --synthetic          # 900-min fake tape, no files
    python tools/gates_replay.py --date ... --keep-diary
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import time
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
for _p in (str(ROOT), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

REPLAY_VERSION = "gates.replay.2026-11-17.3"   # TALLY-2: `(allowed)` is no longer counted as a refusal   # EYES-2d: `stale` is SPLIT into what it really was (see TAPE_PRINT_GAP_SECONDS)

# THE LAW THE OWNER'S OWN MEASUREMENT EXPOSED (`data/eyes2_20261114.txt`, 2026-11-17).
# The walk had ONE word, `stale`, for TWO different states, and both are ordinary facts
# about a tape - not lateness, not illness:
#   (a) rows exist but not ONE print yet  (a depth-only stretch: the book is open, the
#       auction has not started) - `ev_i > 0` so `no-eyes` cannot fire, `last_px == 0.0`
#       so the old test said "stale";
#   (b) the tape ENDED and the grid kept walking to cells_end, freezing `last_ep` - the
#       owner's four comparable days are THIS, to the second: last_row 20:37:48 + a
#       22:00 window = 27 refused cells (they recorded 27), 18:43:46 = 65 (they recorded
#       65), 18:22:16 = 72 (they recorded 72). No silence anywhere: their longest
#       print-gap that day was 56 SECONDS against a 240-second rule.
# Nothing was miscounted; one label was borrowed. The words below are the fix, and the
# threshold gets a name so it can be grepped instead of found (measurement law: a field
# must not mean two things).
TAPE_PRINT_GAP_SECONDS = 240.0     # arm (b): a print EXISTED and then the tape went quiet
PRE_FIRST_PRINT = "pre-first-print"
POST_LAST_ROW = "post-last-row"
_KNOWN_SKIPS = ("no-eyes", PRE_FIRST_PRINT, POST_LAST_ROW, "warm-up", "brain", "snapshot",
                "stale")     # `stale` stays KNOWN so an older paper never reads as a violation


# ------------------------------------------------------------------ tape tools
def discover_tape(data_dir: Path, root_dir: Path, day_str: str,
                  kind: str = "ticks", scoped: bool = False) -> List[Path]:
    """Newest-safe order of raw files for one day (live `ticks.csv` last).

    v8.3 (item 41 - THE leak that made the owner's `1 failed` and hid from mine):
    `scoped=True` means `--data-dir` was NAMED, and then it is a CONTRACT: the
    repo's own drawer (`root_dir/data/archive/`) must NOT be searched. Without
    this flag a test that points the sea at a temp fixture still absorbs the real
    236 slices in the owner's `data/archive/` whenever the DAY NAME COLLIDES -
    which is why `test_a_ticks_tape_alone_can_never_invent_orders` reported 211
    order rows that do not exist in its own fixture, on their box only. The day
    LIST was already scoped (item 19); the file SEARCH was not.
    """
    cands: List[Path] = []
    bases = (data_dir,) if scoped else (data_dir, root_dir, root_dir / "Workspace" / "data")
    for base in bases:
        if not base.exists():
            continue
        compact = day_str.replace("-", "")
        pats = [f"{kind}_{day_str}*.csv", f"{kind}_{day_str}*.csv.gz",
                f"{kind}_{compact}*.csv", f"{kind}_{compact}*.csv.gz",
                f"{kind}.csv", f"{kind}.csv.gz"]
        for pat in pats:
            cands.extend(sorted(base.glob(pat)))
            # v7 (Code Day 3.3, the owner's first real sea): a CLOSED day's tape
            # is not a single file - the bridge folds it into ROTATED slices in
            # `data/archive/` (`ticks_YYYYMMDD_HHMMSS.csv.gz`, `bookmap_bridge_
            # provider.py:403/:917` rotate at ~100 MB; the docstring at :10/:12
            # names both tapes). The owner's own tools already search there
            # (`tools/raw_replay.py:224` `data_dir / "archive"`, `tools/backtest.py:
            # 728` `glob("mbo_*.csv.gz")`) - our finder not doing it is why a 5-day
            # sea fell back to the audit jar and printed `0 verdicts`.
            for sub in ("archive", "raw", "bookmap", "tapes", "data/archive"):
                d = base / sub
                if d.exists():
                    cands.extend(sorted(d.glob(pat)))
    out: List[Path] = []
    for p in cands:
        if p not in out:
            out.append(p)
    # live tails (bare ticks.csv / mbo.csv) are the newest slice: last
    out.sort(key=lambda p: (0 if p.stem.lower() == kind else 1, p.name))
    return out


def available_days(data_dir: Path, root_dir: Path, max_days: int = 30,
                   scoped: bool = False) -> List[str]:
    """Which days can be sailed.

    `scoped=True` (what `--data-dir` / `GATES_DATA_DIR` now sets) means
    "ONLY that folder". Without it the repo's own finder was allowed to
    sprinkle extra days into the list - on the owner's rig `--days 2` with a
    2-day fixture answered [today, yesterday], today having no tape, and the
    sea sailed 1 of 2. A named folder is a CONTRACT, not a hint (Law #12)."""
    days: set = set()
    if scoped:
        for p in sorted(Path(data_dir).rglob("ticks_*.csv*")):
            cand = day_token(p.name)
            if cand:
                days.add(cand)
        return sorted(days, reverse=True)[:max_days]
    try:                                                     # the boat's own finder
        import audit_day as ad
        for f in ad.all_ticks_files(None):
            cand = day_token(Path(f).name)
            if cand:
                days.add(cand)
    except Exception:
        pass
    for base in (data_dir, root_dir, root_dir / "Workspace" / "data"):
        if not base.exists():
            continue
        for p in list(base.rglob("ticks_*.csv*")):
            cand = day_token(p.name)
            if cand:
                days.add(cand)
    return sorted(days, reverse=True)[:max_days]


# ------------------------------------------------------------- the rehearsal's mirror
def mirror_bytes(path: Path) -> int:
    """Size of a file, or of everything under a directory (the brain's memory dir)."""
    try:
        if path.is_dir():
            return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
        return path.stat().st_size
    except OSError:
        return 0


def mirror_dir(out_dir: Path) -> Path:
    """WHERE the borrowed brain keeps its learned files: `config.DATA_DIR`, which
    `isolate_data_dir()` has already pointed at `<drawer>/gates_sandbox` (or, with
    `--mirror-dir X`, at `X/gates_sandbox`).

    Ask the brain, never guess. My first version built the path out of `--data-dir`
    and looked in a folder nothing writes to, so a mirror full of learned levels
    printed `mirror empty` on every run - item 33's class of error, one line away.
    """
    try:
        import config as _cfg
        d = Path(getattr(_cfg, "DATA_DIR", out_dir))
        if d.exists():
            return d
    except Exception:
        pass
    return out_dir


def quarantine_mirror(brain: Path, keep: bool = False) -> str:
    """Set the borrowed brain's LEARNED files aside before a replay (item 47).

    `isolate_data_dir()` stops a rehearsal from poisoning the LIVE drawer, and that
    is right - but the mirror it redirects to PERSISTS, so every sea run also READS
    whatever the previous run left behind (`session_levels.update` writes at
    step2_market_analysis.py:6693 and the same module's loader reads it back). Found
    by the worst kind of accident: my new horizon test PASSED with the mirror present
    and FAILED on a clean tree - same tape, same code, two answers. A replay must
    learn from the tape it is sailing, so the learned files are MOVED (kept, never
    deleted) unless `--keep-mirror` asks for the inheritance on purpose.
    """
    try:
        import factory2_gates as f2
        name = str(getattr(f2, "DATA_SANDBOX_NAME", "gates_sandbox"))
    except Exception:
        name = "gates_sandbox"
    # `brain` IS the folder the borrowed writers use. No name appended, no glob
    # re-derived: `main()` hands it `config.DATA_DIR`, which is the ONE fact the
    # quarantine writes (`isolate_data_dir()` points it at `<drawer>/gates_sandbox`
    # or `<--mirror-dir>/gates_sandbox`). Appending the sandbox name "helpfully" here
    # looked one level too deep and found nothing - which is how my first version
    # printed "mirror empty" over a mirror that was full of learned levels.
    mir = Path(brain)
    learned = [mir / n for n in ("session_levels.json", "last_book_state.json",
                                "power_v2_memory")]
    found = [q for q in learned if q.exists()]
    if keep:
        return (f"mirror KEPT on purpose ({len(found)} inherited item(s), "
                f"{sum(mirror_bytes(q) for q in found)} B): a verdict here is scored "
                f"with someone else's history") if found else "mirror empty (nothing to inherit)"
    if not found:
        return ("mirror empty (a clean rehearsal: no prev-day levels until THIS run "
                "writes them - sail 2+ days if you want prev_day_* rungs)")
    # ONE LEVEL ABOVE the brain folder, which is the repo's `data/` normally (and
    # ignored by .gitignore line 10, `data/*`) - or the drawer named by `--mirror-dir`.
    # Putting it INSIDE the brain would put the archive inside the folder being moved,
    # which `shutil.move` refuses (EISDIR): my first attempt did exactly that and my
    # `except OSError: pass` swallowed the refusal, so the paper said "reset" over a
    # mirror nobody had touched.
    dest = mir.parent / f"{name}_prev_{time.strftime('%Y%m%dT%H%M%S')}"
    try:
        dest.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return f"mirror NOT reset (could not create {dest.name}: {exc})"
    moved, nbytes = 0, 0
    for q in found:
        try:
            nbytes += mirror_bytes(q)
            shutil.move(str(q), str(dest / q.name))
            moved += 1
        except OSError as exc:
            # an unreadable receipt is worse than a broken rehearsal: say WHICH file
            # refused to move, or the paper goes on claiming "mirror empty" and the
            # next reader inherits it as truth (item 47 is exactly that story).
            return (f"mirror reset FAILED after {moved}/{len(found)}: {q.name}: "
                    f"{type(exc).__name__}: {exc} - the run continues on whatever the "
                    f"brain still holds, and the numbers below are NOT a clean sheet")
    if moved != len(found):
        return f"mirror reset PARTIAL: {moved}/{len(found)} moved to {dest.name}/"
    # NOTHING is ever deleted here, on purpose: LAW #13 says we keep what we touched,
    # and `test_old_factory_files_untouched_law_N1` ENFORCES that by banning a
    # recursive-delete call from this module (the ban even reads my comment above, so
    # the word may not appear outside this explanation). A rehearsal therefore leaves
    # one folder per run beside the brain - a few hundred bytes, inside `data/`, which
    # .gitignore line 10 (`data/*`) already hides. Clearing a night's archive is the
    # operator's own hand: `rm -rf data/gates_sandbox_prev_*`, never our code.
    # A same-second rerun collides with the last folder, and `shutil.move` onto an
    # existing file raises rather than overwriting - so the name gets a counter.
    _pfx = dest.name
    _n = 1
    while dest.exists() and any(dest.iterdir()):
        _n += 1
        dest = dest.parent / f"{_pfx}.{_n}"
        if _n > 60:
            break
    if not dest.exists():
        try:
            dest.mkdir(parents=True)
        except OSError as exc:
            return f"mirror NOT reset ({dest.name}: {type(exc).__name__}: {exc})"
    # only OUR own grammar counts: a loose glob would let one unrelated folder turn
    # every night's paper into a noise complaint
    _older = len([q for q in dest.parent.glob(f"{name}_prev_*")
                  if q.is_dir()
                  and re.fullmatch(rf"{name}_prev_\d{{8}}T\d{{6}}(\.\d+)?", q.name)]) - 1
    return (f"mirror reset: {moved} learned item(s), {nbytes} B set aside to "
            f"{dest.name}/ (kept, never deleted"
            + (f"; {_older} earlier rehearsal(s) waiting beside it" if _older > 0
               else "") + ")")


# ------------------------------------------------------------------ the Mapper's band
# OWNER'S LAW (2026-11-13 21:00 Budapest, "the three friends"): the Mapper must not
# draw every fight line in the market - gold does not move far enough, fast enough,
# for the far ones to matter, and a 3,000-line map is slow to draw and slower to read.
# So the Mapper draws ONLY what stands within +/- RUNG_BAND_USD of the current price.
# The number is the owner's, not a fitted one: 80 USD on a ~4,000 gold is +-2%.
RUNG_BAND_USD = 80.0


def band_levels(snap: Dict[str, Any], price: float,
                band: float) -> Tuple[int, int]:
    """Trim the borrowed brain's level map to the band, IN PLACE, and return
    (lines seen, lines kept).

    Why here and not inside the hall: `signal_map.targets` + `memory_doors` ARE the
    map the Mapper draws (`factory2_gates.levels_from_view` builds its rungs from
    exactly these two lists), and `tools/gates_replay.py` is a file we own end to
    end - `factory2_gates.py` and `committee.py` are borrowed/older-branch files and
    patching them here would make the whole paper un-applyable if the owner's copy
    drifted by one byte. The brain's learner rungs (FVG from bars, AVWAP) are built
    inside `levels_from_view` from the RECENT bars, so they are near by construction;
    when the hall is switched on, this same law belongs in `committee.build_rungs`
    and it will ship as its own paper.
    """
    if not band or band <= 0 or price <= 0:
        rows = 0
        for key in ("targets", "memory_doors"):
            rows += len(((snap.get("signal_map") or {}).get(key)) or [])
        return rows, rows
    seen = kept = 0
    sm = snap.get("signal_map")
    if not isinstance(sm, dict):
        return 0, 0
    for key in ("targets", "memory_doors"):
        rows = sm.get(key)
        if not isinstance(rows, list) or not rows:
            continue
        keep = []
        for row in rows:
            seen += 1
            try:
                px = float(row["price"])
            except (KeyError, TypeError, ValueError):
                continue                       # an incomplete line is never guessed
            if abs(px - float(price)) <= float(band):
                keep.append(row)
                kept += 1
        sm[key] = keep
    return seen, kept


_TS_LOCAL_NOTE = "ts:bridge-style (naive = local, date-aware)"

# ONE day grammar for the whole sea (Book Law #12, item 12). A filename like
# `ticks_20261112.csv` used to be sliced into "20261112.c": `date.fromisoformat`
# in Python 3.11+ is generous and ACCEPTED it, while the window math used a
# strict parse - so the day came back "0 prints" on a tape that was sitting
# right there. Half-parsed dates are worse than refused ones.
_DAY_RE = re.compile(r"^(\d{4})-?(\d{2})-?(\d{2})(?:[-_].*)?$")


def canon_day(raw: Any) -> Optional[str]:
    """`2026-11-12` / `20261112` -> ISO; anything else -> None (never a guess)."""
    m = _DAY_RE.match(str(raw or "").strip())
    if not m:
        return None
    y, mo, d = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    try:
        date(y, mo, d)
    except ValueError:
        return None
    return f"{y:04d}-{mo:02d}-{d:02d}"


def day_token(name: str) -> Optional[str]:
    """The trading day a tape file name carries (`ticks_20261112_000000.csv.gz`)."""
    m = re.search(r"(\d{4}-\d{2}-\d{2}|\d{8})", str(name))
    return canon_day(m.group(1)) if m else None


def _line_epoch(line: str) -> Optional[float]:
    """Epoch of a tape row, parsed EXACTLY like the bridge does: `fromisoformat`,
    and a naive stamp means the MACHINE's zone on that row's own date (the bridge
    fixed that bug in `bookmap_bridge_provider._parse_ts` - we must not disagree,
    or the grid walks a shifted day and every cycle reads 'stale eyes')."""
    comma = line.find(",")
    if comma <= 0:
        return None
    ts = line[:comma].strip()
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                dt = datetime.strptime(ts, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.astimezone()          # same law as the bridge's _parse_ts
    return float(dt.timestamp())


def set_ts_parser():
    """Prefer the bridge's own parser when it is importable (identical semantics)."""
    global _line_epoch
    try:
        import bookmap_bridge_provider as bbp
        _orig = bbp._parse_ts

        def _via_bridge(line: str) -> Optional[float]:
            comma = line.find(",")
            if comma <= 0:
                return None
            dt = _orig(line[:comma])
            return None if dt is None else float(dt.timestamp())
        _line_epoch = _via_bridge
        return "ts:bookmap_bridge_provider._parse_ts"
    except Exception:
        return _TS_LOCAL_NOTE


def iter_tape(files: List[Path], start_ep: float, end_ep: float) -> Iterator[Tuple[float, str]]:
    """Chronological (ep, line) events over one day's tape files."""
    for path in files:
        opener = (lambda p=path: __import__("gzip").open(p, "rt", encoding="utf-8",
                                                          errors="ignore")) \
            if str(path).endswith(".gz") else \
            (lambda p=path: open(p, "r", encoding="utf-8", errors="ignore"))
        try:
            with opener() as fh:
                for line in fh:
                    if not line or len(line) < 20 or line[0] == "t":
                        continue
                    ep = _line_epoch(line)
                    if ep is None or ep < start_ep:
                        continue
                    if ep > end_ep:
                        break
                    yield ep, line.rstrip("\r\n")
        except OSError:
            continue


def parse_last(line: str, ep: Optional[float] = None
               ) -> Optional[Tuple[float, float, float]]:
    """(ep, price, size) for a Last/Trade print row, else None. `ep` is taken
    from the tape walker when given - NEVER re-parsed with a second clock
    (a past draft did, and its strptime formats rejected the tape's ISO
    +00:00 stamps -> every print parsed as "no price" -> 0 cycles. Law #12.)"""
    if ",Last," not in line and ",Trade," not in line:
        return None
    import csv as _csv
    parts = next(_csv.reader([line]), [])
    if len(parts) < 4:
        return None
    if ep is None:
        ep = _line_epoch(parts[0])
    try:
        price = float(parts[2])
        size = float(parts[3] or 1.0)
    except (TypeError, ValueError):
        return None
    if ep is None or price <= 0 or not math.isfinite(price):
        return None
    return ep, price, size


def synthetic_tape(minutes: int = 720, seed: int = 7) -> List[Tuple[float, float, float]]:
    """A deterministic fake print tape (trend + chop regime changes) so the SEA
    road itself can be tested where no raw files exist. (ep, price, size)."""
    import random
    rnd = random.Random(seed)
    t0 = int(time.time())
    out: List[Tuple[float, float, float]] = []
    px = 4000.0
    drift = 0.0
    for i in range(minutes * 6):        # 10-second prints
        if i % (45 * 6) == 0:
            drift = rnd.choice([-0.35, 0.0, 0.35])
        px = max(10.0, px + drift + rnd.gauss(0.0, 0.28))
        out.append((t0 + i * 10.0, round(px, 2), float(rnd.randint(1, 40))))
    return out


# ------------------------------------------------------------------- the walk
class M1Walk:
    """Strictly-causal minute buckets (epoch//60) + rolling price ring."""

    def __init__(self) -> None:
        self.bars: Dict[int, List[float]] = {}
        self.keys: List[int] = []                        # ascending, chronological
        self.prices: List[Tuple[float, float]] = []      # (ep, price)

    def add(self, ep: float, price: float, size: float) -> None:
        b = int(ep // 60)
        cur = self.bars.get(b)
        if cur is None:
            self.bars[b] = [price, price, price, price, size if size > 0 else 1.0]
            self.keys.append(b)
        else:
            cur[1] = max(cur[1], price)
            cur[2] = min(cur[2], price)
            cur[3] = price
            cur[4] += (size if size > 0 else 1.0)
        self.prices.append((ep, price))
        if len(self.prices) > 400_000:
            del self.prices[:200_000]

    def candles(self, cap: int = 1200, upto_ep: Optional[float] = None) -> Dict[str, List[float]]:
        keys = self.keys
        if upto_ep is not None:
            keys = [k for k in keys if k < int(upto_ep // 60)]    # CLOSED bars only
        keys = keys[-cap:]
        if not keys:
            return {}
        o, h, l, c, v = ([], [], [], [], [])
        for k in keys:
            b = self.bars[k]
            o.append(b[0]); h.append(b[1]); l.append(b[2]); c.append(b[3]); v.append(b[4])
        return {"open": o, "high": h, "low": l, "close": c, "volume": v}

    def resample(self, minutes: int, upto_ep: float) -> Dict[str, List[float]]:
        cd = self.candles(cap=2400, upto_ep=upto_ep)
        if not cd or minutes <= 1:
            return cd
        sec = minutes * 60
        g: Dict[int, List[float]] = {}
        ks = [k for k in self.keys if upto_ep is None or k < int(upto_ep // 60)]
        ks = ks[-len(cd["close"]):]
        for i, minute_k in enumerate(ks):
            bk = int(minute_k // sec) * sec
            cur = g.get(bk)
            row = (cd["open"][i], cd["high"][i], cd["low"][i], cd["close"][i],
                   cd["volume"][i])
            if cur is None:
                g[bk] = [row[0], row[1], row[2], row[3], row[4]]
            else:
                cur[1] = max(cur[1], row[1]); cur[2] = min(cur[2], row[2])
                cur[3] = row[3]; cur[4] += row[4]
        ks = sorted(g.keys())
        return {"open": [g[k][0] for k in ks], "high": [g[k][1] for k in ks],
                "low": [g[k][2] for k in ks], "close": [g[k][3] for k in ks],
                "volume": [g[k][4] for k in ks]}


def _l3_events(mbo_tail: Any, st: Any, tc_ep: float
               ) -> Tuple[List[Dict[str, Any]], str]:
    """MBO events from the borrowed tail, windowed to the brain's own horizon.

    v7 (item 27): the ORDER TAPE is the only source. The old fallback read the
    ticks tail's own `mbo_events`, whose rows carry `level=-1` and no order id -
    exactly what the bridge names the "929 fake icebergs" bug at
    bookmap_bridge_provider.py:334. With no order tape this returns [] and the
    cycle is stamped `l3q:unavailable(no order tape)`, so a verdict can never be
    dressed up as L3 evidence."""
    src_list: List[Any] = []
    epochs: List[float] = []
    err = ""
    if mbo_tail is not None and mbo_tail.instruments:
        try:
            # v7 REAL-SEA BUG (item 28): v6 picked the busiest instrument with
            # `kv[1].line_count`, but `_InstrumentState` uses __slots__ and has
            # NO line_count (that lives on the TAIL) -> AttributeError every call,
            # swallowed by the except below -> L3 was EMPTY even with a real
            # order tape. A safety `except` around your own typo is a blindfold.
            _, best = max(mbo_tail.instruments.items(),
                          key=lambda kv: len(getattr(kv[1], "mbo_events", None) or []))
            src_list = list(getattr(best, "mbo_events", []) or [])[-4000:]
            # the bridge keeps its own epoch index; reuse it when it is aligned
            # (the same guard the bridge uses at :973), never when it is not.
            eps = list(getattr(best, "mbo_epochs", []) or [])
            if len(eps) == len(src_list):
                epochs = eps
        except Exception as exc:                        # LOUD, never silent
            err = f"{type(exc).__name__}: {exc}"
            src_list = []
    out: List[Dict[str, Any]] = []
    for i, ev in enumerate(src_list):
        if not isinstance(ev, dict):
            continue
        ep: Optional[float] = None
        if epochs:
            try:
                ep = float(epochs[i])
            except (TypeError, ValueError):
                ep = None
        if ep is None:
            ts = ev.get("timestamp") or ev.get("time")
            if not ts:
                continue
            try:
                ep = datetime.fromisoformat(str(ts)).timestamp()
            except ValueError:
                continue
        if tc_ep - 300.0 <= ep <= tc_ep:
            out.append(ev)
    return out, err


def a_has_horizon(res: Dict[str, Any]) -> bool:
    """Does this day's paper contain at least one MEASURED verdict? `False` is not
    an insult - it is the difference between 'we abstained' and 'we failed'."""
    for e in (res.get("episodes") or []):
        if e.get("fwd9") is not None:
            return True
    return False


def forward_price(prices: List[Tuple[float, float]], from_ep: float,
                  minutes: float) -> Optional[float]:
    target = from_ep + minutes * 60.0
    lo, hi = 0, len(prices) - 1
    while lo < hi:
        mid = (lo + hi) // 2
        if prices[mid][0] < target:
            lo = mid + 1
        else:
            hi = mid
    if lo >= len(prices) or prices[lo][0] < target:
        return None
    return prices[lo][1]


def replay_day(day_str: str, every_s: int, window: str, prewarm_min: int,
               data_dir: Path, root_dir: Path, pen, state, synthetic: bool,
               max_cycles: int, quiet: bool,
               json_out: Optional[Path] = None, depth: str = "auto",
               a_max_minutes: int = 0, min_prints: int = 600,
               scoped: bool = False, rung_band: float = RUNG_BAND_USD,
               hands_shadow: bool = False) -> Dict[str, Any]:
    """One day of tape -> cycles -> committee -> diary. Returns tallies."""
    import factory2_gates as f2
    h0, m0 = (int(x) for x in window.split("-")[0].split(":"))
    h1, m1 = (int(x) for x in window.split("-")[1].split(":"))
    d_obj = date.fromisoformat(day_str) if day_str else date.today()
    win_start = datetime(d_obj.year, d_obj.month, d_obj.day, h0, m0,
                         tzinfo=timezone.utc).timestamp()
    win_end = datetime(d_obj.year, d_obj.month, d_obj.day, h1, m1,
                       tzinfo=timezone.utc).timestamp()
    stream_start = win_start - prewarm_min * 60.0
    # v8.1 (item 31, found on the owner's rig, not in a sandbox): a cycle can
    # never be fed before 60 candles exist (the warm-up law at :588), and 60
    # candles of 3-minute M3 are 180 minutes of tape. A --prewarm of 150 could
    # therefore NOT satisfy our own law, so every day that starts its tape late
    # (an OPEN day: its first slice is a fold from the previous evening, the
    # slice's NAME-time is not the first row's time) printed the terrifying
    # `fed 0` while the rows were right there - 147,849 of them on 2026-11-13.
    # The floor is arithmetic, not a taste: prewarm >= every_s * 60 / 60.
    warm_min_needed = int(every_s) * 60 // 60
    if prewarm_min < warm_min_needed:
        prewarm_min = warm_min_needed
        stream_start = win_start - prewarm_min * 60.0

    res_ts_parser = set_ts_parser()          # one clock, no two-part truth
    walk = M1Walk()
    events: List[Tuple[float, float, float, Optional[str]]] = []
    src = "synthetic"
    # v7: the order tape and the ticks tape are TWO things, and both must exist
    # as names even when the day is synthetic or jar-fed - a safety helper that
    # reads them later may never meet a NameError (Code Day 3.2 lesson).
    mbo_files: List[Path] = []
    mbo_rows: List[Tuple[float, str]] = []
    mbo_poison = 0
    n_depth = 0
    if synthetic:
        for i, (ep, px, sz) in enumerate(synthetic_tape(int((win_end - stream_start) / 60))):
            events.append((stream_start + i * 10.0, px, sz, None))
    else:
        sys.path.insert(0, str(HERE))
        want_raw = depth in ("auto", "on")
        files: List[Path] = []
        mbo_files = []
        try:
            files = discover_tape(data_dir, root_dir, day_str, "ticks", scoped=scoped)
            # v7: the ORDER tape is a separate file with a separate grammar
            # (`time,event_type,order_id,price,size,instrument`, 6 cols - the
            # owner's own header, seen verbatim in data/archive/mbo_*.csv.gz).
            mbo_files = discover_tape(data_dir, root_dir, day_str, "mbo", scoped=scoped)
        except OSError:
            files = []
        if want_raw and not files:
            want_raw = False
            src = "jar:audit_day"
        # ONE pass, newest tape first: every line that matters becomes one row.
        # v7 filter truth, read off the bridge's OWN reader (`bookmap_bridge_
        # provider.py:274/304/307/310/321/:332` = Last, Bid, Ask, DepthBid,
        # DepthAsk, Mbo): bare `Bid`/`Ask` rows were dropped by v6, so the
        # top-of-book never moved. `Mbo` rows on the TICKS tape are a FAKE-ORDER
        # trap and are now counted, never fed (see `mbo_poison` below).
        mbo_poison = 0
        n_depth = 0
        for ep, line in iter_tape(files, stream_start, win_end):
            if ",Mbo," in line:
                mbo_poison += 1
                continue
            if (",Depth" in line) or (",Last," in line) or (",Trade," in line) \
                    or (",Bid," in line) or (",Ask," in line):
                if ",Depth" in line or ",Bid," in line or ",Ask," in line:
                    n_depth += 1
                if ",Last," in line or ",Trade," in line:
                    got = parse_last(line, ep)
                    if got:
                        events.append((got[0], got[1], got[2],
                                      line if want_raw else None))
                else:
                    events.append((ep, 0.0, 0.0, line))
        events.sort(key=lambda r: r[0])
        # the order tape in its own list: it never becomes a print and never
        # touches the candle walk - only `_MboTail` reads it.
        mbo_rows = []
        for ep, line in iter_tape(mbo_files, stream_start, win_end):
            if line.count(",") >= 5:      # 6-col grammar or longer, nothing else
                mbo_rows.append((ep, line))
        mbo_rows.sort(key=lambda r: r[0])
        if events:
            src = "tapes:own+mbo" if mbo_rows else "tapes:own+no-mbo"
            if not want_raw:
                src += "+candles-only"
        else:
            # fall back to the boat's own reader (prints only - no L2/L3 possible)
            try:
                import audit_day as ad
                rows, _p, _n = ad.load_ticks(date.fromisoformat(day_str))
                for dt, px, sz in rows:
                    ep = float(dt.timestamp())
                    if stream_start <= ep <= win_end:
                        events.append((ep, float(px), float(sz or 0.0), None))
                src = "jar:audit_day+mbo" if mbo_rows else "jar:audit_day"
                if not want_raw:
                    src += "+candles-only"
            except Exception:
                pass
    n_prints = sum(1 for e in events if e[1] > 0)
    if n_prints < min_prints:
        return {"day": day_str, "ok": False,
                "reason": f"only {n_prints} prints in window - the sea needs "
                         f"{min_prints} to judge a day at all (widen --window, or "
                         f"lower --min-prints on purpose)"}
    cap_warmed = 0
    # v8.2b (item 38, found on the owner's rig by THEIR first verdicts): a capped
    # day ended the TAPE at the same minute it ended the CELLS, so an episode in
    # the last 9 minutes had no future rows to look at and `fwd9` came back None.
    # The cells stay capped (that is the owner's budget); only a few extra minutes
    # of rows are read, and nothing outside the cell bound is ever FED - the cycle
    # pointer walks to `tc`. 15, not 9: `pb4` asks for 12 minutes (v8.5, item 46).
    look_min = 15.0
    if a_max_minutes:
        hard_end = win_start + a_max_minutes * 60.0
        # `--max-minutes 240` is the owner's RAM/budget word, not a request to
        # spend 60 of those minutes standing still. The warm-up is charged
        # OUTSIDE the budget: same rows in RAM, but 50 cycles instead of 0.
        cap_warmed = int(max(0, min(prewarm_min, a_max_minutes)
                             - max(0.0, events[0][0] - win_start) / 60.0))
        if cap_warmed:
            hard_end += cap_warmed * 60.0
        win_end = min(win_end, hard_end)
        _first_row_ep = events[0][0]
        _cells_end = win_end                       # where CELLS stop
        # A verdict in the last minutes of the cell window used to have NO future
        # rows at all (their first two episodes printed `fwd9=None`), because the
        # tape was truncated at the same minute as the cells. Read 10 more minutes
        # of rows - they are never fed to a cycle, only looked forward at.
        win_end = win_end + look_min * 60.0
        events = [e for e in events if e[0] <= win_end]
        if not events:
            # v8.1 (item 31): the cap can outlive the tape. Before this guard an
            # owner shape like `--max-minutes 60` on a day whose first row is at
            # 11:06Z left `events` empty and crashed inside the grid line - a
            # traceback is not an answer, and a silent zero is worse. The day is
            # now refused OUT LOUD, with the two times that explain it.
            _t = datetime.fromtimestamp(_first_row_ep, tz=timezone.utc)
            return {"day": day_str, "ok": False,
                    # v8.2 (the owner's 20:13 packet): a NOT-RUN day used to print
                    # `(src=None)`, because the refusal dict carried no `src` - an
                    # absent field rendered as the word "None", which reads like a
                    # bug in the reader rather than an honest abstain. It now stamps
                    # itself, and carries zero skip_kinds so the teacher never has
                    # to guess between "nothing skipped" and "not measured".
                    "src": "none:cap-missed-the-tape",
                    "lines": 0, "prints": 0, "cycles": 0, "errors": 0,
                    "skip_kinds": {"no-eyes": 0, PRE_FIRST_PRINT: 0, POST_LAST_ROW: 0,
                                   "warm-up": 0, "brain": 0, "snapshot": 0},
                    "window": {"win_start": float(win_start),
                               "win_end": float(win_end),
                               "first_row": _first_row_ep, "last_row": None,
                               "cells": 0},
                    "warmup": int(prewarm_min), "cap_warmed": int(cap_warmed),
                    "reason": f"the cap ({int(a_max_minutes)}min) ends before the "
                              f"tape's first row ({_t:%H:%M}Z on {day_str}) - the "
                              f"slice named {day_str.replace('-', '')} starts late "
                              f"(a slice's name is its FOLD moment, Book 3.4), so "
                              f"there is nothing to sail in this window (item 31)"}
    _cells_end = min(_cells_end if a_max_minutes else win_end, win_end)
    grid = [t for t in range(int(math.ceil(events[0][0] / every_s) * every_s),
                              int(_cells_end) + 1, every_s)
            if win_start <= t <= _cells_end]
    res_grid_cells = len(grid)          # v8.2: the day-math stamps need the grid,
    res_first_row = events[0][0]        # and `grid` does not exist for a refused
    res_last_row = events[-1][0]        # day - so they are taken here, where the
    if max_cycles:
        grid = grid[:max_cycles]

    brain = None
    try:
        from step2_market_analysis import analyze_market as brain   # the borrowed brain
    except Exception as exc:
        return {"day": day_str, "ok": False, "reason": f"no brain here: {exc}"}

    tail = mbo_tail = None
    depth_on = any(e[3] for e in events[:500])
    depth_note = "depth:off(no raw rows)"
    l3_note = (f"L3: ABSENT - no order tape for this day (verdicts are physics-"
               f"less, not failed) | {mbo_poison} ticks-tape Mbo row(s) ignored "
               f"(fake order_id, item 27)"
               if not mbo_rows else
               f"L3: order tape - {len(mbo_rows)} row(s) from {len(mbo_files)} "
               f"file(s), fed only to _MboTail")
    if depth_on or mbo_rows:
        try:
            import bookmap_bridge_provider as bbp        # borrowed, read-only
            import threading as _th
            if depth_on:
                tail = bbp._BridgeTail.__new__(bbp._BridgeTail)
                tail.path = ""
                tail.lock = _th.Lock()
                tail.instruments = {}
                tail.line_count = 0
                tail.bad_lines = 0
                tail.direct_side_hits = 0
                depth_note = "depth:live-from-tape"
            if mbo_rows:
                # ONLY the real order tape feeds this. A `Mbo` row on the TICKS
                # tape has `level=-1` and no order id, and `_MboTail` would key
                # every order by the literal word `BID_NEW` - the bug the bridge
                # itself names at :334 ("929 fake icebergs"). v6 fed exactly
                # that; v7 counts those rows and never feeds them (item 27).
                mbo_tail = bbp._MboTail.__new__(bbp._MboTail)
                mbo_tail.path = ""
                mbo_tail.lock = _th.Lock()
                mbo_tail.instruments = {}
                mbo_tail.line_count = 0
                mbo_tail.bad_lines = 0
        except Exception as exc:
            tail = mbo_tail = None
            depth_on = False
            depth_note = f"depth:absent({type(exc).__name__})"
            l3_note = f"L3: absent({type(exc).__name__})"
    try:
        from step2_market_analysis import snapshot_to_dict
    except Exception:
        snapshot_to_dict = None

    res: Dict[str, Any] = {"day": day_str, "ok": True, "src": src, "cycles": 0,
                           "g2_pass": 0, "verdicts": 0, "tickets": 0,
                           "toll_closes": 0, "toll_minutes": 0.0,
                           "fails": {}, "allowed": 0, "moods": {}, "rungs": {},
                           "episodes": [], "errors": 0,
                           "skips": {"no-eyes": 0, PRE_FIRST_PRINT: 0, POST_LAST_ROW: 0,
                                     "warm-up": 0, "brain": 0, "snapshot": 0}}
    res["warmup"] = int(prewarm_min)
    res["cap_warmed"] = cap_warmed
    res["lookahead_min"] = float(look_min) if a_max_minutes else 0.0
    # v8.5 (item 46 - a half-fix of mine, found while reading the owner's v8d
    # receipt, in which EVERY verdict on BOTH days printed `fwd9=None`): the
    # horizon used to be searched inside `walk.prices`, but the walk is fed only up
    # to `tc` - that IS the causality law, the friends may never see the future -
    # so the list ended where the verdict began and `forward_price()` returned None
    # for EVERY episode on EVERY day, capped or not. `.7` widened the READ and I
    # tested the wrapper (window bounds, the printed words) instead of the number,
    # so three versions shipped with the fix not fixed. `fwd_px` is the day's own
    # print rows, read AFTER the decision, which is exactly what "looked at, never
    # fed" was supposed to mean. Sorted because a closed day is several slices.
    fwd_px = sorted((e[0], e[1]) for e in events if e[1] > 0.0)
    res["fwd_rows"] = len(fwd_px)          # rows the AFTER-the-fact scorer could see
    res["window"] = {"win_start": float(win_start), "win_end": float(win_end),
                     "cells_end": float(_cells_end),
                     "first_row": float(res_first_row), "last_row": float(res_last_row),
                     "cells": int(res_grid_cells)}
    res["depth"] = depth_note + ("" if events else "")
    res["l3"] = l3_note
    res["mbo_rows"] = len(mbo_rows)
    res["mbo_files"] = len(mbo_files)
    res["ticks_mbo_ignored"] = mbo_poison
    res["fed_by"] = ("jar" if src.startswith("jar:")
                     else "raw tape" if src.startswith("tapes:") else src)
    res["lines"] = len(events)
    res["prints"] = n_prints
    res["cap_min"] = a_max_minutes
    res["ts_parser"] = res_ts_parser
    # the Mapper's band, measured (item 42): the owner's law is only useful if the
    # paper says how often it binds. seen/kept are summed over the whole day.
    res["rung_band"] = float(rung_band or 0.0)
    res["levels_seen"] = 0
    res["levels_kept"] = 0
    res["band_empty"] = 0
    # WHERE the finder was allowed to look is a fact about the number above it, so
    # it goes on the paper (item 41: a leak you cannot see is a leak you will ship).
    res["tape_scope"] = ("explicit --data-dir ONLY (repo drawer not searched)" if scoped
                         else "data-dir + repo root + Workspace/data")
    def _skip(reason: str) -> None:
        """One counter per CAUSE. `errors` stays the total (it is in the diary,
        the json and the old tests), but a night must never again say "38
        skipped" without saying WHY - the owner's packet of 2026-11-13 18:43
        is graded on exactly this line (item 31)."""
        res["errors"] += 1
        sk = res.setdefault("skips", {})
        sk[reason] = int(sk.get(reason, 0)) + 1

    ev_i = 0
    fed_i = 0
    mbo_i = 0
    last_px = 0.0
    last_ep = 0.0
    toll_hist: List[float] = []
    ledger_obj = __import__("committee").TicketLedger()
    # ROOM E, TODO 12c: the hands ride in on a dict the SAIL owns, and the sail supplies the
    # function. I first wrote `import factory2_hands` inside factory2_gates.py and it would have
    # been the dead code this file's own comment at :1002 warns about (cv_from_snapshot_dict
    # dropped a key I set) - the hall does not know where tools/ is on sys.path, the replay does.
    shadow = None
    if hands_shadow:                      # a real PARAMETER, not `args`: replay_day never had one
        _hands = __import__("tools.factory2_hands", fromlist=["cycle_hands"])
        shadow = {"cycle_hands": _hands.cycle_hands, "pos": None}
    for ci, tc in enumerate(grid):
        rung = ""
        # last print at/below tc - cheap: the grid is ascending, so a moving pointer
        while ev_i < len(events) and events[ev_i][0] <= tc:
            if events[ev_i][1] > 0:                    # a real print, not a depth row
                last_ep, last_px = events[ev_i][0], events[ev_i][1]
            ev_i += 1
        if ev_i == 0:
            _skip("no-eyes")                              # tape starts after tc
            continue
        if last_px <= 0.0:
            # rows on the tape, not one price yet. Honest name, honest cause: the
            # auction has not printed. (EYES-2d - this used to be filed under `stale`)
            _skip(PRE_FIRST_PRINT)
            continue
        if last_ep < tc - TAPE_PRINT_GAP_SECONDS:
            # a print existed and then nothing moved for > the gap law. On a tape this
            # is overwhelmingly "the day's file ENDED and the grid walked on": the tail
            # is not a stale feed, it is out of data. Named so, at last (EYES-2d).
            _skip(POST_LAST_ROW)
            continue
        # advance the candles and the borrowed tails from the same pointer
        while fed_i < ev_i:
            e_ep, e_px, e_sz, e_ln = events[fed_i]
            if e_px > 0:
                walk.add(e_ep, e_px, e_sz)
            if depth_on and tail is not None and e_ln:
                tail._handle_line(e_ln)     # the bridge's own reader decides all
            fed_i += 1
        # v7: the ORDER tape advances on the same clock, strictly causal, and it
        # is the ONLY thing that can put an order into `_MboTail`.
        while mbo_i < len(mbo_rows) and mbo_rows[mbo_i][0] <= tc:
            if mbo_tail is not None:
                try:
                    mbo_tail._handle_line(mbo_rows[mbo_i][1])
                except Exception:
                    mbo_tail.bad_lines += 1
            mbo_i += 1
        m1 = walk.candles(cap=1200, upto_ep=float(tc))
        if len(m1.get("close") or []) < 60:
            _skip("warm-up")
            continue                                      # the 60-candle law
        st = None
        if depth_on and tail is not None:
            try:
                name = max(tail.instruments.items(),
                           key=lambda kv: len(kv[1].ticks))[0]
                st = tail.instruments.get(name)
            except (ValueError, KeyError):
                st = None
        _l3_ev, _l3_err = _l3_events(mbo_tail, st, float(tc))
        if _l3_err and len(res.setdefault("l3_errors", [])) < 5:
            res["l3_errors"].append(_l3_err)               # never a silent hole
        md = {
            "symbol": "REPLAY", "price": float(last_px),
            "bid": (float(st.best_bid) if st is not None and st.best_bid > 0 else float(last_px)),
            "ask": (float(st.best_ask) if st is not None and st.best_ask > 0 else float(last_px)), "volume": float(sum(m1["volume"][-240:])),
            "candles": m1, "candles_is_m1": True, "candles_tf_minutes": 3,
            "candles_tf": walk.resample(3, float(tc)),
            "tick_data": (list(st.ticks[-3000:]) if st is not None else []),
            "bid_depth": (dict(st.bids) if st is not None else {}),
            "ask_depth": (dict(st.asks) if st is not None else {}),
            "order_events": _l3_ev,
            "order_book": ({"bids": sorted(st.bids.items(), key=lambda t: -t[0])[:40],
                            "asks": sorted(st.asks.items(), key=lambda t: t[0])[:40]}
                           if st is not None else None),
            "book_updates": [],
            "macro": {}, "news": {"headlines": [], "events": [], "fetch_calendar": False},
        }
        try:
            snap = brain(md, now=datetime.fromtimestamp(tc, tz=timezone.utc))
        except Exception as exc:
            _skip("brain")
            be = res.setdefault("brain_errors", [])
            if len(be) < 40:
                be.append(f"{type(exc).__name__}: {exc}")
            continue
        d = snapshot_to_dict(snap) if snapshot_to_dict else None
        if d is None:
            try:
                from dataclasses import asdict
                d = json.loads(json.dumps(asdict(snap), default=str))
            except Exception as exc:
                _skip("snapshot")
                res.setdefault("snap_errors", []).append(f"{type(exc).__name__}: {exc}")
                continue
        # --depth off / no L2 rows => no push, no desks: the sea MUST say so
        # loudly instead of pretending the hall is "just undecided".
        if depth == "off":
            stamps_extra = ["l2:l2-off(depth rows not fed)", "l3q:unavailable(by-config)"]
        elif not depth_on:
            stamps_extra = ["l2:no-depth-rows-in-tape", "l3q:unavailable(no L2/L3)"]
        elif mbo_tail is None and not n_depth:
            stamps_extra = ["l2:prints-only-no-book", "l3q:unavailable(no order tape)"]
        elif mbo_tail is None:
            stamps_extra = ["l3q:unavailable(no order tape)"]
        else:
            stamps_extra = []
        for _w in stamps_extra:
            try:
                d.setdefault("notes", []).append(_w)
            except Exception:
                pass
        _seen, _kept = band_levels(d, float(last_px), rung_band)
        res["levels_seen"] += _seen
        res["levels_kept"] += _kept
        if _seen and not _kept:
            # the whole map was outside the band: NOT a refusal to feed (the cycle
            # still walks, so RungState keeps its memory - skipping here would
            # starve the accepted-counts and fake a clean look at history), but it
            # must be NAMED, because "G2 said NONE" without this line reads as a
            # veto when it is really a radius.
            res["band_empty"] += 1
            try:                                    # onto the paper, not just the counter
                d.setdefault("notes", []).append(f"band:0/{_seen}(nothing near)")
            except Exception:
                pass
        cv = f2.cv_from_snapshot_dict(d, ci + 1)
        cv["ts"] = float(tc)                                  # eyes age = 0, causal
        cv["symbol"] = f"REPLAY/{day_str}"
        # v8f: THIS string is what the committee arms ACCEPT-BEFORE-BOUNCE on. An earlier
        # draft set cv["shadow_law"] instead and was DEAD CODE - f2.cv_from_snapshot_dict()
        # returns a fixed whitelist and dropped it (measured). A live snapshot carries the
        # broker symbol, never "REPLAY/", so the law cannot leak into the old factory.
        out = f2.run_cycle(cv, state, pen, "replay:tapes", now_ts=float(tc),
                           ledger=ledger_obj, shadow=shadow)
        res["cycles"] += 1
        m_t = re.search(r"EXCESS=c(\d+)/h(\d+)m", str(out.get("g3") or ""))
        if m_t:
            toll_hist.append(float(m_t.group(1)))
            res["toll_closes"] = max(res["toll_closes"], int(m_t.group(1)))
            res["toll_minutes"] = max(res["toll_minutes"], float(m_t.group(2)))
        g1 = str(out.get("g1") or "")
        mood = "SLEEP"
        for part in g1.split(" | "):
            if part.startswith("MOOD="):
                mood = part.split("=", 1)[1]
        res["moods"][mood] = res["moods"].get(mood, 0) + 1
        g2 = str(out.get("g2") or "")
        if "LOCATION=NONE" not in g2 and g2:
            res["g2_pass"] += 1
            rung = g2.split("|", 1)[0].replace("LOCATION=", "").strip()
            res["rungs"][rung] = res["rungs"].get(rung, 0) + 1
        g3 = str(out.get("g3") or "")
        fin = out.get("final") or {}
        # TALLY-2 (`2026-11-17`, TODO row 3 measured on the owner's own 6 diaries).
        # The old line fell back to ONE invented word, "(allowed)", whenever the cycle's own
        # list was empty - so it filed "nothing was refused here" INSIDE the refusal tally.
        # Consequence, and it is why row 3 could not be asserted before now: `sum(fails)`
        # equalled `cycles` on EVERY day (the owner's papers: 208/208, 240/240, 124/124,
        # 78/78, 99/99), so `tickets + sum(fails) == cycles` was off by exactly the ticket
        # count and no honest gate could pass. The diary was always right (it stores each
        # cycle's own list); only the tally lied. A cycle that is BOTH allowed and carrying
        # a word now makes the new gate fail out loud instead of hiding in the sum.
        _fl = fin.get("fails") or []
        if _fl:
            for fl in _fl:
                res["fails"][fl] = res["fails"].get(fl, 0) + 1
        else:
            res["allowed"] = res.get("allowed", 0) + 1
        if "VERDICT=BOUNCE" in g3 or "VERDICT=BREAK" in g3:
            res["verdicts"] += 1
            # NOT walk.prices: see the comment above fwd_px (item 46)
            fwd = forward_price(fwd_px, float(tc), 9.0)
            pb4 = forward_price(fwd_px, float(tc), 12.0)
            ep_row = {
                "ep": int(tc), "g2": rung,
                "g3": g3[:200], "allow": bool(fin.get("allow")),
                "ticket": fin.get("ticket"), "price": float(last_px),
                "fwd9": (round(fwd - float(last_px), 2) if fwd is not None else None),
                "pb4": (round(pb4 - float(last_px), 2) if pb4 is not None else None),
                "votes": [{"judge": str(v.get("judge")), "dir": float(v.get("dir") or 0.0)}
                          for v in (d.get("judge_votes") or [])
                          if isinstance(v, dict) and float(v.get("dir") or 0.0)][:16],
            }
            res["episodes"].append(ep_row)
            if res["cycles"] % 40 == 0 and not quiet:
                print(f"[gates_replay] {day_str} {ci + 1}/{len(grid)} "
                      f"cycles | verdicts {res['verdicts']}")
        if fin.get("allow"):
            res["tickets"] += 1
    if json_out:
        try:
            with open(json_out, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(res, fh, ensure_ascii=False, default=str)
        except OSError:
            pass
    if pen is not None:
        try:
            tally = {k: v for k, v in res.items()
                     if k not in ("episodes",) and isinstance(v, (int, float, str))}
            tally["mode"] = "replay:summary"
            tally["line_kind"] = "day"      # law 105: the footer is a THIRD class of line - a
                                            # diary of 103 lines holds 102 cycles + 1 day. Name it
                                            # and the count closes as an equation, not a filter.
            tally["toll_max_closes"] = int(max(toll_hist) if toll_hist else 0)
            pen(tally)
        except Exception:
            pass
    return res


# ----------------------------------------------------------------- the report
def render_text(res: Dict[str, Any]) -> str:
    L = []
    L.append(f"=== gates_replay {REPLAY_VERSION} — day {res.get('day')} "
             f"(src={res.get('src')}) ===")
    if not res.get("ok"):
        L.append(f"NOT RUN: {res.get('reason')}")
        return "\n".join(L)
    cap_txt = f", cap {res['cap_min']}min" if res.get("cap_min") else ""
    if res.get("cap_warmed"):
        cap_txt += f" (+{res['cap_warmed']}min of warming NOT charged, item 31)"
    sk = res.get("skips") or {}
    parts = ", ".join(f"{k} {v}" for k, v in sorted(sk.items()) if v)
    L.append(f"cycles fed           : {res['cycles']}  "
             f"(skipped {res['errors']}{cap_txt}"
             + (f": {parts}" if parts else "") + ")")
    w = res.get("window") or {}
    if w.get("win_start") is not None:
        def _hhmm(ep):
            return datetime.fromtimestamp(float(ep), tz=timezone.utc).strftime("%H:%M")
        warming = 0
        if w.get("first_row") is not None:
            warming = max(0, int(float(w["first_row"]) - float(w["win_start"]))) // 60
        _ce = w.get("cells_end")
        _cells_txt = (f" cells to {_hhmm(_ce)}Z" if _ce is not None
                       and abs(float(_ce) - float(w["win_end"])) > 1 else "")
        L.append(f"day math             : window {_hhmm(w['win_start'])}-"
                 f"{_hhmm(w['win_end'])}Z{_cells_txt}, tape first row "
                 + (f"{_hhmm(w['first_row'])}Z" if w.get("first_row") is not None
                    else "(none)")
                 + f", tape last row "
                 + (f"{_hhmm(w['last_row'])}Z" if w.get("last_row") is not None
                    else "(none)")
                 + f", grid {w.get('cells')} cell(s), warm-up needs 60 candles"
                 + (f" | tail past the tape: {int((res.get('skips') or {}).get(POST_LAST_ROW) or 0)} "
                    f"cell(s) refused for OUT OF DATA, not for a quiet market"
                    if int((res.get("skips") or {}).get(POST_LAST_ROW) or 0) else "")
                 + (f" -> tape starts {warming}min INTO the window"
                    if warming else ""))
    L.append(f"tape rows in RAM     : {res.get('lines', 0)} prints {res.get('prints', 0)}  "
             f"(--max-minutes caps this on small machines)")
    L.append(f"tape clock parsed by : {res.get('ts_parser', '?')}")
    L.append(f"tape scope           : {res.get('tape_scope', '?')}")
    _b = float(res.get("rung_band") or 0.0)
    _seen = int(res.get("levels_seen") or 0)
    _kept = int(res.get("levels_kept") or 0)
    if res.get("fwd_rows") is not None:
        _n_eps = len(res.get("episodes") or [])
        _n_me = sum(1 for e in (res.get("episodes") or []) if e.get("fwd9") is not None)
        L.append(f"scoring rows         : {res['fwd_rows']} print row(s) available to "
                 f"measure a verdict AFTER it was made (+9m fwd9, +12m pb4); "
                 f"verdicts measured {_n_me} of {_n_eps}"
                 + (" - a verdict in the last minutes of the tape cannot be scored; "
                    "that is data, not a bug" if _n_eps and not _n_me else ""))
    L.append(f"mapper band          : " + (
        f"+/-{_b:g} USD of the last print; lines kept {_kept} of {_seen}"
        + (f", map empty on {res.get('band_empty')} cycle(s)" if res.get("band_empty")
           else "") if _b else "OFF (the Mapper draws the whole market)"))
    L.append(f"depth feed           : {res.get('depth', 'depth:off')}")
    L.append(f"order tape (L3)      : {res.get('l3', 'L3: ?')}")
    if res.get("l3_errors"):
        L.append(f"L3 WIRING ERROR    : {res['l3_errors'][:3]}   <- a swallowed "
                 f"exception here once made L3 empty on every cycle (item 28)")
    if res.get("brain_errors"):
        L.append(f"brain errors (first) : {res['brain_errors'][:3]}")
    L.append("G1 moods             : " + ", ".join(f"{k}={v}" for k, v in
                                                   sorted(res["moods"].items())))
    L.append(f"G2 with a rung       : {res['g2_pass']} / {max(1, res['cycles'])}")
    L.append("rung popularity      : " + (", ".join(f"{k}={v}" for k, v in
               sorted(res["rungs"].items(), key=lambda kv: -kv[1])[:12]) or "-"))
    L.append(f"G3 real verdicts     : {res['verdicts']}  (tickets allowed {res['tickets']})")
    # `allowed` is printed OUTSIDE the refusal tally, because it is the ABSENCE of one.
    _tally = ", ".join(f"{k}={v}" for k, v in
                       sorted(res["fails"].items(), key=lambda kv: -kv[1])) or "(nothing refused)"
    _sumf = sum(int(v or 0) for v in res["fails"].values())
    _alw = int(res.get("allowed") or 0)
    L.append("refusal tally        : " + _tally + f"   | allowed {_alw} of {res['cycles']} cycle(s)"
             + ("" if _sumf + _alw == res["cycles"] else
                f"   ** TALLY DOES NOT ADD UP: {_sumf} refused-words + {_alw} allowed != "
                f"{res['cycles']} cycles (a cycle carrying 2 words, or a word that is not a "
                f"refusal) **"))
    eps = res.get("episodes") or []
    if eps:
        L.append("--- verdict episodes (what happened +9m) ---")
        for e in eps[:40]:
            L.append(f"  {datetime.fromtimestamp(e['ep'], tz=timezone.utc):%H:%M:%S} "
                     f"rung={e['g2'] or '-'} allow={int(bool(e['allow']))} "
                     f"fwd9={e['fwd9']} | {e['g3'][:120]}")
    else:
        if not int(res.get("mbo_rows") or 0):
            L.append("--- no verdict episodes: NO ORDER TAPE on this day, so L3 "
                     "has no orders to judge (item 27) - sail a day that has "
                     "data/archive/mbo_*.csv.gz, or feed the live mbo.csv ---")
        else:
            L.append("--- no verdict episodes: the ladder's challenge never paid — "
                     "check toll/tape ---")
    return "\n".join(L)


def merge_days(all_res: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Multi-day aggregate for the night teacher.

    WHY THIS EXISTS (Book Law #12, item 12): `--sea 5` wrote one json PER DAY
    (`gates_sea_20261113_2026-11-09.json` ...) and NOTHING at the plain
    `--json-out` path the shell hands to the teacher - so five sailed days
    graded as "no --sea file given" and the night said NOT HEALTHY while the
    sea was actually fine. A seam between two of our files; per-file tests
    could never see it."""
    good = [r for r in all_res if r.get("ok")]
    days = [str(r.get("day")) for r in all_res]
    out: Dict[str, Any] = {
        "day": (f"{days[0]}..{days[-1]}" if len(days) > 1 else (days[0] if days else "")),
        "days": days, "day_count": len(all_res), "sailed": len(good),
        "ok": bool(good), "cycles": 0, "g2_pass": 0, "verdicts": 0, "tickets": 0,
        "errors": 0, "lines": 0, "prints": 0, "toll_closes": 0, "toll_minutes": 0.0,
        "fails": {}, "allowed": 0, "moods": {}, "rungs": {}, "episodes": [],
        "src": "", "depth": "", "ts_parser": "", "cap_min": 0, "tape_scope": "",
        "rung_band": 0.0, "levels_seen": 0, "levels_kept": 0, "band_empty": 0,
        "lookahead_min": 0.0, "fwd_rows": 0, "measured": 0,
        "mirror": "", "day_order": "",
        "mbo_rows": 0, "ticks_mbo_ignored": 0,
        "tape_days": 0, "jar_days": 0, "l3_days": 0, "fed_by": "",
        # v8.2: provenance ON the paper, so a sea json can be checked against the
        # code that wrote it without asking git (the owner's packets taught me that
        # a json without a version is a rumour).
        "printed_by": REPLAY_VERSION,
        "skip_kinds": {"no-eyes": 0, PRE_FIRST_PRINT: 0, POST_LAST_ROW: 0,
                       "warm-up": 0, "brain": 0, "snapshot": 0},
    }
    srcs, depths = [], []
    for r in good:
        for k in ("cycles", "g2_pass", "verdicts", "tickets", "errors", "lines",
                  "prints", "mbo_rows", "ticks_mbo_ignored"):
            out[k] += int(r.get(k) or 0)
        # v7 (items 25/27): a day sailed from the AUDIT JAR is not a sea. The
        # teacher must be able to say "N raw-tape day(s), M jar day(s)" without
        # re-reading the json of every day, so the counts are made here.
        fb = str(r.get("fed_by") or "")
        if fb == "raw tape":
            out["tape_days"] += 1
        elif fb == "jar":
            out["jar_days"] += 1
        if int(r.get("mbo_rows") or 0) > 0:
            out["l3_days"] += 1
        out["fed_by"] = out["fed_by"] or fb
        out["tape_scope"] = out["tape_scope"] or str(r.get("tape_scope") or "")
        out["mirror"] = out["mirror"] or str(r.get("mirror") or "")
        out["day_order"] = out["day_order"] or str(r.get("day_order") or "")
        out["rung_band"] = float(r.get("rung_band") or 0.0) or out["rung_band"]
        # v8.5 (item 46): `lookahead_min` and `fwd_rows` were per-day only, so the
        # AGGREGATE json - the file the teacher and the owner's receipt actually read
        # - could not tell a scored night from an abstaining one. Neither may be
        # silently zero: `fwd_rows` sums, `lookahead_min` is the day's promise.
        out["lookahead_min"] = float(r.get("lookahead_min") or 0.0) or out["lookahead_min"]
        out["fwd_rows"] = int(out.get("fwd_rows") or 0) + int(r.get("fwd_rows") or 0)
        out["measured"] = int(out.get("measured") or 0) + sum(
            1 for e in (r.get("episodes") or []) if e.get("fwd9") is not None)
        for _bk in ("levels_seen", "levels_kept", "band_empty"):
            out[_bk] = int(out.get(_bk) or 0) + int(r.get(_bk) or 0)
        out["toll_closes"] = max(out["toll_closes"], int(r.get("toll_closes") or 0))
        out["toll_minutes"] = max(out["toll_minutes"], float(r.get("toll_minutes") or 0.0))
        for name, n in (r.get("skips") or {}).items():
            out["skip_kinds"][name] = out["skip_kinds"].get(name, 0) + int(n)
        out["allowed"] = int(out.get("allowed") or 0) + int(r.get("allowed") or 0)
        for k in ("fails", "moods", "rungs"):
            for name, n in (r.get(k) or {}).items():
                out[k][name] = out[k].get(name, 0) + int(n)
        out["episodes"].extend(r.get("episodes") or [])
        for key, acc in (("src", srcs), ("depth", depths)):
            v = str(r.get(key) or "")
            if v and v not in acc:
                acc.append(v)
        out["ts_parser"] = out["ts_parser"] or str(r.get("ts_parser") or "")
        out["cap_min"] = out["cap_min"] or int(r.get("cap_min") or 0)
    out["episodes"] = out["episodes"][:24]          # the report prints a handful
    # v8.2: "none" was ambiguous - it could mean "no days" or "every day refused".
    # A reader must never have to guess, so the empty case names itself.
    out["src"] = ("+".join(srcs) if srcs else
                  ("none:all-days-refused" if all_res else "none:no-days"))
    out["depth"] = "+".join(depths)
    if not good:
        out["reason"] = next((str(r.get("reason")) for r in all_res if r.get("reason")),
                             "no day sailed")
    return out


def _mirror_override_from_argv(argv: Optional[List[str]] = None) -> None:
    """Honour `--mirror-dir` BEFORE the quarantine is set up, not after.

    `isolate_data_dir()` rewrites `config.DATA_DIR` - and the borrowed writers
    (`session_levels.update`, `last_book_state`) read that value AT IMPORT, inside
    their own functions. So an override applied after the quarantine is a no-op for
    THEM and a half-truth for the paper: my first version printed `borrowed writers
    -> /tmp/gm/data/gates_sandbox` while the reset looked in the temp dir, i.e. two
    answers in one receipt. Scanning argv is the only way to be early, since argparse
    itself belongs after the imports. Cheap, exact, and the parsed value below wins
    if the two ever disagree.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    val = None
    for i, tok in enumerate(argv):
        if tok == "--mirror-dir" and i + 1 < len(argv):
            val = argv[i + 1]
        elif tok.startswith("--mirror-dir="):
            val = tok.split("=", 1)[1]
    if not val:
        return
    try:
        import config as _cfg
        mp = Path(val)
        mp.mkdir(parents=True, exist_ok=True)
        _cfg.DATA_DIR = mp      # `isolate_data_dir()` appends its own `gates_sandbox`
    except Exception as exc:
        print(f"[gates_replay] --mirror-dir NOT honoured ({type(exc).__name__}: {exc}); "
              f"the brain stays at the quarantine's own address")


def main(argv: Optional[List[str]] = None) -> int:
    _mirror_override_from_argv()        # must precede the quarantine (item 47)
    import factory2_gates as _f2_iso
    _f2_iso.isolate_data_dir()          # Law #12 item 20: the replayed brain
                                        # WRITES session_levels / last_book_state,
                                        # and a 5-day-old tape must not rewrite
                                        # the LIVE rig's today

    ap = argparse.ArgumentParser(description="factory 2 raw-tape sea (replay-first)")
    ap.add_argument("--date", default=None, help="YYYY-MM-DD")
    ap.add_argument("--days", type=int, default=1, help="last N available days")
    ap.add_argument("--every", type=int, default=180, help="cycle seconds (M3 law)")
    ap.add_argument("--window", default="08:00-23:00", help="UTC window")
    ap.add_argument("--prewarm", type=int, default=150, help="minutes before window")
    ap.add_argument("--synthetic", action="store_true", help="fake tape (meadow of the sea)")
    ap.add_argument("--max-cycles", type=int, default=0)
    ap.add_argument("--quiet", action="store_true", help="no progress lines")
    ap.add_argument("--min-prints", type=int, default=600,
                    help="refuse to judge a window with fewer prints than this "
                         "(a day needs ~600; a short test window needs less)")
    ap.add_argument("--max-minutes", type=int, default=0,
                    help="cap the walk to N minutes after the window start "
                    "(a full day of depth rows is GBs of rows - cap it on small machines)")
    ap.add_argument("--depth", default="auto", choices=("auto", "off"),
                    help="auto = feed the ticks tape's Last/Bid/Ask/Depth rows to "
                    "the borrowed bridge tail (L2) AND the order tape (mbo*.csv"
                    "[.gz], incl. data/archive/) to _MboTail (L3); off = candles "
                    "only. v7: L3 no longer comes from ticks.csv Mbo rows - those "
                    "carry no order id (item 27)")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--write-diary", action="store_true",
                    help="(default already writes) keep the sea diary on disk")
    ap.add_argument("--diary-out", default=None,
                    help="write the sea's diary to this exact path (night-test hook)")
    ap.add_argument("--no-diary", action="store_true", help="diary to stdout only")
    ap.add_argument("--hands-shadow", action="store_true",
                    help="ROOM E after the judge: a PAPER position per GO, written into the same "
                         "diary line. Off by default; it can never send (HALL_ARMED=False, Law #1 TP frozen)")
    ap.add_argument("--out", default=None, help="report txt path")
    ap.add_argument("--json-out", default=None,
                    help="full results (episodes included) for gates_report")
    # `report_evening_gates.sh` has been FORWARDING `--root-dir` since v7 (it is in
    # its "flags the sea takes" list) while the sea had no such argument - i.e. any
    # night that ever used it died on "unrecognized arguments". Now it exists, and it
    # is the knob a test needs to shape a fake repo root (item 41).
    ap.add_argument("--rung-band", type=float, default=RUNG_BAND_USD,
                    help="USD: the Mapper only keeps fight lines within this far of "
                         "the last print (owner's law 2026-11-13, item 42); 0 = draw "
                         "the whole map")
    ap.add_argument("--mirror-dir", default=None,
                    help="where the borrowed brain keeps its learned files (default: "
                         "the repo's quarantine drawer). A rehearsal that wants a "
                         "scratch brain points this at a temp dir; the learned files "
                         "then live at `<dir>/gates_sandbox/`, and the `replay mirror` "
                         "line is printed from THAT address - item 47)")
    ap.add_argument("--keep-mirror", action="store_true",
                    help="let the replay inherit the brain's learned files from the "
                         "last run (default: they are set aside, kept under "
                         "gates_sandbox_prev_<stamp>/, so a rehearsal learns from ITS "
                         "own tape - item 47)")
    ap.add_argument("--root-dir", default=str(ROOT),
                    help="repo root whose data/ may be searched when --data-dir is absent")
    a = ap.parse_args(argv)

    import factory2_gates as f2
    from tools.gates_helpers import RungState

    dd = Path(a.data_dir) if a.data_dir else f2.resolve_data_dir(None)
    # WHERE THE TAPE LIVES is not where our diary lives: the hall's own papers
    # stay in the repo's data/ so the teacher and the fold can always find them
    # (before this, GATES_DATA_DIR=/somewhere parked the diary in /somewhere).
    out_dd = ROOT / "data"
    try:
        out_dd.mkdir(parents=True, exist_ok=True)
    except OSError:
        out_dd = dd
    dd.mkdir(parents=True, exist_ok=True)
    rd = Path(a.root_dir)
    # v8.3 (item 41): ONE flag decides both the day LIST and the file SEARCH, so
    # a named drawer cannot be quietly widened by the repo's own archive.
    scoped = bool(a.data_dir)
    days = []
    if a.date:
        cd = canon_day(a.date)
        if not cd:
            print(f"[gates_replay] --date {a.date!r} is not a day; use YYYY-MM-DD "
                  f"or YYYYMMDD - refusing to guess (Law #12)")
            return 2
        days = [cd]
    else:
        days = available_days(dd, rd, a.days, scoped=scoped) or []
    if not days and not a.synthetic:
        print("[gates_replay] no raw tapes found and --synthetic not given"); return 1
    if not days:
        days = [time.strftime("%Y-%m-%d")]

    diary_path = None
    if not a.no_diary:
        tag = (days[0].replace("-", "") if len(days) == 1
               else time.strftime("%Y%m%d"))
        if a.diary_out:
            diary_path = Path(a.diary_out)
        elif a.write_diary:
            # the evening shell asks for the hall's OWN diary name, so the
            # teacher grades it without a fallback. This flag used to be
            # DECLARED and never read - a dead flag is a lie in a CLI
            # (Book Law #12), so now it means what its help text says.
            diary_path = out_dd / f"gates_shadow_{tag}.jsonl"
        else:
            diary_path = out_dd / f"gates_replay_{tag}.jsonl"
    pen = f2.Pen(str(diary_path) if diary_path else None, dry=diary_path is None)
    state = RungState(None)                       # sea: memory in-RAM, nothing persisted
    all_res: List[Dict[str, Any]] = []
    # item 47: a clean mirror per run. Printed, not assumed, because the number of
    # rungs on day one depends on it and nobody should read this paper without knowing.
    try:
        import config as _cfg
        _addr = Path(_cfg.DATA_DIR)          # the ONE address the brain uses
    except Exception as exc:
        print(f"[gates_replay] mirror check degraded ({type(exc).__name__}: {exc})")
        _addr = mirror_dir(ROOT / "data")
    if a.mirror_dir:
        print(f"[gates_replay] brain address   : {_addr}")
    _mir = quarantine_mirror(_addr, keep=bool(a.keep_mirror))
    print(f"[gates_replay] replay mirror    : {_mir}")
    # item 48: `--sea 2` handed the days to the learner NEWEST FIRST, so 11-12
    # inherited the level book that 11-13 had just written - the future, leaking in
    # through the mirror. Sailing order is chronological from here; the day LIST
    # stays newest-first, because that is what "the last N days" means.
    _order = sorted(days)
    if len(_order) > 1:
        print(f"[gates_replay] day order        : chronological "
              f"({_order[0]} -> {_order[-1]}); the learner may only inherit the PAST")
    for dstr in _order:
        jo = None
        if a.json_out:
            base = Path(a.json_out)
            jo = base if len(days) == 1 else base.with_name(
                f"{base.stem}_{dstr}{base.suffix}")
        res = replay_day(dstr, max(30, a.every), a.window, a.prewarm, dd, rd,
                         pen, state, a.synthetic, a.max_cycles, a.quiet, json_out=jo,
                         depth=a.depth, a_max_minutes=a.max_minutes,
                         min_prints=a.min_prints, scoped=scoped,
                         rung_band=max(0.0, float(a.rung_band)),
                         hands_shadow=bool(a.hands_shadow))
        txt = render_text(res)
        print(txt)
        all_res.append(res)
    agg = merge_days(all_res)
    # DETERMINISM (BELT-1, 2026-11-14): the mirror string names the folder THIS run
    # created, and `quarantine_mirror()` gives each run a fresh name (a timestamp, plus
    # a ".N" counter, plus an "earlier rehearsals waiting beside it" count that grows
    # every time you sail). That is HONEST operator prose and WRONG in a machine record:
    # it made two identical sails print different json and a byte-for-byte rerun test
    # fail on a day whose 50 cycles, 50 g2_pass, 9,714 scoring rows and 0 episodes were
    # IDENTICAL. The paper is now stable; the log keeps the full sentence, because the
    # operator should still see which folder holds yesterday's mirror.
    _raw_mir = str(_mir)
    agg["mirror"] = re.sub(r"; \d+ earlier rehearsal\(s\) waiting beside it", "",
                           re.sub(r"gates_sandbox_prev_[0-9T]+(\.\d+)?",
                                  "gates_sandbox_prev_<RUN>", _raw_mir))
    if agg["mirror"] != _raw_mir:                      # the operator still reads it whole
        print(f"[gates_replay] mirror (full): {_raw_mir[:170]}")
    agg["day_order"] = "chronological" if len(days) > 1 else "single day"
    if a.json_out:
        # the shell/teacher ask for ONE path; multi-day must still land there,
        # and v7 writes it for a SINGLE day too - otherwise the teacher sees
        # `tape_days`/`l3_days` on a 5-day night and nothing at all on a 1-day
        # night, and an absent field is exactly the kind of silence Law L-D
        # forbids (it must read as "not measured", never as "zero").
        try:
            Path(a.json_out).parent.mkdir(parents=True, exist_ok=True)
            with open(a.json_out, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(agg, fh, ensure_ascii=False, indent=1)
        except OSError as exc:
            print(f"[gates_replay] could not write the aggregate json: {exc}")
        # v8.2 (item 33): the aggregate USED to CLOBBER the single day's own json
        # in the same path, because a 1-day `replay_day` writes exactly there first.
        # That is why `window`/`day math` were unreadable from the file a test (or a
        # human) was told to read - the paper silently became the summary. A sibling
        # `<name>.days.json` now keeps every day's own arithmetic, one day or many.
        try:
            sib = Path(a.json_out).with_name(Path(a.json_out).stem + ".days.json")
            with open(sib, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(all_res, fh, ensure_ascii=False, indent=1)
        except OSError as exc:
            print(f"[gates_replay] could not write the per-day json: {exc}")
    good = [r for r in all_res if r.get("ok")]
    total = sum(r["cycles"] for r in good)
    verd = sum(r["verdicts"] for r in good)
    # v8.1 (item 31): "0 cycles" was blamed on the sea when it was really
    # the warming. Say which it is, in the same breath as the count, so a
    # `fed 0` night can never again read as a dead sea.
    _sk = agg["skip_kinds"]
    _cold = int(_sk.get("warm-up", 0)) + int(_sk.get("no-eyes", 0))
    _why = (f" | 0 cycles is the WARMING, not a broken sea: {_cold} cell(s)"
            f" refused before 60 candles existed (item 31)"
            if total == 0 and _cold else "")
    print(f"\n[gates_replay] {len(good)}/{len(all_res)} day(s) sailed, "
          f"{total} cycle(s), {verd} verdict(s). "
          f"HEALTH: {'PASS' if good and total >= 20 else 'CHECK'}"
          f" | diary: {diary_path or '(stdout only)'}" + _why)
    # v7: the honest head-count of WHAT the sea actually ate (items 25/27).
    print(f"[gates_replay] SEA FED BY: {agg['tape_days']} raw-tape day(s), "
          f"{agg['jar_days']} jar day(s) (a jar day has no book and no orders), "
          f"{agg['l3_days']} day(s) with an order tape -> L3 possible; "
          f"{agg['ticks_mbo_ignored']} ticks-tape Mbo row(s) ignored as fake-id")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as fh:
            fh.write("\n\n".join(render_text(r) for r in all_res))
            fh.write(f"\n\n[gates_replay] {len(good)} day(s), {total} cycles, "
                     f"{verd} verdicts\n")
    return 0 if good else 1


if __name__ == "__main__":
    raise SystemExit(main())
