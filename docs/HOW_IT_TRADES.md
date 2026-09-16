# How the Robot Trades — the two most important parts

> **Part 1: how/why it OPENS a position. Part 2: how/why it MANAGES and CLOSES it.**
> Then: how the two strategies compare. Companion docs: `MIXED_SYSTEM_OVERVIEW.md`
> is the map of the app, `DECISION_CHAIN.md` is the exact gate list, and this file
> is the story in plain English.

---

## Part 1 — How a position OPENS: a committee, a judge, and seven gates

### The committee (Step 2 — about 20 voters, every 60 seconds)

Every cycle the robot asks ~20 indicators to vote BUY (+1) or SELL (−1).
Not all voices count equally — **the seniors speak loudest**:

| Family | Voters (weight) | What they measure |
|---|---|---|
| **Big timeframes** | H1 (1.0), M15 (0.8), M5 (0.6) | Is the higher-direction with us? The biggest timeframe has the biggest voice |
| **Trend** | trend direction (0.5–1.0), MACD (0.6), price vs SMA50 (0.7) / SMA20 (0.5) | Is the road tilted our way? |
| **Forces** (order flow) | **OFI (0.9 — the heaviest voice)**, buy−sell pressure (0.8), footprint delta (0.7), depth imbalance (0.7), CVD (0.6), bid/ask ratio (0.6), microprice (0.5), absorption (0.5) | Are real buyers/sellers pushing right now? |
| **Levels** | round numbers, supply/demand zones, VWAP/POC | WHERE the fight happens (they shape the notes: "magnet: toward round 4340", "near supply zone — rejection") |
| **Macro** | DXY, 10Y yields, VIX | Doesn't vote for a direction — it's the **opposition rule**: fighting the macro shrinks the score up to 50%, and extreme opposition *without flow proof* kills the signal entirely |
| **News** | calendar (QUIET/WARNING/BLACKOUT) | BLACKOUT = no trades; WARNING = wider stop, half size; HIGH-impact day = confidence reduced |

The votes are averaged into one **score from −100 to +100**.
- Score ≥ **+15** → the engine *proposes* BUY
- Score ≤ **−15** → the engine *proposes* SELL
- Between → NEUTRAL, do nothing

### The judge (Step 3 — Gemini)

A proposal is not a trade. The AI receives the full snapshot (everything above,
in plain numbers) and answers with its own confidence. **Below 70% → HOLD.**
No AI available (quota, 503) → HOLD. The AI is the only one who can say YES —
but it can never force a trade the committee didn't propose.

### The gates (Step 4 — pure AND logic: one "no" kills the trade)

```
signal ≥ 15  AND  AI asked (interval + strength)
          AND  AI confidence ≥ 70%
          AND  news ≠ BLACKOUT
          AND  spread OK  AND  cooldown passed  AND  < 30 trades today
          AND  risk check passed (daily loss < 3% equity)
          AND  MT5 connected
→ ORDER
```

### What the order looks like at birth

The trade is **born with insurance**:
- **Stop loss = 1.5 × ATR** (with real ATR ~1.6 pts → ~2.4 pts ≈ $2.4 risk)
- **Take profit = 3.0 × ATR** (≈ 4.8 pts ≈ $4.8 — a 1:2 risk:reward)
- **Lot size** = equity × 0.1% ÷ stop distance, clamped to 0.01 (min lot)
- During news WARNING: stop ×1.5 wider, size ×0.5 smaller

---

## Part 2 — How a position is MANAGED and CLOSED: one referee, pure rules

The Position Manager takes over the moment a trade exists (it even adopts
positions from before a restart). **No committee, no AI, no opinions — just
rules**, checked every 60 seconds per ticket.

### What it measures every cycle

Current gain, **best gain ever seen** (high-water mark — it never forgets the
peak), risk earned (R), flow health (CVD vs price over the last 5 min), VWAP
stretch, ATR, and the fresh levels (zones, POC, round numbers).

### The close rules — any ONE can fire (OR logic)

| Rule | Fires when | Why |
|---|---|---|
| **MOMENTUM_EXIT** (v4.3) | in profit ≥ 0.3R + flow against + signal ≥ 40 against | momentum visibly rolled over → take the money at market, don't wait for the TP |
| **FLIP_EXIT** | signal ≥ 55 hard against + flow confirms | the market changed its mind |
| **DIVERGENCE_EXIT** | CVD diverges while not yet in decent profit | the move is dying |
| **TIME_STOP** | 90 min old and < 20% of the way to TP | dead trade — free the margin and the mind |
| **SESSION_FLATTEN** | 21:30 UTC daily | never hold through the CFD break/gap |
| **SL / TP hit** | price reaches them | the market itself decides |

### The protect ladder — the SL only ever TIGHTENS

- **TRAIL** — SL follows the fresh structure behind price (floor 1.2×ATR)
- **BE** — at +1R the SL moves to entry: the trade is now free
- **VWAP_TRAIL / VWAP_STRETCH** — VWAP becomes the anchor; stretched ≥ 2σ → lock half the gain
- **PROFIT_LOCK** (v4.3) — once the gain reaches 1×ATR: the SL may never give back more than 50% of the BEST gain. **A winner cannot become a loser.**
- **NEWS_PROTECT** — HIGH event within 10 min → tighten winners
- **Adaptive TP** — the TP front-runs newly formed opposing structure (pulls the exit closer so you leave *before* the level, not at it)

Every action is journaled to `management_log.csv`, and no edit is sent while
the spread is spiked (never donate the spread).

---

## Part 3 — The two strategies compared

| | **Opening (Part 1)** | **Managing & closing (Part 2)** |
|---|---|---|
| Style | Committee — ~20 weighted voters | One referee — pure rules |
| Logic | **AND** — everything must agree | **OR** — any single rule can close |
| Final say | The AI (≥70% confidence) | Nobody — rules fire automatically |
| Speed | Slow, deliberate, many gates | Instant — one cycle |
| Memory | Fresh eyes every cycle | High-water mark — never forgets the peak |
| Risk | Defined at birth (SL 1.5×ATR, TP 3×ATR, tiny lot) | Only ever shrinks (tighten-only ladder) |
| Personality | Cautious optimist | Paranoid bodyguard |

**The deliberate asymmetry:** it takes a committee to open a trade, but a
single rule to close it. Entries are where you feel most confident and are
most often wrong; exits are where hesitation is most expensive. So the robot
makes getting IN hard and getting OUT easy.

**And the deeper pattern — protect profit faster than you cut loss:**
- To open, the signal must reach **±15** and the AI **70%**.
- To protect a winner, momentum only needs **40** against (not 55 — the flip level).
- To lock profit, the gain only needs **1×ATR** (a fraction of the full 1R wait for break-even).

Entry *defines* the risk. Management spends its whole life rewriting that risk
downward — break-even at 1R, half-locked at 2σ, ratcheted at 1×ATR, flat by 21:30.

## A real example — your SELL from today (16:05)

| | What happened (old code) | What v4.3 does (now live) |
|---|---|---|
| Entry | SELL @ 4292.5 approved while the AI was half-blind (window bug: flat indicators, no trend data) | same entry — but the AI now sees the real trend (entry quality = the votes session's job) |
| Stop at birth | ~25 pts (ATR was 0 → emergency fallback) | ~2.4 pts (real ATR 1.6) |
| The +$8.29 morning peak | SL stayed behind entry — a comeback would close negative | gain 8.15 = 5×ATR → **PROFIT_LOCK ratchets immediately, keeping ≥ $4** |
| The CPI rally | trailed, fought, stopped out ≈ −$7.5 | flow + signal turn → **MOMENTUM_EXIT closes at market, in profit** |
| The day | −$27.42, halted at −3% (the brake worked) | the trade never reaches the brake |

Same market, same signal — the difference is entirely in **Part 2**.
