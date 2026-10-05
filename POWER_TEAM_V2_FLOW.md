# POWER v2 → Shooting handoff (current implementation)

The repository integration is already active in `step2_market_analysis.py`. This page is the current code contract, not a future wiring proposal. All order execution remains disabled; Shooting returns `PLAN_ONLY` and the paper simulator is not a broker.

```text
Bookmap feed + trusted timestamps / aggressor-side labels
       ↓
M5 adapter and regime/freshness checks
       ↓
POWER v2: UP / DOWN / NEITHER + force shares, activity, coverage, blockers
       ├─ NEITHER / stale / weak evidence → Shooting WAIT
       └─ UP or DOWN → Shooting validates market/news/quotes/Scout route/geometry
                         ├─ PASSIVE_LIMIT (LIMIT) plan, preferred by default
                         └─ AGGRESSIVE_MARKET (MARKET) alternative, advisory unless
                            an independent, audited trigger is confirmed
       ↓
Paper entry simulator: pending limit / simulated fill only
       ↓
Escort receives only a confirmed paper fill
```

## What is implemented

- `step2_market_analysis.py` calls Power v2 and passes its canonical direction to `shooting_team.py`; legacy POWER is not the active authority for this handoff.
- Power keeps the existing evidence thresholds. Its output includes direction, force-share split (not probability), activity, coverage, valid families, excluded evidence, and reason code. `NEITHER` is not converted into a guessed side.
- Shooting independently checks regime, freshness, evidence-share schema, explicit news status, feed quality/age, bid/ask, Scout target/route, queue context, and target/stop/cost geometry.
- Shooting returns both a patient `LIMIT` plan and an aggressive `MARKET` alternative when geometry can be assessed. The aggressive option stays ineligible without its own independent trigger and route clearance. Power direction alone is not a market-entry trigger.
- Queue estimates are used only when probability, volumes, timestamp, symbol, order side/price, and the quote snapshot validate. Missing or invalid queue evidence is `UNKNOWN`; the plan never treats it as a guaranteed fill.
- Plans expire and paper fills are cancelled when the plan expires or market price moves beyond its configured revalidation tolerance. Portfolio sizing/exposure is explicitly `NOT_ASSESSED`; this is an execution blocker, not a default approval.
- Every Shooting plan remains `PLAN_ONLY`. The simulator can stage/fill only paper limits and Escort only manages paper-filled plans.

## Power evidence: diagnosis without weakening thresholds

Power's gates are intentionally unchanged: missing/stale/invalid evidence is excluded; direction requires its configured judge/family minimums, coverage, activity, dominance, and family agreement; uncertain/unknown regime remains non-directional. Do not relax these gates just to increase trade count.

There is no raw timestamped trade replay artifact in this workspace: the referenced latest probe CSV lives on the user's Windows machine and was not included here. Therefore no honest claim can be made about whether Power's latest `NEITHER` was caused by low market volume, missing side-labelled trades, insufficient coverage, weak activity, regime uncertainty, or disagreement. The code now surfaces the evidence diagnostics in the Shooting plan, but the empirical diagnosis/replay is still blocked until the actual probe/Bookmap data is supplied.

Time of day alone does not prove low volume. On 5 October 2026, 20:30 Budapest time is 14:30 New York time (US daylight time), around the US cash-session open; that is not inherently a quiet period. Use observed feed activity, coverage, spread and freshness. If those are weak, the correct output is still `NEITHER`/`WAIT`.

## Required validation before any execution review

1. Run the unit suite and the synthetic handoff test. These verify software contracts only, not edge or realistic fills.
2. Replay captured timestamped trades and quotes through the adapter → Power → Shooting, preserving the original feed labels and symbol. Report why each `NEITHER` occurred; do not fabricate sweeps or directional evidence.
3. Validate queue-model calibration against observed fills/cancellations. The current formula is only a heuristic.
4. Add a separate account-risk gate covering sizing, max risk/trade, total open risk, margin, daily loss, correlated positions, and kill switch.
5. Revalidate direction, quote, news, route, bracket and plan expiry immediately before any future order proposal.
6. Keep broker submission disabled until independent historical/out-of-sample and paper evidence, controls, and user authorization are reviewed.
