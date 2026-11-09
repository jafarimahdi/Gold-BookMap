"""
test_nov09_stage_d1_doorman_hardening.py
========================================
Section `#13` Stage D1 -- Step-3 Doorman parse + cache hardening.

Three live defects isolated in the Section `#13` manager check (Strict Law #9:
Doorman layer only -- no judge weight, no gate threshold, no exit rule touched):

  (1) `_parse_response` guessed an action from ANY occurrence of the word "BUY"
      in free prose (a refusal reading "I would NOT buy here" became a BUY) and
      scraped confidence from the FIRST percentage in the text ("spread 0.2%"
      -> confidence 0.2, or "75% of the time" -> 75). An unparseable model
      response could therefore become an unvalidated trade.
  (2) `_prompt_hash` ignored the news perimeter and the signal confidence, so a
      decision cached in a `QUIET` cycle could be re-used minutes later inside a
      `WARNING` window, or seconds before a HIGH event.
  (3) `_check_cache` returned the SHARED cached `Decision` object, so any
      downstream mutation silently rewrote the cache entry for later cycles.
"""
from __future__ import annotations

import step3_ai_decision as s3
from step3_ai_decision import AIDecisionEngine


def _snap(**over):
    class _News:
        news_state = "QUIET"
        impact_level = "LOW"
        minutes_to_next_event = 240.0
        high_impact_minutes = None
        sentiment_score = 0.0

    class _S:
        price = 4200.0
        signal_direction = "BUY"
        signal_strength = 45.0
        signal_confidence = 62.0
        regime = "TREND"
        news = _News()

    s = _S()
    for k, v in over.items():
        if k == "news":
            for nk, nv in v.items():
                setattr(s.news, nk, nv)
        else:
            setattr(s, k, v)
    return s


# --------------------------------------------------------------------------- #
# (1) fail-closed parsing
# --------------------------------------------------------------------------- #
def test_d1_prose_mentioning_buy_is_not_a_buy():
    """The exact leak: a refusal that says 'buy' must never become a BUY decision."""
    action, conf, why = AIDecisionEngine._parse_response(
        "I would NOT buy here. The setup is stretched and liquidity is thin."
    )
    assert action == "HOLD" and conf == 0.0
    assert "fail-closed HOLD" in why


def test_d1_json_still_parses_and_wins():
    action, conf, why = AIDecisionEngine._parse_response(
        '{"action": "sell", "confidence": 71, "rationale": "VWAP rejection"}'
    )
    assert action == "SELL" and conf == 71.0 and why == "VWAP rejection"


def test_d1_json_embedded_in_prose_is_found():
    text = ('Here is my call.\n```json\n{"action":"BUY","confidence":66,'
            '"rationale":"breakout"}\n```\nDone.')
    action, conf, why = AIDecisionEngine._parse_response(text)
    assert action == "BUY" and conf == 66.0 and why == "breakout"


def test_d1_two_objects_picks_the_first_valid_one():
    text = '{"action":"HOLD","confidence":10} and then {"action":"BUY","confidence":90}'
    action, conf, _ = AIDecisionEngine._parse_response(text)
    assert action == "HOLD" and conf == 10.0


def test_d1_broken_json_fails_closed():
    action, conf, _ = AIDecisionEngine._parse_response('{"action": "BUY", "confidence": ')
    assert action == "HOLD" and conf == 0.0


def test_d1_empty_response_fails_closed():
    for text in ("", None, "   \n  ", "ERROR 429 RESOURCE_EXHAUSTED"):
        action, conf, _ = AIDecisionEngine._parse_response(text)
        assert action == "HOLD" and conf == 0.0


def test_d1_legacy_keyword_path_requires_explicit_confidence(monkeypatch):
    """Opt-in legacy path: keyword BUY + explicit confidence >= floor is allowed."""
    import config
    monkeypatch.setattr(config, "AI_PARSE_KEYWORD_FALLBACK", True, raising=False)
    monkeypatch.setattr(config, "AI_PARSE_MIN_KEYWORD_CONF", 60.0, raising=False)
    action, conf, _ = AIDecisionEngine._parse_response("CALL: BUY, confidence: 72")
    assert action == "BUY" and conf == 72.0


def test_d1_legacy_keyword_path_ignores_bare_percentages(monkeypatch):
    """A percentage in prose is not a confidence label."""
    import config
    monkeypatch.setattr(config, "AI_PARSE_KEYWORD_FALLBACK", True, raising=False)
    action, conf, _ = AIDecisionEngine._parse_response("BUY bias, spread 0.2% and 75% odds")
    assert action == "HOLD" and conf == 0.0


def test_d1_legacy_keyword_path_low_confidence_fails_closed(monkeypatch):
    import config
    monkeypatch.setattr(config, "AI_PARSE_KEYWORD_FALLBACK", True, raising=False)
    monkeypatch.setattr(config, "AI_PARSE_MIN_KEYWORD_CONF", 60.0, raising=False)
    action, conf, _ = AIDecisionEngine._parse_response("SELL, confidence: 40")
    assert action == "HOLD" and conf == 0.0


def test_d1_sanitize_still_clamps_garbage():
    assert AIDecisionEngine._sanitize("FLIP", 500.0, "x")[0] == "HOLD"
    assert AIDecisionEngine._sanitize("buy", 500.0, "x") == ("HOLD", 100.0, "x")


# --------------------------------------------------------------------------- #
# (2) perimeter-aware cache key
# --------------------------------------------------------------------------- #
def test_d1_cache_key_changes_with_news_perimeter():
    eng = AIDecisionEngine.__new__(AIDecisionEngine)
    quiet = eng._prompt_hash(_snap())
    warning = eng._prompt_hash(_snap(news={"news_state": "WARNING"}))
    near_high = eng._prompt_hash(
        _snap(news={"news_state": "WARNING", "impact_level": "HIGH",
                    "high_impact_minutes": 6.0, "minutes_to_next_event": 6.0})
    )
    far_high = eng._prompt_hash(
        _snap(news={"news_state": "QUIET", "impact_level": "HIGH",
                    "high_impact_minutes": 300.0, "minutes_to_next_event": 300.0})
    )
    assert quiet != warning != near_high
    assert near_high != far_high


def test_d1_cache_key_changes_with_signal_confidence():
    eng = AIDecisionEngine.__new__(AIDecisionEngine)
    assert eng._prompt_hash(_snap(signal_confidence=40.0)) != eng._prompt_hash(
        _snap(signal_confidence=80.0)
    )


def test_d1_cache_key_stable_for_same_state():
    eng = AIDecisionEngine.__new__(AIDecisionEngine)
    assert eng._prompt_hash(_snap()) == eng._prompt_hash(_snap())


def test_d1_cache_key_legacy_mode_restores_five_field_hash(monkeypatch):
    import config
    eng = AIDecisionEngine.__new__(AIDecisionEngine)
    monkeypatch.setattr(config, "AI_CACHE_KEY_V2", False, raising=False)
    legacy_quiet = eng._prompt_hash(_snap())
    legacy_warning = eng._prompt_hash(_snap(news={"news_state": "WARNING"}))
    assert legacy_quiet != legacy_warning
    assert legacy_quiet == eng._prompt_hash(_snap(signal_confidence=99.0))


# --------------------------------------------------------------------------- #
# (3) cached decisions are copied, never shared
# --------------------------------------------------------------------------- #
def test_d1_cache_hit_returns_an_independent_copy():
    eng = AIDecisionEngine.__new__(AIDecisionEngine)
    snap = _snap()
    decision = s3.Decision(action="BUY", confidence=70.0, rationale="x", model="gemini-test")
    eng._update_cache(snap, decision)

    first = eng._check_cache(snap)
    assert first is not None
    first.action = "SELL"
    first.confidence = 1.0

    second = eng._check_cache(snap)
    assert second is not None
    assert second.action == "BUY" and second.confidence == 70.0
    assert eng._PROMPT_CACHE["decision"].action == "BUY"


def test_d1_cache_miss_outside_window():
    import config
    eng = AIDecisionEngine.__new__(AIDecisionEngine)
    eng._update_cache(_snap(), s3.Decision(action="BUY", confidence=70.0, rationale="x",
                                          model="gemini-test"))
    eng._PROMPT_CACHE["timestamp"] = eng._PROMPT_CACHE["timestamp"].replace(year=2000)
    assert eng._check_cache(_snap()) is None


# --------------------------------------------------------------------------- #
# (4) safety invariants that must survive this change
# --------------------------------------------------------------------------- #
def test_d1_fail_closed_defaults_unchanged():
    import config
    assert config.AI_PARSE_KEYWORD_FALLBACK is False
    assert config.AI_PARSE_MIN_KEYWORD_CONF == 60.0
    assert config.AI_CACHE_KEY_V2 is True
    # Fail-closed invariant: `decide()` only enters the synthetic fallback when the
    # MASTER switch `AI_ALLOW_SYNTHETIC_FALLBACK` is on (step3_ai_decision.py:1475);
    # `AI_FALLBACK_ENABLED` is only the inner guard ANDed inside `_fallback_decision`.
    # Neither may be on by default; the CONJUNCTION is the property that must stay
    # false. `AI_FALLBACK_ENABLED` is an owner-managed `.env` value, so it is never
    # asserted on its own (a live `.env` may carry it as `1` by design).
    assert config.AI_ALLOW_SYNTHETIC_FALLBACK is False
    assert not (config.AI_FALLBACK_ENABLED and config.AI_ALLOW_SYNTHETIC_FALLBACK)
    assert config.AI_DOORMAN_META_GATE_ENABLED is True


def test_d1_fallback_decision_never_fires_while_master_switch_off(monkeypatch):
    """Even with `AI_FALLBACK_ENABLED=1` in `.env`, a failure must stay a HOLD."""
    import config
    monkeypatch.setattr(config, "AI_ALLOW_SYNTHETIC_FALLBACK", False, raising=False)
    monkeypatch.setattr(config, "AI_FALLBACK_ENABLED", True, raising=False)
    eng = AIDecisionEngine.__new__(AIDecisionEngine)
    assert eng._fallback_decision(_snap()) is None


def test_d1_meta_gate_still_vetoes_opposite_side():
    """The Doorman still owns veto-only rights: primary NEUTRAL/BUY vs AI side."""
    import inspect
    src = inspect.getsource(s3)
    assert "DOORMAN META-GATE" in src
    assert 'action = "HOLD"' in src
    # never flips BUY <-> SELL
    assert "action = orig_act" not in src.replace("orig_act = action", "")
