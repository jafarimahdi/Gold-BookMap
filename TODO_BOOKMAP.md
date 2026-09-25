# Gold-BookMap — open list
Last updated: 2026-09-24, after 24t and a review of every item.
Newest build: `Gold-BookMap-24t.zip` (marker `audit-2026-09-24t`).

Rule for this file: nothing is deleted when it is done — it moves to DONE at the bottom with its
build number. Items deleted as *wrong* or *not useful* go to RETIRED, with the reason, so they are
not rediscovered in three weeks as fresh findings.

---

## A. WAITING ON YOU (4) — kept exactly as agreed

| # | Item | What I need from you |
|---|------|----------------------|
| A1 | **Judge-correlation matrix** | The big one. Full note in section D. **This is what we do next.** |
| A2 | **Your item "5."** | Your earlier message had a numbered point 5 that arrived empty. Still unknown. |
| A3 | **Model cooldown in step3 (C5)** | If the AI model errors or times out N times in a row, stop calling it for X minutes instead of retrying every cycle. Yes / no / different numbers. |
| A4 | **Install `Gold-BookMap-24t.zip`** | Built, rehearsed, not yet installed. `audit_day.py` 244229 · `PANEL_ROSTER.md` 4853 · `tools/insight.py` 33659. |

## A-URGENT-2. THE SELFTEST FAILS AFTER MIDNIGHT (found 2026-09-25 01:19)

`python tools/selftest_audit_day.py` now ends `SELFTEST FAILED` with:
`contract: the day's decisions counted missing from the report` and `contract: sent to MT5 missing`.
The test expects `decisions logged that day: 2` and `signals that reached MT5: 1` (lines 301-302 of
the selftest), and its fixture no longer produces them once the local date rolls over.
**Proven not to be caused by the 24u edits:** the pre-patch `audit_day.py` fails identically.
The fixture's clock must be pinned to its own day instead of following `datetime.now(BUDA)`.
**This blocks shipping 24u** — no zip goes out with a red selftest.

## A-URGENT. FOUND 2026-09-24 WHILE MAPPING THE PANEL

**The 4-teams grouping assigns judges to teams by their POSITION in the votes list, not by name.**
`step2_market_analysis.py` ~line 2868: `trend_votes = votes[:5]`, `flow_votes = votes[5:15]`,
`whale_votes = votes[15:35]`, `struct_votes = votes[35:45]`, `world_votes = votes[45:]`.
`V6_4TEAMS_ENABLED` defaults to 1, and `score = ensemble_score` drives the live direction and
strength — so this is in your running signal, not a side path. One silent judge shifts every judge
behind him into a different team; `world_votes` is usually empty, so macro/news often join no team.
**Fix:** an explicit `judge -> team` dictionary. Small, safe, no new maths. Do it *after* the matrix
measures the correct membership. Full write-up in `JUDGE_MAP.md` section 3.

## B. TIME-BOUND (3)

| # | When | Item |
|---|------|------|
| B1 | **Tonight after 23:00** | `bash daily_check.sh 2026-09-24`, then open `data/report_2026-09-24.html` — that one file only. |
| B2 | **Tuesday 29th** | Weight review, plus the gate experiment: `--gate 63` and `--gate 45` against the same tape, compared. |
| B3 | **When ≥3 graded days exist** | `AI_AS_VOTE` — the AI as a weighted voting judge, not only a veto. Blocked until the veto record can set that weight honestly. |

## C. DECISIONS ON RECORD (not tasks — do not reopen)

- **Three parsed-but-unweighted names.** `judge_panel.py` reads `whale_balanced`, `trend_macd` and
  `sma20` from the logs, but none of the three is in `PANEL_ROSTER.md`, and only `whale_balanced`
  appears once in `audit_day.py`. So they speak and are not counted. Either legacy log lines, or
  three judges being ignored. One check, low priority — noted so it is not lost.

- **The 51 silent `except` blocks in `step2_market_analysis.py` stay as they are.** Wrapping them
  risks the live loop for no reporting gain. Rejected on purpose, by you and me.

## D. IDEAS — CHECK LATER (3)

Confirmed by you on 2026-09-24. None started. None touches the live loop — all report-side.

### D1. Judge-correlation matrix ← NEXT
**The problem, in your numbers.** Five judges read the same order book:
`l3_net_flow` 1.5 + `l3_ofi_streak` 1.0 + `l3_imbalance` 0.8 + `l3_large_ofi` 0.6 + `l3_aggr_limit` 0.7
= **4.6 of weight on one single idea.** Meanwhile `macro_yield` sits alone at 0.8.

So when the panel reaches your 50% gate, that does not necessarily mean "half the panel agrees".
It can mean *one* opinion, said five times, outvoting four independent ones.
**33 judges are probably closer to 8 real ideas.**

*Five-year-old version:* five friends recommend the same pizza place — but they share one phone and
all saw the same advert. That is one recommendation, not five.

**What it does:** for every pair of judges, count how often they voted the same way on the same bar.
High agreement = one idea wearing two hats. Output: a coloured grid plus a plain list — "these N
judges are really one voice, combined weight X".

**What it could change (decide only after seeing it):**
- group the clones and cap each *group's* weight instead of each judge's
- or compute the gate on *ideas that agree*, not *judges that agree*
- or change nothing and keep it as a warning label

**Cost:** one pass over the diary you already write. No new data, no live-loop change.
**Risk:** it may show the 50% gate has been measuring the wrong thing for weeks. That is the point.

### D2. A "what changed since yesterday" column
*Your words: "a judge that fell from 80% to 40% is more urgent than one that's been mediocre all week."*

Cheapest real value on the list. A judge stuck at 55% is boring; one that falls off a cliff overnight
means something broke — feed, venue, regime — and nothing in the report shouts about it today.

**Shape:** one column in the panel table, `right% today` vs `right% yesterday`, arrow and delta,
coloured only when the move is ≥15 points **and** both days have ≥3 calls. That sample-size guard is
the whole job — without it, a judge with two calls fakes a cliff every night.
**Cost:** tiny; the cross-day pass already reads yesterday.

### D3. "If I set this judge's weight to X"
*Your words: "a slider showing what the day's signals would have looked like at a different weight,
before you touch `.env`."*

Drag a weight in the browser, watch the day's whatif points and gate decisions recompute from numbers
already in the page. **Nothing real changes** — the report never touches `.env`.

**Honest limit:** it replays only the votes actually recorded. It can say "at weight 0.5 these 12
trades would not have passed the gate"; it cannot say what the robot would have done *next* after
behaving differently. Backtest, not time machine — and that warning belongs printed on the page,
because it looks more authoritative than it is.
**Cost:** medium (per-decision vote data must be embedded; page grows).

---

## DONE

- **24t** — cross-day scoreboard + dropdown embedded inside the day report; past-day summaries inline,
  deep detail by link; `--crossday-days N` / `--no-crossday`.
- **24s** (folded into 24t, never shipped alone) — literal `&mdash;` fixed at all 4 sites in
  `tools/insight.py` plus `add_section`; sections collapsible; panel defaults to "graded today";
  descriptions behind a toggle.
- **24r** — cross-day scoreboard: pre-rendered windows, per-day views, day strips.
- **24q** — cross-day scoreboard, first cut.
- **24p** — friendly single page.
- **24n** — AI VETO RECORD; 33 judges + the AI graded by one method, in one place.
- **24k→24m** — `RISK_REVIEW_2026-09-24.md`, 12 findings re-checked, 15 of 16 shipped.
- **24j** — DST clock fix. **24h/24i** — roster, judges page, MT5 cross-check. **24g** — install docs + 4 features.
- Earlier — the 9 fixes; safe push `41ae850`, tag `good-2026-09-24b`; `.env` confirmed never leaked.

## RETIRED (checked 2026-09-24, deleted with reason)

- **"Trades page: the winning-day branch was never tested."** — **Wrong, there is no such branch.**
  Checked `trades_page()` in `tools/insight.py`: P&L is rendered by `_fmt(pnl, 2, True)`, which prints
  a signed number for any value. The page does not branch on the sign of P&L at all, so a profitable
  day needs no untested code path. Phantom finding, removed.
- **"The nightly ritual: also run `--days 3`, `--date 2026-09-17`, `--date 2026-09-23`, `--scoreboard 5`."**
  — **Redundant since 24t.** The cross-day view is now inside `report_<date>.html`, built from the same
  single pass, so running `--scoreboard 5` separately can only show you the same numbers in a second
  file. The commands still exist and still work if you ever want them; they are no longer a nightly task.
  Only `--weight-ab 3` had independent value, and that is folded into B2 (Tuesday).
- **"The cross-day window is thinner than it looks (older days pre-date the v7.1 diary)."**
  — **True but not a task.** It self-heals as days accumulate, and 24t already names every unusable day
  with its reason on the page itself. Nothing to do, nothing to remember.
