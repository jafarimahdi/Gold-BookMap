"""The offline archive replay pins M5 without leaking its interval to callers."""
from datetime import datetime, timezone

import power_v2_archive_replay as archive_replay
import power_v2_regime
import pytest


def test_archive_replay_uses_m5_and_restores_caller_interval(monkeypatch):
    original = power_v2_regime.BAR_SECONDS
    power_v2_regime.set_bar_seconds(180)
    observed = []

    def fake_regime(_ticks, *, now):
        observed.append(power_v2_regime.BAR_SECONDS)
        return {"market_regime": "UNKNOWN", "breakout_confirmed": False,
                "bar_seconds": power_v2_regime.BAR_SECONDS}

    monkeypatch.setattr(archive_replay, "classify_m5_regime", fake_regime)
    monkeypatch.setattr(
        archive_replay, "build_m5_inputs",
        lambda *_args, **_kwargs: {"context": {}, "judges": {}, "diagnostics": {}})
    monkeypatch.setattr(archive_replay, "decide", lambda _judges, *, context: {"action": "HOLD"})
    ticks = [
        {"timestamp": datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)},
        {"timestamp": datetime(2026, 10, 22, 12, 15, tzinfo=timezone.utc)},
    ]

    try:
        rows = archive_replay.replay(
            ticks, tick_size=0.1, min_trades=1, max_tick_age_seconds=60.0)
        assert rows
        assert observed
        assert set(observed) == {300}
        assert power_v2_regime.BAR_SECONDS == 180
    finally:
        power_v2_regime.set_bar_seconds(original)


def test_archive_replay_restores_caller_interval_when_builder_raises(monkeypatch):
    original = power_v2_regime.BAR_SECONDS
    power_v2_regime.set_bar_seconds(180)
    def fail_builder(*_args, **_kwargs):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(archive_replay, "build_m5_inputs", fail_builder)
    ticks = [
        {"timestamp": datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)},
        {"timestamp": datetime(2026, 10, 22, 12, 15, tzinfo=timezone.utc)},
    ]

    try:
        with pytest.raises(RuntimeError, match="fixture failure"):
            archive_replay.replay(
                ticks, tick_size=0.1, min_trades=1, max_tick_age_seconds=60.0)
        assert power_v2_regime.BAR_SECONDS == 180
    finally:
        power_v2_regime.set_bar_seconds(original)
