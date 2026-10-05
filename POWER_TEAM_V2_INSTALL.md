# POWER v2 integration — current status and safe update notes

**This replaces the original staged-install guide.** That guide's branch-switching and “not integrated” instructions are obsolete. The active checkout is already on the user's feature branch; do not branch-switch or extract an entire review snapshot over it.

## Current state

- `step2_market_analysis.py` calls POWER v2 and passes `UP` / `DOWN` / `NEITHER` to Shooting.
- `shooting_team.py` returns both passive LIMIT and aggressive MARKET plan alternatives, but the aggressive option requires an independent reviewed trigger. No live order is submitted.
- Plans are `PLAN_ONLY`. The paper simulator is only a model and Escort receives only paper-filled plans.
- Power thresholds are unchanged. No captured raw trade replay was available in this workspace, so the latest `NEITHER` cannot be attributed to low volume or another specific cause.

## Safe way to apply a future update

1. Review `git status --short` in the user's checkout. Stop if anything unexpected appears; preserve all existing work.
2. Copy only the explicitly named changed files from the update package into the repository root, overwriting only those same named files after review.
3. Do not extract a full repository snapshot, timestamped backups, temporary scripts, or unrelated files over the checkout.
4. Run the listed tests locally. Tests prove code/control flow, not profitability, realistic fills, or permission to trade.
5. The user reviews and uploads/pushes to GitHub themselves. Do not enable broker orders.

## Required before any future execution-readiness review

- Replay timestamped real trades/quotes and record why each Power result was `NEITHER`; preserve feed/symbol/side-label semantics and do not weaken thresholds to manufacture more signals.
- Validate and calibrate queue estimates against actual fills; current queue probability is heuristic.
- Add and test an account-level risk/permission layer for position sizing, per-trade and aggregate risk, margin, daily loss, concurrent/correlated positions, and a kill switch.
- Revalidate plan direction, quote, news, route, bracket, and expiry immediately before any future order proposal.
- Keep broker submission disabled until independent out-of-sample and paper evidence plus all safety reviews pass and the user explicitly authorizes a separate execution change.
