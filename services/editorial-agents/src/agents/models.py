"""
agents/editorial/models.py — Shared data models for the editorial pipeline.

All dataclasses are frozen (immutable) to prevent accidental mutation
across the Council deliberation boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TopicPackage:
    """
    Source-agnostic topic envelope.

    Accepted from: WatchdogAgent, manual submission, TrendScoutAgent, etc.
    """
    topic_id:     str
    title:        str
    source:       str          # "watchdog" | "manual" | "trend_search" | …
    facts:        List[Dict[str, Any]]  # [{"claim": str, "sources": list, "confidence": float}]
    category:     str          # "corporate" | "political" | "science" | "culture" | …
    verified:     bool
    submitted_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def avg_confidence(self) -> float:
        if not self.facts:
            return 0.0
        return sum(float(f.get("confidence", 0.0)) for f in self.facts) / len(self.facts)

    def all_claims(self) -> List[str]:
        return [str(f.get("claim", "")) for f in self.facts if f.get("claim")]

    def all_sources(self) -> List[str]:
        out: List[str] = []
        for f in self.facts:
            for s in f.get("sources", []):
                if s and s not in out:
                    out.append(str(s))
        return out


# ---------------------------------------------------------------------------
# Intermediate
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NarrativeProposal:
    hook:              str
    conflict:          str
    protagonists:      List[str]
    antagonists:       List[str]
    resolution:        str
    narrative_score:   float       # 0.0–1.0
    top_angles:        List[str]   # max 3


@dataclass(frozen=True)
class FormatSpec:
    format_type:  str    # "explainer" | "documentary" | "animation" | …
    platforms:    List[str]
    rationale:    str


@dataclass(frozen=True)
class MonetizationPlan:
    primary_model:   str           # "adsense" | "sponsorship" | "subscription" | …
    secondary_models: List[str]
    estimated_cpm:   float         # EUR per 1000 views (rough)
    rationale:       str


@dataclass(frozen=True)
class LegalAssessment:
    risk_level:    Literal["low", "medium", "block"]
    flagged_claims: List[str]      # claims that need review
    reasons:       List[str]       # explanation for each flag
    veto:          bool            # True → stop entire pipeline


# ---------------------------------------------------------------------------
# Decision
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EditorialDecision:
    topic_id:                str
    chosen_angle:            str
    formats:                 List[FormatSpec]
    platforms:               List[str]
    tone:                    Literal["serious", "satirical", "educational", "entertainment"]
    monetization:            MonetizationPlan
    legal_risk:              Literal["low", "medium", "block"]
    flagged_claims:          List[str]
    human_approval_required: bool = True   # always True — never override
    decided_at:              str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

@dataclass
class ProductionPackage:
    """
    Mutable output from a production agent (content is assembled incrementally).
    """
    topic_id:                   str
    format_type:                str
    platform:                   str
    content:                    Dict[str, Any]
    assets_required:            List[str]    # what ComfyUI/external tools must generate
    estimated_production_time:  int          # seconds
    ready_for_approval:         bool = False
    human_approval_required:    bool = True  # always True
