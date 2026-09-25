# Gold-BookMap — full handover report
**Written 2026-09-25. Build in the repo: `audit-2026-09-24u`.**

If this conversation is lost, hand this file to any assistant and say:
*"Read HANDOVER.md, JUDGE_MAP.md, JUDGE_GUIDE.md, HORIZON_REVIEW.md, WALL_STOPS_DESIGN.md and
TODO_BOOKMAP.md, then continue."* That is enough to carry on without repeating work.

---

## 1. What this project is

A gold (XAUUSD) trading robot driven by **Bookmap** data, ported from a NinjaTrader design.
It runs continuously and trades the Budapest session **08:00–23:00 local (06:00–21:00 UTC)**.

**The product is not only the robot — it is the audit/test system.** The owner's words:
*"the real goal and main thing here is focusing in the app by itself and making the test system
work perfectly, which can give info about everything done during the day."*

**The daily deliverable is ONE self-contained HTML file** — `data/report_<date>.html`. Run once,
open one file, see everything.

## 2. How a trade is decided (the current chain)

```
step1_data_acquisition   Bookmap tape + L3 depth
step2_market_analysis    33 judges each vote -1 / 0 / +1 with a weight
                         score = sum(dir * weight) / sum(weight)      -> strength 0-100
                         macro opposition rule can shrink it up to 50% or cap to NEUTRAL
                         confidence = agreeing judges / total judges   <-- A HEAD COUNT
step3_ai_decision        the AI may VETO (it never picks trades)
step4_mt5_execution      gate: confidence >= 50; structural SL/TP; sends to MT5
step5_monitoring         tracks the open position
audit_day.py             grades everything afterwards -> the HTML report
```

`.env` verdict, **do not edit**: P3 active, gate 50, AI gate 6, spread 0.50.
**Never put `.env` in a zip** — show the owner which lines to change instead. `.env.example` is fine.

## 3. What is in the repo right now (build 24u)

| file | bytes | what changed recently |
|---|---|---|
| `audit_day.py` | 244910 | one-page report, cross-day scoreboard + dropdown, `RETIRED_JUDGES` table |
| `step2_market_analysis.py` | 211391 | `_may_vote()` gate, 8 judges retired, macro brake kept, retirement logged each cycle |
| `config.py` | 30153 | `RETIRED_JUDGES`, `MACRO_BRAKE_ENABLED` |
| `tools/selftest_audit_day.py` | 43741 | fixture clock frozen at 12:00 UTC (midnight bug) |
| `tools/insight.py` | 33659 | em-dash fix |
| `PANEL_ROSTER.md` | 4853 | the 33 judges + the AI |

Verify any install with: `grep -c "audit-2026-09-24u" audit_day.py` → must print `1`.

## 4. The 8 retired judges (owner's decision, 2026-09-24)

`macro_yield`, `macro_dxy`, `macro_vix` — 120-min clock, wrong horizon for a 15-min scalp.
`vwap_zscore`, `spoof_invert_loose`, `iceberg_legacy`, `l3_ofi_streak`, `delta_pressure` — duplicates.

**How it was done, and why:**
- They **still write their note lines**, so the audit keeps grading them → free A/B on what was silenced.
- **The macro brake survives.** `macro_pairs` still fills, so *"fight the macro only with flow
  evidence"* still works. Had it been deleted, `m_w` would fall to 0 and the brake would switch off
  **silently**. `MACRO_BRAKE_ENABLED=0` disables it deliberately.
- `l3_ofi_streak` was **not a separate vote** — it is the L3 OFI vote with its weight doubled on a
  streak. Retiring it removes the 2× boost only; the order-flow vote itself stays.
- Restore any of them with `RETIRED_JUDGES=` in `.env` (empty = all 33 back).
- **Takes effect only when `main.py` is restarted.**

## 5. ⚠ FIVE REAL DEFECTS FOUND, NOT YET FIXED

These are the most valuable findings in the whole conversation. Do not lose them.

### 5.1 Teams are assigned by list position, not by name
`step2_market_analysis.py` ~line 2868:
```python
trend_votes  = votes[:5]
flow_votes   = votes[5:15]
whale_votes  = votes[15:35]
struct_votes = votes[35:45]
world_votes  = votes[45:]
```
`V6_4TEAMS_ENABLED` defaults to **1**, and `score = ensemble_score` drives the live direction.
Vote #7 is on the "flow" team because it is seventh. **One silent judge shifts every judge behind
him into another team**, every cycle. `world_votes` is usually empty. The code's own comments admit
it: *"This is approximate"*, *"For simplicity"*, and a loop containing only `pass`.
**Fix:** an explicit `judge -> team` dictionary.

### 5.2 The gate counts heads, not weight
```python
agreement  = sum(1 for s, _ in votes if direction matches)
confidence = 100.0 * agreement / max(len(votes), 1)
```
The 50 gate compares against `confidence`. **Weight does not appear in it.** A 0.4-weight judge
cancels a 1.5-weight judge one-for-one. Removing the 8 judges therefore changes what "50" means —
expect more trades until the gate is re-tuned.

### 5.3 Judges are graded on a trade the robot never takes
Robot: `STOP_LOSS_ATR_MULT=2.0`, `TAKE_PROFIT_ATR_MULT=3.5`.
Scalping judges are graded at SL 1.0 / TP 1.5. So `vwap_trend` can be 83% "right" and earn +19.5,
while `footprint_delta` is 68% right and **−44.1**. The percentage answers a smaller question than
the money does. **Proposed fix: a second "robot clock" column** grading every judge at 2.0/3.5.

### 5.4 Weights float by up to ~4× at runtime
`PANEL_ROSTER.md` weights are **nominal, not actual**:
`REGIME_ADAPTIVE=1` (trend ×2.0 / mean-rev ×0.5), `V6_KILLZONES_ENABLED=1` (another ×2.0, compounds),
`range_boost` (whale & iceberg ×2 in RANGE), `persist_mult` (iceberg by wall age),
`VOLATILITY_HIGH_MULT=1.5`, `macro_high_w` (macro raised during HIGH news).
**To freeze everything flat for a clean redesign** (owner's `.env`, not ours to edit):
```
REGIME_ADAPTIVE=0
V6_KILLZONES_ENABLED=0
V6_4TEAMS_ENABLED=0
VOLATILITY_HIGH_MULT=1.0
```

### 5.5 Three judges speak and are never counted
`judge_panel.py` parses `whale_balanced`, `trend_macd`, `sma20`. None is in `PANEL_ROSTER.md`.
Either legacy log lines or three ignored judges. Unresolved.

**Also confirmed safe:** nothing self-learns. The robot never reads `judge_weight_suggestions.json`,
and no code writes `.env` or `config.py`.

## 6. The panel's structural problem (measured, not guessed)

Grouped by what they actually read:
```
THE TAPE     4.10 ┐
THE BOOK     5.70 ├─ ALL ONE BOOKMAP FEED = 14.70 of 22.70 = 65%
HIDDEN SIZE  4.90 ┘
STRUCTURE    4.70 ─── built from the same price+volume ──> 85% one feed
THE WORLD    3.30 ─── the only outside opinion ─────────> 15%  (now 1.5 after retirement)
```
Only **11 judges know something no other judge can**: `iceberg`, `spoof_invert`, `absorption`,
`cvd_divergence`, `microprice`, `sweep`, `queue_pos`, `mtf`, `htf_poc`, `news_sentiment`,
`macro_yield`. The other 22 are largely re-phrasings — full detail in `JUDGE_GUIDE.md`.

## 7. Where the conversation stopped

- **DONE and installed:** 24u. Selftest PASSED, all 15, verified on the owner's machine after
  midnight (the clock fix held).
- **NOT restarted yet:** the retirements activate on the next `main.py` restart.
- **PAUSED by the owner:** `WALL_STOPS_DESIGN.md` (wall-aware SL/TP). Design written, **no code**.
  Three open questions at the end of that file.
- **NEXT, by the owner's instruction:** *redesign the judge voting system from zero*, with
  **explicit confirmation before every change**.

## 8. Standing rules the owner has set (do not break these)

1. Explain like to a 5-year-old — plain words, short sentences, analogies — but stay professional.
2. **Never** ship `.env`. Show the lines; he edits them.
3. Every runnable deliverable must be inside **ONE zip per change set**, verified with `unzip -l`
   and a clean-unzip rehearsal first. Never tell him to run a file he does not have.
4. Always give **file sizes** and an `ls -la` step so he can confirm the right build.
5. Give the **complete remaining route**, with branch conditions — not one step at a time.
6. He runs live and pastes raw output; verify his numbers against your own runs; **admit defects
   explicitly**.
7. No reminder/scheduled-task tooling — he removed it on purpose.
8. His data lives on his machine, not in the assistant's workspace. Missing data there is not a finding.
9. He monitors from a second Git Bash while `--loop` runs — monitoring commands must stay read-only.
10. Nothing changes in the robot without his confirmation.

## 9. Everyday commands

```bash
bash daily_check.sh 2026-09-25          # the night run -> data/report_2026-09-25.html
python audit_day.py --date 2026-09-24   # re-audit one day
python audit_day.py --scoreboard 5      # cross-day (also embedded in the day report now)
python audit_day.py --days 3            # 3-day window
python audit_day.py --weight-ab 3       # weight A/B study
python tools/selftest_audit_day.py      # must print: SELFTEST PASSED: all 15 tests
```
The selftest opens a browser tab on the **demo day 2099-01-05** — synthetic, not his data.

## 10. Still open

Owner's item **"5."** from an earlier message arrived empty and was never answered.
Model cooldown in step3 (C5) — proposed, undecided.
`AI_AS_VOTE` — blocked until ≥3 graded days exist.
Tuesday 29th: weight review + `--gate 63` / `--gate 45`.
Judge-correlation matrix — explained, agreed valuable, **not built**.
