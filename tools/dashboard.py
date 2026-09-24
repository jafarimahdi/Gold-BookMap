"""Visual layer of the day auditor: CSS, per-day dashboard HTML, copy text, index page.

Everything here is stdlib-only and produces SELF-CONTAINED html files: no CDN, no
fonts, no scripts loaded from anywhere. A report can be emailed, copied to a USB
stick or opened from a network drive and still look identical. The only JS inside
is ~15 lines for the "copy for chat" button, tab switching and localStorage notes.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

_STYLE = """
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 620px at 15% -10%,#17203a 0%,#0c0f17 55%) fixed;
 color:#e7ebf5;font:14px/1.55 -apple-system,"Segoe UI",Roboto,Arial,sans-serif}
.bar{position:sticky;top:0;z-index:9;display:flex;flex-wrap:wrap;gap:10px;align-items:center;
 padding:12px 22px;background:rgba(9,12,20,.92);border-bottom:1px solid #1e2540}
.bar .lg{font-weight:800;letter-spacing:.14em;font-size:11px;color:#7d87a3}
.bar .dy{font-weight:700;font-size:15px}
.bar .sp{flex:1}
.btn{background:#1b2440;border:1px solid #2e3a5e;color:#dbe2f5;border-radius:9px;padding:7px 13px;
 font-size:12.5px;font-weight:600;cursor:pointer}
.btn:hover{background:#25304f}
.btn.ok{border-color:#2c6a45;color:#7ef0ac}
.wrap{max-width:1220px;margin:0 auto;padding:20px 22px 60px}
h2{font-size:11.5px;text-transform:uppercase;letter-spacing:.09em;color:#8e98b4;margin:26px 2px 10px}
.card{background:linear-gradient(180deg,#141a2b,#111624);border:1px solid #1f2740;border-radius:14px;padding:16px 18px}
.banner{border-radius:14px;padding:18px 20px;border:1px solid;margin:4px 0 14px;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:24px;align-items:center}
.b-GOOD{background:linear-gradient(180deg,#10281c,#0f1b17);border-color:#1e5136}
.b-LOOK{background:linear-gradient(180deg,#2a2410,#191609);border-color:#5d4a15}
.b-BAD{background:linear-gradient(180deg,#2b1315,#1b0f10);border-color:#6d2429}
.b-NONE{background:linear-gradient(180deg,#161b2a,#111523);border-color:#25304b}
.b-txt{min-width:0}
.b-state{font-size:24px;font-weight:800;letter-spacing:-.01em}
.b-msg{color:#b9c2d8;margin-top:4px;max-width:70ch}
.ringrow{display:flex;gap:14px;flex-wrap:wrap;justify-content:flex-end}
.meta{display:flex;flex-wrap:wrap;gap:7px;margin:14px 0 0}
.chip{background:#161d31;border:1px solid #232d49;border-radius:999px;padding:4px 11px;font-size:11.5px;color:#a8b2ca}
.chip b{color:#e7ebf5;font-weight:600}
.pill{display:inline-block;padding:2px 9px;border-radius:999px;font-size:10.5px;font-weight:800;letter-spacing:.05em}
.p-PASS{background:#12351f;color:#4ee08a}.p-WARN{background:#3a2e10;color:#ffc857}
.p-FAIL{background:#3a1618;color:#ff7b7b}.p-NA{background:#20263a;color:#8b93a7}
.row{display:flex;gap:14px;flex-wrap:wrap;align-items:stretch}
.row>.card{flex:1 1 340px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px}
.kpi{background:linear-gradient(180deg,#141a2b,#111624);border:1px solid #1f2740;border-radius:12px;padding:11px 13px;
 transition:transform .12s,border-color .12s}
.kpi:hover{transform:translateY(-2px);border-color:#37456e}
.kpi span{font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;color:#8b95b0;display:block}
.kpi b{display:block;font-size:21px;margin-top:3px;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.kpi i{font-style:normal;font-size:11px;color:#7c86a2}
.pos{color:#4ee08a}.neg{color:#ff8686}.warn{color:#ffc857}.dim{color:#8b95b0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(345px,1fr));gap:10px}
.tst{background:#131a2a;border:1px solid #1f2740;border-left:3px solid #33405f;border-radius:11px;padding:11px 13px;cursor:pointer}
.tst:hover{background:#18203a}
.tst.PASS{border-left-color:#2fbf71}.tst.WARN{border-left-color:#e0a53a}
.tst.FAIL{border-left-color:#e5544f}.tst.NA{border-left-color:#4a5578}
.tst .hd{display:flex;gap:8px;align-items:baseline}
.tst .no{color:#6e7793;font-size:11px;font-weight:700}
.tst .nm{font-weight:700;font-size:13px}
.tst .hl{color:#9aa4bd;font-size:12px;margin-top:3px}
.tst .bd{display:none;margin-top:9px;border-top:1px dashed #242e4a;padding-top:8px}
.tst.open .bd{display:block}
.tst .bd div{font-size:12px;color:#c2cadf;margin:0 0 4px;white-space:pre-wrap;word-break:break-word}
table{border-collapse:collapse;width:100%}
td.acc{width:230px}td.acc>div{display:flex;align-items:center;gap:8px}td.acc b{width:52px;flex:0 0 52px}td.acc .bar2{flex:1 1 auto;height:9px;min-width:60px}td.acc .bar2 u{display:none}td.acc i{display:block;font-style:normal;font-size:10.5px;color:#7c86a2}
th,td{padding:6px 9px;border-bottom:1px solid #1e2540;text-align:left;font-size:12.5px}
th{color:#8e98b4;font-size:10.5px;text-transform:uppercase;letter-spacing:.06em;font-weight:600}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.bar2{position:relative;background:#1b2238;border-radius:5px;height:13px;min-width:70px;overflow:hidden}
.bar2 i{position:absolute;left:0;top:0;bottom:0;border-radius:5px;display:block}
.bar2 u{position:absolute;right:5px;top:0;font-size:10px;line-height:13px;text-decoration:none;color:#cfd6ea}
.heat{display:grid;grid-template-columns:44px repeat(24,minmax(18px,1fr));gap:3px;align-items:center;margin-top:6px;overflow-x:auto}
.heat .hl2{font-size:10.5px;color:#8b95b0;text-align:right;padding-right:4px}
.heat .cell{height:19px;border-radius:4px;background:#141a2a;border:1px solid #1c2440}
.heat .cap{font-size:9.5px;color:#6e7793;text-align:center;padding-top:2px}
.sess{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.sess div{flex:1 1 130px;background:#101527;border:1px solid #1e2540;border-radius:9px;padding:8px 10px;font-size:12px}
.sess b{display:block;font-size:16px}
textarea.note{background:#0f1424;border:1px solid #2a3350;color:#e7ebf5;font:12.5px/1.5 sans-serif}
.note{outline:1px dashed #3a4668;border-radius:10px;padding:10px 12px;margin-top:12px;background:#0f1424;font-size:12.5px;color:#b9c2d8}
.checklist li{margin:0 0 7px;font-size:12.5px;color:#c2cadf}
.checklist{padding-left:18px;margin:6px 0 0}
.checklist .done{color:#4f6a5c;text-decoration:line-through}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin:2px 0 14px;padding:4px 0 10px;border-bottom:1px solid #161d31}
.tab{background:#141b2d;border:1px solid #232d49;color:#a8b2ca;border-radius:999px;padding:5px 14px;font-size:12px;cursor:pointer}
.tab.on{background:#1d2c52;border-color:#3a5194;color:#eaf0ff;font-weight:700}
.sec{display:none}.sec.on{display:block}
.rawbox{position:relative}
pre.raw{margin:0;max-height:520px;overflow:auto;background:#0b0f1b;border:1px solid #1c2440;border-radius:10px;
 padding:14px;font:12px/1.45 ui-monospace,Consolas,"Courier New",monospace;white-space:pre;color:#c9d2e8}
pre.raw:focus{outline:1px solid #3a5194}
.hidespace{position:absolute;width:1px;height:1px;overflow:hidden;opacity:0;left:-9999px;top:0}
a{color:#8fb6ff}
.daylink{display:block;text-decoration:none;color:inherit;background:#131a2a;border:1px solid #1f2740;
 border-radius:12px;padding:12px 14px;flex:1 1 260px}
.daylink:hover{border-color:#3a5194;background:#182240}
.grid2{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:10px}
@media print{.bar,.tabs,.btn{display:none}body{background:#fff;color:#111}.card,.tst,.kpi{background:#fff;border-color:#ccc}}
"""

_JS = """
function tog(e){var c=e.currentTarget;c.classList.toggle('open');}
function tab(n,el){document.querySelectorAll('.sec').forEach(function(s){s.classList.remove('on')});
 document.querySelectorAll('.tab').forEach(function(t){t.classList.remove('on')});
 document.getElementById('sec-'+n).classList.add('on');el.classList.add('on');}
function cp(){var t=document.getElementById('rawtext').innerText;
 function ok(){var b=document.getElementById('cpbtn');b.textContent='Copied - paste it in chat';b.classList.add('ok');
  setTimeout(function(){b.textContent='Copy everything for chat';b.classList.remove('ok')},2600);}
 function fb(){var r=document.getElementById('rawtext');var s=document.createRange();s.selectNodeContents(r);
  var c=getSelection();c.removeAllRanges();c.addRange(s);try{document.execCommand('copy');ok()}catch(e){r.focus()}
  c.removeAllRanges();}
 if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(ok,fb)}else{fb()}}
function dn(){var b=document.getElementById('rawtext');
 var t=new Blob([b.innerText],{type:'text/plain'});var a=document.createElement('a');
 a.href=URL.createObjectURL(t);a.download=(document.title.replace(/[^A-Za-z0-9._-]+/g,'_')||'report')+'.txt';
 document.body.appendChild(a);a.click();a.remove();}
function note(v){try{localStorage.setItem('gbm-note-'+document.title,v)}catch(e){}}
function done(i,v){try{localStorage.setItem('gbm-chk-'+document.title+'-'+i,v?'1':'')}catch(e){}}
function load(){var k='gbm-note-'+document.title;var n=document.getElementById('note');
 if(n){n.value=localStorage.getItem(k)||'';n.oninput=function(){note(n.value)}}
 document.querySelectorAll('.checklist input').forEach(function(c,i){
  c.checked=!!localStorage.getItem('gbm-chk-'+document.title+'-'+i);
  c.parentNode.classList.toggle('done',c.checked);
  c.onchange=function(){c.parentNode.classList.toggle('done',c.checked);done(i,c.checked)}});
 document.querySelectorAll('.tst').forEach(function(c){c.addEventListener('click',tog)});}
document.addEventListener('DOMContentLoaded',load);
"""


def _esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _num(v, suffix="", dash="--", dec=None):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return dash
    d = 0 if (dec is not None and dec <= 0) else (dec if dec is not None else None)
    if d == 0:
        s = f"{int(round(f)):,}"
    elif d is not None:
        s = f"{f:,.{d}f}"
    elif abs(f - round(f)) < 1e-9 and suffix == "":
        s = f"{int(round(f)):,}"
    else:
        s = f"{f:,.1f}"
    return s + suffix


def _bar(v, mx, cls="pass", label=None, mxw=100.0):
    """a horizontal bar drawn with plain css (no images, no svg needed)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        f = 0.0
    w = 0.0 if mx <= 0 else max(0.0, min(mxw, 100.0 * f / mx))
    col = {"pass": "#2fbf71", "warn": "#e0a53a", "fail": "#e5544f", "info": "#4f7bd6"}[cls]
    lab = label if label is not None else _num(v)
    return (f'<span class=bar2><i style="width:{w:.1f}%;background:{col}"></i>'
            f"<u>{_esc(lab)}</u></span>")


def _ring(pct_val, label, sub=""):
    try:
        p = max(0.0, min(100.0, float(pct_val)))
    except (TypeError, ValueError):
        p = 0.0
    r, c = 30.0, 2 * 3.141592653589793 * 30.0
    col = "#2fbf71" if p >= 66 else ("#e0a53a" if p >= 45 else "#e5544f")
    return (f'<div class=ring style=text-align:center;min-width:104px><svg viewBox="0 0 76 76" width=82 height=82>'
            f'<circle cx=38 cy=38 r={r:.0f} fill=none stroke=#1b2238 stroke-width=7></circle>'
            f'<circle cx=38 cy=38 r={r:.0f} fill=none stroke={col} stroke-width=7 stroke-linecap=round '
            f'stroke-dasharray="{c * p / 100.0:.1f} {c:.1f}" transform="rotate(-90 38 38)"/>'
            f'<text x=38 y=42 text-anchor=middle fill=#e7ebf5 font-size=15 font-weight=700>{p:.0f}%</text></svg>'
            f'<div style="font-size:10px;letter-spacing:.06em;text-transform:uppercase;color:#8b95b0;white-space:nowrap">{_esc(label)}</div>'
            + (f'<div style=font-size:11px;color:#7c86a2>{_esc(sub)}</div>' if sub else "") + "</div>")


def _grade_counts(metrics, rows):
    ct = dict(metrics.get("counts") or {})
    if not ct:
        for g, _n, _h, _w in rows:
            ct[g] = ct.get(g, 0) + 1
    n = sum(ct.get(k, 0) for k in ("PASS", "WARN", "FAIL", "NA"))
    scored = ct.get("PASS", 0) + ct.get("WARN", 0) + ct.get("FAIL", 0)
    health = (100.0 * (ct.get("PASS", 0) + 0.5 * ct.get("WARN", 0)) / scored) if scored else 0.0
    return ct, n, health


def day_dashboard(day, reports, metrics, full_text, history_html=""):
    """The one-page result a user double-clicks: verdict, KPIs, tests, judges, hours, raw text."""
    order = ("alive", "feed", "pipe", "dq", "guards", "sig", "whatif", "teams", "judges",
             "cov", "ver", "lat", "money", "files", "hour")
    rows = [(g, n, h, w) for k in order for g, n, h, w in reports[k].rows]
    ct, n_tests, health = _grade_counts(metrics, rows)
    wi = metrics.get("whatif") or {}
    jd = metrics.get("judges") or {}
    hh = metrics.get("hourly") or {}
    date = _esc(metrics.get("date") or str(day))

    if ct.get("FAIL", 0):
        state, banner, msg = ("STOP - the day is not gradeable", "b-BAD",
                             "Some core check failed. Fix that first: with a broken feed or missing "
                             "evidence every number below is about the setup, not about the strategy.")
    elif ct.get("NA", 0) >= 4:
        state, banner, msg = ("ALIVE, NOT YET MEASURABLE", "b-LOOK",
                              "The robot ran, but several checks have no data yet (they are marked NO-DATA). "
                              "This is a recording problem, not a losing problem - fix the diary, then re-audit.")
    elif ct.get("NA", 0) or ct.get("WARN", 0):
        state, banner, msg = ("GOOD DAY, WITH NOTES", "b-GOOD",
                              "Everything worked. The warnings below are things to watch, not defects - "
                              "read test 7 (what-if) and test 9 (judges) for what the strategy would have done.")
    else:
        state, banner, msg = ("CLEAN DAY", "b-GOOD",
                              "All 15 tests graded with real data and no warnings. Test 7 and 9 are trustworthy as-is.")

    para = (metrics.get("paragraph") or "").strip()
    head = f"<h2>Why</h2><div class=card style=font-size:13.5px>{_esc(para)}</div>" if para else ""

    chips = [
        ("price window", "08:00-23:00 Budapest"),
        ("confidence gate", f"{metrics.get('gate_used', 0):.0f}%"),
        ("AI gate", f"{metrics.get('ai_gate_used', 0):.0f}"),
        ("spread cost", f"{metrics.get('spread_cap', 0):.2f} pts"),
        ("awake", f"{_num(metrics.get('hours_awake'))} h"),
        ("biggest silence", f"{_num(metrics.get('biggest_silence_min'))} min"),
        ("pipeline cycles", _num(metrics.get("cycles"))),
        ("pipeline errors", _num(metrics.get("errors"))),
        ("median cycle", f"{_num(metrics.get('median_ms'))} ms"),
        ("generated", str(metrics.get("generated") or "")[:16].replace("T", " ") + " UTC"),
    ]
    chips_html = "".join(f"<span class=chip>{_esc(a)} <b>{_esc(b)}</b></span>" for a, b in chips)

    kpis = [
        ("Price prints read", _num(metrics.get("feed_prints")), f"{_num(metrics.get('feed_coverage_pct'))}% of window", ""),
        ("MBO (footprint) lines", _num(metrics.get("feed_mbo")), "depth + aggression", ""),
        ("Snapshots decided", _num(metrics.get("decisions")), "every cycle wrote one", ""),
        ("Diary records", _num(metrics.get("diary_records")), "judge evidence", ""),
        ("Cleared the gate", _num(metrics.get("conf_over_gate")),
         f"@ {metrics.get('gate_used', 0):.0f}% conf", ""),
        ("Orders sent to MT5", _num(metrics.get("orders_to_mt5")),
         "0 = signal only, nothing traded", "warn" if not metrics.get("orders_to_mt5") else "pos"),
    ]
    if wi:
        kpis += [
            ("What-if result", f"{wi.get('total_pts', 0):+.1f} pts", f"{wi.get('n', 0)} trades replayed",
             "pos" if wi.get("total_pts", 0) > 0 else "neg"),
            ("Win rate", f"{wi.get('win_rate', 0):.0f}%", "of replayed trades", ""),
            ("Profit factor", f"{wi.get('profit_factor', 0):.2f}", ">1 = wins bigger than losses",
             "pos" if wi.get("profit_factor", 0) >= 1 else "neg"),
            ("Max drawdown", f"{wi.get('max_drawdown_pts', 0):.1f} pts", "worst dip in the replay", "neg"),
        ]
    kpi_html = "".join(
        f'<div class=kpi><span>{_esc(a)}</span><b class="{c}">{_esc(b)}</b><i>{_esc(d)}</i></div>'
        for a, b, d, c in kpis)

    cards = "".join(
        f'<div class="tst {g}"><div class=hd><span class=no>#{i}</span><span class=nm>{_esc(n)}</span>'
        f'<span class="pill p-{g}">{g}</span></div><div class=hl>{_esc(h)}</div>'
        f'<div class=bd>{"".join(f"<div>{_esc(x)}</div>" for x in w)}</div></div>'
        for i, (g, n, h, w) in enumerate(rows, start=1))

    jrows = ""
    for rec in jd.get("table", []):
        acc = rec.get("right_pct", 0.0)
        cls = "pass" if acc >= 55 else ("fail" if acc < 45 else "warn")
        pts = rec.get("pts", 0.0)
        jrows += ("<tr>"
                  f"<td><b>{_esc(rec.get('judge'))}</b>"
                  f"<div class=dim>spoke in {rec.get('part_pct', 0.0):.0f}% of snapshots</div></td>"
                  f"<td class=n><b>{_num(rec.get('votes'))}</b></td>"
                  f"<td class=n>{_esc(rec.get('buy', 0))} / {_esc(rec.get('sell', 0))} / {_esc(rec.get('quiet', 0))}</td>"
                  f'<td class=acc><div><b class="{ "pos" if acc >= 55 else ("neg" if acc < 45 else "warn") }">{acc:.1f}%</b>'
                  f'{_bar(acc, 100.0, cls, "", 100.0)}</div>'
                  f"<i>{_num(rec.get('scored'))} calls scored</i></td>"
                  f"<td class='n {'pos' if pts > 0 else 'neg'}'><b>{pts:+.1f}</b></td></tr>")
    if jrows:
        judges_html = (f'<div class=card><table><tr><th>Judge</th><th class=n>Votes</th><th class=n>Buy / Sell / quiet</th>'
                       f"<th class=acc>Accuracy on its own clock</th><th class=n>Points after spread</th>"
                       f"</tr>{jrows}</table>"
                       f'<div style="font-size:11.5px;color:#8b95b0;margin-top:8px">'
                       f"footprint = your BookMap aggressive buyer vs seller; each judge is scored on its own horizon "
                       f"(footprint/L3 3 M5 bars, iceberg/whale 6, VWAP/structure 12, macro 24). "
                       f"Weight suggestions need at least 20 votes on a day.</div></div>")
    else:
        judges_html = ('<div class=card>No judge data for this day. The diary was written by a build that did not '
                       'keep the panel, so test 9 can only read the notes back. Apply the v7.1 <code>main.py</code> '
                       'patch, run a full day, re-audit - then this table fills itself.</div>')

    heat = ""
    hours = hh.get("hours") or {}
    if hours:
        mx = max((v.get("n", 0) for v in hours.values()), default=0) or 1
        best = max(hours.items(), key=lambda kv: kv[1].get("max", 0))[0] if hours else None
        cells = ""
        for h in range(24):
            v = hours.get(str(h))
            n = (v or {}).get("n", 0)
            frac = n / mx if mx else 0
            col = ("#2fbf71" if str(h) == best else "#4f7bd6") if n else "#141a2a"
            tip = (f"{h:02d}:00  {n} snapshots, best conf {v.get('max', 0):.0f}%, "
                   f"{v.get('buy', 0)} buy / {v.get('sell', 0)} sell") if v else f"{h:02d}:00  nothing"
            cells += (f'<div class=cell title="{_esc(tip)}" '
                      f'style="background:{col};opacity:{0.12 + 0.88 * frac:.2f}"></div>')
        caps = "".join(f'<div class=cap>{h if h % 2 == 0 else ""}</div>' for h in range(24))
        sess = "".join(f'<div>{_esc(s["label"])}<b>{_num(s["n"])}</b>'
                       f'<span class=dim>best conf {s["max"]:.0f}%</span></div>' for s in hh.get("sessions", []))
        heat = (f'<div class=card><div style="font-size:12px;color:#8e98b4">Snapshots per hour '
                f"(green = the hour with the loudest signal; hover any cell)</div>"
                f'<div class=heat><div></div>{cells}</div><div class=heat><div></div>{caps}</div>'
                f'<div class=sess>{sess}</div></div>')

    reasons = metrics.get("guard_reasons") or []
    if reasons:
        mx = 0
        parsed = []
        for txt in reasons:
            m = re.match(r"^(.*) x(\d+)$", txt)
            parsed.append((m.group(1).strip(), float(m.group(2))) if m else (txt, 0.0))
            mx = max(mx, parsed[-1][1])
        rrows = "".join(f"<tr><td>{_esc(k)}</td><td style=width:220px>{_bar(v, mx, 'info', _num(v))}</td></tr>"
                        for k, v in parsed)
        why_no = f'<div class=card><table><tr><th>Why it said NO</th><th>Times</th></tr>{rrows}</table></div>'
    else:
        why_no = '<div class=card>No guard reasons were logged for this day.</div>'

    ring = _ring(health, "tests clean", f"{ct.get('PASS', 0)} pass / {ct.get('WARN', 0)} warn / {ct.get('FAIL', 0)} fail")
    wr_ring = _ring(wi.get("win_rate", 0.0), "what-if win rate", f"{wi.get('n', 0)} trades") if wi else ""
    cov_ring = _ring(metrics.get("feed_coverage_pct", 0.0), "price feed coverage", "of the 15h window")
    gate_pct = (100.0 * metrics.get("conf_over_gate", 0) / metrics["decisions"]) if metrics.get("decisions") else 0.0
    gate_ring = _ring(gate_pct, "signals over gate", f"{_num(metrics.get('conf_over_gate'))} of {_num(metrics.get('decisions'))}")

    checklist = "".join(
        f'<li><label><input type=checkbox> {_esc(re.sub(r"^\\d+\\.\\s*", "", c))}</label></li>'
        for c in (metrics.get("checklist") or []))
    check_html = (f'<div class=card><h2 style="margin-top:0">By hand (saved on this PC only - no upload)</h2>'
                  f'<ul class=checklist>{checklist}</ul></div>' if checklist else "")

    history = (f'<h2>How the robot was doing before</h2><div class=card>{history_html}</div>'
               if history_html else "")

    esc_text = _esc(full_text)
    dtitle = f"Gold-BookMap - {date} (Budapest)"
    return ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{_esc(dtitle)}</title><style>{_STYLE}</style><script>{_JS}</script></head><body>"
            f'<div class=bar><span class=lg>GOLD-BOOKMAP</span><span class=dy>{_esc(date)}</span>'
            '<span class=sp></span>'
            '<button class=btn id=cpbtn onclick="cp()">Copy everything for chat</button>'
            '<button class=btn onclick="dn()">Download .txt</button>'
            '<a class=btn href="history_report.html">Trend table</a>'
            '<button class=btn onclick="window.print()">Print / PDF</button></div>'
            '<div class=wrap>'
            f'<div class="banner {banner}"><div class=b-txt><div class=b-state>{_esc(state)}</div>'
            f'<div class=b-msg>{_esc(msg)}</div>'
            f'<div class=meta>{chips_html}</div></div>'
            f'<div class=ringrow>{ring}{wr_ring}{cov_ring}{gate_ring}</div></div>'
            '<div class="tabs tabsx">'
            '<button class="tab on" onclick="tab(\'score\',this)">Scoreboard</button>'
            '<button class=tab onclick="tab(\'jud\',this)">Judges</button>'
            '<button class=tab onclick="tab(\'tim\',this)">Hours &amp; guards</button>'
            '<button class=tab onclick="tab(\'hand\',this)">By hand (checklist)</button>'
            '<button class=tab onclick="tab(\'raw\',this)">Full text (copy / share)</button>'
            "</div>"

            + head +
            '<h2>The day in numbers</h2>'
            f'<div class=kpis>{kpi_html}</div>'
            f'<div class="sec on" id=sec-score><h2>All 15 tests - click a card for the details</h2>'
            f'<div class=grid>{cards}</div></div>'
            f'<div class=sec id=sec-jud><h2>Judge panel</h2>{judges_html}</div>'
            f'<div class=sec id=sec-hand><h2>By hand</h2>{check_html}</div>'
            f'<div class=sec id=sec-tim><div class=row>{heat}{why_no}</div></div>'
            '<div class="sec" id=sec-raw>'
            '<h2>Plain text of this report - selected by the button above, or click inside and Ctrl+A</h2>'
            f'<div class=rawbox><pre class=raw id=rawtext tabindex=0>{esc_text}</pre></div>'
            '<textarea id=note class=note rows=3 style="width:100%%;resize:vertical" '
            'placeholder="your own note for this day - kept in this browser only, nothing is uploaded"></textarea>'
            "</div>"
            + history +
            "</div></body></html>")


def _day_rows(data_dir):
    """Everything the index needs, one dict per audited day (json first, txt as fallback)."""
    out = {}
    for p in sorted(Path(data_dir).glob("day_metrics_*.json")):
        try:
            m = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        date = m.get("date") or p.stem.replace("day_metrics_", "")
        m.setdefault("date", date)
        m["_source"] = p.name
        out[date] = m
    for p in sorted(Path(data_dir).glob("day_audit_*.txt")):
        date = p.stem.replace("day_audit_", "")
        if date in out:
            continue
        m = {"date": date, "_legacy": True}
        try:
            txt = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        mm = re.search(r"VERDICT .*?pass (\d+) warn (\d+) fail (\d+) nodata (\d+)", txt)
        if mm:
            m["counts"] = {"PASS": int(mm.group(1)), "WARN": int(mm.group(2)),
                          "FAIL": int(mm.group(3)), "NA": int(mm.group(4))}
        for key, pat, conv in (("feed_prints", r"([\d,]+) prints", int),
                               ("decisions", r"(\d+) decisions", int),
                               ("orders_to_mt5", r"(\d+) to MT5", int)):
            mm2 = re.search(pat, txt)
            if mm2:
                m[key] = int(mm2.group(1).replace(",", "")) if conv is int else mm2.group(1)
        m["_source"] = p.name
        out[date] = m
    return [out[k] for k in sorted(out)]


def write_index(data_dir, out_path=None):
    """data/index.html - the file you double-click: one card per day you have ever audited."""
    data_dir = Path(data_dir)
    days = _day_rows(data_dir)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cards = ""
    for m in reversed(days):
        ct = dict(m.get("counts") or {})
        state = ("FAIL" if ct.get("FAIL") else ("LOOK" if ct.get("NA", 0) >= 4 else
                 ("WARN" if ct.get("WARN") else "GOOD"))) if ct else "NONE"
        cls = {"GOOD": "p-PASS", "WARN": "p-WARN", "FAIL": "p-FAIL", "LOOK": "p-WARN",
               "NONE": "p-NA"}[state]
        wi = m.get("whatif") or {}
        jd = m.get("judges") or {}
        bits = [f"{ct.get('PASS', 0)} pass - {ct.get('WARN', 0)} warn - {ct.get('FAIL', 0)} fail"
                f" - {ct.get('NA', 0)} no-data" if ct else "no scoreboard yet",
                f"{_num(m.get('feed_prints'))} prints - {_num(m.get('decisions'))} snapshots"
                f" - {_num(m.get('diary_records'))} diary"]
        if wi:
            bits.append(f"what-if {wi.get('total_pts', 0):+.1f} pts - WR {wi.get('win_rate', 0):.0f}%"
                        + (f" - best judge {jd['best']}" if jd.get("best") else ""))
        if m.get("_legacy"):
            bits.append("text-only day: run  bash daily_check.sh " + _esc(m["date"]) + "  to get the json twin")
        href = f"day_report_{m['date']}.html"
        inner = (f'<div class=hd><span class=nm>{_esc(m["date"])}</span>'
                 f'<span class="pill {cls}">{state}</span></div>'
                 + "".join(f'<div style="font-size:11.5px;color:#9aa4bd">{_esc(b)}</div>' for b in bits))
        if (data_dir / href).exists():
            cards += f'<a class=daylink href="{href}">{inner}</a>'
        else:
            cards += f'<div class=daylink style=cursor:default>{inner}</div>'
    if not days:
        cards = ('<div class=card>No audited day found yet. Run <b>bash daily_check.sh</b> (or '
                 '<b>python audit_day.py --latest</b>) and come back to this page.</div>')
    n_fail = sum(1 for m in days if (m.get("counts") or {}).get("FAIL"))
    n_ok = sum(1 for m in days if (m.get("counts") or {}).get("PASS"))
    newest = f"day_report_{days[-1]['date']}.html" if days else ""
    html = ("<!doctype html><html lang=en><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>Gold-BookMap - all audited days</title><style>{_STYLE}</style><script>{_JS}</script></head><body>"
            '<div class=bar><span class=lg>GOLD-BOOKMAP</span><span class=dy>Audited days</span>'
            '<span class=sp></span>'
            f'<button class=btn onclick="location=\'{newest}\'">Open newest report</button>'
            '<a class=btn href="history_report.html">Trend table</a>'
            '<button class=btn onclick="window.print()">Print / PDF</button></div>'
            '<div class=wrap>'
            f'<div class="banner b-{"BAD" if n_fail else ("LOOK" if len(days) < 2 else "GOOD")}">'
            f'<div class=b-txt><div class=b-state>{len(days)} day(s) audited</div>'
            f'<div class=b-msg>{n_ok} with a passing scoreboard, {n_fail} with a hard failure. '
            "Click a card to open that day. Newest first. This page reads the json files your audit wrote, "
            f"so it is always in step with what you ran. Generated {now}.</div></div></div>"
            '<h2>Days</h2>'
            f'<div class=grid2>{cards}</div>'
            '<h2>What each number means</h2><div class=card style=font-size:12.5px>'
            "PASS/WARN/FAIL/NO-DATA = the 15 checks of that day &middot; price prints = BookMap tape actually read "
            "&middot; snapshots = decision cycles finished &middot; to MT5 = signals the bridge handed over "
            "&middot; what-if = replay of every signal on that day's M5 candles with SL 2xATR / TP 3.5xATR "
            "minus your spread cap &middot; best/worst judge = the panel vote that was right most / least often. "
            "<b>NO-DATA is never a loss</b>; it means the recorder did not save what was needed to judge."
            "</div></div></body></html>")
    out = Path(out_path) if out_path else (data_dir / "index.html")
    out.write_text(html, encoding="utf-8")
    return out


def history_block(data_dir, limit=14):
    """Compact trend table for embedding in the day dashboard (same style, no js needed)."""
    days = _day_rows(data_dir)[-limit:]
    if not days:
        return ""
    head = ("<tr><th>Day</th><th class=n>prints</th><th class=n>snaps</th><th class=n>diary</th>"
            "<th class=n>MT5</th><th class=n>gate</th><th class=n>what-if</th><th class=n>WR</th>"
            "<th class=n>PF</th><th>state</th></tr>")
    trs = ""
    for m in reversed(days):
        ct = m.get("counts") or {}
        wi = m.get("whatif") or {}
        state = ("FAIL" if ct.get("FAIL") else ("NO-DATA" if ct.get("NA", 0) >= 4 else
                 ("WARN" if ct.get("WARN") else "PASS"))) if ct else "NONE"
        pts = wi.get("total_pts")
        pill = state if state in ("PASS", "WARN", "FAIL", "NA") else "NA"
        trs += (f'<tr><td><a href="day_report_{_esc(m["date"])}.html">{_esc(m["date"])}</a></td>'
                f"<td class=n>{_num(m.get('feed_prints'))}</td>"
                f"<td class=n>{_num(m.get('decisions'))}</td>"
                f"<td class=n>{_num(m.get('diary_records'))}</td>"
                f"<td class=n>{_num(m.get('orders_to_mt5'))}</td>"
                f"<td class=n>{_num(m.get('gate_used'), '%', dec=0)}</td>"
                f"<td class='n {'pos' if (pts or 0) > 0 else 'neg'}'>{_num(pts)}</td>"
                f"<td class=n>{_num(wi.get('win_rate'), '%', dec=0)}</td>"
                f"<td class=n>{_num(wi.get('profit_factor'), dec=2)}</td>"
                f'<td><span class="pill p-{pill}">{state}</span></td></tr>')
    return (f"<table>{head}{trs}</table>"
            f'<div style="font-size:11.5px;color:#8b95b0;margin-top:8px">last {len(days)} audited day(s), '
            "newest first - click a day to open its report</div>")
