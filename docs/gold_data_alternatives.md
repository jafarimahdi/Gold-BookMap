# Getting live Gold data into your Python robot — all routes compared

**Situation (2026-09-04):** Your `gold_depth_reader_v2.py` is proven correct end-to-end — DNS, TLS, gateway handshake and system-name all pass. The only blocker is Rithmic's login policy: your `app_name` is not authorized, and free 14-day trial accounts appear not to include API access. Below are **all** realistic routes to live gold data, ranked by practicality for you.

> Key insight: NinjaTrader works with the same username/password because **Rithmic authenticates the software, not just the user**. Any route that avoids Rithmic's conformance process works by *using someone else's already-authorized software* (NinjaTrader) or by *buying data from a different vendor* (Databento, IQFeed, IBKR, …).

---

## Comparison table

| # | Route | Monthly cost | Data | Permission/gatekeeping | Effort | Verdict for you |
|---|---|---|---|---|---|---|
| 1 | **Ask Rithmic by email** (the official door) | Free to ask; historically $99.99/mo for API use + exchange fees | Official COMEX L1/L2/L3 via R|Protocol | Conformance process; may not be available on free trial | One email, then wait | **Do this regardless — it's free** |
| 2 | **NinjaTrader bridge** (export ticks from NT to Python) | $0 (while trial lasts) | Real COMEX futures, whatever NT shows (L1 + DOM depth) | None — NT is already authorized | Medium: small C# NinjaScript + Python reader | **Best free option today** — you already have the data flowing in NT |
| 3 | **Databento** | ~$33–50 (see below) | Official CME/COMEX direct from colocation: trades, top-of-book, MBP-10, **full MBO (the "L3" you originally wanted)** | **None** — self-serve signup, credit card, done in minutes | Low: `pip install databento` | **Best standalone option for a real robot** |
| 4 | IQFeed (DTN) | ~$170–190 all-in with L2 | Solid retail feed, L1 + 5–10 levels of depth | None beyond signup | Medium (Windows client app must run; `pyiqfeed`) | Overkill for one symbol — great breadth, wrong price for GC-only |
| 5 | Interactive Brokers | ~$10–15 data sub, but needs a funded brokerage account | Futures L1 (+ some depth) | Brokerage onboarding (KYC, deposit) | Medium (`ib_async` library) | Only if you want a broker anyway; paper account = delayed data |
| 6 | MetaTrader 5 demo | $0 | **XAUUSD spot/CFD gold** — not COMEX futures; bid/ask tick stream | None (any MT5 broker demo) | Low: `pip install MetaTrader5` (Windows) | Free & easy if "gold price" matters more than "COMEX futures specifically" |
| 7 | yfinance (`GC=F`) | $0 | Delayed-ish top-of-book, unofficial | None | Trivial | Toy/learning only — not for a real robot |

---

## Route 1 — The Rithmic email (do this today, it's free)

Send to **rapi@rithmic.com** (draft was provided earlier in chat). Possible outcomes:
1. They authorize your `app_name` (possibly with a 4-char prefix) → set `RITHMIC_APP_NAME` to exactly what they give you, re-run `gold_depth_reader_v2.py`, done.
2. They say API requires a paid subscription (historically $99.99/mo + exchange fees) → compare with Route 3 below.
3. They issue free **Rithmic Test** credentials → you can validate the whole script on `rituz00100.rithmic.com:443` (system "Rithmic Test") — but the Test system has **no live market data**.

**Cost note:** at ~$99.99/mo, Rithmic API access is roughly 2–3× the cost of Databento for a single-symbol robot. Rithmic's advantage is order routing (trading) through the same connection — irrelevant if you only want data.

**Route 1b — the broker path (alternative official route):** Rithmic-connected brokers (Ironbeam, AMP, Optimus, Dorman, …) can enable R|Protocol API access for their clients — several market it explicitly ("request the API through your broker", ~$100/mo historically). If you plan to trade futures for real eventually, opening an account with such a broker and asking them to enable API access is often faster and easier than the cold email to Rithmic, because the broker handles the relationship.

## Route 2 — NinjaTrader bridge (free, works today, your data is already there)

NinjaTrader is already receiving exactly the data you want, through its authorized connection. Export it:

**How it works:**
1. Write a small **C# NinjaScript** indicator (NinjaTrader plugins are C#-only — Python is not supported inside NT) that subscribes to market data events and writes each tick/quote to a CSV file (or pushes over a local socket).
2. Your Python robot tails that file (or listens on the socket) and processes the data.

NinjaTrader's own support confirms this pattern and publishes a reference sample — *"Using StreamWriter to write to a text file"* — for exactly this use case; community members commonly use the TCP-socket variant.

**Sketch (C# side):**
```csharp
// In a NinjaScript indicator: OnMarketData() fires for every trade/quote
protected override void OnMarketData(MarketDataEventArgs e)
{
    if (e.MarketDataType == MarketDataType.Last)
        System.IO.File.AppendAllText(@"C:\ticks\gc.csv",
            $"{e.Time},{e.Price},{e.Volume}\n");
}
```
**Sketch (Python side):** tail the CSV, parse lines, feed the robot.

**Caveats:**
- NinjaTrader must stay running while the robot runs.
- Your trial (and its data) ends 14 days after signup — after that you need a continuing Rithmic source (paid) or switch to Route 3.
- The feed is licensed for display in NT; piping it into a personal research script is generally tolerated for personal use, but it's not a data license.
- You already wrote the hard part (the Python robot) — this just replaces the "get data" layer.

## Route 3 — Databento (recommended for the standalone robot)

Self-serve, no conformance, no sales calls — this is what the modern algo community actually uses.

- **Live CME/COMEX data, official source**, distributed directly from the Aurora colocation; nanosecond timestamps; no instrument limits.
- **Pricing:** CME non-professional license **$32.65/month** (pass-through of the exchange fee, no markup) **+ usage-based fees** for what you actually stream. For context: streaming tick data for *all* ES and CL outrights was quoted at ~$15.78/month on top of the license — a single symbol like GC costs less. Realistic all-in for a GC robot: **~$35–50/month**. New accounts get free usage credits to start.
- **Schemas:** `trades`, `mbp-1` (top of book), `mbp-10` (10 levels of depth = your L2), and **`mbo` (market-by-order — the full order-by-order "Level 3" your original script's optional section was aiming at)**.
- **Python SDK is first-class:**

```python
import databento as db

client = db.Live(dataset="GLBX.MDP3")  # CME Globex
client.subscribe(
    dataset="GLBX.MDP3",
    schema="mbp-10",           # or "mbo" for full order-by-order
    symbols=["GC.c.0"],        # front-month gold continuous
)
for record in client:
    print(record)
```

- Sign up at databento.com → get an API key → `pip install databento` → stream. Minutes, not weeks.
- Bonus for later: their historical data (same API) is ideal for backtesting the robot.

## Route 4 — IQFeed (solid, but pricey for one symbol)

Layered pricing (2025–2026): Core service ~$108/mo + real-time futures entitlement ~$25/mo + market-depth service ~$24/mo + CME exchange fees (non-pro: ~$2/exchange L1 or ~$13/exchange L2, ~$34 for the 4-exchange L2 bundle) → **~$170–190/month** all-in for gold with depth. Requires their Windows client app running and the `pyiqfeed` Python wrapper. Excellent breadth (thousands of symbols) — but you're paying for breadth you don't need. Choose this only if the robot will grow to many markets.

## Route 5 — Interactive Brokers

If you'll open a brokerage account anyway: IBKR + the `ib_async` Python library streams futures data cheaply (non-pro CME subscription on the order of $10–15/month). A **paper** account alone gets **delayed** data — real-time needs a funded account with subscriptions. Onboarding (KYC, deposit) takes days. Good "later" option when the robot starts trading.

## Route 6 — MetaTrader 5 (free, but it's not COMEX gold)

Any MT5 broker demo account + the official `MetaTrader5` Python package (Windows) streams **XAUUSD** (spot/CFD gold) bid/ask ticks for free, 24/5. Prices track COMEX futures closely but are not the same instrument (no exchange central limit order book; depth = that broker's book). Perfect for learning and prototyping the robot's logic at zero cost; swap in Databento/COMEX later by changing one data-source module.

## Route 7 — yfinance (`GC=F`)

`pip install yfinance`, poll `GC=F` every few seconds. Free, delayed, top-of-book only, unofficial (can break). Fine for a weekend experiment, not for a robot.

---

## Does switching the programming language help? (R, C++, Rust, …) — NO

This is the most important thing to understand about the "permission denied":

**The authorization check happens on Rithmic's SERVER, not in your code.** Every client — Python, R, C++, Rust, Java, whatever — sends the exact same login message over the same WebSocket, containing the same `app_name` field. Rithmic's server reads `app_name` and rejects it. The language you write the client in only changes what you type *before* the "permission denied" arrives.

Evidence:
- R|Protocol is explicitly "language-agnostic, any language, any OS" (it's just Protocol Buffers over WebSocket) — the permission policy applies to all of them equally.
- Existing clients in other languages face the identical wall: `rithmic-rs` (Rust), the new NautilusTrader Rithmic adapter (Rust/Python), etc. The NautilusTrader RFC notes plainly that Rithmic access "depends on vendor credentials, account access, and environment availability."
- There is **no R package for Rithmic at all** — and if one existed, it would hit the same server-side denial.
- Switching Rithmic API flavor doesn't help either: R|API+ (C++/.NET) and R|Diamond have the **same conformance requirement** as R|Protocol.

The only "technical" bypass would be impersonating an approved application's `app_name` — do not do this: it violates Rithmic's terms, gets accounts terminated, and is data-license fraud. Not a path.

### What about R specifically?

If **R** is the language you know best, these routes work well from R:
- **NinjaTrader bridge (Route 2):** the C# exporter writes CSV; R reads CSV natively (`readr`/`data.table::fread`, or `tail`-style streaming). Your robot brain can be 100% R.
- **Interactive Brokers:** the `IBrokers` R package is the classic R API for streaming market data (needs an IBKR account + data subscription).
- **Databento:** no official R SDK, but R can call the Python SDK via `reticulate`, or you can consume their raw TCP/JSON feed.
- Honest note: for market-data robots, Python's ecosystem (async, websockets, pandas) is stronger, and you've already built the Python side — staying with Python is the path of least resistance.

### Checked and ruled out (so you don't waste time)

- **Sierra Chart as a DTC relay for Rithmic data:** Sierra Chart froze development of its Rithmic integration years ago and openly calls it disfavored/deprecated; Rithmic accounts on Sierra get **Level 1 only (no depth)**; and Sierra's DTC server explicitly **restricts external market-data access** ("you cannot… access market data outside of Sierra Chart… restricted based on exchange rules"). Dead end.
- **Trading Technologies (TT):** free demos exist via brokers (Discount Trading, Optimus, ITG) with live CME data on their sim (~$5/exchange/mo non-pro when live). BUT their SDKs are C++/C# (no R, no Python-first story) and real access goes through a broker relationship. Viable only if you adopt the TT ecosystem wholesale — for a data robot, Databento is simpler and cheaper.

## Recommended plan for you

1. **Today:** send the Rithmic email (free; might unlock the trial or fast-track conformance). If you ever plan to trade for real, also consider the broker route (1b).
2. **This week (free):** build the NinjaTrader → CSV/socket bridge and finish your robot's logic against real trial data while it lasts — the reading side can be **Python or R**, whichever you prefer.
3. **For the real standalone robot:** Databento with `GC.c.0` — `mbp-10` for L2 or `mbo` for full order-level detail — ~$35–50/month, live in an afternoon.
4. Keep `gold_depth_reader_v2.py` — if Rithmic authorizes your app, it works as-is.

## Sources

- NinjaTrader: NinjaScript is C#-only, Python not supported — NinjaTrader support forum: https://forum.ninjatrader.com/forum/ninjatrader-8/indicator-development/1244059-not-sure-what-url-to-place-in-python and https://forum.ninjatrader.com/forum/ninjatrader-8/strategy-development/1231378-python-strategy
- NinjaTrader StreamWriter-to-file bridge (official support answer + sample): https://forum.ninjatrader.com/forum/ninjatrader-8/add-on-development/1315068-passing-price-to-python
- Databento live CME pricing ($32.65/mo non-pro pass-through, usage-based, Python/C++ SDKs): https://roadmap.databento.com/announcements/live-cme-data-is-now-open-to-all-users-starting-at-3265month and https://www.elitetrader.com/et/threads/databento-real-time-cme-data-now-open-to-all-users-starting-at-32-65-month.374719/
- Databento for futures traders (pricing detail, MBO, credits, comparison table): https://nexusfi.com/a/data/databento-futures-market-data
- Community view — Databento MBO/L3 best-in-class, Rithmic API docs criticized: https://www.reddit.com/r/algotrading/comments/1gnatd7/best_api_data_feed_for_futures/
- IQFeed pricing: https://dtn.com/financial-analytics/active-trading/dtn-iqfeed/fees and non-pro CME Globex exchange fees: https://iqhelp.dtn.com/how-much-does-the-non-professional-cme-globex-data-cost/ and https://nexusfi.com/a/data/dtn-iqfeed-setup-configuration-guide
- Rithmic context (conformance, gateways, trial): see `rithmic_code_review.md` in this workspace
