# The judge system, from zero — design v8
**Written 2026-09-25. NOTHING BUILT. This is for your approval first.**
Supersedes nothing until you say so. Current live build stays `audit-2026-09-24u`.

You said the judges redesign and the wall idea are the same job. They are — the judges decide
**which way**, the walls decide **where and whether**. One pipeline, designed once.

---

## 1. What is wrong with the system we have

Five things, all measured from your code, all in `HANDOVER.md`:

1. **Teams are grouped by list position** (`votes[:5]`, `votes[5:15]`...). One silent judge reshuffles
   everybody. The live signal runs on this.
2. **The gate counts heads, not weight.** A 0.4 judge cancels a 1.5 judge one-for-one.
3. **Duplicates shout.** Five order-book judges were 4.6 of weight on one idea.
4. **Weights float up to ~4×** by regime, hour and volatility, so the roster numbers are fiction.
5. **Judges are graded on a trade the robot never takes** (TP 1.5 ATR vs the real 3.5).

## 2. The one idea that fixes most of it

**ONE IDEA, ONE VOTE.**

Today 25 judges each shout, and whichever idea has the most judges wins — regardless of whether
those judges are five clones or five independent thinkers.

Instead: judges are put into **named families**. Every family debates internally and comes out with
**one** opinion. Then the families vote — five voices, not twenty-five.

**Five-year-old version:** instead of 25 children shouting at once, you have 5 tables. Each table
talks among itself and one child stands up to speak for the table. Now you can actually hear who
disagrees with whom — and the table with 7 children doesn't automatically beat the table with 2.

This kills the duplicate problem **by design**, without waiting for the correlation matrix. The
matrix becomes a checking tool afterwards, not a prerequisite.

## 3. The five families (25 judges after your retirements)

```
TAPE — what actually traded                     BOOK — orders resting and waiting
  footprint_delta   1.00                          l3_net_flow       1.50
  cvd_divergence    0.60                          l3_imbalance      0.80
  absorption        0.60                          l3_aggr_limit     0.70
  cvd_momentum      0.50                          l3_large_ofi      0.60
  footprint_levels  0.40                          microprice        0.60
  volume_roc        0.40                          queue_pos         0.50

HIDDEN — who is sneaking                        MAP — where price is
  whale_walls       1.40                          vwap_bands        0.80
  iceberg           1.00                          htf_poc           0.70
  sweep             0.90                          vwap_trend        0.60
  spoof_invert      0.60                          supply_demand     0.60
                                                  poc_day           0.50
WORLD — outside opinion                          value_area        0.50
  news_sentiment    1.00                          mtf               0.50
  macro_risk        0.50
  (macro_yield / macro_dxy / macro_vix retired -> they feed the brake only)
```

Inside a family, weights still matter — `l3_net_flow` outvotes `queue_pos` at its own table.
Between families, **you** set how loud each table is. Starting proposal, for your approval:

```
TAPE   1.2     what really traded is the strongest evidence
BOOK   1.2     equally strong, and it is your edge over chart traders
HIDDEN 1.0     powerful but rarer, and easier to fake
MAP    0.8     context, not a trigger
WORLD  0.5     slow, but the only outside voice you have left
```

**A family with no votes is skipped entirely** — it does not count as "disagreeing". That alone
fixes the `world_votes = votes[45:]` bug, where an empty team silently dragged the score down.

## 4. The gate stops counting heads

Today: `confidence = agreeing judges / all judges`.
New: **confidence = weight of agreeing families / weight of families that spoke.**

So "58%" finally means *"58% of the opinion that actually spoke agrees"* — not *"58 out of 100
raised hands, some of them twins"*.

⚠️ **This changes what your 50 gate means.** We keep the old number in the log next to the new one
for a few days so you can see both before re-tuning. No silent change.

## 5. Then the walls — the last question before the shot

Once direction and strength are decided, and only if the gate passes:

```
1. Where is the nearest OPPOSING wall?   -> that is the ceiling. TP goes just before it.
2. Where is the nearest wall BEHIND me?  -> that is the floor.  SL goes just behind it.
3. Is there room between them?           -> if not, DO NOT SHOOT. Log the reason.
```

A wall is only believed if it passes the trust filter — old enough (`iceberg_meta.age_sec`), big
enough, not on the known-liar list (`spoof_levels`). All that data already exists.

**Your football picture:** the judges are your teammates shouting "go left!". The wall check is you
looking up to see whether your friend is actually open before you kick.

## 6. How we build it — 5 steps, each one stops for your approval

| step | what | touches the live loop? | risk |
|---|---|---|---|
| **0** | **Freeze the weights** — 4 lines in *your* `.env` | yes, on restart | none, reversible by deleting the lines |
| **1** | **Name the families** — replace the positional slices with an explicit `judge -> family` dictionary | yes | low, it is a bug fix either way |
| **2** | **One idea, one vote + weighted gate**, old numbers logged beside new | yes | medium — this changes trade decisions |
| **3** | **Dual-clock grading** in the audit (own clock + robot clock 2.0/3.5) | **no** | none, report only |
| **4** | **Wall-aware SL/TP + no-room veto**, shadow mode first | no while shadow | medium when switched on |

Each step = **one zip**, selftest green, sizes given, and I stop and wait. Nothing moves to the next
step without you saying so.

**Suggested order and why:** 0 and 1 first because they cost nothing and make everything else
measurable. Then 3 (report-only, so you can *see* before you change). Then 2. Then 4 last, because it
spends money.

## 7. What I need you to approve

1. **The five families** — is any judge in the wrong one?
2. **The family weights** — TAPE 1.2 / BOOK 1.2 / HIDDEN 1.0 / MAP 0.8 / WORLD 0.5, or different?
3. **Step 0 now?** The four `.env` lines, so we design on numbers that hold still.
4. **The build order** — 0, 1, 3, 2, 4 as above, or do you want the walls sooner?

Nothing is written until you answer. Current build on GitHub stays untouched and green.
