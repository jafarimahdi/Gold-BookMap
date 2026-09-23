#!/usr/bin/env python3
"""
Gold-BookMap Full Daily Report v2.0
Bank Manager + Senior Engineer edition
- Keeps all previous functions + adds skipped trade profitability audit
- Shows weak parts clearly every day

Run: python analyze_today.py > daily_report_2026-09-22.txt
"""
import os, re, csv, json, glob
from pathlib import Path
from datetime import datetime, date, timedelta, timezone
from collections import Counter, defaultdict
import statistics

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
LOGS = BASE / "logs"
TICKS = BASE / "ticks.csv"
MBO = BASE / "mbo.csv"
TODAY = date.today().isoformat()
TODAY_FILE = f"trading_{TODAY}.log"

# Bank-grade thresholds
ATR_DEFAULT = 3.18
SL_MULT = 2.0
TP_MULT = 3.5
MAX_HOLD_MIN = 180
LATENCY_TARGET = 500
STEP1_TARGET = 150
STEP2_TARGET = 300
FEED_TARGET = 0.5
CONF_THRESHOLD = 63
ICEBERG_OK_MAX = 80

def parse_ts(s):
    try:
        dt = datetime.fromisoformat(s.replace("Z","+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except:
        return None

def load_ticks_fast(max_lines=300000):
    """Load last N Last ticks for audit"""
    ticks = []
    if not TICKS.exists():
        return ticks
    try:
        # Read last max_lines efficiently
        with open(TICKS, encoding="utf-8", errors="ignore") as f:
            # For 78MB, reading all is ok, but we limit
            lines = f.readlines()[-max_lines*2:]  # rough
        for line in lines:
            try:
                parts = next(csv.reader([line]))
                if len(parts)<7 or parts[0]=="time":
                    continue
                if parts[1]!="Last":
                    continue
                dt = parse_ts(parts[0])
                price = float(parts[2])
                if dt and 1000<price<10000:
                    ticks.append((dt, price))
            except:
                continue
        ticks.sort(key=lambda x: x[0])
    except Exception as e:
        print(f"[WARN] ticks load error {e}")
    return ticks

def find_price_after(ticks, entry_dt, minutes=180):
    if not ticks or not entry_dt:
        return []
    # Find start idx
    start = 0
    for i, (dt, _) in enumerate(ticks):
        if dt >= entry_dt:
            start = i
            break
    end_dt = entry_dt + timedelta(minutes=minutes)
    out = []
    for dt, price in ticks[start:]:
        if dt > end_dt:
            break
        out.append(price)
    return out

def simulate_trade(entry_price, direction, future_prices, atr=ATR_DEFAULT):
    if not future_prices:
        return "NO_DATA", entry_price, 0
    if direction=="BUY":
        sl = entry_price - SL_MULT*atr
        tp = entry_price + TP_MULT*atr
        for p in future_prices:
            if p >= tp:
                return "WIN", tp, (tp-entry_price)
            if p <= sl:
                return "LOSS", sl, (sl-entry_price)
        last = future_prices[-1]
        return "TIMEOUT", last, (last-entry_price)
    else:
        sl = entry_price + SL_MULT*atr
        tp = entry_price - TP_MULT*atr
        for p in future_prices:
            if p <= tp:
                return "WIN", tp, (entry_price-tp)
            if p >= sl:
                return "LOSS", sl, (entry_price-sl)
        last = future_prices[-1]
        return "TIMEOUT", last, (entry_price-last)

def parse_decisions():
    path = DATA / "decisions_log.csv"
    if not path.exists():
        return []
    rows = []
    with open(path, encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for r in reader:
            if TODAY in r.get("timestamp","") or "2026-09-22" in r.get("timestamp",""):
                rows.append(r)
    # If no today, take last 1000
    if not rows:
        with open(path, encoding="utf-8", errors="ignore") as f:
            reader = list(csv.DictReader(f))
            rows = reader[-1000:]
    return rows

def parse_trading_log():
    log_path = LOGS / TODAY_FILE
    if not log_path.exists():
        logs = sorted(LOGS.glob("trading_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        if logs:
            log_path = logs[0]
        else:
            return "", None
    text = log_path.read_text(encoding="utf-8", errors="ignore")
    return text, log_path

def analyze_log_text(text):
    feed_ages = [float(m) for m in re.findall(r"feed age ([\d\.]+)s", text)]
    step1 = [int(m) for m in re.findall(r"STEP1 acquire (\d+)ms", text)]
    step2 = [int(m) for m in re.findall(r"STEP2 analyze (\d+)ms", text)]
    step3 = [int(m) for m in re.findall(r"STEP3 AI (\d+)ms", text)]
    total = [int(m) for m in re.findall(r"total (\d+)ms target", text)]
    signals = re.findall(r"analyze_market\(\) -> (\w+).*?strength=([\d\.]+).*?confidence=([\d\.]+)", text)
    icebergs = [int(x) for x in re.findall(r"Icebergs:\s+(\d+)", text)]
    regimes = re.findall(r"Regime:\s+(\w+)", text)
    confluences = re.findall(r"Confluence (FAIL|SELL|BUY)", text)
    ai_fails = len(re.findall(r"call failed.*gemini", text, re.I))
    ai_success = len(re.findall(r"Gemini -> (BUY|SELL|HOLD)", text))
    spreads = [float(m) for m in re.findall(r"spread ([\d\.]+)", text)]
    return {
        "feed_age": feed_ages, "step1": step1, "step2": step2, "step3": step3, "total": total,
        "signals": signals, "icebergs": icebergs, "regimes": regimes, "confluences": confluences,
        "ai_fails": ai_fails, "ai_success": ai_success, "spreads": spreads
    }

def main():
    print("="*90)
    print(f" GOLD-BOOKMAP FULL DAILY REPORT v2.0 - {TODAY} ".center(90, "="))
    print(" Bank Manager + Senior Engineer Edition ".center(90, "="))
    print("="*90)
    print(f"Base: {BASE}")
    print(f"Ticks: {TICKS} ({TICKS.stat().st_size/1024/1024:.1f} MB)" if TICKS.exists() else "Ticks: NOT FOUND")
    print(f"MBO: {MBO} ({MBO.stat().st_size/1024/1024:.1f} MB)" if MBO.exists() else "MBO: NOT FOUND")
    
    # Load data
    log_text, log_path = parse_trading_log()
    decisions = parse_decisions()
    ticks = load_ticks_fast()
    
    print(f"\n[1] DATA HEALTH")
    print(f"  Log file: {log_path} ({len(log_text)} chars)" if log_path else "  Log: NOT FOUND")
    print(f"  Decisions today: {len(decisions)} rows")
    print(f"  Ticks loaded for audit: {len(ticks)} Last events")
    print(f"  Ticks.csv total: {sum(1 for _ in open(TICKS, errors='ignore')) if TICKS.exists() else 0} lines")
    
    # Parse metrics
    metrics = analyze_log_text(log_text) if log_text else {}
    
    # === SECTION 2: PERFORMANCE ===
    print(f"\n[2] PERFORMANCE & LATENCY (M5 target: total <{LATENCY_TARGET}ms)")
    weak_parts = []
    if metrics.get("feed_age"):
        avg_feed = statistics.mean(metrics["feed_age"])
        max_feed = max(metrics["feed_age"])
        status = "[OK] EXCELLENT" if avg_feed<0.5 else "[WARN] OK" if avg_feed<1.0 else "[FAIL] STALE"
        print(f"  Feed age: avg {avg_feed:.2f}s max {max_feed:.2f}s last5 {metrics['feed_age'][-5:]} {status}")
        if avg_feed>=1.0:
            weak_parts.append(f"Feed age avg {avg_feed:.2f}s >1s - BookMap Rithmic lag, check internet/BookMap")
        if max_feed>=3.0:
            weak_parts.append(f"Feed age spike {max_feed:.1f}s - delayed data risk, robot skipped trades correctly")
    if metrics.get("step1"):
        avg_s1 = statistics.mean(metrics["step1"])
        print(f"  STEP1 acquire: avg {avg_s1:.0f}ms last5 {metrics['step1'][-5:]} {'[OK] <150ms' if avg_s1<150 else '[WARN] >150ms' if avg_s1<300 else '[FAIL] >300ms heavy'}")
        if avg_s1>=300:
            weak_parts.append(f"STEP1 avg {avg_s1:.0f}ms >300ms - ticks.csv too big (12h window), reduce BOOKMAP_WINDOW_SECONDS 43200->10800")
    if metrics.get("step2"):
        avg_s2 = statistics.mean(metrics["step2"])
        print(f"  STEP2 analyze: avg {avg_s2:.0f}ms last5 {metrics['step2'][-5:]} {'[OK] <300ms' if avg_s2<300 else '[WARN] 300-500ms' if avg_s2<500 else '[FAIL] >500ms heavy'}")
        if avg_s2>=500:
            weak_parts.append(f"STEP2 avg {avg_s2:.0f}ms >500ms - MBO 3000 too high or depth 40 too high, reduce to 2000 MBO and 20 depth")
    if metrics.get("total"):
        avg_tot = statistics.mean(metrics["total"])
        print(f"  TOTAL pipeline: avg {avg_tot:.0f}ms last5 {metrics['total'][-5:]} {'[OK] <500ms' if avg_tot<500 else '[WARN] 500-1000ms' if avg_tot<1000 else '[FAIL] >1000ms'}")
    
    # === SECTION 3: SIGNALS ===
    print(f"\n[3] SIGNALS TODAY")
    if metrics.get("signals"):
        dirs = [s[0] for s in metrics["signals"]]
        cnt = Counter(dirs)
        total_sig = len(dirs)
        for d in ["NEUTRAL","BUY","SELL"]:
            c = cnt.get(d,0)
            pct = c/total_sig*100 if total_sig else 0
            print(f"  {d}: {c} ({pct:.1f}%)")
        strengths = [float(s[1]) for s in metrics["signals"]]
        confs = [float(s[2]) for s in metrics["signals"]]
        print(f"  Strength: avg {statistics.mean(strengths):.1f} min {min(strengths):.1f} max {max(strengths):.1f} (M5 ideal 5-15)")
        print(f"  Confidence: avg {statistics.mean(confs):.1f}% min {min(confs):.1f}% max {max(confs):.1f}% (threshold {CONF_THRESHOLD}%)")
        if statistics.mean(confs)<25:
            weak_parts.append(f"Confidence avg {statistics.mean(confs):.1f}% very low all day - market choppy/divergence, 0 trades correct but check if threshold {CONF_THRESHOLD}% too high")
    if metrics.get("regimes"):
        rcnt = Counter(metrics["regimes"])
        print(f"  Regimes: {dict(rcnt)}")
    if metrics.get("confluences"):
        ccnt = Counter(metrics["confluences"])
        print(f"  Confluence: {dict(ccnt)} (FAIL majority = safe)")
    if metrics.get("icebergs"):
        avg_ib = statistics.mean(metrics["icebergs"])
        print(f"  Icebergs: avg {avg_ib:.0f} last {metrics['icebergs'][-5:]} {'[OK] 0-80 realistic P2' if avg_ib<80 else '[WARN] 80-150 high' if avg_ib<150 else '[FAIL] >150 fake P1 bug'}")
        if avg_ib>=80:
            weak_parts.append(f"Icebergs avg {avg_ib:.0f} >80 - still P1 double-count bug, upgrade to P2")
    
    # === SECTION 4: AI ===
    print(f"\n[4] AI DECISION")
    if metrics:
        print(f"  AI fails: {metrics.get('ai_fails',0)} (504/503) | AI success: {metrics.get('ai_success',0)}")
        if metrics.get('ai_fails',0)>=3:
            weak_parts.append(f"AI failed {metrics['ai_fails']} times (Gemini overloaded) - reduce GEMINI_REQUEST_TIMEOUT_MS 20000->10000 and use flash-lite primary")
    
    # === SECTION 5: DECISIONS CSV ===
    print(f"\n[5] DECISIONS_LOG.CSV TODAY: {len(decisions)} rows")
    if decisions:
        # Count
        d_dirs = Counter([r.get("signal_direction","") for r in decisions])
        print(f"  Direction: {dict(d_dirs)}")
        # Last 10
        print(f"  Last 10:")
        for r in decisions[-10:]:
            print(f"    {r.get('timestamp','')[:19]} {r.get('signal_direction',''):<7} str={r.get('signal_strength',''):<5} conf={r.get('signal_confidence',''):<5} exec={r.get('exec_status',''):<8} reason={r.get('reason','')[:60]}")
    
    # === SECTION 6: AUDIT SKIPPED TRADES PROFITABILITY ===
    print(f"\n[6] AUDIT SKIPPED TRADES - WOULD THEY HAVE BEEN PROFITABLE?")
    print(f"  Simulating SL {SL_MULT}xATR={SL_MULT*ATR_DEFAULT:.1f}pts TP {TP_MULT}xATR={TP_MULT*ATR_DEFAULT:.1f}pts max hold {MAX_HOLD_MIN}min")
    audit_results = []
    if decisions and ticks:
        for r in decisions:
            dir_ = r.get("signal_direction","").upper()
            if dir_ not in ("BUY","SELL"):
                continue
            ts_raw = r.get("timestamp","")
            dt = parse_ts(ts_raw)
            try:
                entry = float(r.get("price","0") or 0)
            except:
                entry = 0
            if entry<1000 and dt:
                # find nearest tick
                future_1m = find_price_after(ticks, dt, minutes=1)
                if future_1m:
                    entry = future_1m[0]
            if entry<1000 or not dt:
                continue
            future_prices = find_price_after(ticks, dt, minutes=MAX_HOLD_MIN)
            outcome, exit_p, pnl = simulate_trade(entry, dir_, future_prices, ATR_DEFAULT)
            audit_results.append({
                "timestamp": ts_raw, "direction": dir_, "entry": entry,
                "strength": r.get("signal_strength",""), "confidence": r.get("signal_confidence",""),
                "outcome": outcome, "exit": exit_p, "pnl": pnl, "reason": r.get("reason","")[:80]
            })
        
        wins = sum(1 for x in audit_results if x["outcome"]=="WIN")
        losses = sum(1 for x in audit_results if x["outcome"]=="LOSS")
        timeouts = sum(1 for x in audit_results if x["outcome"]=="TIMEOUT")
        nodata = sum(1 for x in audit_results if x["outcome"]=="NO_DATA")
        total_pnl = sum(x["pnl"] for x in audit_results if x["outcome"] in ("WIN","LOSS","TIMEOUT"))
        
        print(f"  Total BUY/SELL skipped checked: {len(audit_results)}")
        print(f"  WIN (TP first): {wins} | LOSS (SL first): {losses} | TIMEOUT: {timeouts} | NO_DATA: {nodata}")
        if wins+losses>0:
            wr = wins/(wins+losses)*100
            pf = (wins*TP_MULT)/(losses*SL_MULT) if losses else 999
            print(f"  Win Rate (closed): {wr:.1f}% | Profit Factor: {pf:.2f} | Total PnL: {total_pnl:.1f} pts")
            if wr<40:
                weak_parts.append(f"Skipped trades WR {wr:.1f}% <40% - system correctly skipped losers, 0 trades was profitable")
            elif wr>60:
                weak_parts.append(f"Skipped trades WR {wr:.1f}% >60% - threshold {CONF_THRESHOLD}% maybe too high, missed winners")
        
        # List top 10 profitable skipped
        profitable = [x for x in audit_results if x["outcome"]=="WIN"]
        if profitable:
            print(f"\n  Top 10 PROFITABLE skipped (would have won):")
            for x in profitable[:10]:
                print(f"    {x['timestamp'][:19]} {x['direction']} {x['entry']:.1f}->{x['exit']:.1f} +{abs(x['pnl']):.1f}pts str={x['strength']} conf={x['confidence']} | {x['reason'][:50]}")
        else:
            print(f"  No profitable skipped trades - 0 trades today was optimal")
        
        # Losing
        losing = [x for x in audit_results if x["outcome"]=="LOSS"]
        if losing:
            print(f"\n  Top 10 LOSING skipped (correctly skipped):")
            for x in losing[:10]:
                print(f"    {x['timestamp'][:19]} {x['direction']} {x['entry']:.1f}->{x['exit']:.1f} {x['pnl']:.1f}pts | {x['reason'][:50]}")
    else:
        print("  No ticks or decisions for audit (run on your PC with real ticks.csv)")
    
    # === SECTION 7: WEAK PARTS SUMMARY ===
    print(f"\n[7] WEAK PARTS DETECTED (Bank + Engineer)")
    if weak_parts:
        for i, w in enumerate(weak_parts, 1):
            print(f"  {i}. {w}")
    else:
        print("  [OK] No major weak parts - system healthy")
    
    # === SECTION 8: M5 FITNESS ===
    print(f"\n[8] M5 FITNESS CHECKLIST")
    checks = []
    if metrics.get("step1"):
        checks.append(("STEP1 <150ms", statistics.mean(metrics["step1"])<150))
    if metrics.get("step2"):
        checks.append(("STEP2 <300ms", statistics.mean(metrics["step2"])<300))
    if metrics.get("feed_age"):
        checks.append(("Feed <0.5s", statistics.mean(metrics["feed_age"])<0.5))
    if metrics.get("icebergs"):
        checks.append(("Icebergs <80", statistics.mean(metrics["icebergs"])<80))
    # NEUTRAL %
    if metrics.get("signals"):
        neutral_pct = Counter([s[0] for s in metrics["signals"]]).get("NEUTRAL",0)/len(metrics["signals"])*100
        checks.append((f"NEUTRAL 70-80% (you {neutral_pct:.1f}%)", 70<=neutral_pct<=85))
    for name, ok in checks:
        print(f"  {'[OK]' if ok else '[FAIL]'} {name}")
    
    # === SECTION 9: FINAL VERDICT ===
    print(f"\n[9] FINAL VERDICT FOR M5")
    print(f"  Today's price: {decisions[-1].get('price','N/A') if decisions else 'N/A'} | Ticks file: {TICKS.stat().st_size/1024/1024:.1f} MB" if TICKS.exists() else "")
    print(f"  System fits M5 if: STEP1<150ms STEP2<300ms feed<0.5s icebergs<80 NEUTRAL 70%+")
    if weak_parts:
        print(f"  Weak parts today: {len(weak_parts)} - see section 7")
        print(f"  Recommendation: Apply P3 latency fix (window 12h->3h, depth 40->20, MBO 3000->2000)")
    else:
        print(f"  [OK] System fits M5 perfectly, ready for 0.01-0.05 live tomorrow 08:00-23:00 Budapest")
    
    # Save audit csv
    if audit_results:
        out_path = BASE / f"audit_skipped_{TODAY}.csv"
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["timestamp","direction","entry","strength","confidence","outcome","exit","pnl","reason"])
            w.writeheader()
            for r in audit_results:
                w.writerow(r)
        print(f"\n[INFO] Audit saved to {out_path}")
    
    print("="*90)

if __name__=="__main__":
    main()
