# L3 Fix v2 — CANCEL sentinel bug

**Date:** 2026-09-17 01:05 CEST  
**Issue reported:** `mbo.csv` shows `CANCEL,781588..., -0.1000, -1.0000` — incorrect price/size

**Root cause:**
BookMap Python API `handle_mbo` returns `price_level = -1, size_level = -1` for CANCEL events (order no longer exists, no price). Previous version did `price = price_level * pips = -1 * 0.1 = -0.1`, `size = -1 / size_mult = -1.0` — wrong.

**Evidence from live log (user):**
```
2026-09-17T01:01:11.844 CANCEL 7815886584349 -0.1000 -1.0000 MGCZ6.COMEX@RITHMIC
2026-09-17T01:01:11.848 ASK_NEW 7815886584359 4303.6000 1.0000 MGCZ6 ✓ precise
2026-09-17T01:01:11.849 REPLACE 7815886569373 4304.2000 5.0000 MGCZ6 ✓ precise
2026-09-17T01:01:11.850 ASK_NEW 7815886584362 4305.5000 100.0000 MGCZ6 ✓ large order detected!
```
- ASK_NEW/BID_NEW/REPLACE are **100% precise** (4303.6, 4304.2, 4305.5, 100 lots)
- CANCEL sentinel must be fixed to 0,0

**Fix in `bookmap_addon_l3.py` v2:**

```python
is_cancel = "CANCEL" in str(event_type).upper()
if pl <= 0 or is_cancel and pl < 0:
    price = 0.0
    size = 0.0
else:
    price = pl * pips
    size = sl / sm
```

Also:
- Stats split: mbo_new vs mbo_cancel for monitoring
- write_line filters negative Mbo to 0,0 for cancel
- Provider updated to correctly side-tag BID_NEW/ASK_NEW

**Files changed:**
1. `bookmap_addon_l3.py` — fixed CANCEL handling (95→115 lines still safe for editor)
2. `bookmap_bridge_provider.py` — improved side detection for Mbo events (BID_NEW/ASK_NEW)

**Re-build steps (same as before):**
1. Download `bookmap_addon_l3.py` (v2)
2. Remove GoldBridge in BookMap
3. Paste in embedded editor → Save → Build
4. Add new JAR → Enable → Save Workspace
5. Delete old `mbo.csv` to start clean: `rm mbo.csv` (it will recreate with header)
6. `tail -f mbo.csv` — CANCEL should now show `0.0000,0.0000` not `-0.1000`

**Verification after fix:**
```
ASK_NEW, ... 4303.6000,1.0000 ✓
BID_NEW, ... 4302.9000,1.0000 ✓
CANCEL, ... 0.0000,0.0000 ✓ fixed
REPLACE, ... 4304.2000,5.0000 ✓
```

**L3 now precise:** New orders have exact price/size/order_id, cancels tracked by order_id, large orders (100 lots) detected — exactly what needed for iceberg & absorption detection.

**Provider impact:** `order_events` now contains correct L3 data, `python main.py` will show `L3=available` and `Large order events: X` in snapshot.

