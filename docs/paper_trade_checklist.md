# Paper-Trading Checklist

Run the bot on an MT5 **demo account** (and Rithmic demo feed) for at least
2–4 weeks before any live money. Work through this list in order.

## Phase 0 — One-time setup

- [ ] `.env` created from `.env.example` with your **Rithmic demo** username/password
      (`RITHMIC_SYSTEM=Rithmic Paper Trading`).
- [ ] `DATA_SYMBOL` = the gold futures contract you see in the Rithmic demo
      (usually `GC`); `MT5_SYMBOL` = the CFD in MT5's Market Watch (usually `XAUUSD`).
- [ ] `python3 setup_markets.py` run, both markets confirmed manually.
- [ ] `python3 setup_markets.py --check` prints "mapping OK".
- [ ] `python3 demo_step2.py` → 36/36 pass.
- [ ] `python3 test_data_providers.py` → 27/27 pass.
- [ ] `python3 test_markets.py` → 28/28 pass.
- [ ] `python3 test_indicators_golden.py` → 19/19 pass.
- [ ] `python3 test_session_risk.py` → 25/25 pass.
- [ ] `python3 e2e_test.py` → 31/31 pass.
- [ ] MT5 terminal running, logged into a **demo** account.
- [ ] `GoldTradingEA.mq5` + `GoldSignalIndicator.mq5` compiled (MetaEditor F7).
- [ ] Indicator attached to an XAUUSD chart — you can SEE BUY/SELL arrows.
- [ ] `MT5_SIGNAL_FILE` in `.env` points at the MT5 Common Files folder.

## Phase 1 — Weekend / no-data behaviour (do this FIRST)

- [ ] On a **Saturday**, start `python3 main.py` → it prints
      `SAFETY: SESSION CLOSED — WEEKEND` and does **not** trade or crash.
- [ ] With `DATA_SOURCE=rithmic` but the Rithmic feed stopped/closed, the bot
      prints `SAFETY: FEED STALE/EMPTY` and does **not** trade.
- [ ] With `DATA_SOURCE=replay` + `REPLAY_FILE`, a recorded session replays
      cleanly through Step 2.
- [ ] `data/risk_state.json` absent when there are no losses.

## Phase 2 — Live-feed sanity (Rithmic demo)

- [ ] Rithmic demo terminal shows a moving order book for the gold contract.
- [ ] `python3 main.py` logs `has_data=True` and the snapshot header shows
      `DATA (futures feed): GC -> TRADE (MT5 CFD): XAUUSD`.
- [ ] L2 metrics populate (microprice, OFI, imbalance) and L3 metrics
      (aggressive buys/sells, OFI) are non-zero when the market is active.
- [ ] Spread monitor does **not** print "WIDE" (futures↔CFD basis small).

## Phase 3 — Signal observation (no orders yet)

- [ ] Run `python3 main.py --loop` for several sessions.
- [ ] Watch the indicator arrows on MT5 — do they match the log's BUY/SELL?
- [ ] Every pass writes `data/decisions_log.csv` and `data/mt5_signal.txt`.
- [ ] Note how often the signal confidence actually reaches 70%+.

## Phase 4 — Risk limits (before auto-trading)

- [ ] Set `DAILY_LOSS_LIMIT_PCT=1.0` and `MAX_DRAWDOWN_PCT=5.0` in `.env`.
- [ ] Confirm a manually-simulated halt blocks trading:
      run `python3 -c "from risk_manager import RiskManager; import datetime,timezone;"
      ...` (or just trust `test_session_risk.py`).
- [ ] Confirm `data/risk_state.json` is created when a halt triggers and
      cleared after the halt window.

## Phase 5 — Auto-trading on DEMO

- [ ] Attach the EA to XAUUSD, **enable AutoTrading** in MT5.
- [ ] Let it run during market hours with real Rithmic demo data.
- [ ] Verify each MT5 order matches the signal file (direction, ~lots, SL/TP).
- [ ] Verify NO orders open within the news blackout window.
- [ ] Verify opposite positions are closed before reversal.

## Phase 6 — Review before going live

- [ ] `data/trade_outcomes.csv` populated; compute win rate / profit factor.
- [ ] `python3 backtest.py --replay data/record_*.json` reviewed.
- [ ] Daily-loss breaker behaved correctly on at least one losing day.
- [ ] You have recorded at least one full session with `RECORD_DATA=1`.
- [ ] You accept that on random/synthetic data the current signal engine does
      **not** show an edge (see backtest) — live tuning is required.

---

## Quick reference — the bot's safety nets (all must hold before live)

| Layer | What it does |
|---|---|
| Session gate | No trading on weekends/holidays/breaks |
| Feed gate | No trading when Rithmic data is stale/empty |
| News gate | No entries in the news BLACKOUT window |
| Risk gate | No trading after daily-loss / drawdown breach |
| Confidence gate | No execution below the confidence threshold |
| EA gates | Same rules re-enforced inside MT5 (MQL5) |

If ANY of these fail during paper trading, stop and investigate before going
live.
