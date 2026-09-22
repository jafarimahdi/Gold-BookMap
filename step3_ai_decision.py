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

v5.2 CRITICAL:
- Reduced prompt size (15 bars, 5 headlines) for 4s vs 20s latency
- AI caching 5 min for similar market conditions
- Fallback rule-based decision when AI fails (strength>35 + CVD+L2+L3 align)
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
        "If headlines are missing or stale (hours old), rely on technicals only.\n"
        "L3 DATA: large order events and whale walls are institutional levels — "
        "250+ lots near price = strong support/resistance."
    )

    # v5.2: Prompt cache for similar market conditions
    _PROMPT_CACHE = {"hash": None, "decision": None, "timestamp": None, "price": 0.0}

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

        v5.2: Trimmed to 15 bars, 5 headlines, 3 zones, 100 L3 events for 4s latency
        """
        from step2_market_analysis import snapshot_to_dict
        from session import session_context
        try:
            max_bars = int(getattr(config, "AI_MAX_PROMPT_BARS", 15))
            max_headlines = int(getattr(config, "AI_MAX_HEADLINES", 5))
        except:
            max_bars = 15
            max_headlines = 5

        payload = snapshot_to_dict(snapshot)

        # v5.8 L3 Enhanced: Trim + add L3 top levels, net flow, queue
        if "news" in payload:
            if "news_headlines" in payload["news"]:
                payload["news"]["news_headlines"] = payload["news"]["news_headlines"][:max_headlines]
            if "upcoming_events" in payload["news"]:
                payload["news"]["upcoming_events"] = payload["news"]["upcoming_events"][:3]
        if "order_blocks" in payload:
            payload["order_blocks"] = payload["order_blocks"][:3]
        if "footprint" in payload and "price_levels" in payload["footprint"]:
            fp = payload["footprint"]
            if isinstance(fp.get("price_levels"), dict) and len(fp["price_levels"]) > 10:
                try:
                    levels = fp["price_levels"]
                    sorted_levels = sorted(levels.items(), key=lambda x: x[1].get("buy",0)+x[1].get("sell",0) if isinstance(x[1], dict) else 0, reverse=True)[:10]
                    fp["price_levels"] = dict(sorted_levels)
                except:
                    fp["price_levels"] = {}
        if "level3" in payload:
            if "order_events" in payload["level3"]:
                payload["level3"]["order_events"] = payload["level3"]["order_events"][-100:]
            if "order_book" in payload["level3"]:
                ob = payload["level3"]["order_book"]
                if isinstance(ob, dict):
                    ob["bids"] = ob.get("bids", [])[:10]
                    ob["asks"] = ob.get("asks", [])[:10]
            # v5.8 Enhanced L3 summary for AI
            try:
                l3 = payload["level3"]
                # Top 3 L3 bid/ask levels by size
                bids = sorted(l3.get("order_book", {}).get("bids", []), key=lambda x: x[1] if len(x)>1 else 0, reverse=True)[:3]
                asks = sorted(l3.get("order_book", {}).get("asks", []), key=lambda x: x[1] if len(x)>1 else 0, reverse=True)[:3]
                # Net aggressive flow
                buy_vol = float(l3.get("aggressive_buy_volume", 0) or 0)
                sell_vol = float(l3.get("aggressive_sell_volume", 0) or 0)
                net_flow = buy_vol - sell_vol
                # Iceberg levels with refills
                icebergs = l3.get("iceberg_levels", {}) or {}
                top_icebergs = sorted(icebergs.items(), key=lambda kv: kv[1], reverse=True)[:3]
                # Spoof levels
                spoofs = l3.get("spoof_levels", {}) or {}
                # Queue pos proxy: use imbalance + bid/ask ratio from order_flow if available
                payload["l3_enhanced"] = {
                    "top_bids": [{"price": float(p), "size": float(s), "dist_pct": round(abs(float(p)-float(payload.get("price",0)))/float(payload.get("price",1))*100,3) if payload.get("price") else 0} for p,s in bids],
                    "top_asks": [{"price": float(p), "size": float(s), "dist_pct": round(abs(float(p)-float(payload.get("price",0)))/float(payload.get("price",1))*100,3) if payload.get("price") else 0} for p,s in asks],
                    "net_aggressive_flow_M5": round(net_flow,1),
                    "aggressive_buys": l3.get("aggressive_buys",0),
                    "aggressive_sells": l3.get("aggressive_sells",0),
                    "buy_volume": round(buy_vol,1),
                    "sell_volume": round(sell_vol,1),
                    "top_icebergs": [{"price": float(k), "refills": int(v)} for k,v in top_icebergs],
                    "spoof_levels": [{"price": float(k), "count": int(v)} for k,v in list(spoofs.items())[:3]],
                    "large_order_events": l3.get("large_order_events",0),
                    "iceberg_events": l3.get("iceberg_events",0),
                    "spoof_events": l3.get("spoof_events",0),
                    "ofi_l3": l3.get("ofi",0),
                    "imbalance": round(l3.get("order_book_imbalance",0),3),
                    "buy_streak": l3.get("buy_streak",0),
                    "sell_streak": l3.get("sell_streak",0),
                }
                # Keep old summary for backward compat
                payload["l3_summary"] = payload["l3_enhanced"]
            except Exception as e:
                try:
                    payload["l3_summary"] = {
                        "large_order_events": payload["level3"].get("large_order_events", 0),
                        "iceberg_events": payload["level3"].get("iceberg_events", 0),
                        "ofi_l3": payload["level3"].get("ofi", 0),
                        "imbalance": payload["level3"].get("order_book_imbalance", 0),
                        "error": str(e)[:100]
                    }
                except:
                    pass

        payload["session_context"] = session_context()
        return (
            self.SYSTEM_PROMPT + "\n\n"
            "TRADING SESSION CONTEXT (use this to judge volatility/liquidity):\n"
            + json.dumps(payload["session_context"], indent=2) + "\n\n"
            "MARKET ANALYSIS METRICS (JSON) — trimmed to last {} bars, {} headlines for speed:\n".format(max_bars, max_headlines)
            + json.dumps(payload, indent=2, default=str)
        )

    def _prompt_hash(self, snapshot) -> str:
        """Hash of key market state for caching"""
        try:
            key = f"{snapshot.price:.1f}_{snapshot.signal_direction}_{snapshot.signal_strength:.0f}_{snapshot.regime}_{snapshot.news.news_state}"
            return hashlib.md5(key.encode()).hexdigest()[:8]
        except:
            return ""

    def _check_cache(self, snapshot) -> Optional[Decision]:
        """v5.2: Check if similar market condition cached within 5 min"""
        try:
            cache_min = int(getattr(config, "AI_CACHE_MINUTES", 5))
            if cache_min <= 0:
                return None
            now = datetime.now(timezone.utc)
            cached = self._PROMPT_CACHE
            if cached["hash"] and cached["timestamp"]:
                age_min = (now - cached["timestamp"]).total_seconds() / 60.0
                if age_min < cache_min:
                    # Check price proximity <0.2%
                    if abs(snapshot.price - cached["price"]) / snapshot.price < 0.002:
                        h = self._prompt_hash(snapshot)
                        if h == cached["hash"] and cached["decision"]:
                            logger.info("STEP 3: Using cached AI decision (age %.1f min, hash %s)", age_min, h)
                            return cached["decision"]
        except:
            pass
        return None

    def _update_cache(self, snapshot, decision: Decision):
        try:
            self._PROMPT_CACHE["hash"] = self._prompt_hash(snapshot)
            self._PROMPT_CACHE["decision"] = decision
            self._PROMPT_CACHE["timestamp"] = datetime.now(timezone.utc)
            self._PROMPT_CACHE["price"] = float(snapshot.price or 0.0)
        except:
            pass

    def _fallback_decision(self, snapshot) -> Optional[Decision]:
        """v5.8 L3 Enhanced: Rule-based fallback when AI fails — fast 10s, uses L3 whale+iceberg+netflow"""
        try:
            if not bool(getattr(config, "AI_FALLBACK_ENABLED", True)):
                return None
            fallback_strength = float(getattr(config, "AI_FALLBACK_STRENGTH", 35.0))
            fallback_conf = float(getattr(config, "AI_FALLBACK_CONFIDENCE", 70.0))
            l3_enabled = bool(getattr(config, "AI_FALLBACK_L3_ENABLED", True))
            netflow_thr = float(getattr(config, "L3_NET_FLOW_THRESHOLD", 100.0))

            sig_strength = float(getattr(snapshot, "signal_strength", 0.0) or 0.0)
            sig_dir = str(getattr(snapshot, "signal_direction", "NEUTRAL")).upper()

            # Even if strength low, L3 can trigger if strong
            of = getattr(snapshot, "order_flow", None)
            l3 = getattr(snapshot, "level3", None)
            if not of or not l3:
                return None

            # L3 Enhanced fallback: whale + iceberg + net flow
            if l3_enabled:
                try:
                    # Net aggressive flow
                    net_flow = float(getattr(l3, "aggressive_buy_volume", 0) - getattr(l3, "aggressive_sell_volume", 0))
                    whale_bids = []
                    whale_asks = []
                    price = float(getattr(snapshot, "price", 0) or 0)
                    thr = float(getattr(config, "L3_WHALE_THRESHOLD", 100.0))
                    for p,s in (getattr(l3, "order_book", {}).get("bids", []) or []):
                        if s >= thr and price>0 and abs(p-price)/price <= 0.005:
                            whale_bids.append((p,s))
                    for p,s in (getattr(l3, "order_book", {}).get("asks", []) or []):
                        if s >= thr and price>0 and abs(p-price)/price <= 0.005:
                            whale_asks.append((p,s))
                    iceberg = int(getattr(l3, "iceberg_events", 0) or 0)
                    # Strong L3 BUY: whale bid + iceberg + net buy flow
                    if len(whale_bids)>0 and iceberg>=2 and net_flow > netflow_thr:
                        rationale = f"L3 FALLBACK BUY: whale bid {len(whale_bids)} + iceberg {iceberg} + net flow +{net_flow:.0f} -> BUY (AI failed, M5)"
                        logger.info("STEP 3: L3 FALLBACK BUY @ %.0f%% (%s)", fallback_conf, rationale)
                        return Decision(action="BUY", confidence=fallback_conf, rationale=rationale, model="fallback-l3", timestamp=datetime.now(timezone.utc))
                    if len(whale_asks)>0 and iceberg>=2 and net_flow < -netflow_thr:
                        rationale = f"L3 FALLBACK SELL: whale ask {len(whale_asks)} + iceberg {iceberg} + net flow {net_flow:.0f} -> SELL (AI failed, M5)"
                        logger.info("STEP 3: L3 FALLBACK SELL @ %.0f%% (%s)", fallback_conf, rationale)
                        return Decision(action="SELL", confidence=fallback_conf, rationale=rationale, model="fallback-l3", timestamp=datetime.now(timezone.utc))
                    # Spoof invert fallback
                    spoof_events = int(getattr(l3, "spoof_events", 0) or 0)
                    if spoof_events>=1:
                        spoof_levels = getattr(l3, "spoof_levels", {}) or {}
                        spoof_bid = sum(1 for p in spoof_levels if p < price)
                        spoof_ask = sum(1 for p in spoof_levels if p > price)
                        if spoof_ask>0 and net_flow>50:
                            rationale = f"L3 FALLBACK SPOOF_INVERT fake asks {spoof_ask} + net buy {net_flow:.0f} -> BUY trap"
                            return Decision(action="BUY", confidence=fallback_conf, rationale=rationale, model="fallback-spoof", timestamp=datetime.now(timezone.utc))
                        if spoof_bid>0 and net_flow<-50:
                            rationale = f"L3 FALLBACK SPOOF_INVERT fake bids {spoof_bid} + net sell {net_flow:.0f} -> SELL trap"
                            return Decision(action="SELL", confidence=fallback_conf, rationale=rationale, model="fallback-spoof", timestamp=datetime.now(timezone.utc))
                except Exception as e:
                    logger.debug("L3 fallback error: %s", e)

            # Original fallback: CVD+L2+L3 align
            if sig_strength < fallback_strength or sig_dir not in ("BUY", "SELL"):
                return None

            cvd_sign = 1 if float(getattr(of, "cvd", 0) or 0) > 0 else -1 if float(getattr(of, "cvd", 0) or 0) < 0 else 0
            l2_imb = float(getattr(of, "depth_imbalance", 0) or 0)
            l3_ofi = float(getattr(l3, "ofi", 0) or 0)

            dir_sign = 1 if sig_dir == "BUY" else -1

            aligns = 0
            if cvd_sign == dir_sign:
                aligns += 1
            if (l2_imb > 0 and dir_sign > 0) or (l2_imb < 0 and dir_sign < 0):
                aligns += 1
            if (l3_ofi > 0 and dir_sign > 0) or (l3_ofi < 0 and dir_sign < 0):
                aligns += 1

            if aligns >= 2:
                rationale = f"Fallback rule-based: strength {sig_strength:.1f} >= {fallback_strength}, CVD/L2/L3 aligned {aligns}/3 -> {sig_dir} (AI failed)"
                logger.info("STEP 3: FALLBACK decision -> %s @ %.0f%% (%s)", sig_dir, fallback_conf, rationale)
                return Decision(action=sig_dir, confidence=fallback_conf, rationale=rationale,
                                model="fallback-rule", timestamp=datetime.now(timezone.utc))
        except Exception as e:
            logger.warning("STEP 3: Fallback calc failed: %s", e)
        return None

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
                    # v5.3 fix: 8s is rejected by Gemini (min deadline). Enforce 15s minimum.
                    try:
                        timeout_s = int(getattr(config, "AI_TIMEOUT_SECONDS", 20))
                        timeout_s = max(15, timeout_s)  # Gemini minimum
                        timeout_ms = timeout_s * 1000
                    except:
                        timeout_ms = max(15000, int(getattr(
                            config, "GEMINI_REQUEST_TIMEOUT_MS", 20000)))
                    http_options = genai_types.HttpOptions(timeout=timeout_ms)
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

        v5.2: Added caching and fallback rule-based decision
        """
        # v5.2: Check cache first
        cached = self._check_cache(snapshot)
        if cached:
            return cached

        prompt = self.build_prompt(snapshot)

        if not self.api_keys:
            logger.warning("STEP 3: GEMINI_API_KEY not set -> returning HOLD "
                           "(set the key in .env to enable AI decisions).")
            fb = self._fallback_decision(snapshot)
            if fb:
                return fb
            return Decision(action="HOLD", confidence=0.0,
                            rationale="Gemini API key not configured.",
                            timestamp=datetime.now(timezone.utc))
        if genai is None:
            logger.warning("STEP 3: Gemini SDK not installed "
                           "(pip install google-genai) -> HOLD.")
            fb = self._fallback_decision(snapshot)
            if fb:
                return fb
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
                    # v5.8 L3 Confidence Calibration
                    try:
                        if bool(getattr(config, "AI_CONF_CALIBRATION", True)) and action in ("BUY","SELL"):
                            l3 = getattr(snapshot, "level3", None)
                            of = getattr(snapshot, "order_flow", None)
                            if l3 and of:
                                dir_sign = 1 if action=="BUY" else -1
                                net_flow = float(getattr(l3, "aggressive_buy_volume",0) - getattr(l3, "aggressive_sell_volume",0))
                                ofi_l3 = float(getattr(l3, "ofi",0) or 0)
                                whale_thr = float(getattr(config, "L3_WHALE_THRESHOLD",100.0))
                                price = float(getattr(snapshot, "price",0) or 0)
                                whale_confirm = False
                                # Check whale in direction
                                bids = getattr(l3, "order_book", {}).get("bids", []) if hasattr(l3, "order_book") else []
                                asks = getattr(l3, "order_book", {}).get("asks", []) if hasattr(l3, "order_book") else []
                                if dir_sign>0:
                                    # BUY needs bid whale + positive flow
                                    has_whale_bid = any(s>=whale_thr and price>0 and abs(p-price)/price<=0.005 for p,s in bids)
                                    if has_whale_bid and net_flow>50 and ofi_l3>0:
                                        whale_confirm = True
                                else:
                                    has_whale_ask = any(s>=whale_thr and price>0 and abs(p-price)/price<=0.005 for p,s in asks)
                                    if has_whale_ask and net_flow<-50 and ofi_l3<0:
                                        whale_confirm = True
                                boost = float(getattr(config, "AI_L3_CONF_BOOST",15.0))
                                penalty = float(getattr(config, "AI_L3_CONF_PENALTY",20.0))
                                if whale_confirm:
                                    confidence = min(95.0, confidence + boost)
                                    rationale += f" [L3 CONFIRMS whale+flow+OFI +{boost:.0f}%]"
                                else:
                                    # Check conflict: whale opposite or flow opposite
                                    conflict = False
                                    if dir_sign>0 and (net_flow<-50 or ofi_l3<-100):
                                        conflict = True
                                    if dir_sign<0 and (net_flow>50 or ofi_l3>100):
                                        conflict = True
                                    if conflict:
                                        confidence = max(0.0, confidence - penalty)
                                        rationale += f" [L3 CONFLICTS flow/OFI -{penalty:.0f}% -> reduce]"
                                        if confidence < 55:
                                            action = "HOLD"
                                            rationale += " -> HOLD due L3 conflict"
                    except Exception as ce:
                        logger.debug("L3 conf calibration error: %s", ce)

                    logger.info("STEP 3: Gemini -> %s @ %.1f%% (key #%d, %s)",
                                action, confidence, key_index, model)
                    decision = Decision(action=action, confidence=confidence,
                                    rationale=rationale, raw_response=text,
                                    model=model,
                                    timestamp=datetime.now(timezone.utc))
                    self._update_cache(snapshot, decision)
                    return decision
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

        # nothing worked — try fallback before HOLD
        fb = self._fallback_decision(snapshot)
        if fb:
            return fb

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
