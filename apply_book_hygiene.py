#!/usr/bin/env python3
"""apply_book_hygiene.py — the stale-book cure (book-hygiene-2026-10-07).

ROOT CAUSE (proven 2026-10-07 13:12 live): book level dicts never expire.
Ghost levels from earlier eras (e.g. a bid at the morning peak 4,195.6)
poison the crossed-book sanitize -> published ask 4,195.70 (mid 4,171.10
matched the snapshot exactly). This is ALSO the root cause of the original
frozen ask 4,169.70.

THREE FIXES (all inside bookmap_bridge_provider.py, on top of bridge-fix
and memory-fix):
  H1. Replay v2   — keep only lines inside the BOOKMAP window (era rule),
                    replay the LIVE file the same way (no 64MB hole), honest
                    labels (window restored vs day start).
  H2. Book hygiene — every level gets a last-touched timestamp; levels not
                    touched within BOOKMAP_LEVEL_MAX_AGE (default 900s)
                    expire. Ghosts cannot live for hours.
  H3. Honest quotes — bid/ask = fresh-book extremes (touch tracker is now
                    the fallback, not the primary). FEED GUARD unchanged.

Run from the repo root:  python *book_hygiene*.py     (safe to run twice)
Backup: bookmap_bridge_provider.py.pre_book_hygiene
"""
import shutil
import sys
from pathlib import Path

TARGET = Path("bookmap_bridge_provider.py")

# ---- H1: state gets per-level last-touched timestamps --------------------
H1_FIND = '''        # bridge-fix-2026-10-07: rolling (ts, "B"/"A", price) of fresh depth
        # events — the only live top-of-book source (no Bid/Ask events exist).
        self.depth_touch: List[Any] = []

    def reset(self) -> None:
        self.bids, self.asks = {}, {}
        self.ticks = []
        self.best_bid = self.best_ask = 0.0
        self.last_dt = None
        self.mbo_events = []
        self.depth_touch = []'''

H1_NEW = '''        # bridge-fix-2026-10-07: rolling (ts, "B"/"A", price) of fresh depth
        # events — the only live top-of-book source (no Bid/Ask events exist).
        self.depth_touch: List[Any] = []
        # book-hygiene-2026-10-07: last-touched timestamp per price level so
        # ghost levels expire (root cure of the frozen-quote disease).
        self.level_ts_b: Dict[float, Any] = {}
        self.level_ts_a: Dict[float, Any] = {}

    def reset(self) -> None:
        self.bids, self.asks = {}, {}
        self.ticks = []
        self.best_bid = self.best_ask = 0.0
        self.last_dt = None
        self.mbo_events = []
        self.depth_touch = []
        self.level_ts_b, self.level_ts_a = {}, {}'''

H1B_FIND = '''    __slots__ = ("bids", "asks", "ticks", "best_bid", "best_ask", "last_dt",
                 "mbo_events", "depth_touch")'''

H1B_NEW = '''    __slots__ = ("bids", "asks", "ticks", "best_bid", "best_ask", "last_dt",
                 "mbo_events", "depth_touch", "level_ts_b", "level_ts_a")'''

# ---- H2: handlers record/remove the timestamps --------------------------
H2_FIND = '''        elif event == "DepthBid":
            if operation == "Remove" or size <= 0:
                st.bids.pop(price, None)
            else:
                st.bids[price] = size
                st.depth_touch.append((dt, "B", price))
                if len(st.depth_touch) > 1200:
                    del st.depth_touch[:600]
            st.last_dt = dt
        elif event == "DepthAsk":
            if operation == "Remove" or size <= 0:
                st.asks.pop(price, None)
            else:
                st.asks[price] = size
                st.depth_touch.append((dt, "A", price))
                if len(st.depth_touch) > 1200:
                    del st.depth_touch[:600]
            st.last_dt = dt'''

H2_NEW = '''        elif event == "DepthBid":
            if operation == "Remove" or size <= 0:
                st.bids.pop(price, None)
                st.level_ts_b.pop(price, None)
            else:
                st.bids[price] = size
                st.level_ts_b[price] = dt
                st.depth_touch.append((dt, "B", price))
                if len(st.depth_touch) > 1200:
                    del st.depth_touch[:600]
            st.last_dt = dt
        elif event == "DepthAsk":
            if operation == "Remove" or size <= 0:
                st.asks.pop(price, None)
                st.level_ts_a.pop(price, None)
            else:
                st.asks[price] = size
                st.level_ts_a[price] = dt
                st.depth_touch.append((dt, "A", price))
                if len(st.depth_touch) > 1200:
                    del st.depth_touch[:600]
            st.last_dt = dt'''

# ---- H3: replay v2 (window-filtered + live file included) ---------------
H3_FIND = '''            replayed = 0
            budget = 1200000
            for src in sorted(candidates):
                try:
                    opener = gzip.open if src.endswith(".gz") else open
                    with opener(src, "rt", encoding="utf-8", errors="replace") as fh:
                        for line in fh:
                            if line.endswith("\\n"):
                                self._handle_line(line)
                                replayed += 1
                                if replayed >= budget:
                                    break
                except Exception as exc:
                    logger.warning("BookMap history replay failed for %s: %s",
                                   os.path.basename(src), exc)
                if replayed >= budget:
                    break
            # memory-fix refinement (flow never crosses eras, structure may):
            # drop ticks older than the window so candles/ATR are built only
            # from the current era. Book/depth/wall state is kept either way.
            try:
                win = max(60, int(getattr(config, "BOOKMAP_WINDOW_SECONDS",
                                          getattr(config, "NT_WINDOW_SECONDS", 28800))))
            except Exception:
                win = 10800
            cutoff = datetime.now().astimezone().timestamp() - win
            dropped = 0
            for st in self.instruments.values():
                before = len(st.ticks)
                st.ticks = [t for t in st.ticks
                            if t.get("ts") and t["ts"].timestamp() >= cutoff]
                dropped += before - len(st.ticks)
            if replayed:
                if dropped:
                    logger.info(
                        "BookMap history replay: %d lines from %d recorded file(s); "
                        "day start/overnight gap: dropped %d stale ticks - candles/ATR "
                        "stay honestly shallow until the window fills (map keeps "
                        "structure)", replayed, len(candidates), dropped)
                else:
                    logger.info(
                        "BookMap history replay: %d lines from %d recorded file(s) "
                        "(single-source warm-up; window restored)", replayed,
                        len(candidates))'''

H3_NEW = '''            try:
                win = max(60, int(getattr(config, "BOOKMAP_WINDOW_SECONDS",
                                          getattr(config, "NT_WINDOW_SECONDS", 28800))))
            except Exception:
                win = 10800
            cutoff_ts = datetime.now().astimezone().timestamp() - (win + 300.0)
            replayed = skipped = 0
            budget = 3000000
            # book-hygiene replay v2: the LIVE file is replayed with the same
            # time filter (kills the 64MB catch-up hole) and is always LAST.
            ordered = sorted(candidates)
            if os.path.exists(self.path) and self.path not in ordered:
                ordered = ordered + [self.path]
            for src in ordered:
                try:
                    opener = gzip.open if src.endswith(".gz") else open
                    with opener(src, "rt", encoding="utf-8", errors="replace") as fh:
                        for line in fh:
                            if not line.endswith("\\n"):
                                continue
                            ts_raw = line.split(",", 1)[0]
                            try:
                                ts = _parse_ts(ts_raw)
                                ts_ok = (ts is not None
                                         and ts.timestamp() >= cutoff_ts)
                            except Exception:
                                ts_ok = True
                            if not ts_ok:
                                skipped += 1
                                continue
                            self._handle_line(line)
                            replayed += 1
                            if replayed >= budget:
                                break
                except Exception as exc:
                    logger.warning("BookMap history replay failed for %s: %s",
                                   os.path.basename(src), exc)
                if replayed >= budget:
                    break
            if replayed:
                logger.info(
                    "BookMap history replay v2: %d fresh lines kept (%d stale "
                    "skipped) from %d recorded file(s) (single-source warm-up; "
                    "window restored)", replayed, skipped, len(ordered))
            else:
                logger.info(
                    "BookMap history replay v2: no fresh lines in the recorded "
                    "history (day start/overnight gap; %d stale skipped) - "
                    "candles/ATR stay honestly shallow until the window fills",
                    skipped)'''

# ---- H4: honest quotes from the fresh book ------------------------------
H4_FIND = '''                tb = ta = 0.0
                if st_fix.depth_touch:
                    cutoff_ts = st_fix.depth_touch[-1][0].timestamp() - 30.0
                    cb = [p for ts, sd, p in st_fix.depth_touch
                          if sd == "B" and ts.timestamp() >= cutoff_ts and p > 0]
                    ca = [p for ts, sd, p in st_fix.depth_touch
                          if sd == "A" and ts.timestamp() >= cutoff_ts and p > 0]
                    if cb:
                        tb = max(cb)
                    if ca:
                        ta = min(ca)
                if tb > 0 and ta > 0 and ta >= tb:
                    data["bid"], data["ask"] = tb, ta'''

H4_NEW = '''                # book-hygiene: bid/ask from the FRESH book (levels touched
                # within BOOKMAP_LEVEL_MAX_AGE). Ghost levels cannot publish.
                tb = ta = 0.0
                max_age = float(getattr(config, "BOOKMAP_LEVEL_MAX_AGE", 900.0) or 900.0)
                now_ts = time.time()
                try:
                    fb = [p for p in st_fix.bids
                          if p > 0 and st_fix.level_ts_b.get(p) is not None
                          and (now_ts - st_fix.level_ts_b[p].timestamp()) <= max_age]
                    fa = [p for p in st_fix.asks
                          if p > 0 and st_fix.level_ts_a.get(p) is not None
                          and (now_ts - st_fix.level_ts_a[p].timestamp()) <= max_age]
                    if fb:
                        tb = max(fb)
                    if fa:
                        ta = min(fa)
                except Exception:
                    tb = ta = 0.0
                if not (tb > 0 and ta > 0 and ta >= tb):
                    # fallback: the fresh-touch tracker (bridge-fix)
                    tb = ta = 0.0
                    if st_fix.depth_touch:
                        cutoff_ts = st_fix.depth_touch[-1][0].timestamp() - 30.0
                        cb = [p for ts, sd, p in st_fix.depth_touch
                              if sd == "B" and ts.timestamp() >= cutoff_ts and p > 0]
                        ca = [p for ts, sd, p in st_fix.depth_touch
                              if sd == "A" and ts.timestamp() >= cutoff_ts and p > 0]
                        if cb:
                            tb = max(cb)
                        if ca:
                            ta = min(ca)
                if tb > 0 and ta > 0 and ta >= tb:
                    data["bid"], data["ask"] = tb, ta'''

MARKER_A = "book-hygiene-2026-10-07: last-touched timestamp"
MARKER_B = "st.level_ts_b[price] = dt"
MARKER_C = "book-hygiene replay v2"
MARKER_D = "book-hygiene: bid/ask from the FRESH book"


def load(path):
    raw = path.read_bytes()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    return raw.decode("utf-8").replace("\r\n", "\n"), nl


def save(path, text, nl):
    path.write_bytes((text.replace("\n", nl) if nl != "\n" else text).encode("utf-8"))


def apply_op(text, find, new, marker):
    if marker in text:
        return text, "ALREADY"
    n = text.count(find)
    if n == 1:
        return text.replace(find, new), "PATCHED"
    if n == 0:
        return text, "NOT FOUND"
    return text, "AMBIGUOUS (%dx)" % n


def main():
    print("== book hygiene installer (stale-book cure) ==")
    if not TARGET.exists():
        print("ERROR: run from the repo root (bookmap_bridge_provider.py)")
        return 1
    text, nl = load(TARGET)
    results = []

    text, s1a = apply_op(text, H1B_FIND, H1B_NEW, '"level_ts_b", "level_ts_a"')
    results.append(("H1a slots (level timestamps)", s1a))
    text, s1b = apply_op(text, H1_FIND, H1_NEW, MARKER_A)
    results.append(("H1b state (ghost expiry)", s1b))
    text, s2 = apply_op(text, H2_FIND, H2_NEW, MARKER_B)
    results.append(("H2 handlers (record last-touch)", s2))
    text, s3 = apply_op(text, H3_FIND, H3_NEW, MARKER_C)
    results.append(("H3 replay v2 (window-filtered)", s3))
    text, s4 = apply_op(text, H4_FIND, H4_NEW, MARKER_D)
    results.append(("H4 quotes (fresh-book extremes)", s4))

    problems = []
    width = max(len(n) for n, _ in results) + 4
    for i, (name, status) in enumerate(results, 1):
        print("[{}/5] {} {}".format(i, name.ljust(width, "."), status))
        if status.startswith(("NOT FOUND", "AMBIGUOUS")):
            problems.append(name)

    if any(s == "PATCHED" for _, s in results):
        if not Path(str(TARGET) + ".pre_book_hygiene").exists():
            shutil.copy2(str(TARGET), str(TARGET) + ".pre_book_hygiene")
        save(TARGET, text, nl)
        print("backup: bookmap_bridge_provider.py.pre_book_hygiene (kept out of git)")

    if problems:
        print("\nPROBLEMS:", ", ".join(problems), "-> change nothing, paste output to chat")
        return 1
    print("\nALL DONE -> RESTART the robot (Ctrl+C, then: python main.py --loop)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
