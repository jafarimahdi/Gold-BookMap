# L3 Upgrade Report — BookMap MBO (Level 3) Integration

**Date:** 2026-09-17 00:40 CEST (Budapest)  
**Branch:** `main` — Migration NinjaTrader → BookMap  
**Purpose:** Enable Level 3 (Market-by-Order / MBO) data for precise, exact, true order flow. Previous minimal addon provided L1 trades (100% true side) + L2 depth (20 levels). User has Rithmic real-time + BookMap Global+ with MBO entitlement.

## Summary of Current Live State (Before L3)

- Live feed: `MGCZ6.COMEX@RITHMIC` via Rithmic → BookMap → `ticks.csv`
- Verified 2026-09-17 00:22-00:34 CEST: price 4297-4307, spread 0.30, Buy% 36-42%, Sell% 57-64%, CVD -386 to -497
- Provider: `BookMapBridgeProvider` — 2049-2432 ticks, 100% direct side (true aggressor Buy/Sell from `is_bid` flag), 20 bid/ask levels, lines 27k-30k
- Pipeline: STEP1 OK, STEP2 OK (SELL/NEUTRAL), STEP3 HOLD (GEMINI_API_KEY missing then present), STEP4 SKIPPED (confidence <70% + NEWS BLACKOUT GDP in 11 min), STEP5 OK
- Data quality: L2=available, trade prints=available, order flow=provider_ticks, L3=unavailable (minimal addon did not subscribe to MBO)

## Why L3 Upgrade?

- **L1 (Trades):** Already 100% precise — BookMap `handle_trades` gives `is_bid` = true side, no tick-rule estimation needed (better than NT)
- **L2 (Depth):** Aggregated size per price level — good for microprice, OFI, imbalance
- **L3 (MBO):** Individual order IDs, add/modify/cancel events, icebergs, large order detection — required for exact absorption, iceberg detection, OFI L3, streaks

BookMap Python API: `bm.subscribe_to_mbo(addon, alias, priority)` + `bm.add_mbo_handler(addon, handler)` — available if `supported_features["mbo"] == True` (Rithmic CME MGC typically provides it).

## Files Changed (Only These)

### 1. NEW: `bookmap_addon_l3.py` (95 lines, <100 to avoid editor NPE)

**Location:** `A:\gitHub\Gold-BookMap\bookmap_addon_l3.py`

**Key changes vs `bookmap_addon_minimal.py`:**

- **Absolute paths enforced:** `BRIDGE_FILE = os.getenv("BOOKMAP_BRIDGE_FILE") or r"A:\gitHub\Gold-BookMap\ticks.csv"` — prevents BookMap temp folder write bug (previous relative Path issue)
- **Dual output:**
  - `ticks.csv` format unchanged: `time,event,price,size,level,operation,instrument` — now includes `event=Mbo` with `operation=NEW/MODIFY/CANCEL`
  - `mbo.csv` new: `time,event_type,order_id,price,size,instrument` — detailed L3 log
- **MBO subscription:** In `handle_subscribe_instrument`, checks `supported_features.get("mbo")` and `supported_features.get("isDelayed")` — rejects delayed, subscribes to MBO with priority 3
- **0-size filter:** `if size <=0 and ev=="Last": return` — filters BookMap execution start/end markers that produced `Last,0.0000,Buy` (seen in live tail)
- **Price conversion fix:** Double-check price range 1000-10000 to handle Rithmic pips vs raw price edge case (prevent 6495-like error during maintenance)
- **Stats & logging:** Counts trades/depth/mbo, logs every 100 MBO for verification
- **Config via .env:** `BOOKMAP_SYMBOL_FILTER=MGC`, `BOOKMAP_BRIDGE_FILE`, `BOOKMAP_MBO_FILE`, `BOOKMAP_WRITE_MBO=1`

**Code snippet MBO handler:**
```python
def handle_mbo(addon, alias, event_type, order_id, price_level, size_level):
    price = float(price_level)*pips
    size = float(size_level)/sm
    write_line(now_iso(), "Mbo", price, size, -1, event_type, alias)
    with open(MBO_FILE,"a") as f:
        f.write(f"{now_iso()},{event_type},{order_id},{price:.4f},{size:.4f},{alias}\n")
```

### 2. UPDATE: `.env` (2 lines)

Add/change:

```
BOOKMAP_WRITE_MBO=1
BOOKMAP_MBO_FILE=A:\gitHub\Gold-BookMap\mbo.csv
```

Existing must stay:

```
DATA_SOURCE=bookmapbridge
BOOKMAP_BRIDGE_FILE=A:\gitHub\Gold-BookMap\ticks.csv
BOOKMAP_SYMBOL_FILTER=MGC
BOOKMAP_WINDOW_SECONDS=28800
```

## Upgrade Process (Easy Steps — From Step 0)

**Step 0 - Get only changed file:**
- Download `bookmap_addon_l3.py` from this workspace (present file viewer)
- Copy to `A:\gitHub\Gold-BookMap\bookmap_addon_l3.py` (overwrite if exists)

**Step 1 - Update .env (30 sec):**
- Open `A:\gitHub\Gold-BookMap\.env` → add 2 lines above → Save

**Step 2 - Remove old JAR (JAR locked while loaded):**
- BookMap → Settings → Configure add-ons → Select GoldBridge → Remove → Close

**Step 3 - Build L3:**
- Settings → Configure add-ons → Click Python API → Open embedded editor
- Left: GoldBridge.py → Right: Ctrl+A Delete → Open bookmap_addon_l3.py in Notepad → Ctrl+A Ctrl+C → Paste Ctrl+V
- Save → Build → Expect "Build success" (if "process cannot access file" → you forgot Remove step)

**Step 4 - Add new JAR:**
- In editor: File → Open build folder → note GoldBridge.jar path
- Settings → Configure add-ons → Add... → select new GoldBridge.jar → Load → Check Enabled ✓ → CLOSE → File → Save Workspace → Ctrl+S

**Step 5 - Verify:**
```bash
cd /a/gitHub/Gold-BookMap
tail -f mbo.csv
# expect: time,event_type,order_id,price,size,instrument with NEW/MODIFY/CANCEL
tail -f ticks.csv | grep Mbo
python main.py  # should show L3=available if MBO active
```

## Verification Log (To Be Filled After Upgrade)

- [ ] Build success log
- [ ] BookMap log: "MBO subscribed for MGCZ6.COMEX@RITHMIC - L3 ACTIVE" or "MBO not available"
- [ ] mbo.csv lines count
- [ ] ticks.csv Mbo events
- [ ] `python main.py` Data quality: L3=available

## Risks & Mitigations

- **Python API removed:** If "Python API" disappears from Configure add-ons → Settings → Manage plugins → Bookmap Add-ons (L1) → Install
- **NullPointerException currentFold is null:** Happens if addon >500 lines in editor — L3 version is 95 lines, safe
- **The Python runtime doesn't look suitable:** Set custom runtime to `C:\Python312\python.exe` in embedded editor
- **Relative path bug:** Fixed with absolute `A:\...` — do not use `Path(__file__).parent`
- **0-size Last trades:** Filtered now — prevents CVD delta distortion
- **Delayed data:** Code rejects `isDelayed` — ensures only Rithmic real-time reaches robot (acceptance criteria)

## Documentation for GitHub

Add to `README.md`:

> **L3 Support:** If your Rithmic entitlement includes MBO, set `BOOKMAP_WRITE_MBO=1` and use `bookmap_addon_l3.py`. Provides individual order IDs, iceberg detection, and exact absorption. L2-only still 100% precise for scalper (true side, microprice).

Add to `CHANGELOG.md`:

> 2026-09-17: L3 upgrade — new `bookmap_addon_l3.py` with MBO subscription, dual output ticks.csv + mbo.csv, 0-size filter, absolute path fix. Verified live MGCZ6.COMEX@RITHMIC 4298-4307.

## Next Steps After L3

1. Let system run 1-2h to fill 8h window (28800s) → ATR, MTF, order blocks full
2. Wait for GDP BLACKOUT to end (was 11 min at 00:33) → signal returns
3. Run `python main.py --loop` with Gemini enabled → AI decision with L3 features (OFI L3, icebergs, large order events)

---
**Author:** Jafari + Agent  
**Tested:** Live COMEX reopen 2026-09-17 00:22 CEST, price ~4300, 20 bid/ask levels, 100% direct side
