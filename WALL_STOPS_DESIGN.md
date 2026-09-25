# Wall-aware stops — the design
### "Look where your friend is standing before you shoot"
Written 2026-09-25, after reading the live code. Nothing built yet. Target build: **24v**.

---

## 1. Your idea, in one line

At the exact moment a BUY/SELL is about to be sent, ask the L3 helpers:
**"Where is the wall in front of me, and where is the wall behind me?"**
Put the take-profit **just before the front wall**, the stop-loss **just behind the back wall** —
and if there is no room between them, do not shoot at all.

---

## 2. What already exists (more than you'd think)

`position_manager.compute_structural_stops()` — called by `step4` at line 624, right before the
order goes out. Its own header already says:

> *"A take-profit placed past a wall of resting liquidity never fills because price reverses AT the wall."*

And it already does the front-running:

```python
tp = lvl - sign * config.PM_TP_BUFFER_ATR * atr
notes.append(f"TP front-runs {name} {lvl:.2f}")
```

It even pushes the SL behind round numbers, because *"that is exactly where a liquidity hunt reaches."*

**So the mechanism is built. It is asking the wrong friend.**

## 3. What is missing

The levels it considers are only these:

| source | what it is |
|---|---|
| `demand` / `supply` | order blocks — where price turned **in the past** |
| `vps` | volume nodes / POC — **history** |
| `rr` | round numbers — **arithmetic** |

Not one of them is live order-book data. Meanwhile step2 computes exactly what you asked for, and
then throws it away:

```python
whale_bids / whale_asks   # list of (price, size, distance) — REAL walls, right now
closest = min(whale_bids, key=lambda x: x[2])
notes.append(f"... closest {closest[0]:.1f} dist {closest[2]*100:.2f}% ...")
```

`closest[0]` **is the price of the wall**. It gets formatted into a sentence and discarded.

**Football version:** the robot does look up before shooting — but at where your friend stood five
minutes ago, not where he is now. And it never waits for his hand to go up.

## 4. The three pieces to build

### Piece 1 — stop throwing the wall prices away
New field on the Level3 dataclass (next to `iceberg_levels`, which already works this way):

```python
wall_levels: List[Dict] = field(default_factory=list)
# each: {"price": float, "size": float, "side": "bid"|"ask",
#        "dist_pct": float, "age_sec": float, "score": float, "spoofed": bool}
```

Filled in step2 at the point where `whale_bids` / `whale_asks` already exist. Pure addition —
no existing behaviour changes.

### Piece 2 — let the stop calculator see them
In `compute_structural_stops()`:
- **TP**: add opposing-side walls to the existing `opposing` list. The function already picks the
  nearest and subtracts `PM_TP_BUFFER_ATR`. No new maths.
- **SL**: add same-side walls to the structure list, so the stop goes **behind** the wall that is
  protecting us — a wall is a floor, and we want to be wrong only if the floor actually breaks.

### Piece 3 — the confirmation before the shot (this is the new part)
In `step4`, before sending:

```
front_wall = nearest opposing wall that passes the trust filter
if front_wall is closer than PM_TP_MIN_ATR * atr:
        -> SKIP the trade, reason: "front wall 2043.6 (180 lots) too close, no room to run"
back_wall  = nearest same-side wall behind entry
if back_wall exists:
        -> SL goes behind it + buffer, instead of the ATR guess
```

There is already a precedent for asking L3 at this moment — step4 line 821:
*"Decision 4: Spoof check — don't place limit where spoof wall exists (trap)."*
So this is finishing something that was started, not inventing a new habit.

---

## 5. ⚠ The trust filter — without this, the feature makes you WORSE

Walls lie. A fake wall is the whole reason `spoof_invert` exists. A TP parked in front of a wall
that vanishes gives away money; an SL tucked behind a fake floor is worse.

Good news: everything needed is **already computed**.

| filter | data that already exists | rule |
|---|---|---|
| **Is it real?** | `iceberg_meta[price]["age_sec"]` and `["score"]` — step2 comment: *"real institutional walls (2h) vs algo noise (30s)"* | must be older than `PM_WALL_MIN_AGE_SEC` |
| **Is it a known liar?** | `spoof_levels: Dict[price, count]` | any price in there is **excluded outright** |
| **Is it big enough?** | wall size in lots | must exceed `PM_WALL_MIN_LOTS` |
| **Is it relevant?** | `dist_pct` | ignore walls further than `PM_WALL_MAX_DIST_ATR` |

A wall that fails any of these is invisible to the stop calculator — we fall back to today's
behaviour, which still works.

## 6. Switches (all default OFF — nothing changes until you say so)

```
PM_WALL_STOPS_ENABLED=0     master switch
PM_WALL_MIN_AGE_SEC=120     a wall must have survived 2 minutes to be believed
PM_WALL_MIN_LOTS=50         smaller than this is noise
PM_WALL_MAX_DIST_ATR=3.0    walls further away are not our problem
PM_WALL_SKIP_IF_BLOCKED=1   skip the trade when the front wall leaves no room
```

## 7. ⚠ Why this one needs more care than everything before it

Every change we have shipped so far touched **reporting**. This one changes **what the robot does
with your money** — where the stop sits, and whether a trade happens at all.

So the plan is not "ship and watch":

1. **Shadow mode first.** With `PM_WALL_STOPS_ENABLED=0`, the robot still *computes* the wall-based
   SL/TP and writes both into the diary next to the real ones. It changes nothing; it only records
   what it *would* have done.
2. **The audit compares them.** A new report section: *"wall stops vs ATR stops, on the same trades"*
   — how often the wall TP would have filled when the ATR TP did not, and how often the wall SL
   saved or cost points.
3. **Switch on only if the evidence says so.** After several days of shadow data, not after one.

That way the decision is made by your tape, not by my reasoning — the same rule we used for the
retirements.

## 8. Honest risks

- **Fewer trades.** Rule 3 will refuse trades with no room to run. That is the point, but the count
  will drop and the first day will look "quiet".
- **Walls move.** A wall valid at entry can vanish a minute later. The SL/TP is already placed by
  then. Shadow mode will show how often this happens.
- **Bookmap depth gaps.** If the feed thins, `wall_levels` is empty and we silently fall back to
  today's behaviour. That must be *logged*, not silent — same rule as the macro brake.
- **It is not a magic upgrade.** It moves the stop to a smarter place; it does not make a bad signal
  good.

## 9. What I need from you before building

1. **Shadow mode first — yes or no?** (My strong recommendation: yes.)
2. **Should a blocked front wall SKIP the trade, or just shrink the TP?** Skipping is safer but
   costs trades; shrinking keeps them but takes smaller profits.
3. **Walls for the SL too, or TP only in the first version?** TP-only is about half the work and
   half the risk.
