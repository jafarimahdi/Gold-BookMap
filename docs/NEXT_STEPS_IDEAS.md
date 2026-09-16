# Next Steps — Turning the Data Stream into a Real Robot

*You now have: live gold trades (L1), best bid/ask (L1), and the full order book (L2),
streaming into Python. This file is the menu of what to do with it.*

---

## 1. Is there Level 3 data?

| Level | What it is | Do you have it? |
|---|---|---|
| **L1** | Best bid / best ask / every trade | ✅ yes — `[BID]` `[ASK]` `[TRADE]` |
| **L2** | Order book depth, aggregated **by price** (10 levels) | ✅ yes — `[BOOK]` |
| **L3 / MBO** | Every **individual order**: add / modify / cancel, with order IDs | ❌ not through NinjaTrader |

**L3 (also called MBO — "market by order") would let you** track a specific large order over time, estimate your queue position, and detect spoofing (orders placed and pulled). It exists — CME publishes it — but:

- **NinjaTrader only exposes aggregated L2** to indicators. There is no order-level event in NinjaScript, so our bridge can't carry it. This is a platform limit, not a settings problem.
- Retail-friendly L3 access = **Databento's `mbo` schema** (same CME plan we researched: ~$33/mo + usage, Python SDK, gold = `GC.c.0`). That's the standalone-robot route anyway.
- **Honest advice:** most order-flow analysis works fine on L2 + trades. Graduate to MBO only when you have a specific need — don't pay for it before then.

---

## 2. What the data is good for — signal starter pack

All of these live in `on_event()` in `gold_robot_ntbridge.py`. Start with one or two.

### a) Spread & mid-price (easiest — 5 lines)
```python
if evt["event"] == "Bid": state["best_bid"] = (evt["price"], evt["size"])
if evt["event"] == "Ask": state["best_ask"] = (evt["price"], evt["size"])

if state["best_bid"] and state["best_ask"]:
    spread = state["best_ask"][0] - state["best_bid"][0]
    mid    = (state["best_ask"][0] + state["best_bid"][0]) / 2
    if spread >= 0.50:      # normal on MGC is 0.10 (one tick)
        print(f"[SIGNAL] wide spread {spread:.2f} — liquidity pulling away")
```
*Use: wide spreads signal news/volatility; the mid-price is your fair-value reference.*

### b) Order-book imbalance (the classic L2 signal)
```python
bids, asks = book.top(5)
bvol = sum(s for _, s in bids)
avol = sum(s for _, s in asks)
if bvol + avol > 0:
    ratio = bvol / (bvol + avol)          # 0.5 = balanced
    if ratio > 0.70: print("[SIGNAL] bids stacked — buyers waiting")
    if ratio < 0.30: print("[SIGNAL] asks stacked — sellers waiting")
```
*Use: heavy book on one side often precedes short-term moves in that direction.
(Needs a one-line change so `on_event()` can see the book — I'll wire it when we build this.)*

### c) VWAP — who's in control?
```python
if evt["event"] == "Last":
    state["volume"] += evt["size"]
    state["notional"] = state.get("notional", 0) + evt["price"] * evt["size"]
    vwap = state["notional"] / state["volume"]
    if evt["price"] < vwap - 1.0:
        print(f"[SIGNAL] price {evt['price']:.2f} below VWAP {vwap:.2f} — sellers aggressive")
```
*Use: price above VWAP = buyers in control during the session; below = sellers.*

### d) Trade bursts (momentum ignition detector)
```python
import time
if evt["event"] == "Last":
    now = time.time()
    state["times"] = [t for t in state.get("times", []) if now - t <= 10] + [now]
    if len(state["times"]) >= 30:
        print(f"[SIGNAL] burst: {len(state['times'])} trades in 10 s")
```

### e) Micro-price (better than mid-price)
```python
bp, bs = state["best_bid"]; ap, as_ = state["best_ask"]
micro = (bp * as_ + ap * bs) / (bs + as_)   # weights by opposite side's size
```

---

## 3. Research loop — prove a signal works BEFORE trading it

1. **Archive daily** (with the robot **stopped** — it holds the file open):
   ```bash
   mv /a/gitHub/Rhitmic/ticks.csv /a/gitHub/Rhitmic/ticks_2026-09-14.csv
   ```
   NinjaTrader recreates `ticks.csv` automatically on the next market tick.
2. **Analyze offline** with pandas (you already have Python):
   ```python
   import pandas as pd
   df = pd.read_csv(r"A:\gitHub\Rhitmic\ticks_2026-09-14.csv")
   trades = df[df.event == "Last"]
   print("trades:", len(trades), "| volume:", trades["size"].sum())
   print(trades.groupby(trades.time.str[:13])["size"].sum())   # volume by hour
   ```
3. Ask: *did the signal fire before the move, or after?* Keep only what earns its place.

---

## 4. Roadmap (in order)

| # | Step | Effort | Why |
|---|---|---|---|
| 1 | **Signal pack in the robot** (pick from §2, I build it next) | small | turns a viewer into a robot |
| 2 | **Daily archive + pandas research** (§3) | small | know which signals actually work |
| 3 | **Alerts beyond the terminal** — beep / Telegram message | small | you don't have to stare at the screen |
| 4 | **⚠ Decide data continuity** — see below | decision | the stream dies with the trial |
| 5 | Signals → paper trading → small size | big | the bridge is **read-only**; placing orders needs a NinjaTrader C# strategy or a broker API — a separate project |

## ⚠ 4 is time-sensitive: your data pipeline has an expiry date

The Rithmic trial (~14 days from ~Sep 4) may end within days — when it does, **the stream stops**, even though the robot keeps working perfectly. The options (details in `gold_data_alternatives.md`):

- **Broker route:** ask your broker (Ironbeam/AMP/Optimus…) to enable the Rithmic API for your account (~$100/mo) — this also unblocks your original direct-connection script `gold_depth_reader_v2.py`.
- **Databento route:** ~$35–50/mo, standalone Python robot with no NinjaTrader in the middle — and the only route that offers L3/MBO if you ever want it.
- **Ask Rithmic:** the email draft to rapi@rithmic.com is still waiting to be sent (it asks about trial API access + app authorization).

*Also still pending: change that password that leaked into the logs.*

---

## 5. Data hygiene reminders

- `ticks.csv` grows a few MB/hour with depth on — archive (§3) or delete occasionally; stop the robot first, NinjaTrader doesn't need restarting.
- Keep NinjaTrader + the MGC chart open whenever you want data; the robot can start/stop freely.
- Market clock (Budapest): open Mon 00:00 – Fri 23:00, daily halt 23:00–00:00.
