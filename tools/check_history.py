#!/usr/bin/env python3
"""
check_history.py -- the trend table across every audited day.

Reads data/day_metrics_YYYY-MM-DD.json (written by audit_day.py). Days audited
before that file existed are read back out of data/day_audit_*.txt, so nothing
you already generated is lost.

    python tools/check_history.py              # table + verdict
    python tools/check_history.py --html       # also write data/history_report.html
    python tools/check_history.py --days 14    # last 14 audited days

Exit code 0 = no day is failing the core checks; 1 = look at the RED rows.
"""
from __future__ import annotations

import argparse
import glob
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if not (BASE / "data").exists():                       # file lives in tools/ or root
    BASE = Path(__file__).resolve().parent
DATA = BASE / "data"

COLS = [("date", "DAY", 11), ("feed", "PRICE PRINTS", 13), ("mbo", "MBO", 8),
        ("snap", "SNAPS", 7), ("diary", "DIARY", 7), ("orders", "MT5", 5),
        ("gate", "GATE%", 6), ("wi", "WHAT-IF PTS", 12), ("wr", "WR%", 6),
        ("pf", "PF", 6), ("best", "BEST JUDGE", 22), ("worst", "WORST JUDGE", 22),
        ("med", "MED ms", 7), ("flags", "FLAGS", 24)]


def from_json(path: Path) -> dict:
    m = json.loads(path.read_text(encoding="utf-8"))
    wi = m.get("whatif") or {}
    j = m.get("judges") or {}
    grades = m.get("grades") or {}
    flags = []
    if m.get("version_mismatch"):
        flags.append("env")
    if any(g == "FAIL" for g in grades.values()):
        flags.append(f"{sum(1 for g in grades.values() if g == 'FAIL')}fail")
    if any(g == "NA" for g in grades.values()):
        flags.append(f"{sum(1 for g in grades.values() if g == 'NA')}nodata")
    if int(m.get("feed_prints", 0)) < 1000:
        flags.append("blind")
    return {
        "date": m.get("date", path.stem.replace("day_metrics_", "")),
        "feed": f"{int(m.get('feed_prints', 0)):,}", "mbo": f"{int(m.get('feed_mbo', 0)):,}",
        "snap": str(m.get("decisions", 0)), "diary": str(m.get("diary_records", 0)),
        "orders": str(m.get("orders_to_mt5", 0)),
        "gate": f"{float(m.get('gate_used', 0) or 0):.0f}",
        "wi": (f"{wi['total_pts']:+.1f}" if wi else "-"),
        "wr": (f"{wi['win_rate']:.0f}" if wi else "-"),
        "pf": (f"{wi['profit_factor']:.2f}" if wi else "-"),
        "best": j.get("best", "-"), "worst": j.get("worst", "-"),
        "med": str(int(m.get("median_ms", 0) or 0)), "flags": ",".join(flags) or "-",
    }


def from_text(path: Path) -> dict:
    """Old text reports -> the same columns (used when no JSON was saved)."""
    t = path.read_text(encoding="utf-8", errors="ignore")
    d = date.fromisoformat(path.stem.replace("day_audit_", ""))

    def find(pat, default="-"):
        m = re.search(pat, t)
        return m.group(1) if m else default

    feed = find(r"\[\w+\]\s+TEST\s+2\s+FEED CHECK[\s\S]{0,200}?([\d,]+) trade prints", "0")
    snap = find(r"decisions:\s*(\d+)", find(r"decisions_log\.csv\s+\((\d+) rows", "0"))
    orders = find(r"exec_status: \[\('SKIPPED', (\d+)\)\]", "-")
    return {"date": f"{d:%Y-%m-%d}", "feed": feed, "mbo": "-", "snap": snap, "diary": "-",
            "orders": orders, "gate": "-", "wi": "-", "wr": "-", "pf": "-",
            "best": "-", "worst": "-", "med": "-", "flags": "no json (re-audit this day)"}


def load(days_limit: int = 0):
    rows, seen = [], set()
    for f in sorted(glob.glob(str(DATA / "day_metrics_*.json"))):
        try:
            r = from_json(Path(f))
            rows.append(r); seen.add(r["date"])
        except Exception:
            continue
    for f in sorted(glob.glob(str(DATA / "day_audit_*.txt"))):
        p = Path(f)
        dd = p.stem.replace("day_audit_", "")
        if dd not in seen:
            try:
                rows.append(from_text(p))
            except Exception:
                continue
    rows.sort(key=lambda r: r["date"])
    if days_limit:
        rows = rows[-days_limit:]
    return rows


def render(rows):
    head = " ".join(f"{t:>{w}}" if k not in ("date",) else f"{t:<{w}}" for k, t, w in COLS)
    out = [head, "-" * len(head)]
    for r in rows:
        cells = []
        for k, _t, w in COLS:
            v = str(r.get(k, "-"))
            if k in ("best", "worst"):
                v = v[:w]
            cells.append(f"{v:>{w}}" if k != "date" else f"{v:<{w}}")
        out.append(" ".join(cells))
    return "\n".join(out)


def html(rows) -> str:
    style = ("body{font:13px/1.5 -apple-system,Segoe UI,Roboto,Arial;background:#0f1117;color:#e6e6ec;"
             "padding:22px;margin:0}h1{font-size:18px}table{border-collapse:collapse;width:100%;background:#151824;"
             "border-radius:10px;overflow:hidden}th,td{padding:7px 9px;border-bottom:1px solid #232838;"
             "text-align:right}td:first-child,th:first-child{text-align:left}th{background:#1a1f30;color:#aeb4c4;"
             "font-size:11px;text-transform:uppercase;letter-spacing:.05em}"
             ".pos{color:#39d98a}.neg{color:#ff6b6b}.dim{color:#8b93a7}")
    hd = "".join(f"<th>{t}</th>" for _k, t, _w in COLS)
    body = ""
    for r in rows:
        def cls(v, good_up=0.0):
            try:
                f = float(str(v).replace(",", "").replace("+", ""))
            except ValueError:
                return "dim"
            return "pos" if f >= good_up else "neg"
        body += ("<tr><td><a href='day_report_" + r["date"] + ".html'>" + r["date"] + "</a></td><td>" + r["feed"] + "</td><td>" + r["mbo"] +
                 "</td><td>" + r["snap"] + "</td><td>" + r["diary"] + "</td><td>" + r["orders"] +
                 "</td><td>" + r["gate"] + "</td><td class='" + cls(r["wi"]) + "'>" + r["wi"] +
                 "</td><td class='" + cls(r["wr"], 50) + "'>" + r["wr"] + "</td><td class='" +
                 cls(r["pf"], 1.0) + "'>" + r["pf"] + "</td><td class=dim>" + r["best"] +
                 "</td><td class=dim>" + r["worst"] + "</td><td>" + r["med"] + "</td><td class=neg>" +
                 r["flags"] + "</td></tr>")
    n = len(rows)
    tot_wi = 0.0
    for r in rows:
        try:
            tot_wi += float(r["wi"])
        except ValueError:
            pass
    _dash_style, _dash_js = "", ""
    try:
        import sys as _sys, os as _os
        for _c in (_os.path.dirname(_os.path.abspath(__file__)),
                  _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))):
            if _c not in _sys.path:
                _sys.path.insert(0, _c)
        import dashboard as _d
        _dash_style, _dash_js = _d._STYLE, _d._JS
    except Exception:
        pass
    head2 = ""
    if _dash_style:
        style = _dash_style + "body{padding:0}h1{font-size:19px;margin:0 0 4px}.wrap{max-width:1320px;margin:0 auto;padding:20px 24px 60px}"
        head2 = ("<script>" + _dash_js + "</script>")
        extra = ('<div class=bar><span class=lg>GOLD-BOOKMAP</span><span class=dy>Audited days</span>'
                 '<span class=sp></span>'
                 '<button class=btn onclick="window.print()">Print / PDF</button></div>'
                 '<div class=wrap>')
        closer = "</div></body></html>"
    else:
        extra, closer = "", "</body></html>"
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Robot history</title>"
            "<style>" + style + "</style>" + head2 + "</head><body>" + extra +
            f"<h1>Gold-BookMap - audited days ({n})</h1><div class=dim>click a day to open its full report "
            f"&middot; cumulative what-if {tot_wi:+.1f} pts &middot; generated {datetime.now():%Y-%m-%d %H:%M}</div>"
            f"<div class=card><table><tr>{hd}</tr>{body}</table></div>" + closer)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", action="store_true", help="also write data/history_report.html")
    ap.add_argument("--days", type=int, default=0, help="only the last N audited days")
    args = ap.parse_args()

    rows = load(args.days)
    print("=" * 150)
    print(f" GOLD-BOOKMAP HISTORY  ({len(rows)} audited day(s))".center(150, " "))
    print("=" * 150)
    if not rows:
        print(" No audited days yet. Run:  python audit_day.py --date YYYY-MM-DD")
        print(" Then this table fills up automatically - one row per day, forever.")
        return 1
    print(render(rows))
    print()
    flagged = [r["date"] for r in rows if r["flags"] != "-"]
    if flagged:
        print(f" days needing attention: {', '.join(flagged)}")
    wins = [r for r in rows if r["wi"] not in ("-",)]
    if wins:
        pos = sum(1 for r in wins if float(r["wi"]) > 0)
        print(f" what-if positive on {pos}/{len(wins)} audited days")
    if args.html:
        out = DATA / "history_report.html"
        out.write_text(html(rows), encoding="utf-8")
        print(f" [saved] {out}")
    return 0 if not any("fail" in r["flags"] or "blind" in r["flags"] for r in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
