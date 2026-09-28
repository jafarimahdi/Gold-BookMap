# How the teams fit together — staged v2 flow

This is the intended **data/decision handoff**, not yet a production integration. Keep the existing path untouched until every contract is tested.

```text
Bookmap trades with timestamps + validated aggressor sides
                         │
                         ▼
              M5 ADAPTER (next step)
    selects [bar start, bar end), checks freshness/quality,
    creates normalized signed readings and diagnostics
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
         POWER v2              MEMORY BOOK
     UP / DOWN / NEITHER      one FINAL record per
     force shares, activity,  completed M5 bar
     coverage, reason         inputs + result + price
              │
              ├── NEITHER ──► SHOOTING: WAIT / NO_GO; no side
              │
              └── UP or DOWN ─► SHOOTING checks Scout map, route,
                                reward/risk, spread, queue, permission
                                             │
                                    GO / WAIT / NO_GO plan
                                             │
                                             ▼
                                   ESCORT manages that plan
                                   using its unchanged contract
```

## Ownership boundaries

- **Scout** supplies prices, doors, walls and map facts. POWER v2 does not need the map to measure force.
- **POWER v2** uses executed-trade evidence from a five-minute window. It returns force shares, evidence coverage/activity, context blockers and a three-way direction.
- **Memory book** records the POWER inputs/result once for each completed M5 bar. Historical records do not vote in the next decision.
- **Shooting** is the first component that combines direction with Scout targets and execution/permission constraints. It must explicitly turn `NEITHER` into a non-trade plan.
- **Escort** continues to consume the Shooter's plan. If that plan schema remains stable, Escort should not need a conceptual change, but it still needs regression tests after the Shooter interface changes.
- **Execution** remains out of scope for the prototype. Do not connect v2 to broker order calls in this stage.

## Current next-step adapter

`power_m5_adapter.py` accepts timestamped trades and only trusts direct side labels by default (`is_direct` / `is_bookmap_direct`). It builds `footprint_delta`, `big_prints`, `footprint_levels`, and, when there is enough preceding data, short-window `cvd_momentum`. It deliberately does not fabricate `l3_aggr_limit`, a sweep, an absorption vote, or a range/breakout judgement.

The adapter needs the **data instrument's tick size**, not automatically the CFD's tick size. Your configuration lists a CFD tick size of 0.05, while the Bookmap feed is futures-side; verify the Bookmap contract's actual increment before calling the adapter.

If timestamps or trusted side labels are absent, or the feed is stale, the adapter reports a blocker so POWER returns `NEITHER`. Do not switch `trust_explicit_side=True` until that source's side semantics are verified.

## Intended call sequence (illustrative only)

```python
built = build_m5_inputs(
    tick_data,
    now=now_utc,
    tick_size=verified_bookmap_tick_size,
)
context = {
    **built["context"],
    "in_range": independently_verified_range_state,
    "breakout_confirmed": independently_verified_breakout,
}
answer = decide(built["judges"], context=context)

# Once, on the completed M5 bar only—not on every poll:
append_snapshot(
    "data/power_memory",
    timestamp=m5_bar_end_utc,
    symbol=symbol,
    judges=built["judges"],
    context={**context, "diagnostics": built["diagnostics"]},
    result=answer,
    reference_price=close_of_that_m5_bar,
    phase="FINAL",
)
```

Do not copy this into `step2_market_analysis.py` yet. `independently_verified_range_state`, `independently_verified_breakout`, the exact M5 close handling, and the futures tick-size configuration still need to be settled. The existing Shooting code expects legacy `pick=above/below`/`confidence`; it must be deliberately migrated to `direction=UP/DOWN/NEITHER` before the new module is called there.

## Stages

1. **Now:** add the adapter and its isolated tests to the existing feature branch; run the full unit suite. No app startup.
2. **Next:** review a captured/replay window locally, verify timestamps, side labels, price increment, quality blockers, and output by hand.
3. **Then:** design a separately tested bar-close coordinator plus memory call (one row per completed bar).
4. **After that:** migrate Shooting to the new direction contract; test NEITHER and all existing shot outputs. Verify Escort still sees its expected plan shape.
5. **Only after historical/paper validation:** consider live-chain integration. Unit tests alone never authorize live orders.
