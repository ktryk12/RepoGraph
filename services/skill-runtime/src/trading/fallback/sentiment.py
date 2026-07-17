"""
Basic keyword-based sentiment scoring for trading signals.
Pure Python — no external dependencies.
"""
from __future__ import annotations

import re
from typing import Dict, List, Tuple

_BULLISH_TERMS: List[Tuple[str, float]] = [
    ("moon", 0.8),
    ("bullish", 0.7),
    ("buy", 0.6),
    ("breakout", 0.6),
    ("rally", 0.6),
    ("surge", 0.6),
    ("pump", 0.5),
    ("accumulate", 0.5),
    ("undervalued", 0.5),
    ("support", 0.3),
    ("bounce", 0.4),
    ("uptrend", 0.5),
    ("all time high", 0.7),
    ("ath", 0.6),
    ("green", 0.3),
]

_BEARISH_TERMS: List[Tuple[str, float]] = [
    ("crash", 0.8),
    ("bearish", 0.7),
    ("sell", 0.6),
    ("dump", 0.7),
    ("collapse", 0.8),
    ("plunge", 0.6),
    ("correction", 0.4),
    ("resistance", 0.3),
    ("overvalued", 0.5),
    ("short", 0.5),
    ("downtrend", 0.5),
    ("red", 0.3),
    ("fear", 0.4),
    ("panic", 0.6),
    ("scam", 0.7),
]


def score_text(text: str) -> Dict[str, float]:
    """
    Score text for bullish/bearish sentiment.
    Returns {'bullish': 0..1, 'bearish': 0..1, 'net': -1..1}.
    """
    if not text:
        return {"bullish": 0.0, "bearish": 0.0, "net": 0.0}

    lower = text.lower()
    bullish_score = 0.0
    bearish_score = 0.0

    for term, weight in _BULLISH_TERMS:
        count = len(re.findall(r"\b" + re.escape(term) + r"\b", lower))
        bullish_score += count * weight

    for term, weight in _BEARISH_TERMS:
        count = len(re.findall(r"\b" + re.escape(term) + r"\b", lower))
        bearish_score += count * weight

    # Normalize to 0..1 range
    total = bullish_score + bearish_score
    if total == 0:
        return {"bullish": 0.0, "bearish": 0.0, "net": 0.0}

    norm_bullish = bullish_score / total
    norm_bearish = bearish_score / total
    net = norm_bullish - norm_bearish  # -1 (fully bearish) .. +1 (fully bullish)

    return {"bullish": norm_bullish, "bearish": norm_bearish, "net": net}


def analyze_headlines(headlines: List[str]) -> Dict[str, float]:
    """Aggregate sentiment across multiple headlines."""
    if not headlines:
        return {"bullish": 0.0, "bearish": 0.0, "net": 0.0}
    total_net = sum(score_text(h)["net"] for h in headlines) / len(headlines)
    bullish_count = sum(1 for h in headlines if score_text(h)["net"] > 0)
    bearish_count = len(headlines) - bullish_count
    return {
        "bullish": bullish_count / len(headlines),
        "bearish": bearish_count / len(headlines),
        "net": total_net,
        "headline_count": float(len(headlines)),
    }
