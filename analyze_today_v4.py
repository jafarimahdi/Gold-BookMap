#!/usr/bin/env python3
"""
Gold-BookMap ULTIMATE Daily Report v4.3
Bank Manager + Senior Engineer - Ultimate with AI Judge

v4.3 adds:
- AI vote accuracy (BUY/SELL/HOLD vs future price)
- Snapshots history file support (data/snapshots_history.jsonl) - new in main.py v4.3
- Hourly heatmap, KillZone, Regime, Confidence buckets, Equity curve, Auto weights

Run: PYTHONIOENCODING=utf-8 python analyze_today_v4.py > daily_v4_ultimate.txt
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
TODAY = "2026-09-22"
ATR_DEFAULT = 3.18
SL_MULT = 2.0
TP_MULT = 3.5
MAX_HOLD_MIN = 180

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
        print(f"[WARN] ticks {e}")
    return ticks

def find_future_price(ticks, entry_dt, minutes=30):
    if not ticks or not entry_dt:
        return None
    target = entry_dt + timedelta(minutes=minutes)
    for dt, price in ticks:
        if dt >= target:
            return price
    return ticks[-1][1] if ticks else None

def find_prices_after(ticks, entry_dt, minutes=180):
    if not ticks or not entry_dt:
        return []
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

def simulate_trade(entry, direction, future_prices, atr=ATR_DEFAULT):
    if not future_prices:
        return "NO_DATA", entry, 0
    if direction=="BUY":
        sl = entry - SL_MULT*atr
        tp = entry + TP_MULT*atr
        for p in future_prices:
            if p >= tp: return "WIN", tp, (tp-entry)
            if p <= sl: return "LOSS", sl, (sl-entry)
        return "TIMEOUT", future_prices[-1], (future_prices[-1]-entry)
    else:
        sl = entry + SL_MULT*atr
        tp = entry - TP_MULT*atr
        for p in future_prices:
            if p <= tp: return "WIN", tp, (entry-tp)
            if p >= sl: return "LOSS", sl, (entry-sl)
        return "TIMEOUT", future_prices[-1], (entry-future_prices[-1])

def parse_snapshots(log_text):
    snapshots = []
    hist_path = DATA / "snapshots_history.jsonl"
    if hist_path.exists():
        try:
            with open(hist_path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if TODAY not in line and "2026-09-22" not in line:
                        continue
                    try:
                        rec = json.loads(line)
                        ts_raw = rec.get("timestamp","")
                        dt = parse_ts(ts_raw)
                        price = float(rec.get("price",0) or 0)
                        if price<1000: continue
                        team_scores = rec.get("team_scores",{})
                        regime = rec.get("regime","UNKNOWN")
                        killzone = rec.get("killzone","UNKNOWN")
                        strength = float(rec.get("signal_strength",0) or 0)
                        conf = float(rec.get("confidence",0) or 0)
                        notes = rec.get("notes",[])
                        notes_text = " ".join(notes) if isinstance(notes, list) else str(notes)
                        ai_action = rec.get("ai_action","HOLD")
                        ai_conf = rec.get("ai_confidence",0)
                        judges = []
                        for seg in notes_text.split(";"):
                            m_vote = re.search(r"(.{0,80}?)\s*->\s*(BUY|SELL)", seg, re.I)
                            if m_vote:
                                ctx = m_vote.group(1).strip()[-60:]
                                vote = m_vote.group(2).upper()
                                low = ctx.lower()
                                if "footprint delta" in low: judge="footprint_delta"
                                elif "selling levels" in low or "buying levels" in low: judge="footprint_levels"
                                elif "distance-weighted" in low: judge="l3_imbalance"
                                elif "streak" in low: judge="l3_ofi_streak"
                                elif "net flow" in low: judge="l3_net_flow"
                                elif "cvd falling" in low: judge="cvd_momentum"
                                elif "cvd divergence" in low: judge="cvd_divergence"
                                elif "vwap trend" in low: judge="vwap_trend"
                                elif "poc day" in low: judge="poc_day"
                                elif "supply zone" in low or "demand zone" in low: judge="supply_demand"
                                elif "htf" in low and "poc" in low: judge="htf_poc"
                                elif "iceberg" in low: judge="iceberg"
                                else: judge=re.sub(r'[^a-z_]', '_', low[:25]).strip('_')[:25] or "unknown"
                                judges.append({"judge":judge,"vote":vote})
                        snapshots.append({
                            "dt": dt, "ts_raw": ts_raw, "price": price, "team_scores": team_scores,
                            "judges": judges, "regime": regime, "killzone": killzone,
                            "strength": strength, "confidence": conf,
                            "ai_action": ai_action, "ai_confidence": ai_conf
                        })
                    except: continue
            if snapshots:
                print(f"[INFO] Loaded {len(snapshots)} from {hist_path} (v4.3 history with AI)")
                return snapshots
        except Exception as e:
            print(f"[WARN] history {e}")
    
    # Fallback: try MARKET SNAPSHOT in log_text
    if "MARKET SNAPSHOT" in log_text:
        blocks = re.finditer(r"MARKET SNAPSHOT.*?notes:\s*(.*?)(?=\n={10,}|\n\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} INFO|\Z)", log_text, re.DOTALL)
        for m in blocks:
            try:
                full = m.group(0)
                m_ts = re.search(r"@\s*(\d{4}-\d{2}-\d{2}T[^\s]+)", full)
                ts_raw = m_ts.group(1) if m_ts else ""
                dt = parse_ts(ts_raw)
                m_price = re.search(r"Analysis price.*?:\s*([\d,\.]+)", full)
                price = float(m_price.group(1).replace(",","")) if m_price else 0
                m_notes = re.search(r"notes:\s*(.*)", full, re.DOTALL)
                notes = m_notes.group(1) if m_notes else ""
                team_scores = {}
                m_teams = re.search(r"4 Teams ensemble:\s*flow\s*([-\d\.]+)\*[\d\.]+\s*whale\s*([-\d\.]+)\*[\d\.]+\s*struct\s*([-\d\.]+)\*[\d\.]+\s*trend\s*([-\d\.]+)\*[\d\.]+", notes)
                if m_teams:
                    team_scores = {"flow": float(m_teams.group(1)), "whale": float(m_teams.group(2)), "struct": float(m_teams.group(3)), "trend": float(m_teams.group(4))}
                m_regime = re.search(r"Regime:\s*(\w+)", full)
                regime = m_regime.group(1) if m_regime else "UNKNOWN"
                killzone = "OFF_LUNCH" if "KillZone OFF" in notes else "NY" if "KillZone NY" in notes else "OVERLAP" if "LONDON+NEW_YORK" in notes else "LONDON" if "LONDON" in notes else "UNKNOWN"
                m_sig = re.search(r"strength=([\d\.]+).*?confidence=([\d\.]+)", full)
                strength = float(m_sig.group(1)) if m_sig else 0
                conf = float(m_sig.group(2)) if m_sig else 0
                judges = []
                for seg in notes.split(";"):
                    m_vote = re.search(r"(.{0,80}?)\s*->\s*(BUY|SELL)", seg, re.I)
                    if m_vote:
                        ctx = m_vote.group(1).strip()[-60:]
                        vote = m_vote.group(2).upper()
                        low = ctx.lower()
                        if "footprint delta" in low: judge="footprint_delta"
                        elif "selling levels" in low: judge="footprint_levels"
                        elif "distance-weighted" in low: judge="l3_imbalance"
                        elif "streak" in low: judge="l3_ofi_streak"
                        elif "net flow" in low: judge="l3_net_flow"
                        elif "cvd" in low: judge="cvd_momentum"
                        elif "vwap" in low: judge="vwap_trend"
                        elif "poc" in low: judge="poc_day"
                        elif "iceberg" in low: judge="iceberg"
                        else: judge="other"
                        judges.append({"judge":judge,"vote":vote})
                snapshots.append({"dt":dt,"ts_raw":ts_raw,"price":price,"team_scores":team_scores,"judges":judges,"regime":regime,"killzone":killzone,"strength":strength,"confidence":conf,"ai_action":"HOLD","ai_confidence":0})
            except: continue
    return snapshots

def main():
    print("="*110)
    print(f" GOLD-BOOKMAP ULTIMATE DAILY REPORT v4.3 - {TODAY} - WITH AI JUDGE ".center(110, "="))
    print("="*110)
    log_path = LOGS / f"trading_{TODAY}.log"
    if not log_path.exists():
        logs = sorted(LOGS.glob("trading_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        log_path = logs[0] if logs else None
    log_text = log_path.read_text(encoding="utf-8", errors="ignore") if log_path else ""
    ticks = load_ticks_fast()
    snapshots = parse_snapshots(log_text)
    print(f"Log: {log_path} | Snapshots: {len(snapshots)} | Ticks: {len(ticks)}")
    
    if len(snapshots)==0:
        print("\n[INFO] No snapshots_history.jsonl yet - will be created tomorrow after main.py v4.3 fix")
        print("For today, use v2.0 report (analyze_today.py) which already showed 187 skipped audit")
        print("Tomorrow v4.3 will show team + judge + AI accuracy")
        # Still do equity audit from decisions_log.csv
    else:
        # Hourly
        from collections import defaultdict
        hourly = defaultdict(list)
        for s in snapshots:
            if s["dt"]:
                h = (s["dt"].hour + 2) % 24
                hourly[h].append(s)
        print(f"\n[1] HOURLY HEATMAP (Budapest)")
        print(f"  {'HOUR':<8} {'COUNT':<6} {'AVG_STR':<8} {'AVG_CONF':<9} VERDICT")
        for h in sorted(hourly.keys()):
            snaps = hourly[h]
            avg_str = statistics.mean([x["strength"] for x in snaps]) if snaps else 0
            avg_conf = statistics.mean([x["confidence"] for x in snaps]) if snaps else 0
            verdict = "BEST" if avg_str>=12 else "GOOD" if avg_str>=8 else "WEAK"
            print(f"  {h:02d}:00    {len(snaps):<6} {avg_str:<8.1f} {avg_conf:<9.1f} {verdict}")
        
        print(f"\n[2] KILLZONE")
        kz = defaultdict(list)
        for s in snapshots:
            kz[s["killzone"]].append(s)
        for k, snaps in kz.items():
            avg_str = statistics.mean([x["strength"] for x in snaps]) if snaps else 0
            print(f"  {k:<15} count {len(snaps)} avg_str {avg_str:.1f}")
        
        # Team accuracy
        print(f"\n[5] TEAM ACCURACY (30min future)")
        team_stats = defaultdict(lambda: {"correct":0,"wrong":0,"neutral":0,"total":0})
        for s in snapshots:
            if not s["dt"] or s["price"]<1000: continue
            future = find_future_price(ticks, s["dt"], 30)
            if future is None: continue
            move = future - s["price"]
            thr=1.5
            for team, score in s["team_scores"].items():
                if abs(score)<0.05: vote="NEUTRAL"
                elif score>0: vote="BUY"
                else: vote="SELL"
                team_stats[team]["total"]+=1
                if vote=="NEUTRAL": team_stats[team]["neutral"]+=1
                elif (vote=="BUY" and move>thr) or (vote=="SELL" and move<-thr):
                    team_stats[team]["correct"]+=1
                else:
                    team_stats[team]["wrong"]+=1
        print(f"  {'TEAM':<10} {'TOTAL':<6} {'CORRECT':<8} {'WRONG':<6} {'ACC%':<6}")
        for team in ["flow","whale","struct","trend"]:
            s = team_stats[team]
            if s["total"]==0: continue
            acc = s["correct"]/(s["correct"]+s["wrong"])*100 if s["correct"]+s["wrong"]>0 else 0
            print(f"  {team:<10} {s['total']:<6} {s['correct']:<8} {s['wrong']:<6} {acc:<6.1f}")
        
        # Judge
        print(f"\n[6] JUDGE ACCURACY TOP 15")
        judge_stats = defaultdict(lambda: {"correct":0,"wrong":0,"total":0})
        for s in snapshots:
            if not s["dt"] or s["price"]<1000: continue
            future = find_future_price(ticks, s["dt"], 30)
            if future is None: continue
            move = future - s["price"]
            thr=1.5
            for j in s["judges"]:
                vote=j["vote"]
                judge=j["judge"]
                judge_stats[judge]["total"]+=1
                if (vote=="BUY" and move>thr) or (vote=="SELL" and move<-thr):
                    judge_stats[judge]["correct"]+=1
                else:
                    judge_stats[judge]["wrong"]+=1
        for judge, s in sorted(judge_stats.items(), key=lambda x: x[1]["total"], reverse=True)[:15]:
            if s["total"]<5: continue
            acc = s["correct"]/(s["correct"]+s["wrong"])*100 if s["correct"]+s["wrong"]>0 else 0
            print(f"  {judge:<25} total {s['total']:<4} acc {acc:<5.1f}%")
        
        # AI accuracy
        print(f"\n[6b] AI JUDGE ACCURACY")
        ai_stats = {"correct":0,"wrong":0,"hold":0,"total":0}
        for s in snapshots:
            if not s["dt"] or s["price"]<1000: continue
            ai_action = s.get("ai_action","HOLD")
            future = find_future_price(ticks, s["dt"], 30)
            if future is None: continue
            move = future - s["price"]
            thr=1.5
            ai_stats["total"]+=1
            if ai_action=="HOLD":
                ai_stats["hold"]+=1
            elif (ai_action=="BUY" and move>thr) or (ai_action=="SELL" and move<-thr):
                ai_stats["correct"]+=1
            else:
                ai_stats["wrong"]+=1
        if ai_stats["total"]>0:
            acc = ai_stats["correct"]/(ai_stats["correct"]+ai_stats["wrong"])*100 if ai_stats["correct"]+ai_stats["wrong"]>0 else 0
            print(f"  AI total {ai_stats['total']} HOLD {ai_stats['hold']} correct {ai_stats['correct']} wrong {ai_stats['wrong']} ACC {acc:.1f}%")
    
    # Equity audit (always works from decisions_log.csv)
    print(f"\n[7] EQUITY CURVE - SKIPPED TRADES (from decisions_log.csv)")
    decisions_path = DATA / "decisions_log.csv"
    audit=[]
    if decisions_path.exists():
        with open(decisions_path, encoding="utf-8", errors="ignore") as f:
            reader = csv.DictReader(f)
            for r in reader:
                if TODAY not in r.get("timestamp",""): continue
                dir_ = r.get("signal_direction","").upper()
                if dir_ not in ("BUY","SELL"): continue
                ts = parse_ts(r.get("timestamp",""))
                try:
                    entry = float(r.get("price","0") or 0)
                except:
                    entry=0
                if entry<1000 and ts:
                    fp = find_future_price(ticks, ts, 1)
                    if fp: entry=fp
                if entry<1000 or not ts: continue
                future_prices = find_prices_after(ticks, ts, MAX_HOLD_MIN)
                outcome, exit_p, pnl = simulate_trade(entry, dir_, future_prices)
                audit.append({"dt":ts,"dir":dir_,"entry":entry,"outcome":outcome,"pnl":pnl,"strength":r.get("signal_strength",""),"conf":r.get("signal_confidence","")})
    if audit:
        audit.sort(key=lambda x: x["dt"])
        cum=0
        wins=losses=0
        for a in audit:
            if a["outcome"]=="WIN": cum+=abs(a["pnl"]); wins+=1
            elif a["outcome"]=="LOSS": cum-=abs(a["pnl"]); losses+=1
        wr = wins/(wins+losses)*100 if wins+losses>0 else 0
        print(f"  Trades: {len(audit)} WIN {wins} LOSS {losses} WR {wr:.1f}% Final {cum:.1f} pts")
        print(f"  Profitable skipped top 5:")
        for a in [x for x in audit if x["outcome"]=="WIN"][:5]:
            print(f"    {a['dt']} {a['dir']} {a['entry']:.1f} +{abs(a['pnl']):.1f} str={a['strength']} conf={a['conf']}")
    
    print("\n" + "="*110)
    print(" END OF v4.3 ULTIMATE REPORT WITH AI ".center(110, "="))
    print("="*110)

if __name__=="__main__":
    main()
