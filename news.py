"""
news.py
=======
FREE financial-news fetcher for the AI brain (no API key, no card).

Gold moves mostly on fundamentals: wars, central-bank decisions, inflation
data, geopolitics. This module pulls the latest headlines from Google News
RSS (free, no signup) and hands them to Step 2, so the Gemini decision in
Step 3 can weigh REAL news together with the technicals.

Design goals
------------
- FREE and keyless: Google News RSS needs no account and no card.
- Polite: results are cached (in memory + on disk) and re-fetched at most
  every NEWS_CACHE_MINUTES (default 15 minutes).
- Safe: any failure -> no headlines; the bot keeps working without them.
  Nothing here can crash the pipeline.

Wire-up: `main.py` calls `enrich_market_news(data)` after Step 1, which adds
headlines to `data["news"]["headlines"]`. Step 2 already copies that list into
the snapshot, and Step 3 already prints the whole snapshot into the Gemini
prompt — so headlines flow to the AI automatically.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

try:
    import xml.etree.ElementTree as ET
except ImportError:  # pragma: no cover
    ET = None

import config

logger = logging.getLogger(__name__)

# Google News RSS queries — the topics that actually move GOLD.
NEWS_QUERIES = [
    "gold price",
    "Federal Reserve interest rates",
    "geopolitical conflict war economy",
    "US dollar index",
    "oil prices",
]

_MEMO: Dict[str, Any] = {}          # in-memory cache {fetched_at, headlines}
_MEMO_LOADED = False


# --------------------------------------------------------------------------- #
# Caching (memory + disk) — never hammer the free feed
# --------------------------------------------------------------------------- #
def _cache_path() -> Path:
    return Path(__file__).resolve().parent / "data" / "news_cache.json"


def _load_disk_cache() -> Optional[Dict[str, Any]]:
    global _MEMO_LOADED
    if _MEMO_LOADED:
        return _MEMO.get("disk") or {}
    _MEMO_LOADED = True
    try:
        path = _cache_path()
        if path.exists():
            state = json.loads(path.read_text(encoding="utf-8"))
            _MEMO["disk"] = state
            return state
    except (json.JSONDecodeError, OSError):
        pass
    _MEMO["disk"] = {}
    return {}


def _save_disk_cache(state: Dict[str, Any]) -> None:
    try:
        path = _cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")
        _MEMO["disk"] = state
    except OSError:
        pass


def _fresh_cache() -> Optional[List[str]]:
    """Return cached headlines if they are still fresh, else None."""
    if not config.NEWS_ENABLED:
        return []
    # in-memory cache first
    mem = _MEMO.get("headlines")
    mem_at = _MEMO.get("fetched_at")
    if mem is not None and mem_at is not None:
        if time.time() - mem_at < config.NEWS_CACHE_MINUTES * 60:
            return list(mem)
    # then disk cache
    disk = _load_disk_cache()
    headlines = disk.get("headlines")
    fetched_at = disk.get("fetched_at")
    if headlines and fetched_at:
        try:
            if time.time() - float(fetched_at) < config.NEWS_CACHE_MINUTES * 60:
                _MEMO["headlines"] = list(headlines)
                _MEMO["fetched_at"] = float(fetched_at)
                return list(headlines)
        except (TypeError, ValueError):
            pass
    return None


# --------------------------------------------------------------------------- #
# Fetching
# --------------------------------------------------------------------------- #
def _fetch_rss(url: str, timeout: int) -> List[Dict[str, str]]:
    """Fetch one RSS feed and return [{title, published, source}, ...]."""
    if requests is None:
        return []
    resp = requests.get(url, timeout=timeout,
                        headers={"User-Agent": "Mozilla/5.0 (compatible; "
                                "gold-bot/2.0)"})
    resp.raise_for_status()
    text = resp.content if isinstance(resp.content, bytes) else resp.text
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")

    items: List[Dict[str, str]] = []
    if ET is not None:
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            root = None
        if root is not None:
            for it in root.findall(".//item"):
                title = (it.findtext("title") or "").strip()
                if not title:
                    continue
                source = (it.findtext("source") or "").strip()
                pub = (it.findtext("pubDate") or "").strip()
                # Google News titles end with " - Source"; keep it, the AI
                # reads better with the source attached.
                items.append({"title": title, "published": pub, "source": source})
            if items:
                return items

    # fallback: plain regex extraction (some feeds aren't strict XML)
    titles = re.findall(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", text,
                        re.DOTALL)
    for t in titles[1:]:                       # skip the channel <title>
        t = t.strip()
        if t:
            items.append({"title": t, "published": "", "source": ""})
    return items


def fetch_headlines() -> List[str]:
    """Fetch + dedupe the latest headlines across all queries (cached)."""
    if not config.NEWS_ENABLED:
        return []
    cached = _fresh_cache()
    if cached is not None:
        return cached

    if requests is None:
        logger.warning("news: 'requests' not installed -> no headlines")
        return []

    seen: Dict[str, str] = {}
    for query in NEWS_QUERIES:
        url = ("https://news.google.com/rss/search?q="
               + requests.utils.quote(query) + "&hl=en-US&gl=US&ceid=US:en")
        try:
            for item in _fetch_rss(url, timeout=config.NEWS_TIMEOUT):
                title = item["title"].strip()
                key = re.sub(r"[^a-z0-9 ]+", "", title.lower()).strip()
                key = re.sub(r"\s+", " ", key)
                if key and key not in seen:
                    seen[key] = title
        except Exception as exc:
            logger.info("news: query '%s' failed (%s); continuing.", query, exc)

    headlines = list(seen.values())[: config.NEWS_MAX_HEADLINES]

    # cache whatever we got (even empty — avoid refetch spam)
    now = time.time()
    _MEMO["headlines"] = headlines
    _MEMO["fetched_at"] = now
    _save_disk_cache({"fetched_at": now, "headlines": headlines,
                      "fetched_at_iso": datetime.now(timezone.utc).isoformat()})

    if headlines:
        logger.info("news: %d fresh headlines fetched.", len(headlines))
    else:
        logger.info("news: no headlines fetched (network issue?). "
                    "Bot continues without news.")
    return headlines


def enrich_market_news(data: Dict[str, Any]) -> Dict[str, Any]:
    """Inject fresh headlines into data['news'] for Step 2 / Step 3.

    Never touches existing fields (events, fetch_calendar, ...). Returns the
    same `data` dict for convenience.
    """
    if not config.NEWS_ENABLED:
        return data
    try:
        headlines = fetch_headlines()
        news = data.setdefault("news", {})
        # only fill when the provider didn't already supply headlines
        if not news.get("headlines"):
            news["headlines"] = headlines
    except Exception as exc:  # never let news break the pipeline
        logger.warning("news: enrich failed (%s); continuing.", exc)
    return data


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print("Fetching gold-relevant headlines (free, cached) ...\n")
    heads = fetch_headlines()
    for i, h in enumerate(heads, 1):
        print(f"{i:2d}. {h}")
    print(f"\nTotal: {len(heads)} headlines")
