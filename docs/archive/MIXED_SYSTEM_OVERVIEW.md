# Mixed System Overview
## NinjaTrader data + Gold-MT5 brain + AI decisions + MT5 execution

> **STATUS: Phase 1 BUILT & TESTED** — `ninja_bridge_provider.py` is written and wired in;
> the full pipeline ran end-to-end on bridge data (STEP 1 OK → STEP 2 OK → gates → STEP 5 OK);
> the project's own test suite passes 31/31. See `Gold-MT5/README_MIXED.md` for setup.

*Assessment of combining the NinjaTrader bridge (working) with the Gold-MT5 project
(github.com/jafarimahdi/Gold-MT5, copied to workspace folder `Gold-MT5/`).*

---

## 1. Verdict: YES — and it's cleaner than expected ✅

Your Gold-MT5 project was built with a **pluggable data layer** (Step 1: demo / Rithmic / Databento
providers) and its internal data schema (`tick_data`, `bid_depth`/`ask_depth` as `{price: size}`)
matches almost 1:1 what the NinjaTrader bridge produces. The Rithmic provider in your repo is the
exact code path that hit the rpCode 13 permission wall — the NinjaTrader bridge simply becomes its
replacement, as a new provider.

**What's missing is ONE new file** (~250 lines, mostly adapting already-tested bridge code):
`ninja_bridge_provider.py`.

---

## 2. What each side already brings to the mix

| Component | From | Status |
|---|---|---|
| Live CME gold data, L1 trades/quotes + L2 depth book | NT bridge (GoldBridgeExporter + robot engine) | ✅ running live, battle-tested |
| 25+ signal analysis: order flow, CVD, footprint, **L2 depth imbalance, microprice, liquidity walls**, VWAP, trend, volatility, volume profile | Gold-MT5 `step2_market_analysis.py` | ✅ built |
| **AI decision** (Gemini: BUY/SELL/HOLD + confidence, multi-key rotation) | Gold-MT5 `step3_ai_decision.py` | ✅ built |
| MT5 execution: risk-based lot sizing, SL/TP, news blackout/warning | Gold-MT5 `step4_mt5_execution.py` | ✅ built |
| Monitoring loop, risk manager, trade guard, session control, spread monitor | Gold-MT5 step5 + safety modules | ✅ built |
| Glue: NT bridge → Step 2 schema | **NEW — to build** | 🔨 `ninja_bridge_provider.py` |

---

## 3. Architecture of the mixed system

```
NinjaTrader 8 (MGC/GC chart + GoldBridgeExporter)
        │  writes
        ▼
A:\gitHub\Rhitmic\ticks.csv        (CME gold: trades, quotes, L2 depth)
        │  tailed by
        ▼
ninja_bridge_provider.py   ← the ONE new piece (a Step-1 data provider)
        │  market_data dict: tick_data + bid_depth/ask_depth
        ▼
STEP 2  SignalEngine        → 25+ signals, composite score, MarketSnapshot
        ▼
STEP 3  Gemini AI           → BUY / SELL / HOLD + confidence %
        ▼
STEP 4  MT5 execution       → XAUUSD order on your MT5 account
        ▼
STEP 5  Monitoring loop     → repeat every 60 s
```

**Event mapping (NT bridge → Gold-MT5 schema):**

| Bridge event | Gold-MT5 input |
|---|---|
| `Last` (trade) | `handle_trade(price, volume, side)` — side via tick rule (≥ ask = BUY, ≤ bid = SELL) |
| `Bid` / `Ask` (L1) | best bid/ask → side classification, microprice |
| `DepthBid` / `DepthAsk` (price-keyed book) | `bid_depth` / `ask_depth` `{price: size}` → `handle_depth()` |
| — (no L3 from NT) | `order_events` empty → Level-3 analyzer dormant |
| ISO-ms timestamps | their datetime schema |

**Config changes (in `.env`):**
```
DATA_SOURCE=ninjabridge
NT_BRIDGE_FILE=A:\gitHub\Rhitmic\ticks.csv
DATA_SYMBOL=MGC 12-26        # the chart the exporter runs on
MT5_SYMBOL=XAUUSD            # where it trades
```

---

## 4. Lists — keep / add / drop

### ✅ KEEP from Gold-MT5 (the core loop)
`main.py`, `config.py`, `step1`–`step5`, `data_providers.py` (Base + Demo providers),
`risk_manager.py`, `trade_guard.py`, `session.py`, `spread_monitor.py`, `news.py`,
`markets.py`, `mt5_signal_bridge.py` *(only if you choose the EA path — see below)*

### 🔨 ADD (new build)
- `ninja_bridge_provider.py` — the glue provider
- Config keys above + a **staleness gate**: no fresh NT data → force HOLD (protects against
  NT closed / market halt / chart closed)

### 💤 KEEP but DORMANT / OPTIONAL
- `DatabentoProvider` — the future standalone route (also the only L3/MBO source someday)
- `DemoProvider` — offline pipeline testing without market data
- MacroAnalyzer (step 2) — needs external DXY/yields/VIX feeds; disable initially
- `mt5_ea/` (EA + indicator) + `mt5_signal_bridge.py` — **alternative** execution path.
  ⚠ Pick ONE execution path: Python SDK (step 4, recommended — integrated sizing & news logic)
  **or** the EA (visual, manual lot control). Running both = two robots fighting over one account.

### ❌ NOT NEEDED in the mixed robot
| File | Why drop |
|---|---|
| `rithmic_bridge.py`, `rithmic_test.py`, RithmicProvider | Dead — rpCode 13 permission wall (the reason the NT bridge exists) |
| Level3OrderBookAnalyzer (step 2 section) | No L3 from NinjaTrader — runs empty (verify it degrades silently) |
| `demo_step2.py`, `demo_order_test.py`, `e2e_test.py` | dev artifacts |
| `test_*.py`, `run_all_tests.py` | keep in repo, NOT in the runtime loop |
| `history_download.py`, `deduplicate_trade_outcomes.py`, `broker_diagnostic.py`, `maintenance.py`, `review.py`, `robot_report.py` | offline tooling — run manually, not part of the robot |
| `start_test.bat`, `start_report.bat` (keep `start_bot.bat`) | consolidate |
| 12 documentation files (START_HERE, AI_HANDOFF, CHANGES, TOMORROW_STEPS, …) | history — keep one short README for the mixed system |

### From the current NT-bridge app
| Piece | Role in the mixed system |
|---|---|
| `GoldBridgeExporter.cs` | **THE data source — core** |
| `gold_robot_ntbridge.py` | Its tail/book engine becomes the provider's heart; standalone terminal stays as a monitor window |
| `GoldFlowSignals.cs` | Optional — manual chart viewing, not part of the robot loop |
| `gold_depth_reader_v2.py` | Dormant (direct Rithmic, blocked) |
| `NEXT_STEPS_IDEAS.md` signals | Superseded by your much richer SignalEngine |

---

## 5. Honest constraints — read before building

1. **Demo account first, mandatory.** Run the whole mixed system on an MT5 **demo** account for
   at least 1–2 weeks. Your repo even ships `paper_trade_checklist.md` — use it.
2. **This is a swing robot, not a scalper.** Chain latency: file bridge (ms) + 60 s poll +
   Gemini response (seconds). Signals from 1-minute-scale order flow are the right target.
3. **Data basis:** signals come from CME gold futures (MGC/GC), execution is XAUUSD CFD.
   They track each other closely but not identically — SL/TP are set on XAUUSD prices (step 4
   already does this correctly).
4. **Market hours:** CME halts 23:00–00:00 Budapest + weekends → robot must HOLD on stale data
   (the staleness gate handles this). MT5 broker hours differ slightly.
5. **One PC runs everything:** NinjaTrader + MT5 terminal + Python bot. The chart with the
   exporter must stay open.
6. **AI ≠ profits.** Gemini judges your signals; it is not a magic edge. The edge must come from
   the order-flow signals — validate on demo before risking money.
7. **Gemini API keys** cost/quota — your multi-key rotation already handles outages (falls back to HOLD).

---

## 6. Build plan

| Phase | What | Who |
|---|---|---|
| 1 | Build `ninja_bridge_provider.py`, wire config, staleness gate | me, in the workspace `Gold-MT5/` copy |
| 2 | Dry run: full pipeline, signals logged, **no orders** (MT5 demo, AutoTrading off) | you run it |
| 3 | Demo trading with minimum lots (0.01) | you + me watching outputs |
| 4 | Review results (`review.py`), tune thresholds, then decide about real money | together |
