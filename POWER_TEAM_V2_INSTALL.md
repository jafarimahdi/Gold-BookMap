# Safe local install — POWER Team v2 + memory book

This adds the new files alongside the old code. It does **not** replace `power_team.py`, connect to the shooter, or enable broker trading. The memory book is an audit-only module and does not feed history back into the Power decision.

## 1. Make a feature branch and verify the working tree

Open Git Bash and run:

```bash
cd /a/gitHub/Gold-BookMap
git status --short
```

If `git status --short` lists changes you care about, stop and commit or back them up first. If the tree is clean, make a separate branch:

```bash
git switch -c feature/power-team-m5-v2
```

If that branch already exists, use `git switch feature/power-team-m5-v2` instead.

## 2. Download and extract the bundle

Download `Power-Team-v2-M5.zip` from this conversation. In File Explorer, extract these six files into `A:\gitHub\Gold-BookMap` (the repository root):

- `power_team_v2.py`
- `power_memory_book.py`
- `test_power_team_v2.py`
- `test_power_memory_book.py`
- `POWER_TEAM_V2_DESIGN.md`
- `POWER_TEAM_V2_INSTALL.md`

These names are new and should not overwrite existing project files. Do **not** rename `power_team_v2.py` to `power_team.py`.

## 3. Run the isolated tests

Back in Git Bash:

```bash
cd /a/gitHub/Gold-BookMap
python -m unittest -v test_power_team_v2.py test_power_memory_book.py
```

Expected result: all 16 tests pass. These are code-behavior tests, not evidence of a trading edge or proof that the app's incoming data is M5-normalized.

## 4. Review what changed

```bash
git status --short
git diff --stat
```

At this stage the existing `power_team.py`, `step2_market_analysis.py`, `shooting_team.py`, Escort files, Scout files, `.env`, and credentials should remain untouched. Only the new v2 and test/design files should appear as untracked additions.

## 5. Memory book behavior

When later called by a paper/replay adapter, `power_memory_book.py` writes one UTC JSONL record per completed M5 bar under a directory such as `data/power_memory/`. It stores normalized judge inputs, context, result, reference price, and a hash-chain field. Do not log secrets, and do not let old records vote on new decisions during this first phase. The module is intended for a single writer; it does not yet provide cross-process locking or a regulated immutable archive.

The module is not automatically connected to the app. Unit tests exercise the writer in a temporary folder and do not create project data files.

## 6. Do not wire it into the existing live chain yet

The current caller expects the legacy Power schema (`pick=above/below`, `confidence`). V2 returns `direction=UP/DOWN/NEITHER`, force shares, activity, coverage, and reason codes. A later integration must update the new-version caller and Shooting/permission interface to consume `direction` and explicitly handle `NEITHER`.

Before activation, the adapter must calculate the directional values from a consistent rolling M5 window, validate freshness/side labels, and supply trustworthy range/breakout and data-quality flags. The current candle-sweep signal is excluded because its project caller uses synthetic high/low/volume inputs.

Keep the existing trading configuration unchanged; do not launch the robot while experimenting. Test with historical/replay data first. Never enable live orders merely because unit tests pass.

## 7. Optional commit after review

Once the isolated files and tests are acceptable:

```bash
git add power_team_v2.py power_memory_book.py test_power_team_v2.py test_power_memory_book.py POWER_TEAM_V2_DESIGN.md POWER_TEAM_V2_INSTALL.md
git commit -m "Add isolated M5 Power v2 and memory prototype"
```

Do not push to GitHub until you decide to publish the prototype and have reviewed the integration plan.
