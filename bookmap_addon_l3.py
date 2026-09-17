import os, threading
from datetime import datetime
from pathlib import Path

# ABSOLUTE PATHS - critical when running inside BookMap
BRIDGE_FILE = os.getenv("BOOKMAP_BRIDGE_FILE") or r"A:\gitHub\Gold-BookMap\ticks.csv"
MBO_FILE = os.getenv("BOOKMAP_MBO_FILE") or r"A:\gitHub\Gold-BookMap\mbo.csv"
SYMBOL_ROOT = os.getenv("BOOKMAP_SYMBOL_FILTER") or "GC"
CSV_HEADER = "time,event,price,size,level,operation,instrument\n"
WRITE_MBO = os.getenv("BOOKMAP_WRITE_MBO", "1") == "1"

p = Path(BRIDGE_FILE)
print(f"[BridgeL3] Output: {BRIDGE_FILE}", flush=True)
print(f"[BridgeL3] MBO Output: {MBO_FILE} Enabled={WRITE_MBO}", flush=True)
print(f"[BridgeL3] Symbol filter: {SYMBOL_ROOT}", flush=True)
try:
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists() or p.stat().st_size == 0:
        p.write_text(CSV_HEADER, encoding="utf-8")
        print(f"[BridgeL3] Created header", flush=True)
    if WRITE_MBO:
        mp = Path(MBO_FILE)
        if not mp.exists() or mp.stat().st_size == 0:
            mp.write_text("time,event_type,order_id,price,size,instrument\n", encoding="utf-8")
except Exception as e:
    print(f"[BridgeL3] File create fail: {e}", flush=True)

lock = threading.Lock()
def write_line(t, ev, price, size, lvl, op, inst):
    if size < 0 and ev == "Last":
        return
    if ev == "Mbo" and price < 0:
        # CANCEL sentinel - keep 0 price but log correctly
        price = 0.0
        size = 0.0
    line = f"{t},{ev},{price:.4f},{size:.4f},{lvl},{op},{inst}\n"
    with lock:
        try:
            with open(BRIDGE_FILE, "a", encoding="utf-8") as f:
                f.write(line)
        except:
            pass

def now_iso():
    return datetime.now().astimezone().isoformat(timespec='milliseconds')

infos = {}
stats = {"trades":0,"depth":0,"mbo":0,"mbo_new":0,"mbo_cancel":0}

def should(alias):
    """v5.4.1 EASY-SWITCH: GC filter accepts MGC too, comma list, empty=all"""
    if not SYMBOL_ROOT:
        return True
    up = alias.upper()
    if "," in SYMBOL_ROOT:
        roots = [r.strip().upper() for r in SYMBOL_ROOT.split(",") if r.strip()]
        return any(r in up for r in roots)
    if SYMBOL_ROOT.upper() in ("GC", "MGC"):
        return ("GC" in up or "MGC" in up)
    return SYMBOL_ROOT.upper() in up

def handle_subscribe_instrument(addon, alias, full_name, is_crypto, pips, size_mult, inst_mult, supported_features=None):
    if supported_features is None:
        supported_features = {}
    print(f"[BridgeL3] Sub {alias} ({full_name}) pips={pips} size_mult={size_mult} features={supported_features}", flush=True)
    if supported_features.get("isDelayed"):
        print(f"[BridgeL3] REJECT delayed {alias} - use real-time Rithmic!", flush=True)
        return
    if not should(alias):
        print(f"[BridgeL3] Filter out {alias}", flush=True)
        return
    infos[alias] = (pips, size_mult)
    try:
        import bookmap as bm
        bm.subscribe_to_depth(addon, alias, 1)
        bm.subscribe_to_trades(addon, alias, 2)
        if WRITE_MBO and supported_features.get("mbo"):
            bm.subscribe_to_mbo(addon, alias, 3)
            print(f"[BridgeL3] MBO subscribed for {alias} - L3 ACTIVE", flush=True)
        else:
            print(f"[BridgeL3] MBO not available for {alias} (feature mbo={supported_features.get('mbo')})", flush=True)
    except Exception as e:
        print(f"[BridgeL3] sub fail {e}", flush=True)

def handle_unsubscribe_instrument(addon, alias):
    infos.pop(alias, None)

def handle_depth(addon, alias, is_bid, price_level, size_level):
    if not should(alias): return
    inf = infos.get(alias)
    if not inf: return
    pips, sm = inf
    price = float(price_level) * float(pips)
    size = float(size_level) / float(sm) if sm else float(size_level)
    ev = "DepthBid" if is_bid else "DepthAsk"
    op = "Remove" if size_level==0 else "Update"
    write_line(now_iso(), ev, price, size, -1, op, alias)
    stats["depth"]+=1

def handle_trades(addon, alias, price_level, size_level, is_otc, is_bid, is_start, is_end, agg_id, pas_id):
    if not should(alias): return
    inf = infos.get(alias)
    if not inf: return
    pips, sm = inf
    try:
        cand = float(price_level)*float(pips)
        price = cand if 1000 < cand < 10000 else float(price_level)
        if 1000 < float(price_level) < 10000 and not (1000 < cand < 10000):
            price = float(price_level)
    except:
        price = float(price_level)
    size = float(size_level)/float(sm) if sm else float(size_level)
    if size <= 0:
        return
    side = "Sell" if is_bid else "Buy"
    write_line(now_iso(), "Last", price, size, -1, side, alias)
    stats["trades"]+=1

def handle_mbo(addon, alias, event_type, order_id, price_level, size_level):
    if not WRITE_MBO: return
    if not should(alias): return
    inf = infos.get(alias)
    if not inf: return
    pips, sm = inf
    # Fix sentinel values: CANCEL has price_level=-1, size_level=-1 in BookMap API
    try:
        pl = float(price_level)
        sl = float(size_level)
    except:
        pl = -1
        sl = -1

    is_cancel = "CANCEL" in str(event_type).upper()
    if pl <= 0 or is_cancel and pl < 0:
        # For CANCEL, price is not meaningful - use 0, keep order_id for tracking
        price = 0.0
        size = 0.0
    else:
        price = pl * float(pips)
        size = sl / float(sm) if sm else sl
        # Validate price range for MGC (1000-10000)
        if not (1000 < price < 10000) and 1000 < pl < 10000:
            price = pl  # fallback raw is already correct

    # Write to ticks.csv as Mbo + to mbo.csv detailed
    write_line(now_iso(), "Mbo", price, size, -1, event_type, alias)
    try:
        with open(MBO_FILE, "a", encoding="utf-8") as f:
            f.write(f"{now_iso()},{event_type},{order_id},{price:.4f},{size:.4f},{alias}\n")
    except:
        pass
    stats["mbo"]+=1
    if is_cancel:
        stats["mbo_cancel"]+=1
    else:
        stats["mbo_new"]+=1
    if stats["mbo"] % 200 == 0:
        print(f"[BridgeL3] MBO {stats['mbo']} (new {stats['mbo_new']} cancel {stats['mbo_cancel']}) trades {stats['trades']} depth {stats['depth']}", flush=True)

def handle_response(addon, req_id):
    print(f"[BridgeL3] resp req_id={req_id} stats={stats}", flush=True)

if __name__ == "__main__":
    import bookmap as bm
    addon = bm.create_addon()
    bm.add_depth_handler(addon, handle_depth)
    bm.add_trades_handler(addon, handle_trades)
    try:
        bm.add_mbo_handler(addon, handle_mbo)
    except AttributeError:
        print("[BridgeL3] MBO handler not available", flush=True)
    bm.add_response_data_handler(addon, handle_response)
    bm.start_addon(addon, handle_subscribe_instrument, handle_unsubscribe_instrument)
    print("[BridgeL3] Running with L3...", flush=True)
    bm.wait_until_addon_is_turned_off(addon)
