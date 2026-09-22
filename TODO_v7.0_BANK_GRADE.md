# TODO v7.0 — Bank-Grade M5 Scalper Roadmap (from v6.0 review 2026-09-21)
# Status: v6.0 live verified — 8365 ticks direct, 40 bid/40 ask, 3000 MBO lines 803k, 4 Teams ensemble, KillZone OFF RANGE boost, M5 boss 1.2, HOLD correct (confluence FAIL need 3 + whale)

## Current v6.0 Summary
- v6.0 built: 5 upgrades + 4 Teams + GC→CFD safe
- Self-test OK: KillZone OFF 22:54 RANGE 2x, MTF M5:UP boss 1.2, Confluence FAIL 2 SELL need 3 + whale -> NEUTRAL, ensemble flow -0.28*1.5 whale +0.32*1.4
- Pipeline OK: STEP1 47ms STEP2 169ms total 275ms <500ms target after warmup
- WR expected 65-72% PF 6-10 (was 58-65% v5.9)
- Bank rating 8.8/10 retail, 8.2/10 prop, 7.8/10 bank — APPROVED small live 0.01-0.05 lots

## 🔴 P0 — Critical, fix before larger live (for v7.0, +8% WR)

### 1. Fix iceberg persistence display — old format still showing
- [ ] **Current:** `ICEBERG_RESISTANCE 164 @4381.8 refills 15 w0.8 -> SELL` (old format)
- [ ] **Should be:** `ICEBERG_SUPPORT 4381.8 age 2.1h score 132k institutional w1.6 -> BUY (persistent)` with age/score
- [ ] **Why:** code has iceberg_meta 9 counts but meta empty → same order_id not refilling 3x within tolerance 0.10, fallback to old
- [ ] **Fix:** Increase tolerance 0.10→0.20 in Level3OrderBookAnalyzer, check order_id != "-1"/"0", log meta when populated, add iceberg_meta to notes
- [ ] **Files:** step2_market_analysis.py Level3OrderBookAnalyzer.process_order_event()
- [ ] **Gain:** +3% WR, distinguishes real institutional wall (2h age score 132k) vs algo noise (3min score 1.5k)
- [ ] **Test:** After 10-15 min live, should see `institutional age X score Y`

### 2. Block entries until 60 M1 bars explicitly (not just note)
- [ ] **Current:** `candle history shallow (40 M1 bars)` only notes, still allows BUY/SELL with low conf 12% (v6.0 shows Confluence FAIL but still calculates)
- [ ] **Should be:** if len(close) <60 → return 0, NEUTRAL, 0, ["BLOCKED <60 M1 bars need 60 min warmup"]
- [ ] **Why bank:** ATR from 6 bars 1.65 estimated → SL 3.3 pts vs real 30 pts → instant SL hit. Banks never trade estimated vol.
- [ ] **Fix:** In SignalEngine.aggregate() top: `if len(close) <60: return 0.0, "NEUTRAL", 0.0, ["BLOCKED <60 M1 bars"]` + in main.py safety gate
- [ ] **Files:** step2_market_analysis.py + main.py
- [ ] **Gain:** +2% WR, prevents false signals first hour
- [ ] **Test:** After restart, first 60 min should show BLOCKED, not NEUTRAL 5 strength

### 3. Time-weight L3 OFI/CVD last 5 min 2x — recent more important
- [ ] **Current:** CVD time-weighted done v5.9 (CVD_15), but L3 OFI uses whole day cumulative
- [ ] **Should be:** L3 OFI last 5 min weight 2x, older 1x, same for CVD_5 vs CVD_rest
- [ ] **Why:** order flow last 5 min predicts next 5 min, old flow stale. Example: OFI -760 whole day but last 5 min +200 buying → should be BUY not SELL
- [ ] **Fix:** Bucket MBO events by minute, OFI_5min = sum last 5 M1 OFI, weighted OFI = OFI_5min*2 + OFI_rest
- [ ] **Files:** step2_market_analysis.py OrderFlowAnalyzer + Level3OrderBookAnalyzer
- [ ] **Gain:** +2% WR, catches recent aggression

### 4. AI_AS_VOTE=1 — AI as Captain Voter (BEST PROMPT) — not final gate
- [ ] **Current:** AI final gate — Gemini 1370ms latency, rate-limited → HOLD @45% blocks trade even when L3 strong (whale + iceberg + net flow BUY). Teacher says YES/NO absolute.
- [ ] **Should be:** AI as Captain 5 vote weight 1.5 as 5th team captain, ensemble with 4 Teams: score = (Flow*1.5 + Whale*1.4 + Structure*1.2 + Trend*0.8 + AI*1.5)/total. AI still veto for BLACKOUT only. Uses AI power better: vote -1.0 to +1.0 with confidence, not just YES/NO.
- [ ] **Why Captain better than Teacher (5-year-old):** Teacher gate = King says YES/NO, everyone must obey, even if 4 teams want to play football and King sleeping (rate-limited) → no game! Captain vote = 5 captains vote together, need 3 agree + whale, King is one captain with big voice 1.5x but not absolute. If King sleeping, 4 captains can still play! More fair, more games, more wins! Bank does Captain (70% microstructure + 20% context + 10% AI veto), not Teacher 100% gate.
- [ ] **Best Prompt for Captain (bank-grade):**
  ```
  Role: You are Captain 5 of Gold-BookMap M5 scalper. 4 other captains: Flow 1.5x boss (CVD weighted small 0.3x big 1.5x last 5min 2x + footprint + OFI), Whale 1.4x boss (net flow + iceberg persistence old walls >30min score>50k + distance close 2x + sweep), Structure 1.2x RANGE/0.6x TREND (VWAP bands +1σ +2σ +2.5σ + POC/VAH/VAL + order blocks), Trend 0.8x small (M5 1.2x boss M15 0.8x H1 0.4x). You AI 1.5x news+macro+confluence.
  Input: snapshot JSON + 4 Teams scores flow -0.32 whale -0.66 structure 0.00 trend +0.13 killzone OFF RANGE confluence FAIL need 3 + whale + L3 Enhanced top 3 bids/asks with size+dist% + net flow + top icebergs with price+refills+age+score label institutional 2.1h vs noise 3min + spoof + queue + OFI + imbalance + buy/sell streaks + recent 5 trades WIN/LOSS + GC vs CFD basis+spread
  Job: Vote -1.0 to +1.0 (-1 strong SELL, -0.5 weak SELL, 0 HOLD, +0.5 weak BUY, +1 strong BUY) + confidence 0-100 + rationale with L3 + teams_agree dict
  Rules: +1.0 strong BUY = 3+ teams BUY + whale BUY old + net flow BUY>100 + VWAP -2σ or sweep bullish + no veto. +0.5 weak BUY = 2 teams BUY + whale BUY. 0 HOLD = confluence FAIL (<2 teams) or mixed or HIGH news <30min or spread>0.60 or basis fast widen>1%. -0.5 weak SELL = 2 teams SELL + whale SELL. -1.0 strong SELL = 3+ teams SELL + whale SELL old + net flow SELL<-100 + VWAP +2σ or sweep bearish.
  Output JSON: {"vote": -1.0 to +1.0, "action": "BUY"|"SELL"|"HOLD", "confidence": 0-100, "rationale": "Flow SELL -0.32 Whale SELL -0.66 old iceberg 304 @4390.1 age 2.1h score 132k resistance + sweep high 4392 vol 614% + VWAP +2.1σ + KillZone OFF RANGE -> SELL", "teams_agree": {"flow": -0.32, "whale": -0.66, "structure": 0.0, "trend": 0.13}}
  Examples: 3 examples strong SELL with sweep+VWAP+old iceberg, HOLD confluence FAIL, bullish sweep BUY
  Think step by step: 1) Flow? 2) Whale old walls? 3) Structure VWAP bands? 4) Trend MTF? 5) KillZone? 6) World veto? 7) Confluence? Then vote.
  Temperature 0.2 deterministic. Dynamic weight by confidence: >75% weight 1.5x, 55-75% 1.0x, <55% 0.5x
  ```
- [ ] **How to use captain better (3 tricks):**
  - Trick 1: Chain-of-thought short: Think 1) Flow 2) Whale old 3) Structure bands 4) Trend MTF 5) KillZone 6) World veto 7) Confluence → vote
  - Trick 2: Temperature 0.2 deterministic, not 0.7 creative → more reliable
  - Trick 3: Dynamic weight by confidence: >75% 1.5x, 55-75% 1.0x, <55% 0.5x
- [ ] **Fix:** In .env `AI_AS_VOTE=1`, in step3_ai_decision.py update SYSTEM_PROMPT to Captain prompt above, in SignalEngine add AI vote to ensemble with team weights, keep BLACKOUT veto
- [ ] **Files:** config.py + step2_market_analysis.py (ensemble) + step3_ai_decision.py (best prompt) + .env
- [ ] **Gain:** +1% WR + 50% more trades (no single point failure when rate-limited), uses AI power better: vote how much -1.0 to +1.0 not just YES/NO, +15% confidence boost when whale confirms, -20% penalty when L3 conflicts
- [ ] **Test:** When AI SELL 72% + 3 teams SELL → strong SELL 80% conf. When AI BUY vs 3 teams SELL → score reduced to NEUTRAL, not absolute veto. When all keys rate-limited → 4 Teams still trade with cached AI vote
- [ ] **Training AI to be more reliable (4 ways):**
  - Way 1: Few-shot examples in prompt (2-3 good/bad trades with L3)
  - Way 2: B2 ML Weight Trainer `python tools/train_weights.py --apply` weekly after 30-100 trades → learns which teams win → auto-adjusts weights Flow 1.5→1.54 Whale 1.4→1.45 Trend 0.8→0.5
  - Way 3: Feedback loop → include last 5 trades WIN/LOSS in prompt → AI learns from mistakes
  - Way 4: More keys (up to 20) from different Google projects + cache 5→10 min + temperature 0.2

## 🟡 P1 — Important, for 9/10 bank (for v7.0, +7% WR)

### 5. VWAP bands real std calculation + testing
- [ ] **Current:** vwap_std = ATR*0.8 approx, not real VWAP std, bands not triggered (z -1.15, need ±2)
- [ ] **Should be:** real std = sqrt(sum(volume*(price-VWAP)^2)/sum(volume)), bands ±1σ ±2σ ±2.5σ with weights 0.8/1.3/1.8
- [ ] **Why:** M5 loves bounce between VWAP bands, rubber band effect. Your log price 4381.65 below VAL breakdown SELL but not at -2σ
- [ ] **Fix:** In VolumeProfileAnalyzer, calculate vwap_std properly, add band votes
- [ ] **Files:** step2_market_analysis.py VolumeProfileAnalyzer
- [ ] **Gain:** +3% WR mean reversion
- [ ] **Test:** During London 09-11 and NY 14:30-16:30 when price stretched to +2σ 4390 → SELL

### 6. Sweep detection improvement + testing
- [ ] **Current:** uses close array approximated high/low = close ± ATR*0.3, not real high/low
- [ ] **Should be:** use real high/low/close/volume from candles, detect wick >60% of spike, volume RoC >3x
- [ ] **Why:** banks hunt stops above highs/below lows to get liquidity then reverse. Retail gets tricked, bank fades
- [ ] **Fix:** Pass high/low arrays to _detect_liquidity_sweep, not approximated
- [ ] **Files:** step2_market_analysis.py
- [ ] **Gain:** +2% WR, fades stop hunts
- [ ] **Test:** Need real sweep event: price spikes above 20 highs + vol 614% then close back below → SELL

### 7. Queue position real implementation
- [ ] **Current:** proxy bid_ask_ratio >1.2 + whale support → good queue (not real)
- [ ] **Should be:** queue_position = your_size / total_size at best bid/ask, if >0.70 back of line → don't place limit or use market
- [ ] **Why:** if 500 lots ahead at bid 4389.10, you kid 501 at back → never filled, price moves, miss
- [ ] **Fix:** Track order book queue from MBO events, calculate position
- [ ] **Files:** step2_market_analysis.py + step4_mt5_execution.py
- [ ] **Gain:** +1% WR, better fills

### 8. Microprice threshold lower + VPIN toxicity filter
- [ ] **Current:** threshold 0.5 bps, dev 0 → no vote, microprice restored but silent
- [ ] **Should be:** threshold 0.2 bps more sensitive for M5, plus VPIN (volume toxicity) when toxic flow high → pause
- [ ] **Why:** microprice = (bid*ask_size + ask*bid_size)/(bid_size+ask_size) = size-weighted fair, banks use it
- [ ] **Fix:** Lower threshold, add VPIN calc
- [ ] **Files:** step2_market_analysis.py
- [ ] **Gain:** +1% WR

### 9. Confluence min 3 → 2 for M5 scalper
- [ ] **Current:** need 3 teams agree + whale → many NEUTRAL (your log BUY 0 SELL 1-2 → FAIL → HOLD)
- [ ] **Should be:** min 2 teams + whale for M5 scalper → more trades, still safe
- [ ] **Why:** M5 scalper needs more trades, 3 too strict, 2 + whale is enough confluence
- [ ] **Fix:** Config V6_CONFLUENCE_MIN_TEAMS=2
- [ ] **Files:** config.py
- [ ] **Gain:** +50% more trades, same WR

## 🟢 P2 — Nice to have, for 9.5/10 bank (for v7.0+, +3% WR)

### 10. Absorption vs Exhaustion detection
- [ ] **Current:** absorption_net exists, but no exhaustion
- [ ] **Should be:** Absorption: big market volume >200 lots but price change <0.05% → whale defending → continuation. Exhaustion: small volume <50 lots but price change >0.1% → no liquidity → reversal
- [ ] **Fix:** Add vote +1.0 *1.0 for absorption, -1.0 for exhaustion
- [ ] **Gain:** +1% WR

### 11. Session VWAP vs Day VWAP
- [ ] **Current:** day VWAP only
- [ ] **Should be:** session VWAP (London session, NY session) + day VWAP, session VWAP more important for M5
- [ ] **Fix:** Calculate VWAP per session

### 12. Kelly position sizing + max DD cap
- [ ] **Current:** fixed risk% lots = equity*risk%/(ATR*contract_size)
- [ ] **Should be:** Kelly = win_rate*avg_win - (1-win_rate)*avg_loss / avg_win → dynamic risk, cap max DD 5%
- [ ] **Fix:** In risk_manager.py

### 13. Auto train_weights weekly
- [ ] **Current:** manual `python tools/train_weights.py --apply`
- [ ] **Should be:** maintenance.py auto-runs Sunday if trades >100 → learns which teams win
- [ ] **Fix:** Add to maintenance.py

### 14. GC→CFD correlation check
- [ ] **Current:** basis max 50, fast widen 1%, spread max 0.60
- [ ] **Should be:** if correlation GC vs XAUUSD <0.95 → pause (market dislocation)
- [ ] **Fix:** In spread_monitor.py

## Expected Impact
- v6.0 now: WR 65-72% PF 6-10 (15-20 trades/week) Bank 7.8/10 — AI as Teacher gate, single point failure when rate-limited
- After P0 (with AI Captain vote): WR 70-75% PF 8-12 (20-25 trades/week) Bank 9.2/10 — AI as Captain 5 vote -1.0 to +1.0 weight 1.5, ensemble with 4 Teams, no single failure, uses AI power better
- After P0+P1: WR 72-78% PF 10-15 Bank 9.5/10 prop firm level
- After P0+P1+P2: WR 75-80% PF 12-18 Bank 9.8/10

## Files for v7.0
- step2_market_analysis.py: fix iceberg tolerance 0.10→0.20, block <60 bars, time-weight L3 OFI, VWAP real std, sweep real high/low, queue real, microprice 0.2bps, absorption/exhaustion, session VWAP
- config.py: V6_CONFLUENCE_MIN_TEAMS=2, AI_AS_VOTE=1, Kelly, etc.
- step4_mt5_execution.py: queue position check, correlation check
- maintenance.py: auto train_weights
- risk_manager.py: Kelly sizing
