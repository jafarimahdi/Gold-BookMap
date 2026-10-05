# POWER History Backfill Probe — bounded one-shot add-on

## Purpose and limits

This standalone, read-only Bookmap add-on captures startup backfill for Power M5 regime classification. It is separate from GoldBridge and never reads or changes `ticks.csv` or `mbo.csv`.

The bounded version stores **at most 28 completed five-minute OHLCV bars** in one fixed snapshot:

`%USERPROFILE%\BookmapPowerHistoryProbe\power_history_probe_latest.csv`

Set `POWER_HISTORY_PROBE_DIR` before starting Bookmap to use a different output directory.

The collector aggregates trades in a rolling in-memory window of at most 29 M5 buckets (the extra bucket permits exclusion of a partial final bar). At Bookmap's `REALTIME_START` transition it selects the latest 28 completed bars, validates continuity, writes a compact CSV through a temporary file and atomically replaces the fixed snapshot. It then clears its aggregation state and ignores all subsequent live trades. It does not create per-session timestamped files, rotate production files, or delete unrelated files.

The snapshot has a fixed small row count: a header, up to 28 `M5_BAR` records, and a completion-status record. `READY` is written only for exactly 28 contiguous complete bars. If Bookmap supplies fewer bars or there is a gap, the snapshot reports a non-ready status; the Python loader rejects it so the regime gate fails closed.

The previous legacy probe JAR wrote raw trade rows to timestamped CSVs. This new version does **not** clean those old files. Preserve or move any legacy CSVs yourself after review; do not assume this JAR deletes them.

## Data path

1. Start Bookmap and enable both the existing GoldBridge add-on and this separate probe for the intended instrument.
2. The probe collects only the initial historical/backfill phase. At `REALTIME_START`, it writes the compact snapshot and stops recording. It does not trigger on each `python main.py` run.
3. Run `python main.py` after the snapshot is ready. The Python loader checks the fixed file, exact instrument alias, completion status, 28-bar count, continuity, and age.
4. Probe OHLC is passed only to the Power regime classifier. Original `tick_data` remains the input to `run_power_v2`; the probe never maps aggressor flags or creates orders.

If the Python app starts before a valid snapshot exists, if the latest bar is stale, or if fewer than 28 complete contiguous bars are present, the regime remains UNKNOWN/NEITHER. Do not bypass that gate.

## Build

The source targets Bookmap API `7.4.0.21` and Java 8 bytecode. Build from this directory with Gradle installed:

```powershell
gradle jar
```

The JAR is created under `build\libs\POWER-History-Backfill-Probe-0.2.0-capped.jar`.

If your Bookmap version needs a different API version, change `bookmapApiVersion` only to the matching supported API artifact and rebuild. Do not replace or edit GoldBridge.

## Safe verification

- Test that a valid snapshot contains exactly 28 `M5_BAR` rows, one alias, one `PROBE_COMPLETE,READY` record, and no `TRADE` rows.
- Test that 27 bars, a gap, a stale snapshot, a malformed value, a future timestamp, or a different alias is rejected by the Python loader.
- Keep `TRADING_ENABLED=0`, `EXECUTION_MODE=none`, and `ALLOW_LIVE_TRADING=0` during validation. This add-on itself is read-only; the application still has separate execution configuration.

The 28-bar snapshot satisfies the current ADX period-14 minimum for regime computation. It does not certify feed quality, aggressor-side semantics, a POWER direction, or trading readiness. The POWER force/activity/family gates continue to apply independently.

## Existing legacy file

The old JAR's timestamped raw-trade CSV is not removed by this version. Keep it outside the active probe folder if you want the folder to contain only the bounded snapshot; retain a separate backup if you need the earlier diagnostic evidence.
