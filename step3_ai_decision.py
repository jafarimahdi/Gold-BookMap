"""
step3_ai_decision.py
====================
STEP 3: AI DECISION MAKING (Gemini API)

Feeds the full Step 2 `MarketSnapshot` to Gemini and asks it to return
BUY / SELL / HOLD plus a confidence percentage.

MULTI-KEY SUPPORT
-----------------
You can provide several Gemini keys in `.env`:

    GEMINI_API_KEY=...
    GEMINI_API_KEY_2=...
    GEMINI_API_KEY_3=...

    The engine tries them IN ORDER and, when a key hits a rate limit, pauses
    it briefly (cooldown) and moves on to the next key. Keys from the SAME
    Google project share one quota, so use keys from DIFFERENT projects or
    accounts for this to add capacity.

Runs without any key (returns HOLD) so the pipeline never crashes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import warnings
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

# Prefer Google's supported google-genai SDK. Keep a temporary legacy fallback
# so an existing local environment can still run while it is being upgraded.
try:
    from google import genai as _modern_genai
except ImportError:  # pragma: no cover
    _modern_genai = None

if _modern_genai is not None:
    genai = _modern_genai
    _GENAI_SDK = "google-genai"
else:
    # The old package prints a deprecation warning on import; keep the fallback
    # quiet and make the warning visible in the application status instead.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            import google.generativeai as genai
        except ImportError:  # pragma: no cover
            genai = None
    _GENAI_SDK = "google-generativeai-legacy" if genai is not None else "missing"

import config

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Key state: which keys are exhausted TODAY (persisted so a restart keeps it)
# --------------------------------------------------------------------------- #
_KEY_STATE_FILE = None
# A key that hits a rate limit is skipped on later cycles for this long.
# This cooldown is not a pause between keys; failover is immediate.
def _key_cooldown_minutes() -> int:
    try:
        return max(0, int(getattr(config, "AI_KEY_COOLDOWN_MINUTES", 20)))
    except (TypeError, ValueError):
        return 20


def _key_state_path():
    global _KEY_STATE_FILE
    if _KEY_STATE_FILE is None:
        from pathlib import Path
        _KEY_STATE_FILE = Path(__file__).resolve().parent / "data" / \
            "gemini_keys_state.json"
    return _KEY_STATE_FILE


def _key_id(key: str) -> str:
    """Return a non-reversible identifier for a key (never store the key)."""
    return hashlib.sha256(str(key).encode("utf-8")).hexdigest()[:16]


def _exhausted_keys() -> List[str]:
    """Return safe key IDs currently inside their cooldown.

    Only the NEW cooldown format is honoured. An OLD state file (the buggy
    "blocked for the whole day" format) is deliberately IGNORED, so upgrading
    un-blocks all keys immediately instead of waiting for midnight.
    """
    try:
        path = _key_state_path()
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
            now = datetime.now(timezone.utc)
            out = []
            for k, until in (state.get("cooldowns") or {}).items():
                try:
                    u = datetime.fromisoformat(str(until))
                    if u.tzinfo is None:
                        u = u.replace(tzinfo=timezone.utc)
                    if u > now:
                        # New state files contain only hashed key IDs. Do not
                        # return or log the raw API key.
                        out.append(str(k))
                except ValueError:
                    continue
            return out
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _mark_exhausted(key: str) -> None:
    """Pause `key` for a short cooldown (auto-recovers after it expires)."""
    try:
        path = _key_state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        state = {}
        try:
            if path.exists():
                state = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
        cooldowns = dict(state.get("cooldowns") or {})
        until = datetime.now(timezone.utc) + timedelta(
            minutes=_key_cooldown_minutes())
        # Persist only a one-way ID. Storing the raw key here would expose a
        # credential if the generated data directory is copied or committed.
        cooldowns[_key_id(key)] = until.isoformat()
        path.write_text(json.dumps({
            "date": datetime.now().date().isoformat(),
            "cooldowns": cooldowns,
        }), encoding="utf-8")
    except OSError:
        pass


def _mask(key: str) -> str:
    if len(key) < 8:
        return "***"
    return key[:4] + "..." + key[-4:]


@dataclass
class Decision:
    """AI output consumed by Step 4 (execution)."""
    action: str            # "BUY" | "SELL" | "HOLD"
    confidence: float      # 0-100
    rationale: str
    raw_response: str = ""
    model: str = config.GEMINI_MODEL
    timestamp: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat() if self.timestamp else None
        return d


class AIDecisionEngine:
    """Wraps the Gemini API into a decide(snapshot) -> Decision call."""

    SYSTEM_PROMPT = (
        "You are a professional gold (XAUUSD) trader. Given the market "
        "analysis metrics below, return exactly one trading decision.\n"
        "Respond ONLY with a single line of JSON in this exact format:\n"
        '{"action": "BUY" | "SELL" | "HOLD", "confidence": 0-100, '
        '"rationale": "short explanation"}\n'
        "Rules: only trade with high conviction; weigh trend, order flow "
        "(CVD/delta), footprint, volume profile, macro correlations AND news "
        "together; do not invent data.\n"
        "NEWS & FUNDAMENTALS: the snapshot includes real, recent news "
        "headlines and upcoming economic events. Gold is driven heavily by "
        "fundamentals, so weigh fresh, impactful news MORE than technicals:\n"
        "  - wars / geopolitical tension / risk-off  -> safe-haven demand -> bullish\n"
        "  - Fed hawkish, rate hikes, strong dollar, high real yields -> bearish\n"
        "  - Fed dovish, rate cuts, weak dollar, falling yields -> bullish\n"
        "  - hot inflation / recession fears / crisis -> bullish (hedge)\n"
        "  - risk-on, strong equities, calm markets -> neutral/bearish\n"
        "If headlines are missing or stale (hours old), rely on technicals only."
    )

    def __init__(self, api_key: Optional[str] = None,
                 model: Optional[str] = None,
                 api_keys: Optional[List[str]] = None):
        # build the key list: explicit arg -> config list -> single key
        if api_keys is not None:
            self.api_keys = [k for k in api_keys if k]
        elif getattr(config, "GEMINI_API_KEYS", None):
            self.api_keys = [k for k in config.GEMINI_API_KEYS if k]
        elif api_key:
            self.api_keys = [api_key]
        else:
            self.api_keys = [config.GEMINI_API_KEY] if config.GEMINI_API_KEY else []
        self.api_key = self.api_keys[0] if self.api_keys else ""
        self.model = model or config.GEMINI_MODEL

    # ------------------------------------------------------------------ #
    def build_prompt(self, snapshot) -> str:
        """Serialize the MarketSnapshot into the prompt payload, plus the
        session context (day of week, time, active trading session) so the AI
        can weigh when we are trading — e.g. thin Asia ranges vs high-volume
        London/New York moves.
        """
        from step2_market_analysis import snapshot_to_dict
        from session import session_context
        payload = snapshot_to_dict(snapshot)
        payload["session_context"] = session_context()
        return (
            self.SYSTEM_PROMPT + "\n\n"
            "TRADING SESSION CONTEXT (use this to judge volatility/liquidity):\n"
            + json.dumps(payload["session_context"], indent=2) + "\n\n"
            "MARKET ANALYSIS METRICS (JSON):\n"
            + json.dumps(payload, indent=2, default=str)
        )

    # ------------------------------------------------------------------ #
    def _is_quota_error(self, exc: Exception) -> bool:
        msg = str(exc)
        return ("quota" in msg.lower() or "RESOURCE_EXHAUSTED" in msg
                or "429" in msg)

    def _is_minute_limit(self, exc: Exception) -> bool:
        """True when the error is a PER-MINUTE rate limit (clears quickly).

        Distinguishes it from a per-DAY quota (which only resets at midnight).
        """
        msg = str(exc).lower()
        return ("per minute" in msg or "per_minute" in msg or "rpm" in msg
                or "minute" in msg)

    def _is_model_unavailable(self, exc: Exception) -> bool:
        """True when the MODEL is gone (404 / not found / no longer available).

        Google retires models regularly; this tells us to try the next model
        in the fallback list rather than the next key.
        """
        msg = str(exc)
        return ("404" in msg or "not found" in msg.lower()
                or "no longer available" in msg.lower()
                or "not available" in msg.lower() or "deprecated" in msg.lower())

    def _call_with_key(self, key: str, model: str, prompt: str):
        """Make exactly one Gemini call with `key` and `model`.

        A key failure is handled by `decide()`, which immediately moves to the
        next key. There is deliberately no sleep or retry here: the caller
        wants failover without waiting for a per-minute quota to recover.
        """
        if genai is _modern_genai:
            client = None
            try:
                # The supported SDK expects timeout in milliseconds. Disable
                # automatic function calling because this bot does not expose
                # tools to Gemini and only needs a text decision.
                try:
                    from google.genai import types as genai_types
                except ImportError:
                    # A minimal modern SDK mock may expose Client but not types.
                    genai_types = None

                if genai_types is not None:
                    http_options = genai_types.HttpOptions(
                        timeout=max(1000, int(getattr(
                            config, "GEMINI_REQUEST_TIMEOUT_MS", 20000)))
                    )
                    request_config = genai_types.GenerateContentConfig(
                        automatic_function_calling=(
                            genai_types.AutomaticFunctionCallingConfig(disable=True)
                        )
                    )
                    client = genai.Client(api_key=key,
                                          http_options=http_options)
                    response = client.models.generate_content(
                        model=model,
                        contents=prompt,
                        config=request_config,
                    )
                else:
                    client = genai.Client(api_key=key)
                    response = client.models.generate_content(
                        model=model,
                        contents=prompt,
                    )
            finally:
                if client is not None:
                    try:
                        client.close()
                    except Exception:
                        pass
        else:
            # Temporary fallback for environments that have not installed the
            # supported SDK yet, and for the legacy mock tests.
            genai.configure(api_key=key)
            gm = genai.GenerativeModel(model)
            response = gm.generate_content(prompt)
        return (response.text or "").strip()

    # ------------------------------------------------------------------ #
    def decide(self, snapshot) -> Decision:
        """Return a Decision for the given Step 2 MarketSnapshot.

        Tries each MODEL in the fallback list, and for each model tries each
        KEY in order. A key that hits a rate limit is paused briefly (then it
        auto-recovers); a model that returns 404 (retired) is skipped. This
        makes the bot resilient to Google's frequent model retirements.
        """
        prompt = self.build_prompt(snapshot)

        if not self.api_keys:
            logger.warning("STEP 3: GEMINI_API_KEY not set -> returning HOLD "
                           "(set the key in .env to enable AI decisions).")
            return Decision(action="HOLD", confidence=0.0,
                            rationale="Gemini API key not configured.",
                            timestamp=datetime.now(timezone.utc))
        if genai is None:
            logger.warning("STEP 3: Gemini SDK not installed "
                           "(pip install google-genai) -> HOLD.")
            return Decision(action="HOLD", confidence=0.0,
                            rationale="google-genai SDK not installed.",
                            timestamp=datetime.now(timezone.utc))

        models = getattr(config, "GEMINI_MODELS", None) or [self.model]
        exhausted = _exhausted_keys()
        last_error = ""
        tried_model = None

        for model in models:
            tried_model = model
            for key_index, key in enumerate(self.api_keys, 1):
                if _key_id(key) in exhausted:
                    continue
                try:
                    text = self._call_with_key(key, model, prompt)
                    action, confidence, rationale = self._parse_response(text)
                    logger.info("STEP 3: Gemini -> %s @ %.1f%% (key #%d, %s)",
                                action, confidence, key_index, model)
                    return Decision(action=action, confidence=confidence,
                                    rationale=rationale, raw_response=text,
                                    model=model,
                                    timestamp=datetime.now(timezone.utc))
                except Exception as exc:
                    last_error = str(exc)[:300]
                    if self._is_quota_error(exc):
                        _mark_exhausted(key)
                        logger.warning("STEP 3: key #%d rate-limited (skipping "
                                       "for %d min on later cycles); trying the "
                                       "next key immediately.",
                                       key_index, _key_cooldown_minutes())
                        continue
                    if self._is_model_unavailable(exc):
                        logger.warning("STEP 3: model %s is no longer "
                                       "available; trying the next model.",
                                       model)
                        break                    # move to the next model
                    # other error (network etc.) -> try the next key
                    logger.warning("STEP 3: call failed (key #%d, %s): %s",
                                   key_index, model, last_error[:120])
                    continue

        # nothing worked
        if self._is_quota_error(Exception(last_error)) or not last_error:
            logger.warning("STEP 3: all Gemini keys rate-limited — will retry "
                           "after a short cooldown.")
            return Decision(action="HOLD", confidence=0.0,
                            rationale="Gemini rate-limited (all keys paused)",
                            timestamp=datetime.now(timezone.utc))
        logger.warning("STEP 3: Gemini call failed on all keys/models "
                       "(last model %s).", tried_model or "?")
        return Decision(action="HOLD", confidence=0.0,
                        rationale=f"Gemini call failed: {last_error}",
                        timestamp=datetime.now(timezone.utc))

    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse_response(text: str) -> tuple:
        """Parse the AI's JSON response defensively."""
        action, confidence, rationale = "HOLD", 0.0, ""

        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            try:
                obj = json.loads(m.group(0))
                action = str(obj.get("action", "HOLD")).upper()
                try:
                    confidence = float(obj.get("confidence", 0.0))
                except (TypeError, ValueError):
                    confidence = 0.0
                rationale = str(obj.get("rationale", ""))
                return AIDecisionEngine._sanitize(action, confidence, rationale)
            except json.JSONDecodeError:
                pass

        upper = text.upper()
        if "BUY" in upper:
            action = "BUY"
        elif "SELL" in upper:
            action = "SELL"
        else:
            action = "HOLD"
        pct = re.search(r"(\d{1,3})\s*%", text)
        if pct:
            confidence = float(pct.group(1))
        return AIDecisionEngine._sanitize(action, confidence, rationale)

    @staticmethod
    def _sanitize(action: str, confidence: float, rationale: str) -> tuple:
        action = action if action in ("BUY", "SELL", "HOLD") else "HOLD"
        confidence = float(min(max(confidence, 0.0), 100.0))
        return action, confidence, rationale
