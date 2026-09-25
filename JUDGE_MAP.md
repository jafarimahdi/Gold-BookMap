# The panel, drawn as a family tree
### Who your 33 judges are, who they are related to, and how a vote actually becomes a trade
Checked against the live code on 2026-09-24: `judge_panel.py`, `step2_market_analysis.py`, `config.py`.
(You said 32 — the real count is **33 active + the AI**. A 34th, `iceberg_noise`, has weight 0.00 and is switched off.)

---

## 1. The family tree, by what they actually LOOK AT

Weight = how loudly a judge speaks. The bracket totals are what matters.

```
┌─ THE TAPE: every trade that printed ────────────────────── 4.10 weight, 7 judges
│
│  footprint_delta   1.00 ┐
│  delta_pressure    0.60 ├─ THE DELTA TWINS ─ 2.70
│  cvd_divergence    0.60 │  all four are cumulative delta,
│  cvd_momentum      0.50 ┘  just sliced differently
│
│  absorption        0.60 ┐
│  footprint_levels  0.40 ├─ tape, but a different question ─ 1.40
│  volume_roc        0.40 ┘
│
├─ THE BOOK: orders resting, waiting ─────────────────────── 5.70 weight, 7 judges
│
│  l3_net_flow       1.50 ┐
│  l3_ofi_streak     1.00 ├─ THE OFI TRIPLETS ─ 3.10
│  l3_large_ofi      0.60 ┘  same order-flow-imbalance sum, 3 windows
│
│  l3_imbalance      0.80 ┐
│  l3_aggr_limit     0.70 ├─ shape of the book ─ 2.60
│  microprice        0.60 │
│  queue_pos         0.50 ┘
│
├─ HIDDEN SIZE: who is sneaking ──────────────────────────── 4.90 weight, 6 judges
│
│  whale_walls       1.40 ── big resting wall
│  sweep             0.90 ── someone ate several levels at once
│
│  iceberg           1.00 ┐
│  iceberg_legacy    0.60 ┘─ THE ICEBERG TWINS ─ 1.60  ← literally the same idea,
│                                                        old and new version, BOTH voting
│  spoof_invert      0.60 ┐
│  spoof_invert_loose 0.40┘─ THE SPOOF TWINS ─ 1.00  ← same idea, two strictness settings
│
├─ STRUCTURE: where price is on the map ──────────────────── 4.70 weight, 8 judges
│
│  vwap_bands        0.80 ┐
│  vwap_trend        0.60 ├─ THE VWAP TRIPLETS ─ 1.90  one line, three questions
│  vwap_zscore       0.50 ┘
│
│  htf_poc           0.70 ┐
│  poc_day           0.50 ├─ THE PROFILE TRIPLETS ─ 1.70  one volume profile, three reads
│  value_area        0.50 ┘
│
│  supply_demand     0.60 ── old zones where price turned
│  mtf               0.50 ── do the bigger timeframes agree
│
└─ THE WORLD: nothing to do with gold's own tape ─────────── 3.30 weight, 5 judges

   news_sentiment    1.00 ── headlines
   macro_yield       0.80 ── bond yields
   macro_dxy         0.60 ── the dollar
   macro_risk        0.50 ── risk-on / risk-off mood
   macro_vix         0.40 ── fear index
```

### The number that should worry you

```
THE TAPE    4.10 ┐
THE BOOK    5.70 ├─ ALL THREE ARE ONE BOOKMAP FEED ── 14.70 of 22.70 = 65%
HIDDEN SIZE 4.90 ┘
STRUCTURE   4.70 ─── also built from that same price+volume ── running total 85%
THE WORLD   3.30 ─── the only genuinely outside opinion ──── 15%
```

**85% of your panel's voice comes out of one pipe.** If Bookmap hiccups, or the book is thin for
ten minutes, 85% of your "independent" judges get confused *at the same moment, in the same
direction* — and the panel will look strongly agreed while actually being one confused source.

---

## 2. How it works — for a five-year-old

**Each judge is a kid in a classroom.** The teacher asks: "Should we buy gold, sell gold, or do nothing?"
Every kid puts a hand up for BUY (+1), DOWN for SELL (−1), or sits still (0).

**Some kids shout louder than others.** `l3_net_flow` shouts with a megaphone of 1.5.
`macro_vix` whispers at 0.4. The teacher counts the shouting, not the heads.

**The teacher's sum, exactly as the code does it:**
```
score = (each kid's answer × his loudness, all added up) ÷ (all the loudness added up)
```
So if everybody screams BUY, score = +1.0. Everybody SELL = −1.0. Half and half = 0.
Then `score × 100` is the **strength** you see in the logs. Above your threshold → BUY,
below → SELL, in between → NEUTRAL.

**Then the AI gets to say no.** The panel's suggestion goes to the AI, which can veto it.
That's why your report has a separate *"AI's veto record"* — the AI never picks trades,
it only blocks them.

**Why the family tree matters.** Imagine four of those kids are triplets who share one pair of eyes.
They always raise the same hand, because they saw the same thing. The teacher hears four voices
and thinks "wow, strong agreement!" — but only *one child actually looked out of the window*.
Your `l3_net_flow` + `l3_ofi_streak` + `l3_large_ofi` are exactly that: **3.10 of shouting from
one order-flow number**. The iceberg twins are worse — the same detector, old version and new
version, both voting.

---

## 3. ⚠ A REAL DEFECT I FOUND WHILE CHECKING THIS

I went looking for how votes are summed and found the robot **already tries** to fix the
family problem. In `config.py`, on by default:

```
V6_4TEAMS_ENABLED = 1        ← this is ON in your build
V6_TEAM_FLOW_WEIGHT   1.5
V6_TEAM_WHALE_WEIGHT  1.4
V6_TEAM_STRUCTURE_W   1.2
V6_TEAM_TREND_WEIGHT  0.8
```

The idea is right: average *inside* each team first, then weigh the four teams. That kills the
duplicate-shouting problem. **But look at how a judge is put into a team** —
`step2_market_analysis.py`, around line 2868:

```python
trend_votes  = votes[:5]
flow_votes   = votes[5:15]
whale_votes  = votes[15:35]
struct_votes = votes[35:45]
world_votes  = votes[45:]
```

**Teams are assigned by POSITION IN A LIST, not by which judge it is.** Vote number 7 is on the
"flow" team because it is seventh — not because it watches flow. The code's own comments admit it:
*"This is approximate"*, *"we use vote magnitudes as proxy"*, *"For simplicity"*, and one loop
that does nothing but `pass`.

Two consequences, and they are not small:

1. **If one judge stays silent, every judge behind him shifts team.** The list gets shorter, so
   vote 15 slides into the flow team and a structure judge becomes a "whale". The teams are
   reshuffled on every single cycle.
2. **`world_votes = votes[45:]` is usually empty**, so your macro and news judges — the only
   genuinely independent opinion you own — frequently land in *nobody's* team.

And this ensemble score is not a side experiment: `score = ensemble_score` is what becomes
direction and strength. **Your live signal is currently built on teams assembled by list position.**

I am telling you this before building anything, because it changes what the correlation matrix is for.
It is no longer just "a nice warning label" — it is **the measurement that tells us the correct team
membership**, by name, from your own recorded votes.

---

## 4. What I would do about it, in order

1. **Measure first.** Build the matrix over your recorded diary. Let the data say which judges move
   together — do not trust my family tree above, it is my reasoning, not your evidence.
2. **Fix the teams by NAME.** Replace those five index slices with an explicit dictionary
   `judge -> team`. Small, safe, testable, no new maths. This is the actual bug fix.
3. **Only then** talk about changing weights or the gate.

Nothing above changes your `.env`. Nothing above touches the running loop.
