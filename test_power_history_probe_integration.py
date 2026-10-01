"""Guard the regime-only probe boundary and preserve the force input contract."""
import ast
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
