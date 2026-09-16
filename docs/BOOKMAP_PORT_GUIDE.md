# BOOKMAP PORT GUIDE — Gold-MT5 → Bookmap (Level 3)

> **Purpose of this file:** you (a future AI, or a future us) are asked to rebuild this
> robot so its data source is **Bookmap (Rithmic, full-depth L2 + MBO/L3)** instead of
> **NinjaTrader 8**, with **identical trading behavior**. This document contains
> everything needed: architecture, the data contract, what changes, what must NOT
> change, and the acceptance tests. Read it fully before writing code.

---

## 1. What this system is (30 seconds)

A **gold scalper robot** (max hold ~1 hour by design — respect this everywhere):
- Data: MGC (micro gold futures) live ticks via **NinjaTrader 8 + Rithmic**.
- Brain: ~22 weighted signal votes (order flow, trend, MTF, zones, macro, news) →
  score → AI layer (Gemini) confirm/veto → execution.
- Execution: **MetaTrader 5 terminal** (unchanged by this port).
- Language: Python 3, single folder, no framework, `.env` config with hot-reload.
- Cadence: 60-second cycles, 8-hour rolling analysis window in RAM.

## 2. Current architecture (data flow)

```
NinjaTrader 8 (MGC, Rithmic real-time feed)
  └─ GoldBridgeExporter.cs        (NinjaScript, docs/GoldBridgeExporter.cs)
       writes ticks.csv           (see data contract §3)
            └─ ninja_bridge_provider.py   (tailer: catch-up, 8h window,
                │                          rotation @200MB → gzip data/archive)
                ▼
step1_data_acquisition.py  → step2_market_analysis.py (ALL votes/candles/CVD/VWAP/MTF)
                            → step3_ai_decision.py (Gemini confirm)
                            → step4_mt5_execution.py (MT5 terminal orders)
                            → step5_monitoring.py + position_manager.py (trailing, exits)
Support: config.py (.env), news.py (calendar+blackouts), macro.py, session.py,
         risk_manager.py, trade_guard.py, trade_history.py, maintenance.py,
         tools/backtest.py (replays ticks.csv / data/archive/*.csv.gz)
```

- `docs/GoldFlowSignals.cs` is a **chart-visualization indicator only** (arrows for big
  trades, imbalance panel). NOT part of the data path. Bookmap's own heatmap replaces it.
- `docs/archive/` holds prior art: `gold_depth_reader_v2.py` (early depth experiment),
  `rithmic_code_review.md` (review of a direct-Rithmic attempt — read before choosing
  any path that bypasses Bookmap).

## 3. THE DATA CONTRACT (must not break)

`ticks.csv` — one event per line, CSV, no spaces, `InvariantCulture` decimals:

```
time,event,price,size,level,operation,instrument
2026-09-16T14:30:00.123,Last,4376.4,1,-1,,MGC ##-26
2026-09-16T14:30:00.125,Bid,4376.3,12,-1,,MGC ##-26
2026-09-16T14:30:00.125,Ask,4376.5,7,-1,,MGC ##-26
2026-09-16T14:30:00.127,DepthBid,4375.9,3,4,Update,MGC ##-26
```

| column | meaning |
|---|---|
| `time` | `yyyy-MM-ddTHH:mm:ss.fff` (ms precision) |
| `event` | `Last` (trade) / `Bid` / `Ask` (top-of-book) / `DepthBid` / `DepthAsk` (L2 updates, optional) |
| `price` | decimal point `.` always (never `,`) |
| `size` | volume/contracts at that price |
| `level` | DOM row position for depth events, `-1` for trades/quotes |
| `operation` | `Add`/`Update`/`Remove` for depth, empty otherwise |
| `instrument` | e.g. `MGC ##-26` |

**Rotation conventions (already implemented in Python — reuse, don't rebuild):**
- Chunks are renamed `ticks_YYYYMMDD_HHMMSS.csv` at 200 MB (`NT_ROTATE_MB`),
  gzipped to `data/archive/ticks_*.csv.gz`, fresh `ticks.csv` starts with the header.
- The Bookmap addon should **just append to ticks.csv** and let the existing
  provider/rotation machinery do the rest.
- Unknown `event` values are ignored gracefully downstream — so NEW event types
  (e.g. an `Mbo` stream, see §5) can be added without breaking step2.

## 4. Target architecture (Bookmap)

```
Bookmap (Global plan; Rithmic connection — SAME broker login NT used today)
  └─ our Python addon (Bookmap Python API)
       • subscribe_to_trades → writes `Last` lines (SAME format as §3)
       • top of book from depth snapshot → writes `Bid`/`Ask` lines
       • subscribe_to_depth → writes `DepthBid`/`DepthAsk` lines (now FULL depth)
       • (optional, phase 2) subscribe_to_mbo → separate depth/MBO stream file
            └─ everything from ninja_bridge_provider.py downward: UNCHANGED
```

- If Bookmap **replaces** NT: no login conflict (it takes over the single Rithmic login).
- If Bookmap runs **beside** NT during validation: use R|Trader Pro **plugin mode**
  (broker must enable it; Rithmic allows one platform per login otherwise).
- Bookmap addons also work in **replay mode** → backtests with depth data are possible.

## 5. What improves with Bookmap (use, don't ignore)

1. **Trades carry the aggressor side directly** (`is_bid` flag in the trades handler) —
   today CVD infers buyer/seller by comparing price to bid/ask. Cleaner CVD.
2. **Full-depth L2** (all price levels, not just top of book) → wall/absorption votes.
3. **MBO / L3** (order-by-order add/modify/cancel) → iceberg + spoof detection.
   Suggested: keep the robot's main feed in the §3 format; add depth-derived votes
   as NEW files/signals so the existing pipeline stays intact.

## 6. Hard rules for the port

- **Behavior parity first.** Phase 1 = same ticks.csv format, same votes, same .env
  knob names (`NT_*` names may stay — or alias them, never silently rename).
- **Max hold ~1 hour** — any new depth feature must fit the scalper profile.
- **Delayed data must NEVER reach the robot.** Bookmap's free tier futures data is
  delayed ~10–20 min. Only the broker's Rithmic real-time feed may feed ticks.csv.
- Keep: file rotation + gzip archives, 8h window, news blackouts, risk caps,
  maintenance, log line formats (users and docs depend on them).
- Keep MT5 execution exactly as-is (step4 untouched).

## 7. Acceptance tests (do not skip)

1. **Parallel run ≥ 1 week**: NT and Bookmap both feeding; compare candles, CVD,
   VWAP, and vote scores minute-by-minute. Divergence must be explainable
   (e.g. aggressor-flag CVD vs inferred CVD) and bounded.
2. **Backtest parity**: `PYTHONHASHSEED=0 python tools/backtest.py --file <same day>`
   on NT-fed and Bookmap-fed archives — same cycles/entries (within known
   calendar-fetch nondeterminism).
3. **E2E dry run**: `TRADING_ENABLED=0` for a full trading day; verify decision log,
   news blackout windows, and PM behavior.
4. Only after all three: retire NinjaTrader.

## 8. Bookmap API cheat-sheet (verified 2026-09)

- Docs: `s1.bookmap.com/knowledgebase/docs/API` (Add-ons API = L1 layer).
- Python API: `github.com/BookmapAPI/python-api` (also `jjack33/python-api-bookmap`).
- Key subscriptions: `subscribe_to_depth` (L2), `subscribe_to_mbo` (L3), trades handler.
- Handler signatures (Python API):
  - `on_depth(addon, alias, is_bid, price_level, size_level)` — one changed price level
    per event; a full book snapshot arrives once at subscribe time.
  - trades handler gives `(price_level, size_level, is_bid, ...)` where `is_bid` =
    **aggressor side** (True = aggressive sell into bid, False = aggressive buy into ask —
    verify sign convention against a known tape before trusting it!).
  - Multiply `price_level` by `pips` to get the price; divide `size_level` by
    `size_multiplier` to get contracts.
- Licensing: API access is free for Bookmap users; the **Global plan** is needed for
  Rithmic connectivity and full addon compatibility; real-time futures data comes from
  your Rithmic/broker subscription, not from Bookmap.

## 9. Port checklist (deliverables)

- [ ] `bookmap_addon.py` — Bookmap Python addon writing ticks.csv per §3
- [ ] `bookmap_provider.py` (or verified reuse of `ninja_bridge_provider.py`)
- [ ] Depth stream file format + reader (phase 2, new votes only)
- [ ] Parallel-run comparison tool + 1-week report
- [ ] Updated README + CHANGELOG entries
- [ ] This file updated with what was learned

---

*Written 2026-09-16, v4.4.3 era, by the assistant that built v4.1–v4.4.3 with the user.
Everything referenced here is in this repository — no external state required.*
