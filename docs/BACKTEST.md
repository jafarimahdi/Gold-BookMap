# Backtesting your robot — how to test changes BEFORE they touch money

**New in v4.4.** Until now, the only way to evaluate the robot was to
run it live, one slow day at a time. Now you can replay any recorded
`ticks.csv` through the **real** engine and the **real** position
manager, and get a report: win rate, expectancy, how often each rule
fired. Institutional rule of thumb: *never trade a strategy you have
not replayed.*

---

## 1. The idea in one paragraph

Your NinjaTrader bridge already records every tick into `ticks.csv`
(the file the robot reads). The backtester takes that same file and
replays it at high speed: every 60 seconds of **recorded** time it runs
the real Step 2 analysis (all votes, all weights), takes entries with a
simple score rule (the AI layer is OFF in replay), and manages every
open position with the real position manager (trailing, break-even,
partial exits, every rule). At the end it prints the scorecard.

What is simulated honestly: fills happen at the bid/ask of the event,
SL/TP are checked on every price event, position size uses the same lot
formula as live. What is NOT in the replay: the AI decision layer
(replaced by a fixed score bar) and the news calendar (no calendar in
offline data). The report says "AI: OFF" so you never forget.

## 2. What you need

1. A `ticks.csv` with recorded data (the same file the robot uses).
   Even one trading day is enough for a first look; more days = more
   trustworthy numbers.
2. Python in the Gold-MT5 folder (same environment the robot uses).

## 3. Run it (Windows, PowerShell, in the Gold-MT5 folder)

1. Open PowerShell in the robot folder (`A:\gitHub\Rhitmic\Gold-MT5`).
2. Copy your ticks.csv into the folder (or use it where it is and write
   the full path in step 3).
3. Run:

   ```
   python tools\backtest.py --file ticks.csv
   ```

4. Wait. Rough speed: one trading day (~1-2 million lines) replays in
   about 1-3 minutes. You will see `replaying ticks.csv ...`, then the
   instrument name, then the report.
5. Success looks like this:

   ```
   ==============================================================
   BACKTEST REPORT — REAL step2 engine + REAL position manager
   ==============================================================
   file            : ticks.csv
   events / cycles : 112756 / 176
   signals (>=1)   : 119   entries taken: 8
   trades closed   : 9  (SL 5 / TP 3 / PM 1 / partials 1)
   win rate        : 55.6%   profit factor: 1.46
   expectancy      : $10.15 per trade
   ...
   saved: data\backtest_20260916_101530\trades.csv
   saved: data\backtest_20260916_101530\summary.txt
   ```

## 4. Useful options

| Option | What it does | Example |
|---|---|---|
| `--min-score 20` | raise the entry bar (like tuning `SIGNAL_BUY_THRESHOLD`) | `python tools\backtest.py --file ticks.csv --min-score 20` |
| `--equity 670` | simulate your account size (affects lot sizing and the real-risk guard) | `--equity 670` |
| `--symbol "MGC 12-26"` | trade a specific instrument from the file (default: most active) | `--symbol "MGC 12-26"` |
| `--cycle-seconds 60` | analysis cadence (default 60s, same as live) | `--cycle-seconds 30` |
| `--slippage 0.05` | add adverse slippage to every fill, in price points | `--slippage 0.05` |
| `--workdir data\bt_test` | fixed output folder instead of a timestamped one | `--workdir data\bt_test` |

For exactly repeatable runs, set the hash seed first (otherwise tie
breaks at the score boundary can flip one way or the other between
runs):

```
$env:PYTHONHASHSEED="0"; python tools\backtest.py --file ticks.csv
```

## 5. How to use it (the workflow that matters)

1. Run a backtest on your current data — this is your **baseline**.
2. Change ONE dial in `.env` (for example `SIGNAL_BUY_THRESHOLD=20`,
   or `PM_PARTIAL_TRIGGER_R=0.8`). `.env` changes need no code edit.
3. Run the backtest again into a fixed folder: `--workdir data\bt_after`
4. Compare: `summary.txt` of both runs. Did win rate go up? Did
   expectancy go up? Did the number of trades collapse to nothing?
5. Keep the change only if the numbers convince you — then let it live.

Rules of thumb for reading the report:
- **Fewer than ~30 trades** = the statistics are noise, not evidence.
  Get more data before believing any number.
- **Win rate alone means nothing.** A scalper can win 40% and make
  money (small losses, bigger wins). Look at **expectancy $ per trade**
  and **profit factor** (gross wins / gross losses; above 1.2 is
  interesting, above 1.5 is good).
- **PM rule counts** tell you which exits do the work. If
  `PROFIT_LOCK` and `PARTIAL_EXIT` dominate, your winners are being
  protected. If `SL_HIT` dominates with big per-trade losses, entries
  are the problem, not exits.

## 6. Where the files go

Everything lands inside one folder per run: `data\backtest_<timestamp>\`

- `trades.csv` — one row per closed trade (entry/exit, PnL, exit rule,
  hold time, entry score)
- `summary.txt` — the printed report, saved
- `management_log.csv` — the position manager journal (same format as
  live)
- `trade_memory.json` / `tca_log.csv` — the same memory files live uses

The replay never touches your live `data\pm_state.json`,
`data\trade_memory.json` or `data\management_log.csv`. It also forces
`TRADING_ENABLED`/`PM_ENABLE` ON for its own process only, so a halted
live robot does not mute the replay's position manager.

## 7. Honest limitations (read once)

- The AI layer is OFF — the replay measures the SIGNAL layer with a
  fixed score bar. If live results are better than backtests, the AI is
  earning its keep; if worse, it is hurting.
- No news calendar in replay: news-blackout/warning gates don't fire.
- SL/TP fills are checked at event prices — real gaps between events
  can be slightly worse (conservative option: `--slippage 0.05`).
- Past data is a sample, not a promise. The goal is comparing A vs B
  (before/after a change), not predicting exact future profits.
