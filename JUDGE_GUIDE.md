# Your 25 active judges
### What each one looks at, how it decides, and what only IT can see
Updated 2026-09-25 for build `audit-2026-09-24u`. The 8 judges you retired have been removed.
Taken from the real detector lines in `judge_panel.py` and `step2_market_analysis.py`.

**Removed on your instruction:** `delta_pressure`, `l3_ofi_streak`, `iceberg_legacy`,
`spoof_invert_loose`, `vwap_zscore`, `macro_yield`, `macro_dxy`, `macro_vix`.
They still write their notes so the audit can keep grading them — they simply no longer vote.
Restore any of them with `RETIRED_JUDGES=` in `.env`.

---

## The four taps — where all information comes from

| Tap | What it is | Five-year-old version |
|---|---|---|
| **TAPE** | Every trade that really happened: price, size, and who was impatient | *What people actually BOUGHT* |
| **BOOK** | Every order sitting and waiting, and when it appears or vanishes | *What people SAY they want — promises, not purchases* |
| **MAP** | Candles, averages and volume profiles built from the tape | *The map of where price has been today* |
| **WORLD** | News headlines and the risk mood | *What's happening outside the gold shop* |

TAPE, BOOK and MAP all pour out of your one Bookmap feed. Only WORLD is independent —
and after the retirements it is **1.50 of 17.80 total weight, about 8%**.

---

# TABLE 1 — TAPE · 6 judges · 3.50 weight
*"What people actually bought"*

### `footprint_delta` — 1.00, 15 min ⭐ the core one
- **Looks at:** at every price level, how much was bought by impatient buyers vs sold by impatient sellers.
- **Its sum:** `buys − sells` at each price = the *delta*. Logs `footprint delta +X dominant Y strength Z`.
- **Only it can see:** aggression **at each individual price** — buyers were fierce at 2650 and gave up at 2655.
- **Five-year-old:** in a sweet shop, who grabbed sweets off the shelf without haggling — shelf by shelf.

### `cvd_divergence` — 0.60, 60 min
- **Looks at:** price making a new high **while** cumulative delta does not. Logs `bearish CVD divergence`.
- **Unique — genuinely:** the only judge that compares **price against buying pressure and reports the disagreement**. It votes *against* the move.
- **Five-year-old:** the car is still rolling forward, but nobody is pushing any more.

### `absorption` — 0.60, 15 min
- **Looks at:** heavy selling that does **not** move price down. Logs `absorption net ±X -> BUY`.
- **Unique — genuinely:** the only judge for which **nothing happening is the signal**. Someone huge is quietly eating the push.
- **Five-year-old:** you push the wall as hard as you can and it doesn't move — somebody strong is holding it.

### `cvd_momentum` — 0.50, 30 min
- **Looks at:** the running total of delta all day, and whether it is speeding up or fading. Logs `CVD rising delta X CVD Y`.
- **Unique:** the **direction of travel of the whole day**. Buying can be positive but weakening.
- **Different from the core:** `footprint_delta` is a photo, this is the film.

### `footprint_levels` — 0.40, 15 min
- **Looks at:** how *many* price levels lean buy vs sell. Logs `footprint buying levels 12 > selling 7`.
- **Unique:** **breadth, not size.** One giant buy doesn't impress it; twelve small buys across twelve prices do.

### `volume_roc` — 0.40, 30 min
- **Looks at:** a sudden jump in traded volume plus which way price went. Logs `volume RoC +140% with price up`.
- **Unique:** the **"something just woke up"** alarm. It doesn't care who wins, only that the room got loud.

---

# TABLE 2 — BOOK · 6 judges · 4.70 weight
*"Promises, not purchases"*

### `l3_net_flow` — 1.50, 15 min ⭐ your loudest judge
- **Looks at:** orders being *added and pulled* on each side. Logs `L3 NET FLOW BUY +X (buys A vs sells B)`.
- **Unique:** it sees **intentions appearing and vanishing** — an order placed then cancelled leaves no trade behind, so the tape can never show it.
- **Five-year-old:** who is walking toward the queue, and who is walking away.

### `l3_imbalance` — 0.80, 15 min
- **Looks at:** how much size rests on each side, weighted by closeness to price. Logs `L3 distance-weighted imbalance ±X`.
- **Unique:** the **shape of the wall right now** — a still photograph, where `l3_net_flow` measures change.

### `l3_aggr_limit` — 0.70, 15 min
- **Looks at:** impatient market orders versus patient limit orders. Logs `Aggressive vs limit: buy ratio 1.8`.
- **Unique — genuinely:** the only judge measuring **impatience itself**. Who needs it NOW vs who will wait. Patience is information.

### `l3_large_ofi` — 0.60, 15 min
- **Looks at:** order-flow imbalance counting **only big orders**. Logs `L3 large orders 14 OFI +X`.
- **Unique:** filters out small fry so it hears only the adults.
- **Note:** related to `l3_net_flow`. Now that `l3_ofi_streak` is retired, these two are the only OFI voices left.

### `microprice` — 0.60, 10 min ⏱ fastest clock on the panel
- **Looks at:** the "true" price implied by size on each side vs the plain mid. Logs `microprice X vs mid Y dev +Zbps`.
- **Unique — genuinely:** the **earliest warning that exists**, in fractions of a penny, before price moves at all.
- **Five-year-old:** the seesaw is still level, but more kids are climbing onto one side.

### `queue_pos` — 0.50, 15 min
- **Looks at:** whether *our* order would be near the front or back of the queue. Logs `QUEUE_POS good bid ratio X`.
- **Unique — genuinely:** the only judge thinking about **us, not the market**. "If we join now, do we get filled or are we last in line?"

---

# TABLE 3 — HIDDEN · 4 judges · 3.90 weight
*"Who is sneaking"*

### `whale_walls` — 1.40, 30 min
- **Looks at:** unusually big resting walls. Logs `L3 whale SUPPORT 3 walls 240 lots closest 2043.6`.
- **Unique:** finds the **visible giants** — a floor or ceiling somebody built on purpose.
- **Note:** this judge already knows the wall's exact price. That number is the raw material for the wall-aware SL/TP design.

### `iceberg` — 1.00, 30 min
- **Looks at:** an order that keeps **refilling** after being eaten. Logs `ICEBERG_SUPPORT @ price refills N`.
- **Unique — genuinely:** detects **hidden size** — showing 5 lots, really holding 500. Nothing in TAPE or MAP can ever see this.
- **Five-year-old:** the sweet jar that looks nearly empty but refills every time you look away.

### `sweep` — 0.90, 30 min
- **Looks at:** one order eating several price levels at once. Logs `v6.0 SWEEP BULLISH`.
- **Unique — genuinely:** detects **urgency at any cost**. A sweeper deliberately pays a worse price to get filled instantly.

### `spoof_invert` — 0.60, 30 min
- **Looks at:** big orders that appear and vanish without ever trading — fakes. Logs `SPOOF_INVERT fake bids N -> SELL`.
- **Unique — genuinely:** the only judge that **votes the opposite of what it sees**. It reads lies as information.
- **Five-year-old:** a boy shouts "ice cream over there!" so you run — and he takes your seat. The shout tells you what he really wanted.

---

# TABLE 4 — MAP · 7 judges · 4.20 weight
*"Where price is on the map"*

### `vwap_bands` — 0.80, 60 min
- **Looks at:** how many standard deviations from VWAP. Logs `v6.0 VWAP +2σ -> SELL`.
- **Unique:** knows **how far is too far**, and fades the extreme.

### `htf_poc` — 0.70, 120 min 🕐 slowest clock left
- **Looks at:** the H1/H4 Point of Control. Logs `HTF H4 POC X above price ... magnet`.
- **Unique — genuinely:** the only judge with a **memory longer than today**. Everyone else forgets at midnight.

### `vwap_trend` — 0.60, 60 min 🏆 best performer in testing
- **Looks at:** price above or below VWAP. Logs `VWAP trend UP price X vs VWAP Y`.
- **Unique:** the fairest **"are we expensive or cheap today"** line, because it weights by volume.
- **Note:** reads the same line as `vwap_bands` but is **not** a duplicate — one follows the trend, the other fades the stretch. They can honestly vote against each other.

### `supply_demand` — 0.60, 60 min
- **Looks at:** old zones where price previously turned hard. Logs `near supply zone X`.
- **Unique:** **pure memory of past reactions** — "last time we came here, we bounced".

### `poc_day` — 0.50, 60 min
- **Looks at:** today's Point of Control, where the most volume traded. Logs `POC day X price Y -> above`.
- **Unique:** today's **centre of gravity**, the price everyone agreed on most.

### `value_area` — 0.50, 60 min
- **Looks at:** whether price is inside or outside the zone holding 70% of volume (VAH/VAL).
- **Unique:** knows **normal from abnormal**. Outside the value area, the market is doing something it usually doesn't.

### `mtf` — 0.50, 60 min
- **Looks at:** whether M5, M15 and H1 agree. Logs `MTF ...`.
- **Unique — genuinely:** the only judge that judges **other timeframes rather than the market**. A referee, not a player.

---

# TABLE 5 — WORLD · 2 judges · 1.50 weight
*The only outside opinion you have left*

### `news_sentiment` — 1.00, 120 min
- **Looks at:** high-impact headlines and their tone. Logs `HIGH impact sentiment ±X weight Y`.
- **Unique — genuinely:** the only judge that knows **why**. Everything else sees footprints; this reads the newspaper — and it can know about something *before it hits the tape*.

### `macro_risk` — 0.50, 120 min
- **Looks at:** risk-on / risk-off mood across markets. Logs `risk-off`.
- **Unique:** the general **weather of all markets**, the last non-gold input you kept.

---

## The totals after your retirements

```
TAPE    3.50  ┐
BOOK    4.70  ├─ ALL ONE BOOKMAP FEED = 12.10 of 17.80 = 68%
HIDDEN  3.90  ┘
MAP     4.20  ─── built from the same price+volume ─────> 91.6% one feed
WORLD   1.50  ─── the only outside voice ───────────────> 8.4%
```

## Who is genuinely irreplaceable (10 of the 25)

`iceberg` hidden size · `spoof_invert` deliberate lies · `absorption` a push that produced nothing ·
`cvd_divergence` price and pressure disagreeing · `microprice` the move before the move ·
`sweep` urgency paying a worse price · `queue_pos` whether *we* get filled ·
`mtf` whether the timeframes agree · `htf_poc` memory older than today ·
`news_sentiment` the reason WHY.

The other 15 are re-phrasings of these ideas at different speeds and sizes — which is exactly why
the v8 design puts them at **five tables with one voice each**, instead of 25 shouting at once.

## One loose end

`judge_panel.py` also parses `whale_balanced`, `trend_macd` and `sma20`. None of the three has a
weight in the roster. They speak into the log and are never counted. Unresolved — noted in
`TODO_BOOKMAP.md`.
