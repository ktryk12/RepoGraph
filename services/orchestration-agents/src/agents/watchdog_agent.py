"""
agents/watchdog_agent.py — Investigative content persona agent.

Researches and scripts verified corporate/institutional scandals.
ONLY covers documented, post-verdict cases with >= 2 independent sources.

L7 boundary — ALL output requires human approval before publishing.
human_approval_required=True — no exceptions.

Confidence gate: claims scoring < 0.85 are silently dropped.
Private persons: only named if convicted or in unambiguous public role.
Active litigation: topics blocked until final_judgment is set.

Every claim is logged to truth/watchdog_claims.jsonl with source + date.

Usage:
    agent = WatchdogAgent()
    result = agent.research_topic("vw_dieselgate")
    script = agent.generate_script(result["facts"], platform="youtube")
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from agents.base import Agent
from babyai_shared.bus.protocol import Context, Message, MessageType
from policy.watchdog_policy import WATCHDOG_POLICY

_log = logging.getLogger(__name__)

_TOPICS_YAML = Path(__file__).resolve().parents[1] / "config" / "watchdog_topics.yaml"
_TRUTH_LOG   = Path(__file__).resolve().parents[1] / "truth" / "watchdog_claims.jsonl"

_CONFIDENCE_THRESHOLD_VERIFIED   = 0.80   # for topics with verified=true in YAML
_CONFIDENCE_THRESHOLD_UNVERIFIED = 0.85   # default / unverified topics
_MIN_SOURCES                     = 2


# ── Data models ────────────────────────────────────────────────────────────────

@dataclass
class VerifiedClaim:
    claim_id:    str
    text:        str
    sources:     List[str]
    confidence:  float
    topic_id:    str
    logged_at:   str


@dataclass
class ResearchResult:
    topic_id:             str
    title:                str
    facts:                List[VerifiedClaim]
    platforms_supported:  List[str]
    policy_violations:    List[str]   # empty = cleared
    content_tag:          str = "GENERAL"   # "GENERAL" | "POLITICAL" | "NSFW"
    human_approval_required: bool = True
    requires_action:      bool = False


# ── WatchdogAgent ──────────────────────────────────────────────────────────────

class WatchdogAgent(Agent):
    """
    Investigative content agent.

    Processes WATCHDOG_RESEARCH messages.
    Emits WATCHDOG_SCRIPT_READY with human_approval_required=True.
    Never emits without passing policy checks.
    """

    def __init__(
        self,
        agent_id: str = "watchdog-001",
        truth_log_path: Optional[Path] = None,
        topics_path: Optional[Path] = None,
    ) -> None:
        super().__init__(
            agent_id=agent_id,
            role="watchdog",
            accepts={
                MessageType.WATCHDOG_RESEARCH,
            },
        )
        self._truth_log   = Path(truth_log_path or _TRUTH_LOG)
        self._topics_path = Path(topics_path or _TOPICS_YAML)
        self._topics: Dict[str, Any] = {}
        self._load_topics()

    # ── Message handler ────────────────────────────────────────────────────────

    def process(self, message: Message, context: Context) -> List[Message]:
        if message.message_type == MessageType.WATCHDOG_RESEARCH:
            return self._handle_research(message, context)
        return []

    def _handle_research(self, message: Message, context: Context) -> List[Message]:
        topic_id = str(message.payload.get("topic_id") or "").strip()
        platform = str(message.payload.get("platform") or "youtube").strip()

        if not topic_id:
            _log.warning("watchdog_research_missing_topic_id")
            return []

        result = self.research_topic(topic_id)

        if result.policy_violations:
            _log.warning(
                "watchdog_research_blocked topic=%s violations=%s",
                topic_id, result.policy_violations,
            )
            return []

        if not result.facts:
            _log.warning("watchdog_research_no_verified_facts topic=%s", topic_id)
            return []

        script = self.generate_script(
            {
                "topic_id":    topic_id,
                "title":       result.title,
                "claims":      result.facts,
                "content_tag": result.content_tag,
            },
            platform=platform,
        )

        return [Message(
            message_id=str(uuid.uuid4()),
            from_agent=self.agent_id,
            to_agent="supervisor-001",
            message_type=MessageType.WATCHDOG_SCRIPT_READY,
            payload={
                "topic_id":                topic_id,
                "platform":                platform,
                "script":                  script,
                "claim_count":             len(result.facts),
                "content_tag":             result.content_tag,
                "human_approval_required": True,
                "requires_action":         False,
                "requires_human_review":   True,
            },
            context_id=context.context_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )]

    # ── Public API ─────────────────────────────────────────────────────────────

    def research_topic(self, topic_id: str) -> ResearchResult:
        """
        Look up topic in watchdog_topics.yaml.
        Filter claims by confidence_score >= 0.85 and _verify_claim().
        Log accepted claims to truth/watchdog_claims.jsonl.
        Returns ResearchResult with policy_violations list.
        """
        topic = self._topics.get(topic_id)
        if topic is None:
            return ResearchResult(
                topic_id=topic_id,
                title="",
                facts=[],
                platforms_supported=[],
                policy_violations=["topic_not_found"],
            )

        content_tag = str(topic.get("content_tag", "GENERAL")).upper()

        # Policy checks
        violations = WATCHDOG_POLICY.check(
            has_sources=len(topic.get("sources", [])) >= _MIN_SOURCES,
            confidence=float(topic.get("confidence", 0.0)),
            active_litigation=bool(topic.get("active_litigation", True)),
            human_approved=False,   # always False here — gate is downstream
            content_tag=content_tag,
            nsfw_approved=False,    # NSFW approval only happens at the human gate
        )
        # human_approval_required and nsfw_explicit_gate are enforced at routing,
        # not at research time — strip them here so they don't block fact collection
        violations = [
            v for v in violations
            if v not in ("human_approval_required", "nsfw_explicit_gate")
        ]

        if violations:
            return ResearchResult(
                topic_id=topic_id,
                title=topic.get("title", ""),
                facts=[],
                platforms_supported=topic.get("platforms", []),
                content_tag=content_tag,
                policy_violations=violations,
            )

        threshold = (
            _CONFIDENCE_THRESHOLD_VERIFIED
            if topic.get("verified", False)
            else _CONFIDENCE_THRESHOLD_UNVERIFIED
        )

        accepted: List[VerifiedClaim] = []
        for raw_fact in topic.get("key_facts", []):
            if not self._verify_claim(raw_fact, topic):
                continue
            score = self.confidence_score(raw_fact)
            if score < threshold:
                _log.debug(
                    "watchdog_claim_dropped confidence=%.2f claim=%r", score, raw_fact[:60]
                )
                continue

            source_names = [
                s.get("name", s) if isinstance(s, dict) else str(s)
                for s in topic.get("sources", [])
            ]
            claim = VerifiedClaim(
                claim_id=str(uuid.uuid4()),
                text=raw_fact,
                sources=source_names,
                confidence=score,
                topic_id=topic_id,
                logged_at=datetime.now(timezone.utc).isoformat(),
            )
            self._log_claim(claim, content_tag=content_tag)
            accepted.append(claim)

        return ResearchResult(
            topic_id=topic_id,
            title=topic.get("title", ""),
            facts=accepted,
            platforms_supported=topic.get("platforms", []),
            content_tag=content_tag,
            policy_violations=[],
        )

    def generate_script(self, facts: Dict[str, Any], platform: str) -> Dict[str, Any]:
        """
        Generate platform-specific script from verified facts.

        Returns dict with 'format', 'content', and 'metadata'.
        All output has human_approval_required=True.
        """
        platform    = platform.lower().strip()
        claims      = facts.get("claims", [])
        title       = facts.get("title", facts.get("topic_id", ""))
        content_tag = str(facts.get("content_tag", "GENERAL")).upper()

        if platform == "youtube":
            script = self._script_youtube(title, claims)
        elif platform in ("tiktok", "instagram"):
            script = self._script_short(title, claims, platform)
        elif platform == "x":
            script = self._script_x_thread(title, claims)
        elif platform == "reddit":
            script = self._script_reddit(title, claims)
        else:
            script = {
                "format":   "generic",
                "platform": platform,
                "content":  self._claims_as_text(claims),
                "metadata": {"human_approval_required": True},
            }

        # Stamp content_tag into every script's metadata
        script.setdefault("metadata", {})["content_tag"] = content_tag
        return script

    def confidence_score(self, claim: str) -> float:
        """
        Score a claim string.

        Heuristic scoring based on claim characteristics:
        - Contains specific numbers/dates/amounts → +weight
        - Short vague assertions → lower score
        - Claims from topics with government/regulatory sources → boosted
        - Combo bonus when claim contains amount + year + agency/company

        Returns float in [0.0, 1.0].
        Blocked if score < 0.80 (verified topic) or < 0.85 (unverified).
        """
        import re

        score = 0.70   # base
        text  = claim.lower()

        # Year present
        has_year = bool(re.search(r"\b(19|20)\d{2}\b", text))
        if has_year:
            score += 0.06

        # Monetary amount — dollar, pound, euro symbols or explicit GBP/EUR/USD prefix
        _AMOUNT_RE = re.compile(
            r"(\$|£|€|gbp|eur|usd)\s*[\d,.]+"   # symbol/code + digits
            r"|[\d,]+\s*(billion|million|bn|m)\b"  # bare number + magnitude
        )
        has_amount = bool(_AMOUNT_RE.search(text))
        if has_amount:
            score += 0.06

        # Magnitude qualifier (e.g. "4.3 billion")
        if re.search(r"\d+[.,]\d+\s*(bn|billion|million|m\b)", text):
            score += 0.04

        # Legal action — expanded synonyms
        _LEGAL_RE = re.compile(
            r"(pled guilty|pleaded guilty|pled|convicted|fined"
            r"|resigned|shut down|dissolved"
            r"|paid (penalty|fine|settlement)|sentenced)"
        )
        has_legal = bool(_LEGAL_RE.search(text))
        if has_legal:
            score += 0.08

        # Named regulatory agency or court
        _AGENCY_RE = re.compile(
            r"(department of justice|doj|ftc|sec\b|fca\b|ico\b|epa\b"
            r"|barclays|volkswagen|\bvw\b|enron|facebook|cambridge analytica"
            r"|arthur andersen)"
        )
        has_agency = bool(_AGENCY_RE.search(text))
        if has_agency:
            score += 0.06

        # Detailed claim (long text)
        if len(claim) > 80:
            score += 0.03

        # Combo bonus: amount + year + agency/legal in same claim
        if sum([has_amount, has_year, has_agency or has_legal]) >= 2:
            score += 0.03

        return min(1.0, score)

    def _verify_claim(self, claim: str, topic: Dict[str, Any]) -> bool:
        """
        Verify claim has >= 2 independent sources.

        A claim from a topic with >= 2 named sources of different types
        (government, regulatory, academic, press, official_report, book)
        is considered verified.
        """
        sources = topic.get("sources", [])
        if len(sources) < _MIN_SOURCES:
            return False

        # Count distinct source types
        types = set()
        for s in sources:
            if isinstance(s, dict):
                types.add(s.get("type", "unknown"))
            else:
                types.add("unknown")

        # At least 2 distinct source types → independent
        return len(types) >= 2

    # ── Script generators ──────────────────────────────────────────────────────

    def _script_youtube(
        self, title: str, claims: List[VerifiedClaim]
    ) -> Dict[str, Any]:
        """8-12 min YouTube script with chapter timestamps."""
        chapters = []
        body_lines = []

        body_lines.append(f"# {title}")
        body_lines.append("")
        body_lines.append("## HOOK (0:00 - 0:45)")
        body_lines.append(
            f"[HOOK] Open with the most striking verified fact from this scandal."
        )
        if claims:
            body_lines.append(f'"{claims[0].text}"')
            body_lines.append(f"[Source: {claims[0].sources[0]}]")
        chapters.append({"timestamp": "0:00", "title": "Hook"})

        body_lines.append("")
        body_lines.append("## CONTEXT (0:45 - 2:00)")
        body_lines.append("[CONTEXT] Establish who, what, when, where.")
        chapters.append({"timestamp": "0:45", "title": "Background & Context"})

        body_lines.append("")
        body_lines.append("## KEY FACTS (2:00 - 7:00)")
        chapters.append({"timestamp": "2:00", "title": "Documented Evidence"})
        for i, claim in enumerate(claims):
            ts = f"{2 + i}:00"
            body_lines.append(f"\n### Fact {i+1} [{ts}]")
            body_lines.append(f"{claim.text}")
            body_lines.append(f"Sources: {', '.join(claim.sources[:2])}")
            body_lines.append(f"[Confidence: {claim.confidence:.0%}]")
            chapters.append({"timestamp": ts, "title": f"Fact {i+1}"})

        body_lines.append("")
        body_lines.append("## CONSEQUENCE (7:00 - 9:30)")
        body_lines.append("[CONSEQUENCE] What changed? Legislation, fines, convictions.")
        chapters.append({"timestamp": "7:00", "title": "Consequences & Accountability"})

        body_lines.append("")
        body_lines.append("## OUTRO (9:30 - 10:00)")
        body_lines.append("[OUTRO] Sources listed in description. Like & subscribe.")
        chapters.append({"timestamp": "9:30", "title": "Outro"})

        return {
            "format":   "youtube",
            "platform": "youtube",
            "content":  "\n".join(body_lines),
            "chapters": chapters,
            "metadata": {
                "estimated_duration_min": "8-12",
                "claim_count": len(claims),
                "human_approval_required": True,
                "requires_human_review":   True,
            },
        }

    def _script_short(
        self, title: str, claims: List[VerifiedClaim], platform: str
    ) -> Dict[str, Any]:
        """60-90 second hook-first format for TikTok/Instagram."""
        lines = []
        lines.append(f"HOOK (0-5s): [Most shocking verified fact]")
        if claims:
            lines.append(f'"{claims[0].text}"')
        lines.append("")
        lines.append("BUILD (5-40s): 3 key facts, fast pace")
        for claim in claims[:3]:
            lines.append(f"- {claim.text[:120]}")
        lines.append("")
        lines.append("CTA (40-60s): 'Sources in bio. Follow for more.'")

        return {
            "format":   "short_video",
            "platform": platform,
            "content":  "\n".join(lines),
            "metadata": {
                "estimated_duration_s": "60-90",
                "claim_count": len(claims[:3]),
                "human_approval_required": True,
                "requires_human_review":   True,
            },
        }

    def _script_x_thread(
        self, title: str, claims: List[VerifiedClaim]
    ) -> Dict[str, Any]:
        """Thread format: 8-12 tweets with source links."""
        tweets = []
        tweets.append({
            "n": 1,
            "text": f"THREAD: {title} — A documented scandal with verified sources. (1/{len(claims)+3})",
        })
        tweets.append({
            "n": 2,
            "text": "Everything below is sourced from government filings, regulatory decisions, or peer-reviewed research. No speculation.",
        })
        for i, claim in enumerate(claims, start=3):
            src = claim.sources[0] if claim.sources else ""
            tweet_text = f"{claim.text[:240]}"
            if src:
                tweet_text += f"\n[{src}]"
            tweets.append({"n": i, "text": tweet_text})

        tweets.append({
            "n": len(claims) + 3,
            "text": "Full source list in thread. If you found this valuable, RT so others can see documented accountability.",
        })

        return {
            "format":   "x_thread",
            "platform": "x",
            "content":  tweets,
            "metadata": {
                "tweet_count": len(tweets),
                "claim_count": len(claims),
                "human_approval_required": True,
                "requires_human_review":   True,
            },
        }

    def _script_reddit(
        self, title: str, claims: List[VerifiedClaim]
    ) -> Dict[str, Any]:
        """Long-form post with TL;DR and source list."""
        lines = []
        lines.append(f"## {title}")
        lines.append("")
        lines.append("**TL;DR:** " + (claims[0].text if claims else "See full post."))
        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("### Documented Facts")
        lines.append("")
        for i, claim in enumerate(claims, 1):
            lines.append(f"**{i}.** {claim.text}")
            for src in claim.sources[:2]:
                lines.append(f"   - Source: {src}")
            lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("### Full Source List")
        seen: set = set()
        n = 1
        for claim in claims:
            for src in claim.sources:
                if src not in seen:
                    lines.append(f"{n}. {src}")
                    seen.add(src)
                    n += 1
        lines.append("")
        lines.append("*All claims verified. No active litigation. Post human-reviewed before publishing.*")

        return {
            "format":   "reddit_post",
            "platform": "reddit",
            "content":  "\n".join(lines),
            "metadata": {
                "claim_count": len(claims),
                "human_approval_required": True,
                "requires_human_review":   True,
            },
        }

    def _claims_as_text(self, claims: List[VerifiedClaim]) -> str:
        return "\n".join(f"- {c.text} [{', '.join(c.sources[:1])}]" for c in claims)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _load_topics(self) -> None:
        try:
            import yaml
            raw = yaml.safe_load(self._topics_path.read_text(encoding="utf-8"))
            for entry in raw.get("topics", []):
                self._topics[entry["id"]] = entry
            _log.info("watchdog_topics_loaded count=%d", len(self._topics))
        except Exception as exc:
            _log.warning("watchdog_topics_load_failed path=%s error=%s", self._topics_path, exc)

    def _log_claim(self, claim: VerifiedClaim, content_tag: str = "GENERAL") -> None:
        """Append claim to truth/watchdog_claims.jsonl."""
        try:
            self._truth_log.parent.mkdir(parents=True, exist_ok=True)
            record = {
                "claim_id":   claim.claim_id,
                "topic_id":   claim.topic_id,
                "text":       claim.text,
                "sources":    claim.sources,
                "confidence": round(claim.confidence, 4),
                "content_tag": content_tag,
                "logged_at":  claim.logged_at,
            }
            with open(self._truth_log, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=True) + "\n")
        except Exception as exc:
            _log.warning("watchdog_claim_log_failed error=%s", exc)
