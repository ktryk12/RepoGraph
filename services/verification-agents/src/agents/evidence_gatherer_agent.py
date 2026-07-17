"""
agents/fact_check_agents/evidence_gatherer_agent.py

Orkestrerer firecrawl + market-data-adapter for at indsamle evidens baseret på claim-type.
Accepts CLAIM_ROUTED, emits EVIDENCE_GATHERED.

Genbrug:
  - tools/firecrawl_client.FirecrawlClient.search_and_scrape
  - market-data-adapter via Kafka (for STATISTICAL + CRYPTO_SCAM claims)
  - agents/fact_check_agents/source_validator_agent.SourceValidatorAgent
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agents.base import Agent
from shared.babyai_shared.bus.protocol import Context, Message, MessageType
from agents.fact_check_agents.models import ClaimType, FactCheckContext, SourceTier
from agents.fact_check_agents.source_validator_agent import SourceValidatorAgent

_log = logging.getLogger("fact_check.evidence_gatherer")

_MAX_SOURCES = 8
_SEARCH_TIMEOUT = 15


class EvidenceGathererAgent(Agent):
    """
    Gathers evidence for a claim. Orchestrates FirecrawlClient and optionally
    market-data-adapter for financial/crypto claims. Falls back gracefully if services
    are unavailable (stub mode).
    """

    def __init__(self, agent_id: str = "evidence-gatherer-001") -> None:
        super().__init__(agent_id=agent_id, role="evidence_gatherer")
        self.accepts = {MessageType.CLAIM_ROUTED}
        self._source_validator = SourceValidatorAgent()
        self._firecrawl: Optional[Any] = self._load_firecrawl()
        self._market_data_enabled = False  # TODO: Enable when Kafka integration complete

    def process(self, message: Message, context: Context) -> List[Message]:
        if message.message_type != MessageType.CLAIM_ROUTED:
            return []

        payload    = message.payload or {}
        claim_id   = str(payload.get("claim_id", str(uuid.uuid4())))
        claim_text = str(payload.get("raw_text", ""))
        claim_type = ClaimType(payload.get("claim_type", ClaimType.GENERAL.value))

        raw_sources = self._gather(claim_text, claim_type)
        assessed    = self._source_validator.assess(raw_sources)

        ctx = FactCheckContext(
            claim_id   = claim_id,
            claim_text = claim_text,
            claim_type = claim_type,
            sources    = assessed,
        )

        return [Message(
            message_id   = str(uuid.uuid4()),
            from_agent   = self.agent_id,
            to_agent     = "verdict-agent-001",
            message_type = MessageType.EVIDENCE_GATHERED,
            payload      = {
                **payload,
                "sources": [
                    {"url": s.url, "tier": s.tier.value, "score": s.score,
                     "title": s.title, "snippet": s.snippet}
                    for s in assessed
                ],
                "primary_source_score": ctx.primary_source_score(),
                "sufficient_sources":   self._source_validator.is_sufficient(assessed),
                "gathered_at":          datetime.now(timezone.utc).isoformat(),
            },
            context_id   = message.context_id,
            timestamp    = datetime.now(timezone.utc).isoformat(),
        )]

    # ── Evidence gathering ────────────────────────────────────────────────────

    def _gather(self, claim_text: str, claim_type: ClaimType) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []

        # Primary: web search via Firecrawl
        if self._firecrawl:
            try:
                scraped = self._firecrawl.search_and_scrape(
                    query=claim_text,
                    max_results=_MAX_SOURCES,
                )
                for item in (scraped or []):
                    results.append({
                        "url":        item.get("url", ""),
                        "title":      item.get("title", ""),
                        "snippet":    item.get("content", "")[:500],
                        "fetched_at": datetime.now(timezone.utc).isoformat(),
                    })
            except Exception as exc:
                _log.warning("firecrawl_search_failed claim_type=%s error=%s", claim_type, exc)

        # Supplemental: market-data-adapter for financial/crypto claims
        if self._market_data_enabled and claim_type in (ClaimType.STATISTICAL, ClaimType.CRYPTO_SCAM):
            try:
                # TODO: Replace with Kafka request to market-data-adapter when integration complete
                # news = await self._request_market_data(claim_text, "news", {"limit": 4})
                # for item in (news or []):
                #     results.append({
                #         "url":        item.get("url", ""),
                #         "title":      item.get("title", ""),
                #         "snippet":    item.get("body", "")[:500],
                #         "fetched_at": datetime.now(timezone.utc).isoformat(),
                #     })
                pass
            except Exception as exc:
                _log.warning("market_data_news_failed claim_type=%s error=%s", claim_type, exc)

        if not results:
            _log.info("evidence_gatherer_no_sources claim_text=%s", claim_text[:80])

        return results[:_MAX_SOURCES]

    # ── Client loaders ────────────────────────────────────────────────────────

    @staticmethod
    def _load_firecrawl() -> Optional[Any]:
        try:
            from tools.firecrawl_client import FirecrawlClient
            return FirecrawlClient()
        except Exception:
            return None

    @staticmethod
    async def _request_market_data(self, query: str, data_type: str, parameters: Optional[Dict] = None) -> Optional[Dict]:
        """
        Request market data via Kafka adapter (replaces OpenBB calls).

        TODO: Implement Kafka request/response pattern for news and market data.
        For now returns None to maintain graceful degradation.
        """
        if not self._market_data_enabled:
            _log.debug("Market data integration disabled - skipping %s for %s", data_type, query)
            return None

        # TODO: Implement Kafka market data request pattern
        return None
