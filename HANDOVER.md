# Gold-BookMap — MASTER HANDOVER
**Written 2026-09-25. Complete end-to-end state, history and plan.**

If you are an assistant picking this up cold: read this file first, then the six documents named in
§4. That is enough to continue without asking the owner to repeat anything.

Owner: Jafar. Machine: Windows, Git Bash, `A:\gitHub\Gold-BookMap`.
Repo: `github.com/jafarimahdi/Gold-BookMap` (public). Timezone: Europe/Budapest.

---

# 1. THE GOAL

A gold (XAUUSD) trading robot fed by **Bookmap** market data, ported from a NinjaTrader design.
Trades the Budapest session **08:00–23:00 local (06:00–21:00 UTC)**, runs continuously.

**The product is not only the robot. It is the audit/test system.** The owner's words:

> *"the real goal and main thing here is focusing in the app by itself and making the test system
> work perfectly, which can give info about everything were done during the day"*

And the delivery format:

> *"everything and all report regarding result will be in one HTML ... I run one time and see all of them"*

So: **one self-contained HTML per day, `data/report_<date>.html`, that explains everything the robot
did and whether it was right.**

---

# 2. HOW THE ROBOT WORKS (the live chain)

```
step1_data_acquisition   Bookmap bridge -> ticks.csv (trades) + mbo.csv (order events)
                         instrument: GCZ6.COMEX@RITHMIC, 20 depth levels per side
step2_market_analysis    25 judges each vote -1 / 0 / +1 with a weight
                         score = sum(dir*weight) / sum(weight)  -> strength 0-100
                         macro opposition rule can shrink it or cap to NEUTRAL
                         confidence = agreeing judges / total judges   <-- A HEAD COUNT
step3_ai_decision        Gemini. Can VETO, and with AI_AS_VOTE=1 it also votes
step4_mt5_execution      gate: confidence >= 50; structural SL/TP; sends to MT5
step5_monitoring         60-second loop (MONITOR_POLL_SECONDS=60)
position_manager         manages open trades: BE, TRAIL, TP_UPDATE, FLIP_EXIT,
                         DIVERGENCE_EXIT, TIME_STOP, news protect, session flatten
audit_day.py             grades everything afterwards -> the daily HTML
```

Execution is **MT5 CFD (Pepperstone, XAUUSD)** while the data is **COMEX futures**. The code tracks
the basis, refuses to trade beyond `BASIS_MAX=50`, and pauses on a fast widen. Futures levels are
scaled to CFD prices before use.

---

# 3. THE TEST SYSTEM — this is the heart of the project

## 3.1 `audit_day.py` — the nightly auditor
15 tests over one day of tape, producing one HTML. It never trades and never writes to the robot.
Key properties, all deliberate:
- **stats measured inside their own frame** — a filtered run never overwrites a whole-day card
- **no silent ceilings** — anything capped is stated
- **unreadable data never reduces a total** — skipped days are *named*, with the reason
- **verification by marker + size**, never by trust: `grep -c "audit-2026-09-25v" audit_day.py`

Commands:
```bash
bash daily_check.sh 2026-09-25          # the night run -> data/report_<date>.html
python audit_day.py --date 2026-09-24   # re-audit one day
python audit_day.py --days 3            # 3-day window
python audit_day.py --scoreboard 5      # cross-day (also embedded in the day report)
python audit_day.py --weight-ab 3       # weight A/B study
python tools/selftest_audit_day.py      # must print: SELFTEST PASSED: all 15 tests
bash preflight.sh 2026-09-25            # read-only environment check
```

## 3.2 The daily HTML
One page, self-contained, no external resources. Sections, each foldable:
panel scoreboard → AI veto record → cross-day scoreboard with a day dropdown → full night report →
every decision → trades → judges. Panel table defaults to "graded today"; descriptions behind a toggle.

## 3.3 The measurement tools (built 2026-09-25)
These were built to answer design questions with evidence instead of opinion. **All report-only.**

| tool | question it answers | key output |
|---|---|---|
| `tools/force_study.py` | does a louder panel travel further? does it pick the right side? | correlation, force bands, MFE/MAE/hit, `--independent` for honest sample sizes |
| `tools/real_roster.py` | what weight did each judge REALLY have? | median/min/max per judge, runtime swing, mismatch vs the audit table |
| `tools/signal_health.py` | which judges spoke, which were silent, and why | per-judge diagnosis, `--teams` shows the 4-team scores, `--all-notes` dumps note shapes |
| `tools/depth_profile.py` | what is a "wall" on this instrument? | rebuilds the book from `mbo.csv`, percentiles, threshold suggestions |

Each has an **honesty guard** that refuses to answer when the data cannot support it. Those guards
were not decoration — they caught three wrong conclusions in one day (see §7).

---

# 4. THE DOCUMENTS

| file | what it is |
|---|---|
| `HANDOVER.md` | **this file** — start here |
| `ROADMAP.md` | the complete route, 10 phases, with branch conditions and a gap audit |
| `JUDGE_GUIDE.md` | all 25 active judges, one by one: what each reads, how it votes, its unique power |
| `JUDGE_MAP.md` | the family tree — which judges share a data source, and the weight clusters |
| `TEAMS_DESIGN.md` | POWER / SIGNAL / ESCORT: membership, reasoning, and the Escort rules |
| `ARCHITECTURE_V8.md` | the "targets and force" design in full |
| `WALL_STOPS_DESIGN.md` | wall-aware TP/SL — the owner's football idea, designed, not built |
| `REPAIR_PLAN.md` | the measured defects in priority order |
| `PLATFORM_DECISIONS.md` | timeframe, depth levels, CFD-vs-futures |
| `HORIZON_REVIEW.md` | judge clocks vs the robot's real SL/TP, and the mismatch |
| `TODO_BOOKMAP.md` | the live to-do list, with a RETIRED section for items deleted and why |
| `PANEL_ROSTER.md` | the 33-judge roster — **STALE, see G4 in the roadmap** |
| `TEST_SYSTEM_AUDIT_2026-09-24.md` | end-to-end audit of the auditor itself, 9 defects, 8 fixed |
| `DESIGN_V8_JUDGES.md` | the earlier five-table design, superseded by `TEAMS_DESIGN.md` |

---

# 5. THE JUDGES — 25 active, in two teams

**POWER (16, weight ~8.1 measured) — what has ALREADY happened.** Finished trades, candles built
from them, and the outside world:
`footprint_delta · sweep · l3_aggr_limit · cvd_divergence · cvd_momentum · footprint_levels ·
volume_roc · vwap_bands · htf_poc · vwap_trend · supply_demand · poc_day · value_area · mtf ·
news_sentiment · macro_risk`

**SIGNAL (9, weight ~4.0 measured) — orders WAITING that have not happened yet.** The only judges
that can see the future, and the reason to pay for Bookmap:
`l3_net_flow · whale_walls · iceberg · l3_imbalance · l3_large_ofi · microprice · spoof_invert ·
queue_pos · absorption`

**ESCORT (14, designed, not built)** — wakes only while a position is open, organised by job:
is my exit still clear / is the crowd turning / is my push dying / where do I move my stop / is a
bomb coming. **Rule: Escort may only ever make a trade SAFER.**

**Retired 2026-09-24 by the owner (8):** `macro_yield`, `macro_dxy`, `macro_vix` (120-min clock,
wrong horizon for a scalp); `vwap_zscore`, `spoof_invert_loose`, `iceberg_legacy`, `l3_ofi_streak`,
`delta_pressure` (duplicates). They **still write notes** so the audit can keep grading them —
a free A/B. Restore with `RETIRED_JUDGES=` in `.env`. The macro **brake** survives deliberately
(`macro_pairs` still fills); `MACRO_BRAKE_ENABLED=0` disables it loudly.

**10 judges are genuinely irreplaceable** — nothing else can recover what they see: `iceberg`
(hidden size), `spoof_invert` (lies, read backwards), `absorption` (a push that produced nothing),
`cvd_divergence` (price and pressure disagreeing), `microprice` (the move before the move), `sweep`
(urgency paying a worse price), `queue_pos` (whether *we* get filled), `mtf` (do the clocks agree),
`htf_poc` (memory older than today), `news_sentiment` (the reason WHY).

---

# 5B. THE ESCORT TEAM — designed in full, not yet built

## 5B.1 What it is

POWER and SIGNAL decide whether to open a trade. **ESCORT is the only team that works after the
order is filled.** It sleeps while flat and wakes the moment a position exists, running every cycle
until TP or SL closes it. Then it sleeps again.

> **Five-year-old version:** POWER is your friends shouting *"go left, the goal is open!"*
> SIGNAL is you looking up to check nobody is standing in the way before you kick.
> **ESCORT is your friends running beside the ball, shouting "careful!" or "keep going!" until it
> crosses the line.**

**It already exists — it just has no names.** `position_manager.PositionManager.manage()` runs every
cycle on every bot position (magic 234000; manual trades are never touched) and already applies:

| existing rule | what it does |
|---|---|
| `ADOPT_TIGHTEN` | first sight of a position with an SL wider than `PM_MAX_SL_ATR` → tighten to structure |
| `BE` | at `PM_BE_TRIGGER_R` × initial risk, SL moves to entry ± spread (trade is now free) |
| `TRAIL` | SL trails fresh structure; **only ever tightens**, never widens, with a cooldown |
| `TP_UPDATE` | TP front-runs newly formed opposing structure; may move closer any time, further only once break-even |
| `FLIP_EXIT` | composite signal flips hard against the position **and** order flow confirms → close early |
| `DIVERGENCE_EXIT` | CVD diverges against the position while not yet in decent profit → close |
| `TIME_STOP` | older than `PM_TIME_STOP_MINUTES` with less than `PM_TIME_STOP_MIN_PROGRESS` toward TP → dead trade, free the margin |
| news protect | `PM_NEWS_PROTECT_MINUTES` before a high-impact event → tighten or flatten |
| session flatten | `PM_DAILY_FLATTEN_UTC=21:30` |

**Two judges are already wired into it without being named as such:** `cvd_divergence` *is* the
`DIVERGENCE_EXIT` rule, and `footprint_delta` is what confirms `FLIP_EXIT`.

**What is missing is the panel.** Because the rules are anonymous, the audit can never tell you
*which* escort decision saved money and which one cut a winner short. That is the whole reason to
formalise it.

## 5B.2 The 14 judges, organised by JOB not by data source

While a trade is open you do not care where a number came from. You care what it is warning you
about. A judge can serve in POWER or SIGNAL and still escort — same person, different shift.

### Job 1 — "Is my exit still clear?" (the wall watchers)
| judge | what it watches in-trade |
|---|---|
| `whale_walls` | a **new** wall appears before your TP → take profit earlier; don't wait for a fill that will never come |
| `iceberg` | hidden size defending your target → the wall is bigger than it looks, get out in front of it |
| `spoof_invert` | the wall you are aiming at is **fake** → it will vanish, so your TP can safely run further |

### Job 2 — "Is the crowd turning against me?" (early warning)
| judge | what it watches |
|---|---|
| `microprice` | the very first tilt against you, in fractions of a penny — **the earliest exit signal that exists** |
| `l3_net_flow` | orders massing on the other side |
| `l3_imbalance` | the book's shape flipping against your direction |

### Job 3 — "Is my push dying?" (momentum doctors)
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
| `news_sentiment` | high-impact event approaching → tighten or flatten **before** it lands |

### Deliberately excluded, and why
- `queue_pos` — it only answers *"will we get filled?"*. Once you are in, it has nothing to say.
- `footprint_levels`, `cvd_momentum`, `volume_roc` — too slow and noisy for minute-by-minute management.
- `vwap_trend`, `vwap_bands`, `value_area`, `mtf`, `macro_risk` — they barely move inside one trade;
  they would chatter constantly and decide nothing.
- `l3_large_ofi` — already covered by `l3_net_flow`. Two voices saying one thing is the mistake we
  just removed from the panel.

## 5B.3 How it will work, cycle by cycle

```
position open?  no  -> Escort sleeps, costs nothing
                yes -> every 60s:
      Job 1..5 each produce AT MOST ONE recommendation, with the judge's name attached
      -> the manager applies only actions that make the trade SAFER
      -> every action is journalled: rule, judge, reason, price, time
      -> the nightly audit grades each action against "what if we had done nothing?"
```

**Five recommendations maximum per cycle, never fourteen.** Otherwise we have recreated the shouting
problem, this time with an open position and real money in it.

## 5B.4 The three rules, non-negotiable

1. **Escort may only ever make the trade SAFER.** It can tighten the stop, take profit earlier,
   close, or partial-close. It may **never** widen a stop, push a target further away on a losing
   trade, or add size. `TRAIL` already enforces "only ever tightens" — that becomes the law for the
   whole team. An escort that can loosen protection is not an escort, it is a second gambler.
   *(One deliberate exception already in the code: `TP_UPDATE` may move a target further away **only
   once the trade is break-even** — a locked trade is allowed to run. That stays.)*
2. **Escort must be graded separately.** A new report section: *"what did the escort do today, and
   was it right?"* Every early exit compared against what the trade **would** have done if left
   alone. Expect it to save money on some days and cut winners short on others — you will want to
   tune it, and you cannot tune what you cannot see.
3. **One voice per job.** Five jobs, five named recommendations, no averaging of fourteen opinions.

## 5B.5 How Escort connects to the wall design

Escort and the wall-aware stops (Phase 4b) are the same machinery at two different moments:

- **At entry**, SIGNAL finds the front wall → that is the TP. The back wall → that is the SL.
- **While open**, Escort Job 1 keeps watching *those same walls*. If the wall your TP front-runs
  **disappears**, the reason for that target is gone. If a **new** wall appears in front of it, the
  target is no longer reachable and profit should be taken earlier.

This is why Escort is built last: it guards targets that Phase 4b has not yet created. Building the
guard before the thing it guards would produce a team with nothing to watch.

## 5B.6 Status and dependencies

- **Design:** complete and approved by the owner — *"keep what you designed and i really like it"*.
- **Build:** **Phase 8**, last on purpose. There is no point perfecting the exits of trades the new
  brain is not yet choosing.
- **Depends on:** Phase 1 (the wall judges must actually speak — `whale_walls`, `iceberg`,
  `spoof_invert` are silent today), and Phase 4b (the walls must be placing targets).
- **Owner deferred rule 1** ("only ever safer") for later confirmation along with the build. It is
  recorded here so it is not lost.
- **Risk to watch:** an escort that exits too eagerly will quietly destroy profitability while every
  individual decision looks sensible. That is exactly why rule 2 — separate grading against
  "what if we had done nothing" — is not optional.

---

# 6. THE NEW DESIGN — "targets and force"

The owner's framing, and it is better than an opinion-average:

```
SIGNAL  ->  "Here are the targets: where the resting orders are, how big, how far, how trustworthy."
POWER   ->  "This is how much force exists now. Enough to reach the target above / below / neither."
DECIDE  ->  trade only when force >= distance to a trusted target.
            TP just before it. SL just behind the wall on the other side. No room -> no trade.
ESCORT  ->  guards the position until TP or SL.
```

Why it is better than the current system: today's robot produces *"confidence 58%"*, which reality
cannot judge. This produces *"there is a 180-lot wall at 2043.6, 1.4 ATR away, force is 1.9 ATR"* —
and 15 minutes later the tape answers **yes or no**. It is gradeable, so the audit system can finally
do its job properly. It also dissolves the duplicate-judges problem by design, because SIGNAL stops
voting entirely and starts reporting facts.

---

# 7. WHAT WAS MEASURED ON 2026-09-25 — and what it destroyed

Three confident conclusions died the same day they were born. Record them so nobody re-derives them:

1. **"Moderate force travels furthest" (the hump).** Looked convincing across three horizons.
   **Killed** by `--independent`: overlapping 15-minute windows counted the same tape 15 times.
   With non-overlapping windows the peak moved to a different band at every horizon. One day has
   only ~44 independent 15-min windows, not 590.
2. **"10 of 11 judge weights are wrong."** Derived by guessing which `SIGNAL_W_*` key feeds which
   judge. **Killed** by `real_roster.py`: 17 of 21 actually match. Only 4 disagree.
3. **"The wall thresholds are not the problem."** `signal_health` said the detectors "never ran".
   **Killed** by the bridge log: the detector runs every cycle, finds nothing, and its silence
   branch also requires a wall, so it prints nothing at all.

**The lesson to carry forward: hold the plan loosely and the measurements tightly.**

4. **"Walls do not hold, they get sliced through."** The barrier test measured max excursion
   over the WHOLE horizon, so any trending day read as "sliced through". **Killed** by measuring
   the 2 bars from the touch instead: same data, opposite answer.
5. **"Going to the wall wins 100% of the time."** Win/loss flags with a near TP and a far stop
   always look brilliant. **Killed** by scoring in ATR instead - the same trap that makes an
   83%-right judge lose money.

**Six measurement-driven corrections in one day. Treat any brand-new metric as provisional until
its method has been attacked.**

## 7.1 The defects that survived measurement

| # | defect | evidence | cost |
|---|---|---|---|
| D1 | `struct` team always empty, its 1.2 weight stays in the divisor | 608/608 cycles | **every signal shrunk 24.5%**; max strength 75.5, not 100 |
| D2 | confluence waits for that empty team | 318/608 | **52% of cycles forced to NEUTRAL** |
| D3 | wall thresholds set for the wrong instrument | biggest wall ever seen = 53 lots; threshold = 100 | `whale_walls`, `iceberg`, `spoof_invert`, `queue_pos` **silent all day** |
| D4 | `absorption` votes every cycle, writes no note, counter never resets | code | invisible to the audit; pinned to full strength after 2 events |
| D5 | ATR degraded | 246/665 snapshots "candle history shallow" | **37% of the day** measured in a bad unit |
| D6 | 70% of the day flagged HIGH-impact news | 469/665 | permanent macro boost + permanent 10% confidence tax |
| D7 | depth snapshot reports spread 11.70 on gold | bridge log | `microprice` voted only 55 times |
| D8 | teams sliced by list position, not by name | code ~line 2868 | root cause of D1 and D2 |

## 7.2 The book, measured from `mbo.csv` (300k events)

```
single order size          p50=1    p99=2     max=46
total resting at a price   p50=2    p90=7     p95=8    p99=11   p99.9=18   max=53
adds by side               BID 58,741   ASK 57,842   (balanced)
event vocabulary           BID_NEW / ASK_NEW / CANCEL / REPLACE   (no FILL events)
```
**Half of all orders in COMEX gold are one lot.** Recommended thresholds, with reasoning in §8.

## 7.3 What the panel can actually do

Force picked the correct side **40.9% / 47.8% / 41.7%** (15/30/60 min, independent windows).
Samples are too small to conclude, but **nothing so far shows POWER has an edge on a scalping
horizon** — and it has been running throttled 24.5% with half the panel mute, so it has not yet had
a fair test.

---

# 8. THE PLAN FROM HERE (summary — full version in `ROADMAP.md`)

| phase | what | touches live loop? |
|---|---|---|
| **0** | `V6_4TEAMS_ENABLED=0` → removes the 24.5% ceiling and the 52% NEUTRALs | yes (restart) |
| **1** | `L3_WHALE_THRESHOLD=10`, `ABSORPTION_WALL_SIZE=5`, `SPOOF_SIZE_THRESHOLD=8` → wakes the SIGNAL team | yes (restart) |
| **2** | repair the instruments: ATR, spread 11.70, news 70%, `absorption` | mixed |
| **2b** | put all four diagnostics INTO the one daily HTML | no |
| **3** | re-measure the baseline on a system that is not throttled | no |
| **4** | build the SIGNAL map (trusted targets, logged only) | no |
| **4b** | wall-aware TP/SL + "no room, no trade", shadow first | no while shadow |
| **5** | build the POWER reading; horizons derived from distance | no |
| **6** | shadow decisions written beside the real ones | no |
| **7** | hand over the keys, one reversible switch | yes |
| **8** | build ESCORT | yes, last |

**Threshold reasoning (do not take the tool's numbers literally):** whale 10 ≈ p99. Absorption 5 not
7, because absorption watches only the single best level. Spoof 8 not 2, because half of all orders
are 1 lot and a threshold of 2 would make every ordinary cancel a "spoof".

---

# 9. CURRENT VERSION AND HOW TO VERIFY

**Owner's installed build:** `audit_day.py` **244910** bytes.
Confirm which marker with: `grep -o "audit-2026-09-2[0-9][a-z]" audit_day.py | head -1`
(24u and 25v are byte-identical apart from the marker string.)

Other installed files: `step2_market_analysis.py` **211391**, `config.py` **30153**,
`tools/selftest_audit_day.py` **43741**, `tools/insight.py` **33659**, `PANEL_ROSTER.md` **4853**,
`tools/force_study.py` **20309**, `tools/real_roster.py` **6894**, `tools/signal_health.py` **8559**,
`tools/depth_profile.py` **9202**.

**Git:** last push `1b1050c`, tag `good-2026-09-25a`, all tags pushed.
**Rollback:** `git checkout good-2026-09-25a -- .`
**The five measurement tools are NOT yet committed** — next push should tag `good-2026-09-25b`.

**Release history this week:** 24g install+features · 24h/24i roster+judges page+MT5 cross-check ·
24j DST fix · 24k–24m risk review (15 of 16 shipped) · 24n AI veto record · 24p friendly one page ·
24q/24r cross-day scoreboard + dropdown · 24s/24t em-dash fix, folding sections, cross-day embedded ·
**24u** 8 retirements + selftest midnight fix · 25v–26d the four measurement tools.

---

# 10. OWNER'S STANDING RULES — do not break these

1. Explain like to a 5-year-old — plain words, short sentences, analogies — but stay professional.
2. **Never** ship `.env` in a zip. Give the lines; he edits them. `.env.example` is allowed.
3. Every runnable deliverable inside **ONE zip per change set**, verified with `unzip -l` and a
   clean-unzip rehearsal first. Never tell him to run a file he does not have.
4. Always quote **file sizes** and include an `ls -la` step.
5. Give the **complete remaining route** with branch conditions, not one step at a time.
6. He runs live and pastes raw output. Verify his numbers against your own runs. **Admit defects
   explicitly.**
7. No reminder or scheduled-task tooling — he removed it deliberately.
8. His data lives on his machine. Missing data in the assistant's workspace is not a finding.
9. He monitors from a second Git Bash while `--loop` runs in the first — those commands stay read-only.
10. **Nothing in the robot changes without his confirmation.**
11. Keep **footprint** in the system. Keep the **33-judge roster** saved; he will ask for it again.
12. One change at a time, then measure.

---

# 11. OPEN DECISIONS HE HAS NOT MADE

- wall-blocked entry: **skip the trade** or **shrink the TP**?
- wall stops first version: **TP only** or **TP and SL**?
- **model cooldown in step3 (C5)**: pause the AI after N consecutive errors — proposed, unanswered
- `AI_AS_VOTE=1` is already ON; the AI votes as well as vetoes. Needs a defined place in POWER/SIGNAL
- gate re-tune (`--gate 63` / `--gate 45`) — planned for Tuesday 29th, **only after phases 0–1 settle**
- `BUDAPEST_UTC_OFFSET=2` is a fixed number and **DST ends 25 October** — it will silently be wrong
- `MAX_SPREAD_PCT=0.05` vs `V6_CFD_SPREAD_MAX=0.50` — two spread limits, 10x apart
- `whale_balanced`, `trend_macd`, `sma20` are parsed but carry no weight — three judges speaking
  into the void
- `TEST_SYSTEM_AUDIT` D8 "no dedup across tape sources" — the one item of 16 never shipped

---

# 12. IMMEDIATE NEXT ACTION

```bash
cd /a/gitHub/Gold-BookMap
grep -n "V6_4TEAMS_ENABLED" .env     # must be exactly one line, ending in =0
python main.py --loop                # the robot; leave this window alone
```

Then tonight after 23:00:
```bash
bash daily_check.sh 2026-09-25
python tools/signal_health.py --date 2026-09-25 --teams
python tools/real_roster.py --date 2026-09-25
```

**The number that decides Phase 0 worked: `Confluence FAIL` must be 0.** Yesterday it was 318.
