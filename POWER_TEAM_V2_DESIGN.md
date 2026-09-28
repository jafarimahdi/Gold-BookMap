# POWER Team v2 — M5 force balance

## Goal and boundary

POWER measures **which side is currently applying more short-horizon directional force**. It returns exactly one of `UP`, `DOWN`, or `NEITHER`, alongside an upward/downward force split that sums to 100%.

The percentages are **shares of the directional evidence available to POWER**, not calibrated probabilities of future price movement. Example: `UP 64% / DOWN 36%` means that the included M5 force readings currently lean upward. It does not mean a 64% chance of an up move.

POWER does not find prices, choose a target, approve an order, or execute trades:

- **Scout:** where are the relevant prices / liquidity doors?
- **POWER:** which side has stronger recent pushing force, or is it neither?
- **Shooting / permission layer:** is a trade allowed and is the route worthwhile?
- **Execution layer:** only a separately approved component may send an order.

## M5 measurement contract

The caller must supply normalized directional judge readings from a **consistent rolling five-minute window**:

- `+1`: strongest normalized upward evidence
- `0`: neutral / no directional evidence
- `-1`: strongest normalized downward evidence
- optional `quality` in `[0, 1]`: freshness and reliability of that judge's input

A missing, stale, invalid, or zero-quality reading is excluded, not silently counted as neutral. The caller must not pass session-cumulative CVD or multi-hour aggregates as though they represented the last five minutes. For stable auditability, record whether readings are provisional within the active bar or finalized at the M5 close.

### Direction calculation

Each directional reading is clipped to `[-1, +1]`. With the configured judge weights, POWER accumulates positive evidence into `up_force` and negative evidence into `down_force`:

```text
up_power_pct   = 100 * up_force   / (up_force + down_force)
 down_power_pct = 100 * down_force / (up_force + down_force)
```

The displayed shares sum to 100 whenever evidence exists. With no directional evidence, the display is `50 / 50`, but `activity=0`, `coverage=0`, and `direction=NEITHER` make clear that this is **not** a real contest.

The team also reports:

- `activity`: the strength of the included normalized readings, including the possibility that both sides are active;
- `coverage`: the weighted share of the core judge roster with usable data;
- `valid_judges`, `excluded_judges`, and a reason code;
- separate contextual fields, which do not enter the force percentage.

### Three-way decision

Initial conservative defaults in `power_team_v2.py`:

- the caller must explicitly supply `market_regime=TREND` or `RANGE`; missing/unknown regime returns `NEITHER`;
- normal TREND conditions: at least 55% weighted coverage, at least 3 valid judges, at least 2 valid evidence families, activity at least 0.18, a leading side with at least 60% of directional evidence, and at least 2 families with a signed score of at least 0.15 in that direction;
- RANGE: `NEITHER` unless a separate, reliable breakout confirmation is supplied;
- confirmed range breakout: stricter thresholds—at least 70% coverage, 4 valid judges, all 3 independent evidence families represented and agreeing, activity at least 0.50, and a side share of at least 70%;
- stale feed, bad data quality, high-impact news, or a major contradiction: `NEITHER`.

These are safe starting points for a **paper/research version**, not proven trading thresholds. Calibrate only with time-separated walk-forward tests and retain an untouched out-of-sample period.

## Judge roster and fit

### Core directional judges in v2

The code caps evidence by family: executed flow has 60% of the total budget, large executed prints 20%, and breadth 20%. The three flow judges share that 60%; adding more correlated flow measures cannot increase that family's total influence.

| Judge | v2 role | Recommendation | Superpower / caution |
|---|---|---|---|
| `footprint_delta` | Executed-flow family | Keep | Aggressor buy/sell imbalance across traded prices. Strong M5 input only if built from the same rolling five-minute window. |
| `l3_aggr_limit` | Executed-flow family | Keep, rename in UI if possible | Executed aggressive buying versus selling shows urgency. In the current data model it reads aggressive executed volume; “limit” in the name can mislead. |
| `cvd_momentum` | Executed-flow family, lower subweight | Keep only as short-window change | Recent CVD change can reveal pressure building or fading. Do not pass a session-cumulative value as M5 force; it overlaps with delta. |
| `big_prints` | Large-executed-trades family | Conditional keep | Separates unusually large signed trades from small-trade churn. Size is a proxy, not proof of trader identity. Its signed contribution is scaled by total M5 volume so a small selected subset cannot claim a full-strength vote; if sizes do not distinguish larger prints from ordinary ones, the judge is excluded. |
| `footprint_levels` | Breadth family | Keep, modest influence | Measures whether the imbalance is spread across levels rather than concentrated in one. Normalize with a valid M5 sample and keep its entire family weight capped. |
| `sweep` | Not a v2 force vote | Remove from POWER for now; retain for later review | The repository's current sweep is a candle-based stop-hunt/reversal label (high taken then rejected = bearish; low taken then rejected = bullish), not an aggressive order sweeping the book. More seriously, the current caller supplies synthetic high/low values based on close ± 0.3 ATR and constant volume. That is not reliable evidence for a force score. Reintroduce only after it uses real timestamped OHLCV or MBO/trades, has an explicit semantic (continuation sweep vs stop-run reversal), direction, and tests. |

The v2 family caps are deliberate: `footprint_delta`, `l3_aggr_limit`, and `cvd_momentum` are correlated observations of executed flow. They share one capped family rather than acting like three independent crowds. Current production data still needs a reliable rolling-M5 adapter before any of these readings can safely feed the module.

### Context, resistance, map, and safety judges—not force votes

| Judge | Fit to POWER's force percentage? | Recommendation |
|---|---|---|
| `volume_roc` | No direction; measures activity | Keep as a separate activity/context reading. Rising volume alone is not upward or downward force. |
| `absorption` | Resistance / force efficiency, not simple direction | Keep as a separate warning. It says a push may be getting absorbed. Do not automatically award its sign to the other side. |
| `cvd_divergence` | Warning / disagreement | Keep as a separate contradiction flag. It may reduce trust or cause `NEITHER` under an explicit rule; it must not secretly reverse the force score. |
| `stall_clock` | Compression context | Optional context only. It says how compressed price has been, not who is pushing. |
| `value_area` | Auction regime | Move to context. It can describe range/trend conditions; it is not a directional shove. |
| `mtf` | Higher-timeframe confirmation | Keep separately. M5 is the force horizon; M15 can be context, while H1 should not dominate a five-minute reading. |
| `vwap_bands`, `vwap_trend` | Price location / stretch | Keep outside the force split. These describe where price is relative to value, not how much force is applied. |
| `poc_day`, `htf_poc`, `supply_demand` | Price landmarks | Put with Scout/map data. They name places, not pushing force. |
| `news_sentiment` | Event risk and possibly slow bias | Keep as a safety/context flag. Near high-impact releases the force reading can be unreliable; news tone should not be treated as current executed pressure. |
| `macro_risk` | Slow background | Do not use in an M5 force calculation. If retained, show as background context only. |

### Helpful judges currently outside POWER

These can improve a **separate book/intent confirmation**, but they should not be blended into the “executed push” percentages. Otherwise the display mixes actual trading with orders that may be cancelled before trading:

| Judge | Superpower | Suggested use |
|---|---|---|
| `microprice` | Size-weighted book tilt can move before the last-traded price | Show as an early order-book confirmation beside POWER, clearly labelled “resting-book tilt.” Do not count it as executed force. |
| `l3_net_flow` | Detects resting orders added or pulled | Use as intent / liquidity-change confirmation. Orders can disappear, so keep it separate from traded force. |
| `l3_large_ofi` | Focuses flow imbalance on larger resting orders | Useful large-order context, not executed force. |
| `whale_walls` | Finds large visible resting liquidity at a price | Scout/map input: resistance/support location, not push strength. |
| `iceberg` | Detects repeated refills that may hide more size | Resistance/absorption context; a defending hidden order can explain why a push stalls. |
| `spoof_invert` | Identifies suspicious add-then-cancel behavior | Data-integrity / book-trust warning. It should not become a raw directional POWER vote. |
| `queue_pos` | Estimates whether our order can get filled in the queue | Execution quality only; not market force. |

### One useful missing diagnostic: `flow_efficiency` (candidate, not yet active)

Measure the realized M5 price response relative to signed executed pressure—roughly, “how much did price travel per unit of net aggressive flow?”, normalized by volatility and volume. It can distinguish a shove that is moving the cart from heavy effort being absorbed, and can flag a thin-book price jump on little traded force. Keep it as a separate **effectiveness/quality** diagnostic, not an extra directional vote. A low value must not automatically reverse the side: flow can lead price, and the interpretation needs data-specific validation. The code exposes `flow_efficiency` as context but does not calculate it from raw feeds yet.

## Memory book

`power_memory_book.py` is a separate, append-only history writer for research/paper records. The intended cadence is **one FINAL record per completed M5 bar**; PROVISIONAL intrabar records can be stored separately only when there is a clear audit need. Each daily UTC JSONL file stores the timestamp, symbol, reference price, normalized judge readings, context, team result, and schema version. A hash chain makes accidental edits or missing/reordered records detectable; it is not a substitute for access controls or a regulated immutable archive. It is single-writer by design and must not receive credentials, API keys, or account data.

Initially the memory is **for audit and later evaluation only**. Do not feed its past outputs back into Power as a self-learning signal. First use the records to check judge availability, M5 force behavior, NEITHER frequency, and out-of-sample outcomes. A later learning system should be a separately validated component.

## Opinion and improvements

The three-way answer is essential. In ranges, during weak flow, with conflicting evidence, or when data is unreliable, **NEITHER is better than inventing a direction**. Downstream teams should treat `NEITHER` as “do not use POWER to authorize a side,” not as a hidden neutral buy/sell vote.

I recommend four guardrails:

1. **Separate force, activity, confidence/coverage, and context.** Do not compress them into one opaque score.
2. **Require consistent clocks.** Every directional input needs a clear M5 calculation window, timestamp, freshness check, and units/normalization.
3. **Control correlated votes.** Related order-flow judges should be grouped or capped by evidence family and checked with ablation tests.
4. **Prove it before connecting it to orders.** Log the force split and all inputs in paper mode; grade the next fixed horizon (for example, next 1–3 M5 bars) against a neutral baseline; report precision, coverage, calibration, adverse excursion, and performance by range/trend/news regime. Avoid tuning on the same days used to report success.

## Repository integration warning

The current repository's `step2_market_analysis.py` calls the existing POWER function, and `shooting_team.py` expects its old `pick=above/below` and `confidence` fields. Replacing that function directly with this v2 contract without updating the downstream caller would break the handoff. This implementation is deliberately saved as `power_team_v2.py` and is not wired into the old pipeline. Before activation, update the new-version caller and shooting/permission interface to consume `direction`, explicitly handle `NEITHER`, and keep the legacy paper path isolated. This module itself cannot place orders.

The repository's docs also disagree on roster membership and whether `absorption` belongs to POWER or SIGNAL. The roster above is the recommended v2 ownership, based on each judge's actual information source—not a claim that all old lists already match it.
