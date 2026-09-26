# THE AGREEMENT BOOK
### One charter per team. What it is for, what it may do, what it must never do.
**Written 2026-09-25. This book describes GOALS and RULES only — no code, no formulas.**
Design and logic get derived FROM this book, not the other way round. If a future
change breaks a rule in here, the change is wrong until this book is changed first.

---

# PART 0 — THE LAWS ALL FOUR TEAMS OBEY

These are not suggestions. Every team, every time.

**LAW 1 — One job. Never two.**
A team that starts doing a second job stops doing its first one properly. The scout must
never pick a direction. The legs must never draw the map. The moment a team drifts, we
are back to twenty-five voices guessing and nobody measuring.

**LAW 2 — Say something the tape can check.**
"The market looks bullish" is not allowed. "Price will touch 4400.00 within 20 minutes"
is. Every team must produce claims that reality can mark right or wrong, because a claim
nobody can mark can never be improved.

**LAW 3 — Measure before you build.**
No rule enters the robot because it sounds clever. It enters because it was recorded,
marked against the tape, and survived. On 2026-09-25 three confident theories were killed
by measurement within hours of being born. That is the system working.

**LAW 4 — No silent ceilings.**
If anything is capped, floored, skipped, filtered or refused, it must say so out loud in
the same breath. A robot quietly squashed every signal by 24.5% for months because an
empty team stayed in a division.

**LAW 5 — Missing data is named, never hidden.**
A day that cannot be read is listed with its reason. It is never folded into an average
and never quietly dropped. An unreadable day must never improve or worsen a number.

**LAW 6 — "Nothing" is always a legal answer.**
No door, no push, no room, no trade. A team forced to always have an opinion will always
invent one, and an invented opinion looks exactly like a real one in a log file.

**LAW 7 — Nobody trades until their homework has been marked.**
A new team writes its answers into the diary and changes nothing. Only after the tape has
graded it over real days may it touch a decision. No exceptions, however good it looks.

**LAW 8 — Fail as one line, never as a dead loop.**
Any team that breaks writes a single note and the robot carries on. A new idea must never
be able to stop the machine.

**LAW 9 — Small and reversible.**
One change at a time, each with an off switch. If a change cannot be undone in a minute,
it is too big.

**LAW 10 — The owner decides.**
Nothing changes in the live robot without his word. Every proposal comes with what it
does, what it costs, and how to undo it.

---

# PART 1 — THE SIGNAL TEAM ("the scout")

### Its goal
**To know where the doors are, and to keep that knowledge true minute by minute.**
Nothing else.

### What it may look at
Resting orders only — the order book, order events, iceberg refills, known spoof prices.
Things that have **not happened yet**.

### What it must produce
A map. For every door: price, side, size, distance, how long it has existed, how many
times it has been seen, whether it is growing or melting, whether it is being eaten,
who spotted it, and whether it is a known lie. Plus two scores — **reach** (will price
come here) and **hold** (will it stop price when it does).

### IT MUST
- Keep a **memory**. A door seen twelve times is not the same as one seen once.
- Tell **growing from melting**. That is the single most valuable thing it knows.
- Judge a door by how it compares to its **neighbours**, not by a fixed size. Market
  makers must quote near price; that size is an obligation, not an opinion.
- **Delete** a known liar. Not downgrade — delete.
- Refuse to offer a door so close that the spread eats the trade.
- **Fade** a door that disappears rather than forgetting it instantly. Books flicker.
- Say out loud when it floors, filters or bans something.

### IT MUST NOT
- **Never say which way price will go.** Not once, not as a hint. That is the legs' job.
- Never treat size alone as meaning.
- Never invent a trend it has not watched for at least two cycles.
- Never let the discovery threshold stop it tracking a door it already knows — that is
  how you miss a wall melting away.

### What it is judged on
Do the doors it names actually get reached? Do the ones it calls strong actually hold?

### THE OWNER'S RULES — signed for the SCOUT, 2026-09-25
*(his words, kept as written; the note under each is what it means for the build)*

**S1. "This team is only looking for the doors and information about them; you give this
info to the other teams as well, and should keep yourself updated always and always."**
> Doors and facts about doors. Nothing else, ever. And the map is a LIVING thing - a map
> that is not refreshed is not a map, it is a memory of a market that has gone.

**S2. "Before any position opened by the shooter team, you only select the door which has
value to spend time and energy to watch and keep the details from them."**
> The scout's attention is a budget. Watching everything means watching nothing well.
> Only doors worth a trade earn a page in the notebook - the rest are noise that would
> dilute the memory of the ones that matter.

**S3. "The near doors around the current price are managed by the middlemen and market
makers; also they won't give you so much profit, so check the distance and how big and
strong the door is."**
> Size alone is a lie near the touch. A door must be judged on THREE things together:
> how far, how big, how strong. Never one of them alone. Market makers are obliged to
> quote near price - that is a job, not an opinion, and it vanishes when price arrives.

**S4. "The doors you put in your notebook are moving sometimes as well by small price
movement; keep following them and get the info and update it."**
> **This is now a rule, not a bug report.** A door that slides one tick is the SAME door
> and must keep its whole history. Today the notebook loses it and starts again at
> "seen 1x". Following a moving door is part of the scout's job, not an optimisation.

### Known weaknesses today
- **Breaks rule S4:** a door that moves one tick becomes a new door and loses all its history.
- The notebook lives in memory only and is wiped on every restart.

---

# PART 2 — THE POWER TEAM ("the legs")

### Its goal
**To say which door price will touch first, and how sure we are.**

### What it may look at
Everything that has already happened — finished trades, the candles built from them, and
the outside world.

### What it must produce
One answer: **ceiling, floor, or neither** — with a confidence, a push reading, and the
reasons in plain sentences.

### IT MUST
- Give every judge a **job it can actually do**. Only the few that can genuinely see a
  direction may pick a side; the rest confirm, gate, brake, veto, or stand down.
- Let a **trigger jump the queue**. A sweep is silent 99% of the time; averaging silence
  destroys it.
- Let a **veto stand alone**. A judge reporting a disagreement must be able to cancel the
  trade by itself, even when everything else agrees. Especially then.
- Treat **fuel as a gate**. No fresh volume, no real move, whatever the direction says.
- Let the **regime change the rulebook**, not cast a vote.
- Answer **"neither"** freely.

### IT MUST NOT
- Never average judges that answer different questions.
- Never let a judge with no sense of direction vote on direction.
- Never claim a distance it has not measured.
- Never invent conviction on a quiet market.

### What it is judged on
When it named a door, did price touch that one first? Per group, and per judge.

### THE OWNER'S RULES — signed for the LEGS, 2026-09-25
*(his words, kept as written; the note under each is what it means for the build)*

**L1. "Am I strong enough to reach the target door?"**
> The whole job in one sentence. Not "which way is the market going" - **can we get
> THERE, from HERE, before we run out of push.** Strength is only meaningful against a
> distance and a corridor.

**L2. "What happened previously when the price was here before, and how did it act?"**
> **A new capability.** The legs must remember this price, not just this moment. Was this
> level rejected last time? Did it break easily? Did price stall here for an hour? Today
> `supply_demand`, `poc_day` and `htf_poc` gesture at this, but nobody asks the direct
> question: *last time we stood here, what happened next?*

**L3. "Do other people also want to go in this direction with me?"**
> Agreement, not volume. One shove is not a move. This is breadth (`footprint_levels`),
> the clocks agreeing (`mtf`), and fresh participants arriving (`volume_roc`) - and it is
> why those three confirm rather than vote.

### Known weakness today
On the only measurement so far it picked the correct side **40.9%** of the time — worse
than a coin. That test was taken with a 24.5% ceiling, half the panel mute and selling
banned, so it proves little. **It has never yet had a fair exam.**

---

# PART 3 — THE SHOOTING TEAM ("the shooter")

### Its goal
**To decide whether the shot is worth taking, and where the target and the stop go.**

### What it may look at
The scout's map, the legs' answer, the corridor between price and the target, and the
shelter behind.

### What it must produce
`GO`, `NO_GO` or `WAIT`, and when it says GO: entry, target, stop, risk-to-reward, the
first stop along the way, and the shelter it is leaning on.

### IT MUST
- **Count the corridor before shooting.** The road is often far bigger than the
  destination — on the owner's own screenshot, 180 lots stood between price and a 23-lot
  target. Force must beat the road, not the door.
- **Find the shelter behind.** That is where the stop belongs, and where a tired move can
  rest and wait for help.
- Name the **biggest door in the way** as a *first stop* — a place to expect a pause and
  take part of the profit, not merely an obstacle.
- Take the target from the **book**, just in front of the door — never from a fixed
  multiple.
- Refuse when the reward is barely the spread.
- Refuse when the shelter is so far behind that the risk outweighs the reward.
- **Wait** when the corridor is crowded and the push is only ordinary.
- Explain every refusal in one plain sentence.

### IT MUST NOT
- Never shoot because the door is big. Big doors with crowded roads are traps.
- Never place a stop at a distance with no wall behind it, if a wall exists.
- Never take a trade whose profit is smaller than the cost of making it.
- Never overrule a veto from the legs.
- Never send an order until its homework has been marked. **Today it only writes plans.**

### What it is judged on
When it said GO, did price reach the target before the stop? When it said WAIT, would the
trade have worked anyway?

---

# PART 4 — THE ESCORT TEAM ("the friends") — designed, not built

### Its goal
**To keep an open trade safe until it reaches the target or the stop.**
It sleeps while flat and wakes the moment a position exists.

### What it may look at
Everything, but only in service of one question: *is this trade still the trade we opened?*

### What it must produce
At most one recommendation per job, per cycle, each with the judge's name attached.

### Its five jobs
1. **Is my exit still clear?** — did a new door appear in front, did the target door vanish
2. **Is the crowd turning against me?** — the earliest warning of a reversal
3. **Is my push dying?** — pressure fading while price still drifts
4. **Where do I move my stop to?** — the next shelter as price advances
5. **Is a bomb coming?** — an event approaching

### IT MUST
- **Only ever make the trade safer.** Tighten, take profit early, close, part-close.
- Speak with **one voice per job** — five recommendations at most, never fourteen.
- Write down every action with the reason and the judge that caused it.
- Be **graded separately**, always against *"what if we had done nothing?"*

### IT MUST NOT
- **Never widen a stop.** Never add size. Never move a target further away on a losing
  trade. An escort that can loosen protection is not an escort — it is a second gambler.
  *(One allowed exception, already in the code: a target may move further away once the
  trade is break-even. A locked trade may run.)*
- Never re-open a trade it has closed.
- Never argue with the reason the trade was taken — only with whether it still holds.

### What it is judged on
Did its interventions earn more than they cost, compared with leaving the trade alone?

### Its greatest danger
An escort that exits too eagerly destroys profitability while every single decision looks
sensible in isolation. That is why separate grading is not optional.

---

# PART 5 — HOW THE FOUR FIT TOGETHER

```
SCOUT     "there is a 23-lot door at 4400, fourteen points below,
           it has been there five minutes and it is growing"          <- FACTS

LEGS      "the crowd is pushing down hard, breadth agrees, volume is
           rising, no divergence -> we reach the FLOOR first, 0.55"   <- JUDGEMENT

SHOOTER   "the corridor holds 180 lots in 27 doors - crowded, but the push is
           strong. Shelter sits 0.9 above us. GO SELL: target 4400.60,
           stop 4415.60, R:R 9.0, expect a pause at 4412.50"          <- DECISION

FRIENDS   "the 4400 door just doubled - take profit earlier"          <- PROTECTION
```

**Facts → judgement → decision → protection.** Four teams, four jobs, one direction of
travel. No team reaches backwards into another's work.

---

# PART 6 — HOW THIS BOOK IS USED

1. A new idea is written as a **goal** in the right team's charter first.
2. Only then is logic designed to serve that goal.
3. Anything that breaks a MUST NOT is rejected, however well it performs — because a rule
   broken once for a good reason gets broken forever for bad ones.
4. When reality contradicts the book, **the book changes**, in writing, with the evidence
   that forced it. Nothing here is sacred except Part 0.
