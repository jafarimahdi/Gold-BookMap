#!/usr/bin/env python3
"""Repair `trade_memory.json` records whose points disagree with the broker.

Stage X5 background (`2026-11-10`): `_record_gone()` asked MT5 for one position
with a date range, MT5 ignored the position filter, and the reply was every
deal in 48 h. `trade_memory` then stored `-44.49 pts` for a `+1.84 pt` trade and
the Stage-4R `-12 pt` breaker latched for the rest of the session.

This tool finds every such record and rewrites it from broker truth:
  * when MT5 knows the position  -> exact profit / close price / comment
  * when MT5 does not             -> points recomputed from the recorded USD
                                    and the record is flagged `estimated: true`

It never trades and never touches risk state. Dry-run unless `--apply`.
A timestamped backup is written next to the file before any change.

Usage (owner rig):
    PY=.venv/Scripts/python.exe; [ -x "$PY" ] || PY=python
    "$PY" tools/repair_trade_memory_x5.py            # look, change nothing
    "$PY" tools/repair_trade_memory_x5.py --apply    # repair + backup
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402


def _points_from_usd(usd: float, volume: float, contract: float) -> float:
    if volume <= 0.0 or contract <= 0.0:
        return 0.0
    return usd / (volume * contract)


def _broker_truth(mt5, position_id: int):
    """`(profit_sum, close_price, comment)` for one position, or None."""
    if mt5 is None or position_id <= 0:
        return None
    try:
        deals = mt5.history_deals_get(position=int(position_id)) or []
    except Exception:
        return None
    if not deals:
        return None
    profit = 0.0
    out_vol = 0.0
    out_px = 0.0
    comment = ""
    has_exit = False
    for d in deals:
        profit += sum(float(getattr(d, k, 0.0) or 0.0) for k in ("profit", "swap", "commission"))
        entry = getattr(d, "entry", None)
        px = float(getattr(d, "price", 0.0) or 0.0)
        vol = float(getattr(d, "volume", 0.0) or 0.0)
        if entry in (1, 2, 3) or float(getattr(d, "profit", 0.0) or 0.0) != 0.0:
            has_exit = True
        if entry == 1 and px > 0.0 and vol > 0.0:
            out_vol += vol
            out_px += px * vol
        c = str(getattr(d, "comment", "") or "")
        if c:
            comment = c
    if not has_exit:
        return None
    return profit, (out_px / out_vol if out_vol > 0.0 else 0.0), comment


def main() -> int:
    ap = argparse.ArgumentParser(description="Repair trade_memory records against the broker (Stage X5).")
    ap.add_argument("--apply", action="store_true", help="write the repaired file (default: dry run)")
    ap.add_argument("--data-dir", default=None, help="override the data directory")
    ap.add_argument("--tolerance", type=float, default=1.0, help="absolute point tolerance (default 1.0)")
    args = ap.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else Path(getattr(config, "DATA_DIR", "data"))
    mem_file = data_dir / "trade_memory.json"
    contract = max(1.0, float(getattr(config, "CONTRACT_SIZE", 100.0) or 100.0))

    if not mem_file.exists():
        print(f"no trade_memory.json at {mem_file}")
        return 0
    data = json.loads(mem_file.read_text(encoding="utf-8-sig"))
    records = data.get("closed", []) if isinstance(data, dict) else []
    print(f"file      : {mem_file}")
    print(f"records   : {len(records)}")
    print(f"contract  : {contract:.0f} | tolerance {args.tolerance:.2f} pts")

    mt5 = None
    try:
        import MetaTrader5 as _mt5  # type: ignore
        if bool(config.mt5_initialize(_mt5)):
            mt5 = _mt5
            print("MT5       : connected (broker truth available)")
        else:
            print("MT5       : not available -> USD-derived repair only")
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"MT5       : not available ({exc}) -> USD-derived repair only")

    fixed = 0
    unresolved = 0
    try:
        for rec in records:
            if not isinstance(rec, dict):
                continue
            raw_tkt = str(rec.get("ticket") or "").split(":")[0].strip()
            try:
                pos_id = int(raw_tkt)
            except ValueError:
                continue
            vol = float(rec.get("volume", 0.0) or 0.0)
            gain = float(rec.get("gain_pts", 0.0) or 0.0)
            usd = float(rec.get("pnl_usd_est", 0.0) or 0.0)
            implied = _points_from_usd(usd, vol, contract)
            if abs(implied - gain) <= args.tolerance and abs(usd) <= args.tolerance:
                continue
            if abs(implied - gain) <= args.tolerance:
                continue
            truth = _broker_truth(mt5, pos_id)
            stamp = dt.datetime.fromtimestamp(float(rec.get("closed_ts", 0) or 0), dt.timezone.utc)
            if truth is not None:
                profit, close_px, comment = truth
                new_gain = _points_from_usd(profit, vol, contract)
                print(f"  REPAIR pos={pos_id:<10} {stamp:%m-%d %H:%M}  "
                      f"gain {gain:+8.2f} -> {new_gain:+8.2f} pts | usd {usd:+8.2f} -> {profit:+8.2f}")
                rec["gain_pts"] = round(new_gain, 4)
                rec["pnl_usd_est"] = round(profit, 4)
                if close_px > 0.0:
                    rec["close_price"] = round(close_px, 4)
                rec["estimated"] = False
                rec["exit_reason"] = f"GONE (broker: {comment[:24]})" if comment else "GONE (broker verified)"
                rec["x5_repaired"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
                fixed += 1
            else:
                print(f"  FLAG   pos={pos_id:<10} {stamp:%m-%d %H:%M}  "
                      f"gain {gain:+8.2f} vs usd-implied {implied:+8.2f} -> no broker data, left alone")
                unresolved += 1
    finally:
        if mt5 is not None:
            try:
                mt5.shutdown()
            except Exception:
                pass

    print(f"\nrepairable: {fixed} | unresolved (no broker data): {unresolved}")
    if not args.apply:
        print("dry run -- nothing written. Re-run with --apply to repair.")
        return 0
    if fixed == 0:
        print("nothing to write.")
        return 0
    backup = mem_file.with_name(mem_file.name + f".bak-x5-{dt.datetime.now():%Y%m%d-%H%M%S}")
    shutil.copy2(mem_file, backup)
    mem_file.write_text(json.dumps(data, indent=1), encoding="utf-8")
    print(f"backup    : {backup}")
    print(f"rewritten : {mem_file} ({fixed} record(s))")
    print("restart the loop so the breaker recomputes from clean memory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
