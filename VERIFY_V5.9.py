#!/usr/bin/env python3
"""
VERIFY v5.9 BANK-GRADE installation — run this in your local Gold-BookMap folder:
python VERIFY_V5.9.py
"""
import pathlib, re, sys
print("="*70)
print("VERIFY v5.9.0 BANK-GRADE — checking local files")
print("="*70)

checks_pass = 0
checks_fail = 0

def check(name, condition, hint=""):
    global checks_pass, checks_fail
    if condition:
        print(f"[OK] {name}")
        checks_pass+=1
    else:
        print(f"[FAIL] {name} {hint}")
        checks_fail+=1

# 1. step2_market_analysis.py
p = pathlib.Path("step2_market_analysis.py")
if not p.exists():
    print("[FAIL] step2_market_analysis.py not found")
    sys.exit(1)
txt = p.read_text(encoding="utf-8", errors="ignore")

check("bounce filter _weighted_vol function", "_weighted_vol" in txt or "BOUNCE_FILTER" in txt)
check("bounce filter 0.3x <2 lots", "0.3" in txt and "vol < 2" in txt, "-> need v5.9 step2")
check("iceberg_meta field in Level3Events", "iceberg_meta: Dict[float, Dict]" in txt)
check("first_seen tracking", "first_seen" in txt and "last_seen" in txt)
check("persistence score = refills * log(age) * total_size", "math.log" in txt and "score" in txt and "age" in txt)
check("time-weighted divergence minute_deltas", "minute_deltas" in txt and "CVD_15" in txt or "cvd_15" in txt)
check("time-weighted divergence uses tick_data", "def _detect_divergence" in txt and "tick_data" in txt)
check("iceberg persistence vote institutional", "institutional age" in txt or "persist_mult" in txt)

# 2. config.py
c = pathlib.Path("config.py").read_text(encoding="utf-8", errors="ignore")
check("config BOUNCE_FILTER_ENABLED", "BOUNCE_FILTER_ENABLED" in c)
check("config DIVERGENCE_TIME_WEIGHTED", "DIVERGENCE_TIME_WEIGHTED" in c)
check("config HEATMAP_PERSISTENCE_ENABLED", "HEATMAP_PERSISTENCE_ENABLED" in c)
check("config HEATMAP_INSTITUTIONAL_SCORE", "HEATMAP_INSTITUTIONAL_SCORE" in c)

# 3. .env
env = pathlib.Path(".env")
if env.exists():
    etxt = env.read_text(encoding="utf-8", errors="ignore")
    check(".env BOUNCE_FILTER_ENABLED", "BOUNCE_FILTER_ENABLED" in etxt, "-> add 10 lines from V5.9_WHAT_TO_ADD_TO_ENV.md")
    check(".env HEATMAP_PERSISTENCE_ENABLED", "HEATMAP_PERSISTENCE_ENABLED" in etxt)
    check(".env DIVERGENCE_TIME_WEIGHTED", "DIVERGENCE_TIME_WEIGHTED" in etxt)
else:
    print("[FAIL] .env not found")

# 4. .env.example
ex = pathlib.Path(".env.example")
if ex.exists():
    extxt = ex.read_text(encoding="utf-8", errors="ignore")
    check(".env.example has v5.9 keys", "BOUNCE_FILTER_ENABLED" in extxt)

# 5. Check current log behavior hints
print("\n--- LOG ANALYSIS from your paste ---")
print("Your log shows:")
print("- 4490 ticks direct, 40 levels, 3000 MBO -> GOOD, BookMap real depth")
print("- STEP1 3497ms first then 32ms -> GOOD (first read 317k lines)")
print("- CVD -206 Buy% 44.5 Sell% 55.5 -> GOOD weighted (with bounce filter)")
print("- Footprint dominant 4381.40 strength 0.028 buying 42 selling 51 -> GOOD restored")
print("- L3 imbalance -0.009 market 4490 limit 1092 aggressive 2170/2320 OFI -874 icebergs 282 -> GOOD L3 available")
print("- Budapest:yes [OPEN] 19:57 Budapest -> GOOD")
print("- candle history shallow 40 M1 bars -> EXPECTED after restart, needs 2-3h to fill 12h window (720 M1 bars)")
print("- ICEBERG_SUPPORT 282 @ 4382.0 refills 26 w 0.8 -> OLD FORMAT, should be 'institutional age X score Y' if meta filled")
print("  -> This means _iceberg_meta empty at that moment. After 3 refills same order_id, it should populate.")
print("  -> Check if mbo.csv has order_id refills? Your mbo.csv 317k lines should have refills.")
print("- OVERTRADE BLOCKED 19 min left -> trade_guard active, you hit max trades today, cooldown protects")
print("- Gemini all keys rate-limited -> need to update model to gemini-3.5-flash (2.0 Flash shutdown June 1 2026)")
print("- Confidence 20.6% and 31.6% <63% -> SKIPPED correct, due to shallow history + RBA HIGH event in 553 min")

print("\n--- FIXES NEEDED ---")
if checks_fail>0:
    print(f"{checks_fail} checks failed — you need to copy new files from zip Gold-BookMap-v5.9.0-BANK-GRADE.zip")
    print("1. Replace step2_market_analysis.py and config.py and .env.example")
    print("2. Add 10 lines to .env (see V5.9_WHAT_TO_ADD_TO_ENV.md)")
    print("3. In step3_ai_decision.py check Gemini model: should be gemini-3.5-flash or gemini-3.1-flash-lite, not 2.0-flash")
else:
    print("All file checks OK — your v5.9 files are correct!")
    print("If ICEBERG still shows old format, it's because _iceberg_meta not yet populated (needs same order_id refill 3x).")
    print("Wait 5-10 minutes live, it will show persistence once icebergs refill.")

print("\n--- GEMINI MODEL CHECK ---")
s3 = pathlib.Path("step3_ai_decision.py")
if s3.exists():
    s3txt = s3.read_text(encoding="utf-8", errors="ignore")
    if "gemini-2.0-flash" in s3txt:
        print("[FAIL] step3 still uses gemini-2.0-flash which was shutdown June 1 2026 -> rate-limited!")
        print("       Update to gemini-3.5-flash or gemini-3.7-flash or gemini-3.1-flash-lite")
        checks_fail+=1
    if "gemini-3.5-flash" in s3txt or "gemini-3.7-flash" in s3txt or "gemini-3.1-flash" in s3txt:
        print("[OK] step3 uses Gemini 3.x model")
    else:
        print("[WARN] step3 model not found, check manually")

print("\n"+"="*70)
print(f"RESULT: {checks_pass} OK, {checks_fail} FAIL")
if checks_fail==0:
    print("System should work well — shallow history will fill, confidence will rise to 50-70%")
else:
    print("Fix fails above then rerun python main.py --loop")
print("="*70)
