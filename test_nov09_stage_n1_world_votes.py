"""
test_nov09_stage_n1_world_votes.py
==================================
Section `#12` Stage N1 -- WORLD-vote calibration for the two live macro/news judges
(`Bookmark #12` Leak #3, measured live on `2026-11-03` at `14:57` and `15:11`: a
`+0.02` headline score voted with the SAME `w=1.0` as a real crisis headline, and a
bare VIX `RISK_OFF` reading voted an unconditional BUY).

What is proven here (reverse-engineering corners, Law #9 single-stage isolation):
  1. a micro-score inside `NEWS_SENTIMENT_DEADBAND` casts NO vote and emits no
     `HIGH impact sentiment` panel line;
  2. a strong headline keeps its full weight; a medium one is weight-ramped;
  3. `NEWS_SENTIMENT_DEADBAND=0` + `NEWS_SENTIMENT_FULL_WEIGHT_AT=0` restores the
     exact legacy behaviour (one-line rollback);
  4. `macro_risk` votes BUY only when gold's OWN tape confirms the safe-haven bid
     (positive gold/VIX log-return correlation + price not breaking under VWAP);
  5. an unconfirmed `RISK_OFF` also stops feeding the macro-opposition brake;
  6. `MACRO_RISK_CONFIRM_ENABLED=0` restores legacy behaviour;
  7. the WORLD roster/family assignment is untouched (no judge surgery).
"""
from __future__ import annotations

import config
import judge_families
import step2_market_analysis as s2


def _mk_of(**kwargs) -> s2.OrderFlowMetrics:
    defaults = dict(
        cvd=0.0, delta=0.0, buying_pressure=50.0, selling_pressure=50.0,
        level2_bid_depth=100.0, level2_ask_depth=100.0, bid_ask_ratio=1.0,
        large_orders=0,
    )
    defaults.update(kwargs)
    return s2.OrderFlowMetrics(**defaults)


def _agg(news=None, macro=None, price=4200.0, vwap=4198.0, atr=2.0):
    """Run the real `SignalEngine.aggregate` with only WORLD inputs varied."""
    eng = s2.SignalEngine()
    return eng.aggregate(
        price,
        s2.VolatilityMetrics(atr=atr),
        s2.TrendMetrics(),
        _mk_of(),
        s2.FootprintMetrics(),
        s2.Level3Events(),
        s2.VolumeProfileMetrics(vwap=vwap),
        macro if macro is not None else s2.MacroMetrics(),
        news if news is not None else s2.NewsAndEvents(),
        regime="RANGE",
    )


def _panel(notes):
    return {row["judge"]: row for row in s2.parse_judge_panel(notes)}


# --------------------------------------------------------------------------- #
# 1) news_sentiment dead-band
# --------------------------------------------------------------------------- #
def test_n1_news_micro_score_casts_no_vote_in_high_window():
    """Leak #3: `+0.02` / `-0.02` headlines must be silent, even inside a HIGH window."""
    for score in (0.02, -0.02, 0.01, 0.0):
        news = s2.NewsAndEvents(
            sentiment_score=score, impact_level="HIGH", minutes_to_next_event=15.0
        )
        _, _, _, notes = _agg(news=news)
        joined = " | ".join(notes)
        assert "HIGH impact sentiment" not in joined
        assert "news_sentiment" not in _panel(notes)
    # the supplied 2026-11-03 value is explicitly called out as a dead-band silence
    news = s2.NewsAndEvents(sentiment_score=0.02, impact_level="HIGH", minutes_to_next_event=15.0)
    _, _, _, notes = _agg(news=news)
    assert any("inside dead-band" in n and "+0.02" in n for n in notes)


def test_n1_news_strong_headline_keeps_full_weight_and_direction():
    """A real crisis headline (+0.65 / -0.80) still votes at the full window weight."""
    for score, want_dir in ((0.65, 1.0), (-0.80, -1.0)):
        news = s2.NewsAndEvents(
            sentiment_score=score, impact_level="HIGH", minutes_to_next_event=10.0
        )
        _, _, _, notes = _agg(news=news)
        panel = _panel(notes)
        assert "news_sentiment" in panel
        assert panel["news_sentiment"]["dir"] == want_dir
        assert panel["news_sentiment"]["weight"] == 1.0
        assert any("HIGH impact sentiment" in n and "weight 1.00" in n for n in notes)


def test_n1_news_mid_score_weight_ramps_linearly():
    """+0.30 sits one third of the way from the dead-band to full trust."""
    news = s2.NewsAndEvents(sentiment_score=0.30, impact_level="HIGH", minutes_to_next_event=10.0)
    _, _, _, notes = _agg(news=news)
    panel = _panel(notes)
    assert panel["news_sentiment"]["dir"] == 1.0
    assert 0.30 <= panel["news_sentiment"]["weight"] <= 0.36
    assert any("weight 0.33" in n for n in notes)


def test_n1_news_deadband_zero_restores_legacy_micro_vote(monkeypatch):
    """One-line rollback: `NEWS_SENTIMENT_DEADBAND=0` + `FULL_WEIGHT_AT=0` = legacy."""
    monkeypatch.setattr(config, "NEWS_SENTIMENT_DEADBAND", 0.0, raising=False)
    monkeypatch.setattr(config, "NEWS_SENTIMENT_FULL_WEIGHT_AT", 0.0, raising=False)
    news = s2.NewsAndEvents(sentiment_score=0.02, impact_level="HIGH", minutes_to_next_event=15.0)
    _, _, _, notes = _agg(news=news)
    panel = _panel(notes)
    assert panel["news_sentiment"]["dir"] == 1.0
    assert panel["news_sentiment"]["weight"] == 1.0


def test_n1_news_trust_ramp_table():
    """Exact ramp table (dead-band 0.15 -> full trust 0.60)."""
    ramp = s2.SignalEngine.news_sentiment_ramp
    assert ramp(0.0) == 0.0
    assert ramp(0.02) == 0.0
    assert ramp(-0.02) == 0.0
    assert ramp(0.15) == 0.0                      # at the dead-band edge -> silent
    assert abs(ramp(0.30) - (0.15 / 0.45)) < 1e-9
    assert ramp(0.60) == 1.0
    assert ramp(0.95) == 1.0
    assert ramp(-0.90) == 1.0


def test_n1_news_outside_high_window_stays_silent():
    """No HIGH window -> no vote even for a strong headline (U-09 rule preserved)."""
    news = s2.NewsAndEvents(sentiment_score=0.90, impact_level="HIGH", minutes_to_next_event=300.0)
    _, _, _, notes = _agg(news=news)
    assert "news_sentiment" not in _panel(notes)
    assert any("treated as a normal day" in n for n in notes)


# --------------------------------------------------------------------------- #
# 2) macro_risk needs gold's own confirmation
# --------------------------------------------------------------------------- #
def test_n1_macro_risk_unconfirmed_vix_risk_off_is_silent():
    """A bare RISK_OFF VIX reading (no measured gold/VIX co-move) must not vote BUY."""
    macro = s2.MacroMetrics(vix_index=31.0, risk_sentiment="RISK_OFF", vix_correlation=0.0)
    _, _, _, notes = _agg(macro=macro)
    joined = " | ".join(notes)
    assert "macro_risk" not in _panel(notes)
    assert "risk-off -> safe haven bid" not in joined
    assert any("RISK_OFF unconfirmed by gold tape" in n for n in notes)


def test_n1_macro_risk_confirmed_votes_buy():
    """Positive gold/VIX co-move + price holding above VWAP -> the BUY vote is allowed."""
    macro = s2.MacroMetrics(vix_index=31.0, risk_sentiment="RISK_OFF", vix_correlation=0.35)
    _, _, _, notes = _agg(macro=macro, price=4200.0, vwap=4198.0)
    panel = _panel(notes)
    assert panel["macro_risk"]["dir"] == 1.0
    assert any("risk-off -> safe haven bid" in n for n in notes)


def test_n1_macro_risk_confirmed_but_tape_breaking_down_stays_silent():
    """Correlation alone is not enough: a bid missing from the tape (under VWAP) is silent."""
    macro = s2.MacroMetrics(vix_index=31.0, risk_sentiment="RISK_OFF", vix_correlation=0.35)
    _, _, _, notes = _agg(macro=macro, price=4190.0, vwap=4198.0, atr=2.0)
    assert "macro_risk" not in _panel(notes)
    assert any("under VWAP" in n for n in notes)


def test_n1_unconfirmed_risk_off_does_not_feed_the_macro_brake():
    """The unconfirmed reading must not shrink scores through `macro_pairs` either."""
    macro = s2.MacroMetrics(vix_index=31.0, risk_sentiment="RISK_OFF", vix_correlation=0.0)
    _, _, _, notes = _agg(macro=macro)
    assert not any("macro against" in n for n in notes)
    macro_ok = s2.MacroMetrics(vix_index=31.0, risk_sentiment="RISK_OFF", vix_correlation=0.35)
    _, _, _, notes_ok = _agg(macro=macro_ok, price=4200.0, vwap=4198.0)
    assert any("RETIRED (not voting)" in n for n in notes_ok)


def test_n1_macro_risk_legacy_toggle(monkeypatch):
    """`MACRO_RISK_CONFIRM_ENABLED=0` restores the unconditional legacy BUY vote."""
    monkeypatch.setattr(config, "MACRO_RISK_CONFIRM_ENABLED", False, raising=False)
    macro = s2.MacroMetrics(vix_index=31.0, risk_sentiment="RISK_OFF", vix_correlation=0.0)
    _, _, _, notes = _agg(macro=macro)
    assert _panel(notes)["macro_risk"]["dir"] == 1.0


def test_n1_macro_risk_threshold_is_strictly_positive():
    """`MACRO_RISK_MIN_VIX_CORR` is exclusive: corr == threshold does not confirm."""
    ok, why = s2.SignalEngine.macro_risk_confirmed(
        s2.MacroMetrics(vix_correlation=0.0, risk_sentiment="RISK_OFF"),
        4200.0,
        s2.VolumeProfileMetrics(vwap=4198.0),
        s2.VolatilityMetrics(atr=2.0),
    )
    assert ok is False and "corr" in why


# --------------------------------------------------------------------------- #
# 3) No roster surgery, sane defaults, silence without feeds
# --------------------------------------------------------------------------- #
def test_n1_world_roster_and_families_untouched():
    """Both WORLD judges stay wired in the same family (no judge deleted or moved)."""
    assert judge_families.JUDGE_FAMILY["macro_risk"] == "WORLD"
    assert judge_families.JUDGE_FAMILY["news_sentiment"] == "WORLD"
    assert "macro_risk" not in config.RETIRED_JUDGES
    assert "news_sentiment" not in config.RETIRED_JUDGES
    assert judge_families.FAMILY_WEIGHTS["WORLD"] == 0.5


def test_n1_config_defaults_are_the_measured_calibration():
    assert config.NEWS_SENTIMENT_DEADBAND == 0.15
    assert config.NEWS_SENTIMENT_FULL_WEIGHT_AT == 0.60
    assert config.MACRO_RISK_CONFIRM_ENABLED is True
    assert config.MACRO_RISK_MIN_VIX_CORR == 0.0
    assert config.MACRO_RISK_VWAP_TOL_ATR == 0.10


def test_n1_no_world_feeds_means_no_world_votes():
    """Offline replay / feed outage: empty macro + empty news must stay completely silent."""
    _, _, _, notes = _agg(news=s2.NewsAndEvents(), macro=s2.MacroMetrics())
    panel = _panel(notes)
    assert "macro_risk" not in panel
    assert "news_sentiment" not in panel
    assert not any("safe haven bid" in n for n in notes)
