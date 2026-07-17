"""
agents/editorial/legal_review_agent.py — Legal risk gating voter.

LegalReviewVoter: Council participant with VETO power.
  recommendation="reject" → _EditorialConsensusEngine short-circuits entire pipeline.

LegalReviewAgent: Standalone callable.
"""
from __future__ import annotations

import json
import re
from typing import Any

from babyai.council.base import Agent as CouncilAgent
from babyai.council.proposal import Proposal
from agents.editorial.models import LegalAssessment, TopicPackage
from agents.editorial.journalist_agent import _extract_topic


# ---------------------------------------------------------------------------
# Risk patterns
# ---------------------------------------------------------------------------

# Claims containing these phrases require human legal review
_FLAG_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(allegedly|alleged|unconfirmed|reportedly|rumoured)\b", re.I),
     "unverified_claim"),
    (re.compile(r"\b(defamat|libel|slander)\b", re.I),
     "defamation_risk"),
    (re.compile(r"\b(personally identif|pii|gdpr|private data)\b", re.I),
     "privacy_concern"),
    (re.compile(r"\b(classified|state secret|national security)\b", re.I),
     "national_security"),
    (re.compile(r"\b(ongoing investigation|under investigation|not yet convicted)\b", re.I),
     "prejudices_investigation"),
    (re.compile(r"\b(minor|child|underage)\b", re.I),
     "involves_minor"),
]

# These always veto — regardless of claim count
_HARD_VETO_REASONS = {"national_security", "involves_minor"}

# Soft block threshold: flag count that triggers risk_level="block"
_SOFT_BLOCK_THRESHOLD = 3


class LegalReviewVoter(CouncilAgent):
    """
    Council voter with VETO power.

    If risk_level == "block", vote recommendation is "reject" which causes
    _EditorialConsensusEngine to immediately veto the entire proposal.
    """

    def __init__(self) -> None:
        super().__init__(
            role="legal_review",
            profile_config={"veto_enabled": True},
            memory_ref=None,
        )

    def deliberate(self, proposal: Proposal | dict[str, Any]) -> dict[str, Any]:
        topic      = _extract_topic(proposal)
        assessment = LegalReviewAgent.assess(topic)

        if assessment.veto:
            return {
                "role":              self.role,
                "support_score":     0.0,
                "assumptions_count": len(assessment.flagged_claims),
                "focus":             "legal_veto",
                "risks":             assessment.reasons,
                "constraints":       assessment.flagged_claims,
                "rationale":         json.dumps({
                    "risk_level":     assessment.risk_level,
                    "flagged_claims": assessment.flagged_claims,
                    "reasons":        assessment.reasons,
                    "veto":           True,
                }, ensure_ascii=True),
                # Non-standard key — _EditorialConsensusEngine reads this
                "recommendation": "reject",
            }

        support_score = {"low": 1.0, "medium": 0.6}.get(assessment.risk_level, 0.0)
        return {
            "role":              self.role,
            "support_score":     support_score,
            "assumptions_count": len(assessment.flagged_claims),
            "focus":             "legal_clearance",
            "risks":             assessment.reasons,
            "constraints":       assessment.flagged_claims,
            "rationale":         json.dumps({
                "risk_level":     assessment.risk_level,
                "flagged_claims": assessment.flagged_claims,
                "reasons":        assessment.reasons,
                "veto":           False,
            }, ensure_ascii=True),
        }


class LegalReviewAgent:
    """Standalone legal risk assessor — pattern-based, no LLM."""

    @staticmethod
    def assess(topic: TopicPackage) -> LegalAssessment:
        claims  = topic.all_claims()
        flagged: list[str] = []
        reasons: list[str] = []
        hard_veto = False

        for claim in claims:
            for pattern, reason_code in _FLAG_PATTERNS:
                if pattern.search(claim):
                    flagged.append(claim[:120])
                    if reason_code not in reasons:
                        reasons.append(reason_code)
                    if reason_code in _HARD_VETO_REASONS:
                        hard_veto = True
                    break  # one flag per claim is enough

        veto = hard_veto or len(flagged) >= _SOFT_BLOCK_THRESHOLD

        if veto:
            risk_level = "block"
        elif flagged:
            risk_level = "medium"
        else:
            risk_level = "low"

        return LegalAssessment(
            risk_level=risk_level,
            flagged_claims=flagged,
            reasons=reasons,
            veto=veto,
        )
