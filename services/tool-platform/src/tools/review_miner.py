"""
tools/review_miner.py — Skill: mine user reviews and social sentiment for a topic.

Pure function skill — no side effects, no state, no Kafka.
Called by TrendScoutAgent and CreativeBriefAgent.

Data sources (graceful degradation):
  - Firecrawl (web scraping of review sites) — requires FIRECRAWL_API_KEY
  - market-data-adapter news endpoint — via Kafka integration
  - Fallback: returns empty structure with source="unavailable"
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Review sources config
# ---------------------------------------------------------------------------

_REVIEW_SOURCES: Dict[str, List[str]] = {
    "crypto": [
        "https://coinmarketcap.com/currencies/{symbol}/",
        "https://www.coingecko.com/en/coins/{symbol}",
    ],
    "general": [
        "https://www.reddit.com/search/?q={query}&sort=top&t=week",
    ],
}

_SENTIMENT_POSITIVE = frozenset({
    "bullish", "strong", "growth", "adoption", "partnership", "launch",
    "milestone", "mainnet", "audit", "staking", "yield", "innovative",
    "promising", "undervalued", "accumulate",
})

_SENTIMENT_NEGATIVE = frozenset({
    "bearish", "rug", "scam", "dump", "hack", "exploit", "lawsuit",
    "sec", "fraud", "abandoned", "dead", "whale sell", "exit",
    "overvalued", "bubble", "fud",
})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def mine_reviews(
    topic: str,
    category: str = "general",
    symbol: Optional[str] = None,
    max_sources: int = 2,
) -> Dict[str, Any]:
    """
    Mine reviews and sentiment for a topic.

    Returns:
        {
            "topic": str,
            "positive_signals": List[str],
            "negative_signals": List[str],
            "sentiment_score": float,   # -1.0 to 1.0
            "review_count": int,
            "sources_used": List[str],
            "raw_snippets": List[str],
        }
    """
    empty = _empty_result(topic)

    firecrawl_results = _scrape_with_firecrawl(topic, category, symbol, max_sources)
    if not firecrawl_results:
        _log.debug("review_miner_no_firecrawl topic=%s", topic)
        return empty

    all_text = " ".join(r.get("content", "") or r.get("markdown", "") for r in firecrawl_results)
    if not all_text.strip():
        return empty

    positives, negatives = _extract_signals(all_text)
    score = _sentiment_score(positives, negatives)
    snippets = _extract_snippets(all_text, max_count=5)

    return {
        "topic":            topic,
        "positive_signals": positives,
        "negative_signals": negatives,
        "sentiment_score":  score,
        "review_count":     len(firecrawl_results),
        "sources_used":     [r.get("url", "") for r in firecrawl_results],
        "raw_snippets":     snippets,
    }


def mine_social_mentions(query: str, max_results: int = 10) -> List[Dict[str, Any]]:
    """
    Fetch recent social mentions for a query via market-data-adapter.

    Returns list of {title, url, published_at, sentiment_hint} dicts.
    Never raises.

    TODO: Replace with Kafka integration to market-data-adapter when available.
    """
    try:
        # TODO: Implement Kafka request to market-data-adapter for news
        # For now, return empty list to maintain graceful degradation
        _log.debug("Social mentions via market-data-adapter not yet implemented for query=%s", query)
        return []
    except Exception as exc:
        _log.debug("review_miner_social_failed query=%s error=%s", query, exc)
        return []


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _scrape_with_firecrawl(
    topic: str,
    category: str,
    symbol: Optional[str],
    max_sources: int,
) -> List[Dict[str, Any]]:
    try:
        from tools.firecrawl_client import FirecrawlClient
        client = FirecrawlClient()
        urls = _build_urls(topic, category, symbol, max_sources)
        results = []
        for url in urls:
            data = client.scrape_page(url)
            if data:
                data["url"] = url
                results.append(data)
        return results
    except Exception as exc:
        _log.debug("review_miner_firecrawl_failed topic=%s error=%s", topic, exc)
        return []


def _build_urls(topic: str, category: str, symbol: Optional[str], max_sources: int) -> List[str]:
    templates = _REVIEW_SOURCES.get(category, _REVIEW_SOURCES["general"])[:max_sources]
    slug = (symbol or topic).lower().replace(" ", "-")
    return [
        t.format(symbol=slug, query=topic.replace(" ", "+"))
        for t in templates
    ]


def _extract_signals(text: str) -> tuple[List[str], List[str]]:
    words = set(re.findall(r"\b\w[\w\s]{0,15}\b", text.lower()))
    positives = sorted(words & _SENTIMENT_POSITIVE)
    negatives = sorted(words & _SENTIMENT_NEGATIVE)
    return positives, negatives


def _sentiment_score(positives: List[str], negatives: List[str]) -> float:
    p, n = len(positives), len(negatives)
    total = p + n
    if total == 0:
        return 0.0
    return round((p - n) / total, 3)


def _extract_snippets(text: str, max_count: int) -> List[str]:
    sentences = re.split(r"[.!?\n]", text)
    snippets = [s.strip() for s in sentences if len(s.strip()) > 40]
    return snippets[:max_count]


def _hint_from_title(title: str) -> str:
    lower = title.lower()
    pos = sum(1 for w in _SENTIMENT_POSITIVE if w in lower)
    neg = sum(1 for w in _SENTIMENT_NEGATIVE if w in lower)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def _empty_result(topic: str) -> Dict[str, Any]:
    return {
        "topic":            topic,
        "positive_signals": [],
        "negative_signals": [],
        "sentiment_score":  0.0,
        "review_count":     0,
        "sources_used":     [],
        "raw_snippets":     [],
    }
