# Shooting plan update — 2026-10-05

This is a narrow replacement pack, not a full repository snapshot. Copy only files listed below into the existing repository root after reviewing your working-tree changes. Do not extract the whole review workspace over the checkout.

## Source files to replace

- `shooting_team.py` — returns both passive LIMIT and aggressive MARKET plan alternatives; strict queue/news checks; plan expiry; explicit not-assessed risk blockers. It still never transmits orders.
- `paper_entry_simulator.py` — refuses expired/non-PLAN_ONLY plans and cancels on excessive market drift. Simulated fills only.
- `step2_market_analysis.py` — tags queue estimates with timestamp, symbol and quote snapshot; sends plan timestamp/symbol to Shooting.

## Tests and current-state documents

The pack also contains the updated named tests and POWER v2 docs. They are for the same repository root and are not production source modules.

## Validation already run

- `pytest -q test_shooting_team.py test_paper_entry_simulator.py test_power_v2_shooting_handoff.py` — 30 passed.
- Full repository unit suite: `pytest -q` — 109 passed.
- `python -m py_compile shooting_team.py paper_entry_simulator.py step2_market_analysis.py` — passed.

The Power thresholds were not relaxed. Synthetic tests are not market evidence. No raw timestamped Bookmap/trade replay CSV was present in this workspace, so the latest `NEITHER` cannot honestly be diagnosed as low volume versus missing/stale evidence, regime uncertainty, or weak/disagreeing force. The attached docs record this limitation instead of manufacturing a conclusion.

## Safety boundary

- A `GO` remains an analysis-only plan with `execution_status=PLAN_ONLY`.
- The default chosen plan is a passive `LIMIT`. The aggressive `MARKET` alternative is advisory/ineligible until a separate independently validated trigger is supplied.
- A queue estimate is trusted only if its probability, volumes, timestamp, symbol, side, order price, and quote snapshot all validate; otherwise it is explicitly unknown.
- Missing, unrecognized, WARNING, and BLACKOUT news states block a plan.
- Account risk sizing/exposure is `NOT_ASSESSED` and listed as an execution blocker.
- No broker order, live order setting, GitHub upload, or push was performed.
