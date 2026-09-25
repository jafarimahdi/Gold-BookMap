# Architecture v8 — "Targets and Force"
**Owner's design, 2026-09-25. Written up for approval. Nothing built.**

---

## 1. The idea

```
SIGNAL TEAM   ->  "Here are the targets. This is where the orders are waiting,
                   how big they are, how far away, and whether I trust them."

POWER  TEAM   ->  "This is how much force the market has right now.
                   That is enough to reach the target above / below / neither."

DECISION      ->  trade only when force >= distance to a trusted target.
                  TP = just before that target.  SL = just behind the wall on the other side.

ESCORT TEAM   ->  later. Not part of this build.
```

**Five-year-old version:** SIGNAL is the map — *"there is a sweet shop 300 metres that way, and a
wall 50 metres the other way."* POWER is your legs — *"I can run about 200 metres before I get
tired."* You only run for the sweet shop if your legs can actually get you there. If both are too
far, you sit down. Sitting down is a decision too.

## 2. Why this is better than what the robot does today

Today the robot averages 25 opinions into a percentage and trades when the percentage beats 50.
That number cannot be checked against reality — there is no such thing as "the market was 58% today".

Your design produces a **prediction that reality can judge**:

> *"There is a 180-lot wall at 2043.6, which is 1.4 ATR away. Force is 1.9 ATR over the next
> 15 minutes. That is enough. Buy, TP 2043.2, SL 2039.1."*

Fifteen minutes later the tape answers with a plain yes or no: **did it reach 2043.6?**
That is gradeable, tunable and honest. It is the single biggest improvement available to this robot.

## 3. What each team must produce

### SIGNAL — the liquidity map (updated every cycle)
A list, not a vote:

| field | from | why |
|---|---|---|
| `price` | `whale_walls` (already knows the exact price), `iceberg` refill levels | the target |
| `size` | wall lots | how hard it will be to break |
| `side` | bid / ask | is it above or below us |
| `distance` | price − current, in ATR | how far the legs must carry us |
| `trust` | `iceberg_meta.age_sec` + persistence score, minus anything in `spoof_levels` | is it real or a lie |
| `pressure` | `l3_net_flow`, `l3_imbalance`, `l3_large_ofi`, `microprice`, `absorption` | is this wall growing or being eaten |

Nine judges, one map. **No direction vote at all** — SIGNAL never says buy or sell. It says *where*.

### POWER — the force reading (updated every cycle)
One number with a sign, and it must be in the **same units as distance** (ATR over the horizon):

```
force = how far this market can travel in the next N minutes, and which way
```
Built from the 16 POWER judges: aggression (`footprint_delta`, `sweep`, `l3_aggr_limit`), whether
the push is alive or dying (`cvd_divergence`, `cvd_momentum`, `volume_roc`), and the map context
(`vwap_*`, `poc_day`, `value_area`, `htf_poc`, `supply_demand`, `mtf`) plus the world
(`news_sentiment`, `macro_risk`).

**This is the hard part, and it is where I want to be honest with you:** nobody can write that
conversion from a chair. "Delta of +400 means price travels 1.6 ATR" has to be **measured from your
own tape**, not guessed. See section 5.

### THE DECISION
```
for each trusted target in the direction of force:
        if force >= distance * SAFETY:        -> trade it
                TP = target − buffer           (just before the wall)
                SL = behind the nearest opposite trusted wall
if no target is reachable:                    -> no trade, and say why
```
Natural, honest outcomes fall out of this for free:
- **force strong, wall very close** → no room → no trade (your original wall idea)
- **force weak, wall far** → not reachable → no trade
- **no trusted wall either side** → nothing to aim at → no trade
- **walls both sides, force one way** → the classic good setup

## 4. What this replaces

| today | v8 |
|---|---|
| `confidence = agreeing judges / all judges` (a head count) | `reach ratio = force / distance` (a physical quantity) |
| gate at 50% — meaning unclear | gate at reach ratio ≥ 1.0 × safety — meaning obvious |
| TP/SL from ATR multiples | TP/SL from where the liquidity actually is |
| "was the signal right?" (vague) | "did price reach the target in time?" (yes/no) |

The 4-teams-by-list-position bug disappears, because teams are named.
The head-count gate disappears, because the gate becomes physical.
The duplicate-shouting problem shrinks, because SIGNAL stops voting entirely.

## 5. ⚠ MY STRONGEST RECOMMENDATION — measure before you build

The whole design rests on one assumption:

> **more force = more distance travelled.**

If that is not true on your gold tape, the architecture cannot work — and we would find out after
weeks of building. So **step one is not code in the robot. It is a measurement on days you have
already recorded.**

For every diary snapshot you already have, we know the force ingredients (delta, OFI, aggression)
and, from `ticks.csv`, exactly how far price then travelled in the next 15/30/60 minutes. So we can
plot the real answer:

```
force reading   ->   distance actually travelled
```

Three possible outcomes, and all three are worth knowing:
- **Strong relationship** → we have the conversion, and it is calibrated from your market, not a textbook.
- **Weak relationship** → force predicts direction but not distance. Then TP must come from walls
  only, and force just picks the side. Still useful, simpler design.
- **No relationship** → we learned it now, cheaply, instead of after building it.

**This measurement is report-only.** It touches nothing live, risks nothing, and uses data already
sitting on your disk. It is the cheapest and most valuable thing we can do next.

## 6. My honest list of what is hard

1. **Walls are magnets AND barriers.** Price is drawn toward liquidity, then stops at it. The nearest
   opposing wall is a **ceiling** (that is your TP). Liquidity beyond it is a **magnet** but only once
   the ceiling breaks. The design must not confuse the two.
2. **Walls vanish.** A wall present at entry can be gone a minute later. Trust filter + Escort later.
3. **Thin book days.** No trusted walls means no targets means no trades. That is correct behaviour,
   but it will feel broken the first time. It must be logged loudly: *"no trusted target, not a fault."*
4. **Force is not one number for all horizons.** Force over 15 minutes is not force over 60. Likely
   we need force at 2-3 horizons and match each against targets at matching distance.
5. **Fewer trades.** Almost certainly. If today's robot trades on a 51% vote with no target in mind,
   many of those will now correctly be refused.

## 7. Proposed order of work

| step | what | touches live loop? |
|---|---|---|
| **1** | **Measure force vs distance** on recorded days. Produces a chart and a number. | **no** |
| **2** | Build the SIGNAL map (list of trusted targets) and **log it only** — no behaviour change | no |
| **3** | Build the POWER force reading with the calibration from step 1, **log it only** | no |
| **4** | Shadow decision: every cycle, write what v8 *would* have done next to what the robot did | no |
| **5** | Compare on real days. Only then switch the decision over. | yes, at the end |

Steps 1-4 change nothing about your trading. You would be able to watch the new brain thinking
next to the old one for as long as you like before handing it the keys.
