# Repair plan — measured defects, in priority order
**2026-09-25. Every item below was measured from your own tape, not reasoned about.**
Nothing here has been changed. Items marked **[your .env]** are yours to do; the rest are mine to
build, on your word.

---

## P1 — THE SILENT CEILING  **[your .env, one line, reversible]**

**Measured:** `struct` team = 0.00 in **608 of 608** cycles. Its weight 1.2 stays in the divisor.
Every strength reading is shrunk **24.5%**; the maximum possible reading is **75.5**, not 100.
A true 60 arrives at your gate as 45.3 and is discarded. Confluence forced **NEUTRAL 318 times
(52% of decided cycles)** waiting for a team that cannot exist.

**Cause:** teams are sliced by list position — `struct_votes = votes[35:45]` — and there are only
~11-20 votes per cycle, so the slice is always empty. The team called `whale` is not whale judges
either; it is whoever lands at index 15-35.

**Fix now:**
```
V6_4TEAMS_ENABLED=0
```
This falls back to the plain weighted average of all votes — honest arithmetic, no empty divisor, no
confluence rule demanding a ghost's approval. Delete the line to undo.

**Expect after restart:** more signals clearing the gate, and strength numbers roughly a third higher
for the same market. **Do not also change the gate on the same day** — change one thing, measure it.

**[my view]** This is the highest-value single line in the entire project. It is not a new feature;
it is removing a bug that has been quietly deleting your best signals.

---

## P2 — THE SIGNAL TEAM HAS NO DATA  **[investigation, then my build]**

**Measured:** across 13,982 note lines in a full trading day:
- `whale_walls` — never appeared
- `iceberg` — never appeared
- `spoof_invert` — never appeared
- `queue_pos` — never appeared
- `l3_large_ofi` — 1 vote all day

Working: `l3_imbalance` 608, `l3_net_flow` 384, `microprice` 55.

**What that pattern means:** the judges that work use **aggregate** L3 numbers (one imbalance value,
one OFI value, top-of-book sizes). The silent ones need **per-level depth** and **order-by-order
events** — adds, cancels, refills. That is the `mbo.csv` side of the bridge, and it is not reaching
the detectors.

**Why it matters:** those four are the irreplaceable ones — hidden size, lies, walls, queue position.
They are the whole reason to pay for Bookmap, and the entire "targets and force" architecture is
built on them. **Until they speak, the SIGNAL team does not exist and no wall map can be built.**

**Next check (before any code):**
1. Does `mbo.csv` exist, and is it growing during the session?
2. Does the Bookmap addon actually subscribe to order-level events, or only to aggregated depth?
3. Does `BOOKMAP_WRITE_MBO=1` reach the bridge that writes it?

---

## P3 — `absorption` VOTES INVISIBLY  **[my build, small]**

Its vote at `step2` line 2128 is **unconditional** — it votes every cycle — but it writes **no note**.
So it can never be seen or graded. It is also driven by `absorption_net`, a counter that is never
reset, so it reports the whole session's running total as if it were the current minute (two events
pin it at full strength for the rest of the day).

**Fix:** write a note when it votes, and reset or decay the counter. Then it becomes gradeable like
everyone else.

---

## P4 — ATR IS DEGRADED 37% OF THE DAY  **[investigation]**

**Measured:** `candle history shallow (# M# bars) - ATR/MTF/order blocks/HTF POC limited` appears
**246 times in 665 snapshots**.

ATR is the unit every stop, target, judge horizon and force measurement is denominated in. For over
a third of the day it is built on too few bars. This may be part of why the force study looks like
noise. Likely related to `BOOKMAP_WINDOW_SECONDS=10800` (3 hours) versus the 12 hours the `.env`
comment says M5 needs.

---

## P5 — 70% OF THE DAY IS FLAGGED HIGH-IMPACT NEWS  **[investigation]**

**Measured:** `HIGH impact sentiment ... (boosted)` ×469 and `high-impact event upcoming ->
confidence reduced` ×469, out of 665 snapshots.

Consequences while it is "HIGH": macro weights are boosted to 1.0 (confirmed by `real_roster`), and
confidence is cut by 10%. If the classifier really marks 70% of a normal day as high-impact, then
both effects are permanent rather than exceptional, and the "news protection" is just a constant tax.

---

## P6 — 2,432 NOTE LINES SAY NOTHING  **[cosmetic]**

`RSI vote removed`, `SMA20 vote removed`, `round numbers vote removed`, `Asian range vote removed` —
608 each, 17% of the diary. Tombstones for judges deleted in v5.8.1. Harmless, but they make the
diary harder to read and every note-parsing tool slower.

---

## Order I recommend

1. **P1 tonight** — one `.env` line, restart, and watch tomorrow's numbers. Biggest win, lowest cost.
2. **P2 investigation** — the three checks above. This decides whether v8 is possible at all.
3. **P4** — because ATR quality undermines every measurement we make, including the force study.
4. **P3**, then P5, then P6.

**Nothing about the v8 design is dead.** But it cannot be built on a SIGNAL team with no data, and it
should not be judged against a POWER baseline that is being throttled 24.5% by an empty divisor.
Fix what is measurably broken first, then re-measure, then design.
