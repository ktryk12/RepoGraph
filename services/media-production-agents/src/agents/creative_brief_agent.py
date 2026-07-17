"""
CreativeBriefAgent — triggered by content.opportunity.detected; generates a
content brief and emits content.brief.ready for human review.

L7 boundary — requires_action is ALWAYS False.
No content is published automatically. Human must approve via:
    python -m babyai.cli approve-brief <brief_id>

Kafka consumer: content.opportunity.detected
Kafka producer: content.brief.ready

Brief format:
  - title_options      : 3 candidate titles
  - hook               : opening hook sentence
  - key_points         : 3–5 bullet points
  - tone               : "educational" | "analytical" | "narrative" | "urgent"
  - recommended_format : "short_video" | "long_video" | "thread" | "article"
  - recommended_channel: "youtube" | "twitter" | "linkedin" | "newsletter"
  - target_length_s    : target length in seconds (if video)
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from agents._kafka_publisher import _KafkaPublisher

_log = logging.getLogger(__name__)

_TOPIC_OPPORTUNITY  = "content.opportunity.detected"
_TOPIC_BRIEF_READY  = "content.brief.ready"

_BROKERS  = os.getenv("KAFKA_BOOTSTRAP_SERVERS", os.getenv("KAFKA_BROKERS", "127.0.0.1:9092"))
_GROUP_ID = os.getenv("CREATIVE_BRIEF_GROUP", "creative-brief-agent")

# Claude model for brief generation (falls back to heuristic if unavailable)
_CLAUDE_MODEL = os.getenv("CREATIVE_BRIEF_MODEL", "claude-sonnet-4-6")


# ---------------------------------------------------------------------------
# CreativeBriefAgent
# ---------------------------------------------------------------------------

class CreativeBriefAgent:
    """
    Generates content briefs from scored opportunities.

    Attributes:
        agent_id : str
        role     : str
    """

    agent_id: str = "creative-brief-001"
    role:     str = "creative-brief"

    def __init__(
        self,
        publisher:       Any | None = None,
        claude_client:   Any | None = None,
    ) -> None:
        self._pub    = publisher      # injected in tests
        self._claude = claude_client  # injected in tests; None = lazy init

        self._thread:   Optional[threading.Thread] = None
        self._stop_evt  = threading.Event()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            _log.warning("creative_brief_agent already running")
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run, name="creative-brief-agent", daemon=True
        )
        self._thread.start()
        _log.info("creative_brief_agent started")

    def stop(self) -> None:
        self._stop_evt.set()
        if self._thread:
            self._thread.join(timeout=15)
        _log.info("creative_brief_agent stopped")

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ── Processing ────────────────────────────────────────────────────────────

    def generate_brief(self, opportunity: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate a content brief for an opportunity.
        Safe to call directly (used in tests and ContentOrchestratorAgent).
        Returns the brief dict and emits to Kafka.
        """
        symbol  = opportunity.get("symbol", "Unknown")
        thesis  = opportunity.get("thesis", "")
        verdict = opportunity.get("verdict", "moderate")
        score   = float(opportunity.get("opportunity_score", 0.0))

        brief_id = str(uuid.uuid4())

        # Attempt Claude generation; fall back to heuristic
        brief_content = self._generate_with_claude(symbol, thesis, verdict, score)
        if not brief_content:
            brief_content = self._heuristic_brief(symbol, thesis, verdict, score)

        brief = {
            "source":               "creative_brief_agent",
            "brief_id":             brief_id,
            "created_at":           datetime.now(timezone.utc).isoformat(),
            "opportunity_id":       opportunity.get("opportunity_id", ""),
            "symbol":               symbol,
            "opportunity_score":    score,
            "verdict":              verdict,
            **brief_content,
            "requires_action":      False,   # L7: ALWAYS False
            "requires_human_review": True,
            "status":               "pending_approval",
        }

        self._emit(brief)
        _log.info(
            "creative_brief_generated brief_id=%s symbol=%s format=%s",
            brief_id, symbol, brief.get("recommended_format"),
        )
        return brief

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run(self) -> None:
        consumer = _build_consumer()
        if consumer is None:
            _log.warning("creative_brief_no_kafka_consumer — agent idle")
            return
        try:
            consumer.subscribe([_TOPIC_OPPORTUNITY])
            _log.info("creative_brief_subscribed topic=%s", _TOPIC_OPPORTUNITY)
            while not self._stop_evt.is_set():
                msg = consumer.poll(1.0)
                if msg is None:
                    continue
                if msg.error():
                    _log.warning("creative_brief_kafka_error error=%s", msg.error())
                    continue
                try:
                    payload = json.loads(msg.value().decode("utf-8"))
                    self.generate_brief(payload)
                except Exception as exc:
                    _log.error("creative_brief_process_error error=%s", exc, exc_info=True)
        finally:
            consumer.close()

    def _generate_with_claude(
        self,
        symbol:  str,
        thesis:  str,
        verdict: str,
        score:   float,
    ) -> Optional[Dict[str, Any]]:
        try:
            import anthropic
            client = anthropic.Anthropic(
                api_key=os.getenv("ANTHROPIC_API_KEY", "local"),
                base_url=os.getenv("ANTHROPIC_BASE_URL") or None,
            )
            prompt = _build_prompt(symbol, thesis, verdict, score)
            resp   = client.messages.create(
                model=_CLAUDE_MODEL,
                max_tokens=800,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text if resp.content else ""
            return _parse_claude_response(text, symbol, score)
        except Exception as exc:
            _log.debug("creative_brief_claude_unavailable error=%s", exc)
            return None

    def _heuristic_brief(
        self,
        symbol:  str,
        thesis:  str,
        verdict: str,
        score:   float,
    ) -> Dict[str, Any]:
        """Deterministic fallback brief when Claude is unavailable."""
        tone   = "analytical" if verdict in ("strong", "moderate") else "educational"
        fmt    = "short_video" if score >= 0.75 else "thread"
        length = 90 if fmt == "short_video" else 0

        return {
            "title_options": [
                f"Why {symbol} is worth watching right now",
                f"{symbol}: Signal breakdown and what it means",
                f"Is {symbol} the next big opportunity?",
            ],
            "hook":       f"A {verdict} signal just appeared for {symbol} — here's what the data shows.",
            "key_points": [
                f"Analysis verdict: {verdict} (score {score:.2f})",
                "On-chain data confirms the trend",
                "Risk factors to consider before acting",
                "Historical comparison with similar setups",
            ],
            "tone":               tone,
            "recommended_format": fmt,
            "recommended_channel": "youtube" if fmt == "short_video" else "twitter",
            "target_length_s":    length,
            "generation_method":  "heuristic",
        }

    def _emit(self, brief: Dict[str, Any]) -> None:
        pub = self._pub or _get_publisher()
        if pub is None:
            _log.info("creative_brief_emit_no_kafka brief_id=%s", brief["brief_id"])
            return
        try:
            pub.publish(topic=_TOPIC_BRIEF_READY, key=brief["brief_id"], value=brief)
            pub.flush()
        except Exception as exc:
            _log.error("creative_brief_emit_failed error=%s", exc)


# ---------------------------------------------------------------------------
# Prompt builder + response parser
# ---------------------------------------------------------------------------

def _build_prompt(symbol: str, thesis: str, verdict: str, score: float) -> str:
    return f"""You are a content strategist for a financial intelligence platform.

Generate a content brief for the following investment signal.

Symbol: {symbol}
Verdict: {verdict}
Opportunity score: {score:.2f}/1.00
Thesis summary: {thesis[:500] if thesis else "No thesis available."}

Respond with JSON only — no prose, no markdown fences:
{{
  "title_options": ["<title1>", "<title2>", "<title3>"],
  "hook": "<one sentence opening hook>",
  "key_points": ["<point1>", "<point2>", "<point3>", "<point4>"],
  "tone": "educational|analytical|narrative|urgent",
  "recommended_format": "short_video|long_video|thread|article",
  "recommended_channel": "youtube|twitter|linkedin|newsletter",
  "target_length_s": <integer seconds, 0 if not video>,
  "generation_method": "claude"
}}"""


def _parse_claude_response(text: str, symbol: str, score: float) -> Optional[Dict[str, Any]]:
    try:
        # Strip any accidental markdown fences
        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
        data = json.loads(clean)
        # Validate required keys
        required = {"title_options", "hook", "key_points", "tone",
                    "recommended_format", "recommended_channel", "target_length_s"}
        if required.issubset(data.keys()):
            return data
    except Exception as exc:
        _log.debug("creative_brief_parse_failed error=%s", exc)
    return None


# ---------------------------------------------------------------------------
# Module-level lazy helpers
# ---------------------------------------------------------------------------

_publisher_instance: Any = None


def _get_publisher() -> Any:
    global _publisher_instance
    if _publisher_instance is None:
        _publisher_instance = _KafkaPublisher()
    return _publisher_instance


def _build_consumer():
    try:
        from confluent_kafka import Consumer
        return Consumer({
            "bootstrap.servers":  _BROKERS,
            "group.id":           _GROUP_ID,
            "auto.offset.reset":  "latest",
            "enable.auto.commit": True,
        })
    except Exception as exc:
        _log.warning("creative_brief_consumer_unavailable error=%s", exc)
        return None

