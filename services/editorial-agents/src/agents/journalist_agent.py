"""
agents/editorial/journalist_agent.py — Narrative analyst for editorial council.

JournalistVoter: Council participant — scores narrative strength, proposes angles.
JournalistAgent: Standalone callable for direct use outside council meetings.
"""
from __future__ import annotations

import json
import re
from typing import Any

from babyai.council.base import Agent as CouncilAgent
from babyai.council.proposal import Proposal
from agents.editorial.models import NarrativeProposal, TopicPackage


class JournalistVoter(CouncilAgent):
    """
    Council voter: assesses narrative strength of a TopicPackage.

    Embeds a NarrativeProposal as JSON in the deliberation rationale so
    EditorialCouncilAgent can extract it from agent_rounds.
    """

    def __init__(self) -> None:
        super().__init__(
            role="journalist",
            profile_config={"risk_thresholds": {"narrative_score": 0.5}},
            memory_ref=None,
        )

    def deliberate(self, proposal: Proposal | dict[str, Any]) -> dict[str, Any]:
        topic = _extract_topic(proposal)
        narrative = JournalistAgent.analyse_topic(topic)

        support_score = min(1.0, narrative.narrative_score)
        data = {
            "hook":            narrative.hook,
            "conflict":        narrative.conflict,
            "protagonists":    narrative.protagonists,
            "antagonists":     narrative.antagonists,
            "resolution":      narrative.resolution,
            "narrative_score": narrative.narrative_score,
            "top_angles":      narrative.top_angles,
        }
        return {
            "role":            self.role,
            "support_score":   support_score,
            "assumptions_count": 0,
            "focus":           "narrative_and_hook",
            "risks":           [] if support_score >= 0.5 else ["weak_narrative"],
            "constraints":     [],
            "rationale":       json.dumps(data, ensure_ascii=True),
        }


class JournalistAgent:
    """Standalone narrative analyser — no Council dependency."""

    @staticmethod
    def analyse_topic(topic: TopicPackage) -> NarrativeProposal:
        claims   = topic.all_claims()
        title    = topic.title
        category = topic.category

        # Derive hook from most specific claim (longest, most signals)
        scored_claims = sorted(claims, key=_claim_specificity, reverse=True)
        hook = scored_claims[0] if scored_claims else title

        # Conflict: entity vs. public/regulator
        conflict = _infer_conflict(title, category, claims)

        # Protagonists / antagonists
        protagonists, antagonists = _infer_actors(title, category)

        # Resolution: look for fine/verdict language
        resolution = _infer_resolution(claims)

        # Narrative score: based on how many signals are present
        score = 0.50
        if hook and len(hook) > 40:
            score += 0.10
        if conflict:
            score += 0.10
        if protagonists:
            score += 0.10
        if antagonists:
            score += 0.10
        if resolution:
            score += 0.10
        score = min(1.0, score + (0.05 * min(3, len(claims))))

        # Top angles (deterministic — no LLM needed)
        angles = [
            f"How {title} deceived the public for years",
            f"The regulators who finally caught {title.split()[0]}",
            f"What changed after {title}: laws, fines, and accountability",
        ]

        return NarrativeProposal(
            hook=hook,
            conflict=conflict,
            protagonists=protagonists,
            antagonists=antagonists,
            resolution=resolution,
            narrative_score=round(score, 3),
            top_angles=angles[:3],
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_topic(proposal: Proposal | dict[str, Any]) -> TopicPackage:
    """Pull TopicPackage from a Council Proposal's evidence block."""
    from agents.editorial.models import TopicPackage
    if isinstance(proposal, Proposal):
        evidence = list(proposal.evidence or [])
    else:
        evidence = list(proposal.get("evidence", []) or [])

    raw = evidence[0] if evidence else {}
    if isinstance(raw, dict) and "topic_id" in raw:
        return TopicPackage(
            topic_id=str(raw.get("topic_id", "")),
            title=str(raw.get("title", "")),
            source=str(raw.get("source", "unknown")),
            facts=list(raw.get("facts", [])),
            category=str(raw.get("category", "general")),
            verified=bool(raw.get("verified", False)),
        )
    return TopicPackage(
        topic_id="unknown", title="unknown", source="unknown",
        facts=[], category="general", verified=False,
    )


def _claim_specificity(claim: str) -> float:
    score = len(claim) / 200.0
    text = claim.lower()
    if re.search(r"\$[\d,.]+", text):     score += 0.3
    if re.search(r"\b(19|20)\d{2}\b", text): score += 0.2
    if re.search(r"(fined|convicted|pled|pleaded|resigned)", text): score += 0.3
    return score


def _infer_conflict(title: str, category: str, claims: list[str]) -> str:
    t = title.lower()
    if category == "corporate":
        return f"{title.split()[0]} vs. regulators and the public"
    if category == "political":
        return f"Government accountability in {title}"
    if category == "science":
        return f"Scientific integrity vs. institutional cover-up in {title}"
    return f"Power vs. accountability: {title}"


def _infer_actors(title: str, category: str) -> tuple[list[str], list[str]]:
    words = title.split()
    entity = words[0] if words else "The company"
    if category == "corporate":
        return (["Whistleblowers", "Regulators", "Affected public"],
                [entity, "Corporate leadership"])
    if category == "political":
        return (["Investigative journalists", "Civil society"],
                ["Government officials", entity])
    return (["Public", "Journalists"], [entity])


def _infer_resolution(claims: list[str]) -> str:
    for claim in claims:
        text = claim.lower()
        if re.search(r"(fined|convicted|pled|pleaded|settled|resigned|dissolved|shut down)", text):
            return claim[:160]
    return "Regulatory action taken; ongoing accountability measures."
