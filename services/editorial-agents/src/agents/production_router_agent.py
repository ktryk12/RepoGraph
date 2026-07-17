"""
agents/editorial/production_router_agent.py — Routes editorial decisions to output stubs.

ProductionRouterAgent:
  Receives EDITORIAL_DECISION_READY, fans out to one ProductionPackage per format,
  emits PRODUCTION_ROUTED + HUMAN_APPROVAL_REQUIRED for each package.

Output stubs (deterministic, no LLM):
  animation_script, ai_film, podcast, infographic, longform_article, thread, short_clip
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from agents.base import Agent
from shared.babyai_shared.bus.protocol import Message, MessageType
from agents.editorial.models import EditorialDecision, FormatSpec, MonetizationPlan, ProductionPackage


# ---------------------------------------------------------------------------
# Output stub registry
# format_type → stub function(decision, format_spec) → ProductionPackage
# ---------------------------------------------------------------------------

def _stub_animation_script(
    decision: EditorialDecision,
    fmt: FormatSpec,
) -> ProductionPackage:
    angle = decision.chosen_angle
    return ProductionPackage(
        topic_id                  = decision.topic_id,
        format_type               = "animation",
        platform                  = fmt.platforms[0] if fmt.platforms else "youtube_long",
        content                   = {
            "script_outline": [
                f"SCENE 1 — Hook: {angle[:80]}",
                "SCENE 2 — Background & context (animated timeline)",
                "SCENE 3 — Key evidence (animated data viz)",
                "SCENE 4 — Who's responsible (character reveal)",
                "SCENE 5 — Resolution & call to action",
            ],
            "style":          "2D motion graphics, muted palette",
            "voiceover_tone": decision.tone,
            "duration_sec":   480,
        },
        assets_required           = ["logo_animation", "data_chart_01", "character_profiles"],
        estimated_production_time = 14400,   # 4 h
        ready_for_approval        = False,
        human_approval_required   = True,
    )


def _stub_ai_film(
    decision: EditorialDecision,
    fmt: FormatSpec,
) -> ProductionPackage:
    return ProductionPackage(
        topic_id                  = decision.topic_id,
        format_type               = "documentary",
        platform                  = fmt.platforms[0] if fmt.platforms else "youtube_long",
        content                   = {
            "act_structure": [
                f"ACT I  — The scandal: {decision.chosen_angle[:60]}",
                "ACT II — How it was hidden: key documents & whistleblowers",
                "ACT III — Aftermath: fines, reforms, public impact",
            ],
            "style":         "AI-generated documentary with archival overlays",
            "narration_tone": decision.tone,
            "target_runtime_min": 20,
        },
        assets_required           = [
            "ai_narration_audio",
            "archival_footage_set",
            "b_roll_graphics",
        ],
        estimated_production_time = 28800,   # 8 h
        ready_for_approval        = False,
        human_approval_required   = True,
    )


def _stub_podcast(
    decision: EditorialDecision,
    fmt: FormatSpec,
) -> ProductionPackage:
    return ProductionPackage(
        topic_id                  = decision.topic_id,
        format_type               = "podcast",
        platform                  = fmt.platforms[0] if fmt.platforms else "spotify",
        content                   = {
            "episode_title":   decision.chosen_angle[:80],
            "segments": [
                "Intro & context (5 min)",
                "Deep dive: what happened (15 min)",
                "Expert guest slot placeholder (10 min)",
                "Listener Q&A format (5 min)",
                "Closing + action items (5 min)",
            ],
            "tone":            decision.tone,
            "target_runtime_min": 40,
        },
        assets_required           = ["audio_intro_jingle", "show_art_1400x1400"],
        estimated_production_time = 7200,    # 2 h
        ready_for_approval        = False,
        human_approval_required   = True,
    )


def _stub_infographic(
    decision: EditorialDecision,
    fmt: FormatSpec,
) -> ProductionPackage:
    return ProductionPackage(
        topic_id                  = decision.topic_id,
        format_type               = "infographic",
        platform                  = fmt.platforms[0] if fmt.platforms else "instagram",
        content                   = {
            "headline":    decision.chosen_angle[:60],
            "panels": [
                "What happened — 3-bullet summary",
                "Key numbers — fines / dates / impact",
                "Who's who — protagonist vs antagonist",
                "What changed — policy / law reforms",
            ],
            "colour_scheme": "high-contrast investigative",
            "format":        "1080×1350 portrait",
        },
        assets_required           = ["vector_icons_set", "brand_palette"],
        estimated_production_time = 3600,    # 1 h
        ready_for_approval        = False,
        human_approval_required   = True,
    )


def _stub_longform_article(
    decision: EditorialDecision,
    fmt: FormatSpec,
) -> ProductionPackage:
    return ProductionPackage(
        topic_id                  = decision.topic_id,
        format_type               = "longform_article",
        platform                  = fmt.platforms[0] if fmt.platforms else "medium",
        content                   = {
            "working_title": decision.chosen_angle,
            "sections": [
                "Introduction — why this story matters now",
                "What happened — a chronological account",
                "The cover-up — documents and denials",
                "Turning point — whistleblowers / investigation",
                "Accountability — legal outcomes",
                "What it means for you — policy & reform",
                "Conclusion & further reading",
            ],
            "target_word_count": 3500,
            "tone":              decision.tone,
        },
        assets_required           = ["pull_quote_graphics", "header_image"],
        estimated_production_time = 10800,   # 3 h
        ready_for_approval        = False,
        human_approval_required   = True,
    )


def _stub_thread(
    decision: EditorialDecision,
    fmt: FormatSpec,
) -> ProductionPackage:
    return ProductionPackage(
        topic_id                  = decision.topic_id,
        format_type               = "thread",
        platform                  = fmt.platforms[0] if fmt.platforms else "twitter",
        content                   = {
            "thread_hook":   f"THREAD: {decision.chosen_angle[:100]} 🧵",
            "tweet_outline": [
                "1/ Hook + one shocking fact",
                "2/ Who are the players?",
                "3/ Timeline: how it escalated",
                "4/ The evidence",
                "5/ How it ended (so far)",
                "6/ What YOU can do",
                "7/ Sources + longform link",
            ],
            "tone": decision.tone,
        },
        assets_required           = ["tweet_card_image"],
        estimated_production_time = 1800,    # 30 min
        ready_for_approval        = False,
        human_approval_required   = True,
    )


def _stub_short_clip(
    decision: EditorialDecision,
    fmt: FormatSpec,
) -> ProductionPackage:
    return ProductionPackage(
        topic_id                  = decision.topic_id,
        format_type               = "short_clip",
        platform                  = fmt.platforms[0] if fmt.platforms else "tiktok",
        content                   = {
            "hook_line":    decision.chosen_angle[:60],
            "beats": [
                "0–3 s   — hook text on screen",
                "3–10 s  — shocking stat / quote",
                "10–30 s — 3 rapid facts",
                "30–45 s — verdict / resolution",
                "45–60 s — CTA: follow for full story",
            ],
            "aspect_ratio": "9:16",
            "duration_sec": 60,
            "style":        "text-on-video, punchy cuts",
        },
        assets_required           = ["background_clip", "text_overlay_template"],
        estimated_production_time = 1800,    # 30 min
        ready_for_approval        = False,
        human_approval_required   = True,
    )


# format_type → stub callable
_STUB_REGISTRY: dict[str, Callable[[EditorialDecision, FormatSpec], ProductionPackage]] = {
    "animation":        _stub_animation_script,
    "documentary":      _stub_ai_film,
    "podcast":          _stub_podcast,
    "infographic":      _stub_infographic,
    "longform_article": _stub_longform_article,
    "thread":           _stub_thread,
    "short_clip":       _stub_short_clip,
    # explainer maps to animation stub
    "explainer":        _stub_animation_script,
}


# ---------------------------------------------------------------------------
# ProductionRouterAgent
# ---------------------------------------------------------------------------

class ProductionRouterAgent(Agent):
    """
    Fans out an EditorialDecision to one ProductionPackage per format.
    Emits:
      • PRODUCTION_ROUTED      — one per package (to downstream output agents)
      • HUMAN_APPROVAL_REQUIRED — each package needs human sign-off before publishing
    """

    accepts = [MessageType.EDITORIAL_DECISION_READY]

    def __init__(self) -> None:
        super().__init__(agent_id="production-router-001", role="production_router")

    def handle(self, message: Message) -> list[Message]:
        if message.message_type != MessageType.EDITORIAL_DECISION_READY:
            return []

        decision = _decision_from_payload(message.payload or {})
        packages = self._route(decision)
        messages: list[Message] = []

        for pkg in packages:
            messages.append(Message(
                message_id   = str(uuid.uuid4()),
                from_agent   = self.agent_id,
                to_agent     = f"output-{pkg.format_type}-001",
                message_type = MessageType.PRODUCTION_ROUTED,
                payload      = _package_to_dict(pkg),
                context_id   = message.context_id,
                timestamp    = _now_iso(),
            ))
            # Every package requires explicit human approval
            messages.append(Message(
                message_id   = str(uuid.uuid4()),
                from_agent   = self.agent_id,
                to_agent     = "supervisor",
                message_type = MessageType.HUMAN_APPROVAL_REQUIRED,
                payload      = {
                    "topic_id":                pkg.topic_id,
                    "format_type":             pkg.format_type,
                    "platform":                pkg.platform,
                    "human_approval_required": True,
                    "package_summary":         _package_summary(pkg),
                },
                context_id   = message.context_id,
                timestamp    = _now_iso(),
            ))

        return messages

    def _route(self, decision: EditorialDecision) -> list[ProductionPackage]:
        packages: list[ProductionPackage] = []
        for fmt in decision.formats:
            stub = _STUB_REGISTRY.get(fmt.format_type)
            if stub is None:
                # Fallback: use short_clip stub for unknown formats
                stub = _stub_short_clip
            packages.append(stub(decision, fmt))
        return packages


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _decision_from_payload(payload: dict[str, Any]) -> EditorialDecision:
    formats = [
        FormatSpec(
            format_type=str(f.get("format_type", "explainer")),
            platforms=list(f.get("platforms", [])),
            rationale=str(f.get("rationale", "")),
        )
        for f in payload.get("formats", [])
    ]
    if not formats:
        formats = [FormatSpec(format_type="explainer", platforms=["youtube_long"], rationale="")]

    mon_raw = payload.get("monetization", {}) or {}
    monetization = MonetizationPlan(
        primary_model=str(mon_raw.get("primary_model", "adsense")),
        secondary_models=list(mon_raw.get("secondary_models", [])),
        estimated_cpm=float(mon_raw.get("estimated_cpm", 2.5)),
        rationale=str(mon_raw.get("rationale", "")),
    )

    tone = str(payload.get("tone", "educational"))
    if tone not in ("serious", "satirical", "educational", "entertainment"):
        tone = "educational"

    return EditorialDecision(
        topic_id       = str(payload.get("topic_id", "unknown")),
        chosen_angle   = str(payload.get("chosen_angle", "")),
        formats        = formats,
        platforms      = list(payload.get("platforms", [])),
        tone           = tone,          # type: ignore[arg-type]
        monetization   = monetization,
        legal_risk     = str(payload.get("legal_risk", "low")),  # type: ignore[arg-type]
        flagged_claims = list(payload.get("flagged_claims", [])),
        human_approval_required = True,
        decided_at     = str(payload.get("decided_at", _now_iso())),
    )


def _package_to_dict(pkg: ProductionPackage) -> dict[str, Any]:
    return {
        "topic_id":                   pkg.topic_id,
        "format_type":                pkg.format_type,
        "platform":                   pkg.platform,
        "content":                    pkg.content,
        "assets_required":            pkg.assets_required,
        "estimated_production_time":  pkg.estimated_production_time,
        "ready_for_approval":         pkg.ready_for_approval,
        "human_approval_required":    pkg.human_approval_required,
    }


def _package_summary(pkg: ProductionPackage) -> str:
    return (
        f"{pkg.format_type} for {pkg.platform} "
        f"(~{pkg.estimated_production_time // 60} min to produce) "
        f"— topic_id={pkg.topic_id}"
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
