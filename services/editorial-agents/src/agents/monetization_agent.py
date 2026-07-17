"""
agents/editorial/monetization_agent.py — Monetization strategy voter.

MonetizationVoter: Council participant — picks revenue models per format/platform.
MonetizationAgent: Standalone callable.
"""
from __future__ import annotations

import json
from typing import Any

from babyai.council.base import Agent as CouncilAgent
from babyai.council.proposal import Proposal
from agents.editorial.models import MonetizationPlan, TopicPackage
from agents.editorial.journalist_agent import _extract_topic


# ---------------------------------------------------------------------------
# MONETIZATION_MATRIX
# primary_model → {secondary: [...], cpm: EUR/1000 views, rationale: str}
# ---------------------------------------------------------------------------
MONETIZATION_MATRIX: dict[str, dict[str, Any]] = {
    "adsense": {
        "secondary":  ["sponsorship", "merchandise"],
        "cpm":        2.50,
        "rationale":  "High-volume investigative content — AdSense scales well.",
    },
    "sponsorship": {
        "secondary":  ["adsense", "affiliate"],
        "cpm":        6.00,
        "rationale":  "Niche investigative audience commands premium sponsorship rates.",
    },
    "subscription": {
        "secondary":  ["adsense"],
        "cpm":        12.00,
        "rationale":  "Deep-dive series with repeat viewers suits subscription model.",
    },
    "affiliate": {
        "secondary":  ["adsense"],
        "cpm":        3.50,
        "rationale":  "Product/book recommendations add affiliate revenue stream.",
    },
    "donation": {
        "secondary":  ["merchandise"],
        "cpm":        1.50,
        "rationale":  "Public-interest journalism — donation model aligns with audience values.",
    },
}

# platform → recommended primary model
_PLATFORM_MODEL: dict[str, str] = {
    "youtube_long":  "adsense",
    "youtube_short": "adsense",
    "tiktok":        "sponsorship",
    "instagram":     "sponsorship",
    "spotify":       "subscription",
    "medium":        "subscription",
    "substack":      "subscription",
    "twitter":       "sponsorship",
    "linkedin":      "sponsorship",
}

# category override — some categories are better suited to specific models
_CATEGORY_MODEL: dict[str, str] = {
    "corporate":  "adsense",
    "political":  "donation",
    "science":    "adsense",
    "culture":    "sponsorship",
    "finance":    "sponsorship",
    "general":    "adsense",
}


class MonetizationVoter(CouncilAgent):
    """
    Council voter: recommends monetization model.

    Embeds MonetizationPlan as JSON in rationale.
    """

    def __init__(self) -> None:
        super().__init__(
            role="monetization_strategist",
            profile_config={},
            memory_ref=None,
        )

    def deliberate(self, proposal: Proposal | dict[str, Any]) -> dict[str, Any]:
        topic = _extract_topic(proposal)
        plan  = MonetizationAgent.build_plan(topic)

        support_score = min(1.0, 0.5 + plan.estimated_cpm / 20.0)
        data = {
            "primary_model":    plan.primary_model,
            "secondary_models": plan.secondary_models,
            "estimated_cpm":    plan.estimated_cpm,
            "rationale":        plan.rationale,
        }
        return {
            "role":              self.role,
            "support_score":     support_score,
            "assumptions_count": 0,
            "focus":             "monetization",
            "risks":             [],
            "constraints":       [],
            "rationale":         json.dumps(data, ensure_ascii=True),
        }


class MonetizationAgent:
    """Standalone monetization planner."""

    @staticmethod
    def build_plan(topic: TopicPackage) -> MonetizationPlan:
        # Prefer category override, then fallback to general
        primary = _CATEGORY_MODEL.get(topic.category, "adsense")
        matrix  = MONETIZATION_MATRIX[primary]
        return MonetizationPlan(
            primary_model=primary,
            secondary_models=list(matrix["secondary"]),
            estimated_cpm=float(matrix["cpm"]),
            rationale=matrix["rationale"],
        )

    @staticmethod
    def plan_for_platform(platform: str) -> MonetizationPlan:
        primary = _PLATFORM_MODEL.get(platform, "adsense")
        matrix  = MONETIZATION_MATRIX[primary]
        return MonetizationPlan(
            primary_model=primary,
            secondary_models=list(matrix["secondary"]),
            estimated_cpm=float(matrix["cpm"]),
            rationale=matrix["rationale"],
        )
