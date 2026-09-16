# The Decision Chain — every condition, every judge, in order

**How a trade is born:** every 60 seconds the pipeline runs, and a trade happens
ONLY if it survives every gate below. The logic is pure AND — one "no" anywhere
kills the trade for that cycle. Nothing can force a trade; many things can veto one.
Every failure mode (no data, no key, 503, weekend, MT5 off, real account) ends
in HOLD or SKIPPED — never in an accidental order.

```
STEP 1  data →  STEP 2  signal engine  →  STEP 3  Gemini  →  SAFETY GATES  →  STEP 4  executor  →  order
        feed              (proposes)               (judges)     (context)              (notarizes)
```

---

## ACT 1 — The Signal Engine (Step 2): the PROPOSER

The only component that can *suggest* a trade. It fuses 25+ analyzers into one
composite verdict — direction (BUY/SELL/NEUTRAL), strength (0–100), confidence,
regime (TREND/RANGE), divergence:

| Analyzer group | What it measures |
|---|---|
| Technical | SMA/EMA stack, ADX, MACD, RSI, ATR, Bollinger |
| Order flow | CVD, delta, buy%/sell%, tick-rule classification |
| Level 2 depth | microprice, bid/ask imbalance, OFI, depth slope, walls, absorption |
| Footprint | per-price buying/selling levels, delta imbalance |
| Volume profile | VWAP (+z-score), POC, value area, OBV, A/D |
| Macro | DXY, 10Y yield, VIX + correlations to gold |
| News & calendar | headline sentiment, upcoming events (QUIET/WARNING/BLACKOUT) |
| Session | Sydney/Tokyo/London/NY — knows "thin" hours |

Output: a **proposal** like `SELL strength=30.4 confidence=32.6` — never an order.
Its confidence is already reduced for elevated real yields, upcoming events,
thin sessions — the notes in the snapshot show why.

Macro votes are **change-based** (Δ yields/DXY over 5 sessions, VIX vs its
20-session median) and subject to the **macro-opposition rule**: macro against
the direction shrinks the score up to −50%, and *extreme* opposition without
order-flow confirmation (delta/OFI) caps the signal to NEUTRAL — "you may
fight the macro, but only with flow evidence" (see GOLD_MARKET_DRIVERS.md).

## ACT 2 — The AI gates + Gemini (Step 3): the JUDGE

1. **AI_MIN_SIGNAL_STRENGTH = 7** — strength below 7 → Gemini is not even asked
   (quota saver; tonight: "signal 2.0 < 7, AI skipped").
2. **AI_MIN_INTERVAL_MINUTES = 1** — never ask more often than this.
3. **Gemini sees everything**: the full snapshot above + news + macro + session.
   Returns BUY / SELL / HOLD with a confidence % and reasoning.
4. **Fail-safes**: no key → HOLD; all 3 keys fail (503) → HOLD; timeout (20 s) → HOLD.
   Key rotation (3 keys, 20-min cooldown per failed key) + model fallback list
   (gemini-3.7-flash → 3.5 → 3.6 → 2.5) try hard to get an answer first.

Gemini is the only component that can **approve**. Its confidence must be ≥ 70%.

## ACT 3 — Safety gates (main.py, between the AI and the executor): the CONTEXT

Checked every cycle BEFORE the executor is allowed to do anything:

| # | Gate | Setting | Blocks when |
|---|---|---|---|
| S0 | **Master switch** | TRADING_ENABLED=1 | switch off → analyse only, nothing else matters |
| S1 | **Session** | auto | weekend / holiday / daily break |
| S2 | **Feed freshness** | STALE_DATA_SECONDS=300 | data older than 5 min (CME halt, NT closed) |
| S2b | **Data spread** | MAX_SPREAD_PCT=0.05 | futures spread too wide |
| S3 | **Risk circuit breakers** | DAILY_LOSS_LIMIT_PCT=3, MAX_DRAWDOWN_PCT=10 | day's losses ≥3% of equity → halt until tomorrow; equity 10% below peak → halt |
| S4 | **Anti-overtrading** | COOLDOWN_MINUTES=15, MAX_TRADES_PER_DAY=30 | a trade opened <15 min ago, or 30 trades already today |

S3 and S4 persist their state to disk — a restart does not reset them.

## ACT 4 — The Executor (Step 4): the NOTARY

No opinions — pure mechanical verification, in this exact order:

| # | Check | What it does |
|---|---|---|
| E1 | Kill switch (again) | direct callers cannot bypass TRADING_ENABLED |
| E2 | EXECUTION_MODE=python | only Python may send orders |
| E3 | Valid snapshot | price > 0 — never trade on an empty snapshot |
| E4 | **Confidence ≥ 70%** | Gemini's own confidence re-checked (THE active filter) |
| E5 | Action is BUY/SELL | HOLD is not tradable |
| E6 | **News BLACKOUT** | 15 min before / 20 min after high-impact events: no new entries |
| E7 | MT5 alive | terminal reachable |
| E8 | **Real-account guard** | REAL account + ALLOW_LIVE_TRADING=0 → refuse loudly |
| E9 | **CFD spread guard** | actual XAUUSD spread > 0.05% → defer this cycle |
| E10 | Position ownership | same-direction position exists → skip (no pyramiding); opposite → close it first |
| E11 | SL/TP computed | SL = 1.5×ATR, TP = 3×ATR, ATR re-anchored from futures to the XAUUSD price; news WARNING widens SL ×1.5 and halves size |
| E12 | Broker stops level | SL/TP must respect Pepperstone's minimum distance |
| E13 | Position sizing | lots = equity × 0.1% / (stop distance × 100), clamped to max 0.01 |
| E14 | Broker volume rules | normalized to min/step/max lot |
| E15 | Margin check | free margin must cover the order |
| E16 | order_check preflight | MT5 dry-validates the order before it is sent |
| E17 | order_send | retcode must be TRADE_RETCODE_DONE |
| E18 | Position verified | the new position must actually appear within 5 s |

After E18: the trade is recorded (cooldown starts), logged to decisions_log.csv,
and the signal file is written.

---

## Which gate matters most?

Two different kinds of power — don't confuse them:

**Power to STOP (protection) — ranked:**
1. **TRADING_ENABLED** — absolute; overrides everything instantly, even mid-run
2. **Risk circuit breakers (S3)** — protect the account even if everything else fails
3. **Moment protection (S1, S2, S6/E6, spreads)** — keep you out of bad moments: halts, news, wide spreads
4. **Anti-churn (S4, E10)** — stop the account bleeding via spread/overtrading

**Power to ALLOW (selectivity) — ranked by how often they actually decide:**
1. **Gemini confidence ≥ 70% (E4)** — the active daily filter; most cycles die here
2. **Signal strength ≥ 7** — decides whether Gemini is asked at all
3. **News blackout** — blocks specific hours per week
4. Everything else — passes silently almost always

In practice: the safety tier almost never speaks (it's insurance), and the
selectivity tier decides every single cycle. That is exactly what you want:
insurance that is quiet, judgment that is strict.

## The final decision — three judges, three viewpoints

- The **signal engine** sees only the market (numbers, flow, depth) → proposes
- **Gemini** sees the market AND the world (news, macro, session) → approves/vetoes
- The **executor** sees only mechanics (account, broker rules, positions) → verifies

A trade = proposal + approval + verification, all in the same 60-second cycle.

**Worked example (tonight 02:02):** gates S0–S4 passed silently → strength 30.4 ≥ 7
→ Gemini asked → **HOLD @ 75%** → died at E4 (confidence 0 < 70). No trade, logged, next cycle.

**A full YES looks like:** trending London/NY market → strength ≥ 7 → Gemini
BUY @ 72% → all safety gates green → E1–E18 green → `EXECUTED BUY XAUUSD 0.01
lots @ ... (SL ... / TP ...)` → cooldown 15 min starts.
