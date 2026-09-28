import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from power_memory_book import (
    MemoryIntegrityError,
    append_snapshot,
    read_day,
)


class PowerMemoryBookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "power_memory"
        self.ts = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
        self.result = {
            "direction": "UP",
            "up_power_pct": 68.0,
            "down_power_pct": 32.0,
            "power_total_pct": 100.0,
            "reason_code": "UPWARD_FORCE_DOMINATES",
        }

    def tearDown(self):
        self.temp.cleanup()

    def test_append_and_read_snapshot(self):
        written = append_snapshot(
            self.root,
            timestamp=self.ts,
            symbol="XAUUSD",
            judges={"footprint_delta": {"value": 0.8, "quality": 1.0}},
            context={"in_range": False},
            result=self.result,
            reference_price=4154.0,
        )
        self.assertTrue(written["appended"])
        rows = read_day(self.root, "2026-09-28", symbol="XAUUSD")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["timeframe"], "M5")
        self.assertEqual(rows[0]["result"]["direction"], "UP")
        self.assertEqual(rows[0]["reference_price"], 4154.0)
        self.assertIn("record_hash", rows[0])

    def test_same_bar_retry_is_idempotent(self):
        kwargs = dict(
            timestamp=self.ts,
            symbol="XAUUSD",
            judges={},
            context={},
            result=self.result,
        )
        first = append_snapshot(self.root, **kwargs)
        second = append_snapshot(self.root, **kwargs)
        self.assertTrue(first["appended"])
        self.assertFalse(second["appended"])
        self.assertEqual(len(read_day(self.root, "2026-09-28")), 1)

    def test_naive_timestamp_is_rejected(self):
        with self.assertRaises(ValueError):
            append_snapshot(
                self.root,
                timestamp=datetime(2026, 9, 28, 15, 0),
                symbol="XAUUSD",
                judges={}, context={}, result=self.result,
            )

    def test_tampering_is_detected(self):
        written = append_snapshot(
            self.root,
            timestamp=self.ts,
            symbol="XAUUSD",
            judges={}, context={}, result=self.result,
        )
        path = Path(written["path"])
        row = json.loads(path.read_text(encoding="utf-8"))
        row["result"]["direction"] = "DOWN"
        path.write_text(json.dumps(row) + "\n", encoding="utf-8")
        with self.assertRaises(MemoryIntegrityError):
            read_day(self.root, "2026-09-28")

    def test_nonfinite_numbers_are_safely_serialized(self):
        append_snapshot(
            self.root,
            timestamp=self.ts,
            symbol="XAUUSD",
            judges={"footprint_delta": float("nan")},
            context={},
            result=self.result,
        )
        rows = read_day(self.root, "2026-09-28")
        self.assertIsNone(rows[0]["judges"]["footprint_delta"])


if __name__ == "__main__":
    unittest.main()
