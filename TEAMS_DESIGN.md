# Three teams: POWER, SIGNAL, ESCORT
**Design for approval — nothing built. 2026-09-25.**

Your structure, checked against the code and with my recommendations marked **[my opinion]**.

---

## The idea in one picture

```
POWER   "Which way is it going?"        -> decides DIRECTION      (before the trade)
SIGNAL  "Will it be allowed to get there?" -> decides PERMISSION + WHERE  (at the trade)
ESCORT  "Is my trade still safe?"       -> manages the POSITION    (after the trade)
```

Three teams, three different moments. Nobody does two jobs at once.

**Five-year-old version:** POWER is your friends shouting *"go left, the goal is open!"*
SIGNAL is you looking up to check nobody is standing in the way before you kick.
ESCORT is your friends running beside the ball, shouting *"careful!"* or *"keep going!"* until it
crosses the line.

---

# TEAM 1 — POWER · 14 judges · 10.80 weight
### *Everything that has ALREADY happened*
They read finished trades, the candles built from them, and the outside world. **Not one of them can
see an order that is still waiting.**

| judge | w | what it does | its unique power | why it belongs here |
|---|---|---|---|---|
| `footprint_delta` | 1.00 | at every price, were buyers or sellers the impatient ones | knows aggression **shelf by shelf**, not just overall | pure finished trades |
| `sweep` | 0.90 | spots one order that ate several price levels at once | sees **urgency that paid a worse price on purpose** | built from candles — I checked, it never reads the book |
| `l3_aggr_limit` | 0.70 | ratio of impatient buying to impatient selling | measures **impatience itself** | ⚠ the name says "limit" but the maths uses only executed volume |
| `cvd_divergence` | 0.60 | price makes a new high while buying pressure does not | the only one that **reports a disagreement** and votes against the move | finished trades |
| `absorption` | 0.60 | a big push that produces **no** price movement | the only one for which **nothing happening is the signal** | ⚠ **bridge** — needs the wall *and* the trades eating it |
| `cvd_momentum` | 0.50 | is the day's buying speeding up or fading | knows the **direction of travel of the whole day** | finished trades |
| `footprint_levels` | 0.40 | how many price levels lean buy vs sell | **breadth, not size** — twelve small buys beat one big one | finished trades |
| `volume_roc` | 0.40 | sudden jump in traded volume | the **"something just woke up"** alarm | finished trades |
| `vwap_bands` | 0.80 | how many deviations from the fair price | knows **how far is too far** and fades it | candles |
| `htf_poc` | 0.70 | the 1h/4h price where most volume traded | the only **memory older than today** | candles |
| `vwap_trend` | 0.60 | above or below the volume-weighted average | the fairest **"expensive or cheap today"** line | candles |
| `supply_demand` | 0.60 | old zones where price turned hard | **pure memory of past reactions** | candles |
| `poc_day` | 0.50 | today's most-traded price | today's **centre of gravity** | candles |
| `value_area` | 0.50 | inside or outside the 70%-volume zone | knows **normal from abnormal** | candles |
| `mtf` | 0.50 | do M5, M15 and H1 agree | a **referee, not a player** — it judges other clocks | candles |
| `news_sentiment` | 1.00 | high-impact headlines and their tone | the only judge that knows **why**, and the only one that sees **forward** | outside world |
| `macro_risk` | 0.50 | risk-on / risk-off mood | the **weather of all markets** | outside world |

**[my opinion]** POWER is what every chart trader on earth already has. It is necessary, it is not
your edge, and it should never be allowed to fire a trade on its own.

---

# TEAM 2 — SIGNAL · 8 judges · 7.00 weight
### *Orders WAITING, that have not happened yet*
Every one of these reads resting liquidity — size sitting in the book, appearing, being pulled,
refilling, or lying. **This is the only team that can see the future.**

| judge | w | what it does | its unique power | five-year-old |
|---|---|---|---|---|
| `l3_net_flow` | 1.50 | orders being added and pulled on each side | sees **intentions appear and vanish** — a cancelled order leaves no trace on the tape | who is walking toward the queue, and who is walking away |
| `whale_walls` | 1.40 | unusually big resting walls, **and their exact price** | finds the **visible giants** — a floor or ceiling built on purpose | the big kid standing in the doorway |
| `iceberg` | 1.00 | an order that keeps refilling after being eaten | detects **hidden size** — showing 5, holding 500 | the sweet jar that looks empty but refills when you look away |
| `l3_imbalance` | 0.80 | how much size rests on each side, nearest counting most | the **shape of the wall right now** | which side of the tug-of-war rope has more hands |
| `l3_large_ofi` | 0.60 | the same flow, counting only big orders | **filters out the small fry** — hears only the adults | ignoring the little kids, listening to the big ones |
| `microprice` | 0.60 | the true price implied by size on each side | the **earliest warning that exists**, before price moves at all | the seesaw is level, but more kids are climbing one side |
| `spoof_invert` | 0.60 | big orders that appear then vanish without trading | the only judge that **votes the opposite of what it sees** — it reads lies | a boy shouts "ice cream!" so you run, and he takes your seat |
| `queue_pos` | 0.50 | whether our order lands at the front or back of the queue | the only judge thinking about **us**, not the market | are we first in line or last |

**[my opinion]** This team is the entire reason you pay for Bookmap. It should not be averaged into
POWER — it answers a different question. **Two of these already know exact prices** (`whale_walls`
gives the closest wall price, `iceberg` gives refill levels), which is what makes the wall-aware
TP/SL possible at all.

---

# TEAM 3 — ESCORT · 14 judges · asleep until a position opens
### *Runs beside the trade until TP or SL*

**You already have an escort — it just has no names.** `PositionManager.manage()` runs every cycle
and already does: `ADOPT_TIGHTEN`, `BE` (stop to break-even), `TRAIL`, `TP_UPDATE`, `FLIP_EXIT`,
`DIVERGENCE_EXIT`, `TIME_STOP`, news threat, session flatten. What it lacks is a **named panel** — so
the audit can never tell you *which* escort judge saved or cost you money.

Escort is organised by **job**, not by data source. A judge can serve in POWER or SIGNAL and still
escort — same person, different shift.

### Job 1 — "Is my exit still clear?" (the wall watchers)
| judge | what it watches while you are in the trade |
|---|---|
| `whale_walls` | a **new** wall appears before your TP → take profit earlier, don't wait for a fill that will never come |
| `iceberg` | hidden size defending your target → the wall is bigger than it looks, get out first |
| `spoof_invert` | the wall you are aiming at is **fake** → it will vanish, your TP can run further |

### Job 2 — "Is the crowd turning against me?" (the early-warning guards)
| judge | what it watches |
|---|---|
| `microprice` | the very first tilt against you, in fractions of a penny — **the earliest exit signal you own** |
| `l3_net_flow` | orders massing on the other side |
| `l3_imbalance` | the book's shape flipping against your direction |

### Job 3 — "Is my push dying?" (the momentum doctors)
| judge | what it watches |
|---|---|
| `cvd_divergence` | price still goes your way but nobody is pushing → **already wired as `DIVERGENCE_EXIT`** |
| `absorption` | someone is quietly eating your move → it will stall |
| `footprint_delta` | aggression flips against you → **already wired as `FLIP_EXIT`'s confirmation** |
| `sweep` | a sweep fires against your position → get out now, not later |

### Job 4 — "Where do I move my stop to?" (the rails)
| judge | what it provides |
|---|---|
| `supply_demand` | the zone to hide the stop behind as price advances |
| `poc_day` | today's magnet — a natural trail rail |
| `htf_poc` | the bigger magnet, for trades that run |

### Job 5 — "Is a bomb coming?"
| judge | what it provides |
|---|---|
| `news_sentiment` | a high-impact event approaching → tighten or flatten **before** it lands |

### Deliberately NOT in Escort — and why
- `queue_pos` — it only matters **before** you are filled. Once you are in, it has nothing to say.
- `footprint_levels`, `volume_roc`, `cvd_momentum` — too slow and too noisy for minute-by-minute management.
- `vwap_trend`, `vwap_bands`, `value_area`, `mtf`, `macro_risk` — they barely move inside one trade;
  they would produce constant chatter and no decisions.
- `l3_large_ofi` — already covered by `l3_net_flow`; two voices saying one thing is the mistake we just removed.

---

## **[my opinion]** Three rules I would insist on for Escort

**1. Escort may only ever make the trade SAFER.** It can move the stop closer, take profit earlier,
close, or take partial profit. It may **never** widen a stop, move a target further away while the
trade is losing, or add size. An escort that can loosen protection is not an escort — it is a second
gambler. *(Your `TRAIL` already enforces "only ever tightens". I would extend that rule to the whole
team.)*

**2. Escort must be graded separately.** A new report section: *"what did the escort do today, and
was it right?"* — every early exit compared against what the trade **would** have done if left alone.
This is the only way to find out whether your escort is protecting you or panicking. My honest
expectation: it will save you on some days and cut winners short on others, and you will want to
tune it. You cannot tune what you cannot see.

**3. Escort speaks with one voice per job, not fourteen.** Five jobs → at most five recommendations
per cycle, each with the judge's name attached. Otherwise you get the same shouting problem we just
removed from the panel, only now it is shouting at an open position with your money in it.

---

## DECISIONS TAKEN 2026-09-25

1. **`sweep` and `l3_aggr_limit` move to POWER** — approved. The code reads candles and executed
   volume, not the book.
2. **`absorption` goes to SIGNAL, not a bridge** — corrected after reading the implementation. It
   reads **only** the order book (best bid / best ask size), never the tape. My earlier "bridge"
   suggestion was wrong.
3. **ESCORT approved as designed, deferred.** Build it after POWER and SIGNAL.
4. **The "only ever safer" rule deferred** with the Escort build.

## ⚠ DEFECT FOUND IN `absorption` (2026-09-25, not yet fixed)

`absorption_net` is initialised to 0 **once**, in `__init__` (step2 line 614), and the L2 analyzer is
deliberately persistent across cycles. Nothing ever resets it. The vote is
`clip(absorption_net * 0.5, -1, 1)` at weight 0.60 — so **two net alarms pin the vote at full
strength for the rest of the session**. It reports the whole morning's running total as if it were
the current minute.
**Fix options:** reset per cycle, or decay it so recent alarms dominate. Live-loop change — needs approval.
**Also note:** the name is misleading. It treats a shrinking bid wall as *bearish* (support being
destroyed). That is wall **erosion**, not textbook absorption, which would read a held bid as bullish.



1. **`absorption`** — POWER, SIGNAL, or a bridge judge both can see? *(my suggestion: bridge)*
2. **`sweep` and `l3_aggr_limit` moved to POWER** — agreed? The code says they read candles and
   executed volume, not the book.
3. **The Escort roster above** — anyone to add or remove?
4. **The "only ever safer" rule** for Escort — agreed?
