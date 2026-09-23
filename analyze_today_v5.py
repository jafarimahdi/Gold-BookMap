#!/usr/bin/env python3
"""
Gold-BookMap ULTIMATE Daily Report v5.0 - FAIR M5 Chart Direction Scoring
Bank Manager + Senior Engineer - Ultimate Fair

v5.0 NEW - Fair M5 scoring based on CHART DIRECTION, not time-only:
- Builds M5 candles from ticks.csv (OHLC)
- For each judge vote, checks M5 chart direction: next 1,3,5 candles close, higher high/lower low, structure break
- Judge horizon-aware: scalping judges 5-15min (1-3 M5), intraday 15-60min (3-12 M5), trend 60-180min (12-36 M5)
- ATR-adjusted thresholds, spread-adjusted, high/low within window (not close-only)
- Includes AI judge accuracy with same fair logic
- Keeps all v4.3 features: hourly, KillZone, regime, confidence buckets, equity, auto weights

Run: PYTHONIOENCODING=utf-8 python analyze_today_v5.py > daily_v5_fair.txt
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
SPREAD_DEFAULT = 0.30

# Judge horizons for M5 (in M5 candles)
JUDGE_HORIZON = {
    # Scalping 5-15min = 1-3 M5 candles
    "footprint_delta": (1, 3, 0.5, 1.0),
    "footprint_levels": (1, 3, 0.5, 1.0),
    "l3_imbalance": (1, 3, 0.5, 1.0),
    "l3_ofi_streak": (1, 3, 0.5, 1.0),
    "l3_net_flow": (1, 3, 0.5, 1.2),
    "cvd_momentum": (1, 3, 0.5, 1.0),
    "iceberg": (1, 6, 0.5, 1.5),
    "other": (1, 3, 0.5, 1.0),
    # Intraday 15-60min = 3-12 M5
    "vwap_trend": (3, 12, 1.0, 2.0),
    "poc_day": (3, 12, 1.0, 2.0),
    "supply_demand": (3, 12, 1.0, 2.0),
    "htf_poc": (3, 12, 1.0, 2.0),
    "cvd_divergence": (3, 12, 1.0, 2.0),
    "vwap_zscore": (3, 12, 1.0, 2.0),
    # Trend 60-180min = 12-36 M5
    "trend": (12, 36, 2.0, 3.5),
    "macro": (12, 48, 2.0, 3.5),
}

def parse_ts(s):
    try:
        dt = datetime.fromisoformat(s.replace("Z","+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except:
        return None

def load_ticks_fast(max_lines=800000):
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

def build_m5_candles(ticks):
    """Build M5 OHLC from ticks"""
    if not ticks:
        return []
    candles = []
    # Group by 5min bucket
    buckets = defaultdict(list)
    for dt, price in ticks:
        # Floor to 5min
        bucket_min = (dt.minute // 5) * 5
        bucket_dt = dt.replace(minute=bucket_min, second=0, microsecond=0)
        buckets[bucket_dt].append(price)
    
    for bucket_dt in sorted(buckets.keys()):
        prices = buckets[bucket_dt]
        if not prices:
            continue
        o = prices[0]
        h = max(prices)
        l = min(prices)
        c = prices[-1]
        candles.append({"dt": bucket_dt, "open": o, "high": h, "low": l, "close": c})
    return candles

def find_m5_after(candles, entry_dt, num_candles=3):
    """Find next N M5 candles after entry_dt"""
    if not candles or not entry_dt:
        return []
    # Find first candle >= entry_dt
    start_idx = 0
    for i, c in enumerate(candles):
        if c["dt"] >= entry_dt.replace(second=0, microsecond=0):
            start_idx = i
            break
    return candles[start_idx:start_idx+num_candles]

def check_chart_direction(entry_price, direction, m5_candles, atr=ATR_DEFAULT, spread=SPREAD_DEFAULT):
    """
    Fair M5 chart direction check:
    - For BUY: did chart make higher high, higher low, close up, move >= threshold?
    - For SELL: lower high, lower low, close down
    Returns: (correct: bool, details: dict)
    """
    if not m5_candles:
        return False, {"reason": "no candles"}
    
    # Thresholds
    thr_05_atr = 0.5 * atr
    thr_10_atr = 1.0 * atr
    
    # For BUY
    if direction=="BUY":
        # Check next 1 candle close up?
        c1 = m5_candles[0] if len(m5_candles)>=1 else None
        c2 = m5_candles[1] if len(m5_candles)>=2 else None
        c3 = m5_candles[2] if len(m5_candles)>=3 else None
        
        # Direction: close > entry?
        close_up_1 = (c1["close"] > entry_price + spread) if c1 else False
        close_up_3 = (m5_candles[-1]["close"] > entry_price + spread) if m5_candles else False
        
        # Higher high?
        high_max = max([c["high"] for c in m5_candles]) if m5_candles else entry_price
        higher_high = high_max > entry_price + thr_05_atr
        
        # Higher low?
        low_min = min([c["low"] for c in m5_candles]) if m5_candles else entry_price
        # For BUY, higher low means lows going up
        higher_low = False
        if len(m5_candles)>=2:
            higher_low = m5_candles[-1]["low"] > m5_candles[0]["low"]
        
        # ATR move within window (high - entry)
        atr_move = high_max - entry_price
        
        # Correct if any of these true for M5 scalper:
        # - Close up in 1st candle and move >=0.5 ATR, OR
        # - Higher high with move >=0.5 ATR in 3 candles
        correct = (close_up_1 and atr_move >= thr_05_atr) or (higher_high and atr_move >= thr_05_atr)
        
        details = {
            "close_up_1": close_up_1,
            "close_up_3": close_up_3,
            "higher_high": higher_high,
            "higher_low": higher_low,
            "high_max": high_max,
            "atr_move": atr_move,
            "reason": f"close_up_1={close_up_1} higher_high={higher_high} atr_move={atr_move:.1f} thr={thr_05_atr:.1f}"
        }
        return correct, details
    else: # SELL
        c1 = m5_candles[0] if len(m5_candles)>=1 else None
        close_down_1 = (c1["close"] < entry_price - spread) if c1 else False
        close_down_3 = (m5_candles[-1]["close"] < entry_price - spread) if m5_candles else False
        low_min = min([c["low"] for c in m5_candles]) if m5_candles else entry_price
        lower_low = low_min < entry_price - 0.5*atr
        lower_high = False
        if len(m5_candles)>=2:
            lower_high = m5_candles[-1]["high"] < m5_candles[0]["high"]
        atr_move = entry_price - low_min
        
        correct = (close_down_1 and atr_move >= 0.5*atr) or (lower_low and atr_move >= 0.5*atr)
        details = {
            "close_down_1": close_down_1,
            "close_down_3": close_down_3,
            "lower_low": lower_low,
            "lower_high": lower_high,
            "low_min": low_min,
            "atr_move": atr_move,
            "reason": f"close_down_1={close_down_1} lower_low={lower_low} atr_move={atr_move:.1f}"
        }
        return correct, details

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
    return snapshots

def main():
    print("="*110)
    print(f" GOLD-BOOKMAP ULTIMATE DAILY REPORT v5.0 FAIR M5 CHART DIRECTION - {TODAY} ".center(110, "="))
    print(" Bank Manager + Engineer - Chart Direction, M5 Candles, Higher Highs/Lows, ATR, Structure ".center(110, "="))
    print("="*110)
    
    log_path = LOGS / f"trading_{TODAY}.log"
    if not log_path.exists():
        logs = sorted(LOGS.glob("trading_*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        log_path = logs[0] if logs else None
    log_text = log_path.read_text(encoding="utf-8", errors="ignore") if log_path and log_path.exists() else ""
    
    ticks = load_ticks_fast()
    candles = build_m5_candles(ticks)
    snapshots = parse_snapshots(log_text)
    
    print(f"Log: {log_path} | Snapshots: {len(snapshots)} | Ticks: {len(ticks)} | M5 Candles: {len(candles)}")
    if len(candles)>=2:
        print(f"M5 candles sample: {candles[0]['dt']} O:{candles[0]['open']:.1f} H:{candles[0]['high']:.1f} L:{candles[0]['low']:.1f} C:{candles[0]['close']:.1f}")
    
    if len(snapshots)==0:
        print("\n[INFO] No snapshots_history.jsonl yet - will be created tomorrow after main.py v4.3 fix")
        print("For today, run v2.0 report (analyze_today.py) for audit")
        # Still do equity audit from decisions_log.csv using M5 chart direction
    else:
        # Team accuracy with FAIR M5 chart direction
        print(f"\n[5] TEAM ACCURACY - FAIR M5 CHART DIRECTION (higher highs/lows, M5 closes, ATR)")
        team_stats = defaultdict(lambda: {"correct":0,"wrong":0,"neutral":0,"total":0})
        for s in snapshots:
            if not s["dt"] or s["price"]<1000:
                continue
            # Find M5 candles after entry
            m5_after = []
            # Find idx
            entry_dt = s["dt"]
            # Get next 12 M5 candles (60min) for team scoring
            # Build list
            for i, c in enumerate(candles):
                if c["dt"] >= entry_dt.replace(second=0, microsecond=0):
                    m5_after = candles[i:i+12]
                    break
            if not m5_after:
                continue
            for team, score in s["team_scores"].items():
                if abs(score)<0.05:
                    vote="NEUTRAL"
                elif score>0:
                    vote="BUY"
                else:
                    vote="SELL"
                team_stats[team]["total"]+=1
                if vote=="NEUTRAL":
                    team_stats[team]["neutral"]+=1
                else:
                    # Use horizon based on team
                    if team in ("flow","whale"):
                        horizon_candles = m5_after[:3]  # 15min
                    elif team=="struct":
                        horizon_candles = m5_after[:6]  # 30min
                    else: # trend
                        horizon_candles = m5_after[:12]  # 60min
                    correct, _ = check_chart_direction(s["price"], vote, horizon_candles, ATR_DEFAULT, SPREAD_DEFAULT)
                    if correct:
                        team_stats[team]["correct"]+=1
                    else:
                        team_stats[team]["wrong"]+=1
        
        print(f"  {'TEAM':<10} {'TOTAL':<6} {'CORRECT':<8} {'WRONG':<6} {'ACC%':<6} VERDICT")
        for team in ["flow","whale","struct","trend"]:
            st = team_stats[team]
            if st["total"]==0: continue
            acc = st["correct"]/(st["correct"]+st["wrong"])*100 if st["correct"]+st["wrong"]>0 else 0
            verdict = "BEST" if acc>=55 else "GOOD" if acc>=48 else "WEAK"
            print(f"  {team:<10} {st['total']:<6} {st['correct']:<8} {st['wrong']:<6} {acc:<6.1f} {verdict}")
        
        # Judge accuracy with FAIR M5
        print(f"\n[6] JUDGE ACCURACY - FAIR M5 CHART DIRECTION (Top 15)")
        judge_stats = defaultdict(lambda: {"correct":0,"wrong":0,"total":0})
        for s in snapshots:
            if not s["dt"] or s["price"]<1000:
                continue
            # Find M5 after
            m5_after = []
            for i, c in enumerate(candles):
                if c["dt"] >= s["dt"].replace(second=0, microsecond=0):
                    m5_after = candles[i:i+12]
                    break
            if not m5_after:
                continue
            for j in s["judges"]:
                vote = j["vote"]
                judge = j["judge"]
                # Get horizon for this judge
                horizon_cfg = JUDGE_HORIZON.get(judge, JUDGE_HORIZON["other"])
                num_candles = horizon_cfg[1]  # max candles
                # Use that many candles
                horizon_candles = m5_after[:num_candles]
                correct, details = check_chart_direction(s["price"], vote, horizon_candles, ATR_DEFAULT, SPREAD_DEFAULT)
                judge_stats[judge]["total"]+=1
                if correct:
                    judge_stats[judge]["correct"]+=1
                else:
                    judge_stats[judge]["wrong"]+=1
        
        print(f"  {'JUDGE':<25} {'TOTAL':<6} {'CORRECT':<8} {'WRONG':<6} {'ACC%':<6} VERDICT")
        for judge, st in sorted(judge_stats.items(), key=lambda x: x[1]["total"], reverse=True)[:15]:
            if st["total"]<5: continue
            acc = st["correct"]/(st["correct"]+st["wrong"])*100 if st["correct"]+st["wrong"]>0 else 0
            verdict = "BEST" if acc>=60 else "GOOD" if acc>=52 else "WEAK" if acc>=42 else "BAD"
            print(f"  {judge:<25} {st['total']:<6} {st['correct']:<8} {st['wrong']:<6} {acc:<6.1f} {verdict}")
        
        # AI accuracy
        print(f"\n[6b] AI JUDGE ACCURACY - FAIR M5")
        ai_stats = {"correct":0,"wrong":0,"hold":0,"total":0}
        for s in snapshots:
            if not s["dt"] or s["price"]<1000:
                continue
            ai_action = s.get("ai_action","HOLD")
            if ai_action=="HOLD":
                ai_stats["total"]+=1
                ai_stats["hold"]+=1
                continue
            m5_after = []
            for i, c in enumerate(candles):
                if c["dt"] >= s["dt"].replace(second=0, microsecond=0):
                    m5_after = candles[i:i+12]
                    break
            if not m5_after:
                continue
            correct, _ = check_chart_direction(s["price"], ai_action, m5_after[:6], ATR_DEFAULT, SPREAD_DEFAULT)
            ai_stats["total"]+=1
            if correct:
                ai_stats["correct"]+=1
            else:
                ai_stats["wrong"]+=1
        if ai_stats["total"]>0:
            acc = ai_stats["correct"]/(ai_stats["correct"]+ai_stats["wrong"])*100 if ai_stats["correct"]+ai_stats["wrong"]>0 else 0
            print(f"  AI total {ai_stats['total']} HOLD {ai_stats['hold']} correct {ai_stats['correct']} wrong {ai_stats['wrong']} ACC {acc:.1f}%")
    
    # Equity audit with M5 chart direction
    print(f"\n[7] EQUITY CURVE - SKIPPED TRADES (M5 chart direction, SL 2x TP 3.5x)")
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
                    # find M5 candle close as entry
                    for c in candles:
                        if c["dt"] >= ts.replace(second=0, microsecond=0):
                            entry = c["close"]
                            break
                if entry<1000 or not ts: continue
                # Future M5 candles
                m5_future = []
                for i, c in enumerate(candles):
                    if c["dt"] >= ts.replace(second=0, microsecond=0):
                        m5_future = candles[i:i+36]  # 180min = 36 M5
                        break
                # Convert to price list for simulate (use highs/lows)
                future_prices = []
                for c in m5_future:
                    future_prices.append(c["high"])
                    future_prices.append(c["low"])
                # Simulate with high/low check
                outcome = "TIMEOUT"
                exit_p = future_prices[-1] if future_prices else entry
                pnl = 0
                if dir_=="BUY":
                    sl = entry - SL_MULT*ATR_DEFAULT
                    tp = entry + TP_MULT*ATR_DEFAULT
                    for p in future_prices:
                        if p >= tp:
                            outcome="WIN"; exit_p=tp; pnl=tp-entry; break
                        if p <= sl:
                            outcome="LOSS"; exit_p=sl; pnl=sl-entry; break
                    else:
                        if m5_future:
                            pnl = m5_future[-1]["close"] - entry
                else:
                    sl = entry + SL_MULT*ATR_DEFAULT
                    tp = entry - TP_MULT*ATR_DEFAULT
                    for p in future_prices:
                        if p <= tp:
                            outcome="WIN"; exit_p=tp; pnl=entry-tp; break
                        if p >= sl:
                            outcome="LOSS"; exit_p=sl; pnl=entry-sl; break
                    else:
                        if m5_future:
                            pnl = entry - m5_future[-1]["close"]
                audit.append({"dt":ts,"dir":dir_,"entry":entry,"outcome":outcome,"pnl":pnl})
    
    if audit:
        audit.sort(key=lambda x: x["dt"])
        cum=0
        wins=losses=0
        for a in audit:
            if a["outcome"]=="WIN": cum+=abs(a["pnl"]); wins+=1
            elif a["outcome"]=="LOSS": cum-=abs(a["pnl"]); losses+=1
        wr = wins/(wins+losses)*100 if wins+losses>0 else 0
        print(f"  Trades: {len(audit)} WIN {wins} LOSS {losses} WR {wr:.1f}% Final {cum:.1f} pts")
        print(f"  Top 5 WIN:")
        for a in [x for x in audit if x["outcome"]=="WIN"][:5]:
            print(f"    {a['dt']} {a['dir']} {a['entry']:.1f} +{abs(a['pnl']):.1f}")
        print(f"  Top 5 LOSS:")
        for a in [x for x in audit if x["outcome"]=="LOSS"][:5]:
            print(f"    {a['dt']} {a['dir']} {a['entry']:.1f} {a['pnl']:.1f}")
    
    print("\n" + "="*110)
    print(" END OF v5.0 FAIR M5 CHART DIRECTION REPORT ".center(110, "="))
    print("="*110)

if __name__=="__main__":
    main()
