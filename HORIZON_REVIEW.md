# The clocks: does each judge's time frame fit a 5-minute scalper?
Read from `JUDGE_HORIZON` in `audit_day.py` and the robot's real exit rules in `config.py`, 2026-09-24.

---

## 1. What "the clock" actually is

Each judge has four numbers: `(bars, threshold, SL, TP)`.

```
"footprint_delta": (3, 0.5, 1.0, 1.5)
                    │   │    │    └── graded on a trade with TP = 1.5 × ATR
                    │   │    └─────── ... and SL = 1.0 × ATR
                    │   └──────────── "quick win" = price moved 0.5 × ATR
                    └──────────────── it has 3 M5 bars = 15 minutes to be right
```

**Five-year-old version:** every kid gets a different amount of time to be proved right.
The fast kid gets 15 minutes. The macro kid gets 2 hours. Then each is marked on his own clock.
That part is **correct and fair** — marking a 2-hour opinion on a 15-minute stopwatch would be nonsense.

So: the clocks are all **multiples of your M5 chart** — 3 bars, 6 bars, 12 bars, 24 bars. Nothing is
off-grid. Your chart and your judges agree.

## 2. The four speed groups

| Bars | Time | Judges | Fit for scalping |
|---|---|---|---|
| **2** | 10 min | `microprice` | ✅ perfect — fastest signal you own |
| **3** | 15 min | `footprint_delta`, `footprint_levels`, `l3_imbalance`, `l3_net_flow`, `l3_large_ofi`, `l3_aggr_limit`, `queue_pos`, `absorption` | ✅ this is the real scalping core |
| **6** | 30 min | `iceberg`, `spoof_invert`, `whale_walls`, `sweep`, `cvd_momentum`, `volume_roc` | ✅ correct — hidden size takes time to show |
| **12** | 60 min | `vwap_trend`, `vwap_bands`, `poc_day`, `value_area`, `supply_demand`, `cvd_divergence`, `mtf` | ⚠️ 12 bars is a long time for a scalp |
| **24** | 120 min | `htf_poc`, `news_sentiment`, and the macro judges | ❌ not a scalping clock at all |

**My opinion:** groups 2/3/6 are well chosen and genuinely fit a 5-minute scalper. I would not
change a single number there. `microprice` at 2 bars is exactly right — it is your earliest warning,
so it gets the shortest rope.

---

## 3. ⚠ THE REAL PROBLEM — and it is not the one you expected

Here is what your robot actually does when it takes a trade (`config.py`):

```
STOP_LOSS_ATR_MULT    = 2.0
TAKE_PROFIT_ATR_MULT  = 3.5
```

Now compare that to how your **best** judges are graded:

| | SL | TP | same trade as the robot? |
|---|---|---|---|
| **The robot, for real** | **2.0** | **3.5** | — |
| `footprint_delta`, `l3_imbalance` | 1.0 | 1.5 | ❌ less than half the target |
| `l3_net_flow` | 1.5 | 2.0 | ❌ |
| `microprice` | 1.0 | 1.0 | ❌ tiny |
| `vwap_trend`, `poc_day` | 2.0 | 3.0 | ⚠️ close |
| `htf_poc`, `macro_yield`, `macro_dxy` | **2.0** | **3.5** | ✅ **exact match** |

**Your scalping judges are being marked on a trade your robot never takes.**

`footprint_delta` is graded on "did price move 1.5 × ATR in 15 minutes". Your robot needs
**3.5 × ATR**. A judge can be right, collect a good score, and the robot's actual take-profit never
fills — price went the right way and then stopped, well short of the real target.

**Five-year-old version:** you are marking the kids on whether the ball reached the fence, but the
goal is twice as far away. A kid can score 83% on the fence test and never once score a real goal.

That is why `vwap_trend` shows 83% right and still only +19.5 points, and why `footprint_delta`
is 68% right and **−44.1 points**. It is not a contradiction or a bug — the percentage answers a
smaller question than the money does. The report already tells you this; now you know why.

**And the irony worth stating plainly:** the only judges currently graded on the exact trade your
robot really places are `htf_poc`, `macro_yield` and `macro_dxy` — two of which you have decided to
delete. I am not reopening that decision. I am telling you that when they disappear, the honest
"graded like the real robot" column disappears with them.

---

## 4. My suggestion — the dual clock

Do **not** change the per-judge horizons. They are fair as they are, and if you flatten everyone to
15 minutes you will make the slow judges look artificially terrible.

Instead, grade every judge **twice** and show both columns:

| column | SL/TP | question it answers |
|---|---|---|
| **own clock** (today) | each judge's own | *Was this judge's reading of the market correct?* |
| **robot clock** (new) | always 2.0 / 3.5 | *Would following this judge have made the robot money?* |

A judge that is high on both is genuinely good. High on "own clock" but low on "robot clock" means
**the judge is right but too early or too small** — the reading is fine, the exit rules do not suit
it. That is a completely different problem from a judge that is simply wrong, and right now you
cannot tell those two apart.

**Cost:** one extra `simulate()` call per vote in the audit. Report-side only, no live-loop change,
no `.env` change. It uses data you already record.

**My honest expectation:** the robot-clock column will be uglier than the current one, and it will
reorder the ranking. It is the number that matches your account, so it is the one worth having.

## 5. Direct answers to your three questions

- **Do the clocks fit a 5-minute chart?** Yes — every clock is a whole number of M5 bars. Nothing
  is misaligned with your chart.
- **Are they a good choice for a scalper?** For the 2/3/6-bar judges, yes, and I would not touch
  them. The 12-bar and 24-bar judges are not scalping clocks, and you are right that they answer a
  different question from the one your robot is asking.
- **My suggestion:** leave the clocks alone, and add the robot-clock column instead. The clocks are
  not what is misleading you — the **SL/TP mismatch** is.
