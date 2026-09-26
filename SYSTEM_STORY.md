# The whole story, in plain words
**2026-09-25, end of the week. Written for a five-year-old, and for the next assistant.**

---

# 1. WHAT PROBLEM ARE WE FACING?

## The honest answer: we still do not know if the robot can guess right.

For months the robot has been trading with a panel of judges. Today, for the first time,
we **measured** whether that panel actually knows anything.

> **It picked the correct side 40.9% of the time on a 15-minute view.**
> A coin is 50%. The panel was *worse than a coin*.

That is the real problem. Not a bug - the whole reason the robot exists is to guess which
way price goes, and there was no proof it could.

## But before blaming the judges, we found the robot was wearing a blindfold

Today we opened the machine and found **nine real faults**. Every one of them was making
the judges look worse than they are:

| what was wrong | in plain words |
|---|---|
| A team that was always empty still counted in the maths | every signal was squashed by **24.5%** before it was measured. A "60" arrived as "45" |
| A rule waited for that empty team to agree | **52% of all minutes** were forced to "do nothing" |
| A wall had to be 100 lots to be noticed | the biggest wall in the whole tape was **53**. So the wall judges never spoke once, ever |
| After a losing day the robot could only BUY | a sign was flipped in one line. Sells were silently banned |
| The news alarm was always on | **70% of the day** was treated as "danger", so the robot was permanently timid |
| One judge voted every minute and never wrote it down | nobody could see it or mark it |
| That judge's counter never reset | it shouted this morning's news all afternoon |
| The market-depth window collapsed | ATR - the ruler everything is measured with - shrank to **a quarter** of normal |
| The scout and a judge disagreed about the same wall | one said "wall at 4325.3", the other said "no walls" |

**So the panel was being marked on an exam where the pen kept breaking.** We fixed all nine.
Whether the judges are actually any good - that is Monday's question.

## And one more honest problem: we cannot test it yet

The market closed at 23:00 on Friday. Everything we built tonight has run for about two
hours on a dead, thin Friday evening. **Nothing here is proven.** Monday is the first real day.

---

# 2. HOW FAR HAVE WE COME?

Think of it as three days of work in one:

**We gave the robot a proper eye test.** Four measuring tools that read what the robot
really did, not what we hoped it did. Every one refuses to answer when the data is too thin
to support an answer - because a confident wrong number is worse than no number.

**We fixed the nine faults.** All measured, all verified, all reversible.

**We built two brand-new brains** that run beside the old one and touch nothing:

- **The SCOUT** - draws a live map of the walls, with a memory
- **The LEGS** - looks at that map and says which wall price reaches first

**And we proved they work** - not by testing them, but by reading real cycles as they came
out of the live robot. Nine faults found that way. Not one came from a unit test.

The old robot still makes every real decision. The two new brains just write down what they
*would* have said. That is on purpose: you get to watch them be right or wrong for a week
before they are allowed to touch a single trade.

---

# 3. THE SCOUT (the SIGNAL team) - what it does and why

## The picture

Your robot is a boy in a long hallway. Price is where he stands. Along the walls there are
**doors** - piles of orders waiting at a price.

- Some doors are **thick** (many people), some are **thin** (two people)
- Some are **painted on the wall** - not real doors at all. That is *spoofing*
- Some look thin but have **a big man behind them** who steps back in every time you push.
  That is an *iceberg*

The scout's only job: **walk ahead, write down every door, and keep the list true.**
He never says "go left" or "go right".

## The notebook - the important part

Looking **once** tells you only how thick a door is. Looking **every minute** tells you what
the door is *doing*:

```
"been there all morning"        -> strong
"appeared 30 seconds ago"       -> probably fake
"getting thicker"               -> someone is building it on purpose
"getting thinner"               -> they are leaving, it will break
"people keep pushing, it holds" -> someone big is behind it
```

That is a **film instead of a photo**, and it is the single best idea in the new design.

## Three judges MAKE doors (only these can name a price)

**`whale_walls`** - "there is a big pile here." Ten lots or more.
*Why ten?* Measured from your own tape: a normal price holds 2 lots, the top 1% hold 11,
and the biggest ever seen was 53. Ten is genuinely big for gold.

**`iceberg`** - "this one has a big man behind it." A door that keeps refilling after being
eaten. **Nothing else in the robot can see this.**

**`spoof_invert`** - "that door is painted on." The door is **deleted from the map**, not
doubted. In testing, a 25-lot door - the biggest thing on the map - vanished the moment it
was flagged. *A known liar is not a weak signal; it is not a signal.*

## Six judges DESCRIBE doors (they cannot name a price)

| judge | what it says |
|---|---|
| `l3_net_flow` | is that pile growing or leaving? |
| `l3_large_ofi` | is it grown-ups or children doing it? |
| `l3_imbalance` | which side of the hallway is heavier right now? |
| `microprice` | which way is the floor tilting, before anything moves? |
| `absorption` | "they keep pushing that door and it will not move" |
| `queue_pos` | if we joined this queue, would we actually get served? |

## Every door gets TWO scores, because your tape demanded it

We measured: **price reached the wall 75% of the time - but stopped there only 8%.**
Going there and stopping there are different questions.

- **`reach`** - will price travel here? Mostly *how close*. This picks the **target**.
- **`hold`** - will it stop price? *Age + growth + size, minus how hard it is being eaten.*

```
high reach + high hold  ->  take profit just in front of it
high reach + low hold   ->  it is about to break, ride through
low reach + high hold   ->  a shelter for a stop-loss, not a target
```

## How many doors

The feed shows **20 levels each side** - a hard limit. Only about 1% are real walls, so
**0-8 qualify**. The scout **remembers 8 per side** and **reports 3**, because you only ever
trade toward the nearest reachable one, and a list of eight targets is a list of none.

## And it forgets slowly, on purpose

A door that disappears **fades** - stale after 5 missing minutes, torn out after 15.
The book flickers; a real wall that blinks once should not be forgotten and re-learned.

---

# 4. THE LEGS (the POWER team) - what it does and why

## A different picture: a heavy cart

Price is a **heavy cart** in the street. The crowd is every trader. The cart only moves when
enough people push the same way, at the same time, hard enough.

The legs answer **one question** about the scout's map:

> **"Which door does the cart reach first - the ceiling or the floor?"**

Not "is the market bullish" - you can argue about that forever. **Which door gets touched
first is a fact the street settles in minutes**, so we can always mark the answer.

## The big idea: every judge gets a JOB, not a vote

The old robot let all sixteen judges shout BUY or SELL and averaged them. **That is wrong,
because most of them cannot see a direction at all.** Ask `volume_roc` which way the market
is going - it has no idea. It only knows the street got louder. Averaging that into a
direction is asking a sound-meter which way the wind blows.

**Only four judges may pick a side. The other twelve make that pick stronger, weaker, or cancelled.**

| job | judges | what they do |
|---|---|---|
| **TRIGGER** | `sweep` | "GO. Now." Somebody sprinted - paid a worse price to get in immediately |
| **DIRECTION** | `footprint_delta`, `l3_aggr_limit`, `cvd_momentum` | who is shoving, are they in a hurry, is the cart speeding up |
| **CONFIRMER** | `footprint_levels`, `volume_roc` | is this the whole cart or one corner? is the street filling up? |
| **VETO** | `cvd_divergence` | "the cart is rolling but nobody is pushing" - stands alone, can cancel everything |
| **REGIME** | `value_area`, `mtf` | inside the crowd = it rattles; outside = it runs. Changes how everyone else is read |
| **STRETCH** | `vwap_bands`, `vwap_trend` | "we have come a long way already" - brakes, never argues |
| **MAGNET** | `poc_day`, `htf_poc`, `supply_demand` | where the cart drifts back to - how much clear street there is |
| **STAND DOWN** | `news_sentiment`, `macro_risk` | "a storm is coming, stop pushing" |

## What "the market wants to move" really looks like

Never one judge - a **pattern**:

```
direction agrees   +   breadth confirms (many prices, not one loud print)
+ volume rising    +   still accelerating   +   no divergence warning
+ outside value    +   not already stretched   +   clocks agree   +   no storm
```

**And the strongest of all: a `sweep` fires while all that is true.**

**The trap it avoids:** big delta, no breadth, flat volume, already stretched, divergence
firing. One loud person in an empty street. *Today's old robot reads that as a strong
signal, because delta is big and delta has the heaviest weight.*

## "Neither" is a real answer

Most minutes there is no reachable door and no real push, and the honest reply is to sit
still. **A robot that must always have an opinion will always find one.**

---

# 5. HOW THE TWO TEAMS WORK TOGETHER

## The sentence they build between them

Neither team can trade alone. Together they produce something the old robot never could -
a statement reality can check:

```
SCOUT :  "ceiling at 4325.30, 31 lots, 0.41 ATR away, growing, seen 5 times,
          reach 0.73, hold 0.63.  Floor at 4321.00, thin, shrinking."

LEGS  :  "push +0.55, breadth confirms, volume rising, not stretched, no warning
          -> the cart reaches the CEILING first. Confidence 0.38."

TOGETHER: "price will touch 4325.30 within about 20 minutes, and that wall is
           strong enough to stop it - so take profit just in front."
```

Twenty minutes later the tape says **yes or no**. Nobody argues.

## Why they are kept separate

The scout may **never** say which way to walk. The moment he does, he becomes a second legs
team - and you are back to sixteen voices guessing direction with **nobody drawing the map**.
That is the exact mistake we spent this week removing.

```
SCOUT -> facts    : where the doors are, how thick, how old, growing or melting
LEGS  -> judgement: which one the cart reaches first, and how sure
ESCORT -> later   : runs beside the trade and shouts if the door changes
```

**Three jobs, never mixed.**

## Where the trade would come from

- The **door** gives the take-profit - just in front of the wall
- The **other door** gives the stop-loss - just behind the wall on the far side
- The **legs** decide whether to go at all, and how confidently
- If there is **no room between the doors** - no trade. That is your football idea:
  *look up before you kick, and if your friend is not open, do not shoot.*

## And today none of it is allowed to trade

Both teams write their answer into the diary every minute and change nothing. The old robot
still makes every decision. On Monday evening we mark their homework against what really
happened - **per team, and per judge.**

My prediction, written down now so it can be wrong:
**the PUSH judges will beat the TERRAIN judges, and PUSH alone will beat the whole blended
panel.** If that is true, the terrain judges have been watering down the only group with an
edge - and we will have found it by measuring, not by opinion.
