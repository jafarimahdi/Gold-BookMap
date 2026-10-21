"""Source-vs-model attribution tests for the read-only Council report."""
from datetime import datetime, timezone

from council_shadow import decision_source_counts


def test_current_and_legacy_power_carries_are_not_counted_as_ai_models():
    start = datetime(2026, 10, 22, tzinfo=timezone.utc)
    end = datetime(2026, 10, 23, tzinfo=timezone.utc)
    rows = [
        {"timestamp": "2026-10-22T08:00:00Z", "ai_model": "none-quiet",
         "decision_source": "POWER_V2_CONFIGURED_THRESHOLD_CARRY"},
        {"timestamp": "2026-10-22T08:01:00Z", "ai_model": "gemini-3.8-flash",
         "decision_source": "STEP3"},
        {"timestamp": "2026-10-22T08:02:00Z", "ai_model": "fallback-rules-v1",
         "decision_source": "STEP3"},
        {"timestamp": "2026-10-22T08:03:00Z", "ai_model": "power-lead-v1"},
        {"timestamp": "2026-10-23T00:00:00Z", "ai_model": "gemini-outside-day"},
    ]

    counts = decision_source_counts(
        rows, start, end, "timestamp", "ai_model", "decision_source")

    assert counts == {"carried": 2, "ai": 1, "fallback": 1,
                      "quiet": 0, "other": 0}


def test_source_field_takes_precedence_over_model_name():
    start = datetime(2026, 10, 22, tzinfo=timezone.utc)
    end = datetime(2026, 10, 23, tzinfo=timezone.utc)
    counts = decision_source_counts(
        [{"timestamp": "2026-10-22T08:00:00Z", "ai_model": "gemini-3.8-flash",
          "decision_source": "POWER_V2_CONFIGURED_THRESHOLD_CARRY"}],
        start, end, "timestamp", "ai_model", "decision_source")

    assert counts["carried"] == 1
    assert counts["ai"] == 0
