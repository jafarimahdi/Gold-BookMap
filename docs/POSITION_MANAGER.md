# Position Manager (v4) — the robot no longer "opens and forgets"

**Version 4.4 adds measurable trading** — partial exits (bank half at
+1R, runner rides the ratchet), an adaptive profit lock (RANGE markets
and macro-fighting trades lock earlier and give back less), and trade
memory (every close recorded with its context; recent losses raise the
entry bar).

**Version 4.2 adds the risk perimeter** — protection against the three
things no stop-loss can save you from: scheduled news shocks, the daily
market gap, and spiked spreads at the worst moment.

**Version 4.1 added in-trade intelligence** — footprint imbalance zones,
H1/H4 POC, CVD flow health, VWAP regime handling.
**Version 4 added trade management** — structural stops, break-even,
trailing, early exits. Before v4, SL/TP were blind ATR multiples placed
once at entry and never touched again; Step 5 only *reported* positions.

---

## 1. Where the SL/TP lines now come from (entry time)

`compute_structural_stops()` (in `position_manager.py`, used by Step 4):

| Line | Placement logic | Why |
|---|---|---|
| **SL** | **Beyond** the nearest demand/supply zone (order block), the POC, or a strong round number — plus a `PM_ZONE_BUFFER_ATR` (0.25×ATR) buffer | A stop just inside/at structure is exactly where a liquidity hunt reaches. Behind the zone + buffer, a hunt that reverses leaves you in the trade |
| **SL hunt-guard** | If the stop would land within 0.12×ATR of a strong round (x00/x50), it is pushed behind the round | Gold loves sweeping x00/x50 to fill stops, then reversing |
| **SL clamps** | Distance clamped to [1.2, 3.0]×ATR | **This fixes "SL too far"**: structure half the world away is ignored (ATR fallback), and no stop is ever wider than 3×ATR |
| **TP** | **In front of** the nearest opposing structure (supply/demand zone edge, POC/VAH/VAL, strong round) minus 0.15×ATR | Big traders bank profit INTO liquidity. We exit where they start, not after |
| **TP clamps** | Distance clamped to [1.0, 4.0]×ATR | No tiny targets (spread eats them), no fantasy targets |

All levels come from the **futures feed** (Step 2) and are **scaled to the
CFD trade price** before use — futures 4350 is not CFD 4350 exactly.

Honest note on "order blocks": the classic ICT candle definition is a retail
heuristic. Our zones are swing-based supply/demand (last opposing candle
before a move), and they rank BELOW real resting liquidity (book walls) and
volume nodes (POC) in evidence weight. The combination is the defensible
version of the idea.

## 2. What happens while a position is open (every cycle)

`PositionManager.manage(snapshot)` adopts every bot position (magic 234000 —
**manual trades are never touched**), including positions opened before the
manager existed (restart-safe — your current open position is adopted on the
first cycle after you deploy). Rules, in evaluation order:

| Rule | Fires when | Action |
|---|---|---|
| **FLIP_EXIT** | Composite signal flips against the position with strength ≥ `PM_FLIP_EXIT_SCORE` (55) **and** order flow confirms (delta/pressure against) | Close at market |
| **DIVERGENCE_EXIT** | CVD diverges against the position while it is still early (< 0.3R) | Close at market |
| **TIME_STOP** | Position older than `PM_TIME_STOP_MINUTES` (90) and has travelled < 20% of the TP leg | Close at market ("dead trade") |
| **SESSION_FLATTEN** | Daily at `PM_DAILY_FLATTEN_UTC` (21:30) — before the XAUUSD CFD break; also covers Friday/weekend | Close ALL bot positions (urgent — ignores spread guard) |
| **NEWS_FLATTEN** | (mode=flatten) A HIGH-impact event is ≤ `PM_NEWS_PROTECT_MINUTES` away | Close at market (urgent) |
| **NEWS_PROTECT** | (mode=tighten) HIGH-impact event ≤ 10 min away **and** position is in profit | Lock gains (SL to half the open gain) |
| **ADOPT_TIGHTEN** | First cycle: existing SL is wider than 3×ATR | Tighten to the structural stop |
| **BE** | Profit ≥ `PM_BE_TRIGGER_R` (1.0R) — drops to 0.5R when flow health says the move lacks CVD support | SL to entry + spread buffer |
| **TRAIL** | Fresh structure offers a tighter stop | SL trails behind the newest zone/POC |
| **VWAP_TRAIL** | Trend day (z ≥ 0.5 your side) | SL trails behind VWAP |
| **VWAP_STRETCH** | Price stretched ≥ 2σ beyond VWAP in your favour | Lock half the open gain |
| **PROFIT_LOCK** | Open gain has reached ≥ 1×ATR | Ratchet: SL never gives back more than 50% of the **best** gain — winners close as winners |
| **MOMENTUM_EXIT** | Profitable position + flow health "against" + signal ≥ 40 against | Close AT MARKET immediately — no waiting for the TP or giving profit back to the trail |
| **TP_UPDATE** | New opposing structure formed | TP front-runs it (may only move *further away* once break-even) |
| **PARTIAL_EXIT** (v4.4) | Profit reached +`PM_PARTIAL_TRIGGER_R` (1.0R) on the CURRENT price | Bank half the position at market (`PM_PARTIAL_FRACTION`); the runner keeps the ratchet. Skipped automatically when the position cannot be split (0.01 lots = broker minimum) |

**Adaptive profit lock (v4.4).** The PROFIT_LOCK ratchet adapts to
conditions — in a RANGE market (Step 2's regime call) or when the macro
backdrop (DXY/yields/VIX via the snapshot's `macro_bias`) fights the
position by ≥ 0.3, the ratchet arms at 0.75×ATR instead of 1.0×ATR and
gives back only 40% instead of 50%. In a range, money left on the table
rarely comes back through the door; when fighting macro, take what the
market gives quickly. Both adapters are switches (`PM_REGIME_ADAPTIVE`,
`PM_MACRO_DEFENSE`) and both only ever TIGHTEN — never loosen.

**Trade memory (v4.4).** Every entry and every close (rule exit, SL/TP
hit at the broker — detected via deal history — or manual close) is
recorded in `data/trade_memory.json` with its entry context (AI
confidence, signal strength). The entry side reads it back: a recent
same-direction loss raises the entry bar by +5 points for 30 minutes
(loss memory), and a losing day raises it further (-1% → +5, -2% → +10,
the day ratchet). Fill quality (intended vs actual price) lands in
`data/tca_log.csv`.

**The spread guard** runs under all of it: while the CFD spread exceeds
`PM_ACTION_MAX_SPREAD_PCT` (0.05%), SL/TP edits and non-urgent closes are
postponed to the next cycle — never donate a news-second spread. Urgent
closes (SESSION_FLATTEN, NEWS_FLATTEN) go through regardless.
**Flow memory** persists in `pm_state.json` (`_flow`), so CVD/price
classification needs no warm-up after a restart.

### Hard safety rules
- **The SL can only ever move in your favour. Never widened.**
- One modification per position per `PM_MODIFY_COOLDOWN_SECONDS` (180s) —
  exits are never blocked by the cooldown, only SL/TP edits are.
- Broker minimum stop distance (`trade_stops_level`) respected on every edit.
- Any internal error is contained — management can never break the pipeline.
- Runs only when `PM_ENABLE=1` AND `TRADING_ENABLED=1` AND MT5 is reachable.

## 3. The management journal — `data/management_log.csv`

Every action with its reason, same philosophy as `decisions_log.csv`:

```
timestamp,ticket,side,rule,action,price,profit,r_multiple,old_sl,new_sl,old_tp,new_tp,reason
2026-09-15T12:33:01,1001,BUY,ADOPT_TIGHTEN,MODIFY,4351.0,12.0,0.03,4320.0,4338.6,4380.0,4364.1,structural stop 4338.60 tighter than SL 4320.00 | TP front-runs fresh opposing structure
2026-09-15T13:05:01,1001,BUY,BE,MODIFY,4366.0,160.0,1.42,4338.6,4350.5,4364.1,4364.1,+1.42R reached — stop to break-even
2026-09-15T14:10:01,1001,BUY,FLIP_EXIT,CLOSE,4351.0,-5.0,0.09,4350.5,,4364.1,,signal flipped SELL (61), flow against=True
```

Watch the log vocabulary in the console too:
- `PM: adopted BUY position ticket=...` — first sight of a position
- `PM: ADOPT_TIGHTEN ticket=... SL 4320.00 -> 4338.60 ...`
- `PM: BE ticket=... ` / `PM: TRAIL ticket=...` / `PM: TP_UPDATE`
- `PM: CLOSED ticket=... (FLIP_EXIT: signal flipped SELL (61) ...)`

## 4. Configuration (all in `.env`, hot-reloadable)

| Key | Default | Meaning |
|---|---|---|
| `PM_ENABLE` | 1 | Master switch (0 = old open-and-forget) |
| `PM_BE_TRIGGER_R` | 1.0 | Move to break-even at this multiple of initial risk |
| `PM_TRAIL_ENABLE` | 1 | Trail behind fresh structure |
| `PM_FLIP_EXIT_SCORE` | 55 | Signal strength against us that triggers early exit |
| `PM_FLIP_REQUIRE_FLOW` | 1 | Flip exit needs order-flow confirmation |
| `PM_DIVERGENCE_EXIT` | 1 | Exit on opposing CVD divergence early in the trade |
| `PM_TIME_STOP_MINUTES` | 90 | Max age of a going-nowhere trade (0 = off) |
| `PM_TIME_STOP_MIN_PROGRESS` | 0.2 | ...unless it travelled 20%+ of the TP leg |
| `PM_MIN_SL_ATR` / `PM_MAX_SL_ATR` | 1.2 / 3.0 | SL distance clamps (×ATR) |
| `PM_ZONE_BUFFER_ATR` | 0.25 | SL buffer beyond a zone |
| `PM_TP_BUFFER_ATR` | 0.15 | TP gap in front of opposing structure |
| `PM_ROUND_HUNT_BUFFER_ATR` | 0.12 | Clearance from x00/x50 rounds |
| `PM_TP_MIN_ATR` / `PM_TP_MAX_ATR` | 1.0 / 4.0 | TP distance clamps (×ATR) |
| `PM_MODIFY_COOLDOWN_SECONDS` | 180 | Min seconds between SL/TP edits |
| `PM_NEWS_PROTECT_ENABLE` | 1 | Master switch for news protection |
| `PM_NEWS_PROTECT_MINUTES` | 10 | HIGH event within N minutes → protect |
| `PM_NEWS_PROTECT_MODE` | tighten | tighten (lock gains) or flatten (close all) |
| `PM_DAILY_FLATTEN_UTC` | 21:30 | Close everything before the CFD break (empty = off) |
| `PM_ACTION_MAX_SPREAD_PCT` | 0.05 | Postpone non-urgent actions above this spread |
| `PM_PROFIT_LOCK_MIN_ATR` | 1.0 | Gain (in ATRs) that arms the profit ratchet |
| `PM_PROFIT_GIVEBACK` | 0.5 | Max fraction of the best gain the SL may give back |
| `PM_MOMENTUM_MIN_R` | 0.3 | R earned (at best) that arms the momentum exit |
| `PM_MOMENTUM_SIGNAL` | 40 | Signal strength against us that triggers it |

## 5. Verified behaviour (2026-09-15 sandbox tests, 52/52)

**v4 core (36 tests):**
- SL behind demand bottom 4340 → 4338.75; TP front-runs supply 4365 → 4364.25
- No structure → old 1.5×/3.0×ATR behaviour preserved
- Structure too far → ignored (not a 3×ATR-wide stop)
- SL near round 4350 → pushed behind the hunt (4349.40)
- SELL mirror: SL above supply **top** edge, TP front-runs demand **near** edge
- Futures→CFD scaling correct (1% dislocation test)
- Far-SL adoption tightened 4320 → 4338.6 on first cycle
- BE at +1R; trail subsumption sets the risk-free flag correctly
- Flip exit fires with flow, blocked without it
- Time stop fires even inside the modify cooldown
- Cooldown blocks double-modifies; SL never widens; foreign magic untouched

**v4.1 in-trade intelligence (16 tests):**
- Footprint: a 3:1 one-sided volume run becomes a zone (SL behind bull
  cluster at 4341, TP front-runs bear cluster at 4366); balanced noise
  is ignored
- HTF POC: H4 POC behind → SL anchor; H1 POC ahead → TP target; verified
  against a synthetic 8h session (session 4342.7 / H4 4345.6 / H1 4355.2)
- VWAP_TRAIL on trend days (z ≥ 0.5): SL trails behind VWAP − 0.25×ATR
- VWAP_STRETCH (z ≥ 2.0): SL locks half the open gain
- RANGE days: VWAP joins the TP magnet pool
- Flow health: price up + CVD down → break-even fires at 0.5R with the
  "aggressors exhausted" reason; healthy flow keeps the normal 1.0R trigger

## 6. v4.1 — the five tools during the trade (design notes)

| Tool | Role while a position is open |
|---|---|
| **Order blocks** | Fresh zones every cycle → TRAIL anchors and TP targets |
| **Footprint** | Stacked-imbalance clusters (3:1 over ≥3 prices, real volume) join the zone pool — the freshest support/resistance there is |
| **POC (H1/H4)** | The "bigger candle" magnets: H4/H1 POC behind you = institutional floor; ahead = target. Step 2 attaches them each cycle |
| **CVD** | Flow health: the manager watches whether CVD agrees with the price move over the last ~5 minutes. Disagreement = "aggressors exhausted" → break-even protects early. Divergence against a young trade → exit. The classification and its "next step guess" is logged every cycle |
| **VWAP** | Regime read: trend side (z ≥ 0.5) → trail behind VWAP (institutions defend their average); stretched (z ≥ 2.0) → lock half the gain; range day → VWAP becomes the TP magnet |

Honest limits: the flow/VWAP reads **never open a new trade and never widen
a stop** — they only tighten, protect, and exit. Predictive labels
("expect a stall/reversal") are logged for your review in the journal, not
acted on beyond protection. The CVD history needs ~3 cycles (~3 minutes)
after a restart to warm up.
