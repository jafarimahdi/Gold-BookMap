# Strategy Comparison Report — Entry Engine vs. Position Manager

## The final expert report (v4.3.2 · gold scalping system · September 2026)

> Written from the perspective of professional trading-system management practice:
> how institutional robot desks judge the two halves of a trading machine.
> Companion docs: `HOW_IT_TRADES.md` (plain-English story), `MIXED_SYSTEM_OVERVIEW.md`
> (the map), `DECISION_CHAIN.md` (exact gate list), `POSITION_MANAGER.md` (rule reference).

---

## 0. Executive summary

**Question 1, answered plainly: yes — the Position Manager won the comparison.**
It decides on facts instead of forecasts, its logic is deterministic and provably
tested (82 cases), it runs locally and never goes offline, it controls cost and
risk with surgical precision, and it is nearly perfectly matched to a scalping
profile. The Entry Engine is a reasonable, information-rich *proposal generator*
whose final judge (the AI) is its most fragile and least testable component.

**But the two are not rivals — they are the floor and the ceiling of the same
business.** Management decides whether you *survive* (yesterday: −$27 instead of
≈−$75). Entry decides whether surviving was *worth it* (a 60%-win-rate entry with
the same management multiplies expectancy 4×). A professional system needs a
world-class floor first — this one has it — and then upgrades the ceiling. That
is exactly the remaining roadmap.

---

## 1. Scope and the two design notes

**Note 1 — the entry engine's history (L1 era).** The vote committee is a
stratigraphy of data-access eras. The core votes (trend, SMA, MACD, RSI,
pressure, CVD) are **Level-1 tools** — designed when only trades+prices were
available. The **L2 votes** (OFI 0.9, depth imbalance 0.7, microprice 0.5,
absorption 0.5) were built into the engine for a data hierarchy the robot could
not actually obtain (demo = synthetic, Rithmic = permission wall) and only came
**alive** when the NinjaTrader bridge started feeding a real 20+20 level order
book. The **L3 votes** (aggressive-flow ratio, L3 OFI, order-event imbalance)
remain dormant until an MBO feed exists (Databento, roadmap). Consequence: the
committee's strongest flow voice (OFI, 0.9) has only been voting with real data
since 15 Sep 2026 — the entry engine is effectively **in its first days of full
vision**, and its weights have never been tuned against real data.

**Note 2 — the scalping profile.** This robot scalps gold: M1 analysis cycle,
holds of minutes to ~1 hour maximum, 0.01-lot clip, thin per-trade edge, real
spread cost. Everything below is judged through that lens. Short summary in
advance: **the shorter the hold and the thinner the edge, the more of the P&L
the management side controls** — costs, speed and exit geometry dominate.
Scalping is the management engine's home turf.

---

## 2. Part A — The Entry strategy: complete inventory

### A1. Data it consumes

| Data | Source | Status |
|---|---|---|
| M1 candles (OHLCV) | built from the bridge's own ticks | ✅ live since the v4.3.1 window fix (~480 bars) |
| L1 trade prints (price, size, side) | NT bridge, ~150/min | ✅ live (tick-rule classifies aggressor side) |
| **L2 order book** (20 bid + 20 ask levels, live updates) | NT bridge DepthBid/DepthAsk events | ✅ live — the newest real capability |
| Macro trio (DXY, 10Y real yield, VIX) | Yahoo Finance, 15-min cache | ✅ live |
| News headlines + economic calendar | Google News RSS + calendar, 15-min cache | ✅ live |
| L3 order events (adds/cancels/market orders) | requires MBO feed | 💤 dormant |

### A2. The tools — every vote, its weight, and WHY it exists

**Family 1 — Timeframe context (the seniors).** H1 (weight 1.0), M15 (0.8),
M5 (0.6), derived from the robot's own M1 candles. *Why:* a scalp against the
H1 direction is a fight the retail side rarely wins; the seniors exist to make
counter-trend scalps expensive to propose. (Yesterday's three counter-trend
SELLs are precisely what this family is supposed to suppress — it was
half-blind then, voting on flat indicators.)

**Family 2 — Trend (the road).** Trend direction with strength (0.5–1.0),
price vs SMA50 (0.7) and SMA20 (0.5), MACD histogram (0.6), RSI overbought/
oversold (0.5). *Why:* classical, cheap, L1-era measures of slope — they don't
predict, they veto: don't buy into a falling road.

**Family 3 — Order flow, the forces (who is pushing NOW).** OFI — order-flow
imbalance (**0.9, the heaviest single voice**: the book is being eaten faster
on one side), buy−sell pressure (0.8), footprint delta imbalance (0.7), L2
depth imbalance (0.7), CVD sign (0.6), bid/ask ratio (0.6), microprice pull
(0.5), absorption (0.5). *Why:* for a scalper, flow is the fastest truth
available — it leads price by seconds. This family is why the L2 feed matters.

**Family 4 — Levels (WHERE the fight happens).** Round numbers (4325/4340
psychology), supply/demand zones (order blocks), VWAP / POC / value area, and
H1/H4 POC magnets — applied **regime-aware**: VWAP z-score extremes are faded
only in a RANGE regime; in TREND the levels vote with the break. *Why:* levels
decide where a scalp has room and where it dies; they turn "flow up" into
"flow up *at a level that matters*".

**Family 5 — Macro (the opposition rule).** DXY / 10Y / VIX don't vote a
direction; they punish fighting them: opposition shrinks the score up to 50%,
and extreme opposition without flow proof caps the signal to NEUTRAL.
*Why:* gold is priced against the dollar and real yields — the rule encodes
"you may fight the macro, but only with flow evidence."

**Family 6 — News (the clock of risk).** QUIET/WARNING/BLACKOUT states,
headline sentiment, and reduced confidence on HIGH-event days. *Why:* CPI can
move gold 40 points in an hour (it did, yesterday); the state machine keeps
the robot from standing in front of the train.

### A3. The decision pipeline

```
20 weighted votes → score −100..+100 → ±15 threshold → confidence (agreement+strength)
→ MTF confirmation filter (H1/M15/M5 must not all oppose)
→ GEMINI judge: full snapshot + live headlines + macro → own confidence ≥70%
→ 7 context gates (blackout, spread, cooldown, trade count, daily-loss, MT5)
→ ORDER: SL = 1.5×ATR, TP = 3×ATR (1:2), lots = risk ÷ stop
```

*Why the AI judge:* it is the only component that can read a headline
("Fed official hints at pause") and weigh it against the numbers — context
no indicator provides. It is explicitly instructed to ignore stale news and
fall back to technicals. *Why ≥70%:* the trade must convince two independent
brains — the deterministic committee and the contextual judge.

---

## 3. Part B — The Management strategy: complete inventory

### B1. Data it consumes

| Data | Source | Why it's decisive |
|---|---|---|
| **Realized position truth** (entry, SL, TP, live P&L, spread) | MT5 terminal | facts, not forecasts — the ground truth of the trade |
| **Per-ticket memory** (initial SL, best gain ever, BE status) | `pm_state.json`, restart-safe | never forgets the peak; enforces the ratchet |
| **Own 5-minute flow history** (CVD vs price samples) | PM's cycle log | flow *change*, independent of window size |
| Snapshot levels (zones, POC, VWAP z, H1/H4 POC, ATR) | Step 2, rescaled futures→CFD | structure anchors for the trail and the adaptive TP |
| Composite signal + news state | Step 2 | trigger data for flip/momentum exits |
| Clock + spread meter | local | time stop, daily flatten, cost guard |

### B2. The tools — every rule and WHY it exists

**Measurements:** R-multiple earned (risk units, not dollars — works at any
account size), **high-water mark** (best gain ever seen), flow-health
classifier (5 labels: confirms / exhausted / absorbing / against / quiet),
VWAP regime classifier (magnet / trend-anchor / stretched), futures→CFF scale.

**Exit rules — any ONE fires (OR logic):**
- **MOMENTUM_EXIT** (≥0.3R profit + flow against + signal ≥40): momentum
  visibly rolled over → bank it at market. *Why:* on a scalp, giving back a
  winner while waiting for the TP is the most expensive habit there is.
- **FLIP_EXIT** (signal ≥55 + flow confirms): the market changed its mind.
- **DIVERGENCE_EXIT** (CVD diverges before the trade is paid): the move is dying.
- **TIME_STOP** (90 min + <20% progress): dead trade — free margin and mind.
  *Why:* scalps decay; opportunity cost is real.
- **SESSION_FLATTEN** (21:30 UTC): never hold a scalp through a market break/gap.
- **SL/TP hits**: the market itself decides; the birth geometry (1:2) did the math.

**The protection ladder — tighten-only, never loosen:**
- **TRAIL** behind the freshest structure (anchor pool: demand/supply zones,
  POC, H1/H4 POC; floor 1.2×ATR). *Why:* structure is where the market
  actually defends; ATR floor keeps it sane in volatility.
- **BE at +1R** (entry + buffer): the trade becomes free. *Why:* the first
  rule of professional risk — after full risk is earned, risk zero.
- **VWAP_TRAIL** (trend side, z≥0.5) / **VWAP_STRETCH** (z≥2 → lock half):
  VWAP is where intraday mean reversion lives.
- **PROFIT_LOCK** ratchet (gain ≥1×ATR → never give back more than 50% of the
  BEST gain). *Why:* the user's own observed failure — +$8 peaks closing
  negative — is structurally impossible after this.
- **NEWS_PROTECT** (HIGH event ±10 min → tighten winners), **ADOPT_TIGHTEN**
  (rescues positions with oversized legacy stops).
- **TP_UPDATE** (front-run newly formed opposing structure; may widen only
  after BE). *Why:* exit *before* the level, not at it.

**Guards:** spread guard (postpone edits while spread > 0.05% — never donate
a spiked spread), modification cooldown, full journal to `management_log.csv`.

### B3. The invariants (what makes it "logic" rather than "opinion")

1. **Tighten-only** — the SL state is monotonic; it cannot degrade.
2. **OR-exits** — one condition closes; no committee, no latency, no permission.
3. **High-water memory** — persisted, restart-proof, never negotiated down.
4. **Local-only** — zero external dependencies; it ran all of yesterday while
   the AI layer was dark.

---

## 4. Part C — The master comparison table

| # | Dimension | Entry Engine | Position Manager | **Better** | Why |
|---|---|---|---|---|---|
| 1 | Core job | find opportunity | keep/extract value | — | different jobs |
| 2 | Information quality | forecasts (noisy, public) | realized facts (exact) | **PM** | decisions on facts age better |
| 3 | Data independence | needs bridge + Gemini + quotas | 100% local | **PM** | ran all day during yesterday's AI outage |
| 4 | Determinism | heuristic + LLM black box | pure rules | **PM** | same input → same output, always |
| 5 | Testability | weights never tuned; AI not backtestable | 82 tests + live-proven | **PM** | provable logic beats plausible logic |
| 6 | Decision latency | up to ~80 s/cycle + AI call | one cycle, immediate | **PM** | scalps live and die in seconds |
| 7 | Cost control | spread gate at entry only | spread guard on every edit | **PM** | costs are a scalper's main leak |
| 8 | Risk control | defines risk at birth (1.5×ATR, 1:2, sized lot) | rewrites risk downward for the trade's whole life | **PM** | birth geometry vs lifelong protection |
| 9 | Memory | none (fresh eyes each cycle) | high-water mark, persisted | **PM** | never forgets the peak |
| 10 | Bias immunity | LLM can rationalize; weights hand-set | immune by construction | **PM** | rules don't have moods |
| 11 | **Opportunity creation** | the only source of trades | cannot create a trade | **ENTRY** | management without entry earns zero |
| 12 | **Context breadth** | news text, macro, cross-asset, AI reasoning | price/flow/levels only | **ENTRY** | only the AI can read a headline |
| 13 | **Adaptability to regime** | regime-aware votes (fade in RANGE, follow in TREND) | fixed rule set | **ENTRY** | the committee flexes with market type |
| 14 | Scalping fit (≤1 h holds) | 1-min cycle + AI latency; misses fast bursts; 15-min cooldown | near-perfect (fast exits, ratchet, spread guard, flatten) | **PM** | the shorter the trade, the more PM owns P&L |
| 15 | Failure modes | counter-trend proposals; AI outage freezes ALL entries; quota burn | occasional early exit of a winner; can't rescue a bad entry, only cap it | **PM** | PM failures cost basis points; entry failures cost the day |
| 16 | Current maturity (this system) | first days of full vision; weights untuned; never backtested | professional grade, 82 tests, live-proven | **PM** | the gap is real but closable — that's the roadmap |
| 17 | Observability | decisions_log (rich) | management_log (surgical) | tie | both journal well |
| 18 | ATR usage | birth geometry | every distance, every rule | tie | both now run on the same real ATR |

**Score: PM wins 12 · Entry wins 3 · ties 3** (by dimension, not by importance
— dimensions 11–13 are why entry still matters enormously).

---

## 5. Part D — The scalping lens (design note 2)

For a ≤1-hour scalper the priority order of P&L drivers is: **exit geometry ≥
cost control > entry timing > entry selection.** That is why the PM is the
correct place to have invested first, and it shows in the fit:

- **PM is scalping-grade as built.** Fast market exits (momentum 40), profit
  ratchet from 1×ATR (~2.4 pts), spread guard, daily flatten, journal. One
  tuning recommended: **`PM_TIME_STOP_MINUTES` 90 → 60** to match the 1-hour
  maximum hold. Everything else already speaks scalper.
- **Entry engine is scalping-capable but slower by design.** The 1-minute
  cycle + AI round-trip means it catches *setups*, not bursts. The 15-minute
  cooldown and the H1 veto are conservative brakes — appropriate, but they
  mean the robot is a patient scalper, not a sniper. The recommended
  `AI_MIN_SIGNAL_STRENGTH=12` + `AI_MIN_INTERVAL_MINUTES=2` fit this profile.
- **The one structural gap for scalping is L3.** Aggressive-flow and
  order-event votes (0.8-weight voices) are dormant; an MBO feed would light
  up the fastest family of entry evidence a scalper can have. Roadmap item.

---

## 6. Part E — Final expert verdict

**1) The comparison result stands: the Position Manager is the better, more
logical, more robust decision-maker in this system — by design and by
evidence.** It decides on facts, decides instantly, never goes down, never
loosens, never forgets, and was live-proven on the worst day the system has
had (three bad entries → −4% instead of −11%; the same day it converted a
losing SELL into a possible +$4 exit via the adaptive TP).

**2) The professional interpretation of that result.** This mirrors the
industry's own history. Entry "alpha" is research: slow, statistical, and
humbling — most edge claims die in backtest. Management "alpha" is
engineering: achievable, testable, and cumulative. The famous trend systems
(the Turtles) were ordinary entries plus ruthless management; execution and
risk engineering is where trading firms actually spend their money. A robot
desk manager reviewing this codebase would say: *the half that keeps you in
business is finished to a high standard; the half that makes the business
worth being in is a promising, newly-sighted proposal machine that has never
been measured.*

**3) The manager's action list, in priority order:**
1. **Votes/weights tuning** — the entry committee has never been calibrated
   against real full-vision data. Yesterday's counter-trend SELLs are case
   study #1. (Highest value per hour of work.)
2. **Backtest/replay harness** — 1.6M tick lines/day are already recorded;
   make the entry engine measurable. Until entries are backtested, their
   true quality is unknown — including the AI gate's contribution.
3. **Scalping tuning pack** — `PM_TIME_STOP_MINUTES=60`,
   `AI_MIN_SIGNAL_STRENGTH=12`, `AI_MIN_INTERVAL_MINUTES=2` (all .env).
4. **Real-risk floor guard** — refuse entries whose minimum-lot risk exceeds
   ~1.5% of equity (the first SELL yesterday carried 3.8%).
5. **Later, with evidence:** consider demoting the AI from final gate to
   advisor (its outage froze all entries yesterday, and it cannot be
   backtested); and add the MBO feed to light up the L3 votes.

**Bottom line.** *Entries set the ceiling; exits set the floor. This system's
floor is professional-grade, and yesterday proved it holds under fire. The
ceiling — entry quality — is the remaining work, and for the first time it
has the data, the tools, and the evidence loop to be built properly.*
