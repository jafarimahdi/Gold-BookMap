#!/usr/bin/env python3
"""tools/report_office_bundle.py -- Stitch ALL Company 2 (Report Office) outputs
into ONE self-contained page: data/report_<DATE>.html.

Read-only with respect to trading and scoring: this tool never imports robot
runtime modules and never recomputes a single grade. Every number and console
block embedded here comes verbatim from the artifacts already written by:
  - Stage 1: Toolkit & code test gate (daily_check.sh --check, pytest, rigs)
  - Stage 2: preflight.sh + audit_day.py (17 tests, scoreboard, AI veto, 4 diags)
             + tools/check_history.py
  - Stage 3: tail_probe.py (Truck Camera STEP1/STEP2/STEP3 latency attribution)
  - Stage 4: flow_probe.py (+ optional book_probe.py)
  - Stage 5: report_evening.sh (Desks 1-10 + Watchman + AI Cost + Reconciliation)

Idempotent: running it multiple times replaces its own HTML markers cleanly
without duplicating navigation links or sections.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

NAV_START = "<!-- COMPANY2_NAV_START -->"
NAV_END = "<!-- COMPANY2_NAV_END -->"
STYLE_START = "<!-- COMPANY2_STYLE_START -->"
STYLE_END = "<!-- COMPANY2_STYLE_END -->"
BUNDLE_START = "<!-- COMPANY2_BUNDLE_START -->"
BUNDLE_END = "<!-- COMPANY2_BUNDLE_END -->"

FALLBACK_SHELL_CSS = """
body{background:#0f1115;color:#e8e8ea;font:14px/1.55 system-ui,-apple-system,Segoe UI,sans-serif;margin:0}
.wrap{max-width:1280px;margin:0 auto;padding:22px 24px 60px}
h1{font-size:22px;margin:0 0 10px}
h2{font-size:17px;margin:0 0 8px}
.verdict{background:#171a21;border:1px solid #2a2d34;border-radius:8px;padding:10px 14px;margin:0 0 14px;
         font:13px/1.45 ui-monospace,Consolas,monospace;color:#d6d8dc}
nav{position:sticky;top:0;background:#0f1115;padding:10px 0;border-bottom:1px solid #2a2d34;margin-bottom:18px;z-index:5;display:flex;flex-wrap:wrap;gap:6px 14px}
nav a{color:#7fb2ff;text-decoration:none;font-size:13px;white-space:nowrap}
nav a:hover{text-decoration:underline}
section{margin:0 0 34px}
.sub{color:#9aa0aa;margin:0 0 10px;font-size:13px}
pre.console{background:#0b0d11;border:1px solid #2a2d34;border-radius:8px;padding:14px;
            overflow-x:auto;font:12px/1.5 ui-monospace,Consolas,monospace;color:#d6d8dc;max-height:1500px}
.cards{display:flex;gap:10px;flex-wrap:wrap;margin:6px 0 14px}
.card{background:#171a21;border:1px solid #2a2d34;border-radius:8px;padding:10px 14px;min-width:130px}
.card b{display:block;font-size:19px;line-height:1.15}
.card span{color:#9aa0aa;font-size:12px}
.card.good b{color:#5ad18a} .card.bad b{color:#ef6a6a}
details>summary{cursor:pointer;list-style:none}
details>summary::-webkit-details-marker{display:none}
details>summary h2{display:inline-block;margin:0 0 10px}
details>summary h2:before{content:'\\25B8 ';color:#6e7480}
details[open]>summary h2:before{content:'\\25BE ';color:#6e7480}
a{color:#7fb2ff}
.foot{color:#6e7480;font-size:12px;margin-top:30px;border-top:1px solid #2a2d34;padding-top:12px}
"""

EXTRA_OFFICE_CSS = """
<style>
.c2-toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:8px 0 16px}
.c2-btn{background:#1d2533;color:#e8e8ea;border:1px solid #35507a;border-radius:7px;
        padding:6px 13px;font-size:12.5px;cursor:pointer;text-decoration:none;display:inline-block}
.c2-btn:hover{background:#2b6cb0;border-color:#4b8ad8;color:#fff;text-decoration:none}
.c2-subdesk{margin:10px 0;border:1px solid #252932;border-radius:8px;padding:8px 12px;background:#13161d}
.c2-subdesk>summary{font-weight:600;font-size:13.5px;color:#c8d4ea;padding:2px 0}
.c2-subdesk>summary:hover{color:#7fb2ff}
.c2-subdesk pre.console{margin:8px 0 2px;max-height:none}
.c2-warn{color:#f2c96d}
.card.warn b{color:#f2c96d}
</style>
"""


def _esc(val: Any) -> str:
    return (
        str(val)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _read_text(path: Path) -> str:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        pass
    return ""


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        if path.exists():
            obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(obj, dict):
                return obj
    except Exception:
        pass
    return {}


def _parse_preflight_badge(text: str) -> Tuple[str, str, str]:
    if not text:
        return "not run", "preflight check", ""
    m = re.search(r"RESULT:\s*(\d+)\s*ok,\s*(\d+)\s*warnings?,\s*(\d+)\s*blocking", text, re.I)
    if not m:
        return "ran", "preflight check", ""
    ok_n, warn_n, blk_n = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if blk_n > 0:
        return f"{blk_n} BLOCKING", f"preflight ({ok_n} ok, {warn_n} warn)", "bad"
    if warn_n > 0:
        return f"{ok_n} OK / {warn_n}W", "preflight (0 blocking)", "warn"
    return f"{ok_n} OK", "preflight (0 warnings, 0 blocking)", "good"


def _parse_test_gate_badge(gate_json: Dict[str, Any], gate_txt: str) -> Tuple[str, str, str, bool]:
    checks = gate_json.get("checks") if isinstance(gate_json.get("checks"), list) else []
    if checks:
        total = len(checks)
        failed = [c for c in checks if c.get("status") == "FAIL"]
        passed = [c for c in checks if c.get("status") == "PASS"]
        skipped = [c for c in checks if c.get("status") == "SKIP"]
        if failed:
            names = ", ".join(str(c.get("name", "?")) for c in failed[:3])
            return f"{len(failed)} FAIL", f"tests ({names})", "bad", True
        if passed:
            sub = f"{len(passed)}/{total} test suites green"
            if skipped:
                sub += f" ({len(skipped)} skipped)"
            return "ALL PASS", sub, "good", False
        return "SKIPPED", "code/toolkit tests skipped", "", False
    if gate_txt:
        if "[FAIL]" in gate_txt:
            return "FAIL", "evening test gate", "bad", True
        if "[PASS]" in gate_txt or "[OK]" in gate_txt:
            return "ALL PASS", "evening test gate", "good", False
    return "not run", "run via report_evening.sh", "", False


def _parse_audit_badge(metrics: Dict[str, Any]) -> Tuple[str, str, str]:
    ct = metrics.get("counts") if isinstance(metrics.get("counts"), dict) else {}
    if not ct:
        return "not run", "17-test day audit", ""
    p = int(ct.get("PASS", 0) or 0)
    w = int(ct.get("WARN", 0) or 0)
    f = int(ct.get("FAIL", 0) or 0)
    na = int(ct.get("NA", 0) or 0)
    cls = "bad" if f > 0 else ("warn" if (w > 0 or na > 0) else "good")
    sub = f"17-test day audit (NA {na})" if na else "17-test day audit"
    return f"{p}P / {w}W / {f}F", sub, cls


def _parse_reconciliation_badge(paper_txt: str) -> Tuple[str, str, str, str]:
    if not paper_txt:
        return "not run", "3-desk reconciliation", "", ""
    rec_block = ""
    m_sec = re.search(
        r"(\[chief of staff\] RECONCILIATION.*?)(?:\n-{20,}|\n\[chief of staff\] today's outputs:|\Z)",
        paper_txt,
        re.S,
    )
    if m_sec:
        rec_block = m_sec.group(1).strip()
    m_ok = re.search(r"RECONCILED:\s*all three agree within\s*([0-9.]+)\s*pts", paper_txt)
    if m_ok:
        gap = m_ok.group(1)
        return f"OK ({gap} pts)", "Accountant = Geometer = Shadow", "good", rec_block
    m_flag = re.search(r"FLAG:\s*spread\s*([0-9.]+)\s*pts", paper_txt)
    if m_flag:
        return f"FLAG ({m_flag.group(1)} pts)", "3-desk spread mismatch", "bad", rec_block
    return "partial", "3-desk reconciliation", "warn", rec_block


def _parse_tail_badge(tail_txt: str) -> Tuple[str, str, str]:
    if not tail_txt:
        return "not run", "Truck Camera (tail_probe)", ""
    m = re.search(r"median\s+([0-9,]+)\s*ms.*?p95\s+([0-9,]+)\s*ms.*?max\s+([0-9,]+)\s*ms", tail_txt, re.I | re.S)
    if m:
        med_s, p95_s, max_s = m.group(1), m.group(2), m.group(3)
        try:
            med_v = int(med_s.replace(",", ""))
        except ValueError:
            med_v = 0
        cls = "good" if med_v <= 1500 else ("warn" if med_v <= 3000 else "bad")
        return f"med {med_s}ms", f"p95 {p95_s}ms | max {max_s}ms", cls
    return "ran", "Truck Camera (tail_probe)", ""


def _parse_shadow_badge(shadow_txt: str, paper_txt: str) -> Tuple[str, str, str]:
    src = shadow_txt or paper_txt
    if not src:
        return "not run", "Shadow walls vs fixed ruler", ""
    m = re.search(
        r"SHADOW \(walls\+gate\+trail\)\s+(\d+)\s+([+-]?[0-9.]+)\s+([+-]?[0-9.]+)\s+([0-9.]+)%.*?"
        r"BENCHMARK fixed ruler, same subset\s+\d+\s+([+-]?[0-9.]+)",
        src,
        re.S,
    )
    if m:
        n_tr, sh_pts, _per, wr, bm_pts = m.group(1), float(m.group(2)), m.group(3), m.group(4), float(m.group(5))
        diff = sh_pts - bm_pts
        cls = "good" if diff >= 0 else "bad"
        return f"{sh_pts:+.1f} vs {bm_pts:+.1f}", f"Shadow ({n_tr} tr, WR {wr}%) vs ruler", cls
    return "ran", "Shadow replay", ""


def _parse_xray_badge(xray_json: Dict[str, Any], xray_overview_txt: str) -> Tuple[str, str, str]:
    if isinstance(xray_json, dict) and xray_json:
        st = str(xray_json.get("overall_status") or "READY").upper()
        attn = xray_json.get("attention_items") if isinstance(xray_json.get("attention_items"), list) else []
        emb = int(xray_json.get("embedded_vault_count") or 0)
        cls = "good" if st == "CLEAN" else ("bad" if st == "CRITICAL" else "warn")
        sub = f"X-Ray ({len(attn)} findings, {emb} files in 1 .txt)"
        return st, sub, cls
    if xray_overview_txt:
        return "READY", "X-Ray full-app overview", "good"
    return "not run", "X-Ray (tools/xray_report.py)", ""


def _extract_xray_part1(raw_txt: str) -> str:
    if not raw_txt:
        return ""
    marker = "===== XRAY_VAULT_INDEX ====="
    idx = raw_txt.find(marker)
    return (raw_txt[:idx] if idx >= 0 else raw_txt).strip()


def _section_html(sid: str, title: str, inner_html: str, subtitle: str = "", open_default: bool = False) -> str:
    op = " open" if open_default else ""
    sub = f"<p class='sub'>{subtitle}</p>" if subtitle else ""
    return f"<section id='{sid}'><details{op}><summary><h2>{title}</h2></summary>{sub}{inner_html}</details></section>"


def _subdesk_html(title: str, content: str, open_default: bool = False) -> str:
    if not content:
        return ""
    op = " open" if open_default else ""
    return (
        f"<details class='c2-subdesk'{op}><summary>{_esc(title)}</summary>"
        f"<pre class='console'>{_esc(content)}</pre></details>"
    )


def collect_office_artifacts(root: Path, day_str: str) -> Dict[str, Any]:
    data_dir = root / "data"
    desks: List[Tuple[str, str]] = [
        ("Desk 1 — The Accountant (decision_ledger.py)", _read_text(data_dir / f"decision_ledger_{day_str}.txt")),
        ("Desk 2 — The Geometer (tf_study.py)", _read_text(data_dir / f"tf_study_{day_str}.txt")),
        ("Desk 3 — The Shadow (shadow_replay.py)", _read_text(data_dir / f"shadow_replay_{day_str}.txt")),
        ("Desk 9 — The POWER v2 Report Card (power_report_card.py)", _read_text(data_dir / f"power_report_card_{day_str}.txt")),
        ("Desk 10 — The Council Shadow (council_shadow.py)", _read_text(data_dir / f"council_shadow_{day_str}.txt")),
        ("Desk 4 — The TF Lens (tf_lens.py)", _read_text(data_dir / f"tf_lens_{day_str}.txt")),
        ("Desk 5 — The Fish Anatomist (fish_anatomy.py)", _read_text(data_dir / f"fish_anatomy_{day_str}.txt")),
        ("Desk 6 — The Universe Machine (tf_robot_replay.py)", _read_text(data_dir / f"tf_robot_{day_str}.txt")),
        ("Desk 7 — The Clocks Machine (clocks_verdict.py)", _read_text(data_dir / "clocks_verdict.txt")),
    ]
    return {
        "day": day_str,
        "gate_json": _read_json(data_dir / f"evening_test_gate_{day_str}.json"),
        "gate_txt": _read_text(data_dir / f"evening_test_gate_{day_str}.txt"),
        "preflight_txt": _read_text(data_dir / f"preflight_{day_str}.txt"),
        "metrics": _read_json(data_dir / f"day_metrics_{day_str}.json"),
        "paper_txt": _read_text(data_dir / f"evening_paper_{day_str}.txt"),
        "desks": [(t, txt) for t, txt in desks if txt],
        "shadow_txt": _read_text(data_dir / f"shadow_replay_{day_str}.txt"),
        "tail_txt": _read_text(data_dir / f"tail_probe_{day_str}.txt"),
        "flow_txt": _read_text(data_dir / f"flow_probe_{day_str}.txt"),
        "book_txt": _read_text(data_dir / f"book_probe_{day_str}.txt"),
        "signal_health_txt": _read_text(data_dir / f"signal_health_{day_str}.txt"),
        "signal_map_txt": _read_text(data_dir / f"signal_map_{day_str}.txt"),
        "xray_json": _read_json(data_dir / f"xray_{day_str}.json"),
        "xray_overview_txt": _extract_xray_part1(_read_text(data_dir / f"xray_{day_str}.txt")),
        "history_txt": _read_text(data_dir / f"history_summary_{day_str}.txt"),
        "has_index": (data_dir / "index.html").exists(),
        "has_history_html": (data_dir / "history_report.html").exists(),
        "has_suite_log": (data_dir / f"evening_suite_{day_str}.log").exists(),
        "has_xray_txt": (data_dir / f"xray_{day_str}.txt").exists(),
    }


def _build_chat_digest(art: Dict[str, Any], badges: Dict[str, Tuple[str, str, str]], rec_block: str) -> str:
    day_str = art["day"]
    lines = [
        f"=== GOLD-BOOKMAP EVENING SUMMARY ({day_str}) ===",
        f"Test Gate      : {badges['tests'][0]} ({badges['tests'][1]})",
        f"Setup Preflight: {badges['preflight'][0]} ({badges['preflight'][1]})",
        f"Day Audit (17) : {badges['audit'][0]}",
        f"Reconciliation : {badges['recon'][0]} ({badges['recon'][1]})",
        f"Shadow vs Ruler: {badges['shadow'][0]} ({badges['shadow'][1]})",
        f"Truck Camera   : {badges['tail'][0]} ({badges['tail'][1]})",
    ]
    verdict_line = str((art.get("metrics") or {}).get("verdict_line") or "").strip()
    if verdict_line:
        lines.append(f"Verdict        : {verdict_line}")
    if rec_block:
        lines.append("")
        lines.append(rec_block)
    if art.get("tail_txt"):
        tail_head = "\n".join(art["tail_txt"].splitlines()[:18]).strip()
        if tail_head:
            lines.append("")
            lines.append("--- Truck Camera (head) ---")
            lines.append(tail_head)
    return "\n".join(lines)


def render_office_bundle(art: Dict[str, Any]) -> Tuple[str, str]:
    """Return (nav_links_html, sections_html) for data/report_<DATE>.html."""
    day_str = art["day"]
    t_val, t_sub, t_cls, t_failed = _parse_test_gate_badge(art["gate_json"], art["gate_txt"])
    pf_val, pf_sub, pf_cls = _parse_preflight_badge(art["preflight_txt"])
    au_val, au_sub, au_cls = _parse_audit_badge(art["metrics"])
    rc_val, rc_sub, rc_cls, rec_block = _parse_reconciliation_badge(art["paper_txt"])
    tl_val, tl_sub, tl_cls = _parse_tail_badge(art["tail_txt"])
    sh_val, sh_sub, sh_cls = _parse_shadow_badge(art["shadow_txt"], art["paper_txt"])
    xr_val, xr_sub, xr_cls = _parse_xray_badge(art.get("xray_json") or {}, art.get("xray_overview_txt") or "")

    badges = {
        "tests": (t_val, t_sub, t_cls),
        "preflight": (pf_val, pf_sub, pf_cls),
        "audit": (au_val, au_sub, au_cls),
        "recon": (rc_val, rc_sub, rc_cls),
        "tail": (tl_val, tl_sub, tl_cls),
        "shadow": (sh_val, sh_sub, sh_cls),
        "xray": (xr_val, xr_sub, xr_cls),
    }

    nav_items: List[Tuple[str, str]] = [("office_hub", "Company 2 Hub")]
    sections: List[str] = []

    # 1. Top Company 2 Executive Hub (always open at top)
    digest_txt = _build_chat_digest(art, badges, rec_block)
    xray_overview_txt = art.get("xray_overview_txt") or ""
    badge_tuple_list = [
        badges["tests"],
        badges["preflight"],
        badges["audit"],
        badges["recon"],
        badges["shadow"],
        badges["tail"],
    ]
    if art.get("has_xray_txt") or xray_overview_txt:
        badge_tuple_list.append(badges["xray"])
    cards_html = "<div class='cards'>" + "".join(
        f"<div class='card {cls}'><b>{_esc(val)}</b><span>{_esc(sub)}</span></div>"
        for val, sub, cls in badge_tuple_list
    ) + "</div>"
    links_html = [
        "<button type='button' class='c2-btn' id='c2copybtn'>Copy Evening Summary for Chat</button>",
    ]
    if xray_overview_txt:
        links_html.append("<button type='button' class='c2-btn' id='c2copyxraybtn'>Copy X-Ray Overview for Chat</button>")
    links_html.append("<button type='button' class='c2-btn' onclick='window.print()'>Print / Save PDF</button>")
    if art["has_index"]:
        links_html.append("<a class='c2-btn' href='index.html'>All Audited Days (index.html)</a>")
    if art["has_history_html"]:
        links_html.append("<a class='c2-btn' href='history_report.html'>Multi-Day Trend Table</a>")
    suite_notes = []
    if art.get("has_xray_txt"):
        suite_notes.append(
            f"<b>Single-file chat share (no .zip needed):</b> drag <code>data/xray_{_esc(day_str)}.txt</code> into chat "
            "(contains Executive X-Ray + all embedded text/CSV/JSON artifacts)."
        )
    if art["has_suite_log"]:
        suite_notes.append(f"Master terminal transcript: <code>data/evening_suite_{_esc(day_str)}.log</code>.")
    if not suite_notes:
        suite_notes.append(f"Run <code>bash report_evening.sh {_esc(day_str)}</code> to refresh every Company 2 desk + X-Ray in one command.")
    suite_note = " ".join(suite_notes)
    xray_hidden = (
        f"<textarea id='c2xraydigest' style='position:fixed;left:-9999px;top:-9999px' readonly>{_esc(xray_overview_txt)}</textarea>"
        if xray_overview_txt
        else ""
    )
    copy_js = (
        "<textarea id='c2digest' style='position:fixed;left:-9999px;top:-9999px' readonly>"
        f"{_esc(digest_txt)}</textarea>"
        f"{xray_hidden}"
        "<script>(function(){function wire(bid,tid,msg){var b=document.getElementById(bid),t=document.getElementById(tid);"
        "if(!b||!t)return;b.onclick=function(){var v=t.value;"
        "function ok(){var old=b.textContent;b.textContent=msg;"
        "setTimeout(function(){b.textContent=old;},2000);}"
        "if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(v).then(ok).catch(function(){t.select();document.execCommand('copy');ok();});}"
        "else{t.select();document.execCommand('copy');ok();}};}"
        "wire('c2copybtn','c2digest','Copied Evening Summary!');"
        "wire('c2copyxraybtn','c2xraydigest','Copied X-Ray Overview!');})();</script>"
    )
    hub_inner = cards_html + f"<div class='c2-toolbar'>{''.join(links_html)}</div><p class='sub'>{suite_note}</p>" + copy_js
    sections.append(
        _section_html(
            "office_hub",
            "Company 2 &mdash; The Report Office (one-place evening overview)",
            hub_inner,
            "Every test, probe, replay desk, X-Ray funnel check, and audit check for this day in one document. "
            "Nothing below alters trading or scoring: each block embeds the exact output written by its tool.",
            open_default=True,
        )
    )

    # 1b. Desk 11 — The X-Ray (Full-App Funnel, Veto, AI, Basis/Spread, Hygiene & Manifest)
    if xray_overview_txt:
        nav_items.append(("xray_overview", "X-Ray Overview"))
        sections.append(
            _section_html(
                "xray_overview",
                "Desk 11 &mdash; The X-Ray (Step 1&ndash;5 Funnel, Vetoes, AI Routing, Basis/Spread &amp; State Hygiene)",
                f"<pre class='console' style='max-height:none'>{_esc(xray_overview_txt)}</pre>",
                f"Full plain-text X-Ray + embedded artifact vault saved at data/xray_{_esc(day_str)}.txt (ready to share directly as one .txt file).",
                open_default=True,
            )
        )

    # 2. Test & Setup Gate section (auto-opens if anything failed or blocked)
    gate_display_txt = art["gate_txt"]
    if not gate_display_txt and isinstance(art["gate_json"].get("checks"), list):
        gate_display_txt = "\n".join(
            f"[{c.get('status', '?')}] {c.get('name', '?')} ({c.get('seconds', 0)}s) {c.get('detail', '')}".strip()
            for c in art["gate_json"]["checks"]
        )
    if gate_display_txt or art["preflight_txt"]:
        nav_items.append(("office_gates", "Tests &amp; Setup Gate"))
        gate_parts = []
        if gate_display_txt:
            gate_parts.append(_subdesk_html("Toolkit & Code Test Gate (daily_check --check, pytest, rigs)", gate_display_txt, open_default=t_failed))
        if art["preflight_txt"]:
            gate_parts.append(_subdesk_html("Setup & Evidence Preflight (preflight.sh)", art["preflight_txt"], open_default=(pf_cls == "bad")))
        sections.append(
            _section_html(
                "office_gates",
                "Tests &amp; Setup Gate &mdash; did the testing system and files pass first?",
                "".join(gate_parts),
                "Verified before trusting day grades (.env values remain hidden).",
                open_default=(t_failed or pf_cls == "bad"),
            )
        )

    # 3. Evening Paper (Desks 1-10 + Watchman + AI Cost + Reconciliation)
    if art["paper_txt"] or art["desks"]:
        nav_items.append(("evening_paper", "Evening Paper (Desks 1-10)"))
        ep_parts: List[str] = []
        if rec_block:
            ep_parts.append(f"<pre class='console'>{_esc(rec_block)}</pre>")
        for idx, (d_title, d_txt) in enumerate(art["desks"]):
            ep_parts.append(_subdesk_html(d_title, d_txt, open_default=(idx < 3)))
        if art["paper_txt"]:
            ep_parts.append(
                _subdesk_html(
                    "Full Evening Paper Transcript (Watchman, AI Cost, Editor Verdict & all desks verbatim)",
                    art["paper_txt"],
                    open_default=(not art["desks"]),
                )
            )
        sections.append(
            _section_html(
                "evening_paper",
                "The Evening Paper &mdash; Desks 1&ndash;10 &amp; 3-Desk Reconciliation",
                "".join(ep_parts),
                "Accountant, Geometer, Shadow, POWER v2 Card, Council Shadow, TF Lens, Fish Anatomist, "
                "Universes (M1/M3/M5/M8/M10), Clocks (C1/C2/C3/C6), Watchman, AI Cost, and Reconciliation Law.",
                open_default=True,
            )
        )

    # 4. Truck Camera (tail_probe.py)
    if art["tail_txt"]:
        nav_items.append(("truck_camera", "Truck Camera (Latency)"))
        sections.append(
            _section_html(
                "truck_camera",
                "The Truck Camera &mdash; STEP1 / STEP2 / STEP3 pipeline latency (tail_probe.py)",
                f"<pre class='console' style='max-height:none'>{_esc(art['tail_txt'])}</pre>",
                "Full-pipeline latency distribution, slow-cycle owner attribution, and slowest cycles of the day.",
                open_default=(tl_cls == "bad"),
            )
        )

    # 5. Flow, Order-Book Spies & Signal Doctors (flow_probe / book_probe / signal_health / signal_map)
    if art["flow_txt"] or art["book_txt"] or art.get("signal_health_txt") or art.get("signal_map_txt"):
        nav_items.append(("flow_spies", "Flow &amp; Book Spies"))
        spy_parts = []
        if art.get("signal_health_txt"):
            spy_parts.append(_subdesk_html("Signal Team Doctor — detector votes vs silence & 4-team check (tools/signal_health.py)", art["signal_health_txt"], open_default=True))
        if art.get("signal_map_txt"):
            spy_parts.append(_subdesk_html("Wall Magnet & Barrier Test — do walls attract and hold? (tools/signal_map.py)", art["signal_map_txt"], open_default=False))
        if art["flow_txt"]:
            spy_parts.append(_subdesk_html("Print Flow Spy — sustained CVD tilt & LIFT (flow_probe.py)", art["flow_txt"], open_default=True))
        if art["book_txt"]:
            spy_parts.append(_subdesk_html("Order-Book Spy — MBO eOFI & near-touch LIFT (book_probe.py)", art["book_txt"], open_default=False))
        sections.append(
            _section_html(
                "flow_spies",
                "The Spies &amp; Signal Doctors &mdash; detector health, wall targets, print flow &amp; order-book tilt",
                "".join(spy_parts),
                "Read-only diagnostics checking L3 detector liveness, wall magnet/barrier behavior, and tape/book leans.",
                open_default=False,
            )
        )

    # 6. All Audited Days History Table (check_history.py)
    if art["history_txt"]:
        nav_items.append(("history_all", "All-Days History"))
        sections.append(
            _section_html(
                "history_all",
                "All Audited Days &mdash; multi-day history table (check_history.py)",
                f"<pre class='console' style='max-height:none'>{_esc(art['history_txt'])}</pre>",
                "One row per audited day across the repository's history.",
                open_default=False,
            )
        )

    nav_html = "".join(f"<a href='#{sid}'>{label}</a>" for sid, label in nav_items)
    return nav_html, "".join(sections)


def _strip_existing_markers(html: str) -> str:
    for start_m, end_m in (
        (NAV_START, NAV_END),
        (STYLE_START, STYLE_END),
        (BUNDLE_START, BUNDLE_END),
    ):
        pat = re.compile(re.escape(start_m) + r".*?" + re.escape(end_m), re.S)
        html = pat.sub("", html)
    return html


def inject_into_report_html(root: Path, day_str: str) -> Path:
    data_dir = root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    report_path = data_dir / f"report_{day_str}.html"
    existed_before = report_path.exists()
    existing_raw = report_path.read_text(encoding="utf-8", errors="replace") if existed_before else ""

    # Touch report_path first if needed so write_index links to report_<day>.html,
    # and build index.html BEFORE collecting artifacts so the index link button is
    # already present on the very first run (strict idempotency).
    if not existed_before:
        report_path.write_text("<!doctype html><html></html>", encoding="utf-8", newline="\n")
    try:
        tools_dir = str((root / "tools").resolve())
        if tools_dir not in sys.path:
            sys.path.insert(0, tools_dir)
        import dashboard  # type: ignore

        if hasattr(dashboard, "write_index"):
            dashboard.write_index(data_dir)
    except Exception:
        pass

    art = collect_office_artifacts(root, day_str)
    nav_links, bundle_sections = render_office_bundle(art)

    wrapped_nav = f"{NAV_START}{nav_links}{NAV_END}"
    wrapped_style = f"{STYLE_START}{EXTRA_OFFICE_CSS}{STYLE_END}"
    wrapped_bundle = f"{BUNDLE_START}{bundle_sections}{BUNDLE_END}"

    if existed_before:
        html = _strip_existing_markers(existing_raw)
        if "</head>" in html:
            html = html.replace("</head>", f"{wrapped_style}</head>", 1)
        else:
            html = wrapped_style + html
        if "<nav>" in html:
            html = html.replace("<nav>", f"<nav>{wrapped_nav}", 1)
        if "</nav>" in html:
            html = html.replace("</nav>", f"</nav>{wrapped_bundle}", 1)
        elif "<div class=\"wrap\">" in html:
            html = html.replace("<div class=\"wrap\">", f"<div class=\"wrap\"><nav>{wrapped_nav}</nav>{wrapped_bundle}", 1)
        else:
            html = html + wrapped_bundle
    else:
        verdict = _esc((art.get("metrics") or {}).get("verdict_line") or f"Gold-BookMap Company 2 Evening Report — {day_str}")
        html = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>Gold-BookMap - {_esc(day_str)} - full evening report</title>"
            f"<style>{FALLBACK_SHELL_CSS}</style>{wrapped_style}</head><body><div class='wrap'>"
            f"<h1>Gold-BookMap &mdash; full evening report &mdash; {_esc(day_str)}</h1>"
            f"<div class='verdict'>{verdict}</div>"
            f"<nav>{wrapped_nav}</nav>"
            f"{wrapped_bundle}"
            "<p class='foot'>One page, one command. Every block embeds the exact output written by "
            "its Report Office tool without recomputing.</p></div></body></html>"
        )

    report_path.write_text(html, encoding="utf-8", newline="\n")

    day_report_path = data_dir / f"day_report_{day_str}.html"
    if day_report_path.exists():
        try:
            dr_html = day_report_path.read_text(encoding="utf-8", errors="replace")
            marker = "<!-- report-office-master-link -->"
            banner_link = (
                f"{marker}<div style='background:#0f172a;color:#f8fafc;padding:10px 18px;"
                "border-bottom:2px solid #22c55e;font-family:system-ui,sans-serif;font-size:14px;"
                "display:flex;align-items:center;justify-content:space-between;gap:12px'>"
                "<span><b>Note:</b> This is the raw audit sub-page. The complete Company 2 Master Evening Report "
                "(Test Gate + Preflight + 17 Tests + Desks 1&ndash;11 X-Ray + Probes) is in "
                f"<code>report_{_esc(day_str)}.html</code>.</span>"
                f"<a href='report_{_esc(day_str)}.html' style='background:#22c55e;color:#052e16;"
                "font-weight:700;padding:6px 14px;border-radius:6px;text-decoration:none;white-space:nowrap'>"
                f"Open Full Report (report_{_esc(day_str)}.html) &rarr;</a></div>"
            )
            if marker not in dr_html and "<body>" in dr_html:
                dr_html = dr_html.replace("<body>", f"<body>{banner_link}", 1)
                day_report_path.write_text(dr_html, encoding="utf-8", newline="\n")
        except Exception:
            pass

    return report_path


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Embed all Company 2 Report Office outputs into data/report_<DATE>.html")
    ap.add_argument("--date", required=True, help="Target trading date YYYY-MM-DD")
    ap.add_argument("--dir", default=".", help="Project root directory (default: current directory)")
    ap.add_argument("--open", action="store_true", help="Open the resulting HTML report in the default browser")
    args = ap.parse_args(argv)

    root = Path(args.dir).resolve()
    out_path = inject_into_report_html(root, args.date)
    print(f"[report-office] unified master report ready: {out_path}")

    if args.open:
        try:
            import webbrowser

            webbrowser.open(out_path.resolve().as_uri())
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
