import contextlib
import io
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import audit_day


UTC = timezone.utc
BASE = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def make_m3_ticks(bars=24):
    ticks = []
    for i in range(bars):
        start = BASE + timedelta(seconds=180 * i)
        center = 2000.0 + i * 0.3
        for offset, price in ((10, center), (60, center + 0.1),
                              (120, center - 0.1), (170, center + 0.05)):
            ticks.append((start + timedelta(seconds=offset), price, 1.0))
    return ticks


def make_m3_decision(index=15):
    start = BASE + timedelta(seconds=180 * index)
    price = 2000.0 + index * 0.3
    return {
        "_dt": start + timedelta(seconds=100),
        "signal_direction": "BUY",
        "price": price,
        "signal_confidence": 62.0,
        "ai_confidence": 62.0,
        "regime": "TREND",
    }


class AuditDayTimeframeTests(unittest.TestCase):
    def test_bar_seconds_prefers_recorded_power_interval(self):
        seconds, source = audit_day._audit_bar_seconds(
            date(2026, 10, 22), {"POWER_BAR_SECONDS": 180})
        self.assertEqual(seconds, 180)
        self.assertIn("recorded", source)
        self.assertEqual(audit_day._audit_bar_seconds(date(2026, 10, 22), None)[0], 180)
        self.assertEqual(audit_day._audit_bar_seconds(date(2026, 10, 14), None)[0], 300)
        fallback, reason = audit_day._audit_bar_seconds(
            date(2026, 10, 22), {"POWER_BAR_SECONDS": 0, "TIMEFRAME": "M3"})
        self.assertEqual(fallback, 300)
        self.assertIn("runtime M5 fallback", reason)

    def test_build_candles_uses_m3_epoch_boundaries_and_sorted_ohlc(self):
        ticks = [
            (BASE + timedelta(seconds=179), 2002.0, 1),
            (BASE + timedelta(seconds=10), 2000.0, 1),
            (BASE + timedelta(seconds=180), 2003.0, 1),
        ]
        candles = audit_day.build_candles(ticks, bar_seconds=180)
        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0]["dt"], BASE)
        self.assertEqual((candles[0]["open"], candles[0]["high"],
                          candles[0]["low"], candles[0]["close"]),
                         (2000.0, 2002.0, 2000.0, 2002.0))
        self.assertEqual(candles[1]["dt"], BASE + timedelta(seconds=180))

    def test_default_candle_interval_remains_m5_for_legacy_report_panels(self):
        candles = audit_day.build_candles([
            (BASE + timedelta(seconds=20), 2000.0, 1),
            (BASE + timedelta(seconds=299), 2001.0, 1),
            (BASE + timedelta(seconds=300), 2002.0, 1),
        ])
        self.assertEqual(len(candles), 2)
        self.assertEqual(candles[0]["dt"], BASE)
        self.assertEqual(candles[1]["dt"], BASE + timedelta(seconds=300))

    def test_build_candles_accepts_valid_non_minute_interval(self):
        candles = audit_day.build_candles([
            (BASE + timedelta(seconds=20), 2000.0, 1),
            (BASE + timedelta(seconds=181), 2001.0, 1),
        ], bar_seconds=181)
        self.assertEqual(len(candles), 2)
        self.assertEqual(audit_day._timeframe_label(181), "181s")

    def test_build_candles_rejects_invalid_interval(self):
        for seconds in (0, 30, "bad"):
            with self.subTest(seconds=seconds):
                with self.assertRaises(ValueError):
                    audit_day.build_candles([], bar_seconds=seconds)

    def test_wilder_atr_uses_only_completed_contiguous_bars(self):
        candles = []
        for i in range(16):
            close = 2000.0 + i
            candles.append({
                "dt": BASE + timedelta(seconds=180 * i),
                "open": close, "high": close + 1.0,
                "low": close - 1.0, "close": close, "n": 4,
            })
        entry = BASE + timedelta(seconds=180 * 15 + 30)
        # The 16th bar is still in progress at entry and cannot influence ATR.
        candles[15]["high"] = 99999.0
        atr = audit_day._wilder_atr_before(candles, entry, bar_seconds=180, period=14)
        self.assertAlmostEqual(atr, 2.0)
        # A gap within the most recent ATR window fails closed.
        gap = [c for i, c in enumerate(candles) if i != 8]
        self.assertIsNone(audit_day._wilder_atr_before(gap, entry, bar_seconds=180, period=14))

    def test_wilder_atr_smooths_ranges_after_the_initial_seed(self):
        candles = []
        for i in range(20):
            close = 2000.0 + i
            extra = 5.0 if i >= 15 else 1.0
            candles.append({
                "dt": BASE + timedelta(seconds=180 * i),
                "open": close, "high": close + extra,
                "low": close - 1.0, "close": close, "n": 4,
            })
        ranges = []
        for previous, current in zip(candles, candles[1:]):
            ranges.append(max(current["high"] - current["low"],
                              abs(current["high"] - previous["close"]),
                              abs(current["low"] - previous["close"])))
        expected = sum(ranges[:14]) / 14
        for value in ranges[14:]:
            expected = (expected * 13 + value) / 14
        entry = BASE + timedelta(seconds=180 * 20)
        actual = audit_day._wilder_atr_before(candles, entry, bar_seconds=180, period=14)
        self.assertAlmostEqual(actual, expected)

    def test_hold_horizon_is_converted_from_minutes_for_each_timeframe(self):
        self.assertEqual(audit_day._hold_bars(180, 180), 60)
        self.assertEqual(audit_day._hold_bars(180, 300), 36)
        self.assertEqual(audit_day._hold_bars(181, 180), 60)
        self.assertIsNone(audit_day._hold_bars(0, 180))
        self.assertIsNone(audit_day._hold_bars(2, 180))

    def test_simulation_uses_m3_future_bars_and_explicit_atr_cost(self):
        candles = [
            {"dt": BASE, "open": 2000.0, "high": 2000.4,
             "low": 1999.6, "close": 2000.0},
            {"dt": BASE + timedelta(seconds=180), "open": 2000.0,
             "high": 2004.0, "low": 1999.8, "close": 2003.5},
        ]
        result = audit_day.simulate(
            [], candles, BASE + timedelta(seconds=20), "BUY", 2000.0,
            max_candles=60, sl_mult=2.0, tp_mult=3.5,
            atr=1.0, spread=0.5, bar_seconds=180, require_contiguous=True)
        self.assertAlmostEqual(result, 3.5 - 0.5)
        # If the tape omitted the signal's forming candle, the first later
        # candle is still the first valid replay bar (do not skip it too).
        missing_current = audit_day.simulate(
            [], candles[1:], BASE + timedelta(seconds=20), "BUY", 2000.0,
            max_candles=60, sl_mult=2.0, tp_mult=3.5,
            atr=1.0, spread=0.5, bar_seconds=180, require_contiguous=True)
        self.assertAlmostEqual(missing_current, 3.5 - 0.5)

    def test_simulation_refuses_missing_future_m3_intervals(self):
        flat = {"dt": BASE + timedelta(seconds=180), "open": 2000.0,
                "high": 2000.5, "low": 1999.5, "close": 2000.1}
        later_target = {"dt": BASE + timedelta(seconds=540), "open": 2000.0,
                        "high": 2004.0, "low": 1999.8, "close": 2003.5}
        result = audit_day.simulate(
            [], [flat, later_target], BASE + timedelta(seconds=20), "BUY", 2000.0,
            max_candles=60, sl_mult=2.0, tp_mult=3.5,
            atr=1.0, spread=0.5, bar_seconds=180, require_contiguous=True)
        self.assertIsNone(result)
        missing_first = audit_day.simulate(
            [], [later_target], BASE + timedelta(seconds=20), "BUY", 2000.0,
            max_candles=60, sl_mult=2.0, tp_mult=3.5,
            atr=1.0, spread=0.5, bar_seconds=180, require_contiguous=True)
        self.assertIsNone(missing_first)

    def test_current_m3_mismatch_receipt_is_parsed_as_regime_not_probe_readiness(self):
        line = (
            "POWER HISTORY PROBE | status=TIMEFRAME_MISMATCH:M5_probe_for_M3_regime "
            "probe_tf=M5 regime_tf=M3 regime_bar_seconds=180 loaded_probe_bars=28 "
            "usable_probe_bars=0 probe_compatible=False regime_tail=4/28 "
            "regime_reason=GAP_IN_COMPLETED_M3_HISTORY regime_gaps=1 "
            "latest_bar_present=True tick_age_s=6.3 scale=NO_HISTORY "
            "history_refused=True first_gap_prev=2026-10-22T12:00:00Z "
            "first_gap_next=2026-10-22T12:06:00Z missing_intervals=1 "
            "| LIVE WARM-UP 4/28 M3 bars"
        )
        rows = audit_day._parse_power_history_rows(line)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tail"], 4)
        self.assertEqual(rows[0]["required"], 28)
        self.assertEqual(rows[0]["status"], "TIMEFRAME_MISMATCH:M5_probe_for_M3_regime")
        self.assertFalse(audit_day._power_history_ready(rows[0], 180))
        self.assertTrue(rows[0]["warmup_marker"])

    def test_full_m3_tail_is_ready_despite_expected_missing_m5_probe(self):
        line = (
            "POWER HISTORY PROBE | status=TIMEFRAME_MISMATCH:M5_probe_for_M3_regime "
            "regime_tf=M3 regime_bar_seconds=180 regime_tail=28/28 "
            "regime_reason=ADX_NEUTRAL_BAND regime_gaps=0 latest_bar_present=True "
            "tick_age_s=4.1 scale=NO_HISTORY history_refused=True"
        )
        row = audit_day._parse_power_history_rows(line)[0]
        self.assertTrue(audit_day._power_history_ready(row, 180))
        self.assertFalse(row["warmup_marker"])

    def test_m5_regime_receipt_cannot_satisfy_recorded_m3_readiness(self):
        line = (
            "POWER HISTORY PROBE | status=LOADED probe_tf=M5 regime_tf=M5 "
            "regime_bar_seconds=300 loaded_probe_bars=28 usable_probe_bars=28 "
            "regime_tail=28/28 regime_reason=ADX_TREND regime_gaps=0 "
            "latest_bar_present=True tick_age_s=4.0 scale=OK history_refused=False"
        )
        row = audit_day._parse_power_history_rows(line)[0]
        self.assertFalse(audit_day._power_history_ready(row, 180))
        self.assertTrue(audit_day._power_history_ready(row, 300))

    def test_acceptance_report_uses_active_m3_tail_not_probe_status(self):
        rc = {"POWER_BAR_SECONDS": 180}
        incomplete = (
            "POWER HISTORY PROBE | status=TIMEFRAME_MISMATCH:M5_probe_for_M3_regime "
            "probe_tf=M5 regime_tf=M3 regime_bar_seconds=180 "
            "loaded_probe_bars=28 usable_probe_bars=0 probe_compatible=False "
            "regime_tail=4/28 regime_reason=GAP_IN_COMPLETED_M3_HISTORY "
            "regime_gaps=1 latest_bar_present=True tick_age_s=6.3 "
            "scale=NO_HISTORY history_refused=True | LIVE WARM-UP 4/28 M3 bars"
        )
        with mock.patch.object(audit_day, "_load_run_config", return_value=rc), \
             contextlib.redirect_stdout(io.StringIO()):
            report, _ = audit_day.test_16_team_cycle(
                date(2026, 10, 22), [], incomplete, [])
        details = " ".join(report.rows[0][3])
        self.assertIn("[WARN] 1  active-timeframe regime history", details)
        self.assertIn("last regime tail 4/28 on M3", details)
        self.assertIn("fixed-M5 probe refusal is expected at M3", details)
        self.assertIn("no full 28-bar tail was observed", details)

        ready = incomplete.replace("regime_tail=4/28", "regime_tail=28/28")
        ready = ready.replace("regime_reason=GAP_IN_COMPLETED_M3_HISTORY", "regime_reason=ADX_NEUTRAL_BAND")
        ready = ready.replace("regime_gaps=1", "regime_gaps=0")
        ready = ready.replace("LIVE WARM-UP 4/28 M3 bars", "")
        with mock.patch.object(audit_day, "_load_run_config", return_value=rc), \
             contextlib.redirect_stdout(io.StringIO()):
            ready_report, _ = audit_day.test_16_team_cycle(
                date(2026, 10, 22), [], ready, [])
        ready_details = " ".join(ready_report.rows[0][3])
        self.assertIn("[PASS] 1  active-timeframe regime history", ready_details)
        self.assertIn("[PASS] 10  warm-up only before 28 active bars", ready_details)

    def test_acceptance_report_warns_when_regime_clock_is_m5_but_run_is_m3(self):
        rc = {"POWER_BAR_SECONDS": 180}
        line = (
            "POWER HISTORY PROBE | status=LOADED probe_tf=M5 regime_tf=M5 "
            "regime_bar_seconds=300 loaded_probe_bars=28 usable_probe_bars=28 "
            "regime_tail=28/28 regime_reason=ADX_TREND regime_gaps=0 "
            "latest_bar_present=True tick_age_s=4.0 scale=OK history_refused=False"
        )
        with mock.patch.object(audit_day, "_load_run_config", return_value=rc), \
             contextlib.redirect_stdout(io.StringIO()):
            report, _ = audit_day.test_16_team_cycle(
                date(2026, 10, 22), [], line, [])
        details = " ".join(report.rows[0][3])
        self.assertIn("[WARN] 1  active-timeframe regime history", details)
        self.assertIn("active timeframe expected M3, got M5", details)

    def test_legacy_m5_probe_does_not_prove_m3_readiness(self):
        line = "POWER HISTORY PROBE | status=LOADED loaded_bars=28 scale=OK"
        row = audit_day._parse_power_history_rows(line)[0]
        self.assertIsNone(audit_day._power_history_ready(row, 180))
        self.assertTrue(audit_day._power_history_ready(row, 300))

    def test_isolate_env_does_not_leak_active_timeframe_or_replay_settings(self):
        global_names = ("ATR", "SPREAD", "SL_MULT", "TP_MULT", "MAX_HOLD_MIN",
                        "MAX_HOLD_CANDLES", "WINDOW_START_H", "WINDOW_END_H",
                        "AI_MAX_CALLS", "LATENCY_TARGET_MS", "CONF_MIN", "AI_MIN",
                        "ENV_DUPLICATES")
        old_globals = {name: getattr(audit_day, name) for name in global_names}
        old_env = dict(os.environ)
        old_audit_env = dict(audit_day.ENV)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                env_path = Path(temp_dir) / ".env"
                env_path.write_text("CONFIDENCE_THRESHOLD=35\n", encoding="utf-8")
                os.environ.update({
                    "TIMEFRAME": "M3", "POWER_BAR_SECONDS": "180",
                    "A1_BASE_TF_MINUTES": "3", "VOICE_GAIN": "3.0",
                    "STOP_LOSS_ATR_MULT": "2.0", "TAKE_PROFIT_ATR_MULT": "3.5",
                })
                audit_day._isolate_env(env_path)
                for key in ("TIMEFRAME", "POWER_BAR_SECONDS", "A1_BASE_TF_MINUTES",
                            "VOICE_GAIN", "STOP_LOSS_ATR_MULT", "TAKE_PROFIT_ATR_MULT"):
                    self.assertNotIn(key, os.environ)
                self.assertEqual(os.environ["CONFIDENCE_THRESHOLD"], "35")
        finally:
            os.environ.clear()
            os.environ.update(old_env)
            audit_day.ENV.clear()
            audit_day.ENV.update(old_audit_env)
            for name, value in old_globals.items():
                setattr(audit_day, name, value)

    def test_run_config_snapshot_records_timeframe_and_replay_assumptions(self):
        from types import SimpleNamespace
        import main

        stub = SimpleNamespace(
            AI_CONFIDENCE_THRESHOLD=35, AI_MIN_SIGNAL_STRENGTH=6,
            V6_CFD_SPREAD_MAX=0.5, BOOKMAP_WINDOW_SECONDS=10800,
            BOOKMAP_MAX_DEPTH_LEVELS=150, GEMINI_MODEL="model",
            GEMINI_MODELS=[], EXECUTION_MODE="python", TRADING_ENABLED=True,
            ALLOW_LIVE_TRADING=False, TIMEFRAME="M3", POWER_BAR_SECONDS=180,
            STOP_LOSS_ATR_MULT=2.0, TAKE_PROFIT_ATR_MULT=3.5,
        )
        with mock.patch.object(main, "config", stub), \
             mock.patch.dict("os.environ", {"A1_BASE_TF_MINUTES": "3",
                                             "VOICE_GAIN": "3.0",
                                             "AUDIT_SPREAD": "0.4",
                                             "AUDIT_MAX_HOLD_MIN": "120"}, clear=False):
            snapshot = main._env_snapshot()
        self.assertEqual(snapshot["TIMEFRAME"], "M3")
        self.assertEqual(snapshot["POWER_BAR_SECONDS"], 180)
        self.assertEqual(snapshot["A1_BASE_TF_MINUTES"], "3")
        self.assertEqual(snapshot["VOICE_GAIN"], "3.0")
        self.assertEqual(snapshot["STOP_LOSS_ATR_MULT"], 2.0)
        self.assertEqual(snapshot["TAKE_PROFIT_ATR_MULT"], 3.5)
        self.assertEqual(snapshot["AUDIT_SPREAD"], "0.4")
        self.assertEqual(snapshot["AUDIT_MAX_HOLD_MIN"], "120")

    def test_what_if_uses_recorded_m3_and_reports_assumptions(self):
        rc = {
            "POWER_BAR_SECONDS": 180,
            "AUDIT_MAX_HOLD_MIN": 180,
            "AUDIT_SPREAD": 0.1,
            "STOP_LOSS_ATR_MULT": 2.0,
            "TAKE_PROFIT_ATR_MULT": 3.5,
        }
        with mock.patch.object(audit_day, "_load_run_config", return_value=rc):
            with contextlib.redirect_stdout(io.StringIO()):
                report, _payload = audit_day.test_7_what_if(
                    date(2026, 10, 22), make_m3_ticks(),
                    [make_m3_decision()], None)
        self.assertEqual(len(report.rows), 1)
        grade, _name, headline, why = report.rows[0]
        joined = " ".join(why)
        self.assertIn("M3 candles", joined)
        self.assertIn("60 M3 bars = 180 min effective", joined)
        self.assertIn("Wilder ATR(14)", joined)
        self.assertIn("not actual P&L", headline)
        self.assertNotIn("36 M5", joined)

    def test_confidence_baseline_is_dated_for_october_22_only(self):
        rc = {
            "CONFIDENCE_THRESHOLD": "35",
            "AI_MIN_SIGNAL_STRENGTH": "6",
            "V6_CFD_SPREAD_MAX": "0.50",
            "BOOKMAP_WINDOW_SECONDS": "10800",
            "BOOKMAP_MAX_DEPTH_LEVELS": "150",
            "TRADING_ENABLED": "1",
            "TIMEFRAME": "M3",
            "A1_BASE_TF_MINUTES": "3",
            "VOICE_GAIN": "3.0",
            "POWER_BAR_SECONDS": "180",
            "_path": "/tmp/run_config_20261022.json",
            "written_at": "2026-10-22T10:13:00",
        }
        env = {key: str(value) for key, value in rc.items() if not key.startswith("_")}
        env["TRADING_ENABLED"] = "1"
        with mock.patch.object(audit_day, "_load_run_config", return_value=rc), \
             mock.patch.object(audit_day, "ENV", env), \
             mock.patch.object(audit_day, "ENV_DUPLICATES", {}), \
             contextlib.redirect_stdout(io.StringIO()):
            report, _ = audit_day.test_11_version_config("", date(2026, 10, 22))
        self.assertEqual(report.rows[-1][0], "PASS")
        self.assertIn("35%", " ".join(report.rows[-1][3]))

        with mock.patch.object(audit_day, "_load_run_config", return_value=rc), \
             mock.patch.object(audit_day, "ENV", env), \
             mock.patch.object(audit_day, "ENV_DUPLICATES", {}), \
             contextlib.redirect_stdout(io.StringIO()):
            old_day_report, _ = audit_day.test_11_version_config("", date(2026, 10, 21))
        self.assertEqual(old_day_report.rows[-1][0], "WARN")
        self.assertIn("CONFIDENCE_THRESHOLD: ran=35 expected=50",
                      " ".join(old_day_report.rows[-1][3]))

        sparse_rc = {key: value for key, value in rc.items()
                     if key not in {"A1_BASE_TF_MINUTES", "VOICE_GAIN"}}
        with mock.patch.object(audit_day, "_load_run_config", return_value=sparse_rc), \
             mock.patch.object(audit_day, "ENV", env), \
             mock.patch.object(audit_day, "ENV_DUPLICATES", {}), \
             contextlib.redirect_stdout(io.StringIO()):
            sparse_report, _ = audit_day.test_11_version_config("", date(2026, 10, 22))
        self.assertEqual(sparse_report.rows[-1][0], "WARN")
        sparse_why = " ".join(sparse_report.rows[-1][3])
        self.assertIn("UNVERIFIED", sparse_why)
        self.assertIn("A1_BASE_TF_MINUTES", sparse_why)
        self.assertIn("VOICE_GAIN", sparse_why)

        with mock.patch.object(audit_day, "_load_run_config", return_value=None), \
             mock.patch.object(audit_day, "ENV", env), \
             mock.patch.object(audit_day, "ENV_DUPLICATES", {}), \
             contextlib.redirect_stdout(io.StringIO()):
            no_record_report, _ = audit_day.test_11_version_config(
                "", date(2026, 10, 22))
        self.assertEqual(no_record_report.rows[-1][0], "WARN")
        self.assertIn("UNVERIFIED: no run_config file",
                      " ".join(no_record_report.rows[-1][3]))


if __name__ == "__main__":
    unittest.main()
