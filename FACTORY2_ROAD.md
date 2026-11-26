# GOLD FACTORY 2 — THE WHOLE ROAD, TOLD AS A STORY
### Room by room, step by step, for the owner (five years old is the right age for this: it is the age that asks WHY four times)
*v3, 2026-11-25 night. The compact tracker version lived here before; the story replaces it. Legend:*
`[x]` closed by a receipt · `[!]` open, my job · `[?]` open, waiting on YOU · `[-]` will not be built here

---

## THE WHOLE SHOP IN ONE PARAGRAPH

Imagine a little gold shop on a busy street. Every three minutes the shopkeeper wakes up, looks
through the windows, thinks with three brains, checks the rules at the door, and - when EVERYTHING
agrees - can place one tiny real order with the practice bank. Everything he saw and said and did
goes into one glue-bound book that can only grow. Every night a teacher reads the book and grades
the day. And the door is built so that no one - not me, not you by accident, not a hacker, not a
typo - can ever point the shop at real money without the owner's own hand.

---

## ROOM A — THE EARS
*how a minute of the real street gets inside the shop, without hurting anybody outside*

**A1 [x] THE WINDOW.** The shop has two windows in the same frame. One looks at **old photos** -
the archive days, the big recorded history file, the jar of saved snapshots - that's where the time
machine (Room D) sails. The other looks at the **live street** - BookMap outside painting every
price sticker as it happens, and our eyes reading the growing tape all day. Here is the important
part: we did NOT teach a new robot to see. Seeing is the one skill the OLD factory already had,
proven for years, so the new hall **borrows those eyes** - imports them, reads them, never scratches
them (that is law 108: retired does not mean edited). Kid-version: if your big brother already has
good glasses, you borrow them - you do not take them apart to build your own.
*Receipt:* tonight the cards really moved with the street - candles 6 → 7, LEAN flipped, DIST grew -
nobody typed those numbers.

**A2 [x] THE GLASS CASE.** Now the scary part, and the reason this room exists at all. The borrowed
eyes come with **hands** - old habits: while they look, they write things (the day's high/low file,
the book-state file). If we let those hands write in the old factory's house at 2 a.m., then take
the same eyes, run a TAPE FROM FIVE DAYS AGO through them, and the hands happily stamp five-day-old
prices onto TODAY's memory - the live factory would wake up tomorrow remembering yesterday from last
week. A time-contamination. The fix is a glass case: our whole process points its writing hand at
OUR drawer (`data/gates_sandbox/`), so borrowed writers splash into sand, and the real files outside
are only ever glanced at, never touched. Kid-version: you may look through brother's glasses, but
we taped his pen to the table.
*Receipt:* the hall's first boot line, always: `data quarantine: borrowed writers -> ...\data\gates_sandbox (live session_levels.json / last_book_state.json untouched)`.

**A3 [x] FRESH EYES.** Ears that lie are worse than no ears. So the shop checks its own aliveness:
if the tape stopped printing (market asleep, weekend, BookMap closed), the eyes SAY "I am sleepy" -
`MOOD=SLEEP`, and the cycle writes `SKIPPED (no rung under the feet)`. A vote is only allowed with
fresh eyes; WAITING is recorded as a decision, not as a failure. This is the law the whole factory
runs on: **when the eyes are weak, the correct answer is NO - and NO counts as being right.**
*Receipt:* tonight's quiet cycle printed exactly that, and the diary still added up.

**A4 [x] BELT + TALLY.** Before anyone grades the day, the day must be PROVEN COMPLETE: the line
count equals the classes, every line names itself, the arithmetic closes like a belt. Not trust,
arithmetic. Kid-version: count your pages before you show the book to teacher.
*Receipt:* `55 lines == 53 cycle + 2 order - kindcheck PASS`, from your own paste, tonight.

---

## ROOM B — THE JUDGE
*three brains vote in a fixed order; the first NO ends the argument; every NO gets a name*

**B1 [x] G1 - THE MOOD (the river).** Gold is not pushed by words, it is pushed by orders. G1 watches
the last few 3-minute candles like a boy watching a stream: which way is the current pushing
(`LEAN=UP/DOWN`), how sure is he (`CERTAINTY=0-100`), is the push `FIRM` or wobbling, and `SINCE` how
many candles has it been like this. A push young and wobbly means different things; an old firm push
means something else. No vote of "buy" yet - G1 only sets the weather.
*Receipt:* `g1: MOOD=WALK | CERTAINTY=100 | MOVING=FIRM | SINCE=9 | LEAN=UP`.

**B2 [x] G2 - THE PLACE (the shelves).** Now WHERE. The day's price history is sorted into shelves:
the **value shelf** (`htf_poc` - the price everyone traded at most, the floor of the house), the
**demand shelf** (below, where price bounced up before), the **supply shelf** (above, where price
got pushed down before). Middle-of-nowhere = no trade, because there is no wall to stand on. G2
measures the distance in "how much the day normally wiggles" (ATR) - `DIST_ATR=0.08` means standing
right ON the shelf - and checks the shelf is FRESH (price touched it in the last few candles:
`SOFT=1, CANDS=7`). Kid-version: a catcher only jumps where the ball keeps landing, not in the
middle of the field.
*Receipt:* `g2: LOCATION=htf_poc | DIST_ATR=0.08 | TIER=soft | CANDS=6`.

**B3 [x] G3 - THE TOLLS (the verdict).** If - and only if - the river and the shelf agree, G3 tries
to earn its verdict, and earning means the little toll booths along the tape all PAY UP: `delta`
who was net-pushing, `absorb` the big wall eating hits without moving, `iceberg` the hidden size
resting in the book, `tired` the runners giving out, `creak` the price moving with effort, `scarecrow`
the scary size standing on one side, `tilt` who leans. `TOLL=unpaid` - door shut. `TOLL=paid` - and
STILL G3 checks the PUSH: if the shove died on the way, the answer is `NONE`, and NONE means no.
*Receipt, the best one of tonight:* a full cycle with `TOLL=paid | EXCESS=c4/h10m` ... and `PUSH=NONE`
... and no order. The machine was READY and refused anyway, because refusing without a reason is
what a casino does.
**B4 [x] THE SPREAD DOOR.** Buy-price and sell-price are never the same number; the gap between them
is the ticket-fee. If the fee is wider than the cap, even a GOOD trade loses to the fee - so the door
says no before genius is needed. The funny-sad history: this cap lived in two places at once, and the
place .env controlled was not the place the judge read - so the door was armed by a number nobody
could change. D5 moved it into ONE home, printed it at boot for all to see, and now .env truly moves
it. That is law 111: a setting must have one home.
*Receipt:* `spread cap in force: 0.05 % of price (config MAX_SPREAD_PCT - one home since D5)`.

---

## ROOM C — THE BOOK
*the diary is the shop's ONLY output that anyone trusts - so it is built like a court record*

**C1 [x] ONE LINE, NAMED.** Every cycle writes one ~1KB line into the day's file: time, the builder's
version seal, all three gate cards, the verdict word or the NAMED refusal. The line starts with
`"line_kind": "cycle"` - paper that introduces itself. Append-only: superglue, not white-out.
**C2 [x] MONEY PAGES, SAME BOOK.** Since D2, when the hand does anything (arm, open, close), THAT
also writes a line (`line_kind: "order"`) into the same day's book - so one file holds thoughts AND
deeds, and the equation keeps closing: tonight `53 + 2 = 55`. If money-lines lived in a second book,
the court (you) would have to trust two stories to learn one truth.
**C3 [x] THE REFEREE.** `kindcheck.py` does not trust the diary writer OR me: it counts raw bytes,
demands every JSON line name its kind, re-adds the totals, checks no strange control characters hid
anything. The book is checked by a machine that has nothing to gain.
**C4 [x] THE PEN THAT SHOUTS.** Old, quiet danger: a write that failed (disk full, folder moved,
file locked) was SWALLOWED in silence - the book would just... have holes that nobody could see.
D5 made the pen shout: `DIARY WRITE FAILED #n` to the screen, a counter that survives in the boot
report; same for the verdict's own line. Kid-version: a page you LOST should scream when it falls out;
a silent page is how diaries become fiction.
**C5 [x] FOLD, NEVER EDIT.** Last week's books get a .gz coat and go on the shelf. History is shelved,
never rewritten - that is also why the teacher refuses to grade an old paper with new words (it grades
a pre-.3 day by ITS OWN rules, and says so). The 209 unfolded drawers are row 16's chore list, not an
emergency.

---

## ROOM D — THE READER
*the judge is not allowed to mark his own homework - a separate reader decides if any of it is worth anything*

**D1 [x] THE TIME MACHINE.** Every big claim was rehearsed SAILING first: take a recorded history
file, feed it to the EXACT same brain in the exact 180-second steps, and let the teacher compare
notes. Crucial: same clothes - the sail runs the live code path, not a simpler twin, because a
rehearsal that uses easier rules rehearses an easier robot. That is law 112: copy the WHOLE organ.
**D2 [x] THE NIGHT TEACHER.** After close, `gates_report.py` reads the day's book and prints the big
grade sheet: every gate's counts, every refusal by NAME (last day it read `NO-VERDICT=9 +
LOOK-BOTH-WAYS:spread=3 = 12 fed` - the arithmetic of "why nothing traded"), verdict-vs-market
scoreboards, and the belts. And it must FIND the book - until D5 the teacher only looked in the
public drawer, so a live day stored in the sandbox got graded `no diary - nothing to grade`, a
machine confidently reporting a hole. Fixed: sandbox first, then public. It also refuses to grade
PRETEND paper as if it were live - the teacher has a lie-detector built in, and it fires at his own
inputs, not just the writers'.
**D3 [x] THE MONEY RULER.** The cruelest, most valuable shelf in the room: take the verdicts, ask
"if we had traded, at +9 minutes, was there money - after fees?", on everything we have ever
recorded. Answer so far: 53 measurements, average **−0.19 oz, nothing pays yet**. The shop therefore
sizes itself at 0.01 lot forever - one coin - until the ruler changes its mind. Most trading shops
never print this sentence; ours carries it in the report, because we are not building a gambler, we
are building a JUDGE who happens to be able to hold a coin.
**D4 [x] THE HEALTH LINE.** The only sentence that counts as "the factory is well" is
`THE NEW FACTORY IS HEALTHY (sea passed)` - earned when the teacher's grading AND an independent
sail agree. Two clocks, same time, or no applause.
**D5 [!] TONIGHT.** Your 2 commands (sail today + report). First gradeable live day, day 1.

---

## ROOM E — THE HANDS
*the only part of the shop allowed to touch the bank, built out of guards with an order-shaped hole in the middle*

**E1 [x] ARM - the door inside the hand.** Before anything: the hand asks the TERMINAL itself "who
are you?" The account must PROVE `trade_mode = DEMO`. Unknown type? Refused. Real account? Refused -
kid-logic: "prove you are practice or your hand never moves." The old factory needed two switches to
even TRY real money; ours is stricter on purpose (law: borrow the working organ, then arm it past
the donor). And a refusal is not a shrug - it writes `ARM bucket=REFUSED` into the book, because
even the word NO must leave paper.
*Receipt:* `act=ARM bucket=OK proof=DEMO-MODE` + `EXEC ARMED on the DEMO account - the mirror follows the paper`.
**E2 [x] OPEN - the one-coin law.** When B3 actually shouts an earned GO: ONE position at a time
(even a LOST-PENDING mystery counts as the one, so two orders can never sneak out), size 0.01 lot
(one coin), stop-loss from the shelf G2 stood on, a name tag `F2` on the position. Then the part
most systems skip: after the bank says OK, the hand ASKS THE BANK AGAIN - is it actually standing
on the shelf? `proof=SEEN-AT-BROKER`. Receipts lie; shelves don't.
*Receipt:* ticket `102839925`, and its OPEN line with `px 3479.52, sl 3479.02, vol 0.01, attempts 1`.
**E3 [x] MANAGE - the walk-home rule.** While the coin is out: at +0.5R of good news the stop walks
to the entry price - after that the trade is FREE, worst case zero. The take-profit is FROZEN where
the shelf says, never chased (law #1, from the old factory's best lesson: don't be greedy about the
moon). After 180 minutes of nothing, the shop goes home (time-stop) - boredom costs less than hope.
If the bank says "I don't see your position", the hand does NOT panic and open a new one - it
RE-SYNCS from the bank's own list, because the bank is the memory. And positions without the `F2`
tag? Invisible. Untouchable. Even to close them. We learned that near-miss on the donor's own wire.
**E4 [x] CLOSE - gone means proven.** Close on TP, stop, or the alarm clock - then ask the bank a
third time: `proof=GONE`. Already gone? Then write the calm true line `NOOP / ALREADY-GONE` - not a
crash, not a lie. Every bank answer has a NAME (the donor's full retcode taxonomy: `10009 DONE` is
the only OK; the eight fatal codes each get their own bucket word) plus `attempts`, so "something
happened" is impossible in this house; it is always "requote, retried, refused-because-requote".
**E5 [x] THE HONEST DRILL.** A hand that only touches money on verdict day is a hand nobody can trust,
so twice tonight it drilled: 0.01 tiny round trip, prove-open, close, prove-gone - `SELFTEST: PASS`.
Until D5 the drill was a braggart: it printed "see the order line above!" while writing nothing
(a rehearsal with no receipts is pretend - that became law 114). Now the drill keeps its OWN receipt
book (`gates_selftest_<day>.jsonl`) so it can prove itself forever without ever smudging your live
day-book. And law 115, born when you asked "should I keep tab 1 running?": the TEST SUITE ran that
same drill as a subprocess - harmless on my box (no broker), but on YOUR box it would open a REAL
demo order every `pytest`. Now tests wear a tie (`F2_SELFTEST_DISABLE`): a test that CAN trade, WILL.
**E6 [-] THE LEVER.** Real money is bolted into the wall: the config name exists, but NO code path
arms it without proof the terminal is real AND your explicit flag AND (our own rule) five graded demo
days. Car metaphor: you cannot press the gas without putting on the helmet, entering the practice
track, and signing the owner's book. "Or never" is a fully allowed answer.

---

## ROOM F — THE CLOCKWORK
*the shopkeeper's own body - the program in tab 1*

**F1 [x] THE HEARTBEAT.** One loop, one page: wake → A sees → B judges → C writes → (when earned) E
acts and proves → sleep 180 s. The loop itself is the only thing you ever run. Crashes are not
allowed to end a day: every failure mode writes its name in the book and keeps walking (that is why
"no such flag is a LAW, safety is STRUCTURAL" - law 97 - not a switch you can forget to flip).
**F2 [x] BOOT = IDENTITY.** Four first lines: version seals of everything awake, the spread cap IN
FORCE, and whether the hand armed. Born from law 102's two scary nights (a zip that looked applied
but wasn't - old file, new confidence). Kid-version: the robot always says its own name and what it
is carrying, the second you wake it.
*Receipt:* `hall factory2.2026-11-25.6 / exec .5 / report .4` + the cap line + `EXEC ARMED`.
**F3 [x] CARDS FOR HUMANS.** Each gate prints ONE short line so YOU can read the thoughts without a
teacher's degree; the machine's full page goes into the book. You watch the cards, the teacher reads
the diary, the referee checks both.

---

## ROOM G — THE LAW SHELF
*if it isn't written, it didn't happen - 115 laws, each one a scar with a lesson attached*
**G1 [x] HOLY_BOOK.md** - all laws. A few you already met: #6 "no diary, no ticket" (the book closes
first, THEN the bank sees paper); #14 "no new file without a stated reason" (every file must argue
its existence, like `factory2_exec.py`'s "touching money must live OUTSIDE the hall"); #48 "the map
marker moves in the SAME commit as the work" (a map that does not move is a poster); #82/108 "the old
factory is read and imported, never edited"; #62 "explicit paths, never a wildcard add"; #110 "our
own paper rides the sandbox too"; #111 "a setting has ONE home"; #112 "copy the WHOLE organ"; #113
"print what you write into the diary"; #114 "claims about paper must be MADE TRUE by paper"; #115 "tie
the hands of any test that can touch money".
**G2 [x] FACTORY2_MAP.md** - the tracker with `YOU ARE HERE`. **G3 [x] FACTORY2_TODO.md** - the next
things, each with its ticket. **G4 [x] THIS ROAD** - the story you're reading, in the repo now, so it
can't be lost.

---

## ROOM H — THE STRONG BOX
**H1 [?] THE PUSH** - your hands, tonight: 11 files (7 code + 4 books) into GitHub. **H2 [x] THE TAG**
- `factory2-demo-armed` freezes tonight's exact version forever: anyone, any year, can pull THIS
night off the shelf. **H3 [x] HASH DISCIPLINE** - we hash what LANDED, same turn, never from memory
(remember the night five "remembered hashes" bit us; the rule is the scar).

---

## ONE MINUTE OF REAL LIFE (all rooms at once, tonight's actual numbers)

```
14:00:00  F1 wakes. A: BookMap printed a sticker 40 s ago - eyes are FRESH.
14:00:01  B1: river UP, CERTAINTY=100, FIRM since 9 candles.  B2: standing on `demand`,
          0.15 wiggles away, shelf fresh (CANDS=7).  B3: tolls... PAID. push... NONE.
          -> verdict: NO, named.  C1: one line, `TOLL=paid PUSH=NONE`, into the glue book.
14:00:02  F: three small cards on your screen. E: sleeps - no GO shape, hands stay folded.
          H: nothing to push yet. D: nothing to do until evening.
14:03:00  F1 wakes. The day grows by exactly one page. Nobody has to watch. That is the point.
```

## THE BEDTIME STORY OF EVERYTHING

*The ears hear the street through borrowed glasses, with their pens taped down. The judge's three
brains must ALL agree, and one tired eye means wait - and waiting counts as winning. The tolls must
be paid, and even a paid toll can still be told no. The book remembers every thought and every deed
on one growing stack of named pages, and the pen SHOUTS if a page falls out. The reader marks the
judge's homework and the money-ruler keeps telling us, kindly, "not yet - one coin at a time." The
hand only moves for a bank that proves it is practice, does one tiny thing at a time, asks the bank
TWICE, walks its stop home, and leaves receipts even for its own practice. The clockwork says its
name when it wakes. The shelf keeps 115 scars. The box keeps the night frozen.*
*And the real-money key - the only thing in the whole story with teeth - sits in the little owner's
pocket, where everyone agrees it stays until he himself, on purpose, says otherwise.*
