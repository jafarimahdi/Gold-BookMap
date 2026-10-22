# Gold BookMap — Quick Start

**Use this page for current operating steps.** Older version labels in README and other text files are historical; this update does not change the official application version.

## Before you start

- Keep the app in **DEMO / paper-first** mode. Do not change `.env`, thresholds, caps, or trading authorization just to make a check pass.
- The old `Gold-BookMap-market-quality-guards-20261022.patch` is **not the corrected patch**. Do not apply it. The easiest replacement is `Gold-BookMap-safe-update-20261022.patch` from the hand-off.
- Do not run root-level `apply_*.py` files as installers. They are historical patch helpers, not the normal startup process.
- A separate test worktree may not contain your private `.env`; that is intentional. Do not copy it there just to clear a warning, and do not use the test worktree for normal trading.

## 1. Check the local setup safely

From the project folder, run:

```bash
python main.py --doctor
```

The doctor checks local files, required packages, configured data source, and the effective execution settings. It does **not** call the AI, connect to MT5, verify the live feed or account, place orders, edit `.env`, or write run-config/decision evidence. The existing `config.py` import may ensure the empty `data/` and `logs/` folders exist. It does not print `.env` contents or change thresholds. A PASS result is a local readiness check—not proof that a live feed or running MT5 account is healthy.

## 2. Run without opening new positions

For one cycle:

```bash
python main.py --no-new-entries
```

For a continuous loop:

```bash
python main.py --no-new-entries --loop
```

This mode blocks new Python entries and actionable EA signals. **It still runs analysis (and may use the AI) and still manages/monitors existing positions**, including risk-reducing closes or modifications. It is not a full MT5 shutdown or a no-API-cost mode. The run summary identifies the selected mode and explains any entry block.

Press `Ctrl+C` to stop a continuous loop.

## 3. Only if you deliberately want configured DEMO entries

Normal one-cycle and loop modes are:

```bash
python main.py
python main.py --loop
```

These are **not test-only commands**. They can open a DEMO position when `TRADING_ENABLED`, execution mode, the signal, and all safety gates allow it. `ALLOW_LIVE_TRADING=0` blocks orders on a REAL account; by itself, it does **not** disable DEMO orders. The separate broker quote check and other gates remain in force.

Do not use normal mode just to check whether the app starts. Use `--doctor` or `--no-new-entries` for that purpose.

## What the app does each cycle

1. Acquires market data.
2. Analyzes signals and market quality.
3. Requests an AI decision when the pipeline calls for one.
4. Applies safety/risk gates before allowing a new entry.
5. Continues existing-position management and monitoring.
6. Writes the decision and quality evidence to the audit files.

A failed hard gate blocks a **new entry**; it does not remove the system's strategy logic. The fix prevents a POWER carry from overriding that failed gate.

## If a check says WARN or BLOCK

- Read the reason printed next to it; do not guess.
- Do not lower a threshold, raise a cap, or edit `.env` just to clear the message.
- Keep the complete output for review. The doctor cannot certify account type, live-feed timing, or runtime behavior.
- For a code update, test in a separate Git worktree first. Never apply a patch over a dirty owner checkout or copy the sandbox's `.env`.

## Patch and test commands

For the easiest install, use the single combined patch `Gold-BookMap-safe-update-20261022.patch`. It applies to base SHA `70ba1bc20fd0df2b6be9aff925f2f4ebc5b766cc`. From a separate test worktree at that base, check and apply it:

```bash
git apply --check "/path/to/Gold-BookMap-safe-update-20261022.patch"
git apply "/path/to/Gold-BookMap-safe-update-20261022.patch"
python -m pytest -q test_main_shooting_execution_gate.py test_market_quality_guards.py test_operator_workflow.py
python -m pytest -q
git diff --check
```

For a separately reviewable sequence instead, apply `Gold-BookMap-safe-market-quality-20261022-r1.patch` first, then `Gold-BookMap-operator-simplification-20261022.patch` without resetting the worktree. The second patch is only for use after the first.

If `git apply --check` or any test fails, stop; do not try to force the patch. Do not start normal trading mode as a substitute for tests.
