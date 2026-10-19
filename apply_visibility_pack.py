#!/usr/bin/env python3
"""apply_visibility_pack.py - VISIBILITY PACK (fusion v1 follow-up, 2026-10-17).

Owner's law, his words: make sure the details are visible on both sides and
nothing will hide or forget from our eyes. Four things this pack does:

  1. LOOP SIDE - POWER speaks EVERY cycle:
     a new "STEP 2  POWER" console line (timeframe, direction, activity,
     dominance, reason), and the power-lead's near-misses are never silent:
     "STEP 3  POWER-LEAD  holds: conf X < floor 30 | POWER ..." prints when
     the lead considered a carry and refused it.

  2. NIGHT SIDE - the report tools stop assuming M5 diary bars:
     power_report_card.py + council_shadow.py now derive the bar interval
     from the diary itself, so "+15/+30/+60m" horizons are TRUE minutes on
     an M3 diary (the old code graded +9/+18/+36 min and mislabeled them),
     and minute-to-bar alignment uses the real interval, not a hardcoded
     5-minute window.

  3. NIGHT SIDE - power-lead receipts:
     council_shadow.py counts the day's decisions by ai_model (carried /
     ai / fallback / none-quiet) as section 0; audit_day.py gives
     power-lead carries their OWN bucket in WHO ANSWERED (never counted as
     AI answers) plus a POWER-LEAD CARRIES line; report_evening.sh gains
     desk 9 (POWER card) and desk 10 (Council shadow) so the evening paper
     carries the full POWER story.

  4. TESTS: +4 loop-visibility tests, +3 audit_day fallback-stats tests,
     both rigs grow an M3 day that locks the interval-aware horizons.

Requires: fusion v1 already installed (commit ee284b8 or later).
Usage:
    python apply_visibility_pack.py            # install (.vispack.bak backups)
    python apply_visibility_pack.py --rollback # restore originals

Never stage the .bak files. Demo only. Fail-closed everywhere.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKUP_SUFFIX = ".vispack.bak"

NEW_FILES = {
    "test_audit_fallback_stats.py": '#!/usr/bin/env python3\n"""Reverse-engineering tests for audit_day._fallback_stats after fusion v1.\n\nThe owner\'s law: any change ships with corner tests. Corners probed here:\n  1. power-lead rows are their OWN bucket - never counted as "ai" answers\n     (the AI was quiet when POWER carried the trade).\n  2. a mixed day totals every bucket, power-lead included.\n  3. the pre-24k diary path (no ai_model column at all) is untouched.\n"""\nimport unittest\n\nfrom audit_day import _fallback_stats\n\n\ndef row(model):\n    return {"ai_model": model}\n\n\nclass FallbackStatsPowerLeadTest(unittest.TestCase):\n    def test_power_lead_is_own_bucket_not_ai(self):\n        stats = _fallback_stats([row("power-lead-v1"), row("power-lead-v1")], [])\n        self.assertEqual(stats["power-lead"], 2)\n        self.assertEqual(stats["ai"], 0)\n        self.assertEqual(stats["total"], 2)\n\n    def test_mixed_day_totals_every_bucket(self):\n        stats = _fallback_stats(\n            [row("power-lead-v1"), row("power-lead-v1"), row("gemini-2.5-flash"),\n             row("fallback-rules-v1"), row("none-quiet")], [])\n        self.assertEqual(stats["ai"], 1)\n        self.assertEqual(stats["fallback"], 1)\n        self.assertEqual(stats["none"], 1)\n        self.assertEqual(stats["power-lead"], 2)\n        self.assertEqual(stats["total"], 5)\n        self.assertEqual(stats["source"], "ai_model column")\n\n    def test_pre24k_diary_path_unchanged(self):\n        stats = _fallback_stats(\n            [], [{"ai_rationale": "FALLBACK rule fired"},\n                 {"ai_rationale": "clean ai answer"}])\n        self.assertEqual(stats["fallback"], 1)\n        self.assertEqual(stats["ai"], 1)\n        self.assertEqual(stats["power-lead"], 0)\n        self.assertIn("diary", stats["source"])\n\n\nif __name__ == "__main__":\n    unittest.main()\n',
}

PATCHES = [{'file': 'main.py',
  'anchor': '\ndef run_step2(data):\n',
  'replacement': '\n'
                 'def _record_power_line(snapshot) -> None:\n'
                 '    """fusionv1 (visibility pack): one console line per cycle - POWER\'s own '
                 'state.\n'
                 '\n'
                 '    Shows timeframe, direction, activity, dominance and reason every cycle '
                 'so\n'
                 '    the new design is observable live, not only in the night diary.\n'
                 '    """\n'
                 '    try:\n'
                 '        _pw = getattr(snapshot, "power", None) or {}\n'
                 '        if _pw:\n'
                 '            _record("STEP 2  POWER",\n'
                 '                    "{} {} act {:.2f} dom {:.0f}% ({})".format(\n'
                 '                        str(_pw.get("timeframe", "?")),\n'
                 '                        str(_pw.get("direction", "?")),\n'
                 '                        float(_pw.get("activity", 0.0) or 0.0),\n'
                 '                        max(float(_pw.get("up_power_pct", 0.0) or 0.0),\n'
                 '                            float(_pw.get("down_power_pct", 0.0) or 0.0)),\n'
                 '                        str(_pw.get("reason_code", ""))))\n'
                 '    except Exception:\n'
                 '        pass\n'
                 '\n'
                 '\n'
                 'def run_step2(data):\n',
  'marker': 'def _record_power_line('},
 {'file': 'main.py',
  'anchor': '            f"confidence {snapshot.confidence:.1f})")\n    return snapshot\n',
  'replacement': '            f"confidence {snapshot.confidence:.1f})")\n'
                 '    _record_power_line(snapshot)  # fusionv1 visibility pack\n'
                 '    return snapshot\n',
  'marker': '_record_power_line(snapshot)  # fusionv1 visibility pack'},
 {'file': 'main.py',
  'anchor': '                    action = decision.action\n        except Exception:\n',
  'replacement': '                    action = decision.action\n'
                 '                else:\n'
                 '                    if _lead_on:\n'
                 '                        # fusionv1 visibility pack: near-misses are never '
                 'silent\n'
                 '                        try:\n'
                 '                            _why = ("conf {:.0f} < floor {:.0f}".format(_pc, '
                 '_floor)\n'
                 '                                    if _pc < _floor\n'
                 '                                    else "class disagrees (conf '
                 '{:.0f})".format(_pc))\n'
                 '                            _record("STEP 3  POWER-LEAD",\n'
                 '                                    "holds: {} | POWER {} | {}".format(\n'
                 '                                        _why, _pd, _rc))\n'
                 '                        except Exception:\n'
                 '                            pass\n'
                 '        except Exception:\n',
  'marker': 'holds: {} | POWER {} | {}".format('},
 {'file': 'power_report_card.py',
  'anchor': 'HORIZONS = {"+15m": 3, "+30m": 6, "+60m": 12}  # M5 bars ahead\n',
  'replacement': 'HORIZON_MINUTES = {"+15m": 15, "+30m": 30, "+60m": 60}  # true minutes; '
                 "steps derived from the diary's own bar interval\n",
  'marker': 'HORIZON_MINUTES'},
 {'file': 'power_report_card.py',
  'anchor': '    first, last = bars[0]["ts"], bars[-1]["ts"]\n\n',
  'replacement': '    first, last = bars[0]["ts"], bars[-1]["ts"]\n'
                 "    # fusionv1: derive horizons from the diary's own bar interval "
                 '(M3/M5/...)\n'
                 '    deltas = [(bars[i]["ts"] - bars[i - 1]["ts"]).total_seconds()\n'
                 '              for i in range(1, n)]\n'
                 '    deltas = [d for d in deltas if d > 0]\n'
                 '    bar_interval = statistics.median(deltas) if deltas else 300.0\n'
                 '    bar_interval = min(max(bar_interval, 60.0), 3600.0)\n'
                 '\n'
                 '    def _steps(minutes):\n'
                 '        return max(1, int(round(minutes * 60.0 / bar_interval)))\n'
                 '\n',
  'marker': "derive horizons from the diary's own bar interval"},
 {'file': 'power_report_card.py',
  'anchor': '    print(f"graded symbol: {chosen}   bars: {n}   window: {first:%H:%M} - '
            '{last:%H:%M} UTC")\n'
            '    if len(inventory) > 1:\n',
  'replacement': '    print(f"graded symbol: {chosen}   bars: {n}   window: {first:%H:%M} - '
                 '{last:%H:%M} UTC")\n'
                 '    print(f"bar interval: {bar_interval:.0f}s (M{bar_interval / 60.0:.0f}) - '
                 'horizons are true minutes")\n'
                 '    if len(inventory) > 1:\n',
  'marker': 'horizons are true minutes'},
 {'file': 'power_report_card.py',
  'anchor': '        for label, step in HORIZONS.items():\n',
  'replacement': '        for label, minutes in HORIZON_MINUTES.items():\n'
                 '            step = _steps(minutes)\n',
  'marker': 'for label, minutes in HORIZON_MINUTES.items():'},
 {'file': 'power_report_card.py',
  'anchor': '                    j = i + HORIZONS["+30m"]\n',
  'replacement': '                    j = i + _steps(30)\n',
  'marker': 'j = i + _steps(30)'},
 {'file': 'power_report_card.py',
  'anchor': '    bar_ends = [rec["ts"] for rec in bars]\n\n',
  'replacement': '    bar_ends = [rec["ts"] for rec in bars]\n'
                 "    # fusionv1: alignment window from the diary's own bar interval\n"
                 '    deltas = [(bars[i]["ts"] - bars[i - 1]["ts"]).total_seconds()\n'
                 '              for i in range(1, len(bars))]\n'
                 '    deltas = [d for d in deltas if d > 0]\n'
                 '    bar_interval = statistics.median(deltas) if deltas else 300.0\n'
                 '    bar_interval = min(max(bar_interval, 60.0), 3600.0)\n'
                 '\n'
                 '    def _steps(minutes):\n'
                 '        return max(1, int(round(minutes * 60.0 / bar_interval)))\n'
                 '\n',
  'marker': "alignment window from the diary's own bar interval"},
 {'file': 'power_report_card.py',
  'anchor': '        # find the diary bar whose [end-5m, end] contains this minute\n',
  'replacement': '        # find the diary bar whose [end-interval, end] contains this '
                 'minute\n',
  'marker': '[end-interval, end] contains this minute'},
 {'file': 'power_report_card.py',
  'anchor': '            if end - timedelta(minutes=5) <= ts <= end:\n',
  'replacement': '            if end - timedelta(seconds=bar_interval) <= ts <= end:\n',
  'marker': 'end - timedelta(seconds=bar_interval)'},
 {'file': 'power_report_card.py',
  'anchor': '                j = bar_idx + HORIZONS["+30m"]\n',
  'replacement': '                j = bar_idx + _steps(30)\n',
  'marker': 'j = bar_idx + _steps(30)'},
 {'file': 'council_shadow.py',
  'anchor': 'HORIZON_30 = 6   # M5 bars\nHORIZON_60 = 12\n',
  'replacement': '# horizons are TRUE minutes; steps are derived in main() from the\n'
                 "# diary's own bar interval (M3/M5/...), so labels never lie.\n",
  'marker': 'horizons are TRUE minutes'},
 {'file': 'council_shadow.py',
  'anchor': '    stren_col = find_col(cols, "signal_strength", "strength")\n'
            '    if not (ts_col and conf_col):\n',
  'replacement': '    stren_col = find_col(cols, "signal_strength", "strength")\n'
                 '    model_col = find_col(cols, "ai_model", "model")\n'
                 '    if not (ts_col and conf_col):\n',
  'marker': 'model_col = find_col'},
 {'file': 'council_shadow.py',
  'anchor': '    bar_ends = [rec["ts"] for rec in bars]\n\n',
  'replacement': '    bar_ends = [rec["ts"] for rec in bars]\n'
                 "    # fusionv1: derive horizon steps + alignment window from the diary's\n"
                 '    # own bar interval (M3/M5/...), not a hardcoded M5 assumption.\n'
                 '    deltas = [(bars[i]["ts"] - bars[i - 1]["ts"]).total_seconds()\n'
                 '              for i in range(1, n)]\n'
                 '    deltas = [d for d in deltas if d > 0]\n'
                 '    bar_interval = statistics.median(deltas) if deltas else 300.0\n'
                 '    bar_interval = min(max(bar_interval, 60.0), 3600.0)\n'
                 '\n'
                 '    def _steps(minutes):\n'
                 '        return max(1, int(round(minutes * 60.0 / bar_interval)))\n'
                 '\n'
                 '    HORIZON_30 = _steps(30)\n'
                 '    HORIZON_60 = _steps(60)\n'
                 '\n',
  'marker': 'HORIZON_30 = _steps(30)'},
 {'file': 'council_shadow.py',
  'anchor': '            if end - timedelta(minutes=5) <= ts <= end:\n',
  'replacement': '            if end - timedelta(seconds=bar_interval) <= ts <= end:\n',
  'marker': 'end - timedelta(seconds=bar_interval)'},
 {'file': 'council_shadow.py',
  'anchor': '    print(f"graded symbol: {chosen}   bars: {n}   aligned minutes: '
            '{len(minutes)}")\n'
            '    if twins:\n',
  'replacement': '    print(f"graded symbol: {chosen}   bars: {n}   aligned minutes: '
                 '{len(minutes)}")\n'
                 '    print(f"bar interval: {bar_interval:.0f}s (M{bar_interval / 60.0:.0f}) - '
                 'horizons are true minutes")\n'
                 '\n'
                 "    # fusionv1: power-lead receipts census (what carried the day's trades)\n"
                 '    print()\n'
                 '    print("0. POWER-LEAD RECEIPTS (decisions_log ai_model)")\n'
                 '    if model_col:\n'
                 '        carried = ai = fallback = quiet = other = 0\n'
                 '        for row in rows:\n'
                 '            ts = parse_ts(row.get(ts_col))\n'
                 '            if ts is None or not (day_start <= ts < day_end):\n'
                 '                continue\n'
                 '            m = str(row.get(model_col) or "").strip().lower()\n'
                 '            if m.startswith("power-lead"):\n'
                 '                carried += 1\n'
                 '            elif m.startswith("fallback"):\n'
                 '                fallback += 1\n'
                 '            elif m.startswith("gemini"):\n'
                 '                ai += 1\n'
                 '            elif m.startswith("none") or m == "":\n'
                 '                quiet += 1\n'
                 '            else:\n'
                 '                other += 1\n'
                 '        print(f"  carried: {carried} (ai_model=power-lead-v1) | ai: {ai} | '
                 '"\n'
                 '              f"fallback: {fallback} | none/quiet: {quiet} | other: '
                 '{other}")\n'
                 '    else:\n'
                 '        print("  ai_model column not found in decisions_log - skipped")\n'
                 '    if twins:\n',
  'marker': '0. POWER-LEAD RECEIPTS'},
 {'file': 'audit_day.py',
  'anchor': '        _fb = _fallback_stats(decisions, DIARY_FOR_DAY)\n'
            '        if _fb["total"]:\n',
  'replacement': '        _fb = _fallback_stats(decisions, DIARY_FOR_DAY)\n'
                 '        if _fb.get("power-lead"):\n'
                 '            why.append(f"POWER-LEAD CARRIES: {_fb[\'power-lead\']} trade(s) '
                 'carried by the "\n'
                 '                       f"POWER-lead (fusion v1) - the AI was quiet, POWER '
                 'spoke with "\n'
                 '                       f"dominance and the class floor was met. Receipts: '
                 'ai_model="\n'
                 '                       f"power-lead-v1 rows in the decision log; per-call '
                 'detail in "\n'
                 '                       f"power_report_card.py and council_shadow.py for this '
                 'day")\n'
                 '        if _fb["total"]:\n',
  'marker': 'POWER-LEAD CARRIES:'},
 {'file': 'audit_day.py',
  'anchor': '            models["fallback"] += 1\n'
            '        elif m.startswith("none") or m == "unknown":\n',
  'replacement': '            models["fallback"] += 1\n'
                 '        elif m.startswith("power-lead"):\n'
                 '            models["power-lead"] += 1   # fusion v1: POWER carried it, AI '
                 'was quiet\n'
                 '        elif m.startswith("none") or m == "unknown":\n',
  'marker': 'models["power-lead"] += 1'},
 {'file': 'audit_day.py',
  'anchor': '    total = models.get("ai", 0) + models.get("fallback", 0) + models.get("none", '
            '0)\n',
  'replacement': '    total = (models.get("ai", 0) + models.get("fallback", 0)\n'
                 '             + models.get("none", 0) + models.get("power-lead", 0))\n',
  'marker': '+ models.get("power-lead", 0))'},
 {'file': 'audit_day.py',
  'anchor': '            "none": models.get("none", 0), "cached": models.get("cached", 0),\n'
            '            "none_why": none_why.most_common(),\n',
  'replacement': '            "none": models.get("none", 0), "cached": models.get("cached", '
                 '0),\n'
                 '            "power-lead": models.get("power-lead", 0),\n'
                 '            "none_why": none_why.most_common(),\n',
  'marker': '"power-lead": models.get("power-lead", 0),'},
 {'file': 'report_evening.sh',
  'anchor': '"$PY" shadow_replay.py --date "$DATE" | sed \'s/^/  /\'\nhr\n',
  'replacement': '"$PY" shadow_replay.py --date "$DATE" | sed \'s/^/  /\'\n'
                 'hr\n'
                 '\n'
                 '# ---- fusion v1: the POWER desks (what POWER said, and what it carried) '
                 '---------\n'
                 'echo "[chief of staff] desk 9: the POWER card (what POWER said all day)..."\n'
                 'if [ -f power_report_card.py ]; then\n'
                 '  "$PY" power_report_card.py "$DATE" | sed \'s/^/  /\'\n'
                 'else\n'
                 '  echo "  (power_report_card.py not installed - skipping)"\n'
                 'fi\n'
                 'hr\n'
                 'echo "[chief of staff] desk 10: the Council shadow (what fusion would have '
                 'carried)..."\n'
                 'if [ -f council_shadow.py ]; then\n'
                 '  "$PY" council_shadow.py "$DATE" | sed \'s/^/  /\'\n'
                 'else\n'
                 '  echo "  (council_shadow.py not installed - skipping)"\n'
                 'fi\n'
                 'hr\n',
  'marker': 'desk 9: the POWER card'},
 {'file': 'rig_power_card.py',
  'anchor': '    "frozen-window caveats: 1 (10:35 +30m)",\n\n]\n',
  'replacement': '    "frozen-window caveats: 1 (10:35 +30m)",\n'
                 '    "bar interval: 300s (M5) - horizons are true minutes",\n'
                 ']\n',
  'marker': '"bar interval: 300s (M5) - horizons are true minutes",'},
 {'file': 'rig_power_card.py',
  'anchor': 'print("RIG:", "PASS" if ok else "FAIL")\n',
  'replacement': '\n'
                 '# ---------------------------------------------------------------- M3 day\n'
                 "# 2026-10-19: 40 bars, 180s apart (M3 diary like Monday's robot).\n"
                 '# ref rises +1.5/bar except a one-bar dip to 4090.0 at i=8.\n'
                 '# Refs rise monotonically, so every UP call is right and every DOWN call\n'
                 '# is wrong. With TRUE-minute horizons (steps 5/10/20): +15m N=6 right=3,\n'
                 '# +30m N=5 right=3, +60m N=3 right=3. The old hardcoded M5 steps (3/6/12)\n'
                 '# would land call 5 on the dip at i=8 (+15m right=2) and mislabel every\n'
                 '# horizon (N=6/6/5) - this scenario locks the interval-aware fix.\n'
                 'DAY2 = "2026-10-19"\n'
                 'START2 = datetime(2026, 10, 19, 8, 0, 0, tzinfo=timezone.utc)\n'
                 '\n'
                 '\n'
                 'def ref2(i):\n'
                 '    if i == 8:\n'
                 '        return 4090.0\n'
                 '    return 4100.0 + 1.5 * i\n'
                 '\n'
                 '\n'
                 'rows2 = []\n'
                 'k2 = 0\n'
                 'for i in range(N):\n'
                 '    end = START2 + timedelta(seconds=180 * (i + 1))\n'
                 '    if i in CALLS:\n'
                 '        d, dom = CALLS[i]\n'
                 '        up, down = (dom, 100.0 - dom) if d == "UP" else (100.0 - dom, dom)\n'
                 '        reason = ("UPWARD_FORCE_DOMINATES" if d == "UP"\n'
                 '                  else "DOWNWARD_FORCE_DOMINATES")\n'
                 '        votes = {"footprint_delta": {"value": 0.8 if d == "UP" else -0.8, '
                 '"quality": 1.0},\n'
                 '                 "big_prints": {"value": 0.7 if d == "UP" else -0.7, '
                 '"quality": 1.0}}\n'
                 '    else:\n'
                 '        d, up, down = "NEITHER", 50.0, 50.0\n'
                 '        reason = NEITHER_REASONS[k2 % len(NEITHER_REASONS)]\n'
                 '        k2 += 1\n'
                 '        votes = {"footprint_delta": {"value": 0.05, "quality": 1.0}}\n'
                 '    record = {\n'
                 '        "schema_version": 1,\n'
                 '        "record_id": f"RIG|M3|{end.isoformat()}|FINAL",\n'
                 '        "timestamp_utc": end.isoformat().replace("+00:00", "Z"),\n'
                 '        "symbol": "RIG", "timeframe": "M3", "phase": "FINAL",\n'
                 '        "reference_price": ref2(i),\n'
                 '        "judges": votes, "context": {},\n'
                 '        "result": {"direction": d, "reason_code": reason,\n'
                 '                   "up_power_pct": up, "down_power_pct": down,\n'
                 '                   "input_judges": votes},\n'
                 '    }\n'
                 '    rows2.append(record)\n'
                 '\n'
                 'with (MEM / f"power_{DAY2}.jsonl").open("w", encoding="utf-8") as out:\n'
                 '    for record in rows2:\n'
                 '        out.write(json.dumps(record) + "\\n")\n'
                 '        out.write(json.dumps(phantom(record)) + "\\n")\n'
                 '        out.write(json.dumps(twin(record)) + "\\n")\n'
                 '\n'
                 '# day-2 decisions log rows (APPEND: one log serves both days):\n'
                 '#   08:16 conf50 SELL -> bar5 (UP spoke) co-brave DISAGREE: +30m POWER wins\n'
                 '#       (ref15 4122.5 - ref5 4107.5 = +15 up, class said SELL)\n'
                 '#   08:25 conf50 BUY  -> bar8 (UP spoke) co-brave agree\n'
                 '#   08:20 conf22 HOLD -> quiet minute on bar6 (NEITHER)\n'
                 'with (BASE / "decisions_log.csv").open("a", encoding="utf-8", newline="") as '
                 'out:\n'
                 '    out.write("2026-10-19T08:20:00Z,HOLD,22,HOLD\\n")\n'
                 '    out.write("2026-10-19T08:16:00Z,HOLD,50,SELL\\n")\n'
                 '    out.write("2026-10-19T08:25:00Z,HOLD,50,BUY\\n")\n'
                 '\n'
                 'result2 = subprocess.run(\n'
                 '    [sys.executable, str(Path(__file__).resolve().parent / '
                 '"power_report_card.py"), DAY2,\n'
                 '     "--root", str(MEM)],\n'
                 '    capture_output=True, text=True)\n'
                 'print(result2.stdout)\n'
                 'print(result2.stderr)\n'
                 '\n'
                 'checks2 = [\n'
                 '    "bar interval: 180s (M3) - horizons are true minutes",\n'
                 '    "graded symbol: RIG",\n'
                 '    "+15m: N=6   right=3",\n'
                 '    "+30m: N=5   right=3",\n'
                 '    "+60m: N=3   right=3",\n'
                 '    "matched minutes: 3",\n'
                 '    "class brave (conf >= 45): 2 (67%)",\n'
                 '    "BOTH brave on the same minute: 2",\n'
                 '    "of those: agreeing 1, disagreeing 1",\n'
                 '    "disagreement winners (+30m): POWER 1, CLASS 0, neither 0",\n'
                 ']\n'
                 'ok2 = all(c in result2.stdout for c in checks2)\n'
                 '\n'
                 'print("RIG day1 (M5):", "PASS" if ok else "FAIL")\n',
  'marker': 'RIG day1 (M5):'},
 {'file': 'rig_power_card.py',
  'anchor': 'sys.exit(0 if ok else result.returncode or 1)\n',
  'replacement': 'print("RIG day2 (M3):", "PASS" if ok2 else "FAIL")\n'
                 'for c in checks2:\n'
                 '    if c not in result2.stdout:\n'
                 '        print("  missing:", c)\n'
                 'sys.exit(0 if (ok and ok2) else 1)\n',
  'marker': 'RIG day2 (M3):'},
 {'file': 'rig_council_shadow.py',
  'anchor': 'crafted = [(0, 35, 8, "BUY"), (1, 48, 5, "BUY"), (2, 55, 9, "SELL"),\n'
            '           (3, 25, 4, "BUY"), (4, 47, 7, "SELL"), (5, 33, 6, "HOLD"),\n'
            '           (6, 52, 9, "BUY"), (7, 30, 5, "SELL"), (8, 22, 3, "BUY"),\n'
            '           (9, 20, 2, "BUY"), (10, 18, 1, "HOLD"), (11, 18, 1, "HOLD")]\n',
  'replacement': 'crafted = [(0, 35, 8, "BUY", "power-lead-v1"), (1, 48, 5, "BUY", '
                 '"none-quiet"),\n'
                 '           (2, 55, 9, "SELL", "none-quiet"), (3, 25, 4, "BUY", '
                 '"none-quiet"),\n'
                 '           (4, 47, 7, "SELL", "fallback-rules-v1"), (5, 33, 6, "HOLD", '
                 '"none-quiet"),\n'
                 '           (6, 52, 9, "BUY", "gemini-2.5-flash"), (7, 30, 5, "SELL", '
                 '"none-quiet"),\n'
                 '           (8, 22, 3, "BUY", "none-quiet"), (9, 20, 2, "BUY", '
                 '"none-quiet"),\n'
                 '           (10, 18, 1, "HOLD", "none-quiet"), (11, 18, 1, "HOLD", '
                 '"none-quiet")]\n',
  'marker': 'crafted = [(0, 35, 8, "BUY", "power-lead-v1")'},
 {'file': 'rig_council_shadow.py',
  'anchor': 'with (BASE / "decisions_log.csv").open("w", encoding="utf-8", newline="") as '
            'out:\n'
            '    '
            'out.write("timestamp,action,signal_confidence,signal_direction,signal_strength\\n")\n'
            '    for i, conf, stren, cdir in crafted:\n'
            '        ts = bar_end(i) - timedelta(minutes=1)\n'
            '        out.write(f"{ts.isoformat().replace(\'+00:00\', '
            '\'Z\')},HOLD,{conf},{cdir},{stren}\\n")\n',
  'replacement': 'def write_log(rows_):\n'
                 '    with (BASE / "decisions_log.csv").open("w", encoding="utf-8", '
                 'newline="") as out:\n'
                 '        out.write("timestamp,action,signal_confidence,signal_direction,"\n'
                 '                  "signal_strength,ai_model\\n")\n'
                 '        for ts, conf, cdir, stren, model in rows_:\n'
                 '            out.write(f"{ts.isoformat().replace(\'+00:00\', \'Z\')},HOLD,"\n'
                 '                      f"{conf},{cdir},{stren},{model}\\n")\n'
                 '\n'
                 'write_log([(bar_end(i) - timedelta(minutes=1), conf, cdir, stren, model)\n'
                 '           for i, conf, stren, cdir, model in crafted])\n',
  'marker': 'def write_log(rows_):'},
 {'file': 'rig_council_shadow.py',
  'anchor': 'checks = [\n    "graded symbol: RIG",\n',
  'replacement': 'checks = [\n'
                 '    "bar interval: 300s (M5) - horizons are true minutes",\n'
                 '    "carried: 1 (ai_model=power-lead-v1) | ai: 1 | fallback: 1 | none/quiet: '
                 '9 | other: 0",\n'
                 '    "graded symbol: RIG",\n',
  'marker': '"bar interval: 300s (M5) - horizons are true minutes",'},
 {'file': 'rig_council_shadow.py',
  'anchor': 'print("RIG:", "PASS" if ok else "FAIL")\n',
  'replacement': 'print("RIG day1 (M5):", "PASS" if ok else "FAIL")\n',
  'marker': 'RIG day1 (M5):'},
 {'file': 'rig_council_shadow.py',
  'anchor': 'sys.exit(0 if ok else 1)\n',
  'replacement': '\n'
                 '# ---------------------------------------------------------------- M3 day\n'
                 "# 2026-10-19: 24 bars, 180s apart (M3 diary like Monday's robot).\n"
                 '# refs rise +1.0/bar: every UP mark RIGHT, every DOWN mark WRONG.\n'
                 '# POWER speaks UP (dom 80) on bars 0,3,6,9,12,15,18 and DOWN (dom 70)\n'
                 '# on bars 1,4,7,10,13; craft 6 minutes. With true-minute horizons\n'
                 '# (HORIZON_30 = 10 bars) the old hardcoded M5 window (5 min) would\n'
                 '# MISALIGN every minute - this scenario locks the interval-aware fix.\n'
                 'DAY2 = "2026-10-19"\n'
                 'START2 = datetime(2026, 10, 19, 8, 0, 0, tzinfo=timezone.utc)\n'
                 'N2 = 24\n'
                 'UP_BARS = {0, 3, 6, 9, 12, 15, 18}\n'
                 'DOWN_BARS = {1, 4, 7, 10, 13}\n'
                 '\n'
                 '\n'
                 'def bar_end2(i):\n'
                 '    return START2 + timedelta(seconds=180 * (i + 1))\n'
                 '\n'
                 '\n'
                 'rows2 = []\n'
                 'for i in range(N2):\n'
                 '    if i in UP_BARS:\n'
                 '        d, up, dn, reason = "UP", 80.0, 20.0, "UPWARD_FORCE_DOMINATES"\n'
                 '    elif i in DOWN_BARS:\n'
                 '        d, up, dn, reason = "DOWN", 30.0, 70.0, "DOWNWARD_FORCE_DOMINATES"\n'
                 '    else:\n'
                 '        d, up, dn, reason = "NEITHER", 50.0, 50.0, "WEAK_OR_NO_FORCE"\n'
                 '    votes = {"footprint_delta": {"value": 0.5, "quality": 1.0}}\n'
                 '    rows2.append({\n'
                 '        "schema_version": 1,\n'
                 '        "record_id": f"RIG|M3|{bar_end2(i).isoformat()}|FINAL",\n'
                 '        "timestamp_utc": bar_end2(i).isoformat().replace("+00:00", "Z"),\n'
                 '        "symbol": "RIG", "timeframe": "M3", "phase": "FINAL",\n'
                 '        "reference_price": 4100.0 + 1.0 * i,\n'
                 '        "judges": votes, "context": {},\n'
                 '        "result": {"direction": d, "reason_code": reason,\n'
                 '                   "up_power_pct": up, "down_power_pct": dn,\n'
                 '                   "input_judges": votes},\n'
                 '    })\n'
                 '\n'
                 'with (MEM / f"power_{DAY2}.jsonl").open("w", encoding="utf-8") as out:\n'
                 '    for rec in rows2:\n'
                 '        out.write(json.dumps(rec) + "\\n")\n'
                 '        out.write(json.dumps(twin(rec)) + "\\n")\n'
                 '\n'
                 'crafted2 = [(0, 35, "BUY", "power-lead-v1"),   # agree UP   -> 30-39 spoke, '
                 'R\n'
                 '            (1, 35, "BUY", "none-quiet"),      # DISAGREE   -> 30-39 spoke, '
                 'W\n'
                 '            (4, 32, "SELL", "none-quiet"),     # agree DOWN -> 30-39 spoke, '
                 'W\n'
                 '            (6, 50, "BUY", "gemini-2.5-flash"),  # agree UP  -> >=50 spoke, '
                 'R\n'
                 '            (7, 25, "SELL", "none-quiet"),     # agree DOWN -> 20-29 spoke, '
                 'W\n'
                 '            (9, 22, "BUY", "none-quiet")]      # agree UP   -> 20-29 spoke, '
                 'R\n'
                 'write_log([(bar_end2(i) - timedelta(seconds=60), conf, cdir, 5, model)\n'
                 '           for i, conf, cdir, model in crafted2])\n'
                 '\n'
                 'result2 = subprocess.run(\n'
                 '    [sys.executable, str(HERE / "council_shadow.py"), DAY2, "--root", '
                 'str(MEM)],\n'
                 '    capture_output=True, text=True)\n'
                 'print(result2.stdout)\n'
                 'print(result2.stderr)\n'
                 '\n'
                 'checks2 = [\n'
                 '    "bar interval: 180s (M3) - horizons are true minutes",\n'
                 '    "graded symbol: RIG",\n'
                 '    "aligned minutes: 6",\n'
                 '    "twins filtered (not graded): XAUUSD x24",\n'
                 '    "conf >=50  | spoke: N=1 right@30m 1/1 (100%)",\n'
                 '    "conf 30-39 | spoke: N=3 right@30m 1/3 (33%)",\n'
                 '    "conf 20-29 | spoke: N=2 right@30m 1/2 (50%)",\n'
                 '    "TODAY  (conf>=45 + POWER + agree): passed 1  right@30m 1/1 (100%)",\n'
                 '    "OPT B  (POWER-led, class floor 30, agree): passed 3  right@30m 2/3 '
                 '(67%)",\n'
                 '    "OPT B  (POWER-led, class floor 20, agree): passed 5  right@30m 3/5 '
                 '(60%)",\n'
                 '    "OPT A  (Council v0: conf>=30, dom>=55, 0.5c+0.5d>=45): passed 3  '
                 'right@30m 2/3 (67%)",\n'
                 '    "OPT D  (POWER-only, no class floor): passed 6  right@30m 3/6 (50%)",\n'
                 '    "carried: 1 (ai_model=power-lead-v1) | ai: 1 | fallback: 0 | none/quiet: '
                 '4 | other: 0",\n'
                 ']\n'
                 'ok2 = all(c in result2.stdout for c in checks2)\n'
                 'print("RIG day2 (M3):", "PASS" if ok2 else "FAIL")\n'
                 'for c in checks2:\n'
                 '    if c not in result2.stdout:\n'
                 '        print("  missing:", c)\n'
                 'sys.exit(0 if (ok and ok2) else 1)\n',
  'marker': 'RIG day2 (M3):'},
 {'file': 'test_fusion_power_lead.py',
  'anchor': '        importlib.reload(step3_ai_decision)\n',
  'replacement': '        importlib.reload(step3_ai_decision)\n'
                 '\n'
                 '\n'
                 '# '
                 '--------------------------------------------------------------------------\n'
                 "# 4. VISIBILITY PACK (owner's law: nothing hidden, loop + night)\n"
                 '# '
                 '--------------------------------------------------------------------------\n'
                 'def test_power_line_every_cycle(monkeypatch):\n'
                 '    """One console line per cycle: POWER\'s own state, live in the loop."""\n'
                 '    recorded = []\n'
                 '    monkeypatch.setattr(main, "_record", lambda *a, **k: '
                 'recorded.append(a))\n'
                 '    main._record_power_line(SimpleNamespace(power={\n'
                 '        "timeframe": "M3", "direction": "UP", "activity": 0.31,\n'
                 '        "up_power_pct": 71.0, "down_power_pct": 29.0,\n'
                 '        "reason_code": "UPWARD_FORCE_DOMINATES"}))\n'
                 '    assert recorded == [("STEP 2  POWER",\n'
                 '                         "M3 UP act 0.31 dom 71% '
                 '(UPWARD_FORCE_DOMINATES)")]\n'
                 '\n'
                 '\n'
                 'def test_power_line_silent_without_power(monkeypatch):\n'
                 '    recorded = []\n'
                 '    monkeypatch.setattr(main, "_record", lambda *a, **k: '
                 'recorded.append(a))\n'
                 '    main._record_power_line(SimpleNamespace(power={}))\n'
                 '    main._record_power_line(SimpleNamespace())\n'
                 '    assert recorded == []\n'
                 '\n'
                 '\n'
                 'def test_lead_hold_line_below_floor(monkeypatch):\n'
                 '    """Near-miss is never silent: conf below floor prints WHY it held."""\n'
                 '    open_corridor(monkeypatch)\n'
                 '    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 45.0)\n'
                 '    monkeypatch.setattr(main.config, "POWER_LEAD_CONF_FLOOR", 30.0)\n'
                 '    monkeypatch.setattr(main.config, "POWER_LEAD_ENABLED", True)\n'
                 '    recorded = []\n'
                 '    monkeypatch.setattr(main, "_record", lambda *a, **k: '
                 'recorded.append(a))\n'
                 '    decision = hold_decision()\n'
                 '    ok, reason = _shooting_execution_gate(\n'
                 '        decision, snap(confidence=22.0, direction="UP", '
                 'class_direction="BUY"))\n'
                 '    assert decision.action == "HOLD"\n'
                 '    holds = [a for a in recorded if "POWER-LEAD" in str(a[0])]\n'
                 '    assert holds and "holds: conf 22 < floor 30" in holds[0][1]\n'
                 '\n'
                 '\n'
                 'def test_lead_hold_line_on_disagreement(monkeypatch):\n'
                 '    open_corridor(monkeypatch)\n'
                 '    monkeypatch.setattr(main.config, "AI_CONFIDENCE_THRESHOLD", 45.0)\n'
                 '    monkeypatch.setattr(main.config, "POWER_LEAD_CONF_FLOOR", 30.0)\n'
                 '    monkeypatch.setattr(main.config, "POWER_LEAD_ENABLED", True)\n'
                 '    recorded = []\n'
                 '    monkeypatch.setattr(main, "_record", lambda *a, **k: '
                 'recorded.append(a))\n'
                 '    decision = hold_decision()\n'
                 '    _shooting_execution_gate(\n'
                 '        decision, snap(confidence=35.0, direction="UP", '
                 'class_direction="SELL"))\n'
                 '    holds = [a for a in recorded if "POWER-LEAD" in str(a[0])]\n'
                 '    assert holds and "holds: class disagrees" in holds[0][1]\n',
  'marker': "4. VISIBILITY PACK (owner's law"}]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="")


def apply_all() -> int:
    changed = 0
    skipped = 0
    for name, content in NEW_FILES.items():
        path = ROOT / name
        if path.is_file() and "power-lead" in read(path):
            print(f"  SKIP (already present): {name}")
            skipped += 1
            continue
        write(path, content)
        print(f"  WRITTEN: {name}")
        changed += 1
    for spec in PATCHES:
        path = ROOT / spec["file"]
        if not path.is_file():
            print(f"ERROR: {spec['file']} not found")
            return 1
        text = read(path)
        if spec["marker"] in text:
            print(f"  SKIP (already patched): {spec['file']}")
            skipped += 1
            continue
        anchor = spec["anchor"]
        if anchor not in text:
            print(f"ERROR: anchor not found in {spec['file']}:")
            print("   " + anchor[:90].replace("\n", "\\n"))
            return 1
        backup = path.with_name(path.name + BACKUP_SUFFIX)
        if not backup.exists():
            write(backup, text)
        new = text.replace(anchor, spec["replacement"], 1)
        if spec["marker"] not in new:
            print(f"ERROR: marker missing after patch in {spec['file']}")
            return 1
        write(path, new)
        changed += 1
        print(f"  PATCHED: {spec['file']}")
    print()
    print(f"visibility pack install complete: {changed} change(s) applied, "
          f"{skipped} already present.")
    print("Backups: *.vispack.bak (never stage them).")
    print("No .env changes needed. Loop restart picks it up on the next cycle.")
    return 0


def rollback() -> int:
    restored = set()
    for spec in PATCHES:
        path = ROOT / spec["file"]
        backup = path.with_name(path.name + BACKUP_SUFFIX)
        if backup.is_file():
            write(path, read(backup))
            restored.add(spec["file"])
    for name in NEW_FILES:
        path = ROOT / name
        if path.is_file() and "power-lead" in read(path):
            path.unlink()
            print(f"  REMOVED: {name}")
    for f in sorted(restored):
        print(f"  RESTORED: {f}")
    print(f"rollback complete: {len(restored)} file(s) restored.")
    return 0


def main() -> int:
    if "--rollback" in sys.argv:
        return rollback()
    return apply_all()


if __name__ == "__main__":
    sys.exit(main())
