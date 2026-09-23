#!/usr/bin/env python3
"""
Audit skipped BUY/SELL signals - would they have been profitable?
Run: python audit_skipped_trades.py
Checks decisions_log.csv vs ticks.csv price action 180 min ahead
"""
import csv, re
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
TICKS = BASE / "ticks.csv"

# Config from your .env - SL 2x ATR, TP 3.5x ATR, ATR ~3.0 for GC M5
ATR_DEFAULT = 3.18  # from your logs
SL_MULT = 2.0
TP_MULT = 3.5
MAX_HOLD_MIN = 180  # M5 max hold 180 min

def parse_ts(s):
    try:
        dt = datetime.fromisoformat(s.replace("Z","+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except:
        return None

def load_ticks():
    """Load ticks.csv: time,event,price,size,level,operation,instrument"""
    ticks = []  # list of (dt, price)
    if not TICKS.exists():
        print(f"[WARN] {TICKS} not found, using decisions price only")
        return ticks
    print(f"[INFO] Loading {TICKS} ({TICKS.stat().st_size/1024/1024:.1f} MB)...")
    with open(TICKS, encoding="utf-8", errors="ignore") as f:
        reader = csv.reader(f)
        for row in reader:
            if not row or row[0]=="time":
                continue
            try:
                if len(row)>=7:
                    ts_raw, event, price_raw = row[0], row[1], row[2]
                    if event!="Last":
                        continue
                    dt = parse_ts(ts_raw)
                    price = float(price_raw)
                    if dt and 1000<price<10000:
                        ticks.append((dt, price))
                # keep last 1M to avoid memory blow
                if len(ticks)>1000000:
                    ticks = ticks[-800000:]
            except:
                continue
    ticks.sort(key=lambda x: x[0])
    print(f"[INFO] Loaded {len(ticks)} Last ticks from {ticks[0][0] if ticks else 'N/A'} to {ticks[-1][0] if ticks else 'N/A'}")
    return ticks

def find_price_after(ticks, entry_dt, minutes=180):
    """Return list of prices in next N minutes after entry_dt"""
    if not ticks:
        return []
    # binary search approx
    start_idx = 0
    for i, (dt, _) in enumerate(ticks):
        if dt >= entry_dt:
            start_idx = i
            break
    end_dt = entry_dt + timedelta(minutes=minutes)
    out = []
    for dt, price in ticks[start_idx:]:
        if dt > end_dt:
            break
        out.append(price)
    return out

def load_decisions():
    path = DATA / "decisions_log.csv"
    if not path.exists():
        print(f"[WARN] {path} not found")
        return []
    rows = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for r in reader:
            # Only today and BUY/SELL that were SKIPPED
            dir_ = r.get("signal_direction","").upper()
            if dir_ not in ("BUY","SELL"):
                continue
            # Only skipped due to confidence
            reason = r.get("reason","")
            exec_status = r.get("exec_status","")
            # Include all BUY/SELL even if reason contains confidence
            rows.append(r)
    print(f"[INFO] Found {len(rows)} BUY/SELL signals total (including skipped)")
    # Filter today
    today_rows = [r for r in rows if "2026-09-22" in r.get("timestamp","")]
    if today_rows:
        print(f"[INFO] {len(today_rows)} from today 2026-09-22")
        return today_rows
    return rows[-100:]  # last 100 if no today filter

def simulate_trade(entry_price, direction, future_prices, atr=ATR_DEFAULT):
    """Simulate if TP hit before SL within future_prices"""
    if direction=="BUY":
        sl = entry_price - SL_MULT*atr
        tp = entry_price + TP_MULT*atr
        for p in future_prices:
            if p >= tp:
                return "WIN", tp, (tp-entry_price)
            if p <= sl:
                return "LOSS", sl, (sl-entry_price)
        # No hit, close at last price
        last = future_prices[-1] if future_prices else entry_price
        pnl = last - entry_price
        return "TIMEOUT", last, pnl
    else: # SELL
        sl = entry_price + SL_MULT*atr
        tp = entry_price - TP_MULT*atr
        for p in future_prices:
            if p <= tp:
                return "WIN", tp, (entry_price-tp) * -1  # negative for SELL? keep sign
            if p >= sl:
                return "LOSS", sl, (entry_price-sl) * -1
        last = future_prices[-1] if future_prices else entry_price
        pnl = entry_price - last
        return "TIMEOUT", last, -pnl if direction=="SELL" else pnl

def main():
    print("=== AUDIT SKIPPED TRADES - WOULD THEY HAVE BEEN PROFITABLE? ===")
    ticks = load_ticks()
    decisions = load_decisions()
    
    if not decisions:
        print("No BUY/SELL decisions found")
        return
    
    results = []
    for r in decisions:
        ts_raw = r.get("timestamp","")
        dt = parse_ts(ts_raw)
        direction = r.get("signal_direction","").upper()
        strength = r.get("signal_strength","")
        conf = r.get("signal_confidence","")
        price_raw = r.get("price","0")
        reason = r.get("reason","")[:120]
        
        # Get entry price
        try:
            entry_price = float(price_raw)
        except:
            entry_price = 0
        
        if entry_price < 1000:
            # Try find price from ticks at that time
            if dt and ticks:
                future = find_price_after(ticks, dt, minutes=1)
                if future:
                    entry_price = future[0]
        
        if entry_price < 1000:
            continue
        
        # Get future prices 180 min ahead
        future_prices = find_price_after(ticks, dt, minutes=MAX_HOLD_MIN) if dt else []
        
        if not future_prices:
            # No future data, skip
            result = ("NO_DATA", 0, 0, [])
        else:
            outcome, exit_price, pnl = simulate_trade(entry_price, direction, future_prices, atr=ATR_DEFAULT)
            result = (outcome, exit_price, pnl, future_prices)
        
        results.append({
            "timestamp": ts_raw,
            "direction": direction,
            "entry": entry_price,
            "strength": strength,
            "confidence": conf,
            "reason": reason,
            "outcome": result[0],
            "exit": result[1],
            "pnl_points": result[2],
        })
    
    # Print table
    print(f"\n{'TIME':<20} {'DIR':<4} {'ENTRY':<8} {'STR':<5} {'CONF':<5} {'OUTCOME':<8} {'EXIT':<8} {'PNL pts':<8} {'REASON'}")
    print("-"*130)
    wins = 0
    losses = 0
    timeouts = 0
    total_pnl = 0
    for r in results:
        print(f"{r['timestamp'][:19]:<20} {r['direction']:<4} {r['entry']:<8.1f} {str(r['strength'])[:5]:<5} {str(r['confidence'])[:5]:<5} {r['outcome']:<8} {r['exit']:<8.1f} {r['pnl_points']:<8.1f} {r['reason'][:50]}")
        if r['outcome']=="WIN":
            wins+=1
            total_pnl+= abs(r['pnl_points'])
        elif r['outcome']=="LOSS":
            losses+=1
            total_pnl-= abs(r['pnl_points'])
        elif r['outcome']=="TIMEOUT":
            timeouts+=1
            total_pnl+= r['pnl_points']
    
    print("-"*130)
    print(f"Total skipped BUY/SELL checked: {len(results)}")
    print(f"WIN (TP hit first): {wins} | LOSS (SL hit first): {losses} | TIMEOUT (no SL/TP in 180m): {timeouts} | NO_DATA: {len(results)-wins-losses-timeouts}")
    print(f"Simulated total PnL points: {total_pnl:.1f} (using SL {SL_MULT}xATR={SL_MULT*ATR_DEFAULT:.1f} TP {TP_MULT}xATR={TP_MULT*ATR_DEFAULT:.1f})")
    if wins+losses>0:
        wr = wins/(wins+losses)*100
        print(f"Win Rate on closed trades: {wr:.1f}%")
        print(f"Profit Factor approx: {(wins*TP_MULT)/(losses*SL_MULT) if losses else 999:.2f}")
    
    # List profitable ones
    print("\n=== PROFITABLE SKIPPED TRADES (would have won) ===")
    for r in results:
        if r['outcome']=="WIN":
            print(f"{r['timestamp'][:19]} {r['direction']} entry {r['entry']:.1f} -> exit {r['exit']:.1f} +{abs(r['pnl_points']):.1f} pts | strength {r['strength']} conf {r['confidence']} | {r['reason']}")

    print("\n=== LOSING SKIPPED TRADES (correctly skipped) ===")
    for r in results:
        if r['outcome']=="LOSS":
            print(f"{r['timestamp'][:19]} {r['direction']} entry {r['entry']:.1f} -> SL {r['exit']:.1f} {r['pnl_points']:.1f} pts | {r['reason']}")

    # Save to csv
    out_path = BASE / f"audit_skipped_{datetime.now().strftime('%Y%m%d')}.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["timestamp","direction","entry","strength","confidence","outcome","exit","pnl_points","reason"])
        w.writeheader()
        for r in results:
            w.writerow(r)
    print(f"\n[INFO] Full audit saved to {out_path}")

if __name__=="__main__":
    main()
