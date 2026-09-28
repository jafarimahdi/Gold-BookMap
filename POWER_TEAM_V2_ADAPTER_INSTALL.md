# Next safe step: add and test the M5 adapter

You already have the v2 team and memory modules committed on `feature/power-team-m5-v2`. This adapter extension adds **three new files only** and still does not modify or connect the app pipeline.

## 1. Confirm your branch and clean checkpoint

In Git Bash:

```bash
cd /a/gitHub/Gold-BookMap
git branch --show-current
git status --short
```

Expected branch: `feature/power-team-m5-v2`. Expected status: blank. If not, stop and preserve the listed changes before extracting anything.

## 2. Download and extract the adapter extension

Download `Power-Team-M5-Adapter-Step.zip` from this conversation and extract its four files directly into `A:\gitHub\Gold-BookMap`:

- `power_m5_adapter.py`
- `test_power_m5_adapter.py`
- `POWER_TEAM_V2_FLOW.md`
- `POWER_TEAM_V2_ADAPTER_INSTALL.md`

These are new filenames. Do not overwrite the old `power_team.py` or edit any app integration files yet.

## 3. Run all v2 unit tests

```bash
python -m unittest -v test_power_team_v2.py test_power_memory_book.py test_power_m5_adapter.py
```

Expected result: 21 tests pass. This verifies synthetic test cases and software behavior, not live-feed semantics or a trading edge.

## 4. Review the adapter assumptions before using your own data

- It requires timestamped trades and uses the last five minutes plus the preceding five minutes for the momentum comparison.
- It trusts sides only when `is_direct` or `is_bookmap_direct` is true. Do not relax this until you verify the feed's side semantics.
- It requires the **Bookmap data instrument's tick size**. Do not automatically use the CFD tick size from `.env` for a futures feed.
- It does not infer range/breakout, resting-order flow, or execution permission.
- It fails closed on stale feed or inadequate data quality.

Do not run the trading app or push to GitHub at this stage. After tests pass, inspect a few known replay minutes and compare the adapter's counts and normalized readings with the raw ticks before asking to integrate it.
