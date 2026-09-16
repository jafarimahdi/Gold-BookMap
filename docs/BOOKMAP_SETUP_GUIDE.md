# BookMap Bridge — Setup & Management Guide (v5 BookMap Edition)

**Package:** BookMap → CSV → Python robot (full port from NinjaTrader)
**Files:** `bookmap_addon.py` (BookMap Python API addon) + `bookmap_bridge_provider.py` (tailer)
**Goal:** stream live COMEX gold data into your Python robot through BookMap's *authorized* Rithmic connection — same broker login you used for NinjaTrader, now in BookMap.

```
   Rithmic servers (real-time, same login as NT)
         │  (BookMap Global is authorized — uses your Rithmic credentials)
         ▼
   BookMap Global  ── MGC chart with GoldBookMapBridge addon enabled
         │  (writes every trade / bid / ask / full-depth L2)
         ▼
   Gold-BookMap/ticks.csv  (time,event,price,size,level,operation,instrument)
         │  (operation for Last = Buy/Sell = true aggressor from BookMap)
         ▼
   bookmap_bridge_provider.py  ──► Step 2 SignalEngine (25+ signals)
         │                        (CVD now exact, not tick-rule inferred)
         ▼
   Step 3 Gemini AI → Step 4 MT5 execution → Position Manager → Step 5 loop
```

---

## Part A — BookMap installation (one time)

1. **Get BookMap Global** (or Global+):
   - BookMap has a free Digital tier (delayed futures ~15 min) — good for learning heatmap reading for 1 week, $0.
   - For live trading you need **Global plan + real-time Rithmic** via your broker (same Rithmic login you used in NinjaTrader).
   - Download from https://bookmap.com — install to default `C:\Program Files\BookMap`.

2. **Connect Rithmic in BookMap:**
   - BookMap → Connections → Add → Rithmic → enter same username/password you used in NinjaTrader (Rithmic Paper or Live).
   - Ensure connection status is **green/connected**. This is the ONLY place Rithmic login should be active — close NinjaTrader to avoid single-login conflict, or ask broker for R|Trader Pro plugin mode if you want parallel run for validation.

3. **Install Python API:**
   ```bash
   pip install bookmap
   ```
   - BookMap's Python API is free for all BookMap users (even Digital tier). It's a pip package that talks to BookMap via TCP socket.
   - Also install robot deps:
   ```bash
   cd Gold-BookMap
   pip install -r requirements.txt
   ```

---

## Part B — Set up the Gold chart in BookMap (one time, ~2 min)

1. In BookMap: **Add instrument** → type **MGC** (Micro Gold) or **GC** (standard Gold) → select **front-month contract with real volume** (e.g. `MGCZ5` or `MGC 12-26`). MGC is 10oz, cheaper margins — recommended same as NT version.
2. Timeframe: any — BookMap is tick-based, not candle-based. 1 Minute chart is fine for visual reference.
3. The chart should show live heatmap (liquidity walls) and trades (dots). If you see "Delayed" watermark, you are on free tier — not usable for robot (delayed data protection will block writes). You need real-time Rithmic.

---

## Part C — Install the Python addon (one time, ~5 min)

### Option 1: Run as external Python addon (recommended, simplest)

1. Copy `Gold-BookMap` folder to your PC (e.g. `A:\gitHub\Rhitmic\Gold-BookMap`).
2. In BookMap: **Settings → API plugins configuration** (or toolbar button) → **Add** → select `bookmap_addon.py` file → Enable checkbox.
3. The addon banner will show: `Output file: .../Gold-BookMap/ticks.csv`

### Option 2: Run manually from terminal (also works)

```bash
cd Gold-BookMap
python bookmap_addon.py
```

You should see:
```
[BookMapBridge] Output file: A:\gitHub\Rhitmic\Gold-BookMap\ticks.csv
[BookMapBridge] Symbol filter root: 'MGC'
[BookMapBridge] Starting BookMap addon...
[BookMapBridge] Subscribing to MGCZ5@Rithmic (Micro Gold) pips=0.1 size_mult=1.0
```

Verify: open File Explorer → `Gold-BookMap/ticks.csv` → during market hours, new lines appear within seconds. Example:
```
time,event,price,size,level,operation,instrument
2026-09-16T14:30:00.123,Last,4376.4000,1.0000,-1,Buy,MGCZ5@Rithmic
2026-09-16T14:30:00.124,Bid,4376.3000,12.0000,-1,,MGCZ5@Rithmic
2026-09-16T14:30:00.124,Ask,4376.5000,7.0000,-1,,MGCZ5@Rithmic
2026-09-16T14:30:00.125,DepthBid,4375.9000,3.0000,-1,Update,MGCZ5@Rithmic
```

- `Last` with `Buy`/`Sell` in operation column = BookMap improvement (true aggressor side). Old NT files had empty operation — provider handles both.
- `DepthBid`/`DepthAsk` = full depth (all levels), not just top 5.

### Custom output folder?

The robot finds `ticks.csv` **automatically** — checks in order:
`BOOKMAP_BRIDGE_FILE` env → `NT_BRIDGE_FILE` env → same folder as script → `A:\gitHub\Rhitmic\Gold-MT5` → `C:\BookMapBridge` → `C:\NinjaBridge`.

To force a specific file:
```bash
python gold_robot_bookmap.py --file "A:/gitHub/Rhitmic/Gold-BookMap/ticks.csv"
```

If file doesn't exist yet, provider waits and prints "Waiting for the bridge file" — nothing breaks.

---

## Part D — Run the monitor robot (live tape)

```bash
cd Gold-BookMap
python gold_robot_bookmap.py
```

You should see:
```
[TRADE ] MGCZ5 px=4,376.40 qty=1 @ 2026-09-16T14:30:00.123 Buy
[BID   ] MGCZ5 4,376.30 x 12
[ASK   ] MGCZ5 4,376.50 x 7
[BOOK  ] MGCZ5 bids: 4,376.30x12  4,376.20x8  |  asks: 4,376.50x7  4,376.60x15
[STATS ] 85 events/s | total 12,345 | file 8.2 MB | direct side 98%
```

**Options:**
| Command | What it does |
|---|---|
| `python gold_robot_bookmap.py` | Live mode — only NEW events |
| `python gold_robot_bookmap.py --from-start` | Replay whole file |
| `python gold_robot_bookmap.py --only last` | Only trades |
| `python gold_robot_bookmap.py --book-interval 0` | No [BOOK] summary |
| `python gold_robot_bookmap.py --big 10` | Alert on trades ≥10 lots (MGC micro) |
| `python gold_robot_bookmap.py --file D:\data\ticks.csv` | Different bridge file |

No extra pip needed for monitor — stdlib only.

---

## Part E — Run the full trading pipeline (AI + MT5)

This is identical to the NT version, just data source changed:

```bash
cd Gold-BookMap
python main.py            # one full pass (great for testing)
python main.py --loop     # continuous robot — leave it running
```

Watch the **PIPELINE SUMMARY** at end of each pass. Ctrl+C stops.

**Phases:**
| Phase | .env | What happens |
|---|---|---|
| **2 — dry run** (current) | `TRADING_ENABLED=0` | Full analysis every 60s, decisions logged, **zero orders** |
| **3 — demo trading** | MT5 demo + `TRADING_ENABLED=1` | Real orders on demo when AI confidence ≥70% |
| **4 — review** | let run 1-2 weeks | Review `data/decisions_log.csv` + backtest archives |

---

## Part F — What improves with BookMap (vs NinjaTrader)

1. **True aggressor side** — BookMap's `is_bid` flag gives exact Buy/Sell for each trade. Old NT bridge inferred side via tick rule (price vs bid/ask). CVD is now exact. Provider logs direct-side ratio (should be ~95-100% with BookMap).
2. **Full-depth L2** — NT exporter sent limited depth (top of book + some levels). BookMap sends ALL price levels. Wall/absorption votes are stronger.
3. **MBO/L3 optional** — set `BOOKMAP_WRITE_MBO=1` to also write MBO stream (add/modify/cancel per order). Phase 2: iceberg + spoof detection votes can use `mbo.csv`. Main `ticks.csv` stays compatible.
4. **No C# compile** — old NT bridge needed NinjaScript Editor + F5 compile. BookMap bridge is pure Python — edit and restart.
5. **Same rotation + archival** — `ticks.csv` rotates at 200 MB (configurable) → gzipped to `data/archive/` (~8-10× smaller). Backtest accepts `.csv.gz` directly: `python tools/backtest.py --file data/archive/ticks_20260916_170000.csv.gz`

---

## Daily operation & management

**Start order (each session):**
1. Open BookMap → it auto-connects to Rithmic (green/connected at bottom).
2. Ensure **MGC chart** with addon enabled is open — tab in background is fine; don't close chart while robot runs. Minimizing BookMap window is OK.
3. Run `python main.py --loop` or `gold_robot_bookmap.py`.

**Stop order:** Ctrl+C robot terminal anytime; close BookMap when done. BookMap keeps writing to file even when robot is off, so you can restart robot without touching BookMap.

**Market clock (Budapest time):**
| When | What happens |
|---|---|
| Mon–Fri 00:00–23:00 | Market open — data flows |
| **Daily 23:00–00:00** | CME Globex halt — no ticks (robot prints [STALE]) |
| 23:20–23:45 weekdays | Rithmic maintenance — data may pause |
| **Fri 23:00 → Mon 00:00** | Weekend — no ticks at all |

**When robot prints [STALE]:** checklist:
1. Market closed (see table)
2. BookMap disconnected (check Rithmic green)
3. MGC chart closed
4. Addon disabled (Settings → API plugins → enable)
If BookMap reconnects after drop, file continues — robot resumes automatically.

**Data-file housekeeping (v5, same as v4.4.3):**
- `ticks.csv` grows ~35–40 MB/hour.
- At **200 MB** (`BOOKMAP_ROTATE_MB`) robot rotates file itself: old data → chunk, fresh `ticks.csv` starts, analysis window untouched.
- Chunk compressed to `data/archive/` (~100–150 MB gz per day) — permanent backtest record.
- `BOOKMAP_ARCHIVE_KEEP_DAYS=0` keeps forever; set e.g. `30` to auto-delete older.
- Live folder stays ~200–250 MB, RAM flat (~30 MB for 8h window).

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `has_data=False` in summary | BookMap not running / chart closed / market closed / wrong `BOOKMAP_BRIDGE_FILE` |
| Step 3 always HOLD @ 0% | `GEMINI_API_KEY` missing in `.env` |
| `Delayed data` rejected | BookMap free tier — need Global plan + real-time Rithmic |
| `bookmap package not installed` | `pip install bookmap` — or run addon in DEMO mode (`python bookmap_addon.py --demo`) for testing |
| Trades side inverted | Set `BOOKMAP_FLIP_SIDE=1` in `.env` — some providers invert is_bid meaning |
| File locked / rotation deferred | Normal when BookMap holds file open — provider retries. If stuck, stop robot, delete `ticks.csv`, restart |
| Parallel run NT + BookMap | Use R|Trader Pro plugin mode (broker must enable) — Rithmic allows one platform per login otherwise |

---

## Migration from NinjaTrader version

If you already have Gold-MT5 running:

1. **Stop NT robot** and close NinjaTrader (free the Rithmic login).
2. **Copy Gold-BookMap** folder next to old one (or overwrite — keep tuned `.env` safe first!).
3. **Install BookMap Global** + connect same Rithmic login.
4. **Install addon** (`bookmap_addon.py`) as above.
5. **Paste your tuned `.env`** back, with 2 edits:
   - `DATA_SOURCE=bookmapbridge` (was `ninjabridge`)
   - Keep `DATA_SYMBOL=MGC 12-26` same
   - Optional: rename `NT_*` keys to `BOOKMAP_*` (old names still work as alias).
6. **Run** `python main.py --loop` — should show `BookMapBridge provider: X ticks (Y direct side)` in log.
7. **Validate 1 week parallel** (if you have plugin mode): compare candles, CVD, VWAP, vote scores minute-by-minute. Divergence should be explainable (direct-side CVD vs tick-rule CVD) and bounded.

Then retire NinjaTrader.

---

## Acceptance tests (from BOOKMAP_PORT_GUIDE.md)

1. **Parallel run ≥1 week**: NT and BookMap both feeding; compare candles, CVD, VWAP, votes.
2. **Backtest parity**: `PYTHONHASHSEED=0 python tools/backtest.py --file <same day>` on NT-fed and BookMap-fed archives — same cycles/entries.
3. **E2E dry run**: `TRADING_ENABLED=0` for full trading day; verify decision log, news blackout, PM behavior.
4. Only after all three: retire NinjaTrader.

---

*BookMap edition v5.0 — ported from Gold-MT5 v4.4.3. Behavior parity first, improvements second. Max hold ~1 hour respected everywhere.*
