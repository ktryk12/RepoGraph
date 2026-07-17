"""
agents/editorial/format_strategist_agent.py — Format & platform strategist.

FormatStrategistVoter: Council participant — recommends formats per topic type.
FormatStrategistAgent: Standalone callable for direct use.
"""
from __future__ import annotations

import json
from typing import Any

from babyai.council.base import Agent as CouncilAgent
from babyai.council.proposal import Proposal
from agents.editorial.models import FormatSpec, TopicPackage
from agents.editorial.journalist_agent import _extract_topic


# ---------------------------------------------------------------------------
# FORMAT_MATRIX[format_type] → platforms that support this format
# ---------------------------------------------------------------------------
FORMAT_MATRIX: dict[str, list[str]] = {
    "explainer":        ["youtube_long", "tiktok", "instagram"],
    "documentary":      ["youtube_long"],
    "animation":        ["youtube_long", "tiktok", "instagram", "youtube_short"],
    "podcast":          ["spotify", "youtube_long"],
    "infographic":      ["instagram", "twitter", "linkedin"],
    "longform_article": ["medium", "substack"],
    "thread":           ["twitter", "linkedin"],
    "short_clip":       ["tiktok", "instagram", "youtube_short"],
}

# category → preferred formats (ordered by fit)
_CATEGORY_FORMATS: dict[str, list[str]] = {
    "corporate":  ["documentary", "explainer", "thread"],
    "political":  ["documentary", "longform_article", "thread"],
    "science":    ["explainer", "animation", "podcast"],
    "culture":    ["short_clip", "animation", "infographic"],
    "finance":    ["explainer", "infographic", "thread"],
    "general":    ["explainer", "short_clip", "thread"],
}

# How many formats to recommend
_MAX_FORMATS = 3


class FormatStrategistVoter(CouncilAgent):
    """
    Council voter: decides format & platform fit.

    Embeds list[FormatSpec] as JSON in rationale for EditorialCouncilAgent.
    """

    def __init__(self) -> None:
        super().__init__(
            role="format_strategist",
            profile_config={},
            memory_ref=None,
        )

    def deliberate(self, proposal: Proposal | dict[str, Any]) -> dict[str, Any]:
        topic   = _extract_topic(proposal)
        formats = FormatStrategistAgent.choose_formats(topic)

        support_score = _score_from_formats(formats)
        data = [
            {
                "format_type": f.format_type,
                "platforms":   f.platforms,
                "rationale":   f.rationale,
            }
            for f in formats
        ]
        return {
            "role":              self.role,
            "support_score":     support_score,
            "assumptions_count": 0,
            "focus":             "format_and_platform",
            "risks":             [] if formats else ["no_suitable_format"],
            "constraints":       [],
            "rationale":         json.dumps(data, ensure_ascii=True),
        }


class FormatStrategistAgent:
    """Standalone format strategist — no Council dependency."""

    @staticmethod
    def choose_formats(topic: TopicPackage) -> list[FormatSpec]:
        category = topic.category
        preferred = _CATEGORY_FORMATS.get(category, _CATEGORY_FORMATS["general"])

        specs: list[FormatSpec] = []
        for fmt in preferred[:_MAX_FORMATS]:
            platforms = FORMAT_MATRIX.get(fmt, ["youtube_long"])
            rationale = _format_rationale(fmt, category, topic.title)
            specs.append(FormatSpec(
                format_type=fmt,
                platforms=platforms,
                rationale=rationale,
            ))
        return specs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _score_from_formats(formats: list[FormatSpec]) -> float:
    """Score = 0.5 base + 0.1 per matched format (max 1.0)."""
    return min(1.0, 0.5 + 0.1 * len(formats))


def _format_rationale(fmt: str, category: str, title: str) -> str:
    entity = title.split()[0] if title.split() else "this topic"
    reasons: dict[str, str] = {
        "documentary":      f"{entity} story has investigation arc — fits long-form documentary.",
        "explainer":        f"{category.title()} topics benefit from clear breakdown — explainer format.",
        "animation":        "Visual storytelling amplifies complex topics for broad audiences.",
        "podcast":          "In-depth analysis with expert dialogue suits audio format.",
        "infographic":      "Data-heavy claims translate well to visual infographic.",
        "longform_article": "Policy/legal detail needs longform written treatment.",
        "thread":           "Rapid-fire facts work well as a Twitter/LinkedIn thread.",
        "short_clip":       "High-impact hook clips drive discovery and funnel to long content.",
    }
    return reasons.get(fmt, f"Suitable format for {category} content.")
