"""
agents/fact_check_agents/source_validator_agent.py

Vurderer kildekvalitet baseret på policy/domain/fact_check/source_hierarchy.yaml-tier-modellen.
Kaldes af EvidenceGathererAgent (ikke via message-bus — direkte function call).
"""
from __future__ import annotations

import re
from typing import List

from agents.fact_check_agents.models import SourceAssessment, SourceTier

# Domain → tier mapping (matcher source_hierarchy.yaml)
_TIER_5: list[re.Pattern] = [
    re.compile(r"(europa\.eu|ecb\.europa\.eu|who\.int|un\.org|"
               r"ft\.com|reuters\.com|apnews\.com|bbci\.co\.uk|bbc\.com|"
               r"nytimes\.com|theguardian\.com|lemonde\.fr|"
               r"nature\.com|science\.org|pubmed\.ncbi\.nlm\.nih\.gov)", re.I),
]
_TIER_4: list[re.Pattern] = [
    re.compile(r"(bloomberg\.com|wsj\.com|economist\.com|"
               r"politiken\.dk|berlingske\.dk|information\.dk|"
               r"dr\.dk|tv2\.dk)", re.I),
]
_TIER_3: list[re.Pattern] = [
    re.compile(r"(wikipedia\.org|reddit\.com/r/[a-z]+/comments|"
               r"medium\.com|substack\.com)", re.I),
]
_TIER_2: list[re.Pattern] = [
    re.compile(r"(twitter\.com|x\.com|linkedin\.com|facebook\.com|instagram\.com)", re.I),
]
_BLOCKED: list[re.Pattern] = [
    re.compile(r"(4chan|8kun|zerohedge\.com|infowars\.com|naturalnews\.com)", re.I),
]

_TIER_SCORES = {
    SourceTier.AUTHORITATIVE:   0.95,
    SourceTier.PROFESSIONAL:    0.80,
    SourceTier.JOURNALISTIC:    0.60,
    SourceTier.SOCIAL_OFFICIAL: 0.40,
    SourceTier.SOCIAL_GENERAL:  0.20,
    SourceTier.BLOCKED:         0.00,
}


def _classify_url(url: str) -> SourceTier:
    for p in _BLOCKED:
        if p.search(url):
            return SourceTier.BLOCKED
    for p in _TIER_5:
        if p.search(url):
            return SourceTier.AUTHORITATIVE
    for p in _TIER_4:
        if p.search(url):
            return SourceTier.PROFESSIONAL
    for p in _TIER_3:
        if p.search(url):
            return SourceTier.JOURNALISTIC
    for p in _TIER_2:
        if p.search(url):
            return SourceTier.SOCIAL_OFFICIAL
    return SourceTier.SOCIAL_GENERAL


class SourceValidatorAgent:
    """
    Stateless source quality assessor.
    Scores each URL against the tier model from source_hierarchy.yaml.
    """

    def assess(self, sources: List[dict]) -> List[SourceAssessment]:
        results: List[SourceAssessment] = []
        for src in sources:
            url  = str(src.get("url", ""))
            tier = _classify_url(url)
            score = _TIER_SCORES[tier]
            results.append(SourceAssessment(
                url        = url,
                tier       = tier,
                score      = score,
                title      = str(src.get("title", "")),
                snippet    = str(src.get("snippet", ""))[:500],
                fetched_at = str(src.get("fetched_at", "")),
            ))
        # Sort by score descending
        results.sort(key=lambda s: s.score, reverse=True)
        return results

    def is_sufficient(self, assessments: List[SourceAssessment]) -> bool:
        """True if source set meets minimum policy thresholds."""
        if not assessments:
            return False
        primary_score = max(a.score for a in assessments)
        return primary_score >= 0.60
