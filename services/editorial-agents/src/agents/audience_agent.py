"""
agents/editorial/audience_agent.py — Audience targeting voter.

AudienceVoter: Council participant — profiles target audience segments.
AudienceAgent: Standalone callable.
"""
from __future__ import annotations

import json
from typing import Any

from babyai.council.base import Agent as CouncilAgent
from babyai.council.proposal import Proposal
from agents.editorial.models import TopicPackage
from agents.editorial.journalist_agent import _extract_topic


# category → audience segments (ordered by primary fit)
_AUDIENCE_MAP: dict[str, list[str]] = {
    "corporate":  ["general_public", "investors", "employees", "journalists"],
    "political":  ["citizens", "policy_watchers", "academics", "journalists"],
    "science":    ["curious_adults", "students", "academics", "general_public"],
    "culture":    ["youth_18_34", "general_public", "enthusiasts"],
    "finance":    ["investors", "professionals", "general_public"],
    "general":    ["general_public", "youth_18_34", "professionals"],
}

# Tone preferences per primary audience
_AUDIENCE_TONE: dict[str, str] = {
    "general_public":   "educational",
    "youth_18_34":      "entertainment",
    "investors":        "serious",
    "citizens":         "serious",
    "policy_watchers":  "serious",
    "academics":        "serious",
    "journalists":      "serious",
    "employees":        "educational",
    "students":         "educational",
    "curious_adults":   "educational",
    "professionals":    "serious",
    "enthusiasts":      "entertainment",
}


class AudienceVoter(CouncilAgent):
    """
    Council voter: identifies primary audience and recommends tone.

    Embeds audience data as JSON in rationale.
    """

    def __init__(self) -> None:
        super().__init__(
            role="audience_analyst",
            profile_config={},
            memory_ref=None,
        )

    def deliberate(self, proposal: Proposal | dict[str, Any]) -> dict[str, Any]:
        topic    = _extract_topic(proposal)
        segments = AudienceAgent.analyse_audience(topic)
        tone     = AudienceAgent.recommend_tone(segments)

        support_score = 0.75 if segments else 0.4
        data = {
            "segments":      segments,
            "primary":       segments[0] if segments else "general_public",
            "tone":          tone,
            "reach_estimate": _estimate_reach(segments),
        }
        return {
            "role":              self.role,
            "support_score":     support_score,
            "assumptions_count": 0,
            "focus":             "audience_and_tone",
            "risks":             [] if segments else ["no_clear_audience"],
            "constraints":       [],
            "rationale":         json.dumps(data, ensure_ascii=True),
        }


class AudienceAgent:
    """Standalone audience analyser."""

    @staticmethod
    def analyse_audience(topic: TopicPackage) -> list[str]:
        return _AUDIENCE_MAP.get(topic.category, _AUDIENCE_MAP["general"])

    @staticmethod
    def recommend_tone(segments: list[str]) -> str:
        if not segments:
            return "educational"
        primary = segments[0]
        return _AUDIENCE_TONE.get(primary, "educational")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _estimate_reach(segments: list[str]) -> str:
    """Rough qualitative reach estimate based on segment breadth."""
    if "general_public" in segments:
        return "broad"
    if len(segments) >= 3:
        return "medium"
    return "niche"
