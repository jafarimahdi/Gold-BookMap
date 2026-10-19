#!/usr/bin/env python3
"""Reverse-engineering tests for audit_day._fallback_stats after fusion v1.

The owner's law: any change ships with corner tests. Corners probed here:
  1. power-lead rows are their OWN bucket - never counted as "ai" answers
     (the AI was quiet when POWER carried the trade).
  2. a mixed day totals every bucket, power-lead included.
  3. the pre-24k diary path (no ai_model column at all) is untouched.
"""
import unittest

from audit_day import _fallback_stats


def row(model):
    return {"ai_model": model}


class FallbackStatsPowerLeadTest(unittest.TestCase):
    def test_power_lead_is_own_bucket_not_ai(self):
        stats = _fallback_stats([row("power-lead-v1"), row("power-lead-v1")], [])
        self.assertEqual(stats["power-lead"], 2)
        self.assertEqual(stats["ai"], 0)
        self.assertEqual(stats["total"], 2)

    def test_mixed_day_totals_every_bucket(self):
        stats = _fallback_stats(
            [row("power-lead-v1"), row("power-lead-v1"), row("gemini-2.5-flash"),
             row("fallback-rules-v1"), row("none-quiet")], [])
        self.assertEqual(stats["ai"], 1)
        self.assertEqual(stats["fallback"], 1)
        self.assertEqual(stats["none"], 1)
        self.assertEqual(stats["power-lead"], 2)
        self.assertEqual(stats["total"], 5)
        self.assertEqual(stats["source"], "ai_model column")

    def test_pre24k_diary_path_unchanged(self):
        stats = _fallback_stats(
            [], [{"ai_rationale": "FALLBACK rule fired"},
                 {"ai_rationale": "clean ai answer"}])
        self.assertEqual(stats["fallback"], 1)
        self.assertEqual(stats["ai"], 1)
        self.assertEqual(stats["power-lead"], 0)
        self.assertIn("diary", stats["source"])


if __name__ == "__main__":
    unittest.main()
