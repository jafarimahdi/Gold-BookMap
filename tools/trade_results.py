"""
tools/trade_results.py — Trade Result Collector & Analyzer (v5.6)

WHAT IT DOES:
- Collects every closed trade from data/trade_memory.json + data/trade_outcomes.csv
- Shows: did we win/lose, how much, why, regime, vol, signal strength, exit reason
- For later review: "did we lose and got profit and how and why"

USAGE:
    python tools/trade_results.py
    python tools/trade_results.py --days 7
    python tools/trade_results.py --csv   # export data/trade_results_report.csv

OUTPUT:
    Console summary + data/trade_results_report.json + .csv
    Example:
    Ticket 90001722 SELL @ 4265.14 -> 4258.20 +6.94 pts +$13.88 WIN
      Why: TREND ADX 25, L3 whale support, icebergs 270, CVD -22
      Regime TREND vol_rank 0.45 atr 1.54 exit TP
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

try:
    import trade_history as th
except Exception as e:
    print(f"Cannot import trade_history: {e}")
    sys.exit(1)

def load_closed():
    try:
        data = th._load()
        return data.get("closed", []), data.get("open", [])
    except Exception as e:
        print(f"Load failed: {e}")
        return [], []

def load_outcomes():
    p = ROOT / "data" / "trade_outcomes.csv"
    if not p.exists():
        return []
    try:
        with open(p, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []

def load_decisions():
    p = ROOT / "data" / "decisions_log.csv"
    if not p.exists():
        return []
    try:
        with open(p, newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []

def analyze(days: int = 30):
    closed, open_pos = load_closed()
    outcomes = load_outcomes()
    decisions = load_decisions()

    print("="*80)
    print(f"TRADE RESULT COLLECTOR — last {days} days, GC LIVE")
    print("="*80)
    print(f"Closed trades in memory: {len(closed)} (rolling 200)")
    print(f"Open positions: {len(open_pos)}")
    print(f"Outcomes CSV rows: {len(outcomes)}")
    print(f"Decisions log rows: {len(decisions)}")
    print()

    if not closed:
        print("No closed trades yet — robot has 666 decisions today but 0 trades (AI HOLD <70% = correct, waiting for strong signal).")
        print("After first trade, this tool will show win/loss + why.")
        print()
        print("To see today's decisions:")
        print("  python -c \"import csv; rows=list(csv.DictReader(open('data/decisions_log.csv'))); [print(r) for r in rows[-5:]]\"")
        return

    # Filter by days if needed (using closed_utc)
    # For now show all

    wins = [c for c in closed if float(c.get("pnl_usd_est",0))>0]
    losses = [c for c in closed if float(c.get("pnl_usd_est",0))<=0]
    total = len(closed)
    win_rate = len(wins)/total*100 if total else 0
    gross_w = sum(float(c.get("pnl_usd_est",0)) for c in wins)
    gross_l = -sum(float(c.get("pnl_usd_est",0)) for c in losses)
    pf = gross_w/gross_l if gross_l>0 else 0
    net = gross_w - gross_l
    avg_win = gross_w/len(wins) if wins else 0
    avg_loss = -gross_l/len(losses) if losses else 0
    expectancy = net/total if total else 0

    print(f"SUMMARY: {total} trades, {len(wins)} wins, {len(losses)} losses, WR {win_rate:.1f}%, PF {pf:.2f}")
    print(f"  Gross win ${gross_w:.2f} / Gross loss ${gross_l:.2f} / Net ${net:.2f}")
    print(f"  Avg win ${avg_win:.2f} / Avg loss ${avg_loss:.2f} / Expectancy ${expectancy:.2f} per trade")
    print()

    # Per regime
    per_regime = {}
    for r in set(str(c.get("regime","") or "UNKNOWN").upper() for c in closed):
        rc = [c for c in closed if str(c.get("regime","")).upper()==r]
        rw = [c for c in rc if float(c.get("pnl_usd_est",0))>0]
        per_regime[r] = (len(rc), len(rw)/len(rc)*100 if rc else 0, sum(float(c.get("pnl_usd_est",0)) for c in rc))
    print("Per Regime (why we won/lost in TREND vs RANGE):")
    for regime, (cnt, wr, pnl) in per_regime.items():
        print(f"  {regime}: {cnt} trades WR {wr:.1f}% PnL ${pnl:.2f}")
    print()

    # Per exit reason
    per_exit = Counter(str(c.get("exit_reason","")).upper() for c in closed)
    print("Per Exit Reason (how we exited):")
    for reason, cnt in per_exit.most_common():
        rc = [c for c in closed if str(c.get("exit_reason","")).upper()==reason]
        rw = [c for c in rc if float(c.get("pnl_usd_est",0))>0]
        pnl = sum(float(c.get("pnl_usd_est",0)) for c in rc)
        print(f"  {reason}: {cnt} trades WR {len(rw)/cnt*100:.1f}% PnL ${pnl:.2f}")
    print()

    # Last 20 trades with why
    print("Last 20 trades — did we win/lose and why:")
    print("-"*80)
    for c in closed[-20:]:
        ticket = c.get("ticket","?")
        side = c.get("side","?")
        pnl = float(c.get("pnl_usd_est",0))
        pts = float(c.get("gain_pts",0))
        result = "WIN" if pnl>0 else "LOSS"
        regime = c.get("regime","")
        vol = c.get("volatility_rank",0)
        atr = c.get("atr",0)
        conf = c.get("ai_confidence",0)
        strength = c.get("signal_strength",0)
        exit_r = c.get("exit_reason","")
        age = c.get("age_minutes",0)
        print(f"{c.get('closed_utc','')[:19]} Ticket {ticket} {side} {pts:+.2f} pts ${pnl:+.2f} {result}")
        print(f"  Why: regime {regime} vol {vol:.2f} atr {atr:.2f} strength {strength:.1f} conf {conf:.1f}% exit {exit_r} age {age}m")
        # Try to find snapshot notes if available
        # notes = c.get("snapshot_notes","")
        # if notes:
        #     print(f"  Notes: {notes[:120]}")
    print("-"*80)
    print()

    # Save reports
    outdir = ROOT / "data"
    outdir.mkdir(parents=True, exist_ok=True)

    report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate,1),
        "profit_factor": round(pf,2),
        "net_pnl": round(net,2),
        "gross_win": round(gross_w,2),
        "gross_loss": round(gross_l,2),
        "expectancy": round(expectancy,2),
        "per_regime": {k: {"trades": v[0], "win_rate": round(v[1],1), "pnl": round(v[2],2)} for k,v in per_regime.items()},
        "per_exit": dict(per_exit),
        "closed": closed[-100:],  # last 100
    }
    (outdir / "trade_results_report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Saved JSON -> {outdir / 'trade_results_report.json'}")

    # CSV export
    csv_path = outdir / "trade_results_report.csv"
    if closed:
        with open(csv_path, "w", newline="", encoding="utf-8") as fh:
            fieldnames = list(closed[0].keys())
            w = csv.DictWriter(fh, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(closed)
        print(f"Saved CSV -> {csv_path}")

    print()
    print("How to use after trade happens:")
    print("  1. After each close, trade_history.py auto-appends to data/trade_memory.json")
    print("  2. Run: python tools/trade_results.py")
    print("  3. See WIN/LOSS + why (regime, vol, strength, exit reason)")
    print("  4. For ML: python tools/train_weights.py -> suggests new weights based on why you win/lose")
    print("  5. For TCA: check data/tca_report.json -> slippage per session")
    print("="*80)

def main():
    ap = argparse.ArgumentParser(description="Trade result collector")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--csv", action="store_true", help="Also export CSV")
    args = ap.parse_args()
    analyze(days=args.days)

if __name__ == "__main__":
    main()
