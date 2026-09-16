import os, sys, threading, time
from datetime import datetime
from pathlib import Path

# FIXED PATH - when running INSIDE BookMap, __file__ is inside BookMap's folder,
# not Gold-BookMap. So we MUST use absolute path to your project.
# Change this if your Gold-BookMap is elsewhere!
DEFAULT_BRIDGE = r"A:\gitHub\Gold-BookMap\ticks.csv"

# Allow override via env var
BRIDGE_FILE = os.getenv("BOOKMAP_BRIDGE_FILE") or os.getenv("NT_BRIDGE_FILE") or DEFAULT_BRIDGE
SYMBOL_ROOT = "MGC"
CSV_HEADER = "time,event,price,size,level,operation,instrument\n"

# ensure file + print where we write (so you see in BookMap log)
p = Path(BRIDGE_FILE)
print(f"[Bridge] Output file: {BRIDGE_FILE}", flush=True)
print(f"[Bridge] File exists? {p.exists()} Parent exists? {p.parent.exists()}", flush=True)
try:
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists() or p.stat().st_size == 0:
        p.write_text(CSV_HEADER, encoding="utf-8")
        print(f"[Bridge] Created header at {BRIDGE_FILE}", flush=True)
    else:
        print(f"[Bridge] Using existing file size {p.stat().st_size}", flush=True)
except Exception as e:
    print(f"[Bridge] FAILED to create file {BRIDGE_FILE}: {e}", flush=True)

lock = threading.Lock()
def write_line(t, ev, price, size, lvl, op, inst):
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
bbo = {}

def should(alias):
    return SYMBOL_ROOT in alias.upper()

def handle_subscribe_instrument(addon, alias, full_name, is_crypto, pips, size_mult, inst_mult, supported_features=None):
    if supported_features is None:
        supported_features = {}
    print(f"[Bridge] Sub {alias} pips={pips}", flush=True)
    if supported_features.get("isDelayed"):
        print(f"[Bridge] REJECT delayed {alias}", flush=True)
        return
    if not should(alias):
        print(f"[Bridge] Filter out {alias}", flush=True)
        return
    infos[alias] = (pips, size_mult)
    try:
        import bookmap as bm
        bm.subscribe_to_depth(addon, alias, 1)
        bm.subscribe_to_trades(addon, alias, 2)
    except Exception as e:
        print(f"[Bridge] sub fail {e}", flush=True)

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

def handle_trades(addon, alias, price_level, size_level, is_otc, is_bid, is_start, is_end, agg_id, pas_id):
    if not should(alias): return
    inf = infos.get(alias)
    if not inf: return
    pips, sm = inf
    try:
        cand = float(price_level)*float(pips)
        if 1000 < cand < 10000:
            price = cand
        else:
            price = float(price_level) if 1000 < float(price_level) < 10000 else cand
    except:
        price = float(price_level)
    size = float(size_level)/float(sm) if sm else float(size_level)
    side = "Sell" if is_bid else "Buy"
    write_line(now_iso(), "Last", price, size, -1, side, alias)

def handle_response(addon, req_id):
    print(f"[Bridge] resp {req_id}", flush=True)

if __name__ == "__main__":
    import bookmap as bm
    addon = bm.create_addon()
    bm.add_depth_handler(addon, handle_depth)
    bm.add_trades_handler(addon, handle_trades)
    bm.add_response_data_handler(addon, handle_response)
    bm.start_addon(addon, handle_subscribe_instrument, handle_unsubscribe_instrument)
    print("[Bridge] Running...", flush=True)
    bm.wait_until_addon_is_turned_off(addon)
