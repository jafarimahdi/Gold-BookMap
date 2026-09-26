# The Legs (POWER team) — explained simply
**Design proposal, 2026-09-25. Not built yet.**
Companion to `SCOUT_EXPLAINED.md`. The scout draws the map; the legs decide if we can get there.

---

## 1. The picture: a crowd pushing a heavy cart

Forget charts. Picture a **heavy cart** in a street, and a **crowd pushing it**.

Price is the cart. The crowd is every trader in the market. The cart only moves when
enough people push the same way, at the same time, hard enough.

The Scout already told us where the **doors** are — the walls of orders up the street and
down the street. The legs answer one question about that:

> **"Which door will the cart reach first?"**

Not "is the market bullish". Not "what is the trend". Just: **ceiling or floor, which one
gets touched first.** The street answers within minutes, so we can always check if we were
right.

---

## 2. Why this question and not "which way is it going?"

Because we measured the old question and the answer was embarrassing.

> On independent windows, the panel picked the correct side **40.9% / 47.8% / 41.7%**
> at 15 / 30 / 60 minutes. A coin does better.

"Which way is the market going" is vague, and you can argue about it forever. "Which door
gets touched first" is a fact the tape settles. **You cannot improve what you cannot mark.**

It also makes all sixteen judges comparable for the first time, because they are finally
being asked the *same* question with the *same* right answer.

---

## 3. The big idea: give each judge a JOB, not a vote

This is the heart of it, and it comes from years of watching panels like this fail.

Today all sixteen judges do the same thing: they say BUY or SELL with a weight, and the
robot averages them. **That is wrong, because most of them cannot actually see a direction.**

Ask `volume_roc` which way the market is going and it has no idea — all it knows is that
the street suddenly got louder. Averaging its "opinion" into a direction score is asking a
sound-meter which way the wind blows.

So instead, every judge gets a **job that matches what it can really see**:

| job | what that judge does | who |
|---|---|---|
| **TRIGGER** | "GO. Right now." Rare, loud, decisive | `sweep` |
| **DIRECTION** | which way the crowd is actually pushing | `footprint_delta`, `l3_aggr_limit`, `cvd_momentum` |
| **CONFIRMER** | "is this a real push or one loud person?" | `footprint_levels`, `volume_roc` |
| **VETO** | "the cart is rolling but nobody is pushing any more" | `cvd_divergence` |
| **REGIME** | changes how everyone else should be read | `value_area`, `mtf` |
| **STRETCH** | "we have come a long way already" | `vwap_bands`, `vwap_trend` |
| **MAGNET** | where the cart tends to drift back to | `poc_day`, `htf_poc`, `supply_demand` |
| **STAND DOWN** | "a storm is coming, stop pushing" | `news_sentiment`, `macro_risk` |

**Only four judges are allowed to pick a side.** The other twelve make that pick stronger,
weaker, or cancelled. That is not a demotion — a confirmer that stops one bad trade is
worth more than a voter that adds noise to a hundred.

---

## 4. Each judge, and the smartest way to use it

### The ones that push the cart

**`footprint_delta` — who is shoving, and how hard**
At every price it sees whether the impatient buyers or the impatient sellers won.
**Smart use:** direction AND intensity, not just the sign. A +0.9 delta and a +0.1 delta
are not the same information, and today they both become "BUY".

**`l3_aggr_limit` — are they in a hurry?**
Impatient market orders versus patient limit orders.
**Smart use:** a confirmer with teeth. A push with no urgency is drift, and drift does not
reach a door. Someone who *waits* for a better price does not move a cart.

**`cvd_momentum` — is the cart speeding up or tiring?**
The running total of pressure, and whether it is accelerating.
**Smart use:** it separates "still pushing" from "already pushed". Same delta, opposite
meaning depending on this one.

### The one that shouts GO

**`sweep` — somebody just sprinted**
One order ate several price levels at once. That trader deliberately paid a worse price to
get in *immediately*.
**Smart use: treat it as an EVENT, not a vote.** It is silent 99% of the time, and
averaging silence into a score wastes it. When it fires it should jump the queue — nobody
pays up like that for no reason. This is the single most under-used judge in the panel.

### The ones that say "is this real?"

**`footprint_levels` — breadth**
Is the whole cart being pushed, or one corner? Twelve prices leaning one way beats one big
trade at one price.
**Smart use:** a confirmer. It has no direction of its own and should never vote on one.

**`volume_roc` — is the street filling up?**
A sudden jump in traded volume: new people arriving.
**Smart use:** a **gate**, not a voter. No fresh volume, no real move — a quiet street does
not move a heavy cart however hard three people push. It should scale confidence, never
pick a side.

### The warning

**`cvd_divergence` — the cart is rolling but nobody is pushing**
Price makes a new high while pressure does not follow.
**Smart use: a VETO, standing alone.** This is the only judge that reports a *disagreement*,
and averaging it into a push score destroys exactly what makes it valuable. When it fires
against the intended direction, the trade should not happen — even if everything else agrees.
Especially then.

### The street itself

**`value_area` — are we inside the crowd or outside it?**
Inside the zone where 70% of volume traded, the cart rattles back and forth. Outside it,
the cart runs.
**Smart use: a REGIME SWITCH.** It should change how everyone else is read, not cast a vote.
Inside value, fade the extremes; outside value, follow the push. One judge, two different
rulebooks.

**`mtf` — do the clocks agree?**
M5, M15 and H1 pointing the same way.
**Smart use:** a confidence multiplier. A referee, not a player.

**`vwap_trend` — cheap or expensive today**
**`vwap_bands` — how far from fair we have already come**
**Smart use:** the stretch brake. Pushing further when already at +2σ is running uphill.
These should *reduce* confidence in a push that has already travelled, not argue with it.

**`poc_day`, `htf_poc` — where the cart drifts back to**
**`supply_demand` — where the cart bounced before**
**Smart use: room.** They tell you how much clear street there is before the next magnet.
Note these are effectively **old doors** — the Scout deals in live walls, these in
remembered ones. Worth feeding into the map later rather than voting here.

### The sky

**`news_sentiment` — the only judge that can see the future**
Scheduled events. It knows something is coming before the tape does.
**Smart use:** a stand-down switch near events, plus a slow bias. Never a trigger.

**`macro_risk` — the weather everywhere else**
**Smart use:** background only. Slow, and rarely decisive in a five-minute window.

---

## 5. What "the market wants to move" actually looks like

Not one judge. A **pattern**, and this is the pattern I would trade:

```
DIRECTION agrees        footprint_delta and l3_aggr_limit lean the same way
BREADTH confirms        footprint_levels: many prices, not one big print
FUEL arrives            volume_roc rising - new people in the street
ACCELERATING            cvd_momentum still building, not fading
NO WARNING              cvd_divergence silent
ROOM TO RUN             outside value area, and a door 0.5-1.5 ATR away
NOT STRETCHED           not already at +2sigma in the push direction
CLOCKS AGREE            mtf lined up
NO STORM                no high-impact event inside the window
```

**And the strongest version of all: a `sweep` fires while the above is true.** Someone
paying worse prices to get in immediately, in a street that is filling up, with a reachable
door ahead and nothing standing in the way. That is a market that wants to move.

**The opposite — the trap:** big delta, no breadth, flat volume, price already stretched,
`cvd_divergence` firing. One loud person in an empty street. Today's robot reads that as a
strong signal, because delta is big and delta has the heaviest weight.

---

## 6. How the legs answer, and why "neither" is a real answer

```
PUSH picks a door               ->  "the ceiling"
CONFIRMERS scale it             ->  breadth yes, volume no  -> 0.5x
STRETCH brake                   ->  already +1.8 sigma      -> 0.7x
cvd_divergence veto             ->  silent                  -> proceed
REGIME (value_area)             ->  outside value           -> follow, do not fade
WEATHER                         ->  clear
DISTANCE from the map           ->  ceiling 0.4 ATR, floor 1.9 ATR -> ceiling far easier
--------------------------------------------------------------
ANSWER: ceiling, confidence 0.38
```

**"Neither" must be allowed.** Most minutes there is no reachable door and no real push, and
the honest answer is to sit still. A robot that must always have an opinion will always
find one — and a panel that must always vote will always produce a number, which is exactly
how a coin-flip ends up looking like a signal.

---

## 7. How we will know if it works, with no money at risk

Every cycle, POWER writes its pick into the diary. Later the tape says which door was
actually touched first. Nothing is traded.

After a week we can say, per judge and per group:
**how often did you pick the door price really went to?**

My prediction, written down in advance so it can be wrong:
- **PUSH will beat TERRAIN.**
- **The blended panel will be near 50%, and PUSH alone will be better than the blend.**

If that is true, the terrain judges have been diluting the only group with an edge — and
that is worth more than any amount of weight tuning.
