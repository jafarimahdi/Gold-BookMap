# Options Certification & Master Flowchart (v4.3.2)

> Four requested certifications (L2 depth 20→40 · macro trio · macro opposition rule ·
> the PM's 5-minute flow history) plus the full options flowchart of both strategies,
> with ★ marking the components **I judge critical / best-performing**.

---

## 1. Certifications

### ① L2 order book (20+20 levels) — and the 40-level proposal

**Verdict: KEEP at 20 today. The 40-level upgrade is a v4.4 candidate — with a condition.**

Your instinct is legitimate: deeper book = seeing where **bigger actors leave resting
liquidity** (walls, big willingness-to-fill). Three honest cautions before upgrading:

1. **The scalper-critical analytics live in the top ~10 levels** — microprice, OFI
   (the 0.9-weight heaviest vote!), near-book imbalance. Deep levels mostly improve
   context, not entry timing.
2. **Resting liquidity is not intent.** Big players hide size (icebergs) and pull
   levels in milliseconds. Where big actors *actually traded* shows up in the
   footprint and tick flow — which we already analyze. L2 depth shows offers;
   flow shows hands.
3. **The feed may be the real limit.** The C# exporter forwards everything NT
   provides; NT broker feeds often cap at 10–20 levels per side. If the feed sends
   20, raising our cap changes nothing.

**The 5-minute experiment (do this first):** change `_MAX_BOOK_LEVELS = 20` → `40`
in `ninja_bridge_provider.py`, restart, read the log line
`NinjaBridge provider: N ticks, X bid levels, Y ask levels`. If X/Y say ~40, your
feed has deep data — proceed. If they stay ~20, the feed is the limit — skip it.

**If the experiment succeeds, upgrade it *with a tool that reads depth*** — a
**deep-wall detector**: resting size ≥ K× median beyond level 5 becomes a
magnet/rejection level fed into the PM's anchor pool and the entry notes. Depth
without a reader is just a bigger file.

**And the honest ranking for "seeing the big actors":** L3/MBO order events
(adds/cancels/market orders — three dormant 0.8-weight votes are already waiting
in the engine) > footprint/volume (have it) > deep L2. That's the Databento
roadmap item, and it beats the 40-level idea.

### ② Macro trio (DXY, 10Y real yield, VIX) — do scalpers need it?

**Verdict: KEEP — CERTIFIED.**

Its role is **backdrop filter, not timing tool** — and that's exactly right for a
scalper. Gold is priced against the dollar and real yields; a 1-minute scalp gets
run over when that backdrop flips intraday (yesterday's CPI day is the proof).
Live evidence it's working: `macro against BUY -> score scaled to 96%`,
`10Y rising 0.20%/5d` notes, and the trio feeds the AI's prompt.

The current design (15-min cache, 5-day changes) correctly makes it a *regime
layer*, not a per-minute signal.

**Future update (v4.4 candidate):** add **intraday macro deltas** — DXY/10Y change
over the last 1h / since session open, computed free from the cached series.
Sharper for short holds, zero new cost.

### ③ Macro opposition rule — do scalpers need it?

**Verdict: KEEP — CERTIFIED. One of the best-designed rules in the entry engine.**

It encodes the professional principle *"you may fight the macro, but only with
flow evidence"*: opposition shrinks the score up to 50%, extreme opposition
(>0.8) without order-flow proof vetoes the trade entirely. A hard veto would be
rigid; a soft shrink with a flow escape-hatch is how a desk would write it.
Cost: zero (uses data already fetched).

**Future update:** the 50% shrink and 0.8 veto thresholds are hand-set —
they're on the table for the **votes/weights session**. Optional v4.4 idea: let
the PM see macro opposition too (trading against the macro → arm PROFIT_LOCK
earlier).

### ④ "Own 5-minute flow history" — what it is and how it works

**Verdict: KEEP — CERTIFIED. It is the engine behind your profit-protection features.**

**What it is.** Every cycle (~60 s), for each open position, the PM writes one
tiny row into its own private log: *(timestamp, CVD now, price now)*. It keeps
the last 5 minutes (`PM_FLOW_LOOKBACK_MINUTES=5`).

**CVD in one line.** CVD = cumulative volume delta — a running total of
*aggressive buying minus aggressive selling*. Price is the **result**; CVD is
the **effort** behind it.

**How it works — the car analogy.** Price is where the car is. CVD is the
accelerator pedal. Every minute the PM checks 5 minutes of pedal + movement:
is your trade still being *driven* forward, or is someone pressing the opposite
pedal? Then it classifies:

| Price (your direction) | CVD (your direction) | Label | Meaning |
|---|---|---|---|
| moving in favor | moving in favor | **flow confirms the move** | healthy — hold |
| moving in favor | against | **aggressors exhausted** | expect stall — protect earlier |
| flat | in favor | **passive side absorbing** | a resolution move is loading |
| against | against | **flow against the position** | ← the fuel MOMENTUM_EXIT needs |
| flat | flat | **quiet** | nothing to read |

**Worked example (your SELL @ 4292):** 5 min ago price 4288, CVD −50 → now
price 4297, CVD +30. Price moved **against** the SELL *and* buyers are pressing
the pedal **against** it → label "flow against the position". If the trade is
≥0.3R in profit and the composite signal is ≥40 against → **MOMENTUM_EXIT
closes at market.** No waiting for the TP.

**Three uses of the same history:**
1. the label is required fuel for **MOMENTUM_EXIT**;
2. "aggressors exhausted" arms **early break-even at 0.5R instead of 1.0R**
   (`PM_FLOW_WEAK_BE_R=0.5`) — a move without pedal support protects itself sooner;
3. the health line `PM: ticket | flow: … | vwap: …` journaled every cycle.

**Why "own" history matters:** it's measured by the PM itself from snapshots,
so it works identically no matter how big the tick window is (a 5-minute CVD
*change* is the same whether the counter started 1 minute or 8 hours ago) —
that's why MOMENTUM_EXIT survived the window fix untouched. After a restart it
says `warming up` for ~3 cycles while it rebuilds — normal.

**Future tune:** after a few trading days, review `management_log.csv` and try
lookback 3 vs 5 vs 8 minutes — pick by evidence.

---

## 2. Master flowchart — every option, both strategies

★ = **critical performer / best act in my professional judgment.**

```
                THE GOLD ROBOT — MASTER OPTIONS FLOWCHART (v4.3.2)

════════════════ ENTRY STRATEGY — "should we open a trade?" ════════════════

  DATA SOURCES              ANALYSIS FAMILY (votes)             DECISION
  ─────────────             ───────────────────────             ────────

  M1 candles ──────►  TIMEFRAME (context — the seniors)
  (own build, 8h)      ★ H1   (weight 1.0)   "don't fight the big flow"
                       ★ M15  (0.8)
                         M5   (0.6)

                       TREND (the road — veto tools, not predictors)
                         SMA50 (0.7) · SMA20 (0.5) · MACD (0.6) · RSI (0.5)

  L1 trades ───────►  FORCES (order flow — the scalper's fastest truth)
  (150/min,             ★ OFI order-flow imbalance (0.9) ← HEAVIEST VOICE
   tick-rule)             buy/sell pressure (0.8) · footprint delta (0.7)
  L2 order book ─────►    depth imbalance (0.7) · CVD sign (0.6)
  (20+20 levels)          bid/ask ratio (0.6) · microprice (0.5)
                         absorption (0.5)
                       [L3 votes: aggressive-flow 0.8 · L3-OFI 0.8 ·
                        book-imbalance 0.6 — DORMANT until MBO feed]

                       LEVELS (regime-aware: fade extremes in RANGE,
                               follow breaks in TREND)
                       ★ supply/demand zones · ★ VWAP/POC/value area
                       ★ H1-H4 POC magnets   ·   round numbers

  DXY/10Y/VIX ─────►  ★ MACRO OPPOSITION RULE
  (15-min cache)        "fight the macro only with flow evidence"
                       (score shrink ≤50% · veto >0.8 without flow)

  News + calendar ──►  ★ NEWS STATE MACHINE  QUIET / WARNING / BLACKOUT
                        + confidence cut on HIGH-event days

                            ▼
                 weighted SCORE −100 … +100   (BUY ≥ +15 / SELL ≤ −15)
                            │  confidence = agreement + strength
                            ▼
                 ★ MTF CONFIRMATION FILTER (H1→M15→M5 must not all oppose)
                            ▼
                 ★ AI JUDGE — Gemini, own confidence ≥ 70%
                   (reads the full snapshot + live headlines + macro)
                            ▼
                 ★ 7 CONTEXT GATES — spread · cooldown · 30-trade cap ·
                   daily-loss brake · blackout · MT5 · master switch
                            ▼
                 ★ ORDER BORN WITH INSURANCE
                   SL = 1.5×ATR · TP = 3.0×ATR (1:2) · lots = risk ÷ stop

══════════ MANAGEMENT STRATEGY — "how do we protect and exit?" ══════════

  DATA SOURCES              MEASUREMENTS                    RULES
  ─────────────             ────────────                    ─────

  MT5 position ────►  ★ HIGH-WATER MARK — best gain ever, never forgets
  (realized truth)     R-now (risk units) · live spread meter
                            │
  PM's own log ────►  ★ 5-MINUTE FLOW HISTORY (CVD vs price)
  (5-min lookback)      ├─► labels: confirms / exhausted / absorbing /
                       │         ★ "flow against" → MOMENTUM_EXIT fuel
                       └─► flow-weak → ★ EARLY BREAK-EVEN (0.5R not 1R)
                            │
  Step-2 snapshot ──►  real ATR · ★ ANCHOR POOL (zones / POC / H1-H4 POC)
                      VWAP z-score · composite signal · news state
                            │
        ┌───────────────────┴──────────────────────────────┐
        │                                                  │
   EXITS — OR logic (any ONE fires)         LADDER — tighten-only
   ──────────────────────────────           ──────────────────────────
   ★ MOMENTUM_EXIT (≥0.3R + flow            ★ PROFIT_LOCK ratchet (≥1 ATR:
     against + signal 40)                      keep ≥50% of best gain)
     FLIP_EXIT (signal 55 + flow)            ★ BE at +1R (0.5R if flow weak)
     DIVERGENCE_EXIT (CVD diverges)          ★ TRAIL behind structure
     TIME_STOP (90 min, <20% progress)         (anchor pool, floor 1.2×ATR)
   ★ SESSION_FLATTEN (21:30 UTC daily)         VWAP_TRAIL (z ≥ 0.5)
     SL / TP hit — the market decides          VWAP_STRETCH (z ≥ 2 → lock half)
                                              ★ NEWS_PROTECT (HIGH event ±10 min)
                                              ★ ADAPTIVE TP (front-runs the level)
        │                                                  │
        └───────────────────┬──────────────────────────────┘
                            ▼
              ★ SPREAD GUARD — never edit into a spiked spread
              ★ JOURNAL — every action → management_log.csv
```

### My bold picks (the ★ list) and why

| Component | Why it's elite |
|---|---|
| **OFI (0.9)** | the heaviest vote and the fastest truth a scalper can buy |
| **H1 context (1.0)** | makes counter-trend scalps expensive — yesterday's lesson |
| **Macro opposition rule** | professional design: soft shrink + flow escape-hatch |
| **News state machine** | keeps the robot off the train tracks on CPI days |
| **AI gate ≥70%** | the only tool that can read a headline |
| **Birth geometry (1.5/3 ATR, 1:2)** | every trade starts with insurance and an edge in the math |
| **High-water mark** | never forgets the peak — the ratchet's memory |
| **5-minute flow history** | effort-vs-result measurement; powers momentum exit + early BE |
| **PROFIT_LOCK** | makes "winner closes negative" structurally impossible |
| **TRAIL anchors (zones/POC/htf_poc)** | trails behind where the market actually defends |
| **Adaptive TP** | exits before the level, not at it |
| **MOMENTUM_EXIT + SESSION_FLATTEN + spread guard** | the three exit disciplines of scalping |

### Future update map (in priority order)

1. **Votes/weights session** — calibrate every hand-set number above with real data
2. **Backtest/replay harness** — make the entry side measurable
3. **Scalping pack** — `PM_TIME_STOP_MINUTES=60`, `AI_MIN_SIGNAL_STRENGTH=12`, `AI_MIN_INTERVAL_MINUTES=2`
4. **L2 experiment (20→40) + deep-wall detector** — if the feed supports it
5. **Intraday macro deltas** + opposition-rule tuning
6. **L3/MBO feed (Databento)** — wake the three dormant 0.8-weight voices
