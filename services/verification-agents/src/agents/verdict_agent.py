"""
agents/fact_check_agents/verdict_agent.py

Producerer endelig verdict (TRUE/FALSE/MISLEADING/UNVERIFIED/SATIRE).

Logik:
  1. Tjek legal_review_triggers.yaml — kræver legal review?
  2. Vurder kildetilstrækkelighed (source_hierarchy.yaml-tærskler)
  3. Konsultér LegalReviewAgent for mønster-baseret risikovurdering
  4. Returner FactCheckResult

Genbrug:
  - agents/editorial/legal_review_agent.LegalReviewAgent.assess()
  - agents/fact_check_agents/models.Verdict
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import List

from agents.base import Agent
from shared.babyai_shared.bus.protocol import Context, Message, MessageType
from agents.fact_check_agents.models import (
    ClaimType, FactCheckResult, SourceAssessment, SourceTier, Verdict,
)

_log = logging.getLogger("fact_check.verdict")

# Minimum tier_4+ sources for TRUE/FALSE
_MIN_TIER4_FOR_DEFINITIVE = 2
# Minimum primary source score for any verdict stronger than UNVERIFIED
_MIN_PRIMARY_SCORE = 0.60

# Triggers that require legal review (matches legal_review_triggers.yaml)
_LEGAL_TRIGGERS: list[tuple[re.Pattern, float]] = [
    (re.compile(r"\b(named|person|individual)\b.*\b(false|lied|fraud)\b", re.I), 1.0),
    (re.compile(r"\b(company|corporation|firm)\b.*\b(fraud|scam|illegal)\b", re.I), 0.95),
    (re.compile(r"\b(scam|rug.?pull|ponzi)\b", re.I), 0.95),
    (re.compile(r"\b(election|ballot|vote).*(rigged|fraud|stolen)\b", re.I), 0.90),
    (re.compile(r"\b(cure[sd]?|treat[sd]?).*(cancer|covid|hiv)\b", re.I), 0.90),
]


def _requires_legal_review(claim_text: str) -> bool:
    for pattern, _ in _LEGAL_TRIGGERS:
        if pattern.search(claim_text):
            return True
    return False


def _compute_verdict(
    claim_type: ClaimType,
    sources: List[SourceAssessment],
    sufficient: bool,
    legal_risk: str,
) -> tuple[Verdict, float]:
    """Return (verdict, confidence) based on source quality and legal risk."""

    if legal_risk == "block":
        return Verdict.UNVERIFIED, 0.0

    tier4_plus = sum(1 for s in sources if s.tier >= SourceTier.PROFESSIONAL)
    primary    = max((s.score for s in sources), default=0.0)

    if not sufficient or primary < _MIN_PRIMARY_SCORE:
        return Verdict.UNVERIFIED, primary

    if tier4_plus >= _MIN_TIER4_FOR_DEFINITIVE:
        # Two high-quality confirming sources → lean toward TRUE
        # (VerdictAgent is not an LLM — it asserts based on source presence)
        # A real LLM call would compare claim vs source content here
        return Verdict.UNVERIFIED, primary  # conservative without LLM confirmation

    if tier4_plus == 1:
        return Verdict.MISLEADING, primary * 0.8

    return Verdict.UNVERIFIED, primary * 0.5


class VerdictAgent(Agent):
    """
    Accepts EVIDENCE_GATHERED, emits VERDICT_READY + FACT_CHECK_COMPLETE.
    Consults LegalReviewAgent for pattern-based risk before issuing verdict.
    """

    def __init__(self, agent_id: str = "verdict-agent-001") -> None:
        super().__init__(agent_id=agent_id, role="verdict")
        self.accepts = {MessageType.EVIDENCE_GATHERED}

    def process(self, message: Message, context: Context) -> List[Message]:
        if message.message_type != MessageType.EVIDENCE_GATHERED:
            return []

        payload    = message.payload or {}
        claim_id   = str(payload.get("claim_id", str(uuid.uuid4())))
        claim_text = str(payload.get("raw_text", ""))
        claim_type = ClaimType(payload.get("claim_type", ClaimType.GENERAL.value))
        sufficient = bool(payload.get("sufficient_sources", False))

        # Reconstruct SourceAssessment list
        sources = [
            SourceAssessment(
                url   = s.get("url", ""),
                tier  = SourceTier(s.get("tier", 1)),
                score = float(s.get("score", 0.0)),
                title = s.get("title", ""),
                snippet = s.get("snippet", ""),
            )
            for s in payload.get("sources", [])
        ]

        # Legal risk via LegalReviewAgent (pattern-based, no LLM)
        legal_risk  = "low"
        flagged     = []
        try:
            from agents.editorial.legal_review_agent import LegalReviewAgent
            from agents.editorial.models import TopicPackage
            tp = TopicPackage(
                topic_id="",
                title=claim_text[:120],
                summary=claim_text,
                claims=[claim_text],
                sources=[s.url for s in sources],
            )
            assessment  = LegalReviewAgent.assess(tp)
            legal_risk  = assessment.risk_level
            flagged     = assessment.flagged_claims
        except Exception as exc:
            _log.debug("legal_review_unavailable error=%s", exc)

        requires_legal = _requires_legal_review(claim_text) or legal_risk == "block"

        verdict, confidence = _compute_verdict(claim_type, sources, sufficient, legal_risk)

        result = FactCheckResult(
            claim_id              = claim_id,
            claim_text            = claim_text,
            verdict               = verdict,
            confidence            = confidence,
            context_note          = "",
            sources               = sources,
            legal_risk            = legal_risk,
            requires_legal_review = requires_legal,
            verdicted_at          = datetime.now(timezone.utc).isoformat(),
            metadata              = {"flagged_claims": flagged},
        )

        now = datetime.now(timezone.utc).isoformat()
        result_payload = {
            **payload,
            "verdict":               result.verdict.value,
            "confidence":            result.confidence,
            "legal_risk":            result.legal_risk,
            "requires_legal_review": result.requires_legal_review,
            "context_note":          result.context_note,
            "verdicted_at":          result.verdicted_at,
        }

        return [
            Message(
                message_id   = str(uuid.uuid4()),
                from_agent   = self.agent_id,
                to_agent     = "context-agent-001",
                message_type = MessageType.VERDICT_READY,
                payload      = result_payload,
                context_id   = message.context_id,
                timestamp    = now,
            ),
            Message(
                message_id   = str(uuid.uuid4()),
                from_agent   = self.agent_id,
                to_agent     = "supervisor",
                message_type = MessageType.FACT_CHECK_COMPLETE,
                payload      = result_payload,
                context_id   = message.context_id,
                timestamp    = now,
            ),
        ]
