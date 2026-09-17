# Gold Trading System — BOOKMAP EDITION v5.3 MEDIUM DONE
## BookMap L1+L2+L3 data + 5-step AI brain + MT5 execution + Institutional Upgrades

**Status: ✅ v5.3 MEDIUM 6 DONE 2026-09-17 11:05** — Footprint direct side + absorption 50, vol-adj sizing min-lot skip, iceberg/spoof L3 alpha 1.0/0.8, macro HIGH boost + DXY veto, MBO archival 100MB gzip, TCA weekly report. v5.2 CRITICAL 4 DONE earlier (L3 whale 1.5x, regime-adaptive, AI fallback 8s cache 5min, basis risk). Live trade order 90001722, 10756 ticks 100% direct, L3 OFI -3071, whales 250/300 lots.

```
BookMap Global (MGCZ6.COMEX@RITHMIC chart + GoldBridge L3 addon)  ← run this first
        │ writes
        ▼
Gold-BookMap/ticks.csv   (CME gold: trades with true Buy/Sell, full-depth L2, MBO L3)
Gold-BookMap/mbo.csv     (L3 MBO: order_id, BID_NEW/ASK_NEW/CANCEL/REPLACE, whales 100-300 lots)
        │ tailed by
        ▼
bookmap_bridge_provider.py      ← Step-1 provider (100% direct side, 20 levels, L3 available)
        ▼
STEP 2  SignalEngine (25+ signals: L2 imbalance, microprice, CVD exact, VWAP, L3 OFI -3071, large orders)
        ▼
STEP 3  Gemini AI  →  BUY / SELL / HOLD + confidence (78% SELL verified)
        ▼
STEP 4  MT5 execution  →  XAUUSD on Pepperstone MT5 (structural SL/TP: order blocks / POC)
          order 90001722 deal 59905473 SELL 0.01 @ 4265.14 SL 4270.28 TP 4262.93
        ▼
STEP 4a Position manager  →  break-even, trailing, early exits, profit lock
        ▼
STEP 5  Monitoring loop (repeat every 60 s)
```

**What changed vs NinjaTrader version:**
- `GoldBridgeExporter.cs` (C# NinjaScript) → `bookmap_addon_l3.py` (Python BookMap API, L3 MBO)
- `ninja_bridge_provider.py` → `bookmap_bridge_provider.py` (true side, full depth, L3)
- `gold_robot_ntbridge.py` → `gold_robot_bookmap.py`
- `DATA_SOURCE=ninjabridge` → `DATA_SOURCE=bookmapbridge` (legacy alias still works)
- All downstream (Step 2, AI, MT5, PM, risk, news, macro) **unchanged** — behavior parity.

**BookMap improvements (verified live 2026-09-17):**
1. **True Buy/Sell side** — 10756/10756 = 100% direct side from BookMap `is_bid` → CVD exact. Log: `10756 ticks (10756 direct side)`.
2. **Full-depth L2** — 20 levels, microprice 4302.65, imbalance -0.396, OFI -25, concentration 0.04.
3. **L3 MBO (NEW v5.1)** — individual order IDs (7815886665xxx), BID_NEW/ASK_NEW/CANCEL/REPLACE, whales 100/250/300 lots detected, L3 OFI -3071, large order events 23. CANCEL sentinel fixed (0.0000 not -0.1000). Verified: `mbo.csv` live stream.
4. **No C# compile** — pure Python, 115 lines safe for editor.
5. **Delayed data protection** — rejects `isDelayed`, only Rithmic real-time.

---

## Files — ONE folder, that's it

```
Gold-BookMap/
├── main.py + step1–step5 + config.py      ← pipeline (9 files)
├── data_providers.py + bookmap_bridge_provider.py + ninja_bridge_provider.py ← data layer
├── bookmap_addon_l3.py                    ← BookMap L3 addon (writes ticks.csv + mbo.csv) [NEW v5.1 primary]
├── bookmap_addon_minimal.py               ← L1+L2 fallback (if MBO not entitled)
├── bookmap_addon.py                       ← legacy alias
├── gold_robot_bookmap.py                  ← live monitor robot
├── markets, news, macro, session, risk_manager,
│   trade_guard, spread_monitor, maintenance,
│   mt5_signal_bridge                        ← safety modules
├── .env + .env.example + requirements.txt + README.md + CHANGELOG.md
├── ticks.csv + mbo.csv                     ← live data (178k lines verified)
├── data/   logs/                           ← decisions, snapshots, archives
└── docs/
    ├── BOOKMAP_SETUP_GUIDE.md              ← setup guide (primary)
    ├── L3_UPGRADE_REPORT.md                ← L3 upgrade process
    ├── L3_FIX_v2_REPORT.md                 ← CANCEL sentinel fix
    ├── L3_VERIFIED_REPORT.md               ← live verification 250/300 lot whales
    ├── NT_BRIDGE_SETUP_GUIDE.md            ← legacy NT guide
    ├── BOOKMAP_PORT_GUIDE.md               ← port spec + acceptance tests
    └── POSITION_MANAGER.md, HOW_IT_TRADES.md, BACKTEST.md etc.
```

**One-folder layout:**
```
A:\gitHub\Gold-BookMap\
├── ticks.csv + mbo.csv ← BookMap addon writes LIVE directly here (absolute path)
├── bookmap_addon_l3.py ← addon source (same folder)
├── gold_robot_bookmap.py
├── .env                ← tuned config (BOOKMAP_* keys + BOOKMAP_WRITE_MBO=1)
├── main.py + steps
├── data/ logs/
└── docs/
```

**Setup so BookMap writes into this folder:**
- `.env` has absolute paths: `BOOKMAP_BRIDGE_FILE=A:\gitHub\Gold-BookMap\ticks.csv` and `BOOKMAP_MBO_FILE=...mbo.csv`
- Addon reads env vars, writes directly — no temp folder bug (fixed).

### Tick data lifecycle (v5.1)

- `ticks.csv` ~35-40 MB/hour, `mbo.csv` ~20-30 MB/hour (L3 verbose)
- Rotation at 200 MB → chunk → gzip `data/archive/` (~8-10x smaller, verified)
- Backtest accepts `.csv.gz` directly
- Live footprint ~200-250 MB + archives ~100-150 MB/day, RAM flat (~30 MB for 8h window)

## One-time setup

1. Copy `Gold-BookMap` folder to `A:\gitHub\`.
2. Install deps:
   ```bash
   cd /a/gitHub/Gold-BookMap
   pip install -r requirements.txt
   pip install bookmap
   ```
3. Install **BookMap Global** + Rithmic (same login NT used) — see `docs/BOOKMAP_SETUP_GUIDE.md`.
4. Configure addons: Settings → Configure add-ons → Python API → Open embedded editor → paste `bookmap_addon_l3.py` → Save → Build → Add JAR → Enabled → Save Workspace.
5. `.env`: set `GEMINI_API_KEY`, `BOOKMAP_WRITE_MBO=1`, `BOOKMAP_BRIDGE_FILE`, `BOOKMAP_MBO_FILE`.
6. Verify:
   ```bash
   tail -f mbo.csv   # should show BID_NEW/ASK_NEW/CANCEL 0.0000
   python main.py    # should show L2=available L3=available, large order events
   ```

## How to run (daily)

1. **Start BookMap** — MGCZ6.COMEX@RITHMIC chart with GoldBridge L3 enabled
2. Terminal:
   ```bash
   python main.py            # one pass (test)
   python main.py --loop     # continuous robot — leave running
   python gold_robot_bookmap.py --big 10   # optional monitor window
   ```
3. Watch PIPELINE SUMMARY. Ctrl+C stops.

**Live verification 2026-09-17:**
- 10756 ticks (100% direct side), 20 bid/ask, lines 178001
- L2 available L3 available, OFI L3 -3071, large orders 23
- SELL strength 49.8 confidence 51.4 news QUIET → Gemini SELL 78% → EXECUTED SELL 0.01 XAUUSD @4265.14 order 90001722

## Phases

| Phase | Setup | What happens |
|---|---|---|
| **2 — dry run** | `TRADING_ENABLED=0` | Analysis every 60s, decisions logged, zero orders |
| **3 — demo** | MT5 demo, Algo enabled, `TRADING_ENABLED=1` | Real orders when AI ≥70% (verified live) |
| **4 — review** | run 1-2 weeks | Review `data/decisions_log.csv` + archives |

## Built-in safety (same as NT + BookMap extras)

- `TRADING_ENABLED=0` master switch
- No fresh data (BookMap closed) → no trading (`has_data` gate)
- **Delayed data protection** — addon rejects `isDelayed`
- Confidence <70% → no order; news blackout → no new entries (GDP blackout verified: 11 min → QUIET)
- Risk caps: 1% per trade, max lots, daily loss circuit-breaker, anti-overtrading
- Book hygiene: crossed/stale dropped, max 20 levels, full depth
- Rotation detected automatically — tailer rebuilds
- 0-size Last filtered, CANCEL sentinel fixed

## Quick troubleshooting

| Symptom | Fix |
|---|---|
| `has_data=False` | BookMap not running / chart closed / market closed (23:00-00:00) / wrong path |
| Step 3 always HOLD @0% | `GEMINI_API_KEY` missing |
| `Delayed data rejected` | Need Global plan + real-time Rithmic |
| `bookmap package not installed` | `pip install bookmap` |
| CANCEL -0.1000 | Old addon — use `bookmap_addon_l3.py` v2 |
| `L3=unavailable` | Rithmic entitlement no MBO or `BOOKMAP_WRITE_MBO=0` — L2 still precise |
| `Failed to build: process cannot access` | Remove GoldBridge before Build |
| `NullPointerException currentFold is null` | Addon too large — L3 is 115 lines safe |
| File locked | Normal when BookMap holds file — provider retries |

## Migration from NinjaTrader

See `docs/BOOKMAP_SETUP_GUIDE.md` Part F — 2 .env edits, behavior parity.

## Acceptance tests

1. **Parallel run ≥1 week**: NT vs BookMap — compare candles, CVD, VWAP.
2. **Backtest parity**: `PYTHONHASHSEED=0 python tools/backtest.py --file <day>` on both archives.
3. **E2E dry run**: `TRADING_ENABLED=0` full day — decision log, news blackout, PM.
4. Only after: retire NT.

---

*BookMap edition v5.1 L3 VERIFIED — 2026-09-17 live trade order 90001722, L3 OFI -3071, whales 250/300 lots. Behavior parity first, L3 precision second. Max hold ~1h. All data from BookMap platform.*
