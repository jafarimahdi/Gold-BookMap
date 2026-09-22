# TODO v5.9 — Bank-Grade Improvements (from desk manager review 2026-09-21)
# Status: v5.8.3 BUDAPEST live verified — 671 ticks direct, 40 bid/40 ask, 3000 MBO, BUY strength 27.6 conf 45% HOLD (correct)

## 🔴 P0 — Must fix next (from manager review)

### 1. Block entries until 60 M1 bars (not just note)
- [ ] **Current:** `candle history shallow (6 M1 bars) — ATR/MTF/order blocks/HTF POC limited until window fills` only notes, still allows BUY/SELL with estimated ATR 1.65
- [ ] **Fix:** In `session.py` or `SignalEngine.aggregate()`:
  ```python
  if len(close) < 60:
      return 0.0, "NEUTRAL", 0.0, ["candle history <60 bars — entries blocked until window fills"]
  ```
  Or in `main.py _safety_gates()`: if close <60 → block new entries, management continues
- [ ] **Why bank does it:** ATR 1.65 from 6 bars is 10x too small → SL 2*ATR = 3.3 pts vs real 30 pts → instant SL hit. Banks never trade on estimated vol.
- [ ] **Files:** `step2_market_analysis.py` + `session.py` + `main.py`
- [ ] **Test:** After restart, first 60 min should show BLOCKED, not BUY/SELL with low conf

### 2. Set AI_AS_VOTE=1 for deterministic execution, keep AI only for news BLACKOUT
- [ ] **Current:** AI final gate — Gemini 8483ms latency, 4 keys rate-limited, HOLD @45% blocks trade even when L3 strong (whale + iceberg + net flow BUY)
- [ ] **Fix:** In `.env`: `AI_AS_VOTE=1` → AI becomes vote weight 1.5 in SignalEngine, not final gate. Keep AI BLACKOUT veto:
  ```python
  if news.news_state == "BLACKOUT": return NEUTRAL (always)
  else: AI vote = +1 *1.5 if BUY, -1 *1.5 if SELL, 0 if HOLD
  ```
- [ ] **Why bank does it:** Prop desk uses AI as advisor, not execution owner. Deterministic L3 rule (whale+iceberg+flow) executes in 100ms, AI confirms. Your L3 fallback already does this after 10s — make it primary.
- [ ] **Files:** `config.py` AI_AS_VOTE, `step2_market_analysis.py` aggregate AI vote, `step3_ai_decision.py` keep BLACKOUT logic
- [ ] **Expected:** Latency 14.5s → 2s, London open +$41 not missed due to 503

### 3. Time-weight L3 OFI and CVD (last 5 min 2x)
- [ ] **Current:** CVD = sum(buy-sell) over whole 12h window, OFI L2/L3 cumulative over whole session. Old flow from 11h ago counts same as last 1 min.
- [ ] **Fix:** In `OrderFlowAnalyzer` + `OrderBookDepthAnalyzer`:
  ```python
  # Keep deque of (timestamp, delta, ofi) last 60 min
  # Weighted CVD = sum(delta * exp(-age/300)) — last 5 min weight 2x, 30 min 0.5x
  # Same for OFI
  recent_cvd = sum(d * w for d,w in last_5min) *2.0 + sum(d*w for older) *0.5
  ```
- [ ] **Why bank does it:** Gold is news-driven, flow from 08:00 irrelevant at 19:00 Budapest. Last 5 min aggressive flow predicts next 5 min.
- [ ] **Files:** `step2_market_analysis.py` OrderFlowAnalyzer, OrderBookDepthAnalyzer
- [ ] **Test:** Compare weighted vs unweighted on walk-forward — WR should improve 5%

### 4. Add session VWAP bands (VWAP ±1σ, ±2σ) as mean reversion votes
- [ ] **Current:** You have vwap_zscore = (price-vwap)/std, but only fade in RANGE when |z|>1.5. No explicit bands.
- [ ] **Fix:** In `VolumeProfileAnalyzer` already computes vwap_std. Add votes:
  ```python
  vwap_upper_1 = vwap + vwap_std
  vwap_lower_1 = vwap - vwap_std
  vwap_upper_2 = vwap + 2*vwap_std
  vwap_lower_2 = vwap - 2*vwap_std
  if price > vwap_upper_2: votes.append((-1.0, 1.0)) # 2σ stretched → mean reversion SELL
  if price < vwap_lower_2: votes.append((+1.0, 1.0)) # 2σ stretched → BUY
  if price between vwap and vwap_upper_1 and regime TREND: votes.append((+1.0, 0.6)) # trend continuation
  ```
- [ ] **Why bank does it:** VWAP bands are institutional TP levels. Day traders take profit at VWAP ±1σ, fade at ±2σ. You have POC/VAH/VAL but not VWAP bands.
- [ ] **Files:** `step2_market_analysis.py` VolumeProfileAnalyzer + SignalEngine
- [ ] **Test:** Should improve PF 2.76 → 3.5, reduce max DD

---

## 🟡 P1 — Microstructure deep dive (user asked for more info)

### 5. Bid-ask bounce filter: weight by size
- [x] **Current:** `analyze_tick_data()` counts every tick equally — FIXED v5.9: buy_volume += vol, sell_volume += vol. A 1-lot trade at bid flips CVD same as 50-lot at ask.
- [ ] **Bank explanation:**
  ```
  BookMap gives true side (is_bid), good. But 1-lot prints are often:
  - Odd lots, retail, noise
  - Bid-ask bounce: price alternates bid/ask without direction
  Example: 4381.6 BID 1 lot, 4381.8 ASK 1 lot, 4381.6 BID 1 lot → CVD  -1+1-1 = -1 but no real flow
  
  Bank filter:
  - Ignore trades size < 2 lots for CVD (or weight size^0.7)
  - Or: if trade size < 5 and price = best bid/ask, count 0.3x
  - Large trades >20 lots count 1.5x (institutional)
  
  Formula:
  weighted_delta = sum(vol * side_sign * min(1.5, max(0.3, vol/10)))
  ```
- [x] **Fix:** In `OrderFlowAnalyzer.analyze_tick_data()` DONE v5.9:
  ```python
  def _weighted_vol(vol):
      if vol < 2: return vol * 0.3
      if vol < 5: return vol * 0.7
      if vol > 20: return vol * 1.5
      return vol
  buy_volume += _weighted_vol(vol) if side BUY else ...
  ```
- [ ] **Why:** Your log CVD 35 delta 35 from 671 ticks — average 0.05 per tick. Many 1-lots. Weighted CVD would be ~50, more accurate.
- [ ] **Files:** `step2_market_analysis.py` OrderFlowAnalyzer
- [ ] **Expected:** WR +3%, PF +0.5, less whipsaw in RANGE

### 6. Volume delta divergence time-weighted — DONE v5.9
- [x] **Current:** `_detect_divergence(close, cvd, lookback=15)`:
  ```python
  price_roc = close[-1]/close[-15] -1
  if price_roc >0.001 and cvd<0: return -1 bearish
  if price_roc <-0.001 and cvd>0: return +1 bullish
  ```
  CVD is cumulative over whole window, not last 15 bars. So divergence uses stale CVD.
- [ ] **Bank explanation:**
  ```
  True divergence = price and volume disagree RECENTLY:
  - Price making lower low (4380.4 → 4378.2) but CVD making higher low (buy volume increasing)
  - Means sellers exhausted, buyers absorbing → bullish reversal
  
  Time-weighted:
  - Compute CVD_15 = sum(delta last 15 M1 bars)
  - Compute price_roc_15 = close[-1]/close[-15] -1
  - If price_roc_15 < -0.001 and CVD_15 > 0 and CVD_15 rising (CVD_15 > CVD_15_prev) → bullish +1
  - Weight by volume: if CVD_15 > 100 lots and price drop >0.2% → strong divergence 1.2x
  
  Example from your data:
  - Price 4381.7, VWAP 4380.92, POC 4380.83 → price above value
  - If price drops to 4380.0 but CVD last 15 bars = +80 (buying) → hidden buying, should BUY
  ```
- [x] **Fix:** In `analyze_market()` DONE v5.9:
  ```python
  # Build CVD per M1 bar from tick_data timestamps
  # Then divergence on last 15 bars only, volume-weighted
  recent_deltas = [delta for bar in last 15]
  cvd_15 = sum(recent_deltas)
  cvd_15_prev = sum(recent_deltas[-30:-15])
  if price_roc_15 < -0.001 and cvd_15 > 0 and cvd_15 > cvd_15_prev:
      divergence = +1.0 * min(1.2, abs(cvd_15)/100)
  ```
- [ ] **Files:** `step2_market_analysis.py` _detect_divergence + analyze_market
- [ ] **Expected:** Catches reversals early, WR +5% in RANGE regime

### 7. Liquidity heatmap persistence: track age of whale walls — DONE v5.9
- [x] **Current:** `Level3OrderBookAnalyzer` detects icebergs 321 levels but doesn't know how long they sat:
  ```
  ICEBERG_SUPPORT 321 @4381.1 refills 52
  ```
  52 refills could be 1 minute (algo) or 2 hours (institutional) — different meaning.
- [ ] **Bank explanation:**
  ```
  Liquidity heatmap = time x price x size:
  - X axis: time (08:00-23:00 Budapest)
  - Y axis: price (4370-4390)
  - Color: total resting size at that price over time
  
  Persistence matters:
  - Wall that sat 2 hours at 4381.1 with 52 refills = real institutional support (central bank, ETF)
    → BUY with high confidence, place limit 0.1 behind it (queue behind whale)
  - Wall that appeared 30 seconds ago, 3 refills, size 100 → algo spoof or short-term
    → Fade it, or wait for cancel (spoof invert)
  
  Age tracking:
  - order_map[order_id] = {price, size, add_ts, last_seen, refills, total_vol}
  - For each price level: first_seen = min(add_ts for orders at that price)
  - Age = now - first_seen
  - Persistence score = refills * log(age_seconds) * size
  
  Example:
  - Price 4381.1 first seen 17:00, now 19:23 = 2h23m = 8580 sec, refills 52, size 281 lots
    → score = 52 * log(8580) * 281 = 52*9.06*281 = 132k → strong institutional
  - Price 4382.7 first seen 19:20, now 19:23 = 3 min = 180 sec, refills 3, size 100
    → score = 3*5.19*100 = 1557 → weak, ignore or fade
  ```
- [x] **Fix:** In `Level3OrderBookAnalyzer` DONE v5.9:
  ```python
  # Add to _iceberg_levels: price -> {refills, first_seen, last_seen, total_size, score}
  # In process_order_event NEW:
  if order_id in order_map and same price:
      order_map[order_id][refills] +=1
      order_map[order_id][last_seen] = now
  else:
      order_map[order_id] = {price, size, first_seen: now, ...}
  
  # In analyze():
  for price, info in iceberg_levels:
      age = now - info[first_seen]
      score = info[refills] * math.log(max(1,age)) * info[total_size]
      info[score] = score
      # Vote weight = 0.8 + min(1.2, score/50000) → 0.8-2.0x
  ```
- [ ] **Files:** `step2_market_analysis.py` Level3OrderBookAnalyzer + SignalEngine votes
- [ ] **Expected:** Distinguish real institutional support (2h wall) vs algo noise (30s wall), WR +8% in TREND, avoids spoof traps
- [ ] **Visualization (future):** Add `data/heatmap.json` = {price: {size, age, score}} for BookMap heatmap overlay

---

## 📊 Progress

- v5.8.3 DONE: Budapest hours + footprint restored + HTF POC voting + recency fix + shallow history fix (64 MB catchup)
- v5.9 TODO: 4 P0 + 3 P1 = 7 items above
- Next: Start with #1 Block entries <60 bars (safety), then #2 AI_AS_VOTE=1 (latency)

Last updated: 2026-09-21 19:30 Budapest by Agent
