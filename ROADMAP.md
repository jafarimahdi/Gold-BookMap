# The complete route — from today to a finished judge system
**2026-09-25.** Every step, in order, with what decides the next one.
Nothing in here is built without your word. Report-only steps are marked **[safe]**.

---

## WHERE WE ARE

**Done and proven:**
- 25 judges named, described, grouped (`JUDGE_GUIDE.md`)
- 8 judges retired — installed, activates on the next restart
- Teams defined: **POWER** (what already happened) / **SIGNAL** (orders waiting) / **ESCORT** (guards an open trade)
- Architecture agreed: **SIGNAL says where the targets are, POWER says whether we can reach them**
- Four measurement tools built and run on real tape

**Measured defects, none yet fixed:**

| # | defect | evidence | cost |
|---|---|---|---|
| D1 | `struct` team always empty, weight stays in divisor | 608/608 cycles | **every signal shrunk 24.5%**, max strength 75.5 not 100 |
| D2 | confluence waits for a team that cannot exist | 318/608 | **52% of cycles forced NEUTRAL** |
| D3 | wall thresholds set for the wrong instrument | biggest wall ever = 53 lots, threshold = 100 | **4 judges silent forever** |
| D4 | `absorption` votes every cycle, writes no note, counter never resets | code | invisible + pinned after 2 events |
| D5 | ATR degraded | 246/665 snapshots "candle history shallow" | **37% of the day** measured in a bad unit |
| D6 | 70% of the day flagged HIGH-impact news | 469/665 | permanent macro boost + permanent confidence tax |
| D7 | depth snapshot spread reads 11.70 on gold | log | `microprice` voted only 55 times |
| D8 | teams sliced by list position, not by name | code | the root cause of D1 and D2 |

---

## PHASE 0 — TODAY (25 Sep) · the ceiling comes off

**Change:** `V6_4TEAMS_ENABLED=0` in `.env`, then `python main.py --loop`.
Fixes **D1 + D2**. Also activates the 8 retirements for the first time.

**Tonight after 23:00:**
```bash
bash daily_check.sh 2026-09-25
python tools/signal_health.py --date 2026-09-25 --teams
python tools/real_roster.py --date 2026-09-25
```

**Branch:**
- `Confluence FAIL` = **0** and strength readings reach above 75.5 → Phase 0 worked, go to Phase 1
- the lines are still there → the `.env` edit did not reach the robot; fix and repeat
- trades jump alarmingly → revert to `=1`, restart, and we think again before anything else

---

## PHASE 1 — TOMORROW (26 Sep) · wake the SIGNAL team

**Change three `.env` lines** (measured from your own book, with my two overrides):
```
L3_WHALE_THRESHOLD=10
ABSORPTION_WALL_SIZE=5
SPOOF_SIZE_THRESHOLD=8
```
Fixes **D3**. Restart required.

**Check the next evening:** `python tools/signal_health.py --date 2026-09-26`

**Branch:**
- `whale_walls`, `iceberg`, `spoof_invert` now vote → **the SIGNAL team exists**, go to Phase 2
- still silent → the problem is not the threshold. Next suspect: whale detection reads only
  `order_events[-500:]`, and the L3 analyzer is rebuilt from scratch every cycle, so it may never
  hold enough of the book. That becomes a code fix, not a setting.
- they vote *constantly* (every cycle) → thresholds too low, raise toward p99.9 (18 / 7 / 9)

---

## PHASE 2 — repair the measurement instruments **[mostly safe]**

Before designing anything new, the instruments must be trustworthy.

- **D5 — ATR shallow 37% of the day.** Likely `BOOKMAP_WINDOW_SECONDS=10800` (3h) versus the 12h
  your own `.env` comment says M5 needs. Investigation first, then one setting.
- **D7 — spread 11.70.** Gold's spread is cents. The depth snapshot is probably not instantaneous.
  This corrupts `microprice`, `l3_imbalance` and `absorption` — three of the four SIGNAL judges that
  currently work.
- **D6 — 70% HIGH-impact news.** Either yesterday was extraordinary or the classifier is wrong.
  Costs you a permanent 10% confidence tax and a permanent macro boost.
- **D4 — `absorption`.** Give it a note so it can be graded; reset or decay its counter.

**Why before the new design:** ATR is the unit every target, stop and force reading is denominated
in. Building v8 on a bad ATR would poison every measurement we take afterwards.

---

## PHASE 2b — put the diagnostics INTO the one daily HTML **[safe]**

`force_study`, `real_roster`, `signal_health` and `depth_profile` currently print to the terminal.
Three of the four write no HTML at all. Your rule from the start has been:

> *"everything and all report regarding result will be in one HTML ... I run one time and see all of them"*

So each becomes a folding section inside `data/report_<date>.html`, beside the panel, the veto record
and the cross-day scoreboard:
- **the real roster** — what every judge's weight actually was today, and what swung
- **signal health** — who spoke, who was silent, and why
- **book profile** — what a "wall" really is on this instrument today
- **force vs distance** — once there are enough independent windows to say anything

**Why it matters beyond tidiness:** you will not run four terminal commands every night forever, and
a measurement nobody looks at is a measurement that stops being true.

---

## PHASE 3 — re-measure the baseline **[safe]**

With the ceiling off, the SIGNAL team awake and the instruments fixed, run the measurements again —
this time on a system that is not throttled:

```bash
python tools/force_study.py --days 5 --independent
python tools/real_roster.py --days 5
python tools/signal_health.py --date <day> --teams
```

**This is the first honest baseline this robot has ever had.** Everything measured so far was taken
through a 24.5% ceiling with half the panel mute.

**Branch on the force study:**
- force picks the side > 55% → POWER is a direction-picker, v8 proceeds as designed
- force is ~50% → POWER becomes a **filter**, and SIGNAL's walls decide everything. Simpler robot,
  still good, and honestly the more likely outcome on today's evidence
- the hump reappears with 25+ independent readings per band → a force **window**, not a minimum

**Timing:** needs ~3 trading days for the 15-min answer, 6 for 30-min, 11 for 60-min.

---

## PHASE 4 — build the SIGNAL map **[safe, report-only]**

The list of trusted targets, logged every cycle and shown in the daily report:
`price · size · side · distance in ATR · trust (age, persistence, not in spoof_levels)`

**Changes no behaviour.** You watch it for several days and compare the walls it names against what
you see in Bookmap with your own eyes. That comparison is worth more than any statistic.

**Depends on:** Phase 1 succeeding. Without wall detection there is nothing to map.

---

## PHASE 4b — WALL-AWARE STOPS **[the football idea — full design in `WALL_STOPS_DESIGN.md`]**

This was agreed, designed, and then **left out of the first version of this roadmap**. It is not part
of the SIGNAL map — it is what the map is *for*.

Three pieces, in order:
1. **Stop throwing the wall prices away.** `whale_walls` already computes `closest[0]`, the exact
   price, and prints it into a sentence. It must become a field on the snapshot, next to the
   existing `iceberg_levels` / `iceberg_meta`.
2. **Feed them to `compute_structural_stops()`.** The function already front-runs the nearest
   opposing level (`tp = lvl - sign * PM_TP_BUFFER_ATR * atr`) — it just never sees a live wall,
   only order blocks, volume nodes and round numbers. Add walls as candidates. No new maths.
3. **The confirmation before the shot.** If the nearest trusted opposing wall is closer than
   `PM_TP_MIN_ATR * atr` → **do not trade**, and log the reason. Precedent exists: step4 already
   asks L3 "is there a spoof wall here?" before placing a limit.

**Trust filter is mandatory** — age (`iceberg_meta.age_sec`), size, and never a price listed in
`spoof_levels`. Without it this feature makes you worse, not better.

**Shadow first**, always: compute wall-based SL/TP, write them into the diary beside the real ones,
change nothing. Then the audit shows *"wall stops vs ATR stops on the same trades"* for several days.

**Open questions you have not answered yet** (from `WALL_STOPS_DESIGN.md` §9):
- blocked front wall → **skip the trade**, or **shrink the TP**?
- first version: **TP only**, or **TP and SL together**?

---


## PHASE 5 — build the POWER reading **[safe, report-only]**

One signed number in the same units as distance, built from the 16 POWER judges, shaped by whatever
Phase 3 proved: a reach estimate if force predicts distance, a side-picker or a filter if not.

---

## PHASE 6 — shadow decisions **[safe]**

Every cycle, write what v8 **would** have done next to what the robot actually did:
> *"v8: BUY, target 2043.6 (180 lots, 1.4 ATR), TP 2043.2, SL 2039.1 — robot: NEUTRAL"*

Compare for as long as you like. **Still changes nothing.**

---

## PHASE 7 — hand over the keys

Only when Phase 6 has convinced you. One `.env` switch, reversible, with the old brain still there.

---

## PHASE 8 — the ESCORT team

Built last, on purpose: it manages open positions, and there is no point perfecting the exit of
trades the new brain is not yet choosing. Design is already agreed in `TEAMS_DESIGN.md`, including
the rule that **Escort may only ever make a trade safer** — never widen a stop, never add size.

---

## STILL OPEN, NOT FORGOTTEN

| item | status |
|---|---|
| **D8 — teams by name** | superseded for now by switching teams off; still the proper fix if teams ever return |
| **Judge-correlation matrix** | explained and agreed valuable. Largely solved *by design* through POWER/SIGNAL, so it becomes a verification tool, not a prerequisite |
| **Dual-clock grading** (own clock + robot clock 2.0/3.5) | report-only, cheap, high value. Fits naturally in Phase 3 |
| **`AI_AS_VOTE=1`** | already ON in your `.env`. The AI is a voting captain, not only a veto. Needs a place in the POWER/SIGNAL design |
| **`BUDAPEST_UTC_OFFSET=2`** | fixed number; DST ends **25 October** and it silently becomes wrong |
| **`MAX_SPREAD_PCT=0.05` vs `V6_CFD_SPREAD_MAX=0.50`** | two spread limits 10x apart; matters once walls set targets |
| **Three unweighted judges** | `whale_balanced`, `trend_macd`, `sma20` parse but never count |
| **Gate re-tune** | Tuesday 29th, `--gate 63` / `--gate 45` — but **only after** Phases 0-1 settle |
| Day-picker on the report, "what changed since yesterday", weight slider | agreed ideas, unscheduled |

---

---

## AUDIT OF THIS ROADMAP (2026-09-25) — what the first version missed

Checked against every design document and every promise made in the workspace.
**Nine gaps found. All are now recorded here.**

| # | what was missing | where it belongs |
|---|---|---|
| G1 | **Wall-aware SL/TP** — designed in `WALL_STOPS_DESIGN.md`, absent from the plan | added as **Phase 4b** |
| G2 | **`queue_pos` will NOT wake up in Phase 1.** Its gate is `QUEUE_POS_THRESHOLD=0.7`, a *fill-probability ratio*, not a lot size. The three size thresholds do not touch it | needs its own diagnosis in Phase 1 |
| G3 | **The new tools are console-only.** `real_roster`, `signal_health`, `depth_profile` print to the terminal and write nothing into the daily HTML. That breaks your standing rule: *"everything in one HTML, I run one time and see all of them"* | new **Phase 2b** |
| G4 | **`PANEL_ROSTER.md` is still the stale table** and you asked for that roster to be kept accurate | regenerate from `real_roster.py` in Phase 2 |
| G5 | **Model cooldown in step3 (C5)** — proposed, never answered, dropped off the list | added to Still Open |
| G6 | **The five new tools are not committed to git** — they exist only on your disk and mine | do with the next push, tag `good-2026-09-25b` |
| G7 | **`TEST_SYSTEM_AUDIT_2026-09-24.md` D8 "no dedup across tape sources"** — the one item of 16 never shipped. `grep dedup audit_day.py` = 0 | Still Open, watch item |
| G8 | **Horizon derived from distance** — the best idea in `PLATFORM_DECISIONS.md` §1, never scheduled. In v8 a judge's clock stops being a setting and becomes *"how long should this distance take?"* | belongs in Phase 5 |
| G9 | **`l3_large_ofi` voted exactly once in a full day.** Not silent, not working either | investigate in Phase 1 |

### Also re-confirmed, so it cannot be lost
- **Footprint stays.** Your standing instruction from the beginning. `footprint_delta`,
  `footprint_levels`, `cvd_momentum`, `cvd_divergence`, `volume_roc` are all active. Only
  `delta_pressure` was retired, and only because it duplicates `footprint_delta` on a slower clock.
- **`.env` is never shipped in a zip.** Every change is given to you as lines to edit yourself.
- **One zip per change set**, `unzip -l` verified, clean-unzip rehearsed, sizes quoted every time.
- **The audit system is the product**, not a side tool. Everything above is measured by it.

## THE ONE RULE FOR ALL OF IT

**One change at a time, then measure.** Every defect on this list was found by measuring, and three
of my own confident conclusions were destroyed by the next measurement. The robot is not short of
ideas — it has been short of honest instruments. That is what we are building first.
