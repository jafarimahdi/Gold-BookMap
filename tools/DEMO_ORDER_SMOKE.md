# One-shot DEMO order-path smoke test

This test is **prepared but not run**. It deliberately bypasses strategy signal generation; it is only for checking that this application environment can submit and then close one small MT5 order on an account positively identified by MT5 as **DEMO**. It is not a strategy, profitability, or live-trading test.

## Safety properties

- No order is submitted by default. The first command below is a preflight only.
- Sending requires `--send`, a positive `--max-loss` in the MT5 account currency, an interactive terminal, and an exact typed challenge. The cap is checked against a conservative estimated stop loss before and after fill; it cannot guarantee a maximum realized loss because gaps, slippage, commission, or broker behavior can exceed estimates. `ALLOW_LIVE_TRADING` must remain `False`; the script never edits `.env` or config.
- MT5 must positively report DEMO mode, terminal/account trading permission, and a connected session. A fresh snapshot must carry a PASS from the normal feed/safety gate (including validated BookMap L2/instrument checks). The tool also rechecks the market session, risk/circuit breaker, RL size/pause, cooldown, and daily trade cap. A broker quote no older than five seconds, acceptable spread, broker-valid SL/TP, minimum lot, broker margin, configured risk budget, and MT5 `order_check` must all pass.
- It refuses if there is any open position or pending order on the configured symbol, or if the Gold BookMap single-instance lock is occupied. Stop the bot first; do not use `--no-new-entries --loop` as a substitute for stopping it.
- It writes `data/demo_order_test_pending.json` immediately before the one market-order request. That durable marker makes the normal pipeline and Step 4 refuse new strategy entries if the tool is interrupted. It does not retry an uncertain result; it keeps the app lock held while the operator checks MT5 and resolves every position or pending order on the test symbol. The request uses a dedicated test magic/comment and protective stops. `data/demo_order_test_result.json` records the verified test position ID for accounting exclusion.
- After the position is visible, the operator checks it in MT5 and presses Enter for immediate closure. There is no leave-open option. If MT5 refuses the close, the tool keeps the app lock held and waits for manual closure/verification. If interrupted, the persistent marker blocks strategy entries; close any test position and cancel/review any pending order on the symbol, then run `python tools/demo_order_smoke.py --recover`. Recovery requires exact DEMO/account verification and typed confirmation, and clears the marker only after no position or pending order remains on the test symbol. Refresh with `python main.py --no-new-entries` before another preflight or normal strategy run.

## Operator steps

Run from the project root in the normal Windows environment with the owner's `.env` and MT5 terminal available. Do not copy credentials into another worktree.

1. Stop any running Gold BookMap process and confirm MT5 is logged into the intended DEMO account.
2. Refresh the normal market snapshot without allowing new strategy entries:

   ```powershell
   python main.py --doctor
   python main.py --no-new-entries
   ```

   `--no-new-entries` still analyzes the market, may call the AI, and continues managing existing positions. It is not a no-broker-action mode. Do not use it if you need the bot fully idle; stop the bot instead.

3. Run the no-order preflight in the direction you explicitly choose:

   ```powershell
   python tools/demo_order_smoke.py --side BUY
   # or, if you choose the other side:
   python tools/demo_order_smoke.py --side SELL
   ```

4. Review the printed symbol, minimum volume, entry, SL/TP, estimated loss, configured risk budget, quote ages, and spread. If preflight is blocked, stop and investigate; do not lower settings or force a strategy signal.
5. Only if you choose to send one DEMO order and the estimated loss is acceptable, run the same side with a maximum loss cap in the account's currency. Example syntax only—choose the cap yourself; do not copy the example amount blindly:

   ```powershell
   python tools/demo_order_smoke.py --side BUY --send --max-loss <YOUR_ACCOUNT_CURRENCY_CAP>
   ```

   Read the current preflight again. At the first prompt, type exactly the printed challenge (for example `OPEN DEMO BUY XAUUSD 0.01`). Any mismatch cancels without sending. If it opens, inspect the position in MT5 and press Enter to close it. Check MT5 and `data/demo_order_test_result.json` before considering the test complete.

If the process is interrupted or the persistent marker remains after a close, first stop the bot and manually inspect/close any `GBM-DEMO-SMOKE` position in MT5. Then run:

```powershell
python tools/demo_order_smoke.py --recover
```

Recovery does not place orders. It checks the original DEMO account, holds the app lock, verifies that no position or pending order remains on the test symbol, and requires the exact printed resolution phrase before removing the strategy-entry block.

The tool does not change `.env`, threshold, `ALLOW_LIVE_TRADING`, or normal strategy behavior. A pass proves only that this specific DEMO account/symbol/order path accepted and exposed one test position; it does not prove strategy quality or feed latency.
