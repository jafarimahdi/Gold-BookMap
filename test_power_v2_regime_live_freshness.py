from datetime import datetime, timedelta, timezone

from power_v2_regime import classify_m5_regime


BASE = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)


def test_fresh_current_bar_tick_proves_feed_alive_but_is_not_used_for_adx():
    ticks = []
    bars = 36
    for i in range(bars):
        start = BASE + timedelta(minutes=5 * i)
        center = 4300.0 + i * 0.5
        for offset, price in ((10, center), (60, center + 0.2),
                              (120, center - 0.2), (295, center + 0.15)):
            ticks.append({"timestamp": start + timedelta(seconds=offset),
                          "price": price, "volume": 1.0, "side": "BUY"})

    final_end = BASE + timedelta(minutes=5 * bars)
    now = final_end + timedelta(seconds=90)
    # This tick is in the still-forming bar: it can validate feed freshness only.
    ticks.append({"timestamp": now - timedelta(seconds=1), "price": 9999.0,
                  "volume": 1.0, "side": "BUY"})

    result = classify_m5_regime(ticks, now=now)
    assert result["latest_tick_age_seconds"] == 1.0
    assert result["market_regime"] == "TREND"
    assert result["reason"] == "ADX_TREND"
    assert result["bars_used"] == 28
