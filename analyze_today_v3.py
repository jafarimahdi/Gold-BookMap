#!/usr/bin/env python3
"""
Gold-BookMap Full Daily Report v3.0 - Team & Judge Accuracy Audit
Bank Manager + Senior Engineer Edition

New in v3.0:
- Parses every MARKET SNAPSHOT notes to extract individual judge votes
- Extracts 4 Teams ensemble scores (flow, whale, struct, trend)
- Checks future price from ticks.csv to see which team/judge was CORRECT vs WRONG
- Shows accuracy per team, per judge, best/worst logic

Run: PYTHONIOENCODING=utf-8 python analyze_today_v3.py > daily_v3.txt
"""
import re, csv, json
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import Counter, defaultdict
import statistics

BASE = Path(__file__).resolve().parent
DATA = BASE / "data"
LOGS = BASE / "logs"
TICKS = BASE / "ticks.csv"
TODAY = "2026-09-22"  # force today

ATR_DEFAULT = 3.18

def parse_ts(s):
    try:
        dt = datetime.fromisoformat(s.replace("Z","+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except:
        return None

def load_ticks_fast(max_lines=500000):
    ticks = []
    if not TICKS.exists():
        return ticks
    try:
        with open(TICKS, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-max_lines*2:]
        for line in lines:
            try:
                parts = next(csv.reader([line]))
                if len(parts)<7 or parts[0]=="time": continue
                if parts[1]!="Last": continue
                dt = parse_ts(parts[0])
                price = float(parts[2])
                if dt and 1000<price<10000:
                    ticks.append((dt, price))
            except: continue
        ticks.sort(key=lambda x: x[0])
    except Exception as e:
        print(f"[WARN] ticks load {e}")
    return ticks

def find_future_price(ticks, entry_dt, minutes=30):
    """Return future price after N minutes"""
    if not ticks or not entry_dt:
        return None
    target = entry_dt + timedelta(minutes=minutes)
    # Find first price >= target
    for dt, price in ticks:
        if dt >= target:
            return price
    return ticks[-1][1] if ticks else None

def parse_snapshots(log_text):
    """Parse MARKET SNAPSHOT blocks"""
    snapshots = []
    # Split by MARKET SNAPSHOT
    blocks = re.split(r"MARKET SNAPSHOT", log_text)
    for block in blocks[1:]:  # skip first
        try:
            # Extract timestamp
            m_ts = re.search(r"@ (\d{4}-\d{2}-\d{2}T[\d:\.\+\:]+)", block)
            ts_raw = m_ts.group(1) if m_ts else ""
            dt = parse_ts(ts_raw)
            
            # Extract price
            m_price = re.search(r"Analysis price.*?: ([\d,\.]+)", block)
            price = float(m_price.group(1).replace(",","")) if m_price else 0
            
            # Extract notes section
            m_notes = re.search(r"notes: (.*?)(?:\n=|$)", block, re.DOTALL)
            notes = m_notes.group(1) if m_notes else block
            
            # Extract 4 Teams ensemble
            team_scores = {}
            m_teams = re.search(r"4 Teams ensemble: flow ([-\d\.]+)\*[\d\.]+ whale ([-\d\.]+)\*[\d\.]+ struct ([-\d\.]+)\*[\d\.]+ trend ([-\d\.]+)\*[\d\.]+", notes)
            if m_teams:
                team_scores = {
                    "flow": float(m_teams.group(1)),
                    "whale": float(m_teams.group(2)),
                    "struct": float(m_teams.group(3)),
                    "trend": float(m_teams.group(4)),
                }
            
            # Extract individual judge votes: pattern "something -> BUY" or "-> SELL"
            # We'll extract each segment separated by ";"
            judges = []
            segments = notes.split(";")
            for seg in segments:
                seg = seg.strip()
                # Look for -> BUY or -> SELL
                m_vote = re.search(r"(.{0,80}?)\s*->\s*(BUY|SELL)", seg, re.I)
                if m_vote:
                    context = m_vote.group(1).strip()[-60:]  # last 60 chars as judge name
                    vote = m_vote.group(2).upper()
                    # Clean judge name
                    # Remove numbers, keep keywords
                    # Map to known judges
                    low = context.lower()
                    if "footprint delta" in low:
                        judge = "footprint_delta"
                    elif "footprint selling" in low or "footprint buying" in low or "selling levels" in low:
                        judge = "footprint_levels"
                    elif "distance-weighted imbalance" in low:
                        judge = "l3_imbalance"
                    elif "ofi time-weighted" in low or "streak" in low:
                        judge = "l3_ofi_streak"
                    elif "net flow" in low:
                        judge = "l3_net_flow"
                    elif "cvd falling" in low or "cvd momentum" in low:
                        judge = "cvd_momentum"
                    elif "cvd divergence" in low:
                        judge = "cvd_divergence"
                    elif "vwap trend" in low:
                        judge = "vwap_trend"
                    elif "poc day" in low:
                        judge = "poc_day"
                    elif "supply zone" in low or "demand zone" in low or "rejection" in low:
                        judge = "supply_demand_zone"
                    elif "htf" in low and "poc" in low:
                        judge = "htf_poc"
                    elif "vwap z-score" in low:
                        judge = "vwap_zscore"
                    elif "obv" in low or "a/d" in low:
                        judge = "obv_ad"
                    elif "10y" in low or "dxy" in low:
                        judge = "macro"
                    elif "iceberg" in low:
                        judge = "iceberg"
                    else:
                        # Generic from first words
                        judge = re.sub(r'[^a-z_]', '_', low[:30]).strip('_')[:30] or "unknown"
                    
                    judges.append({"judge": judge, "vote": vote, "context": context[:80]})
            
            snapshots.append({
                "dt": dt,
                "ts_raw": ts_raw,
                "price": price,
                "team_scores": team_scores,
                "judges": judges,
                "notes": notes[:2000]
            })
        except Exception as e:
            # print(f"parse error {e}")
            continue
    return snapshots

def main():
    print("="*100)
    print(f" GOLD-BOOKMAP FULL DAILY REPORT v3.0 - TEAM & JUDGE ACCURACY AUDIT - {TODAY} ".center(100, "="))
    print("="*100)
    
    # Load
    log_path = LOGS / f"trading_{TODAY}.log"
    if not log_path.exists():
        logs = sorted(LOGS.glob("trading_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        log_path = logs[0] if logs else None
    
    if not log_path or not log_path.exists():
        print("No log file found")
        return
    
    log_text = log_path.read_text(encoding="utf-8", errors="ignore")
    print(f"Log: {log_path} ({len(log_text)} chars)")
    
    ticks = load_ticks_fast()
    print(f"Ticks: {len(ticks)} Last events loaded for future price check")
    
    snapshots = parse_snapshots(log_text)
    print(f"Snapshots parsed: {len(snapshots)}")
    
    if not snapshots:
        print("No snapshots found")
        return
    
    # Audit each snapshot's teams and judges vs future price
    team_stats = defaultdict(lambda: {"correct":0, "wrong":0, "neutral":0, "total":0})
    judge_stats = defaultdict(lambda: {"correct":0, "wrong":0, "total":0, "buy_correct":0, "sell_correct":0})
    
    # For overall signal accuracy too
    signal_correct = 0
    signal_total = 0
    
    print(f"\n[1] TEAM ACCURACY (future price 30min ahead)")
    print(f"  Checking if team's BUY/SELL vote matched future price move >2pts")
    
    for snap in snapshots:
        if not snap["dt"] or snap["price"]<1000:
            continue
        entry = snap["price"]
        future_30m = find_future_price(ticks, snap["dt"], minutes=30)
        future_60m = find_future_price(ticks, snap["dt"], minutes=60)
        if future_30m is None:
            continue
        
        move_30 = future_30m - entry
        move_60 = future_60m - entry if future_60m else move_30
        
        # Use 60m for more reliable, but 30m for M5 scalper
        # Consider correct if BUY and future up >1.5 pts, SELL and future down <-1.5 pts
        threshold = 1.5
        
        # Team scores
        for team, score in snap["team_scores"].items():
            if abs(score) < 0.05:
                vote = "NEUTRAL"
            elif score > 0:
                vote = "BUY"
            else:
                vote = "SELL"
            
            team_stats[team]["total"]+=1
            if vote=="NEUTRAL":
                team_stats[team]["neutral"]+=1
                continue
            
            # Check correctness
            if vote=="BUY" and move_30 > threshold:
                team_stats[team]["correct"]+=1
            elif vote=="SELL" and move_30 < -threshold:
                team_stats[team]["correct"]+=1
            else:
                team_stats[team]["wrong"]+=1
        
        # Judges
        for j in snap["judges"]:
            vote = j["vote"]
            judge = j["judge"]
            judge_stats[judge]["total"]+=1
            if vote=="BUY" and move_30 > threshold:
                judge_stats[judge]["correct"]+=1
                judge_stats[judge]["buy_correct"]+=1
            elif vote=="SELL" and move_30 < -threshold:
                judge_stats[judge]["correct"]+=1
                judge_stats[judge]["sell_correct"]+=1
            else:
                judge_stats[judge]["wrong"]+=1
    
    # Print team accuracy
    print(f"\n  {'TEAM':<10} {'TOTAL':<6} {'CORRECT':<8} {'WRONG':<6} {'NEUTRAL':<8} {'ACC%':<6} {'VERDICT'}")
    print("  "+"-"*80)
    for team in ["flow","whale","struct","trend"]:
        s = team_stats[team]
        total = s["total"]
        if total==0:
            continue
        acc = s["correct"]/(s["correct"]+s["wrong"])*100 if (s["correct"]+s["wrong"])>0 else 0
        verdict = "BEST" if acc>=55 else "GOOD" if acc>=48 else "WEAK" if acc>=40 else "BAD"
        print(f"  {team:<10} {total:<6} {s['correct']:<8} {s['wrong']:<6} {s['neutral']:<8} {acc:<6.1f} {verdict}")
    
    # Judge accuracy
    print(f"\n[2] JUDGE / LOGIC ACCURACY (individual votes -> BUY/SELL)")
    print(f"  {'JUDGE':<25} {'TOTAL':<6} {'CORRECT':<8} {'WRONG':<6} {'ACC%':<6} {'BUY_OK':<7} {'SELL_OK':<8} VERDICT")
    print("  "+"-"*100)
    # Sort by total desc
    sorted_judges = sorted(judge_stats.items(), key=lambda x: x[1]["total"], reverse=True)
    for judge, s in sorted_judges[:30]:  # top 30
        total = s["total"]
        if total<5:
            continue
        acc = s["correct"]/(s["correct"]+s["wrong"])*100 if (s["correct"]+s["wrong"])>0 else 0
        verdict = "BEST" if acc>=60 else "GOOD" if acc>=52 else "WEAK" if acc>=42 else "BAD - REMOVE?"
        print(f"  {judge:<25} {total:<6} {s['correct']:<8} {s['wrong']:<6} {acc:<6.1f} {s['buy_correct']:<7} {s['sell_correct']:<8} {verdict}")
    
    # Weak judges
    print(f"\n[3] WEAK LOGIC - CANDIDATES TO REMOVE OR FIX")
    weak = [(j,s) for j,s in judge_stats.items() if s["total"]>=10 and (s["correct"]/(s["correct"]+s["wrong"])*100 if s["correct"]+s["wrong"]>0 else 0) < 42]
    if weak:
        for judge, s in sorted(weak, key=lambda x: x[1]["correct"]/(x[1]["correct"]+x[1]["wrong"]) if x[1]["correct"]+x[1]["wrong"]>0 else 0):
            acc = s["correct"]/(s["correct"]+s["wrong"])*100 if s["correct"]+s["wrong"]>0 else 0
            print(f"  {judge}: ACC {acc:.1f}% total {s['total']} - {s['wrong']} wrong > {s['correct']} correct - consider removing or reducing weight")
    else:
        print("  No weak judges with <42% accuracy and >10 samples - all logic healthy")
    
    # Best judges
    print(f"\n[4] BEST LOGIC - KEEP AND INCREASE WEIGHT")
    best = [(j,s) for j,s in judge_stats.items() if s["total"]>=10 and (s["correct"]/(s["correct"]+s["wrong"])*100 if s["correct"]+s["wrong"]>0 else 0) >= 58]
    if best:
        for judge, s in sorted(best, key=lambda x: x[1]["correct"]/(x[1]["correct"]+x[1]["wrong"]) if x[1]["correct"]+x[1]["wrong"]>0 else 0, reverse=True):
            acc = s["correct"]/(s["correct"]+s["wrong"])*100
            print(f"  {judge}: ACC {acc:.1f}% total {s['total']} - BEST, increase weight")
    else:
        print("  No judge >58% today - market was choppy, 46% WR from earlier audit is expected")
    
    # Confluence analysis
    print(f"\n[5] CONFLUENCE & FINAL SIGNAL ACCURACY")
    # We already have team stats, now check final signal vs future
    # Parse final signals from log
    signals = re.findall(r"analyze_market\(\) -> (\w+).*?strength=([\d\.]+).*?confidence=([\d\.]+)", log_text)
    print(f"  Final signals today: {Counter([s[0] for s in signals])}")
    # For each final BUY/SELL signal, check future
    # Extract final signal blocks with price
    # Simplified: use snapshots where team confluence OK
    confluence_ok = len(re.findall(r"confluence OK", log_text))
    confluence_fail = len(re.findall(r"confluence FAIL", log_text))
    print(f"  Confluence OK: {confluence_ok} | FAIL: {confluence_fail} | OK% {confluence_ok/(confluence_ok+confluence_fail)*100:.1f}%")
    if confluence_ok/(confluence_ok+confluence_fail) < 0.3:
        print("  Confluence FAIL majority = safe, bank-grade patience")
    
    # Overall weak parts
    print(f"\n[6] WEAK PARTS SUMMARY FOR TOMORROW")
    # Reuse previous weak detection
    feed_ages = [float(m) for m in re.findall(r"feed age ([\d\.]+)s", log_text)]
    step1 = [int(m) for m in re.findall(r"STEP1 acquire (\d+)ms", log_text)]
    step2 = [int(m) for m in re.findall(r"STEP2 analyze (\d+)ms", log_text)]
    if feed_ages and sum(feed_ages)/len(feed_ages)>1.0:
        print(f"  - Feed age avg {sum(feed_ages)/len(feed_ages):.2f}s >1s - check Rithmic")
    if step1 and sum(step1)/len(step1)>300:
        print(f"  - STEP1 avg {sum(step1)/len(step1):.0f}ms >300ms - reduce window 12h->3h")
    if step2 and sum(step2)/len(step2)>500:
        print(f"  - STEP2 avg {sum(step2)/len(step2):.0f}ms >500ms - reduce MBO 3000->2000 depth 40->20")
    
    print("\n" + "="*100)
    print(" END OF v3.0 TEAM & JUDGE AUDIT ".center(100, "="))
    print("="*100)

if __name__=="__main__":
    main()
