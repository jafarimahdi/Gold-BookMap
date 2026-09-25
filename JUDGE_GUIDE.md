# Your 33 judges, one by one
### What each one looks at, how it decides, and what only IT can see
Taken from the real detector lines in `judge_panel.py` and `step2_market_analysis.py`, 2026-09-24.
Read with `JUDGE_MAP.md` (the family tree) next to it.

---

## First: the three places ALL information comes from

Every judge, without exception, drinks from one of these four taps:

| Tap | What it is | Five-year-old version |
|---|---|---|
| **TAPE** | Every trade that actually happened: time, price, size, and whether the buyer or the seller was the impatient one | *What people actually BOUGHT* |
| **BOOK** | Every order sitting and waiting, on both sides, and when they appear and vanish | *What people SAY they want to buy — promises, not purchases* |
| **MAP** | Candles, averages and volume profiles built from the tape | *The map of where price has been today* |
| **WORLD** | Bond yields, the dollar, VIX, news headlines | *What's happening outside the gold shop* |

The important thing: **TAPE, BOOK and MAP all come out of your one Bookmap feed.** Only WORLD is
independent. That is the 85% problem from `JUDGE_MAP.md`.

---

# THE TAPE TEAM — "what people actually bought"

### 1. `footprint_delta` — 1.00, 15 min ⭐ the core one
- **Looks at:** at every single price level, how much was bought by impatient buyers vs sold by impatient sellers.
- **Its sum:** `buys − sells` at each price = the *delta*. Logs `footprint delta +X dominant Y strength Z`.
- **Votes BUY when:** delta is positive and one side clearly dominates.
- **Only it can see:** the aggression *at each individual price*, not just overall. It knows buyers were fierce at 2650 but gave up at 2655.
- **Five-year-old:** in a sweet shop, who grabbed sweets off the shelf without haggling — at each shelf separately.

### 2. `delta_pressure` — 0.60, 30 min
- **Looks at:** the same delta, but as a percentage. Logs `Delta Buy% 67 >60% -> BUY`.
- **Votes when:** one side is over 60% for a sustained stretch.
- **Unique:** nothing really. It is `footprint_delta` with a simpler threshold and a slower clock.
- **Twin warning:** ⚠ essentially a slower copy of #1.

### 3. `cvd_momentum` — 0.50, 30 min
- **Looks at:** the *running total* of delta all day (CVD), and whether it is speeding up or fading. Logs `CVD rising delta X CVD Y`.
- **Unique:** it knows the **direction of travel of the whole day**, not this minute. Buying can be positive but *weakening* — only CVD spots that.
- **Different from #1:** #1 is a photo, this is the film.

### 4. `cvd_divergence` — 0.60, 60 min
- **Looks at:** price making a new high **while** CVD does not. Logs `bearish CVD divergence`.
- **Unique — genuinely:** it is the only judge that compares **price against buying pressure and reports the disagreement**. It votes *against* the move. A new high with no buying behind it is a trap.
- **Five-year-old:** the car is still rolling forward, but nobody is pushing any more.

### 5. `absorption` — 0.60, 15 min
- **Looks at:** heavy selling that does **not** move price down. Logs `absorption net ±X -> BUY`.
- **Unique — genuinely:** the only judge that treats **nothing happening as the signal**. Everyone else needs movement; this one gets interested when a big push produces no result, because someone huge is quietly eating it.
- **Five-year-old:** you push the wall as hard as you can and it doesn't move — somebody strong is holding it from the other side.

### 6. `footprint_levels` — 0.40, 15 min
- **Looks at:** how *many* price levels lean buy vs sell. Logs `footprint buying levels 12 > selling 7`.
- **Unique:** **breadth, not size.** One giant buy at one price doesn't impress it; twelve small buys across twelve prices do.
- **Different from #1:** #1 counts kilos, this counts shelves.

### 7. `volume_roc` — 0.40, 30 min
- **Looks at:** a sudden jump in traded volume, plus which way price went. Logs `volume RoC +140% with price up -> bullish`.
- **Unique:** it is the only **"something just woke up"** alarm. It doesn't care who wins, only that the room got loud.

---

# THE BOOK TEAM — "promises, not purchases"

### 8. `l3_net_flow` — 1.50, 15 min ⭐ your loudest judge
- **Looks at:** orders being *added and pulled* on each side over a window. Logs `L3 NET FLOW BUY +X (buys A vs sells B) w C`.
- **Unique:** it sees **intentions appearing and vanishing**, which the tape can never show — an order that is placed and cancelled leaves no trade behind.
- **Five-year-old:** who is walking toward the queue, and who is walking away.

### 9. `l3_ofi_streak` — 1.00, 15 min
- **Looks at:** the same net flow, but **time-weighted and in a run**. Logs `L3 OFI time-weighted recent streak B7/S2`.
- **Unique:** *persistence*. Seven pushes in a row beats one big push.
- **Twin warning:** ⚠ same number as #8, different window.

### 10. `l3_large_ofi` — 0.60, 15 min
- **Looks at:** the same net flow again, **counting only big orders**. Logs `L3 large orders 14 OFI +X`.
- **Unique:** filters out small fry, so it hears only the adults.
- **Twin warning:** ⚠ #8, #9, #10 are one number three ways — **3.10 of weight**.

### 11. `l3_imbalance` — 0.80, 15 min
- **Looks at:** how much size is *resting* on each side, weighted by how close it is to the current price. Logs `L3 distance-weighted imbalance ±X`.
- **Unique:** it measures the **shape of the wall right now**, a still photograph. #8–#10 measure change; this one measures the standing state.

### 12. `l3_aggr_limit` — 0.70, 15 min
- **Looks at:** impatient market orders versus patient limit orders. Logs `Aggressive vs limit: buy ratio 1.8`.
- **Unique — genuinely:** the only judge that measures **impatience itself**. Who needs it NOW versus who is happy to wait. Patience is information.

### 13. `microprice` — 0.60, 10 min ⏱ fastest clock on the panel
- **Looks at:** the "true" price implied by the size on each side, versus the plain mid-price. Logs `microprice X vs mid Y dev +Z bps`.
- **Unique — genuinely:** the **earliest warning that exists**, in fractions of a penny, before price moves at all. It has the shortest clock (10 min) for that reason.
- **Five-year-old:** the seesaw is still level, but you can already see which side has more kids climbing on.

### 14. `queue_pos` — 0.50, 15 min
- **Looks at:** whether *our* order would be near the front or the back of the queue. Logs `QUEUE_POS good bid ratio X`.
- **Unique — genuinely:** the only judge thinking about **us, not the market**. It asks "if we join now, do we get filled or are we last in line?"

---

# THE HIDDEN-SIZE TEAM — "who is sneaking"

### 15. `whale_walls` — 1.40, 30 min
- **Looks at:** unusually big resting walls of orders. Logs `L3 whale SUPPORT 3 walls 240 lots`.
- **Unique:** finds the **visible giants** — a floor or a ceiling somebody has built on purpose.

### 16. `iceberg` — 1.00, 30 min
- **Looks at:** an order that keeps **refilling** after being eaten. Logs `ICEBERG_SUPPORT @ price refills N`.
- **Unique — genuinely:** it detects **hidden size** — someone showing 5 lots but really holding 500, revealed only because it keeps coming back. Nothing in TAPE or MAP can ever see this.
- **Five-year-old:** the sweet jar that looks nearly empty but refills every time you look away — someone big is topping it up from behind.

### 17. `iceberg_legacy` — 0.60, 30 min
- **Looks at:** the same thing, older method. Logs `L3 icebergs N imb ±X`.
- **Unique:** **nothing.** ⚠ This is the old version of #16 and it still votes. 1.60 combined for one idea. My honest opinion: **this is the first thing I would switch off**, or at minimum the clearest candidate for the correlation matrix to confirm.

### 18. `sweep` — 0.90, 30 min
- **Looks at:** one order eating several price levels in one go. Logs `v6.0 SWEEP BULLISH`.
- **Unique — genuinely:** detects **urgency at any cost**. A sweeper deliberately pays a worse price to get filled instantly — that only happens when someone knows something or must act now.

### 19. `spoof_invert` — 0.60, 30 min
- **Looks at:** big orders that appear and vanish without ever trading — fakes. Logs `SPOOF_INVERT fake bids N -> SELL`.
- **Unique — genuinely:** the only judge that **votes the opposite way to what it sees**. Fake buy orders mean someone wants you to think "up" — so it votes down. It reads lies as information.
- **Five-year-old:** a boy shouts "look, ice cream over there!" so you run — and he takes your seat. The shout tells you what he really wanted.

### 20. `spoof_invert_loose` — 0.40, 30 min
- **Same as #19 with a looser threshold.** ⚠ Twin. No unique knowledge.

---

# THE MAP TEAM — "where price is on the map"

### 21. `vwap_trend` — 0.60, 60 min 🏆 your best performer in testing
- **Looks at:** price above or below the volume-weighted average price. Logs `VWAP trend UP price X vs VWAP Y`.
- **Unique:** the single **fairest "are we expensive or cheap today"** line, because it weights by volume.

### 22. `vwap_bands` — 0.80, 60 min
- **Looks at:** how many standard deviations from VWAP. Logs `v6.0 VWAP +2σ -> SELL`.
- **Unique:** it knows **how far is too far**, and fades the extreme. #21 follows the trend; this one bets against the stretch. ⚠ Same line, but they can genuinely vote opposite ways.

### 23. `vwap_zscore` — 0.50, 60 min
- **Same distance, expressed as a z-score.** ⚠ Twin of #22. Little unique content.

### 24. `poc_day` — 0.50, 60 min
- **Looks at:** today's Point of Control — the price where the most volume traded. Logs `POC day X price Y -> above`.
- **Unique:** knows **today's centre of gravity**, the price everyone agreed on most.

### 25. `htf_poc` — 0.70, 120 min 🕐 slowest clock
- **Looks at:** the same, but on H1/H4. Logs `HTF H4 POC X above price ... magnet`.
- **Unique — genuinely:** the only judge with a **memory longer than today**. It knows a price from hours ago still pulls like a magnet. Everyone else forgets at midnight.

### 26. `value_area` — 0.50, 60 min
- **Looks at:** whether price is inside or outside the zone where 70% of volume traded (VAH/VAL).
- **Unique:** knows the difference between **normal and abnormal**. Outside the value area means the market is doing something it usually doesn't.

### 27. `supply_demand` — 0.60, 60 min
- **Looks at:** old zones where price previously turned hard. Logs `near supply zone X`.
- **Unique:** **pure memory of past reactions** — not volume, not average, just "last time we came here, we bounced".

### 28. `mtf` — 0.50, 60 min
- **Looks at:** whether M5, M15, H1 all agree. Logs `MTF ...`.
- **Unique — genuinely:** it is the only judge that judges **other timeframes rather than the market**. It's a referee, not a player. Its whole job is "do the clocks agree?"

---

# THE WORLD TEAM — the only outside opinion you own

### 29. `news_sentiment` — 1.00, 120 min
- **Looks at:** high-impact headlines and their tone. Logs `HIGH impact sentiment ±X weight Y`.
- **Unique — genuinely:** the only judge that knows **why**. Everything else sees the footprints; this one reads the newspaper. It's also the only one that can know about something *before it hits the tape*.

### 30. `macro_yield` — 0.80, 120 min
- **Looks at:** 10-year bond yields over 5 days. Logs `10Y rising 0.4%/5d`.
- **Unique — genuinely:** gold's true long-run rival. Higher yields = holding gold costs more. **No amount of order-book data contains this.**

### 31. `macro_dxy` — 0.60, 120 min
- **Looks at:** the dollar over 5 days.
- **Unique:** gold is priced in dollars, so a dollar move changes gold's price without anybody trading gold at all.

### 32. `macro_risk` — 0.50, 120 min
- **Looks at:** risk-on / risk-off mood. Logs `risk-off`.
- **Unique:** the general weather of all markets.

### 33. `macro_vix` — 0.40, 120 min
- **Looks at:** the fear index. Logs `VIX stress +12%`.
- **Unique:** knows when people are **frightened**, which sends money into gold regardless of order flow.

---

## Who is genuinely irreplaceable

If you deleted these, **no other judge could ever recover the information**:

| Judge | The thing only it knows |
|---|---|
| `iceberg` | Hidden size that never shows in the book |
| `spoof_invert` | Deliberate lies, read backwards |
| `absorption` | A big push that produced *nothing* |
| `cvd_divergence` | Price and pressure disagreeing |
| `microprice` | The move before the move, in fractions of a penny |
| `sweep` | Urgency that willingly pays a worse price |
| `queue_pos` | Whether *we* would actually get filled |
| `mtf` | Whether the timeframes agree with each other |
| `htf_poc` | Memory older than today |
| `news_sentiment` | The reason WHY |
| `macro_yield` | Gold's rival asset — invisible to every tape judge |

**That is 11 real, separate ideas.** The other 22 judges are mostly re-phrasings, and they carry
**more than half the total weight**. This is exactly the imbalance the correlation matrix will measure.

## Who I would question first

1. `iceberg_legacy` (0.60) — superseded version of `iceberg`, still voting.
2. `spoof_invert_loose` (0.40) — same idea as `spoof_invert`, looser.
3. `vwap_zscore` (0.50) — same distance as `vwap_bands`, different units.
4. `delta_pressure` (0.60) — slower `footprint_delta`.
5. `l3_large_ofi` / `l3_ofi_streak` — keep one, not both, unless the data proves they diverge.

**I am not recommending you delete any of them yet.** That list is my reasoning from reading the code.
The matrix measures it from your own recorded votes — and if the data contradicts me, the data wins.

## One loose end found while writing this

`judge_panel.py` also parses three names that are **not** in your weighted roster: `whale_balanced`,
`trend_macd` and `sma20`. They appear in the logs and get read, but carry no weight in the roster
table. Either they are legacy lines, or three judges are speaking and not being counted.
Worth one check — added to the todo list.
