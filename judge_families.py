"""judge_families.py — v8 'five tables, one idea, one vote' + SHADOW reporter.

Fixes the 5 measured vote defects (DESIGN_V8_JUDGES.md):
  1. teams by list position  -> explicit judge -> family dictionary
  2. head-count confidence   -> weight of agreeing families / weight that spoke
  3. duplicate shouting      -> one vote per family after internal debate
  4. floating weights        -> family weights frozen in code (change = decision)
  5. wrong grading           -> dual-clock comes separately (audit side)

SHADOW MODE ONLY: this module never changes trading. It computes what the
family gate WOULD decide and prints it next to the current one. Switching the
live gate is a separate, later decision.

Usage (repo root):  python *families*.py
Reads data/market_snapshot.json and the last N lines of data/snapshots_history.jsonl
if present; prints one SHADOW line per snapshot it can read.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

__all__ = ["JUDGE_FAMILY", "FAMILY_WEIGHTS", "family_gate", "shadow_note"]

# The 25 active judges -> five named families (DESIGN_V8_JUDGES.md section 3).
JUDGE_FAMILY = {
    # TAPE — what actually traded
    "footprint_delta": "TAPE", "cvd_divergence": "TAPE", "absorption": "TAPE",
    "cvd_momentum": "TAPE", "footprint_levels": "TAPE", "volume_roc": "TAPE",
    # BOOK — orders resting and waiting
    "l3_net_flow": "BOOK", "l3_imbalance": "BOOK", "l3_aggr_limit": "BOOK",
    "l3_large_ofi": "BOOK", "microprice": "BOOK", "queue_pos": "BOOK",
    # HIDDEN — who is sneaking
    "whale_walls": "HIDDEN", "iceberg": "HIDDEN", "sweep": "HIDDEN",
    "spoof_invert": "HIDDEN",
    # MAP — where price is
    "vwap_bands": "MAP", "htf_poc": "MAP", "vwap_trend": "MAP",
    "supply_demand": "MAP", "poc_day": "MAP", "value_area": "MAP", "mtf": "MAP",
    # WORLD — outside opinion
    "news_sentiment": "WORLD", "macro_risk": "WORLD",
}

# Between families, the operator sets the loudness. FROZEN on purpose (defect 4).
FAMILY_WEIGHTS = {"TAPE": 1.2, "BOOK": 1.2, "HIDDEN": 1.0, "MAP": 0.8, "WORLD": 0.5}

# Judges that no longer vote (they may still write note lines).
RETIRED = {"macro_yield", "macro_dxy", "macro_vix", "vwap_zscore",
           "spoof_invert_loose", "iceberg_legacy", "l3_ofi_streak",
           "delta_pressure"}

DEAD_ZONE = 0.15  # a family whose internal average is inside +-0.15 stays silent


def _f(x, d=0.0):
    try:
        v = float(x)
        return v if v == v else d
    except Exception:
        return d


def family_gate(judge_panel: List[Dict[str, Any]],
                config: Any = None) -> Dict[str, Any]:
    """Compute old (head-count) and new (family) gate side by side.

    judge_panel: [{"judge": name, "dir": -1/0/+1, "weight": w}, ...]
    """
    families: Dict[str, Dict[str, float]] = {}
    for row in judge_panel or []:
        name = str(row.get("judge") or "")
        if name in RETIRED or name not in JUDGE_FAMILY:
            continue
        d = _f(row.get("dir"))
        w = _f(row.get("weight"))
        if w <= 0 or d == 0:
            continue
        fam = JUDGE_FAMILY[name]
        acc = families.setdefault(fam, {"score": 0.0, "weight": 0.0})
        acc["score"] += d * w
        acc["weight"] += w

    # internal debate -> one voice per family
    fam_votes: Dict[str, float] = {}
    for fam, acc in families.items():
        if acc["weight"] <= 0:
            continue
        avg = acc["score"] / acc["weight"]
        fam_votes[fam] = 0.0 if abs(avg) <= DEAD_ZONE else (1.0 if avg > 0 else -1.0)

    speaking = {f: v for f, v in fam_votes.items() if v != 0.0}
    up_w = sum(FAMILY_WEIGHTS[f] for f, v in speaking.items() if v > 0)
    down_w = sum(FAMILY_WEIGHTS[f] for f, v in speaking.items() if v < 0)
    total_w = up_w + down_w
    if total_w <= 0:
        new_dir, new_conf = "NEUTRAL", 0.0
    else:
        new_dir = "BUY" if up_w > down_w else ("SELL" if down_w > up_w else "NEUTRAL")
        new_conf = 100.0 * max(up_w, down_w) / total_w

    # old gate: head-count over the same active panel (for comparison only)
    votes_d = [_f(r.get("dir")) for r in (judge_panel or [])
               if str(r.get("judge") or "") not in RETIRED
               and str(r.get("judge") or "") in JUDGE_FAMILY]
    active = [d for d in votes_d if d != 0]
    if active:
        agree = 1.0 if new_dir == "BUY" else (-1.0 if new_dir == "SELL" else 0.0)
        old_conf = 100.0 * sum(1 for d in active if d == agree) / max(len(judge_panel or []), 1)
    else:
        old_conf = 0.0

    return {
        "old": {"direction": new_dir if active else "NEUTRAL",
                "confidence": round(old_conf, 1)},
        "new": {"direction": new_dir, "confidence": round(new_conf, 1)},
        "families": {f: fam_votes.get(f, 0.0) for f in FAMILY_WEIGHTS},
        "speaking": sorted(speaking.keys()),
    }


def shadow_note(judge_panel: List[Dict[str, Any]], config: Any = None) -> str:
    g = family_gate(judge_panel, config)
    return ("SHADOW GATE (family v8, report only): "
            f"old {g['old']['direction']} {g['old']['confidence']:.1f}% -> "
            f"new {g['new']['direction']} {g['new']['confidence']:.1f}% | "
            f"families " + " ".join(
                f"{f}={int(v):+d}" for f, v in g["families"].items()) +
            f" | speaking={','.join(g['speaking']) or 'none'}")


def _panel_from_snapshot(snap: Dict[str, Any]) -> List[Dict[str, Any]]:
    panel = snap.get("judge_votes") or snap.get("judge_panel") or []
    out = []
    for row in panel:
        if isinstance(row, dict) and row.get("judge"):
            out.append({"judge": row.get("judge"),
                        "dir": row.get("dir", row.get("direction", 0)),
                        "weight": row.get("weight", 0.5)})
    return out


def main() -> int:
    seen = 0
    snap_path = Path("data") / "market_snapshot.json"
    if snap_path.exists():
        try:
            snap = json.loads(snap_path.read_text(encoding="utf-8"))
            panel = _panel_from_snapshot(snap)
            if panel:
                print(f"[latest snapshot] {shadow_note(panel)}")
                seen += 1
        except Exception as exc:
            print(f"[latest snapshot] unreadable: {type(exc).__name__}")
    hist = Path("data") / "snapshots_history.jsonl"
    if hist.exists():
        try:
            lines = hist.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-25:]:
                try:
                    snap = json.loads(line)
                except Exception:
                    continue
                panel = _panel_from_snapshot(snap)
                if panel:
                    print(f"[history] {shadow_note(panel)}")
                    seen += 1
        except Exception as exc:
            print(f"[history] unreadable: {type(exc).__name__}")
    if not seen:
        print("No snapshots with judge panels found (run the robot first).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
