"""
agents/fact_check_agents/claim_router_agent.py

Klassificerer en claim til ClaimType og sender CLAIM_ROUTED til EvidenceGathererAgent.
Pattern-baseret — ingen LLM-kald. Hurtig første triage.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import List

from agents.base import Agent
from shared.babyai_shared.bus.protocol import Context, Message, MessageType
from agents.fact_check_agents.models import ClaimType


_PATTERNS: list[tuple[re.Pattern, ClaimType]] = [
    (re.compile(r"\b(\d[\d,.]*\s*%|\d+\s*(million|billion|trillion|kr|usd|eur))\b", re.I), ClaimType.STATISTICAL),
    (re.compile(r'\b(said|says|stated|quote[sd]?|according to)\b', re.I),                  ClaimType.QUOTE),
    (re.compile(r'\b(scam|rug.?pull|ponzi|fraud|fake coin|exit.?scam)\b', re.I),           ClaimType.CRYPTO_SCAM),
    (re.compile(r'\b(election|vote[sd]?|parliament|minister|government|party)\b', re.I),   ClaimType.POLITICAL),
    (re.compile(r'\b(cure[sd]?|treat[sd]?|vaccine|drug|medicine|dose)\b', re.I),           ClaimType.MEDICAL),
    (re.compile(r'\b(product|review|price|buy|sell|brand|company)\b', re.I),               ClaimType.PRODUCT),
]


def classify_claim(text: str) -> ClaimType:
    for pattern, claim_type in _PATTERNS:
        if pattern.search(text):
            return claim_type
    return ClaimType.GENERAL


class ClaimRouterAgent(Agent):
    """
    Accepts CLAIM_DETECTED, classifies claim type, emits CLAIM_ROUTED to evidence-gatherer.
    Stateless — safe to run concurrently.
    """

    def __init__(self, agent_id: str = "claim-router-001") -> None:
        super().__init__(agent_id=agent_id, role="claim_router")
        self.accepts = {MessageType.CLAIM_DETECTED}

    def process(self, message: Message, context: Context) -> List[Message]:
        if message.message_type != MessageType.CLAIM_DETECTED:
            return []

        payload    = message.payload or {}
        claim_id   = str(payload.get("claim_id", str(uuid.uuid4())))
        claim_text = str(payload.get("raw_text", ""))
        claim_type = classify_claim(claim_text)

        return [Message(
            message_id   = str(uuid.uuid4()),
            from_agent   = self.agent_id,
            to_agent     = "evidence-gatherer-001",
            message_type = MessageType.CLAIM_ROUTED,
            payload      = {
                **payload,
                "claim_id":   claim_id,
                "claim_type": claim_type.value,
                "routed_at":  datetime.now(timezone.utc).isoformat(),
            },
            context_id   = message.context_id,
            timestamp    = datetime.now(timezone.utc).isoformat(),
        )]
