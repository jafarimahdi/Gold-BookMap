# POWER History Backfill Probe — separate diagnostic add-on

## Purpose

This is a standalone, read-only Bookmap add-on to test one question: **does the Bookmap indicator/add-on receive past market trades on startup, before it transitions to live data?**

It is deliberately separate from the existing Gold-BookMap bridge. It does **not** open, edit, truncate, or write `ticks.csv`, `mbo.csv`, or any application/POWER files. It writes only a new timestamp-named probe CSV under:

`%USERPROFILE%\BookmapPowerHistoryProbe\power_history_probe_YYYYMMDD_HHMMSS.csv`

You may set `POWER_HISTORY_PROBE_DIR` before starting Bookmap to choose another output folder.

The probe records the raw Bookmap event timestamp, alias, price, size, phase, and raw `TradeInfo.isBidAggressor` flag. It intentionally does **not** translate that flag into BUY/SELL, because its meaning must first be checked against the installed bridge and feed.

## What it tests

- `BackfilledDataListener`: asks Bookmap to send available cloud backfill to this add-on.
- `TradeDataListener` + `TimeListener`: records the timestamped trades that Bookmap actually delivers.
- `HistoricalModeListener`: marks the transition to live data (`REALTIME_START`).
- `analyze_probe.py`: counts completed five-minute buckets in pre-live history. It uses the latest PRE_REALTIME trade timestamp as the historical completion cutoff, so a later LIVE timestamp cannot promote a partial historical bar. A readiness label also requires the `REALTIME_START` marker and a single instrument alias.

Bookmap can only send history actually available from its selected connection/provider. The add-on cannot manufacture missing provider history. Historical backfill may also differ from real-time data in depth/detail; this probe records trades only.

## Build

The source is compiled against Bookmap API `7.4.0.21`, matching your reported Bookmap `7.4.0 build 21`. The backfill listener is supported from Bookmap API 7.2 onward.

From this directory, with Gradle installed:

```powershell
gradle jar
```

The JAR will be under `build\libs\POWER-History-Backfill-Probe-0.1.0-probe.jar`.

If the target Bookmap build is not compatible with 7.4.0.21, set the matching artifact version in Gradle, for example:

```powershell
gradle -PbookmapApiVersion=<matching-version> jar
```

Do not replace or edit the existing BookMapBridge add-on.

## Safe test steps

1. Check the Bookmap version using **Help → About**. It must support the backfill listener (7.2+); use matching API artifacts to build.
2. This is a standalone diagnostic and does not require the Python app loop. Do not start or stop the app loop specifically for this probe test.
3. Build this separate JAR and add it in Bookmap using **Settings → API plugins configuration → Add**. Enable only **POWER History Backfill Probe (standalone)** for the test instrument.
4. Leave the existing bridge and its files unchanged. The probe writes to its own folder/file only.
5. Wait for the Bookmap log message `Bookmap entered real-time mode`, then run:

   ```powershell
   python analyze_probe.py "$env:USERPROFILE\BookmapPowerHistoryProbe\power_history_probe_YYYYMMDD_HHMMSS.csv"
   ```

6. Inspect the report:
   - **Longest pre-realtime run ≥28**: a 28-bar sequence exists somewhere in the returned history, showing the source can deliver that much contiguous history.
   - **Pre-realtime tail run ≥28**: the most recent contiguous historical sequence reaches 28; this is the stronger startup-history check.
   - If the tail run is below 28, the longest run can still pass while immediate POWER readiness fails; the current Power gate needs the latest completed-bar run, plus freshness and regime checks.
   - **Zero historical rows**: no evidence that this add-on received startup backfill. It may be unavailable for the connection, unsupported by the installed version, or not delivered in this add-on mode; do not infer the cause from zero rows alone.
7. Keep POWER fail-closed. This diagnostic does not enable trading, alter thresholds, or authorize an order.

## Limits of this first probe

- The included JAR manifest identifies Bookmap API `7.4.0.21`, matching Bookmap `7.4.0 build 21`. It has **not yet been loaded inside the user's Bookmap/Rithmic installation**.
- The exact Bookmap version and the installed indicator/bridge implementation have not yet been inspected, so provider-specific backfill behavior remains unverified.
- The 28-bar report checks timestamp continuity only. It does not certify aggressor-side semantics, data quality, ADX regime, or POWER direction.
- This is a collector/diagnostic add-on, not a finished visual chart indicator and not a production bridge replacement. The optional Power M5 startup integration is in the repository root: `step2_market_analysis.py` and `power_history_probe_loader.py`. It reads this probe CSV for M5 regime classification and keeps the original `tick_data` input to `run_power_v2` unchanged.
