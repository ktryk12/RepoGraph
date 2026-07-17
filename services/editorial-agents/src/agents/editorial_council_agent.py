"""
agents/editorial/editorial_council_agent.py — EditorialCouncilAgent.

Message-bus Agent that wraps a 5-voter Council deliberation.

Dispatch:
  TOPIC_SUBMITTED → run council → EDITORIAL_DECISION_READY (or dead-letter on veto)

Council voters:
  journalist        — narrative score + top angles
  format_strategist — format × platform recommendations
  audience_analyst  — audience segments + tone
  monetization_strategist — revenue model + CPM estimate
  legal_review      — risk flags, VETO power (blocks entire pipeline)
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from agents.base import Agent
from shared.babyai_shared.bus.protocol import Message, MessageType
from agents.editorial.models import (
    EditorialDecision, FormatSpec, MonetizationPlan, TopicPackage,
)
from agents.editorial.journalist_agent      import JournalistVoter
from agents.editorial.format_strategist_agent import FormatStrategistVoter
from agents.editorial.audience_agent        import AudienceVoter
from agents.editorial.monetization_agent    import MonetizationVoter
from agents.editorial.legal_review_agent    import LegalReviewVoter

from babyai.council.council  import Council
from babyai.council.proposal import Proposal


# ---------------------------------------------------------------------------
# Consensus engine
# ---------------------------------------------------------------------------

class _EditorialConsensusEngine:
    """
    Weighted average — with one hard rule:
    If the legal_review voter casts 'reject', veto the whole proposal.
    """

    def aggregate(
        self,
        *,
        votes: list[dict[str, Any]],
        proposal: Any,
        domain: str,
        project_id: str,
    ) -> dict[str, Any]:
        # Legal veto — check before anything else
        for vote in votes:
            if (
                str(vote.get("role", "")) == "legal_review"
                and str(vote.get("recommendation", "")) == "reject"
            ):
                return {
                    "recommendation": "reject",
                    "confidence":     1.0,
                    "rationale":      "legal_veto",
                }

        # Weighted vote
        approve_w = 0.0
        reject_w  = 0.0
        for vote in votes:
            rec    = str(vote.get("recommendation", "reject")).lower()
            conf   = float(vote.get("confidence", 0.0))
            weight = float(vote.get("weight", 1.0))
            signal = conf * weight
            if rec == "approve":
                approve_w += signal
            else:
                reject_w  += signal

        recommendation = "approve" if approve_w >= reject_w else "reject"
        total = approve_w + reject_w
        confidence = max(approve_w, reject_w) / total if total > 0 else 0.0
        return {
            "recommendation": recommendation,
            "confidence":     round(confidence, 3),
            "rationale":      "weighted_vote",
        }


# ---------------------------------------------------------------------------
# EditorialCouncilAgent — message-bus layer
# ---------------------------------------------------------------------------

_COUNCIL_DOMAIN  = "editorial"
_COUNCIL_PROJECT = "babyai-editorial"


class EditorialCouncilAgent(Agent):
    """
    Receives TOPIC_SUBMITTED messages, runs 5-voter deliberation,
    emits EDITORIAL_DECISION_READY (or HUMAN_APPROVAL_REQUIRED on block).
    """

    accepts = [MessageType.TOPIC_SUBMITTED]

    def __init__(self) -> None:
        super().__init__(agent_id="editorial-council-001", role="editorial_council")

    # ------------------------------------------------------------------
    # Message handler
    # ------------------------------------------------------------------

    def handle(self, message: Message) -> list[Message]:
        if message.message_type != MessageType.TOPIC_SUBMITTED:
            return []

        payload  = message.payload or {}
        topic    = _topic_from_payload(payload)
        decision = self._run_council(topic)

        if decision.legal_risk == "block":
            return [self._make_veto_message(message, decision)]

        return [self._make_decision_message(message, decision)]

    # ------------------------------------------------------------------
    # Council orchestration
    # ------------------------------------------------------------------

    def _run_council(self, topic: TopicPackage) -> EditorialDecision:
        voters = [
            JournalistVoter(),
            FormatStrategistVoter(),
            AudienceVoter(),
            MonetizationVoter(),
            LegalReviewVoter(),
        ]
        council = Council(
            project_id=_COUNCIL_PROJECT,
            domain=_COUNCIL_DOMAIN,
            agent_roster=voters,
            consensus_engine=_EditorialConsensusEngine(),
        )

        # Embed TopicPackage as evidence[0] so voters can extract it
        topic_dict = {
            "topic_id":   topic.topic_id,
            "title":      topic.title,
            "source":     topic.source,
            "facts":      topic.facts,
            "category":   topic.category,
            "verified":   topic.verified,
        }
        proposal_id = council.submit_proposal(
            claim=f"Produce editorial content for: {topic.title}",
            evidence=[topic_dict],
            confidence=topic.avg_confidence(),
            assumptions=[],
        )
        deliberation = council.run_deliberation(proposal_id)
        decision_obj = council.reach_decision(deliberation)

        return _build_editorial_decision(topic, deliberation, decision_obj)

    # ------------------------------------------------------------------
    # Message builders
    # ------------------------------------------------------------------

    def _make_decision_message(
        self, incoming: Message, decision: EditorialDecision
    ) -> Message:
        return Message(
            message_id   = str(uuid.uuid4()),
            from_agent   = self.agent_id,
            to_agent     = "production-router-001",
            message_type = MessageType.EDITORIAL_DECISION_READY,
            payload      = _decision_to_dict(decision),
            context_id   = incoming.context_id,
            timestamp    = _now_iso(),
        )

    def _make_veto_message(
        self, incoming: Message, decision: EditorialDecision
    ) -> Message:
        return Message(
            message_id   = str(uuid.uuid4()),
            from_agent   = self.agent_id,
            to_agent     = "supervisor",
            message_type = MessageType.HUMAN_APPROVAL_REQUIRED,
            payload      = {
                "reason":          "legal_veto",
                "topic_id":        decision.topic_id,
                "flagged_claims":  decision.flagged_claims,
                "human_approval_required": True,
            },
            context_id   = incoming.context_id,
            timestamp    = _now_iso(),
        )


# ---------------------------------------------------------------------------
# Decision builder — extract domain data from agent_rounds
# ---------------------------------------------------------------------------

def _build_editorial_decision(
    topic: TopicPackage,
    deliberation: dict[str, Any],
    council_decision: Any,
) -> EditorialDecision:
    """
    Walk agent_rounds to extract per-voter results embedded in rationale JSON.
    Falls back gracefully if a voter's rationale is missing or malformed.
    """
    rounds: list[dict[str, Any]] = deliberation.get("agent_rounds", [])

    formats:      list[FormatSpec]    = []
    monetization: MonetizationPlan | None = None
    tone          = "educational"
    chosen_angle  = topic.title
    flagged       = []
    legal_risk    = "low"

    for round_entry in rounds:
        role        = round_entry.get("role", "")
        delib       = round_entry.get("deliberation", {})
        rationale   = delib.get("rationale", "")

        try:
            data = json.loads(rationale) if isinstance(rationale, str) and rationale.startswith(("{", "[")) else {}
        except (json.JSONDecodeError, ValueError):
            data = {}

        if role == "journalist":
            angles = data.get("top_angles", [])
            if angles:
                chosen_angle = angles[0]

        elif role == "format_strategist" and isinstance(data, list):
            for f in data:
                if isinstance(f, dict) and f.get("format_type"):
                    formats.append(FormatSpec(
                        format_type=str(f["format_type"]),
                        platforms=list(f.get("platforms", [])),
                        rationale=str(f.get("rationale", "")),
                    ))

        elif role == "audience_analyst":
            tone = str(data.get("tone", "educational"))

        elif role == "monetization_strategist":
            monetization = MonetizationPlan(
                primary_model=str(data.get("primary_model", "adsense")),
                secondary_models=list(data.get("secondary_models", [])),
                estimated_cpm=float(data.get("estimated_cpm", 2.5)),
                rationale=str(data.get("rationale", "")),
            )

        elif role == "legal_review":
            legal_risk  = str(data.get("risk_level", "low"))
            flagged     = list(data.get("flagged_claims", []))

    # Defaults if voters returned nothing usable
    if not formats:
        formats = [FormatSpec(
            format_type="explainer",
            platforms=["youtube_long"],
            rationale="Default format.",
        )]
    if monetization is None:
        monetization = MonetizationPlan(
            primary_model="adsense",
            secondary_models=[],
            estimated_cpm=2.5,
            rationale="Default monetization.",
        )

    platforms = list({p for f in formats for p in f.platforms})

    return EditorialDecision(
        topic_id       = topic.topic_id,
        chosen_angle   = chosen_angle,
        formats        = formats,
        platforms      = platforms,
        tone           = tone,          # type: ignore[arg-type]
        monetization   = monetization,
        legal_risk     = legal_risk,    # type: ignore[arg-type]
        flagged_claims = flagged,
        human_approval_required = True,
    )


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _topic_from_payload(payload: dict[str, Any]) -> TopicPackage:
    return TopicPackage(
        topic_id  = str(payload.get("topic_id",  "unknown")),
        title     = str(payload.get("title",     "unknown")),
        source    = str(payload.get("source",    "unknown")),
        facts     = list(payload.get("facts",    [])),
        category  = str(payload.get("category",  "general")),
        verified  = bool(payload.get("verified", False)),
    )


def _decision_to_dict(decision: EditorialDecision) -> dict[str, Any]:
    return {
        "topic_id":                decision.topic_id,
        "chosen_angle":            decision.chosen_angle,
        "formats": [
            {
                "format_type": f.format_type,
                "platforms":   f.platforms,
                "rationale":   f.rationale,
            }
            for f in decision.formats
        ],
        "platforms":               decision.platforms,
        "tone":                    decision.tone,
        "monetization": {
            "primary_model":   decision.monetization.primary_model,
            "secondary_models":decision.monetization.secondary_models,
            "estimated_cpm":   decision.monetization.estimated_cpm,
            "rationale":       decision.monetization.rationale,
        },
        "legal_risk":              decision.legal_risk,
        "flagged_claims":          decision.flagged_claims,
        "human_approval_required": True,
        "decided_at":              decision.decided_at,
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
