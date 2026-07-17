"""
claim_detector/ranker.py — composite score: virality × controversy × factcheckability.

Factcheckability: how likely is this a verifiable factual claim (vs. opinion/joke/satire).
"""
from __future__ import annotations

import re
from typing import List

from claim_detector.models import ClaimCandidate, DetectedClaim

import uuid
from datetime import datetime, timezone

# High-signal factcheckability patterns
_FACTCHECKABLE_PATTERNS = [
    re.compile(r"\b(\d[\d,.]*\s*%|\d+\s*(million|billion|people|cases|deaths))\b", re.I),
    re.compile(r"\b(study|research|report|data|statistics|official|confirmed|proven)\b", re.I),
    re.compile(r"\b(government|ministry|scientist|expert|spokesperson|official).{0,30}(said|says|stated|confirmed)\b", re.I),
    re.compile(r"\b(vaccine|treatment|cure|drug)\b.{0,50}\b(causes|prevents|kills|works)\b", re.I),
    re.compile(r"\b(election|vote|ballot).{0,30}(fraud|rigged|stolen|fake)\b", re.I),
]

# Low-signal patterns (opinions, jokes, spam)
_LOW_SIGNAL_PATTERNS = [
    re.compile(r"\b(lol|lmao|haha|omg|wtf)\b", re.I),
    re.compile(r"\b(follow me|subscribe|link in bio|swipe up)\b", re.I),
    re.compile(r"^\s*#\w+", re.M),  # pure hashtag posts
]

_WEIGHTS = {"virality": 0.35, "controversy": 0.30, "factcheckability": 0.35}


def _factcheckability(text: str) -> float:
    if not text:
        return 0.0
    score = 0.0
    for p in _FACTCHECKABLE_PATTERNS:
        if p.search(text):
            score += 0.2
    for p in _LOW_SIGNAL_PATTERNS:
        if p.search(text):
            score -= 0.3
    return max(0.0, min(1.0, score))


def rank(candidates: List[ClaimCandidate]) -> List[DetectedClaim]:
    results = []
    now = datetime.now(timezone.utc).isoformat()
    for c in candidates:
        fc = _factcheckability(c.raw_text)
        composite = (
            _WEIGHTS["virality"]        * c.virality_score
            + _WEIGHTS["controversy"]   * c.controversy_score
            + _WEIGHTS["factcheckability"] * fc
        )
        results.append(DetectedClaim(
            claim_id              = str(uuid.uuid4()),
            raw_text              = c.raw_text,
            source_url            = c.source_url,
            platform              = c.platform,
            detected_at           = now,
            virality_score        = c.virality_score,
            controversy_score     = c.controversy_score,
            factcheckability_score= fc,
            composite_score       = composite,
            metadata              = c.metadata,
        ))
    results.sort(key=lambda d: d.composite_score, reverse=True)
    return results
