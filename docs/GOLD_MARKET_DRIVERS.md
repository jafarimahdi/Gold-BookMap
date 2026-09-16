# What Really Moves Gold — driver hierarchy & vote redesign

*Reference framework for restructuring the Signal Engine (step2). Based on how
gold actually trades: a macro asset with an institutional futures market
(COMEX GC/MGC) where price discovery happens in the order book.*

**Design principle: match each driver to its horizon.**
Macro drivers (slow) must not out-vote flow (fast) inside a 60-second cycle —
macro sets PERMISSION & BIAS, flow pulls the TRIGGER, structure picks the WHERE.

---

## GROUP 1 — MACRO DRIVERS (decide direction over days–weeks)

*Role in the engine: BIAS layer — not per-minute votes. They tilt, permit or
block directions, and scale confidence.*

| Rank | Driver | Why it moves gold | Engine role |
|---|---|---|---|
| 1 | **Real interest rates** (10Y TIPS / nominal − breakevens) | Gold pays no yield — real yield is its opportunity cost. THE dominant relationship: real yields ↓ = gold ↑ | Bias vote from **Δ real yields** (change, never level) |
| 2 | **US Dollar (DXY)** | Gold is priced in USD; sustained inverse link | Bias vote from **Δ DXY**, weight ↑ |
| 3 | **Fed policy path** (FOMC, dots, speeches) | Drives both #1 and #2; the event calendar of gold | News/event gates (already built: BLACKOUT/WARNING) |
| 4 | **Inflation data** (CPI, PCE) | Feeds real-yield expectations; biggest scheduled movers | Event gates |
| 5 | **Risk-off shocks** (VIX spike, equities/bond stress, geopolitics) | Safe-haven bids — violent but often transient | VIX **spike** vote (level-based VIX is noise) |
| 6 | **Central bank buying** (PBoC, EM CBs) | Structural floor since 2022; sets the bull backdrop | Via news sentiment only (no free data) |
| 7 | **ETF / investment flows** (GLD et al.) | Persistent demand shifts, daily granularity | Out of scope for now |
| 8 | **Positioning extremes (COT)** | Fuel for squeezes; weekly, lagging | Out of scope for now |

## GROUP 2 — ORDER FLOW & LIQUIDITY (moves price second-to-minute)

*Role in the engine: TRIGGER layer — the strongest per-cycle votes. This is
where our REAL data lives (NT bridge = L2 depth 20 levels + trades).*

| Rank | Driver | Why it moves gold | Engine role |
|---|---|---|---|
| 1 | **Aggressive flow** (delta / CVD **relative to volume**) | Who pays the spread to get in — the market's true intent | Strong vote — MUST be volume-normalized |
| 2 | **Book pressure** (OFI, depth imbalance) | The book leads price; adds/removes precede moves | Strongest always-alive vote (keep OFI 0.9) |
| 3 | **Absorption & sweeps** | Big passive orders eating aggression = reversal; liquidity runs = continuation & stop cascades | Reversal/continuation votes + NEW sweep detector |
| 4 | **Volume profile** (VWAP / POC / value area) | Institutional benchmark; acceptance vs rejection at VA edges tells you the day's character | Position votes + RANGE-regime fades |
| 5 | **Session liquidity rhythm** | Asia ranges → London expands (often THE daily move) → NY continues/reverses; rollover/lunch = thin traps | Session-aware weighting + NEW Asian-range/London-breakout structure |
| 6 | **CVD–price divergence** | Exhaustion/continuation tell | Strong vote but needs LONGER lookback (15, not 5) |
| 7 | **Footprint deltas** | Per-price confirmation | Medium vote (keep) |
| 8 | **L3 / MBO events** (icebergs, large pulls, ghost liquidity) | The true institutional fingerprint — gold's book is famous for games | ⚠️ **NOT in our feed** (NT exports no order events). Option: Databento MBO ~$33/mo |

## GROUP 3 — STRUCTURE & TECHNICALS (decides WHERE price reacts)

*Role in the engine: LOCATION layer — confirmation, entry/exit zones,
confidence multiplier. Rarely initiates.*

| Rank | Driver | Why it matters in gold | Engine role |
|---|---|---|---|
| 1 | **Higher-timeframe trend** (M15/H1/H4) | Don't fight the flow of a $2T market | Revive MTF from our OWN M1 data (M5/M15 aggregation) |
| 2 | **Key levels**: prior-day H/L, session H/L, Asian range, **round numbers** (…00/25/50) | Gold has memory; stops and options dealers cluster there | NEW level votes (round numbers are free to compute) |
| 3 | **Order blocks / supply-demand zones** | Where big players previously acted | Keep (exists) |
| 4 | **Momentum indicators** (RSI/MACD/ADX) | Useful on M15+; mostly noise on M1 | Compute on M5/M15, lower weight on M1 |
| 5 | **Volatility regime** (ATR / BB width / vol_rank) | Not direction — strategy selector: fade in balance, follow in expansion | Use the currently-UNUSED volatility_rank as a switch |

---

## The redesigned parliament (target influence per cycle)

| Layer | Share | How it acts |
|---|---|---|
| **FLOW (trigger)** | **~45%** | OFI 0.9, delta-normalized 0.8, absorption 0.5, sweeps 0.6 (new), divergence 0.8–1.0 (longer lookback), microprice 0.5, footprint 0.7, VWAP/POC position 1.0, book imbalance 0.7 |
| **STRUCTURE (location)** | **~30%** | MTF M5/M15 revived (0.6/0.8), session ranges + London breakout 0.8 (new), round numbers 0.5 (new), zones 0.6, M15 momentum 0.6, vol-regime switch |
| **MACRO (permission)** | **~25%** | Δ real yields ±0.8, Δ DXY ±0.6, VIX spike 0.4, news sentiment 0.4 (down from 0.7), RISK_OFF 0.5 — applied as BIAS: they tilt the score and scale confidence, and a strong macro-against vote can cap the score at NEUTRAL |

News calendar states (QUIET/WARNING/BLACKOUT) stay as GATES, exactly as now.

## Change list (priority order)

**v2 — fix the broken votes (small, no new data needed): — ✅ SHIPPED 2026-09-15**
1. ✅ delta → `buy% - sell%` (volume-relative, like OFI) — kills thin-market distortion
2. ✅ real yields: level-based −0.4-always → **Δ-based** ±0.8 bias — removes permanent bearish drag
3. ✅ DXY: correlation-conditional 0.2 → **Δ-based** ±0.6
4. ✅ divergence lookback 5 → 15 candles
5. ✅ news sentiment weight 0.7 → 0.4
6. ✅ MACD on M1 0.8 → 0.6 (noisy on 1-min gold)
7. ✅ microprice 0.3 → 0.5 (underrated fair-value measure)
Plus ✅ VIX-spike vote (vs 20-session median, +0.4) and ✅ the **macro-opposition
rule**: opposition shrinks the score up to −50%; extreme opposition (>0.8)
without flow confirmation (delta/OFI) caps the signal to NEUTRAL.

**Decisions taken 2026-09-15 (gold-desk rules):**
- **Round numbers = 3-state, flow-dependent** (v3): vote WITH direction on
  approach (magnet); fade AT the level without flow confirmation; vote with a
  break ONLY when delta/OFI confirm (stop-run). The number is the location,
  the flow is the judge.
- **Macro shrinks, doesn't kill**: gold makes multi-day counter-macro moves;
  a hard veto would block the best squeezes. Extreme opposition requires
  flow evidence to trade against.

**v3 — gold-native structure (from data we already have): — ✅ SHIPPED 2026-09-15**
8. ✅ revive MTF: M1 candles resampled to M5/M15/H1 (≥20 bars each) — M5 alive
   with a 2h window, M15 unlocks at 8h
9. ✅ round-number 3-state vote (x00=1.0/x50=0.8/x25=0.6/x10=0.4 tiers):
   magnet on approach (×0.3), follow-flow when tested (×0.6), continuation
   only on a flow-confirmed break (×0.6) / snapback on a failed one (×0.4)
10. ✅ Asian-range box (00:00–07:00 UTC from tick timestamps) + London-morning
    breakout vote (×0.8); widen `NT_WINDOW_SECONDS` 7200 → 28800 for the full
    range and the M15 timeframe
11. ✅ vol-regime switch: VWAP-z fade weighs 0.8 in compressed volatility,
    0.4 when expanding (breakouts run)

**Verified:** M5 aggregation unit-exact · round-tier mapping exact · break-with-
flow BUY 49.4 vs break-without-flow NEUTRAL 9.8 · London breakout +0.8 vote
only 08:00–12:00 UTC · MTF {'M5':'DOWN'} derived from own M1 · v2 macro
regression intact · full e2e green (round-number vote fired organically:
"round 4340 tested, flow down -> follow flow").

**decision:**
12. L3: accept dead (redistribute) OR Databento MBO feed (~$33/mo) for true
    order events — icebergs, pulls, ghost liquidity. Gold's L2 is famously
    gamed; MBO is the antidote.

## Honest constraints to remember

- Our feed = L2 depth (20 levels/side) + trades. **No order events** — every
  "LEVEL 3" line in the snapshots will stay zero without a new data source.
- Analysis runs on MGC futures (price discovery venue) and trades XAUUSD CFD —
  basis handled via ATR re-anchoring; signals remain futures-driven by design.
- Macro series are cached 15-min; Δ-based macro votes need ~24–48h of cached
  history to be meaningful — keep the cache warm (already persisted).
