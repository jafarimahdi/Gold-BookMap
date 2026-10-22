"""Guard the regime-only probe boundary and preserve the force input contract."""
import ast
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


class PowerProbeBoundaryTests(unittest.TestCase):
    def test_probe_history_is_only_used_by_regime_and_force_gets_original_ticks(self):
        source_path = Path(__file__).with_name("step2_market_analysis.py")
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        calls = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in {"classify_m5_regime", "run_power_v2"}:
                    calls[node.func.id] = node
        self.assertIn("classify_m5_regime", calls)
        self.assertIn("run_power_v2", calls)
        regime_call = calls["classify_m5_regime"]
        force_call = calls["run_power_v2"]
        self.assertIsInstance(regime_call.args[0], ast.Name)
        self.assertEqual(regime_call.args[0].id, "_regime_ticks")
        self.assertIsInstance(force_call.args[0], ast.Name)
        self.assertEqual(force_call.args[0].id, "tick_data")

    def test_no_assignment_replaces_original_tick_data_with_probe_merge(self):
        source_path = Path(__file__).with_name("step2_market_analysis.py")
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        bad_assignments = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                value = node.value
                if any(isinstance(target, ast.Name) and target.id == "tick_data"
                       for target in targets):
                    if isinstance(value, ast.Name) and value.id == "_regime_ticks":
                        bad_assignments.append(node)
        self.assertFalse(bad_assignments)

    def test_power_history_note_exposes_active_m3_readiness_and_gap_reason(self):
        import step2_market_analysis as step2

        note = step2._format_power_history_probe_note(
            {"status": "TIMEFRAME_MISMATCH:M5_probe_for_M3_regime",
             "probe_timeframe": "M5", "loaded_m5_bars": 28, "usable_bars": 0,
             "probe_compatible": False, "history_bars_refused": True,
             "regime_latest_expected_bar_present": True,
             "price_scale_check": {"status": "NO_HISTORY"}},
            {"bar_seconds": 180, "latest_contiguous_bars": 4,
             "reason": "GAP_IN_COMPLETED_M3_HISTORY", "recent_gap_count": 1,
             "latest_tick_age_seconds": 6.3,
             "recent_gaps": [{"previous_bar_end_utc": "2026-10-22T12:00:00Z",
                               "next_bar_start_utc": "2026-10-22T12:06:00Z",
                               "missing_bar_intervals": 1}]},
            "M3", 28, " | LIVE WARM-UP 4/28 M3 bars")
        self.assertIn("status=TIMEFRAME_MISMATCH:M5_probe_for_M3_regime", note)
        self.assertIn("regime_tf=M3 regime_bar_seconds=180", note)
        self.assertIn("regime_tail=4/28", note)
        self.assertIn("regime_reason=GAP_IN_COMPLETED_M3_HISTORY", note)
        self.assertIn("regime_gaps=1", note)
        self.assertIn("tick_age_s=6.3", note)
        self.assertIn("first_gap_next=2026-10-22T12:06:00Z", note)
        self.assertIn("LIVE WARM-UP 4/28", note)

    def test_fixed_m5_probe_is_observed_but_not_used_by_m3_regime(self):
        import step2_market_analysis as step2

        with tempfile.TemporaryDirectory() as temp:
            Path(temp, "power_history_probe_latest.csv").touch()
            step2._POWER_PROBE_STARTUP_CACHE.clear()
            rows = [{"start": float(i), "open": 4200.0, "high": 4201.0,
                     "low": 4199.0, "close": 4200.5} for i in range(28)]
            def fake_loader(**_kwargs):
                return {"status": "LOADED", "loaded_bars": 28,
                        "bars": [dict(row) for row in rows]}
            live = [{"timestamp": datetime(2026, 10, 21, 12, 7, 29,
                                           tzinfo=timezone.utc), "price": 4200.0}]
            with patch.dict(os.environ, {"POWER_HISTORY_PROBE_DIR": temp}, clear=False), \
                    patch("power_history_probe_loader.load_probe_regime_bars",
                          side_effect=fake_loader):
                returned_live, m3_diagnostics, m3_history = \
                    step2._power_probe_startup_history(
                        "GCZ6.COMEX@RITHMIC",
                        datetime(2026, 10, 21, 12, 7, 30, tzinfo=timezone.utc),
                        live, bar_seconds=180)
                self.assertIs(returned_live, live)
                self.assertEqual(m3_diagnostics["loaded_m5_bars"], 28)
                self.assertTrue(m3_diagnostics["timeframe_mismatch"])
                self.assertEqual(m3_diagnostics["usable_bars"], 0)
                self.assertEqual(m3_diagnostics["rejected_probe_bars"], 28)
                self.assertEqual(m3_history, [])
                _live, m5_diagnostics, m5_history = \
                    step2._power_probe_startup_history(
                        "GCZ6.COMEX@RITHMIC",
                        datetime(2026, 10, 21, 12, 7, 30, tzinfo=timezone.utc),
                        live, bar_seconds=300)
                self.assertTrue(m5_diagnostics["probe_compatible"])
                self.assertEqual(m5_diagnostics["usable_bars"], 28)
                self.assertEqual(len(m5_history), 28)

            # A fixed-M5 source being configured does not mean a mismatch
            # occurred when there are no probe rows to reject.
            step2._POWER_PROBE_STARTUP_CACHE.clear()
            with patch.dict(os.environ, {"POWER_HISTORY_PROBE_DIR": temp}, clear=False), \
                    patch("power_history_probe_loader.load_probe_regime_bars",
                          return_value={"status": "NO_DATA", "loaded_bars": 0,
                                        "bars": []}):
                _live, empty_diagnostics, empty_history = \
                    step2._power_probe_startup_history(
                        "GCZ6.COMEX@RITHMIC",
                        datetime(2026, 10, 21, 12, 7, 30, tzinfo=timezone.utc),
                        live, bar_seconds=180)
                self.assertFalse(empty_diagnostics["timeframe_mismatch"])
                self.assertFalse(empty_diagnostics.get("history_bars_refused", False))
                self.assertEqual(empty_diagnostics["status"], "NO_DATA")
                self.assertEqual(empty_diagnostics["usable_bars"], 0)
                self.assertEqual(empty_history, [])
            step2._POWER_PROBE_STARTUP_CACHE.clear()


if __name__ == "__main__":
    unittest.main()
