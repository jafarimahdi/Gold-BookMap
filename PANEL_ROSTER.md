# The panel — your 33 judges and the AI

**Saved 2026-09-24.** Generated from the robot's own `JUDGE_DEFAULT_W` and
`JUDGE_HORIZON` tables, so it matches what the code actually grades with.

- **weight** = how loudly this judge speaks when the votes are added up
- **clock** = how far ahead it is graded (a scalper judged on 15 min, a macro judge on 120)

---


## Footprint

| name | weight | clock | what it watches |
|---|---|---|---|
| `absorption` | 0.60 | 15 min | Heavy selling that does NOT move price down = someone big is absorbing it. |
| `cvd_divergence` | 0.60 | 60 min | Price makes a new high but delta does not - the move is not supported. |
| `cvd_momentum` | 0.50 | 30 min | Is cumulative delta accelerating or fading? |
| `delta_pressure` | 0.60 | 30 min | Sustained one-sided aggression building up. |
| `footprint_delta` | 1.00 | 15 min | At each price, were buyers or sellers the aggressors? The core order-flow read. |
| `footprint_levels` | 0.40 | 15 min | Counts how many price levels lean buy vs sell - breadth, not size. |
| `volume_roc` | 0.40 | 30 min | Sudden change in traded volume - something woke up. |

## Order book

| name | weight | clock | what it watches |
|---|---|---|---|
| `l3_aggr_limit` | 0.70 | 15 min | Aggressive market orders vs passive limit orders. |
| `l3_imbalance` | 0.80 | 15 min | More size resting on one side of the book than the other. |
| `l3_large_ofi` | 0.60 | 15 min | The same, but only counting large orders. |
| `l3_net_flow` | 1.50 | 15 min | Net aggressive buying minus selling. Your heaviest judge (1.5). |
| `l3_ofi_streak` | 1.00 | 15 min | Order-flow imbalance pushing the same way several prints in a row. |
| `microprice` | 0.60 | 10 min | The true mid, weighted by book size - leans toward the next tick. |
| `queue_pos` | 0.50 | 15 min | How deep the queue is at the best bid/ask. |

## Hidden size

| name | weight | clock | what it watches |
|---|---|---|---|
| `iceberg` | 1.00 | 30 min | A resting order that keeps refilling - someone hiding a big position. |
| `iceberg_legacy` | 0.60 | 30 min | The older iceberg rule, kept for comparison. |
| `iceberg_noise` **[OFF]** | 0.00 | — | OFF (weight 0.0) - was too noisy and is deliberately silenced. |
| `spoof_invert` | 0.60 | 30 min | A big order that vanishes before being hit - fake pressure, so fade it. |
| `spoof_invert_loose` | 0.40 | 30 min | The same idea with a looser threshold. |
| `sweep` | 0.90 | 30 min | Someone cleared several price levels in one go - urgency. |
| `whale_walls` | 1.40 | 30 min | A very large resting order acting as a wall. Weight 1.4. |

## Structure

| name | weight | clock | what it watches |
|---|---|---|---|
| `htf_poc` | 0.70 | 120 min | The same, but from higher timeframes. Slowest judge: 120 minutes. |
| `mtf` | 0.50 | 60 min | Multi-timeframe agreement: H1, M15 and M5 pointing the same way. |
| `poc_day` | 0.50 | 60 min | Today's point of control - the price with the most traded volume. |
| `supply_demand` | 0.60 | 60 min | Known supply and demand zones from earlier trading. |
| `value_area` | 0.50 | 60 min | Is price inside or outside the day's value area? |
| `vwap_bands` | 0.80 | 60 min | How far price has stretched from VWAP - snap-back or breakout. |
| `vwap_trend` | 0.60 | 60 min | Price above or below VWAP, and which way VWAP is sloping. |
| `vwap_zscore` | 0.50 | 60 min | The same stretch, measured in standard deviations. |

## Macro

| name | weight | clock | what it watches |
|---|---|---|---|
| `macro_dxy` | 0.60 | 120 min | The dollar index. A stronger dollar usually presses gold down. |
| `macro_risk` | 0.50 | 120 min | Overall risk-on vs risk-off mood. |
| `macro_vix` | 0.40 | 120 min | Volatility/fear gauge. |
| `macro_yield` | 0.80 | 120 min | Bond yields. Rising yields usually press gold down. |
| `news_sentiment` | 1.00 | 120 min | Tone of the incoming news feed. |

## Referee

| name | weight | clock | what it watches |
|---|---|---|---|
| `THE AI (Gemini)` | — | — | Asked only when the panel's signal is strong enough. Today it is a GATE, not a vote: whatever it says is final, and it can refuse a trade all 33 judges wanted. |

---

**Total: 33 active judges + the AI.** `iceberg_noise` is present but switched
off (weight 0.00) because it was too noisy.

## How they work together

1. All 33 judges vote every cycle. Each vote is multiplied by that judge's weight.
2. The weighted votes combine into a direction (BUY/SELL/NEUTRAL) and a strength.
3. If the strength clears `AI_MIN_SIGNAL_STRENGTH` (6), the AI is asked.
4. **The AI's answer is final.** It can refuse a trade all 33 judges wanted.
   That is item 10 (`AI_AS_VOTE`) — still open, still your decision.

Measured 2026-09-24: the AI was asked on roughly **one cycle in three**; about half
the cycles never reached it because the signal was below strength 6.
