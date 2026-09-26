# The Scout (SIGNAL team) — explained simply
**What it is, what it does every minute, and what each of the nine judges contributes.**
Live since 2026-09-25. Module: `signal_team.py`. Casts no vote, sends no order.

---

## 1. The picture

Your robot is a boy walking down a long hallway. Price is where he stands.

Along the hallway there are **doors** — piles of orders waiting at a price. Some doors
are thick (lots of people), some are thin (two people). Some are **painted on the wall**
and aren't doors at all. Some look thin but have **a big man behind them** who steps back
in every time you push.

The Scout's only job: **walk ahead, write down every door, and keep the list true.**

He never says "go left" or "go right". That is the job of the legs (POWER).

---

## 2. What the Scout does every 60 seconds

```
1. LOOK      at the order book, the icebergs and the known liars
2. WRITE     any new door into the notebook
3. UPDATE    doors he already knows: is it thicker? thinner? still there?
4. MARK      doors people are chewing on
5. CROSS OUT doors proven to be painted on
6. FORGET    doors that stopped appearing
7. HAND OVER the three best doors above and the three best below
```

Step 3 is the whole point. **Looking once tells you how thick a door is. Looking every
minute tells you what it is doing** — and that is worth far more.

---

## 3. The notebook — what he remembers about each door

| he writes down | why it matters |
|---|---|
| **price** | where the door is |
| **side** | above him (ceiling) or below him (floor) |
| **how thick, now** | a big pile is harder to push through |
| **how thick, when first seen** | so he can tell growing from melting |
| **how many times he has seen it** | been there all morning, or arrived 30 seconds ago |
| **how old it is** | same idea, in seconds |
| **how often people chewed on it** | a door under attack is about to fall |
| **who spotted it** | one judge, or two agreeing |
| **is it painted on** | if yes, it is deleted entirely |
| **cycles missing** | it fades out, it does not vanish instantly |

---

## 4. The three judges who CREATE doors

They are the only ones who can name a price.

**`whale_walls` — "there is a big pile here."**
Reads the resting orders. Anything at or above 10 lots is a door.
*Why 10?* Measured from your own tape: a typical price holds 2 lots, the top 1% hold 11,
and the biggest ever seen was 53. Ten is genuinely big for gold.

**`iceberg` — "this one has a big man behind it."**
Spots a door that keeps refilling after being eaten. Someone is showing 5 lots and
holding 500. **Nothing else in the robot can see this** — it leaves no trace on the tape.
When iceberg confirms a door the Scout already knows, its hold score jumps (0.33 → 0.49).

**`spoof_invert` — "that door is painted on."**
Spots big orders that appear then vanish without ever trading. **The door is deleted
from the map.** Not lowered, not doubted — deleted. In testing, a 25-lot ceiling, the
biggest thing on the map, disappeared completely the moment it was flagged.
*A known liar is not a weak signal. It is not a signal.*

---

## 5. The six judges who DESCRIBE doors

They cannot name a price. They tell you about the doors that already exist.

**`l3_net_flow` — "is that pile growing or leaving?"**
The Scout compares this minute's thickness to the first time he saw it.
Growing = someone is building on purpose. Shrinking = they are walking away.

**`l3_large_ofi` — "is it grown-ups or children?"**
Marks a door as big-player when the size is well past the threshold.

**`l3_imbalance` — "which side of the hallway is heavier right now?"**
Context, reported as a plain fact.

**`microprice` — "which way is the floor tilting?"**
The earliest hint of movement, in fractions of a penny, before price moves at all.

**`absorption` — "people are pushing that door and it will not move."**
The strongest live signal there is. Someone big is holding it — or it is about to break.
When absorption fires, the nearest door on that side gets an **attacked** mark and its
hold score falls (in testing 0.63 → 0.34 after six attacks).

**`queue_pos` — "if we joined this queue, would we get served?"**
The only judge that thinks about *us* rather than the market.

---

## 6. Two scores per door — because one number cannot say both

We measured your tape and found something surprising:

> **Price reached the wall 75% of the time, in about 5 minutes — but stalled at it only
> 8% of the time, and came back past it 83% of the time.**

Going there and stopping there are **different questions**. So every door gets two numbers:

### `reach` — will price travel here?
Mostly **how close it is**, partly how big. This picks the **target**.

### `hold` — will it stop price when it arrives?
**Age + growth + big-player size, minus how hard it is being eaten.** This decides whether
you take profit in front of it, or expect it to break.

**How to read the pair:**

| reach | hold | what it means |
|---|---|---|
| high | high | price will go there and stop — **take profit just in front** |
| high | low | price will go there and burst through — **ride it, do not exit** |
| low | high | strong door but far away — **a shelter for a stop-loss**, not a target |
| low | low | ignore it |

---

## 7. How many doors, and why

- The feed gives **20 levels per side** — a hard ceiling (`BOOKMAP_MAX_DEPTH_LEVELS=20`)
- Only the top 1–2% are real walls, so **0–8 qualify** per side
- The Scout **remembers 8 per side** — more than the feed usually produces, so nothing
  real is lost, and a door that briefly drops out keeps its history for when price returns
- He **reports 3 per side** — you only ever trade toward the nearest reachable one, and
  a list of eight targets is a list of none

---

## 8. Forgetting, on purpose

A door that stops appearing does not vanish — it **fades**. Each missing minute cuts its
scores. After 5 missing minutes it is stale; after 15 it is torn out of the notebook.
Anything beyond 4 ATR is dropped — not a scalper's problem.

Fading rather than deleting matters: the book flickers, and a real wall that blinks for
one cycle should not be forgotten and then re-learned from scratch.

---

## 9. What the Scout hands over

```
SIGNAL MAP: ceiling 4325.30 (31 lots, 0.41 ATR, growing, seen 5x, reach 0.73 hold 0.63)
          | floor   4323.00 (10 lots, 0.31 ATR, shrinking, seen 2x, reach 0.37 hold 0.17)
          | tracking 6, 1 spoof banned
```

Plus a context block — book imbalance, OFI, absorption, large orders — as **facts**.

**Notice what is missing: any opinion about direction.** The ceiling is thicker, closer
and getting stronger while the floor is thinning. A human reads that and thinks "down".
The Scout does not say it. If he did, he would become a second POWER team, and we would
be back to twenty-five voices guessing direction and nobody drawing the map.

**Three jobs, never mixed: the scout draws, the legs decide, the friends guard.**
