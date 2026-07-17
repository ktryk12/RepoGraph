"""agents/fact_check_agents/models.py — shared domain types for fact-check pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class ClaimType(str, Enum):
    STATISTICAL   = "statistical"
    QUOTE         = "quote"
    PRODUCT       = "product"
    CRYPTO_SCAM   = "crypto_scam"
    POLITICAL     = "political"
    MEDICAL       = "medical"
    GENERAL       = "general"


class Verdict(str, Enum):
    TRUE        = "TRUE"
    FALSE       = "FALSE"
    MISLEADING  = "MISLEADING"
    UNVERIFIED  = "UNVERIFIED"
    SATIRE      = "SATIRE"


class SourceTier(int, Enum):
    BLOCKED        = 0
    SOCIAL_GENERAL = 1
    SOCIAL_OFFICIAL = 2
    JOURNALISTIC   = 3
    PROFESSIONAL   = 4
    AUTHORITATIVE  = 5


@dataclass
class ClaimCandidate:
    claim_id: str
    raw_text: str
    source_url: str
    platform: str
    detected_at: str
    virality_score: float = 0.0
    controversy_score: float = 0.0


@dataclass
class SourceAssessment:
    url: str
    tier: SourceTier
    score: float
    title: str = ""
    snippet: str = ""
    fetched_at: str = ""


@dataclass
class FactCheckContext:
    claim_id: str
    claim_text: str
    claim_type: ClaimType
    sources: List[SourceAssessment] = field(default_factory=list)
    context_note: str = ""
    legal_risk: str = "low"
    flagged_claims: List[str] = field(default_factory=list)

    def primary_source_score(self) -> float:
        if not self.sources:
            return 0.0
        return max(s.score for s in self.sources)

    def tier_count(self, min_tier: SourceTier) -> int:
        return sum(1 for s in self.sources if s.tier >= min_tier)


@dataclass
class FactCheckResult:
    claim_id: str
    claim_text: str
    verdict: Verdict
    confidence: float
    context_note: str
    sources: List[SourceAssessment]
    legal_risk: str
    requires_legal_review: bool
    verdicted_at: str
    metadata: Dict[str, Any] = field(default_factory=dict)
