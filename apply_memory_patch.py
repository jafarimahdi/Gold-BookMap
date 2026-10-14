#!/usr/bin/env python3
"""apply_memory_patch.py -- TEAM MEMORY: report cards for the AI doorman (v1.0, 2026-10-14).

The owner's idea, made real the safe way: the judges vote fresh every bell
(reproducible, twin-provable), but the company remembers -- and now the AI
doorman (Captain 5, the weigher of votes) reads the judges' REPORT CARDS in
every prompt, built from the nightly audit's own grades.

2 surgical inserts into step3_ai_decision.py:

  1. `_judge_report_card_block(snapshot)` -- reads data/judge_report_card.json
     (built by judge_report_card.py from the audit's judge_panel_*.csv) and
     returns a compact "JUDGE REPORT CARDS" block, or "" when there is
     nothing trustworthy to show.
  2. build_prompt() inserts that block between the session context and the
     market metrics. When the block is "" the prompt is BYTE-IDENTICAL to
     the unpatched build (fail-closed).

Laws baked in:
  * FAIL-CLOSED: missing/corrupt/stale card, no snapshot timestamp, any
    exception -> "" -> prompt unchanged. No new failure modes for the doorman.
  * NO LOOK-AHEAD: the card is used only if EVERY source day is strictly
    BEFORE the snapshot's own day. The guard reads the SNAPSHOT timestamp,
    never the wall clock -- a twin replay of any recorded day sees exactly
    the card that existed before that day (rebuild it first with
    `python judge_report_card.py --as-of <day>`).
  * STALENESS: if the newest graded day is more than AI_CARD_MAX_STALE_DAYS
    (default 4) behind the trading day, the card is ignored -- old memory is
    worse than no memory in a changed regime.
  * NOISE FLOOR: judges with fewer than AI_CARD_MIN_SCORED (default 20)
    scored calls in the window are not shown.
  * FROZEN WEIGHTS UNTOUCHED: this informs the AI weigher; it changes no
    judge weights (the audit's suggestion file says "review, not auto-apply"
    and the same law applies here).

Composes with apply_doorman_patch.py (different code regions, either order).

Run from the project root:  python apply_memory_patch.py
"""
import py_compile
import shutil
import sys
from pathlib import Path

TARGET = "step3_ai_decision.py"
BACKUP = TARGET + ".pre_memory"

MARKER_HELPER = "def _judge_report_card_block(snapshot=None):"
MARKER_INSERT = "+ _judge_report_card_block(snapshot)"

# ---------------------------------------------------------------- edit 1 ----
HELPER_ANCHOR = '''class AIDecisionEngine:
    """Wraps the Gemini API into a decide(snapshot) -> Decision call."""
'''

HELPER_INSERT = r'''_JUDGE_CARD_CACHE = {}


def _judge_report_card_block(snapshot=None):
    """TEAM MEMORY (2026-10-14): per-judge report cards for the AI weigher.

    Reads data/judge_report_card.json (built by judge_report_card.py from the
    nightly audit's judge_panel_*.csv grades) and returns a compact prompt
    block, or "" when there is nothing trustworthy to show.

    Fail-closed: any problem -> "" -> the prompt is byte-identical to a
    no-card build. No look-ahead: the card is used only if every source day
    is strictly BEFORE the snapshot's own day; the guard reads the snapshot
    timestamp, never the wall clock (twin-replay safe).
    """
    try:
        from pathlib import Path
        from datetime import date

        ts = getattr(snapshot, "timestamp", None)
        if ts is None:
            return ""
        try:
            aware = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            today = aware.astimezone(timezone.utc).date().isoformat()
        except Exception:
            return ""

        def _knob(name, default):
            import os
            try:
                return int(os.environ.get(name) or default)
            except Exception:
                return default

        card_path = Path(__file__).resolve().parent / "data" / "judge_report_card.json"
        try:
            mtime = card_path.stat().st_mtime
        except OSError:
            return ""
        cache_key = (str(card_path), mtime, today)
        if _JUDGE_CARD_CACHE.get("key") == cache_key:
            return _JUDGE_CARD_CACHE.get("block", "")

        card = json.loads(card_path.read_text(encoding="utf-8"))
        days = [str(d) for d in (card.get("source_days") or [])]
        block = ""
        if days and all(d and d < today for d in days):
            max_stale = _knob("AI_CARD_MAX_STALE_DAYS", 4)
            min_scored = _knob("AI_CARD_MIN_SCORED", 20)
            try:
                age = (date.fromisoformat(today) - date.fromisoformat(max(days))).days
            except Exception:
                age = 10 ** 6
            judges = []
            for j in (card.get("judges") or []):
                try:
                    judges.append((str(j["judge"]), int(j["scored"]),
                                   float(j["hit_pct"]), float(j["pts_per_call"])))
                except Exception:
                    continue
            judges = [t for t in judges if t[1] >= min_scored]
            if age <= max_stale and judges:
                judges.sort(key=lambda t: (-t[3], -t[2], t[0]))
                lines = []
                for name, scored, hit, ppc in judges:
                    if ppc > 0 and hit >= 55.0:
                        tag = "STRONG"
                    elif ppc < -0.25 or hit < 40.0:
                        tag = "WEAK"
                    else:
                        tag = "MIXED"
                    lines.append("- {}: {:.0f}% right, {:+.2f} pts/call, {} scored calls [{}]"
                                 .format(name, hit, ppc, scored, tag))
                block = ("JUDGE REPORT CARDS (each judge's real track record over {} graded "
                         "day(s) {}..{}, scored on its own clock by the nightly audit; weigh "
                         "the panel votes accordingly):\n{}\n(tag rule: STRONG = pts/call>0 "
                         "and hit>=55%; WEAK = pts/call<-0.25 or hit<40%; else MIXED)\n\n"
                         .format(len(days), min(days), max(days), "\n".join(lines)))
                try:
                    logger.info("STEP 3: judge report card in prompt (%d judges, %s..%s)",
                                len(judges), min(days), max(days))
                except Exception:
                    pass
        _JUDGE_CARD_CACHE["key"] = cache_key
        _JUDGE_CARD_CACHE["block"] = block
        return block
    except Exception:
        return ""


''' + HELPER_ANCHOR

# ---------------------------------------------------------------- edit 2 ----
PROMPT_ANCHOR = r'''            + json.dumps(payload["session_context"], indent=2) + "\n\n"
            "MARKET ANALYSIS METRICS (JSON) — trimmed to last {} bars, {} headlines for speed:\n".format(max_bars, max_headlines)'''

PROMPT_NEW = r'''            + json.dumps(payload["session_context"], indent=2) + "\n\n"
            + _judge_report_card_block(snapshot)
            + "MARKET ANALYSIS METRICS (JSON) — trimmed to last {} bars, {} headlines for speed:\n".format(max_bars, max_headlines)'''


def main() -> int:
    path = Path(TARGET)
    if not path.exists():
        print(f"[fail] {TARGET} not found next to this script (run from the project root).")
        return 1
    src = path.read_text(encoding="utf-8")

    if MARKER_HELPER in src and MARKER_INSERT in src:
        print("[skip] memory patch already applied -- nothing to do.")
        return 0
    if MARKER_HELPER in src or MARKER_INSERT in src:
        print("[fail] half-applied state found (one marker present, one missing).")
        print("       Restore step3_ai_decision.py.pre_memory and re-run, or fix by hand.")
        return 1

    for name, anchor in (("helper anchor", HELPER_ANCHOR), ("prompt anchor", PROMPT_ANCHOR)):
        n = src.count(anchor)
        if n != 1:
            print(f"[fail] {name} found {n} times (expected exactly 1) -- file changed?")
            print("       NOT writing anything. The live file is untouched.")
            return 1

    # one backup, made only once (it preserves the state before this patch)
    backup = Path(BACKUP)
    if not backup.exists():
        shutil.copy2(TARGET, BACKUP)
        print(f"[ok] backup: {BACKUP}")
    else:
        print(f"[ok] backup already exists, kept: {BACKUP}")

    new = src.replace(HELPER_ANCHOR, HELPER_INSERT, 1)
    new = new.replace(PROMPT_ANCHOR, PROMPT_NEW, 1)
    path.write_text(new, encoding="utf-8")
    print("[ok] 1/2 helper planted: _judge_report_card_block() before class AIDecisionEngine")
    print("[ok] 2/2 build_prompt inserts the report-card block (empty card = byte-identical prompt)")

    try:
        py_compile.compile(TARGET, doraise=True)
    except Exception as e:
        shutil.copy2(BACKUP, TARGET)
        print(f"[fail] py_compile failed after patch: {e}")
        print(f"       restored {TARGET} from {BACKUP} -- live file is safe.")
        return 1
    print("[ok] py_compile passed")
    print()
    print("Next: python judge_report_card.py   (builds data/judge_report_card.json from your")
    print("      existing judge_panel_*.csv audit nights), then restart the app.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
