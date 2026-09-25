# Timeframe, depth, and the CFD question
**2026-09-25. Checked against your code. Opinions marked [my view].**

---

## 1. Which timeframe fits this strategy?

**First, stop thinking about "a timeframe". You have four clocks, and they should not be the same.**

| clock | what it is | today | [my view] |
|---|---|---|---|
| **DATA** | how fast book/tape updates arrive | milliseconds, from Bookmap | correct, leave it |
| **DECISION** | how often the robot asks "should I trade?" | one loop cycle | **this is the one that matters** |
| **CONTEXT** | the candles used for ATR, VWAP, POC, value area | **M5** | keep M5 |
| **HORIZON** | how long a judge is given to be right | 15 / 30 / 60 min | see below |

**[my view] Keep M5 for CONTEXT. It is the right choice and I would not change it.** ATR, VWAP,
POC and value area need enough trades per bar to be stable. On M1 they are noisy; on M15 they are
stale for a scalper. M5 on gold is the correct middle.

**But M5 is the wrong clock for DECISIONS in a liquidity strategy.** A wall can appear, get eaten
and vanish inside 40 seconds. If the robot only looks up every 5 minutes, it is reading a map that
was printed before the road changed. The target list must refresh **every cycle**, not every bar.

### The important part: your architecture deletes the horizon problem

Today each judge gets an arbitrary clock — 3 bars, 6 bars, 12 bars. Those numbers were guesses.

In "targets and force" the horizon is **no longer a setting**. It is:

> **"how long should it take to travel the distance to that wall?"**

A wall 0.4 ATR away should be reached in minutes. A wall 2.5 ATR away needs an hour. The horizon is
**derived from the distance**, per trade. That is more honest than any fixed number, and it makes
the prediction testable: *did price reach 2043.6 within the time that distance deserves?*

**[my view] This is the single cleanest thing about your design and I would build it this way.**

---

## 2. How many order-book levels do you actually have?

**Bookmap is not the limit. Your settings are.** Three numbers, and they disagree:

| where | value | what it means |
|---|---|---|
| `bookmap_bridge_provider.py` | `BOOKMAP_MAX_DEPTH_LEVELS`, default **40** | how many levels per side the bridge collects |
| `config.py` | default **40** | same setting |
| `audit_day.py` expects | **20** | so your `.env` is very likely set to 20 |
| **`OrderBookDepthAnalyzer(levels=5)`** | **5** | ⚠ **how many the maths actually reads** |

### ⚠ The finding

Your bridge collects 20–40 levels per side. The analyzer that computes **microprice, imbalance, OFI
and absorption** looks at **the top 5 only**. Everything deeper is received, stored, and never used
by those judges.

For an ordinary momentum robot that is fine — the top of book is where the action is.
**For your wall strategy it is a serious limitation**, because the wall you want to aim at is
usually *not* in the top 5 levels. It is 10, 20, 30 ticks away. That is the whole point of a target.

**[my view] Before building the SIGNAL map, confirm two things on your own machine:**
1. what `BOOKMAP_MAX_DEPTH_LEVELS` is actually set to in your `.env`
2. whether the Bookmap feed is really delivering that many levels, or silently fewer

Gold futures on CME publish full depth, so 20–40 is a *choice*, not a ceiling. If the wall map needs
deeper vision, the number can go up — but more levels means more data per cycle, so it is a
measured decision, not a free one.

---

## 3. Bookmap data, MT5 CFD execution — does it work?

**Yes, and your code already takes it seriously.** This is one of the better-built parts of the app:

- `step4` tracks the **basis** (futures price − CFD price) every cycle
- `BASIS_MAX = 50` refuses to trade when the gap is abnormal
- a **fast-widen check** pauses trading if the basis jumps more than 1% in 5 minutes
- `compute_structural_stops()` converts futures levels to the CFD scale before using them

So the architecture is sound: **read the truth from the futures book, execute where you have an
account.** That is exactly what a professional desk does when it has no depth on its trading venue.

### But three honest warnings

**a) Your CFD has no order book of its own.** Every wall you will ever aim at is a **futures** wall.
It is real, and CFD price follows futures closely — but your TP sits on a level that does not exist
in your broker's book. Usually fine. Occasionally the CFD will wick through a level the futures
respected.

**b) Level conversion is multiplicative, the basis is additive.** The code uses
`scale = cfd_price / futures_price` and multiplies every level by it. Basis is normally described
as a **difference** (futures − spot ≈ $45), not a ratio. The two agree closely near current price
and drift apart further away. For a wall 30 ticks out the error is small — but it is not zero, and
**your TP is decided by exactly that number**. Worth measuring once, not assuming.

**c) Spread and slippage eat scalps.** Your gate is `spread 0.50`. A wall-based TP that front-runs
by a small buffer can be entirely consumed by spread on a fast move. The buffer must be at least a
spread, ideally two.

**[my view] None of these stops the design. All three must be measured before the walls are allowed
to place real stops** — which is exactly why shadow mode exists in the plan.

---

## 4. The process from here — where we are and what comes next

```
DONE     25 judges named, 8 retired, teams split POWER / SIGNAL / ESCORT
DONE     force study built and run on real data
FOUND    the hump: moderate force travels furthest, loudest travels LEAST
                (peak in band 2 at all three horizons, loudest = 54-63% of peak)
NOW      re-run the study with the direction + risk columns (25w)
```

### Step A — finish the measurement *(you, one command, no risk)*
Install 25w, run it on 2026-09-24. Read the **DIRECTION** line.
- above ~55% → force is a real side-picker, the architecture stands as designed
- near 50% → force is a **filter**, not a picker; walls must do all the work (simpler robot, still good)

### Step B — confirm the hump *(needs more days)*
One day is one market. The hump must appear on several days before it changes any rule. Every
trading day from now adds one, automatically, because the diary is already being written.

### Step C — check the depth *(you, two minutes)*
Tell me `BOOKMAP_MAX_DEPTH_LEVELS` from your `.env`, and I will tell you whether the SIGNAL map can
see far enough to be useful.

### Step D — build the SIGNAL map *(me, report-only)*
The list of trusted targets — price, size, distance, trust — logged every cycle and shown in the
report. **Changes no behaviour.** You watch it for a few days and see whether the walls it names
match what you see in Bookmap with your own eyes. That comparison is the real test.

### Step E — build the POWER reading *(me, report-only)*
Using whatever Step A and B tell us: a reach number if force predicts distance, a side-picker or a
filter if it does not.

### Step F — shadow decisions *(me, report-only)*
Every cycle, write what v8 *would* have done beside what the robot actually did. Compare for as long
as you like.

### Step G — hand over the keys *(only after F convinces you)*

Steps A to F change nothing about your trading. Nothing is switched on without your word.
