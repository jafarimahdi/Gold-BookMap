"""
test_nov09_stage_n2_news_perimeter.py
=====================================
Section `#12` Stage N2 -- news-perimeter alignment + calendar hygiene
(four-pass items (3) and (4) of the Section `#12` manager check).

Defects healed (Strict Law #9: news/perimeter layer only, no judge weight,
no gate threshold, no exit rule touched):
  (3) `_high_now` paired `EconomicCalendar.classify()`'s "most important event of
      the week" impact level with `news_state()`'s "nearest event of ANY impact"
      minutes -> a HIGH event days away could open a HIGH boost window while the
      nearby event was a LOW one. Now the window reads the signed minutes to the
      NEAREST HIGH event.
  (4) `EconomicCalendar` hard-coded `Path(__file__).parent/"data"` (breaking
      `conftest`/replay isolation) and `get_upcoming_events()` fell back to
      FABRICATED FOMC/CPI/NFP dates, which could manufacture a real BLACKOUT
      window during a feed outage. Now the cache follows `config.DATA_DIR` and an
      empty feed returns an EMPTY calendar (`NEWS_ALLOW_FAKE_EVENTS=1` opts back in).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import config
import step2_market_analysis as s2


NOW = datetime(2026, 11, 9, 15, 0, tzinfo=timezone.utc)


def _event(title: str, impact: str, minutes: float) -> dict:
    return {
        "title": title,
        "impact": impact,
        "country": "USD",
        "date": (NOW + timedelta(minutes=minutes)).isoformat(),
        "forecast": "",
        "previous": "",
    }


def _mk_of(**kwargs) -> s2.OrderFlowMetrics:
    defaults = dict(
        cvd=0.0, delta=0.0, buying_pressure=50.0, selling_pressure=50.0,
        level2_bid_depth=100.0, level2_ask_depth=100.0, bid_ask_ratio=1.0,
        large_orders=0,
    )
    defaults.update(kwargs)
    return s2.OrderFlowMetrics(**defaults)


def _agg(news=None):
    eng = s2.SignalEngine()
    return eng.aggregate(
        4200.0,
        s2.VolatilityMetrics(atr=2.0),
        s2.TrendMetrics(),
        _mk_of(),
        s2.FootprintMetrics(),
        s2.Level3Events(),
        s2.VolumeProfileMetrics(vwap=4198.0),
        s2.MacroMetrics(),
        news if news is not None else s2.NewsAndEvents(),
        regime="RANGE",
    )


def _panel(notes):
    return {row["judge"]: row for row in s2.parse_judge_panel(notes)}


# --------------------------------------------------------------------------- #
# (3) perimeter alignment
# --------------------------------------------------------------------------- #
def test_n2_nearest_high_event_picks_the_closest_high():
    cal = s2.EconomicCalendar()
    events = [
        _event("CPI (far)", "HIGH", 4 * 24 * 60),
        _event("NFP (near)", "HIGH", 45),
        _event("Housing starts", "LOW", 5),
    ]
    mins, title = cal.nearest_high_event(events, now=NOW)
    assert title == "NFP (near)"
    assert abs(mins - 45.0) < 1e-6


def test_n2_nearest_high_event_none_when_no_high_exists():
    cal = s2.EconomicCalendar()
    mins, title = cal.nearest_high_event([_event("Housing", "LOW", 10)], now=NOW)
    assert mins is None and title == ""


def test_n2_distant_high_event_no_longer_opens_a_high_window():
    """The exact bug: a HIGH event 300 min away while the nearest gateable event is
    a MEDIUM one 5 min away (`news_state` skips LOW, so minutes refer to the MEDIUM)."""
    cal = s2.EconomicCalendar()
    events = [_event("FOMC", "HIGH", 300), _event("Retail sales", "MEDIUM", 5)]
    state, mins_to_next, _ = cal.news_state(events, now=NOW)
    hi_mins, hi_title = cal.nearest_high_event(events, now=NOW)
    assert abs(mins_to_next - 5) < 1e-6
    assert hi_title == "FOMC" and abs(hi_mins - 300) < 1e-6

    news = s2.NewsAndEvents(
        sentiment_score=0.90, impact_level="HIGH", minutes_to_next_event=mins_to_next,
        high_impact_minutes=hi_mins, high_impact_title=hi_title,
    )
    _, _, _, notes = _agg(news=news)
    assert "news_sentiment" not in _panel(notes)
    assert any("HIGH event is 300 min away" in n for n in notes)


def test_n2_legacy_path_would_have_opened_the_window():
    """Same impact/minutes without the new anchor reproduces the OLD (buggy) boost."""
    news = s2.NewsAndEvents(
        sentiment_score=0.90, impact_level="HIGH", minutes_to_next_event=5.0,
    )
    _, _, _, notes = _agg(news=news)
    assert _panel(notes)["news_sentiment"]["dir"] == 1.0


def test_n2_near_high_event_still_boosts():
    news = s2.NewsAndEvents(
        sentiment_score=0.90, impact_level="HIGH", minutes_to_next_event=45.0,
        high_impact_minutes=45.0, high_impact_title="NFP",
    )
    _, _, _, notes = _agg(news=news)
    assert _panel(notes)["news_sentiment"]["weight"] == 1.0


def test_n2_just_released_high_event_counts_inside_the_window():
    """A HIGH event 30 min in the past is still within the boost window (signed minutes)."""
    news = s2.NewsAndEvents(
        sentiment_score=-0.70, impact_level="HIGH", minutes_to_next_event=-30.0,
        high_impact_minutes=-30.0, high_impact_title="CPI",
    )
    _, _, _, notes = _agg(news=news)
    assert _panel(notes)["news_sentiment"]["dir"] == -1.0


def test_n2_high_window_still_disabled_for_empty_news():
    _, _, _, notes = _agg(news=s2.NewsAndEvents())
    assert "news_sentiment" not in _panel(notes)


# --------------------------------------------------------------------------- #
# (4) calendar hygiene
# --------------------------------------------------------------------------- #
def test_n2_calendar_cache_follows_config_data_dir(tmp_path, monkeypatch):
    cal = s2.EconomicCalendar()
    monkeypatch.setattr(config, "DATA_DIR", tmp_path, raising=False)
    assert cal._cache_path() == tmp_path / "calendar_cache.json"
    events = [_event("CPI", "HIGH", 60)]
    cal._write_cached_events(events)
    disk = json.loads((tmp_path / "calendar_cache.json").read_text(encoding="utf-8"))
    assert disk["events"][0]["title"] == "CPI"
    got, fresh = cal._read_cached_events()
    assert fresh is True and got[0]["title"] == "CPI"
    # never writes into the repo data dir while a test sandbox is active
    assert not (tmp_path.parent / "data" / "calendar_cache.json").exists()


def test_n2_empty_calendar_returns_empty_not_fabricated(monkeypatch):
    cal = s2.EconomicCalendar()
    monkeypatch.setattr(cal, "fetch_live_events", lambda max_events=20: [])
    monkeypatch.setattr(config, "NEWS_ALLOW_FAKE_EVENTS", False, raising=False)
    assert cal.get_upcoming_events() == []
    assert cal.fallback_events()          # the placeholder generator still exists


def test_n2_fake_events_only_when_opted_in(monkeypatch):
    cal = s2.EconomicCalendar()
    monkeypatch.setattr(cal, "fetch_live_events", lambda max_events=20: [])
    monkeypatch.setattr(config, "NEWS_ALLOW_FAKE_EVENTS", True, raising=False)
    events = cal.get_upcoming_events()
    assert events and all("title" in e for e in events)


def test_n2_empty_calendar_can_never_manufacture_a_blackout(monkeypatch):
    """The original danger: placeholder FOMC dates inside the blackout window."""
    cal = s2.EconomicCalendar()
    monkeypatch.setattr(cal, "fetch_live_events", lambda max_events=20: [])
    monkeypatch.setattr(config, "NEWS_ALLOW_FAKE_EVENTS", False, raising=False)
    events = cal.get_upcoming_events()
    state, _, _ = cal.news_state(events, now=NOW)
    assert state == "QUIET"
    # and the legacy placeholders WOULD gate real trading 10 minutes before their
    # invented FOMC date -- i.e. a pure feed outage could have blocked entries.
    legacy = cal.fallback_events()
    fomc = next(e for e in legacy if "FOMC" in e["title"])
    fomc_dt = datetime.fromisoformat(fomc["date"])
    near_placeholder = fomc_dt - timedelta(minutes=10)
    state_legacy, mins_legacy, title_legacy = cal.news_state(legacy, now=near_placeholder)
    assert state_legacy == "BLACKOUT" and "FOMC" in title_legacy
    assert abs(mins_legacy - 10.0) < 1e-6


def test_n2_config_default_disallows_fake_events():
    assert config.NEWS_ALLOW_FAKE_EVENTS is False
    assert config.NEWS_PERIMETER_IGNORE_LOW is True
    assert config.NEWS_HIGH_WINDOW_MINUTES == 120.0


def test_n2_snapshot_defaults_keep_legacy_consumers_working():
    news = s2.NewsAndEvents()
    assert news.high_impact_minutes is None
    assert news.high_impact_title == ""
    assert news.impact_level == "LOW"
