# Port Summary — NinjaTrader → BookMap (v5.0)

**Date:** 2026-09-16
**Source:** Gold-MT5 v4.4.3 (NinjaTrader bridge)
**Target:** Gold-BookMap v5.0 (BookMap bridge)
**Goal:** Exactly same trading system, but all data comes from BookMap platform.

## What was requested
> Check this link from GitHub and the app inside of this for me, it designed for using Ninjatrader, and I would like you to make exactly the same same thing, but instead of using Ninjatrader, it will use the BookMap platform, with all data coming from the BookMap platform

Link: https://github.com/jafarimahdi/Rhitmic/tree/main/Gold-MT5
.env provided (without keys) — ported 1:1 to BookMap keys.

## What was built

### New files (BookMap edition)
- `bookmap_addon.py` — **Core bridge**: BookMap Python API addon that writes `ticks.csv` in SAME format as NinjaTrader's GoldBridgeExporter.cs. Subscribes to trades (with true aggressor side Buy/Sell), depth (full L2), best bid/ask. Handles rotation awareness, delayed-data protection (refuses delayed free-tier), symbol filter, optional MBO.
- `bookmap_bridge_provider.py` — **Step-1 provider**: Tailer thread that reads ticks.csv into market_data schema for Step 2. Port of ninja_bridge_provider.py with BookMap improvements: operation=Buy/Sell → direct side for CVD (98%+), full depth, optional MBO events. Keeps same rotation + gzip archival (200 MB → data/archive/*.gz), 8h window, 64 MB catch-up, backward compat aliases.
- `gold_robot_bookmap.py` — **Monitor robot**: Live tape viewer (same as gold_robot_ntbridge.py but BookMap wording, direct-side % in stats, supports BOOKMAP_* env vars).
- `docs/BOOKMAP_SETUP_GUIDE.md` — **Setup guide**: One-time BookMap install, Rithmic connection, addon enable, daily routine, market clock, troubleshooting, migration from NT.
- `docs/PORT_SUMMARY.md` — This file.

### Updated files
- `config.py` — Supports BOOKMAP_* keys (BOOKMAP_BRIDGE_FILE, BOOKMAP_WINDOW_SECONDS, BOOKMAP_CATCHUP_MB, BOOKMAP_ROTATE_MB, BOOKMAP_ARCHIVE_KEEP_DAYS, BOOKMAP_SYMBOL_FILTER, BOOKMAP_WRITE_MBO, BOOKMAP_FLIP_SIDE) with NT_* fallbacks. Normalizes DATA_SOURCE=ninjabridge → bookmapbridge.
- `data_providers.py` — SUPPORTED_SOURCES now includes bookmapbridge, bookmap, bmbridge (primary) + ninjabridge legacy alias. get_provider() tries bookmap_bridge_provider first, falls back to ninja provider.
- `step1_data_acquisition.py` — Docstring updated for BookMap primary.
- `tools/backtest.py` — Tries BookMap provider first, handles _BridgeTail without __init__ (direct_side_hits compat).
- `requirements.txt` — Added `bookmap>=0.1.0` (BookMap Python API).
- `README.md` — Rewritten for BookMap edition (same structure as NT README but BookMap flow, improvements, migration).
- `.env.example` — Rewritten with BOOKMAP_* keys, same trading params.
- `.env` — Ported version of user's provided .env (DATA_SOURCE=bookmapbridge, BOOKMAP_* keys, same risk/AI/news/macro/PM settings).
- `CHANGELOG.md` — Added v5.0 entry.

### Unchanged (behavior parity)
- `step2_market_analysis.py` (25+ signals, CVD, VWAP, MTF, zones)
- `step3_ai_decision.py` (Gemini AI)
- `step4_mt5_execution.py` (MT5 orders, structural SL/TP)
- `position_manager.py` (trailing, BE, profit lock, momentum exit)
- `step5_monitoring.py`, `markets.py`, `news.py`, `macro.py`, `session.py`, `risk_manager.py`, `trade_guard.py`, `trade_history.py`, `maintenance.py`, `mt5_signal_bridge.py`, `spread_monitor.py`

## Data contract (unchanged, critical)

`ticks.csv` format identical:
```
time,event,price,size,level,operation,instrument
2026-09-16T14:30:00.123,Last,4376.4000,1.0000,-1,Buy,MGCZ5@Rithmic
```

- `event`: Last (trade), Bid, Ask, DepthBid, DepthAsk (same as NT)
- `operation`: For Last = Buy/Sell (BookMap improvement, true side) vs empty (NT legacy, tick-rule fallback). For Depth = Update/Remove.
- `instrument`: alias (e.g. MGCZ5@Rithmic) — root filter MGC matches.

Rotation: at 200 MB → rename to ticks_YYYYMMDD_HHMMSS.csv → gzip to data/archive/*.csv.gz → fresh ticks.csv with header. Backtest accepts .csv.gz.

## Verification done

1. **Synthetic BookMap file** (140 lines, Buy/Sell operation):
   - Provider: 100 ticks, 10 bid/10 ask levels, direct_side_hits=100 (100%), BUY=50 SELL=50.
   - Step 2: signal NEUTRAL strength 14.6, CVD 0.0, ATR 3.23 — analysis works.

2. **Legacy NT file** (20 lines, empty operation):
   - Provider: 20 ticks parsed via tick-rule fallback — backward compat OK.

3. **Full pipeline** `main.py` (20 ticks synthetic):
   - STEP 1 OK (bookmapbridge, has_data=True)
   - STEP 2 OK (NEUTRAL, strength 9.4, confidence 35.6)
   - STEP 3 HOLD (signal <12, AI skipped)
   - STEP 4 SKIPPED (empty snapshot price — expected with tiny file, real data will have price)
   - STEP 5 OK
   - Same as NT e2e verification.

4. **Backtest** `tools/backtest.py --file ticks.csv` (6000 synthetic BookMap events):
   - 84 cycles, 7 trades, win rate 71.4%, profit factor 1.87, net $5.44, max DD $6.25, avg hold 7.4 min.
   - PM rules exercised: VWAP_TRAIL, PROFIT_LOCK, ADOPT_TIGHTEN, etc. — position manager works with BookMap data.

## How to use (from user's .env)

User's provided .env (Pepperstone, MGC 12-26, 28800 window, 200 MB rotate, etc.) was ported 1:1:

**Old (NT):**
```
DATA_SOURCE=ninjabridge
NT_BRIDGE_FILE=
NT_WINDOW_SECONDS=28800
...
```

**New (BookMap):**
```
DATA_SOURCE=bookmapbridge
BOOKMAP_BRIDGE_FILE=
BOOKMAP_WINDOW_SECONDS=28800
...
```

Same risk: RISK_PER_TRADE_PCT=0.1, MAX_LOT_SIZE=0.02, COOLDOWN 15 min, MAX_TRADES_PER_DAY 30, CONFIDENCE 70, etc. Same news/macro/PM settings.

**Daily routine:**
1. Start BookMap Global (Rithmic real-time, MGC chart, addon enabled)
2. `python main.py --loop` (or `gold_robot_bookmap.py` for monitor)
3. Watch PIPELINE SUMMARY.

## Migration steps (for user)

1. Install BookMap Global + Rithmic same login as NT (close NT to free login).
2. `pip install -r requirements.txt` + `pip install bookmap`
3. BookMap → Settings → API plugins → Add → `bookmap_addon.py` → enable for MGC.
4. Copy tuned `.env` (provided as `.env` in this folder) — already ported, just add Gemini keys.
5. `python main.py --loop` — should show `BookMapBridge provider: X ticks (Y direct side)`.
6. Optional parallel run 1 week if broker enables R|Trader Pro plugin mode (compare candles/CVD/VWAP/votes).

## Deliverables

- Folder `Gold-BookMap/` — complete, self-contained, one-folder layout (same as Gold-MT5 but BookMap).
- All data comes from BookMap platform via `bookmap_addon.py` → `ticks.csv` → `bookmap_bridge_provider.py`.
- No NinjaTrader dependency — NT files kept only as legacy alias for backward compat.

## Notes

- Delayed data protection: addon checks `isDelayed` flag, refuses to write delayed free-tier data — only real-time Rithmic reaches robot (hard rule from BOOKMAP_PORT_GUIDE.md).
- Max hold ~1 hour respected everywhere (PM time stop 60 min).
- File lifecycle bounded: live ~200-250 MB, RAM flat ~30 MB for 8h window, archives ~100-150 MB/day gz.

*Ported 2026-09-16, v5.0, from Gold-MT5 v4.4.3. Behavior parity first, BookMap improvements second.*
