"""
tools/train_weights.py — B2 ML Self-Learning from trade_memory.json (v5.5)

WHY
---
After 100+ live trades, which signal votes actually make money for THIS robot?
The 200-trade rolling memory now includes regime, vol_rank, ATR, signal_strength.

This tool:
 1. Loads data/trade_memory.json closed trades
 2. Calculates win rate, PF, expectancy per:
    - regime (TREND/RANGE/NEUTRAL)
    - side (BUY/SELL)
    - signal_strength bucket
    - volatility bucket
    - exit_reason
 3. Suggests new SIGNAL_W_* weights:
    new_weight = old * (1 + 0.15*(win_rate-0.5))  — winners get boosted
 4. Optionally writes data/weight_suggestions.json and can auto-patch .env if --apply

USAGE
    python tools/train_weights.py
    python tools/train_weights.py --apply   # patches .env with suggested weights
    python tools/train_weights.py --min-trades 30

OUTPUT
    data/weight_suggestions.json
    console report

INTEGRATION WITH GC
-------------------
Works with GC or MGC — regime/vol detection is symbol-agnostic. GC's deeper book
gives better L3 stats, so win rate should improve after GC switch.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import trade_history as th  # noqa: E402

def load_closed() -> List[Dict]:
    try:
        data = th._load()
        return data.get("closed", [])
    except Exception:
        return []

def bucket_strength(s: float) -> str:
    s = abs(float(s or 0))
    if s < 15:
        return "weak<15"
    if s < 30:
        return "mid15-30"
    if s < 50:
        return "strong30-50"
    return "very_strong>50"

def bucket_vol(v: float) -> str:
    v = float(v or 0.5)
    if v < 0.3:
        return "low_vol<0.3"
    if v < 0.6:
        return "mid_vol0.3-0.6"
    return "high_vol>0.6"

def analyze(closed: List[Dict]) -> Dict:
    if not closed:
        return {"error": "No closed trades yet — need at least 20-30 live trades for ML"}

    total = len(closed)
    wins = [c for c in closed if float(c.get("pnl_usd_est", 0)) > 0]
    win_rate = len(wins)/total if total else 0
    gross_w = sum(float(c.get("pnl_usd_est",0)) for c in wins)
    gross_l = -sum(float(c.get("pnl_usd_est",0)) for c in closed if float(c.get("pnl_usd_est",0)) <=0)
    pf = gross_w/gross_l if gross_l>0 else 0
    expectancy = (gross_w - gross_l)/total if total else 0

    # per regime
    per_regime = {}
    for regime in set(str(c.get("regime","") or "UNKNOWN").upper() for c in closed):
        rc = [c for c in closed if str(c.get("regime","")).upper()==regime]
        if not rc:
            continue
        rw = [c for c in rc if float(c.get("pnl_usd_est",0))>0]
        per_regime[regime] = {
            "trades": len(rc),
            "win_rate": round(len(rw)/len(rc)*100,1) if rc else 0,
            "pnl": round(sum(float(c.get("pnl_usd_est",0)) for c in rc),2),
            "avg_pnl": round(sum(float(c.get("pnl_usd_est",0)) for c in rc)/len(rc),2) if rc else 0,
        }

    # per side
    per_side = {}
    for side in ("BUY","SELL"):
        sc = [c for c in closed if str(c.get("side","")).upper()==side]
        if not sc:
            continue
        sw = [c for c in sc if float(c.get("pnl_usd_est",0))>0]
        per_side[side] = {
            "trades": len(sc),
            "win_rate": round(len(sw)/len(sc)*100,1) if sc else 0,
            "pnl": round(sum(float(c.get("pnl_usd_est",0)) for c in sc),2),
        }

    # per strength bucket
    per_strength = {}
    for b in ["weak<15","mid15-30","strong30-50","very_strong>50"]:
        bc = [c for c in closed if bucket_strength(c.get("signal_strength",0))==b]
        if not bc:
            continue
        bw = [c for c in bc if float(c.get("pnl_usd_est",0))>0]
        per_strength[b] = {
            "trades": len(bc),
            "win_rate": round(len(bw)/len(bc)*100,1) if bc else 0,
            "pnl": round(sum(float(c.get("pnl_usd_est",0)) for c in bc),2),
        }

    # per vol bucket
    per_vol = {}
    for b in ["low_vol<0.3","mid_vol0.3-0.6","high_vol>0.6"]:
        bc = [c for c in closed if bucket_vol(c.get("volatility_rank",0.5))==b]
        if not bc:
            continue
        bw = [c for c in bc if float(c.get("pnl_usd_est",0))>0]
        per_vol[b] = {
            "trades": len(bc),
            "win_rate": round(len(bw)/len(bc)*100,1) if bc else 0,
            "pnl": round(sum(float(c.get("pnl_usd_est",0)) for c in bc),2),
        }

    # per exit reason
    per_exit = {}
    for reason in set(str(c.get("exit_reason","")).upper() for c in closed):
        rc = [c for c in closed if str(c.get("exit_reason","")).upper()==reason]
        if len(rc) < 2:
            continue
        rw = [c for c in rc if float(c.get("pnl_usd_est",0))>0]
        per_exit[reason] = {
            "trades": len(rc),
            "win_rate": round(len(rw)/len(rc)*100,1) if rc else 0,
            "pnl": round(sum(float(c.get("pnl_usd_est",0)) for c in rc),2),
        }

    # Suggest weight adjustments — simple RL
    # If TREND win rate high, boost trend weights, reduce range weights
    # If RANGE win rate high, opposite
    suggestions = {}
    current_weights = {
        "SIGNAL_W_TREND": float(getattr(config, "SIGNAL_W_TREND", 0.5)),
        "SIGNAL_W_VWAP": float(getattr(config, "SIGNAL_W_VWAP", 0.6)),
        "SIGNAL_W_L3_WHALE": float(getattr(config, "SIGNAL_W_L3_WHALE", 1.5)),
        "SIGNAL_W_ICEBERG": float(getattr(config, "SIGNAL_W_ICEBERG", 1.0)),
        "SIGNAL_W_SPOOF": float(getattr(config, "SIGNAL_W_SPOOF", 0.8)),
        "SIGNAL_W_OFI": float(getattr(config, "SIGNAL_W_OFI", 0.9)),
        "SIGNAL_W_L3_OFI": float(getattr(config, "SIGNAL_W_L3_OFI", 0.8)),
    }

    # Logic: if TREND regime wins >60%, boost trend weight 15%
    trend_stats = per_regime.get("TREND")
    range_stats = per_regime.get("RANGE")
    if trend_stats and trend_stats["trades"]>=5:
        if trend_stats["win_rate"] >= 60:
            suggestions["SIGNAL_W_TREND"] = round(current_weights["SIGNAL_W_TREND"]*1.15,2)
        elif trend_stats["win_rate"] <= 40:
            suggestions["SIGNAL_W_TREND"] = round(current_weights["SIGNAL_W_TREND"]*0.85,2)
    if range_stats and range_stats["trades"]>=5:
        if range_stats["win_rate"] >= 60:
            suggestions["SIGNAL_W_VWAP"] = round(current_weights["SIGNAL_W_VWAP"]*1.15,2)
        elif range_stats["win_rate"] <= 40:
            suggestions["SIGNAL_W_VWAP"] = round(current_weights["SIGNAL_W_VWAP"]*0.85,2)

    # If high vol bucket losing, reduce mean-reversion (VWAP) weight
    high_vol = per_vol.get("high_vol>0.6")
    if high_vol and high_vol["trades"]>=5 and high_vol["win_rate"]<=40:
        suggestions["SIGNAL_W_VWAP"] = round(suggestions.get("SIGNAL_W_VWAP", current_weights["SIGNAL_W_VWAP"])*0.8,2)

    # If L3 whale trades exist and losing, reduce whale weight
    # We infer from exit reason or notes — approximate via overall win rate
    if win_rate < 0.45 and total>=20:
        # losing overall — reduce weakest?
        suggestions["SIGNAL_W_TREND"] = round(suggestions.get("SIGNAL_W_TREND", current_weights["SIGNAL_W_TREND"])*0.9,2)

    return {
        "total_trades": total,
        "win_rate": round(win_rate*100,1),
        "profit_factor": round(pf,2),
        "expectancy_usd": round(expectancy,2),
        "gross_win": round(gross_w,2),
        "gross_loss": round(gross_l,2),
        "net_pnl": round(gross_w-gross_l,2),
        "per_regime": per_regime,
        "per_side": per_side,
        "per_strength": per_strength,
        "per_vol": per_vol,
        "per_exit": per_exit,
        "current_weights": current_weights,
        "suggested_weights": suggestions,
        "ml_ready": total>=20,
        "recommendation": "Apply suggested weights" if suggestions and total>=30 else "Need more trades (30+)",
    }

def apply_to_env(suggestions: Dict[str,float]) -> bool:
    """Patch .env with suggested weights"""
    env_path = ROOT / ".env"
    if not env_path.exists():
        print(f".env not found at {env_path}, cannot apply")
        return False
    content = env_path.read_text(encoding="utf-8")
    for k,v in suggestions.items():
        if k in content:
            # replace line
            import re
            content = re.sub(rf"^{k}=.*$", f"{k}={v}", content, flags=re.MULTILINE)
        else:
            content += f"\n{k}={v}\n"
    # backup
    backup = ROOT / f".env.backup.{int(__import__('time').time())}"
    env_path.rename(backup)
    env_path.write_text(content, encoding="utf-8")
    print(f"Applied {len(suggestions)} weights to .env, backup at {backup}")
    return True

def main():
    ap = argparse.ArgumentParser(description="B2 ML train weights from trade memory")
    ap.add_argument("--apply", action="store_true", help="Patch .env with suggestions")
    ap.add_argument("--min-trades", type=int, default=20, help="Min trades to suggest")
    args = ap.parse_args()

    closed = load_closed()
    print(f"Loaded {len(closed)} closed trades from {th.memory_path()}")
    report = analyze(closed)

    out_path = ROOT / "data" / "weight_suggestions.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport saved -> {out_path}\n")

    # Pretty print
    print("="*70)
    print(f"B2 ML REPORT: {report.get('total_trades',0)} trades, win {report.get('win_rate',0)}%, PF {report.get('profit_factor',0)}, exp ${report.get('expectancy_usd',0)}")
    print("="*70)
    print("\nPer Regime:")
    for r, s in report.get("per_regime",{}).items():
        print(f"  {r}: {s['trades']} trades WR {s['win_rate']}% PnL ${s['pnl']} avg ${s['avg_pnl']}")
    print("\nPer Side:")
    for side, s in report.get("per_side",{}).items():
        print(f"  {side}: {s['trades']} WR {s['win_rate']}% PnL ${s['pnl']}")
    print("\nPer Strength:")
    for b,s in report.get("per_strength",{}).items():
        print(f"  {b}: {s['trades']} WR {s['win_rate']}%")
    print("\nPer Vol:")
    for b,s in report.get("per_vol",{}).items():
        print(f"  {b}: {s['trades']} WR {s['win_rate']}%")
    print("\nSuggested Weights:")
    for k,v in report.get("suggested_weights",{}).items():
        old = report.get("current_weights",{}).get(k,0)
        print(f"  {k}: {old} -> {v} ({'+' if v>old else ''}{round((v-old)/old*100,1) if old else 0}%)")

    if args.apply and report.get("suggested_weights"):
        if len(closed) < args.min_trades:
            print(f"\nNot applying — only {len(closed)} trades < min {args.min_trades}")
        else:
            apply_to_env(report["suggested_weights"])
    print("\nDone. Run with --apply to patch .env after 30+ trades.")

if __name__ == "__main__":
    main()
