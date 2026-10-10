#!/usr/bin/env python3
"""apply_a1_patch.py -- installs A1 "One Bar Service" (Design 3) into
step2_market_analysis.py. Anchor-based, refuses double-patching, writes only
when EVERY anchor is found exactly once. You keep a .pre_a1 backup first.

What it inserts (4 surgical changes, one file):
  1. module-level: the A1 bar-service engine (constants, cache, serve/store)
  2. head of analyze_market(): serve-or-compute check
  3. tail of analyze_market(): cache store
  4. _power_probe_startup_history(): POWER history ring cap
     (POWER_HISTORY_MAX_BARS, default 300)

Usage (git bash, project root, AFTER your backup):
    cp step2_market_analysis.py step2_market_analysis.py.pre_a1
    python apply_a1_patch.py
    python -m py_compile step2_market_analysis.py     # must print nothing

The full compute path is byte-for-byte the old code between the inserted
blocks; the twin-day runner proves snapshot identity at every bar close.
"""
import os
import sys

TARGET = "step2_market_analysis.py"

MODULE_BLOCK = '''

# --------------------------------------------------------------------------- #
# A1 BAR SERVICE (v1.2, 2026-10-10) -- Design 3 "One Bar Service".
# The full analysis runs ONCE per completed M{A1_BASE_TF_MINUTES} bar; between
# bar closes the cached snapshot is SERVED with:
#   - fresh quote fields: timestamp / price / bid / ask / volume / data_quality
#   - the LIVE SCOUT CHAIN re-run on EVERY serve, with everything the
#     notebook eats: candles -> ATR -> volume profile -> H1/H4 POCs -> order
#     blocks -> session levels -> L2-OFI -> order flow -> L3 -> notebook
#     (doors must keep growing/fading at full per-cycle speed, not bar speed)
#   - the LIVE PAIR re-run on EVERY serve: paper-entry simulator + Escort
#     (fills and position guarding keep their full per-cycle clock speed)
# Verdict fields (technicals, judges, POWER, Shooting plan) belong to the last
# closed bar -- the declared A1 semantic (design review 2026-10-10).
# Force-recompute rules (never serve): a bar closed; history shrank (feed
# reset); cache older than 3 bars (tape-stall guard); news state
# WARNING/BLACKOUT; any serve-path error (fail-open to the full compute).
# --------------------------------------------------------------------------- #
import os as _a1_os

try:
    A1_BASE_TF_MINUTES = max(1, int(_a1_os.environ.get("A1_BASE_TF_MINUTES", "5") or 5))
except Exception:
    A1_BASE_TF_MINUTES = 5
try:
    POWER_HISTORY_MAX_BARS = max(50, int(_a1_os.environ.get("POWER_HISTORY_MAX_BARS", "300") or 300))
except Exception:
    POWER_HISTORY_MAX_BARS = 300

_A1_BAR_SERVICE_CACHE: Dict[str, Any] = {}


def _a1_bucket_index(now, tf_minutes=None):
    """Clock-derived UTC bucket index (epoch minutes // tf). DST-immune."""
    tf = int(tf_minutes or A1_BASE_TF_MINUTES)
    return int((now.timestamp() // 60.0) // tf)


def _a1_last_closed_bucket(now, tf_minutes=None):
    """Bucket index of the last FULLY CLOSED bar (the bucket before now's)."""
    return _a1_bucket_index(now, tf_minutes) - 1


def _a1_news_state_of(snapshot) -> str:
    try:
        return str(getattr(getattr(snapshot, "news", None), "news_state", "") or "").upper()
    except Exception:
        return ""


def _a1_candle_count(market_data) -> int:
    try:
        candles = market_data.get("candles", {})
        if isinstance(candles, dict):
            closes = candles.get("close", [])
            return len(closes) if hasattr(closes, "__len__") else 0
    except Exception:
        pass
    return 0


def _a1_cap_history_bars(history_bars, diagnostics):
    """A1: bound the POWER probe history ring (POWER_HISTORY_MAX_BARS)."""
    if isinstance(history_bars, (list, tuple)) and len(history_bars) > POWER_HISTORY_MAX_BARS:
        diagnostics = dict(diagnostics or {})
        diagnostics["history_capped_from"] = len(history_bars)
        history_bars = history_bars[-POWER_HISTORY_MAX_BARS:]
        diagnostics["history_capped_to"] = len(history_bars)
    return history_bars, diagnostics


def _a1_strip_live_pair_notes(notes):
    """Drop the stored cycle's live-pair + serve notes (re-run on each serve)."""
    drop = ("PAPER ENTRY: ", "ESCORT: ", "SIGNAL MAP: ", "A1 bar-service:")
    return [n for n in (notes or []) if not str(n).startswith(drop)]


def _a1_refresh_scout(market_data, served, price, spread_now):
    """v1.2: rebuild EVERYTHING the scout notebook eats, live, every serve:
    candles -> ATR (with the fallback ladder) -> volume profile -> H1/H4 POCs
    -> order blocks -> session levels -> persistent-L2 OFI -> order flow ->
    L3 -> notebook ingest -> add_history -> render. The notebook's memory
    doors (poc_day/vwap/value_edge/htf_poc/supply-demand/session) and the
    door band (ATR-sensitive) therefore keep full per-cycle cadence, exactly
    as in the pre-A1 world. Only the verdict layer (judges/POWER/technicals/
    signal engine) stays cached at bar cadence. Mirrors analyze_market's own
    constructions verbatim. Returns (scout_notes, atr_live)."""
    open_, high, low, close, vol = _extract_candles(market_data)
    ta = TechnicalAnalyzer()
    atr_live = 0.0
    if len(close) >= 2:
        atr_live = ta.compute_atr(high, low, close)
        if atr_live <= 0:
            atr_live = float(np.mean(np.asarray(high, dtype=float) -
                                     np.asarray(low, dtype=float)))
    tick_data = market_data.get("tick_data") or []
    now = served.timestamp

    # persistent L2 -> order flow (as in analyze_market)
    l2 = _get_persistent_l2()
    for upd in market_data.get("book_updates") or []:
        l2.update(upd.get("bids"), upd.get("asks"))
    bid_depth = market_data.get("bid_depth") or {}
    ask_depth = market_data.get("ask_depth") or {}
    book = market_data.get("order_book")
    if (not bid_depth or not ask_depth) and book:
        bid_depth = bid_depth or dict(book.get("bids", []))
        ask_depth = ask_depth or dict(book.get("asks", []))
    if bid_depth or ask_depth:
        l2.update(bid_depth, ask_depth)
        _save_book_state(bid_depth, ask_depth)
    l2_metrics = l2.summary(bid_depth, ask_depth)
    try:
        l2.absorption_events = 0
        l2.absorption_net = 0
    except Exception:
        pass
    order_flow = OrderFlowAnalyzer().analyze_tick_data(
        tick_data, bid_depth, ask_depth, l2_metrics=l2_metrics)

    # L3 (as in analyze_market)
    l3 = Level3OrderBookAnalyzer(large_size=float(
        getattr(config, "L3_LARGE_ORDER_LOTS",
                getattr(config, "L3_WHALE_THRESHOLD", 100.0))))
    for ev in market_data.get("order_events") or []:
        l3.process_order_event(ev)
    if book:
        l3.update_order_book(book.get("bids", []), book.get("asks", []))
    level3 = l3.analyze()

    # volume profile / htf poc / order blocks (as in analyze_market)
    vp = VolumeProfileAnalyzer().analyze(high, low, close, vol) if len(close) else \
        VolumeProfileMetrics(timestamp=now)
    htf_poc = {}
    try:
        _vpa = VolumeProfileAnalyzer()
        for _tf, _n in (("H1", 60), ("H4", 240)):
            if len(close) >= _n:
                _p, _vh, _vl = _vpa.compute_poc_and_value_area(
                    high[-_n:], low[-_n:], vol[-_n:])
                if _p > 0:
                    htf_poc[_tf] = float(_p)
    except Exception:
        htf_poc = {}
    try:
        import config as _cfg
        ob_enabled = getattr(_cfg, "ORDER_BLOCKS_ENABLED", True)
    except ImportError:
        ob_enabled = True
    order_blocks = _detect_order_blocks(high, low, close, open_) if ob_enabled else []

    # notebook: ingest then remembered doors then render (as in analyze_market)
    scout_notes = []
    signal_map = {}
    try:
        from signal_team import build_signal_map as _a1_bsm, describe as _a1_dsm
        from signal_team import get_book as _a1_gb
        signal_map = _a1_bsm(level3, price, atr_live, config, order_flow,
                             spread=spread_now)
        _hist = []
        if getattr(vp, "poc", 0):
            _hist.append({"price": float(vp.poc), "kind": "poc_day"})
        if getattr(vp, "vwap", 0):
            _hist.append({"price": float(vp.vwap), "kind": "vwap"})
        for _va in ("value_area_high", "value_area_low"):
            _vv = getattr(vp, _va, 0)
            if _vv:
                _hist.append({"price": float(_vv), "kind": "value_edge"})
        try:
            import session_levels as _sl
            _st = _sl.update(price, DATA_DIR() if callable(globals().get("DATA_DIR"))
                             else "data")
            _hist.extend(_sl.levels_for_map(_st))
        except Exception:
            pass
        for _k, _v in (htf_poc or {}).items():
            if _v:
                _hist.append({"price": float(_v), "kind": "htf_poc"})
        for _ob in (order_blocks or [])[:8]:
            try:
                _lo, _hi = float(_ob.get("low", 0)), float(_ob.get("high", 0))
                _kind = "demand" if str(_ob.get("type", "")).lower().startswith("d") else "supply"
                if _lo:
                    _hist.append({"price": _lo, "kind": _kind})
                if _hi and abs(_hi - _lo) > 1e-9:
                    _hist.append({"price": _hi, "kind": _kind})
            except Exception:
                continue
        if _hist:
            _a1_gb().add_history(price, atr_live, _hist, config)
            signal_map = _a1_gb()._render(
                price, max(atr_live, 1e-9),
                float(getattr(config, "L3_WHALE_THRESHOLD", 10.0)), 0, level3,
                order_flow, spread_now)
        scout_notes.append(_a1_dsm(signal_map))
    except Exception as _a1_scout_err:
        scout_notes.append("scout unavailable: "
                           f"{type(_a1_scout_err).__name__}: {_a1_scout_err}")
    served.order_flow = order_flow
    served.level3 = level3
    served.signal_map = signal_map
    return scout_notes, atr_live


def _a1_try_serve(market_data, now, price):
    """Return the served snapshot when no bar has closed since the last full
    compute, else None (caller falls through to the full compute). Any
    exception propagates to the caller's fail-open handler."""
    entry = _A1_BAR_SERVICE_CACHE.get("entry")
    if not entry:
        return None
    key = (str(market_data.get("symbol", "")), _a1_last_closed_bucket(now))
    if entry.get("key") != key:
        return None                                  # a bar closed: full compute
    snap = entry.get("snapshot")
    if snap is None:
        return None
    n_stored = entry.get("candle_count") or 0
    n_now = _a1_candle_count(market_data)
    if n_stored and n_now and n_now < n_stored:
        return None                                  # feed reset / history shrank
    try:
        age_s = (now - entry["stored_at"]).total_seconds()
    except Exception:
        return None
    if age_s > 3 * A1_BASE_TF_MINUTES * 60 or age_s < -60:
        return None                                  # tape-stall / clock-skew guard
    if _a1_news_state_of(snap) in ("WARNING", "BLACKOUT"):
        return None                                  # news window: always fresh

    import copy as _a1_copy
    served = _a1_copy.deepcopy(snap)
    bid = float(market_data.get("bid") or price)
    ask = float(market_data.get("ask") or price)
    served.timestamp = now
    served.price = price
    served.bid = bid
    served.ask = ask
    served.volume = float(market_data.get("volume") or 0.0)
    try:
        served.data_quality = dict(market_data.get("data_quality") or {})
    except Exception:
        pass
    _f_ask = float(market_data.get("ask") or 0.0)
    _f_bid = float(market_data.get("bid") or 0.0)
    _spread_now = abs(_f_ask - _f_bid) if _f_ask and _f_bid else 0.0

    # ---- live scout chain (full notebook feed), EVERY serve ----------------
    scout_notes, _atr = _a1_refresh_scout(market_data, served, price, _spread_now)

    # ---- live pair: paper-entry simulator + Escort, re-run EVERY serve -----
    pair_notes = []
    _escort_plan = {}
    try:
        from paper_entry_simulator import simulate_entry as _a1_sim
        _entry_simulation = _a1_sim(served.shot, price, config=config, now=now)
        served.entry_simulation = _entry_simulation
        pair_notes.append("PAPER ENTRY: " + str(_entry_simulation.get("status", "UNKNOWN"))
                          + " - " + str(_entry_simulation.get("reason", "")))
        _escort_plan = _entry_simulation.get("filled_plan") or {}
    except Exception as _a1_sim_err:
        pair_notes.append("paper entry simulator unavailable: "
                          f"{type(_a1_sim_err).__name__}: {_a1_sim_err}")
    try:
        from escort_team import escort_cycle as _a1_esc, describe as _a1_esc_say
        from signal_team import get_book as _a1_book2
        _escort = _a1_esc(_escort_plan, served.signal_map, price, _atr, _spread_now,
                          served.order_flow, served.footprint, served.level3,
                          served.news, served.divergence, _a1_book2(), config,
                          bid=bid, ask=ask)
        served.escort = _escort
        pair_notes.append(_a1_esc_say(_escort))
    except Exception as _a1_esc_err:
        pair_notes.append("escort unavailable: "
                          f"{type(_a1_esc_err).__name__}: {_a1_esc_err}")

    served.notes = (_a1_strip_live_pair_notes(served.notes) + scout_notes + pair_notes
                    + [f"A1 bar-service: verdicts cached from bar close (bucket #{key[1]}, "
                       f"computed {entry['stored_at'].strftime('%H:%M:%S')}Z); "
                       "scout/orderflow/L3, fills, escort, quotes LIVE"])
    logger.debug("A1 bar-service: SERVE key=%s (computed %s)", key, entry["stored_at"])
    return served


def _a1_cache_store(snapshot, market_data, now, spread_now=0.0):
    """Remember the full-compute snapshot for serving until the next bar close."""
    _A1_BAR_SERVICE_CACHE["entry"] = {
        "key": (str(market_data.get("symbol", "")), _a1_last_closed_bucket(now)),
        "snapshot": snapshot,
        "stored_at": now,
        "candle_count": _a1_candle_count(market_data),
        "spread_now": float(spread_now or 0.0),
    }
    logger.debug("A1 bar-service: STORE key=%s", _A1_BAR_SERVICE_CACHE["entry"]["key"])
'''

HEAD_ANCHOR = '''    now = now or datetime.now(timezone.utc)
    price = float(market_data.get("price") or market_data.get("bid") or 0.0)
'''

HEAD_INSERT = HEAD_ANCHOR + '''
    # A1 BAR SERVICE: serve the cached snapshot between bar closes (Design 3).
    try:
        _a1_served = _a1_try_serve(market_data, now, price)
        if _a1_served is not None:
            return _a1_served
    except Exception as _a1_serve_err:
        logger.warning("A1 bar-service serve path failed -> full compute: %s",
                       _a1_serve_err)
'''

PROBE_ANCHOR = "    return live_ticks or [], diagnostics, history_bars"

PROBE_INSERT = '''    # A1: bound the POWER probe history ring (POWER_HISTORY_MAX_BARS).
    history_bars, diagnostics = _a1_cap_history_bars(history_bars, diagnostics)
    return live_ticks or [], diagnostics, history_bars'''

TAIL_ANCHOR = '''    logger.info("analyze_market() -> %s (strength=%s, confidence=%s, news=%s)",
                direction, strength, confidence, news_state)
    return snapshot
'''

TAIL_INSERT = '''    logger.info("analyze_market() -> %s (strength=%s, confidence=%s, news=%s)",
                direction, strength, confidence, news_state)
    # A1 BAR SERVICE: store for serving until the next bar close.
    try:
        _a1_cache_store(snapshot, market_data, now, spread_now=_spread_now)
    except Exception as _a1_store_err:
        logger.warning("A1 bar-service store failed (serving disabled): %s",
                       _a1_store_err)
    return snapshot
'''


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else TARGET
    if not os.path.exists(path):
        print(f"[abort] {path} not found (run from the project root, or pass the path)")
        sys.exit(1)
    src = open(path, encoding="utf-8").read()

    if "A1 BAR SERVICE" in src:
        print("[abort] this file already contains the A1 bar service -- refusing to patch twice")
        sys.exit(1)

    checks = [
        ("module anchor", "_POWER_PROBE_STARTUP_CACHE = {}", 1),
        ("probe-history anchor", PROBE_ANCHOR, 1),
        ("analyze_market head anchor", HEAD_ANCHOR, 1),
        ("analyze_market tail anchor",
         'logger.info("analyze_market() -> %s (strength=%s, confidence=%s, news=%s)",', 1),
    ]
    ok = True
    for name, needle, want in checks:
        n = src.count(needle)
        if n != want:
            print(f"[abort] anchor '{name}' found {n}x (expected {want}x) -- "
                  "your file differs from the reviewed base; NOTHING was written")
            ok = False
    if not ok:
        sys.exit(1)

    out = src.replace("_POWER_PROBE_STARTUP_CACHE = {}\n",
                      "_POWER_PROBE_STARTUP_CACHE = {}\n" + MODULE_BLOCK + "\n", 1)
    out = out.replace(PROBE_ANCHOR, PROBE_INSERT, 1)
    out = out.replace(HEAD_ANCHOR, HEAD_INSERT, 1)
    out = out.replace(TAIL_ANCHOR, TAIL_INSERT, 1)

    tmp = path + ".a1_tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(out)
    import py_compile
    try:
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as e:
        os.remove(tmp)
        print(f"[abort] patched file failed py_compile -- NOTHING was written:\n{e}")
        sys.exit(1)
    os.replace(tmp, path)
    print(f"[ok] A1 bar service installed into {path}")
    print(f"     lines: {src.count(chr(10))} -> {out.count(chr(10))} "
          f"(+{out.count(chr(10)) - src.count(chr(10))})")
    print("     4 insertions: engine block, serve check, cache store, history cap")
    print("     next: python -m py_compile step2_market_analysis.py  (already verified)")
    print("           then the proof ladder: test_a1_rig.py -> twin_day_runner.py")


if __name__ == "__main__":
    main()
