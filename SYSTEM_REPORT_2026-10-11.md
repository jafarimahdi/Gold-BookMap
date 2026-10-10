# 🤖 THE GOLD ROBOT — END-TO-END SYSTEM REPORT
**Written 2026-10-11 · after the A1 install (`1fc0708`), the test-suite green (160/160), and the first three nights of Lens + Anatomist readings**
*Owner's copy — plain language, step by step, no secrets.*

---

## PART 0 — The whole system in one paragraph

Every trading day, a robot watches the gold market through Bookmap's eyes, thinks about it every 5 minutes (and since this week: serves its thinking instantly between bells), almost always decides **"no trade"** — and writes everything down. Every evening, a newspaper team of **seven desks** reads the day's tape and diaries and tells the owner the truth: what the robot did, what it refused, what its own ideas would have earned, whether the robot's glasses (timeframe) saw the fish, what the fish looked like inside, and whether all the tellers agree. The robot has **never sent a real order** (demo only, `ALLOW_LIVE_TRADING=0`), and on the last three days its refusals were worth **+2,215.9 points saved**. The system is currently a **well-instrumented research robot**: excellent at not losing, not yet proven at winning — and the gap between the two is now *measured*, with a fix program underway.

---

## PART 1 — The day, step by step (the live pipeline)

```
 Bookmap (the market's camera)
   │  every trade print, every order-book level, every order event
   ▼
 the add-on + bridge (the translators) ──── the Watchman 🐕 checks the tape's heartbeat
   │                                           (barks if prices go silent 120s)
   ▼
 STEP 1 — data acquisition (one combined picture of the market)
   ▼
 STEP 2 — THE BRAIN (step2_market_analysis.py, ~4,700→5,045 lines)
   │  ├─ The Scout (signal_team): the door notebook — where the walls (big resting
   │  │   orders) are, which are growing/fading, where the targets and shelters are.
   │  │   Runs EVERY cycle (~5s), eyes never close.
   │  ├─ 33 judges: order-flow, footprint, L3, technicals, VWAP/value-area,
   │  │   macro (dollar/yields/VIX), news — each votes UP/DOWN with a weight & clock.
   │  ├─ POWER v2 (the coach): force shares per side, regime (TREND/RANGE via
   │  │   M5 ADX ≥ 25), and hard gates (activity, coverage, dominance, agreement).
   │  │   Says NEITHER most of the time — by design on calm days.
   │  ├─ The Signal Engine: confidence 0–100 from the panel.
   │  ├─ Shooting (plan_shot): only when POWER says UP/DOWN — checks the nearest
   │  │   Scout door, road ratio, reward/risk ≥ 1.2, queue fill, news state.
   │  │   Output is ALWAYS "PLAN_ONLY" — never transmits. GO has never fired.
   │  ├─ The paper-fill simulator + Escort (the exit guard): manage pretend
   │  │   positions — trail, profit lock, session flatten. Every cycle.
   │  └─ 🆕 A1 BAR SERVICE (installed 2026-10-10): the judges/POWER/technicals
   │      are computed ONCE per completed M5 bar and served (~4 ms) between bells;
   │      the Scout, fills, Escort and quotes stay live every cycle.
   │      Twin-proven: 0 verdict differences at 36/36 bells, 81% less brain time.
   ▼
 STEP 3 — the AI (Gemini): a VETO only. Can refuse a trade all 33 judges wanted.
   ▼
 STEP 4 — MT5 (the broker terminal): DEMO only. 0 orders sent in the entire
          recorded history of this build. The pipeline ends in a locked door.
   ▼
 the diary (snapshots_history.jsonl + decisions CSV) — every cycle written down,
          plus ticks.csv = the raw tape (the single source of truth).
```

**The day's safety net (always on):** flash-crash kill switch · daily loss cap 3% · max drawdown 10% · cooldowns · session gate 08:00–23:00 Budapest · evening flatten 21:30 UTC · lot 0.01 fixed.

---

## PART 2 — The evening, desk by desk (the newspaper)

One command — `bash report_evening.sh <date>` (v1.2, the Lens and Anatomist joined 2026-10-11):

| # | Desk | Tool | The question it answers |
|---|---|---|---|
| 1 | 🧮 **The Accountant** | `decision_ledger.py` | Who stopped each signal (confidence gate / team veto), and was stopping right? *(counterfactual pts)* |
| 2 | 📐 **The Geometer** | `tf_study.py` | How fast does the day's signal edge fade — same decisions replayed under M3/M5/M8/M15 exit geometries? |
| 3 | 🎭 **The Shadow** | `shadow_replay.py` | What would the robot's OWN wall-logic exits have done vs a fixed ruler? (5-day trial running) |
| 4 | 👓 **The Lens** 🆕 | `tf_lens.py` | Would finer clocks (M2/M3/M8/M10) have SEEN more moments than the M5 home clock — and were they real fish? |
| 5 | 🐟 **The Anatomist** 🆕 | `fish_anatomy.py` | The day's fish opened: WHEN they swim, how BIG minute-by-minute, how much HEAT before payoff, whether the PANEL noticed them |
| 6 | 📰 **The Editor** | `audit_day.py` (via `daily_check.sh`) | The 16-test grade of the day: tape health, speed, judges, what-if |
| 7 | 🐕 **The Watchman** | `tape_watchdog_v2.sh` log | Did the tape ever go silent/frozen? What time? |
| — | ⚖️ **Reconciliation** | (built into the script) | Accountant = Geometer = Shadow benchmark, to the penny — or a FLAG |

Everything writes only to `data/`. The robot never imports any of it. **Informs, never switches.**

---

## PART 3 — The laws (why it behaves so conservatively)

1. **Paper first, always** — `ALLOW_LIVE_TRADING=0`; even a GO is PLAN_ONLY.
2. **One change per week** — this week was A1 (the fast boots). Next: A2 (the NOW button), then A3 (burst exits).
3. **5-day rules for every verdict** — no judge weight change, no exit-philosophy change, no TF switch on 1–3 days of data.
4. **A5 LOCKED: never loosen POWER thresholds to make it trade.** (M3/A6 is gated behind evidence precisely because it brushes against this law.)
5. **Fix at source, never auto-rescale; user pushes to GitHub; backups never staged; git bash only.**

---

## PART 4 — The scoreboard (the three recorded days + this week's work)

| Measure | Tue 10-07 | Wed 10-08 | Thu 10-09 |
|---|---|---|---|
| Prints / decisions | 103,788 / 913 | 99,173 / 1,121 | 60,716 / 792 |
| Orders to MT5 | **0** | **0** | **0** |
| What-if (trade-everything ghost) | −998.7 | −142.7 | −1,074.5 |
| **Value of saying no** | **+998.7** | **+142.7** | **+1,074.5** → **+2,215.9 total** |
| Editor grade | 10/6/0 | 10/5/1 | 11/4/1 |
| Reconciliation | — | 0.00 ✓ | 0.00 ✓ |

**This week's builds:** A1 installed & twin-proven (`1fc0708`) · test suite 160/160 (`a5558ab`) · the Lens (DST bug found & fixed, October lake forever) · the Anatomist · report_evening v1.2.

**What the new instruments found (3 days, 12 fish):**
- The M5 home clock saw 3/1/1 moments; M3 saw 3/4/5 — and on quiet days **M5's own moments were worth 0.00**.
- The panel SEES the fish (median conf 37/46/59) but **believes below the 50 gate** — belief is the bottleneck; and its highest-confidence pick was a rock.
- Fish are cheap to hold: median heat 0.2–0.9 pts before +1 pt pays; size ladder +1.4→+2.0 by 9–12 min on normal days (the Tuesday river monster: +16.8 in 15 min).
- The exit disease (from the Shadow): TP never fires (0/93), wins +1.14 vs losses −5.10 — **inverted risk/reward**; stops are fine.

---

## PART 5 — THE VERDICT (expert hat, no sugar-coating)

| Subsystem | Grade | One line |
|---|---|---|
| Data & plumbing | **A−** | 100k prints/day, watchdog heartbeat, one source of truth, audit conventions reconciled to the penny |
| Measurement & reporting | **A** | 7 desks, 3-teller reconciliation at 0.00, controls and noise floors, self-diagnosing errors |
| Safety & refusal | **A−** | +2,215.9 saved in 3 days; 0 orders ever; flash-crash switch fired live 3× at zero cost |
| Speed | **B+** | A1 just installed (81% less brain time); the 208s tail's funeral is Monday's TEST 12 |
| Decision layer | **C+** | Right nose (sees fish), wrong belief (conf below gate at fish, 62 on a rock); calibration flagged (C6) |
| Exit layer | **D** | Inverted R/R — the known disease; fix (A3) designed against measured numbers, gated on the 5-day verdict (~Oct 14) |
| **Trading edge** | **UNPROVEN** | By design: no trades, no fills yet; the fill model itself is unvalidated (needs 5–10 paper fills) |

**Bottom line:** as a *trading system*, the robot does not yet meet the standard — it hasn't caught a single fish, and its exit geometry is mathematically sick. As an *instrumented research system*, it is at a professional standard: every claim in this report is checkable, every tool is rigged, every change is twin-proven, and every refusal earned points. **Does it need to improve a lot? The decision and exit layers need the scheduled work (A2 → A3, possibly A6) — but nothing needs to be *rebuilt*; the foundation (data, safety, measurement) is exactly what you want to build an edge on.** Realistic view: 2–4 more weeks of the fish program + paper fills before any "is it profitable" question is even well-posed.

---

## PART 6 — Why you can trust the reports (and where their limits are)

**The trust mechanisms (each one earned):**
1. **The reconciliation contract** — three independent tellers (Accountant, Geometer, Shadow-benchmark) compute the day's number three different ways; agreement within 0.00 pts is required, or the paper prints a FLAG. This caught 2 real bugs in one week before they could lie to you.
2. **MT5 is the truth, not our own records** — the money cross-check (TEST 13) compares against the terminal and *names the gap* when our books disagree (it found a September hole this week — G9).
3. **No look-ahead, by construction** — every replay tool (Shadow, Lens, Anatomist) fires decisions only on closed bars and walks the tape strictly forward.
4. **Rigs for every tool** — 24-check lens rig, 16-check anatomy rig, 23-check A1 rig: every tool proved itself on a fake lake with known fish before touching your data (this discipline caught: a dead ramp, a DST bug, an ADX mismatch, a test-design bug).
5. **The twin-day gate** — robot changes must produce *identical verdicts* to the old brain on a real recorded day, compared against the old brain's own noise floor. A1 passed 36/36 bells at 0 diffs.
6. **Informs, never switches** — report tools cannot touch the robot, `.env`, or MT5. The measurement and the trading are physically separated.
7. **Honest limits printed in the paper itself** — small samples named, warm-up named, WARM-UP ONLY rows shown rather than hidden.

**The honest limits (what the reports canNOT yet tell you):**
- **3–4 days of data.** Every verdict waits for 5 days and the t-test; single days are weather.
- **The what-if is a convention** (fixed SL 2×ATR / TP 3.5×ATR, constant ATR, 0.5 spread) — a ruler for comparing, not a promise of live fills.
- **No live fill evidence yet** — the paper-fill simulator is unvalidated until 5–10 real paper fills happen (calm markets have blocked this).
- **Favorable travel ≠ harvested profit** — the Lens/Anatomist measure what the fish *offered*; harvesting is the exit problem (A3), and the Shadow already showed the current exits capture ~1.14 pts of a move.
- Known cosmetic leaks (wall-clock stamps in some fields) are documented and normalized in comparisons, not hidden.

---

## PART 7 — The week ahead

- **Monday:** A1's first live day (watch: TEST 12, `A1 bar-service` notes, the tail) + the full 7-desk paper for the first time.
- **Mon–Fri:** the fish census grows nightly (Lens + Anatomist); 5-day clocks reach their verdict (~Oct 14): whale_walls, htf_poc, the exit philosophy → A3 design review.
- **Next weekend:** A2 (the NOW button) design review — written against measured numbers (heat budget 1.5–2 pts, 9-min payoff, own-confirmation rule). Then rung 2 (full M3 re-vote) if the engine is ready.

---

## 👦 The whole thing for the 5-year-old owner

The robot is a **fisherman with a camera, a diary, and seven nightly newspaper reporters**. The camera films the lake all day. The fisherman watches and almost always says "not yet" — and this week, saying "not yet" saved 2,215 cookies while the other fishermen (the ghost who trades everything) lost them.

The reporters are honest because **three of them count the same money separately and must agree to the penny**, the camera's film is checked by a puppy, and every new tool must pass a fake-lake exam before touching real film. When something breaks, the newspaper *says so* instead of hiding it — that's why you can sleep well reading it.

But the fisherman hasn't caught a fish yet. His net is the wrong shape (wins 1 cookie, loses 5 — being re-sewn to the measured fish: risk 1½, catch 2–4, nine-minute egg-timer), his teachers see the fish but don't believe them (the red button next week will believe with its OWN eyes), and his old glasses sleep through quiet days (your 3-minute glasses are waiting for the big microscope's verdict).

**Is he a good fisherman? He's a careful one with excellent equipment, honest books, and a repair plan for every measured flaw. The catching starts when the plan finishes — one present per week.** 🎣📓✨
