#!/usr/bin/env python3
"""
probe_l3_flow.py -- O23 PROBE: is the L3 order-event stream FROZEN? (2026-10-15)
================================================================================
SUSPICION (from the owner's loop, 20:33-21:10): "OFI (L3): -464", "Limit: 784"
and "Large order events: 3" printed the SAME numbers for 40+ minutes while
their neighbors moved every cycle. Those fields come from
market_data["order_events"] -> a fresh Level3OrderBookAnalyzer per cycle.
If the output is frozen, the INPUT slice is frozen -- somewhere between
mbo.csv (the raw file), the provider's mbo tailer, and step 2.

THIS PROBE IS 100% READ-ONLY. It changes nothing, writes nothing, imports
nothing from the robot. Run it while the robot is RUNNING, from the project
root (takes ~80 seconds):

    python probe_l3_flow.py

Then paste the WHOLE output to the assistant. It answers:
  [1] Is step 2's saved snapshot changing between cycles? (and are the L3
      fields inside it frozen?)
  [2] Is the RAW mbo.csv file still growing with fresh timestamps?
      -> if [2] FRESH but [1] FROZEN, the bug is in the provider/adapter
         layer, and section [4] shows the exact live code to fix.
  [3] Does the live provider/step1 match the assistant's workshop copies?
      (hashes) -> tells the assistant whether workshop anchors are safe.
  [4] The live code sections that assemble order_events (for the fix).
"""
import hashlib
import json
import time
from pathlib import Path

ROOT = Path.cwd()
SNAP = ROOT / "data" / "market_snapshot.json"
MBO = ROOT / "mbo.csv"
SLEEP_SEC = 75

# the assistant's workshop copies (normalized: \r stripped), 2026-10-15
WORKSHOP_HASHES = {
    "bookmap_bridge_provider.py": "c9ecd5a5cd121a90",
    "step1_data_acquisition.py": "4ed80a078fd27697",
}


def banner(text):
    print("\n" + "=" * 78)
    print(text)
    print("=" * 78)


def find_l3(obj, path=""):
    """Recursively dig for the frozen suspects in the saved snapshot."""
    found = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            if k in ("ofi", "limit_orders", "market_orders",
                     "large_order_events", "order_book_imbalance",
                     "aggressive_buys", "aggressive_sells"):
                found[p] = v
            elif isinstance(v, (dict, list)):
                found.update(find_l3(v, p))
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:5]):
            found.update(find_l3(v, f"{path}[{i}]"))
    return found


def read_snapshot():
    try:
        return json.loads(SNAP.read_text(encoding="utf-8", errors="replace"))
    except Exception as e:
        return {"__error__": f"{type(e).__name__}: {e}"}


def mbo_state():
    try:
        size = MBO.stat().st_size
        with MBO.open("rb") as f:
            f.seek(max(0, size - 400))
            tail = f.read().decode("utf-8", errors="replace").strip().splitlines()
        last = tail[-1] if tail else ""
        return {"size": size, "last_line": last[:160]}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def show_section(fname, needle, before=6, after=30, cap=70):
    p = ROOT / fname
    if not p.is_file():
        print(f"  [ -- ] {fname} not found")
        return
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    hits = [i for i, ln in enumerate(lines) if needle in ln]
    if not hits:
        print(f"  [ -- ] '{needle}' not found in {fname}")
        return
    i = hits[0]
    lo, hi = max(0, i - before), min(len(lines), i + after)
    if hi - lo > cap:
        hi = lo + cap
    print(f"  --- {fname} lines {lo + 1}..{hi} (around '{needle}', first hit) ---")
    for n in range(lo, hi):
        print(f"  {n + 1:5d}: {lines[n]}")


def main():
    banner("O23 PROBE - L3 order-event freshness (READ-ONLY) - v1.0")
    print(f"cwd: {ROOT}")

    # [1] + [2] first readings
    snap1 = read_snapshot()
    mbo1 = mbo_state()
    l3_1 = find_l3(snap1)
    print("\n[1] STEP 2 SNAPSHOT (data/market_snapshot.json) - first read:")
    if not l3_1:
        print("  no L3-suspect fields found inside the snapshot (they may live")
        print("  only in the console print); top-level keys:")
        for k in list(snap1.keys())[:40]:
            print(f"    - {k}")
    else:
        for k, v in l3_1.items():
            print(f"    {k} = {v}")
    print("\n[2] RAW mbo.csv - first read:")
    for k, v in mbo1.items():
        print(f"    {k}: {v}")

    print(f"\n    ... sleeping {SLEEP_SEC}s (the robot keeps running) ...")
    time.sleep(SLEEP_SEC)

    snap2 = read_snapshot()
    mbo2 = mbo_state()
    l3_2 = find_l3(snap2)
    print("\n[1b] SNAPSHOT - second read:")
    for k, v in l3_2.items():
        print(f"    {k} = {v}")
    print("\n[2b] RAW mbo.csv - second read:")
    for k, v in mbo2.items():
        print(f"    {k}: {v}")

    banner("PROBE VERDICTS")
    if "error" in mbo1:
        print("[2] mbo.csv UNREADABLE - paste this to the assistant.")
    elif mbo2.get("size", 0) > mbo1.get("size", 0):
        print(f"[2] mbo.csv FRESH: +{mbo2['size'] - mbo1['size']} bytes in {SLEEP_SEC}s"
              " -> the RAW feed is alive.")
    else:
        print("[2] mbo.csv NOT GROWING -> the raw feed itself stopped"
              " (check the BookMap side/exporter).")
    if l3_1 and l3_2:
        frozen = [k for k in l3_1 if k in l3_2 and l3_1[k] == l3_2[k]]
        moved = [k for k in l3_1 if k in l3_2 and l3_1[k] != l3_2[k]]
        if moved:
            print(f"[1] SNAPSHOT L3 fields MOVED: {moved} -> NOT frozen tonight"
                  " (O23 may be intermittent - paste anyway).")
        elif frozen:
            print(f"[1] SNAPSHOT L3 fields FROZEN across {SLEEP_SEC}s: {frozen}")
            print("    -> O23 CONFIRMED at step 2 output. With [2] fresh, the")
            print("       freeze is in the provider/adapter layer (section [4]).")
    else:
        print("[1] snapshot carried no L3 fields - verdict deferred to [4].")

    banner("[3] LIVE CODE vs WORKSHOP COPIES (normalized sha256, first 16)")
    for fname, want in WORKSHOP_HASHES.items():
        p = ROOT / fname
        if not p.is_file():
            print(f"  {fname}: not found")
            continue
        got = hashlib.sha256(p.read_bytes().replace(b"\r", b"")).hexdigest()[:16]
        verdict = "SAME (workshop copy is trustworthy)" if got == want else "DIFFERENT (live file has drifted)"
        print(f"  {fname}: {got} vs workshop {want} -> {verdict}")

    banner("[4] THE LIVE CODE THAT ASSEMBLES order_events (for the assistant)")
    show_section("bookmap_bridge_provider.py", "self.order_events = combined",
                 before=38, after=6)
    print()
    show_section("bookmap_bridge_provider.py", "st.mbo_events.append",
                 before=18, after=10)
    print()
    show_section("bookmap_bridge_provider.py", "def _prune_old_ticks",
                 before=2, after=28)
    print()
    show_section("step1_data_acquisition.py", "order_events",
                 before=3, after=6)

    banner("DONE - paste this WHOLE output to the assistant.")


if __name__ == "__main__":
    main()
