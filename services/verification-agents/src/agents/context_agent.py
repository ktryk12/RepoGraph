"""
agents/fact_check_agents/context_agent.py

Tilføjer kontekst og nuance til verdikten inden publicering.
Accepts VERDICT_READY, emits FACT_CHECK_COMPLETE med context_note.

"Påstanden er teknisk sand men vildledende fordi..." — denne agent skriver den note.
Pattern-baseret for hastigheds skyld; udskiftes med LLM-kald i Sprint 3.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import List

from agents.base import Agent
from shared.babyai_shared.bus.protocol import Context, Message, MessageType
from agents.fact_check_agents.models import ClaimType, Verdict


_CONTEXT_TEMPLATES = {
    (Verdict.MISLEADING, ClaimType.STATISTICAL): (
        "Påstanden bruger korrekte tal, men udelader vigtig kontekst. "
        "Sammenlign med baserate og tidsperiode for det fulde billede."
    ),
    (Verdict.MISLEADING, ClaimType.QUOTE): (
        "Citatet er korrekt gengivet, men taget ud af sammenhæng. "
        "Læs hele udtalelsen for den fulde betydning."
    ),
    (Verdict.MISLEADING, ClaimType.POLITICAL): (
        "Påstanden indeholder faktuelle elementer, men vinklingsmæssigt selektiv. "
        "Se primærkilder for upartisk fremstilling."
    ),
    (Verdict.UNVERIFIED, ClaimType.CRYPTO_SCAM): (
        "Vi kunne ikke verificere påstanden med tilstrækkelige primærkilder. "
        "Udvis forsigtighed ved investeringsbeslutninger baseret på uverificerede claims."
    ),
    (Verdict.UNVERIFIED, ClaimType.MEDICAL): (
        "Medicinsk påstand ikke tilstrækkeligt understøttet af peer-reviewede kilder. "
        "Konsultér sundhedsmyndigheder eller læge."
    ),
}

_DEFAULT_CONTEXT = {
    Verdict.TRUE:       "Påstanden er understøttet af troværdige primærkilder.",
    Verdict.FALSE:      "Påstanden modsvares direkte af dokumenterede primærkilder.",
    Verdict.MISLEADING: "Påstanden er delvist korrekt, men giver et misvisende helhedsindtryk.",
    Verdict.UNVERIFIED: "Vi kunne ikke finde tilstrækkelig evidens til at bekræfte eller afkræfte påstanden.",
    Verdict.SATIRE:     "Indholdet er satirisk og ikke ment som faktuel information.",
}


class ContextAgent(Agent):
    """
    Enriches a VERDICT_READY payload with a context_note before re-emitting
    FACT_CHECK_COMPLETE to the production router.
    """

    def __init__(self, agent_id: str = "context-agent-001") -> None:
        super().__init__(agent_id=agent_id, role="context")
        self.accepts = {MessageType.VERDICT_READY}

    def process(self, message: Message, context: Context) -> List[Message]:
        if message.message_type != MessageType.VERDICT_READY:
            return []

        payload    = message.payload or {}
        verdict    = Verdict(payload.get("verdict", Verdict.UNVERIFIED.value))
        claim_type = ClaimType(payload.get("claim_type", ClaimType.GENERAL.value))

        context_note = (
            _CONTEXT_TEMPLATES.get((verdict, claim_type))
            or _DEFAULT_CONTEXT.get(verdict, "")
        )

        return [Message(
            message_id   = str(uuid.uuid4()),
            from_agent   = self.agent_id,
            to_agent     = "production-router-001",
            message_type = MessageType.FACT_CHECK_COMPLETE,
            payload      = {
                **payload,
                "context_note": context_note,
                "enriched_at":  datetime.now(timezone.utc).isoformat(),
            },
            context_id   = message.context_id,
            timestamp    = datetime.now(timezone.utc).isoformat(),
        )]
