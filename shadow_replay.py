#!/usr/bin/env python3
"""shadow_replay.py -- Level B v1: replay the day's signals through the ROBOT'S
OWN exit logic (walls + value gate + profit-lock trail), on the recorded tape.

WHY THIS EXISTS (the owner's objection, 2026-10-09, correct): grading signals
with a FIXED TP/SL ruler measures the ruler, not the robot. The v8 money path
is: wall brackets (TP before the opposing wall, SL behind the shelter wall,
ATR only as fallback) -> value gate (RR >= 1.2 AND reward >= 1.0 ATR) ->
Escort profit-lock trail. Only a replay of THAT path answers "would the real
logic have made money?"

WHAT IS REAL HERE (no guessing):
- The walls are the ROBOT'S OWN, recorded per cycle in its diary
  (snapshots_history.jsonl / diary_YYYYMMDD.jsonl, field 'signal_map' with
  'above'/'below' doors: price + size + resistance). Nothing is reconstructed
  from raw book events in v1.
- compute_wall_brackets() and value_gate() are the v8 functions, embedded
  VERBATIM from wall_brackets.py (the shipped v8 module).
- The price walk is the recorded tape, read with the audit's own conventions
  (via tf_study's proven reader), one minute at a time, walking FORWARD only -
  no look-ahead: each candle is checked strictly after the entry minute.

WHAT IS SIMPLIFIED IN v1 (said out loud, v2 will close these):
- Entry is the signal's recorded price (the paper-fill model is not replayed).
- The trail is the Escort job-4 rule in simplified form: once unrealised
  profit >= 0.5 x risk, trail the stop at high-water-mark -/+ 0.5 x ATR,
  never widening. The full five-job Escort (target-nearer, divergence close,
  news bombs) needs the wall stream over time = v2.
- Judges and POWER are NOT re-voted: the day's real signals are the input.
  Re-voting the whole panel on the recorded book = v2 (the full shadow robot).

Usage (git bash, project folder, after the robot's day is done):
    python shadow_replay.py --date 2026-10-09

Output: console summary + data/shadow_replay_<date>.txt + .csv (one row per
signal). The BENCHMARK row (fixed 2.0/3.5 x AUDIT_ATR, no gate, no trail)
uses the Editor's convention and must match decision_ledger.py's ALL SIGNALS.
"""
import argparse, bisect, csv, os, sys
from datetime import datetime
from types import SimpleNamespace

import tf_study as T   # the proven tape reader / candles / walk conventions


# ---------------------------------------------------------------- v8 engine --
# Verbatim from wall_brackets.py (v8, shipped 2026-10-07). Pure functions; the
# only inputs are the snapshot's signal_map/bid/ask, price and ATR.
def _wb_num(value, default=None):
    import math
    try:
        x = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return x if math.isfinite(x) else default


def _doors(snapshot, key):
    sm = getattr(snapshot, "signal_map", None)
    if not isinstance(sm, dict):
        return []
    out = []
    for raw in (sm.get(key) or []):
        if not isinstance(raw, dict):
            continue
        px = _wb_num(raw.get("price"))
        size = _wb_num(raw.get("size"), 0.0) or 0.0
        if px is None or px <= 0 or size <= 0:
            continue
        out.append({"price": px, "size": size})
    return out


def compute_wall_brackets(action, price, atr, snapshot):
    """Return (sl, tp, notes). (None, None, notes) = no trusted wall -> fallback."""
    notes = []
    px = _wb_num(price)
    a = _wb_num(atr, 0.0) or 0.0
    if px is None or px <= 0:
        return None, None, ["wall brackets: no valid price"]

    bid = _wb_num(getattr(snapshot, "bid", 0.0), 0.0) or 0.0
    ask = _wb_num(getattr(snapshot, "ask", 0.0), 0.0) or 0.0
    spread = (ask - bid) if (ask > bid > 0) else 0.0
    buf = max(spread * 2.0, 0.15 * a)
    min_lots = 5.0  # WALL_MIN_LOTS default; trusts big walls only

    is_buy = str(action).upper() == "BUY"
    opposing = [d for d in _doors(snapshot, "above" if is_buy else "below")
                if d["size"] >= min_lots and (d["price"] > px if is_buy else d["price"] < px)]
    behind = [d for d in _doors(snapshot, "below" if is_buy else "above")
              if d["size"] >= min_lots and (d["price"] < px if is_buy else d["price"] > px)]
    if not opposing:
        return None, None, ["wall brackets: no trusted opposing wall -> fallback"]
    target_wall = min(opposing, key=lambda d: d["price"] - px if is_buy else px - d["price"])
    tp = (target_wall["price"] - buf) if is_buy else (target_wall["price"] + buf)

    if behind:
        shelter = max(behind, key=lambda d: d["price"] if is_buy else -d["price"])
        sl = (shelter["price"] - buf) if is_buy else (shelter["price"] + buf)
        sl_src = f"wall {shelter['price']:.2f} ({shelter['size']:.0f} lots)"
    else:
        sl = (px - 2.0 * a) if is_buy else (px + 2.0 * a)
        sl_src = "2xATR fallback (no wall behind)"

    ok = (tp > px > sl) if is_buy else (tp < px < sl)
    if not ok:
        return None, None, ["wall brackets: unsafe geometry -> fallback"]
    notes.append(f"wall brackets: TP {tp:.2f} (wall {target_wall['price']:.2f}/"
                 f"{target_wall['size']:.0f} lots), SL {sl:.2f} ({sl_src})")
    return sl, tp, notes


def value_gate(action, price, sl, tp, atr, config):
    """Reward must beat risk AND be worth taking. Fail-closed on missing numbers."""
    px, s, t, a = _wb_num(price), _wb_num(sl), _wb_num(tp), _wb_num(atr, 0.0) or 0.0
    if px is None or s is None or t is None:
        return False, "value gate: missing price/SL/TP numbers"
    reward = abs(t - px)
    risk = abs(px - s)
    if risk <= 0 or reward <= 0:
        return False, "value gate: degenerate reward/risk"
    rr = reward / risk
    min_rr = _wb_num(getattr(config, "ENTRY_MIN_RR", 1.2), 1.2)
    min_reward_atr = _wb_num(getattr(config, "ENTRY_MIN_REWARD_ATR", 1.0), 1.0)
    if rr < min_rr:
        return False, (f"value gate: reward/risk {rr:.2f} < {min_rr:.2f} "
                       f"(reward {reward:.2f}, risk {risk:.2f}) - not worth the risk")
    if a > 0 and reward < min_reward_atr * a:
        return False, (f"value gate: reward {reward:.2f} < {min_reward_atr:.2f} ATR "
                       f"({min_reward_atr * a:.2f}) - no value in the target")
    return True, f"value gate: R:R {rr:.2f}, reward {reward:.2f} vs risk {risk:.2f}"
# ------------------------------------------------------------------ v8 end --


CFG = SimpleNamespace(ENTRY_MIN_RR=1.2, ENTRY_MIN_REWARD_ATR=1.0)  # robot defaults


def load_diary(date_str):
    """(times, records) for that day from the per-day diary, or the history file.
    Each record kept as (aware-UTC dt, dict with signal_map/price)."""
    compact = date_str.replace("-", "")
    candidates = [os.path.join("data", f"diary_{compact}.jsonl"),
                  "data/snapshots_history.jsonl", "snapshots_history.jsonl"]
    times, records = [], []
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line[0] != "{":
                        continue
                    try:
                        rec = __import__("json").loads(line)
                    except ValueError:
                        continue
                    dt = T.parse_ts(rec.get("timestamp"))
                    if dt is None or not T.on_day(dt, date_str):
                        continue
                    times.append(dt)
                    records.append(rec)
            if records:
                break                     # per-day diary is complete; stop there
        except OSError:
            continue
    order = sorted(range(len(times)), key=lambda i: times[i])
    return [times[i] for i in order], [records[i] for i in order]


def walk_shadow(entry_dt, direction, entry, sl, tp, atr, keys, candles,
                max_min, cost, trail_on=True):
    """Walk M1 candles forward with the robot's exit stack:
    SL (incl. profit-lock trail) checked before TP inside a candle (conservative).
    Trail: once unrealised >= 0.5 x risk (Escort job-4 'only trail once it is
    working'), stop follows high-water-mark -/+ 0.5 x ATR, never widening.
    Returns (outcome, pts, note)."""
    buy = direction == "BUY"
    i = T.walk_start(keys, entry_dt)
    if i is None:
        return ("last_bar", None, "")
    max_bars = max(1, int(max_min))          # M1 candles: 1 bar = 1 minute
    seg = candles[i:i + max_bars]
    if not seg:
        return ("last_bar", None, "")
    risk = abs(entry - sl)
    stop, target = sl, tp
    hwm = entry
    trailed = False
    for c in seg:
        _, h, l, cl, _ = c
        # update high-water mark first (this candle's extremes may both count)
        hwm = max(hwm, h) if buy else min(hwm, l)
        # trail check (on the close of each candle, like Escort's cycle)
        if trail_on and risk > 0:
            r = ((hwm - entry) if buy else (entry - hwm)) / risk
            if r >= 0.5:
                new_stop = (hwm - 0.5 * atr) if buy else (hwm + 0.5 * atr)
                if (new_stop > stop) if buy else (new_stop < stop):
                    stop = new_stop
                    trailed = True
        # exit checks: STOP (original or trailed) first - conservative
        hit_stop = (l <= stop) if buy else (h >= stop)
        hit_tp = (h >= target) if buy else (l <= target)
        if hit_stop:
            out = "TRAIL" if trailed and stop != sl else "SL"
            pts = (stop - entry) if buy else (entry - stop)
            return (out, pts - cost, f"stop {stop:.2f}")
        if hit_tp:
            return ("TP", ((target - entry) if buy else (entry - target)) - cost, f"tp {target:.2f}")
    resid = (seg[-1][3] - entry) if buy else (entry - seg[-1][3])
    return ("TIMEOUT", resid - cost, f"close {seg[-1][3]:.2f}")


def walk_benchmark(entry_dt, direction, entry, const_atr, keys, candles,
                   max_min, cost, sl_m=2.0, tp_m=3.5, tf=1):
    """The fixed ruler (Editor/ledger convention): constant ATR, no gate, no trail.
    v1.1: walks the LEDGER'S candle size (tf, default M5) so the ALL-signals row
    reconciles with decision_ledger.py exactly - same ruler, same walk, same
    walkability rule. The SHADOW itself keeps its finer M1 walk."""
    sl = entry - sl_m * const_atr if direction == "BUY" else entry + sl_m * const_atr
    tp = entry + tp_m * const_atr if direction == "BUY" else entry - tp_m * const_atr
    buy = direction == "BUY"
    i = T.walk_start(keys, entry_dt)
    if i is None:
        return (None, None)
    seg = candles[i:i + max(1, int(max_min) // tf)]
    if not seg:
        return (None, None)
    for c in seg:
        _, h, l, cl, _ = c
        if (l <= sl) if buy else (h >= sl):
            return ("SL", -sl_m * const_atr - cost)
        if (h >= tp) if buy else (l <= tp):
            return ("TP", tp_m * const_atr - cost)
    resid = (seg[-1][3] - entry) if buy else (entry - seg[-1][3])
    return ("TIMEOUT", resid - cost)


def main():
    ap = argparse.ArgumentParser(description="Level B v1: wall-logic shadow replay")
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--cost", type=float, default=float(os.environ.get("AUDIT_SPREAD", "0.50")))
    ap.add_argument("--max-min", type=int, default=int(os.environ.get("AUDIT_MAX_HOLD_MIN", "180")))
    args = ap.parse_args()
    const_atr = float(os.environ.get("AUDIT_ATR", "3.18"))

    lines = []
    say = lines.append
    say("=" * 100)
    say(f" SHADOW REPLAY (Level B v1) - {args.date}  the robot's OWN exit logic on the recorded day")
    say(" walls: the robot's own diary signal_map | brackets+gate: v8 code verbatim |")
    say(" trail: Escort job-4 simplified (r>=0.5 -> trail 0.5 ATR, never widen) | walk: M1, no look-ahead")
    say(" v1 does NOT re-vote judges/POWER (that is v2) and does not replay the paper-fill model")
    say("=" * 100)

    trades, err = T.read_day_trades(args.date)
    if err:
        say(f" [no tape] {err}")
        print("\n".join(lines)); sys.exit(0)
    signals, err = T.read_decisions(args.date)
    if err:
        say(f" [no decisions] {err}")
        print("\n".join(lines)); sys.exit(0)
    dtimes, drecs = load_diary(args.date)
    if not dtimes:
        say(" [no diary] no diary_*.jsonl / snapshots_history.jsonl records on this date -")
        say("   the shadow needs the robot's own recorded walls (field 'signal_map').")
        print("\n".join(lines)); sys.exit(0)

    keys1, candles1 = T.build_candles(trades, 1)     # M1 walk
    keys5, candles5 = T.build_candles(trades, 5)     # M5 ATR (robot's clock)
    atrs5 = T.atr_series(candles5)

    say(f" tape: {len(trades)} prints | signals: {len(signals)} | diary records: {len(dtimes)}")
    say("")

    rows_out = []
    n_gate_skip = n_wall = n_fallback = n_nodiary = n_lastbar = 0
    sh_pts = sh_n = sh_wins = 0
    bm_pts = bm_n = 0            # benchmark on the SHADOW'S TAKEN subset
    bma_pts = bma_n = 0          # benchmark on ALL signals (must match the ledger)
    diag_ages = []
    for ts, direction, entry, conf in signals:
        # nearest diary record at/before the signal (the walls the robot saw)
        j = bisect.bisect_right(dtimes, ts) - 1
        if j < 0:
            rows_out.append({"timestamp": str(ts), "direction": direction, "price": f"{entry:.2f}",
                             "outcome": "NO_DIARY", "pts": "", "bracket": "-", "gate": "-",
                             "detail": "no diary record at/before this signal"})
            n_nodiary += 1
            continue
        diag_ages.append((ts - dtimes[j]).total_seconds())
        rec = drecs[j]
        snap = SimpleNamespace(signal_map=rec.get("signal_map") or {},
                               bid=0.0, ask=0.0)
        # M5 ATR of the last closed candle (the robot's clock)
        k5 = T.last_closed_idx(keys5, ts, 5)
        atr = atrs5[k5] if k5 >= 0 else None
        if not atr or atr <= 0:
            atr = const_atr    # honest fallback, reported in the row

        sl, tp, bnotes = compute_wall_brackets(direction, entry, atr, snap)
        if sl is None:         # robot's fallback: ATR brackets
            sl = entry - 2.0 * atr if direction == "BUY" else entry + 2.0 * atr
            tp = entry + 3.5 * atr if direction == "BUY" else entry - 3.5 * atr
            bracket = "ATR fallback"
            n_fallback += 1
        else:
            bracket = "wall"
            if bnotes and "2xATR fallback (no wall behind)" in bnotes[0]:
                bracket = "wall TP + ATR SL"
            n_wall += 1
        ok, gwhy = value_gate(direction, entry, sl, tp, atr, CFG)
        # the fixed-ruler benchmark runs for EVERY walkable signal (gate or not),
        # on the LEDGER'S M5 walk, so its ALL-signals total reconciles exactly
        bo, bp = walk_benchmark(ts, direction, entry, const_atr, keys5, candles5,
                                args.max_min, args.cost, tf=5)
        if bp is not None:
            bma_pts += bp; bma_n += 1
        if not ok:
            rows_out.append({"timestamp": str(ts), "direction": direction, "price": f"{entry:.2f}",
                             "outcome": "GATE_SKIP", "pts": "0.00", "bracket": bracket,
                             "gate": "FAIL", "detail": gwhy})
            n_gate_skip += 1
            continue
        outcome, pts, note = walk_shadow(ts, direction, entry, sl, tp, atr,
                                         keys1, candles1, args.max_min, args.cost)
        if pts is None:
            rows_out.append({"timestamp": str(ts), "direction": direction, "price": f"{entry:.2f}",
                             "outcome": "LAST_BAR", "pts": "", "bracket": bracket,
                             "gate": "PASS", "detail": "signal inside the tape's last candle"})
            n_lastbar += 1
            continue
        sh_pts += pts; sh_n += 1; sh_wins += 1 if pts > 0 else 0
        if bp is not None:
            bm_pts += bp; bm_n += 1
        rows_out.append({"timestamp": str(ts), "direction": direction, "price": f"{entry:.2f}",
                         "outcome": outcome, "pts": f"{pts:+.2f}", "bracket": bracket,
                         "gate": "PASS", "detail": note + " | " + (bnotes[0] if bnotes else "")})

    say(f" {'':34} {'N':>5} {'PTS':>10} {'PTS/SIG':>9} {'WR%':>6}")
    say(" " + "-" * 70)
    say(f" {'SHADOW (walls+gate+trail)':<34} {sh_n:>5} {sh_pts:>+10.1f} "
        f"{(sh_pts / sh_n if sh_n else 0):>+9.2f} {(100.0 * sh_wins / sh_n if sh_n else 0):>6.1f}")
    say(f" {'BENCHMARK fixed ruler, same subset':<34} {bm_n:>5} {bm_pts:>+10.1f} "
        f"{(bm_pts / bm_n if bm_n else 0):>+9.2f}")
    say(f" {'BENCHMARK fixed ruler, ALL signals':<34} {bma_n:>5} {bma_pts:>+10.1f} "
        f"{(bma_pts / bma_n if bma_n else 0):>+9.2f}")
    say(" " + "-" * 70)
    say(f" signals: {len(signals)} | wall brackets: {n_wall} | ATR fallback: {n_fallback} | "
        f"value-gate skips: {n_gate_skip} | no diary: {n_nodiary} | last bar: {n_lastbar}")
    if diag_ages:
        import statistics as st
        say(f" diary match age (signal vs nearest record at/before): median "
            f"{st.median(diag_ages):.0f}s, max {max(diag_ages):.0f}s")
    say("")
    say(" how to read it:")
    say("   SHADOW = what the robot's OWN exit logic (recorded walls, v8 brackets+gate,")
    say("   profit-lock trail) would have done with these signals. The 'same subset'")
    say("   BENCHMARK row is the fixed TP/SL ruler on exactly the signals the shadow")
    say("   took - the gap between those two rows is the honest size of 'the walls")
    say("   change the answer'. The 'ALL signals' BENCHMARK row uses the ledger's own")
    say("   M5 walk and must match the decision ledger's ALL SIGNALS exactly.")
    say("   GATE_SKIP rows are the value gate saying 'not worth it' - that is the robot")
    say("   refusing its own trade, counted here exactly as it would refuse live.")

    out_csv = os.path.join("data", f"shadow_replay_{args.date}.csv")
    try:
        os.makedirs("data", exist_ok=True)
        if rows_out:
            with open(out_csv, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=list(rows_out[0].keys()))
                w.writeheader(); w.writerows(rows_out)
            say(f" [saved] {out_csv}")
    except OSError as e:
        say(f" [could not save {out_csv}: {e}]")
    out_txt = os.path.join("data", f"shadow_replay_{args.date}.txt")
    try:
        with open(out_txt, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError as e:
        say(f" [could not save {out_txt}: {e}]")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
