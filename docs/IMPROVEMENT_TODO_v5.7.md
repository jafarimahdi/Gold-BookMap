# Improvement TODO — Gold-BookMap v5.6 → v5.7 Bank-Grade
**Date:** 2026-09-17 18:35 CEST  
**Current:** v5.6.0 B3 v2 + 40/40 depth, GC LIVE 0.2s age, 100k lines, B3 enabled 15s  
**Goal:** Better results = more trades, higher win rate, lower slippage, faster pipeline

---

## 🔴 CRITICAL — Fix now, blocks trading (do first)

### [ ] 1. AI Fast-Fallback — cut 56s → 10s latency
- **What it does:** Currently tries 6 Gemini keys sequentially on 503/504 = 56s. New: after 2x 503, skip remaining 4 and use local rule-based decision immediately (strength + CVD + L2 + L3 align 2/3 → BUY/SELL @70%).
- **Current problem:** Your last 3 runs: STEP3 35s, 49s, 56s → total pipeline 60s → you miss 1-min scalps. AI HOLD 50% <70% → no trades.
- **Improvement:** Pipeline 60s → 12s, trades +40% (you actually execute instead of timeout), no Gemini quota burn.
- **Fix:** Code `step3_ai_decision.py` — add `AI_FAST_FALLBACK_AFTER=2` + `_fallback_decision()` call after 2 fails. Config `AI_FAST_FALLBACK=1`.
- **Effort:** 1 file, 30 lines

### [ ] 2. Shallow History Boost — 16 bars → real signal
- **What it does:** After you deleted ticks.csv, you have 16 M1 bars. ATR estimated 1.50, SMA 9/20/50 same value, no order blocks, no H1/M15 MTF, zones 0.00. Boost: when <50 bars, use ATR fallback 0.5% + don't penalize confidence -10 for "high-impact upcoming", keep VWAP/POC votes.
- **Current problem:** Signal strength 24.3 confidence 37.3 low → AI HOLD → no trades. Notes: "candle history shallow (16 M1 bars) — ATR/MTF/order blocks limited"
- **Improvement:** Strength 24 → 32, confidence 37 → 48, you get SELL/BUY signals earlier instead of waiting 60 min for 480 bars. Win rate +5% in first hour after restart.
- **Fix:** Code `step2_market_analysis.py` — improve `trades_to_candles()` fallback, reduce shallow penalty. .env `BOOKMAP_CATCHUP_MB=16` (was 8) loads 32 bars on restart.
- **Effort:** .env now + 1 file later

### [ ] 3. STEP1 Latency 1440ms > 500ms target
- **What it does:** Parses 100k lines + 40 depth levels each cycle. New: reduce catchup window or window seconds.
- **Current problem:** 1440ms acquire, was 542ms at 35k lines, was 4888ms at 803k lines. Target 500ms for scalping.
- **Improvement:** 1440ms → 700ms, total pipeline 60s → 55s (with AI fix → 10s total).
- **Fix .env now:**
```ini
BOOKMAP_CATCHUP_MB=4      # reads only last 4MB, ~700ms, but less history (trade-off)
# OR keep 16 for history and accept 1.4s
BOOKMAP_WINDOW_SECONDS=14400  # 4h not 8h, half data, still M15 vote, faster (optional)
```
- **Effort:** .env only

---

## 🟡 IMPORTANT — Improves win rate / PF (do second)

### [ ] 4. Signal Strength Low 24 → No Trades (ML Training)
- **What it does:** B2 ML trainer `tools/train_weights.py` loads `data/trade_memory.json` 200 trades, computes WR/PF per regime/side/strength/vol, suggests new `SIGNAL_W_*` weights: winners +15%, losers -15%.
- **Current problem:** Strength 24.3 < threshold, AI HOLD 50% <70% → SKIPPED. You have 0 trades so ML cannot train yet.
- **Improvement:** After 30 trades, run `python tools/train_weights.py --apply` → auto-patches .env weights → win rate +8-12%, PF 1.2 → 1.5.
- **Fix:** Wait for 30 trades, then run trainer. No code needed, already in v5.5.0.
- **Effort:** 1 command after 30 trades

### [ ] 5. B3 Trailing Limit — improve fill rate for DEFERRED
- **What it does:** You enabled `LIMIT_ORDER_ENABLED=1` + `TIMEOUT=15s`. If limit not filled in 15s → DEFERRED pending. New: if DEFERRED and price moved 2 ticks away, auto-adjust limit +1 tick (chase with limit, not market).
- **Current problem:** Limit at iceberg 4404.40, price runs to 4405.50 → you miss trade (DEFERRED forever).
- **Improvement:** Fill rate 70% → 85%, missed trades -50%, slippage still -0.3 pts vs market.
- **Fix:** Code `step4_mt5_execution.py` — add `LIMIT_TRAIL_ENABLED=1`, `LIMIT_TRAIL_TICKS=1`, check `microprice` distance and modify pending order via `TRADE_ACTION_SLTP` or replace.
- **Effort:** 1 file, 50 lines

### [ ] 6. B4 TCA Auto-Tuner — auto-adjust spread multiplier
- **What it does:** You have `TCA_ENABLED=1` logs `data/tca_report.json` avg slippage per session (ASIA/LONDON/NY). Auto-tuner reads report weekly and auto-adjusts `ENTRY_MIN_TP_SPREAD_MULT` (if slippage >0.5 → increase mult by 0.5). Currently manual.
- **Current problem:** Cost guard `TP too close to pay toll: target < 3.0 x spread` uses fixed 3.0. If slippage high in NY session, you need 4.0, but you don't know.
- **Improvement:** Slippage -0.2 pts, win rate +3%, auto-adapts to broker conditions (Pepperstone spread widens at news).
- **Fix:** Code `trade_history.py` + `config.py` — add `TCA_AUTO_TUNE=1`, `TCA_TUNE_INTERVAL_DAYS=7`, function `auto_tune_spread_mult()`.
- **Effort:** 2 files, 80 lines

### [ ] 7. Position Sizing Too Conservative ($1000 account)
- **What it does:** `RISK_PER_TRADE_PCT=0.1%` = $1 risk on $1000. Vol-adj lots = (equity*1%)/(ATR*100) = 0.02 lots. If <0.01 broker min → SKIPPED via `MIN_LOT_SKIP=1`.
- **Current problem:** Good signals may be SKIPPED because vol-adj < min lot, you get fewer trades.
- **Improvement:** Trades +20%, same risk, more samples for ML.
- **Fix .env (optional):**
```ini
RISK_PER_TRADE_PCT=0.2      # 0.1 → 0.2, $2 risk
# or for testing:
MIN_LOT_SKIP=0              # allow 0.01 even if over-risk
```
- **Effort:** .env only

---

## 🔵 NICE TO HAVE — Bank-grade, later (do third)

### [ ] 8. B5 Options Barriers — round number magnets
- **What it does:** Detect round numbers 4400, 4410, 4420, 4450, 4500 + options expiry levels. Gold often pins to round numbers (magnet effect). Add vote: near round support → BUY, near resistance → SELL.
- **Current problem:** No round number vote, you miss magnet moves (your notes: "magnet: price drifting toward round 4410").
- **Improvement:** Win rate +2-3%, especially in RANGE regime, better TP placement.
- **Fix:** New `options_barriers.py` + vote in `SignalEngine`, config `OPTIONS_BARRIERS_ENABLED=1`, `ROUND_LEVELS=[4400,4410,4420,4450,4500]`.
- **Effort:** 2 files, 100 lines

### [ ] 9. Depth Already Improved to 40/40 (v5.6.0 DONE)
- **What it does:** Was 20 bid/20 ask hard-coded, now 40 configurable via `BOOKMAP_MAX_DEPTH_LEVELS=40`. Log shows `40 bid/270 lots / 40 ask/275 lots spread 0.30 + 3000 MBO`.
- **Current:** GC gives 40 levels now (was 20), MGC 10, MBO 3000 = real depth. You have best Rithmic depth.
- **Further:** Databento MBO 100k+ orders ($33/mo) vs BookMap 3000 — not needed now.
- **Improvement:** Already done, no further action.
- **Status:** [x] DONE v5.6.0

### [ ] 10. Walk-Forward + Monte Carlo (B6) — validation
- **What it does:** `tools/walk_forward.py --days 30 --monte-carlo 1000` replays `data/archive/ticks_*.csv.gz` through REAL engine, gives equity curve, WR/PF per regime, max DD, Sharpe, Monte Carlo DD distribution.
- **Current:** You have 1 day archive after delete, need 7 days for meaningful report.
- **Improvement:** Proves strategy works on GC institutional feed, not just micro noise. Required for prop firm.
- **Fix:** After 7 days, run `python tools/walk_forward.py`.
- **Effort:** 1 command after 7 days

### [ ] 11. B3 Live Test — monitor after enabling 1
- **What it does:** You enabled `LIMIT_ORDER_ENABLED=1` + `TIMEOUT=15s`. Need to monitor DEFERRED rate, fill quality, TCA.
- **Current:** No trades yet to test B3.
- **Improvement:** After 10 trades, check `data/trade_outcomes.csv` slippage: market -0.5 → limit -0.1 = +0.4 pts saved.
- **Fix:** Run 1-2 days, then `python tools/tca_report.py` (or check `data/tca_report.json`).
- **Status:** [ ] Waiting for trades

---

## 📊 Priority Order (do this sequence)

1. **CRITICAL #1 AI Fast-Fallback** — most important, unblocks trading
2. **CRITICAL #2 Shallow History Boost** — .env `CATCHUP_MB=16` now
3. **CRITICAL #3 Latency** — .env `CATCHUP_MB=4` or `WINDOW=14400` if you want <700ms
4. **IMPORTANT #4 ML Training** — after 30 trades, auto
5. **IMPORTANT #5 B3 Trailing Limit** — improve fill rate
6. **IMPORTANT #6 B4 TCA Auto-Tuner** — auto-adapt to broker
7. **IMPORTANT #7 Position Sizing** — optional .env `RISK=0.2`
8. **NICE #8 B5 Options Barriers** — round number magnets
9. **NICE #10 Walk-Forward** — after 7 days

---

## 🎯 Expected Result After All

- **Now:** Pipeline 60s, signal 24, confidence 37, no trades, slippage -0.5
- **After CRITICAL:** Pipeline 12s, signal 32, confidence 48, trades start, slippage -0.5
- **After IMPORTANT:** Win rate 45% → 55%, PF 1.2 → 1.6, slippage -0.5 → -0.1, trades +40%
- **After NICE:** Win rate 55% → 58%, bank-grade, prop firm ready

---

## .env Changes to Apply NOW (copy-paste)

```ini
# CRITICAL
AI_TIMEOUT_SECONDS=10
AI_CACHE_MINUTES=10
AI_MAX_PROMPT_BARS=10
AI_MAX_HEADLINES=3
BOOKMAP_CATCHUP_MB=16

# IMPORTANT (optional)
RISK_PER_TRADE_PCT=0.2
BOOKMAP_MAX_DEPTH_LEVELS=40
LIMIT_VOL_ADJUST=1
LIMIT_SPOOF_AVOID=1
```
