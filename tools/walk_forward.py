"""
tools/walk_forward.py — B6 Walk-Forward + Monte Carlo (v5.5)

WHAT
----
Replays last N days of archived ticks.csv.gz + mbo.csv.gz through REAL engine
(backtest.py) and aggregates:

- Equity curve over 30 days
- Win rate, PF, expectancy per regime/side/session
- Max drawdown, Sharpe, Sortino
- L3 metrics: whale win rate, iceberg PF (from B2)
- Monte Carlo 1000 shuffles of trades -> DD distribution

WHY GC SWITCH MATTERS
---------------------
GC has 10x deeper book than MGC, so L3 metrics (whale/iceberg) are cleaner.
Walk-forward on GC archive proves strategy works on institutional feed,
not just micro noise.

USAGE
    python tools/walk_forward.py
    python tools/walk_forward.py --days 7 --monte-carlo 500
    python tools/walk_forward.py --equity 1000 --min-score 15

OUTPUT
    data/walk_forward_<timestamp>/
        trades_all.csv
        equity_curve.csv
        summary.txt
        walk_forward_report.json
        monte_carlo.json

INTEGRATION
-----------
Uses Backtester class from backtest.py — same REAL engine.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import random
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from tools.backtest import Backtester  # noqa: E402

def find_archives(days: int) -> List[Path]:
    """Find ticks archives from last N days, sorted oldest->newest"""
    patterns = [
        str(ROOT / "data" / "archive" / "ticks_*.csv.gz"),
        str(ROOT / "data" / "archive" / "ticks_*.csv"),
        str(ROOT / "data" / "ticks.csv"),
        str(ROOT / "ticks.csv"),
    ]
    files = []
    for pat in patterns:
        files.extend([Path(p) for p in glob.glob(pat)])
    # Deduplicate and sort by mtime
    files = sorted(set(files), key=lambda p: p.stat().st_mtime if p.exists() else 0)
    # Filter last N days
    cutoff = time.time() - days*86400
    recent = [f for f in files if f.exists() and f.stat().st_mtime >= cutoff]
    # If not enough, take last N files regardless of age
    if len(recent) < 2 and len(files) >= 2:
        recent = files[-min(days, len(files)):]
    return recent

def sharpe_ratio(returns: List[float]) -> float:
    if len(returns) < 2:
        return 0.0
    import numpy as np
    r = np.array(returns, dtype=float)
    if r.std() == 0:
        return 0.0
    return float(r.mean() / r.std() * (252*24*60)**0.5)  # approx annualized for M1

def max_drawdown(equity: List[float]) -> float:
    peak = equity[0] if equity else 0
    mdd = 0.0
    for e in equity:
        if e > peak:
            peak = e
        dd = peak - e
        if dd > mdd:
            mdd = dd
    return mdd

def monte_carlo(trades: List[Dict], runs: int = 1000) -> Dict:
    """Shuffle trade order 1000x to estimate DD distribution"""
    if not trades:
        return {"error": "No trades for Monte Carlo"}
    pnls = [float(t.get("pnl_usd",0)) for t in trades]
    results = []
    for _ in range(runs):
        shuffled = pnls[:]
        random.shuffle(shuffled)
        equity = 0
        peak = 0
        mdd = 0
        for pnl in shuffled:
            equity += pnl
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > mdd:
                mdd = dd
        results.append(mdd)
    results.sort()
    return {
        "runs": runs,
        "mean_dd": round(sum(results)/len(results),2) if results else 0,
        "median_dd": round(results[len(results)//2],2) if results else 0,
        "p95_dd": round(results[int(len(results)*0.95)],2) if results else 0,
        "max_dd": round(max(results),2) if results else 0,
        "min_dd": round(min(results),2) if results else 0,
    }

class WalkForward:
    def __init__(self, args):
        self.args = args
        self.archives = find_archives(args.days)
        self.all_trades: List[Dict] = []
        self.equity_curve: List[float] = [args.equity]
        self.daily_reports: List[Dict] = []

    def run(self):
        print(f"Found {len(self.archives)} archives for last {self.args.days} days:")
        for f in self.archives:
            print(f"  {f} ({f.stat().st_size/1024/1024:.1f} MB)")

        equity = float(self.args.equity)
        for arch in self.archives:
            print(f"\n=== Replaying {arch.name} ===")
            # Build args for Backtester
            bt_args = argparse.Namespace(
                file=str(arch),
                symbol=self.args.symbol,
                trade_symbol=self.args.trade_symbol,
                cycle_seconds=self.args.cycle_seconds,
                window_seconds=self.args.window_seconds,
                equity=equity,
                min_score=self.args.min_score,
                cooldown_seconds=self.args.cooldown_seconds,
                slippage=self.args.slippage,
                workdir=str(ROOT / "data" / f"wf_tmp_{arch.stem}"),
            )
            try:
                bt = Backtester(bt_args)
                report = bt.run()
                # Collect trades
                for t in bt.trades:
                    t["archive"] = arch.name
                    self.all_trades.append(t)
                # Update equity
                equity += float(report.get("net_usd",0))
                self.equity_curve.append(equity)
                self.daily_reports.append({
                    "archive": arch.name,
                    "trades": report.get("trades",0),
                    "win_rate": report.get("win_rate",0),
                    "net_usd": report.get("net_usd",0),
                    "profit_factor": report.get("profit_factor",0),
                    "max_dd": report.get("max_drawdown_usd",0),
                })
                print(f"  -> {report.get('trades',0)} trades, net ${report.get('net_usd',0)}, WR {report.get('win_rate',0)}%")
            except Exception as e:
                print(f"  !! Failed {arch.name}: {e}")
                import traceback
                traceback.print_exc()
                continue

        # Final aggregate
        total = len(self.all_trades)
        wins = [t for t in self.all_trades if float(t.get("pnl_usd",0))>0]
        gross_w = sum(float(t.get("pnl_usd",0)) for t in wins)
        gross_l = -sum(float(t.get("pnl_usd",0)) for t in self.all_trades if float(t.get("pnl_usd",0))<=0)
        win_rate = len(wins)/total*100 if total else 0
        pf = gross_w/gross_l if gross_l>0 else 0
        exp = (gross_w-gross_l)/total if total else 0
        mdd = max_drawdown(self.equity_curve)
        # Sharpe from daily returns
        daily_pnls = [r["net_usd"] for r in self.daily_reports]
        sharpe = sharpe_ratio(daily_pnls)

        # L3 metrics
        l3_whale = [t for t in self.all_trades if "whale" in str(t.get("reason","")).lower()]
        l3_ice = [t for t in self.all_trades if "iceberg" in str(t.get("reason","")).lower()]
        l3_report = {}
        if l3_whale:
            l3_report["whale_trades"] = len(l3_whale)
            l3_report["whale_win_rate"] = round(len([t for t in l3_whale if float(t.get("pnl_usd",0))>0])/len(l3_whale)*100,1)
            l3_report["whale_pnl"] = round(sum(float(t.get("pnl_usd",0)) for t in l3_whale),2)
        if l3_ice:
            l3_report["iceberg_trades"] = len(l3_ice)
            l3_report["iceberg_win_rate"] = round(len([t for t in l3_ice if float(t.get("pnl_usd",0))>0])/len(l3_ice)*100,1)
            gw = sum(float(t.get("pnl_usd",0)) for t in l3_ice if float(t.get("pnl_usd",0))>0)
            gl = -sum(float(t.get("pnl_usd",0)) for t in l3_ice if float(t.get("pnl_usd",0))<=0)
            l3_report["iceberg_pf"] = round(gw/gl,2) if gl>0 else 0

        mc = monte_carlo(self.all_trades, runs=self.args.monte_carlo)

        final = {
            "generated_utc": datetime.now(timezone.utc).isoformat(),
            "days": self.args.days,
            "archives": [str(f) for f in self.archives],
            "total_trades": total,
            "win_rate": round(win_rate,1),
            "profit_factor": round(pf,2),
            "expectancy_usd": round(exp,2),
            "net_pnl": round(self.equity_curve[-1]-self.args.equity,2) if self.equity_curve else 0,
            "start_equity": self.args.equity,
            "end_equity": round(self.equity_curve[-1],2) if self.equity_curve else 0,
            "max_drawdown": round(mdd,2),
            "sharpe_approx": round(sharpe,2),
            "daily_reports": self.daily_reports,
            "l3_metrics": l3_report,
            "monte_carlo": mc,
            "gc_note": "GC institutional feed — deeper book than MGC, L3 metrics cleaner",
        }

        # Save
        outdir = ROOT / "data" / f"walk_forward_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "walk_forward_report.json").write_text(json.dumps(final, indent=2), encoding="utf-8")
        (outdir / "monte_carlo.json").write_text(json.dumps(mc, indent=2), encoding="utf-8")

        # trades_all.csv
        with open(outdir / "trades_all.csv", "w", newline="", encoding="utf-8") as fh:
            if self.all_trades:
                w = csv.DictWriter(fh, fieldnames=list(self.all_trades[0].keys()))
                w.writeheader()
                w.writerows(self.all_trades)

        # equity_curve.csv
        with open(outdir / "equity_curve.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["step","equity"])
            for i, eq in enumerate(self.equity_curve):
                w.writerow([i, eq])

        # summary.txt
        lines = [
            "="*70,
            f"WALK-FORWARD REPORT — {self.args.days} days, {len(self.archives)} archives, GC feed",
            "="*70,
            f"Period: last {self.args.days} days",
            f"Archives: {len(self.archives)} files",
            f"Total trades: {total}  Wins: {len(wins)}  WR: {win_rate:.1f}%  PF: {pf:.2f}",
            f"Net PnL: ${final['net_pnl']}  Expectancy: ${exp:.2f}  Max DD: ${mdd:.2f}  Sharpe~ {sharpe:.2f}",
            f"Start ${self.args.equity} -> End ${final['end_equity']}",
            f"L3: {l3_report}",
            f"Monte Carlo ({mc.get('runs',0)} runs): mean DD ${mc.get('mean_dd',0)} median ${mc.get('median_dd',0)} p95 ${mc.get('p95_dd',0)} max ${mc.get('max_dd',0)}",
            "="*70,
            "Daily breakdown:",
        ]
        for dr in self.daily_reports:
            lines.append(f"  {dr['archive']}: {dr['trades']} trades WR {dr['win_rate']}% net ${dr['net_usd']} PF {dr['profit_factor']}")
        lines.append("="*70)
        summary = "\n".join(lines)
        (outdir / "summary.txt").write_text(summary, encoding="utf-8")
        print("\n" + summary)
        print(f"\nSaved to {outdir}")
        return final

def main():
    ap = argparse.ArgumentParser(description="B6 Walk-Forward over archive")
    ap.add_argument("--days", type=int, default=30, help="Last N days")
    ap.add_argument("--equity", type=float, default=1000.0)
    ap.add_argument("--min-score", type=float, default=15.0)
    ap.add_argument("--symbol", default="", help="Instrument to trade")
    ap.add_argument("--trade-symbol", default="", help="Trade symbol")
    ap.add_argument("--cycle-seconds", type=float, default=60.0)
    ap.add_argument("--window-seconds", type=float, default=28800.0)
    ap.add_argument("--cooldown-seconds", type=float, default=300.0)
    ap.add_argument("--slippage", type=float, default=0.0)
    ap.add_argument("--monte-carlo", type=int, default=1000, help="MC runs")
    args = ap.parse_args()
    wf = WalkForward(args)
    wf.run()

if __name__ == "__main__":
    main()
