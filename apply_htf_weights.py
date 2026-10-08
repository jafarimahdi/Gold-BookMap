#!/usr/bin/env python3
r"""
apply_htf_weights.py - the best judge gets a tunable (2026-10-08)
==================================================================
On 2026-10-07 the judge leaderboard said: htf_poc is the best earner
(+966 pts, +2.37 pts/call). Its three vote weights were the ONLY ones among
the top judges still HARDCODED inside step2_market_analysis.py:

    H1 magnet          votes.append((+/-1.0, 0.7))
    H1 mean-reversion  votes.append((+/-1.0, 0.4))
    H4 magnet          votes.append((+/-1.0, 0.9))

whale_walls (the worst earner, -2.38/call) is ALREADY tunable from .env
(L3_WHALE_CLOSE_WEIGHT / MID / FAR / L3_RANGE_BOOST). So when the week
roll-up says "raise htf_poc, lower whale_walls", the whale half is one .env
line - and the htf half would have been a code edit on the live analyser.
This patch makes it a .env line too.

WHAT CHANGES IN BEHAVIOUR: NOTHING. The defaults are the exact old numbers
(0.7 / 0.4 / 0.9). No weight is changed by this script. The only visible
difference: the three HTF notes now carry "w 0.7" like every other weighted
judge, so the diary records the weight that was really applied (TEAM
AGREEMENTS law 2: say something the tape can check).

New .env keys (optional - leave them out and nothing changes):

    HTF_POC_H1_WEIGHT=0.7        # H1 POC within 1 ATR -> magnet vote
    HTF_POC_H1_FAR_WEIGHT=0.4    # H1 POC > 2 ATR away -> mean-reversion vote
    HTF_POC_H4_WEIGHT=0.9        # H4 POC within 1.5 ATR -> strong magnet vote

SECOND FINDING (found while testing this patch, measured, not guessed):
the diary RECORDER never saw the most common htf_poc vote. judge_panel.py
(and the identical copy embedded in step2) match htf_poc notes with

    ...price.*?magnet.*?-> (BUY|SELL)|(?:mean reversion|strong| ) (BUY|SELL)

The left half needs "magnet" BEFORE "->" (the note says "-> BUY magnet", so
it never fits) and the right half needs TWO spaces before BUY/SELL. Only the
"strong" H4 notes and the "mean reversion" notes matched. Result: every plain
H1 magnet vote (w 0.7) was missing from judge_votes since v7.1, so the
leaderboard graded the best judge on a SUBSET of its calls. audit_day.py's own
retro-parse regex matched those plain magnets - but had its own gap (below).
This touches RECORDING only: the trading votes list is built directly in
step2, POWER v2 treats htf_poc as context (not force), and the family gate is
shadow-first. Nothing the robot DOES changes; what the diary REMEMBERS does.

audit_day.py's retro-parse (used only for diaries WITHOUT judge_votes) had the
mirror-image gap: it matched plain magnets but not "strong"/"mean reversion".
One pattern now accepts all three note shapes and lives in all three places:

    HTF H[14] POC ([\d.]+) (?:far )?(above|below) price[^\n]*?-> (?:mean reversion |strong )?(BUY|SELL)

Touches: step2_market_analysis.py (vote block + recorder regex),
         config.py (3 declared keys), judge_panel.py (recorder regex),
         audit_day.py (retro-parse regex).
All-or-nothing: every anchor is checked BEFORE anything is written.
Backups: <file>.pre_htf_weights (gitignored by the *.pre_* rule).

Run from the project root (the folder with main.py):

    python apply_htf_weights.py
    python -m py_compile step2_market_analysis.py config.py
"""
from pathlib import Path
import shutil
import sys

ROOT = Path.cwd()
SUFFIX = ".pre_htf_weights"

# ---------------------------------------------------------------- step2 block
OLD_BLOCK = '''        # HTF POC H1/H4 votes - big timeframe volume magnets (user requested more logic/data)
        try:
            if htf_poc:
                h1_poc = htf_poc.get("H1")
                h4_poc = htf_poc.get("H4")
                if h1_poc:
                    dist_h1 = (price - h1_poc) / (volatility.atr or price*0.002) if price else 0
                    if abs(dist_h1) <= 1.0:  # within 1 ATR of H1 POC
                        if price > h1_poc:
                            votes.append((+1.0, 0.7))
                            notes.append(f"HTF H1 POC {h1_poc:.1f} below price {price:.1f} (dist {dist_h1:+.2f} ATR) -> BUY magnet")
                        else:
                            votes.append((-1.0, 0.7))
                            notes.append(f"HTF H1 POC {h1_poc:.1f} above price {price:.1f} (dist {dist_h1:+.2f} ATR) -> SELL magnet")
                    else:
                        # Price far from H1 POC -> mean reversion toward it
                        if price > h1_poc and dist_h1 > 2.0:
                            votes.append((-1.0, 0.4))
                            notes.append(f"HTF H1 POC {h1_poc:.1f} far below price {price:.1f} ({dist_h1:.1f} ATR) -> mean reversion SELL")
                        elif price < h1_poc and dist_h1 < -2.0:
                            votes.append((+1.0, 0.4))
                            notes.append(f"HTF H1 POC {h1_poc:.1f} far above price {price:.1f} ({dist_h1:.1f} ATR) -> mean reversion BUY")
                if h4_poc:
                    dist_h4 = (price - h4_poc) / (volatility.atr or price*0.002) if price else 0
                    if abs(dist_h4) <= 1.5:
                        if price > h4_poc:
                            votes.append((+1.0, 0.9))
                            notes.append(f"HTF H4 POC {h4_poc:.1f} below price {price:.1f} (dist {dist_h4:+.2f} ATR) -> strong BUY magnet (H4)")
                        else:
                            votes.append((-1.0, 0.9))
                            notes.append(f"HTF H4 POC {h4_poc:.1f} above price {price:.1f} (dist {dist_h4:+.2f} ATR) -> strong SELL magnet (H4)")
        except Exception as he:
            notes.append(f"HTF POC vote error: {he}")
'''

NEW_BLOCK = '''        # HTF POC H1/H4 votes - big timeframe volume magnets (user requested more logic/data)
        # htf_weights: the three weights are .env-tunable (HTF_POC_H1_WEIGHT /
        # HTF_POC_H1_FAR_WEIGHT / HTF_POC_H4_WEIGHT); defaults = the old hardcoded
        # 0.7 / 0.4 / 0.9, and the note carries "w X" so the diary keeps the real one.
        try:
            if htf_poc:
                h1_poc = htf_poc.get("H1")
                h4_poc = htf_poc.get("H4")
                htf_w_h1 = float(getattr(config, "HTF_POC_H1_WEIGHT", 0.7))
                htf_w_h1_far = float(getattr(config, "HTF_POC_H1_FAR_WEIGHT", 0.4))
                htf_w_h4 = float(getattr(config, "HTF_POC_H4_WEIGHT", 0.9))
                if h1_poc:
                    dist_h1 = (price - h1_poc) / (volatility.atr or price*0.002) if price else 0
                    if abs(dist_h1) <= 1.0:  # within 1 ATR of H1 POC
                        if price > h1_poc:
                            votes.append((+1.0, htf_w_h1))
                            notes.append(f"HTF H1 POC {h1_poc:.1f} below price {price:.1f} (dist {dist_h1:+.2f} ATR) w {htf_w_h1:.1f} -> BUY magnet")
                        else:
                            votes.append((-1.0, htf_w_h1))
                            notes.append(f"HTF H1 POC {h1_poc:.1f} above price {price:.1f} (dist {dist_h1:+.2f} ATR) w {htf_w_h1:.1f} -> SELL magnet")
                    else:
                        # Price far from H1 POC -> mean reversion toward it
                        if price > h1_poc and dist_h1 > 2.0:
                            votes.append((-1.0, htf_w_h1_far))
                            notes.append(f"HTF H1 POC {h1_poc:.1f} far below price {price:.1f} ({dist_h1:.1f} ATR) w {htf_w_h1_far:.1f} -> mean reversion SELL")
                        elif price < h1_poc and dist_h1 < -2.0:
                            votes.append((+1.0, htf_w_h1_far))
                            notes.append(f"HTF H1 POC {h1_poc:.1f} far above price {price:.1f} ({dist_h1:.1f} ATR) w {htf_w_h1_far:.1f} -> mean reversion BUY")
                if h4_poc:
                    dist_h4 = (price - h4_poc) / (volatility.atr or price*0.002) if price else 0
                    if abs(dist_h4) <= 1.5:
                        if price > h4_poc:
                            votes.append((+1.0, htf_w_h4))
                            notes.append(f"HTF H4 POC {h4_poc:.1f} below price {price:.1f} (dist {dist_h4:+.2f} ATR) w {htf_w_h4:.1f} -> strong BUY magnet (H4)")
                        else:
                            votes.append((-1.0, htf_w_h4))
                            notes.append(f"HTF H4 POC {h4_poc:.1f} above price {price:.1f} (dist {dist_h4:+.2f} ATR) w {htf_w_h4:.1f} -> strong SELL magnet (H4)")
        except Exception as he:
            notes.append(f"HTF POC vote error: {he}")
'''
STEP2_MARKER = 'getattr(config, "HTF_POC_H1_WEIGHT"'

# ---------------------------------------------------------------- config.py
CFG_ANCHOR = '    g["L3_TREND_BOOST"] = _ffloat("L3_TREND_BOOST", 2.0)  # TREND: OFI+aggressive 2x\n'
CFG_INSERT = CFG_ANCHOR + (
    '    # htf_weights (2026-10-08): the HTF POC judge weights, .env-tunable; defaults = old hardcoded\n'
    '    g["HTF_POC_H1_WEIGHT"] = _ffloat("HTF_POC_H1_WEIGHT", 0.7)        # H1 POC within 1 ATR -> magnet\n'
    '    g["HTF_POC_H1_FAR_WEIGHT"] = _ffloat("HTF_POC_H1_FAR_WEIGHT", 0.4)  # H1 POC > 2 ATR -> mean reversion\n'
    '    g["HTF_POC_H4_WEIGHT"] = _ffloat("HTF_POC_H4_WEIGHT", 0.9)        # H4 POC within 1.5 ATR -> strong magnet\n'
)
CFG_MARKER = 'g["HTF_POC_H1_WEIGHT"]'

# ---------------------------------------------------------------- recorder regex
# One pattern, three places. It must accept ALL THREE note shapes step2 writes:
#   "... -> BUY magnet"  "... -> strong SELL magnet (H4)"  "... -> mean reversion BUY"
# (with or without the new "w X" before the arrow).
NEW_PAT = r"HTF H[14] POC ([\d.]+) (?:far )?(above|below) price[^\n]*?-> (?:mean reversion |strong )?(BUY|SELL)"
RE_MARKER = "(?:mean reversion |strong )?(BUY|SELL)"

# live recorder: identical line in judge_panel.py and in the copy embedded in step2
# (left half needs "magnet" before "->", right half needs two spaces: plain H1 magnets never matched)
OLD_RE = r'''    ("htf_poc",           r"HTF H(1|4) POC ([\d.]+) (?:far )?(above|below) price.*?magnet.*?-> (BUY|SELL)|(?:mean reversion|strong| ) (BUY|SELL)"),'''
NEW_RE = '    ("htf_poc",           r"' + NEW_PAT + '"),  # htf_weights: all three note shapes recorded'

# retro-parse in audit_day.py (used only for diaries WITHOUT judge_votes): the mirror image,
# it matched plain magnets but not "strong" / "mean reversion". Same pattern fixes it.
AUDIT_OLD = '    ("htf_poc",           r"HTF H[14] POC ([' + chr(92) + 'd.]+) (?:far )?(above|below) price[^' + chr(92) + 'n]*?-> (BUY|SELL)"),'
AUDIT_NEW = NEW_RE


def _backup(path: Path) -> None:
    bak = path.with_name(path.name + SUFFIX)
    if not bak.exists():
        shutil.copy2(path, bak)
        print(f"backup {bak.name}")


def main() -> int:
    if not (ROOT / "main.py").exists():
        print("ERROR: run this from the project root (the folder with main.py)")
        return 2
    step2 = ROOT / "step2_market_analysis.py"
    cfg = ROOT / "config.py"
    jp = ROOT / "judge_panel.py"
    ad = ROOT / "audit_day.py"
    for p in (step2, cfg):
        if not p.exists():
            print(f"MISSING {p.name}")
            return 1
    s2 = step2.read_text(encoding="utf-8")
    cf = cfg.read_text(encoding="utf-8")
    jpt = jp.read_text(encoding="utf-8") if jp.exists() else None
    adt = ad.read_text(encoding="utf-8") if ad.exists() else None

    todo = {
        "step2_block": STEP2_MARKER not in s2,
        "step2_regex": RE_MARKER not in s2 and OLD_RE in s2,
        "config": CFG_MARKER not in cf,
        "judge_panel": (jpt is not None) and (RE_MARKER not in jpt),
        "audit_day": (adt is not None) and (RE_MARKER not in adt),
    }
    if not any(todo.values()):
        print("ALREADY step2_market_analysis.py + config.py + judge_panel.py + audit_day.py (nothing to do)")
        return 0

    # ---- check EVERYTHING before writing ANYTHING -----------------------
    problems = []
    if todo["step2_block"]:
        n = s2.count(OLD_BLOCK)
        if n != 1:
            problems.append(f"step2_market_analysis.py: HTF POC vote block found {n}x (want exactly 1)")
    if RE_MARKER not in s2:
        n = s2.count(OLD_RE)
        if n != 1:
            problems.append(f"step2_market_analysis.py: embedded htf_poc recorder regex found {n}x (want exactly 1)")
    if todo["config"]:
        n = cf.count(CFG_ANCHOR)
        if n != 1:
            problems.append(f"config.py: L3_TREND_BOOST anchor found {n}x (want exactly 1)")
    if todo["judge_panel"]:
        n = jpt.count(OLD_RE)
        if n != 1:
            problems.append(f"judge_panel.py: htf_poc recorder regex found {n}x (want exactly 1)")
    if todo["audit_day"]:
        n = adt.count(AUDIT_OLD)
        if n != 1:
            problems.append(f"audit_day.py: htf_poc retro-parse regex found {n}x (want exactly 1)")
    if problems:
        for p in problems:
            print("ERROR:", p)
        print("NOTHING was written - paste this output back.")
        return 1

    # ---- write ------------------------------------------------------------
    if todo["step2_block"] or todo["step2_regex"]:
        _backup(step2)
        edits = []
        if todo["step2_block"]:
            s2 = s2.replace(OLD_BLOCK, NEW_BLOCK, 1)
            edits.append("HTF POC weights read from config; notes carry 'w X'")
        if todo["step2_regex"]:
            s2 = s2.replace(OLD_RE, NEW_RE, 1)
            edits.append("embedded recorder regex: plain H1 magnet notes now recorded")
        step2.write_text(s2, encoding="utf-8")
        print(f"PATCHED step2_market_analysis.py ({len(edits)} edits: " + "; ".join(edits) + ")")
    else:
        print("ALREADY step2_market_analysis.py")
    if todo["config"]:
        _backup(cfg)
        cfg.write_text(cf.replace(CFG_ANCHOR, CFG_INSERT, 1), encoding="utf-8")
        print("PATCHED config.py (3 keys: HTF_POC_H1_WEIGHT 0.7 / HTF_POC_H1_FAR_WEIGHT 0.4 / HTF_POC_H4_WEIGHT 0.9)")
    else:
        print("ALREADY config.py")
    if jpt is None:
        print("SKIP judge_panel.py (not present - main.py then has no fallback recorder; fine)")
    elif todo["judge_panel"]:
        _backup(jp)
        jp.write_text(jpt.replace(OLD_RE, NEW_RE, 1), encoding="utf-8")
        print("PATCHED judge_panel.py (recorder regex: plain H1 magnet notes now recorded)")
    else:
        print("ALREADY judge_panel.py")
    if adt is None:
        print("SKIP audit_day.py (not present)")
    elif todo["audit_day"]:
        _backup(ad)
        ad.write_text(adt.replace(AUDIT_OLD, AUDIT_NEW, 1), encoding="utf-8")
        print("PATCHED audit_day.py (retro-parse regex: strong / mean-reversion shapes now recognised too)")
    else:
        print("ALREADY audit_day.py")

    print("\nBehaviour unchanged: defaults are the old hardcoded numbers; only the diary")
    print("now records every htf_poc vote with its real weight.")
    print("Next:  python -m py_compile step2_market_analysis.py config.py judge_panel.py audit_day.py   (silence = good)")
    print("       python tools/selftest_audit_day.py                                             (16/16 PASS)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
